import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach shell renders the existing full workspace in the main column", async () => {
  const appShell = await source("components/task3/AppShell.tsx");
  const appStyles = await source("components/task3/task3.css");
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  assert.match(appShell, /<CoachPanel/);
  // layoutMode 死 prop 已删：唯一调用点固定 full 档，无 pane 后缀派生。
  assert.doesNotMatch(appShell, /layoutMode/);
  assert.match(appShell, /data-session-rail/);
  assert.doesNotMatch(appShell, /CoachSidebar/);
  assert.match(appStyles, /task3-workspace\[data-session-rail="true"\]/);
  assert.match(styles, /\.task6-coach-panel/);
  assert.match(styles, /prefers-reduced-motion/);
  assert.doesNotMatch(styles, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
});

test("Coach availability and empty states keep separate responsive semantics", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  const shell = await source("components/task3/AppShell.tsx");
  const shellStyles = await source("components/task3/task3.css");
  // v6（0910）：旧 header 退役——状态点移入 AppShell 共用顶栏（仅绿点无文字），
  // "可用"文案与 availability 行不再存在。
  assert.doesNotMatch(panel, /task6-coach-availability/);
  assert.doesNotMatch(panel, /<span className="task6-coach-state" data-state=/);
  assert.match(shell, /className="task3-coach-status-dot"\s+data-state=\{capability\}/);
  assert.match(shellStyles, /\.task3-coach-status-dot\[data-state="ready"\]/);
  assert.doesNotMatch(styles, /\.task6-coach-context-line\s*\{[\s\S]*overflow-wrap:\s*anywhere/);
  assert.match(styles, /\.task6-coach-panel > \.task6-coach-state\s*\{[\s\S]*white-space:\s*normal/);
  assert.doesNotMatch(styles, /^\.task6-coach-state\s*\{/m);
});

test("Coach sends and polls agent runs through the shared API adapter", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  assert.match(coach, /stopCoachAgentRun/);
  assert.match(coach, /retryCoachAgentRun/);
  assert.match(coach, /getCoachAgentRun/);
  assert.doesNotMatch(coach, /video_path|raw_trace|protobuf|api_key|access_token|refresh_token/);
});

test("Settings route covers Provider, Profile, capture, theme, and Storage", async () => {
  const page = await source("app/settings/page.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const providerSection = await source("components/task6/ProviderSettingsSection.tsx");
  const combined = `${settings}\n${providerSection}`;
  assert.match(page, /SettingsWorkspace/);
  for (const label of ["Provider", "配置档", "自动采集", "主题", "存储"]) {
    assert.match(settings, new RegExp(label));
  }
  assert.match(settings, /useTheme/);
  assert.match(settings, /读取失败时才使用的默认值/);
  assert.match(settings, /总占用/);
  // Provider 的 OAuth 授权动作与其状态文案随主从式重做搬进分区组件。
  assert.match(providerSection, /getProviderAuthOperation/);
  assert.match(providerSection, /cancelProviderAuthOperation/);
  for (const status of ["等待认证输入", "授权成功", "已取消", "已超时", "授权失败"]) {
    assert.match(providerSection, new RegExp(status));
  }
  assert.doesNotMatch(combined, />\s*一键清空\s*</);
  assert.doesNotMatch(combined, /\{profile\.status\}|\{capture\.raw_input_permission\}|Account/);
});

test("Settings reuses the in-memory snapshot when revisiting and forces refresh after changes", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /let settingsSnapshot: SettingsSnapshot \| null = null/);
  assert.match(settings, /if \(!force && settingsSnapshot\)/);
  assert.match(settings, /await refresh\(true\)/);
});

test("Settings hash deep links map old anchor ids onto the new tabs", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /window\.location\.hash\.slice\(1\)/);
  assert.match(settings, /window\.addEventListener\("hashchange", syncActiveNav\)/);
  assert.match(settings, /window\.removeEventListener\("hashchange", syncActiveNav\)/);
  assert.doesNotMatch(settings, /const activeNav = "llm-provider"/);
  // 分区切换化后左栏点击只写 state；hash 深链经别名映射落到对应屏，
  // 历史页的 /settings#kovaak-directories 等旧锚点链接继续可达。
  assert.match(settings, /HASH_SECTION_ALIASES\[hash\] \?\? NAV_ITEMS\[0\]\.id/);
  assert.match(settings, /"kovaak-directories": "kovaak"/);
  assert.match(settings, /"external-telemetry": "capture"/);
  assert.match(settings, /"app-update": "general"/);
});

