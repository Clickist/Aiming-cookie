import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import { openOrCreateRuntimeSession, recoverInterruptedRun } from "../src/runtime-session.ts";
import { runSpike } from "../src/run-spike.ts";

async function createEnv(cwd: string) {
  const sourceRoot = process.env.PI_SOURCE_DIR;
  assert.ok(sourceRoot, "PI_SOURCE_DIR is required");
  const module = await import(pathToFileURL(join(sourceRoot, "packages", "agent", "src", "node.ts")).href) as {
    NodeExecutionEnv: new (options: { cwd: string }) => unknown;
  };
  return new module.NodeExecutionEnv({ cwd });
}

async function withTempDirectory(run: (directory: string) => Promise<void>) {
  const directory = await mkdtemp(join(tmpdir(), "aiming-cookie-pi-recovery-"));
  try {
    await run(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function withFakeProxy(run: (endpoint: string) => Promise<void>) {
  let requests = 0;
  const server = createServer((_request, response) => {
    requests += 1;
    response.writeHead(200, { "content-type": "application/x-ndjson" });
    if (requests === 1) {
      response.end('{"type":"start"}\n{"type":"tool_call","id":"session-tool-1","name":"get_analysis_summary","arguments":{"analysis_id":"analysis-fixture-1"}}\n{"type":"done","stop_reason":"toolUse","usage":{"input":10,"output":4,"total_tokens":14}}\n');
      return;
    }
    response.end('{"type":"start"}\n{"type":"text_delta","delta":"session coach answer"}\n{"type":"done","stop_reason":"stop","usage":{"input":10,"output":4,"total_tokens":14}}\n');
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  try {
    await run(`http://127.0.0.1:${address.port}/fixture`);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
}

test("recovery marks a stale running marker interrupted without replaying the tool", async () => {
  await withTempDirectory(async (directory) => {
    const filePath = join(directory, "runtime.jsonl");
    const initial = await openOrCreateRuntimeSession({ env: await createEnv(directory), filePath, sessionId: "session-recovery-1" });
    await initial.session.appendCustomEntry("aiming_cookie_run.v0", { run_id: "run-stale", status: "running" });
    let toolExecutions = 0;
    const toolExecutionSpy = () => {
      toolExecutions += 1;
    };

    const reopened = await openOrCreateRuntimeSession({ env: await createEnv(directory), filePath, sessionId: "session-recovery-1" });
    const recovery = await recoverInterruptedRun(reopened);
    assert.deepEqual(recovery, { previousRunId: "run-stale" });
    toolExecutionSpy;
    assert.equal(toolExecutions, 0);
    const markers = (await reopened.session.getEntries()).filter((entry: { type?: string; customType?: string }) => entry.type === "custom" && entry.customType === "aiming_cookie_run.v0");
    assert.deepEqual(markers.map((entry: { data: unknown }) => entry.data), [
      { run_id: "run-stale", status: "running" },
      { run_id: "run-stale", status: "interrupted" },
    ]);
  });
});

test("a completed marker does not emit run.interrupted on reopen", async () => {
  await withTempDirectory(async (directory) => {
    const filePath = join(directory, "runtime.jsonl");
    const initial = await openOrCreateRuntimeSession({ env: await createEnv(directory), filePath, sessionId: "session-completed-1" });
    await initial.session.appendCustomEntry("aiming_cookie_run.v0", { run_id: "run-completed", status: "completed" });
    await withFakeProxy(async (endpoint) => {
      const result = await runSpike({
        endpoint,
        filePath,
        sessionId: "session-completed-1",
        runId: "run-next",
        prompt: "Use the proxy fixture.",
      });
      assert.ok(!result.events.some((event) => event.type === "run.interrupted"));
    });
  });
});

test("end-to-end Spike emits approved events and recovers the completed transcript", async () => {
  await withTempDirectory(async (directory) => {
    const filePath = join(directory, "runtime.jsonl");
    await withFakeProxy(async (endpoint) => {
      const result = await runSpike({
        endpoint,
        filePath,
        sessionId: "session-e2e-1",
        runId: "run-e2e-1",
        prompt: "Use the proxy fixture.",
      });
      assert.deepEqual(result.events.map((event) => event.type), [
        "run.started",
        "assistant.completed",
        "tool.started",
        "tool.progress",
        "tool.completed",
        "assistant.delta",
        "assistant.completed",
        "run.completed",
      ]);
      const reopened = await openOrCreateRuntimeSession({ env: await createEnv(directory), filePath, sessionId: "session-e2e-1" });
      assert.equal(await recoverInterruptedRun(reopened), null);
      const context = await reopened.session.buildContext();
      assert.deepEqual(context.messages.map((message: { role: string }) => message.role), ["user", "assistant", "toolResult", "assistant"]);
    });
  });
});
