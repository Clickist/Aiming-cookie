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
from math import ceil, gcd, hypot, isfinite
from typing import Any

import numpy as np


METRIC_VERSION = "native_flicking.v1"
SPARC_METRIC_VERSION = "native_flicking.sparc.v2"
ALIGNMENT_VERSION = "time_alignment.v1"


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
    performance: Mapping[str, Any] | Any | None,
) -> dict[str, Any]:
    """Align canonical epoch records to a Performance challenge window.

    ``performance`` may be a mapping or the existing ``PerformanceData`` object.
    It must expose ``challenge_start_utc`` and a positive time limit, either as
    ``time_limit_ms`` or a ``challenge_profile.time_limit`` value in seconds.
    The result has one of ``aligned``, ``partial``, ``failed``, or ``unavailable``.
    """
    if points is None:
        return _alignment_result("unavailable", None, (), "raw_input_missing")

    trajectory = derive_trajectory(points)
    context = _performance_context(performance)
    if context is None:
        return _alignment_result("unavailable", None, (), "performance_anchor_missing")

    start_ms, duration_ms = context
    end_ms = start_ms + duration_ms
    inside = tuple(item for item in trajectory if start_ms <= item["timestamp_ms"] <= end_ms)
    if not inside:
        return _alignment_result("failed", 0.0, (), "trace_outside_challenge_window")

    first_raw = trajectory[0]["timestamp_ms"]
    last_raw = trajectory[-1]["timestamp_ms"]
    overlap_start = max(first_raw, start_ms)
    overlap_end = min(last_raw, end_ms)
    covered_duration_ms = max(0, overlap_end - overlap_start)
    coverage_ratio = covered_duration_ms / duration_ms
    status = "aligned" if first_raw <= start_ms and last_raw >= end_ms else "partial"
    warnings = () if status == "aligned" else ("trace_coverage_partial",)
    return {
        "timebase_version": ALIGNMENT_VERSION,
        "raw_clock_source": "system_wall_clock_epoch_ms",
        "anchor_source": "performance.challenge_start_utc",
        "offset_ms": inside[0]["timestamp_ms"] - start_ms,
        "challenge_time_range_ms": [0, duration_ms],
        "status": status,
        "coverage_ratio": coverage_ratio,
        "covered_duration_ms": covered_duration_ms,
        "points": list(inside),
        "warnings": list(warnings),
    }


def analyze_native_flicking(
    points: Iterable[Mapping[str, Any] | Any] | None,
    performance: Mapping[str, Any] | Any | None,
    *,
    stats: Mapping[str, Any] | None = None,
    calibration: Mapping[str, Any] | None = None,
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

    alignment = align_points_to_challenge(trajectory, performance)
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

    performance_context = _performance_context(performance)
    if performance_context is None:
        return _unavailable_result(sources, alignment, "performance_anchor_missing", trajectory)
    challenge_start_ms, challenge_duration_ms = performance_context
    challenge_end_ms = challenge_start_ms + challenge_duration_ms
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

    timeline = _sorted_timeline([*flick_events, *_performance_timeline(performance)])
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
        if pressed and not previous_pressed and start_ms <= point["timestamp_ms"] <= end_ms:
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
) -> list[dict[str, int]]:
    return [
        point
        for point in points
        if start_ms < point["timestamp_ms"] <= end_ms
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
    for anchor_ms in anchors:
        event_points = _event_points(aligned_points, previous_anchor_ms, anchor_ms)
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


def _performance_context(performance: Mapping[str, Any] | Any | None) -> tuple[int, int] | None:
    if performance is None:
        return None
    header = _value(performance, "header") or performance
    anchor = _value(header, "challenge_start_utc")
    if isinstance(anchor, bool) or not isinstance(anchor, int):
        return None

    duration_ms = _value(performance, "time_limit_ms")
    if duration_ms is None:
        duration_ms = _value(header, "time_limit_ms")
    if duration_ms is None:
        profile = _value(header, "challenge_profile")
        time_limit_seconds = _value(profile, "time_limit")
        if isinstance(time_limit_seconds, (int, float)) and not isinstance(time_limit_seconds, bool):
            duration_ms = time_limit_seconds * 1_000
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    if duration_ms <= 0 or int(duration_ms) != duration_ms:
        return None
    return anchor, int(duration_ms)


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


def _performance_timeline(performance: Mapping[str, Any] | Any | None) -> list[dict[str, Any]]:
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
        if not isinstance(timestamp_ms, (int, float)) or isinstance(timestamp_ms, bool):
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
    "derive_trajectory",
]
