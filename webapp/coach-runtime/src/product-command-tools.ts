import { createHash } from "node:crypto";
import { loadPiAi } from "./pi-source.ts";
import { isRecord, type CoachToolBridge } from "./contracts.ts";

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
  "training_plan.execution.record", "training_plan.retest.record",
  "analysis.metrics.distribution", "analysis.evidence.list", "analysis.evidence.signal_window",
  "analysis.evidence.compare", "analysis.run_facts.get", "analysis.outcomes.timeline",
  "analysis.events.list", "analysis.events.get", "analysis.events.rank",
  "analysis.events.filter", "analysis.events.aggregate", "analysis.events.co_occurrence",
  "analysis.events.sequence", "profile.aiming.snapshot",
  "product.readiness.get",
  "kovaak_scores.lookup", "kovaak_scores.refresh_connected",
  "teaching_session.update",
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
const FORBIDDEN_KEYS = new Set([
  "owner", "owner_id", "owner_scope", "actor", "risk", "authority", "confirmation", "confirmation_ref",
  "request_basis", "path", "video_path",
  "url", "credential", "credentials", "api_key", "authorization", "token",
  "bearer_token", "desktop_token", "password", "secret", "raw_trace", "payload", "endpoint",
]);
const PRODUCT_COMMAND_STATUSES = new Set([
  "succeeded", "failed", "cancelled", "needs_confirmation", "unavailable",
]);
const PATH_OR_URL_TEXT = /(?:https?:\/\/|file:(?:\/\/)?|(?:^|[\s"'`([{=,:])(?:\/|~[\\/]|\.{1,2}[\\/]|[A-Za-z]:[\\/]|\\\\))/i;

function validateBridge(bridge: CoachToolBridge): void {
  const url = new URL(bridge.endpoint);
  if (bridge.schema_version !== "coach_tool_bridge.v1" || url.protocol !== "http:" ||
      !["127.0.0.1", "localhost"].includes(url.hostname) || !url.port ||
      url.pathname !== "/api/coach/tools/execute" || url.search || url.hash) {
    throw new Error("Product command bridge is unavailable");
  }
}

function containsForbidden(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbidden);
  if (!isRecord(value)) {
    return typeof value === "string" && PATH_OR_URL_TEXT.test(value);
  }
  return Object.entries(value).some(([key, child]) => {
    const normalized = key.toLowerCase().replaceAll("-", "_");
    return FORBIDDEN_KEYS.has(normalized) || normalized.includes("path") || normalized.includes("credential") || containsForbidden(child);
  });
}

function containsUnsafeResult(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsUnsafeResult);
  if (!isRecord(value)) {
    return typeof value === "string" && PATH_OR_URL_TEXT.test(value);
  }
  return Object.entries(value).some(([key, child]) => {
    const normalized = key.toLowerCase().replaceAll("-", "_");
    return FORBIDDEN_KEYS.has(normalized) || normalized.endsWith("_path") ||
      normalized.endsWith("_url") || normalized.includes("credential") ||
      containsUnsafeResult(child);
  });
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

function hasValidCommandParameters(commandName: ProductCommandName, parameters: Record<string, unknown>): boolean {
  if (commandName === "kovaak_scores.lookup") {
    return hasExactKeys(parameters, ["profile_ref"]) &&
      typeof parameters.profile_ref === "string" && /^steam_profile:[1-9]\d*$/.test(parameters.profile_ref);
  }
  if (commandName === "kovaak_scores.refresh_connected") return hasExactKeys(parameters, []);
  return true;
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

export function createProductCommandTool(
  bridge: CoachToolBridge,
  options: ProductCommandToolOptions = {},
) {
  validateBridge(bridge);
  const excludedCommands = new Set(options.excludedCommands ?? []);
  const commandNames = PRODUCT_COMMAND_NAMES.filter((name) => !excludedCommands.has(name));
  const commandSchema = Type.Union(commandNames.map((name) => Type.Literal(name)));
  return {
    name: "run_product_command",
    label: "Run product command",
    description: "查询分析数据、导航或准备训练动作。Evidence：调用 analysis.evidence.list（仅传 analysis_ref），返回各 segment_ref 及 available_channels。事件：调用 analysis.events.list（传 analysis_ref 与 scope），表结果含 table_ref 与 field_catalog。不要猜测 ref，只用已返回的 ref。写操作需用户授权。不得提交路径、URL、credential 或任意 payload。",
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
      const isKovaakScoreCommand = KOVAAK_SCORE_COMMANDS.has(params.command_name);
      if (!commandNames.includes(params.command_name)) {
        throw new Error("Product command is not available for this turn");
      }
      if (!isRecord(params.parameters) ||
          !hasValidCommandParameters(params.command_name, params.parameters) ||
          (params.instruction_quote !== undefined && (
            typeof params.instruction_quote !== "string" || !params.instruction_quote ||
            params.instruction_quote.length > 512 || PATH_OR_URL_TEXT.test(params.instruction_quote)
          )) ||
          (!isKovaakScoreCommand && containsForbidden(params.parameters))) {
        throw new Error("Product command contains unsupported fields");
      }
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
      const secrets = [bridge.bearer_token, bridge.desktop_token].filter((value): value is string => Boolean(value));
      if (containsUnsafeResult(providerResult) || secrets.some((secret) => responseText.includes(secret))) {
        throw new Error("Product command returned an invalid result");
      }
      return { content: [{ type: "text", text: responseText }], details: { event } };
    },
  };
}
