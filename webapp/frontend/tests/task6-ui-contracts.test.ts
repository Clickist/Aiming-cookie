import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// task6 Coach / Settings 的用户可见、边界与禁令合同。
//
// 前身是 tests/task6-source.test.ts（43 用例整文件锁源码字面形态），按
// 2026-09-28「死板断言」审查报告 A-1 分流退役：
// - 纯「代码长某样」断言（JSX 属性排列、变量名、注释锚、indexOf 顺序）删除；
// - 用户可见合同、边界合同与「真拦截过回归」的守卫降级为存在性/值级弱断言；
// - 行为合同（时序竞速、兜底优先级、形变收敛）需要抽 lib 纯函数或渲染测试，
//   本批不做，见报告 §四 待办。
//
// 约定：这里只允许「值级 / 存在性 / 禁令」三种形态，不再锁代码的具体写法。

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach 与 Settings 表面不出现裸色值或内联 color", async () => {
  const styles = await source("components/task6/task6.css");
  assert.doesNotMatch(styles, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
  const combined = `${await source("components/task6/CoachPanel.tsx")}\n${await source("components/task6/SettingsWorkspace.tsx")}`;
  assert.doesNotMatch(combined, /style=\{\{[^}]*color|#[0-9a-fA-F]{3,8}\b/);
});

test("Coach composer 的可访问名来自字典键", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 无障碍合同：输入区必须有显式可访问名，且文案走字典（不得裸写）。
  assert.match(panel, /aria-label=\{t\("coach\.composer\.ariaLabel"\)\}/);
});

test("状态点与语义色跟随状态，而不是固定色", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  const shellStyles = await source("components/task3/task3.css");
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  const coach = await source("components/task6/CoachPanel.tsx");
  // 共用顶栏状态点由 capability 驱动 data-state，颜色按状态变体给。
  assert.match(shell, /task3-coach-status-dot[\s\S]{0,80}data-state=\{capability\}/);
  assert.match(shellStyles, /\.task3-coach-status-dot\[data-state="ready"\]/);
  // 0912 拍板：成功反馈是裸绿字（task6-ok），不套 Status 框；Coach 完成步走 success tone。
  assert.match(panel, /task6-ok/);
  assert.match(coach, /"completed"[\s\S]{0,40}"success"/);
});

test("KovaaK 连接面保持只读、不上屏第三方成绩单、同意门只在向导上下文", async () => {
  const panel = await source("components/kovaak/KovaaKConnectionPanel.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 0912 拍板（点点）：S2 成绩单有版权不上屏（改叫 KovaaKs/Steam 在线成绩）。
  assert.doesNotMatch(panel, /S2|Benchmark|成绩单|score-row|让 Coach 看看/);
  assert.doesNotMatch(settings, /S2|Benchmark/);
  // 数据留给 Coach 后台读取：连接面不得创建 run / 训练计划 / 执行 / 复测。
  assert.doesNotMatch(panel, /createCoachAgentRun|training-plan|execution|retest/);
  // 同意勾选：settings 屏退役后，强制关系只在 onboarding 上下文靠 disabled 保持。
  assert.match(panel, /context === "onboarding"[\s\S]{0,40}!identityConsent/);
});

test("Coach 运行控制走共享 API 适配器，不携带原始字段也不写训练记录", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  assert.match(coach, /createCoachAgentRun/);
  assert.match(coach, /getCoachAgentRun/);
  assert.match(coach, /stopCoachAgentRun/);
  assert.match(coach, /retryCoachAgentRun/);
  // 安全边界：原始 trace / 凭据字段不得进入渲染层。
  assert.doesNotMatch(coach, /video_path|raw_trace|protobuf|api_key|access_token|refresh_token/);
  // Coach 只读当前训练：训练写操作不从这里发起。
  assert.doesNotMatch(coach, /createTrainingPlan|recordTrainingExecution|recordRetest|completeTraining/);
  // 时间点链接：解析与格式化在 lib/rich-text，渲染层只消费。
  assert.match(coach, /CoachMessageText/);
  assert.match(coach, /defaultAnalysisRef/);
  const text = await source("components/task7/CoachMessageText.tsx");
  assert.match(text, /parseTimeSegments/);
  assert.match(text, /from "@\/lib\/rich-text"/);
  assert.match(text, /task6-time-link/);
});

