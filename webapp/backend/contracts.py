from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from kovaak_tracker.metric_definitions import get_metric_definition

from .source_requirements import validate_source_requirements

ANALYSIS_RESULT_SCHEMA_VERSION = "analysis_result.v1"
ANALYSIS_RESULT_V2_SCHEMA_VERSION = "analysis_result.v2"
ANALYSIS_VERSION = "flicking_fair_summary.v1"
NATIVE_ANALYSIS_VERSION = "native_flicking.v1"
DYNAMIC_CLICKING_ANALYSIS_VERSION = "dynamic_clicking.v1"
CONTINUOUS_TRACKING_ANALYSIS_VERSION = "continuous_tracking.v1"
TARGET_SWITCHING_ANALYSIS_VERSION = "target_switching.v1"
SCENARIO_OUTCOME_ONLY_VERSION = "scenario_outcome_only.v1"
LEGACY_ANALYSIS_VERSION = "legacy_unversioned"
SUMMARY_TYPE = "flicking"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "artifact_manifest.v1"
ARTIFACT_MANIFEST_V2_SCHEMA_VERSION = "artifact_manifest.v2"
ERROR_SCHEMA_VERSION = "error.v1"

_LEGACY_SAFE_ERROR_MESSAGE = "分析失败，请重试；若持续失败请联系维护者。"

_NARRATION_STATUSES = frozenset({"available", "unavailable", "not_requested"})
_INPUT_MODES_V2 = frozenset({"input_native", "multimodal", "video_fallback"})
_ARTIFACT_AVAILABILITIES_V2 = frozenset(
    {"available", "missing", "unsupported", "unavailable", "invalid"}
)
_ARTIFACT_OWNERS_V2 = frozenset({"analysis", "kovaak_run", "user_source"})
_METRIC_PROVENANCE_KINDS_V2 = frozenset({"measured", "derived", "fused", "inferred"})
_EVIDENCE_V2_FIELDS = frozenset(
    {"sources", "provenance", "availability", "alignment", "warnings"}
)
_PUBLIC_EVIDENCE_SEGMENT_FIELDS = (
    "segment_id", "analysis_ref", "analyzer_ref", "segment_kind", "start_ms", "end_ms",
    "focus_start_ms", "focus_end_ms", "title_key", "rank_reason", "issue_refs",
    "metric_refs", "event_refs", "available_channels", "source_coverage", "confidence",
    "limitations",
)
_ANALYSIS_EVIDENCE_REF_FIELDS = frozenset(
    {
        "artifact_ref",
        "evidence_revision",
        "contract_version",
        "checksum_sha256",
        "size_bytes",
    }
)
_SCENARIO_RESOLUTION_FIELDS = frozenset(
    {
        "schema_version",
        "scenario_hash",
        "display_name",
        "registry_version",
        "manifest_version",
        "scenario_profile_ref",
        "classification_source",
        "classification_confidence",
        "profile_status",
        "reviewed_at",
        "source_refs",
        "supersedes",
        "manifest_status",
        "fixture_ref",
        "review_source_ref",
        "manifest_reviewed_at",
        "family_gate_refs",
        "aim_family",
        "subdomains",
        "target_motion",
        "allowed_analyzers",
        "allowed_metric_families",
        "claim_ceiling",
        "family_analyzer_dispatch",
        "limitations",
    }
)
_SCENARIO_HASH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SCENARIO_PROFILE_REF_RE = re.compile(
    r"^scenario:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+@[1-9][0-9]*$"
)
_VERSIONED_ANALYZER_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$"
)

_ERROR_CATEGORIES = frozenset(
    {
        "input_validation",
        "local_cv_runtime",
        "llm_provider",
        "network_cloud",
        "storage_disk",
        "internal_unknown",
    }
)


class UnsupportedContractVersion(ValueError):
    """Raised when a stored contract has an unrecognized schema_version."""


class ContractSerializationError(TypeError):
    """Raised when a value cannot be serialized as strict JSON after normalization."""


def _finite_original(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "+infinity" if value > 0 else "-infinity"
    raise ValueError("not a non-finite float")


def _json_path_segment(key: str | int) -> str:
    if isinstance(key, int):
        return f"[{key}]"
    return f".{key}" if key else ""


def normalize_json_value(
    value: object, *, path: str = "$"
) -> tuple[object, list[dict]]:
    issues: list[dict] = []

    if isinstance(value, float):
        if not math.isfinite(value):
            issues.append(
                {
                    "path": path,
                    "code": "non_finite_number",
                    "original": _finite_original(value),
                }
            )
            return None, issues
        return value, issues

    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, child in value.items():
            child_path = f"{path}{_json_path_segment(key)}"
            normalized_child, child_issues = normalize_json_value(child, path=child_path)
            out[str(key)] = normalized_child
            issues.extend(child_issues)
        return out, issues

    if isinstance(value, (list, tuple)):
        out_list: list[object] = []
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            normalized_child, child_issues = normalize_json_value(child, path=child_path)
            out_list.append(normalized_child)
            issues.extend(child_issues)
        return out_list, issues

    if value is None or isinstance(value, (bool, int, str)):
        return value, issues

    return value, issues


def _empty_artifact_manifest() -> dict:
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "inputs": [],
        "outputs": [],
    }


def _narration_block(*, status: str, text: str | None) -> dict:
    if status not in _NARRATION_STATUSES:
        raise ValueError(f"invalid narration status: {status}")
    if status == "available":
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("narration status available requires non-empty text")
    else:
        if text is not None:
            raise ValueError("narration text must be null unless status is available")
    return {
        "status": status,
        "text": text if status == "available" else None,
        "provider": None,
        "model": None,
        "usage": None,
    }


def _resolve_narration_from_build(
    *, narration_status: str, report_narration: object
) -> dict:
    if narration_status == "available":
        if not isinstance(report_narration, str) or not report_narration.strip():
            raise ValueError("narration status available requires non-empty text")
        return _narration_block(status="available", text=report_narration)
    return _narration_block(status=narration_status, text=None)


def _legacy_narration_from_report(narration: object) -> dict:
    if isinstance(narration, str) and narration.strip():
        return _narration_block(status="available", text=narration)
    return _narration_block(status="not_requested", text=None)


