/**
 * Coach 停止竞态行为回归（2026-10-16 系统评审 #2 正式化）。
 *
 * 合同：初始化前停止不消费 provider 流、不落 assistant 回复；停止未真实
 * 收尾不伪造 stopped 终态（保持 running + 恰一条 run_stop_requested 事件）、
 * 同会话并发闸不重开，真实收尾后才可重发；中途停止保留已生成 partial、
 * 无完整迟到回复；停止信号取消 compaction 独立摘要 LLM 调用且停止后不再
 * 进入 prompt/重试；停止收尾 listener 恰一次 onDone、单 listener 异常不破
 * 坏广播。
 *
 * 纯本地：turn 流全为进程内 fake；profile 指向 loopback 死端口，漏网真实
 * 请求立即 ECONNREFUSED 显形——零外呼。所有等待有界，防挂。
 */
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-stop-race-"));
process.env.DATA_ROOT = dataRoot;

import {
  createAgentRun,
  getAgentRun,
  stopAgentRun,
  subscribeAgentRun,
  type AgentRunEvent,
} from "../src/agent-runs.ts";
import { isTaskActive } from "../src/task-manager.ts";
import { ensureSession, readSessionMessages } from "../src/session-repo.ts";
import { saveProfile } from "../src/provider-store.ts";
import { loadPiAi } from "../src/pi-source.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

// 小窗口自定义档：context_window=1000 → 压缩触发线 800 tokens（T6 用）。
// 其余用例历史极小，不会触发压缩；base_url 不可达（127.0.0.1:9），所有
// 模型调用都应被注入的 fake 流截获，漏网的真实请求立即连接失败而显形。
saveProfile({
  kind: "custom_openai_compatible",
  provider_id: "stop-race-fixture",
  provider_name: "Stop Race Fixture",
  base_url: "http://127.0.0.1:9/v1",
  credential: { type: "api_key", key: "stop-race-test-key" },
  model_id: "stop-race-fixture-model",
  context_window: 1000,
  max_tokens: 1024,
});

// ── 测试基建 ─────────────────────────────────────────────────────────────

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForCond(name: string, cond: () => boolean | Promise<boolean>, timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!(await cond())) {
    if (Date.now() > deadline) throw new Error(`bounded wait exceeded: ${name}`);
    await delay(20);
  }
}

/** 有界等待任务收尾（等价 waitForTask 但带超时，防挂）。 */
async function waitForTaskSettled(runRef: string, timeoutMs = 15_000): Promise<void> {
  await waitForCond(`task settled: ${runRef}`, () => !isTaskActive(runRef), timeoutMs);
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

type FakeStream = { push(event: unknown): void; end(result: unknown): void };

const createEventStream = ((await loadPiAi()) as {
  createAssistantMessageEventStream: () => FakeStream;
}).createAssistantMessageEventStream;

const FAKE_USAGE = {
  input: 1,
  output: 1,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 2,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

function assistantMessage(
  text: string,
  stopReason: "stop" | "aborted" | "error",
  errorMessage?: string,
) {
  return {
    role: "assistant" as const,
    content: [{ type: "text", text }],
    api: "openai-completions",
    provider: "stop-race-fixture",
    model: "stop-race-fixture-model",
    usage: FAKE_USAGE,
    stopReason,
    ...(errorMessage ? { errorMessage } : {}),
    timestamp: 0,
  };
}

function signalOf(options: unknown): AbortSignal | undefined {
  return (options as { signal?: AbortSignal } | undefined)?.signal;
}

/**
 * 完整回复的同步假流：start → text_delta(text) → done(stop)。
 * abortAware=false 时完全无视取消信号（构造「停止无法收尾」的病态 provider）。
 */
function fullReplyStreamFn(text: string, abortAware = false): StreamFn {
  return (_model, _context, options) => {
    const stream = createEventStream();
    const signal = signalOf(options);
    queueMicrotask(() => {
      if (abortAware && signal?.aborted) {
        const aborted = assistantMessage("", "aborted", "aborted before start");
        stream.push({ type: "done", reason: "aborted", message: aborted });
        stream.end(aborted);
        return;
      }
      const initial = assistantMessage("", "stop");
      const final = assistantMessage(text, "stop");
      stream.push({ type: "start", partial: initial });
      stream.push({ type: "text_start", contentIndex: 0, partial: initial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: final });
      stream.push({ type: "text_end", contentIndex: 0, content: text, partial: final });
      stream.push({ type: "done", reason: "stop", message: final });
      stream.end(final);
    });
    return stream;
  };
}

function stopEventCount(events: AgentRunEvent[], code: string): number {
  return events.filter((event) => event.code === code).length;
}

function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (b && typeof b === "object" && typeof (b as { text?: unknown }).text === "string" ? (b as { text: string }).text : ""))
      .join("");
  }
  return "";
}

