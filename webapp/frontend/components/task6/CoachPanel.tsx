"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createCoachAgentRun,
  decideCoachConfirmation,
  detachCoachContext,
  getCoachAgentRun,
  getCoachContexts,
  getCoachPrimary,
  getCurrentTraining,
  retryCoachAgentRun,
  stopCoachAgentRun,
} from "@/lib/api";
import { openKovaakScenario } from "@/lib/desktop";
import { presentCoachContext } from "@/lib/contracts";
import type {
  CoachAgentRunV1,
  CoachConfirmationV1,
  CoachContextRefV1,
  CoachThreadMessageOut,
  CurrentTrainingItemV1,
  CurrentTrainingV1,
  ProviderProfileState,
} from "@/lib/types";
import { Button, Empty, ErrorState, IconButton, Notice, Status, Toast } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachLayoutMode = "side-by-side" | "overlay" | "full";
const COACH_PENDING_INTENT_KEY = "aiming-cookie.ui.coach-pending-intent";

function pageLabel(pathname: string): string {
  if (pathname.startsWith("/tasks")) return "任务状态";
  if (pathname.startsWith("/analyze")) return "新建分析";
  if (pathname.startsWith("/history")) return "历史";
  if (pathname.startsWith("/settings")) return "设置";
  return "当前页面";
}

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

function trainingStatusLabel(status: CurrentTrainingItemV1["status"]): string {
  switch (status) {
    case "active": return "进行中";
    case "planned": return "待练习";
    case "completed": return "已完成";
    case "cancelled": return "已取消";
  }
}

function trainingSummaryItem(training: CurrentTrainingV1): CurrentTrainingItemV1 | null {
  return training.items.find((item) => item.status === "active") ?? training.items[0] ?? null;
}

function validKovaaKItemName(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.trim().length <= 120;
}

function contextLabel(context: ReturnType<typeof presentCoachContext>, prefix: string): string {
  return context.kind === "analysis" ? `${prefix}${context.label}` : context.label;
}

type ToolStepState = "done" | "active" | "fail";

interface ToolStep {
  key: string;
  label: string;
  meta: string | null;
  state: ToolStepState;
}

/* 工具步骤标签：已知 product command 用中文呈现，未知的回退为合同里的 command_name；
   标签映射是纯前端呈现层，不改任何后端合同。 */
const TOOL_COMMAND_LABELS: Record<string, string> = {
  "analysis.evidence.list": "读取分析证据",
  "analysis.events.list": "读取事件记录",
  "analysis.events.filter": "筛选事件记录",
  "analysis.outcomes.timeline": "比较历史表现",
  "analysis.retry": "重新分析",
  "kovaak_scores.refresh_connected": "刷新 KovaaK 成绩",
  "training_plan.item.add": "更新训练安排",
  "training_plan.execution.record": "记录训练执行",
  "training_plan.retest.record": "记录复测结果",
};

function deriveToolSteps(run: CoachAgentRunV1 | null): ToolStep[] {
  if (!run) return [];
  const steps = run.events
    .filter((event) => event.type === "tool")
    .map((event, index) => {
      const payload = event.payload ?? {};
      const commandName = typeof payload.command_name === "string" ? payload.command_name : null;
      const topic = typeof payload.topic === "string" ? payload.topic : null;
      const warning = payload.warning_or_error;
      const warningMessage = warning && typeof warning === "object" && typeof (warning as { message?: unknown }).message === "string"
        ? (warning as { message: string }).message
        : null;
      const failed = event.code === "failed" || event.code === "cancelled" || event.code === "unavailable";
      return {
        key: event.event_ref || `tool-${index}`,
        label: commandName ? TOOL_COMMAND_LABELS[commandName] ?? commandName : topic ? "查阅训练知识" : event.message,
        meta: warningMessage ?? (commandName ? null : topic),
        state: (failed ? "fail" : "done") as ToolStepState,
      };
    });
  if ((run.status === "queued" || run.status === "running") && run.phase === "tool_execution") {
    steps.push({ key: "tool-active", label: "正在执行工具…", meta: null, state: "active" });
  }
  return steps;
}

function kovaakIntentDraft(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const itemName = (value as { item_name?: unknown }).item_name;
  return validKovaaKItemName(itemName)
    ? `请优先看看 KovaaK 项目「${itemName.trim()}」该怎么练。`
    : null;
}

