import assert from "node:assert/strict";
import { test } from "node:test";

import {
  WIZARD_STEP_COUNT,
  WIZARD_TYPES,
  buildWizardPayload,
  emptyWizardDraft,
  isWizardCustom,
  previewBuiltinRequestUrl,
  previewCustomRequestUrl,
  wizardCatalogProvider,
  wizardDefaultName,
  wizardPayloadReady,
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

test("wizard offers exactly the four agreed type entries, custom last", () => {
  assert.equal(WIZARD_TYPES.length, 4);
  assert.deepEqual(WIZARD_TYPES.map((type) => type.id), ["deepseek", "openai", "anthropic", "custom"]);
  assert.equal(WIZARD_TYPES.filter((type) => type.custom).length, 1);
  assert.equal(WIZARD_STEP_COUNT, 4);
});

test("wizard resolves builtin entries from the catalog and flags availability", () => {
  assert.equal(wizardCatalogProvider(catalog, "deepseek")?.provider_name, "DeepSeek");
  assert.equal(wizardCatalogProvider(catalog, "anthropic"), undefined);
  assert.equal(wizardDefaultName(catalog, "deepseek"), "DeepSeek");
  assert.equal(wizardDefaultName(catalog, "custom"), "自定义 Provider");
  assert.equal(isWizardCustom("custom"), true);
  assert.equal(isWizardCustom("deepseek"), false);
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
