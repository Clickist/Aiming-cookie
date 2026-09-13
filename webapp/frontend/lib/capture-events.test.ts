import assert from "node:assert/strict";
import { test } from "node:test";

import {
  describeCaptureRunEvent,
  finalizationPendingText,
  traceErrorCodeLabel,
  TRACE_ERROR_LABELS,
  videoErrorCodeLabel,
  VIDEO_ERROR_LABELS,
} from "./capture-events";

test("every backend video_error code maps to a human phrase", () => {
  // 与后端写入点逐一对齐（kovaak_run_store / kovaak_capture_finalizer）。
  const backendVideoCodes = [
    "video_capture_unavailable",
    "video_window_invalid",
    "video_capture_session_mismatch",
    "video_coverage_gap",
    "video_pause_unsupported",
    "video_time_alignment_unavailable",
    "video_hardware_invalid",
    "video_capture_protocol_invalid",
    "video_pending_state_invalid",
    "video_managed_path_invalid",
    "video_receipt_invalid",
    "video_waiting_artifact",
    "removed_by_user",
  ];
  for (const code of backendVideoCodes) {
    const label = videoErrorCodeLabel(code);
    assert.notEqual(label, code, `video_error ${code} must map to a human phrase`);
    assert.ok(label.length > 0);
  }
  // 映射表不藏冗余：表键与钉住清单一致。
  assert.deepEqual(
    Object.keys(VIDEO_ERROR_LABELS).sort(),
    [...backendVideoCodes].sort(),
  );
});

test("every backend trace_error code maps to a human phrase", () => {
  const backendTraceCodes = [
    "trace_raw_window_coverage_gap",
    "trace_raw_queue_dropped",
    "trace_raw_ring_expired",
    "trace_snapshot_stale",
    "trace_capture_unavailable",
    "trace_snapshot_failed",
    "trace_quality_insufficient",
    "trace_attach_failed",
    "trace_legacy_quality_unknown",
    "trace_waiting_snapshot",
    "removed_by_user",
  ];
  for (const code of backendTraceCodes) {
    const label = traceErrorCodeLabel(code);
    assert.notEqual(label, code, `trace_error ${code} must map to a human phrase`);
  }
  assert.deepEqual(
    Object.keys(TRACE_ERROR_LABELS).sort(),
    [...backendTraceCodes].sort(),
  );
});

test("unknown error codes pass through verbatim instead of a invented phrase", () => {
  assert.equal(videoErrorCodeLabel("video_brand_new_code"), "video_brand_new_code");
  assert.equal(traceErrorCodeLabel("trace_brand_new_code"), "trace_brand_new_code");
});

test("healthy run reads as recorded with trace attached", () => {
  const described = describeCaptureRunEvent({
    scenario: "1w2ts reload",
    video_attached: true,
    raw_attached: true,
    video_error: null,
    trace_error: null,
    finalization_state: "finalized",
  });
  assert.equal(described.scenario, "1w2ts reload");
  assert.equal(described.videoLabel, "已录制");
  assert.equal(described.traceLabel, "已记录");
  assert.equal(described.healthy, true);
});

test("missing video and trace render the agreed human sentences", () => {
  const described = describeCaptureRunEvent({
    scenario: "1w2ts reload",
    video_attached: false,
    raw_attached: false,
    video_error: "video_capture_unavailable",
    trace_error: "trace_raw_window_coverage_gap",
    finalization_state: "finalized",
  });
  assert.equal(described.videoLabel, "未录制（打这局时应用未在录制）");
  assert.equal(described.traceLabel, "缺失（这一时间窗没有输入数据）");
  assert.equal(described.healthy, false);
});

test("window invalid maps to the not-recording attribution and unknown code passes through", () => {
  const described = describeCaptureRunEvent({
    video_attached: false,
    raw_attached: false,
    video_error: "video_window_invalid",
    trace_error: "trace_totally_unknown",
    finalization_state: "finalized",
  });
  assert.equal(described.videoLabel, "未录制（打这局时应用未在录制）");
  assert.equal(described.traceLabel, "缺失（trace_totally_unknown）");
});

test("waiting codes and in-flight finalization read as organizing, not failed", () => {
  const waiting = describeCaptureRunEvent({
    video_attached: false,
    raw_attached: false,
    video_error: "video_waiting_artifact",
    trace_error: "trace_waiting_snapshot",
    finalization_state: "pending",
  });
  assert.equal(waiting.videoLabel, "整理中");
  assert.equal(waiting.traceLabel, "整理中");

  const inFlight = describeCaptureRunEvent({
    video_attached: false,
    raw_attached: false,
    video_error: null,
    trace_error: null,
    finalization_state: "finalizing",
  });
  assert.equal(inFlight.videoLabel, "整理中");
  assert.equal(inFlight.traceLabel, "整理中");
});

test("blank scenario falls back to 未知场景", () => {
  const described = describeCaptureRunEvent({ video_attached: true, raw_attached: true });
  assert.equal(described.scenario, "未知场景");
});

test("in-flight finalization_error reads as a truthful waiting phrase, unknown codes fall through", () => {
  // 收尾局等 Raw Input 落盘：必须是进行中的人话，而不是按 limitations 误报成
  // 「Raw 来源不可用」。
  assert.equal(finalizationPendingText("trace_waiting_snapshot"), "正在等待输入数据落盘");
  assert.equal(finalizationPendingText("waiting_for_sources"), "正在等待训练数据落盘");
  // 终态/未知码不编造：返回 null，让调用方走原有 limitations 派生。
  assert.equal(finalizationPendingText("video_coverage_gap"), null);
  assert.equal(finalizationPendingText("brand_new_code"), null);
  assert.equal(finalizationPendingText(null), null);
  assert.equal(finalizationPendingText(undefined), null);
});
