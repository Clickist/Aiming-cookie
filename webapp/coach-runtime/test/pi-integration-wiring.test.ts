import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-pi-wiring-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun } from "../src/agent-runs.ts";
import { extractUsage, shouldCompactNow } from "../src/turn.ts";
import { saveProfile } from "../src/provider-store.ts";
import { loadPiAgent, loadPiAi } from "../src/pi-source.ts";
import { waitForTask } from "../src/task-manager.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import { streamAssistant } from "./pi-fake-stream.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "pi-wiring-test-key" },
});

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
  owner?: string,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      {
        host: "127.0.0.1",
        port: address.port,
        method,
        path,
        headers: body || owner
          ? {
              ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}),
              ...(owner ? { "X-User-Id": owner } : {}),
            }
          : undefined,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
        });
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function withServer(run: (server: http.Server) => Promise<void>): Promise<void> {
  const server = createSidecarServer();
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      try {
        await run(server);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
      }
    });
  });
}

// ── 带非零 usage 的门控假流 ────────────────────────────────────────────────

const USAGE = {
  input: 1234,
  output: 567,
  cacheRead: 8,
  cacheWrite: 0,
  totalTokens: 1809,
  cost: { total: 0.0021 },
};

type StreamLike = {
  push(event: unknown): void;
  end(result: unknown): void;
};

type RoundHandle = {
  finish: (text: string) => void;
  contextText: string;
};

function assistantMessage(content: Array<Record<string, unknown>>, stopReason: "stop") {
  return {
    role: "assistant" as const,
    content,
    api: "openai-completions",
    provider: "aiming-cookie-coach-e2e",
    model: "fixture-model",
    usage: USAGE,
    stopReason,
    timestamp: 0,
  };
}

async function makeGatedStreamFn(): Promise<(rounds: RoundHandle[]) => StreamFn> {
  const ai = await loadPiAi();
  const createStream = ai.createAssistantMessageEventStream as unknown as () => StreamLike;
  return (rounds: RoundHandle[]): StreamFn =>
    (_model, context) => {
      const initial = assistantMessage([], "stop");
      let release!: (text: string) => void;
      const released = new Promise<string>((resolve) => {
        release = resolve;
      });
      const stream = createStream();
      void released.then((text) => {
        const final = assistantMessage([{ type: "text", text }], "stop");
        stream.push({ type: "start", partial: initial });
        stream.push({ type: "text_start", contentIndex: 0, partial: initial });
        stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: final });
        stream.push({ type: "text_end", contentIndex: 0, content: text, partial: final });
        stream.push({ type: "done", reason: "stop", message: final });
        stream.end(final);
      });
      rounds.push({
        finish: (text: string) => {
          release(text);
        },
        contextText: JSON.stringify(context.messages),
      });
      // Held open: nothing is emitted until finish() resolves `released`.
      return Promise.resolve(stream as unknown as ReturnType<StreamFn>);
    };
}

async function waitForRounds(rounds: RoundHandle[], count: number): Promise<void> {
  for (let attempt = 0; attempt < 500 && rounds.length < count; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.ok(rounds.length >= count, `expected ${count} provider rounds, saw ${rounds.length}`);
}

// ── extractUsage（纯函数）──────────────────────────────────────────────────

test("extractUsage maps pi usage fields and normalizes cost object", () => {
  const mapped = extractUsage({
    role: "assistant",
    usage: { input: 10, output: 5, cacheRead: 2, cacheWrite: 3, reasoning: 7, totalTokens: 27, cost: { total: 0.5 } },
  });
  assert.deepEqual(mapped, {
    input_tokens: 10,
    output_tokens: 5,
    cache_read_tokens: 2,
    cache_write_tokens: 3,
    reasoning_tokens: 7,
    total_tokens: 27,
    cost: 0.5,
  });
});

test("extractUsage returns null for missing or empty usage", () => {
  assert.equal(extractUsage(null), null);
  assert.equal(extractUsage({ role: "assistant" }), null);
  assert.equal(extractUsage({ role: "assistant", usage: {} }), null);
});

// ── shouldCompactNow（pi 内建估算 + 阈值判定的接线）────────────────────────

test("shouldCompactNow refuses unknown context windows without touching the session", async () => {
  const probes: string[] = [];
  const session = {
    getBranch: async () => {
      probes.push("called");
      return [];
    },
  };
  assert.equal(await shouldCompactNow(session, 0), false);
  assert.equal(await shouldCompactNow(session, -5), false);
  assert.deepEqual(probes, []);
});

test("shouldCompactNow follows the last assistant usage against the context window", async () => {
  const { InMemorySessionRepo } = (await loadPiAgent()) as {
    InMemorySessionRepo: new () => { create: () => Promise<any> };
  };

  const big = await new InMemorySessionRepo().create();
  await big.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }] });
  await big.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "hello" }],
    usage: { input: 900_000, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 900_010 },
  });
  assert.equal(await shouldCompactNow(big, 128_000), true);
  assert.equal(await shouldCompactNow(big, 0), false);

  const small = await new InMemorySessionRepo().create();
  await small.appendMessage({ role: "user", content: [{ type: "text", text: "hi" }] });
  await small.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "hello" }],
    usage: { input: 100, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 110 },
  });
  assert.equal(await shouldCompactNow(small, 128_000), false);
});

