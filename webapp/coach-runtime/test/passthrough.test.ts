/**
 * Sidecar data-layer passthrough tests (revived from the 2026-08-22 revert):
 * thinking_text on partial revisions and args_preview / result_preview /
 * duration_ms on tool activities must ride the run state, the SSE stream,
 * and the NDJSON /v1/turn frames. Frontend wiring is out of scope.
 */
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-passthrough-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun, subscribeAgentRun } from "../src/agent-runs.ts";
import { saveProfile } from "../src/provider-store.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";
import { waitForTask } from "../src/task-manager.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

writeFileSync(join(dataRoot, "passthrough-notes.txt"), "analysis: aim down after peek", "utf8");

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "passthrough-test-key" },
});

const THINKING = "先看命中率，再对比基线。";

test("reasoning streams carry thinking_text through partial broadcasts and run state", async () => {
  const seen: Array<{ text: string; thinking: string | null }> = [];
  const created = createAgentRun("passthrough-owner", "讲讲这局的问题", {
    sessionId: 71,
    streamFn: async () =>
      streamAssistant([
        { type: "thinking", thinking: THINKING },
        { type: "text", text: "这局的主要问题是第 6 杆的开镜延迟。" },
      ], "stop"),
  });
  const unsubscribe = subscribeAgentRun("passthrough-owner", created.run_ref, {
    onPartial: (text, thinking) => {
      seen.push({ text, thinking });
    },
  });
  await waitForTask(created.run_ref);
  unsubscribe();

  const final = getAgentRun("passthrough-owner", created.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed: ${JSON.stringify(final.error)}`);

  // The first broadcast is thinking-only: no answer text yet, but the live
  // thinking window is already attached. text stays a string (cumulative "")
  // so text-only listeners never see a null payload.
  assert.ok(seen.length >= 1, `expected at least one partial broadcast, got ${seen.length}`);
  assert.equal(seen[0].text, "");
  assert.equal(seen[0].thinking, THINKING);
  assert.ok(seen.every((item) => typeof item.text === "string"));
  assert.equal(final.partial_thinking, THINKING);
});

test("non-reasoning streams keep thinking_text null end to end", async () => {
  const seen: Array<{ text: string; thinking: string | null }> = [];
  const created = createAgentRun("passthrough-owner", "直接总结。", {
    sessionId: 72,
    streamFn: async () =>
      streamAssistant([{ type: "text", text: "好的，直接总结如下。" }], "stop"),
  });
  const unsubscribe = subscribeAgentRun("passthrough-owner", created.run_ref, {
    onPartial: (text, thinking) => {
      seen.push({ text, thinking });
    },
  });
  await waitForTask(created.run_ref);
  unsubscribe();

  const final = getAgentRun("passthrough-owner", created.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed: ${JSON.stringify(final.error)}`);
  assert.ok(seen.length >= 1);
  assert.ok(seen.every((item) => item.thinking === null));
  assert.equal(final.partial_thinking, null);
});

