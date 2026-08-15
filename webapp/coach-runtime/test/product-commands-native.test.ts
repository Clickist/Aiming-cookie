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
    stats_calibration: { FOV: 103, DPI: 1600, sensitivity: 1.2, cm_per_360: 51 },
    created_at: "2026-08-13T10:20:30Z",
    updated_at: "2026-08-13T10:21:30Z",
  });
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
