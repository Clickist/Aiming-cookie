import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_TABLE_COLUMNS,
  formatTimecode,
  formatTimecodeRange,
  parseBoldSegments,
  parseRichText,
  parseTimeSegments,
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

// ── 受控命名链接（购买链接呈现，2026-09-11）──────────────────────────────

test("whitelisted https named link parses into a link segment", () => {
  const segments = parseBoldSegments("淘宝在这：[Darmoshark M3 Micro Pro](https://s.click.taobao.com/t?e=m%3D2%26s%3Dx) 自己看");
  assert.equal(segments.length, 3);
  assert.equal(segments[0].text, "淘宝在这：");
  assert.equal(segments[1].link, "https://s.click.taobao.com/t?e=m%3D2%26s%3Dx");
  assert.equal(segments[1].text, "Darmoshark M3 Micro Pro");
  assert.equal(segments[2].text, " 自己看");
});

test("non-whitelisted host and http links stay literal text", () => {
  const text = "a [x](https://evil.example.com/t) b [y](http://s.click.taobao.com/t) c";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
  assert.ok(segments.every((s) => !s.link));
});

test("unclosed link shape stays literal mid-stream, nothing swallowed", () => {
  const text = "链接在这：[Darmoshark M3 Micro Pro](https://s.click.taobao.com/t?e=x";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
});

test("javascript: and bare-domain shapes never become links", () => {
  const text = "[点我](javascript:alert(1)) 和 [x](https://sub.item.jd.com/a.html)";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
  assert.ok(segments.every((s) => !s.link));
});

test("whitelisted bare https URL links through (identity answer fallback, 1.0.0)", () => {
  const text = "他的主页在这里：https://space.bilibili.com/14425468 ，想关注的话可以去看看。";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
  const link = segments.find((s) => s.link);
  assert.ok(link);
  assert.equal(link.link, "https://space.bilibili.com/14425468");
  assert.equal(link.text, "https://space.bilibili.com/14425468");
});

test("bare URL at end of text and before newline both terminate cleanly", () => {
  const segments = parseBoldSegments("主页：https://space.bilibili.com/14425468");
  assert.ok(segments.some((s) => s.link === "https://space.bilibili.com/14425468"));
  const multiline = parseBoldSegments("第一行 https://item.jd.com/1000123.html\n第二行");
  assert.ok(multiline.some((s) => s.link === "https://item.jd.com/1000123.html"));
});

test("non-whitelisted bare https URL stays literal text", () => {
  const text = "先看 https://evil.example.com/x 就好";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
  assert.ok(segments.every((s) => !s.link));
});

// ── steam:// 深链（社区基准训练单，2026-09-13 intro session）─────────────

test("bare steam:// deep link links through with query separators intact", () => {
  const url =
    "steam://run/824270/?action=jump-to-playlist;sharecode=KovaaKsScreamingPulledEgg";
  const segments = parseBoldSegments(`装这套训练单：${url} 完事。`);
  const link = segments.find((s) => s.link);
  assert.ok(link);
  assert.equal(link.link, url);
  assert.equal(link.text, url);
});

