import assert from "node:assert/strict";
import { test } from "node:test";

import {
  WIZARD_STEP_COUNT,
  WIZARD_TYPES,
  buildWizardPayload,
  buildWizardProbePayload,
  emptyWizardDraft,
  isWizardCustom,
  previewBuiltinRequestUrl,
  previewCustomRequestUrl,
  wizardCatalogProvider,
  wizardCheckFingerprint,
  wizardDefaultName,
  wizardNameConflicts,
  wizardPayloadReady,
  isMemberWizardType,
  wizardTypeOptions,
  type WizardBuildContext,
  type WizardDraft,
} from "./provider-wizard";
import type { ProviderCatalogV1 } from "./types";

const catalog: ProviderCatalogV1 = {
  schema_version: "coach_provider_catalog.v1",
  providers: [
    {
      provider_id: "deepseek",
      provider_name: "DeepSeek",
      auth_modes: ["api_key"],
      base_url: "https://api.deepseek.com",
      models: [{ model_id: "deepseek-chat", model_name: "DeepSeek Chat", provider_id: "deepseek" }],
    },
    {
      provider_id: "openai",
      provider_name: "OpenAI",
      auth_modes: ["api_key"],
      base_url: "https://api.openai.com/v1",
      models: [{ model_id: "gpt-4o", model_name: "GPT-4o", provider_id: "openai" }],
    },
  ],
};

const customContext: WizardBuildContext = {
  isCustom: true,
  customKind: "custom_openai_compatible",
  builtinModelIsReasoning: false,
  builtinProviderAvailable: false,
  defaultName: "自定义 Provider",
  isFirstProfile: false,
};

test("wizard keeps the fixed four-entry list as the missing-catalog fallback", () => {
  assert.equal(WIZARD_STEP_COUNT, 2);
  assert.deepEqual(WIZARD_TYPES.map((type) => type.id), ["deepseek", "openai", "anthropic", "custom"]);
  assert.equal(WIZARD_TYPES.filter((type) => type.custom).length, 1);
  // 类型卡只显示名字（点点拍板）：不再携带说明副文案。
  for (const type of WIZARD_TYPES) {
    assert.equal("hint" in type, false);
  }
});

test("wizard derives the full vendor catalog with the member tier pinned first", () => {
  // 点点 0911 线框拍板：第 1 步类型卡从 catalog 派生完整厂商目录，
  // 「自定义 OpenAI 兼容」恒兜底在末尾。
  const options = wizardTypeOptions(catalog);
  assert.deepEqual(options.map((type) => type.id), ["deepseek", "openai", "custom"]);
  assert.equal(options[options.length - 1].label, "自定义 OpenAI 兼容");
  assert.equal(options.filter((type) => type.custom).length, 1);
  // 会员档（WP-C 升级，线框 ①）：出现在「添加服务」列表且**置顶为推荐**，
  // 显示名照线框文案，不是目录里的旧名。
  const withRelay: ProviderCatalogV1 = {
    schema_version: "coach_provider_catalog.v1",
    providers: [
      ...catalog.providers,
      { provider_id: "aiming-cookie-relay", provider_name: "Aiming Cookie 官方", auth_modes: ["api_key"], models: [] },
    ],
  };
  const memberOptions = wizardTypeOptions(withRelay);
  assert.equal(memberOptions[0].id, "aiming-cookie-relay");
  assert.equal(memberOptions[0].label, "Aiming Cookie（推荐）");
  assert.equal(memberOptions[0].custom, false);
  assert.equal(isMemberWizardType("aiming-cookie-relay"), true);
  assert.equal(isMemberWizardType("deepseek"), false);
  // 目录缺失时回落固定四项（离线行为与上一批一致）——没有目录就没有会员档，
  // 不做"凭硬编码 ID 也能选"的幽灵入口。
  assert.deepEqual(wizardTypeOptions(null).map((type) => type.id), ["deepseek", "openai", "anthropic", "custom"]);
  assert.deepEqual(wizardTypeOptions({ providers: [] }).map((type) => type.id), ["deepseek", "openai", "anthropic", "custom"]);
});

