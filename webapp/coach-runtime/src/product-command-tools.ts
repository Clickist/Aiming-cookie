import { createHash } from "node:crypto";
import { loadPiAi } from "./pi-source.ts";
import { isRecord, type CoachToolBridge } from "./contracts.ts";
import { NATIVE_READ_COMMANDS, executeNativeRead } from "./product-commands-native.ts";
import { isNativeEvidenceCommand, executeNativeEvidence, predicateStructureError } from "./evidence-native.ts";
import { isNativeWriteCommand, executeNativeWrite, isNativeAnalysisDeleteCommand, executeNativeAnalysisDelete, isNativeAnalysisRetryCommand, executeNativeAnalysisRetry, type NativeWriteResult } from "./product-commands-write.ts";
import { isNativeEloshapesCommand, executeNativeEloshapes } from "./eloshapes-native.ts";
import { isNativeKovaakScoreCommand, executeNativeKovaakScore } from "./kovaak-scores-native.ts";
import { isNativePythonAnalysisCommand, executeNativePythonAnalysis } from "./python-analysis.ts";

type TypeBuilder = {
  Literal(value: string): unknown;
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  Optional(schema: unknown): unknown;
  String(options?: Record<string, unknown>): unknown;
  Union(schemas: unknown[]): unknown;
};

const { Type } = (await loadPiAi()) as { Type: TypeBuilder };

export const PRODUCT_COMMAND_NAMES = [
  "run.list", "run.get", "history.list", "history.trend", "analysis.get",
  "analysis.compare", "navigation.open", "analysis.create_from_run", "analysis.retry", "analysis.delete",
  "training_plan.generate_draft", "training_plan.save", "training_plan.activate",
  "training_plan.pause", "training_plan.adjust", "training_plan.review", "training_plan.item.add",
  "training_plan.execution.record", "training_plan.retest.record", "teaching_session.update",
  "scenario_memory.set",
  "analysis.metrics.distribution", "analysis.evidence.list", "analysis.evidence.signal_window",
  "analysis.evidence.compare", "analysis.run_facts.get", "analysis.outcomes.timeline",
  "analysis.events.list", "analysis.events.get", "analysis.events.rank",
  "analysis.events.filter", "analysis.events.aggregate", "analysis.events.co_occurrence",
  "analysis.events.sequence", "profile.aiming.snapshot",
  "product.readiness.get",
  "kovaak_scores.lookup", "kovaak_scores.refresh_connected",
  "eloshapes.query", "peripheral_profile.get", "peripheral_profile.update",
] as const;
type ProductCommandName = typeof PRODUCT_COMMAND_NAMES[number];
type ProductCommandToolOptions = {
  excludedCommands?: readonly ProductCommandName[];
};
const KOVAAK_SCORE_COMMANDS = new Set<ProductCommandName>([
  "kovaak_scores.lookup", "kovaak_scores.refresh_connected",
]);

const WRITE_COMMANDS = new Set<ProductCommandName>([
  "analysis.create_from_run", "analysis.retry", "analysis.delete", "training_plan.generate_draft",
  "training_plan.save", "training_plan.activate", "training_plan.pause", "training_plan.adjust",
  "training_plan.item.add", "training_plan.execution.record", "training_plan.retest.record",
]);
const PRODUCT_COMMAND_STATUSES = new Set([
  "succeeded", "failed", "cancelled", "needs_confirmation", "unavailable",
]);