test("Settings section navigation stays visible while the content scrolls", async () => {
  const styles = await source("components/task6/task6-settings.css");
  // 设置 overlay 从应用工具栏（48px）下沿开始；内容锚点落点保留呼吸间距
  // ——导航与分区标题都不被遮挡。（旧 sticky 断言随 v6 面板化失效：nav 实为
  // 随面板 stretch，此前仅误匹配已退役的 .task6-coach-top，一并删除。）
  assert.match(styles, /\.task6-settings-section\s*\{[\s\S]*scroll-margin-top:\s*var\(--space-4\);/);
});

test("Settings hides section navigation at the narrow breakpoint", async () => {
  const styles = await source("components/task6/task6-settings.css");
  assert.match(styles, /@media \(max-width: 839px\)[\s\S]*\.task6-settings-nav\s*\{[\s\S]*display:\s*none;/);
});

test("Settings keeps the Profile override as a single three-state card action", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  // 三态按钮（0912 拍板）：灰「保存」（无输入，禁用）→ 橙「保存」（有未保存输入）
  // →「删除」（已覆盖，点击退回 Stats 读取值）。
  assert.match(settings, /profileOverrideActive \? \(/);
  assert.match(settings, /disabled=\{!profileDirty \|\| !profileHasInput\}/);
  assert.match(settings, /variant="primary">删除<\/Button>/);
  assert.doesNotMatch(settings, /aria-describedby="task6-profile-help"/);
  assert.doesNotMatch(settings, /<p className="task6-muted">Stats 自动读取优先/);
  assert.doesNotMatch(settings, /profile_default/);
  assert.doesNotMatch(settings, /偏好只保存在本机/);
  assert.match(styles, /\.task6-card-actions\s*\{[^}]*justify-content:\s*flex-end/);
});

test("Settings feeds the latest Stats calibration into the Profile placeholders", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /run\.stats_calibration/);
  assert.match(settings, /latestStatsCalibration\?\.cm_per_360 != null/);
  assert.match(settings, /latestStatsCalibration\?\.fov != null/);
});

