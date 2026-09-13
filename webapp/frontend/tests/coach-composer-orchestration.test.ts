import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 前端追平批 5（输入框编排，digests §11）的接线合同。lib/composer 的纯逻辑
// 行为已由 lib/composer.test.ts 用 node:test 数值锁定；这里用源码断言
// （同 coach-send-poll-resilience.test.ts 风格）锁「组件确实按规格消费它们」。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("running-send lands in visible queue chips with a 96-char preview and three per-chip actions", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 预览必须走统一截断助手（纯逻辑单测锁 96+省略号，这里锁渲染消费）。
  assert.match(panel, /truncateQueuePreview\(chip\.text\)/);
  // 三操作（0911 点点拍板）：立即＝打断并立刻发 / 回填编辑（排队条消失）/
  // 逐条取消（Cline #12226 丢消息教训）。
  assert.match(panel, /promoteChipToSendNow = async \(chip: QueuedChip\)/);
  assert.match(panel, /sendText\(chip\.text, \{ force: true, refs: chip\.refs \}\)/);
  assert.match(panel, /label="立即打断发送"/);
  assert.match(panel, /task6-queue-chip-now">立即</);
  assert.match(panel, /backfillChipToDraft = \(chip: QueuedChip\)/);
  assert.match(panel, /label="取消发送"[\s\S]*?removeQueuedChip\(chips, chip\.id\)/);
  // 回填编辑后焦点回输入框：编辑目标可直接续写。
  assert.match(panel, /backfillChipToDraft[\s\S]*?textareaRef\.current\?\.focus\(\)/s);
});

