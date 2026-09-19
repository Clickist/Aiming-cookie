import {
  PROVIDER_CATALOG_SCHEMA,
  isRecord,
  type CoachRuntimeProviderProfile,
  type CustomProviderModel,
  type ProviderCatalogModel,
  type ProviderCatalogResponse,
  type ProviderCredential,
} from "./contracts.ts";
import {
  SnapshotCredentialStore,
  projectProviderAuthCapability,
  type PiAuthProvider,
} from "./provider-auth.ts";
import {
  loadPiAi,
  loadPiAnthropicMessages,
  loadPiOpenAiCompletions,
  loadPiProvidersAll,
} from "./pi-source.ts";
import { parseProviderProfile, ProviderProfileError } from "./provider-profile.ts";
import type { StreamFn } from "./stream-openai-compatible.ts";

export type PiModel = {
  id: string;
  name: string;
  api: string;
  provider: string;
  baseUrl: string;
  reasoning: boolean;
  input: string[];
  contextWindow: number;
  maxTokens: number;
};

type PiAssistantStream = {
  result(): Promise<{ stopReason?: string; errorMessage?: string }>;
};

type PiProvider = PiAuthProvider & {
  stream: (...args: unknown[]) => unknown;
  streamSimple: (...args: unknown[]) => unknown;
};

export type PiModels = {
  getProviders(): readonly PiProvider[];
  getProvider(id: string): PiProvider | undefined;
  getModels(provider?: string): readonly PiModel[];
  getModel(provider: string, modelId: string): PiModel | undefined;
  getAuth(model: PiModel): Promise<{ auth: Record<string, unknown>; source?: string } | undefined>;
  setProvider(provider: PiProvider): void;
  streamSimple(model: PiModel, context: unknown, options?: Record<string, unknown>): PiAssistantStream;
};

export type ResolvedProviderModel = {
  models: PiModels;
  model: PiModel;
  credentialStore: SnapshotCredentialStore;
  hasRuntimeCredential: boolean;
};

function profileCredential(profile: CoachRuntimeProviderProfile): ProviderCredential | undefined {
  if (profile.credential) return profile.credential;
  return profile.api_key ? { type: "api_key", key: profile.api_key } : undefined;
}

async function createBuiltinModels(credentials: SnapshotCredentialStore): Promise<PiModels> {
  const all = (await loadPiProvidersAll()) as {
    builtinModels: (options?: { credentials?: SnapshotCredentialStore }) => PiModels;
  };
  const models = all.builtinModels({ credentials });
  await injectAimingCookieRelayProvider(models);
  return models;
}

/**
 * Aiming Cookie（推荐）——会员档（WP-C 升级，2026-09-19 契约冻结）。
 *
 * 这条档从「内测中转（key 对用户可见、模型全家桶、按 API 计费）」升级为
 * **账号订阅档**：auth 换成浏览器登录 + deep-link 换票拿到的 JWT（存在档案
 * credential 里，见 member-auth.ts），模型锁 `deepseek-v4-flash`，端点固定
 * 指向广东 ECS 的 ac-gateway 会员入口（契约 §0：客户端 AI base_url 是
 * `https://token.gearclickist.com:8443/member`，网关只透传 `/v1/...`，
 * 故这里带上 `/v1` 让 OpenAI 兼容调用打到 `/member/v1/chat/completions`）。
 *
 * 目录、档解析、方言继承三条链路共用 createBuiltinModels，因此在这里注入。
 * 模型清单固定一条，能力/方言按 model_id 从 Pi 内建目录继承
 * （deepseek-v4-flash 自带 thinkingFormat 方言，缺失会让"说出声"复发）。
 *
 * 基址仍可由环境变量覆盖，供本地 dev / e2e 指向 mock：
 * `AC_MEMBER_GATEWAY_BASE_URL`（新）优先，`AC_RELAY_BASE_URL`（旧注入名）保留
 * 一个版本以兼容既有构建脚本。
 */
