// B5' 语言指令块冒烟（2026-09-20 拍板：教练语言跟随当条用户消息的主要语言，
// 单套中文提示词 + 恒定注入语言块，不按 locale 分支——X-Locale 不再喂教练
// 语言）。锁定两件事：
// 1. assembleSystemPrompt 组装出的每份 system prompt 都带语言指令块（恒定
//    注入，与请求 locale 无关——语言块不消费 locale）；
// 2. 英文用户消息的完整 turn（mock 流返回英文回复）在展示归一化链路上
//    原样通过：@time 标记、英文单位表格、英文正文一字不吞。
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-language-follow-"));
process.env.DATA_ROOT = dataRoot;

const { COACH_RUNTIME_TURN_SCHEMA_V1 } = await import("../src/contracts.ts");
const { assembleSystemPrompt, LANGUAGE_FOLLOW_POLICY, runCoachTurn } = await import("../src/turn.ts");
const { resolveSystemPrompt } = await import("../src/load-system-prompt.ts");
const { ensureAppDataDirs } = await import("../src/app-data.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("language-follow block rides on every composed system prompt, locale-independent", () => {
  const composed = assembleSystemPrompt(resolveSystemPrompt(undefined), "");
  assert.ok(composed.includes(LANGUAGE_FOLLOW_POLICY));
  // 块内容锚点：跟随用户消息主要语言、英文社群术语、知识库中文照常读、
  // 其余纪律任何语言同样遵守。
  assert.match(LANGUAGE_FOLLOW_POLICY, /主要语言/);
  assert.match(LANGUAGE_FOLLOW_POLICY, /甩枪=flick/);
  assert.match(LANGUAGE_FOLLOW_POLICY, /停稳=settle/);
  assert.match(LANGUAGE_FOLLOW_POLICY, /转火=target switching/);
  assert.match(LANGUAGE_FOLLOW_POLICY, /照常检索、照常读中文条目/);
  assert.match(LANGUAGE_FOLLOW_POLICY, /任何输出语言下同样遵守/);
  // 语言块不读 locale（assembleSystemPrompt 无 locale 入参）：任意基础提示词
  // 组装结果都带块，zh/en 请求拿到同一份组装。
  assert.ok(assembleSystemPrompt("base", "skills").includes(LANGUAGE_FOLLOW_POLICY));
});

test("english user message turn keeps an English reply intact through the display pipeline", async () => {
  ensureAppDataDirs();
  const reply = [
    "Watch @2.3s — the flick overshoots the target and a micro-correction lands the click.",
    "",
    "| Drill | Duration |\n| --- | --- |\n| Flick warmup | 5 min |",
  ].join("\n");
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: reply }], "stop");

  const response = await runCoachTurn(
    {
      schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
      run_id: "language-follow-turn-1",
      session_id: "coach-thread:401",
      user_id: "test-user",
      messages: [{ role: "user", content: "Why do my flicks keep overshooting?" }],
      model: {
        kind: "builtin" as const,
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash",
        credential: { type: "api_key" as const, key: "language-follow-test-key" },
      },
    },
    { streamFn },
  );

  assert.equal(response.ok, true, `turn should succeed, error: ${JSON.stringify(response.error)}`);
  const text = response.reply ?? "";
  assert.match(text, /overshoots/);
  // @time 标记与英文单位表格在白名单归一化后原样保留。
  assert.match(text, /@2\.3s/);
  assert.match(text, /5 min/);
});

test("zh user message keeps the same composed prompt shape (no locale/branch divergence)", async () => {
  ensureAppDataDirs();
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: "回看 @2.3s，甩枪冲过头了。" }], "stop");
  const response = await runCoachTurn(
    {
      schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
      run_id: "language-follow-turn-2",
      session_id: "coach-thread:402",
      user_id: "test-user",
      messages: [{ role: "user", content: "我甩枪老冲过头怎么办？" }],
      model: {
        kind: "builtin" as const,
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash",
        credential: { type: "api_key" as const, key: "language-follow-test-key" },
      },
    },
    { streamFn },
  );
  assert.equal(response.ok, true, `turn should succeed, error: ${JSON.stringify(response.error)}`);
  assert.match(response.reply ?? "", /@2\.3s/);
});