function validateBridge(bridge: CoachToolBridge): void {
  const url = new URL(bridge.endpoint);
  if (bridge.schema_version !== "coach_tool_bridge.v1" || url.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(url.hostname) || !url.port ||
      url.pathname !== "/api/coach/tools/execute" || url.search || url.hash) {
    throw new Error("Product command bridge is unavailable");
  }
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function stableKey(bridge: CoachToolBridge, commandName: string, parameters: Record<string, unknown>): string {
  return `turn:${createHash("sha256").update(canonicalJson({ turn: bridge.turn_id, commandName, parameters })).digest("hex")}`;
}

function hasExactKeys(parameters: Record<string, unknown>, keys: string[]): boolean {
  const received = Object.keys(parameters);
  return received.length === keys.length && keys.every((key) => Object.hasOwn(parameters, key));
}

// ── Strict parameter contracts ─────────────────────────────────────────
//
// The 2026-08-16 deep test showed that a fully open `parameters` object makes
// wrong shapes either silently ignored (false-positive answers) or opaque
// errors the model cannot self-correct. The command families below get an
// explicit contract: unknown fields are rejected with the allowed list.
// Requiredness/format of table_ref stays in evidence-native so a missing
// table_ref reaches the model as a structured invalid_parameters result.

const ELOSHAPES_FILTER_KEYS = [
  "weight_max", "size_category", "shape", "front_flare",
  "side_curvature", "hump_placement", "hand_compatibility",
  "brand_search", "model_search", "limit",
] as const;

const EVENTS_LIST_SCOPE = new Set(["whole_run", "evidence_segment"]);
const EVENTS_RANK_DIRECTION = new Set(["asc", "desc"]);
const ANALYSIS_REF_RE = /^analysis:\d+$/;

function unsupportedKeysError(
  parameters: Record<string, unknown>,
  allowed: readonly string[],
  commandName: string,
): string | null {
  const unknown = Object.keys(parameters).filter((key) => !allowed.includes(key));
  if (unknown.length === 0) return null;
  return `unsupported fields: ${commandName} does not accept ${unknown.map((key) => `"${key}"`).join(", ")}; allowed fields: ${allowed.join(", ")}`;
}

function requiredStringError(value: unknown, field: string): string | null {
  return typeof value === "string" && value.length > 0 ? null : `unsupported fields: ${field} must be a non-empty string`;
}

function optionalNumberError(value: unknown, field: string): string | null {
  if (value === undefined || value === null) return null;
  return typeof value === "number" && Number.isFinite(value) ? null : `unsupported fields: ${field} must be a number`;
}

function stringArrayError(value: unknown, field: string): string | null {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value) || value.length === 0 || !value.every((item) => typeof item === "string" && item.length > 0)) {
    return `unsupported fields: ${field} must be a non-empty array of strings`;
  }
  return null;
}

function predicatesError(value: unknown, field: string, required: boolean): string | null {
  if (value === undefined || value === null) {
    return required ? `unsupported fields: ${field} is required — an array of {field, operator, value} predicates (at least one)` : null;
  }
  if (!Array.isArray(value) || (required && value.length === 0)) {
    return `unsupported fields: ${field} must be a non-empty array of {field, operator, value} predicates`;
  }
  for (let i = 0; i < value.length; i++) {
    const error = predicateStructureError(value[i], `${field}[${i}]`);
    if (error) return `unsupported fields: ${error}`;
  }
  return null;
}

