import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { CoachToolBridge } from "../src/contracts.ts";
import {
  PRODUCT_COMMAND_NAMES,
  createProductCommandTool,
} from "../src/product-command-tools.ts";
import { isNativeWriteCommand } from "../src/product-commands-write.ts";
import { isNativePythonAnalysisCommand } from "../src/python-analysis.ts";

// Native commands read from the app-data file system. Point DATA_ROOT at a
// throwaway directory before any command executes (getDataRoot caches on the
// first call), and let each test write the fixture files it needs.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-product-command-"));
process.env.DATA_ROOT = dataRoot;

function writeFixture(relativePath: string, content: unknown): void {
  const absolutePath = join(dataRoot, ...relativePath.split("/"));
  mkdirSync(join(absolutePath, ".."), { recursive: true });
  writeFileSync(absolutePath, JSON.stringify(content), "utf8");
}

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
  // teaching_session.update is rebuilt as a native write command backed by
  // teaching/session.json (see product-commands-write.ts).
  assert.ok(PRODUCT_COMMAND_NAMES.includes("teaching_session.update"));
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

test("analysis.get returns the native overview with a safe audit event", async () => {
  writeFixture("analyses/6/overview.json", {
    status: "done",
    analysis_type: "flicking",
    input_mode: "multimodal",
    run_ref: "run:52326",
    created_at: "2026-08-08T16:10:09+08:00",
    started_at: "2026-08-08T16:10:10+08:00",
    finished_at: "2026-08-08T16:10:20+08:00",
    error: null,
  });

  const result = await createProductCommandTool(null).execute("analysis-get", {
    command_name: "analysis.get",
    parameters: { analysis_ref: "analysis:6" },
  });

  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.schema_version, "coach_product_command_result.v1");
  assert.equal(parsed.status, "succeeded");
  assert.equal(parsed.audit_ref, "native");
  assert.match(String(parsed.command_id), /^native:analysis\.get:/);
  assert.equal(parsed.result_ref, "analysis:6");
  // Native reads never carry bridge confirmation metadata.
  assert.ok(!Object.hasOwn(parsed, "confirmation"));
  assert.equal((parsed.result as { analysis_ref: string }).analysis_ref, "analysis:6");
  assert.equal((parsed.result as { run_ref: string }).run_ref, "run:52326");

  assert.deepEqual(result.details.event, {
    type: "product_command",
    command_id: parsed.command_id,
    command_name: "analysis.get",
    status: "succeeded",
    result_ref: "analysis:6",
    audit_ref: "native",
    ui_event: null,
    warning_or_error: null,
  });
});

test("native generate_draft writes a draft and never exposes confirmation metadata", async () => {
  const result = await createProductCommandTool(null).execute("plan-draft", {
    command_name: "training_plan.generate_draft",
    parameters: { plan_payload: { title: "Test" } },
  });

  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.schema_version, "coach_product_command_result.v1");
  assert.equal(parsed.status, "succeeded");
  assert.match(String(parsed.command_id), /^command:[a-f0-9]{32}$/);
  assert.match(String(parsed.audit_ref), /^audit:[a-f0-9]{32}$/);
  assert.match(String(parsed.result_ref), /^plan:/);
  // Native writes carry no confirmation metadata to the Provider.
  assert.ok(!JSON.stringify(result).includes("confirmation_ref"));
  assert.ok(!JSON.stringify(result).includes("confirmation:local-only"));
  assert.equal(result.details.event.status, "succeeded");
});

test("native write failures surface the handler's real error message", async () => {
  // Deep-test Bug 2: generate_draft without plan_payload reported a generic
  // "product command could not be completed", so the model could not self-correct.
  const result = await createProductCommandTool(null).execute("draft-missing-payload", {
    command_name: "training_plan.generate_draft",
    parameters: { analysis_ref: "analysis:6", plan_type: "static_clicking", focus: "terminal control" },
  });

  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.status, "failed");
  const warning = parsed.warning_or_error as { code: string; message: string };
  assert.equal(warning.code, "internal_error");
  assert.match(warning.message, /plan_payload is required/);
});