test("wizard resolves builtin entries from the catalog and flags availability", () => {
  assert.equal(wizardCatalogProvider(catalog, "deepseek")?.provider_name, "DeepSeek");
  assert.equal(wizardCatalogProvider(catalog, "anthropic"), undefined);
  assert.equal(wizardDefaultName(catalog, "deepseek"), "DeepSeek");
  assert.equal(wizardDefaultName(catalog, "custom"), "自定义 Provider");
  assert.equal(isWizardCustom("custom"), true);
  assert.equal(isWizardCustom("deepseek"), false);
  // 打开向导无预选类型（线框）：选卡后「下一步」才解锁。
  assert.equal(emptyWizardDraft().typeId, "");
  assert.equal(isWizardCustom(""), false);
});

test("custom request preview keeps /v1 as-is for OpenAI and strips it for Anthropic", () => {
  assert.equal(
    previewCustomRequestUrl("custom_openai_compatible", "https://gw.example/v1/"),
    "https://gw.example/v1/chat/completions",
  );
  assert.equal(
    previewCustomRequestUrl("custom_anthropic_compatible", "https://gw.example/v1"),
    "https://gw.example/v1/messages",
  );
  assert.equal(previewCustomRequestUrl("custom_openai_compatible", "  "), "");
});

test("builtin endpoint preview comes from the catalog base_url", () => {
  assert.equal(previewBuiltinRequestUrl(catalog, "deepseek"), "https://api.deepseek.com");
  assert.equal(previewBuiltinRequestUrl(catalog, "anthropic"), "");
});

test("display name conflicts are a soft hint matched case-insensitively", () => {
  const names = ["My DeepSeek", "公司网关 "];
  assert.equal(wizardNameConflicts("", names), false);
  assert.equal(wizardNameConflicts("另一个名字", names), false);
  assert.equal(wizardNameConflicts("my deepseek", names), true);
  assert.equal(wizardNameConflicts("  公司网关", names), true);
});

test("check fingerprint binds only the connectivity fields, not model or name", () => {
  const draft: WizardDraft = { ...emptyWizardDraft(), typeId: "custom", baseUrl: "https://gw.example/v1", apiKey: "sk-1" };
  const fingerprint = wizardCheckFingerprint(draft);
  // 测试成功后挑选模型 / 改名不得令结论失效。
  assert.equal(wizardCheckFingerprint({ ...draft, modelId: "qwen3" }), fingerprint);
  assert.equal(wizardCheckFingerprint({ ...draft, name: "改名" }), fingerprint);
  // 端点、Key 与类型变化则重锁完成按钮（指纹失效机制保留）。
  assert.notEqual(wizardCheckFingerprint({ ...draft, baseUrl: "https://other.example" }), fingerprint);
  assert.notEqual(wizardCheckFingerprint({ ...draft, apiKey: "sk-2" }), fingerprint);
  assert.notEqual(wizardCheckFingerprint({ ...draft, typeId: "deepseek" }), fingerprint);
});

test("probe payload lets builtin test connectivity without a model and gates on inputs", () => {
  const builtinDraft: WizardDraft = { ...emptyWizardDraft(), typeId: "deepseek", apiKey: "sk-1" };
  const probe = buildWizardProbePayload(builtinDraft, {
    isCustom: false,
    customKind: "custom_openai_compatible",
    builtinProviderAvailable: true,
    defaultName: "DeepSeek",
  });
  assert.ok(probe);
  assert.equal(probe.kind, "builtin");
  assert.equal(probe.provider_id, "deepseek");
  assert.equal(probe.model_id, "");
  assert.equal(probe.api_key, "sk-1");
  // 目录不可用时禁测；Key 可留空（回落已存/环境凭据语义）。
  assert.equal(
    buildWizardProbePayload(builtinDraft, {
      isCustom: false,
      customKind: "custom_openai_compatible",
      builtinProviderAvailable: false,
      defaultName: "DeepSeek",
    }),
    null,
  );
  const customDraft: WizardDraft = { ...emptyWizardDraft(), typeId: "custom", baseUrl: "https://gw.example/v1", apiKey: "sk-1" };
  const customProbe = buildWizardProbePayload(customDraft, {
    isCustom: true,
    customKind: "custom_openai_compatible",
    builtinProviderAvailable: false,
    defaultName: "自定义 Provider",
  });
  assert.ok(customProbe);
  assert.equal(customProbe.model_id, "");
  assert.equal(customProbe.base_url, "https://gw.example/v1");
  // 自定义缺端点或 Key 时不允许发起测试。
  assert.equal(
    buildWizardProbePayload({ ...customDraft, baseUrl: "" }, {
      isCustom: true,
      customKind: "custom_openai_compatible",
      builtinProviderAvailable: false,
      defaultName: "自定义 Provider",
    }),
    null,
  );
  assert.equal(
    buildWizardProbePayload({ ...customDraft, apiKey: "" }, {
      isCustom: true,
      customKind: "custom_openai_compatible",
      builtinProviderAvailable: false,
      defaultName: "自定义 Provider",
    }),
    null,
  );
});