test("send key morphs by run/draft state; running actions live beside it in a caret menu", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 0911 点点拍板（取代 09-01「恒为提交」）：运行中且输入框为空＝终止键
  // （IconStop、点击＝stop）；运行中且已输入＝发送（submitComposer＝入队）。
  assert.match(
    panel,
    /composerBusy && !draft\.trim\(\) && quotes\.length === 0 \? \(\s*<button\s+aria-label="停止生成"\s+className="task6-composer-send task6-composer-send--stop"\s+onClick=\{\(\) => void stop\(\)\}/s,
  );
  assert.match(panel, /<IconStop \/><\/button>/);
  const sendAt = panel.indexOf('aria-label="发送"');
  assert.ok(sendAt !== -1, "send button must exist");
  const clickAt = panel.indexOf("onClick={submitComposer}", sendAt);
  assert.ok(clickAt > sendAt, "send button must submit directly");
  // Esc 与终止键同功能（0912 点点拍板：旁挂发送选项菜单废弃，无菜单抢占 Esc）。
  assert.match(panel, /event\.key === "Escape" && composerBusy && !draft\.trim\(\) && quotes\.length === 0\)/);
  // 运行中占位提示随态切换。
  assert.match(panel, /composerBusy\s*\?\s*"继续输入以排队后续修改"/);
  // 终止形态样式：同形正圆换 error 底。
  assert.match(styles, /\.task6-composer-send--stop\s*\{[^}]*background:\s*var\(--error\)/);
  // 0912 点点拍板：运行中不再旁挂 caret 选项菜单——排队由运行中发送自动入队
  //（sendText enqueue 分支）与 composer 上方队列 chips 承担。
  assert.doesNotMatch(panel, /task6-send-caret/);
  assert.doesNotMatch(panel, /className="task6-send-menu"/);
  assert.doesNotMatch(styles, /\.task6-send-caret\s*\{/);
  assert.doesNotMatch(styles, /\.task6-send-menu[\s,{]/);
});

test("@ mention dropdown navigates above the submit path and tokenizes the pick", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const keydownAt = panel.indexOf("onKeyDown={(event) => {");
  const guardAt = panel.indexOf("keyCode === 229) return;");
  const mentionAt = panel.indexOf("if (mentionOpen) {");
  // IME 守卫必须在所有新键盘路径（含引用下拉导航）之前。
  assert.ok(keydownAt !== -1 && guardAt !== -1 && mentionAt > guardAt);
  // ↑↓ 循环 / Enter 选中 / Esc 关闭。
  const mentionChunk = panel.slice(mentionAt, panel.indexOf("// ↑ 翻发送历史"));
  assert.match(mentionChunk, /ArrowDown/);
  assert.match(mentionChunk, /ArrowUp/);
  assert.match(mentionChunk, /selectMentionCandidate\(/);
  assert.match(mentionChunk, /Escape/);
  // 选中落词走 applyMentionSelection（尾随空格 token 化防再触发，逻辑在单测锁定）。
  assert.match(panel, /applyMentionSelection\(draft, caret, candidate\.token\)/);
});

test("coach drafts persist under three-level scoped keys with debounced writes", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 三级作用域映射：草稿会话 → NEW_CONVO；无会话 → PENDING_CONVO；其余按会话 id。
  assert.match(
    panel,
    /const draftScope: CoachDraftScope = draftSession\s*\?\s*\{ kind: "new-convo" \}\s*:\s*sessionId == null\s*\?\s*\{ kind: "pending-convo" \}\s*:\s*\{ kind: "session", sessionId \};/,
  );
  // 多窗格 pane 后缀已删（唯一调用点固定 full 档，本就无后缀）：键生成
  // 不带 pane 维度，localStorage 存储结果与删除前完全一致。
  assert.doesNotMatch(panel, /paneSuffix|layoutMode/);
  assert.match(panel, /coachDraftStorageKey\(draftScope\)/);
  // 切换作用域先恢复已存草稿；写入经 300–500ms debounce 落 localStorage。
  // （2026-08-27 划选引用拍板③升级：存储值改为 envelope v2 { v:2, text, quotes }，
  // 恢复入口同步恢复引用数组；v1 纯文本读兼容下沉到 lib/composer 单测锁定。）
  assert.match(
    panel,
    /const restored = readCoachDraftEnvelope\(window\.localStorage, draftStorageKey\);\s*setDraft\(restored\.text\);\s*setQuotes\(restored\.quotes\);/,
  );
  assert.match(panel, /setTimeout\(\s*\(\) => writeCoachDraftEnvelope\(window\.localStorage, draftStorageKey, \{ text: draft, quotes \}\),\s*COACH_DRAFT_DEBOUNCE_MS,/s);
});

test("send consumes draft storage synchronously — NEW_CONVO residue prefill guard (0911 审计 §12.2)", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 同步清三键：当前 scope + NEW_CONVO + PENDING_CONVO。防抖清空会随 scope
  // 切换落到新 session 键，NEW_CONVO 残留已发全文，下次新建对话被恢复成预填。
  assert.match(panel, /const clearComposerDraftStorage = \(\) => \{/);
  const helperAt = panel.indexOf("const clearComposerDraftStorage");
  const helper = panel.slice(helperAt, helperAt + 600);
  assert.match(helper, /writeCoachDraftEnvelope\(window\.localStorage, draftStorageKey, empty\)/);
  assert.match(helper, /coachDraftStorageKey\(\{ kind: "new-convo" \}\), empty\)/);
  assert.match(helper, /coachDraftStorageKey\(\{ kind: "pending-convo" \}\), empty\)/);
  // sendText 的两个消费分支（运行中入队 / 乐观上屏）都必须同步清存储。
  const sendTextBody = panel.slice(
    panel.indexOf("const sendText = async ("),
    panel.indexOf("const composeOutgoing"),
  );
  const clears = sendTextBody.match(/clearComposerDraftStorage\(\)/g) ?? [];
  assert.ok(clears.length >= 2, "both sendText branches must clear draft storage synchronously");
});

test("ArrowUp walks the in-memory sent history and restores the pre-walk draft", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 发送成功即入历史，翻页索引复位。
  assert.match(panel, /pushSentHistory = useCallback\(\(text: string\)/);
  assert.match(panel, /historyIndexRef\.current = null;\s*historyBackupRef\.current = null;/);
  // 空草稿或正在翻页时接管 ↑；↓ 仅在翻页中生效；到端点回退原草稿。
  assert.match(panel, /event\.key === "ArrowUp" && \(draft\.trim\(\) === "" \|\| historyIndexRef\.current !== null\)/);
  assert.match(panel, /event\.key === "ArrowDown" && historyIndexRef\.current !== null/);
  assert.match(panel, /historyBackupRef\.current = draft;/);
  assert.match(panel, /setDraft\(next === null \? \(historyBackupRef\.current \?\? ""\) : history\[next\] \?\? ""\)/);
});

