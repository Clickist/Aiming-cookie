import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 视频面板复盘升级 P2 信号片段循环的源码行为锁定。
// 规格出处：docs/video-pane-revamp-brief.md §二 P2（时间段按钮方案，AB 三态
// 已废弃）＋§三 D2/D4/D5/D6 拍板。数据映射与降级路径的行为断言在
// lib/metric-format.test.ts（与 P1 同一分层惯例）。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("P2 data contract: segment buttons come from evidence-segments projection with peak fallback", async () => {
  const formats = await source("lib/metric-format.ts");
  // 数据核查落点：权威源＝getAnalysisEvidenceSegments 消费的
  // frontend_evidence_segments.v1；播放区间为 preroll 校正后的相对毫秒，
  // availability 门控；降级＝P1 peak 标记 ± 固定窗口。
  assert.match(formats, /export interface SegmentButton \{/);
  assert.match(formats, /id: string;/);
  assert.match(formats, /startMs: number;/);
  assert.match(formats, /endMs: number;/);
  assert.match(formats, /kindLabel: string;/);
  assert.match(formats, /worst: "最差",/);
  assert.match(formats, /typical: "典型",/);
  assert.match(formats, /improved: "改善",/);
  assert.match(formats, /if \(playback\?\.availability !== "available"\) continue;/);
  assert.match(formats, /startMs < 0 \|\| endMs <= startMs/);
  assert.match(formats, /const SIGNAL_SEGMENT_FALLBACK_WINDOW_MS = 2500;/);
  assert.match(formats, /\.filter\(\(marker\) => marker\.type === "peak"\)/);
});