test("Settings provider wizard selects match the shared field height", async () => {
  const providerSection = await source("components/task6/ProviderSettingsSection.tsx");
  const theme = await source("ui/theme.css");
  // 向导的 Model / 思考力度下拉与共享字段同一控件高度。
  assert.match(providerSection, /<select className="ac-field__control" onChange=\{\(event\) => wizardSetDraft\(\{ modelId: event\.target\.value \}\)\} value=\{wizardDraft\.modelId\}>/);
  assert.match(providerSection, /className="ac-field__control"\s*\n\s*onChange=\{\(event\) => wizardSetDraft\(\{ reasoningEffort/);
  assert.match(theme, /\.ac-field__control\s*\{[\s\S]*height:\s*var\(--control-height\)/);
});

test("Settings auto-detects custom Provider protocols and keeps a fallback choice", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const providerSection = await source("components/task6/ProviderSettingsSection.tsx");
  const helpers = await source("lib/provider-helpers.ts");
  assert.match(providerSection, /custom_anthropic_compatible/);
  assert.match(helpers, /anthropic-messages/);
  assert.match(providerSection, /customKind === "custom_anthropic_compatible" \? "https:\/\/provider\.example" : "https:\/\/provider\.example\/v1"/);
  assert.match(providerSection, /discoverCustomProviderModels/);
  // 协议识别失败的回退：hook 保留 needsProtocolChoice，向导留手动 Model ID。
  assert.match(helpers, /needsProtocolChoice/);
  assert.match(providerSection, /列表中没有需要的 Model ID/);
  assert.match(providerSection, /customProtocolConfirmed/);
  assert.match(settings, /getProviderCatalog\(\)\.catch\(\(\) => null\)/);
  // capture 首载 3s 竞速与 OAuth 授权轮询的异步形态各自保留。
  assert.match(settings, /window\.setTimeout\(\(\) => resolve\(null\), CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS\)/);
  assert.match(providerSection, /window\.setTimeout\(\(\) => \{/);
  assert.doesNotMatch(providerSection, /onClick=\{\(\) => void discoverCustomModels\(\)\}/);
  assert.match(providerSection, /isCustomProviderKind\(selectedProfile\.kind\)/);
});

test("Provider model selection does not reset the API key draft", async () => {
  const providerSection = await source("components/task6/ProviderSettingsSection.tsx");
  // 向导中换 Model 只落 modelId；key 草稿不被清空。
  assert.match(providerSection, /<select className="ac-field__control" onChange=\{\(event\) => wizardSetDraft\(\{ modelId: event\.target\.value \}\)\} value=\{wizardDraft\.modelId\}>/);
  assert.doesNotMatch(providerSection, /modelId: event\.target\.value[\s\S]{0,80}apiKey: ""/);
  // 只有类型切换 / 自定义端点与 key 自身的变动才重置检查结论。
  const resetGuard = providerSection.match(/const wizardSetDraft = \(patch: Partial<WizardDraft>\) => \{[\s\S]*?\n  \};/)?.[0] ?? "";
  assert.match(resetGuard, /patch\.typeId !== undefined \|\| patch\.baseUrl !== undefined \|\| patch\.apiKey !== undefined/);
});

test("Settings refreshes native capture status while it is open", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /const pollCaptureStatus = async \(\) =>/);
  assert.match(settings, /window\.setInterval\(\(\) => void pollCaptureStatus\(\), 1_000\)/);
  assert.match(settings, /window\.clearInterval\(timer\)/);
});

test("Settings capture status only turns unavailable after consecutive failed polls", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /CAPTURE_UNAVAILABLE_POLL_LIMIT = 3/);
  assert.match(settings, /next\.availability === "unavailable"/);
  assert.match(settings, /unavailableStreak \+= 1/);
  // 未达阈值直接 return：单次/两次瞬时失败保留上一个已知良好状态。
  assert.match(settings, /if \(unavailableStreak < CAPTURE_UNAVAILABLE_POLL_LIMIT\) return/);
  assert.match(settings, /unavailableStreak = 0/);
});

test("Settings initial load gives up on a slow capture status instead of blocking", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS = 3_000/);
  assert.match(settings, /new Promise<null>\(\(resolve\) => \{\s*window\.setTimeout\(\(\) => resolve\(null\), CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS\);/);
  assert.match(settings, /Promise\.race\(\[getCaptureStatus\(\), captureTimeout\]\)/);
  // getCaptureStatus() 不再裸等：挂起的控制链最多 3 秒让首屏落地 null，之后由 1s 轮询补状态。
  assert.doesNotMatch(settings, /Promise\.all\(\[\s*getCaptureStatus\(\),\s*getStorage\(\)/);
});

test("Coach is the main workspace instead of a closable sidebar", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /<CoachPanel/);
  assert.doesNotMatch(shell, /CoachSidebar/);
  assert.doesNotMatch(shell, /onClose=\{closeCoach\}/);
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
  // 0912 点点拍板：成功反馈是裸绿字（不带 Status 框）。
  assert.match(panel, /feedback\.tone === "success"\s*\? <span className="task6-ok">/);
  assert.doesNotMatch(panel, /feedback\.tone === "success" \? "info"/);
  assert.match(coach, /item\.status === "completed" \? "success"/);
});

test("Settings hosts the KovaaK connection surface without adding a Benchmark route or score-only Coach command", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  const fixtures = await source("fixtures/task7-fixtures.ts");
  assert.match(settings, /KovaaKConnectionPanel/);
  // 0912 点点拍板：S2 Benchmark 成绩单有版权不上屏——连接后只显示已连接，
  // 全界面不出现 S2 字样（改叫 KovaaKs/Steam 在线成绩）；数据留给 Coach 后台读取。
  assert.match(panel, /已连接 KovaaKs 在线成绩/);
  assert.doesNotMatch(panel, /S2|Benchmark|成绩单|score-row|让 Coach 看看/);
  assert.doesNotMatch(settings, /S2|Benchmark/);
  // 同意勾选（0912 点点拍板）：settings 屏退役，仅 onboarding 向导保留；
  // 强制关系只在向导上下文靠 disabled 保持。
  assert.match(panel, /context === "onboarding" && !identityConsent\)/);
  assert.match(panel, /disabled=\{busy \|\| \(context === "onboarding" && !identityConsent\)\}/);
  assert.doesNotMatch(panel, /这不是 Aiming Cookie 账号连接/);
  assert.doesNotMatch(panel, /不会保存或展示|仅读取时本次使用/);
  assert.doesNotMatch(panel, /createCoachAgentRun|training-plan|execution|retest/);
  assert.doesNotMatch(settings, /Benchmark/);
  assert.match(fixtures, /export const KOVAAK_SCORES[\s\S]*availability: "unavailable"/);
  const unavailableScoresFixture = fixtures.slice(
    fixtures.indexOf("export const KOVAAK_SCORES:"),
    fixtures.indexOf("export const KOVAAK_SCORES_AVAILABLE:"),
  );
  assert.doesNotMatch(unavailableScoresFixture, /黄金 III|黄金 I|白银 I/);
});

test("Settings retires the Stats tooltip with its block and removes unreachable mobile nav animation", async () => {
  // narrow 断点的滑窗需要 839px 块与 reduced-motion 标记同串（分跨两文件）。
  const styles = `${await source("components/task6/task6-settings.css")}\n${await source("components/task6/task6.css")}`;
  // 0912 起 Stats 只读块整体退役，tooltip 基类与 reduced-motion 覆盖随块清理。
  assert.doesNotMatch(styles, /task6-info-tooltip/);
  assert.doesNotMatch(styles, /task6-settings-open/);
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
  assert.match(coach, /getCurrentTraining/);
  assert.match(coach, /current_training\.v1/);
  assert.match(coach, /aiming-cookie:coach-kovaak-intent/);
  assert.match(coach, /sessionStorage\.getItem/);
  assert.match(coach, /setDraft/);
  assert.match(coach, /slice\(0, 3\)/);
  assert.match(coach, /disabled={capability !== "ready" \|\| !summaryItem\.display_name}/);
  assert.doesNotMatch(coach, /createTrainingPlan|recordTrainingExecution|recordRetest|completeTraining/);
});