export function CoachPanel({
  capability,
  currentAnalysisRef,
  layoutMode = "side-by-side",
  onRequestContext,
  pathname = "/history",
}: {
  capability: CoachCapability;
  currentAnalysisRef: string | null;
  layoutMode?: CoachLayoutMode;
  onRequestContext: (analysisRef: string) => Promise<void>;
  pathname?: string;
}) {
  const [contexts, setContexts] = useState<CoachContextRefV1[]>([]);
  const [messages, setMessages] = useState<CoachThreadMessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<CoachAgentRunV1 | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<CoachConfirmationV1 | null>(null);
  const [currentTraining, setCurrentTraining] = useState<CurrentTrainingV1 | null>(null);
  const [currentTrainingError, setCurrentTrainingError] = useState(false);
  const [trainingExpanded, setTrainingExpanded] = useState(false);
  const [launchingScenarioRef, setLaunchingScenarioRef] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesRef = useRef<HTMLElement | null>(null);
  const stickToBottomRef = useRef(true);
  const seenFeedCountRef = useRef(0);
  const [unreadCount, setUnreadCount] = useState(0);

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
    let cancelled = false;
    void getCurrentTraining().then((training) => {
      if (cancelled) return;
      if (training.schema_version !== "current_training.v1") {
        setCurrentTraining(null);
        setCurrentTrainingError(true);
        return;
      }
      setCurrentTraining(training);
      setCurrentTrainingError(false);
    }).catch(() => {
      if (!cancelled) setCurrentTrainingError(true);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const handleKovaaKIntent = (event: Event) => {
      const nextDraft = kovaakIntentDraft((event as CustomEvent<unknown>).detail);
      if (nextDraft) setDraft(nextDraft);
    };
    const pending = window.sessionStorage.getItem(COACH_PENDING_INTENT_KEY);
    if (pending) {
      window.sessionStorage.removeItem(COACH_PENDING_INTENT_KEY);
      try {
        const nextDraft = kovaakIntentDraft(JSON.parse(pending));
        if (nextDraft) setDraft(nextDraft);
      } catch {
        // Ignore malformed local UI intent data.
      }
    }
    window.addEventListener("aiming-cookie:coach-kovaak-intent", handleKovaaKIntent);
    return () => window.removeEventListener("aiming-cookie:coach-kovaak-intent", handleKovaaKIntent);
  }, []);

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

  const toolSteps = useMemo(() => deriveToolSteps(run), [run]);
  // 对话流条目数：历史消息 + 当前 run 块（流式文字/工具步骤/卡片合记为 1 条）
  const feedCount = messages.length + (run ? 1 : 0);

  // 自动回到底部：用户已在底部时跟随新内容；向上阅读时不抢滚动，累计未读提示
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      seenFeedCountRef.current = feedCount;
      setUnreadCount((count) => (count ? 0 : count));
    } else if (feedCount > seenFeedCountRef.current) {
      setUnreadCount(feedCount - seenFeedCountRef.current);
    }
  }, [feedCount, messages, run]);

  const handleMessagesScroll = () => {
    const el = messagesRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    stickToBottomRef.current = atBottom;
    if (atBottom) {
      seenFeedCountRef.current = feedCount;
      setUnreadCount(0);
    }
  };

  const scrollToLatest = () => {
    const el = messagesRef.current;
    if (!el) return;
    stickToBottomRef.current = true;
    el.scrollTop = el.scrollHeight;
    seenFeedCountRef.current = feedCount;
    setUnreadCount(0);
  };

  const visibleContexts = useMemo(
    () => contexts.map(presentCoachContext),
    [contexts],
  );
  const activeAnalysisRefs = new Set(contexts.filter((item) => item.status === "active").map((item) => item.analysis_ref));
  const summaryItem = currentTraining ? trainingSummaryItem(currentTraining) : null;
  const visibleTrainingItems = currentTraining?.items.slice(0, 3) ?? [];

  const writeTrainingQuestion = (item: CurrentTrainingItemV1) => {
    if (!item.display_name || capability !== "ready") return;
    setDraft(`请根据我当前的「${item.display_name}」训练安排，帮我解释下一步应关注什么。`);
  };

  const startTrainingScenario = async (item: CurrentTrainingItemV1) => {
    const scenarioProfileRef = item.scenario_profile_ref;
    if (!scenarioProfileRef) {
      setFeedback("该训练项目暂时没有可验证的 KovaaK 场景");
      return;
    }
    setLaunchingScenarioRef(scenarioProfileRef);
    try {
      const result = await openKovaakScenario(scenarioProfileRef);
      setFeedback(result.message);
    } catch {
      setFeedback("未能请求打开 KovaaK，请稍后重试");
    } finally {
      setLaunchingScenarioRef(null);
    }
  };

  const renderTrainingLaunch = (item: CurrentTrainingItemV1) => {
    if (!item.scenario_profile_ref || item.scenario_availability !== "available") {
      return <small className="task6-training-unavailable">尚未绑定可启动的 KovaaK 场景</small>;
    }
    return (
      <Button
        disabled={launchingScenarioRef !== null}
        onClick={() => void startTrainingScenario(item)}
        size="compact"
        variant="primary"
      >
        {launchingScenarioRef === item.scenario_profile_ref ? "正在打开…" : "在 KovaaK 中开始"}
      </Button>
    );
  };

  const trainingSection = (
    <section aria-label="当前训练计划" className="task6-current-training" role="region">
      <div className="task6-current-training-head">
        <span className="task6-current-training-title">当前训练计划</span>
        {currentTraining?.plan_status === "paused" ? <Status tone="warning">已暂停</Status> : null}
        {currentTraining?.plan_status === "active" ? <Status tone="info">进行中</Status> : null}
        <button
          aria-expanded={trainingExpanded}
          className="task6-training-toggle"
          onClick={() => setTrainingExpanded((expanded) => !expanded)}
          type="button"
        >
          {trainingExpanded ? "收起" : "展开"}
        </button>
      </div>
      {currentTrainingError ? <ErrorState title="当前训练暂时无法读取" /> : null}
      {currentTraining?.availability === "unavailable" ? <Notice tone="warning" title="当前训练暂不可用">本地训练摘要暂时无法读取，稍后再试。</Notice> : null}
      {currentTraining?.reason === "no_current_plan" ? (
        <div className="task6-training-empty">
          <div className="task6-training-empty-title">还没有当前训练安排</div>
          <p>创建训练安排后，这里会显示练什么、练多少和本轮注意点。</p>
        </div>
      ) : null}
      {summaryItem ? (
        <>
          <div className="task6-current-training-scenario">
            <span className="task6-training-scenario-label">当前训练项目</span>
            <strong>{summaryItem.display_name ?? "未命名项目"}</strong>
          </div>
          <dl className="task6-training-kv">
            <dt>练什么</dt><dd>{summaryItem.practice_condition ?? "暂未说明"}</dd>
            <dt>练多少</dt><dd>{summaryItem.dose_guardrail ?? "暂未说明"}</dd>
            <dt>注意</dt><dd>{summaryItem.cue ?? "暂未说明"}</dd>
          </dl>
          <div className="task6-training-actions">
            {renderTrainingLaunch(summaryItem)}
            <Button disabled={capability !== "ready"} onClick={() => writeTrainingQuestion(summaryItem)} size="compact" variant="secondary">问 Coach</Button>
          </div>
          {trainingExpanded ? (
            <div className="task6-training-list">
              {visibleTrainingItems.map((item, index) => (
                <article className="task6-training-item" data-status={item.status} key={`${item.display_name ?? "item"}-${index}`}>
                  <div className="task6-training-item-title">
                    <strong>{item.display_name ?? "当前训练项目"}</strong>
                    <Status tone={item.status === "completed" ? "info" : item.status === "cancelled" ? "warning" : "neutral"}>{trainingStatusLabel(item.status)}</Status>
                  </div>
                  <p>{item.cue ?? item.practice_condition ?? "暂无可展示的训练说明。"}</p>
                  {item.scenario_availability === "unavailable" ? <small className="task6-training-unavailable">项目暂不可用</small> : null}
                  <div className="task6-training-item-actions">
                    {renderTrainingLaunch(item)}
                    <Button disabled={capability !== "ready" || !item.display_name} onClick={() => writeTrainingQuestion(item)} size="compact" variant="secondary">问 Coach</Button>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );

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
      stickToBottomRef.current = true;
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

  const newTopic = async () => {
    setDraft("");
    setRun(null);
    try {
      const active = contexts.filter((context) => context.status === "active");
      for (const context of active) {
        await detachCoachContext(context.context_ref);
      }
      if (active.length) {
        setFeedback("已清除当前会话上下文；历史消息仍保留。");
      }
      await refresh();
    } catch {
      setFeedback("未能清除上下文，请重试。");
    }
  };

  const headerState = capability === "loading"
    ? { state: "neutral", label: "正在读取" }
    : capability === "ready"
      ? { state: "success", label: "可用" }
      : capability === "unavailable"
        ? { state: "error", label: capabilityLabel(capability) }
        : { state: "warning", label: capabilityLabel(capability) };

  const activeAnalysisContext = visibleContexts.find((context) => context.kind === "analysis" && context.status === "active");
  const currentAnalysisLabel = activeAnalysisContext?.label ?? (currentAnalysisRef ? "当前分析" : null);

  const suggestionItems = useMemo(() => {
    const base = ["总结最近进步"];
    if (summaryItem?.display_name) {
      base.push(`关于「${summaryItem.display_name}」的训练建议`);
    }
    if (currentAnalysisLabel) {
      base.push("这次分析的核心问题是什么");
    }
    if (base.length < 2) base.push("今天练什么");
    return base.slice(0, 3);
  }, [summaryItem?.display_name, currentAnalysisLabel]);

  const header = (
    <header className={["task6-coach-header", layoutMode === "full" ? "task6-coach-full-header" : ""].filter(Boolean).join(" ")}>
      <div className="task6-coach-header-row">
        <span className="task6-coach-title">Aiming Coach</span>
        <span className="task6-coach-availability" data-state={headerState.state}>{headerState.label}</span>
        <div className="task6-coach-header-actions">
          {capability === "ready" ? (
            <IconButton label="开始新话题" onClick={() => void newTopic()} title="开始新话题">＋</IconButton>
          ) : null}
        </div>
      </div>
      <div className="task6-coach-context">
        {currentAnalysisLabel ? (
          <div className="task6-coach-context-line">正在查看的分析：<b>{currentAnalysisLabel}</b></div>
        ) : (
          <div className="task6-coach-context-line">当前页面：<b>{pageLabel(pathname)}</b>（未附加具体分析）</div>
        )}
      </div>
    </header>
  );

  if (capability === "loading") {
    return (
      <div className="task6-coach-panel">
        {header}
        <div className="task6-coach-state">
          <Status>正在读取 Coach 状态</Status>
        </div>
      </div>
    );
  }

  if (capability !== "ready") {
    return (
      <div className="task6-coach-panel">
        {header}
        {trainingSection}
        <div className="task6-coach-state">
          <Status tone={capability === "unavailable" ? "error" : "warning"}>
            {capabilityLabel(capability)}
          </Status>
          <h2>{capability === "unconfigured" ? "激活 Coach" : "恢复 Coach"}</h2>
          <p>本地 Analysis、History 和确定性诊断保持可用。连接第三方 Provider 后才会显示对话与上下文。</p>
          <Button href="/settings" variant="secondary">打开 Provider 设置</Button>
        </div>
      </div>
    );
  }

  if (loadError && messages.length === 0) {
    return (
      <div className="task6-coach-panel">
        {header}
        {trainingSection}
        <div className="task6-coach-state">
          <ErrorState title="Coach 暂时不可用"><Button onClick={() => void refresh()} variant="secondary">重试</Button></ErrorState>
        </div>
      </div>
    );
  }

  return (
    <div className="task6-coach-panel">
      {header}
      {trainingSection}

      <div className="task6-messages-wrap">
      <section aria-label="Coach 消息" className="task6-messages" onScroll={handleMessagesScroll} ref={messagesRef}>
        {messages.length === 0 && !run ? (
          <Empty title="开始一段 Coach 对话">可以不绑定任何训练记录，也可以附加 1～N 条分析引用。</Empty>
        ) : null}
        {messages.map((message) => {
          const messageContexts = message.context_refs.map(presentCoachContext);
          return (
            <article className="task6-message" data-role={message.role} key={message.id}>
              <p>{message.content}</p>
              {messageContexts.length ? (
                <div aria-label="本条消息使用的上下文" className="task6-message-contexts">
                  {messageContexts.map((context) => (
                    <button disabled={context.status === "deleted"} key={context.contextRef} onClick={() => locateContext(context)} type="button">
                      {contextLabel(context, "引用分析：")}{context.status === "deleted" ? "（已删除）" : ""}
                    </button>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
        {run?.partial_text ? (
          <article className="task6-message" data-role="assistant">
            <p>{run.partial_text}<span className="task6-streaming-cursor" /></p>
          </article>
        ) : null}
        {toolSteps.length ? (
          <ol aria-label="工具执行步骤" className="task6-tool-tl">
            {toolSteps.map((step) => (
              <li className="task6-tool-step" data-state={step.state} key={step.key}>
                <span aria-hidden="true" className="task6-tool-dot" />
                <span className="task6-tool-body">
                  <span className="task6-tool-label">{step.label}</span>
                  {step.meta ? <span className="task6-tool-meta">{step.meta}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
        {run?.status === "failed" ? (
          <div className="task6-error-card" role="alert">
            <div className="task6-error-card-head">
              <div className="task6-error-card-title">{runErrorTitle(run.error)}</div>
              <div className="task6-error-card-desc">
                {run.error?.message ?? "Coach 暂时无法响应。"} 已生成的部分已保留；本地分析、历史和视频不受影响。
              </div>
            </div>
            <div className="task6-error-card-actions">
              {run.error?.retryable ? (
                <Button onClick={() => void retry()} size="compact" variant="secondary">重试</Button>
              ) : null}
              <Button onClick={() => setRun(null)} size="compact" variant="ghost">稍后再说</Button>
            </div>
          </div>
        ) : null}
        {pendingConfirmation ? (
          <div className="task6-cfm-card">
            <div className="task6-cfm-head">
              <div>
                <div className="task6-cfm-title">确认 Coach 操作</div>
                <div className="task6-cfm-desc">{pendingConfirmation.impact.message}</div>
              </div>
            </div>
            <div className="task6-cfm-note">拒绝不会改变任何东西。确认后操作不可撤销，但你的 Stats / Performance 源文件永远不会被删除。</div>
            <div className="task6-cfm-actions">
              <Button onClick={() => void decide("reject")} size="compact" variant="ghost">拒绝</Button>
              <Button className="task6-cfm-danger" onClick={() => void decide("confirm")} size="compact" variant="secondary">确认执行</Button>
            </div>
          </div>
        ) : null}
        {run?.status === "stopped" ? <Notice title="生成已停止">已生成的部分内容已保留，可以修改问题后再次发送。</Notice> : null}
        {!run ? (
          <div className="task6-suggestions">
            {suggestionItems.map((text) => (
              <button className="task6-suggestion" key={text} onClick={() => setDraft(text)} type="button">{text}</button>
            ))}
          </div>
        ) : null}
      </section>
      {unreadCount > 0 ? (
        <button className="task6-unread-prompt" onClick={scrollToLatest} type="button">
          ↓ {unreadCount} 条新内容 · 回到底部
        </button>
      ) : null}
      </div>

      <footer className="task6-composer">
        <div className="task6-composer-attachments">
          {visibleContexts.length ? (
            visibleContexts.map((context) => (
              <span className={["task6-context-chip", context.status === "deleted" ? "" : "task6-context-chip-ref"].filter(Boolean).join(" ")} data-status={context.status} key={context.contextRef}>
                <button disabled={context.status === "deleted"} onClick={() => locateContext(context)} type="button">{contextLabel(context, "已附加分析：")}</button>
                <button aria-label={`移除 ${context.label}`} onClick={() => void removeContext(context.contextRef)} type="button">×</button>
              </span>
            ))
          ) : null}
          {currentAnalysisRef && !activeAnalysisRefs.has(currentAnalysisRef) ? (
            <button
              className="task6-context-chip task6-context-chip-add"
              onClick={() => void onRequestContext(currentAnalysisRef).catch(() => setFeedback("当前 Analysis 未能附加，请重试。"))}
              type="button"
            >
              ＋ 附加当前 Analysis
            </button>
          ) : null}
        </div>
        <div className="task6-composer-input">
          <textarea
            id="coach-draft"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder={currentAnalysisLabel ? "继续追问这次分析…" : "向 Coach 提问，可以聊训练，也可以让它帮你操作应用…"}
            rows={1}
            value={draft}
          />
          {run && ["queued", "running"].includes(run.status) ? (
            <button aria-label="停止生成" className="task6-composer-send" onClick={() => void stop()} type="button" title="停止生成">■</button>
          ) : (
            <button aria-label="发送" className="task6-composer-send" disabled={!draft.trim()} onClick={() => void send()} type="button">↑</button>
          )}
        </div>
        <div aria-live="polite" className="task6-composer-status">
          {run?.phase === "queued" ? "Coach 正在等待开始" : run?.phase === "tool_execution" ? "Coach 正在执行工具" : run?.phase === "text_generation" ? "Coach 正在生成回复" : ""}
        </div>
      </footer>

      {feedback ? <Toast onClose={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
