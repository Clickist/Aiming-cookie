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
  "analysis.compare", "navigation.open", "analysis.create_from_run", "analysis.retry",
  "training_plan.generate_draft", "training_plan.save", "training_plan.activate",
  "training_plan.pause", "training_plan.adjust", "training_plan.review",
  "analysis.metrics.distribution", "analysis.evidence.list", "analysis.evidence.signal_window",
  "analysis.evidence.compare", "analysis.run_facts.get", "analysis.outcomes.timeline",
  "analysis.events.list", "analysis.events.get", "analysis.events.rank",
  "analysis.events.filter", "analysis.events.aggregate", "analysis.events.co_occurrence",
  "analysis.events.sequence", "profile.aiming.snapshot",
] as const;
type ProductCommandName = typeof PRODUCT_COMMAND_NAMES[number];

const WRITE_COMMANDS = new Set<ProductCommandName>([
  "analysis.create_from_run", "analysis.retry", "training_plan.generate_draft",
  "training_plan.save", "training_plan.activate", "training_plan.pause", "training_plan.adjust",
]);
const FORBIDDEN_KEYS = new Set([
  "owner", "owner_id", "owner_scope", "actor", "risk", "path", "video_path",
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

function safeCommandEvent(result: Record<string, unknown>, commandName: string) {
  if (result.schema_version !== "coach_product_command_result.v1" || typeof result.command_id !== "string" ||
      typeof result.status !== "string" || !PRODUCT_COMMAND_STATUSES.has(result.status) ||
      typeof result.audit_ref !== "string") {
    throw new Error("Product command returned an invalid result");
  }
  return {
    type: "product_command" as const,
    command_id: result.command_id,
    command_name: commandName,
    status: result.status,
    result_ref: typeof result.result_ref === "string" ? result.result_ref : null,
    audit_ref: result.audit_ref,
    ui_event: isRecord(result.ui_event) ? result.ui_event : null,
    warning_or_error: isRecord(result.warning_or_error) ? result.warning_or_error : null,
  };
}

export function createProductCommandTool(bridge: CoachToolBridge) {
  validateBridge(bridge);
  const commandSchema = Type.Union(PRODUCT_COMMAND_NAMES.map((name) => Type.Literal(name)));
  return {
    name: "run_product_command",
    label: "Run product command",
    description: "通过 Aiming Cookie 产品命令层查询、导航或提出可恢复动作。写操作授权与确认只由可信 UI/backend 决定；不得提交 authorization、confirmation、owner、risk、路径、URL、credential 或任意 payload。",
    parameters: Type.Object({
      command_name: commandSchema,
      parameters: Type.Object({}, { additionalProperties: true }),
      idempotency_key: Type.Optional(Type.String({ maxLength: 256 })),
    }, { additionalProperties: false }),
    async execute(_id: string, params: { command_name: ProductCommandName; parameters: Record<string, unknown>; idempotency_key?: string }, signal?: AbortSignal) {
      if (!PRODUCT_COMMAND_NAMES.includes(params.command_name) || !isRecord(params.parameters) || containsForbidden(params.parameters)) {
        throw new Error("Product command contains unsupported fields");
      }
      const body: Record<string, unknown> = {
        command_name: params.command_name,
        parameters: params.parameters,
      };
      if (WRITE_COMMANDS.has(params.command_name)) body.idempotency_key = params.idempotency_key || stableKey(bridge, params.command_name, params.parameters);
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
      const responseText = JSON.stringify(parsed);
      const secrets = [bridge.bearer_token, bridge.desktop_token].filter((value): value is string => Boolean(value));
      if (!isRecord(parsed) || containsUnsafeResult(parsed) || secrets.some((secret) => responseText.includes(secret))) {
        throw new Error("Product command returned an invalid result");
      }
      const event = safeCommandEvent(parsed, params.command_name);
      return { content: [{ type: "text", text: JSON.stringify(parsed) }], details: { event } };
    },
  };
}
