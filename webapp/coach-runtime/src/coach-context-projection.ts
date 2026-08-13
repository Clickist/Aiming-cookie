import { isCanonicalDiagnosticContext } from "./analysis-summary-tool.ts";

export type CoachDiagnosticContext = Record<string, unknown>;
type JsonRecord = CoachDiagnosticContext;

const MAX_CONTEXT_BYTES = 32 * 1024;
const INPUT_MODES = new Set(["input_native", "multimodal", "video_fallback"]);
const CLAIM_LEVELS = new Set([
  "measured",
  "deterministic_rule",
  "research_supported",
  "community_practice",
  "community_consensus",
  "experimental",
]);
const SOURCE_LEVELS = new Set([
  "product_contract",
  "academic_peer_reviewed",
  "community_practice",
  "community_consensus",
  "personal_experience_unverified",
  "experimental",
]);
const PROCESSED_QUERY_CAPABILITIES = [
  "analysis.events.list",
  "analysis.events.get",
  "analysis.events.rank",
  "analysis.events.filter",
  "analysis.events.aggregate",
  "analysis.events.co_occurrence",
  "analysis.events.sequence",
  "analysis.evidence.compare",
];

const METRIC_FIELDS = [
  "value", "unit", "metric_version", "classification", "min", "max", "mean",
  "median", "med", "p25", "p50", "p75", "p90", "std", "iqr", "count", "n",
  "score", "status", "key", "availability", "sample_count", "coverage", "outlier_method",
] as const;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeScalar(value: unknown): value is null | boolean | number | string {
  return value === null || typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value)) || typeof value === "string";
}

function safeStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function safeScalarFields(value: unknown, fields: readonly string[]): JsonRecord {
  if (!isRecord(value)) return {};
  const projected: JsonRecord = {};
  for (const field of fields) {
    if (field in value && isSafeScalar(value[field])) projected[field] = value[field];
  }
  return projected;
}

function projectMetric(value: unknown, requireDeterministic: boolean): JsonRecord | null {
  if (!isRecord(value)) {
    return !requireDeterministic && isSafeScalar(value) ? { value } : null;
  }
  if (requireDeterministic && value.classification !== "deterministic") return null;
  if (!requireDeterministic && value.classification !== undefined &&
      value.classification !== null && value.classification !== "deterministic") return null;

  const metric = safeScalarFields(value, METRIC_FIELDS);
  for (const field of ["limitations", "outlier_refs", "sample_refs"] as const) {
    const strings = safeStrings(value[field]);
    if (strings.length > 0) metric[field] = strings;
  }
  if (isRecord(value.provenance) &&
      new Set(["measured", "derived", "fused"]).has(String(value.provenance.kind))) {
    metric.provenance = {
      kind: value.provenance.kind,
      ...(safeStrings(value.provenance.sources).length > 0
        ? { sources: safeStrings(value.provenance.sources) }
        : {}),
    };
  }
  if (isRecord(value.definition)) {
    const definition = safeScalarFields(value.definition, ["name", "description"]);
    if (Object.values(definition).every((item) => typeof item === "string") &&
        Object.keys(definition).length > 0) metric.definition = definition;
  }
  return Object.keys(metric).length > 0 ? metric : null;
}

function projectSummary(value: unknown, requireDeterministic: boolean): JsonRecord {
  if (!isRecord(value)) return {};
  const summary: JsonRecord = {};
  for (const [key, rawMetric] of Object.entries(value)) {
    const metric = projectMetric(rawMetric, requireDeterministic);
    if (metric !== null) summary[key] = metric;
  }
  return summary;
}