def build_artifact_manifest_v1(
    *,
    video_path: str | None,
    csv_path: str | None,
    created_at: str | None,
) -> dict:
    inputs: list[dict] = []

    def _add_input(
        *,
        path: str | None,
        entry_id: str,
        kind: str,
        media_type: str,
    ) -> None:
        if not path or not str(path).strip():
            return
        p = str(path)
        if os.path.isfile(p) and os.access(p, os.R_OK):
            status = "available"
            size_bytes = os.path.getsize(p)
        else:
            status = "missing"
            size_bytes = None
        inputs.append(
            {
                "id": entry_id,
                "kind": kind,
                "media_type": media_type,
                "size_bytes": size_bytes,
                "checksum_sha256": None,
                "status": status,
                "created_at": created_at,
            }
        )

    _add_input(
        path=video_path,
        entry_id="input-video",
        kind="input_video",
        media_type="video/mp4",
    )
    _add_input(
        path=csv_path,
        entry_id="input-stats-csv",
        kind="input_stats_csv",
        media_type="text/csv",
    )

    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "inputs": inputs,
        "outputs": [],
    }


def build_analysis_result_v1(
    *,
    report: dict,
    timeline: list[dict],
    narration_status: str,
    cm_per_360: float | None,
    fov: float | None,
    artifact_manifest: dict,
    created_at: str,
    completed_at: str,
) -> dict:
    narration = _resolve_narration_from_build(
        narration_status=narration_status,
        report_narration=report.get("narration"),
    )
    raw: dict[str, object] = {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "summary_type": SUMMARY_TYPE,
        "created_at": created_at,
        "completed_at": completed_at,
        "input": {
            "cm_per_360": cm_per_360,
            "fov": fov,
        },
        "deterministic": {
            "diagnosis": report.get("diagnosis", {}),
            "figures": report.get("figures", {}),
            "timeline": timeline,
        },
        "narration": narration,
        "artifact_manifest": artifact_manifest,
        "notes": list(report.get("notes") or []),
        "normalization_issues": [],
    }
    normalized, issues = normalize_json_value(raw)
    if not isinstance(normalized, dict):
        raise ContractSerializationError("analysis result normalization failed")
    result = dict(normalized)
    result["normalization_issues"] = issues
    return result


def _validate_analysis_result_v1(result: dict) -> dict:
    if result.get("schema_version") != ANALYSIS_RESULT_SCHEMA_VERSION:
        raise UnsupportedContractVersion(result.get("schema_version"))

    narration = result.get("narration") or {}
    status = narration.get("status")
    text = narration.get("text")
    _narration_block(status=status, text=text)

    normalized, issues = normalize_json_value(result)
    if not isinstance(normalized, dict):
        raise ContractSerializationError("analysis result normalization failed")
    out = dict(normalized)
    existing = list(out.get("normalization_issues") or [])
    out["normalization_issues"] = existing + issues
    return out


def _wrap_legacy_report(
    stored_result: dict,
    *,
    cm_per_360: float | None,
    fov: float | None,
    created_at: str | None,
    updated_at: str | None,
) -> dict:
    raw: dict[str, object] = {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "analysis_version": LEGACY_ANALYSIS_VERSION,
        "summary_type": SUMMARY_TYPE,
        "created_at": created_at,
        "completed_at": updated_at,
        "input": {
            "cm_per_360": cm_per_360,
            "fov": fov,
        },
        "deterministic": {
            "diagnosis": stored_result.get("diagnosis", {}),
            "figures": stored_result.get("figures", {}),
            "timeline": list(stored_result.get("timeline") or []),
        },
        "narration": _legacy_narration_from_report(stored_result.get("narration")),
        "artifact_manifest": _empty_artifact_manifest(),
        "notes": list(stored_result.get("notes") or []),
        "normalization_issues": [],
    }
    normalized, issues = normalize_json_value(raw)
    if not isinstance(normalized, dict):
        raise ContractSerializationError("legacy wrap normalization failed")
    out = dict(normalized)
    out["normalization_issues"] = issues
    return out


def _is_legacy_report_shape(stored_result: dict) -> bool:
    if "schema_version" in stored_result:
        return False
    return any(
        key in stored_result
        for key in ("diagnosis", "figures", "narration", "notes", "timeline")
    )


