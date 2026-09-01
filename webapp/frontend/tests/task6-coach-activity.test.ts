import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import { settleTerminalWorkSegments, type CoachWorkSegment } from "../components/task6/CoachRunActivity";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("terminal settlement freezes a trailing streaming thinking segment in place", () => {
  const segments: CoachWorkSegment[] = [
    { kind: "thinking", key: "t1", text: "半截思考", streaming: true, startedAtMs: 1_200, frozenMs: null },
    { kind: "tool", step: { key: "s1", label: "读取文件", meta: null, state: "active", command: "read" } },
  ];
  const settled = settleTerminalWorkSegments(segments, 2_000);
  // 思考段：流式终止、终文保留、时长按冻结时刻补算（0.8 秒）
  const thinking = settled[0];
  assert.ok(thinking?.kind === "thinking");
  assert.equal(thinking.streaming, false);
  assert.equal(thinking.frozenMs, 800);
  assert.equal(thinking.text, "半截思考");
  // 活动工具步按 stepFromToolEvent 的 cancelled→fail 同款语义收尾
  const tool = settled[1];
  assert.ok(tool?.kind === "tool");
  assert.equal(tool.step.state, "fail");
});

test("terminal settlement leaves already settled segments untouched", () => {
  const frozen: CoachWorkSegment = {
    kind: "thinking", key: "t1", text: "已冻结", streaming: false, startedAtMs: null, frozenMs: 400,
  };
  const done: CoachWorkSegment = {
    kind: "tool",
    step: { key: "s1", label: "查询训练记录", meta: null, state: "done", command: "run.list", durationMs: 1_200 },
  };
  const settled = settleTerminalWorkSegments([frozen, done], 9_000);
  assert.deepEqual(settled, [frozen, done]);
});

test("Coach panel consumes SSE thinking_text into the interleaved work stream", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // partial 帧字段接入：thinking_text＝当前思考段全文，写进 liveSegments
  assert.match(panel, /thinking_text\?: unknown/);
  assert.match(panel, /text: thinking/);
  // 0828 修复：帧内 thinking/text 交错——正文增量不得冻结思考段（开段只由
  // 轮边界 activity 驱动），否则每帧交错都会撕裂出新段。
  assert.match(panel, /正文增量只更新正文/);
  assert.doesNotMatch(panel, /text\.length > 0\)[\s\S]{0,120}freezeThinkingSegment/);
  // activity 驱动的实时段推进（thinking started＝新段，tool＝步骤入列）
  assert.match(panel, /applyLiveActivity\(streamedEvent\)/);
  assert.match(panel, /const settledSegments = source/);
  // 成功回合归档而非静默丢弃（0827 拍板：按会话键 Map 缓存，切换会话不丢；
  // 0828 升级：归档交错的思考段/工具段并持久化 localStorage v2，可恢复）
  assert.match(panel, /nextTurns\.set\(activeSessionKeyRef\.current, \{ segments: settledSegments \}\)/);
  assert.match(panel, /persistArchivedTurns\(nextTurns\)/);
  assert.match(panel, /useState<Map<string, ArchivedTurn>>\(readArchivedTurns\)/);
  // 轮询兜底路径共享同一归档收敛
  const settleCount = panel.match(/settleSucceeded\(next\)/g)?.length ?? 0;
  assert.ok(settleCount >= 2, "finalize 与轮询两条终态路径都要走 settleSucceeded");
});

test("streaming answer renders through the same text pipeline as final answers", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const activity = await source("components/task6/CoachRunActivity.tsx");
  // 流式文字不再裸渲染：批7（digests §10）起 partial 与最终答案同走受控富
  // 渲染管线 CoachMessageText；流式光标经 tail 插到续写位，不游离在组件外。
  // （旧断言锁单行 JSX 属性串，因批7 加 tail 属性换行而更新。）
  const at = panel.indexOf("text={run.partial_text}");
  assert.ok(at > -1, "streaming partial must render through CoachMessageText");
  const start = panel.lastIndexOf("<CoachMessageText", at);
  const end = panel.indexOf("/>", at);
  const streamingBlock = panel.slice(Math.max(0, start), end);
  assert.match(streamingBlock, /text=\{run\.partial_text\}/);
  assert.match(streamingBlock, /task6-streaming-cursor/);
  // 归档回合渲染交错的思考段/工具段时序（0828：CoachWorkStream 统一呈现）
  assert.match(panel, /<CoachWorkStream segments=\{archivedTurn\.segments\} \/>/);
  assert.match(panel, /<CoachWorkStream segments=\{workSegments\}/);
  // 思考段与工具段交错（前因后果可读，不再思考一堆动作一堆）
  assert.match(activity, /export type CoachWorkSegment/);
  assert.match(activity, /export function CoachWorkStream/);
  // 思考块标题状态机：流式「思考中」扫光 → 完成后冻结秒数（0828 拍板改文案）
  assert.match(activity, /思考中/);
  assert.match(activity, /思考过程 · 持续了 \$\{Math\.max\(1, Math\.round\(frozenSeconds \/ 1000\)\)\} 秒/);
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

test("failed and stopped runs settle the live work stream instead of leaving it thinking", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 终态收敛接线：workSegments 对非 queued/running 的 run 就地冻结残留流式段
  // （0.1.12 真机修复：失败路径没有 settleSucceeded 的归档清场，且实时段
  // 清空后会从 events 重建——渲染层统一终结，「思考中」不得挂在错误卡片上方）。
  const memoAt = panel.indexOf("const workSegments = useMemo");
  assert.ok(memoAt > -1, "workSegments memo must exist");
  const memoChunk = panel.slice(memoAt, panel.indexOf("}, [liveSegments, run, analysisEtaSeconds]", memoAt));
  assert.match(memoChunk, /settleTerminalWorkSegments\(source, Date\.now\(\)\)/);
  assert.match(memoChunk, /!\["queued", "running"\]\.includes\(run\.status\)/);
  // 纯逻辑在 CoachRunActivity（呈现组件与收敛逻辑同模块，types 就地复用）
  const activity = await source("components/task6/CoachRunActivity.tsx");
  assert.match(activity, /export function settleTerminalWorkSegments/);
});
