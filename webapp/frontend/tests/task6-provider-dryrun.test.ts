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
  // 未验证通过前向导第 4 步的「完成」不可用。
  assert.match(section, /disabled=\{!wizardVerified\}/);
  // 验证结论绑定表单指纹：再次变动即失效回到未验证态。
  assert.match(section, /wizardCheck\.fingerprint !== wizardFingerprint[\s\S]{0,80}abort\(\)/);
  assert.match(section, /wizardVerified = wizardCheck\.phase === "done"\s*\n\s*&& wizardCheck\.passed\s*\n\s*&& wizardCheck\.fingerprint === wizardFingerprint/);
  // 冻结提交时的候选 payload，期间的表单变动不得解锁保存。
  assert.match(section, /const payload = wizardPayload;\s*\n\s*const fingerprint = wizardFingerprint;/);
});

test("Settings provider wizard reports dry-run results inline instead of toast-only", async () => {
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  // 向导第 4 步的内联 live 区块承载主要结果反馈。
  assert.match(section, /aria-live="polite" className="task6-wizard-step-body"/);
  assert.match(section, /连接成功 · /);
  assert.match(section, /请核对 API Key、Base URL 与所选模型后重试/);
  assert.match(section, /<Notice tone="error">\{wizardCheck\.message\}<\/Notice>/);
  // 校验进行中可再次点击取消，不卡死 UI；Toast 仅作其余操作的辅助反馈。
  assert.match(section, /停止检查/);
  assert.match(section, /若 controller\.signal\.aborted|\(controller\.signal\.aborted\)/);
});
