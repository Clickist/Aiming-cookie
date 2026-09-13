import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { appendFileSync } from "node:fs";

import {
  COACH_RUNTIME_TURN_SCHEMA,
  COACH_RUNTIME_TURN_SCHEMA_V1,
  failureResponse,
  isRecord,
  makeError,
  successResponse,
  type CoachReasoningEffort,
  type CoachRuntimeMessage,
  type CoachRuntimeProviderProfile,
  type CoachRuntimeTurnResponse,
  type CoachRuntimeTurnSchema,
  type CoachRuntimeToolEvent,
  type CoachRuntimeUsage,
} from "./contracts.ts";
import { skillsExecutionEnv } from "./skills-env.ts";
import { createProductCommandTool } from "./product-command-tools.ts";
import { resolveSystemPromptWithEnvFacts } from "./load-system-prompt.ts";
import {
  extractRuntimeSecrets,
  parseProviderProfile,
  ProviderProfileError,
  redactRuntimeSecrets,
} from "./provider-profile.ts";
import { resolveProviderModel, type PiModels, type ResolvedProviderModel } from "./provider-models.ts";
import { loadPiAgent, loadPiNodeEnv } from "./pi-source.ts";
import { getDataRoot } from "./app-data.ts";
import { createBashTool, createEditTool, createFindTool, createGrepTool, createLsTool, createReadTool, createWriteTool, explicitAnalysisRefsFromText, runScopedAnalysisReads, runScopedSkillReads } from "./fs-tools.ts";
import { createWebSearchTools } from "./web-search-native.ts";
import { extractMessageText } from "./session-repo.ts";
import { isIntroSession } from "./intro-session.ts";
import type { StreamFn } from "./stream-openai-compatible.ts";

// ── Types ────────────────────────────────────────────────────────────────

type ParsedRequest = {
  schema_version: CoachRuntimeTurnSchema;
  run_id: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  session_id?: string;
  system_prompt?: string;
  /** 结构化分析引用（前端引用菜单选择）：与消息文本里的 analysis:N 同效，
      但用户界面不再出现机器码（0911 点点）。 */
  context_refs?: string[];
  model: CoachRuntimeProviderProfile;
  tool_bridge?: import("./contracts.ts").CoachToolBridge;
};

type TurnOptions = {
  onPartial?: (partial: CoachPartialRevision) => Promise<void> | void;
  onActivity?: (activity: CoachActivityUpdate) => Promise<void> | void;
  onComplete?: (timing: CoachTurnTiming) => Promise<void> | void;
  /** Internal: persistent Coach session for this thread (agent-runs path). */
  session?: unknown;
  /** Test seam: inject a fake provider stream (e.g. streamSimple stub) without a network call. */
  streamFn?: StreamFn;
};

export type CoachPartialRevision = {
  revision: number;
  /** Null on thinking-only revisions (extended thinking streams before any text). */
  text: string | null;
  /** Live extended-thinking text (pi thinking_delta), full-replace like text. */
  thinking_text: string | null;
  elapsed_ms: number;
  provider_rounds: number;
};

export type CoachActivityUpdate = {
  sequence: number;
  kind: "thinking" | "tool" | "queue";
  state: "started" | "completed" | "failed" | "updated";
  /** thinking started 专用：上一轮思考段终文（full-replace）。恒带——null＝
     该轮无前置思考段；undefined＝旧版 sidecar（无分段协议）。 */
  thinking_text?: string | null;
  tool_call_id?: string;
  tool_name?: string;
  command_name?: string;
  /** coach_ui_event carried by product commands (e.g. video_time navigation). */
  ui_event?: Record<string, unknown>;
  /** Raw agent-core call arguments, JSON-summarized for the UI (pi-style tool blocks). */
  args_preview?: string;
  /** Raw agent-core result payload, JSON/text-summarized for the UI. */
  result_preview?: string;
  /** Wall time of the tool call in ms (present on completion). */
  duration_ms?: number;
  /** Live engine queue sizes on pi `queue_update`（digests §11 批 5 最小透传）。 */
  steer_count?: number;
  follow_up_count?: number;
  next_turn_count?: number;
};

export type CoachTurnTiming = {
  total_ms: number;
  first_provider_event_ms: number | null;
  first_text_delta_ms: number | null;
  first_safe_text_ms: number | null;
  provider_rounds: number;
  provider_ms: number;
  provider_round_ms: number[];
  tool_ms: number;
  repair_ms: number;
};

// ── Raw passthrough helpers ───────────────────────────────────────────────

