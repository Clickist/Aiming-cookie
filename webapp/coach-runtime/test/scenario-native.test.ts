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

test("isNativeScenarioCommand 只认 scenario.open / scenario.list", () => {
  assert.equal(isNativeScenarioCommand("scenario.open"), true);
  assert.equal(isNativeScenarioCommand("scenario.list"), true);
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
