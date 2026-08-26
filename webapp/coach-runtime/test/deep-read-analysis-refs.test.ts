import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { StreamFn } from "../src/stream-openai-compatible.ts";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-deep-read-refs-"));
process.env.DATA_ROOT = dataRoot;

const {
  failureResponse,
  successResponse,
  COACH_RUNTIME_TURN_SCHEMA_V1,
} = await import("../src/contracts.ts");
const {
  assembleSystemPrompt,
  TIME_LINK_DISCIPLINE_POLICY,
  runCoachTurn,
} = await import("../src/turn.ts");
const { resolveSystemPrompt } = await import("../src/load-system-prompt.ts");
const { ensureAppDataDirs } = await import("../src/app-data.ts");
const {
  ensureSession,
  readConversationMeta,
  updateConversationAnalysisIds,
  updateConversationDeepReadAnalysisIds,
} = await import("../src/session-repo.ts");
const { getCoachSessionDetail } = await import("../src/sidecar-coach-data.ts");
const { streamAssistant } = await import("./pi-fake-stream.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

function turnRequest(runId: string, sessionId: string, content: string) {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA_V1,
    run_id: runId,
    session_id: sessionId,
    user_id: "test-user",
    messages: [{ role: "user", content }],
    analysis_summary: null,
    model: {
      kind: "builtin" as const,
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      credential: { type: "api_key" as const, key: "deep-read-test-key" },
    },
  };
}

/** Two-round fake stream: optional tool calls first, then a stop text reply. */
function streamFnWithToolCalls(rounds: Array<Array<Record<string, unknown>>>): StreamFn {
  let call = 0;
  return async () => {
    const roundIndex = call;
    call += 1;
    if (roundIndex < rounds.length) {
      return streamAssistant(rounds[roundIndex], "toolUse");
    }
    return streamAssistant([{ type: "text", text: "讲完了。" }], "stop");
  };
}

test("non-subject fs deep reads land in deep_read_analysis_refs without polluting analysis_refs", async () => {
  ensureAppDataDirs();
  const dir = join(dataRoot, "analyses", "7");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), "{}", "utf8");

  const response = await runCoachTurn(
    turnRequest("deep-read-turn-1", "coach-thread:201", "总结一下最近几局的表现吧。"),
    {
      streamFn: streamFnWithToolCalls([
        [
          { type: "toolCall", id: "read-1", name: "read", arguments: { path: "analyses/7/overview.json" } },
        ],
        [
          { type: "toolCall", id: "ls-1", name: "ls", arguments: { path: "analyses/7" } },
          { type: "toolCall", id: "read-2", name: "read", arguments: { path: "analyses/7/overview.json" } },
        ],
      ]),
    },
  );

  assert.equal(response.ok, true, `turn should succeed, error: ${JSON.stringify(response.error)}`);
  // 深读不进主题 refs，但必须进 deep_read_analysis_refs（按出现顺序去重），
  // 否则前端 @时间链接在纯总结类对话里永远没有视频可挂。
  assert.deepEqual(response.analysis_refs, []);
  assert.deepEqual(response.deep_read_analysis_refs, ["analysis:7"]);
});

test("subject engagement keeps analysis_refs while deep_read_analysis_refs stays clean", async () => {
  ensureAppDataDirs();
  const dir = join(dataRoot, "analyses", "9");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), "{}", "utf8");

  // 用户显式引用是主题；AI 深读另一个分析只是参照。
  writeFileSync(join(dataRoot, "analyses", "9", "extra.json"), "{}", "utf8");
  const response = await runCoachTurn(
    turnRequest("deep-read-turn-2", "coach-thread:202", "结合 analysis:9 讲讲主要问题，再对比一下以前的表现。"),
    {
      streamFn: streamFnWithToolCalls([
        [
          { type: "toolCall", id: "read-3", name: "read", arguments: { path: "analyses/9/extra.json" } },
        ],
      ]),
    },
  );

  assert.equal(response.ok, true, `turn should succeed, error: ${JSON.stringify(response.error)}`);
  assert.deepEqual(response.analysis_refs, ["analysis:9"]);
  // analyses/9 的深读与主题重复，语义上它已经是主题引用，不再进深读列表；
  // 深读列表只承载“非主题回看”兜底。
  assert.deepEqual(response.deep_read_analysis_refs, []);
});

test("response contract carries deep_read_analysis_refs with an empty default", () => {
  const success = successResponse("ok");
  assert.deepEqual(success.deep_read_analysis_refs, []);
  const failure = failureResponse({
    category: "coach_runtime",
    code: "stopped",
    message: "已停止生成。",
    retryable: true,
  });
  assert.deepEqual(failure.deep_read_analysis_refs, []);

  const explicit = successResponse("ok", [], COACH_RUNTIME_TURN_SCHEMA_V1, [], null, [], ["analysis:3"]);
  assert.deepEqual(explicit.deep_read_analysis_refs, ["analysis:3"]);
});

test("session meta unions deep-read ids separately from subject ids", async () => {
  ensureAppDataDirs();
  await ensureSession(203);
  updateConversationAnalysisIds(203, [5]);
  updateConversationDeepReadAnalysisIds(203, [11]);
  updateConversationDeepReadAnalysisIds(203, [4, 11, -2]);
  updateConversationDeepReadAnalysisIds(204, []);

  const meta203 = readConversationMeta(203);
  assert.deepEqual(meta203.analysis_session_ids, [5]);
  assert.deepEqual(meta203.deep_read_analysis_session_ids, [4, 11]);

  // 空更新不得凭空创建字段默认值以外的状态（未写入过的会话保持 undefined → 投影为 []）。
  await ensureSession(205);
  const meta205 = readConversationMeta(205);
  assert.equal(meta205.analysis_session_ids, undefined);
  assert.equal(meta205.deep_read_analysis_session_ids, undefined);
});

test("session detail projection exposes deep_read_analysis_session_ids", async () => {
  ensureAppDataDirs();
  await ensureSession(206);
  updateConversationDeepReadAnalysisIds(206, [8, 3]);

  const detail = await getCoachSessionDetail("desktop-local", 206);
  assert.deepEqual(detail.deep_read_analysis_session_ids, [3, 8]);
  assert.ok(detail.messages !== undefined);
});

test("system prompt carries the time-link discipline rule for subject-less conversations", () => {
  // 规则串存在性断言：没有主题分析挂载的对话不要输出 @X.Xs 回看引导。
  assert.match(TIME_LINK_DISCIPLINE_POLICY, /@X\.Xs/);
  assert.match(TIME_LINK_DISCIPLINE_POLICY, /不要输出 @X\.Xs 回看引导/);
  assert.match(TIME_LINK_DISCIPLINE_POLICY, /口述/);

  const composed = assembleSystemPrompt(resolveSystemPrompt(undefined), "");
  assert.ok(composed.includes(TIME_LINK_DISCIPLINE_POLICY));
});
