/**
 * Native SQLite implementations of read-only product commands.
 *
 * These commands query the same SQLite database that the Python backend owns,
 * eliminating the HTTP tool bridge round-trip for read operations.
 *
 * Each function mirrors the Python implementation's SQL and return shape.
 * Owner filtering is always applied. No security sanitization is performed —
 * this is a single-user desktop app and the data belongs to the user.
 */
import type { SqliteDb } from "./db.ts";

// ── Types ──────────────────────────────────────────────────────────────

export type NativeCommandResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

type CommandHandler = (
  db: SqliteDb,
  parameters: Record<string, unknown>,
  ownerId: string,
) => NativeCommandResult;

// ── Helpers ────────────────────────────────────────────────────────────

function parseJsonColumn(value: unknown): unknown | null {
  if (typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

/** Convert SQLite "YYYY-MM-DD HH:MM:SS" timestamp to ISO-8601 with Z suffix. */
function sqliteTimestampToWireUtc(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  // SQLite CURRENT_TIMESTAMP is "YYYY-MM-DD HH:MM:SS" in UTC.
  // Replace space with T, append Z.
  return value.includes("T") ? value : value.replace(" ", "T") + "Z";
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) {
    throw new Error(`${field} is required`);
  }
  return value;
}

function parseAnalysisRef(ref: unknown): number {
  if (typeof ref === "number" && Number.isInteger(ref) && ref > 0) return ref;
  if (typeof ref === "string") {
    const match = ref.match(/^analysis:(\d+)$/);
    if (match) return parseInt(match[1], 10);
    const parsed = parseInt(ref, 10);
    if (Number.isInteger(parsed) && parsed > 0) return parsed;
  }
  throw new Error("invalid analysis_ref");
}

// ── Simple commands ────────────────────────────────────────────────────

const calibrationGet: CommandHandler = (db, _params, ownerId) => {
  const row = db.prepare(
    "SELECT cm_per_360, fov, updated_at FROM calibration_profiles WHERE owner_id = ?",
  ).get(ownerId) as Record<string, unknown> | undefined;
  if (!row) {
    return {
      status: "succeeded",
      result: {
        schema_version: "calibration_profile.v1",
        configured: false,
        values: { cm_per_360: null, fov: null },
        dpi: null,
        sensitivity: null,
        adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
        updated_at: null,
      },
    };
  }
  return {
    status: "succeeded",
    result: {
      schema_version: "calibration_profile.v1",
      configured: true,
      values: {
        cm_per_360: row.cm_per_360 as number | null,
        fov: row.fov as number | null,
      },
      dpi: null,
      sensitivity: null,
      adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
      updated_at: sqliteTimestampToWireUtc(row.updated_at),
    },
  };
};

const kovaakConnectionGet: CommandHandler = (db, _params, ownerId) => {
  const row = db.prepare(
    "SELECT owner_id, steam_id, connected_at, updated_at FROM kovaak_connections WHERE owner_id = ?",
  ).get(ownerId) as Record<string, unknown> | undefined;
  return {
    status: "succeeded",
    result_ref: "kovaak_connection:current",
    result: { connection_ref: "kovaak_connection:current", connected: row !== undefined },
  };
};

const peripheralProfileGet: CommandHandler = (db, _params, ownerId) => {
  const row = db.prepare(
    `SELECT owner_id, grip_type, hand_length_mm, wrist_rest_position,
            current_mouse_shape, current_mouse_weight_g, current_mousepad,
            budget_tier, preferred_flare, preferred_hump, preferred_size_category,
            created_at, updated_at
     FROM peripheral_profiles WHERE owner_id = ?`,
  ).get(ownerId) as Record<string, unknown> | undefined;
  if (!row) {
    return {
      status: "succeeded",
      result: { schema_version: "peripheral_profile.v1", configured: false, owner_ref: ownerId },
    };
  }
  return {
    status: "succeeded",
    result: {
      schema_version: "peripheral_profile.v1",
      configured: true,
      owner_ref: ownerId,
      profile_ref: `peripheral:${ownerId}`,
      grip_type: row.grip_type ?? null,
      hand_length_mm: row.hand_length_mm ?? null,
      wrist_rest_position: row.wrist_rest_position ?? null,
      current_mouse_shape: row.current_mouse_shape ?? null,
      current_mouse_weight_g: row.current_mouse_weight_g ?? null,
      current_mousepad: row.current_mousepad ?? null,
      budget_tier: row.budget_tier ?? null,
      preferred_flare: row.preferred_flare ?? null,
      preferred_hump: row.preferred_hump ?? null,
      preferred_size_category: row.preferred_size_category ?? null,
      updated_at: sqliteTimestampToWireUtc(row.updated_at),
    },
  };
};

function navigationOpen(parameters: Record<string, unknown>): NativeCommandResult {
  const target = requireString(parameters.target, "target");
  const validTargets = ["history", "analysis", "analysis_section", "flick_event", "evidence", "video_time"];
  if (!validTargets.includes(target)) {
    return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "unsupported navigation target" } };
  }
  const event: Record<string, unknown> = { schema_version: "coach_ui_event.v1" };
  if (target === "history") {
    event.kind = "history";
  } else if (target === "analysis" || target === "analysis_section") {
    event.kind = "analysis";
    event.analysis_ref = requireString(parameters.analysis_ref, "analysis_ref");
    if (target === "analysis_section") {
      const section = requireString(parameters.section, "section");
      const validSections = ["overview", "metrics", "diagnosis", "training", "evidence"];
      if (!validSections.includes(section)) {
        return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "unsupported analysis section" } };
      }
      event.section = section;
    }
  } else if (target === "flick_event") {
    event.kind = "flick";
    event.analysis_ref = requireString(parameters.analysis_ref, "analysis_ref");
    event.event_ref = requireString(parameters.event_ref, "event_ref");
  } else if (target === "evidence") {
    event.kind = "evidence";
    event.analysis_ref = requireString(parameters.analysis_ref, "analysis_ref");
    event.evidence_ref = requireString(parameters.evidence_ref, "evidence_ref");
  } else if (target === "video_time") {
    event.kind = "video_time";
    event.analysis_ref = requireString(parameters.analysis_ref, "analysis_ref");
    const timeMs = parameters.time_ms;
    if (typeof timeMs !== "number" || !Number.isFinite(timeMs)) {
      return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "time_ms must be a number" } };
    }
    event.time_ms = timeMs;
  }
  return { status: "succeeded", result: event };
}

