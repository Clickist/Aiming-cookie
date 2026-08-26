import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-deep-read-"));
process.env.DATA_ROOT = dataRoot;

const { createAgentRun, getAgentRun } = await import("../src/agent-runs.ts");
const { saveProfile } = await import("../src/provider-store.ts");
const {
  ensureSession,
  readConversationMeta,
} = await import("../src/session-repo.ts");
const { waitForTask } = await import("../src/task-manager.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "deep-read-run-key" },
});

test("a run's deep reads land in state and the session meta fallback list", async () => {
  const dir = join(dataRoot, "analyses", "12");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), "{}", "utf8");
  // Session must exist before meta union writes land predictably.
  const sessionId = 61;
  await ensureSession(sessionId);

  let providerCalls = 0;
  const streamFn: StreamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "read-run-1",
        name: "read",
        arguments: { path: "analyses/12/overview.json" },
      }], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "历史对比讲完了。" }], "stop");
  };

  const run = createAgentRun("deep-read-owner", "和之前几局对比一下，总结共性问题。", { sessionId, streamFn });
  await waitForTask(run.run_ref);

  const final = getAgentRun("deep-read-owner", run.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed, error: ${JSON.stringify(final.error)}`);
  assert.deepEqual(final.analysis_refs, []);
  assert.deepEqual(final.deep_read_analysis_refs, ["analysis:12"]);

  // 会话聚合：深读 id 并列落在 deep_read_analysis_session_ids，不进主题列表。
  const meta = readConversationMeta(sessionId);
  assert.equal(meta.analysis_session_ids, undefined);
  assert.deepEqual(meta.deep_read_analysis_session_ids, [12]);
});
