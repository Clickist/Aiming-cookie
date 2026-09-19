import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-routes-"));
process.env.DATA_ROOT = dataRoot;
// 官方档（aiming-cookie-relay）的 base_url 走环境变量注入（源码不含真实地址）；
// 不设则官方档不注入、建档 502。占位地址即可，计费请求由 stubBillingFetch 拦截。
process.env.AC_RELAY_BASE_URL = "http://127.0.0.1:3000/v1";

import { findStoredProfile, loadProfile, loadProviderStore } from "../src/provider-store.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      {
        host: "127.0.0.1",
        port: address.port,
        method,
        path,
        headers: body
          ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }
          : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
        });
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function withServer(run: (server: http.Server) => Promise<void>): Promise<void> {
  const server = createSidecarServer();
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      try {
        await run(server);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
      }
    });
  });
}

type ProfileView = {
  id: number;
  provider_id: string;
  model_id: string;
  is_default: boolean;
};

async function listedProfiles(server: http.Server): Promise<ProfileView[]> {
  const res = await request(server, "GET", "/v1/provider-profiles");
  assert.equal(res.statusCode, 200);
  return (res.json as { profiles: ProfileView[] }).profiles;
}

/** Tests share one DATA_ROOT; start from a known empty store. */
async function clearProfiles(server: http.Server): Promise<void> {
  for (const profile of await listedProfiles(server)) {
    const res = await request(server, "DELETE", `/v1/provider-profiles/${profile.id}`);
    assert.equal(res.statusCode, 200);
  }
}

async function createProfile(server: http.Server, body: unknown): Promise<ProfileView> {
  const res = await request(server, "POST", "/v1/provider-profiles", JSON.stringify(body));
  assert.equal(res.statusCode, 201);
  return res.json as ProfileView;
}

const BUILTIN_BODY = JSON.stringify({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  is_default: true,
});

test("GET /v1/provider-profiles returns an empty list before any profile is saved", async () => {
  await withServer(async (server) => {
    const res = await request(server, "GET", "/v1/provider-profiles");
    assert.equal(res.statusCode, 200);
    assert.deepEqual((res.json as { profiles: unknown[] }).profiles, []);
  });
});

test("POST /v1/provider-profiles persists a builtin profile and returns the projection", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    assert.equal(created.statusCode, 201);
    const profile = created.json as Record<string, unknown>;
    assert.equal(profile.kind, "builtin");
    assert.equal(profile.provider_id, "opencode-go");
    assert.equal(profile.model_id, "deepseek-v4-flash");
    assert.equal(profile.is_default, true);
    assert.equal(profile.has_api_key, false);

    const listed = await request(server, "GET", "/v1/provider-profiles");
    assert.equal((listed.json as { profiles: unknown[] }).profiles.length, 1);
  });
});

test("POST /v1/provider-profiles keeps the user display name for builtin profiles", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      name: "我的 DeepSeek",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    }));
    assert.equal(created.statusCode, 201);
    assert.equal((created.json as Record<string, unknown>).name, "我的 DeepSeek");
    const stored = findStoredProfile(loadProviderStore(), (created.json as { id: number }).id);
    assert.ok(stored);
    if (stored.kind !== "builtin") assert.fail("expected a builtin profile");
    assert.equal(stored.name, "我的 DeepSeek");

    // 无 name 的旧式请求体不硬性要求命名：投影回落 provider_id。
    const unnamed = await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    assert.equal(unnamed.statusCode, 201);
    assert.equal((unnamed.json as Record<string, unknown>).name, "opencode-go");
  });
});

test("PUT /v1/provider-profiles/{id} without a name keeps the stored builtin display name", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      name: "我的 DeepSeek",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      api_key: "sk-live-key",
    });
    const updated = await request(server, "PUT", `/v1/provider-profiles/${created.id}`, JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    }));
    assert.equal(updated.statusCode, 200);
    assert.equal((updated.json as Record<string, unknown>).name, "我的 DeepSeek");
  });
});

