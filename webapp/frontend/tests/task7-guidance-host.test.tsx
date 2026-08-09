import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("GuidanceHost owns semantic navigation and bounded UI acknowledgement", async () => {
  const component = await source("components/task7/GuidanceHost.tsx");
  assert.match(component, /resolveGuidanceTarget/);
  assert.match(component, /validateGuidancePrefill/);
  assert.match(component, /acknowledgeCoachGuidance/);
  assert.match(component, /aiming-cookie:coach-guidance/);
  assert.match(component, /aiming-cookie:coach-guidance-ack/);
  assert.match(component, /completed.*cancelled.*failed.*timed_out/);
  assert.match(component, /getElementById/);
  assert.doesNotMatch(component, /querySelector\(|document\.querySelector/);
  assert.doesNotMatch(component, /window\.location|window\.open|tauri|invoke\(/i);
});

test("AppShell keeps Coach mounted while semantic guidance crosses routes", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /<GuidanceHost/);
  assert.match(shell, /aria-hidden={!coachWorkspaceRoute/);
  assert.match(shell, /aiming-cookie:coach-guidance/);
});
