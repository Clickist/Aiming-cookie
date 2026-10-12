import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-agent-runs-http-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, stopAgentRun } from "../src/agent-runs.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import { DESKTOP_TEST_TOKEN } from "./desktop-token-env.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

// 路由 owner 与直建 run 的 owner 保持一致（ownerIdFromRequest 读 x-user-id）。
const TEST_OWNER_ID = "http-test-owner";

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
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
        headers: {
          "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN,
          "x-user-id": TEST_OWNER_ID,
          ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}),
        },
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

async function listen(server: http.Server): Promise<void> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
}

async function close(server: http.Server): Promise<void> {
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

test("POST /v1/agent-runs answers 409 session_busy for a concurrent same-session turn (P1-11)", async () => {
  // 占用 run：streamFn 挂起保持 running。
  let release: (() => void) | null = null;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  const busy = createAgentRun(TEST_OWNER_ID, "占用的一问", {
    sessionId: 9101,
    streamFn: async () => {
      await blocked;
      return streamAssistant([{ type: "text", text: "好" }], "stop");
    },
  });
  const server = createSidecarServer();
  await listen(server);
  try {
    const res = await request(server, "POST", "/v1/agent-runs", JSON.stringify({
      schema_version: "coach_agent_run_request.v1",
      content: "并发的第二问",
      session_id: 9101,
    }));
    assert.equal(res.statusCode, 409);
    assert.equal((res.json as { detail: string }).detail, "session_busy");
  } finally {
    release!();
    await stopAgentRun(TEST_OWNER_ID, busy.run_ref);
    await close(server);
  }
});

test("GET /v1/agent-runs/:ref supports the events_since cursor (TS-1)", async () => {
  const run = createAgentRun(TEST_OWNER_ID, "增量一问", {
    sessionId: 9102,
    streamFn: async () => streamAssistant([], "error"),
  });
  const server = createSidecarServer();
  await listen(server);
  try {
    const full = await request(server, "GET", `/v1/agent-runs/${encodeURIComponent(run.run_ref)}?events_since=0`);
    assert.equal(full.statusCode, 200);
    const fullState = full.json as { events: Array<{ sequence: number }>; latest_sequence: number };
    assert.ok(fullState.events.length >= 1);
    assert.equal(fullState.latest_sequence, fullState.events[fullState.events.length - 1]!.sequence);

    const incremental = await request(
      server,
      "GET",
      `/v1/agent-runs/${encodeURIComponent(run.run_ref)}?events_since=${fullState.latest_sequence}`,
    );
    assert.equal(incremental.statusCode, 200);
    const incrementalState = incremental.json as { events: number[]; latest_sequence: number };
    assert.equal(incrementalState.events.length, 0);
    assert.equal(incrementalState.latest_sequence, fullState.latest_sequence);

    const invalid = await request(server, "GET", `/v1/agent-runs/${encodeURIComponent(run.run_ref)}?events_since=-1`);
    assert.equal(invalid.statusCode, 400);
    const unknown = await request(server, "GET", "/v1/agent-runs/agent_run%3Amissing?events_since=0");
    assert.equal(unknown.statusCode, 404);
  } finally {
    await close(server);
  }
});
