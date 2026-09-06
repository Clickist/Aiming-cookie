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
  return all.builtinModels({ credentials });
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
  return {
    schema_version: PROVIDER_CATALOG_SCHEMA,
    providers: models.getProviders().map((provider) => ({
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

export function createModelsStreamFn(models: PiModels): StreamFn {
  return (model, context, options) =>
    models.streamSimple(model as PiModel, context, options);
}
