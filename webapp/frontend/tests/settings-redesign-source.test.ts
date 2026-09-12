import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 设置页重设计（点点拍板的设计合同）源码合同：
// 工作区同款左栏 + 分区切换化骨架（5+1 屏，0911 拍板线框）、
// Provider 主从式与两步向导（测试通过才可完成）、最近采集事件块。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Settings shell reuses the workspace rail skeleton with tab switching", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  // 左栏 5+1 个切换项（0911 拍板）：通用置顶，高级收纳诊断工具在最后。
  for (const label of ["通用", "LLM Provider", "自动采集", "KovaaK", "数据与存储", "高级"]) {
    assert.match(settings, new RegExp(label));
  }
  // 合并屏保留原分区标题层级：通用三小节、KovaaK 两小节（0912 起卡内自包含标题）。
  // 0912 点点拍板：高级屏取消，外部遥测导入界面下线。
  for (const subsection of ["主题", "Profile", "应用更新", "本地目录", "KovaaKs 在线成绩"]) {
    assert.match(settings, new RegExp(subsection));
  }
  // 切换语义：左栏按钮 → state；未选屏 hidden 隐藏（保留 DOM 状态不重挂载）。
  assert.match(settings, /className="task6-settings-nav-link"\s*\n\s*data-current=/);
  assert.match(settings, /type="button"/);
  assert.match(settings, /hidden=\{activeNav !== "general"\}/);
  assert.doesNotMatch(settings, /hidden=\{activeNav !== "advanced"\}/);
  assert.doesNotMatch(settings, /href=\{`#\$\{item\.id\}`\}/);
  // scroll spy 退役：无滚动监听；切换时右屏滚动位置归零。
  assert.doesNotMatch(settings, /scroller\.addEventListener/);
  assert.match(settings, /contentRef\.current\.scrollTop = 0/);
  // 锚点平滑滚动随锚点一起退役。
  assert.doesNotMatch(styles, /scroll-behavior:\s*smooth/);
  // 现有返回入口保留；hash 深链保留（旧锚点 id 别名映射到新屏）。
  assert.match(settings, /SettingsExit onExit=\{\(\) => router\.push\("\/"\)\}/);
  assert.match(settings, /window\.addEventListener\("hashchange", syncActiveNav\)/);
  // 左栏与工作区会话列表同款（0910 四轮更新）：衬托底色融入工作区、
  // 分隔线取消（右屏满铺主面板自己分层）+ 两档断点宽度。
  assert.doesNotMatch(styles, /\.task6-settings-nav\s*\{[^}]*border-inline-end/);
  assert.match(styles, /\.task6-settings-nav\s*\{[\s\S]*background:\s*var\(--surface-container-low\);/);
  // 0912 拍板：圆角浅色面板从内容列升格到主列本身（满铺 + 承托底 4px 缝）。
  assert.match(styles, /\.task6-settings-main\s*\{[^}]*border-radius:\s*var\(--radius-lg\)/);
  assert.match(styles, /\.task6-settings-main\s*\{[^}]*background:\s*var\(--surface-container\);/);
  assert.match(styles, /@media \(max-width: 1359px\) and \(min-width: 1120px\)\s*\{\s*\.task6-settings-nav\s*\{[^}]*264px/);
});

