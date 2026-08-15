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
  assert.match(value, /historyEvidenceState/);
  // 摘要 Dialog 已移除：不再按需加载完整分析投影。
  assert.doesNotMatch(value, /getHistoryAnalysisDetail|detail\.history|visual_replay/);
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

test("history keeps refresh and Coach return without batch attach", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  assert.match(client, /<Button onClick=\{\(\) => void loadHistory\(\)\} size="compact" variant="ghost">刷新<\/Button>/);
  assert.doesNotMatch(client, /新建分析/);
  assert.doesNotMatch(client, /attachCoachContext|publishCoachIntent|batch-analysis/);
  assert.match(styles, /@media \(min-width: 840px\) and \(max-width: 1159px\)[\s\S]*\.task3-workspace\[data-coach-open="true"\] \.task4-page-head[\s\S]*width:\s*calc\(100% - var\(--task3-coach-width, 360px\)\);[\s\S]*flex-wrap:\s*wrap;/);
});

test("history hands multi-selected runs and analyses to the Coach via the pending-intent draft", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 待分析、训练记录、分析记录共用选择集（上限 5）
  assert.match(value, /const MAX_SELECTED_RUNS = 5;/);
  assert.match(value, /selectedCount >= MAX_SELECTED_RUNS/);
  assert.match(value, /const selectedCount = selectedRunIds\.length \+ selectedAnalysisIds\.length;/);
  // 无任何可用 tier 的训练记录禁用勾选；未完成的分析不可选
  assert.match(value, /disabled=\{run\.supported_input_modes\.length === 0\}/);
  assert.match(value, /disabled=\{session\.status !== "done"\}/);
  // 「让 Coach 分析」拼话术交给 Coach 输入框，用户发送后逐条处理
  assert.match(value, /buildCoachAnalysisDraft\(\{/);
  assert.match(value, /sessionStorage\.setItem\(COACH_PENDING_INTENT_KEY/);
  assert.match(value, /让 Coach 分析/);
  // 详情抽屉与摘要弹窗已移除：入口按钮不再存在
  assert.doesNotMatch(value, /查看 Run/);
  assert.doesNotMatch(value, /查看摘要/);
  assert.doesNotMatch(value, /RunInspector/);
  assert.doesNotMatch(value, /getHistoryAnalysisDetail/);
});

test("history sections order pending first, analyses second, run records last", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const pendingAt = value.indexOf('id="pending-title"');
  const analysisAt = value.indexOf('id="analysis-title"');
  const runsAt = value.indexOf('id="runs-title"');
  assert.ok(pendingAt !== -1 && analysisAt !== -1 && runsAt !== -1, "all three sections must exist");
  assert.ok(pendingAt < analysisAt && analysisAt < runsAt, "section order must be pending → analysis → runs");
});

test("history never promotes an analysis summary into the scenario title", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /presentRecordLabel\(\{[\s\S]*scenario: session\.scenario/);
  assert.doesNotMatch(value, /scenario:\s*session\.summary_label/);
});

test("History keeps Analysis consumption local", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.doesNotMatch(value, /analysisHref\(/);
  assert.doesNotMatch(value, /href=\{[^}]*\/analysis/);
  assert.doesNotMatch(value, /onLoadDetail/);
});
