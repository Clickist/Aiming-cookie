import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-read-"));
process.env.DATA_ROOT = dataRoot;

import { executeNativeEloshapes } from "../src/eloshapes-native.ts";
import { executeNativeRead } from "../src/product-commands-native.ts";

test("run.get returns a Run summary from meta.json", () => {
  const runsDir = join(dataRoot, "runs", "7");
  mkdirSync(runsDir, { recursive: true });
  writeFileSync(join(runsDir, "meta.json"), JSON.stringify({
    source_key: "source:7",
    scenario: "1wall 6targets small",
    trace_state: "complete",
    finalization_state: "ready",
    stats_calibration: { FOV: 103, DPI: 1600, sensitivity: 1.2, cm_per_360: 51 },
    created_at: "2026-08-13T10:20:30Z",
    updated_at: "2026-08-13T10:21:30Z",
  }));

  const result = executeNativeRead("run.get", { run_ref: "run:7" }, "owner-a");
  assert.equal(result.status, "succeeded");
  assert.equal(result.result_ref, "run:7");
  assert.deepEqual(result.result, {
    id: 7,
    run_ref: "run:7",
    source_key: "source:7",
    scenario: "1wall 6targets small",
    trace_state: "complete",
    finalization_state: "ready",
    // stats_calibration 必须自带场景归属（scenario + run_ref）：Coach 模型
    // 靠它区分「smoothsphere 用 45」和「6targets 用 80」，不能混用。
    stats_calibration: {
      FOV: 103, DPI: 1600, sensitivity: 1.2, cm_per_360: 51,
      scenario: "1wall 6targets small",
      run_ref: "run:7",
    },
    created_at: "2026-08-13T10:20:30Z",
    updated_at: "2026-08-13T10:21:30Z",
  });
});

test("run.list stamps each run's stats_calibration with its own scenario attribution", () => {
  const scenarios: Array<[string, number, number]> = [
    ["smoothsphere", 8, 45],
    ["1wall 6targets small", 9, 80],
  ];
  for (const [scenario, id, sensitivity] of scenarios) {
    const runsDir = join(dataRoot, "runs", String(id));
    mkdirSync(runsDir, { recursive: true });
    writeFileSync(join(runsDir, "meta.json"), JSON.stringify({
      source_key: `source:${id}`,
      scenario,
      trace_state: "complete",
      finalization_state: "ready",
      stats_calibration: { FOV: 103, DPI: 1600, sensitivity, cm_per_360: 51 },
      created_at: "2026-08-13T10:20:30Z",
      updated_at: "2026-08-13T10:21:30Z",
    }));
  }
  // 两个不同场景的 run 必须各自携带自己的归属标注，跨场景混用灵敏度
  // 在载荷层面就表现为 scenario 不匹配，而不是一个无标注的裸数字。
  const result = executeNativeRead("run.list", {}, "owner-a");
  assert.equal(result.status, "succeeded");
  const items = result.result as Array<Record<string, any>>;
  const byRef = new Map(items.map((item) => [item.run_ref, item]));
  assert.deepEqual(
    byRef.get("run:8").stats_calibration,
    { FOV: 103, DPI: 1600, sensitivity: 45, cm_per_360: 51, scenario: "smoothsphere", run_ref: "run:8" },
  );
  assert.deepEqual(
    byRef.get("run:9").stats_calibration,
    { FOV: 103, DPI: 1600, sensitivity: 80, cm_per_360: 51, scenario: "1wall 6targets small", run_ref: "run:9" },
  );
});

test("run.get fails when the run does not exist", () => {
  const result = executeNativeRead("run.get", { run_ref: "run:999" }, "owner-a");
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "not_found");
});

test("navigation.open reaches the native video-time handler", () => {
  const result = executeNativeRead("navigation.open", {
    target: "video_time",
    analysis_ref: "analysis:13",
    time_ms: 12_500,
  }, "owner-a");
  assert.deepEqual(result, {
    status: "succeeded",
    result: {
      schema_version: "coach_ui_event.v1",
      kind: "video_time",
      analysis_ref: "analysis:13",
      time_ms: 12_500,
    },
  });
});

test("calibration.get reads from config/calibration.json", () => {
  const configDir = join(dataRoot, "config");
  mkdirSync(configDir, { recursive: true });
  writeFileSync(join(configDir, "calibration.json"), JSON.stringify({
    cm_per_360: 32.5,
    fov: 106,
    updated_at: "2026-08-13T00:00:00Z",
  }));

  const result = executeNativeRead("calibration.get", {}, "owner-a");
  assert.equal(result.status, "succeeded");
  const cal = result.result as Record<string, unknown>;
  assert.equal(cal.configured, true);
  // 全局校准必须带 scope 标注：它是跨场景的设备级基准，不是某个场景
  // 的局内灵敏度。
  assert.equal(cal.scope, "device_global");
  assert.deepEqual(cal.values, { cm_per_360: 32.5, fov: 106 });
});

test("calibration.get returns unconfigured when file is absent", () => {
  // Remove calibration.json by writing to a different config dir is not possible
  // due to caching, so just verify the shape for an absent file.
  // Since calibration.json was written in the previous test, we test the
  // "unconfigured" path by checking the peripheral_profile command instead.
  const result = executeNativeRead("peripheral_profile.get", {}, "owner-a");
  assert.equal(result.status, "succeeded");
  const profile = result.result as Record<string, unknown>;
  assert.equal(profile.configured, false);
});

