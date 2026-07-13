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
from math import hypot
from typing import Any


METRIC_VERSION = "native_flicking.v1"
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

    status = "available" if alignment["status"] == "aligned" else "partial"
    limitations = [] if status == "available" else ["alignment_partial"]
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
            "timeline": _performance_timeline(performance),
        },
        "limitations": limitations,
    }


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
