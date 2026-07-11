import { loadPiAi } from "./pi-source.ts";

type AssistantMessage = {
  role: "assistant";
  content: Array<Record<string, unknown>>;
  api: string;
  provider: string;
  model: string;
  usage: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
    totalTokens: number;
    cost: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  };
  stopReason: "stop" | "toolUse";
  timestamp: number;
};

function usage(input: number, output: number) {
  return {
    input,
    output,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: input + output,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function assistant(content: Array<Record<string, unknown>>, stopReason: "stop" | "toolUse"): AssistantMessage {
  return {
    role: "assistant",
    content,
    api: "openai-completions",
    provider: "aiming-cookie-coach-fixture",
    model: "fixture-model",
    usage: usage(8, 12),
    stopReason,
    timestamp: 0,
  };
}

/** Injectable StreamFn for tests: single-turn text reply without network. */
export function createFakeStreamFn(replyText = "fixture coach reply") {
  return async () => {
    const ai = await loadPiAi();
    const createAssistantMessageEventStream = ai.createAssistantMessageEventStream as () => {
      push(event: unknown): void;
      end(result: unknown): void;
    };
    const stream = createAssistantMessageEventStream();

    queueMicrotask(() => {
      const partial = assistant([{ type: "text", text: "" }], "stop");
      const message = assistant([{ type: "text", text: replyText }], "stop");
      stream.push({ type: "start", partial });
      stream.push({ type: "text_start", contentIndex: 0, partial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: replyText, partial: message });
      stream.push({ type: "text_end", contentIndex: 0, content: replyText, partial: message });
      stream.push({ type: "done", reason: "stop", message });
      stream.end(message);
    });

    return stream;
  };
}