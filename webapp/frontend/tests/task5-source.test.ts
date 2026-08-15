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
  assert.match(workspace, /诊断/);
  assert.match(workspace, /视频/);
  assert.match(workspace, /数据/);
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

test("video view consumes managed URLs and evidence segment anchors", async () => {
  const video = await source("components/task5/VideoView.tsx");
  assert.match(video, /getAnalysisEvidenceSegments/);
  assert.match(video, /getManagedVideoUrl/);
  assert.match(video, /getAnalysisVideoBlob/);
  assert.match(video, /URL\.createObjectURL/);
  assert.match(video, /URL\.revokeObjectURL/);
  assert.match(video, /relative_start_ms/);
  assert.match(video, /没有可用视觉证据/);
  assert.match(video, /<video/);
  assert.match(video, /aria-label="分析时间轴"/);
  assert.doesNotMatch(video, /raw_trace|video_path|file:\/\//);
});

test("video view separates no-video and input-data tiers from real evidence loss", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 无视频：本局没有录制视频（input-native 档）。
  assert.match(video, /本局没有录制视频/);
  // 本档不消费视觉测量：文案不再暗示视频被移除或服务故障。
  assert.match(video, /本档分析基于输入数据/);
  assert.match(video, /不代表证据被移除/);
  assert.match(video, /presentation\.family\.status === "supported"/);
  // baseline 档挂上回放后，播放器上说明回放不参与视觉测量。
  assert.match(video, /视频回放仍可观看；回放不参与视觉测量结论/);
  // 吓人文案只保留给本应消费视觉测量的档位与真实加载失败。
  assert.match(video, /视觉证据当前不可用/);
});

test("diagnosis suppresses scenario-specific advice when the scenario is not classified", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  assert.match(diagnosis, /presentation\.family\.status !== "unavailable"/);
  assert.match(diagnosis, /当前场景尚未完成核验/);
});

test("video view keeps EvidenceSegment failure and retry local to the timeline", async () => {
  const video = await source("components/task5/VideoView.tsx");
  assert.match(video, /segmentsLoading/);
  assert.match(video, /segmentsFailed/);
  assert.match(video, /loadSegments/);
  assert.match(video, /证据片段暂时不可用/);
  assert.match(video, /重试证据片段/);
  assert.match(video, /没有可用证据片段/);
});

test("video player keeps the evidence segment control on one line", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const styles = await source("components/task5/task5.module.css");
  assert.match(video, /className=\{`\$\{styles\.playerBarBtn\} \$\{styles\.playerBarEvidence\}`\}[\s\S]*证据片段 \{segmentRows\.length\}/);
  assert.match(styles, /\.playerBarBtn[^{]*\{[\s\S]*flex:\s*0 0 32px;/);
  assert.match(styles, /\.playerBarEvidence[^{]*\{[\s\S]*width:\s*auto;[\s\S]*white-space:\s*nowrap;/);
});

test("analysis header does not repeat the evidence summary", async () => {
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  const styles = await source("components/task5/task5.module.css");
  assert.doesNotMatch(workspace, /结论依赖|视觉证据已校验/);
  assert.doesNotMatch(workspace, /<div className=\{styles\.evidenceRow\}>/);
  assert.match(workspace, /aria-describedby="analysis-evidence-summary"/);
  assert.match(workspace, /aria-label="查看本次分析证据"/);
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
  assert.match(video, /aria-label="音量"/);
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
  assert.match(diagnosis, /分析发现/);
  assert.match(diagnosis, /issue\.severity !== "info"/);
  assert.match(diagnosis, /候选解释/);
  assert.match(diagnosis, /规则化练习建议/);
  assert.doesNotMatch(diagnosis, /历史候选说明/);
  assert.match(diagnosis, /查看证据/);
  assert.match(diagnosis, /查看指标/);
  assert.match(diagnosis, /问 Coach/);
  assert.doesNotMatch(diagnosis, /最需要处理|三层根因|<h4>处方<\/h4>/);
  assert.match(data, /metrics\.formal/);
  assert.match(data, /文本摘要/);
  assert.match(data, /limitations/);
  assert.doesNotMatch(`${diagnosis}${data}`, /raw_trace|stats_source_ref|performance_source_ref|absolute_path/);
});

test("diagnosis keeps descriptive metrics and true empty states inside consistent cards", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const styles = await source("components/task5/task5.module.css");

  assert.match(diagnosis, /summaryMode === "descriptive"/);
  assert.match(diagnosis, /当前缺少可比较标准，只展示本局数值/);
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
  const styles = await source("components/task5/task5.module.css");
  assert.match(data, /getAnalysisData/);
  assert.match(data, /event_distribution/);
  assert.match(data, /target_relative_error_radius/);
  assert.match(data, /onSelectTime/);
  assert.match(data, /tracking_fixed_window: "固定跟踪窗口"/);
  assert.match(data, /tracking_episode: "跟踪片段"/);
  assert.match(data, /low_confidence: "低可信度观测"/);
  assert.match(data, /共 \$\{radiusPoints\.length\} 个样本/);
  assert.match(data, /no_target_visible: "个别帧未检测到目标"/);
  assert.match(data, /return metric\.definition\?\.name \?\? metricReference\(metric\)/);
  assert.match(data, /return metric\.definition\?\.description \?\? null/);
  assert.match(data, /item\.dataset\.metricLabel === selectedMetric/);
  assert.match(data, /referenceKey === "target_switching\.path_efficiency"/);
  assert.match(data, /kill: "击杀"/);
  assert.match(data, /switch_chain: "目标切换链"/);
  assert.match(data, /transition: "开始切换"/);
  assert.match(data, /next_target_acquired: "到达下一目标"/);
  assert.match(data, /settle: "稳定完成"/);
  assert.doesNotMatch(data, /const METRIC_LABELS/);
  assert.match(data, /source\.includes\("tracking-analysis"\)/);
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
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  assert.match(data, /getAnalysisFamilyData/);
  assert.match(data, /frontend_analysis_family_data\.v1/);
  assert.match(data, /switch_chain/);
  assert.match(data, /tracking_fixed_window/);
  assert.match(data, /tracking_change_response/);
  assert.match(data, /static_flick/);
  assert.match(data, /切换到新目标耗时/);
  assert.match(data, /到达后稳定耗时/);
  assert.match(data, /观测到的变向响应/);
  assert.match(data, /加速阶段|减速阶段|稳定阶段/);
  assert.match(data, /peak: "速度峰值"/);
  assert.match(data, /corrective: "修正动作"/);
  assert.match(data, /presentation\.video\.kind === "seekable"/);
  assert.match(data, /加载更多/);
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
