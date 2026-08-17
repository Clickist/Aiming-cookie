/**
 * Shared metric formatting helpers (extracted from task5 DataView).
 *
 * Pure functions only — no rendering, no data fetching. Used by the analysis
 * data/diagnosis views and the Coach message metrics card so numbers, units
 * and limitation copy render identically everywhere.
 */

import type { AnalysisMetricPresentation } from "./contracts";

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
