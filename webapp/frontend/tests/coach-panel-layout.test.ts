import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import { SCALE_TOKENS } from "../ui/tokens";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

// frontend-parity 批 3（对话窗格版式改版）行为锁定。
// 规格出处：docs/frontend-parity-research-digests.md §8「Top5 改向」。

test("Coach messages flow top-down beside the pinned composer", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 0827 拍板回退底部锚定：不再渲染 auto-margin spacer，消息从上往下自然排布
  assert.doesNotMatch(coach, /task6-msg-spacer/);
  assert.doesNotMatch(styles, /\.task6-msg-spacer/);
  // 红线（缩窄 0828）：消息区布局不允许 grid-template-rows 压底方案；
  // .task6-collapse 的 0fr/1fr 是折叠显隐动画，不是布局锚定，不在禁列。
  const messagesLayout = styles.match(/\.task6-messages\s*\{[^}]*\}/)?.[0] ?? "";
  assert.doesNotMatch(messagesLayout, /grid-template-rows/);
});

test("the empty conversation hero centers inside the leftover space", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 空会话（无消息且无 run）时 hero 顶替 spacer 槽位
  assert.match(coach, /messages\.length === 0 && !run \? \(\s*<div className="task6-empty-hero">[\s\S]{0,200}?<Empty /);
  const hero = styles.match(/\.task6-empty-hero\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.ok(hero.length > 0, ".task6-empty-hero block must exist");
  assert.match(hero, /flex:\s*1/);
  assert.match(hero, /place-content:\s*center/);
});

test("the conversation column converges to ~720px relative to its parent", async () => {
  const styles = await source("components/task6/task6.css");
  // 内容列宽收敛到 min(100%, 720px)：父容器相对单位，
  // 视频面板压缩面板宽度时 composer 自动退为全宽
  assert.match(styles, /--task6-coach-content-width:\s*min\(100%,\s*720px\)/);
  assert.doesNotMatch(styles, /72vw/);
  // 0828 拍板：滚动容器上移到面板本身——滚动条贯穿全列高、贴窗口右缘；
  // 头部与 composer sticky 悬浮，消息内容自然撑高驱动面板滚动。
  assert.match(styles, /\.task6-coach-panel\s*\{[^}]*overflow-y:\s*auto/);
  assert.match(styles, /\.task6-coach-top\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0/);
  assert.match(styles, /\.task6-composer\s*\{[^}]*position:\s*sticky;[^}]*bottom:\s*0/);
  assert.doesNotMatch(styles, /\.task6-messages\s*\{[^}]*overflow-y/);
  // user 气泡保持栏内短气泡语义（与助手同一对话列上界的不对称消解）
  const userEntry = styles.match(/\.task6-message-entry\[data-role="user"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(userEntry, /max-width:\s*min\(36em,\s*92%\)/);
});

test("header training card folds into a single header chip; discussion bar stays mounted", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 折叠态并入 header 行的单 chip（替换掉常驻训练卡 section）
  assert.match(coach, /className="task6-training-chip"/);
  assert.match(coach, /aria-label="当前训练计划"/);
  assert.doesNotMatch(coach, /<section aria-label="当前训练计划" className="task6-current-training"/);
  assert.doesNotMatch(coach, /const trainingSection = \(/);
  assert.doesNotMatch(coach, /\{trainingSection\}/);
  // 展开细节沿用 training-reveal 动画合同
  assert.match(coach, /className="task6-training-reveal"/);
  // chip 样式存在，旧折叠卡样式清干净
  assert.match(styles, /\.task6-training-chip\s*\{/);
  assert.doesNotMatch(styles, /\.task6-training-toggle|\.task6-training-summary|\.task6-training-empty-title|\.task6-current-training-head/);
  // discussion 条常驻（0827 拍板回退）：有 pending 或有已完成讨论都显示
  assert.match(
    coach,
    /\{\s*\(pendingAnalyses\.length > 0 \|\| discussionAnalysisIds\.length > 0\) \? \(\s*<div aria-label="本次讨论的分析" className="task6-discussion-bar/,
  );
});

test("user bubbles adopt the pill-tier radius and menu items follow the nested formula", async () => {
  const themeCss = await source("ui/theme.css");
  const cursorDoc = await source("../../DESIGN-cursor.md");
  const styles = await source("components/task6/task6.css");
  // 新几何 token 三处家园同值登记
  assert.match(themeCss, /--radius-xl:\s*16px/);
  assert.equal(SCALE_TOKENS["radius-xl"], "16px");
  assert.match(cursorDoc, /four-step radius scale[\s\S]{0,120}--radius-sm\.\.xl/);
  // user 气泡 ≥16px pill 向
  const userBubble = styles.match(/\.task6-message\[data-role="user"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(userBubble, /border-radius:\s*var\(--radius-xl\)/);
  // 嵌套公式：菜单外R(lg=8) − 菜单padding(space-1=4) = item 半径 sm=4
  for (const name of ["task6-composer-model-item", "task6-provider-picker-item"]) {
    const item = styles.match(new RegExp(`\\.${name}\\s*\\{[^}]*\\}`))?.[0] ?? "";
    assert.match(item, /border-radius:\s*var\(--radius-sm\)/, name);
    assert.doesNotMatch(item, /--radius-md/, name);
  }
  // chip 两档归并：独立交互 chip 用 md，内联小标签维持 sm
  const suggestion = styles.match(/\.task6-suggestion\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(suggestion, /border-radius:\s*var\(--radius-md\)/);
  const contextChip = styles.match(/\.task6-context-chip\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(contextChip, /border-radius:\s*var\(--radius-sm\)/);
});

test("model picker sits left of the send key inside the composer corner cluster", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 0827 拍板 B1：模型选择挪进输入卡右下角簇、发送键左侧；旧「工具行拆出」
  // 结构（task6-composer-tools）随拍板移除。
  const cornerAt = coach.indexOf('className="task6-composer-corner"');
  const modelAt = coach.indexOf("<CoachModelMenu");
  const sendAt = coach.indexOf("task6-composer-send");
  assert.ok(cornerAt !== -1, "composer corner cluster must exist");
  assert.ok(modelAt > cornerAt && sendAt > modelAt, "模型菜单必须在簇内且位于发送键之前");
  assert.doesNotMatch(coach, /task6-composer-tools/);
  // 0828 拍板：右侧整条预留不合理——改为只加 padding-bottom 让位底部按钮
  // 簇（文字左右全宽可用），不回归无条件 gutter（196px）。
  assert.match(
    styles,
    /\.task6-composer-input:has\(\.task6-composer-model\) textarea\s*\{[^}]*padding-bottom:\s*44px/,
  );
  assert.doesNotMatch(styles, /padding-right:\s*180px/);
  assert.doesNotMatch(styles, /196px/);
  // wrap 仍是菜单的行内静态锚点，菜单继续向上弹出
  const wrap = styles.match(/\.task6-composer-model-wrap\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(wrap, /position:\s*relative/);
  // 绝对定位移到簇容器，发送钮退为簇内静态项
  const corner = styles.match(/\.task6-composer-corner\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(corner, /position:\s*absolute/);
  const send = styles.match(/\.task6-composer-send\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.doesNotMatch(send, /position:\s*absolute/);
});
