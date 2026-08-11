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
import { createProductCommandTool } from "./product-command-tools.ts";
import { createFakeStreamFn } from "./fake-stream.ts";
import { resolveSystemPrompt } from "./load-system-prompt.ts";
import { extractRuntimeSecrets, parseProviderProfile, ProviderProfileError, redactRuntimeSecrets } from "./provider-profile.ts";
import { createModelsStreamFn, resolveProviderModel, type ResolvedProviderModel } from "./provider-models.ts";
import { loadPiAgent } from "./pi-source.ts";
import type { StreamFn } from "./stream-openai-compatible.ts";
import {
  fallbackForTeachingTurn,
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

class ToolComplianceError extends Error {}
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
  if (error instanceof ToolComplianceError) return "tool_compliance_required";
  return error instanceof ProviderProfileError ? error.code : "turn_failed";
}

const STOPPED_USER_MESSAGE = "已停止生成。";

function userFacingErrorMessage(error: unknown, stopped: boolean): string {
  if (stopped) return STOPPED_USER_MESSAGE;
  if (error instanceof ToolComplianceError) return "这项操作未能安全完成，请重试。";
  if (error instanceof ProviderProfileError) return "Provider 配置不可用，请在设置中检查后重试。";
  return "Coach 暂时无法完成回复，请稍后重试。";
}

