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

test("app shell hides the Coach entry on unsupported pages and marks the task count without layout width", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /\{coachSupported \? \(/);
  assert.doesNotMatch(value, /disabled=\{!coachSupported\}/);
  assert.doesNotMatch(value, /当前页面不支持 Coach/);
  assert.match(value, /task3-task-nav-dot/);
  assert.match(value, /key={pathname}/);
});

test("app shell styles encode active navigation and reduced-motion route transitions", async () => {
  const value = await source("components/task3/task3.css");
  assert.match(value, /a\[aria-current=\"page\"\][^{]*\{[\s\S]*border-bottom-color: var\(--primary\)/);
  assert.match(value, /task3-route-fade 140ms ease-out/);
  assert.match(value, /prefers-reduced-motion: reduce[\s\S]*task3-route-content/);
});

test("responsive shell entries fade without animating layout properties", async () => {
  const value = await source("components/task3/task3.css");
  assert.match(value, /\.task3-primary-nav\s*\{[\s\S]*opacity 160ms cubic-bezier\(0\.23, 1, 0\.32, 1\)[\s\S]*display 160ms allow-discrete/);
  assert.match(value, /\.task3-primary-nav a:first-child\s*\{[\s\S]*background-color 150ms ease-out[\s\S]*opacity 160ms cubic-bezier\(0\.23, 1, 0\.32, 1\)[\s\S]*display 160ms allow-discrete/);
  assert.match(value, /@media \(max-width: 720px\)[\s\S]*\.task3-primary-nav a:first-child\s*\{[\s\S]*opacity:\s*0;[\s\S]*translateY\(-2px\)[\s\S]*display 120ms allow-discrete/);
  assert.match(value, /@media \(max-width: 560px\)[\s\S]*\.task3-primary-nav\s*\{[\s\S]*opacity:\s*0;[\s\S]*translateY\(-2px\)[\s\S]*display 120ms allow-discrete/);
  assert.match(value, /prefers-reduced-motion: reduce[\s\S]*\.task3-primary-nav[\s\S]*transform:\s*none/);
  assert.doesNotMatch(value, /\.task3-primary-nav[^{}]*\{[^}]*transition:[^;}]*(?:width|height|padding|margin|gap|flex|grid|top|left)/);
});

test("onboarding never persists credentials in browser storage", async () => {
  const value = await source("components/task3/OnboardingFlow.tsx");
  assert.doesNotMatch(value, /localStorage|sessionStorage|indexedDB/);
});

test("onboarding provider catalog failure has error status semantics", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(onboarding, /setCatalogUnavailable\(true\)/);
  assert.match(onboarding, /connectionState === "failed" \|\| \(catalogUnavailable && !custom\)/);
  assert.match(onboarding, /catalogUnavailable && !custom/);
  assert.match(onboarding, /data-tone=\{statusTone\}/);
  assert.match(styles, /\[data-tone="error"\][^{]*\{[\s\S]*color: var\(--error\)/);
});

test("custom Provider auto-detects protocol and exposes protocol choice only as fallback", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  assert.doesNotMatch(onboarding, /useState\("http:\/\/127\.0\.0\.1:11434\/v1"\)/);
  assert.match(onboarding, /discoverCustomProviderModels/);
  assert.match(onboarding, /custom_anthropic_compatible/);
  assert.match(onboarding, /anthropic-messages/);
  assert.match(onboarding, /customKind === "custom_anthropic_compatible" \? "https:\/\/provider\.example" : "https:\/\/provider\.example\/v1"/);
  assert.match(onboarding, /window\.setTimeout\(\(\) => \{/);
  assert.doesNotMatch(onboarding, /onClick=\{\(\) => void discoverCustomModels\(\)\}/);
  assert.match(onboarding, /customProtocolNeedsChoice/);
  assert.match(onboarding, /customProtocolConfirmed/);
  assert.match(onboarding, /列表中没有需要的 Model ID/);
  assert.match(onboarding, /customModelState === "manual"/);
});

test("onboarding uses accessible Provider and Model listboxes without category tabs", async () => {
  const value = await source("components/task3/OnboardingFlow.tsx");
  assert.match(value, /aria-haspopup="listbox"/);
  assert.match(value, /aria-expanded=\{openMenu === "provider"\}/);
  assert.match(value, /aria-expanded=\{openMenu === "model"\}/);
  assert.match(value, /role="listbox"/);
  assert.match(value, /role="option"/);
  assert.match(value, /aria-live="polite"/);
  assert.match(value, /aria-atomic="true"/);
  assert.doesNotMatch(value, /role="tablist"/);
});

test("onboarding keeps a three-column action row with a status dot and skip tooltip", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(onboarding, /task3-onboarding-wizard-actions/);
  assert.match(onboarding, /task3-onboarding-skip-tooltip/);
  assert.match(styles, /task3-onboarding-status span::before/);
  assert.match(styles, /task3-onboarding-wizard-actions[^{]*\{[\s\S]*justify-content: space-between/);
  assert.match(styles, /task3-onboarding-skip-wrap:hover[\s\S]*task3-onboarding-skip-tooltip/);
});

test("KovaaK onboarding is optional and uses the shared identity-free connection panel", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  assert.match(onboarding, /KovaaKConnectionPanel/);
  assert.match(onboarding, /可选/);
  assert.match(panel, /getKovaaKConnection/);
  assert.match(panel, /saveKovaaKConnection/);
  assert.match(panel, /refreshKovaaKConnection/);
  assert.match(panel, /deleteKovaaKConnection/);
  assert.match(panel, /getKovaaKScores/);
  assert.match(panel, /aiming-cookie\.ui\.coach-pending-intent/);
  assert.doesNotMatch(panel, /syncKovaaKScores|steam_id|indexedDB/);
  assert.doesNotMatch(panel, /Storage\.setItem\([^\n]*(?:steamProfile|steam_profile|STEAM_ID|STEAM_PROFILE)/);
});

test("tasks render translated machine codes instead of DTO labels", async () => {
  const value = await source("components/task3/TasksClient.tsx");
  assert.doesNotMatch(value, /\.state_label|\.phase_label/);
  assert.match(value, /presentTask/);
});

test("Task 3 styles consume semantic tokens and contain no raw color literals", async () => {
  const value = await source("components/task3/task3.css");
  const kovaak = await source("components/kovaak/kovaak.css");
  assert.doesNotMatch(value, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  assert.doesNotMatch(kovaak, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  assert.match(value, /var\(--surface/);
  assert.match(kovaak, /var\(--(?:on-)?surface/);
});

test("Analyze applies a query Run ref only after the pending Run list is loaded", async () => {
  const value = await source("components/task3/AnalyzeClient.tsx");
  assert.match(value, /new URLSearchParams\(window\.location\.search\)\.get\("run"\)/);
  assert.match(value, /pending\.find\(\(run\) => run\.run_ref === requestedRunRef\)/);
  assert.match(value, /requestedRun\?\.id \?\? \(pending\.length === 1 \? pending\[0\]\.id : null\)/);
});

test("Analyze uses the available workspace width when details are absent or Coach is open", async () => {
  const client = await source("components/task3/AnalyzeClient.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(client, /data-layout=\{selectedRun \? "split" : "single"\}/);
  assert.match(styles, /\.task3-analyze-grid\[data-layout="single"\][\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.task3-workspace\[data-coach-open="true"\] \.task3-analyze-grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.task3-analyze-grid\[data-layout="single"\] \.task3-analyze-manual-cards[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
});
