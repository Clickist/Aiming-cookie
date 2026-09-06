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

/** 等待整理态：run 还在 finalization 流程里，不算失败。 */
export const CAPTURE_PENDING_VIDEO_ERRORS = new Set([
  "video_waiting_artifact",
]);

export const CAPTURE_PENDING_TRACE_ERRORS = new Set([
  "trace_waiting_snapshot",
]);

export const VIDEO_ERROR_LABELS: Record<string, string> = {
  // 打这局时应用未在录制（无 capture 会话 / 采集服务不可用）。
  video_capture_unavailable: "打这局时应用未在录制",
  // 无有效 capture 会话（含窗口/控制通道无效）：同上一个人话归因。
  video_window_invalid: "打这局时应用未在录制",
  // AC 在这局之后重启过，回放缓冲已丢失，无法补录。
  video_capture_session_mismatch: "AC 在这局之后重启过，回放画面没有保留",
  // 这一时间窗没有完整画面数据。
  video_coverage_gap: "这一时间窗没有完整画面数据",
  // 暂停局 fail-closed：不生成永久录像。
  video_pause_unsupported: "暂停的对局不生成录像",
  // 时间对齐不可用，录像无法与输入对齐。
  video_time_alignment_unavailable: "时间对齐失败，录像无法与输入对齐",
  // 采集硬件 / 协议错误。
  video_hardware_invalid: "采集硬件初始化失败",
  video_capture_protocol_invalid: "采集协议错误",
  // 本地录像文件校验失败（reconcile 清理时发现托管路径/回执无效）。
  video_pending_state_invalid: "本地录像文件校验失败",
  video_managed_path_invalid: "本地录像文件校验失败",
  video_receipt_invalid: "本地录像文件校验失败",
  // 等待整理态：describe 会归一为「整理中」，表内短语仅兜底。
  video_waiting_artifact: "正在等待录像整理",
  // 用户主动移除。
  removed_by_user: "已由你移除",
};

export const TRACE_ERROR_LABELS: Record<string, string> = {
  // 这一时间窗没有输入数据（覆盖缺口）。
  trace_raw_window_coverage_gap: "这一时间窗没有输入数据",
  // 采集队列丢点 / 缓冲环过期：输入数据不完整。
  trace_raw_queue_dropped: "采集队列丢点，输入数据不完整",
  trace_raw_ring_expired: "采集缓冲过期，输入数据不完整",
  // 快照过保留期 / 采集不可用。
  trace_snapshot_stale: "输入快照已过保留期",
  trace_capture_unavailable: "打这局时应用未在录制",
  trace_snapshot_failed: "输入快照解析失败",
  trace_quality_insufficient: "输入数据质量不足",
  trace_attach_failed: "输入轨迹写入失败",
  trace_legacy_quality_unknown: "输入数据质量无法判定",
  // 等待整理态：describe 会归一为「整理中」，表内短语仅兜底。
  trace_waiting_snapshot: "正在等待输入轨迹整理",
  // 用户主动移除。
  removed_by_user: "已由你移除",
};

export interface CaptureRunEventInput {
  scenario?: string | null;
  video_attached?: boolean | null;
  raw_attached?: boolean | null;
  video_error?: string | null;
  trace_error?: string | null;
  finalization_state?: string | null;
}

export interface CaptureRunEventDescription {
  scenario: string;
  /** 例：`已录制` / `未录制（打这局时应用未在录制）` / `整理中`。 */
  videoLabel: string;
  /** 例：`已记录` / `缺失（这一时间窗没有输入数据）` / `整理中`。 */
  traceLabel: string;
  /** 双证齐 = 正常局。 */
  healthy: boolean;
}

function finalizationInProgress(state: string | null | undefined): boolean {
  return state === "pending" || state === "capturing" || state === "finalizing";
}

/** 错误码 → 人话短语；未知码原样透传（不编造）。 */
export function videoErrorCodeLabel(code: string): string {
  return VIDEO_ERROR_LABELS[code] ?? code;
}

/** 错误码 → 人话短语；未知码原样透传（不编造）。 */
export function traceErrorCodeLabel(code: string): string {
  return TRACE_ERROR_LABELS[code] ?? code;
}

export function describeCaptureRunEvent(run: CaptureRunEventInput): CaptureRunEventDescription {
  const pending = finalizationInProgress(run.finalization_state);

  let videoLabel: string;
  if (run.video_attached) {
    videoLabel = "已录制";
  } else if (run.video_error && !CAPTURE_PENDING_VIDEO_ERRORS.has(run.video_error)) {
    videoLabel = `未录制（${videoErrorCodeLabel(run.video_error)}）`;
  } else if (run.video_error || pending) {
    videoLabel = "整理中";
  } else {
    videoLabel = "未录制";
  }

  let traceLabel: string;
  if (run.raw_attached) {
    traceLabel = "已记录";
  } else if (run.trace_error && !CAPTURE_PENDING_TRACE_ERRORS.has(run.trace_error)) {
    traceLabel = `缺失（${traceErrorCodeLabel(run.trace_error)}）`;
  } else if (run.trace_error || pending) {
    traceLabel = "整理中";
  } else {
    traceLabel = "缺失";
  }

  return {
    scenario: run.scenario?.trim() || "未知场景",
    videoLabel,
    traceLabel,
    healthy: Boolean(run.video_attached) && Boolean(run.raw_attached),
  };
}
