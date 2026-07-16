import { loadPiAi } from "./pi-source.ts";

const { Type } = (await loadPiAi()) as {
	Type: {
		Object(
			properties: Record<string, unknown>,
			options?: Record<string, unknown>,
		): unknown;
	};
};

type JsonRecord = Record<string, unknown>;

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

const TOP_LEVEL_KEYS = new Set([
	"schema_version",
	"analysis_ref",
	"diagnosis",
	"evidence_summary",
	"warnings",
]);
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
	"metric_refs",
	"event_refs",
	"limitations",
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
	"community_consensus",
	"experimental",
]);
const SOURCE_LEVELS = new Set([
	"product_contract",
	"academic_peer_reviewed",
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
	return Object.entries(value).every(([key, item]) => {
		if (
			key === "metric_refs" ||
			key === "event_refs" ||
			key === "limitations"
		) {
			return isSafeStringArray(item);
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
	if (!isRecord(value) || !hasOnlyKeys(value, TOP_LEVEL_KEYS)) return false;
	if (Object.keys(value).length !== TOP_LEVEL_KEYS.size) return false;
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

export function createAnalysisSummaryTool(analysisSummary: string | null) {
	let summaryText = "当前没有可用的分析摘要。";
	let hasAnalysis = false;
	if (
		analysisSummary &&
		analysisSummary.trim().length > 0 &&
		Buffer.byteLength(analysisSummary, "utf8") <= 64 * 1024
	) {
		try {
			const parsed = JSON.parse(analysisSummary);
			if (
				!hasDuplicateJsonObjectKeys(analysisSummary) &&
				isCanonicalDiagnosticContext(parsed)
			) {
				summaryText = analysisSummary;
				hasAnalysis = true;
			}
		} catch {
			// Fail closed; invalid input never becomes model-visible content.
		}
	}

	return {
		name: "get_analysis_summary",
		label: "Get diagnostic context",
		description:
			"返回本轮请求中已附带的 coach_diagnostic_context.v1 JSON（只读，不访问磁盘或数据库）。",
		parameters: Type.Object({}, { additionalProperties: false }),
		async execute() {
			return {
				content: [{ type: "text", text: summaryText }],
				details: {
					has_analysis: hasAnalysis,
					context_schema: "coach_diagnostic_context.v1",
				},
			};
		},
	};
}
