import assert from "node:assert/strict";
import test from "node:test";

import Database from "better-sqlite3";

import {
  AgentRunError,
  createAgentRun,
  getAgentRun,
  releaseTeachingRun,
  resumeWaitingRuns,
  retryAgentRun,
} from "../src/agent-runs.ts";
import { waitForTask } from "../src/task-manager.ts";

function createAgentRunDb(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE coach_threads (
      id INTEGER PRIMARY KEY,
      user_id TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'primary',
      status TEXT NOT NULL DEFAULT 'active',
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE coach_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      thread_id INTEGER NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      context_refs_json TEXT,
      trace_json TEXT
    );
    CREATE TABLE coach_context_refs (
      context_ref TEXT PRIMARY KEY,
      thread_id INTEGER NOT NULL,
      kind TEXT NOT NULL,
      analysis_session_id INTEGER NOT NULL,
      comparison_session_id INTEGER,
      target_ref TEXT,
      start_ms REAL,
      end_ms REAL,
      projection_json TEXT,
      comparison_projection_json TEXT,
      status TEXT NOT NULL,
      attached_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE coach_agent_runs (
      run_ref TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL,
      thread_id INTEGER NOT NULL,
      parent_run_ref TEXT,
      attempt INTEGER NOT NULL,
      status TEXT NOT NULL,
      phase TEXT NOT NULL,
      content TEXT NOT NULL,
      user_message_id INTEGER,
      context_refs_json TEXT NOT NULL DEFAULT '[]',
      partial_text TEXT,
      error_json TEXT,
      stop_requested INTEGER NOT NULL DEFAULT 0,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      started_at TEXT,
      finished_at TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE coach_agent_run_events (
      event_ref TEXT PRIMARY KEY,
      run_ref TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      phase TEXT NOT NULL,
      code TEXT NOT NULL,
      message TEXT NOT NULL,
      payload_json TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(run_ref, sequence)
    );
    CREATE TABLE provider_profiles (
      id INTEGER PRIMARY KEY,
      owner_id TEXT NOT NULL,
      provider_id TEXT NOT NULL,
      name TEXT NOT NULL,
      kind TEXT NOT NULL,
      base_url TEXT,
      model_id TEXT NOT NULL,
      context_window INTEGER,
      max_tokens INTEGER,
      is_default INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE provider_credentials (
      profile_id INTEGER NOT NULL,
      owner_id TEXT NOT NULL,
      credential_json TEXT,
      needs_reauth INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE teaching_sessions (
      session_ref TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL,
      thread_id INTEGER NOT NULL,
      state_json TEXT NOT NULL,
      version INTEGER NOT NULL,
      active_run_ref TEXT,
      pending_confirmation_ref TEXT,
      pause_reason TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
  `);
  return db;
}

test("agent run reads use wire-format UTC timestamps", () => {
  const db = createAgentRunDb();
  try {
    db.prepare("INSERT INTO coach_threads(id, user_id) VALUES(1, 'desktop-local')").run();
    db.prepare(
      "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content, " +
      "context_refs_json, created_at) VALUES('agent_run:timestamp', 'desktop-local', 1, 1, " +
      "'queued', 'queued', 'test', '[]', '2026-08-13 10:20:30')",
    ).run();

    const run = getAgentRun(db, "desktop-local", "agent_run:timestamp");
    assert.equal(run?.created_at, "2026-08-13T10:20:30Z");
  } finally {
    db.close();
  }
});

test("retry reuses the original user message instead of appending a duplicate", async () => {
  const db = createAgentRunDb();
  try {
    db.prepare("INSERT INTO coach_threads(id, user_id) VALUES(1, 'desktop-local')").run();
    const message = db.prepare(
      "INSERT INTO coach_messages(thread_id, role, content, context_refs_json) " +
      "VALUES(1, 'user', '请分析这一局', '[]') RETURNING id",
    ).get() as { id: number };
    db.prepare(
      "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content, " +
      "user_message_id, context_refs_json, error_json) " +
      "VALUES('agent_run:parent', 'desktop-local', 1, 1, 'failed', 'completed', ?, ?, '[]', ?)",
    ).run(
      "请分析这一局",
      message.id,
      JSON.stringify({ domain: "model", code: "turn_failed", message: "failed", retryable: true }),
    );
    db.prepare(
      "INSERT INTO coach_agent_run_events(event_ref, run_ref, sequence, event_type, phase, code, message) " +
      "VALUES('event:parent', 'agent_run:parent', 1, 'error', 'completed', 'turn_failed', 'failed')",
    ).run();

    const retried = retryAgentRun(db, "desktop-local", "agent_run:parent");
    assert.ok(retried);
    await waitForTask(retried.run_ref);

    const child = db.prepare(
      "SELECT user_message_id FROM coach_agent_runs WHERE run_ref=?",
    ).get(retried.run_ref) as { user_message_id: number };
    const count = db.prepare(
      "SELECT COUNT(*) AS count FROM coach_messages WHERE thread_id=1 AND role='user'",
    ).get() as { count: number };
    assert.equal(child.user_message_id, message.id);
    assert.equal(count.count, 1);
  } finally {
    db.close();
  }
});

test("a missing Provider leaves the run queued for automatic recovery", async () => {
  const db = createAgentRunDb();
  try {
    db.prepare("INSERT INTO coach_threads(id, user_id) VALUES(1, 'desktop-local')").run();

    const created = createAgentRun(db, "desktop-local", "等 Provider 配好后继续", { sessionId: 1 });
    await waitForTask(created.run_ref);

    const waiting = db.prepare(
      "SELECT status, phase, error_json, finished_at FROM coach_agent_runs WHERE run_ref=?",
    ).get(created.run_ref) as {
      status: string;
      phase: string;
      error_json: string;
      finished_at: string | null;
    };
    assert.equal(waiting.status, "queued");
    assert.equal(waiting.phase, "queued");
    assert.equal(JSON.parse(waiting.error_json).code, "provider_unconfigured");
    assert.equal(waiting.finished_at, null);
    const events = db.prepare(
      "SELECT code FROM coach_agent_run_events WHERE run_ref=? ORDER BY sequence",
    ).all(created.run_ref) as Array<{ code: string }>;
    assert.equal(events.at(-1)?.code, "provider_waiting");
  } finally {
    db.close();
  }
});

test("Provider recovery resumes the same run without appending its user message again", async () => {
  const db = createAgentRunDb();
  try {
    db.prepare("INSERT INTO coach_threads(id, user_id) VALUES(1, 'desktop-local')").run();
    const message = db.prepare(
      "INSERT INTO coach_messages(thread_id, role, content, context_refs_json) " +
      "VALUES(1, 'user', '继续刚才的问题', '[]') RETURNING id",
    ).get() as { id: number };
    db.prepare(
      "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content, " +
      "user_message_id, context_refs_json, error_json) " +
      "VALUES('agent_run:waiting', 'desktop-local', 1, 1, 'queued', 'queued', ?, ?, '[]', ?)",
    ).run(
      "继续刚才的问题",
      message.id,
      JSON.stringify({
        domain: "permission",
        code: "provider_unconfigured",
        message: "waiting",
        retryable: true,
      }),
    );
    db.prepare(
      "INSERT INTO coach_agent_run_events(event_ref, run_ref, sequence, event_type, phase, code, message) " +
      "VALUES('event:waiting', 'agent_run:waiting', 1, 'status', 'queued', 'provider_waiting', 'waiting')",
    ).run();
    db.prepare(
      "INSERT INTO provider_profiles(id, owner_id, provider_id, name, kind, model_id, is_default) " +
      "VALUES(1, 'desktop-local', 'unknown-provider', 'Unknown', 'builtin', 'unknown-model', 1)",
    ).run();

    assert.deepEqual(resumeWaitingRuns(db, "desktop-local"), ["agent_run:waiting"]);
    await waitForTask("agent_run:waiting");

    const run = db.prepare(
      "SELECT user_message_id FROM coach_agent_runs WHERE run_ref='agent_run:waiting'",
    ).get() as { user_message_id: number };
    const count = db.prepare(
      "SELECT COUNT(*) AS count FROM coach_messages WHERE thread_id=1 AND role='user'",
    ).get() as { count: number };
    assert.equal(run.user_message_id, message.id);
    assert.equal(count.count, 1);
  } finally {
    db.close();
  }
});

test("a stale teaching-state update releases its claim and fails explicitly", () => {
  const db = createAgentRunDb();
  try {
    db.prepare(
      "INSERT INTO teaching_sessions(session_ref, owner_id, thread_id, state_json, version, active_run_ref) " +
      "VALUES('teaching_session:0123456789abcdef0123456789abcdef', 'desktop-local', 1, ?, 2, 'agent_run:active')",
    ).run(JSON.stringify({ phase: "teach" }));

    assert.throws(
      () => releaseTeachingRun(
        db,
        "desktop-local",
        "teaching_session:0123456789abcdef0123456789abcdef",
        1,
        "agent_run:active",
        { phase: "practice_ready" },
      ),
      (error: unknown) => error instanceof AgentRunError && error.code === "teaching_state_conflict",
    );

    const current = db.prepare(
      "SELECT state_json, version, active_run_ref FROM teaching_sessions",
    ).get() as { state_json: string; version: number; active_run_ref: string | null };
    assert.deepEqual(JSON.parse(current.state_json), { phase: "teach" });
    assert.equal(current.version, 2);
    assert.equal(current.active_run_ref, null);
  } finally {
    db.close();
  }
});

test("a stale release clears the current run without overwriting newer teaching state", () => {
  const db = createAgentRunDb();
  try {
    db.prepare(
      "INSERT INTO teaching_sessions(session_ref, owner_id, thread_id, state_json, version, active_run_ref) " +
      "VALUES('teaching_session:0123456789abcdef0123456789abcdef', 'desktop-local', 1, ?, 2, 'agent_run:active')",
    ).run(JSON.stringify({ phase: "paused" }));

    releaseTeachingRun(
      db,
      "desktop-local",
      "teaching_session:0123456789abcdef0123456789abcdef",
      1,
      "agent_run:active",
      null,
    );

    const current = db.prepare(
      "SELECT state_json, version, active_run_ref FROM teaching_sessions",
    ).get() as { state_json: string; version: number; active_run_ref: string | null };
    assert.deepEqual(JSON.parse(current.state_json), { phase: "paused" });
    assert.equal(current.version, 2);
    assert.equal(current.active_run_ref, null);
  } finally {
    db.close();
  }
});