/** 递归读取指定线程会话 JSONL 的 message 条目（pi repo 布局在子目录内，
 * 文件名为 `<uuid>_<threadId>.jsonl` 或 legacy `<threadId>.jsonl`）。 */
function dumpSessionMessages(threadId: number): Array<{ role: string; text: string; stopReason?: string }> {
  const convDir = join(dataRoot, "conversations");
  let jsonlText = "";
  const dump = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name);
      if (entry.isDirectory()) dump(p);
      else if (entry.name.match(new RegExp(`(?:^|_)${threadId}\\.jsonl$`))) {
        jsonlText += readFileSync(p, "utf8");
      }
    }
  };
  dump(convDir);
  return jsonlText
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as { type?: string; message?: { role?: string; content?: unknown; stopReason?: string } })
    .filter((e) => e.type === "message")
    .map((e) => ({
      role: e.message?.role ?? "?",
      text: extractText(e.message?.content),
      stopReason: e.message?.stopReason,
    }));
}

// ── T1 初始化前停止 ─────────────────────────────────────────────────────

test("stop before initialization: no provider call, no assistant reply lands, resend works after settle", async () => {
  const owner = "stop-race-1";
  const threadId = 4101;
  const gate = deferred();
  let streamStarted = false;
  const gatedStreamFn: StreamFn = (_model, _context, options) => {
    streamStarted = true;
    const stream = createEventStream();
    const signal = signalOf(options);
    void (async () => {
      await gate.promise;
      const final = signal?.aborted
        ? assistantMessage("", "aborted", "aborted by test")
        : assistantMessage("LATE_FULL_REPLY_T1", "stop");
      stream.push({ type: "done", reason: final.stopReason, message: final });
      stream.end(final);
    })();
    return stream;
  };

  try {
    const run1 = createAgentRun(owner, "USER_MESSAGE_ONE", { sessionId: threadId, streamFn: gatedStreamFn });
    // 同步停止：落在 harness 注册前窗口（stopCoachTurn 找不到可中止对象）。
    await stopAgentRun(owner, run1.run_ref);
    await waitForTaskSettled(run1.run_ref);

    const run = getAgentRun(owner, run1.run_ref);
    assert.ok(run);
    assert.equal(run.status, "stopped", "run must end stopped");
    assert.equal(streamStarted, false, "provider stream must never be consumed after a pre-init stop");
    assert.equal(stopEventCount(run.events, "run_stop_requested"), 1, "exactly one run_stop_requested event");
    assert.equal(stopEventCount(run.events, "run_stopped"), 1, "terminal run_stopped event present");
    assert.equal(stopEventCount(run.events, "run_succeeded"), 0, "no success event on a stopped run");

    const messages = dumpSessionMessages(threadId);
    assert.ok(messages.some((m) => m.role === "user" && m.text === "USER_MESSAGE_ONE"), "user message persists");
    assert.ok(
      !messages.some((m) => m.role === "assistant"),
      "no assistant reply may land for a run stopped before initialization",
    );

    // 收尾后重发：同会话并发闸放行，新回合正常完成。
    const run2 = createAgentRun(owner, "USER_MESSAGE_TWO", {
      sessionId: threadId,
      streamFn: fullReplyStreamFn("REPLY_TWO"),
    });
    await waitForTaskSettled(run2.run_ref);
    const done2 = getAgentRun(owner, run2.run_ref);
    assert.ok(done2);
    assert.equal(done2.status, "succeeded", `resend must succeed, error: ${JSON.stringify(done2.error)}`);
    const visible = await readSessionMessages(threadId);
    assert.deepEqual(
      visible.map((m) => `${m.role}:${m.content}`),
      ["user:USER_MESSAGE_ONE", "user:USER_MESSAGE_TWO", "assistant:REPLY_TWO"],
      "active branch must be user1 → user2 → reply2 with no late reply from run1",
    );
  } finally {
    gate.resolve();
  }
});

// ── T2 注册后中途停止：保留 partial、无完整迟到回复 ─────────────────────

