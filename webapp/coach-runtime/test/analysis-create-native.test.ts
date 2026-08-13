import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import Database from "better-sqlite3";

import { executeNativeWrite } from "../src/product-commands-write.ts";

type SeedOptions = {
  performance?: boolean;
  trace?: boolean;
  video?: boolean;
  window?: boolean;
};

function createDb(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL,
      status TEXT NOT NULL,
      video_path TEXT,
      csv_path TEXT,
      cm_per_360 REAL,
      fov REAL,
      analysis_type TEXT NOT NULL,
      input_mode TEXT NOT NULL,
      kovaak_run_id INTEGER,
      input_snapshot_json TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL DEFAULT 3,
      task_group_ref TEXT,
      attempt_number INTEGER NOT NULL DEFAULT 1,
      task_state TEXT,
      task_phase TEXT,
      calibration_request_json TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE kovaak_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL,
      source_key TEXT NOT NULL,
      scenario TEXT,
      stats_path TEXT,
      performance_path TEXT,
      mouse_trace_path TEXT,
      trace_state TEXT NOT NULL DEFAULT 'none',
      window_start_epoch_ms INTEGER,
      window_end_epoch_ms INTEGER,
      alignment_state TEXT NOT NULL DEFAULT 'unresolved',
      alignment_summary TEXT,
      video_path TEXT,
      video_state TEXT NOT NULL DEFAULT 'none',
      video_summary_json TEXT,
      stats_summary TEXT,
      performance_summary TEXT
    );
    CREATE TABLE coach_command_idempotency (
      owner_id TEXT NOT NULL,
      command_name TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      parameters_digest TEXT NOT NULL,
      result_json TEXT NOT NULL,
      latest_audit_ref TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(owner_id, command_name, idempotency_key)
    );
    CREATE TABLE coach_product_commands (
      audit_ref TEXT PRIMARY KEY,
      command_id TEXT NOT NULL,
      owner_id TEXT NOT NULL,
      thread_id INTEGER,
      user_message_ref TEXT,
      command_name TEXT NOT NULL,
      risk TEXT NOT NULL,
      authorization_source TEXT NOT NULL,
      idempotency_key TEXT,
      parameters_digest TEXT,
      safe_parameters_summary_json TEXT,
      status TEXT NOT NULL,
      result_ref TEXT,
      ui_event_json TEXT,
      warning_code TEXT,
      result_json TEXT NOT NULL
    );
  `);
  return db;
}

function fingerprint(path: string): Record<string, unknown> {
  const data = Buffer.from(requireFile(path));
  const stat = statSync(path, { bigint: true });
  return {
    path,
    basename: path.split(/[\\/]/).at(-1),
    sha256: createHash("sha256").update(data).digest("hex"),
    size: data.length,
    mtime_ns: stat.mtimeNs,
    parser_version: "fixture.v1",
    availability: "available",
  };
}

function requireFile(path: string): Buffer {
  return readFileSync(path);
}

function encodeSummary(source: Record<string, unknown>, extra: Record<string, unknown> = {}): string {
  const mtime = source.mtime_ns as bigint;
  const text = JSON.stringify({ source: { ...source, mtime_ns: mtime.toString() }, ...extra });
  return text.replace(`\"mtime_ns\":\"${mtime}\"`, `\"mtime_ns\":${mtime}`);
}

function writeTrace(path: string): void {
  const trace = Buffer.alloc(12);
  trace.write("ACRI", 0, "ascii");
  trace[4] = 2;
  trace.writeUInt32LE(0, 8);
  writeFileSync(path, trace);
}

function seedRun(db: Database.Database, root: string, options: SeedOptions): number {
  mkdirSync(root, { recursive: true });
  const stats = join(root, "Stats.csv");
  writeFileSync(stats, "stats");
  const statsSummary = encodeSummary(fingerprint(stats));

  let performance: string | null = null;
  let performanceSummary: string | null = null;
  if (options.performance) {
    performance = join(root, "Performance.perf");
    writeFileSync(performance, "performance");
    performanceSummary = encodeSummary(fingerprint(performance), {
      header: { scenario_hash: "7378a811f430b6072d052a75896afb98" },
    });
  }

  let trace: string | null = null;
  if (options.trace) {
    trace = join(root, "trace.bin");
    writeTrace(trace);
  }

  let video: string | null = null;
  let videoSummary: string | null = null;
  if (options.video) {
    video = join(root, "video-fixture.mp4");
    writeFileSync(video, "video");
    const bytes = requireFile(video);
    videoSummary = JSON.stringify({
      fingerprint: {
        sha256: createHash("sha256").update(bytes).digest("hex"),
        size: bytes.length,
      },
    });
  }

  const alignment = options.window
    ? JSON.stringify({
      start_ms: 1_000,
      end_ms: 2_000,
      duration_ms: 1_000,
      start_source: "fixture",
      end_source: "fixture",
      timebase_version: "time_alignment.v2",
      warnings: [],
    })
    : null;
  const row = db.prepare(
    "INSERT INTO kovaak_runs(" +
    "user_id, source_key, scenario, stats_path, performance_path, mouse_trace_path, trace_state, " +
    "window_start_epoch_ms, window_end_epoch_ms, alignment_state, alignment_summary, " +
    "video_path, video_state, video_summary_json, stats_summary, performance_summary" +
    ") VALUES('desktop-local', 'fixture', '1wall 6targets small', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
    "RETURNING id",
  ).get(
    stats,
    performance,
    trace,
    trace ? "attached" : "none",
    options.window ? 1_000 : null,
    options.window ? 2_000 : null,
    options.window ? "resolved" : "unresolved",
    alignment,
    video,
    video ? "attached" : "none",
    videoSummary,
    statsSummary,
    performanceSummary,
  ) as { id: number };
  return row.id;
}

