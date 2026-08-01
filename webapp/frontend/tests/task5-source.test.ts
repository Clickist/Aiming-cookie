import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("analysis route implements one workspace with three title tabs", async () => {
  const page = await source("app/analysis/[analysisId]/page.tsx");
  const workspace = await source("components/task5/AnalysisWorkspace.tsx");
  const primitives = await source("ui/primitives.tsx");
  assert.match(page, /AnalysisWorkspace/);
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
  assert.match(video, /relative_start_ms/);
  assert.match(video, /没有可用视觉证据/);
  assert.match(video, /<video/);
  assert.match(video, /aria-label="分析时间轴"/);
  assert.doesNotMatch(video, /raw_trace|video_path|file:\/\//);
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

test("diagnosis distinguishes current observations from legacy candidate explanations", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const data = await source("components/task5/DataView.tsx");
  assert.match(diagnosis, /priorityReason/);
  assert.match(diagnosis, /rootCauses/);
  assert.match(diagnosis, /presentationKind/);
  assert.match(diagnosis, /claimLabel/);
  assert.match(diagnosis, /重点观察/);
  assert.match(diagnosis, /候选解释/);
  assert.match(diagnosis, /规则化练习建议/);
  assert.match(diagnosis, /历史候选说明/);
  assert.match(diagnosis, /查看证据/);
  assert.match(diagnosis, /查看指标/);
  assert.match(diagnosis, /问 Coach/);
  assert.doesNotMatch(diagnosis, /最需要处理|三层根因|<h4>处方<\/h4>/);
  assert.match(data, /metrics\.formal/);
  assert.match(data, /文本摘要/);
  assert.match(data, /limitations/);
  assert.doesNotMatch(`${diagnosis}${data}`, /raw_trace|stats_source_ref|performance_source_ref|absolute_path/);
});

test("data view consumes the bounded analysis-data projection without a pseudo trend", async () => {
  const data = await source("components/task5/DataView.tsx");
  assert.match(data, /getAnalysisData/);
  assert.match(data, /event_distribution/);
  assert.match(data, /target_relative_error_radius/);
  assert.match(data, /onSelectTime/);
  assert.match(data, /tracking_fixed_window: "固定跟踪窗口"/);
  assert.match(data, /tracking_episode: "跟踪片段"/);
  assert.match(data, /low_confidence: "低可信度观测"/);
  assert.match(data, /共 \$\{radiusPoints\.length\} 个样本/);
  assert.match(data, /"continuous_tracking\.target_relative_error_px": "目标偏差"/);
  assert.match(data, /"continuous_tracking\.time_in_radius_ratio": "目标范围内时间占比"/);
  assert.match(data, /no_target_visible: "个别帧未检测到目标"/);
  assert.match(data, /metricLabel\(metricReference\(metric\)\)/);
  assert.match(data, /item\.dataset\.metricLabel === selectedMetric/);
  assert.match(data, /"target_switching\.transition_time_ms": "切换到新目标耗时"/);
  assert.match(data, /"target_switching\.transition_distance_px": "切换位移"/);
  assert.match(data, /"target_switching\.path_efficiency": "路径效率"/);
  assert.match(data, /"target_switching\.settle_duration_ms": "到达后稳定耗时"/);
  assert.match(data, /referenceKey === "target_switching\.path_efficiency"/);
  assert.match(data, /kill: "击杀"/);
  assert.match(data, /switch_chain: "目标切换链"/);
  assert.match(data, /transition: "开始切换"/);
  assert.match(data, /next_target_acquired: "到达下一目标"/);
  assert.match(data, /settle: "稳定完成"/);
  assert.match(data, /return METRIC_LABELS\[key\] \?\? key/);
  assert.match(data, /source\.includes\("tracking-analysis"\)/);
  assert.match(data, /unavailableMetrics/);
  assert.match(data, /<details className=\{styles\.unavailableMetrics\}>/);
  assert.doesNotMatch(data, /metric\.sources\.join\(" \+ "\)/);
  assert.doesNotMatch(data, /radiusPoints\.map\(\(point\) => <button/);
  assert.doesNotMatch(data, /跨记录趋势|<p className={styles\.sectionKicker}>Trend/);
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