function extractBridgeSecrets(rawRequest: unknown): string[] {
  if (!isRecord(rawRequest) || !isRecord(rawRequest.tool_bridge)) return [];
  return [rawRequest.tool_bridge.bearer_token, rawRequest.tool_bridge.desktop_token]
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

const MANDATORY_POLICY = "\n\nMandatory Coach policy: use only registered product tools; when the user asks to delete an Analysis, call run_product_command with analysis.delete so the trusted UI/backend can create confirmation--a prose request to reply with confirmation is not an action; distinguish measured, deterministic_rule, research_supported, community_consensus, and experimental claims; never invent that an action succeeded; never advise ignoring hits, whether a shot hit, or accuracy; never reveal bridge tokens, paths, URLs, credentials, raw traces, arbitrary payloads, internal schema/table/tool/field names, raw cursors, or raw event/segment refs; write user-facing plain Chinese without exposing canonical timestamps.";
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

const DELETE_REFERENCE_PATTERN = /(?:\u5220\u9664|\u5220\u6389|\u79fb\u9664|delete|remove)\s*(?:the\s+)?analysis\s*:?\s*(\d+)\b/gi;
const DELETE_DISCUSSION_PATTERN = /\b(?:how|why|should|would|can\s+i|could\s+i|explain|discuss|what|impact)\b|\u5982\u4f55|\u600e\u4e48|\u662f\u5426|\u8ba8\u8bba|\u5f71\u54cd|\u540e\u679c|(?:\u6211|\u81ea\u5df1)\s*(?:\u53ef\u4ee5|\u80fd\u5426|\u662f\u5426)\s*(?:\u5220\u9664|\u5220\u6389|\u79fb\u9664)/i;
const DELETE_NEGATION_PATTERN = /(?:\u4e0d\u8981|\u522b|\u65e0\u9700|\u4e0d\u9700\u8981)\s*(?:\u5220\u9664|\u5220\u6389|\u79fb\u9664)|\b(?:no|not|don't|do\s+not)\b(?:\s+\w+){0,3}\s+(?:delete|remove)\b/i;

function attachedAnalysisRefs(analysisSummary: string | null): Set<string> {
  const refs = new Set<string>();
  if (!analysisSummary) return refs;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return refs;
  }
  const add = (value: unknown) => {
    if (typeof value === "string" && /^analysis:[1-9]\d*$/.test(value)) refs.add(value);
  };
  const addProjectionRef = (projection: unknown) => {
    if (!isRecord(projection) || !isRecord(projection.analysis_ref)) return;
    add(projection.analysis_ref.analysis_id);
  };
  if (isRecord(parsed) && parsed.schema_version === "coach_turn_context.v1" && Array.isArray(parsed.contexts)) {
    for (const context of parsed.contexts) {
      if (!isRecord(context)) continue;
      add(context.analysis_ref);
      add(context.comparison_analysis_ref);
      addProjectionRef(context.projection);
      addProjectionRef(context.comparison_projection);
    }
  } else {
    addProjectionRef(parsed);
  }
  return refs;
}

function requiredAnalysisDeleteRef(request: ParsedRequest): string | null {
  const userText = request.messages.at(-1)?.content ?? "";
  if (DELETE_DISCUSSION_PATTERN.test(userText) || DELETE_NEGATION_PATTERN.test(userText)) return null;
  const requested = new Set<string>();
  for (const match of userText.matchAll(DELETE_REFERENCE_PATTERN)) {
    requested.add(`analysis:${match[1]}`);
  }
  if (requested.size !== 1) return null;
  const [analysisRef] = requested;
  return attachedAnalysisRefs(request.analysis_summary).has(analysisRef) ? analysisRef : null;
}

function toolCompliancePrompt(analysisRef: string): string {
  return `The current user explicitly requested deletion of ${analysisRef}. Do not write a prose reply or call any other tool. Call only run_product_command with command_name analysis.delete and parameters exactly {"analysis_ref":"${analysisRef}"}. The trusted UI/backend must create the structured confirmation.`;
}

function isAllowedRequiredDeletionRef(value: unknown, analysisRef: string): boolean {
  return value === analysisRef || value === analysisRef.slice("analysis:".length);
}

const TEACHING_RETEST_OUTCOMES = new Set([
  "coach_retest_outcome.v1:improved",
  "coach_retest_outcome.v1:unchanged",
  "coach_retest_outcome.v1:worsened",
  "coach_retest_outcome.v1:mixed_or_inconclusive",
]);

function isValidTeachingRetestWrite(
  teaching: TeachingTurnContract,
  parameters: Record<string, unknown>,
): boolean {
  const expectedKind = teaching.retest.intent === "near_transfer" ? "near_transfer" : "matched";
  const comparability = parameters.comparability;
  const result = parameters.result;
  if (teaching.retest.intent === "none" || parameters.kind !== expectedKind ||
      !["comparable", "not_comparable", "unavailable"].includes(String(comparability)) ||
      typeof result !== "string" || !TEACHING_RETEST_OUTCOMES.has(result)) {
    return false;
  }
  return comparability === "comparable" ||
    result === "coach_retest_outcome.v1:mixed_or_inconclusive";
}

function deeplyEqualPreparedValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
      left.every((value, index) => deeplyEqualPreparedValue(value, right[index]));
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length &&
    leftKeys.every((key, index) => key === rightKeys[index] && deeplyEqualPreparedValue(left[key], right[key]));
}

function isExactPreparedItemWrite(
  teaching: TeachingTurnContract,
  parameters: unknown,
): boolean {
  return teaching.prepared_plan_ref !== null && teaching.prepared_item !== null && isRecord(parameters) &&
    deeplyEqualPreparedValue(parameters, {
      plan_ref: teaching.prepared_plan_ref,
      item_payload: teaching.prepared_item,
    });
}

