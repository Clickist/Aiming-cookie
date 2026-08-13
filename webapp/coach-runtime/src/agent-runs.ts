/**
 * Native Coach agent run lifecycle.
 *
 * Ports the essential create/start/stop/retry/poll lifecycle from Python's
 * coach_agent_runs.py + coach_service.py into the Node sidecar, eliminating
 * the Python → Node HTTP round-trip for LLM turns.
 *
 * Simplified vs. Python:
 *   - Teaching session reconciliation is ported with partial lesson extraction
 *     from the context bundle (cue, observation, candidates). No prepared_plan_item.
 *   - Analysis soft-start and guidance compilation are not implemented.
 *   - Provider recovery / resume_waiting_runs is implemented.
 *   - Confirmation execution calls executeNativeWrite for coach_side_effect
 *     confirmations; the full audit reconciliation is simplified.
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
  TeachingTurnContract,
} from "./contracts.ts";
import { TEACHING_TURN_CONTRACT_SCHEMA } from "./contracts.ts";
import { extractRuntimeSecrets, redactRuntimeSecrets } from "./provider-profile.ts";
import { runCoachTurn, stopCoachTurn } from "./turn.ts";
import { startTask, stopTask, waitForTask, isTaskActive } from "./task-manager.ts";
import { executeNativeWrite } from "./product-commands-write.ts";
import { loadArtifact, buildAnalysisBrief } from "./evidence-native.ts";

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
 * TODO: benchmark_summary.
 */
