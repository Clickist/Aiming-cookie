/**
 * Coach-owned Provider profile persistence (multi-profile).
 *
 * `config/provider.json` stores a v2 multi-profile document:
 *
 *   {
 *     "schema_version": 2,
 *     "active_id": 1,
 *     "next_id": 2,
 *     "profiles": [
 *       { "id": 1, "kind": "builtin", "provider_id": "opencode-go",
 *         "model_id": "deepseek-v4-flash",
 *         "credential": { "type": "api_key", "key": "..." } }
 *     ]
 *   }
 *
 * `active_id` selects the profile Coach turns resolve against. Reads also
 * understand the older shapes — the v1 single-profile document (`{ profile }`),
 * the legacy Python multi-profile document (`{ next_id, profiles, credentials }`)
 * and a bare profile object — and migrate them in memory; the v2 format is
 * written on the next save.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { isRecord, type CoachRuntimeProviderProfile, type ProviderCredential } from "./contracts.ts";
import { getConfigDir } from "./app-data.ts";

const PROVIDER_FILE = "provider.json";
const STORE_SCHEMA_VERSION = 2;

/** Stable id given to the single migrated v1 profile (and the first created one). */
export const DEFAULT_PROFILE_ID = 1;

/** A stored profile: the runtime profile plus its stable unique id. */
export type StoredProviderProfile = { id: number } & CoachRuntimeProviderProfile;

export type ProviderProfileStore = {
  schema_version: typeof STORE_SCHEMA_VERSION;
  active_id: number | null;
  next_id: number;
  profiles: StoredProviderProfile[];
};

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

function isProfileId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function emptyStore(): ProviderProfileStore {
  return { schema_version: STORE_SCHEMA_VERSION, active_id: null, next_id: DEFAULT_PROFILE_ID, profiles: [] };
}

function normalizeNextId(...candidates: unknown[]): number {
  let next = DEFAULT_PROFILE_ID;
  for (const candidate of candidates) {
    if (isProfileId(candidate)) next = Math.max(next, candidate);
  }
  return next;
}

function stripStoredId(entry: StoredProviderProfile): CoachRuntimeProviderProfile {
  const { id: _id, ...profile } = entry;
  return profile;
}

export function findStoredProfile(
  store: ProviderProfileStore,
  id: number,
): StoredProviderProfile | undefined {
  return store.profiles.find((entry) => entry.id === id);
}

/** Point `active_id` at a stored profile; returns false when the id is unknown. */
export function setActiveProfileId(store: ProviderProfileStore, id: number): boolean {
  if (!findStoredProfile(store, id)) return false;
  store.active_id = id;
  return true;
}