test("POST /v1/provider-profiles appends a second profile and keeps the first active", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "custom_openai_compatible",
      name: "Local Lab",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-a",
      api_key: "custom-key",
    });
    assert.notEqual(first.id, second.id);

    const profiles = await listedProfiles(server);
    assert.equal(profiles.length, 2);
    assert.equal(profiles[0]?.is_default, true);
    assert.equal(profiles[1]?.is_default, false);
    // 添加不改变 active：coach 回合仍解析第一档。
    assert.equal(loadProfile()?.model_id, "deepseek-v4-flash");
  });
});

test("POST /v1/provider-profiles with an id updates that profile only", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const updated = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      id: second.id,
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    }));
    assert.equal(updated.statusCode, 200);
    assert.equal((updated.json as ProfileView).model_id, "deepseek-v4-pro");

    const profiles = await listedProfiles(server);
    assert.equal(profiles.length, 2);
    assert.deepEqual(profiles.find((profile) => profile.id === first.id)?.model_id, "deepseek-v4-flash");
    assert.deepEqual(profiles.find((profile) => profile.id === second.id)?.model_id, "deepseek-v4-pro");
  });
});

test("GET /v1/provider-profiles/status reports the active profile", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const res = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    assert.equal(status.profile_id, created.id);
    assert.equal(status.configured, false);
    assert.equal(status.status, "unconfigured");
  });
});

test("GET /v1/provider-profiles/status returns an unconfigured projection when nothing is saved", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    assert.equal(status.profile_id, null);
    assert.equal(status.configured, false);
    assert.equal(status.status, "unconfigured");
  });
});

test("PUT /v1/provider-profiles/{id} updates that profile", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const updated = await request(server, "PUT", `/v1/provider-profiles/${created.id}`, JSON.stringify({
      kind: "builtin",
      provider_id: "deepseek",
      model_id: "deepseek-v3",
    }));
    assert.equal(updated.statusCode, 200);
    const profile = updated.json as Record<string, unknown>;
    assert.equal(profile.provider_id, "deepseek");
    assert.equal(profile.model_id, "deepseek-v3");
  });
});

test("PUT /v1/provider-profiles/{id} on a missing profile returns 404", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "PUT", "/v1/provider-profiles/9999", BUILTIN_BODY);
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/provider-profiles/{id}/default switches the active profile", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    });
    const res = await request(server, "POST", `/v1/provider-profiles/${second.id}/default`);
    assert.equal(res.statusCode, 200);
    const profile = res.json as ProfileView;
    assert.equal(profile.id, second.id);
    assert.equal(profile.is_default, true);
    assert.equal(loadProfile()?.model_id, "deepseek-v4-pro");

    const statuses = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal((statuses.json as { profile_id: number }).profile_id, second.id);
    const listed = await listedProfiles(server);
    assert.equal(listed.find((entry) => entry.id === first.id)?.is_default, false);
    assert.equal(listed.find((entry) => entry.id === second.id)?.is_default, true);
  });
});

test("POST /v1/provider-profiles/{id}/default on a missing profile returns 404", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "POST", "/v1/provider-profiles/9999/default");
    assert.equal(res.statusCode, 404);
  });
});

test("PUT /v1/provider-profiles/{id}/auth/api-key stores the credential", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const res = await request(server, "PUT", `/v1/provider-profiles/${created.id}/auth/api-key`, JSON.stringify({
      api_key: "write-only-key",
    }));
    assert.equal(res.statusCode, 200);
    const profile = res.json as Record<string, unknown>;
    assert.equal(profile.has_api_key, true);
    assert.equal(profile.credential_configured, true);
  });
});

test("PUT /v1/provider-profiles/{id}/auth/api-key only touches the addressed profile", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const res = await request(server, "PUT", `/v1/provider-profiles/${second.id}/auth/api-key`, JSON.stringify({
      api_key: "second-key",
    }));
    assert.equal(res.statusCode, 200);
    // 只有被指定的档写入 credential；active 档保持无凭证。
    assert.equal(loadProfile()?.credential, undefined);
    const listed = await request(server, "GET", "/v1/provider-profiles");
    const profiles = (listed.json as { profiles: Array<{ id: number; is_default: boolean; has_api_key: boolean }> }).profiles;
    assert.equal(profiles.find((profile) => profile.id === first.id)?.has_api_key, false);
    assert.equal(profiles.find((profile) => profile.id === second.id)?.has_api_key, true);
  });
});