test("payload readiness gates on model plus custom baseUrl/apiKey", () => {
  const draft: WizardDraft = { ...emptyWizardDraft(), typeId: "custom", modelId: "qwen3", baseUrl: "https://gw.example/v1", apiKey: "sk-1" };
  assert.equal(wizardPayloadReady(draft, customContext), true);
  assert.equal(wizardPayloadReady({ ...draft, baseUrl: "" }, customContext), false);
  assert.equal(wizardPayloadReady({ ...draft, apiKey: "" }, customContext), false);
  assert.equal(wizardPayloadReady({ ...draft, modelId: " " }, customContext), false);
});

test("builtin payload is gated on catalog availability and pins provider_id", () => {
  const draft: WizardDraft = { ...emptyWizardDraft(), typeId: "deepseek", modelId: "deepseek-chat" };
  const builtinContext: WizardBuildContext = {
    isCustom: false,
    customKind: "custom_openai_compatible",
    builtinModelIsReasoning: false,
    builtinProviderAvailable: true,
    defaultName: "DeepSeek",
    isFirstProfile: false,
  };
  assert.equal(wizardPayloadReady(draft, builtinContext), true);
  assert.equal(wizardPayloadReady(draft, { ...builtinContext, builtinProviderAvailable: false }), false);
  const payload = buildWizardPayload(draft, builtinContext);
  assert.equal(payload?.kind, "builtin");
  assert.equal(payload?.provider_id, "deepseek");
  assert.equal(payload?.base_url, null);
});

test("built payload carries the dry-run and creation fields in one shape", () => {
  const draft: WizardDraft = {
    ...emptyWizardDraft(),
    typeId: "custom",
    name: "  公司网关 ",
    baseUrl: "https://gw.example/v1",
    modelId: "qwen3",
    apiKey: "sk-1",
  };
  const payload = buildWizardPayload(draft, {
    ...customContext,
    selectedCustomModel: { model_id: "qwen3", context_window: 32768, max_tokens: 8192 },
  });
  assert.ok(payload);
  assert.equal(payload.name, "公司网关");
  assert.equal(payload.kind, "custom_openai_compatible");
  assert.equal(payload.provider_id, null);
  assert.equal(payload.base_url, "https://gw.example/v1");
  assert.equal(payload.model_id, "qwen3");
  assert.equal(payload.api_key, "sk-1");
  assert.equal(payload.context_window, 32768);
  assert.equal(payload.max_tokens, 8192);
  // 非第一个档案默认不抢「当前使用」。
  assert.equal(payload.is_default, false);
});

test("first profile is created as the active one and reasoning only rides builtin reasoning models", () => {
  const draft: WizardDraft = { ...emptyWizardDraft(), typeId: "deepseek", modelId: "deepseek-reasoner", reasoningEffort: "high" };
  const payload = buildWizardPayload(draft, {
    isCustom: false,
    customKind: "custom_openai_compatible",
    builtinModelIsReasoning: true,
    builtinProviderAvailable: true,
    defaultName: "DeepSeek",
    isFirstProfile: true,
  });
  assert.equal(payload?.is_default, true);
  assert.equal(payload?.reasoning_effort, "high");
  const nonReasoning = buildWizardPayload(draft, {
    isCustom: false,
    customKind: "custom_openai_compatible",
    builtinModelIsReasoning: false,
    builtinProviderAvailable: true,
    defaultName: "DeepSeek",
    isFirstProfile: true,
  });
  assert.equal(nonReasoning?.reasoning_effort, null);
});
