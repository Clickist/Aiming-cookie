"""Deterministic, target-relative continuous tracking analysis.

The analyzer consumes already aligned numeric tracks.  It deliberately keeps
capture alignment latency separate from observed tracking timing and produces
descriptive measurements only; it does not infer a player mechanism.
"""

from __future__ import annotations

from copy import deepcopy
from math import hypot, isfinite
from statistics import median
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import coherence, periodogram


ANALYSIS_VERSION = "continuous_tracking.v1"
SCHEMA_VERSION = "continuous_tracking_analysis.v1"
INPUT_SCHEMA_VERSION = "continuous_tracking_input.v1"
_REF_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._@+-")


class TrackingAnalysisError(ValueError):
    pass


def _ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or any(char not in _REF_CHARS for char in value):
        raise TrackingAnalysisError(f"{field} is invalid")
    return value


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise TrackingAnalysisError(f"{field} is invalid")
    number = float(value)
    if minimum is not None and number < minimum:
        raise TrackingAnalysisError(f"{field} is invalid")
    return number


def _ratio(value: Any, field: str) -> float:
    number = _number(value, field)
    if not 0 <= number <= 1:
        raise TrackingAnalysisError(f"{field} is invalid")
    return number


def _points(raw: Any, field: str, *, radius: bool) -> list[dict[str, float | int | bool | None]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        raise TrackingAnalysisError(f"{field} must be non-empty")
    points: list[dict[str, float | int | bool | None]] = []
    previous: int | None = None
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise TrackingAnalysisError(f"{field}[{index}] is invalid")
        time_ms = _number(item.get("canonical_time_ms"), f"{field}[{index}].canonical_time_ms", minimum=0)
        if int(time_ms) != time_ms or (previous is not None and int(time_ms) <= previous):
            raise TrackingAnalysisError(f"{field} timestamps are not strictly ordered")
        confidence = _ratio(
            item.get("confidence", 1.0), f"{field}[{index}].confidence",
        )
        raw_complete = item.get("measurement_complete")
        if raw_complete is not None and not isinstance(raw_complete, bool):
            raise TrackingAnalysisError(
                f"{field}[{index}].measurement_complete is invalid"
            )
        point: dict[str, float | int | bool | None] = {
            "canonical_time_ms": int(time_ms),
            "x": _number(item.get("x"), f"{field}[{index}].x"),
            "y": _number(item.get("y"), f"{field}[{index}].y"),
            "confidence": confidence,
            "measurement_complete": (
                confidence == 1.0 if raw_complete is None else raw_complete
            ),
            "occluded": bool(item.get("occluded", False)),
        }
        if radius:
            raw_radius = item.get("radius")
            point["radius"] = None if raw_radius is None else _number(raw_radius, f"{field}[{index}].radius", minimum=0.01)
        points.append(point)
        previous = int(time_ms)
    return points


def _by_time(points: Sequence[Mapping[str, Any]], field: str) -> dict[int, Mapping[str, Any]]:
    output = {int(point["canonical_time_ms"]): point for point in points}
    if len(output) != len(points):
        raise TrackingAnalysisError(f"{field} timestamps are not unique")
    return output


def _velocity(points: Sequence[Mapping[str, Any]], index: int) -> tuple[float, float] | None:
    if not 0 < index < len(points):
        return None
    left, right = points[index - 1], points[index]
    delta = int(right["canonical_time_ms"]) - int(left["canonical_time_ms"])
    if delta <= 0:
        return None
    return (
        (float(right["x"]) - float(left["x"])) / delta,
        (float(right["y"]) - float(left["y"])) / delta,
    )


def _available(target: Mapping[str, Any], crosshair: Mapping[str, Any]) -> bool:
    return (
        not bool(target["occluded"])
        and not bool(crosshair["occluded"])
        and bool(target["measurement_complete"])
        and bool(crosshair["measurement_complete"])
    )


def _metric(
    key: str,
    values: Sequence[float | None],
    *,
    unit: str,
    event_refs: Sequence[str],
    analysis_ref: str,
    segment_refs: Sequence[str],
    condition_refs: Sequence[str] = (),
    limitations: Sequence[str] = (),
    confidence: float = 1.0,
) -> dict[str, Any]:
    valid = [float(value) for value in values if value is not None and isfinite(value)]
    ordered = sorted(valid)
    availability = "available" if valid else "unavailable"
    distribution = None
    if valid:
        def percentile(fraction: float) -> float:
            return ordered[int(round((len(ordered) - 1) * fraction))]
        distribution = {
            "min": ordered[0], "p10": percentile(.10), "p25": percentile(.25),
            "median": median(ordered), "p75": percentile(.75), "p90": percentile(.90),
            "max": ordered[-1], "histogram_bins": [],
        }
    return {
        "schema_version": "metric_record.v1",
        "metric_key": key,
        "metric_version": f"{key}.v1",
        "value": float(median(valid)) if valid else None,
        "unit": unit,
        "availability": availability,
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": [f"{analysis_ref}:source:tracking-analysis"],
        },
        "population": {
            "sample_count": len(values),
            "valid_count": len(valid),
            "excluded_count": len(values) - len(valid),
        },
        "distribution": distribution,
        "condition_refs": sorted(set(condition_refs)),
        "event_refs": list(event_refs),
        "evidence_segment_refs": list(segment_refs),
        "coverage": len(valid) / len(values) if values else 0.0,
        "confidence": confidence if valid else 0.0,
        "limitations": sorted(set(limitations)),
    }


