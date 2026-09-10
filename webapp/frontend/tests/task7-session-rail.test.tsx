import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function source(path: string): Promise<string> {
  return readFile(new URL(path, root), "utf8");
}

test("SessionRail stays a prop-driven, client-only surface", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  assert.match(component, /export interface SessionRailProps/);
  assert.match(component, /onNewSession\?:/);
  assert.match(component, /onSelectSession\?:/);
  assert.match(component, /onArchiveSession\?:/);
  assert.match(component, /onSoftDeleteSession\?:/);
  assert.doesNotMatch(component, /from ["']@\/lib\/api["']/);
  assert.doesNotMatch(component, /fetch\(/);
});

test("SessionRail groups sessions into 今天/昨天/近 7 天/更早 with static headers", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  // 分桶仍基于既有时间戳字段（updated_at 优先），组内保持倒序
  assert.match(component, /sessionTimestamp/);
  assert.match(component, /\.sort\(/);
  // 四个时间分组文案与数量角标
  assert.match(component, /"今天"/);
  assert.match(component, /"昨天"/);
  assert.match(component, /"近 7 天"/);
  assert.match(component, /"更早"/);
  assert.match(component, /task7-session-rail__count/);
  // 组头复用既有分组样式类；第一版静态组头，无 <details> 折叠
  assert.match(component, /task7-session-rail__group-summary/);
  assert.match(component, /task7-session-rail__group-label/);
  assert.match(component, /task7-session-rail__group-items/);
  assert.doesNotMatch(component, /<details/);
  assert.doesNotMatch(component, /collapsed/);
  assert.match(styles, /\.task7-session-rail__group\s*\{/);
});

test("SessionRail includes search and keyboard semantics", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  assert.match(component, /type="search"/);
  assert.match(component, /aria-current=\{current \? "page"/);
  assert.match(component, /aria-label="训练历史"/);
  assert.match(component, /aria-label="系统设置"/);
  assert.match(component, /aria-label=\{`归档/);
  assert.match(component, /aria-label=\{`删除/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /prefers-reduced-motion/);
});

test("SessionRail deletes only after an inline two-step confirmation", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  // 第一次点击只进入待确认态（0910 拍板：动作键图标化，删除键变红色✓，
  // aria-label 与 title 承载"确认删除"语义），第二次点击才触发软删回调
  assert.match(component, /pendingDeleteId/);
  assert.match(component, /setPendingDeleteId\(session\.id\)/);
  assert.match(component, /aria-label=\{`确认删除 /);
  assert.match(component, /item-action--confirm/);
  assert.match(component, /title="再次点击确认删除"/);
  // 文案必须是删除而非归档；禁止浏览器原生 confirm 与全屏对话框
  assert.doesNotMatch(component, /window\.confirm/);
  assert.doesNotMatch(component, /<dialog/i);
});

test("SessionRail keeps hover actions off the session date", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  // 操作条位于 session 按钮之后的兄弟节点，绝不嵌套进按钮覆盖内容
  assert.match(
    component,
    /task7-session-rail__session-date[\s\S]*?<\/button>\s*\{session\.id !== "draft"[\s\S]*?<span className="task7-session-rail__item-actions">/,
  );
  // 0910 二次拍板（浮层化替代旧"禁止绝对定位"）：操作条绝对定位＋右缘
  // scrim 渐变，日期/摘要是"被淡出遮住"而非挤压或硬叠；显隐仍只靠透明度
  assert.match(styles, /\.task7-session-rail__item-actions\s*\{[^}]*position:\s*absolute/);
  assert.match(styles, /--rail-action-scrim:\s*var\(--surface-container\)/);
  assert.match(styles, /linear-gradient\(to right, transparent, var\(--rail-action-scrim\)/);
  assert.match(styles, /\.task7-session-rail__item-actions\s*\{[^}]*opacity:\s*0;/);
  assert.match(styles, /prefers-reduced-motion/);
});

test("SessionRail stays expanded with a persistent footer", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  assert.match(component, /task7-session-rail__footer/);
  assert.match(component, /providerStatus/);
  assert.match(component, /task7-session-rail__footer-label/);
  assert.match(component, /训练历史/);
  assert.match(component, /系统设置/);
  // 展开态保证：header 始终渲染新建对话，列表与 footer 恒定存在
  assert.match(component, /task7-session-rail__new/);
  // 无任何折叠/收起残留
  assert.doesNotMatch(component, /onCollapsedChange/);
  assert.doesNotMatch(component, /collapsed/);
  assert.doesNotMatch(component, /IconChevronLeft|IconChevronRight/);
  assert.doesNotMatch(component, /收起|展开会话栏/);
  assert.doesNotMatch(styles, /data-collapsed/);
  assert.doesNotMatch(styles, /task7-session-rail__iconbar|__icon-button/);
  assert.doesNotMatch(styles, /data-overlay|-overlay/);
  assert.doesNotMatch(styles, /max-width: 1119px/);
  assert.doesNotMatch(styles, /@keyframes task7-session-rail-slide-in/);
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)/);
  assert.match(styles, /prefers-reduced-motion/);
  assert.match(styles, /--task7-rail-width:\s*288px/);
});

test("SessionRail keeps session rows at equal height with no stray border", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  // 恒定三行（title/summary/date），无 summary 时保持占位，保证所有项等高
  assert.match(component, /task7-session-rail__session-title/);
  assert.match(component, /task7-session-rail__session-summary/);
  assert.match(component, /task7-session-rail__session-date/);
  assert.match(component, /\{summaryLine\}/);
  assert.match(component, /aria-hidden=\{!summaryLine \? true : undefined\}/);
  // 无浏览器默认边框（border:0），避免莫名出现的框
  assert.match(styles, /\.task7-session-rail__session\s*\{[\s\S]*border:\s*0;[\s\S]*\}/);
  assert.match(styles, /\.task7-session-rail__session-summary\[aria-hidden="true"\][\s\S]*min-height/);
});
