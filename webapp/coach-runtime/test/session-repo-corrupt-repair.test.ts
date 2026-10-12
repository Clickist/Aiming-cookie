import assert from "node:assert/strict";
import { appendFileSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-session-corrupt-"));
process.env.DATA_ROOT = dataRoot;

const { getConversationsDir } = await import("../src/app-data.ts");
const {
  ensureSession,
  readSessionMessages,
  sessionExists,
  deleteSessionFile,
} = await import("../src/session-repo.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

function coachDir(): string {
  return join(getConversationsDir(), "--coach--");
}

function sessionFilePath(threadId: number): string {
  const name = readdirSync(coachDir()).find((file) => file.endsWith(`_${threadId}.jsonl`));
  if (!name) throw new Error(`session file for ${threadId} not found`);
  return join(coachDir(), name);
}

function corruptQuarantineFiles(threadId?: number): string[] {
  if (!existsSync(coachDir())) return [];
  const files = readdirSync(coachDir()).filter((file) => file.includes(".corrupt-"));
  return threadId === undefined ? files : files.filter((file) => file.includes(`_${threadId}.jsonl.corrupt-`));
}

async function appendUserMessage(threadId: number, text: string): Promise<void> {
  const session = await ensureSession(threadId);
  await session.appendMessage({ role: "user", content: [{ type: "text", text }], timestamp: Date.now() });
}

test("a torn trailing line no longer bricks the session: quarantine + truncate rebuild", async () => {
  const threadId = 8101;
  await appendUserMessage(threadId, "第一问");
  await appendUserMessage(threadId, "第二问");
  // 模拟进程死在 appendFile 中途：尾部追加半行 JSON（无换行收尾）。
  appendFileSync(sessionFilePath(threadId), '{"type":"message","id":"torn', "utf8");

  // 修复前该会话 open 抛 invalid_entry、整段对话不可读（回归红线：修复后必须可读）。
  const messages = await readSessionMessages(threadId);
  assert.deepEqual(
    messages.map((message) => [message.role, message.content]),
    [["user", "第一问"], ["user", "第二问"]],
  );

  // 撕裂内容隔离留痕，绝不静默蒸发。
  const quarantined = corruptQuarantineFiles(threadId);
  assert.equal(quarantined.length, 1, `expected one quarantine file, got ${JSON.stringify(quarantined)}`);
  assert.ok(readFileSync(join(coachDir(), quarantined[0]!), "utf8").includes('{"type":"message","id":"torn'));

  // 修复后会话立即可继续追加（turn 路径依赖）。
  await appendUserMessage(threadId, "第三问");
  const messagesAfter = await readSessionMessages(threadId);
  assert.equal(messagesAfter.length, 3);

  // cause 码落 coach-error.log（降级可观测合同）。
  const log = readFileSync(join(dataRoot, "coach-error.log"), "utf8");
  assert.match(log, /corrupt_session_repaired .*cause=invalid_entry/);
});

test("a corrupt middle line truncates the session at the last fully valid prefix", async () => {
  const threadId = 8102;
  await appendUserMessage(threadId, "保留我");
  await appendUserMessage(threadId, "在我之后丢");
  // 把垃圾行插进中间（同步盘冲突拷贝形态）：第一行之后。
  const filePath = sessionFilePath(threadId);
  const lines = readFileSync(filePath, "utf8").split("\n");
  lines.splice(2, 0, "{{{ not json at all");
  writeFileSync(filePath, lines.join("\n"), "utf8");

  const messages = await readSessionMessages(threadId);
  assert.deepEqual(
    messages.map((message) => message.content),
    ["保留我"],
  );
  assert.equal(corruptQuarantineFiles(threadId).length, 1);
  const log = readFileSync(join(dataRoot, "coach-error.log"), "utf8");
  assert.match(log, /corrupt_session_repaired .*cause=invalid_entry/);
});

test("an unreadable header quarantines the whole file so the session can be recreated", async () => {
  const threadId = 8103;
  await appendUserMessage(threadId, "旧的");
  const filePath = sessionFilePath(threadId);
  // 直接绕过 list 写坏 header：openSession 收不到 metadata，但自愈入口仍按
  // 不可修复处理（整文件隔离）。走 detail 层验证不砖化：openSession 返回 null、
  // ensureSession 以同一 id 重建。
  writeFileSync(filePath, "not a header at all\n", "utf8");
  const { openSession } = await import("../src/session-repo.ts");
  // header 损坏的文件 repo.list 不可见（pi 跳过），openSession 走不到 repair——
  // 这里直改文件内容验证的是「open 阶段撞上 invalid_session 也要自愈」，通过
  // 直接以 metadata 打开不可行，故只验证可观测行为：会话仍可重建、不抛砖。
  assert.equal(await openSession(threadId), null);
  assert.equal(await sessionExists(threadId), false);
  await appendUserMessage(threadId, "新的");
  assert.equal(await sessionExists(threadId), true);
  const messages = await readSessionMessages(threadId);
  assert.deepEqual(messages.map((message) => message.content), ["新的"]);
});

test("deleting a session clears the lookup caches so it disappears immediately", async () => {
  const threadId = 8104;
  await appendUserMessage(threadId, "临别的");
  assert.equal(await sessionExists(threadId), true);
  await deleteSessionFile(threadId);
  assert.equal(await sessionExists(threadId), false);
  assert.deepEqual(await readSessionMessages(threadId), []);
});

test("ui message reads are cached per file stat and bust on append", async () => {
  const threadId = 8105;
  const { readSessionMessagesForUi } = await import("../src/session-repo.ts");
  await appendUserMessage(threadId, "一");
  const first = await readSessionMessagesForUi(threadId);
  const second = await readSessionMessagesForUi(threadId);
  // 文件未变：同一解析结果直接复用（TS-4：列表轮询不再每会话全量重解析）。
  assert.equal(first, second);
  await appendUserMessage(threadId, "二");
  const third = await readSessionMessagesForUi(threadId);
  assert.deepEqual(third.map((message) => message.content), ["一", "二"]);
});
