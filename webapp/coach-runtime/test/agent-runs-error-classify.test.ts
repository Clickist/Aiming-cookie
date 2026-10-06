import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-classify-"));
process.env.DATA_ROOT = dataRoot;

const { createAgentRun, getAgentRun } = await import("../src/agent-runs.ts");
const { saveProfile } = await import("../src/provider-store.ts");
const { ensureSession } = await import("../src/session-repo.ts");
const { waitForTask } = await import("../src/task-manager.ts");

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "error-classify-run-key" },
});

// 错误文案分层合同（点点 2026-10-06 拍板矩阵）：provider 原始错误文本经
// turn.ts classifyCoachFailureCode 提取稳定 code，run.error 带码透传前端
// （api.error.* 字典），不再把网络/额度/鉴权全部折叠成 turn_failed。

async function runToFailure(ownerId: string, sessionId: number, streamFn: StreamFn) {
  await ensureSession(sessionId);
  const created = createAgentRun(ownerId, "触发失败的一问", { sessionId, streamFn });
  await waitForTask(created.run_ref);
  const failed = getAgentRun(ownerId, created.run_ref);
  assert.ok(failed, "run record should exist");
  assert.equal(failed.status, "failed", `run should fail, error: ${JSON.stringify(failed.error)}`);
  return failed;
}

test("a provider 403 quota body maps to quota_exhausted, non-retryable", async () => {
  const failed = await runToFailure("classify-quota-owner", 91, (async () => {
    throw new Error(
      'HTTP 403 {"error":{"message":"预扣费额度失败, 用户剩余额度: ¥0.01, 需要预扣费额度: ¥0.02","type":"new_api_error","code":"insufficient_user_quota"}}',
    );
  }) as StreamFn);
  assert.equal(failed.error?.code, "quota_exhausted");
  assert.equal(failed.error?.retryable, false, "quota exhaustion must not invite a retry");
  assert.equal(failed.error?.domain, "model", "quota failures are not network-class");
});

test("a provider 401 auth body maps to provider_auth_invalid, non-retryable", async () => {
  const failed = await runToFailure("classify-auth-owner", 92, (async () => {
    throw new Error(
      'HTTP 401 {"error":{"message":"Authentication Fails, Your api key: ****.com is invalid","type":"authentication_error","code":"invalid_request_error"}}',
    );
  }) as StreamFn);
  assert.equal(failed.error?.code, "provider_auth_invalid");
  assert.equal(failed.error?.retryable, false, "invalid key must not invite a retry");
});

test("a transport reset maps to network_transient, retryable, network domain", async () => {
  const failed = await runToFailure("classify-network-owner", 93, (async () => {
    throw new Error("fetch failed: ECONNRESET");
  }) as StreamFn);
  assert.equal(failed.error?.code, "network_transient");
  assert.equal(failed.error?.retryable, true, "transport resets stay retryable");
  assert.equal(failed.error?.domain, "network", "transport resets are network-class");
});

test("a 429 throttle maps to service_overloaded, retryable", async () => {
  const failed = await runToFailure("classify-overload-owner", 94, (async () => {
    throw new Error("HTTP 429 Too Many Requests: rpm exhausted");
  }) as StreamFn);
  assert.equal(failed.error?.code, "service_overloaded");
  assert.equal(failed.error?.retryable, true, "throttles stay retryable");
});
