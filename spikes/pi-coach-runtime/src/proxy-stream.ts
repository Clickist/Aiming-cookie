import { loadPiAi } from "./pi-source.ts";

type ProxyOptions = {
  endpoint: string;
  runId: string;
  fetchImpl?: typeof fetch;
};

type WireUsage = { input: number; output: number; total_tokens: number };
type WireEvent =
  | { type: "start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "done"; stop_reason: "stop" | "toolUse"; usage: WireUsage }
  | { type: "error"; code: string; message: string };

type AssistantMessage = {
  role: "assistant";
  content: Array<Record<string, unknown>>;
  api: string;
  provider: "aiming-cookie-proxy-fixture";
  model: string;
  usage: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
    totalTokens: number;
    cost: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  };
  stopReason: "stop" | "toolUse" | "error" | "aborted";
  errorMessage?: string;
  timestamp: number;
};

class ProxyProtocolError extends Error {}

function message(model: string, content: Array<Record<string, unknown>>, stopReason: AssistantMessage["stopReason"], usage?: WireUsage, errorMessage?: string): AssistantMessage {
  return {
    role: "assistant",
    content,
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    model,
    usage: {
      input: usage?.input ?? 0,
      output: usage?.output ?? 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: usage?.total_tokens ?? 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason,
    ...(errorMessage ? { errorMessage } : {}),
    timestamp: 0,
  };
}

function parseWireEvent(line: string): WireEvent {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new ProxyProtocolError("Invalid NDJSON");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value) || typeof (value as { type?: unknown }).type !== "string") {
    throw new ProxyProtocolError("Invalid NDJSON event");
  }
  const event = value as Record<string, unknown>;
  switch (event.type) {
    case "start":
      return { type: "start" };
    case "text_delta":
      if (typeof event.delta !== "string") throw new ProxyProtocolError("Invalid text delta");
      return { type: "text_delta", delta: event.delta };
    case "tool_call":
      if (typeof event.id !== "string" || typeof event.name !== "string" || typeof event.arguments !== "object" || event.arguments === null || Array.isArray(event.arguments)) {
        throw new ProxyProtocolError("Invalid tool call");
      }
      return { type: "tool_call", id: event.id, name: event.name, arguments: event.arguments as Record<string, unknown> };
    case "done": {
      const usage = event.usage as Record<string, unknown> | undefined;
      if ((event.stop_reason !== "stop" && event.stop_reason !== "toolUse") || !usage || typeof usage.input !== "number" || typeof usage.output !== "number" || typeof usage.total_tokens !== "number") {
        throw new ProxyProtocolError("Invalid done event");
      }
      return { type: "done", stop_reason: event.stop_reason, usage: usage as WireUsage };
    }
    case "error":
      if (typeof event.code !== "string" || typeof event.message !== "string") throw new ProxyProtocolError("Invalid error event");
      return { type: "error", code: event.code, message: event.message };
    default:
      throw new ProxyProtocolError("Unknown NDJSON event");
  }
}

/** Converts the spike-only local fake_llm_proxy.v0 NDJSON wire into Pi's StreamFn contract. */
export function createProxyStreamFn({ endpoint, runId, fetchImpl = fetch }: ProxyOptions) {
  return async (model: { id: string }, context: { messages: unknown[]; tools?: unknown[] }, options?: { signal?: AbortSignal }) => {
    const ai = await loadPiAi();
    const createAssistantMessageEventStream = ai.createAssistantMessageEventStream as () => {
      push(event: unknown): void;
      end(result: unknown): void;
    };
    const stream = createAssistantMessageEventStream();

    queueMicrotask(async () => {
      let finished = false;
      const content: Array<Record<string, unknown>> = [];
      let textOpen = false;
      const closeText = (partial: AssistantMessage) => {
        if (!textOpen) return;
        const index = content.length - 1;
        const block = content[index];
        stream.push({ type: "text_end", contentIndex: index, content: block?.text ?? "", partial });
        textOpen = false;
      };
      const finishError = (code: "proxy_http_error" | "proxy_protocol_error" | "proxy_network_error" | "proxy_aborted") => {
        if (finished) return;
        finished = true;
        const stopReason = code === "proxy_aborted" ? "aborted" : "error";
        const error = message(model.id, [], stopReason, undefined, code);
        stream.push({ type: "error", reason: stopReason, error });
        stream.end(error);
      };

      try {
        const response = await fetchImpl(endpoint, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/x-ndjson",
          },
          body: JSON.stringify({
            schema_version: "fake_llm_proxy.v0",
            run_id: runId,
            model: model.id,
            messages: context.messages,
            tools: context.tools ?? [],
          }),
          signal: options?.signal,
        });
        if (!response.ok) {
          finishError("proxy_http_error");
          return;
        }
        if (!response.body) throw new ProxyProtocolError("Missing NDJSON response body");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const handleLine = (line: string) => {
          if (line.trim() === "") return;
          if (finished) throw new ProxyProtocolError("Event after terminal event");
          const event = parseWireEvent(line);
          const partial = message(model.id, content, "stop");
          if (event.type === "start") {
            stream.push({ type: "start", partial });
            return;
          }
          if (event.type === "text_delta") {
            if (!textOpen) {
              content.push({ type: "text", text: "" });
              stream.push({ type: "text_start", contentIndex: content.length - 1, partial: message(model.id, content, "stop") });
              textOpen = true;
            }
            const text = content[content.length - 1];
            text.text = `${text.text ?? ""}${event.delta}`;
            stream.push({ type: "text_delta", contentIndex: content.length - 1, delta: event.delta, partial: message(model.id, content, "stop") });
            return;
          }
          if (event.type === "tool_call") {
            closeText(partial);
            const toolCall = { type: "toolCall", id: event.id, name: event.name, arguments: event.arguments };
            content.push(toolCall);
            const toolIndex = content.length - 1;
            const toolPartial = message(model.id, content, "toolUse");
            stream.push({ type: "toolcall_start", contentIndex: toolIndex, partial: toolPartial });
            stream.push({ type: "toolcall_delta", contentIndex: toolIndex, delta: JSON.stringify(event.arguments), partial: toolPartial });
            stream.push({ type: "toolcall_end", contentIndex: toolIndex, toolCall, partial: toolPartial });
            return;
          }
          if (event.type === "done") {
            closeText(message(model.id, content, event.stop_reason, event.usage));
            const done = message(model.id, content, event.stop_reason, event.usage);
            finished = true;
            stream.push({ type: "done", reason: event.stop_reason, message: done });
            stream.end(done);
            return;
          }
          finishError("proxy_http_error");
        };

        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          let newline = buffer.indexOf("\n");
          while (newline !== -1) {
            handleLine(buffer.slice(0, newline).replace(/\r$/, ""));
            buffer = buffer.slice(newline + 1);
            newline = buffer.indexOf("\n");
          }
          if (done) break;
        }
        const trailing = buffer.trim();
        if (trailing) handleLine(trailing);
        if (!finished) throw new ProxyProtocolError("NDJSON response ended without terminal event");
      } catch (error) {
        if (options?.signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
          finishError("proxy_aborted");
        } else if (error instanceof ProxyProtocolError) {
          finishError("proxy_protocol_error");
        } else {
          finishError("proxy_network_error");
        }
      }
    });

    return stream;
  };
}
