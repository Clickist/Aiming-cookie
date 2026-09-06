/**
 * LLM Provider 添加向导的纯逻辑（无 React、无请求）。
 *
 * 合同（点点拍板）：四步向导，最后一步「测试连接」是硬门槛——失败留在本步，
 * 成功才「完成」写入。类型入口固定四个，不做厂商目录墙：
 * DeepSeek / OpenAI / Anthropic / 自定义 OpenAI 兼容。
 *
 * 内置类型的端点由后端 vendored 目录按 provider_id 决定（sidecar 的
 * parseProviderProfile 不接受 builtin 自带 base_url），因此向导对内置类型
 * 只展示只读端点 Preview；只有自定义类型才显示可编辑 Base URL。
 */

import type {
  CustomProviderKind,
  CustomProviderModel,
  ProviderCatalogEntry,
  ProviderCatalogV1,
  ProviderProfileCreate,
  ProviderReasoningEffort,
} from "@/lib/types";

export interface WizardTypeOption {
  id: string;
  label: string;
  hint: string;
  /** 自定义类型：显示可编辑 Base URL 并走协议自动探测。 */
  custom: boolean;
}

export const WIZARD_TYPES: readonly WizardTypeOption[] = [
  { id: "deepseek", label: "DeepSeek", hint: "官方 DeepSeek API", custom: false },
  { id: "openai", label: "OpenAI", hint: "官方 OpenAI API", custom: false },
  { id: "anthropic", label: "Anthropic", hint: "官方 Claude API", custom: false },
  { id: "custom", label: "自定义 OpenAI 兼容", hint: "任意 OpenAI 兼容端点（自动识别 Anthropic 兼容协议）", custom: true },
];

export const WIZARD_STEP_COUNT = 4;

export interface WizardDraft {
  typeId: string;
  name: string;
  /** 仅自定义类型可编辑；内置类型后端按 provider_id 固定端点。 */
  baseUrl: string;
  modelId: string;
  apiKey: string;
  reasoningEffort: ProviderReasoningEffort | "";
}

export function emptyWizardDraft(): WizardDraft {
  return { typeId: WIZARD_TYPES[0].id, name: "", baseUrl: "", modelId: "", apiKey: "", reasoningEffort: "" };
}

export function isWizardCustom(typeId: string): boolean {
  return WIZARD_TYPES.find((type) => type.id === typeId)?.custom ?? false;
}

/** 内置类型在当前 catalog 是否可用（vendored 目录缺失时选项置灰）。 */
export function wizardCatalogProvider(
  catalog: ProviderCatalogV1 | null,
  typeId: string,
): ProviderCatalogEntry | undefined {
  return catalog?.providers.find((provider) => provider.provider_id === typeId) ?? undefined;
}

export function wizardDefaultName(catalog: ProviderCatalogV1 | null, typeId: string): string {
  if (isWizardCustom(typeId)) return "自定义 Provider";
  return wizardCatalogProvider(catalog, typeId)?.provider_name ?? "Provider";
}

/**
 * 实际请求地址 Preview（纯拼接，不发请求）。拼接规则与 sidecar 一致：
 * - OpenAI 兼容：`{base}/chat/completions`，base 原样保留（带不带 /v1 都行）。
 * - Anthropic 兼容：base 去尾部 `/v1` 后拼 `/v1/messages`。
 */
export function previewCustomRequestUrl(kind: CustomProviderKind, baseUrl: string): string {
  const trimmed = baseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  if (kind === "custom_anthropic_compatible") {
    return `${trimmed.replace(/\/v1$/, "")}/v1/messages`;
  }
  return `${trimmed}/chat/completions`;
}

/** 内置类型的只读端点 Preview（catalog 提供的 base_url）。 */
export function previewBuiltinRequestUrl(
  catalog: ProviderCatalogV1 | null,
  typeId: string,
): string {
  return wizardCatalogProvider(catalog, typeId)?.base_url ?? "";
}

export interface WizardBuildContext {
  isCustom: boolean;
  /** 自定义类型协议自动探测确认后落定的 kind。 */
  customKind: CustomProviderKind;
  /** 自定义类型发现的模型（带来 context_window / max_tokens 元数据）。 */
  selectedCustomModel?: CustomProviderModel;
  /** 内置目录里选中模型是否确认支持推理（决定 reasoning_effort 是否随表单提交）。 */
  builtinModelIsReasoning: boolean;
  /** 内置类型在 catalog 中可用（provider 存在才可建）。 */
  builtinProviderAvailable: boolean;
  /** 表单名为空时的回落名（与旧表单语义一致）。 */
  defaultName: string;
  /** 第一个档案自动设为当前。 */
  isFirstProfile: boolean;
}

/** 表单齐备：能构建入库 payload（第 4 步测试连接的门槛之一）。 */
export function wizardPayloadReady(draft: WizardDraft, context: WizardBuildContext): boolean {
  if (!draft.modelId.trim()) return false;
  if (context.isCustom) {
    return Boolean(draft.baseUrl.trim() && draft.apiKey.trim());
  }
  return context.builtinProviderAvailable;
}

/**
 * 干跑与入库共用同一份候选 payload（「检查连接」验的就是将来要存的内容）。
 * 未齐备时返回 null。
 */
export function buildWizardPayload(
  draft: WizardDraft,
  context: WizardBuildContext,
): ProviderProfileCreate | null {
  if (!wizardPayloadReady(draft, context)) return null;
  return {
    name: draft.name.trim() || context.defaultName,
    kind: context.isCustom ? context.customKind : "builtin",
    provider_id: context.isCustom ? null : draft.typeId,
    base_url: context.isCustom ? draft.baseUrl.trim() : null,
    model_id: draft.modelId.trim(),
    reasoning_effort: !context.isCustom && context.builtinModelIsReasoning && draft.reasoningEffort
      ? draft.reasoningEffort
      : null,
    context_window: context.isCustom ? context.selectedCustomModel?.context_window ?? null : null,
    max_tokens: context.isCustom ? context.selectedCustomModel?.max_tokens ?? null : null,
    api_key: draft.apiKey || null,
    is_default: context.isFirstProfile,
  };
}
