/**
 * Native Coach agent run lifecycle.
 *
 * Ports the essential create/start/stop/retry/poll lifecycle from Python's
 * coach_agent_runs.py + coach_service.py into the Node sidecar, eliminating
 * the Python → Node HTTP round-trip for LLM turns.
 *
 * Simplified vs. Python:
 *   - Teaching session reconciliation is not ported; teaching_turn is null.
 *   - Analysis soft-start and guidance compilation are not implemented.
 *   - Context enrichment (analysis briefs from evidence artifacts) is not ported.
 *   - Provider recovery / resume_waiting_runs is not implemented.
 *   - Confirmation execution calls executeNativeWrite for coach_side_effect
 *     confirmations; the full audit reconciliation is simplified.
 *
 * These are tracked as TODOs for a future tier.
 */
import { randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";

import type { SqliteDb } from "./db.ts";
import { getDb } from "./db.ts";
import { isRecord } from "./contracts.ts";
import type {
  CoachRuntimeProviderProfile,
  CoachRuntimeMessage,
  CoachRuntimeToolEvent,
} from "./contracts.ts";
import { extractRuntimeSecrets, redactRuntimeSecrets } from "./provider-profile.ts";
import { runCoachTurn, stopCoachTurn } from "./turn.ts";
import { startTask, stopTask, waitForTask, isTaskActive } from "./task-manager.ts";
import { executeNativeWrite } from "./product-commands-write.ts";

// ── Types ─────────────────────────────────────────────────────────────

type AnyDict = Record<string, any>;

export type AgentRunState = {
  schema_version: "coach_agent_run.v1";
  run_ref: string;
  session_id: number;
  parent_run_ref: string | null;
  attempt: number;
  status: "queued" | "running" | "succeeded" | "failed" | "stopped";
  phase: "queued" | "text_generation" | "tool_execution" | "completed";
  partial_text: string | null;
  error: AnyDict | null;
  contexts: AnyDict[];
  events: AgentRunEvent[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type AgentRunEvent = {
  schema_version: "coach_agent_run_event.v1";
  event_ref: string;
  sequence: number;
  type: string;
  phase: string;
  code: string;
  message: string;
  payload: AnyDict | null;
  created_at: string;
};

export class AgentRunError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "AgentRunError";
    this.code = code;
  }
}

// ── DB helpers ────────────────────────────────────────────────────────

function parseJson(value: unknown): AnyDict | null {
  if (typeof value !== "string" || !value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function parseJsonArray(value: unknown): AnyDict[] {
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function sqliteTimestampToWireUtc(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.includes("T") ? value : value.replace(" ", "T") + "Z";
}

// ── Event helpers ─────────────────────────────────────────────────────

function appendEvent(
  db: SqliteDb,
  runRef: string,
  eventType: string,
  phase: string,
  code: string,
  message: string,
  payload?: AnyDict | null,
): void {
  const row = db.prepare(
    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM coach_agent_run_events WHERE run_ref=?",
  ).get(runRef) as { next_sequence: number };
  const sequence = row.next_sequence;
  const payloadJson = payload
    ? JSON.stringify(payload, null, 0)
    : null;
  db.prepare(
    "INSERT INTO coach_agent_run_events(event_ref, run_ref, sequence, event_type, phase, code, message, payload_json) " +
    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(
    `agent_event:${randomUUID().replace(/-/g, "")}`,
    runRef,
    sequence,
    eventType,
    phase,
    code,
    message,
    payloadJson,
  );
}

function loadEvents(db: SqliteDb, runRef: string): AgentRunEvent[] {
  const rows = db.prepare(
    "SELECT event_ref, sequence, event_type, phase, code, message, payload_json, created_at " +
    "FROM coach_agent_run_events WHERE run_ref=? ORDER BY sequence",
  ).all(runRef) as AnyDict[];
  return rows.map((row) => ({
    schema_version: "coach_agent_run_event.v1",
    event_ref: row.event_ref,
    sequence: row.sequence,
    type: row.event_type,
    phase: row.phase,
    code: row.code,
    message: row.message,
    payload: parseJson(row.payload_json),
    created_at: row.created_at,
  }));
}

// ── Run state helpers ─────────────────────────────────────────────────

function setRun(
  db: SqliteDb,
  runRef: string,
  status: string,
  phase: string,
  options: {
    partialText?: string | null;
    error?: AnyDict | null;
    started?: boolean;
    finished?: boolean;
  } = {},
): boolean {
  const guard = options.finished
    ? " AND status IN ('queued', 'running') AND stop_requested=0"
    : "";
  const errorJson = options.error
    ? JSON.stringify(options.error, null, 0)
    : null;
  const cursor = db.prepare(
    "UPDATE coach_agent_runs SET status=?, phase=?, partial_text=?, error_json=?, " +
    "started_at=CASE WHEN ? THEN COALESCE(started_at, CURRENT_TIMESTAMP) ELSE started_at END, " +
    "finished_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at END, " +
    "updated_at=CURRENT_TIMESTAMP WHERE run_ref=?" + guard,
  ).run(
    status,
    phase,
    options.partialText ?? null,
    errorJson,
    options.started ? 1 : 0,
    options.finished ? 1 : 0,
    runRef,
  );
  return cursor.changes === 1;
}

function isStopRequested(db: SqliteDb, runRef: string): boolean {
  const row = db.prepare(
    "SELECT stop_requested FROM coach_agent_runs WHERE run_ref=?",
  ).get(runRef) as { stop_requested: number } | undefined;
  return row !== undefined && row.stop_requested === 1;
}

function markStopped(db: SqliteDb, runRef: string, partialText?: string | null): boolean {
  const cursor = db.prepare(
    "UPDATE coach_agent_runs SET status='stopped', phase='completed', " +
    "partial_text=COALESCE(?, partial_text), error_json=NULL, " +
    "finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP " +
    "WHERE run_ref=? AND stop_requested=1 AND status IN ('queued', 'running')",
  ).run(partialText ?? null, runRef);
  if (cursor.changes !== 1) return false;
  appendEvent(db, runRef, "status", "completed", "run_stopped", "Coach run stopped by the user");
  return true;
}

// ── Provider profile loading ──────────────────────────────────────────

/**
 * Load the default provider profile + credential from SQLite, producing
 * a CoachRuntimeProviderProfile suitable for runCoachTurn.
 *
 * Returns null if no default profile exists or the credential is missing.
 */
export function loadDefaultProviderProfile(db: SqliteDb, ownerId: string): {
  profile: CoachRuntimeProviderProfile;
  needsReauth: boolean;
} | null {
  const profileRow = db.prepare(
    "SELECT id, provider_id, name, kind, base_url, model_id, context_window, max_tokens " +
    "FROM provider_profiles WHERE owner_id=? AND is_default=1 LIMIT 1",
  ).get(ownerId) as AnyDict | undefined;
  if (!profileRow) return null;

  const credRow = db.prepare(
    "SELECT credential_json, needs_reauth FROM provider_credentials WHERE profile_id=? AND owner_id=?",
  ).get(profileRow.id, ownerId) as { credential_json: string; needs_reauth: number } | undefined;

  const credential = credRow ? parseJson(credRow.credential_json) : null;
  const needsReauth = credRow ? credRow.needs_reauth === 1 : false;

  if (profileRow.kind === "builtin") {
    const profile: CoachRuntimeProviderProfile = {
      kind: "builtin",
      provider_id: profileRow.provider_id,
      model_id: profileRow.model_id,
      ...(credential && typeof credential.type === "string" ? { credential } : {}),
    };
    return { profile, needsReauth };
  }

  // Custom providers require credential + base_url + context_window + max_tokens
  if (!profileRow.base_url || !credential) return null;
  const profile: CoachRuntimeProviderProfile = {
    kind: profileRow.kind,
    provider_id: profileRow.provider_id,
    provider_name: profileRow.name,
    base_url: profileRow.base_url,
    model_id: profileRow.model_id,
    credential,
    ...(typeof profileRow.context_window === "number" ? { context_window: profileRow.context_window } : {}),
    ...(typeof profileRow.max_tokens === "number" ? { max_tokens: profileRow.max_tokens } : {}),
  };
  return { profile, needsReauth };
}

// ── Thread resolution ─────────────────────────────────────────────────

function resolveThreadId(db: SqliteDb, ownerId: string, sessionId?: number): number {
  if (sessionId !== undefined && Number.isInteger(sessionId) && sessionId > 0) {
    const row = db.prepare(
      "SELECT id FROM coach_threads WHERE id=? AND user_id=? AND status='active'",
    ).get(sessionId, ownerId) as { id: number } | undefined;
    if (row) return row.id;
    throw new AgentRunError("session_unavailable", "Coach session is unavailable");
  }
  // Look for the primary thread
  const row = db.prepare(
    "SELECT id FROM coach_threads WHERE user_id=? AND kind='primary' AND status='active' ORDER BY id LIMIT 1",
  ).get(ownerId) as { id: number } | undefined;
  if (row) return row.id;
  // Create one if none exists
  const result = db.prepare(
    "INSERT INTO coach_threads(user_id, kind, status) VALUES(?, 'primary', 'active') RETURNING id",
  ).get(ownerId) as { id: number };
  return result.id;
}

// ── Context bundle building ───────────────────────────────────────────

/**
 * Build a context bundle (analysis_summary) from active context_refs.
 *
 * Simplified port of Python's build_context_bundle. Reads projections
 * from coach_context_refs and wraps them in a coach_turn_context.v1 bundle.
 *
 * TODO: benchmark_summary, analysis_brief enrichment from evidence artifacts.
 */
function buildContextBundle(
  db: SqliteDb,
  threadId: number,
  requestedRefs: string[] | null,
): { bundle: AnyDict; snapshots: AnyDict[] } {
  const rows = db.prepare(
    "SELECT context_ref, kind, analysis_session_id, comparison_session_id, target_ref, " +
    "start_ms, end_ms, projection_json, comparison_projection_json " +
    "FROM coach_context_refs WHERE thread_id=? AND status='active' " +
    "ORDER BY attached_at, context_ref",
  ).all(threadId) as AnyDict[];

  const available = new Map<string, AnyDict>();
  for (const row of rows) {
    available.set(row.context_ref, row);
  }

  const refs = requestedRefs ?? [...available.keys()];
  if (refs.length > 8) {
    throw new AgentRunError("invalid_context_refs", "Context refs must be unique and bounded");
  }

  const contexts: AnyDict[] = [];
  const snapshots: AnyDict[] = [];

  for (const ref of refs) {
    const row = available.get(ref);
    if (!row) {
      throw new AgentRunError("context_unavailable", "One or more contexts are unavailable");
    }
    const projection = parseJson(row.projection_json);
    if (!projection) {
      throw new AgentRunError("context_unavailable", "Context projection is unavailable");
    }
    let comparisonProjection = null;
    if (row.kind === "comparison") {
      comparisonProjection = parseJson(row.comparison_projection_json);
    }
    snapshots.push({
      context_ref: ref,
      kind: row.kind,
      analysis_ref: `analysis:${row.analysis_session_id}`,
      comparison_analysis_ref: row.comparison_session_id ? `analysis:${row.comparison_session_id}` : null,
      status: "active",
    });
    contexts.push({
      context_ref: ref,
      kind: row.kind,
      analysis_ref: `analysis:${row.analysis_session_id}`,
      comparison_analysis_ref: row.comparison_session_id ? `analysis:${row.comparison_session_id}` : null,
      target_ref: row.target_ref ?? null,
      time_range_ms: row.start_ms != null ? [row.start_ms, row.end_ms] : null,
      projection,
      comparison_projection: comparisonProjection,
    });
  }

  return {
    bundle: {
      schema_version: "coach_turn_context.v1",
      contexts,
      benchmark_summary: null, // TODO: port benchmark_summary projection
    },
    snapshots,
  };
}

// ── Message helpers ───────────────────────────────────────────────────

function loadMessages(db: SqliteDb, threadId: number): CoachRuntimeMessage[] {
  const rows = db.prepare(
    "SELECT id, role, content FROM coach_messages WHERE thread_id=? ORDER BY id",
  ).all(threadId) as { id: number; role: string; content: string }[];
  return rows
    .filter((r) => r.role === "user" || r.role === "assistant")
    .map((r) => ({ role: r.role as "user" | "assistant", content: r.content }));
}

function appendUserMessage(
  db: SqliteDb,
  threadId: number,
  content: string,
  contextRefs?: AnyDict[],
): number {
  const contextRefsJson = contextRefs
    ? JSON.stringify(contextRefs, null, 0)
    : "[]";
  const row = db.prepare(
    "INSERT INTO coach_messages(thread_id, role, content, context_refs_json) " +
    "VALUES(?, 'user', ?, ?) RETURNING id",
  ).get(threadId, content, contextRefsJson) as { id: number };
  db.prepare("UPDATE coach_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?").run(threadId);
  return row.id;
}

function appendAssistantMessage(
  db: SqliteDb,
  threadId: number,
  content: string,
  trace: CoachRuntimeToolEvent[],
): void {
  const traceJson = trace.length > 0 ? JSON.stringify(trace, null, 0) : null;
  db.prepare(
    "INSERT INTO coach_messages(thread_id, role, content, trace_json) VALUES(?, 'assistant', ?, ?)",
  ).run(threadId, content, traceJson);
  db.prepare("UPDATE coach_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?").run(threadId);
}

// ── Async turn execution ──────────────────────────────────────────────

/**
 * Run a single Coach turn asynchronously, persisting the result to DB.
 *
 * This is the core background task that:
 * 1. Sets status to running
 * 2. Loads prior messages and appends the user message
 * 3. Loads the provider profile
 * 4. Calls runCoachTurn()
 * 5. Persists the assistant message and updates run status
 */
async function runAgentTurn(
  db: SqliteDb,
  runRef: string,
  ownerId: string,
  threadId: number,
  content: string,
  bundle: AnyDict,
  snapshots: AnyDict[],
  signal: AbortSignal,
): Promise<void> {
  const analysisSummary = JSON.stringify(bundle, null, 0);
  const persistenceStart = performance.now();

  try {
    // Phase 1: text generation
    setRun(db, runRef, "running", "text_generation", { started: true });
    appendEvent(db, runRef, "phase", "text_generation", "text_generation_started", "Coach is generating a response");

    if (isStopRequested(db, runRef)) {
      markStopped(db, runRef);
      return;
    }

    // Load prior messages and append user message
    const priorMessages = loadMessages(db, threadId);
    const userMessageId = appendUserMessage(db, threadId, content, snapshots);
    db.prepare(
      "UPDATE coach_agent_runs SET user_message_id=? WHERE run_ref=? AND user_message_id IS NULL",
    ).run(userMessageId, runRef);

    if (isStopRequested(db, runRef)) {
      markStopped(db, runRef);
      return;
    }

    // Load provider profile
    const providerResult = loadDefaultProviderProfile(db, ownerId);
    if (!providerResult) {
      const failure = {
        domain: "permission",
        code: "provider_unconfigured",
        message: "Coach Provider is not configured",
        retryable: true,
      };
      setRun(db, runRef, "failed", "completed", { error: failure, finished: true });
      appendEvent(db, runRef, "error", "completed", failure.code, failure.message);
      return;
    }

    if (providerResult.needsReauth) {
      const failure = {
        domain: "permission",
        code: "provider_reauthentication_required",
        message: "Provider credential requires reauthentication",
        retryable: true,
      };
      setRun(db, runRef, "failed", "completed", { error: failure, finished: true });
      appendEvent(db, runRef, "error", "completed", failure.code, failure.message);
      return;
    }

    // Build the turn request
    const turnRequest = {
      schema_version: "coach_runtime_turn.v1",
      run_id: runRef,
      session_id: `coach-thread:${threadId}`,
      user_id: ownerId,
      messages: [...priorMessages, { role: "user" as const, content }],
      analysis_summary: analysisSummary,
      model: providerResult.profile,
      // tool_bridge is null — native DB access handles product commands
      // teaching_turn is null — not ported in this tier
    };

    const secrets = extractRuntimeSecrets(turnRequest);

    // Execute the turn
    const response = await runCoachTurn(turnRequest, {
      onPartial: async (partial) => {
        if (signal.aborted) return;
        const safeText = partial.text.slice(0, 12_000);
        db.prepare(
          "UPDATE coach_agent_runs SET partial_text=?, updated_at=CURRENT_TIMESTAMP " +
          "WHERE run_ref=? AND status='running' AND stop_requested=0",
        ).run(safeText, runRef);
      },
      onActivity: async (activity) => {
        if (signal.aborted) return;
        if (activity.kind === "tool" && activity.state === "started") {
          db.prepare(
            "UPDATE coach_agent_runs SET phase='tool_execution', updated_at=CURRENT_TIMESTAMP " +
            "WHERE run_ref=? AND status IN ('queued', 'running')",
          ).run(runRef);
        } else if (activity.kind === "tool" && activity.state !== "started") {
          db.prepare(
            "UPDATE coach_agent_runs SET phase='text_generation', updated_at=CURRENT_TIMESTAMP " +
            "WHERE run_ref=? AND status IN ('queued', 'running')",
          ).run(runRef);
        }
        const payload: AnyDict = {};
        for (const key of ["sequence", "kind", "state", "tool_call_id", "tool_name", "command_name"] as const) {
          if (activity[key] !== undefined) (payload as AnyDict)[key] = activity[key];
        }
        appendEvent(
          db,
          runRef,
          activity.kind === "tool" ? "tool" : "phase",
          activity.kind === "tool" && activity.state === "started" ? "tool_execution" : "text_generation",
          `${activity.kind}_${activity.state}`,
          "Coach activity update",
          payload,
        );
      },
    });

    if (signal.aborted || isStopRequested(db, runRef)) {
      const partialReply = response.partial_reply;
      markStopped(db, runRef, partialReply);
      return;
    }

    // Persist tool events
    const toolEvents = response.tool_events ?? [];
    for (const event of toolEvents) {
      appendEvent(
        db,
        runRef,
        event.type === "product_command" && isRecord(event) && (event as AnyDict).status === "needs_confirmation"
          ? "confirmation"
          : "tool",
        "tool_execution",
        String((event as AnyDict).status ?? "tool_event"),
        "Coach product tool event",
        event as AnyDict,
      );
    }

    // Determine final status
    if (response.ok) {
      const reply = response.reply ?? "(本次未能生成回复,见 notes)";
      const redactedReply = redactRuntimeSecrets(reply, secrets);
      // Persist assistant message
      appendAssistantMessage(db, threadId, redactedReply, toolEvents);
      appendEvent(db, runRef, "text", "text_generation", "text_available", "Coach response text is available");

      const completed = setRun(db, runRef, "succeeded", "completed", {
        partialText: redactedReply,
        finished: true,
      });
      if (completed) {
        const timing: AnyDict = { persistence_ms: Math.max(0, Math.round(performance.now() - persistenceStart)) };
        appendEvent(db, runRef, "phase", "completed", "latency_trace", "Coach latency trace", timing);
        appendEvent(db, runRef, "status", "completed", "run_succeeded", "Coach run completed");
      } else {
        markStopped(db, runRef, redactedReply);
      }
    } else {
      // Turn failed
      const error = response.error;
      const failure: AnyDict = error
        ? {
            domain: error.domain in {"network": 1, "model": 1, "permission": 1, "tool": 1} ? error.domain : "model",
            code: error.code,
            message: error.message,
            retryable: error.retryable,
          }
        : {
            domain: "model",
            code: "generation_failed",
            message: "Coach generation failed",
            retryable: true,
          };

      // Provider-waiting codes keep the run queued
      if (failure.code === "provider_unconfigured" || failure.code === "provider_reauthentication_required") {
        setRun(db, runRef, "queued", "queued", { partialText: null, error: failure });
        appendEvent(db, runRef, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
        return;
      }

      const completed = setRun(db, runRef, "failed", "completed", {
        partialText: response.partial_reply,
        error: failure,
        finished: true,
      });
      if (completed) {
        appendEvent(db, runRef, "error", "completed", failure.code, failure.message);
      } else {
        markStopped(db, runRef, response.partial_reply);
      }
    }
  } catch (error) {
    // Unexpected error — mark as failed
    const failure = {
      domain: "model",
      code: "generation_failed",
      message: "Coach generation failed",
      retryable: true,
    };
    const completed = setRun(db, runRef, "failed", "completed", { error: failure, finished: true });
    if (completed) {
      appendEvent(db, runRef, "error", "completed", failure.code, failure.message);
    } else {
      markStopped(db, runRef);
    }
  }
}

// ── Public lifecycle functions ────────────────────────────────────────

/**
 * Create a new agent run and start the turn asynchronously.
 * Returns the initial run state (status will be 'queued' or 'running').
 */
export function createAgentRun(
  db: SqliteDb,
  ownerId: string,
  content: string,
  options: {
    contextRefs?: string[] | null;
    sessionId?: number;
  } = {},
): AgentRunState {
  if (!content || !content.trim()) {
    throw new AgentRunError("invalid_text", "Coach text is invalid");
  }
  const safeContent = content.trim().slice(0, 12_000);

  const threadId = resolveThreadId(db, ownerId, options.sessionId);
  const { bundle, snapshots } = buildContextBundle(
    db,
    threadId,
    options.contextRefs ?? null,
  );

  const runRef = `agent_run:${randomUUID().replace(/-/g, "")}`;
  const snapshotsJson = JSON.stringify(snapshots, null, 0);

  db.prepare(
    "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content, " +
    "user_message_id, context_refs_json) VALUES(?, ?, ?, 1, 'queued', 'queued', ?, NULL, ?)",
  ).run(runRef, ownerId, threadId, safeContent, snapshotsJson);

  appendEvent(db, runRef, "status", "queued", "run_queued", "Coach run queued");

  // Start the async turn
  startTask(runRef, (signal) => runAgentTurn(db, runRef, ownerId, threadId, safeContent, bundle, snapshots, signal));

  return getAgentRun(db, ownerId, runRef)!;
}

/**
 * Read the current state of an agent run.
 */
export function getAgentRun(db: SqliteDb, ownerId: string, runRef: string): AgentRunState | null {
  const row = db.prepare(
    "SELECT * FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
  ).get(runRef, ownerId) as AnyDict | undefined;
  if (!row) return null;

  const contexts = parseJsonArray(row.context_refs_json);
  const error = parseJson(row.error_json);
  const events = loadEvents(db, runRef);

  return {
    schema_version: "coach_agent_run.v1",
    run_ref: row.run_ref,
    session_id: row.thread_id,
    parent_run_ref: row.parent_run_ref ?? null,
    attempt: row.attempt,
    status: row.status,
    phase: row.phase,
    partial_text: row.partial_text ?? null,
    error,
    contexts,
    events,
    created_at: row.created_at,
    started_at: sqliteTimestampToWireUtc(row.started_at),
    finished_at: sqliteTimestampToWireUtc(row.finished_at),
  };
}

/**
 * Stop a running agent run.
 * Sets stop_requested=1, calls stopCoachTurn, and waits for graceful shutdown.
 */
export function stopAgentRun(db: SqliteDb, ownerId: string, runRef: string): AgentRunState | null {
  const current = getAgentRun(db, ownerId, runRef);
  if (!current) return null;
  if (["succeeded", "failed", "stopped"].includes(current.status)) return current;

  db.prepare(
    "UPDATE coach_agent_runs SET stop_requested=1, updated_at=CURRENT_TIMESTAMP " +
    "WHERE run_ref=? AND owner_id=? AND status IN ('queued', 'running')",
  ).run(runRef, ownerId);

  // Signal the turn to stop
  stopCoachTurn(runRef);
  stopTask(runRef);

  // Wait briefly for graceful shutdown
  if (isTaskActive(runRef)) {
    // Can't use async in this synchronous context; mark stopped directly
    if (!isStopRequested(db, runRef) || current.status === "queued") {
      markStopped(db, runRef, current.partial_text);
    }
  }

  return getAgentRun(db, ownerId, runRef);
}

/**
 * Retry a failed agent run by creating a child run with attempt+1.
 */
export function retryAgentRun(
  db: SqliteDb,
  ownerId: string,
  runRef: string,
): AgentRunState | null {
  const detail = getAgentRun(db, ownerId, runRef);
  if (!detail) return null;
  if (detail.status !== "failed" || !detail.error?.retryable) {
    throw new AgentRunError("retry_not_allowed", "Coach run is not retryable");
  }

  const row = db.prepare(
    "SELECT content, context_refs_json, thread_id, attempt FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
  ).get(runRef, ownerId) as AnyDict;

  const snapshots = parseJsonArray(row.context_refs_json);
  const refs = snapshots.map((s) => s.context_ref).filter((r: unknown) => typeof r === "string");
  const threadId = row.thread_id;
  const { bundle } = buildContextBundle(db, threadId, refs);

  const newRunRef = `agent_run:${randomUUID().replace(/-/g, "")}`;
  const attempt = (detail.attempt ?? 1) + 1;

  // Check for existing retry with same parent+attempt
  const existing = db.prepare(
    "SELECT run_ref FROM coach_agent_runs WHERE owner_id=? AND parent_run_ref=? AND attempt=? ORDER BY created_at, run_ref LIMIT 1",
  ).get(ownerId, runRef, attempt) as { run_ref: string } | undefined;
  if (existing) {
    return getAgentRun(db, ownerId, existing.run_ref);
  }

  db.prepare(
    "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, parent_run_ref, attempt, status, phase, content, " +
    "user_message_id, context_refs_json) VALUES(?, ?, ?, ?, ?, 'queued', 'queued', ?, NULL, ?)",
  ).run(newRunRef, ownerId, threadId, runRef, attempt, row.content, JSON.stringify(snapshots, null, 0));

  appendEvent(db, newRunRef, "status", "queued", "run_queued", "Coach run queued");

  startTask(newRunRef, (signal) =>
    runAgentTurn(db, newRunRef, ownerId, threadId, row.content, bundle, snapshots, signal),
  );

  return getAgentRun(db, ownerId, newRunRef);
}

// ── Confirmation handling ─────────────────────────────────────────────

/**
 * Decide a pending confirmation (confirm or reject).
 *
 * For coach_side_effect confirmations, executes the underlying product
 * command natively via executeNativeWrite.
 */
export function decideConfirmation(
  db: SqliteDb,
  ownerId: string,
  confirmationRef: string,
  decision: "confirm" | "reject",
): AnyDict | null {
  if (decision !== "confirm" && decision !== "reject") {
    throw new AgentRunError("invalid_decision", "Decision must be confirm or reject");
  }

  const request = db.prepare(
    "SELECT confirmation_ref, action, target_ref, status, impact_code, impact_message, created_at, decided_at " +
    "FROM coach_confirmation_requests WHERE confirmation_ref=? AND owner_id=?",
  ).get(confirmationRef, ownerId) as AnyDict | undefined;
  if (!request) return null;

  // Check for existing audit
  const existingAudit = db.prepare(
    "SELECT audit_ref, decision, result_status, audit_state, execution_result_json " +
    "FROM coach_confirmation_audits WHERE confirmation_ref=? AND owner_id=?",
  ).get(confirmationRef, ownerId) as AnyDict | undefined;

  if (existingAudit) {
    const execution = parseJson(existingAudit.execution_result_json);
    return formatConfirmation(request, existingAudit.audit_ref, execution, existingAudit.audit_state);
  }

  // Create pending audit
  const auditRef = `confirmation_audit:${randomUUID().replace(/-/g, "")}`;
  const resultStatus = decision === "confirm" ? "confirmed" : "rejected";

  // Atomically update status and create audit
  const cursor = db.prepare(
    "UPDATE coach_confirmation_requests SET status=?, decided_at=CURRENT_TIMESTAMP " +
    "WHERE confirmation_ref=? AND owner_id=? AND status='pending'",
  ).run(resultStatus, confirmationRef, ownerId);
  if (cursor.changes !== 1) {
    // Status changed concurrently — re-read
    const refreshed = getAgentRun(db, ownerId, confirmationRef);
    return refreshed ? formatConfirmation(request, null, null, null) : null;
  }

  db.prepare(
    "INSERT INTO coach_confirmation_audits(audit_ref, confirmation_ref, owner_id, decision, result_status, audit_state) " +
    "VALUES(?, ?, ?, ?, ?, 'pending')",
  ).run(auditRef, confirmationRef, ownerId, decision, resultStatus);

  // Execute the confirmed command if needed
  let execution: AnyDict | null = null;
  if (request.action === "coach_side_effect" && decision === "confirm") {
    execution = executeConfirmedCommand(db, ownerId, confirmationRef);
  }

  // Complete the audit
  db.prepare(
    "UPDATE coach_confirmation_audits SET audit_state='completed', execution_result_json=? " +
    "WHERE confirmation_ref=? AND owner_id=? AND audit_state='pending'",
  ).run(execution ? JSON.stringify(execution, null, 0) : null, confirmationRef, ownerId);

  // Read the final state
  const finalRequest = db.prepare(
    "SELECT confirmation_ref, action, target_ref, status, impact_code, impact_message, created_at, decided_at " +
    "FROM coach_confirmation_requests WHERE confirmation_ref=? AND owner_id=?",
  ).get(confirmationRef, ownerId) as AnyDict;

  return formatConfirmation(finalRequest, auditRef, execution, "completed");
}

function executeConfirmedCommand(db: SqliteDb, ownerId: string, confirmationRef: string): AnyDict | null {
  const row = db.prepare(
    "SELECT command_name, parameters_json, idempotency_key, thread_id " +
    "FROM coach_command_confirmations WHERE confirmation_ref=? AND owner_id=?",
  ).get(confirmationRef, ownerId) as AnyDict | undefined;
  if (!row || !row.parameters_json || !row.idempotency_key) {
    return null;
  }

  const parameters = parseJson(row.parameters_json);
  if (!parameters) return null;

  const result = executeNativeWrite(
    db,
    row.command_name,
    parameters,
    ownerId,
    row.idempotency_key,
  );

  // Update confirmation status to consumed
  db.prepare(
    "UPDATE coach_command_confirmations SET status='consumed', consumed_at=CURRENT_TIMESTAMP " +
    "WHERE confirmation_ref=? AND owner_id=?",
  ).run(confirmationRef, ownerId);

  return result as AnyDict;
}

function formatConfirmation(
  request: AnyDict,
  auditRef: string | null,
  execution: AnyDict | null,
  auditState: string | null,
): AnyDict {
  return {
    schema_version: "coach_confirmation.v1",
    confirmation_ref: request.confirmation_ref,
    action: request.action,
    target_ref: request.target_ref,
    status: request.status,
    impact: {
      code: request.impact_code,
      message: request.impact_message,
    },
    audit_ref: auditRef,
    audit_state: auditState,
    execution,
    created_at: request.created_at,
    decided_at: request.decided_at,
  };
}
