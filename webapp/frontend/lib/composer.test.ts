import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COACH_DRAFT_DEBOUNCE_MS,
  activeMentionQuery,
  applyMentionSelection,
  buildMentionCandidates,
  coachDraftStorageKey,
  filterMentionCandidates,
  readCoachDraft,
  stepSentHistory,
  truncateQueuePreview,
  writeCoachDraft,
} from "./composer";

// frontend-parity 批 5（输入框编排）纯助手行为锁定。
// 规格出处：docs/frontend-parity-research-digests.md §11。

function fakeStorage(entries: Map<string, string> = new Map()): Storage {
  return {
    length: entries.size,
    clear: () => entries.clear(),
    getItem: (key: string) => (entries.has(key) ? (entries.get(key) as string) : null),
    key: () => null,
    removeItem: (key: string) => void entries.delete(key),
    setItem: (key: string, value: string) => void entries.set(key, value),
  } as Storage;
}

test("queue chip preview collapses whitespace and truncates at 96 chars", () => {
  assert.equal(truncateQueuePreview("  a\n\nb  c "), "a b c");
  const long = "训".repeat(120);
  const preview = truncateQueuePreview(long);
  assert.equal(preview.length, 97); // 96 + 省略号
  assert.ok(preview.endsWith("…"));
});

test("draft storage keys follow the three-level scope contract with pane suffix", () => {
  assert.equal(coachDraftStorageKey({ kind: "session", sessionId: 12 }), "aiming-cookie.coach-draft.session.12");
  assert.equal(coachDraftStorageKey({ kind: "pending-convo" }), "aiming-cookie.coach-draft.PENDING_CONVO");
  assert.equal(coachDraftStorageKey({ kind: "new-convo" }), "aiming-cookie.coach-draft.NEW_CONVO");
  // 多窗格实例后缀防串写
  assert.equal(
    coachDraftStorageKey({ kind: "session", sessionId: 12 }, "overlay"),
    "aiming-cookie.coach-draft.session.12.pane-overlay",
  );
});

test("coach draft helpers degrade silently without usable storage and empty writes clear", () => {
  assert.equal(readCoachDraft(null, "k"), "");
  const store = fakeStorage();
  writeCoachDraft(store, "k", "草稿");
  assert.equal(readCoachDraft(store, "k"), "草稿");
  writeCoachDraft(store, "k", "");
  assert.equal(readCoachDraft(store, "k"), "");
});

test("debounce constant stays inside the spec window", () => {
  assert.ok(COACH_DRAFT_DEBOUNCE_MS >= 300 && COACH_DRAFT_DEBOUNCE_MS <= 500);
});

test("mention candidates dedupe tokens, labels, and prefer scenario labels over bare refs", () => {
  const candidates = buildMentionCandidates({
    analysisIds: [3, 5, 3],
    scenarioByAnalysisId: { 3: "1wall6targets_small" },
    scenarioNames: ["1wall6targets_small", null, "Gridshot"],
  });
  assert.deepEqual(candidates, [
    { token: "analysis:3", label: "1wall6targets_small" },
    { token: "analysis:5", label: "分析 #5" },
    { token: "Gridshot", label: "Gridshot" },
  ]);
});

test("mention trigger opens on boundary @, closes on whitespace and mid-word @", () => {
  assert.equal(activeMentionQuery("看看 @an", 6), "an"); // caret 紧跟 n
  assert.equal(activeMentionQuery("@", 1), ""); // 空查询＝全量列表
  assert.equal(activeMentionQuery("邮箱 x@analy", 10), null); // 非边界 @ 不触发
  assert.equal(activeMentionQuery("@analysis:3 done", 16), null); // 空格闭合＝token 化完成
  assert.equal(activeMentionQuery("@a b", 4), null);
  assert.equal(activeMentionQuery("", 0), null);
});

test("mention query keeps matching after partial token input", () => {
  assert.equal(activeMentionQuery("参考 @analysis:3 的", 14), "analysis:3");
});

test("selection replaces the fragment, appends the closing space, and moves the caret", () => {
  const result = applyMentionSelection("看看 @ana剩余", 7, "analysis:33");
  assert.equal(result.text, "看看 analysis:33 剩余");
  assert.equal(result.caret, "看看 analysis:33 ".length);
  // 落词后的新光标处不再解析出查询：token 化（尾随空格闭合）防再触发
  assert.equal(activeMentionQuery(result.text, result.caret), null);
});

test("mention filtering matches token or label case-insensitively", () => {
  const candidates = buildMentionCandidates({
    analysisIds: [9],
    scenarioNames: ["Gridshot"],
  });
  assert.deepEqual(filterMentionCandidates(candidates, "grid"), [{ token: "Gridshot", label: "Gridshot" }]);
  assert.deepEqual(filterMentionCandidates(candidates, "ANALYSIS"), [{ token: "analysis:9", label: "分析 #9" }]);
  assert.deepEqual(filterMentionCandidates(candidates, ""), candidates);
});

test("sent history stepping walks from newest to oldest and returns past both ends", () => {
  assert.equal(stepSentHistory(3, null, "up"), 2);
  assert.equal(stepSentHistory(3, 2, "up"), 1);
  assert.equal(stepSentHistory(3, 0, "up"), 0);
  assert.equal(stepSentHistory(3, 1, "down"), 2);
  assert.equal(stepSentHistory(3, 2, "down"), null); // 回到原草稿
  assert.equal(stepSentHistory(0, null, "up"), null);
});
