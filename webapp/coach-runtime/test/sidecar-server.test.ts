import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

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
          ? {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            }
          : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({
            statusCode: res.statusCode ?? 0,
            json: raw ? JSON.parse(raw) : null,
          });
        });
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

test("GET /healthz returns ok", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "GET", "/healthz");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, { ok: true });
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v0/turn with invalid JSON returns 400", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v0/turn", "{not-json");
    assert.equal(res.statusCode, 400);
    assert.equal((res.json as { ok: boolean }).ok, false);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});