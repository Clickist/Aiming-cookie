"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  attachCoachPrimaryAnalysis,
  getCoachPrimary,
  postCoachPrimaryMessage,
} from "@/lib/api";
import type {
  CoachAnalysisRefOut,
  CoachPrimaryResponse,
  CoachThreadMessageOut,
} from "@/lib/types";

const STARTER_CHIPS = ["减速段怎么练", "握持建议", "没有分析也能聊什么？"];

function parseAttachId(raw: string | null): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n;
}

export default function CoachClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const attachFromUrl = parseAttachId(searchParams.get("analysis"));

  const [data, setData] = useState<CoachPrimaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [attachBusy, setAttachBusy] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextSessionId, setContextSessionId] = useState<number | undefined>(
    undefined,
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const attachOnceRef = useRef<number | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const res = await getCoachPrimary({ signal });
    setData(res);
    return res;
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    refresh(ctrl.signal)
      .catch((e) =>
        setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [refresh]);

  useEffect(() => {
    if (attachFromUrl === undefined) return;
    if (attachOnceRef.current === attachFromUrl) return;
    attachOnceRef.current = attachFromUrl;

    const ctrl = new AbortController();
    setAttachBusy(true);
    setError(null);
    attachCoachPrimaryAnalysis(attachFromUrl, { signal: ctrl.signal })
      .then(() => refresh(ctrl.signal))
      .then(() => setContextSessionId(attachFromUrl))
      .catch((e) =>
        setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setAttachBusy(false));

    return () => ctrl.abort();
  }, [attachFromUrl, refresh]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [data?.messages, sending]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }, [input]);

  const messages = data?.messages ?? [];
  const refs = data?.refs ?? [];

  const activeRefs = refs.filter((r) => r.status === "active");
  const deletedRefs = refs.filter((r) => r.status === "deleted");

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setError(null);
    setInput("");

    const ctx =
      contextSessionId !== undefined &&
      activeRefs.some((r) => r.analysis_session_id === contextSessionId)
        ? contextSessionId
        : undefined;

    try {
      const r = await postCoachPrimaryMessage(trimmed, ctx);
      await refresh();
      if (r.reply == null && r.notes.length > 0) {
        setError(r.notes.join(" · "));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="min-h-dvh flex flex-col bg-background">
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline sticky top-0 z-30 shrink-0">
        <div className="flex items-center gap-sm">
          <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
            Aiming Cookie
          </span>
          <div className="h-4 w-px bg-outline mx-xs" />
          <span className="text-label-md text-on-surface-variant">教练</span>
        </div>
        <div className="flex items-center gap-md">
          <Link
            href="/history"
            className="text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            历史记录
          </Link>
          <Link
            href="/"
            className="text-label-md text-on-surface-variant hover:text-primary transition-colors"
          >
            上传
          </Link>
        </div>
      </header>

      <main
        id="main-content"
        className="flex-1 flex flex-col max-w-[var(--spacing-container-max)] w-full mx-auto min-h-0"
      >
        <section className="px-md py-sm border-b border-outline bg-surface-container-low shrink-0">
          <p className="text-label-sm text-on-surface-variant uppercase tracking-widest font-mono mb-xs">
            关联分析
          </p>
          {attachBusy && (
            <p className="text-label-md text-on-surface-variant">正在附加分析…</p>
          )}
          {!attachBusy && refs.length === 0 && (
            <p className="text-body-md text-on-surface-variant">
              暂无关联。可从报告页「跟教练深聊」附加，或直接聊天（无指标上下文）。
            </p>
          )}
          <div className="flex flex-wrap gap-xs mt-xs">
            {activeRefs.map((ref) => (
              <RefChip
                key={ref.id}
                refRow={ref}
                selected={contextSessionId === ref.analysis_session_id}
                onSelect={() => {
                  if (ref.analysis_session_id != null) {
                    setContextSessionId(ref.analysis_session_id);
                  }
                }}
                onOpenReport={() => {
                  if (ref.analysis_session_id != null) {
                    router.push(`/sessions/${ref.analysis_session_id}/report`);
                  }
                }}
              />
            ))}
            {deletedRefs.map((ref) => (
              <RefChip key={ref.id} refRow={ref} deleted />
            ))}
          </div>
          {contextSessionId !== undefined &&
            activeRefs.some((r) => r.analysis_session_id === contextSessionId) && (
              <div className="mt-sm flex items-center gap-sm text-label-sm text-on-surface-variant">
                <span>
                  下一条消息将附带分析 #{contextSessionId} 的诊断上下文
                </span>
                <button
                  type="button"
                  onClick={() => setContextSessionId(undefined)}
                  className="text-primary hover:opacity-80 font-mono uppercase tracking-wider"
                >
                  清除
                </button>
              </div>
            )}
        </section>

        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-md space-y-md no-scrollbar min-h-[40vh]"
        >
          {loading ? (
            <p className="text-center text-on-surface-variant text-label-md py-md">
              加载对话…
            </p>
          ) : messages.length === 0 ? (
            <EmptyHint />
          ) : (
            messages
              .filter((m) => m.role === "user" || m.role === "assistant")
              .map((m) => <MessageBubble key={m.id} message={m} />)
          )}
          {sending && (
            <div className="flex gap-sm">
              <div className="w-8 h-8 rounded bg-primary-container shrink-0 flex items-center justify-center mt-1">
                <span className="material-symbols-outlined text-on-primary-container text-xs animate-pulse">
                  auto_awesome
                </span>
              </div>
              <div className="bg-background border border-outline p-md rounded-xl rounded-tl-none shadow-sm">
                <p className="text-body-md text-on-surface-variant">教练思考中…</p>
              </div>
            </div>
          )}
        </div>

        <div className="p-md bg-surface-container border-t border-outline shrink-0">
          {messages.length === 0 && !loading && (
            <div className="flex gap-xs mb-md overflow-x-auto no-scrollbar">
              {STARTER_CHIPS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => void send(c)}
                  className="shrink-0 bg-background border border-outline px-sm py-1.5 rounded-full text-[11px] font-bold text-on-surface-variant hover:border-primary hover:text-primary transition-all whitespace-nowrap"
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {error && (
            <p className="text-label-sm text-error mb-xs break-words">{error}</p>
          )}

          <div className="bg-background border border-outline rounded-xl flex flex-col gap-xs shadow-inner focus-within:border-primary transition-colors">
            <div className="flex items-end gap-sm">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
                placeholder="问教练任何问题…（可不附带分析）"
                rows={1}
                disabled={sending}
                className="flex-1 bg-transparent border-none focus:ring-0 focus:outline-none text-on-surface text-body-md py-sm px-sm placeholder:text-on-surface-variant/40 resize-none font-sans disabled:opacity-60"
              />
              <button
                type="button"
                onClick={() => void send(input)}
                disabled={!input.trim() || sending}
                aria-label="发送消息"
                className="w-10 h-10 rounded-lg bg-primary flex items-center justify-center text-on-primary hover:brightness-110 active:scale-95 transition-all mb-1 mr-1 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <span
                  className="material-symbols-outlined text-base"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                >
                  send
                </span>
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function RefChip({
  refRow,
  deleted,
  selected,
  onSelect,
  onOpenReport,
}: {
  refRow: CoachAnalysisRefOut;
  deleted?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onOpenReport?: () => void;
}) {
  const sid = refRow.analysis_session_id;
  const label = sid != null ? `分析 #${sid}` : `引用 #${refRow.id}`;

  if (deleted) {
    return (
      <span
        className="inline-flex items-center gap-1 px-sm py-1 rounded-full border border-outline bg-surface-container text-label-sm text-on-surface-variant line-through opacity-70"
        title={refRow.deleted_at ? `已删除 · ${refRow.deleted_at}` : "已删除"}
      >
        {label}
        <span className="font-mono text-[10px] uppercase tracking-widest">
          已删除
        </span>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        onClick={onSelect}
        className={`inline-flex items-center gap-1 px-sm py-1 rounded-full border text-label-sm transition-colors ${
          selected
            ? "border-primary bg-primary-container text-on-primary-container"
            : "border-outline bg-background text-on-surface hover:border-primary"
        }`}
      >
        {label}
        <span className="font-mono text-[10px] uppercase tracking-widest opacity-80">
          上下文
        </span>
      </button>
      {sid != null && onOpenReport && (
        <button
          type="button"
          onClick={onOpenReport}
          className="text-label-sm text-primary hover:opacity-80 font-mono"
          title="打开报告"
        >
          报告
        </button>
      )}
    </span>
  );
}

function EmptyHint() {
  return (
    <div className="text-center py-md px-sm">
      <p className="text-body-md text-on-surface mb-xs">和你的 AI 教练对话</p>
      <p className="text-label-md text-on-surface-variant">
        可直接提问；从报告进入会自动附加该次分析。已删除的分析会保留在引用列表中并标记为已删除。
      </p>
    </div>
  );
}

function MessageBubble({ message }: { message: CoachThreadMessageOut }) {
  const isUser = message.role === "user";
  const time = message.created_at?.slice(11, 16) ?? "";

  if (isUser) {
    return (
      <div className="flex flex-col items-end gap-xs w-full">
        <div className="max-w-[85%] bg-primary text-on-primary p-md rounded-xl rounded-tr-none shadow-md">
          <p className="text-body-md font-medium leading-relaxed whitespace-pre-wrap break-words">
            {message.content}
          </p>
        </div>
        <span className="text-[10px] text-on-surface-variant font-mono text-right pr-1 uppercase tracking-widest">
          {time} · YOU
        </span>
      </div>
    );
  }

  return (
    <div className="flex gap-sm">
      <div className="w-8 h-8 rounded bg-primary-container shrink-0 flex items-center justify-center mt-1">
        <span className="material-symbols-outlined text-on-primary-container text-xs">
          auto_awesome
        </span>
      </div>
      <div className="flex flex-col gap-xs max-w-[90%]">
        <div className="bg-background border border-outline p-md rounded-xl rounded-tl-none shadow-sm">
          <p className="text-body-md text-on-surface leading-relaxed whitespace-pre-wrap break-words">
            {message.content}
          </p>
        </div>
        <span className="text-[10px] text-on-surface-variant font-mono pl-1 uppercase tracking-widest">
          {time} · AI COACH
        </span>
      </div>
    </div>
  );
}
