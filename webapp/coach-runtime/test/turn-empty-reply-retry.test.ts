// B1 空回复受控重试（2026-10-01 12 机诊断定罪）：模型偶发正常结束但零正文，
// turn 层同 harness 受控重试恰好一次（nudge 提示词，照抄 next_turn 排水模式）。
// 锁定四件事：
// 1. 空回复 → 重试一次 → 成功，且 provider 恰好被调两次；
// 2. 重试仍空 → 走既有失败路径，userFacing 是中文分层文案（不再漏英文裸串）；
// 3. nudge 不落会话历史（重试请求在内存里携带，会话里不留假用户消息）；
// 4. 重试请求的 LLM 消息列表不携带空 assistant 条目（pi 转换层对无正文且无
//    tool_calls 的 assistant 条目整条跳过，空回复也不落会话——二次 422 无从发生）。
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-empty-reply-retry-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun } from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import { readSessionMessages } from "../src/session-repo.ts";
import { waitForTask } from "../src/task-manager.ts";
import { loadPiAi } from "../src/pi-source.ts";
import { streamAssistant } from "./pi-fake-stream.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "empty-reply-test-key" },
});

const NUDGE_MARKER = "上一条回复内容为空";

/** text 块拼接（与 turn.ts extractAssistantText 同判据的测试简化版）。 */
function assistantText(message: unknown): string {
  const record = message as { role?: unknown; content?: unknown };
  if (!record || record.role !== "assistant" || !Array.isArray(record.content)) return "";
  return record.content
    .map((block) =>
      block && typeof block === "object" && (block as { type?: unknown }).type === "text"
        ? String((block as { text?: unknown }).text ?? "")
        : "")
    .join("")
    .trim();
}

/** LLM 消息列表里是否存在「无正文且无 tool_calls」的空 assistant 条目。 */
function hasEmptyAssistantEntry(messages: unknown[]): boolean {
  return messages.some((message) => {
    const record = message as { role?: unknown; content?: unknown };
    if (!record || record.role !== "assistant" || !Array.isArray(record.content)) return false;
    const hasText = assistantText(record).length > 0;
    const hasToolCalls = record.content.some(
      (block) => block && typeof block === "object" && (block as { type?: unknown }).type === "toolCall",
    );
    return !hasText && !hasToolCalls;
  });
}

/** stopReason=error 且带 errorMessage 的空回复流（网关错误体透传形态）。 */
async function errorAssistant(errorMessage: string) {
  const ai = await loadPiAi();
  const createStream = ai.createAssistantMessageEventStream as () => {
    push(event: unknown): void;
    end(result: unknown): void;
  };
  const stream = createStream();
  const final = {
    role: "assistant" as const,
    content: [] as unknown[],
    api: "openai-completions",
    provider: "aiming-cookie-coach-e2e",
    model: "fixture-model",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0 },
    stopReason: "error" as const,
    errorMessage,
    timestamp: 0,
  };
  queueMicrotask(() => {
    stream.push({ type: "start", partial: final });
    stream.push({ type: "done", reason: "error", message: final });
    stream.end(final);
  });
  return stream;
}

