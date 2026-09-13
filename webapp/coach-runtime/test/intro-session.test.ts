import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-intro-session-"));
process.env.DATA_ROOT = dataRoot;

import { COACH_RUNTIME_TURN_SCHEMA_V1 } from "../src/contracts.ts";
import { ensureIntroSession, readIntroSessionFlag } from "../src/intro-session.ts";
import { INTRO_KICKOFF_SENTINEL, isIntroKickoffMessage } from "../src/intro-kickoff.ts";
import { userProfileUpdateError } from "../src/intro-context-native.ts";
import { saveProfile } from "../src/provider-store.ts";
import { readSessionMessages } from "../src/session-repo.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import { runCoachTurn } from "../src/turn.ts";
import { streamAssistant } from "./pi-fake-stream.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";

const skillsDir = resolve(dirname(fileURLToPath(import.meta.url)), "../prompts/skills");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

function request(server: http.Server, method: string, path: string): Promise<{ statusCode: number; json: unknown }> {
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

function turnRequest(sessionId: string, content: string) {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
    run_id: `intro-turn-${sessionId}`,
    session_id: sessionId,
    user_id: "test-user",
    messages: [{ role: "user", content }],
    model: {
      kind: "builtin" as const,
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      credential: { type: "api_key" as const, key: "intro-test-key" },
    },
  };
}

test("intro-session SKILL.md carries the hard constraints", () => {
  const raw = readFileSync(join(skillsDir, "intro-session", "SKILL.md"), "utf8");
  for (const fragment of [
    "开场分析",
    "禁止动作层断言",
    "社区基准训练单",
    "steam://run/824270/?action=jump-to-playlist;sharecode=KovaaKsScreamingPulledEgg",
    "steam_profile_url",
    "处方卡等第一份正经分析后解锁",
    "没有也行",
  ]) {
    assert.ok(raw.includes(fragment), `SKILL.md must contain ${fragment}`);
  }
  assert.ok(raw.includes("禁止出现「歌单」「官方基准」字样"), "SKILL.md must state the banned-term rule");
  for (const line of raw.split(/\r?\n/)) {
    if (line.includes("歌单") || line.includes("官方基准")) {
      assert.ok(line.includes("禁止"), `banned term must only appear in the prohibition line: ${line}`);
    }
  }
});

test("intro-session endpoints create idempotently and persist the flag", async () => {
  // Provider 可用（当前档凭据此刻解析得出）才允许发 kickoff：先落一份带 key 的
  // 内置档，否则 provider 闸门会拦下首条消息（见 intro-kickoff-gate.test.ts）。
  saveProfile({
    kind: "builtin",
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key", key: "intro-test-key" },
  });
  const server = createSidecarServer();
  await new Promise<void>((resolvePromise) => server.listen(0, "127.0.0.1", () => resolvePromise()));
  try {
    const before = await request(server, "GET", "/coach/intro-session");
    assert.equal(before.statusCode, 200);
    assert.deepEqual(before.json, { created: false, session_id: null, has_messages: false });

    const first = await request(server, "POST", "/coach/intro-session");
    assert.equal(first.statusCode, 200);
    const firstBody = first.json as {
      session_id: number;
      created: boolean;
      run_ref: string | null;
      provider_ready: boolean;
    };
    assert.equal(firstBody.created, true);
    assert.ok(Number.isInteger(firstBody.session_id) && firstBody.session_id > 0);
    // First launch auto-sends the first Coach message via a kickoff agent run.
    assert.equal(firstBody.provider_ready, true);
    assert.ok(typeof firstBody.run_ref === "string" && firstBody.run_ref.startsWith("agent_run:"));

    const second = await request(server, "POST", "/coach/intro-session");
    assert.equal(second.statusCode, 200);
    const secondBody = second.json as {
      session_id: number;
      created: boolean;
      run_ref: string | null;
      provider_ready: boolean;
    };
    assert.equal(secondBody.session_id, firstBody.session_id, "second POST must return the same session id");
    assert.equal(secondBody.created, false);
    assert.equal(secondBody.run_ref ?? null, null, "second POST must not start another kickoff run");
    assert.equal(secondBody.provider_ready, true);

    const after = await request(server, "GET", "/coach/intro-session");
    assert.deepEqual(after.json, {
      created: true,
      session_id: firstBody.session_id,
      has_messages: false,
    });

    // Flag is durable on disk in the sidecar config dir.
    const flagPath = join(dataRoot, "config", "intro-session.json");
    assert.ok(existsSync(flagPath), "flag file must exist");
    const flag = JSON.parse(readFileSync(flagPath, "utf8")) as Record<string, unknown>;
    assert.equal(flag.created, true);
    assert.equal(flag.session_id, firstBody.session_id);
    assert.equal(typeof flag.created_at, "string");
  } finally {
    await new Promise<void>((resolvePromise, reject) =>
      server.close((error) => (error ? reject(error) : resolvePromise())),
    );
  }
});

