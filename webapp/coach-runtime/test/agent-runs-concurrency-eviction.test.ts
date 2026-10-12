import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// 独立 data root：node --test 每文件独立进程，runs Map 与会话文件都只在本
// 文件内有效，不污染检出目录。
const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-gate-"));
process.env.DATA_ROOT = dataRoot;

import {
  AgentRunError,
  createAgentRun,
  getAgentRun,
  getAgentRunEventsSince,
  retryAgentRun,
  stopAgentRun,
  sweepTerminalRuns,
} from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "gate-test-key" },
});

test("createAgentRun refuses a second concurrent turn on the same session (P1-11)", async () => {
  // 占用 run：streamFn 挂起，保持 running 直到测试放行。
  let release: (() => void) | null = null;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  const first = createAgentRun("busy-owner", "第一问", {
    sessionId: 9001,
    streamFn: async () => {
      await blocked;
      return streamAssistant([{ type: "text", text: "好" }], "stop");
    },
  });
  try {
    assert.throws(
      () => createAgentRun("busy-owner", "第二问", { sessionId: 9001 }),
      (error: unknown) => error instanceof AgentRunError && error.code === "session_busy",
      "second create on the same session must trip session_busy",
    );
    // 不同 owner 的同一会话同样被闸（并发语义按会话算，不按 owner 算）。
    assert.throws(
      () => createAgentRun("other-owner", "多窗口", { sessionId: 9001 }),
      (error: unknown) => error instanceof AgentRunError && error.code === "session_busy",
    );
    // 新会话不受影响。
    const other = createAgentRun("busy-owner", "别的会话", { sessionId: 9006 });
    await stopAgentRun("busy-owner", other.run_ref);
  } finally {
    release!();
    await stopAgentRun("busy-owner", first.run_ref);
  }
});

test("retryAgentRun trips session_busy while another run is active on the session (P1-11)", async () => {
  // A：首拍 provider 错误 → failed + retryable；B：挂起进行中占同一会话；
  // retry(A) 必须以 session_busy 拒绝（重试也是往同一 JSONL 追加的新 turn）。
  const failedRun = createAgentRun("retry-gate-owner", "失败的一问", {
    sessionId: 9003,
    streamFn: async () => streamAssistant([], "error"),
  });
  await waitForTask(failedRun.run_ref);
  const failed = getAgentRun("retry-gate-owner", failedRun.run_ref);
  assert.ok(failed && failed.status === "failed", `run should fail: ${JSON.stringify(failed?.error)}`);
  assert.equal(failed.error?.retryable, true);

  let release: (() => void) | null = null;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  const activeRun = createAgentRun("retry-gate-owner", "占用会话的一问", {
    sessionId: 9003,
    streamFn: async () => {
      await blocked;
      return streamAssistant([{ type: "text", text: "好" }], "stop");
    },
  });
  try {
    assert.throws(
      () => retryAgentRun("retry-gate-owner", failedRun.run_ref),
      (error: unknown) => error instanceof AgentRunError && error.code === "session_busy",
      "retry must trip session_busy while the session is occupied",
    );
  } finally {
    release!();
    await stopAgentRun("retry-gate-owner", activeRun.run_ref);
  }
});

test("terminal runs are evicted after the TTL sweep (TS-1)", async () => {
  const run = createAgentRun("evict-owner", "淘汰我", {
    sessionId: 9004,
    streamFn: async () => streamAssistant([], "error"), // 快速终态，不碰真网络
  });
  await waitForTask(run.run_ref); // streamFn error → failed（终态）
  const failed = getAgentRun("evict-owner", run.run_ref);
  assert.ok(failed && failed.status === "failed");

  // TTL 之前不淘汰。
  assert.equal(sweepTerminalRuns(Date.now() + 1000), 0);
  assert.ok(getAgentRun("evict-owner", run.run_ref));
  // TTL 之后淘汰（前面用例的终态 run 会一并淘汰，断言 ≥1 而非精确值）；
  // GET 随之 404（前端轮询早已停拍）。
  assert.ok(sweepTerminalRuns(Date.now() + 31 * 60 * 1000) >= 1);
  assert.equal(getAgentRun("evict-owner", run.run_ref), null);
});

test("getAgentRunEventsSince returns only events past the cursor (TS-1)", async () => {
  const run = createAgentRun("cursor-owner", "增量拉取", { sessionId: 9005, streamFn: async () => streamAssistant([], "error") });
  await waitForTask(run.run_ref);
  const full = getAgentRunEventsSince("cursor-owner", run.run_ref, 0);
  assert.ok(full);
  assert.ok(full.events.length >= 1, `expected events, got ${JSON.stringify(full.events)}`);
  assert.equal(full.latest_sequence, full.events[full.events.length - 1]!.sequence);

  const incremental = getAgentRunEventsSince("cursor-owner", run.run_ref, full.latest_sequence);
  assert.ok(incremental);
  assert.equal(incremental.events.length, 0);
  assert.equal(incremental.latest_sequence, full.latest_sequence);

  // 越权/未知 run → null（404 语义不变）。
  assert.equal(getAgentRunEventsSince("wrong-owner", run.run_ref, 0), null);
  assert.equal(getAgentRunEventsSince("cursor-owner", "agent_run:none", 0), null);
});
