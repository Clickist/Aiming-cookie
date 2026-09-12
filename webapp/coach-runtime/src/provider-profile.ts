import {
  PROVIDER_PROFILE_STATUS_SCHEMA,
  isRecord,
  makeError,
  type CoachReasoningEffort,
  type CoachRuntimeProviderProfile,
  type ProviderCredential,
  type ProviderProfileStatusResponse,
} from "./contracts.ts";
import { parseProviderCredential, ProviderAuthRequestError } from "./provider-auth.ts";

export type ProviderProfileErrorCode =
  | "invalid_profile"
  | "unknown_provider"
  | "unknown_model";

export class ProviderProfileError extends Error {
  readonly code: ProviderProfileErrorCode;

  constructor(code: ProviderProfileErrorCode, message: string) {
    super(message);
    this.name = "ProviderProfileError";
    this.code = code;
  }
}

function requiredString(raw: Record<string, unknown>, field: string): string {
  const value = raw[field];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new ProviderProfileError("invalid_profile", `model.${field} must be a non-empty string`);
  }
  return value.trim();
}

function optionalPositiveInteger(raw: Record<string, unknown>, field: string): number | undefined {
  const value = raw[field];
  if (value === undefined) return undefined;
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new ProviderProfileError("invalid_profile", `model.${field} must be a positive integer when supplied`);
  }
  return value;
}

const REASONING_EFFORT_VALUES: readonly CoachReasoningEffort[] = ["minimal", "low", "medium", "high", "off"];

function optionalReasoningEffort(raw: Record<string, unknown>): CoachReasoningEffort | undefined {
  const value = raw.reasoning_effort;
  if (value === undefined) return undefined;
  if (!REASONING_EFFORT_VALUES.includes(value as CoachReasoningEffort)) {
    throw new ProviderProfileError(
      "invalid_profile",
      "model.reasoning_effort must be one of minimal|low|medium|high|off when supplied",
    );
  }
  return value as CoachReasoningEffort;
}

/** 内置档的用户显示名：缺省/空白都按未命名处理，非字符串按非法输入 400。 */
function optionalDisplayName(raw: Record<string, unknown>): string | undefined {
  if (raw.name === undefined) return undefined;
  if (typeof raw.name !== "string") {
    throw new ProviderProfileError("invalid_profile", "model.name must be a string when supplied");
  }
  const trimmed = raw.name.trim();
  return trimmed ? trimmed : undefined;
}

function normalizeHttpBaseUrl(value: string, kind?: unknown): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new ProviderProfileError("invalid_profile", "model.base_url must be a valid HTTP(S) URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new ProviderProfileError("invalid_profile", "model.base_url must be a valid HTTP(S) URL");
  }
  const normalized = value.replace(/\/+$/, "");
  return kind === "custom_anthropic_compatible" && normalized.endsWith("/v1")
    ? normalized.slice(0, -3)
    : normalized;
}

function parseRuntimeCredential(
  raw: Record<string, unknown>,
  expectedType?: ProviderCredential["type"],
): ProviderCredential | undefined {
  if (raw.credential !== undefined && raw.api_key !== undefined) {
    throw new ProviderProfileError(
      "invalid_profile",
      "model.credential and migration model.api_key cannot both be supplied",
    );
  }
  if (raw.credential !== undefined) {
    try {
      return parseProviderCredential(raw.credential, expectedType);
    } catch (error) {
      if (error instanceof ProviderAuthRequestError) {
        throw new ProviderProfileError("invalid_profile", `model.${error.message}`);
      }
      throw error;
    }
  }
  if (raw.api_key !== undefined) {
    if (typeof raw.api_key !== "string" || raw.api_key.length === 0) {
      throw new ProviderProfileError(
        "invalid_profile",
        "model.api_key must be a non-empty string when supplied",
      );
    }
    if (expectedType && expectedType !== "api_key") {
      throw new ProviderProfileError("invalid_profile", `model credential must be ${expectedType}`);
    }
    return { type: "api_key", key: raw.api_key };
  }
  return undefined;
}