test("Coach refreshes the visible training plan after a completed run", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  assert.match(coach, /const refreshCurrentTraining = useCallback/);
  assert.match(coach, /await Promise\.all\(\[refresh\(\), refreshCurrentTraining\(\)\]\)/);
});

test("Coach never overlays a training-read error on a valid no-plan response", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  assert.match(coach, /currentTrainingError && !currentTraining/);
  assert.match(coach, /currentTraining\?\.reason === "no_current_plan"/);
  assert.match(coach, /currentTraining\.reason !== "no_current_plan"/);
});

test("new Coach sessions are created only when the first message is sent", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(shell, /const handleNewCoachSession = \(\) =>/);
  assert.match(shell, /setDraftSession\(true\)/);
  assert.match(shell, /onEnsureSession=\{ensureCoachSession\}/);
  assert.match(panel, /const effectiveSessionId = sessionId \?\? \(onEnsureSession/);
  assert.match(panel, /createCoachAgentRun\(/);
});

test("Coach shows a sent user message immediately and restores the draft on failure", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /optimisticMessageIdRef/);
  assert.match(panel, /setMessages\(\(current\) => \[\.\.\.current,/);
  assert.match(panel, /role: "user"/);
  assert.match(panel, /message\.id !== optimisticId/);
  // 失败回填仅在等待期间没有重新输入时发生，不覆盖用户新草稿。
  assert.match(panel, /setDraft\(\(current\) => \(current\.trim\(\) \? current : content\)\)/);
});

test("Coach refresh appends not-yet-persisted optimistic bubbles after backend messages", async () => {
  // 乐观消息（id<0）一定比所有已落库消息新：刷新合并若把它拼在 backendMessages
  // 之前，第二条消息会显示在第一条上面（0915 真机实测的时序倒错）。
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /return \[\.\.\.backendMessages, \.\.\.uniqueOptimistic\]/);
  assert.doesNotMatch(panel, /return \[\.\.\.uniqueOptimistic, \.\.\.backendMessages\]/);
});

test("Coach sends and streams Provider runs through the shared API adapter", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /const created = await createCoachAgentRun\([\s\S]*?setRun\(created\)/);
  // SSE stream replaces the fixed-interval poll; getCoachAgentRun remains the
  // shared fetch adapter used for finalizing and as the polling fallback.
  assert.match(panel, /getCoachAgentRun\(runRef/);
  assert.match(panel, /const next = await fetchRun\(\)[\s\S]*?setRun\(next\)/);
});

test("Coach keeps each session's active or failed run when switching conversations", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /runBySessionRef/);
  assert.match(panel, /runBySessionRef\.current\.get\(activeSessionKey\)/);
  assert.match(panel, /runBySessionRef\.current\.set\(activeSessionKeyRef\.current, run\)/);
});

test("Coach shows the empty home only when there are no messages or runs, with no in-chat suggestions", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /const homeMode = messages\.length === 0 && !run && !homeExit;/);
  assert.match(panel, /messages\.map/);
  // 对话中建议条已整体移除（点点 0910 拍板）：开局引导由空对话首页专属
  // chips 承担，对话进行中不再出现 suggestion 渲染块。
  assert.doesNotMatch(panel, /suggestionItems/);
  assert.doesNotMatch(panel, /\{!run && !pendingRunStart && !homeMode && !homeExit \? \(/);
});

test("Coach composer has an explicit accessible name", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /<textarea[\s\S]*?aria-label="向 Coach 提问"/);
});

