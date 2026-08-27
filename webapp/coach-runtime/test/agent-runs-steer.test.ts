import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-steer-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun } from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import { loadPiAi } from "../src/pi-source.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "steer-test-key" },
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

// ── Gated fake provider stream ───────────────────────────────────────────

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

type StreamLike = {
  push(event: unknown): void;
  end(result: unknown): void;
};

/** A provider round that only starts emitting once finish() is called. */
type RoundHandle = {
  /** Release with answer text; drives the loop to its next decision point. */
  finish: (text: string) => void;
  /** Serialized provider-visible context messages for assertions. */
  contextText: string;
};

function assistantMessage(content: Array<Record<string, unknown>>, stopReason: "stop") {
  return {
    role: "assistant" as const,
    content,
    api: "openai-completions",
    provider: "aiming-cookie-coach-e2e",
    model: "fixture-model",
    usage: EMPTY_USAGE,
    stopReason,
    timestamp: 0,
  };
}

/**
 * Every provider call registers a fresh held-open round on `rounds`. The
 * agent loop awaits its first event meanwhile, pinning the run inside
 * prompt() so steer / follow-up requests hit a live harness deterministically.
 */
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

// ── Route semantics without a live session ───────────────────────────────

test("POST /v1/agent-runs/:ref/steer answers explicit error codes without a session", async () => {
  await withServer(async (server) => {
    // Unknown run ref: owner-visible absence stays 404, never 500.
    const missing = await request(
      server,
      "POST",
      "/v1/agent-runs/agent_run:does-not-exist/steer",
      JSON.stringify({ text: "转向" }),
    );
    assert.equal(missing.statusCode, 404);
    assert.deepEqual(missing.json, { detail: "Coach agent run is unavailable" });

    const missingFollowUp = await request(
      server,
      "POST",
      "/v1/agent-runs/agent_run:does-not-exist/follow-up",
      JSON.stringify({ text: "接着问" }),
    );
    assert.equal(missingFollowUp.statusCode, 404);

    // Invalid bodies are rejected before any lookup.
    for (const [body, expectedDetail] of [
      ["not-json", "request body is not valid JSON"],
      [JSON.stringify([]), "Request body must be a JSON object"],
      [JSON.stringify({}), "text is required"],
      [JSON.stringify({ text: "   " }), "text must be a non-empty string"],
      [JSON.stringify({ text: "转向", drain_mode: "whenever" }), 'drain_mode must be "all" or "one-at-a-time"'],
    ] as Array<[string, string]>) {
      const res = await request(server, "POST", "/v1/agent-runs/agent_run:does-not-exist/steer", body);
      assert.equal(res.statusCode, 400, `status for body ${body}`);
      assert.deepEqual(res.json, { detail: expectedDetail }, `detail for body ${body}`);
    }
  });
});

test("POST /v1/agent-runs/:ref/steer and /follow-up refuse runs that are not actively running", async () => {
  await withServer(async (server) => {
    const run = createAgentRun("steer-owner-a", "先回答我这一个问题就好", {
      sessionId: 7001,
      streamFn: () => streamAssistant([{ type: "text", text: "已完成" }], "stop"),
    });
    await waitForTask(run.run_ref);
    const finished = getAgentRun("steer-owner-a", run.run_ref);
    assert.ok(finished);
    assert.equal(finished.status, "succeeded");

    for (const verb of ["steer", "follow-up"]) {
      const res = await request(
        server,
        "POST",
        `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/${verb}`,
        JSON.stringify({ text: "运行都结束了才来的一句话" }),
        "steer-owner-a",
      );
      assert.equal(res.statusCode, 409, `${verb} on a finished run`);
      assert.deepEqual(res.json, { detail: "run_not_steerable" });
    }
  });
});

// ── Live passthrough against a running harness ───────────────────────────

