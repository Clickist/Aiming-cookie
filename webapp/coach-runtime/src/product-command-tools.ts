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
  "kovaak_scores.lookup", "kovaak_scores.refresh_connected",
] as const;
type ProductCommandName = typeof PRODUCT_COMMAND_NAMES[number];
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

export function createProductCommandTool(bridge: CoachToolBridge) {
  validateBridge(bridge);
  const commandSchema = Type.Union(PRODUCT_COMMAND_NAMES.map((name) => Type.Literal(name)));
  return {
    name: "run_product_command",
    label: "Run product command",
    description: "通过 Aiming Cookie 产品命令层查询、导航或提出可恢复动作。Coach 可以准备用户明确陈述的训练事实；写入仍必须由可信 UI/backend 确认后执行。Evidence 必须从已附加的 analysis:N 开始：先调用 analysis.evidence.list，parameters 只传 analysis_ref（可选 limit/segment_kinds/issue_refs）；仅当结果返回 segment_ref 与 available_channels 后，才能调用 analysis.evidence.signal_window，并且 parameters 只传该 segment_ref 与从 available_channels 选择的 channel_keys。事件列表 analysis.events.list 只传 analysis_ref、scope='whole_run'、从 processed event table 目录选择的 event_kinds，以及可选 limit；scope='evidence_segment' 时还必须传已返回且 reachable 的 segment_ref。ProcessedEventTable 查询必须从上下文目录或成功结果取得 table_ref 与 field_catalog，不能用 analysis_ref 代替 table_ref：analysis.events.aggregate 只传 table_ref、数值 fields，以及可选 group_by='run_phase' 或已注册分类字段；analysis.events.rank 只传 table_ref、数值 field、direction='asc'|'desc'、predicates 数组和 limit；analysis.events.filter 只传 table_ref、predicates 与可选 limit；analysis.events.get 只传已返回的 table_ref 与 event_ref。不得猜 artifact/segment/table/event ref 或字段。删除 Analysis 使用 analysis.delete，parameters 必须精确为已附加上下文中的 {\"analysis_ref\":\"analysis:N\"}；仅对本轮已 reachable 的 Analysis，数字 shorthand N 会在可信 bridge 内规范化。删除必须等待可信 UI/backend 的结构化确认。写操作授权与确认只由可信 UI/backend 决定；不得提交 authorization、confirmation、owner、risk、路径、URL、credential 或任意 payload。",
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
      if (!PRODUCT_COMMAND_NAMES.includes(params.command_name) || !isRecord(params.parameters) ||
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