test("registered mid-stream stop keeps the generated partial and never lands a complete late reply", async () => {
  const owner = "stop-race-2";
  const threadId = 4102;
  const partialText = "PARTIAL_KEPT_T2";
  // abort 感知假流：先吐 partial，收到 abort 即以 aborted + 已生成正文收场
  //（真实 provider abort 语义）；完整结尾永远不生成。
  const midStreamFn: StreamFn = (_model, _context, options) => {
    const stream = createEventStream();
    const signal = signalOf(options);
    queueMicrotask(() => {
      const initial = assistantMessage("", "stop");
      const partial = assistantMessage(partialText, "stop");
      stream.push({ type: "start", partial: initial });
      stream.push({ type: "text_start", contentIndex: 0, partial: initial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: partialText, partial });
      const finishAborted = () => {
        const final = assistantMessage(partialText, "aborted");
        stream.push({ type: "done", reason: "aborted", message: final });
        stream.end(final);
      };
      if (signal?.aborted) finishAborted();
      else signal?.addEventListener("abort", finishAborted, { once: true });
    });
    return stream;
  };

  const run = createAgentRun(owner, "USER_MESSAGE_MID", { sessionId: threadId, streamFn: midStreamFn });
  await waitForCond("partial streamed", () => (getAgentRun(owner, run.run_ref)?.partial_text ?? "").includes(partialText));

  await stopAgentRun(owner, run.run_ref);
  await waitForTaskSettled(run.run_ref);

  const done = getAgentRun(owner, run.run_ref);
  assert.ok(done);
  assert.equal(done.status, "stopped");
  assert.ok(
    (done.partial_text ?? "").includes(partialText),
    "already generated partial must be preserved in run state",
  );
  assert.equal(stopEventCount(done.events, "run_stop_requested"), 1);
  assert.equal(stopEventCount(done.events, "run_stopped"), 1);
  assert.equal(stopEventCount(done.events, "run_succeeded"), 0);

  const assistantEntries = dumpSessionMessages(threadId).filter((m) => m.role === "assistant");
  assert.deepEqual(
    assistantEntries.map((m) => m.text),
    [partialText],
    "session JSONL must hold exactly the generated partial — never a complete late reply",
  );
});

// ── T3 停止无法收尾：不伪造终态、并发闸不重开（repro L1 正式化）────────

