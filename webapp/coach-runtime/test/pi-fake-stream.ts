import { loadPiAi } from "../src/pi-source.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

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

function assistant(content: Array<Record<string, unknown>>, stopReason: "stop" | "toolUse" | "error") {
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

export async function streamAssistant(
  content: Array<Record<string, unknown>>,
  stopReason: "stop" | "toolUse" | "error",
) {
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
