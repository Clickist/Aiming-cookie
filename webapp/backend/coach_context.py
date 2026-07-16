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
    {"measured", "deterministic_rule", "research_supported", "community_consensus", "experimental"}
)
_SOURCE_LEVELS = frozenset(
    {"product_contract", "academic_peer_reviewed", "community_consensus", "personal_experience_unverified", "experimental"}
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


def _project_issue(issue: object) -> dict[str, object] | None:
    if not isinstance(issue, Mapping):
        return None
    out: dict[str, object] = {}
    for key in ("signal", "severity", "priority", "priority_reason"):
        if key not in issue or _is_forbidden_key(key):
            continue
        value = _safe_scalar(issue[key])
        if value is not _MISSING:
            out[key] = value

    for key in ("plain_language_meaning", "expected_result"):
        if key not in issue:
            continue
        value = _safe_scalar(issue.get(key))
        if value is not _MISSING:
            out[key] = value

    claim_level = issue.get("claim_level")
    out["claim_level"] = (
        claim_level
        if isinstance(claim_level, str) and claim_level in _CLAIM_LEVELS
        else "experimental"
    )

    for key in ("metric_refs", "event_refs", "limitations"):
        values = _safe_string_list(issue.get(key))
        if values:
            out[key] = values

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
            "retest_after",
            "stop_or_adjust_rule",
        ):
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
        if (projected := _project_issue(issue)) is not None
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
    return {
        "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        "analysis_ref": _project_analysis_ref(analysis_result, schema_version),
        "diagnosis": _project_diagnosis(
            deterministic.get("diagnosis"),
            fallback_summary=deterministic.get("metrics"),
            require_deterministic_metrics=schema_version == "analysis_result.v2",
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
    return json.dumps(context, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


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
            )
        )
    return CoachDiagnosis(
        profile=profile,
        issues=issues,
        summary=dict(_mapping(diagnosis.get("summary"))),
        comparison=diagnosis.get("comparison"),
        meta=dict(_mapping(diagnosis.get("meta"))),
    )