test("named steam:// link parses even without an https shape", () => {
  const segments = parseBoldSegments(
    "[一键装进 KovaaK](steam://run/824270/?action=jump-to-playlist;sharecode=KovaaKsScreamingPulledEgg)",
  );
  assert.equal(segments.length, 1);
  assert.equal(segments[0].text, "一键装进 KovaaK");
  assert.match(segments[0].link ?? "", /^steam:\/\/run\/824270\//);
});

test("other custom schemes and host-less steam links stay literal text", () => {
  const text = "别点 steam: 与 file:///etc/passwd 和 ftp://example.com/x";
  const segments = parseBoldSegments(text);
  assert.equal(flat(segments), text);
  assert.ok(segments.every((s) => !s.link));
});

// ── 引用块（结构化「下一步」降级形态）────────────────────────────────────

test("consecutive blockquote lines fold into one blockquote node", () => {
  const nodes = parseRichText("正文\n\n> 下一步\n> 主攻甩枪类\n\n尾段");
  assert.deepEqual(nodes.map((n) => n.kind), ["paragraph", "blockquote", "paragraph"]);
  const quote = nodes[1];
  assert.ok(quote.kind === "blockquote");
  assert.deepEqual(quote.children.map((c) => c.kind), ["paragraph"]);
  const para = quote.children[0];
  assert.ok(para.kind === "paragraph");
  assert.equal(flat(para.segments), "下一步\n主攻甩枪类");
});

test("unclosed blockquote renders the partial inner content without swallowing", () => {
  const nodes = parseRichText("> 半截引用还在");
  assert.equal(nodes.length, 1);
  assert.ok(nodes[0].kind === "blockquote");
  const para = nodes[0].children[0];
  assert.ok(para.kind === "paragraph");
  assert.equal(flat(para.segments), "半截引用还在");
});

test("named link shape wins over bare URL detection (no double processing)", () => {
  const text = "链接：[B 站主页](https://space.bilibili.com/14425468) 收好";
  const segments = parseBoldSegments(text);
  assert.equal(segments.length, 3);
  assert.equal(segments[0].text, "链接：");
  assert.equal(segments[1].text, "B 站主页");
  assert.equal(segments[1].link, "https://space.bilibili.com/14425468");
  assert.equal(segments[2].text, " 收好");
});

test("bold and named link can coexist in one paragraph", () => {
  const nodes = parseRichText("**第一只** [皂品 Z1 Pro](https://s.click.taobao.com/t?e=z) 209 元");
  const p = nodes[0];
  assert.ok(p.kind === "paragraph");
  const segs = p.segments;
  assert.ok(segs.some((s) => s.bold && s.text === "第一只"));
  assert.ok(segs.some((s) => s.link === "https://s.click.taobao.com/t?e=z"));
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

// ── @time 时间码（视频面板复盘升级 P0.3，brief D7）────────────────────────

test("timecode format keeps one decimal for fractional seconds and pads minutes", () => {
  assert.equal(formatTimecode(51.5), "00:51.5");
  assert.equal(formatTimecode(0), "00:00");
  assert.equal(formatTimecode(38), "00:38");
  assert.equal(formatTimecode(59.96), "01:00"); // 十分位四舍五入进位
  assert.equal(formatTimecode(75.04), "01:15");
});

test("timecode carries into h:mm:ss beyond one hour", () => {
  assert.equal(formatTimecode(3671.2), "1:01:11");
  assert.equal(formatTimecode(3600), "1:00:00");
});

test("timecode range floors both ends to whole seconds with en dash", () => {
  assert.equal(formatTimecodeRange(38.2, 43.7), "00:38–00:43");
  assert.equal(formatTimecodeRange(0, 9.9), "00:00–00:09");
});

test("parseTimeSegments extracts point chips with raw label and millisecond target", () => {
  const segments = parseTimeSegments("先看 @51.5s 的甩枪，再看收枪。");
  assert.deepEqual(
    segments.map((segment) => segment.chip?.kind ?? null),
    [null, "point", null],
  );
  const chip = segments[1].chip;
  assert.ok(chip && chip.kind === "point");
  if (chip && chip.kind === "point") {
    assert.equal(chip.raw, "@51.5s");
    assert.equal(chip.label, "00:51.5");
    assert.equal(chip.timeMs, 51500);
  }
});

test("parseTimeSegments supports range tokens and keeps plain text untouched", () => {
  const segments = parseTimeSegments("@38.2-43.7s 是信号窗口，@3s 之前是热身。");
  const rangeChip = segments.find((segment) => segment.chip?.kind === "range")?.chip;
  assert.ok(rangeChip && rangeChip.kind === "range");
  if (rangeChip && rangeChip.kind === "range") {
    assert.equal(rangeChip.raw, "@38.2-43.7s");
    assert.equal(rangeChip.label, "00:38–00:43");
    assert.equal(rangeChip.startMs, 38200);
    assert.equal(rangeChip.endMs, 43700);
  }
  // 无命中文本一字不动；原文（含 @ 记号）保留在 text 字段。
  assert.deepEqual(parseTimeSegments("没有时间标记的一段话。"), [
    { text: "没有时间标记的一段话。" },
  ]);
});
