import assert from "node:assert/strict";
import test from "node:test";

import {
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  resolveKnowledgeEntry,
  validateKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

test("loads the canonical packaged registry without a TypeScript prose copy", () => {
  const registry = loadKnowledgeRegistry();
  assert.equal(registry.schema_version, "coach_knowledge_registry.v2");
  assert.ok(registry.entries.length >= 10);
  assert.match(entryRef(registry.entries[0]), /^knowledge:[a-z0-9._-]+@\d+$/);
});

test("deterministic query matches signal alias, metric, topic and is bounded", () => {
  const registry = loadKnowledgeRegistry();
  const results = queryKnowledgeRegistry(registry, {
    topic: "static_clicking",
    issue_signal: "reverse high",
    metric_refs: ["metric:reverse_ratio"],
    supported_use: "definition",
  });
  assert.ok(results.length >= 1 && results.length <= 3);
  assert.equal(results[0].entry_id, "static.flicking-terminal-control");
  assert.ok(results.every((entry) => entry.status === "active"));
});

test("historical refs resolve only against their explicit v1 registry", () => {
  const legacy = loadKnowledgeRegistry("2026-07-14.v1");
  assert.equal(legacy.schema_version, "coach_knowledge_registry.v1");
  const historical = resolveKnowledgeEntry(
    "2026-07-14.v1",
    "knowledge:metric.stopping-corrections.definition@1",
  );
  assert.equal(historical.entry_id, "metric.stopping-corrections.definition");
  assert.throws(
    () => resolveKnowledgeEntry("2026-07-22.v2", "knowledge:metric.stopping-corrections.definition@1"),
    /unknown knowledge entry/,
  );
});

test("query requires a condition and unknown input never falls back to all entries", () => {
  const registry = loadKnowledgeRegistry();
  assert.throws(() => queryKnowledgeRegistry(registry, {}), /query condition/);
  assert.deepEqual(queryKnowledgeRegistry(registry, { topic: "unknown-topic" }), []);
});

test("validator enforces source/claim and body/settings experimental discipline", () => {
  const registry = structuredClone(loadKnowledgeRegistry("2026-07-14.v1"));
  const body = registry.entries.find((entry) => entry.category === "body_tension_hypothesis");
  if (!body || !("max_claim_level" in body)) throw new Error("missing legacy body entry");
  body.max_claim_level = "community_consensus";
  assert.throws(() => validateKnowledgeRegistry(registry), /experimental/);
});

test("v2 research sources are primary and each section source covers its entry families", () => {
  const registry = structuredClone(loadKnowledgeRegistry());
  if (registry.schema_version !== "coach_knowledge_registry.v2") throw new Error("missing v2 registry");
  assert.ok(!registry.sources.some((source) => source.source_ref === "research.task10-assessment"));
  assert.ok(!registry.sources.some((source) => (
    source.source_level === "academic_peer_reviewed"
    && source.author_or_org === "Aiming Cookie research assessment"
  )));

  const source = registry.sources.find((item) => item.source_ref === "community.rawinput-tracking");
  if (!source) throw new Error("missing tracking source");
  source.applicability = ["predictable_tracking"];
  assert.throws(() => validateKnowledgeRegistry(registry), /family scope/);
});
