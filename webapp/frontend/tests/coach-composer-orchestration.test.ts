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
  // 三操作：上浮立即 steer / 回填编辑 / 逐条取消（Cline #12226 丢消息教训）。
  assert.match(panel, /promoteChipToSteer = async \(chip: QueuedChip\)/);
  assert.match(panel, /backfillChipToDraft = \(chip: QueuedChip\)/);
  assert.match(panel, /label="取消发送"[\s\S]*?removeQueuedChip\(chips, chip\.id\)/);
  // 回填编辑后焦点回输入框：编辑目标可直接续写。
  assert.match(panel, /backfillChipToDraft[\s\S]*?textareaRef\.current\?\.focus\(\)/s);
});

test("busy composer send key becomes the four-action steer menu", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 运行中才渲染下拉；空闲仍是普通提交键（复用同一入口 submitComposer）。
  assert.match(panel, /\{composerBusy \? \(/);
  assert.match(panel, /aria-label="发送"/);
  const menuChunk = panel.slice(panel.indexOf('className="task6-send-menu"'));
  assert.match(menuChunk, /立即转向/);
  assert.match(menuChunk, /加入队列/);
  // interrupt-steer＝stop 收敛终态后 force 重发，复用既有 stop 入口而非新动词。
  assert.match(panel, /interruptAndSteer = async/);
  assert.match(panel, /stopCoachAgentRun\(active\.run_ref[\s\S]*?sendText\(content, \{ force: true \}\)/s);
  assert.match(menuChunk, /void stop\(\)/);
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

test("coach drafts persist under three-level scoped keys with pane suffix and debounced writes", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 三级作用域映射：草稿会话 → NEW_CONVO；无会话 → PENDING_CONVO；其余按会话 id。
  assert.match(
    panel,
    /const draftScope: CoachDraftScope = draftSession\s*\?\s*\{ kind: "new-convo" \}\s*:\s*sessionId == null\s*\?\s*\{ kind: "pending-convo" \}\s*:\s*\{ kind: "session", sessionId \};/,
  );
  // 多窗格实例加 pane 后缀防互相覆盖。
  assert.match(panel, /const paneSuffix = layoutMode === "full" \? undefined : layoutMode;/);
  assert.match(panel, /coachDraftStorageKey\(draftScope, paneSuffix\)/);
  // 切换作用域先恢复已存草稿；写入经 300–500ms debounce 落 localStorage。
  assert.match(panel, /setDraft\(readCoachDraft\(window\.localStorage, draftStorageKey\)\)/);
  assert.match(panel, /setTimeout\(\s*\(\) => writeCoachDraft\(window\.localStorage, draftStorageKey, draft\),\s*COACH_DRAFT_DEBOUNCE_MS,/s);
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
  // 运行中发送菜单的全局键处理（Esc 关闭/↑↓ 移焦）同守卫。
  const menuEffect = panel.slice(
    panel.indexOf("if (!sendMenuOpen) return undefined;"),
    panel.indexOf("const headerState"),
  );
  assert.ok(
    menuEffect.includes("if (event.isComposing || event.keyCode === 229) return;"),
    "send-menu global keydown must skip IME composition events",
  );
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

test("edit-resend truncates the saved session before starting the replacement run", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const sendChunk = panel.slice(
    panel.indexOf("const sendText = async"),
    panel.indexOf("const submitComposer"),
  );
  // 截断派（item 7）：先剪服务端历史再建新 run——其后的历史不参与本次上下文。
  assert.match(sendChunk, /await truncateCoachSession\(sessionId, editing\.index\);/);
  assert.ok(
    sendChunk.indexOf("truncateCoachSession") < sendChunk.indexOf("createCoachAgentRun("),
    "truncation must happen before the replacement run is created",
  );
  // 截断已落服务端但发送失败的窗口：退出编辑态并明示「截断已生效」，防重复截断。
  assert.match(sendChunk, /truncatedDone/);
  assert.match(panel, /截断已生效/);
  // 编辑横幅与入口只面向已落库用户消息，且仅在空闲时提供。
  assert.match(panel, /正在编辑第 /);
  assert.match(panel, /message\.role === "user" && message\.id > 0 && !composerBusy/);
});