test("Provider section is a master-detail surface with a single add entry", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  const helpers = await source("lib/provider-helpers.ts");
  const styles = await source("components/task6/task6-settings.css");
  // 0912 线框拍板：列表与详情是两张自包含卡；列表行=名称+类型小字+状态点，
  // 「当前使用」徽章退役；列表底唯一主按钮。
  assert.match(section, /className="task6-provider-list-item"/);
  assert.match(section, /className="task6-provider-list-type"/);
  assert.doesNotMatch(section, /当前使用<\/Badge>/);
  assert.match(section, /task6-provider-list-card/);
  assert.match(section, /task6-provider-detail-card/);
  assert.match(section, /\+ 添加服务/);
  assert.doesNotMatch(section, /task6-provider-picker/);
  // 行尾状态点（线框）：绿点＝连接正常，红点＝探测不通（数据来自现有测活状态）。
  assert.match(section, /className="task6-provider-dot" data-ready=\{profile\.status === "ready"\}/);
  assert.match(styles, /\.task6-provider-dot\s*\{[^}]*background:\s*var\(--error\)/);
  // 详情标题行（线框 A）：内联改名 + 铅笔图标 + 类型 chip + 角落 ghost「设为当前」
  // + 行尾垃圾桶；「删除此档案」卡片区块退役，删除走既有确认弹窗与保留门槛。
  assert.match(section, /className="task6-provider-name-edit"/);
  assert.match(section, /aria-label="删除此档案"/);
  assert.match(section, /disabled=\{lastKeeper \|\| detail\.is_default\}/);
  assert.match(section, /deleteProviderProfile/);
  assert.match(section, /variant="ghost">设为当前<\/Button>/);
  assert.doesNotMatch(section, /task6-provider-danger/);
  // 详情：内联编辑显示名、设为当前、测活合并行（上次测活 · 行尾重测按钮）。
  assert.match(section, /setDefaultProviderProfile/);
  assert.match(section, /上次测活成功/);
  assert.match(section, /testProviderProfile\(/);
  // API Key（0912 线框）：静息=紧凑圆点胶囊+眼睛；编辑态回车/失焦即存，
  // 无 ✓/× 按钮、无确认弹窗；移除 Key 入口一并退役（点点 0912 追加拍板）。
  assert.match(section, /••••••••/);
  assert.match(section, /setProviderApiKey/);
  assert.doesNotMatch(section, /deleteProviderCredential/);
  assert.doesNotMatch(section, /密钥只存在本机/);
  // Base URL（0912 拍板）：失焦/回车即存，行内保存钮退役。
  assert.match(section, /onBlur=\{\(\) => void saveBaseUrl\(detail\)\}/);
  // 详情模型列表（0912 线框补齐）：内置=目录只读行+⟳刷新；自定义按
  // profile_id 就地发现，失败显示线框红字；发现结果存档一份，
  // 详情页免点获取模型直接显示（点点 0912 拍板）。
  assert.match(section, /listStoredCustomProviderModels/);
  assert.match(section, /连接失败，请检查设置/);
  assert.match(section, /detail\.discovered_models\?\.length/);
  // 官方档（线框 B）：按 provider_id=aiming-cookie-relay 识别，详情无
  // Base URL/API Key 常规行，改走计费二选 + 会员计划空态 + API 计费真余额。
  assert.match(helpers, /"aiming-cookie-relay"/);
  assert.match(section, /isOfficialRelayProfile/);
  assert.match(section, /会员计划/);
  assert.match(section, /API 计费/);
  assert.match(section, /会员系统上线后开放/);
  assert.match(section, /刷新余额/);
  assert.match(section, /getOfficialRelayBalance/);
  // 皮肤 token 化：主从式样式走容器/描边 token，不出硬编码色。
  assert.match(styles, /\.task6-provider-master\s*\{[^}]*display:\s*flex/);
  assert.match(styles, /\.task6-provider-list-card\s*\{[^}]*flex:\s*0 0 190px/);
});

