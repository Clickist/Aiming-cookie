import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-retry-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun, retryAgentRun } from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import { readSessionMessages } from "../src/session-repo.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "retry-test-key" },
});

const CONTENT = "帮我把刚才失败的分析重跑一遍";

test("retrying a failed run does not duplicate the user message in the persistent session", async () => {
  // First attempt: Provider error reply -> run fails with a retryable error.
  // Retries: normal text reply.
  let providerCalls = 0;
  const streamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([], "error");
    }
    return streamAssistant([{ type: "text", text: "已重新排队分析。" }], "stop");
  };

  const created = createAgentRun("retry-owner", CONTENT, { sessionId: 61, streamFn });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("retry-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed");
  assert.equal(failed.error?.retryable, true, `failure should be retryable: ${JSON.stringify(failed.error)}`);

  const retried = retryAgentRun("retry-owner", created.run_ref);
  assert.ok(retried);
  await waitForTask(retried.run_ref);

  const final = getAgentRun("retry-owner", retried.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `retry should succeed, error: ${JSON.stringify(final.error)}`);

  // The persistent session must hold exactly one user message with the
  // retried content — not one per attempt.
  const messages = await readSessionMessages(61);
  const duplicates = messages.filter((message) => message.role === "user" && message.content === CONTENT);
  assert.equal(duplicates.length, 1, `expected 1 user message, got ${duplicates.length}: ${JSON.stringify(messages.map((m) => m.role))}`);
});

test("retry inherits contextRefs so the retried turn keeps its pinned analysis", async () => {
  // contextRefs 是 RunRecord 级字段（前端引用菜单），createAgentRun 只对首次
  // 回合生效；retryAgentRun 构造 newRecord 时曾漏抄 → 重试回合
  // context_refs=undefined，主题钉选丢失。analysis_refs 是 context_refs 在本
  // run 状态里的可观测投影（turn.ts 把 context_refs 记为主题级参与）。
  let providerCalls = 0;
  const streamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) return streamAssistant([], "error");
    return streamAssistant([{ type: "text", text: "已按原主题重跑。" }], "stop");
  };

  const created = createAgentRun("retry-owner", "重跑刚才的主题分析", {
    sessionId: 63,
    streamFn,
    contextRefs: ["analysis:7"],
  });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("retry-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed");
  assert.equal(failed.error?.retryable, true);

  const retried = retryAgentRun("retry-owner", created.run_ref);
  assert.ok(retried);
  await waitForTask(retried.run_ref);

  const final = getAgentRun("retry-owner", retried.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `retry should succeed, error: ${JSON.stringify(final.error)}`);
  assert.ok(
    final.analysis_refs.includes("analysis:7"),
    `retried turn must keep the pinned analysis: ${JSON.stringify(final.analysis_refs)}`,
  );
});

test("a stream that resolves with stopReason=error and partial text fails retryably", async () => {
  // 2026-08-21 实测形态：provider 中途断流时 harness resolve 一条
  // stopReason=error 的部分消息而非 throw。半截话不能当完整答案交付：
  // run 必须以可重试失败结束，且部分正文作为 partial 保留。
  const created = createAgentRun("retry-owner", "讲讲这局的问题", {
    sessionId: 62,
    streamFn: async () =>
      streamAssistant([{ type: "text", text: "这局先看命中率，第 6 杀只有 41%，这属于" }], "error"),
  });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("retry-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed", `run should fail, error: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.retryable, true);
  assert.ok(
    (failed.partial_text ?? "").includes("这属于"),
    `partial text should be preserved: ${JSON.stringify(failed.partial_text)}`,
  );
});
