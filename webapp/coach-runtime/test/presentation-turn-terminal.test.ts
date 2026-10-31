/**
 * Regression: runCoachTurn's stopped/failed responses must carry the
 * coach_presentation.v1 terminal snapshot so the NDJSON final frame is as rich
 * as the streaming partial frames (openui survives, generation_status is the
 * terminal one, secrets stay redacted).
 */
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-presentation-turn-terminal-"));
process.env.DATA_ROOT = dataRoot;

const { COACH_RUNTIME_TURN_SCHEMA_V1 } = await import("../src/contracts.ts");
const { runCoachTurn, stopCoachTurn } = await import("../src/turn.ts");
const { ensureAppDataDirs } = await import("../src/app-data.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

const SECRET = "sk-terminal-presentation-secret";
const REPLY = [
  `这局过早点火占了大半（key ${SECRET}）：`,
  "",
  "```openui-lang",
  "root = Card([m1])",
  "m1 = MetricIndicatorInline(\"23%\")",
  "```",
  "",
  "下一步只练停半拍。",
].join("\n");

function request(runId: string, threadId: number) {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
    run_id: runId,
    session_id: `coach-thread:${threadId}`,
    user_id: "test-user",
    messages: [{ role: "user", content: "分析这局" }],
    model: {
      kind: "builtin" as const,
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      credential: { type: "api_key" as const, key: SECRET },
    },
  };
}

test("failed turn response carries a failed presentation projection", async () => {
  ensureAppDataDirs();
  const runId = "presentation-turn-failed";
  const response = await runCoachTurn(request(runId, 9701), {
    streamFn: async () => streamAssistant([{ type: "text", text: REPLY }], "error"),
  });

  assert.equal(response.ok, false);
  assert.equal(response.error?.code, "provider_stream_interrupted");
  assert.ok(response.presentation, "failed turn response must carry a presentation snapshot");
  assert.equal(response.presentation.generation_status, "failed");
  assert.equal(response.presentation.message_id, runId);
  assert.equal(response.presentation.run_id, runId);
  assert.ok(
    response.presentation.segments.some((segment) => segment.kind === "openui"),
    "the openui segment must survive into the failed final response",
  );
  assert.ok(!JSON.stringify(response.presentation).includes(SECRET), "secret must stay redacted");
});

test("stopped turn response carries a stopped presentation projection", async () => {
  ensureAppDataDirs();
  const runId = "presentation-turn-stopped";
  const response = await runCoachTurn(request(runId, 9702), {
    streamFn: async () => streamAssistant([{ type: "text", text: REPLY }], "stop"),
    // Stop after the first streamed snapshot so the terminal response must
    // preserve that snapshot rather than invent or drop it.
    onPartial: () => {
      stopCoachTurn(runId);
    },
  });

  assert.equal(response.ok, false);
  assert.equal(response.error?.code, "stopped");
  assert.ok(response.presentation, "stopped turn response must carry a presentation snapshot");
  assert.equal(response.presentation.generation_status, "stopped");
  assert.equal(response.presentation.message_id, runId);
  assert.ok(
    response.presentation.segments.some((segment) => segment.kind === "openui"),
    "the openui segment must survive into the stopped final response",
  );
  assert.ok(!JSON.stringify(response.presentation).includes(SECRET), "secret must stay redacted");
});