test("KovaaK score lookup accepts literal Steam IDs and profile URLs", async () => {
  const tool = createProductCommandTool(null);
  const result = await tool.execute("lookup", {
    command_name: "kovaak_scores.lookup",
    parameters: { profile_ref: "steam_profile:1" },
  });

  // Opaque refs without a turn-scoped map still report unavailable and never
  // echo the profile ref or any score payload.
  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.audit_ref, "native");
  assert.equal(parsed.status, "unavailable");
  assert.equal((parsed.warning_or_error as { code: string }).code, "temporary_profile_unavailable");
  assert.ok(!JSON.stringify(result).includes("steam_profile:1"));

  // A literal 17-digit Steam ID or a steamcommunity profile URL pasted by the
  // user is wired straight through to the benchmark endpoint (here stubbed to
  // fail, proving the request left the building instead of being rejected).
  const originalFetch = globalThis.fetch;
  const requestedUrls: string[] = [];
  globalThis.fetch = (async (url) => {
    requestedUrls.push(String(url));
    return new Response("upstream down", { status: 503 });
  }) as typeof fetch;
  try {
    for (const profile_ref of [
      "76561199033719938",
      "https://steamcommunity.com/profiles/76561199033719938",
      "https://steamcommunity.com/profiles/76561199033719938/",
    ]) {
      const literal = await tool.execute("lookup-literal", {
        command_name: "kovaak_scores.lookup",
        parameters: { profile_ref },
      });
      const literalParsed = JSON.parse(literal.content[0]?.text ?? "{}") as Record<string, unknown>;
      assert.equal(literalParsed.status, "unavailable");
      assert.equal((literalParsed.warning_or_error as { code: string }).code, "kovaak_scores_unavailable");
    }
    assert.ok(requestedUrls.length > 0, "benchmark endpoint should have been called");
    assert.ok(requestedUrls.every((url) => url.includes("steamId=76561199033719938")));
  } finally {
    globalThis.fetch = originalFetch;
  }

  for (const parameters of [
    { profile_ref: "steam_profile:0" },
    { profile_ref: "steam_profile:1", extra: "field" },
    { profile_ref: { ref: "steam_profile:1" } },
    { profile_ref: ["steam_profile:1"] },
    { profile_ref: "not-a-steam-input" },
  ]) {
    await assert.rejects(
      tool.execute("lookup-rejected", { command_name: "kovaak_scores.lookup", parameters }),
      /unsupported fields/,
    );
  }
});

test("KovaaK connected-account refresh validates an empty parameter object natively", async () => {
  const tool = createProductCommandTool(null);
  const result = await tool.execute("refresh", { command_name: "kovaak_scores.refresh_connected", parameters: {} });

  // No connected Steam account in the fixture data root: native refresh
  // reports unavailable without making a network request.
  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.audit_ref, "native");
  assert.equal(parsed.status, "unavailable");
  assert.equal((parsed.warning_or_error as { code: string }).code, "connected_account_unavailable");

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
});

test("KovaaK score command events retain no profile reference or score payload", async () => {
  const result = await createProductCommandTool(null).execute("lookup", {
    command_name: "kovaak_scores.lookup",
    parameters: { profile_ref: "steam_profile:1" },
  });

  const event = result.details.event as Record<string, unknown>;
  assert.equal(event.type, "product_command");
  assert.equal(event.command_name, "kovaak_scores.lookup");
  assert.equal(event.status, "unavailable");
  assert.equal(event.audit_ref, "native");
  assert.ok(!("result" in event), "trace event must not carry the score payload");
  assert.ok(!JSON.stringify(result.details.event).includes("steam_profile:1"));
  assert.ok(!JSON.stringify(result.details.event).includes("total_records"));
  assert.ok(!JSON.stringify(result.details.event).includes("overall_rank"));
});