function eventsCommandError(commandName: ProductCommandName, parameters: Record<string, unknown>): string | null {
  if (commandName === "analysis.events.list") {
    const unknown = unsupportedKeysError(parameters, ["analysis_ref", "scope", "segment_ref", "event_kinds", "limit"], commandName);
    if (unknown) return unknown;
    if (typeof parameters.analysis_ref !== "string" || !ANALYSIS_REF_RE.test(parameters.analysis_ref)) {
      return 'unsupported fields: analysis_ref must be "analysis:<id>"';
    }
    if (parameters.scope !== undefined && parameters.scope !== null && !EVENTS_LIST_SCOPE.has(parameters.scope as string)) {
      return `unsupported fields: scope must be one of ${[...EVENTS_LIST_SCOPE].join(", ")}`;
    }
    return stringArrayError(parameters.event_kinds, "event_kinds") ?? optionalNumberError(parameters.limit, "limit");
  }
  if (commandName === "analysis.events.get") {
    const unknown = unsupportedKeysError(parameters, ["table_ref", "event_ref"], commandName);
    if (unknown) return unknown;
    // table_ref requiredness/format is validated natively so the model gets
    // the structured invalid_parameters result (Bug 5).
    return requiredStringError(parameters.event_ref, "event_ref");
  }
  if (commandName === "analysis.events.rank") {
    const unknown = unsupportedKeysError(parameters, ["table_ref", "field", "direction", "predicates", "limit"], commandName);
    if (unknown) return unknown;
    if (parameters.direction !== undefined && parameters.direction !== null && !EVENTS_RANK_DIRECTION.has(parameters.direction as string)) {
      return `unsupported fields: direction must be one of ${[...EVENTS_RANK_DIRECTION].join(", ")}`;
    }
    return requiredStringError(parameters.field, "field")
      ?? predicatesError(parameters.predicates, "predicates", false)
      ?? optionalNumberError(parameters.limit, "limit");
  }
  if (commandName === "analysis.events.filter") {
    const unknown = unsupportedKeysError(parameters, ["table_ref", "predicates", "limit"], commandName);
    if (unknown) return unknown;
    return predicatesError(parameters.predicates, "predicates", true) ?? optionalNumberError(parameters.limit, "limit");
  }
  if (commandName === "analysis.events.aggregate") {
    const unknown = unsupportedKeysError(parameters, ["table_ref", "fields", "group_by"], commandName);
    if (unknown) return unknown;
    return stringArrayError(parameters.fields, "fields");
  }
  if (commandName === "analysis.events.co_occurrence") {
    const unknown = unsupportedKeysError(parameters, ["table_ref", "left", "right", "relation"], commandName);
    if (unknown) return unknown;
    // left/right are single predicates, not arrays.
    for (const label of ["left", "right"] as const) {
      const error = predicateStructureError(parameters[label], label);
      if (error) return `unsupported fields: ${error}`;
    }
    return null;
  }
  // analysis.events.sequence
  const unknown = unsupportedKeysError(parameters, ["table_ref", "fields", "mode"], commandName);
  if (unknown) return unknown;
  return stringArrayError(parameters.fields, "fields");
}

/** Returns a rejection message for invalid parameters, or null when valid. */
function commandParameterError(commandName: ProductCommandName, parameters: Record<string, unknown>): string | null {
  if (commandName === "kovaak_scores.lookup") {
    if (!hasExactKeys(parameters, ["profile_ref"])) return 'unsupported fields: kovaak_scores.lookup takes exactly "profile_ref"';
    if (typeof parameters.profile_ref !== "string" ||
        !(
          /^steam_profile:[1-9]\d*$/.test(parameters.profile_ref) ||
          /^\d{17}$/.test(parameters.profile_ref) ||
          /^https:\/\/steamcommunity\.com\/profiles\/\d{17}\/?$/.test(parameters.profile_ref)
        )) {
      return "unsupported fields: profile_ref must be a steam_profile ref, a 17-digit Steam ID, or a steamcommunity profile URL";
    }
    return null;
  }
  if (commandName === "kovaak_scores.refresh_connected") {
    return hasExactKeys(parameters, []) ? null : "unsupported fields: kovaak_scores.refresh_connected takes no parameters";
  }
  if (commandName === "eloshapes.query") {
    return unsupportedKeysError(parameters, ELOSHAPES_FILTER_KEYS, commandName)
      ?? optionalNumberError(parameters.weight_max, "weight_max")
      ?? optionalNumberError(parameters.limit, "limit");
  }
  if (commandName === "training_plan.generate_draft") {
    const unknown = unsupportedKeysError(parameters, ["plan_payload", "evidence_refs", "verification_targets"], commandName);
    if (unknown) return unknown;
    if (parameters.plan_payload === undefined || typeof parameters.plan_payload !== "object" || Array.isArray(parameters.plan_payload)) {
      return 'unsupported fields: training_plan.generate_draft requires "plan_payload" (the draft plan object), e.g. {plan_payload: {...}}';
    }
    return null;
  }
  if (commandName.startsWith("analysis.events.")) {
    return eventsCommandError(commandName, parameters);
  }
  return null;
}

