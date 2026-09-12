/**
 * Shared metric formatting helpers (extracted from task5 DataView).
 *
 * Pure functions only — no rendering, no data fetching. Used by the analysis
 * data/diagnosis views and the Coach message metrics card so numbers, units
 * and limitation copy render identically everywhere.
 */

import type { AnalysisMetricPresentation } from "./contracts";
import type { FrontendEvidenceSegmentsV1, TimelineEvent } from "./types";

/* 事件与行类型的自然语言命名（原稿「事件命名」面板 + 分布图） */
export const EVENT_KIND_LABELS: Record<string, string> = {
  kill: "击杀",
  miss: "未命中",
  peak: "速度峰值",
  corrective: "修正动作",
  transition: "开始切换",
  next_target_acquired: "到达下一目标",
  settle: "稳定完成",
  switch_chain: "目标切换链",
  static_flick: "单次 Flick",
  tracking_fixed_window: "固定跟踪窗口",
  tracking_episode: "跟踪片段",
  tracking_change_response: "观测到的变向响应",
  tracking_loss: "偏离",
  tracking_reacquisition: "重新捕获",
  low_confidence: "低可信度观测",
};

export const LIMITATION_LABELS: Record<string, string> = {
  "Exact scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Unknown or multi-target scenarios remain fail-closed.": "未知场景或多目标布局不生成此类结论。",
  "Unknown hashes and concurrent target layouts are not classified by this entry.": "未知场景或多目标布局不生成此类结论。",
  alignment_latency_reported_separately: "对齐延迟单独报告，不等同于跟随滞后。",
  capture_alignment_descriptor_not_human_response: "这是采集对齐描述，不能分离具体响应来源。",
  descriptive_correction_burden: "仅描述修正负担，不作为机制结论。",
  descriptive_smoothness_not_a_mechanism: "平滑度只作描述，不解释成因。",
  not_inferred_from_capture_alignment_or_tracking_samples: "当前证据不足以分离具体响应来源。",
  player_aim_motion_unavailable_fixed_viewport_center: "固定视口录制无法分离玩家视角运动。",
  tracking_sparc_requires_uniform_window_and_accuracy_guardrail: "该平滑度指标需要均匀时间窗与准确度门槛。",
  visual_quality_limited: "视觉质量受限",
  visual_quality_profile_unavailable: "视觉质量验证不可用",
  visual_quality_below_threshold: "视觉质量未达到分析门槛",
  target_relative_channels_unavailable: "缺少安全的目标相对误差通道",
  target_relative_target_ambiguous: "目标身份无法可靠确定",
  target_relative_samples_unavailable: "目标相对误差样本不可用",
  no_target_visible: "个别帧未检测到目标",
};

export function metricReference(metric: AnalysisMetricPresentation): string {
  return metric.referenceKey ?? metric.key;
}

export function metricLabel(metric: AnalysisMetricPresentation): string {
  return metric.definition?.name ?? metricReference(metric);
}

export function metricDescription(metric: AnalysisMetricPresentation): string | null {
  return metric.definition?.description ?? null;
}

export function metricSourceText(metric: AnalysisMetricPresentation): string {
  if (metric.sources.some((source) => source.includes("tracking-analysis"))) return "tracking-analysis";
  return metric.sources.join("、") || "未标注来源";
}

export function valueText(metric: AnalysisMetricPresentation): string {
  if (metric.value === null) return "不可用";
  const value = typeof metric.value === "number" ? Number(metric.value.toFixed(3)) : metric.value;
  const referenceKey = metricReference(metric);
  if (
    (referenceKey.endsWith("time_in_radius_ratio") || referenceKey === "target_switching.path_efficiency" || referenceKey === "path_efficiency")
    && typeof value === "number"
  ) {
    return `${Number((value * 100).toFixed(1))}%`;
  }
  const unit = {
    count: " 次",
    ms: " ms",
    px: " px",
    px_per_ms2: " px/ms²",
  }[metric.unit ?? ""] ?? "";
  return `${value}${unit}`;
}

export function availabilityLabel(availability: string): string {
  if (availability === "available") return "可用";
  if (availability === "limited") return "受限";
  return "暂不可用";
}

