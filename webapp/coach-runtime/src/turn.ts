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
  type TeachingTurnContract,
} from "./contracts.ts";
import { createAnalysisSummaryTool } from "./analysis-summary-tool.ts";
import { createCoachKnowledgeTool } from "./knowledge-tools.ts";
import { createSkillLoaderTool, skillsSystemPromptBlock } from "./skill-loader.ts";
import { createProductCommandTool } from "./product-command-tools.ts";
import { getDb } from "./db.ts";
import { createFakeStreamFn } from "./fake-stream.ts";
import { resolveSystemPrompt } from "./load-system-prompt.ts";
import { extractRuntimeSecrets, parseProviderProfile, ProviderProfileError, redactRuntimeSecrets } from "./provider-profile.ts";
import { createModelsStreamFn, resolveProviderModel, type ResolvedProviderModel } from "./provider-models.ts";
import { loadPiAgent } from "./pi-source.ts";
import type { StreamFn } from "./stream-openai-compatible.ts";
import {
  parseTeachingProviderDraft,
  parseTeachingTurnContract,
  teachingEnvelopeInstruction,
  teachingTurnHoldsState,
  teachingTurnRequiresLocalFallback,
} from "./teaching-policy.ts";

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

type ParsedRequest = {
  schema_version: CoachRuntimeTurnSchema;
  run_id: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  session_id?: string;
  analysis_summary: string | null;
  system_prompt?: string;
  model: CoachRuntimeProviderProfile;
  tool_bridge?: import("./contracts.ts").CoachToolBridge;
  teaching_turn?: TeachingTurnContract;
};

type TurnOptions = {
  streamFn?: StreamFn;
  onPartial?: (partial: CoachPartialRevision) => Promise<void> | void;
  onActivity?: (activity: CoachActivityUpdate) => Promise<void> | void;
  onComplete?: (timing: CoachTurnTiming) => Promise<void> | void;
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

class EmptyAssistantReplyError extends Error {}

const activeTurns = new Map<string, { abort: () => void }>();
const stopRequested = new Set<string>();

export function stopCoachTurn(runId: string): boolean {
  const active = activeTurns.get(runId);
  if (!active) return false;
  stopRequested.add(runId);
  active.abort();
  return true;
}

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
  const analysisSummaryValue = raw.analysis_summary;
  const analysisSummary: string | null =
    typeof analysisSummaryValue === "string" ? analysisSummaryValue : null;
  const systemPrompt = typeof raw.system_prompt === "string" ? raw.system_prompt : undefined;
  const toolBridge = raw.tool_bridge;
  if (toolBridge !== undefined && !isRecord(toolBridge)) throw new Error("tool_bridge must be an object");
  const teachingTurn = raw.teaching_turn === undefined ? undefined : parseTeachingTurnContract(raw.teaching_turn);
  const model = parseProviderProfile(raw.model);

  return {
    schema_version: schemaVersion,
    run_id: raw.run_id,
    user_id: raw.user_id,
    messages,
    session_id: sessionId,
    analysis_summary: analysisSummary,
    system_prompt: systemPrompt,
    model,
    tool_bridge: toolBridge as ParsedRequest["tool_bridge"],
    teaching_turn: teachingTurn,
  };
}

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
  const prompt = [
    {
      role: "user" as const,
      content: [{ type: "text" as const, text: last.content }],
      timestamp: Date.now(),
    },
  ];
  return { history, prompt };
}

function extractAssistantReply(messages: unknown[]): string {
  let sawEmptyAssistant = false;
  let sawErrorAssistant = false;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!isRecord(message) || message.role !== "assistant") continue;
    if (message.stopReason === "error") {
      sawErrorAssistant = true;
      continue;
    }
    if (message.stopReason === "aborted") continue;
    const content = message.content;
    if (!Array.isArray(content)) continue;
    const parts = content
      .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
      .map((block) => (block as { text: string }).text);
    const text = parts.join("").trim();
    if (text.length > 0) return text;
    if (message.stopReason === "stop" || message.stopReason === "length") {
      sawEmptyAssistant = true;
    }
  }
  if (sawEmptyAssistant) throw new EmptyAssistantReplyError("Provider returned an empty assistant reply");
  if (sawErrorAssistant) throw new EmptyAssistantReplyError("Provider returned an error response");
  throw new Error("No assistant reply in agent transcript");
}

