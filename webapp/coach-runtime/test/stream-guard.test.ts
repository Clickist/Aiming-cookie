// 流式重复熔断（stream guard，2026-10 █ 野外案）+ FFFD 输入权威拦截。
// 两层防线 coach-runtime 侧行为锁定：
// 1. 输出侧：full-replace 文本连续 █ ≥200 → 熔断 abort（fire-and-forget，照
//    用户停止写法）→ 恰一次同 harness 降档重试（nudge 不落历史）；最终呈现
//    绝不是 "stopped"（防「引擎打断谎报用户主动停止」，同 2026-10-12 重启
//    事故形态）；thinking level 重试后恢复原档；[stream-guard] 诊断行落
//    coach-error.log；run events 含 stream_guard_retried。
// 2. 二次熔断不再循环：corrupted_stream 可重试失败，partial 带回 █ 文本。
// 3. kill-switch：AIMING_COOKIE_STREAM_GUARD=0 整体关闭检测。
// 4. 不误伤：200 以内合法 █（进度条）原样通过、不重试。
// 5. 输入侧：U+FFFD ≥2 → createAgentRun / steerAgentRun 一致拒绝
//    （invalid_encoding）；单个 FFFD 放行。
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-stream-guard-"));
process.env.DATA_ROOT = dataRoot;

import { AgentRunError, createAgentRun, getAgentRun, steerAgentRun } from "../src/agent-runs.ts";
import { openSession, readSessionMessages } from "../src/session-repo.ts";
import { saveProfile } from "../src/provider-store.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "stream-guard-test-key" },
});

const BLOCK_RUN = "█".repeat(250);
const SAFE_BAR = "█".repeat(80);

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

/**
 * 推出 start/text_start/text_delta(█) 后挂住不结束的假流：text_delta 命中
 * 熔断 → harness.abort() 同步置位 signal → 挂住的 next() 拒绝 AbortError →
 * pi agent-loop 走 emitRunFailure(aborted=true)，prompt() resolve
 * stopReason="aborted" 的空消息——正是熔断分叉要接管（而非谎报 stopped）
 * 的形态。
 */
function hangingBlockStream(signal: AbortSignal | undefined, blockText: string): unknown {
  const final = {
    role: "assistant" as const,
    content: [{ type: "text", text: blockText }],
    api: "openai-completions",
    provider: "aiming-cookie-coach-e2e",
    model: "fixture-model",
    usage: EMPTY_USAGE,
    stopReason: "stop" as const,
    timestamp: 0,
  };
  const initial = { ...final, content: [] as unknown[] };
  const events = [
    { type: "start", partial: initial },
    { type: "text_start", contentIndex: 0, partial: initial },
    { type: "text_delta", contentIndex: 0, delta: blockText, partial: final },
  ];
  let index = 0;
  return {
    [Symbol.asyncIterator]() {
      return {
        next(): Promise<IteratorResult<unknown>> {
          if (index < events.length) {
            return Promise.resolve({ value: events[index++]!, done: false });
          }
          return new Promise((_resolve, reject) => {
            const abortError = () =>
              reject(Object.assign(new Error("This operation was aborted"), { name: "AbortError" }));
            if (signal?.aborted) {
              abortError();
              return;
            }
            signal?.addEventListener("abort", abortError, { once: true });
          });
        },
      };
    },
  };
}

function reasoningOf(options: unknown): string | undefined {
  return (options as { reasoning?: string } | undefined)?.reasoning;
}

function signalOf(options: unknown): AbortSignal | undefined {
  return (options as { signal?: AbortSignal } | undefined)?.signal;
}

