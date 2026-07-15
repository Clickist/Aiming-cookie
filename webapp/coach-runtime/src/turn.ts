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
};

type TurnOptions = {
  streamFn?: StreamFn;
};

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
  const history = conversational.slice(0, -1).map((message) => toHistoryMessage(message, model));
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

function responseSchemaFor(rawRequest: unknown): CoachRuntimeTurnSchema {
  return isRecord(rawRequest) && rawRequest.schema_version === COACH_RUNTIME_TURN_SCHEMA_V0
    ? COACH_RUNTIME_TURN_SCHEMA_V0
    : COACH_RUNTIME_TURN_SCHEMA;
}

function errorCode(error: unknown): string {
  return error instanceof ProviderProfileError ? error.code : "turn_failed";
}

function extractBridgeSecrets(rawRequest: unknown): string[] {
  if (!isRecord(rawRequest) || !isRecord(rawRequest.tool_bridge)) return [];
  return [rawRequest.tool_bridge.bearer_token, rawRequest.tool_bridge.desktop_token]
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

const MANDATORY_POLICY = "\n\nMandatory Coach policy: use only registered product tools; distinguish measured, deterministic_rule, research_supported, community_consensus, and experimental claims; never invent that an action succeeded; never reveal bridge tokens, paths, URLs, credentials, raw traces, or arbitrary payloads.";

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

export async function runCoachTurn(rawRequest: unknown, options: TurnOptions = {}): Promise<CoachRuntimeTurnResponse> {
  const responseSchema = responseSchemaFor(rawRequest);
  const secrets = [...extractRuntimeSecrets(rawRequest), ...extractBridgeSecrets(rawRequest)];
  let agent: {
    prompt: (input: unknown) => Promise<void>;
    state: { messages: unknown[]; tools: Array<{ name: string }> };
  } | null = null;
  try {
    const request = parseRequest(rawRequest);
    const resolved = await resolveProviderModel(request.model);
    const { Agent } = (await loadPiAgent()) as {
      Agent: new (opts: Record<string, unknown>) => {
        prompt: (input: unknown) => Promise<void>;
        state: { messages: unknown[]; tools: Array<{ name: string }> };
      };
    };
    const { history, prompt } = splitConversation(request.messages, resolved.model);
    const streamFn = options.streamFn ?? createModelsStreamFn(resolved.models);
    const tools = [createAnalysisSummaryTool(request.analysis_summary)];
    if (request.schema_version === COACH_RUNTIME_TURN_SCHEMA_V1) {
      tools.push(createCoachKnowledgeTool() as never);
    }
    if (request.schema_version === COACH_RUNTIME_TURN_SCHEMA_V1 && request.tool_bridge) {
      tools.push(createProductCommandTool(request.tool_bridge) as never);
    }
    agent = new Agent({
      streamFn,
      initialState: {
        systemPrompt: `${resolveSystemPrompt(request.system_prompt)}${MANDATORY_POLICY}`,
        model: resolved.model,
        tools,
        messages: history,
      },
    });

    await agent.prompt(prompt);
    const reply = redactRuntimeSecrets(extractAssistantReply(agent.state.messages), secrets);
    return successResponse(reply, [], request.schema_version, collectToolEvents(agent.state.messages));
  } catch (error) {
    return failureResponse(
      makeError({
        category: "coach_runtime",
        code: errorCode(error),
        message: redactRuntimeSecrets(error instanceof Error ? error.message : String(error), secrets),
        retryable: false,
      }),
      [],
      responseSchema,
      agent === null ? [] : collectToolEvents(agent.state.messages),
    );
  }
}

export async function runCoachTurnWithFakeStream(
  rawRequest: unknown,
  replyText?: string,
): Promise<CoachRuntimeTurnResponse> {
  return runCoachTurn(rawRequest, { streamFn: createFakeStreamFn(replyText) });
}
