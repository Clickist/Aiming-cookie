import assert from "node:assert/strict";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import { FORBIDDEN_TOOL_NAMES } from "../src/contracts.ts";
import { createFakeStreamFn } from "../src/fake-stream.ts";
import { loadDefaultCoachSystemPrompt } from "../src/load-system-prompt.ts";
import { loadPiAgent } from "../src/pi-source.ts";
import { buildCoachModel } from "../src/stream-openai-compatible.ts";

const CANONICAL_ANALYSIS_CONTEXT = {
	schema_version: "coach_diagnostic_context.v1",
	analysis_ref: {
		analysis_id: "analysis:42",
		analysis_result_version: "analysis_result.v2",
		analysis_type: "flicking",
		input_mode: "input_native",
	},
	diagnosis: {
		profile: {
			archetype_id: "decel_jitter",
			label: "safe label",
			confidence: 0.9,
		},
		issues: [],
		summary: {
			distance: {
				value: 12,
				unit: "raw_counts",
				classification: "deterministic",
			},
		},
		comparison: null,
		meta: { summary_type: "flicking", classification: "deterministic" },
	},
	evidence_summary: {
		availability: { raw_input: "available" },
		alignment: { status: "aligned" },
	},
	warnings: [],
};

const CANONICAL_ANALYSIS_CONTEXT_V2 = {
	schema_version: "coach_diagnostic_context.v2",
	analysis_ref: CANONICAL_ANALYSIS_CONTEXT.analysis_ref,
	scenario: {
		scenario_profile_ref: "scenario:static-clicking:1",
		analyzer_refs: ["analyzer:static-clicking:1"],
		support_status: "supported",
		limitations: [],
	},
	run_facts: {
		mode: "inline",
		field_registry_version: "source_field_registry.v1",
		facts: {
			schema_version: "canonical_run_facts.v1",
			analysis_ref: "analysis:42",
		},
		section_summaries: [],
		limitations: [],
	},
	diagnosis: CANONICAL_ANALYSIS_CONTEXT.diagnosis,
	evidence_summary: {
		...CANONICAL_ANALYSIS_CONTEXT.evidence_summary,
		confidence: 0.9,
		artifact_ref: "analysis:42:evidence:abcdef0123456789",
		evidence_revision: "sha256:abcdef0123456789",
		segment_refs: ["segment:analysis:42:typical:1"],
	},
	trends: [],
	training: { active_plan_ref: null, recent_retest_ref: null },
	limitations: [],
};

