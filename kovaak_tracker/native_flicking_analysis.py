"""Deterministic, input-native flicking measurements.

This module deliberately measures only canonical Raw Input records:
``{timestamp_ms, dx, dy, buttons}``.  It never writes cumulative coordinates
back into those records.  When a Performance anchor is present, raw epoch
milliseconds are aligned to the challenge window; Performance events remain
source facts on the challenge-relative timeline.

Assumptions and non-goals:
- ``timestamp_ms`` is Unix epoch milliseconds and records retain capture order.
- ``dx``/``dy`` are raw mouse counts, not pixels, degrees, or physical distance.
- Path length includes each record delta. Speed starts with the second record,
  because the first delta has no preceding time interval.
- Calibration is opt-in and must explicitly provide ``raw_counts_per_unit`` and
  a declared unit. No sensitivity, FOV, target position, or target inference is
  guessed by this adapter.
- Video is intentionally absent from this API; it is not needed for
  ``input_native`` analysis.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from math import ceil, gcd, hypot, isfinite
from typing import Any

from .time_alignment import TimeAlignmentError, reject_pause_evidence, resolve_time_window

import numpy as np


METRIC_VERSION = "native_flicking.v1"
SPARC_METRIC_VERSION = "native_flicking.sparc.v2"
ALIGNMENT_VERSION = "time_alignment.v2"


class NativeFlickingAnalysisError(ValueError):
    """Raised when canonical Raw Input records cannot be analyzed safely."""


def derive_trajectory(points: Iterable[Mapping[str, Any] | Any]) -> list[dict[str, int]]:
    """Return a prefix-sum trajectory derived from immutable raw point values.

    The returned dictionaries are new objects. Input points are read-only from
    the adapter's perspective and are never augmented with cumulative fields.
    Same-millisecond records are valid and preserve input order; timestamps may
    not move backwards.
    """
    normalized = _normalize_points(points)
    x_raw_counts = 0
    y_raw_counts = 0
    trajectory: list[dict[str, int]] = []
    for item in normalized:
        x_raw_counts += item["dx"]
        y_raw_counts += item["dy"]
        trajectory.append(
            {
                **item,
                "x_raw_counts": x_raw_counts,
                "y_raw_counts": y_raw_counts,
            }
        )
    return trajectory


def align_points_to_challenge(
    points: Iterable[Mapping[str, Any] | Any] | None,
    performance: Mapping[str, Any] | Any | None = None,
    *,
    canonical_window: Mapping[str, Any] | Any | None = None,
    _trajectory_is_derived: bool = False,
) -> dict[str, Any]:
    """Align canonical epoch records to a Performance challenge window.

    ``performance`` may be a mapping or the existing ``PerformanceData`` object.
    It must expose ``challenge_start_utc`` and a positive time limit, either as
    ``time_limit_ms`` or a ``challenge_profile.time_limit`` value in seconds.
    The result has one of ``aligned``, ``partial``, ``failed``, or ``unavailable``.
    """
    if points is None:
        return _alignment_result("unavailable", None, (), "raw_input_missing")

    trajectory = points if _trajectory_is_derived else derive_trajectory(points)
    context = (
        _canonical_window_context(canonical_window)
        if canonical_window is not None
        else _performance_context(performance)
    )
    if context is None:
        return _alignment_result("unavailable", None, (), "performance_anchor_missing")

    start_ms, end_ms, alignment_provenance, window_warnings = context
    duration_ms = end_ms - start_ms
    inside = tuple(item for item in trajectory if start_ms <= item["timestamp_ms"] < end_ms)
    if not inside:
        return _alignment_result("failed", 0.0, (), "trace_outside_challenge_window")

    first_raw = trajectory[0]["timestamp_ms"]
    last_raw = trajectory[-1]["timestamp_ms"]
    overlap_start = max(first_raw, start_ms)
    overlap_end = min(last_raw, end_ms)
    covered_duration_ms = max(0, overlap_end - overlap_start)
    coverage_ratio = covered_duration_ms / duration_ms
    status = "aligned" if first_raw <= start_ms and last_raw >= end_ms else "partial"
    warnings = list(window_warnings)
    if status != "aligned":
        warnings.append("trace_coverage_partial")
    return {
        "timebase_version": ALIGNMENT_VERSION,
        "raw_clock_source": "system_wall_clock_epoch_ms",
        "anchor_source": alignment_provenance,
        "challenge_start_epoch_ms": start_ms,
        "challenge_end_epoch_ms": end_ms,
        "window_semantics": "half_open",
        "offset_ms": inside[0]["timestamp_ms"] - start_ms,
        "challenge_time_range_ms": [0, duration_ms],
        "status": status,
        "coverage_ratio": coverage_ratio,
        "covered_duration_ms": covered_duration_ms,
        "points": list(inside),
        "warnings": list(dict.fromkeys(warnings)),
    }


def analyze_native_flicking(
    points: Iterable[Mapping[str, Any] | Any] | None,
    performance: Mapping[str, Any] | Any | None,
    *,
    stats: Mapping[str, Any] | None = None,
    calibration: Mapping[str, Any] | None = None,
    canonical_window: Mapping[str, Any] | Any | None = None,
) -> dict[str, Any]:
    """Analyze raw mouse movement without video, target, or sensitivity guesses.

    ``stats`` is optional source-fact metadata. ``calibration`` is optional and
    only produces a second path-length metric when it explicitly declares
    ``raw_counts_per_unit``, ``unit``, ``provenance``, and availability
    ``available``. All baseline metrics retain raw-count units.
    """
    sources = {
        "raw_input": _source("raw_input", "kinematics", "missing" if points is None else "available"),
        "performance": _source(
            "performance",
            "event_anchor",
            "missing" if performance is None else "available",
        ),
    }
    if stats is not None:
        sources["stats"] = {
            **_source("stats", "scenario_config", "available"),
            "facts": dict(stats),
        }

    if points is None:
        alignment = _alignment_result("unavailable", None, (), "raw_input_missing")
        sources["raw_input"]["alignment"] = "unavailable"
        sources["performance"]["alignment"] = "unavailable"
        return _unavailable_result(sources, alignment, "raw_input_missing")

    try:
        trajectory = derive_trajectory(points)
    except NativeFlickingAnalysisError as exc:
        sources["raw_input"] = _source("raw_input", "kinematics", "invalid")
        alignment = _alignment_result("failed", 0.0, (), "raw_input_invalid")
        sources["raw_input"]["warnings"] = [str(exc)]
        sources["raw_input"]["alignment"] = "failed"
        sources["performance"]["alignment"] = "unavailable"
        return _unavailable_result(sources, alignment, "raw_input_invalid")

    alignment = align_points_to_challenge(
        trajectory,
        performance,
        canonical_window=canonical_window,
        _trajectory_is_derived=True,
    )
    sources["raw_input"]["alignment"] = alignment["status"]
    sources["performance"]["alignment"] = alignment["status"]
    if alignment["status"] == "unavailable":
        return _unavailable_result(sources, alignment, "performance_anchor_missing", trajectory)
    if alignment["status"] == "failed":
        return _unavailable_result(sources, alignment, "trace_outside_challenge_window", trajectory)

    aligned_points = alignment["points"]
    metrics = _kinematic_metrics(aligned_points, alignment["coverage_ratio"])
    calibrated = _calibrated_path_length(metrics["path_length"], calibration)
    if calibrated is not None:
        metrics["calibrated_path_length"] = calibrated

    challenge_start_ms = alignment.get("challenge_start_epoch_ms")
    challenge_end_ms = alignment.get("challenge_end_epoch_ms")
    if not isinstance(challenge_start_ms, int) or not isinstance(challenge_end_ms, int):
        return _unavailable_result(sources, alignment, "performance_anchor_missing", trajectory)
    click_anchors = _left_click_anchors(
        trajectory,
        challenge_start_ms,
        challenge_end_ms,
    )
    flick_events = _build_flick_events(
        aligned_points,
        anchors=click_anchors,
        challenge_start_ms=challenge_start_ms,
        coverage=alignment["coverage_ratio"],
        alignment_status=alignment["status"],
    )
    metrics.update(_session_flick_metrics(flick_events, alignment["coverage_ratio"]))

    status = "available" if alignment["status"] == "aligned" else "partial"
    limitations = ["target_relative_facts_unavailable"]
    if status == "partial":
        limitations.append("alignment_partial")
    if not click_anchors:
        limitations.append("left_click_anchors_missing")
    elif not flick_events:
        limitations.append("no_movement_clicks_ignored")

    timeline = _sorted_timeline(
        [
            *flick_events,
            *_performance_timeline(
                performance,
                duration_ms=challenge_end_ms - challenge_start_ms,
            ),
        ]
    )
    return {
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "status": status,
        "evidence": {
            "sources": sources,
            "alignment": _alignment_without_points(alignment),
            "coverage": alignment["coverage_ratio"],
            "warnings": list(alignment["warnings"]),
        },
        "deterministic": {
            "trajectory": {
                "unit": "raw_counts",
                "point_count": len(trajectory),
                "points": trajectory,
            },
            "metrics": metrics,
            "timeline": timeline,
        },
        "limitations": limitations,
    }


def build_native_static_evidence_extension(
    artifact: Mapping[str, Any],
    native_result: Mapping[str, Any] | None,
    *,
    raw_source_ref: str | None = None,
    scenario_profile_ref: str | None = None,
) -> dict[str, Any]:
    """Project an input-native result into the bounded evidence contract.

    This is deliberately an adapter: all metric values, versions, and native
    event segmentation remain those produced above.  The adapter only adds
    analysis-scoped refs and private derived sample sets; it never creates
    target-relative facts.
    """
    from .analysis_evidence import validate_analysis_evidence_artifact_v1

    projected = deepcopy(dict(artifact))
    if not isinstance(native_result, Mapping):
        return validate_analysis_evidence_artifact_v1(projected)
    analysis_ref = projected["analysis_ref"]
    window = projected["canonical_time_window"]
    window_start = int(window["start_ms"])
    window_end = int(window["end_ms"])
    deterministic = native_result.get("deterministic") or {}
    if not isinstance(raw_source_ref, str) or not raw_source_ref:
        return validate_analysis_evidence_artifact_v1(projected)
    raw_ref = raw_source_ref
    source_coverage = max(
        0.0,
        min(1.0, float((native_result.get("evidence") or {}).get("coverage") or 0.0)),
    )

    trajectory = deterministic.get("trajectory") or {}
    points = trajectory.get("points") if isinstance(trajectory, Mapping) else None
    if isinstance(points, list):
        projected["sample_sets"], projected["signal_bundles"] = _native_signal_bundles(
            points,
            analysis_ref=analysis_ref,
            window_start=window_start,
            window_end=window_end,
            source_ref=raw_ref,
            source_coverage=source_coverage,
        )

    flicks = [
        item for item in (deterministic.get("timeline") or [])
        if isinstance(item, Mapping) and item.get("event_type") == "flick"
    ]
    event_bundle, legacy_to_event = _native_event_bundle(
        flicks,
        analysis_ref=analysis_ref,
        window_start=window_start,
        window_end=window_end,
        source_ref=raw_ref,
    )
    projected["event_bundles"] = [event_bundle] if event_bundle["events"] else []
    projected["metric_records"] = _native_metric_records(
        deterministic.get("metrics") or {},
        source_ref=raw_ref,
        scenario_profile_ref=scenario_profile_ref,
        legacy_to_event=legacy_to_event,
    )
    projected["evidence_segments"] = _native_segments(
        flicks,
        projected["metric_records"],
        analysis_ref=analysis_ref,
        window_start=window_start,
        window_end=window_end,
        available_channels=[
            channel["channel_key"]
            for bundle in projected["signal_bundles"]
            for channel in bundle["channels"]
        ],
        source_coverage=source_coverage,
        legacy_to_event=legacy_to_event,
    )
    for metric in projected["metric_records"]:
        metric_ref = f"metric:{metric['metric_key']}@{metric['metric_version']}"
        metric["evidence_segment_refs"] = [
            segment["segment_id"]
            for segment in projected["evidence_segments"]
            if metric_ref in segment["metric_refs"]
        ]
    validate_analysis_evidence_artifact_v1(projected)
    return projected


def _native_signal_bundles(
    points: list[Mapping[str, Any]],
    *,
    analysis_ref: str,
    window_start: int,
    window_end: int,
    source_ref: str,
    source_coverage: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inside = [
        dict(point) for point in points
        if isinstance(point.get("timestamp_ms"), int)
        and window_start <= int(point["timestamp_ms"]) < window_end
    ]
    if not inside:
        return [], []
    base_x = int(inside[0].get("x_raw_counts") or 0)
    base_y = int(inside[0].get("y_raw_counts") or 0)
    sample_sets: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []

    def add_channel(key: str, unit: str, values: list[list[float | int]]) -> None:
        if not values:
            return
        sample_id = f"{analysis_ref}:samples:{key.replace('.', '-') }"
        sample_sets.append({"sample_set_id": sample_id, "channel_key": key, "unit": unit, "points": values})
        channels.append({
            "channel_key": key,
            "source_refs": [source_ref],
            "coordinate_space": "raw_input_counts",
            "unit": unit,
            "sample_rate_semantics": "irregular_source_order",
            "samples_ref": sample_id,
            "coverage": source_coverage,
            "confidence_summary": source_coverage,
            "transform_version": "native_flicking.trajectory.v1",
            "limitations": ["target_relative_facts_unavailable"],
        })

    add_channel(
        "mouse.position_x",
        "raw_counts",
        [[int(point["timestamp_ms"]), int(point.get("x_raw_counts") or 0) - base_x] for point in inside],
    )
    add_channel(
        "mouse.position_y",
        "raw_counts",
        [[int(point["timestamp_ms"]), int(point.get("y_raw_counts") or 0) - base_y] for point in inside],
    )
    speed_samples = _speed_samples(inside)
    add_channel("mouse.speed", "raw_counts_per_second", [[timestamp, speed] for timestamp, speed, _ in speed_samples])
    acceleration_samples = _acceleration_samples(speed_samples)
    add_channel(
        "mouse.acceleration",
        "raw_counts_per_second_squared",
        [[timestamp, acceleration] for timestamp, acceleration, _ in acceleration_samples],
    )
    return sample_sets, [{
        "schema_version": "signal_bundle.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "visual_quality_profile_ref": None,
        "observed_visual_domain": None,
        "channels": channels,
    }] if channels else []


def _native_event_bundle(
    flicks: list[Mapping[str, Any]],
    *,
    analysis_ref: str,
    window_start: int,
    window_end: int,
    source_ref: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    events: list[dict[str, Any]] = []
    legacy_to_event: dict[str, str] = {}
    attr_keys = {
        "movement_duration_ms", "time_to_peak_ms", "accel_duration_ms", "decel_duration_ms",
        "settle_duration_ms", "decel_frac", "peak_position_pct", "peak_speed", "path_length",
        "displacement", "path_efficiency", "straightness", "reverse_ratio",
        "direction_reverse_ratio", "corrective_count", "submovement_count",
        "trough_depth_ratio", "submovement_overlap", "sparc",
    }
    for index, flick in enumerate(flicks, 1):
        legacy_id = flick.get("id")
        if not isinstance(legacy_id, str):
            legacy_id = f"flick:{index}"
        event_id = f"{analysis_ref}:event:static-flick:{index}"
        start = _native_integral_ms(flick.get("start_ms"), window_start)
        movement_end = _native_integral_ms(flick.get("end_ms"), window_start)
        settle_end = _native_integral_ms(flick.get("settle_end_ms"), window_start)
        if start is None or movement_end is None or settle_end is None:
            continue
        end = min(window_end, max(start, settle_end))
        if not (window_start <= start < window_end and start <= end <= window_end):
            continue
        metrics = flick.get("metrics") or {}
        attributes: dict[str, Any] = {
            "legacy_event_ref": legacy_id,
            "peak_ms": _native_integral_ms(flick.get("peak_ms"), window_start),
            "settle_end_ms": settle_end,
            "quality": str(flick.get("quality") or "available"),
        }
        for key in attr_keys:
            value = metrics.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                continue
            attributes[key] = value
        attributes = {key: value for key, value in attributes.items() if value is not None}
        limitations = list(dict.fromkeys(str(item) for item in (flick.get("limitations") or []) if isinstance(item, str)))
        events.append({
            "event_id": event_id,
            "event_kind": "static_flick",
            "start_ms": start,
            "end_ms": end,
            "actor_refs": [],
            "source_refs": [source_ref],
            "confidence": max(0.0, min(1.0, float(flick.get("coverage") or 0.0))),
            "attributes": attributes,
            "limitations": limitations,
        })
        legacy_to_event[legacy_id] = event_id
    return {"schema_version": "event_bundle.v1", "analysis_ref": analysis_ref, "events": events, "outcome_associations": []}, legacy_to_event


def _native_integral_ms(value: Any, offset: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        return None
    if int(value) != value:
        return None
    return int(value) + offset


def _native_metric_records(
    metrics: Mapping[str, Any],
    *,
    source_ref: str,
    scenario_profile_ref: str | None,
    legacy_to_event: Mapping[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key, legacy in metrics.items():
        if not isinstance(key, str) or not isinstance(legacy, Mapping):
            continue
        value = legacy.get("value")
        availability = legacy.get("availability")
        if availability not in {"available", "partial", "unavailable"}:
            continue
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            continue
        metric_key = f"static_clicking.{key}"
        metric_version = str(legacy.get("metric_version") or METRIC_VERSION)
        sample_count = int(legacy.get("sample_count") or 0)
        valid_count = sample_count if availability != "unavailable" else 0
        limitations = list(dict.fromkeys(str(item) for item in (legacy.get("limitations") or []) if isinstance(item, str)))
        distribution = None
        if any(field in legacy for field in ("min", "p25", "median", "p75", "p90", "max")):
            distribution = {
                "min": legacy.get("min"), "p10": None, "p25": legacy.get("p25"),
                "median": legacy.get("median", value), "p75": legacy.get("p75"),
                "p90": legacy.get("p90"), "max": legacy.get("max"), "histogram_bins": [],
            }
            limitations.append("p10_and_histogram_not_emitted_by_native_adapter")
        condition_refs = [scenario_profile_ref] if isinstance(scenario_profile_ref, str) else []
        calibration_ref = legacy.get("calibration_ref")
        if isinstance(calibration_ref, str):
            condition_refs.append(calibration_ref)
        event_refs = [
            legacy_to_event[ref]
            for ref in [*(legacy.get("outlier_refs") or []), *(legacy.get("sample_refs") or [])]
            if isinstance(ref, str) and ref in legacy_to_event
        ]
        records.append({
            "schema_version": "metric_record.v1",
            "metric_key": metric_key,
            "metric_version": metric_version,
            "value": None if availability == "unavailable" else value,
            "unit": str(legacy.get("unit") or "source_native"),
            "availability": availability,
            "classification": "deterministic",
            "provenance": {"kind": "derived", "source_refs": [source_ref]},
            "population": {"sample_count": max(0, sample_count), "valid_count": max(0, valid_count), "excluded_count": 0},
            "distribution": distribution,
            "condition_refs": list(dict.fromkeys(condition_refs)),
            "event_refs": list(dict.fromkeys(event_refs)),
            "evidence_segment_refs": [],
            "coverage": legacy.get("coverage"),
            "confidence": legacy.get("coverage"),
            "limitations": list(dict.fromkeys(limitations)),
        })
    return records


def _native_segments(
    flicks: list[Mapping[str, Any]],
    metric_records: list[dict[str, Any]],
    *,
    analysis_ref: str,
    window_start: int,
    window_end: int,
    available_channels: list[str],
    source_coverage: float,
    legacy_to_event: dict[str, str],
) -> list[dict[str, Any]]:
    candidates: list[tuple[int, str, float]] = []
    for index, flick in enumerate(flicks, 1):
        legacy_id = flick.get("id") if isinstance(flick.get("id"), str) else f"flick:{index}"
        event_id = legacy_to_event.get(legacy_id)
        value = (flick.get("metrics") or {}).get("corrective_count")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
            value = (flick.get("metrics") or {}).get("peak_speed")
        if event_id is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
            candidates.append((index, event_id, float(value)))
    if not candidates:
        return []
    values = sorted(item[2] for item in candidates)
    median = values[(len(values) - 1) // 2]
    distances = {event_id: abs(value - median) for _, event_id, value in candidates}
    uses_correction_rank = any(
        isinstance((flicks[index - 1].get("metrics") or {}).get("corrective_count"), (int, float))
        and not isinstance((flicks[index - 1].get("metrics") or {}).get("corrective_count"), bool)
        for index, _, _ in candidates
    )
    if len(candidates) == 1:
        selected: list[tuple[str, tuple[int, str, float]]] = [("typical", candidates[0])]
    else:
        worst = (
            max(candidates, key=lambda item: (item[2], -item[0]))
            if uses_correction_rank
            else max(candidates, key=lambda item: (distances[item[1]], item[2], -item[0]))
        )
        first_half = candidates[: max(1, len(candidates) // 2)]
        later = candidates[len(first_half):]
        improved = None
        if later:
            if uses_correction_rank:
                first_score = sorted(item[2] for item in first_half)[(len(first_half) - 1) // 2]
                candidate = min(later, key=lambda item: (item[2], item[0]))
                improved_condition = candidate[2] < first_score
            else:
                first_score = sorted(abs(item[2] - median) for item in first_half)[(len(first_half) - 1) // 2]
                candidate = min(later, key=lambda item: (abs(item[2] - median), item[0]))
                improved_condition = abs(candidate[2] - median) < first_score
            if improved_condition:
                improved = candidate
        selected = [("worst", worst)]
        if improved is not None and improved[1] not in {candidate[1] for _, candidate in selected}:
            selected.append(("improved", improved))
        remaining = [item for item in candidates if item[1] not in {candidate[1] for _, candidate in selected}]
        if remaining:
            typical = min(remaining, key=lambda item: (distances[item[1]], item[0]))
            selected.append(("typical", typical))
    segments: list[dict[str, Any]] = []
    rank_metric_key = "static_clicking.corrective_count" if uses_correction_rank else "static_clicking.peak_speed"
    rank_metric = next(
        (metric for metric in metric_records if metric["metric_key"] == rank_metric_key),
        None,
    )
    metric_ref = (
        f"metric:{rank_metric_key}@{rank_metric['metric_version']}"
        if rank_metric is not None
        else f"metric:static_clicking.peak_speed@{METRIC_VERSION}"
    )
    for rank_reason, (index, event_id, _) in selected:
        flick = flicks[index - 1]
        start = _native_integral_ms(flick.get("start_ms"), window_start)
        movement_end = _native_integral_ms(flick.get("end_ms"), window_start)
        settle_end = _native_integral_ms(flick.get("settle_end_ms"), window_start)
        if start is None or movement_end is None or settle_end is None:
            continue
        end = min(window_end, max(start + 1, settle_end))
        truncated = end - start > 12_000
        if truncated:
            end = min(window_end, start + 12_000)
        if not (window_start <= start < end <= window_end):
            continue
        focus_end = min(end, max(start + 1, movement_end))
        limitations = ["within_run_rank_not_learning_effect", "descriptive_rank_not_health_threshold"]
        if len(candidates) == 1:
            limitations.append("insufficient_events_for_within_run_rank")
        if truncated:
            limitations.append("segment_truncated_to_12s")
        if rank_reason == "improved":
            limitations = ["within_run_late_relative_improvement_not_learning_effect", "descriptive_rank_not_health_threshold"]
        segments.append({
            "schema_version": "evidence_segment.v1",
            "segment_id": f"{analysis_ref}:segment:{rank_reason}:{index}",
            "analysis_ref": analysis_ref,
            "analyzer_ref": METRIC_VERSION,
            "segment_kind": rank_reason,
            "start_ms": start,
            "end_ms": end,
            "focus_start_ms": start,
            "focus_end_ms": focus_end,
            "title_key": f"static_clicking.{rank_reason}",
            "rank_reason": rank_reason,
            "issue_refs": [],
            "metric_refs": [metric_ref],
            "event_refs": [event_id],
            "available_channels": available_channels,
            "source_coverage": max(0.0, min(1.0, source_coverage)),
            "confidence": max(0.0, min(1.0, source_coverage)),
            "video_playback": {"availability": "unavailable", "artifact_ref": None, "start_ms": None, "end_ms": None},
            "limitations": limitations,
        })
    return segments


def _left_click_anchors(
    points: list[dict[str, int]],
    start_ms: int,
    end_ms: int,
) -> list[int]:
    """Return challenge-window timestamps for left-button rising edges."""
    anchors: list[int] = []
    previous_pressed = False
    for point in points:
        pressed = bool(point["buttons"] & 1)
        if pressed and not previous_pressed and start_ms <= point["timestamp_ms"] < end_ms:
            anchors.append(point["timestamp_ms"])
        previous_pressed = pressed
    return anchors


def _speed_buckets(points: list[dict[str, int]]) -> list[dict[str, float | int]]:
    """Aggregate speed at each timestamp while preserving per-record path."""
    buckets: list[dict[str, float | int]] = []
    for point in points:
        timestamp_ms = point["timestamp_ms"]
        distance = hypot(point["dx"], point["dy"])
        if buckets and buckets[-1]["timestamp_ms"] == timestamp_ms:
            buckets[-1]["distance"] += distance
        else:
            buckets.append({"timestamp_ms": timestamp_ms, "distance": distance})

    for index, bucket in enumerate(buckets):
        if index == 0:
            bucket["interval_start_ms"] = int(bucket["timestamp_ms"])
            bucket["duration_ms"] = 0
            bucket["speed"] = 0.0
            continue
        bucket["interval_start_ms"] = int(buckets[index - 1]["timestamp_ms"])
        elapsed_ms = int(bucket["timestamp_ms"]) - int(bucket["interval_start_ms"])
        if elapsed_ms <= 0:
            raise NativeFlickingAnalysisError("raw input timestamps are not monotonic")
        bucket["duration_ms"] = elapsed_ms
        bucket["speed"] = float(bucket["distance"]) * 1_000 / elapsed_ms
    return buckets


def _event_points(
    points: list[dict[str, int]],
    start_ms: int,
    end_ms: int,
    *,
    include_start: bool = False,
) -> list[dict[str, int]]:
    return [
        point
        for point in points
        if (
            start_ms <= point["timestamp_ms"] <= end_ms
            if include_start
            else start_ms < point["timestamp_ms"] <= end_ms
        )
    ]


def _event_sparc(
    speed_buckets: list[dict[str, float | int]],
) -> tuple[float | None, dict[str, int | float | None], str | None]:
    """Compute SPARC on an integer-ms grid with normalized frequency scale."""
    if not speed_buckets:
        return None, {
            "source_sample_count": 0,
            "resample_step_ms": None,
            "resampled_sample_count": 0,
        }, "sparc_requires_at_least_eight_resampled_samples"

    durations = [
        int(bucket["duration_ms"])
        for bucket in speed_buckets
        if int(bucket["duration_ms"]) > 0
    ]
    if not durations:
        return None, {
            "source_sample_count": len(speed_buckets),
            "resample_step_ms": None,
            "resampled_sample_count": 0,
        }, "sparc_requires_at_least_eight_resampled_samples"

    step_ms = durations[0]
    for duration_ms in durations[1:]:
        step_ms = gcd(step_ms, duration_ms)
    resampled: list[float] = []
    for bucket in speed_buckets:
        duration_ms = int(bucket["duration_ms"])
        repeats = max(1, duration_ms // step_ms)
        resampled.extend([float(bucket["speed"])] * repeats)

    sampling = {
        "source_sample_count": len(speed_buckets),
        "resample_step_ms": step_ms,
        "resampled_sample_count": len(resampled),
    }
    if len(resampled) < 8:
        return None, sampling, "sparc_requires_at_least_eight_resampled_samples"

    speed = np.asarray(resampled, dtype=float)
    spectrum = np.abs(np.fft.rfft(speed))
    dc = float(spectrum[0])
    if dc <= 0:
        return None, sampling, "sparc_spectrum_unresolved"
    spectrum = spectrum / dc
    fps = 1_000.0 / step_ms
    freqs = np.fft.rfftfreq(len(speed), d=1.0 / fps)
    above = np.where(spectrum > 0.05)[0]
    if above.size == 0 or int(above.max()) < 2:
        return None, sampling, "sparc_spectrum_unresolved"
    cutoff = int(above.max())
    f_values = freqs[1 : cutoff + 1]
    amplitude_values = spectrum[1 : cutoff + 1]
    frequency_span = float(f_values[-1] - f_values[0])
    if frequency_span <= 0:
        return None, sampling, "sparc_spectrum_unresolved"
    normalized_f = (f_values - f_values[0]) / frequency_span
    sparc = float(
        -np.sum(
            np.sqrt(
                np.diff(normalized_f) ** 2
                + np.diff(amplitude_values) ** 2
            )
        )
    )
    if not isfinite(sparc):
        return None, sampling, "sparc_spectrum_unresolved"
    return sparc, sampling, "sparc_cross_polling_comparability_unverified"


def _event_quantities(
    points: list[dict[str, int]],
    speed_buckets: list[dict[str, float | int]],
    start_ms: int,
    anchor_ms: int,
    challenge_start_ms: int,
) -> dict[str, Any]:
    moving_points = [
        point for point in points if hypot(point["dx"], point["dy"]) > 0
    ]
    movement_start_ms = start_ms
    if moving_points:
        end_ms = moving_points[-1]["timestamp_ms"]
        event_buckets = [
            bucket
            for bucket in speed_buckets
            if start_ms < int(bucket["timestamp_ms"]) <= end_ms
        ]
        first_moving_timestamp = moving_points[0]["timestamp_ms"]
        event_buckets = [
            bucket
            for bucket in event_buckets
            if int(bucket["timestamp_ms"]) >= first_moving_timestamp
        ]
        if event_buckets:
            movement_start_ms = max(start_ms, int(event_buckets[0]["interval_start_ms"]))
    else:
        end_ms = anchor_ms
        event_buckets = []

    path_length = sum(hypot(point["dx"], point["dy"]) for point in points)
    net_dx = sum(point["dx"] for point in points)
    net_dy = sum(point["dy"] for point in points)
    displacement = hypot(net_dx, net_dy)
    limitations: list[str] = ["target_relative_facts_unavailable"]

    if path_length > 0 and displacement > 0:
        efficiency = displacement / path_length
    else:
        efficiency = None
        limitations.append("zero_net_displacement" if path_length > 0 else "zero_path_length")

    speeds = [float(bucket["speed"]) for bucket in event_buckets]
    if speeds:
        peak_position = max(range(len(speeds)), key=lambda index: speeds[index])
        peak_bucket = event_buckets[peak_position]
        peak_ms = int(peak_bucket["timestamp_ms"])
        peak_speed = speeds[peak_position]
    else:
        peak_position = None
        peak_ms = anchor_ms
        peak_speed = None

    if peak_position is not None:
        comparisons = list(zip(speeds[peak_position:], speeds[peak_position + 1 :]))
        reverse_ratio = (
            sum(1 for previous, current in comparisons if current > previous)
            / len(comparisons)
            if comparisons
            else 0.0
        )
    else:
        reverse_ratio = None

    primary_dx = sum(
        point["dx"]
        for point in points
        if point["timestamp_ms"] <= peak_ms
    )
    primary_dy = sum(
        point["dy"]
        for point in points
        if point["timestamp_ms"] <= peak_ms
    )
    if primary_dx == 0 and primary_dy == 0 and moving_points:
        primary_dx = moving_points[0]["dx"]
        primary_dy = moving_points[0]["dy"]
    primary_norm = hypot(primary_dx, primary_dy)

    reverse_path = 0.0
    corrective_count = 0
    in_corrective = False
    if primary_norm > 0:
        for point in moving_points:
            dot = point["dx"] * primary_dx + point["dy"] * primary_dy
            is_reverse = dot < 0
            if is_reverse:
                reverse_path += hypot(point["dx"], point["dy"])
                if point["timestamp_ms"] > peak_ms and not in_corrective:
                    corrective_count += 1
                in_corrective = point["timestamp_ms"] > peak_ms
            elif point["timestamp_ms"] > peak_ms:
                in_corrective = False

    direction_reverse_ratio = reverse_path / path_length if path_length > 0 else None
    submovement_count = (1 + corrective_count) if moving_points else 0

    trough_depth_ratio = None
    if peak_position is not None and peak_speed and len(speeds) >= 3:
        troughs = [
            speeds[index]
            for index in range(peak_position + 1, len(speeds) - 1)
            if speeds[index] <= speeds[index - 1]
            and speeds[index] < speeds[index + 1]
        ]
        if troughs:
            trough_depth_ratio = min(troughs) / peak_speed
    limitations.append("trough_depth_ratio_not_temporal_overlap")

    sparc, sampling, sparc_limitation = _event_sparc(event_buckets)
    if sparc_limitation is not None:
        limitations.append(sparc_limitation)
    if "sparc_cross_polling_comparability_unverified" not in limitations:
        limitations.append("sparc_cross_polling_comparability_unverified")
    limitations.append("reacceleration_ratio_is_discrete_speed_delta_sign")
    limitations.append("direction_reverse_ratio_is_raw_path_sign_change")
    limitations.append("corrective_counts_use_discrete_direction_sign_runs")

    return {
        "start_ms": float(movement_start_ms - challenge_start_ms),
        "peak_ms": float(peak_ms - challenge_start_ms),
        "end_ms": float(end_ms - challenge_start_ms),
        "settle_end_ms": float(anchor_ms - challenge_start_ms),
        "relative_ms": float(anchor_ms - challenge_start_ms),
        "metrics": {
            "movement_duration_ms": float(end_ms - movement_start_ms),
            "time_to_peak_ms": float(peak_ms - movement_start_ms),
            "accel_duration_ms": float(max(0, peak_ms - movement_start_ms)),
            "decel_duration_ms": float(max(0, end_ms - peak_ms)),
            "settle_duration_ms": float(max(0, anchor_ms - end_ms)),
            "decel_frac": (
                float(end_ms - peak_ms) / (end_ms - movement_start_ms)
                if end_ms > movement_start_ms and peak_position is not None
                else None
            ),
            "peak_position_pct": (
                100.0 * (peak_ms - movement_start_ms) / (end_ms - movement_start_ms)
                if end_ms > movement_start_ms and peak_position is not None
                else None
            ),
            "peak_speed": peak_speed,
            "path_length": float(path_length),
            "displacement": float(displacement),
            "path_efficiency": efficiency,
            "straightness": efficiency,
            "reverse_ratio": reverse_ratio,
            "direction_reverse_ratio": direction_reverse_ratio,
            "corrective_count": corrective_count,
            "submovement_count": submovement_count,
            "trough_depth_ratio": trough_depth_ratio,
            # Product compatibility key: this is the same trough-depth proxy,
            # not a literal temporal-overlap decomposition.
            "submovement_overlap": trough_depth_ratio,
            "sparc": sparc,
        },
        "sampling": sampling,
        "quality": "available",
        "limitations": limitations,
    }


def _build_flick_events(
    aligned_points: list[dict[str, int]],
    *,
    anchors: list[int],
    challenge_start_ms: int,
    coverage: float,
    alignment_status: str,
) -> list[dict[str, Any]]:
    if not anchors:
        return []

    aligned_speed_buckets = _speed_buckets(aligned_points)
    events: list[dict[str, Any]] = []
    first_observed_ms = aligned_points[0]["timestamp_ms"] if aligned_points else challenge_start_ms
    previous_anchor_ms = max(challenge_start_ms, first_observed_ms)
    for anchor_index, anchor_ms in enumerate(anchors):
        event_points = _event_points(
            aligned_points,
            previous_anchor_ms,
            anchor_ms,
            include_start=anchor_index == 0,
        )
        quantities = _event_quantities(
            event_points,
            aligned_speed_buckets,
            previous_anchor_ms,
            anchor_ms,
            challenge_start_ms,
        )
        limitations = list(quantities["limitations"])
        if quantities["metrics"]["path_length"] <= 0:
            previous_anchor_ms = anchor_ms
            continue
        if alignment_status == "partial":
            limitations.append("alignment_partial")
        event_index = len(events) + 1
        events.append(
            {
                "id": f"flick:{event_index}",
                "event_type": "flick",
                "source": "raw_input",
                "segmentation_basis": "left_button_press",
                "start_ms": quantities["start_ms"],
                "peak_ms": quantities["peak_ms"],
                "end_ms": quantities["end_ms"],
                "settle_end_ms": quantities["settle_end_ms"],
                "relative_ms": quantities["relative_ms"],
                "metrics": quantities["metrics"],
                "sampling": quantities["sampling"],
                "quality": "partial" if alignment_status == "partial" else quantities["quality"],
                "coverage": coverage,
                "limitations": limitations,
            }
        )
        previous_anchor_ms = anchor_ms
    return events


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, min(len(ordered), ceil(percentile * len(ordered))))
    return ordered[rank - 1]


def _session_flick_metrics(
    events: list[dict[str, Any]], coverage: float,
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {
        "flick_count": _metric(
            "flick_count",
            float(len(events)),
            "count",
            sample_count=len(events),
            coverage=coverage,
            limitations=["alignment_partial"] if coverage < 1.0 else [],
        )
    }
    if not events:
        return metrics

    units = {
        "movement_duration_ms": "ms",
        "time_to_peak_ms": "ms",
        "accel_duration_ms": "ms",
        "decel_duration_ms": "ms",
        "settle_duration_ms": "ms",
        "decel_frac": "dimensionless",
        "peak_position_pct": "percent",
        "peak_speed": "raw_counts_per_second",
        "path_length": "raw_counts",
        "displacement": "raw_counts",
        "path_efficiency": "dimensionless",
        "straightness": "dimensionless",
        "reverse_ratio": "dimensionless",
        "direction_reverse_ratio": "dimensionless",
        "corrective_count": "count",
        "submovement_count": "count",
        "trough_depth_ratio": "dimensionless",
        "submovement_overlap": "dimensionless",
        "sparc": "dimensionless",
    }
    for key, unit in units.items():
        values = [
            float(event["metrics"][key])
            for event in events
            if isinstance(event["metrics"].get(key), (int, float))
            and not isinstance(event["metrics"].get(key), bool)
            and isfinite(float(event["metrics"][key]))
        ]
        if not values:
            continue
        p25 = _nearest_rank(values, 0.25)
        median = _nearest_rank(values, 0.50)
        p75 = _nearest_rank(values, 0.75)
        p90 = _nearest_rank(values, 0.90)
        lower_fence = p25 - 1.5 * (p75 - p25)
        upper_fence = p75 + 1.5 * (p75 - p25)
        outlier_refs = [
            event["id"]
            for event in events
            if isinstance(event["metrics"].get(key), (int, float))
            and not isinstance(event["metrics"].get(key), bool)
            and (
                float(event["metrics"][key]) < lower_fence
                or float(event["metrics"][key]) > upper_fence
            )
        ]
        sample_refs = [
            event["id"]
            for event in events
            if isinstance(event["metrics"].get(key), (int, float))
            and not isinstance(event["metrics"].get(key), bool)
            and isfinite(float(event["metrics"][key]))
        ]
        distribution_limitations = ["descriptive_distribution_not_health_threshold"]
        if key == "sparc":
            distribution_limitations.append("sparc_cross_polling_comparability_unverified")
        if key == "reverse_ratio":
            distribution_limitations.append("reacceleration_ratio_is_discrete_speed_delta_sign")
        if key == "direction_reverse_ratio":
            distribution_limitations.append("direction_reverse_ratio_is_raw_path_sign_change")
        if key in {"corrective_count", "submovement_count"}:
            distribution_limitations.append("corrective_counts_use_discrete_direction_sign_runs")
        if key in {"trough_depth_ratio", "submovement_overlap"}:
            distribution_limitations.append("trough_depth_ratio_not_temporal_overlap")
        if coverage < 1.0:
            distribution_limitations.append("alignment_partial")
        output_key = "flick_path_length" if key == "path_length" else key
        metric = _metric(
            output_key,
            median,
            unit,
            sample_count=len(values),
            coverage=coverage,
            limitations=distribution_limitations,
        )
        if key == "sparc":
            metric["metric_version"] = SPARC_METRIC_VERSION
        metric.update(
            {
                "mean": float(np.mean(values)),
                "std": float(np.std(values)),
                "min": min(values),
                "max": max(values),
                "median": median,
                "p25": p25,
                "p75": p75,
                "p90": p90,
                "iqr": p75 - p25,
                "outlier_method": "tukey_1_5_iqr_descriptive",
                "outlier_refs": outlier_refs,
                "sample_refs": sample_refs,
            }
        )
        metrics[output_key] = metric
    return metrics


def _sorted_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(timeline, key=lambda item: float(item.get("relative_ms", 0.0)))


def _normalize_points(points: Iterable[Mapping[str, Any] | Any]) -> list[dict[str, int]]:
    if isinstance(points, (str, bytes)):
        raise NativeFlickingAnalysisError("points must be canonical point records")

    normalized: list[dict[str, int]] = []
    previous_timestamp: int | None = None
    try:
        iterator = iter(points)
    except TypeError as exc:
        raise NativeFlickingAnalysisError("points must be an iterable") from exc

    for index, raw in enumerate(iterator):
        timestamp_ms = _require_integer(raw, "timestamp_ms", index)
        dx = _require_integer(raw, "dx", index)
        dy = _require_integer(raw, "dy", index)
        buttons = _require_integer(raw, "buttons", index)
        if buttons < 0:
            raise NativeFlickingAnalysisError(f"point {index} buttons must be non-negative")
        if previous_timestamp is not None and timestamp_ms < previous_timestamp:
            raise NativeFlickingAnalysisError("raw input timestamps are not monotonic")
        previous_timestamp = timestamp_ms
        normalized.append(
            {
                "timestamp_ms": timestamp_ms,
                "dx": dx,
                "dy": dy,
                "buttons": buttons,
            }
        )
    return normalized


def _require_integer(record: Mapping[str, Any] | Any, key: str, index: int) -> int:
    value = _value(record, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeFlickingAnalysisError(f"point {index} {key} must be an integer")
    return value


def _canonical_window_context(
    window: Mapping[str, Any] | Any | None,
) -> tuple[int, int, str, tuple[str, ...]] | None:
    if window is None:
        return None
    if _value(window, "schema_version") != "canonical_time_window.v1":
        return None
    start_ms = _value(window, "start_ms")
    end_ms = _value(window, "end_ms")
    duration_ms = _value(window, "duration_ms")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (start_ms, end_ms, duration_ms)):
        return None
    if start_ms < 0 or end_ms <= start_ms or duration_ms != end_ms - start_ms:
        return None
    start_source = _value(window, "start_source")
    end_source = _value(window, "end_source")
    if not isinstance(start_source, str) or not start_source or not isinstance(end_source, str) or not end_source:
        return None
    raw_warnings = _value(window, "warnings") or ()
    if not isinstance(raw_warnings, (list, tuple)) or not all(
        isinstance(item, str) and item for item in raw_warnings
    ):
        return None
    return start_ms, end_ms, start_source + "+" + end_source, tuple(raw_warnings)


def _performance_context(
    performance: Mapping[str, Any] | Any | None,
) -> tuple[int, int, str, tuple[str, ...]] | None:
    if performance is None:
        return None
    header = _value(performance, "header") or performance
    anchor = _value(header, "challenge_start_utc")
    if isinstance(anchor, bool) or not isinstance(anchor, int):
        return None

    # Existing analysis snapshots carry an explicitly supplied duration. Keep
    # this legacy path readable; new PerformanceData inputs use v2 below.
    direct_duration = _value(performance, "time_limit_ms")
    if direct_duration is None:
        direct_duration = _value(header, "time_limit_ms")
    if isinstance(direct_duration, (int, float)) and not isinstance(direct_duration, bool):
        if direct_duration > 0 and int(direct_duration) == direct_duration:
            try:
                reject_pause_evidence(
                    performance,
                    float(_value(performance, "pause_duration_seconds") or 0.0),
                    pause_count=_value(performance, "pause_count"),
                )
            except (TimeAlignmentError, TypeError, ValueError):
                return None
            return (
                anchor,
                anchor + int(direct_duration),
                "legacy.performance.challenge_start_utc+time_limit_ms",
                (),
            )

    try:
        window = resolve_time_window(
            performance if _value(performance, "header") is not None else header,
            stats_challenge_start_epoch_ms=_value(performance, "stats_challenge_start_epoch_ms"),
            stats_event_times_seconds=_value(performance, "stats_event_times_seconds") or (),
            performance_event_times_seconds=_value(performance, "performance_event_times_seconds") or (),
            pause_count=_value(performance, "pause_count"),
            pause_duration_seconds=float(_value(performance, "pause_duration_seconds") or 0.0),
        )
    except (TimeAlignmentError, TypeError, ValueError):
        return None
    return (
        window.start_ms,
        window.end_ms,
        window.start_source + "+" + window.end_source,
        window.warnings,
    )


def _alignment_result(
    status: str,
    coverage_ratio: float | None,
    points: tuple[dict[str, int], ...],
    warning: str,
) -> dict[str, Any]:
    return {
        "timebase_version": ALIGNMENT_VERSION,
        "raw_clock_source": "system_wall_clock_epoch_ms",
        "anchor_source": "performance.challenge_start_utc",
        "offset_ms": None,
        "challenge_time_range_ms": None,
        "status": status,
        "coverage_ratio": coverage_ratio,
        "covered_duration_ms": None if coverage_ratio is None else 0,
        "points": list(points),
        "warnings": [warning],
    }


def _alignment_without_points(alignment: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in alignment.items() if key != "points"}


def _kinematic_metrics(points: list[dict[str, int]], coverage: float) -> dict[str, dict[str, Any]]:
    path_length = sum(hypot(point["dx"], point["dy"]) for point in points)
    speed_samples = _speed_samples(points)
    acceleration_samples = _acceleration_samples(speed_samples)
    return {
        "path_length": _metric(
            "path_length",
            path_length,
            "raw_counts",
            sample_count=len(points),
            coverage=coverage,
        ),
        "mean_speed": _sample_metric(
            "mean_speed",
            speed_samples,
            "raw_counts_per_second",
            coverage,
            "requires_at_least_two_points_with_positive_time_delta",
        ),
        "mean_acceleration": _sample_metric(
            "mean_acceleration",
            acceleration_samples,
            "raw_counts_per_second_squared",
            coverage,
            "requires_three_points_with_positive_time_deltas",
        ),
    }


def _speed_samples(points: list[dict[str, int]]) -> list[tuple[int, float, int]]:
    timestamp_buckets: list[tuple[int, float]] = []
    for point in points:
        timestamp_ms = point["timestamp_ms"]
        distance = hypot(point["dx"], point["dy"])
        if timestamp_buckets and timestamp_buckets[-1][0] == timestamp_ms:
            previous_timestamp, previous_distance = timestamp_buckets[-1]
            timestamp_buckets[-1] = (previous_timestamp, previous_distance + distance)
        else:
            timestamp_buckets.append((timestamp_ms, distance))

    samples: list[tuple[int, float, int]] = []
    for previous, current in zip(timestamp_buckets, timestamp_buckets[1:]):
        elapsed_ms = current[0] - previous[0]
        samples.append((current[0], current[1] * 1_000 / elapsed_ms, elapsed_ms))
    return samples


def _acceleration_samples(
    speed_samples: list[tuple[int, float, int]],
) -> list[tuple[int, float, int]]:
    samples: list[tuple[int, float, int]] = []
    for previous, current in zip(speed_samples, speed_samples[1:]):
        elapsed_ms = current[0] - previous[0]
        samples.append((current[0], (current[1] - previous[1]) * 1_000 / elapsed_ms, elapsed_ms))
    return samples


def _metric(
    key: str,
    value: float,
    unit: str,
    *,
    sample_count: int,
    coverage: float,
    limitations: list[str] | None = None,
    provenance_sources: list[str] | None = None,
    calibration_ref: str | None = None,
) -> dict[str, Any]:
    metric = {
        "key": key,
        "value": value,
        "unit": unit,
        "availability": "available",
        "provenance": {
            "kind": "derived",
            "sources": provenance_sources or ["raw_input"],
        },
        "metric_version": METRIC_VERSION,
        "sample_count": sample_count,
        "coverage": coverage,
        "limitations": limitations or [],
    }
    if calibration_ref is not None:
        metric["calibration_ref"] = calibration_ref
    return metric


def _sample_metric(
    key: str,
    samples: list[tuple[int, float, int]],
    unit: str,
    coverage: float,
    missing_limitation: str,
) -> dict[str, Any]:
    if not samples:
        return {
            "key": key,
            "value": None,
            "unit": unit,
            "availability": "unavailable",
            "provenance": {"kind": "derived", "sources": ["raw_input"]},
            "metric_version": METRIC_VERSION,
            "sample_count": 0,
            "coverage": coverage,
            "limitations": [missing_limitation],
        }
    total_duration_ms = sum(sample[2] for sample in samples)
    weighted_mean = sum(sample[1] * sample[2] for sample in samples) / total_duration_ms
    return _metric(
        key,
        weighted_mean,
        unit,
        sample_count=len(samples),
        coverage=coverage,
    )


def _calibrated_path_length(
    path_length: Mapping[str, Any], calibration: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if calibration is None or calibration.get("availability") != "available":
        return None
    scale = calibration.get("raw_counts_per_unit")
    unit = calibration.get("unit")
    provenance = calibration.get("provenance")
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or scale <= 0
        or not isinstance(unit, str)
        or not unit
        or not isinstance(provenance, str)
        or not provenance
    ):
        return None
    return _metric(
        "calibrated_path_length",
        path_length["value"] / scale,
        unit,
        sample_count=path_length["sample_count"],
        coverage=path_length["coverage"],
        provenance_sources=["raw_input", f"calibration:{provenance}"],
        calibration_ref=f"calibration:{provenance}",
    )


def _performance_timeline(
    performance: Mapping[str, Any] | Any | None,
    *,
    duration_ms: int | None = None,
) -> list[dict[str, Any]]:
    events = _value(performance, "events")
    if events is None:
        return []
    timeline: list[dict[str, Any]] = []
    for event in events:
        timestamp_ms = _value(event, "timestamp_ms")
        if timestamp_ms is None:
            timestamp = _value(event, "timestamp")
            if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                timestamp_ms = timestamp * 1_000
        if (
            not isinstance(timestamp_ms, (int, float))
            or isinstance(timestamp_ms, bool)
            or not isfinite(float(timestamp_ms))
            or timestamp_ms < 0
            or (duration_ms is not None and timestamp_ms >= duration_ms)
        ):
            continue
        item = {
            "relative_ms": float(timestamp_ms),
            "source": "performance",
            "payload_type": _value(event, "payload_type") or "",
        }
        for key in ("count", "delta", "value"):
            value = _value(event, key)
            if value is not None:
                item[key] = value
        timeline.append(item)
    return timeline


def _unavailable_result(
    sources: dict[str, dict[str, Any]],
    alignment: dict[str, Any],
    limitation: str,
    trajectory: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    return {
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "status": "unavailable",
        "evidence": {
            "sources": sources,
            "alignment": _alignment_without_points(alignment),
            "coverage": alignment["coverage_ratio"],
            "warnings": list(alignment["warnings"]),
        },
        "deterministic": {
            "trajectory": {
                "unit": "raw_counts",
                "point_count": len(trajectory or []),
                "points": trajectory or [],
            },
            "metrics": {},
            "timeline": [],
        },
        "limitations": [limitation],
    }


def _source(kind: str, role: str, availability: str) -> dict[str, Any]:
    return {
        "source": kind,
        "role": role,
        "availability": availability,
        "alignment": "not_required",
        "warnings": [],
    }


def _value(record: Mapping[str, Any] | Any | None, key: str) -> Any:
    if record is None:
        return None
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)


__all__ = [
    "NativeFlickingAnalysisError",
    "align_points_to_challenge",
    "analyze_native_flicking",
    "build_native_static_evidence_extension",
    "derive_trajectory",
]
