"""Canonical, allow-list-only context passed from Analysis to Coach runtimes."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION = "coach_diagnostic_context.v1"
COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION = "coach_diagnostic_context.v2"
COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION = "coach_diagnostic_context.v3"
_COACH_CONTEXT_V2_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "analysis_ref",
        "scenario",
        "run_facts",
        "diagnosis",
        "evidence_summary",
        "trends",
        "training",
        "limitations",
    }
)
_COACH_CONTEXT_V3_TOP_LEVEL_FIELDS = frozenset(
    {*_COACH_CONTEXT_V2_TOP_LEVEL_FIELDS, "processed_events"}
)
_PROCESSED_EVENT_QUERY_CAPABILITIES = [
    "analysis.events.list",
    "analysis.events.get",
    "analysis.events.rank",
    "analysis.events.filter",
    "analysis.events.aggregate",
    "analysis.events.co_occurrence",
    "analysis.events.sequence",
    "analysis.evidence.compare",
]
_COACH_CONTEXT_MAX_BYTES = 32 * 1024
_COACH_FACTS_MAX_BYTES = 8 * 1024
_TARGET_RELATIVE_FACTS_UNAVAILABLE = "target_relative_facts_unavailable"
_REPHRASED_TARGET_RELATIVE_MEANINGS = {
    "reverse_ratio high": "移动收尾时出现了较多反向修正",
}

_MISSING = object()
_METRIC_FIELDS = frozenset(
    {
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
    }
)
_COMPARISON_FIELDS = frozenset(
    {
        "status",
        "reason",
        "comparable",
        "metric",
        "delta",
        "unit",
        "classification",
    }
)
_META_FIELDS = frozenset(
    {
        "summary_type",
        "analysis_context",
        "metric_version",
        "scenario_identity_version",
        "calibration_compatibility",
        "minimum_evidence_quality",
        "classification",
    }
)
_WARNING_FIELDS = frozenset({"code", "domain", "retryable", "user_message_key", "evidence_ref"})
_CLAIM_LEVELS = frozenset(
    {
        "measured", "deterministic_rule", "research_supported",
        "community_practice", "community_consensus", "experimental",
    }
)
_SOURCE_LEVELS = frozenset(
    {
        "product_contract", "academic_peer_reviewed", "community_practice",
        "community_consensus", "personal_experience_unverified", "experimental",
    }
)
_EVIDENCE_REF_FIELDS = (
    "id",
    "source",
    "artifact_id",
    "alignment_status",
    "availability",
    "local_only",
)
_ANALYSIS_REF_FIELDS = frozenset(
    {"analysis_id", "analysis_result_version", "analysis_type", "input_mode"}
)
_ANALYSIS_ID_PATTERN = re.compile(r"^analysis:[A-Za-z0-9][A-Za-z0-9._-]*$")
_NETWORK_URL_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_V2_INPUT_MODES = frozenset({"input_native", "multimodal", "video_fallback"})
_V2_SCENARIO_AIM_FAMILIES = frozenset({
    "static_clicking", "dynamic_clicking", "continuous_tracking",
    "target_switching", "movement_aiming", "unknown",
})


def _is_absolute_path(value: str) -> bool:
    candidate = value.strip()
    return (
        os.path.isabs(candidate)
        or candidate.casefold().startswith("file:")
        or candidate.startswith(("~/", "../", "..\\"))
        or candidate.startswith("\\")
        or (
            len(candidate) >= 3
            and candidate[0].isalpha()
            and candidate[1] == ":"
            and candidate[2] in {"/", "\\"}
        )
    )


def _contains_sensitive_text(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|password|secret)\s*[:=]|\bbearer\s+\S{8,}|"
            r"\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{8,}|"
            r"(?:raw[\s_-]*trace|target[\s_-]*inference|"
            r"sensitivity[\s_-]*heuristic|external[\s_-]*progress|"
            r"benchmark|payload)",
            value,
        )
    )


def _is_network_url(value: str) -> bool:
    candidate = value.strip()
    if _NETWORK_URL_PREFIX.match(candidate):
        return True
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return True
    return bool(parsed.scheme and parsed.netloc)


def _is_forbidden_key(key: object) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    if compact == "path" or compact.endswith("path") or compact.endswith("paths"):
        return True
    if any(
        marker in compact
        for marker in (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "clientsecret",
            "credential",
            "authorization",
            "password",
            "secret",
        )
    ):
        return True
    if compact.startswith("rawinput") and compact != "rawinput":
        return True
    if any(
        marker in compact
        for marker in (
            "targetinference",
            "sensitivity",
            "heuristic",
            "benchmark",
            "external",
            "progress",
            "payload",
            "rawtrace",
            "tracepoints",
        )
    ):
        return True
    return compact in {
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
    }


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _MISSING
    if isinstance(value, str):
        return (
            value
            if not _is_absolute_path(value)
            and not _is_network_url(value)
            and not _contains_sensitive_text(value)
            else _MISSING
        )
    return _MISSING


def _safe_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, str) and _safe_scalar(item) is not _MISSING
    ]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _project_metric(
    metric: object,
    *,
    require_deterministic: bool,
) -> dict[str, object] | None:
    if isinstance(metric, Mapping):
        classification = metric.get("classification")
        if require_deterministic:
            if classification != "deterministic":
                return None
        elif classification not in (None, "deterministic"):
            return None
        provenance_value = metric.get("provenance")
        if "provenance" in metric:
            if not isinstance(provenance_value, Mapping):
                return None
            if provenance_value.get("kind") not in {
                "measured",
                "derived",
                "fused",
            }:
                return None
        out: dict[str, object] = {}
        for key in _METRIC_FIELDS:
            if key not in metric or _is_forbidden_key(key):
                continue
            if key in {"limitations", "outlier_refs", "sample_refs"}:
                values = _safe_string_list(metric[key])
                if values:
                    out[key] = values
                continue
            if key == "provenance" and isinstance(metric[key], Mapping):
                provenance: dict[str, object] = {}
                kind = _safe_scalar(metric[key].get("kind"))
                if kind is not _MISSING:
                    provenance["kind"] = kind
                sources = _safe_string_list(metric[key].get("sources"))
                if sources:
                    provenance["sources"] = sources
                if provenance:
                    out[key] = provenance
                continue
            value = _safe_scalar(metric[key])
            if key == "classification" and value is None:
                continue
            if value is not _MISSING:
                out[key] = value
        return out or None

    if require_deterministic:
        return None
    value = _safe_scalar(metric)
    return {"value": value} if value is not _MISSING else None


def _project_summary(
    summary: object,
    *,
    require_deterministic: bool,
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    if not isinstance(summary, Mapping):
        return out
    for metric_key, metric in summary.items():
        key = str(metric_key)
        if _is_forbidden_key(key):
            continue
        projected = _project_metric(
            metric,
            require_deterministic=require_deterministic,
        )
        if projected is not None:
            out[key] = projected
    return out


def resolve_registry_teaching_entry(issue: Mapping[str, Any]) -> dict[str, Any] | None:
    registry_version = issue.get("knowledge_registry_version")
    entry_refs_raw = issue.get("knowledge_entry_refs")
    observation_ref = issue.get("observation_ref")
    if (
        not isinstance(registry_version, str)
        or not registry_version.strip()
        or not isinstance(entry_refs_raw, list)
        or len(entry_refs_raw) != 1
        or not isinstance(entry_refs_raw[0], str)
        or not entry_refs_raw[0].strip()
        or not isinstance(observation_ref, str)
        or not observation_ref.strip()
    ):
        return None
    entry_ref = entry_refs_raw[0].strip()
    explicit_registry_metrics = {
        metric_ref
        for metric_ref in _safe_string_list(issue.get("metric_refs"))
        if metric_ref.startswith("metric:")
    }
    try:
        from kovaak_tracker.coach.knowledge_registry import (
            KnowledgeRegistryError,
            resolve_entry,
        )

        entry = resolve_entry(
            registry_version=registry_version.strip(),
            entry_reference=entry_ref,
        )
    except (KeyError, TypeError, ValueError, OSError, KnowledgeRegistryError):
        return None
    if entry.get("status") != "active":
        return None
    if observation_ref.strip() not in entry.get("observation_refs", []):
        return None
    if explicit_registry_metrics and not explicit_registry_metrics.intersection(
        entry.get("metric_refs", [])
    ):
        return None
    return entry


def _registry_teaching_overlay(issue: Mapping[str, Any]) -> dict[str, Any] | None:
    entry = resolve_registry_teaching_entry(issue)
    if entry is None or "candidate_experiment" not in entry.get("supported_uses", []):
        return None

    cue = entry.get("cue")
    dose = entry.get("dose_guardrail")
    matched_retest = entry.get("matched_retest")
    stop_rules = entry.get("stop_adjust_rule")
    definition = entry.get("definition")
    expected_direction = entry.get("expected_direction")
    if (
        not isinstance(cue, Mapping)
        or not isinstance(dose, list)
        or len(dose) != 1
        or not isinstance(dose[0], Mapping)
        or not isinstance(matched_retest, Mapping)
        or not isinstance(stop_rules, list)
        or len(stop_rules) != 1
        or not isinstance(stop_rules[0], Mapping)
        or not isinstance(definition, Mapping)
        or not isinstance(expected_direction, Mapping)
    ):
        return None
    texts = {
        "cue": cue.get("text"),
        "dosage": dose[0].get("text"),
        "retest_after": matched_retest.get("text"),
        "stop_or_adjust_rule": stop_rules[0].get("text"),
        "purpose": definition.get("text"),
        "direction": expected_direction.get("text"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in texts.values()):
        return None
    source_level = cue.get("claim_level")
    return {
        "root_cause": {"level": "training", "text": texts["purpose"]},
        "prescription": {
            "cue": texts["cue"],
            "purpose": texts["purpose"],
            "dosage": texts["dosage"],
            "target_metrics": list(entry.get("metric_refs") or []),
            "expected_direction": [texts["direction"]],
            "retest_after": texts["retest_after"],
            "stop_or_adjust_rule": texts["stop_or_adjust_rule"],
            "source_level": (
                source_level if source_level in _SOURCE_LEVELS else "experimental"
            ),
        },
    }


def _issue_with_registry_teaching(issue: object) -> object:
    if not isinstance(issue, Mapping):
        return issue
    overlay = _registry_teaching_overlay(issue)
    if overlay is None:
        return issue
    enriched = dict(issue)
    root_causes = issue.get("root_causes")
    if not isinstance(root_causes, list) or not root_causes:
        enriched["root_causes"] = [overlay["root_cause"]]
    enriched["prescriptions"] = [overlay["prescription"]]
    return enriched


def _project_issue(
    issue: object,
    *,
    include_specific_scenarios: bool = True,
    inherited_limitations: Sequence[str] = (),
) -> dict[str, object] | None:
    if not isinstance(issue, Mapping):
        return None
    out: dict[str, object] = {}
    for key in ("signal", "severity", "priority", "priority_reason"):
        if key not in issue or _is_forbidden_key(key):
            continue
        value = _safe_scalar(issue[key])
        if value is not _MISSING:
            out[key] = value

    limitations = _safe_string_list(issue.get("limitations"))
    effective_limitations = list(dict.fromkeys([*limitations, *inherited_limitations]))
    for key in ("plain_language_meaning", "expected_result"):
        if key not in issue:
            continue
        value = _safe_scalar(issue.get(key))
        if (
            key == "plain_language_meaning"
            and _TARGET_RELATIVE_FACTS_UNAVAILABLE in effective_limitations
            and issue.get("signal") in _REPHRASED_TARGET_RELATIVE_MEANINGS
        ):
            value = _REPHRASED_TARGET_RELATIVE_MEANINGS[issue["signal"]]
        if value is not _MISSING:
            out[key] = value

    claim_level = issue.get("claim_level")
    out["claim_level"] = (
        claim_level
        if isinstance(claim_level, str) and claim_level in _CLAIM_LEVELS
        else "experimental"
    )

    for key in ("metric_refs", "event_refs"):
        values = _safe_string_list(issue.get(key))
        if values:
            out[key] = values
    if effective_limitations:
        out["limitations"] = effective_limitations

    if resolve_registry_teaching_entry(issue) is not None:
        out["observation_ref"] = issue["observation_ref"].strip()
        out["knowledge_registry_version"] = issue[
            "knowledge_registry_version"
        ].strip()
        out["knowledge_entry_refs"] = [
            issue["knowledge_entry_refs"][0].strip()
        ]

    primary = issue.get("primary_evidence_segment_ref")
    if primary is not None:
        primary = _safe_scalar(primary)
        if (
            primary is _MISSING
            or not isinstance(primary, str)
            or not primary.startswith("analysis:")
            or ":segment:" not in primary
        ):
            return None
        out["primary_evidence_segment_ref"] = primary
    supporting = _safe_string_list(issue.get("supporting_evidence_segment_refs"))
    if len(supporting) > 2 or any(
        not ref.startswith("analysis:") or ":segment:" not in ref
        for ref in supporting
    ):
        return None
    if supporting:
        out["supporting_evidence_segment_refs"] = supporting

    verification = issue.get("verification")
    if isinstance(verification, Mapping):
        projected_verification: dict[str, object] = {}
        comparable_requirements = _safe_string_list(
            verification.get("comparable_requirements")
        )
        if comparable_requirements:
            projected_verification["comparable_requirements"] = comparable_requirements
        success_signals = _safe_string_list(verification.get("success_signals"))
        if success_signals:
            projected_verification["success_signals"] = success_signals
        if "insufficient_evidence_behavior" in verification:
            insufficient = _safe_scalar(verification.get("insufficient_evidence_behavior"))
            if insufficient is not _MISSING:
                projected_verification["insufficient_evidence_behavior"] = insufficient
        if projected_verification:
            out["verification"] = projected_verification

    root_causes: list[dict[str, object]] = []
    for root_cause in issue.get("root_causes") or []:
        if not isinstance(root_cause, Mapping):
            continue
        projected: dict[str, object] = {}
        for key in ("level", "text"):
            if key not in root_cause:
                continue
            value = _safe_scalar(root_cause.get(key))
            if value is not _MISSING:
                projected[key] = value
        if projected:
            root_causes.append(projected)
    if root_causes:
        out["root_causes"] = root_causes

    prescriptions: list[dict[str, object]] = []
    for prescription in issue.get("prescriptions") or []:
        if not isinstance(prescription, Mapping):
            continue
        projected: dict[str, object] = {}
        for key in (
            "scenario",
            "reason",
            "cue",
            "purpose",
            "dosage",
            "retest_after",
            "stop_or_adjust_rule",
        ):
            if key == "scenario" and not include_specific_scenarios:
                continue
            if key not in prescription:
                continue
            value = _safe_scalar(prescription.get(key))
            if value is not _MISSING and value != "":
                projected[key] = value
        for key in ("target_metrics", "expected_direction"):
            values = _safe_string_list(prescription.get(key))
            if values:
                projected[key] = values
        source_level = prescription.get("source_level")
        projected["source_level"] = (
            source_level
            if isinstance(source_level, str) and source_level in _SOURCE_LEVELS
            else "experimental"
        )
        if projected:
            prescriptions.append(projected)
    if prescriptions:
        out["prescriptions"] = prescriptions

    return out or None


def _project_comparison(comparison: object) -> dict[str, object] | None:
    if not isinstance(comparison, Mapping):
        return None
    if comparison.get("classification") != "deterministic":
        return None
    out: dict[str, object] = {}
    for key in _COMPARISON_FIELDS:
        if key not in comparison:
            continue
        value = _safe_scalar(comparison.get(key))
        if key == "classification" and value is None:
            continue
        if value is not _MISSING:
            out[key] = value
    return out or None


def _project_meta(meta: object) -> dict[str, object]:
    if not isinstance(meta, Mapping) or meta.get("classification") not in (None, "deterministic"):
        return {}
    out: dict[str, object] = {}
    for key in _META_FIELDS:
        if key not in meta:
            continue
        value = _safe_scalar(meta.get(key))
        if key == "classification" and value is None:
            continue
        if value is not _MISSING:
            out[key] = value
    return out


def _project_diagnosis(
    diagnosis: object,
    *,
    fallback_summary: object = None,
    require_deterministic_metrics: bool,
    attach_registry_teaching: bool = False,
    include_specific_scenarios: bool = True,
    inherited_limitations: Sequence[str] = (),
) -> dict[str, object]:
    data = _mapping(diagnosis)
    profile = _mapping(data.get("profile"))
    projected_profile: dict[str, object] = {}
    for key in ("archetype_id", "label", "confidence"):
        if key not in profile:
            continue
        value = _safe_scalar(profile.get(key))
        if value is not _MISSING:
            projected_profile[key] = value
    tags = _safe_string_list(profile.get("secondary_tags"))
    if tags:
        projected_profile["secondary_tags"] = tags

    issues = [
        projected
        for issue in data.get("issues") or []
        if (
            projected := _project_issue(
                _issue_with_registry_teaching(issue)
                if attach_registry_teaching else issue,
                include_specific_scenarios=include_specific_scenarios,
                inherited_limitations=inherited_limitations,
            )
        ) is not None
    ]
    summary = _project_summary(
        data.get("summary"),
        require_deterministic=require_deterministic_metrics,
    )
    fallback = _project_summary(
        fallback_summary,
        require_deterministic=require_deterministic_metrics,
    )
    for key, metric in fallback.items():
        summary[key] = {**metric, **summary.get(key, {})}

    return {
        "profile": projected_profile,
        "issues": issues,
        "summary": summary,
        "comparison": _project_comparison(data.get("comparison")),
        "meta": _project_meta(data.get("meta")),
    }


def _project_evidence_ref(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    out: dict[str, object] = {}
    for key in _EVIDENCE_REF_FIELDS:
        projected = _safe_scalar(value.get(key))
        if projected is not _MISSING and projected is not None:
            out[key] = projected
    metric_keys = _safe_string_list(value.get("metric_keys"))
    if metric_keys:
        out["metric_keys"] = metric_keys
    time_range = value.get("challenge_time_range_ms")
    if isinstance(time_range, list) and len(time_range) == 2:
        projected_range = [_safe_scalar(item) for item in time_range]
        if all(item is not _MISSING and item is not None for item in projected_range):
            out["challenge_time_range_ms"] = projected_range
    return out or None


def _project_warning(warning: object) -> dict[str, object] | None:
    if not isinstance(warning, Mapping):
        return None
    out: dict[str, object] = {}
    for key in _WARNING_FIELDS:
        if _is_forbidden_key(key):
            continue
        if key == "evidence_ref":
            evidence_ref = _project_evidence_ref(warning.get(key))
            if evidence_ref is not None:
                out[key] = evidence_ref
            continue
        value = _safe_scalar(warning.get(key))
        if value is not _MISSING and value is not None:
            out[key] = value
    return out if isinstance(out.get("code"), str) else None


def _project_warnings(*warning_lists: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for warnings in warning_lists:
        if not isinstance(warnings, list):
            continue
        for warning in warnings:
            projected = _project_warning(warning)
            if projected is None:
                continue
            code = str(projected["code"])
            if code not in seen:
                seen.add(code)
                out.append(projected)
    return out


def _project_evidence_summary(result: Mapping[str, Any], schema_version: str) -> dict[str, object]:
    if schema_version == "analysis_result.v2":
        evidence = _mapping(result.get("evidence"))
        availability: dict[str, object] = {}
        for key, raw_value in _mapping(evidence.get("availability")).items():
            if _is_forbidden_key(key):
                continue
            value = _safe_scalar(raw_value)
            if value is not _MISSING:
                availability[str(key)] = value
        alignment = _mapping(evidence.get("alignment"))
        projected_alignment: dict[str, object] = {}
        for key in ("status", "coverage_ratio"):
            if key not in alignment:
                continue
            value = _safe_scalar(alignment.get(key))
            if value is not _MISSING:
                projected_alignment[key] = value
        out: dict[str, object] = {
            "availability": availability,
            "alignment": projected_alignment,
        }
        if "coverage" in evidence:
            coverage = _safe_scalar(evidence.get("coverage"))
            if coverage is not _MISSING:
                out["coverage"] = coverage
        return out

    availability: dict[str, object] = {}
    manifest = _mapping(result.get("artifact_manifest"))
    for artifact in manifest.get("inputs") or []:
        if not isinstance(artifact, Mapping):
            continue
        kind = artifact.get("kind")
        status = _safe_scalar(artifact.get("status"))
        if (
            isinstance(kind, str)
            and not _is_forbidden_key(kind)
            and status is not _MISSING
        ):
            availability[kind] = status
    return {"availability": availability, "alignment": {}}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _project_bounded_v2_section_summary(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    expected = {
        "section_key",
        "section_ref",
        "completeness",
        "present_field_count",
        "source_absent_field_count",
        "omitted_known_field_count",
    }
    if set(value) != expected:
        return None
    section_key = value.get("section_key")
    section_ref = value.get("section_ref")
    completeness = value.get("completeness")
    if not isinstance(section_key, str) or _safe_scalar(section_key) is _MISSING:
        return None
    if not isinstance(section_ref, str) or _safe_scalar(section_ref) is _MISSING:
        return None
    if completeness not in {"complete_allowlisted", "partial"}:
        return None
    counts: dict[str, int] = {}
    for key in (
        "present_field_count",
        "source_absent_field_count",
        "omitted_known_field_count",
    ):
        count = value.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        counts[key] = count
    return {
        "section_key": section_key,
        "section_ref": section_ref,
        "completeness": completeness,
        **counts,
    }


def _project_v2_run_facts(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    mode = value.get("mode")
    if mode not in {"inline", "section_refs", "unavailable"}:
        return None
    limitations = _safe_string_list(value.get("limitations"))
    if len(limitations) > 8:
        return None

    if mode == "unavailable":
        if set(value) - {"mode", "field_registry_version", "limitations"}:
            return None
        if "field_registry_version" in value and value.get("field_registry_version") != "source_field_registry.v1":
            return None
        projected_unavailable = {"mode": mode}
        if "field_registry_version" in value:
            projected_unavailable["field_registry_version"] = value["field_registry_version"]
        projected_unavailable["limitations"] = limitations
        return projected_unavailable

    field_registry_version = value.get("field_registry_version")
    if "field_registry_version" in value and field_registry_version != "source_field_registry.v1":
        return None
    if mode == "inline":
        if set(value) - {"mode", "field_registry_version", "facts", "limitations"}:
            return None
        facts = value.get("facts")
        if not isinstance(facts, Mapping):
            return None
        try:
            from kovaak_tracker.analysis_evidence import validate_canonical_run_facts_v1

            validated = validate_canonical_run_facts_v1(facts)
        except (TypeError, ValueError, KeyError):
            return None
        if len(_canonical_json_bytes(validated)) > _COACH_FACTS_MAX_BYTES:
            return None
        projected_inline = {
            "mode": mode,
            "facts": validated,
            "limitations": limitations,
        }
        if "field_registry_version" in value:
            projected_inline["field_registry_version"] = field_registry_version
        return projected_inline

    if set(value) - {
        "mode",
        "field_registry_version",
        "section_summaries",
        "limitations",
    }:
        return None
    summaries = value.get("section_summaries")
    if not isinstance(summaries, list) or len(summaries) > 7:
        return None
    projected = []
    for item in summaries:
        summary = _project_bounded_v2_section_summary(item)
        if summary is None:
            return None
        projected.append(summary)
    projected_section_refs = {
        "mode": mode,
        "section_summaries": projected,
        "limitations": limitations,
    }
    if "field_registry_version" in value:
        projected_section_refs["field_registry_version"] = field_registry_version
    return projected_section_refs


def _project_v2_scenario(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = {
        "scenario_profile_ref", "analyzer_refs", "support_status",
        "limitations", "display_name", "aim_family",
    }
    if set(value) - allowed:
        return None
    profile_ref = value.get("scenario_profile_ref")
    if profile_ref is not None and (
        not isinstance(profile_ref, str) or _safe_scalar(profile_ref) is _MISSING
    ):
        return None
    analyzer_refs = _safe_string_list(value.get("analyzer_refs"))
    if len(analyzer_refs) > 16:
        return None
    status = value.get("support_status")
    if status not in {"supported", "partial", "outcome_only", "unsupported", "unavailable"}:
        return None
    limitations = _safe_string_list(value.get("limitations"))
    if len(limitations) > 8:
        return None
    display_name = value.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        display_name = None
    aim_family = value.get("aim_family")
    if aim_family is not None and not isinstance(aim_family, str):
        aim_family = None
    return {
        "scenario_profile_ref": profile_ref,
        "analyzer_refs": analyzer_refs,
        "support_status": status,
        "limitations": limitations,
        "display_name": display_name,
        "aim_family": aim_family,
    }


def _project_v2_evidence_summary(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = {
        "availability",
        "alignment",
        "coverage",
        "confidence",
        "artifact_ref",
        "evidence_revision",
        "segment_refs",
    }
    if set(value) - allowed:
        return None
    availability: dict[str, object] = {}
    for key, raw in _mapping(value.get("availability")).items():
        if _is_forbidden_key(key):
            continue
        safe = _safe_scalar(raw)
        if safe is not _MISSING:
            availability[str(key)] = safe
    alignment: dict[str, object] = {}
    for key in ("status", "coverage_ratio"):
        if key in _mapping(value.get("alignment")):
            safe = _safe_scalar(_mapping(value.get("alignment")).get(key))
            if safe is not _MISSING:
                alignment[key] = safe
    projected: dict[str, object] = {"availability": availability, "alignment": alignment}
    for key in ("coverage", "confidence"):
        if key in value:
            safe = _safe_scalar(value.get(key))
            if safe is not _MISSING:
                projected[key] = safe
    for key in ("artifact_ref", "evidence_revision"):
        if key in value:
            safe = _safe_scalar(value.get(key))
            if safe is not _MISSING:
                projected[key] = safe
    segment_refs = _safe_string_list(value.get("segment_refs"))
    if len(segment_refs) > 24:
        return None
    projected["segment_refs"] = segment_refs
    return projected


def _project_v2_context(context: Mapping[str, Any]) -> dict[str, object] | None:
    if set(context) != _COACH_CONTEXT_V2_TOP_LEVEL_FIELDS:
        return None
    if context.get("schema_version") != COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION:
        return None
    analysis_ref = _validated_analysis_ref(context.get("analysis_ref"))
    if analysis_ref is None or analysis_ref["analysis_result_version"] != "analysis_result.v2":
        return None
    scenario = _project_v2_scenario(context.get("scenario"))
    run_facts = _project_v2_run_facts(context.get("run_facts"))
    evidence = _project_v2_evidence_summary(context.get("evidence_summary"))
    if scenario is None or run_facts is None or evidence is None:
        return None
    raw_diagnosis = _mapping(context.get("diagnosis"))
    for raw_issue in raw_diagnosis.get("issues") or []:
        if not isinstance(raw_issue, Mapping):
            continue
        raw_supporting = raw_issue.get("supporting_evidence_segment_refs")
        if isinstance(raw_supporting, list) and len(raw_supporting) > 2:
            return None
    diagnosis = _project_diagnosis(
        context.get("diagnosis"),
        require_deterministic_metrics=True,
        inherited_limitations=_safe_string_list(scenario.get("limitations")),
    )
    segment_refs = list(evidence.get("segment_refs") or [])
    for issue in diagnosis["issues"]:
        primary = issue.get("primary_evidence_segment_ref")
        supporting = issue.get("supporting_evidence_segment_refs") or []
        for ref in [primary, *supporting]:
            if isinstance(ref, str) and ref not in segment_refs:
                segment_refs.append(ref)
    if len(segment_refs) > 24:
        return None
    evidence["segment_refs"] = segment_refs
    if len(diagnosis["summary"]) > 24 or len(diagnosis["issues"]) > 6:
        return None
    trends = context.get("trends")
    if not isinstance(trends, list) or len(trends) > 4:
        return None
    if not all(isinstance(item, Mapping) for item in trends):
        return None
    projected_trends = [dict(item) for item in trends]
    training = context.get("training")
    if not isinstance(training, Mapping) or set(training) != {"active_plan_ref", "recent_retest_ref"}:
        return None
    projected_training: dict[str, object] = {}
    for key in ("active_plan_ref", "recent_retest_ref"):
        ref = training.get(key)
        if ref is not None and (not isinstance(ref, str) or _safe_scalar(ref) is _MISSING):
            return None
        projected_training[key] = ref
    limitations = _safe_string_list(context.get("limitations"))
    if len(limitations) > 8:
        return None
    projected = {
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "scenario": scenario,
        "run_facts": run_facts,
        "diagnosis": diagnosis,
        "evidence_summary": evidence,
        "trends": projected_trends,
        "training": projected_training,
        "limitations": limitations,
    }
    if len(_canonical_json_bytes(projected)) > _COACH_CONTEXT_MAX_BYTES:
        return None
    return projected


def _project_v3_processed_events(
    value: object,
    *,
    analysis_id: str,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "mode", "tables", "query_capabilities", "limitations",
    }:
        return None
    if value.get("mode") != "table_refs":
        return None
    tables = value.get("tables")
    if not isinstance(tables, list) or not 1 <= len(tables) <= 8:
        return None
    from kovaak_tracker.analysis_evidence import validate_processed_event_table_v1

    projected_tables: list[dict] = []
    refs: set[str] = set()
    try:
        for table in tables:
            validated = validate_processed_event_table_v1(table)
            if validated["analysis_ref"] != analysis_id or validated["table_ref"] in refs:
                return None
            refs.add(validated["table_ref"])
            projected_tables.append(validated)
    except (TypeError, ValueError):
        return None
    capabilities = _safe_string_list(value.get("query_capabilities"))
    if capabilities != _PROCESSED_EVENT_QUERY_CAPABILITIES:
        return None
    limitations = _safe_string_list(value.get("limitations"))
    if len(limitations) > 8:
        return None
    return {
        "mode": "table_refs",
        "tables": projected_tables,
        "query_capabilities": list(_PROCESSED_EVENT_QUERY_CAPABILITIES),
        "limitations": limitations,
    }


def _without_inline_processed_event_refs(
    diagnosis: Mapping[str, Any],
) -> dict[str, object]:
    projected = dict(diagnosis)
    projected["summary"] = {
        metric_key: {
            field: field_value
            for field, field_value in metric.items()
            if field not in {"sample_refs", "outlier_refs"}
        }
        for metric_key, metric in _mapping(projected.get("summary")).items()
        if isinstance(metric, Mapping)
    }
    return projected


def _project_v3_context(context: Mapping[str, Any]) -> dict[str, object] | None:
    if set(context) != _COACH_CONTEXT_V3_TOP_LEVEL_FIELDS:
        return None
    if context.get("schema_version") != COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION:
        return None
    v2_input = {
        key: value
        for key, value in context.items()
        if key != "processed_events"
    }
    v2_input["schema_version"] = COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION
    projected_v2 = _project_v2_context(v2_input)
    if projected_v2 is None:
        return None
    projected_v2 = {
        **projected_v2,
        "diagnosis": _without_inline_processed_event_refs(
            _mapping(projected_v2.get("diagnosis"))
        ),
    }
    analysis_id = projected_v2["analysis_ref"]["analysis_id"]
    processed = _project_v3_processed_events(
        context.get("processed_events"),
        analysis_id=analysis_id,
    )
    if processed is None:
        return None
    projected = {
        **projected_v2,
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
        "processed_events": processed,
    }
    if len(_canonical_json_bytes(projected)) > _COACH_CONTEXT_MAX_BYTES:
        return None
    return projected


def _validated_analysis_ref(
    value: object,
    *,
    allow_extra: bool = False,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    keys = set(value)
    if not _ANALYSIS_REF_FIELDS.issubset(keys):
        return None
    if not allow_extra and keys != _ANALYSIS_REF_FIELDS:
        return None

    version = value.get("analysis_result_version")
    analysis_id = value.get("analysis_id")
    analysis_type = value.get("analysis_type")
    input_mode = value.get("input_mode")
    stable_id = isinstance(analysis_id, str) and bool(
        _ANALYSIS_ID_PATTERN.fullmatch(analysis_id)
    )
    safe_analysis_type = (
        isinstance(analysis_type, str)
        and bool(analysis_type.strip())
        and _safe_scalar(analysis_type) is not _MISSING
    )

    if version == "analysis_result.v2":
        if not stable_id:
            return None
        if not safe_analysis_type:
            return None
        if input_mode not in _V2_INPUT_MODES:
            return None
    elif version == "analysis_result.v1":
        if analysis_id is not None and not stable_id:
            return None
        if analysis_type is not None and not safe_analysis_type:
            return None
        if input_mode != "unknown":
            return None
    elif version == "unavailable":
        if (
            analysis_id is not None
            or analysis_type is not None
            or input_mode is not None
        ):
            return None
    else:
        return None

    return {
        "analysis_id": analysis_id,
        "analysis_result_version": version,
        "analysis_type": analysis_type,
        "input_mode": input_mode,
    }


def _project_analysis_ref(
    result: Mapping[str, Any],
    schema_version: str,
) -> dict[str, object]:
    if schema_version == "analysis_result.v2":
        candidate = {
            "analysis_id": result.get("analysis_id"),
            "analysis_result_version": schema_version,
            "analysis_type": result.get("analysis_type"),
            "input_mode": result.get("input_mode"),
        }
    else:
        candidate = {
            "analysis_id": result.get("analysis_id"),
            "analysis_result_version": schema_version,
            "analysis_type": result.get("summary_type"),
            "input_mode": "unknown",
        }
    validated = _validated_analysis_ref(candidate)
    if validated is None:
        raise ValueError("analysis result contains an invalid Coach analysis_ref")
    return validated


def project_coach_diagnostic_context(
    analysis_result: Mapping[str, Any],
) -> dict[str, object]:
    """Project an analysis_result.v1/v2 through the Coach's strict allow-list."""
    if not isinstance(analysis_result, Mapping):
        raise TypeError("analysis_result must be a mapping")
    schema_version = analysis_result.get("schema_version")
    if schema_version not in {"analysis_result.v1", "analysis_result.v2"}:
        raise ValueError(f"unsupported analysis result schema: {schema_version!r}")

    deterministic = _mapping(analysis_result.get("deterministic"))
    evidence = _mapping(analysis_result.get("evidence"))
    input_snapshot = _mapping(analysis_result.get("input_snapshot"))
    scenario_resolution = _mapping(input_snapshot.get("scenario_resolution"))
    scenario_profile_ref = scenario_resolution.get("scenario_profile_ref")
    has_verified_scenario = (
        isinstance(scenario_profile_ref, str) and bool(scenario_profile_ref.strip())
    )
    scenario_data = _mapping(analysis_result.get("scenario"))
    scenario_limitations = _safe_string_list(scenario_data.get("limitations"))
    if not scenario_limitations:
        scenario_limitations = _safe_string_list(deterministic.get("limitations"))
    if not scenario_limitations:
        scenario_limitations = _safe_string_list(scenario_resolution.get("limitations"))
    if schema_version == "analysis_result.v2" and isinstance(
        evidence.get("derived_artifact"), Mapping
    ):
        legacy = {
            "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
            "analysis_ref": _project_analysis_ref(analysis_result, schema_version),
            "diagnosis": _project_diagnosis(
                deterministic.get("diagnosis"),
                fallback_summary=deterministic.get("metrics"),
                require_deterministic_metrics=True,
                attach_registry_teaching=has_verified_scenario,
                include_specific_scenarios=has_verified_scenario,
                inherited_limitations=scenario_limitations,
            ),
            "evidence_summary": _project_evidence_summary(analysis_result, schema_version),
            "warnings": _project_warnings(
                analysis_result.get("warnings"), evidence.get("warnings")
            ),
        }
        facts = deterministic.get("canonical_run_facts")
        if not isinstance(facts, Mapping):
            facts = analysis_result.get("canonical_run_facts")
        if isinstance(facts, Mapping):
            run_facts = {
                "mode": "inline",
                "field_registry_version": "source_field_registry.v1",
                "facts": facts,
                "limitations": [],
            }
        else:
            run_facts = {
                "mode": "unavailable",
                "limitations": ["canonical_run_facts_not_inline_available"],
            }
        artifact = _mapping(evidence.get("derived_artifact"))
        if analysis_result.get("analysis_type") == "dynamic_clicking":
            snapshot = _mapping(analysis_result.get("input_snapshot"))
            resolution = _mapping(snapshot.get("scenario_resolution"))
            scenario_data = {
                "scenario_profile_ref": resolution.get("scenario_profile_ref"),
                "analyzer_refs": [analysis_result.get("analysis_version")],
                "support_status": deterministic.get("support_status", "unavailable"),
                "limitations": deterministic.get("limitations") or [],
            }
        scenario_ref = scenario_data.get("scenario_profile_ref")
        scenario = {
            "scenario_profile_ref": scenario_ref if isinstance(scenario_ref, str) else None,
            "analyzer_refs": _safe_string_list(scenario_data.get("analyzer_refs")),
            "support_status": (
                scenario_data.get("support_status")
                if scenario_data.get("support_status") in {
                    "supported", "partial", "outcome_only", "unsupported", "unavailable",
                }
                else "supported" if scenario_data else "unavailable"
            ),
            "limitations": _safe_string_list(scenario_data.get("limitations"))[:8],
            "display_name": scenario_resolution.get("display_name"),
            "aim_family": scenario_resolution.get("aim_family"),
        }
        evidence_summary = dict(legacy["evidence_summary"])
        evidence_summary["segment_refs"] = []
        artifact_ref = _safe_scalar(artifact.get("artifact_ref"))
        revision = _safe_scalar(artifact.get("evidence_revision"))
        if artifact_ref is not _MISSING:
            evidence_summary["artifact_ref"] = artifact_ref
        if revision is not _MISSING:
            evidence_summary["evidence_revision"] = revision
        v2 = {
            "schema_version": COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
            "analysis_ref": legacy["analysis_ref"],
            "scenario": scenario,
            "run_facts": run_facts,
            "diagnosis": legacy["diagnosis"],
            "evidence_summary": evidence_summary,
            "trends": [],
            "training": {"active_plan_ref": None, "recent_retest_ref": None},
            "limitations": [],
        }
        processed_tables = evidence.get("processed_event_tables")
        if isinstance(processed_tables, list) and processed_tables:
            queryable_diagnosis = _without_inline_processed_event_refs(
                _mapping(v2["diagnosis"])
            )
            v3 = {
                **v2,
                "schema_version": COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
                "diagnosis": queryable_diagnosis,
                "processed_events": {
                    "mode": "table_refs",
                    "tables": processed_tables,
                    "query_capabilities": list(_PROCESSED_EVENT_QUERY_CAPABILITIES),
                    "limitations": [],
                },
            }
            projected_v3 = _project_v3_context(v3)
            if projected_v3 is not None:
                return projected_v3
            raise ValueError("processed event context cannot be projected safely")
        projected_v2 = _project_v2_context(v2)
        if projected_v2 is not None:
            return projected_v2
    return {
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        "analysis_ref": _project_analysis_ref(analysis_result, schema_version),
        "diagnosis": _project_diagnosis(
            deterministic.get("diagnosis"),
            fallback_summary=deterministic.get("metrics"),
            require_deterministic_metrics=schema_version == "analysis_result.v2",
            attach_registry_teaching=(
                schema_version == "analysis_result.v1" or has_verified_scenario
            ),
            include_specific_scenarios=(
                schema_version == "analysis_result.v1" or has_verified_scenario
            ),
            inherited_limitations=scenario_limitations,
        ),
        "evidence_summary": _project_evidence_summary(analysis_result, schema_version),
        "warnings": _project_warnings(
            analysis_result.get("warnings"),
            evidence.get("warnings"),
        ),
    }


