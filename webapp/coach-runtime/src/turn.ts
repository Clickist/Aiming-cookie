import {
  COACH_RUNTIME_TURN_SCHEMA,
  COACH_RUNTIME_TURN_SCHEMA_V0,
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
  validateTeachingDraft,
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
  analysis_summary: string | null;
  system_prompt?: string;
  model: CoachRuntimeProviderProfile;
  tool_bridge?: import("./contracts.ts").CoachToolBridge;
  teaching_turn?: TeachingTurnContract;
};

type TurnOptions = {
  streamFn?: StreamFn;
};

class GroundingError extends Error {}
class ToolComplianceError extends Error {}

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

function parseLegacyProfile(raw: Record<string, unknown>): CoachRuntimeProviderProfile {
  if (!isRecord(raw.model)) {
    throw new Error("model config is required");
  }
  const model = raw.model;
  if (typeof model.base_url !== "string" || typeof model.api_key_env !== "string" || typeof model.model_id !== "string") {
    throw new Error("legacy model.base_url, model.api_key_env, and model.model_id are required");
  }
  const apiKey = process.env[model.api_key_env];
  if (!apiKey) {
    throw new Error("legacy provider credential is unavailable");
  }
  return parseProviderProfile({
    kind: "custom_openai_compatible",
    provider_name: "legacy-openai-compatible",
    base_url: model.base_url,
    api_key: apiKey,
    model_id: model.model_id,
  });
}

function parseRequest(raw: unknown): ParsedRequest {
  if (!isRecord(raw)) {
    throw new Error("Request must be a JSON object");
  }
  const schemaVersion = raw.schema_version;
  if (schemaVersion !== COACH_RUNTIME_TURN_SCHEMA_V0 && schemaVersion !== COACH_RUNTIME_TURN_SCHEMA_V1) {
    throw new Error(`Unsupported schema_version: ${String(schemaVersion)}`);
  }
  if (typeof raw.run_id !== "string" || typeof raw.user_id !== "string") {
    throw new Error("run_id and user_id are required strings");
  }

  const messages = parseMessages(raw.messages);
  const analysisSummaryValue = raw.analysis_summary;
  const analysisSummary: string | null =
    typeof analysisSummaryValue === "string" ? analysisSummaryValue : null;
  const systemPrompt = typeof raw.system_prompt === "string" ? raw.system_prompt : undefined;
  const toolBridge = raw.tool_bridge;
  if (toolBridge !== undefined && !isRecord(toolBridge)) throw new Error("tool_bridge must be an object");
  if (raw.teaching_turn !== undefined && schemaVersion !== COACH_RUNTIME_TURN_SCHEMA_V1) {
    throw new Error("teaching_turn requires coach_runtime_turn.v1");
  }
  const teachingTurn = raw.teaching_turn === undefined ? undefined : parseTeachingTurnContract(raw.teaching_turn);
  const model =
    schemaVersion === COACH_RUNTIME_TURN_SCHEMA_V0 &&
    (!isRecord(raw.model) || raw.model.kind === undefined)
      ? parseLegacyProfile(raw)
      : parseProviderProfile(raw.model);

  return {
    schema_version: schemaVersion,
    run_id: raw.run_id,
    user_id: raw.user_id,
    messages,
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
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!isRecord(message) || message.role !== "assistant") continue;
    if (message.stopReason === "error" || message.stopReason === "aborted") continue;
    const content = message.content;
    if (!Array.isArray(content)) continue;
    const parts = content
      .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
      .map((block) => (block as { text: string }).text);
    const text = parts.join("").trim();
    if (text.length > 0) return text;
  }
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

function responseSchemaFor(rawRequest: unknown): CoachRuntimeTurnSchema {
  return isRecord(rawRequest) && rawRequest.schema_version === COACH_RUNTIME_TURN_SCHEMA_V0
    ? COACH_RUNTIME_TURN_SCHEMA_V0
    : COACH_RUNTIME_TURN_SCHEMA;
}

function errorCode(error: unknown): string {
  if (error instanceof GroundingError) return "grounding_violation";
  if (error instanceof ToolComplianceError) return "tool_compliance_required";
  return error instanceof ProviderProfileError ? error.code : "turn_failed";
}

const STOPPED_USER_MESSAGE = "已停止生成。";

function userFacingErrorMessage(error: unknown, stopped: boolean): string {
  if (stopped) return STOPPED_USER_MESSAGE;
  if (error instanceof GroundingError) return "这次回复未通过内容校验，请重试。";
  if (error instanceof ToolComplianceError) return "这项操作未能安全完成，请重试。";
  if (error instanceof ProviderProfileError) return "Provider 配置不可用，请在设置中检查后重试。";
  return "Coach 暂时无法完成回复，请稍后重试。";
}