// ── Medium commands ────────────────────────────────────────────────────

const analysisGet: CommandHandler = (db, params, ownerId) => {
  const analysisId = parseAnalysisRef(params.analysis_ref);
  const row = db.prepare(
    `SELECT id, user_id, status, analysis_type, input_mode, kovaak_run_id,
            error, created_at, started_at, finished_at
     FROM sessions WHERE id = ?`,
  ).get(analysisId) as Record<string, unknown> | undefined;
  if (!row) {
    return { status: "failed", warning_or_error: { code: "not_found", message: "Analysis 不存在" } };
  }
  if (row.user_id !== ownerId) {
    return { status: "failed", warning_or_error: { code: "forbidden", message: "无权访问此 Analysis" } };
  }
  const error = parseJsonColumn(row.error);
  let safeError = null;
  if (error && typeof error === "object") {
    const e = error as Record<string, unknown>;
    safeError = {};
    for (const key of ["schema_version", "category", "code", "message", "retryable"]) {
      if (key in e) (safeError as Record<string, unknown>)[key] = e[key];
    }
  }
  return {
    status: "succeeded",
    result_ref: `analysis:${analysisId}`,
    result: {
      analysis_ref: `analysis:${row.id}`,
      id: row.id,
      status: row.status,
      analysis_type: (row.analysis_type as string) ?? "flicking",
      input_mode: (row.input_mode as string) ?? "video_fallback",
      run_ref: row.kovaak_run_id ? `run:${row.kovaak_run_id}` : null,
      created_at: sqliteTimestampToWireUtc(row.created_at),
      started_at: sqliteTimestampToWireUtc(row.started_at),
      finished_at: sqliteTimestampToWireUtc(row.finished_at),
      error: safeError,
    },
  };
};

const coachSessionList: CommandHandler = (db, params, ownerId) => {
  const includeArchived = params.include_archived === true;
  const statuses = includeArchived ? ["active", "archived"] : ["active"];
  const placeholders = statuses.map(() => "?").join(",");
  const limit = Math.min(Math.max(typeof params.limit === "number" ? params.limit : 100, 1), 200);
  const rows = db.prepare(
    `SELECT t.id, t.user_id, t.kind, t.title, t.status, t.deleted_at,
            t.created_at, t.updated_at,
            COUNT(m.id) AS message_count,
            (SELECT content FROM coach_messages lm
             WHERE lm.thread_id = t.id ORDER BY lm.id DESC LIMIT 1) AS last_message_preview
     FROM coach_threads t
     LEFT JOIN coach_messages m ON m.thread_id = t.id
     WHERE t.user_id = ? AND t.status IN (${placeholders})
       AND (t.kind <> 'primary' OR EXISTS (
         SELECT 1 FROM coach_messages pm WHERE pm.thread_id = t.id))
     GROUP BY t.id
     ORDER BY t.updated_at DESC, t.id DESC LIMIT ?`,
  ).all(ownerId, ...statuses, limit) as Array<Record<string, unknown>>;

  const items = rows.map((row) => {
    const analysisIds = db.prepare(
      `SELECT analysis_session_id FROM coach_analysis_refs
       WHERE thread_id = ? AND status = 'active' AND analysis_session_id IS NOT NULL
       ORDER BY id`,
    ).all(row.id) as Array<{ analysis_session_id: number }>;
    return {
      id: row.id,
      user_id: row.user_id,
      kind: row.kind,
      title: row.title ?? null,
      status: row.status,
      deleted_at: row.deleted_at ?? null,
      created_at: sqliteTimestampToWireUtc(row.created_at),
      updated_at: sqliteTimestampToWireUtc(row.updated_at),
      message_count: row.message_count,
      last_message_preview: row.last_message_preview ?? null,
      analysis_session_ids: analysisIds.map((r) => r.analysis_session_id),
    };
  });
  return { status: "succeeded", result: items };
};

