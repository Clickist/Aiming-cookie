import { loadPiAi } from "./pi-source.ts";

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

const V1_TOP_LEVEL_KEYS = new Set([
	"schema_version",
	"analysis_ref",
	"diagnosis",
	"evidence_summary",
	"warnings",
]);
const V2_TOP_LEVEL_KEYS = new Set([
	"schema_version",
	"analysis_ref",
	"scenario",
	"run_facts",
	"diagnosis",
	"evidence_summary",
	"trends",
	"training",
	"limitations",
]);
const V3_TOP_LEVEL_KEYS = new Set([...V2_TOP_LEVEL_KEYS, "processed_events"]);
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
const ANALYSIS_REF_KEYS = new Set([
	"analysis_id",
	"analysis_result_version",
	"analysis_type",
	"input_mode",
]);
const DIAGNOSIS_KEYS = new Set([
	"profile",
	"issues",
	"summary",
	"comparison",
	"meta",
]);
const PROFILE_KEYS = new Set([
	"archetype_id",
	"label",
	"confidence",
	"secondary_tags",
]);
const ISSUE_KEYS = new Set([
	"signal",
	"severity",
	"priority",
	"priority_reason",
	"plain_language_meaning",
	"expected_result",
	"claim_level",
	"observation_ref",
	"knowledge_registry_version",
	"knowledge_entry_refs",
	"metric_refs",
	"event_refs",
	"limitations",
	"primary_evidence_segment_ref",
	"supporting_evidence_segment_refs",
	"verification",
	"root_causes",
	"prescriptions",
]);
const VERIFICATION_KEYS = new Set([
	"comparable_requirements",
	"success_signals",
	"insufficient_evidence_behavior",
]);
const ROOT_CAUSE_KEYS = new Set(["level", "text"]);
const PRESCRIPTION_KEYS = new Set([
	"scenario",
	"reason",
	"cue",
	"purpose",
	"dosage",
	"retest_after",
	"stop_or_adjust_rule",
	"target_metrics",
	"expected_direction",
	"source_level",
]);
const METRIC_KEYS = new Set([
	"value",
	"unit",
	"provenance",
	"metric_version",
	"classification",
	"min",
	"max",
	"mean",
	"median",
	"med",
	"p25",
	"p50",
	"p75",
	"p90",
	"std",
	"iqr",
	"count",
	"n",
	"score",
	"status",
	"key",
	"availability",
	"sample_count",
	"coverage",
	"limitations",
	"outlier_method",
	"outlier_refs",
	"sample_refs",
	"definition",
]);
const PROVENANCE_KEYS = new Set(["kind", "sources"]);
const COMPARISON_KEYS = new Set([
	"status",
	"reason",
	"comparable",
	"metric",
	"delta",
	"unit",
	"classification",
]);
const META_KEYS = new Set([
	"summary_type",
	"analysis_context",
	"metric_version",
	"scenario_identity_version",
	"calibration_compatibility",
	"minimum_evidence_quality",
	"classification",
]);
const EVIDENCE_SUMMARY_KEYS = new Set([
	"availability",
	"alignment",
	"coverage",
]);
const V2_EVIDENCE_SUMMARY_KEYS = new Set([
	"availability",
	"alignment",
	"coverage",
	"confidence",
	"artifact_ref",
	"evidence_revision",
	"segment_refs",
]);
const ALIGNMENT_KEYS = new Set(["status", "coverage_ratio"]);
const WARNING_KEYS = new Set([
	"code",
	"domain",
	"retryable",
	"user_message_key",
	"evidence_ref",
]);
const EVIDENCE_REF_KEYS = new Set([
	"id",
	"source",
	"artifact_id",
	"alignment_status",
	"availability",
	"local_only",
	"metric_keys",
	"challenge_time_range_ms",
]);
const CLAIM_LEVELS = new Set([
	"measured",
	"deterministic_rule",
	"research_supported",
	"community_practice",
	"community_consensus",
	"experimental",
]);
const SOURCE_LEVELS = new Set([
	"product_contract",
	"academic_peer_reviewed",
	"community_practice",
	"community_consensus",
	"personal_experience_unverified",
	"experimental",
]);
const PROVENANCE_KINDS = new Set(["measured", "derived", "fused"]);
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

function validateProfile(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, PROFILE_KEYS)) return false;
	return Object.entries(value).every(([key, item]) =>
		key === "secondary_tags" ? isSafeStringArray(item) : isSafeScalar(item),
	);
}

