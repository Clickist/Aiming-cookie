import assert from "node:assert/strict";
import test from "node:test";

import { projectCoachDiagnosticContext } from "../src/coach-context-projection.ts";

const analysisRef = {
  analysis_id: "analysis:42",
  analysis_result_version: "analysis_result.v2",
  analysis_type: "flicking",
  input_mode: "input_native",
};
const diagnosis = {
  profile: {},
  issues: [],
  summary: {
    distance: { value: 12, classification: "deterministic" },
  },
  comparison: null,
  meta: {},
};
const evidenceSummary = {
  availability: { raw_input: "available" },
  alignment: { status: "aligned" },
};

const canonicalV1 = {
  schema_version: "coach_diagnostic_context.v1",
  analysis_ref: analysisRef,
  diagnosis,
  evidence_summary: evidenceSummary,
  warnings: [],
};
const canonicalV2 = {
  schema_version: "coach_diagnostic_context.v2",
  analysis_ref: analysisRef,
  scenario: {
    scenario_profile_ref: null,
    analyzer_refs: [],
    support_status: "unavailable",
    limitations: [],
  },
  run_facts: {
    mode: "unavailable",
    limitations: ["canonical_run_facts_not_inline_available"],
  },
  diagnosis,
  evidence_summary: { ...evidenceSummary, segment_refs: [] },
  trends: [],
  training: { active_plan_ref: null, recent_retest_ref: null },
  limitations: [],
};
const processedTable = {
  schema_version: "processed_event_table.v1",
  table_ref: "analysis:42:table:static_flick",
  analysis_ref: "analysis:42",
  analyzer_ref: "native_flicking.v1",
  family: "static_clicking",
  event_kind: "static_flick",
  row_count: 1,
  included_count: 1,
  excluded_count: 0,
  completeness: "complete",
  field_catalog: [{
    field_key: "corrective_count",
    role: "metric",
    value_type: "number",
    unit: "count",
    metric_key: "static_clicking.corrective_count",
    metric_version: "native_flicking.v1",
    expected_direction: "comparison_only",
    limitations: [],
  }],
  index_fields: ["corrective_count"],
  rows_ref: "analysis:42:table:static_flick",
  limitations: [],
};
const canonicalV3 = {
  ...canonicalV2,
  schema_version: "coach_diagnostic_context.v3",
  processed_events: {
    mode: "table_refs",
    tables: [processedTable],
    query_capabilities: [
      "analysis.events.list",
      "analysis.events.get",
      "analysis.events.rank",
      "analysis.events.filter",
      "analysis.events.aggregate",
      "analysis.events.co_occurrence",
      "analysis.events.sequence",
      "analysis.evidence.compare",
    ],
    limitations: [],
  },
};

test("preserves canonical Coach diagnostic context v1/v2/v3", () => {
  for (const context of [canonicalV1, canonicalV2, canonicalV3]) {
    assert.deepEqual(projectCoachDiagnosticContext(context), context);
  }
});

test("projects a stored raw v1 result and fills its session analysis ref", () => {
  const projected = projectCoachDiagnosticContext({
    schema_version: "analysis_result.v1",
    summary_type: "flicking",
    deterministic: {
      diagnosis: {
        summary: { accuracy: { value: 0.91, unit: "ratio" } },
      },
    },
    artifact_manifest: {
      inputs: [{ kind: "input_video", status: "available" }],
    },
  }, 7);

  assert.equal(projected?.schema_version, "coach_diagnostic_context.v1");
  assert.deepEqual(projected?.analysis_ref, {
    analysis_id: "analysis:7",
    analysis_result_version: "analysis_result.v1",
    analysis_type: "flicking",
    input_mode: "unknown",
  });
  assert.deepEqual((projected?.diagnosis as Record<string, unknown>).summary, {
    accuracy: { value: 0.91, unit: "ratio" },
  });
});

test("projects a stored raw v2 result with processed tables to canonical v3", () => {
  const projected = projectCoachDiagnosticContext({
    schema_version: "analysis_result.v2",
    analysis_version: "native_flicking.v1",
    analysis_id: "analysis:42",
    analysis_type: "flicking",
    input_mode: "input_native",
    deterministic: {
      metrics: {
        distance: { value: 12, classification: "deterministic" },
        inferred: { value: 99, classification: "inferred" },
      },
      diagnosis: { profile: {}, issues: [], summary: {}, comparison: null, meta: {} },
    },
    evidence: {
      availability: { raw_input: "available" },
      alignment: { status: "aligned" },
      derived_artifact: {
        artifact_ref: "analysis:42:evidence:abc",
        evidence_revision: "sha256:abc",
      },
      processed_event_tables: [processedTable],
      warnings: [],
    },
    warnings: [],
  });

  assert.equal(projected?.schema_version, "coach_diagnostic_context.v3");
  const projectedDiagnosis = projected?.diagnosis as Record<string, unknown>;
  assert.deepEqual(projectedDiagnosis.summary, {
    distance: { value: 12, classification: "deterministic" },
  });
  assert.deepEqual((projected?.processed_events as Record<string, unknown>).tables, [processedTable]);
});

test("keeps Python-compatible inline run facts above 8 KiB when the context stays under 32 KiB", () => {
  const projected = projectCoachDiagnosticContext({
    schema_version: "analysis_result.v2",
    analysis_id: "analysis:42",
    analysis_type: "flicking",
    input_mode: "input_native",
    deterministic: {
      canonical_run_facts: { detail: "x".repeat(9 * 1024) },
      metrics: {},
      diagnosis: {},
    },
    evidence: {
      availability: {},
      alignment: {},
      derived_artifact: { artifact_ref: "analysis:42:evidence:abc" },
    },
  });

  assert.equal((projected?.run_facts as Record<string, unknown>).mode, "inline");
});

test("fails closed for unsupported, invalid, and oversized contexts", () => {
  assert.equal(projectCoachDiagnosticContext({ schema_version: "analysis_result.v99" }), null);
  assert.equal(projectCoachDiagnosticContext({
    schema_version: "analysis_result.v2",
    analysis_id: "analysis:42",
    analysis_type: "flicking",
    input_mode: "unsupported",
  }), null);
  assert.equal(projectCoachDiagnosticContext({
    ...canonicalV1,
    diagnosis: {
      ...diagnosis,
      issues: [{ signal: "x".repeat(33 * 1024) }],
    },
  }), null);
});