test("Provider add wizard is a two-step modal with test-then-finish on the second step", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  const wizard = await source("lib/provider-wizard.ts");
  const styles = await source("components/task6/task6-settings.css");
  // 两步（点点 0911 拍板）：①选类型 ②名称与凭据（含测试/完成）。
  assert.match(wizard, /export const WIZARD_STEP_COUNT = 2;/);
  assert.match(wizard, /export const WIZARD_TYPES: readonly WizardTypeOption\[\]/);
  // 类型目录（点点 0911 线框拍板）：catalog 派生完整厂商列表可滚动；
  // 官方中转不进列表，「自定义 OpenAI 兼容」兜底；WIZARD_TYPES 降为
  // 目录缺失时的回落。
  assert.match(wizard, /export function wizardTypeOptions\(/);
  assert.match(wizard, /OFFICIAL_RELAY_PROVIDER_ID/);
  assert.doesNotMatch(wizard, /hint: "/);
  assert.match(section, /wizardTypeOptions\(wizardCatalogSource\)/);
  assert.match(styles, /\.task6-wizard-type-grid\s*\{[^}]*overflow-y:\s*auto/);
  assert.match(section, /第 \$\{wizardStep\}\/\$?\{?WIZARD_STEP_COUNT\}? 步/);
  // 测试按钮语义：失败红字「连接失败」留本步；成功绿字「✓ 连接成功」且按钮变「完成」。
  assert.match(section, /\{wizardVerified \? "完成" : "测试"\}/);
  assert.match(section, /<Notice tone="error">连接失败<\/Notice>/);
  assert.match(section, /✓ 连接成功<\/p>/);
  // 免模型连通探测：内置不选模型也能先测连；模型列表在测试成功后才展示/获取。
  assert.match(wizard, /export function buildWizardProbePayload/);
  assert.match(section, /wizardProbePayload/);
  assert.match(section, /测试通过后在此获取并选择模型。/);
  assert.match(section, /获取模型/);
  // 显示名称重名软提示：只提示不阻断。
  assert.match(section, /wizardNameConflicts\(/);
  assert.match(section, /已有同名档案，建议换一个名字以便区分。/);
  // 指纹失效机制保留：表单（端点/Key）变动即重锁完成按钮。
  assert.match(wizard, /export function wizardCheckFingerprint/);
  assert.match(section, /wizardCheck\.fingerprint === wizardFingerprint/);
  // API Key 步有显隐切换，且此步尚未写入档案。
  assert.match(section, /wizardShowKey \? "text" : "password"/);
  assert.match(section, /此步尚未写入档案/);
  // 第一个档案自动设为当前（payload 层语义保留）。
  assert.match(wizard, /isFirstProfile: boolean/);
  // 内置类型端点只读由目录决定，自定义类型才有 Base URL Preview。
  assert.match(wizard, /previewCustomRequestUrl/);
  assert.match(wizard, /previewBuiltinRequestUrl/);
});

test("Capture section hosts the aggregate status dot and the diagnostics export row", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 0912 点点拍板：标题旁一颗状态点——任一采集源（采集服务/外部遥测）异常整点转橙，
  // 橙点悬浮「采集服务不正常」；具体采集细节不上屏。
  assert.match(settings, /className="task6-capture-status-dot"/);
  assert.match(settings, /captureIssue \? "采集服务不正常"/);
  assert.match(settings, /const captureIssue = \(capture \? capture\.runtime_health !== "healthy" : false\) \|\| externalBroken;/);
  assert.match(settings, /externalTelemetry\?\.activation === "runtime_unavailable"/);
  // 开关行合并 KovaaK 在线状态；细节行（Raw Input 授权/平台/暂停局）与回放缓冲卡退役。
  assert.match(settings, /className="task6-toggle-row"/);
  assert.match(settings, /" · "\}\{captureLabel\(capture\.kovaak_process_present/);
  assert.doesNotMatch(settings, /Raw Input 授权|暂停局处理|回放缓冲/);
  assert.match(settings, /授权并启用自动采集/);
  // 最近采集事件块：复用 lib 人话映射，截取最近若干局。
  assert.match(settings, /describeCaptureRunEvent\(/);
  assert.match(settings, /RECENT_CAPTURE_EVENTS_LIMIT = 8/);
  // 0912：高级屏取消——诊断包挪回自动采集屏，外部遥测导入界面下线。
  assert.doesNotMatch(settings, /id="advanced"/);
  assert.doesNotMatch(settings, /ExternalTelemetryPanel/);
  const captureAt = settings.indexOf('id="capture"');
  const kovaakAt = settings.indexOf('id="kovaak"');
  const diagnosticsAt = settings.indexOf('className="task6-capture-diagnostics"');
  assert.ok(captureAt !== -1 && captureAt < diagnosticsAt && diagnosticsAt < kovaakAt);
  assert.match(settings, /给开发者排障用的/);
});

test("Recent capture events translate backend error codes with a pure lib mapping", async () => {
  const lib = await source("lib/capture-events.ts");
  // 典型拍板映射：未在录制 / 覆盖缺口；未知码透传原始码。
  assert.match(lib, /video_capture_unavailable: "打这局时应用未在录制"/);
  assert.match(lib, /video_window_invalid: "打这局时应用未在录制"/);
  assert.match(lib, /trace_raw_window_coverage_gap: "这一时间窗没有输入数据"/);
  assert.match(lib, /return VIDEO_ERROR_LABELS\[code\] \?\? code;/);
  assert.match(lib, /return TRACE_ERROR_LABELS\[code\] \?\? code;/);
});

test("Theme, Profile, and Storage sections follow the agreed one-line or grouped layouts", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  // 主题：三张横排预览卡（上半配色预览块、下方名称），选中卡描边 accent（--primary）。
  assert.match(settings, /className="task6-theme-cards"/);
  assert.match(settings, /className="task6-theme-card-swatch"/);
  assert.match(styles, /\.task6-theme-card\[data-selected="true"\]\s*\{[^}]*border-color:\s*var\(--primary\)/);
  // Profile：自包含卡（标题+小字进卡内，两行）；输入框灰字占位 = Stats 读取值。
  assert.match(settings, /Profile 默认值/);
  assert.match(settings, /className="task6-card-desc"/);
  assert.match(settings, /placeholder=\{statsCm360Placeholder\}/);
  assert.match(settings, /placeholder=\{statsFovPlaceholder\}/);
  assert.match(styles, /\.task6-profile-fields\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  // 应用更新：第一行 = 「应用更新」+ 状态 chip；第二行 = 当前版本（左）+ 行尾「检查更新」。
  assert.match(settings, /className="task6-app-update-head"/);
  assert.match(settings, /className="task6-app-update-row"/);
  assert.match(settings, /className="task6-update-chip"/);
  assert.match(settings, /已是最新/);
  assert.match(settings, /有新版本/);
  // 存储：总占用大数字 + 占比条；清理动作独立放底部。
  assert.match(settings, /className="task6-storage-total"/);
  assert.match(settings, /className="task6-storage-cleanup"/);
  assert.match(styles, /\.task6-storage-total-number\s*\{[^}]*font:\s*600 var\(--text-display\)/);
});

test("Storage cleanup rows downgrade destructive actions and surface real file facts", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  const api = await source("lib/api.ts");
  // 幽灵降级：移除动作统一 ghost + 悬停转 var(--error)，不再大红实心。
  assert.match(settings, /className="task6-btn-danger-ghost"/);
  assert.doesNotMatch(settings, /variant="danger">移除/);
  assert.match(
    styles,
    /\.ac-button\.task6-btn-danger-ghost\[data-variant="ghost"\]:hover[^{]*\{[^}]*var\(--error\)/,
  );
  // 两个删除动作合并为单「移除…」+ 确认弹窗；删除 API 不变（按 kinds 逐个调用）。
  assert.match(settings, /移除…\s*<\/Button>/);
  assert.match(settings, /for \(const kind of kinds\) await removeRunEvidence\(run\.id, kind\)/);
  // 行内真实数据：size（可得才渲染）+ 对局日期；按大小降序。
  // 0912 点点拍板：文件名副行退役，打开文件位置保留。
  assert.match(settings, /formatBytes\(sizeBytes\)/);
  assert.match(settings, /formatDay\(run\.training_at \?\? run\.created_at\)/);
  assert.doesNotMatch(settings, /task6-storage-row-files/);
  assert.match(settings, /\.sort\(\(a, b\) => \(b\.sizeBytes \?\? -1\) - \(a\.sizeBytes \?\? -1\)\)/);
  // 打开文件位置：前端只发条目 id/kind；本地路径只在后端解析（path-free）。
  assert.match(settings, /打开文件位置/);
  assert.match(api, /kind: "run_video" \| "run_raw" \| "incomplete_capture"/);
  assert.doesNotMatch(api, /storage\/reveal[^\n]*path/);
});

test("Settings forms converge on the left-label right-value row language", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  const directories = await source("components/kovaak/KovaaKDirectoriesPanel.tsx");
  // Profile 标签移到左列（label 绑定输入框）；Stats 只读值走同一行语言。
  assert.match(settings, /className="task6-form-row"/);
  assert.match(settings, /htmlFor="task6-profile-cm360"/);
  assert.doesNotMatch(settings, /<Field label="cm\/360">/);
  assert.match(styles, /\.task6-form-row-label\s*\{[^}]*width:\s*96px/);
  assert.match(styles, /\.task6-form-row-label\s*\{[^}]*color:\s*var\(--on-surface-variant\)/);
  // KovaaK 本地目录：只读状态是纯文本行，不再用徽标/输入壳。
  assert.match(directories, /kovaak-directory-entry-info/);
  assert.doesNotMatch(directories, /tone=\{directory\?\.path/);
});

test("Recent capture events render plain rows with a semantic dot and hover details", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6-settings.css");
  const lib = await source("lib/capture-events.ts");
  // 状态词上屏、括号里的人话归因收进悬停 title（纯函数拆分，未知不硬造）。
  assert.match(lib, /export function splitStatusLabel/);
  assert.match(lib, /export function summarizeCaptureRunStatus/);
  assert.match(settings, /summarizeCaptureRunStatus\(described\.videoLabel, described\.traceLabel\)/);
  // 纯行样式：语义色圆点 + 场景名 + 行尾精简状态词；无边框壳。
  assert.match(settings, /className="task6-capture-event-dot"/);
  assert.match(settings, /className="task6-capture-event-status"\>\{status\.word\}/);
  assert.match(settings, /title=\{`视频 \$\{described\.videoLabel\} · 轨迹 \$\{described\.traceLabel\}`\}/);
  assert.doesNotMatch(settings, /task6-capture-event-detail/);
  assert.doesNotMatch(styles, /\.task6-capture-event\s*\{[^}]*border:\s*1px/);
  // 语义色：绿=已就绪（event-kill），橙=整理中/未录制（event-peak），灰=缺失。
  assert.match(styles, /\.task6-capture-event-dot\s*\{[^}]*background:\s*var\(--event-kill\)/);
  assert.match(styles, /\.task6-capture-event\[data-tone="working"\] \.task6-capture-event-dot\s*\{[^}]*background:\s*var\(--event-peak\)/);
  assert.match(styles, /\.task6-capture-event\[data-tone="missing"\] \.task6-capture-event-dot\s*\{[^}]*background:\s*var\(--on-surface-variant\)/);
});
