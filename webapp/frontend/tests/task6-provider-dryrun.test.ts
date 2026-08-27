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

test("SettingsWorkspace gates adding behind a passing dry run", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /testProviderProfileDraft/);
  // 未验证通过前「添加 Provider」不可用。
  assert.match(settings, /disabled=\{!canAddProvider \|\| \(draftVerifyApplies && !draftVerified\)\}/);
  // 验证结论绑定表单指纹：再次变动即失效回到未验证态。
  assert.match(settings, /draftCheck\.fingerprint !== draftFingerprint[\s\S]{0,80}abort\(\)/);
  assert.match(settings, /draftVerified = draftCheck\.phase === "done"\s*\n\s*&& draftCheck\.passed\s*\n\s*&& draftCheck\.fingerprint === draftFingerprint/);
  // 冻结提交时的候选 payload，期间的表单变动不得解锁保存。
  assert.match(settings, /const payload = draftPayload;\s*\n\s*const fingerprint = draftFingerprint;/);
});

test("SettingsWorkspace reports dry-run results inline instead of toast-only", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 表单下方的内联 live 区块承载主要结果反馈。
  assert.match(settings, /aria-live="polite" style=\{\{ display: "grid"/);
  assert.match(settings, /检查通过 · /);
  assert.match(settings, /请核对 API Key、Base URL 与所选模型后重试/);
  assert.match(settings, /<Notice tone="error">\{draftCheck\.message\}<\/Notice>/);
  // 校验进行中可再次点击取消，不卡死 UI；Toast 仅作其余操作的辅助反馈。
  assert.match(settings, /停止检查/);
  assert.match(settings, /若 controller\.signal\.aborted|\(controller\.signal\.aborted\)/);
  assert.match(settings, /\{feedback \? <Toast/);
});
