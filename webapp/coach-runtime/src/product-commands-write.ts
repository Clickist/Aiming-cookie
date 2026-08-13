/**
 * Native SQLite implementations of write product commands.
 *
 * These commands perform the same DB mutations as the Python backend,
 * eliminating the HTTP tool bridge for write operations.
 *
 * Owner filtering is always applied. No security sanitization is performed —
 * this is a single-user desktop app and the data belongs to the user.
 *
 * Idempotency: commands in WRITE_COMMANDS_WITH_IDEMPOTENCY check the
 * coach_command_idempotency table and replay prior results. DIRECT_WRITE_COMMANDS
 * (teaching_session.update, peripheral_profile.update) skip idempotency,
 * matching the Python dispatch.
 */
import { createHash, randomUUID } from "node:crypto";
import { copyFileSync, mkdirSync, existsSync, linkSync, rmSync } from "node:fs";
import { resolve, join } from "node:path";
import { homedir } from "node:os";
import type { SqliteDb } from "./db.ts";
import {
  assertFrozenCopy,
  assertSameFile,
  buildNativeAnalysisInput,
  NativeAnalysisInputError,
} from "./analysis-input-native.ts";

// ── Types ──────────────────────────────────────────────────────────────

type AnyDict = Record<string, any>;

export type NativeWriteResult = {
  status: "succeeded" | "failed";
  result?: unknown;
  result_ref?: string;
  ui_event?: AnyDict | null;
  warning_or_error?: { code: string; message: string };
  command_id: string;
  audit_ref: string;
};

/** Handler return type (command_id/audit_ref filled by the dispatch wrapper). */
type HandlerResult = {
  status: "succeeded" | "failed";
  result?: unknown;
  result_ref?: string;
  ui_event?: AnyDict | null;
  warning_or_error?: { code: string; message: string };
};

type WriteHandler = (db: SqliteDb, params: AnyDict, ownerId: string) => HandlerResult;

// ── Constants ──────────────────────────────────────────────────────────

const RESULT_SCHEMA_VERSION = "coach_product_command_result.v1";

/**
 * Commands that require idempotency checking (Python _WRITE_COMMANDS).
 * These check coach_command_idempotency before executing and record
 * the result afterwards.
 */
const WRITE_COMMANDS_WITH_IDEMPOTENCY = new Set<string>([
  "analysis.create_from_run",
  "analysis.retry",
  "analysis.delete",
  "training_plan.generate_draft",
  "training_plan.save",
  "training_plan.activate",
  "training_plan.pause",
  "training_plan.adjust",
  "training_plan.item.add",
  "training_plan.execution.record",
  "training_plan.retest.record",
  "calibration.save",
  "calibration.delete",
  "kovaak.connection.disconnect",
  "coach.session.create",
  "coach.session.rename",
  "coach.session.archive",
  "coach.session.delete",
  "coach.context.detach",
]);

/**
 * Write commands without idempotency (Python _DIRECT_WRITE_COMMANDS).
 */
const DIRECT_WRITE_COMMANDS = new Set<string>([
  "teaching_session.update",
  "peripheral_profile.update",
]);

export const NATIVE_WRITE_COMMANDS = new Set<string>([
  ...WRITE_COMMANDS_WITH_IDEMPOTENCY,
  ...DIRECT_WRITE_COMMANDS,
]);

// ── Helpers ────────────────────────────────────────────────────────────

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function stableJson(value: unknown): string {
  return JSON.stringify(value);
}

function idempotencyDigest(commandName: string, params: AnyDict): string {
  return createHash("sha256").update(canonicalJson({ command_name: commandName, parameters: params })).digest("hex");
}

function newAuditRef(): string {
  return `audit:${randomUUID().replace(/-/g, "")}`;
}

function newCommandId(): string {
  return `command:${randomUUID().replace(/-/g, "")}`;
}

function sqliteTimestampToWireUtc(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.includes("T") ? value : value.replace(" ", "T") + "Z";
}

