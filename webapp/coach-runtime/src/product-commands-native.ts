/**
 * File-based implementations of read-only product commands.
 *
 * These commands read directly from the app-data file system (JSON files),
 * eliminating SQLite database access for read operations.
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { getDataRoot, getAnalysesDir, getConfigDir, getSessionsDir, getTrainingDir } from "./app-data.ts";
import { reportAnalysisRead } from "./fs-tools.ts";

// ── Types ──────────────────────────────────────────────────────────────

export type NativeCommandResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

type AnyDict = Record<string, any>;

type CommandHandler = (
  parameters: Record<string, unknown>,
  ownerId: string,
) => NativeCommandResult;

// ── File helpers ───────────────────────────────────────────────────────

function readJsonFile<T = AnyDict>(path: string): T | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as T;
  } catch {
    return null;
  }
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

function parseRunRef(ref: unknown): number {
  if (typeof ref === "number" && Number.isInteger(ref) && ref > 0) return ref;
  if (typeof ref === "string") {
    const match = ref.match(/^run:(\d+)$/);
    if (match) return parseInt(match[1], 10);
    const parsed = parseInt(ref, 10);
    if (Number.isInteger(parsed) && parsed > 0) return parsed;
  }
  throw new Error("invalid run_ref");
}

// ── Simple commands ────────────────────────────────────────────────────

const calibrationGet: CommandHandler = (_params, _ownerId) => {
  const data = readJsonFile(join(getConfigDir(), "calibration.json"));
  if (!data) {
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
      configured: data.cm_per_360 != null || data.fov != null,
      values: {
        cm_per_360: data.cm_per_360 ?? null,
        fov: data.fov ?? null,
      },
      dpi: null,
      sensitivity: null,
      adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
      updated_at: data.updated_at ?? null,
    },
  };
};

const kovaakConnectionGet: CommandHandler = (_params, _ownerId) => {
  const data = readJsonFile(join(getConfigDir(), "kovaak-connection.json"));
  return {
    status: "succeeded",
    result_ref: "kovaak_connection:current",
    result: { connection_ref: "kovaak_connection:current", connected: data !== null },
  };
};

const peripheralProfileGet: CommandHandler = (_params, ownerId) => {
  const data = readJsonFile(join(getConfigDir(), "peripheral.json"));
  if (!data) {
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
      grip_type: data.grip_type ?? null,
      hand_length_cm: data.hand_length_cm ?? null,
      wrist_position: data.wrist_position ?? null,
      grip_preference: data.grip_preference ?? null,
      current_mouse_brand: data.current_mouse_brand ?? null,
      current_mouse_model: data.current_mouse_model ?? null,
      current_mousepad: data.current_mousepad ?? null,
      budget: data.budget ?? null,
      updated_at: data.updated_at ?? null,
    },
  };
};

function navigationOpen(parameters: Record<string, unknown>): NativeCommandResult {
  const target = requireString(parameters.target, "target");
  const validTargets = ["history", "analysis", "analysis_section", "flick_event", "evidence", "video_time"];
  if (!validTargets.includes(target)) {
    return { status: "failed", warning_or_error: { code: "invalid_parameters", message: "unsupported navigation target" } };
  }
  const event: AnyDict = { schema_version: "coach_ui_event.v1" };
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

// ── Analysis & history commands ────────────────────────────────────────

const analysisGet: CommandHandler = (params, _ownerId) => {
  const analysisId = parseAnalysisRef(params.analysis_ref);
  const overview = readJsonFile(join(getAnalysesDir(), String(analysisId), "overview.json"));
  if (!overview) {
    return { status: "failed", warning_or_error: { code: "not_found", message: "Analysis 不存在" } };
  }
  reportAnalysisRead(analysisId);
  return {
    status: "succeeded",
    result_ref: `analysis:${analysisId}`,
    result: {
      analysis_ref: `analysis:${analysisId}`,
      id: analysisId,
      status: overview.status ?? "done",
      analysis_type: overview.analysis_type ?? "flicking",
      input_mode: overview.input_mode ?? "video_fallback",
      run_ref: overview.run_ref ?? null,
      created_at: overview.created_at ?? null,
      started_at: overview.started_at ?? null,
      finished_at: overview.finished_at ?? null,
      error: overview.error ?? null,
    },
  };
};

const coachSessionList: CommandHandler = (_params, _ownerId) => {
  // Sessions are managed via the REST API (sidecar-coach-data.ts) and
  // conversations directory. The Coach can use ls/read tools to explore.
  return { status: "succeeded", result: [] };
};

const productReadinessGet: CommandHandler = (_params, _ownerId) => {
  const configDir = getConfigDir();
  const onboarding = readJsonFile(join(configDir, "onboarding.json"));
  const provider = readJsonFile(join(configDir, "provider.json"));
  const kovaakConnection = readJsonFile(join(configDir, "kovaak-connection.json"));
  const calibration = readJsonFile(join(configDir, "calibration.json"));
  const peripheral = readJsonFile(join(configDir, "peripheral.json"));
  return {
    status: "succeeded",
    result: {
      schema_version: "product_readiness.v1",
      has_completed_onboarding: onboarding?.completed === true,
      has_provider_configured: provider?.profile != null,
      has_kovaak_connection: kovaakConnection !== null,
      has_calibration: calibration !== null && (calibration.cm_per_360 != null || calibration.fov != null),
      has_peripheral_profile: peripheral !== null,
    },
  };
};

const historyList: CommandHandler = (_params, _ownerId) => {
  const analysesDir = getAnalysesDir();
  let entries: string[];
  try {
    entries = readdirSync(analysesDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort((a, b) => {
        const na = parseInt(a, 10);
        const nb = parseInt(b, 10);
        return (Number.isFinite(na) && Number.isFinite(nb)) ? nb - na : b.localeCompare(a);
      });
  } catch {
    return { status: "succeeded", result: [] };
  }

  const items: AnyDict[] = [];
  for (const entry of entries) {
    const overview = readJsonFile(join(analysesDir, entry, "overview.json"));
    if (!overview) continue;
    const id = parseInt(entry, 10);
    items.push({
      id: Number.isFinite(id) ? id : entry,
      analysis_ref: `analysis:${entry}`,
      run_ref: overview.run_ref ?? null,
      status: overview.status ?? "done",
      created_at: overview.created_at ?? null,
      finished_at: overview.finished_at ?? null,
      summary_label: overview.summary_label ?? null,
      analysis_type: overview.analysis_type ?? "flicking",
      input_mode: overview.input_mode ?? "video_fallback",
      scenario: overview.scenario ?? null,
      training_at: overview.training_at ?? null,
      analysis_completed_at: overview.finished_at ?? null,
    });
  }
  return { status: "succeeded", result: items };
};

// ── Comparison logic (ported from Python history_trends.compare_analysis_results) ──
// This is pure computation on analysis result objects — no DB access.

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

function getResultMetric(result: AnyDict, key: string): AnyDict | null {
  const det = result.deterministic;
  if (!det || typeof det !== "object") return null;
  const metrics = (det as AnyDict).metrics;
  if (!metrics || typeof metrics !== "object") return null;
  const value = (metrics as AnyDict)[key];
  return value && typeof value === "object" ? (value as AnyDict) : null;
}

function analysisVersion(result: AnyDict): string | null {
  return "analysis_version" in result ? safeIdentity(result.analysis_version) : null;
}

function timebaseVersion(result: AnyDict): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  const window = (snapshot as AnyDict).canonical_time_window;
  if (!window || typeof window !== "object") return null;
  const value = (window as AnyDict).timebase_version;
  return value != null ? safeIdentity(value) : null;
}

function scenarioResolution(result: AnyDict): AnyDict | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  const resolution = (snapshot as AnyDict).scenario_resolution;
  return resolution && typeof resolution === "object" ? (resolution as AnyDict) : null;
}

function validateScenarioResolutionV1(value: unknown): void {
  if (!value || typeof value !== "object") throw new Error("scenario_resolution must be a dict");
  const obj = value as AnyDict;
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

function scenarioResolutionIsInvalid(result: AnyDict): boolean {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return false;
  const snap = snapshot as AnyDict;
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

function scenarioHash(result: AnyDict): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  const value = resolution.scenario_hash;
  return typeof value === "string" && SAFE_SCENARIO_HASH_RE.test(value) ? value : null;
}

function scenarioProfileRef(result: AnyDict): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  const value = resolution.scenario_profile_ref;
  return typeof value === "string" && SAFE_SCENARIO_PROFILE_REF_RE.test(value) ? value : null;
}

function scenarioRegistryVersion(result: AnyDict): string | null {
  const resolution = scenarioResolution(result);
  if (!resolution) return null;
  return safeIdentity(resolution.registry_version);
}

function scenarioName(result: AnyDict): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  return safeScenario((snapshot as AnyDict).scenario);
}

function scenarioIdentityVersion(result: AnyDict): string | null {
  const snapshot = result.input_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;
  return safeIdentity((snapshot as AnyDict).scenario_identity_version);
}

function fullCoverage(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value === 1.0;
}

function hasCompleteSwitchingMetricEvidence(
  result: AnyDict,
  metric: AnyDict,
): boolean {
  if (result.analysis_type !== "target_switching") return false;
  const det = result.deterministic;
  if (!det || typeof det !== "object") return false;
  if ((det as AnyDict).support_status !== "supported") return false;
  if (!TARGET_SWITCHING_COMPARISON_METRICS.has(metric.key as string)) return false;
  const conditionRefs = metric.condition_refs;
  if (!Array.isArray(conditionRefs) || conditionRefs.length !== 1 || conditionRefs[0] !== TARGET_SWITCHING_CHAIN_CONDITION_REF) return false;
  const evidence = result.evidence;
  if (!evidence || typeof evidence !== "object") return false;
  const coverage = (evidence as AnyDict).coverage;
  return typeof coverage === "number" && Number.isFinite(coverage) && coverage > 0.0 && coverage <= 1.0;
}

function qualityReason(
  result: AnyDict,
  metric: AnyDict,
): string | null {
  if (metric.classification !== "deterministic") return "metric_not_deterministic";
  if (metric.availability !== "available") return "metric_unavailable";
  if (!fullCoverage(metric.coverage)) return "insufficient_metric_coverage";
  const evidence = result.evidence;
  if (!evidence || typeof evidence !== "object") return "insufficient_evidence_coverage";
  const evidenceObj = evidence as AnyDict;
  if (!fullCoverage(evidenceObj.coverage) && !hasCompleteSwitchingMetricEvidence(result, metric)) {
    return "insufficient_evidence_coverage";
  }
  const alignmentValue = evidenceObj.alignment;
  const alignment = alignmentValue && typeof alignmentValue === "object"
    ? (alignmentValue as AnyDict).status
    : null;
  if (alignment !== "aligned" && alignment !== "not_required") return "insufficient_alignment_quality";
  return null;
}

function familyComparabilityReason(
  current: AnyDict,
  baseline: AnyDict,
  currentMetric: AnyDict,
  baselineMetric: AnyDict,
  missingReason: string,
): string | null {
  const currentDet = current.deterministic;
  const baselineDet = baseline.deterministic;
  if (!currentDet || typeof currentDet !== "object" || !baselineDet || typeof baselineDet !== "object") {
    return missingReason;
  }
  const currentProfile = safeIdentity((currentDet as AnyDict).visual_quality_profile_ref);
  const baselineProfile = safeIdentity((baselineDet as AnyDict).visual_quality_profile_ref);
  if (!currentProfile || !baselineProfile) return "visual_quality_profile_missing";
  if (currentProfile !== baselineProfile) return "visual_quality_profile_mismatch";
  const currentMotion = safeIdentity((currentDet as AnyDict).scenario_motion_class);
  const baselineMotion = safeIdentity((baselineDet as AnyDict).scenario_motion_class);
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
  current: AnyDict,
  baseline: AnyDict,
  metricKey: string,
): AnyDict {
  if (current.schema_version !== "analysis_result.v2" || baseline.schema_version !== "analysis_result.v2") {
    return { comparable: false, reason: "analysis_result_version_mismatch" };
  }

  if ("analysis_version" in current || "analysis_version" in baseline) {
    const cv = analysisVersion(current);
    const bv = analysisVersion(baseline);
    if (!cv || cv !== bv) {
      return { comparable: false, reason: "analysis_version_mismatch" };
    }
  }

  const currentSnapshot = current.input_snapshot;
  const baselineSnapshot = baseline.input_snapshot;
  const currentHasWindow = currentSnapshot && typeof currentSnapshot === "object" &&
    "canonical_time_window" in (currentSnapshot as AnyDict);
  const baselineHasWindow = baselineSnapshot && typeof baselineSnapshot === "object" &&
    "canonical_time_window" in (baselineSnapshot as AnyDict);
  if (currentHasWindow || baselineHasWindow) {
    const ct = timebaseVersion(current);
    const bt = timebaseVersion(baseline);
    if (!ct || ct !== bt) {
      return { comparable: false, reason: "timebase_version_mismatch" };
    }
  }

  if (scenarioResolutionIsInvalid(current) || scenarioResolutionIsInvalid(baseline)) {
    return { comparable: false, reason: "scenario_resolution_invalid" };
  }

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

  for (const [result, metric] of [
    [current, currentMetric],
    [baseline, baselineMetric],
  ] as [AnyDict, AnyDict][]) {
    const reason = qualityReason(result, metric);
    if (reason) {
      return { comparable: false, reason };
    }
  }

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

  const currentCalibration = safeIdentity(currentMetric.calibration_ref);
  const baselineCalibration = safeIdentity(baselineMetric.calibration_ref);
  if (!currentCalibration || !baselineCalibration) {
    return { comparable: false, reason: "calibration_compatibility_missing" };
  }
  if (currentCalibration !== baselineCalibration) {
    return { comparable: false, reason: "calibration_mismatch" };
  }

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

/** Read the full analysis result for one analysis id. */
function readAnalysisResult(analysisId: number): AnyDict | null {
  const dir = join(getAnalysesDir(), String(analysisId));
  // Full analysis_result.v2 lives in the backend session record; result.json
  // would win if a writer ever produced it, evidence.json is a last resort.
  const standalone = readJsonFile(join(dir, "result.json"));
  if (standalone) return standalone;
  const session = readJsonFile(join(getSessionsDir(), `${analysisId}.json`));
  const sessionResult = session?.result;
  if (session?.status === "done" && sessionResult && typeof sessionResult === "object") {
    return sessionResult as AnyDict;
  }
  return readJsonFile(join(dir, "evidence.json"));
}