const productReadinessGet: CommandHandler = (db, _params, ownerId) => {
  const productState = db.prepare(
    "SELECT * FROM product_state WHERE owner_id = ?",
  ).get(ownerId) as Record<string, unknown> | undefined;
  const kovaakConnection = db.prepare(
    "SELECT 1 FROM kovaak_connections WHERE owner_id = ?",
  ).get(ownerId) !== undefined;
  const calibration = db.prepare(
    "SELECT cm_per_360, fov FROM calibration_profiles WHERE owner_id = ?",
  ).get(ownerId) as Record<string, unknown> | undefined;
  const peripheral = db.prepare(
    "SELECT 1 FROM peripheral_profiles WHERE owner_id = ?",
  ).get(ownerId) !== undefined;
  return {
    status: "succeeded",
    result: {
      schema_version: "product_readiness.v1",
      has_completed_onboarding: productState !== undefined,
      has_kovaak_connection: kovaakConnection,
      has_calibration: calibration !== undefined && (calibration.cm_per_360 !== null || calibration.fov !== null),
      has_peripheral_profile: peripheral,
    },
  };
};

// ── Complex commands ───────────────────────────────────────────────────

const historyList: CommandHandler = (db, _params, ownerId) => {
  const rows = db.prepare(
    `SELECT s.id, s.status, s.created_at, s.finished_at, s.attempts,
            s.max_attempts, s.llm_cost_cny, s.analysis_type, s.input_mode,
            s.kovaak_run_id,
            CASE WHEN json_valid(s.result) THEN COALESCE(
              json_extract(s.result, '$.deterministic.diagnosis.profile.label'),
              json_extract(s.result, '$.diagnosis.profile.label')) END AS summary_label,
            COALESCE(
              CASE WHEN json_valid(s.input_snapshot_json) THEN json_extract(s.input_snapshot_json, '$.scenario') END,
              kr.scenario) AS scenario,
            kr.created_at AS training_at,
            CASE WHEN json_valid(s.result) THEN json_extract(s.result, '$.evidence.alignment.status') END AS alignment_status,
            CASE WHEN json_valid(s.result) THEN json_extract(s.result, '$.evidence.coverage') END AS evidence_coverage,
            kr.trace_state AS run_trace_state
     FROM sessions AS s
     LEFT JOIN kovaak_runs AS kr ON kr.id = s.kovaak_run_id AND kr.user_id = s.user_id
     WHERE s.user_id = ?
     ORDER BY s.created_at DESC, s.id DESC`,
  ).all(ownerId) as Array<Record<string, unknown>>;

  const items = rows.map((row) => {
    const analysisType = (row.analysis_type as string) ?? "flicking";
    const inputMode = (row.input_mode as string) ?? "video_fallback";
    return {
      id: row.id,
      analysis_ref: `analysis:${row.id}`,
      run_ref: row.kovaak_run_id ? `run:${row.kovaak_run_id}` : null,
      status: row.status,
      created_at: sqliteTimestampToWireUtc(row.created_at),
      finished_at: sqliteTimestampToWireUtc(row.finished_at),
      attempts: row.attempts,
      max_attempts: row.max_attempts,
      llm_cost_cny: row.llm_cost_cny,
      summary_label: row.summary_label ?? null,
      analysis_type: analysisType,
      input_mode: inputMode,
      kovaak_run_id: row.kovaak_run_id ?? null,
      scenario: row.scenario ?? null,
      training_at: sqliteTimestampToWireUtc(row.training_at),
      analysis_completed_at: sqliteTimestampToWireUtc(row.finished_at),
    };
  });
  return { status: "succeeded", result: items };
};

const historyTrend: CommandHandler = (db, params, ownerId) => {
  const metricKey = requireString(params.metric_key, "metric_key");
  const rows = db.prepare(
    `SELECT id, result FROM sessions
     WHERE user_id = ? AND status = 'done' AND result IS NOT NULL
     ORDER BY created_at DESC, id DESC LIMIT 100`,
  ).all(ownerId) as Array<{ id: number; result: string }>;

  const parsed = rows
    .map((r) => ({ id: r.id, result: parseJsonColumn(r.result) as Record<string, unknown> | null }))
    .filter((r) => r.result !== null && r.result.schema_version === "analysis_result.v2");

  if (parsed.length < 2) {
    return {
      status: "succeeded",
      result: { comparable: false, reason: "insufficient_history" },
    };
  }

  const current = parsed[0];
  for (let i = 1; i < parsed.length; i++) {
    const baseline = parsed[i];
    const comparison = compareAnalysisResults(current.result!, baseline.result!, metricKey);
    if (comparison.comparable) {
      return {
        status: "succeeded",
        result: {
          ...comparison,
          current_session_id: current.id,
          baseline_session_id: baseline.id,
        },
      };
    }
  }
  return {
    status: "succeeded",
    result: { comparable: false, reason: "no_comparable_baseline", current_session_id: current.id },
  };
};

