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
    source("components/task6/task6-settings.css"),
  ]);
  assert.match(historyStyles, /\.task4-col\s*\{[\s\S]*max-width:\s*580px[\s\S]*margin-inline:\s*auto/);
  // 设置页右屏升格为满铺浅色面板后（点点拍板），内容列降为面板内全宽
  // 透明滚动区；有界居中消费宽度（0912 点点：980 收窄到 760 集中）由滚动区
  // 直接子元素承载；设置页输入框统一 36px 标准档。
  assert.match(settingsStyles, /\.task6-settings-content\s*\{[^}]*width:\s*100%;/);
  assert.match(settingsStyles, /\.task6-settings-content\s*\{[^}]*overflow-y:\s*auto;/);
  assert.match(settingsStyles, /\.task6-settings-content\s*>\s*\*\s*\{[^}]*max-width:\s*760px;/);
  assert.match(settingsStyles, /\.task6-settings-content \.ac-field__control\s*\{[^}]*height:\s*var\(--control-height\)/);
});

test("Coach opens a center video pane from time-link analysis refs", async () => {
  const [shell, panel, videoPane] = await Promise.all([
    source("components/task3/AppShell.tsx"),
    source("components/task6/CoachPanel.tsx"),
    source("components/task7/CoachVideoPane.tsx"),
  ]);
  assert.match(shell, /<CoachVideoPane/);
  assert.match(shell, /videoTarget/);
  assert.match(shell, /onOpenVideo=/);
  assert.match(panel, /onOpenVideo/);
  assert.match(panel, /analysis_refs/);
  assert.match(videoPane, /getSession/);
  assert.match(videoPane, /presentAnalysisWorkspace/);
  assert.match(videoPane, /<VideoView/);
});

test("legacy Tasks and Analysis URLs are compatibility redirects", async () => {
  // /analyze 页面已整体移除（History 承接分析入口），不再保留 redirect。
  const routes = await Promise.all([
    source("app/tasks/page.tsx"),
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
  // 渐进渲染（加载慢专项 D）后 loading 不再整页 return：主框架（含退出
  // 按钮）常驻，独立顶层状态只剩错误兜底页，因此出现次数从 3 降为 2。
  assert.doesNotMatch(settings, /if \(loading\) return/);
  assert.ok((settings.match(/<SettingsExit/g) ?? []).length >= 2);
});
