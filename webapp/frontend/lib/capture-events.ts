/**
 * 设置页「最近采集事件」的人话映射（纯函数，无副作用）。
 *
 * 错误码清单来自后端 run meta 的实际写入点：
 * - video_error：webapp/backend/kovaak_run_store.py（mark_run_video_unavailable、
 *   invalidate_run_for_video_coverage_gap、reconcile_run_videos）与
 *   webapp/backend/kovaak_capture_finalizer.py（_TERMINAL_VIDEO_ERRORS 等）。
 * - trace_error：webapp/backend/kovaak_run_store.py（mark_mouse_trace_unavailable、
 *   _raw_snapshot_receipt_quality、reconcile_mouse_traces）。
 *
 * 合同：未知码显示原始码，不编造解释。
 */

import { t, type MessageKey } from "./i18n/core";

/** 等待整理态：run 还在 finalization 流程里，不算失败。 */
export const CAPTURE_PENDING_VIDEO_ERRORS = new Set([
  "video_waiting_artifact",
]);

export const CAPTURE_PENDING_TRACE_ERRORS = new Set([
  "trace_waiting_snapshot",
]);

/**
 * finalization_error 的「进行中」人话映射（非失败态）。
 * 收尾局的 trace 附加要等 Raw Input 数据落盘，这段时间 run 是 retryable，
 * 但历史行上按 limitations 派生只会得到「Raw 来源不可用」——事实相反。
 * 未知码返回 null，由调用方回退到原有 limitations 派生，不编造解释。
 */
const FINALIZATION_PENDING_KEYS: Record<string, MessageKey> = {
  trace_waiting_snapshot: "capture.finalization.waitingInputData",
  waiting_for_sources: "capture.finalization.waitingTrainingData",
};

export function finalizationPendingText(error: string | null | undefined): string | null {
  if (!error) return null;
  const key = FINALIZATION_PENDING_KEYS[error];
  return key === undefined ? null : t(key);
}

export const VIDEO_ERROR_KEYS: Record<string, MessageKey> = {
  // 打这局时应用未在录制（无 capture 会话 / 采集服务不可用）。
  video_capture_unavailable: "capture.error.notRecording",
  // 无有效 capture 会话（含窗口/控制通道无效）：同上一个人话归因。
  video_window_invalid: "capture.error.notRecording",
  // AC 在这局之后重启过，回放缓冲已丢失，无法补录。
  video_capture_session_mismatch: "capture.videoError.sessionMismatch",
  // 这一时间窗没有完整画面数据。
  video_coverage_gap: "capture.videoError.coverageGap",
  // 暂停局 fail-closed：不生成永久录像。
  video_pause_unsupported: "capture.videoError.pauseUnsupported",
  // 时间对齐不可用，录像无法与输入对齐。
  video_time_alignment_unavailable: "capture.videoError.timeAlignmentUnavailable",
  // 采集硬件 / 协议错误。
  video_hardware_invalid: "capture.videoError.hardwareInvalid",
  video_capture_protocol_invalid: "capture.videoError.protocolInvalid",
  // 本地录像文件校验失败（reconcile 清理时发现托管路径/回执无效）。
  video_pending_state_invalid: "capture.videoError.fileInvalid",
  video_managed_path_invalid: "capture.videoError.fileInvalid",
  video_receipt_invalid: "capture.videoError.fileInvalid",
  // 等待整理态：describe 会归一为「整理中」，表内短语仅兜底。
  video_waiting_artifact: "capture.videoError.waitingArtifact",
  // 用户主动移除。
  removed_by_user: "capture.error.removedByUser",
};

export const TRACE_ERROR_KEYS: Record<string, MessageKey> = {
  // 这一时间窗没有输入数据（覆盖缺口）。
  trace_raw_window_coverage_gap: "capture.traceError.coverageGap",
  // 采集队列丢点 / 缓冲环过期：输入数据不完整。
  trace_raw_queue_dropped: "capture.traceError.queueDropped",
  trace_raw_ring_expired: "capture.traceError.ringExpired",
  // 快照过保留期 / 采集不可用。
  trace_snapshot_stale: "capture.traceError.snapshotStale",
  trace_capture_unavailable: "capture.error.notRecording",
  trace_snapshot_failed: "capture.traceError.snapshotFailed",
  trace_quality_insufficient: "capture.traceError.qualityInsufficient",
  trace_attach_failed: "capture.traceError.attachFailed",
  trace_legacy_quality_unknown: "capture.traceError.qualityUnknown",
  // 等待整理态：describe 会归一为「整理中」，表内短语仅兜底。
  trace_waiting_snapshot: "capture.traceError.waitingSnapshot",
  // 用户主动移除。
  removed_by_user: "capture.error.removedByUser",
};