test("DELETE /v1/provider-profiles/{id}/auth/credential removes the stored credential", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    await request(server, "PUT", `/v1/provider-profiles/${created.id}/auth/api-key`, JSON.stringify({ api_key: "k" }));
    const res = await request(server, "DELETE", `/v1/provider-profiles/${created.id}/auth/credential`);
    assert.equal(res.statusCode, 200);
    const profile = res.json as Record<string, unknown>;
    assert.equal(profile.credential_configured, false);
    assert.equal(profile.has_api_key, false);
  });
});

test("DELETE /v1/provider-profiles/{id} removes the last profile", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const deleted = await request(server, "DELETE", `/v1/provider-profiles/${created.id}`);
    assert.equal(deleted.statusCode, 200);
    assert.deepEqual(deleted.json, { deleted: true, id: created.id });
    assert.deepEqual(await listedProfiles(server), []);
    const status = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal((status.json as { profile_id: number | null }).profile_id, null);
  });
});

test("DELETE /v1/provider-profiles/{id} of a non-active profile keeps the active one", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    });
    const deleted = await request(server, "DELETE", `/v1/provider-profiles/${second.id}`);
    assert.deepEqual(deleted.json, { deleted: true, id: second.id });
    const profiles = await listedProfiles(server);
    assert.equal(profiles.length, 1);
    assert.equal(profiles[0]?.id, first.id);
    assert.equal(profiles[0]?.is_default, true);
    assert.equal(loadProfile()?.model_id, "deepseek-v4-flash");
  });
});

test("DELETE /v1/provider-profiles/{id} of the active profile promotes the first remaining one", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    });
    const deleted = await request(server, "DELETE", `/v1/provider-profiles/${first.id}`);
    assert.deepEqual(deleted.json, { deleted: true, id: first.id });
    const profiles = await listedProfiles(server);
    assert.equal(profiles.length, 1);
    assert.equal(profiles[0]?.id, second.id);
    assert.equal(profiles[0]?.is_default, true);
    assert.equal(loadProfile()?.model_id, "deepseek-v4-pro");
    const status = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal((status.json as { profile_id: number }).profile_id, second.id);
  });
});

test("DELETE /v1/provider-profiles/{id} for a missing profile reports deleted=false", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const deleted = await request(server, "DELETE", "/v1/provider-profiles/9999");
    assert.equal(deleted.statusCode, 200);
    assert.deepEqual(deleted.json, { deleted: false, id: 9999 });
  });
});

test("POST /v1/provider-profiles/{id}/test on a missing profile returns 404", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "POST", "/v1/provider-profiles/9999/test");
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/provider-profiles/custom/models proxies the custom /models endpoint", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    const signal = init?.signal;
    if (signal?.aborted) throw new Error("aborted");
    return new Response(JSON.stringify({ data: [
      { id: "model-a", context_window: 32768, max_tokens: 4096 },
      { id: "model-b" },
    ] }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({
      protocol: "openai-completions",
      base_url: "https://provider.example/v1",
      api_key: "request-only-key",
    }));
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, {
      models: [
        { model_id: "model-a", context_window: 32768, max_tokens: 4096 },
        { model_id: "model-b", context_window: null, max_tokens: null },
      ],
    });
  }).finally(() => {
    globalThis.fetch = realFetch;
  });
});