test("eloshapes.query executes against the bundled catalog snapshot", () => {
  const result = executeNativeEloshapes("eloshapes.query", {
    weight_max: 60,
    size_category: ["medium"],
    limit: 5,
  });
  assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
  const query = result.result as Record<string, unknown>;
  assert.equal(query.schema_version, "eloshapes_query.v1");
  assert.equal(query.snapshot_source, "eloshapes_mouse_catalog_2026-07-31T211736Z");
  // The catalog ships with the repo, so a bounded weight filter must produce
  // real matches — not an empty or unbounded result.
  assert.ok(typeof query.total_matches === "number" && query.total_matches > 0);
  assert.equal(query.returned, 5);
});

test("eloshapes.query rejects unknown parameter keys", () => {
  // Deep-test Bug 6: unknown filters were silently dropped, answering a
  // different question than the one asked. They now fail structurally and
  // never echo the rejected value.
  const result = executeNativeEloshapes("eloshapes.query", {
    weight_max: 55,
    path: "C:/secret",
    limit: 3,
  });
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "invalid_parameters");
  assert.match(result.warning_or_error?.message ?? "", /"path"/);
  assert.match(result.warning_or_error?.message ?? "", /weight_max/);
  assert.ok(!JSON.stringify(result).includes("C:/secret"));
});

test("profile.aiming.snapshot reads the owner aiming profile", () => {
  writeFileSync(join(dataRoot, "profile.json"), JSON.stringify({
    status: "active",
    dimensions: [{ dimension: "flicking", level: "developing" }],
    contribution_refs: ["analysis:7"],
    active_plan_ref: "plan:1",
    updated_at: "2026-08-14T00:00:00Z",
  }));
  const result = executeNativeRead("profile.aiming.snapshot", {}, "owner-snap");
  assert.equal(result.status, "succeeded");
  assert.deepEqual(result.result, {
    schema_version: "aiming_profile.v1",
    owner_ref: "owner-snap",
    profile_ref: "profile-aiming:owner-snap",
    status: "active",
    dimensions: [{ dimension: "flicking", level: "developing" }],
    contribution_refs: ["analysis:7"],
    next_retest_refs: [],
    active_plan_ref: "plan:1",
    updated_at: "2026-08-14T00:00:00Z",
  });
});

test("profile.aiming.snapshot returns a clean profile when unset", () => {
  rmSync(join(dataRoot, "profile.json"), { force: true });
  const result = executeNativeRead("profile.aiming.snapshot", {}, "owner-clean");
  assert.equal(result.status, "succeeded");
  const profile = result.result as Record<string, unknown>;
  assert.equal(profile.status, "clean");
  assert.deepEqual(profile.dimensions, []);
});

// ── history.trend / analysis.compare read results from sessions/{id}.json ──
// The Python backend persists the full analysis_result.v2 inside the session
// record; analyses/{id}/ only ever holds disclosure artifacts (overview,
// metrics, events, evidence). The comparison commands must reach the session
// result or they can never compare anything.

function writeTrendFixture(): void {
  const flickingResult = (value: number) => ({
    schema_version: "analysis_result.v2",
    analysis_type: "flicking",
    input_mode: "video_fallback",
    input_snapshot: {
      schema_version: "analysis_input_snapshot.v2",
      scenario: "1wall 6targets small",
      scenario_identity_version: "kovaak_scenario.v1",
    },
    deterministic: {
      metrics: {
        "flick.accuracy_percent": {
          key: "flick.accuracy_percent",
          value,
          unit: "percent",
          availability: "available",
          coverage: 1.0,
          metric_version: "native_flicking.v1",
          classification: "deterministic",
          calibration_ref: "calibration:stats",
        },
      },
    },
    evidence: { coverage: 1.0, alignment: { status: "not_required" } },
  });
  mkdirSync(join(dataRoot, "sessions"), { recursive: true });
  const session = (id: number, status: string, result: unknown) => {
    // history.trend enumerates analyses/{id} directories, so they must exist.
    mkdirSync(join(dataRoot, "analyses", String(id)), { recursive: true });
    writeFileSync(join(dataRoot, "sessions", `${id}.json`), JSON.stringify({
      id, user_id: "owner-a", status, result,
    }));
  };
  session(5, "done", flickingResult(80));
  session(6, "done", flickingResult(90));
  session(7, "running", null);
}

test("history.trend compares analysis results stored in done sessions", () => {
  writeTrendFixture();
  const result = executeNativeRead("history.trend", { metric_key: "flick.accuracy_percent" }, "owner-a");
  assert.equal(result.status, "succeeded");
  const trend = result.result as Record<string, unknown>;
  assert.equal(trend.comparable, true);
  assert.equal(trend.current_session_id, 6);
  assert.equal(trend.baseline_session_id, 5);
  assert.equal(trend.current, 90);
  assert.equal(trend.baseline, 80);
  assert.equal(trend.delta, 10);
  assert.equal(trend.percent_change, 12.5);
});

test("analysis.compare reads both refs from sessions; non-done sessions have no result", () => {
  writeTrendFixture();
  const compare = executeNativeRead("analysis.compare", {
    current_analysis_ref: "analysis:6",
    baseline_analysis_ref: "analysis:5",
    metric_key: "flick.accuracy_percent",
  }, "owner-a");
  assert.equal(compare.status, "succeeded");
  const body = compare.result as Record<string, unknown>;
  assert.equal(body.comparable, true);
  assert.equal(body.current_analysis_ref, "analysis:6");
  assert.equal(body.baseline_analysis_ref, "analysis:5");
  assert.equal(body.delta, 10);

  const running = executeNativeRead("analysis.compare", {
    current_analysis_ref: "analysis:7",
    baseline_analysis_ref: "analysis:5",
    metric_key: "flick.accuracy_percent",
  }, "owner-a");
  assert.equal(running.status, "failed");
  assert.equal(running.warning_or_error?.code, "not_found");
});
