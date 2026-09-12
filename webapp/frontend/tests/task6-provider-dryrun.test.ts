import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("lib/api exposes a dry-run provider test against the dedicated sidecar route", async () => {
  const api = await source("lib/api.ts");
  assert.match(api, /export async function testProviderProfileDraft\(/);
  // 干跑复用既有测试端点族的 collection 级形态，不发明第二套平行接口。
  assert.match(api, /\/v1\/provider-profiles\/test"/);
  assert.match(api, /body: JSON\.stringify\(profile\)/);
  // 返回沿用既有 ready/error 状态投影类型。
  assert.match(api, /Promise<ProviderProfileStatus>/);
});

test("Settings provider wizard gates adding behind a passing dry run", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  assert.match(section, /testProviderProfileDraft/);
  // 测试通过前按钮是「测试」，通过后才变「完成」；完成要求 payload 齐备
  // （含测试成功后选定的模型）。
  assert.match(section, /disabled=\{wizardVerified \? !wizardPayload : !wizardProbePayload\}/);
  assert.match(section, /\{wizardVerified \? "完成" : "测试"\}/);
  // 验证结论绑定表单指纹：再次变动即失效回到未验证态。
  assert.match(section, /wizardCheck\.fingerprint !== wizardFingerprint[\s\S]{0,80}abort\(\)/);
  assert.match(section, /wizardVerified = wizardCheck\.phase === "done"\s*\n\s*&& wizardCheck\.passed\s*\n\s*&& wizardCheck\.fingerprint === wizardFingerprint/);
  // 冻结提交时的探测候选与指纹，期间的表单变动不得解锁保存。
  assert.match(section, /const payload = wizardProbePayload;\s*\n\s*const fingerprint = wizardFingerprint;/);
});

test("Settings provider wizard reports dry-run results inline instead of toast-only", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  // 步骤 2 的内联 live 文案承载主要结果反馈：成功绿字、失败红字留本步。
  assert.match(section, /<p className="task6-ok" aria-live="polite">✓ 连接成功<\/p>/);
  assert.match(section, /<Notice tone="error">连接失败<\/Notice>/);
  assert.match(section, /连接成功 · /);
  assert.match(section, /请核对 API Key 与端点后重试/);
  // 校验进行中可再次点击取消，不卡死 UI；Toast 仅作其余操作的辅助反馈。
  assert.match(section, /再次点「测试」可取消/);
  assert.match(section, /若 controller\.signal\.aborted|\(controller\.signal\.aborted\)/);
});
