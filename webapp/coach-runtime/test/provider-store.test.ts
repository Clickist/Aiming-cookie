import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-store-"));
process.env.DATA_ROOT = dataRoot;

import { deleteProfile, loadProfile, providerConfigPath, saveProfile } from "../src/provider-store.ts";

test("saveProfile writes a { profile } document and loadProfile reads it back", () => {
  saveProfile({
    kind: "builtin",
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key", key: "stored-secret" },
  });
  const profile = loadProfile();
  assert.ok(profile);
  assert.equal(profile.kind, "builtin");
  assert.equal(profile.provider_id, "opencode-go");
  assert.equal(profile.model_id, "deepseek-v4-flash");
  assert.deepEqual(profile.credential, { type: "api_key", key: "stored-secret" });

  const doc = JSON.parse(readFileSync(providerConfigPath(), "utf8"));
  assert.equal(doc.profile.provider_id, "opencode-go");
});

test("loadProfile returns null when the document is missing", () => {
  deleteProfile();
  assert.equal(loadProfile(), null);
});

test("deleteProfile removes the document and reports whether it existed", () => {
  saveProfile({ kind: "builtin", provider_id: "opencode-go", model_id: "deepseek-v4-flash" });
  assert.equal(deleteProfile(), true);
  assert.equal(deleteProfile(), false);
  assert.equal(loadProfile(), null);
});

test("loadProfile migrates a legacy Python multi-profile document default", () => {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "provider.json"), JSON.stringify({
    next_id: 3,
    profiles: [
      { id: 1, name: "Non-default", provider_id: "deepseek", kind: "builtin", base_url: null, model_id: "deepseek-v3", context_window: null, max_tokens: null, is_default: false, created_at: "x", updated_at: "x" },
      { id: 2, name: "Default", provider_id: "opencode-go", kind: "builtin", base_url: null, model_id: "deepseek-v4-flash", context_window: null, max_tokens: null, is_default: true, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "2": { credential_type: "api_key", credential_json: JSON.stringify({ type: "api_key", key: "legacy-key" }), revision: 1, needs_reauth: false, updated_at: "x" },
    },
  }), "utf8");

  const profile = loadProfile();
  assert.ok(profile);
  assert.equal(profile.kind, "builtin");
  assert.equal(profile.provider_id, "opencode-go");
  assert.deepEqual(profile.credential, { type: "api_key", key: "legacy-key" });
});

test("loadProfile migrates a legacy custom provider profile", () => {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "provider.json"), JSON.stringify({
    next_id: 2,
    profiles: [
      { id: 1, name: "Custom", provider_id: "custom:abc", kind: "custom_openai_compatible", base_url: "https://provider.example/v1", model_id: "model-x", context_window: 32768, max_tokens: 4096, is_default: true, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "1": { credential_type: "api_key", credential_json: JSON.stringify({ type: "api_key", key: "k" }), revision: 1, needs_reauth: false, updated_at: "x" },
    },
  }), "utf8");

  const profile = loadProfile();
  assert.ok(profile);
  assert.equal(profile.kind, "custom_openai_compatible");
  assert.equal(profile.provider_name, "Custom");
  assert.equal(profile.base_url, "https://provider.example/v1");
  assert.equal(profile.context_window, 32768);
  assert.equal(profile.max_tokens, 4096);
  assert.deepEqual(profile.credential, { type: "api_key", key: "k" });
});

test("loadProfile skips a legacy profile whose credential needs reauth", () => {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "provider.json"), JSON.stringify({
    next_id: 2,
    profiles: [
      { id: 1, name: "Expired", provider_id: "opencode-go", kind: "builtin", base_url: null, model_id: "deepseek-v4-flash", context_window: null, max_tokens: null, is_default: true, created_at: "x", updated_at: "x" },
    ],
    credentials: {
      "1": { credential_type: "oauth", credential_json: JSON.stringify({ type: "oauth", access: "a", refresh: "r", expires: 1 }), revision: 1, needs_reauth: true, updated_at: "x" },
    },
  }), "utf8");
  assert.equal(loadProfile(), null);
});

test("loadProfile returns null for an invalid document", () => {
  const dir = join(dataRoot, "config");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "provider.json"), "not-json", "utf8");
  assert.equal(loadProfile(), null);
});
