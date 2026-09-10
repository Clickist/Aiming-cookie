import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 设置页重设计（点点拍板的设计合同）源码合同：
// 工作区同款左栏 + 锚点/scroll spy、七分区 layout、Provider 主从式与
// 四步向导（末步硬门槛）、最近采集事件块。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Settings shell reuses the workspace rail skeleton with the nav as anchors", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const styles = await source("components/task6/task6.css");
  // 左栏条目即设置导航，条目名保持现状七项，不重命名不重新分组。
  for (const label of ["LLM Provider", "Profile", "主题", "自动采集与 Raw Input", "KovaaK 本地目录", "KovaaK 成绩", "存储"]) {
    assert.match(settings, new RegExp(label));
  }
  // 锚点条目 + scroll spy（滚动容器 = 面板内容列 .task6-settings-content，
  // v6 面板化后从 overlay main 下移）+ 平滑滚动。
  assert.match(settings, /href=\{`#\$\{item\.id\}`\}/);
  assert.match(settings, /scroller\.addEventListener\("scroll", onScroll, \{ passive: true \}\)/);
  assert.match(styles, /@media \(prefers-reduced-motion: no-preference\)\s*\{\s*\.task6-settings-content\s*\{\s*scroll-behavior:\s*smooth;/);
  // 现有返回入口保留；hash 同步与 scroll spy 共存。
  assert.match(settings, /SettingsExit onExit=\{\(\) => router\.push\("\/"\)\}/);
  assert.match(settings, /window\.addEventListener\("hashchange", syncActiveNav\)/);
  // 左栏与工作区会话列表同款（0910 四轮更新）：衬托底色融入工作区、
  // 分隔线取消（内容列圆角子面板自己分层）+ 两档断点宽度。
  assert.doesNotMatch(styles, /\.task6-settings-nav\s*\{[^}]*border-inline-end/);
  assert.match(styles, /\.task6-settings-nav\s*\{[\s\S]*background:\s*var\(--surface-container-low\);/);
  assert.match(styles, /\.task6-settings-content\s*\{[\s\S]*border-radius:\s*var\(--radius-lg\)/);
  assert.match(styles, /@media \(max-width: 1359px\) and \(min-width: 1120px\)\s*\{\s*\.task6-settings-nav\s*\{[^}]*264px/);
});

test("Provider section is a master-detail surface with a single add entry", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  const styles = await source("components/task6/task6.css");
  // 左档案列表：一行=名称+类型小字+「当前使用」徽章；列表底唯一主按钮。
  assert.match(section, /className="task6-provider-list-item"/);
  assert.match(section, /className="task6-provider-list-type"/);
  assert.match(section, /<Badge tone="neutral">当前使用<\/Badge>/);
  assert.match(section, /\+ 添加服务/);
  assert.doesNotMatch(section, /task6-provider-picker/);
  // 详情：内联编辑显示名、设为当前、测连常驻 + 重测。
  assert.match(section, /className="task6-provider-name-edit"/);
  assert.match(section, /setDefaultProviderProfile/);
  assert.match(section, /上次测连成功/);
  assert.match(section, /testProviderProfile\(/);
  // API Key：掩码状态 + 更换/移除分开 + 本机安全辅文。
  assert.match(section, /•••• 已配置/);
  assert.match(section, /setProviderApiKey/);
  assert.match(section, /deleteProviderCredential/);
  assert.match(section, /密钥只存在本机，不会上传/);
  // 底部独立危险区：删除档案；最后一个档案禁止删光，当前使用先切换。
  assert.match(section, /className="task6-provider-danger"/);
  assert.match(section, /deleteProviderProfile/);
  assert.match(section, /disabled=\{lastKeeper \|\| detail\.is_default\}/);
  // 皮肤 token 化：主从式样式走容器/描边 token，不出硬编码色。
  assert.match(styles, /\.task6-provider-master\s*\{[^}]*display:\s*flex/);
  assert.match(styles, /\.task6-provider-list\s*\{[^}]*flex:\s*0 0 240px/);
});

test("Provider add wizard is a four-step modal with the connectivity gate last", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  const wizard = await source("lib/provider-wizard.ts");
  // 四步：选类型 / 名称与端点 / API Key / 测试连接；类型固定四入口。
  assert.match(wizard, /export const WIZARD_TYPES: readonly WizardTypeOption\[\]/);
  assert.equal((wizard.match(/custom: (true|false)/g) ?? []).length, 4);
  assert.match(section, /添加 Provider · 第 \$\{wizardStep\}\/4 步/);
  // 末步硬门槛：未测连通过「完成」不可用；失败就地显示原因留在本步。
  assert.match(section, /disabled=\{!wizardVerified\}/);
  assert.match(section, /disabled=\{!wizardPayload\} onClick=\{\(\) => void runWizardCheck\(\)\}/);
  // API Key 步有显隐切换，且此步尚未写入档案。
  assert.match(section, /wizardShowKey \? "text" : "password"/);
  assert.match(section, /此步尚未写入档案/);
  // 第一个档案自动设为当前；其余完成后询问。
  assert.match(wizard, /isFirstProfile: boolean/);
  assert.match(section, /已自动设为当前使用/);
  assert.match(section, /设为当前并关闭/);
  // 内置类型端点只读由目录决定，自定义类型才有 Base URL Preview。
  assert.match(wizard, /previewCustomRequestUrl/);
  assert.match(wizard, /previewBuiltinRequestUrl/);
});

test("Capture section hosts the health lamp, toggle rows, recent events, and diagnostics at the bottom", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 顶部一行采集健康状态（runtime_health 灯）。
  assert.match(settings, /runtimeHealthTone\(capture\.runtime_health\)/);
  // 开关行清单：功能名+说明在左、开关/状态在右。
  assert.match(settings, /className="task6-toggle-row"/);
  assert.match(settings, /授权并启用自动采集/);
  // 最近采集事件块：复用 lib 人话映射，截取最近若干局。
  assert.match(settings, /describeCaptureRunEvent\(/);
  assert.match(settings, /RECENT_CAPTURE_EVENTS_LIMIT = 8/);
  // 导出采集诊断包挪到分区底部并写明用途。
  assert.match(settings, /className="task6-capture-diagnostics"/);
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
  const styles = await source("components/task6/task6.css");
  // 主题：一行（名称+说明 | 三段切换）+ 下方预览条。
  assert.match(settings, /className="task6-theme-row"/);
  assert.match(settings, /className="task6-theme-segments"/);
  assert.match(settings, /className="task6-theme-preview"/);
  assert.match(styles, /\.task6-theme-segment\[data-selected="true"\]/);
  // Profile：字段按相关性分组两列（手动校准 / Stats 自动读取）。
  assert.match(settings, /手动校准/);
  assert.match(settings, /Stats 自动读取/);
  assert.match(styles, /\.task6-profile-fields\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/);
  // 存储：总占用大数字 + 占比条；清理动作独立放底部。
  assert.match(settings, /className="task6-storage-total"/);
  assert.match(settings, /className="task6-storage-cleanup"/);
  assert.match(styles, /\.task6-storage-total-number\s*\{[^}]*font:\s*600 var\(--text-display\)/);
});