const analysisCompare: CommandHandler = (db, params, ownerId) => {
  const currentId = parseAnalysisRef(params.current_analysis_ref);
  const baselineId = parseAnalysisRef(params.baseline_analysis_ref);
  const metricKey = requireString(params.metric_key, "metric_key");

  const currentRow = db.prepare(
    "SELECT user_id, status, result FROM sessions WHERE id = ?",
  ).get(currentId) as Record<string, unknown> | undefined;
  const baselineRow = db.prepare(
    "SELECT user_id, status, result FROM sessions WHERE id = ?",
  ).get(baselineId) as Record<string, unknown> | undefined;

  if (!currentRow || !baselineRow) {
    return { status: "failed", warning_or_error: { code: "not_found", message: "Analysis 不存在" } };
  }
  if (currentRow.user_id !== ownerId || baselineRow.user_id !== ownerId) {
    return { status: "failed", warning_or_error: { code: "forbidden", message: "无权访问此 Analysis" } };
  }
  if (currentRow.status !== "done" || baselineRow.status !== "done") {
    return { status: "failed", warning_or_error: { code: "analysis_not_ready", message: "Analysis 尚未完成" } };
  }
  const currentResult = parseJsonColumn(currentRow.result) as Record<string, unknown> | null;
  const baselineResult = parseJsonColumn(baselineRow.result) as Record<string, unknown> | null;
  if (!currentResult || !baselineResult) {
    return { status: "failed", warning_or_error: { code: "analysis_result_missing", message: "Analysis 结果不可用" } };
  }
  const comparison = compareAnalysisResults(currentResult, baselineResult, metricKey);
  return {
    status: "succeeded",
    result: {
      ...comparison,
      current_analysis_ref: `analysis:${currentId}`,
      baseline_analysis_ref: `analysis:${baselineId}`,
    },
  };
};

// ── Comparison logic (ported from history_trends.compare_analysis_results) ──

const SAFE_IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const SAFE_SCENARIO_HASH_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/;
const SAFE_SCENARIO_PROFILE_REF_RE = /^scenario:[A-Za-z0-9][A-Za-z0-9._-]{0,159}@[1-9][0-9]*$/;
const V2_INPUT_MODES = new Set(["input_native", "multimodal", "video_fallback"]);
const TARGET_SWITCHING_CHAIN_CONDITION_REF = "condition:target_switching:stats_kill_bounded_chain";
const TARGET_SWITCHING_COMPARISON_METRICS = new Set([
  "target_switching.transition_time_ms",
  "target_switching.settle_duration_ms",
]);

const SCENARIO_RESOLUTION_FIELDS = new Set([
  "schema_version", "scenario_hash", "display_name", "registry_version",
  "manifest_version", "scenario_profile_ref", "classification_source",
  "classification_confidence", "profile_status", "reviewed_at",
  "source_refs", "supersedes", "manifest_status", "fixture_ref",
  "review_source_ref", "manifest_reviewed_at", "family_gate_refs",
  "aim_family", "subdomains", "target_motion", "allowed_analyzers",
  "allowed_metric_families", "claim_ceiling", "family_analyzer_dispatch",
  "limitations",
]);

function safeIdentity(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    !SAFE_IDENTITY_RE.test(value) ||
    value.toLowerCase().startsWith("file:") ||
    /^[A-Za-z]:/.test(value)
  ) {
    return null;
  }
  return value;
}

function safeInputMode(value: unknown): string | null {
  return typeof value === "string" && V2_INPUT_MODES.has(value) ? value : null;
}

function safeScenario(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const scenario = value.trim();
  const lowered = scenario.toLowerCase();
  if (
    scenario.startsWith("\\") ||
    /^[A-Za-z]:[\\/]/.test(scenario) ||
    lowered.startsWith("file:") ||
    /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(lowered)
  ) {
    return null;
  }
  return scenario;
}

function getResultMetric(result: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const det = result.deterministic;
  if (!det || typeof det !== "object") return null;
  const metrics = (det as Record<string, unknown>).metrics;
  if (!metrics || typeof metrics !== "object") return null;
  const value = (metrics as Record<string, unknown>)[key];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function analysisVersion(result: Record<string, unknown>): string | null {
  return "analysis_version" in result ? safeIdentity(result.analysis_version) : null;
}

function timebaseVersion(result: Record<string, unknown>): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  const window = (snapshot as Record<string, unknown>).canonical_time_window;
  if (!window || typeof window !== "object") return null;
  const value = (window as Record<string, unknown>).timebase_version;
  return value != null ? safeIdentity(value) : null;
}

