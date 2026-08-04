"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisData, getAnalysisFamilyData } from "@/lib/api";
import type { AnalysisMetricPresentation, AnalysisWorkspacePresentation } from "@/lib/contracts";
import type {
  FrontendAnalysisDataV1,
  FrontendAnalysisFamilyDataRowV1,
  FrontendAnalysisFamilyDataV1,
} from "@/lib/types";
import { Badge, Button, Empty, Loading, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

const METRIC_LABELS: Record<string, string> = {
  "continuous_tracking.target_relative_error_px": "目标偏差",
  "continuous_tracking.time_in_radius_ratio": "目标范围内时间占比",
  "continuous_tracking.loss_count": "偏离目标次数",
  "continuous_tracking.loss_duration_ms": "偏离目标持续时间",
  "continuous_tracking.reacquisition_latency_ms": "重新捕获延迟",
  "continuous_tracking.correction_burden": "修正负担",
  "target_switching.transition_time_ms": "切换到新目标耗时",
  "target_switching.transition_distance_px": "切换位移",
  "target_switching.path_efficiency": "路径效率",
  "target_switching.settle_duration_ms": "到达后稳定耗时",
  transition_time_ms: "切换到新目标耗时",
  transition_distance_px: "切换位移",
  path_efficiency: "路径效率",
  settle_duration_ms: "到达后稳定耗时",
  target_relative_error_px: "目标偏差",
  time_in_radius_ratio: "目标范围内时间占比",
  loss_count: "偏离目标次数",
  loss_duration_ms: "偏离目标持续时间",
  reacquisition_latency_ms: "重新跟上耗时",
  correction_burden: "修正负担",
  sparc: "运动平滑度（SPARC）",
  decel_frac: "减速占比",
  linearity: "直线性",
  reverse_ratio: "反向修正",
  duration_ms: "偏离持续时间",
  observed_change_response_ms: "观测到的变向响应",
  alignment_latency_ms: "采集对齐延迟",
  post_change_error_px: "变向后目标偏差",
  accel_duration_ms: "加速阶段",
  decel_duration_ms: "减速阶段",
  peak_speed: "峰值速度",
  corrective_count: "修正次数",
};

/* 事件与行类型的自然语言命名（原稿「事件命名」面板 + 分布图） */
const EVENT_KIND_LABELS: Record<string, string> = {
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

const FAMILY_METRIC_DESCRIPTIONS: Record<string, string> = {
  transition_time_ms: "中位数；越短越好但不追极限",
  transition_distance_px: "本次击杀到下一目标的移动距离",
  path_efficiency: "直线程度；低于 0.6 说明绕路明显",
  settle_duration_ms: "本次证据无法可靠判定稳定完成点",
  target_relative_error_px: "鼠标与目标中心的距离（归一化前）",
  time_in_radius_ratio: "鼠标在目标半径内的时间比例",
  loss_count: "鼠标离开目标半径的次数",
  loss_duration_ms: "每次偏离的典型持续时间（中位数）",
  reacquisition_latency_ms: "偏离后重新进入目标范围的耗时（中位数）",
  correction_burden: "维持跟踪需要的修正强度",
  sparc: "移动轨迹的频谱弧长（越大越平滑）",
  decel_frac: "减速段占整次 Flick 的时间比例",
  linearity: "路径质量",
  reverse_ratio: "反向修正比例",
};

const FAMILY_GROUPS: Record<string, { title: string; keys: string[] }[]> = {
  target_switching: [
    { title: "切换速度", keys: ["target_switching.transition_time_ms", "transition_time_ms"] },
    { title: "移动质量", keys: ["target_switching.transition_distance_px", "transition_distance_px", "target_switching.path_efficiency", "path_efficiency"] },
    { title: "稳定控制", keys: ["target_switching.settle_duration_ms", "settle_duration_ms"] },
  ],
  continuous_tracking: [
    { title: "跟踪质量", keys: ["continuous_tracking.target_relative_error_px", "target_relative_error_px", "continuous_tracking.time_in_radius_ratio", "time_in_radius_ratio", "continuous_tracking.sparc", "sparc"] },
    { title: "偏离与恢复", keys: ["continuous_tracking.loss_count", "loss_count", "continuous_tracking.loss_duration_ms", "loss_duration_ms", "continuous_tracking.reacquisition_latency_ms", "reacquisition_latency_ms"] },
    { title: "控制负担", keys: ["continuous_tracking.correction_burden", "correction_burden"] },
  ],
  static_clicking: [
    { title: "停枪控制", keys: ["sparc", "decel_frac"] },
    { title: "动作质量", keys: ["linearity", "reverse_ratio"] },
    { title: "效率", keys: ["path_efficiency"] },
  ],
};

const LIMITATION_LABELS: Record<string, string> = {
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

function metricReference(metric: AnalysisMetricPresentation): string {
  return metric.referenceKey ?? metric.key;
}

function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

function metricDescription(key: string): string | null {
  return FAMILY_METRIC_DESCRIPTIONS[key] ?? null;
}

function metricSourceText(metric: AnalysisMetricPresentation): string {
  if (metric.sources.some((source) => source.includes("tracking-analysis"))) return "tracking-analysis";
  return metric.sources.join("、") || "未标注来源";
}

function valueText(metric: AnalysisMetricPresentation): string {
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

function availabilityLabel(availability: string): string {
  if (availability === "available") return "可用";
  if (availability === "limited") return "受限";
  return "暂不可用";
}

function familyMetricText(key: string, value: number): string {
  if (key === "path_efficiency" || key === "time_in_radius_ratio" || key.endsWith("path_efficiency") || key.endsWith("time_in_radius_ratio")) {
    return `${Number((value * 100).toFixed(1))}%`;
  }
  if (key.endsWith("_ms")) return `${Number(value.toFixed(1))} ms`;
  if (key.endsWith("_px")) return `${Number(value.toFixed(1))} px`;
  if (key === "corrective_count" || key.endsWith("_count")) return `${Number(value.toFixed(1))} 次`;
  if (key === "peak_speed") return `${Number(value.toFixed(2))} counts/ms`;
  return String(Number(value.toFixed(3)));
}

function formatRelativeTime(value: number): string {
  const totalSeconds = Math.max(0, value) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function rowBounds(row: FrontendAnalysisFamilyDataRowV1): [number, number] | null {
  const values = Object.values(row.timing).filter(Number.isFinite);
  return values.length ? [Math.min(...values), Math.max(...values)] : null;
}

function limitationLabel(limitation: string): string {
  return LIMITATION_LABELS[limitation] ?? limitation;
}

function unique(items: readonly string[]): string[] {
  return Array.from(new Set(items));
}

function MetricOverviewPanel({
  metrics,
  onSelectMetric,
  familyCode,
}: {
  metrics: AnalysisMetricPresentation[];
  onSelectMetric: (metric: string) => void;
  familyCode: string;
}) {
  const groups = FAMILY_GROUPS[familyCode] ?? [{ title: "指标", keys: [] }];
  const groupMap = groups.map((group) => ({
    ...group,
    metrics: metrics.filter((metric) => {
      const ref = metricReference(metric);
      return group.keys.includes(ref) || group.keys.includes(metric.key);
    }),
  })).filter((group) => group.metrics.length > 0);

  const remaining = metrics.filter((metric) =>
    !groups.some((group) => group.keys.includes(metricReference(metric)) || group.keys.includes(metric.key)));

  return (
    <div className={styles.metricOverviewPanel}>
      {groupMap.map((group) => (
        <div className={styles.metricGroupBlock} key={group.title}>
          <div className={styles.metricGroupHeader}>
            <span>{group.title}</span>
            <span>{group.metrics.length} 项</span>
          </div>
          <div className={styles.metricGroupRows}>
            {group.metrics.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metricReference(metric))}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(ref) ?? availabilityLabel(metric.availability)}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {remaining.length ? (
        <div className={styles.metricGroupBlock}>
          <div className={styles.metricGroupHeader}>
            <span>其他</span>
            <span>{remaining.length} 项</span>
          </div>
          <div className={styles.metricGroupRows}>
            {remaining.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metricReference(metric))}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(ref) ?? availabilityLabel(metric.availability)}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SwitchChainRow({
  index,
  onSelectTime,
  row,
}: {
  index: number;
  onSelectTime: (timeMs: number) => void;
  row: FrontendAnalysisFamilyDataRowV1;
}) {
  const bounds = rowBounds(row);
  const { kill_ms: kill, transition_ms: transition, acquire_ms: acquire, settle_ms: settle } = row.timing;
  const hasAll = [kill, transition, acquire].every(Number.isFinite);
  const total = settle ?? acquire ?? transition ?? 1;
  const slow = typeof row.metrics.path_efficiency === "number" && row.metrics.path_efficiency < 0.6;
  const accessibleLabel = [
    `完整切换 #${index + 1}`,
    `切换到新目标耗时 ${familyMetricText("transition_time_ms", row.metrics.transition_time_ms)}`,
    `切换位移 ${familyMetricText("transition_distance_px", row.metrics.transition_distance_px)}`,
    `路径效率 ${familyMetricText("path_efficiency", row.metrics.path_efficiency)}`,
    `到达后稳定耗时 ${familyMetricText("settle_duration_ms", row.metrics.settle_duration_ms)}`,
  ].join("，");

  return (
    <button
      aria-label={accessibleLabel}
      className={styles.switchRow}
      data-slow={slow || undefined}
      disabled={!bounds}
      onClick={() => bounds && onSelectTime(bounds[0])}
      type="button"
    >
      <span className={styles.switchIdx}>#{index + 1}</span>
      <span className={styles.switchBar} aria-hidden="true">
        <span className={styles.switchTrack} />
        {Number.isFinite(kill) ? <span className={styles.switchDot} style={{ insetInlineStart: `${((kill ?? 0) / total) * 100}%` }} /> : null}
        {Number.isFinite(transition) && Number.isFinite(acquire) ? (
          <span
            className={styles.switchMove}
            style={{
              insetInlineStart: `${((transition ?? 0) / total) * 100}%`,
              width: `${Math.max(1, (((acquire ?? 0) - (transition ?? 0)) / total) * 100)}%`,
            }}
          />
        ) : null}
        {Number.isFinite(acquire) && Number.isFinite(settle) ? (
          <span
            className={styles.switchSettle}
            style={{
              insetInlineStart: `${((acquire ?? 0) / total) * 100}%`,
              width: `${Math.max(1, (((settle ?? 0) - (acquire ?? 0)) / total) * 100)}%`,
            }}
          />
        ) : null}
        {Number.isFinite(transition) ? <span className={styles.switchTick} style={{ insetInlineStart: `${((transition ?? 0) / total) * 100}%` }} /> : null}
        {Number.isFinite(acquire) ? <span className={styles.switchTick} style={{ insetInlineStart: `${((acquire ?? 0) / total) * 100}%` }} /> : null}
        {Number.isFinite(settle) ? <span className={styles.switchTick} style={{ insetInlineStart: `${((settle ?? 0) / total) * 100}%` }} /> : null}
      </span>
      <span className={styles.switchNum}>{familyMetricText("transition_time_ms", row.metrics.transition_time_ms)}</span>
      <span className={styles.switchNum}>{familyMetricText("transition_distance_px", row.metrics.transition_distance_px)}</span>
      <span className={styles.switchNum}>{familyMetricText("path_efficiency", row.metrics.path_efficiency)}</span>
      <span className={styles.switchNum}>{Number.isFinite(row.metrics.settle_duration_ms) ? familyMetricText("settle_duration_ms", row.metrics.settle_duration_ms) : "—"}</span>
    </button>
  );
}

function SwitchingDataView({
  data,
  familyData,
  loadingFamily,
  loadingMoreFamily,
  onLoadMoreFamily,
  onSelectTime,
  presentation,
  onSelectMetric,
}: {
  data: FrontendAnalysisDataV1 | null;
  familyData: FrontendAnalysisFamilyDataV1 | null;
  loadingFamily: boolean;
  loadingMoreFamily: boolean;
  onLoadMoreFamily: () => void;
  onSelectTime: (timeMs: number) => void;
  onSelectMetric: (metric: string) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const rows = familyData?.rows ?? [];
  const slowRowIndex = rows.reduce((acc, row, index) => {
    if (row.kind !== "switch_chain") return acc;
    if (acc === -1) return index;
    const current = rows[acc].metrics.path_efficiency ?? Infinity;
    const candidate = row.metrics.path_efficiency ?? Infinity;
    return candidate < current ? index : acc;
  }, -1);
  const goodRowIndex = rows.reduce((acc, row, index) => {
    if (row.kind !== "switch_chain") return acc;
    if (acc === -1) return index;
    const current = rows[acc].metrics.path_efficiency ?? 0;
    const candidate = row.metrics.path_efficiency ?? 0;
    return candidate > current ? index : acc;
  }, -1);
  const transitionTimes = rows
    .filter((row) => row.kind === "switch_chain")
    .map((row) => row.metrics.transition_time_ms)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const medianTransition = transitionTimes.length ? transitionTimes[Math.floor(transitionTimes.length / 2)] : null;

  return (
    <div className={styles.familyDataLayout} data-family="switching">
      <div className={styles.metricsColumn}>
        <div className={styles.sectionHead}>
          <span className={styles.sectionTitle}>指标总览</span>
          <span className={styles.sectionHint}>切换专项</span>
        </div>
        <MetricOverviewPanel familyCode="target_switching" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        <Notice tone="warning">
          当前证据<b>不能</b>判断：目标选择、第一枪、首次伤害、持续目标身份与重新进入——界面不展示也不暗示这些结论。
        </Notice>
        <div className={styles.boundaryPanel}>
          <div className={styles.boundaryTitle}>事件命名</div>
          <dl className={styles.boundaryKv}>
            <dt>本次击杀</dt><dd>kill</dd>
            <dt>一次完整切换</dt><dd>switch_chain</dd>
            <dt>开始切换</dt><dd>transition</dd>
            <dt>到达新目标</dt><dd>next_target_acquired</dd>
            <dt>稳定完成</dt><dd>settle</dd>
          </dl>
        </div>
      </div>
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">切换链</h2>
          <span className={styles.sectionCount}>{familyData?.total_count ?? rows.length} 次完整切换</span>
          <span className={styles.sectionHint}>点击行跳到视频对应时间</span>
        </div>
          {slowRowIndex >= 0 || goodRowIndex >= 0 ? (
          <div className={styles.familyHighlights}>
            {slowRowIndex >= 0 ? <Badge tone="info">表现较慢的切换 · #{slowRowIndex + 1}</Badge> : null}
            {goodRowIndex >= 0 ? <Badge tone="info">较好对照 · #{goodRowIndex + 1}</Badge> : null}
          </div>
        ) : null}
        {loadingFamily ? <Loading>正在读取逐行动作数据</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title="切换链数据暂不可用">本次证据没有可安全展示的逐行动作记录。</Notice>
        ) : null}
        {rows.length ? (
          <div className={styles.switchChainPanel}>
            {rows.map((row, index) =>
              row.kind === "switch_chain" ? (
                <SwitchChainRow index={index} key={`${row.kind}-${index}`} onSelectTime={onSelectTime} row={row} />
              ) : null,
            )}
          </div>
        ) : null}
        {familyData && familyData.next_offset !== null ? (
          <Button disabled={loadingMoreFamily} onClick={onLoadMoreFamily} variant="secondary">
            {loadingMoreFamily ? "正在加载" : `加载更多（已显示 ${rows.length} / ${familyData.total_count}）`}
          </Button>
        ) : null}
        <div className={styles.switchLegend}>
          <span><span className={styles.switchLegendDot} />本次击杀</span>
          <span><span className={styles.switchLegendMove} />开始切换 → 到达新目标</span>
          <span><span className={styles.switchLegendSettle} />到达 → 稳定完成</span>
        </div>
        <div className={styles.chartCard}>
          <p className={styles.chartCap}>
            文本摘要：{rows.length} 次完整切换{medianTransition !== null ? `的中位耗时 ${familyMetricText("transition_time_ms", medianTransition)}` : ""}
            {slowRowIndex >= 0 ? `；#${slowRowIndex + 1} 明显偏慢，建议优先在视频回看` : ""}
            {goodRowIndex >= 0 ? `；#${goodRowIndex + 1} 是较好的对照` : ""}。
          </p>
        </div>
      </div>
    </div>
  );
}

function TrackingDataView({
  data,
  familyData,
  loadingFamily,
  onSelectTime,
  presentation,
  onSelectMetric,
}: {
  data: FrontendAnalysisDataV1 | null;
  familyData: FrontendAnalysisFamilyDataV1 | null;
  loadingFamily: boolean;
  onSelectTime: (timeMs: number) => void;
  onSelectMetric: (metric: string) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const radiusPoints = data?.target_relative_error_radius.points ?? [];
  const peakRadius = Math.max(0, ...radiusPoints.map((point) => point.normalized_error_radius));
  const lossRows = familyData?.rows.filter((row) => row.kind === "tracking_loss") ?? [];
  const reacqRows = familyData?.rows.filter((row) => row.kind === "tracking_reacquisition") ?? [];
  const timelineMax = Math.max(1, ...lossRows.concat(reacqRows).flatMap((row) => Object.values(row.timing)));
  const hasFormalMetrics = presentation.metrics.formal.length > 0;

  const longestLoss = lossRows.reduce<{ row: FrontendAnalysisFamilyDataRowV1 | null; duration: number }>(
    (acc, row) => {
      const bounds = rowBounds(row);
      const duration = bounds ? bounds[1] - bounds[0] : 0;
      return duration > acc.duration ? { row, duration } : acc;
    },
    { row: null, duration: 0 },
  );
  const slowestReacq = reacqRows.reduce<{ row: FrontendAnalysisFamilyDataRowV1 | null; duration: number }>(
    (acc, row) => {
      const bounds = rowBounds(row);
      const duration = bounds ? bounds[1] - bounds[0] : 0;
      return duration > acc.duration ? { row, duration } : acc;
    },
    { row: null, duration: 0 },
  );

  const links: { label: string; kindLabel: string; seq: number; start: number; end: number }[] = [];
  if (longestLoss.row) {
    const bounds = rowBounds(longestLoss.row);
    if (bounds) links.push({ label: "表现较差的偏离", kindLabel: "偏离", seq: lossRows.indexOf(longestLoss.row) + 1, start: bounds[0], end: bounds[1] });
  }
  if (slowestReacq.row) {
    const bounds = rowBounds(slowestReacq.row);
    if (bounds) links.push({ label: "重新捕获较慢", kindLabel: "重新捕获", seq: reacqRows.indexOf(slowestReacq.row) + 1, start: bounds[0], end: bounds[1] });
  }

  return (
    <div className={styles.familyDataLayout} data-family="tracking" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>指标总览</span>
            <span className={styles.sectionHint}>按理解目的分组</span>
          </div>
          <MetricOverviewPanel familyCode="continuous_tracking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">跟踪分段</h2>
          <span className={styles.sectionCount}>{familyData?.total_count ?? familyData?.rows.length ?? 0} 条记录</span>
        </div>
        <div className={styles.chartGrid}>
          <div
            aria-label={`目标相对误差半径分布，共 ${radiusPoints.length} 个样本，峰值 ${Number(peakRadius.toFixed(2))}`}
            className={styles.chartCard}
            role="img"
          >
            <div className={styles.chartTitle}>
              目标相对误差半径分布
              <Badge tone="neutral" style={{ marginInlineStart: "auto" }}>已归一化</Badge>
            </div>
            {radiusPoints.length ? (
              <>
                <div aria-hidden="true" className={styles.errorSeries} role="presentation">
                  {Array.from({ length: 20 }).map((_, index) => {
                    const binMin = (index / 20) * peakRadius * 1.1;
                    const binMax = ((index + 1) / 20) * peakRadius * 1.1;
                    const count = radiusPoints.filter((p) => p.normalized_error_radius >= binMin && p.normalized_error_radius < binMax).length;
                    const height = Math.max(2, (count / Math.max(1, radiusPoints.length / 8)) * 100);
                    return <i key={index} style={{ height: `${Math.min(100, height)}%` }} />;
                  })}
                </div>
                <div className={styles.errorSeriesAxis}><span>0.0</span><span>{Number(peakRadius.toFixed(2))}</span></div>
              </>
            ) : (
              <p className={styles.chartCap}>目标相对误差样本不可用。</p>
            )}
            <p className={styles.chartCap}>
              {`按目标半径归一化后的偏差分布。共 ${radiusPoints.length} 个样本，峰值 ${Number(peakRadius.toFixed(2))}。数值已在本地按目标半径归一化并量化；页面不接收位置或半径坐标。`}
            </p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>偏离与重新捕获时序</div>
            {lossRows.length || reacqRows.length ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 100">
                <line opacity="0.3" stroke="var(--outline-variant)" strokeWidth="1" x1="20" x2="340" y1="50" y2="50" />
                {lossRows.map((row, index) => {
                  const bounds = rowBounds(row);
                  if (!bounds) return null;
                  const left = 20 + (bounds[0] / timelineMax) * 320;
                  const width = Math.max(4, ((bounds[1] - bounds[0]) / timelineMax) * 320);
                  return <rect key={`loss-${index}`} fill="var(--primary)" height="16" opacity="0.6" width={width} x={left} y="42" />;
                })}
                {reacqRows.map((row, index) => {
                  const bounds = rowBounds(row);
                  if (!bounds) return null;
                  const left = 20 + (bounds[0] / timelineMax) * 320;
                  const width = Math.max(4, ((bounds[1] - bounds[0]) / timelineMax) * 320);
                  return <line key={`reacq-${index}`} stroke="var(--tertiary)" strokeWidth="2" x1={left} x2={left + width} y1="70" y2="70" />;
                })}
                <rect fill="var(--primary)" height="10" opacity="0.6" width="10" x="20" y="84" />
                <text fill="var(--on-surface)" fontSize="10" x="34" y="93">偏离（宽度=持续时间）</text>
                <line stroke="var(--tertiary)" strokeWidth="2" x1="160" x2="175" y1="89" y2="89" />
                <text fill="var(--on-surface)" fontSize="10" x="180" y="93">重新捕获延迟</text>
              </svg>
            ) : (
              <p className={styles.chartCap}>本次没有可定位的偏离/重新捕获事件。</p>
            )}
            <p className={styles.chartCap}>
              {lossRows.length} 次偏离事件，{reacqRows.length} 次重新捕获记录。
            </p>
          </div>
        </div>

        {links.length && presentation.video.kind === "seekable" ? (
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>视频联动入口</div>
            <p className={styles.chartCap}>点击跳到 Video 视图对应时间：</p>
            <div className={styles.videoLinks}>
              {links.map((link) => (
                <div className={styles.videoLinkRow} key={link.label}>
                  <span>{link.label}</span>
                  <Button onClick={() => onSelectTime(link.start)} size="compact" variant="ghost">
                    {formatRelativeTime(link.start)} – {formatRelativeTime(link.end)} · {link.kindLabel} #{link.seq}
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className={styles.boundaryPanel}>
          <div className={styles.boundaryTitle}>分析边界</div>
          <dl className={styles.boundaryKv}>
            <dt>测量范围</dt><dd>目标偏差、偏离/重新捕获、运动平滑度（需目标坐标和移动轨迹）。</dd>
            <dt>重新捕获延迟</dt><dd>包含系统延迟和认知延迟，无法分离具体响应来源。</dd>
            <dt>不可用</dt><dd>频率域指标（phase lag / velocity gain / coherence）、理想路径对比（需视觉坐标系轨迹）。</dd>
          </dl>
        </div>

        {loadingFamily ? <Loading>正在读取逐行动作数据</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title="跟踪分段数据暂不可用">通用指标仍然可用；本次没有可安全展示的逐行动作记录。</Notice>
        ) : null}
      </div>
    </div>
  );
}

function FlickingDataView({
  data,
  familyData,
  loadingFamily,
  onSelectTime,
  presentation,
  onSelectMetric,
}: {
  data: FrontendAnalysisDataV1 | null;
  familyData: FrontendAnalysisFamilyDataV1 | null;
  loadingFamily: boolean;
  onSelectTime: (timeMs: number) => void;
  onSelectMetric: (metric: string) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const rows = familyData?.rows.filter((row) => row.kind === "static_flick") ?? [];
  const efficiencies = rows.map((row) => row.metrics.path_efficiency).filter(Number.isFinite);
  const medianEff = efficiencies.length ? efficiencies.sort((a, b) => a - b)[Math.floor(efficiencies.length / 2)] : null;
  const bestRow = rows.reduce<{ row: FrontendAnalysisFamilyDataRowV1 | null }>(
    (acc, row) => ((row.metrics.path_efficiency ?? -1) > (acc.row?.metrics.path_efficiency ?? -1) ? { row } : acc),
    { row: null },
  ).row;
  const slowRow = rows.reduce<{ row: FrontendAnalysisFamilyDataRowV1 | null }>(
    (acc, row) => ((row.metrics.path_efficiency ?? Infinity) < (acc.row?.metrics.path_efficiency ?? Infinity) ? { row } : acc),
    { row: null },
  ).row;
  const flickLinks: { label: string; kindLabel: string; seq: number; start: number; end: number }[] = [];
  if (bestRow) {
    const bounds = rowBounds(bestRow);
    if (bounds) flickLinks.push({ label: "表现较好", kindLabel: "Flick", seq: rows.indexOf(bestRow) + 1, start: bounds[0], end: bounds[1] });
  }
  if (slowRow && slowRow !== bestRow) {
    const bounds = rowBounds(slowRow);
    if (bounds) flickLinks.push({ label: "表现较慢", kindLabel: "Flick", seq: rows.indexOf(slowRow) + 1, start: bounds[0], end: bounds[1] });
  }

  function phaseDurations(rowsArg: FrontendAnalysisFamilyDataRowV1[]): { accel: number; decel: number; settle: number } | null {
    const accel = rowsArg
      .map((row) => (row.timing.peak_ms ?? NaN) - (row.timing.start_ms ?? NaN))
      .filter(Number.isFinite);
    const decel = rowsArg
      .map((row) => (row.timing.movement_end_ms ?? NaN) - (row.timing.peak_ms ?? NaN))
      .filter(Number.isFinite);
    const settle = rowsArg
      .map((row) => (row.timing.settle_end_ms ?? NaN) - (row.timing.movement_end_ms ?? NaN))
      .filter(Number.isFinite);
    if (!accel.length || !decel.length) return null;
    const median = (values: number[]) => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
    return { accel: median(accel), decel: median(decel), settle: median(settle) };
  }

  const phases = phaseDurations(rows);
  const totalPhase = phases ? phases.accel + phases.decel + phases.settle : 0;
  const hasFormalMetrics = presentation.metrics.formal.length > 0;

  return (
    <div className={styles.familyDataLayout} data-family="flicking" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>指标总览</span>
            <span className={styles.sectionHint}>按理解目的分组</span>
          </div>
          <MetricOverviewPanel familyCode="static_clicking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">逐次 Flick</h2>
          <span className={styles.sectionCount}>{familyData?.total_count ?? rows.length} 次记录</span>
        </div>
        <div className={styles.chartGrid}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>时序分布</div>
            {phases && totalPhase > 0 ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 90">
                <rect fill="var(--tertiary)" height="40" opacity="0.75" width={(phases.accel / totalPhase) * 320} x="20" y="20" />
                <rect fill="var(--primary)" height="40" opacity="0.75" width={(phases.decel / totalPhase) * 320} x={20 + (phases.accel / totalPhase) * 320} y="20" />
                <rect fill="var(--on-surface-variant)" height="40" opacity="0.75" width={(phases.settle / totalPhase) * 320} x={20 + ((phases.accel + phases.decel) / totalPhase) * 320} y="20" />
                <text fill="var(--on-tertiary)" fontSize="12" fontWeight="600" textAnchor="middle" x={20 + (phases.accel / totalPhase) * 160} y="45">{Math.round((phases.accel / totalPhase) * 100)}%</text>
                <text fill="var(--on-primary)" fontSize="12" fontWeight="600" textAnchor="middle" x={20 + (phases.accel / totalPhase) * 320 + (phases.decel / totalPhase) * 160} y="45">{Math.round((phases.decel / totalPhase) * 100)}%</text>
                <rect fill="var(--tertiary)" height="8" opacity="0.75" width="8" x="20" y="72" />
                <text fill="var(--on-surface)" fontSize="10" x="32" y="79">加速 {familyMetricText("accel_duration_ms", phases.accel)}</text>
                <rect fill="var(--primary)" height="8" opacity="0.75" width="8" x="110" y="72" />
                <text fill="var(--on-surface)" fontSize="10" x="122" y="79">减速 {familyMetricText("decel_duration_ms", phases.decel)}</text>
                <rect fill="var(--on-surface-variant)" height="8" opacity="0.75" width="8" x="220" y="72" />
                <text fill="var(--on-surface)" fontSize="10" x="232" y="79">稳定 {familyMetricText("settle_duration_ms", phases.settle)}</text>
              </svg>
            ) : (
              <p className={styles.chartCap}>阶段时序样本不足。</p>
            )}
            <p className={styles.chartCap}>移动持续时间的阶段分解（中位数）。减速阶段占比超过一半表示减速控制是关键。</p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>路径质量分布</div>
            {efficiencies.length ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 110">
                {Array.from({ length: 10 }).map((_, index) => {
                  const binMin = 0.6 + index * 0.04;
                  const binMax = 0.6 + (index + 1) * 0.04;
                  const count = efficiencies.filter((value) => value >= binMin && value < binMax).length;
                  const height = Math.max(4, (count / Math.max(1, efficiencies.length / 5)) * 90);
                  return <rect key={index} x={20 + index * 32} y={100 - height} width="26" height={height} fill="var(--tertiary)" opacity="0.7" />;
                })}
                <text fill="var(--on-surface-variant)" fontSize="9" textAnchor="start" x="20" y="108">60%</text>
                <text fill="var(--on-surface-variant)" fontSize="9" textAnchor="end" x="340" y="108">100%</text>
                {medianEff !== null ? (
                  <>
                    <line stroke="var(--primary)" strokeDasharray="3 2" strokeWidth="1.5" x1={20 + ((medianEff - 0.6) / 0.4) * 320} x2={20 + ((medianEff - 0.6) / 0.4) * 320} y1="10" y2="100" />
                    <text fill="var(--primary)" fontSize="10" x={24 + ((medianEff - 0.6) / 0.4) * 320} y="16">{Number((medianEff * 100).toFixed(0))}%</text>
                  </>
                ) : null}
              </svg>
            ) : (
              <p className={styles.chartCap}>路径效率样本不足。</p>
            )}
            <p className={styles.chartCap}>
              {rows.length} 次 Flick 的路径效率分布。{medianEff !== null ? `中位数 ${Number((medianEff * 100).toFixed(0))}%（橙色虚线）。` : ""}
            </p>
          </div>
        </div>

        {flickLinks.length && presentation.video.kind === "seekable" ? (
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>视频联动入口</div>
            <p className={styles.chartCap}>点击跳到 Video 视图对应时间：</p>
            <div className={styles.videoLinks}>
              {flickLinks.map((link) => (
                <div className={styles.videoLinkRow} key={link.label}>
                  <span>{link.label}</span>
                  <Button onClick={() => onSelectTime(link.start)} size="compact" variant="ghost">
                    {formatRelativeTime(link.start)} – {formatRelativeTime(link.end)} · {link.kindLabel} #{link.seq}
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className={styles.boundaryPanel}>
          <div className={styles.boundaryTitle}>分析边界</div>
          <dl className={styles.boundaryKv}>
            <dt>测量范围</dt><dd>仅 raw input（timestamp, dx, dy），不推断目标位置。</dd>
            <dt>坐标系</dt><dd>dx/dy 是 mouse counts，需 DPI/sens 校准才能转物理距离。</dd>
            <dt>不可用</dt><dd>目标误差、视觉路径对比（需视觉坐标系轨迹）。</dd>
          </dl>
        </div>

        {loadingFamily ? <Loading>正在读取逐次 Flick 数据</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title="逐次 Flick 数据暂不可用">通用指标仍然可用；本次没有可安全展示的逐行动作记录。</Notice>
        ) : null}
      </div>
    </div>
  );
}

function GenericDataView({
  data,
  familyData,
  loadingFamily,
  onSelectTime,
  presentation,
  onSelectMetric,
}: {
  data: FrontendAnalysisDataV1 | null;
  familyData: FrontendAnalysisFamilyDataV1 | null;
  loadingFamily: boolean;
  onSelectTime: (timeMs: number) => void;
  onSelectMetric: (metric: string) => void;
  presentation: AnalysisWorkspacePresentation;
}) {
  const radiusPoints = data?.target_relative_error_radius.points ?? [];
  const peakRadius = Math.max(0, ...radiusPoints.map((point) => point.normalized_error_radius));
  const maxEventCount = Math.max(1, ...(data?.event_distribution.map((item) => item.count) ?? []));
  const markersByKind = useMemo(() => {
    const markers = new Map<string, number>();
    for (const marker of data?.event_markers ?? []) markers.set(marker.kind, marker.relative_ms);
    return markers;
  }, [data?.event_markers]);
  const hasFormalMetrics = presentation.metrics.formal.length > 0;

  return (
    <div className={styles.familyDataLayout} data-family="generic" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>指标总览</span>
            <span className={styles.sectionHint}>按理解目的分组</span>
          </div>
          <MetricOverviewPanel familyCode="static_clicking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.chartGrid}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle} id="family-detail-title">事件分布</div>
            {data?.event_distribution.length ? (
              <div className={styles.distributionPlot} role="group">
                {data.event_distribution.map(({ kind, count }) => {
                  const relativeMs = markersByKind.get(kind);
                  return (
                    <button
                      className={styles.distributionBar}
                      disabled={relativeMs === undefined}
                      key={kind}
                      onClick={() => relativeMs !== undefined && onSelectTime(relativeMs)}
                      type="button"
                    >
                      <span>{EVENT_KIND_LABELS[kind] ?? kind}</span>
                      <i style={{ width: `${(count / maxEventCount) * 100}%` }} />
                      <strong>{count}</strong>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className={styles.chartCap}>当前没有可安全公开的事件 marker。</p>
            )}
            <p className={styles.chartCap}>
              {data?.event_distribution.length ? `已验证事件共 ${data.event_distribution.reduce((sum, item) => sum + item.count, 0)} 个。` : "没有足够事件生成分布摘要。"}
            </p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>目标相对误差半径分布</div>
            {radiusPoints.length ? (
              <div aria-hidden="true" className={styles.errorSeries} role="presentation">
                {Array.from({ length: 20 }).map((_, index) => {
                  const binMin = (index / 20) * peakRadius * 1.1;
                  const binMax = ((index + 1) / 20) * peakRadius * 1.1;
                  const count = radiusPoints.filter((p) => p.normalized_error_radius >= binMin && p.normalized_error_radius < binMax).length;
                  const height = Math.max(2, (count / Math.max(1, radiusPoints.length / 8)) * 100);
                  return <i key={index} style={{ height: `${Math.min(100, height)}%` }} />;
                })}
              </div>
            ) : (
              <p className={styles.chartCap}>目标相对误差样本不可用。</p>
            )}
            <p className={styles.chartCap}>按目标半径归一化后的偏差分布；页面不接收位置或半径坐标。</p>
          </div>
        </div>

        {loadingFamily ? <Loading>正在读取逐行动作数据</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title="专项动作数据暂不可用">通用指标仍然可用。</Notice>
        ) : null}
      </div>
    </div>
  );
}

export function DataView({
  onSelectMetric,
  onSelectTime,
  presentation,
  selectedMetric,
}: {
  onSelectMetric: (metric: string) => void;
  onSelectTime: (timeMs: number) => void;
  presentation: AnalysisWorkspacePresentation;
  selectedMetric: string | null;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<FrontendAnalysisDataV1 | null>(null);
  const [loadingData, setLoadingData] = useState(true);
  const [dataUnavailable, setDataUnavailable] = useState(false);
  const [familyData, setFamilyData] = useState<FrontendAnalysisFamilyDataV1 | null>(null);
  const [loadingFamily, setLoadingFamily] = useState(false);
  const [loadingMoreFamily, setLoadingMoreFamily] = useState(false);
  const [familyUnavailable, setFamilyUnavailable] = useState(false);
  const wantsFamilyDetail = true;

  useEffect(() => {
    let active = true;
    setLoadingData(true);
    setDataUnavailable(false);
    void getAnalysisData(presentation.analysisId)
      .then((next) => {
        if (active) setData(next);
      })
      .catch(() => {
        if (active) setDataUnavailable(true);
      })
      .finally(() => {
        if (active) setLoadingData(false);
      });
    return () => {
      active = false;
    };
  }, [presentation.analysisId]);

  useEffect(() => {
    let active = true;
    setFamilyData(null);
    setFamilyUnavailable(false);
    if (!wantsFamilyDetail) {
      setLoadingFamily(false);
      return () => {
        active = false;
      };
    }
    setLoadingFamily(true);
    void getAnalysisFamilyData(presentation.analysisId)
      .then((next) => {
        if (active && next.schema_version === "frontend_analysis_family_data.v1") setFamilyData(next);
      })
      .catch(() => {
        if (active) setFamilyUnavailable(true);
      })
      .finally(() => {
        if (active) setLoadingFamily(false);
      });
    return () => {
      active = false;
    };
  }, [presentation.analysisId]);

  const loadMoreFamily = async () => {
    const offset = familyData?.next_offset;
    if (offset === null || offset === undefined || loadingMoreFamily) return;
    setLoadingMoreFamily(true);
    try {
      const next = await getAnalysisFamilyData(presentation.analysisId, { offset });
      setFamilyData((current) => {
        if (!current || current.analysis_ref !== next.analysis_ref || current.family !== next.family) return current;
        return { ...next, rows: [...current.rows, ...next.rows] };
      });
    } catch {
      setFamilyUnavailable(true);
    } finally {
      setLoadingMoreFamily(false);
    }
  };

  const sharedLimitations = useMemo(
    () => unique([
      ...presentation.limitations,
      ...(data?.limitations ?? []),
      ...presentation.metrics.formal.flatMap((metric) => metric.limitations),
      ...presentation.metrics.limited.flatMap((metric) => metric.limitations),
    ]),
    [data?.limitations, presentation.limitations, presentation.metrics.formal, presentation.metrics.limited],
  );
  const sharedLimitationLabels = useMemo(
    () => sharedLimitations.map(limitationLabel),
    [sharedLimitations],
  );
  const availableLimited = useMemo(
    () => presentation.metrics.limited.filter((metric) => metric.value !== null && metric.availability !== "unavailable"),
    [presentation.metrics.limited],
  );
  const unavailableMetrics = useMemo(
    () => [
      ...presentation.metrics.formal.filter((metric) => metric.value === null || metric.availability === "unavailable"),
      ...presentation.metrics.limited.filter((metric) => metric.value === null || metric.availability === "unavailable"),
    ],
    [presentation.metrics.formal, presentation.metrics.limited],
  );

  useEffect(() => {
    if (!selectedMetric) return;
    const row = Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-metric-label]") ?? [])
      .find((item) => item.dataset.metricLabel === selectedMetric);
    row?.scrollIntoView({ block: "center" });
    row?.focus();
  }, [selectedMetric]);

  const viewProps = {
    data,
    familyData,
    loadingFamily,
    loadingMoreFamily,
    onLoadMoreFamily: () => void loadMoreFamily(),
    onSelectTime,
    onSelectMetric,
    presentation,
  };

  const familyView = familyData?.family ?? "unsupported";

  return (
    <div className={styles.dataView} ref={rootRef}>
      {loadingData ? <Loading>正在读取安全数据投影</Loading> : null}
      {dataUnavailable ? <Notice tone="warning" title="Analysis Data 当前不可用">页面不会用 Session 结果或历史趋势填补这部分数据。</Notice> : null}
      {!loadingData && !dataUnavailable ? (
        familyView === "switching" ? <SwitchingDataView {...viewProps} /> :
        familyView === "tracking" ? <TrackingDataView {...viewProps} /> :
        familyView === "flicking" ? <FlickingDataView {...viewProps} /> :
        <GenericDataView {...viewProps} />
      ) : null}

      {familyUnavailable ? <Notice tone="warning" title="专项动作数据读取失败">通用指标仍然可用；这部分不会用示例数据填补。</Notice> : null}

      {availableLimited.length ? (
        <section className={styles.limitedMetrics} aria-labelledby="limited-metrics-title">
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle} id="limited-metrics-title">实验性或受限指标</h2>
            <Badge tone="warning">不用于正式结论</Badge>
          </div>
          <div className={styles.metricOverviewPanel}>
            {availableLimited.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metricReference(metric))}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(ref) ?? availabilityLabel(metric.availability)}</span>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {unavailableMetrics.length ? (
        <details className={styles.unavailableMetrics}>
          <summary>不可用指标（{unavailableMetrics.length} 项）与原因——不补假数据</summary>
          {unavailableMetrics.map((metric) => (
            <div className={styles.metricRow} data-metric-label={metricReference(metric)} key={metricReference(metric)}>
              <span className={styles.metricKey}>{metricLabel(metricReference(metric))}</span>
              <span className={styles.metricValue}>不可用</span>
              <span className={styles.metricPlain}>
                {metric.limitations.map(limitationLabel).join("；") || "本次证据不足以安全计算"}
                {" · 来源："}{metricSourceText(metric)}
              </span>
            </div>
          ))}
        </details>
      ) : null}

      {sharedLimitationLabels.length ? (
        <section className={styles.analysisLimitations}>
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>Analysis 范围限制</h2>
          </div>
          <ul>{sharedLimitationLabels.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}