test("empty reply retries exactly once on the same harness and succeeds", async () => {
  let calls = 0;
  const contexts: Array<{ messages: Array<Record<string, unknown>> }> = [];
  const streamFn: StreamFn = async (_model, context) => {
    calls += 1;
    contexts.push(context as { messages: Array<Record<string, unknown>> });
    if (calls === 1) return streamAssistant([], "stop");
    return streamAssistant([{ type: "text", text: "重试后的完整回答。" }], "stop");
  };

  const created = createAgentRun("empty-reply-owner", "讲讲这局的命中率", { sessionId: 71, streamFn });
  await waitForTask(created.run_ref);

  const run = getAgentRun("empty-reply-owner", created.run_ref);
  assert.ok(run);
  assert.equal(run.status, "succeeded", `retry should succeed, error: ${JSON.stringify(run.error)}`);
  assert.equal(calls, 2, `provider should be called exactly twice (1 retry), got ${calls}`);
  assert.match(run.partial_text ?? "", /重试后的完整回答/);

  // nudge 不落历史：会话里只有原始用户消息与助手回复，没有系统口吻的假用户消息。
  const messages = await readSessionMessages(71);
  assert.ok(
    messages.some((message) => message.role === "assistant" && String(message.content).includes("重试后的完整回答")),
    `assistant reply should be persisted: ${JSON.stringify(messages.map((message) => message.role))}`,
  );
  assert.equal(
    messages.filter((message) => message.role === "user" && String(message.content).includes(NUDGE_MARKER)).length,
    0,
    `nudge must not be persisted: ${JSON.stringify(messages.map((message) => [message.role, String(message.content).slice(0, 40)]))}`,
  );

  // 重试请求不携带空 assistant 条目（422 验证）：第二次调用的 LLM 消息列表里
  // 没有任何空 assistant 条目，且以 nudge 用户消息收尾。
  const retryContext = contexts[1];
  assert.ok(retryContext);
  assert.equal(
    hasEmptyAssistantEntry(retryContext.messages),
    false,
    `retry request must not carry an empty assistant entry: ${JSON.stringify(retryContext.messages)}`,
  );
  const lastMessage = retryContext.messages[retryContext.messages.length - 1] as
    | { role?: unknown; content?: Array<{ type?: unknown; text?: unknown }> }
    | undefined;
  assert.ok(lastMessage);
  assert.equal(lastMessage.role, "user");
  const lastText = Array.isArray(lastMessage.content)
    ? lastMessage.content.map((block) => String(block?.text ?? "")).join("")
    : "";
  assert.match(lastText, new RegExp(NUDGE_MARKER));
});

test("a retry that is empty again fails with the layered Chinese copy", async () => {
  let calls = 0;
  const streamFn: StreamFn = async () => {
    calls += 1;
    return streamAssistant([], "stop");
  };

  const created = createAgentRun("empty-reply-owner", "再来一轮还是空的", { sessionId: 72, streamFn });
  await waitForTask(created.run_ref);

  const run = getAgentRun("empty-reply-owner", created.run_ref);
  assert.ok(run);
  assert.equal(run.status, "failed");
  assert.equal(calls, 2, `exactly one retry, got ${calls} provider calls`);
  assert.equal(run.error?.retryable, true);
  assert.equal(run.error?.message, "模型本次没有返回内容，请稍后重试。");
});

test("provider error text stays passthrough for gateway classification (A3 channel)", async () => {
  // EmptyAssistantReplyError(fromProviderError=true) 的 userFacing 必须原样透传
  // 底层错误体：前端网关错误码分流（契约 §5.3）要从 run.error.message 里做
  // 子串识别。走真实 turn 链路验证。
  // 2026-10-06 错误分层矩阵：quota 类分类码确定后 retryable=false（重试无
  // 意义），前端给充值引导而非重试按钮——原 true 断言随矩阵作废。
  const streamFn: StreamFn = async () =>
    errorAssistant('403: {"error":{"message":"本回合预扣超过剩余额度","type":"quota_prehold_insufficient"}}');
  const created = createAgentRun("empty-reply-owner", "网关错误透传", { sessionId: 73, streamFn });
  await waitForTask(created.run_ref);
  const run = getAgentRun("empty-reply-owner", created.run_ref);
  assert.ok(run);
  assert.equal(run.status, "failed");
  assert.equal(run.error?.code, "quota_exhausted", `quota body must classify: ${JSON.stringify(run.error)}`);
  assert.equal(run.error?.retryable, false);
  assert.ok(
    String(run.error?.message ?? "").includes("quota_prehold_insufficient"),
    `gateway body must pass through: ${JSON.stringify(run.error)}`,
  );
});
