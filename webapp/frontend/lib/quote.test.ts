import assert from "node:assert/strict";
import { test } from "node:test";

import {
  QUOTE_HEADER,
  QUOTE_MAX_CHARS,
  SEND_BUDGET_CHARS,
  composeQuotedContent,
  evaluateAssistantSelection,
  isWithinSendBudget,
  parseQuotedContent,
  snapshotQuote,
  stripQuoteMarkers,
  type CoachQuote,
  type SelectionProbeElement,
} from "./quote";

// 划选引用（quote-reply）纯逻辑行为锁定。
// 规格出处：docs/quote-feature-research.md §2.1/§2.2/§3.4/§5.1。

function fakeArticle(attrs: Record<string, string>): SelectionProbeElement {
  return { getAttribute: (name: string) => attrs[name] ?? null };
}

// ── 快照（snapshotQuote）────────────────────────────────────────────────

test("stripQuoteMarkers removes leading blockquote markers and folds CRLF", () => {
  assert.equal(stripQuoteMarkers("> a\r\nb\r\n> c"), "a\nb\nc");
  assert.equal(stripQuoteMarkers(">nested"), "nested");
  // 单独的 ">" 行是空引文行，剥成空串
  assert.equal(stripQuoteMarkers("x\n>\ny"), "x\n\ny");
});

test("snapshotQuote trims, rejects empty, and caps at the per-quote limit", () => {
  assert.deepEqual(snapshotQuote("  引文  "), { ok: true, text: "引文" });
  // 每行只剥一层结构记号："> > x" 保留内层为引文内容
  const nested = snapshotQuote("> > 竖线嵌套");
  assert.ok(nested.ok && nested.text === "> 竖线嵌套");
  assert.equal(snapshotQuote("   \n\t ").ok, false);
  const tooLong = "长".repeat(QUOTE_MAX_CHARS + 1);
  const verdict = snapshotQuote(tooLong);
  assert.equal(verdict.ok, false);
  assert.ok(!verdict.ok && verdict.reason === "too_long");
  // 恰好在上限内的可通过
  assert.equal(snapshotQuote("字".repeat(QUOTE_MAX_CHARS)).ok, true);
});

test("snapshotQuote normalizes line-leading markers before storage", () => {
  const snap = snapshotQuote("第一行\n> 第二行带结构记号");
  assert.ok(snap.ok);
  assert.ok(snap.ok && snap.text === "第一行\n第二行带结构记号");
});

// ── 拼装（composeQuotedContent）与解析（parseQuotedContent）round-trip ──

const q1: CoachQuote = { id: 1, text: "第一段引文第一行\n第二行…" };
const q2: CoachQuote = { id: 2, text: "另一段引文" };

test("composed content matches the research §3.4 shape exactly", () => {
  const composed = composeQuotedContent({ quotes: [q1, q2], text: "帮我解释这句话为什么对。" });
  assert.ok(composed !== null);
  assert.equal(
    composed,
    [
      "[引用 Coach]",
      "> 第一段引文第一行",
      "> 第二行…",
      "",
      "[引用 Coach]",
      "> 另一段引文",
      "",
      "帮我解释这句话为什么对。",
    ].join("\n"),
  );
});

test("quote-only composition is forbidden and empty input yields null (拍板①)", () => {
  assert.equal(composeQuotedContent({ quotes: [q1], text: "   \n" }), null);
  assert.equal(composeQuotedContent({ quotes: [], text: "" }), null);
  // 无引用时原样透出正文
  assert.equal(composeQuotedContent({ quotes: [], text: " 只问一句 " }), "只问一句");
  // quote 对象缺文本的不参与拼装
  assert.equal(composeQuotedContent({ quotes: [{ id: 9, text: "" }], text: "正文" }), "正文");
});

test("parse walks consecutive header chains at index 0 only", () => {
  const parsed = parseQuotedContent(composeQuotedContent({ quotes: [q1, q2], text: "两个问题：其一？" })!);
  assert.deepEqual(parsed.quotes, ["第一段引文第一行\n第二行…", "另一段引文"]);
  assert.equal(parsed.text, "两个问题：其一？");
});