/** Compact JSON/text summary of an agent-core value, capped for UI display. */
function summarizeForUi(value: unknown, limit: number): string | undefined {
  if (value === null || value === undefined) return undefined;
  let text: string;
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  if (!text.trim()) return undefined;
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

// ── Abort tracking ───────────────────────────────────────────────────────

/**
 * Live Pi harness queue hooks for a running turn (P3 Composer 排队/转向透传）。
 * 纯转发：enqueue 直映 pi AgentHarness.steer()/followUp()，setDrainMode 直映
 * setSteeringMode()/setFollowUpMode()（QueueMode "all" | "one-at-a-time"）。
 * harness 随 runCoachTurn 的 turn 结束销毁，队列态零持久化。
 */
type TurnQueueTarget = {
  enqueue: (kind: CoachQueueKind, text: string) => Promise<void>;
  setDrainMode: (kind: CoachQueueKind, mode: CoachDrainMode) => Promise<void>;
};

const activeTurns = new Map<string, { abort: () => void; queue?: TurnQueueTarget }>();
const stopRequested = new Set<string>();

export function stopCoachTurn(runId: string): boolean {
  const active = activeTurns.get(runId);
  if (!active) return false;
  stopRequested.add(runId);
  active.abort();
  return true;
}

// ── Composer queue passthrough (steer / follow-up) ───────────────────────

/**
 * Composer 排队的三条队列。next_turn 的排队缓冲在我们这层（见 queueTarget），
 * 排水用同一个 harness 连续 prompt()——pi 的 turn 循环负责把排队消息作为
 * 下一轮开头注入；不直接透传 harness.nextTurn() 的原因见 queueTarget 注释。
 */
export type CoachQueueKind = "steer" | "follow_up" | "next_turn";

/** pi AgentHarness QueueMode verbatim；不改名、不解释。 */
export type CoachDrainMode = "all" | "one-at-a-time";

export type CoachQueueRequest = {
  kind: CoachQueueKind;
  text: string;
  drain_mode?: CoachDrainMode;
};

export type CoachQueueResult =
  | { ok: true }
  | { ok: false; code: "turn_not_active" | string };

function engineErrorCode(error: unknown): string {
  if (error instanceof Error && typeof (error as { code?: unknown }).code === "string") {
    return `engine:${(error as { code: string }).code}`;
  }
  return "engine_error";
}

/**
 * Forward a queued message onto the live Pi harness of an active run.
 *
 * Pure passthrough with zero persistence: a steer lands in the engine's
 * steering queue (injected mid-run at the next drain point), a follow_up in
 * the follow-up queue (drained when the agent would otherwise stop). Returns
 * turn_not_active when no running harness is registered for the run — the
 * caller translates that into explicit HTTP semantics.
 */
export async function queueCoachTurnMessage(
  runId: string,
  request: CoachQueueRequest,
): Promise<CoachQueueResult> {
  const active = activeTurns.get(runId);
  const queue = active?.queue;
  if (!queue) return { ok: false, code: "turn_not_active" };
  try {
    if (request.drain_mode !== undefined) {
      await queue.setDrainMode(request.kind, request.drain_mode);
    }
    await queue.enqueue(request.kind, request.text);
    return { ok: true };
  } catch (error) {
    // e.g. harness already idle between registration and teardown: engine
    // AgentHarnessError("invalid_state") surfaces as an explicit code instead
    // of a 500.
    return { ok: false, code: engineErrorCode(error) };
  }
}

/**
 * 从最终 assistant 消息提取 provider usage（审计#20）。pi 的 Usage 结构字段
 * 缺失时映射为 null——0 是真实计量，null 才是"provider 没报"。cost 可能是
 * 分项对象（{input,output,...,total}），取其 total。
 */
export function extractUsage(message: unknown): CoachRuntimeUsage | null {
  if (!isRecord(message) || !isRecord(message.usage)) return null;
  const usage = message.usage;
  const hasAny = ["input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens", "cost"]
    .some((key) => usage[key] !== undefined);
  if (!hasAny) return null;
  const num = (value: unknown): number | null =>
    typeof value === "number" && Number.isFinite(value) ? value : null;
  return {
    input_tokens: num(usage.input),
    output_tokens: num(usage.output),
    cache_read_tokens: num(usage.cacheRead),
    cache_write_tokens: num(usage.cacheWrite),
    reasoning_tokens: num(usage.reasoning),
    total_tokens: num(usage.totalTokens),
    cost: typeof usage.cost === "number"
      ? usage.cost
      : isRecord(usage.cost) && typeof usage.cost.total === "number"
        ? usage.cost.total
        : null,
  };
}

/**
 * 长会话压缩判定（审计#18）：用 pi 内建的 estimateContextTokens + shouldCompact
 * 按 token 余量判断是否该让 harness.compact() 压缩。contextWindow 未知（≤0）
 * 时明确返回 false——宁可用 40 条兜底也不盲目压缩每一轮。
 */
export async function shouldCompactNow(session: unknown, contextWindow: number): Promise<boolean> {
  if (typeof contextWindow !== "number" || contextWindow <= 0) return false;
  const { estimateContextTokens, shouldCompact, DEFAULT_COMPACTION_SETTINGS } = (await loadPiAgent()) as {
    estimateContextTokens: (messages: unknown[]) => { tokens: number };
    shouldCompact: (tokens: number, contextWindow: number, settings: unknown) => boolean;
    DEFAULT_COMPACTION_SETTINGS: unknown;
  };
  const target = session as { getBranch(): Promise<unknown[]> };
  const branch = await target.getBranch();
  const messages = branch
    .filter((entry) => isRecord(entry) && entry.type === "message" && isRecord(entry.message))
    .map((entry) => (entry as { message: unknown }).message);
  const estimate = estimateContextTokens(messages);
  return shouldCompact(estimate.tokens, contextWindow, DEFAULT_COMPACTION_SETTINGS);
}

// ── Request parsing ──────────────────────────────────────────────────────

function parseMessages(raw: unknown): CoachRuntimeMessage[] {
  if (!Array.isArray(raw)) {
    throw new Error("messages must be an array");
  }
  return raw.map((item) => {
    if (!isRecord(item) || (item.role !== "user" && item.role !== "assistant" && item.role !== "system")) {
      throw new Error("Invalid message role");
    }
    if (typeof item.content !== "string") {
      throw new Error("Invalid message content");
    }
    return { role: item.role, content: item.content };
  });
}

function parseRequest(raw: unknown): ParsedRequest {
  if (!isRecord(raw)) {
    throw new Error("Request must be a JSON object");
  }
  const schemaVersion = raw.schema_version;
  if (schemaVersion !== COACH_RUNTIME_TURN_SCHEMA_V1) {
    throw new Error(`Unsupported schema_version: ${String(schemaVersion)}`);
  }
  if (typeof raw.run_id !== "string" || typeof raw.user_id !== "string") {
    throw new Error("run_id and user_id are required strings");
  }

  const messages = parseMessages(raw.messages);
  const sessionId = raw.session_id;
  if (sessionId !== undefined && (typeof sessionId !== "string" || !/^coach-thread:[0-9]+$/.test(sessionId))) {
    throw new Error("session_id must be an opaque Coach thread identity");
  }
  const systemPrompt = typeof raw.system_prompt === "string" ? raw.system_prompt : undefined;
  const toolBridge = raw.tool_bridge;
  if (toolBridge !== undefined && !isRecord(toolBridge)) throw new Error("tool_bridge must be an object");
  const model = parseProviderProfile(raw.model);
  // 结构化引用：只收合法的 analysis:N，非法项静默丢弃，封顶 10 条
  // （与单条消息文本里手打多个 ref 的合理上限同量级）。
  const contextRefs = Array.isArray(raw.context_refs)
    ? (raw.context_refs.filter(
        (ref): ref is string => typeof ref === "string" && /^analysis:[1-9][0-9]*$/.test(ref),
      ).slice(0, 10))
    : undefined;

  return {
    schema_version: schemaVersion,
    run_id: raw.run_id,
    user_id: raw.user_id,
    messages,
    session_id: sessionId,
    system_prompt: systemPrompt,
    context_refs: contextRefs,
    model,
    tool_bridge: toolBridge as ParsedRequest["tool_bridge"],
  };
}

// ── Conversation helpers ─────────────────────────────────────────────────

/** Extract the numeric Coach thread id from an opaque `coach-thread:<id>` id. */
function threadIdFromSessionId(sessionId: string | undefined): number | null {
  const match = sessionId ? /^coach-thread:([1-9][0-9]*)$/.exec(sessionId) : null;
  return match ? Number(match[1]) : null;
}

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

function toHistoryMessage(message: CoachRuntimeMessage, model: ResolvedProviderModel["model"]) {
  if (message.role === "user") {
    return {
      role: "user" as const,
      content: [{ type: "text" as const, text: message.content }],
      timestamp: Date.now(),
    };
  }
  return {
    role: "assistant" as const,
    content: [{ type: "text" as const, text: message.content }],
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: EMPTY_USAGE,
    stopReason: "stop" as const,
    timestamp: Date.now(),
  };
}

function splitConversation(messages: CoachRuntimeMessage[], model: ResolvedProviderModel["model"]) {
  const conversational = messages.filter((message) => message.role !== "system");
  if (conversational.length === 0) {
    throw new Error("At least one user message is required");
  }
  const last = conversational[conversational.length - 1];
  if (last.role !== "user") {
    throw new Error("Last message must be from user");
  }
  const rawHistory = conversational.slice(0, -1);
  const pairedHistory: CoachRuntimeMessage[] = [];
  for (let index = 0; index + 1 < rawHistory.length;) {
    const user = rawHistory[index];
    const assistant = rawHistory[index + 1];
    if (user.role === "user" && assistant.role === "assistant") {
      pairedHistory.push(user, assistant);
      index += 2;
    } else {
      index += 1;
    }
  }
  const history = pairedHistory.map((message) => toHistoryMessage(message, model));
  return { history, lastMessage: last.content };
}

// ── Persistent session wrapper ───────────────────────────────────────────

/** Upper bound on context messages so long conversations don't grow unbounded. */
const MAX_CONTEXT_MESSAGES = 40;

// 条数窗口不够：工具结果单条可达数十万字符（read 整份 evidence.json、
// product command 全量 JSON），持久化后每回合重发，实测把请求顶到 12-15
// 万 token——免费中转通道直接 429/空回复（2026-09-06 中转站内测事故）。
// 处置对齐 Claude Code 的 microcompact：超过触发阈值时，把旧 toolResult 的
// 内容替换为占位符，只保留最近几条完整结果；user/assistant 文本永不触碰
// （Anthropic 官方称清除旧工具结果是"最安全、最轻量的 compaction 形式"）。
// 触发阈值对齐 Anthropic context editing 默认 100K token，按 OpenAI 口径
// 1 token≈4 字符折算（上下文大头是 ASCII JSON，折算可信）；真实 token 计数
// 由 pi compaction（contextWindow−16K 触发，shouldCompactNow）兜底。
const CONTEXT_CLEAR_TRIGGER_CHARS = 400_000;
const CLEAR_KEEP_TOOL_RESULTS = 3;
export const CLEARED_TOOL_RESULT_PLACEHOLDER = "[Old tool result content cleared]";

function contextMessageChars(message: unknown): number {
  if (!isRecord(message)) return 0;
  return JSON.stringify((message as { content?: unknown }).content ?? "").length;
}

function isMessageEntry(entry: unknown): entry is {
  type: string;
  id: string;
  message: { role: string; content: unknown };
} {
  return isRecord(entry) && entry.type === "message" && isRecord(entry.message);
}

function redactMessage(message: unknown, secrets: string[]): unknown {
  const content = (message as { content?: unknown })?.content;
  if (typeof content === "string") {
    return { ...(message as object), content: redactRuntimeSecrets(content, secrets) };
  }
  if (Array.isArray(content)) {
    return {
      ...(message as object),
      content: content.map((block) =>
        isRecord(block) && block.type === "text" && typeof block.text === "string"
          ? { ...block, text: redactRuntimeSecrets(block.text, secrets) }
          : block,
      ),
    };
  }
  return message;
}

/**
 * Wrap a persistent Pi session for harness use.
 *
 * - The current user message is already persisted by agent-runs before the
 *   turn, so the harness's fresh copy of the same prompt is skipped.
 * - Assistant replies are redacted at the write boundary and failed / empty
 *   replies are not persisted (mirroring the pre-Pi lifecycle).
 * - buildContext() drops the trailing current user message and caps the
 *   window, keeping long conversations bounded.
 */
export function wrapCoachSession(session: unknown, secrets: string[]): unknown {
  const target = session as {
    appendMessage(message: unknown): Promise<string>;
    buildContext(options?: unknown): Promise<{ messages: unknown[] }>;
    getBranch(): Promise<unknown[]>;
  };
  return new Proxy(target, {
    get(proxyTarget, prop, receiver) {
      if (prop === "appendMessage") {
        return async (message: unknown): Promise<string | undefined> => {
          const role = (message as { role?: unknown })?.role;
          if (role === "user") {
            const text = extractMessageText((message as { content?: unknown })?.content);
            const branch = await proxyTarget.getBranch();
            const last = branch[branch.length - 1];
            if (
              isMessageEntry(last) &&
              last.message.role === "user" &&
              extractMessageText(last.message.content) === text
            ) {
              return last.id;
            }
            return proxyTarget.appendMessage(message);
          }
          if (role === "assistant") {
            const assistant = message as { content?: unknown; stopReason?: unknown };
            const text = extractMessageText(assistant.content);
            const hasToolCalls = Array.isArray(assistant.content)
              && assistant.content.some((c) => isRecord(c) && c.type === "toolCall");
            // A provider stream that errors (or is aborted) mid-turn can still
            // carry generated text the user already saw streamed. Drop the
            // message only when it is truly empty; persist any non-empty reply
            // so what the UI showed survives a restart (2026-08-20 fix for the
            // text_available-but-jsonl-missing bug).
            if (!text.trim() && !hasToolCalls) {
              return undefined;
            }
            return proxyTarget.appendMessage(redactMessage(message, secrets));
          }
          return proxyTarget.appendMessage(message);
        };
      }
      if (prop === "buildContext") {
        return async (options?: unknown) => {
          const context = await proxyTarget.buildContext(options);
          const messages = context.messages;
          const last = messages[messages.length - 1];
          const withoutCurrent =
            last && (last as { role?: unknown }).role === "user" ? messages.slice(0, -1) : messages;
          let trimmed = withoutCurrent.slice(-MAX_CONTEXT_MESSAGES);
          // microcompact：总字符超阈值时清除旧 toolResult 内容（保留最近
          // CLEAR_KEEP_TOOL_RESULTS 条完整），对话文本不动。只改发往 Provider
          // 的视图，不回写 session。清除不改变消息条数与角色序列，天然不产生
          // 孤立 tool 开头。
          const totalChars = trimmed.reduce((sum, m) => sum + contextMessageChars(m), 0);
          if (totalChars > CONTEXT_CLEAR_TRIGGER_CHARS) {
            const toolResultIndexes: number[] = [];
            trimmed.forEach((m, index) => {
              if ((m as { role?: unknown }).role === "toolResult") toolResultIndexes.push(index);
            });
            const staleCount = toolResultIndexes.length - CLEAR_KEEP_TOOL_RESULTS;
            if (staleCount > 0) {
              const staleIndexes = new Set(toolResultIndexes.slice(0, staleCount));
              trimmed = trimmed.map((m, index) => {
                if (!staleIndexes.has(index)) return m;
                return {
                  ...(m as object),
                  content: [{ type: "text", text: CLEARED_TOOL_RESULT_PLACEHOLDER }],
                };
              });
            }
          }
          // 对齐到 user/system 边界：截断可能切断 assistant(tool_calls)→tool 的配对，
          // 孤立开头的 tool 消息会触发 Provider "tool must follow tool_calls" 错误。
          while (trimmed.length > 0) {
            const firstRole = (trimmed[0] as { role?: unknown }).role;
            if (firstRole === "user" || firstRole === "system") break;
            trimmed = trimmed.slice(1);
          }
          try {
            const seq = trimmed.map((m) => {
              const role = (m as { role?: unknown }).role;
              const content = (m as { content?: unknown }).content;
              const hasToolCalls = Array.isArray(content)
                && content.some((c) => isRecord(c) && c.type === "toolCall");
              return hasToolCalls ? `${role}(tool_calls)` : role;
            }).join(" → ");
            appendFileSync(join(getDataRoot(), "coach-debug.log"), `${new Date().toISOString()} [buildContext] ${seq}\n`, "utf8");
          } catch {
            // best-effort debug
          }
          return { ...context, messages: trimmed };
        };
      }
      const value = Reflect.get(proxyTarget, prop, receiver);
      return typeof value === "function" ? value.bind(proxyTarget) : value;
    },
  });
}

/**
 * Append the turn's user message to the persistent session unless the branch
 * already ends with the same user text. Retried runs replay the original
 * content, and the failed attempt already persisted it — without this check
 * every retry would duplicate the user message in history and Provider
 * context. Mirrors the wrapped-session dedup so the pre-turn persist and the
 * harness persist cannot double-write.
 */
export async function appendUserMessageOnce(session: unknown, text: string): Promise<void> {
  const target = session as {
    appendMessage(message: unknown): Promise<string>;
    getBranch(): Promise<unknown[]>;
  };
  const branch = await target.getBranch();
  const last = branch[branch.length - 1];
  if (
    isMessageEntry(last) &&
    last.message.role === "user" &&
    extractMessageText(last.message.content) === text
  ) {
    return;
  }
  await target.appendMessage({
    role: "user",
    content: [{ type: "text", text }],
    timestamp: Date.now(),
  });
}

// ── Skills loading ───────────────────────────────────────────────────────

const SOURCE_SKILLS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "skills");