const CANONICAL_ANALYSIS_CONTEXT_V3 = {
	...CANONICAL_ANALYSIS_CONTEXT_V2,
	schema_version: "coach_diagnostic_context.v3",
	processed_events: {
		mode: "table_refs",
		tables: [{
			schema_version: "processed_event_table.v1",
			table_ref: "analysis:42:table:static_flick",
			analysis_ref: "analysis:42",
			analyzer_ref: "native_flicking.v1",
			family: "static_clicking",
			event_kind: "static_flick",
			row_count: 73,
			included_count: 73,
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
		}],
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

test("registered tools are read-only whitelist without bash/read/write/edit", async () => {
	const tool = createAnalysisSummaryTool("fixture summary");
	assert.equal(tool.name, "get_analysis_summary");
	assert.ok(!FORBIDDEN_TOOL_NAMES.has(tool.name));

	const { Agent } = (await loadPiAgent()) as {
		Agent: new (
			opts: Record<string, unknown>,
		) => { state: { tools: Array<{ name: string }> } };
	};
	const agent = new Agent({
		streamFn: createFakeStreamFn(),
		initialState: {
			systemPrompt: loadDefaultCoachSystemPrompt(),
			model: buildCoachModel({
				base_url: "https://api.deepseek.com",
				api_key_env: "DEEPSEEK_API_KEY",
				model_id: "deepseek-chat",
				context_window: 32768,
				max_tokens: 4096,
			}),
			tools: [tool],
			messages: [],
		},
	});

	const names = agent.state.tools.map((entry) => entry.name);
	for (const forbidden of FORBIDDEN_TOOL_NAMES) {
		assert.ok(!names.includes(forbidden));
	}
	assert.deepEqual(names, ["get_analysis_summary"]);
});

test("analysis tool returns the exact canonical diagnostic context JSON", async () => {
	const context = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT);
	const result = await createAnalysisSummaryTool(context).execute();
	assert.equal(result.content[0]?.text, context);
	assert.equal(result.details.context_schema, "coach_diagnostic_context.v1");
});

test("analysis tool preserves the existing prescription dosage field", async () => {
	const context = structuredClone(CANONICAL_ANALYSIS_CONTEXT);
	context.diagnosis.issues = [{
		signal: "relative velocity mismatch",
		claim_level: "deterministic_rule",
		prescriptions: [{
			cue: "Match speed before committing the click.",
			purpose: "Test one current training direction.",
			dosage: "Change only one motion variable per block.",
			source_level: "community_practice",
		}],
	}];
	const wire = JSON.stringify(context);

	const result = await createAnalysisSummaryTool(wire).execute();

	assert.equal(result.details.has_analysis, true);
	assert.equal(result.content[0]?.text, wire);
});

test("analysis tool accepts the frozen v2 context without upgrading historical v1", async () => {
	const v1 = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT);
	const v2 = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT_V2);
	const v1Result = await createAnalysisSummaryTool(v1).execute();
	const v2Result = await createAnalysisSummaryTool(v2).execute();

	assert.equal(v1Result.details.has_analysis, true);
	assert.equal(v1Result.details.context_schema, "coach_diagnostic_context.v1");
	assert.equal(v1Result.content[0]?.text, v1);
	assert.equal(v2Result.details.has_analysis, true);
	assert.equal(v2Result.details.context_schema, "coach_diagnostic_context.v2");
	assert.equal(v2Result.content[0]?.text, v2);
});

test("analysis tool accepts stable issue knowledge refs and rejects malformed pairs", async () => {
	const context = structuredClone(CANONICAL_ANALYSIS_CONTEXT_V2);
	context.diagnosis.issues = [{
		signal: "terminal control unstable",
		severity: "watch",
		observation_ref: "event.flick",
		knowledge_registry_version: "2026-07-29.v4",
		knowledge_entry_refs: ["knowledge:static.flicking-terminal-control@2"],
	}];

	const accepted = await createAnalysisSummaryTool(JSON.stringify(context)).execute();
	assert.equal(accepted.details.has_analysis, true);

	const malformed = structuredClone(context);
	malformed.diagnosis.issues[0]!.knowledge_entry_refs = [
		"knowledge:static.flicking-terminal-control@0",
	];
	const incomplete = structuredClone(context);
	delete incomplete.diagnosis.issues[0]!.knowledge_entry_refs;
	for (const value of [malformed, incomplete]) {
		const rejected = await createAnalysisSummaryTool(JSON.stringify(value)).execute();
		assert.equal(rejected.details.has_analysis, false);
	}
});