export const AIMING_COOKIE_RELAY_PROVIDER_ID = "aiming-cookie-relay";
/** 档显示名（剑线稿 ①：置顶推荐、账号订阅，登录即用）。 */
export const AIMING_COOKIE_RELAY_PROVIDER_NAME = "Aiming Cookie（推荐）";
/** 模型锁（契约 §0）：sub / boost 两令牌都是这一个模型。 */
export const AIMING_COOKIE_RELAY_MODEL_ID = "deepseek-v4-flash";
/** 会员档端点默认值（契约 §0）；带 /v1 以便 OpenAI 兼容路径透传给网关。 */
export const MEMBER_GATEWAY_BASE_URL = "https://token.gearclickist.com:8443/member/v1";

export function relayBaseUrl(): string {
  return process.env.AC_MEMBER_GATEWAY_BASE_URL
    ?? process.env.AC_RELAY_BASE_URL
    ?? MEMBER_GATEWAY_BASE_URL;
}
const AIMING_COOKIE_RELAY_MODEL_IDS = [AIMING_COOKIE_RELAY_MODEL_ID] as const;

async function injectAimingCookieRelayProvider(models: PiModels): Promise<void> {
  const ai = (await loadPiAi()) as {
    createProvider: (options: Record<string, unknown>) => PiProvider;
  };
  const openAiCompletions = (await loadPiOpenAiCompletions()) as {
    stream: (...args: unknown[]) => unknown;
    streamSimple: (...args: unknown[]) => unknown;
  };
  // 能力与方言按 model_id 从 Pi 内建目录继承（与 resolveCustomProfile 的
  // catalogHit 同款）：deepseek-v4-flash 自带 thinkingFormat 方言，缺失会
  // 让"说出声"复发。目录未收录的模型按默认能力处理。
  const catalogModels = models.getModels() as Array<
    PiModel & { compat?: Record<string, unknown>; thinkingLevelMap?: unknown }
  >;
  const relayModels = AIMING_COOKIE_RELAY_MODEL_IDS.map((modelId) => {
    const catalogHit = catalogModels.find((candidate) => candidate.id === modelId);
    return {
      id: modelId,
      name: catalogHit?.name ?? modelId,
      api: "openai-completions",
      provider: AIMING_COOKIE_RELAY_PROVIDER_ID,
      baseUrl: relayBaseUrl(),
      reasoning: catalogHit ? catalogHit.reasoning === true : true,
      input: catalogHit ? [...catalogHit.input] : ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: catalogHit?.contextWindow ?? CUSTOM_PROVIDER_DEFAULT_CONTEXT_WINDOW,
      maxTokens: catalogHit?.maxTokens ?? CUSTOM_PROVIDER_DEFAULT_MAX_TOKENS,
      ...(catalogHit?.compat ? { compat: catalogHit.compat } : {}),
      ...(catalogHit?.thinkingLevelMap ? { thinkingLevelMap: catalogHit.thinkingLevelMap } : {}),
    };
  });
  const provider = ai.createProvider({
    id: AIMING_COOKIE_RELAY_PROVIDER_ID,
    name: AIMING_COOKIE_RELAY_PROVIDER_NAME,
    baseUrl: relayBaseUrl(),
    auth: {
      // 会员档凭据 = 换票拿到的 JWT（member-auth.ts 写进档 credential）。
      // 交互式 prompt 保留为兜底（未走登录链时手贴凭据），主链路不触达。
      apiKey: {
        name: `${AIMING_COOKIE_RELAY_PROVIDER_NAME} token`,
        login: async (interaction: {
          prompt: (prompt: { type: string; message: string }) => Promise<string>;
        }) => ({
          type: "api_key",
          key: await interaction.prompt({
            type: "secret",
            message: `输入 ${AIMING_COOKIE_RELAY_PROVIDER_NAME} token`,
          }),
        }),
        resolve: async ({ credential }: { credential?: { key?: string } }) =>
          credential?.key
            ? { auth: { apiKey: credential.key }, source: "stored credential" }
            : undefined,
      },
    },
    models: relayModels,
    api: {
      stream: openAiCompletions.stream,
      streamSimple: openAiCompletions.streamSimple,
    },
  });
  models.setProvider(provider);
}

export function toCatalogModel(model: PiModel): ProviderCatalogModel {
  return {
    model_id: model.id,
    model_name: model.name,
    api: model.api,
    provider_id: model.provider,
    base_url: model.baseUrl,
    reasoning: model.reasoning,
    input: [...model.input],
    context_window: model.contextWindow,
    max_tokens: model.maxTokens,
  };
}