def coerce_analysis_result_v1(
    stored_result: dict | None,
    *,
    cm_per_360: float | None = None,
    fov: float | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict | None:
    if stored_result is None:
        return None
    if not isinstance(stored_result, dict):
        raise TypeError("stored_result must be a dict or None")

    schema_version = stored_result.get("schema_version")
    if schema_version == ANALYSIS_RESULT_SCHEMA_VERSION:
        return _validate_analysis_result_v1(stored_result)
    if schema_version is not None:
        raise UnsupportedContractVersion(schema_version)
    if _is_legacy_report_shape(stored_result):
        return _wrap_legacy_report(
            stored_result,
            cm_per_360=cm_per_360,
            fov=fov,
            created_at=created_at,
            updated_at=updated_at,
        )
    raise UnsupportedContractVersion(None)


def _is_absolute_path(value: str) -> bool:
    stripped = value.lstrip()
    return (
        stripped.casefold().startswith("file:")
        or os.path.isabs(stripped)
        or stripped.startswith("\\")
        or (
            len(stripped) >= 3
            and stripped[0].isalpha()
            and stripped[1] == ":"
            and stripped[2] in {"/", "\\"}
        )
    )


def _is_v2_path_key(key: str) -> bool:
    compact = "".join(character for character in key.casefold() if character.isalnum())
    return compact == "path" or compact.endswith("path") or compact.endswith("paths")


def _assert_v2_path_safe(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}{_json_path_segment(key_text)}"
            if _is_v2_path_key(key_text):
                raise ValueError(f"v2 contracts must not contain path keys: {child_path}")
            _assert_v2_path_safe(child, path=child_path)
        return

    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_v2_path_safe(child, path=f"{path}[{index}]")
        return

    if isinstance(value, str) and _is_absolute_path(value):
        raise ValueError(f"v2 contracts must not contain absolute paths: {path}")


def _require_nonempty_string(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _validate_stable_ref(field: str, value: object) -> str:
    ref = _require_nonempty_string(field, value)
    if "/" in ref or "\\" in ref:
        raise ValueError(f"{field} must be a stable ID, not a path")
    return ref


def build_artifact_manifest_v2(
    *,
    external_inputs: list[dict],
    owned_outputs: list[dict],
) -> dict:
    return _validate_artifact_manifest_v2(
        {
            "schema_version": ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
            "external_inputs": external_inputs,
            "owned_outputs": owned_outputs,
        }
    )


def _validate_artifact_entry_v2(field: str, index: int, value: object) -> str:
    path = f"artifact_manifest.{field}[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a dict")
    artifact_id = _validate_stable_ref(f"{path}.id", value.get("id"))
    kind = _require_nonempty_string(f"{path}.kind", value.get("kind"))
    if field == "owned_outputs" and kind == "raw_input":
        raise ValueError("raw_input artifacts are kovaak_run-owned external inputs")
    if field == "external_inputs" and kind == "analysis_result":
        raise ValueError("analysis_result artifacts must be analysis-owned outputs")

    availability = value.get("availability")
    if availability is not None and availability not in _ARTIFACT_AVAILABILITIES_V2:
        raise ValueError(f"invalid {path}.availability: {availability}")
    ownership = value.get("ownership")
    if ownership is not None:
        if ownership not in _ARTIFACT_OWNERS_V2:
            raise ValueError(f"invalid {path}.ownership: {ownership}")
        if field == "owned_outputs" and ownership != "analysis":
            raise ValueError("owned_outputs ownership must be analysis")
    for boolean_field in ("managed", "local_only"):
        if boolean_field in value and not isinstance(value[boolean_field], bool):
            raise ValueError(f"{path}.{boolean_field} must be a bool")
    return artifact_id


def _validate_artifact_manifest_v2(manifest: dict) -> dict:
    if manifest.get("schema_version") != ARTIFACT_MANIFEST_V2_SCHEMA_VERSION:
        raise UnsupportedContractVersion(manifest.get("schema_version"))
    artifact_ids: set[str] = set()
    for field in ("external_inputs", "owned_outputs"):
        entries = manifest.get(field)
        if not isinstance(entries, list):
            raise ValueError(f"artifact_manifest.{field} must be a list")
        for index, entry in enumerate(entries):
            artifact_id = _validate_artifact_entry_v2(field, index, entry)
            if artifact_id in artifact_ids:
                raise ValueError(f"duplicate artifact id: {artifact_id}")
            artifact_ids.add(artifact_id)
    _assert_v2_path_safe(manifest, path="$.artifact_manifest")
    return dict(manifest)


def _validate_evidence_v2(evidence: object) -> dict:
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be a dict")
    missing = _EVIDENCE_V2_FIELDS.difference(evidence)
    if missing:
        raise ValueError(f"evidence missing required fields: {', '.join(sorted(missing))}")
    if not isinstance(evidence.get("sources"), (dict, list)):
        raise ValueError("evidence.sources must be a dict or list")
    for field in ("provenance", "availability", "alignment"):
        if not isinstance(evidence.get(field), dict):
            raise ValueError(f"evidence.{field} must be a dict")
    if not isinstance(evidence.get("warnings"), list):
        raise ValueError("evidence.warnings must be a list")
    derived_artifact = evidence.get("derived_artifact")
    if derived_artifact is not None:
        if not isinstance(derived_artifact, dict) or set(derived_artifact) != _ANALYSIS_EVIDENCE_REF_FIELDS:
            raise ValueError("evidence.derived_artifact must be a safe artifact ref")
        for field in ("artifact_ref", "evidence_revision", "contract_version", "checksum_sha256"):
            _validate_stable_ref(f"evidence.derived_artifact.{field}", derived_artifact.get(field))
        if derived_artifact["contract_version"] not in {
            "analysis_evidence_artifact.v1",
            "analysis_evidence_artifact.v2",
        }:
            raise UnsupportedContractVersion(derived_artifact["contract_version"])
        revision = derived_artifact["evidence_revision"]
        checksum = derived_artifact["checksum_sha256"]
        if not revision.startswith("sha256:") or revision[7:] != checksum:
            raise ValueError("evidence.derived_artifact revision/checksum mismatch")
        if (
            isinstance(derived_artifact["size_bytes"], bool)
            or not isinstance(derived_artifact["size_bytes"], int)
            or derived_artifact["size_bytes"] <= 0
        ):
            raise ValueError("evidence.derived_artifact.size_bytes must be positive")
    processed_tables = evidence.get("processed_event_tables")
    if processed_tables is not None:
        if not isinstance(processed_tables, list) or not 1 <= len(processed_tables) <= 8:
            raise ValueError("evidence.processed_event_tables must be a bounded list")
        from kovaak_tracker.analysis_evidence import validate_processed_event_table_v1

        table_refs: set[str] = set()
        for table in processed_tables:
            validated = validate_processed_event_table_v1(table)
            if validated["table_ref"] in table_refs:
                raise ValueError("evidence.processed_event_tables contains duplicate refs")
            table_refs.add(validated["table_ref"])
    return evidence


def _validate_canonical_time_window(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("input_snapshot.canonical_time_window must be a dict")
    if value.get("schema_version") != "canonical_time_window.v1":
        raise UnsupportedContractVersion(value.get("schema_version"))
    start_ms = value.get("start_ms")
    end_ms = value.get("end_ms")
    duration_ms = value.get("duration_ms")
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in (start_ms, end_ms, duration_ms)
    ) or start_ms < 0 or end_ms <= start_ms or duration_ms != end_ms - start_ms:
        raise ValueError("input_snapshot.canonical_time_window has an invalid range")
    if value.get("window_semantics") != "half_open":
        raise ValueError("input_snapshot.canonical_time_window must be half_open")
    for field in ("timebase_version", "start_source", "end_source"):
        _require_nonempty_string(
            f"input_snapshot.canonical_time_window.{field}", value.get(field)
        )
    warnings = value.get("warnings")
    if not isinstance(warnings, list) or not all(
        isinstance(item, str) and item for item in warnings
    ):
        raise ValueError("input_snapshot.canonical_time_window.warnings must be a list")
    return value


def _validate_string_list(field: str, value: object, *, allow_empty: bool = True) -> list:
    if (
        not isinstance(value, list)
        or len(value) > 64
        or (not allow_empty and not value)
    ):
        raise ValueError(f"{field} must be a list")
    if not all(
        isinstance(item, str)
        and item
        and len(item) <= 500
        and not any(ord(char) < 32 for char in item)
        for item in value
    ):
        raise ValueError(f"{field} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def validate_scenario_resolution_v1(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("input_snapshot.scenario_resolution must be a dict")
    if set(value) != _SCENARIO_RESOLUTION_FIELDS:
        raise ValueError("input_snapshot.scenario_resolution fields are invalid")
    if value.get("schema_version") != "scenario_resolution.v1":
        raise UnsupportedContractVersion(value.get("schema_version"))

    scenario_hash = value.get("scenario_hash")
    if scenario_hash is not None:
        if (
            not isinstance(scenario_hash, str)
            or not _SCENARIO_HASH_RE.fullmatch(scenario_hash)
        ):
            raise ValueError("scenario_resolution.scenario_hash is invalid")
    display_name = value.get("display_name")
    if display_name is not None:
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name) > 240
            or any(ord(char) < 32 for char in display_name)
        ):
            raise ValueError("scenario_resolution.display_name is invalid")
    for field in ("registry_version", "manifest_version"):
        version = value.get(field)
        if (
            not isinstance(version, str)
            or not version.strip()
            or len(version) > 80
            or any(ord(char) < 32 for char in version)
        ):
            raise ValueError(f"scenario_resolution.{field} is invalid")
    profile_ref = value.get("scenario_profile_ref")
    if profile_ref is not None:
        if (
            not isinstance(profile_ref, str)
            or not _SCENARIO_PROFILE_REF_RE.fullmatch(profile_ref)
        ):
            raise ValueError("scenario_resolution.scenario_profile_ref is invalid")

    classification_source = value.get("classification_source")
    if classification_source not in {
        "reviewed_registry", "official_metadata", "unknown", "name_heuristic",
        "user_declaration", "local_scenario_definition", "family_default",
        "challenge_shape", "scenario_override",
    }:
        raise ValueError("scenario_resolution.classification_source is invalid")
    confidence = value.get("classification_confidence")
    if confidence not in {"confirmed", "candidate", "unknown"}:
        raise ValueError("scenario_resolution.classification_confidence is invalid")
    profile_status = value.get("profile_status")
    if profile_status not in {"active", "superseded", "retired", "unknown"}:
        raise ValueError("scenario_resolution.profile_status is invalid")
    reviewed_at = value.get("reviewed_at")
    if reviewed_at is not None:
        if (
            not isinstance(reviewed_at, str)
            or not reviewed_at.strip()
            or len(reviewed_at) > 40
        ):
            raise ValueError("scenario_resolution.reviewed_at is invalid")
    source_refs = _validate_string_list(
        "scenario_resolution.source_refs", value.get("source_refs")
    )
    supersedes = _validate_string_list(
        "scenario_resolution.supersedes", value.get("supersedes")
    )
    for index, superseded_ref in enumerate(supersedes):
        _validate_stable_ref(
            f"scenario_resolution.supersedes[{index}]", superseded_ref
        )
    manifest_status = value.get("manifest_status")
    if manifest_status not in {"active", "pending_gate", "retired", "unlisted"}:
        raise ValueError("scenario_resolution.manifest_status is invalid")
    fixture_ref = value.get("fixture_ref")
    review_source_ref = value.get("review_source_ref")
    manifest_reviewed_at = value.get("manifest_reviewed_at")
    for field, field_value in (
        ("fixture_ref", fixture_ref),
        ("review_source_ref", review_source_ref),
        ("manifest_reviewed_at", manifest_reviewed_at),
    ):
        if field_value is not None:
            if (
                not isinstance(field_value, str)
                or not field_value.strip()
                or len(field_value) > 500
            ):
                raise ValueError(f"scenario_resolution.{field} is invalid")
    family_gate_refs = _validate_string_list(
        "scenario_resolution.family_gate_refs", value.get("family_gate_refs")
    )
    aim_family = value.get("aim_family")
    if aim_family not in {
        "static_clicking",
        "dynamic_clicking",
        "continuous_tracking",
        "target_switching",
        "movement_aiming",
        "unknown",
    }:
        raise ValueError("scenario_resolution.aim_family is invalid")

    subdomains = _validate_string_list(
        "scenario_resolution.subdomains", value.get("subdomains")
    )
    if set(subdomains) - {
        "precision", "speed", "smooth", "reactive", "predictable", "control", "mixed",
    }:
        raise ValueError("scenario_resolution.subdomains is invalid")
    target_motion = value.get("target_motion")
    if not isinstance(target_motion, dict) or set(target_motion) != {
        "model", "target_count_model",
    }:
        raise ValueError("scenario_resolution.target_motion fields are invalid")
    if target_motion.get("model") not in {
        "static", "predictable", "reactive", "mixed", "unknown",
    } or target_motion.get("target_count_model") not in {
        "single", "sequential", "concurrent", "unknown",
    }:
        raise ValueError("scenario_resolution.target_motion enum is invalid")
    allowed_analyzers = _validate_string_list(
        "scenario_resolution.allowed_analyzers", value.get("allowed_analyzers")
    )
    allowed_metric_families = _validate_string_list(
        "scenario_resolution.allowed_metric_families",
        value.get("allowed_metric_families"),
    )
    claim_ceiling = value.get("claim_ceiling")
    if claim_ceiling not in {"family_specific", "descriptive_only", "outcome_only"}:
        raise ValueError("scenario_resolution.claim_ceiling is invalid")
    dispatch = value.get("family_analyzer_dispatch")
    if dispatch not in {"allowed", "none"}:
        raise ValueError("scenario_resolution.family_analyzer_dispatch is invalid")
    if not all(_VERSIONED_ANALYZER_RE.fullmatch(item) for item in allowed_analyzers):
        raise ValueError("scenario_resolution.allowed_analyzers is invalid")
    if set(allowed_metric_families) - {
        "outcome", "input_kinematics", "static_clicking", "dynamic_clicking",
        "continuous_tracking", "target_switching",
    }:
        raise ValueError("scenario_resolution.allowed_metric_families is invalid")
    limitations = _validate_string_list(
        "scenario_resolution.limitations",
        value.get("limitations"),
        allow_empty=manifest_status == "active",
    )

    if profile_ref is None:
        if reviewed_at is not None or source_refs or supersedes:
            raise ValueError("scenario_resolution profile provenance requires a profile ref")
    elif (
        scenario_hash is None
        or classification_source not in {"reviewed_registry", "official_metadata"}
        or confidence != "confirmed"
        or reviewed_at is None
        or not source_refs
        or aim_family == "unknown"
    ):
        raise ValueError("scenario_resolution profile provenance is incomplete")

    if manifest_status == "unlisted":
        if (
            fixture_ref is not None
            or review_source_ref is not None
            or manifest_reviewed_at is not None
            or family_gate_refs
        ):
            raise ValueError("scenario_resolution unlisted manifest has gate provenance")
    elif (
        scenario_hash is None
        or fixture_ref is None
        or review_source_ref is None
        or manifest_reviewed_at is None
        or not family_gate_refs
    ):
        raise ValueError("scenario_resolution listed manifest provenance is incomplete")

    if manifest_status == "active":
        if (
            scenario_hash is None
            or
            profile_ref is None
            or profile_status != "active"
            or classification_source not in {"reviewed_registry", "official_metadata"}
            or confidence != "confirmed"
            or aim_family == "unknown"
            or dispatch != "allowed"
            or not allowed_analyzers
            or claim_ceiling != "family_specific"
        ):
            raise ValueError("scenario_resolution active dispatch is inconsistent")
    elif dispatch == "allowed":
        # Baseline family dispatch: the identified family pipeline runs on
        # native facts without exact visual calibration. It is granted by a
        # verified local scenario definition, the Stats-derived challenge
        # shape, a name-only family candidate, the unresolved default, a
        # user-confirmed scenario override, or a reviewed profile whose
        # manifest gate is not active (exact-review provenance kept, visual
        # claims withheld).
        if not (
            manifest_status != "active"
            and aim_family in {
                "static_clicking", "dynamic_clicking",
                "continuous_tracking", "target_switching",
            }
            and claim_ceiling == "descriptive_only"
            and allowed_analyzers == [f"{aim_family}.baseline.v1"]
            and allowed_metric_families == ["outcome", "input_kinematics"]
            and limitations
        ):
            raise ValueError("scenario_resolution baseline dispatch is inconsistent")
        if profile_ref is None and classification_source not in {
            "local_scenario_definition", "name_heuristic", "family_default",
            "challenge_shape", "scenario_override",
        }:
            raise ValueError("scenario_resolution baseline dispatch is inconsistent")
    elif dispatch != "none":
        raise ValueError("scenario_resolution dispatch is invalid")

    if manifest_status in {"pending_gate", "retired"} and (
        profile_ref is None
        or classification_source not in {"reviewed_registry", "official_metadata"}
        or confidence != "confirmed"
        or aim_family == "unknown"
    ):
        raise ValueError("scenario_resolution listed profile is inconsistent")

    if classification_source == "unknown" and (
        profile_ref is not None
        or confidence != "unknown"
        or profile_status != "unknown"
        or aim_family != "unknown"
        or subdomains
        or target_motion != {"model": "unknown", "target_count_model": "unknown"}
        or allowed_analyzers
        or claim_ceiling != "outcome_only"
        or not limitations
    ):
        raise ValueError("scenario_resolution.scenario_profile_ref is invalid for unknown")
    return value


