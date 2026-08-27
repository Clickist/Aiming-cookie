"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createCoachAgentRun,
  getCoachAgentRun,
  getCoachAgentRunStreamUrl,
  getCoachSession,
  getCurrentTraining,
  listSessions,
  retryCoachAgentRun,
  steerCoachAgentRun,
  stopCoachAgentRun,
  truncateCoachSession,
} from "@/lib/api";
import { isDesktopRuntime, openKovaakScenario } from "@/lib/desktop";
import { COACH_PENDING_INTENT_KEY, computeAnalysisEtaSeconds } from "@/lib/contracts";
import {
  COACH_DRAFT_DEBOUNCE_MS,
  activeMentionQuery,
  applyMentionSelection,
  buildMentionCandidates,
  coachDraftStorageKey,
  filterMentionCandidates,
  readCoachDraft,
  removeQueuedChip,
  stepSentHistory,
  truncateQueuePreview,
  writeCoachDraft,
  type CoachDraftScope,
  type MentionCandidate,
  type QueuedChip,
} from "@/lib/composer";
import { CoachMessageText } from "@/components/task7/CoachMessageText";
import { CoachModelMenu } from "./CoachModelMenu";
import { CoachStepList, CoachThinkingBlock, ElapsedTicker, type CoachToolStep } from "./CoachRunActivity";
import type {
  CoachAgentRunEventV1,
  CoachAgentRunV1,
  CoachThreadMessageOut,
  CurrentTrainingItemV1,
  CurrentTrainingV1,
  ProviderProfileState,
  SessionListItem,
} from "@/lib/types";
import { IconChevronDown, IconClose, IconHistory, IconSend } from "@/ui/icons";
import { Button, Empty, ErrorState, IconButton, Notice, Status, Toast, useAnimatedPresence } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";
type CoachLayoutMode = "side-by-side" | "overlay" | "full";

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

/** 步骤形态与呈现组件共享；解析由 deriveToolSteps 完成，本文件不再持有展示逻辑。 */
type ToolStep = CoachToolStep;

/* 工具步骤标签：已知 product command 用中文呈现，未知的回退为合同里的 command_name；
   标签映射是纯前端呈现层，不改任何后端合同。 */
const TOOL_COMMAND_LABELS: Record<string, string> = {
  get_analysis_summary: "读取已附加分析",
  get_coach_knowledge: "查阅训练知识",
  run_product_command: "查询产品数据",
  // 文件系统工具
  read: "读取文件",
  write: "写入文件",
  ls: "浏览文件",
  // 训练记录与分析
  "run.list": "查询训练记录",
  "run.get": "读取训练详情",
  "history.list": "查询历史训练",
  "history.trend": "分析近期趋势",
  "analysis.get": "读取分析结果",
  "analysis.compare": "比较分析结果",
  "analysis.create_from_run": "开始分析",
  "analysis.retry": "重新分析",
  "analysis.delete": "删除分析",
  // 证据与事件
  "analysis.evidence.list": "读取分析证据",
  "analysis.evidence.signal_window": "查看信号片段",
  "analysis.evidence.compare": "比较证据",
  "analysis.run_facts.get": "读取训练事实",
  "analysis.outcomes.timeline": "比较历史表现",
  "analysis.metrics.distribution": "查看指标分布",
  "analysis.events.list": "读取事件记录",
  "analysis.events.get": "读取事件详情",
  "analysis.events.rank": "事件排序",
  "analysis.events.filter": "筛选事件记录",
  "analysis.events.aggregate": "聚合事件",
  "analysis.events.co_occurrence": "事件共现",
  "analysis.events.sequence": "事件序列",
  // 训练计划
  "training_plan.generate_draft": "生成训练计划",
  "training_plan.save": "保存训练计划",
  "training_plan.activate": "启用训练计划",
  "training_plan.pause": "暂停训练计划",
  "training_plan.adjust": "调整训练计划",
  "training_plan.review": "回顾训练计划",
  "training_plan.item.add": "更新训练安排",
  "training_plan.execution.record": "记录训练执行",
  "training_plan.retest.record": "记录复测结果",
  // 画像与成绩
  "profile.aiming.snapshot": "查询瞄准画像",
  "kovaak_scores.lookup": "查询 KovaaK 成绩",
  "kovaak_scores.refresh_connected": "刷新 KovaaK 成绩",
  "eloshapes.query": "查询鼠标尺寸",
  "peripheral_profile.get": "查询外设偏好",
  "peripheral_profile.update": "更新外设偏好",
  "product.readiness.get": "检查产品状态",
  "navigation.open": "打开界面",
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
      const argsPreview = typeof payload.args_preview === "string" ? payload.args_preview : null;
      const resultPreview = typeof payload.result_preview === "string" ? payload.result_preview : null;
      const durationMs =
        typeof payload.duration_ms === "number" && Number.isFinite(payload.duration_ms)
          ? Math.round(payload.duration_ms)
          : null;
      // 开始事件的 created_at 是活动步「经过时间跳动」的基准（分钟:秒）。
      const createdAtMs = Date.parse(event.created_at);
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
        meta: warningMessage ?? (commandName ? null : topic ?? previous?.meta ?? null),
        state: (failed ? "fail" : activityState === "started" ? "active" : "done") as ToolStep["state"],
        command: commandName ?? previous?.command ?? null,
        durationMs: durationMs ?? previous?.durationMs ?? null,
        startedAtMs:
          activityState === "started" && Number.isFinite(createdAtMs)
            ? createdAtMs
            : previous?.startedAtMs ?? null,
        argsPreview: argsPreview ?? previous?.argsPreview ?? null,
        resultPreview: resultPreview ?? previous?.resultPreview ?? null,
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
      command: null,
    });
  }
  return steps;
}

/**
 * Extract the latest `navigation.open` video_time UI event from a run's event
 * list. The Coach-side contract carries the coach_ui_event on the tool event's
 * `payload.ui_event` (or the payload itself when the result is flattened).
 * Returns null when no unhandled video_time target is present.
 */
