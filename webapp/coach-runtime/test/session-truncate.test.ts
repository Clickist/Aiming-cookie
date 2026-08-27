import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-session-truncate-test-"));
process.env.DATA_ROOT = dataRoot;

const { createSidecarServer } = await import("../src/sidecar-server.ts");
const { getCoachSessionDetail, truncateCoachSession } = await import("../src/sidecar-coach-data.ts");
const { ensureSession, readSessionMessages, truncateSessionFromMessage } = await import("../src/session-repo.ts");

function request(server: http.Server, method: string, path: string, body?: string): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: "127.0.0.1",
        port: (server.address() as { port: number }).port,
        method,
        path,
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
        });
      },
    );
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

async function withServer(fn: (server: http.Server) => Promise<void>): Promise<void> {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    await fn(server);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
}

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

async function seedConversation(threadId: number): Promise<void> {
  const session = await ensureSession(threadId);
  const turns: Array<[string, string]> = [
    ["user", "第一条"],
    ["assistant", "回复一"],
    ["user", "第二条"],
    ["assistant", "回复二"],
    ["user", "第三条"],
    ["assistant", "回复三"],
  ];
  for (const [role, text] of turns) {
    await session.appendMessage({ role, content: [{ type: "text", text }], timestamp: Date.now() });
  }
}

test("truncate keeps the prefix and drops the tail on disk state, single-session compatible", async () => {
  await seedConversation(810);
  await truncateSessionFromMessage(810, 2);
  // readSessionMessages walks the branch from leaf: 前两条保留，其余不再参与
  assert.deepEqual((await readSessionMessages(810)).map((m) => m.content), ["第一条", "回复一"]);
  // 详情合同同步反映截断结果
  const detail = await getCoachSessionDetail("desktop-local", 810);
  assert.equal(detail.messages.length, 2);

  // 后续发送仍然可用：appendMessage 沿新 leaf 组链，历史保持单线
  const reopened = await ensureSession(810);
  await reopened.appendMessage({
    role: "user",
    content: [{ type: "text", text: "编辑后的第一条" }],
    timestamp: Date.now(),
  });
  const afterResend = await readSessionMessages(810);
  assert.deepEqual(afterResend.map((m) => m.content), ["第一条", "回复一", "编辑后的第一条"]);
});

test("truncate to zero empties the conversation and accepts a fresh thread", async () => {
  await seedConversation(811);
  await truncateSessionFromMessage(811, 0);
  assert.deepEqual(await readSessionMessages(811), []);
  const reopened = await ensureSession(811);
  await reopened.appendMessage({ role: "user", content: [{ type: "text", text: "重新开始" }], timestamp: Date.now() });
  assert.deepEqual((await readSessionMessages(811)).map((m) => m.content), ["重新开始"]);
});

test("truncate beyond current length is an idempotent no-op", async () => {
  await seedConversation(812);
  await truncateSessionFromMessage(812, 99);
  assert.equal((await readSessionMessages(812)).length, 6);
  await truncateSessionFromMessage(813, 0); // 不存在的会话：no-op 而非抛错
});

test("truncateCoachSession route wrapper returns the refreshed detail and enforces contracts", async () => {
  await seedConversation(814);
  const detail = await truncateCoachSession("desktop-local", 814, 4);
  assert.deepEqual(detail.messages.map((m) => m.content), ["第一条", "回复一", "第二条", "回复二"]);

  await assert.rejects(
    () => truncateCoachSession("desktop-local", 814, -1),
    /keep_messages/,
  );
  await assert.rejects(
    () => truncateCoachSession("desktop-local", Number.NaN, 0),
    /session id/,
  );
  await assert.rejects(
    () => truncateCoachSession("desktop-local", 999999, 0),
    (error: unknown) => error instanceof Error && /unavailable/.test(error.message),
  );
});

test("POST /v1/sessions/:id/truncate answers 200 with refreshed detail and explicit errors", async () => {
  await seedConversation(820);
  await withServer(async (server) => {
    const ok = await request(server, "POST", "/v1/sessions/820/truncate", JSON.stringify({ keep_messages: 2 }));
    assert.equal(ok.statusCode, 200);
    assert.deepEqual((ok.json as { messages: Array<{ content: string }> }).messages.map((m) => m.content), [
      "第一条",
      "回复一",
    ]);

    const invalidKeep = await request(server, "POST", "/v1/sessions/820/truncate", JSON.stringify({ keep_messages: -2 }));
    assert.equal(invalidKeep.statusCode, 400);

    const unknownSession = await request(server, "POST", "/v1/sessions/999999/truncate", JSON.stringify({ keep_messages: 0 }));
    assert.equal(unknownSession.statusCode, 404);

    const malformed = await request(server, "POST", "/v1/sessions/820/truncate", "{not-json");
    assert.equal(malformed.statusCode, 400);

    const emptyBody = await request(server, "POST", "/v1/sessions/820/truncate");
    assert.equal(emptyBody.statusCode, 400);
  });
});
