import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 视频面板复盘升级 P1 事件上轴的行为锁定。
// 规格出处：docs/video-pane-revamp-brief.md §二 P1＋§四核查项 1。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("P1 marker data contract maps event types to semantic tokens and excludes estimated corrective", async () => {
  const markers = await source("lib/metric-format.ts");
  // 数据契约收敛为 { timeMs, type, label }；timeMs 由 relative_ms ?? time_s×1000
  // 前端推导，不改底层合同（brief §四核查项 1）。
  assert.match(markers, /export interface TimelineMarker \{/);
  assert.match(markers, /timeMs: number;/);
  assert.match(markers, /type: "kill" \| "miss" \| "peak";/);
  assert.match(markers, /label: string;/);
  assert.match(markers, /event\.relative_ms/);
  assert.match(markers, /Math\.round\(event\.time_s \* 1000\)/);
  // type→语义色桶：kill / miss+death 同桶 / peak；corrective 因后端标注
  // 粗估（corrective_frame_estimated）被排除，其余未知类型 fail-closed。
  assert.match(markers, /kill: "kill",/);
  assert.match(markers, /miss: "miss",/);
  assert.match(markers, /death: "miss",/);
  assert.match(markers, /peak: "peak",/);
  assert.doesNotMatch(markers, /corrective: "corrective"/);
});

test("P1 marker layer sits between progress and cursor, and its hit layer rides above the range input", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const position = (needle: string) => video.indexOf(needle);
  // DOM 栈序＝timelineTrack → timelineProgress → timelineMarkers →
  // timelineCursor → timelineInput；命中层浮在 input 之后（z8）。
  const order = [
    position("styles.timelineTrack"),
    position("styles.timelineProgress"),
    position("styles.timelineMarkers"),
    position("styles.timelineCursor"),
    position("styles.timelineInput"),
    position("styles.timelineMarkerLayer"),
  ];
  for (const index of order) assert.ok(index >= 0);
  assert.ok(order[0] < order[1] && order[1] < order[2] && order[2] < order[3]);
  assert.ok(order[3] < order[4] && order[4] < order[5]);
  // 视觉层是纯展示（不接管指针），语义色全走 --event-* token。
  assert.match(video, /className=\{styles\.timelineMarkers\}/);
  const css = await source("components/task5/task5.module.css");
  assert.match(css, /\.timelineMarkers\s*\{[^}]*pointer-events:\s*none;/s);
  assert.doesNotMatch(css, /\.timelineMarker[^L\w][^}]*#[0-9a-fA-F]{3,8}|rgb\(|hsl\(/);
  assert.match(css, /\.timelineMarker\[data-event="kill"\]\s*\{[^}]*background:\s*var\(--event-kill\);/);
  assert.match(css, /\.timelineMarker\[data-event="miss"\]\s*\{[^}]*background:\s*var\(--event-miss\);/);
  assert.match(css, /\.timelineMarker\[data-event="peak"\]\s*\{[^}]*background:\s*var\(--event-peak\);/);
});

test("P1 color plus shape double channel keeps each marker type recognizable without color", async () => {
  const css = await source("components/task5/task5.module.css");
  // 形状语义：kill=菱形◆、miss=下三角∨、peak=上三角▲（::before clip-path，
  // 颜色之外的第二通道，形状映射与 data-event 一一对应且稳定一致）。
  const kill = css.slice(css.indexOf('.timelineMarker[data-event="kill"]'));
  const missStart = css.indexOf('.timelineMarker[data-event="miss"]');
  const peakStart = css.indexOf('.timelineMarker[data-event="peak"]');
  assert.ok(kill.length > 0 && missStart > -1 && peakStart > -1);
  const killBlock = kill.slice(0, missStart);
  const missBlock = kill.slice(kill.indexOf('[data-event="miss"]'), peakStart);
  const peakBlock = kill.slice(kill.indexOf('[data-event="peak"]'));
  assert.match(killBlock, /clip-path:\s*polygon\(50% 0%, 100% 50%, 50% 100%, 0% 50%\)/);
  assert.match(missBlock, /clip-path:\s*polygon\(0% 0%, 100% 0%, 50% 100%\)/);
  assert.match(peakBlock, /clip-path:\s*polygon\(50% 0%, 100% 100%, 0% 100%\)/);
  // 竖干宽度 2px，符合 brief「绝对定位 2px 竖条插层」。
  assert.match(css, /\.timelineMarker\s*\{[\s\S]*?width:\s*2px;/);
});

test("P1 hover shows the label tooltip and click seeks then pauses on the anchor frame", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const css = await source("components/task5/task5.module.css");
  // hover tooltip：命中按钮携带时间码＋label，样式层走 focus/hover 显隐惯例。
  assert.match(video, /aria-label=\{tip\}/);
  assert.match(video, /formatRelativeTime\(marker\.timeMs\)\} \$\{marker\.label\}/);
  assert.match(video, /role="tooltip"/);
  assert.match(css, /\.timelineMarkerHit:focus-visible \.markerTip/);
  assert.match(
    css,
    /@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.timelineMarkerHit:hover \.markerTip/,
  );
  // 点击＝seek 并暂停在锚点帧（复用 P0 到达链路：直接 seek + pause + arrive 脉冲）。
  assert.match(video, /const seekAndArrive = \(timeMs: number\) => \{/);
  assert.match(
    video,
    /seekAndArrive[\s\S]*?video\.currentTime = clamp\(timeMs, 0, timelineMax\) \/ 1000;[\s\S]*?if \(!video\.paused\) video\.pause\(\);[\s\S]*?setArriveSeq\(\(seq\) => seq \+ 1\);/,
  );
  assert.match(video, /onClick=\{\(\) => seekAndArrive\(marker\.timeMs\)\}/);
  assert.doesNotMatch(video, /seekAndArrive[\s\S]{0,200}void video\.play\(\)/);
});

test("P1 reduced motion degrades the marker tooltip to a static fade", async () => {
  const css = await source("components/task5/task5.module.css");
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  // 无位移的静态淡入：transform 固定居中锚定，时长降为 reduce token；
  // 标记本身无循环动画（无 infinite）。
  assert.match(reduced, /\.markerTip\s*\{[^}]*transition:\s*opacity var\(--duration-reduced-motion\) var\(--ease-out\);[^}]*transform:\s*translate\(-50%, 0\);/s);
  assert.doesNotMatch(css, /infinite/);
});