test("P2 row renders at the video bottom from mapped buttons only and hides while unresolved or empty", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 数据链路：getAnalysisEvidenceSegments → 权威投影；空响应触发 peak 降级；
  // 解析中（null）不渲染整排，最终为空也不渲染整排。
  assert.match(video, /getAnalysisEvidenceSegments\(analysisId\)/);
  // 权威负载先存 state，投影在按钮排 memo 里做：扩窗右界的时长钳制随
  // metadata 更新（与降级路径同一 maxMs 机制）。
  assert.match(video, /projectEvidenceSegmentButtons\(evidenceSegments, \{/);
  assert.match(video, /maxMs: durationMs > 0 \? timelineMax : undefined/);
  assert.match(video, /projectPeakFallbackButtons\(timelineMarkers, \{/);
  assert.match(video, /\{signalSegmentButtons\.length > 0 \? \(/);
  // 文案「00:38–00:43 类型词」复用 metric/rich-text 时间码约定。
  assert.match(video, /formatTimecodeRange\(segment\.startMs \/ 1000, segment\.endMs \/ 1000\)/);
  assert.match(video, /aria-label=\{`循环播放 \$\{formatTimecodeRange/);
  assert.match(video, /aria-pressed=\{active\}/);
  // 横向放不下横向滚动（overflow-x: auto），不做聚合归类。
  const css = await source("components/task5/task5.module.css");
  assert.match(css, /\.signalRow\s*\{[^}]*overflow-x:\s*auto;/s);
  assert.doesNotMatch(video, /aggregate|分组|归类按/);
});

test("P2 loop state machine: press loops from start, re-press exits, drag and pause bail out", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 进入：seek 到段起点并开始播放；同枚再按＝退出且不动播放头不代按键。
  assert.match(video, /if \(loopRef\.current\?\.id === segment\.id\) \{\s*exitSignalLoop\(\);\s*return;/);
  assert.match(video, /setLoopTarget\(segment\);\s*seek\(segment\.startMs\);\s*if \(video\.paused\) void video\(\)\.play\(\)|if \(video\.paused\) void video\.play\(\);/);
  // 循环运转：越过段终点回卷起点（保持当前 playbackRate），经 loopRef 免闭包。
  assert.match(video, /onTimeUpdate=\{\(event\) => \{[\s\S]*?const active = loopRef\.current;[\s\S]*?if \(active && ms >= active\.endMs\) \{[\s\S]*?event\.currentTarget\.currentTime = active\.startMs \/ 1000;/);
  // 退出路径②：拖动进度条（onChange 直接操纵刻度）退出循环。
  assert.match(video, /exitSignalLoop\(\);\s*seek\(Number\(event\.currentTarget\.value\)\)/);
  // 退出路径③：任何原生 pause（⏸ 按钮、标记点击暂停、@time 到达、媒体结束）
  // 共用一条路径清空循环目标。
  assert.match(video, /const onPause = \(\) => \{\s*setIsPlaying\(false\);[\s\S]*?loopRef\.current = null;\s*setLoopTarget\(null\);\s*\};/);
  // 速度档位只影响 playbackRate，循环状态不受变速影响（无 setSpeed 处调用 exit）。
  const speedExits = [...video.matchAll(/setSpeed\(([^)]*)\)/g)].filter((match) =>
    /exitSignalLoop/.test(match.input?.slice(match.index, match.index + 200) ?? ""),
  );
  assert.equal(speedExits.length, 0);
});

test("P2 loop visuals: band sits between progress and markers with handles, always-on center timecode", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const position = (needle: string) => video.indexOf(needle);
  // DOM 栈序契约：track → progress → band(P2) → markers(P1) → cursor → input。
  const order = [
    position("styles.timelineTrack"),
    position("styles.timelineProgress"),
    position("styles.timelineBandLayer"),
    position("styles.timelineMarkers"),
    position("styles.timelineCursor"),
    position("styles.timelineInput"),
    position("styles.timelineMarkerLayer"),
  ];
  for (const [index, value] of order.entries()) assert.ok(value >= 0, `order[${index}]`);
  assert.ok(order[0]! < order[1]! && order[1]! < order[2]! && order[2]! < order[3]!);
  assert.ok(order[3]! < order[4]! && order[4]! < order[5]! && order[5]! < order[6]!);

  const css = await source("components/task5/task5.module.css");
  const bandStart = css.indexOf(".timelineBandLayer");
  const bandEnd = css.indexOf("/* ── P1 事件上轴");
  const band = css.slice(bandStart, bandEnd);
  // D4：--event-peak 透明版用 color-mix，无字面量颜色；层禁指针。
  assert.match(band, /\.timelineBand\s*\{[^}]*background:\s*color-mix\(in srgb, var\(--event-peak\) 22%, transparent\);/s);
  assert.match(band, /\.timelineBandLayer\s*\{[^}]*pointer-events:\s*none;/s);
  // 两端 2px 实线把手常亮（::before/::after）。
  assert.match(band, /\.timelineBand::before,\s*\n\.timelineBand::after\s*\{[^}]*width:\s*2px;\s*background:\s*var\(--event-peak\);/s);
  // 中央时间码常显：不被 hover/focus 门控，字体走 token 刻度。
  assert.match(band, /\.timelineBandLabel\s*\{[^}]*font-size:\s*var\(--text-micro\);[\s\S]*transform:\s*translate\(-50%, -50%\);/s);
  assert.doesNotMatch(band, /\.timelineBandLabel[^{]*\{[^}]*opacity:\s*0/);
  // 色带完全静态：block 内无 animation/infinite，reduced-motion 无需新增降级。
  assert.doesNotMatch(band, /animation|infinite/);
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.doesNotMatch(reduced, /timelineBand/);
  // 红线复核：整个文件无 hex/rgb/hsl 字面量、无 grid-template-rows。
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}|rgb\(|hsl\(/);
  assert.doesNotMatch(css, /grid-template-rows/);
});

test("P2 speed dock floats beside the row only while looping and shares the global rate state", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // D6：仅 loopTarget 存在时浮出；直接 setSpeed 与全局变速同一状态源。
  assert.match(video, /\{loopTarget \? \(\s*<div aria-label="循环节奏（与全局变速同步）"/);
  assert.match(video, /onClick=\{\(\) => setSpeed\(step\)\}/);
  assert.match(video, /SPEED_STEPS\.map\(\(step\) => \(/);
  // 全局变速副作用唯一（playbackRate 赋值只有既有 effect 一处），变速坞不自建速率通道。
  assert.match(video, /video\.playbackRate = speed;/);
  assert.equal([...video.matchAll(/video\.playbackRate\s*=/g)].length, 1);
});

test("P2 Esc is an optional quick exit that never hijacks text entry or plain states", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 无循环时不消费不拦截；输入面聚焦时不劫持；IME 与修饰键守卫沿用仓库惯例。
  assert.match(video, /if \(event\.key !== "Escape"\) return;\s*if \(!loopRef\.current\) return;/);
  assert.match(video, /event\.isComposing \|\| event\.keyCode === 229/);
  assert.match(video, /\(tag === "INPUT" && \(target as HTMLInputElement\)\.type !== "range"\)/);
  // 只做内部退出，不吞事件（不影响其他组件的 Esc 语义）。
  const escBlock = video.slice(video.indexOf('event.key !== "Escape"'));
  const block = escBlock.slice(0, escBlock.indexOf("removeEventListener"));
  assert.doesNotMatch(block, /preventDefault/);
});
