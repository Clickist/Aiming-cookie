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

test("SessionRail includes flat time-sorted navigation, search, and keyboard semantics", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  assert.match(component, /sessionTimestamp/);
  assert.match(component, /\.sort\(/);
  assert.doesNotMatch(component, /UNASSOCIATED_SCENARIO|<details/);
  assert.match(component, /type="search"/);
  assert.match(component, /aria-current=\{current \? "page"/);
  assert.match(component, /aria-label="训练历史"/);
  assert.match(component, /aria-label="系统设置"/);
  assert.match(component, /aria-label=\{`归档/);
  assert.match(component, /aria-label=\{`删除/);
  assert.match(styles, /:focus-visible/);
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