test("Coach training actions distinguish plan context from a reviewed KovaaK launch", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const desktop = await source("lib/desktop.ts");
  assert.match(coach, /当前训练计划/);
  // 0918 拍板（点点）：浮层标题「当前训练」下再标「当前训练项目」信息重复，
  // 标签退役，项目名 display_name 直接顶到浮层首行。
  assert.doesNotMatch(coach, /当前训练项目/);
  assert.match(coach, /task6-training-item-actions/);
  assert.doesNotMatch(coach, /task6-training-actions/);
  // 0913 拍板：展开时头部行泛称化去重名；按钮行右对齐；删除训练计划直删不弹窗。
  assert.match(coach, /trainingHeaderLabel/);
  assert.match(coach, /删除训练计划/);
  assert.match(coach, /deleteCurrentTraining/);
  assert.match(coach, /在 KovaaK 中开始/);
  assert.match(coach, /display_name/);
  assert.match(coach, /kind === "scenario"/);
  assert.match(coach, /正在理解问题和分析上下文/);
  assert.match(coach, /读取已附加分析/);
  // 1.0.0 内测拍板：不可一键开始时保持安静，「项目暂不可用/尚未绑定」是噪音。
  assert.doesNotMatch(coach, /尚未绑定可启动的 KovaaK 场景/);
  assert.doesNotMatch(coach, /项目暂不可用/);
  assert.match(desktop, /scenario_open/);
  assert.match(desktop, /当前网页预览不能启动 KovaaK/);
  assert.doesNotMatch(coach, /steam:\/\//);
});