export function limitationLabel(limitation: string): string {
  return LIMITATION_LABELS[limitation] ?? limitation;
}

/* ── 视频面板复盘升级 P1 事件上轴（brief §二 P1）────────────────────────
   标记数据契约对齐 videojs-markers 四元组习惯，收敛为 { timeMs, type, label }。
   数据源是 AnalysisWorkspacePresentation.timeline（presentTimelineEvent 投影）：
   type/label 后端齐备；时间无现成 ms 字段，由 relative_ms ?? time_s×1000
   纯前端推导，不改底层合同。 */

/** 事件类型 → 时间轴标记语义桶（决定 --event-* token 与形状）。
    miss/death 归入同一"失误"桶（brief：miss/death → --event-miss）。 */
const MARKER_TYPE_BUCKETS: Record<string, TimelineMarker["type"]> = {
  kill: "kill",
  miss: "miss",
  death: "miss",
  peak: "peak",
};

export interface TimelineMarker {
  timeMs: number;
  type: "kill" | "miss" | "peak";
  label: string;
}

/**
 * 把分析 timeline 投影为上轴标记：只保留 kill / miss(death) / peak 三类
 * 高频事件。corrective 本批不上——后端 corrective_frames 是 peak→end 中点
 * 的粗估质心（worker extras 显式标注 corrective_frame_estimated），锚帧精度
 * 不足以支撑「seek 并暂停在锚点帧」的语义。无有效时间的事件丢弃不编造。
 */
export function projectTimelineMarkers(events: ReadonlyArray<TimelineEvent>): TimelineMarker[] {
  const markers: Array<TimelineMarker & { sortMs: number }> = [];
  for (const event of events) {
    const type = MARKER_TYPE_BUCKETS[event.type];
    if (!type) continue;
    // relative_ms 是首选权威值（原样使用）；否则由秒转毫秒。
    const sortMs = typeof event.relative_ms === "number" && Number.isFinite(event.relative_ms)
      ? event.relative_ms
      : typeof event.time_s === "number" && Number.isFinite(event.time_s)
        ? Math.round(event.time_s * 1000)
        : null;
    if (sortMs === null || sortMs < 0) continue;
    markers.push({
      timeMs: sortMs,
      type,
      label: event.label || EVENT_KIND_LABELS[event.type] || event.type,
      sortMs,
    });
  }
  markers.sort((left, right) => left.sortMs - right.sortMs);
  return markers.map(({ timeMs, type, label }) => ({ timeMs, type, label }));
}

/* ── 视频面板复盘升级 P2 信号片段循环（brief §二 P2＋D2/D4/D5/D6）────────
   数据核查结论（brief §四核查项 2）：精确信号窗口的权威源是
   GET /api/sessions/{id}/evidence-segments（frontend_evidence_segments.v1，
   前端已有 getAnalysisEvidenceSegments 接线）——playback.relative_start_ms /
   relative_end_ms 由 canonical_time_window 与 MP4 preroll 校正为「视频相对
   毫秒」，availability 门控，segment_id 可关联 analysis:。segment_kind 词表
   是 worst / typical / improved（worker rank_reason），映射短类型词。
   接口失败 / 响应为空 / 全部 playback unavailable 时降级：用 P1 的
   projectTimelineMarkers 结果里的 peak（速度峰值类）锚点 ± 固定窗口做前端
   推导按钮；kill/miss 保持 P1 的点标记语义不出循环窗。均不改底层合同。 */

/** 视频底部时间段按钮的数据形状：视频相对毫秒区间＋短类型词。 */
export interface SegmentButton {
  id: string;
  startMs: number;
  endMs: number;
  kindLabel: string;
}

/** segment_kind → 短类型词；未知 kind 原样透传（不编造语义）。
    拍板：worst→「修正最多」与诊断页「最差」解歧；typical→「参照」。 */
export const SEGMENT_KIND_LABELS: Record<string, string> = {
  worst: "修正最多",
  typical: "参照",
  improved: "改善",
};