function videoTimeTargetFromRun(run: CoachAgentRunV1 | null): {
  analysisRef: string;
  timeMs: number;
  eventKey: string;
} | null {
  if (!run) return null;
  for (const event of run.events) {
    const payload = event.payload;
    if (!payload || typeof payload !== "object") continue;
    const candidate = (payload as Record<string, unknown>).ui_event ?? payload;
    if (
      candidate
      && typeof candidate === "object"
      && (candidate as Record<string, unknown>).schema_version === "coach_ui_event.v1"
      && (candidate as Record<string, unknown>).kind === "video_time"
    ) {
      const analysisRef = (candidate as Record<string, unknown>).analysis_ref;
      const timeMs = (candidate as Record<string, unknown>).time_ms;
      if (typeof analysisRef === "string" && typeof timeMs === "number" && Number.isFinite(timeMs)) {
        return {
          analysisRef,
          timeMs,
          eventKey: `${event.event_ref ?? event.sequence}:${analysisRef}:${timeMs}`,
        };
      }
    }
  }
  return null;
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
  const [feedback, setFeedback] = useState<{ text: string; seq: number } | null>(null);
  const feedbackSeqRef = useRef(0);
  // Toast 的关闭是 200ms exit 后的延迟回调：若用户关掉提示后立刻重试
  // 又失败，迟到的旧 onClose 会把新提示清掉。seq 兼作 Toast 的 key
  //（同一条消息连续出现也强制重开）与 onClose 的新鲜度校验
  //（2026-08-22 e2e Toast 二次触发暴露的竞态）。
  const notify = useCallback((message: string) => {
    feedbackSeqRef.current += 1;
    const seq = feedbackSeqRef.current;
    setFeedback({ text: message, seq });
  }, []);
  const [currentTraining, setCurrentTraining] = useState<CurrentTrainingV1 | null>(null);
  const [currentTrainingError, setCurrentTrainingError] = useState(false);
  const [trainingExpanded, setTrainingExpanded] = useState(false);
  const trainingPresence = useAnimatedPresence(trainingExpanded, 180);
  const [launchingScenarioRef, setLaunchingScenarioRef] = useState<string | null>(null);
  const [analysisSessionIds, setAnalysisSessionIds] = useState<number[]>([]);
  const [deepReadAnalysisSessionIds, setDeepReadAnalysisSessionIds] = useState<number[]>([]);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesRef = useRef<HTMLElement | null>(null);
  const stickToBottomRef = useRef(true);
  const seenFeedCountRef = useRef(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const appliedSoftStartRef = useRef<string | null>(null);
  const refreshRevisionRef = useRef(0);
  const trainingRefreshRevisionRef = useRef(0);
  const optimisticMessageIdRef = useRef(-1);
  // send 的同步重入锁：setRun(created) 在两次 await 网络往返之后，窗口期内
  // 第二次 Enter/双击会完整重入并产生重复会话与消息；进入函数即置位，finally 必清。
  const sendingRef = useRef(false);
  // 思考流（SSE partial 帧的 thinking_text）。REST 轮询兜底不携带该字段，
  // 降级路径不显示思考块，行为与旧版一致。
  const [liveThinking, setLiveThinking] = useState<string | null>(null);
  const liveThinkingRef = useRef<string | null>(null);
  // startAt=首个思考帧时刻；frozenMs=首个回答 token 到达时冻结的思考窗口，
  // 回合结束后归档展示「已思考 N 秒」。
  const thinkingTrackerRef = useRef<{ startAt: number | null; frozenMs: number | null }>({
    startAt: null,
    frozenMs: null,
  });
  type ArchivedTurn = { run: CoachAgentRunV1; thinkingText: string | null; thinkingMs: number | null };
  // 成功回合的活动摘要：对话流不再「成功即消失」，工具步骤收敛行保留在
  // 已落库回答之上，直到下一次发送或切换会话。
  const [archivedTurn, setArchivedTurn] = useState<ArchivedTurn | null>(null);

  const clearThinkingStream = useCallback(() => {
    setLiveThinking(null);
    liveThinkingRef.current = null;
    thinkingTrackerRef.current = { startAt: null, frozenMs: null };
  }, []);

  // ── Composer 编排（digests §11 批 5）───────────────────────────────────
  // 运行中发送不再静默也不只弹提示：进可见队列 chips（前端权威态，引擎
  // steer/followUp 无逐条取消动词），每条可视可编辑可删。
  const [queuedChips, setQueuedChips] = useState<QueuedChip[]>([]);
  const queuedChipsRef = useRef<QueuedChip[]>([]);
  queuedChipsRef.current = queuedChips;
  const chipSeqRef = useRef(0);
  // 发送键四动作（steer / queue / interrupt-steer / interrupt）
  const [sendMenuOpen, setSendMenuOpen] = useState(false);
  const sendMenuRef = useRef<HTMLDivElement | null>(null);
  // @ 引用下拉（LibreChat Mention 骨架）
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const mentionCaretRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // 编辑重发（截断派）：记录待编辑消息的展示序号
  const [editingResend, setEditingResend] = useState<{ index: number } | null>(null);
  // ↑ 发送历史（会话内存即可）
  const sentHistoryRef = useRef<string[]>([]);
  const historyIndexRef = useRef<number | null>(null);
  const historyBackupRef = useRef<string | null>(null);
  // send 同步重入之外，还需要随时读取最新 run：409 恢复分支据此分叉
  const activeRunRef = useRef<CoachAgentRunV1 | null>(null);
  useEffect(() => {
    activeRunRef.current = run;
  }, [run]);
  const composerBusy = Boolean(run && ["queued", "running"].includes(run.status));

  // 草稿三级持久（digests §11 item 4）：sessionId | PENDING_CONVO | NEW_CONVO，
  // 多窗格实例按 layoutMode 加 pane 后缀防串；400ms debounce 落 localStorage。
  const draftScope: CoachDraftScope = draftSession
    ? { kind: "new-convo" }
    : sessionId == null
      ? { kind: "pending-convo" }
      : { kind: "session", sessionId };
  const paneSuffix = layoutMode === "full" ? undefined : layoutMode;
  const draftStorageKey = coachDraftStorageKey(draftScope, paneSuffix);
  const draftRestoredRef = useRef(false);
  useEffect(() => {
    // 恢复先于挂载意图消费：本效果声明在 pending-intent 效果之前，React 按
    // 声明顺序执行，意图文案会覆盖已恢复草稿。
    setDraft(readCoachDraft(window.localStorage, draftStorageKey));
    historyIndexRef.current = null;
    historyBackupRef.current = null;
  }, [draftStorageKey]);
  useEffect(() => {
    // 首帧跳过：恢复值本身不需要回写。
    if (!draftRestoredRef.current) {
      draftRestoredRef.current = true;
      return;
    }
    const timer = setTimeout(
      () => writeCoachDraft(window.localStorage, draftStorageKey, draft),
      COACH_DRAFT_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [draft, draftStorageKey]);

  const activeSessionKey = draftSession ? "draft" : `session:${sessionId ?? "primary"}`;
  const activeSessionKeyRef = useRef(activeSessionKey);
  const runBySessionRef = useRef(new Map<string, CoachAgentRunV1>());
  activeSessionKeyRef.current = activeSessionKey;

  // The active run's reads win (streamed live); the session's engaged-analysis
  // list (persisted from completed runs) is the fallback after the run clears.
  // 主题 reads 之后继续回落到深读 refs（取最后一个）：总结/对比类回复深读旧分析
  // 不属于主题挂载，但回复里的「回看 @51.5s」链接仍需可点。此链只服务 @time
  // 链接定位；「本次讨论」挂载条保持只消费主题 refs（discussionAnalysisIds）。
  const topicRunRef = run?.analysis_refs?.length ? run.analysis_refs[0] : null;
  const topicSessionRef = analysisSessionIds.length ? `analysis:${analysisSessionIds[0]}` : null;
  const deepReadRunRef = run?.deep_read_analysis_refs?.length
    ? run.deep_read_analysis_refs[run.deep_read_analysis_refs.length - 1]
    : null;
  const deepReadSessionRef = deepReadAnalysisSessionIds.length
    ? `analysis:${deepReadAnalysisSessionIds[deepReadAnalysisSessionIds.length - 1]}`
    : null;
  const defaultAnalysisRef = topicRunRef ?? topicSessionRef ?? deepReadRunRef ?? deepReadSessionRef;

  const refresh = useCallback(async () => {
    if (capability !== "ready") return;
    if (draftSession || sessionId == null) {
      setMessages([]);
      setAnalysisSessionIds([]);
      setDeepReadAnalysisSessionIds([]);
      setLoadError(false);
      return;
    }
    const revision = ++refreshRevisionRef.current;
    try {
      const detail = await getCoachSession(sessionId);
      if (revision !== refreshRevisionRef.current) return;
      setMessages((current) => {
        const optimistic = current.filter((message) => message.id < 0);
        const backendMessages = detail.messages ?? [];
        const backendKeys = new Set(backendMessages.map((message) => `${message.role}\x00${message.content}`));
        const uniqueOptimistic = optimistic.filter(
          (message) => !backendKeys.has(`${message.role}\x00${message.content}`),
        );
        return [...uniqueOptimistic, ...backendMessages];
      });
      setAnalysisSessionIds(detail.analysis_session_ids ?? []);
      setDeepReadAnalysisSessionIds(detail.deep_read_analysis_session_ids ?? []);
      setLoadError(false);
    } catch {
      if (revision === refreshRevisionRef.current) setLoadError(true);
    }
  }, [capability, draftSession, sessionId]);

  useEffect(() => {
    setRun(runBySessionRef.current.get(activeSessionKey) ?? null);
    setUnreadCount(0);
    stickToBottomRef.current = true;
    clearThinkingStream();
    setArchivedTurn(null);
  }, [activeSessionKey, clearThinkingStream]);

  useEffect(() => {
    if (run) {
      runBySessionRef.current.set(activeSessionKeyRef.current, run);
    } else {
      runBySessionRef.current.delete(activeSessionKeyRef.current);
    }
  }, [run]);

  // Coach's `navigation.open` may emit a video_time UI event; forward it to the
  // video pane exactly once per event so streaming and polling both resolve it.
  const handledVideoEventsRef = useRef(new Set<string>());
  useEffect(() => {
    if (!onOpenVideo) return;
    const target = videoTimeTargetFromRun(run);
    if (!target || handledVideoEventsRef.current.has(target.eventKey)) return;
    handledVideoEventsRef.current.add(target.eventKey);
    onOpenVideo(target.analysisRef, target.timeMs);
  }, [onOpenVideo, run]);

  // Analyses this discussion engaged with: the live run's reads win, the
  // session's persisted list is the fallback after the run clears.
  const discussionAnalysisIds = useMemo(() => {
    const ids = new Set<number>();
    for (const ref of run?.analysis_refs ?? []) {
      const match = /^analysis:([1-9][0-9]*)$/.exec(ref);
      if (match) ids.add(Number(match[1]));
    }
    for (const id of analysisSessionIds) ids.add(id);
    return [...ids];
  }, [analysisSessionIds, run?.analysis_refs]);

  // Backend session id == analysis id in the file-based architecture, so the
  // sessions list can supply the scenario name for the discussion tag. Fetch is
  // best-effort; without a name the tag falls back to the analysis ref.
  type DiscussionAnalysisInfo = { scenario: string | null; runId: number | null };
  const [analysisScenarios, setAnalysisScenarios] = useState<Record<number, DiscussionAnalysisInfo>>({});
  const discussionAnalysisKey = discussionAnalysisIds.join(",");
  useEffect(() => {
    if (!discussionAnalysisKey) {
      setAnalysisScenarios({});
      return;
    }
    let cancelled = false;
    void listSessions()
      .then((response) => {
        if (cancelled) return;
        const map: Record<number, DiscussionAnalysisInfo> = {};
        for (const item of response.sessions) map[item.id] = { scenario: item.scenario ?? null, runId: item.kovaak_run_id ?? null };
        setAnalysisScenarios(map);
      })
      .catch(() => {
        if (!cancelled) setAnalysisScenarios({});
      });
    return () => { cancelled = true; };
  }, [discussionAnalysisKey]);

  // 进行中的分析挂进「本次讨论」条：显示「正在分析：场景名」+ 呼吸点与经过
  // 时间，完成后由自动开讲接管变成可点击的视频 chip。有分析在跑时 3s 轮询，
  // 空闲 10s。同一轮询顺带保存会话快照，供分析步骤的预估耗时计算复用。
  const [pendingAnalyses, setPendingAnalyses] = useState<
    { id: number; scenario: string | null; runId: number | null; startedAtMs: number | null }[]
  >([]);
  const [sessionsSnapshot, setSessionsSnapshot] = useState<SessionListItem[]>([]);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (cancelled) return;
      let fast = false;
      try {
        const response = await listSessions();
        if (cancelled) return;
        setSessionsSnapshot(response.sessions);
        const pending = response.sessions
          .filter((item) => item.status === "queued" || item.status === "running")
          .sort((a, b) => b.id - a.id)
          .map((item) => ({
            id: item.id,
            scenario: item.scenario ?? null,
            runId: item.kovaak_run_id ?? null,
            // started_at 优先（已在跑），否则从入队时间起算。
            startedAtMs: (() => {
              const parsed = Date.parse(item.started_at ?? item.created_at);
              return Number.isFinite(parsed) ? parsed : null;
            })(),
          }));
        setPendingAnalyses(pending);
        fast = pending.length > 0;
      } catch {
        if (!cancelled) setPendingAnalyses([]);
      } finally {
        if (!cancelled) timer = setTimeout(tick, fast ? 3000 : 10000);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // 分析类步骤的预估耗时：本机历史已完成分析的实际执行时长中位数
  // （纯前端启发式，见 computeAnalysisEtaSeconds；无样本时不显示，不编造数字）。
  const ANALYSIS_ETA_COMMANDS = new Set(["analysis.create_from_run", "analysis.retry"]);
  const analysisEtaSeconds = useMemo(
    () => computeAnalysisEtaSeconds(sessionsSnapshot),
    [sessionsSnapshot],
  );

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
      setArchivedTurn(null);
      clearThinkingStream();
      setRun(softStartRun);
      return;
    }
    setRun(null);
    void refresh();
  }, [refresh, softStartRun, clearThinkingStream]);

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

    // 成功终态统一收敛：回合摘要归档（工具步骤收敛行 + 思考秒数），思考流清空，
    // 本地 run 解除以落库消息接管对话。
    const settleSucceeded = (next: CoachAgentRunV1) => {
      const tracker = thinkingTrackerRef.current;
      const frozenMs =
        tracker.frozenMs ?? (tracker.startAt !== null ? Date.now() - tracker.startAt : null);
      setArchivedTurn({ run: next, thinkingText: liveThinkingRef.current, thinkingMs: frozenMs });
      clearThinkingStream();
      setRun(null);
    };

    // 轮询兜底的有限退避：瞬断不杀轮询（1s→2s→4s…上限 10s），连续失败达到
    // 上限才放弃并把本地 run 收敛为明确的中断终态；任何一次成功即清零计数、
    // 恢复正常节奏。
    const POLL_MAX_FAILURES = 8;
    const POLL_BASE_INTERVAL_MS = 1000;
    const POLL_MAX_INTERVAL_MS = 10_000;
    let pollFailures = 0;

    // 404 表示 run 已不存在（如 sidecar 重启）：立即置中断终态，不再无谓重试。
    const isMissingRunError = (error: unknown) =>
      error instanceof Error && error.name === "ApiError_404";

    const finalizeRun = async () => {
      try {
        const next = await fetchRun();
        if (cancelled) return;
        setRun(next);
        await Promise.all([refresh(), refreshCurrentTraining()]);
        if (next.status === "succeeded") settleSucceeded(next);
      } catch (error) {
        if (cancelled) return;
        // 终态确认不能一次失败就丢：run 已消失立即中断收敛，
        // 其余情况回到轮询路径由退避续拍兜底。
        if (isMissingRunError(error)) {
          markInterrupted();
          return;
        }
        pollFailures += 1;
        if (pollFailures >= POLL_MAX_FAILURES) {
          markInterrupted();
          return;
        }
        schedulePoll();
      }
    };

    const schedulePoll = () => {
      if (cancelled) return;
      clearPollTimer();
      const delay = Math.min(POLL_BASE_INTERVAL_MS * 2 ** pollFailures, POLL_MAX_INTERVAL_MS);
      pollTimer = setTimeout(async () => {
        try {
          const next = await fetchRun();
          if (cancelled) return;
          pollFailures = 0;
          setRun(next);
          if (["queued", "running"].includes(next.status)) {
            schedulePoll();
          } else {
            await Promise.all([refresh(), refreshCurrentTraining()]);
            if (next.status === "succeeded") settleSucceeded(next);
          }
        } catch (error) {
          if (cancelled) return;
          if (isMissingRunError(error)) {
            markInterrupted();
            return;
          }
          pollFailures += 1;
          if (pollFailures >= POLL_MAX_FAILURES) {
            markInterrupted();
            return;
          }
          schedulePoll();
        }
      }, delay);
    };

    const closeStream = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };

    // 断线收敛：本地合成明确的失败终态，解除 composer 锁并给出可见提示；
    // run_ref 保留，用户可直接点「重试」。
    const markInterrupted = () => {
      if (cancelled || finished) return;
      finished = true;
      clearPollTimer();
      closeStream();
      streamActive = false;
      clearThinkingStream();
      setRun((prev) => prev
        ? {
          ...prev,
          status: "failed",
          error: {
            domain: "network",
            code: "run_interrupted",
            message: "回复已中断：与本地服务的连接断开。",
            retryable: true,
          },
        }
        : prev);
      notify("回复已中断");
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
          // partial 帧同时携带 text 与 thinking_text（思考帧的 text 为 null 或
          // 重发的最新正文）。思考帧进入折叠块；首个非空回答 token 冻结
          // 思考窗口时长供归档展示。
          const data = JSON.parse(event.data) as { text?: unknown; thinking_text?: unknown };
          const thinking = typeof data.thinking_text === "string" ? data.thinking_text : "";
          if (thinking.trim()) {
            if (thinkingTrackerRef.current.startAt === null) {
              thinkingTrackerRef.current.startAt = Date.now();
            }
            liveThinkingRef.current = thinking;
            setLiveThinking(thinking);
          }
          if (typeof data.text === "string") {
            const text = data.text;
            if (text.length > 0 && thinkingTrackerRef.current.startAt !== null && thinkingTrackerRef.current.frozenMs === null) {
              thinkingTrackerRef.current.frozenMs = Date.now() - thinkingTrackerRef.current.startAt;
            }
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
  }, [liveRunRef, refresh, refreshCurrentTraining, sessionId, clearThinkingStream]);

  const toolSteps = useMemo(
    () =>
      deriveToolSteps(run).map((step) =>
        step.command && ANALYSIS_ETA_COMMANDS.has(step.command)
          ? { ...step, etaSeconds: analysisEtaSeconds }
          : step,
      ),
    [run, analysisEtaSeconds],
  );
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

  // 头部瘦身（digests §8）：折叠态训练卡并入 header 行成单 chip；
  // 展开细节沿用 .task6-training-reveal 的动画合同（useAnimatedPresence + inert），
  // 读取失败/不可用也收敛进 chip 的展开区，不再三层常驻压顶。
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
      notify("该训练项目暂时没有可验证的 KovaaK 场景");
      return;
    }
    setLaunchingScenarioRef(scenarioProfileRef);
    try {
      const result = await openKovaakScenario(scenarioProfileRef);
      notify(result.message);
    } catch {
      notify("未能请求打开 KovaaK，请稍后重试");
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

  // 头部瘦身（digests §8）：折叠态训练卡并入 header 行成单 chip；
  // 展开细节沿用 .task6-training-reveal 的动画合同（useAnimatedPresence + inert），
  // 读取失败/不可用也收敛进 chip 的展开区，不再三层常驻压顶。
  const trainingUnavailable =
    currentTraining?.availability === "unavailable" && currentTraining.reason !== "no_current_plan";

  const trainingChipLabel =
    currentTrainingError && !currentTraining
      ? "训练计划暂不可读"
      : trainingUnavailable
        ? "当前训练暂不可用"
        : noCurrentPlan
          ? "还没有当前训练安排"
          : summaryItem
            ? summaryItem.display_name ?? "未命名项目"
            : null;
  const trainingExpandable = Boolean(summaryItem) || (currentTrainingError && !currentTraining) || trainingUnavailable;

  const trainingChip = trainingChipLabel === null ? null : (
    <button
      aria-expanded={trainingExpandable ? trainingExpanded : undefined}
      aria-label="当前训练计划"
      className="task6-training-chip"
      onClick={() => setTrainingExpanded((expanded) => !expanded)}
      title={trainingChipLabel}
      type="button"
    >
      <span className="task6-training-chip-label">{trainingChipLabel}</span>
      {trainingExpandable ? <IconChevronDown className="task6-training-chip-caret" /> : null}
    </button>
  );

  const trainingReveal = (
    <div
      aria-hidden={!trainingExpanded || undefined}
      className="task6-training-reveal"
      data-state={trainingPresence.state}
      inert={!trainingExpanded || undefined}
    >
      <div className="task6-training-reveal-inner">
        {currentTrainingError && !currentTraining ? <ErrorState title="当前训练暂时无法读取" /> : null}
        {trainingUnavailable ? <Notice tone="warning" title="当前训练暂不可用">本地训练摘要暂时无法读取，稍后再试。</Notice> : null}
        {summaryItem ? (
          <section aria-label="当前训练计划" className="task6-training-details">
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
          </section>
        ) : null}
      </div>
    </div>
  );

  // ── 发送/队列编排（digests §11 批 5）───────────────────────────────────

  const pushSentHistory = useCallback((text: string) => {
    sentHistoryRef.current = [...sentHistoryRef.current, text].slice(-50);
    historyIndexRef.current = null;
    historyBackupRef.current = null;
  }, []);

  /** 队列 chips 入列：前端权威态，逐条可视可编辑可删（丢消息教训）。 */
  const enqueueQueuedItem = useCallback((text: string) => {
    const content = text.trim();
    if (!content) return;
    chipSeqRef.current += 1;
    const id = chipSeqRef.current;
    setQueuedChips((chips) => [...chips, { id, text: content }]);
  }, []);

  const appendOptimisticUserMessage = useCallback((content: string) => {
    const optimisticMessageId = optimisticMessageIdRef.current--;
    stickToBottomRef.current = true;
    setMessages((current) => [...current, {
      id: optimisticMessageId,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      legacy_session_id: null,
    }]);
    return optimisticMessageId;
  }, []);

  /**
   * 实际发送，成功受理返回 true。
   * 运行中且非 force 一律转可见队列 chips——正式编排取代批 1 的 notify 止血，
   * 击键绝不凭空消失。force 仅供「打断并转向」等显式编排动作使用。
   */
  const sendText = async (
    contentRaw: string,
    opts: { force?: boolean; editingResend?: { index: number } } = {},
  ): Promise<boolean> => {
    const content = contentRaw.trim();
    if (!content || sendingRef.current) return false;
    const active = activeRunRef.current;
    if (!opts.force && active && ["queued", "running"].includes(active.status)) {
      // 已受理即清空输入：文本已移入 chip（不是复制），需要改写时走回填编辑。
      enqueueQueuedItem(content);
      setDraft("");
      notify("当前回复仍在生成中，这条已加入输入框上方队列，可随时取消、编辑或立即转向。");
      return true;
    }
    // 同步重入锁：必须在任何 await 之前置位，重入直接丢弃。
    sendingRef.current = true;
    // 新回合开始：清掉上一回合的归档摘要与思考流残留。
    setArchivedTurn(null);
    clearThinkingStream();
    const editing = opts.editingResend;
    let truncatedDone = false;
    let optimisticId: number | null = null;
    try {
      // 编辑重发＝截断派：先把会话截到该消息之前再发送（该消息之后的历史不参与上下文）。
      if (editing) {
        if (sessionId == null) {
          throw Object.assign(new Error("编辑重发需要已保存的会话"), { name: "ComposerEditNeedsSession" });
        }
        await truncateCoachSession(sessionId, editing.index);
        truncatedDone = true;
        setMessages((current) => current.slice(0, editing.index));
        window.dispatchEvent(new CustomEvent("aiming-cookie:coach-session-updated"));
      }
      const effectiveSessionId = sessionId ?? (onEnsureSession ? await onEnsureSession() : null);
      if (sessionId === null && onEnsureSession && effectiveSessionId === null) {
        notify("未能创建会话，草稿已保留，请重试。");
        return false;
      }
      optimisticId = appendOptimisticUserMessage(content);
      setDraft("");
      stickToBottomRef.current = true;
      pushSentHistory(content);
      const created = await createCoachAgentRun(
        content,
        effectiveSessionId == null ? {} : { sessionId: effectiveSessionId },
      );
      setRun(created);
      if (editing) setEditingResend(null);
      // 会话标题会随第一条消息更新，通知 AppShell 刷新侧栏列表。
      window.dispatchEvent(new CustomEvent("aiming-cookie:coach-session-updated"));
      return true;
    } catch (error) {
      if (optimisticId !== null) {
        setMessages((current) => current.filter((message) => message.id !== optimisticId));
      }
      // 仅当等待期间用户没有重新输入时才回填，避免覆盖新草稿。
      setDraft((current) => (current.trim() ? current : content));
      if (truncatedDone) {
        // 截断已在服务端生效但发送失败：退出编辑态防止再次按旧序号重复截断。
        setEditingResend(null);
        notify(requestFeedback(error, "消息未发送；截断已生效，请直接重新发送当前内容。"));
      } else {
        notify(requestFeedback(error, "消息未发送，草稿已保留，请重试。"));
      }
      return false;
    } finally {
      sendingRef.current = false;
    }
  };

  const submitComposer = () => {
    const content = draft.trim();
    if (!content) return;
    setMentionQuery(null);
    setSendMenuOpen(false);
    const editing = editingResend;
    void sendText(content, editing ? { editingResend: editing } : undefined);
  };

  /**
   * 409 run_not_steerable ＝ 可恢复的正常态（本轮刚结束/亚毫秒注册窗）：
   * 立即转不进去了就转回可见队列等自动发送；404（run 已不存在）同理。
   */
  const isRecoverableSteerReject = (error: unknown): boolean => {
    const name = error instanceof Error ? error.name : "";
    return name === "ApiError_409" || name === "ApiError_404";
  };

  const activeRunIsBusy = () =>
    Boolean(activeRunRef.current && ["queued", "running"].includes(activeRunRef.current!.status));

  /** 四动作之 steer：把当前草稿立即注入运行中的回合。 */
  const steerWithDraft = async () => {
    const content = draft.trim();
    if (!content) return;
    setSendMenuOpen(false);
    const active = activeRunRef.current;
    if (!active || !["queued", "running"].includes(active.status)) {
      void sendText(content);
      return;
    }
    try {
      await steerCoachAgentRun(active.run_ref, content);
      pushSentHistory(content);
      setDraft("");
      appendOptimisticUserMessage(content);
    } catch (error) {
      if (isRecoverableSteerReject(error)) {
        if (activeRunIsBusy()) {
          enqueueQueuedItem(content);
          setDraft("");
          notify("引擎这一窗口没能接收转向，先排入队列，本轮结束后自动发送。");
        } else {
          void sendText(content);
        }
      } else {
        notify(requestFeedback(error, "未能立即转向，草稿已保留，请重试。"));
      }
    }
  };

  /** 队列 chip 上浮：能转则立即 steer 并展示乐观气泡，不能转走可恢复分支。 */
  const promoteChipToSteer = async (chip: QueuedChip) => {
    const acceptAndRemove = () => setQueuedChips((chips) => removeQueuedChip(chips, chip.id));
    const active = activeRunRef.current;
    if (!active || !["queued", "running"].includes(active.status)) {
      if (await sendText(chip.text)) acceptAndRemove();
      return;
    }
    try {
      await steerCoachAgentRun(active.run_ref, chip.text);
      pushSentHistory(chip.text);
      appendOptimisticUserMessage(chip.text);
      acceptAndRemove();
    } catch (error) {
      if (!isRecoverableSteerReject(error)) {
        notify(requestFeedback(error, "未能立即插入，内容仍保留在队列中。"));
        return;
      }
      // 本轮已经收不了转向：不再忙就当普通发送放行，仍忙则 chip 留守等自动发送。
      if (activeRunIsBusy()) {
        notify("引擎这一窗口没能接收转向，chip 保留在队列里，本轮结束后自动发送。");
      } else if (await sendText(chip.text)) {
        acceptAndRemove();
      }
    }
  };

  const backfillChipToDraft = (chip: QueuedChip) => {
    setQueuedChips((chips) => removeQueuedChip(chips, chip.id));
    setDraft(chip.text);
    setMentionQuery(null);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
    });
  };

  /** 四动作之 interrupt-steer：停止当前生成并以此草稿开启新回复。 */
  const interruptAndSteer = async () => {
    const content = draft.trim();
    if (!content) return;
    setSendMenuOpen(false);
    const active = activeRunRef.current;
    if (!active) {
      void sendText(content);
      return;
    }
    try {
      setRun(await stopCoachAgentRun(active.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      notify("未能停止生成，请重试。");
      return;
    }
    // stop 已在服务端收敛终态：绕过运行守卫立即开始新回复。
    void sendText(content, { force: true });
  };

  // 成功终态自动放行队首 chip：让排队语义在多轮长对话中自然流转。
  const prevStatusRef = useRef<CoachAgentRunV1["status"] | null>(null);
  useEffect(() => {
    const status = run?.status ?? null;
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (!prev || !["queued", "running"].includes(prev) || status !== "succeeded") return;
    const first = queuedChipsRef.current[0];
    if (!first) return;
    void sendText(first.text).then((accepted) => {
      if (accepted) setQueuedChips((chips) => removeQueuedChip(chips, first.id));
    });
  }, [run]);

  // ── ↑ 发送历史（item 5，内存 sent array，会话内有效）───────────────────
  const navigateSentHistory = useCallback(
    (direction: "up" | "down") => {
      const history = sentHistoryRef.current;
      if (history.length === 0) return;
      if (direction === "up" && historyIndexRef.current === null) {
        historyBackupRef.current = draft;
      }
      const next = stepSentHistory(history.length, historyIndexRef.current, direction);
      historyIndexRef.current = next;
      setDraft(next === null ? (historyBackupRef.current ?? "") : history[next] ?? "");
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        el?.setSelectionRange(el.value.length, el.value.length);
      });
    },
    [draft],
  );

  // ── @ 引用下拉（item 3）：候选与查询状态 ───────────────────────────────
  const syncMentionQuery = useCallback(() => {
    const el = textareaRef.current;
    if (!el) {
      setMentionQuery(null);
      return;
    }
    setMentionQuery(activeMentionQuery(el.value, el.selectionStart ?? el.value.length));
    setMentionIndex(0);
  }, []);

  const mentionCandidates = useMemo(
    () =>
      buildMentionCandidates({
        analysisIds: [
          ...new Set([...discussionAnalysisIds, ...pendingAnalyses.map((analysis) => analysis.id)]),
        ],
        scenarioByAnalysisId: Object.fromEntries(
          Object.entries(analysisScenarios).map(([id, info]) => [Number(id), info.scenario]),
        ),
        scenarioNames: [
          ...(currentTraining?.items.map((item) => item.display_name) ?? []),
          ...sessionsSnapshot.map((session) => session.scenario),
        ],
      }),
    [discussionAnalysisIds, pendingAnalyses, analysisScenarios, currentTraining, sessionsSnapshot],
  );
  const filteredMentionCandidates = useMemo(
    () => filterMentionCandidates(mentionCandidates, mentionQuery ?? ""),
    [mentionCandidates, mentionQuery],
  );
  const mentionOpen = mentionQuery !== null && filteredMentionCandidates.length > 0;

  const selectMentionCandidate = (candidate: MentionCandidate) => {
    const el = textareaRef.current;
    const caret = mentionCaretRef.current ?? el?.selectionStart ?? draft.length;
    const next = applyMentionSelection(draft, caret, candidate.token);
    setDraft(next.text);
    setMentionQuery(null);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(next.caret, next.caret);
    });
  };

  /** 编辑重发：把原消息放回输入框并记录截断点（展示序号）。 */
  const startEditResend = (index: number, content: string) => {
    setEditingResend({ index });
    setDraft(content);
    setMentionQuery(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      el?.focus();
      el?.setSelectionRange(el.value.length, el.value.length);
    });
  };


  const retry = async () => {
    if (!run) return;
    try {
      setRun(await retryCoachAgentRun(run.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      notify("重试未能开始，请稍后再试。");
    }
  };

  const stop = async () => {
    if (!run) return;
    try {
      setRun(await stopCoachAgentRun(run.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      notify("未能停止生成，请重试。");
    }
  };

  // 运行中发送键下拉：外点关闭、Esc 关闭、↑↓ 在动作间移动焦点（IME 守卫）。
  useEffect(() => {
    if (!sendMenuOpen) return undefined;
    const menuButtons = () =>
      sendMenuRef.current
        ? [...sendMenuRef.current.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")]
        : [];
    const onPointerDown = (event: MouseEvent) => {
      if (sendMenuRef.current && !sendMenuRef.current.contains(event.target as Node)) {
        setSendMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // IME 守卫与 textarea 同款：isComposing + keyCode 229。
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") {
        setSendMenuOpen(false);
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const buttons = menuButtons();
        if (buttons.length === 0) return;
        event.preventDefault();
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const delta = event.key === "ArrowDown" ? 1 : -1;
        const next = current < 0
          ? (event.key === "ArrowDown" ? 0 : buttons.length - 1)
          : (current + delta + buttons.length) % buttons.length;
        buttons[next]?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [sendMenuOpen]);

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
          {trainingChip}
          {onClose ? <IconButton label="关闭 Coach" onClick={onClose} title="关闭 Coach"><IconClose /></IconButton> : null}
        </div>
      </div>
      {trainingChip ? trainingReveal : null}
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
        <div className="task6-coach-state">
          <ErrorState title="Coach 暂时不可用"><Button onClick={() => void refresh()} variant="secondary">重试</Button></ErrorState>
        </div>
      </div>
    );
  }

  return (
    <div className="task6-coach-panel">
      {header}

      {/* 本次讨论的分析挂载条：只在有进行中的分析（pending）时出现；
          完成后条收起，入口回落到消息内 @time 链接与 History。 */}
      {pendingAnalyses.length > 0 ? (
        <div aria-label="本次讨论的分析" className="task6-discussion-bar task6-suggestions" role="region">
          <span>本次讨论</span>
          {pendingAnalyses.map((item) => (
            <span
              className="task6-suggestion"
              data-pending="true"
              key={`pending-${item.id}`}
              title="分析完成后可点击打开视频"
            >
              <span aria-hidden="true" className="task6-pulse-dot task6-chip-dot" />
              {item.scenario ?? `分析 #${item.id}`}{item.runId != null ? ` · run ${item.runId}` : ""}
              <ElapsedTicker sinceMs={item.startedAtMs} />
            </span>
          ))}
          {discussionAnalysisIds.map((id) => (
            <button
              className="task6-suggestion"
              key={id}
              onClick={() => onOpenVideo?.(`analysis:${id}`, 0)}
              title="打开视频讲解"
              type="button"
            >
              {(analysisScenarios[id]?.scenario ?? `分析 #${id}`)}{analysisScenarios[id]?.runId != null ? ` · run ${analysisScenarios[id]?.runId}` : ""}
            </button>
          ))}
        </div>
      ) : null}

      <div className="task6-messages-wrap">
      <section aria-label="Coach 消息" className="task6-messages" onScroll={handleMessagesScroll} ref={messagesRef}>
        {/* 底部锚定（digests §8 病灶①）：非空会话 spacer 吸收剩余空间把消息压向
            钉底 composer；空会话时同槽位换成占满剩余空间的 hero 空态。 */}
        {messages.length === 0 && !run ? (
          <div className="task6-empty-hero">
            <Empty title="开始一段 Coach 对话">可以直接提问训练问题，Coach 会读取你的分析数据。</Empty>
          </div>
        ) : (
          <div aria-hidden="true" className="task6-msg-spacer" />
        )}
        {messages.map((message, index) => (
          <div className="task6-message-entry" data-role={message.role} key={message.id}>
            <article className="task6-message" data-role={message.role}>
              {message.role === "assistant" ? (
                /* 受控富渲染（digests §10）：助手消息走 task7-rich 块结构，
                   不再套 <p>（表格/列表不能内嵌在段落里）。 */
                <CoachMessageText text={message.content} analysisRef={defaultAnalysisRef} onOpenVideo={onOpenVideo} />
              ) : (
                <p>{message.content}</p>
              )}
            </article>
            {/* 编辑重发（截断派，item 7）：仅空闲且已落库消息提供入口 */}
            {message.role === "user" && message.id > 0 && !composerBusy ? (
              <IconButton
                className="task6-message-edit"
                label="编辑重发"
                onClick={() => startEditResend(index, message.content)}
                size="compact"
                title="编辑这条消息并重发；其后的历史将不参与本次上下文"
              >
                <IconHistory />
              </IconButton>
            ) : null}
          </div>
        ))}
        {run?.partial_text ? (
          <article className="task6-message" data-role="assistant">
            {/* 流式期间与最终答案同一渲染路径：@time 链接实时可点，
                消除完成后裸文本→格式化的跳变；光标经 tail 插在续写位。 */}
            <CoachMessageText
              text={run.partial_text}
              analysisRef={defaultAnalysisRef}
              onOpenVideo={onOpenVideo}
              tail={
                run && ["queued", "running"].includes(run.status) ? <span className="task6-streaming-cursor" /> : null
              }
            />
          </article>
        ) : null}
        {run && ["queued", "running", "failed", "stopped"].includes(run.status) ? (
          <>
            <CoachThinkingBlock
              streaming={liveThinking !== null && ["queued", "running"].includes(run.status)}
              text={liveThinking}
              startedAtMs={thinkingTrackerRef.current.startAt}
            />
            <CoachStepList steps={toolSteps} stopped={run.status === "stopped"} />
          </>
        ) : null}
        {!run && archivedTurn ? (
          <>
            {archivedTurn.thinkingText !== null || archivedTurn.thinkingMs != null ? (
              <CoachThinkingBlock
                frozenSeconds={archivedTurn.thinkingMs}
                streaming={false}
                text={archivedTurn.thinkingText}
              />
            ) : null}
            <CoachStepList steps={deriveToolSteps(archivedTurn.run)} />
          </>
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
        {/* 编辑重发横幅（item 7）：明确的截断语义提示 */}
        {editingResend ? (
          <div className="task6-editing-banner" role="status">
            <span>
              正在编辑第 {editingResend.index + 1} 条消息 · 发送后其后的历史将不参与本次上下文
            </span>
            <Button onClick={() => setEditingResend(null)} size="compact" variant="ghost">取消编辑</Button>
          </div>
        ) : null}
        {/* 运行中队列 chips（item 1）：96 字符预览，逐条可上浮转向/回填编辑/取消 */}
        {queuedChips.length > 0 ? (
          <div aria-label="待发送队列" className="task6-queue-chips" role="list">
            {queuedChips.map((chip) => (
              <div className="task6-queue-chip" key={chip.id} role="listitem">
                <span className="task6-queue-chip-text" title={chip.text}>{truncateQueuePreview(chip.text)}</span>
                <IconButton label="上浮立即转向" onClick={() => void promoteChipToSteer(chip)} size="compact" title="立即插入当前回复">
                  <IconChevronDown className="task6-icon-flip" />
                </IconButton>
                <IconButton label="回填编辑" onClick={() => backfillChipToDraft(chip)} size="compact" title="放回输入框编辑">
                  <IconHistory />
                </IconButton>
                <IconButton label="取消发送" onClick={() => setQueuedChips((chips) => removeQueuedChip(chips, chip.id))} size="compact" title="取消这条排队消息">
                  <IconClose />
                </IconButton>
              </div>
            ))}
          </div>
        ) : null}
        <div className="task6-composer-input">
          <textarea
            aria-label="向 Coach 提问"
            id="coach-draft"
            onChange={(event) => {
              mentionCaretRef.current = event.target.selectionStart;
              setDraft(event.target.value);
              syncMentionQuery();
            }}
            onKeyDown={(event) => {
              // 中文等输入法按 Enter 确认候选词时 isComposing 为 true，不应提交。
              if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
              // @ 引用下拉（item 3）：↑↓ 导航 / Enter 选中 / Esc 关闭
              if (mentionOpen) {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  setMentionIndex((current) => (current + 1) % filteredMentionCandidates.length);
                  return;
                }
                if (event.key === "ArrowUp") {
                  event.preventDefault();
                  setMentionIndex((current) => (current - 1 + filteredMentionCandidates.length) % filteredMentionCandidates.length);
                  return;
                }
                if (event.key === "Enter") {
                  event.preventDefault();
                  selectMentionCandidate(filteredMentionCandidates[Math.min(mentionIndex, filteredMentionCandidates.length - 1)]!);
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  setMentionQuery(null);
                  return;
                }
              }
              // ↑ 翻发送历史（item 5）：空草稿或正在翻页时接管
              if (!event.shiftKey && event.key === "ArrowUp" && (draft.trim() === "" || historyIndexRef.current !== null)) {
                event.preventDefault();
                navigateSentHistory("up");
                return;
              }
              if (!event.shiftKey && event.key === "ArrowDown" && historyIndexRef.current !== null) {
                event.preventDefault();
                navigateSentHistory("down");
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submitComposer();
              }
            }}
            placeholder="向 Coach 提问，可以聊训练，也可以让它帮你操作应用…"
            ref={textareaRef}
            rows={3}
            value={draft}
          />
          {/* @ 引用下拉浮层（item 3） */}
          {mentionOpen ? (
            <div
              aria-label="@ 引用候选"
              className="task6-mention-menu"
              role="listbox"
            >
              {filteredMentionCandidates.slice(0, 8).map((candidate, index) => (
                <button
                  aria-selected={index === mentionIndex}
                  className="task6-mention-item"
                  key={`${candidate.token}-${index}`}
                  onClick={() => selectMentionCandidate(candidate)}
                  onMouseDown={(event) => event.preventDefault()}
                  role="option"
                  type="button"
                >
                  <strong>{candidate.token}</strong>
                  <small>{candidate.label}</small>
                </button>
              ))}
            </div>
          ) : null}
          {composerBusy ? (
            /* 运行中发送键四动作（item 2）：steer / queue / interrupt-steer / interrupt */
            <div className="task6-send-actions" ref={sendMenuRef}>
              <button
                aria-expanded={sendMenuOpen}
                aria-haspopup="menu"
                aria-label="运行中发送选项"
                className="task6-composer-send"
                data-open={sendMenuOpen || undefined}
                onClick={() => setSendMenuOpen((open) => !open)}
                title="发送选项：转向 / 排队 / 打断"
                type="button"
              >
                <IconSend />
              </button>
              {sendMenuOpen ? (
                <div aria-label="运行中发送选项" className="task6-send-menu" role="menu">
                  <button disabled={!draft.trim()} onClick={() => void steerWithDraft()} role="menuitem" type="button">
                    立即转向<small>不打断当前回复，直接注入本回合</small>
                  </button>
                  <button disabled={!draft.trim()} onClick={() => { setSendMenuOpen(false); enqueueQueuedItem(draft); setDraft(""); }} role="menuitem" type="button">
                    加入队列<small>本轮结束后按顺序自动发送，可随时取消</small>
                  </button>
                  <button disabled={!draft.trim()} onClick={() => void interruptAndSteer()} role="menuitem" type="button">
                    打断并转向<small>停止当前生成并以此内容开始新回复</small>
                  </button>
                  <div className="task6-send-menu-separator" role="separator" />
                  <button onClick={() => void stop()} role="menuitem" type="button">
                    停止生成<small>结束本轮回复</small>
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <button aria-label="发送" className="task6-composer-send" disabled={!draft.trim()} onClick={submitComposer} type="button"><IconSend /></button>
          )}
        </div>
        {/* 工具行拆出（digests §8）：模型菜单不再与 textarea 同行抢占宽度，
            textarea 只为发送钮保留右侧空间。模型选择器运行中保持可用（item 6，
            选择对下一段回复生效），不随运行态连坐 disabled。 */}
        <div className="task6-composer-tools">
          <CoachModelMenu
            onError={(message) => notify(message)}
          />
        </div>
      </footer>

      {feedback ? (
        <Toast key={feedback.seq} onClose={() => setFeedback((current) => (current && current.seq === feedback.seq ? null : current))}>
          {feedback.text}
        </Toast>
      ) : null}
    </div>
  );
}