function scenarioResolution(result: Record<string, unknown>): Record<string, unknown> | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  const resolution = (snapshot as Record<string, unknown>).scenario_resolution;
  return resolution && typeof resolution === "object" ? (resolution as Record<string, unknown>) : null;
}

function validateScenarioResolutionV1(value: unknown): void {
  if (!value || typeof value !== "object") throw new Error("scenario_resolution must be a dict");
  const obj = value as Record<string, unknown>;
  const keys = new Set(Object.keys(obj));
  for (const f of SCENARIO_RESOLUTION_FIELDS) {
    if (!keys.has(f)) throw new Error("scenario_resolution fields are invalid");
  }
  for (const k of keys) {
    if (!SCENARIO_RESOLUTION_FIELDS.has(k)) throw new Error("scenario_resolution fields are invalid");
  }
  if (obj.schema_version !== "scenario_resolution.v1") throw new Error("unsupported contract version");
  const scenarioHash = obj.scenario_hash;
  if (scenarioHash != null && (typeof scenarioHash !== "string" || !SAFE_SCENARIO_HASH_RE.test(scenarioHash))) {
    throw new Error("scenario_resolution.scenario_hash is invalid");
  }
  const profileRef = obj.scenario_profile_ref;
  if (profileRef != null && (typeof profileRef !== "string" || !SAFE_SCENARIO_PROFILE_REF_RE.test(profileRef))) {
    throw new Error("scenario_resolution.scenario_profile_ref is invalid");
  }
  for (const field of ["registry_version", "manifest_version"]) {
    const v = obj[field];
    if (typeof v !== "string" || !v.trim() || v.length > 80) {
      throw new Error(`scenario_resolution.${field} is invalid`);
    }
  }
}

function scenarioResolutionIsInvalid(result: Record<string, unknown>): boolean {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return false;
  const snap = snapshot as Record<string, unknown>;
  const resolution = snap.scenario_resolution;
  if (resolution == null) {
    return snap.schema_version === "analysis_input_snapshot.v3";
  }
  try {
    validateScenarioResolutionV1(resolution);
    return false;
  } catch {
    return true;
  }
}

function scenarioHash(result: Record<string, unknown>): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  const value = resolution.scenario_hash;
  return typeof value === "string" && SAFE_SCENARIO_HASH_RE.test(value) ? value : null;
}

function scenarioProfileRef(result: Record<string, unknown>): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  const value = resolution.scenario_profile_ref;
  return typeof value === "string" && SAFE_SCENARIO_PROFILE_REF_RE.test(value) ? value : null;
}

function scenarioRegistryVersion(result: Record<string, unknown>): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  return safeIdentity(resolution.registry_version);
}

function scenarioName(result: Record<string, unknown>): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  return safeScenario((snapshot as Record<string, unknown>).scenario);
}

function scenarioIdentityVersion(result: Record<string, unknown>): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  return safeIdentity((snapshot as Record<string, unknown>).scenario_identity_version);
}

function fullCoverage(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value === 1.0;
}

function hasCompleteSwitchingMetricEvidence(
  result: Record<string, unknown>,
  metric: Record<string, unknown>,
): boolean {
  if (result.analysis_type !== "target_switching") return false;
  const det = result.deterministic;
  if (!det || typeof det !== "object") return false;
  if ((det as Record<string, unknown>).support_status !== "supported") return false;
  if (!TARGET_SWITCHING_COMPARISON_METRICS.has(metric.key as string)) return false;
  const conditionRefs = metric.condition_refs;
  if (!Array.isArray(conditionRefs) || conditionRefs.length !== 1 || conditionRefs[0] !== TARGET_SWITCHING_CHAIN_CONDITION_REF) return false;
  const evidence = result.evidence;
  if (!evidence || typeof evidence !== "object") return false;
  const coverage = (evidence as Record<string, unknown>).coverage;
  return typeof coverage === "number" && Number.isFinite(coverage) && coverage > 0.0 && coverage <= 1.0;
}

function qualityReason(
  result: Record<string, unknown>,
  metric: Record<string, unknown>,
): string | null {
  if (metric.classification !== "deterministic") return "metric_not_deterministic";
  if (metric.availability !== "available") return "metric_unavailable";
  if (!fullCoverage(metric.coverage)) return "insufficient_metric_coverage";
  const evidence = result.evidence;
  if (!evidence || typeof evidence !== "object") return "insufficient_evidence_coverage";
  const evidenceObj = evidence as Record<string, unknown>;
  if (!fullCoverage(evidenceObj.coverage) && !hasCompleteSwitchingMetricEvidence(result, metric)) {
    return "insufficient_evidence_coverage";
  }
  const alignmentValue = evidenceObj.alignment;
  const alignment = alignmentValue && typeof alignmentValue === "object"
    ? (alignmentValue as Record<string, unknown>).status
    : null;
  if (alignment !== "aligned" && alignment !== "not_required") return "insufficient_alignment_quality";
  return null;
}

