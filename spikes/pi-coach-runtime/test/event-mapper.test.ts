import assert from "node:assert/strict";
import test from "node:test";

import { createEventMapper } from "../src/event-mapper.ts";

const FIXED_TIME = "2026-07-11T00:00:00.000Z";

function mapperFixture() {
  const emitted: Array<{ schema_version: string; run_id: string; sequence: number; emitted_at: string; type: string; payload: Record<string, unknown> }> = [];
  const mapper = createEventMapper({
    runId: "run-mapper-1",
    clock: () => FIXED_TIME,
    emit: (event) => emitted.push(event),
  });
  return { mapper, emitted };
}

test("mapper emits coach_runtime_event.v0 with monotonic sequence", () => {
  const { mapper, emitted } = mapperFixture();
  mapper({ type: "agent_start" });
  mapper({
    type: "message_end",
    message: {
      role: "assistant",
      stopReason: "stop",
      usage: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0, totalTokens: 3 },
    },
  });
  mapper({ type: "agent_end", messages: [] });
  assert.deepEqual(emitted.map((event) => event.sequence), [1, 2, 3]);
  assert.deepEqual(emitted.map((event) => event.type), ["run.started", "assistant.completed", "run.completed"]);
  for (const event of emitted) {
    assert.equal(event.schema_version, "coach_runtime_event.v0");
    assert.equal(event.run_id, "run-mapper-1");
    assert.equal(event.emitted_at, FIXED_TIME);
  }
});

test("mapper only exposes approved event types and payload fields", () => {
  const { mapper, emitted } = mapperFixture();
  mapper({
    type: "tool_execution_start",
    toolCallId: "tool-1",
    toolName: "get_analysis_summary",
    args: { analysis_id: "analysis-fixture-1", extra: "not-secret" },
  });
  assert.deepEqual(emitted, [
    {
      schema_version: "coach_runtime_event.v0",
      run_id: "run-mapper-1",
      sequence: 1,
      emitted_at: FIXED_TIME,
      type: "tool.started",
      payload: {
        tool_call_id: "tool-1",
        tool_name: "get_analysis_summary",
        input: { analysis_id: "analysis-fixture-1", extra: "not-secret" },
      },
    },
  ]);
});

test("mapper ignores thinking and raw provider payloads", () => {
  const { mapper, emitted } = mapperFixture();
  mapper({
    type: "message_update",
    message: { role: "assistant", provider: "provider", raw: { secret: "never-map" } },
    assistantMessageEvent: { type: "thinking_delta", delta: "hidden" },
  });
  assert.deepEqual(emitted, []);
});