function safeCommandEvent(result: Record<string, unknown>, commandName: string) {
  if (result.schema_version !== "coach_product_command_result.v1" || typeof result.command_id !== "string" ||
      typeof result.status !== "string" || !PRODUCT_COMMAND_STATUSES.has(result.status) ||
      typeof result.audit_ref !== "string") {
    throw new Error("Product command returned an invalid result");
  }
  const isKovaakScoreCommand = KOVAAK_SCORE_COMMANDS.has(commandName as ProductCommandName);
  return {
    type: "product_command" as const,
    command_id: result.command_id,
    command_name: commandName,
    status: result.status,
    result_ref: typeof result.result_ref === "string" ? result.result_ref : null,
    audit_ref: result.audit_ref,
    ui_event: !isKovaakScoreCommand && isRecord(result.ui_event) ? result.ui_event : null,
    warning_or_error: !isKovaakScoreCommand && isRecord(result.warning_or_error) ? result.warning_or_error : null,
    ...(result.authorization_source === "explicit_user_request" ? {
      authorization_source: "explicit_user_request" as const,
    } : {}),
  };
}

type NativeCommandResult = {
  status: "succeeded" | "failed";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

function nativeToToolResult(commandName: string, nativeResult: NativeCommandResult) {
  const uiEvent = isRecord(nativeResult.result)
    && nativeResult.result.schema_version === "coach_ui_event.v1"
    ? nativeResult.result
    : null;
  const event = {
    type: "product_command" as const,
    command_id: `native:${commandName}:${Date.now()}`,
    command_name: commandName,
    status: nativeResult.status,
    result_ref: nativeResult.result_ref ?? null,
    audit_ref: "native",
    ui_event: uiEvent,
    warning_or_error: nativeResult.warning_or_error ?? null,
  };
  const responseText = JSON.stringify({
    schema_version: "coach_product_command_result.v1",
    command_id: event.command_id,
    status: nativeResult.status,
    audit_ref: "native",
    ...(nativeResult.result_ref ? { result_ref: nativeResult.result_ref } : {}),
    ...(nativeResult.result !== undefined ? { result: nativeResult.result } : {}),
    ...(nativeResult.warning_or_error ? { warning_or_error: nativeResult.warning_or_error } : {}),
  });
  return { content: [{ type: "text", text: responseText }], details: { event } };
}

function writeResultToToolResult(commandName: string, result: NativeWriteResult) {
  const event = {
    type: "product_command" as const,
    command_id: result.command_id,
    command_name: commandName,
    status: result.status,
    result_ref: result.result_ref ?? null,
    audit_ref: result.audit_ref,
    ui_event: result.ui_event ?? null,
    warning_or_error: result.warning_or_error ?? null,
  };
  const responseObj: Record<string, unknown> = {
    schema_version: "coach_product_command_result.v1",
    command_id: result.command_id,
    status: result.status,
    audit_ref: result.audit_ref,
  };
  if (result.result_ref !== undefined) responseObj.result_ref = result.result_ref;
  if (result.result !== undefined) responseObj.result = result.result;
  if (result.ui_event) responseObj.ui_event = result.ui_event;
  if (result.warning_or_error) responseObj.warning_or_error = result.warning_or_error;
  return { content: [{ type: "text", text: JSON.stringify(responseObj) }], details: { event } };
}

export function createProductCommandTool(
  bridge: CoachToolBridge | null,
  options: ProductCommandToolOptions & { ownerId?: string } = {},
) {
  const ownerId = options.ownerId ?? "";
  if (bridge) validateBridge(bridge);
  const excludedCommands = new Set(options.excludedCommands ?? []);
  const commandNames = PRODUCT_COMMAND_NAMES.filter((name) => !excludedCommands.has(name));
  const commandSchema = Type.Union(commandNames.map((name) => Type.Literal(name)));
  return {
    name: "run_product_command",
    label: "Run product command",
    description: "查询分析数据、导航或准备训练动作。Evidence：调用 analysis.evidence.list（仅传 analysis_ref），返回各 segment_ref 及 available_channels。事件：调用 analysis.events.list（传 analysis_ref 与 scope），表结果含 table_ref 与 field_catalog。查 KovaaK 成绩：kovaak_scores.lookup 的 profile_ref 传用户提供的 17 位 Steam ID 或 steamcommunity.com 主页链接。参数形态要点：analysis.events.* 表命令的 table_ref 形如 analysis:<id>:table:<event_kind>（用 events.list 返回的原值），predicates 是 [{field, operator, value}] 数组，operator 取 eq/lt/lte/gt/gte/between/available/unavailable，filter 至少一个谓词；eloshapes.query 只接受 weight_max、size_category、shape、front_flare、side_curvature、hump_placement、hand_compatibility、brand_search、model_search、limit；training_plan.generate_draft 必须传 plan_payload 对象。未知参数会被拒绝并列出允许的字段。不要猜测 ref，只用已返回的 ref。不得提交路径、credential 或任意 payload。",
    parameters: Type.Object({
      command_name: commandSchema,
      parameters: Type.Object({}, { additionalProperties: true }),
      idempotency_key: Type.Optional(Type.String({ maxLength: 256 })),
      instruction_quote: Type.Optional(Type.String({ minLength: 1, maxLength: 512 })),
    }, { additionalProperties: false }),
    async execute(_id: string, params: {
      command_name: ProductCommandName;
      parameters: Record<string, unknown>;
      idempotency_key?: string;
      instruction_quote?: string;
    }, signal?: AbortSignal) {
      if (!commandNames.includes(params.command_name)) {
        throw new Error("Product command is not available for this turn");
      }
      if (!isRecord(params.parameters)) {
        throw new Error("Product command contains unsupported fields");
      }
      const parameterError = commandParameterError(params.command_name, params.parameters);
      if (parameterError) {
        throw new Error(parameterError);
      }

      // Native read commands: read JSON files directly, skip the HTTP bridge.
      if (NATIVE_READ_COMMANDS.has(params.command_name)) {
        const nativeResult = executeNativeRead(params.command_name, params.parameters, ownerId);
        return nativeToToolResult(params.command_name, nativeResult);
      }

      // Native evidence commands: read artifact from filesystem, skip the HTTP bridge.
      if (isNativeEvidenceCommand(params.command_name)) {
        const nativeResult = executeNativeEvidence(params.command_name, params.parameters, ownerId);
        return nativeToToolResult(params.command_name, nativeResult);
      }

      // Native write commands: write JSON files directly, skip the HTTP bridge.
      if (isNativeWriteCommand(params.command_name)) {
        let idempotencyKey = params.idempotency_key;
        if (!idempotencyKey && bridge) {
          idempotencyKey = stableKey(bridge, params.command_name, params.parameters);
        } else if (!idempotencyKey) {
          // No bridge — generate a stable key scoped to owner + command + params.
          idempotencyKey = `native:${createHash("sha256").update(canonicalJson({ owner: ownerId, commandName: params.command_name, parameters: params.parameters })).digest("hex")}`;
        }
        const nativeResult = executeNativeWrite(params.command_name, params.parameters, ownerId, idempotencyKey);
        return writeResultToToolResult(params.command_name, nativeResult);
      }

      // Native analysis delete: HTTP DELETE to the Python backend so the session,
      // managed workspace, and tombstone are removed together, then the local
      // progressive-disclosure directory is cleaned up.
      if (isNativeAnalysisDeleteCommand(params.command_name)) {
        let idempotencyKey = params.idempotency_key;
        if (!idempotencyKey) {
          idempotencyKey = `native:${createHash("sha256").update(canonicalJson({ owner: ownerId, commandName: params.command_name, parameters: params.parameters })).digest("hex")}`;
        }
        const nativeResult = await executeNativeAnalysisDelete(
          params.command_name, params.parameters, ownerId, idempotencyKey, signal,
        );
        return writeResultToToolResult(params.command_name, nativeResult);
      }

      // Native Python analysis trigger: HTTP to the Python backend, skip the bridge.
      if (isNativePythonAnalysisCommand(params.command_name)) {
        let idempotencyKey = params.idempotency_key;
        if (!idempotencyKey) {
          idempotencyKey = `native:${createHash("sha256").update(canonicalJson({ owner: ownerId, commandName: params.command_name, parameters: params.parameters })).digest("hex")}`;
        }
        const nativeResult = await executeNativePythonAnalysis(
          params.command_name, params.parameters, ownerId, idempotencyKey, signal,
        );
        return writeResultToToolResult(params.command_name, nativeResult);
      }

      // Native analysis retry: HTTP POST to the Python backend so the failed
      // session is re-enqueued through the shared product command handler.
      if (isNativeAnalysisRetryCommand(params.command_name)) {
        let idempotencyKey = params.idempotency_key;
        if (!idempotencyKey) {
          idempotencyKey = `native:${createHash("sha256").update(canonicalJson({ owner: ownerId, commandName: params.command_name, parameters: params.parameters })).digest("hex")}`;
        }
        const nativeResult = await executeNativeAnalysisRetry(
          params.command_name, params.parameters, ownerId, idempotencyKey, signal,
        );
        return writeResultToToolResult(params.command_name, nativeResult);
      }

      // Native eloshapes query: read artifact files, skip the HTTP bridge.
      if (isNativeEloshapesCommand(params.command_name)) {
        const nativeResult = executeNativeEloshapes(params.command_name, params.parameters);
        return nativeToToolResult(params.command_name, nativeResult);
      }

      // Native KovaaK scores: async HTTP call to KovaaK API, skip the HTTP bridge.
      if (isNativeKovaakScoreCommand(params.command_name)) {
        // lookup takes the user-provided 17-digit Steam ID or steamcommunity
        // profile URL directly (or a turn-scoped steam_profile ref when the
        // caller supplies one); refresh_connected reads steam_id from config.
        const nativeResult = await executeNativeKovaakScore(
          params.command_name, params.parameters, ownerId,
        );
        return nativeToToolResult(params.command_name, nativeResult);
      }

      if (!bridge) {
        throw new Error("Product command bridge is unavailable");
      }

      // Bridge path for write/evidence commands.
      const isKovaakScoreCommand = KOVAAK_SCORE_COMMANDS.has(params.command_name);
      const body: Record<string, unknown> = {
        command_name: params.command_name,
        parameters: params.parameters,
      };
      if (WRITE_COMMANDS.has(params.command_name)) body.idempotency_key = params.idempotency_key || stableKey(bridge, params.command_name, params.parameters);
      if (params.instruction_quote !== undefined) body.instruction_quote = params.instruction_quote;
      const headers: Record<string, string> = { "Content-Type": "application/json", Authorization: `Bearer ${bridge.bearer_token}` };
      if (bridge.desktop_token) headers["X-Aiming-Cookie-Desktop-Token"] = bridge.desktop_token;
      let parsed: unknown;
      try {
        const response = await fetch(bridge.endpoint, { method: "POST", headers, body: JSON.stringify(body), signal });
        parsed = await response.json();
        if (!response.ok) throw new Error("backend rejected command");
      } catch {
        throw new Error("Product command bridge request failed");
      }
      if (!isRecord(parsed)) {
        throw new Error("Product command returned an invalid result");
      }
      const event = safeCommandEvent(parsed, params.command_name);
      const providerResult = { ...parsed };
      delete providerResult.confirmation;
      const responseText = JSON.stringify(providerResult);
      return { content: [{ type: "text", text: responseText }], details: { event } };
    },
  };
}
