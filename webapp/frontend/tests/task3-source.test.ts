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