def _validate_analysis_result_v2(result: dict) -> dict:
    if result.get("schema_version") != ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        raise UnsupportedContractVersion(result.get("schema_version"))

    result = dict(result)
    if "analysis_version" not in result:
        result["analysis_version"] = LEGACY_ANALYSIS_VERSION
    analysis_id = _validate_stable_ref("analysis_id", result.get("analysis_id"))
    _require_nonempty_string("analysis_type", result.get("analysis_type"))
    _require_nonempty_string("analysis_version", result.get("analysis_version"))
    input_mode = result.get("input_mode")
    if input_mode not in _INPUT_MODES_V2:
        raise ValueError(f"invalid input_mode: {input_mode}")
    kovaak_run_ref = result.get("kovaak_run_ref")
    if kovaak_run_ref is not None:
        _validate_stable_ref("kovaak_run_ref", kovaak_run_ref)
    owner_id = result.get("owner_id")
    if owner_id is not None:
        _validate_stable_ref("owner_id", owner_id)
    local_profile = result.get("local_profile")
    if local_profile is not None:
        _validate_stable_ref("local_profile", local_profile)
        if owner_id is None:
            raise ValueError("local_profile requires owner_id")
    evidence = _validate_evidence_v2(result.get("evidence"))
    for table in evidence.get("processed_event_tables") or []:
        if table.get("analysis_ref") != analysis_id:
            raise ValueError("processed event table is bound to another analysis")

    if not isinstance(result.get("deterministic"), dict):
        raise ValueError("deterministic must be a dict")
    if not isinstance(result.get("input_snapshot"), dict):
        raise ValueError("input_snapshot must be a dict")
    input_snapshot = result["input_snapshot"]
    if (
        isinstance(kovaak_run_ref, str)
        and kovaak_run_ref.startswith("run:")
        and input_snapshot.get("source_requirements_version") == "automatic_quality_tier.v1"
    ):
        source_gate = validate_source_requirements(input_snapshot)
        if not source_gate["ready"] or source_gate["selected_mode"] != input_mode:
            missing = ", ".join(str(item) for item in source_gate["missing"])
            raise ValueError(f"Run input snapshot does not match its automatic tier: {missing}")
    snapshot_version = input_snapshot.get("schema_version")
    if snapshot_version in {"analysis_input_snapshot.v2", "analysis_input_snapshot.v3"}:
        scenario_resolution = input_snapshot.get("scenario_resolution")
        if snapshot_version == "analysis_input_snapshot.v3" and scenario_resolution is None:
            raise ValueError("analysis_input_snapshot.v3 requires scenario_resolution")
        if scenario_resolution is not None:
            validate_scenario_resolution_v1(scenario_resolution)
        canonical_value = input_snapshot.get("canonical_time_window")
        if canonical_value is None and input_mode in {"input_native", "multimodal"}:
            raise ValueError("native input snapshot requires a canonical time window")
        if canonical_value is not None:
            canonical_window = _validate_canonical_time_window(canonical_value)
        else:
            canonical_window = None
        if input_mode in {"input_native", "multimodal"} and canonical_window is not None:
            alignment = result["evidence"].get("alignment") or {}
            if (
                alignment.get("challenge_start_epoch_ms")
                != canonical_window["start_ms"]
                or alignment.get("challenge_end_epoch_ms")
                != canonical_window["end_ms"]
            ):
                raise ValueError("evidence alignment must match the frozen canonical window")
    if not isinstance(result.get("warnings"), list):
        raise ValueError("warnings must be a list")
    if not isinstance(result.get("errors"), list):
        raise ValueError("errors must be a list")
    if not isinstance(result.get("normalization_issues", []), list):
        raise ValueError("normalization_issues must be a list")

    artifact_manifest = result.get("artifact_manifest")
    if not isinstance(artifact_manifest, dict):
        raise ValueError("artifact_manifest must be a dict")
    validated_manifest = _validate_artifact_manifest_v2(artifact_manifest)
    manifest_analysis_id = validated_manifest.get("analysis_id")
    if manifest_analysis_id is not None:
        manifest_analysis_id = _validate_stable_ref(
            "artifact_manifest.analysis_id", manifest_analysis_id,
        )
        if manifest_analysis_id != analysis_id:
            raise ValueError("artifact_manifest.analysis_id must match analysis_id")
    result_artifacts = [
        entry
        for entry in validated_manifest["owned_outputs"]
        if entry.get("kind") == "analysis_result"
    ]
    if result_artifacts and (
        len(result_artifacts) != 1 or result_artifacts[0].get("id") != analysis_id
    ):
        raise ValueError(
            "artifact_manifest must contain one analysis_result matching analysis_id"
        )
    status = result.get("status", "done")
    if status != "done":
        raise ValueError(f"invalid analysis result status: {status}")
    _require_nonempty_string("created_at", result.get("created_at"))
    _require_nonempty_string("completed_at", result.get("completed_at"))

    result = dict(result)
    result["status"] = status
    result["artifact_manifest"] = {
        **validated_manifest,
        "analysis_id": analysis_id,
    }
    _assert_v2_path_safe(result)

    normalized, issues = normalize_json_value(result)
    if not isinstance(normalized, dict):
        raise ContractSerializationError("analysis result normalization failed")
    out = dict(normalized)
    v2_issues = [
        {
            "location": issue["path"],
            "code": issue["code"],
            "original": issue["original"],
        }
        for issue in issues
    ]
    out["normalization_issues"] = list(out.get("normalization_issues") or []) + v2_issues
    return out