for (const fixture of [
  {
    name: "multimodal",
    options: { performance: true, trace: true, video: true, window: true },
    expectedType: "flicking",
  },
  {
    name: "input_native",
    options: { performance: true, trace: true, video: false, window: true },
    expectedType: "flicking",
  },
  {
    name: "video_fallback",
    options: { performance: false, trace: false, video: true, window: false },
    expectedType: "flicking",
  },
] as const) {
  test(`analysis.create_from_run selects ${fixture.name} and freezes a worker-compatible v3 snapshot`, () => {
    const db = createDb();
    const root = mkdtempSync(join(tmpdir(), "analysis-native-"));
    const previousRoot = process.env.DATA_ROOT;
    process.env.DATA_ROOT = join(root, "data");
    try {
      const runId = seedRun(db, join(root, "run"), fixture.options);
      const result = executeNativeWrite(
        db,
        "analysis.create_from_run",
        { run_ref: `run:${runId}` },
        "desktop-local",
        `create-${fixture.name}`,
      );
      assert.equal(result.status, "succeeded");
      const session = db.prepare("SELECT * FROM sessions").get() as Record<string, unknown>;
      assert.equal(session.status, "queued");
      assert.equal(session.task_state, "queued");
      assert.equal(session.input_mode, fixture.name);
      assert.equal(session.analysis_type, fixture.expectedType);
      const snapshot = JSON.parse(String(session.input_snapshot_json));
      assert.equal(snapshot.schema_version, "analysis_input_snapshot.v3");
      assert.equal(snapshot.source_requirements_version, "automatic_quality_tier.v1");
      assert.equal(
        snapshot.scenario_resolution.scenario_profile_ref,
        fixture.name === "video_fallback" ? null : "scenario:static.1wall_6targets_small@1",
      );
      assert.equal(
        snapshot.scenario_resolution.family_analyzer_dispatch,
        fixture.name === "video_fallback" ? "none" : "allowed",
      );
      assert.equal(snapshot.sources.stats.availability, "available");
      assert.equal(typeof snapshot.sources.stats.fingerprint.mtime_ns, "number");
      const python = process.platform === "win32"
        ? join(process.cwd(), ".venv", "Scripts", "python.exe")
        : join(process.cwd(), ".venv", "bin", "python");
      const workerValidation = spawnSync(
        python,
        [
          "-c",
          "import json,sys; from webapp.backend.worker_source_validation import _read_frozen_source_bytes; " +
          "snapshot=json.loads(sys.stdin.read()); " +
          "[_read_frozen_source_bytes(kind, snapshot['sources'][kind]) for kind in ('stats','performance') if kind in snapshot['sources']]",
        ],
        { cwd: process.cwd(), input: String(session.input_snapshot_json), encoding: "utf8" },
      );
      assert.equal(workerValidation.status, 0, workerValidation.stderr);
      if (fixture.name === "video_fallback") {
        assert.match(String(session.csv_path), /stats\.csv$/);
      }
      if (fixture.name === "input_native") assert.equal(session.video_path, "");
    } finally {
      if (previousRoot === undefined) delete process.env.DATA_ROOT;
      else process.env.DATA_ROOT = previousRoot;
      db.close();
      rmSync(root, { recursive: true, force: true });
    }
  });
}

