import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("legacy analysis route redirects while its reusable workspace assets remain", async () => {
  const page = await source("app/analysis/[analysisId]/page.tsx");
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  const primitives = await source("ui/primitives.tsx");
  assert.match(page, /redirect\("\/history"\)/);
  // 批 3 起三个 tab 文案走字典（analysis.tabs.*）。
  assert.match(workspace, /t\("analysis\.tabs\.diagnosis"\)/);
  assert.match(workspace, /t\("analysis\.tabs\.video"\)/);
  assert.match(workspace, /t\("analysis\.tabs\.data"\)/);
  assert.match(workspace, /<Tabs/);
  assert.match(primitives, /aria-selected/);

  const coachLocator = await source("components/task5/AnalysisWorkspace.tsx");
  assert.match(coachLocator, /window\.addEventListener\("aiming-cookie:coach-locate", locateCoachContext\)/);
  assert.match(coachLocator, /window\.removeEventListener\("aiming-cookie:coach-locate", locateCoachContext\)/);
  assert.match(coachLocator, /event\.preventDefault\(\)/);
  assert.match(coachLocator, /setTab\("video"\)/);
  assert.match(coachLocator, /setPlayheadMs\(locator\.relative_start_ms\)/);
  assert.doesNotMatch(workspace, /第二条|Benchmark|ReportView/);
});

test("video view consumes managed URLs and keeps the timeline seekable", async () => {
  const video = await source("components/task5/VideoView.tsx");
  assert.match(video, /getManagedVideoUrl/);
  assert.match(video, /getAnalysisVideoBlob/);
  assert.match(video, /URL\.createObjectURL/);
  assert.match(video, /URL\.revokeObjectURL/);
  assert.match(video, /t\("analysis\.video\.noEvidenceTitle"\)/);
  assert.match(video, /<video/);
  assert.match(video, /aria-label=\{t\("analysis\.video\.timelineAria"\)\}/);
  assert.doesNotMatch(video, /raw_trace|video_path|file:\/\//);
});

test("video view separates no-video and input-data tiers from real evidence loss", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 无视频：本局没有录制视频（input-native 档）。
  assert.match(video, /t\("analysis\.video\.noEvidenceBody"\)/);
  // 本档不消费视觉测量：文案不再暗示视频被移除或服务故障。
  assert.match(video, /t\("analysis\.video\.inputOnlyTitle"\)/);
  assert.match(video, /t\("analysis\.video\.inputOnlyBody"\)/);
  assert.match(video, /presentation\.family\.status === "supported"/);
  // 吓人文案只保留给本应消费视觉测量的档位与真实加载失败。
  assert.match(video, /t\("analysis\.video\.unavailableTitle"\)/);
});

test("diagnosis suppresses scenario-specific advice when the scenario is not classified", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  assert.match(diagnosis, /presentation\.family\.status !== "unavailable"/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.unverifiedTitle"\)/);
});

test("analysis header does not repeat the evidence summary", async () => {
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  const styles = await source("components/task5/task5.module.css");
  assert.doesNotMatch(workspace, /结论依赖|视觉证据已校验/);
  assert.doesNotMatch(workspace, /<div className=\{styles\.evidenceRow\}>/);
  assert.match(workspace, /aria-describedby="analysis-evidence-summary"/);
  assert.match(workspace, /aria-label=\{t\("analysis\.header\.evidenceAria"\)\}/);
  assert.match(workspace, /id="analysis-evidence-summary" role="tooltip"/);
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.evidenceSummary:hover \.evidenceTooltip\s*\{[^}]*\}\s*\}/);
  assert.match(styles, /\.evidenceSummary:focus-within \.evidenceTooltip/);
  assert.match(styles, /\.evidenceTrigger:focus-visible/);
});