test("intro skill is injected only into the intro session", async () => {
  const ensured = await ensureIntroSession();
  const seenSystemPrompts: string[] = [];
  const captureStreamFn: StreamFn = (_model, context) => {
    seenSystemPrompts.push(String((context as { systemPrompt?: unknown }).systemPrompt ?? ""));
    return streamAssistant([{ type: "text", text: "好的。" }], "stop");
  };

  const intro = await runCoachTurn(
    turnRequest(`coach-thread:${ensured.session_id}`, "开场"),
    { streamFn: captureStreamFn },
  );
  assert.equal(intro.ok, true, `intro turn should succeed: ${JSON.stringify(intro.error)}`);
  assert.ok(
    seenSystemPrompts[0].includes("<intro_session_skill>"),
    "intro session system prompt must contain the intro skill block",
  );
  assert.ok(seenSystemPrompts[0].includes("禁止动作层断言"));

  // A different (non-intro) session must not get the intro flow.
  const other = await runCoachTurn(
    turnRequest("coach-thread:987654", "日常问题"),
    { streamFn: captureStreamFn },
  );
  assert.equal(other.ok, true);
  const otherPrompt = seenSystemPrompts[seenSystemPrompts.length - 1];
  assert.ok(!otherPrompt.includes("<intro_session_skill>"), "other sessions must not inject the intro skill");
  assert.ok(!otherPrompt.includes("<name>intro-session</name>"), "intro skill must stay out of available_skills");
});

test("intro kickoff user message never shows in UI reads", async () => {
  const ensured = await ensureIntroSession();
  assert.equal(isIntroKickoffMessage(`${INTRO_KICKOFF_SENTINEL} 开始`), true);
  assert.equal(isIntroKickoffMessage("平时都玩什么游戏？"), false);
  // The synthesized kickoff message is persisted for the provider context but
  // filtered out of the visible message list the UI reads.
  const messages = await readSessionMessages(ensured.session_id);
  assert.ok(
    messages.every((message) => !isIntroKickoffMessage(message.content)),
    `UI reads leaked the kickoff message: ${JSON.stringify(messages)}`,
  );
});

test("user_profile.update rejects non-whitelisted fields and accepts partial answers", () => {
  assert.match(String(userProfileUpdateError({ steam_id: "x" })), /unsupported fields/);
  assert.match(String(userProfileUpdateError({})), /at least one profile field/);
  assert.equal(userProfileUpdateError({ self_assessment: "追踪还行" }), null);
  assert.equal(
    userProfileUpdateError({
      games: ["CS2"],
      experience: "小半年",
      goal: "甩枪上 Fox",
      steam_profile_url: "https://steamcommunity.com/id/vapor/",
    }),
    null,
  );
  assert.match(String(userProfileUpdateError({ games: "CS2" })), /array of strings/);
});
