// Coach 压缩（compaction）改进回归锁（点点 2026-10-07 拍板三项）：
// 1. 触发线＝窗口 80%（替代 pi 固定 reserve 16384：1M 窗口下旧公式触发点
//    98.4%，55 万 tokens 会话也压不动——2026-10-05 生产事故实证）；
// 2. 压缩是一次独立 LLM 摘要调用（可能几十秒），started/completed/failed
//    经 activity 通道透出（agent-runs 转发为 phase 事件），前端据此显示
//    “正在整理会话记忆”状态行；
// 3. 压缩失败 fail-open（不拦对话）的既有语义不得回退。
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-compaction-activity-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun } from "../src/agent-runs.ts";
import { loadPiAgent, loadPiAi } from "../src/pi-source.ts";
import { saveProfile } from "../src/provider-store.ts";
import { ensureSession } from "../src/session-repo.ts";
import { waitForTask } from "../src/task-manager.ts";
import { shouldCompactNow } from "../src/turn.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

// 小窗口自定义档：context_window=1000 → 触发线 800 tokens，少量历史即可
// 触发压缩；base_url 不可达（127.0.0.1:9）——所有模型调用都应被注入的
// fake 流截获，漏网的真实请求会立即连接失败而显形。
saveProfile({
  kind: "custom_openai_compatible",
  provider_id: "compaction-fixture",
  provider_name: "Compaction Fixture",
  base_url: "http://127.0.0.1:9/v1",
  credential: { type: "api_key", key: "compaction-fixture-key" },
  model_id: "small-window-fixture",
  context_window: 1000,
  max_tokens: 1024,
});

// ── 同步返回的假流 ────────────────────────────────────────────────────────
// pi 的 ModelsImpl.completeSimple 直接对 streamSimple 返回值 .result()：
// async fake 返回的是 Promise，在这条路径必然 TypeError。压缩测试的 fake
// 必须同步返回流对象（harness 主链路对两种形态都兼容）。

type FakeStream = { push(event: unknown): void; end(result: unknown): void };

const FAKE_USAGE = {
  input: 12,
  output: 34,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 46,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

const createEventStream = ((await loadPiAi()) as {
  createAssistantMessageEventStream: () => FakeStream;
}).createAssistantMessageEventStream;

function assistantMessage(
  content: Array<Record<string, unknown>>,
  stopReason: "stop" | "error",
  errorMessage?: string,
) {
  return {
    role: "assistant" as const,
    content,
    api: "openai-completions",
    provider: "compaction-fixture",
    model: "small-window-fixture",
    usage: FAKE_USAGE,
    stopReason,
    ...(errorMessage ? { errorMessage } : {}),
    timestamp: 0,
  };
}

/** 同步返回流对象（非 Promise）：text 块按 text_start/delta/end 推进。 */
function fakeStream(
  content: Array<Record<string, unknown>>,
  stopReason: "stop" | "error",
  errorMessage?: string,
): FakeStream {
  const stream = createEventStream();
  const initial = assistantMessage([], stopReason, errorMessage);
  const final = assistantMessage(content, stopReason, errorMessage);
  queueMicrotask(() => {
    stream.push({ type: "start", partial: initial });
    content.forEach((block, contentIndex) => {
      const text = String((block as { text?: unknown }).text ?? "");
      stream.push({ type: "text_start", contentIndex, partial: initial });
      stream.push({ type: "text_delta", contentIndex, delta: text, partial: final });
      stream.push({ type: "text_end", contentIndex, content: text, partial: final });
    });
    stream.push({ type: "done", reason: stopReason, message: final });
    stream.end(final);
  });
  return stream;
}

/** 摘要调用判别：pi generateSummaryWithUsage 固定携带 SUMMARIZATION_SYSTEM_PROMPT。 */
const SUMMARY_SYSTEM_MARKER = "context summarization assistant";

function compactionStreamFn(summaryMode: "ok" | "error"): StreamFn {
  return (_model, context) => {
    const systemPrompt = (context as { systemPrompt?: unknown } | null)?.systemPrompt;
    if (typeof systemPrompt === "string" && systemPrompt.includes(SUMMARY_SYSTEM_MARKER)) {
      if (summaryMode === "error") {
        // 文案刻意避开可重试 pattern（无状态码/overloaded 字样）：摘要立即
        // 失败、无重试退避，测试不必等 pi 的 1s/2s 补偿延迟。
        return fakeStream([], "error", "compaction disabled by test fixture");
      }
      return fakeStream([{ type: "text", text: "会话摘要：用户在练习转火，下一步做复测。" }], "stop");
    }
    return fakeStream([{ type: "text", text: "这是压缩后的正常回答。" }], "stop");
  };
}

// 历史文本：~1900 中文字符 × 0.7 ≈ 1300 tokens > 1000×80%。末条 assistant
// 带 110 tokens 的 usage（远低于触发线），逼迫判定走 CJK 字符估算兜底层——
// 与生产事故（usage 失真低估）同形态。
const BIG_HISTORY_TEXT =
  "压缩触发测试的历史包袱：这一段中文用于撑起 CJK 感知字符估算，让折叠视图超过小窗口的八成触发线。".repeat(40);

async function seedHistory(threadId: number): Promise<void> {
  const session = await ensureSession(threadId);
  await session.appendMessage({
    role: "user",
    content: [{ type: "text", text: BIG_HISTORY_TEXT }],
    timestamp: Date.now(),
  });
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "好的，我们继续。" }],
    usage: { input: 100, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 110 },
    stopReason: "stop",
    timestamp: Date.now(),
  });
}