function buildContextBundle(
  db: SqliteDb,
  threadId: number,
  requestedRefs: string[] | null,
  ownerId: string,
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
    // Enrich projection with analysis brief from evidence artifacts.
    const analysisRef = `analysis:${row.analysis_session_id}`;
    const enrichedProjection = { ...projection };
    if (!enrichedProjection.analysis_brief) {
      const loaded = loadArtifact(db, analysisRef, ownerId);
      if (loaded) {
        const brief = buildAnalysisBrief(loaded.artifact, enrichedProjection.diagnosis ?? null);
        if (brief) enrichedProjection.analysis_brief = brief;
      }
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
      analysis_ref: analysisRef,
      comparison_analysis_ref: row.comparison_session_id ? `analysis:${row.comparison_session_id}` : null,
      target_ref: row.target_ref ?? null,
      time_range_ms: row.start_ms != null ? [row.start_ms, row.end_ms] : null,
      projection: enrichedProjection,
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

function loadMessages(
  db: SqliteDb,
  threadId: number,
  excludedMessageId?: number,
): CoachRuntimeMessage[] {
  const rows = db.prepare(
    "SELECT id, role, content FROM coach_messages WHERE thread_id=? ORDER BY id",
  ).all(threadId) as { id: number; role: string; content: string }[];
  return rows
    .filter((r) => r.id !== excludedMessageId && (r.role === "user" || r.role === "assistant"))
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

function loadOrAppendUserMessage(
  db: SqliteDb,
  runRef: string,
  threadId: number,
  content: string,
  snapshots: AnyDict[],
): { priorMessages: CoachRuntimeMessage[]; userMessageId: number } {
  return db.transaction(() => {
    const run = db.prepare(
      "SELECT user_message_id FROM coach_agent_runs WHERE run_ref=?",
    ).get(runRef) as { user_message_id: number | null } | undefined;
    if (!run) {
      throw new AgentRunError("run_unavailable", "Coach run is unavailable");
    }
    if (run.user_message_id !== null) {
      const stored = db.prepare(
        "SELECT id FROM coach_messages WHERE id=? AND thread_id=? AND role='user'",
      ).get(run.user_message_id, threadId) as { id: number } | undefined;
      if (!stored) {
        throw new AgentRunError("teaching_message_missing", "Stored Coach user message is unavailable");
      }
      return {
        priorMessages: loadMessages(db, threadId, stored.id),
        userMessageId: stored.id,
      };
    }

    const priorMessages = loadMessages(db, threadId);
    const userMessageId = appendUserMessage(db, threadId, content, snapshots);
    const cursor = db.prepare(
      "UPDATE coach_agent_runs SET user_message_id=? WHERE run_ref=? AND user_message_id IS NULL",
    ).run(userMessageId, runRef);
    if (cursor.changes !== 1) {
      throw new AgentRunError("teaching_message_conflict", "Coach user message changed before execution");
    }
    return { priorMessages, userMessageId };
  })();
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

// ── Teaching session helpers ──────────────────────────────────────────

const EMPTY_OBSERVATION = "尚未选择可重复观察";
const NO_GROUNDED_ISSUE_QUESTION = "这次分析还没看出一个明确问题。你自己最想先解决哪种失误或哪段动作?";

const TEACHING_PHASES = new Set([
  "intake", "hypothesize", "teach", "await_teach_back", "teach_back_repair",
  "practice_ready", "await_execution_confirmation", "retest_ready",
  "await_retest_confirmation", "revise", "follow_up", "paused", "stopped_for_discomfort",
]);

/**
 * Load the teaching session for an owner+thread from the DB.
 * Returns null if no session exists or the state is invalid.
 */
function loadTeachingSession(
  db: SqliteDb,
  ownerId: string,
  threadId: number,
): { sessionRef: string; version: number; state: AnyDict; activeRunRef: string | null } | null {
  const row = db.prepare(
    "SELECT session_ref, version, state_json, active_run_ref " +
    "FROM teaching_sessions WHERE owner_id=? AND thread_id=?",
  ).get(ownerId, threadId) as AnyDict | undefined;
  if (!row) return null;
  const state = parseJson(row.state_json);
  if (!state || typeof state.phase !== "string" || !TEACHING_PHASES.has(state.phase)) return null;
  return {
    sessionRef: row.session_ref as string,
    version: row.version as number,
    state,
    activeRunRef: (row.active_run_ref as string | null) ?? null,
  };
}

// ── Lesson extraction (simplified port of Python _selected_context_issue + _lesson_from_bundle) ──

const RAW_REFERENCE_RE = /\b(?:analysis|run|event|segment|table|metric):/i;
const PATH_OR_SECRET_RE = /(?:[A-Za-z]:[\\/]|\\\\|file:|https?:\/\/|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=])/i;

function boundedLessonText(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > maximum) return null;
  if (PATH_OR_SECRET_RE.test(text) || RAW_REFERENCE_RE.test(text)) return null;
  return text;
}

type SelectedIssue = { issue: AnyDict; projection: AnyDict; contextRef: string };

/**
 * Find the most relevant diagnostic issue from the context bundle.
 * Simplified port of Python _selected_context_issue — checks explicit issue
 * targets first, then falls back to the first context with diagnosis issues.
 */
function selectedContextIssue(bundle: AnyDict): SelectedIssue | null {
  const contexts = bundle.contexts;
  if (!Array.isArray(contexts)) return null;

  // An explicit issue target is a user-selected context and therefore wins.
  for (const item of contexts) {
    if (!isRecord(item) || item.kind !== "issue") continue;
    const analysisRef = item.analysis_ref;
    const targetRef = item.target_ref;
    if (typeof analysisRef !== "string" || typeof targetRef !== "string") continue;
    const prefix = `${analysisRef}:issue:`;
    if (!targetRef.startsWith(prefix)) continue;
    const index = parseInt(targetRef.slice(prefix.length), 10);
    if (!Number.isInteger(index) || index < 0) continue;
    const projection = item.projection;
    if (!isRecord(projection)) continue;
    const diagnosis = projection.diagnosis;
    const issues = isRecord(diagnosis) ? diagnosis.issues : null;
    if (Array.isArray(issues) && index < issues.length && isRecord(issues[index])) {
      const contextRef = item.context_ref;
      if (typeof contextRef === "string") {
        return { issue: issues[index], projection, contextRef };
      }
    }
  }

  // Fallback: first context with diagnosis issues.
  for (const item of contexts) {
    if (!isRecord(item)) continue;
    const projection = item.projection;
    if (!isRecord(projection)) continue;
    const diagnosis = projection.diagnosis;
    const issues = isRecord(diagnosis) ? diagnosis.issues : null;
    if (!Array.isArray(issues) || issues.length === 0) continue;
    const contextRef = item.context_ref;
    if (typeof contextRef !== "string") continue;
    const firstIssue = issues.find((i: unknown) => isRecord(i));
    if (firstIssue) {
      return { issue: firstIssue as AnyDict, projection, contextRef };
    }
  }
  return null;
}

type Lesson = {
  observation: string | null;
  primaryCandidate: string | null;
  alternatives: string[];
  cue: string | null;
  changedVariable: string | null;
  approvedDose: string | null;
  question: string;
  contextRef: string;
};

/**
 * Extract lesson data (observation, candidates, cue) from the context bundle's
 * diagnostic issues. Simplified port of Python _lesson_from_bundle — does not
 * use coach_problem_compiler, so it only follows the fallback path.
 */
function lessonFromBundle(bundle: AnyDict): Lesson | null {
  const selected = selectedContextIssue(bundle);
  if (!selected) return null;
  const { issue, contextRef } = selected;

  const observation = boundedLessonText(issue.plain_language_meaning, 1200);

  // Compile candidates from root_causes (training/hypothesis level only).
  const candidateTexts: string[] = [];
  const rootCauses = issue.root_causes;
  if (Array.isArray(rootCauses)) {
    for (const cause of rootCauses) {
      if (!isRecord(cause) || (cause.level !== "training" && cause.level !== "hypothesis")) continue;
      const text = boundedLessonText(cause.text, 130);
      if (text !== null && !candidateTexts.includes(text)) candidateTexts.push(text);
      if (candidateTexts.length === 3) break;
    }
  }

  // Extract cue + approved_dose from the first valid prescription.
  let cue: string | null = null;
  let approvedDose: string | null = null;
  const prescriptions = issue.prescriptions;
  if (Array.isArray(prescriptions)) {
    for (const prescription of prescriptions) {
      if (!isRecord(prescription)) continue;
      const candidateCue = boundedLessonText(prescription.cue, 240);
      if (candidateCue === null || candidateCue.toLowerCase() === "not_applicable") continue;
      cue = candidateCue;
      approvedDose = boundedLessonText(prescription.dosage, 480);
      break;
    }
  }

  const primaryCandidate = candidateTexts.length > 0 ? `我先从${candidateTexts[0]}这个方向查起` : null;
  const alternatives = candidateTexts.slice(1, 3).map((text) => `也可能和${text}有关`);

  // Build a discriminator question for the intake phase.
  let question = NO_GROUNDED_ISSUE_QUESTION;
  if (observation !== null && candidateTexts.length >= 2) {
    question = `这次出现「${observation}」时，你更明显感觉到「${candidateTexts[0]}」还是「${candidateTexts[1]}」?`;
  } else if (observation !== null && candidateTexts.length >= 1) {
    question = `这次出现「${observation}」时，你自己最先感觉卡在哪一步?`;
  }

  return {
    observation,
    primaryCandidate,
    alternatives,
    cue,
    changedVariable: cue !== null ? "注意点" : null,
    approvedDose,
    question,
    contextRef,
  };
}

/**
 * Build a TeachingTurnContract from the session state and context bundle.
 *
 * Partial port of Python's _teaching_contract — extracts cue, observation,
 * and candidates from the context bundle's diagnostic issues, and uses them
 * to fill empty state fields (hydration pattern).
 *
 * Still simplified vs. Python:
 *   - No prepared_plan_item compilation (requires plan-store access).
 *   - No humanized/qualified candidate text.
 *   - No peripheral-change-request detection.
 *   - No coach_problem_compiler integration.
 */
function buildTeachingTurn(
  session: {
    sessionRef: string;
    version: number;
    state: AnyDict;
  },
  bundle: AnyDict,
): TeachingTurnContract | null {
  const state = session.state;
  const phase = state.phase as TeachingTurnContract["phase"];
  const lesson = lessonFromBundle(bundle);

  const questionKindMap: Record<string, TeachingTurnContract["question_kind"]> = {
    intake: "discriminator",
    await_teach_back: "teach_back",
    teach_back_repair: "teach_back_repair",
    follow_up: "follow_up",
  };
  const questionMap: Record<string, string | null> = {
    intake: NO_GROUNDED_ISSUE_QUESTION,
    await_teach_back: "这组练习的注意点是什么?",
    teach_back_repair: "用自己的话再说一次这组只改变什么?",
    follow_up: "下一次你准备在哪个相近任务里复测?",
  };

  const questionKind = questionKindMap[phase] ?? "none";
  const question = questionKind !== "none"
    ? (phase === "intake" && lesson ? lesson.question : (questionMap[phase] ?? null))
    : null;

  const commandMap: Record<string, TeachingTurnContract["allowed_command"] | null> = {
    await_execution_confirmation: "training_plan.execution.record",
    await_retest_confirmation: "training_plan.retest.record",
  };
  const allowedCommand = commandMap[phase] ?? null;

  const confirmationIntent: TeachingTurnContract["confirmation_intent"] =
    phase === "await_execution_confirmation" ? "execution" :
    phase === "await_retest_confirmation" ? "retest" : "none";

  const stateObservation = state.observation?.summary;
  const isEmptyObservation = stateObservation === EMPTY_OBSERVATION;
  const observation = isEmptyObservation || typeof stateObservation !== "string"
    ? (lesson?.observation ?? null)
    : stateObservation;

  const statePrimary = isRecord(state.primary_candidate) ? state.primary_candidate.label : null;
  const primary = typeof statePrimary === "string" ? statePrimary : (lesson?.primaryCandidate ?? null);

  const stateAlternatives: string[] = Array.isArray(state.alternatives)
    ? state.alternatives
        .map((item: unknown) => (isRecord(item) ? item.label : null))
        .filter((label: unknown): label is string => typeof label === "string")
    : [];
  const alternatives = stateAlternatives.length > 0 ? stateAlternatives : (lesson?.alternatives ?? []);

  return {
    schema_version: TEACHING_TURN_CONTRACT_SCHEMA,
    session_ref: session.sessionRef,
    session_version: session.version,
    phase,
    problem_id: null,
    problem_label: null,
    evidence_strength: "limited",
    supporting_evidence: [],
    counterevidence_status: "not_observed",
    counterevidence: [],
    observation,
    primary_candidate: primary,
    alternatives,
    cue: typeof state.cue === "string" ? state.cue : (lesson?.cue ?? null),
    changed_variable: typeof state.changed_variable === "string" ? state.changed_variable : (lesson?.changedVariable ?? null),
    active_item_ref: typeof state.active_item_ref === "string" ? state.active_item_ref : null,
    prepared_plan_ref: null,
    prepared_item: null,
    next_recommendation: null,
    question_kind: questionKind,
    question,
    allowed_command: allowedCommand,
    confirmation_intent: confirmationIntent,
    retest: {
      intent: (state.retest_intent as TeachingTurnContract["retest"]["intent"]) ?? "none",
      comparability_required: (state.retest_intent ?? "none") !== "none",
      comparability: (state.retest_comparability as TeachingTurnContract["retest"]["comparability"]) ?? "unresolved",
      revision_decision: (state.revision_decision as TeachingTurnContract["retest"]["revision_decision"]) ?? null,
    },
    ratio_sources: [],
    approved_dose: lesson?.approvedDose ?? null,
    discriminator: null,
    soft_start: false,
  };
}

/**
 * Compute the next teaching state after a successful turn (no tool command used).
 *
 * Simplified port of Python's _state_after_success. Advances the phase
 * according to the fixed transition map. Resets lesson data on follow_up→intake.
 */
function stateAfterSuccess(state: AnyDict, contract: TeachingTurnContract): AnyDict {
  const nextPhaseMap: Record<string, string> = {
    intake: "hypothesize",
    hypothesize: "teach",
    teach: "practice_ready",
    await_teach_back: "practice_ready",
    teach_back_repair: "practice_ready",
    retest_ready: "await_retest_confirmation",
    revise: "follow_up",
    follow_up: "intake",
  };
  let nextPhase = nextPhaseMap[contract.phase] ?? contract.phase;

  // Guard: intake with no candidate stays in intake
  if (contract.phase === "intake" && contract.primary_candidate === null) {
    nextPhase = "intake";
  }
  // Guard: retest_ready with no retest intent stays in retest_ready
  if (contract.phase === "retest_ready" && contract.retest.intent === "none") {
    nextPhase = "retest_ready";
  }

  const next: AnyDict = JSON.parse(JSON.stringify(state));
  next.phase = nextPhase;
  next.pending_confirmation_ref = null;
  next.pause_reason = null;

  // follow_up → intake resets the lesson
  if (contract.phase === "follow_up") {
    next.observation = { summary: EMPTY_OBSERVATION, source_refs: [] };
    next.primary_candidate = null;
    next.alternatives = [];
    next.cue = null;
    next.changed_variable = null;
    next.active_item_ref = null;
    next.retest_intent = "none";
    next.retest_comparability = "unresolved";
    next.revision_decision = null;
    next.next_recommendation = null;
  }

  return next;
}

/**
 * Persist the next teaching session state, incrementing version and releasing
 * the active run. Simplified — does not use optimistic-locking CAS beyond the
 * version check.
 */
export function releaseTeachingRun(
  db: SqliteDb,
  ownerId: string,
  sessionRef: string,
  expectedVersion: number,
  runRef: string,
  nextState: AnyDict | null,
): void {
  if (nextState !== null) {
    const cursor = db.prepare(
      "UPDATE teaching_sessions SET state_json=?, version=version+1, active_run_ref=NULL, " +
      "pending_confirmation_ref=?, pause_reason=?, updated_at=CURRENT_TIMESTAMP " +
      "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref=?",
    ).run(
      JSON.stringify(nextState),
      nextState.pending_confirmation_ref ?? null,
      nextState.pause_reason ?? null,
      sessionRef,
      ownerId,
      expectedVersion,
      runRef,
    );
    if (cursor.changes === 1) return;
  } else {
    const cursor = db.prepare(
      "UPDATE teaching_sessions SET active_run_ref=NULL, updated_at=CURRENT_TIMESTAMP " +
      "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref=?",
    ).run(sessionRef, ownerId, expectedVersion, runRef);
    if (cursor.changes === 1) return;
  }

  const current = db.prepare(
    "SELECT version, active_run_ref FROM teaching_sessions WHERE session_ref=? AND owner_id=?",
  ).get(sessionRef, ownerId) as { version: number; active_run_ref: string | null } | undefined;
  if (!current) {
    throw new AgentRunError("teaching_session_missing", "TeachingSession is unavailable");
  }

  if (current.active_run_ref === runRef) {
    const released = db.prepare(
      "UPDATE teaching_sessions SET active_run_ref=NULL, updated_at=CURRENT_TIMESTAMP " +
      "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref=?",
    ).run(sessionRef, ownerId, current.version, runRef);
    if (released.changes !== 1) {
      throw new AgentRunError("teaching_state_conflict", "TeachingSession changed before the run was released");
    }
  } else if (current.active_run_ref !== null) {
    throw new AgentRunError("teaching_state_conflict", "TeachingSession is owned by another run");
  }

  if (nextState !== null) {
    throw new AgentRunError("teaching_state_conflict", "TeachingSession changed before the lesson advanced");
  }
}

/**
 * Check whether the model used a teaching command (training_plan.item.add,
 * training_plan.execution.record, training_plan.retest.record) in this turn.
 */
function usedTeachingCommand(toolEvents: CoachRuntimeToolEvent[]): boolean {
  const teachingCommands = new Set([
    "teaching_session.update",
    "training_plan.item.add",
    "training_plan.execution.record",
    "training_plan.retest.record",
  ]);
  return toolEvents.some(
    (e) => e.type === "product_command" && teachingCommands.has(e.command_name),
  );
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
  let teachingSession: ReturnType<typeof loadTeachingSession> = null;

  try {
    // Phase 1: text generation
    setRun(db, runRef, "running", "text_generation", { started: true });
    appendEvent(db, runRef, "phase", "text_generation", "text_generation_started", "Coach is generating a response");

    if (isStopRequested(db, runRef)) {
      markStopped(db, runRef);
      return;
    }

    const { priorMessages } = loadOrAppendUserMessage(
      db,
      runRef,
      threadId,
      content,
      snapshots,
    );

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
      setRun(db, runRef, "queued", "queued", { error: failure });
      appendEvent(db, runRef, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
      return;
    }

    if (providerResult.needsReauth) {
      const failure = {
        domain: "permission",
        code: "provider_reauthentication_required",
        message: "Provider credential requires reauthentication",
        retryable: true,
      };
      setRun(db, runRef, "queued", "queued", { error: failure });
      appendEvent(db, runRef, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
      return;
    }

    // Load teaching session and build the teaching turn contract
    teachingSession = loadTeachingSession(db, ownerId, threadId);
    const teachingTurn = teachingSession ? buildTeachingTurn(teachingSession, bundle) : undefined;

    // Build the turn request
    const turnRequest: AnyDict = {
      schema_version: "coach_runtime_turn.v1",
      run_id: runRef,
      session_id: `coach-thread:${threadId}`,
      user_id: ownerId,
      messages: [...priorMessages, { role: "user" as const, content }],
      analysis_summary: analysisSummary,
      model: providerResult.profile,
      // tool_bridge is null — native DB access handles product commands
    };
    if (teachingTurn) {
      turnRequest.teaching_turn = teachingTurn;
    }

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
        appendEvent(
          db,
          runRef,
          "text",
          "text_generation",
          "text_revision",
          "Coach response text was revised",
          {
            mode: "replace",
            revision: partial.revision,
            elapsed_ms: partial.elapsed_ms,
            provider_rounds: partial.provider_rounds,
          },
        );
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
      // Release the teaching session claim on stop (no state advancement)
      if (teachingSession) {
        releaseTeachingRun(
          db,
          ownerId,
          teachingSession.sessionRef,
          teachingSession.version,
          runRef,
          null,
        );
      }
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

      // Update teaching session state after a successful turn.
      // If the model used a teaching command, just release the run (the
      // confirmation flow handles state advancement). Otherwise, advance
      // the phase via stateAfterSuccess.
      if (teachingSession && teachingTurn) {
        const usedCommand = usedTeachingCommand(toolEvents);
        const nextState = usedCommand ? null : stateAfterSuccess(teachingSession.state, teachingTurn);
        releaseTeachingRun(
          db,
          ownerId,
          teachingSession.sessionRef,
          teachingSession.version,
          runRef,
          nextState,
        );
      }

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

      // Provider-waiting codes keep the run queued (and the teaching claim alive)
      if (failure.code === "provider_unconfigured" || failure.code === "provider_reauthentication_required") {
        setRun(db, runRef, "queued", "queued", { partialText: null, error: failure });
        appendEvent(db, runRef, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
        return;
      }

      // Release the teaching session claim on failure (no state advancement)
      if (teachingSession) {
        releaseTeachingRun(
          db,
          ownerId,
          teachingSession.sessionRef,
          teachingSession.version,
          runRef,
          null,
        );
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
    if (teachingSession) {
      try {
        releaseTeachingRun(
          db,
          ownerId,
          teachingSession.sessionRef,
          teachingSession.version,
          runRef,
          null,
        );
      } catch {
        // Preserve the original failure; the guarded release already fails closed.
      }
    }
    const failure = error instanceof AgentRunError
      ? {
          domain: "tool",
          code: error.code,
          message: error.message,
          retryable: false,
        }
      : {
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
    ownerId,
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
 * Requeue Provider-waiting runs for one owner, at most once per run.
 *
 * Called on every poll of GET /api/coach/agent-runs/{ref}. Checks for runs in
 * 'queued' status with a provider-waiting error (provider_unconfigured or
 * provider_reauthentication_required). If the provider is now configured,
 * clears the error and restarts the turn.
 *
 * Port of Python's resume_waiting_runs. Simplified:
 *   - No terminal locks (Node is single-threaded within one process).
 *   - Reuses loadDefaultProviderProfile as the readiness check.
 */
export function resumeWaitingRuns(db: SqliteDb, ownerId: string): string[] {
  const providerResult = loadDefaultProviderProfile(db, ownerId);
  if (!providerResult || providerResult.needsReauth) return [];

  const rows = db.prepare(
    "SELECT run_ref, thread_id, content, context_refs_json, error_json " +
    "FROM coach_agent_runs WHERE owner_id=? AND status='queued' " +
    "AND stop_requested=0 AND error_json IS NOT NULL " +
    "ORDER BY created_at, rowid",
  ).all(ownerId) as AnyDict[];

  const providerWaitCodes = new Set(["provider_unconfigured", "provider_reauthentication_required"]);
  const resumed: string[] = [];

  for (const row of rows) {
    const error = parseJson(row.error_json);
    if (!error || typeof error.code !== "string" || !providerWaitCodes.has(error.code)) continue;

    const runRef = row.run_ref as string;
    if (isTaskActive(runRef)) continue;

    // Re-read to confirm still queued+waiting (no concurrent mutation)
    const current = db.prepare(
      "SELECT thread_id, content, context_refs_json, error_json " +
      "FROM coach_agent_runs WHERE owner_id=? AND run_ref=? " +
      "AND status='queued' AND stop_requested=0",
    ).get(ownerId, runRef) as AnyDict | undefined;
    if (!current) continue;

    const currentError = parseJson(current.error_json);
    if (!currentError || typeof currentError.code !== "string" || !providerWaitCodes.has(currentError.code)) continue;

    const snapshots = parseJsonArray(current.context_refs_json);
    const refs = snapshots
      .map((s) => s.context_ref)
      .filter((r: unknown): r is string => typeof r === "string");

    const threadId = current.thread_id as number;
    let bundle: AnyDict;
    try {
      const rebuilt = buildContextBundle(db, threadId, refs, ownerId);
      bundle = rebuilt.bundle;
    } catch {
      continue;
    }

    // Atomically clear the error and requeue
    const expectedError = JSON.stringify(currentError, null, 0);
    const cursor = db.prepare(
      "UPDATE coach_agent_runs SET error_json=NULL, partial_text=NULL, " +
      "phase='queued', updated_at=CURRENT_TIMESTAMP " +
      "WHERE owner_id=? AND run_ref=? AND status='queued' " +
      "AND stop_requested=0 AND error_json=?",
    ).run(ownerId, runRef, expectedError);
    if (cursor.changes !== 1) continue;

    appendEvent(db, runRef, "status", "queued", "provider_requeued", "Coach run requeued after Provider recovery");

    const safeContent = (current.content as string).trim().slice(0, 12_000);
    startTask(runRef, (signal) =>
      runAgentTurn(db, runRef, ownerId, threadId, safeContent, bundle, snapshots, signal),
    );
    resumed.push(runRef);
  }

  return resumed;
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
    created_at: sqliteTimestampToWireUtc(row.created_at) ?? row.created_at,
    started_at: sqliteTimestampToWireUtc(row.started_at),
    finished_at: sqliteTimestampToWireUtc(row.finished_at),
  };
}

/**
 * Stop a running agent run.
 * Sets stop_requested=1, calls stopCoachTurn, and waits up to 3 seconds
 * for the background task to finish cooperatively. If the task is still
 * active after the timeout, force-marks the run as stopped.
 */
export async function stopAgentRun(db: SqliteDb, ownerId: string, runRef: string): Promise<AgentRunState | null> {
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

  // Wait up to 3 seconds for graceful shutdown
  if (isTaskActive(runRef)) {
    await waitForTask(runRef, 3000);
    // If still active after timeout, force mark stopped
    if (isTaskActive(runRef)) {
      markStopped(db, runRef, current.partial_text);
    }
  } else {
    // No active task — mark stopped directly
    markStopped(db, runRef, current.partial_text);
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
    "SELECT content, context_refs_json, user_message_id, thread_id, attempt FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
  ).get(runRef, ownerId) as AnyDict;

  const snapshots = parseJsonArray(row.context_refs_json);
  const refs = snapshots.map((s) => s.context_ref).filter((r: unknown) => typeof r === "string");
  const threadId = row.thread_id;
  const { bundle } = buildContextBundle(db, threadId, refs, ownerId);

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
    "user_message_id, context_refs_json) VALUES(?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)",
  ).run(
    newRunRef,
    ownerId,
    threadId,
    runRef,
    attempt,
    row.content,
    row.user_message_id,
    JSON.stringify(snapshots, null, 0),
  );

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
    // Status changed concurrently — re-read the confirmation request
    const refreshed = db.prepare(
      "SELECT confirmation_ref, action, target_ref, status, impact_code, impact_message, created_at, decided_at " +
      "FROM coach_confirmation_requests WHERE confirmation_ref=? AND owner_id=?",
    ).get(confirmationRef, ownerId) as AnyDict | undefined;
    return refreshed ? formatConfirmation(refreshed, null, null, null) : null;
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
