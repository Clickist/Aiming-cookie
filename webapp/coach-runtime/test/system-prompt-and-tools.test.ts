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

test("default coach system prompt is product-owned and excludes coding-agent default", () => {
	const prompt = loadDefaultCoachSystemPrompt();
	assert.ok(prompt.includes("Aiming Cookie"));
	assert.ok(!prompt.includes(CODING_AGENT_DEFAULT_PROMPT_MARKER));
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
