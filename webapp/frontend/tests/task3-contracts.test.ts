import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildRunAnalysisRequest,
  getProductStartRoute,
  getRunModeAvailability,
  isRunPauseFailClosed,
  presentRecordLabel,
  presentTask,
} from "../lib/contracts";
import type { KovaaKRunListItem, ProductStateV1, TaskDetailV1 } from "../lib/types";

function productState(overrides: Partial<ProductStateV1>): ProductStateV1 {
  return {
    schema_version: "product_state.v1",
    availability: "available",
    onboarding_completed: false,
    onboarding_completion_kind: null,
    has_pending_runs: false,
    has_runs: false,
    has_analyses: false,
    error: null,
    ...overrides,
  };
}

test("conditional start distinguishes unavailable, onboarding, and Coach", () => {
  assert.equal(getProductStartRoute(productState({ availability: "unavailable" })), null);
  assert.equal(getProductStartRoute(productState({ onboarding_completed: false })), "/onboarding");
  assert.equal(getProductStartRoute(productState({ onboarding_completed: true })), "/");
  assert.equal(
    getProductStartRoute(productState({ onboarding_completed: true, has_runs: true })),
    "/",
  );
  assert.equal(
    getProductStartRoute(productState({ onboarding_completed: true, has_analyses: true })),
    "/",
  );
});

test("task presentation maps machine codes to Chinese and ignores backend English labels", () => {
  const task = {
    schema_version: "task_detail.v1",
    availability: "available",
    task_ref: "task:1",
    analysis_ref: "analysis:1",
    state: "running",
    state_label: "Running from DTO",
    phase: "computing_kinematics",
    phase_label: "Computing movement metrics from DTO",
    input_mode: "input_native",
    analysis_type: "flicking",
    run_ref: "run:1",
    presentation_label: "1wall 6targets | 训练：2026-07-25T00:00:00Z | 分析：分析尚未完成",
    training_at: "2026-07-25T00:00:00Z",
    analysis_completed_at: null,
    failure: null,
    partial_outcome: null,
    retryable: false,
    can_delete: false,
    created_at: "2026-07-25T00:00:00Z",
    started_at: null,
    finished_at: null,
    attempt_number: 1,
    attempt_history: [],
    error: null,
  } satisfies TaskDetailV1;

  assert.deepEqual(presentTask(task), {
    state: "分析中",
    phase: "计算运动学指标",
    failureDomain: null,
    presentationLabel: "1wall 6targets | 训练：2026-07-25T00:00:00Z | 分析：分析尚未完成",
  });
});

test("record labels include scenario and available timestamps without exposing transport refs", () => {
  const label = presentRecordLabel({
    scenario: "1wall 5targets pasu",
    trainingAt: "2026-08-09T08:10:09Z",
    analysisCompletedAt: "2026-08-09T08:12:30Z",
  });
  assert.equal(label, "1wall 5targets pasu | 训练：2026-08-09T08:10:09Z | 分析：2026-08-09T08:12:30Z");
  assert.doesNotMatch(label, /run:\d+|analysis:\d+/);
  assert.equal(
    presentRecordLabel({ scenario: "C:\\Users\\private\\stats.csv", trainingAt: null, analysisCompletedAt: null }),
    "未命名场景 | 训练：训练时间未知 | 分析：分析尚未完成",
  );
});

test("run mode availability consumes supported_input_modes without re-deriving evidence", () => {
  const run = {
    id: 7,
    run_ref: "run:7",
    source_key: null,
    scenario: "1wall6targets",
    source_availability: {},
    trace_quality: {
      state: "attached",
      availability: "available",
      alignment_status: "aligned",
      coverage: 1,
    },
    trace_state: "attached",
    trace_error: null,
    video_artifact_ref: null,
    finalization_state: "completed",
    readiness_state: "pending_analysis",
    analysis_count: 0,
    supported_input_modes: ["input_native"],
    evidence_availability: {},
    alignment: {},
    video_quality: {},
    limitations: ["video_unavailable"],
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
  } satisfies KovaaKRunListItem;

  assert.equal(getRunModeAvailability(run, "input_native").available, true);
  assert.equal(getRunModeAvailability(run, "multimodal").available, false);
  assert.equal(getRunModeAvailability(run, "video_fallback").available, false);
});

test("pause warning follows the selected Run instead of global capture history", () => {
  const alignedRun = {
    alignment: { state: "resolved" },
  } satisfies Pick<KovaaKRunListItem, "alignment">;
  const pausedRun = {
    alignment: { state: "unavailable", error_code: "pause_unsupported" },
  } satisfies Pick<KovaaKRunListItem, "alignment">;

  assert.equal(isRunPauseFailClosed(alignedRun), false);
  assert.equal(isRunPauseFailClosed(pausedRun), true);
});

test("analysis request keeps profile defaults and per-run overrides separate", () => {
  assert.deepEqual(
    buildRunAnalysisRequest({
      profileDefault: { cm_per_360: 42, fov: 103 },
      manualOverride: { cm_per_360: 38 },
    }),
    {
      profile_default: { cm_per_360: 42, fov: 103 },
      manual_override: { cm_per_360: 38 },
    },
  );
});
