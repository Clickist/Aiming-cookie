"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createCoachAgentRun,
  decideCoachConfirmation,
  detachCoachContext,
  getCoachAgentRun,
  getCoachContexts,
  getCoachPrimary,
  retryCoachAgentRun,
  stopCoachAgentRun,
} from "@/lib/api";
import { presentCoachContext } from "@/lib/contracts";
import type {
  CoachAgentRunV1,
  CoachConfirmationV1,
  CoachContextRefV1,
  CoachThreadMessageOut,
  ProviderProfileState,
} from "@/lib/types";
import { Badge, Button, Dialog, Empty, ErrorState, Notice, Status, Toast } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";

function confirmationFromPayload(payload: Record<string, unknown> | null): CoachConfirmationV1 | null {
  const value = payload?.confirmation;
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<CoachConfirmationV1>;
  return candidate.schema_version === "coach_confirmation.v1" && typeof candidate.confirmation_ref === "string"
    ? candidate as CoachConfirmationV1
    : null;
}

function capabilityLabel(capability: Exclude<CoachCapability, "loading" | "ready">): string {
  switch (capability) {
    case "unconfigured": return "尚未配置 Provider";
    case "auth_expired": return "Provider 认证已过期";
    case "needs_reauth": return "Provider 需要重新认证";
    case "model_unavailable": return "所选模型不可用";
    case "connection_failed": return "Provider 连接失败";
    case "unavailable": return "Coach 本地服务不可用";
  }
}

function runErrorTitle(error: CoachAgentRunV1["error"]): string {
  switch (error?.domain) {
    case "network": return "网络不可用";
    case "model": return "模型生成失败";
    case "permission": return "操作权限不足";
    case "tool": return "工具执行失败";
    default: return "Coach 生成失败";
  }
}

