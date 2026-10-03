import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COACH_TIMEPOINT_DEDUPE_MS,
  COACH_TIMEPOINT_LIMIT,
  normalizeCoachTimestamp,
  projectCoachTimepoints,
  resolveCoachMessageAnalysisRefs,
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

// ── 英文消息分支（教练语言跟随用户消息语言；含汉字消息走中文提取，行为
// 逐字节不变——上面的既有中文断言即 zh 回归面）────────────────────────────

test("english replies label chips with the nearest English phrase before the @time", () => {
  const points = projectCoachTimepoints([
    "Watch @2.3s, the crosshair drifts past the target before the click.",
    "the crosshair settles late @8.4s",
    "watch the flick land @12.6s cleanly",
  ]);
  assert.equal(points[0]?.label, "Watch");
  // 前置词组超上限时按词边界截尾，截点在词中间则丢掉被截半的词。
  assert.equal(points[1]?.label, "settles late");
  assert.equal(points[1]?.label.length, 12);
  assert.equal(points[2]?.label, "flick land");
});

test("english labels fall back to the phrase after the @time, then to the english fallback", () => {
  // 前置无英文词组（裸 @ 开头）→ 取 @time 后的英文词组。
  const after = projectCoachTimepoints(["@2.1s crosshair overcorrection"]);
  assert.equal(after[0]?.label, "crosshair");
  // @time 后紧跟标点再接词组：跳过标点取词组；首词超上限时整词保留（不截半词）。
  const punctuated = projectCoachTimepoints(["@4.0s — micro-correction burst"]);
  assert.equal(punctuated[0]?.label, "micro-correction");
  // 前后都无可用词组（有字母但凑不出 ≥4 字符词组）→ 英文兜底标签。
  const bare = projectCoachTimepoints(["go @3.4s"]);
  assert.equal(bare[0]?.label, "replay");
  // 纯符号/数字消息声明不了语言 → 维持默认中文兜底（既有行为）。
  const symbolOnly = projectCoachTimepoints(["@3.4s。"]);
  assert.equal(symbolOnly[0]?.label, "回看点");
});

test("mixed-language sessions pick the branch per message, not per session", () => {
  const points = projectCoachTimepoints([
    "回看 @4.8s，看那一甩。",
    "watch the flick land @12.6s cleanly",
  ]);
  assert.deepEqual(
    points.map((point) => point.label),
    ["看那一甩", "flick land"],
  );
});

// ── 历史 @time 链接的视频归属（1002 串视频修复）──────────────────────────

test("per-message analysis refs: two sequential mounts land every reply on its own analysis", () => {
  // 常规形态（上一版序数近似漏掉的报障场景）：一次分析回 2 条消息时，第 2 条
  // 曾被错归到下一个分析。时间就近：A1、A2 的 created_at 都在 101 挂载之后、
  // 102 挂载之前 → 都归 101；B1 在 102 挂载之后 → 归 102。挂载时间取挂载
  // run 的 started_at（后端语义），早于该回合全部回复。
  const table = resolveCoachMessageAnalysisRefs(
    [
      { id: 1, role: "user", created_at: "2026-10-01T10:00:00.000Z" },
      { id: 2, role: "assistant", created_at: "2026-10-01T10:00:05.000Z" },
      { id: 3, role: "assistant", created_at: "2026-10-01T10:00:09.000Z" },
      { id: 4, role: "user", created_at: "2026-10-01T10:04:00.000Z" },
      { id: 5, role: "assistant", created_at: "2026-10-01T10:05:06.000Z" },
    ],
    [
      { id: 101, attached_at: "2026-10-01T09:59:59.000Z" },
      { id: 102, attached_at: "2026-10-01T10:04:59.000Z" },
    ],
  );
  assert.equal(table.get(2), "analysis:101");
  assert.equal(table.get(3), "analysis:101");
  assert.equal(table.get(5), "analysis:102");
  // 用户消息不参与归属。
  assert.equal(table.has(1), false);
  assert.equal(table.has(4), false);
});

