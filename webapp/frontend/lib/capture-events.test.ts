import assert from "node:assert/strict";
import { test } from "node:test";

import {
  describeCaptureRunEvent,
  finalizationPendingText,
  summarizeCaptureRunStatus,
  traceErrorCodeLabel,
  TRACE_ERROR_KEYS,
  videoErrorCodeLabel,
  VIDEO_ERROR_KEYS,
} from "./capture-events";
import { translate } from "./i18n/core";

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
    Object.keys(VIDEO_ERROR_KEYS).sort(),
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
    Object.keys(TRACE_ERROR_KEYS).sort(),
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
  // 断言与字典同源（zh-CN 值逐字来自现行文案），改词不再碎测试。
  assert.equal(described.videoLabel, translate("zh-CN", "capture.evidence.videoRecorded"));
  assert.equal(described.traceLabel, translate("zh-CN", "capture.evidence.traceRecorded"));
  assert.equal(described.videoStatus, "attached");
  assert.equal(described.traceStatus, "attached");
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
  assert.equal(
    described.videoLabel,
    translate("zh-CN", "capture.evidence.videoMissingDetail", { detail: translate("zh-CN", "capture.error.notRecording") }),
  );
  assert.equal(
    described.traceLabel,
    translate("zh-CN", "capture.evidence.traceMissingDetail", { detail: translate("zh-CN", "capture.traceError.coverageGap") }),
  );
  assert.equal(described.videoStatus, "not_recorded");
  assert.equal(described.traceStatus, "missing");
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
  assert.equal(
    described.videoLabel,
    translate("zh-CN", "capture.evidence.videoMissingDetail", { detail: translate("zh-CN", "capture.error.notRecording") }),
  );
  assert.equal(
    described.traceLabel,
    translate("zh-CN", "capture.evidence.traceMissingDetail", { detail: "trace_totally_unknown" }),
  );
});

test("waiting codes and in-flight finalization read as organizing, not failed", () => {
  const waiting = describeCaptureRunEvent({
    video_attached: false,
    raw_attached: false,
    video_error: "video_waiting_artifact",
    trace_error: "trace_waiting_snapshot",
    finalization_state: "pending",
  });
  assert.equal(waiting.videoLabel, translate("zh-CN", "capture.evidence.organizing"));
  assert.equal(waiting.traceLabel, translate("zh-CN", "capture.evidence.organizing"));

  const inFlight = describeCaptureRunEvent({
    video_attached: false,
    raw_attached: false,
    video_error: null,
    trace_error: null,
    finalization_state: "finalizing",
  });
  assert.equal(inFlight.videoLabel, translate("zh-CN", "capture.evidence.organizing"));
  assert.equal(inFlight.traceLabel, translate("zh-CN", "capture.evidence.organizing"));
});

test("blank scenario falls back to the dictionary unknown-scenario word", () => {
  const described = describeCaptureRunEvent({ video_attached: true, raw_attached: true });
  assert.equal(described.scenario, translate("zh-CN", "capture.evidence.unknownScenario"));
});

test("row status merges on the structured status enum, not display words (i18n 批 1 解耦)", () => {
  // 任一证整理中 → 整理中（working）；双证齐 → 已就绪（ready）；
  // 视频未录制 → 未录制（working）；其余 → 缺失（missing）。
  assert.deepEqual(summarizeCaptureRunStatus("pending", "attached"), { word: translate("zh-CN", "capture.status.organizing"), tone: "working" });
  assert.deepEqual(summarizeCaptureRunStatus("attached", "pending"), { word: translate("zh-CN", "capture.status.organizing"), tone: "working" });
  assert.deepEqual(summarizeCaptureRunStatus("attached", "attached"), { word: translate("zh-CN", "capture.status.ready"), tone: "ready" });
  assert.deepEqual(summarizeCaptureRunStatus("not_recorded", "missing"), { word: translate("zh-CN", "capture.status.notRecorded"), tone: "working" });
  assert.deepEqual(summarizeCaptureRunStatus("attached", "missing"), { word: translate("zh-CN", "capture.status.missing"), tone: "missing" });
  // describe 与 summarize 同源衔接：结构化状态直接来自 describe 的分支。
  const described = describeCaptureRunEvent({
    video_attached: true,
    raw_attached: false,
    video_error: null,
    trace_error: "trace_quality_insufficient",
    finalization_state: "finalized",
  });
  assert.deepEqual(
    summarizeCaptureRunStatus(described.videoStatus, described.traceStatus),
    { word: translate("zh-CN", "capture.status.missing"), tone: "missing" },
  );
});

test("in-flight finalization_error reads as a truthful waiting phrase, unknown codes fall through", () => {
  // 收尾局等 Raw Input 落盘：必须是进行中的人话，而不是按 limitations 误报成
  // 「Raw 来源不可用」。
  assert.equal(finalizationPendingText("trace_waiting_snapshot"), translate("zh-CN", "capture.finalization.waitingInputData"));
  assert.equal(finalizationPendingText("waiting_for_sources"), translate("zh-CN", "capture.finalization.waitingTrainingData"));
  // 终态/未知码不编造：返回 null，让调用方走原有 limitations 派生。
  assert.equal(finalizationPendingText("video_coverage_gap"), null);
  assert.equal(finalizationPendingText("brand_new_code"), null);
  assert.equal(finalizationPendingText(null), null);
  assert.equal(finalizationPendingText(undefined), null);
});