export function parseProviderProfile(raw: unknown): CoachRuntimeProviderProfile {
  if (!isRecord(raw)) {
    throw new ProviderProfileError("invalid_profile", "model profile must be a JSON object");
  }
  if ("api_key_env" in raw) {
    throw new ProviderProfileError(
      "invalid_profile",
      "environment-variable credential indirection is not accepted by the v1 model profile",
    );
  }

  if (raw.kind === "builtin") {
    const credential = parseRuntimeCredential(raw);
    const reasoningEffort = optionalReasoningEffort(raw);
    const name = optionalDisplayName(raw);
    return {
      kind: "builtin",
      ...(name !== undefined ? { name } : {}),
      provider_id: requiredString(raw, "provider_id"),
      model_id: requiredString(raw, "model_id"),
      ...(reasoningEffort !== undefined ? { reasoning_effort: reasoningEffort } : {}),
      ...(credential ? { credential } : {}),
    };
  }

  if (raw.kind === "custom_openai_compatible" || raw.kind === "custom_anthropic_compatible") {
    const credential = parseRuntimeCredential(raw, "api_key");
    if (!credential || credential.type !== "api_key" || !credential.key) {
      throw new ProviderProfileError(
        "invalid_profile",
        "model custom provider requires an api_key credential",
      );
    }
    const providerName = requiredString(raw, "provider_name");
    const providerId =
      raw.provider_id === undefined ? providerName : requiredString(raw, "provider_id");
    const contextWindow = optionalPositiveInteger(raw, "context_window");
    const maxTokens = optionalPositiveInteger(raw, "max_tokens");
    const reasoningEffort = optionalReasoningEffort(raw);
    return {
      kind: raw.kind,
      provider_id: providerId,
      provider_name: providerName,
      base_url: normalizeHttpBaseUrl(requiredString(raw, "base_url"), raw.kind),
      credential,
      model_id: requiredString(raw, "model_id"),
      ...(reasoningEffort !== undefined ? { reasoning_effort: reasoningEffort } : {}),
      ...(contextWindow !== undefined ? { context_window: contextWindow } : {}),
      ...(maxTokens !== undefined ? { max_tokens: maxTokens } : {}),
      // 模型发现存档（点点 0912 拍板）：逐项过滤，坏项直接丢弃不 400。
      ...(Array.isArray(raw.discovered_models)
        ? {
            discovered_models: (raw.discovered_models as unknown[])
              .filter((item) => typeof item === "object" && item !== null && typeof (item as { model_id?: unknown }).model_id === "string")
              .map((item) => {
                const record = item as { model_id: string; context_window?: unknown; max_tokens?: unknown };
                return {
                  model_id: record.model_id,
                  context_window: typeof record.context_window === "number" ? record.context_window : null,
                  max_tokens: typeof record.max_tokens === "number" ? record.max_tokens : null,
                };
              }),
          }
        : {}),
    };
  }

  throw new ProviderProfileError("invalid_profile", "model.kind must select a supported provider profile kind");
}

export function sanitizeProviderProfile(profile: CoachRuntimeProviderProfile): Record<string, unknown> {
  if (profile.kind === "builtin") {
    return {
      kind: profile.kind,
      ...(profile.name !== undefined ? { name: profile.name } : {}),
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
    };
  }
  return {
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
    ...(profile.context_window !== undefined ? { context_window: profile.context_window } : {}),
    ...(profile.max_tokens !== undefined ? { max_tokens: profile.max_tokens } : {}),
  };
}

