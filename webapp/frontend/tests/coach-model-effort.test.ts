import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("profile types carry the optional reasoning_effort knob (five UI levels)", async () => {
  const types = await source("lib/types.ts");
  // 五档语义与 sidecar contracts 一致：xhigh/max 是 Pi 内部档，不外露。
  assert.match(types, /export type ProviderReasoningEffort = "minimal" \| "low" \| "medium" \| "high" \| "off";/);
  assert.match(types, /interface ProviderProfileCreate \{[\s\S]*?reasoning_effort\?: ProviderReasoningEffort \| null;/);
  assert.match(types, /interface ProviderProfile \{[\s\S]*?reasoning_effort\?: ProviderReasoningEffort \| null;/);
});

test("switchProviderModel routes effort adjustments through the model switch endpoint", async () => {
  const api = await source("lib/api.ts");
  assert.match(api, /reasoningEffort\?: ProviderReasoningEffort \| null/);
  // 缺省不携带 → sidecar 沿用已存力度（切模型不动力度）；null → 清回默认。
  assert.match(api, /opts\.reasoningEffort !== undefined \? \{ reasoning_effort: opts\.reasoningEffort \} : \{\}/);
});

test("CoachModelMenu offers a reasoning-gated effort section in the model menu", async () => {
  const menu = await source("components/task6/CoachModelMenu.tsx");
  // 仅当前模型确认支持推理时出现。
  assert.match(menu, /currentModel\?\.reasoning === true/);
  assert.match(menu, /coach\.model\.effortLabel/);
  // 选项齐全：「默认」=未设置（运行时 fallback 高档），「关闭」=显式 off。
  // i18n 批 4：档位 label 是字典键，渲染时经 t() 解析。
  for (const labelKey of [
    "coach.effort.default",
    "coach.effort.off",
    "coach.effort.minimal",
    "coach.effort.low",
    "coach.effort.medium",
    "coach.effort.high",
  ]) {
    assert.match(menu, new RegExp(`label: "${labelKey}"`));
  }
  // 与切模型同路由：挂默认档，对下一段回复生效。
  assert.match(menu, /switchProviderModel\(activeModelId, \{ reasoningEffort: nextEffort \}\)/);
});

test("custom profile model discovery projects catalog reasoning so the effort menu can appear", async () => {
  // 1003 修复回归锁：custom 档模型发现此前不带 reasoning，力度菜单对
  // 自定义 Provider（含推理模型）恒隐藏。投影必须透传 sidecar 元数据。
  const types = await source("lib/types.ts");
  assert.match(types, /interface CustomProviderModel \{[\s\S]*?reasoning\?: boolean;/);
  const menu = await source("components/task6/CoachModelMenu.tsx");
  assert.match(menu, /reasoning: model\.reasoning === true/);
});

test("effort menu renders model-supported levels instead of a hardcoded five", async () => {
  // 1003 点点拍板：写死五档在不支持的模型上说谎（pi clamp 静默收敛，勾选态
  // ≠实际运行档）。菜单按目录 reasoning_efforts 过滤；字段缺失回落全五档。
  const types = await source("lib/types.ts");
  assert.match(types, /interface CustomProviderModel \{[\s\S]*?reasoning_efforts\?: ProviderReasoningEffort\[\];/);
  assert.match(types, /interface ProviderCatalogModel \{[\s\S]*?reasoning_efforts\?: ProviderReasoningEffort\[\];/);
  const menu = await source("components/task6/CoachModelMenu.tsx");
  assert.match(menu, /currentModel\?\.reasoning_efforts/);
  assert.match(menu, /option\.value === "" \|\| !supportedEfforts \|\| supportedEfforts\.includes\(option\.value\)/);
  assert.match(menu, /effortOptions\.map\(\(option\)/);
});

test("Settings provider wizard gates the effort select and includes it in the shared payload", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  const lib = await source("lib/provider-wizard.ts");
  // 内置目录的 reasoning 元数据决定显隐。
  assert.match(section, /wizardModelIsReasoning/);
  assert.match(section, /<Field label=\{t\("settings\.provider\.wizardEffortField"\)\}>/);
  // 干跑与入库共用 payload：一处声明两路生效（lib 纯函数 buildWizardPayload）。
  assert.match(lib, /builtinModelIsReasoning[\s\S]*?draft\.reasoningEffort\s*\?\s*draft\.reasoningEffort/);
});

test("wizard effort select renders model-supported levels with the stored value kept visible", async () => {
  // 1003 点点拍板：向导下拉与 composer 菜单同语言——按目录 reasoning_efforts
  // 过滤；旧 sidecar 无字段回落全量；已存档位不在支持表时保留显示（防 select
  // 无 matching option 假装成「默认」的假一致）。
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  assert.match(section, /const WIZARD_EFFORT_OPTIONS: ReadonlyArray<\{ value: ProviderReasoningEffort; label: MessageKey \}>/);
  assert.match(section, /wizardModelEfforts\s*\?\s*WIZARD_EFFORT_OPTIONS\.filter/);
  assert.match(section, /: WIZARD_EFFORT_OPTIONS;/);
  assert.match(section, /storedExtra/);
});
