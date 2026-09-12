/**
 * LLM Provider 添加向导的纯逻辑（无 React、无请求）。
 *
 * 合同（点点 0911 线框拍板）：两步向导——①选类型（类型卡只显示名字，无说明
 * 副文案；完整厂商目录可滚动，不再写死四张卡）；②名称与凭据（显示名称 +
 * API Key，自定义多一行 Base URL），底部「测试」：失败红字「连接失败」留在
 * 本步，成功绿字「✓ 连接成功」且按钮变「完成」写入列表。
 *
 * 类型目录从内置 catalog（/v1/providers/catalog）派生：官方中转
 *（aiming-cookie-relay）是托管档案，不出现在「添加服务」列表；目录缺失时
 * 回落固定四项，「自定义 OpenAI 兼容」恒兜底在末尾。卡片可用性仍按目录
 * 存在与否置灰（目录缺失置灰逻辑保留）。
 *
 * 内置类型的端点由后端 vendored 目录按 provider_id 决定（sidecar 的
 * parseProviderProfile 不接受 builtin 自带 base_url），因此向导对内置类型
 * 只展示只读端点 Preview；只有自定义类型才显示可编辑 Base URL。
 *
 * 测试与模型解耦（点点拍板）：模型列表在测试成功后才展示/获取；内置类型
 * 不选模型也能先测连——sidecar /test 对缺 model 的候选档走免模型连通探测。
 * 因此连通检查的指纹只绑定影响连通判定的字段，模型/名称/思考力度的后续
 * 调整不令已通过的测试失效。
 */

import { OFFICIAL_RELAY_PROVIDER_ID } from "@/lib/provider-helpers";
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
  /** 自定义类型：显示可编辑 Base URL 并走协议自动探测。 */
  custom: boolean;
}

export const WIZARD_TYPES: readonly WizardTypeOption[] = [
  { id: "deepseek", label: "DeepSeek", custom: false },
  { id: "openai", label: "OpenAI", custom: false },
  { id: "anthropic", label: "Anthropic", custom: false },
  { id: "custom", label: "自定义 OpenAI 兼容", custom: true },
];

const WIZARD_CUSTOM_FALLBACK: WizardTypeOption = { id: "custom", label: "自定义 OpenAI 兼容", custom: true };

/**
 * 第 1 步类型卡列表（点点 0911 线框拍板）：从内置 catalog 派生完整厂商
 * 目录（官方中转除外），末尾恒兜底「自定义 OpenAI 兼容」；catalog 缺失时
 * 回落固定四项。渲染层负责对目录缺失的内置卡置灰。
 */
export function wizardTypeOptions(catalog: ProviderCatalogV1 | null): WizardTypeOption[] {
  const builtin = (catalog?.providers ?? [])
    .filter((provider) => provider.provider_id !== OFFICIAL_RELAY_PROVIDER_ID)
    .map((provider) => ({ id: provider.provider_id, label: provider.provider_name, custom: false }));
  return builtin.length ? [...builtin, WIZARD_CUSTOM_FALLBACK] : [...WIZARD_TYPES];
}

export const WIZARD_STEP_COUNT = 2;

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
  // 打开向导时无预选类型（线框）：选卡后「下一步」才解锁。
  return { typeId: "", name: "", baseUrl: "", modelId: "", apiKey: "", reasoningEffort: "" };
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

/** 显示名称重名软提示（点点拍板：只提示不阻断，大小写不敏感）。 */
export function wizardNameConflicts(name: string, existingNames: readonly string[]): boolean {
  const candidate = name.trim().toLowerCase();
  if (!candidate) return false;
  return existingNames.some((existing) => existing.trim().toLowerCase() === candidate);
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

/**
 * 连通性检查绑定的表单指纹：仅覆盖影响连通判定的字段（类型、端点、Key）。
 * 模型在测试成功后才选择、名称与思考力度不影响连通、自定义协议 kind 是由
 * 端点+Key 派生的结论——均不参与：测试通过后挑选模型不得令「✓ 连接成功」
 * 失效（点点 0911 拍板）。
 */
export function wizardCheckFingerprint(draft: WizardDraft): string {
  return JSON.stringify({
    typeId: draft.typeId,
    baseUrl: draft.baseUrl.trim(),
    apiKey: draft.apiKey,
  });
}

export type WizardProbeContext = Pick<
  WizardBuildContext,
  "isCustom" | "customKind" | "builtinProviderAvailable" | "defaultName"
>;

/**
 * 「测试」按钮的候选 payload：内置不选模型也可先测连（sidecar 对缺 model
 * 的候选走免模型连通探测）。凭据/端点不齐备时返回 null（按钮禁用）。
 */
export function buildWizardProbePayload(
  draft: WizardDraft,
  context: WizardProbeContext,
): ProviderProfileCreate | null {
  if (context.isCustom) {
    if (!draft.baseUrl.trim() || !draft.apiKey.trim()) return null;
    return {
      name: draft.name.trim() || context.defaultName,
      kind: context.customKind,
      provider_id: null,
      base_url: draft.baseUrl.trim(),
      model_id: "",
      api_key: draft.apiKey || null,
      is_default: false,
    };
  }
  if (!context.builtinProviderAvailable) return null;
  return {
    name: draft.name.trim() || context.defaultName,
    kind: "builtin",
    provider_id: draft.typeId,
    base_url: null,
    model_id: "",
    api_key: draft.apiKey || null,
    is_default: false,
  };
}

/** 表单齐备：能构建入库 payload（「完成」写入列表的门槛）。 */
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
