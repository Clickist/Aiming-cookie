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
