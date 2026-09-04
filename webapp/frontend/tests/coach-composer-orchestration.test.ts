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

test("send key always submits; running-state actions live beside it in a caret menu", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 发送键恒为 submitComposer：运行中点击走 sendText busy 分支＝自动入队
  // （点点 09-01 拍板：移除批5 ed8d067 的"首击只开菜单"四动作开关——它被
  // 真机感知为"第一次点击永远发不出去"的防误触）。
  const sendAt = panel.indexOf('aria-label="发送"');
  assert.ok(sendAt !== -1, "send button must exist");
  const clickAt = panel.indexOf("onClick={submitComposer}", sendAt);
  assert.ok(clickAt > sendAt, "send button must submit directly");
  // 发送键本体不得再被运行态替换成菜单开关（旧防误触的吞击点）。
  const btnChunk = panel.slice(
    panel.indexOf('className="task6-composer-send"'),
    panel.indexOf("<IconSend /></button>"),
  );
  assert.match(btnChunk, /onClick=\{submitComposer\}/);
  assert.doesNotMatch(btnChunk, /setSendMenuOpen/);
  // 转向/打断/停止不消失：收敛到旁挂 caret 菜单（仅运行中渲染）。
  assert.match(panel, /className="task6-send-caret"/);
  assert.match(styles, /\.task6-send-caret\s*\{/);
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
  // （2026-08-27 划选引用拍板③升级：存储值改为 envelope v2 { v:2, text, quotes }，
  // 恢复入口同步恢复引用数组；v1 纯文本读兼容下沉到 lib/composer 单测锁定。）
  assert.match(
    panel,
    /const restored = readCoachDraftEnvelope\(window\.localStorage, draftStorageKey\);\s*setDraft\(restored\.text\);\s*setQuotes\(restored\.quotes\);/,
  );
  assert.match(panel, /setTimeout\(\s*\(\) => writeCoachDraftEnvelope\(window\.localStorage, draftStorageKey, \{ text: draft, quotes \}\),\s*COACH_DRAFT_DEBOUNCE_MS,/s);
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

// 编辑重发（截断派）已按点点 0827 拍板整体移除：小时钟入口、横幅、sendText 截断分支与本锁定测试一并删除。
