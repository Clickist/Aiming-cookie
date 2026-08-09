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
  validateTeachingDirectResponse,
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
  onComplete?: (timing: CoachTurnTiming) => Promise<void> | void;
};

export type CoachPartialRevision = {
  revision: number;
  text: string;
  elapsed_ms: number;
  provider_rounds: number;
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

type GroundingReason =
  | "internal_vocabulary"
  | "hits_accuracy_dismissal"
  | "target_relative_claim"
  | "unsupported_practice"
  | "analogy_disclaimer"
  | "analogy_not_direct"
  | "missing_analogy"
  | "partial_problem_free"
  | "invented_comparison"
  | "requested_metric_denial"
  | "quantity_conversion"
  | "ungrounded_quantity"
  | "unit_mismatch"
  | "prescription_dose"
  | "unsupported_causal_claim"
  | "unavailable_metric"
  | "missing_answer_target";

class GroundingError extends Error {
  constructor(
    readonly reason: GroundingReason,
    message: string,
    readonly details: string[] = [],
  ) {
    super(message);
  }
}
class ToolComplianceError extends Error {}
class EmptyAssistantReplyError extends Error {}

const activeTurns = new Map<string, { abort: () => void }>();
const stopRequested = new Set<string>();
const TEACHING_INTERRUPTION = /[?？]|(?:为什么|为何|优先|没回答|不对|不是|纠正|解释|说明|比较|视频|噪声|限制|剂量|组数|停止条件|复测|练习方向|训练方向|今天能执行|直接(?:给|告诉)|(?:不要|别)先问|感受)/;
const DIRECT_PRACTICE_REQUEST = /(?:练习方向|训练方向|今天能执行|直接(?:给|告诉)|(?:不要|别)先问|感受|怎么判断|判断标准|练(?:得)?对|命中率|准确率|是否打中|有没有打中)/;
const DIRECT_TEACHING_INTERRUPTION_INSTRUCTION =
  "\n\nDirect teaching interruption: the newest user message asks for an explanation, correction, limitation, practice direction, dose, groups, stopping condition, or retest. " +
  "Answer that request directly in the usual two-field envelope. Do not ask a follow-up question or repeat the scripted phase question; do not advance the teaching phase or create a training record. " +
  "Keep the existing evidence, dose, confirmation, and tool boundaries. Do not invent a scenario, dose, group count, stopping condition, cause, or retest result.";
const DIRECT_TEACHING_INTERRUPTION_FALLBACK =
  "现有分析还不能确定原因，所以我不会把练习提示说成结论。它只是现在可以试的方向。";

function directTeachingInterruptionFallback(
  contract: TeachingTurnContract,
  userContent: string,
): string {
  if (!DIRECT_PRACTICE_REQUEST.test(userContent) || contract.cue === null) {
    return DIRECT_TEACHING_INTERRUPTION_FALLBACK;
  }
  const cue = /[。.!！]$/.test(contract.cue) ? contract.cue : `${contract.cue}。`;
  const dose = contract.approved_dose === null
    ? ""
    : /[。.!！]$/.test(contract.approved_dose)
      ? contract.approved_dose
      : `${contract.approved_dose}。`;
  return `今天先只练一个方向：${cue}${dose}训练时继续记录命中率，不要为了动作提示忽略是否打中；练完若要判断有没有帮助，按相同场景和设置复测。`;
}

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
  if (typeof model.base_url !== "string" || typeof model.api_key_env !== "string"
    || typeof model.model_id !== "string" || !Number.isSafeInteger(model.context_window)
    || model.context_window <= 0 || !Number.isSafeInteger(model.max_tokens) || model.max_tokens <= 0) {
    throw new Error("legacy model requires verified base_url, credential, model_id, context_window, and max_tokens");
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
    context_window: model.context_window,
    max_tokens: model.max_tokens,
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
    if (message.stopReason === "stop" || message.stopReason === "length") {
      sawEmptyAssistant = true;
    }
  }
  if (sawEmptyAssistant) throw new EmptyAssistantReplyError("Provider returned an empty assistant reply");
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
const GROUNDING_REPAIR_PROMPT = "Rewrite the answer to the current user in plain Chinese. Fix only the failed requirement described below. Do not add a fact, quantity, cause, or verdict that is unavailable in the attached context or successful tool results. Do not expose schema, table, tool, or field names, raw refs or cursors, canonical timestamps, or Markdown formatting. Do not call tools again.";
const CONFIRMATION_REPAIR_GUIDANCE = " If a confirmation is already pending, state that the trusted confirmation UI is ready.";
const INSUFFICIENT_EVIDENCE_REPAIR_GUIDANCE = " If evidence is insufficient, say so without adding a quantity.";
const TRUSTED_CONFIRMATION_REPLY = "操作准备好了。请在确认界面查看影响并选择确认或取消；聊天里回复“确认”不会执行。";
const TEACHING_FALLBACK_NOTE = "teaching_fallback";
const TEACHING_HOLD_NOTE = "teaching_hold";
const GROUNDING_FALLBACK_NOTE = "grounding_fallback";

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

function hasPendingConfirmation(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" && event.status === "needs_confirmation");
}

function hasRequiredDeleteConfirmation(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" &&
    event.command_name === "analysis.delete" && event.status === "needs_confirmation");
}

