import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach shell implements side-by-side, overlay, and full-content modes", async () => {
  const shell = await source("components/task6/CoachSidebar.tsx");
  const appShell = await source("components/task3/AppShell.tsx");
  const appStyles = await source("components/task3/task3.css");
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  const contracts = await source("lib/contracts.ts");
  const primitives = await source("ui/primitives.tsx");
  assert.match(shell, /1160/);
  assert.match(shell, /840/);
  assert.match(shell, /useState\(\s*\(\) => typeof document === "undefined" \? 0 : document\.documentElement\.clientWidth/);
  assert.match(contracts, /COACH_MIN_WIDTH\s*=\s*320/);
  assert.match(contracts, /COACH_DEFAULT_WIDTH\s*=\s*360/);
  assert.match(contracts, /COACH_MAX_WIDTH\s*=\s*480/);
  assert.match(contracts, /COACH_WIDTH_STEP\s*=\s*16/);
  assert.match(appShell, /--task3-coach-width/);
  assert.match(appStyles, /var\(--task3-coach-width/);
  assert.match(appShell, /useAnimatedPresence\(showCoach,\s*160\)/);
  assert.match(appShell, /data-coach-open=\{coachPresence\.state === "open" \|\| undefined\}/);
  assert.match(appShell, /coachPresence\.present\s*\?/);
  assert.match(shell, /setPointerCapture/);
  assert.match(shell, /onPointerMove/);
  assert.match(shell, /aria-valuemin/);
  assert.match(shell, /data-state=\{state\}/);
  assert.doesNotMatch(shell, /if \(!open\) return null/);
  assert.match(panel, /aria-live/);
  assert.match(styles, /transition:\s*transform 200ms cubic-bezier\(0\.32, 0\.72, 0, 1\),\s*opacity 200ms cubic-bezier\(0\.32, 0\.72, 0, 1\)/);
  assert.match(styles, /data-state="closed"[\s\S]*translateX\(16px\)/);
  assert.doesNotMatch(styles, /animation:\s*task6-coach-slide/);
  assert.match(primitives, /requestAnimationFrame\(\(\) => \{\s*frame = window\.requestAnimationFrame/);
  assert.match(styles, /\.task6-tool-step\[data-state="active"\] \.task6-tool-dot::after[\s\S]*animation:\s*task6-tool-pulse/);
  assert.doesNotMatch(styles, /\.task6-tool-step\[data-state="active"\] \.task6-tool-dot\s*\{[^}]*animation:/);
  assert.match(styles, /prefers-reduced-motion/);
  assert.match(styles, /data-mode="side-by-side"\]\[data-state="closed"\][\s\S]*?position:\s*fixed/);
  assert.match(styles, /contain:\s*layout paint/);
  assert.match(styles, /data-mode="side-by-side"\][\s\S]*?overflow:\s*clip/);
  assert.doesNotMatch(styles, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
});

test("Coach availability and empty states keep separate responsive semantics", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  assert.match(panel, /className="task6-coach-availability" data-state=\{headerState\.state\}/);
  assert.doesNotMatch(panel, /<span className="task6-coach-state" data-state=/);
  assert.match(styles, /\.task6-coach-header-row\s*\{[\s\S]*grid-template-columns:\s*auto minmax\(0, 1fr\) auto/);
  assert.match(styles, /\.task6-coach-availability\s*\{[\s\S]*overflow-wrap:\s*anywhere/);
  assert.match(styles, /\.task6-coach-context-line\s*\{[\s\S]*overflow-wrap:\s*anywhere/);
  assert.match(styles, /\.task6-coach-panel > \.task6-coach-state\s*\{[\s\S]*white-space:\s*normal/);
  assert.doesNotMatch(styles, /^\.task6-coach-state\s*\{/m);
});

test("Coach context is removable and L0 payloads never enter the UI adapter", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const contracts = await source("lib/contracts.ts");
  assert.match(coach, /detachCoachContext/);
  assert.match(coach, /stopCoachAgentRun/);
  assert.match(coach, /retryCoachAgentRun/);
  assert.match(coach, /decideCoachConfirmation/);
  assert.match(coach, /已定位/);
  assert.match(coach, /message\.context_refs/);
  assert.match(contracts, /presentCoachContext/);
  assert.doesNotMatch(coach, /video_path|raw_trace|protobuf|api_key|access_token|refresh_token/);

  assert.match(coach, /cancelable: true/);
  assert.match(coach, /const located = !window\.dispatchEvent\(/);
  assert.match(coach, /setFeedback\(located \? "已定位" : "未能定位，请重试。"\)/);
});

test("Settings route covers Provider, Profile, capture, theme, and Storage", async () => {
  const page = await source("app/settings/page.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(page, /SettingsWorkspace/);
  for (const label of ["Provider", "配置档", "自动采集", "主题", "存储"]) {
    assert.match(settings, new RegExp(label));
  }
  assert.match(settings, /useTheme/);
  assert.match(settings, /Stats 自动读取优先/);
  assert.match(settings, /不提供自动清理/);
  assert.match(settings, /getProviderAuthOperation/);
  assert.match(settings, /cancelProviderAuthOperation/);
  for (const status of ["等待认证输入", "授权成功", "已取消", "已超时", "授权失败"]) {
    assert.match(settings, new RegExp(status));
  }
  assert.doesNotMatch(settings, />\s*一键清空\s*</);
  assert.doesNotMatch(settings, /\{profile\.status\}|\{capture\.raw_input_permission\}|Account/);
});

test("Settings section navigation follows the current URL hash", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /window\.location\.hash\.slice\(1\)/);
  assert.match(settings, /window\.addEventListener\("hashchange", syncActiveNav\)/);
  assert.match(settings, /window\.removeEventListener\("hashchange", syncActiveNav\)/);
  assert.doesNotMatch(settings, /const activeNav = "llm-provider"/);
});

test("Settings section navigation stays below the sticky app toolbar", async () => {
  const styles = await source("components/task6/task6.css");
  assert.match(styles, /\.task6-settings-nav\s*\{[\s\S]*position:\s*sticky;[\s\S]*top:\s*68px;/);
  assert.match(styles, /\.task6-settings-section\s*\{[\s\S]*scroll-margin-top:\s*68px;/);
});

test("Settings hides section navigation at the narrow breakpoint", async () => {
  const styles = await source("components/task6/task6.css");
  assert.match(styles, /@media \(max-width: 839px\)[\s\S]*\.task6-settings-nav\s*\{[\s\S]*display:\s*none;/);
});

test("Settings keeps Profile actions together and moves boundary copy into an accessible tooltip", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6.css");
  assert.match(settings, /className="task6-profile-actions"/);
  assert.match(settings, /variant="danger">删除<\/Button>/);
  assert.match(settings, /aria-describedby="task6-profile-help"/);
  assert.match(settings, /id="task6-profile-help" role="tooltip"/);
  assert.doesNotMatch(settings, /<p className="task6-muted">Stats 自动读取优先/);
  assert.doesNotMatch(settings, /profile_default/);
  assert.doesNotMatch(settings, /偏好只保存在本机/);
  assert.match(styles, /\.task6-profile-summary\s*\{[\s\S]*position:\s*relative;[\s\S]*flex:\s*1;/);
  assert.match(styles, /\.task6-info\s*\{[\s\S]*position:\s*static;/);
});

test("Settings Provider type and auth selects match the shared field height", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const theme = await source("ui/theme.css");
  assert.match(settings, /<select className="ac-field__control" onChange=\{\(event\) => \{\s*const nextProviderId/);
  assert.match(settings, /<select className="ac-field__control" onChange=\{\(event\) => setNewAuthMode/);
  assert.match(theme, /\.ac-field__control\s*\{[\s\S]*height:\s*38px/);
});

test("Settings auto-detects custom Provider protocols and keeps a fallback choice", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /custom_anthropic_compatible/);
  assert.match(settings, /anthropic-messages/);
  assert.match(settings, /customKind === "custom_anthropic_compatible" \? "https:\/\/provider\.example" : "https:\/\/provider\.example\/v1"/);
  assert.match(settings, /discoverCustomProviderModels/);
  assert.match(settings, /customProtocolNeedsChoice/);
  assert.match(settings, /customProtocolConfirmed/);
  assert.match(settings, /getProviderCatalog\(\)\.catch\(\(\) => null\)/);
  assert.match(settings, /window\.setTimeout\(\(\) => \{/);
  assert.doesNotMatch(settings, /onClick=\{\(\) => void discoverCustomModels\(\)\}/);
  assert.match(settings, /列表中没有需要的 Model ID/);
  assert.match(settings, /isCustomProviderKind\(profile\.kind\)/);
});

test("closing Coach persists the user's preference", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /const closeCoach/);
  assert.match(shell, /COACH_OPEN_KEY, "closed"/);
  assert.match(shell, /onClose=\{closeCoach\}/);
});

test("Settings and Coach use primitives and expose focus-safe dialogs", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const coach = await source("components/task6/CoachPanel.tsx");
  const combined = `${settings}\n${coach}`;
  assert.match(combined, /<Dialog/);
  assert.match(combined, /<Notice/);
  assert.match(combined, /<Button/);
  assert.doesNotMatch(combined, /style=\{\{[^}]*color|#[0-9a-fA-F]{3,8}\b/);
});

test("KovaaK and Coach status colors follow their semantic state", async () => {
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  const coach = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /feedback\.tone === "success"\s*\? <Status tone="success">/);
  assert.doesNotMatch(panel, /feedback\.tone === "success" \? "info"/);
  assert.match(coach, /item\.status === "completed" \? "success"/);
});

test("Settings hosts the KovaaK connection surface without adding a Benchmark route or score-only Coach command", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  const fixtures = await source("fixtures/task7-fixtures.ts");
  assert.match(settings, /KovaaKConnectionPanel/);
  assert.match(panel, /KovaaK 成绩/);
  assert.match(panel, /S2 训练单/);
  assert.doesNotMatch(panel, /Viscose S2/);
  assert.match(panel, /aiming-cookie:coach-kovaak-intent/);
  assert.match(panel, /sessionStorage\.setItem/);
  assert.match(panel, /window\.location\.assign\("\/history"\)/);
  assert.match(panel, /Control Tracking|Reactive Tracking|Flick Tech|Click Timing/);
  assert.match(panel, /该 ID 会保存在本机/);
  assert.match(panel, /不会发送给 Coach Provider/);
  assert.match(panel, /保存在本机，不回显/);
  assert.doesNotMatch(panel, /不会保存或展示|仅读取时本次使用/);
  assert.doesNotMatch(panel, /createCoachAgentRun|training-plan|execution|retest/);
  assert.doesNotMatch(settings, /Benchmark/);
  assert.match(fixtures, /export const KOVAAK_SCORES[\s\S]*availability: "unavailable"/);
  assert.doesNotMatch(fixtures, /黄金 III|黄金 I|白银 I/);
});

test("Settings keeps its narrow tooltip inside the viewport and removes unreachable mobile nav animation", async () => {
  const styles = await source("components/task6/task6.css");
  assert.match(styles, /\.task6-info-tooltip\s*{[^}]*left:\s*0;[^}]*right:\s*auto;/s);
  const narrow = styles.slice(
    styles.indexOf("@media (max-width: 839px)"),
    styles.indexOf("@media (prefers-reduced-motion: reduce)"),
  );
  assert.match(narrow, /\.task6-settings-nav\s*{\s*display:\s*none;/);
  assert.doesNotMatch(narrow, /\.task6-settings-nav-title/);
});

test("review KovaaK refresh reports zero completion for an empty score corpus", async () => {
  const { apiScenario, handleReviewApiRequest } = await import("../fixtures/task7-fixtures");
  const result = handleReviewApiRequest(apiScenario(), {
    method: "POST",
    path: "/api/kovaak-connection/refresh",
  });
  assert.equal(result.status, 200);
  assert.deepEqual(
    (result.body as { difficulty_counts: { easier: number; medium: number } }).difficulty_counts,
    { easier: 0, medium: 0 },
  );
});

test("Coach reads current training locally and turns shortcut intents into drafts only", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const sidebar = await source("components/task6/CoachSidebar.tsx");
  assert.match(coach, /getCurrentTraining/);
  assert.match(coach, /current_training\.v1/);
  assert.match(coach, /aiming-cookie:coach-kovaak-intent/);
  assert.match(coach, /sessionStorage\.getItem/);
  assert.match(coach, /setDraft/);
  assert.match(coach, /slice\(0, 3\)/);
  assert.match(coach, /disabled={capability !== "ready" \|\| !item\.display_name}/);
  assert.doesNotMatch(sidebar, /capability !== "ready"/);
  assert.doesNotMatch(coach, /createTrainingPlan|recordTrainingExecution|recordRetest|completeTraining/);
});

test("Coach training actions distinguish plan context from a reviewed KovaaK launch", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const desktop = await source("lib/desktop.ts");
  assert.match(coach, /当前训练计划/);
  assert.match(coach, /当前训练项目/);
  assert.match(coach, /task6-training-item-actions/);
  assert.doesNotMatch(coach, /task6-training-actions/);
  assert.match(coach, /在 KovaaK 中开始/);
  assert.match(coach, /scenario_profile_ref/);
  assert.match(coach, /正在查看的分析/);
  assert.match(coach, /已附加分析：/);
  assert.match(coach, /引用分析：/);
  assert.match(coach, /尚未绑定可启动的 KovaaK 场景/);
  assert.match(desktop, /scenario_open/);
  assert.match(desktop, /当前网页预览不能启动 KovaaK/);
  assert.doesNotMatch(coach, /steam:\/\//);
});

test("Coach current training animates expand and collapse without leaving interactive hidden content", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  assert.match(coach, /useAnimatedPresence\(trainingExpanded,\s*180\)/);
  assert.match(coach, /className="task6-training-reveal"/);
  assert.match(coach, /data-state=\{trainingPresence\.state\}/);
  assert.match(coach, /aria-hidden=\{!trainingExpanded \|\| undefined\}/);
  assert.match(coach, /inert=\{!trainingExpanded \|\| undefined\}/);
  assert.match(styles, /\.task6-training-reveal\s*\{[\s\S]*grid-template-rows:\s*0fr;[\s\S]*opacity:\s*0;[\s\S]*translateY\(-4px\)/);
  assert.match(styles, /\.task6-training-reveal\[data-state="open"\]\s*\{[\s\S]*grid-template-rows:\s*1fr;[\s\S]*opacity:\s*1;[\s\S]*translateY\(0\)/);
  assert.match(styles, /prefers-reduced-motion:\s*reduce[\s\S]*\.task6-training-reveal/);
});
