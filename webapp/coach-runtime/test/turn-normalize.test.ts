import assert from "node:assert/strict";
import { test } from "node:test";

import { normalizeUserFacingText } from "../src/turn.ts";

// frontend-parity 批 7（受控富渲染，digests §10）：
// 归一化从「删字符」改「白名单透传」。透传 list/table/bold/@time；
// 继续剥 H1–H6、代码块 fence、引用块、Mermaid、图片、一切 HTML。
// JSONL 存模型原始输出，本函数只作用于展示链路，历史消息零迁移。

test("ordered list markers pass through intact", () => {
  const input = "训练计划：\n1. 热身 5 分钟\n2) 主任务 Aim Trainer\n3、拉伸放松";
  assert.equal(normalizeUserFacingText(input), input);
});

test("unordered list markers pass through intact", () => {
  const input = "要点：\n- 命中率优先\n* 连击稳定性\n+ 尾段不抢节奏";
  assert.equal(normalizeUserFacingText(input), input);
});

test("GFM tables pass through cell-for-cell", () => {
  const input = [
    "| 场景 | 命中率 | 平均 TTK |",
    "| --- | ---: | :--- |",
    "| Aim Trainer | 41.2% | 1.8s |",
    "| Gridshot | 87.0% | 0.9s |",
  ].join("\n");
  assert.equal(normalizeUserFacingText(input), input);
});

test("inline bold and @time markers pass through", () => {
  assert.equal(normalizeUserFacingText("**第 6 杀**是转折点，回看 @41.2s"), "**第 6 杀**是转折点，回看 @41.2s");
});

test("heading markers are stripped but heading text survives as plain paragraph", () => {
  assert.equal(normalizeUserFacingText("## 本周重点\n\n内容"), "本周重点\n\n内容");
  // GFM 语义：# 后无空格不是标题，原样透传
  assert.equal(normalizeUserFacingText("##标题"), "##标题");
});

test("fenced code blocks are removed with their content, closed or not", () => {
  assert.equal(normalizeUserFacingText("前文\n```python\nprint('x')\n```\n后文"), "前文\n后文");
  // 未闭合 fence（流式中途）：已知围栏之后的残余一并吞掉，终稿闭合后收敛
  assert.equal(normalizeUserFacingText("前文\n```\n半截"), "前文");
  // mermaid 属 fence 家族
  assert.equal(normalizeUserFacingText("看图\n```mermaid\ngraph TD; A-->B;\n```"), "看图");
});

test("blockquote markers are stripped, quoted text survives", () => {
  assert.equal(normalizeUserFacingText("> 引用一句话"), "引用一句话");
});

test("image syntax disappears entirely", () => {
  assert.equal(normalizeUserFacingText("成果 ![截图](https://evil.example/x.png)如上"), "成果 如上");
});

test("all HTML is stripped, including event-handler vectors and script blocks", () => {
  // 行内标签剥除，文本保留
  assert.equal(normalizeUserFacingText("<b>加粗</b> 文本"), "加粗 文本");
  assert.equal(normalizeUserFacingText('<img src=x onerror="alert(1)">'), "");
  // script/style/iframe 连标签带内容整体剥除
  assert.equal(normalizeUserFacingText("<script>alert(document.cookie)</script>正文"), "正文");
  assert.equal(normalizeUserFacingText("<style>*{}</style>正文"), "正文");
  assert.equal(normalizeUserFacingText('<iframe src="https://evil.example"></iframe>正文'), "正文");
  // 注释与未闭合标签同样不留痕迹；普通比较符不受影响
  assert.equal(normalizeUserFacingText("<!-- 注释 -->正文 <a href='x'"), "正文");
  assert.equal(normalizeUserFacingText("当 5<6 时结束"), "当 5<6 时结束");
});

test("inline code backticks unwrap while keeping content (not in whitelist)", () => {
  assert.equal(normalizeUserFacingText("按下 `Shift` 键"), "按下 Shift 键");
});

test("normalization is idempotent for the whitelisted shapes", () => {
  const input = "| a | b |\n| - | - |\n1. **要点** @12.3s";
  const once = normalizeUserFacingText(input);
  assert.equal(once, input);
  assert.equal(normalizeUserFacingText(once), once);
});
