/**
 * Sidecar Coach data-access layer.
 *
 * Reads and writes the same SQLite tables the Python backend uses
 * (coach_threads, coach_messages, coach_agent_runs, coach_context_refs,
 * coach_analysis_refs) via better-sqlite3. Every function returns the
 * same JSON shape the corresponding Python route returns so the frontend
 * can call either backend transparently.
 */

import type http from "node:http";
import { createHash } from "node:crypto";

import { getDb, type SqliteDb } from "./db.ts";

function sqliteTimestampToWireUtc(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.includes("T") ? value : value.replace(" ", "T") + "Z";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function ownerIdFromRequest(req: http.IncomingMessage): string {
  const raw = req.headers["x-user-id"];
  if (typeof raw === "string" && raw.trim()) return raw;
  return "desktop-local";
}

function parseJson(value: unknown): unknown {
  if (typeof value !== "string" || !value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Thread resolution
// ---------------------------------------------------------------------------

function getOrCreatePrimaryThread(db: SqliteDb, ownerId: string): { id: number; user_id: string; kind: string; created_at: string; updated_at: string } {
  db.prepare(
    "UPDATE coach_threads SET status='active', deleted_at=NULL, updated_at=CURRENT_TIMESTAMP "
      + "WHERE user_id=? AND kind='primary' AND status='deleted'",
  ).run(ownerId);
  db.prepare(
    "INSERT OR IGNORE INTO coach_threads(user_id, kind) VALUES(?, 'primary')",
  ).run(ownerId);
  const row = db.prepare(
    "SELECT id, user_id, kind, created_at, updated_at FROM coach_threads "
      + "WHERE user_id=? AND kind='primary' AND status <> 'deleted'",
  ).get(ownerId) as { id: number; user_id: string; kind: string; created_at: string; updated_at: string };
  return row;
}

function getPrimaryThread(db: SqliteDb, ownerId: string): { id: number; user_id: string; kind: string; created_at: string; updated_at: string } | undefined {
  return db.prepare(
    "SELECT id, user_id, kind, created_at, updated_at FROM coach_threads "
      + "WHERE user_id=? AND kind='primary' AND status <> 'deleted'",
  ).get(ownerId) as { id: number; user_id: string; kind: string; created_at: string; updated_at: string } | undefined;
}

/** Resolve the active thread for a request — creates primary if no session_id. */
function threadIdForRequest(db: SqliteDb, ownerId: string, sessionId?: number): number {
  if (sessionId === undefined) {
    return getOrCreatePrimaryThread(db, ownerId).id;
  }
  const row = db.prepare(
    "SELECT id FROM coach_threads WHERE user_id=? AND id=? AND status <> 'deleted'",
  ).get(ownerId, sessionId) as { id: number } | undefined;
  if (!row) throw new CoachDataError(404, "Coach session is unavailable");
  return row.id;
}

// ---------------------------------------------------------------------------
// Error helper
// ---------------------------------------------------------------------------

export class CoachDataError extends Error {
  constructor(public statusCode: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------------------
// Session shaping (matches Python _coach_session_out)
// ---------------------------------------------------------------------------

function analysisSessionIds(db: SqliteDb, threadId: number): number[] {
  const rows = db.prepare(
    "SELECT analysis_session_id FROM coach_analysis_refs "
      + "WHERE thread_id=? AND status='active' AND analysis_session_id IS NOT NULL ORDER BY id",
  ).all(threadId) as { analysis_session_id: number }[];
  return rows.map((r) => r.analysis_session_id);
}

interface SessionRow {
  id: number;
  user_id: string;
  kind: string;
  title: string | null;
  status: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
}

interface SessionOut {
  id: number;
  user_id: string;
  kind: string;
  title: string | null;
  status: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
  analysis_session_ids: number[];
}

function shapeSession(db: SqliteDb, row: SessionRow): SessionOut {
  const kind = row.kind === "primary" ? "primary" : "conversation";
  let preview: string | null = row.last_message_preview;
  if (typeof preview === "string" && preview.length > 240) {
    preview = preview.slice(0, 240);
  }
  return {
    id: row.id,
    user_id: row.user_id,
    kind,
    title: row.title,
    status: row.status ?? "active",
    deleted_at: row.deleted_at,
    created_at: row.created_at,
    updated_at: row.updated_at,
    message_count: row.message_count ?? 0,
    last_message_preview: preview,
    analysis_session_ids: analysisSessionIds(db, row.id),
  };
}

const SESSION_COLUMNS =
  "t.id, t.user_id, t.kind, t.title, t.status, t.deleted_at, "
  + "t.created_at, t.updated_at, COUNT(m.id) AS message_count, "
  + "(SELECT content FROM coach_messages lm WHERE lm.thread_id=t.id ORDER BY lm.id DESC LIMIT 1) AS last_message_preview "
  + "FROM coach_threads t LEFT JOIN coach_messages m ON m.thread_id=t.id";

// ---------------------------------------------------------------------------
// Message loading + card computation (matches Python load_messages + _coach_message_cards)
// ---------------------------------------------------------------------------

interface MessageRow {
  id: number;
  role: string;
  content: string;
  created_at: string;
  trace_json: string | null;
  legacy_session_id: number | null;
  context_json: string | null;
  context_refs_json: string | null;
}

interface ContextRefSnapshot {
  context_ref?: string;
  kind?: string;
  status?: string;
  analysis_ref?: string;
  target_ref?: string | null;
  time_range_ms?: number[] | null;
  [key: string]: unknown;
}

interface CoachMessageOut {
  id: number;
  role: string;
  content: string;
  created_at: string;
  legacy_session_id: number | null;
  context: unknown;
  context_refs: ContextRefSnapshot[];
  cards: CoachMessageCard[];
}

const COACH_CARD_COMMANDS: Record<string, string> = {
  "analysis.run_facts.get": "metrics",
  "analysis.outcomes.timeline": "timeline",
  "analysis.events.list": "timeline",
  "analysis.events.filter": "timeline",
  "analysis.events.get": "timeline",
  "analysis.evidence.list": "evidence",
  "analysis.evidence.signal_window": "evidence",
};

function validAnalysisRef(value: unknown): boolean {
  if (typeof value !== "string" || !value.startsWith("analysis:")) return false;
  const suffix = value.slice("analysis:".length);
  return /^\d+$/.test(suffix) && Number(suffix) > 0 && value === `analysis:${Number(suffix)}`;
}

function safeTimeRange(value: unknown): [number, number] | null {
  if (
    !Array.isArray(value)
    || value.length !== 2
    || !value.every((p) => typeof p === "number" && !Number.isNaN(p))
    || value[0] < 0
    || value[1] < value[0]
  ) {
    return null;
  }
  return [value[0], value[1]];
}

function briefTimeRanges(
  context: unknown,
  analysisRef: string,
): Array<[string | null, [number, number]]> {
  if (!isRecord(context) || context.schema_version !== "coach_turn_context.v1") return [];
  const contexts = context.contexts;
  if (!Array.isArray(contexts)) return [];
  const ranges: Array<[string | null, [number, number]]> = [];
  for (const item of contexts) {
    if (!isRecord(item) || item.analysis_ref !== analysisRef) continue;
    const projection = item.projection;
    if (!isRecord(projection)) continue;
    const brief = projection.analysis_brief;
    if (!isRecord(brief)) continue;
    const segments = brief.evidence_segments;
    if (!Array.isArray(segments)) continue;
    for (const segment of segments) {
      if (!isRecord(segment)) continue;
      const tr = safeTimeRange([segment.relative_start_ms, segment.relative_end_ms]);
      if (tr) {
        const segId = typeof segment.segment_id === "string" ? segment.segment_id : null;
        ranges.push([segId, tr]);
      }
    }
  }
  return ranges;
}

interface CoachMessageCard {
  schema_version: "coach_message_card.v1";
  kind: string;
  analysis_ref: string;
  target_ref: string | null;
  time_range_ms: [number, number] | null;
}

function coachMessageCards(message: {
  role: string;
  context_refs: ContextRefSnapshot[];
  trace: unknown[];
  context: unknown;
}): CoachMessageCard[] {
  if (message.role !== "assistant") return [];
  const contexts = message.context_refs.filter(
    (item) => item.status === "active" && validAnalysisRef(item.analysis_ref),
  ).slice(0, 2);
  if (!contexts.length) return [];

  const kinds: string[] = [];
  for (const event of message.trace) {
    if (!isRecord(event) || event.status !== "succeeded") continue;
    const kind = COACH_CARD_COMMANDS[event.command_name as string];
    if (kind && !kinds.includes(kind)) kinds.push(kind);
  }
  for (const ctx of contexts) {
    const ck = ctx.kind;
    const inferred =
      ck === "metric" ? "metrics"
      : ck === "evidence_segment" || ck === "time_range" ? "evidence"
      : ck === "comparison" ? "timeline"
      : null;
    if (inferred && !kinds.includes(inferred)) kinds.push(inferred);
  }

  const cards: CoachMessageCard[] = [];
  const seen = new Set<string>();
  for (const kind of kinds) {
    for (const ctx of contexts) {
      const targetRef = ctx.target_ref;
      const timeRanges: Array<[number, number] | null> = [safeTimeRange(ctx.time_range_ms)];
      const briefRanges = kind === "evidence" ? briefTimeRanges(message.context, ctx.analysis_ref!) : [];
      if (timeRanges[0] === null && briefRanges.length) {
        timeRanges.length = 0;
        for (const [, tr] of briefRanges) timeRanges.push(tr);
      }
      for (let index = 0; index < timeRanges.length; index++) {
        const timeRange = timeRanges[index];
        const segmentRef = index < briefRanges.length ? briefRanges[index][0] : null;
        const cardKey = `${kind}:${ctx.analysis_ref}:${index}`;
        if (seen.has(cardKey)) continue;
        cards.push({
          schema_version: "coach_message_card.v1",
          kind,
          analysis_ref: ctx.analysis_ref!,
          target_ref:
            typeof targetRef === "string" && targetRef.length <= 200
              ? targetRef
              : segmentRef,
          time_range_ms: timeRange,
        });
        seen.add(cardKey);
        if (cards.length === 4) return cards;
      }
    }
  }
  return cards;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function overlaySnapshotStatuses(db: SqliteDb, snapshots: ContextRefSnapshot[]): ContextRefSnapshot[] {
  return snapshots.map((snapshot) => {
    const ref = snapshot.context_ref;
    if (typeof ref !== "string") return snapshot;
    const row = db.prepare(
      "SELECT status, deleted_at FROM coach_context_refs WHERE context_ref=?",
    ).get(ref) as { status: string; deleted_at: string | null } | undefined;
    if (row && row.status === "deleted") {
      return { ...snapshot, status: "deleted", deleted_at: row.deleted_at };
    }
    return snapshot;
  });
}

function loadMessages(db: SqliteDb, threadId: number): CoachMessageOut[] {
  const rows = db.prepare(
    "SELECT id, role, content, created_at, trace_json, legacy_session_id, "
      + "context_json, context_refs_json FROM coach_messages WHERE thread_id=? ORDER BY id",
  ).all(threadId) as MessageRow[];

  return rows.map((r) => {
    const trace = (parseJson(r.trace_json) as unknown[]) ?? [];
    const context = parseJson(r.context_json);
    let contextRefs = parseJson(r.context_refs_json);
    if (Array.isArray(contextRefs) && contextRefs.every((item) => isRecord(item))) {
      contextRefs = overlaySnapshotStatuses(db, contextRefs as ContextRefSnapshot[]);
    } else {
      contextRefs = [];
    }
    const message = {
      id: r.id,
      role: r.role,
      content: r.content,
      created_at: r.created_at,
      legacy_session_id: r.legacy_session_id,
      context,
      context_refs: contextRefs as ContextRefSnapshot[],
      trace,
    };
    return {
      ...message,
      cards: coachMessageCards(message),
    };
  });
}

// ---------------------------------------------------------------------------
// Analysis refs (matches Python _coach_ref_out)
// ---------------------------------------------------------------------------

interface AnalysisRefRow {
  id: number;
  thread_id: number;
  analysis_session_id: number | null;
  status: string;
  attached_at: string;
  deleted_at: string | null;
}

interface AnalysisRefOut {
  id: number;
  analysis_session_id: number | null;
  status: string;
  attached_at: string;
  deleted_at: string | null;
}

function shapeAnalysisRef(r: AnalysisRefRow): AnalysisRefOut {
  return {
    id: r.id,
    analysis_session_id: r.analysis_session_id,
    status: r.status === "deleted" ? "unavailable" : r.status,
    attached_at: r.attached_at,
    deleted_at: r.deleted_at,
  };
}

function listAnalysisRefs(db: SqliteDb, threadId: number): AnalysisRefOut[] {
  const rows = db.prepare(
    "SELECT id, thread_id, analysis_session_id, status, attached_at, deleted_at "
      + "FROM coach_analysis_refs WHERE thread_id=? ORDER BY id",
  ).all(threadId) as AnalysisRefRow[];
  return rows.map(shapeAnalysisRef);
}

// ---------------------------------------------------------------------------
// Context refs (matches Python coach_context_refs._public)
// ---------------------------------------------------------------------------

interface ContextRefRow {
  context_ref: string;
  thread_id: number;
  kind: string;
  analysis_session_id: number;
  comparison_session_id: number | null;
  target_ref: string | null;
  start_ms: number | null;
  end_ms: number | null;
  label: string | null;
  status: string;
  attached_at: string;
  detached_at: string | null;
  deleted_at: string | null;
}

interface ContextRefOut {
  schema_version: "coach_context_ref.v1";
  context_ref: string;
  kind: string;
  status: string;
  label: string;
  analysis_ref: string;
  comparison_analysis_ref: string | null;
  target_ref: string | null;
  time_range_ms: [number, number] | null;
  attached_at: string;
  detached_at: string | null;
  deleted_at: string | null;
}

function publicLabel(value: unknown): string {
  if (typeof value === "string" && value && !/\banalysis:[1-9][0-9]*\b/.test(value)) {
    return value;
  }
  return "分析记录";
}

function shapeContextRef(row: ContextRefRow): ContextRefOut {
  return {
    schema_version: "coach_context_ref.v1",
    context_ref: row.context_ref,
    kind: row.kind,
    status: row.status,
    label: publicLabel(row.label),
    analysis_ref: `analysis:${row.analysis_session_id}`,
    comparison_analysis_ref: row.comparison_session_id
      ? `analysis:${row.comparison_session_id}`
      : null,
    target_ref: row.target_ref,
    time_range_ms:
      row.start_ms !== null && row.end_ms !== null
        ? [row.start_ms, row.end_ms]
        : null,
    attached_at: row.attached_at,
    detached_at: row.detached_at,
    deleted_at: row.deleted_at,
  };
}

// ---------------------------------------------------------------------------
// Public API — each function takes a db and returns plain objects
// ---------------------------------------------------------------------------

/** GET /v1/agent-runs/:ref — matches Python coach_agent_runs.get_run(). */
export function getAgentRun(runRef: string, ownerId: string): Record<string, unknown> | null {
  const db = getDb();
  if (!db) return null;
  const row = db.prepare(
    "SELECT * FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
  ).get(runRef, ownerId) as Record<string, unknown> | undefined;
  if (!row) return null;

  const contexts = parseJson(row.context_refs_json) ?? [];
  const error = parseJson(row.error_json);

  const eventRows = db.prepare(
    "SELECT event_ref, sequence, event_type, phase, code, message, payload_json, created_at "
      + "FROM coach_agent_run_events WHERE run_ref=? ORDER BY sequence",
  ).all(runRef) as Array<{
    event_ref: string;
    sequence: number;
    event_type: string;
    phase: string;
    code: string;
    message: string;
    payload_json: string | null;
    created_at: string;
  }>;

  const events = eventRows.map((r) => ({
    schema_version: "coach_agent_run_event.v1",
    event_ref: r.event_ref,
    sequence: r.sequence,
    type: r.event_type,
    phase: r.phase,
    code: r.code,
    message: r.message,
    payload: parseJson(r.payload_json),
    created_at: r.created_at,
  }));

  return {
    schema_version: "coach_agent_run.v1",
    run_ref: row.run_ref,
    session_id: row.thread_id as number,
    parent_run_ref: row.parent_run_ref ?? null,
    attempt: row.attempt as number,
    status: row.status as string,
    phase: row.phase as string,
    partial_text: row.partial_text ?? null,
    error,
    contexts,
    events,
    created_at: sqliteTimestampToWireUtc(row.created_at),
    started_at: sqliteTimestampToWireUtc(row.started_at),
    finished_at: sqliteTimestampToWireUtc(row.finished_at),
  };
}

/** GET /v1/sessions — matches Python list_coach_sessions(). */
export function listCoachSessions(
  ownerId: string,
  opts: { q?: string; includeArchived?: boolean } = {},
): { sessions: SessionOut[] } {
  const db = getDb();
  if (!db) return { schema_version: "coach_session_list.v1", sessions: [] };

  const statuses = opts.includeArchived ? ["active", "archived"] : ["active"];
  const placeholders = statuses.map(() => "?").join(",");
  const conditions = [
    "t.user_id=?",
    `t.status IN (${placeholders})`,
    "(t.kind <> 'primary' OR EXISTS (SELECT 1 FROM coach_messages pm WHERE pm.thread_id=t.id))",
  ];
  const params: unknown[] = [ownerId, ...statuses];
  if (opts.q && opts.q.trim()) {
    const needle = `%${opts.q.trim()}%`;
    conditions.push(
      "(COALESCE(t.title, '') LIKE ? OR EXISTS "
        + "(SELECT 1 FROM coach_messages qm WHERE qm.thread_id=t.id AND qm.content LIKE ?))",
    );
    params.push(needle, needle);
  }
  params.push(100); // limit

  const rows = db.prepare(
    `${SESSION_COLUMNS} WHERE ${conditions.join(" AND ")} GROUP BY t.id `
      + "ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
  ).all(...params) as SessionRow[];

  return { schema_version: "coach_session_list.v1", sessions: rows.map((r) => shapeSession(db, r)) };
}

/** GET /v1/primary — matches Python _build_coach_primary_response(). */
export function getCoachPrimary(
  ownerId: string,
  sessionId?: number,
): {
  thread: { id: number; user_id: string; kind: string; created_at: string; updated_at: string };
  messages: CoachMessageOut[];
  refs: AnalysisRefOut[];
} {
  const db = getDb();
  if (!db) {
    const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    return {
      thread: { id: 0, user_id: ownerId, kind: "primary", created_at: now, updated_at: now },
      messages: [],
      refs: [],
    };
  }

  let thread: { id: number; user_id: string; kind: string; created_at: string; updated_at: string } | undefined;
  if (sessionId !== undefined) {
    thread = db.prepare(
      "SELECT id, user_id, kind, created_at, updated_at FROM coach_threads "
        + "WHERE user_id=? AND id=? AND status <> 'deleted'",
    ).get(ownerId, sessionId) as typeof thread | undefined;
    if (!thread) throw new CoachDataError(404, "Coach session is unavailable");
  } else {
    thread = getPrimaryThread(db, ownerId);
  }

  if (!thread) {
    const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    return {
      thread: { id: 0, user_id: ownerId, kind: "primary", created_at: now, updated_at: now },
      messages: [],
      refs: [],
    };
  }

  return {
    thread,
    messages: loadMessages(db, thread.id),
    refs: listAnalysisRefs(db, thread.id),
  };
}

/** GET /v1/contexts — matches Python get_coach_contexts(). */
export function listCoachContexts(
  ownerId: string,
  sessionId?: number,
): { contexts: ContextRefOut[] } {
  const db = getDb();
  if (!db) return { schema_version: "coach_context_list.v1", contexts: [] };
  const threadId = threadIdForRequest(db, ownerId, sessionId);
  const rows = db.prepare(
    "SELECT context_ref, thread_id, kind, analysis_session_id, comparison_session_id, "
      + "target_ref, start_ms, end_ms, label, status, attached_at, detached_at, deleted_at "
      + "FROM coach_context_refs WHERE thread_id=? AND status='active' "
      + "ORDER BY attached_at, context_ref",
  ).all(threadId) as ContextRefRow[];
  return { schema_version: "coach_context_list.v1", contexts: rows.map(shapeContextRef) };
}

/** POST /v1/sessions — matches Python create_coach_session(). */
export function createCoachSession(ownerId: string, title?: string): SessionOut {
  const db = getDb();
  if (!db) throw new CoachDataError(503, "Database is unavailable");
  const normalizedTitle = title && title.trim() ? title.trim().slice(0, 120) : "新对话";
  const row = db.prepare(
    "INSERT INTO coach_threads(user_id, kind, title, status) "
      + "VALUES(?, 'conversation', ?, 'active') "
      + "RETURNING id, user_id, kind, title, status, deleted_at, created_at, updated_at",
  ).get(ownerId, normalizedTitle) as SessionRow;
  return shapeSession(db, row);
}

/** PATCH /v1/sessions/:id — matches Python update_coach_session(). */
export function updateCoachSession(
  ownerId: string,
  sessionId: number,
  update: { title?: string; status?: "archived" },
): SessionOut {
  const db = getDb();
  if (!db) throw new CoachDataError(503, "Database is unavailable");
  if (update.title === undefined && update.status === undefined) {
    throw new CoachDataError(400, "Coach session update is empty");
  }
  let updated = false;
  if (update.title !== undefined) {
    const title = update.title.trim();
    if (!title) throw new CoachDataError(400, "session title cannot be empty");
    const result = db.prepare(
      "UPDATE coach_threads SET title=?, updated_at=CURRENT_TIMESTAMP "
        + "WHERE id=? AND user_id=? AND status <> 'deleted'",
    ).run(title.slice(0, 120), sessionId, ownerId);
    if (result.changes !== 1) throw new CoachDataError(404, "Coach session is unavailable");
    updated = true;
  }
  if (update.status === "archived") {
    const result = db.prepare(
      "UPDATE coach_threads SET status='archived', updated_at=CURRENT_TIMESTAMP "
        + "WHERE id=? AND user_id=? AND status <> 'deleted'",
    ).run(sessionId, ownerId);
    if (result.changes !== 1 && !updated) {
      throw new CoachDataError(404, "Coach session is unavailable");
    }
  }
  const row = db.prepare(
    `${SESSION_COLUMNS} WHERE t.user_id=? AND t.id=? GROUP BY t.id`,
  ).get(ownerId, sessionId) as SessionRow | undefined;
  if (!row) throw new CoachDataError(404, "Coach session is unavailable");
  return shapeSession(db, row);
}

/** DELETE /v1/sessions/:id — matches Python delete_coach_session(). */
export function deleteCoachSession(ownerId: string, sessionId: number): SessionOut {
  const db = getDb();
  if (!db) throw new CoachDataError(503, "Database is unavailable");
  db.prepare(
    "UPDATE coach_threads SET status='deleted', deleted_at=CURRENT_TIMESTAMP, "
      + "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status <> 'deleted'",
  ).run(sessionId, ownerId);
  const row = db.prepare(
    `${SESSION_COLUMNS} WHERE t.user_id=? AND t.id=? GROUP BY t.id`,
  ).get(ownerId, sessionId) as SessionRow | undefined;
  if (!row) throw new CoachDataError(404, "Coach session is unavailable");
  return shapeSession(db, row);
}

// ---------------------------------------------------------------------------
// Context attach (simplified port of Python coach_context_refs.attach_context)
// ---------------------------------------------------------------------------

const _CONTEXT_KINDS = new Set([
  "analysis", "issue", "time_range", "metric", "evidence_segment", "comparison",
]);
const _ANALYSIS_REF_RE = /^analysis:([1-9][0-9]*)$/;
const _SAFE_TARGET_REF_RE = /^[A-Za-z][A-Za-z0-9_.:@-]{1,200}$/;

/**
 * Build a simplified diagnostic context projection from an analysis result.
 *
 * Simplified vs. Python project_coach_diagnostic_context: the Python version
 * runs an extensive allow-list filter over every field (hundreds of lines of
 * validation). This port extracts the key structural fields (schema_version,
 * analysis_ref, diagnosis, evidence) and passes them through without the
 * per-field allow-list scrubbing. The Coach may need to call analysis tools
 * for details that the full projection would have included inline.
 */
function buildSimplifiedProjection(
  sessionId: number,
  result: unknown,
): Record<string, unknown> {
  const r = isRecord(result) ? result : {};
  const schemaVersion = r.schema_version === "analysis_result.v2"
    ? "analysis_result.v2"
    : "analysis_result.v1";
  const deterministic = isRecord(r.deterministic) ? r.deterministic : {};
  const diagnosis = isRecord(deterministic.diagnosis) ? deterministic.diagnosis : {};
  return {
    schema_version: "coach_diagnostic_context.v1",
    analysis_ref: {
      analysis_id: `analysis:${sessionId}`,
      analysis_result_version: schemaVersion,
      analysis_type: typeof r.analysis_type === "string" ? r.analysis_type : null,
      input_mode: typeof r.input_mode === "string" ? r.input_mode : "unknown",
    },
    diagnosis: {
      profile: isRecord(diagnosis.profile) ? diagnosis.profile : {},
      issues: Array.isArray(diagnosis.issues) ? diagnosis.issues : [],
      summary: isRecord(diagnosis.summary)
        ? diagnosis.summary
        : isRecord(deterministic.metrics) ? deterministic.metrics : {},
      comparison: diagnosis.comparison ?? null,
      meta: isRecord(diagnosis.meta) ? diagnosis.meta : {},
    },
    evidence_summary: { availability: {}, alignment: {} },
    warnings: [],
  };
}

/** POST /v1/context/attach — simplified port of Python attach_context(). */
export function attachCoachContext(
  ownerId: string,
  context: {
    kind: string;
    analysis_ref: string;
    target_ref?: string;
    start_ms?: number;
    end_ms?: number;
    comparison_analysis_ref?: string;
  },
  sessionId?: number,
): { action: string; context: ContextRefOut } {
  const db = getDb();
  if (!db) throw new CoachDataError(503, "Database is unavailable");

  const { kind, analysis_ref } = context;
  const target_ref = context.target_ref;
  const start_ms = context.start_ms;
  const end_ms = context.end_ms;
  const comparison_analysis_ref = context.comparison_analysis_ref;

  if (!_CONTEXT_KINDS.has(kind)) {
    throw new CoachDataError(400, "invalid_kind");
  }

  const refMatch = analysis_ref.match(_ANALYSIS_REF_RE);
  if (!refMatch) {
    throw new CoachDataError(400, "invalid_analysis_ref");
  }
  const analysisSessionId = parseInt(refMatch[1], 10);

  // Validate ownership and status
  const session = db.prepare(
    "SELECT id, user_id, status, result FROM sessions WHERE id=?",
  ).get(analysisSessionId) as { id: number; user_id: string; status: string; result: string | null } | undefined;
  if (!session || session.user_id !== ownerId) {
    throw new CoachDataError(404, "not_found");
  }
  if (session.status !== "done") {
    throw new CoachDataError(409, "analysis_unavailable");
  }

  const threadId = threadIdForRequest(db, ownerId, sessionId);
  const projection = buildSimplifiedProjection(analysisSessionId, parseJson(session.result));

  // Validate time_range
  let validatedStartMs: number | null = null;
  let validatedEndMs: number | null = null;
  if (kind === "time_range") {
    if (typeof start_ms !== "number") {
      throw new CoachDataError(400, "invalid_time_range");
    }
    validatedStartMs = start_ms;
    validatedEndMs = typeof end_ms === "number" ? end_ms : start_ms;
    if (validatedStartMs < 0 || validatedEndMs < validatedStartMs) {
      throw new CoachDataError(400, "invalid_time_range");
    }
  } else if (start_ms !== undefined && start_ms !== null) {
    throw new CoachDataError(400, "invalid_time_range");
  }

  // Validate comparison
  let comparisonSessionId: number | null = null;
  let comparisonProjectionJson: string | null = null;
  if (kind === "comparison") {
    const compMatch = comparison_analysis_ref?.match(_ANALYSIS_REF_RE);
    if (!compMatch) {
      throw new CoachDataError(400, "invalid_comparison_ref");
    }
    comparisonSessionId = parseInt(compMatch[1], 10);
    const compSession = db.prepare(
      "SELECT id, user_id, status, result FROM sessions WHERE id=?",
    ).get(comparisonSessionId) as { id: number; user_id: string; status: string; result: string | null } | undefined;
    if (!compSession || compSession.user_id !== ownerId) {
      throw new CoachDataError(404, "not_found");
    }
    if (compSession.status !== "done") {
      throw new CoachDataError(409, "analysis_unavailable");
    }
    comparisonProjectionJson = JSON.stringify(
      buildSimplifiedProjection(comparisonSessionId, parseJson(compSession.result)),
    );
  } else if (comparison_analysis_ref) {
    throw new CoachDataError(400, "invalid_comparison_ref");
  }

  // Validate target_ref (simplified — skips issue/metric/evidence_segment membership checks)
  let validatedTargetRef: string | null = null;
  if (kind === "analysis" || kind === "comparison") {
    if (target_ref) {
      throw new CoachDataError(400, "invalid_target_ref");
    }
    validatedTargetRef = analysis_ref;
  } else {
    if (!target_ref || !_SAFE_TARGET_REF_RE.test(target_ref)) {
      throw new CoachDataError(400, "invalid_target_ref");
    }
    validatedTargetRef = target_ref;
  }

  // Compute dedupe_key (keys in alphabetical order to match Python sort_keys=True)
  const dedupeKey = createHash("sha256").update(
    JSON.stringify({
      analysis_ref,
      comparison_analysis_ref: comparison_analysis_ref ?? undefined,
      end_ms: validatedEndMs,
      kind,
      start_ms: validatedStartMs,
      target_ref: validatedTargetRef,
    }),
  ).digest("hex");
  const contextRef = `context:${dedupeKey.slice(0, 24)}`;

  // Check for existing active context
  const existing = db.prepare(
    "SELECT context_ref, thread_id, kind, analysis_session_id, comparison_session_id, "
      + "target_ref, start_ms, end_ms, label, status, attached_at, detached_at, deleted_at "
      + "FROM coach_context_refs WHERE thread_id=? AND dedupe_key=?",
  ).get(threadId, dedupeKey) as ContextRefRow | undefined;
  if (existing && existing.status === "active") {
    return { action: "already_attached", context: shapeContextRef(existing) };
  }

  const projectionJson = JSON.stringify(projection);

  db.prepare(
    "INSERT INTO coach_context_refs(context_ref, thread_id, dedupe_key, kind, "
      + "analysis_session_id, comparison_session_id, target_ref, start_ms, end_ms, label, "
      + "projection_json, comparison_projection_json, status) "
      + "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') "
      + "ON CONFLICT(thread_id, dedupe_key) DO UPDATE SET status='active', "
      + "projection_json=excluded.projection_json, "
      + "comparison_projection_json=excluded.comparison_projection_json, "
      + "label=excluded.label, detached_at=NULL, "
      + "deleted_at=NULL, attached_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
  ).run(
    contextRef, threadId, dedupeKey, kind, analysisSessionId, comparisonSessionId,
    validatedTargetRef, validatedStartMs, validatedEndMs, null,
    projectionJson, comparisonProjectionJson,
  );

  // Ensure the analysis session is tracked in coach_analysis_refs
  db.prepare(
    "INSERT OR IGNORE INTO coach_analysis_refs(thread_id, analysis_session_id, status) "
      + "VALUES(?, ?, 'active')",
  ).run(threadId, analysisSessionId);

  const row = db.prepare(
    "SELECT context_ref, thread_id, kind, analysis_session_id, comparison_session_id, "
      + "target_ref, start_ms, end_ms, label, status, attached_at, detached_at, deleted_at "
      + "FROM coach_context_refs WHERE thread_id=? AND dedupe_key=?",
  ).get(threadId, dedupeKey) as ContextRefRow;

  return { schema_version: "coach_context_mutation.v1", action: "attached", context: shapeContextRef(row) };
}

/** POST /v1/contexts/:ref/detach — matches Python detach_coach_context(). */
export function detachCoachContext(
  ownerId: string,
  contextRef: string,
  sessionId?: number,
): { action: string; context: ContextRefOut } | null {
  const db = getDb();
  if (!db) throw new CoachDataError(503, "Database is unavailable");
  const threadId = threadIdForRequest(db, ownerId, sessionId);

  const row = db.prepare(
    "SELECT c.* FROM coach_context_refs c JOIN coach_threads t ON t.id=c.thread_id "
      + "WHERE c.context_ref=? AND c.thread_id=? AND t.user_id=?",
  ).get(contextRef, threadId, ownerId) as ContextRefRow | undefined;
  if (!row) return null;

  if (row.status !== "active") {
    return { action: "already_detached", context: shapeContextRef(row) };
  }

  db.prepare(
    "UPDATE coach_context_refs SET status='detached', detached_at=CURRENT_TIMESTAMP, "
      + "updated_at=CURRENT_TIMESTAMP WHERE context_ref=?",
  ).run(contextRef);

  const updated = db.prepare(
    "SELECT context_ref, thread_id, kind, analysis_session_id, comparison_session_id, "
      + "target_ref, start_ms, end_ms, label, status, attached_at, detached_at, deleted_at "
      + "FROM coach_context_refs WHERE context_ref=?",
  ).get(contextRef) as ContextRefRow;
  return { schema_version: "coach_context_mutation.v1", action: "detached", context: shapeContextRef(updated) };
}