test("POST /v1/provider-profiles/custom/models with profile_id discovers via the stored credential (点点 09-08)", async () => {
  const seen: Array<{ url: string; auth: string | undefined }> = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.includes("/models")) {
      seen.push({
        url,
        auth: (init?.headers as Record<string, string> | undefined)?.Authorization,
      });
      return new Response(JSON.stringify({ data: [
        { id: "relay-model-x", context_window: 131072 },
      ] }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return realFetch(input, init);
  }) as typeof fetch;

  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "custom_openai_compatible",
      name: "自家中转站",
      base_url: "https://relay.example/v1",
      model_id: "relay-model-x",
      api_key: "stored-key",
    });

    const res = await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({
      profile_id: created.id,
    }));
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, {
      models: [{ model_id: "relay-model-x", context_window: 131072, max_tokens: null }],
    });
    // 档内凭证就地发现：key 不出 sidecar，URL 取档 base_url。
    assert.equal(seen.length, 1);
    assert.ok(seen[0]!.url.startsWith("https://relay.example/v1"));
    assert.equal(seen[0]!.auth, "Bearer stored-key");
  }).finally(() => {
    globalThis.fetch = realFetch;
  });
});

test("custom/models with profile_id: missing profile returns 404, builtin profile returns 400", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const missing = await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({
      profile_id: 9999,
    }));
    assert.equal(missing.statusCode, 404);

    await createProfile(server, {
      kind: "builtin",
      provider_id: "deepseek",
      model_id: "deepseek-v4-flash",
      api_key: "builtin-key",
    });
    const listed = await listedProfiles(server);
    const builtin = await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({
      profile_id: listed[0]!.id,
    }));
    assert.equal(builtin.statusCode, 400);
  });
});

test("POST /v1/provider-profiles with invalid input returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      model_id: "deepseek-v4-flash",
    }));
    assert.equal(res.statusCode, 400);
  });
});

const MODEL_SWITCH_BODY = (modelId: string, profileId?: number) => JSON.stringify({
  schema_version: "coach_provider_model_switch.v1",
  model_id: modelId,
  ...(profileId !== undefined ? { profile_id: profileId } : {}),
});

test("POST /v1/provider-profiles/model switches the active profile model and persists it", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro"));
    assert.equal(res.statusCode, 200);
    const status = res.json as { ok: boolean; status: string; model: { model_id: string; model_name: string } | null };
    assert.equal(status.ok, true);
    assert.equal(status.status, "unconfigured");
    assert.equal(status.model?.model_id, "deepseek-v4-pro");
    // 0.83.0 起 opencode-go 目录把该条目改名带 (New) 后缀，投影忠实反映。
    assert.equal(status.model?.model_name, "DeepSeek V4 Pro (New)");

    // Re-reading through a fresh request must still see the switched model.
    const listed = await request(server, "GET", "/v1/provider-profiles");
    const profile = (listed.json as { profiles: Array<{ model_id: string }> }).profiles[0];
    assert.equal(profile.model_id, "deepseek-v4-pro");
  });
});

test("POST /v1/provider-profiles/model keeps the stored credential intact", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    await request(server, "PUT", `/v1/provider-profiles/${created.id}/auth/api-key`, JSON.stringify({ api_key: "keep-me" }));
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro"));
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { credential_source: string | null }).credential_source, "runtime_profile");
    assert.equal(loadProfile()?.credential?.type, "api_key");
    const listed = await request(server, "GET", "/v1/provider-profiles");
    const profile = (listed.json as { profiles: Array<{ has_api_key: boolean; credential_configured: boolean }> }).profiles[0];
    assert.equal(profile.has_api_key, true);
    assert.equal(profile.credential_configured, true);
  });
});

test("POST /v1/provider-profiles/model rejects a model outside the provider catalog", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("not-a-real-model"));
    assert.equal(res.statusCode, 400);
    // 目录外的模型：提示换模型，而不是透传底层原因。
    assert.equal((res.json as { detail: string }).detail, "所选模型不可用，请选择当前 Provider 目录中的模型");
    // The failed switch must not touch the persisted profile.
    const listed = await request(server, "GET", "/v1/provider-profiles");
    assert.equal((listed.json as { profiles: Array<{ model_id: string }> }).profiles[0].model_id, "deepseek-v4-flash");
  });
});

