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
  type CoachRuntimeMessage,
  type CoachRuntimeProviderProfile,
  type CoachRuntimeTurnResponse,
  type CoachRuntimeTurnSchema,
  type CoachRuntimeToolEvent,
} from "./contracts.ts";
import { createProductCommandTool } from "./product-command-tools.ts";
import { resolveSystemPrompt } from "./load-system-prompt.ts";
import {
  extractRuntimeSecrets,
  parseProviderProfile,
  ProviderProfileError,
  redactRuntimeSecrets,
} from "./provider-profile.ts";
import { resolveProviderModel, type PiModels, type ResolvedProviderModel } from "./provider-models.ts";
import { loadPiAgent, loadPiNodeEnv } from "./pi-source.ts";
import { getDataRoot } from "./app-data.ts";
import { createReadTool, createWriteTool, createLsTool, runScopedAnalysisReads } from "./fs-tools.ts";
import { extractMessageText } from "./session-repo.ts";
import type { StreamFn } from "./stream-openai-compatible.ts";

// ── Types ────────────────────────────────────────────────────────────────

type ParsedRequest = {
  schema_version: CoachRuntimeTurnSchema;
  run_id: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  session_id?: string;
  system_prompt?: string;
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
  text: string;
  elapsed_ms: number;
  provider_rounds: number;
};

export type CoachActivityUpdate = {
  sequence: number;
  kind: "thinking" | "tool";
  state: "started" | "completed" | "failed";
  tool_call_id?: string;
  tool_name?: string;
  command_name?: string;
  /** coach_ui_event carried by product commands (e.g. video_time navigation). */
  ui_event?: Record<string, unknown>;
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

// ── Abort tracking ───────────────────────────────────────────────────────

const activeTurns = new Map<string, { abort: () => void }>();
const stopRequested = new Set<string>();

export function stopCoachTurn(runId: string): boolean {
  const active = activeTurns.get(runId);
  if (!active) return false;
  stopRequested.add(runId);
  active.abort();
  return true;
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

  return {
    schema_version: schemaVersion,
    run_id: raw.run_id,
    user_id: raw.user_id,
    messages,
    session_id: sessionId,
    system_prompt: systemPrompt,
    model,
    tool_bridge: toolBridge as ParsedRequest["tool_bridge"],
  };
}

// ── Conversation helpers ─────────────────────────────────────────────────

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
            if (assistant.stopReason === "error" || assistant.stopReason === "aborted" || (!text.trim() && !hasToolCalls)) {
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
function skillsExecutionEnv(base: Record<string, unknown>): Record<string, unknown> {
  const forwardSlash = (value: string) => value.replace(/\\/g, "/");
  const slashPath = (value: unknown) => (typeof value === "string" ? forwardSlash(value) : value);
  const normalizeResultPath = (result: unknown): unknown => {
    if (result && typeof result === "object" && (result as { ok?: boolean }).ok === true) {
      const value = (result as { value?: unknown }).value;
      if (Array.isArray(value)) {
        return {
          ...(result as object),
          value: value.map((entry) =>
            entry && typeof entry === "object" && typeof (entry as { path?: unknown }).path === "string"
              ? { ...entry, path: forwardSlash((entry as { path: string }).path) }
              : entry,
          ),
        };
      }
      if (value && typeof value === "object") {
        const path = (value as { path?: unknown }).path;
        if (typeof path === "string") {
          return { ...(result as object), value: { ...(value as object), path: forwardSlash(path) } };
        }
      }
    }
    return result;
  };
  const call = (name: string, args: unknown[]): Promise<unknown> =>
    (base[name] as (...args: unknown[]) => Promise<unknown>)(...args);
  return {
    cwd: forwardSlash(String(base.cwd ?? "")),
    fileInfo: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("fileInfo", [slashPath(path), signal])),
    listDir: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("listDir", [slashPath(path), signal])),
    canonicalPath: async (path: unknown, signal?: unknown) =>
      normalizeResultPath(await call("canonicalPath", [slashPath(path), signal])),
    readTextFile: (path: unknown, signal?: unknown) => call("readTextFile", [slashPath(path), signal]),
    readTextLines: (path: unknown, options?: unknown) => call("readTextLines", [slashPath(path), options]),
    exists: (path: unknown, signal?: unknown) => call("exists", [slashPath(path), signal]),
  };
}

// ── Text helpers ─────────────────────────────────────────────────────────

function normalizeUserFacingText(value: string): string {
  return value
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/gm, "")
    .replace(/\*\*/g, "")
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

// ── Error helpers ────────────────────────────────────────────────────────

class EmptyAssistantReplyError extends Error {}

