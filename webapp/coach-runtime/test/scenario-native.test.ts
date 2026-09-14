import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// getDataRoot / getPythonBackendConfig cache on first use; isolate the data root
// before importing the module under test.
const dataRoot = mkdtempSync(join(tmpdir(), "ac-scenario-native-"));
process.env.DATA_ROOT = dataRoot;

import { executeNativeScenario, isNativeScenarioCommand } from "../src/scenario-native.ts";

const ORIGINAL_FETCH = globalThis.fetch;

test.afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  delete process.env.AIMING_COOKIE_DESKTOP_RUNTIME_CONFIG;
});

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("isNativeScenarioCommand 只认 scenario.open / scenario.list / scenario.search", () => {
  assert.equal(isNativeScenarioCommand("scenario.open"), true);
  assert.equal(isNativeScenarioCommand("scenario.list"), true);
  assert.equal(isNativeScenarioCommand("scenario.search"), true);
  assert.equal(isNativeScenarioCommand("scenario_memory.set"), false);
  assert.equal(isNativeScenarioCommand("analysis.get"), false);
});

test("scenario.open 只发 coach_ui_event，不在后端打开场景", async () => {
  let fetched = false;
  globalThis.fetch = (async () => {
    fetched = true;
    return { ok: true, status: 200, json: async () => ({}) };
  }) as typeof fetch;

  const result = await executeNativeScenario("scenario.open", { scenario_name: "  1wall 6targets small  " });

  assert.equal(result.status, "succeeded");
  assert.equal(fetched, false);
  assert.equal(result.result_ref, "scenario:1wall 6targets small");
  assert.deepEqual(result.result, {
    schema_version: "coach_ui_event.v1",
    kind: "scenario",
    scenario_name: "1wall 6targets small",
  });
});

test("scenario.open 拒绝未知字段与空名字", async () => {
  const unknown = await executeNativeScenario("scenario.open", { scenario_name: "x", mode: "challenge" });
  assert.equal(unknown.status, "failed");
  assert.match(unknown.warning_or_error!.message, /scenario_name/);

  const empty = await executeNativeScenario("scenario.open", {});
  assert.equal(empty.status, "failed");
  assert.equal(empty.warning_or_error!.code, "invalid_parameters");
});

test("scenario.list 从 Python 后端读取本机场景清单", async () => {
  const runtimeConfig = join(dataRoot, "desktop-runtime.json");
  const { writeFileSync } = await import("node:fs");
  writeFileSync(runtimeConfig, JSON.stringify({ python_base_url: "http://127.0.0.1:9999", python_token: "tok" }), "utf-8");
  process.env.AIMING_COOKIE_DESKTOP_RUNTIME_CONFIG = runtimeConfig;

  let requested = "";
  globalThis.fetch = (async (url: any, init: any) => {
    requested = String(url);
    assert.equal(init.headers["X-Aiming-Cookie-Desktop-Token"], "tok");
    return {
      ok: true,
      status: 200,
      json: async () => ({
        schema_version: "kovaak_scenarios.v1",
        availability: "available",
        scenarios: ["1wall 6targets small", "pasu", 42, ""],
      }),
    };
  }) as typeof fetch;

  const result = await executeNativeScenario("scenario.list", {});
  assert.equal(requested, "http://127.0.0.1:9999/api/kovaak-scenarios");
  assert.equal(result.status, "succeeded");
  assert.deepEqual((result.result as any).scenarios, ["1wall 6targets small", "pasu"]);
});

test("scenario.list 在 Python 未就绪时返回 unavailable", async () => {
  process.env.AIMING_COOKIE_DESKTOP_RUNTIME_CONFIG = join(dataRoot, "missing-runtime.json");
  const result = await executeNativeScenario("scenario.list", {});
  assert.equal(result.status, "unavailable");
  assert.equal(result.warning_or_error!.code, "kovaak_list_unavailable");
});

function stubPopular(data: unknown[]): { calls: string[] } {
  const calls: string[] = [];
  globalThis.fetch = (async (input: any) => {
    calls.push(String(input));
    return {
      ok: true,
      status: 200,
      json: async () => ({ page: 0, max: 10, total: data.length, data }),
    };
  }) as typeof fetch;
  return { calls };
}

test("scenario.search 模糊搜官方场景库，默认 limit=10", async () => {
  const { calls } = stubPopular([
    {
      rank: 1,
      leaderboardId: 2,
      scenarioName: "1wall 6targets small",
      scenario: { aimType: null },
      counts: { plays: 72_963_049, entries: 1_280_269 },
      topScore: { score: 1960 },
    },
  ]);

  const result = await executeNativeScenario("scenario.search", { query: "  1wall  " });

  assert.equal(result.status, "succeeded");
  assert.equal(result.result_ref, "kovaak_scenarios:official");
  const payload = result.result as any;
  assert.equal(payload.schema_version, "kovaak_scenario_search.v1");
  assert.equal(payload.query, "1wall");
  assert.equal(payload.limit, 10);
  assert.equal(payload.count, 1);
  assert.deepEqual(payload.scenarios, [
    {
      scenario_name: "1wall 6targets small",
      leaderboard_id: 2,
      aim_type: null,
      plays: 72_963_049,
      entries: 1_280_269,
      top_score: 1960,
    },
  ]);
  assert.ok(calls[0].includes("scenarioNameSearch=1wall"));
  assert.ok(calls[0].includes("max=10"));
});

test("scenario.search 无结果时返回空列表而非报错", async () => {
  stubPopular([]);
  const result = await executeNativeScenario("scenario.search", { query: "zzz-none", limit: 5 });
  assert.equal(result.status, "succeeded");
  assert.equal((result.result as any).count, 0);
  assert.deepEqual((result.result as any).scenarios, []);
});

test("scenario.search limit 被钳到上限 20", async () => {
  const { calls } = stubPopular([]);
  const result = await executeNativeScenario("scenario.search", { query: "track", limit: 999 });
  assert.equal(result.status, "succeeded");
  assert.equal((result.result as any).limit, 20);
  assert.ok(calls[0].includes("max=20"));
});

test("scenario.search 网络失败返回可读的 unavailable", async () => {
  globalThis.fetch = (async () => new Response("down", { status: 503 })) as typeof fetch;
  const result = await executeNativeScenario("scenario.search", { query: "1wall" });
  assert.equal(result.status, "unavailable");
  assert.equal(result.warning_or_error!.code, "scenario_search_unavailable");
  assert.match(result.warning_or_error!.message, /HTTP 503/);
});

test("scenario.search 拒绝未知字段、空 query 与非法 limit", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("network must not be reached for invalid parameters");
  }) as typeof fetch;
  try {
    const unknown = await executeNativeScenario("scenario.search", { query: "x", mode: "challenge" });
    assert.equal(unknown.status, "failed");
    assert.match(unknown.warning_or_error!.message, /query, limit/);

    const empty = await executeNativeScenario("scenario.search", { query: "   " });
    assert.equal(empty.status, "failed");
    assert.equal(empty.warning_or_error!.code, "invalid_parameters");

    for (const limit of [0, -3, 1.5, "10"]) {
      const bad = await executeNativeScenario("scenario.search", { query: "x", limit });
      assert.equal(bad.status, "failed", `limit=${String(limit)}`);
      assert.match(bad.warning_or_error!.message, /limit/);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