test("stream guard aborts a degenerate █ stream and retries exactly once with degraded thinking", async () => {
  rmSync(join(dataRoot, "coach-error.log"), { force: true });
  const reasonings: Array<string | undefined> = [];
  const streamFn: StreamFn = async (_model, _context, options) => {
    reasonings.push(reasoningOf(options));
    if (reasonings.length === 1) return hangingBlockStream(signalOf(options), BLOCK_RUN);
    return streamAssistant([{ type: "text", text: "重试后的完整回答。" }], "stop");
  };

  const created = createAgentRun("stream-guard-owner", "讲讲这局的命中率", { sessionId: 8101, streamFn });
  await waitForTask(created.run_ref);

  const run = getAgentRun("stream-guard-owner", created.run_ref);
  assert.ok(run);
  // 恰一次重试：provider 恰被调两次；重试请求思考档从 high 降一档到 medium。
  assert.equal(reasonings.length, 2, `exactly one retry, got ${reasonings.length} provider calls`);
  assert.equal(reasonings[0], "high");
  assert.equal(reasonings[1], "medium");

  // 重点回归：熔断路径的最终呈现绝不是「用户主动停止」。
  assert.equal(run.status, "succeeded", `retry should succeed, error: ${JSON.stringify(run.error)}`);
  assert.notEqual(run.error?.code, "stopped");
  assert.ok(
    run.events.every((event) => event.code !== "stopped"),
    `no stopped event expected: ${JSON.stringify(run.events.map((event) => event.code))}`,
  );
  assert.match(run.partial_text ?? "", /重试后的完整回答/);
  assert.ok(!(run.partial_text ?? "").includes("已停止生成"));

  // thinking level 已恢复原档：会话依次留下 medium（降档重试）与 high（恢复）。
  const session = await openSession(8101);
  assert.ok(session);
  const entries = (await session.getBranch()) as Array<{ type?: string; thinkingLevel?: string }>;
  const levelChanges = entries
    .filter((entry) => entry.type === "thinking_level_change")
    .map((entry) => entry.thinkingLevel);
  assert.deepEqual(levelChanges, ["medium", "high"]);

  // 诊断行落 coach-error.log（机读：run / max_run / action）。
  assert.ok(existsSync(join(dataRoot, "coach-error.log")), "coach-error.log must exist after a breaker trip");
  const log = readFileSync(join(dataRoot, "coach-error.log"), "utf8");
  assert.match(log, /\[stream-guard\] run=\S+ max_run=250 action=abort_retry_degraded/);

  // run events 含 stream_guard_retried（phase 事件，带用户可见过程文案）。
  const guardEvent = run.events.find((event) => event.code === "stream_guard_retried");
  assert.ok(
    guardEvent,
    `stream_guard_retried event expected: ${JSON.stringify(run.events.map((event) => event.code))}`,
  );
  assert.equal(guardEvent.type, "phase");
  assert.equal(
    (guardEvent.payload as { message?: string } | null)?.message,
    "检测到异常输出，已自动降档重试",
  );

  // nudge 不落会话历史；重试后的回复落历史。
  const messages = await readSessionMessages(8101);
  assert.equal(
    messages.filter((message) =>
      message.role === "user" && String(message.content).includes("异常的乱码字符")
    ).length,
    0,
    `nudge must not be persisted: ${JSON.stringify(messages.map((message) => [message.role, String(message.content).slice(0, 40)]))}`,
  );
  assert.ok(
    messages.some((message) => message.role === "assistant" && String(message.content).includes("重试后的完整回答")),
    "assistant retry reply should be persisted",
  );
});

test("a second breaker trip during the retry fails with corrupted_stream instead of looping", async () => {
  rmSync(join(dataRoot, "coach-error.log"), { force: true });
  let calls = 0;
  const streamFn: StreamFn = async (_model, _context, options) => {
    calls += 1;
    return hangingBlockStream(signalOf(options), BLOCK_RUN);
  };

  const created = createAgentRun("stream-guard-owner", "再来一轮还是乱码", { sessionId: 8102, streamFn });
  await waitForTask(created.run_ref);

  const run = getAgentRun("stream-guard-owner", created.run_ref);
  assert.ok(run);
  assert.equal(calls, 2, `exactly one retry, no loop, got ${calls} provider calls`);
  assert.equal(run.status, "failed");
  assert.notEqual(run.error?.code, "stopped");
  assert.equal(run.error?.code, "corrupted_stream");
  assert.equal(run.error?.retryable, true);
  // domain 分类矩阵：corrupted_stream 是模型输出退化，归 model 类（非网络/存储）。
  assert.equal(run.error?.domain, "model");
  assert.ok(
    (run.partial_text ?? "").includes("█".repeat(200)),
    `partial must carry the █ text: ${JSON.stringify((run.partial_text ?? "").slice(0, 60))}`,
  );
});

