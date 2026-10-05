/**
 * 开场分析 kickoff 双语（2026-09 i18n 收尾）。
 *
 * 语言链路：开场回合由系统代用户合成（用户尚未输入），唯一语言信号是
 * 应用语言——POST /coach/intro-session 的 X-Locale（前端首启 locale，经
 * apiFetchSidecar 恒带）→ introKickoffPrompt(locale)，locale 同时随 run
 * 记录透传（RunRecord.locale）。教练随后跟随 kickoff 语言（语言指令块口径：
 * kickoff 即本回合的用户消息）。
 *
 * 单独一个 DATA_ROOT：intro session 一生一次，必须从未置位状态起步。
 */
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-intro-kickoff-locale-"));
process.env.DATA_ROOT = dataRoot;

import { saveProfile } from "../src/provider-store.ts";
import {
  INTRO_KICKOFF_SENTINEL,
  introKickoffPrompt,
  isIntroKickoffMessage,
} from "../src/intro-kickoff.ts";
import { readConversationMeta } from "../src/session-repo.ts";
import { DESKTOP_TEST_TOKEN } from "./desktop-token-env.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

/** 中文 kickoff 逐字锁定（1005 主线化改版）：与 src/intro-kickoff.ts 保持逐字节一致。 */
const ZH_PROMPT =
  `${INTRO_KICKOFF_SENTINEL} 现在开始本次「开场分析」。按 intro-session skill 的「开场即主线」发首条消息：` +
  "一句自我介绍，然后直接给主线任务（AC 需要一局真实数据才能开始帮你；现在就去打一局，还没装 KovaaK 的先装；打完回来喊我分析——这一局同时确认 AC 在你电脑上一切运作正常），末行带主线卡。" +
  "发这条的同时并行调用 intro_context.get 和 user_profile.get 拿背景数据；本地已有记录则按 skill 规则开场直接分析最近一局。" +
  "不要问任何了解用户的问题（四问在首次分析完成后才出场），也不要用任何引导用的假用户消息。";

test("zh kickoff stays byte-identical and remains the default", () => {
  assert.equal(introKickoffPrompt("zh-CN"), ZH_PROMPT);
  assert.equal(introKickoffPrompt(), ZH_PROMPT, "default locale must stay zh-CN");
});

test("en kickoff carries the same instructions in English", () => {
  const en = introKickoffPrompt("en-US");
  // 哨兵是两种语言共用的机器标记：UI 过滤按它匹配，与正文语言无关。
  assert.ok(en.startsWith(INTRO_KICKOFF_SENTINEL));
  assert.ok(isIntroKickoffMessage(en), "en kickoff must be filtered from UI reads too");
  assert.notEqual(en, ZH_PROMPT);
  assert.ok(en.includes("main quest"), "en kickoff must carry the main-quest instruction");
  assert.ok(!ZH_PROMPT.includes("平时都玩什么游戏"), "kickoff must NOT ask the old first question (四问后移)");
  assert.ok(en.includes("intro_context.get"));
  assert.ok(en.includes("user_profile.get"));
  assert.doesNotMatch(en.slice(INTRO_KICKOFF_SENTINEL.length), /[\u4e00-\u9fff]/, "body must be English");
});

function postJson(
  server: http.Server,
  path: string,
  headers: Record<string, string>,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolvePromise, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      {
        host: "127.0.0.1",
        port: address.port,
        method: "POST",
        path,
        headers: { "x-aiming-cookie-desktop-token": DESKTOP_TEST_TOKEN, ...headers },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolvePromise({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
        });
      },
    );
    req.on("error", reject);
    req.end();
  });
}

/** 从 Pi 会话 JSONL 原文里取 kickoff 用户消息（UI 读取口径会过滤它）。 */
function readKickoffFromSession(sessionId: number): string | null {
  // Pi 会话库按 cwd 嵌套：conversations/--coach--/<timestamp>_<id>.jsonl。
  const sessionDir = join(dataRoot, "conversations", "--coach--");
  let files: string[];
  try {
    files = readdirSync(sessionDir).filter((file) => file.endsWith(`_${sessionId}.jsonl`));
  } catch {
    return null;
  }
  for (const file of files) {
    let raw: string;
    try {
      raw = readFileSync(join(sessionDir, file), "utf8");
    } catch {
      continue;
    }
    for (const line of raw.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line) as {
          type?: unknown;
          message?: { role?: unknown; content?: unknown };
        };
        if (entry.type !== "message" || entry.message?.role !== "user") continue;
        const content = entry.message.content;
        if (!Array.isArray(content)) continue;
        const text = content
          .filter((part): part is { type: string; text: string } =>
            typeof part === "object" && part !== null && (part as { type?: unknown }).type === "text")
          .map((part) => part.text)
          .join("");
        if (isIntroKickoffMessage(text)) return text;
      } catch {
        // 非完整行（写入中）：等下一轮轮询。
      }
    }
  }
  return null;
}

async function waitFor<T>(probe: () => T | null, timeoutMs: number): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = probe();
    if (value !== null) return value;
    if (Date.now() > deadline) throw new Error("waitFor timed out");
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

test("POST /coach/intro-session with X-Locale: en-US synthesizes the English kickoff and title", async () => {
  // Provider 可用（凭据离线可判）才会发 kickoff；key 是假的，只让 run 起得来。
  saveProfile({
    kind: "builtin",
    provider_id: "opencode-go",
    model_id: "deepseek-v4-flash",
    credential: { type: "api_key", key: "intro-kickoff-locale-key" },
  });
  const server = createSidecarServer();
  await new Promise<void>((resolvePromise) => server.listen(0, "127.0.0.1", () => resolvePromise()));
  try {
    const headers = { "X-User-Id": "intro-locale-owner", "X-Locale": "en-US" };
    const post = await postJson(server, "/coach/intro-session", headers);
    assert.equal(post.statusCode, 200);
    const body = post.json as {
      session_id: number;
      run_ref: string | null;
      provider_ready: boolean;
    };
    assert.equal(body.provider_ready, true);
    assert.ok(typeof body.run_ref === "string" && body.run_ref.startsWith("agent_run:"));

    // kickoff 用户消息在 provider 调用之前就已持久化（agent-runs 先
    // appendUserMessageOnce 再起 turn）——轮询 JSONL 即可离线断言语言。
    const kickoff = await waitFor(() => readKickoffFromSession(body.session_id), 5000);
    assert.equal(kickoff, introKickoffPrompt("en-US"), "kickoff run must carry the English prompt");

    // 会话标题同链路双语：首启 locale=en-US → Intro Session（title_source=user 钉死）。
    assert.equal(readConversationMeta(body.session_id).title, "Intro Session");

    // 收尾：停掉后台 run，避免假凭据的网络回合继续跑。
    const stop = await postJson(server, `/v1/agent-runs/${encodeURIComponent(body.run_ref!)}/stop`, headers);
    assert.equal(stop.statusCode, 200);
  } finally {
    await new Promise<void>((resolvePromise, reject) =>
      server.close((error) => (error ? reject(error) : resolvePromise())),
    );
  }
});