function hasRequiredDirectDeletion(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command" &&
    event.command_name === "analysis.delete" && event.status === "succeeded" &&
    isRecord(event) && (event as Record<string, unknown>).authorization_source === "explicit_user_request");
}

function hasProductCommandEvent(events: CoachRuntimeToolEvent[]): boolean {
  return events.some((event) => event.type === "product_command");
}

function groundingReasonGuidance(
  error: GroundingError,
  request: ParsedRequest,
  currentMessages: unknown[],
): string {
  switch (error.reason) {
    case "internal_vocabulary":
      return " Remove internal protocol vocabulary and describe the same user-facing meaning naturally.";
    case "hits_accuracy_dismissal":
      return " Do not tell the user to ignore hits or accuracy; retain them as outcome evidence alongside any movement observation.";
    case "target_relative_claim":
      return " The context lacks target-relative facts. Describe only the measured movement ending and reverse corrections; do not mention a landing point, overshoot, undershoot, reaching, aligning with, crossing, or returning to a target.";
    case "unsupported_practice":
      return " This partial analysis can describe findings but cannot create a cue or practice method because no approved teaching cue is attached. State the observation and the evidence limit only.";
    case "analogy_disclaimer":
      return " Keep one short, natural analogy, but do not explain that it is an analogy or add a visible evidence disclaimer after it.";
    case "analogy_not_direct":
      return " Keep one short, completed analogy that explains an observation already present in the attached context. Remove every follow-up question, new task or scenario, and practice cue.";
    case "missing_analogy":
      return " The user requested one short, natural analogy. Complete it directly using an observation already present in the attached context. Do not add a disclaimer, follow-up question, new task or scenario, or practice cue.";
    case "partial_problem_free":
      return " Partial evidence does not prove that an unmeasured movement phase is good, normal, or problem-free. State only the observed finding and its limit.";
    case "invented_comparison":
      return " Only one Analysis is attached. Do not invent a previous run, a second test, or different conditions to compare.";
    case "requested_metric_denial":
      return " Use the available values of the metrics the user named. Missing thresholds limit evaluation, not whether those values can be quoted.";
    case "quantity_conversion":
    case "ungrounded_quantity":
    case "unit_mismatch":
    case "prescription_dose": {
      const unitPairs = repairSourceUnitPairs(request, currentMessages);
      const available = unitPairs.length > 0
        ? ` The only source value/unit pairs available for exact copying are: ${unitPairs.join("; ")}.`
        : " There is no source quantity available to copy.";
      return `${available} Use only the exact source number and unit verbatim, preserving the source unit. Do not convert or approximate it, round it, or repurpose it as a practice dose; omit every other quantity.`;
    }
    case "unsupported_causal_claim":
      return " Remove the unsupported cause or ability judgment. Keep the measured observation and describe any cause only as unconfirmed.";
    case "unavailable_metric":
      return " Remove metric identifiers that are unavailable in the attached context and answer with available user-facing observations only.";
    case "missing_answer_target":
      return ` Answer every omitted topic explicitly and separately: ${error.details.join(", ")}. Keep each answer within the available evidence.`;
  }
}

