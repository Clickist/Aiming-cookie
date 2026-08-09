import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("desktop window keeps the Coach workspace above the supported width floor", async () => {
  const config = JSON.parse(await source("src-tauri/tauri.conf.json")) as {
    app: { windows: Array<{ minWidth?: number; width?: number }> };
  };
  assert.equal(config.app.windows[0]?.minWidth, 1180);
  assert.ok((config.app.windows[0]?.width ?? 0) >= 1180);
});

test("History and Settings use a centered bounded consumption width", async () => {
  const [historyStyles, settingsStyles] = await Promise.all([
    source("components/task4/task4.css"),
    source("components/task6/task6.css"),
  ]);
  assert.match(historyStyles, /\.task4-page\s*\{[\s\S]*max-width:\s*1040px[\s\S]*margin-inline:\s*auto/);
  assert.match(settingsStyles, /\.task6-settings-layout\s*\{[\s\S]*max-width:\s*1040px[\s\S]*margin:\s*0 auto/);
});

test("Coach owns optional message cards and a center video pane", async () => {
  const [shell, panel, cards, videoPane] = await Promise.all([
    source("components/task3/AppShell.tsx"),
    source("components/task6/CoachPanel.tsx"),
    source("components/task7/CoachMessageCards.tsx"),
    source("components/task7/CoachVideoPane.tsx"),
  ]);
  assert.match(shell, /<CoachVideoPane/);
  assert.match(shell, /videoTarget/);
  assert.match(shell, /onOpenVideo=/);
  assert.match(panel, /<CoachMessageCards/);
  assert.match(panel, /onOpenVideo/);
  assert.match(cards, /message\.cards/);
  assert.doesNotMatch(cards, /JSON\.parse\(message\.content\)|dangerouslySetInnerHTML/);
  assert.match(videoPane, /getSession/);
  assert.match(videoPane, /presentAnalysisWorkspace/);
  assert.match(videoPane, /<VideoView/);
});

test("legacy Tasks and Analysis URLs are compatibility redirects", async () => {
  const routes = await Promise.all([
    source("app/tasks/page.tsx"),
    source("app/analyze/page.tsx"),
    source("app/analysis/page.tsx"),
    source("app/analysis/[analysisId]/page.tsx"),
  ]);
  for (const route of routes) {
    assert.match(route, /redirect\("\/(?:history)?"\)/);
  }
});

test("Settings exposes a return action in every top-level state", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /function SettingsExit/);
  assert.ok((settings.match(/<SettingsExit/g) ?? []).length >= 3);
});
