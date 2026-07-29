"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisData } from "@/lib/api";
import type { AnalysisMetricPresentation, AnalysisWorkspacePresentation } from "@/lib/contracts";
import type { FrontendAnalysisDataV1 } from "@/lib/types";
import { Badge, Empty, Loading, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

const METRIC_LABELS: Record<string, string> = {
  sparc: "运动平滑度（SPARC）",
  visual_validation: "视觉验证状态",
  "continuous_tracking.target_relative_error_px": "目标偏差",
  "continuous_tracking.time_in_radius_ratio": "目标范围内时间占比",
  "continuous_tracking.loss_count": "偏离目标次数",
  "continuous_tracking.loss_duration_ms": "偏离目标持续时间",
  "continuous_tracking.reacquisition_latency_ms": "重新跟上耗时",
  "continuous_tracking.relative_lag_ms": "相对滞后",
  "continuous_tracking.phase_lag_ms": "相位滞后",
  "continuous_tracking.coherence": "运动一致性",
  "continuous_tracking.velocity_gain": "速度跟随比例",
  "continuous_tracking.alignment_latency_ms": "采集对齐延迟",
  "continuous_tracking.observed_change_response_ms": "观测到的变向响应",
  "continuous_tracking.human_response_latency_ms": "人的反应延迟",
  "continuous_tracking.correction_direction_reversal_count": "修正方向反转次数",
  "continuous_tracking.smoothness_acceleration_rms": "加速度波动",
  "continuous_tracking.sparc": "运动平滑度（SPARC）",
  "target_switching.transition_time_ms": "切换耗时",
  "target_switching.transition_distance_px": "切换距离",
  "target_switching.path_efficiency": "路径效率",
  "target_switching.settle_duration_ms": "稳定耗时",
};

function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

function metricReference(metric: AnalysisMetricPresentation): string {
  return metric.referenceKey ?? metric.key;
}

function valueText(metric: AnalysisMetricPresentation): string {
  if (metric.value === null) return "不可用";
  const value = typeof metric.value === "number" ? Number(metric.value.toFixed(3)) : metric.value;
  const referenceKey = metricReference(metric);
  if (
    (referenceKey.endsWith("time_in_radius_ratio")
      || referenceKey === "target_switching.path_efficiency")
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

function sourceLabel(metric: AnalysisMetricPresentation): string {
  const labels = metric.sources.map((source) => {
    if (source.includes("tracking-analysis")) return "本地视觉跟踪";
    if (source.includes("raw") || source.includes("input")) return "Raw Input";
    if (source.includes("performance")) return "Performance";
    if (source.includes("stats")) return "Stats";
    if (source.includes("video") || source.includes("mp4") || source.includes("visual")) return "本地视频分析";
    return "本地分析证据";
  });
  return labels.length ? Array.from(new Set(labels)).join(" + ") : "来源未提供";
}

function availabilityLabel(availability: string): string {
  if (availability === "available") return "可用";
  if (availability === "limited") return "受限";
  return "暂不可用";
}

function metricGroup(metric: AnalysisMetricPresentation): string {
  if (metric.sources.some((source) => source.includes("raw") || source.includes("input"))) return "输入运动学";
  if (metric.sources.some((source) => source.includes("video") || source.includes("mp4"))) return "视觉验证";
  return "结果与质量";
}

const EVENT_LABELS: Record<string, string> = {
  target_available: "目标出现",
  target_change_point: "目标变向",
  tracking_loss: "跟踪丢失",
  tracking_reacquisition: "重新跟上",
  tracking_fixed_window: "固定跟踪窗口",
  tracking_episode: "跟踪片段",
  low_confidence: "低可信度观测",
  tracking_change_response: "变向响应",
  hit: "命中",
  shot: "开火",
  kill: "击杀",
  switch_chain: "目标切换链",
  transition: "开始切换",
  next_target_acquired: "到达下一目标",
  settle: "稳定完成",
};

const LIMITATION_LABELS: Record<string, string> = {
  "Exact scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Occlusion re-entry was not observed; no re-entry claim is enabled.": "本局未观察到遮挡后重新出现，因此不判断重新捕获能力。",
  "The reviewed legacy black-ball detector initializes one CSRT identity.": "视觉跟踪仅维护一个目标身份。",
  "Unknown or multi-target scenarios remain fail-closed.": "未知场景或多目标布局不生成此类结论。",
  "Unknown hashes and concurrent target layouts are not classified by this entry.": "未知场景或多目标布局不生成此类结论。",
  alignment_latency_reported_separately: "对齐延迟单独报告，不等同于跟随滞后。",
  capture_alignment_descriptor_not_human_response: "这是采集对齐描述，不代表人的反应时间。",
  descriptive_correction_burden: "仅描述修正负担，不作为机制结论。",
  descriptive_smoothness_not_a_mechanism: "平滑度只作描述，不解释成因。",
  not_inferred_from_capture_alignment_or_tracking_samples: "当前证据不足以推断人的反应延迟。",
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

function limitationLabel(limitation: string): string {
  return LIMITATION_LABELS[limitation] ?? "当前分析存在未展开的技术限制";
}

function eventLabel(kind: string): string {
  return EVENT_LABELS[kind] ?? "已记录事件";
}

function unique(items: readonly string[]): string[] {
  return Array.from(new Set(items));
}

function limitationLabels(items: readonly string[]): string[] {
  return unique(items.map(limitationLabel));
}

function MetricRow({
  metric,
  limitations,
}: {
  metric: AnalysisMetricPresentation;
  limitations: string[];
}) {
  return (
    <article
      className={styles.metricRow}
      data-metric={metricReference(metric)}
      data-metric-label={metric.key}
      tabIndex={-1}
    >
      <div><strong>{metricLabel(metricReference(metric))}</strong><span>{sourceLabel(metric)}</span></div>
      <div className={styles.metricValue}><strong>{valueText(metric)}</strong><span>{availabilityLabel(metric.availability)}</span></div>
      <dl>
        <div><dt>覆盖</dt><dd>{metric.coverage === null ? "未知" : `${Math.round(metric.coverage * 100)}%`}</dd></div>
      </dl>
      {limitations.length ? <p className={styles.metricLimitations}>限制：{limitations.join("；")}</p> : null}
    </article>
  );
}

export function DataView({
  onSelectTime,
  presentation,
  selectedMetric,
}: {
  onSelectTime: (timeMs: number) => void;
  presentation: AnalysisWorkspacePresentation;
  selectedMetric: string | null;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<FrontendAnalysisDataV1 | null>(null);
  const [loadingData, setLoadingData] = useState(true);
  const [dataUnavailable, setDataUnavailable] = useState(false);

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

  const sharedLimitations = useMemo(
    () => unique([...presentation.limitations, ...(data?.limitations ?? [])]),
    [data?.limitations, presentation.limitations],
  );
  const sharedLimitationLabels = useMemo(
    () => limitationLabels(sharedLimitations),
    [sharedLimitations],
  );
  const grouped = useMemo(() => {
    const rows = new Map<string, AnalysisMetricPresentation[]>();
    for (const metric of presentation.metrics.formal) {
      const group = metricGroup(metric);
      rows.set(group, [...(rows.get(group) ?? []), metric]);
    }
    return Array.from(rows.entries());
  }, [presentation.metrics.formal]);
  const availableLimited = useMemo(
    () => presentation.metrics.limited.filter((metric) => metric.value !== null && metric.availability !== "unavailable"),
    [presentation.metrics.limited],
  );
  const unavailableMetrics = useMemo(
    () => presentation.metrics.limited.filter((metric) => metric.value === null || metric.availability === "unavailable"),
    [presentation.metrics.limited],
  );
  const markersByKind = useMemo(() => {
    const markers = new Map<string, number>();
    for (const marker of data?.event_markers ?? []) markers.set(marker.kind, marker.relative_ms);
    return markers;
  }, [data?.event_markers]);
  const maxEventCount = Math.max(1, ...(data?.event_distribution.map((item) => item.count) ?? []));
  const radiusPoints = data?.target_relative_error_radius.points ?? [];
  const peakRadius = Math.max(0, ...radiusPoints.map((point) => point.normalized_error_radius));
  const radiusScale = Math.max(1, peakRadius);
  const technicalLimitations = useMemo(() => unique([
    ...sharedLimitations,
    ...presentation.metrics.formal.flatMap((metric) => metric.limitations),
    ...presentation.metrics.limited.flatMap((metric) => metric.limitations),
  ]), [presentation.metrics.formal, presentation.metrics.limited, sharedLimitations]);

  useEffect(() => {
    if (!selectedMetric) return;
    const row = Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-metric]") ?? [])
      .find((item) => item.dataset.metric === selectedMetric || item.dataset.metricLabel === selectedMetric);
    row?.scrollIntoView({ block: "center" });
    row?.focus();
  }, [selectedMetric]);

  return (
    <div className={styles.dataLayout} ref={rootRef}>
      <section className={styles.metricsColumn} aria-labelledby="metrics-title">
        <div className={styles.sectionHeading}>
          <div><p className={styles.sectionKicker}>Metrics</p><h2 id="metrics-title">正式指标</h2></div>
          <Status tone="success">{presentation.metrics.formal.length} 项</Status>
        </div>
        {grouped.length ? grouped.map(([group, metrics]) => (
          <section className={styles.metricGroup} key={group}>
            <header><h3>{group}</h3><span>{metrics.length} 项</span></header>
            <div className={styles.metricGroupRows}>{metrics.map((metric) => <MetricRow key={metricReference(metric)} limitations={limitationLabels(metric.limitations).filter((item) => !sharedLimitationLabels.includes(item))} metric={metric} />)}</div>
          </section>
        )) : <Empty title="没有可正式展示的指标">不可用和实验性指标不会被填入正式区。</Empty>}
        {presentation.metrics.formal.length ? <p className={styles.comparabilityNote}>跨记录比较：本次结果未附带通过可比性检查的历史记录。</p> : null}

        {availableLimited.length ? (
          <section className={styles.limitedMetrics} aria-labelledby="limited-title">
            <div className={styles.sectionHeading}><div><p className={styles.sectionKicker}>Limited</p><h2 id="limited-title">实验性或受限指标</h2></div><Badge tone="warning">不用于正式结论</Badge></div>
            {availableLimited.map((metric) => <MetricRow key={metricReference(metric)} limitations={limitationLabels(metric.limitations).filter((item) => !sharedLimitationLabels.includes(item))} metric={metric} />)}
          </section>
        ) : null}

        {unavailableMetrics.length ? (
          <details className={styles.unavailableMetrics}>
            <summary>另有 {unavailableMetrics.length} 项指标暂不可用</summary>
            <p>这些指标没有足够证据，不参与当前结论。展开可查看逐项原因。</p>
            <div>{unavailableMetrics.map((metric) => <MetricRow key={metricReference(metric)} limitations={limitationLabels(metric.limitations).filter((item) => !sharedLimitationLabels.includes(item))} metric={metric} />)}</div>
          </details>
        ) : null}

        {sharedLimitationLabels.length ? (
          <section className={styles.analysisLimitations} aria-labelledby="analysis-limitations-title">
            <h2 id="analysis-limitations-title">Analysis 范围限制</h2>
            <ul>{sharedLimitationLabels.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          </section>
        ) : null}
      </section>

      <aside className={styles.chartsColumn} aria-label="图表与证据摘要">
        {loadingData ? <Loading>正在读取安全数据投影</Loading> : null}
        {dataUnavailable ? <Notice tone="warning" title="Analysis Data 当前不可用">页面不会用 Session 结果或历史趋势填补这部分数据。</Notice> : null}
        {data ? <>
          <section className={styles.chartBand}>
            <header><div><p className={styles.sectionKicker}>Distribution</p><h2>事件分布</h2></div></header>
            {data.event_distribution.length ? (
              <div className={styles.distributionPlot} role="group" aria-label="按已验证事件种类统计的分布图">
                {data.event_distribution.map(({ kind, count }) => {
                  const relativeMs = markersByKind.get(kind);
                  return <button disabled={relativeMs === undefined} key={kind} onClick={() => {
                    if (relativeMs !== undefined) onSelectTime(relativeMs);
                  }} type="button">
                    <span>{eventLabel(kind)}</span><i style={{ width: `${(count / maxEventCount) * 100}%` }} /><strong>{count}</strong>
                  </button>;
                })}
              </div>
            ) : <p className={styles.compactUnavailable}>当前没有可安全公开的事件 marker。</p>}
            <p className={styles.chartSummary}><strong>文本摘要：</strong>{data.event_distribution.length ? `已验证事件共 ${data.event_distribution.reduce((sum, item) => sum + item.count, 0)} 个。` : "没有足够事件生成分布摘要。"}</p>
          </section>

          <section className={styles.chartBand}>
            <header><div><p className={styles.sectionKicker}>Target-relative error</p><h2>目标相对误差</h2></div></header>
            {data.target_relative_error_radius.availability === "available" ? (
              <div className={styles.errorSeries} role="img" aria-label={`按挑战相对时间量化的目标相对误差序列，共 ${radiusPoints.length} 个样本，峰值 ${Number(peakRadius.toFixed(2))}`}>
                {radiusPoints.map((point) => <i aria-hidden="true" key={point.relative_ms} style={{ height: `${Math.max(3, (point.normalized_error_radius / radiusScale) * 100)}%` }} />)}
              </div>
            ) : <p className={styles.compactUnavailable}>目标相对误差：{limitationLabel(data.target_relative_error_radius.reason ?? "target_relative_samples_unavailable")}</p>}
            <p className={styles.chartSummary}>{data.target_relative_error_radius.availability === "available" ? `共 ${radiusPoints.length} 个样本，峰值 ${Number(peakRadius.toFixed(2))}。` : ""}数值已在本地按目标半径归一化并量化；页面不接收位置或半径坐标。</p>
          </section>
        </> : null}

        <Notice title="校准快照">
          cm/360：{presentation.calibration.cmPer360 ?? "未确定"}（{presentation.calibration.cmSource ?? "来源未知"}） · FOV：{presentation.calibration.fov ?? "未确定"}（{presentation.calibration.fovSource ?? "来源未知"}）
        </Notice>
        {technicalLimitations.length ? (
          <details className={styles.technicalLimitations}>
            <summary>技术详情</summary>
            <ul>{technicalLimitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          </details>
        ) : null}
      </aside>
    </div>
  );
}