test("analysis tool accepts comparison contexts only with two safe projections", async () => {
	const comparisonProjection = structuredClone(CANONICAL_ANALYSIS_CONTEXT);
	comparisonProjection.analysis_ref = {
		...comparisonProjection.analysis_ref,
		analysis_id: "analysis:43",
	};
	const comparison = {
		schema_version: "coach_turn_context.v1",
		contexts: [{
			context_ref: "context:comparison-42-43",
			kind: "comparison",
			analysis_ref: "analysis:42",
			comparison_analysis_ref: "analysis:43",
			target_ref: null,
			time_range_ms: null,
			projection: CANONICAL_ANALYSIS_CONTEXT,
			comparison_projection: comparisonProjection,
		}],
	};

	const accepted = await createAnalysisSummaryTool(JSON.stringify(comparison)).execute();
	assert.equal(accepted.details.has_analysis, true);
	assert.equal(accepted.details.context_schema, "coach_turn_context.v1");

	const missingProjection = structuredClone(comparison) as {
		contexts: Array<Record<string, unknown>>;
	};
	delete missingProjection.contexts[0]?.comparison_projection;
	const poisoned = structuredClone(comparison) as {
		contexts: Array<Record<string, unknown>>;
	};
	const poisonedProjection = poisoned.contexts[0]?.comparison_projection;
	assert.ok(poisonedProjection && typeof poisonedProjection === "object");
	(poisonedProjection as Record<string, unknown>).raw_trace = [{ dx: 1 }];
	for (const rejectedValue of [missingProjection, poisoned]) {
		const rejected = await createAnalysisSummaryTool(JSON.stringify(rejectedValue)).execute();
		assert.equal(rejected.details.has_analysis, false);
		assert.equal(rejected.content[0]?.text, "当前没有可用的分析摘要。");
	}
});

test("analysis bundle discloses an index before an exact context projection", async () => {
	const comparisonProjection = structuredClone(CANONICAL_ANALYSIS_CONTEXT);
	comparisonProjection.analysis_ref = {
		...comparisonProjection.analysis_ref,
		analysis_id: "analysis:43",
	};
	const bundle = {
		schema_version: "coach_turn_context.v1",
		contexts: [{
			context_ref: "context:comparison-42-43",
			kind: "comparison",
			analysis_ref: "analysis:42",
			comparison_analysis_ref: "analysis:43",
			target_ref: null,
			time_range_ms: null,
			projection: CANONICAL_ANALYSIS_CONTEXT,
			comparison_projection: comparisonProjection,
		}],
	};
	const tool = createAnalysisSummaryTool(JSON.stringify(bundle));

	const indexResult = await tool.execute();
	const index = JSON.parse(indexResult.content[0]?.text ?? "null");
	assert.equal(index.schema_version, "coach_analysis_context_index.v1");
	assert.equal(index.contexts[0]?.context_ref, "context:comparison-42-43");
	assert.deepEqual(index.contexts[0]?.available_projections, ["primary", "comparison"]);
	assert.equal("projection" in index.contexts[0], false);
	assert.equal(indexResult.details.result_kind, "index");

	const primaryResult = await tool.execute("primary-call", {
		context_ref: "context:comparison-42-43",
		projection: "primary",
	});
	assert.deepEqual(JSON.parse(primaryResult.content[0]?.text ?? "null"), CANONICAL_ANALYSIS_CONTEXT);
	assert.equal(primaryResult.details.result_kind, "projection");
	assert.equal(primaryResult.details.context_ref, "context:comparison-42-43");

	const comparisonResult = await tool.execute("comparison-call", {
		context_ref: "context:comparison-42-43",
		projection: "comparison",
	});
	assert.deepEqual(JSON.parse(comparisonResult.content[0]?.text ?? "null"), comparisonProjection);

	const missingResult = await tool.execute("missing-call", {
		context_ref: "context:missing",
		projection: "primary",
	});
	assert.equal(missingResult.details.result_kind, "unavailable");
	assert.equal(missingResult.details.has_analysis, true);
});

