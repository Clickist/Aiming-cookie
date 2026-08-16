/**
 * File-based implementations of write product commands.
 *
 * These commands read and write JSON files in the app-data directory,
 * eliminating SQLite database access for write operations.
 *
 * Commands that require the Python analysis worker (analysis.retry) and
 * complex session management (coach.session.*, coach.context.detach) are NOT
 * native — they delegate to the Python backend bridge or the REST API.
 * analysis.create_from_run is native via the Python REST API (see
 * python-analysis.ts); teaching_session.update is native below.
 */
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync, appendFileSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { getAnalysesDir, getConfigDir, getTeachingDir, getTrainingDir } from "./app-data.ts";
import { isTeachingPhase, isTeachingPhaseTransitionAllowed } from "./teaching-policy.ts";
import { getPythonBackendConfig } from "./python-backend.ts";

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

type WriteHandler = (params: AnyDict, ownerId: string) => HandlerResult;

// ── Native write command set ───────────────────────────────────────────
//
// Commands NOT listed here (coach.session.*, coach.context.detach) fall
// through to the Python backend bridge.
// analysis.create_from_run is handled natively in python-analysis.ts and
// analysis.retry natively below.

export const NATIVE_WRITE_COMMANDS = new Set<string>([
  "training_plan.generate_draft",
  "training_plan.save",
  "training_plan.activate",
  "training_plan.pause",
  "training_plan.adjust",
  "training_plan.item.add",
  "training_plan.execution.record",
  "training_plan.retest.record",
  "teaching_session.update",
  "scenario_memory.set",
  "calibration.save",
  "calibration.delete",
  "peripheral_profile.update",
  "kovaak.connection.disconnect",
]);

// ── Helpers ────────────────────────────────────────────────────────────

function newCommandId(): string {
  return `command:${randomUUID().replace(/-/g, "")}`;
}