function coachSkillsDir(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  return resourceRoot ? resolve(resourceRoot, "skills") : SOURCE_SKILLS_DIR;
}

/**
 * The Pi skills loader computes relative paths with forward-slash comparison,
 * which throws on absolute Windows paths (backslash separators). Wrap the
 * execution env so every path it returns is forward-slash normalized, and
 * normalize file-read inputs before delegating. Only the loadSkills call uses
 * this wrapper; the harness receives the original env.
 */

// ── Text helpers ─────────────────────────────────────────────────────────

/** 代码块 fence 行（``` 或 ~~~，GFM 允许 ≤3 空格缩进）。 */
const FENCE_OPEN_RE = /^ {0,3}(?:`{3,}|~{3,})/;
/** 围栏闭合行：只有同一字符的 fence 记号（允许更长），无 info string。 */
const FENCE_CLOSE_RE = /^ {0,3}(`{3,}|~{3,})\s*$/;

/**
 * 剥除 fenced code / mermaid：连围栏带内容整体删除。流式片段里未闭合的
 * fence 同样吞掉其后残余，终稿闭合后自然收敛——这是「消 fence 流式陷阱」
 * 的代价面，只发生在展示链路。
 */
function stripFencedBlocks(text: string): string {
  const out: string[] = [];
  let fenceToken: string | null = null;
  for (const line of text.split("\n")) {
    if (fenceToken === null) {
      if (FENCE_OPEN_RE.test(line)) fenceToken = line.trim().slice(0, 1);
      else out.push(line);
      continue;
    }
    const trimmed = line.trim();
    if (trimmed.startsWith(fenceToken) && FENCE_CLOSE_RE.test(line)) fenceToken = null;
  }
  return out.join("\n");
}