test("analysis.create_from_run reserves the owner atomically", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-active-"));
  const previousRoot = process.env.DATA_ROOT;
  process.env.DATA_ROOT = join(root, "data");
  try {
    const runId = seedRun(db, join(root, "run"), {
      performance: true, trace: true, video: false, window: true,
    });
    db.prepare(
      "INSERT INTO sessions(user_id, status, analysis_type, input_mode) " +
      "VALUES('desktop-local', 'running', 'flicking', 'input_native')",
    ).run();
    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "active",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "active_analysis");
    assert.equal((db.prepare("SELECT COUNT(*) AS count FROM sessions").get() as { count: number }).count, 1);
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("analysis.create_from_run does not select an input-native tier for an invalid canonical window", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-window-"));
  const previousRoot = process.env.DATA_ROOT;
  process.env.DATA_ROOT = join(root, "data");
  try {
    const runId = seedRun(db, join(root, "run"), {
      performance: true, trace: true, video: false, window: true,
    });
    db.prepare(
      "UPDATE kovaak_runs SET alignment_summary=json_set(alignment_summary, '$.timebase_version', 'fixture.v1') WHERE id=?",
    ).run(runId);
    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "bad-window",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "input_unavailable");
    assert.equal((db.prepare("SELECT COUNT(*) AS count FROM sessions").get() as { count: number }).count, 0);
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("analysis.create_from_run derives the scenario directory from an auto-discovered Stats path", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-scenario-"));
  const previousRoot = process.env.DATA_ROOT;
  const previousInstall = process.env.KOVAAK_INSTALL_DIR;
  process.env.DATA_ROOT = join(root, "data");
  delete process.env.KOVAAK_INSTALL_DIR;
  try {
    const installRoot = join(root, "FPSAimTrainer");
    const fpsAimTrainer = join(installRoot, "FPSAimTrainer");
    const runId = seedRun(db, join(fpsAimTrainer, "stats"), {
      performance: true,
      trace: true,
      video: false,
      window: true,
    });
    const scenarioDir = join(fpsAimTrainer, "Saved", "SaveGames", "Scenarios");
    mkdirSync(scenarioDir, { recursive: true });
    const scenarioDefinition = [
      "Name=1wall 6targets small",
      "AddedBots=static.bot;static.bot",
      "PlayerCharacters=player",
      "[Bot Profile]",
      "Name=static",
      "CharacterProfile=target",
      "[Character Profile]",
      "Name=target",
      "MaxSpeed=0",
      "[Character Profile]",
      "Name=player",
      "WeaponProfileNames=pistol",
      "[Weapon Profile]",
      "Name=pistol",
      "Type=Hitscan",
      "Category=Semiauto",
      "ShotsPerClick=1",
      "DamagePerShot=100",
      "",
    ].join("\n");
    writeFileSync(join(scenarioDir, "1wall 6targets small.sce"), scenarioDefinition);

    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "auto-discovered-path",
    );

    assert.equal(result.status, "succeeded");
    const session = db.prepare("SELECT input_snapshot_json FROM sessions").get() as {
      input_snapshot_json: string;
    };
    const snapshot = JSON.parse(session.input_snapshot_json);
    assert.equal(snapshot.scenario_behavior_descriptor?.display_name, "1wall 6targets small");
    assert.equal(
      snapshot.scenario_behavior_descriptor?.source_sha256,
      createHash("sha256").update(scenarioDefinition).digest("hex"),
    );
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    if (previousInstall === undefined) delete process.env.KOVAAK_INSTALL_DIR;
    else process.env.KOVAAK_INSTALL_DIR = previousInstall;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("analysis.create_from_run falls back when an optional higher-tier source is missing", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-fallback-"));
  const previousRoot = process.env.DATA_ROOT;
  process.env.DATA_ROOT = join(root, "data");
  try {
    const runId = seedRun(db, join(root, "run"), {
      performance: true, trace: true, video: true, window: true,
    });
    const run = db.prepare("SELECT video_path FROM kovaak_runs WHERE id=?").get(runId) as { video_path: string };
    rmSync(run.video_path);
    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "missing-video",
    );
    assert.equal(result.status, "succeeded");
    const session = db.prepare("SELECT input_mode, video_path FROM sessions").get() as {
      input_mode: string;
      video_path: string;
    };
    assert.equal(session.input_mode, "input_native");
    assert.equal(session.video_path, "");
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("analysis.create_from_run reports input_unavailable when no tier has Stats", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-no-tier-"));
  const previousRoot = process.env.DATA_ROOT;
  process.env.DATA_ROOT = join(root, "data");
  try {
    const runId = seedRun(db, join(root, "run"), { video: true });
    const run = db.prepare("SELECT stats_path FROM kovaak_runs WHERE id=?").get(runId) as { stats_path: string };
    rmSync(run.stats_path);
    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "no-stats",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "input_unavailable");
    assert.equal((db.prepare("SELECT COUNT(*) AS count FROM sessions").get() as { count: number }).count, 0);
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("analysis.create_from_run removes its uploading reservation when managed input setup fails", () => {
  const db = createDb();
  const root = mkdtempSync(join(tmpdir(), "analysis-native-failure-"));
  const previousRoot = process.env.DATA_ROOT;
  const blockedDataRoot = join(root, "not-a-directory");
  writeFileSync(blockedDataRoot, "blocked");
  process.env.DATA_ROOT = blockedDataRoot;
  try {
    const runId = seedRun(db, join(root, "run"), { video: true });
    const result = executeNativeWrite(
      db, "analysis.create_from_run", { run_ref: `run:${runId}` }, "desktop-local", "failure",
    );
    assert.equal(result.status, "failed");
    assert.equal(result.warning_or_error?.code, "input_setup_failed");
    assert.equal((db.prepare("SELECT COUNT(*) AS count FROM sessions").get() as { count: number }).count, 0);
  } finally {
    if (previousRoot === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousRoot;
    db.close();
    rmSync(root, { recursive: true, force: true });
  }
});
