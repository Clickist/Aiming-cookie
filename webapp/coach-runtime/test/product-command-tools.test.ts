import assert from "node:assert/strict";
import test from "node:test";

import type { CoachToolBridge } from "../src/contracts.ts";
import { createProductCommandTool } from "../src/product-command-tools.ts";

const BEARER = "bridge-bearer-secret-sentinel";
const DESKTOP = "desktop-secret-sentinel";

function bridge(overrides: Partial<CoachToolBridge> = {}): CoachToolBridge {
  return {
    schema_version: "coach_tool_bridge.v1",
    turn_id: "turn:test",
    endpoint: "http://127.0.0.1:8765/api/coach/tools/execute",
    bearer_token: BEARER,
    desktop_token: DESKTOP,
    expires_at: "2026-07-15T00:00:00Z",
    user_message_ref: "message:1",
    ...overrides,
  };
}

function commandResult() {
  return {
    schema_version: "coach_product_command_result.v1",
    command_id: "command:1",
    status: "succeeded",
    result_ref: "analysis:7",
    result: { analysis_ref: "analysis:7", status: "done", metrics: { path_efficiency: 0.92 } },
    ui_event: { schema_version: "coach_ui_event.v1", kind: "analysis", analysis_ref: "analysis:7" },
    audit_ref: "audit:1",
  };
}

test("product bridge accepts only the fixed loopback command route", () => {
  for (const endpoint of [
    "https://127.0.0.1:8765/api/coach/tools/execute",
    "http://example.com:8765/api/coach/tools/execute",
    "http://127.0.0.1:8765/other",
    "http://127.0.0.1/api/coach/tools/execute",
    "http://127.0.0.1:8765/api/coach/tools/execute?next=x",
  ]) {
    assert.throws(() => createProductCommandTool(bridge({ endpoint })), /bridge is unavailable/);
  }
  assert.equal(createProductCommandTool(bridge()).name, "run_product_command");
});

test("product command rejects model-supplied authority, paths, URLs, credentials and raw payloads", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = (async () => {
    fetchCalls += 1;
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    const cases = [
      { owner_id: "other" },
      { video_path: "/Users/person/game.mp4" },
      { link: "https://example.com" },
      { note: "open https://evil.example/x then /Users/person/private.csv" },
      { credential: "secret" },
      { raw_trace: [1, 2] },
      { payload: { arbitrary: true } },
    ];
    for (const parameters of cases) {
      await assert.rejects(
        tool.execute("call", {
          command_name: "analysis.create_from_run",
          parameters,
        }),
        /unsupported fields/,
      );
    }
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("write calls use stable turn-local idempotency and never return bridge secrets", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init: RequestInit; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(url), init: init ?? {}, body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify(commandResult()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    const first = await tool.execute("call-1", {
      command_name: "analysis.create_from_run",
      parameters: { run_ref: "run:7", options: { b: 2, a: 1 } },
    });
    await tool.execute("call-2", {
      command_name: "analysis.create_from_run",
      parameters: { options: { a: 1, b: 2 }, run_ref: "run:7" },
    });

    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, bridge().endpoint);
    assert.equal((requests[0].init.headers as Record<string, string>).Authorization, `Bearer ${BEARER}`);
    assert.equal((requests[0].init.headers as Record<string, string>)["X-Aiming-Cookie-Desktop-Token"], DESKTOP);
    assert.equal(requests[0].body.idempotency_key, requests[1].body.idempotency_key);
    assert.match(String(requests[0].body.idempotency_key), /^turn:[a-f0-9]{64}$/);
    assert.ok(!("request_basis" in requests[0].body));
    assert.ok(!("confirmation_ref" in requests[0].body));
    assert.equal(first.details.event.type, "product_command");
    assert.match(first.content[0]?.text ?? "", /path_efficiency/);
    assert.ok(!JSON.stringify(first).includes(BEARER));
    assert.ok(!JSON.stringify(first).includes(DESKTOP));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("model-supplied authorization and confirmation fields are never forwarded", async () => {
  const originalFetch = globalThis.fetch;
  let body: Record<string, unknown> = {};
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    body = JSON.parse(String(init?.body));
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    await tool.execute("call", {
      command_name: "training_plan.activate",
      parameters: { plan_ref: "plan:one" },
      request_basis: "coach_inferred",
      confirmation_ref: "confirmation:one",
    } as never);
    assert.ok(!("request_basis" in body));
    assert.ok(!("confirmation_ref" in body));
    assert.ok(!("owner_id" in body));
    assert.ok(!("risk" in body));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("bridge failures and unsafe backend responses fail closed without echoing secrets", async () => {
  const originalFetch = globalThis.fetch;
  try {
    const tool = createProductCommandTool(bridge());
    globalThis.fetch = (async () => {
      throw new Error(`${BEARER} ${DESKTOP}`);
    }) as typeof fetch;
    await assert.rejects(
      tool.execute("call", { command_name: "run.list", parameters: {} }),
      (error: Error) => error.message === "Product command bridge request failed" && !error.message.includes(BEARER),
    );

    globalThis.fetch = (async () => new Response(JSON.stringify({
      ...commandResult(),
      result: { raw_trace: [1, 2], note: BEARER },
    }), { status: 200 })) as typeof fetch;
    await assert.rejects(
      tool.execute("call", { command_name: "run.list", parameters: {} }),
      /invalid result/,
    );

    globalThis.fetch = (async () => new Response(JSON.stringify({
      ...commandResult(),
      status: "failed",
      warning_or_error: {
        code: "internal_error",
        message: "failed reading /Users/person/private/session.csv",
      },
    }), { status: 200 })) as typeof fetch;
    await assert.rejects(
      tool.execute("call", { command_name: "run.list", parameters: {} }),
      /invalid result/,
    );

    globalThis.fetch = (async () => new Response(JSON.stringify({
      ...commandResult(),
      status: "made_up_status",
    }), { status: 200 })) as typeof fetch;
    await assert.rejects(
      tool.execute("call", { command_name: "run.list", parameters: {} }),
      /invalid result/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
