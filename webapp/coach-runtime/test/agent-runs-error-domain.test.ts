import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-domain-"));
process.env.DATA_ROOT = dataRoot;

const { createAgentRun, getAgentRun } = await import("../src/agent-runs.ts");
const { saveProfile } = await import("../src/provider-store.ts");
const { ensureSession } = await import("../src/session-repo.ts");
const { waitForTask } = await import("../src/task-manager.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "domain-mapping-run-key" },
});

// 回归锁：turn 响应的 error 没有 domain 字段（是 category/code）。此前读
// error.domain 恒为 undefined，流中断这类网络失败被折叠成 "model"。
test("a stream-interrupted turn failure maps to the network domain", async () => {
  const sessionId = 81;
  await ensureSession(sessionId);

  // stopReason "error" 让 turn 走 provider_stream_interrupted 失败路径
  // （半截正文 + retryable），对应真实 provider 流中途断开的第一种形态。
  const streamFn: StreamFn = async () =>
    streamAssistant([{ type: "text", text: "回复只说到一半" }], "error");

  const created = createAgentRun("domain-owner", "网络抖动的一问", { sessionId, streamFn });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("domain-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed", `run should fail, error: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.code, "provider_stream_interrupted");
  assert.equal(failed.error?.domain, "network", `stream interruption is network-class, got: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.retryable, true, "stream interruption keeps its retryable signal");
});
