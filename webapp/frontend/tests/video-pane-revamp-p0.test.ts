import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import { formatTimecode, formatTimecodeRange } from "../lib/rich-text";

// 视频面板复盘升级 P0 精读三件套的行为锁定。
// 规格出处：docs/video-pane-revamp-brief.md §二 P0（D1/D7 已拍板）。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  // 统一换行符为 LF 再供断言。为什么：下方有跨行正则（如 frameStepRef 与
  // fpsSampledRef 两连行）按 `\n` 匹配，而 Windows 检出的源码是 CRLF（\r\n），
  // 多出的 \r 会让正确的代码在 Windows 上稳定误报失败（2026-08-29 起的既有坑，
  // 2026-09-06 修复）。归一后本文件的断言对两种换行符都能通过。
  const raw = await readFile(path.join(root, relativePath), "utf8");
  return raw.replace(/\r\n/g, "\n");
}

test("P0.1 frame stepping: ,/. keys plus buttons step by inferred fps with 33ms fallback", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 帧长兜底常量 33ms（≈30fps）；Shift 加倍粗调；按钮与键盘共用同一 stepFrame。
  assert.match(video, /const FRAME_STEP_FALLBACK_MS = 33/);
  assert.match(video, /const FRAME_COARSE_FACTOR = 2;/);
  assert.match(video, /const stepFrame = \(direction: number, coarse: boolean\)/);
  assert.match(video, /stepFrameRef\.current\(event\.key === "," \? -1 : 1, event\.shiftKey\)/);
  assert.match(video, /aria-label="后退一帧"/);
  assert.match(video, /aria-label="前进一帧"/);
  // 帧率推算：HTML 标准不暴露 fps，用 getVideoPlaybackQuality 差分推算，
  // 暂停中/窗口过短不编造，换源重置回兜底。
  assert.match(video, /getVideoPlaybackQuality/);
  assert.match(video, /totalVideoFrames - base\.totalVideoFrames/);
  assert.match(video, /if \(!video\.paused && elapsed >= 300 && frames > 0\)/);
  assert.match(video, /frameStepRef\.current = FRAME_STEP_FALLBACK_MS;\n\s+fpsSampledRef\.current = false;/);
});

test("P0.1 keyboard stepping stays scoped to the visible video pane and never hijacks global letter keys", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 监听器只存在于本组件挂载期（组件只在视频面板内渲染），随卸载移除。
  assert.match(video, /document\.addEventListener\("keydown", onKeyDown\)/);
  assert.match(video, /document\.removeEventListener\("keydown", onKeyDown\)/);
  assert.doesNotMatch(video, /window\.addEventListener\("keydown"/);
  // composer / 一切文本输入面聚焦时不抢键；IME 守卫与仓库惯例一致。
  assert.match(video, /target\.isContentEditable/);
  assert.match(video, /\(tag === "INPUT" && \(target as HTMLInputElement\)\.type !== "range"\)/);
  assert.match(video, /event\.isComposing \|\| event\.keyCode === 229/);
});

test("P0.1 legacy +/-5s jumps survive only as button long-press, not as the primary step", async () => {
  const video = await source("components/task5/VideoView.tsx");
  // 长按 ±5s 连发＋延迟阈值；旧固定 ±5s 的直接按钮 onClick 已消失。
  assert.match(video, /const LONG_STEP_MS = 5000;/);
  assert.match(video, /beginHoldJump\(-1\)/);
  assert.match(video, /beginHoldJump\(1\)/);
  assert.match(video, /HOLD_REPEAT_MS\)/);
});

test("P0.2 speed control cycles 0.25x -> 0.5x -> 1x and applies playbackRate immediately", async () => {
  const video = await source("components/task5/VideoView.tsx");
  assert.match(video, /const SPEED_STEPS = \[0\.25, 0\.5, 1\];/);
  assert.match(video, /SPEED_STEPS\[\(index \+ 1\) % SPEED_STEPS\.length\]/);
  assert.match(video, /aria-label=\{`播放速度 \$\{speed\}×，点击切换到下一档`\}/);
  assert.match(video, /变速循环：0\.25× → 0\.5× → 1×/);
  // 切换即时作用并随换源恢复：既有 playbackRate 副作用保持。
  assert.match(video, /video\.playbackRate = speed/);
});