export interface CaptureRunEventInput {
  scenario?: string | null;
  video_attached?: boolean | null;
  raw_attached?: boolean | null;
  video_error?: string | null;
  trace_error?: string | null;
  finalization_state?: string | null;
}

/**
 * 采集证据的结构化状态（i18n 批 1 解耦）：状态归并只比较这个枚举，
 * 显示词（videoLabel/traceLabel/status.word）只做显示——翻译后逻辑不碎。
 */
export type CaptureEvidenceStatus = "attached" | "pending" | "not_recorded" | "missing";

export interface CaptureRunEventDescription {
  scenario: string;
  /** 例：`已录制` / `未录制（打这局时应用未在录制）` / `整理中`。 */
  videoLabel: string;
  /** 例：`已记录` / `缺失（这一时间窗没有输入数据）` / `整理中`。 */
  traceLabel: string;
  /** 与 videoLabel 同源的结构化状态（供 summarizeCaptureRunStatus 归并）。 */
  videoStatus: CaptureEvidenceStatus;
  /** 与 traceLabel 同源的结构化状态。 */
  traceStatus: CaptureEvidenceStatus;
  /** 双证齐 = 正常局。 */
  healthy: boolean;
}

function finalizationInProgress(state: string | null | undefined): boolean {
  return state === "pending" || state === "capturing" || state === "finalizing";
}

/** 错误码 → 人话短语；未知码原样透传（不编造）。 */
export function videoErrorCodeLabel(code: string): string {
  const key = VIDEO_ERROR_KEYS[code];
  return key === undefined ? code : t(key);
}

/** 错误码 → 人话短语；未知码原样透传（不编造）。 */
export function traceErrorCodeLabel(code: string): string {
  const key = TRACE_ERROR_KEYS[code];
  return key === undefined ? code : t(key);
}

export type CaptureRunStatusTone = "ready" | "working" | "missing";

/**
 * 行级状态归并（设置页「最近采集事件」每行一个圆点 + 一个状态词）：
 * 任一证整理中 → 整理中（橙）；双证齐 → 已就绪（绿）；
 * 视频未录制 → 未录制（橙）；其余（视频在、轨迹缺失）→ 缺失（灰）。
 * 归并依据是 describeCaptureRunEvent 的结构化状态枚举，不是显示词。
 */
export function summarizeCaptureRunStatus(
  videoStatus: CaptureEvidenceStatus,
  traceStatus: CaptureEvidenceStatus,
): { word: string; tone: CaptureRunStatusTone } {
  if (videoStatus === "pending" || traceStatus === "pending") {
    return { word: t("capture.status.organizing"), tone: "working" };
  }
  if (videoStatus === "attached" && traceStatus === "attached") {
    return { word: t("capture.status.ready"), tone: "ready" };
  }
  if (videoStatus === "not_recorded") {
    return { word: t("capture.status.notRecorded"), tone: "working" };
  }
  return { word: t("capture.status.missing"), tone: "missing" };
}

export function describeCaptureRunEvent(run: CaptureRunEventInput): CaptureRunEventDescription {
  const pending = finalizationInProgress(run.finalization_state);

  let videoStatus: CaptureEvidenceStatus;
  let videoLabel: string;
  if (run.video_attached) {
    videoStatus = "attached";
    videoLabel = t("capture.evidence.videoRecorded");
  } else if (run.video_error && !CAPTURE_PENDING_VIDEO_ERRORS.has(run.video_error)) {
    videoStatus = "not_recorded";
    videoLabel = t("capture.evidence.videoMissingDetail", { detail: videoErrorCodeLabel(run.video_error) });
  } else if (run.video_error || pending) {
    videoStatus = "pending";
    videoLabel = t("capture.evidence.organizing");
  } else {
    videoStatus = "not_recorded";
    videoLabel = t("capture.evidence.videoMissing");
  }

  let traceStatus: CaptureEvidenceStatus;
  let traceLabel: string;
  if (run.raw_attached) {
    traceStatus = "attached";
    traceLabel = t("capture.evidence.traceRecorded");
  } else if (run.trace_error && !CAPTURE_PENDING_TRACE_ERRORS.has(run.trace_error)) {
    traceStatus = "missing";
    traceLabel = t("capture.evidence.traceMissingDetail", { detail: traceErrorCodeLabel(run.trace_error) });
  } else if (run.trace_error || pending) {
    traceStatus = "pending";
    traceLabel = t("capture.evidence.organizing");
  } else {
    traceStatus = "missing";
    traceLabel = t("capture.evidence.traceMissing");
  }

  return {
    scenario: run.scenario?.trim() || t("capture.evidence.unknownScenario"),
    videoLabel,
    traceLabel,
    videoStatus,
    traceStatus,
    healthy: Boolean(run.video_attached) && Boolean(run.raw_attached),
  };
}