test("POST /v1/provider-profiles/model updates a custom profile model id", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-a",
      context_window: 32768,
      max_tokens: 4096,
      api_key: "custom-key",
    }));
    assert.equal(created.statusCode, 201);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("custom-model-b"));
    assert.equal(res.statusCode, 200);
    assert.equal(loadProfile()?.model_id, "custom-model-b");
    const listed = await request(server, "GET", "/v1/provider-profiles");
    const profile = (listed.json as { profiles: Array<{ model_id: string; kind: string }> }).profiles[0];
    assert.equal(profile.kind, "custom_openai_compatible");
    assert.equal(profile.model_id, "custom-model-b");
  });
});

test("POST /v1/provider-profiles/model with profile_id switches that profile and leaves the active one alone", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const first = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const second = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    });
    const res = await request(
      server,
      "POST",
      "/v1/provider-profiles/model",
      MODEL_SWITCH_BODY("deepseek-v4-pro", second.id),
    );
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { model: { model_id: string } | null }).model?.model_id, "deepseek-v4-pro");

    const profiles = await listedProfiles(server);
    assert.equal(profiles.find((profile) => profile.id === first.id)?.model_id, "deepseek-v4-flash");
    assert.equal(profiles.find((profile) => profile.id === second.id)?.model_id, "deepseek-v4-pro");
    // 指定档切换不改变 active：coach 回合仍用第一档。
    assert.equal(loadProfile()?.model_id, "deepseek-v4-flash");
  });
});

test("POST /v1/provider-profiles/model with an invalid profile_id returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro", -1));
    assert.equal(res.statusCode, 400);
    assert.equal((res.json as { detail: string }).detail, "profile_id must be a positive integer when supplied");
  });
});

test("POST /v1/provider-profiles/model with an unknown profile_id returns 404", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro", 9999));
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/provider-profiles/model switches a stored profile without discovered capabilities", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    // 走真实创建链路：custom profile 不带 context_window/max_tokens（端点
    // /models 未回报）也能创建；默认能力下模型可解析，切换须成功，
    // 且回退默认值只注入 resolve 层，不得写进落盘文档。
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-a",
      api_key: "custom-key",
    }));
    assert.equal(created.statusCode, 201);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("custom-model-b"));
    assert.equal(res.statusCode, 200);
    const persisted = loadProfile();
    assert.ok(persisted);
    assert.equal(persisted.model_id, "custom-model-b");
    if (persisted.kind === "custom_openai_compatible") {
      assert.equal(persisted.context_window, undefined);
      assert.equal(persisted.max_tokens, undefined);
    } else {
      assert.fail("expected a custom_openai_compatible profile");
    }
  });
});

test("POST /v1/provider-profiles/model without a saved profile returns 404", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro"));
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/provider-profiles/model with an invalid body returns 400", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const missingSchema = await request(server, "POST", "/v1/provider-profiles/model", JSON.stringify({ model_id: "deepseek-v4-pro" }));
    assert.equal(missingSchema.statusCode, 400);
    const blankModel = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("  "));
    assert.equal(blankModel.statusCode, 400);
  });
});

test("PUT /v1/provider-profiles/{id} without api_key keeps the stored credential", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      api_key: "sk-live-onboarding-key",
    });

    // OnboardingFlow 重跑连接步骤：预填现有档、不动 key（body 不带 api_key）。
    const updated = await request(server, "PUT", `/v1/provider-profiles/${created.id}`, JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-pro",
    }));
    assert.equal(updated.statusCode, 200);
    assert.equal((updated.json as Record<string, unknown>).model_id, "deepseek-v4-pro");
    assert.equal((updated.json as Record<string, unknown>).has_api_key, true);

    const stored = findStoredProfile(loadProviderStore(), created.id as number);
    assert.ok(stored);
    assert.ok(stored.credential);
    if (stored.credential.type === "api_key") {
      assert.equal(stored.credential.key, "sk-live-onboarding-key");
    } else {
      assert.fail("expected the stored api_key credential to survive");
    }
  });
});

