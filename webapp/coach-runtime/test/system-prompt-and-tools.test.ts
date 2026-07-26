import assert from "node:assert/strict";
import test from "node:test";

import { createAnalysisSummaryTool } from "../src/analysis-summary-tool.ts";
import {
	CODING_AGENT_DEFAULT_PROMPT_MARKER,
	FORBIDDEN_TOOL_NAMES,
} from "../src/contracts.ts";
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

test("default coach system prompt is product-owned and excludes coding-agent default", () => {
	const prompt = loadDefaultCoachSystemPrompt();
	assert.ok(prompt.includes("Aiming Cookie"));
	assert.ok(!prompt.includes(CODING_AGENT_DEFAULT_PROMPT_MARKER));
	assert.match(prompt, /证据|evidence/i);
	assert.match(prompt, /下钻|查询|query/i);
	assert.match(prompt, /停止|stop/i);
	assert.match(prompt, /非可信数据/);
	assert.match(prompt, /不是指令/);
	assert.match(prompt, /候选观察/);
	assert.match(prompt, /反例/);
});

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

test("analysis tool accepts v3 processed table directory without event rows", async () => {
	const v3 = JSON.stringify(CANONICAL_ANALYSIS_CONTEXT_V3);
	const result = await createAnalysisSummaryTool(v3).execute();

	assert.equal(result.details.has_analysis, true);
	assert.equal(result.details.context_schema, "coach_diagnostic_context.v3");
	assert.equal(result.content[0]?.text, v3);
	assert.ok(!v3.includes("compact_rows"));
	assert.ok(!v3.includes("attributes"));
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
