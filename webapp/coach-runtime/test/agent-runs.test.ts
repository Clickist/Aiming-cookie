import assert from "node:assert/strict";
import test from "node:test";

import {
  AgentRunError,
  createAgentRun,
  decideConfirmation,
  getAgentRun,
  resumeWaitingRuns,
  retryAgentRun,
  stopAgentRun,
  subscribeAgentRun,
  type AgentRunState,
} from "../src/agent-runs.ts";
import { waitForTask } from "../src/task-manager.ts";

test("createAgentRun returns a queued run with ISO UTC timestamps", () => {
  const run = createAgentRun("test-owner", "请分析这一局", { sessionId: 1 });
  assert.equal(run.schema_version, "coach_agent_run.v1");
  assert.ok(run.run_ref.startsWith("agent_run:"));
  assert.equal(run.session_id, 1);
  assert.equal(run.parent_run_ref, null);
  assert.equal(run.attempt, 1);
  // The background task runs synchronously up to the first await, so without
  // a provider.json the run already transitioned to queued-with-waiting by
  // the time createAgentRun returns.
  assert.equal(run.status, "queued");
  assert.equal(run.error?.code, "provider_unconfigured");
  assert.equal(run.finished_at, null);
  assert.match(run.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  assert.ok(run.created_at.endsWith("Z"));
});

test("createAgentRun rejects empty content", () => {
  assert.throws(
    () => createAgentRun("test-owner", "  ", { sessionId: 1 }),
    (error: unknown) => error instanceof AgentRunError && error.code === "invalid_text",
  );
});

test("a missing Provider leaves the run queued for automatic recovery", async () => {
  const created = createAgentRun("test-owner", "等 Provider 配好后继续", { sessionId: 2 });
  await waitForTask(created.run_ref);

  const run = getAgentRun("test-owner", created.run_ref);
  assert.ok(run);
  assert.equal(run.status, "queued");
  assert.equal(run.phase, "queued");
  assert.equal(run.error?.code, "provider_unconfigured");
  assert.equal(run.finished_at, null);
  const lastEvent = run.events[run.events.length - 1];
  assert.equal(lastEvent?.code, "provider_waiting");
});

test("getAgentRun returns null for a different owner", () => {
  const run = createAgentRun("owner-a", "hello", { sessionId: 3 });
  assert.ok(getAgentRun("owner-a", run.run_ref));
  assert.equal(getAgentRun("owner-b", run.run_ref), null);
});

test("retryAgentRun on a non-failed run throws retry_not_allowed", () => {
  const run = createAgentRun("test-owner", "hello retry", { sessionId: 4 });
  assert.throws(
    () => retryAgentRun("test-owner", run.run_ref),
    (error: unknown) => error instanceof AgentRunError && error.code === "retry_not_allowed",
  );
});

test("retryAgentRun returns null for an unknown run ref", () => {
  assert.equal(retryAgentRun("test-owner", "agent_run:nonexistent"), null);
});

test("decideConfirmation always returns null in the file-based architecture", () => {
  assert.equal(decideConfirmation("test-owner", "confirmation:any", "confirm"), null);
  assert.equal(decideConfirmation("test-owner", "confirmation:any", "reject"), null);
});

test("resumeWaitingRuns returns empty when no provider is configured", () => {
  assert.deepEqual(resumeWaitingRuns("test-owner"), []);
});

test("subscribeAgentRun returns null for an unknown run or a different owner", () => {
  const run = createAgentRun("owner-sse", "hello", { sessionId: 8 });
  assert.equal(subscribeAgentRun("test-owner", "agent_run:nonexistent", {}), null);
  assert.equal(subscribeAgentRun("other-owner", run.run_ref, {}), null);
});

test("subscribeAgentRun notifies done once the run reaches a terminal status", async () => {
  const run = createAgentRun("test-owner", "subscribe live", { sessionId: 9 });
  await waitForTask(run.run_ref);
  let done: AgentRunState | null = null;
  const unsubscribe = subscribeAgentRun("test-owner", run.run_ref, {
    onDone: (state) => {
      done = state;
    },
  });
  assert.ok(unsubscribe);
  await stopAgentRun("test-owner", run.run_ref);
  assert.equal(done?.status, "stopped");
});

test("subscribeAgentRun unsubscribes before terminal status stops future notifications", async () => {
  const run = createAgentRun("test-owner", "subscribe cancel", { sessionId: 10 });
  await waitForTask(run.run_ref);
  let onDoneCalls = 0;
  const unsubscribe = subscribeAgentRun("test-owner", run.run_ref, {
    onDone: () => {
      onDoneCalls += 1;
    },
  });
  assert.ok(unsubscribe);
  unsubscribe();
  await stopAgentRun("test-owner", run.run_ref);
  await Promise.resolve();
  assert.equal(onDoneCalls, 0);
});

test("subscribeAgentRun notifies done when the run is already terminal", async () => {
  const run = createAgentRun("test-owner", "subscribe terminal", { sessionId: 11 });
  await waitForTask(run.run_ref);
  await stopAgentRun("test-owner", run.run_ref);
  let done: AgentRunState | null = null;
  const unsubscribe = subscribeAgentRun("test-owner", run.run_ref, {
    onDone: (state) => {
      done = state;
    },
  });
  assert.ok(unsubscribe);
  await Promise.resolve();
  assert.equal(done?.status, "stopped");
});