function restrictTurnTools<T extends {
  name: string;
  execute: (...args: any[]) => Promise<unknown>;
}>(
  tool: T,
  requiredRef: () => string | null,
  toolsDisabled: () => boolean,
  onDisabledToolAttempt: () => void,
  hasIssuedRequiredDeletion: () => boolean,
  onRequiredDeletionIssued: () => void,
  onUnrequestedDeletionAttempt: () => void,
  teachingTurn: () => TeachingTurnContract | undefined,
  hasTeachingWriteViolation: () => boolean,
  onOutOfPhaseTeachingWrite: () => void,
  onProductCommandExecutionFailure: () => void,
): T {
  const execute = tool.execute.bind(tool);
  return {
    ...tool,
    async execute(...args: any[]) {
      if (toolsDisabled()) {
        onDisabledToolAttempt();
        throw new ToolComplianceError("Grounding repair may not call tools");
      }
      if (hasTeachingWriteViolation()) {
        throw new ToolComplianceError("Teaching turn already rejected a training write");
      }
      const teaching = teachingTurn();
      if (teaching && tool.name === "run_product_command" && isRecord(args[1])) {
        const commandName = String(args[1].command_name);
        const isTrainingWrite = [
          "training_plan.item.add", "training_plan.execution.record", "training_plan.retest.record",
        ].includes(commandName);
        const requiresActiveItem = [
          "training_plan.execution.record", "training_plan.retest.record",
        ].includes(commandName);
        const parameters = args[1].parameters;
        if (isTrainingWrite && (commandName !== teaching.allowed_command ||
            (requiresActiveItem && (!isRecord(parameters) || parameters.item_ref !== teaching.active_item_ref)) ||
            (commandName === "training_plan.item.add" && !isExactPreparedItemWrite(teaching, parameters)) ||
            (commandName === "training_plan.retest.record" &&
              (!isRecord(parameters) || !isValidTeachingRetestWrite(teaching, parameters))))) {
          onOutOfPhaseTeachingWrite();
          throw new ToolComplianceError("Teaching turn may only write its allowed active training fact");
        }
      }
      const analysisRef = requiredRef();
      if (analysisRef === null && tool.name === "run_product_command" &&
          isRecord(args[1]) && args[1].command_name === "analysis.delete") {
        onUnrequestedDeletionAttempt();
        throw new ToolComplianceError("Analysis deletion requires an explicit reachable user request");
      }
      if (analysisRef !== null) {
        const parameters = args[1];
        if (tool.name !== "run_product_command" || !isRecord(parameters) ||
            parameters.command_name !== "analysis.delete" || !isRecord(parameters.parameters) ||
            Object.keys(parameters.parameters).length !== 1 ||
            !isAllowedRequiredDeletionRef(parameters.parameters.analysis_ref, analysisRef)) {
          throw new ToolComplianceError("Explicit Analysis deletion may only invoke analysis.delete for the attached Analysis");
        }
        if (hasIssuedRequiredDeletion()) {
          throw new ToolComplianceError("Explicit Analysis deletion may only invoke the product bridge once per turn");
        }
        onRequiredDeletionIssued();
      }
      try {
        const result = await execute(...args);
        if (tool.name === "run_product_command" && isRecord(result) &&
            isRecord(result.details) && isRecord(result.details.event) &&
            ["failed", "cancelled", "unavailable"].includes(String(result.details.event.status))) {
          onProductCommandExecutionFailure();
        }
        return result;
      } catch (error) {
        if (tool.name === "run_product_command") onProductCommandExecutionFailure();
        throw error;
      }
    },
  } as T;
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

function hasRequiredDirectDeletion(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" &&
    event.command_name === "analysis.delete" &&
    (event.status === "succeeded" || event.status === "needs_confirmation"));
}

