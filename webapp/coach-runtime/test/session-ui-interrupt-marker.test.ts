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

// 新用例的 label：额外区分「重启/进程死亡打断」标记。
const labelEx = (message: { role: string; content: string; stopped?: boolean; interrupted?: boolean }) =>
  `${message.role}:${message.content}${message.stopped ? "#stopped" : ""}${message.interrupted ? "#interrupted" : ""}`;

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

test("without any run record, synthesized markers are interrupted and real aborted text is only stopped", async () => {
  const threadId = 833;
  const session = await ensureSession(threadId);
  const stamp = Date.now();
  const text = (role: string, value: string) =>
    session.appendMessage({ role, content: [{ type: "text", text: value }], timestamp: stamp });
  await text("user", "u0");
  // 真实 aborted 消息：带正文的半截回复，只有 stopped、无 interrupted。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "half" }],
    stopReason: "aborted",
    timestamp: stamp,
  });
  // 尾部：有工具活动无正文的回合，重启后无内存 run 结算 → 合成标记 interrupted。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "toolCall", id: "call-tail", name: "read", arguments: {} }],
    timestamp: stamp,
  });

  assert.deepEqual(
    (await readSessionMessagesForUi(threadId)).map(labelEx),
    ["user:u0", "assistant:half#stopped", "assistant:#stopped#interrupted"],
  );
});

test("with an in-process run record, trailingTurnInProcess clears interrupted only on the trailing marker", async () => {
  const threadId = 834;
  await seedOngoingTrailingTurn(threadId);

  // 用一个 stopped run 造「本进程内结束」的记录（终态记录留在 runs Map）。
  const run = createAgentRun("marker-owner-b", "帮我热身", { sessionId: threadId });
  await waitForTask(run.run_ref);
  await stopAgentRun("marker-owner-b", run.run_ref);

  assert.deepEqual(
    (await readSessionMessagesForUi(threadId, { trailingTurnInProcess: true })).map(labelEx),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped"],
  );
});

test("trailingTurnInProcess bypasses the ui stat cache in both directions", async () => {
  // 正向：默认 opts 先读（写缓存）→ 带标志读必须反映标志，不被缓存钉死。
  const threadId = 835;
  await seedOngoingTrailingTurn(threadId);
  assert.deepEqual(
    (await readSessionMessagesForUi(threadId)).map(labelEx),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped#interrupted"],
  );
  assert.deepEqual(
    (await readSessionMessagesForUi(threadId, { trailingTurnInProcess: true })).map(labelEx),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped"],
  );

  // 反向：带标志读先行且不得写缓存 → 随后的默认读不得吃到带标志的合成结果。
  const freshId = 836;
  await seedOngoingTrailingTurn(freshId);
  assert.deepEqual(
    (await readSessionMessagesForUi(freshId, { trailingTurnInProcess: true })).map(labelEx),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped"],
  );
  assert.deepEqual(
    (await readSessionMessagesForUi(freshId)).map(labelEx),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped#interrupted"],
  );
});

test("getCoachSessionDetail marks the trailing tool-only turn as interrupted after a restart", async () => {
  // 无内存 run（模拟重启后）：detail 须对无正文的尾部回合如实带 interrupted。
  const threadId = 837;
  await seedOngoingTrailingTurn(threadId);

  const detail = await getCoachSessionDetail("marker-owner-d", threadId);
  assert.deepEqual(
    detail.messages.map((message) => labelEx(message as { role: string; content: string; interrupted?: boolean; stopped?: boolean })),
    ["user:u0", "assistant:a0", "user:u1", "assistant:#stopped", "user:u2", "assistant:#stopped#interrupted"],
  );
});

test("a turn cut off mid-text (partial reply + open tool activity) also gets an interrupted marker", async () => {
  // 1012 报障精确形态：回复已写出半截正文 + 工具调用，进程死在工具阶段。
  // 此前该形态不合成任何标记，重启后就是"话说到一半、没有任何提示"。
  const threadId = 838;
  const session = await ensureSession(threadId);
  const stamp = Date.now();
  const text = (role: string, value: string) =>
    session.appendMessage({ role, content: [{ type: "text", text: value }], timestamp: stamp });
  await text("user", "u0");
  await text("assistant", "a0");
  await text("user", "分析这局");
  // 半截话 + 工具调用（进程在这里被杀）
  await session.appendMessage({
    role: "assistant",
    content: [
      { type: "text", text: "我再读一下事件级数据" },
      { type: "toolCall", id: "call-read", name: "read", arguments: {} },
    ],
    timestamp: stamp,
  });
  await session.appendMessage({
    role: "toolResult",
    toolCallId: "call-read",
    toolName: "read",
    content: [{ type: "text", text: "…大文件…" }],
    timestamp: stamp,
  });

  assert.deepEqual(
    (await readSessionMessagesForUi(threadId)).map(labelEx),
    [
      "user:u0",
      "assistant:a0",
      "user:分析这局",
      "assistant:我再读一下事件级数据",
      "assistant:#stopped#interrupted",
    ],
  );

  // 本进程内（run 记录在内存）→ 尾标记不算重启打断（进行中中间态）。
  assert.deepEqual(
    (await readSessionMessagesForUi(threadId, { trailingTurnInProcess: true })).map(labelEx),
    [
      "user:u0",
      "assistant:a0",
      "user:分析这局",
      "assistant:我再读一下事件级数据",
      "assistant:#stopped",
    ],
  );
});

test("mid-text cut-off marker settles at the next user turn and does not fire for completed or stopped turns", async () => {
  const threadId = 839;
  const session = await ensureSession(threadId);
  const stamp = Date.now();
  const text = (role: string, value: string) =>
    session.appendMessage({ role, content: [{ type: "text", text: value }], timestamp: stamp });
  await text("user", "u0");
  // 历史回合：半截话 + 工具调用后被下一条 user 结算 → 永久标记。
  await session.appendMessage({
    role: "assistant",
    content: [
      { type: "text", text: "旧半截话" },
      { type: "toolCall", id: "call-old", name: "read", arguments: {} },
    ],
    timestamp: stamp,
  });
  await text("user", "u1");
  // 正常完成回合：带工具调用但最终有纯文本终稿 → 不标。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "先读数据" }, { type: "toolCall", id: "call-ok", name: "read", arguments: {} }],
    timestamp: stamp,
  });
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "这是最终结论" }],
    timestamp: stamp,
  });
  await text("user", "u2");
  // 用户停止：aborted 半截话 → 只标 stopped，不标 interrupted。
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "被打断前的话" }],
    stopReason: "aborted",
    timestamp: stamp,
  });

  assert.deepEqual(
    (await readSessionMessagesForUi(threadId)).map(labelEx),
    [
      "user:u0",
      "assistant:旧半截话",
      "assistant:#stopped",
      "user:u1",
      "assistant:先读数据",
      "assistant:这是最终结论",
      "user:u2",
      "assistant:被打断前的话#stopped",
    ],
  );
});