function projectIssue(value: unknown, inheritedLimitations: string[]): JsonRecord | null {
  if (!isRecord(value)) return null;
  const issue = safeScalarFields(value, [
    "signal", "severity", "priority", "priority_reason", "plain_language_meaning", "expected_result",
  ]);
  issue.claim_level = typeof value.claim_level === "string" && CLAIM_LEVELS.has(value.claim_level)
    ? value.claim_level
    : "experimental";

  for (const field of ["metric_refs", "event_refs"] as const) {
    const strings = safeStrings(value[field]);
    if (strings.length > 0) issue[field] = strings;
  }
  const limitations = [...new Set([...safeStrings(value.limitations), ...inheritedLimitations])];
  if (limitations.length > 0) issue.limitations = limitations;

  if (isRecord(value.verification)) {
    const verification = safeScalarFields(value.verification, ["insufficient_evidence_behavior"]);
    for (const field of ["comparable_requirements", "success_signals"] as const) {
      const strings = safeStrings(value.verification[field]);
      if (strings.length > 0) verification[field] = strings;
    }
    if (Object.keys(verification).length > 0) issue.verification = verification;
  }

  if (Array.isArray(value.root_causes)) {
    const rootCauses = value.root_causes
      .map((item) => safeScalarFields(item, ["level", "text"]))
      .filter((item) => Object.keys(item).length > 0);
    if (rootCauses.length > 0) issue.root_causes = rootCauses;
  }

  if (Array.isArray(value.prescriptions)) {
    const prescriptions = value.prescriptions.flatMap((raw) => {
      if (!isRecord(raw)) return [];
      const prescription = safeScalarFields(raw, [
        "scenario", "reason", "cue", "purpose", "dosage", "retest_after", "stop_or_adjust_rule",
      ]);
      for (const field of ["target_metrics", "expected_direction"] as const) {
        const strings = safeStrings(raw[field]);
        if (strings.length > 0) prescription[field] = strings;
      }
      prescription.source_level = typeof raw.source_level === "string" && SOURCE_LEVELS.has(raw.source_level)
        ? raw.source_level
        : "experimental";
      return [prescription];
    });
    if (prescriptions.length > 0) issue.prescriptions = prescriptions;
  }

  const primary = value.primary_evidence_segment_ref;
  if (typeof primary === "string" && primary.startsWith("analysis:") && primary.includes(":segment:")) {
    issue.primary_evidence_segment_ref = primary;
  }
  const supporting = safeStrings(value.supporting_evidence_segment_refs);
  if (supporting.length <= 2 && supporting.every((ref) => ref.startsWith("analysis:") && ref.includes(":segment:"))) {
    if (supporting.length > 0) issue.supporting_evidence_segment_refs = supporting;
  }
  return issue;
}

function projectDiagnosis(
  value: unknown,
  fallbackSummary: unknown,
  requireDeterministic: boolean,
  inheritedLimitations: string[],
): JsonRecord {
  const diagnosis = isRecord(value) ? value : {};
  const profile = safeScalarFields(diagnosis.profile, ["archetype_id", "label", "confidence"]);
  if (isRecord(diagnosis.profile)) {
    const tags = safeStrings(diagnosis.profile.secondary_tags);
    if (tags.length > 0) profile.secondary_tags = tags;
  }

  const issues = Array.isArray(diagnosis.issues)
    ? diagnosis.issues.map((item) => projectIssue(item, inheritedLimitations)).filter((item) => item !== null)
    : [];
  const summary = {
    ...projectSummary(fallbackSummary, requireDeterministic),
    ...projectSummary(diagnosis.summary, requireDeterministic),
  };
  const comparison = isRecord(diagnosis.comparison) && diagnosis.comparison.classification === "deterministic"
    ? safeScalarFields(diagnosis.comparison, [
      "status", "reason", "comparable", "metric", "delta", "unit", "classification",
    ])
    : null;
  const rawMeta = isRecord(diagnosis.meta) ? diagnosis.meta : {};
  const meta = rawMeta.classification === undefined || rawMeta.classification === null ||
      rawMeta.classification === "deterministic"
    ? safeScalarFields(rawMeta, [
      "summary_type", "analysis_context", "metric_version", "scenario_identity_version",
      "calibration_compatibility", "minimum_evidence_quality", "classification",
    ])
    : {};
  return { profile, issues, summary, comparison, meta };
}

function projectEvidenceSummary(result: JsonRecord, version: "analysis_result.v1" | "analysis_result.v2"): JsonRecord {
  if (version === "analysis_result.v2") {
    const evidence = isRecord(result.evidence) ? result.evidence : {};
    const availability = isRecord(evidence.availability)
      ? Object.fromEntries(Object.entries(evidence.availability).filter(([, item]) => isSafeScalar(item)))
      : {};
    const alignment = safeScalarFields(evidence.alignment, ["status", "coverage_ratio"]);
    const projected: JsonRecord = { availability, alignment };
    if (isSafeScalar(evidence.coverage)) projected.coverage = evidence.coverage;
    return projected;
  }

  const availability: JsonRecord = {};
  const manifest = isRecord(result.artifact_manifest) ? result.artifact_manifest : {};
  if (Array.isArray(manifest.inputs)) {
    for (const input of manifest.inputs) {
      if (isRecord(input) && typeof input.kind === "string" && isSafeScalar(input.status)) {
        availability[input.kind] = input.status;
      }
    }
  }
  return { availability, alignment: {} };
}

