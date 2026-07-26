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

test("diagnosis and data views expose evidence and limitations without unsafe fields", async () => {
  const diagnosis = await source("components/task5/DiagnosisView.tsx");
  const data = await source("components/task5/DataView.tsx");
  assert.match(diagnosis, /priorityReason/);
  assert.match(diagnosis, /rootCauses/);
  assert.match(diagnosis, /查看证据/);
  assert.match(data, /metrics\.formal/);
  assert.match(data, /文本摘要/);
  assert.match(data, /limitations/);
  assert.doesNotMatch(`${diagnosis}${data}`, /raw_trace|stats_source_ref|performance_source_ref|absolute_path/);
});

test("analysis components use frozen tokens and no raw colors", async () => {
  const css = await source("components/task5/task5.module.css");
  assert.match(css, /var\(--surface/);
  assert.match(css, /var\(--outline-variant\)/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}|rgb\(|hsl\(/);
});