test("video volume follows familiar mute, hover, focus, and slider behavior", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const styles = await source("components/task5/task5.module.css");
  assert.match(video, /const \[volume, setVolume\] = useState\(1\)/);
  assert.match(video, /const \[muted, setMuted\] = useState\(false\)/);
  assert.match(video, /aria-pressed=\{muted\}/);
  assert.match(video, /aria-label=\{t\("analysis\.video\.volumeAria"\)\}/);
  assert.match(video, /aria-orientation="vertical"/);
  assert.match(video, /\\uFE0E/);
  assert.match(video, /className=\{styles\.volumeIcon\}/);
  assert.match(video, /type="range"/);
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.volumeControl:hover \.volumePopover\s*\{[^}]*\}\s*\}/);
  assert.match(styles, /\.volumeControl:focus-within \.volumePopover/);
  assert.match(styles, /\.volumePopover[^{]*\{[\s\S]*position:\s*absolute[\s\S]*inset-inline-start:\s*50%;[\s\S]*flex-direction:\s*column/);
  assert.match(styles, /\.volumeSlider[^{]*\{[\s\S]*writing-mode:\s*vertical-lr;[\s\S]*direction:\s*rtl;/);
});

test("diagnosis distinguishes current observations from legacy candidate explanations", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const data = await source("components/task5/DataView.tsx");
  assert.match(diagnosis, /priorityReason/);
  assert.match(diagnosis, /rootCauses/);
  assert.match(diagnosis, /presentationKind/);
  assert.match(diagnosis, /claimLabel/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.issuesTitle"\)/);
  assert.match(diagnosis, /issue\.severity !== "info"/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.candidateTitle"\)/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.prescriptionTitle"\)/);
  assert.doesNotMatch(diagnosis, /历史候选说明/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.viewEvidence"\)/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.viewMetric"\)/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.askCoach"\)/);
  assert.doesNotMatch(diagnosis, /最需要处理|三层根因|<h4>处方<\/h4>/);
  assert.match(data, /metrics\.formal/);
  assert.match(data, /t\("analysis\.data\.switching\.summaryPrefix"\)/);
  assert.match(data, /limitations/);
  assert.doesNotMatch(`${diagnosis}${data}`, /raw_trace|stats_source_ref|performance_source_ref|absolute_path/);
});

test("diagnosis keeps descriptive metrics and true empty states inside consistent cards", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const styles = await source("components/task5/task5.module.css");

  assert.match(diagnosis, /summaryMode === "descriptive"/);
  assert.match(diagnosis, /t\("analysis\.diagnosis\.summaryDescriptiveHint"\)/);
  assert.match(diagnosis, /summaryMode !== "descriptive"/);
  assert.doesNotMatch(diagnosis, /\? "描述性"/);
  assert.match(diagnosis, /unit === "percent"/);
  assert.match(diagnosis, /unit === "dimensionless" \|\| unit === "ratio"/);
  assert.match(diagnosis, /className=\{styles\.metricSummaryEmpty\}/);
  assert.match(styles, /\.metricSummaryEmpty[^{]*\{[\s\S]*border:\s*1px solid var\(--outline-variant\);[\s\S]*background:\s*var\(--surface\);/);
  assert.match(styles, /\.metricSummaryPanel \.metricRow > :global\(\.ac-status\)[^{]*\{[\s\S]*grid-column:\s*2;[\s\S]*justify-self:\s*end;/);
});

test("diagnosis profile explanation is available on hover and keyboard focus", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const styles = await source("components/task5/task5.module.css");

  assert.match(diagnosis, /className=\{styles\.profileLabel\}/);
  assert.match(diagnosis, /aria-describedby="analysis-profile-explanation"/);
  assert.match(diagnosis, /id="analysis-profile-explanation" role="tooltip"/);
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.profileLabel:hover \.profileTooltip\s*\{[^}]*\}\s*\}/);
  assert.match(styles, /\.profileLabel:focus-within \.profileTooltip/);
  assert.match(styles, /\.profileLabel:focus-visible/);
});

