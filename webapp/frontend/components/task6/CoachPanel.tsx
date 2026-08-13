"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createCoachAgentRun,
  getCoachAgentRun,
  getCoachAgentRunStreamUrl,
  getCoachSession,
  getCurrentTraining,
  retryCoachAgentRun,
  stopCoachAgentRun,
} from "@/lib/api";
import { isDesktopRuntime, openKovaakScenario } from "@/lib/desktop";
import { CoachMessageText } from "@/components/task7/CoachMessageText";
import type {
  CoachAgentRunEventV1,
  CoachAgentRunV1,
  CoachThreadMessageOut,
  CurrentTrainingItemV1,
  CurrentTrainingV1,
  ProviderProfileState,
} from "@/lib/types";
import { Button, Empty, ErrorState, IconButton, Notice, Status, Toast, useAnimatedPresence } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachLayoutMode = "side-by-side" | "overlay" | "full";
const COACH_PENDING_INTENT_KEY = "aiming-cookie.ui.coach-pending-intent";

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

function requestFeedback(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  if (error.name === "DesktopRuntimeUnavailableError") {
    return "桌面运行时暂时不可用，草稿已保留，请重启应用后重试。";
  }
  if (error.name.startsWith("ApiError_") && error.message.trim()) {
    return fallback.replace("，请重试。", "") + "：" + error.message;
  }
  return fallback;
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
  get_analysis_summary: "读取已附加分析",
  get_coach_knowledge: "查阅训练知识",
  run_product_command: "查询产品数据",
  "run.list": "查询训练记录",
  "run.get": "读取训练详情",
  "history.list": "查询历史训练",
  "history.trend": "分析近期趋势",
  "analysis.get": "读取分析结果",
  "analysis.compare": "比较分析结果",
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
  const stepMap = new Map<string, ToolStep>();
  run.events
    .filter((event) => event.type === "tool")
    .forEach((event, index) => {
      const payload = event.payload ?? {};
      const toolCallId = typeof payload.tool_call_id === "string" ? payload.tool_call_id : null;
      const toolName = typeof payload.tool_name === "string" ? payload.tool_name : null;
      const commandName = typeof payload.command_name === "string" ? payload.command_name : null;
      const topic = typeof payload.topic === "string" ? payload.topic : null;
      const activityState = typeof payload.state === "string" ? payload.state : null;
      const warning = payload.warning_or_error;
      const warningMessage = warning && typeof warning === "object" && typeof (warning as { message?: unknown }).message === "string"
        ? (warning as { message: string }).message
        : null;
      const failed = activityState === "failed" || event.code === "failed" || event.code === "cancelled" || event.code === "unavailable";
      const key = toolCallId ?? event.event_ref ?? `tool-${index}`;
      const previous = stepMap.get(key);
      stepMap.set(key, {
        key,
        label: commandName
          ? TOOL_COMMAND_LABELS[commandName] ?? commandName
          : previous?.label ?? (toolName
            ? TOOL_COMMAND_LABELS[toolName] ?? toolName
            : topic ? "查阅训练知识" : event.message),
        meta: warningMessage ?? (commandName ? null : topic),
        state: (failed ? "fail" : activityState === "started" ? "active" : "done") as ToolStepState,
      });
    });
  const steps = [...stepMap.values()];
  if ((run.status === "queued" || run.status === "running") && !steps.some((step) => step.state === "active")) {
    steps.push({
      key: "coach-active",
      label: run.phase === "queued"
        ? "等待开始"
        : run.partial_text ? "正在组织回复" : "正在理解问题和分析上下文",
      meta: null,
      state: "active",
    });
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

function pendingIntentDraft(value: unknown): string | null {
  if (value && typeof value === "object") {
    const draft = (value as { draft?: unknown }).draft;
    if (typeof draft === "string" && draft.trim().length > 0 && draft.trim().length <= 240) {
      return draft.trim();
    }
  }
  return kovaakIntentDraft(value);
}

export function CoachPanel({
  capability,
  draftSession = false,
  sessionId = null,
  layoutMode = "full",
  onEnsureSession,
  onClose,
  onOpenVideo,
  pathname = "/history",
  softStartRun = null,
}: {
  capability: CoachCapability;
  draftSession?: boolean;
  sessionId?: number | null;
  layoutMode?: CoachLayoutMode;
  onEnsureSession?: () => Promise<number | null>;
  onClose?: () => void;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
  pathname?: string;
  softStartRun?: CoachAgentRunV1 | null;
}) {
  const [messages, setMessages] = useState<CoachThreadMessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<CoachAgentRunV1 | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [currentTraining, setCurrentTraining] = useState<CurrentTrainingV1 | null>(null);
  const [currentTrainingError, setCurrentTrainingError] = useState(false);
  const [trainingExpanded, setTrainingExpanded] = useState(false);
  const trainingPresence = useAnimatedPresence(trainingExpanded, 180);
  const [launchingScenarioRef, setLaunchingScenarioRef] = useState<string | null>(null);
  const [analysisSessionIds, setAnalysisSessionIds] = useState<number[]>([]);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesRef = useRef<HTMLElement | null>(null);
  const stickToBottomRef = useRef(true);
  const seenFeedCountRef = useRef(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const appliedSoftStartRef = useRef<string | null>(null);
  const refreshRevisionRef = useRef(0);
  const trainingRefreshRevisionRef = useRef(0);
  const optimisticMessageIdRef = useRef(-1);
  const activeSessionKey = draftSession ? "draft" : `session:${sessionId ?? "primary"}`;
  const activeSessionKeyRef = useRef(activeSessionKey);
  const runBySessionRef = useRef(new Map<string, CoachAgentRunV1>());
  activeSessionKeyRef.current = activeSessionKey;

  // The active run's reads win (streamed live); the session's engaged-analysis
  // list (persisted from completed runs) is the fallback after the run clears.
  const defaultAnalysisRef = run?.analysis_refs?.length
    ? run.analysis_refs[0]
    : analysisSessionIds.length ? `analysis:${analysisSessionIds[0]}` : null;

  const refresh = useCallback(async () => {
    if (capability !== "ready") return;
    if (draftSession || sessionId == null) {
      setMessages([]);
      setAnalysisSessionIds([]);
      setLoadError(false);
      return;
    }
    const revision = ++refreshRevisionRef.current;
    try {
      const detail = await getCoachSession(sessionId);
      if (revision !== refreshRevisionRef.current) return;
      setMessages(detail.messages ?? []);
      setAnalysisSessionIds(detail.analysis_session_ids ?? []);
      setLoadError(false);
    } catch {
      if (revision === refreshRevisionRef.current) setLoadError(true);
    }
  }, [capability, draftSession, sessionId]);

  useEffect(() => {
    setRun(runBySessionRef.current.get(activeSessionKey) ?? null);
    setUnreadCount(0);
    stickToBottomRef.current = true;
  }, [activeSessionKey]);

  useEffect(() => {
    if (run) {
      runBySessionRef.current.set(activeSessionKeyRef.current, run);
    } else {
      runBySessionRef.current.delete(activeSessionKeyRef.current);
    }
  }, [run]);

  const refreshCurrentTraining = useCallback(async () => {
    const revision = ++trainingRefreshRevisionRef.current;
    try {
      const training = await getCurrentTraining();
      if (revision !== trainingRefreshRevisionRef.current) return;
      if (training.schema_version !== "current_training.v1") {
        setCurrentTraining(null);
        setCurrentTrainingError(true);
        return;
      }
      setCurrentTraining(training);
      setCurrentTrainingError(false);
    } catch {
      if (revision === trainingRefreshRevisionRef.current) setCurrentTrainingError(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [refresh]);

  useEffect(() => {
    if (
      !softStartRun
      || (sessionId !== null && softStartRun.session_id !== sessionId)
      || appliedSoftStartRef.current === softStartRun.run_ref
    ) return;
    appliedSoftStartRef.current = softStartRun.run_ref;
    stickToBottomRef.current = true;
    if (["queued", "running"].includes(softStartRun.status)) {
      setRun(softStartRun);
      return;
    }
    setRun(null);
    void refresh();
  }, [refresh, softStartRun]);

  useEffect(() => {
    void refreshCurrentTraining();
  }, [refreshCurrentTraining]);

  // Pending intent from History/Settings must only be consumed by the Coach
  // workspace panel. Hidden panels on other routes must NOT read or destroy
  // the sessionStorage intent — otherwise it's gone before the user arrives.
  const isCoachWorkspace = pathname === "/" || pathname === "/s" || pathname === "/s/";

  useEffect(() => {
    const applyPendingIntent = (value: unknown) => {
      const nextDraft = pendingIntentDraft(value);
      if (nextDraft) setDraft(nextDraft);
    };
    const handleKovaaKIntent = (event: Event) => {
      if (!isCoachWorkspace) return;
      applyPendingIntent((event as CustomEvent<unknown>).detail);
    };
    const handleCoachDraft = (event: Event) => {
      if (!isCoachWorkspace) return;
      applyPendingIntent((event as CustomEvent<unknown>).detail);
      window.sessionStorage.removeItem(COACH_PENDING_INTENT_KEY);
    };
    // Read and consume sessionStorage only when on the Coach workspace.
    if (isCoachWorkspace) {
      const pending = window.sessionStorage.getItem(COACH_PENDING_INTENT_KEY);
      if (pending) {
        window.sessionStorage.removeItem(COACH_PENDING_INTENT_KEY);
        try {
          applyPendingIntent(JSON.parse(pending));
        } catch {
          // Ignore malformed local UI intent data.
        }
      }
    }
    window.addEventListener("aiming-cookie:coach-kovaak-intent", handleKovaaKIntent);
    window.addEventListener("aiming-cookie:coach-draft", handleCoachDraft);
    return () => {
      window.removeEventListener("aiming-cookie:coach-kovaak-intent", handleKovaaKIntent);
      window.removeEventListener("aiming-cookie:coach-draft", handleCoachDraft);
    };
  }, [isCoachWorkspace]);

  // 实时流式输出：优先走 sidecar SSE（token 级），EventSource 连接失败时回退到轮询。
  const liveRunRef = run && ["queued", "running"].includes(run.status) ? run.run_ref : null;

  useEffect(() => {
    if (!liveRunRef) return;
    let cancelled = false;
    let eventSource: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let streamActive = false;
    let finished = false;
    const runRef = liveRunRef;

    const clearPollTimer = () => {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    };

    const fetchRun = () => getCoachAgentRun(runRef, sessionId == null ? {} : { sessionId });

    const finalizeRun = async () => {
      try {
        const next = await fetchRun();
        if (cancelled) return;
        setRun(next);
        await Promise.all([refresh(), refreshCurrentTraining()]);
        if (next.status === "succeeded") setRun(null);
      } catch {
        if (!cancelled) setLoadError(true);
      }
    };

    const schedulePoll = () => {
      if (cancelled) return;
      clearPollTimer();
      pollTimer = setTimeout(async () => {
        try {
          const next = await fetchRun();
          if (cancelled) return;
          setRun(next);
          if (["queued", "running"].includes(next.status)) {
            schedulePoll();
          } else {
            await Promise.all([refresh(), refreshCurrentTraining()]);
            if (next.status === "succeeded") setRun(null);
          }
        } catch {
          if (!cancelled) setLoadError(true);
        }
      }, 700);
    };

    const closeStream = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };

    const startPolling = () => {
      if (cancelled || finished) return;
      closeStream();
      streamActive = false;
      schedulePoll();
    };

    const setupStream = async () => {
      if (cancelled) return;
      if (!isDesktopRuntime()) {
        // Browser/dev sessions have no sidecar SSE — use the polling fallback.
        schedulePoll();
        return;
      }
      let es: EventSource;
      try {
        const streamUrl = await getCoachAgentRunStreamUrl(runRef);
        if (cancelled) return;
        es = new EventSource(streamUrl);
      } catch {
        if (!cancelled) schedulePoll();
        return;
      }
      eventSource = es;
      let opened = false;

      es.onopen = () => {
        if (cancelled) return;
        opened = true;
        streamActive = true;
        clearPollTimer();
      };

      es.addEventListener("partial", (event: MessageEvent) => {
        if (cancelled || !opened) return;
        try {
          const data = JSON.parse(event.data) as { text?: unknown };
          const text = data.text;
          if (typeof text === "string") {
            setRun((prev) => (prev ? { ...prev, partial_text: text } : prev));
          }
        } catch {
          // Ignore malformed stream frames.
        }
      });

      es.addEventListener("activity", (event: MessageEvent) => {
        if (cancelled || !opened) return;
        try {
          const data = JSON.parse(event.data) as { event?: CoachAgentRunEventV1 };
          const streamedEvent = data.event;
          if (streamedEvent) {
            setRun((prev) => {
              if (!prev) return prev;
              if (prev.events.some((item) => item.sequence === streamedEvent.sequence)) return prev;
              return {
                ...prev,
                phase: streamedEvent.phase,
                events: [...prev.events, streamedEvent],
              };
            });
          }
        } catch {
          // Ignore malformed stream frames.
        }
      });

      es.addEventListener("done", () => {
        if (cancelled || finished) return;
        finished = true;
        closeStream();
        streamActive = false;
        void finalizeRun();
      });

      es.onerror = () => {
        if (cancelled || finished) return;
        // Stream failed or was interrupted: fall back to polling.
        startPolling();
      };
    };

    void setupStream();

    // Safety net: if the stream has not opened shortly after connecting, fall
    // back to polling instead of hanging on a dead EventSource.
    pollTimer = setTimeout(() => {
      if (cancelled || finished) return;
      if (!streamActive && eventSource) startPolling();
    }, 1500);

    return () => {
      cancelled = true;
      clearPollTimer();
      closeStream();
    };
  }, [liveRunRef, refresh, refreshCurrentTraining, sessionId]);

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

  const summaryItem = currentTraining ? trainingSummaryItem(currentTraining) : null;
  const visibleTrainingItems = currentTraining?.items.slice(0, 3) ?? [];
  const noCurrentPlan = currentTraining?.reason === "no_current_plan";

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
        {noCurrentPlan ? <span className="task6-training-empty-title">还没有当前训练安排</span> : null}
        {summaryItem && !trainingExpanded ? (
          <span className="task6-training-summary" title={summaryItem.display_name ?? "未命名项目"}>
            {summaryItem.display_name ?? "未命名项目"}
          </span>
        ) : null}
        {summaryItem ? (
          <button
            aria-expanded={trainingExpanded}
            className="task6-training-toggle"
            onClick={() => setTrainingExpanded((expanded) => !expanded)}
            type="button"
          >
            {trainingExpanded ? "收起" : "展开"}
          </button>
        ) : null}
      </div>
      {currentTrainingError && !currentTraining ? <ErrorState title="当前训练暂时无法读取" /> : null}
      {currentTraining?.availability === "unavailable" && currentTraining.reason !== "no_current_plan" ? <Notice tone="warning" title="当前训练暂不可用">本地训练摘要暂时无法读取，稍后再试。</Notice> : null}
      {summaryItem && trainingPresence.present ? (
        <div
          aria-hidden={!trainingExpanded || undefined}
          className="task6-training-reveal"
          data-state={trainingPresence.state}
          inert={!trainingExpanded || undefined}
        >
          <div className="task6-training-reveal-inner">
            <div className="task6-training-details">
              <div className="task6-current-training-scenario">
                <span className="task6-training-scenario-label">当前训练项目</span>
                <strong>{summaryItem.display_name ?? "未命名项目"}</strong>
              </div>
              <dl className="task6-training-kv">
                <dt>练什么</dt><dd>{summaryItem.practice_condition ?? "暂未说明"}</dd>
                <dt>练多少</dt><dd>{summaryItem.dose_guardrail ?? "暂未说明"}</dd>
                <dt>注意</dt><dd>{summaryItem.cue ?? "暂未说明"}</dd>
                <dt>观察</dt><dd>{summaryItem.observation ?? "暂未说明"}</dd>
                <dt>复测</dt><dd>{summaryItem.retest ?? "暂未说明"}</dd>
              </dl>
              <div className="task6-training-list">
                {visibleTrainingItems.map((item, index) => (
                  <article className="task6-training-item" data-status={item.status} key={`${item.display_name ?? "item"}-${index}`}>
                    <div className="task6-training-item-title">
                      <strong>{item.display_name ?? "当前训练项目"}</strong>
                      <Status tone={item.status === "completed" ? "success" : item.status === "cancelled" ? "warning" : "neutral"}>{trainingStatusLabel(item.status)}</Status>
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
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );

  const send = async () => {
    const content = draft.trim();
    if (!content || run?.status === "running" || run?.status === "queued") return;
    let optimisticId: number | null = null;
    try {
      const effectiveSessionId = sessionId ?? (onEnsureSession ? await onEnsureSession() : null);
      if (sessionId === null && onEnsureSession && effectiveSessionId === null) {
        setFeedback("未能创建会话，草稿已保留，请重试。");
        return;
      }
      const optimisticMessageId = optimisticMessageIdRef.current--;
      optimisticId = optimisticMessageId;
      setDraft("");
      stickToBottomRef.current = true;
      setMessages((current) => [...current, {
        id: optimisticMessageId,
        role: "user",
        content,
        created_at: new Date().toISOString(),
        legacy_session_id: null,
      }]);
      const created = await createCoachAgentRun(
        content,
        effectiveSessionId == null ? {} : { sessionId: effectiveSessionId },
      );
      setRun(created);
    } catch (error) {
      if (optimisticId !== null) {
        setMessages((current) => current.filter((message) => message.id !== optimisticId));
      }
      setDraft(content);
      setFeedback(requestFeedback(error, "消息未发送，草稿已保留，请重试。"));
    }
  };

  const retry = async () => {
    if (!run) return;
    try {
      setRun(await retryCoachAgentRun(run.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      setFeedback("重试未能开始，请稍后再试。");
    }
  };

  const stop = async () => {
    if (!run) return;
    try {
      setRun(await stopCoachAgentRun(run.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      setFeedback("未能停止生成，请重试。");
    }
  };

  const headerState = capability === "loading"
    ? { state: "neutral", label: "正在读取" }
    : capability === "ready"
      ? { state: "success", label: "可用" }
      : capability === "unavailable"
        ? { state: "error", label: capabilityLabel(capability) }
        : { state: "warning", label: capabilityLabel(capability) };

  const suggestionItems = useMemo(() => {
    const base = ["总结最近进步"];
    if (summaryItem?.display_name) {
      base.push(`关于「${summaryItem.display_name}」的训练建议`);
    }
    base.push("今天练什么");
    return base.slice(0, 3);
  }, [summaryItem?.display_name]);

  const header = (
    <header className={["task6-coach-header", layoutMode === "full" ? "task6-coach-full-header" : ""].filter(Boolean).join(" ")}>
      <div className="task6-coach-header-row">
        <span className="task6-coach-title">Aiming Coach</span>
        <span className="task6-coach-availability" data-state={headerState.state}>{headerState.label}</span>
        <div className="task6-coach-header-actions">
          {onClose ? <IconButton label="关闭 Coach" onClick={onClose} title="关闭 Coach">×</IconButton> : null}
        </div>
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
          <Empty title="开始一段 Coach 对话">可以直接提问训练问题，Coach 会读取你的分析数据。</Empty>
        ) : null}
        {messages.map((message) => (
          <div className="task6-message-entry" data-role={message.role} key={message.id}>
            <article className="task6-message" data-role={message.role}>
              <p>
                {message.role === "assistant"
                  ? <CoachMessageText text={message.content} analysisRef={defaultAnalysisRef} onOpenVideo={onOpenVideo} />
                  : message.content}
              </p>
            </article>
          </div>
        ))}
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
        <div className="task6-composer-input">
          <textarea
            aria-label="向 Coach 提问"
            id="coach-draft"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="向 Coach 提问，可以聊训练，也可以让它帮你操作应用…"
            rows={3}
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