def _project_existing_context(
    context: Mapping[str, Any],
) -> dict[str, object] | None:
    projected_ref = _validated_analysis_ref(
        context.get("analysis_ref"),
        allow_extra=True,
    )
    if projected_ref is None:
        return None

    evidence = _mapping(context.get("evidence_summary"))
    availability: dict[str, object] = {}
    for key, raw_value in _mapping(evidence.get("availability")).items():
        if _is_forbidden_key(key):
            continue
        value = _safe_scalar(raw_value)
        if value is not _MISSING:
            availability[str(key)] = value
    raw_alignment = _mapping(evidence.get("alignment"))
    alignment: dict[str, object] = {}
    for key in ("status", "coverage_ratio"):
        if key not in raw_alignment:
            continue
        value = _safe_scalar(raw_alignment.get(key))
        if value is not _MISSING:
            alignment[key] = value
    projected_evidence: dict[str, object] = {
        "availability": availability,
        "alignment": alignment,
    }
    if "coverage" in evidence:
        coverage = _safe_scalar(evidence.get("coverage"))
        if coverage is not _MISSING:
            projected_evidence["coverage"] = coverage

    return {
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        "analysis_ref": projected_ref,
        "diagnosis": _project_diagnosis(
            context.get("diagnosis"),
            require_deterministic_metrics=(
                projected_ref["analysis_result_version"] == "analysis_result.v2"
            ),
        ),
        "evidence_summary": projected_evidence,
        "warnings": _project_warnings(context.get("warnings")),
    }


