"use client";

import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import {
  createCoachAgentRun,
  deleteCurrentTraining,
  getCoachAgentRun,
  getCoachAgentRunStreamUrl,
  getCoachSession,
  getCurrentTraining,
  listSessions,
  retryCoachAgentRun,
  stopCoachAgentRun,
} from "@/lib/api";
import { isDesktopRuntime, openKovaakScenario } from "@/lib/desktop";
import { ANALYSIS_AUTO_TEACH_EVENT, COACH_PENDING_INTENT_KEY, COACH_SESSION_UPDATED_EVENT, computeAnalysisEtaSeconds, formatHistoryDate } from "@/lib/contracts";
import { discussionChipLabel, groupDiscussionChips, DISCUSSION_BAR_MAX_PINNED } from "@/lib/discussion-bar";
import { MEMBER_COPY, bothPoolsEmpty, classifyMemberGatewayError, formatMemberDate, gatewayErrorNotice } from "@/lib/member";
import { useMemberState } from "@/lib/member-state";
import { MemberNotice, memberEndDate, memberNotice, memberNoticeText } from "@/components/task3/MemberChrome";
import { coachGreeting, coachHomeChips } from "@/lib/coach-home";
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
import { CoachWorkStream, ElapsedTicker, freezeThinkingSegment, settleTerminalWorkSegments, type CoachToolStep, type CoachWorkSegment } from "./CoachRunActivity";
import type {
  CoachAgentRunEventV1,
  CoachAgentRunV1,
  CoachThreadMessageOut,
  CurrentTrainingItemV1,
  CurrentTrainingV1,
  ProviderProfileState,
  SessionListItem,
} from "@/lib/types";
import { IconChevronDown, IconClose, IconHistory, IconPlus, IconSend, IconStop } from "@/ui/icons";
import { Button, ErrorState, IconButton, Status, Toast, useAnimatedPresence } from "@/ui/primitives";

type CoachCapability = "loading" | ProviderProfileState | "unavailable";

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

// run 创建期间（ensureSession 串行链 + create 往返）的占位工作段标签：与
// queued 态的"等待开始"同款，落 run 后无缝换成真实事件流（0910 死窗修复）。
// 计时起点在发送时写入 pendingRunStartAtRef（走表，0912 审计），故段在
// 渲染处内联构造，不再是模块常量。

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
/** 结构化分析引用 chip（引用菜单选中）：token 供发送结构化挂载，label 给人看。 */
type MentionRefChip = { token: string; label: string };

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
  "purchase_links.lookup": "查询购买链接",
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

/**
 * 从 run 事件序列重建交错工作流段（轮询兜底与刷新恢复路径；SSE 实时路径
 * 走 liveSegments 增量）。thinking_started 事件＝上一思考段边界：其 payload
 * 的 thinking_text 是上一段终文（sidecar 每轮冻结下发），据此补齐丢帧。
 */