test("PUT /v1/provider-profiles/{id} keeps a custom profile credential when no key is sent", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "custom_openai_compatible",
      name: "Local Lab",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-a",
      api_key: "custom-live-key",
    });

    const updated = await request(server, "PUT", `/v1/provider-profiles/${created.id}`, JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab Renamed",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-b",
    }));
    assert.equal(updated.statusCode, 200);
    const profile = updated.json as Record<string, unknown>;
    assert.equal(profile.name, "Local Lab Renamed");
    assert.equal(profile.has_api_key, true);

    const stored = findStoredProfile(loadProviderStore(), created.id as number);
    assert.ok(stored);
    if (stored.credential && stored.credential.type === "api_key") {
      assert.equal(stored.credential.key, "custom-live-key");
    } else {
      assert.fail("expected the custom api_key credential to survive");
    }
  });
});

test("PUT /v1/provider-profiles/{id} with a new api_key replaces the credential", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await createProfile(server, {
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      api_key: "old-key",
    });

    const updated = await request(server, "PUT", `/v1/provider-profiles/${created.id}`, JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      api_key: "new-key",
    }));
    assert.equal(updated.statusCode, 200);
    assert.equal((updated.json as Record<string, unknown>).has_api_key, true);

    const stored = findStoredProfile(loadProviderStore(), created.id as number);
    assert.ok(stored?.credential);
    if (stored.credential.type === "api_key") {
      assert.equal(stored.credential.key, "new-key");
    } else {
      assert.fail("expected an api_key credential");
    }
  });
});

// ── 官方中转档余额（点点 0912 拍板）：new-api 计费兼容端点，sidecar 算好下发 ──

function stubBillingFetch(total: number, used: number, status = 200): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: unknown) => {
    const url = String(input);
    if (status !== 200) return new Response("{}", { status });
    const json = url.endsWith("/dashboard/billing/subscription")
      ? { hard_limit_usd: total }
      : { total_usage: used };
    return new Response(JSON.stringify(json), { status: 200 });
  }) as typeof fetch;
  return () => { globalThis.fetch = original; };
}

test("member status reports logged_in false when no member credential is stored", async () => {
  // WP-C：会员档退役了「API 计费 / 余额」路径，额度只以百分比由 /api/me 下发；
  // 未登录是常态（不是错误）→ 200 + logged_in:false。
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "GET", "/v1/provider-profiles/member/me");
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { ok: boolean }).ok, false);
    assert.equal((res.json as { logged_in: boolean }).logged_in, false);
    assert.equal((res.json as { code: string }).code, "unauthorized");
  });
});

test("member exchange rejects a ticket that has no local pending device_code", async () => {
  // 契约 §3.3-4（dc 绑定）：本地没有待用 dc（转发的链接 / 冷启动）→ 丢弃 ticket，
  // 降级为「无 ticket」分支，且必须是 200 + 结构化 code（不是异常）。
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "POST", "/v1/provider-profiles/member/exchange", JSON.stringify({
      ticket: "a".repeat(64),
      dc: "no-such-device-code",
    }));
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { ok: boolean }).ok, false);
    assert.equal((res.json as { code: string }).code, "dc_mismatch");
  });
});

test("member exchange without a ticket pair degrades to the no-ticket branch", async () => {
  // 契约 §3.3-3：ticket 与 dc 必须成对；只有一个 → 忽略 ticket（不报错）。
  await withServer(async (server) => {
    await clearProfiles(server);
    const res = await request(server, "POST", "/v1/provider-profiles/member/exchange", JSON.stringify({
      ticket: "b".repeat(64),
    }));
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { code: string }).code, "no_ticket");
  });
});

test("member logout clears the relay credential and reports the BYOK fallback", async () => {
  // ④b：退出登录只停用订阅额度；配过 BYOK → 自动切过去，没配过 → 返回 null。
  await withServer(async (server) => {
    await clearProfiles(server);
    const member = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      provider_id: "aiming-cookie-relay",
      model_id: "deepseek-v4-flash",
      api_key: "jwt-like-token",
    }));
    assert.equal(member.statusCode, 201);
    const byok = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "custom_openai_compatible",
      provider_id: "byok-user",
      provider_name: "BYOK User",
      base_url: "https://provider.example/v1",
      model_id: "some-model",
      api_key: "sk-byok",
      context_window: 32768,
      max_tokens: 4096,
    }));
    assert.equal(byok.statusCode, 201);
    const byokId = (byok.json as { id: number }).id;

    const res = await request(server, "POST", "/v1/provider-profiles/member/logout");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, {
      ok: true,
      relay_profile_id: (member.json as { id: number }).id,
      fallback_profile_id: byokId,
      active_profile_id: byokId,
    });
    // 档还在（只是凭据被清），会员状态回到未登录。
    const me = await request(server, "GET", "/v1/provider-profiles/member/me");
    assert.equal((me.json as { logged_in: boolean }).logged_in, false);
  });
});

