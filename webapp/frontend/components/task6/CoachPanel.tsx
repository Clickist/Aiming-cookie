"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";

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
  readCoachDraftEnvelope,
  removeQueuedChip,
  stepSentHistory,
  truncateQueuePreview,
  writeCoachDraftEnvelope,
  type CoachDraftScope,
  type MentionCandidate,
  type QueuedChip,
} from "@/lib/composer";
import {
  QUOTE_HEADER,
  QUOTE_MAX_CHARS,
  SEND_BUDGET_CHARS,
  composeQuotedContent,
  evaluateAssistantSelection,
  isWithinSendBudget,
  messageArticleFromNode,
  parseQuotedContent,
  selectionAnchorRect,
  snapshotQuote,
  type CoachQuote,
} from "@/lib/quote";
import { CoachMessageText } from "@/components/task7/CoachMessageText";
import { CoachModelMenu } from "./CoachModelMenu";
import { CoachWorkStream, ElapsedTicker, type CoachToolStep, type CoachWorkSegment } from "./CoachRunActivity";
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

/** 划选浮层「引用」条的估算半宽（px），用于把浮层横向钳制在面板宽度内。 */
const SELECTION_TOOLBAR_HALF_WIDTH = 84;

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

/** 从单个 tool activity 事件装配步骤（SSE 实时与 events 重建共用）。 */
function stepFromToolEvent(event: CoachAgentRunEventV1, previous: CoachToolStep | null): CoachToolStep {
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
  return {
    key: toolCallId ?? event.event_ref ?? `tool-${event.sequence}`,
    label: commandName
      ? TOOL_COMMAND_LABELS[commandName] ?? commandName
      : previous?.label ?? (toolName
        ? TOOL_COMMAND_LABELS[toolName] ?? toolName
        : topic ? "查阅训练知识" : event.message),
    meta: warningMessage ?? (commandName ? null : topic ?? previous?.meta ?? null),
    state: (failed ? "fail" : activityState === "started" ? "active" : "done") as CoachToolStep["state"],
    command: commandName ?? previous?.command ?? null,
    durationMs: durationMs ?? previous?.durationMs ?? null,
    startedAtMs:
      activityState === "started" && Number.isFinite(createdAtMs)
        ? createdAtMs
        : previous?.startedAtMs ?? null,
    argsPreview: argsPreview ?? previous?.argsPreview ?? null,
    resultPreview: resultPreview ?? previous?.resultPreview ?? null,
  };
}

/** 冻结一个思考段：终文落定、流式终止、时长按冻结时刻与段起点差补算。 */
function freezeThinkingSegment(
  segment: Extract<CoachWorkSegment, { kind: "thinking" }>,
  text: string | null,
  frozenAtMs: number | null,
): Extract<CoachWorkSegment, { kind: "thinking" }> {
  return {
    ...segment,
    text: text ?? segment.text,
    streaming: false,
    frozenMs:
      segment.frozenMs
      ?? (segment.startedAtMs != null && frozenAtMs != null && frozenAtMs > segment.startedAtMs
        ? frozenAtMs - segment.startedAtMs
        : null),
  };
}

/**
 * 从 run 事件序列重建交错工作流段（轮询兜底与刷新恢复路径；SSE 实时路径
 * 走 liveSegments 增量）。thinking_started 事件＝上一思考段边界：其 payload
 * 的 thinking_text 是上一段终文（sidecar 每轮冻结下发），据此补齐丢帧。
 */
