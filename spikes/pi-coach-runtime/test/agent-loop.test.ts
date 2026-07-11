import assert from "node:assert/strict";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import { createEventMapper } from "../src/event-mapper.ts";
import { createPythonAnalysisClient } from "../src/python-analysis-client.ts";
import { createFixtureStreamFn } from "../src/fake-stream.ts";
import { loadPiAgent } from "../src/pi-source.ts";

const FIXED_TIME = "2026-07-11T00:00:00.000Z";

function makeModel() {
  return {
    id: "fixture-model",
    name: "fixture model",
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    baseUrl: "http://127.0.0.1/fixture",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: 2048,
  };
}

async function runFixture(options: { failTool?: boolean } = {}) {
  const { Agent } = await loadPiAgent();
  const events: unknown[] = [];
  const mapper = createEventMapper({
    runId: "run-task-2",
    clock: () => FIXED_TIME,
    emit: (event) => events.push(event),
  });
  const agent = new Agent({
    streamFn: createFixtureStreamFn(),
    initialState: {
      model: makeModel(),
      tools: [createAnalysisSummaryTool({ client: createPythonAnalysisClient(), fail: options.failTool })],
    },
  });
  const unsubscribe = agent.subscribe(mapper);
  try {
    await agent.prompt("Use the analysis fixture.");
  } finally {
    unsubscribe();
  }
  return events as Array<{ type: string; payload: Record<string, unknown> }>;
}

test("real Pi tool forwards Python progress into tool.progress", async () => {
  const events = await runFixture();
  assert.ok(events.some((event) => event.type === "assistant.delta" && event.payload.text === "fixture coach answer"));
  assert.ok(events.some((event) => event.type === "tool.started"));
  assert.ok(events.some((event) => event.type === "tool.progress" && event.payload.details && (event.payload.details as { stage?: unknown }).stage === "loading_fixture"));
  assert.ok(events.some((event) => event.type === "tool.completed" && event.payload.ok === true));
});

test("read-only fixture tool forwards progress and returns deterministic summary", async () => {
  const tool = createAnalysisSummaryTool({ client: createPythonAnalysisClient() });
  const updates: unknown[] = [];
  const result = await tool.execute("tool-fixture-1", { analysis_id: "analysis-fixture-1" }, undefined, (update: unknown) => updates.push(update));
  assert.equal(updates.length, 1);
  assert.deepEqual(updates[0], { details: { stage: "loading_fixture" } });
  assert.deepEqual(result.details, {
    analysis_id: "analysis-fixture-1",
    schema_version: "analysis_result.v1",
    summary_type: "flicking",
    diagnosis: { summary: { fixture_signal: "stable" } },
    notes: ["fixture-only"],
  });
});

test("tool failure maps to one stable run.error without leaking stack", async () => {
  const events = await runFixture({ failTool: true });
  const errors = events.filter((event) => event.type === "run.error");
  assert.equal(errors.length, 1);
  const error = errors[0]?.payload.error as { code: string; message: string; details: unknown };
  assert.equal(error.code, "spike_internal_error");
  assert.ok(!error.message.includes("Error:"));
  assert.ok(!JSON.stringify(error).includes(process.cwd()));
});