test("GET /v1/provider-profiles/{id}/auth/credential reveals the stored key", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      api_key: "sk-visible-in-beta",
    }));
    assert.equal(created.statusCode, 201);
    const id = (created.json as { id: number }).id;
    const res = await request(server, "GET", `/v1/provider-profiles/${id}/auth/credential`);
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { api_key: string }).api_key, "sk-visible-in-beta");
  });
});

test("GET credential returns 404 when the profile has no stored key", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    assert.equal(created.statusCode, 201);
    const id = (created.json as { id: number }).id;
    const res = await request(server, "GET", `/v1/provider-profiles/${id}/auth/credential`);
    assert.equal(res.statusCode, 404);
  });
});

// ── 模型发现存档（点点 0912 拍板）：详情页免点获取模型，获取模型只做更新 ──

function stubCustomModelsFetch(): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(
    JSON.stringify({ data: [{ id: "mock-model-a" }, { id: "mock-model-b" }] }),
    { status: 200 },
  )) as typeof fetch;
  return () => { globalThis.fetch = original; };
}

const CUSTOM_BODY = JSON.stringify({
  kind: "custom_openai_compatible",
  name: "自家中转站",
  base_url: "http://127.0.0.1:9/v1",
  model_id: "mock-model-a",
  api_key: "sk-custom",
});

test("stored custom model discovery persists discovered_models into the profile projection", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", CUSTOM_BODY);
    assert.equal(created.statusCode, 201);
    const id = (created.json as { id: number }).id;
    const restore = stubCustomModelsFetch();
    try {
      const res = await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({ profile_id: id }));
      assert.equal(res.statusCode, 200);
      assert.deepEqual((res.json as { models: Array<{ model_id: string }> }).models.map((m) => m.model_id), ["mock-model-a", "mock-model-b"]);
      // 投影透出存档列表
      const listed = await listedProfiles(server);
      const view = listed;
      const detailed = await request(server, "GET", "/v1/provider-profiles");
      const profile = (detailed.json as { profiles: Array<{ id: number; discovered_models: Array<{ model_id: string }> | null }> }).profiles
        .find((p) => p.id === id);
      assert.ok(profile?.discovered_models);
      assert.deepEqual(profile.discovered_models.map((m) => m.model_id), ["mock-model-a", "mock-model-b"]);
    } finally {
      restore();
    }
  });
});

test("PUT without discovered_models keeps the stored list; rename does not wipe it", async () => {
  await withServer(async (server) => {
    await clearProfiles(server);
    const created = await request(server, "POST", "/v1/provider-profiles", CUSTOM_BODY);
    const id = (created.json as { id: number }).id;
    const restore = stubCustomModelsFetch();
    try {
      await request(server, "POST", "/v1/provider-profiles/custom/models", JSON.stringify({ profile_id: id }));
    } finally {
      restore();
    }
    // 改名 + 换 URL 的整档更新（不带 discovered_models）
    const updated = await request(server, "PUT", `/v1/provider-profiles/${id}`, JSON.stringify({
      kind: "custom_openai_compatible",
      name: "改名后的中转",
      base_url: "http://127.0.0.1:9/v2",
      model_id: "mock-model-a",
    }));
    assert.equal(updated.statusCode, 200);
    const profile = (updated.json as { discovered_models: Array<{ model_id: string }> | null }).discovered_models;
    assert.ok(profile);
    assert.equal(profile.length, 2);
  });
});
