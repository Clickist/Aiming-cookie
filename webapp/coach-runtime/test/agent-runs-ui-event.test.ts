import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-ui-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun } from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "ui-event-test-key" },
});

test("navigation.open video_time ui_event reaches the run event stream", async () => {
  let providerCalls = 0;
  const streamFn: StreamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "nav-call",
        name: "run_product_command",
        arguments: {
          command_name: "navigation.open",
          parameters: { target: "video_time", analysis_ref: "analysis:1", time_ms: 3400 },
        },
      }], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "已打开视频讲解。" }], "stop");
  };

  const run = createAgentRun("ui-event-owner", "请打开视频讲解", { sessionId: 51, streamFn });
  await waitForTask(run.run_ref);

  const final = getAgentRun("ui-event-owner", run.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed, error: ${JSON.stringify(final.error)}`);

  const uiEvents = final.events
    .map((event) => event.payload?.ui_event)
    .filter((value): value is Record<string, unknown> =>
      typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "video_time");
  assert.equal(uiEvents.length, 1, "exactly one video_time ui_event should reach run.events");
  const uiEvent = uiEvents[0];
  assert.equal(uiEvent.schema_version, "coach_ui_event.v1");
  assert.equal(uiEvent.analysis_ref, "analysis:1");
  assert.equal(uiEvent.time_ms, 3400);

  // The ui_event rides on the tool-completed event for the same tool call.
  const carrier = final.events.find((event) => event.payload?.ui_event === uiEvent);
  assert.ok(carrier);
  assert.equal(carrier.type, "tool");
  assert.equal(carrier.payload?.tool_call_id, "nav-call");
  assert.equal(carrier.payload?.command_name, "navigation.open");
});
