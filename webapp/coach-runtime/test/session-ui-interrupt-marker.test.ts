import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-session-ui-marker-"));
process.env.DATA_ROOT = dataRoot;

const { createAgentRun, getAgentRun, stopAgentRun } = await import("../src/agent-runs.ts");
const { getCoachSessionDetail } = await import("../src/sidecar-coach-data.ts");
const { ensureSession, readSessionMessagesForUi } = await import("../src/session-repo.ts");
const { waitForTask } = await import("../src/task-manager.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

const label = (message: { role: string; content: string; stopped?: boolean }) =>
  `${message.role}:${message.content}${message.stopped ? "#stopped" : ""}`;

// 会话末尾是「有工具活动但无正文」的 assistant 回合；前面另有一个真停止回合。
async function seedOngoingTrailingTurn(threadId: number): Promise<void> {
  const session = await ensureSession(threadId);
  const stamp = Date.now();
  const text = (role: string, value: string) =>
    session.appendMessage({ role, content: [{ type: "text", text: value }], timestamp: stamp });
  await text("user", "u0");
  await text("assistant", "a0");
  await text("user", "u1");
  // 真停止的历史回合：被下一条 user 边界结算，标记必须永久保留。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "toolCall", id: "call-old", name: "read", arguments: {} }],
    stopReason: "aborted",
    timestamp: stamp,
  });
  await text("user", "u2");
  // 进行中的尾部回合：run 活着时它是中间态，不该打 stopped 标记。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "toolCall", id: "call-live", name: "read", arguments: {} }],
    timestamp: stamp,
  });
}

test("suppressTrailingInterruptMarker drops only the trailing marker, keeping historical ones", async () => {
  const threadId = 831;
  await seedOngoingTrailingTurn(threadId);

  assert.deepEqual(
    (await readSessionMessagesForUi(threadId)).map(label),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped"],
  );
  assert.deepEqual(
    (await readSessionMessagesForUi(threadId, { suppressTrailingInterruptMarker: true })).map(label),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2"],
  );
});

test("getCoachSessionDetail suppresses the trailing marker while a run is active and restores it after stop", async () => {
  const threadId = 832;
  await seedOngoingTrailingTurn(threadId);

  // 无 Provider 时 run 停在 queued（等待恢复），不写会话，但仍是活跃 run。
  const run = createAgentRun("marker-owner", "帮我安排一个训练计划", { sessionId: threadId });
  await waitForTask(run.run_ref);
  assert.equal(getAgentRun("marker-owner", run.run_ref)?.status, "queued");

  const active = await getCoachSessionDetail("marker-owner", threadId);
  assert.deepEqual(
    active.messages.map((message) => label(message as { role: string; content: string; stopped?: boolean })),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2"],
    "活跃 run 期间不得合成「回答已停止」尾标记",
  );

  // run 终结后该回合不再进行中，真停止语义恢复（标记应重新出现）。
  await stopAgentRun("marker-owner", run.run_ref);
  const settled = await getCoachSessionDetail("marker-owner", threadId);
  assert.deepEqual(
    settled.messages.map((message) => label(message as { role: string; content: string; stopped?: boolean })),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped"],
  );
});
