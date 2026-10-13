import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COACH_DISPLAY_GUARD_DENSITY_MIN_CHARS,
  COACH_DISPLAY_GUARD_DENSITY_THRESHOLD,
  COACH_DISPLAY_GUARD_MIN_BLOCK_RUN,
  COACH_SEND_MIN_REPLACEMENT_CHARS,
  countReplacementChars,
  detectCorruptedReply,
} from "./coach-display-guard";

// 显示层块字符守卫（lib/coach-display-guard.ts）：锁定 run/密度两条判据的
// 阈值边界与合并计数口径。█=U+2588，替换符=U+FFFD。

test("run 阈值边界：63 不命中、64 命中", () => {
  const below = detectCorruptedReply("\u2588".repeat(COACH_DISPLAY_GUARD_MIN_BLOCK_RUN - 1));
  assert.equal(below.hit, false);
  assert.equal(below.maxRun, 63);
  const at = detectCorruptedReply("\u2588".repeat(COACH_DISPLAY_GUARD_MIN_BLOCK_RUN));
  assert.equal(at.hit, true);
  assert.equal(at.maxRun, 64);
});

test("█ 与 U+FFFD 合并计数：同一段交错也算一个 run", () => {
  // 32+32 分开不足 64，合并后恰达阈值——锁定合并口径。
  const merged = detectCorruptedReply(
    "\u2588".repeat(32) + "\uFFFD".repeat(32),
  );
  assert.equal(merged.hit, true);
  assert.equal(merged.maxRun, 64);
  // 被正常字符隔开的两段不合并：40+40 仍不命中（文本 <120，密度路径不参与）。
  const separated = detectCorruptedReply(
    "\u2588".repeat(40) + "abc" + "\u2588".repeat(40),
  );
  assert.equal(separated.hit, false);
  assert.equal(separated.maxRun, 40);
});

test("密度路径：≥120 字符且占比 ≥30% 命中，29% 不命中", () => {
  assert.ok(COACH_DISPLAY_GUARD_DENSITY_MIN_CHARS === 120);
  assert.ok(COACH_DISPLAY_GUARD_DENSITY_THRESHOLD === 0.3);
  // 42 块 + 98 正常 = 140 字符，密度恰 0.30 → 命中；run 只有 1。
  const at = detectCorruptedReply("x\u2588".repeat(42) + "y".repeat(56));
  assert.equal(at.len, 140);
  assert.equal(at.maxRun, 1);
  assert.ok(Math.abs(at.density - 0.3) < 1e-9);
  assert.equal(at.hit, true);
  // 41 块 + 99 正常 = 140 字符，密度 ≈0.293 → 不命中。
  const below = detectCorruptedReply("x\u2588".repeat(41) + "y".repeat(58));
  assert.equal(below.len, 140);
  assert.equal(below.hit, false);
  // 密度只算非空白字符：空白不入分母，不稀释占比（120 空格 + 42 块 + 98 字母仍恰 0.30 命中）。
  const withSpaces = detectCorruptedReply(" ".repeat(120) + "\u2588".repeat(42) + "a".repeat(98));
  assert.equal(withSpaces.len, 260);
  assert.ok(Math.abs(withSpaces.density - COACH_DISPLAY_GUARD_DENSITY_THRESHOLD) < 1e-9);
  assert.equal(withSpaces.hit, true);
});

test("短文本高占比不触发密度路径（避免误伤短回复）", () => {
  const short = detectCorruptedReply("\u2588\u2588\uFFFD\u2588");
  assert.ok(short.len < COACH_DISPLAY_GUARD_DENSITY_MIN_CHARS);
  assert.equal(short.hit, false);
  assert.ok(short.density > COACH_DISPLAY_GUARD_DENSITY_THRESHOLD);
});

test("正常文本不命中（中英混排、标点与换行）", () => {
  const normal = detectCorruptedReply(
    "教练建议先降低灵敏度，再逐步提高命中率。\nTry lowering your sensitivity first, then build up speed.\n",
  );
  assert.equal(normal.hit, false);
  assert.equal(normal.maxRun, 0);
  assert.equal(normal.density, 0);
  assert.equal(detectCorruptedReply("").hit, false);
});

test("countReplacementChars 只数 U+FFFD，不数 █", () => {
  assert.equal(countReplacementChars("\uFFFD\uFFFD\u2588\u2588"), 2);
  assert.equal(countReplacementChars("正常文本"), 0);
  assert.equal(countReplacementChars(""), 0);
  assert.ok(COACH_SEND_MIN_REPLACEMENT_CHARS === 2);
});
