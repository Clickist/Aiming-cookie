/**
 * Regression: a failure that happens after the turn returned (outer catch in
 * agent-runs, e.g. conversation meta persistence) must relabel the last
 * streaming presentation snapshot as failed instead of leaving it "streaming".
 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-presentation-run-failure-"));
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
  credential: { type: "api_key", key: "presentation-run-failure-key" },
});

const REPLY = [
  "先看这段分布：",
  "",
  "```openui-lang",
  "root = Card([m1])",
  "m1 = MetricIndicatorInline(\"23%\")",
  "```",
  "",
  "下一步只练停半拍。",
].join("\n");

test("outer-catch failure projects the streaming snapshot with generation_status failed", async () => {
  const sessionId = 9601;
  const analysisDir = join(dataRoot, "analyses", "13");
  mkdirSync(analysisDir, { recursive: true });
  writeFileSync(join(analysisDir, "overview.json"), "{}", "utf8");
  await ensureSession(sessionId);

  // Make the post-turn persistence write throw (EISDIR): this is the
  // agent-runs outer catch, not the turn's own failure path.
  mkdirSync(join(dataRoot, "conversations", `${sessionId}.meta.json`), { recursive: true });

  let providerCalls = 0;
  const streamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "read-presentation-1",
        name: "read",
        arguments: { path: "analyses/13/overview.json" },
      }], "toolUse");
    }
    return streamAssistant([{ type: "text", text: REPLY }], "stop");
  };

  const created = createAgentRun("presentation-failure-owner", "和之前几局对比一下。", { sessionId, streamFn });
  await waitForTask(created.run_ref);

  const failed = getAgentRun("presentation-failure-owner", created.run_ref);
  assert.ok(failed);
  assert.equal(failed.status, "failed", `run should fail, error: ${JSON.stringify(failed.error)}`);
  assert.ok(failed.partial_presentation, "failed run must keep the last streaming snapshot");
  assert.equal(
    failed.partial_presentation.generation_status,
    "failed",
    "outer-catch failure must not leave the snapshot marked streaming",
  );
  assert.ok(
    failed.partial_presentation.segments.some((segment) => segment.kind === "openui"),
    "the unclosed/streamed openui segment must survive the failed projection",
  );
  assert.equal(failed.partial_presentation.message_id, created.run_ref);
});