test("P0.3 chat @time arrival pauses the video and pulses the playhead once", async () => {
  const video = await source("components/task5/VideoView.tsx");
  const pane = await source("components/task7/CoachVideoPane.tsx");
  const css = await source("components/task5/task5.module.css");
  // 跳转意图显式信号链路：AppShell 每次点击递增序号，pane 透传给播放器——
  // 同一时间码重复点击也构成新意图，不依赖 initialTimeMs 值变化。
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /setVideoTarget\(\{ analysisRef, seq: \(videoSeqRef\.current \+= 1\), timeMs \}\)/);
  assert.match(pane, /jumpSeqRef\.current = Math\.max\(jumpSeqRef\.current \+ 1, jumpSeq\);/);
  assert.match(pane, /setJumpTarget\(\{ seq: jumpSeqRef\.current, ms: initialTimeMs \}\)/);
  assert.match(pane, /jumpTarget=\{jumpTarget\}/);
  // 到达即暂停（手动拖动走 onChange → seek 自身通道，不受影响）；
  // ms=0 是普通打开入口，不暂停。
  assert.match(
    video,
    /if \(!jumpTarget \|\| jumpTarget\.ms <= 0\) return;[\s\S]*?video\.currentTime = jumpTarget\.ms \/ 1000;[\s\S]*?if \(!video\.paused\) video\.pause\(\);/,
  );
  // 一次性高亮脉冲：合同合规写法＝完整阴影 token var(--ring)；
  // key 重放让连续点击重复触发；reduced-motion 下关动画只留静态高亮一次。
  assert.match(video, /data-arrive=\{arriveActive \? "true" : undefined\}/);
  assert.match(video, /key=\{`cursor-\$\{arriveSeq\}`\}/);
  assert.match(
    css,
    /\.timelineCursor\[data-arrive="true"\]\s*\{[^}]*box-shadow:\s*var\(--ring\);[^}]*animation:\s*timelineCursorArrive var\(--duration-surface\) var\(--ease-out\) 2;/,
  );
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reduced, /\.timelineCursor\[data-arrive="true"\]\s*\{\s*animation:\s*none;/);
  // keyframes 只动 opacity（有限两拍非循环），无裸颜色、无循环动画。
  const keyframes = css.slice(css.indexOf("@keyframes timelineCursorArrive"));
  assert.match(
    keyframes,
    /timelineCursorArrive \{\s*0% \{[\s\S]*opacity: 1;[\s\S]*50% \{[\s\S]*opacity: 0\.4;[\s\S]*100% \{[\s\S]*opacity: 1;/,
  );
  assert.doesNotMatch(keyframes, /infinite/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}|rgb\(|hsl\(/);
});

test("P0.3/D7 message time markers render as timecodes while keeping task6-time-link colors", async () => {
  const renderer = await source("components/task7/CoachMessageText.tsx");
  // 解析与格式化收敛到 lib/rich-text（单点/区间一个正则）。
  assert.match(renderer, /parseTimeSegments/);
  assert.match(renderer, /from "@\/lib\/rich-text"/);
  assert.doesNotMatch(renderer, /TIME_POINT_PATTERN/);
  // chip 展示文案是格式化时间码，不再透出原文 @51.5s。
  assert.match(renderer, /\{chip\.label\}/);
  // 点击行为不变：跳转＋暂停；区间跳起点。既有语义色类两个都保留。
  assert.match(renderer, /function chipTargetMs\(chip: TimeChip\): number/);
  assert.match(renderer, /chip\.kind === "range" \? chip\.startMs : chip\.timeMs/);
  assert.match(renderer, /className="task6-time-link"/);
  assert.match(renderer, /task6-time-link task6-time-link--static/);

  // 格式化行为抽查（深度断言在 lib/rich-text 单测）。
  assert.equal(formatTimecode(51.5), "00:51.5");
  assert.equal(formatTimecode(3671.2), "1:01:11");
  assert.equal(formatTimecodeRange(38.2, 43.7), "00:38–00:43");
});