function projectWarnings(...values: unknown[]): JsonRecord[] {
  const projected: JsonRecord[] = [];
  const codes = new Set<string>();
  for (const value of values) {
    if (!Array.isArray(value)) continue;
    for (const raw of value) {
      if (!isRecord(raw) || typeof raw.code !== "string" || codes.has(raw.code)) continue;
      const warning = safeScalarFields(raw, ["code", "domain", "retryable", "user_message_key"]);
      codes.add(raw.code);
      projected.push(warning);
    }
  }
  return projected;
}

function projectProcessedEventTables(value: unknown, analysisId: string): JsonRecord[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 8) return null;
  const tables: JsonRecord[] = [];
  const refs = new Set<string>();
  for (const raw of value) {
    if (!isRecord(raw) || raw.schema_version !== "processed_event_table.v1" ||
        raw.analysis_ref !== analysisId || typeof raw.table_ref !== "string" ||
        raw.rows_ref !== raw.table_ref || !raw.table_ref.startsWith(`${analysisId}:table:`) ||
        refs.has(raw.table_ref)) return null;
    if (typeof raw.analyzer_ref !== "string" || typeof raw.family !== "string" ||
        typeof raw.event_kind !== "string" ||
        !["row_count", "included_count", "excluded_count"].every((key) =>
          typeof raw[key] === "number" && Number.isInteger(raw[key]) && (raw[key] as number) >= 0) ||
        raw.row_count !== raw.included_count ||
        !new Set(["complete", "partial", "unavailable"]).has(String(raw.completeness)) ||
        (raw.completeness === "complete" && raw.excluded_count !== 0) ||
        !Array.isArray(raw.field_catalog) || raw.field_catalog.length < 1 || raw.field_catalog.length > 64 ||
        !Array.isArray(raw.index_fields) || raw.index_fields.length > 8 ||
        !safeStrings(raw.limitations).every((item) => typeof item === "string")) return null;

    const fieldKeys = new Set<string>();
    const fieldCatalog: JsonRecord[] = [];
    for (const rawField of raw.field_catalog) {
      if (!isRecord(rawField) || typeof rawField.field_key !== "string" || fieldKeys.has(rawField.field_key) ||
          !new Set(["identity", "timing", "condition", "metric", "outcome", "quality"]).has(String(rawField.role)) ||
          !new Set(["number", "string", "ref", "string_list", "boolean"]).has(String(rawField.value_type)) ||
          !["unit", "metric_key", "metric_version"].every((key) => rawField[key] === null || typeof rawField[key] === "string") ||
          (rawField.metric_version !== null && typeof rawField.metric_version === "string" &&
            !/^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$/.test(rawField.metric_version)) ||
          !new Set(["lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only"]).has(String(rawField.expected_direction)) ||
          !safeStrings(rawField.limitations).every((item) => typeof item === "string")) return null;
      fieldKeys.add(rawField.field_key);
      fieldCatalog.push(rawField);
    }
    if (!safeStrings(raw.index_fields).every((field) => fieldKeys.has(field))) return null;
    refs.add(raw.table_ref);
    tables.push({ ...raw, field_catalog: fieldCatalog });
  }
  return tables;
}

