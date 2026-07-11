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
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    model: "fixture-model",
    usage: usage(10, 4),
    stopReason,
    timestamp: 0,
  };
}

/** A two-turn in-memory StreamFn fixture: tool use first, final text second. */
export function createFixtureStreamFn() {
  let turn = 0;

  return async () => {
    const ai = await loadPiAi();
    const createAssistantMessageEventStream = ai.createAssistantMessageEventStream as () => {
      push(event: unknown): void;
      end(result: unknown): void;
    };
    const stream = createAssistantMessageEventStream();
    const currentTurn = turn++;

    queueMicrotask(() => {
      if (currentTurn === 0) {
        const toolCall = {
          type: "toolCall",
          id: "tool-fixture-1",
          name: "get_analysis_summary",
          arguments: { analysis_id: "analysis-fixture-1" },
        };
        const partial = assistant([{ ...toolCall, arguments: {} }], "toolUse");
        const message = assistant([toolCall], "toolUse");
        stream.push({ type: "start", partial });
        stream.push({ type: "toolcall_start", contentIndex: 0, partial });
        stream.push({ type: "toolcall_delta", contentIndex: 0, delta: '{"analysis_id":"analysis-fixture-1"}', partial: message });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: message });
        stream.push({ type: "done", reason: "toolUse", message });
        stream.end(message);
        return;
      }

      const partial = assistant([{ type: "text", text: "" }], "stop");
      const message = assistant([{ type: "text", text: "fixture coach answer" }], "stop");
      stream.push({ type: "start", partial });
      stream.push({ type: "text_start", contentIndex: 0, partial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: "fixture coach answer", partial: message });
      stream.push({ type: "text_end", contentIndex: 0, content: "fixture coach answer", partial: message });
      stream.push({ type: "done", reason: "stop", message });
      stream.end(message);
    });

    return stream;
  };
}
