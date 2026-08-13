import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { createSidecarServer } from "../src/sidecar-server.ts";

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
  headers?: http.OutgoingHttpHeaders,
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
          ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body), ...headers }
          : headers,
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

function withServer(fn: (server: http.Server) => Promise<void>): Promise<void> {
  const server = createSidecarServer();
  return new Promise<void>((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      try {
        await fn(server);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        server.close((err) => {
          if (err) reject(err);
        });
      }
    });
  });
}

test("POST /v1/agent-runs with valid content returns 202", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/agent-runs",
      JSON.stringify({ content: "hello", context_refs: [] }),
      { "X-User-Id": "test" });
    assert.equal(res.statusCode, 202);
    const body = res.json as { run_ref: string; status: string };
    assert.ok(body.run_ref.startsWith("agent_run:"));
    assert.equal(body.status, "queued");
  });
});

test("POST /v1/agent-runs with empty content returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/agent-runs",
      JSON.stringify({ content: "" }),
      { "X-User-Id": "test" });
    assert.equal(res.statusCode, 400);
  });
});

test("POST /v1/agent-runs/:ref/stop returns 404 for unknown run", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/agent-runs/agent_run:test/stop", undefined, { "X-User-Id": "test" });
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/agent-runs/:ref/retry returns 404 for unknown run", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/agent-runs/agent_run:test/retry", undefined, { "X-User-Id": "test" });
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/confirmations/:ref/decision returns 404 for unknown confirmation", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/confirmations/confirmation:test/decision",
      JSON.stringify({ decision: "confirm" }),
      { "X-User-Id": "test" });
    assert.equal(res.statusCode, 404);
  });
});

test("POST /v1/agent-runs with invalid JSON returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/agent-runs", "{not-json", { "X-User-Id": "test" });
    assert.equal(res.statusCode, 400);
  });
});

test("POST /v1/confirmations/:ref/decision with invalid decision returns 400", async () => {
  await withServer(async (server) => {
    const res = await request(server, "POST", "/v1/confirmations/confirmation:test/decision",
      JSON.stringify({ decision: "maybe" }),
      { "X-User-Id": "test" });
    assert.equal(res.statusCode, 400);
  });
});
