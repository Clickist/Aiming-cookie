// adaptive-thinking-fallback 测试（2026-10-12 野外 haiku-5.5 案）：Anthropic
// 新模型家族废弃 thinking.type.enabled（要求 adaptive + output_config.effort），
// BYOK 未知模型走老协议必 400。fetch 层识别特征 → 请求体手术重发一次。
// 依赖全部注入（假 base fetch + 假日志），单测零真实网络。
import assert from "node:assert/strict";
import test from "node:test";

const { rewriteThinkingEnabledBody, isThinkingUnsupportedResponse, createAdaptiveThinkingFetch } =
  await import("../src/adaptive-thinking-fallback.ts");
const { classifyCoachFailureCode, isRetryableFailureCode } = await import("../src/turn.ts");

const ANTHROPIC_400_BODY =
  '400 {"type":"error","error":{"type":"invalid_request_error","message":"\\"thinking.type.enabled\\" is not supported for this model. Use \\"thinking.type.adaptive\\" and \\"output_config.effort\\" to control thinking behavior."},"request_id":"req_011CfxG9R9NKvpqvWs8oQwz9"}';

function okResponse(text: string): Response {
  return new Response(text, { status: 200 });
}

function errorResponse(status: number, text: string): Response {
  return new Response(text, { status });
}

// ── rewriteThinkingEnabledBody：请求体手术 ──────────────────────────────────

test("rewrite converts thinking.enabled to adaptive, keeps display, drops budget_tokens", () => {
  const body = JSON.stringify({
    model: "claude-haiku-5-5",
    max_tokens: 1024,
    thinking: { type: "enabled", budget_tokens: 1024, display: "summarized" },
    messages: [{ role: "user", content: "hi" }],
  });
  const out = rewriteThinkingEnabledBody(body);
  assert.ok(out);
  const parsed = JSON.parse(out) as { thinking: Record<string, unknown> };
  assert.equal(parsed.thinking.type, "adaptive");
  assert.equal(parsed.thinking.display, "summarized");
  assert.ok(!("budget_tokens" in parsed.thinking));
  // 手术只动 thinking，其余字段原样。
  const original = JSON.parse(body) as Record<string, unknown>;
  assert.equal(parsed.model, original.model);
  assert.equal(parsed.max_tokens, original.max_tokens);
});

test("rewrite returns null for non-thinking, adaptive, and non-JSON bodies (idempotent, no loop)", () => {
  assert.equal(rewriteThinkingEnabledBody("not json"), null);
  assert.equal(rewriteThinkingEnabledBody(JSON.stringify({ model: "x", messages: [] })), null);
  assert.equal(
    rewriteThinkingEnabledBody(JSON.stringify({ thinking: { type: "adaptive" }, messages: [] })),
    null,
  );
  assert.equal(
    rewriteThinkingEnabledBody(JSON.stringify({ thinking: { type: "disabled" }, messages: [] })),
    null,
  );
});

// ── isThinkingUnsupportedResponse：响应特征判定 ─────────────────────────────

test("matches anthropic thinking-unsupported 400 body", () => {
  assert.equal(isThinkingUnsupportedResponse(400, ANTHROPIC_400_BODY), true);
});

test("does not match other 400s, other statuses, or empty bodies", () => {
  assert.equal(isThinkingUnsupportedResponse(400, '400 {"error":{"message":"prompt is too long: 200000 tokens > 200000"}}'), false);
  assert.equal(isThinkingUnsupportedResponse(404, ANTHROPIC_400_BODY), false);
  assert.equal(isThinkingUnsupportedResponse(500, ANTHROPIC_400_BODY), false);
  assert.equal(isThinkingUnsupportedResponse(400, ""), false);
});

// ── createAdaptiveThinkingFetch：一次自愈重发 ────────────────────────────────

test("retries once with rewritten body on thinking-unsupported 400 and returns the retry response", async () => {
  const calls: Array<{ body?: unknown }> = [];
  const base = (async (_input: string | URL, init?: RequestInit) => {
    calls.push({ body: init?.body });
    if (calls.length === 1) return errorResponse(400, ANTHROPIC_400_BODY);
    return okResponse("streamed");
  }) as typeof globalThis.fetch;
  const logs: string[] = [];
  const fetch = createAdaptiveThinkingFetch(base, { writeLog: (line) => logs.push(line) });

  const init: RequestInit = {
    method: "POST",
    body: JSON.stringify({ model: "claude-haiku-5-5", thinking: { type: "enabled", budget_tokens: 1024 } }),
  };
  const res = await fetch("https://api.anthropic.com/v1/messages", init);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 2);
  const retryBody = JSON.parse(String(calls[1]!.body)) as { thinking: Record<string, unknown> };
  assert.equal(retryBody.thinking.type, "adaptive");
  assert.ok(!("budget_tokens" in retryBody.thinking));
  // 诊断行随 coach-error.log 落盘（随报障包带出）。
  assert.equal(logs.length, 1);
  assert.ok(logs[0]!.includes("[adaptive-fallback]"));
});

test("second thinking-unsupported 400 is returned as-is (no loop)", async () => {
  const calls: Array<unknown> = [];
  const base = (async (_input: string | URL, init?: RequestInit) => {
    calls.push(init?.body);
    return errorResponse(400, ANTHROPIC_400_BODY);
  }) as typeof globalThis.fetch;
  const fetch = createAdaptiveThinkingFetch(base, { writeLog: () => {} });

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    body: JSON.stringify({ model: "x", thinking: { type: "enabled", budget_tokens: 1024 } }),
  });
  assert.equal(res.status, 400);
  assert.equal(calls.length, 2);
});

test("non-matching 400 and bodyless requests pass through untouched", async () => {
  let calls = 0;
  const base = (async () => {
    calls += 1;
    return errorResponse(400, '400 {"error":{"message":"prompt is too long"}}');
  }) as typeof globalThis.fetch;
  const fetch = createAdaptiveThinkingFetch(base, { writeLog: () => {} });

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    body: JSON.stringify({ model: "x", thinking: { type: "enabled" } }),
  });
  assert.equal(res.status, 400);
  assert.equal(calls, 1);

  const res2 = await fetch("https://api.anthropic.com/v1/models", { method: "GET" });
  assert.equal(res2.status, 400);
  assert.equal(calls, 2);
});

// ── C：分类稳定码与可重试语义 ────────────────────────────────────────────────

test("classifyCoachFailureCode maps thinking-protocol 400 to model_protocol_unsupported (order guard)", () => {
  // 报障包 20261012-192831-5e704b84 野外原文整串（pi 把 HTTP body 原样拼进 Error）。
  assert.equal(classifyCoachFailureCode(new Error(ANTHROPIC_400_BODY)), "model_protocol_unsupported");
  // 不被鉴权/额度/过载宽 pattern 抢走：invalid_request_error 含 "invalid" 但
  // auth 族要求 invalid api key；400 不在 429/50x 族。
  assert.equal(classifyCoachFailureCode(new Error('400 invalid_request_error: max_tokens too large')), "turn_failed");
});

test("model_protocol_unsupported is not retryable (deterministic failure)", () => {
  assert.equal(isRetryableFailureCode("model_protocol_unsupported"), false);
});