def build_analysis_result_v2(
    *,
    analysis_version: str | None = None,
    analysis_id: str,
    analysis_type: str,
    input_mode: str,
    owner_id: str | None = None,
    local_profile: str | None = None,
    kovaak_run_ref: str | None,
    evidence: dict,
    deterministic: dict,
    artifact_manifest: dict,
    input_snapshot: dict,
    created_at: str,
    completed_at: str,
    warnings: list,
    errors: list,
) -> dict:
    if analysis_version is None:
        analysis_version = (
            NATIVE_ANALYSIS_VERSION
            if input_mode in {"input_native", "multimodal"}
            else ANALYSIS_VERSION
        )
    result = {
        "schema_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
        "analysis_version": analysis_version,
        "analysis_id": analysis_id,
        "analysis_type": analysis_type,
        "input_mode": input_mode,
        "evidence": evidence,
        "deterministic": deterministic,
        "artifact_manifest": artifact_manifest,
        "input_snapshot": input_snapshot,
        "created_at": created_at,
        "completed_at": completed_at,
        "warnings": warnings,
        "errors": errors,
        "normalization_issues": [],
    }
    if kovaak_run_ref is not None:
        result["kovaak_run_ref"] = kovaak_run_ref
    if owner_id is not None:
        result["owner_id"] = owner_id
    if local_profile is not None:
        result["local_profile"] = local_profile
    return _validate_analysis_result_v2(result)