test("guided teaching facts are registered as native write commands", async () => {
  writeFixture("training/plan.json", {
    plan_id: "plan:1",
    status: "active",
    version: 1,
    items: [],
  });

  const tool = createProductCommandTool(null);
  const cases = [
    ["training_plan.item.add", { plan_ref: "plan:1", item_payload: { title: "Practice" } }],
    ["training_plan.execution.record", { item_ref: "item:1", scenario_ref: "scenario:1", run_refs: [], completion_status: "completed" }],
    ["training_plan.retest.record", { item_ref: "item:1", kind: "matched", expected_metric_ref: "metric:sparc", expected_direction: "down", result: "improved", analysis_refs: [] }],
  ] as const;

  for (const [command_name, parameters] of cases) {
    assert.ok(PRODUCT_COMMAND_NAMES.includes(command_name));
    assert.ok(isNativeWriteCommand(command_name), `${command_name} should be native`);
    const result = await tool.execute("first", { command_name, parameters });
    const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
    assert.equal(parsed.schema_version, "coach_product_command_result.v1");
    assert.equal(parsed.status, "succeeded");
    assert.match(String(parsed.command_id), /^command:[a-f0-9]{32}$/);
    assert.match(String(parsed.audit_ref), /^audit:[a-f0-9]{32}$/);
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

test("analysis.retry executes natively and never routes through the bridge", async () => {
  writeFixture("desktop-runtime.json", { python_base_url: "http://127.0.0.1:9999", python_token: "python-token" });
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = (async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ id: 7, status: "queued" }), { status: 200 });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    // Even with a bridge configured, analysis.retry goes straight to the
    // Python backend's retry route — the bridge endpoint is never called.
    const result = await tool.execute("call", {
      command_name: "analysis.retry",
      parameters: { analysis_ref: "analysis:7" },
    });
    assert.deepEqual(urls, ["http://127.0.0.1:9999/api/sessions/7/retry"]);
    const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
    assert.equal(parsed.status, "succeeded");
    assert.equal(result.details.event.type, "product_command");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("native retry idempotency is stable per owner+command+parameters and never echoes secrets", async () => {
  writeFixture("desktop-runtime.json", { python_base_url: "http://127.0.0.1:9999", python_token: "python-token" });
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; headers: Record<string, string> }> = [];
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(url), headers: init?.headers as Record<string, string> });
    return new Response(JSON.stringify({ id: 7, status: "queued" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const tool = createProductCommandTool(bridge());
    const first = await tool.execute("call-1", {
      command_name: "analysis.retry",
      parameters: { analysis_ref: "analysis:7" },
    });
    await tool.execute("call-2", {
      command_name: "analysis.retry",
      parameters: { analysis_ref: "analysis:7" },
    });

    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, "http://127.0.0.1:9999/api/sessions/7/retry");
    assert.equal(requests[0].headers["Idempotency-Key"], requests[1].headers["Idempotency-Key"]);
    assert.match(String(requests[0].headers["Idempotency-Key"]), /^native:[a-f0-9]{64}$/);
    assert.equal(requests[0].headers["X-User-Id"], "desktop-local");
    // The bridge credentials are not carried on the native request, and the
    // tool result never echoes either secret.
    assert.ok(!("Authorization" in requests[0].headers));
    assert.ok(!JSON.stringify(first).includes(BEARER));
    assert.ok(!JSON.stringify(first).includes(DESKTOP));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("analysis creation is native via the Python REST API (not a native write)", () => {
  assert.equal(isNativeWriteCommand("analysis.create_from_run"), false);
  assert.equal(isNativePythonAnalysisCommand("analysis.create_from_run"), true);
});

// The bridge-only contracts (bounded instruction_quote forwarding, bridge
// fail-closed status validation) were removed when analysis.retry — the last
// bridge-routed command — went native; no registered command reaches the
// bridge transport anymore.

test("guided teaching facts execute as native writes without TS-side filtering", async () => {
  writeFixture("training/plan.json", {
    plan_id: "plan:1",
    status: "saved",
    version: 1,
    items: [],
  });

  const tool = createProductCommandTool(null);
  // Extra params reach the native handler untouched (no TS-side security
  // filtering); the handler reads the fields it needs and ignores the rest.
  const result = await tool.execute("call", {
    command_name: "training_plan.item.add",
    parameters: { plan_ref: "plan:1", item_payload: { title: "Practice" }, authority: "coach" },
  });
  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.status, "succeeded");
  assert.match(String(parsed.command_id), /^command:[a-f0-9]{32}$/);
  assert.equal(result.details.event.type, "product_command");
});

test("provider receives the bounded result while the trace event retains only its audit projection", async () => {
  writeFixture("analyses/7/evidence.json", {
    schema_version: "analysis_evidence.v1",
    evidence_segments: [],
    event_bundles: [{
      bundle_ref: "bundle:1",
      events: [{
        event_id: "event:analysis:7:shot:1",
        event_kind: "shot",
        start_ms: 120,
        end_ms: 140,
        attributes: {},
      }],
    }],
    metric_records: [],
  });

  const result = await createProductCommandTool(null).execute("events-page-1", {
    command_name: "analysis.events.list" as typeof PRODUCT_COMMAND_NAMES[number],
    parameters: { analysis_ref: "analysis:7", scope: "whole_run", event_kinds: ["shot"], limit: 20 },
  });

  const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
  assert.equal(parsed.status, "succeeded");
  assert.equal(parsed.audit_ref, "native");
  assert.equal(parsed.result_ref, "analysis:7:events:0");
  // The Provider sees the full bounded result (records); the trace event
  // retains only the audit projection.
  assert.deepEqual(result.details.event, {
    type: "product_command",
    command_id: parsed.command_id,
    command_name: "analysis.events.list",
    status: "succeeded",
    result_ref: "analysis:7:events:0",
    audit_ref: "native",
    ui_event: null,
    warning_or_error: null,
  });
  assert.ok(!("result" in result.details.event));
  assert.ok(!JSON.stringify(result.details.event).includes("event:analysis:7:shot:1"));
  assert.ok(!JSON.stringify(result.details.event).includes("next_cursor"));
});

test("guided teaching facts retain only safe audit projections in the trace", async () => {
  writeFixture("training/plan.json", {
    plan_id: "plan:1",
    status: "saved",
    version: 1,
    items: [],
  });

  const tool = createProductCommandTool(null);
  const cases = [
    ["training_plan.item.add", { plan_ref: "plan:1", item_payload: { title: "Practice" } }],
    ["training_plan.execution.record", { item_ref: "item:1", scenario_ref: "scenario:1", run_refs: [], completion_status: "completed" }],
    ["training_plan.retest.record", { item_ref: "item:1", kind: "matched", expected_metric_ref: "metric:sparc", expected_direction: "down", result: "improved", analysis_refs: [] }],
  ] as const;

  for (const [command_name, parameters] of cases) {
    const result = await tool.execute("call", { command_name, parameters });
    const event = result.details.event as Record<string, unknown>;
    assert.equal(event.type, "product_command");
    assert.equal(event.command_name, command_name);
    assert.equal(event.status, "succeeded");
    assert.match(String(event.command_id), /^command:[a-f0-9]{32}$/);
    assert.match(String(event.audit_ref), /^audit:[a-f0-9]{32}$/);
    assert.ok(!("result" in event), "trace event must not carry the write payload");
    assert.ok(!JSON.stringify(event).includes("private_note"));
  }
});

test("model-supplied authorization and confirmation fields never reach a native command", async () => {
  writeFixture("training/plan.json", {
    plan_id: "plan:one",
    status: "saved",
    version: 1,
    items: [],
  });

  const result = await createProductCommandTool(null).execute("call", {
    command_name: "training_plan.activate",
    parameters: { plan_ref: "plan:one" },
    request_basis: "coach_inferred",
    confirmation_ref: "confirmation:one",
  } as never);

  const serialized = JSON.stringify(result);
  assert.ok(!serialized.includes("request_basis"));
  assert.ok(!serialized.includes("confirmation_ref"));
  assert.ok(!serialized.includes("confirmation:one"));
  assert.ok(!serialized.includes("owner_id"));
  assert.ok(!serialized.includes("risk"));
  assert.equal((JSON.parse(result.content[0]?.text ?? "{}") as { status: string }).status, "succeeded");
});

test("native retry fails closed on backend errors without echoing secrets", async () => {
  writeFixture("desktop-runtime.json", { python_base_url: "http://127.0.0.1:9999", python_token: "python-token" });
  const originalFetch = globalThis.fetch;
  try {
    const tool = createProductCommandTool(bridge());
    // A transport failure that leaks bridge secrets in its message must not
    // surface them: the native path swallows the error and reports a bounded
    // failure code instead.
    globalThis.fetch = (async () => {
      throw new Error(`${BEARER} ${DESKTOP}`);
    }) as typeof fetch;
    const result = await tool.execute("call", {
      command_name: "analysis.retry",
      parameters: { analysis_ref: "analysis:3" },
    });
    const parsed = JSON.parse(result.content[0]?.text ?? "{}") as Record<string, unknown>;
    assert.equal(parsed.status, "failed");
    assert.ok(!JSON.stringify(result).includes(BEARER));
    assert.ok(!JSON.stringify(result).includes(DESKTOP));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
