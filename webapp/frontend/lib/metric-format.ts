/**
 * Shared metric formatting helpers (extracted from task5 DataView).
 *
 * Pure functions only — no rendering, no data fetching. Used by the analysis
 * data/diagnosis views and the Coach message metrics card so numbers, units
 * and limitation copy render identically everywhere.
 */

import { t, type MessageKey } from "./i18n/core";
import type { AnalysisMetricPresentation } from "./contracts";
import type { FrontendEvidenceSegmentsV1, TimelineEvent } from "./types";

/* 事件与行类型的自然语言命名（原稿「事件命名」面板 + 分布图）。
   i18n 批 1：表值是字典键，经 eventKindLabel() 在调用时解析。 */
const EVENT_KIND_KEYS: Record<string, MessageKey> = {
  kill: "metric.eventKind.kill",
  miss: "metric.eventKind.miss",
  peak: "metric.eventKind.peak",
  corrective: "metric.eventKind.corrective",
  transition: "metric.eventKind.transition",
  next_target_acquired: "metric.eventKind.nextTargetAcquired",
  settle: "metric.eventKind.settle",
  switch_chain: "metric.eventKind.switchChain",
  static_flick: "metric.eventKind.staticFlick",
  tracking_fixed_window: "metric.eventKind.trackingFixedWindow",
  tracking_episode: "metric.eventKind.trackingEpisode",
  tracking_change_response: "metric.eventKind.trackingChangeResponse",
  tracking_loss: "metric.eventKind.trackingLoss",
  tracking_reacquisition: "metric.eventKind.trackingReacquisition",
  low_confidence: "metric.eventKind.lowConfidence",
};

/** 事件类型 → 展示词；未知类型原样透传（不编造）。 */
export function eventKindLabel(kind: string): string {
  const key = EVENT_KIND_KEYS[kind];
  return key === undefined ? kind : t(key);
}

const LIMITATION_KEYS: Record<string, MessageKey> = {
  "Exact scenario hash, 1920x1080 resolution and one target bot only.": "metric.limitation.exactScenarioGate",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "metric.limitation.exactScenarioGate",
  "Unknown or multi-target scenarios remain fail-closed.": "metric.limitation.unknownMultiTargetFailClosed",
  "Unknown hashes and concurrent target layouts are not classified by this entry.": "metric.limitation.unknownMultiTargetFailClosed",
  alignment_latency_reported_separately: "metric.limitation.alignmentLatencySeparate",
  capture_alignment_descriptor_not_human_response: "metric.limitation.captureAlignmentNotResponse",
  descriptive_correction_burden: "metric.limitation.descriptiveCorrectionBurden",
  descriptive_smoothness_not_a_mechanism: "metric.limitation.descriptiveSmoothness",
  not_inferred_from_capture_alignment_or_tracking_samples: "metric.limitation.notInferredFromAlignment",
  player_aim_motion_unavailable_fixed_viewport_center: "metric.limitation.fixedViewportCenter",
  tracking_sparc_requires_uniform_window_and_accuracy_guardrail: "metric.limitation.sparcGuardrail",
  visual_quality_limited: "metric.limitation.visualQualityLimited",
  visual_quality_profile_unavailable: "metric.limitation.visualQualityProfileUnavailable",
  visual_quality_below_threshold: "metric.limitation.visualQualityBelowThreshold",
  target_relative_channels_unavailable: "metric.limitation.targetRelativeChannelsUnavailable",
  target_relative_target_ambiguous: "metric.limitation.targetRelativeAmbiguous",
  target_relative_samples_unavailable: "metric.limitation.targetRelativeSamplesUnavailable",
  no_target_visible: "metric.limitation.noTargetVisible",
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
  return metric.sources.join(t("metric.source.joinSeparator")) || t("metric.source.unlabeled");
}

export function valueText(metric: AnalysisMetricPresentation): string {
  if (metric.value === null) return t("metric.value.unavailable");
  const value = typeof metric.value === "number" ? Number(metric.value.toFixed(3)) : metric.value;
  const referenceKey = metricReference(metric);
  if (
    (referenceKey.endsWith("time_in_radius_ratio") || referenceKey === "target_switching.path_efficiency" || referenceKey === "path_efficiency")
    && typeof value === "number"
  ) {
    return `${Number((value * 100).toFixed(1))}%`;
  }
  const unitKeys: Record<string, MessageKey> = {
    count: "metric.unit.count",
    ms: "metric.unit.ms",
    px: "metric.unit.px",
    px_per_ms2: "metric.unit.pxPerMs2",
  };
  const unitKey = metric.unit ? unitKeys[metric.unit] : undefined;
  const unit = unitKey === undefined ? "" : t(unitKey);
  return `${value}${unit}`;
}

export function availabilityLabel(availability: string): string {
  if (availability === "available") return t("metric.availability.available");
  if (availability === "limited") return t("metric.availability.limited");
  return t("metric.availability.unavailable");
}

export function limitationLabel(limitation: string): string {
  const key = LIMITATION_KEYS[limitation];
  return key === undefined ? limitation : t(key);
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
      label: event.label || eventKindLabel(event.type),
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

/** segment_kind → 字典键；未知 kind 原样透传（不编造语义）。
    拍板：worst→「修正最多」与诊断页「最差」解歧；typical→「参照」。 */
export const SEGMENT_KIND_KEYS: Record<string, MessageKey> = {
  worst: "metric.segment.worst",
  typical: "metric.segment.typical",
  improved: "metric.segment.improved",
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
      kindLabel: (() => {
        const kind = segment.segment_kind;
        if (!kind) return t("metric.segment.fallback");
        const key = SEGMENT_KIND_KEYS[kind];
        return key === undefined ? kind : t(key);
      })(),
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
