/**
 * 开场分析 Provider 可用性闸门 + 自愈（PRD §6.1.1）。
 *
 * 单测聚焦 sidecar 行为：当前档凭据此刻解析不出来时不创建 kickoff run
 * （run_ref=null、provider_ready=false），只在会话真正有可见消息后才不再补发。
 * 单独一个 DATA_ROOT：flag 是「一生一次」，必须从未置位状态起步。
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-intro-gate-"));
process.env.DATA_ROOT = dataRoot;

import { saveProfile } from "../src/provider-store.ts";
import { ensureIntroSession } from "../src/intro-session.ts";
import { ensureSession } from "../src/session-repo.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

function request(
  server: http.Server,
  method: string,
  path: string,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolvePromise, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request({ host: "127.0.0.1", port: address.port, method, path }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolvePromise({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
      });
    });
    req.on("error", reject);
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

test("provider unavailable: session is created but no kickoff run, provider_ready=false", async () => {
  await withServer(async (server) => {
    // 全新的 DATA_ROOT：当前档缺失，也无凭据可解析。
    const before = await request(server, "GET", "/coach/intro-session");
    assert.deepEqual(before.json, { created: false, session_id: null, has_messages: false });

    const post = await request(server, "POST", "/coach/intro-session");
    assert.equal(post.statusCode, 200);
    const body = post.json as {
      session_id: number;
      created: boolean;
      run_ref: string | null;
      provider_ready: boolean;
    };
    assert.equal(body.created, true);
    assert.ok(Number.isInteger(body.session_id) && body.session_id > 0);
    // 凭据不可用：绝不发 kickoff（否则只会挂起 provider_waiting 后随重启丢失）。
    assert.equal(body.provider_ready, false);
    assert.equal(body.run_ref, null);

    const after = await request(server, "GET", "/coach/intro-session");
    assert.deepEqual(after.json, {
      created: true,
      session_id: body.session_id,
      has_messages: false,
    });
  });
});

test("provider becomes ready: a later POST self-heals and creates the kickoff run", async () => {
  // 用户后来在设置里配好了 Provider（带 key 的内置档，凭据解析离线可判）。
  saveProfile({
    kind: "builtin",
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key", key: "intro-gate-key" },
  });

  await withServer(async (server) => {
    const post = await request(server, "POST", "/coach/intro-session");
    assert.equal(post.statusCode, 200);
    const body = post.json as {
      session_id: number;
      created: boolean;
      run_ref: string | null;
      provider_ready: boolean;
    };
    // flag 早已置位（会话已存在），这里只补发 kickoff，不重复报名。
    assert.equal(body.created, false);
    assert.equal(body.provider_ready, true);
    assert.ok(typeof body.run_ref === "string" && body.run_ref.startsWith("agent_run:"));

    // 清掉后台 kickoff，避免它继续打网络并影响下一条断言。
    await request(server, "POST", `/v1/agent-runs/${encodeURIComponent(body.run_ref!)}/stop`);
  });
});

test("session already has messages: kickoff is not resent, has_messages=true", async () => {
  const ensured = await ensureIntroSession();
  const session = await ensureSession(ensured.session_id);
  await session.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "开场分析已开讲" }],
    timestamp: Date.now(),
  });

  await withServer(async (server) => {
    const status = await request(server, "GET", "/coach/intro-session");
    assert.deepEqual(status.json, {
      created: true,
      session_id: ensured.session_id,
      has_messages: true,
    });

    const post = await request(server, "POST", "/coach/intro-session");
    assert.equal(post.statusCode, 200);
    const body = post.json as { run_ref: string | null; provider_ready: boolean };
    assert.equal(body.run_ref, null, "session with messages must not get another kickoff run");
    assert.equal(body.provider_ready, true);
  });
});
