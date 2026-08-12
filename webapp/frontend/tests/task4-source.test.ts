import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");
async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("history page is a light list and does not render full result payloads or benchmark UI", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /getHistorySessions/);
  assert.match(value, /getHistoryAnalysisDetail/);
  assert.match(value, /historyEvidenceState/);
  assert.match(value, /detail\.history/);
  assert.match(value, /visual_replay/);
  assert.doesNotMatch(value, /Benchmark|Plotly|result\.deterministic|video_url/);
});

test("history preserves stale rows when refresh fails", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /refreshing|unavailable|旧内容|保留/);
});

test("history renders unavailable Run sources as semantic notices", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /function RunSectionState/);
  assert.match(value, /<Notice tone="warning" title={title}>/);
  assert.match(value, /runDiscovery === "browser_unavailable" \|\| runDiscovery === "service_unavailable"/);
});

test("history loading and empty states use the local panel treatment", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  assert.equal(client.match(/className="task4-panel task4-state-panel"/g)?.length, 2);
  assert.match(styles, /\.task4-state-panel\s*{[\s\S]*min-height:\s*88px;[\s\S]*padding:\s*18px 20px;/);
});

test("history keeps refresh, Coach return, and delegates analysis to Coach", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  assert.match(client, /<Button onClick=\{\(\) => void loadHistory\(\)\} size="compact" variant="ghost">刷新<\/Button>/);
  assert.doesNotMatch(client, /新建分析/);
  assert.match(client, /让 Coach 分析/);
  assert.match(client, /aiming-cookie:coach-draft/);
  assert.match(client, /publishCoachIntent/);
  assert.match(client, /const askCoachToAnalyze[\s\S]*?kind: "batch-analysis"[\s\S]*?id: run\.id[\s\S]*?router\.push\("\/"\);/);
  assert.doesNotMatch(client, /publishCoachIntent\(\{ draft: `请分析这次训练/);
  assert.match(styles, /@media \(min-width: 840px\) and \(max-width: 1159px\)[\s\S]*\.task3-workspace\[data-coach-open="true"\] \.task4-page-head[\s\S]*width:\s*calc\(100% - var\(--task3-coach-width, 360px\)\);[\s\S]*flex-wrap:\s*wrap;/);
});

test("history supports selecting and batch-attaching completed analyses to Coach", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  assert.match(client, /attachCoachContext/);
  assert.match(client, /publishCoachIntent/);
  assert.match(client, /selectedAnalysisRefs/);
  assert.match(client, /router\.push\("\/"\)/);
  assert.match(client, /type="checkbox"/);
  assert.match(client, /disabled=\{!canAttach\}/);
  assert.match(client, /Promise\.allSettled\(/);
  assert.match(client, /引用所选分析/);
  assert.match(styles, /\.task4-row-select\s*\{/);
});

test("history and run inspector do not expose path or raw trace fields", async () => {
  const value = await source("components/task4/RunInspector.tsx");
  assert.doesNotMatch(value, /stats_source_ref|performance_source_ref|trace_artifact_ref|trace_error/);
  assert.match(value, /data-operation=\"manage_storage\"/);
  assert.match(value, /disabled variant=\"secondary\"/);
});

test("history never promotes an analysis summary into the scenario title", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /presentRecordLabel\(\{[\s\S]*scenario: session\.scenario/);
  assert.doesNotMatch(value, /scenario:\s*session\.summary_label/);
});

test("History keeps Analysis consumption local or sends it to Coach", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.doesNotMatch(value, /analysisHref\(/);
  assert.doesNotMatch(value, /href=\{[^}]*\/analysis/);
  assert.match(value, /onLoadDetail\(session\.id\)/);
  assert.match(value, /attachSelectedAnalyses/);
});
