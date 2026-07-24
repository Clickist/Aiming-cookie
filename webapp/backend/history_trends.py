"""Path-safe History read models and deterministic trend comparison."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from .contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    validate_scenario_resolution_v1,
)
from .db import get_conn
from .workspace import session_dir


_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_SCENARIO_PROFILE_REF = re.compile(
    r"^scenario:[A-Za-z0-9][A-Za-z0-9._-]{0,159}@[1-9][0-9]*$"
)
_SAFE_SCENARIO_HASH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_ALIGNMENT_STATUSES = {"aligned", "partial", "failed", "unavailable", "not_required"}
_EVIDENCE_AVAILABILITIES = {"available", "missing", "unsupported", "unavailable", "invalid"}
_V2_INPUT_MODES = {"input_native", "multimodal", "video_fallback"}


def _safe_identity(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not _SAFE_IDENTITY.fullmatch(value)
        or value.casefold().startswith("file:")
        or re.match(r"^[A-Za-z]:", value)
    ):
        return None
    return value


def _safe_input_mode(value: object) -> str | None:
    return value if isinstance(value, str) and value in _V2_INPUT_MODES else None


def _metric(result: dict, key: str) -> dict | None:
    deterministic = result.get("deterministic")
    if not isinstance(deterministic, dict):
        return None
    metrics = deterministic.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(key)
    return value if isinstance(value, dict) else None


def _scenario(result: dict) -> str | None:
    snapshot = result.get("input_snapshot")
    return safe_scenario(snapshot.get("scenario")) if isinstance(snapshot, dict) else None


def _scenario_identity_version(result: dict) -> str | None:
    snapshot = result.get("input_snapshot")
    value = snapshot.get("scenario_identity_version") if isinstance(snapshot, dict) else None
    return _safe_identity(value)


def _scenario_resolution(result: dict) -> dict | None:
    snapshot = result.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    resolution = snapshot.get("scenario_resolution")
    return resolution if isinstance(resolution, dict) else None


def _scenario_resolution_is_invalid(result: dict) -> bool:
    snapshot = result.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return False
    resolution = snapshot.get("scenario_resolution")
    if resolution is None:
        return snapshot.get("schema_version") == "analysis_input_snapshot.v3"
    try:
        validate_scenario_resolution_v1(resolution)
    except (TypeError, ValueError):
        return True
    return False


def _scenario_profile_ref(result: dict) -> str | None:
    resolution = _scenario_resolution(result)
    value = resolution.get("scenario_profile_ref") if resolution else None
    return (
        value
        if isinstance(value, str) and _SAFE_SCENARIO_PROFILE_REF.fullmatch(value)
        else None
    )


def _scenario_hash(result: dict) -> str | None:
    resolution = _scenario_resolution(result)
    value = resolution.get("scenario_hash") if resolution else None
    return (
        value
        if isinstance(value, str) and _SAFE_SCENARIO_HASH.fullmatch(value)
        else None
    )


def _scenario_registry_version(result: dict) -> str | None:
    resolution = _scenario_resolution(result)
    return _safe_identity(resolution.get("registry_version")) if resolution else None


def _analysis_version(result: dict) -> str | None:
    value = result.get("analysis_version")
    return _safe_identity(value) if value is not None else None


def _timebase_version(result: dict) -> str | None:
    snapshot = result.get("input_snapshot")
    window = snapshot.get("canonical_time_window") if isinstance(snapshot, dict) else None
    value = window.get("timebase_version") if isinstance(window, dict) else None
    return _safe_identity(value) if value is not None else None


def _full_coverage(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) == 1.0
    )


def _quality_reason(result: dict, metric: dict) -> str | None:
    if metric.get("classification") != "deterministic":
        return "metric_not_deterministic"
    if metric.get("availability") != "available":
        return "metric_unavailable"
    if not _full_coverage(metric.get("coverage")):
        return "insufficient_metric_coverage"
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        return "insufficient_evidence_coverage"
    if not _full_coverage(evidence.get("coverage")):
        return "insufficient_evidence_coverage"
    alignment_value = evidence.get("alignment")
    alignment = alignment_value.get("status") if isinstance(alignment_value, dict) else None
    if alignment not in {"aligned", "not_required"}:
        return "insufficient_alignment_quality"
    return None


def _family_comparability_reason(
    current: dict,
    baseline: dict,
    current_metric: dict,
    baseline_metric: dict,
    missing_reason: str,
) -> str | None:
    current_deterministic = current.get("deterministic")
    baseline_deterministic = baseline.get("deterministic")
    if not isinstance(current_deterministic, dict) or not isinstance(
        baseline_deterministic, dict
    ):
        return missing_reason
    current_profile = _safe_identity(
        current_deterministic.get("visual_quality_profile_ref")
    )
    baseline_profile = _safe_identity(
        baseline_deterministic.get("visual_quality_profile_ref")
    )
    if not current_profile or not baseline_profile:
        return "visual_quality_profile_missing"
    if current_profile != baseline_profile:
        return "visual_quality_profile_mismatch"
    current_motion = _safe_identity(
        current_deterministic.get("scenario_motion_class")
    )
    baseline_motion = _safe_identity(
        baseline_deterministic.get("scenario_motion_class")
    )
    if not current_motion or current_motion != baseline_motion:
        return "motion_condition_mismatch"
    condition_sets = []
    for metric in (current_metric, baseline_metric):
        raw_refs = metric.get("condition_refs")
        if not isinstance(raw_refs, list) or not raw_refs:
            return "metric_condition_missing"
        refs = tuple(sorted(filter(None, (_safe_identity(ref) for ref in raw_refs))))
        if len(refs) != len(raw_refs):
            return "metric_condition_missing"
        condition_sets.append(refs)
    if condition_sets[0] != condition_sets[1]:
        return "metric_condition_mismatch"
    return None


def compare_analysis_results(current: dict, baseline: dict, metric_key: str) -> dict:
    """Compare two fully compatible v2 deterministic metrics."""
    if current.get("schema_version") != ANALYSIS_RESULT_V2_SCHEMA_VERSION or baseline.get(
        "schema_version"
    ) != ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        return {"comparable": False, "reason": "analysis_result_version_mismatch"}
    if "analysis_version" in current or "analysis_version" in baseline:
        current_analysis_version = _analysis_version(current)
        baseline_analysis_version = _analysis_version(baseline)
        if not current_analysis_version or current_analysis_version != baseline_analysis_version:
            return {"comparable": False, "reason": "analysis_version_mismatch"}
    if (
        isinstance(current.get("input_snapshot"), dict)
        and "canonical_time_window" in current["input_snapshot"]
    ) or (
        isinstance(baseline.get("input_snapshot"), dict)
        and "canonical_time_window" in baseline["input_snapshot"]
    ):
        current_timebase = _timebase_version(current)
        baseline_timebase = _timebase_version(baseline)
        if not current_timebase or current_timebase != baseline_timebase:
            return {"comparable": False, "reason": "timebase_version_mismatch"}
    if _scenario_resolution_is_invalid(current) or _scenario_resolution_is_invalid(
        baseline
    ):
        return {"comparable": False, "reason": "scenario_resolution_invalid"}
    current_resolution = _scenario_resolution(current)
    baseline_resolution = _scenario_resolution(baseline)
    scenario_predicates = (
        (
            "scenario_hash",
            _scenario_hash(current),
            _scenario_hash(baseline),
        ),
        (
            "scenario_profile_ref",
            _scenario_profile_ref(current),
            _scenario_profile_ref(baseline),
        ),
        (
            "scenario_registry_version",
            _scenario_registry_version(current),
            _scenario_registry_version(baseline),
        ),
    ) if current_resolution is not None or baseline_resolution is not None else (
        ("scenario", _scenario(current), _scenario(baseline)),
        (
            "scenario_identity_version",
            _scenario_identity_version(current),
            _scenario_identity_version(baseline),
        ),
    )
    predicates = (
        (
            "analysis_type",
            _safe_identity(current.get("analysis_type")),
            _safe_identity(baseline.get("analysis_type")),
        ),
        *scenario_predicates,
        (
            "input_mode",
            _safe_input_mode(current.get("input_mode")),
            _safe_input_mode(baseline.get("input_mode")),
        ),
    )
    for name, left, right in predicates:
        if not left or left != right:
            return {"comparable": False, "reason": f"{name}_mismatch"}

    if _safe_identity(metric_key) is None:
        return {"comparable": False, "reason": "metric_key_mismatch"}
    current_metric = _metric(current, metric_key)
    baseline_metric = _metric(baseline, metric_key)
    if current_metric is None or baseline_metric is None:
        return {"comparable": False, "reason": "metric_missing"}
    if (
        _safe_identity(current_metric.get("key")) != metric_key
        or _safe_identity(baseline_metric.get("key")) != metric_key
    ):
        return {"comparable": False, "reason": "metric_key_mismatch"}
    family_missing_reason = {
        "dynamic_clicking": "dynamic_comparability_missing",
        "continuous_tracking": "continuous_tracking_comparability_missing",
        "target_switching": "target_switching_comparability_missing",
    }.get(current.get("analysis_type"))
    if family_missing_reason:
        reason = _family_comparability_reason(
            current,
            baseline,
            current_metric,
            baseline_metric,
            family_missing_reason,
        )
        if reason:
            return {"comparable": False, "reason": reason}
    for result, metric in ((current, current_metric), (baseline, baseline_metric)):
        reason = _quality_reason(result, metric)
        if reason:
            return {"comparable": False, "reason": reason}

    current_version = _safe_identity(current_metric.get("metric_version"))
    baseline_version = _safe_identity(baseline_metric.get("metric_version"))
    if not current_version or current_version != baseline_version:
        return {"comparable": False, "reason": "metric_version_mismatch"}
    current_unit = _safe_identity(current_metric.get("unit"))
    baseline_unit = _safe_identity(baseline_metric.get("unit"))
    if not current_unit or current_unit != baseline_unit:
        return {"comparable": False, "reason": "metric_unit_mismatch"}
    current_calibration = _safe_identity(current_metric.get("calibration_ref"))
    baseline_calibration = _safe_identity(baseline_metric.get("calibration_ref"))
    if not current_calibration or not baseline_calibration:
        return {"comparable": False, "reason": "calibration_compatibility_missing"}
    if current_calibration != baseline_calibration:
        return {"comparable": False, "reason": "calibration_mismatch"}

    current_value = current_metric.get("value")
    baseline_value = baseline_metric.get("value")
    if (
        isinstance(current_value, bool)
        or isinstance(baseline_value, bool)
        or not isinstance(current_value, (int, float))
        or not isinstance(baseline_value, (int, float))
        or not math.isfinite(float(current_value))
        or not math.isfinite(float(baseline_value))
    ):
        return {"comparable": False, "reason": "metric_value_invalid"}
    delta = float(current_value) - float(baseline_value)
    percent = None if baseline_value == 0 else delta / abs(float(baseline_value)) * 100
    return {
        "comparable": True,
        "reason": None,
        "classification": "deterministic",
        "metric_key": metric_key,
        "unit": current_unit,
        "metric_version": current_version,
        "current": float(current_value),
        "baseline": float(baseline_value),
        "delta": delta,
        "percent_change": percent,
    }


def build_matched_dynamic_baseline(
    current: dict,
    baselines: list[tuple[int, dict]],
    metric_keys: list[str],
) -> dict:
    """Select the first prior Run with at least one fully comparable metric."""
    if current.get("analysis_type") != "dynamic_clicking":
        return {"comparable": False, "reason": "analysis_type_mismatch"}
    for session_id, baseline in baselines:
        baseline_metrics: dict[str, float] = {}
        comparisons: dict[str, dict[str, float | None]] = {}
        for metric_key in metric_keys:
            comparison = compare_analysis_results(current, baseline, metric_key)
            if not comparison["comparable"]:
                continue
            baseline_metrics[metric_key] = comparison["baseline"]
            comparisons[metric_key] = {
                "current": comparison["current"],
                "baseline": comparison["baseline"],
                "delta": comparison["delta"],
                "percent_change": comparison["percent_change"],
            }
        if baseline_metrics:
            baseline_ref = baseline.get("analysis_id")
            if not isinstance(baseline_ref, str) or not baseline_ref.startswith("analysis:"):
                baseline_ref = f"analysis:{session_id}"
            return {
                "comparable": True,
                "reason": None,
                "baseline_analysis_ref": baseline_ref,
                "baseline_metrics": baseline_metrics,
                "metric_comparisons": comparisons,
            }
    return {"comparable": False, "reason": "no_comparable_baseline"}


async def matched_dynamic_baseline_for_user(
    user_id: str,
    current: dict,
    metric_keys: list[str],
) -> dict:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, result FROM sessions WHERE user_id=? AND status='done' "
        "AND result IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 100",
        (user_id,),
    )
    rows = await cur.fetchall()
    baselines: list[tuple[int, dict]] = []
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
            baselines.append((int(row["id"]), result))
    return build_matched_dynamic_baseline(current, baselines, metric_keys)


def build_matched_tracking_baseline(
    current: dict,
    baselines: list[tuple[int, dict]],
    metric_keys: list[str],
) -> dict:
    """Select the first prior Run with at least one fully comparable metric."""
    if current.get("analysis_type") != "continuous_tracking":
        return {"comparable": False, "reason": "analysis_type_mismatch"}
    for session_id, baseline in baselines:
        baseline_metrics: dict[str, float] = {}
        comparisons: dict[str, dict[str, float | None]] = {}
        for metric_key in metric_keys:
            comparison = compare_analysis_results(current, baseline, metric_key)
            if not comparison["comparable"]:
                continue
            baseline_metrics[metric_key] = comparison["baseline"]
            comparisons[metric_key] = {
                "current": comparison["current"],
                "baseline": comparison["baseline"],
                "delta": comparison["delta"],
                "percent_change": comparison["percent_change"],
            }
        if baseline_metrics:
            baseline_ref = baseline.get("analysis_id")
            if not isinstance(baseline_ref, str) or not baseline_ref.startswith("analysis:"):
                baseline_ref = f"analysis:{session_id}"
            return {
                "comparable": True,
                "reason": None,
                "baseline_analysis_ref": baseline_ref,
                "baseline_metrics": baseline_metrics,
                "metric_comparisons": comparisons,
            }
    return {"comparable": False, "reason": "no_comparable_baseline"}


async def matched_tracking_baseline_for_user(
    user_id: str,
    current: dict,
    metric_keys: list[str],
) -> dict:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, result FROM sessions WHERE user_id=? AND status='done' "
        "AND result IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 100",
        (user_id,),
    )
    rows = await cur.fetchall()
    baselines: list[tuple[int, dict]] = []
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
            baselines.append((int(row["id"]), result))
    return build_matched_tracking_baseline(current, baselines, metric_keys)


def build_matched_target_switching_baseline(
    current: dict,
    baselines: list[tuple[int, dict]],
    metric_keys: list[str],
) -> dict:
    """Select the first prior Run with at least one fully comparable metric."""
    if current.get("analysis_type") != "target_switching":
        return {"comparable": False, "reason": "analysis_type_mismatch"}
    for session_id, baseline in baselines:
        baseline_metrics: dict[str, float] = {}
        comparisons: dict[str, dict[str, float | None]] = {}
        for metric_key in metric_keys:
            comparison = compare_analysis_results(current, baseline, metric_key)
            if not comparison["comparable"]:
                continue
            baseline_metrics[metric_key] = comparison["baseline"]
            comparisons[metric_key] = {
                "current": comparison["current"],
                "baseline": comparison["baseline"],
                "delta": comparison["delta"],
                "percent_change": comparison["percent_change"],
            }
        if baseline_metrics:
            baseline_ref = baseline.get("analysis_id")
            if not isinstance(baseline_ref, str) or not baseline_ref.startswith("analysis:"):
                baseline_ref = f"analysis:{session_id}"
            return {
                "comparable": True,
                "reason": None,
                "baseline_analysis_ref": baseline_ref,
                "baseline_metrics": baseline_metrics,
                "metric_comparisons": comparisons,
            }
    return {"comparable": False, "reason": "no_comparable_baseline"}


async def matched_target_switching_baseline_for_user(
    user_id: str,
    current: dict,
    metric_keys: list[str],
) -> dict:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, result FROM sessions WHERE user_id=? AND status='done' "
        "AND result IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 100",
        (user_id,),
    )
    rows = await cur.fetchall()
    baselines: list[tuple[int, dict]] = []
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
            baselines.append((int(row["id"]), result))
    return build_matched_target_switching_baseline(current, baselines, metric_keys)


def safe_scenario(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    scenario = value.strip()
    lowered = scenario.lower()
    if (
        os.path.isabs(scenario)
        or scenario.startswith("\\")
        or re.match(r"^[A-Za-z]:[\\/]", scenario)
        or lowered.startswith("file:")
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", scenario)
    ):
        return None
    return scenario


def _fingerprint_matches(
    path: object,
    expected_sha256: object,
    expected_size: object,
    expected_mtime_ns: object,
) -> str:
    if not isinstance(path, str) or not path:
        return "not_present"
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except OSError:
        return "unavailable"
    if not candidate.is_file():
        return "unavailable"
    if (
        not isinstance(expected_sha256, str)
        or not expected_sha256
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        return "invalid"
    if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime_ns:
        return "invalid"
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = candidate.stat()
    except OSError:
        return "unavailable"
    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return "invalid"
    return "available" if digest.hexdigest() == expected_sha256 else "invalid"


def _stat_matches(
    path: object,
    expected_size: object,
    expected_mtime_ns: object,
) -> str:
    if not isinstance(path, str) or not path:
        return "not_present"
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except OSError:
        return "unavailable"
    if not candidate.is_file():
        return "unavailable"
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        return "invalid"
    return "available" if (
        stat.st_size == expected_size and stat.st_mtime_ns == expected_mtime_ns
    ) else "invalid"


def _flat_source_availability(row: dict, kind: str) -> str:
    return _stat_matches(
        row.get(f"snapshot_{kind}_path"),
        row.get(f"snapshot_{kind}_size"),
        row.get(f"snapshot_{kind}_mtime_ns"),
    )


def _flat_trace_state(row: dict) -> str:
    return "attached" if (
        row.get("snapshot_trace_path") or row.get("snapshot_trace_sha256")
    ) else "none"


def _snapshot_trace_state(trace: object) -> str:
    if not isinstance(trace, dict):
        return "none"
    return "attached" if trace.get("artifact_ref") or trace.get("path") else "none"


def _trace_quality(
    *,
    state: object,
    availability: str,
    alignment_status: object,
    coverage: object,
) -> dict[str, object]:
    return {
        "state": state if isinstance(state, str) and state else "none",
        "availability": availability,
        "alignment_status": (
            alignment_status if alignment_status in _ALIGNMENT_STATUSES else None
        ),
        "coverage": (
            float(coverage)
            if not isinstance(coverage, bool)
            and isinstance(coverage, (int, float))
            and math.isfinite(float(coverage))
            else None
        ),
    }


def analysis_list_item(row: dict) -> dict:
    run_id = row.get("kovaak_run_id")
    source_availability = {
        kind: _flat_source_availability(row, kind)
        for kind in ("stats", "performance", "video")
        if row.get(f"snapshot_{kind}_path") is not None
    }
    trace_availability = _flat_source_availability(row, "trace")
    return {
        "id": int(row["id"]),
        "analysis_ref": f"analysis:{row['id']}",
        "run_ref": f"run:{run_id}" if run_id is not None else None,
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row.get("finished_at"),
        "attempts": int(row.get("attempts") or 0),
        "max_attempts": int(row.get("max_attempts") or 1),
        "llm_cost_cny": float(row.get("llm_cost_cny") or 0),
        "summary_label": row.get("summary_label"),
        "analysis_type": row.get("analysis_type") or "flicking",
        "input_mode": row.get("input_mode") or "video_fallback",
        "kovaak_run_id": run_id,
        "scenario": safe_scenario(row.get("scenario")),
        "source_availability": source_availability,
        "trace_quality": _trace_quality(
            state=_flat_trace_state(row),
            availability=trace_availability,
            alignment_status=row.get("alignment_status"),
            coverage=row.get("evidence_coverage"),
        ),
    }


def _snapshot_source_availability(source: object) -> str:
    if not isinstance(source, dict):
        return "not_present"
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, dict):
        return "invalid" if source.get("path") else "not_present"
    return _fingerprint_matches(
        source.get("path"),
        fingerprint.get("sha256"),
        fingerprint.get("size"),
        fingerprint.get("mtime_ns"),
    )


def _is_managed_session_file(session_id: int, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        path = Path(value).resolve()
        path.relative_to(session_dir(session_id))
    except (OSError, ValueError):
        return False
    return path.suffix.lower() == ".mp4" and path.is_file()


def _manifest_is_path_free(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            compact = str(key).replace("_", "").lower()
            if compact == "path" or compact.endswith("path") or compact.endswith("paths"):
                return False
            if not _manifest_is_path_free(child):
                return False
        return True
    if isinstance(value, list):
        return all(_manifest_is_path_free(child) for child in value)
    if isinstance(value, str):
        return not (
            os.path.isabs(value)
            or value.startswith("\\")
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or value.lower().startswith("file:")
        )
    return True


def visual_replay_capability(session: dict) -> dict[str, object]:
    session_id = int(session["id"])
    input_mode = session.get("input_mode") or "video_fallback"
    if input_mode == "input_native":
        return {
            "kind": "native_only",
            "available": False,
            "seekable": False,
            "endpoint": None,
            "artifact_ref": None,
            "reason": "input_native_has_no_visual_replay",
        }

    result = session.get("result")
    artifact_ref: str | None = None
    contracted = False
    if isinstance(result, dict) and result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        evidence = result.get("evidence") or {}
        sources = evidence.get("sources") or {}
        mp4 = sources.get("mp4") if isinstance(sources, dict) else None
        if (
            isinstance(mp4, dict)
            and mp4.get("source") == "mp4"
            and mp4.get("availability") in {"available", "unavailable"}
        ):
            candidate_ref = mp4.get("artifact_ref")
            stable_ref = (
                isinstance(candidate_ref, str)
                and bool(candidate_ref)
                and "/" not in candidate_ref
                and "\\" not in candidate_ref
                and not candidate_ref.lower().startswith("file:")
            )
            manifest = result.get("artifact_manifest") or {}
            entries = manifest.get("external_inputs") or []
            contracted = stable_ref and any(
                isinstance(entry, dict)
                and entry.get("id") == candidate_ref
                and entry.get("kind") == "mp4"
                and entry.get("source") == "mp4"
                and entry.get("availability") == "available"
                and entry.get("status") == "available"
                and entry.get("ownership") == "analysis"
                and entry.get("managed") is True
                and entry.get("local_only") is True
                and entry.get("format_version") == "mp4"
                for entry in entries
            )
            if contracted:
                artifact_ref = candidate_ref
    elif (
        isinstance(result, dict)
        and result.get("schema_version") == ANALYSIS_RESULT_SCHEMA_VERSION
        and input_mode == "video_fallback"
    ):
        manifest = result.get("artifact_manifest") or {}
        if (
            manifest.get("schema_version") == "artifact_manifest.v1"
            and _manifest_is_path_free(manifest)
        ):
            for entry in manifest.get("inputs") or []:
                if (
                    isinstance(entry, dict)
                    and entry.get("kind") == "input_video"
                    and entry.get("status") == "available"
                ):
                    contracted = True
                    candidate_ref = entry.get("id")
                    artifact_ref = candidate_ref if isinstance(candidate_ref, str) else None
                    break
        if result.get("analysis_version") == LEGACY_ANALYSIS_VERSION and not (
            manifest.get("inputs") or manifest.get("outputs")
        ):
            contracted = True

    managed_file = _is_managed_session_file(session_id, session.get("video_path"))
    if contracted and managed_file:
        return {
            "kind": "seekable_mp4",
            "available": True,
            "seekable": True,
            "endpoint": f"/api/sessions/{session_id}/video",
            "artifact_ref": artifact_ref,
            "reason": None,
        }
    return {
        "kind": "unavailable",
        "available": False,
        "seekable": False,
        "endpoint": None,
        "artifact_ref": artifact_ref,
        "reason": "visual_replay_unavailable",
    }


def _manifest_entries(result: dict) -> dict[str, dict]:
    manifest = result.get("artifact_manifest") or {}
    entries = [
        *list(manifest.get("external_inputs") or []),
        *list(manifest.get("owned_outputs") or []),
    ]
    return {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _metric_keys_for_source(result: dict, source_name: str) -> list[str]:
    metrics = ((result.get("deterministic") or {}).get("metrics") or {})
    out: list[str] = []
    for key, metric in metrics.items():
        if not isinstance(key, str) or not isinstance(metric, dict):
            continue
        sources = (metric.get("provenance") or {}).get("sources") or []
        if source_name in sources:
            out.append(key)
    return sorted(out)


def evidence_references(result: object, analysis_ref: str) -> list[dict[str, object]]:
    if not isinstance(result, dict) or result.get("schema_version") != ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        return []
    evidence = result.get("evidence") or {}
    sources = evidence.get("sources") or {}
    source_items = sources.items() if isinstance(sources, dict) else enumerate(sources)
    manifest = _manifest_entries(result)
    refs: list[dict[str, object]] = []
    for source_key, source in source_items:
        if not isinstance(source, dict):
            continue
        source_name = source.get("source") or source_key
        artifact_id = source.get("artifact_ref")
        if not isinstance(source_name, str) or not _SAFE_SOURCE.fullmatch(source_name):
            continue
        if not isinstance(artifact_id, str) or "/" in artifact_id or "\\" in artifact_id:
            continue
        artifact = manifest.get(artifact_id)
        if not isinstance(artifact, dict) or not isinstance(artifact.get("local_only"), bool):
            continue
        availability = source.get("availability")
        alignment = source.get("alignment")
        if availability not in _EVIDENCE_AVAILABILITIES or alignment not in _ALIGNMENT_STATUSES:
            continue
        reference: dict[str, object] = {
            "id": f"evidence:{analysis_ref}:{source_name}",
            "source": source_name,
            "artifact_id": artifact_id,
            "alignment_status": alignment,
            "availability": availability,
            "local_only": artifact["local_only"],
            "metric_keys": _metric_keys_for_source(result, source_name),
        }
        time_range = source.get("challenge_time_range_ms")
        if (
            isinstance(time_range, list)
            and len(time_range) == 2
            and all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                for value in time_range
            )
        ):
            reference["challenge_time_range_ms"] = [float(value) for value in time_range]
        refs.append(reference)
    return refs


async def analysis_history_detail(session: dict) -> dict[str, object]:
    session_id = int(session["id"])
    run_id = session.get("kovaak_run_id")
    snapshot = session.get("input_snapshot") or {}
    sources = snapshot.get("sources") or {}
    source_availability = {
        key: _snapshot_source_availability(source)
        for key, source in sources.items()
        if isinstance(key, str) and key != "video"
    }
    trace_availability = _snapshot_source_availability(snapshot.get("trace"))
    result = session.get("result") if isinstance(session.get("result"), dict) else {}
    evidence = result.get("evidence") or {}
    alignment = evidence.get("alignment") or {}
    analysis_ref = f"analysis:{session_id}"
    result_analysis_ref = result.get("analysis_id")
    if isinstance(result_analysis_ref, str) and result_analysis_ref == analysis_ref:
        analysis_ref = result_analysis_ref
    return {
        "analysis_ref": analysis_ref,
        "run_ref": f"run:{run_id}" if run_id is not None else None,
        "scenario": safe_scenario(snapshot.get("scenario")),
        "input_mode": session.get("input_mode") or "video_fallback",
        "source_availability": source_availability,
        "trace_quality": _trace_quality(
            state=_snapshot_trace_state(snapshot.get("trace")),
            availability=trace_availability,
            alignment_status=alignment.get("status"),
            coverage=evidence.get("coverage"),
        ),
        "visual_replay": visual_replay_capability(session),
        "diagnosis_locator": {
            "analysis_ref": analysis_ref,
            "section": "diagnosis",
        },
        "evidence_refs": evidence_references(result, analysis_ref),
    }


async def recent_trend_for_user(user_id: str, metric_key: str) -> dict:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, result FROM sessions WHERE user_id=? AND status='done' "
        "AND result IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 100",
        (user_id,),
    )
    rows = await cur.fetchall()
    parsed: list[tuple[int, dict]] = []
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
            parsed.append((int(row["id"]), result))
    if len(parsed) < 2:
        return {"comparable": False, "reason": "insufficient_history"}
    current_id, current = parsed[0]
    for baseline_id, baseline in parsed[1:]:
        comparison = compare_analysis_results(current, baseline, metric_key)
        if comparison["comparable"]:
            return {
                **comparison,
                "current_session_id": current_id,
                "baseline_session_id": baseline_id,
            }
    return {
        "comparable": False,
        "reason": "no_comparable_baseline",
        "current_session_id": current_id,
    }
