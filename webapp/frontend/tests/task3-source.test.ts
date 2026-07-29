import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("app shell exposes landmarks and skip navigation without an Account surface", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /<header/);
  assert.match(value, /<nav/);
  assert.match(value, /<main/);
  assert.match(value, /skip-link/);
  assert.doesNotMatch(value, /Account/);
});

test("app shell keeps Coach visible on unsupported pages and marks the task count without layout width", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /disabled={!coachSupported}/);
  assert.match(value, /当前页面不支持 Coach/);
  assert.match(value, /task3-task-nav-dot/);
  assert.match(value, /key={pathname}/);
});

test("app shell styles encode active navigation and reduced-motion route transitions", async () => {
  const value = await source("components/task3/task3.css");
  assert.match(value, /a\[aria-current=\"page\"\][^{]*\{[\s\S]*border-bottom-color: var\(--primary\)/);
  assert.match(value, /task3-route-fade 140ms ease-out/);
  assert.match(value, /prefers-reduced-motion: reduce[\s\S]*task3-route-content/);
});

test("onboarding never persists credentials in browser storage", async () => {
  const value = await source("components/task3/OnboardingFlow.tsx");
  assert.doesNotMatch(value, /localStorage|sessionStorage|indexedDB/);
});

test("tasks render translated machine codes instead of DTO labels", async () => {
  const value = await source("components/task3/TasksClient.tsx");
  assert.doesNotMatch(value, /\.state_label|\.phase_label/);
  assert.match(value, /presentTask/);
});

test("Task 3 styles consume semantic tokens and contain no raw color literals", async () => {
  const value = await source("components/task3/task3.css");
  assert.doesNotMatch(value, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  assert.match(value, /var\(--surface/);
});

test("Analyze applies a query Run ref only after the pending Run list is loaded", async () => {
  const value = await source("components/task3/AnalyzeClient.tsx");
  assert.match(value, /new URLSearchParams\(window\.location\.search\)\.get\("run"\)/);
  assert.match(value, /pending\.find\(\(run\) => run\.run_ref === requestedRunRef\)/);
  assert.match(value, /requestedRun\?\.id \?\? \(pending\.length === 1 \? pending\[0\]\.id : null\)/);
});