export async function listBuiltinProviderCatalog(): Promise<ProviderCatalogResponse> {
  const credentials = new SnapshotCredentialStore("__catalog__");
  const models = await createBuiltinModels(credentials);
  // Aiming Cookie 官方固定在目录首位（1.0.0 前点点拍板）：注入顺序在 pi
  // 内建 Provider 之后，这里在目录出口重排，onboarding 与设置共用本目录。
  const providers = models.getProviders();
  const ordered = [
    ...providers.filter((provider) => provider.id === AIMING_COOKIE_RELAY_PROVIDER_ID),
    ...providers.filter((provider) => provider.id !== AIMING_COOKIE_RELAY_PROVIDER_ID),
  ];
  return {
    schema_version: PROVIDER_CATALOG_SCHEMA,
    providers: ordered.map((provider) => ({
      ...projectProviderAuthCapability(provider),
      base_url: provider.baseUrl ?? null,
      models: models.getModels(provider.id).map(toCatalogModel),
    })),
  };
}

const CUSTOM_MODEL_DISCOVERY_TIMEOUT_MS = 10_000;
const CUSTOM_MODEL_MAX_ITEMS = 200;

// Many OpenAI-compatible endpoints (e.g. DeepSeek) omit context_window/max_tokens
// from their /models response; fall back to the same capability pair the vendored
// pi coding-agent applies to custom providers (model-registry.ts).
const CUSTOM_PROVIDER_DEFAULT_CONTEXT_WINDOW = 128_000;
const CUSTOM_PROVIDER_DEFAULT_MAX_TOKENS = 16_384;

/**
 * Read a custom Provider `/models` list without persisting its API key.
 * Mirrors the removed Python discovery helper; this is a plain HTTP proxy, not
 * a Pi capability.
 */
export async function fetchCustomProviderModels(
  protocol: "openai-completions" | "anthropic-messages",
  baseUrl: string,
  apiKey: string,
  timeoutMs: number = CUSTOM_MODEL_DISCOVERY_TIMEOUT_MS,
): Promise<CustomProviderModel[]> {
  const headers: Record<string, string> =
    protocol === "anthropic-messages"
      ? { "x-api-key": apiKey, "anthropic-version": "2023-06-01" }
      : { Authorization: `Bearer ${apiKey}` };
  const normalizedBase =
    protocol === "anthropic-messages"
      ? baseUrl.trim().replace(/\/+$/, "").replace(/\/v1$/, "")
      : baseUrl.trim().replace(/\/+$/, "");
  const modelPath = protocol === "anthropic-messages" ? "/v1/models" : "/models";

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  timeout.unref?.();
  try {
    const response = await fetch(`${normalizedBase}${modelPath}`, { headers, signal: controller.signal });
    if (!response.ok) throw new Error("custom provider models request failed");
    const body = await response.json();
    if (!isRecord(body) || !Array.isArray(body.data)) {
      throw new Error("custom provider models response is invalid");
    }
    const models: CustomProviderModel[] = [];
    for (const item of body.data) {
      if (!isRecord(item)) continue;
      const modelId = item.id;
      if (typeof modelId !== "string" || !modelId.trim()) continue;
      if (models.some((candidate) => candidate.model_id === modelId)) continue;
      const contextWindow =
        typeof item.context_window === "number" && Number.isSafeInteger(item.context_window) && item.context_window > 0
          ? item.context_window
          : null;
      const maxTokens =
        typeof item.max_tokens === "number" && Number.isSafeInteger(item.max_tokens) && item.max_tokens > 0
          ? item.max_tokens
          : null;
      models.push({ model_id: modelId.trim(), context_window: contextWindow, max_tokens: maxTokens });
      if (models.length === CUSTOM_MODEL_MAX_ITEMS) break;
    }
    return models;
  } finally {
    clearTimeout(timeout);
  }
}

async function resolveBuiltinProfile(
  profile: Extract<CoachRuntimeProviderProfile, { kind: "builtin" }>,
): Promise<ResolvedProviderModel> {
  const credential = profileCredential(profile);
  const credentialStore = new SnapshotCredentialStore(profile.provider_id, credential);
  const models = await createBuiltinModels(credentialStore);
  if (!models.getProvider(profile.provider_id)) {
    throw new ProviderProfileError("unknown_provider", `Unknown provider: ${profile.provider_id}`);
  }
  const model = models.getModel(profile.provider_id, profile.model_id);
  if (!model) {
    throw new ProviderProfileError(
      "unknown_model",
      `Unknown model for provider ${profile.provider_id}: ${profile.model_id}`,
    );
  }
  return {
    models,
    model,
    credentialStore,
    hasRuntimeCredential: credential !== undefined,
  };
}

