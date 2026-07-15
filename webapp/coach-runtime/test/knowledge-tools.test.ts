import assert from "node:assert/strict";
import test from "node:test";

import { createCoachKnowledgeTool, getCoachKnowledge } from "../src/knowledge-tools.ts";

const ALLOWED_SOURCE_LEVELS = new Set([
  "product_contract",
  "academic_peer_reviewed",
  "community_consensus",
  "personal_experience_unverified",
  "experimental",
]);
const ALLOWED_CLAIM_LEVELS = new Set([
  "deterministic_rule",
  "research_supported",
  "community_consensus",
  "experimental",
]);

test("knowledge retrieval is bounded, versioned and traceable", async () => {
  const result = getCoachKnowledge({
    topic: "stopping_corrections",
    issue_signal: "reverse high",
    metric_refs: ["metric:reverse_ratio"],
    supported_use: "definition",
  });
  assert.equal(result.issue_signal, "reverse_ratio high");
  assert.ok(result.entries.length >= 1 && result.entries.length <= 3);
  for (const entry of result.entries) {
    assert.match(entry.entry_ref, /^knowledge:/);
    assert.ok(entry.entry_version >= 1);
    assert.ok(entry.sources.length > 0);
    assert.ok(entry.sources.every((source) => ALLOWED_SOURCE_LEVELS.has(source.source_level)));
    assert.ok(ALLOWED_CLAIM_LEVELS.has(entry.max_claim_level));
    assert.ok(entry.limitations.length > 0);
    assert.notEqual(entry.max_claim_level, "measured");
  }

  const tool = createCoachKnowledgeTool();
  const output = await tool.execute("knowledge-call", {
    topic: "body_tension",
    issue_signal: "ptc high",
    metric_refs: ["metric:ptc"],
    supported_use: "candidate_hypothesis",
  });
  const parsed = JSON.parse(output.content[0]?.text ?? "null");
  assert.ok(parsed.entries.length >= 1);
  assert.equal(output.details.event.type, "knowledge");
  assert.equal(output.details.event.registry_version, parsed.registry_version);
  assert.deepEqual(output.details.event.entry_refs, parsed.entries.map((entry: { entry_ref: string }) => entry.entry_ref));
  assert.ok(output.details.event.max_claim_levels.every((level: string) => level === "experimental" || level === "deterministic_rule"));
});

test("unknown knowledge query returns an empty bounded result", () => {
  const result = getCoachKnowledge({ topic: "unknown-topic" });
  assert.deepEqual(result.entries, []);
});