function responseSchemaFor(_rawRequest: unknown): CoachRuntimeTurnSchema {
  return COACH_RUNTIME_TURN_SCHEMA;
}

function errorCode(error: unknown): string {
  return error instanceof ProviderProfileError ? error.code : "turn_failed";
}

const STOPPED_USER_MESSAGE = "已停止生成。";

function userFacingErrorMessage(error: unknown, stopped: boolean): string {
  if (stopped) return STOPPED_USER_MESSAGE;
  if (error instanceof ProviderProfileError) return "Provider 配置不可用，请在设置中检查后重试。";
  if (error instanceof EmptyAssistantReplyError && error.message.trim()) return error.message;
  return "Coach 暂时无法完成回复，请稍后重试。";
}

// ── Extract assistant text from a Pi message ──────────────────────────────

function extractAssistantText(message: unknown): string | null {
  if (!isRecord(message) || message.role !== "assistant") return null;
  const content = message.content;
  if (!Array.isArray(content)) return null;
  const text = content
    .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
    .map((block) => (block as { text: string }).text)
    .join("")
    .trim();
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
  const recordAnalysisRead = (analysisId: number) => {
    const ref = `analysis:${analysisId}`;
    if (!analysisRefs.includes(ref)) analysisRefs.push(ref);
  };
  let activeRunId: string | null = null;
  let partialRevision = 0;
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

    // Load Coach skills (peripheral reference, KovaaK data reference, teaching).
    const skills = (await loadSkills(skillsExecutionEnv(env as Record<string, unknown>), coachSkillsDir())).skills;

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
    const systemPrompt = (context: { resources: { skills?: unknown[] } }) => {
      const skillsBlock = formatSkillsForSystemPrompt(context.resources.skills ?? []);
      return `${resolveSystemPrompt(request.system_prompt)}\n\n${skillsBlock}\n\n${MANDATORY_POLICY}`;
    };

    // Build tools: file system tools + knowledge + product commands
    const dataRoot = getDataRoot();
    const tools = [
      createReadTool(dataRoot),
      createWriteTool(dataRoot),
      createLsTool(dataRoot),
      createProductCommandTool(request.tool_bridge ?? null, {
        ownerId: request.user_id,
      }),
    ];

    // Allow a test-injected stream to stand in for the resolved provider
    // stream. The wrapper keeps every other Models method working while
    // overriding streamSimple, so the harness streams through the fake.
    const harnessModels: PiModels = options.streamFn
      ? Object.assign(Object.create(resolved.models), {
          streamSimple: options.streamFn as PiModels["streamSimple"],
        })
      : resolved.models;

    // Create AgentHarness
    const harness = new AgentHarness({
      env,
      session,
      models: harnessModels,
      systemPrompt,
      tools,
      model: resolved.model,
      resources: { skills },
    });

    // Subscribe to events for streaming and tracking
    const publishActivity = async (
      activity: Omit<CoachActivityUpdate, "sequence">,
    ): Promise<void> => {
      if (!options.onActivity) return;
      await options.onActivity({ sequence: ++activitySequence, ...activity });
    };

    const publishPartial = async (text: string | null, force = false): Promise<void> => {
      if (!options.onPartial || text === null || text === lastPartialText) return;
      const now = performance.now();
      const sentenceBoundary = /[。！？.!?]$/.test(text);
      if (!force && partialRevision > 0 && now - lastPartialAt < 80 && !sentenceBoundary) return;
      partialRevision += 1;
      lastPartialText = text;
      lastPartialAt = now;
      firstSafeTextMs ??= Math.max(0, Math.round(now - turnStartedAt));
      await options.onPartial({
        revision: partialRevision,
        text,
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
        await publishActivity({ kind: "thinking", state: "started" });
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
        });
        // Collect tool result events for the response
        if (isRecord(detailEvent) && (detailEvent.type === "knowledge" || detailEvent.type === "product_command")) {
          collectedToolEvents.push(detailEvent as CoachRuntimeToolEvent);
        }
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
    activeTurns.set(request.run_id, { abort: () => { void harness.abort(); } });

    // Run the turn. Analysis reads (read/ls tools and native product commands)
    // are reported only to this turn's collector, so concurrent turns cannot
    // pollute each other's analysis_refs.
    const replyMessage = await runScopedAnalysisReads(recordAnalysisRead, () =>
      harness.prompt(lastMessage),
    );

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
      );
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
        retryable: stopped || error instanceof EmptyAssistantReplyError,
      }),
      [],
      responseSchema,
      collectedToolEvents,
      lastPartialText ? safePartialReply(lastPartialText, secrets) : null,
      responseRunId,
      analysisRefs,
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
