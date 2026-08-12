import assert from "node:assert/strict";
import test from "node:test";

import type { CoachToolBridge } from "../src/contracts.ts";
import {
  PRODUCT_COMMAND_NAMES,
  createProductCommandTool,
} from "../src/product-command-tools.ts";

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

test("Provider management commands remain outside the Coach product-command allowlist", () => {
  assert.deepEqual(
    PRODUCT_COMMAND_NAMES.filter((name) => name.startsWith("provider.")),
    [],
  );
});

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

test("product command allowlist includes every bounded evidence query", () => {
  const evidenceQueries = [
    "analysis.metrics.distribution",
    "analysis.evidence.list",
    "analysis.evidence.signal_window",
    "analysis.evidence.compare",
    "analysis.run_facts.get",
    "analysis.outcomes.timeline",
    "analysis.events.list",
    "analysis.events.get",
    "analysis.events.rank",
    "analysis.events.filter",
    "analysis.events.aggregate",
    "analysis.events.co_occurrence",
    "analysis.events.sequence",
  ];

  for (const commandName of evidenceQueries) {
    assert.ok(
      PRODUCT_COMMAND_NAMES.includes(commandName as typeof PRODUCT_COMMAND_NAMES[number]),
      `missing bounded evidence command: ${commandName}`,
    );
  }
  assert.ok(PRODUCT_COMMAND_NAMES.includes("profile.aiming.snapshot"));
  assert.ok(PRODUCT_COMMAND_NAMES.includes("analysis.delete"));
  assert.ok(PRODUCT_COMMAND_NAMES.includes("kovaak_scores.lookup"));
  assert.ok(PRODUCT_COMMAND_NAMES.includes("kovaak_scores.refresh_connected"));
});

test("turn-scoped exclusions remove discovery commands from the model tool", async () => {
  const tool = createProductCommandTool(bridge(), {
    excludedCommands: ["run.list", "analysis.create_from_run"],
  });
  const schema = JSON.stringify(tool.parameters);

  assert.ok(!schema.includes('"run.list"'));
  assert.ok(!schema.includes('"analysis.create_from_run"'));
  assert.ok(schema.includes('"analysis.run_facts.get"'));
  await assert.rejects(
    tool.execute("excluded", { command_name: "run.list", parameters: {} }),
    /not available for this turn/,
  );
});

