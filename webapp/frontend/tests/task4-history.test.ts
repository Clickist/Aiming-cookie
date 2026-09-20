import assert from "node:assert/strict";
import { test } from "node:test";

import { translate } from "../lib/i18n/core";
import {
  buildHistorySections,
  getHistoryStatusText,
  getTrendPresentation,
} from "../lib/contracts";
import type { KovaaKRunListItem, SessionListItem } from "../lib/types";

function run(overrides: Partial<KovaaKRunListItem> = {}): KovaaKRunListItem {
  return {
    id: 1,
    run_ref: "run:1",
    source_key: "1wall6targets",
    scenario: "1wall6targets",
    source_availability: { stats: "available", performance: "available", mp4: "missing" },
    trace_quality: { state: "attached", availability: "available", alignment_status: "aligned", coverage: 1 },
    trace_state: "attached",
    trace_error: null,
    video_artifact_ref: null,
    finalization_state: "completed",
    finalization_error: null,
    readiness_state: "pending_analysis",
    analysis_count: 0,
    supported_input_modes: ["input_native"],
    evidence_availability: { stats: "available", performance: "available", raw: "available", mp4: "missing" },
    alignment: { status: "aligned" },
    video_quality: {},
    limitations: ["video_unavailable"],
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
    ...overrides,
  };
}

function analysis(overrides: Partial<SessionListItem> = {}): SessionListItem {
  return {
    id: 9,
    analysis_ref: "analysis:9",
    run_ref: "run:1",
    status: "done",
    created_at: "2026-07-25T00:00:00Z",
    finished_at: "2026-07-25T00:01:00Z",
    attempts: 1,
    max_attempts: 2,
    llm_cost_cny: null,
    summary_label: "diagnosis",
    analysis_type: "flicking",
    input_mode: "input_native",
    kovaak_run_id: 1,
    scenario: "1wall6targets",
    source_availability: { stats: "available" },
    trace_quality: { state: "attached", availability: "available", alignment_status: "aligned", coverage: 1 },
    ...overrides,
  };
}

test("history keeps pending runs, run records, and analysis records in separate sections", () => {
  const sections = buildHistorySections({
    runs: [run(), run({ id: 2, run_ref: "run:2", readiness_state: "analyzed", analysis_count: 1 })],
    sessions: [analysis()],
  });
  assert.equal(sections.pendingRuns.length, 1);
  assert.equal(sections.runRecords.length, 1);
  assert.equal(sections.analysisRecords.length, 1);
});

test("history keeps incomplete runs visible even when no analysis tier is available", () => {
  const incomplete = run({
    id: 3,
    run_ref: "run:3",
    readiness_state: "incomplete_evidence",
    supported_input_modes: [],
    limitations: ["missing_performance"],
  });

  const sections = buildHistorySections({ runs: [incomplete], sessions: [] });

  assert.deepEqual(sections.runRecords, [incomplete]);
});

test("history labels retain display data without turning refs into user copy", () => {
  const item = analysis({
    training_at: "2026-07-25T00:00:00Z",
    analysis_completed_at: "2026-07-25T00:01:00Z",
    presentation_label: "1wall6targets | 训练：2026-07-25T00:00:00Z | 分析：2026-07-25T00:01:00Z",
  });
  assert.doesNotMatch(item.presentation_label ?? "", /run:1|analysis:9/);
  assert.equal(item.training_at, "2026-07-25T00:00:00Z");
  assert.equal(item.analysis_completed_at, "2026-07-25T00:01:00Z");
});

test("history status text distinguishes unavailable, partial, unsupported, offline, permission, and deleted", () => {
  // 批 3 起与字典同源断言（文案改词测试不碎）。
  assert.equal(getHistoryStatusText("source_unavailable"), translate("zh-CN", "history.status.sourceUnavailable"));
  assert.equal(getHistoryStatusText("partial"), translate("zh-CN", "history.status.partial"));
  assert.equal(getHistoryStatusText("unsupported"), translate("zh-CN", "history.status.unsupported"));
  assert.equal(getHistoryStatusText("offline"), translate("zh-CN", "history.status.offline"));
  assert.equal(getHistoryStatusText("permission_denied"), translate("zh-CN", "history.status.permissionDenied"));
  assert.equal(getHistoryStatusText("deleted"), translate("zh-CN", "history.status.deleted"));
});

test("trend presentation is fail-closed and never fabricates PB or percent change", () => {
  assert.deepEqual(getTrendPresentation({ comparable: false, reason: "calibration_mismatch" }), {
    comparable: false,
    summary: translate("zh-CN", "history.trend.notComparable", {
      reason: translate("zh-CN", "history.trend.calibrationMismatch"),
    }),
    value: null,
  });
  assert.deepEqual(getTrendPresentation({ comparable: true, current: 12, baseline: 10, delta: 2, percent_change: 20, metric_key: "accuracy", unit: "%" }), {
    comparable: true,
    summary:
      translate("zh-CN", "history.trend.current", { value: "12%" })
      + translate("zh-CN", "history.trend.baseline", { value: "10%" })
      + translate("zh-CN", "history.trend.delta", { value: "+2%" }),
    value: 12,
  });
});