test("data view consumes the bounded analysis-data projection without a pseudo trend", async () => {
  const data = await source("components/task5/DataView.tsx");
  const formats = await source("lib/metric-format.ts");
  const styles = await source("components/task5/task5.module.css");
  assert.match(data, /getAnalysisData/);
  assert.match(data, /event_distribution/);
  assert.match(data, /target_relative_error_radius/);
  assert.match(data, /onSelectTime/);
  // 事件/指标标签与格式化在共享 lib/metric-format.ts，DataView 导入复用。
  assert.match(data, /from "@\/lib\/metric-format"/);
  assert.match(formats, /tracking_fixed_window: "metric\.eventKind\.trackingFixedWindow"/);
  assert.match(formats, /tracking_episode: "metric\.eventKind\.trackingEpisode"/);
  assert.match(formats, /low_confidence: "metric\.eventKind\.lowConfidence"/);
  assert.match(data, /t\("analysis\.data\.tracking\.radiusBody", \{ n: radiusPoints\.length, peak: Number\(peakRadius\.toFixed\(2\)\) \}\)/);
  assert.match(formats, /no_target_visible: "metric\.limitation\.noTargetVisible"/);
  assert.match(formats, /return metric\.definition\?\.name \?\? metricReference\(metric\)/);
  assert.match(formats, /return metric\.definition\?\.description \?\? null/);
  assert.match(data, /item\.dataset\.metricLabel === selectedMetric/);
  assert.match(formats, /referenceKey === "target_switching\.path_efficiency"/);
  assert.match(formats, /kill: "metric\.eventKind\.kill"/);
  assert.match(formats, /switch_chain: "metric\.eventKind\.switchChain"/);
  assert.match(formats, /transition: "metric\.eventKind\.transition"/);
  assert.match(formats, /next_target_acquired: "metric\.eventKind\.nextTargetAcquired"/);
  assert.match(formats, /settle: "metric\.eventKind\.settle"/);
  assert.doesNotMatch(data, /const METRIC_LABELS/);
  assert.match(formats, /source\.includes\("tracking-analysis"\)/);
  assert.match(data, /unavailableMetrics/);
  assert.match(data, /<details className=\{styles\.unavailableMetrics\}>/);
  assert.doesNotMatch(data, /metric\.sources\.join\(" \+ "\)/);
  assert.doesNotMatch(data, /radiusPoints\.map\(\(point\) => <button/);
  assert.doesNotMatch(data, /跨记录趋势|<p className={styles\.sectionKicker}>Trend/);
  assert.doesNotMatch(data, /preserveAspectRatio="none"/);
  assert.match(data, /data-metrics=\{hasFormalMetrics \? "available" : "empty"\}/);
  assert.match(styles, /\.chartGrid[\s\S]*repeat\(auto-fit, minmax\(min\(100%, 340px\), 1fr\)\)/);
  assert.match(styles, /\.familyDataLayout\[data-metrics="empty"\][\s\S]*grid-template-columns: minmax\(0, 1fr\)/);
});

test("data view renders bounded family rows without adding a family tab", async () => {
  const data = await source("components/task5/DataView.tsx");
  const formats = await source("lib/metric-format.ts");
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  assert.match(data, /getAnalysisFamilyData/);
  assert.match(data, /frontend_analysis_family_data\.v1/);
  assert.match(data, /switch_chain/);
  assert.match(formats, /tracking_fixed_window/);
  assert.match(formats, /tracking_change_response/);
  assert.match(formats, /static_flick/);
  assert.match(data, /t\("analysis\.data\.switchChain\.ariaTransition"/);
  assert.match(data, /t\("analysis\.data\.switchChain\.ariaSettle"/);
  assert.match(formats, /tracking_change_response: "metric\.eventKind\.trackingChangeResponse"/);
  assert.match(data, /t\("analysis\.data\.flicking\.(accel|decel|settle)"/);
  assert.match(formats, /peak: "metric\.eventKind\.peak"/);
  assert.match(formats, /corrective: "metric\.eventKind\.corrective"/);
  assert.match(data, /presentation\.video\.kind === "seekable"/);
  assert.match(data, /t\("analysis\.data\.loadMore"/);
  assert.doesNotMatch(data, /人的反应(?:时间|延迟)/);
  assert.doesNotMatch(workspace, /Switching.*Tracking.*Flicking|family-tab/i);
});

test("analysis components use frozen tokens and no raw colors", async () => {
  const css = await source("components/task5/task5.module.css");
  const dataView = await source("components/task5/DataView.tsx");
  assert.match(css, /var\(--surface/);
  assert.match(css, /var\(--outline-variant\)/);
  assert.match(css, /\.distributionPlot button \{[\s\S]*min-height: 24px/);
  assert.match(dataView, /className=\{styles\.distributionPlot\} role="group"/);
  assert.doesNotMatch(dataView, /className=\{styles\.distributionPlot\} role="img"/);
  assert.match(css, /\.errorSeries \{[\s\S]*gap: 1px/);
  assert.match(css, /\.errorSeries i \{[\s\S]*min-width: 0/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}|rgb\(|hsl\(/);
});