test("analysis.get accepts the public command-route result shape", async () => {
  const originalFetch = globalThis.fetch;
  const routeResult = {
    schema_version: "coach_product_command_result.v1",
    command_id: "command:analysis:6",
    status: "succeeded",
    result_ref: "analysis:6",
    audit_ref: "audit:analysis:6",
    ui_event: null,
    confirmation: null,
    warning_or_error: null,
    result: {
      analysis_ref: "analysis:6",
      id: 6,
      status: "done",
      analysis_type: "flicking",
      input_mode: "multimodal",
      run_ref: "run:52326",
      created_at: "2026-08-08T16:10:09+08:00",
      started_at: "2026-08-08T16:10:10+08:00",
      finished_at: "2026-08-08T16:10:20+08:00",
      error: null,
    },
  };
  globalThis.fetch = (async () => new Response(JSON.stringify(routeResult), { status: 200 })) as typeof fetch;
  try {
    const result = await createProductCommandTool(bridge()).execute("analysis-get", {
      command_name: "analysis.get",
      parameters: { analysis_ref: "analysis:6" },
    });

    const { confirmation: _confirmation, ...providerResult } = routeResult;
    assert.equal(result.content[0]?.text, JSON.stringify(providerResult));
    assert.ok(!Object.hasOwn(JSON.parse(result.content[0]?.text ?? "{}"), "confirmation"));
    assert.deepEqual(result.details.event, {
      type: "product_command",
      command_id: "command:analysis:6",
      command_name: "analysis.get",
      status: "succeeded",
      result_ref: "analysis:6",
      audit_ref: "audit:analysis:6",
      ui_event: null,
      warning_or_error: null,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("pending confirmation metadata stays local while the Provider receives only the safe command result", async () => {
  const originalFetch = globalThis.fetch;
  const routeResult = {
    schema_version: "coach_product_command_result.v1",
    command_id: "command:plan-draft",
    status: "needs_confirmation",
    result_ref: null,
    result: null,
    ui_event: null,
    confirmation: {
      schema_version: "coach_product_command_confirmation.v1",
      confirmation_ref: "confirmation:local-only",
      command_name: "training_plan.generate_draft",
    },
    warning_or_error: { code: "confirmation_required", message: "User confirmation required" },
    audit_ref: "audit:plan-draft",
  };
  globalThis.fetch = (async () => new Response(JSON.stringify(routeResult), { status: 200 })) as typeof fetch;
  try {
    const result = await createProductCommandTool(bridge()).execute("plan-draft", {
      command_name: "training_plan.generate_draft",
      parameters: { plan_payload: { title: "Test" } },
    });

    assert.equal(result.details.event.status, "needs_confirmation");
    assert.ok(!result.content[0]?.text.includes("confirmation_ref"));
    assert.ok(!result.content[0]?.text.includes("confirmation:local-only"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("KovaaK score lookup only forwards a bridge-issued profile reference", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<Record<string, unknown>> = [];
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    await tool.execute("lookup", {
      command_name: "kovaak_scores.lookup",
      parameters: { profile_ref: "steam_profile:1" },
    });
    assert.deepEqual(requests, [{
      command_name: "kovaak_scores.lookup",
      parameters: { profile_ref: "steam_profile:1" },
    }]);

    for (const parameters of [
      { profile_ref: "https://steamcommunity.com/profiles/76561199033719938" },
      { profile_ref: "76561199033719938" },
      { profile_ref: "steam_profile:0" },
      { profile_ref: "steam_profile:1", extra: "field" },
      { profile_ref: { ref: "steam_profile:1" } },
      { profile_ref: ["steam_profile:1"] },
    ]) {
      await assert.rejects(
        tool.execute("lookup-rejected", { command_name: "kovaak_scores.lookup", parameters }),
        /unsupported fields/,
      );
    }
    assert.equal(requests.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("KovaaK connected-account refresh accepts exactly an empty parameter object", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<Record<string, unknown>> = [];
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    await tool.execute("refresh", { command_name: "kovaak_scores.refresh_connected", parameters: {} });
    assert.deepEqual(requests, [{ command_name: "kovaak_scores.refresh_connected", parameters: {} }]);

    for (const parameters of [
      { profile_ref: "steam_profile:1" },
      { url: "https://steamcommunity.com/profiles/76561199033719938" },
      { steam_id: "76561199033719938" },
      { nested: {} },
    ]) {
      await assert.rejects(
        tool.execute("refresh-rejected", { command_name: "kovaak_scores.refresh_connected", parameters }),
        /unsupported fields/,
      );
    }
    assert.equal(requests.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("KovaaK score command events retain no profile reference or score payload", async () => {
  const originalFetch = globalThis.fetch;
  const scorePayload = { total_records: 78, completed: 18 };
  globalThis.fetch = (async () => new Response(JSON.stringify({
    schema_version: "coach_product_command_result.v1",
    command_id: "command:kovaak:1",
    status: "succeeded",
    result_ref: "kovaak_scores:temporary:1",
    audit_ref: "audit:kovaak:1",
    result: scorePayload,
    ui_event: { result: scorePayload },
    warning_or_error: { scorePayload },
  }), { status: 200 })) as typeof fetch;
  try {
    const result = await createProductCommandTool(bridge()).execute("lookup", {
      command_name: "kovaak_scores.lookup",
      parameters: { profile_ref: "steam_profile:1" },
    });
    assert.deepEqual(result.details.event, {
      type: "product_command",
      command_id: "command:kovaak:1",
      command_name: "kovaak_scores.lookup",
      status: "succeeded",
      result_ref: "kovaak_scores:temporary:1",
      audit_ref: "audit:kovaak:1",
      ui_event: null,
      warning_or_error: null,
    });
    assert.ok(!JSON.stringify(result.details.event).includes("steam_profile:1"));
    assert.ok(!JSON.stringify(result.details.event).includes("total_records"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("guided teaching facts are registered as write commands", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<Record<string, unknown>> = [];
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    const cases = [
      ["training_plan.item.add", { plan_ref: "plan:1", item_payload: { title: "Practice" } }],
      ["training_plan.execution.record", { item_ref: "item:1", scenario_ref: "scenario:1", run_refs: [] }],
      ["training_plan.retest.record", { item_ref: "item:1", kind: "matched", run_refs: [] }],
    ] as const;

    for (const [command_name, parameters] of cases) {
      assert.ok(PRODUCT_COMMAND_NAMES.includes(command_name));
      await tool.execute("first", { command_name, parameters });
      await tool.execute("second", { command_name, parameters });
    }

    assert.equal(requests.length, 6);
    for (let index = 0; index < requests.length; index += 2) {
      assert.equal(requests[index].command_name, requests[index + 1].command_name);
      assert.equal(requests[index].idempotency_key, requests[index + 1].idempotency_key);
      assert.match(String(requests[index].idempotency_key), /^turn:[a-f0-9]{64}$/);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("product tool documents the reachable Evidence query chain", () => {
  const description = createProductCommandTool(bridge()).description;
  assert.match(description, /analysis\.evidence\.list/);
  assert.match(description, /segment_ref/);
  assert.match(description, /available_channels/);
  assert.match(description, /analysis\.events\.list/);
  assert.match(description, /table_ref/);
  assert.match(description, /field_catalog/);
});

test("product command passes parameters through to bridge without TS-side filtering", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = (async () => {
    fetchCalls += 1;
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    // FORBIDDEN_KEYS filtering was removed — Python bridge validates server-side.
    const result = await tool.execute("call", {
      command_name: "analysis.create_from_run",
      parameters: { run_ref: "run:7", note: "https://example.com" },
    });
    assert.equal(fetchCalls, 1);
    assert.ok(result);
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

test("product commands forward a bounded exact instruction quote", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Record<string, unknown>[] = [];
  globalThis.fetch = (async (_url, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({
      ...commandResult(), authorization_source: "explicit_user_request",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    const result = await createProductCommandTool(bridge()).execute("direct-delete", {
      command_name: "analysis.delete",
      parameters: { analysis_ref: "analysis:3" },
      instruction_quote: "delete this analysis",
    });
    assert.equal(requests[0]?.instruction_quote, "delete this analysis");
    assert.equal(result.details.event.authorization_source, "explicit_user_request");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("guided teaching facts pass through to bridge without TS-side security filtering", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = (async () => {
    fetchCalls += 1;
    return new Response(JSON.stringify(commandResult()), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    // Security filtering (FORBIDDEN_KEYS) was removed — the Python bridge
    // validates parameters server-side via _TOOL_BRIDGE_PAYLOAD_KEYS.
    const result = await tool.execute("call", {
      command_name: "training_plan.item.add",
      parameters: { authority: "coach" },
    });
    assert.equal(fetchCalls, 1);
    assert.ok(result);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("provider receives the bounded result while the trace event retains only its audit projection", async () => {
  const originalFetch = globalThis.fetch;
  const fullResult = {
    schema_version: "coach_product_command_result.v1",
    command_id: "command:events:1",
    status: "succeeded",
    result_ref: "analysis:7:events:page:1",
    audit_ref: "audit:events:1",
    result: {
      analysis_ref: "analysis:7",
      event_refs: ["event:analysis:7:shot:1"],
      records: [{ event_ref: "event:analysis:7:shot:1", event_kind: "shot", start_ms: 120 }],
      next_cursor: "cursor:opaque-events-page-2",
    },
  };
  globalThis.fetch = (async () => new Response(JSON.stringify(fullResult), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })) as typeof fetch;
  try {
    const result = await createProductCommandTool(bridge()).execute("events-page-1", {
      command_name: "analysis.events.list" as typeof PRODUCT_COMMAND_NAMES[number],
      parameters: { analysis_ref: "analysis:7", scope: "whole_run", event_kinds: ["shot"], limit: 20 },
    });

    assert.equal(result.content[0]?.text, JSON.stringify(fullResult));
    assert.deepEqual(result.details.event, {
      type: "product_command",
      command_id: "command:events:1",
      command_name: "analysis.events.list",
      status: "succeeded",
      result_ref: "analysis:7:events:page:1",
      audit_ref: "audit:events:1",
      ui_event: null,
      warning_or_error: null,
    });
    assert.ok(JSON.stringify(result.details.event).includes("analysis:7:events:page:1"));
    assert.ok(!JSON.stringify(result.details.event).includes("next_cursor"));
    assert.ok(!JSON.stringify(result.details.event).includes("opaque-events-page-2"));
    assert.ok(!("result" in result.details.event));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("guided teaching facts retain only safe audit projections in the trace", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    ...commandResult(),
    result: { item_ref: "item:1", private_note: "do not trace this" },
  }), { status: 200 })) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    const cases = [
      ["training_plan.item.add", { plan_ref: "plan:1", item_payload: { title: "Practice" } }],
      ["training_plan.execution.record", { item_ref: "item:1", scenario_ref: "scenario:1", run_refs: [] }],
      ["training_plan.retest.record", { item_ref: "item:1", kind: "matched", run_refs: [] }],
    ] as const;

    for (const [command_name, parameters] of cases) {
      const result = await tool.execute("call", { command_name, parameters });
      assert.deepEqual(result.details.event, {
        type: "product_command",
        command_id: "command:1",
        command_name,
        status: "succeeded",
        result_ref: "analysis:7",
        audit_ref: "audit:1",
        ui_event: { schema_version: "coach_ui_event.v1", kind: "analysis", analysis_ref: "analysis:7" },
        warning_or_error: null,
      });
      assert.ok(!JSON.stringify(result.details.event).includes("private_note"));
    }
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

test("bridge failures and invalid statuses fail closed without echoing secrets", async () => {
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

    // Invalid status is still rejected by safeCommandEvent validation.
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