test("analysis bundle above 64 KiB remains available through exact projection fetches", async () => {
	const contexts = Array.from({ length: 8 }, (_, offset) => {
		const analysisId = offset + 1;
		const projection = structuredClone(CANONICAL_ANALYSIS_CONTEXT_V2);
		projection.analysis_ref = {
			...projection.analysis_ref,
			analysis_id: `analysis:${analysisId}`,
		};
		projection.limitations = ["bounded context note ".repeat(550)];
		return {
			context_ref: `context:analysis-${analysisId}`,
			kind: "analysis",
			analysis_ref: `analysis:${analysisId}`,
			comparison_analysis_ref: null,
			target_ref: `analysis:${analysisId}`,
			time_range_ms: null,
			projection,
			comparison_projection: null,
		};
	});
	const wire = JSON.stringify({ schema_version: "coach_turn_context.v1", contexts });
	assert.ok(Buffer.byteLength(wire, "utf8") > 64 * 1024);
	assert.ok(Buffer.byteLength(wire, "utf8") <= 256 * 1024);

	const tool = createAnalysisSummaryTool(wire);
	const indexResult = await tool.execute();
	assert.equal(indexResult.details.has_analysis, true);
	assert.equal(indexResult.details.result_kind, "index");
	const index = JSON.parse(indexResult.content[0]?.text ?? "null");
	assert.equal(index.contexts.length, 8);

	const projectionResult = await tool.execute("projection-call", {
		context_ref: "context:analysis-8",
		projection: "primary",
	});
	assert.equal(projectionResult.details.result_kind, "projection");
	assert.equal(JSON.parse(projectionResult.content[0]?.text ?? "null").analysis_ref.analysis_id, "analysis:8");

	const constrainedTool = createAnalysisSummaryTool(wire, { maxResultBytes: 4 * 1024 });
	const constrainedIndex = await constrainedTool.execute();
	assert.equal(constrainedIndex.details.result_kind, "index");
	const constrainedProjection = await constrainedTool.execute("projection-call", {
		context_ref: "context:analysis-8",
		projection: "primary",
	});
	assert.equal(constrainedProjection.details.result_kind, "unavailable");
	assert.equal(constrainedProjection.details.reason, "context_budget_exceeded");
	assert.match(constrainedProjection.content[0]?.text ?? "", /上下文窗口不足/);
});

test("analysis tool accepts only a bounded de-identified benchmark summary", async () => {
	const summary = {
		schema_version: "coach_benchmark_summary.v1",
		catalog_ref: "benchmark-catalog:viscose-s2@1",
		catalog_version: "viscose-s2.2026-07-29.v1",
		observed_at: "2026-07-29T10:15:00Z",
		completion: {
			easier: { completed: 18, required: 39 },
			medium: { completed: 7, required: 39 },
		},
		provisional_ranks: { easier: 3, medium: 3 },
		scenarios: ["easier", "medium"].flatMap((difficulty) =>
			Array.from({ length: 39 }, (_, index) => ({
				difficulty,
				scenario_name: `Safe ${difficulty} Scenario ${index + 1}`,
				category: "flick_tech",
				subcategory: "stability",
				score: index < (difficulty === "easier" ? 18 : 7) ? 123.45 + index : 0,
				scenario_rank: index % 10,
			})),
		),
		review_candidates: [{
			difficulty: "easier",
			scenario_name: "Safe easier Scenario 1",
			category: "flick_tech",
			subcategory: "stability",
			score: 123.45,
			scenario_rank: 0,
		}],
	};

	const accepted = await createAnalysisSummaryTool(JSON.stringify(summary)).execute();
	assert.equal(accepted.details.has_analysis, true);
	assert.equal(accepted.details.context_schema, "coach_benchmark_summary.v1");
	const withFirstCourseLabel = (category: string, subcategory: string) => {
		const poisoned = structuredClone(summary);
		poisoned.scenarios[0]!.category = category;
		poisoned.scenarios[0]!.subcategory = subcategory;
		poisoned.review_candidates[0]!.category = category;
		poisoned.review_candidates[0]!.subcategory = subcategory;
		return poisoned;
	};
	const withFirstScenarioProviderPayload = () => {
		const poisoned = structuredClone(summary) as typeof summary & {
			scenarios: Array<Record<string, unknown>>;
		};
		poisoned.scenarios[0]!.provider_payload = { url: "https://example.invalid/private" };
		return poisoned;
	};

	for (const poisoned of [
		{ ...summary, steam_id: "00000000000000000" },
		{ ...summary, scenarios: [{ ...summary.scenarios[0], scenario_name: "https://example.invalid/private" }] },
		withFirstCourseLabel("untrusted_category", "stability"),
		withFirstCourseLabel("control_tracking", "precision"),
		withFirstScenarioProviderPayload(),
		{ ...summary, review_candidates: Array.from({ length: 9 }, () => summary.review_candidates[0]) },
		{ ...summary, scenarios: summary.scenarios.slice(0, 77) },
		{ ...summary, review_candidates: [{ ...summary.review_candidates[0], score: 999 }] },
	]) {
		const rejected = await createAnalysisSummaryTool(JSON.stringify(poisoned)).execute();
		assert.equal(rejected.details.has_analysis, false);
		assert.equal(rejected.content[0]?.text, "当前没有可用的分析摘要。");
	}
});

