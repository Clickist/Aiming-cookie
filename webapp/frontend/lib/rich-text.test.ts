import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_TABLE_COLUMNS,
  parseBoldSegments,
  parseRichText,
  type RichInline,
} from "./rich-text";

// frontend-parity 批 7（受控富渲染，digests §10）受限解析器行为锁定。
// 流式容错：未闭合表格/列表按已收到部分渲染、不吞字；围栏开启后内容
// 隐藏（与 sidecar 归一化同语义）。排印红线：解析层禁做任何空格插写。

const flat = (segments: RichInline[]): string => segments.map((s) => s.text).join("");

test("plain paragraphs pass through verbatim without space insertion", () => {
  const nodes = parseRichText("命中率41.2%，第6杀是转折。");
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].kind, "paragraph");
  assert.equal(flat(nodes[0].kind === "paragraph" ? nodes[0].segments : []), "命中率41.2%，第6杀是转折。");
});

test("paired ** renders bold segments; multi-run paragraphs keep newlines", () => {
  const nodes = parseRichText("先说结论：**主任务全对**。\n然后看细节");
  const p = nodes[0];
  assert.ok(p.kind === "paragraph");
  assert.deepEqual(p.segments.map((s) => [s.text, s.bold]), [
    ["先说结论：", false],
    ["主任务全对", true],
    ["。\n然后看细节", false],
  ]);
});

test("unclosed ** stays literal and swallows nothing (streaming tolerance)", () => {
  assert.deepEqual(parseBoldSegments("半截 **要点还在"), [
    { text: "半截 **要点还在", bold: false },
  ]);
  // 闭合片段到达后同一次重解析即收敛
  assert.deepEqual(parseBoldSegments("半截 **要点还在**"), [
    { text: "半截 ", bold: false },
    { text: "要点还在", bold: true },
  ]);
});

test("ordered lists keep numbering semantics across 1./1)/1、 markers", () => {
  const nodes = parseRichText("1. 热身\n2) 主任务\n3、拉伸");
  assert.equal(nodes.length, 1);
  assert.ok(nodes[0].kind === "list");
  assert.equal(nodes[0].ordered, true);
  assert.equal(nodes[0].items.length, 3);
  assert.equal(flat(nodes[0].items[2].segments), "拉伸");
});

test("unordered lists nest by two-space indent and dedent pops back", () => {
  const nodes = parseRichText("- 甲\n- 乙\n  - 乙一\n  - 乙二\n- 丙");
  const root = nodes[0];
  assert.ok(root.kind === "list" && !root.ordered);
  assert.equal(root.items.length, 3);
  const nested = root.items[1].children;
  assert.equal(nested.length, 1);
  assert.ok(nested[0].kind === "list");
  assert.equal(nested[0].items.length, 2);
  assert.equal(flat(nested[0].items[1].segments), "乙二");
});

test("blank lines between items continue the same loose list", () => {
  const nodes = parseRichText("1. 第一天\n\n2. 第二天");
  assert.ok(nodes[0].kind === "list");
  assert.equal(nodes[0].items.length, 2);
});

test("indented continuation lines append into their item", () => {
  const nodes = parseRichText("- 主项\n  续行说明\n- 下一个");
  const list = nodes[0];
  assert.ok(list.kind === "list");
  assert.equal(list.items.length, 2);
  assert.match(flat(list.items[0].segments), /主项\n续行说明/s);
});

test("GFM tables parse header, alignment row, numeric columns and escaped pipes", () => {
  const nodes = parseRichText(
    "| 场景 | 命中率 | 备注 |\n| --- | ---: | --- |\n| Aim Trainer | 41.2% | 用 \\| 分隔 |\n| Gridshot | 87 | 两列数值",
  );
  assert.equal(nodes.length, 1);
  const t = nodes[0];
  assert.ok(t.kind === "table");
  assert.equal(t.header?.length, 3);
  assert.deepEqual(t.numericCols, [false, true, false]);
  assert.equal(flat(t.rows[0][2]), "用 | 分隔");
});

test("streaming partial tables render what arrived, one piece at a time", () => {
  // 只有表头行（分隔行未到）：按段落保守渲染，不猜列结构也不吞字
  const head = parseRichText("| 场景 | 命中率 |")[0];
  assert.equal(head.kind, "paragraph");
  // 表头＋分隔行已到、数据行只到了一行
  const partial = parseRichText("| 场景 | 命中率 |\n| --- | --- |\n| Gridshot | 87 |")[0];
  assert.ok(partial.kind === "table");
  assert.equal(partial.rows.length, 1);
});

test(`tables wider than ${MAX_TABLE_COLUMNS} columns degrade to faithful paragraphs`, () => {
  const header = ["c1", "c2", "c3", "c4", "c5", "c6"].join(" | ");
  const delim = ["---", "---", "---", "---", "---", "---"].join(" | ");
  const row = ["a", "b", "c", "d", "e", "f"].join(" | ");
  const nodes = parseRawForTest(`| ${header} |\n| ${delim} |\n| ${row} |`);
  assert.equal(nodes.length, 1);
  const p = nodes[0];
  assert.ok(p.kind === "paragraph");
  assert.match(flat(p.segments), /c6/); // 信息保真，整段降级而不是截断
});

test("fence blocks hide content until closed, then disappear entirely", () => {
  const open = parseRawForTest("前文\n```\n代码一半");
  assert.equal(open.length, 1);
  assert.equal(flat((open[0] as { segments: RichInline[] }).segments), "前文");
  const closed = parseRawForTest("前文\n```js\ncode()\n```\n后文");
  assert.equal(closed.length, 2);
  assert.equal(flat((closed[1] as { segments: RichInline[] }).segments), "后文");
});

test("malicious HTML passes through as inert literal data (React escapes downstream)", () => {
  const hostile = '前<b>中<img src=x onerror="alert(1)">后<script>alert(1)</script>';
  const nodes = parseRichText(hostile);
  const joined = nodes
    .flatMap((node) =>
      node.kind === "paragraph"
        ? node.segments
        : node.kind === "list"
          ? node.items.flatMap((item) => item.segments)
          : [],
    )
    .map((segment) => segment.text)
    .join("");
  // 解析器不做 HTML 解释，也没有任何 html 节点种类；原文逐字符透传，
  // 安全边界由 React 转义兜底。
  assert.ok(joined.includes('<img src=x onerror="alert(1)">'));
  assert.ok(!joined.includes("<安全占位"));
});

test("list/table/bold structures coexist inside one reply like a training plan", () => {
  const plan = [
    "本周计划如下：",
    "",
    "1. **热身** 5 分钟",
    "2. 主任务",
    "",
    "| 动作 | 次数 |",
    "| --- | :--- |",
    "| Gridshot | 3 |",
    "",
    "完成后回看 @41.2s",
  ].join("\n");
  const nodes = parseRichText(plan);
  assert.deepEqual(
    nodes.map((node) => node.kind),
    ["paragraph", "list", "table", "paragraph"],
  );
});

// ── helpers ──────────────────────────────────────────────────────────────

function parseRawForTest(text: string): ReturnType<typeof parseRichText> {
  return parseRichText(text);
}