function collectCredentialStrings(value: unknown, secrets: string[]): void {
  if (typeof value === "string") {
    if (value.length > 0) secrets.push(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectCredentialStrings(item, secrets);
    return;
  }
  if (isRecord(value)) {
    for (const item of Object.values(value)) collectCredentialStrings(item, secrets);
  }
}

export function extractRuntimeSecrets(raw: unknown): string[] {
  if (!isRecord(raw)) return [];
  const model = isRecord(raw.model) ? raw.model : isRecord(raw.profile) ? raw.profile : undefined;
  if (!model) return [];

  const secrets: string[] = [];
  if (typeof model.api_key === "string" && model.api_key.length > 0) {
    secrets.push(model.api_key);
  }
  if (model.credential !== undefined) collectCredentialStrings(model.credential, secrets);
  return secrets;
}

export function redactRuntimeSecrets(message: string, secrets: string[]): string {
  let redacted = message;
  for (const secret of secrets) {
    if (secret.length > 0) redacted = redacted.split(secret).join("[REDACTED]");
  }
  return redacted;
}

type ResolvedProfile = Awaited<ReturnType<typeof import("./provider-models.ts")["resolveProviderModel"]>>;
type ResolveProviderModel = (profile: unknown) => Promise<ResolvedProfile>;

type ProfileOperationOptions = {
  resolveProviderModel?: ResolveProviderModel;
};

function piAuthErrorCode(error: unknown): "auth" | "oauth" | null {
  if (!(error instanceof Error) || error.name !== "ModelsError" || !isRecord(error)) {
    return null;
  }
  return error.code === "auth" || error.code === "oauth" ? error.code : null;
}

async function resolveProfile(
  profile: CoachRuntimeProviderProfile,
  options: ProfileOperationOptions,
): Promise<ResolvedProfile> {
  const resolve =
    options.resolveProviderModel ??
    (await import("./provider-models.ts")).resolveProviderModel;
  return resolve(profile);
}

export async function getProviderProfileStatus(
  rawProfile: unknown,
  options: ProfileOperationOptions = {},
): Promise<ProviderProfileStatusResponse> {
  const secrets = extractRuntimeSecrets({ profile: rawProfile });
  let profile: CoachRuntimeProviderProfile | null = null;
  let model: ProviderProfileStatusResponse["model"] = null;
  try {
    profile = parseProviderProfile(rawProfile);
    const { toCatalogModel } = await import("./provider-models.ts");
    const resolved = await resolveProfile(profile, options);
    model = toCatalogModel(resolved.model);
    if (profile.credential?.type === "oauth" && Date.now() >= profile.credential.expires) {
      return {
        schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
        ok: true,
        status: "auth_expired",
        profile: sanitizeProviderProfile(profile),
        model,
        credential_source: "runtime_profile",
        error: null,
      };
    }
    const auth = await resolved.models.getAuth(resolved.model);
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: true,
      status: auth ? "ready" : "unconfigured",
      profile: sanitizeProviderProfile(profile),
      model,
      credential_source: auth
        ? resolved.hasRuntimeCredential
          ? "runtime_profile"
          : auth.source ?? null
        : null,
      error: null,
    };
  } catch (error) {
    const authCode = piAuthErrorCode(error);
    if (authCode) {
      return {
        schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
        ok: false,
        status: "needs_reauth",
        profile: profile ? sanitizeProviderProfile(profile) : null,
        model,
        credential_source: null,
        error: makeError({
          category: "provider_auth",
          code: authCode,
          message: redactRuntimeSecrets(
            error instanceof Error ? error.message : String(error),
            secrets,
          ),
          retryable: false,
        }),
      };
    }
    const code = error instanceof ProviderProfileError ? error.code : "profile_status_failed";
    const status = code === "unknown_provider" || code === "unknown_model" ? "model_unavailable" : "unconfigured";
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: false,
      status,
      profile: null,
      model: null,
      credential_source: null,
      error: makeError({
        category: "provider_profile",
        code,
        message: redactRuntimeSecrets(error instanceof Error ? error.message : String(error), secrets),
        retryable: false,
      }),
    };
  }
}

const CONNECTION_TEST_TIMEOUT_MS = 30_000;

type ConnectionTestOptions = ProfileOperationOptions & {
  timeoutMs?: number;
};

function connectionTimeout(timeoutMs: number | undefined): number {
  if (timeoutMs === undefined) return CONNECTION_TEST_TIMEOUT_MS;
  return Math.max(1, Math.min(timeoutMs, CONNECTION_TEST_TIMEOUT_MS));
}

