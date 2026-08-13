import { loadPiAi } from "../src/pi-source.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";
import { runCoachTurn } from "../src/turn.ts";

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

type ToolCall = {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
};

function assistant(content: Array<Record<string, unknown>>, stopReason: "stop" | "toolUse") {
  return {
    role: "assistant" as const,
    content,
    api: "openai-completions",
    provider: "aiming-cookie-coach-e2e",
    model: "fixture-model",
    usage: EMPTY_USAGE,
    stopReason,
    timestamp: 0,
  };
}

async function streamAssistant(content: Array<Record<string, unknown>>, stopReason: "stop" | "toolUse") {
  const ai = await loadPiAi();
  const createStream = ai.createAssistantMessageEventStream as () => {
    push(event: unknown): void;
    end(result: unknown): void;
  };
  const stream = createStream();
  queueMicrotask(() => {
    const initial = assistant([], stopReason);
    const final = assistant(content, stopReason);
    stream.push({ type: "start", partial: initial });
    for (const [contentIndex, block] of content.entries()) {
      if (block.type === "toolCall") {
        const toolCall = block as ToolCall;
        const partial = assistant([
          { type: "toolCall", id: toolCall.id, name: toolCall.name, arguments: {} },
        ], stopReason);
        stream.push({ type: "toolcall_start", contentIndex, partial });
        stream.push({
          type: "toolcall_delta",
          contentIndex,
          delta: JSON.stringify(toolCall.arguments),
          partial,
        });
        stream.push({ type: "toolcall_end", contentIndex, toolCall, partial: final });
      } else if (block.type === "text") {
        const text = String(block.text ?? "");
        stream.push({ type: "text_start", contentIndex, partial: initial });
        stream.push({ type: "text_delta", contentIndex, delta: text, partial: final });
        stream.push({ type: "text_end", contentIndex, content: text, partial: final });
      }
    }
    stream.push({ type: "done", reason: stopReason, message: final });
    stream.end(final);
  });
  return stream;
}

function toolResultText(context: unknown, toolName: string): string {
  if (!context || typeof context !== "object") throw new Error("missing Pi context");
  const messages = (context as { messages?: unknown }).messages;
  if (!Array.isArray(messages)) throw new Error("missing Pi messages");
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message || typeof message !== "object") continue;
    const record = message as { role?: unknown; toolName?: unknown; content?: unknown };
    if (record.role !== "toolResult" || record.toolName !== toolName || !Array.isArray(record.content)) continue;
    const first = record.content[0];
    if (first && typeof first === "object" && typeof (first as { text?: unknown }).text === "string") {
      return (first as { text: string }).text;
    }
  }
  throw new Error(`missing ${toolName} result`);
}

function knowledgeQueryFromAnalysis(analysisSummary: string) {
  const analysis = JSON.parse(analysisSummary) as {
    diagnosis?: { issues?: Array<{ signal?: unknown; metric_refs?: unknown }> };
  };
  const issue = analysis.diagnosis?.issues?.[0];
  if (!issue || typeof issue.signal !== "string") throw new Error("analysis has no issue signal");
  const metricRefs = Array.isArray(issue.metric_refs)
    ? issue.metric_refs
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.startsWith("metric:") ? value : `metric:${value}`)
    : [];
  return {
    issue_signal: issue.signal,
    metric_refs: metricRefs,
    supported_use: "explanation_only",
  };
}

export function createAnalysisKnowledgeE2EStream(analysisSummary: string): StreamFn {
  const query = knowledgeQueryFromAnalysis(analysisSummary);
  let callCount = 0;
  return async (_model, context) => {
    callCount += 1;
    if (callCount === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "knowledge-call",
        name: "get_coach_knowledge",
        arguments: query,
      }], "toolUse");
    }
    const result = JSON.parse(toolResultText(context, "get_coach_knowledge")) as {
      entries?: Array<{ entry_ref?: unknown }>;
    };
    const refs = (result.entries ?? [])
      .map((entry) => entry.entry_ref)
      .filter((value): value is string => typeof value === "string");
    if (refs.length === 0) throw new Error("knowledge retrieval returned no entries");
    return streamAssistant([{
      type: "text",
      text: `已根据当前 Analysis 检索 ${refs.join(", ")}`,
    }], "stop");
  };
}

export async function runAnalysisKnowledgeE2E(analysisSummary: string) {
  return runCoachTurn({
    schema_version: "coach_runtime_turn.v1",
    run_id: "analysis-knowledge-e2e",
    user_id: "e2e-owner",
    messages: [{ role: "user", content: "解释当前最优先问题" }],
    model: {
      kind: "builtin",
      provider_id: "anthropic",
      model_id: "claude-haiku-4-5",
    },
  }, { streamFn: createAnalysisKnowledgeE2EStream(analysisSummary) });
}
