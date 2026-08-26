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
  // 区块共用空态（RunSectionState）+ 分析记录空态 + watcher 引导卡片两张，共 4 处。
  assert.equal(client.match(/className="task4-panel task4-state-panel"/g)?.length, 4);
  assert.match(styles, /\.task4-state-panel\s*{[\s\S]*min-height:\s*88px;[\s\S]*padding:\s*var\(--space-4\) var\(--space-5\);/);
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

test("History polls incomplete runs and desktop empty states, but not browser empty states", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 终态集合之外（discovered/pending/capturing/finalizing/retryable…）证据还会变化。
  assert.match(value, /new Set\(\["finalized", "source_unavailable", "unavailable"\]\)/);
  assert.match(value, /isDesktopRuntime\(\) && \(runs\.length === 0 \|\| runs\.some\(\(run\) => !RUN_FINALIZED_STATES\.has\(run\.finalization_state\)\)\)/);
  assert.match(value, /if \(!shouldPollHistory\) return undefined;/);
  assert.match(value, /setInterval\(\(\) => void loadHistory\(\), 5000\)/);
  assert.match(value, /clearInterval\(timer\)/);
});

test("history distinguishes desktop empty states with watcher guidance instead of new timers", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // watcher 健康搭既有 loadHistory 轮询的便车，不得引入新的定时器。
  assert.match(value, /getKovaaKLocalDirectories/);
  assert.doesNotMatch(value, /setInterval\((?!.*loadHistory)/);
  // 只有桌面版且全部列表为空、且判定为问题态时才显示；未知/正在摄取不提示。
  assert.match(
    value,
    /runDiscovery === "available" && allListsEmpty && \(watcherStatus === "no_candidates" \|\| watcherStatus === "not_exporting"\)/,
  );
  // 状态 a：未找到目录 → 引导去 设置 → KovaaK 本地目录。
  assert.match(value, /未找到你的 KovaaK 训练数据/);
  assert.match(value, /settings#kovaak-directories/);
  // 状态 b： KovaaK 的实际选项是 Challenge Completion（不存在 "Always"）。
  assert.match(value, /KovaaK 未在导出训练数据/);
  assert.match(value, /请在 KovaaK 中打开 设置 → 其他 → 统计数据输出，选择 Challenge Completion，然后完成一局挑战。/);
  assert.doesNotMatch(value, /Always/);
});
