import assert from "node:assert/strict";
import test from "node:test";

import { runAnalysisKnowledgeE2E } from "./knowledge-analysis-e2e-fixture.ts";

test("canonical Analysis issue and metric drive Pi knowledge retrieval and safe event refs", async () => {
  const context = {
    schema_version: "coach_diagnostic_context.v1",
    analysis_ref: {
      analysis_id: "analysis:e2e",
      analysis_result_version: "analysis_result.v2",
      analysis_type: "flicking",
      input_mode: "input_native",
    },
    diagnosis: {
      profile: { archetype_id: "rough_braker", label: "制动波动", confidence: 1 },
      issues: [{
        signal: "sparc low",
        severity: "info",
        priority: 1,
        priority_reason: "[experimental] 观察项排序第 1",
        claim_level: "experimental",
        metric_refs: ["sparc"],
        limitations: ["threshold_requires_product_calibration"],
      }],
      summary: { sparc: { med: -7, classification: "deterministic" } },
      comparison: null,
      meta: { summary_type: "flicking" },
    },
    evidence_summary: { availability: { raw_input: "available" }, alignment: { status: "aligned" } },
    warnings: [],
  };

  const response = await runAnalysisKnowledgeE2E(JSON.stringify(context));
  assert.equal(response.ok, true);
  assert.match(response.reply ?? "", /knowledge:static\.flicking-terminal-control@2/);
  assert.equal(response.tool_events.length, 1);
  const event = response.tool_events[0];
  assert.equal(event?.type, "knowledge");
  if (event?.type !== "knowledge") throw new Error("missing knowledge event");
  assert.equal(event.issue_signal, "sparc low");
  assert.ok(event.entry_refs.includes("knowledge:static.flicking-terminal-control@2"));
  assert.deepEqual(event.entry_refs.map((ref) => Number(ref.split("@")[1])), event.entry_versions);
  assert.ok(event.section_refs.length > 0);
  assert.ok(event.claim_refs.every((ref) => ref.startsWith("claim:")));
  assert.ok(event.claim_levels.length >= event.max_claim_levels.length);
  assert.ok(!JSON.stringify(event).includes("threshold_requires_product_calibration"));
});
