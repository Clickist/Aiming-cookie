/**
 * Simplified Coach agent run lifecycle.
 *
 * Phase 1 rewrite: replaces the SQLite-based lifecycle with in-memory run
 * state and Pi-session-backed conversation persistence. Removes teaching
 * sessions, confirmations, context bundles, and all SQLite dependencies.
 *
 * Agent run state lives in an in-memory Map (sufficient for a local
 * single-user desktop app). Conversations are persisted as Pi JSONL sessions
 * in app-data/conversations/ via JsonlSessionRepo (see session-repo.ts).
 */

import { randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

import { isRecord } from "./contracts.ts";
import type {
  CoachRuntimeProviderProfile,
  CoachRuntimeToolEvent,
} from "./contracts.ts";
import { extractRuntimeSecrets, redactRuntimeSecrets } from "./provider-profile.ts";
import { runCoachTurn, stopCoachTurn } from "./turn.ts";
import { startTask, stopTask, waitForTask, isTaskActive } from "./task-manager.ts";
import { getConfigDir } from "./app-data.ts";
import {
  ensureSession,
  nextSessionIdSync,
  readSessionMessages,
  updateConversationAnalysisIds,
} from "./session-repo.ts";

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
  events: AgentRunEvent[];
  /** Analysis refs (`analysis:{id}`) the run engaged with via file reads. */
  analysis_refs: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type AgentRunEvent = {
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

/**
 * Subscriber callbacks for a single agent run. Partial text and activity
 * events stream while the run is queued/running; done fires once the run
 * reaches a terminal status (succeeded/failed/stopped).
 */
export type AgentRunListener = {
  onPartial?: (text: string) => void;
  onActivity?: (event: AgentRunEvent) => void;
  onDone?: (state: AgentRunState) => void;
};

export class AgentRunError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "AgentRunError";
    this.code = code;
  }
}

// ── In-memory run store ───────────────────────────────────────────────

interface RunRecord {
  state: AgentRunState;
  ownerId: string;
  threadId: number;
  content: string;
  stopRequested: boolean;
  events: AgentRunEvent[];
  listeners: Set<AgentRunListener>;
}

const runs = new Map<string, RunRecord>();

// ── Provider profile loading ──────────────────────────────────────────

export function loadDefaultProviderProfile(_ownerId: string): {
  profile: CoachRuntimeProviderProfile;
  needsReauth: boolean;
} | null {
  const configPath = join(getConfigDir(), "provider.json");
  if (!existsSync(configPath)) return null;
  try {
    const raw = readFileSync(configPath, "utf8");
    const config = JSON.parse(raw);
    if (!isRecord(config)) return null;
    const profile = config.profile ?? config;
    if (!isRecord(profile) || typeof profile.kind !== "string") return null;
    return {
      profile: profile as CoachRuntimeProviderProfile,
      needsReauth: false,
    };
  } catch {
    return null;
  }
}

// ── Session management ────────────────────────────────────────────────

function resolveThreadId(ownerId: string, sessionId?: number): number {
  if (sessionId !== undefined && Number.isInteger(sessionId) && sessionId > 0) {
    return sessionId;
  }
  // Auto-allocate next session id
  return nextSessionIdSync();
}

// ── Event helpers ─────────────────────────────────────────────────────

function appendEvent(record: RunRecord, type: string, phase: string, code: string, message: string, payload?: AnyDict | null): AgentRunEvent {
  const sequence = record.events.length + 1;
  const event: AgentRunEvent = {
    schema_version: "coach_agent_run_event.v1",
    event_ref: `agent_event:${randomUUID().replace(/-/g, "")}`,
    sequence,
    type,
    phase,
    code,
    message,
    payload: payload ?? null,
    created_at: new Date().toISOString(),
  };
  record.events.push(event);
  return event;
}

function setRunStatus(record: RunRecord, status: AgentRunState["status"], phase: AgentRunState["phase"], options: { partialText?: string | null; error?: AnyDict | null; started?: boolean; finished?: boolean } = {}): void {
  const state = record.state;
  state.status = status;
  state.phase = phase;
  if (options.partialText !== undefined) state.partial_text = options.partialText;
  if (options.error !== undefined) state.error = options.error;
  if (options.started) state.started_at = state.started_at ?? new Date().toISOString();
  if (options.finished) state.finished_at = new Date().toISOString();
  if (isTerminalStatus(status)) broadcastDone(record);
}