function deriveWorkSegments(run: CoachAgentRunV1 | null): CoachWorkSegment[] {
  if (!run) return [];
  const segments: CoachWorkSegment[] = [];
  // 占位步的经过计时基准（0912 审计：等待期只有静态文字，不走表）。
  const runStartMs = Number.isFinite(Date.parse(run.created_at)) ? Date.parse(run.created_at) : null;
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
          startedAtMs: runStartMs,
        },
      });
    } else if (segments.length === 0) {
      // 0910（点点）：running 且全无事件（首 token 延迟、引擎内部重试的沉默
      // 期）同样要进工作态——占位表达"正在理解问题"，不违反 0828（那条移除
      // 的是思考/正文已可见时的重复占位）。
      segments.push({
        kind: "tool",
        step: {
          key: "coach-running-placeholder",
          label: "正在理解问题和分析上下文",
          meta: null,
          state: "active",
          command: null,
          startedAtMs: runStartMs,
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
/**
 * Extract the latest `scenario.open` UI event (kind=scenario) from a run's
 * event list. The Coach only requests the open; the frontend performs the
 * local Scenarios/*.sce resolution + Steam deep-link dispatch.
 */
function scenarioTargetFromRun(run: CoachAgentRunV1 | null): {
  scenarioName: string;
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
      && (candidate as Record<string, unknown>).kind === "scenario"
    ) {
      const scenarioName = (candidate as Record<string, unknown>).scenario_name;
      if (typeof scenarioName === "string" && scenarioName.trim()) {
        const name = scenarioName.trim();
        return { scenarioName: name, eventKey: `${event.event_ref ?? event.sequence}:${name}` };
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

/**
 * 退场存在（0912）：点击删除先标记 exiting 播收拢动画，animationend（或
 * 兜底计时）真正从 state 移除；reduced-motion 下没有动画，直接移除，避免
 * 等不到 animationend 而卡在半删状态。
 */
function useExitAnimation<K extends number | string>(
  onFinalize: (key: K) => void,
  fallbackMs: number,
) {
  const [exitingKeys, setExitingKeys] = useState<K[]>([]);
  const timersRef = useRef(new Map<K, ReturnType<typeof setTimeout>>());

  const finalize = useCallback((key: K) => {
    const timer = timersRef.current.get(key);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(key);
    }
    setExitingKeys((current) => current.filter((item) => item !== key));
    onFinalize(key);
  }, [onFinalize]);

  const requestExit = useCallback((key: K) => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      finalize(key);
      return;
    }
    setExitingKeys((current) => (current.includes(key) ? current : [...current, key]));
    if (!timersRef.current.has(key)) {
      timersRef.current.set(key, setTimeout(() => finalize(key), fallbackMs));
    }
  }, [finalize, fallbackMs]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.clear();
    };
  }, []);

  return { exitingKeys, requestExit, finalize };
}

export function CoachPanel({
  capability,
  draftSession = false,
  sessionId = null,
  handoverSessionId = null,
  onEnsureSession,
  onHomeShellChange,
  onOpenVideo,
  pathname = "/history",
  softStartRun = null,
  onActiveRunChange,
  onCoachMessagesChange,
}: {
  capability: CoachCapability;
  draftSession?: boolean;
  sessionId?: number | null;
  /** 首条发送交接窗（AppShell 同名状态）：新会话已建、选择未落地的窗口。 */
  handoverSessionId?: number | null;
  onEnsureSession?: () => Promise<number | null>;
  /** 空对话首页激活态上报（v6：AppShell 据此隐藏共用顶栏，三键常浮）。 */
  onHomeShellChange?: (active: boolean) => void;
  onOpenVideo?: (analysisRef: string, timeMs?: number) => void;
  pathname?: string;
  softStartRun?: CoachAgentRunV1 | null;
  onActiveRunChange?: (active: boolean) => void;
  /** 当前会话 assistant 讲解文本上报：视频面板底部回看 chips 跟随正文 @time。 */
  onCoachMessagesChange?: (texts: ReadonlyArray<string>) => void;
}) {
  const router = useRouter();
  const [messages, setMessages] = useState<CoachThreadMessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<CoachAgentRunV1 | null>(null);
  // 会员态（④ 双池皆空 / ⑨ 提示 / 401 降级）：与 AppShell 同源，60s 轮询。
  const member = useMemberState();
  const [memberNoticeDismissed, setMemberNoticeDismissed] = useState(false);
  const [memberSoftNoticeDismissed, setMemberSoftNoticeDismissed] = useState(false);
  // ④/⑨ 右：会员双池皆空才触发发送禁用（历史照常可读）。BYOK 用户 me=null，
  // 永远不进这两态——会员体系不影响 BYOK（回归红线）。
  const memberQuotaOut = member.me !== null && member.me.member && bothPoolsEmpty(member.me);
  const memberConnectionLost = member.me !== null
    && (member.me.status === "expired" || member.me.status === "refunded")
    && bothPoolsEmpty(member.me);
  const sendBlockedByMember = memberQuotaOut || memberConnectionLost;
  const memberPeriodEnd = formatMemberDate(member.me?.period_end ?? null);
  const memberBlockNotice = memberQuotaOut
    ? MEMBER_COPY.quotaExhausted
    : memberConnectionLost
      ? MEMBER_COPY.connectionLost(memberPeriodEnd)
      : null;
  // ⑨ 左（订阅失效但加油包有余量）与 ⑧（扣款失败）：线框里这两条挂在教练页，
  // 一次性可关闭；到期状态由左下角 chip 常驻承载。④（双池皆空）覆盖前者，
  // 故 "lost" 在这里让位给上面的 block 提示。推导复用 memberNotice 单一事实源。
  const memberSoftNoticeKey = memberBlockNotice ? null : memberNotice(member.me);
  const memberSoftNotice = memberSoftNoticeKey && memberSoftNoticeKey !== "lost" && member.me
    ? memberNoticeText(memberSoftNoticeKey, member.me, memberEndDate(member.me))
    : null;
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
  // exitMs 260 > 行高塌缩过渡 200ms（--duration-surface）：离场卸载不能剪掉
  // 收起动画的尾巴（0912 审计：训练计划收起动效重做后的配平）。
  const trainingPresence = useAnimatedPresence(trainingExpanded, 260);
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

  // 错误卡独立存活态（0912 逐帧审计）：failed run 会被若干路径置回 null
  // （终态后的 refresh 竞态、切换会话等），run 一空错误卡凭空消失，失败回合
  // 就成了没有任何重试入口的死胡同。卡片快照带所属会话 id，按会话归属渲染：
  // 切到别的会话自然隐藏、切回来恢复显示；只随用户动作清除——点「重试」、
  // 点「稍后再说」、或新回合/重试把 run 拉回 queued/running。
  // （勿按 activeSessionKey 变化清除：首条发送的 sessionId 落地可以晚十几秒
  // ——列表刷新很慢——那次"键落地"不是用户切换，会把新失败卡误清掉。）
  const [failedCard, setFailedCard] = useState<
    null | { sessionId: number; runRef: string; title: string; message: string; retryable: boolean }
  >(null);
  useEffect(() => {
    if (run && ["queued", "running"].includes(run.status)) {
      setFailedCard(null);
      return;
    }
    if (run?.status === "failed" && run.error) {
      setFailedCard({
        sessionId: run.session_id,
        runRef: run.run_ref,
        title: runErrorTitle(run.error),
        message: run.error.message,
        retryable: run.error.retryable,
      });
    }
  }, [run]);

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
  // @ 引用下拉（LibreChat Mention 骨架）
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const mentionCaretRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const trainingPopRef = useRef<HTMLDivElement | null>(null);
  const trainingChipRef = useRef<HTMLButtonElement | null>(null);
  const trainingChipLabelRef = useRef<HTMLSpanElement | null>(null);

  // 输入框自动长高（0911 点点，对齐 ZCode 规格）：随内容增长，上限 8 行；
  // 超过 8 行框停住、overflow 转 auto（内部滚动条）。padding（模型钮/引用钮
  // 让位 44px 等）动态读取，行高以 computed lineHeight 为准。
  const composerInputRef = useRef<HTMLDivElement | null>(null);
  const autosizeComposer = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const cs = getComputedStyle(ta);
    const line = parseFloat(cs.lineHeight) || 18;
    const max = parseFloat(cs.paddingTop) + line * 8 + parseFloat(cs.paddingBottom);
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, max)}px`;
    ta.style.overflowY = ta.scrollHeight > max + 1 ? "auto" : "hidden";
  }, []);
  useLayoutEffect(() => {
    autosizeComposer();
  }, [draft, autosizeComposer]);
  // 高度自愈（0918 点点报障「输入框高度塌了」）：autosize 原本只认 draft，
  // 模型钮在 provider 发现模型的窗口期会整颗卸载（CoachModelMenu return
  // null），期间重算会把过时的小高度写进 inline style；按钮恢复后没人再
  // 重算，塌陷高度就一直卡到下次输入。观察输入卡宽度（拖窗折行变化）与
  // 角簇子树（模型钮挂载/卸载），布局一变即重算自愈。
  useEffect(() => {
    const input = composerInputRef.current;
    if (!input) return undefined;
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => autosizeComposer()) : null;
    if (ro) ro.observe(input);
    const cornerEl = input.querySelector(".task6-composer-corner");
    const mo = typeof MutationObserver !== "undefined" && cornerEl ? new MutationObserver(() => autosizeComposer()) : null;
    if (mo && cornerEl) mo.observe(cornerEl, { childList: true, subtree: true });
    return () => {
      ro?.disconnect();
      mo?.disconnect();
    };
  }, [autosizeComposer]);
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

  // 草稿三级持久（digests §11 item 4 + 拍板③）：sessionId | PENDING_CONVO | NEW_CONVO；
  // 400ms debounce 落 localStorage。存储值为 envelope v2 { v:2, text, quotes }；
  // 读端兼容 legacy 纯文本。（多窗格 pane 后缀已删：唯一调用点固定 full 档，
  // 本就不产生后缀，存储键保持不变。）
  const draftScope: CoachDraftScope = draftSession
    ? { kind: "new-convo" }
    : sessionId == null
      ? { kind: "pending-convo" }
      : { kind: "session", sessionId };
  const draftStorageKey = coachDraftStorageKey(draftScope);
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
  // 发送即消费：同步清掉当前 scope 键与 NEW_CONVO/PENDING_CONVO，绕过 400ms
  // 防抖。防抖清空会与新会话 scope 切换竞速——清空写落到切换后的 session 键，
  // NEW_CONVO 残留已发全文，下次新建对话被恢复成预填（0911 审计 §12.2）。
  const clearComposerDraftStorage = () => {
    const empty = { text: "", quotes: [] as CoachQuote[] };
    writeCoachDraftEnvelope(window.localStorage, draftStorageKey, empty);
    writeCoachDraftEnvelope(window.localStorage, coachDraftStorageKey({ kind: "new-convo" }), empty);
    writeCoachDraftEnvelope(window.localStorage, coachDraftStorageKey({ kind: "pending-convo" }), empty);
  };

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
    if (draftSession) {
      setMessages([]);
      setAnalysisSessionIds([]);
      setDeepReadAnalysisSessionIds([]);
      setLoadError(false);
      return;
    }
    if (sessionId == null) {
      if (handoverSessionId == null) {
        // 无交接在途（删除/归档当前会话后选择 momentarily 为空）：整表清空，
        // 不让已删会话的消息残留在屏上（0911"闪成旧会话"同族敏感区）。
        // 同时清掉残留的 run 与失败卡——否则已删会话的错误卡和工作流会
        // 以僵尸形态挂在空页上（0912 晚点点截图实锤）。
        setMessages([]);
        setRun(null);
        setFailedCard(null);
        setAnalysisSessionIds([]);
        setDeepReadAnalysisSessionIds([]);
        setLoadError(false);
        return;
      }
      // 首条发送交接窗（ensureSession 已建会话、sessionId prop 还没落地）：
      // 只能清后端来源的消息，乐观气泡必须原地保留——整表清空正是"首条消息
      // 发送后气泡凭空消失直到回合结束"的根源（0912 逐帧审计）；等 sessionId
      // 落地后 refresh 的合并逻辑会按 role+content 对乐观气泡去重接管。
      setMessages((current) => current.filter((message) => message.id < 0));
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
        // 乐观气泡已被后端接管（同 role+content）时丢弃，否则它一定比所有
        // 落库消息都新（id<0 只由本次新增的未落库消息产生），必须拼在
        // backendMessages 之后；拼在最前会让第二条消息显示在第一条上面
        // （0915 CDP 真机实测的时序倒错）。
        const uniqueOptimistic = optimistic.filter(
          (message) => !backendKeys.has(`${message.role}\x00${message.content}`),
        );
        return [...backendMessages, ...uniqueOptimistic];
      });
      setAnalysisSessionIds(detail.analysis_session_ids ?? []);
      setDeepReadAnalysisSessionIds(detail.deep_read_analysis_session_ids ?? []);
      setLoadError(false);
    } catch {
      if (revision === refreshRevisionRef.current) setLoadError(true);
    }
  }, [capability, draftSession, sessionId, handoverSessionId]);

  // 会话键切换时的活跃回合迁移：首条发送的 run 建立于 draft 键下，
  // ensureSession 的慢速列表刷新返回后 sessionId prop 才落地、键才切到
  // session:N——若按"取新键下的 run"整表清空，正在生成的工作态、SSE 流式
  // 监听和终态 finalize 会被整链误杀，落库的消息从此无人接上屏（0912 点点
  // 三连实锤）。run.session_id 与新键会话一致时原地搬键延续；其余切换
  // （用户换会话等）维持原"取新键"语义。
  const prevSessionKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const prevKey = prevSessionKeyRef.current;
    prevSessionKeyRef.current = activeSessionKey;
    const migrating = prevKey !== null && prevKey !== activeSessionKey
      ? runBySessionRef.current.get(prevKey)
      : null;
    if (
      prevKey !== null
      && migrating
      && ["queued", "running"].includes(migrating.status)
      && sessionId != null
      && migrating.session_id === sessionId
    ) {
      // 原地搬键：setRun 收到同一引用直接 bail，工作态/思考流/SSE 无感延续。
      runBySessionRef.current.delete(prevKey);
      runBySessionRef.current.set(activeSessionKey, migrating);
      setRun(migrating);
      setUnreadCount(0);
      stickToBottomRef.current = true;
      return;
    }
    // 终态 run 不恢复：切换窗口里 run 可能已走到终态落库（归档接管渲染），
    // 把终态对象原样挂回会挡住归档、自身又从 events 重建出空段（0912 深夜
    // 切走切回实锤：工作流整块消失）。活跃 run 才值得跨切换延续。
    const restored = runBySessionRef.current.get(activeSessionKey) ?? null;
    setRun(restored != null && ["queued", "running"].includes(restored.status) ? restored : null);
    setUnreadCount(0);
    stickToBottomRef.current = true;
    clearThinkingStream();
    // 错误卡的跨会话显隐由 failedCard.sessionId 归属渲染决定（见上），
    // 这里不再清——"键落地"≠用户切换，清了会把首条发送的新失败卡误掉。
    // 归档摘要按会话键缓存在 archivedTurns 里，切换会话不再删除对应条目，
    // 切回时可恢复（0827 拍板）。
  }, [activeSessionKey, clearThinkingStream, sessionId]);

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

  // Coach's `scenario.open` emits a kind=scenario UI event only after the user
  // confirmed in conversation; open exactly once per event so streaming and
  // polling both resolve it.
  const handledScenarioEventsRef = useRef(new Set<string>());
  useEffect(() => {
    const target = scenarioTargetFromRun(run);
    if (!target || handledScenarioEventsRef.current.has(target.eventKey)) return;
    handledScenarioEventsRef.current.add(target.eventKey);
    // 0912 点点拍板：成功派发不上 toast——KovaaK 窗口自己弹出即反馈；
    // 失败路径（未映射/未安装/派发失败）仍提示。
    void openKovaakScenario(target.scenarioName)
      .then((result) => {
        if (result.status !== "scenario_dispatched") notify(result.message);
      })
      .catch(() => notify("未能请求打开 KovaaK，请稍后重试"));
  }, [notify, run]);

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
  type DiscussionAnalysisInfo = { scenario: string | null; runId: number | null; when: string | null };
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
        for (const item of response.sessions) {
          map[item.id] = {
            scenario: item.scenario ?? null,
            runId: item.kovaak_run_id ?? null,
            when: item.training_at ?? item.created_at ?? null,
          };
        }
        setAnalysisScenarios(map);
      })
      .catch(() => {
        if (!cancelled) setAnalysisScenarios({});
      });
    return () => { cancelled = true; };
  }, [discussionAnalysisKey]);

  // 已完成讨论 chip 的折叠态（0905 拍板）：只平铺前 3 个，余量收进行尾 ▾
  // 下拉。面板长驻、切换会话不重置——条目由 discussionAnalysisIds 驱动，
  // 开关状态只归用户。进行中的 pending chip 不参与折叠（调用方永远平铺）。
  const [discussionOverflowOpen, setDiscussionOverflowOpen] = useState(false);
  const discussionBarRef = useRef<HTMLDivElement | null>(null);
  // 0918 宽度感知收编（彩名报障「框体砍半/只露个头」）：平铺数不再写死 3，
  // 顶栏放不下就整颗收进 ▾ 菜单，半截 chip 只允许作为 pending 超长的 CSS
  // 兜底存在。收敛规则见 portal 后的挤压反馈 layout effect。
  const [discussionPinnedCount, setDiscussionPinnedCount] = useState(DISCUSSION_BAR_MAX_PINNED);
  const discussionChipsRef = useRef<HTMLDivElement | null>(null);
  const discussionShrinkFailedRowWidthRef = useRef<number | null>(null);
  const [discussionRowWidthTick, setDiscussionRowWidthTick] = useState(0);
  useEffect(() => {
    if (!discussionOverflowOpen) return undefined;
    // 关闭路径（CoachModelMenu 同款惯例）：点击菜单外部 mousedown 关闭 +
    // Escape 关闭（IME 守卫，输入法确认候选词期间不消费 Esc）。
    const onPointerDown = (event: MouseEvent) => {
      if (discussionBarRef.current && !discussionBarRef.current.contains(event.target as Node)) {
        setDiscussionOverflowOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") setDiscussionOverflowOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [discussionOverflowOpen]);
  const discussionChips = discussionAnalysisIds.map((id) => ({
    id,
    label: discussionChipLabel(id, analysisScenarios[id]),
  }));
  // 标签文案宽度参与的收敛重算锚：场景名异步补齐会改变 chip 宽度。
  const discussionLabelsKey = discussionChips.map((chip) => chip.label).join("\n");
  const { pinned: pinnedDiscussionChips, overflow: overflowDiscussionChips } = groupDiscussionChips(discussionChips, discussionPinnedCount);

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
    // 自动开讲不能依赖用户守在分析页：在同一次列表响应里维护「本生命周期内
    // 观察到 running → done」的集合，对新完成的 id 以 ANALYSIS_AUTO_TEACH_EVENT
    // 派发（AppShell 监听后单点开讲，防重沿用其 localStorage 标记）。
    // 只触发新鲜转换，翻旧记录不开讲。
    const seenRunning = new Set<number>();
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
        const finished: number[] = [];
        for (const item of response.sessions) {
          if (item.status === "queued" || item.status === "running") {
            seenRunning.add(item.id);
          } else if (item.status === "done" && seenRunning.has(item.id)) {
            seenRunning.delete(item.id);
            finished.push(item.id);
          } else {
            seenRunning.delete(item.id);
          }
        }
        for (const id of finished) {
          window.dispatchEvent(new CustomEvent(ANALYSIS_AUTO_TEACH_EVENT, {
            detail: { analysis_ref: `analysis:${id}` },
          }));
        }
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
  // 点点 09-08：分析耗时随 analysis_type 差一个量级，进行中的分析会话入列后
  // （3s 轮询）按其类型分桶——发起的头一两秒用全局中位数，随后渐进精确。
  const ANALYSIS_ETA_COMMANDS = new Set(["analysis.create_from_run", "analysis.retry"]);
  const currentAnalysisType = useMemo(
    () =>
      sessionsSnapshot.find((item) => item.status === "queued" || item.status === "running")
        ?.analysis_type ?? null,
    [sessionsSnapshot],
  );
  const analysisEtaSeconds = useMemo(
    () => computeAnalysisEtaSeconds(sessionsSnapshot, { currentAnalysisType }),
    [sessionsSnapshot, currentAnalysisType],
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

  // 删除当前训练计划（0913 拍板）：计划由 Coach 生成可随时重排，直接删不弹
  // 确认；无论成败都重拉一次——失败时面板原样保留，删除成功则落 no_current_plan
  // 空态（「让 Coach 安排」接管）。
  const handleDeleteTrainingPlan = useCallback(async () => {
    try {
      await deleteCurrentTraining();
    } finally {
      await refreshCurrentTraining();
    }
  }, [refreshCurrentTraining]);

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

  // 向 AppShell 上报回合活跃状态：分析完成自动开讲据此在回合进行中让路
  // （分析由当前回合自行讲述，再开讲等于同一分析问两遍）。
  useEffect(() => {
    onActiveRunChange?.(run !== null && ["queued", "running"].includes(run.status));
  }, [onActiveRunChange, run]);

  // 向 AppShell 上报当前会话的 assistant 讲解文本：视频面板底部回看 chips
  // 跟随正文 @time（拍板）。只在内容变化时上报，避免刷新轮询引起无谓重渲染。
  const assistantTexts = useMemo(
    () => messages.filter((message) => message.role === "assistant").map((message) => message.content),
    [messages],
  );
  useEffect(() => {
    onCoachMessagesChange?.(assistantTexts);
  }, [onCoachMessagesChange, assistantTexts]);

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
      // 收敛目标会话键：await 期间不能再用 cancelled 判活——终态 setRun 会让
      // 本 effect cleanup（liveRunRef 变 null）把 cancelled 置真，若据此跳过
      // settle，成功回合的归档/残影清除/落库接管就全部失效（回复双份回归）。
      // 只有会话键真正变化（用户切走）才需要放弃写入，避免污染新会话。
      const settleKey = activeSessionKeyRef.current;
      try {
        const next = await fetchRun();
        if (cancelled) return;
        setRun(next);
        if (!["queued", "running"].includes(next.status)) {
          // run 终态：消息与自动命名所需数据均已落盘，通知 AppShell 刷新侧栏
          //（标题由命名钩子异步落库，AppShell 见 title_pending 会再补刷一次）。
          window.dispatchEvent(new CustomEvent(COACH_SESSION_UPDATED_EVENT));
        }
        await Promise.all([refresh(), refreshCurrentTraining()]);
        // 刷新期间用户可能已切走会话：此时 settleSucceeded 会用新会话键归档并
        // 清掉新会话的活跃流，必须复查。但 cleanup 因终态 setRun 触发的
        // cancelled=true 不代表切会话，故以会话键是否变化为准。
        if (activeSessionKeyRef.current !== settleKey) return;
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
        // 同 finalizeRun：await 期间用会话键而非 cancelled 判活（终态 setRun
        // 触发的 cleanup 会置 cancelled=true，但那不是切会话）。
        const settleKey = activeSessionKeyRef.current;
        try {
          const next = await fetchRun();
          if (cancelled) return;
          pollFailures = 0;
          setRun(next);
          if (["queued", "running"].includes(next.status)) {
            schedulePoll();
          } else {
            window.dispatchEvent(new CustomEvent(COACH_SESSION_UPDATED_EVENT));
            await Promise.all([refresh(), refreshCurrentTraining()]);
            // 同 finalizeRun：以会话键是否变化判断是否切会话，避免切会话后把
            // 归档写到新会话键；cleanup 触发的 cancelled 不阻止本会话 settle。
            if (activeSessionKeyRef.current !== settleKey) return;
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
      分析类长任务注入本机历史 ETA。run 到达终态后残留的流式思考段/活动
      工具步就地终结——失败路径没有 settleSucceeded 的归档清场，不冻结的话
      「思考中」扫光与经过计时会永远挂在错误卡片上方（0.1.12 真机修复）。 */
  const workSegments = useMemo(() => {
    const source = liveSegments.length > 0 ? liveSegments : deriveWorkSegments(run);
    const terminal = run != null && !["queued", "running"].includes(run.status);
    return (terminal ? settleTerminalWorkSegments(source, Date.now()) : source).map((segment) =>
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
  // 浮层仍锚定消息内容区（absolute 相对滚动内容，滚动即收起、不做跟随重算）。
  // 坐标取消息区（currentTarget）当前视口位置——它随面板滚动一起移动，
  // `rect - hostRect` 已是内容坐标；再加 scrollTop/scrollLeft 会把滚动量
  // 双算进定位，浮层随滚动量向下偏出屏幕（0.1.12 修复）。
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
    const rawLeft = rect.cx - hostRect.left;
    const clampedLeft = Math.min(
      Math.max(rawLeft, Math.min(SELECTION_TOOLBAR_HALF_WIDTH, hostRect.width / 2)),
      Math.max(hostRect.width - SELECTION_TOOLBAR_HALF_WIDTH, SELECTION_TOOLBAR_HALF_WIDTH),
    );
    const localTop = rect.top - hostRect.top;
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

  /** 删除单条引用块（整块删除，文字锁定不可编辑）：先播退场再真正移除。 */
  const { exitingKeys: exitingQuoteIds, requestExit: removeQuote, finalize: finalizeQuoteRemoval } =
    useExitAnimation<number>((id) => setQuotes((current) => current.filter((item) => item.id !== id)), 220);

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

  // 空态主行动：只把提示写进 composer 并聚焦，不自动发送（用户仍可改）。
  const requestTrainingPlan = () => {
    setDraft("帮我安排一个训练计划");
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const startTrainingScenario = async (item: CurrentTrainingItemV1) => {
    const scenarioName = item.display_name;
    if (!scenarioName) {
      notify("该训练项目暂时没有本机可启动的 KovaaK 场景");
      return;
    }
    setLaunchingScenarioRef(scenarioName);
    try {
      const result = await openKovaakScenario(scenarioName);
      notify(result.message);
    } catch {
      notify("未能请求打开 KovaaK，请稍后重试");
    } finally {
      setLaunchingScenarioRef(null);
    }
  };

  const renderTrainingLaunch = (item: CurrentTrainingItemV1) => {
    if (!item.display_name || item.scenario_availability !== "available") {
      // 不可一键开始时保持安静（1.0.0 内测拍板：不可用标签是噪音）：
      // Coach 生成计划不带 reviewed 场景 ref，这个状态对它是常态而非异常。
      return null;
    }
    return (
      <Button
        disabled={launchingScenarioRef !== null}
        onClick={() => void startTrainingScenario(item)}
        size="compact"
        variant="primary"
      >
        {launchingScenarioRef === item.display_name ? "正在打开…" : "在 KovaaK 中开始"}
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
            : /* 计划在但投影不出条目：兜底显示，绝不让 chip 整个消失 */
              currentTraining
              ? "训练计划"
              : null;
  // noCurrentPlan 也要可展开：点了只开空面板等于没有反馈（0911 审计 §四.6）。
  const trainingExpandable = Boolean(summaryItem) || (currentTrainingError && !currentTraining) || trainingUnavailable || noCurrentPlan;

  // 0913 拍板：展开时头部行改泛称「当前训练」，面板里的粗体名是唯一标题，
  // 名字不再出现两次；折叠态 chip 照旧显示条目名（一眼可见当前训练）。
  const trainingHeaderLabel = trainingExpanded && summaryItem ? "当前训练" : trainingChipLabel;

  // 折叠态头部天然宽写进容器 CSS 变量：max-content 不可过渡，量出标签宽 +
  // chip 水平 padding + gap + caret 后按像素插值。文字变化（换场景/空态）重测；
  // chip 会随 homeShell/能力分支整体挂载卸载，deps 不变时 layout effect 不会
  // 重跑——所以量测同时挂在 pop 的 callback ref 上，节点一挂载就量。
  // 上限 150px（0913 拍板）：胶囊悬在正文右上，宽了滚动时行尾会从它底下
  // 穿过被盖住；超限就地渐隐截断（data-truncated 驱动 CSS mask），不打底省略号。
  const measureTrainingPopWidth = useCallback(() => {
    const pop = trainingPopRef.current;
    const chip = trainingChipRef.current;
    const label = trainingChipLabelRef.current;
    if (!pop || !chip || !label) return;
    const chipStyle = window.getComputedStyle(chip);
    const padding = parseFloat(chipStyle.paddingLeft) + parseFloat(chipStyle.paddingRight);
    const gap = parseFloat(chipStyle.columnGap) || 0;
    const popBorder = parseFloat(window.getComputedStyle(pop).borderLeftWidth) * 2;
    const caret = trainingExpandable ? 16 : 0;
    const suffix = trainingExpandable ? gap + caret : 0;
    const maxLabel = Math.max(0, 150 - padding - suffix - popBorder);
    const truncated = label.scrollWidth > maxLabel;
    const labelWidth = Math.min(label.scrollWidth, maxLabel);
    pop.dataset.truncated = truncated ? "true" : "false";
    pop.style.setProperty("--pop-folded-w", `${Math.ceil(labelWidth + padding + suffix + popBorder)}px`);
  }, [trainingExpandable]);

  const setTrainingPopRef = useCallback((node: HTMLDivElement | null) => {
    trainingPopRef.current = node;
    if (node) measureTrainingPopWidth();
  }, [measureTrainingPopWidth]);

  useLayoutEffect(() => {
    measureTrainingPopWidth();
  }, [measureTrainingPopWidth, trainingHeaderLabel]);

  const trainingReveal = (
    <div
      aria-hidden={!trainingExpanded || undefined}
      className="task6-training-reveal"
      data-state={trainingPresence.state}
      inert={!trainingExpanded || undefined}
    >
      <div className="task6-training-reveal-inner">
        {currentTrainingError && !currentTraining ? (
          <p className="task6-training-pop-note">训练计划暂时读不出来，稍后再试。</p>
        ) : null}
        {trainingUnavailable ? (
          <p className="task6-training-pop-note">本地训练摘要暂时无法读取，稍后再试。</p>
        ) : null}
        {noCurrentPlan && !summaryItem ? (
          <div className="task6-training-pop-empty">
            <p>还没有训练安排，让 Coach 按你的弱点排一个。</p>
            <Button onClick={requestTrainingPlan} size="compact" variant="primary">让 Coach 安排</Button>
          </div>
        ) : null}
        {summaryItem ? (
          <section aria-label="当前训练计划" className="task6-training-details">
            <div className="task6-current-training-scenario">
              <strong>{summaryItem.display_name ?? "未命名项目"}</strong>
            </div>
            {/* 面板只讲用户要执行的三件事（1.0.0 内测反馈：注意/观察是
                Coach 的执行细节，不该进面板；整段文字用两行截断防刷屏）。 */}
            <dl className="task6-training-kv">
              <dt>练什么</dt>
              <dd title={summaryItem.practice_condition ?? undefined}>{summaryItem.practice_condition ?? "暂未说明"}</dd>
              <dt>练多少</dt>
              <dd title={summaryItem.dose_guardrail ?? undefined}>{summaryItem.dose_guardrail ?? "暂未说明"}</dd>
              <dt>复测</dt>
              <dd title={summaryItem.retest ?? undefined}>{summaryItem.retest ?? "暂未说明"}</dd>
            </dl>
            {/* 多条目计划才补一行式清单；单条目时 details 已经讲完，不再重复。 */}
            {visibleTrainingItems.length > 1 ? (
              <div className="task6-training-list">
                {visibleTrainingItems
                  .filter((item) => item !== summaryItem)
                  .map((item, index) => (
                    <div className="task6-training-item-row" key={`${item.display_name ?? "item"}-${index}`}>
                      <strong>{item.display_name ?? "未命名项目"}</strong>
                      <Status tone={item.status === "completed" ? "success" : item.status === "cancelled" ? "warning" : "neutral"}>{trainingStatusLabel(item.status)}</Status>
                    </div>
                  ))}
              </div>
            ) : null}
            <div className="task6-training-item-actions">
              {renderTrainingLaunch(summaryItem)}
              <Button disabled={capability !== "ready" || !summaryItem.display_name} onClick={() => writeTrainingQuestion(summaryItem)} size="compact" variant="secondary">问 Coach</Button>
              <Button disabled={capability !== "ready"} onClick={handleDeleteTrainingPlan} size="compact" variant="ghost">删除训练计划</Button>
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );

  // 单容器胶囊↔面板：头部行（原 chip 按钮）常驻，面板体在下方法定塌缩/
  // 展开；容器自己的宽度/背景/圆角连续形变，不再是"卡片从胶囊下面冒出来"。
  const trainingChip = trainingChipLabel === null ? null : (
    <div
      className="task6-training-pop"
      data-open={trainingExpanded || undefined}
      ref={setTrainingPopRef}
    >
      <button
        aria-expanded={trainingExpandable ? trainingExpanded : undefined}
        aria-label="当前训练计划"
        className="task6-training-chip"
        onClick={() => setTrainingExpanded((expanded) => !expanded)}
        ref={trainingChipRef}
        title={trainingChipLabel}
        type="button"
      >
        <span className="task6-training-chip-label" ref={trainingChipLabelRef}>{trainingHeaderLabel}</span>
        {trainingExpandable ? <IconChevronDown className="task6-training-chip-caret" /> : null}
      </button>
      {trainingReveal}
    </div>
  );

  // ── 发送/队列编排（digests §11 批 5）───────────────────────────────────

  const pushSentHistory = useCallback((text: string) => {
    sentHistoryRef.current = [...sentHistoryRef.current, text].slice(-50);
    historyIndexRef.current = null;
    historyBackupRef.current = null;
  }, []);

  /** 队列 chips 入列：前端权威态，逐条可视可编辑可删（丢消息教训）。 */
  const enqueueQueuedItem = useCallback((text: string, refs?: string[]) => {
    const content = text.trim();
    if (!content) return;
    chipSeqRef.current += 1;
    const id = chipSeqRef.current;
    setQueuedChips((chips) => [...chips, { id, text: content, ...(refs?.length ? { refs } : {}) }]);
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
   * 乐观发送的失败回滚：气泡撤下；草稿/引用按守卫回填——等待期用户已重新
   * 输入/挂引用则保留新值，不用旧内容覆盖。
   */
  const rollbackOptimisticSend = (optimisticId: number, content: string, quotesSnapshot: CoachQuote[], refsSnapshot: MentionRefChip[]) => {
    setMessages((current) => current.filter((message) => message.id !== optimisticId));
    setDraft((current) => (current.trim() ? current : content));
    setQuotes((current) => (current.length ? current : quotesSnapshot));
    setMentionRefs((current) => (current.length ? current : refsSnapshot));
  };

  /**
   * 实际发送，成功受理返回 true。
   * 运行中且非 force 一律转可见队列 chips——正式编排取代批 1 的 notify 止血，
   * 击键绝不凭空消失。force 仅供「打断并转向」等显式编排动作使用。
   * 乐观 UI（点点 09-08 拍板）：气泡上屏/清框/消费引用全部在任何 await 之前，
   * 新会话首条的 ensureSession 串行链不再阻塞点击反馈；失败由
   * rollbackOptimisticSend 整体回滚（引用快照恢复＝失败后引用仍在 composer）。
   */
  const sendText = async (
    contentRaw: string,
    opts: { force?: boolean; refs?: string[] } = {},
  ): Promise<boolean> => {
    const content = contentRaw.trim();
    if (!content || sendingRef.current) return false;
    // 结构化引用：chip 路径显式带 refs；普通路径消费当前挂着的引用 chips。
    const refs = opts.refs ?? mentionRefsRef.current.map((ref) => ref.token);
    const refsSnapshot = mentionRefsRef.current;
    const active = activeRunRef.current;
    if (!opts.force && active && ["queued", "running"].includes(active.status)) {
      // 已受理即清空输入：文本已移入 chip（不是复制），需要改写时走回填编辑。
      // 引用块已随拼装串进入 chip（拍板②降级为纯文本），待拼装引用一并消费。
      enqueueQueuedItem(content, refs.length ? refs : undefined);
      setDraft("");
      setQuotes([]);
      if (!opts.refs) setMentionRefs([]);
      clearComposerDraftStorage();
      notify("当前回复仍在生成中，这条已加入输入框上方队列，可随时取消、编辑或立即打断发送。");
      return true;
    }
    // 同步重入锁：必须在任何 await 之前置位，重入直接丢弃。
    sendingRef.current = true;
    // 工作流死窗占位：从这一刻到 setRun(created) 之间也要有"等待开始"。
    // 占位步带 startedAtMs，经过计时从点击一刻走表（0912 审计：等待期
    // 只有静态文字，用户对已经等了多久毫无感知）。
    pendingRunStartAtRef.current = Date.now();
    setPendingRunStart(true);
    // 新回合开始：清掉本会话上一回合的归档摘要与思考流残留。
    clearArchivedTurn();
    clearThinkingStream();
    // 乐观上屏（点击后的第一个可见反馈，必须在 ensureSession 之前）：
    // 拼装串已进入消息，引用块此刻消费，失败时由回滚恢复快照。
    const optimisticId = appendOptimisticUserMessage(content);
    const quotesSnapshot = quotes;
    setDraft("");
    setQuotes([]);
    if (!opts.refs) setMentionRefs([]);
    clearComposerDraftStorage();
    stickToBottomRef.current = true;
    pushSentHistory(content);
    try {
      const effectiveSessionId = sessionId ?? (onEnsureSession ? await onEnsureSession() : null);
      if (sessionId === null && onEnsureSession && effectiveSessionId === null) {
        setPendingRunStart(false);
        rollbackOptimisticSend(optimisticId, content, quotesSnapshot, refsSnapshot);
        notify("未能创建会话，草稿已保留，请重试。");
        return false;
      }
      const created = await createCoachAgentRun(
        content,
        {
          ...(effectiveSessionId == null ? {} : { sessionId: effectiveSessionId }),
          ...(refs.length ? { contextRefs: refs } : {}),
        },
      );
      setPendingRunStart(false);
      setRun(created);
      // 侧栏刷新改由 run 终态（finalizeRun/轮询终态分支）派发——此处派发时
      // 用户消息尚未落盘，刷新扑空，是新会话一直显示"新对话"的竞态根源。
      return true;
    } catch (error) {
      setPendingRunStart(false);
      rollbackOptimisticSend(optimisticId, content, quotesSnapshot, refsSnapshot);
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
    // 空对话首页的首条发送：先记下居中 composer 的位置交给过渡动画
    //（气泡飞升＋composer 滑落），再走常规乐观上屏。
    beginHomeExit(content);
    setMentionQuery(null);
    void sendText(content);
  };

  /** 队列 chip 上浮：能转则立即 steer 并展示乐观气泡，不能转走可恢复分支。 */
  /**
   * 排队 chip 的「立即」（0911 点点拍板：立刻打断发送）：停止当前生成并
   * 立即发出这条排队消息——与旧「上浮立即转向（不打断注入）」不同，语义
   * 是打断。stop 收敛终态后 force 发送，绕过运行守卫；发送失败 chip 留守。
   */
  const promoteChipToSendNow = async (chip: QueuedChip) => {
    const active = activeRunRef.current;
    if (!active || !["queued", "running"].includes(active.status)) {
      if (await sendText(chip.text, { refs: chip.refs })) setQueuedChips((chips) => removeQueuedChip(chips, chip.id));
      return;
    }
    try {
      setRun(await stopCoachAgentRun(active.run_ref, sessionId == null ? {} : { sessionId }));
    } catch {
      notify("未能停止当前生成，请重试。");
      return;
    }
    // 打断的回复落库后刷新会话消息（fire-and-forget，不阻塞下面的立即发送）。
    void refresh().catch(() => {});
    const accepted = await sendText(chip.text, { force: true, refs: chip.refs });
    if (accepted) setQueuedChips((chips) => removeQueuedChip(chips, chip.id));
  };

  const backfillChipToDraft = (chip: QueuedChip) => {
    setQueuedChips((chips) => removeQueuedChip(chips, chip.id));
    setDraft(chip.text);
    if (chip.refs?.length) {
      // 随 chip 排队的引用放回引用 chips（去重）。
      setMentionRefs((current) => {
        const known = new Set(current.map((ref) => ref.token));
        const restored = chip.refs!
          .filter((token) => !known.has(token))
          .map((token) => {
            const label = mentionCandidates.find((candidate) => candidate.token === token)?.label
              ?? `分析 #${token.slice("analysis:".length)}`;
            return { token, label };
          });
        return [...current, ...restored];
      });
    }
    setMentionQuery(null);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
    });
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
    void sendText(first.text, { refs: first.refs }).then((accepted) => {
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

  // ── 空对话首页（点点 0910 拍板）＋首条发送过渡 ─────────────────────────
  // 首条消息从居中首页发出时：消息以 user 气泡形态从输入框位置飞向消息区、
  // composer 同步滑落底部（FLIP，WAAPI 驱动）；系统「减少动态效果」开启时
  // 全部跳切到终态。
  const [homeExit, setHomeExit] = useState<null | { composerRect: DOMRect; text: string }>(null);
  // run 创建中的死窗占位（0910 点点报"工作态有点问题"）：ensureSession 串行链
  // 加 createCoachAgentRun 往返期间 run 还是 null，工作流整块不渲染，UI 假死。
  const [pendingRunStart, setPendingRunStart] = useState(false);
  // 占位步的计时起点：点击发送一刻（ElapsedTicker 据此每秒走表）。
  const pendingRunStartAtRef = useRef(Date.now());
  const homeComposerRef = useRef<HTMLDivElement | null>(null);
  const footerComposerRef = useRef<HTMLElement | null>(null);
  const homeFlyRef = useRef<HTMLDivElement | null>(null);
  const homeExitTimerRef = useRef<number | null>(null);
  const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  useLayoutEffect(() => {
    if (!homeExit) return;
    const finish = () => {
      homeExitTimerRef.current = null;
      textareaRef.current?.focus();
      setHomeExit(null);
    };
    // 布局 effect 在首帧绘制前完成定位与起动画——旧版 effect+双 rAF 会让
    // 首帧先画出已落底的 composer 再跳回起点，正是"闪"的来源（0910 点点报）。
    const fly = homeFlyRef.current;
    const footer = footerComposerRef.current;
    if (!fly || !footer || prefersReducedMotion()) {
      finish();
      return;
    }
    const easing = "cubic-bezier(0.2, 0.7, 0.2, 1)";
    const from = homeExit.composerRect;
    const target = messagesRef.current?.querySelector<HTMLElement>('.task6-message-entry[data-role="user"]');
    if (target) {
      const to = target.getBoundingClientRect();
      // 飞行气泡以目标气泡的宽度与位置起形，只位移不改形（文字不拉伸）；
      // fill forwards 压过 data-home-fly 的隐藏基态，落定后由卸载接管。
      fly.style.left = `${to.left}px`;
      fly.style.top = `${to.top}px`;
      fly.style.width = `${to.width}px`;
      const dx = from.left + from.width / 2 - (to.left + to.width / 2);
      const dy = from.top + from.height / 2 - (to.top + to.height / 2);
      fly.animate(
        [
          { transform: `translate(${dx}px, ${dy}px)`, opacity: 1 },
          { transform: "translate(0px, 0px)", opacity: 1 },
        ],
        { duration: 340, easing, fill: "forwards" },
      );
    }
    const drop = from.top - footer.getBoundingClientRect().top;
    if (Math.abs(drop) > 4) {
      footer.animate(
        [
          { transform: `translateY(${drop}px)`, opacity: 1 },
          { transform: "translateY(0px)", opacity: 1 },
        ],
        { duration: 300, easing, fill: "forwards" },
      );
    }
    homeExitTimerRef.current = window.setTimeout(finish, 380);
    return () => {
      if (homeExitTimerRef.current !== null) window.clearTimeout(homeExitTimerRef.current);
    };
    // deps 仅 homeExit：textareaRef/messagesRef 都是稳定 ref。
  }, [homeExit]);

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
        // 分析候选按 id 倒序＝最新的局排最前（菜单只展示前 8 条）；标签带
        // 场景名+对局时间——只给场景名分不清是哪一局（0911 点点）。挂载中的
        // 主题、进行中的分析、最近完成的分析（新对话首页也有得引用）全并进来。
        analysisIds: [
          ...new Set([
            ...discussionAnalysisIds,
            ...pendingAnalyses.map((analysis) => analysis.id),
            ...sessionsSnapshot
              .filter((item) => item.status === "done")
              .sort((a, b) => b.id - a.id)
              .slice(0, 8)
              .map((item) => item.id),
          ]),
        ].sort((a, b) => b - a),
        analysisLabels: Object.fromEntries(
          sessionsSnapshot.map((item) => {
            const scenario = item.scenario ?? `分析 #${item.id}`;
            const when = item.training_at ?? item.created_at ?? null;
            return [item.id, when ? `${scenario} · ${formatHistoryDate(when)}` : scenario];
          }),
        ),
        scenarioNames: [
          ...(currentTraining?.items.map((item) => item.display_name) ?? []),
          ...sessionsSnapshot.map((session) => session.scenario),
        ],
      }),
    [discussionAnalysisIds, pendingAnalyses, sessionsSnapshot, currentTraining],
  );
  const filteredMentionCandidates = useMemo(
    () => filterMentionCandidates(mentionCandidates, mentionQuery ?? ""),
    [mentionCandidates, mentionQuery],
  );
  const mentionOpen = mentionQuery !== null && filteredMentionCandidates.length > 0;

  // 结构化分析引用（0911 点点）：菜单选中分析＝挂到这组引用上，随发送作为
  // context_refs 结构化传给后端钉主题——消息文本与界面都不出现 analysis:N
  // 机器码；用户看到的是「场景 · 对局时间」chip。
  const [mentionRefs, setMentionRefs] = useState<MentionRefChip[]>([]);
  const mentionRefsRef = useRef<MentionRefChip[]>([]);
  mentionRefsRef.current = mentionRefs;

  /** 移除 @ 引用 chip：同样先播退场再真正从 state 摘除。 */
  const { exitingKeys: exitingMentionTokens, requestExit: removeMentionRef, finalize: finalizeMentionRefRemoval } =
    useExitAnimation<string>(
      (token) => setMentionRefs((refs) => refs.filter((item) => item.token !== token)),
      220,
    );

  const selectMentionCandidate = (candidate: MentionCandidate) => {
    const el = textareaRef.current;
    const caret = el?.selectionStart ?? mentionCaretRef.current ?? draft.length;
    // 手打 @ 的流：光标前有未消费的 @ 片段，选中后要把它摘掉/替换。
    const typedFragmentAt = draft.slice(0, caret).lastIndexOf("@");
    const hasTypedFragment = typedFragmentAt >= 0 && activeMentionQuery(draft, caret) !== null;

    if (candidate.token.startsWith("analysis:")) {
      // 分析候选＝挂结构化引用，不改草稿文本（机器码对用户不可见）。
      setMentionRefs((refs) =>
        refs.some((ref) => ref.token === candidate.token)
          ? refs
          : [...refs, { token: candidate.token, label: candidate.label }],
      );
      if (hasTypedFragment) {
        setDraft(draft.slice(0, typedFragmentAt) + draft.slice(caret));
        requestAnimationFrame(() => {
          el?.focus();
          el?.setSelectionRange(typedFragmentAt, typedFragmentAt);
        });
      }
      setMentionQuery(null);
      return;
    }
    if (hasTypedFragment) {
      // 场景名替换 @ 片段并 token 化（尾随空格防再触发）。
      const next = applyMentionSelection(draft, caret, candidate.token);
      setDraft(next.text);
      requestAnimationFrame(() => {
        el?.focus();
        el?.setSelectionRange(next.caret, next.caret);
      });
    } else {
      // 加号菜单路径（无 @ 片段）：场景名直接插入光标处。
      const nextCaret = caret + candidate.token.length + 1;
      setDraft(`${draft.slice(0, caret)}${candidate.token} ${draft.slice(caret)}`);
      requestAnimationFrame(() => {
        el?.focus();
        el?.setSelectionRange(nextCaret, nextCaret);
      });
    }
    setMentionQuery(null);
  };

  // 外点收起：@ 菜单开着的任何时刻，按下菜单以外的地方（含输入框/页面其余
  // 部分）即收起；加号按钮除外——它自己走开/关 toggle（0911 点点：持续吸焦）。
  useEffect(() => {
    if (!mentionOpen) return undefined;
    const onDocMouseDown = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(".task6-mention-menu")) return;
      if (target?.closest(".task6-composer-mention")) return;
      setMentionQuery(null);
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [mentionOpen]);

  /**
   * 错误卡「重试」（0910 点点报"点了不会重试"）：优先走服务端重试（原样重跑
   * 回合，不重复用户消息）。服务端 409/404 时不许哑掉——409 先与 服务端状态
   * 对齐（仍在跑＝恢复直播；已完成＝拉取落库消息接管对话；确实不可重试＝
   * 明说），404（run 已随服务重启丢失）＝把最后一条用户问题放回输入框。
   * runRef 可由错误卡快照传入（0912）：卡片比 run 对象活得久，run 被清后
   * 重试仍要能落到正确的回合上。
   */
  const retry = async (runRefOverride?: string) => {
    const runRef = runRefOverride ?? run?.run_ref;
    if (!runRef) return;
    try {
      setRun(await retryCoachAgentRun(runRef, sessionId == null ? {} : { sessionId }));
      return;
    } catch (error) {
      const name = error instanceof Error ? error.name : "";
      if (name !== "ApiError_409" && name !== "ApiError_404") {
        notify("重试未能开始，请稍后再试。");
        return;
      }
      if (name === "ApiError_409") {
        try {
          const resynced = await getCoachAgentRun(runRef, sessionId == null ? {} : { sessionId });
          if (["queued", "running"].includes(resynced.status)) {
            setRun(resynced); // 服务端仍在跑：恢复直播/轮询，什么也不用重做。
            return;
          }
          if (resynced.status === "succeeded") {
            setRun(null); // 回复其实已落库：解除错误卡，拉取消息接管对话。
            setFailedCard(null);
            void refresh();
            notify("回复已完成，已为你载入。");
            return;
          }
          setRun(resynced);
          notify("这个回合无法重试，请重新描述你的问题。");
          return;
        } catch {
          // 状态拿不到＝run 已丢失，走下方放回输入框兜底。
        }
      }
    }
    // run 已丢失（如本地服务重启）：解除错误卡，把问题放回输入框。
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    if (lastUser) setDraft(lastUser.content);
    setRun(null);
    setFailedCard(null);
    void refresh();
    notify("原回合已丢失，你的问题已放回输入框，确认后即可重发。");
  };

  const stop = async () => {
    if (!run) return;
    try {
      setRun(await stopCoachAgentRun(run.run_ref, sessionId == null ? {} : { sessionId }));
      // 被打断的半截回复此刻才随终态落库：补一次会话刷新让它在屏幕上出现，
      // 否则要等下一回合终态才回来（可达分钟级）。
      void refresh().catch(() => {});
    } catch {
      notify("未能停止生成，请重试。");
    }
  };

  const headerState = capability === "loading"
    ? { state: "neutral", label: "正在读取" }
    : capability === "ready"
      ? { state: "success", label: "可用" }
      : capability === "unavailable"
        ? { state: "error", label: capabilityLabel(capability) }
        : { state: "warning", label: capabilityLabel(capability) };

  // ── 空对话首页（点点 0910 拍板）─────────────────────────────────────────
  const homeGreeting = coachGreeting(new Date());
  const homeChips = useMemo(() => coachHomeChips(), []);
  // 纯空对话（无消息、无 run、非过渡帧）才显示首页；发送首条后由常规消息流接管。
  const homeMode = messages.length === 0 && !run && !homeExit;
  // 空对话首页壳层（点点 0910 拍板）：header 状态行与"本次讨论"条是上次会话的
  // 上下文残留，空对话时连同过渡帧一起不渲染，首页只留居中 hero；异常分支
  // （loading/不可用/加载失败）不在本条件内，状态提示照常显示。
  const homeShell = messages.length === 0 && !run;
  useEffect(() => {
    onHomeShellChange?.(homeShell);
  }, [homeShell, onHomeShellChange, messages.length, run, homeExit]);
  // v6 四轮：讨论条 portal 挂载点解析。顶栏（含 task3-coach-topbar-slot）由
  // homeShell 上报经 AppShell 渲染，槽位 DOM 总是晚本面板一帧出现——render
  // 期间查询永远落空，改为每次提交后在 DOM 解析一次（无依赖 layout effect）；
  // 解析到才挂 portal，首页（homeShell）或槽位缺失时保持 null 不渲染。
  const [discussionBarHost, setDiscussionBarHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (homeShell) {
      if (discussionBarHost !== null) setDiscussionBarHost(null);
      return;
    }
    const host = document.getElementById("task3-coach-topbar-slot");
    if (host !== discussionBarHost) setDiscussionBarHost(host);
  });
  // 顶栏行宽度变化（拖窗/缩放）触发重收敛；行＝slot 的父级（task3-coach-topbar）。
  useEffect(() => {
    if (discussionBarHost === null) return undefined;
    const row = discussionBarHost.parentElement;
    if (!row || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => setDiscussionRowWidthTick((tick) => tick + 1));
    observer.observe(row);
    return () => observer.disconnect();
  }, [discussionBarHost]);
  // 挤压反馈收敛（0918）：唯一所有者是本 layout effect——列表/标签键值变化
  // 时回到全平铺（键值记在 ref，不用 passive effect，否则与收敛 setState 跨
  // 阶段批处理互吃：reset 设回 3、collapse 减到 2 合并成一次 3→3 渲染，
  // deps 不再变化，溢出态永久卡死）。溢出就把最后一颗平铺 chip 整颗收进
  // ▾ 菜单并记录失败时的行宽；行宽比失败点宽出一档（24px 滞后防拖动抖动）
  // 才整体放宽到上限、让溢出分支自己走回收敛点。绘制前同步消化，用户
  // 看不到中间的溢出帧。
  const discussionCollapseKeysRef = useRef<string | null>(null);
  const discussionPinnedCap = Math.min(DISCUSSION_BAR_MAX_PINNED, discussionChips.length);
  useLayoutEffect(() => {
    const chips = discussionChipsRef.current;
    if (!chips) return;
    const row = discussionBarHost?.parentElement ?? null;
    const collapseKeys = `${discussionAnalysisKey}\u0000${discussionLabelsKey}`;
    if (discussionCollapseKeysRef.current !== collapseKeys) {
      discussionCollapseKeysRef.current = collapseKeys;
      if (discussionPinnedCount !== discussionPinnedCap) {
        setDiscussionPinnedCount(discussionPinnedCap);
        return;
      }
    }
    if (chips.scrollWidth - chips.clientWidth > 1) {
      if (discussionPinnedCount <= 0) return;
      if (row) discussionShrinkFailedRowWidthRef.current = row.clientWidth;
      setDiscussionPinnedCount(discussionPinnedCount - 1);
      return;
    }
    const failedAt = discussionShrinkFailedRowWidthRef.current;
    if (failedAt === null || row === null) return;
    if (row.clientWidth <= failedAt + 24) return;
    if (discussionPinnedCount >= discussionPinnedCap) return;
    discussionShrinkFailedRowWidthRef.current = null;
    setDiscussionPinnedCount(discussionPinnedCap);
  }, [discussionBarHost, discussionPinnedCount, discussionPinnedCap, discussionRowWidthTick, discussionAnalysisKey, discussionLabelsKey, pendingAnalyses.length]);
  const beginHomeExit = (text: string) => {
    if (messages.length !== 0 || run || prefersReducedMotion()) return;
    const rect = homeComposerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHomeExit({ composerRect: rect, text });
  };
  // 首页下半（chips＋提示）：正常帧与退出淡出帧共用。
  const homeTail = (
    <>
      <div aria-label="试试这样问" className="task6-home-chips" role="list">
        {homeChips.map((chip) => (
          <button
            className="task6-suggestion"
            key={chip.id}
            onClick={() => {
              // 拍板：只填入草稿，由用户自己修改后发送，不直发。
              setDraft(chip.prompt);
              requestAnimationFrame(() => textareaRef.current?.focus());
            }}
            role="listitem"
            title="填入输入框，可修改后再发送"
            type="button"
          >
            {chip.label}
          </button>
        ))}
      </div>
    </>
  );

  // composer 主体（队列 chips＋引用块＋输入卡）提取为单变量：空对话首页把整个
  // composer 搬进居中 hero（homeMode 时 footer 不渲染），发送时再落回 footer——
  // 两处按条件互斥渲染，任一时刻只有一份实例（#coach-draft 唯一）。
  const composerCore = (
    <>
      {/* 运行中队列 chips（item 1）：96 字符预览，逐条可立即打断发送/回填编辑/取消 */}
      {queuedChips.length > 0 ? (
        <div aria-label="待发送队列" className="task6-queue-chips" role="list">
          {queuedChips.map((chip) => (
            <div className="task6-queue-chip" key={chip.id} role="listitem">
              <span className="task6-queue-chip-text" title={chip.text}>{truncateQueuePreview(chip.text)}</span>
              <IconButton label="立即打断发送" onClick={() => void promoteChipToSendNow(chip)} size="compact" title="停止当前回复并立即发送这条">
                <IconChevronDown className="task6-icon-flip" />
                <span className="task6-queue-chip-now">立即</span>
              </IconButton>
              <IconButton label="回填编辑" onClick={() => backfillChipToDraft(chip)} size="compact" title="放回输入框编辑，排队条消失">
                <IconHistory />
              </IconButton>
              <IconButton label="取消发送" onClick={() => setQueuedChips((chips) => removeQueuedChip(chips, chip.id))} size="compact" title="删除这条排队消息">
                <IconClose />
              </IconButton>
            </div>
          ))}
        </div>
      ) : null}
      {/* 结构化分析引用 chips（引用菜单选中的分析）：人话标签呈现，发送时
          作为 context_refs 结构化挂载——文本里不出现 analysis:N（0911 点点）。 */}
      {mentionRefs.length > 0 ? (
        <div aria-label="已引用的分析" className="task6-queue-chips" data-mention-refs="true" role="list">
          {mentionRefs.map((ref) => (
            <div
              className="task6-queue-chip"
              data-exiting={exitingMentionTokens.includes(ref.token) || undefined}
              key={ref.token}
              onAnimationEnd={(event) => {
                if (event.animationName === "task6-quote-out") finalizeMentionRefRemoval(ref.token);
              }}
              role="listitem"
            >
              <span className="task6-queue-chip-text" title={ref.label}>{ref.label}</span>
              <IconButton label="移除这条引用" onClick={() => removeMentionRef(ref.token)} size="compact" title="移除引用">
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
            <blockquote
              className="task6-quote-block"
              data-exiting={exitingQuoteIds.includes(quote.id) || undefined}
              key={quote.id}
              onAnimationEnd={(event) => {
                if (event.animationName === "task6-quote-out") finalizeQuoteRemoval(quote.id);
              }}
              role="listitem"
            >
              <span className="task6-quote-head">引用 Coach</span>
              <p className="task6-quote-body" title={quote.text}>{quote.text}</p>
              <IconButton label="删除这条引用" onClick={() => removeQuote(quote.id)} size="compact" title="删除整块引用（文字不可编辑）">
                <IconClose />
              </IconButton>
            </blockquote>
          ))}
        </div>
      ) : null}
      <div className="task6-composer-input" ref={composerInputRef}>
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
                // Esc 关候选时把 @ 片段一并删掉：残留的 "@an" 让后续输入反复
                // 触发下拉，发送后也是一段死文本（0911 审计 §四.4）。
                const el = textareaRef.current;
                const caret = mentionCaretRef.current ?? el?.selectionStart ?? draft.length;
                const queryLength = (mentionQuery ?? "").length;
                const fragmentStart = Math.max(0, caret - queryLength - 1);
                if (draft.slice(fragmentStart, caret).includes("@")) {
                  const next = draft.slice(0, fragmentStart) + draft.slice(caret);
                  setDraft(next);
                  requestAnimationFrame(() => {
                    el?.focus();
                    el?.setSelectionRange(fragmentStart, fragmentStart);
                  });
                }
                setMentionQuery(null);
                return;
              }
            }
            // 运行中且输入框为空：Esc＝终止生成，与终止键同功能（0911 点点拍板）。
            if (event.key === "Escape" && composerBusy && !draft.trim() && quotes.length === 0) {
              event.preventDefault();
              void stop();
              return;
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
          placeholder={composerBusy
            ? "继续输入以排队后续修改"
            : sendBlockedByMember
              ? "向 Coach 提问…（发送暂不可用，历史照常可读）"
              : "向 Coach 提问，可以聊训练，也可以让它帮你操作应用…"}
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
                <strong>{candidate.label}</strong>
                {candidate.hint ? <small>{candidate.hint}</small> : null}
              </button>
            ))}
          </div>
        ) : null}
        {/* 右下角簇（0827 拍板 B1）：模型/力度选择与发送键同一行、发送键左侧，
            取代旧「输入卡下方工具行」；wrap 自带 position:relative 锚点，
            菜单仍向上弹出。@ 引用钮已移至输入卡左下角（0910 拍板，与发送键
            水平对称），不再占据角簇首位。 */}
        {/* 左下角 + 引用钮（点点 0910 拍板）：32px 圆形、弱色描边形态，与
            primary 橙发送键明确区分；点击＝在草稿尾部落一个真实的 @，
            整条 mention 管线（候选/选中/token 化）零新增。 */}
        <button
          aria-label="引用分析或场景"
          aria-expanded={mentionOpen || undefined}
          className="task6-composer-mention"
          onClick={() => {
            // 纯开关（0911 点点）：打开候选菜单不碰草稿——分析候选挂结构化
            // 引用（chip 呈现），场景候选才写入光标处文本。
            if (mentionCandidates.length === 0) {
              notify("还没有可引用的分析或场景；完成一次分析后就能在这里引用。");
              return;
            }
            setMentionQuery(mentionOpen ? null : "");
            requestAnimationFrame(() => textareaRef.current?.focus());
          }}
          title="引用一份分析或场景"
          type="button"
        ><IconPlus /></button>
        <div className="task6-composer-corner">
          <CoachModelMenu
            onError={(message) => notify(message)}
          />
          {/* 发送键随运行态变形（0911 点点拍板，取代 09-01 的「恒为提交」）：
              空闲＝发送；运行中且输入框为空＝终止键（点击/Esc 同功能）；
              运行中且已输入＝发送（点击/Enter 自动入队）。运行中不再旁挂
              caret 选项菜单（0912 点点拍板：排队由 Enter/点击自动入队与
              composer 上方队列 chips 承担，旁挂菜单形态废弃）。 */}
          <div className="task6-send-actions">
            {composerBusy && !draft.trim() && quotes.length === 0 ? (
              <button
                aria-label="停止生成"
                className="task6-composer-send task6-composer-send--stop"
                onClick={() => void stop()}
                title="停止生成（Esc 同功能）"
                type="button"
              ><IconStop /></button>
            ) : (
              <button
                aria-label="发送"
                className="task6-composer-send"
                disabled={!draft.trim() || sendBlockedByMember}
                onClick={submitComposer}
                title={draft.trim() || quotes.length === 0 ? undefined : "只有引用、没有正文时不能发送，请补充你的问题或要求"}
                type="button"
              ><IconSend /></button>
            )}
          </div>
        </div>
      </div>
    </>
  );

  // v6（0910 拍板）：旧 header（Aiming Coach/可用文字/训练 chip）由 AppShell
  // 的共用顶栏（task3-coach-topbar）取代；训练 chip 悬浮在面板交换区右上。
  const header = trainingChip ? (
    <div className="task6-coach-floating">{trainingChip}</div>
  ) : null;

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
    <div className="task6-coach-shell">
      {/* 训练 chip 悬浮胶囊（v6）：钉在面板交换区右上、顶栏之下，不随消息滚动；
          放在滚动容器外层以免被 overflow 裁剪。空对话首页不渲染。 */}
      {homeShell ? null : header}
      <div className="task6-coach-panel" ref={messagesRef} onScroll={handleMessagesScroll}>
      {/* 本次讨论的分析挂载条：进行中的分析以 pending chip 呈现（永远平铺，
          不参与折叠）；已完成的讨论 chip 常驻保留（0827 拍板），只平铺前 3
          个（0905 拍板防挤压），余量收进行尾 ▾ 下拉，点击项与平铺 chip 同
          一行为：打开视频讲解。空对话首页不渲染（homeShell 拍板）。
          v6 四轮：经 portal 挂入共用顶栏（AppShell 的 task3-coach-topbar-slot，
          标题之后），不再占用面板内 sticky 吸顶区。 */}
      {(!homeShell && (pendingAnalyses.length > 0 || discussionAnalysisIds.length > 0) && discussionBarHost != null) ? (
        createPortal(
          <div aria-label="本次讨论的分析" className="task6-discussion-bar task6-suggestions" ref={discussionBarRef} role="region">
            {/* 0918 防遮三键：chip 收进可收缩容器内裁剪，▾ 与下拉留在容器外，
                挤压时展开入口始终可见可点。宽度不足由上方收敛逻辑整颗收编，
                容器硬裁只是 pending 超长的兜底。 */}
            <div className="task6-discussion-chips" ref={discussionChipsRef}>
              {pendingAnalyses.map((item) => (
                <span
                  className="task6-discussion-chip"
                  data-pending="true"
                  key={`pending-${item.id}`}
                  title="分析完成后可点击打开视频"
                >
                  <span aria-hidden="true" className="task6-pulse-dot task6-chip-dot" />
                  <span className="task6-discussion-chip-label">
                    {item.scenario ?? `分析 #${item.id}`}{item.runId != null ? ` · run ${item.runId}` : ""}
                  </span>
                  <ElapsedTicker sinceMs={item.startedAtMs} />
                </span>
              ))}
              {pinnedDiscussionChips.map((chip) => (
                <button
                  className="task6-discussion-chip"
                  key={chip.id}
                  onClick={() => onOpenVideo?.(`analysis:${chip.id}`, 0)}
                  // 悬停给完整标题：挤压截断时唯一能认出是哪局的途径（菜单项同款全名）。
                  title={chip.label}
                  type="button"
                >
                  <span className="task6-discussion-chip-label">{chip.label}</span>
                </button>
              ))}
            </div>
            {overflowDiscussionChips.length > 0 ? (
              <button
                aria-expanded={discussionOverflowOpen}
                aria-haspopup="menu"
                aria-label={`展开其余 ${overflowDiscussionChips.length} 个讨论过的分析`}
                className="task6-discussion-chip task6-discussion-toggle"
                onClick={() => setDiscussionOverflowOpen((open) => !open)}
                title="展开其余讨论过的分析"
                type="button"
              >
                <IconChevronDown className="task6-discussion-caret" />
              </button>
            ) : null}
            {discussionOverflowOpen && overflowDiscussionChips.length > 0 ? (
              <div aria-label="更多讨论过的分析" className="task6-discussion-menu" role="menu">
                {overflowDiscussionChips.map((chip) => (
                  <button
                    className="task6-discussion-item"
                    key={chip.id}
                    onClick={() => {
                      setDiscussionOverflowOpen(false);
                      onOpenVideo?.(`analysis:${chip.id}`, 0);
                    }}
                    role="menuitem"
                    title="打开视频讲解"
                    type="button"
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>,
          discussionBarHost,
        )
      ) : null}

      <div className="task6-messages-wrap" data-home-fly={homeExit ? "true" : undefined}>
      <section
        aria-label="Coach 消息"
        className="task6-messages"
        onMouseUp={handleMessagesMouseUp}
      >
        {/* 顶部锚定（0827 拍板，回退底部锚定）：消息从上往下自然排布，短
            会话不再在头部留大空洞；空会话时同槽位换成「新对话首页」hero
            （问候＋居中 composer＋建议 chips＋能力提示，点点 0910 拍板），
            发出首条消息时由 homeExit 过渡帧接管（气泡飞升＋composer 滑落）。 */}
        {homeMode ? (
          <div className="task6-empty-hero">
            <div className="task6-home-greet">{homeGreeting}</div>
            <div className="task6-home-composer" ref={homeComposerRef}>{composerCore}</div>
            {homeTail}
          </div>
        ) : homeExit ? (
          /* 过渡帧：hero 骨架淡出让位（CSS 动画），飞行气泡由 WAAPI 驱动。 */
          <div aria-hidden="true" className="task6-empty-hero task6-home-leaving">
            <div className="task6-home-greet">{homeGreeting}</div>
            {homeTail}
          </div>
        ) : null}
        {homeExit ? <div className="task6-home-fly" ref={homeFlyRef}>{homeExit.text}</div> : null}
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
                  <>
                    {/* 受控富渲染（digests §10）：助手消息走 task7-rich 块结构，
                       不再套 <p>（表格/列表不能内嵌在段落里）。 */}
                    <CoachMessageText text={message.content} analysisRef={defaultAnalysisRef} onOpenVideo={onOpenVideo} />
                    {/* 被停止的半截回复如实标记：与「停止生成」的停止尾标一致，
                        打断并转向后旧回复不再伪装成完整回答（0911 审计 §12.5）。 */}
                    {message.stopped ? <span className="task6-message-stopped">回答已停止</span> : null}
                  </>
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
        {run ? (
          <CoachWorkStream segments={workSegments} stopped={run.status === "stopped"} />
        ) : pendingRunStart ? (
          /* run 创建中的死窗占位（0910）：与 queued 态"等待开始"同款视觉，
             落 run 后无缝换成真实事件流；经过计时从发送一刻走表（0912 审计）。 */
          <CoachWorkStream segments={[{
            kind: "tool",
            step: {
              key: "coach-pending-start",
              label: "等待开始",
              meta: null,
              state: "active",
              command: null,
              startedAtMs: pendingRunStartAtRef.current,
            },
          }]} />
        ) : null}
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
        {(() => {
          // 失败呈现二选一：run 本体仍在（failed）直接用 run；run 已被清
          // （终态竞态等）则用 failedCard 快照顶上——卡片绝不凭空消失。
          // 两种来源都按会话归属显示（run.session_id / failedCard.sessionId
          // 必须等于当前生效会话）：切到其他会话不显示别处的错误卡，空首页
          // 与删除残留的僵尸 run 也不得泄漏（0912 晚点点截图实锤）。
          const cardSessionId = sessionId ?? handoverSessionId;
          let error: null | { runRef: string; title: string; message: string; retryable: boolean } = null;
          if (run && run.status === "failed" && run.error && run.session_id === cardSessionId) {
            error = { runRef: run.run_ref, title: runErrorTitle(run.error), message: run.error.message, retryable: run.error.retryable };
          } else if (failedCard && cardSessionId != null && cardSessionId === failedCard.sessionId) {
            error = failedCard;
          }
          if (!error) return null;
          // 网关错误码分流（契约 §5.3，新旧值双认）：quota_exhausted → ④ 口径，
          // member_required → 未订阅引导，jwt_expired → 重登录引导（静默降级未登录）。
          const gatewayCode = classifyMemberGatewayError(error.message);
          const gateway = gatewayCode ? gatewayErrorNotice(gatewayCode) : null;
          return (
            <div className="task6-error-card" role="alert">
              <div className="task6-error-card-head">
                <div className="task6-error-card-title">{error.title}</div>
                <div className="task6-error-card-desc">
                  {gateway ? gateway.text : <>{error.message} 已生成的部分已保留；本地分析、历史和视频不受影响。</>}
                </div>
              </div>
              <div className="task6-error-card-actions">
                {gatewayCode === "jwt_expired" ? (
                  <Button onClick={() => router.push("/account")} size="compact" variant="secondary">重新登录</Button>
                ) : null}
                {error.retryable && !gateway ? (
                  <Button onClick={() => void retry(error.runRef)} size="compact" variant="secondary">重试</Button>
                ) : null}
                <Button onClick={() => { setFailedCard(null); setRun(null); }} size="compact" variant="ghost">稍后再说</Button>
              </div>
            </div>
          );
        })()}
        {/* 对话中不再常驻建议 chips（点点 0910 拍板）：开局引导由空对话首页
            三颗 chips 承担，对话进行中重复出现属干扰。 */}
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

      {/* ④/⑨/⑧ 页内提示（线框：只陈述事实 + 指路，零商业化）。一次性可关闭；
          关闭后状态由左下角 chip 常驻承载。发送键禁用由 sendBlockedByMember 控制。 */}
      {homeMode || !memberBlockNotice || memberNoticeDismissed ? null : (
        <MemberNotice
          onDismiss={() => setMemberNoticeDismissed(true)}
          tone={memberConnectionLost ? "error" : "warn"}
        >
          {memberBlockNotice}
        </MemberNotice>
      )}
      {homeMode || !memberSoftNotice || memberSoftNoticeDismissed ? null : (
        <MemberNotice
          onDismiss={() => setMemberSoftNoticeDismissed(true)}
          tone="warn"
        >
          {memberSoftNotice}
        </MemberNotice>
      )}

      {homeMode ? null : (
        <footer className="task6-composer" ref={footerComposerRef} data-home-drop={homeExit ? "true" : undefined}>
          {/* 未读提示（0828）：挂 composer 钉在其上沿之外，随悬浮输入框恒定可见。
              空对话首页（homeMode）时本 footer 不渲染——composer 已整体搬进
              居中 hero（composerCore），发送首条后随过渡落回此处。 */}
          {unreadCount > 0 ? (
            <button className="task6-unread-prompt" onClick={scrollToLatest} type="button">
              ↓ {unreadCount} 条新内容 · 回到底部
            </button>
          ) : null}
          {composerCore}
        </footer>
      )}

      {feedback ? (
        <Toast key={feedback.seq} onClose={() => setFeedback((current) => (current && current.seq === feedback.seq ? null : current))}>
          {feedback.text}
        </Toast>
      ) : null}
      </div>
    </div>
  );
}