export async function testProviderConnection(
  rawProfile: unknown,
  options: ConnectionTestOptions = {},
): Promise<ProviderProfileStatusResponse> {
  const secrets = extractRuntimeSecrets({ profile: rawProfile });
  let profile: CoachRuntimeProviderProfile | undefined;
  const controller = new AbortController();
  const timeoutMs = connectionTimeout(options.timeoutMs);
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    profile = parseProviderProfile(rawProfile);
    const { toCatalogModel } = await import("./provider-models.ts");
    const resolved = await resolveProfile(profile, options);
    const timeoutResult = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(new Error("Provider connection test timed out"));
      }, timeoutMs);
      timeout.unref?.();
    });
    const auth = await resolved.models.getAuth(resolved.model);
    if (!auth) throw new Error("Provider credential is unavailable");
    const result = resolved.models
      .streamSimple(
        resolved.model,
        {
          messages: [{ role: "user", content: "Reply with OK.", timestamp: Date.now() }],
        },
        { signal: controller.signal },
      )
      .result();
    const message = await Promise.race([result, timeoutResult]);
    if (message.stopReason === "error" || message.stopReason === "aborted") {
      throw new Error("Provider connection test failed");
    }
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: true,
      status: "ready",
      profile: sanitizeProviderProfile(profile),
      model: toCatalogModel(resolved.model),
      credential_source: resolved.hasRuntimeCredential ? "runtime_profile" : auth.source ?? null,
      error: null,
    };
  } catch (error) {
    const code = error instanceof ProviderProfileError ? error.code : "connection_failed";
    const status =
      code === "unknown_provider" || code === "unknown_model"
        ? "model_unavailable"
        : code === "invalid_profile"
          ? "unconfigured"
          : "connection_failed";
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: false,
      status,
      profile: profile ? sanitizeProviderProfile(profile) : null,
      model: null,
      credential_source: null,
      error: makeError({
        category: "provider_connection",
        code,
        message: redactRuntimeSecrets(
          error instanceof ProviderProfileError ? error.message : "Provider connection test failed",
          secrets,
        ),
        retryable: status === "connection_failed",
      }),
    };
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

/**
 * 免模型的连通性探测（点点 0911 两步式向导拍板）：候选档尚未选定 model 时，
 * 内置档验证 Provider 目录存在性、凭据与端点连通；自定义档验证 Base URL 与
 * Key 并顺带探测协议。与 testProviderConnection 同构的状态投影，只读干跑，
 * 零持久化；选定模型后的完整干跑仍走 testProviderConnection。
 */
export async function probeProviderConnection(
  rawProfile: unknown,
  options: ConnectionTestOptions = {},
): Promise<ProviderProfileStatusResponse> {
  const secrets = extractRuntimeSecrets({ profile: rawProfile });
  const timeoutMs = connectionTimeout(options.timeoutMs);
  try {
    if (!isRecord(rawProfile)) {
      throw new ProviderProfileError("invalid_profile", "model profile must be a JSON object");
    }
    const { probeBuiltinProvider, probeCustomProvider } = await import("./provider-models.ts");
    if (rawProfile.kind === "builtin") {
      const providerId = typeof rawProfile.provider_id === "string" ? rawProfile.provider_id.trim() : "";
      if (!providerId) {
        throw new ProviderProfileError("invalid_profile", "model.provider_id must be a non-empty string");
      }
      await probeBuiltinProvider(providerId, parseRuntimeCredential(rawProfile), timeoutMs);
    } else if (
      rawProfile.kind === "custom_openai_compatible"
      || rawProfile.kind === "custom_anthropic_compatible"
    ) {
      const credential = parseRuntimeCredential(rawProfile, "api_key");
      const apiKey = credential?.type === "api_key" && credential.key ? credential.key : "";
      if (!apiKey) {
        throw new ProviderProfileError(
          "invalid_profile",
          "model custom provider requires an api_key credential",
        );
      }
      const baseUrl = typeof rawProfile.base_url === "string" ? rawProfile.base_url : "";
      await probeCustomProvider(baseUrl, apiKey, timeoutMs);
    } else {
      throw new ProviderProfileError(
        "invalid_profile",
        "model.kind must select a supported provider profile kind",
      );
    }
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: true,
      status: "ready",
      profile: null,
      model: null,
      credential_source: null,
      error: null,
    };
  } catch (error) {
    const code = error instanceof ProviderProfileError ? error.code : "connection_failed";
    const status =
      code === "unknown_provider" || code === "unknown_model"
        ? "model_unavailable"
        : code === "invalid_profile"
          ? "unconfigured"
          : "connection_failed";
    return {
      schema_version: PROVIDER_PROFILE_STATUS_SCHEMA,
      ok: false,
      status,
      profile: null,
      model: null,
      credential_source: null,
      error: makeError({
        category: "provider_connection",
        code,
        message: redactRuntimeSecrets(
          error instanceof Error ? error.message : "Provider connection probe failed",
          secrets,
        ),
        retryable: status === "connection_failed",
      }),
    };
  }
}