def _validate_coverage_v2(field: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null")
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError(f"{field} must be between 0 and 1")


def _validate_producer_evidence_v2(evidence: dict) -> None:
    if "coverage" not in evidence:
        raise ValueError("evidence.coverage is required for persisted v2 results")
    _validate_coverage_v2("evidence.coverage", evidence.get("coverage"))
    sources = evidence["sources"]
    items = sources.items() if isinstance(sources, dict) else enumerate(sources)
    for source_key, source in items:
        field = f"evidence.sources[{source_key!r}]"
        if not isinstance(source, dict):
            raise ValueError(f"{field} must be a dict")
        source_name = _require_nonempty_string(f"{field}.source", source.get("source"))
        if isinstance(sources, dict) and source_name != source_key:
            raise ValueError(f"{field}.source must match its source key")
        _require_nonempty_string(f"{field}.role", source.get("role"))
        availability = source.get("availability")
        if availability not in _ARTIFACT_AVAILABILITIES_V2:
            raise ValueError(f"invalid {field}.availability: {availability}")
        _validate_stable_ref(f"{field}.artifact_ref", source.get("artifact_ref"))
        version = source.get("parser_or_format_version")
        if version is None or isinstance(version, bool) or (
            isinstance(version, str) and not version.strip()
        ):
            raise ValueError(f"{field}.parser_or_format_version is required")
        _require_nonempty_string(f"{field}.alignment", source.get("alignment"))
        warnings = source.get("warnings")
        if not isinstance(warnings, list):
            raise ValueError(f"{field}.warnings must be a list")


def _validate_producer_metrics_v2(deterministic: dict) -> None:
    metrics = deterministic.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("deterministic.metrics must be a dict for persisted v2 results")
    required = {
        "key", "value", "unit", "availability", "provenance",
        "metric_version", "coverage", "classification", "limitations",
    }
    for metric_key, metric in metrics.items():
        field = f"deterministic.metrics[{metric_key!r}]"
        if not isinstance(metric, dict):
            raise ValueError(f"{field} must be a dict")
        missing = required.difference(metric)
        if missing:
            raise ValueError(f"{field} missing required fields: {', '.join(sorted(missing))}")
        if metric.get("key") != metric_key:
            raise ValueError(f"{field}.key must match its metric key")
        value = metric.get("value")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(f"{field}.value must be a number or null")
        _require_nonempty_string(f"{field}.unit", metric.get("unit"))
        availability = metric.get("availability")
        if availability not in _ARTIFACT_AVAILABILITIES_V2:
            raise ValueError(f"invalid {field}.availability: {availability}")
        if availability == "available" and value is None:
            raise ValueError(f"{field}.value is required when availability is available")
        provenance = metric.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{field}.provenance must be a dict")
        kind = provenance.get("kind")
        if kind not in _METRIC_PROVENANCE_KINDS_V2:
            raise ValueError(f"invalid {field}.provenance.kind: {kind}")
        provenance_sources = provenance.get("sources")
        if not isinstance(provenance_sources, list) or not provenance_sources:
            raise ValueError(f"{field}.provenance.sources must be a non-empty list")
        for source_index, source in enumerate(provenance_sources):
            _require_nonempty_string(
                f"{field}.provenance.sources[{source_index}]", source,
            )
        _require_nonempty_string(f"{field}.metric_version", metric.get("metric_version"))
        if metric.get("classification") != "deterministic":
            raise ValueError(f"{field}.classification must be deterministic")
        _validate_coverage_v2(f"{field}.coverage", metric.get("coverage"))
        limitations = metric.get("limitations")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) and item for item in limitations
        ):
            raise ValueError(f"{field}.limitations must be a list of strings")


