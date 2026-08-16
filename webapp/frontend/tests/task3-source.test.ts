import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("app shell exposes an AppBar, skip navigation, a SessionRail, and the Coach workspace", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /<header/);
  assert.match(value, /<main/);
  assert.match(value, /skip-link/);
  assert.match(value, /<SessionRail/);
  assert.match(value, /<CoachPanel/);
  assert.doesNotMatch(value, /Account/);
});

test("AppBar hosts the frameless Tauri window controls", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const controls = await source("components/task3/TauriWindowControls.tsx");
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const styles = await source("components/task3/task3.css");
  assert.ok(shell.includes('className="task3-toolbar"'));
  assert.ok(shell.includes('if (event.button === 0) void startWindowDragging();'));
  assert.match(shell, /<TauriWindowControls \/>/);
  assert.match(controls, /@tauri-apps\/api\/window/);
  assert.match(controls, /getCurrentWindow\(\)/);
  assert.match(controls, /appWindow\.minimize\(\)/);
  assert.match(controls, /appWindow\.toggleMaximize\(\)/);
  assert.match(controls, /appWindow\.close\(\)/);
  assert.match(controls, /appWindow\.startDragging\(\)/);
  assert.match(controls, /onMouseDown={stopTitleBarDrag}/);
  assert.match(controls, /event\.stopPropagation\(\)/);
  assert.ok(controls.includes('runWindowControl("minimize")'));
  assert.ok(controls.includes('runWindowControl("toggleMaximize")'));
  assert.ok(controls.includes('runWindowControl("close")'));
  assert.match(onboarding, /className="task3-onboarding-brand"/);
  assert.ok(onboarding.includes('if (event.button === 0) void startWindowDragging();'));
  assert.match(onboarding, /<TauriWindowControls \/>/);
  assert.match(styles, /\.task3-window-controls[^{]*\{[\s\S]*align-self:\s*stretch/);
  assert.match(styles, /\.task3-window-control[^{]*\{[\s\S]*width:\s*46px/);
  assert.match(styles, /\.task3-window-control--close:hover[^{]*\{[\s\S]*background:\s*var\(--error\)/);
});

test("frameless startup keeps window controls mounted while product state resolves", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /const startupPending = coachWorkspaceRoute && !startupRouteResolved/);
  assert.doesNotMatch(shell, /if \(coachWorkspaceRoute && !startupRouteResolved\) return null/);
  assert.match(shell, /<TauriWindowControls \/>[\s\S]*\{startupPending \? null : \(/);
  assert.match(shell, /const keepSessionRailMounted = !shellHidden && !startupPending/);
});

test("AppShell is the only mounted Coach owner on Coach routes", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const routePage = await source("components/task7/CoachWorkspacePage.tsx");
  assert.match(shell, /coachWorkspaceRoute \? \(/);
  assert.doesNotMatch(shell, /hidden=\{!coachWorkspaceRoute\}/);
  assert.match(shell, /<CoachPanel/);
  assert.doesNotMatch(routePage, /import[\s\S]*CoachPanel|<CoachPanel|getDefaultProviderStatus|attachCoachContext/);
  assert.match(routePage, /return null/);
});

test("AppShell opens a fresh draft for intent navigation but keeps the primary session otherwise", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 带分析意图进入且无进行中会话 → 新草稿承接新意图（独立 effect 响应路由变化）。
  assert.match(shell, /get\("intent"\) !== "coach-analysis"\) return;[\s\S]*?setDraftSession\(true\);/);
  assert.match(shell, /window\.history\.replaceState\(null, "", window\.location\.pathname\);/);
  // 其余情况恢复 primary 会话（上次对话的延续）。
  assert.match(shell, /const primary = coachSessions\.find\(\(session\) => session\.kind === "primary"\);/);
});

test("app shell removes transient status controls from the AppBar", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.doesNotMatch(value, /task3-capture-status/);
  assert.doesNotMatch(value, /task3-analysis-status/);
  assert.doesNotMatch(value, /task3-provider-status/);
  assert.doesNotMatch(value, /CoachSidebar/);
  assert.doesNotMatch(value, /task3-primary-nav/);
  assert.doesNotMatch(value, /href="\/analyze"/);
  assert.doesNotMatch(value, /href="\/tasks"/);
});

test("app shell styles keep the 48px AppBar and make Settings a top-bar-below overlay", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const value = await source("components/task3/task3.css");
  assert.match(value, /\.task3-toolbar[^{]*\{[\s\S]*height:\s*48px/);
  assert.match(value, /\.task3-route-content\[data-settings-page="true"\][\s\S]*position:\s*fixed/);
  assert.match(value, /inset:\s*48px 0 0/);
  assert.doesNotMatch(value, /task3-route-fade/);
  assert.match(shell, /useAnimatedPresence\(settingsRoute, 160\)/);
  assert.match(shell, /settingsOverlayChildren/);
  assert.match(shell, /settingsPresence\.state === "open" \? "open" : "opening"/);
  assert.match(value, /data-settings-page="true"[\s\S]*opacity 160ms var\(--ease-out/);
  assert.match(value, /data-settings-motion="opening"[\s\S]*data-settings-motion="closing"[\s\S]*translateX\(8px\)/);
  assert.match(value, /prefers-reduced-motion: reduce[\s\S]*duration-reduced-motion, 120ms/);
});

test("onboarding step and listbox entrances use short transform-and-opacity motion", async () => {
  const value = await source("components/task3/task3.css");
  assert.match(value, /task3-onboarding-enter 180ms cubic-bezier\(0\.23, 1, 0\.32, 1\)/);
  assert.match(value, /@keyframes task3-onboarding-enter[\s\S]*opacity:\s*0;[\s\S]*translateY\(4px\)/);
  assert.match(value, /task3-onboarding-dropdown-menu[\s\S]*transform-origin:\s*top center/);
  assert.match(value, /task3-onboarding-dropdown-enter 180ms cubic-bezier\(0\.23, 1, 0\.32, 1\)/);
  assert.match(value, /@keyframes task3-onboarding-dropdown-enter[\s\S]*scale\(0\.97\)/);
});

test("session selection updates the Coach deep link", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /const coachWorkspaceRoute = pathname === "\/" \|\| pathname === "\/s" \|\| pathname === "\/s\/"/);
  assert.match(value, /useSearchParams/);
  assert.match(value, /parseSessionId\(searchParams\.get\("sessionId"\)\)/);
  assert.match(value, /router\.push\(`\/s\?sessionId=\$\{session\.id\}`\)/);
  assert.match(value, /routeSessionId !== null/);
});

test("session archive and delete failures surface through the existing Toast", async () => {
  const value = await source("components/task3/AppShell.tsx");
  assert.match(value, /import \{[^}]*Toast[^}]*\} from "@\/ui\/primitives"/);
  assert.match(value, /setSessionFeedback\("未能归档会话，请重试。"\)/);
  assert.match(value, /setSessionFeedback\("未能删除会话，请重试。"\)/);
  assert.match(value, /操作已完成，但会话列表暂时未能刷新。/);
  assert.match(value, /<Toast onClose=\{\(\) => setSessionFeedback\(null\)\}>/);
});

