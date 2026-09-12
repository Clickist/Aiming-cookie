import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COACH_TIMEPOINT_DEDUPE_MS,
  COACH_TIMEPOINT_LIMIT,
  projectCoachTimepoints,
} from "./coach-timepoints";

// 拍板：视频面板底部回看 chips 跟随 Coach 讲解内容（assistant 正文 @time），
// 不再由分析器自动切片驱动 UI。@time 解析复用 lib/rich-text 的
// parseTimeSegments（同一套 TIME_TOKEN_PATTERN），这里锁定投影行为。

test("coach chips reuse the shared @time parser: point/range forms and ms conversion", () => {
  const points = projectCoachTimepoints([
    "回看 @4.8s，看那一甩。",
    "再看 @51.5s。",
    "区间 @38.2-43.7s 这一下。",
  ]);
  assert.deepEqual(
    points.map((point) => point.timeMs),
    [4800, 38200, 51500],
  );
});

test("label takes the nearest Chinese phrase before the @time, truncated to 12 chars", () => {
  const points = projectCoachTimepoints([
    "收尾放慢动作 @4.8s，看那一甩从冲到最后停住。",
    "一二三四五六七八九十甲乙丙丁戊 @8.4s",
  ]);
  assert.equal(points[0]?.label, "收尾放慢动作");
  // @time 前最近连续汉字超过上限时截取尾部 12 字。
  assert.equal(points[1]?.label, "四五六七八九十甲乙丙丁戊");
  assert.equal(points[1]?.label.length, 12);
});

test("label falls back to the phrase after the @time, then to 「回看点」", () => {
  const after = projectCoachTimepoints(["回看 @2.1s 对比一下"]);
  assert.equal(after[0]?.label, "对比一下");
  // 前后都无可用中文上下文（纯符号环境）→ 兜底标签。
  const bare = projectCoachTimepoints(["@3.4s。"]);
  assert.equal(bare[0]?.label, "回看点");
});

test("merges all assistant messages, sorts ascending and dedupes within ±300ms keeping the first", () => {
  const points = projectCoachTimepoints([
    "先说 @9.1s 这次。",
    "再看 @4.8s 和 @4.7s、@14.3s。",
  ]);
  assert.deepEqual(
    points.map((point) => point.timeMs),
    [4800, 9100, 14300],
  );
  assert.equal(COACH_TIMEPOINT_DEDUPE_MS, 300);
  // 保留先出现的（消息序在前者：@4.8s 先于 @4.7s）。
  assert.equal(points[0]?.id, "coach-1-0");
});

test("caps the merged multi-turn list at eight points", () => {
  const text = Array.from({ length: 10 }, (_, index) => `第${index}处 @${index + 1}.0s 看这里`).join("；");
  const points = projectCoachTimepoints([text]);
  assert.equal(COACH_TIMEPOINT_LIMIT, 8);
  assert.equal(points.length, 8);
  assert.deepEqual(
    points.map((point) => point.timeMs),
    [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000],
  );
});

test("empty or anchor-free input yields no chips (renderer falls back to evidence segments)", () => {
  assert.deepEqual(projectCoachTimepoints([]), []);
  assert.deepEqual(projectCoachTimepoints(["这局没有时间锚点，纯口头讲解。"]), []);
});

test("maxMs drops anchors beyond the video duration once metadata is known", () => {
  const points = projectCoachTimepoints(["@4.8s 开头 @14.3s 中段 @51.5s 末尾"], { maxMs: 20000 });
  assert.deepEqual(
    points.map((point) => point.timeMs),
    [4800, 14300],
  );
  // 时长未知（undefined）时不丢锚点，交给播放层 clamp。
  assert.equal(projectCoachTimepoints(["@51.5s 末尾"]).length, 1);
});
