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

test("current training fixture contract stays read-only, bounded, and status-explicit", async () => {
  const fixtures = await import("../fixtures/task7-fixtures");
  const training = fixtures.CURRENT_TRAINING_ACTIVE;
  assert.equal(training.schema_version, "current_training.v1");
  assert.equal(training.plan_status, "active");
  assert.equal(training.visible_item_count, 1);
  assert.equal(training.items.length, 1);
  assert.deepEqual(training.items.map((item) => item.status), ["planned"]);
  assert.equal(training.items[0]?.scenario_profile_ref, "scenario:static.1wall_6targets_small@1");
  assert.equal(training.items[0]?.practice_condition, "保持完全相同的静态场景条件，只测试一个终点控制提示。");
  assert.equal(training.items[0]?.cue, "只使用一个动作效果提示：先受控地到达目标，再让点击跟随已经稳定的瞄点。");
  assert.equal(training.items[0]?.observation, null);
  assert.doesNotMatch(JSON.stringify(training), /diagnosis_ref|metric_ref|execution_ref/);
});