// ── Stream subscription helpers ───────────────────────────────────────

function isTerminalStatus(status: AgentRunState["status"]): boolean {
  return status === "succeeded" || status === "failed" || status === "stopped";
}

function snapshotRun(record: RunRecord): AgentRunState {
  return {
    ...record.state,
    events: [...record.events],
  };
}

function broadcastPartial(record: RunRecord, text: string): void {
  for (const listener of record.listeners) {
    try {
      listener.onPartial?.(text);
    } catch {
      // A listener (e.g. a closed SSE socket) must not break the run turn.
    }
  }
}

function broadcastActivity(record: RunRecord, event: AgentRunEvent): void {
  for (const listener of record.listeners) {
    try {
      listener.onActivity?.(event);
    } catch {
      // A listener (e.g. a closed SSE socket) must not break the run turn.
    }
  }
}

function broadcastDone(record: RunRecord): void {
  const state = snapshotRun(record);
  for (const listener of record.listeners) {
    try {
      listener.onDone?.(state);
    } catch {
      // A listener (e.g. a closed SSE socket) must not break the run turn.
    }
  }
  record.listeners.clear();
}

// ── Async turn execution ──────────────────────────────────────────────

async function runAgentTurn(
  runRef: string,
  ownerId: string,
  threadId: number,
  content: string,
  signal: AbortSignal,
): Promise<void> {
  const record = runs.get(runRef);
  if (!record) return;
  const persistenceStart = performance.now();

  try {
    setRunStatus(record, "running", "text_generation", { started: true });
    appendEvent(record, "phase", "text_generation", "text_generation_started", "Coach is generating a response");

    if (record.stopRequested) {
      setRunStatus(record, "stopped", "completed", { finished: true });
      appendEvent(record, "status", "completed", "run_stopped", "Coach run stopped by the user");
      return;
    }

    // Load provider profile
    const providerResult = loadDefaultProviderProfile(ownerId);
    if (!providerResult) {
      const failure = {
        domain: "permission",
        code: "provider_unconfigured",
        message: "Coach Provider is not configured",
        retryable: true,
      };
      setRunStatus(record, "queued", "queued", { error: failure });
      appendEvent(record, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
      return;
    }

    // Open the persistent Coach thread session and load prior messages.
    const session = await ensureSession(threadId);
    const priorMessages = await readSessionMessages(threadId);

    // Persist the user message before the turn so it survives early failures.
    await session.appendMessage({
      role: "user",
      content: [{ type: "text", text: content }],
      timestamp: Date.now(),
    });

    // Build the turn request
    const turnRequest: AnyDict = {
      schema_version: "coach_runtime_turn.v1",
      run_id: runRef,
      session_id: `coach-thread:${threadId}`,
      user_id: ownerId,
      messages: [...priorMessages, { role: "user" as const, content }],
      model: providerResult.profile,
    };

    const secrets = extractRuntimeSecrets(turnRequest);

    // Execute the turn. The persistent session is handed to the harness so
    // history is loaded through Session.buildContext(); the harness persists
    // the current user message and the (redacted) assistant reply.
    const response = await runCoachTurn(turnRequest, {
      session,
      onPartial: async (partial) => {
        if (signal.aborted) return;
        const safeText = partial.text.slice(0, 12_000);
        record.state.partial_text = safeText;
        appendEvent(record, "text", "text_generation", "text_revision", "Coach response text was revised", {
          mode: "replace",
          revision: partial.revision,
          elapsed_ms: partial.elapsed_ms,
          provider_rounds: partial.provider_rounds,
        });
        broadcastPartial(record, safeText);
      },
      onActivity: async (activity) => {
        if (signal.aborted) return;
        if (activity.kind === "tool" && activity.state === "started") {
          record.state.phase = "tool_execution";
        } else if (activity.kind === "tool" && activity.state !== "started") {
          record.state.phase = "text_generation";
        }
        const payload: AnyDict = {};
        for (const key of ["sequence", "kind", "state", "tool_call_id", "tool_name", "command_name"] as const) {
          if (activity[key] !== undefined) (payload as AnyDict)[key] = activity[key];
        }
        const event = appendEvent(
          record,
          activity.kind === "tool" ? "tool" : "phase",
          activity.kind === "tool" && activity.state === "started" ? "tool_execution" : "text_generation",
          `${activity.kind}_${activity.state}`,
          "Coach activity update",
          payload,
        );
        broadcastActivity(record, event);
      },
    });

    // Record the analyses this turn read so the frontend can resolve `@3.4s`
    // time links to video seeks, and keep the session's engaged-analysis list
    // in sync so the ref survives a panel refresh/reload.
    const analysisRefs = response.analysis_refs ?? [];
    record.state.analysis_refs = analysisRefs;
    if (analysisRefs.length > 0) {
      const analysisIds: number[] = [];
      for (const ref of analysisRefs) {
        const match = /^analysis:([1-9][0-9]*)$/.exec(ref);
        if (match) analysisIds.push(Number(match[1]));
      }
      if (analysisIds.length > 0) updateConversationAnalysisIds(threadId, analysisIds);
    }

    if (signal.aborted || record.stopRequested) {
      setRunStatus(record, "stopped", "completed", { finished: true });
      appendEvent(record, "status", "completed", "run_stopped", "Coach run stopped by the user");
      return;
    }

    if (response.ok) {
      const reply = response.reply ?? "(本次未能生成回复)";
      const redactedReply = redactRuntimeSecrets(reply, secrets);

      // The assistant reply is persisted by the harness through the wrapped
      // session in turn.ts (redacted at the write boundary).

      setRunStatus(record, "succeeded", "completed", {
        partialText: redactedReply,
        finished: true,
      });
      appendEvent(record, "text", "text_generation", "text_available", "Coach response text is available");
      const timing: AnyDict = { persistence_ms: Math.max(0, Math.round(performance.now() - persistenceStart)) };
      appendEvent(record, "phase", "completed", "latency_trace", "Coach latency trace", timing);
      appendEvent(record, "status", "completed", "run_succeeded", "Coach run completed");
    } else {
      const error = response.error;
      const failure: AnyDict = error
        ? {
            domain: error.domain in { network: 1, model: 1, permission: 1, tool: 1 } ? error.domain : "model",
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

      if (failure.code === "provider_unconfigured" || failure.code === "provider_reauthentication_required") {
        setRunStatus(record, "queued", "queued", { partialText: null, error: failure });
        appendEvent(record, "status", "queued", "provider_waiting", "Coach run is waiting for Provider");
        return;
      }

      setRunStatus(record, "failed", "completed", {
        partialText: response.partial_reply,
        error: failure,
        finished: true,
      });
      appendEvent(record, "error", "completed", failure.code, failure.message);
    }
  } catch (error) {
    const failure = error instanceof AgentRunError
      ? { domain: "tool", code: error.code, message: error.message, retryable: false }
      : { domain: "model", code: "generation_failed", message: "Coach generation failed", retryable: true };
    setRunStatus(record, "failed", "completed", { error: failure, finished: true });
    appendEvent(record, "error", "completed", failure.code, failure.message);
  }
}

// ── Public lifecycle functions ────────────────────────────────────────

export function createAgentRun(
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
  const threadId = options.sessionId ?? resolveThreadId(ownerId, options.sessionId);

  const runRef = `agent_run:${randomUUID().replace(/-/g, "")}`;
  const now = new Date().toISOString();

  const record: RunRecord = {
    state: {
      schema_version: "coach_agent_run.v1",
      run_ref: runRef,
      session_id: threadId,
      parent_run_ref: null,
      attempt: 1,
      status: "queued",
      phase: "queued",
      partial_text: null,
      error: null,
      events: [],
      analysis_refs: [],
      created_at: now,
      started_at: null,
      finished_at: null,
    },
    ownerId,
    threadId,
    content: safeContent,
    stopRequested: false,
    events: [],
    listeners: new Set(),
  };

  runs.set(runRef, record);
  appendEvent(record, "status", "queued", "run_queued", "Coach run queued");

  startTask(runRef, (signal) => runAgentTurn(runRef, ownerId, threadId, safeContent, signal));

  return getAgentRun(ownerId, runRef)!;
}

export function resumeWaitingRuns(ownerId: string): string[] {
  const providerResult = loadDefaultProviderProfile(ownerId);
  if (!providerResult) return [];

  const resumed: string[] = [];
  for (const [runRef, record] of runs) {
    if (record.ownerId !== ownerId) continue;
    if (record.state.status !== "queued") continue;
    if (!record.state.error) continue;
    const code = record.state.error.code;
    if (code !== "provider_unconfigured" && code !== "provider_reauthentication_required") continue;
    if (isTaskActive(runRef)) continue;

    record.state.error = null;
    record.state.partial_text = null;
    record.state.phase = "queued";
    appendEvent(record, "status", "queued", "provider_requeued", "Coach run requeued after Provider recovery");

    startTask(runRef, (signal) => runAgentTurn(runRef, ownerId, record.threadId, record.content, signal));
    resumed.push(runRef);
  }
  return resumed;
}

export function getAgentRun(ownerId: string, runRef: string): AgentRunState | null {
  const record = runs.get(runRef);
  if (!record || record.ownerId !== ownerId) return null;
  return {
    ...record.state,
    events: [...record.events],
  };
}

/**
 * Subscribe to live stream updates for a single agent run.
 *
 * Returns an unsubscribe function, or null when the run is not visible to the
 * owner. If the run is already terminal at subscribe time, the listener's
 * onDone is notified once (deferred to the next microtask) and no permanent
 * subscription is kept.
 */
export function subscribeAgentRun(
  ownerId: string,
  runRef: string,
  listener: AgentRunListener,
): (() => void) | null {
  const record = runs.get(runRef);
  if (!record || record.ownerId !== ownerId) return null;
  if (isTerminalStatus(record.state.status)) {
    const snapshot = snapshotRun(record);
    queueMicrotask(() => {
      listener.onDone?.(snapshot);
    });
    return () => {};
  }
  record.listeners.add(listener);
  return () => {
    record.listeners.delete(listener);
  };
}

export async function stopAgentRun(ownerId: string, runRef: string): Promise<AgentRunState | null> {
  const record = runs.get(runRef);
  if (!record || record.ownerId !== ownerId) return null;
  const current = getAgentRun(ownerId, runRef);
  if (!current) return null;
  if (["succeeded", "failed", "stopped"].includes(current.status)) return current;

  record.stopRequested = true;
  stopCoachTurn(runRef);
  stopTask(runRef);

  if (isTaskActive(runRef)) {
    await waitForTask(runRef, 3000);
    if (isTaskActive(runRef)) {
      setRunStatus(record, "stopped", "completed", { finished: true });
      appendEvent(record, "status", "completed", "run_stopped", "Coach run stopped by the user");
    }
  } else {
    setRunStatus(record, "stopped", "completed", { finished: true });
    appendEvent(record, "status", "completed", "run_stopped", "Coach run stopped by the user");
  }

  return getAgentRun(ownerId, runRef);
}

export function retryAgentRun(ownerId: string, runRef: string): AgentRunState | null {
  const detail = getAgentRun(ownerId, runRef);
  if (!detail) return null;
  if (detail.status !== "failed" || !detail.error?.retryable) {
    throw new AgentRunError("retry_not_allowed", "Coach run is not retryable");
  }

  const record = runs.get(runRef);
  if (!record) return null;

  const newRunRef = `agent_run:${randomUUID().replace(/-/g, "")}`;
  const attempt = (detail.attempt ?? 1) + 1;
  const now = new Date().toISOString();

  const newRecord: RunRecord = {
    state: {
      ...detail,
      run_ref: newRunRef,
      parent_run_ref: runRef,
      attempt,
      status: "queued",
      phase: "queued",
      partial_text: null,
      error: null,
      events: [],
      created_at: now,
      started_at: null,
      finished_at: null,
    },
    ownerId,
    threadId: record.threadId,
    content: record.content,
    stopRequested: false,
    events: [],
    listeners: new Set(),
  };

  runs.set(newRunRef, newRecord);
  appendEvent(newRecord, "status", "queued", "run_queued", "Coach run queued");

  startTask(newRunRef, (signal) => runAgentTurn(newRunRef, ownerId, record.threadId, record.content, signal));

  return getAgentRun(ownerId, newRunRef);
}

export function decideConfirmation(
  _ownerId: string,
  _confirmationRef: string,
  _decision: "confirm" | "reject",
): AnyDict | null {
  // Confirmations are not used in the file-based architecture.
  // Coach executes write commands directly via file system tools.
  return null;
}
