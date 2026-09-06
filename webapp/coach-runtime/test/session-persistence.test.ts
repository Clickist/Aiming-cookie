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
const { wrapCoachSession, CLEARED_TOOL_RESULT_PLACEHOLDER: CLEAR_PLACEHOLDER } = await import("../src/turn.ts");

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

test("wrapped session clears stale tool results Claude-Code style instead of dropping messages", async () => {
  const session = await ensureSession(105);
  const stamp = Date.now();
  const append = (role: string, text: string) =>
    session.appendMessage({ role, content: [{ type: "text", text }], timestamp: stamp });
  const giantToolResult = (callId: string, ch: string) =>
    session.appendMessage({
      role: "toolResult",
      toolCallId: callId,
      toolName: "read",
      content: [{ type: "text", text: ch.repeat(150_000) }],
      isError: false,
      timestamp: stamp,
    });
  // 对话 + 4 条巨型 toolResult（共 ~600K 字符 > 400K 触发阈值）。
  // 清除语义：最旧 1 条被占位符替换（keep=3），其余全部保留；对话文本一条不丢。
  await append("user", "u0");
  await append("assistant", "a0");
  await giantToolResult("call-G", "G");
  await append("user", "u1");
  await append("assistant", "a1");
  await giantToolResult("call-H", "H");
  await append("user", "u2");
  await append("assistant", "a2");
  await giantToolResult("call-I", "I");
  await append("user", "u3");
  await append("assistant", "a3");
  await giantToolResult("call-J", "J");
  await append("user", "u4");
  await append("assistant", "a4");
  await append("user", "current");

  const wrapped = wrapCoachSession(session, []);
  const ctx = await wrapped.buildContext();
  const roles = ctx.messages.map((m) => (m as { role?: string }).role ?? "");
  const serialized = JSON.stringify(ctx.messages);

  // 最旧的巨型 toolResult 被替换为占位符，最近 3 条完整保留。
  assert.ok(!serialized.includes("GGGG"), "stalest oversized tool result should be cleared");
  assert.ok(serialized.includes(CLEAR_PLACEHOLDER), "clearing placeholder should be present");
  assert.ok(serialized.includes("HHHH"), "kept tool result H stays intact");
  assert.ok(serialized.includes("IIII"), "kept tool result I stays intact");
  assert.ok(serialized.includes("JJJJ"), "kept tool result J stays intact");
  // 对话文本一条不丢（microcompact 与旧逐出方案的核心差异）。
  for (const t of ["u0", "a0", "u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4"]) {
    assert.ok(serialized.includes(`"${t}"`), `conversation text ${t} must survive`);
  }
  // 条数与序列不变：清除不改消息条数/角色，天然无孤立 tool 开头。
  assert.equal(roles.filter((r) => r === "toolResult").length, 4);
  assert.equal(roles[0], "user");
  assert.equal(roles[roles.length - 1], "assistant");

  // 未超阈值的纯小对话：一切原样，零行为变化。
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
  assert.ok(!smallSerialized.includes("cleared"), "no clearing below the trigger threshold");
});