test("stop that cannot finalize keeps status running, keeps the session gate closed, and allows resend only after settle", async () => {
  const owner = "stop-race-3";
  const threadId = 4103;
  const gate = deferred();
  let streamStarted = false;
  // 病态 provider：无视取消信号挂在 gate 上；gate 释放后继续推进 text_delta
  // 到完整正文再 done（late delta + late done 双形状，构造 lastRawPartialText
  // 若随迟到 delta 推进则截断防线失效的精确形状）。
  const stubbornStreamFn: StreamFn = () => {
    streamStarted = true;
    const stream = createEventStream();
    void (async () => {
      const initial = assistantMessage("", "stop");
      const partial = assistantMessage("PARTIAL_L3", "stop");
      stream.push({ type: "start", partial: initial });
      stream.push({ type: "text_start", contentIndex: 0, partial: initial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: "PARTIAL_L3", partial });
      await gate.promise;
      const fullText = "REPLY_OF_STOPPED_RUN_L3";
      stream.push({
        type: "text_delta",
        contentIndex: 0,
        delta: fullText.slice("PARTIAL_L3".length, 18),
        partial: assistantMessage(fullText.slice(0, 18), "stop"),
      });
      stream.push({
        type: "text_delta",
        contentIndex: 0,
        delta: fullText.slice(18),
        partial: assistantMessage(fullText, "stop"),
      });
      const final = assistantMessage(fullText, "stop");
      stream.push({ type: "done", reason: "stop", message: final });
      stream.end(final);
    })();
    return stream;
  };

  try {
    const run1 = createAgentRun(owner, "USER_MESSAGE_L3", { sessionId: threadId, streamFn: stubbornStreamFn });
    await waitForCond("run1 streaming", () => streamStarted && (getAgentRun(owner, run1.run_ref)?.partial_text ?? "").includes("PARTIAL_L3"));

    const stopped = await stopAgentRun(owner, run1.run_ref);
    assert.ok(stopped);
    assert.equal(stopped.status, "running", "stop must NOT fake a stopped terminal while the turn task is still live");
    assert.equal(stopEventCount(stopped.events, "run_stop_requested"), 1, "exactly one stop_requested event");
    assert.equal(isTaskActive(run1.run_ref), true, "turn task must still be live");
    assert.equal(stopped.finished_at, null, "no finished_at may be written before real settlement");

    assert.throws(
      () => createAgentRun(owner, "USER_MESSAGE_L3_B", { sessionId: threadId, streamFn: fullReplyStreamFn("REPLY_B") }),
      (error: unknown) => error instanceof Error && (error as { code?: string }).code === "session_busy",
      "second same-session send must be rejected while the stopped-but-live turn holds the session",
    );

    // 真实收尾后才可重发。
    gate.resolve();
    await waitForTaskSettled(run1.run_ref);
    const settled = getAgentRun(owner, run1.run_ref);
    assert.ok(settled);
    assert.equal(settled.status, "stopped", "run settles stopped once the turn task actually ends");
    assert.ok(
      (settled.partial_text ?? "").includes("PARTIAL_L3"),
      "the partial the user saw stays in run state",
    );
    // 持久化边界：迟到完整正文（含迟到 delta 推进）不得进会话；落盘的必须是
    // 停止前已冻结的 partial，且 stopReason 标 aborted（不保留迟到 done 的
    // stop 正常终态）。
    const jsonlAfter = dumpSessionMessages(threadId).filter((m) => m.role === "assistant");
    assert.deepEqual(
      jsonlAfter.map((m) => ({ text: m.text, stopReason: m.stopReason })),
      [{ text: "PARTIAL_L3", stopReason: "aborted" }],
      "session must hold only the frozen pre-stop partial, marked aborted — never the late full reply",
    );

    const run2 = createAgentRun(owner, "USER_MESSAGE_L3_C", {
      sessionId: threadId,
      streamFn: fullReplyStreamFn("REPLY_L3_C"),
    });
    await waitForTaskSettled(run2.run_ref);
    const done2 = getAgentRun(owner, run2.run_ref);
    assert.ok(done2);
    assert.equal(done2.status, "succeeded", `resend after settle must succeed, error: ${JSON.stringify(done2.error)}`);
    const visible = await readSessionMessages(threadId);
    assert.ok(
      !visible.some((m) => m.role === "assistant" && m.content.includes("REPLY_OF_STOPPED_RUN_L3")),
      "stopped run's complete reply must never appear in the active branch",
    );
  } finally {
    gate.resolve();
  }
});

// ── T5 停止收尾的 listener 清理与恰一次 onDone ──────────────────────────

test("stop finalization notifies listeners once, survives a throwing listener, and late subscribers get the terminal snapshot", async () => {
  const owner = "stop-race-5";
  const threadId = 4105;
  const partialText = "PARTIAL_T5";
  const midStreamFn: StreamFn = (_model, _context, options) => {
    const stream = createEventStream();
    const signal = signalOf(options);
    queueMicrotask(() => {
      const initial = assistantMessage("", "stop");
      const partial = assistantMessage(partialText, "stop");
      stream.push({ type: "start", partial: initial });
      stream.push({ type: "text_delta", contentIndex: 0, delta: partialText, partial });
      const finishAborted = () => {
        const final = assistantMessage(partialText, "aborted");
        stream.push({ type: "done", reason: "aborted", message: final });
        stream.end(final);
      };
      if (signal?.aborted) finishAborted();
      else signal?.addEventListener("abort", finishAborted, { once: true });
    });
    return stream;
  };

  const run = createAgentRun(owner, "USER_MESSAGE_T5", { sessionId: threadId, streamFn: midStreamFn });
  const doneA: Array<unknown> = [];
  const doneB: Array<unknown> = [];
  // listener A 在 onActivity 抛异常：不得破坏广播，也不得影响恰一次 onDone。
  const unsubscribeA = subscribeAgentRun(owner, run.run_ref, {
    onActivity: () => {
      throw new Error("listener boom");
    },
    onDone: (state) => doneA.push(state.status),
  });
  const unsubscribeB = subscribeAgentRun(owner, run.run_ref, { onDone: (state) => doneB.push(state.status) });
  assert.ok(unsubscribeA);
  assert.ok(unsubscribeB);

  await waitForCond("partial streamed", () => (getAgentRun(owner, run.run_ref)?.partial_text ?? "").includes(partialText));
  await stopAgentRun(owner, run.run_ref);
  await waitForTaskSettled(run.run_ref);
  await delay(20);

  assert.deepEqual(doneA, ["stopped"], "throwing listener must still receive exactly one onDone");
  assert.deepEqual(doneB, ["stopped"], "healthy listener must receive exactly one onDone");

  // 收尾后再订阅：立即（deferred）拿到终态快照，不保留常驻订阅。
  let lateDone: string | null = null;
  const unsubscribeLate = subscribeAgentRun(owner, run.run_ref, { onDone: (state) => { lateDone = state.status; } });
  assert.ok(unsubscribeLate);
  await delay(20);
  assert.equal(lateDone, "stopped");
  unsubscribeLate();
  unsubscribeA();
  unsubscribeB();
});

