import { loadPiAi } from "./pi-source.ts";

// typebox lives in third_party/pi/node_modules; import its compile submodule
// via a relative path to the Pi checkout so resolution doesn't depend on a
// node_modules entry in the webapp tree.
const { Compile } = await import(
	"../../../third_party/pi/node_modules/typebox/build/compile/index.mjs"
);
const { Type } = (await loadPiAi()) as {
	Type: {
		Object(
			properties: Record<string, unknown>,
			options?: Record<string, unknown>,
		): unknown;
		Optional(schema: unknown): unknown;
		String(options?: Record<string, unknown>): unknown;
		Literal(value: string): unknown;
		Union(schemas: unknown[]): unknown;
		Null(): unknown;
		Boolean(): unknown;
		Number(options?: Record<string, unknown>): unknown;
		Array(schema: unknown, options?: Record<string, unknown>): unknown;
		Tuple(schemas: unknown[]): unknown;
		Record(key: unknown, value: unknown): unknown;
		Any(): unknown;
	};
};

type JsonRecord = Record<string, unknown>;

const METRIC_VERSION_RE = /^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$/;
const OBSERVATION_REF_RE = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
const KNOWLEDGE_REGISTRY_VERSION_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}\.v[1-9][0-9]*$/;
const KNOWLEDGE_ENTRY_REF_RE = /^knowledge:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+@[1-9][0-9]*$/;
const BENCHMARK_COURSE_LABELS = new Set([
	"click_timing:precision",
	"click_timing:reading",
	"click_timing:stability",
	"control_tracking:arm",
	"control_tracking:blending",
	"control_tracking:fingertip",
	"control_tracking:wrist",
	"flick_tech:micro",
	"flick_tech:post_flick",
	"flick_tech:speed",
	"flick_tech:stability",
	"reactive_tracking:control",
	"reactive_tracking:reading",
	"reactive_tracking:speed",
]);

function hasDuplicateJsonObjectKeys(source: string): boolean {
	let offset = 0;

	const skipWhitespace = () => {
		while (/\s/.test(source[offset] ?? "")) offset += 1;
	};

	const parseString = (): string => {
		const start = offset;
		offset += 1;
		while (offset < source.length) {
			const char = source[offset];
			if (char === "\\") {
				offset += 2;
				continue;
			}
			offset += 1;
			if (char === '"') return JSON.parse(source.slice(start, offset));
		}
		throw new SyntaxError("unterminated JSON string");
	};

	const parseScalar = () => {
		for (const literal of ["true", "false", "null"]) {
			if (source.startsWith(literal, offset)) {
				offset += literal.length;
				return;
			}
		}
		const number = source
			.slice(offset)
			.match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
		if (!number) throw new SyntaxError("invalid JSON scalar");
		offset += number[0].length;
	};

	const parseValue = (): boolean => {
		skipWhitespace();
		if (source[offset] === "{") return parseObject();
		if (source[offset] === "[") return parseArray();
		if (source[offset] === '"') {
			parseString();
			return false;
		}
		parseScalar();
		return false;
	};

	const parseObject = (): boolean => {
		offset += 1;
		skipWhitespace();
		if (source[offset] === "}") {
			offset += 1;
			return false;
		}

		const keys = new Set<string>();
		let duplicate = false;
		while (offset < source.length) {
			skipWhitespace();
			const key = parseString();
			if (keys.has(key)) duplicate = true;
			keys.add(key);
			skipWhitespace();
			if (source[offset] !== ":") throw new SyntaxError("missing JSON colon");
			offset += 1;
			const nestedDuplicate = parseValue();
			duplicate = duplicate || nestedDuplicate;
			skipWhitespace();
			if (source[offset] === "}") {
				offset += 1;
				return duplicate;
			}
			if (source[offset] !== ",") throw new SyntaxError("missing JSON comma");
			offset += 1;
		}
		throw new SyntaxError("unterminated JSON object");
	};

	const parseArray = (): boolean => {
		offset += 1;
		skipWhitespace();
		if (source[offset] === "]") {
			offset += 1;
			return false;
		}

		let duplicate = false;
		while (offset < source.length) {
			const nestedDuplicate = parseValue();
			duplicate = duplicate || nestedDuplicate;
			skipWhitespace();
			if (source[offset] === "]") {
				offset += 1;
				return duplicate;
			}
			if (source[offset] !== ",") throw new SyntaxError("missing JSON comma");
			offset += 1;
		}
		throw new SyntaxError("unterminated JSON array");
	};

	const duplicate = parseValue();
	skipWhitespace();
	if (offset !== source.length) throw new SyntaxError("trailing JSON content");
	return duplicate;
}

const PROCESSED_EVENTS_KEYS = new Set([
	"mode", "tables", "query_capabilities", "limitations",
]);
const PROCESSED_TABLE_KEYS = new Set([
	"schema_version", "table_ref", "analysis_ref", "analyzer_ref", "family",
	"event_kind", "row_count", "included_count", "excluded_count", "completeness",
	"field_catalog", "index_fields", "rows_ref", "limitations",
]);
const PROCESSED_FIELD_KEYS = new Set([
	"field_key", "role", "value_type", "unit", "metric_key", "metric_version",
	"expected_direction", "limitations",
]);
const PROCESSED_QUERY_CAPABILITIES = [
	"analysis.events.list",
	"analysis.events.get",
	"analysis.events.rank",
	"analysis.events.filter",
	"analysis.events.aggregate",
	"analysis.events.co_occurrence",
	"analysis.events.sequence",
	"analysis.evidence.compare",
];
const CLAIM_LEVELS = new Set([
	"measured",
	"deterministic_rule",
	"research_supported",
	"community_practice",
	"community_consensus",
	"experimental",
]);
type AnalysisResultVersion =
	| "analysis_result.v1"
	| "analysis_result.v2"
	| "unavailable";

function isRecord(value: unknown): value is JsonRecord {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: JsonRecord, allowed: Set<string>): boolean {
	return Object.keys(value).every((key) => allowed.has(key));
}

function hasExactKeys(value: JsonRecord, expected: Set<string>): boolean {
	return (
		Object.keys(value).length === expected.size && hasOnlyKeys(value, expected)
	);
}