test("刷新合并把未落库的乐观消息排在已落库消息之后（0915 真机回归守卫）", async () => {
  // 乐观消息（id<0）一定比所有已落库消息新：拼在前面会让第二条消息显示在
  // 第一条上面（0915 真机实测的时序倒错）。本批保留该弱守卫，待抽 lib 纯函数
  // 或渲染测试覆盖同一合同后再退役。
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /return \[\.\.\.backendMessages, \.\.\.uniqueOptimistic\]/);
  assert.doesNotMatch(panel, /return \[\.\.\.uniqueOptimistic, \.\.\.backendMessages\]/);
});

test("空对话首页只在没有消息与运行时出现，会话中不再有建议条", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /const homeMode = messages\.length === 0 && !run/);
  // 0910 拍板：对话中建议条整体移除，开局引导由空对话首页 chips 承担。
  assert.doesNotMatch(panel, /suggestionItems/);
});

test("训练浮层收起时留在文档流内（0912 逐帧实锤：absolute 收起会重排成竖窄条）", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 收起走 grid 行高塌缩，两种状态都在文档流内；closed 态不得再出现覆盖规则。
  assert.match(styles, /\.task6-training-reveal\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(styles, /\.task6-training-reveal\[data-state="open"\]\s*\{[^}]*grid-template-rows:\s*1fr/);
  assert.doesNotMatch(styles, /\.task6-training-reveal\[data-state="closed"\]/);
  // 隐藏内容对 AT 与指针都要不可达（inert + aria-hidden）。
  assert.match(coach, /aria-hidden=\{!trainingExpanded/);
  assert.match(coach, /inert=\{!trainingExpanded/);
  assert.match(styles, /prefers-reduced-motion[\s\S]*\.task6-training-reveal/);
});

test("训练胶囊折叠态超限渐隐截断，展开态无阴影且 focus 橙圈退役", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 折叠宽由测量值写入 --pop-folded-w（max-content 不可插值），超限走渐隐截断。
  assert.match(coach, /setProperty\("--pop-folded-w"/);
  assert.match(coach, /pop\.dataset\.truncated/);
  assert.match(styles, /\[data-truncated="true"\][^{]*\{[^}]*text-overflow:\s*clip/);
  assert.match(styles, /\[data-truncated="true"\][^{]*\{[^}]*mask-image:\s*linear-gradient\(to right/);
  // 展开态只有背景＋描边（无阴影、无橙色光）；0927 拍板：focus 橙圈全部去掉。
  assert.match(styles, /--pop-open-w:\s*min\(300px,\s*100%\)/);
  assert.doesNotMatch(styles, /\.task6-training-pop\[data-open\]\s*\{[^}]*box-shadow/);
  assert.doesNotMatch(styles, /\.task6-training-pop:focus-within/);
  // 视频面板打开时训练胶囊整层淡出（opacity+visibility，pointer-events 立即失效）。
  assert.match(styles, /\.task6-coach-floating\s*\{[^}]*opacity:\s*1;/);
  const shellStyles = await source("components/task3/task3.css");
  assert.match(shellStyles, /\.task3-coach-view\[data-video-open="true"\]\s+\.task6-coach-floating\s*\{[^}]*opacity:\s*0;[^}]*visibility:\s*hidden;[^}]*pointer-events:\s*none/);
});

test("讨论条常驻在滚动区之前，溢出菜单自带层级与无障碍状态（0918「点了没反应」守卫）", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const styles = await source("components/task6/task6.css");
  // 平铺/溢出分组收敛到 lib/discussion-bar（行为由 lib/discussion-bar.test.ts 锁）；
  // 面板只负责把常驻条渲染在滚动消息区之前，不被滚走。
  const barAt = coach.indexOf('className="task6-discussion-bar');
  const messagesAt = coach.indexOf('aria-label={t("coach.messages.label")}');
  assert.ok(barAt !== -1 && messagesAt !== -1, "讨论条与消息区都必须存在");
  assert.ok(barAt < messagesAt, "讨论条必须先于滚动消息区渲染");
  assert.match(coach, /groupDiscussionChips\(/);
  assert.match(coach, /aria-expanded=\{discussionOverflowOpen\}/);
  // 菜单必须自带层级：conversation 是 z-index:1 的绝对定位层，无层级会被盖住。
  assert.match(styles, /\.task6-discussion-menu\s*\{[^}]*z-index:\s*3/);
  // 关闭路径（CoachModelMenu 同款惯例）：外点 mousedown + 带 IME 守卫的 Escape
  // 收起溢出菜单（composition 期间的 Escape 不得吞掉输入法候选操作）。
  assert.match(coach, /document\.addEventListener\("mousedown", onPointerDown\)/);
  assert.match(coach, /isComposing \|\| event\.keyCode === 229[\s\S]{0,60}Escape[\s\S]{0,40}setDiscussionOverflowOpen\(false\)/);
});

test("工具步无点线时间线、停止态渲染收尾行、ETA 只对分析类命令显示", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const activity = await source("components/task6/CoachRunActivity.tsx");
  const styles = await source("components/task6/task6.css");
  assert.match(coach, /stopped=\{run\.status === "stopped"\}/);
  assert.match(activity, /coach\.activity\.stoppedRow/);
  assert.match(coach, /ANALYSIS_ETA_COMMANDS/);
  assert.match(coach, /computeAnalysisEtaSeconds\(/);
  assert.match(activity, /task6-tool-glyph/);
  // 0828 视觉：点线时间线退役；已删的 composer 状态行不留孤儿规则。
  assert.doesNotMatch(styles, /\.task6-tool-dot/);
  assert.doesNotMatch(activity, /task6-tool-dot/);
  assert.doesNotMatch(styles, /task6-composer-status/);
});

test("思考块默认折叠且展开权全归用户，折叠容器走 grid 行高过渡", async () => {
  const activity = await source("components/task6/CoachRunActivity.tsx");
  const styles = await source("components/task6/task6.css");
  // 0828 再拍板：任何时刻默认折叠，流式也不自动展开，无自动开合计时器。
  assert.match(activity, /const \[open, setOpen\] = useState\(false\)/);
  assert.doesNotMatch(activity, /setAutoOpen|everStreamedRef|manualOpen/);
  // chevron 展开指示替代文字「· 收起」。
  assert.doesNotMatch(activity, /· 收起/);
  assert.match(styles, /\.task6-collapse\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(styles, /\.task6-collapse\[data-state="open"\]\s*\{[^}]*grid-template-rows:\s*1fr/);
  assert.match(styles, /\.task6-collapse-inner\s*\{[^}]*overflow:\s*hidden/);
  assert.match(styles, /prefers-reduced-motion[\s\S]*\.task6-collapse/);
});

test("面板头部/训练块/讨论条不靠 ::after 发丝线切开（0827/0912 拍板）", async () => {
  const styles = await source("components/task6/task6.css");
  // 头部三块与底部输入区、面板同一片 surface-container-low：不靠延伸发丝线
  // 切开，让顶部连贯下来（与 composer 一致）。
  assert.doesNotMatch(styles, /\.task6-coach-header::after/);
  assert.doesNotMatch(styles, /\.task6-current-training::after/);
  assert.doesNotMatch(styles, /\.task6-discussion-bar::after/);
});

test("capture 状态：首载 3 秒竞速放弃、连续 3 次失败才置不可用（阈值合同）", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS\s*=\s*3_000/);
  assert.match(settings, /CAPTURE_UNAVAILABLE_POLL_LIMIT\s*=\s*3/);
  for (const constant of ["CAPTURE_STATUS_FIRST_LOAD_TIMEOUT_MS", "CAPTURE_UNAVAILABLE_POLL_LIMIT"]) {
    assert.ok(settings.split(constant).length - 1 >= 2, `${constant} 必须既有定义也有消费`);
  }
  // 首载不裸等：超时与状态读取竞速，超时最多让首屏落地 null，之后由轮询补状态。
  assert.match(settings, /Promise\.race\(/);
  // 未达阈值保留上一个已知良好状态（单次瞬时失败不算不可用）。
  assert.match(settings, /unavailableStreak < CAPTURE_UNAVAILABLE_POLL_LIMIT/);
});

test("设置页 hash 深链继续认历史锚点（旧链接可达）", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /window\.location\.hash/);
  assert.match(settings, /hashchange/);
  // 历史页的 /settings#kovaak-directories 等旧锚点链接必须继续可落到对应屏。
  for (const legacyAnchor of ["kovaak-directories", "external-telemetry", "app-update"]) {
    assert.match(settings, new RegExp(`"${legacyAnchor}":`), legacyAnchor);
  }
});

test("设置页窄断点隐藏左导航", async () => {
  const styles = await source("components/task6/task6-settings.css");
  // 839px 块之后的窄断点区：左导航隐藏（标题行在宽屏样式里正常存在，
  // 只禁止窄断点再补一条已退役的 nav-title 规则）。
  const narrow = styles.slice(styles.indexOf("@media (max-width: 839px)"));
  assert.match(narrow, /\.task6-settings-nav\s*\{\s*display:\s*none;/);
  assert.doesNotMatch(narrow, /\.task6-settings-nav-title/);
});

test("已退役的 Coach/Settings 结构、类名与措辞不得复活", async () => {
  const coach = await source("components/task6/CoachPanel.tsx");
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const shell = await source("components/task3/AppShell.tsx");
  const styles = `${await source("components/task6/task6.css")}\n${await source("components/task6/task6-settings.css")}`;
  const shellStyles = await source("components/task3/task3.css");
  // 结构：Coach 是主工作区不是可关侧栏；layoutMode 死 prop 不复活。
  assert.doesNotMatch(shell, /CoachSidebar|layoutMode/);
  // 旧 availability 行、会话内建议条、旧训练动作条已退役（0910/0913 拍板）。
  assert.doesNotMatch(coach, /task6-coach-availability|<span className="task6-coach-state" data-state=|suggestionItems|task6-training-actions/);
  // 退役措辞与噪音文案。
  assert.doesNotMatch(coach, /当前训练项目|尚未绑定可启动的 KovaaK 场景|项目暂不可用|· 收起|steam:\/\//);
  assert.doesNotMatch(settings, /profile_default|偏好只保存在本机|Stats 自动读取优先|一键清空/);
  // WP-C：设置页仍不引入 Clerk 式 Account 挂件（会员档走自家账号会话与用户中心）。
  assert.doesNotMatch(settings, /<Account|from "@clerk\//);
  for (const name of [
    "task6-coach-top",
    "task6-coach-body",
    "task6-coach-context",
    "task6-resizer",
    "task6-coach-sidebar",
    "task6-coach-scrim",
    "task6-info-tooltip",
    "task6-settings-open",
    "task6-tool-dot",
    "task6-composer-status",
    "task6-training-actions",
  ]) {
    assert.doesNotMatch(styles, new RegExp(`${name}\\b`), name);
  }
  for (const name of ["task3-coach-rail", "task3-coach-content"]) {
    assert.doesNotMatch(shellStyles, new RegExp(`${name}\\b`), name);
  }
});