def _validate_producer_artifacts_v2(manifest: dict, analysis_id: str) -> None:
    all_ids = {
        entry["id"]
        for field in ("external_inputs", "owned_outputs")
        for entry in manifest[field]
    }
    required = {
        "id", "kind", "source", "availability", "ownership", "managed",
        "local_only", "status", "derived_from",
    }
    for field in ("external_inputs", "owned_outputs"):
        for index, entry in enumerate(manifest[field]):
            path = f"artifact_manifest.{field}[{index}]"
            missing = required.difference(entry)
            if missing:
                raise ValueError(f"{path} missing required fields: {', '.join(sorted(missing))}")
            _require_nonempty_string(f"{path}.source", entry.get("source"))
            if entry.get("availability") not in _ARTIFACT_AVAILABILITIES_V2:
                raise ValueError(f"invalid {path}.availability: {entry.get('availability')}")
            if entry.get("ownership") not in _ARTIFACT_OWNERS_V2:
                raise ValueError(f"invalid {path}.ownership: {entry.get('ownership')}")
            for boolean_field in ("managed", "local_only"):
                if not isinstance(entry.get(boolean_field), bool):
                    raise ValueError(f"{path}.{boolean_field} must be a bool")
            _require_nonempty_string(f"{path}.status", entry.get("status"))
            version = entry.get("parser_version", entry.get("format_version"))
            if version is None or isinstance(version, bool) or (
                isinstance(version, str) and not version.strip()
            ):
                raise ValueError(f"{path} requires parser_version or format_version")
            derived_from = entry.get("derived_from")
            if not isinstance(derived_from, list):
                raise ValueError(f"{path}.derived_from must be a list")
            for ref_index, ref in enumerate(derived_from):
                stable_ref = _validate_stable_ref(f"{path}.derived_from[{ref_index}]", ref)
                if stable_ref not in all_ids:
                    raise ValueError(f"{path}.derived_from references unknown artifact: {stable_ref}")
            if entry.get("kind") == "raw_input" and (
                entry.get("ownership") != "kovaak_run" or not entry.get("local_only")
            ):
                raise ValueError("raw_input artifacts must be kovaak_run-owned and local-only")
            if field == "owned_outputs" and entry.get("ownership") != "analysis":
                raise ValueError("owned_outputs ownership must be analysis")

    result_artifacts = [
        entry
        for entry in manifest["owned_outputs"]
        if entry.get("kind") == "analysis_result" and entry.get("id") == analysis_id
    ]
    if len(result_artifacts) != 1:
        raise ValueError(
            "artifact_manifest must contain one persisted analysis_result artifact"
        )
    result_artifact = result_artifacts[0]
    if result_artifact.get("format_version") != ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        raise ValueError("analysis_result artifact format_version must match analysis_result.v2")


def validate_analysis_result_v2_for_persistence(
    result: dict,
    *,
    owner_id: str,
    analysis_id: str,
    analysis_type: str,
    input_mode: str,
    kovaak_run_ref: str | None,
    require_local_profile: bool = False,
) -> dict:
    """Validate the complete producer envelope before a v2 result becomes terminal."""
    validated = _validate_analysis_result_v2(result)
    if validated.get("analysis_version") == LEGACY_ANALYSIS_VERSION:
        raise ValueError("persisted analysis_result.v2 requires explicit analysis_version")
    if validated.get("owner_id") != owner_id:
        raise ValueError("analysis_result.v2 owner_id must match the session owner")
    if require_local_profile and validated.get("local_profile") != owner_id:
        raise ValueError("desktop analysis_result.v2 requires matching local_profile")
    if validated.get("analysis_id") != analysis_id:
        raise ValueError("analysis_result.v2 analysis_id must match the session")
    if validated.get("analysis_type") != analysis_type:
        raise ValueError("analysis_result.v2 analysis_type must match the session request")
    if validated.get("input_mode") != input_mode:
        raise ValueError("analysis_result.v2 input_mode must match the session request")
    if validated.get("kovaak_run_ref") != kovaak_run_ref:
        raise ValueError("analysis_result.v2 kovaak_run_ref must match the session request")
    _validate_producer_evidence_v2(validated["evidence"])
    _validate_producer_metrics_v2(validated["deterministic"])
    _validate_producer_artifacts_v2(validated["artifact_manifest"], validated["analysis_id"])
    return validated