function hasProductCommandEvent(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command");
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
  let requiredDeleteForTurn = false;
  let requiredDeleteBridgeCallIssued = false;
  let unrequestedDeletionAttempted = false;
  let outOfPhaseTeachingWriteAttempted = false;
  let productCommandExecutionFailed = false;
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
    const systemPrompt = `${resolveSystemPrompt(request.system_prompt)}${MANDATORY_POLICY}${request.teaching_turn ? `\n\n${teachingEnvelopeInstruction(request.teaching_turn)}` : ""}${explicitMetricInstruction(explicitMetrics)}`;
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
    const requiredDeleteRef = requiredAnalysisDeleteRef(request);
    requiredDeleteForTurn = requiredDeleteRef !== null;
    const tools = [restrictTurnTools(
      createAnalysisSummaryTool(request.analysis_summary, { maxResultBytes: maxAnalysisResultBytes }),
      () => requiredDeleteRef,
      () => false,
      () => {},
      () => requiredDeleteBridgeCallIssued,
      () => { requiredDeleteBridgeCallIssued = true; },
      () => { unrequestedDeletionAttempted = true; },
      () => request.teaching_turn,
      () => outOfPhaseTeachingWriteAttempted,
      () => { outOfPhaseTeachingWriteAttempted = true; },
      () => { productCommandExecutionFailed = true; },
    )];
    tools.push(restrictTurnTools(
      createCoachKnowledgeTool(),
      () => requiredDeleteRef,
      () => false,
      () => {},
      () => requiredDeleteBridgeCallIssued,
      () => { requiredDeleteBridgeCallIssued = true; },
      () => { unrequestedDeletionAttempted = true; },
      () => request.teaching_turn,
      () => outOfPhaseTeachingWriteAttempted,
      () => { outOfPhaseTeachingWriteAttempted = true; },
      () => { productCommandExecutionFailed = true; },
    ) as never);
    if (request.tool_bridge) {
      tools.push(restrictTurnTools(
        createProductCommandTool(request.tool_bridge, hasAttachedAnalysis ? {
          excludedCommands: ["run.list", "analysis.create_from_run"],
        } : {}),
        () => requiredDeleteRef,
        () => false,
        () => {},
        () => requiredDeleteBridgeCallIssued,
        () => { requiredDeleteBridgeCallIssued = true; },
        () => { unrequestedDeletionAttempted = true; },
        () => request.teaching_turn,
        () => outOfPhaseTeachingWriteAttempted,
        () => { outOfPhaseTeachingWriteAttempted = true; },
        () => { productCommandExecutionFailed = true; },
      ) as never);
    }
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
      if (request.teaching_turn || requiredDeleteForTurn) return;
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
    if (requiredDeleteRef === null && unrequestedDeletionAttempted) {
      throw new ToolComplianceError("Analysis deletion requires an explicit reachable user request");
    }
    if (productCommandExecutionFailed) {
      throw new ToolComplianceError("Product command execution failed");
    }
    if (outOfPhaseTeachingWriteAttempted && request.teaching_turn) {
      const fallback = fallbackForTeachingTurn(request.teaching_turn).text;
      return successResponse(fallback, [], request.schema_version, collectToolEvents(currentMessages), request.run_id);
    }
    if (requiredDeleteRef !== null &&
        !hasRequiredDirectDeletion(collectToolEvents(currentMessages))) {
      if (hasProductCommandEvent(collectToolEvents(currentMessages))) {
        throw new ToolComplianceError("Explicit Analysis deletion did not create the required structured confirmation");
      }
      partialMessageStart = agent.state.messages.length;
      await agent.prompt([{
        role: "user" as const,
        content: [{
          type: "text" as const,
          text: toolCompliancePrompt(requiredDeleteRef),
        }],
        timestamp: Date.now(),
      }]);
      currentMessages = agent.state.messages.slice(turnMessageStart);
      if (productCommandExecutionFailed) {
        throw new ToolComplianceError("Product command execution failed");
      }
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
          safePartialReply(
            extractAssistantPartial(agent.state.messages.slice(partialMessageStart)),
            request,
            currentMessages,
            secrets,
          ),
          request.run_id,
        );
      }
      if (!hasRequiredDirectDeletion(collectToolEvents(currentMessages))) {
        throw new ToolComplianceError("Explicit Analysis deletion did not execute");
      }
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
        retryable: stopped || error instanceof ToolComplianceError || error instanceof EmptyAssistantReplyError,
      }),
      [],
      responseSchema,
      agent === null ? [] : collectToolEvents(agent.state.messages.slice(turnMessageStart)),
      agent === null || requiredDeleteForTurn || error instanceof ToolComplianceError
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