test("AIMING_COOKIE_STREAM_GUARD=0 disables the breaker entirely", async () => {
  process.env.AIMING_COOKIE_STREAM_GUARD = "0";
  try {
    let calls = 0;
    const streamFn: StreamFn = async () => {
      calls += 1;
      return streamAssistant([{ type: "text", text: `进度：${BLOCK_RUN}` }], "stop");
    };
    const created = createAgentRun("stream-guard-owner", "关掉熔断再试", { sessionId: 8103, streamFn });
    await waitForTask(created.run_ref);
    const run = getAgentRun("stream-guard-owner", created.run_ref);
    assert.ok(run);
    assert.equal(calls, 1, `no retry when the kill switch is on, got ${calls} provider calls`);
    assert.equal(run.status, "succeeded");
    assert.ok(run.events.every((event) => event.code !== "stream_guard_retried"));
  } finally {
    delete process.env.AIMING_COOKIE_STREAM_GUARD;
  }
});

test("legal █ runs under the threshold pass through untouched", async () => {
  let calls = 0;
  const bar = `本局命中率进度条：${SAFE_BAR} 62%`;
  const streamFn: StreamFn = async () => {
    calls += 1;
    return streamAssistant([{ type: "text", text: bar }], "stop");
  };
  const created = createAgentRun("stream-guard-owner", "看下进度条", { sessionId: 8104, streamFn });
  await waitForTask(created.run_ref);
  const run = getAgentRun("stream-guard-owner", created.run_ref);
  assert.ok(run);
  assert.equal(calls, 1, `no retry for a legal bar, got ${calls} provider calls`);
  assert.equal(run.status, "succeeded");
  assert.ok((run.partial_text ?? "").includes(SAFE_BAR), "the bar must survive verbatim");
  assert.ok(run.events.every((event) => event.code !== "stream_guard_retried"));
});

test("FFFD input guard: two or more replacement chars rejected at create and steer, one passes", async () => {
  // create：≥2 拒绝（invalid_encoding，与 invalid_text 同样同步抛出）。
  assert.throws(
    () => createAgentRun("fffd-owner", "好好的话\uFFFD\uFFFD", { sessionId: 8201 }),
    (error: unknown) => error instanceof AgentRunError && error.code === "invalid_encoding",
  );
  assert.throws(
    () => createAgentRun("fffd-owner", "坏\uFFFD的\uFFFD输入", { sessionId: 8202 }),
    (error: unknown) => error instanceof AgentRunError && error.code === "invalid_encoding",
  );

  // create：单个 FFFD 放行。
  const passing = createAgentRun("fffd-owner", "里面有一个\uFFFD字符但整体正常", {
    sessionId: 8203,
    streamFn: () => streamAssistant([{ type: "text", text: "好的。" }], "stop"),
  });
  await waitForTask(passing.run_ref);
  const passingRun = getAgentRun("fffd-owner", passing.run_ref);
  assert.ok(passingRun);
  assert.equal(passingRun.status, "succeeded");

  // steer：同判据（gated 流把 run 钉在 running 态，harness 存活）。
  const rounds: Array<{ finish: (text: string) => void }> = [];
  const gated: StreamFn = (_model, _context) => {
    let release!: (text: string) => void;
    const released = new Promise<string>((resolve) => {
      release = resolve;
    });
    rounds.push({ finish: release });
    return released.then((text) => streamAssistant([{ type: "text", text }], "stop"));
  };
  const running = createAgentRun("fffd-owner", "先开始回答", { sessionId: 8204, streamFn: gated });
  for (let attempt = 0; attempt < 500 && rounds.length < 1; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.ok(rounds.length >= 1, "first provider round should be pinned open");

  await assert.rejects(
    () => steerAgentRun("fffd-owner", running.run_ref, { kind: "steer", text: "转向\uFFFD\uFFFD" }),
    (error: unknown) => error instanceof AgentRunError && error.code === "invalid_encoding",
  );
  // 单个 FFFD 的 steer 放行（到达 harness 队列）。
  assert.deepEqual(
    await steerAgentRun("fffd-owner", running.run_ref, { kind: "steer", text: "单个\uFFFD放行" }),
    { queued: true },
  );

  // 排空所有 gate，让 run 走到终态（steer 消息会再开一轮）。
  for (let index = 0, guard = 0; guard < 10; guard += 1) {
    if (index >= rounds.length) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    if (index >= rounds.length) break;
    rounds[index]!.finish("继续的内容");
    index += 1;
  }
  await waitForTask(running.run_ref);
});
