import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import http from "node:http";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-dryrun-"));
process.env.DATA_ROOT = dataRoot;

import { loadProviderStore } from "../src/provider-store.ts";
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

/** Full content fingerprint of DATA_ROOT: any byte written anywhere shows up. */
function dataRootFingerprint(dir: string, prefix = ""): Map<string, string> {
  const entries = new Map<string, string>();
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const relative = prefix ? `${prefix}/${name}` : name;
    if (statSync(full).isDirectory()) {
      for (const [key, value] of dataRootFingerprint(full, relative)) entries.set(key, value);
      continue;
    }
    entries.set(relative, createHash("sha256").update(readFileSync(full)).digest("hex"));
  }
  return entries;
}

/** Minimal OpenAI-compatible streaming endpoint: always answers "OK" and stops. */
function withFakeOpenAI(run: (baseUrl: string) => Promise<void>): Promise<void> {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const chunk = (delta: Record<string, unknown>, finish: string | null) => {
      res.write(`data: ${JSON.stringify({
        id: "chatcmpl-dryrun",
        object: "chat.completion.chunk",
        created: 1_700_000_000,
        model: "fixture-model",
        choices: [{ index: 0, delta, finish_reason: finish }],
      })}\n\n`);
    };
    chunk({ role: "assistant", content: "OK" }, null);
    chunk({}, "stop");
    res.write("data: [DONE]\n\n");
    res.end();
  });
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      const port = (server.address() as AddressInfo).port;
      try {
        await run(`http://127.0.0.1:${port}/v1`);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
      }
    });
  });
}

/** Minimal OpenAI-compatible endpoint that also answers GET /models with JSON. */
function withFakeOpenAIPlusModels(run: (baseUrl: string) => Promise<void>): Promise<void> {
  const server = http.createServer((req, res) => {
    if (req.method === "GET" && (req.url ?? "").endsWith("/models")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ data: [{ id: "fixture-model" }] }));
      return;
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const chunk = (delta: Record<string, unknown>, finish: string | null) => {
      res.write(`data: ${JSON.stringify({
        id: "chatcmpl-dryrun",
        object: "chat.completion.chunk",
        created: 1_700_000_000,
        model: "fixture-model",
        choices: [{ index: 0, delta, finish_reason: finish }],
      })}\n\n`);
    };
    chunk({ role: "assistant", content: "OK" }, null);
    chunk({}, "stop");
    res.write("data: [DONE]\n\n");
    res.end();
  });
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      const port = (server.address() as AddressInfo).port;
      try {
        await run(`http://127.0.0.1:${port}/v1`);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
      }
    });
  });
}

test("POST /v1/provider-profiles/test rejects an invalid candidate without persisting anything", async () => {
  await withServer(async (server) => {
    const before = dataRootFingerprint(dataRoot);
    const res = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab",
      // base_url 缺失 → 候选档不完整，必须 400 且不落库。
      model_id: "custom-model-a",
      api_key: "draft-key",
    }));
    assert.equal(res.statusCode, 400);
    assert.deepEqual(dataRootFingerprint(dataRoot), before);
    assert.equal(loadProviderStore().profiles.length, 0);
  });
});

test("POST /v1/provider-profiles/test reports an unresolvable draft without persisting anything", async () => {
  await withServer(async (server) => {
    const before = dataRootFingerprint(dataRoot);
    const res = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "not-a-real-model",
      api_key: "draft-key",
    }));
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    // 复用既有投影：干跑失败也以 200 + 状态字段表达。
    assert.equal(status.status, "model_unavailable");
    assert.equal(status.configured, false);
    assert.equal(status.profile_id, null);
    assert.equal(typeof status.message, "string");

    assert.deepEqual(dataRootFingerprint(dataRoot), before);
    assert.equal(loadProviderStore().profiles.length, 0);
  });
});

test("POST /v1/provider-profiles/test verifies a passing draft without persisting anything", async () => {
  await withFakeOpenAI(async (baseUrl) => {
    await withServer(async (server) => {
      const before = dataRootFingerprint(dataRoot);
      const res = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
        kind: "custom_openai_compatible",
        name: "Local Lab",
        base_url: baseUrl,
        model_id: "fixture-model",
        api_key: "draft-key",
      }));
      assert.equal(res.statusCode, 200);
      const status = res.json as Record<string, unknown>;
      assert.equal(status.profile_id, null);
      assert.equal(status.configured, true);
      assert.equal(status.status, "ready");

      // 干跑成功同样零持久化：DATA_ROOT 每个字节保持原样。
      assert.deepEqual(dataRootFingerprint(dataRoot), before);
      const listed = await request(server, "GET", "/v1/provider-profiles");
      assert.deepEqual((listed.json as { profiles: unknown[] }).profiles, []);
      const active = await request(server, "GET", "/v1/provider-profiles/status");
      assert.equal((active.json as { profile_id: number | null }).profile_id, null);
      assert.equal((active.json as { status: string }).status, "unconfigured");
    });
  });
});

