"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { getAnalysisData, getAnalysisFamilyData } from "@/lib/api";
import type { AnalysisMetricPresentation, AnalysisWorkspacePresentation } from "@/lib/contracts";
import {
  availabilityLabel,
  eventKindLabel,
  limitationLabel,
  metricDescription,
  metricLabel,
  metricReference,
  metricSourceText,
  valueText,
} from "@/lib/metric-format";
import type {
  FrontendAnalysisDataV1,
  FrontendAnalysisFamilyDataRowV1,
  FrontendAnalysisFamilyDataV1,
} from "@/lib/types";
import { t, useT, type MessageKey } from "@/lib/i18n";
import { Badge, Button, Empty, Loading, Notice, Status } from "@/ui/primitives";

import styles from "./task5.module.css";

// i18n 批 3：分组标题是字典键（MessageKey），渲染时经 t() 解析（§2c——不在模块
// 加载期固化 t() 结果，保证切语言即时生效）。
const FAMILY_GROUPS: Record<string, { title: MessageKey; keys: string[] }[]> = {
  target_switching: [
    { title: "analysis.data.group.switchSpeed", keys: ["target_switching.transition_time_ms", "transition_time_ms"] },
    { title: "analysis.data.group.movementQuality", keys: ["target_switching.transition_distance_px", "transition_distance_px", "target_switching.path_efficiency", "path_efficiency"] },
    { title: "analysis.data.group.stabilityControl", keys: ["target_switching.settle_duration_ms", "settle_duration_ms"] },
  ],
  continuous_tracking: [
    { title: "analysis.data.group.trackingQuality", keys: ["continuous_tracking.target_relative_error_px", "target_relative_error_px", "continuous_tracking.time_in_radius_ratio", "time_in_radius_ratio", "continuous_tracking.sparc", "sparc"] },
    { title: "analysis.data.group.deviationRecovery", keys: ["continuous_tracking.loss_count", "loss_count", "continuous_tracking.loss_duration_ms", "loss_duration_ms", "continuous_tracking.reacquisition_latency_ms", "reacquisition_latency_ms"] },
    { title: "analysis.data.group.controlBurden", keys: ["continuous_tracking.correction_burden", "correction_burden"] },
  ],
  static_clicking: [
    { title: "analysis.data.group.stopControl", keys: ["sparc", "decel_frac"] },
    { title: "analysis.data.group.actionQuality", keys: ["linearity", "reverse_ratio"] },
    { title: "analysis.data.group.efficiency", keys: ["path_efficiency"] },
  ],
};