test("POST /v1/agent-runs/:ref/steer forwards into the running Pi session mid-turn", async () => {
  await withServer(async (server) => {
    const rounds: RoundHandle[] = [];
    const buildStreamFn = await makeGatedStreamFn();

    const run = createAgentRun("steer-owner-live", "开始讨论第一局", {
      sessionId: 7101,
      streamFn: buildStreamFn(rounds),
    });

    await waitForRounds(rounds, 1);

    const steered = await request(
      server,
      "POST",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/steer`,
      JSON.stringify({ text: "转向：重点讲准度问题" }),
      "steer-owner-live",
    );
    assert.equal(steered.statusCode, 200);
    assert.deepEqual(steered.json, {
      schema_version: "coach_agent_run_steer.v1",
      run_ref: run.run_ref,
      kind: "steer",
      queued: true,
    });

    // Releasing the first round lets the loop reach its steering drain point;
    // the queued user turn must appear in the NEXT provider call's context.
    rounds[0].finish("第一轮部分结论");
    await waitForRounds(rounds, 2);
    assert.ok(
      rounds[1].contextText.includes("转向：重点讲准度问题"),
      "steered text must be injected into the follow-up provider round",
    );

    rounds[1].finish("已按转向补充");
    await waitForTask(run.run_ref);

    const done = getAgentRun("steer-owner-live", run.run_ref);
    assert.ok(done);
    assert.equal(done.status, "succeeded", `run should succeed, error: ${JSON.stringify(done.error)}`);
  });
});

test("POST /v1/agent-runs/:ref/follow-up queues behind an active stop boundary", async () => {
  await withServer(async (server) => {
    const rounds: RoundHandle[] = [];
    const buildStreamFn = await makeGatedStreamFn();

    const run = createAgentRun("steer-owner-followup", "请总结刚才的练习", {
      sessionId: 7102,
      streamFn: buildStreamFn(rounds),
    });

    await waitForRounds(rounds, 1);

    const followUp = await request(
      server,
      "POST",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/follow-up`,
      JSON.stringify({ text: "追问：下一段该练什么" }),
      "steer-owner-followup",
    );
    assert.equal(followUp.statusCode, 200);
    assert.deepEqual(followUp.json, {
      schema_version: "coach_agent_run_steer.v1",
      run_ref: run.run_ref,
      kind: "follow_up",
      queued: true,
    });

    rounds[0].finish("本轮总结");
    // Follow-up drains exactly where the agent would otherwise stop: one more
    // provider round must start carrying the queued question.
    await waitForRounds(rounds, 2);
    assert.ok(
      rounds[1].contextText.includes("追问：下一段该练什么"),
      "follow-up text must ride the next provider round after the stop boundary",
    );

    rounds[1].finish("补答：下一段练跟枪");
    await waitForTask(run.run_ref);
    const done = getAgentRun("steer-owner-followup", run.run_ref);
    assert.ok(done);
    assert.equal(done.status, "succeeded", `run should succeed, error: ${JSON.stringify(done.error)}`);
  });
});

test("steer enqueue surfaces a queue_update activity event on the run stream", async () => {
  await withServer(async (server) => {
    const rounds: RoundHandle[] = [];
    const buildStreamFn = await makeGatedStreamFn();

    const run = createAgentRun("steer-owner-queue-update", "先聊第一局的走位", {
      sessionId: 7104,
      streamFn: buildStreamFn(rounds),
    });
    await waitForRounds(rounds, 1);

    // Pi 的 steer() 在返回前同步发布 queue_update；经 turn.ts 最小透传
    // （digests §11 批 5），POST 返回时事件必须已在 run events 里。
    const steered = await request(
      server,
      "POST",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/steer`,
      JSON.stringify({ text: "转向：重点讲压枪" }),
      "steer-owner-queue-update",
    );
    assert.equal(steered.statusCode, 200);

    rounds[0].finish("第一轮结论");
    await waitForRounds(rounds, 2);
    rounds[1].finish("补充完成");
    await waitForTask(run.run_ref);

    const done = getAgentRun("steer-owner-queue-update", run.run_ref);
    assert.ok(done);
    assert.equal(done.status, "succeeded", `run should succeed, error: ${JSON.stringify(done.error)}`);

    const updates = done.events.filter((event) => event.code === "queue_updated");
    assert.ok(updates.length >= 1, "engine queue_update must surface as a queue_updated activity event");
    const first = updates[0]!;
    // 合同内 type（phase），payload 携带引擎队列实况供前端 chips 对账。
    assert.equal(first.type, "phase");
    assert.equal(first.payload?.kind, "queue");
    assert.deepEqual(
      {
        steer: first.payload?.steer_count,
        follow_up: first.payload?.follow_up_count,
        next_turn: first.payload?.next_turn_count,
      },
      { steer: 1, follow_up: 0, next_turn: 0 },
    );
    // 各排水点的后续更新也要可见：队列清空后 steer_count 归零。
    const last = updates[updates.length - 1]!;
    assert.equal(last.payload?.steer_count, 0);
  });
});

test("drain_mode all forwards QueueMode so queued steers land in a single round", async () => {
  await withServer(async (server) => {
    const rounds: RoundHandle[] = [];
    const buildStreamFn = await makeGatedStreamFn();

    const run = createAgentRun("steer-owner-mode", "继续复盘", {
      sessionId: 7103,
      streamFn: buildStreamFn(rounds),
    });

    await waitForRounds(rounds, 1);

    for (const text of ["第一条：加练 tracking", "第二条：复习灵敏度"]) {
      const queued = await request(
        server,
        "POST",
        `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/steer`,
        JSON.stringify({ text, drain_mode: "all" }),
        "steer-owner-mode",
      );
      assert.equal(queued.statusCode, 200);
    }

    rounds[0].finish("轮次结论");
    await waitForRounds(rounds, 2);
    assert.ok(
      rounds[1].contextText.includes("第一条：加练 tracking")
        && rounds[1].contextText.includes("第二条：复习灵敏度"),
      '"all" drain mode must empty the whole queue into one round',
    );
    assert.ok(
      rounds[1].contextText.indexOf("第一条：加练 tracking")
        < rounds[1].contextText.indexOf("第二条：复习灵敏度"),
      "queued order is preserved",
    );

    rounds[1].finish("一次性回应两条");
    await waitForTask(run.run_ref);
    const done = getAgentRun("steer-owner-mode", run.run_ref);
    assert.ok(done);
    assert.equal(done.status, "succeeded", `run should succeed, error: ${JSON.stringify(done.error)}`);
  });
});
