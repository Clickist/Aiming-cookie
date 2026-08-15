import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { executeNativeEvidence } from "../src/evidence-native.ts";

// Native evidence commands read evidence.json from DATA_ROOT/analyses/{id}/.
// Point DATA_ROOT at a throwaway directory before any command executes
// (getDataRoot caches on the first call) and write the fixture artifact once.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-evidence-"));
process.env.DATA_ROOT = dataRoot;

const artifact = {
  schema_version: "analysis_evidence_artifact.v1",
  analysis_ref: "analysis:9",
  canonical_run_facts: { scenario_profile_ref: "scenario:test@1" },
  canonical_time_window: { start_ms: 0, end_ms: 1000, timebase_version: "tb.v1" },
  event_bundles: [{
    schema_version: "event_bundle.v1",
    analysis_ref: "analysis:9",
    events: [
      {
        event_id: "flick:1", event_kind: "static_flick",
        start_ms: 100, end_ms: 400, confidence: 1, limitations: [],
        attributes: { path_efficiency: 0.5, decel_frac: 0.8 },
      },
      {
        event_id: "flick:2", event_kind: "static_flick",
        start_ms: 500, end_ms: 800, confidence: 1, limitations: [],
        attributes: { path_efficiency: 0.9, decel_frac: 0.4 },
      },
    ],
  }],
  evidence_segments: [
    {
      segment_id: "analysis:9:segment:s1", segment_kind: "typical",
      start_ms: 0, end_ms: 450, focus_start_ms: 100, focus_end_ms: 400,
    },
    {
      segment_id: "analysis:9:segment:s2", segment_kind: "worst",
      start_ms: 450, end_ms: 1000, focus_start_ms: 500, focus_end_ms: 800,
    },
  ],
  metric_records: [{
    metric_key: "static_clicking.decel_frac",
    metric_version: "native_flicking.v1",
    value: 0.6,
    unit: "dimensionless",
    availability: "available",
    classification: "deterministic",
    provenance: { kind: "derived", source_refs: [] },
    population: { sample_count: 2, valid_count: 2, excluded_count: 0 },
    condition_refs: [],
    event_refs: ["flick:1", "flick:2"],
    evidence_segment_refs: [],
    coverage: 1,
    confidence: 1,
    limitations: [],
  }],
  sample_sets: [],
  signal_bundles: [],
  normalized_outcome_records: [],
};
mkdirSync(join(dataRoot, "analyses", "9"), { recursive: true });
writeFileSync(join(dataRoot, "analyses", "9", "evidence.json"), JSON.stringify(artifact));

const TABLE_REF = "analysis:9:table:static_flick";

function run(commandName: string, parameters: Record<string, unknown>) {
  return executeNativeEvidence(commandName, parameters, "owner");
}

test("events.filter with well-formed predicates still matches correctly", () => {
  const result = run("analysis.events.filter", {
    table_ref: TABLE_REF,
    predicates: [{ field: "path_efficiency", operator: "lt", value: 0.7 }],
  });
  assert.equal(result.status, "succeeded");
  assert.equal(result.result?.matched_count, 1);
  assert.deepEqual(result.result?.event_refs, ["flick:1"]);
});

test("events.filter rejects predicates passed as loose top-level fields", () => {
  // Deep-test Bug 1: the model passed {field, operator, value} at the top
  // level; the command silently matched every row (matched_count = full table).
  const result = run("analysis.events.filter", {
    table_ref: TABLE_REF,
    field: "path_efficiency",
    operator: "lt",
    value: 0.7,
  });
  assert.equal(result.status, "failed");
  assert.equal(result.warning_or_error?.code, "invalid_parameters");
  assert.match(result.warning_or_error?.message ?? "", /predicates/);
});

test("events.filter rejects malformed predicate shapes", () => {
  const cases: Array<Record<string, unknown>> = [
    { table_ref: TABLE_REF, predicates: [] },
    { table_ref: TABLE_REF, predicates: [{ conditions: [{ field: "path_efficiency", operator: "lt", value: 0.7 }] }] },
    { table_ref: TABLE_REF, predicates: [{ field: "path_efficiency", operator: "lt", value: 0.7, mode: "strict" }] },
    { table_ref: TABLE_REF, predicates: [{ operator: "lt", value: 0.7 }] },
    { table_ref: TABLE_REF, predicates: [{ field: "path_efficiency", operator: "below", value: 0.7 }] },
    { table_ref: TABLE_REF, predicates: [{ field: "decel_frac", operator: "between", value: [0.5] }] },
    { table_ref: TABLE_REF, predicates: "path_efficiency < 0.7" },
  ];
  for (const parameters of cases) {
    const result = run("analysis.events.filter", parameters);
    assert.equal(result.status, "failed", JSON.stringify(parameters));
    assert.equal(result.warning_or_error?.code, "invalid_parameters", JSON.stringify(parameters));
  }
});

test("evidence.compare accepts segment refs and compares their metrics", () => {
  // Deep-test Bug 3: segment refs were passed straight to requireArtifact,
  // which only resolves analysis:N refs, so segment comparison always failed
  // with "evidence is unavailable".
  const result = run("analysis.evidence.compare", {
    evidence_refs: ["analysis:9:segment:s1", "analysis:9:segment:s2"],
    metric_keys: ["static_clicking.decel_frac"],
  });
  assert.equal(result.status, "succeeded", JSON.stringify(result.warning_or_error));
  const comparisons = result.result?.comparisons as Array<Record<string, unknown>>;
  assert.equal(comparisons.length, 2);
  const first = comparisons[0].metrics as Array<{ value: number }>;
  const second = comparisons[1].metrics as Array<{ value: number }>;
  assert.equal(first[0].value, 0.8);
  assert.equal(second[0].value, 0.4);
  assert.deepEqual(comparisons[1].deltas_from_first, { "static_clicking.decel_frac": -0.4 });
});
