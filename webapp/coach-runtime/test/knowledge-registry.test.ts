import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  REGISTRY_SCHEMA_VERSION_V1,
  activeScenarioProfileRefs,
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  queryKnowledgeRegistryWithTotals,
  resolveKnowledgeEntry,
  validateKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

function loadRawV4(): ReturnType<typeof loadKnowledgeRegistry> {
  return JSON.parse(
    readFileSync(new URL("../../../knowledge/coach/registry.v4.json", import.meta.url), "utf8"),
  ) as ReturnType<typeof loadKnowledgeRegistry>;
}

function loadRawV5(): ReturnType<typeof loadKnowledgeRegistry> {
  return JSON.parse(
    readFileSync(new URL("../../../knowledge/coach/registry.v5.json", import.meta.url), "utf8"),
  ) as ReturnType<typeof loadKnowledgeRegistry>;
}

function loadRawScenarioRegistry(): unknown {
  return JSON.parse(
    readFileSync(new URL("../../../knowledge/scenarios/registry.v1.json", import.meta.url), "utf8"),
  );
}

function loadRawScenarioManifest(): unknown {
  return JSON.parse(
    readFileSync(new URL("../../../knowledge/scenarios/launch-manifest.v1.json", import.meta.url), "utf8"),
  );
}

test("loads the canonical packaged registry without a TypeScript prose copy", () => {
  const registry = loadKnowledgeRegistry();
  assert.equal(registry.schema_version, "coach_knowledge_registry.v3");
  assert.ok(registry.entries.length >= 19);
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
  assert.ok(results.length >= 1 && results.length <= 8);
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

test("exact entry refs take precedence and use-only fallback returns no arbitrary entries", () => {
  const registry = loadKnowledgeRegistry();
  const reference = entryRef(registry.entries[0]!);
  const exact = queryKnowledgeRegistry(registry, {
    registry_version: registry.registry_version,
    entry_ref: reference,
    topic: "unknown-topic",
  });
  assert.deepEqual(exact.map(entryRef), [reference]);
  assert.deepEqual(queryKnowledgeRegistry(registry, { supported_use: "explanation_only" }), []);
  assert.throws(
    () => queryKnowledgeRegistry(registry, {
      registry_version: "2026-07-29.v4",
      entry_ref: reference,
    }),
    /registry version does not match/,
  );
});

test("validator enforces source/claim and body/settings experimental discipline", () => {
  const registry = structuredClone(loadKnowledgeRegistry("2026-07-14.v1"));
  const body = registry.entries.find((entry) => entry.category === "body_tension_hypothesis");
  if (!body || !("max_claim_level" in body)) throw new Error("missing legacy body entry");
  body.max_claim_level = "community_consensus";
  assert.throws(() => validateKnowledgeRegistry(registry), /experimental/);
});

test("v2 research sources are primary and each section source covers its entry families", () => {
  const registry = structuredClone(loadKnowledgeRegistry("2026-07-28.v3"));
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

test("v3 scenario prescriptions are explicit and limited to reviewed families", () => {
  const registry = loadKnowledgeRegistry("2026-07-28.v3");
  assert.equal(registry.registry_version, "2026-07-28.v3");
  if (registry.schema_version !== "coach_knowledge_registry.v2") throw new Error("missing v3 registry");
  const expected = new Map([
    ["static.flicking-terminal-control", "scenario:static.1wall_6targets_small@1"],
    ["dynamic.click-error-and-acquisition", "scenario:dynamic.pasu_small_reload@1"],
    ["dynamic.speed-matching-and-reading", "scenario:dynamic.pasu_small_reload@1"],
    ["tracking.predictable-speed-matching", "scenario:tracking.whj_smooth_strafe_sphere_easy@1"],
    ["switching.transition-and-arrival", "scenario:switching.beants_larger@1"],
  ]);
  for (const entry of registry.entries) {
    if (!("scenario_prescription" in entry)) throw new Error("missing scenario prescription");
    const expectedRef = expected.get(entry.entry_id);
    if (!expectedRef) {
      assert.equal(entry.scenario_prescription, "not_applicable");
      continue;
    }
    assert.notEqual(entry.scenario_prescription, "not_applicable");
    if (entry.scenario_prescription === "not_applicable") continue;
    assert.equal(entry.scenario_prescription.scenario_profile_ref, expectedRef);
    assert.equal(entry.scenario_prescription.review_after, "next comparable practice session");
    assert.equal(entry.scenario_prescription.claim_level, "experimental");
    assert.ok(entry.scenario_prescription.practice_condition);
    assert.ok(entry.scenario_prescription.source_refs.length);
  }
});

test("v3 rejects malformed scenario prescriptions", () => {
  const registry = structuredClone(loadKnowledgeRegistry("2026-07-28.v3"));
  if (registry.schema_version !== "coach_knowledge_registry.v2") throw new Error("missing v3 registry");
  const prescribed = registry.entries.find((entry) => entry.entry_id === "static.flicking-terminal-control");
  if (!prescribed || !("scenario_prescription" in prescribed) || prescribed.scenario_prescription === "not_applicable") {
    throw new Error("missing prescribed entry");
  }

  delete (prescribed.scenario_prescription as Partial<typeof prescribed.scenario_prescription>).scenario_profile_ref;
  assert.throws(() => validateKnowledgeRegistry(registry), /scenario_prescription/);
});

test("scenario prescriptions resolve only to canonical active scenario profiles", () => {
  const activeRefs = activeScenarioProfileRefs(
    loadRawScenarioRegistry(),
    loadRawScenarioManifest(),
  );
  assert.ok(activeRefs.has("scenario:static.1wall_6targets_small@1"));

  const pendingManifest = structuredClone(loadRawScenarioManifest()) as {
    entries: Array<{ scenario_profile_ref: string; status: string }>;
  };
  pendingManifest.entries[0].status = "pending_gate";
  assert.ok(!activeScenarioProfileRefs(
    loadRawScenarioRegistry(), pendingManifest,
  ).has(pendingManifest.entries[0].scenario_profile_ref));

  for (const scenarioProfileRef of [
    "scenario:movement.unreviewed@99",
    "scenario:static.retired@1",
    "scenario:static.1wall_6targets_small@99",
  ]) {
    const registry = structuredClone(loadKnowledgeRegistry("2026-07-28.v3"));
    if (registry.schema_version !== "coach_knowledge_registry.v2") throw new Error("missing v3 registry");
    const prescribed = registry.entries.find((entry) => entry.entry_id === "static.flicking-terminal-control");
    if (!prescribed || !("scenario_prescription" in prescribed) || prescribed.scenario_prescription === "not_applicable") {
      throw new Error("missing prescribed entry");
    }
    prescribed.scenario_prescription.scenario_profile_ref = scenarioProfileRef;
    assert.throws(() => validateKnowledgeRegistry(registry), /active scenario/);
  }
});

test("v4 loads article-granular community entries with capability boundaries", () => {
  const registry = loadKnowledgeRegistry("2026-07-29.v4");
  assert.equal(registry.schema_version, "coach_knowledge_registry.v3");
  const explanation = registry.entries.find((entry) => entry.entry_id === "community.score-farming-context");
  const experiment = registry.entries.find((entry) => entry.entry_id === "community.friction-and-surface");
  const prescribed = registry.entries.find((entry) => entry.entry_id === "static.flicking-terminal-control");
  if (!explanation || !experiment || !prescribed) throw new Error("missing v4 entries");
  assert.deepEqual(explanation.supported_uses, ["explanation_only"]);
  assert.equal(explanation.cue, undefined);
  assert.deepEqual(experiment.supported_uses, [
    "explanation_only", "diagnosis_support", "candidate_experiment",
  ]);
  assert.notEqual(experiment.cue, "not_applicable");
  assert.equal(experiment.near_transfer_retest, undefined);
  assert.deepEqual(prescribed.supported_uses, [
    "explanation_only", "diagnosis_support", "candidate_experiment", "scenario_prescription",
  ]);
  assert.notEqual(prescribed.scenario_prescription, "not_applicable");
  assert.deepEqual(validateKnowledgeRegistry(registry), registry);
});

test("v4 versions migrated entries without changing community entry refs", () => {
  const v3 = loadKnowledgeRegistry("2026-07-28.v3");
  const v4 = loadKnowledgeRegistry("2026-07-29.v4");
  const migratedIds = new Set(v3.entries.map((entry) => entry.entry_id));

  for (const entry of v4.entries) {
    assert.equal(entry.entry_version, migratedIds.has(entry.entry_id) ? 2 : 1);
  }
  assert.equal(
    new Set(v3.entries.map(entryRef)).intersection(new Set(v4.entries.map(entryRef))).size,
    0,
  );
});

test("v4 TypeScript validator matches the shared size and unsafe-text boundaries", () => {
  const manySources = structuredClone(loadRawV4());
  if (!manySources.sources?.[0]) throw new Error("missing source template");
  while (manySources.sources.length < 257) {
    manySources.sources.push({
      ...structuredClone(manySources.sources[0]),
      source_ref: `community.synthetic.source-${manySources.sources.length}`,
    });
  }
  assert.doesNotThrow(() => validateKnowledgeRegistry(manySources));

  const manyTopics = structuredClone(loadRawV4());
  manyTopics.entries[0].topics = Array.from({ length: 33 }, (_, index) => `topic.${index}`);
  assert.doesNotThrow(() => validateKnowledgeRegistry(manyTopics));

  const unsafe = structuredClone(loadRawV4());
  if (!("definition" in unsafe.entries[0])) throw new Error("missing v4 definition");
  unsafe.entries[0].definition.text = "api_key=supersecret";
  assert.throws(() => validateKnowledgeRegistry(unsafe), /unsafe text/);

  const blank = structuredClone(loadRawV4());
  if (!("definition" in blank.entries[0])) throw new Error("missing v4 definition");
  blank.entries[0].definition.text = "   ";
  assert.throws(() => validateKnowledgeRegistry(blank), /bounded text/);

  const longListText = structuredClone(loadRawV4());
  if (!("alternative_explanations" in longListText.entries[0])) throw new Error("missing v4 prose list");
  longListText.entries[0].alternative_explanations[0] = "x".repeat(501);
  assert.throws(() => validateKnowledgeRegistry(longListText), /length|bounded text/);

  const invalidAlias = structuredClone(loadRawV4());
  invalidAlias.signal_aliases["bad?alias"] = "canonical.signal";
  assert.throws(() => validateKnowledgeRegistry(invalidAlias), /signal alias/);
});

test("v4 rejects malformed capability prefixes and forbidden fields", () => {
  const invalidPrefix = structuredClone(loadKnowledgeRegistry("2026-07-29.v4"));
  const experiment = invalidPrefix.entries.find((entry) => entry.entry_id === "community.friction-and-surface");
  if (!experiment) throw new Error("missing experiment entry");
  experiment.supported_uses = ["candidate_experiment"];
  assert.throws(() => validateKnowledgeRegistry(invalidPrefix), /capability|supported_uses/);

  const forbidden = structuredClone(loadKnowledgeRegistry("2026-07-29.v4"));
  const explanation = forbidden.entries.find((entry) => entry.entry_id === "community.score-farming-context");
  if (!explanation) throw new Error("missing explanation entry");
  explanation.cue = {
    section_ref: "community.score-farming-context.cue",
    claim_level: "community_practice",
    source_refs: ["community.rawinput.article.scorefarm"],
    text: "unexpected cue",
  };
  assert.throws(() => validateKnowledgeRegistry(forbidden), /forbidden|capability/);
});

test("v5 adds only revisable mouse-fit and input-latency differential intake", () => {
  const registry = loadKnowledgeRegistry("2026-08-06.v5");
  assert.equal(registry.registry_version, "2026-08-06.v5");
  assert.deepEqual(validateKnowledgeRegistry(loadRawV5()), registry);
  const sources = new Map(registry.sources.map((source) => [source.source_ref, source]));
  assert.deepEqual(sources.get("research.mouse-shape-ergonomics")?.supports_sections, ["mechanisms"]);
  assert.deepEqual(sources.get("research.cursor-latency-tracking")?.supports_sections, ["mechanisms"]);

  for (const entryId of [
    "hypothesis.mouse-fit-differential-intake",
    "hypothesis.input-latency-differential-intake",
  ]) {
    const entry = registry.entries.find((candidate) => candidate.entry_id === entryId);
    if (!entry || !("quality_prerequisites" in entry)) throw new Error(`missing ${entryId}`);
    assert.deepEqual(entry.supported_uses, [
      "explanation_only", "diagnosis_support", "candidate_experiment",
    ]);
    assert.ok(entry.quality_prerequisites.includes("user_report_available"));
    assert.ok(entry.alternative_explanations.length > 0);
    assert.ok(entry.counterevidence.length > 0);
    assert.notEqual(entry.cue, "not_applicable");
    assert.notEqual(entry.matched_retest, "not_applicable");
    assert.notEqual(entry.stop_adjust_rule, "not_applicable");
    assert.deepEqual(entry.definition.source_refs, ["product.problem-hypothesis-spec"]);
    if (entry.stop_adjust_rule === "not_applicable") continue;
    assert.match(entry.stop_adjust_rule.map((section) => section.text).join(" "), /pain/i);
  }
  const latency = registry.entries.find((entry) => entry.entry_id === "hypothesis.input-latency-differential-intake");
  if (!latency || !("definition" in latency)) throw new Error("missing latency entry");
  assert.match(latency.definition.text, /^Cursor or visual-feedback delay/);
});

test("v6 adds reviewed community practice without device recommendations", () => {
  const registry = loadKnowledgeRegistry("2026-08-06.v6");
  assert.equal(registry.registry_version, "2026-08-06.v6");
  assert.deepEqual(validateKnowledgeRegistry(registry), registry);
  const efficiency = registry.entries.find((entry) => entry.entry_id === "community.aim-efficiency-framework");
  const practice = registry.entries.find((entry) => entry.entry_id === "community.practice-intent-and-autopilot");
  const tension = registry.entries.find((entry) => entry.entry_id === "hypothesis.tension-management");
  if (!efficiency || !practice || !tension) throw new Error("missing v6 community entries");
  assert.deepEqual(efficiency.supported_uses, ["explanation_only"]);
  assert.deepEqual(practice.supported_uses, ["explanation_only"]);
  const tempo = registry.entries.find((entry) => entry.entry_id === "community.qiluno.distance-adaptive-click-tempo");
  const timing = registry.entries.find((entry) => entry.entry_id === "community.qiluno.confirmation-timing-schools");
  const reset = registry.entries.find((entry) => entry.entry_id === "community.qiluno.reset-as-continuity");
  const reactive = registry.entries.find((entry) => entry.entry_id === "tracking.reactive-change-response");
  const smoothness = registry.entries.find((entry) => entry.entry_id === "tracking.control-smoothness");
  if (!tempo || !timing || !reset || !reactive || !smoothness) throw new Error("missing Qiluno community entries");
  assert.deepEqual(tempo.supported_uses, ["explanation_only"]);
  assert.match(tempo.definition.text, /near targets/i);
  assert.match(JSON.stringify(timing), /settled/i);
  assert.match(JSON.stringify(timing), /deceleration/i);
  assert.match(JSON.stringify(timing), /single universally correct/i);
  assert.match(reset.forbidden_inferences.join(" "), /user report/i);
  for (const entry of [tempo, timing, reset]) {
    for (const field of ["cue", "dose_guardrail", "matched_retest", "near_transfer_retest", "stop_adjust_rule", "scenario_prescription"]) {
      assert.ok(!(field in entry));
    }
  }
  assert.match(reactive.scope.text, /has not changed/i);
  assert.match(smoothness.mechanisms.at(-1)?.text ?? "", /continuous reading/i);
  const staticSource = registry.sources.find((source) => source.source_ref === "community.qiluno.bilibili.static-guide");
  assert.equal(staticSource?.author_or_org, "天才烧酒琪露诺");
  assert.equal(staticSource?.source_level, "coach_first_party");
  assert.equal(staticSource?.published_at, "2024-03-10");
  assert.match(staticSource?.locator ?? "", /BV1Xt421L72J/);
  for (const entry of [reactive, smoothness]) assert.equal(entry.entry_version, 2);
  assert.equal(tension.entry_version, 3);
  assert.ok(tension.mechanisms.some((section) => /arm/i.test(section.text) && /wrist/i.test(section.text)));
  assert.ok(!JSON.stringify(registry).match(/\b(?:logitech|wooting|op1|gpx)\b/i));
  assert.ok(!JSON.stringify(registry).match(/90%|94%|转化率97|One War/i));
});

test("v5 remains loadable after v6 is packaged", () => {
  assert.equal(loadKnowledgeRegistry("2026-08-06.v5").registry_version, "2026-08-06.v5");
});

test("query totals report matches beyond the result cap", () => {
  const synthetic = {
    schema_version: REGISTRY_SCHEMA_VERSION_V1,
    registry_version: "synthetic.v1",
    signal_aliases: {},
    entries: Array.from({ length: 12 }, (_, index) => ({
      entry_id: `synthetic.entry-${String(index).padStart(2, "0")}`,
      entry_version: 1,
      status: "active" as const,
      category: "metric_definition",
      topics: ["synthetic_topic"],
      signals: [],
      metric_refs: [],
      text: "synthetic entry",
      sources: [{ source_ref: "synthetic.source", source_level: "product_contract" }],
      max_claim_level: "deterministic_rule",
      limitations: ["synthetic"],
      counterevidence: [],
      supported_uses: ["definition"],
    })),
  };
  const outcome = queryKnowledgeRegistryWithTotals(synthetic, { topic: "synthetic_topic" });
  assert.equal(outcome.entries.length, 8);
  assert.equal(outcome.total_matches, 12);

  const exact = queryKnowledgeRegistryWithTotals(synthetic, { entry_ref: "knowledge:synthetic.entry-00@1" });
  assert.equal(exact.entries.length, 1);
  assert.equal(exact.total_matches, 1);

  assert.throws(
    () => queryKnowledgeRegistryWithTotals(synthetic, { topic: "synthetic_topic", registry_version: "other.v1" }),
    /registry version does not match/,
  );
});