async function resolveCustomProfile(
  profile: Extract<
    CoachRuntimeProviderProfile,
    { kind: "custom_openai_compatible" | "custom_anthropic_compatible" }
  >,
): Promise<ResolvedProviderModel> {
  const credential = profileCredential(profile);
  if (!credential || credential.type !== "api_key" || !credential.key) {
    throw new ProviderProfileError("invalid_profile", "Custom provider API key credential is unavailable");
  }
  const credentialStore = new SnapshotCredentialStore(profile.provider_id, credential);
  const ai = (await loadPiAi()) as {
    createModels: (options?: { credentials?: SnapshotCredentialStore }) => PiModels;
    createProvider: (options: Record<string, unknown>) => PiProvider;
  };
  const openAiCompletions = (await loadPiOpenAiCompletions()) as {
    stream: (...args: unknown[]) => unknown;
    streamSimple: (...args: unknown[]) => unknown;
  };
  const anthropicMessages = (await loadPiAnthropicMessages()) as {
    anthropicMessagesApi: () => {
      stream: (...args: unknown[]) => unknown;
      streamSimple: (...args: unknown[]) => unknown;
    };
  };
  const api = profile.kind === "custom_anthropic_compatible"
    ? "anthropic-messages"
    : "openai-completions";
  // 能力与方言查 pi 内建目录（models.dev 元数据，随 pi 版本更新），不猜
  // 名字：按发现的 model_id 匹配，命中即整体继承 reasoning、thinkingFormat
  // 方言、reasoning_content 回传标志与档位映射（如 deepseek-v4-flash 自带
  // thinkingFormat:"deepseek" 与回传标志——缺失正是"说出声"复发的根因，
  // 审计#24）。目录未收录的模型按"会思考"处理：Pi 的 clampThinkingLevel
  // 对真不支持者收敛；极端端点报参数错时的降级重试留待真实案例出现再加。
  const catalogModels = (await createBuiltinModels(credentialStore)).getModels();
  const catalogHit = (
    catalogModels as Array<
      PiModel & { compat?: Record<string, unknown>; thinkingLevelMap?: unknown }
    >
  ).find((candidate) => candidate.id === profile.model_id);
  const model: PiModel & {
    cost: Record<string, number>;
    compat?: Record<string, unknown>;
    thinkingLevelMap?: unknown;
  } = {
    id: profile.model_id,
    name: catalogHit?.name ?? profile.model_id,
    api,
    provider: profile.provider_id,
    baseUrl: profile.base_url,
    reasoning: catalogHit ? catalogHit.reasoning === true : true,
    input: catalogHit ? [...catalogHit.input] : ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: profile.context_window ?? catalogHit?.contextWindow ?? CUSTOM_PROVIDER_DEFAULT_CONTEXT_WINDOW,
    maxTokens: profile.max_tokens ?? catalogHit?.maxTokens ?? CUSTOM_PROVIDER_DEFAULT_MAX_TOKENS,
    ...(catalogHit?.compat ? { compat: catalogHit.compat } : {}),
    ...(catalogHit?.thinkingLevelMap ? { thinkingLevelMap: catalogHit.thinkingLevelMap } : {}),
  };
  const provider = ai.createProvider({
    id: profile.provider_id,
    name: profile.provider_name,
    baseUrl: profile.base_url,
    auth: {
      apiKey: {
        name: `${profile.provider_name} API key`,
        resolve: async ({ credential: stored }: { credential?: { key?: string } }) =>
          stored?.key
            ? { auth: { apiKey: stored.key }, source: "stored credential" }
            : undefined,
      },
    },
    models: [model],
    api: profile.kind === "custom_anthropic_compatible"
      ? anthropicMessages.anthropicMessagesApi()
      : {
          stream: openAiCompletions.stream,
          streamSimple: openAiCompletions.streamSimple,
        },
  });
  const models = ai.createModels({ credentials: credentialStore });
  models.setProvider(provider);
  const resolved = models.getModel(profile.provider_id, profile.model_id);
  if (!resolved) {
    throw new ProviderProfileError("unknown_model", "Custom provider model construction failed");
  }
  return {
    models,
    model: resolved,
    credentialStore,
    hasRuntimeCredential: true,
  };
}