/**
 * 权威路径：evidence-segments 投影 → 时间段按钮。
 * 只接受 playback.available 且起止毫秒齐备、区间有限的段；其余静默丢弃，
 * 交给调用方的降级路径。后端 focus 窗口是「瞬间」级（实测 400-700ms 宽），
 * 原样当循环区间是不到 1 秒的眨眼循环——输出时扩成最小有效循环窗：取
 * 焦点区间与「焦点中心 ± SIGNAL_SEGMENT_FALLBACK_WINDOW_MS」的并集（与
 * 降级路径同一哲学；焦点区间自身 ≥ 2×窗口时并集即原区间，不再扩大）。
 * maxMs 提供时把终点钳在视频时长内（起点恒 ≥ 0）；钳后退化为空区间的段
 * 剔除。结果按起点升序（与时间轴阅读方向一致）。
 */
export function projectEvidenceSegmentButtons(
  payload: FrontendEvidenceSegmentsV1,
  options: { maxMs?: number } = {},
): SegmentButton[] {
  const { maxMs } = options;
  const clampEnd = (value: number) =>
    typeof maxMs === "number" && Number.isFinite(maxMs) && maxMs > 0
      ? Math.min(value, maxMs)
      : value;
  const buttons: SegmentButton[] = [];
  for (const segment of payload.segments) {
    const playback = segment.playback;
    if (playback?.availability !== "available") continue;
    const startMs = playback.relative_start_ms;
    const endMs = playback.relative_end_ms;
    if (
      typeof startMs !== "number" || !Number.isFinite(startMs)
      || typeof endMs !== "number" || !Number.isFinite(endMs)
      || startMs < 0 || endMs <= startMs
    ) continue;
    // 中心取整到毫秒，避免奇数区间和产生半毫秒窗口。
    const centerMs = Math.round((startMs + endMs) / 2);
    const button: SegmentButton = {
      id: segment.segment_id,
      startMs: Math.max(0, Math.min(startMs, centerMs - SIGNAL_SEGMENT_FALLBACK_WINDOW_MS)),
      endMs: clampEnd(Math.max(endMs, centerMs + SIGNAL_SEGMENT_FALLBACK_WINDOW_MS)),
      kindLabel: (segment.segment_kind && SEGMENT_KIND_LABELS[segment.segment_kind])
        || segment.segment_kind
        || "片段",
    };
    if (button.endMs <= button.startMs) continue;
    buttons.push(button);
  }
  return buttons.sort((left, right) => left.startMs - right.startMs);
}

/** 降级窗口半径：锚点前后各 0.75s＝一次约 1.5s 的精读循环（收窄自 2.5s）。 */
export const SIGNAL_SEGMENT_FALLBACK_WINDOW_MS = 750;
/** 降级来源可能高产（逐次挥击都算 peak），排超限截断保护按钮排可用性。 */
export const SIGNAL_SEGMENT_FALLBACK_LIMIT = 12;

/**
 * 降级路径：peak 标记 ± 固定窗口推导按钮。maxMs 提供时把终点钳在视频时长内；
 * clamp 后退化为空区间的窗口剔除。
 */
export function projectPeakFallbackButtons(
  markers: ReadonlyArray<TimelineMarker>,
  options: { maxMs?: number; limit?: number } = {},
): SegmentButton[] {
  const { maxMs, limit = SIGNAL_SEGMENT_FALLBACK_LIMIT } = options;
  const clampEnd = (value: number) =>
    typeof maxMs === "number" && Number.isFinite(maxMs) && maxMs > 0
      ? Math.min(value, maxMs)
      : value;
  return markers
    .filter((marker) => marker.type === "peak")
    .sort((left, right) => left.timeMs - right.timeMs)
    .slice(0, limit)
    .map((marker) => ({
      id: `peak-${marker.timeMs}`,
      startMs: Math.max(0, marker.timeMs - SIGNAL_SEGMENT_FALLBACK_WINDOW_MS),
      endMs: clampEnd(marker.timeMs + SIGNAL_SEGMENT_FALLBACK_WINDOW_MS),
      kindLabel: marker.label,
    }))
    .filter((button) => button.endMs > button.startMs);
}
