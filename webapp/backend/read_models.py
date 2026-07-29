"""Versioned, path-free read models for the formal desktop frontend.

The database and native capture layers keep private paths and diagnostics.  This
module is the single projection boundary used by the HTTP routes and contract
tests, so a read failure cannot be silently represented as an empty collection.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

PRODUCT_STATE_SCHEMA_VERSION = "product_state.v1"
CAPTURE_STATUS_SCHEMA_VERSION = "capture_status.v1"
TASK_LIST_SCHEMA_VERSION = "task_list.v1"
TASK_DETAIL_SCHEMA_VERSION = "task_detail.v1"
ANALYSIS_DATA_SCHEMA_VERSION = "frontend_analysis_data.v1"

_TASK_PHASE_LABELS = {
    "preparing_training_record": "Preparing training record",
    "aligning_input_events": "Aligning input events",
    "computing_kinematics": "Computing movement metrics",
    "analyzing_video": "Analyzing video",
    "generating_diagnostics": "Generating diagnostics",
}
_TASK_STATE_LABELS = {
    "importing": "Importing",
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
    "retrying": "Retrying",
}
_FAILURE_DOMAINS = {
    "source_file",
    "alignment",
    "kinematics",
    "video",
    "provider",
    "coach",
    "network",
}
_FORBIDDEN_TEXT = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var)/|Traceback|secret|token|password)",
    re.IGNORECASE,
)
_PUBLIC_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,239}$")
_EVENT_KIND = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_T = TypeVar("_T")


def _error_payload(code: str, message: str) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "retryable": False,
    }


def _finite_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return int(round(numeric))


def _bounded_evenly(items: Sequence[_T], maximum: int) -> list[_T]:
    if len(items) <= maximum:
        return list(items)
    return [items[round(index * (len(items) - 1) / (maximum - 1))] for index in range(maximum)]


def _safe_analysis_limitations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        item
        for item in value
        if isinstance(item, str)
        and 0 < len(item) <= 240
        and not _FORBIDDEN_TEXT.search(item)
    ))


def _project_event_rows(
    artifact: Mapping[str, object], *, window_start_ms: int, window_end_ms: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    events_by_ref: dict[str, dict[str, object]] = {}
    bundles = artifact.get("event_bundles")
    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, Mapping):
            continue
        events = bundle.get("events")
        for raw in events if isinstance(events, list) else []:
            if not isinstance(raw, Mapping):
                continue
            event_ref = raw.get("event_id")
            kind = raw.get("event_kind")
            start_ms = _finite_int(raw.get("start_ms"))
            if (
                not isinstance(event_ref, str)
                or not _PUBLIC_REF.fullmatch(event_ref)
                or not isinstance(kind, str)
                or not _EVENT_KIND.fullmatch(kind)
                or start_ms is None
                or not window_start_ms <= start_ms < window_end_ms
            ):
                continue
            events_by_ref.setdefault(event_ref, {
                "event_ref": event_ref,
                "kind": kind,
                "relative_ms": start_ms - window_start_ms,
            })
    events = sorted(events_by_ref.values(), key=lambda item: (item["relative_ms"], item["event_ref"]))
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    distribution = [
        {"kind": kind, "count": count}
        for kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    first_by_kind: dict[str, dict[str, object]] = {}
    for event in events:
        first_by_kind.setdefault(str(event["kind"]), event)
    representatives = list(first_by_kind.values())
    if len(representatives) >= 128:
        return _bounded_evenly(representatives, 128), distribution
    selected_refs = {str(event["event_ref"]) for event in representatives}
    remaining = [event for event in events if str(event["event_ref"]) not in selected_refs]
    markers = representatives + _bounded_evenly(remaining, 128 - len(representatives))
    return sorted(markers, key=lambda item: (item["relative_ms"], item["event_ref"])), distribution


def _sample_values(sample_set: object, *, window_start_ms: int, window_end_ms: int) -> dict[int, float]:
    if not isinstance(sample_set, Mapping):
        return {}
    points = sample_set.get("points")
    if not isinstance(points, list):
        return {}
    values: dict[int, float] = {}
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            continue
        time_ms = _finite_int(point[0])
        value = point[1]
        if (
            time_ms is None
            or not window_start_ms <= time_ms < window_end_ms
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            continue
        values[time_ms] = float(value)
    return values


def _target_relative_error_radius(
    artifact: Mapping[str, object], *, window_start_ms: int, window_end_ms: int,
) -> dict[str, object]:
    sample_sets = artifact.get("sample_sets")
    samples_by_ref = {
        sample.get("sample_set_id"): sample
        for sample in sample_sets if isinstance(sample_sets, list) and isinstance(sample, Mapping)
        if isinstance(sample.get("sample_set_id"), str)
    }
    target_channels: dict[str, dict[str, str]] = {}
    bundles = artifact.get("signal_bundles")
    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, Mapping):
            continue
        channels = bundle.get("channels")
        for channel in channels if isinstance(channels, list) else []:
            if not isinstance(channel, Mapping):
                continue
            key = channel.get("channel_key")
            sample_ref = channel.get("samples_ref")
            if not isinstance(key, str) or not isinstance(sample_ref, str):
                continue
            match = re.fullmatch(
                r"target\.([A-Za-z0-9._:-]+)\.(position_x|position_y|visible_radius)", key,
            )
            if match:
                target_channels.setdefault(match.group(1), {})[match.group(2)] = sample_ref
            elif key == "crosshair.position_x":
                target_channels.setdefault("__crosshair__", {})["position_x"] = sample_ref
            elif key == "crosshair.position_y":
                target_channels.setdefault("__crosshair__", {})["position_y"] = sample_ref

    crosshair = target_channels.pop("__crosshair__", {})
    required = {"position_x", "position_y", "visible_radius"}
    candidates = [
        channels for channels in target_channels.values()
        if required <= set(channels) and {"position_x", "position_y"} <= set(crosshair)
    ]
    if not candidates:
        return {
            "availability": "unavailable",
            "reason": "target_relative_channels_unavailable",
            "points": [],
        }
    if len(candidates) != 1:
        return {
            "availability": "unavailable",
            "reason": "target_relative_target_ambiguous",
            "points": [],
        }

    target = candidates[0]
    sample_maps = {
        name: _sample_values(samples_by_ref.get(sample_ref), window_start_ms=window_start_ms, window_end_ms=window_end_ms)
        for name, sample_ref in {
            "crosshair_x": crosshair["position_x"],
            "crosshair_y": crosshair["position_y"],
            "target_x": target["position_x"],
            "target_y": target["position_y"],
            "radius": target["visible_radius"],
        }.items()
    }
    common_times = set.intersection(*(set(values) for values in sample_maps.values())) if sample_maps else set()
    points = []
    for time_ms in sorted(common_times):
        radius = sample_maps["radius"][time_ms]
        if radius <= 0:
            continue
        error = math.hypot(
            sample_maps["crosshair_x"][time_ms] - sample_maps["target_x"][time_ms],
            sample_maps["crosshair_y"][time_ms] - sample_maps["target_y"][time_ms],
        ) / radius
        if math.isfinite(error):
            points.append({
                "relative_ms": time_ms - window_start_ms,
                "normalized_error_radius": round(error, 2),
            })
    if not points:
        return {
            "availability": "unavailable",
            "reason": "target_relative_samples_unavailable",
            "points": [],
        }
    return {
        "availability": "available",
        "reason": None,
        "points": _bounded_evenly(points, 120),
    }


def build_frontend_analysis_data_v1(
    *, analysis_ref: str, artifact: Mapping[str, object],
) -> dict[str, object]:
    """Project one validated, owner-bound artifact without coordinate channels."""
    if not _PUBLIC_REF.fullmatch(analysis_ref) or artifact.get("analysis_ref") != analysis_ref:
        raise ValueError("analysis data artifact is bound to another analysis")
    window = artifact.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise ValueError("analysis data canonical window is unavailable")
    window_start_ms = _finite_int(window.get("start_ms"))
    window_end_ms = _finite_int(window.get("end_ms"))
    if window_start_ms is None or window_end_ms is None or window_end_ms <= window_start_ms:
        raise ValueError("analysis data canonical window is invalid")
    markers, distribution = _project_event_rows(
        artifact,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )
    return {
        "schema_version": ANALYSIS_DATA_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "limitations": _safe_analysis_limitations(artifact.get("limitations")),
        "event_markers": markers,
        "event_distribution": distribution,
        "target_relative_error_radius": _target_relative_error_radius(
            artifact,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        ),
    }


def build_product_state_v1(
    *,
    onboarding_completed: bool | None = None,
    onboarding_completion_kind: str | None = None,
    has_pending_runs: bool | None = None,
    has_runs: bool | None = None,
    has_analyses: bool | None = None,
    read_error: str | None = None,
) -> dict[str, object]:
    """Build the conditional-start read model.

    ``None`` is intentional in the unavailable shape: it cannot be confused
    with an authoritative empty state by a client.
    """
    if read_error:
        return {
            "schema_version": PRODUCT_STATE_SCHEMA_VERSION,
            "availability": "unavailable",
            "onboarding_completed": None,
            "onboarding_completion_kind": None,
            "has_pending_runs": None,
            "has_runs": None,
            "has_analyses": None,
            "error": _error_payload(read_error, "Product state is unavailable"),
        }
    if onboarding_completed is not True and onboarding_completed is not False:
        raise ValueError("onboarding_completed is required for an available state")
    if onboarding_completion_kind not in {"connected", "skipped", "legacy", None}:
        raise ValueError("invalid onboarding_completion_kind")
    return {
        "schema_version": PRODUCT_STATE_SCHEMA_VERSION,
        "availability": "available",
        "onboarding_completed": onboarding_completed,
        "onboarding_completion_kind": onboarding_completion_kind,
        "has_pending_runs": bool(has_pending_runs),
        "has_runs": bool(has_runs),
        "has_analyses": bool(has_analyses),
        "error": None,
    }


def _native_status_value(native_status: Mapping[str, object] | None, key: str, default: object = None) -> object:
    if not isinstance(native_status, Mapping):
        return default
    return native_status.get(key, default)


def _source_state(native_status: Mapping[str, object] | None, source: str) -> str | None:
    value = _native_status_value(native_status, source)
    return value.get("state") if isinstance(value, Mapping) else None


def build_capture_status_v1(
    *,
    native_status: Mapping[str, object] | None = None,
    runs: Sequence[Mapping[str, object]] = (),
    read_error: str | None = None,
) -> dict[str, object]:
    """Project native coordinator status plus per-Run evidence attachment."""
    if read_error:
        return {
            "schema_version": CAPTURE_STATUS_SCHEMA_VERSION,
            "availability": "unavailable",
            "platform_supported": None,
            "raw_input_permission": "not_determined",
            "capture_enabled": None,
            "kovaak_process_present": None,
            "replay_buffer_active": None,
            "runtime_health": "unavailable",
            "finalization_state": "unknown",
            "pause_state": "unknown",
            "pause_fail_closed": True,
            "runs": [],
            "error": _error_payload(read_error, "Capture status is unavailable"),
        }

    phase = _native_status_value(native_status, "phase")
    enabled = _native_status_value(native_status, "enabled") is True
    raw_state = _source_state(native_status, "raw")
    video_state = _source_state(native_status, "video")
    available_native = isinstance(native_status, Mapping)
    platform_supported = bool(
        available_native
        and raw_state != "unavailable"
        and video_state != "unavailable"
    )
    explicit_permission = _native_status_value(native_status, "rawInputPermission")
    if explicit_permission not in {"granted", "denied", "not_determined"}:
        explicit_permission = None
    if explicit_permission is not None:
        permission = explicit_permission
    elif raw_state in {"capturing", "finalizing"} and enabled:
        permission = "granted"
    else:
        permission = "not_determined"
    if phase in {"error"} or not available_native:
        runtime_health = "unavailable"
    elif phase in {"degraded"} or raw_state == "degraded" or video_state == "degraded":
        runtime_health = "degraded"
    else:
        runtime_health = "healthy"

    public_runs: list[dict[str, object]] = []
    pause_fail_closed = False
    finalization_state = "idle"
    for run in runs:
        run_ref = run.get("run_ref")
        if not isinstance(run_ref, str) or not run_ref:
            continue
        trace_state = run.get("trace_state")
        video_ref = run.get("video_artifact_ref")
        alignment = run.get("alignment")
        error_code = alignment.get("error_code") if isinstance(alignment, Mapping) else None
        if error_code == "pause_unsupported":
            pause_fail_closed = True
        state = run.get("finalization_state")
        if state in {"pending", "capturing", "finalizing"}:
            finalization_state = str(state)
        public_runs.append({
            "run_ref": run_ref,
            "raw_attached": trace_state == "attached",
            "video_attached": isinstance(video_ref, str) and bool(video_ref),
        })

    if phase == "finalizing" or finalization_state == "finalizing":
        finalization_state = "finalizing"
    elif phase in {"capturing", "degraded"} and finalization_state == "idle":
        finalization_state = "capturing"
    return {
        "schema_version": CAPTURE_STATUS_SCHEMA_VERSION,
        "availability": "available",
        "platform_supported": platform_supported,
        "raw_input_permission": permission,
        "capture_enabled": enabled,
        "kovaak_process_present": _native_status_value(
            native_status, "kovaakProcessPresent", False,
        ) is True,
        "replay_buffer_active": enabled and video_state == "capturing",
        "runtime_health": runtime_health,
        "finalization_state": finalization_state,
        "pause_state": "fail_closed" if pause_fail_closed else "clear",
        "pause_fail_closed": pause_fail_closed,
        "runs": public_runs,
        "error": None,
    }


def _decode_json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
    return value


def _safe_failure(row: Mapping[str, object]) -> dict[str, object] | None:
    raw = _decode_json(row.get("error"))
    if isinstance(raw, Mapping):
        code = raw.get("code") if isinstance(raw.get("code"), str) else "task_failed"
        raw_message = raw.get("message")
        message = raw_message if isinstance(raw_message, str) else "Task failed"
        if _FORBIDDEN_TEXT.search(message):
            message = "Task failed"
        retryable = raw.get("retryable") is True
    elif isinstance(raw, str) and raw:
        code, message, retryable = "task_failed", raw, False
        if _FORBIDDEN_TEXT.search(message):
            message = "Task failed"
    else:
        return None
    domain = row.get("failure_domain")
    if domain not in _FAILURE_DOMAINS:
        domain = "source_file" if code in {"source_unavailable", "missing_snapshot"} else "kinematics"
    return {"domain": domain, "code": code, "message": message, "retryable": retryable}


def _partial_outcome(row: Mapping[str, object]) -> dict[str, object] | None:
    explicit = _decode_json(row.get("partial_outcome_json"))
    if isinstance(explicit, Mapping):
        return {
            "status": "partial",
            "native_preserved": explicit.get("native_preserved") is True,
            "visual_status": str(explicit.get("visual_status") or "unavailable"),
            "reason_code": str(explicit.get("reason_code") or "visual_unavailable"),
        }
    if row.get("status") != "done" or row.get("input_mode") != "multimodal":
        return None
    result = _decode_json(row.get("result"))
    if not isinstance(result, Mapping):
        return None
    evidence = result.get("evidence")
    availability = evidence.get("availability") if isinstance(evidence, Mapping) else None
    video_status = None
    if isinstance(availability, Mapping):
        video_status = availability.get("mp4") or availability.get("video")
    deterministic = result.get("deterministic")
    native_preserved = isinstance(deterministic, Mapping) and deterministic.get("status") in {
        "available", "limited",
    }
    if video_status in {"unavailable", "missing", "not_present"} and native_preserved:
        return {
            "status": "partial",
            "native_preserved": True,
            "visual_status": "unavailable",
            "reason_code": "video_unavailable",
        }
    return None


def _state_for(row: Mapping[str, object]) -> str:
    status = row.get("status")
    if status == "uploading":
        return "importing"
    if status in {"queued", "running", "done", "failed", "retrying"}:
        return str(row["task_state"]) if row.get("task_state") in _TASK_STATE_LABELS else str(status)
    return "failed"


def _phase_for(row: Mapping[str, object], state: str) -> str | None:
    phase = row.get("task_phase")
    if phase in _TASK_PHASE_LABELS:
        if row.get("input_mode") == "input_native" and phase == "analyzing_video":
            return "computing_kinematics"
        return str(phase)
    if state in {"importing", "queued", "retrying"}:
        return "preparing_training_record"
    if state == "running":
        return (
            "computing_kinematics"
            if row.get("input_mode") == "video_fallback"
            else "aligning_input_events"
        )
    return None


def _attempt_projection(row: Mapping[str, object]) -> dict[str, object]:
    state = _state_for(row)
    failure = _safe_failure(row)
    return {
        "attempt_ref": f"analysis:{row.get('id')}",
        "attempt_number": int(row.get("attempt_number") or 1),
        "state": state,
        "state_label": _TASK_STATE_LABELS[state],
        "phase": _phase_for(row, state),
        "failure": failure,
        "partial_outcome": _partial_outcome(row),
        "retryable": bool(failure and failure.get("retryable")),
        "can_delete": state in {"done", "failed"},
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


def build_task_detail_v1(
    row: Mapping[str, object],
    *,
    attempts: Sequence[Mapping[str, object]] = (),
    read_error: str | None = None,
) -> dict[str, object]:
    if read_error:
        return {
            "schema_version": TASK_DETAIL_SCHEMA_VERSION,
            "availability": "unavailable",
            "task_ref": None,
            "error": _error_payload(read_error, "Task is unavailable"),
        }
    state = _state_for(row)
    phase = _phase_for(row, state)
    attempt_rows = list(attempts) or [row]
    attempt_rows.sort(key=lambda item: int(item.get("attempt_number") or 1))
    failure = _safe_failure(row)
    task_ref = row.get("task_group_ref") or f"task:{row.get('id')}"
    return {
        "schema_version": TASK_DETAIL_SCHEMA_VERSION,
        "availability": "available",
        "task_ref": task_ref,
        "analysis_ref": f"analysis:{row.get('id')}",
        "state": state,
        "state_label": _TASK_STATE_LABELS[state],
        "phase": phase,
        "phase_label": _TASK_PHASE_LABELS.get(phase) if phase else None,
        "input_mode": row.get("input_mode") or "video_fallback",
        "analysis_type": row.get("analysis_type") or "flicking",
        "run_ref": f"run:{row['kovaak_run_id']}" if row.get("kovaak_run_id") else None,
        "failure": failure,
        "partial_outcome": _partial_outcome(row),
        "retryable": bool(failure and failure.get("retryable")),
        "can_delete": state in {"done", "failed"},
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "attempt_number": int(row.get("attempt_number") or 1),
        "attempt_history": [_attempt_projection(item) for item in attempt_rows],
        "error": None,
    }


def build_task_list_v1(
    rows: Sequence[Mapping[str, object]] = (),
    *,
    read_error: str | None = None,
) -> dict[str, object]:
    if read_error:
        return {
            "schema_version": TASK_LIST_SCHEMA_VERSION,
            "availability": "unavailable",
            "tasks": [],
            "error": _error_payload(read_error, "Tasks are unavailable"),
        }
    tasks = [build_task_detail_v1(row) for row in rows]
    return {
        "schema_version": TASK_LIST_SCHEMA_VERSION,
        "availability": "available",
        "tasks": tasks,
        "error": None,
    }


def _calibration_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric > 0 else None


def resolve_calibration_v1(
    *,
    stats: Mapping[str, object] | None,
    manual_override: Mapping[str, object] | None,
    profile_default: Mapping[str, object] | None,
) -> dict[str, dict[str, object | None]]:
    """Resolve each field independently: Stats > manual > profile > unknown."""
    selected: dict[str, dict[str, object | None]] = {}
    for field in ("cm_per_360", "fov"):
        candidates = (
            ("stats", stats),
            ("manual_override", manual_override),
            ("profile_default", profile_default),
        )
        value = None
        source = "undetermined"
        for candidate_source, candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_value = _calibration_value(candidate.get(field))
            if candidate_value is not None:
                value = candidate_value
                source = candidate_source
                break
        selected[field] = {"value": value, "source": source}
    return selected


__all__ = [
    "ANALYSIS_DATA_SCHEMA_VERSION",
    "CAPTURE_STATUS_SCHEMA_VERSION",
    "PRODUCT_STATE_SCHEMA_VERSION",
    "TASK_DETAIL_SCHEMA_VERSION",
    "TASK_LIST_SCHEMA_VERSION",
    "build_capture_status_v1",
    "build_frontend_analysis_data_v1",
    "build_product_state_v1",
    "build_task_detail_v1",
    "build_task_list_v1",
    "resolve_calibration_v1",
]
