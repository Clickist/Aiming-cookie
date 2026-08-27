import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 「划选引用」（docs/quote-feature-research.md）的接线合同。
// lib/quote 与 envelope v2 的纯逻辑行为已由 lib/quote.test.ts /
// lib/composer.test.ts 数值锁定；这里用源码断言锁「CoachPanel 确实按规格消费」。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

function chunkBetween(text: string, startMarker: string, endMarker: string): string {
  const start = text.indexOf(startMarker);
  const end = endMarker === "" ? text.length : text.indexOf(endMarker, start + startMarker.length);
  assert.ok(start !== -1, `marker missing: ${startMarker}`);
  return text.slice(start, end === -1 ? text.length : end);
}

test("selection evaluation is anchored to a single non-streaming assistant article", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // mouseup 主判定挂在消息滚动区上（WebKit 的 selectionchange 只作收起清理）
  assert.match(panel, /onMouseUp=\{handleMessagesMouseUp\}/);
  assert.match(panel, /evaluateAssistantSelection\(\{/);
  assert.match(panel, /messageArticleFromNode\(selection\?\.anchorNode/);
  assert.match(panel, /messageArticleFromNode\(selection\?\.focusNode/);
  // 流式禁划：partial article 带 data-streaming，运行中才置 true
  const partialChunk = chunkBetween(panel, "{run?.partial_text ?", "{run && [");
  assert.match(partialChunk, /data-streaming=\{composerBusy \? "true" : undefined\}/);
});

test("selection toolbar appears with one Quote action and every close path is wired", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const toolbarChunk = chunkBetween(panel, 'className="task6-selection-toolbar"', "</section>");
  assert.match(toolbarChunk, /<button onClick=\{addQuoteFromSelection\} type="button">引用<\/button>/);
  // 工具条自身的 mousedown/mouseup 不冒泡也不折叠原生选区
  assert.match(toolbarChunk, /onMouseDown=\{\(event\) => event\.preventDefault\(\)\}/);
  assert.match(toolbarChunk, /onMouseUp=\{\(event\) => event\.stopPropagation\(\)\}/);
  // 打开期间的四条关闭路径：滚动 / 选区折叠 / Esc / 外点
  const closeEffect = chunkBetween(panel, "if (!selectionBar) return undefined;", "addQuoteFromSelection");
  assert.match(closeEffect, /addEventListener\("scroll"/);
  assert.match(closeEffect, /addEventListener\("selectionchange"/);
  assert.match(closeEffect, /if \(event\.key === "Escape"\) setSelectionBar\(null\)/);
  assert.match(closeEffect, /addEventListener\("mousedown", onPointerDown\)/);
  assert.match(closeEffect, /selectionToolbarRef\.current\.contains\(event\.target as Node\)/);
  // IME 守卫与既有浮层家族一致
  assert.match(closeEffect, /if \(event\.isComposing || event\.keyCode === 229\) return;/);
});

test("clicking Quote consumes the mouseup snapshot, rejects oversize, and refocuses the textarea", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const action = chunkBetween(panel, "const addQuoteFromSelection", "/** 删除单条引用块");
  // 文本来自 mouseup 快照而非点击时的 window.getSelection（WKWebView 风险缓解）
  assert.match(action, /snapshotQuote\(selectionBar\.text\)/);
  // 超长引文给出可见拒绝而不是静默截断
  assert.match(action, /QUOTE_MAX_CHARS/);
  assert.match(action, /setQuotes\(\(current\) => \[\.\.\.current, \{ id: quoteSeqRef\.current, text: snapshot\.text \}\]\)/);
  assert.ok(action.indexOf("removeAllRanges") < action.indexOf("textareaRef.current?.focus"));
});

test("outbound composition funnels steer, interrupt-steer, enqueue, and submit through one gate", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const gate = chunkBetween(panel, "const composeOutgoing", "const submitComposer");
  // 无引用走原样正文路径；有引用拼装后才可发送
  assert.match(gate, /composeQuotedContent\(\{ quotes, text: draft \}\)/);
  // 长度预算超限给用户可见提示（不依赖 sidecar 静默切尾）
  assert.match(gate, /isWithinSendBudget\(composed\)/);
  assert.match(gate, /SEND_BUDGET_CHARS/);
  const submitAt = panel.indexOf("const submitComposer");
  const steerAt = panel.indexOf("const steerWithDraft");
  const steerBody = panel.slice(steerAt, panel.indexOf("const promoteChipToSteer"));
  const interruptBody = chunkBetween(panel, "const interruptAndSteer", "// 成功终态自动放行队首 chip");
  for (const [name, body] of [["steer", steerBody], ["interrupt-steer", interruptBody]] as const) {
    assert.ok(body.includes("composeOutgoing()"), `${name} must compose outbound content`);
  }
  assert.ok(submitAt < steerAt, "compose gate must be declared before its consumers");
  // 四动作菜单的「加入队列」同样消费拼装结果并在入列后消费待拼装引用
  const sendMenuAt = panel.indexOf('className="task6-send-menu"');
  const enqueueCode = panel.indexOf(
    'setSendMenuOpen(false); const content = composeOutgoing(); if (content === null) return; enqueueQueuedItem(content); setDraft(""); setQuotes([]);',
  );
  assert.ok(sendMenuAt !== -1 && enqueueCode > sendMenuAt, "menu enqueue must route through the compose gate");
});

test("quote-only sends are blocked with a visible hint at both gates (拍板①)", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const gate = chunkBetween(panel, "const composeOutgoing", "const submitComposer");
  assert.match(gate, /只有引用、没有正文时不能发送，请补充你的问题或要求。/);
  const idleSend = chunkBetween(panel, 'aria-label="发送"', "<IconSend /></button>");
  assert.match(idleSend, /disabled=\{!draft\.trim\(\)\}/);
  assert.match(idleSend, /只有引用、没有正文时不能发送，请补充你的问题或要求/);
});

test("quotes are consumed on accepted sends and preserved when the send fails", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const sendText = chunkBetween(panel, "const sendText = async", "const composeOutgoing");
  const acceptBlock = chunkBetween(sendText, "optimisticId = appendOptimisticUserMessage(content)", "pushSentHistory(content)");
  assert.match(acceptBlock, /setQuotes\(\[\]\)/);
  // 失败分支只回填草稿文本，保留引用块等待重试
  const failureBlock = chunkBetween(sendText, "} catch (error) {", "} finally {");
  assert.doesNotMatch(failureBlock, /setQuotes\(/);
  assert.match(failureBlock, /setDraft\(\(current\) => \(current\.trim\(\) \? current : content\)\)/);
  // steer 成功分支同步消费
  const steerSuccess = chunkBetween(panel, "await steerCoachAgentRun(active.run_ref, content)", "appendOptimisticUserMessage(content)");
  assert.match(steerSuccess, /setQuotes\(\[\]\)/);
});

test("composer renders removable locked quote blocks in the slot above the textarea", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const listAt = panel.indexOf('className="task6-quote-list"');
  const inputAt = panel.indexOf('className="task6-composer-input"');
  assert.ok(listAt !== -1 && inputAt !== -1);
  assert.ok(listAt < inputAt, "quote blocks must render above the composer input");
  const listChunk = panel.slice(listAt, inputAt);
  // 多条并存、来源标注 + 锁定文本体 + 整块删除
  assert.match(listChunk, /quotes\.map\(\(quote\) => \(/);
  assert.match(listChunk, /引用 Coach<\/span>/);
  assert.match(listChunk, /title=\{quote\.text\}\>\{quote\.text\}/);
  assert.match(listChunk, /label="删除这条引用" onClick=\{\(\) => removeQuote\(quote\.id\)\}/);
});

test("draft persistence upgrades to envelope v2 and restores quotes with the scoped key", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(
    panel,
    /const restored = readCoachDraftEnvelope\(window\.localStorage, draftStorageKey\);\s*setDraft\(restored\.text\);\s*setQuotes\(restored\.quotes\);/,
  );
  // 写入随 draft+quotes 双依赖 debounce 落 localStorage（拍板③）
  assert.match(
    panel,
    /setTimeout\(\s*\(\) => writeCoachDraftEnvelope\(window\.localStorage, draftStorageKey, \{ text: draft, quotes \}\),\s*COACH_DRAFT_DEBOUNCE_MS,/s,
  );
  assert.match(panel, /\[draft, quotes, draftStorageKey\]/);
});

test("sent user messages echo serialized quotes and legacy content passes through", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 回显入口只挂在 user 分支，assistant 仍走受控富渲染
  assert.match(panel, /<UserMessageBody content=\{message\.content\} \/>\s*\)/);
  const body = chunkBetween(panel, "function UserMessageBody", "export function CoachPanel");
  assert.match(body, /parseQuotedContent\(content\)/);
  assert.match(body, /parsed\.quotes\.map\(\(text, index\) => \(/);
  // 未命中形状的 legacy 内容按原文纯文本渲染（p 标签兜底分支保留）
  assert.match(body, /<p>\{parsed\.text\}<\/p>/);
});
