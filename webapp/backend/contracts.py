from __future__ import annotations

import json
import math
import os

ANALYSIS_RESULT_SCHEMA_VERSION = "analysis_result.v1"
ANALYSIS_RESULT_V2_SCHEMA_VERSION = "analysis_result.v2"
ANALYSIS_VERSION = "flicking_fair_summary.v1"
LEGACY_ANALYSIS_VERSION = "legacy_unversioned"
SUMMARY_TYPE = "flicking"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "artifact_manifest.v1"
ARTIFACT_MANIFEST_V2_SCHEMA_VERSION = "artifact_manifest.v2"
ERROR_SCHEMA_VERSION = "error.v1"

_LEGACY_SAFE_ERROR_MESSAGE = "分析失败，请重试；若持续失败请联系维护者。"

_NARRATION_STATUSES = frozenset({"available", "unavailable", "not_requested"})
_INPUT_MODES_V2 = frozenset({"input_native", "multimodal", "video_fallback"})
_EVIDENCE_V2_FIELDS = frozenset(
    {"sources", "provenance", "availability", "alignment", "warnings"}
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
    return (
        os.path.isabs(value)
        or value.startswith("\\")
        or (
            len(value) >= 3
            and value[0].isalpha()
            and value[1] == ":"
            and value[2] in {"/", "\\"}
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


def _validate_artifact_manifest_v2(manifest: dict) -> dict:
    if manifest.get("schema_version") != ARTIFACT_MANIFEST_V2_SCHEMA_VERSION:
        raise UnsupportedContractVersion(manifest.get("schema_version"))
    for field in ("external_inputs", "owned_outputs"):
        if not isinstance(manifest.get(field), list):
            raise ValueError(f"artifact_manifest.{field} must be a list")
    _assert_v2_path_safe(manifest, path="$.artifact_manifest")
    return dict(manifest)


def _validate_evidence_v2(evidence: object) -> dict:
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be a dict")
    missing = _EVIDENCE_V2_FIELDS.difference(evidence)
    if missing:
        raise ValueError(f"evidence missing required fields: {', '.join(sorted(missing))}")
    return evidence


def _validate_analysis_result_v2(result: dict) -> dict:
    if result.get("schema_version") != ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        raise UnsupportedContractVersion(result.get("schema_version"))

    _require_nonempty_string("analysis_id", result.get("analysis_id"))
    _require_nonempty_string("analysis_type", result.get("analysis_type"))
    input_mode = result.get("input_mode")
    if input_mode not in _INPUT_MODES_V2:
        raise ValueError(f"invalid input_mode: {input_mode}")
    kovaak_run_ref = result.get("kovaak_run_ref")
    if kovaak_run_ref is not None:
        _validate_stable_ref("kovaak_run_ref", kovaak_run_ref)
    _validate_evidence_v2(result.get("evidence"))

    if not isinstance(result.get("deterministic"), dict):
        raise ValueError("deterministic must be a dict")
    if not isinstance(result.get("input_snapshot"), dict):
        raise ValueError("input_snapshot must be a dict")
    if not isinstance(result.get("warnings"), list):
        raise ValueError("warnings must be a list")
    if not isinstance(result.get("errors"), list):
        raise ValueError("errors must be a list")
    if not isinstance(result.get("normalization_issues", []), list):
        raise ValueError("normalization_issues must be a list")

    artifact_manifest = result.get("artifact_manifest")
    if not isinstance(artifact_manifest, dict):
        raise ValueError("artifact_manifest must be a dict")
    _validate_artifact_manifest_v2(artifact_manifest)
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
    analysis_id: str,
    analysis_type: str,
    input_mode: str,
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
    result = {
        "schema_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
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
    return _validate_analysis_result_v2(result)


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