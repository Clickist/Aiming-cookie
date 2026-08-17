import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-routes-"));
process.env.DATA_ROOT = dataRoot;

import { loadProfile } from "../src/provider-store.ts";
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
    const created = await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    assert.equal(created.statusCode, 201);
    const profile = created.json as Record<string, unknown>;
    assert.equal(profile.id, 1);
    assert.equal(profile.kind, "builtin");
    assert.equal(profile.provider_id, "opencode-go");
    assert.equal(profile.model_id, "deepseek-v4-flash");
    assert.equal(profile.is_default, true);
    assert.equal(profile.has_api_key, false);

    const listed = await request(server, "GET", "/v1/provider-profiles");
    assert.equal((listed.json as { profiles: unknown[] }).profiles.length, 1);
  });
});

test("GET /v1/provider-profiles/status reports the saved profile", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const res = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    assert.equal(status.profile_id, 1);
    assert.equal(status.configured, false);
    assert.equal(status.status, "unconfigured");
  });
});

test("GET /v1/provider-profiles/status returns an unconfigured projection when nothing is saved", async () => {
  await withServer(async (server) => {
    // The shared DATA_ROOT may still hold a profile from an earlier test.
    await request(server, "DELETE", "/v1/provider-profiles/1");
    const res = await request(server, "GET", "/v1/provider-profiles/status");
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    assert.equal(status.profile_id, null);
    assert.equal(status.configured, false);
    assert.equal(status.status, "unconfigured");
  });
});

test("PUT /v1/provider-profiles/{id} overwrites the single profile", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const updated = await request(server, "PUT", "/v1/provider-profiles/1", JSON.stringify({
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

test("PUT /v1/provider-profiles/{id}/auth/api-key stores the credential", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const res = await request(server, "PUT", "/v1/provider-profiles/1/auth/api-key", JSON.stringify({
      api_key: "write-only-key",
    }));
    assert.equal(res.statusCode, 200);
    const profile = res.json as Record<string, unknown>;
    assert.equal(profile.has_api_key, true);
    assert.equal(profile.credential_configured, true);
  });
});

test("DELETE /v1/provider-profiles/{id}/auth/credential removes the stored credential", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    await request(server, "PUT", "/v1/provider-profiles/1/auth/api-key", JSON.stringify({ api_key: "k" }));
    const res = await request(server, "DELETE", "/v1/provider-profiles/1/auth/credential");
    assert.equal(res.statusCode, 200);
    const profile = res.json as Record<string, unknown>;
    assert.equal(profile.credential_configured, false);
    assert.equal(profile.has_api_key, false);
  });
});

test("DELETE /v1/provider-profiles/{id} removes the single profile", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const deleted = await request(server, "DELETE", "/v1/provider-profiles/1");
    assert.equal(deleted.statusCode, 200);
    assert.deepEqual(deleted.json, { deleted: true, id: 1 });
    const listed = await request(server, "GET", "/v1/provider-profiles");
    assert.deepEqual((listed.json as { profiles: unknown[] }).profiles, []);
  });
});

test("POST /v1/provider-profiles/{id}/test on a missing profile returns 404", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles/1/test");
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

test("POST /v1/provider-profiles with invalid input returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      model_id: "deepseek-v4-flash",
    }));
    assert.equal(res.statusCode, 400);
  });
});

const MODEL_SWITCH_BODY = (modelId: string) => JSON.stringify({
  schema_version: "coach_provider_model_switch.v1",
  model_id: modelId,
});

test("POST /v1/provider-profiles/model switches the default profile model and persists it", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro"));
    assert.equal(res.statusCode, 200);
    const status = res.json as { ok: boolean; status: string; model: { model_id: string; model_name: string } | null };
    assert.equal(status.ok, true);
    assert.equal(status.status, "unconfigured");
    assert.equal(status.model?.model_id, "deepseek-v4-pro");
    assert.equal(status.model?.model_name, "DeepSeek V4 Pro");

    // Re-reading through a fresh request must still see the switched model.
    const listed = await request(server, "GET", "/v1/provider-profiles");
    const profile = (listed.json as { profiles: Array<{ model_id: string }> }).profiles[0];
    assert.equal(profile.model_id, "deepseek-v4-pro");
  });
});

test("POST /v1/provider-profiles/model keeps the stored credential intact", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    await request(server, "PUT", "/v1/provider-profiles/1/auth/api-key", JSON.stringify({ api_key: "keep-me" }));
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

test("POST /v1/provider-profiles/model rejects a stored profile whose model cannot resolve", async () => {
  await withServer(async (server) => {
    // 走真实创建链路：custom profile 不带 context_window/max_tokens 也能
    // 创建，但每次模型切换都解析不出能力；切换须拒绝并透传底层原因，
    // 而不是误导成「模型不可用」，且不得落盘死模型。
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab",
      base_url: "https://provider.example/v1",
      model_id: "custom-model-a",
      api_key: "custom-key",
    }));
    assert.equal(created.statusCode, 201);
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("custom-model-b"));
    assert.equal(res.statusCode, 400);
    assert.equal(
      (res.json as { detail: string }).detail,
      "Custom provider did not return verified context_window and max_tokens",
    );
    assert.equal(loadProfile()?.model_id, "custom-model-a");
  });
});

test("POST /v1/provider-profiles/model without a saved profile returns 404", async () => {
  await withServer(async (server) => {
    await request(server, "DELETE", "/v1/provider-profiles/1");
    const res = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("deepseek-v4-pro"));
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/provider-profiles/model with an invalid body returns 400", async () => {
  await withServer(async (server) => {
    await request(server, "POST", "/v1/provider-profiles", BUILTIN_BODY);
    const missingSchema = await request(server, "POST", "/v1/provider-profiles/model", JSON.stringify({ model_id: "deepseek-v4-pro" }));
    assert.equal(missingSchema.statusCode, 400);
    const blankModel = await request(server, "POST", "/v1/provider-profiles/model", MODEL_SWITCH_BODY("  "));
    assert.equal(blankModel.statusCode, 400);
  });
});
