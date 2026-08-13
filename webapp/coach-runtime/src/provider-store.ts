/**
 * Coach-owned Provider profile persistence.
 *
 * The desktop product has exactly one owner and one selected Provider, so the
 * config document is a single `config/provider.json` with a `{ profile }` body:
 *
 *   { "profile": { "kind": "builtin", "provider_id": "opencode-go",
 *                  "model_id": "deepseek-v4-flash",
 *                  "credential": { "type": "api_key", "key": "..." } } }
 *
 * `loadProfile` also understands the legacy Python multi-profile document
 * (`{ next_id, profiles, credentials }`) so a pre-existing on-disk config keeps
 * working until the user re-saves through the Coach UI.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { isRecord, type CoachRuntimeProviderProfile, type ProviderCredential } from "./contracts.ts";
import { getConfigDir } from "./app-data.ts";

const PROVIDER_FILE = "provider.json";

export function providerConfigPath(): string {
  return join(getConfigDir(), PROVIDER_FILE);
}

function isProviderProfile(value: unknown): value is CoachRuntimeProviderProfile {
  return (
    isRecord(value)
    && (value.kind === "builtin"
      || value.kind === "custom_openai_compatible"
      || value.kind === "custom_anthropic_compatible")
  );
}

function legacyCredential(
  profile: Record<string, unknown>,
  credentials: Record<string, unknown> | undefined,
): { reauth: boolean; credential: ProviderCredential | undefined } {
  const record = isRecord(credentials) ? credentials[String(profile.id)] : undefined;
  if (isRecord(record) && record.needs_reauth === true) {
    return { reauth: true, credential: undefined };
  }
  let raw: unknown = profile.credential;
  if (isRecord(record)) {
    const encoded = record.credential_json;
    try {
      raw = typeof encoded === "string" ? JSON.parse(encoded) : encoded;
    } catch {
      raw = undefined;
    }
  }
  if (!isRecord(raw) || (raw.type !== "api_key" && raw.type !== "oauth")) {
    return { reauth: false, credential: undefined };
  }
  return { reauth: false, credential: raw as ProviderCredential };
}

function migrateLegacyProfile(
  profile: Record<string, unknown>,
  credentials: Record<string, unknown> | undefined,
): CoachRuntimeProviderProfile | null {
  const kind = profile.kind;
  const providerId = typeof profile.provider_id === "string" ? profile.provider_id.trim() : "";
  const modelId = typeof profile.model_id === "string" ? profile.model_id.trim() : "";
  if (kind !== "builtin" && kind !== "custom_openai_compatible" && kind !== "custom_anthropic_compatible") return null;
  if (!providerId || !modelId) return null;

  const { reauth, credential } = legacyCredential(profile, credentials);
  if (reauth) return null;
  if (kind === "builtin") {
    return {
      kind,
      provider_id: providerId,
      model_id: modelId,
      ...(credential ? { credential } : {}),
    };
  }

  const providerName = typeof profile.name === "string" && profile.name.trim()
    ? profile.name.trim()
    : providerId;
  const baseUrl = typeof profile.base_url === "string" ? profile.base_url.trim() : "";
  const contextWindow = typeof profile.context_window === "number" ? profile.context_window : 0;
  const maxTokens = typeof profile.max_tokens === "number" ? profile.max_tokens : 0;
  if (!baseUrl || !credential || credential.type !== "api_key") return null;
  if (!Number.isSafeInteger(contextWindow) || contextWindow <= 0) return null;
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0) return null;
  return {
    kind,
    provider_id: providerId,
    provider_name: providerName,
    base_url: baseUrl,
    model_id: modelId,
    context_window: contextWindow,
    max_tokens: maxTokens,
    credential,
  };
}

export function loadProfile(): CoachRuntimeProviderProfile | null {
  const path = providerConfigPath();
  if (!existsSync(path)) return null;
  try {
    const config = JSON.parse(readFileSync(path, "utf8"));
    if (!isRecord(config)) return null;

    // Coach single-profile document.
    if (isRecord(config.profile) && isProviderProfile(config.profile)) {
      return config.profile;
    }

    // Legacy Python multi-profile document.
    if (Array.isArray(config.profiles)) {
      const list = config.profiles.filter(isRecord);
      const selected = list.find((entry) => entry.is_default === true) ?? list[0];
      if (selected) return migrateLegacyProfile(selected, config.credentials);
      return null;
    }

    // Bare profile object (the document is the profile itself).
    return isProviderProfile(config) ? config : null;
  } catch {
    return null;
  }
}

export function saveProfile(profile: CoachRuntimeProviderProfile): void {
  const dir = getConfigDir();
  mkdirSync(dir, { recursive: true });
  const path = join(dir, PROVIDER_FILE);
  const tmpPath = join(dir, `.${PROVIDER_FILE}.tmp`);
  writeFileSync(tmpPath, JSON.stringify({ profile }, null, 2), "utf8");
  renameSync(tmpPath, path);
}

export function deleteProfile(): boolean {
  const path = providerConfigPath();
  if (!existsSync(path)) return false;
  rmSync(path, { force: true });
  return true;
}