function isForbiddenKey(key: string): boolean {
	const compact = key.toLowerCase().replace(/[^a-z0-9]/g, "");
	if (
		compact === "path" ||
		compact.endsWith("path") ||
		compact.endsWith("paths")
	)
		return true;
	if (
		[
			"apikey",
			"accesstoken",
			"refreshtoken",
			"clientsecret",
			"credential",
			"authorization",
			"password",
			"secret",
		].some((marker) => compact.includes(marker))
	)
		return true;
	if (compact.startsWith("rawinput") && compact !== "rawinput") return true;
	if (
		[
			"targetinference",
			"sensitivity",
			"heuristic",
			"benchmark",
			"external",
			"progress",
			"payload",
			"rawtrace",
			"tracepoints",
		].some((marker) => compact.includes(marker))
	)
		return true;
	return new Set([
		"dx",
		"dy",
		"button",
		"buttons",
		"points",
		"trace",
		"trajectory",
		"timestamp",
		"timestamps",
		"timestampsample",
		"timestampsamples",
		"sample",
		"samples",
		"rawsample",
		"rawsamples",
	]).has(compact);
}

function isUnsafeString(value: string): boolean {
	const candidate = value.trim();
	if (
		/^(?:[a-z]:[\\/]|[\\/]{2}|\/|~[\\/]|\.\.[\\/]|file:)/i.test(candidate) ||
		/^[a-z][a-z0-9+.-]*:\/\//i.test(candidate)
	)
		return true;
	return (
		/(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret)\s*[:=]/i.test(
			value,
		) ||
		/\bbearer\s+\S{8,}/i.test(value) ||
		/\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{8,}/i.test(value) ||
		/(?:raw[\s_-]*trace|target[\s_-]*inference|sensitivity[\s_-]*heuristic|external[\s_-]*progress|benchmark|payload)/i.test(
			value,
		)
	);
}

function isSafeScalar(value: unknown): boolean {
	return (
		value === null ||
		typeof value === "boolean" ||
		(typeof value === "number" && Number.isFinite(value)) ||
		(typeof value === "string" && !isUnsafeString(value))
	);
}

function isSafeStringArray(value: unknown): value is string[] {
	return (
		Array.isArray(value) &&
		value.every((item) => typeof item === "string" && !isUnsafeString(item))
	);
}

// --- TypeBox schemas (structural validation) ---
// ScalarSchema covers null|boolean|finite-number|string.  Compiled TypeBox
// Number already rejects NaN/Infinity, so the structural shape matches
// isSafeScalar — only the string-content safety check remains as a post-check.
const ScalarSchema = Type.Union([
	Type.Null(),
	Type.Boolean(),
	Type.Number(),
	Type.String(),
]);

const SafeStringArraySchema = Type.Array(Type.String());

// Leaf schemas — all use additionalProperties: false to replace hasOnlyKeys.
// Fields are Optional unless the original validator requires them explicitly.

const ProfileSchema = Type.Object(
	{
		archetype_id: Type.Optional(ScalarSchema),
		label: Type.Optional(ScalarSchema),
		confidence: Type.Optional(ScalarSchema),
		secondary_tags: Type.Optional(SafeStringArraySchema),
	},
	{ additionalProperties: false },
);

