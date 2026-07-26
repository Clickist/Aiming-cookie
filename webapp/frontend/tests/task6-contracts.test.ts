import assert from "node:assert/strict";
import { test } from "node:test";

import {
  coachLayoutMode,
  presentCoachContext,
  presentStorageCategories,
} from "../lib/contracts";

test("Coach layout breakpoints and width clamp are frozen", () => {
  assert.deepEqual(coachLayoutMode(1280, 360), { mode: "side-by-side", width: 360 });
  assert.deepEqual(coachLayoutMode(960, 480), { mode: "overlay", width: 480 });
  assert.deepEqual(coachLayoutMode(820, 360), { mode: "full", width: 360 });
  assert.deepEqual(coachLayoutMode(1160, 700), { mode: "side-by-side", width: 480 });
});

test("Coach context presentation is allow-listed and deleted refs stay unavailable", () => {
  const context = presentCoachContext({
    schema_version: "coach_context_ref.v1",
    context_ref: "context:1",
    kind: "evidence_segment",
    target_ref: "segment:1",
    label: "片段 01:22-01:25",
    status: "deleted",
    locator: { view: "video", relative_start_ms: 82000 },
  });
  assert.deepEqual(context, {
    contextRef: "context:1",
    kind: "evidence_segment",
    label: "片段 01:22-01:25",
    status: "deleted",
    locator: { view: "video", relative_start_ms: 82000 },
  });
  assert.doesNotMatch(JSON.stringify(context), /path|raw|video_path|token|secret/);
});

test("Storage maps exactly four categories without inventing cleanup", () => {
  assert.deepEqual(presentStorageCategories({
    analysis_artifacts_bytes: 1,
    run_video_bytes: 2,
    run_raw_bytes: 3,
    incomplete_recovery_bytes: 4,
  }), [
    ["分析产物", 1],
    ["Run 录像", 2],
    ["Raw trace", 3],
    ["未完成采集", 4],
  ]);
});
