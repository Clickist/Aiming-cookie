import { makeSpikeError, isRecord, type SpikeRuntimeEvent } from "./contracts.ts";

type MapperOptions = {
  runId: string;
  clock: () => string;
  emit: (event: SpikeRuntimeEvent) => void;
};

function detailsFrom(value: unknown): unknown {
  return isRecord(value) && "details" in value ? value.details ?? null : null;
}

function usageFrom(message: unknown): Record<string, unknown> | null {
  if (!isRecord(message) || !isRecord(message.usage)) return null;
  const usage = message.usage;
  return {
    input: typeof usage.input === "number" ? usage.input : 0,
    output: typeof usage.output === "number" ? usage.output : 0,
    cacheRead: typeof usage.cacheRead === "number" ? usage.cacheRead : 0,
    cacheWrite: typeof usage.cacheWrite === "number" ? usage.cacheWrite : 0,
    totalTokens: typeof usage.totalTokens === "number" ? usage.totalTokens : 0,
  };
}

function stopReasonFrom(message: unknown): string {
  return isRecord(message) && typeof message.stopReason === "string" ? message.stopReason : "unknown";
}

/** Maps the supported Pi Agent event subset into the Spike-only runtime envelope. */
export function createEventMapper({ runId, clock, emit }: MapperOptions) {
  let sequence = 0;
  let emittedError = false;
  const publish = (type: string, payload: Record<string, unknown>) => {
    emit({
      schema_version: "coach_runtime_event.v0",
      run_id: runId,
      sequence: ++sequence,
      emitted_at: clock(),
      type,
      payload: { ...payload },
    });
  };
  const publishError = () => {
    if (emittedError) return;
    emittedError = true;
    publish("run.error", {
      error: makeSpikeError({
        category: "internal_unknown",
        code: "spike_internal_error",
        message: "Fixture tool execution failed",
        retryable: false,
        trace_id: null,
        details: null,
      }),
    });
  };

  return (event: unknown) => {
    if (!isRecord(event) || typeof event.type !== "string") return;
    switch (event.type) {
      case "agent_start":
        publish("run.started", {});
        return;
      case "agent_end":
        publish("run.completed", {});
        return;
      case "message_update": {
        const assistantEvent = event.assistantMessageEvent;
        if (isRecord(assistantEvent) && assistantEvent.type === "text_delta" && typeof assistantEvent.delta === "string") {
          publish("assistant.delta", { text: assistantEvent.delta });
        }
        return;
      }
      case "message_end":
        if (isRecord(event.message) && event.message.role === "assistant") {
          publish("assistant.completed", {
            stop_reason: stopReasonFrom(event.message),
            usage: usageFrom(event.message),
          });
        }
        return;
      case "tool_execution_start":
        publish("tool.started", {
          tool_call_id: typeof event.toolCallId === "string" ? event.toolCallId : "",
          tool_name: typeof event.toolName === "string" ? event.toolName : "",
          input: event.args ?? null,
        });
        return;
      case "tool_execution_update":
        publish("tool.progress", {
          tool_call_id: typeof event.toolCallId === "string" ? event.toolCallId : "",
          tool_name: typeof event.toolName === "string" ? event.toolName : "",
          details: detailsFrom(event.partialResult),
        });
        return;
      case "tool_execution_end": {
        const isError = event.isError === true;
        publish("tool.completed", {
          tool_call_id: typeof event.toolCallId === "string" ? event.toolCallId : "",
          tool_name: typeof event.toolName === "string" ? event.toolName : "",
          ok: !isError,
          details: detailsFrom(event.result),
        });
        if (isError) publishError();
        return;
      }
      default:
        return;
    }
  };
}