test("Coach composer uses a raised input surface without an outer divider", async () => {
  const styles = await source("components/task6/task6.css");
  const header = styles.match(/\.task6-coach-header\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const training = styles.match(/\.task6-current-training\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const panel = styles.match(/\.task6-coach-panel\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const messagesWrap = styles.match(/\.task6-messages-wrap\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const composer = styles.match(/\.task6-composer\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const input = styles.match(/\.task6-composer-input\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  assert.doesNotMatch(header, /border-bottom/);
  assert.doesNotMatch(training, /border-bottom/);
  // 头部三块与底部输入区、面板同一片 surface-container-low：不靠
  // 延伸发丝线切开，让顶部连贯下来（与 composer 一致）。
  assert.doesNotMatch(styles, /\.task6-coach-header::after/);
  assert.doesNotMatch(styles, /\.task6-current-training::after/);
  assert.doesNotMatch(styles, /\.task6-discussion-bar::after/);
  assert.doesNotMatch(composer, /border-top/);
  assert.match(panel, /padding-inline:\s*max\(14px, calc\(\(100% - var\(--task6-coach-content-width\)\) \/ 2\)\)/);
  assert.match(messagesWrap, /width:\s*100%/);
  assert.match(composer, /width:\s*100%/);
  // 0827 面板升为 surface 主面；0828 composer sticky 钉底悬浮——同主面
  // 底色不透明遮挡滚过的消息，输入卡用 surface-bright——两主题下都比主面
  // 亮一档（light 白卡 / dark 浮起卡）。
  assert.match(composer, /background:\s*var\(--surface\)/);
  assert.match(input, /background:\s*var\(--surface-bright\)/);
});

test("Coach current training animates expand and collapse without leaving interactive hidden content", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 0912 逐帧审计改约（推翻 0828 的 absolute 收起法）：closed 态瞬时切
  // position:absolute + inset-inline 会让文字在 200ms 淡出期重排成一字一行
  // 的竖窄条（真机逐帧实锤）。收起改走 grid 行高塌缩——两种状态都在文档
  // 流内、宽度全程不变、inner 裁剪；exitMs 260 > 200ms 过渡，离场卸载不剪
  // 掉塌缩动画的尾巴。
  assert.match(coach, /useAnimatedPresence\(trainingExpanded,\s*260\)/);
  assert.match(coach, /className="task6-training-reveal"/);
  assert.match(coach, /data-state=\{trainingPresence\.state\}/);
  assert.match(coach, /aria-hidden=\{!trainingExpanded \|\| undefined\}/);
  assert.match(coach, /inert=\{!trainingExpanded \|\| undefined\}/);
  assert.match(styles, /\.task6-training-reveal\s*\{[^}]*grid-template-rows:\s*0fr;[^}]*opacity:\s*0;/);
  assert.match(styles, /\.task6-training-reveal\[data-state="open"\]\s*\{[^}]*grid-template-rows:\s*1fr;[^}]*opacity:\s*1;/);
  // closed 态不得再脱离文档流（竖窄条重排的根源），隐藏内容不可点由 inert 保证。
  assert.doesNotMatch(styles, /\.task6-training-reveal\[data-state="closed"\]/);
  assert.match(styles, /\.task6-training-reveal-inner\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/);
  assert.match(styles, /prefers-reduced-motion:\s*reduce[\s\S]*\.task6-training-reveal/);
});

test("Coach training chip morphs as one container instead of growing a card underneath", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  const shellStyles = await source("components/task3/task3.css");
  // 0912 改约：折叠↔展开是同一容器变形长大（对齐 ZCode 小胶囊→面板），
  // chip 与 reveal 同住 .task6-training-pop，data-open 驱动宽度/圆角/底色形变。
  assert.match(coach, /className="task6-training-pop"/);
  assert.match(coach, /data-open=\{trainingExpanded \|\| undefined\}/);
  // 折叠态自然宽写进 --pop-folded-w（max-content 不可插值，必须量出像素）。
  assert.match(coach, /setProperty\("--pop-folded-w"/);
  assert.match(styles, /\.task6-training-pop\s*\{[^}]*width:\s*var\(--pop-folded-w\)/);
  // 0913 拍板：折叠宽上限降到 150px（胶囊悬在正文右上，宽了滚动盖行尾）；
  // 超限渐隐截断（data-truncated + mask 尾端 14px 渐隐），不打省略号。
  assert.match(coach, /maxLabel = Math\.max\(0, 150 - padding - suffix - popBorder\)/);
  assert.match(coach, /pop\.dataset\.truncated/);
  assert.match(styles, /\.task6-training-pop:not\(\[data-open\]\)\[data-truncated="true"\]\s+\.task6-training-chip-label\s*\{[^}]*text-overflow:\s*clip/);
  assert.match(styles, /\.task6-training-pop:not\(\[data-open\]\)\[data-truncated="true"\]\s+\.task6-training-chip-label\s*\{[^}]*mask-image:\s*linear-gradient\(to right,\s*black calc\(100% - 14px\),\s*transparent 100%\)/);
  assert.match(styles, /\.task6-training-pop\s*\{[^}]*transition:[^}]*width var\(--duration-surface\)/);
  assert.match(styles, /\.task6-training-pop\[data-open\]\s*\{[^}]*width:\s*var\(--pop-open-w\)[^}]*border-radius:\s*var\(--radius-lg\)/);
  // 0912 返工：展开态收窄到 300px，且只有背景＋描边（无阴影、无橙色光）。
  assert.match(styles, /--pop-open-w:\s*min\(300px,\s*100%\)/);
  assert.doesNotMatch(styles, /\.task6-training-pop\[data-open\]\s*\{[^}]*box-shadow/);
  // 0912 折叠态改实底：container-high + 描边保留，hover 升 highest。
  assert.match(styles, /\.task6-training-pop\s*\{[^}]*background:\s*var\(--surface-container-high\)/);
  assert.match(styles, /\.task6-training-pop:not\(\[data-open\]\):hover\s*\{[^}]*background:\s*var\(--surface-container-highest\)/);
  // caret 合同：折叠向右、展开向下。
  assert.match(styles, /\.task6-training-chip-caret\s*\{[^}]*transform:\s*rotate\(-90deg\)/);
  assert.match(styles, /\.task6-training-pop\[data-open\]\s+\.task6-training-chip-caret\s*\{[^}]*transform:\s*rotate\(0deg\)/);
  // 空态不再用蓝框 Notice：面板内文案 + 一键让 Coach 排计划（只填不发）。
  assert.doesNotMatch(coach, /<Notice/);
  assert.match(coach, /className="task6-training-pop-empty"/);
  assert.match(coach, /setDraft\("帮我安排一个训练计划"\)/);
  assert.match(coach, /requestAnimationFrame\(\(\) => textareaRef\.current\?\.focus\(\)\)/);
  // 键盘可达才画环（鼠标点击展开不该常驻橙圈）。
  assert.match(styles, /\.task6-training-pop:has\(:focus-visible\)\s*\{[^}]*outline:\s*2px solid var\(--primary\)/);
  assert.doesNotMatch(styles, /\.task6-training-pop:focus-within/);
  // reduced-motion 名单含容器形变与 caret 旋转。
  assert.match(styles, /prefers-reduced-motion[\s\S]*\.task6-training-pop,[\s\S]*\.task6-training-chip-caret\s*\{[^}]*transition:\s*none/);
  // 视频面板打开时训练胶囊整层淡出（opacity+visibility，pointer-events 立即失效），
  // 关闭即回；过渡 150ms = --duration-fast，reduced-motion 下瞬切。
  assert.match(styles, /\.task6-coach-floating\s*\{[^}]*opacity:\s*1;[^}]*transition:[^}]*opacity var\(--duration-fast\)/);
  assert.match(shellStyles, /\.task3-coach-view\[data-video-open="true"\]\s+\.task6-coach-floating\s*\{[^}]*opacity:\s*0;[^}]*visibility:\s*hidden;[^}]*pointer-events:\s*none/);
  assert.match(styles, /prefers-reduced-motion[\s\S]*\.task6-coach-floating,/);
});

