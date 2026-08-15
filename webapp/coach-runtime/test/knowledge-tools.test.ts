import assert from "node:assert/strict";
import test from "node:test";

import { createCoachKnowledgeTool, getCoachKnowledge } from "../src/knowledge-tools.ts";

const ALLOWED_CLAIM_LEVELS = new Set([
  "deterministic_rule",
  "research_supported",
  "community_practice",
  "community_consensus",
  "experimental",
]);

test("knowledge retrieval is bounded, versioned and traceable", async () => {
  const result = getCoachKnowledge({
    topic: "static_clicking",
    issue_signal: "reverse high",
    metric_refs: ["metric:reverse_ratio"],
    supported_use: "definition",
  });
  assert.equal(result.issue_signal, "reverse_ratio high");
  assert.ok(result.entries.length >= 1 && result.entries.length <= 8);
  for (const entry of result.entries) {
    assert.match(entry.entry_ref, /^knowledge:/);
    assert.ok(entry.entry_version >= 1);
    assert.ok(entry.source_refs.length > 0);
    assert.ok(Array.isArray(entry.sections));
    assert.ok(!("sources" in entry));
    assert.ok(entry.limitations.length > 0);
  }

  const tool = createCoachKnowledgeTool();
  const output = await tool.execute("knowledge-call", {
    topic: "tension_management",
    issue_signal: "tension hypothesis",
    metric_refs: ["metric:sparc"],
    supported_use: "candidate_hypothesis",
  });
  const parsed = JSON.parse(output.content[0]?.text ?? "null");
  assert.ok(parsed.entries.length >= 1);
  assert.equal(output.details.event.type, "knowledge");
  assert.equal(output.details.event.registry_version, parsed.registry_version);
  assert.deepEqual(output.details.event.entry_refs, parsed.entries.map((entry: { entry_ref: string }) => entry.entry_ref));
  assert.ok(output.details.event.section_refs.length > 0);
  assert.ok(output.details.event.claim_refs.every((ref: string) => ref.startsWith("claim:")));
  assert.ok(output.details.event.claim_levels.every((level: string) => ALLOWED_CLAIM_LEVELS.has(level)));
  assert.ok(output.details.event.max_claim_levels.every((level: string) => ALLOWED_CLAIM_LEVELS.has(level)));
  assert.ok(!JSON.stringify(output.details.event).includes("task-effect cue"));
});

test("unknown knowledge query returns an empty bounded result", () => {
  const result = getCoachKnowledge({ topic: "unknown-topic" });
  assert.deepEqual(result.entries, []);
});

test("baseline metric queries hit knowledge entries in every ref shape", () => {
  // baseline 档没有诊断 signal；实测指标名直接按 metric 检索即可命中：
  // bare（sparc）、family 前缀（static_clicking.corrective_count /
  // continuous_tracking.sparc）与 metric: 前缀都归一到同一批条目。
  const bare = getCoachKnowledge({ metric_refs: ["sparc"] });
  assert.ok(bare.entries.length >= 1);
  assert.ok(bare.entries.some((entry) => entry.metric_refs.includes("metric:sparc")));

  const familyPrefixed = getCoachKnowledge({
    metric_refs: ["static_clicking.corrective_count"],
  });
  assert.ok(familyPrefixed.entries.some(
    (entry) => entry.entry_ref === "knowledge:static.flicking-terminal-control@3",
  ));

  const trackingBySparc = getCoachKnowledge({
    metric_refs: ["continuous_tracking.sparc"],
  });
  assert.ok(trackingBySparc.entries.some(
    (entry) => entry.entry_ref === "knowledge:tracking.control-smoothness@3",
  ));

  const canonical = getCoachKnowledge({ metric_refs: ["metric:sparc"] });
  assert.deepEqual(
    canonical.entries.map((entry) => entry.entry_ref),
    bare.entries.map((entry) => entry.entry_ref),
  );
});

