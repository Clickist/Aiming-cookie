import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-session-test-"));
process.env.DATA_ROOT = dataRoot;

const { getConversationsDir } = await import("../src/app-data.ts");
const {
  createCoachSession,
  getCoachSessionDetail,
  listCoachSessions,
} = await import("../src/sidecar-coach-data.ts");
const {
  ensureSession,
  listSessionIds,
  nextSessionIdSync,
  readSessionMessages,
} = await import("../src/session-repo.ts");
const { wrapCoachSession } = await import("../src/turn.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("session-repo creates, persists and reads Pi sessions", async () => {
  const session = await ensureSession(1);
  await session.appendMessage({ role: "user", content: [{ type: "text", text: "你好" }], timestamp: Date.now() });
  await session.appendMessage({ role: "assistant", content: [{ type: "text", text: "回复" }], timestamp: Date.now() });
  const messages = await readSessionMessages(1);
  assert.deepEqual(messages.map((m) => [m.role, m.content]), [
    ["user", "你好"],
    ["assistant", "回复"],
  ]);
  assert.ok((await listSessionIds()).includes(1));
});

test("nextSessionIdSync allocates past existing sessions", async () => {
  await ensureSession(3);
  await ensureSession(5);
  assert.equal(nextSessionIdSync(), 6);
});

test("createCoachSession + detail preserve the frontend shape", async () => {
  const created = await createCoachSession("test-owner", "会话标题");
  assert.equal(created.status, "active");
  assert.equal(created.title, "会话标题");
  const detail = await getCoachSessionDetail("test-owner", created.id);
  assert.ok(Array.isArray(detail.messages));
  assert.equal(detail.message_count, 0);
  const listed = await listCoachSessions("test-owner");
  assert.ok(listed.sessions.some((s) => s.id === created.id));
});

test("legacy plain JSONL conversation is migrated on first access", async () => {
  const dir = getConversationsDir();
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, "42.jsonl"),
    JSON.stringify({ role: "user", content: "旧消息", timestamp: Date.now() }) + "\n" +
      JSON.stringify({ role: "assistant", content: "旧回复", timestamp: Date.now() }) + "\n",
    "utf8",
  );
  await ensureSession(42);
  const messages = await readSessionMessages(42);
  assert.deepEqual(messages.map((m) => [m.role, m.content]), [
    ["user", "旧消息"],
    ["assistant", "旧回复"],
  ]);
  assert.ok(existsSync(join(dir, "--coach--")));
});

test("wrapped session skips the duplicate current user message", async () => {
  const session = await ensureSession(100);
  await session.appendMessage({ role: "user", content: [{ type: "text", text: "你好" }], timestamp: Date.now() });
  const wrapped = wrapCoachSession(session, []);
  const ctx = await wrapped.buildContext();
  assert.equal(ctx.messages.length, 0);
  await wrapped.appendMessage({ role: "user", content: [{ type: "text", text: "你好" }], timestamp: Date.now() });
  const msgs = await readSessionMessages(100);
  assert.equal(msgs.length, 1);
});

test("wrapped session redacts assistant replies and skips failures", async () => {
  const session = await ensureSession(101);
  await session.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }], timestamp: Date.now() });
  const wrapped = wrapCoachSession(session, ["TOP-SECRET"]);
  await wrapped.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "key is TOP-SECRET ok" }],
    timestamp: Date.now(),
    stopReason: "stop",
  });
  await wrapped.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "" }],
    timestamp: Date.now(),
    stopReason: "error",
  });
  const msgs = await readSessionMessages(101);
  assert.equal(msgs.length, 2);
  assert.equal(msgs[1].content, "key is [REDACTED] ok");
});

test("wrapped session truncates buildContext to the recent window", async () => {
  const session = await ensureSession(102);
  for (let i = 0; i < 50; i++) {
    await session.appendMessage({ role: "user", content: [{ type: "text", text: `u${i}` }], timestamp: Date.now() });
    await session.appendMessage({ role: "assistant", content: [{ type: "text", text: `a${i}` }], timestamp: Date.now() });
  }
  const wrapped = wrapCoachSession(session, []);
  const ctx = await wrapped.buildContext();
  assert.equal(ctx.messages.length, 40);
  assert.equal((ctx.messages[0] as { content: Array<{ text: string }> }).content[0].text, "u30");
  assert.equal(
    (ctx.messages[ctx.messages.length - 1] as { content: Array<{ text: string }> }).content[0].text,
    "a49",
  );
});
