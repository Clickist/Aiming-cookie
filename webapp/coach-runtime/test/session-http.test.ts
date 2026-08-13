import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

process.env.DATA_ROOT = mkdtempSync(join(tmpdir(), "aiming-cookie-http-test-"));

const { createSidecarServer } = await import("../src/sidecar-server.ts");

function request(server: http.Server, method: string, path: string, body?: string): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") { reject(new Error("not listening")); return; }
    const req = http.request(
      { host: "127.0.0.1", port: address.port, method, path, headers: { "X-User-Id": "test" } },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c) => chunks.push(c));
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

test("session routes keep their HTTP shapes over the Pi session store", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const created = await request(server, "POST", "/v1/sessions", JSON.stringify({ title: "测试会话" }));
    assert.equal(created.statusCode, 201);
    const session = created.json as { id: number; title: string; status: string; message_count: number; last_message_preview: string | null };
    assert.equal(session.status, "active");
    assert.equal(session.title, "测试会话");
    assert.equal(session.message_count, 0);

    const listed = await request(server, "GET", "/v1/sessions");
    assert.equal(listed.statusCode, 200);
    const listBody = listed.json as { schema_version: string; sessions: unknown[] };
    assert.equal(listBody.schema_version, "coach_session_list.v1");
    assert.ok(listBody.sessions.some((s) => (s as { id: number }).id === session.id));

    const detail = await request(server, "GET", `/v1/sessions/${session.id}`);
    assert.equal(detail.statusCode, 200);
    assert.ok(Array.isArray((detail.json as { messages: unknown[] }).messages));

    const patched = await request(server, "PATCH", `/v1/sessions/${session.id}`, JSON.stringify({ status: "archived" }));
    assert.equal(patched.statusCode, 200);
    assert.equal((patched.json as { status: string }).status, "archived");

    const deleted = await request(server, "DELETE", `/v1/sessions/${session.id}`);
    assert.equal(deleted.statusCode, 200);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
    rmSync(process.env.DATA_ROOT!, { recursive: true, force: true });
  }
});