function extractAssistantPartial(messages: unknown[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!isRecord(message) || message.role !== "assistant") continue;
    const content = message.content;
    if (!Array.isArray(content)) continue;
    const text = content
      .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
      .map((block) => (block as { text: string }).text)
      .join("")
      .trim();
    if (text.length > 0) return text;
  }
  return null;
}

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
  return "Coach 暂时无法完成回复，请稍后重试。";
}

function extractBridgeSecrets(rawRequest: unknown): string[] {
  if (!isRecord(rawRequest) || !isRecord(rawRequest.tool_bridge)) return [];
  return [rawRequest.tool_bridge.bearer_token, rawRequest.tool_bridge.desktop_token]
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

const MANDATORY_POLICY = "\n\nMandatory Coach policy: use only registered product tools; when the user asks to delete an Analysis, call run_product_command with analysis.delete so the trusted UI/backend can create confirmation--a prose request to reply with confirmation is not an action; when the user asks about KovaaK scores/成绩/分数, call kovaak_scores.refresh_connected for the connected account, or kovaak_scores.lookup only when the user supplied steam_profile:N; do not substitute history.list or history.trend; when the user explicitly asks to generate a training-plan draft, call training_plan.generate_draft even without attached analysis and report any grounding error from the command; distinguish measured, deterministic_rule, research_supported, community_consensus, and experimental claims; never invent that an action succeeded; never advise ignoring hits, whether a shot hit, or accuracy; write user-facing plain Chinese without exposing canonical timestamps.";

const PROVIDER_CONTEXT_SAFETY_TOKENS = 4096;
const TOOL_SCHEMA_RESERVE_BYTES = 8 * 1024;

function analysisResultBudgetBytes(
  contextWindow: number,
  maxTokens: number,
  systemPrompt: string,
  conversation: unknown[],
): number {
  const inputBudgetTokens = Math.max(0, contextWindow - maxTokens - PROVIDER_CONTEXT_SAFETY_TOKENS);
  const knownContextBytes = Buffer.byteLength(systemPrompt, "utf8") +
    Buffer.byteLength(JSON.stringify(conversation), "utf8") + TOOL_SCHEMA_RESERVE_BYTES;
  // UTF-8 bytes are a conservative upper bound for tokenizer input tokens.
  return Math.max(0, inputBudgetTokens - knownContextBytes);
}

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

const METRIC_PARTS = new Set([
  "accuracy", "correction", "count", "coverage", "decel", "deviation",
  "distance", "duration", "efficiency", "error", "jitter", "latency", "loss",
  "overshoot", "path", "ratio", "rate", "reacquisition", "score", "sparc",
  "speed", "time", "velocity",
]);

function metricLike(value: string): boolean {
  return value.split(/[._]/).some((part) => METRIC_PARTS.has(part.toLowerCase()));
}

function metricAvailable(value: unknown): boolean {
  if (!isRecord(value)) return true;
  return !["unavailable", "unsupported", "undetermined", "insufficient_evidence"]
    .includes(String(value.availability ?? value.status ?? "").toLowerCase());
}

type RequestedMetricValue = { key: string; value: number };

function requestedSummaryMetricValues(
  analysisSummary: string | null,
  userContent: string,
): RequestedMetricValue[] {
  if (!analysisSummary || !userContent) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return [];
  }
  const projections: unknown[] = [];
  if (isRecord(parsed) && parsed.schema_version === "coach_turn_context.v1" && Array.isArray(parsed.contexts)) {
    for (const context of parsed.contexts) {
      if (!isRecord(context)) continue;
      projections.push(context.projection);
      if (context.comparison_projection !== null) projections.push(context.comparison_projection);
    }
  } else {
    projections.push(parsed);
  }
  const values = new Map<string, number>();
  for (const projection of projections) {
    const diagnosis = isRecord(projection) && isRecord(projection.diagnosis)
      ? projection.diagnosis : null;
    const summary = diagnosis && isRecord(diagnosis.summary) ? diagnosis.summary : null;
    if (summary === null) continue;
    for (const [key, metric] of Object.entries(summary)) {
      if (!metricLike(key) || !metricAvailable(metric) || !isRecord(metric) ||
          typeof metric.value !== "number" || !Number.isFinite(metric.value) ||
          !userContent.includes(key)) continue;
      values.set(key, metric.value);
    }
  }
  return [...values].map(([key, value]) => ({ key, value }));
}