function parseJsonColumn(value: unknown): unknown | null {
  if (typeof value !== "string") return null;
  try { return JSON.parse(value); } catch { return null; }
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${field} is required`);
  return value;
}

// ── Workspace helpers (mirror Python config.resolve_data_root + workspace.session_dir) ──

function resolveDataRoot(): string {
  const override = process.env.DATA_ROOT;
  if (override) return resolve(override);
  if (process.platform === "win32") {
    const appData = process.env.APPDATA || process.env.LOCALAPPDATA;
    const base = appData || join(homedir(), "AppData", "Roaming");
    return resolve(base, "Aiming Cookie");
  }
  if (process.platform === "darwin") {
    return resolve(homedir(), "Library", "Application Support", "Aiming Cookie");
  }
  const xdg = process.env.XDG_DATA_HOME;
  const base = xdg || join(homedir(), ".local", "share");
  return resolve(base, "Aiming Cookie");
}

function sessionDir(sessionId: number): string {
  return resolve(resolveDataRoot(), "sessions", String(sessionId));
}

function removeSessionWorkspace(sessionId: number): boolean {
  const path = sessionDir(sessionId);
  if (!existsSync(path)) return false;
  rmSync(path, { recursive: true, force: true });
  return true;
}

function parseRef(value: unknown, expectedKind: string): { id: number; ref: string } {
  if (typeof value !== "string") throw new Error(`${expectedKind}_ref must be a stable reference`);
  const match = value.match(new RegExp(`^${expectedKind}:([1-9]\\d*)$`));
  if (!match) throw new Error(`${expectedKind}_ref must be a ${expectedKind}: reference`);
  return { id: parseInt(match[1], 10), ref: value };
}

function ok(result: unknown, resultRef?: string, uiEvent?: AnyDict | null): HandlerResult {
  return { status: "succeeded", result, result_ref: resultRef, ui_event: uiEvent };
}

function fail(code: string, message: string): HandlerResult {
  return { status: "failed", warning_or_error: { code, message } };
}

function safePlan(plan: AnyDict): AnyDict {
  const keys = [
    "plan_id", "plan_ref", "status", "version", "version_ref",
    "plan_payload", "adjustment_reason", "evidence_refs",
    "verification_targets", "created_at", "updated_at",
  ];
  const out: AnyDict = {};
  for (const key of keys) { if (key in plan) out[key] = plan[key]; }
  return out;
}

function safeAnalysis(session: AnyDict): AnyDict {
  const error = session.error;
  let safeError = null;
  if (error && typeof error === "object") {
    const e = error as AnyDict;
    safeError = {};
    for (const key of ["schema_version", "category", "code", "message", "retryable"]) {
      if (key in e) (safeError as AnyDict)[key] = e[key];
    }
  }
  return {
    analysis_ref: `analysis:${session.id}`,
    id: session.id,
    status: session.status,
    analysis_type: session.analysis_type ?? "flicking",
    input_mode: session.input_mode ?? "video_fallback",
    run_ref: session.kovaak_run_id ? `run:${session.kovaak_run_id}` : null,
    created_at: session.created_at,
    started_at: session.started_at,
    finished_at: session.finished_at,
    error: safeError,
  };
}

// ── Session summary helper (mirrors coach_store._session_summary) ──

function sessionSummary(db: SqliteDb, row: AnyDict): AnyDict {
  const analysisIds = db.prepare(
    "SELECT analysis_session_id FROM coach_analysis_refs WHERE thread_id=? AND status='active' AND analysis_session_id IS NOT NULL ORDER BY id",
  ).all(row.id) as Array<{ analysis_session_id: number }>;
  return {
    id: row.id,
    user_id: row.user_id,
    kind: row.kind,
    title: row.title ?? null,
    status: row.status,
    deleted_at: row.deleted_at ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
    message_count: row.message_count ?? 0,
    last_message_preview: row.last_message_preview ?? null,
    analysis_session_ids: analysisIds.map((r) => r.analysis_session_id),
  };
}

function getSessionById(db: SqliteDb, userId: string, sessionId: number, includeDeleted = false): AnyDict | null {
  let where = "t.user_id=? AND t.id=?";
  const params: Array<unknown> = [userId, sessionId];
  if (!includeDeleted) where += " AND status <> 'deleted'";
  const row = db.prepare(
    "SELECT t.id, t.user_id, t.kind, t.title, t.status, t.deleted_at, t.created_at, t.updated_at, " +
    "COUNT(m.id) AS message_count, " +
    "(SELECT content FROM coach_messages lm WHERE lm.thread_id=t.id ORDER BY lm.id DESC LIMIT 1) AS last_message_preview " +
    "FROM coach_threads t LEFT JOIN coach_messages m ON m.thread_id=t.id " +
    `WHERE ${where} GROUP BY t.id`,
  ).get(...params) as AnyDict | undefined;
  return row ? sessionSummary(db, row) : null;
}

// ── Simple write commands ─────────────────────────────────────────────

// calibration.save
const calibrationSave: WriteHandler = (db, params, ownerId) => {
  const cmPer360 = params.cm_per_360;
  const fov = params.fov;
  if (cmPer360 === null && fov === null) throw new Error("cm_per_360 and fov cannot both be empty");
  if (cmPer360 !== null && cmPer360 !== undefined && !(cmPer360 > 0 && cmPer360 <= 1000)) {
    throw new Error("cm_per_360 must be between 0 and 1000");
  }
  if (fov !== null && fov !== undefined && !(fov > 0 && fov <= 180)) {
    throw new Error("fov must be between 0 and 180");
  }
  db.prepare(
    "INSERT INTO calibration_profiles(owner_id, cm_per_360, fov) VALUES(?, ?, ?) " +
    "ON CONFLICT(owner_id) DO UPDATE SET cm_per_360=excluded.cm_per_360, fov=excluded.fov, updated_at=CURRENT_TIMESTAMP",
  ).run(ownerId, cmPer360 ?? null, fov ?? null);
  const row = db.prepare("SELECT cm_per_360, fov, updated_at FROM calibration_profiles WHERE owner_id=?").get(ownerId) as AnyDict;
  return ok({
    schema_version: "calibration_profile.v1",
    configured: true,
    values: { cm_per_360: row.cm_per_360 ?? null, fov: row.fov ?? null },
    dpi: null,
    sensitivity: null,
    adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
    updated_at: sqliteTimestampToWireUtc(row.updated_at),
  }, "calibration:current");
};

// calibration.delete
const calibrationDelete: WriteHandler = (db, _params, ownerId) => {
  const result = db.prepare("DELETE FROM calibration_profiles WHERE owner_id=?").run(ownerId);
  const deletionState = result.changes > 0 ? "completed" : "already_absent";
  return ok({
    schema_version: "calibration_profile.v1",
    configured: false,
    values: { cm_per_360: null, fov: null },
    dpi: null,
    sensitivity: null,
    adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
    updated_at: null,
    deletion_state: deletionState,
  }, "calibration:current");
};

// kovaak.connection.disconnect
const kovaakDisconnect: WriteHandler = (db, _params, ownerId) => {
  const result = db.prepare("DELETE FROM kovaak_connections WHERE owner_id=?").run(ownerId);
  return ok({
    connection_ref: "kovaak_connection:current",
    disconnected: true,
    was_connected: result.changes > 0,
  }, "kovaak_connection:current");
};

// coach.session.create
const coachSessionCreate: WriteHandler = (db, params, ownerId) => {
  const rawTitle = typeof params.title === "string" ? params.title.trim() : "";
  const title = rawTitle ? rawTitle.slice(0, 120) : "新对话";
  const row = db.prepare(
    "INSERT INTO coach_threads(user_id, kind, title, status) VALUES(?, 'conversation', ?, 'active') " +
    "RETURNING id, user_id, kind, title, status, deleted_at, created_at, updated_at",
  ).get(ownerId, title) as AnyDict;
  const summary = sessionSummary(db, row);
  return ok(summary, `session:${summary.id}`);
};

// coach.session.rename
const coachSessionRename: WriteHandler = (db, params, ownerId) => {
  const sessionRef = requireString(params.session_ref, "session_ref");
  if (!sessionRef.startsWith("session:")) throw new Error("session_ref is required");
  const sessionId = parseInt(sessionRef.split(":")[1], 10);
  if (!Number.isInteger(sessionId)) throw new Error("session_ref is invalid");
  const rawTitle = typeof params.title === "string" ? params.title.trim() : "";
  if (!rawTitle) throw new Error("session title cannot be empty");
  const title = rawTitle.slice(0, 120);
  const result = db.prepare(
    "UPDATE coach_threads SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status <> 'deleted'",
  ).run(title, sessionId, ownerId);
  if (result.changes !== 1) {
    const existing = getSessionById(db, ownerId, sessionId, true);
    if (!existing) return fail("not_found", "Coach session is unavailable");
    return ok(existing, sessionRef);
  }
  return ok(getSessionById(db, ownerId, sessionId)!, sessionRef);
};

// coach.session.archive
const coachSessionArchive: WriteHandler = (db, params, ownerId) => {
  const sessionRef = requireString(params.session_ref, "session_ref");
  if (!sessionRef.startsWith("session:")) throw new Error("session_ref is required");
  const sessionId = parseInt(sessionRef.split(":")[1], 10);
  if (!Number.isInteger(sessionId)) throw new Error("session_ref is invalid");
  const result = db.prepare(
    "UPDATE coach_threads SET status='archived', updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status <> 'deleted'",
  ).run(sessionId, ownerId);
  if (result.changes !== 1) {
    const existing = getSessionById(db, ownerId, sessionId);
    if (!existing) return fail("not_found", "Coach session is unavailable");
    return ok(existing, sessionRef);
  }
  return ok(getSessionById(db, ownerId, sessionId)!, sessionRef);
};

// coach.session.delete
const coachSessionDelete: WriteHandler = (db, params, ownerId) => {
  const sessionRef = requireString(params.session_ref, "session_ref");
  if (!sessionRef.startsWith("session:")) throw new Error("session_ref is required");
  const sessionId = parseInt(sessionRef.split(":")[1], 10);
  if (!Number.isInteger(sessionId)) throw new Error("session_ref is invalid");
  db.prepare(
    "UPDATE coach_threads SET status='deleted', deleted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status <> 'deleted'",
  ).run(sessionId, ownerId);
  const existing = getSessionById(db, ownerId, sessionId, true);
  if (!existing) return fail("not_found", "Coach session is unavailable");
  return ok(existing, sessionRef);
};

// coach.context.detach
const coachContextDetach: WriteHandler = (db, params, ownerId) => {
  const contextRef = requireString(params.context_ref, "context_ref");
  const threadId = params.session_id;
  if (typeof threadId !== "number" || !Number.isInteger(threadId)) throw new Error("session_id is required");
  const row = db.prepare(
    "SELECT c.* FROM coach_context_refs c JOIN coach_threads t ON t.id=c.thread_id WHERE c.context_ref=? AND c.thread_id=? AND t.user_id=?",
  ).get(contextRef, threadId, ownerId) as AnyDict | undefined;
  if (!row) return fail("not_found", "Coach context is unavailable");
  if (row.status !== "active") {
    return ok({ context_ref: contextRef, status: "already_detached" }, contextRef);
  }
  db.prepare(
    "UPDATE coach_context_refs SET status='detached', detached_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE context_ref=?",
  ).run(contextRef);
  return ok({ context_ref: contextRef, status: "detached" }, contextRef);
};

// ── Peripheral profile update ─────────────────────────────────────────

const _GRIP_TYPES = new Set(["fingertip", "fingertip_claw", "claw", "claw_palm", "palm"]);
const _WRIST_POSITIONS = new Set(["suspended", "on_pad"]);
const _PERIPHERAL_ALLOWED_FIELDS = new Set([
  "grip_type", "hand_length_cm", "wrist_position", "grip_preference",
  "current_mouse_brand", "current_mouse_model", "current_mousepad", "budget",
]);
const _PERIPHERAL_COLUMNS = [
  "grip_type", "hand_length_cm", "wrist_position", "grip_preference",
  "current_mouse_brand", "current_mouse_model", "current_mousepad", "budget",
];

function validatePeripheralField(field: string, value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (field === "grip_type" && typeof value === "string" && !_GRIP_TYPES.has(value)) {
    throw new Error(`grip_type must be one of ${[..._GRIP_TYPES].sort().join(", ")}`);
  }
  if (field === "wrist_position" && typeof value === "string" && !_WRIST_POSITIONS.has(value)) {
    throw new Error(`wrist_position must be one of ${[..._WRIST_POSITIONS].sort().join(", ")}`);
  }
  if (field === "hand_length_cm") {
    const v = typeof value === "number" ? value : parseFloat(String(value));
    if (!(v >= 5 && v <= 30)) throw new Error("hand_length_cm must be between 5 and 30");
    return v;
  }
  if (typeof value === "string") {
    const s = value.trim();
    return s ? s.slice(0, 200) : null;
  }
  return value;
}

const peripheralProfileUpdate: WriteHandler = (db, params, ownerId) => {
  const validated: AnyDict = {};
  for (const field of _PERIPHERAL_ALLOWED_FIELDS) {
    if (field in params) validated[field] = validatePeripheralField(field, params[field]);
  }
  if (Object.keys(validated).length === 0) throw new Error("at least one field must be provided");

  const existing = db.prepare(
    "SELECT grip_type, hand_length_cm, wrist_position, grip_preference, current_mouse_brand, current_mouse_model, current_mousepad, budget FROM peripheral_profiles WHERE owner_id=?",
  ).get(ownerId) as AnyDict | undefined;
  const merged: AnyDict = { ...(existing ?? {}) };
  Object.assign(merged, validated);

  const values = _PERIPHERAL_COLUMNS.map((c) => merged[c] ?? null);
  const placeholders = _PERIPHERAL_COLUMNS.map(() => "?").join(", ");
  const updateSet = _PERIPHERAL_COLUMNS.map((c) => `${c}=?`).join(", ");
  db.prepare(
    `INSERT INTO peripheral_profiles(owner_id, ${_PERIPHERAL_COLUMNS.join(", ")}) VALUES(?, ${placeholders}) ` +
    `ON CONFLICT(owner_id) DO UPDATE SET ${updateSet}, updated_at=CURRENT_TIMESTAMP`,
  ).run(ownerId, ...values, ...values);

  const row = db.prepare(
    "SELECT grip_type, hand_length_cm, wrist_position, grip_preference, current_mouse_brand, current_mouse_model, current_mousepad, budget, updated_at FROM peripheral_profiles WHERE owner_id=?",
  ).get(ownerId) as AnyDict;
  return ok({
    schema_version: "peripheral_profile.v1",
    configured: true,
    grip_type: row.grip_type ?? null,
    hand_length_cm: row.hand_length_cm ?? null,
    wrist_position: row.wrist_position ?? null,
    grip_preference: row.grip_preference ?? null,
    current_mouse_brand: row.current_mouse_brand ?? null,
    current_mouse_model: row.current_mouse_model ?? null,
    current_mousepad: row.current_mousepad ?? null,
    budget: row.budget ?? null,
    updated_at: sqliteTimestampToWireUtc(row.updated_at),
  }, "peripheral_profile:current");
};

// ── Analysis commands ─────────────────────────────────────────────────

// analysis.delete
const analysisDelete: WriteHandler = (db, params, ownerId) => {
  const { id: analysisId, ref } = parseRef(params.analysis_ref, "analysis");
  const deleteSession = db.transaction(() => {
    const row = db.prepare("SELECT id, user_id, status FROM sessions WHERE id=?").get(analysisId) as AnyDict | undefined;
    if (!row) throw new Error("not_found:Analysis 不存在");
    if (row.user_id !== ownerId) throw new Error("forbidden:无权访问此 Analysis");
    const status = row.status ?? "";
    if (status !== "done" && status !== "failed") {
      throw new Error("active:分析进行中，请等完成或失败后再删除");
    }
    // Mark analysis refs as deleted (mirrors coach_store.mark_analysis_refs_deleted)
    db.prepare(
      "UPDATE coach_analysis_refs SET status='deleted', deleted_at=CURRENT_TIMESTAMP WHERE analysis_session_id=? AND status='active'",
    ).run(analysisId);
    // Mark context refs as deleted (mirrors coach_context_refs.mark_analysis_deleted)
    db.prepare(
      "UPDATE coach_context_refs SET status='deleted', deleted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP " +
      "WHERE status='active' AND (analysis_session_id=? OR comparison_session_id=?)",
    ).run(analysisId, analysisId);
    // Insert tombstone
    db.prepare(
      "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) VALUES(?, ?)",
    ).run(analysisId, ownerId);
    // Delete chat messages and session
    db.prepare("DELETE FROM chat_messages WHERE session_id=?").run(analysisId);
    db.prepare("DELETE FROM sessions WHERE id=?").run(analysisId);
  });
  try {
    deleteSession();
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    if (msg.startsWith("not_found:")) return fail("not_found", msg.slice("not_found:".length));
    if (msg.startsWith("forbidden:")) return fail("forbidden", msg.slice("forbidden:".length));
    if (msg.startsWith("active:")) return fail("active", msg.slice("active:".length));
    return fail("internal_error", "product command could not be completed");
  }
  // TODO: remove_session_workspace and _invalidate_profile_for_deleted_analysis
  // are file/system operations that remain on the Python side for now.
  return ok({
    analysis_ref: ref,
    deleted: true,
    cleanup_pending: false,
  }, ref);
};

// analysis.retry
const analysisRetry: WriteHandler = (db, params, ownerId) => {
  const { id: analysisId, ref } = parseRef(params.analysis_ref, "analysis");
  // Check ownership
  const session = db.prepare("SELECT user_id FROM sessions WHERE id=?").get(analysisId) as AnyDict | undefined;
  if (!session) return fail("not_found", "Analysis 不存在");
  if (session.user_id !== ownerId) return fail("forbidden", "无权访问此 Analysis");
  // Check no other active session
  const active = db.prepare(
    "SELECT id FROM sessions WHERE user_id=? AND status IN ('uploading', 'queued', 'running') LIMIT 1",
  ).get(ownerId) as AnyDict | undefined;
  if (active && active.id !== analysisId) {
    return { status: "failed", result_ref: `analysis:${active.id}`, warning_or_error: { code: "active_analysis", message: "已有其它 Analysis 正在进行" } };
  }
  // Check session is failed
  const row = db.prepare("SELECT * FROM sessions WHERE id=?").get(analysisId) as AnyDict | undefined;
  if (!row || row.status !== "failed") {
    return fail("invalid_status", "仅 failed 状态可重试");
  }
  // Check no existing retry
  const hasRetry = db.prepare("SELECT 1 FROM sessions WHERE parent_session_id=? LIMIT 1").get(analysisId);
  if (hasRetry) return fail("invalid_status", "this failed attempt already has a retry attempt");
  // Create retry session
  const parentGroup = row.task_group_ref || `task:${analysisId}`;
  const nextAttempt = (row.attempt_number || 1) + 1;
  const inputMode = (row.input_mode as string) || "video_fallback";
  const retrySession = db.transaction(() => {
    const newId = db.prepare(
      "INSERT INTO sessions(" +
      "user_id, status, video_path, csv_path, cm_per_360, fov, analysis_type, " +
      "input_mode, kovaak_run_id, input_snapshot_json, attempts, max_attempts, " +
      "task_group_ref, parent_session_id, attempt_number, task_state, task_phase, " +
      "calibration_request_json" +
      ") VALUES(?, 'uploading', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'retrying', 'preparing_training_record', ?) RETURNING id",
    ).get(
      row.user_id, row.video_path ?? null, row.csv_path ?? null,
      row.cm_per_360 ?? null, row.fov ?? null, row.analysis_type ?? null,
      row.input_mode ?? null, row.kovaak_run_id ?? null,
      row.input_snapshot_json ?? null, row.max_attempts ?? 3,
      parentGroup, analysisId, nextAttempt, row.calibration_request_json ?? null,
    ) as AnyDict;
    return newId.id;
  })();

  // Copy managed inputs from parent session to retry workspace.
  const retryWorkspace = sessionDir(retrySession);
  let retryVideo = row.video_path ?? "";
  let retryCsv = row.csv_path ?? "";
  try {
    if ((inputMode === "video_fallback" || inputMode === "multimodal") && row.video_path && existsSync(row.video_path)) {
      const videoDest = resolve(retryWorkspace, "video.mp4");
      mkdirSync(retryWorkspace, { recursive: true });
      copyFileSync(row.video_path, videoDest);
      retryVideo = videoDest;
    }
    if (inputMode === "video_fallback" && row.csv_path && existsSync(row.csv_path)) {
      const csvDest = resolve(retryWorkspace, "stats.csv");
      mkdirSync(retryWorkspace, { recursive: true });
      copyFileSync(row.csv_path, csvDest);
      retryCsv = csvDest;
    }
    // Transition from 'uploading' to 'queued'.
    const transitionResult = db.prepare(
      "UPDATE sessions SET status='queued', task_state='queued', " +
      "task_phase='preparing_training_record', video_path=?, csv_path=?, " +
      "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='uploading'",
    ).run(retryVideo || null, retryCsv || null, retrySession);
    if (transitionResult.changes !== 1) {
      throw new Error("active:retry attempt reservation was lost");
    }
  } catch (error) {
    try { removeSessionWorkspace(retrySession); } catch { /* best-effort */ }
    db.prepare(
      "UPDATE sessions SET status='failed', task_state='failed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
    ).run(retrySession);
    const msg = error instanceof Error ? error.message : "";
    if (msg.startsWith("active:")) return fail("active", msg.slice("active:".length));
    return fail("input_setup_failed", "无法建立分析输入快照");
  }

  const updated = db.prepare("SELECT * FROM sessions WHERE id=?").get(retrySession) as AnyDict;
  return ok(safeAnalysis(updated), `analysis:${retrySession}`);
};

// analysis.create_from_run
const analysisCreateFromRun: WriteHandler = (db, params, ownerId) => {
  const { id: runId } = parseRef(params.run_ref, "run");
  const ownedRun = db.prepare(
    "SELECT id FROM kovaak_runs WHERE id=? AND user_id=?",
  ).get(runId, ownerId);
  if (!ownedRun) {
    const anyOwner = db.prepare("SELECT id FROM kovaak_runs WHERE id=?").get(runId);
    return anyOwner
      ? fail("forbidden", "No permission to access this Run")
      : fail("not_found", "KovaaK run does not exist");
  }

  const existing = db.prepare(
    "SELECT id, status FROM sessions WHERE user_id=? AND kovaak_run_id=? ORDER BY id DESC",
  ).all(ownerId, runId) as Array<{ id: number; status: string }>;
  const reusable = existing.find((session) => session.status === "done")
    ?? existing.find((session) => ["uploading", "queued", "running"].includes(session.status));
  if (reusable) {
    const analysisRef = `analysis:${reusable.id}`;
    return ok({ session_id: reusable.id, analysis_ref: analysisRef, reused: true }, analysisRef);
  }

  let frozen;
  try {
    frozen = buildNativeAnalysisInput(db, runId, ownerId);
  } catch (error) {
    if (error instanceof NativeAnalysisInputError) {
      const [code, ...message] = error.message.split(":");
      return fail(code || "input_unavailable", message.join(":").trim() || error.message);
    }
    throw error;
  }

  const cmPer360 = typeof params.cm_per_360 === "number" && Number.isFinite(params.cm_per_360)
    ? params.cm_per_360
    : null;
  const fov = typeof params.fov === "number" && Number.isFinite(params.fov) ? params.fov : null;
  const profileDefault = params.profile_default !== null && typeof params.profile_default === "object"
    && !Array.isArray(params.profile_default) ? params.profile_default : null;
  const explicitManualOverride = params.manual_override !== null && typeof params.manual_override === "object"
    && !Array.isArray(params.manual_override) ? params.manual_override : null;
  const calibrationRequest = {
    profile_default: profileDefault,
    manual_override: explicitManualOverride ?? (
      cmPer360 !== null || fov !== null ? { cm_per_360: cmPer360, fov } : null
    ),
  };

  const reservation = db.prepare(
    "INSERT INTO sessions(" +
    "user_id, status, video_path, csv_path, cm_per_360, fov, analysis_type, " +
    "input_mode, kovaak_run_id, input_snapshot_json, attempts, max_attempts, " +
    "attempt_number, task_state, task_phase, calibration_request_json" +
    ") SELECT ?, 'uploading', '', '', ?, ?, ?, ?, ?, ?, 0, 3, 1, 'importing', NULL, ? " +
    "WHERE NOT EXISTS (SELECT 1 FROM sessions WHERE user_id=? " +
    "AND status IN ('uploading', 'queued', 'running')) RETURNING id",
  ).get(
    ownerId,
    cmPer360,
    fov,
    frozen.analysisType,
    frozen.selectedMode,
    runId,
    frozen.snapshotJson,
    JSON.stringify(calibrationRequest),
    ownerId,
  ) as { id: number } | undefined;
  if (!reservation) {
    const nowReusable = db.prepare(
      "SELECT id FROM sessions WHERE user_id=? AND kovaak_run_id=? " +
      "AND status IN ('uploading', 'queued', 'running') ORDER BY id DESC LIMIT 1",
    ).get(ownerId, runId) as { id: number } | undefined;
    if (nowReusable) {
      const analysisRef = `analysis:${nowReusable.id}`;
      return ok({ session_id: nowReusable.id, analysis_ref: analysisRef, reused: true }, analysisRef);
    }
    const active = db.prepare(
      "SELECT id FROM sessions WHERE user_id=? AND status IN ('uploading', 'queued', 'running') LIMIT 1",
    ).get(ownerId) as { id: number } | undefined;
    return {
      status: "failed",
      result_ref: active ? `analysis:${active.id}` : undefined,
      warning_or_error: { code: "active_analysis", message: "Another Analysis is already active" },
    };
  }

  const sessionId = reservation.id;
  db.prepare("UPDATE sessions SET task_group_ref=? WHERE id=?").run(`task:${sessionId}`, sessionId);
  const workspace = sessionDir(sessionId);
  let managedVideo = "";
  let managedCsv = "";
  try {
    if (frozen.selectedMode === "multimodal" || frozen.selectedMode === "video_fallback") {
      if (!frozen.videoPath || !frozen.videoFingerprint) {
        throw new NativeAnalysisInputError("source_unavailable: video identity missing");
      }
      mkdirSync(workspace, { recursive: true });
      managedVideo = resolve(workspace, "video.mp4");
      linkSync(frozen.videoPath, managedVideo);
      assertSameFile(managedVideo, frozen.videoPath);
      assertFrozenCopy(managedVideo, frozen.videoFingerprint);
    }
    if (frozen.selectedMode === "video_fallback") {
      mkdirSync(workspace, { recursive: true });
      managedCsv = resolve(workspace, "stats.csv");
      copyFileSync(frozen.statsPath, managedCsv);
      assertFrozenCopy(managedCsv, frozen.statsFingerprint);
    }
    const transitioned = db.prepare(
      "UPDATE sessions SET status='queued', task_state='queued', " +
      "task_phase='preparing_training_record', video_path=?, csv_path=?, " +
      "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status='uploading'",
    ).run(managedVideo, managedCsv, sessionId, ownerId);
    if (transitioned.changes !== 1) {
      throw new NativeAnalysisInputError("upload_state_lost: Analysis input state is no longer valid");
    }
  } catch (error) {
    const cleanupWorkspace = db.transaction(() => {
      const current = db.prepare(
        "SELECT status FROM sessions WHERE id=? AND user_id=?",
      ).get(sessionId, ownerId) as { status: string } | undefined;
      if (!current) return true;
      if (current.status === "uploading") {
        db.prepare("DELETE FROM sessions WHERE id=? AND user_id=? AND status='uploading'")
          .run(sessionId, ownerId);
        return true;
      }
      if (current.status === "queued") {
        db.prepare(
          "UPDATE sessions SET status='failed', task_state='failed', updated_at=CURRENT_TIMESTAMP " +
          "WHERE id=? AND user_id=? AND status='queued'",
        ).run(sessionId, ownerId);
        return true;
      }
      return false;
    })();
    if (cleanupWorkspace) {
      try { removeSessionWorkspace(sessionId); } catch { /* best-effort */ }
    }
    if (error instanceof NativeAnalysisInputError) {
      const [code, ...message] = error.message.split(":");
      return fail(code || "input_setup_failed", message.join(":").trim() || error.message);
    }
    return fail("input_setup_failed", "Could not prepare the Analysis input snapshot");
  }

  const analysisRef = `analysis:${sessionId}`;
  return ok({
    session_id: sessionId,
    analysis_ref: analysisRef,
    input_mode: frozen.selectedMode,
    limitations: frozen.limitations,
  }, analysisRef, {
    schema_version: "coach_ui_event.v1",
    kind: "analysis",
    analysis_ref: analysisRef,
  });
};

// ── Training plan commands ────────────────────────────────────────────

function selectPlanRow(db: SqliteDb, ownerId: string, planId: string): AnyDict {
  const row = db.prepare(
    "SELECT p.plan_id, p.owner_id, p.status, p.current_version, p.created_at, p.updated_at, " +
    "v.plan_payload_json, v.adjustment_reason, v.evidence_refs_json, v.verification_targets_json " +
    "FROM training_plans p JOIN training_plan_versions v ON v.plan_id=p.plan_id AND v.version=p.current_version " +
    "WHERE p.owner_id=? AND p.plan_id=?",
  ).get(ownerId, planId) as AnyDict | undefined;
  if (row) return row;
  const exists = db.prepare("SELECT 1 FROM training_plans WHERE plan_id=?").get(planId);
  if (exists) {
    const error = new Error("forbidden:无权访问此 Training Plan");
    (error as any).code = "forbidden";
    throw error;
  }
  const error = new Error("not_found:Training Plan 不存在");
  (error as any).code = "not_found";
  throw error;
}

function planFromRow(row: AnyDict): AnyDict {
  const version = row.current_version;
  return {
    plan_id: row.plan_id,
    plan_ref: row.plan_id,
    status: row.status,
    version: version,
    version_ref: `${row.plan_id}:v${version}`,
    plan_payload: parseJsonColumn(row.plan_payload_json),
    adjustment_reason: row.adjustment_reason ?? null,
    evidence_refs: parseJsonColumn(row.evidence_refs_json) ?? [],
    verification_targets: parseJsonColumn(row.verification_targets_json) ?? [],
    created_at: sqliteTimestampToWireUtc(row.created_at),
    updated_at: sqliteTimestampToWireUtc(row.updated_at),
  };
}

function appendTransition(
  db: SqliteDb,
  ownerId: string, planId: string, version: number,
  event: string, fromStatus: string | null, toStatus: string, reason: string | null = null,
): void {
  db.prepare(
    "INSERT INTO training_plan_transitions(owner_id, plan_id, version, event, from_status, to_status, reason) VALUES(?, ?, ?, ?, ?, ?, ?)",
  ).run(ownerId, planId, version, event, fromStatus, toStatus, reason);
}

// training_plan.generate_draft
const trainingPlanGenerateDraft: WriteHandler = (db, params, ownerId) => {
  const payload = params.plan_payload;
  if (!payload || typeof payload !== "object") throw new Error("plan_payload is required");
  const evidenceRefs = Array.isArray(params.evidence_refs) ? params.evidence_refs : [];
  const verificationTargets = Array.isArray(params.verification_targets) ? params.verification_targets : [];
  const planId = `plan:${randomUUID().replace(/-/g, "")}`;

  db.transaction(() => {
    db.prepare(
      "INSERT INTO training_plans(plan_id, owner_id, status, current_version) VALUES(?, ?, 'draft', 1)",
    ).run(planId, ownerId);
    db.prepare(
      "INSERT INTO training_plan_versions(plan_id, version, plan_payload_json, adjustment_reason, evidence_refs_json, verification_targets_json) VALUES(?, 1, ?, NULL, ?, ?)",
    ).run(planId, stableJson(payload), stableJson(evidenceRefs), stableJson(verificationTargets));
    appendTransition(db, ownerId, planId, 1, "generated", null, "draft");
  })();

  const plan = planFromRow(selectPlanRow(db, ownerId, planId));
  return ok(safePlan(plan), plan.plan_ref);
};

// training_plan.save (draft → saved)
const trainingPlanSave: WriteHandler = (db, params, ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  db.transaction(() => {
    const row = selectPlanRow(db, ownerId, planRef);
    if (row.status !== "draft") throw new Error(`invalid_transition:cannot save a ${row.status} plan; expected draft`);
    db.prepare(
      "UPDATE training_plans SET status='saved', updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND plan_id=? AND status='draft'",
    ).run(ownerId, planRef);
    appendTransition(db, ownerId, planRef, row.current_version, "saved", "draft", "saved");
  })();
  const plan = planFromRow(selectPlanRow(db, ownerId, planRef));
  return ok(safePlan(plan), plan.plan_ref);
};

// training_plan.pause (active → paused)
const trainingPlanPause: WriteHandler = (db, params, ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  db.transaction(() => {
    const row = selectPlanRow(db, ownerId, planRef);
    if (row.status !== "active") throw new Error(`invalid_transition:cannot pause a ${row.status} plan; expected active`);
    db.prepare(
      "UPDATE training_plans SET status='paused', updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND plan_id=? AND status='active'",
    ).run(ownerId, planRef);
    appendTransition(db, ownerId, planRef, row.current_version, "paused", "active", "paused");
  })();
  const plan = planFromRow(selectPlanRow(db, ownerId, planRef));
  return ok(safePlan(plan), plan.plan_ref);
};

// training_plan.activate (saved/paused → active)
const trainingPlanActivate: WriteHandler = (db, params, ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  db.transaction(() => {
    const row = selectPlanRow(db, ownerId, planRef);
    const fromStatus = row.status;
    if (fromStatus !== "saved" && fromStatus !== "paused") {
      throw new Error(`invalid_transition:cannot activate a ${fromStatus} plan`);
    }
    // Check for existing active plan
    const active = db.prepare(
      "SELECT plan_id, current_version FROM training_plans WHERE owner_id=? AND status='active' AND plan_id<>?",
    ).get(ownerId, planRef) as AnyDict | undefined;
    if (active) {
      // Pause the existing active plan (replace_active is always true in native path)
      db.prepare(
        "UPDATE training_plans SET status='paused', updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND plan_id=? AND status='active'",
      ).run(ownerId, active.plan_id);
      appendTransition(db, ownerId, active.plan_id, active.current_version, "paused", "active", "paused", `replaced_by:${planRef}`);
    }
    db.prepare(
      "UPDATE training_plans SET status='active', updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND plan_id=? AND status=?",
    ).run(ownerId, planRef, fromStatus);
    appendTransition(db, ownerId, planRef, row.current_version, "activated", fromStatus, "active");
  })();
  const plan = planFromRow(selectPlanRow(db, ownerId, planRef));
  return ok(safePlan(plan), plan.plan_ref);
};

// training_plan.adjust (new version)
const trainingPlanAdjust: WriteHandler = (db, params, ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const payload = params.plan_payload;
  if (!payload || typeof payload !== "object") throw new Error("plan_payload is required");
  const adjustmentReason = requireString(params.adjustment_reason, "adjustment_reason");
  const evidenceRefs = Array.isArray(params.evidence_refs) ? params.evidence_refs : [];
  const verificationTargets = Array.isArray(params.verification_targets) ? params.verification_targets : [];

  db.transaction(() => {
    const row = selectPlanRow(db, ownerId, planRef);
    if (row.status === "draft") throw new Error("invalid_transition:cannot adjust a draft plan before it is saved");
    const nextVersion = row.current_version + 1;
    db.prepare(
      "INSERT INTO training_plan_versions(plan_id, version, plan_payload_json, adjustment_reason, evidence_refs_json, verification_targets_json) VALUES(?, ?, ?, ?, ?, ?)",
    ).run(planRef, nextVersion, stableJson(payload), adjustmentReason, stableJson(evidenceRefs), stableJson(verificationTargets));
    db.prepare(
      "UPDATE training_plans SET current_version=?, updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND plan_id=?",
    ).run(nextVersion, ownerId, planRef);
    appendTransition(db, ownerId, planRef, nextVersion, "adjusted", row.status, row.status, adjustmentReason);
  })();
  const plan = planFromRow(selectPlanRow(db, ownerId, planRef));
  return ok(safePlan(plan), plan.plan_ref);
};

// ── Training plan fact commands ───────────────────────────────────────

function selectItemRow(db: SqliteDb, ownerId: string, itemRef: string): AnyDict {
  const row = db.prepare(
    "SELECT item_ref, owner_id, plan_id, plan_version, item_revision, status, item_payload_json, created_at, updated_at " +
    "FROM training_plan_items WHERE owner_id=? AND item_ref=?",
  ).get(ownerId, itemRef) as AnyDict | undefined;
  if (row) return row;
  const exists = db.prepare("SELECT 1 FROM training_plan_items WHERE item_ref=?").get(itemRef);
  if (exists) {
    const error = new Error("forbidden:无权访问此 Training Plan");
    (error as any).code = "forbidden";
    throw error;
  }
  const error = new Error("not_found:Training Plan 条目不存在");
  (error as any).code = "not_found";
  throw error;
}

function itemProjection(row: AnyDict, statusRef?: string): AnyDict {
  const payload = parseJsonColumn(row.item_payload_json);
  return {
    item_ref: row.item_ref,
    plan_id: row.plan_id,
    plan_revision: row.plan_version,
    plan_revision_ref: `${row.plan_id}:v${row.plan_version}`,
    item_revision: row.item_revision,
    item_revision_ref: `${row.item_ref}:v${row.item_revision}`,
    status: row.status,
    status_ref: statusRef ?? null,
    ...(payload as AnyDict),
    created_at: sqliteTimestampToWireUtc(row.created_at),
    updated_at: sqliteTimestampToWireUtc(row.updated_at),
  };
}

// training_plan.item.add
const trainingPlanItemAdd: WriteHandler = (db, params, ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const itemPayload = params.item_payload;
  if (!itemPayload || typeof itemPayload !== "object") throw new Error("item_payload is required");
  const planVersion = typeof params.plan_version === "number" ? params.plan_version : undefined;
  const itemRef = `plan-item:${randomUUID().replace(/-/g, "")}`;
  const statusRef = `plan-item-status:${randomUUID().replace(/-/g, "")}`;

  db.transaction(() => {
    const plan = selectPlanRow(db, ownerId, planRef);
    const version = planVersion ?? plan.current_version;
    // Verify the version exists
    db.prepare(
      "SELECT 1 FROM training_plan_versions WHERE plan_id=? AND version=?",
    ).get(planRef, version);
    db.prepare(
      "INSERT INTO training_plan_items(item_ref, owner_id, plan_id, plan_version, item_revision, status, item_payload_json) " +
      "VALUES(?, ?, ?, ?, 1, 'planned', ?)",
    ).run(itemRef, ownerId, planRef, version, stableJson(itemPayload));
    db.prepare(
      "INSERT INTO training_plan_item_statuses(status_ref, owner_id, item_ref, plan_id, plan_version, from_status, to_status, reason) " +
      "VALUES(?, ?, ?, ?, ?, NULL, 'planned', NULL)",
    ).run(statusRef, ownerId, itemRef, planRef, version);
  })();

  const row = selectItemRow(db, ownerId, itemRef);
  return ok(itemProjection(row, statusRef), itemRef);
};

// training_plan.execution.record
const trainingPlanExecutionRecord: WriteHandler = (db, params, ownerId) => {
  const itemRef = requireString(params.item_ref, "item_ref");
  const scenarioRef = requireString(params.scenario_ref, "scenario_ref");
  const runRefs = Array.isArray(params.run_refs) ? params.run_refs : [];
  const plannedDose = params.planned_dose;
  const completedDose = params.completed_dose;
  const completionStatus = requireString(params.completion_status, "completion_status");
  const userFeedback = params.user_feedback ?? {};
  if (!["completed", "partial", "skipped"].includes(completionStatus)) {
    throw new Error("unknown execution status");
  }
  const executionRef = `plan-execution:${randomUUID().replace(/-/g, "")}`;

  const row = selectItemRow(db, ownerId, itemRef);
  db.prepare(
    "INSERT INTO training_plan_executions(execution_ref, owner_id, item_ref, plan_id, plan_version, item_revision, " +
    "scenario_ref, run_refs_json, planned_dose_json, completed_dose_json, completion_status, user_feedback) " +
    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(
    executionRef, ownerId, itemRef, row.plan_id, row.plan_version, row.item_revision,
    scenarioRef, stableJson(runRefs), stableJson(plannedDose), stableJson(completedDose),
    completionStatus, typeof userFeedback === "string" ? userFeedback : stableJson(userFeedback),
  );

  return ok({
    execution_ref: executionRef,
    item_ref: itemRef,
    plan_revision_ref: `${row.plan_id}:v${row.plan_version}`,
    item_revision_ref: `${itemRef}:v${row.item_revision}`,
    scenario_ref: scenarioRef,
    run_refs: runRefs,
    planned_dose: plannedDose,
    completed_dose: completedDose,
    completion_status: completionStatus,
    user_feedback: userFeedback,
  }, executionRef);
};

// training_plan.retest.record
const trainingPlanRetestRecord: WriteHandler = (db, params, ownerId) => {
  const itemRef = requireString(params.item_ref, "item_ref");
  const kind = requireString(params.kind, "kind");
  if (!["matched", "near_transfer"].includes(kind)) throw new Error("unknown retest kind");
  const expectedMetricRef = requireString(params.expected_metric_ref, "expected_metric_ref");
  const expectedDirection = requireString(params.expected_direction, "expected_direction");
  const analysisRefs = Array.isArray(params.analysis_refs) ? params.analysis_refs : [];
  const comparability = params.comparability ?? "unresolved";
  const result = requireString(params.result, "result");
  const limitations = Array.isArray(params.limitations) ? params.limitations : [];

  const retestRef = `retest:${randomUUID().replace(/-/g, "")}`;
  const row = selectItemRow(db, ownerId, itemRef);
  db.prepare(
    "INSERT INTO training_plan_retests(retest_ref, owner_id, item_ref, plan_id, plan_version, item_revision, kind, " +
    "expected_metric_ref, expected_direction, analysis_refs_json, comparability, result, limitations_json) " +
    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(
    retestRef, ownerId, itemRef, row.plan_id, row.plan_version, row.item_revision, kind,
    expectedMetricRef, expectedDirection, stableJson(analysisRefs), comparability, result, stableJson(limitations),
  );

  return ok({
    retest_ref: retestRef,
    item_ref: itemRef,
    plan_revision_ref: `${row.plan_id}:v${row.plan_version}`,
    item_revision_ref: `${itemRef}:v${row.item_revision}`,
    kind: kind,
    expected_metric_ref: expectedMetricRef,
    expected_direction: expectedDirection,
    analysis_refs: analysisRefs,
    comparability: comparability,
    result: result,
    limitations: limitations,
  }, retestRef);
};

// ── Teaching session state validation ─────────────────────────────────

const _TEACHING_SESSION_SCHEMA = "teaching_session.v1";
const _TEACHING_PHASES = new Set([
  "intake", "hypothesize", "teach", "await_teach_back", "teach_back_repair",
  "practice_ready", "await_execution_confirmation", "retest_ready",
  "await_retest_confirmation", "revise", "follow_up", "paused", "stopped_for_discomfort",
]);
const _RETEST_INTENTS = new Set(["none", "immediate_matched", "delayed_matched", "near_transfer"]);
const _TEACHING_COMPARABILITY = new Set(["unresolved", "comparable", "not_comparable", "not_requested"]);
const _TEACHING_REVISION_DECISIONS = new Set([null, "retain", "lower", "reject"]);
const _TEACHING_PAUSE_REASONS = new Set([null, "user_refused", "discomfort", "awaiting_confirmation"]);

/**
 * Validate a TeachingSession state after partial merge.
 *
 * Port of Python teaching_session_store.validate_state. Simplified: skips
 * per-field text scanning (path/secret regex), candidate label length limits,
 * source_ref regex validation, and next_recommendation structural checks.
 * Focuses on schema_version, phase enum, observation structure, alternatives
 * count, and the retest/revision/pause enum constraints.
 */
function validateTeachingState(state: unknown): AnyDict {
  if (typeof state !== "object" || state === null || Array.isArray(state)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }
  const s = state as AnyDict;

  if (s.schema_version !== _TEACHING_SESSION_SCHEMA) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }
  if (typeof s.phase !== "string" || !_TEACHING_PHASES.has(s.phase)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  // observation must be {summary, source_refs}
  const obs = s.observation;
  if (typeof obs !== "object" || obs === null || Array.isArray(obs)
    || Object.keys(obs).length !== 2
    || typeof (obs as AnyDict).summary !== "string"
    || (obs as AnyDict).summary.length > 1200
    || !Array.isArray((obs as AnyDict).source_refs)
    || (obs as AnyDict).source_refs.length > 8
  ) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  // alternatives max 2
  if (!Array.isArray(s.alternatives) || s.alternatives.length > 2) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  // Enum fields
  if (typeof s.retest_intent !== "string" || !_RETEST_INTENTS.has(s.retest_intent)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }
  if (typeof s.retest_comparability !== "string" || !_TEACHING_COMPARABILITY.has(s.retest_comparability)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }
  if (!_TEACHING_REVISION_DECISIONS.has(s.revision_decision)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }
  if (!_TEACHING_PAUSE_REASONS.has(s.pause_reason)) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  // Cross-field constraint: revision_decision requires comparable
  if (s.retest_comparability !== "comparable" && s.revision_decision !== null) {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  // pending_confirmation_ref must be string or null
  if (s.pending_confirmation_ref !== null && typeof s.pending_confirmation_ref !== "string") {
    throw new Error("invalid_state:TeachingSession state is invalid");
  }

  return s;
}

// ── Teaching session update ───────────────────────────────────────────

const teachingSessionUpdate: WriteHandler = (db, params, ownerId) => {
  const sessionRef = requireString(params.session_ref, "session_ref");
  const expectedVersion = params.expected_version;
  if (typeof expectedVersion !== "number" || !Number.isInteger(expectedVersion) || expectedVersion < 0) {
    throw new Error("expected_version must be a non-negative integer");
  }
  const nextPhase = params.next_phase;
  if (nextPhase !== null && nextPhase !== undefined && typeof nextPhase !== "string") {
    throw new Error("next_phase must be a string or null");
  }
  const updates = params.updates ?? {};
  if (typeof updates !== "object" || updates === null) throw new Error("updates must be an object");

  // Forbidden direct update fields
  const forbiddenFields = new Set(["active_run_ref", "pending_confirmation_ref", "schema_version", "version", "phase"]);
  for (const key of Object.keys(updates)) {
    if (forbiddenFields.has(key)) throw new Error("TeachingSession update contains forbidden fields");
  }

  let updatedRow: AnyDict;
  try {
    updatedRow = db.transaction(() => {
      // Select owned session for update
      const row = db.prepare(
        "SELECT session.* FROM teaching_sessions AS session " +
        "JOIN coach_threads AS thread ON thread.id=session.thread_id " +
        "WHERE session.session_ref=? AND session.owner_id=? AND thread.user_id=? AND thread.kind='primary'",
      ).get(sessionRef, ownerId, ownerId) as AnyDict | undefined;
      if (!row) throw new Error("conflict:TeachingSession is unavailable");
      if (row.version !== expectedVersion) throw new Error("conflict:TeachingSession changed before this update could apply");

      const currentState = parseJsonColumn(row.state_json) as AnyDict ?? {};
      const merged: AnyDict = { ...currentState };
      if (nextPhase !== null && nextPhase !== undefined) merged.phase = nextPhase;
      Object.assign(merged, updates);

      const normalized = validateTeachingState(merged);

      const result = db.prepare(
        "UPDATE teaching_sessions SET state_json=?, version=version+1, " +
        "pending_confirmation_ref=?, pause_reason=?, updated_at=CURRENT_TIMESTAMP " +
        "WHERE session_ref=? AND owner_id=? AND version=?",
      ).run(
        stableJson(normalized), normalized.pending_confirmation_ref ?? null,
        normalized.pause_reason ?? null, sessionRef, ownerId, expectedVersion,
      );
      if (result.changes !== 1) throw new Error("conflict:TeachingSession changed before this update could apply");
      return db.prepare("SELECT * FROM teaching_sessions WHERE session_ref=?").get(sessionRef) as AnyDict;
    })();
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    if (msg.startsWith("conflict:")) return fail("session_conflict", "TeachingSession changed or is unavailable");
    if (msg.startsWith("invalid_state:")) return fail("invalid_state", msg.slice("invalid_state:".length));
    throw error;
  }

  const state = parseJsonColumn(updatedRow.state_json) as AnyDict ?? {};
  return ok({
    session_ref: updatedRow.session_ref,
    version: updatedRow.version,
    state: state,
    active_run_ref: updatedRow.active_run_ref ?? null,
  }, sessionRef);
};

// ── Handler registry ──────────────────────────────────────────────────

const HANDLERS: Record<string, WriteHandler> = {
  "calibration.save": calibrationSave,
  "calibration.delete": calibrationDelete,
  "kovaak.connection.disconnect": kovaakDisconnect,
  "coach.session.create": coachSessionCreate,
  "coach.session.rename": coachSessionRename,
  "coach.session.archive": coachSessionArchive,
  "coach.session.delete": coachSessionDelete,
  "coach.context.detach": coachContextDetach,
  "peripheral_profile.update": peripheralProfileUpdate,
  "analysis.delete": analysisDelete,
  "analysis.retry": analysisRetry,
  "analysis.create_from_run": analysisCreateFromRun,
  "training_plan.generate_draft": trainingPlanGenerateDraft,
  "training_plan.save": trainingPlanSave,
  "training_plan.activate": trainingPlanActivate,
  "training_plan.pause": trainingPlanPause,
  "training_plan.adjust": trainingPlanAdjust,
  "training_plan.item.add": trainingPlanItemAdd,
  "training_plan.execution.record": trainingPlanExecutionRecord,
  "training_plan.retest.record": trainingPlanRetestRecord,
  "teaching_session.update": teachingSessionUpdate,
};

export function isNativeWriteCommand(commandName: string): boolean {
  return NATIVE_WRITE_COMMANDS.has(commandName);
}

// ── Idempotency + audit helpers ───────────────────────────────────────

type IdempotencyRecord = { digest: string; result: AnyDict };

function lookupIdempotency(db: SqliteDb, ownerId: string, commandName: string, idempotencyKey: string): IdempotencyRecord | null {
  const row = db.prepare(
    "SELECT parameters_digest, result_json FROM coach_command_idempotency WHERE owner_id=? AND command_name=? AND idempotency_key=?",
  ).get(ownerId, commandName, idempotencyKey) as AnyDict | undefined;
  if (!row) return null;
  return { digest: row.parameters_digest, result: parseJsonColumn(row.result_json) as AnyDict };
}

function recordIdempotency(
  db: SqliteDb, ownerId: string, commandName: string,
  idempotencyKey: string, digest: string, result: AnyDict,
): void {
  db.prepare(
    "INSERT INTO coach_command_idempotency(" +
    "owner_id, command_name, idempotency_key, parameters_digest, result_json, latest_audit_ref" +
    ") VALUES(?, ?, ?, ?, ?, ?) " +
    "ON CONFLICT(owner_id, command_name, idempotency_key) DO UPDATE SET " +
    "result_json=excluded.result_json, latest_audit_ref=excluded.latest_audit_ref, " +
    "updated_at=CURRENT_TIMESTAMP " +
    "WHERE coach_command_idempotency.parameters_digest=excluded.parameters_digest",
  ).run(ownerId, commandName, idempotencyKey, digest, stableJson(result), result.audit_ref);
}

function appendAudit(
  db: SqliteDb, ownerId: string, result: AnyDict, context: AnyDict,
): void {
  const warning = result.warning_or_error;
  db.prepare(
    "INSERT INTO coach_product_commands(" +
    "audit_ref, command_id, owner_id, thread_id, user_message_ref, command_name, " +
    "risk, authorization_source, idempotency_key, parameters_digest, " +
    "safe_parameters_summary_json, status, result_ref, ui_event_json, " +
    "warning_code, result_json" +
    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(
    result.audit_ref,
    result.command_id,
    ownerId,
    context.thread_id ?? null,
    context.user_message_ref ?? null,
    context.command_name ?? "unknown",
    context.risk ?? "reversible_write",
    context.authorization_source ?? "explicit_user_request",
    context.idempotency_key ?? null,
    context.parameters_digest ?? null,
    stableJson(context.safe_parameters_summary ?? {}),
    result.status,
    result.result_ref ?? null,
    result.ui_event != null ? stableJson(result.ui_event) : null,
    warning && typeof warning === "object" ? warning.code : null,
    stableJson(result),
  );
  if (typeof context.idempotency_key === "string") {
    db.prepare(
      "UPDATE coach_command_idempotency SET latest_audit_ref=?, updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND command_name=? AND idempotency_key=?",
    ).run(result.audit_ref, ownerId, context.command_name, context.idempotency_key);
  }
}

// ── Main dispatch ─────────────────────────────────────────────────────

export function executeNativeWrite(
  db: SqliteDb,
  commandName: string,
  params: AnyDict,
  ownerId: string,
  idempotencyKey: string | undefined,
): NativeWriteResult {
  const handler = HANDLERS[commandName];
  const commandId = newCommandId();
  const auditRef = newAuditRef();

  /** Build a wire-format result object from status + optional fields. */
  function makeResult(
    status: "succeeded" | "failed",
    opts?: { result?: unknown; result_ref?: string; ui_event?: AnyDict | null; warning_or_error?: { code: string; message: string } },
  ): AnyDict {
    const r: AnyDict = { schema_version: RESULT_SCHEMA_VERSION, command_id: commandId, status, audit_ref: auditRef };
    if (opts?.result_ref !== undefined) r.result_ref = opts.result_ref;
    if (opts?.result !== undefined) r.result = opts.result;
    if (opts?.ui_event) r.ui_event = opts.ui_event;
    if (opts?.warning_or_error) r.warning_or_error = opts.warning_or_error;
    return r;
  }

  /** Audit the result and return it to the caller. */
  function auditAndReturn(result: AnyDict, digest?: string | null): NativeWriteResult {
    appendAudit(db, ownerId, result, {
      command_name: commandName,
      risk: "reversible_write",
      authorization_source: "explicit_user_request",
      idempotency_key: idempotencyKey,
      parameters_digest: digest ?? null,
      safe_parameters_summary: {},
    });
    return result as NativeWriteResult;
  }

  if (!handler) {
    return auditAndReturn(makeResult("failed", {
      warning_or_error: { code: "unknown_command", message: `${commandName} is not a native write command` },
    }));
  }

  const useIdempotency = WRITE_COMMANDS_WITH_IDEMPOTENCY.has(commandName);
  let digest: string | null = null;

  // Idempotency check
  if (useIdempotency) {
    if (!idempotencyKey) {
      return auditAndReturn(makeResult("failed", {
        warning_or_error: { code: "idempotency_key_required", message: "write commands require an idempotency_key" },
      }));
    }
    try {
      digest = idempotencyDigest(commandName, params);
    } catch {
      return auditAndReturn(makeResult("failed", {
        warning_or_error: { code: "invalid_parameters", message: "parameters are not JSON-safe" },
      }));
    }
    const prior = lookupIdempotency(db, ownerId, commandName, idempotencyKey);
    if (prior) {
      if (prior.digest !== digest) {
        return auditAndReturn(makeResult("failed", {
          warning_or_error: { code: "idempotency_conflict", message: "idempotency key was already used with different parameters" },
        }), digest);
      }
      // Replay prior result with fresh command_id and audit_ref
      const replay: AnyDict = { ...prior.result, command_id: commandId, audit_ref: auditRef };
      return auditAndReturn(replay, digest);
    }
  }

  // Execute handler
  let handlerResult: HandlerResult;
  try {
    handlerResult = handler(db, params, ownerId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "product command could not be completed";
    if (message.startsWith("not_found:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "not_found", message: message.slice("not_found:".length) } };
    } else if (message.startsWith("forbidden:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "forbidden", message: message.slice("forbidden:".length) } };
    } else if (message.startsWith("invalid_transition:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "invalid_training_plan", message: message.slice("invalid_transition:".length) } };
    } else if (message.startsWith("active:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "active", message: message.slice("active:".length) } };
    } else {
      handlerResult = { status: "failed", warning_or_error: { code: "internal_error", message: "product command could not be completed" } };
    }
  }

  const wireResult = makeResult(handlerResult.status, handlerResult);

  // Record idempotency (after successful or failed execution)
  if (useIdempotency && idempotencyKey && digest) {
    try {
      recordIdempotency(db, ownerId, commandName, idempotencyKey, digest, wireResult);
    } catch {
      // Idempotency conflict — another execution with different params already recorded
      return auditAndReturn(makeResult("failed", {
        warning_or_error: { code: "idempotency_conflict", message: "idempotency key was already used with different parameters" },
      }), digest);
    }
  }

  return auditAndReturn(wireResult, digest);
}