function extractBridgeSecrets(rawRequest: unknown): string[] {
  if (!isRecord(rawRequest) || !isRecord(rawRequest.tool_bridge)) return [];
  return [rawRequest.tool_bridge.bearer_token, rawRequest.tool_bridge.desktop_token]
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

const MANDATORY_POLICY = "\n\nMandatory Coach policy: use only registered product tools; when the user asks to delete an Analysis, call run_product_command with analysis.delete so the trusted UI/backend can create confirmation--a prose request to reply with confirmation is not an action; distinguish measured, deterministic_rule, research_supported, community_consensus, and experimental claims; never invent that an action succeeded; never reveal bridge tokens, paths, URLs, credentials, raw traces, arbitrary payloads, internal schema/table/tool/field names, raw cursors, or raw event/segment refs; write user-facing plain Chinese without exposing canonical timestamps.";
const GROUNDING_REPAIR_PROMPT = "Rewrite the answer to the current user in plain Chinese. Keep only exact quantities and metric identifiers present in the attached context or successful tool results. Keep every number paired with its source unit, or only a display alias that preserves that unit's meaning. Do not convert a count, duration, ratio, or any other quantity into another unit. Do not expose schema, table, tool, or field names, raw refs or cursors, canonical timestamps, or Markdown formatting. Describe evidence in user-facing language; without an explicit relative playback anchor, do not state an exact video time. Do not call tools again.";
const CONFIRMATION_REPAIR_GUIDANCE = " If a confirmation is already pending, state that the trusted confirmation UI is ready.";
const INSUFFICIENT_EVIDENCE_REPAIR_GUIDANCE = " If evidence is insufficient, say so without adding a quantity.";
const TRUSTED_CONFIRMATION_REPLY = "该操作已进入结构化确认流程。请在可信确认界面查看影响并选择确认或取消；文字回复不会执行操作。";
const TEACHING_FALLBACK_NOTE = "teaching_fallback";
const TEACHING_HOLD_NOTE = "teaching_hold";

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
      return execute(...args);
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

function hasPendingConfirmation(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" && event.status === "needs_confirmation");
}

function hasRequiredDeleteConfirmation(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" &&
    event.command_name === "analysis.delete" && event.status === "needs_confirmation");
}

function hasProductCommandEvent(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command");
}