test("per-message analysis refs: re-mounting an old analysis re-attributes later replies to it", () => {
  // 回挂旧分析：后端把 101 的 attached_at 刷新并移到台账末尾，之后的追问归 101。
  const table = resolveCoachMessageAnalysisRefs(
    [
      { id: 1, role: "assistant", created_at: "2026-10-01T10:05:30.000Z" },
      { id: 2, role: "assistant", created_at: "2026-10-01T11:00:10.000Z" },
    ],
    [
      { id: 102, attached_at: "2026-10-01T10:04:59.000Z" },
      { id: 101, attached_at: "2026-10-01T11:00:00.000Z" },
    ],
  );
  assert.equal(table.get(1), "analysis:102");
  assert.equal(table.get(2), "analysis:101");
});

test("per-message analysis refs: replies older than every mount fall to the first mount", () => {
  // 消息早于一切挂载（如挂载前的打招呼回复）：归第一个挂载。
  const table = resolveCoachMessageAnalysisRefs(
    [{ id: 1, role: "assistant", created_at: "2026-10-01T09:00:00.000Z" }],
    [
      { id: 11, attached_at: "2026-10-01T10:00:00.000Z" },
      { id: 22, attached_at: "2026-10-01T11:00:00.000Z" },
    ],
  );
  assert.equal(table.get(1), "analysis:11");
});

test("per-message analysis refs: a single mount keeps every reply on it, empty refs degrade to null", () => {
  // 单挂载会话：所有回复都归它（与修复前行为一致）。
  const single = resolveCoachMessageAnalysisRefs(
    [
      { id: 1, role: "assistant", created_at: "2026-10-01T10:00:05.000Z" },
      { id: 2, role: "assistant", created_at: "2026-10-01T10:01:00.000Z" },
    ],
    [{ id: 7, attached_at: "2026-10-01T09:59:59.000Z" }],
  );
  assert.equal(single.get(1), "analysis:7");
  assert.equal(single.get(2), "analysis:7");
  // 无主题挂载：返回 null，由调用方走深读兜底链。
  const none = resolveCoachMessageAnalysisRefs(
    [{ id: 1, role: "assistant", created_at: "2026-10-01T10:00:00.000Z" }],
    [],
  );
  assert.equal(none.get(1), null);
});

test("per-message analysis refs: legacy sessions without mount times keep the ordinal approximation", () => {
  // 升级前的旧会话只有并集 id 列表（attached_at 全空）：退回上一版的序数近似，
  // 旧会话行为不回退。
  const table = resolveCoachMessageAnalysisRefs(
    [
      { id: 1, role: "assistant", created_at: "2026-10-01T10:00:00.000Z" },
      { id: 2, role: "assistant", created_at: "2026-10-01T10:01:00.000Z" },
      { id: 3, role: "assistant", created_at: "2026-10-01T10:02:00.000Z" },
    ],
    [{ id: 11, attached_at: "" }, { id: 22, attached_at: "" }],
  );
  assert.equal(table.get(1), "analysis:11");
  assert.equal(table.get(2), "analysis:22");
  assert.equal(table.get(3), "analysis:22");
});

test("normalizeCoachTimestamp unifies ISO and sqlite UTC stamps onto one epoch timeline", () => {
  // ISO 8601（pi 会话条目与 meta attached_at 的实际形态）。
  assert.equal(normalizeCoachTimestamp("2026-10-01T10:00:00.000Z"), Date.parse("2026-10-01T10:00:00.000Z"));
  // 带时区偏移的 ISO 折算到同一时间线（+08:00 的 18 点 = UTC 10 点）。
  assert.equal(normalizeCoachTimestamp("2026-10-01T18:00:00+08:00"), Date.parse("2026-10-01T10:00:00.000Z"));
  // sqlite CURRENT_TIMESTAMP 形态（无时区）：必须按 UTC 解析，不落本地时区。
  assert.equal(normalizeCoachTimestamp("2026-10-01 10:00:00"), Date.parse("2026-10-01T10:00:00.000Z"));
  assert.equal(normalizeCoachTimestamp("2026-10-01 10:00:00.500"), Date.parse("2026-10-01T10:00:00.500Z"));
  // 空串/缺参/乱码 → null（挂载时间未知）。
  assert.equal(normalizeCoachTimestamp(""), null);
  assert.equal(normalizeCoachTimestamp("  "), null);
  assert.equal(normalizeCoachTimestamp(undefined), null);
  assert.equal(normalizeCoachTimestamp("not-a-time"), null);
});