export function CoachPanel({
  capability,
  currentAnalysisRef,
  onRequestContext,
}: {
  capability: CoachCapability;
  currentAnalysisRef: string | null;
  onRequestContext: (analysisRef: string) => Promise<void>;
}) {
  const [contexts, setContexts] = useState<CoachContextRefV1[]>([]);
  const [messages, setMessages] = useState<CoachThreadMessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<CoachAgentRunV1 | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<CoachConfirmationV1 | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    if (capability !== "ready") return;
    try {
      const [contextResult, primary] = await Promise.all([getCoachContexts(), getCoachPrimary()]);
      setContexts(contextResult.contexts.filter((context) => context.status !== "detached"));
      setMessages(primary.messages);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [capability]);

  useEffect(() => {
    void refresh();
    const handleContextUpdate = () => void refresh();
    window.addEventListener("aiming-cookie:coach-context-updated", handleContextUpdate);
    return () => {
      window.removeEventListener("aiming-cookie:coach-context-updated", handleContextUpdate);
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [refresh]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await getCoachAgentRun(run.run_ref);
        if (cancelled) return;
        setRun(next);
        const confirmation = next.events
          .map((event) => confirmationFromPayload(event.payload))
          .find(Boolean);
        if (confirmation) setPendingConfirmation(confirmation);
        if (["queued", "running"].includes(next.status)) {
          pollRef.current = setTimeout(() => void poll(), 700);
        } else {
          await refresh();
          if (next.status === "succeeded") setRun(null);
        }
      } catch {
        if (!cancelled) setLoadError(true);
      }
    };
    pollRef.current = setTimeout(() => void poll(), 400);
    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [refresh, run]);

  const visibleContexts = useMemo(
    () => contexts.map(presentCoachContext),
    [contexts],
  );
  const activeAnalysisRefs = new Set(contexts.filter((item) => item.status === "active").map((item) => item.analysis_ref));
  const contextSwitchNotice = currentAnalysisRef && activeAnalysisRefs.size > 0 && !activeAnalysisRefs.has(currentAnalysisRef);

  const removeContext = async (contextRef: string) => {
    try {
      await detachCoachContext(contextRef);
      setFeedback("已移除上下文；旧消息保留发送时使用的引用。");
      await refresh();
    } catch {
      setFeedback("上下文未能移除，请重试。");
    }
  };

  const locateContext = (context: ReturnType<typeof presentCoachContext>) => {
    const located = !window.dispatchEvent(new CustomEvent("aiming-cookie:coach-locate", {
      cancelable: true,
      detail: context.locator ?? { kind: context.kind },
    }));
    setFeedback(located ? "已定位" : "未能定位，请重试。");
  };

  const send = async () => {
    const content = draft.trim();
    if (!content || run?.status === "running" || run?.status === "queued") return;
    try {
      const created = await createCoachAgentRun(
        content,
        contexts.filter((context) => context.status === "active").map((context) => context.context_ref),
      );
      setDraft("");
      setRun(created);
    } catch {
      setFeedback("消息未发送，草稿已保留，请重试。");
    }
  };

  const retry = async () => {
    if (!run) return;
    try {
      setRun(await retryCoachAgentRun(run.run_ref));
    } catch {
      setFeedback("重试未能开始，请稍后再试。");
    }
  };

  const stop = async () => {
    if (!run) return;
    try {
      setRun(await stopCoachAgentRun(run.run_ref));
    } catch {
      setFeedback("未能停止生成，请重试。");
    }
  };

  const decide = async (decision: "confirm" | "reject") => {
    if (!pendingConfirmation) return;
    try {
      const result = await decideCoachConfirmation(pendingConfirmation.confirmation_ref, decision);
      setPendingConfirmation(null);
      setFeedback(result.audit_state === "pending" ? "操作已确认，正在恢复执行。" : decision === "confirm" ? "操作已确认并完成审计。" : "已拒绝操作。");
    } catch {
      setFeedback("操作结果暂时无法确认；请刷新状态，系统不会自动重复执行。");
    }
  };

  if (capability === "loading") return <div className="task6-coach-state"><Status>正在读取 Coach 状态</Status></div>;
  if (capability !== "ready") {
    const label = capabilityLabel(capability);
    return (
      <div className="task6-coach-state">
        <Status tone={capability === "unavailable" ? "error" : "warning"}>
          {label}
        </Status>
        <h2>{capability === "unconfigured" ? "激活 Coach" : "恢复 Coach"}</h2>
        <p>本地 Analysis、History 和确定性诊断保持可用。连接第三方 Provider 后才会显示对话与上下文。</p>
        <Button href="/settings" variant="secondary">打开 Provider 设置</Button>
      </div>
    );
  }

  if (loadError && messages.length === 0) {
    return <ErrorState title="Coach 暂时不可用"><Button onClick={() => void refresh()} variant="secondary">重试</Button></ErrorState>;
  }

  return (
    <div className="task6-coach-panel">
      <section aria-label="发送上下文" className="task6-contexts">
        <div className="task6-section-heading">
          <span>上下文</span>
          <Badge>{visibleContexts.length}</Badge>
        </div>
        {visibleContexts.length ? (
          <div className="task6-context-list">
            {visibleContexts.map((context) => (
              <div className="task6-context-chip" data-status={context.status} key={context.contextRef}>
                <button disabled={context.status === "deleted"} onClick={() => locateContext(context)} type="button">
                  <span>{context.label}</span>
                  {context.status === "deleted" ? <small>已删除 / unavailable</small> : null}
                </button>
                <button aria-label={`移除 ${context.label}`} onClick={() => void removeContext(context.contextRef)} type="button">×</button>
              </div>
            ))}
          </div>
        ) : <p className="task6-muted">当前会话没有绑定分析，可直接进行一般训练问答。</p>}
        {contextSwitchNotice ? (
          <Notice tone="warning" title="当前页面与会话上下文不同">
            已有引用不会被静默替换。附加当前 Analysis 后可跨记录比较。
          </Notice>
        ) : null}
        {currentAnalysisRef && !activeAnalysisRefs.has(currentAnalysisRef) ? (
          <Button onClick={() => void onRequestContext(currentAnalysisRef).catch(() => setFeedback("当前 Analysis 未能附加，请重试。"))} size="compact" variant="ghost">附加当前 Analysis</Button>
        ) : null}
      </section>

      <section aria-label="Coach 消息" className="task6-messages">
        {messages.length === 0 && !run ? <Empty title="开始一段 Coach 对话">可以不绑定任何训练记录，也可以附加 1～N 条分析引用。</Empty> : null}
        {messages.map((message) => {
          const messageContexts = message.context_refs.map(presentCoachContext);
          return (
            <article className="task6-message" data-role={message.role} key={message.id}>
              <header>{message.role === "user" ? "你" : "Coach"}</header>
              <p>{message.content}</p>
              {messageContexts.length ? (
                <div aria-label="本条消息使用的上下文" className="task6-message-contexts">
                  {messageContexts.map((context) => (
                    <button disabled={context.status === "deleted"} key={context.contextRef} onClick={() => locateContext(context)} type="button">
                      {context.label}{context.status === "deleted" ? "（已删除）" : ""}
                    </button>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
        {run?.partial_text ? (
          <article className="task6-message" data-role="assistant"><header>Coach</header><p>{run.partial_text}</p></article>
        ) : null}
        {run?.events.filter((event) => event.type === "tool").map((event) => (
          <div className="task6-tool-event" key={event.event_ref}><Status tone="info">工具执行</Status><span>{event.message}</span></div>
        ))}
        {run?.status === "failed" ? (
          <Notice tone="warning" title={runErrorTitle(run.error)}>
            <p>{run.error?.message ?? "已生成内容会保留。"}</p>
            {run.error?.retryable ? <Button onClick={() => void retry()} size="compact" variant="secondary">重试</Button> : null}
          </Notice>
        ) : null}
        {run?.status === "stopped" ? <Notice title="生成已停止">已生成的部分内容已保留，可以修改问题后再次发送。</Notice> : null}
      </section>

      <footer className="task6-composer">
        <label htmlFor="coach-draft">消息</label>
        <textarea
          id="coach-draft"
          onChange={(event) => setDraft(event.target.value)}
          placeholder="询问训练、诊断或复测建议"
          rows={3}
          value={draft}
        />
        <div className="task6-composer-actions">
          {run && ["queued", "running"].includes(run.status) ? (
            <Button onClick={() => void stop()} variant="secondary">停止</Button>
          ) : null}
          <Button disabled={!draft.trim() || Boolean(run && ["queued", "running"].includes(run.status))} onClick={() => void send()}>发送</Button>
        </div>
        <div aria-live="polite" className="task6-live-region">
          {run?.phase === "queued" ? "Coach 正在等待开始" : run?.phase === "tool_execution" ? "Coach 正在执行工具" : run?.phase === "text_generation" ? "Coach 正在生成回复" : ""}
        </div>
      </footer>

      <Dialog
        footer={<><Button onClick={() => void decide("reject")} variant="secondary">拒绝</Button><Button onClick={() => void decide("confirm")}>确认执行</Button></>}
        onClose={() => void decide("reject")}
        open={Boolean(pendingConfirmation)}
        title="确认 Coach 操作"
      >
        <p>{pendingConfirmation?.impact.message}</p>
      </Dialog>
      {feedback ? <Toast onClose={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
