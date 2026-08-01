import {
  PROVIDER_CATALOG_SCHEMA,
  type CoachRuntimeProviderProfile,
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
  const model: PiModel & { cost: Record<string, number> } = {
    id: profile.model_id,
    name: profile.model_id,
    api,
    provider: profile.provider_id,
    baseUrl: profile.base_url,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 8192,
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
