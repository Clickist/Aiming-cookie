import { loadPiOpenAiCompletions } from "./pi-source.ts";
import type { CoachRuntimeModelConfig } from "./contracts.ts";

export type StreamFn = (
  model: unknown,
  context: unknown,
  options?: Record<string, unknown>,
) => Promise<unknown> | unknown;

/** Compatibility-only model builder for the legacy v0 caller. Production turns resolve a Pi model. */
export function buildCoachModel(config: CoachRuntimeModelConfig) {
  return {
    id: config.model_id,
    name: config.model_id,
    api: "openai-completions",
    provider: "openai",
    baseUrl: config.base_url.replace(/\/$/, ""),
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 8192,
  };
}

/** Compatibility-only direct Pi API stream. Production uses Models.streamSimple. */
export async function createOpenAiCompatibleStreamFn(): Promise<StreamFn> {
  const ai = (await loadPiOpenAiCompletions()) as { streamSimple: StreamFn };
  return ai.streamSimple;
}

/** Compatibility-only environment resolver for callers still using the old v0 shape. */
export function createApiKeyResolver(apiKeyEnv: string) {
  return (_provider: string) => {
    const value = process.env[apiKeyEnv];
    return value && value.length > 0 ? value : undefined;
  };
}
