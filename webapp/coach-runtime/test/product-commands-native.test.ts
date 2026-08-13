import assert from "node:assert/strict";
import test from "node:test";

import Database from "better-sqlite3";

import { executeNativeRead } from "../src/product-commands-native.ts";

function createRunDb(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE kovaak_runs (
      id INTEGER PRIMARY KEY,
      user_id TEXT NOT NULL,
      source_key TEXT,
      scenario TEXT,
      trace_state TEXT,
      alignment_state TEXT,
      alignment_summary TEXT,
      finalization_state TEXT,
      video_path TEXT,
      video_state TEXT,
      stats_summary TEXT,
      created_at TEXT,
      updated_at TEXT
    );
    CREATE TABLE sessions (
      id INTEGER PRIMARY KEY,
      kovaak_run_id INTEGER,
      user_id TEXT NOT NULL
    );
  `);
  return db;
}

test("run.get returns one owner-scoped Run summary", () => {
  const db = createRunDb();
  try {
    db.prepare(
      "INSERT INTO kovaak_runs(id, user_id, source_key, scenario, trace_state, finalization_state, " +
      "stats_summary, created_at, updated_at) VALUES(7, 'owner-a', 'source:7', '1wall 6targets small', " +
      "'complete', 'ready', ?, '2026-08-13 10:20:30', '2026-08-13 10:21:30')",
    ).run(JSON.stringify({ config: { FOV: 103, DPI: 1600, "Horiz Sens": 1.2 }, cm_per_360: 51 }));
    db.prepare("INSERT INTO sessions(id, kovaak_run_id, user_id) VALUES(3, 7, 'owner-a')").run();

    const result = executeNativeRead(db, "run.get", { run_ref: "run:7" }, "owner-a");
    assert.equal(result.status, "succeeded");
    assert.equal(result.result_ref, "run:7");
    assert.deepEqual(result.result, {
      id: 7,
      run_ref: "run:7",
      source_key: "source:7",
      scenario: "1wall 6targets small",
      trace_state: "complete",
      finalization_state: "ready",
      analysis_count: 1,
      stats_calibration: { FOV: 103, DPI: 1600, sensitivity: 1.2, cm_per_360: 51 },
      created_at: "2026-08-13T10:20:30Z",
      updated_at: "2026-08-13T10:21:30Z",
    });

    const forbidden = executeNativeRead(db, "run.get", { run_ref: "run:7" }, "owner-b");
    assert.equal(forbidden.status, "failed");
    assert.equal(forbidden.warning_or_error?.code, "not_found");
  } finally {
    db.close();
  }
});

test("navigation.open reaches the native video-time handler", () => {
  const db = new Database(":memory:");
  try {
    const result = executeNativeRead(db, "navigation.open", {
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
  } finally {
    db.close();
  }
});