function projectRawResult(result: JsonRecord, sessionId?: number): JsonRecord | null {
  const version = result.schema_version;
  if (version !== "analysis_result.v1" && version !== "analysis_result.v2") return null;

  const resultId = typeof result.analysis_id === "string"
    ? result.analysis_id
    : sessionId !== undefined ? `analysis:${sessionId}` : null;
  const analysisType = version === "analysis_result.v2" ? result.analysis_type : result.summary_type;
  const inputMode = version === "analysis_result.v2" ? result.input_mode : "unknown";
  if (version === "analysis_result.v2" && (
    typeof resultId !== "string" || typeof analysisType !== "string" ||
    !INPUT_MODES.has(String(inputMode))
  )) return null;
  if (version === "analysis_result.v1" && analysisType !== undefined &&
      analysisType !== null && typeof analysisType !== "string") return null;

  const deterministic = isRecord(result.deterministic) ? result.deterministic : {};
  const evidence = isRecord(result.evidence) ? result.evidence : {};
  const inputSnapshot = isRecord(result.input_snapshot) ? result.input_snapshot : {};
  const resolution = isRecord(inputSnapshot.scenario_resolution) ? inputSnapshot.scenario_resolution : {};
  const rawScenario = isRecord(result.scenario) ? result.scenario : {};
  const scenarioLimitations = safeStrings(rawScenario.limitations).length > 0
    ? safeStrings(rawScenario.limitations)
    : safeStrings(deterministic.limitations).length > 0
      ? safeStrings(deterministic.limitations)
      : safeStrings(resolution.limitations);
  const diagnosis = projectDiagnosis(
    deterministic.diagnosis,
    deterministic.metrics,
    version === "analysis_result.v2",
    scenarioLimitations,
  );
  const evidenceSummary = projectEvidenceSummary(result, version);
  const analysisRef: JsonRecord = {
    analysis_id: resultId,
    analysis_result_version: version,
    analysis_type: typeof analysisType === "string" ? analysisType : null,
    input_mode: inputMode,
  };

  if (version === "analysis_result.v2" && isRecord(evidence.derived_artifact)) {
    if (Object.keys(diagnosis.summary as JsonRecord).length > 24 ||
        (diagnosis.issues as unknown[]).length > 6) return null;

    const scenarioSource = analysisType === "dynamic_clicking"
      ? {
        scenario_profile_ref: resolution.scenario_profile_ref,
        analyzer_refs: typeof result.analysis_version === "string" ? [result.analysis_version] : [],
        support_status: deterministic.support_status,
        limitations: deterministic.limitations,
      }
      : rawScenario;
    const scenario: JsonRecord = {
      scenario_profile_ref: typeof scenarioSource.scenario_profile_ref === "string"
        ? scenarioSource.scenario_profile_ref
        : null,
      analyzer_refs: safeStrings(scenarioSource.analyzer_refs).slice(0, 16),
      support_status: new Set(["supported", "partial", "outcome_only", "unsupported", "unavailable"])
          .has(String(scenarioSource.support_status))
        ? scenarioSource.support_status
        : Object.keys(scenarioSource).length > 0 ? "supported" : "unavailable",
      limitations: safeStrings(scenarioSource.limitations).slice(0, 8),
      display_name: typeof resolution.display_name === "string" ? resolution.display_name : undefined,
      aim_family: typeof resolution.aim_family === "string" ? resolution.aim_family : undefined,
    };
    const facts = isRecord(deterministic.canonical_run_facts)
      ? deterministic.canonical_run_facts
      : isRecord(result.canonical_run_facts) ? result.canonical_run_facts : null;
    const runFacts = facts !== null && Buffer.byteLength(JSON.stringify(facts), "utf8") <= MAX_CONTEXT_BYTES
      ? {
        mode: "inline",
        field_registry_version: "source_field_registry.v1",
        facts,
        limitations: [],
      }
      : { mode: "unavailable", limitations: ["canonical_run_facts_not_inline_available"] };
    const artifact = evidence.derived_artifact;
    const v2Evidence: JsonRecord = { ...evidenceSummary, segment_refs: [] };
    if (isSafeScalar(artifact.artifact_ref)) v2Evidence.artifact_ref = artifact.artifact_ref;
    if (isSafeScalar(artifact.evidence_revision)) v2Evidence.evidence_revision = artifact.evidence_revision;
    const base = {
      schema_version: "coach_diagnostic_context.v2",
      analysis_ref: analysisRef,
      scenario,
      run_facts: runFacts,
      diagnosis,
      evidence_summary: v2Evidence,
      trends: [],
      training: { active_plan_ref: null, recent_retest_ref: null },
      limitations: [],
    };

    const processedTables = projectProcessedEventTables(evidence.processed_event_tables, String(resultId));
    if (processedTables !== null) {
      const v3 = {
        ...base,
        schema_version: "coach_diagnostic_context.v3",
        diagnosis: {
          ...diagnosis,
          summary: Object.fromEntries(Object.entries(diagnosis.summary as JsonRecord).map(([key, rawMetric]) => {
            if (!isRecord(rawMetric)) return [key, rawMetric];
            const { sample_refs: _sampleRefs, outlier_refs: _outlierRefs, ...metric } = rawMetric;
            return [key, metric];
          })),
        },
        processed_events: {
          mode: "table_refs",
          tables: processedTables,
          query_capabilities: PROCESSED_QUERY_CAPABILITIES,
          limitations: [],
        },
      };
      return v3;
    }
    return base;
  }

  return {
    schema_version: "coach_diagnostic_context.v1",
    analysis_ref: analysisRef,
    diagnosis,
    evidence_summary: evidenceSummary,
    warnings: projectWarnings(result.warnings, evidence.warnings),
  };
}

export function projectCoachDiagnosticContext(
  storedResult: unknown,
  sessionId?: number,
): CoachDiagnosticContext | null {
  const projected = isCanonicalDiagnosticContext(storedResult)
    ? storedResult
    : isRecord(storedResult) ? projectRawResult(storedResult, sessionId) : null;
  if (projected === null || !isCanonicalDiagnosticContext(projected)) return null;
  return Buffer.byteLength(JSON.stringify(projected), "utf8") <= MAX_CONTEXT_BYTES
    ? projected
    : null;
}
