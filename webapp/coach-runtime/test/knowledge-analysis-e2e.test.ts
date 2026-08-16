import assert from "node:assert/strict";
import test from "node:test";

import { runAnalysisKnowledgeE2E } from "./knowledge-analysis-e2e-fixture.ts";

test("canonical Analysis issue drives knowledge lookup via the materialized index read path", async () => {
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
  // The knowledge tool is retired: lookup happens by reading knowledge/index.json
  // and matching the issue signal against the entry signals listed there.
  assert.match(response.reply ?? "", /knowledge:static\.flicking-terminal-control@3/);
  assert.equal(response.tool_events.length, 0);
  assert.ok(!JSON.stringify(response).includes("threshold_requires_product_calibration"));
});
