"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { deleteSession, listSessions } from "@/lib/api";
import type { SessionListItem, SessionStatusEnum } from "@/lib/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; sessions: SessionListItem[] }
  | { kind: "err"; message: string };

function sessionHref(item: SessionListItem): string {
  if (item.status === "done") {
    return `/sessions/${item.id}/report`;
  }
  return `/sessions/${item.id}`;
}

const STATUS_LABEL: Record<SessionStatusEnum, string> = {
  queued: "排队中",
  running: "分析中",
  done: "已完成",
  failed: "失败",
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function HistoryPage() {
  const router = useRouter();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const res = await listSessions();
      setState({ kind: "ok", sessions: res.sessions });
    } catch (err) {
      setState({
        kind: "err",
        message: err instanceof Error ? err.message : "加载失败",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleOpen = (item: SessionListItem) => {
    router.push(sessionHref(item));
  };

  const handleDelete = async (
    e: React.MouseEvent,
    item: SessionListItem,
  ) => {
    e.stopPropagation();
    const ok = window.confirm(
      `确定删除分析 #${item.id}？此操作不可恢复。`,
    );
    if (!ok) return;
    setDeletingId(item.id);
    try {
      await deleteSession(item.id);
      await load();
    } catch (err) {
      window.alert(
        err instanceof Error ? err.message : "删除失败，请稍后重试",
      );
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-dvh flex flex-col bg-background text-on-surface">
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline">
        <div className="flex items-center gap-sm">
          <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
            Aiming Cookie
          </span>
          <div className="h-4 w-px bg-outline mx-xs" />
          <span className="text-label-md text-on-surface-variant">
            历史记录
          </span>
        </div>
        <nav className="flex items-center gap-md">
          <Link
            href="/"
            className="text-label-md text-primary hover:brightness-110 transition-colors"
          >
            新建分析
          </Link>
        </nav>
      </header>

      <main className="flex-1 w-full max-w-[var(--spacing-container-max)] mx-auto px-md py-xl">
        <h1 className="font-display text-display-lg text-on-surface tracking-tight mb-md">
          历史记录
        </h1>

        {state.kind === "loading" && (
          <p className="text-body-md text-on-surface-variant">加载中…</p>
        )}

        {state.kind === "err" && (
          <div
            role="alert"
            className="border border-outline-variant bg-surface-container-high rounded-md px-md py-sm max-w-[36rem]"
          >
            <p className="text-label-md text-on-surface mb-sm">
              无法加载历史记录
            </p>
            <p className="text-body-md text-on-surface-variant break-words mb-md">
              {state.message}
            </p>
            <button
              type="button"
              onClick={() => void load()}
              className="text-label-md text-primary hover:brightness-110"
            >
              重试
            </button>
          </div>
        )}

        {state.kind === "ok" && state.sessions.length === 0 && (
          <div className="bg-surface-container-low border border-outline rounded-lg p-lg text-center max-w-[36rem]">
            <p className="text-body-lg text-on-surface-variant mb-md">
              还没有分析记录
            </p>
            <Link
              href="/"
              className="inline-block bg-primary text-on-primary font-label-md px-md py-sm rounded-md hover:brightness-110 transition-colors"
            >
              新建分析
            </Link>
          </div>
        )}

        {state.kind === "ok" && state.sessions.length > 0 && (
          <div className="border border-outline rounded-lg overflow-hidden bg-surface-container-low">
            <div className="hidden md:grid md:grid-cols-[4rem_1fr_6rem_10rem_auto] gap-sm px-md py-sm border-b border-outline-variant text-label-sm text-on-surface-variant uppercase tracking-wide">
              <span>ID</span>
              <span>摘要</span>
              <span>状态</span>
              <span>创建时间</span>
              <span className="text-right">操作</span>
            </div>
            <ul className="divide-y divide-outline-variant">
              {state.sessions.map((item) => (
                <li key={item.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => handleOpen(item)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleOpen(item);
                      }
                    }}
                    className="grid grid-cols-1 md:grid-cols-[4rem_1fr_6rem_10rem_auto] gap-xs md:gap-sm items-center px-md py-md cursor-pointer hover:bg-surface-container-high transition-colors"
                  >
                    <span className="font-mono text-label-md text-on-surface">
                      #{item.id}
                    </span>
                    <span className="text-body-md text-on-surface truncate">
                      {item.summary_label ?? "—"}
                    </span>
                    <span className="text-label-md text-on-surface-variant">
                      {STATUS_LABEL[item.status]}
                    </span>
                    <span className="text-label-sm text-on-surface-variant">
                      {formatTime(item.created_at)}
                    </span>
                    <div
                      className="flex items-center gap-sm justify-start md:justify-end"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        onClick={() => handleOpen(item)}
                        className="text-label-md text-primary hover:brightness-110"
                      >
                        查看
                      </button>
                      <button
                        type="button"
                        disabled={deletingId === item.id}
                        onClick={(e) => void handleDelete(e, item)}
                        className="text-label-md text-on-surface-variant hover:text-error disabled:opacity-50"
                      >
                        {deletingId === item.id ? "删除中…" : "删除"}
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>
    </div>
  );
}
