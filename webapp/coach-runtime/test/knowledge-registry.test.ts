import assert from "node:assert/strict";
import test from "node:test";

import {
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  validateKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

test("loads the canonical packaged registry without a TypeScript prose copy", () => {
  const registry = loadKnowledgeRegistry();
  assert.equal(registry.schema_version, "coach_knowledge_registry.v1");
  assert.ok(registry.entries.length >= 40);
  assert.match(entryRef(registry.entries[0]), /^knowledge:[a-z0-9._-]+@\d+$/);
});

test("deterministic query matches signal alias, metric, topic and is bounded", () => {
  const registry = loadKnowledgeRegistry();
  const results = queryKnowledgeRegistry(registry, {
    topic: "stopping_corrections",
    issue_signal: "reverse high",
    metric_refs: ["metric:reverse_ratio"],
    supported_use: "definition",
  });
  assert.ok(results.length >= 1 && results.length <= 3);
  assert.equal(results[0].entry_id, "metric.stopping-corrections.definition");
  assert.ok(results.every((entry) => entry.status === "active"));
});

test("query requires a condition and unknown input never falls back to all entries", () => {
  const registry = loadKnowledgeRegistry();
  assert.throws(() => queryKnowledgeRegistry(registry, {}), /query condition/);
  assert.deepEqual(queryKnowledgeRegistry(registry, { topic: "unknown-topic" }), []);
});

test("validator enforces source/claim and body/settings experimental discipline", () => {
  const registry = structuredClone(loadKnowledgeRegistry());
  const body = registry.entries.find((entry) => entry.category === "body_tension_hypothesis");
  assert.ok(body);
  body.max_claim_level = "community_consensus";
  assert.throws(() => validateKnowledgeRegistry(registry), /experimental/);
});