function groundingRepairPrompt(
  request: ParsedRequest,
  currentMessages: unknown[],
  error: GroundingError,
): string {
  const events = collectToolEvents(currentMessages);
  return `${GROUNDING_REPAIR_PROMPT}${groundingReasonGuidance(error, request, currentMessages)}${hasPendingConfirmation(events) ? CONFIRMATION_REPAIR_GUIDANCE : ""}${INSUFFICIENT_EVIDENCE_REPAIR_GUIDANCE}`;
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

function explicitMetricFallback(metrics: RequestedMetricValue[]): string | null {
  if (metrics.length === 0) return null;
  return `当前可引用的数值是${metrics.map(({ key, value }) => `${key} 为 ${String(value)}`).join("，")}。没有阈值或基线只限制对好坏和优先级的判断，不会限制引用这些数值。`;
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
  return "这几份记录里没有能直接对照的指标，所以现在不能判断表现有没有变化。要比较，需要场景、模式、指标、单位、校准和记录质量都能对上。";
}

function deterministicGroundingFallback(
  request: ParsedRequest,
  currentMessages: unknown[],
  reason?: GroundingReason,
): string | null {
  if (hasPendingConfirmation(collectToolEvents(currentMessages))) {
    return TRUSTED_CONFIRMATION_REPLY;
  }
  const targets = requestedAnswerTargets(request.messages.at(-1)?.content ?? "");
  if (targets.length > 0 && reason !== undefined) {
    const tensionBoundary = request.analysis_summary
      ? "紧张：现有分析不能判断是不是紧张导致。"
      : "紧张：当前没有附加本局分析，不能据此判断是不是紧张导致。";
    const answers: Record<AnswerTarget, string> = {
      tension: tensionBoundary,
      hardware: "鼠标：目前没有证据表明更换设备会解决这个问题。",
      transfer: "迁移：能否迁移到其他 FPS，需要单独复测。",
    };
    return targets.map((target) => answers[target]).join("");
  }
  if (reason === "target_relative_claim" &&
      analysisHasLimitation(request.analysis_summary, "target_relative_facts_unavailable")) {
    return "当前数据缺少目标相对事实，不能判断是否到位、过冲或欠冲。";
  }
  if ((reason === "missing_analogy" || reason === "analogy_not_direct") &&
      analysisHasLimitation(request.analysis_summary, "target_relative_facts_unavailable")) {
    return "就像只看到刹车过程、没看到停车线，当前数据缺少目标相对事实，所以不能判断是否到位、过冲或欠冲。";
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

const CHINESE_QUANTITY_CLAIM = /(?:约|大约|大概|大致|约莫|差不多|将近|接近)?(?:一半|半数|半|大半|大部分|少数|多数|[一二三四五六七八九十百千万两〇零]+分之[一二三四五六七八九十百千万两〇零]+|[一二三四五六七八九十百千万两〇零]+成)/g;
const ANALOGY_QUANTITY_MARKER = /(?:就像|像是|像(?!素)|比方|好比|打个比方|仿佛)/;

function hasFiniteNumericValue(value: unknown, depth = 0): boolean {
  if (depth > 12) return false;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.some((child) => hasFiniteNumericValue(child, depth + 1));
  if (!isRecord(value)) return false;
  return Object.values(value).some((child) => hasFiniteNumericValue(child, depth + 1));
}

function analysisHasNumericFacts(analysisSummary: string | null): boolean {
  if (!analysisSummary) return false;
  try {
    return hasFiniteNumericValue(JSON.parse(analysisSummary));
  } catch {
    return false;
  }
}

function hasUnrequestedChineseQuantity(value: string, userContent: string): boolean {
  const normalize = (claim: string): string => claim.replace(/^(?:约|大约|大概|大致|约莫|差不多|将近|接近)/, "");
  const requested = new Set(
    [...userContent.matchAll(CHINESE_QUANTITY_CLAIM)].map((match) => normalize(match[0])),
  );
  const sentences = value.split(/[。！？!?；;\n]/);
  return sentences.some((sentence, index) => {
    const previous = sentences[index - 1] ?? "";
    return [...sentence.matchAll(CHINESE_QUANTITY_CLAIM)].some((match) => (
      !requested.has(normalize(match[0])) &&
      !ANALOGY_QUANTITY_MARKER.test(sentence.slice(0, match.index ?? 0)) &&
      !ANALOGY_QUANTITY_MARKER.test(previous)
    ));
  });
}

const INTERNAL_PROTOCOL_EGRESS_PATTERNS = [
  /\bcoach_(?:diagnostic|turn)_context(?:\.v\d+)?\b/i,
  /\b(?:schema_version|processed[_ ]event[_ ]table|field_catalog|table_ref|event_ref|segment_ref|segment_id|segment_kind|start_ms|end_ms|focus_start_ms|focus_end_ms|available_channels|signal_window(?:\.v\d+)?|result_ref|next_cursor|canonical_time(?:_window)?|run_product_command)\b/i,
  /\banalysis\.(?:evidence|events|metrics|run_facts|outcomes)\.[a-z0-9_]+\b/i,
  /\banalysis:[1-9]\d*:segment:[A-Za-z0-9_.:@-]+\b/i,
  /\b(?:L0|L1|L2|L3|DTO)\b/,
  /(?<![\d.])\d{12,}(?![\d.])/,
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
    throw new GroundingError(
      "internal_vocabulary",
      "Coach response was rejected because it exposed internal protocol vocabulary",
    );
  }
}

const AVAILABLE_METRIC_VALUE_ABSENCE = /(?:没有|无|暂无).{0,8}(?:可引用|可用).{0,4}(?:数值|指标)/;
const AVAILABLE_METRIC_VALUE_USE_DENIAL = /(?:数值).{0,12}(?:不可用|无法(?:引用|使用))|(?:指标).{0,12}(?:无法引用(?:数值)?)/;
const HIT_OR_ACCURACY_DISMISSAL = /(?:别|不要|不必|无需|不用).{0,8}(?:看|管|考虑|在意).{0,12}(?:是否|有没有)?(?:打中|命中|准确率|命中率)|(?:打中|命中|准确率|命中率).{0,12}(?:不重要|不用看|别看|无需看)/;
const TARGET_RELATIVE_CLAIM = /(?:落点|过冲|欠冲|冲过(?:目标|靶|头)|没到(?:目标|靶)|(?:甩|移动|准星|鼠标).{0,16}(?:到|接近|靠近|贴上).{0,4}(?:目标|靶)|(?:到|接近|靠近|贴上).{0,4}(?:目标|靶)|(?:准星|鼠标).{0,16}(?:对上|对准|到达|越过|冲过|折回(?:来)?找|往回找).{0,8}(?:目标|靶)|overshoot|undershoot|crosshair.{0,24}target)/i;
const TARGET_RELATIVE_UNCERTAINTY = /(?:不能|无法|不可|不应|不要|没有证据|证据不足|还不知道|尚不清楚|不能判断|无法判断|不代表|不说明|不是说|不等于|cannot|can't|unable|insufficient|not enough evidence)/i;
const PRACTICE_REQUEST = /(?:怎么练|如何练|练法|练习(?:方向)?|训练(?:方向)?|今天能执行|动作提示|\bcue\b|直接(?:给|告诉).{0,12}(?:练|提示))/i;
const SPECIFIC_PRACTICE_CUE = /(?:先|请|就|要|把|让|试着|尝试|练习时|训练时|可以|建议).{0,40}(?:提前|减速|加速|停(?:住|下)?|收(?:速度|力)?|移动|手腕|手臂|鼠标|准星|跟枪|甩枪|瞄准|放松|发力)/;
const NON_PRESCRIPTION_BOUNDARY = /(?:不能|无法|不应|不会|不要|先不|暂不|证据不足).{0,24}(?:定|给|建议|采用|使用|练法|练习|训练|动作|提示|cue)?/i;
const ANALOGY_REQUEST = /(?:类比|比喻)/;
const ANALOGY_MARKER = /(?:就像|像是|像.{1,24}一样|类似于|相当于|好比|仿佛|如同|把.{0,16}(?:想成|理解成))/;
const ANALOGY_DISCLAIMER = /(?:(?:这个|这|以上|刚才的)?(?:比喻|类比).{0,24}(?:不能|不代表|不证明|不是).{0,20}(?:证据|原因|结论)|(?:不能|不代表).{0,20}(?:把|将).{0,10}(?:比喻|类比).{0,10}(?:当成|作为).{0,10}(?:证据|结论))/;
const COMPONENT_PROBLEM_FREE = /(?:甩|启动|加速|减速|收尾|修正|移动|跟枪|瞄准).{0,12}(?:本身)?(?:没(?:有|什么)?|不存在)(?:什么)?问题|(?:甩|启动|加速|减速|收尾|修正|移动|跟枪|瞄准).{0,12}(?:正常|很好)/;
const EVALUATION_UNCERTAINTY = /(?:不能|无法|不可|不应|没有证据|证据不足|还不知道|尚不清楚|不能判断|无法判断|不代表|不说明)/;
const USER_COMPARISON = /(?:上一次|上次|之前|前一次|两次|上一局|前一局|相比|比较)/;
const INVENTED_COMPARISON = /(?:上一次|上次|前一次|上一局|前一局).{0,48}(?:这次|本次|这一局)|(?:这次|本次|这一局).{0,48}(?:上一次|上次|前一次|上一局|前一局)|两次(?:测|结果|记录|分析|条件)|(?:对比|比较).{0,12}两次/;

type AnswerTarget = "tension" | "hardware" | "transfer";

const ANSWER_TARGET_LABELS: Record<AnswerTarget, string> = {
  tension: "紧张/手紧是否相关",
  hardware: "是否需要换鼠标",
  transfer: "能否迁移到其他 FPS",
};

function requestsAnalogy(userContent: string): boolean {
  return ANALOGY_REQUEST.test(userContent) &&
    !/(?:不要|别|不用|无需|不需要).{0,8}(?:类比|比喻)/.test(userContent);
}

function containsCompletedAnalogy(reply: string): boolean {
  if (ANALOGY_MARKER.test(reply)) return true;
  if (/(?:可以|能|会|再|之后|先).{0,8}(?:打个比方|比方说)/.test(reply)) return false;
  return /(?:打个比方|比方说)[：:,，]?\s*.{6,}/.test(reply);
}

function userQuestionClauses(userContent: string): string[] {
  return (userContent.match(/[^，,。；;！？!?]+[！？!?]?/g) ?? []).filter((clause) => (
    /[？?]/.test(clause) ||
    /(?:是不是|是否|会不会|要不要|需不需要|该不该|能不能|可不可以|可以吗|有关吗|影响吗)/.test(clause)
  ));
}

function requestedAnswerTargets(userContent: string): AnswerTarget[] {
  const clauses = userQuestionClauses(userContent);
  const targets: AnswerTarget[] = [];
  if (clauses.some((clause) => /(?:紧张|手紧|手僵|僵硬|发力)/.test(clause))) {
    targets.push("tension");
  }
  if (clauses.some((clause) => (
    /(?:鼠标|外设|设备)/.test(clause) && /(?:换|更换|买|要不要|需不需要|该不该)/.test(clause)
  ))) {
    targets.push("hardware");
  }
  if (clauses.some((clause) => /(?:迁移|跨游戏|实战|其他\s*FPS|别的\s*FPS|其他游戏|别的游戏)/i.test(clause))) {
    targets.push("transfer");
  }
  return targets.length >= 2 ? targets : [];
}

function missingAnswerTargets(userContent: string, reply: string): AnswerTarget[] {
  const subjects: Record<AnswerTarget, RegExp> = {
    tension: /(?:紧张|手紧|手僵|僵硬|发力)/,
    hardware: /(?:鼠标|外设|设备)/,
    transfer: /(?:迁移|跨游戏|实战|其他\s*FPS|别的\s*FPS|其他游戏|别的游戏)/i,
  };
  const answers: Record<AnswerTarget, RegExp> = {
    tension: /(?:不能判断|无法判断|不确定|可能|不一定|相关|无关|导致|原因|证据)/,
    hardware: /(?:不用换|不必换|不需要换|没必要换|需要换|建议换|不建议换|更换|证据|支持)/,
    transfer: /(?:能否|能|不能|可以|不可以|需要|复测|验证|判断|未知|不确定|支持)/,
  };
  const segments = reply.split(/[。！？!?；;\n]/);
  return requestedAnswerTargets(userContent).filter((target) => !segments.some((segment) => (
    subjects[target].test(segment) && answers[target].test(segment)
  )));
}

function analysisHasLimitation(
  analysisSummary: string | null,
  limitation: string,
): boolean {
  if (!analysisSummary) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return false;
  }
  const visit = (value: unknown, depth = 0): boolean => {
    if (depth > 12) return false;
    if (Array.isArray(value)) return value.some((child) => visit(child, depth + 1));
    if (!isRecord(value)) return false;
    if (Array.isArray(value.limitations) && value.limitations.includes(limitation)) return true;
    return Object.values(value).some((child) => visit(child, depth + 1));
  };
  return visit(parsed);
}

function analysisHasScenarioSupportStatus(
  analysisSummary: string | null,
  status: string,
): boolean {
  if (!analysisSummary) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return false;
  }
  const visit = (value: unknown, depth = 0): boolean => {
    if (depth > 12) return false;
    if (Array.isArray(value)) return value.some((child) => visit(child, depth + 1));
    if (!isRecord(value)) return false;
    if (isRecord(value.scenario) && value.scenario.support_status === status) return true;
    return Object.values(value).some((child) => visit(child, depth + 1));
  };
  return visit(parsed);
}

function analysisProjectionCount(analysisSummary: string | null): number {
  if (!analysisSummary) return 0;
  let parsed: unknown;
  try {
    parsed = JSON.parse(analysisSummary);
  } catch {
    return 0;
  }
  if (!isRecord(parsed)) return 0;
  if (parsed.schema_version === "coach_turn_context.v1" && Array.isArray(parsed.contexts)) {
    return parsed.contexts.filter((context) => isRecord(context) && isRecord(context.projection)).length;
  }
  return typeof parsed.schema_version === "string" &&
    parsed.schema_version.startsWith("coach_diagnostic_context.v") ? 1 : 0;
}

function shouldGuardPartialPractice(request: ParsedRequest): boolean {
  return request.teaching_turn === undefined &&
    PRACTICE_REQUEST.test(request.messages.at(-1)?.content ?? "") &&
    analysisHasScenarioSupportStatus(request.analysis_summary, "partial");
}

function containsUnsupportedTargetRelativeClaim(reply: string): boolean {
  return reply.split(/[。！？!?\n]/).some((sentence) => (
    TARGET_RELATIVE_CLAIM.test(sentence) && !TARGET_RELATIVE_UNCERTAINTY.test(sentence)
  ));
}

function prescribesSpecificPractice(reply: string): boolean {
  return reply.split(/[。！？!?\n]/).some((sentence) => (
    SPECIFIC_PRACTICE_CUE.test(sentence) && !NON_PRESCRIPTION_BOUNDARY.test(sentence)
  ));
}

function containsAnalogyDisclaimer(reply: string): boolean {
  return ANALOGY_DISCLAIMER.test(reply);
}

function claimsUnsupportedProblemFree(reply: string, userContent: string): boolean {
  if (COMPONENT_PROBLEM_FREE.test(userContent)) return false;
  return reply.split(/[。！？!?\n]/).some((sentence) => (
    COMPONENT_PROBLEM_FREE.test(sentence) && !EVALUATION_UNCERTAINTY.test(sentence)
  ));
}

function claimsInventedComparison(
  reply: string,
  request: ParsedRequest,
  currentMessages: unknown[],
): boolean {
  if (analysisProjectionCount(request.analysis_summary) !== 1 ||
      USER_COMPARISON.test(request.messages.at(-1)?.content ?? "") ||
      parseToolPayloads(currentMessages).some(({ commandName }) => (
        commandName === "analysis.compare" || commandName === "analysis.evidence.compare"
      ))) {
    return false;
  }
  return INVENTED_COMPARISON.test(reply);
}

function claimsAvailableMetricValuesUnavailable(reply: string): boolean {
  if (AVAILABLE_METRIC_VALUE_ABSENCE.test(reply)) return true;
  return AVAILABLE_METRIC_VALUE_USE_DENIAL.test(reply) &&
    !/(?:数值|指标).{0,12}(?:可引用|可用)|(?:可引用|可用).{0,12}(?:数值|指标)/.test(reply);
}

function dismissesHitsOrAccuracy(reply: string): boolean {
  return reply.split(/[。！？!?]/).some((sentence) => {
    if (!HIT_OR_ACCURACY_DISMISSAL.test(sentence)) return false;
    return !/(?:还|仍|也|同时|以及|配合|结合).{0,20}(?:看|记录|评估|分析|考虑)/.test(sentence);
  });
}

function validateGroundedReply(
  reply: string,
  request: ParsedRequest,
  currentMessages: unknown[],
): void {
  validateUserFacingEgress(reply);
  if (dismissesHitsOrAccuracy(reply)) {
    throw new GroundingError(
      "hits_accuracy_dismissal",
      "Coach response was rejected because it told the user to ignore hits or accuracy",
    );
  }
  if (analysisHasLimitation(request.analysis_summary, "target_relative_facts_unavailable") &&
      containsUnsupportedTargetRelativeClaim(reply)) {
    throw new GroundingError(
      "target_relative_claim",
      "Coach response was rejected because it claimed unavailable target-relative facts",
    );
  }
  if (shouldGuardPartialPractice(request) && prescribesSpecificPractice(reply)) {
    throw new GroundingError(
      "unsupported_practice",
      "Coach response was rejected because it invented a practice cue for partial evidence",
    );
  }
  if (containsAnalogyDisclaimer(reply)) {
    throw new GroundingError(
      "analogy_disclaimer",
      "Coach response was rejected because it explained an analogy with a visible disclaimer",
    );
  }
  const userContent = request.messages.at(-1)?.content ?? "";
  if (requestsAnalogy(userContent) && !containsCompletedAnalogy(reply)) {
    throw new GroundingError(
      "missing_analogy",
      "Coach response was rejected because it omitted the analogy explicitly requested by the user",
    );
  }
  if (requestsAnalogy(userContent) && /[?？]/.test(reply)) {
    throw new GroundingError(
      "analogy_not_direct",
      "Coach response was rejected because it asked follow-up questions instead of directly completing the analogy",
    );
  }
  if (analysisHasScenarioSupportStatus(request.analysis_summary, "partial") &&
      claimsUnsupportedProblemFree(reply, userContent)) {
    throw new GroundingError(
      "partial_problem_free",
      "Coach response was rejected because it declared an unmeasured phase problem-free",
    );
  }
  if (claimsInventedComparison(reply, request, currentMessages)) {
    throw new GroundingError(
      "invented_comparison",
      "Coach response was rejected because it invented a second Analysis for comparison",
    );
  }
  const missingTargets = missingAnswerTargets(userContent, reply);
  if (missingTargets.length > 0) {
    throw new GroundingError(
      "missing_answer_target",
      "Coach response was rejected because it omitted part of a multi-part user request",
      missingTargets.map((target) => ANSWER_TARGET_LABELS[target]),
    );
  }
  const namedMetricValues = requestedSummaryMetricValues(
    request.analysis_summary,
    request.messages.at(-1)?.content ?? "",
  );
  if (namedMetricValues.length > 0 && claimsAvailableMetricValuesUnavailable(reply)) {
    throw new GroundingError(
      "requested_metric_denial",
      "Coach response was rejected because it denied available requested metric values",
    );
  }
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
  if (analysisHasNumericFacts(request.analysis_summary) &&
      hasUnrequestedChineseQuantity(quantitativeText, userContent)) {
    throw new GroundingError(
      "quantity_conversion",
      "Coach response was rejected because it converted a measured quantity into an unrequested Chinese quantity",
    );
  }
  const ungroundedQuantity = textNumberClaims(quantitativeText).find((claim) => (
    claim.percent
      ? !allowedPercentNumbers.has(claim.value)
      : !allowedNumbers.has(claim.value)
  ));
  if (ungroundedQuantity) {
    throw new GroundingError(
      "ungrounded_quantity",
      "Coach response was rejected because it contained an ungrounded quantity",
    );
  }
  const ungroundedUnit = [...quantitativeText.matchAll(UNIT_CLAIM_PATTERN)].find((match) => {
    const numeric = Number(match[1]);
    const unit = normalizedUnit(match[2]);
    if (!Number.isFinite(numeric) || unit === null) return false;
    return !allowedUnits.get(match[1])?.has(unit);
  });
  if (ungroundedUnit) {
    throw new GroundingError(
      "unit_mismatch",
      "Coach response was rejected because it paired a quantity with an unsupported unit",
    );
  }
  if (hasUnrequestedPrescriptionDose(quantitativeText, userPrescriptionUnits)) {
    throw new GroundingError(
      "prescription_dose",
      "Coach response was rejected because it used an observed quantity as an unrequested prescription dose",
    );
  }
  const sourceHasLimitation = containsEvidenceLimitation(summaryPayload) ||
    toolPayloads.some(({ payload }) => containsEvidenceLimitation(payload));
  if (sourceHasLimitation && containsUnsupportedCausalClaim(quantitativeText)) {
    throw new GroundingError(
      "unsupported_causal_claim",
      "Coach response was rejected because it attributed unavailable evidence to player ability",
    );
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
    throw new GroundingError(
      "unavailable_metric",
      "Coach response was rejected because it cited an unavailable metric",
    );
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
  let groundingRepairActive = false;
  let groundingRepairToolAttempted = false;
  let requiredDeleteBridgeCallIssued = false;
  let unrequestedDeletionAttempted = false;
  let outOfPhaseTeachingWriteAttempted = false;
  let productCommandExecutionFailed = false;
  let teachingFallbackUsed = false;
  let teachingHoldUsed = false;
  let groundingFallbackUsed = false;
  let parsedRequest: ParsedRequest | null = null;
  let partialRevision = 0;
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
    const userInterruptsTeaching = request.teaching_turn !== undefined &&
      (TEACHING_INTERRUPTION.test(request.messages.at(-1)?.content ?? "") ||
        teachingTurnRequiresLocalFallback(request.teaching_turn));
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
    const systemPrompt = `${resolveSystemPrompt(request.system_prompt)}${MANDATORY_POLICY}${request.teaching_turn ? `\n\n${teachingEnvelopeInstruction(request.teaching_turn)}` : ""}${userInterruptsTeaching ? DIRECT_TEACHING_INTERRUPTION_INSTRUCTION : ""}${explicitMetricInstruction(explicitMetrics)}`;
    const maxAnalysisResultBytes = analysisResultBudgetBytes(
      resolved.model.contextWindow,
      resolved.model.maxTokens,
      systemPrompt,
      [...history, ...prompt],
    );
    const requiredDeleteRef = requiredAnalysisDeleteRef(request);
    requiredDeleteForTurn = requiredDeleteRef !== null;
    const tools = [restrictTurnTools(
      createAnalysisSummaryTool(request.analysis_summary, { maxResultBytes: maxAnalysisResultBytes }),
      () => requiredDeleteRef,
      () => groundingRepairActive,
      () => { groundingRepairToolAttempted = true; },
      () => requiredDeleteBridgeCallIssued,
      () => { requiredDeleteBridgeCallIssued = true; },
      () => { unrequestedDeletionAttempted = true; },
      () => request.teaching_turn,
      () => outOfPhaseTeachingWriteAttempted,
      () => { outOfPhaseTeachingWriteAttempted = true; },
      () => { productCommandExecutionFailed = true; },
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
        () => { productCommandExecutionFailed = true; },
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
      validateGroundedReply(text, request, currentMessages);
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
      } else if (event.type === "message_end" && event.message?.role === "assistant") {
        const providerStartedAt = providerStarts.shift();
        if (providerStartedAt !== undefined) {
          const roundMs = Math.max(0, Math.round(now - providerStartedAt));
          providerRoundMs.push(roundMs);
          providerMs += roundMs;
        }
      } else if (event.type === "tool_execution_start") {
        toolStarts.set(event.toolCallId, now);
      } else if (event.type === "tool_execution_end") {
        const toolStartedAt = toolStarts.get(event.toolCallId);
        if (toolStartedAt !== undefined) {
          toolMs += Math.max(0, now - toolStartedAt);
          toolStarts.delete(event.toolCallId);
        }
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
      validateGroundedReply(fallback, request, currentMessages);
      return successResponse(fallback, [TEACHING_FALLBACK_NOTE], request.schema_version, collectToolEvents(currentMessages), request.run_id);
    }
    if (requiredDeleteRef !== null &&
        !hasRequiredDeleteConfirmation(collectToolEvents(currentMessages)) &&
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
      if (!hasRequiredDeleteConfirmation(collectToolEvents(currentMessages)) &&
          !hasRequiredDirectDeletion(collectToolEvents(currentMessages))) {
        throw new ToolComplianceError("Explicit Analysis deletion did not create the required structured confirmation");
      }
    }
    const toolEvents = collectToolEvents(currentMessages);
    if (hasPendingConfirmation(toolEvents)) {
      return successResponse(TRUSTED_CONFIRMATION_REPLY, [], request.schema_version, toolEvents, request.run_id);
    }
    let rawReply: string;
    try {
      rawReply = redactRuntimeSecrets(extractAssistantReply(currentMessages), secrets);
    } catch (error) {
      if (!(error instanceof EmptyAssistantReplyError)) throw error;
      const emptyReplyFallback = request.teaching_turn
        ? (explicitMetricFallback(explicitMetrics) ?? (userInterruptsTeaching
          ? directTeachingInterruptionFallback(
            request.teaching_turn,
            request.messages.at(-1)?.content ?? "",
          )
          : fallbackForTeachingTurn(request.teaching_turn).text))
        : (explicitMetricFallback(explicitMetrics) ?? deterministicGroundingFallback(request, currentMessages) ??
          "这次模型没有生成可显示的回答。附带分析仍然可用，请换一种问法重试。");
      validateGroundedReply(emptyReplyFallback, request, currentMessages);
      return successResponse(
        emptyReplyFallback,
        request.teaching_turn
          ? [userInterruptsTeaching ? TEACHING_HOLD_NOTE : TEACHING_FALLBACK_NOTE]
          : [],
        request.schema_version,
        collectToolEvents(currentMessages),
        request.run_id,
      );
    }
    let reply = normalizeUserFacingText(rawReply);
    if (request.teaching_turn) {
      const draft = parseTeachingProviderDraft(rawReply);
      const directReply = draft === null ? rawReply : draft.text;
      if (userInterruptsTeaching) {
        if ((draft !== null || !rawReply.trimStart().startsWith("{")) &&
            validateTeachingDirectResponse(request.teaching_turn, directReply).ok) {
          reply = normalizeUserFacingText(directReply);
        } else {
          reply = explicitMetricFallback(explicitMetrics) ?? directTeachingInterruptionFallback(
            request.teaching_turn,
            request.messages.at(-1)?.content ?? "",
          );
        }
        teachingHoldUsed = true;
      } else if (draft === null || !validateTeachingDraft(request.teaching_turn, draft).ok) {
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
        const metricFallback = claimsAvailableMetricValuesUnavailable(reply)
          ? explicitMetricFallback(explicitMetrics)
          : null;
        if (metricFallback !== null) {
          validateGroundedReply(metricFallback, request, currentMessages);
          return successResponse(metricFallback, [TEACHING_HOLD_NOTE], request.schema_version, [], request.run_id);
        }
        reply = userInterruptsTeaching
          ? directTeachingInterruptionFallback(
            request.teaching_turn,
            request.messages.at(-1)?.content ?? "",
          )
          : fallbackForTeachingTurn(request.teaching_turn).text;
        validateGroundedReply(reply, request, currentMessages);
        return successResponse(
          reply,
          [userInterruptsTeaching ? TEACHING_HOLD_NOTE : TEACHING_FALLBACK_NOTE],
          request.schema_version,
          userInterruptsTeaching ? [] : collectToolEvents(currentMessages),
          request.run_id,
        );
      }
      partialMessageStart = agent.state.messages.length;
      groundingRepairActive = true;
      const repairStartedAt = performance.now();
      try {
        await agent.prompt([{
          role: "user" as const,
          content: [{
            type: "text" as const,
            text: groundingRepairPrompt(request, currentMessages, error),
          }],
          timestamp: Date.now(),
        }]);
      } finally {
        repairMs += Math.max(0, performance.now() - repairStartedAt);
      }
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
        const metricFallback = claimsAvailableMetricValuesUnavailable(reply)
          ? explicitMetricFallback(explicitMetrics)
          : null;
        const deterministicFallback = deterministicGroundingFallback(
          request,
          currentMessages,
          repairError.reason,
        ) ?? deterministicGroundingFallback(request, currentMessages, error.reason);
        const fallback = metricFallback ?? deterministicFallback;
        if (fallback === null) throw repairError;
        reply = fallback;
        validateGroundedReply(reply, request, currentMessages);
        groundingFallbackUsed = metricFallback === null;
      }
    }
    await publishPartial(reply, currentMessages, true);
    return successResponse(
      reply,
      groundingFallbackUsed
        ? [GROUNDING_FALLBACK_NOTE]
        : teachingFallbackUsed
          ? [TEACHING_FALLBACK_NOTE]
          : teachingHoldUsed
            ? [TEACHING_HOLD_NOTE]
            : [],
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
