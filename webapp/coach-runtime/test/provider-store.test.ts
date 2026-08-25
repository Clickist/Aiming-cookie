import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-store-"));
process.env.DATA_ROOT = dataRoot;

import {
  DEFAULT_PROFILE_ID,
  deleteProfileById,
  loadProfile,
  loadProviderStore,
  providerConfigPath,
  saveProfile,
  saveProviderStore,
  setActiveProfileId,
} from "../src/provider-store.ts";

const BUILTIN_PROFILE = {
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "stored-secret" },
} as const;

function writeRawDocument(document: unknown): void {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(providerConfigPath(), JSON.stringify(document), "utf8");
}

test("saveProviderStore writes the v2 multi-profile document and loads it back", () => {
  writeRawDocument({ none: true });
  saveProviderStore({
    schema_version: 2,
    active_id: 2,
    next_id: 3,
    profiles: [
      { id: 1, ...BUILTIN_PROFILE },
      { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
    ],
  });

  const store = loadProviderStore();
  assert.equal(store.schema_version, 2);
  assert.equal(store.active_id, 2);
  assert.equal(store.next_id, 3);
  assert.deepEqual(store.profiles, [
    { id: 1, ...BUILTIN_PROFILE },
    { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
  ]);

  const doc = JSON.parse(readFileSync(providerConfigPath(), "utf8"));
  assert.equal(doc.schema_version, 2);
  assert.equal(doc.active_id, 2);
  assert.equal(doc.profiles.length, 2);
});

test("saveProfile creates the first profile as id 1 active via the legacy shim", () => {
  writeRawDocument({ none: true });
  saveProfile(BUILTIN_PROFILE);
  const store = loadProviderStore();
  assert.deepEqual(store, {
    schema_version: 2,
    active_id: DEFAULT_PROFILE_ID,
    next_id: DEFAULT_PROFILE_ID + 1,
    profiles: [{ id: DEFAULT_PROFILE_ID, ...BUILTIN_PROFILE }],
  });
  const profile = loadProfile();
  assert.ok(profile);
  assert.equal("id" in profile, false);
  assert.equal(profile.kind, "builtin");
  assert.equal(profile.model_id, "deepseek-v4-flash");
  assert.deepEqual(profile.credential, { type: "api_key", key: "stored-secret" });
});

test("saveProfile replaces the active profile in place and keeps other profiles", () => {
  writeRawDocument({
    schema_version: 2,
    active_id: 2,
    next_id: 3,
    profiles: [
      { id: 1, kind: "builtin", provider_id: "opencode-go", model_id: "deepseek-v4-flash" },
      { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
    ],
  });

  saveProfile({ kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v4-pro" });
  const store = loadProviderStore();
  assert.equal(store.active_id, 2);
  assert.deepEqual(store.profiles, [
    { id: 1, kind: "builtin", provider_id: "opencode-go", model_id: "deepseek-v4-flash" },
    { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v4-pro" },
  ]);
  assert.equal(loadProfile()?.model_id, "deepseek-v4-pro");
});

test("setActiveProfileId and deleteProfileById operate on stored ids", () => {
  writeRawDocument({
    schema_version: 2,
    active_id: 1,
    next_id: 3,
    profiles: [
      { id: 1, kind: "builtin", provider_id: "opencode-go", model_id: "deepseek-v4-flash" },
      { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
    ],
  });
  const store = loadProviderStore();

  assert.equal(setActiveProfileId(store, 2), true);
  assert.equal(store.active_id, 2);
  assert.equal(setActiveProfileId(store, 99), false);
  assert.equal(store.active_id, 2);

  assert.equal(deleteProfileById(store, 99), false);
  // Deleting a non-active profile leaves the active selection alone.
  assert.equal(deleteProfileById(store, 1), true);
  assert.equal(store.active_id, 2);
  assert.deepEqual(store.profiles, [
    { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
  ]);
  // Deleting the last (active) profile clears the active selection.
  assert.equal(deleteProfileById(store, 2), true);
  assert.deepEqual(store, {
    schema_version: 2,
    active_id: null,
    next_id: 3,
    profiles: [],
  });
});

test("deleteProfileById on the active profile promotes the first remaining one", () => {
  writeRawDocument({
    schema_version: 2,
    active_id: 1,
    next_id: 4,
    profiles: [
      { id: 1, kind: "builtin", provider_id: "opencode-go", model_id: "deepseek-v4-flash" },
      { id: 2, kind: "builtin", provider_id: "deepseek", model_id: "deepseek-v3" },
      { id: 3, kind: "builtin", provider_id: "anthropic", model_id: "claude-x" },
    ],
  });
  const store = loadProviderStore();
  assert.equal(deleteProfileById(store, 3), true);
  assert.equal(store.active_id, 1);
  assert.equal(deleteProfileById(store, 1), true);
  assert.equal(store.active_id, 2);
  saveProviderStore(store);
  assert.equal(loadProfile()?.model_id, "deepseek-v3");
});

test("loadProviderStore returns an empty store when the document is missing", () => {
  writeRawDocument({ none: true });
  const store = loadProviderStore();
  assert.deepEqual(store, { schema_version: 2, active_id: null, next_id: 1, profiles: [] });
  assert.equal(loadProfile(), null);
});

test("loadProviderStore migrates the v1 { profile } document to id 1 active", () => {
  writeRawDocument({ profile: BUILTIN_PROFILE });
  const store = loadProviderStore();
  assert.deepEqual(store, {
    schema_version: 2,
    active_id: DEFAULT_PROFILE_ID,
    next_id: DEFAULT_PROFILE_ID + 1,
    profiles: [{ id: DEFAULT_PROFILE_ID, ...BUILTIN_PROFILE }],
  });
  // Migration is in memory; the next save persists the v2 format.
  saveProviderStore(store);
  const doc = JSON.parse(readFileSync(providerConfigPath(), "utf8"));
  assert.equal(doc.schema_version, 2);
  assert.equal(doc.active_id, 1);
  assert.equal(doc.profiles.length, 1);
});

test("loadProviderStore migrates a legacy Python multi-profile document", () => {
  writeRawDocument({
    next_id: 3,
    profiles: [
      { id: 1, name: "Non-default", provider_id: "deepseek", kind: "builtin", base_url: null, model_id: "deepseek-v3", context_window: null, max_tokens: null, is_default: false, created_at: "x", updated_at: "x" },
      { id: 2, name: "Default", provider_id: "opencode-go", kind: "builtin", base_url: null, model_id: "deepseek-v4-flash", context_window: null, max_tokens: null, is_default: true, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "2": { credential_type: "api_key", credential_json: JSON.stringify({ type: "api_key", key: "legacy-key" }), revision: 1, needs_reauth: false, updated_at: "x" },
    },
  });

  const store = loadProviderStore();
  assert.equal(store.active_id, 2);
  assert.equal(store.next_id, 3);
  assert.equal(store.profiles.length, 2);
  assert.deepEqual(store.profiles[1], {
    id: 2,
    kind: "builtin",
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key", key: "legacy-key" },
  });
  const profile = loadProfile();
  assert.ok(profile);
  assert.equal(profile.provider_id, "opencode-go");
  assert.deepEqual(profile.credential, { type: "api_key", key: "legacy-key" });
});

test("loadProviderStore migrates a legacy custom provider profile", () => {
  writeRawDocument({
    next_id: 2,
    profiles: [
      { id: 1, name: "Custom", provider_id: "custom:abc", kind: "custom_openai_compatible", base_url: "https://provider.example/v1", model_id: "model-x", context_window: 32768, max_tokens: 4096, is_default: true, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "1": { credential_type: "api_key", credential_json: JSON.stringify({ type: "api_key", key: "k" }), revision: 1, needs_reauth: false, updated_at: "x" },
    },
  });

  const store = loadProviderStore();
  assert.equal(store.profiles.length, 1);
  const entry = store.profiles[0];
  assert.ok(entry);
  assert.equal(entry.kind, "custom_openai_compatible");
  assert.equal(entry.provider_name, "Custom");
  assert.equal(entry.base_url, "https://provider.example/v1");
  assert.equal(entry.context_window, 32768);
  assert.equal(entry.max_tokens, 4096);
  assert.deepEqual(entry.credential, { type: "api_key", key: "k" });
});

test("loadProviderStore skips legacy profiles that need reauth or fail validation", () => {
  writeRawDocument({
    next_id: 4,
    profiles: [
      { id: 1, name: "Expired", provider_id: "opencode-go", kind: "builtin", base_url: null, model_id: "deepseek-v4-flash", context_window: null, max_tokens: null, is_default: true, created_at: "x", updated_at: "x" },
      { id: 3, name: "Broken", provider_id: "", kind: "builtin", base_url: null, model_id: "deepseek-v4-flash", context_window: null, max_tokens: null, is_default: false, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "1": { credential_type: "oauth", credential_json: JSON.stringify({ type: "oauth", access: "a", refresh: "r", expires: 1 }), revision: 1, needs_reauth: true, updated_at: "x" },
    },
  });

  const store = loadProviderStore();
  assert.deepEqual(store.profiles, []);
  assert.equal(store.active_id, null);
  assert.equal(loadProfile(), null);
});

test("loadProviderStore returns an empty store for an invalid document", () => {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(providerConfigPath(), "not-json", "utf8");
  assert.equal(loadProfile(), null);
  assert.deepEqual(loadProviderStore().profiles, []);
});
