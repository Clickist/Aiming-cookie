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

test("wrapped session persists assistant text even when the provider errored mid-turn", async () => {
  const session = await ensureSession(103);
  await session.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }], timestamp: Date.now() });
  const wrapped = wrapCoachSession(session, []);
  // Regression: a stream that errors after emitting text used to be dropped,
  // so the UI showed a reply the persisted session never kept.
  await wrapped.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "被中断前已经生成的正文" }],
    timestamp: Date.now(),
    stopReason: "error",
  });
  const msgs = await readSessionMessages(103);
  assert.equal(msgs.length, 2);
  assert.equal(msgs[1].content, "被中断前已经生成的正文");
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

test("wrapped session enforces the char budget on top of the message window", async () => {
  const session = await ensureSession(105);
  const stamp = Date.now();
  const append = (role: string, text: string) =>
    session.appendMessage({ role, content: [{ type: "text", text }], timestamp: stamp });
  // 三对小型问答 + 一条巨型 toolResult + 一对收尾问答。全部都在 40 条窗口内，
  // 触发的是字符预算而不是条数上限。
  await append("user", "u0");
  await append("assistant", "a0");
  await append("user", "u1");
  await append("assistant", "a1");
  const giant = "G".repeat(150_000);
  await session.appendMessage({
    role: "toolResult",
    toolCallId: "call-1",
    toolName: "read",
    content: [{ type: "text", text: giant }],
    isError: false,
    timestamp: stamp,
  });
  await append("user", "u2");
  await append("assistant", "a2");
  await append("user", "current");

  const wrapped = wrapCoachSession(session, []);
  const ctx = await wrapped.buildContext();
  const roles = ctx.messages.map((m) => (m as { role?: string }).role ?? "");
  const serialized = JSON.stringify(ctx.messages);

  // 巨型 toolResult 独超预算且不在最新位置 → 连同更旧消息一起被整条逐出，
  // 只留放得下的最新消息；头部对齐保证第一条是 user，不会出现孤立
  // toolResult/assistant(tool_calls) 开头。
  assert.ok(!serialized.includes("GGGG"), "oversized mid-history tool result should be evicted");
  assert.ok(!serialized.includes("a0"), "oldest small messages should be dropped");
  assert.ok(roles[0] === "user", `first message should align to user, got ${roles[0]}`);
  assert.equal(roles[roles.length - 1], "assistant");

  // 极端：单条消息独超预算（修复前遗留的旧会话才可能出现）。预算保住最新
  // 一条后，头部对齐会把非 user/system 开头的孤立 toolResult 丢掉——最终
  // 上下文为空也不崩溃、不带巨型载荷，回合由 harness 的当前 user 消息兜底。
  const freshSession = await ensureSession(107);
  await freshSession.appendMessage({ role: "user", content: [{ type: "text", text: "u0" }], timestamp: stamp });
  await freshSession.appendMessage({
    role: "toolResult",
    toolCallId: "call-2",
    toolName: "read",
    content: [{ type: "text", text: giant }],
    isError: false,
    timestamp: stamp,
  });
  await freshSession.appendMessage({ role: "user", content: [{ type: "text", text: "current" }], timestamp: stamp });
  const freshCtx = await wrapCoachSession(freshSession, []).buildContext();
  assert.ok(!JSON.stringify(freshCtx.messages).includes("GGGG"), "oversized payload never reaches the provider");
  assert.ok(Array.isArray(freshCtx.messages));

  // 小对话不受预算影响：40 条窗口内全保留。
  const smallSession = await ensureSession(106);
  for (let i = 0; i < 6; i++) {
    await smallSession.appendMessage({ role: "user", content: [{ type: "text", text: `s-u${i}` }], timestamp: stamp });
    await smallSession.appendMessage({ role: "assistant", content: [{ type: "text", text: `s-a${i}` }], timestamp: stamp });
  }
  await smallSession.appendMessage({ role: "user", content: [{ type: "text", text: "current" }], timestamp: stamp });
  const smallCtx = await wrapCoachSession(smallSession, []).buildContext();
  const smallSerialized = JSON.stringify(smallCtx.messages);
  assert.ok(smallSerialized.includes("s-u0"), "small conversations keep full history");
  assert.ok(smallSerialized.includes("s-a5"));
});