def coerce_analysis_result_v2(stored_result: dict | None) -> dict | None:
    if stored_result is None:
        return None
    if not isinstance(stored_result, dict):
        raise TypeError("stored_result must be a dict or None")
    return _validate_analysis_result_v2(stored_result)


def coerce_analysis_result(
    stored_result: dict | None,
    *,
    cm_per_360: float | None = None,
    fov: float | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict | None:
    if stored_result is None:
        return None
    if not isinstance(stored_result, dict):
        raise TypeError("stored_result must be a dict or None")

    if stored_result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        return coerce_analysis_result_v2(stored_result)
    return coerce_analysis_result_v1(
        stored_result,
        cm_per_360=cm_per_360,
        fov=fov,
        created_at=created_at,
        updated_at=updated_at,
    )


def decode_input_snapshot_json(raw_snapshot: object) -> dict | None:
    try:
        parsed = json.loads(raw_snapshot) if raw_snapshot else None
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def project_error_for_session(error: dict | None) -> dict | None:
    if not isinstance(error, dict):
        return None
    return {
        key: error.get(key)
        for key in ("schema_version", "category", "code", "message", "retryable", "trace_id")
    }


def project_evidence_segment(segment: dict) -> dict:
    return {key: segment[key] for key in _PUBLIC_EVIDENCE_SEGMENT_FIELDS if key in segment}


def dump_contract_json(value: object) -> str:
    normalized, _ = normalize_json_value(value)
    try:
        return json.dumps(normalized, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractSerializationError(str(exc)) from exc


def analysis_result_to_coach_report(result_v1: dict) -> dict:
    narration = result_v1.get("narration") or {}
    status = narration.get("status")
    narration_out: str | None
    if status == "available":
        narration_out = narration.get("text")
    else:
        narration_out = None
    deterministic = result_v1.get("deterministic") or {}
    return {
        "diagnosis": deterministic.get("diagnosis", {}),
        "figures": deterministic.get("figures", {}),
        "narration": narration_out,
        "notes": list(result_v1.get("notes") or []),
        "timeline": list(deterministic.get("timeline") or []),
    }


def build_error_v1(
    *,
    category: str,
    code: str,
    message: str,
    retryable: bool,
    trace_id: str | None,
    details: object | None = None,
) -> dict:
    if category not in _ERROR_CATEGORIES:
        raise ValueError(f"invalid error category: {category}")
    return {
        "schema_version": ERROR_SCHEMA_VERSION,
        "category": category,
        "code": code,
        "message": message,
        "retryable": retryable,
        "trace_id": trace_id,
        "details": details,
    }


def _validate_error_v1(error: dict) -> dict:
    if error.get("schema_version") != ERROR_SCHEMA_VERSION:
        raise UnsupportedContractVersion(error.get("schema_version"))
    category = error.get("category")
    if category not in _ERROR_CATEGORIES:
        raise ValueError(f"invalid error category: {category}")
    return dict(error)


def _wrap_legacy_error_string() -> dict:
    return {
        "schema_version": ERROR_SCHEMA_VERSION,
        "category": "internal_unknown",
        "code": "legacy_error",
        "message": _LEGACY_SAFE_ERROR_MESSAGE,
        "retryable": False,
        "trace_id": None,
        "details": None,
    }


def coerce_error_v1(stored_error: str | dict | None) -> dict | None:
    if stored_error is None:
        return None

    if isinstance(stored_error, dict):
        schema_version = stored_error.get("schema_version")
        if schema_version == ERROR_SCHEMA_VERSION:
            return _validate_error_v1(stored_error)
        raise UnsupportedContractVersion(schema_version)

    if not isinstance(stored_error, str):
        raise TypeError("stored_error must be str, dict, or None")

    text = stored_error.strip()
    if not text:
        return _wrap_legacy_error_string()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _wrap_legacy_error_string()

    if not isinstance(parsed, dict):
        return _wrap_legacy_error_string()

    schema_version = parsed.get("schema_version")
    if schema_version == ERROR_SCHEMA_VERSION:
        return _validate_error_v1(parsed)
    raise UnsupportedContractVersion(schema_version)


# ── Analysis result metric-definition projection ───────────────────────
#
# Migrated from the deleted coach_context module (2026-08-13 rewrite).
# Adds catalog display definitions to public result metric objects. Read-only
# projection: the stored analysis result is not mutated.

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
_NETWORK_URL_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


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


def _resolve_metric_definition(
    metric_key: str,
) -> dict[str, object] | None:
    """Return only display fields from the unified metric catalog."""
    if not metric_key:
        return None
    defn = get_metric_definition(metric_key)
    if defn is None:
        return None
    result: dict[str, object] = {}
    for field in ("name", "description"):
        value = defn.get(field)
        if isinstance(value, str) and value:
            result[field] = value
    return result or None


def _project_metric_definition_map(metrics: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for metric_key, metric in metrics.items():
        if not isinstance(metric, Mapping):
            projected[metric_key] = metric
            continue
        projected_metric = dict(metric)
        projected_metric.pop("definition", None)
        definition = _resolve_metric_definition(str(metric_key))
        if definition is not None:
            projected_metric["definition"] = definition
        projected[metric_key] = projected_metric
    return projected


def project_analysis_result_metric_definitions(
    analysis_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Add catalog display definitions to public result metric objects.

    This is a read-time projection: the stored analysis result is not
    mutated, and all fields outside the two public metric containers are
    preserved as-is.
    """
    projected = dict(analysis_result)
    deterministic = analysis_result.get("deterministic")
    if not isinstance(deterministic, Mapping):
        return projected
    projected_deterministic = dict(deterministic)
    metrics = deterministic.get("metrics")
    if isinstance(metrics, Mapping):
        projected_deterministic["metrics"] = _project_metric_definition_map(metrics)
    diagnosis = deterministic.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        projected_diagnosis = dict(diagnosis)
        summary = diagnosis.get("summary")
        if isinstance(summary, Mapping):
            projected_diagnosis["summary"] = _project_metric_definition_map(summary)
        projected_deterministic["diagnosis"] = projected_diagnosis
    projected["deterministic"] = projected_deterministic
    return projected