function explicitMetricInstruction(metrics: RequestedMetricValue[]): string {
  if (metrics.length === 0) return "";
  const values = metrics.map(({ key, value }) => `${key}=${String(value)}`).join(", ");
  return `\n\nExplicit available metric request: the attached Analysis contains ${values}. The user named these metrics. Use their actual values first when answering. A missing threshold or baseline limits evaluation only; it does not make the value unavailable. Do not claim these values are unavailable.`;
}

function normalizeUserFacingText(value: string): string {
  return value
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/gm, "")
    .replace(/\*\*/g, "")
    .replace(/`([^`\n]+)`/g, "$1")
    .trim();
}

function safeDisplayString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0 || trimmed.length > 200) return null;
  if (/^(?:[a-z]:[\\/]|[\\/]{2}|\/|~[\\/]|\.\.[\\/]|file:)/i.test(trimmed)) return null;
  if (/[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return null;
  if (/(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret)\s*[:=]/i.test(value)) return null;
  if (/\bbearer\s+\S{8,}/i.test(value)) return null;
  if (/\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{8,}/i.test(value)) return null;
  return trimmed;
}

export function diagnosticContextPromptText(
  parsed: Record<string, unknown>,
  maxBytes: number,
): string | null {
  const analysisRef = isRecord(parsed.analysis_ref) ? parsed.analysis_ref : null;
  const scenario = isRecord(parsed.scenario) ? parsed.scenario : null;
  const diagnosis = isRecord(parsed.diagnosis) ? parsed.diagnosis : null;

  const lines: string[] = [
    "Aiming Cookie has already selected this exact Analysis for the current turn.",
  ];

  if (analysisRef) {
    if (typeof analysisRef.analysis_id === "string") {
      lines.push(`analysis_ref: ${analysisRef.analysis_id}`);
    }
    const analysisType = safeDisplayString(analysisRef.analysis_type);
    if (analysisType) lines.push(`analysis_type: ${analysisType}`);
  }

  if (scenario) {
    const displayName = safeDisplayString(scenario.display_name);
    const aimFamily = safeDisplayString(scenario.aim_family);
    if (displayName) lines.push(`scenario: ${displayName}`);
    if (aimFamily) lines.push(`aim_family: ${aimFamily}`);
  }

  if (diagnosis) {
    if (isRecord(diagnosis.profile)) {
      const profileLabel = safeDisplayString(diagnosis.profile.label);
      if (profileLabel) lines.push(`profile: ${profileLabel}`);
    }
    if (Array.isArray(diagnosis.issues)) {
      const issueLines: string[] = [];
      for (const issue of diagnosis.issues) {
        if (!isRecord(issue)) continue;
        const signal = safeDisplayString(issue.signal);
        if (!signal) continue;
        const severity = safeDisplayString(issue.severity);
        issueLines.push(severity ? `- ${signal} [${severity}]` : `- ${signal}`);
      }
      if (issueLines.length > 0) {
        lines.push(`diagnosis_issues:\n${issueLines.join("\n")}`);
      }
    }
    if (isRecord(diagnosis.summary)) {
      const issueMetricRefs = new Set<string>();
      if (Array.isArray(diagnosis.issues)) {
        for (const issue of diagnosis.issues) {
          if (!isRecord(issue) || !Array.isArray(issue.metric_refs)) continue;
          for (const ref of issue.metric_refs) {
            if (typeof ref === "string") issueMetricRefs.add(ref);
          }
        }
      }

      const metricEntries: Array<{
        rawKey: string;
        displayName: string;
        value: number | string;
        unit: string | null;
      }> = [];
      for (const [key, metric] of Object.entries(diagnosis.summary)) {
        if (!isRecord(metric) || !metricAvailable(metric)) continue;
        const val = metric.value;
        if (val == null) continue;
        const safeKey = safeDisplayString(key);
        if (!safeKey) continue;
        const translatedName = isRecord(metric.definition) && typeof metric.definition.name === "string"
          ? safeDisplayString(metric.definition.name)
          : null;
        const displayName = translatedName || safeKey;
        if (typeof val === "number" && Number.isFinite(val)) {
          metricEntries.push({ rawKey: safeKey, displayName, value: val, unit: safeDisplayString(metric.unit) });
        } else if (typeof val === "string") {
          const safeVal = safeDisplayString(val);
          if (safeVal) metricEntries.push({ rawKey: safeKey, displayName, value: safeVal, unit: safeDisplayString(metric.unit) });
        }
      }

      if (metricEntries.length > 15) {
        const prioritized = metricEntries.filter((e) => issueMetricRefs.has(e.rawKey));
        const rest = metricEntries.filter((e) => !issueMetricRefs.has(e.rawKey)).slice(0, 10);
        metricEntries.length = 0;
        metricEntries.push(...prioritized, ...rest);
      }

      if (metricEntries.length > 0) {
        const metricLines = metricEntries.map(({ displayName, value, unit }) =>
          `- ${displayName}: ${String(value)}${unit ? ` (${unit})` : ""}`,
        );
        const withoutMetricsBytes = Buffer.byteLength(lines.join("\n"), "utf8");
        const prefixBytes = Buffer.byteLength("\nMetrics:", "utf8");
        if (withoutMetricsBytes + prefixBytes < maxBytes) {
          const fittedLines: string[] = [];
          let used = withoutMetricsBytes + prefixBytes;
          for (const line of metricLines) {
            const lineBytes = Buffer.byteLength(`\n${line}`, "utf8");
            if (used + lineBytes > maxBytes) break;
            fittedLines.push(line);
            used += lineBytes;
          }
          if (fittedLines.length > 0) {
            lines.push(`Metrics:\n${fittedLines.join("\n")}`);
          }
        }
      }
    }
  }

  const text = lines.join("\n");
  return Buffer.byteLength(text, "utf8") <= maxBytes ? text : null;
}

function hasAttachedAnalysisContext(analysisSummary: string | null): boolean {
  if (!analysisSummary) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return false;
  }
  if (!isRecord(parsed)) return false;
  if (parsed.schema_version === "coach_diagnostic_context.v3") {
    return isRecord(parsed.analysis_ref) &&
      typeof parsed.analysis_ref.analysis_id === "string";
  }
  if (parsed.schema_version !== "coach_turn_context.v1" ||
      !Array.isArray(parsed.contexts) || parsed.contexts.length === 0) return false;
  return parsed.contexts.every((context) => isRecord(context) &&
    typeof context.context_ref === "string" &&
    /^context:[A-Za-z0-9._:-]+$/.test(context.context_ref) &&
    typeof context.analysis_ref === "string" &&
    /^analysis:[1-9][0-9]*$/.test(context.analysis_ref));
}

function singleAttachedAnalysisInput(
  analysisSummary: string | null,
  maxBytes: number,
): string | null {
  if (!analysisSummary) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (parsed.schema_version === "coach_diagnostic_context.v3") {
    return diagnosticContextPromptText(parsed, maxBytes);
  }
  if (parsed.schema_version !== "coach_turn_context.v1" ||
      !Array.isArray(parsed.contexts) || parsed.contexts.length !== 1) return null;
  const context = parsed.contexts[0];
  if (!isRecord(context) || typeof context.context_ref !== "string" ||
      typeof context.analysis_ref !== "string" || !isRecord(context.projection)) return null;
  const text = [
    "Aiming Cookie has already selected this exact Analysis for the current turn.",
    `context_ref: ${context.context_ref}`,
    `analysis_ref: ${context.analysis_ref}`,
    `analysis_projection: ${JSON.stringify(context.projection)}`,
  ].join("\n");
  return Buffer.byteLength(text, "utf8") <= maxBytes ? text : null;
}

function safePartialReply(
  value: string | null,
  _request: ParsedRequest,
  _currentMessages: unknown[],
  secrets: string[],
): string | null {
  const redacted = normalizeUserFacingText(redactRuntimeSecrets(value ?? "", secrets));
  return redacted || null;
}

export async function runCoachTurn(rawRequest: unknown, options: TurnOptions = {}): Promise<CoachRuntimeTurnResponse> {
  const turnStartedAt = performance.now();
  const responseSchema = responseSchemaFor(rawRequest);
  const responseRunId = isRecord(rawRequest) && typeof rawRequest.run_id === "string"
    ? rawRequest.run_id
    : null;
  const secrets = [...extractRuntimeSecrets(rawRequest), ...extractBridgeSecrets(rawRequest)];
  let agent: {
    prompt: (input: unknown) => Promise<void>;
    abort: () => void;
    subscribe: (listener: (event: any) => Promise<void> | void) => () => void;
    state: { messages: unknown[]; tools: Array<{ name: string }> };
  } | null = null;
  let unsubscribe: (() => void) | null = null;
  let activeRunId: string | null = null;
  let turnMessageStart = 0;
  let partialMessageStart = 0;
  let parsedRequest: ParsedRequest | null = null;
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
  try {
    const request = parseRequest(rawRequest);
    parsedRequest = request;
    const explicitMetrics = requestedSummaryMetricValues(
      request.analysis_summary,
      request.messages.at(-1)?.content ?? "",
    );
    const resolved = await resolveProviderModel(request.model);
    const { Agent } = (await loadPiAgent()) as {
      Agent: new (opts: Record<string, unknown>) => {
        prompt: (input: unknown) => Promise<void>;
        abort: () => void;
        subscribe: (listener: (event: any) => Promise<void> | void) => () => void;
        state: { messages: unknown[]; tools: Array<{ name: string }> };
      };
    };
    const { history, prompt } = splitConversation(request.messages, resolved.model);
    turnMessageStart = history.length;
    partialMessageStart = history.length;
    const baseStreamFn = options.streamFn ?? createModelsStreamFn(resolved.models);
    const streamFn: StreamFn = ((model, context, streamOptions) => {
      providerRounds += 1;
      providerStarts.push(performance.now());
      return baseStreamFn(model, context, streamOptions);
    }) as StreamFn;
    const publishActivity = async (
      activity: Omit<CoachActivityUpdate, "sequence">,
    ): Promise<void> => {
      if (!options.onActivity) return;
      await options.onActivity({ sequence: ++activitySequence, ...activity });
    };
    const hasAttachedAnalysis = hasAttachedAnalysisContext(request.analysis_summary);
    const systemPrompt = `${resolveSystemPrompt(request.system_prompt)}${skillsSystemPromptBlock()}${MANDATORY_POLICY}${request.teaching_turn ? `\n\n${teachingEnvelopeInstruction(request.teaching_turn)}` : ""}${explicitMetricInstruction(explicitMetrics)}`;
    const maxAnalysisResultBytes = analysisResultBudgetBytes(
      resolved.model.contextWindow,
      resolved.model.maxTokens,
      systemPrompt,
      [...history, ...prompt],
    );
    const attachedAnalysisInput = singleAttachedAnalysisInput(
      request.analysis_summary,
      maxAnalysisResultBytes,
    );
    if (attachedAnalysisInput !== null) {
      prompt[0].content.push({ type: "text" as const, text: attachedAnalysisInput });
    }
    const nativeDb = getDb();
    const tools = [
      createAnalysisSummaryTool(request.analysis_summary, { maxResultBytes: maxAnalysisResultBytes }),
      createCoachKnowledgeTool(),
      createSkillLoaderTool(),
    ];
    // Product command tool: native reads go to SQLite directly, writes/evidence
    // still go through the HTTP bridge. If no bridge is available, only native
    // reads work.
    tools.push(createProductCommandTool(request.tool_bridge ?? null, {
      db: nativeDb,
      ownerId: request.user_id,
      ...(hasAttachedAnalysis ? {
        excludedCommands: ["run.list", "analysis.create_from_run"],
      } : {}),
    }));
    agent = new Agent({
      streamFn,
      sessionId: request.session_id,
      initialState: {
        systemPrompt,
        model: resolved.model,
        tools,
        messages: history,
      },
    });

    const publishPartial = async (
      text: string | null,
      currentMessages: unknown[],
      force = false,
    ): Promise<void> => {
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

    unsubscribe = agent.subscribe(async (event) => {
      const now = performance.now();
      if (event.type === "message_start" && event.message?.role === "assistant") {
        firstProviderEventMs ??= Math.max(0, Math.round(now - turnStartedAt));
        await publishActivity({ kind: "thinking", state: "started" });
      } else if (event.type === "message_end" && event.message?.role === "assistant") {
        const providerStartedAt = providerStarts.shift();
        if (providerStartedAt !== undefined) {
          const roundMs = Math.max(0, Math.round(now - providerStartedAt));
          providerRoundMs.push(roundMs);
          providerMs += roundMs;
        }
      } else if (event.type === "tool_execution_start") {
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
      } else if (event.type === "tool_execution_end") {
        const toolStartedAt = toolStarts.get(event.toolCallId);
        if (toolStartedAt !== undefined) {
          toolMs += Math.max(0, now - toolStartedAt);
          toolStarts.delete(event.toolCallId);
        }
        await publishActivity({
          kind: "tool",
          state: event.isError ? "failed" : "completed",
          tool_call_id: event.toolCallId,
          tool_name: event.toolName,
        });
      }
      if (
        event.type !== "message_update" ||
        event.assistantMessageEvent?.type !== "text_delta"
      ) return;
      firstTextDeltaMs ??= Math.max(0, Math.round(now - turnStartedAt));
      if (request.teaching_turn) return;
      const currentMessages = agent?.state.messages.slice(turnMessageStart) ?? [event.message];
      await publishPartial(
        safePartialReply(extractAssistantPartial([event.message]), request, currentMessages, secrets),
        currentMessages,
      );
    });

    if (activeTurns.has(request.run_id)) {
      throw new Error("Duplicate active Coach run id");
    }
    activeRunId = request.run_id;
    activeTurns.set(request.run_id, { abort: () => agent?.abort() });

    await agent.prompt(prompt);
    let currentMessages = agent.state.messages.slice(turnMessageStart);
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
        collectToolEvents(currentMessages),
        safePartialReply(extractAssistantPartial(currentMessages), request, currentMessages, secrets),
        request.run_id,
      );
    }
    const rawReply = redactRuntimeSecrets(extractAssistantReply(currentMessages), secrets);
    let reply = normalizeUserFacingText(rawReply);
    if (request.teaching_turn) {
      const draft = parseTeachingProviderDraft(rawReply);
      reply = normalizeUserFacingText(draft !== null ? draft.text : rawReply);
    }
    await publishPartial(reply, currentMessages, true);
    return successResponse(
      reply,
      [],
      request.schema_version,
      collectToolEvents(currentMessages),
      request.run_id,
    );
  } catch (error) {
    const stopped = activeRunId !== null && stopRequested.has(activeRunId);
    return failureResponse(
      makeError({
        category: "coach_runtime",
        code: stopped ? "stopped" : errorCode(error),
        message: userFacingErrorMessage(error, stopped),
        retryable: stopped || error instanceof EmptyAssistantReplyError,
      }),
      [],
      responseSchema,
      agent === null ? [] : collectToolEvents(agent.state.messages.slice(turnMessageStart)),
      agent === null
        ? null
        : parsedRequest === null
          ? null
          : safePartialReply(
            extractAssistantPartial(agent.state.messages.slice(partialMessageStart)),
            parsedRequest,
            agent.state.messages.slice(turnMessageStart),
            secrets,
          ),
      responseRunId,
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

export async function runCoachTurnWithFakeStream(
  rawRequest: unknown,
  replyText?: string,
): Promise<CoachRuntimeTurnResponse> {
  return runCoachTurn(rawRequest, { streamFn: createFakeStreamFn(replyText) });
}