test("Coach activity collapse animates height and the thinking block auto-manages open state", async () => {
  const activity = await source("components/task6/CoachRunActivity.tsx");
  const styles = await source("components/task6/task6.css");
  // 折叠容器：grid 行高过渡（0828 调研对齐 MIT 系聊天前端共识），替代瞬时显隐
  assert.match(styles, /\.task6-collapse\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(styles, /\.task6-collapse\[data-state="open"\]\s*\{[^}]*grid-template-rows:\s*1fr/);
  assert.match(styles, /\.task6-collapse-inner\s*\{[^}]*overflow:\s*hidden/);
  assert.match(styles, /\.task6-collapse,\s*\n\s*\.task6-caret\s*\{[^}]*transition:\s*none/);
  // 思考块状态机（0828 再拍板）：任何时刻默认折叠——流式也不自动展开，
  // 展开与否全归用户；无自动开合计时器。
  assert.match(activity, /const \[open, setOpen\] = useState\(false\);/);
  assert.doesNotMatch(activity, /setAutoOpen|everStreamedRef|manualOpen/);
  assert.doesNotMatch(activity, /setTimeout\(\(\) => setAutoOpen\(false\), 1000\)/);
  // chevron 展开指示替代文字「· 收起」
  assert.doesNotMatch(activity, /· 收起/);
  assert.match(activity, /className="task6-caret" data-open=\{open\}/);
});

test("Coach renders time-point links without parsing model prose", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const text = await source("components/task7/CoachMessageText.tsx");
  // 复盘升级 P0.3（brief D7）：@time 解析与时间码格式化收敛到 lib/rich-text，
  // 渲染层只消费 chip 数据（label=显示文案，点击仍跳原始毫秒）。
  const lib = await source("lib/rich-text.ts");
  assert.match(coach, /CoachMessageText/);
  assert.match(coach, /defaultAnalysisRef/);
  assert.match(coach, /analysis_refs/);
  assert.match(text, /parseTimeSegments/);
  assert.match(text, /from "@\/lib\/rich-text"/);
  // 区间模式：可选的「-(数值)」段与只在末尾出现的 s 缀。
  assert.match(lib, /TIME_TOKEN_PATTERN = \/@\(/);
  assert.match(lib, /TIME_TOKEN_PATTERN[^\n]*\?\:-\(/);
  assert.match(text, /onOpenVideo/);
  assert.match(text, /task6-time-link/);
});


test("Coach tool steps collapse done steps, show analysis ETA, and mark stopped runs", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const activity = await source("components/task6/CoachRunActivity.tsx");
  const styles = await source("components/task6/task6.css");
  // 0828 拍板：思考段与工具段按时序交错（CoachWorkStream），连续同名完成步
  // 聚合成一行摘要（点击展开逐行明细），呈现逻辑在 CoachRunActivity，
  // 面板只喂 segments（SSE 实时优先，轮询从 events 重建）。
  assert.match(coach, /<CoachWorkStream segments=\{workSegments\}/);
  assert.match(activity, /task6-done-list/);
  assert.match(activity, /function WorkStepLine/);
  assert.match(activity, /function WorkGroupLine/);
  // ETA 只对分析类命令显示，且样本来自真实执行时长（started_at 优先）。
  assert.match(coach, /ANALYSIS_ETA_COMMANDS = new Set\(\["analysis\.create_from_run", "analysis\.retry"\]\)/);
  assert.match(coach, /computeAnalysisEtaSeconds\(sessionsSnapshot, \{ currentAnalysisType \}\)/);
  assert.match(activity, /task6-tool-eta/);
  // 停止态渲染「回答已停止」收尾行。
  assert.match(coach, /stopped=\{run\.status === "stopped"\}/);
  assert.match(activity, /回答已停止，可重新提问/);
  // 0828 视觉：行首语义图标槽 + 行尾折叠箭头，无点线时间线。
  assert.match(activity, /task6-tool-glyph/);
  assert.match(styles, /\.task6-tool-glyph/);
  assert.match(styles, /\.task6-work-stream/);
  assert.doesNotMatch(styles, /\.task6-tool-dot/);
  assert.doesNotMatch(activity, /task6-tool-dot/);
  // 已删的 composer 状态行不留孤儿规则。
  assert.doesNotMatch(styles, /task6-composer-status/);
});

test("Coach pins the discussion analysis bar above the scrolling conversation and opens the video pane", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 常驻条在滚动区之前渲染（不在对话流里被滚走）
  const discussionAt = coach.indexOf('aria-label="本次讨论的分析"');
  const messagesAt = coach.indexOf('aria-label="Coach 消息"');
  assert.ok(discussionAt !== -1 && messagesAt !== -1, "discussion bar and messages section must exist");
  assert.ok(discussionAt < messagesAt, "discussion bar must render before the scrolling messages section");
  assert.match(coach, /task6-discussion-bar task6-suggestions/);
  // 项目名是按钮：点击打开左侧视频讲解（平铺 chip 与下拉菜单项同一行为）
  assert.match(coach, /onClick=\{\(\) => onOpenVideo\?\.\(`analysis:\$\{chip\.id\}`, 0\)\}/);
  // 吸顶条样式：flex 收缩为 none，不参与对话滚动
  assert.match(styles, /\.task6-discussion-bar\s*\{[\s\S]*flex:\s*none;[\s\S]*\}/);
  // 吸顶条与整面板同底色，不再有 ::after 发丝线切开
  assert.doesNotMatch(styles, /\.task6-discussion-bar::after/);
});