function familyMetricText(key: string, value: number): string {
  if (key === "path_efficiency" || key === "time_in_radius_ratio" || key.endsWith("path_efficiency") || key.endsWith("time_in_radius_ratio")) {
    return `${Number((value * 100).toFixed(1))}%`;
  }
  if (key.endsWith("_ms")) return `${Number(value.toFixed(1))} ms`;
  if (key.endsWith("_px")) return `${Number(value.toFixed(1))} px`;
  if (key === "corrective_count" || key.endsWith("_count")) return `${Number(value.toFixed(1))}${t("metric.unit.count")}`;
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
  const t = useT();
  const groups = FAMILY_GROUPS[familyCode] ?? [{ title: "analysis.data.group.metricsFallback", keys: [] }];
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
            <span>{t(group.title)}</span>
            <span>{t("analysis.data.group.count", { n: group.metrics.length })}</span>
          </div>
          <div className={styles.metricGroupRows}>
            {group.metrics.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metric)}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(metric) ?? availabilityLabel(metric.availability)}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {remaining.length ? (
        <div className={styles.metricGroupBlock}>
          <div className={styles.metricGroupHeader}>
            <span>{t("analysis.data.group.other")}</span>
            <span>{t("analysis.data.group.count", { n: remaining.length })}</span>
          </div>
          <div className={styles.metricGroupRows}>
            {remaining.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metric)}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(metric) ?? availabilityLabel(metric.availability)}</span>
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
  const t = useT();
  const bounds = rowBounds(row);
  const { kill_ms: kill, transition_ms: transition, acquire_ms: acquire, settle_ms: settle } = row.timing;
  const hasAll = [kill, transition, acquire].every(Number.isFinite);
  const total = settle ?? acquire ?? transition ?? 1;
  const slow = typeof row.metrics.path_efficiency === "number" && row.metrics.path_efficiency < 0.6;
  const accessibleLabel = [
    t("analysis.data.switchChain.aria", { index: index + 1 }),
    t("analysis.data.switchChain.ariaTransition", { value: familyMetricText("transition_time_ms", row.metrics.transition_time_ms) }),
    t("analysis.data.switchChain.ariaDistance", { value: familyMetricText("transition_distance_px", row.metrics.transition_distance_px) }),
    t("analysis.data.switchChain.ariaEfficiency", { value: familyMetricText("path_efficiency", row.metrics.path_efficiency) }),
    t("analysis.data.switchChain.ariaSettle", { value: familyMetricText("settle_duration_ms", row.metrics.settle_duration_ms) }),
  ].join(t("common.separator.comma"));

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
  const t = useT();
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
          <span className={styles.sectionTitle}>{t("analysis.data.overviewTitle")}</span>
          <span className={styles.sectionHint}>{t("analysis.data.overviewHintSwitching")}</span>
        </div>
        <MetricOverviewPanel familyCode="target_switching" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        <Notice tone="warning">
          {t("analysis.data.switching.cannotJudgePrefix")}<b>{t("analysis.data.switching.cannotJudge")}</b>{t("analysis.data.switching.cannotJudgeSuffix")}
        </Notice>
        <div className={styles.boundaryPanel}>
          <div className={styles.boundaryTitle}>{t("analysis.data.switching.eventNaming")}</div>
          <dl className={styles.boundaryKv}>
            <dt>{t("analysis.data.switching.kill")}</dt><dd>kill</dd>
            <dt>{t("analysis.data.switching.fullSwitch")}</dt><dd>switch_chain</dd>
            <dt>{t("analysis.data.switching.startSwitch")}</dt><dd>transition</dd>
            <dt>{t("analysis.data.switching.arriveNewTarget")}</dt><dd>next_target_acquired</dd>
            <dt>{t("analysis.data.switching.settleDone")}</dt><dd>settle</dd>
          </dl>
        </div>
      </div>
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">{t("analysis.data.switching.chainTitle")}</h2>
          <span className={styles.sectionCount}>{t("analysis.data.switching.totalCount", { n: familyData?.total_count ?? rows.length })}</span>
          <span className={styles.sectionHint}>{t("analysis.data.switching.rowHint")}</span>
        </div>
          {slowRowIndex >= 0 || goodRowIndex >= 0 ? (
          <div className={styles.familyHighlights}>
            {slowRowIndex >= 0 ? <Badge tone="info">{t("analysis.data.switching.slowBadge", { n: slowRowIndex + 1 })}</Badge> : null}
            {goodRowIndex >= 0 ? <Badge tone="info">{t("analysis.data.switching.goodBadge", { n: goodRowIndex + 1 })}</Badge> : null}
          </div>
        ) : null}
        {loadingFamily ? <Loading>{t("analysis.data.loadingRows")}</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title={t("analysis.data.switching.chainUnavailableTitle")}>{t("analysis.data.switching.chainUnavailableBody")}</Notice>
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
            {loadingMoreFamily ? t("analysis.data.loadingMore") : t("analysis.data.loadMore", { shown: rows.length, total: familyData.total_count })}
          </Button>
        ) : null}
        <div className={styles.switchLegend}>
          <span><span className={styles.switchLegendDot} />{t("analysis.data.switching.kill")}</span>
          <span><span className={styles.switchLegendMove} />{t("analysis.data.switching.legendMove")}</span>
          <span><span className={styles.switchLegendSettle} />{t("analysis.data.switching.legendSettle")}</span>
        </div>
        <div className={styles.chartCard}>
          <p className={styles.chartCap}>
            {t("analysis.data.switching.summaryPrefix")}
            {t("analysis.data.switching.totalCount", { n: rows.length })}
            {medianTransition !== null ? t("analysis.data.switching.summaryMedian", { value: familyMetricText("transition_time_ms", medianTransition) }) : ""}
            {slowRowIndex >= 0 ? t("analysis.data.switching.summarySlow", { n: slowRowIndex + 1 }) : ""}
            {goodRowIndex >= 0 ? t("analysis.data.switching.summaryGood", { n: goodRowIndex + 1 }) : ""}
            {t("analysis.data.switching.summaryTail")}
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
  const t = useT();

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
    if (bounds) links.push({ label: t("analysis.data.tracking.worstDeviation"), kindLabel: t("analysis.data.tracking.deviation"), seq: lossRows.indexOf(longestLoss.row) + 1, start: bounds[0], end: bounds[1] });
  }
  if (slowestReacq.row) {
    const bounds = rowBounds(slowestReacq.row);
    if (bounds) links.push({ label: t("analysis.data.tracking.slowReacquisition"), kindLabel: t("analysis.data.tracking.reacquisition"), seq: reacqRows.indexOf(slowestReacq.row) + 1, start: bounds[0], end: bounds[1] });
  }

  return (
    <div className={styles.familyDataLayout} data-family="tracking" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>{t("analysis.data.overviewTitle")}</span>
            <span className={styles.sectionHint}>{t("analysis.data.overviewHintGrouped")}</span>
          </div>
          <MetricOverviewPanel familyCode="continuous_tracking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">{t("analysis.data.tracking.title")}</h2>
          <span className={styles.sectionCount}>{t("analysis.data.tracking.totalCount", { n: familyData?.total_count ?? familyData?.rows.length ?? 0 })}</span>
        </div>
        <div className={styles.chartGrid}>
          <div
            aria-label={t("analysis.data.tracking.radiusAria", { n: radiusPoints.length, peak: Number(peakRadius.toFixed(2)) })}
            className={styles.chartCard}
            role="img"
          >
            <div className={styles.chartTitle}>
              {t("analysis.data.tracking.radiusTitle")}
              <Badge tone="neutral" style={{ marginInlineStart: "auto" }}>{t("analysis.data.tracking.normalized")}</Badge>
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
              <p className={styles.chartCap}>{t("analysis.data.tracking.samplesUnavailable")}</p>
            )}
            <p className={styles.chartCap}>
              {t("analysis.data.tracking.radiusBody", { n: radiusPoints.length, peak: Number(peakRadius.toFixed(2)) })}
            </p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.tracking.timelineTitle")}</div>
            {lossRows.length || reacqRows.length ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 100">
                <line opacity="0.3" stroke="var(--outline-variant)" strokeWidth="1" x1="20" x2="340" y1="50" y2="50" />
                {lossRows.map((row, index) => {
                  const bounds = rowBounds(row);
                  if (!bounds) return null;
                  const left = 20 + (bounds[0] / timelineMax) * 320;
                  const width = Math.max(4, ((bounds[1] - bounds[0]) / timelineMax) * 320);
                  return <rect key={`loss-${index}`} fill="var(--event-peak)" height="16" opacity="0.6" width={width} x={left} y="42" />;
                })}
                {reacqRows.map((row, index) => {
                  const bounds = rowBounds(row);
                  if (!bounds) return null;
                  const left = 20 + (bounds[0] / timelineMax) * 320;
                  const width = Math.max(4, ((bounds[1] - bounds[0]) / timelineMax) * 320);
                  return <line key={`reacq-${index}`} stroke="var(--tertiary)" strokeWidth="2" x1={left} x2={left + width} y1="70" y2="70" />;
                })}
                <rect fill="var(--event-peak)" height="10" opacity="0.6" width="10" x="20" y="84" />
                <text className={styles.chartText} x="34" y="93">{t("analysis.data.tracking.legendDeviation")}</text>
                <line stroke="var(--tertiary)" strokeWidth="2" x1="160" x2="175" y1="89" y2="89" />
                <text className={styles.chartText} x="180" y="93">{t("analysis.data.tracking.legendReacquisition")}</text>
              </svg>
            ) : (
              <p className={styles.chartCap}>{t("analysis.data.tracking.noEvents")}</p>
            )}
            <p className={styles.chartCap}>
              {t("analysis.data.tracking.eventCounts", { loss: lossRows.length, reacq: reacqRows.length })}
            </p>
          </div>
        </div>

        {links.length && presentation.video.kind === "seekable" ? (
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.videoLinks.title")}</div>
            <p className={styles.chartCap}>{t("analysis.data.videoLinks.hint")}</p>
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
          <div className={styles.boundaryTitle}>{t("analysis.data.boundary.title")}</div>
          <dl className={styles.boundaryKv}>
            <dt>{t("analysis.data.boundary.scope")}</dt><dd>{t("analysis.data.boundary.trackingScope")}</dd>
            <dt>{t("analysis.data.tracking.legendReacquisition")}</dt><dd>{t("analysis.data.boundary.reacqDelayBody")}</dd>
            <dt>{t("metric.value.unavailable")}</dt><dd>{t("analysis.data.boundary.trackingUnavailableBody")}</dd>
          </dl>
        </div>

        {loadingFamily ? <Loading>{t("analysis.data.loadingRows")}</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title={t("analysis.data.tracking.unavailableTitle")}>{t("analysis.data.familyRowsUnavailableBody")}</Notice>
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
  const t = useT();
  const flickLinks: { label: string; kindLabel: string; seq: number; start: number; end: number }[] = [];
  if (bestRow) {
    const bounds = rowBounds(bestRow);
    if (bounds) flickLinks.push({ label: t("analysis.data.flicking.bestLabel"), kindLabel: "Flick", seq: rows.indexOf(bestRow) + 1, start: bounds[0], end: bounds[1] });
  }
  if (slowRow && slowRow !== bestRow) {
    const bounds = rowBounds(slowRow);
    if (bounds) flickLinks.push({ label: t("analysis.data.flicking.slowLabel"), kindLabel: "Flick", seq: rows.indexOf(slowRow) + 1, start: bounds[0], end: bounds[1] });
  }

  return (
    <div className={styles.familyDataLayout} data-family="flicking" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>{t("analysis.data.overviewTitle")}</span>
            <span className={styles.sectionHint}>{t("analysis.data.overviewHintGrouped")}</span>
          </div>
          <MetricOverviewPanel familyCode="static_clicking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="family-detail-title">{t("analysis.data.flicking.title")}</h2>
          <span className={styles.sectionCount}>{t("analysis.data.flicking.totalCount", { n: familyData?.total_count ?? rows.length })}</span>
        </div>
        <div className={styles.chartGrid}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.flicking.timingTitle")}</div>
            {phases && totalPhase > 0 ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 90">
                <rect fill="var(--tertiary)" height="40" opacity="0.75" width={(phases.accel / totalPhase) * 320} x="20" y="20" />
                <rect fill="var(--event-peak)" height="40" opacity="0.75" width={(phases.decel / totalPhase) * 320} x={20 + (phases.accel / totalPhase) * 320} y="20" />
                <rect fill="var(--on-surface-variant)" height="40" opacity="0.75" width={(phases.settle / totalPhase) * 320} x={20 + ((phases.accel + phases.decel) / totalPhase) * 320} y="20" />
                <text className={styles.chartTextOnTertiary} textAnchor="middle" x={20 + (phases.accel / totalPhase) * 160} y="45">{Math.round((phases.accel / totalPhase) * 100)}%</text>
                <text className={styles.chartTextOnPrimary} textAnchor="middle" x={20 + (phases.accel / totalPhase) * 320 + (phases.decel / totalPhase) * 160} y="45">{Math.round((phases.decel / totalPhase) * 100)}%</text>
                <rect fill="var(--tertiary)" height="8" opacity="0.75" width="8" x="20" y="72" />
                <text className={styles.chartText} x="32" y="79">{t("analysis.data.flicking.accel", { value: familyMetricText("accel_duration_ms", phases.accel) })}</text>
                <rect fill="var(--event-peak)" height="8" opacity="0.75" width="8" x="110" y="72" />
                <text className={styles.chartText} x="122" y="79">{t("analysis.data.flicking.decel", { value: familyMetricText("decel_duration_ms", phases.decel) })}</text>
                <rect fill="var(--on-surface-variant)" height="8" opacity="0.75" width="8" x="220" y="72" />
                <text className={styles.chartText} x="232" y="79">{t("analysis.data.flicking.settle", { value: familyMetricText("settle_duration_ms", phases.settle) })}</text>
              </svg>
            ) : (
              <p className={styles.chartCap}>{t("analysis.data.flicking.timingInsufficient")}</p>
            )}
            <p className={styles.chartCap}>{t("analysis.data.flicking.timingBody")}</p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.flicking.pathTitle")}</div>
            {efficiencies.length ? (
              <svg className={styles.chartSvg} preserveAspectRatio="xMidYMid meet" viewBox="0 0 360 110">
                {Array.from({ length: 10 }).map((_, index) => {
                  const binMin = 0.6 + index * 0.04;
                  const binMax = 0.6 + (index + 1) * 0.04;
                  const count = efficiencies.filter((value) => value >= binMin && value < binMax).length;
                  const height = Math.max(4, (count / Math.max(1, efficiencies.length / 5)) * 90);
                  return <rect key={index} x={20 + index * 32} y={100 - height} width="26" height={height} fill="var(--tertiary)" opacity="0.7" />;
                })}
                <text className={styles.chartTextMuted} textAnchor="start" x="20" y="108">60%</text>
                <text className={styles.chartTextMuted} textAnchor="end" x="340" y="108">100%</text>
                {medianEff !== null ? (
                  <>
                    <line stroke="var(--event-peak)" strokeDasharray="3 2" strokeWidth="1.5" x1={20 + ((medianEff - 0.6) / 0.4) * 320} x2={20 + ((medianEff - 0.6) / 0.4) * 320} y1="10" y2="100" />
                    <text className={styles.chartTextEmph} x={24 + ((medianEff - 0.6) / 0.4) * 320} y="16">{Number((medianEff * 100).toFixed(0))}%</text>
                  </>
                ) : null}
              </svg>
            ) : (
              <p className={styles.chartCap}>{t("analysis.data.flicking.pathInsufficient")}</p>
            )}
            <p className={styles.chartCap}>
              {t("analysis.data.flicking.pathBody", { n: rows.length })}{medianEff !== null ? t("analysis.data.flicking.pathMedian", { value: Number((medianEff * 100).toFixed(0)) }) : ""}
            </p>
          </div>
        </div>

        {flickLinks.length && presentation.video.kind === "seekable" ? (
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.videoLinks.title")}</div>
            <p className={styles.chartCap}>{t("analysis.data.videoLinks.hint")}</p>
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
          <div className={styles.boundaryTitle}>{t("analysis.data.boundary.title")}</div>
          <dl className={styles.boundaryKv}>
            <dt>{t("analysis.data.boundary.scope")}</dt><dd>{t("analysis.data.boundary.flickScope")}</dd>
            <dt>{t("analysis.data.boundary.frame")}</dt><dd>{t("analysis.data.boundary.frameBody")}</dd>
            <dt>{t("metric.value.unavailable")}</dt><dd>{t("analysis.data.boundary.flickUnavailableBody")}</dd>
          </dl>
        </div>

        {loadingFamily ? <Loading>{t("analysis.data.loadingFlick")}</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title={t("analysis.data.flicking.unavailableTitle")}>{t("analysis.data.familyRowsUnavailableBody")}</Notice>
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
  const t = useT();

  return (
    <div className={styles.familyDataLayout} data-family="generic" data-metrics={hasFormalMetrics ? "available" : "empty"}>
      {hasFormalMetrics ? (
        <div className={styles.metricsColumn}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>{t("analysis.data.overviewTitle")}</span>
            <span className={styles.sectionHint}>{t("analysis.data.overviewHintGrouped")}</span>
          </div>
          <MetricOverviewPanel familyCode="static_clicking" metrics={presentation.metrics.formal} onSelectMetric={onSelectMetric} />
        </div>
      ) : null}
      <div className={styles.detailColumn}>
        <div className={styles.chartGrid}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle} id="family-detail-title">{t("analysis.data.generic.eventTitle")}</div>
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
                      <span>{eventKindLabel(kind)}</span>
                      <i style={{ width: `${(count / maxEventCount) * 100}%` }} />
                      <strong>{count}</strong>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className={styles.chartCap}>{t("analysis.data.generic.noMarkers")}</p>
            )}
            <p className={styles.chartCap}>
              {data?.event_distribution.length ? t("analysis.data.generic.eventTotal", { n: data.event_distribution.reduce((sum, item) => sum + item.count, 0) }) : t("analysis.data.generic.eventInsufficient")}
            </p>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>{t("analysis.data.tracking.radiusTitle")}</div>
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
              <p className={styles.chartCap}>{t("analysis.data.tracking.samplesUnavailable")}</p>
            )}
            <p className={styles.chartCap}>{t("analysis.data.generic.radiusBodyShort")}</p>
          </div>
        </div>

        {loadingFamily ? <Loading>{t("analysis.data.loadingRows")}</Loading> : null}
        {familyData?.availability === "unavailable" ? (
          <Notice tone="warning" title={t("analysis.data.generic.unavailableTitle")}>{t("analysis.data.generic.unavailableBody")}</Notice>
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
  const t = useT();
  const rootRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<FrontendAnalysisDataV1 | null>(null);
  const [loadingData, setLoadingData] = useState(true);
  const [dataUnavailable, setDataUnavailable] = useState(false);
  const [familyData, setFamilyData] = useState<FrontendAnalysisFamilyDataV1 | null>(null);
  const [loadingFamily, setLoadingFamily] = useState(false);
  const [loadingMoreFamily, setLoadingMoreFamily] = useState(false);
  const [familyUnavailable, setFamilyUnavailable] = useState(false);
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
      {loadingData ? <Loading>{t("analysis.data.loadingProjection")}</Loading> : null}
      {dataUnavailable ? <Notice tone="warning" title={t("analysis.data.dataUnavailableTitle")}>{t("analysis.data.dataUnavailableBody")}</Notice> : null}
      {!loadingData && !dataUnavailable ? (
        familyView === "switching" ? <SwitchingDataView {...viewProps} /> :
        familyView === "tracking" ? <TrackingDataView {...viewProps} /> :
        familyView === "flicking" ? <FlickingDataView {...viewProps} /> :
        <GenericDataView {...viewProps} />
      ) : null}

      {familyUnavailable ? <Notice tone="warning" title={t("analysis.data.familyUnavailableTitle")}>{t("analysis.data.familyUnavailableBody")}</Notice> : null}

      {availableLimited.length ? (
        <section className={styles.limitedMetrics} aria-labelledby="limited-metrics-title">
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle} id="limited-metrics-title">{t("analysis.data.limitedTitle")}</h2>
            <Badge tone="warning">{t("analysis.data.limitedBadge")}</Badge>
          </div>
          <div className={styles.metricOverviewPanel}>
            {availableLimited.map((metric) => {
              const ref = metricReference(metric);
              return (
                <button className={styles.metricRow} data-metric-label={ref} key={ref} onClick={() => onSelectMetric(ref)} type="button">
                  <span className={styles.metricKey}>{metricLabel(metric)}</span>
                  <span className={styles.metricValue}>{valueText(metric)}</span>
                  <span className={styles.metricPlain}>{metricDescription(metric) ?? availabilityLabel(metric.availability)}</span>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {unavailableMetrics.length ? (
        <details className={styles.unavailableMetrics}>
          <summary>{t("analysis.data.unavailableSummary", { n: unavailableMetrics.length })}</summary>
          {unavailableMetrics.map((metric) => (
            <div className={styles.metricRow} data-metric-label={metricReference(metric)} key={metricReference(metric)}>
              <span className={styles.metricKey}>{metricLabel(metric)}</span>
              <span className={styles.metricValue}>{t("metric.value.unavailable")}</span>
              <span className={styles.metricPlain}>
                {metric.limitations.map(limitationLabel).join(t("common.separator.semicolon")) || t("analysis.data.noSafeLimitation")}
                {t("analysis.data.sourcePrefix")}{metricSourceText(metric)}
              </span>
            </div>
          ))}
        </details>
      ) : null}

      {sharedLimitationLabels.length ? (
        <section className={styles.analysisLimitations}>
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>{t("analysis.data.limitationsTitle")}</h2>
          </div>
          <ul>{sharedLimitationLabels.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}