test("SessionRail is the persistent left navigation without a right Coach sidebar", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(shell, /showSessionRail = !shellHidden/);
  assert.doesNotMatch(shell, /CoachSidebar/);
  assert.doesNotMatch(shell, /data-coach-open/);
  assert.match(styles, /data-session-rail="true"[^{]*\{[\s\S]*grid-template-columns:\s*var\(--task7-rail-width, 292px\) minmax\(0, 1fr\)/);
  assert.match(styles, /\.task3-workspace > \.task7-session-rail[\s\S]*height:\s*calc\(100vh - 48px\)/);
  assert.match(styles, /\.task3-app[^{]*\{[\s\S]*overflow-x:\s*clip/);
});

test("Coach workspace fills the viewport so the composer stays at the bottom", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(shell, /data-coach-workspace=\{coachWorkspaceRoute \|\| undefined\}/);
  assert.match(styles, /\.task3-workspace\[data-coach-workspace="true"\][^{]*\{[\s\S]*height:\s*calc\(100vh - 48px\)/);
  assert.match(styles, /\.task3-workspace\[data-coach-workspace="true"\] > \.task3-route-content[^{]*\{[\s\S]*display:\s*flex/);
  assert.match(styles, /\.task3-workspace\[data-coach-workspace="true"\] > \.task3-route-content[^{]*\{[\s\S]*flex-direction:\s*column/);
  assert.match(styles, /\.task3-coach-view[^{]*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(styles, /\.task3-coach-conversation[^{]*\{[\s\S]*justify-content:\s*center/);
});

test("AppShell keeps the existing Provider read for the SessionRail footer", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /getDefaultProviderStatus\(/);
  assert.doesNotMatch(shell, /listTasks\(/);
  assert.match(shell, /\}, \[shellHidden\]\)/);
});

test("Settings route hides the SessionRail and exposes a Coach return action", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(shell, /const settingsRoute = pathname\.startsWith\("\/settings"\)/);
  assert.match(shell, /const showSessionRail = !shellHidden && !settingsRoute/);
  assert.match(settings, /label="退出设置"/);
  assert.match(settings, /title="返回 Coach"/);
  assert.match(settings, /router\.push\("\/"\)/);
});

test("AppBar has no responsive status-control sizing", async () => {
  const value = await source("components/task3/task3.css");
  assert.doesNotMatch(value, /task3-capture-status|task3-analysis-status|task3-provider-status/);
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

test("onboarding preserves the API key draft while selecting and testing a model", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const selectModel = onboarding.slice(
    onboarding.indexOf("const selectModel"),
    onboarding.indexOf("const updateCustomConnection"),
  );
  const connect = onboarding.slice(
    onboarding.indexOf("const connect"),
    onboarding.indexOf("const submitPrompt"),
  );

  assert.doesNotMatch(selectModel, /setApiKey/);
  assert.doesNotMatch(connect, /setApiKey/);
  assert.match(connect, /profileId === null/);
  assert.match(connect, /updateProviderProfile\(profileId,/);
});

test("onboarding requires a Provider and enabled desktop capture before completion", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(onboarding, /task3-onboarding-wizard-actions/);
  assert.doesNotMatch(onboarding, /task3-onboarding-skip-tooltip|completeOnboarding\("skipped"\)/);
  assert.match(onboarding, /!desktop \|\| !captureOptIn/);
  assert.match(onboarding, /status\.raw_input_permission === "denied"/);
  assert.match(onboarding, /status\.runtime_health === "unavailable"/);
  assert.match(onboarding, /completeOnboarding\("connected"\)/);
  assert.match(styles, /task3-onboarding-status span::before/);
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

test("Task 3 styles consume semantic tokens and contain no raw color literals", async () => {
  const value = await source("components/task3/task3.css");
  const kovaak = await source("components/kovaak/kovaak.css");
  assert.doesNotMatch(value, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  assert.doesNotMatch(kovaak, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  assert.match(value, /var\(--surface/);
  assert.match(kovaak, /var\(--(?:on-)?surface/);
});