function familyComparabilityReason(
  current: Record<string, unknown>,
  baseline: Record<string, unknown>,
  currentMetric: Record<string, unknown>,
  baselineMetric: Record<string, unknown>,
  missingReason: string,
): string | null {
  const currentDet = current.deterministic;
  const baselineDet = baseline.deterministic;
  if (!currentDet || typeof currentDet !== "object" || !baselineDet || typeof baselineDet !== "object") {
    return missingReason;
  }
  const currentProfile = safeIdentity((currentDet as Record<string, unknown>).visual_quality_profile_ref);
  const baselineProfile = safeIdentity((baselineDet as Record<string, unknown>).visual_quality_profile_ref);
  if (!currentProfile || !baselineProfile) return "visual_quality_profile_missing";
  if (currentProfile !== baselineProfile) return "visual_quality_profile_mismatch";
  const currentMotion = safeIdentity((currentDet as Record<string, unknown>).scenario_motion_class);
  const baselineMotion = safeIdentity((baselineDet as Record<string, unknown>).scenario_motion_class);
  if (!currentMotion || currentMotion !== baselineMotion) return "motion_condition_mismatch";
  const conditionSets: string[][] = [];
  for (const metric of [currentMetric, baselineMetric]) {
    const rawRefs = metric.condition_refs;
    if (!Array.isArray(rawRefs) || rawRefs.length === 0) return "metric_condition_missing";
    const refs = rawRefs
      .map((r: unknown) => safeIdentity(r))
      .filter((r): r is string => r !== null)
      .sort();
    if (refs.length !== rawRefs.length) return "metric_condition_missing";
    conditionSets.push(refs);
  }
  if (JSON.stringify(conditionSets[0]) !== JSON.stringify(conditionSets[1])) return "metric_condition_mismatch";
  return null;
}