test("baseline deceleration metrics reach the tracking control-smoothness entry", () => {
  // v7 为 tracking.control-smoothness 补挂了基础档指标标签，使 baseline 追踪分析
  // 讲 decel/path 时能借到对应知识条目（此前只有视觉管线指标可查）。
  for (const metric of ["decel_frac", "path_efficiency", "reverse_ratio"]) {
    const result = getCoachKnowledge({ metric_refs: [metric] });
    assert.ok(
      result.entries.some((entry) => entry.entry_ref === "knowledge:tracking.control-smoothness@3"),
      `bare ${metric} should hit tracking.control-smoothness`,
    );
  }
  const familyPrefixed = getCoachKnowledge({ metric_refs: ["continuous_tracking.decel_frac"] });
  assert.ok(familyPrefixed.entries.some(
    (entry) => entry.entry_ref === "knowledge:tracking.control-smoothness@3",
  ));
});

test("knowledge event preserves every source returned with a projected entry", async () => {
  const query = {
    entry_ref: "knowledge:tracking.control-smoothness@3",
    supported_use: "candidate_experiment",
  };
  const result = getCoachKnowledge(query);
  const [entry] = result.entries;
  assert.ok(entry);
  const projectedSourceRefs = [...new Set(entry.sections.flatMap((section) => section.source_refs))];
  assert.ok(projectedSourceRefs.length > 8);
  assert.deepEqual(entry.source_refs, projectedSourceRefs);

  const output = await createCoachKnowledgeTool().execute("knowledge-provenance", query);
  assert.deepEqual(output.details.event.source_refs, projectedSourceRefs);
  assert.equal(output.details.event.source_levels.length, projectedSourceRefs.length);
});

test("exact versioned retrieval returns a bounded projection and source refs", () => {
  const result = getCoachKnowledge({
    registry_version: "2026-08-06.v5",
    entry_ref: "knowledge:hypothesis.input-latency-differential-intake@1",
    supported_use: "candidate_experiment",
  });
  assert.equal(result.registry_version, "2026-08-06.v5");
  assert.equal(result.entries.length, 1);
  const [entry] = result.entries;
  assert.equal(entry?.entry_ref, "knowledge:hypothesis.input-latency-differential-intake@1");
  assert.ok(entry?.sections.some((section) => section.section_ref.endsWith(".cue")));
  assert.ok(entry?.source_refs.length);
  assert.ok(!("sources" in (entry ?? {})));
  assert.ok(!("family_scope" in (entry ?? {})));
});

test("knowledge event accepts v5 differential-intake evidence", async () => {
  const tool = createCoachKnowledgeTool();
  const output = await tool.execute("knowledge-v5", {
    topic: "input_latency_differential",
    supported_use: "candidate_experiment",
  });
  const parsed = JSON.parse(output.content[0]?.text ?? "null");
  assert.equal(parsed.registry_version, "2026-08-16.v8");
  assert.equal(output.details.event.type, "knowledge");
  assert.equal(output.details.event.registry_version, "2026-08-16.v8");
  assert.ok(output.details.event.entry_refs.includes(
    "knowledge:hypothesis.input-latency-differential-intake@1",
  ));
});

test("knowledge results report total_matches and has_more", async () => {
  const narrow = getCoachKnowledge({ metric_refs: ["metric:sparc"] });
  assert.ok(narrow.entries.length >= 1);
  assert.equal(narrow.total_matches, narrow.entries.length);
  assert.equal(narrow.has_more, false);

  const tool = createCoachKnowledgeTool();
  const output = await tool.execute("knowledge-totals", { metric_refs: ["metric:sparc"] });
  const parsed = JSON.parse(output.content[0]?.text ?? "{}") as {
    total_matches: number; has_more: boolean; entries: unknown[];
  };
  assert.equal(parsed.total_matches, parsed.entries.length);
  assert.equal(parsed.has_more, false);

  const exact = getCoachKnowledge({ entry_ref: narrow.entries[0].entry_ref });
  assert.equal(exact.total_matches, 1);
  assert.equal(exact.has_more, false);
});