test("Discussion bar pins at most three finished chips and folds the rest behind an overflow menu", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const lib = await source("lib/discussion-bar.ts");
  const styles = await source("components/task6/task6.css");
  // 平铺/溢出分组收敛到 lib 纯函数（单测在 lib/discussion-bar.test.ts），
  // 面板只消费 pinned/overflow 两组；pending chip 独立平铺，不参与折叠。
  assert.match(lib, /export const DISCUSSION_BAR_MAX_PINNED = 3;/);
  // 0918 宽度感知（彩名报障「框体砍半/只露个头」）：分组上限来自挤压反馈
  // 收敛的平铺数（溢出→整颗收进菜单；富余→滞后试探放宽），不再写死 3。
  assert.match(coach, /groupDiscussionChips\(discussionChips, discussionPinnedCount\)/);
  assert.match(coach, /const chips = discussionChipsRef\.current/);
  assert.match(coach, /chips\.scrollWidth - chips\.clientWidth > 1/);
  // 平铺 chip 悬停给完整标题：截断时唯一能认出是哪局的途径。
  assert.match(coach, /title=\{chip\.label\}/);
  assert.match(coach, /pinnedDiscussionChips\.map/);
  assert.match(coach, /overflowDiscussionChips\.length > 0 \? \(/);
  // 箭头按钮：discussion-chip 状态标签档 + aria-expanded + 计数 aria-label。
  assert.match(coach, /className="task6-discussion-chip task6-discussion-toggle"/);
  assert.match(coach, /aria-expanded=\{discussionOverflowOpen\}/);
  assert.match(coach, /aria-label=\{`展开其余 \$\{overflowDiscussionChips\.length\} 个讨论过的分析`\}/);
  // 菜单项点击＝关菜单并打开视频（与平铺 chip 同一行为）。
  assert.match(coach, /setDiscussionOverflowOpen\(false\);\s*onOpenVideo\?\.\(`analysis:\$\{chip\.id\}`, 0\);/);
  // 关闭路径（CoachModelMenu 同款惯例）：外点 mousedown + IME 守卫的 Escape。
  assert.match(coach, /document\.addEventListener\("mousedown", onPointerDown\)/);
  assert.match(coach, /if \(event\.isComposing \|\| event\.keyCode === 229\) return;\s*if \(event\.key === "Escape"\) setDiscussionOverflowOpen\(false\);/);
  // 下拉样式：绝对定位悬浮层挂在吸顶条右缘，宽度有界不溢出面板。
  assert.match(styles, /\.task6-discussion-menu\s*\{[^}]*position:\s*absolute;[^}]*top:\s*calc\(100% \+ var\(--space-1\)\);[^}]*right:\s*var\(--space-4\);[^}]*max-width:\s*320px;[^}]*\}/s);
  // 0918：菜单必须自带层级——对话面板 conversation 是 z-index:1 的绝对
  // 定位层，无层级菜单弹出即被盖住（「点了没反应」）；须高于面板(1)/把手(2)，
  // 低于设置 overlay(20)/窗口三键(60)。
  assert.match(styles, /\.task6-discussion-menu\s*\{[^}]*z-index:\s*3/);
  assert.match(styles, /\.task6-discussion-item/);
});

test("retired coach skeleton classes stay dead in task6.css and task3.css", async () => {
  const styles = await source("components/task6/task6.css");
  const task3Styles = await source("components/task3/task3.css");
  // 0910 清理：旧 Coach 骨架类（sidebar/scrim/resizer/top/body/context、
  // task3 rail/content）已核实 tsx 零引用并删除；不得复活。
  // 注意 .task6-coach-contexts（复数）仍在使用，\b 词边界不得误伤。
  for (const name of [
    "task6-coach-top",
    "task6-coach-body",
    "task6-coach-context",
    "task6-resizer",
    "task6-coach-sidebar",
    "task6-coach-scrim",
  ]) {
    assert.doesNotMatch(styles, new RegExp(`${name}\\b`), name);
  }
  for (const name of ["task3-coach-rail", "task3-coach-content"]) {
    assert.doesNotMatch(task3Styles, new RegExp(`${name}\\b`), name);
  }
});

test("settings keeps 应用更新 reachable as a 通用 subsection (0911 审计 §12.7)", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 分区切换化后应用更新不再是独立分区/导航项：收进置顶「通用」屏三小节
  // 之一，通用永远在导航里，审计要求的「可达」继续成立。
  assert.match(settings, /\{ id: "general", label: "通用" \}/);
  assert.match(settings, /task6-profile-group-title">应用更新/);
});