test("analysis tool accepts v3 processed table directory without event rows", async () => {
	const v3 = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT_V3);
	const result = await createAnalysisSummaryTool(v3).execute();

	assert.equal(result.details.has_analysis, true);
	assert.equal(result.details.context_schema, "coach_diagnostic_context.v3");
	assert.equal(result.content[0]?.text, v3);
	assert.ok(!v3.includes("compact_rows"));
	assert.ok(!v3.includes("attributes"));
});

test("system prompt routes score and training-plan requests to product commands", async () => {
	const prompt = loadDefaultCoachSystemPrompt();
	assert.match(prompt, /KovaaK scores.*kovaak_scores\.lookup/);
	assert.match(prompt, /generate a training-plan draft.*training_plan\.generate_draft/);
});

test("analysis tool accepts only string metric definition names and descriptions", async () => {
  const acceptedContext = structuredClone(CANONICAL_ANALYSIS_CONTEXT);
  acceptedContext.diagnosis.summary.distance.definition = {
    name: "Distance",
    description: "Measured displacement",
  };
  const accepted = await createAnalysisSummaryTool(JSON.stringify(acceptedContext)).execute();
  assert.equal(accepted.details.has_analysis, true);

  for (const definition of [
    { direction: "higher_better" },
    { name: 1 },
    { description: false },
    "Distance",
    null,
  ]) {
    const invalidContext = structuredClone(CANONICAL_ANALYSIS_CONTEXT);
    invalidContext.diagnosis.summary.distance.definition = definition;
    const rejected = await createAnalysisSummaryTool(JSON.stringify(invalidContext)).execute();
    assert.equal(rejected.details.has_analysis, false);
  }
});

test("analysis tool rejects malformed processed metric versions", async () => {
	const malformed = structuredClone(CANONICAL_ANALYSIS_CONTEXT_V3);
	malformed.processed_events.tables[0]!.field_catalog[0]!.metric_version =
		"native_flicking.version1";
	const result = await createAnalysisSummaryTool(
		JSON.stringify(malformed),
	).execute();

	assert.equal(result.details.has_analysis, false);
	assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。");
});

test("analysis tool accepts bounded v2 issue segment refs and rejects invalid ones", async () => {
	const primary = "analysis:42:segment:worst:1";
	const supporting = [
		"analysis:42:segment:typical:1",
		"analysis:42:segment:improved:1",
	];
	const context = {
		...CANONICAL_ANALYSIS_CONTEXT_V2,
		diagnosis: {
			...CANONICAL_ANALYSIS_CONTEXT_V2.diagnosis,
			issues: [{
				signal: "sparc low",
				severity: "info",
				claim_level: "experimental",
				primary_evidence_segment_ref: primary,
				supporting_evidence_segment_refs: supporting,
			}],
		},
		evidence_summary: {
			...CANONICAL_ANALYSIS_CONTEXT_V2.evidence_summary,
			segment_refs: [primary, ...supporting],
		},
	};

	const accepted = await createAnalysisSummaryTool(JSON.stringify(context)).execute();
	assert.equal(accepted.details.has_analysis, true);

	const tooMany = structuredClone(context);
	tooMany.diagnosis.issues[0]?.supporting_evidence_segment_refs.push(
		"analysis:42:segment:extra:1",
	);
	const invalid = structuredClone(context);
	invalid.diagnosis.issues[0]!.primary_evidence_segment_ref = "not-a-segment-ref";
	for (const value of [tooMany, invalid]) {
		const rejected = await createAnalysisSummaryTool(JSON.stringify(value)).execute();
		assert.equal(rejected.details.has_analysis, false);
	}
});

