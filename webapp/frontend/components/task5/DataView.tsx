"use client";

import { useEffect, useMemo, useRef } from "react";

import type { AnalysisMetricPresentation, AnalysisWorkspacePresentation } from "@/lib/contracts";
import { Badge, Button, Empty, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

function valueText(metric: AnalysisMetricPresentation): string {
  if (metric.value === null) return "不可用";
  const value = typeof metric.value === "number" ? Number(metric.value.toFixed(3)) : metric.value;
  return `${value}${metric.unit ? ` ${metric.unit}` : ""}`;
}

function sourceLabel(metric: AnalysisMetricPresentation): string {
  return metric.sources.length ? metric.sources.join(" + ") : "来源未提供";
}

function metricGroup(metric: AnalysisMetricPresentation): string {
  if (metric.sources.some((source) => source.includes("raw") || source.includes("input"))) return "输入运动学";
  if (metric.sources.some((source) => source.includes("video") || source.includes("mp4"))) return "视觉验证";
  return "结果与质量";
}

function MetricRow({ metric }: { metric: AnalysisMetricPresentation }) {
  return (
    <article className={styles.metricRow} data-metric={metric.key} tabIndex={-1}>
      <div><strong>{metric.key}</strong><span>{sourceLabel(metric)}</span></div>
      <div className={styles.metricValue}><strong>{valueText(metric)}</strong><span>{metric.availability}</span></div>
      <dl>
        <div><dt>覆盖</dt><dd>{metric.coverage === null ? "未知" : `${Math.round(metric.coverage * 100)}%`}</dd></div>
        <div><dt>可比性</dt><dd>当前合同未提供</dd></div>
      </dl>
      {metric.limitations.length ? <p className={styles.metricLimitations}>限制：{metric.limitations.join(" · ")}</p> : null}
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
  const grouped = useMemo(() => {
    const rows = new Map<string, AnalysisMetricPresentation[]>();
    for (const metric of presentation.metrics.formal) {
      const group = metricGroup(metric);
      rows.set(group, [...(rows.get(group) ?? []), metric]);
    }
    return Array.from(rows.entries());
  }, [presentation.metrics.formal]);
  const eventCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const event of presentation.timeline) counts.set(event.type, (counts.get(event.type) ?? 0) + 1);
    return Array.from(counts.entries()).sort((left, right) => right[1] - left[1]);
  }, [presentation.timeline]);
  const maxEventCount = Math.max(1, ...eventCounts.map(([, count]) => count));

  useEffect(() => {
    if (!selectedMetric) return;
    const row = Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-metric]") ?? [])
      .find((item) => item.dataset.metric === selectedMetric);
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
            <div className={styles.metricGroupRows}>{metrics.map((metric) => <MetricRow key={metric.key} metric={metric} />)}</div>
          </section>
        )) : <Empty title="没有可正式展示的指标">不可用和实验性指标不会被填入正式区。</Empty>}

        {presentation.metrics.limited.length ? (
          <section className={styles.limitedMetrics} aria-labelledby="limited-title">
            <div className={styles.sectionHeading}><div><p className={styles.sectionKicker}>Limited</p><h2 id="limited-title">实验性或受限指标</h2></div><Badge tone="warning">不用于正式结论</Badge></div>
            {presentation.metrics.limited.map((metric) => <MetricRow key={metric.key} metric={metric} />)}
          </section>
        ) : null}
      </section>

      <aside className={styles.chartsColumn} aria-label="图表与证据摘要">
        <section className={styles.chartBand}>
          <header><div><p className={styles.sectionKicker}>Distribution</p><h2>事件分布</h2></div></header>
          {eventCounts.length ? (
            <div className={styles.distributionPlot} role="img" aria-label="按事件类型统计的分布图">
              {eventCounts.map(([type, count]) => (
                <button key={type} onClick={() => {
                  const event = presentation.timeline.find((item) => item.type === type);
                  const time = event?.relative_ms ?? (typeof event?.time_s === "number" ? event.time_s * 1000 : null);
                  if (time !== null) onSelectTime(time);
                }} type="button">
                  <span>{type}</span><i style={{ width: `${(count / maxEventCount) * 100}%` }} /><strong>{count}</strong>
                </button>
              ))}
            </div>
          ) : <Empty title="事件分布不可用">当前结果没有公开的事件时间轴。</Empty>}
          <p className={styles.chartSummary}><strong>文本摘要：</strong>{eventCounts.length ? `当前时间轴包含 ${presentation.timeline.length} 个事件，${eventCounts[0][0]} 数量最多。` : "没有足够事件生成分布摘要。"}</p>
        </section>

        <section className={styles.chartBand}>
          <header><div><p className={styles.sectionKicker}>Trajectory</p><h2>运动轨迹</h2></div></header>
          <Empty title="轨迹暂不可展示">安全 Analysis v2 投影没有公开位置序列；页面不会读取或重建原始输入轨迹。</Empty>
          <p className={styles.chartSummary}><strong>文本摘要：</strong>当前只能呈现已经确定的指标和事件，不能画出可核验的二维轨迹。</p>
        </section>

        <section className={styles.chartBand}>
          <header><div><p className={styles.sectionKicker}>Trend</p><h2>跨记录趋势</h2></div></header>
          <Empty title="趋势暂不可比较">当前 Analysis 没有附带满足场景、模式、单位、校准和质量门槛的历史序列。</Empty>
          <p className={styles.chartSummary}><strong>文本摘要：</strong>没有生成差异百分比或伪 PB；可比较性需要由 History 合同明确给出。</p>
        </section>

        <Notice title="校准快照">
          cm/360：{presentation.calibration.cmPer360 ?? "未确定"}（{presentation.calibration.cmSource ?? "来源未知"}） · FOV：{presentation.calibration.fov ?? "未确定"}（{presentation.calibration.fovSource ?? "来源未知"}）
        </Notice>
      </aside>
    </div>
  );
}