/** 压缩 activity 帧的合同断言：phase 形态事件、payload 白名单携带 kind/state。 */
function assertCompactionEvents(run: NonNullable<ReturnType<typeof getAgentRun>>, expected: string[]): void {
  const events = run.events.filter((event) => event.code.startsWith("compaction_"));
  assert.deepEqual(
    events.map((event) => event.code),
    expected,
    `compaction event codes: ${JSON.stringify(run.events.map((event) => event.code))}`,
  );
  for (const event of events) {
    assert.equal(event.type, "phase");
    const payload = event.payload ?? {};
    assert.equal(payload.kind, "compaction");
    assert.equal(payload.state, event.code.slice("compaction_".length));
    assert.equal(typeof payload.sequence, "number");
  }
}

// ── shouldCompactNow：80% 触发线 ──────────────────────────────────────────

test("shouldCompactNow triggers above 80% of the context window", async () => {
  const { InMemorySessionRepo } = (await loadPiAgent()) as {
    InMemorySessionRepo: new () => { create: () => Promise<any> };
  };

  // 790/1000 = 79%：低于触发线不压缩。
  const below = await new InMemorySessionRepo().create();
  await below.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }] });
  await below.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "hello" }],
    usage: { input: 700, output: 50, cacheRead: 40, cacheWrite: 0, totalTokens: 790 },
    stopReason: "stop",
  });
  assert.equal(await shouldCompactNow(below, 1000), false);

  // 810/1000 = 81%：高于触发线必压缩。
  const above = await new InMemorySessionRepo().create();
  await above.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }] });
  await above.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "hello" }],
    usage: { input: 720, output: 50, cacheRead: 40, cacheWrite: 0, totalTokens: 810 },
    stopReason: "stop",
  });
  assert.equal(await shouldCompactNow(above, 1000), true);
});

test("shouldCompactNow refuses unknown context windows without touching the session", async () => {
  const probes: string[] = [];
  const session = {
    buildContext: async () => {
      probes.push("called");
      return { messages: [] };
    },
  };
  assert.equal(await shouldCompactNow(session, 0), false);
  assert.equal(await shouldCompactNow(session, -5), false);
  assert.deepEqual(probes, []);
});

// ── 压缩 activity 序列（agent-runs 全链路）────────────────────────────────

test("compaction publishes started→completed activities and the turn succeeds", async () => {
  const threadId = 7601;
  await seedHistory(threadId);
  const created = createAgentRun("compaction-owner", "继续聊下一步怎么练", {
    sessionId: threadId,
    streamFn: compactionStreamFn("ok"),
  });
  await waitForTask(created.run_ref);

  const run = getAgentRun("compaction-owner", created.run_ref);
  assert.ok(run);
  assert.equal(run.status, "succeeded", `error: ${JSON.stringify(run.error)}`);
  assertCompactionEvents(run, ["compaction_started", "compaction_completed"]);
  assert.match(run.partial_text ?? "", /压缩后的正常回答/);
});

test("compaction failure publishes started→failed and fail-open keeps the dialogue alive", async () => {
  const threadId = 7602;
  await seedHistory(threadId);
  const created = createAgentRun("compaction-owner", "压缩失败也要继续对话", {
    sessionId: threadId,
    streamFn: compactionStreamFn("error"),
  });
  await waitForTask(created.run_ref);

  const run = getAgentRun("compaction-owner", created.run_ref);
  assert.ok(run);
  // fail-open：摘要失败只落 failed activity 与诊断日志，回合照常成功。
  assert.equal(run.status, "succeeded", `error: ${JSON.stringify(run.error)}`);
  assertCompactionEvents(run, ["compaction_started", "compaction_failed"]);
  assert.match(run.partial_text ?? "", /压缩后的正常回答/);
});