test("tool activities carry args_preview, result_preview, and duration_ms", async () => {
  let providerCalls = 0;
  const streamFn: StreamFn = async () => {
    providerCalls += 1;
    if (providerCalls === 1) {
      return streamAssistant([{
        type: "toolCall",
        id: "read-call",
        name: "read",
        arguments: { path: "passthrough-notes.txt" },
      }], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "已读取训练记录。" }], "stop");
  };

  const created = createAgentRun("passthrough-owner", "读一下训练记录", { sessionId: 73, streamFn });
  await waitForTask(created.run_ref);

  const final = getAgentRun("passthrough-owner", created.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed: ${JSON.stringify(final.error)}`);

  const toolEvents = final.events.filter((event) => event.type === "tool");
  const started = toolEvents.find((event) => event.code === "tool_started");
  const completed = toolEvents.find((event) => event.code === "tool_completed");
  assert.ok(started, "tool_started activity should reach run.events");
  assert.ok(completed, "tool_completed activity should reach run.events");

  assert.equal(started.payload?.args_preview, '{"path":"passthrough-notes.txt"}');
  assert.equal(started.payload?.result_preview, undefined);
  assert.equal(started.payload?.duration_ms, undefined);

  assert.match(String(completed.payload?.result_preview), /aim down after peek/);
  assert.equal(typeof completed.payload?.duration_ms, "number");
  assert.ok((completed.payload?.duration_ms as number) >= 0);
});

test("thinking passthrough keeps only the trailing window (8KB buffer, 12KB transport cap)", async () => {
  const long = `${"头".repeat(1)}${"思".repeat(9_000)}`;
  const created = createAgentRun("passthrough-owner", "长思考", {
    sessionId: 74,
    streamFn: async () =>
      streamAssistant([
        { type: "thinking", thinking: long },
        { type: "text", text: "结论如下。" },
      ], "stop"),
  });
  await waitForTask(created.run_ref);

  const final = getAgentRun("passthrough-owner", created.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", `run should succeed: ${JSON.stringify(final.error)}`);
  // The live buffer is capped at 8KB (trailing window); the 12KB transport
  // slice in agent-runs must never deliver more than that.
  assert.equal(final.partial_thinking?.length, 8_000);
  assert.equal(final.partial_thinking, long.slice(-8_000));
  assert.ok(!final.partial_thinking?.includes("头"));
});

function collectSseFrames(
  server: http.Server,
  path: string,
  headers: http.OutgoingHttpHeaders,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      { host: "127.0.0.1", port: address.port, method: "GET", path, headers },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      },
    );
    req.on("error", reject);
    req.end();
  });
}

function sseEvents(body: string, name: string): Array<Record<string, unknown>> {
  const frames: Array<Record<string, unknown>> = [];
  for (const block of body.split("\n\n")) {
    const lines = block.split("\n");
    const eventLine = lines.find((line) => line.startsWith("event: "));
    if (eventLine !== `event: ${name}`) continue;
    const dataLine = lines.find((line) => line.startsWith("data: "));
    if (dataLine) frames.push(JSON.parse(dataLine.slice("data: ".length)) as Record<string, unknown>);
  }
  return frames;
}

test("SSE partial frames carry thinking_text alongside a string text payload", async () => {
  // The delayed stream keeps the run alive long enough for the SSE
  // subscription to attach before the first thinking broadcast.
  const created = createAgentRun("passthrough-sse-owner", "推理一下", {
    sessionId: 75,
    streamFn: async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
      return streamAssistant([
        { type: "thinking", thinking: "先分析瞄准轨迹。" },
        { type: "text", text: "分析完成。" },
      ], "stop");
    },
  });

  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const body = await collectSseFrames(
      server,
      `/v1/agent-runs/${encodeURIComponent(created.run_ref)}/stream`,
      { "Connection": "close", "X-User-Id": "passthrough-sse-owner" },
    );
    const partials = sseEvents(body, "partial");
    assert.ok(partials.length >= 1, `expected partial frames, body: ${body}`);
    for (const frame of partials) {
      assert.equal(frame.schema_version, "coach_agent_run_stream.v1");
      assert.equal(frame.type, "partial");
      assert.equal(typeof frame.text, "string");
    }
    assert.ok(
      partials.some((frame) => frame.thinking_text === "先分析瞄准轨迹。"),
      `a partial frame should carry the live thinking: ${JSON.stringify(partials)}`,
    );
    assert.ok(partials.every((frame) => frame.thinking_text === null || typeof frame.thinking_text === "string"));
    const done = sseEvents(body, "done");
    assert.equal(done.length, 1);
    assert.equal(done[0].status, "succeeded");
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("NDJSON partial frames accept thinking-only revisions and carry thinking_text", async () => {
  const server = createSidecarServer({
    turnRunner: async (_request, options) => {
      await options?.onPartial?.({
        revision: 1,
        text: null,
        thinking_text: "推理中……",
        elapsed_ms: 5,
        provider_rounds: 1,
      });
      await options?.onPartial?.({
        revision: 2,
        text: "答案如下。",
        thinking_text: "推理中……",
        elapsed_ms: 9,
        provider_rounds: 1,
      });
      return {
        schema_version: "coach_runtime_turn.v1",
        run_id: "agent_run:passthrough-ndjson",
        ok: true,
        reply: "答案如下。",
        partial_reply: null,
        error: null,
        notes: [],
        tool_events: [],
      };
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const body = await new Promise<string>((resolve, reject) => {
      const req = http.request(
        {
          host: "127.0.0.1",
          port: address.port,
          method: "POST",
          path: "/v1/turn",
          headers: { "Content-Type": "application/json", "Accept": "application/x-ndjson" },
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk: Buffer) => chunks.push(chunk));
          res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
        },
      );
      req.on("error", reject);
      req.end("{}");
    });
    const frames = body.trim().split("\n").map((line) => JSON.parse(line) as Record<string, unknown>);
    assert.deepEqual(frames.map((frame) => frame.type), ["partial", "partial", "final"]);

    // Thinking-only revision: text is null, thinking rides the frame.
    assert.equal(frames[0].revision, 1);
    assert.equal(frames[0].text, null);
    assert.equal(frames[0].thinking_text, "推理中……");
    assert.equal(frames[1].text, "答案如下。");
    assert.equal(frames[1].thinking_text, "推理中……");
    assert.equal((frames[2].response as { ok: boolean }).ok, true);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});