function newAuditRef(): string {
  return `audit:${randomUUID().replace(/-/g, "")}`;
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${field} is required`);
  return value;
}

function readJsonFile<T = AnyDict>(path: string): T | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as T;
  } catch {
    return null;
  }
}

function writeJsonFile(path: string, data: unknown): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function ok(result: unknown, resultRef?: string, uiEvent?: AnyDict | null): HandlerResult {
  return { status: "succeeded", result, result_ref: resultRef, ui_event: uiEvent };
}

function fail(code: string, message: string): HandlerResult {
  return { status: "failed", warning_or_error: { code, message } };
}

// Handler-thrown messages are forwarded to the Provider only when they stay
// bounded and cannot carry a filesystem path or credential (e.g. Node fs
// errors embed absolute paths). Everything else degrades to the generic text.
const HANDLER_MESSAGE_MAX = 200;
const HANDLER_MESSAGE_PATH = /\\|(?:[A-Za-z]:[\\/]|\/(?:Users|home|tmp|var|temp)\b)/i;

function isSafeHandlerMessage(message: string): boolean {
  return message.length > 0 && message.length <= HANDLER_MESSAGE_MAX && !HANDLER_MESSAGE_PATH.test(message);
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

// ── Config write commands ──────────────────────────────────────────────

// calibration.save
const calibrationSave: WriteHandler = (params, _ownerId) => {
  const cmPer360 = params.cm_per_360;
  const fov = params.fov;
  if (cmPer360 === null && fov === null) throw new Error("cm_per_360 and fov cannot both be empty");
  if (cmPer360 !== null && cmPer360 !== undefined && !(cmPer360 > 0 && cmPer360 <= 1000)) {
    throw new Error("cm_per_360 must be between 0 and 1000");
  }
  if (fov !== null && fov !== undefined && !(fov > 0 && fov <= 180)) {
    throw new Error("fov must be between 0 and 180");
  }
  const configDir = getConfigDir();
  const existing = readJsonFile(join(configDir, "calibration.json")) ?? {};
  const data = {
    ...existing,
    cm_per_360: cmPer360 ?? existing.cm_per_360 ?? null,
    fov: fov ?? existing.fov ?? null,
    updated_at: nowIso(),
  };
  writeJsonFile(join(configDir, "calibration.json"), data);
  return ok({
    schema_version: "calibration_profile.v1",
    configured: true,
    values: { cm_per_360: data.cm_per_360, fov: data.fov },
    dpi: null,
    sensitivity: null,
    adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
    updated_at: data.updated_at,
  }, "calibration:current");
};

// calibration.delete
const calibrationDelete: WriteHandler = (_params, _ownerId) => {
  const path = join(getConfigDir(), "calibration.json");
  const existed = existsSync(path);
  if (existed) {
    const data = readJsonFile(path) ?? {};
    writeJsonFile(path, { ...data, cm_per_360: null, fov: null, updated_at: nowIso() });
  }
  return ok({
    schema_version: "calibration_profile.v1",
    configured: false,
    values: { cm_per_360: null, fov: null },
    dpi: null,
    sensitivity: null,
    adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
    updated_at: null,
    deletion_state: existed ? "completed" : "already_absent",
  }, "calibration:current");
};

// kovaak.connection.disconnect
const kovaakDisconnect: WriteHandler = (_params, _ownerId) => {
  const path = join(getConfigDir(), "kovaak-connection.json");
  const existed = existsSync(path);
  if (existed) rmSync(path, { force: true });
  return ok({
    connection_ref: "kovaak_connection:current",
    disconnected: true,
    was_connected: existed,
  }, "kovaak_connection:current");
};

// ── Peripheral profile update ─────────────────────────────────────────

const _GRIP_TYPES = new Set(["fingertip", "fingertip_claw", "claw", "claw_palm", "palm"]);
const _WRIST_POSITIONS = new Set(["suspended", "on_pad"]);
const _PERIPHERAL_ALLOWED_FIELDS = new Set([
  "grip_type", "hand_length_cm", "wrist_position", "grip_preference",
  "current_mouse_brand", "current_mouse_model", "current_mousepad", "budget",
]);

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

const peripheralProfileUpdate: WriteHandler = (params, _ownerId) => {
  const validated: AnyDict = {};
  for (const field of _PERIPHERAL_ALLOWED_FIELDS) {
    if (field in params) validated[field] = validatePeripheralField(field, params[field]);
  }
  if (Object.keys(validated).length === 0) throw new Error("at least one field must be provided");

  const configDir = getConfigDir();
  const existing = readJsonFile(join(configDir, "peripheral.json")) ?? {};
  const merged: AnyDict = { ...existing, ...validated, updated_at: nowIso() };
  writeJsonFile(join(configDir, "peripheral.json"), merged);
  return ok({
    schema_version: "peripheral_profile.v1",
    configured: true,
    grip_type: merged.grip_type ?? null,
    hand_length_cm: merged.hand_length_cm ?? null,
    wrist_position: merged.wrist_position ?? null,
    grip_preference: merged.grip_preference ?? null,
    current_mouse_brand: merged.current_mouse_brand ?? null,
    current_mouse_model: merged.current_mouse_model ?? null,
    current_mousepad: merged.current_mousepad ?? null,
    budget: merged.budget ?? null,
    updated_at: merged.updated_at,
  }, "peripheral_profile:current");
};

// ── Analysis delete ────────────────────────────────────────────────────
//
// analysis.delete is async: it must synchronise with the Python backend so the
// session record (sessions/{id}.json), its managed workspace, and the deletion
// tombstone are removed together — otherwise the frontend History keeps showing
// a ghost analysis. It then best-effort removes the local progressive-disclosure
// directory so the Coach's own analysis read/list stops seeing it too.

const ANALYSIS_DELETE_TIMEOUT_MS = 15_000;

export function isNativeAnalysisDeleteCommand(commandName: string): boolean {
  return commandName === "analysis.delete";
}

export async function executeNativeAnalysisDelete(
  commandName: string,
  params: AnyDict,
  ownerId: string,
  _idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<NativeWriteResult> {
  const commandId = newCommandId();
  const auditRef = newAuditRef();

  if (commandName !== "analysis.delete") {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "unknown_command", message: `${commandName} is not an analysis delete command` },
    };
  }

  let ref: string;
  let analysisId: string;
  try {
    ref = requireString(params.analysis_ref, "analysis_ref");
    const match = ref.match(/^analysis:(\d+)$/);
    if (!match) throw new Error("invalid analysis_ref");
    analysisId = match[1];
  } catch (error) {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: {
        code: "internal_error",
        message: error instanceof Error ? error.message : "analysis.delete 参数无效",
      },
    };
  }

  const python = getPythonBackendConfig();
  if (!python) {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "python_backend_unavailable", message: "Python 分析后端未就绪，请稍后重试" },
    };
  }

  const dir = join(getAnalysesDir(), analysisId);
  const dirExisted = existsSync(dir);
  let response: Response;
  try {
    response = await fetch(`${python.baseUrl}/api/sessions/${analysisId}`, {
      method: "DELETE",
      headers: {
        "X-Aiming-Cookie-Desktop-Token": python.token,
        "X-User-Id": ownerId || "desktop-local",
      },
      signal: signal ?? AbortSignal.timeout(ANALYSIS_DELETE_TIMEOUT_MS),
    });
  } catch {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: ref,
      warning_or_error: { code: "delete_failed", message: "Analysis 删除失败" },
    };
  }

  if (response.ok || response.status === 404) {
    // The Python session is gone (200) or already absent (404) — remove the
    // local disclosure directory so the Coach stops reading the deleted analysis.
    let cleanupPending = false;
    if (dirExisted) {
      try {
        rmSync(dir, { recursive: true, force: true });
      } catch {
        cleanupPending = true;
      }
    }
    if (response.status === 404 && !dirExisted) {
      return {
        status: "failed",
        command_id: commandId,
        audit_ref: auditRef,
        result_ref: ref,
        warning_or_error: { code: "not_found", message: "Analysis 不存在" },
      };
    }
    return {
      status: "succeeded",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: ref,
      result: { analysis_ref: ref, deleted: true, cleanup_pending: cleanupPending },
    };
  }

  let detail = "";
  try {
    detail = await response.text();
  } catch {
    // Non-text error body — fall through to the generic message.
  }
  return {
    status: "failed",
    command_id: commandId,
    audit_ref: auditRef,
    result_ref: ref,
    warning_or_error: {
      code: "delete_failed",
      message: detail ? `Analysis 删除失败: ${detail}` : "Analysis 删除失败",
    },
  };
}

// ── analysis.retry (native) ────────────────────────────────────────────
//
// Like analysis.delete, retry must go through the Python backend so the
// failed session is re-enqueued by the shared product command handler
// (queue state, idempotency and session status stay consistent).

const ANALYSIS_RETRY_TIMEOUT_MS = 15_000;

export function isNativeAnalysisRetryCommand(commandName: string): boolean {
  return commandName === "analysis.retry";
}

export async function executeNativeAnalysisRetry(
  commandName: string,
  params: AnyDict,
  ownerId: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<NativeWriteResult> {
  const commandId = newCommandId();
  const auditRef = newAuditRef();

  if (commandName !== "analysis.retry") {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "unknown_command", message: `${commandName} is not an analysis retry command` },
    };
  }

  let ref: string;
  let analysisId: string;
  try {
    ref = requireString(params.analysis_ref, "analysis_ref");
    const match = ref.match(/^analysis:(\d+)$/);
    if (!match) throw new Error("invalid analysis_ref");
    analysisId = match[1];
  } catch (error) {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: {
        code: "internal_error",
        message: error instanceof Error ? error.message : "analysis.retry 参数无效",
      },
    };
  }

  const python = getPythonBackendConfig();
  if (!python) {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: ref,
      warning_or_error: { code: "python_backend_unavailable", message: "Python 分析后端未就绪，请稍后重试" },
    };
  }

  let response: Response;
  try {
    response = await fetch(`${python.baseUrl}/api/sessions/${analysisId}/retry`, {
      method: "POST",
      headers: {
        "X-Aiming-Cookie-Desktop-Token": python.token,
        "X-User-Id": ownerId || "desktop-local",
        "Idempotency-Key": idempotencyKey,
      },
      signal: signal ?? AbortSignal.timeout(ANALYSIS_RETRY_TIMEOUT_MS),
    });
  } catch {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: ref,
      warning_or_error: { code: "retry_failed", message: "Analysis 重试失败" },
    };
  }

  if (response.ok) {
    let session: AnyDict | null = null;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object") session = body as AnyDict;
    } catch {
      // Non-JSON body — report the retry without session details.
    }
    return {
      status: "succeeded",
      command_id: commandId,
      audit_ref: auditRef,
      result_ref: ref,
      result: {
        analysis_ref: ref,
        retried: true,
        session_id: typeof session?.id === "number" ? session.id : Number(analysisId),
        session_status: typeof session?.status === "string" ? session.status : null,
      },
    };
  }

  let detail = "";
  try {
    detail = await response.text();
  } catch {
    // Non-text error body — fall through to the generic message.
  }
  return {
    status: "failed",
    command_id: commandId,
    audit_ref: auditRef,
    result_ref: ref,
    warning_or_error: {
      code: response.status === 404 ? "not_found" : "retry_failed",
      message: detail ? `Analysis 重试失败: ${detail}` : "Analysis 重试失败",
    },
  };
}

// ── Training plan commands ─────────────────────────────────────────────

function planPath(): string {
  return join(getTrainingDir(), "plan.json");
}

function readPlan(): AnyDict | null {
  return readJsonFile(planPath());
}

function writePlan(plan: AnyDict): void {
  writeJsonFile(planPath(), plan);
}

function planProjection(plan: AnyDict): AnyDict {
  return safePlan({
    plan_id: plan.plan_id,
    plan_ref: plan.plan_id,
    status: plan.status,
    version: plan.version ?? 1,
    version_ref: `${plan.plan_id}:v${plan.version ?? 1}`,
    plan_payload: plan.plan_payload,
    adjustment_reason: plan.adjustment_reason ?? null,
    evidence_refs: plan.evidence_refs ?? [],
    verification_targets: plan.verification_targets ?? [],
    created_at: plan.created_at,
    updated_at: plan.updated_at,
  });
}

// training_plan.generate_draft
const trainingPlanGenerateDraft: WriteHandler = (params, _ownerId) => {
  const payload = params.plan_payload;
  if (!payload || typeof payload !== "object") throw new Error("plan_payload is required");
  const evidenceRefs = Array.isArray(params.evidence_refs) ? params.evidence_refs : [];
  const verificationTargets = Array.isArray(params.verification_targets) ? params.verification_targets : [];
  const planId = `plan:${randomUUID().replace(/-/g, "")}`;
  const now = nowIso();
  const plan: AnyDict = {
    plan_id: planId,
    status: "draft",
    version: 1,
    plan_payload: payload,
    evidence_refs: evidenceRefs,
    verification_targets: verificationTargets,
    items: [],
    created_at: now,
    updated_at: now,
  };
  writePlan(plan);
  return ok(planProjection(plan), plan.plan_id);
};

// training_plan.save (draft → saved)
const trainingPlanSave: WriteHandler = (params, _ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const plan = readPlan();
  if (!plan || plan.plan_id !== planRef) {
    return fail("not_found", "Training Plan 不存在");
  }
  if (plan.status !== "draft") {
    return fail("invalid_training_plan", `cannot save a ${plan.status} plan; expected draft`);
  }
  plan.status = "saved";
  plan.updated_at = nowIso();
  writePlan(plan);
  return ok(planProjection(plan), plan.plan_id);
};

// training_plan.pause (active → paused)
const trainingPlanPause: WriteHandler = (params, _ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const plan = readPlan();
  if (!plan || plan.plan_id !== planRef) {
    return fail("not_found", "Training Plan 不存在");
  }
  if (plan.status !== "active") {
    return fail("invalid_training_plan", `cannot pause a ${plan.status} plan; expected active`);
  }
  plan.status = "paused";
  plan.updated_at = nowIso();
  writePlan(plan);
  return ok(planProjection(plan), plan.plan_id);
};

// training_plan.activate (saved/paused → active)
const trainingPlanActivate: WriteHandler = (params, _ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const plan = readPlan();
  if (!plan || plan.plan_id !== planRef) {
    return fail("not_found", "Training Plan 不存在");
  }
  if (plan.status !== "saved" && plan.status !== "paused") {
    return fail("invalid_training_plan", `cannot activate a ${plan.status} plan`);
  }
  plan.status = "active";
  plan.updated_at = nowIso();
  writePlan(plan);
  return ok(planProjection(plan), plan.plan_id);
};

// training_plan.adjust (new version)
const trainingPlanAdjust: WriteHandler = (params, _ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const payload = params.plan_payload;
  if (!payload || typeof payload !== "object") throw new Error("plan_payload is required");
  const adjustmentReason = requireString(params.adjustment_reason, "adjustment_reason");
  const evidenceRefs = Array.isArray(params.evidence_refs) ? params.evidence_refs : [];
  const verificationTargets = Array.isArray(params.verification_targets) ? params.verification_targets : [];

  const plan = readPlan();
  if (!plan || plan.plan_id !== planRef) {
    return fail("not_found", "Training Plan 不存在");
  }
  if (plan.status === "draft") {
    return fail("invalid_training_plan", "cannot adjust a draft plan before it is saved");
  }
  const nextVersion = (plan.version ?? 1) + 1;
  plan.version = nextVersion;
  plan.plan_payload = payload;
  plan.adjustment_reason = adjustmentReason;
  plan.evidence_refs = evidenceRefs;
  plan.verification_targets = verificationTargets;
  plan.updated_at = nowIso();
  writePlan(plan);
  return ok(planProjection(plan), plan.plan_id);
};

// ── Training plan item & execution commands ───────────────────────────

// training_plan.item.add
const trainingPlanItemAdd: WriteHandler = (params, _ownerId) => {
  const planRef = requireString(params.plan_ref, "plan_ref");
  const itemPayload = params.item_payload;
  if (!itemPayload || typeof itemPayload !== "object") throw new Error("item_payload is required");
  const plan = readPlan();
  if (!plan || plan.plan_id !== planRef) {
    return fail("not_found", "Training Plan 不存在");
  }
  const itemRef = `plan-item:${randomUUID().replace(/-/g, "")}`;
  const item: AnyDict = {
    item_ref: itemRef,
    plan_id: plan.plan_id,
    plan_version: plan.version ?? 1,
    item_revision: 1,
    status: "planned",
    ...itemPayload as AnyDict,
  };
  if (!Array.isArray(plan.items)) plan.items = [];
  plan.items.push(item);
  plan.updated_at = nowIso();
  writePlan(plan);
  return ok({
    item_ref: itemRef,
    plan_id: plan.plan_id,
    plan_revision: plan.version ?? 1,
    plan_revision_ref: `${plan.plan_id}:v${plan.version ?? 1}`,
    item_revision: 1,
    item_revision_ref: `${itemRef}:v1`,
    status: "planned",
    status_ref: null,
    ...itemPayload as AnyDict,
  }, itemRef);
};

// training_plan.execution.record / retest.record — append to history.jsonl
function appendHistory(record: AnyDict): void {
  const historyPath = join(getTrainingDir(), "history.jsonl");
  mkdirSync(dirname(historyPath), { recursive: true });
  appendFileSync(historyPath, JSON.stringify(record) + "\n", "utf-8");
}

// training_plan.execution.record
const trainingPlanExecutionRecord: WriteHandler = (params, _ownerId) => {
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
  const plan = readPlan();
  const record: AnyDict = {
    execution_ref: executionRef,
    item_ref: itemRef,
    plan_id: plan?.plan_id ?? null,
    plan_version: plan?.version ?? null,
    scenario_ref: scenarioRef,
    run_refs: runRefs,
    planned_dose: plannedDose,
    completed_dose: completedDose,
    completion_status: completionStatus,
    user_feedback: userFeedback,
    recorded_at: nowIso(),
  };
  appendHistory(record);
  return ok(record, executionRef);
};

// training_plan.retest.record
const trainingPlanRetestRecord: WriteHandler = (params, _ownerId) => {
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
  const record: AnyDict = {
    retest_ref: retestRef,
    item_ref: itemRef,
    kind,
    expected_metric_ref: expectedMetricRef,
    expected_direction: expectedDirection,
    analysis_refs: analysisRefs,
    comparability,
    result,
    limitations,
    recorded_at: nowIso(),
  };
  appendHistory(record);
  return ok(record, retestRef);
};

// ── Teaching session commands ─────────────────────────────────────────
//
// Guided teaching state is one owner-scoped JSON file, teaching/session.json.
// teaching_session.update is the single write entry point: it validates the
// phase transition (teaching-policy) and the restricted lesson fields, then
// writes directly — no confirmation round-trip by product decision.

const TEACHING_SESSION_SCHEMA = "coach_teaching_session.v1";
const TEACHING_LESSON_TEXT_FIELDS = ["observation", "hypothesis", "cue", "single_variable"] as const;
const TEACHING_LESSON_PATH_OR_URL = /(?:https?:\/\/|file:(?:\/\/)?|(?:^|[\s"'`([{=,:])[A-Za-z]:[\\/]|\\\\)/i;
const TEACHING_LESSON_UNSAFE_TEXT = /\b(?:api[_-]?key|authorization|credential|token|raw_trace|payload)\b/i;
const TEACHING_PHASES_THAT_COMPLETED_A_LESSON = new Set(["revise", "follow_up"]);

function sessionPath(): string {
  return join(getTeachingDir(), "session.json");
}

function readTeachingSession(): AnyDict | null {
  return readJsonFile(sessionPath());
}

function writeTeachingSession(session: AnyDict): void {
  writeJsonFile(sessionPath(), session);
}

function teachingSessionProjection(session: AnyDict): AnyDict {
  return {
    schema_version: session.schema_version,
    phase: session.phase,
    lesson: session.lesson,
    completed_lessons: session.completed_lessons,
    paused_reason: session.paused_reason,
    created_at: session.created_at,
    updated_at: session.updated_at,
  };
}

// teaching_session.update
const teachingSessionUpdate: WriteHandler = (params, _ownerId) => {
  const updates = params.updates;
  if (!updates || typeof updates !== "object" || Array.isArray(updates)) {
    return fail("invalid_teaching_session", "updates must be an object with phase, lesson or paused_reason");
  }
  const updateKeys = Object.keys(updates);
  if (updateKeys.length === 0) {
    return fail("invalid_teaching_session", "updates must contain at least one field");
  }
  for (const key of updateKeys) {
    if (key !== "phase" && key !== "lesson" && key !== "paused_reason") {
      return fail("invalid_teaching_session", `updates contains unsupported field: ${key}`);
    }
  }

  let nextPhase: string | null = null;
  if ("phase" in updates) {
    if (!isTeachingPhase(updates.phase)) {
      return fail("invalid_teaching_session", "phase is invalid");
    }
    nextPhase = updates.phase as string;
  }

  const lessonUpdates: AnyDict = {};
  if ("lesson" in updates) {
    const rawLesson = updates.lesson;
    if (!rawLesson || typeof rawLesson !== "object" || Array.isArray(rawLesson)) {
      return fail("invalid_teaching_session", "lesson must be an object");
    }
    const lessonKeys = Object.keys(rawLesson);
    if (lessonKeys.length === 0) {
      return fail("invalid_teaching_session", "lesson must contain at least one field");
    }
    for (const key of lessonKeys) {
      if (key !== "practice_refs" && !TEACHING_LESSON_TEXT_FIELDS.includes(key as typeof TEACHING_LESSON_TEXT_FIELDS[number])) {
        return fail("invalid_teaching_session", `lesson contains unsupported field: ${key}`);
      }
    }
    for (const key of TEACHING_LESSON_TEXT_FIELDS) {
      if (!(key in rawLesson)) continue;
      const value = rawLesson[key];
      if (value !== null && (typeof value !== "string" || value.length === 0 || value.length > 480 ||
          TEACHING_LESSON_PATH_OR_URL.test(value) || TEACHING_LESSON_UNSAFE_TEXT.test(value))) {
        return fail("invalid_teaching_session", `lesson.${key} is invalid`);
      }
      lessonUpdates[key] = value;
    }
    if ("practice_refs" in rawLesson) {
      const refs = rawLesson.practice_refs;
      if (!Array.isArray(refs) || refs.length > 8 ||
          refs.some((ref: unknown) => typeof ref !== "string" || ref.length === 0 || ref.length > 160)) {
        return fail("invalid_teaching_session", "lesson.practice_refs is invalid");
      }
      lessonUpdates.practice_refs = refs;
    }
  }

  let pausedReason: string | null | undefined;
  if ("paused_reason" in updates) {
    const value = updates.paused_reason;
    if (value !== null && (typeof value !== "string" || value.length === 0 || value.length > 480)) {
      return fail("invalid_teaching_session", "paused_reason is invalid");
    }
    pausedReason = value;
  }

  const existing = readTeachingSession();
  const now = nowIso();

  if (!existing) {
    if (nextPhase === null) {
      return fail("invalid_teaching_session", "phase is required when creating a teaching session");
    }
    if (nextPhase !== "intake") {
      return fail("invalid_teaching_transition", `cannot create a teaching session in ${nextPhase}; expected intake`);
    }
    const session: AnyDict = {
      schema_version: TEACHING_SESSION_SCHEMA,
      phase: nextPhase,
      lesson: Object.keys(lessonUpdates).length > 0 ? lessonUpdates : null,
      completed_lessons: [],
      paused_reason: pausedReason ?? null,
      created_at: now,
      updated_at: now,
    };
    writeTeachingSession(session);
    return ok(teachingSessionProjection(session), "teaching_session:current");
  }

  if (nextPhase !== null && nextPhase !== existing.phase) {
    const fromPhase = isTeachingPhase(existing.phase) ? existing.phase : "intake";
    if (!isTeachingPhaseTransitionAllowed(fromPhase, nextPhase)) {
      return fail("invalid_teaching_transition", `cannot move the teaching session from ${existing.phase} to ${nextPhase}`);
    }
  }

  const session: AnyDict = { ...existing };
  // Returning to intake starts a new lesson: a loop that reached revise or
  // follow_up archives the current lesson; any other restart drops it.
  if (nextPhase === "intake" && existing.phase !== "intake") {
    if (!Array.isArray(session.completed_lessons)) session.completed_lessons = [];
    if (TEACHING_PHASES_THAT_COMPLETED_A_LESSON.has(existing.phase as string) && session.lesson) {
      session.completed_lessons.push({ lesson: session.lesson, phase: existing.phase, completed_at: now });
    }
    session.lesson = null;
  }
  if (nextPhase !== null) session.phase = nextPhase;
  if (Object.keys(lessonUpdates).length > 0) {
    session.lesson = { ...(session.lesson ?? {}), ...lessonUpdates };
  }
  if (pausedReason !== undefined) {
    session.paused_reason = pausedReason;
  } else if (nextPhase !== null && nextPhase !== existing.phase && nextPhase !== "paused" &&
             (existing.phase === "paused" || existing.phase === "stopped_for_discomfort")) {
    session.paused_reason = null;
  }
  session.updated_at = now;
  writeTeachingSession(session);
  return ok(teachingSessionProjection(session), "teaching_session:current");
};

// ── Scenario memory command ───────────────────────────────────────────
//
// Scenario family memory is one owner-scoped JSON file,
// config/scenario-overrides.json. scenario_memory.set is the Coach's single
// write entry point after the user confirms a scenario's aim family once;
// the Python read side applies it above the heuristic identification chain
// (exact reviewed hashes keep priority).

const SCENARIO_OVERRIDES_SCHEMA = "scenario_overrides.v1";
const SCENARIO_OVERRIDE_FAMILIES = new Set([
  "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
]);
const SCENARIO_HASH_RE = /^[0-9a-f]{32}$/;
const SCENARIO_OVERRIDE_MAX_ENTRIES = 5000;

function scenarioOverridesPath(): string {
  return join(getConfigDir(), "scenario-overrides.json");
}

// scenario_memory.set
const scenarioMemorySet: WriteHandler = (params, _ownerId) => {
  const scenarioHash = params.scenario_hash;
  const aimFamily = params.aim_family;
  const note = params.note;
  if (typeof scenarioHash !== "string" || !SCENARIO_HASH_RE.test(scenarioHash)) {
    return fail("invalid_scenario_memory", "scenario_hash must be 32 lowercase hex characters");
  }
  if (typeof aimFamily !== "string" || !SCENARIO_OVERRIDE_FAMILIES.has(aimFamily)) {
    return fail(
      "invalid_scenario_memory",
      `aim_family must be one of ${[...SCENARIO_OVERRIDE_FAMILIES].sort().join(", ")}`,
    );
  }
  if (note !== undefined && note !== null && (typeof note !== "string" || note.length > 200)) {
    return fail("invalid_scenario_memory", "note must be a string of at most 200 characters");
  }
  const existing = readJsonFile(scenarioOverridesPath());
  const existingOverrides = existing && typeof existing === "object" && !Array.isArray(existing)
    && existing.overrides && typeof existing.overrides === "object" && !Array.isArray(existing.overrides)
    ? existing.overrides as AnyDict
    : {};
  const overrides: AnyDict = { ...existingOverrides };
  const trimmedNote = typeof note === "string" ? note.trim() : "";
  overrides[scenarioHash] = {
    aim_family: aimFamily,
    confirmed_by: "user",
    note: trimmedNote || null,
    updated_at: nowIso(),
  };
  if (Object.keys(overrides).length > SCENARIO_OVERRIDE_MAX_ENTRIES) {
    return fail("invalid_scenario_memory", "scenario memory exceeds the entry limit");
  }
  writeJsonFile(scenarioOverridesPath(), {
    schema_version: SCENARIO_OVERRIDES_SCHEMA,
    overrides,
  });
  return ok({
    schema_version: SCENARIO_OVERRIDES_SCHEMA,
    scenario_hash: scenarioHash,
    aim_family: aimFamily,
    confirmed_by: "user",
    note: overrides[scenarioHash].note,
    updated_at: overrides[scenarioHash].updated_at,
  }, `scenario_override:${scenarioHash}`);
};

// ── Handler registry ──────────────────────────────────────────────────

const HANDLERS: Record<string, WriteHandler> = {
  "calibration.save": calibrationSave,
  "calibration.delete": calibrationDelete,
  "kovaak.connection.disconnect": kovaakDisconnect,
  "peripheral_profile.update": peripheralProfileUpdate,
  "training_plan.generate_draft": trainingPlanGenerateDraft,
  "training_plan.save": trainingPlanSave,
  "training_plan.activate": trainingPlanActivate,
  "training_plan.pause": trainingPlanPause,
  "training_plan.adjust": trainingPlanAdjust,
  "training_plan.item.add": trainingPlanItemAdd,
  "training_plan.execution.record": trainingPlanExecutionRecord,
  "training_plan.retest.record": trainingPlanRetestRecord,
  "teaching_session.update": teachingSessionUpdate,
  "scenario_memory.set": scenarioMemorySet,
};

export function isNativeWriteCommand(commandName: string): boolean {
  return NATIVE_WRITE_COMMANDS.has(commandName);
}

// ── Main dispatch ─────────────────────────────────────────────────────

export function executeNativeWrite(
  commandName: string,
  params: AnyDict,
  ownerId: string,
  _idempotencyKey?: string,
): NativeWriteResult {
  const handler = HANDLERS[commandName];
  const commandId = newCommandId();
  const auditRef = newAuditRef();

  if (!handler) {
    return {
      status: "failed",
      command_id: commandId,
      audit_ref: auditRef,
      warning_or_error: { code: "unknown_command", message: `${commandName} is not a native write command` },
    };
  }

  let handlerResult: HandlerResult;
  try {
    handlerResult = handler(params, ownerId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "product command could not be completed";
    if (message.startsWith("not_found:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "not_found", message: message.slice("not_found:".length) } };
    } else if (message.startsWith("forbidden:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "forbidden", message: message.slice("forbidden:".length) } };
    } else if (message.startsWith("invalid_transition:")) {
      handlerResult = { status: "failed", warning_or_error: { code: "invalid_training_plan", message: message.slice("invalid_transition:".length) } };
    } else if (isSafeHandlerMessage(message)) {
      // Surface the real reason (e.g. "plan_payload is required") so the model
      // can self-correct; only bounded, path/secret-free messages pass (Bug 2).
      handlerResult = { status: "failed", warning_or_error: { code: "internal_error", message } };
    } else {
      handlerResult = { status: "failed", warning_or_error: { code: "internal_error", message: "product command could not be completed" } };
    }
  }

  const result: AnyDict = {
    schema_version: "coach_product_command_result.v1",
    command_id: commandId,
    status: handlerResult.status,
    audit_ref: auditRef,
  };
  if (handlerResult.result_ref !== undefined) result.result_ref = handlerResult.result_ref;
  if (handlerResult.result !== undefined) result.result = handlerResult.result;
  if (handlerResult.ui_event) result.ui_event = handlerResult.ui_event;
  if (handlerResult.warning_or_error) result.warning_or_error = handlerResult.warning_or_error;
  return result as NativeWriteResult;
}
