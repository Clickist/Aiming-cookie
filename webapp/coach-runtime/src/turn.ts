import {
  COACH_RUNTIME_TURN_SCHEMA,
  failureResponse,
  isRecord,
  makeError,
  successResponse,
  type CoachRuntimeMessage,
  type CoachRuntimeTurnRequest,
  type CoachRuntimeTurnResponse,
} from "./contracts.ts";
import { createAnalysisSummaryTool } from "./analysis-summary-tool.ts";
import { createFakeStreamFn } from "./fake-stream.ts";
import { resolveSystemPrompt } from "./load-system-prompt.ts";
import { loadPiAgent } from "./pi-source.ts";
import { buildCoachModel, createApiKeyResolver, createOpenAiCompatibleStreamFn, type StreamFn } from "./stream-openai-compatible.ts";

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

type TurnOptions = {
  streamFn?: StreamFn;
};

function parseRequest(raw: unknown): CoachRuntimeTurnRequest {
  if (!isRecord(raw)) {
    throw new Error("Request must be a JSON object");
  }
  if (raw.schema_version !== COACH_RUNTIME_TURN_SCHEMA) {
    throw new Error(`Unsupported schema_version: ${String(raw.schema_version)}`);
  }
  if (typeof raw.run_id !== "string" || typeof raw.user_id !== "string") {
    throw new Error("run_id and user_id are required strings");
  }
  if (!Array.isArray(raw.messages)) {
    throw new Error("messages must be an array");
  }
  const messages: CoachRuntimeMessage[] = raw.messages.map((item) => {
    if (!isRecord(item) || (item.role !== "user" && item.role !== "assistant" && item.role !== "system")) {
      throw new Error("Invalid message role");
    }
    if (typeof item.content !== "string") {
      throw new Error("Invalid message content");
    }
    return { role: item.role, content: item.content };
  });
  if (!isRecord(raw.model)) {
    throw new Error("model config is required");
  }
  const model = raw.model;
  if (typeof model.base_url !== "string" || typeof model.api_key_env !== "string" || typeof model.model_id !== "string") {
    throw new Error("model.base_url, model.api_key_env, and model.model_id are required");
  }
  const analysisSummary =
    raw.analysis_summary === null || typeof raw.analysis_summary === "string" ? raw.analysis_summary : null;
  const systemPrompt = typeof raw.system_prompt === "string" ? raw.system_prompt : undefined;
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA,
    run_id: raw.run_id,
    user_id: raw.user_id,
    messages,
    analysis_summary: analysisSummary,
    system_prompt: systemPrompt,
    model: {
      base_url: model.base_url,
      api_key_env: model.api_key_env,
      model_id: model.model_id,
    },
  };
}

function toHistoryMessage(message: CoachRuntimeMessage) {
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
    api: "openai-completions",
    provider: "aiming-cookie-coach",
    model: "history",
    usage: EMPTY_USAGE,
    stopReason: "stop" as const,
    timestamp: Date.now(),
  };
}

function splitConversation(messages: CoachRuntimeMessage[]) {
  const conversational = messages.filter((message) => message.role !== "system");
  if (conversational.length === 0) {
    throw new Error("At least one user message is required");
  }
  const last = conversational[conversational.length - 1];
  if (last.role !== "user") {
    throw new Error("Last message must be from user");
  }
  const history = conversational.slice(0, -1).map(toHistoryMessage);
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

export async function runCoachTurn(rawRequest: unknown, options: TurnOptions = {}): Promise<CoachRuntimeTurnResponse> {
  try {
    const request = parseRequest(rawRequest);
    const { Agent } = (await loadPiAgent()) as { Agent: new (opts: Record<string, unknown>) => { prompt: (input: unknown) => Promise<void>; state: { messages: unknown[]; tools: Array<{ name: string }> } } };
    const { history, prompt } = splitConversation(request.messages);
    const streamFn = options.streamFn ?? (await createOpenAiCompatibleStreamFn());
    const agent = new Agent({
      streamFn,
      getApiKey: createApiKeyResolver(request.model.api_key_env),
      initialState: {
        systemPrompt: resolveSystemPrompt(request.system_prompt),
        model: buildCoachModel(request.model),
        tools: [createAnalysisSummaryTool(request.analysis_summary)],
        messages: history,
      },
    });

    await agent.prompt(prompt);
    const reply = extractAssistantReply(agent.state.messages);
    return successResponse(reply);
  } catch (error) {
    return failureResponse(
      makeError({
        category: "coach_runtime",
        code: "turn_failed",
        message: error instanceof Error ? error.message : String(error),
        retryable: false,
      }),
    );
  }
}

export async function runCoachTurnWithFakeStream(
  rawRequest: unknown,
  replyText?: string,
): Promise<CoachRuntimeTurnResponse> {
  return runCoachTurn(rawRequest, { streamFn: createFakeStreamFn(replyText) });
}