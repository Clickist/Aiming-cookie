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
  assert.match(menu, /思考力度/);
  // 选项齐全：「默认」=未设置（运行时 fallback 高档），「关闭」=显式 off。
  for (const label of ["默认", "关闭", "极简", "低", "中", "高"]) {
    assert.match(menu, new RegExp(`label: "${label}"`));
  }
  // 与切模型同路由：挂默认档，对下一段回复生效。
  assert.match(menu, /switchProviderModel\(activeModelId, \{ reasoningEffort: nextEffort \}\)/);
});

test("SettingsWorkspace gates the effort select and includes it in the shared draft payload", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 内置目录的 reasoning 元数据决定显隐。
  assert.match(settings, /selectedModelIsReasoning/);
  assert.match(settings, /<Field label="思考力度">/);
  // 干跑与入库共用 payload：一处声明两路生效。
  assert.match(settings, /reasoning_effort: selectedModelIsReasoning && newReasoningEffort \? newReasoningEffort : null,/);
});
