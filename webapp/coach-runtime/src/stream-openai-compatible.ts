import { loadPiAi } from "./pi-source.ts";
import type { CoachRuntimeModelConfig } from "./contracts.ts";

export type StreamFn = (
  model: unknown,
  context: unknown,
  options?: Record<string, unknown>,
) => Promise<unknown> | unknown;

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

/** Production StreamFn: Pi openai-completions streaming via env API key. */
export async function createOpenAiCompatibleStreamFn(): Promise<StreamFn> {
  const ai = await loadPiAi();
  const streamSimple = ai.streamSimple as StreamFn;
  return streamSimple;
}

export function createApiKeyResolver(apiKeyEnv: string) {
  return (_provider: string) => {
    const value = process.env[apiKeyEnv];
    return value && value.length > 0 ? value : undefined;
  };
}