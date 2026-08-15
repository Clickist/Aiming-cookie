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

test("SessionRail follows the active narrow and footer contract", async () => {
  const component = await source("components/task7/SessionRail.tsx");
  const styles = await source("components/task7/session-rail.css");
  assert.match(component, /task7-session-rail__footer/);
  assert.match(component, /providerStatus/);
  assert.match(component, /onCollapsedChange/);
  assert.match(component, /Escape/);
  assert.match(component, /trapOverlayFocus/);
  assert.match(component, /type OverlayState = "closed" \| "opening" \| "open" \| "closing"/);
  assert.match(component, /toggleRail\(true\)/);
  assert.match(component, /closeOverlay\(true\)/);
  assert.match(component, /prefers-reduced-motion: reduce[\s\S]*\? 120 : 200/);
  assert.match(component, /inert=\{overlayState === "closing" \|\| undefined\}/);
  assert.match(component, /title="收起\/展开会话栏"/);
  assert.match(component, /type="button">←<\/button>/);
  assert.match(component, /type="button">→<\/button>/);
  assert.match(component, /task7-session-rail__footer-label/);
  assert.match(component, /训练历史/);
  assert.match(component, /系统设置/);
  assert.match(styles, /--task7-rail-width:\s*56px/);
  assert.match(styles, /data-collapsed/);
  assert.match(styles, /task7-session-rail__iconbar/);
  assert.match(styles, /@media \(max-width: 1119px\)/);
  assert.match(styles, /data-overlay-state/);
  assert.doesNotMatch(styles, /@keyframes task7-session-rail-slide-in/);
  assert.match(styles, /transition:\s*transform var\(--duration-surface, 200ms\) var\(--ease-out/);
  assert.match(styles, /data-motion="instant"/);
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)/);
  assert.match(styles, /prefers-reduced-motion: reduce[\s\S]*duration-reduced-motion, 120ms/);
  assert.match(styles, /color-mix\(in srgb, var\(--on-surface\) 12%, transparent\)/);
  assert.match(styles, /data-overlay="true"\][^{]*\{[\s\S]*grid-template-columns: minmax\(0, 1fr\)/);
});