function compareAnalysisResults(
  current: Record<string, unknown>,
  baseline: Record<string, unknown>,
  metricKey: string,
): Record<string, unknown> {
  if (current.schema_version !== "analysis_result.v2" || baseline.schema_version !== "analysis_result.v2") {
    return { comparable: false, reason: "analysis_result_version_mismatch" };
  }

  // Analysis version must match (when either side carries one).
  if ("analysis_version" in current || "analysis_version" in baseline) {
    const cv = analysisVersion(current);
    const bv = analysisVersion(baseline);
    if (!cv || cv !== bv) {
      return { comparable: false, reason: "analysis_version_mismatch" };
    }
  }

  // Timebase version must match (when either side has a canonical_time_window).
  const currentSnapshot = current.input_snapshot;
  const baselineSnapshot = baseline.input_snapshot;
  const currentHasWindow = currentSnapshot && typeof currentSnapshot === "object" &&
    "canonical_time_window" in (currentSnapshot as Record<string, unknown>);
  const baselineHasWindow = baselineSnapshot && typeof baselineSnapshot === "object" &&
    "canonical_time_window" in (baselineSnapshot as Record<string, unknown>);
  if (currentHasWindow || baselineHasWindow) {
    const ct = timebaseVersion(current);
    const bt = timebaseVersion(baseline);
    if (!ct || ct !== bt) {
      return { comparable: false, reason: "timebase_version_mismatch" };
    }
  }

  // Scenario resolution must be structurally valid.
  if (scenarioResolutionIsInvalid(current) || scenarioResolutionIsInvalid(baseline)) {
    return { comparable: false, reason: "scenario_resolution_invalid" };
  }

  // Build the predicate list (resolution-aware vs. legacy).
  const currentResolution = scenarioResolution(current);
  const baselineResolution = scenarioResolution(baseline);
  type Predicate = [string, string | null, string | null];
  let scenarioPredicates: Predicate[];
  if (currentResolution !== null || baselineResolution !== null) {
    scenarioPredicates = [
      ["scenario_hash", scenarioHash(current), scenarioHash(baseline)],
      ["scenario_profile_ref", scenarioProfileRef(current), scenarioProfileRef(baseline)],
      ["scenario_registry_version", scenarioRegistryVersion(current), scenarioRegistryVersion(baseline)],
    ];
  } else {
    scenarioPredicates = [
      ["scenario", scenarioName(current), scenarioName(baseline)],
      ["scenario_identity_version", scenarioIdentityVersion(current), scenarioIdentityVersion(baseline)],
    ];
  }
  const predicates: Predicate[] = [
    ["analysis_type", safeIdentity(current.analysis_type), safeIdentity(baseline.analysis_type)],
    ...scenarioPredicates,
    ["input_mode", safeInputMode(current.input_mode), safeInputMode(baseline.input_mode)],
  ];
  for (const [name, left, right] of predicates) {
    if (!left || left !== right) {
      return { comparable: false, reason: `${name}_mismatch` };
    }
  }

  // Metric lookup and key validation.
  if (safeIdentity(metricKey) === null) {
    return { comparable: false, reason: "metric_key_mismatch" };
  }
  const currentMetric = getResultMetric(current, metricKey);
  const baselineMetric = getResultMetric(baseline, metricKey);
  if (!currentMetric || !baselineMetric) {
    return { comparable: false, reason: "metric_missing" };
  }
  if (safeIdentity(currentMetric.key) !== metricKey || safeIdentity(baselineMetric.key) !== metricKey) {
    return { comparable: false, reason: "metric_key_mismatch" };
  }

  // Family-specific comparability (dynamic_clicking / tracking / switching).
  const familyMissingReason = ({
    dynamic_clicking: "dynamic_comparability_missing",
    continuous_tracking: "continuous_tracking_comparability_missing",
    target_switching: "target_switching_comparability_missing",
  } as Record<string, string>)[current.analysis_type as string];
  if (familyMissingReason) {
    const reason = familyComparabilityReason(current, baseline, currentMetric, baselineMetric, familyMissingReason);
    if (reason) {
      return { comparable: false, reason };
    }
  }

  // Quality checks (classification, availability, coverage, alignment).
  for (const [result, metric] of [
    [current, currentMetric],
    [baseline, baselineMetric],
  ] as [Record<string, unknown>, Record<string, unknown>][]) {
    const reason = qualityReason(result, metric);
    if (reason) {
      return { comparable: false, reason };
    }
  }

  // Metric version and unit must match.
  const currentMetricVersion = safeIdentity(currentMetric.metric_version);
  const baselineMetricVersion = safeIdentity(baselineMetric.metric_version);
  if (!currentMetricVersion || currentMetricVersion !== baselineMetricVersion) {
    return { comparable: false, reason: "metric_version_mismatch" };
  }
  const currentUnit = safeIdentity(currentMetric.unit);
  const baselineUnit = safeIdentity(baselineMetric.unit);
  if (!currentUnit || currentUnit !== baselineUnit) {
    return { comparable: false, reason: "metric_unit_mismatch" };
  }

  // Calibration must be present and match.
  const currentCalibration = safeIdentity(currentMetric.calibration_ref);
  const baselineCalibration = safeIdentity(baselineMetric.calibration_ref);
  if (!currentCalibration || !baselineCalibration) {
    return { comparable: false, reason: "calibration_compatibility_missing" };
  }
  if (currentCalibration !== baselineCalibration) {
    return { comparable: false, reason: "calibration_mismatch" };
  }

  // Value must be finite numbers.
  const currentValue = currentMetric.value;
  const baselineValue = baselineMetric.value;
  if (
    typeof currentValue === "boolean" || typeof baselineValue === "boolean" ||
    typeof currentValue !== "number" || typeof baselineValue !== "number" ||
    !Number.isFinite(currentValue) || !Number.isFinite(baselineValue)
  ) {
    return { comparable: false, reason: "metric_value_invalid" };
  }

  const delta = currentValue - baselineValue;
  const percentChange = baselineValue !== 0 ? (delta / Math.abs(baselineValue)) * 100 : null;

  return {
    comparable: true,
    reason: null,
    classification: "deterministic",
    metric_key: metricKey,
    unit: currentUnit,
    metric_version: currentMetricVersion,
    current: currentValue,
    baseline: baselineValue,
    delta,
    percent_change: percentChange,
  };
}

// ── run.list ───────────────────────────────────────────────────────────

const runList: CommandHandler = (db, _params, ownerId) => {
  const rows = db.prepare(
    `SELECT kr.id, kr.source_key, kr.scenario, kr.trace_state,
            kr.alignment_state, kr.alignment_summary,
            kr.finalization_state, kr.video_path, kr.video_state,
            kr.created_at, kr.updated_at,
            json_extract(kr.stats_summary, '$.config.FOV') AS stats_fov,
            json_extract(kr.stats_summary, '$.config.DPI') AS stats_dpi,
            json_extract(kr.stats_summary, '$.config."Horiz Sens"') AS stats_sensitivity,
            json_extract(kr.stats_summary, '$.cm_per_360') AS stats_cm_per_360,
            (SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id = kr.id AND s.user_id = kr.user_id) AS analysis_count
     FROM kovaak_runs AS kr
     WHERE kr.user_id = ?
     ORDER BY kr.created_at DESC, kr.id DESC LIMIT 100`,
  ).all(ownerId) as Array<Record<string, unknown>>;

  const items = rows.map((row) => ({
    id: row.id,
    run_ref: `run:${row.id}`,
    source_key: row.source_key,
    scenario: row.scenario,
    trace_state: row.trace_state ?? "none",
    finalization_state: row.finalization_state ?? "pending",
    analysis_count: row.analysis_count,
    stats_calibration: {
      FOV: row.stats_fov ?? null,
      DPI: row.stats_dpi ?? null,
      sensitivity: row.stats_sensitivity ?? null,
      cm_per_360: row.stats_cm_per_360 ?? null,
    },
    created_at: sqliteTimestampToWireUtc(row.created_at),
    updated_at: sqliteTimestampToWireUtc(row.updated_at),
  }));
  return { status: "succeeded", result: items };
};