test("POST /v1/provider-profiles/test leaves an existing stored profile untouched", async () => {
  await withServer(async (server) => {
    const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    }));
    assert.equal(created.statusCode, 201);

    const createdId = (created.json as { id: number }).id;

    const before = dataRootFingerprint(dataRoot);
    for (const body of [
      JSON.stringify({
        kind: "builtin",
        provider_id: "opencode-go",
        model_id: "not-a-real-model",
        api_key: "draft-key",
      }),
      JSON.stringify({ kind: "nonsense" }),
    ]) {
      await request(server, "POST", "/v1/provider-profiles/test", body);
    }

    // 已存在档案时干跑也不得改写 active、next_id 或已存 key。
    assert.deepEqual(dataRootFingerprint(dataRoot), before);
    const store = loadProviderStore();
    assert.equal(store.profiles.length, 1);
    assert.equal(store.active_id, createdId);
    assert.equal(store.next_id, 2);
    const listed = await request(server, "GET", "/v1/provider-profiles");
    assert.equal((listed.json as { profiles: Array<{ model_id: string }> }).profiles[0].model_id, "deepseek-v4-flash");
  });
});

test("POST /v1/provider-profiles/test probes a model-less custom draft without persisting anything", async () => {
  await withFakeOpenAIPlusModels(async (baseUrl) => {
    await withServer(async (server) => {
      const before = dataRootFingerprint(dataRoot);
      const profilesBefore = loadProviderStore().profiles.length;
      const res = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
        kind: "custom_openai_compatible",
        name: "Local Lab",
        base_url: baseUrl,
        // model_id 缺省 → 免模型连通探测（点点 0911 两步式向导）。
        model_id: "",
        api_key: "draft-key",
      }));
      assert.equal(res.statusCode, 200);
      const status = res.json as Record<string, unknown>;
      assert.equal(status.profile_id, null);
      assert.equal(status.status, "ready");
      assert.equal(status.configured, true);

      assert.deepEqual(dataRootFingerprint(dataRoot), before);
      assert.equal(loadProviderStore().profiles.length, profilesBefore);
    });
  });
});

test("POST /v1/provider-profiles/test probes a model-less builtin draft for reachability", async () => {
  await withServer(async (server) => {
    const before = dataRootFingerprint(dataRoot);
    const profilesBefore = loadProviderStore().profiles.length;
    // 未知 provider → model_unavailable（不发网络请求）。
    const unknown = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
      kind: "builtin",
      provider_id: "not-a-provider",
      model_id: "",
      api_key: "draft-key",
    }));
    assert.equal(unknown.statusCode, 200);
    assert.equal((unknown.json as Record<string, unknown>).status, "model_unavailable");

    // 已知 provider 但没有可用凭据 → connection_failed（同样零持久化）。
    const noKey = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: " ",
    }));
    assert.equal(noKey.statusCode, 200);
    assert.equal((noKey.json as Record<string, unknown>).status, "connection_failed");

    assert.deepEqual(dataRootFingerprint(dataRoot), before);
    assert.equal(loadProviderStore().profiles.length, profilesBefore);
  });
});

test("POST /v1/provider-profiles/test probes an unreachable custom endpoint as connection_failed", async () => {
  await withServer(async (server) => {
    const before = dataRootFingerprint(dataRoot);
    const profilesBefore = loadProviderStore().profiles.length;
    const res = await request(server, "POST", "/v1/provider-profiles/test", JSON.stringify({
      kind: "custom_openai_compatible",
      name: "Local Lab",
      // 端口 1 无监听，连接立即被拒；两种协议都失败才判失败。
      base_url: "http://127.0.0.1:1/v1",
      model_id: "",
      api_key: "draft-key",
    }));
    assert.equal(res.statusCode, 200);
    const status = res.json as Record<string, unknown>;
    assert.equal(status.status, "connection_failed");
    assert.equal(status.configured, false);

    assert.deepEqual(dataRootFingerprint(dataRoot), before);
    assert.equal(loadProviderStore().profiles.length, profilesBefore);
  });
});
