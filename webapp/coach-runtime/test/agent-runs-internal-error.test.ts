import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-internal-"));
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
  credential: { type: "api_key", key: "internal-error-run-key" },
});

test("a non-AgentRunError crash is classified as an internal error, not a model failure", async () => {
  const sessionId = 71;
  const analysisDir = join(dataRoot, "analyses", "13");
  mkdirSync(analysisDir, { recursive: true });
  writeFileSync(join(analysisDir, "overview.json"), "{}", "utf8");
  // The run reads this analysis so the response carries a deep-read ref and
  // agent-runs persists it through updateConversationDeepReadAnalysisIds.
  await ensureSession(sessionId);

  // Simulate an internal persistence bug: block the conversation meta path
  // with a directory so writeConversationMeta throws (EISDIR) inside the
  // run's turn — exactly the kind of sidecar-internal crash the catch-all
  // must classify as internal, not as a model failure.
  mkdirSync(join(dataRoot, "conversations", `${sessionId}.meta.json`), { recursive: true });

  let providerCalls = 0;
  const streamFn: StreamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "read-run-1",
        name: "read",
        arguments: { path: "analyses/13/overview.json" },
      }], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "历史对比讲完了。" }], "stop");
  };

  const created = createAgentRun("internal-owner", "和之前几局对比一下，总结共性问题。", { sessionId, streamFn });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("internal-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed", `run should fail, error: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.domain, "coach_runtime", `error should be internal, got: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.code, "internal_error", `error should be internal_error, got: ${JSON.stringify(failed.error)}`);
  assert.equal(failed.error?.retryable, false, `internal errors must not be retryable, got: ${JSON.stringify(failed.error)}`);

  // The original error is appended to coach-error.log for diagnosis.
  assert.ok(existsSync(join(dataRoot, "coach-error.log")), "coach-error.log must exist after an internal error");
  const log = readFileSync(join(dataRoot, "coach-error.log"), "utf8");
  assert.match(log, /\[agent-run\]/);
});