/**
 * 受限白名单归一化（frontend-parity 批 7，digests §10）。
 *
 * 展示端从「删字符」改为「白名单透传」：放行训练计划天然的受限子集——
 * GFM 表格、有序/无序列表标记、行内加粗 **…** 与 @time 标记；其余继续剥除：
 * H1–H6 标题记号、代码块 fence（含内容）、引用块记号、Mermaid（属 fence 家族）、
 * 图片语法、一切 HTML。红线：任意 HTML 渲染＝XSS 面，受限解析器绝不引入
 * rehype-raw 式通道，这里先在源头剥干净。行内代码不在白名单，保留内容去反引号。
 * JSONL 持久化的是模型原始输出，本函数只作用于展示链路，历史消息零迁移。
 */
export function normalizeUserFacingText(value: string): string {
  return stripFencedBlocks(value)
    .replace(/<(script|style|iframe)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<[^>\n]*>/g, "")
    // 流式片段里未闭合的标签形（<tag… 到行尾无 >）：只吞掉确有标签开头的
    // 形状，普通文本里的比较符（如 5<6）不受影响。
    .replace(/(^|[ \t])<(?:[a-zA-Z/!?][^>\n]*)$/gm, "$1")
    .replace(/^ {0,3}#{1,6}\s+/gm, "")
    .replace(/^ {0,3}>\s?/gm, "")
    .replace(/!\[[^\]\n]*\]\([^)\n]*\)/g, "")
    .replace(/`([^`\n]+)`/g, "$1")
    .trim();
}

function safePartialReply(
  value: string | null,
  secrets: string[],
): string | null {
  const redacted = normalizeUserFacingText(redactRuntimeSecrets(value ?? "", secrets));
  return redacted || null;
}

function extractBridgeSecrets(rawRequest: unknown): string[] {
  if (!isRecord(rawRequest) || !isRecord(rawRequest.tool_bridge)) return [];
  return [rawRequest.tool_bridge.bearer_token, rawRequest.tool_bridge.desktop_token]
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

// ── Tool event collection ────────────────────────────────────────────────

function collectToolEvents(messages: unknown[]): CoachRuntimeToolEvent[] {
  const events: CoachRuntimeToolEvent[] = [];
  for (const message of messages) {
    if (!isRecord(message) || message.role !== "toolResult" || !isRecord(message.details)) continue;
    const event = message.details.event;
    if (isRecord(event) && (event.type === "knowledge" || event.type === "product_command")) {
      events.push(event as CoachRuntimeToolEvent);
    }
  }
  return events;
}

// ── Policy ───────────────────────────────────────────────────────────────

const MANDATORY_POLICY =
  "\n\nMandatory Coach policy: distinguish measured, deterministic_rule, research_supported, community_consensus, and experimental claims; never invent that an action succeeded; never advise ignoring hits, whether a shot hit, or accuracy; write user-facing plain Chinese without exposing canonical timestamps.";

// 回看引导话术纪律。@X.Xs 只有在对话确有挂载的主题分析时才会被前端渲染成
// 可点击的视频跳转；纯总结、历史对比类对话没有主题分析挂载，输出的 @标记
// 是死链接（深读兜底仅覆盖 AI 自己读过的分析）。没有主题时改口述时间点。
export const TIME_LINK_DISCIPLINE_POLICY =
  "\n\n回看引导纪律：@X.Xs 时间标记只有在对话确有主题分析挂载时才是可点击的（用户引用了 analysis:N、或本次讨论中创建过分析、打开过它的视频证据）。纯总结、跨局历史对比这类没有主题分析挂载的对话里，不要输出 @X.Xs 回看引导；需要提具体时刻就改用口述时间点（例如「51 秒处」），不要硬造可点击的标记。";

/** Compose the full system prompt from the base prompt and the skills block. */
export function assembleSystemPrompt(basePrompt: string, skillsBlock: string): string {
  return `${basePrompt}\n\n${skillsBlock}\n\n${MANDATORY_POLICY}${TIME_LINK_DISCIPLINE_POLICY}`;
}

// ── Error helpers ────────────────────────────────────────────────────────

class EmptyAssistantReplyError extends Error {}

function responseSchemaFor(_rawRequest: unknown): CoachRuntimeTurnSchema {
  return COACH_RUNTIME_TURN_SCHEMA;
}

function errorCode(error: unknown): string {
  return error instanceof ProviderProfileError ? error.code : "turn_failed";
}

// 网络类瞬断（undici 的 "terminated"/"fetch failed"、socket/超时等）允许
// 用户直接重试：这类失败不是对话内容问题，标成不可重试会把一次抖动变成
// 死局。
function isTransientProviderError(error: unknown): boolean {
  const message = error instanceof Error ? error.message.toLowerCase() : String(error ?? "").toLowerCase();
  return /terminated|fetch failed|econnreset|econnrefused|socket hang up|etimedout|timeout|network/.test(message);
}

const STOPPED_USER_MESSAGE = "已停止生成。";

function userFacingErrorMessage(error: unknown, stopped: boolean): string {
  if (stopped) return STOPPED_USER_MESSAGE;
  if (error instanceof ProviderProfileError) return "Provider 配置不可用，请在设置中检查后重试。";
  if (error instanceof EmptyAssistantReplyError && error.message.trim()) return error.message;
  return "Coach 暂时无法完成回复，请稍后重试。";
}

// ── Extract assistant text from a Pi message ──────────────────────────────

// 推理模型默认开次顶级（high）思考档（点点 2026-08-25 拍板）。不传档位时
// Pi 对 deepseek 系 thinkingFormat 会下发 thinking:disabled，模型转而把
// 推理"说出声"写进正文污染回复（2026-08-25 内测 onboarding 实测）；显式
// 开档后 API 把推理分离进独立通道。opencode-go 元数据与 deepseek 同款，
// 此前靠中转站忽略 disabled 才碰巧正常，显式传档转为明确正确。不支持
// high 的模型由 Pi 的 clampThinkingLevel 自动落到最近可用档；非推理模型
// 维持默认 off 不动请求形态。
//
// 档级旋钮 reasoning_effort 覆盖默认值："off" = 显式关闭思考（用户拍板
// 接受说出声回归），"minimal".."high" 原样下发（非推理模型由 Pi 收敛为
// off）；未设置（含手工改库产生的未知值）走默认分支——默认路径行为不得
// 变化，这是 deepseek 说出声 bug 的回归红线。
export function defaultThinkingLevel(
  model: unknown,
  reasoningEffort?: CoachReasoningEffort,
): Exclude<CoachReasoningEffort, "off"> | undefined {
  if (reasoningEffort === "off") return undefined;
  if (reasoningEffort === "minimal" || reasoningEffort === "low"
    || reasoningEffort === "medium" || reasoningEffort === "high") {
    return reasoningEffort;
  }
  if (!isRecord(model)) return undefined;
  return model.reasoning === true ? "high" : undefined;
}

function extractAssistantText(message: unknown): string | null {
  if (!isRecord(message) || message.role !== "assistant") return null;
  const content = message.content;
  if (!Array.isArray(content)) return null;
  // 面向用户的文本按块 trim 后用单换行连接、丢弃纯空白块：模型在工具
  // 调用间隙输出的 narration 块自带 \n\n 头尾，直接拼接会让对话气泡出现
  // 连续空行（2026-08-21 实测）。
  const text = content
    .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
    .map((block) => (block as { text: string }).text.trim())
    .filter((text) => text.length > 0)
    .join("\n");
  return text.length > 0 ? text : null;
}

// ── Main turn function ───────────────────────────────────────────────────

export async function runCoachTurn(
  rawRequest: unknown,
  options: TurnOptions = {},
): Promise<CoachRuntimeTurnResponse> {
  const turnStartedAt = performance.now();
  const responseSchema = responseSchemaFor(rawRequest);
  const responseRunId = isRecord(rawRequest) && typeof rawRequest.run_id === "string"
    ? rawRequest.run_id
    : null;
  const secrets = [...extractRuntimeSecrets(rawRequest), ...extractBridgeSecrets(rawRequest)];

  let unsubscribe: (() => void) | null = null;
  const analysisRefs: string[] = [];
  // 非主题深读（fs read/ls 读 analyses/N/ 做历史参照）的 @时间链接兜底列表：
  // 纯总结/对比类对话里 AI 会按话术写「回看 @51.5s」，但 analysis_refs 只收
  // 主题级参与、此时为空，前端链接永远点不动。深读引用单列（不代表本次讨论
  // 的主题），前端在 analysis_refs 找不到视频时用它兜底。
  const deepReadAnalysisRefs: string[] = [];
  // 只有「主题级」参与进 analysis_refs（本次讨论挂载/@time 解析/会话 meta）：
  // 用户显式引用、本讨论创建的分析、evidence 视频打开。历史对比等参照性
  // 读取（fs 深读、analysis.get/compare）不算主题，避免污染「本次讨论」。
  const recordAnalysisRead = (analysisId: number, subject = false) => {
    const ref = `analysis:${analysisId}`;
    if (subject) {
      if (!analysisRefs.includes(ref)) analysisRefs.push(ref);
      // 主题确立后，同 id 不再算“非主题回看”。
      const deepIndex = deepReadAnalysisRefs.indexOf(ref);
      if (deepIndex !== -1) deepReadAnalysisRefs.splice(deepIndex, 1);
      return;
    }
    if (!analysisRefs.includes(ref) && !deepReadAnalysisRefs.includes(ref)) {
      deepReadAnalysisRefs.push(ref);
    }
  };
  let activeRunId: string | null = null;
  let partialRevision = 0;
  // 思考按 provider 轮分段（0828：前端要按"想一段→做一步"的时序交错呈现，
  // 单一累积流给不出前因后果）。buffer 只累积当前段：message_start（每轮
  // assistant 消息开始）时把已累积内容作为上一段终文随 activity 下发并清空；
  // partial 帧的 thinking_text 因此恒为当前段全文。
  let thinkingBuffer = "";
  let activitySequence = 0;
  let lastPartialText: string | null = null;
  let lastPartialAt = 0;
  let firstProviderEventMs: number | null = null;
  let firstTextDeltaMs: number | null = null;
  let firstSafeTextMs: number | null = null;
  let providerRounds = 0;
  let providerMs = 0;
  const providerRoundMs: number[] = [];
  let toolMs = 0;
  let repairMs = 0;
  const providerStarts: number[] = [];
  const toolStarts = new Map<string, number>();
  let collectedToolEvents: CoachRuntimeToolEvent[] = [];
  // Skill 调用工作事件（09-10 拍板）：模型 read SKILL.md = 加载该技能，
  // 每个 run 只记首次；实机评估与前端工作流呈现都吃这个事件。
  const seenSkillReads = new Set<string>();
  const recordSkillRead = (skillName: string) => {
    if (seenSkillReads.has(skillName)) return;
    seenSkillReads.add(skillName);
    collectedToolEvents.push({ type: "skill", skill_name: skillName });
  };

  try {
    const request = parseRequest(rawRequest);
    const resolved = await resolveProviderModel(request.model);
    const { history, lastMessage } = splitConversation(request.messages, resolved.model);

    // Load Pi classes
    const { AgentHarness, InMemorySessionRepo, loadSkills, formatSkillsForSystemPrompt } = (await loadPiAgent()) as {
      AgentHarness: new (opts: Record<string, unknown>) => InstanceType<typeof Object> & {
        prompt: (text: string) => Promise<unknown>;
        subscribe: (listener: (event: any, signal?: AbortSignal) => Promise<void> | void) => () => void;
        abort: () => Promise<unknown>;
        steer: (text: string) => Promise<void>;
        followUp: (text: string) => Promise<void>;
        setSteeringMode: (mode: CoachDrainMode) => Promise<void>;
        setFollowUpMode: (mode: CoachDrainMode) => Promise<void>;
        compact: (customInstructions?: string) => Promise<unknown>;
      };
      InMemorySessionRepo: new () => {
        create: () => Promise<{
          appendMessage: (message: unknown) => Promise<string>;
        }>;
      };
      loadSkills: (env: unknown, dirs: string) => Promise<{ skills: unknown[]; diagnostics: unknown[] }>;
      formatSkillsForSystemPrompt: (skills: unknown[]) => string;
    };
    const { NodeExecutionEnv } = (await loadPiNodeEnv()) as {
      NodeExecutionEnv: new (opts: { cwd: string }) => unknown;
    };

    // Create execution environment with cwd pointing to app-data
    const env = new NodeExecutionEnv({ cwd: getDataRoot() });

    // Load the bundled Coach skills (all skills under prompts/skills; see skills-env.ts).
    const skills = (await loadSkills(skillsExecutionEnv(env as Record<string, unknown>), coachSkillsDir())).skills;

    // Intro Session（一辈子一次的开场分析）：只对开场分析会话注入 intro skill。
    // 该 skill 不是模型可选技能，而是本会话的固定流程，所以永不进入模型的
    // <available_skills> 列表；注入方式是把 SKILL.md 全文追加到系统提示词，
    // 其他任何会话都拿不到它（防止开场流程泄漏进日常对话）。
    const introSessionActive = isIntroSession(threadIdFromSessionId(request.session_id));
    const modelSkills = skills.filter((skill) => (skill as { name?: unknown }).name !== "intro-session");
    const introSkill = introSessionActive
      ? skills.find((skill) => (skill as { name?: unknown }).name === "intro-session")
      : undefined;
    const introSkillBlock = introSkill
      ? `\n\n<intro_session_skill>\n${(introSkill as { content?: unknown }).content ?? ""}\n</intro_session_skill>`
      : "";

    // Use the persistent Coach thread session when the caller provides one
    // (agent-runs path) so history comes from Session.buildContext(); otherwise
    // fall back to an in-memory session rebuilt from the request.
    const session = options.session
      ? wrapCoachSession(options.session, secrets)
      : await (async () => {
          const repo = new InMemorySessionRepo();
          const memorySession = await repo.create();
          for (const historyMessage of history.slice(-MAX_CONTEXT_MESSAGES)) {
            await memorySession.appendMessage(historyMessage);
          }
          return memorySession;
        })();

    // Build system prompt via harness callback: the base prompt plus the
    // spec-compatible skills block (Pi injects resources into the callback).
    // 基础提示词带本机环境实测（探测在 sidecar 启动时已热身，此处命中缓存）。
    const baseSystemPrompt = await resolveSystemPromptWithEnvFacts(request.system_prompt);
    const systemPrompt = (context: { resources: { skills?: unknown[] } }) =>
      assembleSystemPrompt(
        baseSystemPrompt + introSkillBlock,
        formatSkillsForSystemPrompt(context.resources.skills ?? []),
      );

    // Build tools: file system tools + knowledge + product commands + web.
    // read/write/ls/edit/grep/find/bash 全部来自 pi coding-agent 原版实现
    // （fs-tools.ts 只加 Coach 产品护栏），read/write 的 Coach 包装见 fs-tools.ts。
    // web_search/fetch_page 为免 key 联网工具（见 web-search-native.ts）。
    const dataRoot = getDataRoot();
    const tools = [
      await createReadTool(dataRoot),
      await createWriteTool(dataRoot),
      await createLsTool(dataRoot),
      await createEditTool(dataRoot),
      await createGrepTool(dataRoot),
      await createFindTool(dataRoot),
      await createBashTool(dataRoot),
      createProductCommandTool(request.tool_bridge ?? null, {
        ownerId: request.user_id,
      }),
      ...await createWebSearchTools(),
    ];

    // Allow a test-injected stream to stand in for the resolved provider
    // stream. The wrapper keeps every other Models method working while
    // overriding streamSimple, so the harness streams through the fake.
    const harnessModels: PiModels = options.streamFn
      ? Object.assign(Object.create(resolved.models), {
          streamSimple: options.streamFn as PiModels["streamSimple"],
        })
      : resolved.models;

    // Create AgentHarness. Provider 请求启用 Pi 内建重试（OpenAI SDK 的
    // 连接/5xx 预流式重试）：代理与上游网络抖动是流中断的常见来源，
    // maxRetries=0 会让一次瞬断直接终结整轮对话（2026-08-21 实测 terminated）。
    // DeepSeek 系端点（内置 provider 或自定义 deepseek.com）的推理模型：
    // 不传思考档时 Pi 会下发 thinking:{type:"disabled"}，模型转而把推理
    // 过程"说出声"写进正文——回复被内部独白淹没（2026-08-25 内测 onboarding
    // 实测）。显式开思考档，API 才会把推理分离进 reasoning_content。
    // 其余 provider 维持默认（off），不改既有请求形态。档级 reasoning_effort
    // 显式设置时覆盖默认（见 defaultThinkingLevel）。
    const thinkingLevel = defaultThinkingLevel(resolved.model, request.model.reasoning_effort);

    const harness = new AgentHarness({
      env,
      session,
      models: harnessModels,
      systemPrompt,
      tools,
      model: resolved.model,
      resources: { skills: modelSkills },
      // 超时硬顶 8 分钟（pi/SDK 默认 10 分钟）+ 重试等待 15 秒封顶（默认 60
      // 秒会让限流后的流式像"挂死"，审计#14）。推理模型的长思考单请求通常
      // 远小于该顶；触顶会变成显式可重试错误而不是无限等待。
      streamOptions: { maxRetries: 2, timeoutMs: 480_000, maxRetryDelayMs: 15_000 },
      // 压缩/分支摘要生成的重试（pi RetryPolicy，区别于流式 maxRetries）：
      // compaction 本身是一次网络调用，抖动不该判死整轮压缩（审计#19）。
      retry: { enabled: true, maxRetries: 2, baseDelayMs: 1_000 },
      ...(thinkingLevel ? { thinkingLevel } : {}),
    });

    // Subscribe to events for streaming and tracking
    const publishActivity = async (
      activity: Omit<CoachActivityUpdate, "sequence">,
    ): Promise<void> => {
      if (!options.onActivity) return;
      await options.onActivity({ sequence: ++activitySequence, ...activity });
    };

    const publishPartial = async (text: string | null, force = false, thinkingChanged = false): Promise<void> => {
      if (!options.onPartial) return;
      if (text === null && !thinkingChanged) return;
      if (text === lastPartialText && !thinkingChanged) return;
      const now = performance.now();
      const sentenceBoundary = /[。！？.!?]$/.test(text ?? "");
      const thinkingOnly = text === null || text === lastPartialText;
      // Thinking-only revisions stream at a coarser cadence: they carry no new
      // answer text, so a tight per-token loop would flood the event stream.
      if (!force && thinkingOnly && now - lastPartialAt < 200) return;
      if (!force && !thinkingOnly && partialRevision > 0 && now - lastPartialAt < 80 && !sentenceBoundary) return;
      partialRevision += 1;
      lastPartialText = text;
      lastPartialAt = now;
      firstSafeTextMs ??= Math.max(0, Math.round(now - turnStartedAt));
      await options.onPartial({
        revision: partialRevision,
        text,
        thinking_text: thinkingBuffer || null,
        elapsed_ms: Math.max(0, Math.round(now - turnStartedAt)),
        provider_rounds: providerRounds,
      });
    };

    unsubscribe = harness.subscribe(async (event) => {
      const now = performance.now();
      const eventType: string = event.type;

      if (eventType === "before_provider_request") {
        providerRounds += 1;
        providerStarts.push(now);
        firstProviderEventMs ??= Math.max(0, Math.round(now - turnStartedAt));
        return;
      }

      if (eventType === "message_start" && isRecord(event.message) && event.message.role === "assistant") {
        // 每轮 assistant 消息开始＝上一轮思考段终结：把已累积的 buffer 作为
        // 上一段终文随 thinking started 帧下发（前端据此开新思考段并补齐
        // 丢帧；GET 轮询经 run events 也能重建完整分段）。字段恒带（null 也
        // 带）：前端以 undefined 区分旧版 sidecar（无分段）并整体降级单块。
        await publishActivity({
          kind: "thinking",
          state: "started",
          thinking_text: thinkingBuffer.trim() ? thinkingBuffer : null,
        });
        thinkingBuffer = "";
        return;
      }

      if (eventType === "message_end" && isRecord(event.message) && event.message.role === "assistant") {
        const providerStartedAt = providerStarts.shift();
        if (providerStartedAt !== undefined) {
          const roundMs = Math.max(0, Math.round(now - providerStartedAt));
          providerRoundMs.push(roundMs);
          providerMs += roundMs;
        }
        return;
      }

      if (eventType === "tool_execution_start") {
        toolStarts.set(event.toolCallId, now);
        const commandName = isRecord(event.args) && typeof event.args.command_name === "string"
          ? event.args.command_name.slice(0, 96)
          : undefined;
        await publishActivity({
          kind: "tool",
          state: "started",
          tool_call_id: event.toolCallId,
          tool_name: event.toolName,
          command_name: commandName,
          args_preview: summarizeForUi(event.args, 400),
        });
        return;
      }

      if (eventType === "tool_execution_end") {
        const toolStartedAt = toolStarts.get(event.toolCallId);
        if (toolStartedAt !== undefined) {
          toolMs += Math.max(0, now - toolStartedAt);
          toolStarts.delete(event.toolCallId);
        }
        // Product command results carry a coach_ui_event (e.g. video_time)
        // that must ride the activity so the frontend can act on it live.
        const detailEvent = isRecord(event.result) && isRecord(event.result.details)
          ? event.result.details.event
          : null;
        const commandUiEvent = isRecord(detailEvent) && detailEvent.type === "product_command" && isRecord(detailEvent.ui_event)
          ? detailEvent.ui_event
          : undefined;
        const commandName = isRecord(detailEvent) && typeof detailEvent.command_name === "string"
          ? detailEvent.command_name
          : undefined;
        await publishActivity({
          kind: "tool",
          state: event.isError ? "failed" : "completed",
          tool_call_id: event.toolCallId,
          tool_name: event.toolName,
          ...(commandName ? { command_name: commandName } : {}),
          ...(commandUiEvent ? { ui_event: commandUiEvent } : {}),
          ...(toolStartedAt !== undefined ? { duration_ms: Math.max(0, Math.round(now - toolStartedAt)) } : {}),
          result_preview: summarizeForUi(event.result, 600) ?? (event.isError ? "tool error" : undefined),
        });
        // Collect tool result events for the response
        if (isRecord(detailEvent) && (detailEvent.type === "knowledge" || detailEvent.type === "product_command")) {
          collectedToolEvents.push(detailEvent as CoachRuntimeToolEvent);
        }
        return;
      }

      if (eventType === "after_provider_response") {
        // 限流/服务端错误可见化（审计#16）：此前 429/5xx 的 HTTP 状态不可
        // 观测，排障只能翻 provider 端。非 2xx 落诊断日志，不打断对话。
        const status = (event as { status?: unknown }).status;
        if (typeof status === "number" && status >= 400) {
          try {
            appendFileSync(
              join(getDataRoot(), "coach-error.log"),
              `${new Date().toISOString()} [coach-turn] provider HTTP ${status} run=${request.run_id}\n`,
              "utf8",
            );
          } catch {
            // best-effort
          }
        }
        return;
      }

      if (eventType === "queue_update") {
        // Pi harness 在 steer/followUp 入列与各排水点同步发布 queue_update。
        // digests §11 批 5 最小透传：沿用既有 activity 通道把它记进 run
        // events（SSE 与 GET 同源），前端队列 chips 保持前端权威态，可据此对账。
        lastSteerCount = Array.isArray(event.steer) ? event.steer.length : 0;
        lastFollowUpCount = Array.isArray(event.followUp) ? event.followUp.length : 0;
        await publishActivity({
          kind: "queue",
          state: "updated",
          steer_count: lastSteerCount,
          follow_up_count: lastFollowUpCount,
          next_turn_count: Array.isArray(event.nextTurn) ? event.nextTurn.length : nextTurnTexts.length,
        });
        return;
      }

      if (
        eventType === "message_update"
        && isRecord(event.assistantMessageEvent)
        && event.assistantMessageEvent.type === "thinking_delta"
        && typeof event.assistantMessageEvent.delta === "string"
      ) {
        // Extended thinking streams as full-replace deltas (like text): keep
        // only the trailing 8KB so unbounded reasoning can't grow memory or
        // the SSE payload. Non-reasoning models never emit this event.
        thinkingBuffer = `${thinkingBuffer}${event.assistantMessageEvent.delta}`.slice(-8_000);
        await publishPartial(lastPartialText, false, true);
        return;
      }

      if (eventType === "message_update" && isRecord(event.assistantMessageEvent) && event.assistantMessageEvent.type === "text_delta") {
        firstTextDeltaMs ??= Math.max(0, Math.round(now - turnStartedAt));
        const partialText = extractAssistantText(event.message);
        await publishPartial(safePartialReply(partialText, secrets));
        return;
      }
    });

    // Register abort handler
    if (activeTurns.has(request.run_id)) {
      throw new Error("Duplicate active Coach run id");
    }
    activeRunId = request.run_id;
    // next_turn 的排队缓冲在我们这层：pi 的 nextTurnQueue 排水语义是把排队
    // 消息 prepend 进"下一次 prompt"的消息列表，而 harness 随单次 run 生死，
    // 直接透传会把排水时机丢掉、排队消息随 run 销毁。这里缓冲入列文本，run
    // 的首轮 prompt 结束后用同一 harness 连续 prompt() 排水（见下方循环），
    // 引擎机制仍是 pi 的 turn 循环 + 会话持久化。
    const nextTurnTexts: string[] = [];
    let lastSteerCount = 0;
    let lastFollowUpCount = 0;
    const queueTarget: TurnQueueTarget = {
      enqueue: async (kind, text) => {
        if (kind === "steer") return harness.steer(text);
        if (kind === "follow_up") return harness.followUp(text);
        nextTurnTexts.push(text);
        await publishActivity({
          kind: "queue",
          state: "updated",
          steer_count: lastSteerCount,
          follow_up_count: lastFollowUpCount,
          next_turn_count: nextTurnTexts.length,
        });
      },
      setDrainMode: (kind, mode) => {
        if (kind === "steer") return harness.setSteeringMode(mode);
        if (kind === "follow_up") return harness.setFollowUpMode(mode);
        return Promise.resolve(); // next_turn 单槽先进先出，无排水模式概念
      },
    };
    activeTurns.set(request.run_id, {
      // abort() 返回 Promise：浮动 promise 若拒绝会变成 unhandled rejection
      // 崩掉整个 sidecar（stopCoachTurn 走同一闭包）。吞掉但落诊断日志。
      abort: () => {
        harness.abort().catch((error) => {
          console.error("[coach] harness abort failed", error);
        });
      },
      queue: queueTarget,
    });

    // 长会话压缩（pi 内建 compaction，审计#18）：token 余量不足时先让 pi 把
    // 旧历史压成摘要——compaction entry 写进会话后，pi 的 buildContext 自动
    // 用摘要替换被压缩历史，查询侧零改动；下方 MAX_CONTEXT_MESSAGES=40 降级
    // 为极端兜底。压缩失败绝不拦对话，只落诊断日志。
    try {
      const shouldCompact = await shouldCompactNow(
        session,
        (resolved.model as { contextWindow?: number }).contextWindow ?? 0,
      );
      if (shouldCompact) await harness.compact();
    } catch (compactionError) {
      try {
        appendFileSync(
          join(getDataRoot(), "coach-error.log"),
          `${new Date().toISOString()} [coach-turn] compaction skipped: ${compactionError instanceof Error ? compactionError.message : String(compactionError)}\n`,
          "utf8",
        );
      } catch {
        // best-effort
      }
    }

    // Run the turn. Analysis engagement is scoped: explicit "analysis:N"
    // references in the user's message pin the discussion subject; analysis
    // creation and native evidence commands report subjects. Reference reads
    // (history comparison via file reads, analysis.get/compare) stay out of
    // the discussion list. 结构化 context_refs（前端引用菜单）与文本 ref 同效。
    const explicitRefIds = [
      ...explicitAnalysisRefsFromText(
        typeof lastMessage === "string" ? lastMessage : JSON.stringify(lastMessage ?? ""),
      ),
      ...(request.context_refs ?? []).map((ref) => Number(ref.slice("analysis:".length))),
    ];
    for (const id of explicitRefIds) {
      recordAnalysisRead(id, true);
    }
    let replyMessage = await runScopedSkillReads(recordSkillRead, () =>
      runScopedAnalysisReads(recordAnalysisRead, () => harness.prompt(lastMessage)),
    );
    // next_turn 排水：首轮结束后用同一 harness 连续 prompt()，排队的追问依次
    // 成为后续 turn 的用户消息——会话持续、run_id 不变，前端无需新开 run。
    while (nextTurnTexts.length > 0 && !stopRequested.has(request.run_id)) {
      replyMessage = await runScopedSkillReads(recordSkillRead, () =>
        harness.prompt(nextTurnTexts.shift()!),
      );
    }
    const turnUsage = extractUsage(replyMessage);

    if (stopRequested.has(request.run_id)) {
      return failureResponse(
        makeError({
          category: "coach_runtime",
          code: "stopped",
          message: STOPPED_USER_MESSAGE,
          retryable: true,
        }),
        [],
        request.schema_version,
        collectedToolEvents,
        lastPartialText ? safePartialReply(lastPartialText, secrets) : null,
        request.run_id,
        analysisRefs,
        deepReadAnalysisRefs,
      );
    }

    // Provider 流中途断开时，harness 可能 resolve 一条 stopReason=error 且
    // 带部分正文的消息（而非 throw，2026-08-21 实测两种形态都存在）。
    // 半截话不能当完整答案交付：按可重试失败返回。已生成正文已由会话层
    // 持久化，并作为 partial 带回，前端继续展示并允许一键重试。
    if (isRecord(replyMessage) && replyMessage.stopReason === "error") {
      const interruptedText = extractAssistantText(replyMessage);
      if (interruptedText !== null) {
        return failureResponse(
          makeError({
            category: "coach_runtime",
            code: "provider_stream_interrupted",
            message: "回复流被中断，已生成的部分已保留，可直接重试。",
            retryable: true,
          }),
          [],
          request.schema_version,
          collectedToolEvents,
          safePartialReply(interruptedText, secrets),
          request.run_id,
          analysisRefs,
          deepReadAnalysisRefs,
          turnUsage,
        );
      }
    }

    // Extract reply text
    const isAborted = isRecord(replyMessage) && replyMessage.stopReason === "aborted";
    const isError = isRecord(replyMessage) && replyMessage.stopReason === "error";
    const rawReply = extractAssistantText(replyMessage);

    if (rawReply === null) {
      try {
        appendFileSync(
          join(getDataRoot(), "coach-error.log"),
          `${new Date().toISOString()} [coach-turn] replyMessage=${JSON.stringify(replyMessage)}\n`,
          "utf8",
        );
      } catch {
        // best-effort
      }
    }

    if (rawReply === null) {
      if (isAborted) {
        return failureResponse(
          makeError({
            category: "coach_runtime",
            code: "stopped",
            message: STOPPED_USER_MESSAGE,
            retryable: true,
          }),
          [],
          request.schema_version,
          collectedToolEvents,
          lastPartialText ? safePartialReply(lastPartialText, secrets) : null,
          request.run_id,
          analysisRefs,
          deepReadAnalysisRefs,
        );
      }
      const providerError = isRecord(replyMessage) && typeof replyMessage.errorMessage === "string"
        ? replyMessage.errorMessage
        : null;
      throw new EmptyAssistantReplyError(
        isError
          ? providerError ?? "Provider returned an error response"
          : "Provider returned an empty assistant reply",
      );
    }

    const redactedReply = redactRuntimeSecrets(rawReply, secrets);
    const reply = normalizeUserFacingText(redactedReply);
    await publishPartial(reply, true);

    return successResponse(
      reply,
      [],
      request.schema_version,
      collectedToolEvents,
      request.run_id,
      analysisRefs,
      deepReadAnalysisRefs,
      turnUsage,
    );
  } catch (error) {
    const stopped = activeRunId !== null && stopRequested.has(activeRunId);
    // eslint-disable-next-line no-console
    console.error("[coach-turn] turn failed:", error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : error);
    try {
      appendFileSync(
        join(getDataRoot(), "coach-error.log"),
        `${new Date().toISOString()} [coach-turn] ${error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error)}\n`,
        "utf8",
      );
    } catch {
      // Best-effort error capture; never mask the original failure.
    }
    return failureResponse(
      makeError({
        category: "coach_runtime",
        code: stopped ? "stopped" : errorCode(error),
        message: userFacingErrorMessage(error, stopped),
        retryable: stopped || error instanceof EmptyAssistantReplyError || isTransientProviderError(error),
      }),
      [],
      responseSchema,
      collectedToolEvents,
      lastPartialText ? safePartialReply(lastPartialText, secrets) : null,
      responseRunId,
      analysisRefs,
      deepReadAnalysisRefs,
    );
  } finally {
    unsubscribe?.();
    if (activeRunId !== null) {
      activeTurns.delete(activeRunId);
      stopRequested.delete(activeRunId);
    }
    if (options.onComplete) {
      const completedAt = performance.now();
      for (const startedAt of providerStarts.splice(0)) {
        const roundMs = Math.max(0, Math.round(completedAt - startedAt));
        providerRoundMs.push(roundMs);
        providerMs += roundMs;
      }
      for (const startedAt of toolStarts.values()) {
        toolMs += Math.max(0, completedAt - startedAt);
      }
      await options.onComplete({
        total_ms: Math.max(0, Math.round(completedAt - turnStartedAt)),
        first_provider_event_ms: firstProviderEventMs,
        first_text_delta_ms: firstTextDeltaMs,
        first_safe_text_ms: firstSafeTextMs,
        provider_rounds: providerRounds,
        provider_ms: providerMs,
        provider_round_ms: providerRoundMs,
        tool_ms: Math.max(0, Math.round(toolMs)),
        repair_ms: Math.max(0, Math.round(repairMs)),
      });
    }
  }
}