test("analysis tool never echoes discarded duplicate-key values", async () => {
	const canonical = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT);
	const poisoned = canonical.replace(
		'"label":"safe label"',
		String.raw`"label":"C:\\Users\\point\\private\\trace.csv","label":"safe label"`,
	);
	const result = await createAnalysisSummaryTool(poisoned).execute();

	assert.equal(result.details.has_analysis, false);
	assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。");
	assert.ok(!result.content[0]?.text.includes("private"));
});

test("analysis tool rejects invalid, wrong-schema and oversized input without echoing it", async () => {
	for (const input of [
		"not-json-secret",
		JSON.stringify({
			schema_version: "other.v1",
			secret: "wrong-schema-secret",
		}),
		JSON.stringify({
			schema_version: "coach_diagnostic_context.v1",
			value: "x".repeat(70 * 1024),
		}),
	]) {
		const result = await createAnalysisSummaryTool(input).execute();
		assert.equal(result.details.has_analysis, false);
		assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。");
		assert.ok(!result.content[0]?.text.includes("secret"));
	}
});

test("analysis tool fails closed on forbidden canonical-looking keys and values", async () => {
	const canonical = CANONICAL_ANALYSIS_CONTEXT;
	const forbiddenKeys: Array<[string, Record<string, unknown>]> = [
		["raw_trace", { ...canonical, raw_trace: "raw-trace-tool-sentinel" }],
		["dx", { ...canonical, diagnosis: { ...canonical.diagnosis, dx: [1, 2] } }],
		["dy", { ...canonical, diagnosis: { ...canonical.diagnosis, dy: [3, 4] } }],
		[
			"timestamps",
			{
				...canonical,
				diagnosis: { ...canonical.diagnosis, timestamps: [1_000, 1_001] },
			},
		],
		[
			"path",
			{
				...canonical,
				evidence_summary: {
					...canonical.evidence_summary,
					path: "path-tool-sentinel",
				},
			},
		],
		[
			"payload",
			{
				...canonical,
				diagnosis: { ...canonical.diagnosis, payload: "payload-tool-sentinel" },
			},
		],
		[
			"heuristic",
			{
				...canonical,
				diagnosis: {
					...canonical.diagnosis,
					unverified_heuristic: "heuristic-tool-sentinel",
				},
			},
		],
		["Benchmark", { ...canonical, Benchmark: "benchmark-tool-sentinel" }],
		["secret", { ...canonical, api_key: "sk-tool-secret-sentinel" }],
	];

	for (const [label, poisoned] of forbiddenKeys) {
		const result = await createAnalysisSummaryTool(
			JSON.stringify(poisoned),
		).execute();
		assert.equal(result.details.has_analysis, false, label);
		assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。", label);
		assert.ok(!result.content[0]?.text.includes("sentinel"), label);
	}

	for (const forbiddenValue of [
		String.raw`C:\Users\point\private\trace.csv`,
		String.raw`\\server\share\private\trace.csv`,
		"file:///C:/Users/point/private/trace.csv",
		"file:C:/private/trace.csv",
		"http://127.0.0.1/private",
		"https://example.invalid/private",
		"custom+scheme://private/resource",
		"api_key=sk-tool-secret-value-sentinel",
	]) {
		const poisoned = {
			...canonical,
			diagnosis: {
				...canonical.diagnosis,
				profile: { ...canonical.diagnosis.profile, label: forbiddenValue },
			},
		};
		const result = await createAnalysisSummaryTool(
			JSON.stringify(poisoned),
		).execute();
		assert.equal(result.details.has_analysis, false, forbiddenValue);
		assert.equal(
			result.content[0]?.text,
			"当前没有可用的分析摘要。",
			forbiddenValue,
		);
		assert.ok(
			!result.content[0]?.text.includes(forbiddenValue),
			forbiddenValue,
		);
	}
});

