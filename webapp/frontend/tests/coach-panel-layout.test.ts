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

test("Coach messages are bottom-anchored beside the pinned composer", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 非空会话渲染零高 auto-margin spacer，把消息流压向钉底的 composer
  assert.match(coach, /className="task6-msg-spacer"/);
  const spacer = styles.match(/\.task6-msg-spacer\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.ok(spacer.length > 0, ".task6-msg-spacer block must exist");
  assert.match(spacer, /margin-top:\s*auto/);
  // 红线：不允许 grid-template-rows 方案
  assert.doesNotMatch(styles, /grid-template-rows/);
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
  // user 气泡保持栏内短气泡语义（与助手同一对话列上界的不对称消解）
  const userEntry = styles.match(/\.task6-message-entry\[data-role="user"\]\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(userEntry, /max-width:\s*min\(36em,\s*92%\)/);
});

test("header training card folds into a single header chip; discussion bar only when pending", async () => {
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
  // discussion 条只在有 pending 分析时出现（不再用已完成项让条常驻）
  assert.match(
    coach,
    /\{\s*pendingAnalyses\.length > 0 \? \(\s*<div aria-label="本次讨论的分析" className="task6-discussion-bar/,
  );
  assert.doesNotMatch(coach, /discussionAnalysisIds\.length > 0 \|\| pendingAnalyses\.length > 0/);
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

test("composer tools row splits out of the textarea line without the reserved gutter", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 模型菜单移入输入卡下方的工具行
  assert.match(coach, /<div className="task6-composer-tools">\s*<CoachModelMenu/);
  // 删除同行预留与 :has 挂钩规则
  assert.doesNotMatch(styles, /196px/);
  assert.doesNotMatch(styles, /:has\(\.task6-composer-model\) textarea/);
  // 工具行样式存在；wrap 由绝对定位改为行内静态锚点，菜单继续向上弹出
  assert.match(styles, /\.task6-composer-tools\s*\{/);
  const wrap = styles.match(/\.task6-composer-model-wrap\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(wrap, /position:\s*relative/);
});