def context_from_legacy_diagnosis(diagnosis: object) -> dict[str, object]:
    """Compatibility bridge for callers that have only the pre-Task-5 diagnosis object."""
    data = asdict(diagnosis) if is_dataclass(diagnosis) else diagnosis
    projected = _project_diagnosis(
        data,
        require_deterministic_metrics=False,
    )
    return {
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        "analysis_ref": {
            "analysis_id": None,
            "analysis_result_version": "unavailable",
            "analysis_type": None,
            "input_mode": None,
        },
        "diagnosis": projected,
        "evidence_summary": {"availability": {}, "alignment": {}},
        "warnings": [],
    }


def coerce_coach_diagnostic_context(value: object) -> dict[str, object] | None:
    """Return canonical context for existing context, result, or legacy diagnosis input."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        schema_version = value.get("schema_version")
        if schema_version == COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION:
            return _project_v3_context(value)
        if schema_version == COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION:
            return _project_v2_context(value)
        if schema_version == COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION:
            return _project_existing_context(value)
        if schema_version in {"analysis_result.v1", "analysis_result.v2"}:
            return project_coach_diagnostic_context(value)
        if schema_version is not None:
            return None
        return context_from_legacy_diagnosis(value)
    if is_dataclass(value):
        return context_from_legacy_diagnosis(value)
    return None


def serialize_coach_diagnostic_context(context: Mapping[str, Any] | None) -> str | None:
    """Use one exact JSON serialization for Pi's string-only runtime contract."""
    if context is None:
        return None
    try:
        wire = _canonical_json_bytes(context)
    except (TypeError, ValueError):
        return None
    if (
        context.get("schema_version") in {
            COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
            COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
        }
        and len(wire) > _COACH_CONTEXT_MAX_BYTES
    ):
        return None
    return wire.decode("utf-8")