// ── profile.aiming.snapshot ───────────────────────────────────────────

const profileAimingSnapshot: CommandHandler = (db, _params, ownerId) => {
  const state = db.prepare(
    "SELECT rebuild_state, updated_at FROM aiming_profile_state WHERE owner_id = ?",
  ).get(ownerId) as Record<string, unknown> | undefined;

  const dimensions = db.prepare(
    "SELECT projection_json FROM aiming_profile_dimensions WHERE owner_id = ? ORDER BY dimension_key, scope, scope_ref LIMIT 24",
  ).all(ownerId) as Array<{ projection_json: string }>;

  const contributions = db.prepare(
    "SELECT contribution_ref FROM profile_contributions WHERE owner_id = ? AND status = 'active' ORDER BY updated_at DESC, analysis_ref LIMIT 24",
  ).all(ownerId) as Array<{ contribution_ref: string }>;

  const activePlan = db.prepare(
    "SELECT plan_id FROM training_plans WHERE owner_id = ? AND status = 'active'",
  ).get(ownerId) as { plan_id: string } | undefined;

  return {
    status: "succeeded",
    result_ref: `profile-aiming:${ownerId}`,
    result: {
      schema_version: "aiming_profile.v1",
      owner_ref: ownerId,
      profile_ref: `profile-aiming:${ownerId}`,
      status: state?.rebuild_state ?? "clean",
      dimensions: dimensions.map((d) => parseJsonColumn(d.projection_json)).filter(Boolean),
      contribution_refs: contributions.map((c) => c.contribution_ref),
      next_retest_refs: [],
      active_plan_ref: activePlan?.plan_id ?? null,
      updated_at: sqliteTimestampToWireUtc(state?.updated_at),
    },
  };
};

// ── training_plan.review ──────────────────────────────────────────────

const trainingPlanReview: CommandHandler = (db, _params, ownerId) => {
  const plan = db.prepare(
    "SELECT plan_id, owner_id, status, current_version, created_at, updated_at FROM training_plans WHERE owner_id = ? ORDER BY status DESC LIMIT 1",
  ).get(ownerId) as Record<string, unknown> | undefined;

  if (!plan) {
    return { status: "succeeded", result: { schema_version: "training_plan_review.v1", has_plan: false } };
  }

  const version = db.prepare(
    "SELECT plan_payload_json, evidence_refs_json, verification_targets_json FROM training_plan_versions WHERE plan_id = ? AND version = ?",
  ).get(plan.plan_id, plan.current_version) as Record<string, unknown> | undefined;

  return {
    status: "succeeded",
    result: {
      schema_version: "training_plan_review.v1",
      has_plan: true,
      plan_ref: plan.plan_id,
      status: plan.status,
      version: plan.current_version,
      payload: version ? parseJsonColumn(version.plan_payload_json) : null,
      evidence_refs: version ? parseJsonColumn(version.evidence_refs_json) : null,
      verification_targets: version ? parseJsonColumn(version.verification_targets_json) : null,
      created_at: sqliteTimestampToWireUtc(plan.created_at),
      updated_at: sqliteTimestampToWireUtc(plan.updated_at),
    },
  };
};

// ── Dispatch ───────────────────────────────────────────────────────────

const HANDLERS: Record<string, CommandHandler> = {
  "calibration.get": calibrationGet,
  "kovaak.connection.get": kovaakConnectionGet,
  "peripheral_profile.get": peripheralProfileGet,
  "analysis.get": analysisGet,
  "coach.session.list": coachSessionList,
  "product.readiness.get": productReadinessGet,
  "history.list": historyList,
  "history.trend": historyTrend,
  "analysis.compare": analysisCompare,
  "run.list": runList,
  "profile.aiming.snapshot": profileAimingSnapshot,
  "training_plan.review": trainingPlanReview,
};

export const NATIVE_READ_COMMANDS = new Set([
  "calibration.get",
  "kovaak.connection.get",
  "peripheral_profile.get",
  "analysis.get",
  "coach.session.list",
  "product.readiness.get",
  "history.list",
  "history.trend",
  "analysis.compare",
  "run.list",
  "profile.aiming.snapshot",
  "training_plan.review",
]);

export function executeNativeRead(
  db: SqliteDb,
  commandName: string,
  parameters: Record<string, unknown>,
  ownerId: string,
): NativeCommandResult {
  // navigation.open has no DB access — handle specially.
  if (commandName === "navigation.open") {
    return navigationOpen(parameters);
  }
  const handler = HANDLERS[commandName];
  if (!handler) {
    return { status: "failed", warning_or_error: { code: "unknown_command", message: `${commandName} is not a native command` } };
  }
  try {
    return handler(db, parameters, ownerId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "native command failed";
    return { status: "failed", warning_or_error: { code: "native_error", message } };
  }
}
