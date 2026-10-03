/**
 * 桌面令牌闸门 + CORS 白名单回显（ARCHITECTURE「本次启动 token」）。
 *
 * 覆盖：healthz 豁免、无 token/错 token/env 未配置 fail-closed、SSE query
 * token 兜底、OPTIONS 预检不设闸、ACAO 只对白名单 Origin 回显。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-desktop-token-gate-"));
process.env.DATA_ROOT = dataRoot;

const { createSidecarServer } = await import("../src/sidecar-server.ts");
const { DESKTOP_TEST_TOKEN } = await import("./desktop-token-env.ts");

function request(
  server: http.Server,
  method: string,
  path: string,
  headers: http.OutgoingHttpHeaders = {},
): Promise<{ statusCode: number; json: unknown; headers: http.IncomingHttpHeaders }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      { host: "127.0.0.1", port: address.port, method, path, headers },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({
            statusCode: res.statusCode ?? 0,
            json: raw ? JSON.parse(raw) : null,
            headers: res.headers,
          });
        });
      },
    );
    req.on("error", reject);
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

test("GET /v1/sessions without a token is rejected with the structured 401", async () => {
  await withServer(async (server) => {
    const res = await request(server, "GET", "/v1/sessions");
    assert.equal(res.statusCode, 401);
    const body = res.json as { ok: boolean; error: { code: string; message: string } };
    assert.equal(body.ok, false);
    assert.equal(body.error.code, "auth.desktop_token_invalid");
  });
});

test("a wrong token is rejected in constant time without detail leakage", async () => {
  await withServer(async (server) => {
    const res = await request(server, "GET", "/v1/sessions", {
      "x-aiming-cookie-desktop-token": "wrong-token",
    });
    assert.equal(res.statusCode, 401);
    assert.equal(
      (res.json as { error: { code: string } }).error.code,
      "auth.desktop_token_invalid",
    );
  });
});

test("the correct token header unlocks business routes", async () => {
  await withServer(async (server) => {
    const res = await request(server, "GET", "/v1/sessions", {
      "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
    });
    assert.equal(res.statusCode, 200);
  });
});

test("an unset token env fails closed even for a matching attempt", async () => {
  const original = process.env.AIMING_COOKIE_DESKTOP_TOKEN;
  delete process.env.AIMING_COOKIE_DESKTOP_TOKEN;
  await withServer(async (server) => {
    const anonymous = await request(server, "GET", "/v1/sessions");
    assert.equal(anonymous.statusCode, 401);
    const withToken = await request(server, "GET", "/v1/sessions", {
      "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
    });
    assert.equal(withToken.statusCode, 401);
  }).finally(() => {
    if (original !== undefined) process.env.AIMING_COOKIE_DESKTOP_TOKEN = original;
  });
});

test("/healthz stays open without a token", async () => {
  await withServer(async (server) => {
    const res = await request(server, "GET", "/healthz");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, { ok: true });
  });
});

test("CORS preflight answers 204 without a token and carries the token allow-header", async () => {
  await withServer(async (server) => {
    const res = await request(server, "OPTIONS", "/v1/sessions", {
      Origin: "http://tauri.localhost",
      "Access-Control-Request-Method": "POST",
    });
    assert.equal(res.statusCode, 204);
    assert.equal(res.headers["access-control-allow-origin"], "http://tauri.localhost");
    assert.match(
      String(res.headers["access-control-allow-headers"]),
      /x-aiming-cookie-desktop-token/,
    );
  });
});

test("ACAO is echoed only for whitelisted origins", async () => {
  await withServer(async (server) => {
    const allowed = await request(server, "GET", "/v1/sessions", {
      Origin: "http://localhost:3000",
      "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
    });
    assert.equal(allowed.statusCode, 200);
    assert.equal(allowed.headers["access-control-allow-origin"], "http://localhost:3000");

    const evil = await request(server, "GET", "/v1/sessions", {
      Origin: "http://evil.example",
      "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
    });
    assert.equal(evil.statusCode, 200);
    assert.equal(evil.headers["access-control-allow-origin"], undefined);

    const noOrigin = await request(server, "GET", "/v1/sessions", {
      "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
    });
    assert.equal(noOrigin.statusCode, 200);
    assert.equal(noOrigin.headers["access-control-allow-origin"], undefined);
  });
});

test("the SSE stream route accepts the same token via query parameter", async () => {
  await withServer(async (server) => {
    // 无 token：闸门 401（先于路由，run 不存在也看不到 404）。
    const denied = await request(server, "GET", "/v1/agent-runs/nope/stream");
    assert.equal(denied.statusCode, 401);
    // 错 token query：仍然 401。
    const wrong = await request(server, "GET", "/v1/agent-runs/nope/stream?desktop_token=bad");
    assert.equal(wrong.statusCode, 401);
    // 正确 query token：过闸进入路由（run 不存在 → 404 而非 401）。
    const allowed = await request(
      server,
      "GET",
      `/v1/agent-runs/nope/stream?desktop_token=${encodeURIComponent(DESKTOP_TEST_TOKEN)}`,
    );
    assert.equal(allowed.statusCode, 404);
    // query token 兜底仅限 /stream 路由：其他路径不带头仍是 401。
    const otherRoute = await request(
      server,
      "GET",
      `/v1/sessions?desktop_token=${encodeURIComponent(DESKTOP_TEST_TOKEN)}`,
    );
    assert.equal(otherRoute.statusCode, 401);
  });
});