// ── live：usage 透出到 run state ──────────────────────────────────────────

test("agent run state surfaces provider usage from the final assistant message", async () => {
  const rounds: RoundHandle[] = [];
  const buildStreamFn = await makeGatedStreamFn();

  const run = createAgentRun("wiring-owner-usage", "看看这轮用了多少", {
    sessionId: 7501,
    streamFn: buildStreamFn(rounds),
  });

  await waitForRounds(rounds, 1);
  rounds[0].finish("回答正文");
  await waitForTask(run.run_ref);

  const done = getAgentRun("wiring-owner-usage", run.run_ref);
  assert.ok(done);
  assert.equal(done.status, "succeeded", `error: ${JSON.stringify(done.error)}`);
  assert.ok(done.usage, "run state must carry usage");
  assert.equal(done.usage?.total_tokens, USAGE.totalTokens);
  assert.equal(done.usage?.input_tokens, USAGE.input);
  assert.equal(done.usage?.cost, USAGE.cost.total);
});

// ── live：next_turn 排进同 run 的下一轮 ────────────────────────────────────

test("POST /v1/agent-runs/:ref/next-turn drains into a same-run follow-on turn", async () => {
  await withServer(async (server) => {
    const rounds: RoundHandle[] = [];
    const buildStreamFn = await makeGatedStreamFn();

    const run = createAgentRun("wiring-owner-nextturn", "开始第一轮讨论", {
      sessionId: 7502,
      streamFn: buildStreamFn(rounds),
    });

    await waitForRounds(rounds, 1);

    const queued = await request(
      server,
      "POST",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/next-turn`,
      JSON.stringify({ text: "追问：紧接着展开第二点" }),
      "wiring-owner-nextturn",
    );
    assert.equal(queued.statusCode, 200);
    assert.deepEqual(queued.json, {
      schema_version: "coach_agent_run_steer.v1",
      run_ref: run.run_ref,
      kind: "next_turn",
      queued: true,
    });

    // 首轮放行 → 排水循环立刻用同一 harness 发起第二轮（承载排队文本）。
    rounds[0].finish("第一轮结论");
    await waitForRounds(rounds, 2);
    assert.ok(
      rounds[1].contextText.includes("追问：紧接着展开第二点"),
      "next-turn text must lead the follow-on provider round",
    );

    rounds[1].finish("第二轮展开完成");
    await waitForTask(run.run_ref);

    const done = getAgentRun("wiring-owner-nextturn", run.run_ref);
    assert.ok(done);
    assert.equal(done.status, "succeeded", `error: ${JSON.stringify(done.error)}`);
  });
});

test("POST /v1/agent-runs/:ref/next-turn answers explicit error codes like steer", async () => {
  await withServer(async (server) => {
    const missing = await request(
      server,
      "POST",
      "/v1/agent-runs/agent_run:does-not-exist/next-turn",
      JSON.stringify({ text: "迟到的追问" }),
    );
    assert.equal(missing.statusCode, 404);
    assert.deepEqual(missing.json, { detail: "Coach agent run is unavailable" });

    const run = createAgentRun("wiring-owner-late", "先跑完这一轮", {
      sessionId: 7503,
      streamFn: () => streamAssistant([{ type: "text", text: "已完成" }], "stop"),
    });
    await waitForTask(run.run_ref);

    const late = await request(
      server,
      "POST",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/next-turn`,
      JSON.stringify({ text: "运行都结束了才来的一句话" }),
      "wiring-owner-late",
    );
    assert.equal(late.statusCode, 409);
    assert.deepEqual(late.json, { detail: "run_not_steerable" });
  });
});

// ── live：会话级 stats 端点 ────────────────────────────────────────────────

test("GET /v1/sessions/:id/stats returns pi session stats; unknown id answers 404", async () => {
  await withServer(async (server) => {
    const created = await request(server, "POST", "/v1/sessions", JSON.stringify({ title: "统计测试" }));
    assert.equal(created.statusCode, 201);
    const sessionId = (created.json as { id: number }).id;

    const stats = await request(server, "GET", `/v1/sessions/${sessionId}/stats`);
    assert.equal(stats.statusCode, 200);
    const body = stats.json as { session_id: number; stats: { messageCount?: unknown } };
    assert.equal(body.session_id, sessionId);
    assert.equal(typeof body.stats.messageCount, "number");

    const missing = await request(server, "GET", "/v1/sessions/999999/stats");
    assert.equal(missing.statusCode, 404);

    const invalid = await request(server, "GET", "/v1/sessions/not-a-number/stats");
    assert.equal(invalid.statusCode, 400);
  });
});