test("composition guard applies to every new keyboard surface introduced by batch 5", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 0912 拍板：旁挂发送菜单已废弃，其全局键处理随之移除——面板内不得再有
  // sendMenuOpen 驱动的全局 keydown（textarea/mention 的 IME 守卫另有断言）。
  assert.doesNotMatch(panel, /sendMenuOpen/);
  const modelMenu = await source("components/task6/CoachModelMenu.tsx");
  assert.ok(
    modelMenu.includes("if (event.isComposing || event.keyCode === 229) return;"),
    "model menu Escape handling must skip IME composition events",
  );
});

test("model selector stays usable during runs and takes effect next turn", async () => {
  const [panel, menu] = await Promise.all([
    source("components/task6/CoachPanel.tsx"),
    source("components/task6/CoachModelMenu.tsx"),
  ]);
  // 不再接受「运行中连坐」prop，面板也不传。
  assert.doesNotMatch(menu, /disabled: boolean/);
  assert.doesNotMatch(panel, /<CoachModelMenu[^>]*disabled/s);
  assert.match(menu, /disabled=\{switching\}/);
  assert.match(menu, /对下一段回复生效/);
});

// 编辑重发（截断派）已按点点 0827 拍板整体移除：小时钟入口、横幅、sendText 截断分支与本锁定测试一并删除。

test("plus button is a pure menu toggle; outside mousedown dismisses (0911 点点)", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 加号＝纯开关：开菜单不碰草稿（不再往输入框塞 @）。
  assert.match(panel, /setMentionQuery\(mentionOpen \? null : ""\);/);
  assert.doesNotMatch(panel, /discardUnconsumedPlusAt/);
  // 外点收起：文档级 mousedown，菜单与加号自身除外。
  assert.match(panel, /document\.addEventListener\("mousedown", onDocMouseDown\)/);
  assert.match(panel, /target\?\.closest\("\.task6-mention-menu"\)\) return;/);
  assert.match(panel, /target\?\.closest\("\.task6-composer-mention"\)\) return;/);
  // 光标以 DOM 实时值为准（程序化操作不触发 onChange，ref 会是过期值）。
  assert.match(panel, /const caret = el\?\.selectionStart \?\? mentionCaretRef\.current \?\? draft\.length;/);
});

test("analysis mention picks mount as structured refs — no analysis:N anywhere user-visible (0911 点点)", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const composer = await source("lib/composer.ts");
  // 菜单渲染人话标签（类型说明仅场景候选有），绝不渲染 analysis:N 机器码。
  assert.match(panel, /<strong>\{candidate\.label\}<\/strong>/);
  assert.match(panel, /\{candidate\.hint \? <small>/);
  // 分析候选＝挂结构化引用 chip，不改草稿文本。
  assert.match(panel, /token: candidate\.token, label: candidate\.label/);
  assert.match(panel, /setMentionRefs\(\(refs\) =>/);
  // 手打 @ 流选中分析后要摘掉 @ 片段。
  assert.match(panel, /setDraft\(draft\.slice\(0, typedFragmentAt\) \+ draft\.slice\(caret\)\)/);
  // 发送把结构化 refs 传给后端（context_refs），钉主题不再依赖消息文本。
  assert.match(panel, /contextRefs: refs/);
  const api = await source("lib/api.ts");
  assert.match(api, /context_refs: opts\.contextRefs/);
  // 分析元数据带对局时间；候选含最近完成的分析（新对话首页也有得引用）。
  assert.match(panel, /when: item\.training_at \?\? item\.created_at \?\? null/);
  assert.match(panel, /item\.status === "done"/);
  // composer 层不再有 scenarioByAnalysisId 旧签名；标签由调用方合成。
  assert.doesNotMatch(composer, /scenarioByAnalysisId/);
});