export async function resolveProviderModel(rawProfile: unknown): Promise<ResolvedProviderModel> {
  const profile = parseProviderProfile(rawProfile);
  return profile.kind === "builtin" ? resolveBuiltinProfile(profile) : resolveCustomProfile(profile);
}

/**
 * 免模型连通探测（点点 0911 两步式向导）：内置档在选定 model 前先打目录
 * base_url 的 /models 验证 Key 与连通。协议按目录首个模型的 api 推断；
 * 运行时未带 api_key 时回落凭据层解析（含环境/系统级凭据），两者皆无则
 * 视为凭据不可用。只读，不落库。
 */
export async function probeBuiltinProvider(
  providerId: string,
  credential: ProviderCredential | undefined,
  timeoutMs: number = CUSTOM_MODEL_DISCOVERY_TIMEOUT_MS,
): Promise<void> {
  const credentialStore = new SnapshotCredentialStore(providerId, credential);
  const models = await createBuiltinModels(credentialStore);
  const provider = models.getProvider(providerId);
  if (!provider) {
    throw new ProviderProfileError("unknown_provider", `Unknown provider: ${providerId}`);
  }
  const firstModel = models.getModels(providerId)[0];
  // 大多数内置 Provider 在目录层带 base_url；个别（如 opencode-go）按模型
  // 携带端点，回落首个模型的 baseUrl 探测。
  const baseUrl = provider.baseUrl ?? firstModel?.baseUrl ?? null;
  if (!baseUrl) {
    throw new ProviderProfileError("invalid_profile", `Provider ${providerId} has no base URL to probe`);
  }
  let apiKey = credential?.type === "api_key" && credential.key ? credential.key : null;
  if (!apiKey) {
    const auth = firstModel ? await models.getAuth(firstModel) : undefined;
    const resolved = auth?.auth?.apiKey;
    apiKey = typeof resolved === "string" && resolved ? resolved : null;
  }
  if (!apiKey) throw new Error("Provider credential is unavailable");
  const protocol = firstModel?.api === "anthropic-messages" ? "anthropic-messages" : "openai-completions";
  try {
    await fetchCustomProviderModels(protocol, baseUrl, apiKey, timeoutMs);
  } catch {
    throw new Error("Provider 端点连接失败，请检查网络与 API Key");
  }
}

/**
 * 免模型连通探测的自定义档变体：协议未知（正是探测要回答的问题），按
 * OpenAI 兼容与 Anthropic 兼容两种协议依次尝试 /models，任一应答即视为
 * 连通（Key 与端点同时得到验证）。端点应答但清单为空也算连通。
 */
export async function probeCustomProvider(
  rawBaseUrl: string,
  apiKey: string,
  timeoutMs: number = CUSTOM_MODEL_DISCOVERY_TIMEOUT_MS,
): Promise<void> {
  let parsed: URL;
  try {
    parsed = new URL(rawBaseUrl.trim());
  } catch {
    throw new ProviderProfileError("invalid_profile", "model.base_url must be a valid HTTP(S) URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new ProviderProfileError("invalid_profile", "model.base_url must be a valid HTTP(S) URL");
  }
  // 协议各自的 base_url 归一（去尾部 /v1 与否）由 fetchCustomProviderModels
  // 内部处理，这里不预裁剪，避免 OpenAI 兼容端点丢掉 /v1 前缀。
  for (const protocol of ["openai-completions", "anthropic-messages"] as const) {
    try {
      await fetchCustomProviderModels(protocol, rawBaseUrl, apiKey, timeoutMs);
      return;
    } catch {
      // 换下一种协议重试；两种都失败才按连通失败上报。
    }
  }
  throw new Error("Provider 端点连接失败，请检查 Base URL 与 API Key");
}

export function createModelsStreamFn(models: PiModels): StreamFn {
  return (model, context, options) =>
    models.streamSimple(model as PiModel, context, options);
}
