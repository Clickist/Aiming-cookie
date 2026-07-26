import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildHistorySections,
  getHistoryStatusText,
  getTrendPresentation,
  presentRunInspector,
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

test("history status text distinguishes unavailable, partial, unsupported, offline, permission, and deleted", () => {
  assert.equal(getHistoryStatusText("source_unavailable"), "来源不可用");
  assert.equal(getHistoryStatusText("partial"), "部分结果");
  assert.equal(getHistoryStatusText("unsupported"), "不支持");
  assert.equal(getHistoryStatusText("offline"), "离线");
  assert.equal(getHistoryStatusText("permission_denied"), "权限被拒绝");
  assert.equal(getHistoryStatusText("deleted"), "引用已删除");
});

test("trend presentation is fail-closed and never fabricates PB or percent change", () => {
  assert.deepEqual(getTrendPresentation({ comparable: false, reason: "calibration_mismatch" }), {
    comparable: false,
    summary: "暂不可比较：校准不一致",
    value: null,
  });
  assert.deepEqual(getTrendPresentation({ comparable: true, current: 12, baseline: 10, delta: 2, percent_change: 20, metric_key: "accuracy", unit: "%" }), {
    comparable: true,
    summary: "当前 12% · 基线 10% · 差异 +2%",
    value: 12,
  });
});

test("run inspector projects five levels without paths or internal identifiers", () => {
  const inspector = presentRunInspector(run({
    trace_error: "C:\\Users\\private\\trace.bin",
    stats_source_ref: "C:\\secret\\stats.csv",
  } as Partial<KovaaKRunListItem>));
  assert.equal(inspector.identity.scenario, "1wall6targets");
  assert.equal(inspector.evidence.raw.availability, "可用");
  assert.equal(inspector.capabilities.modes[0]?.code, "input_native");
  assert.equal(inspector.operations.includes("manage_storage"), true);
  assert.doesNotMatch(JSON.stringify(inspector), /C:\\|Users|trace\.bin|stats\.csv|run:1/);
});
