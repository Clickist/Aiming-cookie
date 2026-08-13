import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-routes-"));
process.env.DATA_ROOT = dataRoot;

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
