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

test("the empty conversation home centers greeting, composer and chips in the leftover space", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 空会话（无消息且无 run、非过渡帧）渲染「新对话首页」hero（点点 0910 拍板）
  assert.match(coach, /const homeMode = messages\.length === 0 && !run && !homeExit;/);
  assert.match(coach, /homeMode \? \(\s*<div className="task6-empty-hero">/);
  const hero = styles.match(/\.task6-empty-hero\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.ok(hero.length > 0, ".task6-empty-hero block must exist");
  assert.match(hero, /flex:\s*1/);
  assert.match(hero, /flex-direction:\s*column/);
  assert.match(hero, /justify-content:\s*center/);
  // 问候语来自 coach-home（时间轮换、不带称呼）；composer 整体居中进 hero
  assert.match(coach, /coachGreeting\(new Date\(\)\)/);
  assert.match(coach, /className="task6-home-composer" ref=\{homeComposerRef\}>\{composerCore\}<\/div>/);
  // 首页态 footer 不渲染：composer 全组件只有 composerCore 一份实例
  assert.match(coach, /\{homeMode \? null : \(\s*<footer className="task6-composer" ref=\{footerComposerRef\}[^>]*>/);
});

test("home chips fill the draft instead of sending; the first send animates out of the home", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // chips（一行三颗）只 setDraft 填入草稿，不直发（0910 拍板）
  const tailAt = coach.indexOf("const homeTail = (");
  const coreAt = coach.indexOf("const composerCore = (");
  assert.ok(tailAt !== -1 && coreAt !== -1 && tailAt < coreAt, "homeTail 必须在 composerCore 之前定义");
  const tail = coach.slice(tailAt, coreAt);
  assert.match(tail, /setDraft\(chip\.prompt\)/);
  assert.doesNotMatch(tail, /sendText|submitComposer/);
  // 过渡动画：气泡飞升（WAAPI）＋composer 滑落，系统减少动态效果时跳切
  assert.match(coach, /prefers-reduced-motion: reduce/);
  assert.match(coach, /homeFlyRef/);
  assert.match(coach, /fly\.animate\(/);
  assert.match(coach, /footer\.animate\(/);
  assert.match(styles, /\.task6-home-fly\s*\{/);
  assert.match(styles, /task6-home-leave/);
});

test("the composer exposes a quote-analysis button that reuses the @ mention pipeline", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 点点 0910 拍板：引用钮改为左下角圆形「+」（IconPlus），移出右下角簇，
  // 与右下发送键水平对称；角簇次序变为 corner → 模型/力度菜单 → 发送键。
  const inputAt = coach.indexOf('className="task6-composer-input"');
  const mentionAt = coach.indexOf("task6-composer-mention");
  const cornerAt = coach.indexOf('className="task6-composer-corner"');
  const modelAt = coach.indexOf("<CoachModelMenu");
  const sendAt = coach.indexOf("task6-composer-send");
  assert.ok(inputAt !== -1 && mentionAt !== -1);
  assert.ok(mentionAt > inputAt && mentionAt < cornerAt, "+ 引用钮须在输入卡内、右下角簇之前");
  assert.ok(modelAt > cornerAt && sendAt > modelAt, "模型/力度菜单须在簇内且位于发送键之前");
  const chunk = coach.slice(mentionAt, cornerAt);
  assert.match(chunk, /<IconPlus \/>/);
  // 点击＝落一个真实 @ 再同步查询：整条 mention 管线复用，零新增逻辑
  assert.match(chunk, /setDraft\(next\)/);
  assert.match(chunk, /syncMentionQuery\(\)/);
  // CSS：absolute 锚定输入卡左下（发送键角簇 right/bottom 对称值 8px），正圆
  const mentionCss = styles.match(/\.task6-composer-mention\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(mentionCss, /position:\s*absolute/);
  assert.match(mentionCss, /left:\s*8px/);
  assert.match(mentionCss, /bottom:\s*8px/);
  assert.match(mentionCss, /border-radius:\s*50%/);
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
  // discussion 条常驻（0827 拍板回退）：有 pending 或有已完成讨论都显示；
  // 空对话首页不渲染（0910 homeShell 拍板），header 状态行同样让位纯 hero。
  // v6 四轮：讨论条经 portal 挂入共用顶栏（CoachPanel createPortal →
  // AppShell 的 task3-coach-topbar-slot，标题之后），槽位缺失时不渲染。
  assert.match(
    coach,
    /\{\s*\(!homeShell && \(pendingAnalyses\.length > 0 \|\| discussionAnalysisIds\.length > 0\) && discussionBarHost != null\) \? \(\s*createPortal\(\s*<div aria-label="本次讨论的分析" className="task6-discussion-bar/,
  );
  assert.match(coach, /document\.getElementById\("task3-coach-topbar-slot"\)/);
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /<div className="task3-coach-topbar-slot" id="task3-coach-topbar-slot" \/>/);
  assert.match(styles, /\.task3-coach-topbar-slot \.task6-discussion-bar\s*\{/);
  // 0910 拍板：顶栏内不再渲染「本次讨论」文字标签（只留 chips/菜单结构），
  // 对应的 label 弱化样式块随之删除，不留死代码。
  assert.doesNotMatch(coach, /<span>本次讨论<\/span>/);
  assert.doesNotMatch(styles, /\.task3-coach-topbar-slot \.task6-discussion-bar > span:first-child/);
  assert.match(coach, /\{homeShell \? null : header\}/);
  assert.match(coach, /const homeShell = messages\.length === 0 && !run;/);
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
  // 嵌套公式（0910 新档）：菜单外 R(lg=14) − 菜单 padding(space-1=4) = item md=10
  // provider-picker 菜单已随设置页主从式重做移除；嵌套公式仅约束仍存在的
  // 菜单项（composer 模型菜单）。
  for (const name of ["task6-composer-model-item"]) {
    const item = styles.match(new RegExp(`\\.${name}\\s*\\{[^}]*\\}`))?.[0] ?? "";
    assert.match(item, /border-radius:\s*var\(--radius-md\)/, name);
    assert.doesNotMatch(item, /--radius-sm/, name);
  }
  // chips 全胶囊（点点 0910 四轮拍板：讨论 chip 也胶囊化，取消早先
  // "讨论条归 sm"的决定）；弱色差方案：container-high 底 + highest hover、无描边。
  const suggestion = styles.match(/\.task6-suggestion\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(suggestion, /border-radius:\s*999px/);
  const contextChip = styles.match(/\.task6-context-chip\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(contextChip, /border-radius:\s*var\(--radius-sm\)/);
  const discussionChip = styles.match(/\.task6-discussion-chip\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(discussionChip, /border-radius:\s*999px/);
  assert.match(discussionChip, /background:\s*var\(--surface-container-high\)/);
  // 无描边：border 仅允许显式归零（border: 0），UA 默认 outset 白框不得回归。
  assert.doesNotMatch(discussionChip, /border:\s*(?!0)[1-9]/);
  assert.doesNotMatch(discussionChip, /border:\s*(?!0)(?:none|transparent)?\s*solid/);
  const discussionHover = styles.match(/button\.task6-discussion-chip:hover\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(discussionHover, /background:\s*var\(--surface-container-highest\)/);
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