test("parse falls back to verbatim passthrough for legacy or edited content", () => {
  assert.deepEqual(parseQuotedContent("普通历史消息"), { quotes: [], text: "普通历史消息" });
  // 正文里的 header 不是开头链的一部分，保持原样
  const midHeader = `开场白\n${QUOTE_HEADER}\n> 被混进正文的假引用`;
  assert.deepEqual(parseQuotedContent(midHeader), { quotes: [], text: midHeader });
  // 头后没有 blockquote 行＝不是我们的产出形状，整体保守回退
  const orphan = `${QUOTE_HEADER}\n随便什么`;
  assert.deepEqual(parseQuotedContent(orphan), { quotes: [], text: orphan });
});

test("parse survives defensive normalization of line-leading markers in source quotes", () => {
  const snap = snapshotQuote("回答要点\n> 原本带嵌套记号\n>**加粗开头也剥**");
  assert.ok(snap.ok);
  const composed = composeQuotedContent({ quotes: [{ id: 1, text: snap.text }], text: "复述一下" });
  const parsed = parseQuotedContent(composed!);
  assert.deepEqual(parsed.quotes, ["回答要点\n原本带嵌套记号\n**加粗开头也剥**"]);
  assert.equal(parsed.text, "复述一下");
});

test("multi-paragraph quotes keep their internal blank lines through round-trip", () => {
  const composed = composeQuotedContent({ quotes: [{ id: 1, text: "上段\n\n下段" }], text: "问" });
  const parsed = parseQuotedContent(composed!);
  assert.deepEqual(parsed.quotes, ["上段\n\n下段"]);
  assert.equal(parsed.text, "问");
});

// ── 长度预算 ────────────────────────────────────────────────────────────

test("send budget accepts at-limit content and rejects beyond it", () => {
  assert.equal(isWithinSendBudget("字".repeat(SEND_BUDGET_CHARS)), true);
  assert.equal(isWithinSendBudget("字".repeat(SEND_BUDGET_CHARS + 1)), false);
});

test("budget guard uses the composed string including serialized quotes", () => {
  // 正文自身贴着预算，但拼装串加上引文段开销后越线
  const bigBody = "正".repeat(SEND_BUDGET_CHARS - 12);
  const composed = composeQuotedContent({ quotes: [q2], text: bigBody })!;
  assert.ok(composed.length > SEND_BUDGET_CHARS);
  assert.equal(isWithinSendBudget(composed), false);
});

// ── 划选判定（evaluateAssistantSelection）──────────────────────────────

const doneArticle = fakeArticle({ "data-role": "assistant" });
const streamingArticle = fakeArticle({ "data-role": "assistant", "data-streaming": "true" });

test("a completed assistant article with an uncollapsed non-empty selection qualifies", () => {
  const target = evaluateAssistantSelection({
    collapsed: false,
    selectedText: " Coach 的结论 ",
    anchorArticle: doneArticle,
    focusArticle: doneArticle,
  });
  assert.ok(target);
  assert.ok(target!.article === doneArticle);
  assert.equal(target!.text, "Coach 的结论");
});

test("collapsed ranges, whitespace-only text, and cross-article selections never qualify", () => {
  const probe = { collapsed: true, selectedText: "x", anchorArticle: doneArticle, focusArticle: doneArticle };
  assert.equal(evaluateAssistantSelection(probe), null);
  assert.equal(
    evaluateAssistantSelection({ ...probe, collapsed: false, selectedText: "   " }),
    null,
  );
  const another = fakeArticle({ "data-role": "assistant" });
  assert.equal(
    evaluateAssistantSelection({
      collapsed: false,
      selectedText: "跨消息",
      anchorArticle: doneArticle,
      focusArticle: another,
    }),
    null,
  );
});

test("selections anchored in user messages are rejected by role and missing containers", () => {
  const userBubble = fakeArticle({ "data-role": "user" });
  assert.equal(
    evaluateAssistantSelection({
      collapsed: false,
      selectedText: "用户气泡",
      anchorArticle: userBubble,
      focusArticle: userBubble,
    }),
    null,
  );
  assert.equal(
    evaluateAssistantSelection({ collapsed: false, selectedText: "面板空白处", anchorArticle: null, focusArticle: null }),
    null,
  );
});

test("streaming assistant messages are excluded from quoting", () => {
  assert.equal(
    evaluateAssistantSelection({
      collapsed: false,
      selectedText: "正在生成的文字",
      anchorArticle: streamingArticle,
      focusArticle: streamingArticle,
    }),
    null,
  );
});