/** Delete one stored profile; deleting the active one promotes the first remaining. */
export function deleteProfileById(store: ProviderProfileStore, id: number): boolean {
  if (!findStoredProfile(store, id)) return false;
  store.profiles = store.profiles.filter((entry) => entry.id !== id);
  if (store.active_id === id) store.active_id = store.profiles[0]?.id ?? null;
  return true;
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

function parseV2Document(config: Record<string, unknown>): ProviderProfileStore | null {
  if (config.schema_version !== STORE_SCHEMA_VERSION || !Array.isArray(config.profiles)) return null;
  const store = emptyStore();
  const seen = new Set<number>();
  for (const entry of config.profiles) {
    if (!isRecord(entry)) continue;
    const id = entry.id;
    if (!isProfileId(id) || !isProviderProfile(entry)) continue;
    if (seen.has(id)) continue;
    seen.add(id);
    store.profiles.push({ id, ...entry });
  }
  store.active_id = isProfileId(config.active_id) && seen.has(config.active_id) ? config.active_id : null;
  const maxId = store.profiles.reduce((max, entry) => Math.max(max, entry.id), 0);
  store.next_id = normalizeNextId(config.next_id, maxId + 1);
  return store;
}

function parseSingleProfileDocument(profile: CoachRuntimeProviderProfile): ProviderProfileStore {
  return {
    schema_version: STORE_SCHEMA_VERSION,
    active_id: DEFAULT_PROFILE_ID,
    next_id: DEFAULT_PROFILE_ID + 1,
    profiles: [{ id: DEFAULT_PROFILE_ID, ...profile }],
  };
}

function parseLegacyPythonDocument(config: Record<string, unknown>): ProviderProfileStore | null {
  if (!Array.isArray(config.profiles)) return null;
  const credentials = isRecord(config.credentials) ? config.credentials : undefined;
  const store = emptyStore();
  let defaultId: number | null = null;
  for (const entry of config.profiles) {
    if (!isRecord(entry) || !isProfileId(entry.id)) continue;
    const migrated = migrateLegacyProfile(entry, credentials);
    if (!migrated || findStoredProfile(store, entry.id)) continue;
    if (entry.is_default === true && defaultId === null) defaultId = entry.id;
    store.profiles.push({ id: entry.id, ...migrated });
  }
  store.active_id = defaultId !== null && findStoredProfile(store, defaultId)
    ? defaultId
    : (store.profiles[0]?.id ?? null);
  const maxId = store.profiles.reduce((max, entry) => Math.max(max, entry.id), 0);
  store.next_id = normalizeNextId(config.next_id, maxId + 1);
  return store;
}

function parseStoreDocument(config: unknown): ProviderProfileStore {
  if (!isRecord(config)) return emptyStore();
  return parseV2Document(config)
    ?? (isRecord(config.profile) && isProviderProfile(config.profile)
      ? parseSingleProfileDocument(config.profile)
      : null)
    ?? parseLegacyPythonDocument(config)
    ?? (isProviderProfile(config) ? parseSingleProfileDocument(config) : null)
    ?? emptyStore();
}

/** Read the multi-profile store; missing or invalid documents read as empty. */
export function loadProviderStore(): ProviderProfileStore {
  const path = providerConfigPath();
  if (!existsSync(path)) return emptyStore();
  try {
    return parseStoreDocument(JSON.parse(readFileSync(path, "utf8")));
  } catch {
    return emptyStore();
  }
}

/** Atomically persist the store in the v2 multi-profile format. */
export function saveProviderStore(store: ProviderProfileStore): void {
  const dir = getConfigDir();
  mkdirSync(dir, { recursive: true });
  const path = join(dir, PROVIDER_FILE);
  const tmpPath = join(dir, `.${PROVIDER_FILE}.tmp`);
  writeFileSync(tmpPath, JSON.stringify({
    schema_version: store.schema_version,
    active_id: store.active_id,
    next_id: store.next_id,
    profiles: store.profiles,
  }, null, 2), "utf8");
  renameSync(tmpPath, path);
}

/** The active profile Coach turns resolve against (stored id stripped). */
export function loadProfile(): CoachRuntimeProviderProfile | null {
  const store = loadProviderStore();
  const entry = store.active_id !== null ? findStoredProfile(store, store.active_id) : undefined;
  return entry ? stripStoredId(entry) : null;
}

/**
 * Legacy single-profile shim: replace the active profile in place (keeping its
 * id and active flag), or create it as the single active profile when none
 * exists. Other stored profiles are left untouched.
 */
export function saveProfile(profile: CoachRuntimeProviderProfile): void {
  const store = loadProviderStore();
  const active = store.active_id !== null ? findStoredProfile(store, store.active_id) : undefined;
  if (active) {
    store.profiles = store.profiles.map((entry) => (entry.id === active.id ? { id: active.id, ...profile } : entry));
  } else {
    const entry: StoredProviderProfile = { id: store.next_id, ...profile };
    store.profiles.push(entry);
    store.next_id = Math.max(store.next_id, entry.id + 1);
    store.active_id = entry.id;
  }
  saveProviderStore(store);
}