function groundingRepairPrompt(request: ParsedRequest, currentMessages: unknown[]): string {
  const events = collectToolEvents(currentMessages);
  const unitPairs = repairSourceUnitPairs(request, currentMessages);
  const unitGuidance = unitPairs.length > 0
    ? ` The only source value/unit pairs available for copying are: ${unitPairs.join("; ")}. Omit every other quantity.`
    : "";
  return `${GROUNDING_REPAIR_PROMPT}${unitGuidance}${hasPendingConfirmation(events) ? CONFIRMATION_REPAIR_GUIDANCE : ""}${INSUFFICIENT_EVIDENCE_REPAIR_GUIDANCE}`;
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

function addMetricIdentifier(target: Set<string>, value: unknown): void {
  if (typeof value !== "string" || !/^[A-Za-z][A-Za-z0-9._-]*$/.test(value)) return;
  target.add(value);
  const tail = value.split(".").at(-1);
  if (tail) target.add(tail);
}

function metricAvailable(value: unknown): boolean {
  if (!isRecord(value)) return true;
  return !["unavailable", "unsupported", "undetermined", "insufficient_evidence"]
    .includes(String(value.availability ?? value.status ?? "").toLowerCase());
}

function collectNumericLeafKeys(value: unknown, target: Set<string>, depth = 0): void {
  if (!isRecord(value) || depth > 8) return;
  for (const [key, child] of Object.entries(value)) {
    if (typeof child === "number" && Number.isFinite(child)) addMetricIdentifier(target, key);
    else if (isRecord(child)) collectNumericLeafKeys(child, target, depth + 1);
  }
}

function projectionMetrics(projection: unknown): Set<string> {
  const metrics = new Set<string>();
  if (!isRecord(projection)) return metrics;
  const diagnosis = isRecord(projection.diagnosis) ? projection.diagnosis : {};
  const summary = isRecord(diagnosis.summary) ? diagnosis.summary : {};
  for (const [key, value] of Object.entries(summary)) {
    if (metricAvailable(value)) addMetricIdentifier(metrics, key);
  }
  if (Array.isArray(diagnosis.issues)) {
    for (const issue of diagnosis.issues) {
      if (!isRecord(issue) || !Array.isArray(issue.metric_refs)) continue;
      for (const ref of issue.metric_refs) addMetricIdentifier(metrics, ref);
    }
  }
  const processed = isRecord(projection.processed_events) ? projection.processed_events : {};
  if (Array.isArray(processed.tables)) {
    for (const table of processed.tables) {
      if (!isRecord(table) || !Array.isArray(table.field_catalog)) continue;
      for (const field of table.field_catalog) {
        if (!isRecord(field) || field.role !== "metric" || !metricAvailable(field)) continue;
        addMetricIdentifier(metrics, field.field_key);
        addMetricIdentifier(metrics, field.metric_key);
      }
    }
  }
  const runFacts = isRecord(projection.run_facts) ? projection.run_facts : {};
  collectNumericLeafKeys(runFacts.facts, metrics);
  return metrics;
}

function projectionMetricSets(analysisSummary: string | null): Set<string>[] {
  if (!analysisSummary) return [];
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
  const byAnalysis = new Map<string, Set<string>>();
  for (const projection of projections) {
    if (!isRecord(projection)) continue;
    const analysis = isRecord(projection.analysis_ref) ? projection.analysis_ref.analysis_id : null;
    const key = typeof analysis === "string" ? analysis : `projection:${byAnalysis.size}`;
    byAnalysis.set(key, projectionMetrics(projection));
  }
  return [...byAnalysis.values()];
}

function deterministicComparisonFallback(analysisSummary: string | null): string | null {
  const metricSets = projectionMetricSets(analysisSummary);
  if (metricSets.length < 2) return null;
  const common = new Set(metricSets[0]);
  for (const metricSet of metricSets.slice(1)) {
    for (const metric of [...common]) {
      if (!metricSet.has(metric)) common.delete(metric);
    }
  }
  if (common.size > 0) return null;
  return "这些上下文没有足够的共同可比指标，因此不能据此声称玩家表现发生变化。当前只能确认输入模式或证据范围不同；需要同场景、同模式、同指标、同单位、同校准和同质量的记录后再比较。";
}

function deterministicGroundingFallback(
  request: ParsedRequest,
  currentMessages: unknown[],
): string | null {
  if (hasPendingConfirmation(collectToolEvents(currentMessages))) {
    return TRUSTED_CONFIRMATION_REPLY;
  }
  const comparison = deterministicComparisonFallback(request.analysis_summary);
  if (comparison !== null) return comparison;
  if (!request.analysis_summary && collectToolEvents(currentMessages).length === 0) {
    return "当前没有附加可用的分析上下文，因此这不是针对某一局的诊断。可以先做轻松的眼手协调、慢速预瞄和肩颈放松；开始正式训练后再根据实际反馈调整。";
  }
  return null;
}

function parseToolPayloads(messages: unknown[]): Array<{ payload: unknown; commandName: string | null }> {
  const payloads: Array<{ payload: unknown; commandName: string | null }> = [];
  for (const message of messages) {
    if (!isRecord(message) || message.role !== "toolResult" || !Array.isArray(message.content)) continue;
    const event = isRecord(message.details) && isRecord(message.details.event)
      ? message.details.event
      : null;
    const commandName = event && typeof event.command_name === "string" ? event.command_name : null;
    for (const block of message.content) {
      if (!isRecord(block) || block.type !== "text" || typeof block.text !== "string") continue;
      try {
        const payload = JSON.parse(block.text);
        if (isRecord(payload) && typeof payload.status === "string" && payload.status !== "succeeded") continue;
        payloads.push({ payload, commandName });
      } catch {
        // Non-JSON tool text cannot authorize an exact quantitative claim.
      }
    }
  }
  return payloads;
}

function collectNumbersAndRefs(
  value: unknown,
  numbers: Set<string>,
  percentNumbers: Set<string>,
  refs: Set<string>,
  depth = 0,
  allowPercent = false,
): void {
  if (depth > 12) return;
  if (typeof value === "number" && Number.isFinite(value)) {
    numbers.add(String(value));
    numbers.add(value.toFixed(1));
    if (!Number.isInteger(value)) {
      for (let decimals = 2; decimals <= 4; decimals += 1) {
        numbers.add(value.toFixed(decimals));
      }
    }
    if (allowPercent && value >= 0 && value <= 1) {
      const percentage = value * 100;
      percentNumbers.add(String(percentage));
      percentNumbers.add(percentage.toFixed(1));
      for (let decimals = 2; decimals <= 4; decimals += 1) {
        percentNumbers.add(percentage.toFixed(decimals));
      }
    }
    return;
  }
  if (typeof value === "string") {
    if (/^[a-z][a-z0-9_]*:[A-Za-z0-9_.:@-]+$/i.test(value)) refs.add(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const child of value) {
      collectNumbersAndRefs(child, numbers, percentNumbers, refs, depth + 1, allowPercent);
    }
  } else if (isRecord(value)) {
    const ratioRecord = value.unit === "ratio";
    for (const [key, child] of Object.entries(value)) {
      collectNumbersAndRefs(
        child,
        numbers,
        percentNumbers,
        refs,
        depth + 1,
        ratioRecord || /(?:^|[._])(ratio|coverage)(?:$|[._])/i.test(key),
      );
    }
  }
}

function collectToolCollectionCounts(
  value: unknown,
  numbers: Set<string>,
  depth = 0,
): void {
  if (depth > 12) return;
  if (Array.isArray(value)) {
    numbers.add(String(value.length));
    for (const child of value) collectToolCollectionCounts(child, numbers, depth + 1);
  } else if (isRecord(value)) {
    for (const child of Object.values(value)) {
      collectToolCollectionCounts(child, numbers, depth + 1);
    }
  }
}

const UNIT_ALIASES = new Map([
  ["%", "percent"], ["ratio", "percent"], ["percent", "percent"],
  ["count", "count"], ["counts", "count"], ["次", "count"],
  ["ms", "ms"], ["millisecond", "ms"], ["milliseconds", "ms"], ["毫秒", "ms"],
  ["px", "px"], ["pixel", "px"], ["pixels", "px"], ["像素", "px"],
  ["s", "second"], ["sec", "second"], ["second", "second"], ["seconds", "second"], ["秒", "second"],
  ["min", "minute"], ["minute", "minute"], ["minutes", "minute"], ["分", "minute"], ["分钟", "minute"],
  ["hour", "hour"], ["hours", "hour"], ["小时", "hour"],
  ["frame", "frame"], ["frames", "frame"], ["帧", "frame"],
  ["group", "group"], ["groups", "group"], ["组", "group"],
  ["round", "round"], ["rounds", "round"], ["轮", "round"],
  ["cm/360", "cm_per_360"], ["cm_per_360", "cm_per_360"],
  ["deg", "degree"], ["degree", "degree"], ["degrees", "degree"], ["度", "degree"],
  ["fps", "fps"], ["hz", "hz"], ["px/s", "px_per_second"],
]);
const UNIT_CLAIM_PATTERN = /(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*(%|cm\/360|px\/s|milliseconds?|millisecond|seconds?|second|minutes?|minute|counts?|count|frames?|frame|groups?|group|rounds?|round|degrees?|degree|hours?|hour|px|ms|fps|hz|sec|min|deg|cm|次|个|条|毫秒|分钟|小时|像素|帧|组|轮|秒|分|度)/gi;
const PRESCRIPTION_CONTEXT = /(?:训练|练习|热身|重复|做|休息|train(?:ing)?|practice|warm[- ]?up|repeat|rest)/i;
const PRESCRIPTION_DOSE_UNITS = new Set(["minute", "second", "hour", "count", "group", "round"]);

function normalizedUnit(value: string): string | null {
  return UNIT_ALIASES.get(value.toLowerCase()) ?? null;
}

function numericRepresentations(value: number): string[] {
  const representations = new Set([String(value), value.toFixed(1)]);
  if (!Number.isInteger(value)) {
    for (let decimals = 2; decimals <= 4; decimals += 1) {
      representations.add(value.toFixed(decimals));
    }
  }
  return [...representations];
}

function addUnitClaim(target: Map<string, Set<string>>, value: number, unit: string): void {
  for (const representation of numericRepresentations(value)) {
    const units = target.get(representation) ?? new Set<string>();
    units.add(unit);
    target.set(representation, units);
  }
}

function collectStructuredUnitClaims(value: unknown, target: Map<string, Set<string>>, depth = 0): void {
  if (depth > 12) return;
  if (Array.isArray(value)) {
    for (const child of value) collectStructuredUnitClaims(child, target, depth + 1);
    return;
  }
  if (!isRecord(value)) return;
  const unit = typeof value.unit === "string" ? normalizedUnit(value.unit) : null;
  if (unit !== null && typeof value.value === "number" && Number.isFinite(value.value)) {
    addUnitClaim(target, value.value, unit);
    if (unit === "percent" && value.value >= 0 && value.value <= 1) {
      addUnitClaim(target, value.value * 100, unit);
    }
  }
  for (const child of Object.values(value)) {
    collectStructuredUnitClaims(child, target, depth + 1);
  }
}

function collectSourceUnitPairs(value: unknown, target: Set<string>, depth = 0): void {
  if (depth > 12 || target.size >= 24) return;
  if (Array.isArray(value)) {
    for (const child of value) collectSourceUnitPairs(child, target, depth + 1);
    return;
  }
  if (!isRecord(value)) return;
  const unit = typeof value.unit === "string" ? normalizedUnit(value.unit) : null;
  if (unit !== null && typeof value.value === "number" && Number.isFinite(value.value)) {
    target.add(`${String(value.value)} ${value.unit.toLowerCase()}`);
  }
  for (const child of Object.values(value)) {
    collectSourceUnitPairs(child, target, depth + 1);
  }
}

function repairSourceUnitPairs(request: ParsedRequest, currentMessages: unknown[]): string[] {
  const pairs = new Set<string>();
  if (request.analysis_summary) {
    try {
      collectSourceUnitPairs(JSON.parse(request.analysis_summary), pairs);
    } catch {
      // Malformed summaries are already excluded from grounding sources.
    }
  }
  for (const { payload } of parseToolPayloads(currentMessages)) {
    collectSourceUnitPairs(payload, pairs);
  }
  return [...pairs].slice(0, 24);
}

function collectTextUnitClaims(value: string, target: Map<string, Set<string>>): void {
  for (const match of value.matchAll(UNIT_CLAIM_PATTERN)) {
    const numeric = Number(match[1]);
    const unit = normalizedUnit(match[2]);
    if (Number.isFinite(numeric) && unit !== null) addUnitClaim(target, numeric, unit);
  }
}

function collectPrescriptionUnitClaims(value: string, target: Map<string, Set<string>>): void {
  for (const sentence of value.split(/[。！？!?\n]/)) {
    if (PRESCRIPTION_CONTEXT.test(sentence)) collectTextUnitClaims(sentence, target);
  }
}

function hasUnrequestedPrescriptionDose(value: string, userPrescriptionUnits: Map<string, Set<string>>): boolean {
  return value.split(/[。！？!?\n]/).some((sentence) => {
    if (!PRESCRIPTION_CONTEXT.test(sentence)) return false;
    return [...sentence.matchAll(UNIT_CLAIM_PATTERN)].some((match) => {
      const numeric = Number(match[1]);
      const unit = normalizedUnit(match[2]);
      return Number.isFinite(numeric) && unit !== null && PRESCRIPTION_DOSE_UNITS.has(unit) &&
        !userPrescriptionUnits.get(match[1])?.has(unit);
    });
  });
}

function containsEvidenceLimitation(value: unknown, depth = 0): boolean {
  if (depth > 12) return false;
  if (Array.isArray(value)) return value.some((child) => containsEvidenceLimitation(child, depth + 1));
  if (!isRecord(value)) {
    return typeof value === "string" && /(?:unavailable|unsupported|insufficient[_ ]evidence|not[_ ]available|missing|outcome_only)/i.test(value);
  }
  return Object.entries(value).some(([key, child]) => {
    const normalizedKey = key.toLowerCase();
    if (normalizedKey.includes("limitation") && Array.isArray(child) && child.length > 0) return true;
    if (["availability", "status", "support_status", "mode"].includes(normalizedKey) &&
        typeof child === "string" && /^(?:unavailable|unsupported|insufficient[_ ]evidence|not[_ ]available|missing|outcome_only)$/i.test(child)) {
      return true;
    }
    return containsEvidenceLimitation(child, depth + 1);
  });
}

function containsUnsupportedCausalClaim(value: string): boolean {
  const sentences = value.split(/[。！？!?\n]/);
  return sentences.some((sentence, index) => {
    const window = `${sentence} ${sentences[index + 1] ?? ""}`;
    const evidenceLimitation = /(?:证据|视觉|视频|信号|数据|指标|evidence|visual|video|signal|metric)[^,，;；]{0,20}(?:缺失|不可用|不支持|不足|unavailable|unsupported|missing)/i.test(window) ||
      /(?:只有|仅有|仅|only)[^,，;；]{0,20}(?:结果|outcome)/i.test(window);
    if (!evidenceLimitation) {
      return false;
    }
    if (/(?:无法|不能|不可|不应|不要|不(?:能)?)[\s]*(?:说明|表明|意味着|证明|归因|indicates?|means?|proves?)/i.test(window)) return false;
    const causal = /(?:说明|表明|意味着|导致|归因于|证明|indicates?|means?|caused?\s+by)/i.test(window);
    const playerDeficit = /(?:你的|玩家(?:的)?|you(?:r)?|player(?:'s)?)[^,，;；]{0,24}(?:不足|欠缺|薄弱|差|问题|缺陷|失败|不够|weak|deficient|poor|lack(?:ing)?|insufficient)|(?:视觉搜索|能力|控制|追踪|瞄准|visual search|aiming control|tracking)[^,，;；]{0,16}(?:不足|欠缺|薄弱|差|问题|缺陷|失败|不够|weak|deficient|poor|lack(?:ing)?|insufficient)/i.test(window);
    return causal && playerDeficit;
  });
}

function textNumberClaims(value: string): Array<{ value: string; percent: boolean }> {
  return [...value.matchAll(/(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*(%)?/g)]
    .map((match) => ({ value: match[1], percent: match[2] === "%" }));
}

const INTERNAL_PROTOCOL_EGRESS_PATTERNS = [
  /\bcoach_(?:diagnostic|turn)_context(?:\.v\d+)?\b/i,
  /\b(?:schema_version|processed[_ ]event[_ ]table|field_catalog|table_ref|event_ref|segment_ref|segment_id|segment_kind|start_ms|end_ms|focus_start_ms|focus_end_ms|available_channels|signal_window(?:\.v\d+)?|result_ref|next_cursor|canonical_time(?:_window)?|run_product_command)\b/i,
  /\banalysis\.(?:evidence|events|metrics|run_facts|outcomes)\.[a-z0-9_]+\b/i,
  /\banalysis:[1-9]\d*:segment:[A-Za-z0-9_.:@-]+\b/i,
  /\b(?:L0|L1|L2|L3|DTO)\b/,
  /(?<!\d)\d{12,}(?!\d)/,
  /(?:```|^\s*\|.*\|\s*$)/m,
];

function normalizeUserFacingText(value: string): string {
  return value
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/gm, "")
    .replace(/\*\*/g, "")
    .replace(/`([^`\n]+)`/g, "$1")
    .trim();
}

function validateUserFacingEgress(value: string): void {
  if (INTERNAL_PROTOCOL_EGRESS_PATTERNS.some((pattern) => pattern.test(value))) {
    throw new GroundingError("Coach response was rejected because it exposed internal protocol vocabulary");
  }
}

function validateGroundedReply(
  reply: string,
  request: ParsedRequest,
  currentMessages: unknown[],
): void {
  validateUserFacingEgress(reply);
  const userClaims = textNumberClaims(request.messages.at(-1)?.content ?? "");
  const allowedNumbers = new Set(userClaims.map((claim) => claim.value));
  const allowedPercentNumbers = new Set(
    userClaims.filter((claim) => claim.percent).map((claim) => claim.value),
  );
  const allowedRefs = new Set<string>();
  const allowedUnits = new Map<string, Set<string>>();
  collectTextUnitClaims(request.messages.at(-1)?.content ?? "", allowedUnits);
  const userPrescriptionUnits = new Map<string, Set<string>>();
  collectPrescriptionUnitClaims(request.messages.at(-1)?.content ?? "", userPrescriptionUnits);
  let summaryPayload: unknown = null;
  if (request.analysis_summary) {
    try {
      summaryPayload = JSON.parse(request.analysis_summary);
      collectNumbersAndRefs(
        summaryPayload, allowedNumbers, allowedPercentNumbers, allowedRefs,
      );
      collectStructuredUnitClaims(summaryPayload, allowedUnits);
    } catch {
      summaryPayload = null;
    }
  }
  for (const source of request.teaching_turn?.ratio_sources ?? []) {
    collectNumbersAndRefs({ value: source.value, unit: "ratio" }, allowedNumbers, allowedPercentNumbers, allowedRefs);
    collectStructuredUnitClaims({ value: source.value, unit: "ratio" }, allowedUnits);
  }
  const toolPayloads = parseToolPayloads(currentMessages);
  if (toolPayloads.length > 0) allowedNumbers.add(String(toolPayloads.length));
  for (const { payload } of toolPayloads) {
    collectNumbersAndRefs(payload, allowedNumbers, allowedPercentNumbers, allowedRefs);
    collectStructuredUnitClaims(payload, allowedUnits);
    collectToolCollectionCounts(payload, allowedNumbers);
  }

  let quantitativeText = reply;
  for (const ref of [...allowedRefs].sort((a, b) => b.length - a.length)) {
    quantitativeText = quantitativeText.replaceAll(ref, "");
  }
  quantitativeText = quantitativeText
    .replace(/\bAnalysis\s+\d+\b/gi, "")
    .replace(/\banalysis:\d+\b/gi, "")
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?\d+[.)、]\s+/, ""))
    .join("\n");
  const ungroundedQuantity = textNumberClaims(quantitativeText).find((claim) => (
    claim.percent
      ? !allowedPercentNumbers.has(claim.value)
      : !allowedNumbers.has(claim.value)
  ));
  if (ungroundedQuantity) {
    throw new GroundingError("Coach response was rejected because it contained an ungrounded quantity");
  }
  const ungroundedUnit = [...quantitativeText.matchAll(UNIT_CLAIM_PATTERN)].find((match) => {
    const numeric = Number(match[1]);
    const unit = normalizedUnit(match[2]);
    if (!Number.isFinite(numeric) || unit === null) return false;
    return !allowedUnits.get(match[1])?.has(unit);
  });
  if (ungroundedUnit) {
    throw new GroundingError("Coach response was rejected because it paired a quantity with an unsupported unit");
  }
  if (hasUnrequestedPrescriptionDose(quantitativeText, userPrescriptionUnits)) {
    throw new GroundingError("Coach response was rejected because it used an observed quantity as an unrequested prescription dose");
  }
  const sourceHasLimitation = containsEvidenceLimitation(summaryPayload) ||
    toolPayloads.some(({ payload }) => containsEvidenceLimitation(payload));
  if (sourceHasLimitation && containsUnsupportedCausalClaim(quantitativeText)) {
    throw new GroundingError("Coach response was rejected because it attributed unavailable evidence to player ability");
  }

  const replyMetrics = new Set(
    [...reply.matchAll(/[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+/g)]
      .map((match) => match[0])
      .filter(metricLike),
  );
  if (replyMetrics.size === 0) return;
  const metricSets = projectionMetricSets(request.analysis_summary);
  const allowedMetrics = metricSets.length === 0
    ? new Set<string>()
    : new Set(metricSets[0]);
  for (const metricSet of metricSets.slice(1)) {
    for (const metric of [...allowedMetrics]) {
      if (!metricSet.has(metric)) allowedMetrics.delete(metric);
    }
  }
  for (const { payload, commandName } of toolPayloads) {
    if (metricSets.length > 1 && !["analysis.compare", "analysis.evidence.compare"].includes(commandName ?? "")) {
      continue;
    }
    const toolMetrics = new Set<string>();
    const visit = (value: unknown, depth = 0): void => {
      if (depth > 12) return;
      if (typeof value === "string" && metricLike(value)) addMetricIdentifier(toolMetrics, value);
      else if (Array.isArray(value)) value.forEach((child) => visit(child, depth + 1));
      else if (isRecord(value)) {
        for (const [key, child] of Object.entries(value)) {
          if (metricLike(key)) addMetricIdentifier(toolMetrics, key);
          visit(child, depth + 1);
        }
      }
    };
    visit(payload);
    for (const metric of toolMetrics) allowedMetrics.add(metric);
  }
  if ([...replyMetrics].some((metric) => !allowedMetrics.has(metric))) {
    throw new GroundingError("Coach response was rejected because it cited an unavailable metric");
  }
}

function safePartialReply(
  value: string | null,
  request: ParsedRequest,
  currentMessages: unknown[],
  secrets: string[],
): string | null {
  const redacted = normalizeUserFacingText(redactRuntimeSecrets(value ?? "", secrets));
  if (!redacted) return null;
  try {
    validateGroundedReply(redacted, request, currentMessages);
    return redacted;
  } catch (error) {
    if (error instanceof GroundingError) return null;
    throw error;
  }
}

export async function runCoachTurn(rawRequest: unknown, options: TurnOptions = {}): Promise<CoachRuntimeTurnResponse> {
  const responseSchema = responseSchemaFor(rawRequest);
  const responseRunId = isRecord(rawRequest) && typeof rawRequest.run_id === "string"
    ? rawRequest.run_id
    : null;
  const secrets = [...extractRuntimeSecrets(rawRequest), ...extractBridgeSecrets(rawRequest)];
  let agent: {
    prompt: (input: unknown) => Promise<void>;
    abort: () => void;
    state: { messages: unknown[]; tools: Array<{ name: string }> };
  } | null = null;
  let activeRunId: string | null = null;
  let turnMessageStart = 0;
  let partialMessageStart = 0;
  let requiredDeleteForTurn = false;
  let groundingRepairActive = false;
  let groundingRepairToolAttempted = false;
  let requiredDeleteBridgeCallIssued = false;
  let unrequestedDeletionAttempted = false;
  let outOfPhaseTeachingWriteAttempted = false;
  let teachingFallbackUsed = false;
  let teachingHoldUsed = false;
  let parsedRequest: ParsedRequest | null = null;
  try {
    const request = parseRequest(rawRequest);
    parsedRequest = request;
    if (request.teaching_turn && teachingTurnRequiresLocalFallback(request.teaching_turn)) {
      const fallback = fallbackForTeachingTurn(request.teaching_turn).text;
      validateGroundedReply(fallback, request, []);
      return successResponse(
        fallback,
        [TEACHING_HOLD_NOTE],
        request.schema_version,
        [],
        request.run_id,
      );
    }
    const resolved = await resolveProviderModel(request.model);
    const { Agent } = (await loadPiAgent()) as {
      Agent: new (opts: Record<string, unknown>) => {
        prompt: (input: unknown) => Promise<void>;
        abort: () => void;
        state: { messages: unknown[]; tools: Array<{ name: string }> };
      };
    };
    const { history, prompt } = splitConversation(request.messages, resolved.model);
    turnMessageStart = history.length;
    partialMessageStart = history.length;
    const streamFn = options.streamFn ?? createModelsStreamFn(resolved.models);
    const requiredDeleteRef = requiredAnalysisDeleteRef(request);
    requiredDeleteForTurn = requiredDeleteRef !== null;
    const tools = [restrictTurnTools(
      createAnalysisSummaryTool(request.analysis_summary),
      () => requiredDeleteRef,
      () => groundingRepairActive,
      () => { groundingRepairToolAttempted = true; },
      () => requiredDeleteBridgeCallIssued,
      () => { requiredDeleteBridgeCallIssued = true; },
      () => { unrequestedDeletionAttempted = true; },
      () => request.teaching_turn,
      () => outOfPhaseTeachingWriteAttempted,
      () => { outOfPhaseTeachingWriteAttempted = true; },
    )];
    if (request.schema_version === COACH_RUNTIME_TURN_SCHEMA_V1) {
      tools.push(restrictTurnTools(
        createCoachKnowledgeTool(),
        () => requiredDeleteRef,
        () => groundingRepairActive,
        () => { groundingRepairToolAttempted = true; },
        () => requiredDeleteBridgeCallIssued,
        () => { requiredDeleteBridgeCallIssued = true; },
        () => { unrequestedDeletionAttempted = true; },
        () => request.teaching_turn,
        () => outOfPhaseTeachingWriteAttempted,
        () => { outOfPhaseTeachingWriteAttempted = true; },
      ) as never);
    }
    if (request.schema_version === COACH_RUNTIME_TURN_SCHEMA_V1 && request.tool_bridge) {
      tools.push(restrictTurnTools(
        createProductCommandTool(request.tool_bridge),
        () => requiredDeleteRef,
        () => groundingRepairActive,
        () => { groundingRepairToolAttempted = true; },
        () => requiredDeleteBridgeCallIssued,
        () => { requiredDeleteBridgeCallIssued = true; },
        () => { unrequestedDeletionAttempted = true; },
        () => request.teaching_turn,
        () => outOfPhaseTeachingWriteAttempted,
        () => { outOfPhaseTeachingWriteAttempted = true; },
      ) as never);
    }
    agent = new Agent({
      streamFn,
      initialState: {
        systemPrompt: `${resolveSystemPrompt(request.system_prompt)}${MANDATORY_POLICY}${request.teaching_turn ? `\n\n${teachingEnvelopeInstruction(request.teaching_turn)}` : ""}`,
        model: resolved.model,
        tools,
        messages: history,
      },
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
    if (outOfPhaseTeachingWriteAttempted && request.teaching_turn) {
      const fallback = fallbackForTeachingTurn(request.teaching_turn).text;
      validateGroundedReply(fallback, request, currentMessages);
      return successResponse(fallback, [TEACHING_FALLBACK_NOTE], request.schema_version, collectToolEvents(currentMessages), request.run_id);
    }
    if (requiredDeleteRef !== null && !hasRequiredDeleteConfirmation(collectToolEvents(currentMessages))) {
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
      if (!hasRequiredDeleteConfirmation(collectToolEvents(currentMessages))) {
        throw new ToolComplianceError("Explicit Analysis deletion did not create the required structured confirmation");
      }
    }
    const toolEvents = collectToolEvents(currentMessages);
    if (hasPendingConfirmation(toolEvents)) {
      return successResponse(TRUSTED_CONFIRMATION_REPLY, [], request.schema_version, toolEvents, request.run_id);
    }
    const rawReply = redactRuntimeSecrets(extractAssistantReply(currentMessages), secrets);
    let reply = normalizeUserFacingText(rawReply);
    if (request.teaching_turn) {
      const draft = parseTeachingProviderDraft(rawReply);
      if (draft === null || !validateTeachingDraft(request.teaching_turn, draft).ok) {
        reply = fallbackForTeachingTurn(request.teaching_turn).text;
        teachingFallbackUsed = true;
      } else {
        reply = normalizeUserFacingText(draft.text);
        teachingHoldUsed = teachingTurnHoldsState(request.teaching_turn);
      }
    }
    try {
      validateGroundedReply(reply, request, currentMessages);
    } catch (error) {
      if (!(error instanceof GroundingError)) throw error;
      if (request.teaching_turn) {
        reply = fallbackForTeachingTurn(request.teaching_turn).text;
        validateGroundedReply(reply, request, currentMessages);
        return successResponse(reply, [TEACHING_FALLBACK_NOTE], request.schema_version, collectToolEvents(currentMessages), request.run_id);
      }
      partialMessageStart = agent.state.messages.length;
      groundingRepairActive = true;
      await agent.prompt([{
        role: "user" as const,
        content: [{
          type: "text" as const,
          text: groundingRepairPrompt(request, currentMessages),
        }],
        timestamp: Date.now(),
      }]);
      currentMessages = agent.state.messages.slice(turnMessageStart);
      if (groundingRepairToolAttempted) {
        throw new ToolComplianceError("Grounding repair attempted to call a tool");
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
      reply = normalizeUserFacingText(redactRuntimeSecrets(extractAssistantReply(currentMessages), secrets));
      try {
        validateGroundedReply(reply, request, currentMessages);
      } catch (repairError) {
        if (!(repairError instanceof GroundingError)) throw repairError;
        const fallback = deterministicGroundingFallback(request, currentMessages);
        if (fallback === null) throw repairError;
        reply = fallback;
        validateGroundedReply(reply, request, currentMessages);
      }
    }
    return successResponse(
      reply,
      teachingFallbackUsed ? [TEACHING_FALLBACK_NOTE] : teachingHoldUsed ? [TEACHING_HOLD_NOTE] : [],
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
        retryable: stopped || error instanceof GroundingError || error instanceof ToolComplianceError,
      }),
      [],
      responseSchema,
      agent === null ? [] : collectToolEvents(agent.state.messages.slice(turnMessageStart)),
      agent === null || requiredDeleteForTurn || error instanceof GroundingError || error instanceof ToolComplianceError
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
    if (activeRunId !== null) {
      activeTurns.delete(activeRunId);
      stopRequested.delete(activeRunId);
    }
  }
}

export async function runCoachTurnWithFakeStream(
  rawRequest: unknown,
  replyText?: string,
): Promise<CoachRuntimeTurnResponse> {
  return runCoachTurn(rawRequest, { streamFn: createFakeStreamFn(replyText) });
}