// ── T6 停在 compaction：摘要调用被真实取消，不进 prompt ─────────────────

const SUMMARY_SYSTEM_MARKER = "context summarization assistant";

test("stop during compaction cancels the summary LLM call and the turn stops without prompting", async () => {
  const owner = "stop-race-6";
  const threadId = 4106;
  // 历史文本撑过 1000×80% 触发线（与 turn-compaction-activity 同口径）。
  const bigHistoryText =
    "压缩触发测试的历史包袱：这一段中文用于撑起 CJK 感知字符估算，让折叠视图超过小窗口的八成触发线。".repeat(40);
  const session = await ensureSession(threadId);
  await session.appendMessage({
    role: "user",
    content: [{ type: "text", text: bigHistoryText }],
    timestamp: Date.now(),
  });
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "好的，我们继续。" }],
    usage: { input: 100, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 110 },
    stopReason: "stop",
    timestamp: Date.now(),
  });

  let summaryCalls = 0;
  let normalPromptCalls = 0;
  const compactionAwareStreamFn: StreamFn = (_model, context, options) => {
    const systemPrompt = (context as { systemPrompt?: unknown } | null)?.systemPrompt;
    const stream = createEventStream();
    const signal = signalOf(options);
    if (typeof systemPrompt === "string" && systemPrompt.includes(SUMMARY_SYSTEM_MARKER)) {
      summaryCalls += 1;
      queueMicrotask(() => {
        const initial = assistantMessage("", "error");
        stream.push({ type: "start", partial: initial });
        // 摘要调用只在收到注入的停止信号时收场：pi compact 的独立 LLM 请求
        // 必须随 stop 真实取消（fetch 级 abort 注入），否则测试有界等待超时。
        const finishAborted = () => {
          const final = assistantMessage("", "error", "summary aborted by stop-race test");
          stream.push({ type: "done", reason: "error", message: final });
          stream.end(final);
        };
        if (signal?.aborted) finishAborted();
        else signal?.addEventListener("abort", finishAborted, { once: true });
      });
      return stream;
    }
    normalPromptCalls += 1;
    queueMicrotask(() => {
      const final = assistantMessage("COMPACTION_RACE_REPLY", "stop");
      stream.push({ type: "done", reason: "stop", message: final });
      stream.end(final);
    });
    return stream;
  };

  const run = createAgentRun(owner, "USER_MESSAGE_T6", { sessionId: threadId, streamFn: compactionAwareStreamFn });
  await waitForCond("compaction started", () =>
    (getAgentRun(owner, run.run_ref)?.events ?? []).some((event) => event.code === "compaction_started"));

  const stopped = await stopAgentRun(owner, run.run_ref);
  await waitForTaskSettled(run.run_ref);

  const done = getAgentRun(owner, run.run_ref);
  assert.ok(done);
  assert.equal(done.status, "stopped");
  assert.equal(stopEventCount(done.events, "run_stop_requested"), 1, "exactly one stop_requested event (old code: none)");
  assert.deepEqual(
    done.events.filter((event) => event.code.startsWith("compaction_")).map((event) => event.code),
    ["compaction_started"],
    "compaction started then was cancelled via the injected signal (failed activity is deterministically suppressed by the abort guard on activity broadcast; the fail-open skip still logs)",
  );
  assert.equal(normalPromptCalls, 0, "turn must never prompt after a stop during compaction");
  assert.equal(stopEventCount(done.events, "run_succeeded"), 0);
  assert.ok(
    !dumpSessionMessages(threadId).some((m) => m.role === "assistant" && m.text.includes("COMPACTION_RACE_REPLY")),
    "no complete reply may land for a run stopped during compaction",
  );
  assert.equal(summaryCalls, 1, "summary call must have been made exactly once before cancellation");
});