def _tables(analysis_ref: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from .analysis_evidence import (
        tracking_processed_field_catalog_v1,
        validate_processed_event_table_v1,
    )

    tables: list[dict[str, Any]] = []
    for kind in sorted({row["row_kind"] for row in rows}):
        kind_rows = [row for row in rows if row["row_kind"] == kind]
        table = {
            "schema_version": "processed_event_table.v1",
            "table_ref": f"{analysis_ref}:table:{kind}",
            "analysis_ref": analysis_ref,
            "analyzer_ref": ANALYSIS_VERSION,
            "family": "continuous_tracking",
            "event_kind": kind,
            "row_count": len(kind_rows),
            "included_count": len(kind_rows),
            "excluded_count": 0,
            "completeness": "partial" if any(row["limitations"] for row in kind_rows) else "complete",
            "field_catalog": tracking_processed_field_catalog_v1(kind),
            "index_fields": ["start_ms", "end_ms", "target_track_ref"],
            "rows_ref": f"{analysis_ref}:table:{kind}",
            "limitations": sorted({limitation for row in kind_rows for limitation in row["limitations"]}),
        }
        tables.append(validate_processed_event_table_v1(table))
    return tables


def _frequency_metrics(samples: Sequence[Mapping[str, Any]], model: str) -> dict[str, float] | None:
    usable = [sample for sample in samples if sample["usable"]]
    if model not in {"predictable", "control"} or len(usable) < 64:
        return None
    times = np.asarray([sample["time_ms"] for sample in usable], dtype=float)
    deltas = np.diff(times)
    if not len(deltas) or np.max(np.abs(deltas - np.median(deltas))) > 1.0:
        return None
    target_xy = np.asarray([sample["target_position"] for sample in usable], dtype=float)
    crosshair_xy = np.asarray([sample["crosshair_position"] for sample in usable], dtype=float)
    centered = target_xy - np.mean(target_xy, axis=0)
    if not np.any(centered):
        return None
    _, _, axes = np.linalg.svd(centered, full_matrices=False)
    axis = axes[0]
    target_axis = centered @ axis
    crosshair_axis = (crosshair_xy - np.mean(crosshair_xy, axis=0)) @ axis
    if not np.any(crosshair_axis):
        return None
    chunks = np.array_split(target_axis, 4)
    target_range = float(np.ptp(target_axis))
    target_std = float(np.std(target_axis))
    chunk_stds = [float(np.std(chunk)) for chunk in chunks]
    if (
        target_range <= 0
        or target_std <= 0
        or any(len(chunk) < 16 for chunk in chunks)
        or max(float(np.mean(chunk)) for chunk in chunks)
        - min(float(np.mean(chunk)) for chunk in chunks) > 0.25 * target_range
        or max(chunk_stds) - min(chunk_stds) > 0.25 * target_std
    ):
        return None
    sample_rate_hz = 1000.0 / float(np.median(deltas))
    if len(target_axis) % 4:
        return None
    chunk_peak_bins = []
    for chunk in chunks:
        chunk_frequencies, chunk_power = periodogram(chunk, fs=sample_rate_hz)
        positive_bins = np.flatnonzero(chunk_frequencies > 0)
        if not len(positive_bins):
            return None
        chunk_peak_bins.append(int(positive_bins[np.argmax(chunk_power[positive_bins])]))
    if len(set(chunk_peak_bins)) != 1:
        return None
    frequencies, target_power = periodogram(target_axis, fs=sample_rate_hz)
    positive = np.flatnonzero(frequencies > 0)
    if not len(positive):
        return None
    dominant_index = int(positive[np.argmax(target_power[positive])])
    dominant_frequency = float(frequencies[dominant_index])
    duration_s = (times[-1] - times[0]) / 1000.0
    if (
        dominant_frequency <= 0
        or target_power[dominant_index] <= 0
        or dominant_frequency * duration_s < 3.0
    ):
        return None
    target_fft = np.fft.rfft(target_axis)
    crosshair_fft = np.fft.rfft(crosshair_axis)
    ratio = crosshair_fft[dominant_index] / target_fft[dominant_index]
    phase_lag_ms = float(-np.angle(ratio) / (2 * np.pi * dominant_frequency) * 1000.0)
    velocity_gain = float(abs(ratio))
    coherence_frequencies, coherence_values = coherence(
        target_axis,
        crosshair_axis,
        fs=sample_rate_hz,
        nperseg=min(64, len(target_axis)),
    )
    coherence_index = int(np.argmin(np.abs(coherence_frequencies - dominant_frequency)))
    result = {
        "phase_lag_ms": phase_lag_ms,
        "velocity_gain": velocity_gain,
        "coherence": float(coherence_values[coherence_index]),
    }
    return result if all(isfinite(value) for value in result.values()) else None


def _tracking_sparc(samples: Sequence[Mapping[str, Any]]) -> float | None:
    usable = [sample for sample in samples if sample["usable"]]
    if len(usable) < 16:
        return None
    times = np.asarray([sample["time_ms"] for sample in usable], dtype=float)
    deltas = np.diff(times)
    if not len(deltas) or np.max(np.abs(deltas - np.median(deltas))) > 1.0:
        return None
    speeds = np.asarray([
        hypot(*sample["crosshair_velocity"])
        for sample in usable
        if sample["crosshair_velocity"] is not None
    ])
    if len(speeds) < 15:
        return None
    from .flicking import _segment_sparc

    value = float(_segment_sparc(speeds, 1000.0 / float(np.median(deltas))))
    return value if isfinite(value) else None


def _predictability_events(
    payload: Mapping[str, Any], segment_ref: str, event_time_ms: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw = payload.get("predictability_evidence", [])
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TrackingAnalysisError("predictability_evidence must be a list")
    events: list[dict[str, Any]] = []
    accepted: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise TrackingAnalysisError("predictability evidence is invalid")
        if item.get("schema_version") != "motion_predictability_evidence.v1":
            raise TrackingAnalysisError("predictability evidence schema is unsupported")
        evidence_ref = _ref(
            item.get("evidence_ref"), f"predictability_evidence[{index}].evidence_ref",
        )
        if _ref(item.get("segment_ref"), f"predictability_evidence[{index}].segment_ref") != segment_ref:
            raise TrackingAnalysisError("predictability evidence is bound to another segment")
        kind = item.get("kind")
        if kind not in {"known_script", "periodicity", "repeatability", "model_fit"}:
            raise TrackingAnalysisError("predictability evidence kind is invalid")
        source_refs = item.get("source_refs")
        if isinstance(source_refs, (str, bytes)) or not isinstance(source_refs, Sequence) or not source_refs:
            raise TrackingAnalysisError("predictability evidence source_refs are required")
        validated_source_refs = [
            _ref(source_ref, "predictability evidence source_ref")
            for source_ref in source_refs
        ]
        model_ref = _ref(item.get("model_ref"), "predictability.model_ref")
        model_version = _ref(item.get("model_version"), "predictability.model_version")
        fit_metric = _ref(item.get("fit_metric"), "predictability.fit_metric")
        fit_metric_version = _ref(
            item.get("fit_metric_version"), "predictability.fit_metric_version",
        )
        fit_value = _number(item.get("fit_value"), "predictability.fit_value")
        threshold_ref = _ref(item.get("threshold_ref"), "predictability.threshold_ref")
        acceptance = item.get("acceptance")
        if acceptance not in {"accepted", "rejected"}:
            raise TrackingAnalysisError("predictability evidence acceptance is invalid")
        availability = item.get("availability")
        if availability not in {"available", "unavailable"}:
            raise TrackingAnalysisError("predictability evidence availability is invalid")
        confidence = _ratio(item.get("confidence"), "predictability.confidence")
        raw_limitations = item.get("limitations")
        if isinstance(raw_limitations, (str, bytes)) or not isinstance(raw_limitations, Sequence):
            raise TrackingAnalysisError("predictability evidence limitations must be a list")
        evidence_limitations = [
            _ref(value, "predictability evidence limitation")
            for value in raw_limitations
        ]
        is_accepted = (
            acceptance == "accepted"
            and availability == "available"
            and confidence == 1.0
            and not evidence_limitations
        )
        events.append({
            "event_id": evidence_ref,
            "event_kind": "motion_predictability_evidence",
            "start_ms": event_time_ms,
            "end_ms": event_time_ms,
            "actor_refs": [],
            "source_refs": validated_source_refs,
            "confidence": confidence,
            "attributes": {
                "schema_version": "motion_predictability_evidence.v1",
                "segment_ref": segment_ref,
                "kind": kind,
                "model_ref": model_ref,
                "model_version": model_version,
                "fit_metric": fit_metric,
                "fit_metric_version": fit_metric_version,
                "fit_value": fit_value,
                "threshold_ref": threshold_ref,
                "acceptance": acceptance,
                "availability": availability,
            },
            "limitations": evidence_limitations,
        })
        if is_accepted:
            accepted.append(evidence_ref)
    return events, sorted(set(accepted))


def analyze_continuous_tracking_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise TrackingAnalysisError("continuous tracking input schema is unsupported")
    analysis_ref = _ref(payload.get("analysis_ref"), "analysis_ref")
    window = payload.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise TrackingAnalysisError("canonical_time_window is required")
    start_ms = int(_number(window.get("start_ms"), "canonical_time_window.start_ms", minimum=0))
    end_ms = int(_number(window.get("end_ms"), "canonical_time_window.end_ms", minimum=start_ms + 1))
    if end_ms <= start_ms:
        raise TrackingAnalysisError("canonical_time_window is invalid")
    resolution = payload.get("scenario_resolution")
    if not isinstance(resolution, Mapping) or resolution.get("aim_family") != "continuous_tracking":
        raise TrackingAnalysisError("scenario_resolution must be continuous_tracking")
    motion = resolution.get("target_motion")
    model = motion.get("model") if isinstance(motion, Mapping) else "unknown"
    if model not in {"predictable", "reactive", "control", "mixed", "unknown"}:
        model = "unknown"
    quality = payload.get("visual_quality")
    if not isinstance(quality, Mapping):
        raise TrackingAnalysisError("visual_quality is required")
    quality_enabled = (
        quality.get("status") in {"accepted", "limited"}
        and "tracking" in (quality.get("enabled_metric_families") or [])
    )
    player_motion_status = payload.get("player_motion_status")
    if player_motion_status not in {
        "available_shared_trajectory",
        "unavailable_fixed_viewport_center",
    }:
        raise TrackingAnalysisError("player_motion_status is invalid")
    player_motion_available = player_motion_status == "available_shared_trajectory"
    player_motion_limitation = "player_aim_motion_unavailable_fixed_viewport_center"
    target_raw = payload.get("target_track")
    if not isinstance(target_raw, Mapping):
        raise TrackingAnalysisError("target_track is required")
    target_ref = _ref(target_raw.get("track_ref"), "target_track.track_ref")
    target = _points(target_raw.get("samples"), "target_track.samples", radius=True)
    crosshair = _points(payload.get("crosshair_samples"), "crosshair_samples", radius=False)
    target_by_time = _by_time(target, "target_track.samples")
    crosshair_by_time = _by_time(crosshair, "crosshair_samples")
    shared_times = [time_ms for time_ms in target_by_time if time_ms in crosshair_by_time and start_ms <= time_ms < end_ms]
    if not shared_times:
        raise TrackingAnalysisError("target and crosshair tracks have no common canonical samples")
    target_index = {int(point["canonical_time_ms"]): index for index, point in enumerate(target)}
    crosshair_index = {int(point["canonical_time_ms"]): index for index, point in enumerate(crosshair)}
    raw_alignment_latency_ms = payload.get("alignment_latency_ms")
    alignment_latency_ms = (
        None
        if raw_alignment_latency_ms is None
        else _number(raw_alignment_latency_ms, "alignment_latency_ms")
    )
    segment_ref = f"{analysis_ref}:segment:tracking:1"
    predictability_events, predictive_refs = _predictability_events(
        payload, segment_ref, start_ms,
    )
    rows: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    limitations = list(quality.get("limitations") or [])
    if not quality_enabled:
        limitations.append("continuous_tracking_quality_unavailable")
    for ordinal, time_ms in enumerate(shared_times, 1):
        target_point, crosshair_point = target_by_time[time_ms], crosshair_by_time[time_ms]
        usable = quality_enabled and _available(target_point, crosshair_point)
        sample_limitations: list[str] = []
        if not quality_enabled:
            sample_limitations.append("continuous_tracking_quality_unavailable")
        if not _available(target_point, crosshair_point):
            sample_limitations.append("low_confidence_or_occluded")
        radius = target_point.get("radius")
        if usable and radius is None:
            sample_limitations.append("target_radius_unavailable")
        error = hypot(float(target_point["x"]) - float(crosshair_point["x"]), float(target_point["y"]) - float(crosshair_point["y"])) if usable else None
        on_target = (error <= float(radius)) if error is not None and radius is not None else None
        target_velocity = (
            _velocity(target, target_index[time_ms])
            if usable and player_motion_available else None
        )
        crosshair_velocity = (
            _velocity(crosshair, crosshair_index[time_ms])
            if usable and player_motion_available else None
        )
        lag_ms = gain = None
        if target_velocity is not None and crosshair_velocity is not None:
            speed_squared = target_velocity[0] ** 2 + target_velocity[1] ** 2
            if speed_squared > 0:
                error_x = float(target_point["x"]) - float(crosshair_point["x"])
                error_y = float(target_point["y"]) - float(crosshair_point["y"])
                lag_ms = (error_x * target_velocity[0] + error_y * target_velocity[1]) / speed_squared
                gain = (crosshair_velocity[0] * target_velocity[0] + crosshair_velocity[1] * target_velocity[1]) / speed_squared
        samples.append({
            "event_ref": f"{analysis_ref}:tracking-sample:{ordinal}", "time_ms": time_ms,
            "usable": usable, "error_px": error, "radius": radius, "on_target": on_target,
            "lag_ms": lag_ms, "gain": gain, "target_velocity": target_velocity,
            "crosshair_velocity": crosshair_velocity,
            "target_position": (float(target_point["x"]), float(target_point["y"])),
            "crosshair_position": (float(crosshair_point["x"]), float(crosshair_point["y"])),
            "limitations": sample_limitations,
        })

    usable = [sample for sample in samples if sample["usable"]]
    radii_available = bool(usable) and all(sample["radius"] is not None for sample in usable)
    if not radii_available:
        limitations.append("target_radius_unavailable")

    episode = {
        "event_ref": f"{analysis_ref}:tracking-episode:1", "row_kind": "tracking_episode",
        "start_ms": shared_times[0], "end_ms": min(end_ms, shared_times[-1] + 1),
        "target_track_ref": target_ref, "condition_ref": f"{analysis_ref}:condition:{model}",
        "sample_count": len(samples), "usable_sample_count": sum(sample["usable"] for sample in samples),
        "limitations": sorted({
            *(
                [player_motion_limitation]
                if not player_motion_available else []
            ),
            *(
                limitation
                for sample in samples
                for limitation in sample["limitations"]
            ),
        }),
    }
    rows.append(episode)

    loss_start: int | None = None
    loss_rows: list[dict[str, Any]] = []
    reacquisition_rows: list[dict[str, Any]] = []
    if radii_available:
        for sample in samples:
            if sample["on_target"] is False and loss_start is None:
                loss_start = sample["time_ms"]
            elif sample["on_target"] is True and loss_start is not None:
                loss_ref = f"{analysis_ref}:tracking-loss:{len(loss_rows) + 1}"
                loss_rows.append({
                    "event_ref": loss_ref, "row_kind": "tracking_loss", "start_ms": loss_start,
                    "end_ms": sample["time_ms"], "duration_ms": sample["time_ms"] - loss_start,
                    "target_track_ref": target_ref, "limitations": [],
                })
                reacquisition_rows.append({
                    "event_ref": f"{analysis_ref}:tracking-reacquisition:{len(reacquisition_rows) + 1}",
                    "row_kind": "tracking_reacquisition", "start_ms": sample["time_ms"],
                    "end_ms": sample["time_ms"] + 1, "loss_ref": loss_ref,
                    "reacquisition_latency_ms": sample["time_ms"] - loss_start,
                    "target_track_ref": target_ref,
                    "limitations": [],
                })
                loss_start = None
        if loss_start is not None:
            loss_rows.append({
                "event_ref": f"{analysis_ref}:tracking-loss:{len(loss_rows) + 1}",
                "row_kind": "tracking_loss", "start_ms": loss_start, "end_ms": end_ms,
                "duration_ms": end_ms - loss_start, "target_track_ref": target_ref,
                "limitations": ["loss_right_censored"],
            })
    rows.extend(loss_rows)
    rows.extend(reacquisition_rows)

    changes_raw = payload.get("target_change_points", [])
    if isinstance(changes_raw, (str, bytes)) or not isinstance(changes_raw, Sequence):
        raise TrackingAnalysisError("target_change_points must be a list")
    change_rows: list[dict[str, Any]] = []
    for index, change in enumerate(changes_raw, 1):
        if not isinstance(change, Mapping):
            raise TrackingAnalysisError("target change point is invalid")
        change_ref = _ref(change.get("event_ref"), "target_change_points.event_ref")
        change_time = int(_number(change.get("time_ms"), "target_change_points.time_ms", minimum=start_ms))
        if change_time >= end_ms:
            raise TrackingAnalysisError("target change point is outside canonical window")
        target_change_velocity = (
            _velocity(target, target_index[change_time])
            if player_motion_available and change_time in target_index else None
        )
        response_time = None
        if target_change_velocity is not None:
            for sample in samples:
                if sample["time_ms"] <= change_time or not sample["usable"] or sample["crosshair_velocity"] is None:
                    continue
                if sample["crosshair_velocity"][0] * target_change_velocity[0] + sample["crosshair_velocity"][1] * target_change_velocity[1] > 0:
                    response_time = sample["time_ms"] - change_time
                    break
        change_rows.append({
            "event_ref": f"{analysis_ref}:tracking-change-response:{index}",
            "row_kind": "tracking_change_response", "start_ms": change_time,
            "end_ms": min(end_ms, change_time + 1), "change_ref": change_ref,
            "observed_change_response_ms": response_time,
            "alignment_latency_ms": alignment_latency_ms,
            "post_change_error_px": next((
                sample["error_px"]
                for sample in samples
                if sample["time_ms"] > change_time and sample["error_px"] is not None
            ), None),
            "target_track_ref": target_ref,
            "limitations": (
                [player_motion_limitation]
                if not player_motion_available
                else [] if response_time is not None
                else ["change_response_not_observed"]
            ),
        })
    rows.extend(change_rows)

    window_ms = int(_number(payload.get("fixed_window_ms", 1_000), "fixed_window_ms", minimum=1))
    fixed_rows: list[dict[str, Any]] = []
    for index, window_start in enumerate(range(start_ms, end_ms, window_ms), 1):
        window_end = min(end_ms, window_start + window_ms)
        window_samples = [sample for sample in samples if window_start <= sample["time_ms"] < window_end]
        fixed_rows.append({
            "event_ref": f"{analysis_ref}:tracking-fixed-window:{index}", "row_kind": "tracking_fixed_window",
            "start_ms": window_start, "end_ms": window_end, "sample_count": len(window_samples),
            "usable_sample_count": sum(sample["usable"] for sample in window_samples),
            "condition_ref": f"{analysis_ref}:condition:{model}",
            "target_relative_error_px": (
                sum(sample["error_px"] for sample in window_samples if sample["error_px"] is not None)
                / sum(sample["error_px"] is not None for sample in window_samples)
                if any(sample["error_px"] is not None for sample in window_samples) else None
            ),
            "time_in_radius_ratio": (
                sum(1.0 if sample["on_target"] else 0.0 for sample in window_samples)
                / len(window_samples)
                if window_samples and all(sample["on_target"] is not None for sample in window_samples)
                else None
            ),
            "correction_burden": None,
            "sparc": (
                _tracking_sparc(window_samples)
                if player_motion_available else None
            ),
            "target_track_ref": target_ref,
            "limitations": (
                sorted({
                    limitation
                    for sample in window_samples
                    for limitation in sample["limitations"]
                })
                if window_samples else ["window_has_no_shared_samples"]
            ),
        })
    rows.extend(fixed_rows)

    errors = [sample["error_px"] for sample in samples]
    on_target_values = [
        1.0 if sample["on_target"] else 0.0 for sample in usable
    ] if radii_available else []

    def correction_summary(
        items: Sequence[Mapping[str, Any]],
    ) -> tuple[int | None, float | None]:
        if not player_motion_available:
            return None, None
        velocities = [
            sample["crosshair_velocity"]
            for sample in items
            if sample["usable"] and sample["crosshair_velocity"] is not None
        ]
        reversals = 0
        accelerations: list[float] = []
        for left, right in zip(velocities, velocities[1:]):
            if left[0] * right[0] + left[1] * right[1] < 0:
                reversals += 1
            accelerations.append(hypot(right[0] - left[0], right[1] - left[1]))
        rms = (
            (sum(value * value for value in accelerations) / len(accelerations)) ** 0.5
            if accelerations
            else None
        )
        return reversals, rms

    correction_count, acceleration_rms = correction_summary(samples)
    sparc = _tracking_sparc(samples)
    for row in fixed_rows:
        window_samples = [
            sample for sample in samples
            if row["start_ms"] <= sample["time_ms"] < row["end_ms"]
        ]
        row["correction_burden"] = correction_summary(window_samples)[0]
        if not player_motion_available:
            row["limitations"] = sorted({
                *row["limitations"], player_motion_limitation,
            })
    episode.update({
        "target_relative_error_px": float(median([
            sample["error_px"] for sample in usable if sample["error_px"] is not None
        ])) if any(sample["error_px"] is not None for sample in usable) else None,
        "time_in_radius_ratio": (
            sum(on_target_values) / len(on_target_values) if on_target_values else None
        ),
        "loss_count": len(loss_rows) if radii_available else None,
        "correction_burden": correction_count,
        "sparc": sparc,
    })

    event_refs = [row["event_ref"] for row in rows]
    condition_ref = episode["condition_ref"]
    confidence = len(usable) / len(samples)
    low_quality = len(usable) != len(samples)
    if low_quality:
        limitations.append("low_confidence_or_occluded_samples_excluded")
    spectral = (
        _frequency_metrics(samples, model)
        if player_motion_available and not change_rows else None
    )
    episode.update({
        "phase_lag_ms": spectral["phase_lag_ms"] if spectral else None,
        "velocity_gain": spectral["velocity_gain"] if spectral else None,
        "coherence": spectral["coherence"] if spectral else None,
    })
    spectral_limitations = (
        []
        if spectral is not None
        else [
            player_motion_limitation
            if not player_motion_available
            else "frequency_metrics_require_long_uniform_steady_segment"
        ]
    )

    def metric(
        key: str,
        values: Sequence[float | None],
        *,
        unit: str,
        refs: Sequence[str] = event_refs,
        extra_limitations: Sequence[str] = (),
        conditions: Sequence[str] = (condition_ref,),
    ) -> dict[str, Any]:
        record = _metric(
            key,
            values,
            unit=unit,
            event_refs=refs,
            analysis_ref=analysis_ref,
            segment_refs=[segment_ref],
            condition_refs=conditions,
            limitations=[*limitations, *extra_limitations],
            confidence=confidence,
        )
        if record["coverage"] is not None:
            record["coverage"] = min(float(record["coverage"]), confidence)
        return record

    metrics = {
        "continuous_tracking.target_relative_error_px": metric(
            "continuous_tracking.target_relative_error_px", errors, unit="px",
        ),
        "continuous_tracking.time_in_radius_ratio": metric(
            "continuous_tracking.time_in_radius_ratio", on_target_values, unit="ratio",
            extra_limitations=[] if radii_available else ["target_radius_unavailable"],
        ),
        "continuous_tracking.loss_count": metric(
            "continuous_tracking.loss_count",
            [float(len(loss_rows))] if radii_available else [], unit="count",
            refs=[row["event_ref"] for row in loss_rows],
            extra_limitations=[] if radii_available else ["target_radius_unavailable"],
        ),
        "continuous_tracking.loss_duration_ms": metric(
            "continuous_tracking.loss_duration_ms",
            [row["duration_ms"] for row in loss_rows] if radii_available else [], unit="ms",
            refs=[row["event_ref"] for row in loss_rows],
            extra_limitations=[] if radii_available else ["target_radius_unavailable"],
        ),
        "continuous_tracking.reacquisition_latency_ms": metric(
            "continuous_tracking.reacquisition_latency_ms",
            [row["reacquisition_latency_ms"] for row in reacquisition_rows] if radii_available else [], unit="ms",
            refs=[row["event_ref"] for row in reacquisition_rows],
            extra_limitations=[] if radii_available else ["target_radius_unavailable"],
        ),
        "continuous_tracking.relative_lag_ms": metric(
            "continuous_tracking.relative_lag_ms",
            [sample["lag_ms"] for sample in samples], unit="ms",
            extra_limitations=[
                "alignment_latency_reported_separately",
                *(
                    [player_motion_limitation]
                    if not player_motion_available else []
                ),
            ],
        ),
        "continuous_tracking.phase_lag_ms": metric(
            "continuous_tracking.phase_lag_ms",
            [spectral["phase_lag_ms"] if spectral else None], unit="ms",
            extra_limitations=spectral_limitations,
        ),
        "continuous_tracking.coherence": metric(
            "continuous_tracking.coherence",
            [spectral["coherence"] if spectral else None], unit="ratio",
            extra_limitations=spectral_limitations,
        ),
        "continuous_tracking.velocity_gain": metric(
            "continuous_tracking.velocity_gain",
            [spectral["velocity_gain"] if spectral else None], unit="ratio",
            extra_limitations=spectral_limitations,
        ),
        "continuous_tracking.alignment_latency_ms": metric(
            "continuous_tracking.alignment_latency_ms", [alignment_latency_ms], unit="ms",
            refs=[], extra_limitations=["capture_alignment_descriptor_not_human_response"],
        ),
        "continuous_tracking.observed_change_response_ms": metric(
            "continuous_tracking.observed_change_response_ms",
            [row["observed_change_response_ms"] for row in change_rows], unit="ms",
            refs=[row["event_ref"] for row in change_rows],
            extra_limitations=(
                [player_motion_limitation]
                if not player_motion_available
                else [] if change_rows
                else ["no_validated_change_points"]
            ),
        ),
        "continuous_tracking.human_response_latency_ms": metric(
            "continuous_tracking.human_response_latency_ms", [], unit="ms", refs=[],
            extra_limitations=["not_inferred_from_capture_alignment_or_tracking_samples"],
        ),
        "continuous_tracking.correction_direction_reversal_count": metric(
            "continuous_tracking.correction_direction_reversal_count",
            [float(correction_count)] if correction_count is not None else [], unit="count",
            extra_limitations=[
                "descriptive_correction_burden",
                *(
                    [player_motion_limitation]
                    if not player_motion_available else []
                ),
            ],
        ),
        "continuous_tracking.smoothness_acceleration_rms": metric(
            "continuous_tracking.smoothness_acceleration_rms",
            [acceleration_rms], unit="px_per_ms2",
            extra_limitations=[
                "descriptive_smoothness_not_a_mechanism",
                *(
                    [player_motion_limitation]
                    if not player_motion_available else []
                ),
            ],
        ),
        "continuous_tracking.sparc": metric(
            "continuous_tracking.sparc", [sparc], unit="dimensionless",
            extra_limitations=[
                "tracking_sparc_requires_uniform_window_and_accuracy_guardrail",
                *(
                    [player_motion_limitation]
                    if not player_motion_available else []
                ),
            ],
        ),
    }
    if predictive_refs and player_motion_available:
        metrics["continuous_tracking.predictive_lead_ms"] = _metric(
            "continuous_tracking.predictive_lead_ms",
            [sample["lag_ms"] for sample in samples],
            unit="ms",
            event_refs=event_refs,
            analysis_ref=analysis_ref,
            segment_refs=[segment_ref],
            condition_refs=predictive_refs,
            limitations=limitations,
            confidence=confidence,
        )
    from .analysis_evidence import (
        validate_evidence_segment_v1,
        validate_event_bundle_v1,
        validate_metric_record_v1,
    )

    for metric_record in metrics.values():
        validate_metric_record_v1(metric_record)
    support_status = (
        "outcome_only"
        if not quality_enabled
        else "partial"
        if low_quality or not usable or not player_motion_available
        else "supported"
    )
    available_channels = [
        channel for channel in payload.get("available_channel_keys") or []
        if isinstance(channel, str)
    ]
    metric_refs = [
        f"metric:{record['metric_key']}@{record['metric_version']}"
        for record in metrics.values()
    ]

    def evidence_segment(
        *,
        segment_id: str,
        segment_kind: str,
        segment_start: int,
        segment_end: int,
        segment_events: Sequence[str],
        segment_metric_refs: Sequence[str],
        segment_limitations: Sequence[str],
    ) -> dict[str, Any]:
        focus_end = min(segment_end, segment_start + 20_000)
        segment = {
            "schema_version": "evidence_segment.v1",
            "segment_id": segment_id,
            "analysis_ref": analysis_ref,
            "analyzer_ref": ANALYSIS_VERSION,
            "segment_kind": segment_kind,
            "start_ms": segment_start,
            "end_ms": segment_end,
            "focus_start_ms": segment_start,
            "focus_end_ms": focus_end,
            "title_key": f"continuous_tracking.{segment_kind}",
            "rank_reason": segment_kind,
            "issue_refs": [],
            "metric_refs": list(segment_metric_refs),
            "event_refs": list(segment_events),
            "available_channels": available_channels if quality_enabled else [],
            "source_coverage": confidence,
            "confidence": confidence,
            "video_playback": {
                "availability": "unavailable",
                "artifact_ref": None,
                "start_ms": None,
                "end_ms": None,
            },
            "limitations": sorted(set(segment_limitations)),
        }
        return validate_evidence_segment_v1(segment, canonical_window=dict(window))

    evidence_segments = [evidence_segment(
        segment_id=segment_ref,
        segment_kind="typical",
        segment_start=shared_times[0],
        segment_end=min(end_ms, shared_times[-1] + 1),
        segment_events=[*event_refs, *predictive_refs],
        segment_metric_refs=metric_refs,
        segment_limitations=limitations,
    )]
    if loss_rows:
        worst = max(loss_rows, key=lambda row: row["duration_ms"])
        evidence_segments.append(evidence_segment(
            segment_id=f"{analysis_ref}:segment:tracking:failure:1",
            segment_kind="worst",
            segment_start=worst["start_ms"],
            segment_end=worst["end_ms"],
            segment_events=[worst["event_ref"]],
            segment_metric_refs=[
                "metric:continuous_tracking.loss_duration_ms@continuous_tracking.loss_duration_ms.v1"
            ],
            segment_limitations=worst["limitations"],
        ))
    if reacquisition_rows:
        recovery = reacquisition_rows[0]
        evidence_segments.append(evidence_segment(
            segment_id=f"{analysis_ref}:segment:tracking:recovery:1",
            segment_kind="improved",
            segment_start=recovery["start_ms"],
            segment_end=recovery["end_ms"],
            segment_events=[recovery["event_ref"]],
            segment_metric_refs=[
                "metric:continuous_tracking.reacquisition_latency_ms@continuous_tracking.reacquisition_latency_ms.v1"
            ],
            segment_limitations=[],
        ))

    events = list(predictability_events)
    for row in rows:
        attributes = {
            key: value
            for key, value in row.items()
            if key not in {
                "event_ref", "row_kind", "start_ms", "end_ms", "limitations",
            }
            and value is not None
        }
        events.append({
            "event_id": row["event_ref"],
            "event_kind": row["row_kind"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "actor_refs": [target_ref],
            "source_refs": [f"{analysis_ref}:source:tracking-analysis"],
            "confidence": confidence,
            "attributes": attributes,
            "limitations": list(row["limitations"]),
        })
    event_bundle = validate_event_bundle_v1({
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_associations": [],
    })
    processed_tables = _tables(analysis_ref, rows)
    return {
        "schema_version": SCHEMA_VERSION, "analysis_version": ANALYSIS_VERSION,
        "analysis_ref": analysis_ref, "analysis_type": "continuous_tracking",
        "support_status": support_status, "scenario_motion_class": model,
        "processed_rows": rows, "processed_event_tables": processed_tables,
        "metrics": metrics, "evidence_segments": evidence_segments,
        "comparison": payload.get("comparison"),
        "limitations": sorted(set([
            *limitations,
            *(
                [player_motion_limitation]
                if not player_motion_available else []
            ),
        ])),
        "evidence_extension": {
            "event_bundle": event_bundle,
            "metric_records": list(metrics.values()),
            "evidence_segments": evidence_segments,
            "processed_event_tables": processed_tables,
        },
    }


def extend_analysis_evidence_with_continuous_tracking_v1(
    artifact: Mapping[str, Any], analysis_result: Mapping[str, Any],
) -> dict[str, Any]:
    from .analysis_evidence import (
        validate_analysis_evidence_artifact_v1,
        validate_event_bundle_v1,
        validate_metric_record_v1,
    )

    projected = validate_analysis_evidence_artifact_v1(artifact)
    if (
        analysis_result.get("schema_version") != SCHEMA_VERSION
        or analysis_result.get("analysis_version") != ANALYSIS_VERSION
        or analysis_result.get("analysis_ref") != projected["analysis_ref"]
    ):
        raise TrackingAnalysisError("continuous tracking result is incompatible")
    extension = analysis_result.get("evidence_extension")
    if not isinstance(extension, Mapping):
        raise TrackingAnalysisError("continuous tracking evidence extension is missing")
    event_bundle = validate_event_bundle_v1(extension.get("event_bundle"))
    if event_bundle["analysis_ref"] != projected["analysis_ref"]:
        raise TrackingAnalysisError("continuous tracking event bundle is bound to another analysis")
    metrics = [
        validate_metric_record_v1(metric)
        for metric in extension.get("metric_records") or []
    ]
    segments = list(extension.get("evidence_segments") or [])
    available_channels = {
        channel["channel_key"]
        for bundle in projected["signal_bundles"]
        for channel in bundle["channels"]
    }
    for segment in segments:
        if not set(segment.get("available_channels") or []) <= available_channels:
            raise TrackingAnalysisError(
                "continuous tracking evidence segment references unavailable channels"
            )
    existing_metric_keys = {
        metric["metric_key"] for metric in projected["metric_records"]
    }
    if existing_metric_keys.intersection(metric["metric_key"] for metric in metrics):
        raise TrackingAnalysisError("continuous tracking metric already exists")
    projected = deepcopy(projected)
    if event_bundle["events"]:
        projected["event_bundles"].append(event_bundle)
    projected["metric_records"].extend(metrics)
    projected["evidence_segments"].extend(deepcopy(segments))
    for limitation in analysis_result.get("limitations") or []:
        if limitation not in projected["limitations"]:
            projected["limitations"].append(limitation)
    return validate_analysis_evidence_artifact_v1(projected)


__all__ = [
    "ANALYSIS_VERSION",
    "TrackingAnalysisError",
    "analyze_continuous_tracking_v1",
    "extend_analysis_evidence_with_continuous_tracking_v1",
]