function deriveWorkSegments(run: CoachAgentRunV1 | null): CoachWorkSegment[] {
  if (!run) return [];
  const segments: CoachWorkSegment[] = [];
  const stepIndex = new Map<string, number>();
  let thinkingCount = 0;
  const freezeLastThinking = (text: string | null, frozenAtMs: number | null) => {
    for (let i = segments.length - 1; i >= 0; i -= 1) {
      const segment = segments[i];
      if (segment.kind !== "thinking") continue;
      if (segment.streaming) {
        segments[i] = freezeThinkingSegment(segment, text, frozenAtMs);
      } else if (text) {
        // 已冻结的段也接受终文补写（partial 只写到过中间态）。
        segments[i] = { ...segment, text };
      }
      return;
    }
  };
  for (const event of run.events) {
    const payload = event.payload ?? {};
    if (event.type === "phase" && event.code === "thinking_started") {
      // 旧协议（字段缺失）：events 无分段终文，不按轮开段（无内容可填）。
      if (payload.thinking_text === undefined) continue;
      const settledText = typeof payload.thinking_text === "string" ? payload.thinking_text : null;
      freezeLastThinking(settledText, Date.parse(event.created_at) || null);
      thinkingCount += 1;
      const startedAtMs = Date.parse(event.created_at);
      segments.push({
        kind: "thinking",
        key: event.event_ref ?? `think-${event.sequence}-${thinkingCount}`,
        text: "",
        streaming: true,
        startedAtMs: Number.isFinite(startedAtMs) ? startedAtMs : null,
        frozenMs: null,
      });
      continue;
    }
    if (event.type === "tool") {
      const key = typeof payload.tool_call_id === "string" ? payload.tool_call_id : event.event_ref ?? `tool-${event.sequence}`;
      const previousIndex = stepIndex.get(key);
      const previous = previousIndex != null ? segments[previousIndex] : undefined;
      const step = stepFromToolEvent(event, previous?.kind === "tool" ? previous.step : null);
      freezeLastThinking(null, Date.parse(event.created_at) || null);
      if (previousIndex != null && previous?.kind === "tool") {
        segments[previousIndex] = { kind: "tool", step };
      } else {
        stepIndex.set(step.key, segments.length);
        segments.push({ kind: "tool", step });
      }
      continue;
    }
    if (event.type === "text") {
      // 首个正文增量＝思考段终结（首个回答 token 冻结思考窗口的既有语义）。
      freezeLastThinking(null, Date.parse(event.created_at) || null);
    }
  }
  if ((run.status === "queued" || run.status === "running") && !segments.some((segment) => segment.kind === "tool" && segment.step.state === "active")) {
    // 0828 拍板：思考期/正文期的占位步骤（"正在理解问题和分析上下文"等）
    // 是废话——思考行与流式正文本身就是状态；仅排队且无任何可见活动时保留。
    if (run.status === "queued") {
      segments.push({
        kind: "tool",
        step: {
          key: "coach-active",
          label: "等待开始",
          meta: null,
          state: "active",
          command: null,
        },
      });
    }
  }
  return segments;
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

/**
 * 已发送用户消息的引用回显（调研 §3.5）：拼装串里的 `[引用 Coach]` 段渲染
 * 为引用块视觉，正文正常呈现；未命中固定开头形状的 legacy 消息按原文纯文本
 * 渲染。Codex 客户端「发送后看不到选了什么」是长期 open bug——这里必须做。
 */
function UserMessageBody({ content }: { content: string }): ReactNode {
  const parsed = parseQuotedContent(content);
  return (
    <>
      {parsed.quotes.map((text, index) => (
        <blockquote className="task6-quote-block" key={index}>
          <span className="task6-quote-head">{QUOTE_HEADER}</span>
          <p className="task6-quote-body">{text}</p>
        </blockquote>
      ))}
      <p>{parsed.text}</p>
    </>
  );
}

/** 已完成回合的归档：交错的思考段/工具段时序（0828 起思考按 provider 轮
   分段，与工具步骤按发生顺序共存；run 原始事件不保留，段在归档时一次性
   冻结）。持久化到 localStorage——跨页面刷新/应用重启仍能恢复（桌面单机
   场景，无跨机诉求；配额超限等失败静默降级为不恢复）。 */
type ArchivedTurn = { segments: CoachWorkSegment[] };
const ARCHIVED_TURNS_KEY = "aiming-cookie.coach-archived-turns";
const ARCHIVED_TURNS_MAX = 24;

/** v1 归档（单思考流 + 步骤列表）迁移：思考段在前、步骤在后——旧数据的
   时序已不可恢复，按旧呈现顺序平移。 */
function archivedTurnFromLegacy(value: unknown): ArchivedTurn | null {
  if (!value || typeof value !== "object") return null;
  const record = value as { segments?: unknown; thinkingText?: unknown; thinkingMs?: unknown; steps?: unknown };
  if (Array.isArray(record.segments)) return { segments: record.segments as CoachWorkSegment[] };
  const thinkingText = typeof record.thinkingText === "string" && record.thinkingText.trim().length > 0
    ? record.thinkingText
    : null;
  const steps = Array.isArray(record.steps) ? (record.steps as CoachToolStep[]) : [];
  if (!thinkingText && steps.length === 0) return null;
  const segments: CoachWorkSegment[] = [];
  if (thinkingText) {
    segments.push({
      kind: "thinking",
      key: "legacy-think",
      text: thinkingText,
      streaming: false,
      startedAtMs: null,
      frozenMs: typeof record.thinkingMs === "number" ? record.thinkingMs : null,
    });
  }
  for (const step of steps) segments.push({ kind: "tool", step });
  return { segments };
}

function readArchivedTurns(): Map<string, ArchivedTurn> {
  if (typeof window === "undefined") return new Map();
  try {
    const raw = window.localStorage.getItem(ARCHIVED_TURNS_KEY);
    if (!raw) return new Map();
    const entries = Object.entries(JSON.parse(raw) as Record<string, unknown>);
    const restored = new Map<string, ArchivedTurn>();
    for (const [key, value] of entries) {
      const turn = archivedTurnFromLegacy(value);
      if (turn) restored.set(key, turn);
    }
    return restored;
  } catch {
    return new Map();
  }
}

function persistArchivedTurns(map: Map<string, ArchivedTurn>) {
  if (typeof window === "undefined") return;
  try {
    let entries = [...map.entries()];
    if (entries.length > ARCHIVED_TURNS_MAX) entries = entries.slice(-ARCHIVED_TURNS_MAX);
    window.localStorage.setItem(ARCHIVED_TURNS_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    // 配额超限/隐私模式等：持久化失败只影响刷新后的恢复，不阻断当次会话。
  }
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
  const messagesRef = useRef<HTMLDivElement | null>(null);
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
  // 思考段与工具段的实时时序流（0828）：SSE activity/partial 逐段增量；
  // 轮询兜底无实时 activity，渲染时从 run.events 重建（deriveWorkSegments）。
  // 段内文本 full-replace；思考段在下一轮开始/首个正文/回合终结时冻结。
  const [liveSegments, setLiveSegments] = useState<CoachWorkSegment[]>([]);
  const liveSegmentsRef = useRef<CoachWorkSegment[]>([]);
  liveSegmentsRef.current = liveSegments;
  // 丢帧兜底段的 key 序号（partial 帧没有稳定事件标识）。
  const partialKeyRef = useRef(0);
  // 分段协议握手：thinking started 帧恒带 thinking_text（null 也带）＝新
  // 协议；字段缺失＝旧版 sidecar（partial 是累积全文）——按轮写段会把同一
  // 份全文重复写进每一段（表现为"十几个思考块点开都一样"），必须整体
  // 降级为单思考块模式。
  const segmentedThinkingRef = useRef(false);

  const clearThinkingStream = useCallback(() => {
    setLiveSegments([]);
  }, []);

  // 成功回合的活动摘要：对话流不再「成功即消失」，工作流时序段保留在
  // 已落库回答之上，直到下一次发送（切换会话不再清除，见 archivedTurns）。
  // 按会话键缓存 + localStorage 持久化（0828 拍板）：刷新/重启后切回原会话，
  // 思考/步骤归档块仍可恢复；仅保留最近 ARCHIVED_TURNS_MAX 个会话的归档。
  const [archivedTurns, setArchivedTurns] = useState<Map<string, ArchivedTurn>>(readArchivedTurns);
  // 渲染期镜像：settle 归档要在 updater 外组装下一份 Map（updater 可能被
  // React 延迟到渲染阶段才执行，不能在里面读可变 ref / 做持久化副作用）。
  const archivedTurnsRef = useRef(archivedTurns);
  archivedTurnsRef.current = archivedTurns;

  /** SSE activity：thinking started＝新思考段开始（上一段由帧内终文冻结）；
      tool started/completed＝工具段入列/收尾。 */
  const applyLiveActivity = useCallback((event: CoachAgentRunEventV1) => {
    const payload = event.payload ?? {};
    const createdAtMs = Date.parse(event.created_at);
    if (event.type === "phase" && event.code === "thinking_started") {
      // 旧协议（字段缺失）：不按轮开段，保持单思考块降级模式。
      if (payload.thinking_text === undefined) return;
      segmentedThinkingRef.current = true;
      const settledText = typeof payload.thinking_text === "string" ? payload.thinking_text : null;
      setLiveSegments((current) => {
        const next = [...current];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          const segment = next[i];
          if (segment.kind !== "thinking") continue;
          if (segment.streaming) next[i] = freezeThinkingSegment(segment, settledText, createdAtMs || Date.now());
          break;
        }
        next.push({
          kind: "thinking",
          key: event.event_ref ?? `live-think-${event.sequence}`,
          text: "",
          streaming: true,
          startedAtMs: Number.isFinite(createdAtMs) ? createdAtMs : Date.now(),
          frozenMs: null,
        });
        return next;
      });
      return;
    }
    if (event.type === "tool") {
      const step = stepFromToolEvent(event, null);
      // 动手即思考段终结（想完才做）：冻结最后一个流式思考段。
      const frozenAt = Number.isFinite(createdAtMs) ? createdAtMs : Date.now();
      setLiveSegments((current) => {
        const next = [...current];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          const segment = next[i];
          if (segment.kind !== "thinking") continue;
          if (segment.streaming) next[i] = freezeThinkingSegment(segment, null, frozenAt);
          break;
        }
        const index = next.findIndex((segment) => segment.kind === "tool" && segment.step.key === step.key);
        if (index >= 0) {
          const existing = next[index];
          if (existing.kind === "tool") next[index] = { kind: "tool", step: stepFromToolEvent(event, existing.step) };
        } else {
          next.push({ kind: "tool", step });
        }
        return next;
      });
    }
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
  // ── 划选引用（quote-reply，docs/quote-feature-research.md）───────────
  // 引用块是 composer 外挂结构（独立数组），不混进 textarea 字符串：
  // 每块可整块删除、文字锁定不可编辑；发送时才由 composeQuotedContent 拼装。
  const [quotes, setQuotes] = useState<CoachQuote[]>([]);
  const quoteSeqRef = useRef(0);
  // 划选浮层状态：文本在 mouseup 时已快照进 state（WKWebView 上点击按钮
  // 可能折叠原生选区，点击「引用」只读快照不再依赖 window.getSelection）。
  const [selectionBar, setSelectionBar] = useState<
    { text: string; left: number; top: number; flipBelow: boolean } | null
  >(null);
  const selectionToolbarRef = useRef<HTMLDivElement | null>(null);
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

  // 草稿三级持久（digests §11 item 4 + 拍板③）：sessionId | PENDING_CONVO | NEW_CONVO，
  // 多窗格实例按 layoutMode 加 pane 后缀防串；400ms debounce 落 localStorage。
  // 存储值为 envelope v2 { v:2, text, quotes }；读端兼容 legacy 纯文本。
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
    const restored = readCoachDraftEnvelope(window.localStorage, draftStorageKey);
    setDraft(restored.text);
    setQuotes(restored.quotes);
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
      () => writeCoachDraftEnvelope(window.localStorage, draftStorageKey, { text: draft, quotes }),
      COACH_DRAFT_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [draft, quotes, draftStorageKey]);

  const activeSessionKey = draftSession ? "draft" : `session:${sessionId ?? "primary"}`;
  const activeSessionKeyRef = useRef(activeSessionKey);
  const runBySessionRef = useRef(new Map<string, CoachAgentRunV1>());
  activeSessionKeyRef.current = activeSessionKey;
  // 当前会话的归档回合（渲染与清除都只看当前键；键是字符串，可直接作 Map key）。
  const archivedTurn = archivedTurns.get(activeSessionKey) ?? null;
  // 新回合开始时清除本会话的归档摘要；其他会话的条目保留。
  const clearArchivedTurn = useCallback(() => {
    setArchivedTurns((current) => {
      const nextTurns = new Map(current);
      nextTurns.delete(activeSessionKeyRef.current);
      persistArchivedTurns(nextTurns);
      return nextTurns;
    });
  }, []);

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
    // 归档摘要按会话键缓存在 archivedTurns 里，切换会话不再删除对应条目，
    // 切回时可恢复（0827 拍板）；活跃 run 的思考流维持不跨会话保留。
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
      clearArchivedTurn();
      clearThinkingStream();
      setRun(softStartRun);
      return;
    }
    setRun(null);
    void refresh();
  }, [refresh, softStartRun, clearThinkingStream, clearArchivedTurn]);

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

    // 成功终态统一收敛：回合工作流时序归档（思考段/工具段交错，思考段在
    // 归档时刻统一冻结），实时段清空，本地 run 解除以落库消息接管对话。
    const settleSucceeded = (next: CoachAgentRunV1) => {
      const settledAt = Date.now();
      // SSE 模式用实时段；轮询兜底（无 activity 直送）从 events 重建。
      // 立即快照冻结：下面 clearThinkingStream 会同步清空实时状态，任何
      // 延迟到渲染阶段的行为都不能再影响归档内容。
      const source = liveSegmentsRef.current.length > 0 ? liveSegmentsRef.current : deriveWorkSegments(next);
      const settledSegments = source
        .map((segment) =>
          segment.kind === "thinking" && segment.streaming
            ? freezeThinkingSegment(segment, null, settledAt)
            : segment,
        )
        .filter((segment) => segment.kind !== "thinking" || segment.text.trim().length > 0);
      // 写入当前会话键的归档条目（Map + localStorage 持久化，切换/刷新不丢）。
      const nextTurns = new Map(archivedTurnsRef.current);
      nextTurns.set(activeSessionKeyRef.current, { segments: settledSegments });
      setArchivedTurns(nextTurns);
      persistArchivedTurns(nextTurns);
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
          // 重发的最新正文）。thinking_text＝当前思考段全文（full-replace），
          // 写进最后一个流式思考段；首个非空回答 token 冻结思考段时长。
          const data = JSON.parse(event.data) as { text?: unknown; thinking_text?: unknown };
          const thinking = typeof data.thinking_text === "string" ? data.thinking_text : "";
          if (thinking.trim()) {
            setLiveSegments((current) => {
              const next = [...current];
              if (segmentedThinkingRef.current) {
                // 新协议：thinking_text＝当前思考段全文。开段只由轮边界
                // activity 驱动——帧里 thinking/text 可能交错（sidecar 同帧
                // 双字段），这里永远写回最后一个思考段，绝不新开段。
                for (let i = next.length - 1; i >= 0; i -= 1) {
                  const segment = next[i];
                  if (segment.kind !== "thinking") continue;
                  next[i] = { ...segment, text: thinking };
                  return next;
                }
                // 完全没段（activity 全丢）才兜底开段。
                next.push({
                  kind: "thinking",
                  key: `live-think-p${partialKeyRef.current += 1}`,
                  text: thinking,
                  streaming: true,
                  startedAtMs: Date.now(),
                  frozenMs: null,
                });
                return next;
              }
              // 旧协议：累积全文，覆盖唯一思考块（v1 呈现语义）。
              const only = next.findIndex((segment) => segment.kind === "thinking");
              if (only >= 0) {
                const segment = next[only];
                if (segment.kind === "thinking") next[only] = { ...segment, text: thinking };
              } else {
                next.push({
                  kind: "thinking",
                  key: `live-think-legacy${partialKeyRef.current += 1}`,
                  text: thinking,
                  streaming: true,
                  startedAtMs: Date.now(),
                  frozenMs: null,
                });
              }
              return next;
            });
          }
          if (typeof data.text === "string") {
            // 正文增量只更新正文；思考段的冻结交给轮边界 activity / tool /
            // settle——同轮内 thinking 与 text 可能交替，这里冻结会撕裂段落。
            setRun((prev) => (prev ? { ...prev, partial_text: data.text as string } : prev));
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
            // 实时段时序同步推进（新思考段/工具步入列与收尾）。
            applyLiveActivity(streamedEvent);
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
  }, [liveRunRef, refresh, refreshCurrentTraining, sessionId, clearThinkingStream, applyLiveActivity]);

  /** 工作流时序段（run 期间）：SSE 实时优先，轮询兜底从 events 重建；
      分析类长任务注入本机历史 ETA。 */
  const workSegments = useMemo(() => {
    const source = liveSegments.length > 0 ? liveSegments : deriveWorkSegments(run);
    return source.map((segment) =>
      segment.kind === "tool"
        && segment.step.command
        && ANALYSIS_ETA_COMMANDS.has(segment.step.command)
        ? { ...segment, step: { ...segment.step, etaSeconds: analysisEtaSeconds } }
        : segment,
    );
  }, [liveSegments, run, analysisEtaSeconds]);
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

  // ── 划选 → 浮层（quote-reply，调研 §2.1/§2.2）────────────────────────
  // 主判定用 mouseup（WebKit 的 selectionchange 触发时机不稳，只用于收起清理）；
  // 合格选区必须完整落在同一条非流式 assistant 消息容器内（纯逻辑在
  // evaluateAssistantSelection，node:test 直测）。0828 滚动容器上移 panel 后：
  // 浮层仍锚定消息内容区（absolute 相对滚动内容，滚动即收起、不做跟随重算），
  // rect 取 mouseup 的消息区，滚动状态取 panel。
  const closeSelectionBar = useCallback(() => {
    setSelectionBar(null);
  }, []);

  const handleMessagesMouseUp = (event: ReactMouseEvent<HTMLElement>) => {
    const scroller = messagesRef.current;
    const hostRect = event.currentTarget.getBoundingClientRect();
    const selection = typeof window.getSelection === "function" ? window.getSelection() : null;
    const anchor = messageArticleFromNode(selection?.anchorNode ?? null);
    const focus = messageArticleFromNode(selection?.focusNode ?? null);
    const target = evaluateAssistantSelection({
      collapsed: !selection || selection.isCollapsed,
      selectedText: selection?.toString() ?? "",
      anchorArticle: anchor,
      focusArticle: focus,
    });
    if (!target || !scroller || !selection) {
      closeSelectionBar();
      return;
    }
    const rect = selectionAnchorRect(selection);
    if (!rect) {
      // WKWebView 折叠边界可能拿不到任何矩形：宁可不弹浮层。
      closeSelectionBar();
      return;
    }
    const rawLeft = rect.cx - hostRect.left + scroller.scrollLeft;
    const clampedLeft = Math.min(
      Math.max(rawLeft, Math.min(SELECTION_TOOLBAR_HALF_WIDTH, hostRect.width / 2)),
      Math.max(hostRect.width - SELECTION_TOOLBAR_HALF_WIDTH, SELECTION_TOOLBAR_HALF_WIDTH),
    );
    const localTop = rect.top - hostRect.top + scroller.scrollTop;
    setSelectionBar({
      text: target.text,
      left: clampedLeft,
      top: localTop,
      // 选区贴近视口顶部时翻到选区下方弹出。
      flipBelow: rect.top < 64,
    });
  };

  // 关闭路径：点击别处 / 选区折叠（selectionchange 收起清理）/ 消息列表滚动 /
  // Esc。点击工具条自身不关（mousedown preventDefault 同时保住原生选区）。
  useEffect(() => {
    if (!selectionBar) return undefined;
    const scroller = messagesRef.current;
    const onScroll = () => setSelectionBar(null);
    const onSelectionChange = () => {
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed) setSelectionBar(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") setSelectionBar(null);
    };
    const onPointerDown = (event: MouseEvent) => {
      if (selectionToolbarRef.current && !selectionToolbarRef.current.contains(event.target as Node)) {
        setSelectionBar(null);
      }
    };
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      scroller?.removeEventListener("scroll", onScroll);
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [selectionBar]);

  /** 点击「引用」：消费 mouseup 时快照的文本；折叠原选区并聚焦输入框。 */
  const addQuoteFromSelection = () => {
    const snapshot = selectionBar ? snapshotQuote(selectionBar.text) : null;
    setSelectionBar(null);
    window.getSelection()?.removeAllRanges();
    if (!snapshot) return;
    if (!snapshot.ok) {
      notify(`选中的引文超过 ${QUOTE_MAX_CHARS} 字符上限，请选择更短的内容再引用。`);
      return;
    }
    quoteSeqRef.current += 1;
    setQuotes((current) => [...current, { id: quoteSeqRef.current, text: snapshot.text }]);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
    });
  };

  /** 删除单条引用块（整块删除，文字本身锁定不可编辑）。 */
  const removeQuote = (id: number) => {
    setQuotes((current) => current.filter((item) => item.id !== id));
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
    opts: { force?: boolean } = {},
  ): Promise<boolean> => {
    const content = contentRaw.trim();
    if (!content || sendingRef.current) return false;
    const active = activeRunRef.current;
    if (!opts.force && active && ["queued", "running"].includes(active.status)) {
      // 已受理即清空输入：文本已移入 chip（不是复制），需要改写时走回填编辑。
      // 引用块已随拼装串进入 chip（拍板②降级为纯文本），待拼装引用一并消费。
      enqueueQueuedItem(content);
      setDraft("");
      setQuotes([]);
      notify("当前回复仍在生成中，这条已加入输入框上方队列，可随时取消、编辑或立即转向。");
      return true;
    }
    // 同步重入锁：必须在任何 await 之前置位，重入直接丢弃。
    sendingRef.current = true;
    // 新回合开始：清掉本会话上一回合的归档摘要与思考流残留。
    clearArchivedTurn();
    clearThinkingStream();
    let optimisticId: number | null = null;
    try {
      const effectiveSessionId = sessionId ?? (onEnsureSession ? await onEnsureSession() : null);
      if (sessionId === null && onEnsureSession && effectiveSessionId === null) {
        notify("未能创建会话，草稿已保留，请重试。");
        return false;
      }
      optimisticId = appendOptimisticUserMessage(content);
      setDraft("");
      // 受理成功即消费待拼装引用：拼装串已进入消息；失败分支保留引用回 composer。
      setQuotes([]);
      stickToBottomRef.current = true;
      pushSentHistory(content);
      const created = await createCoachAgentRun(
        content,
        effectiveSessionId == null ? {} : { sessionId: effectiveSessionId },
      );
      setRun(created);
      // 会话标题会随第一条消息更新，通知 AppShell 刷新侧栏列表。
      window.dispatchEvent(new CustomEvent("aiming-cookie:coach-session-updated"));
      return true;
    } catch (error) {
      if (optimisticId !== null) {
        setMessages((current) => current.filter((message) => message.id !== optimisticId));
      }
      // 仅当等待期间用户没有重新输入时才回填，避免覆盖新草稿。
      setDraft((current) => (current.trim() ? current : content));
      notify(requestFeedback(error, "消息未发送，草稿已保留，请重试。"));
      return false;
    } finally {
      sendingRef.current = false;
    }
  };

  /**
   * 出站内容统一组装口（调研 §3.3）：composer 的四条出站路径（普通提交 /
   * 立即转向 / 打断并转向 / 加入队列）都先经此处把引用块拼进最终串再分流，
   * 保证 steer 这类直连引擎的路径同样携带引文。长度预算与 quote-only
   * （拍板①）在此给出用户可见拒绝，不依赖 sidecar 静默切尾。
   */
  const composeOutgoing = (): string | null => {
    const body = draft.trim();
    if (quotes.length === 0) {
      if (!body) return null;
      if (body.length > SEND_BUDGET_CHARS) {
        notify(`消息 ${body.length} 字符，超出单条 ${SEND_BUDGET_CHARS} 上限，请精简后再发送。`);
        return null;
      }
      return body;
    }
    const composed = composeQuotedContent({ quotes, text: draft });
    if (composed === null) {
      // 拍板①：只有引用、没有正文时禁止发送。
      notify("只有引用、没有正文时不能发送，请补充你的问题或要求。");
      return null;
    }
    if (!isWithinSendBudget(composed)) {
      notify(`引用加正文合计 ${composed.length} 字符，超出单条 ${SEND_BUDGET_CHARS} 上限，请缩短引用或正文。`);
      return null;
    }
    return composed;
  };

  const submitComposer = () => {
    if (!draft.trim() && quotes.length === 0) return;
    const content = composeOutgoing();
    if (content === null) return;
    setMentionQuery(null);
    setSendMenuOpen(false);
    void sendText(content);
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
    if (!draft.trim() && quotes.length === 0) return;
    const content = composeOutgoing();
    if (content === null) return;
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
      // 引用已随拼装串注入本回合：待拼装引用一并消费。
      setQuotes([]);
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
    if (!draft.trim() && quotes.length === 0) return;
    const content = composeOutgoing();
    if (content === null) return;
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
    /* 滚动容器（0828）：滚动条挂在面板本身——贯穿全列高、贴窗口右缘；
       messagesRef 供吸底/未读/划选坐标共用，onScroll 检测吸底状态。 */
    <div className="task6-coach-panel" ref={messagesRef} onScroll={handleMessagesScroll}>
      {/* 悬浮顶区（0828）：header 与讨论挂载条一起 sticky 钉在滚动口顶部。 */}
      <div className="task6-coach-top">
        {header}

        {/* 本次讨论的分析挂载条：进行中的分析以 pending chip 呈现；已完成的
            讨论 chip 常驻保留（0827 拍板），作为打开视频讲解的常设入口。 */}
        {(pendingAnalyses.length > 0 || discussionAnalysisIds.length > 0) ? (
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
      </div>

      <div className="task6-messages-wrap">
      <section
        aria-label="Coach 消息"
        className="task6-messages"
        onMouseUp={handleMessagesMouseUp}
      >
        {/* 顶部锚定（0827 拍板，回退底部锚定）：消息从上往下自然排布，短
            会话不再在头部留大空洞；空会话时同槽位换成占满剩余空间的 hero 空态。 */}
        {messages.length === 0 && !run ? (
          <div className="task6-empty-hero">
            <Empty title="开始一段 Coach 对话">可以直接提问训练问题，Coach 会读取你的分析数据。</Empty>
          </div>
        ) : null}
        {messages.map((message, index) => (
          <Fragment key={message.id}>
            {/* 归档回合的过程摘要（已思考/步骤计数）挂在它产出的那条回复上方
                （0827 拍板 Codex 式次序），而不是回复下面。 */}
            {!run && archivedTurn != null && index === messages.length - 1 && message.role === "assistant" ? (
              /* 归档的工作流时序段（思考/工具交错，v2 localStorage）挂回复上方。 */
              <CoachWorkStream segments={archivedTurn.segments} />
            ) : null}
            <div className="task6-message-entry" data-role={message.role}>
              <article className="task6-message" data-role={message.role}>
                {message.role === "assistant" ? (
                  /* 受控富渲染（digests §10）：助手消息走 task7-rich 块结构，
                     不再套 <p>（表格/列表不能内嵌在段落里）。 */
                  <CoachMessageText text={message.content} analysisRef={defaultAnalysisRef} onOpenVideo={onOpenVideo} />
                ) : (
                  /* 已发送用户消息的引用块回显（Codex 不做回显是长期 bug）；
                     未命中拼装形状的 legacy 内容按原文纯文本渲染。 */
                  <UserMessageBody content={message.content} />
                )}
              </article>
            </div>
          </Fragment>
        ))}
        {/* 工作流时序流（思考段/工具段交错）在回复文字上方（0827 拍板 Codex
            式次序）。0828：条件从"进行中状态枚举"放宽为 run 存在即显示——
            succeeded 到归档接管之间有 refresh 网络往返，按枚举会在该空窗里
            整个消失。 */}
        {run ? <CoachWorkStream segments={workSegments} stopped={run.status === "stopped"} /> : null}
        {run?.partial_text ? (
          <article
            className="task6-message"
            data-role="assistant"
            /* 流式禁划标记（调研 §2.5）：划选判定看到 data-streaming 即不出浮层；
               不用 user-select 强禁，保留 Cmd+C 复制自由。终态残留的 partial
               文本是可引用的最终快照，不携带该标记。 */
            data-streaming={composerBusy ? "true" : undefined}
          >
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
        {/* 划选浮层：锚定在消息滚动内容内部（随滚动归位由 scroll 关闭接管） */}
        {selectionBar ? (
          <div
            aria-label="划选操作"
            className="task6-selection-toolbar"
            data-flip={selectionBar.flipBelow ? "below" : "above"}
            onMouseDown={(event) => event.preventDefault()}
            onMouseUp={(event) => event.stopPropagation()}
            ref={selectionToolbarRef}
            role="toolbar"
            style={{ left: `${selectionBar.left}px`, top: `${selectionBar.top}px` }}
          >
            <button onClick={addQuoteFromSelection} type="button">引用</button>
          </div>
        ) : null}
      </section>
      </div>

      <footer className="task6-composer">
        {/* 未读提示（0828）：挂 composer 钉在其上沿之外，随悬浮输入框恒定可见。 */}
        {unreadCount > 0 ? (
          <button className="task6-unread-prompt" onClick={scrollToLatest} type="button">
            ↓ {unreadCount} 条新内容 · 回到底部
          </button>
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
        {/* 划选引用块（textarea 上方独立插槽）：多条并存，逐条整块删除；
            文本体锁定不可编辑（只读呈现元素），超长块内部滚动。 */}
        {quotes.length > 0 ? (
          <div aria-label="引用 Coach 的发言" className="task6-quote-list" role="list">
            {quotes.map((quote) => (
              <blockquote className="task6-quote-block" key={quote.id} role="listitem">
                <span className="task6-quote-head">引用 Coach</span>
                <p className="task6-quote-body" title={quote.text}>{quote.text}</p>
                <IconButton label="删除这条引用" onClick={() => removeQuote(quote.id)} size="compact" title="删除整块引用（文字不可编辑）">
                  <IconClose />
                </IconButton>
              </blockquote>
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
          {/* 右下角簇（0827 拍板 B1）：模型选择挪到与发送键同一行、发送键左侧，
              取代旧「输入卡下方工具行」；wrap 自带 position:relative 锚点，
              菜单仍向上弹出。 */}
          <div className="task6-composer-corner">
            <CoachModelMenu
              onError={(message) => notify(message)}
            />
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
                    <button disabled={!draft.trim()} onClick={() => { setSendMenuOpen(false); const content = composeOutgoing(); if (content === null) return; enqueueQueuedItem(content); setDraft(""); setQuotes([]); }} role="menuitem" type="button">
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
              /* 拍板①：quote-only 仍被禁用（引用不构成正文），悬停给出发送提示。 */
              <button
                aria-label="发送"
                className="task6-composer-send"
                disabled={!draft.trim()}
                onClick={submitComposer}
                title={draft.trim() || quotes.length === 0 ? undefined : "只有引用、没有正文时不能发送，请补充你的问题或要求"}
                type="button"
              ><IconSend /></button>
            )}
          </div>
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
