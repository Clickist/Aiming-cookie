import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("app shell exposes skip navigation, a SessionRail, and the Coach workspace without a spanning top bar", async () => {
  const value = await source("components/task3/AppShell.tsx");
  // 横跨顶栏已全局拆除（0910 拍板）：无 task3-toolbar 渲染，窗口三键全局常浮。
  assert.doesNotMatch(value, /task3-toolbar/);
  assert.match(value, /task3-wincontrols-global/);
  assert.match(value, /<main/);
  assert.match(value, /skip-link/);
  assert.match(value, /<SessionRail/);
  assert.match(value, /<CoachPanel/);
  assert.doesNotMatch(value, /Account/);
});

test("window controls float globally and title areas take over window dragging", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const controls = await source("components/task3/TauriWindowControls.tsx");
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const styles = await source("components/task3/task3.css");
  const rail = await source("components/task7/SessionRail.tsx");
  const history = await source("components/task4/HistoryClient.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 横跨顶栏已全局拆除（0910 拍板）：无 task3-toolbar 渲染，窗口三键全局常浮。
  assert.doesNotMatch(shell, /task3-toolbar/);
  assert.ok(shell.includes('className="task3-wincontrols-global"'));
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
  // 拖拽补偿：左栏品牌行 / 历史页头 / 设置标题行的左键空白处拖拽。
  assert.match(controls, /startWindowDraggingOnBackground/);
  assert.match(controls, /closest\("button, a, input, select, textarea"\)/);
  assert.ok(rail.includes('onMouseDown={startWindowDraggingOnBackground}'));
  assert.ok(history.includes('onMouseDown={startWindowDraggingOnBackground}'));
  assert.ok(settings.includes('onMouseDown={startWindowDraggingOnBackground}'));
  // 0911 审计 §10.1/10.2：设置内容面板上方全宽顶带也接同一拖拽助手；
  // 历史页 0911 点点第三批顶栏化——实底顶栏自身兼作拖拽带（第四轮升至 56px）。
  assert.ok(settings.includes('className="task6-settings-topband" onMouseDown={startWindowDraggingOnBackground}'));
  assert.ok(history.includes('className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}'));
  assert.doesNotMatch(history, /task4-topbar-band/);
  const settingsStyles = await source("components/task6/task6-settings.css");
  // 设置顶带随三键浮层对齐（0911 点点第二批：键 4px 起 + 44px 高 = 底缘 48px）。
  assert.match(settingsStyles, /\.task6-settings-topband\s*\{[^}]*height:\s*48px/);
  assert.match(settingsStyles, /\.task6-settings-main\s*\{[^}]*flex-direction:\s*column/);
  const historyStyles = await source("components/task4/task4.css");
  // 历史顶栏 56px 实底（0911 点点第三批 A 顶栏化 + 第四轮 56px：内容离窗顶远一点）。
  assert.match(historyStyles, /\.task4-page-head\s*\{[^}]*height:\s*56px/);
  assert.match(historyStyles, /\.task4-page-shell\s*\{[^}]*flex-direction:\s*column/);
  // 0911 点点：空对话首页无顶栏但顶部也要能拖——透明拖拽带占顶栏槽位。
  assert.match(shell, /coachHomeActive \? \(\s*\/\*[\s\S]*?\*\/\s*<div\s+aria-hidden="true"\s+className="task3-home-drag-band"\s+onMouseDown=\{startWindowDraggingOnBackground\}/);
  const shellStylesSource = await source("components/task3/task3.css");
  assert.match(shellStylesSource, /\.task3-home-drag-band\s*\{[^}]*height:\s*44px/);
  assert.match(onboarding, /className="task3-onboarding-brand"/);
  assert.ok(onboarding.includes('if (event.button === 0) void startWindowDragging();'));
  assert.match(onboarding, /<TauriWindowControls \/>/);
  assert.match(styles, /\.task3-wincontrols-global[^{]*\{[\s\S]*position:\s*fixed/);
  assert.match(styles, /\.task3-window-controls[^{]*\{[\s\S]*align-self:\s*stretch/);
  assert.match(styles, /\.task3-window-control[^{]*\{[\s\S]*width:\s*46px/);
  // 关闭键 hover 无独立红底，与另两键同走通用 hover 色（0911 终态拍板）。
  assert.doesNotMatch(styles, /task3-window-control--close:hover[^{]*\{[^}]*background/);
});

test("frameless startup keeps window controls mounted while product state resolves", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /const startupPending = coachWorkspaceRoute && !startupRouteResolved/);
  assert.doesNotMatch(shell, /if \(coachWorkspaceRoute && !startupRouteResolved\) return null/);
  assert.match(shell, /<TauriWindowControls \/>[\s\S]*\{startupPending \? null : \(/);
  assert.match(shell, /const keepSessionRailMounted = !shellHidden && !startupPending/);
});

test("desktop capture restore retries with backoff instead of giving up after the first failed read", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 旧的 best-effort 单发恢复（挂在冷启动第一次 getProductState 上）已移除：
  // 后端未就绪时静默放弃会让重启后的捕获一直停留在关闭状态。
  assert.doesNotMatch(shell, /best-effort capture restore on restart/);
  assert.match(shell, /const CAPTURE_RESTORE_MAX_ATTEMPTS = 10;/);
  assert.match(shell, /const CAPTURE_RESTORE_FIRST_DELAY_MS = 1_000;/);
  assert.match(shell, /const CAPTURE_RESTORE_MAX_DELAY_MS = 30_000;/);
  assert.match(
    shell,
    /Math\.min\(CAPTURE_RESTORE_FIRST_DELAY_MS \* 2 \*\* attempt, CAPTURE_RESTORE_MAX_DELAY_MS\)/,
  );
  assert.match(shell, /attempt \+ 1 >= CAPTURE_RESTORE_MAX_ATTEMPTS\) return;/);
  // 门控语义与启动路由一致：未走完 onboarding 明确无需恢复、立即停止。
  assert.match(
    shell,
    /未完成 onboarding 明确无需恢复[\s\S]{0,200}?state\.availability === "available" && state\.onboarding_completed !== true\) return;[\s\S]{0,200}?await setDesktopCaptureEnabled\(true\);/,
  );
  // 组件卸载或路由离开 Coach 工作区时清理退避定时器并停止重试。
  assert.match(shell, /\(\) => \{\s*cancelled = true;\s*if \(timer\) clearTimeout\(timer\);\s*\};/);
});

test("AppShell is the only mounted Coach owner on Coach routes", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const routePage = await source("components/task7/CoachWorkspacePage.tsx");
  // 保持挂载改版后不再条件卸载：非 coach 路由由 display:none 承担隐藏。
  assert.match(shell, /display: coachWorkspaceRoute \? undefined : "none",/);
  assert.doesNotMatch(shell, /hidden=\{!coachWorkspaceRoute\}/);
  assert.match(shell, /<CoachPanel/);
  assert.doesNotMatch(routePage, /import[\s\S]*CoachPanel|<CoachPanel|getDefaultProviderStatus|attachCoachContext/);
  assert.match(routePage, /return null/);
});

test("coach view stays mounted off-coach routes and hides only via display", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 保持挂载（点点拍板）：切 /history、/settings 不卸载 CoachPanel/顶栏/stage
  // ——回来不重新拉消息、SSE 与流式思考段不丢；隐藏完全依赖外层
  // .task3-coach-view 既有的 display:none，不再有条件渲染分支。
  assert.doesNotMatch(shell, /coachWorkspaceRoute \? \(/);
  assert.match(shell, /display: coachWorkspaceRoute \? undefined : "none",/);
});

test("AppShell opens a fresh draft for intent navigation but keeps the primary session otherwise", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 带分析意图进入且无进行中会话 → 新草稿承接新意图（独立 effect 响应路由变化）。
  assert.match(shell, /get\("intent"\) !== "coach-analysis"\) return;[\s\S]*?setDraftSession\(true\);/);
  assert.match(shell, /window\.history\.replaceState\(null, "", window\.location\.pathname\);/);
  // 其余情况恢复 primary 会话（上次对话的延续）。
  assert.match(shell, /const primary = coachSessions\.find\(\(session\) => session\.kind === "primary"\);/);
});

test("retired AppBar nav and Tasks center styles are gone", async () => {
  const styles = await source("components/task3/task3.css");
  assert.doesNotMatch(styles, /\.task3-primary-nav|\.task3-tool-nav|\.t-btn\b|\.t-icon\b|\.task3-tasks-panel|\.task3-task-item|\.task3-stage-stepper/);
  // 横跨顶栏已全局拆除（0910 拍板）：.task3-toolbar 样式删除（spacer 仍被
  // onboarding 品牌行使用），全局常浮三键样式存在。
  assert.doesNotMatch(styles, /\.task3-toolbar(?!-spacer)[^{]*\{/);
  assert.match(styles, /\.task3-wincontrols-global[^{]*\{[\s\S]*position:\s*fixed/);
  assert.match(styles, /\.task3-mode-badge|\.task3-preview-badge/);
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

test("app shell removes the spanning top bar and makes Settings a full-bleed overlay", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const value = await source("components/task3/task3.css");
  // 横跨顶栏已全局拆除（0910 拍板）：.task3-toolbar 样式不存在，设置
  // overlay 不再给顶栏让 48px，直接满铺。
  assert.doesNotMatch(value, /\.task3-toolbar(?!-spacer)[^{]*\{/);
  assert.match(value, /\.task3-route-content\[data-settings-page="true"\][\s\S]*position:\s*fixed/);
  assert.match(value, /inset:\s*0;/);
  assert.doesNotMatch(value, /task3-route-fade/);
  assert.match(shell, /useAnimatedPresence\(settingsRoute, 160\)/);
  assert.match(shell, /settingsOverlayChildren/);
  assert.match(shell, /settingsPresence\.state === "open" \? "open" : "opening"/);
  assert.match(value, /data-settings-page="true"[\s\S]*opacity var\(--duration-fast\) var\(--ease-out/);
  assert.match(value, /data-settings-motion="opening"[\s\S]*data-settings-motion="closing"[\s\S]*translateX\(8px\)/);
  assert.match(value, /prefers-reduced-motion: reduce[\s\S]*duration-reduced-motion\) var\(--ease-out/);
});

test("onboarding step and listbox entrances use short transform-and-opacity motion", async () => {
  const value = await source("components/task3/task3.css");
  assert.match(value, /task3-onboarding-enter var\(--duration-fast\) var\(--ease-out\)/);
  assert.match(value, /@keyframes task3-onboarding-enter[\s\S]*opacity:\s*0;[\s\S]*translateY\(4px\)/);
  assert.match(value, /task3-onboarding-dropdown-menu[\s\S]*transform-origin:\s*top center/);
  assert.match(value, /task3-onboarding-dropdown-enter var\(--duration-fast\) var\(--ease-out\)/);
  assert.match(value, /@keyframes task3-onboarding-dropdown-enter[\s\S]*scale\(0\.97\)/);
});

test("skip link keeps its hiding transform under prefers-reduced-motion", async () => {
  const value = await source("components/task3/task3.css");
  const reduced = value.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/);
  assert.ok(reduced, "reduced-motion override block exists");
  const rules = reduced[0].replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(rules, /task3-skip-link/);
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
  assert.match(value, /notifySessionFeedback\("未能归档会话，请重试。"\)/);
  assert.match(value, /notifySessionFeedback\("未能删除会话，请重试。"\)/);
  assert.match(value, /操作已完成，但会话列表暂时未能刷新。/);
  // seq 兼作重挂载 key 与 onClose 新鲜度校验：迟到的旧关闭不清掉新提示。
  assert.match(value, /<Toast key=\{sessionFeedback\.seq\}/);
  assert.match(value, /current && current\.seq === sessionFeedback\.seq \? null : current/);
});

test("SessionRail is the persistent left navigation without a right Coach sidebar", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(shell, /showSessionRail = !shellHidden/);
  assert.doesNotMatch(shell, /CoachSidebar/);
  assert.doesNotMatch(shell, /data-coach-open/);
  assert.match(styles, /data-session-rail="true"[^{]*\{[\s\S]*grid-template-columns:\s*var\(--task7-rail-width, 292px\) minmax\(0, 1fr\)/);
  assert.match(styles, /\.task3-workspace > \.task7-session-rail[^{]*\{[^}]*height:\s*calc\(100vh - var\(--task3-window-inset\) \* 2\)/);
  assert.match(styles, /\.task3-app[^{]*\{[\s\S]*overflow-x:\s*clip/);
});

test("Coach workspace fills the viewport so the composer stays at the bottom", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const styles = await source("components/task3/task3.css");
  assert.match(shell, /data-coach-workspace=\{coachWorkspaceRoute \|\| undefined\}/);
  assert.match(styles, /\.task3-workspace\[data-coach-workspace="true"\][^{]*\{[^}]*height:\s*calc\(100vh - var\(--task3-window-inset\) \* 2\)/);
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

test("KovaaK onboarding step is retired while the shared connection panel stays in Settings", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  // 0912 点点拍板：onboarding 收敛为两步，KovaaK 连接不再引导，仍可在设置中连接。
  assert.doesNotMatch(onboarding, /KovaaKConnectionPanel/);
  assert.match(onboarding, /useState<1 \| 2>\(1\)/);
  assert.match(onboarding, /共 2 步/);
  assert.doesNotMatch(onboarding, /共 3 步|setStep\(3\)|step !== 3/);
  assert.match(settings, /<KovaaKConnectionPanel context="settings" \/>/);
  assert.match(panel, /getKovaaKConnection/);
  assert.match(panel, /saveKovaaKConnection/);
  assert.match(panel, /refreshKovaaKConnection/);
  assert.match(panel, /deleteKovaaKConnection/);
  assert.match(panel, /getKovaaKScores/);
  // 0912 点点拍板：成绩单不上屏，Coach 意图触发器随之退役（Coach 侧后台读取保留）。
  assert.doesNotMatch(panel, /COACH_PENDING_INTENT_KEY|aiming-cookie:coach-kovaak-intent/);
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