// ── history.trend ──────────────────────────────────────────────────────

const historyTrend: CommandHandler = (params, _ownerId) => {
  const metricKey = requireString(params.metric_key, "metric_key");
  const analysesDir = getAnalysesDir();
  let entries: string[];
  try {
    entries = readdirSync(analysesDir, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => parseInt(e.name, 10))
      .filter((n) => Number.isFinite(n))
      .sort((a, b) => b - a); // newest first
  } catch {
    entries = [];
  }

  const parsed: Array<{ id: number; result: AnyDict }> = [];
  for (const id of entries.slice(0, 100)) {
    const result = readAnalysisResult(id);
    if (result && result.schema_version === "analysis_result.v2") {
      parsed.push({ id, result });
    }
  }

  if (parsed.length < 2) {
    return {
      status: "succeeded",
      result: { comparable: false, reason: "insufficient_history" },
    };
  }

  const current = parsed[0];
  for (let i = 1; i < parsed.length; i++) {
    const baseline = parsed[i];
    const comparison = compareAnalysisResults(current.result, baseline.result, metricKey);
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

// ── analysis.compare ───────────────────────────────────────────────────

const analysisCompare: CommandHandler = (params, _ownerId) => {
  const currentId = parseAnalysisRef(params.current_analysis_ref);
  const baselineId = parseAnalysisRef(params.baseline_analysis_ref);
  const metricKey = requireString(params.metric_key, "metric_key");

  const currentResult = readAnalysisResult(currentId);
  const baselineResult = readAnalysisResult(baselineId);

  if (!currentResult || !baselineResult) {
    return { status: "failed", warning_or_error: { code: "not_found", message: "Analysis 不存在" } };
  }
  if (!currentResult.schema_version || !baselineResult.schema_version) {
    return { status: "failed", warning_or_error: { code: "analysis_result_missing", message: "Analysis 结果不可用" } };
  }
  reportAnalysisRead(currentId);
  reportAnalysisRead(baselineId);

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

// ── run.list ───────────────────────────────────────────────────────────

const runList: CommandHandler = (_params, _ownerId) => {
  const runsDir = join(getDataRoot(), "runs");
  let entries: string[];
  try {
    entries = readdirSync(runsDir, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort((a, b) => {
        const na = parseInt(a, 10);
        const nb = parseInt(b, 10);
        return (Number.isFinite(na) && Number.isFinite(nb)) ? nb - na : b.localeCompare(a);
      });
  } catch {
    return { status: "succeeded", result: [] };
  }

  const items: AnyDict[] = [];
  for (const entry of entries.slice(0, 100)) {
    const meta = readJsonFile(join(runsDir, entry, "meta.json"));
    if (!meta) continue;
    const id = parseInt(entry, 10);
    items.push({
      id: Number.isFinite(id) ? id : entry,
      run_ref: `run:${entry}`,
      source_key: meta.source_key ?? null,
      scenario: meta.scenario ?? null,
      trace_state: meta.trace_state ?? "none",
      finalization_state: meta.finalization_state ?? "pending",
      stats_calibration: meta.stats_calibration ?? {
        FOV: null, DPI: null, sensitivity: null, cm_per_360: null,
      },
      created_at: meta.created_at ?? null,
      updated_at: meta.updated_at ?? null,
    });
  }
  return { status: "succeeded", result: items };
};

const runGet: CommandHandler = (params, _ownerId) => {
  const runId = parseRunRef(params.run_ref);
  const meta = readJsonFile(join(getDataRoot(), "runs", String(runId), "meta.json"));
  if (!meta) {
    return { status: "failed", warning_or_error: { code: "not_found", message: "KovaaK run does not exist" } };
  }
  return {
    status: "succeeded",
    result_ref: `run:${runId}`,
    result: {
      id: runId,
      run_ref: `run:${runId}`,
      source_key: meta.source_key ?? null,
      scenario: meta.scenario ?? null,
      trace_state: meta.trace_state ?? "none",
      finalization_state: meta.finalization_state ?? "pending",
      stats_calibration: meta.stats_calibration ?? {
        FOV: null, DPI: null, sensitivity: null, cm_per_360: null,
      },
      created_at: meta.created_at ?? null,
      updated_at: meta.updated_at ?? null,
    },
  };
};

// ── profile.aiming.snapshot ───────────────────────────────────────────

const profileAimingSnapshot: CommandHandler = (_params, ownerId) => {
  const data = readJsonFile(join(getDataRoot(), "profile.json"));
  return {
    status: "succeeded",
    result_ref: `profile-aiming:${ownerId}`,
    result: {
      schema_version: "aiming_profile.v1",
      owner_ref: ownerId,
      profile_ref: `profile-aiming:${ownerId}`,
      status: data?.status ?? "clean",
      dimensions: data?.dimensions ?? [],
      contribution_refs: data?.contribution_refs ?? [],
      next_retest_refs: [],
      active_plan_ref: data?.active_plan_ref ?? null,
      updated_at: data?.updated_at ?? null,
    },
  };
};

// ── training_plan.review ──────────────────────────────────────────────

const trainingPlanReview: CommandHandler = (_params, _ownerId) => {
  const plan = readJsonFile(join(getTrainingDir(), "plan.json"));
  if (!plan) {
    return { status: "succeeded", result: { schema_version: "training_plan_review.v1", has_plan: false } };
  }
  return {
    status: "succeeded",
    result: {
      schema_version: "training_plan_review.v1",
      has_plan: true,
      plan_ref: plan.plan_id ?? null,
      status: plan.status ?? null,
      version: plan.version ?? 1,
      payload: plan.plan_payload ?? null,
      evidence_refs: plan.evidence_refs ?? null,
      verification_targets: plan.verification_targets ?? null,
      created_at: plan.created_at ?? null,
      updated_at: plan.updated_at ?? null,
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
  "run.get": runGet,
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
  "run.get",
  "navigation.open",
  "profile.aiming.snapshot",
  "training_plan.review",
]);

export function executeNativeRead(
  commandName: string,
  parameters: Record<string, unknown>,
  ownerId: string,
): NativeCommandResult {
  // navigation.open has no file access — handle specially.
  if (commandName === "navigation.open") {
    return navigationOpen(parameters);
  }
  const handler = HANDLERS[commandName];
  if (!handler) {
    return { status: "failed", warning_or_error: { code: "unknown_command", message: `${commandName} is not a native command` } };
  }
  try {
    return handler(parameters, ownerId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "native command failed";
    return { status: "failed", warning_or_error: { code: "native_error", message } };
  }
}
