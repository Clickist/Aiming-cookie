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
  assert.match(shell, /1160/);
  assert.match(shell, /840/);
  assert.match(contracts, /COACH_MIN_WIDTH\s*=\s*320/);
  assert.match(contracts, /COACH_DEFAULT_WIDTH\s*=\s*360/);
  assert.match(contracts, /COACH_MAX_WIDTH\s*=\s*480/);
  assert.match(contracts, /COACH_WIDTH_STEP\s*=\s*16/);
  assert.match(appShell, /--task3-coach-width/);
  assert.match(appStyles, /var\(--task3-coach-width/);
  assert.match(shell, /setPointerCapture/);
  assert.match(shell, /onPointerMove/);
  assert.match(shell, /aria-valuemin/);
  assert.match(panel, /aria-live/);
  assert.match(styles, /prefers-reduced-motion/);
  assert.doesNotMatch(styles, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
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

test("Settings hosts the KovaaK connection surface without adding a Benchmark route or score-only Coach command", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  assert.match(settings, /KovaaKConnectionPanel/);
  assert.match(panel, /KovaaK 成绩/);
  assert.match(panel, /aiming-cookie:coach-kovaak-intent/);
  assert.match(panel, /sessionStorage\.setItem/);
  assert.match(panel, /window\.location\.assign\("\/history"\)/);
  assert.match(panel, /Control Tracking|Reactive Tracking|Flick Tech|Click Timing/);
  assert.doesNotMatch(panel, /createCoachAgentRun|training-plan|execution|retest/);
  assert.doesNotMatch(settings, /Benchmark/);
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
