import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 首启「开场分析」前端接线合同（PRD §6.1.1 / frontend-uiux-design §6.1.1）。
// 行为锁定在 lib/intro-session.test.ts 与 lib/rich-text.test.ts（node:test 数值）；
// 这里用源码断言锁组件确实按规格消费它们（同 task6-source 风格）。

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("AppShell triggers the intro session once, gated on onboarding completion", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // onboarding 完成度门控：只有明确 completed=true 才放行，null/unknown 不触发。
  assert.match(shell, /setOnboardingResolved\(state\.onboarding_completed === true\)/);
  assert.match(shell, /onboardingResolved/);
  // 触发点必须落在 Coach 工作区且启动路由已解析之后。
  assert.match(
    shell,
    /if \(!coachWorkspaceRoute \|\| !onboardingResolved \|\| !startupRouteResolved\) return undefined;/,
  );
  // 一次性：in-flight promise 存 ref，只在首次创建（StrictMode 双跑复用同一 promise）。
  assert.match(shell, /const introTriggerRef = useRef<ReturnType<typeof triggerIntroSession> \| null>\(null\)/);
  assert.match(shell, /if \(introTriggerRef\.current === null\) \{/);
  assert.match(shell, /introTriggerRef\.current = triggerIntroSession\(\{/);
  // 触发失败静默降级：只进前端错误通道，不弹 Toast。
  assert.match(shell, /logFrontendError\("intro-session"/);
  // 打开会话复用既有路径：整表刷新 + 路由切换，不重复命名标题。
  assert.match(shell, /void reloadCoachSessions\(sessionId\)\.catch\(\(\) => \{\}\)/);
  assert.match(shell, /router\.push\(`\/s\?sessionId=\$\{sessionId\}`\)/);
});

test("intro session invents no skip/close entry (开新对话/切会话即视为跳过)", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const trigger = await source("lib/intro-session.ts");
  const status = await source("lib/api.ts");
  for (const [name, value] of [
    ["AppShell", shell],
    ["intro-session", trigger],
    ["api", status],
  ] as const) {
    assert.doesNotMatch(value, /skipIntro|dismissIntro|intro-session[^\n]{0,40}skip/i, name);
  }
});

test("api client exposes GET/POST /coach/intro-session with local types", async () => {
  const api = await source("lib/api.ts");
  assert.match(api, /export async function getIntroSession\(/);
  assert.match(api, /export async function createIntroSession\(/);
  assert.match(api, /apiFetchSidecar\("\/coach\/intro-session", \{ method: "GET" \}/);
  assert.match(api, /apiFetchSidecar\("\/coach\/intro-session", \{ method: "POST" \}/);
  assert.match(api, /import type \{ IntroSessionCreated, IntroSessionStatus \} from "\.\/intro-session"/);
  // 契约未进 OpenAPI：局部类型必须标注为手写来源，且标题由 sidecar 定、前端不传。
  assert.match(api, /尚未进入后端 OpenAPI，故此处手写/);
  assert.match(api, /标题由 sidecar 定，前端不传/);
});

test("steam:// deep links are whitelisted as a protocol and routed through openExternalUrl", async () => {
  const rich = await source("lib/rich-text.ts");
  const message = await source("components/task7/CoachMessageText.tsx");
  assert.match(rich, /COACH_LINK_PROTOCOLS: ReadonlySet<string> = new Set\(\["steam:"\]\)/);
  assert.match(rich, /COACH_LINK_PROTOCOLS\.has\(url\.protocol\)/);
  // 富文本渲染层仍走受控外链出口（桌面 WebView2：opener 拉起，浏览器预览新标签）。
  assert.match(message, /openExternalUrl\(linkHref\)/);
});

test("tauri capability allows the steam:// opener scope additively", async () => {
  // 独立 capability 文件，不改动他人写的 default.json（只加不删）。
  const capability = await source("src-tauri/capabilities/steam.json");
  assert.match(capability, /"identifier": "external-deep-links"/);
  assert.match(capability, /"identifier": "opener:allow-open-url"/);
  assert.match(capability, /"url": "steam:\*"/);
  const base = await source("src-tauri/capabilities/default.json");
  assert.match(base, /"opener:default"/);
  assert.doesNotMatch(base, /steam:/);
});