test("analysis tool rejects invalid refs but accepts v1 stable or null refs", async () => {
	const invalidRefs: Array<[string, Record<string, unknown>]> = [
		[
			"non-string analysis id",
			{ ...CANONICAL_ANALYSIS_CONTEXT.analysis_ref, analysis_id: 42 },
		],
		[
			"unstable analysis id",
			{
				...CANONICAL_ANALYSIS_CONTEXT.analysis_ref,
				analysis_id: "analysis-42",
			},
		],
		[
			"unknown result version",
			{
				...CANONICAL_ANALYSIS_CONTEXT.analysis_ref,
				analysis_result_version: "analysis_result.v99",
			},
		],
		[
			"invalid v2 mode",
			{ ...CANONICAL_ANALYSIS_CONTEXT.analysis_ref, input_mode: "unknown" },
		],
		[
			"invalid analysis type",
			{ ...CANONICAL_ANALYSIS_CONTEXT.analysis_ref, analysis_type: 7 },
		],
		[
			"path analysis type",
			{
				...CANONICAL_ANALYSIS_CONTEXT.analysis_ref,
				analysis_type: String.raw`C:\Users\point\private\trace.csv`,
			},
		],
		[
			"url analysis type",
			{
				...CANONICAL_ANALYSIS_CONTEXT.analysis_ref,
				analysis_type: "https://example.invalid/private",
			},
		],
	];

	for (const [label, analysisRef] of invalidRefs) {
		const result = await createAnalysisSummaryTool(
			JSON.stringify({
				...CANONICAL_ANALYSIS_CONTEXT,
				analysis_ref: analysisRef,
			}),
		).execute();
		assert.equal(result.details.has_analysis, false, label);
		assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。", label);
	}

	for (const analysisId of [null, "analysis:7"]) {
		const context = JSON.stringify({
			...CANONICAL_ANALYSIS_CONTEXT,
			analysis_ref: {
				analysis_id: analysisId,
				analysis_result_version: "analysis_result.v1",
				analysis_type: "flicking",
				input_mode: "unknown",
			},
			diagnosis: {
				...CANONICAL_ANALYSIS_CONTEXT.diagnosis,
				summary: { distance: { value: 12, unit: "raw_counts" } },
			},
		});
		const result = await createAnalysisSummaryTool(context).execute();
		assert.equal(result.details.has_analysis, true);
		assert.equal(result.content[0]?.text, context);
	}
});

test("analysis tool requires deterministic classification for v2 metrics and comparisons", async () => {
	const invalidV2Contexts = [
		{
			...CANONICAL_ANALYSIS_CONTEXT,
			diagnosis: {
				...CANONICAL_ANALYSIS_CONTEXT.diagnosis,
				summary: { distance: { value: 12, unit: "raw_counts" } },
			},
		},
		{
			...CANONICAL_ANALYSIS_CONTEXT,
			diagnosis: {
				...CANONICAL_ANALYSIS_CONTEXT.diagnosis,
				summary: {
					distance: { value: 12, unit: "raw_counts", classification: null },
				},
			},
		},
		{
			...CANONICAL_ANALYSIS_CONTEXT,
			diagnosis: {
				...CANONICAL_ANALYSIS_CONTEXT.diagnosis,
				comparison: { status: "comparable", delta: 1 },
			},
		},
	];

	for (const context of invalidV2Contexts) {
		const result = await createAnalysisSummaryTool(
			JSON.stringify(context),
		).execute();
		assert.equal(result.details.has_analysis, false);
		assert.equal(result.content[0]?.text, "当前没有可用的分析摘要。");
	}
});