def diagnostic_context_to_coach_diagnosis(context: Mapping[str, Any] | None):
    """Adapt canonical context to the legacy Python agent API without rereading analysis."""
    from kovaak_tracker.coach.diagnosis import (
        CoachDiagnosis,
        DiagnosisIssue,
        Prescription,
        ProfileMatch,
        RootCause,
    )

    diagnosis = _mapping(_mapping(context).get("diagnosis"))
    profile_data = _mapping(diagnosis.get("profile"))
    profile = ProfileMatch(
        archetype_id=str(profile_data.get("archetype_id") or "unclassified"),
        label=str(profile_data.get("label") or "未分类"),
        confidence=float(profile_data.get("confidence") or 0.0),
        secondary_tags=_safe_string_list(profile_data.get("secondary_tags")),
    )
    issues = []
    for issue_data in diagnosis.get("issues") or []:
        if not isinstance(issue_data, Mapping):
            continue
        root_causes = [
            RootCause(level=str(item.get("level") or "symptom"), text=str(item.get("text") or ""))
            for item in issue_data.get("root_causes") or []
            if isinstance(item, Mapping)
        ]
        prescriptions = [
            Prescription(
                scenario=str(item.get("scenario") or ""),
                reason=str(item.get("reason") or ""),
                cue=str(item.get("cue") or ""),
                purpose=str(item.get("purpose") or ""),
                target_metrics=_safe_string_list(item.get("target_metrics")),
                expected_direction=_safe_string_list(item.get("expected_direction")),
                retest_after=str(item.get("retest_after") or ""),
                stop_or_adjust_rule=str(item.get("stop_or_adjust_rule") or ""),
                source_level=str(item.get("source_level") or "experimental"),
            )
            for item in issue_data.get("prescriptions") or []
            if isinstance(item, Mapping)
        ]
        issues.append(
            DiagnosisIssue(
                signal=str(issue_data.get("signal") or ""),
                severity=str(issue_data.get("severity") or "info"),
                root_causes=root_causes,
                prescriptions=prescriptions,
                priority=int(issue_data.get("priority") or 99),
                priority_reason=str(issue_data.get("priority_reason") or ""),
                plain_language_meaning=str(issue_data.get("plain_language_meaning") or ""),
                claim_level=str(issue_data.get("claim_level") or "experimental"),
                metric_refs=_safe_string_list(issue_data.get("metric_refs")),
                event_refs=_safe_string_list(issue_data.get("event_refs")),
                limitations=_safe_string_list(issue_data.get("limitations")),
                expected_result=str(issue_data.get("expected_result") or ""),
                verification=dict(_mapping(issue_data.get("verification"))),
                primary_evidence_segment_ref=(
                    str(issue_data["primary_evidence_segment_ref"])
                    if issue_data.get("primary_evidence_segment_ref") is not None
                    else None
                ),
                supporting_evidence_segment_refs=_safe_string_list(
                    issue_data.get("supporting_evidence_segment_refs")
                ),
            )
        )
    return CoachDiagnosis(
        profile=profile,
        issues=issues,
        summary=dict(_mapping(diagnosis.get("summary"))),
        comparison=diagnosis.get("comparison"),
        meta=dict(_mapping(diagnosis.get("meta"))),
    )