const VerificationSchema = Type.Object(
	{
		comparable_requirements: Type.Optional(SafeStringArraySchema),
		success_signals: Type.Optional(SafeStringArraySchema),
		insufficient_evidence_behavior: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const RootCauseSchema = Type.Object(
	{
		level: Type.Optional(ScalarSchema),
		text: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const PrescriptionSchema = Type.Object(
	{
		scenario: Type.Optional(ScalarSchema),
		reason: Type.Optional(ScalarSchema),
		cue: Type.Optional(ScalarSchema),
		purpose: Type.Optional(ScalarSchema),
		dosage: Type.Optional(ScalarSchema),
		retest_after: Type.Optional(ScalarSchema),
		stop_or_adjust_rule: Type.Optional(ScalarSchema),
		target_metrics: Type.Optional(SafeStringArraySchema),
		expected_direction: Type.Optional(SafeStringArraySchema),
		source_level: Type.Optional(
			Type.Union([
				Type.Literal("product_contract"),
				Type.Literal("academic_peer_reviewed"),
				Type.Literal("community_practice"),
				Type.Literal("community_consensus"),
				Type.Literal("personal_experience_unverified"),
				Type.Literal("experimental"),
			]),
		),
	},
	{ additionalProperties: false },
);

// kind is required (original validator explicitly checks typeof + Set.has).
const ProvenanceSchema = Type.Object(
	{
		kind: Type.Union([
			Type.Literal("measured"),
			Type.Literal("derived"),
			Type.Literal("fused"),
		]),
		sources: Type.Optional(SafeStringArraySchema),
	},
	{ additionalProperties: false },
);

const EvidenceRefSchema = Type.Object(
	{
		id: Type.Optional(ScalarSchema),
		source: Type.Optional(ScalarSchema),
		artifact_id: Type.Optional(ScalarSchema),
		alignment_status: Type.Optional(ScalarSchema),
		availability: Type.Optional(ScalarSchema),
		local_only: Type.Optional(ScalarSchema),
		metric_keys: Type.Optional(SafeStringArraySchema),
		challenge_time_range_ms: Type.Optional(
			Type.Array(ScalarSchema, { minItems: 2, maxItems: 2 }),
		),
	},
	{ additionalProperties: false },
);

// Compiled leaf validators.
const profileValidator = Compile(ProfileSchema);
const verificationValidator = Compile(VerificationSchema);
const rootCauseValidator = Compile(RootCauseSchema);
const prescriptionValidator = Compile(PrescriptionSchema);
const provenanceValidator = Compile(ProvenanceSchema);
const evidenceRefValidator = Compile(EvidenceRefSchema);

// --- Composite schemas ---

// Flat-record schemas (replace validateFlatRecord + COMPARISON/META/ALIGNMENT keys).
const ComparisonSchema = Type.Object(
	{
		status: Type.Optional(ScalarSchema),
		reason: Type.Optional(ScalarSchema),
		comparable: Type.Optional(ScalarSchema),
		metric: Type.Optional(ScalarSchema),
		delta: Type.Optional(ScalarSchema),
		unit: Type.Optional(ScalarSchema),
		classification: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const MetaSchema = Type.Object(
	{
		summary_type: Type.Optional(ScalarSchema),
		analysis_context: Type.Optional(ScalarSchema),
		metric_version: Type.Optional(ScalarSchema),
		scenario_identity_version: Type.Optional(ScalarSchema),
		calibration_compatibility: Type.Optional(ScalarSchema),
		minimum_evidence_quality: Type.Optional(ScalarSchema),
		classification: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const AlignmentSchema = Type.Object(
	{
		status: Type.Optional(ScalarSchema),
		coverage_ratio: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const MetricSchema = Type.Object(
	{
		value: Type.Optional(ScalarSchema),
		unit: Type.Optional(ScalarSchema),
		provenance: Type.Optional(ProvenanceSchema),
		metric_version: Type.Optional(ScalarSchema),
		classification: Type.Optional(Type.Literal("deterministic")),
		min: Type.Optional(ScalarSchema),
		max: Type.Optional(ScalarSchema),
		mean: Type.Optional(ScalarSchema),
		median: Type.Optional(ScalarSchema),
		med: Type.Optional(ScalarSchema),
		p25: Type.Optional(ScalarSchema),
		p50: Type.Optional(ScalarSchema),
		p75: Type.Optional(ScalarSchema),
		p90: Type.Optional(ScalarSchema),
		std: Type.Optional(ScalarSchema),
		iqr: Type.Optional(ScalarSchema),
		count: Type.Optional(ScalarSchema),
		n: Type.Optional(ScalarSchema),
		score: Type.Optional(ScalarSchema),
		status: Type.Optional(ScalarSchema),
		key: Type.Optional(ScalarSchema),
		availability: Type.Optional(ScalarSchema),
		sample_count: Type.Optional(ScalarSchema),
		coverage: Type.Optional(ScalarSchema),
		limitations: Type.Optional(SafeStringArraySchema),
		outlier_method: Type.Optional(ScalarSchema),
		outlier_refs: Type.Optional(SafeStringArraySchema),
		sample_refs: Type.Optional(SafeStringArraySchema),
		definition: Type.Optional(Type.Object(
			{
				name: Type.Optional(Type.String()),
				description: Type.Optional(Type.String()),
			},
			{ additionalProperties: false },
		)),
	},
	{ additionalProperties: false },
);

const IssueSchema = Type.Object(
	{
		signal: Type.Optional(ScalarSchema),
		severity: Type.Optional(ScalarSchema),
		priority: Type.Optional(ScalarSchema),
		priority_reason: Type.Optional(ScalarSchema),
		plain_language_meaning: Type.Optional(ScalarSchema),
		expected_result: Type.Optional(ScalarSchema),
		claim_level: Type.Optional(
			Type.Union([
				Type.Literal("measured"),
				Type.Literal("deterministic_rule"),
				Type.Literal("research_supported"),
				Type.Literal("community_practice"),
				Type.Literal("community_consensus"),
				Type.Literal("experimental"),
			]),
		),
		observation_ref: Type.Optional(Type.String()),
		knowledge_registry_version: Type.Optional(Type.String()),
		knowledge_entry_refs: Type.Optional(Type.Array(Type.String())),
		metric_refs: Type.Optional(SafeStringArraySchema),
		event_refs: Type.Optional(SafeStringArraySchema),
		limitations: Type.Optional(SafeStringArraySchema),
		primary_evidence_segment_ref: Type.Optional(
			Type.Union([Type.Null(), Type.String()]),
		),
		supporting_evidence_segment_refs: Type.Optional(SafeStringArraySchema),
		verification: Type.Optional(VerificationSchema),
		root_causes: Type.Optional(Type.Array(RootCauseSchema)),
		prescriptions: Type.Optional(Type.Array(PrescriptionSchema)),
	},
	{ additionalProperties: false },
);

const DiagnosisSchema = Type.Object(
	{
		profile: ProfileSchema,
		issues: Type.Array(IssueSchema),
		summary: Type.Record(Type.String(), Type.Any()),
		comparison: Type.Union([Type.Null(), ComparisonSchema]),
		meta: MetaSchema,
	},
	{ additionalProperties: false },
);

const WarningSchema = Type.Object(
	{
		code: Type.String(),
		domain: Type.Optional(ScalarSchema),
		retryable: Type.Optional(ScalarSchema),
		user_message_key: Type.Optional(ScalarSchema),
		evidence_ref: Type.Optional(EvidenceRefSchema),
	},
	{ additionalProperties: false },
);

const EvidenceSummarySchema = Type.Object(
	{
		availability: Type.Record(Type.String(), ScalarSchema),
		alignment: AlignmentSchema,
		coverage: Type.Optional(ScalarSchema),
	},
	{ additionalProperties: false },
);

const V2ScenarioSchema = Type.Object(
	{
		scenario_profile_ref: Type.Union([Type.Null(), Type.String()]),
		analyzer_refs: SafeStringArraySchema,
		support_status: Type.Union([
			Type.Literal("supported"),
			Type.Literal("partial"),
			Type.Literal("outcome_only"),
			Type.Literal("unsupported"),
			Type.Literal("unavailable"),
		]),
		limitations: SafeStringArraySchema,
		display_name: Type.Optional(Type.String()),
		aim_family: Type.Optional(Type.String()),
	},
	{ additionalProperties: false },
);

const V2EvidenceSummarySchema = Type.Object(
	{
		availability: Type.Record(Type.String(), ScalarSchema),
		alignment: AlignmentSchema,
		coverage: Type.Optional(ScalarSchema),
		confidence: Type.Optional(ScalarSchema),
		artifact_ref: Type.Optional(ScalarSchema),
		evidence_revision: Type.Optional(ScalarSchema),
		segment_refs: Type.Optional(SafeStringArraySchema),
	},
	{ additionalProperties: false },
);

// Compiled composite validators.
const metricValidator = Compile(MetricSchema);
const issueValidator = Compile(IssueSchema);
const diagnosisValidator = Compile(DiagnosisSchema);
const warningValidator = Compile(WarningSchema);
const evidenceSummaryValidator = Compile(EvidenceSummarySchema);
const v2ScenarioValidator = Compile(V2ScenarioSchema);
const v2EvidenceSummaryValidator = Compile(V2EvidenceSummarySchema);

// --- Context-level schemas ---

const AnalysisRefSchema = Type.Object(
	{
		analysis_id: Type.Any(),
		analysis_result_version: Type.Any(),
		analysis_type: Type.Any(),
		input_mode: Type.Any(),
	},
	{ additionalProperties: false },
);

const TrainingSchema = Type.Object(
	{
		active_plan_ref: Type.Union([Type.Null(), Type.String()]),
		recent_retest_ref: Type.Union([Type.Null(), Type.String()]),
	},
	{ additionalProperties: false },
);

// V2 context: exact key set + schema_version literal.
// Nested fields are validated by post-check functions, so they use Type.Any()
// here — the schema only enforces key presence and rejects extras.
const V2ContextSchema = Type.Object(
	{
		schema_version: Type.Literal("coach_diagnostic_context.v2"),
		analysis_ref: Type.Any(),
		scenario: Type.Any(),
		run_facts: Type.Any(),
		diagnosis: Type.Any(),
		evidence_summary: Type.Any(),
		trends: Type.Array(Type.Any(), { maxItems: 4 }),
		training: TrainingSchema,
		limitations: Type.Array(Type.String()),
	},
	{ additionalProperties: false },
);

const V3ContextSchema = Type.Object(
	{
		schema_version: Type.Literal("coach_diagnostic_context.v3"),
		analysis_ref: Type.Any(),
		scenario: Type.Any(),
		run_facts: Type.Any(),
		diagnosis: Type.Any(),
		evidence_summary: Type.Any(),
		trends: Type.Array(Type.Any(), { maxItems: 4 }),
		training: TrainingSchema,
		limitations: Type.Array(Type.String()),
		processed_events: Type.Any(),
	},
	{ additionalProperties: false },
);

const V1ContextSchema = Type.Object(
	{
		schema_version: Type.Literal("coach_diagnostic_context.v1"),
		analysis_ref: Type.Any(),
		diagnosis: Type.Any(),
		evidence_summary: Type.Any(),
		warnings: Type.Array(Type.Any()),
	},
	{ additionalProperties: false },
);

const BenchmarkSummarySchema = Type.Object(
	{
		schema_version: Type.Any(),
		catalog_ref: Type.Any(),
		catalog_version: Type.Any(),
		observed_at: Type.Any(),
		completion: Type.Any(),
		provisional_ranks: Type.Any(),
		scenarios: Type.Array(Type.Any()),
		review_candidates: Type.Array(Type.Any()),
	},
	{ additionalProperties: false },
);

const BenchmarkScenarioSchema = Type.Object(
	{
		difficulty: Type.Union([
			Type.Literal("easier"),
			Type.Literal("medium"),
		]),
		scenario_name: Type.String(),
		category: Type.String(),
		subcategory: Type.String(),
		score: Type.Number(),
		scenario_rank: Type.Number(),
	},
	{ additionalProperties: false },
);

const ContextBundleSchema = Type.Object(
	{
		schema_version: Type.Literal("coach_turn_context.v1"),
		contexts: Type.Array(Type.Any()),
		benchmark_summary: Type.Optional(Type.Any()),
	},
	{ additionalProperties: false },
);

const ContextBundleItemSchema = Type.Object(
	{
		context_ref: Type.String(),
		kind: Type.Union([
			Type.Literal("analysis"),
			Type.Literal("issue"),
			Type.Literal("time_range"),
			Type.Literal("metric"),
			Type.Literal("evidence_segment"),
			Type.Literal("comparison"),
		]),
		analysis_ref: Type.String(),
		comparison_analysis_ref: Type.Union([Type.Null(), Type.String()]),
		target_ref: Type.Union([Type.Null(), Type.String()]),
		time_range_ms: Type.Union([
			Type.Null(),
			Type.Tuple([Type.Number(), Type.Number()]),
		]),
		projection: Type.Any(),
		comparison_projection: Type.Any(),
	},
	{ additionalProperties: false },
);

// Compiled context-level validators.
const analysisRefValidator = Compile(AnalysisRefSchema);
const v2ContextValidator = Compile(V2ContextSchema);
const v3ContextValidator = Compile(V3ContextSchema);
const v1ContextValidator = Compile(V1ContextSchema);
const benchmarkSummaryValidator = Compile(BenchmarkSummarySchema);
const benchmarkScenarioValidator = Compile(BenchmarkScenarioSchema);
const contextBundleValidator = Compile(ContextBundleSchema);
const contextBundleItemValidator = Compile(ContextBundleItemSchema);

function validateProfile(value: unknown): boolean {
	if (!profileValidator.Check(value)) return false;
	return Object.entries(value as JsonRecord).every(([key, item]) =>
		key === "secondary_tags" ? isSafeStringArray(item) : isSafeScalar(item),
	);
}

function validateVerification(value: unknown): boolean {
	if (!verificationValidator.Check(value)) return false;
	return Object.entries(value as JsonRecord).every(([key, item]) =>
		key === "insufficient_evidence_behavior"
			? isSafeScalar(item)
			: isSafeStringArray(item),
	);
}

function validateRootCause(value: unknown): boolean {
	if (!rootCauseValidator.Check(value)) return false;
	return Object.values(value as JsonRecord).every(isSafeScalar);
}

function validatePrescription(value: unknown): boolean {
	if (!prescriptionValidator.Check(value)) return false;
	return Object.entries(value as JsonRecord).every(([key, item]) => {
		if (key === "target_metrics" || key === "expected_direction")
			return isSafeStringArray(item);
		// source_level already validated by schema (literal union); its values
		// are inherently safe scalars, so fall through to isSafeScalar.
		return isSafeScalar(item);
	});
}

function validateIssue(value: unknown): boolean {
	if (!issueValidator.Check(value)) return false;
	const record = value as JsonRecord;
	const hasKnowledgeRegistryVersion = "knowledge_registry_version" in record;
	const hasKnowledgeEntryRefs = "knowledge_entry_refs" in record;
	if (hasKnowledgeRegistryVersion !== hasKnowledgeEntryRefs) return false;
	return Object.entries(record).every(([key, item]) => {
		if (key === "observation_ref") {
			return (
				typeof item === "string" &&
				item.length <= 160 &&
				OBSERVATION_REF_RE.test(item)
			);
		}
		if (key === "knowledge_registry_version") {
			return (
				typeof item === "string" &&
				KNOWLEDGE_REGISTRY_VERSION_RE.test(item)
			);
		}
		if (key === "knowledge_entry_refs") {
			return (
				Array.isArray(item) &&
				item.length > 0 &&
				item.length <= 8 &&
				item.every(
					(ref) =>
						typeof ref === "string" &&
						ref.length <= 180 &&
						KNOWLEDGE_ENTRY_REF_RE.test(ref),
				) &&
				new Set(item).size === item.length
			);
		}
		if (
			key === "metric_refs" ||
			key === "event_refs" ||
			key === "limitations" ||
			key === "supporting_evidence_segment_refs"
		) {
			return isSafeStringArray(item) &&
				(key !== "supporting_evidence_segment_refs" || (
					item.length <= 2 &&
					item.every((ref) => ref.startsWith("analysis:") && ref.includes(":segment:"))
				));
		}
		if (key === "primary_evidence_segment_ref") {
			return item === null || (
				typeof item === "string" &&
				item.startsWith("analysis:") &&
				item.includes(":segment:") &&
				isSafeScalar(item)
			);
		}
		if (key === "verification") return validateVerification(item);
		if (key === "root_causes")
			return Array.isArray(item) && item.every(validateRootCause);
		if (key === "prescriptions")
			return Array.isArray(item) && item.every(validatePrescription);
		if (key === "claim_level")
			return typeof item === "string" && CLAIM_LEVELS.has(item);
		return isSafeScalar(item);
	});
}

function validateProvenance(value: unknown): boolean {
	if (!provenanceValidator.Check(value)) return false;
	return value.sources === undefined || isSafeStringArray(value.sources);
}

function validateMetric(
	value: unknown,
	requireDeterministic: boolean,
): boolean {
	if (!metricValidator.Check(value)) return false;
	const record = value as JsonRecord;
	if (Object.keys(record).length === 0) return false;
	if (requireDeterministic && record.classification !== "deterministic") {
		return false;
	}
	return Object.entries(record).every(([key, item]) => {
		if (
			key === "limitations" ||
			key === "outlier_refs" ||
			key === "sample_refs"
		) {
			return isSafeStringArray(item);
		}
		if (key === "provenance") return validateProvenance(item);
		if (key === "definition") {
			if (item === undefined) return true;
			if (!isRecord(item)) return false;
			return Object.entries(item).every(
				([k, v]) =>
					(k === "name" || k === "description") && typeof v === "string",
			);
		}
		// classification already constrained to "deterministic" by schema.
		return isSafeScalar(item);
	});
}

function validateSummary(
	value: unknown,
	requireDeterministic: boolean,
): boolean {
	return (
		isRecord(value) &&
		Object.entries(value).every(
			([key, item]) =>
				!isForbiddenKey(key) && validateMetric(item, requireDeterministic),
		)
	);
}

function validateDiagnosis(
	value: unknown,
	resultVersion: AnalysisResultVersion,
): boolean {
	if (!diagnosisValidator.Check(value)) return false;
	const record = value as JsonRecord;
	if (!validateProfile(record.profile)) return false;
	if (!Array.isArray(record.issues) || !record.issues.every(validateIssue))
		return false;
	if (!validateSummary(record.summary, resultVersion === "analysis_result.v2"))
		return false;
	const comparison = record.comparison;
	if (comparison !== null) {
		const comp = comparison as JsonRecord;
		if (comp.classification !== "deterministic" ||
			!Object.values(comp).every(isSafeScalar))
			return false;
	}
	const meta = record.meta as JsonRecord;
	if (
		meta.classification !== undefined &&
		meta.classification !== "deterministic"
	)
		return false;
	if (!Object.values(meta).every(isSafeScalar)) return false;
	return true;
}

function validateEvidenceRef(value: unknown): boolean {
	if (!evidenceRefValidator.Check(value)) return false;
	return Object.entries(value as JsonRecord).every(([key, item]) => {
		if (key === "metric_keys") return isSafeStringArray(item);
		if (key === "challenge_time_range_ms") {
			return (
				Array.isArray(item) && item.length === 2 && item.every(isSafeScalar)
			);
		}
		return isSafeScalar(item);
	});
}

function validateWarning(value: unknown): boolean {
	if (!warningValidator.Check(value)) return false;
	return Object.entries(value as JsonRecord).every(([key, item]) =>
		key === "evidence_ref" ? validateEvidenceRef(item) : isSafeScalar(item),
	);
}

function validateEvidenceSummary(value: unknown): boolean {
	if (!evidenceSummaryValidator.Check(value)) return false;
	const record = value as JsonRecord;
	return Object.entries(record).every(([key, item]) => {
		if (key === "availability") {
			return Object.entries(item as JsonRecord).every(
				([source, availability]) =>
					!isForbiddenKey(source) && isSafeScalar(availability),
			);
		}
		if (key === "alignment")
			return Object.values(item as JsonRecord).every(isSafeScalar);
		return isSafeScalar(item);
	});
}

function validateBoundedFactsValue(value: unknown, depth = 0): boolean {
	if (depth > 8) return false;
	if (isSafeScalar(value)) return true;
	if (Array.isArray(value)) {
		return value.length <= 512 && value.every((item) => validateBoundedFactsValue(item, depth + 1));
	}
	if (!isRecord(value) || Object.keys(value).length > 128) return false;
	return Object.entries(value).every(
		([key, item]) => !isForbiddenKey(key) && validateBoundedFactsValue(item, depth + 1),
	);
}

function validateV2RunFacts(value: unknown): boolean {
	if (!isRecord(value) || typeof value.mode !== "string") return false;
	const limitations = value.limitations;
	if (!isSafeStringArray(limitations) || limitations.length > 8) return false;
	if (value.mode === "unavailable") {
		return hasOnlyKeys(value, new Set(["mode", "field_registry_version", "limitations"]));
	}
	if (
		"field_registry_version" in value &&
		value.field_registry_version !== "source_field_registry.v1"
	) return false;
	if (value.mode === "inline") {
		const inlineKeys = new Set(["mode", "field_registry_version", "facts", "section_summaries", "limitations"]);
		if (!hasOnlyKeys(value, inlineKeys) || !("facts" in value) || !("limitations" in value)) return false;
		if ("section_summaries" in value && (!Array.isArray(value.section_summaries) || value.section_summaries.length !== 0)) return false;
		return (
			isRecord(value.facts) &&
			validateBoundedFactsValue(value.facts) &&
			Buffer.byteLength(JSON.stringify(value.facts), "utf8") <= 8 * 1024
		);
	}
	if (value.mode !== "section_refs") return false;
	if (!hasOnlyKeys(value, new Set(["mode", "field_registry_version", "section_summaries", "limitations"]))) return false;
	if (!("section_summaries" in value) || !("limitations" in value)) return false;
	const summaries = value.section_summaries;
	if (!Array.isArray(summaries) || summaries.length > 7) return false;
	return summaries.every((item) => {
		if (!isRecord(item) || !hasExactKeys(item, new Set([
			"section_key",
			"section_ref",
			"completeness",
			"present_field_count",
			"source_absent_field_count",
			"omitted_known_field_count",
		]))) return false;
		return (
			typeof item.section_key === "string" && !isUnsafeString(item.section_key) &&
			typeof item.section_ref === "string" && !isUnsafeString(item.section_ref) &&
			(item.completeness === "complete_allowlisted" || item.completeness === "partial") &&
			["present_field_count", "source_absent_field_count", "omitted_known_field_count"].every((key) =>
				typeof item[key] === "number" && Number.isInteger(item[key]) && item[key] >= 0,
			)
		);
	});
}

function validateV2Scenario(value: unknown): boolean {
	if (!v2ScenarioValidator.Check(value)) return false;
	const record = value as JsonRecord;
	return (
		(record.scenario_profile_ref === null || !isUnsafeString(record.scenario_profile_ref as string)) &&
		isSafeStringArray(record.analyzer_refs) && record.analyzer_refs.length <= 16 &&
		isSafeStringArray(record.limitations) && record.limitations.length <= 8
	);
}

function validateV2EvidenceSummary(value: unknown): boolean {
	if (!v2EvidenceSummaryValidator.Check(value)) return false;
	const record = value as JsonRecord;
	if (!Object.entries(record.availability as JsonRecord).every(([key, item]) => !isForbiddenKey(key) && isSafeScalar(item))) return false;
	if (!Object.values(record.alignment as JsonRecord).every(isSafeScalar)) return false;
	for (const key of ["coverage", "confidence", "artifact_ref", "evidence_revision"]) {
		if (key in record && !isSafeScalar(record[key])) return false;
	}
	return !("segment_refs" in record) || (isSafeStringArray(record.segment_refs) && record.segment_refs.length <= 24);
}

function validateV2Context(value: JsonRecord): boolean {
	if (!v2ContextValidator.Check(value)) return false;
	const resultVersion = validatedAnalysisRefVersion(value.analysis_ref);
	if (resultVersion !== "analysis_result.v2") return false;
	if (!validateV2Scenario(value.scenario) || !validateV2RunFacts(value.run_facts)) return false;
	if (!validateDiagnosis(value.diagnosis, resultVersion) || !validateV2EvidenceSummary(value.evidence_summary)) return false;
	if (!(value.trends as unknown[]).every(validateBoundedFactsValue)) return false;
	if (!Object.values(value.training as JsonRecord).every((item) => item === null || !isUnsafeString(item as string))) return false;
	if (!isSafeStringArray(value.limitations) || value.limitations.length > 8) return false;
	return Buffer.byteLength(JSON.stringify(value), "utf8") <= 32 * 1024;
}

function validateProcessedEventTable(value: unknown, analysisId: string): boolean {
	if (!isRecord(value) || !hasExactKeys(value, PROCESSED_TABLE_KEYS)) return false;
	if (value.schema_version !== "processed_event_table.v1" || value.analysis_ref !== analysisId) return false;
	if (
		typeof value.table_ref !== "string" || !value.table_ref.startsWith(`${analysisId}:table:`) ||
		value.rows_ref !== value.table_ref || isUnsafeString(value.table_ref)
	) return false;
	for (const key of ["analyzer_ref", "family", "event_kind"]) {
		if (typeof value[key] !== "string" || isUnsafeString(value[key])) return false;
	}
	for (const key of ["row_count", "included_count", "excluded_count"]) {
		if (typeof value[key] !== "number" || !Number.isInteger(value[key]) || value[key] < 0) return false;
	}
	if (value.row_count !== value.included_count) return false;
	if (!new Set(["complete", "partial", "unavailable"]).has(String(value.completeness))) return false;
	if (value.completeness === "complete" && value.excluded_count !== 0) return false;
	if (!Array.isArray(value.field_catalog) || value.field_catalog.length < 1 || value.field_catalog.length > 64) return false;
	const fieldKeys = new Set<string>();
	for (const field of value.field_catalog) {
		if (!isRecord(field) || !hasExactKeys(field, PROCESSED_FIELD_KEYS)) return false;
		if (typeof field.field_key !== "string" || isUnsafeString(field.field_key) || fieldKeys.has(field.field_key)) return false;
		fieldKeys.add(field.field_key);
		if (!new Set(["identity", "timing", "condition", "metric", "outcome", "quality"]).has(String(field.role))) return false;
		if (!new Set(["number", "string", "ref", "string_list", "boolean"]).has(String(field.value_type))) return false;
		for (const key of ["unit", "metric_key", "metric_version"]) {
			if (field[key] !== null && (typeof field[key] !== "string" || isUnsafeString(field[key]))) return false;
		}
		if (field.metric_version !== null && !METRIC_VERSION_RE.test(field.metric_version as string)) return false;
		if (!new Set(["lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only"]).has(String(field.expected_direction))) return false;
		if (!isSafeStringArray(field.limitations) || field.limitations.length > 8) return false;
	}
	if (!isSafeStringArray(value.index_fields) || value.index_fields.length > 8 || !value.index_fields.every((field) => fieldKeys.has(field))) return false;
	return isSafeStringArray(value.limitations) && value.limitations.length <= 8;
}

function validateV3Context(value: JsonRecord): boolean {
	if (!v3ContextValidator.Check(value)) return false;
	const { processed_events: processedEvents, ...v2Fields } = value;
	if (!validateV2Context({ ...v2Fields, schema_version: "coach_diagnostic_context.v2" })) return false;
	if (!isRecord(processedEvents) || !hasExactKeys(processedEvents, PROCESSED_EVENTS_KEYS)) return false;
	if (processedEvents.mode !== "table_refs") return false;
	if (!Array.isArray(processedEvents.tables) || processedEvents.tables.length < 1 || processedEvents.tables.length > 8) return false;
	const analysisRef = value.analysis_ref;
	if (!isRecord(analysisRef) || typeof analysisRef.analysis_id !== "string") return false;
	if (!processedEvents.tables.every((table) => validateProcessedEventTable(table, analysisRef.analysis_id as string))) return false;
	if (!isSafeStringArray(processedEvents.query_capabilities)) return false;
	if (JSON.stringify(processedEvents.query_capabilities) !== JSON.stringify(PROCESSED_QUERY_CAPABILITIES)) return false;
	if (!isSafeStringArray(processedEvents.limitations) || processedEvents.limitations.length > 8) return false;
	return Buffer.byteLength(JSON.stringify(value), "utf8") <= 32 * 1024;
}

function validatedAnalysisRefVersion(
	value: unknown,
): AnalysisResultVersion | undefined {
	if (!analysisRefValidator.Check(value)) {
		return undefined;
	}
	const record = value as JsonRecord;
	const version = record.analysis_result_version;
	const analysisId = record.analysis_id;
	const analysisType = record.analysis_type;
	const inputMode = record.input_mode;
	const stableId =
		typeof analysisId === "string" &&
		/^analysis:[A-Za-z0-9][A-Za-z0-9._-]*$/.test(analysisId);
	const safeAnalysisType =
		typeof analysisType === "string" &&
		analysisType.trim().length > 0 &&
		!isUnsafeString(analysisType);

	if (version === "analysis_result.v2") {
		if (!stableId) return undefined;
		if (!safeAnalysisType) return undefined;
		if (
			!new Set(["input_native", "multimodal", "video_fallback"]).has(
				String(inputMode),
			)
		) {
			return undefined;
		}
		return version;
	}
	if (version === "analysis_result.v1") {
		if (analysisId !== null && !stableId) return undefined;
		if (analysisType !== null && !safeAnalysisType) return undefined;
		return inputMode === "unknown" ? version : undefined;
	}
	if (version === "unavailable") {
		return analysisId === null && analysisType === null && inputMode === null
			? version
			: undefined;
	}
	return undefined;
}

function isCanonicalDiagnosticContext(value: unknown): value is JsonRecord {
	if (!isRecord(value)) return false;
	if (value.schema_version === "coach_diagnostic_context.v3") return validateV3Context(value);
	if (value.schema_version === "coach_diagnostic_context.v2") return validateV2Context(value);
	if (!v1ContextValidator.Check(value)) return false;
	const resultVersion = validatedAnalysisRefVersion(value.analysis_ref);
	if (resultVersion === undefined) return false;
	return (
		validateDiagnosis(value.diagnosis, resultVersion) &&
		validateEvidenceSummary(value.evidence_summary) &&
		(value.warnings as unknown[]).every(validateWarning)
	);
}

function isCanonicalBenchmarkSummary(value: unknown): value is JsonRecord {
	if (!benchmarkSummaryValidator.Check(value)) return false;
	const record = value as JsonRecord;
	if (
		record.schema_version !== "coach_benchmark_summary.v1" ||
		record.catalog_ref !== "benchmark-catalog:viscose-s2@1" ||
		typeof record.catalog_version !== "string" || record.catalog_version.length > 120 ||
		typeof record.observed_at !== "string" || record.observed_at.length > 40 ||
		!Number.isFinite(Date.parse(record.observed_at)) ||
		!isRecord(record.completion) || !isRecord(record.provisional_ranks) ||
		!Array.isArray(record.scenarios) || record.scenarios.length !== 78 ||
		!Array.isArray(record.review_candidates) || record.review_candidates.length > 8
	) return false;
	for (const difficulty of ["easier", "medium"]) {
		const completion = record.completion[difficulty];
		const rank = record.provisional_ranks[difficulty];
		if (!isRecord(completion) || !hasExactKeys(completion, new Set(["completed", "required"])) ||
			!Number.isInteger(completion.completed) || !Number.isInteger(completion.required) ||
			completion.completed < 0 || completion.completed > completion.required || completion.required !== 39 ||
			!Number.isInteger(rank) || rank < 0 || rank > 9) return false;
	}
	if (Object.keys(record.completion).length !== 2 || Object.keys(record.provisional_ranks).length !== 2) return false;
	const itemKeys = new Set<string>();
	const scenariosByKey = new Map<string, JsonRecord>();
	const validItem = (item: unknown): item is JsonRecord => {
		if (!benchmarkScenarioValidator.Check(item)) return false;
		const rec = item as JsonRecord;
		if (
			typeof rec.scenario_name !== "string" || rec.scenario_name.length === 0 ||
			rec.scenario_name.length > 200 || isUnsafeString(rec.scenario_name) ||
			!BENCHMARK_COURSE_LABELS.has(`${rec.category}:${rec.subcategory}`) ||
			!Number.isFinite(rec.score) || rec.score < 0 ||
			!Number.isInteger(rec.scenario_rank) || rec.scenario_rank < 0 || rec.scenario_rank > 9
		) return false;
		return true;
	};
	for (const item of record.scenarios) {
		if (!validItem(item)) return false;
		const rec = item as JsonRecord;
		const key = `${rec.difficulty}:${rec.scenario_name}`;
		if (itemKeys.has(key)) return false;
		itemKeys.add(key);
		scenariosByKey.set(key, rec);
	}
	const candidateKeys = new Set<string>();
	return (record.review_candidates as unknown[]).every((item) => {
		if (!validItem(item)) return false;
		const key = `${item.difficulty}:${item.scenario_name}`;
		const scenario = scenariosByKey.get(key);
		if (
			candidateKeys.has(key) || scenario === undefined ||
			scenario.category !== item.category || scenario.subcategory !== item.subcategory ||
			scenario.score !== item.score || scenario.scenario_rank !== item.scenario_rank
		) return false;
		candidateKeys.add(key);
		return true;
	});
}

function isCanonicalContextBundle(value: unknown): value is JsonRecord {
	if (!contextBundleValidator.Check(value)) return false;
	const record = value as JsonRecord;
	const contexts = record.contexts as unknown[];
	if (contexts.length > 8) return false;
	if (record.benchmark_summary !== undefined && record.benchmark_summary !== null && !isCanonicalBenchmarkSummary(record.benchmark_summary)) return false;
	const refs = new Set<string>();
	for (const item of contexts) {
		if (!contextBundleItemValidator.Check(item)) return false;
		const ctx = item as JsonRecord;
		if (
			isUnsafeString(ctx.context_ref as string) ||
			refs.has(ctx.context_ref as string)
		) return false;
		refs.add(ctx.context_ref as string);
		if (!/^analysis:[1-9][0-9]*$/.test(ctx.analysis_ref as string)) return false;
		if (ctx.comparison_analysis_ref !== null &&
			!/^analysis:[1-9][0-9]*$/.test(ctx.comparison_analysis_ref as string)
		) return false;
		if (ctx.target_ref !== null && !isSafeScalar(ctx.target_ref)) return false;
		if (ctx.time_range_ms !== null) {
			const tr = ctx.time_range_ms as [number, number];
			if (!(tr[0] >= 0) || !(tr[1] >= 0) || tr[1] < tr[0]) return false;
		}
		if (!isCanonicalDiagnosticContext(ctx.projection)) return false;
		if (ctx.kind === "comparison") {
			if (ctx.comparison_analysis_ref === null || !isCanonicalDiagnosticContext(ctx.comparison_projection)) return false;
		} else if (ctx.comparison_analysis_ref !== null || ctx.comparison_projection !== null) {
			return false;
		}
	}
	return true;
}

type AnalysisSummaryToolOptions = {
	maxResultBytes?: number;
};

export function createAnalysisSummaryTool(
	analysisSummary: string | null,
	options: AnalysisSummaryToolOptions = {},
) {
	let summaryText = "当前没有可用的分析摘要。";
	let hasAnalysis = false;
	let parsedSummary: JsonRecord | null = null;
	let contextSchema: "coach_diagnostic_context.v1" | "coach_diagnostic_context.v2" | "coach_diagnostic_context.v3" | "coach_turn_context.v1" | "coach_benchmark_summary.v1" =
		"coach_diagnostic_context.v1";
	const summaryBytes = analysisSummary ? Buffer.byteLength(analysisSummary, "utf8") : 0;
	if (
		analysisSummary &&
		analysisSummary.trim().length > 0 &&
		summaryBytes <= 256 * 1024
	) {
		try {
			const parsed = JSON.parse(analysisSummary);
			const canonicalBundle = isCanonicalContextBundle(parsed);
			if (
				!hasDuplicateJsonObjectKeys(analysisSummary) &&
				(
					canonicalBundle ||
					(summaryBytes <= 64 * 1024 && (
						isCanonicalDiagnosticContext(parsed) || isCanonicalBenchmarkSummary(parsed)
					))
				)
			) {
				summaryText = analysisSummary;
				parsedSummary = parsed;
				hasAnalysis = canonicalBundle
					? parsed.contexts.length > 0 || ("benchmark_summary" in parsed && parsed.benchmark_summary !== null)
					: true;
				contextSchema = parsed.schema_version;
			}
		} catch {
			// Fail closed; invalid input never becomes model-visible content.
		}
	}

	return {
		name: "get_analysis_summary",
		label: "Get diagnostic context",
		description:
			"读取本轮已附带的 Coach 分析（只读，不访问磁盘或数据库）。多上下文首次不传参数以取得紧凑索引，再用精确 context_ref 和 primary/comparison 每次读取一份投影；benchmark 用 benchmark。分数和排名只能决定优先查看什么，不能诊断 reading、紧张、握法或硬件原因。",
		parameters: Type.Object({
			context_ref: Type.Optional(Type.String({ maxLength: 256 })),
			projection: Type.Optional(Type.Union([
				Type.Literal("primary"),
				Type.Literal("comparison"),
				Type.Literal("benchmark"),
			])),
		}, { additionalProperties: false }),
		async execute(
			_id?: string,
			params: { context_ref?: string; projection?: "primary" | "comparison" | "benchmark" } = {},
		) {
			const result = (text: string, details: JsonRecord) => {
				if (options.maxResultBytes !== undefined && Buffer.byteLength(text, "utf8") > options.maxResultBytes) {
					return {
						content: [{
							type: "text",
							text: "当前模型的上下文窗口不足以读取这份分析，请改用更大上下文窗口的模型。",
						}],
						details: {
							...details,
							result_kind: "unavailable",
							reason: "context_budget_exceeded",
						},
					};
				}
				return { content: [{ type: "text", text }], details };
			};
			if (parsedSummary?.schema_version === "coach_turn_context.v1") {
				const contexts = parsedSummary.contexts as JsonRecord[];
				const requestedRef = params.context_ref?.trim();
				if (!requestedRef && params.projection === "benchmark") {
					const benchmark = parsedSummary.benchmark_summary;
					if (benchmark !== null && benchmark !== undefined) {
						return result(JSON.stringify(benchmark), {
								has_analysis: hasAnalysis,
								context_schema: contextSchema,
								result_kind: "projection",
								context_ref: null,
								projection: "benchmark",
							});
					}
				}
				if (!requestedRef && params.projection === undefined) {
					const index = {
						schema_version: "coach_analysis_context_index.v1",
						contexts: contexts.map((context) => ({
							context_ref: context.context_ref,
							kind: context.kind,
							analysis_ref: context.analysis_ref,
							comparison_analysis_ref: context.comparison_analysis_ref,
							target_ref: context.target_ref,
							time_range_ms: context.time_range_ms,
							available_projections: context.comparison_projection === null
								? ["primary"] : ["primary", "comparison"],
						})),
						benchmark_summary_available:
							parsedSummary.benchmark_summary !== null && parsedSummary.benchmark_summary !== undefined,
					};
					return result(JSON.stringify(index), {
							has_analysis: hasAnalysis,
							context_schema: contextSchema,
							result_kind: "index",
							context_count: contexts.length,
						});
				}

				const context = contexts.find((item) => item.context_ref === requestedRef);
				const projection = params.projection ?? "primary";
				const selected = projection === "primary"
					? context?.projection
					: projection === "comparison" ? context?.comparison_projection : null;
				if (context && selected !== null && selected !== undefined) {
					return result(JSON.stringify(selected), {
							has_analysis: hasAnalysis,
							context_schema: contextSchema,
							result_kind: "projection",
							context_ref: requestedRef,
							projection,
						});
				}
				return {
					content: [{ type: "text", text: "请求的分析上下文投影不可用。" }],
					details: {
						has_analysis: hasAnalysis,
						context_schema: contextSchema,
						result_kind: "unavailable",
						context_ref: requestedRef ?? null,
						projection,
					},
				};
			}
			return result(summaryText, {
					has_analysis: hasAnalysis,
					context_schema: contextSchema,
					result_kind: hasAnalysis ? "summary" : "unavailable",
				});
		},
	};
}