function validateVerification(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, VERIFICATION_KEYS)) return false;
	return Object.entries(value).every(([key, item]) =>
		key === "insufficient_evidence_behavior"
			? isSafeScalar(item)
			: isSafeStringArray(item),
	);
}

function validateRootCause(value: unknown): boolean {
	return (
		isRecord(value) &&
		hasOnlyKeys(value, ROOT_CAUSE_KEYS) &&
		Object.values(value).every(isSafeScalar)
	);
}

function validatePrescription(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, PRESCRIPTION_KEYS)) return false;
	return Object.entries(value).every(([key, item]) => {
		if (key === "target_metrics" || key === "expected_direction")
			return isSafeStringArray(item);
		if (key === "source_level")
			return typeof item === "string" && SOURCE_LEVELS.has(item);
		return isSafeScalar(item);
	});
}

function validateIssue(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, ISSUE_KEYS)) return false;
	const hasKnowledgeRegistryVersion = "knowledge_registry_version" in value;
	const hasKnowledgeEntryRefs = "knowledge_entry_refs" in value;
	if (hasKnowledgeRegistryVersion !== hasKnowledgeEntryRefs) return false;
	return Object.entries(value).every(([key, item]) => {
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
	if (!isRecord(value) || !hasOnlyKeys(value, PROVENANCE_KEYS)) return false;
	if (typeof value.kind !== "string" || !PROVENANCE_KINDS.has(value.kind))
		return false;
	return value.sources === undefined || isSafeStringArray(value.sources);
}

function validateMetric(
	value: unknown,
	requireDeterministic: boolean,
): boolean {
	if (
		!isRecord(value) ||
		Object.keys(value).length === 0 ||
		!hasOnlyKeys(value, METRIC_KEYS)
	) {
		return false;
	}
	if (requireDeterministic && value.classification !== "deterministic") {
		return false;
	}
	return Object.entries(value).every(([key, item]) => {
		if (
			key === "limitations" ||
			key === "outlier_refs" ||
			key === "sample_refs"
		) {
			return isSafeStringArray(item);
		}
		if (key === "provenance") return validateProvenance(item);
		if (key === "classification") return item === "deterministic";
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

function validateFlatRecord(value: unknown, keys: Set<string>): boolean {
	return (
		isRecord(value) &&
		hasOnlyKeys(value, keys) &&
		Object.values(value).every(isSafeScalar)
	);
}

function validateDiagnosis(
	value: unknown,
	resultVersion: AnalysisResultVersion,
): boolean {
	if (!isRecord(value) || !hasExactKeys(value, DIAGNOSIS_KEYS)) return false;
	return Object.entries(value).every(([key, item]) => {
		if (key === "profile") return validateProfile(item);
		if (key === "issues")
			return Array.isArray(item) && item.every(validateIssue);
		if (key === "summary") {
			return validateSummary(item, resultVersion === "analysis_result.v2");
		}
		if (key === "comparison") {
			return (
				item === null ||
				(validateFlatRecord(item, COMPARISON_KEYS) &&
					isRecord(item) &&
					item.classification === "deterministic")
			);
		}
		if (key === "meta") {
			return (
				validateFlatRecord(item, META_KEYS) &&
				(!isRecord(item) ||
					item.classification === undefined ||
					item.classification === "deterministic")
			);
		}
		return false;
	});
}

function validateEvidenceRef(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, EVIDENCE_REF_KEYS)) return false;
	return Object.entries(value).every(([key, item]) => {
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
	if (
		!isRecord(value) ||
		!hasOnlyKeys(value, WARNING_KEYS) ||
		typeof value.code !== "string"
	) {
		return false;
	}
	return Object.entries(value).every(([key, item]) =>
		key === "evidence_ref" ? validateEvidenceRef(item) : isSafeScalar(item),
	);
}

function validateEvidenceSummary(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, EVIDENCE_SUMMARY_KEYS))
		return false;
	if (!("availability" in value) || !("alignment" in value)) return false;
	return Object.entries(value).every(([key, item]) => {
		if (key === "availability") {
			return (
				isRecord(item) &&
				Object.entries(item).every(
					([source, availability]) =>
						!isForbiddenKey(source) && isSafeScalar(availability),
				)
			);
		}
		if (key === "alignment") return validateFlatRecord(item, ALIGNMENT_KEYS);
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
	if (!isRecord(value) || !hasExactKeys(value, new Set([
		"scenario_profile_ref",
		"analyzer_refs",
		"support_status",
		"limitations",
	]))) return false;
	return (
		(value.scenario_profile_ref === null || (typeof value.scenario_profile_ref === "string" && !isUnsafeString(value.scenario_profile_ref))) &&
		isSafeStringArray(value.analyzer_refs) && value.analyzer_refs.length <= 16 &&
		typeof value.support_status === "string" && new Set(["supported", "partial", "outcome_only", "unsupported", "unavailable"]).has(value.support_status) &&
		isSafeStringArray(value.limitations) && value.limitations.length <= 8
	);
}

function validateV2EvidenceSummary(value: unknown): boolean {
	if (!isRecord(value) || !hasOnlyKeys(value, V2_EVIDENCE_SUMMARY_KEYS)) return false;
	if (!("availability" in value) || !("alignment" in value)) return false;
	if (!isRecord(value.availability) || !Object.entries(value.availability).every(([key, item]) => !isForbiddenKey(key) && isSafeScalar(item))) return false;
	if (!validateFlatRecord(value.alignment, ALIGNMENT_KEYS)) return false;
	for (const key of ["coverage", "confidence", "artifact_ref", "evidence_revision"]) {
		if (key in value && !isSafeScalar(value[key])) return false;
	}
	return !("segment_refs" in value) || (isSafeStringArray(value.segment_refs) && value.segment_refs.length <= 24);
}

function validateV2Context(value: JsonRecord): boolean {
	if (!hasExactKeys(value, V2_TOP_LEVEL_KEYS) || value.schema_version !== "coach_diagnostic_context.v2") return false;
	const resultVersion = validatedAnalysisRefVersion(value.analysis_ref);
	if (resultVersion !== "analysis_result.v2") return false;
	if (!validateV2Scenario(value.scenario) || !validateV2RunFacts(value.run_facts)) return false;
	if (!validateDiagnosis(value.diagnosis, resultVersion) || !validateV2EvidenceSummary(value.evidence_summary)) return false;
	if (!Array.isArray(value.trends) || value.trends.length > 4 || !value.trends.every((item) => validateBoundedFactsValue(item))) return false;
	if (!isRecord(value.training) || !hasExactKeys(value.training, new Set(["active_plan_ref", "recent_retest_ref"]))) return false;
	if (!Object.values(value.training).every((item) => item === null || (typeof item === "string" && !isUnsafeString(item)))) return false;
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
	if (!hasExactKeys(value, V3_TOP_LEVEL_KEYS) || value.schema_version !== "coach_diagnostic_context.v3") return false;
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
	if (!isRecord(value) || !hasExactKeys(value, ANALYSIS_REF_KEYS)) {
		return undefined;
	}
	const version = value.analysis_result_version;
	const analysisId = value.analysis_id;
	const analysisType = value.analysis_type;
	const inputMode = value.input_mode;
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
	if (!hasExactKeys(value, V1_TOP_LEVEL_KEYS)) return false;
	if (value.schema_version !== "coach_diagnostic_context.v1") return false;
	const resultVersion = validatedAnalysisRefVersion(value.analysis_ref);
	if (resultVersion === undefined) return false;
	return (
		validateDiagnosis(value.diagnosis, resultVersion) &&
		validateEvidenceSummary(value.evidence_summary) &&
		Array.isArray(value.warnings) &&
		value.warnings.every(validateWarning)
	);
}

function isCanonicalBenchmarkSummary(value: unknown): value is JsonRecord {
	if (!isRecord(value) || !hasExactKeys(value, new Set([
		"schema_version", "catalog_ref", "catalog_version", "observed_at", "completion",
		"provisional_ranks", "scenarios", "review_candidates",
	]))) return false;
	if (
		value.schema_version !== "coach_benchmark_summary.v1" ||
		value.catalog_ref !== "benchmark-catalog:viscose-s2@1" ||
		typeof value.catalog_version !== "string" || value.catalog_version.length > 120 ||
		typeof value.observed_at !== "string" || value.observed_at.length > 40 ||
		!Number.isFinite(Date.parse(value.observed_at)) ||
		!isRecord(value.completion) || !isRecord(value.provisional_ranks) ||
		!Array.isArray(value.scenarios) || value.scenarios.length !== 78 ||
		!Array.isArray(value.review_candidates) || value.review_candidates.length > 8
	) return false;
	for (const difficulty of ["easier", "medium"]) {
		const completion = value.completion[difficulty];
		const rank = value.provisional_ranks[difficulty];
		if (!isRecord(completion) || !hasExactKeys(completion, new Set(["completed", "required"])) ||
			!Number.isInteger(completion.completed) || !Number.isInteger(completion.required) ||
			completion.completed < 0 || completion.completed > completion.required || completion.required !== 39 ||
			!Number.isInteger(rank) || rank < 0 || rank > 9) return false;
	}
	if (Object.keys(value.completion).length !== 2 || Object.keys(value.provisional_ranks).length !== 2) return false;
	const itemKeys = new Set<string>();
	const scenariosByKey = new Map<string, JsonRecord>();
	const validItem = (item: unknown): item is JsonRecord => {
		if (!isRecord(item) || !hasExactKeys(item, new Set([
			"difficulty", "scenario_name", "category", "subcategory", "score", "scenario_rank",
		]))) return false;
		if (
			(item.difficulty !== "easier" && item.difficulty !== "medium") ||
			typeof item.scenario_name !== "string" || item.scenario_name.length === 0 ||
			item.scenario_name.length > 200 || isUnsafeString(item.scenario_name) ||
			typeof item.category !== "string" || typeof item.subcategory !== "string" ||
			!BENCHMARK_COURSE_LABELS.has(`${item.category}:${item.subcategory}`) ||
			typeof item.score !== "number" || !Number.isFinite(item.score) || item.score < 0 ||
			!Number.isInteger(item.scenario_rank) || item.scenario_rank < 0 || item.scenario_rank > 9
		) return false;
		return true;
	};
	for (const item of value.scenarios) {
		if (!validItem(item)) return false;
		const key = `${item.difficulty}:${item.scenario_name}`;
		if (itemKeys.has(key)) return false;
		itemKeys.add(key);
		scenariosByKey.set(key, item);
	}
	const candidateKeys = new Set<string>();
	return value.review_candidates.every((item) => {
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
	if (!isRecord(value) || !hasOnlyKeys(value, new Set([
		"schema_version", "contexts", "benchmark_summary",
	]))) {
		return false;
	}
	if (value.schema_version !== "coach_turn_context.v1") return false;
	if (!Array.isArray(value.contexts) || value.contexts.length > 8) return false;
	if ("benchmark_summary" in value && value.benchmark_summary !== null && !isCanonicalBenchmarkSummary(value.benchmark_summary)) return false;
	const refs = new Set<string>();
	for (const item of value.contexts) {
		if (!isRecord(item) || !hasExactKeys(item, new Set([
			"context_ref", "kind", "analysis_ref", "comparison_analysis_ref",
			"target_ref", "time_range_ms", "projection", "comparison_projection",
		]))) return false;
		if (
			typeof item.context_ref !== "string" || isUnsafeString(item.context_ref) ||
			refs.has(item.context_ref)
		) return false;
		refs.add(item.context_ref);
		if (!new Set(["analysis", "issue", "time_range", "metric", "evidence_segment", "comparison"]).has(String(item.kind))) return false;
		if (typeof item.analysis_ref !== "string" || !/^analysis:[1-9][0-9]*$/.test(item.analysis_ref)) return false;
		if (item.comparison_analysis_ref !== null && (
			typeof item.comparison_analysis_ref !== "string" ||
			!/^analysis:[1-9][0-9]*$/.test(item.comparison_analysis_ref)
		)) return false;
		if (item.target_ref !== null && (typeof item.target_ref !== "string" || !isSafeScalar(item.target_ref))) return false;
		if (item.time_range_ms !== null && (
			!Array.isArray(item.time_range_ms) || item.time_range_ms.length !== 2 ||
			!item.time_range_ms.every((part) => typeof part === "number" && Number.isFinite(part) && part >= 0) ||
			item.time_range_ms[1] < item.time_range_ms[0]
		)) return false;
		if (!isCanonicalDiagnosticContext(item.projection)) return false;
		if (item.kind === "comparison") {
			if (item.comparison_analysis_ref === null || !isCanonicalDiagnosticContext(item.comparison_projection)) return false;
		} else if (item.comparison_analysis_ref !== null || item.comparison_projection !== null) {
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
