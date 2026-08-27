import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach panel consumes SSE thinking_text into the collapsible thinking block", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // partial 帧字段接入
  assert.match(panel, /thinking_text\?: unknown/);
  assert.match(panel, /setLiveThinking\(thinking\)/);
  // 首个回答 token 冻结思考窗口，供归档展示
  assert.match(panel, /frozenMs = Date\.now\(\) - thinkingTrackerRef\.current\.startAt/);
  // 成功回合归档而非静默丢弃
  assert.match(panel, /setArchivedTurn\(\{ run: next/);
  // 轮询兜底路径共享同一归档收敛
  const settleCount = panel.match(/settleSucceeded\(next\)/g)?.length ?? 0;
  assert.ok(settleCount >= 2, "finalize 与轮询两条终态路径都要走 settleSucceeded");
});

test("streaming answer renders through the same text pipeline as final answers", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const activity = await source("components/task6/CoachRunActivity.tsx");
  // 流式文字不再裸渲染
  assert.match(
    panel,
    /<CoachMessageText text=\{run\.partial_text\} analysisRef=\{defaultAnalysisRef\} onOpenVideo=\{onOpenVideo\} \/>/,
  );
  // 归档回合保留活动摘要与思考秒数
  assert.match(panel, /archivedTurn\.thinkingMs/);
  assert.match(panel, /deriveToolSteps\(archivedTurn\.run\)/);
  // 思考块标题状态机：进行中→完成后冻结秒数
  assert.match(activity, /正在思考/);
  assert.match(activity, /已思考 \$\{Math\.max\(1, Math\.round\(frozenSeconds \/ 1000\)\)\} 秒/);
});

test("tool steps surface duration and detail previews from existing contract fields", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const activity = await source("components/task6/CoachRunActivity.tsx");
  // 面板解析这些 sidecar 已下发但旧版被丢弃的字段
  for (const field of ["args_preview", "result_preview", "duration_ms"]) {
    assert.match(panel, new RegExp(`payload\\.${field}`));
  }
  // 组件呈现：完成步耗时、可展开明细、aria-expanded 可达性
  assert.match(activity, /formatDuration\(step\.durationMs\)/);
  assert.match(activity, /task6-tool-detail-toggle/);
  assert.match(activity, /aria-expanded/);
});

test("long analysis steps tick elapsed time next to the local ETA", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /etaSeconds: analysisEtaSeconds/);
  // pending chip 计时基准：started_at 优先，否则入队时间
  assert.match(panel, /Date\.parse\(item\.started_at \?\? item\.created_at\)/);
  const activity = await source("components/task6/CoachRunActivity.tsx");
  assert.match(activity, /预计约 \{etaSeconds\} 秒/);
});

test("working-state motion loops are disabled under reduced motion", async () => {
  const styles = await source("components/task6/task6.css");
  // 循环动画齐全
  for (const keyframe of ["task6-shimmer", "task6-pulse", "task6-blink", "task6-enter"]) {
    assert.match(styles, new RegExp(`@keyframes ${keyframe}`));
  }
  // reduced-motion 分支明确关闭并保留 shimmer 文字可读性
  const reduced = styles.slice(styles.indexOf("prefers-reduced-motion"));
  assert.match(reduced, /\.task6-shimmer-text,\s*\n\s*\.task6-pulse-dot/);
  assert.match(reduced, /background:\s*none;\s*\n\s*color: var\(--on-surface-variant\)/);
  // 不再引用不存在的旧伪元素动画（死规则清理）
  assert.doesNotMatch(reduced, /task6-tool-dot::after/);
});
