"""Deterministic, target-relative analysis for dynamic clicking.

The module consumes already aligned numeric observations. It never opens media,
reads a parser payload, or invents a target association from a nearest pixel.
"""

from __future__ import annotations

from copy import deepcopy
from math import hypot, isfinite
from statistics import median
from typing import Any, Mapping, Sequence

from .analysis_evidence import (
    dynamic_processed_field_catalog_v1,
    validate_event_bundle,
    validate_event_bundle_v1,
    validate_metric_record_v1,
    validate_processed_event_table_v1,
)


ANALYSIS_VERSION = "dynamic_clicking.v1"
SCHEMA_VERSION = "dynamic_clicking_analysis.v1"
INPUT_SCHEMA_VERSION = "dynamic_clicking_input.v1"
_MAX_INTERPOLATION_GAP_MS = 120
_REF_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._@+-")


class DynamicClickingAnalysisError(ValueError):
    pass


def _ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or any(char not in _REF_CHARS for char in value):
        raise DynamicClickingAnalysisError(f"{field} is invalid")
    return value


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise DynamicClickingAnalysisError(f"{field} is invalid")
    number = float(value)
    if minimum is not None and number < minimum:
        raise DynamicClickingAnalysisError(f"{field} is invalid")
    return number


def _ratio(value: Any, field: str) -> float:
    number = _number(value, field)
    if not 0 <= number <= 1:
        raise DynamicClickingAnalysisError(f"{field} is invalid")
    return number


def _points(raw: Any, field: str, *, radius: bool = False) -> list[dict[str, float | int]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        raise DynamicClickingAnalysisError(f"{field} must be non-empty")
    points: list[dict[str, float | int]] = []
    previous: int | None = None
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise DynamicClickingAnalysisError(f"{field}[{index}] is invalid")
        time_ms = _number(item.get("canonical_time_ms"), f"{field}[{index}].canonical_time_ms", minimum=0)
        if int(time_ms) != time_ms or (previous is not None and int(time_ms) <= previous):
            raise DynamicClickingAnalysisError(f"{field} timestamps are not strictly ordered")
        point: dict[str, float | int] = {
            "canonical_time_ms": int(time_ms),
            "x": _number(item.get("x"), f"{field}[{index}].x"),
            "y": _number(item.get("y"), f"{field}[{index}].y"),
            "confidence": _ratio(item.get("confidence", 1.0), f"{field}[{index}].confidence"),
        }
        if radius:
            point["radius"] = _number(item.get("radius"), f"{field}[{index}].radius", minimum=0.01)
        points.append(point)
        previous = int(time_ms)
    return points


def _interpolate(points: list[dict[str, float | int]], time_ms: int, max_gap_ms: int) -> dict[str, float] | None:
    if not points or time_ms < points[0]["canonical_time_ms"] or time_ms > points[-1]["canonical_time_ms"]:
        return None
    for point in points:
        if point["canonical_time_ms"] == time_ms:
            return {key: float(point[key]) for key in ("x", "y", "confidence", "radius") if key in point}
    for left, right in zip(points, points[1:]):
        left_time = int(left["canonical_time_ms"])
        right_time = int(right["canonical_time_ms"])
        if left_time < time_ms < right_time:
            gap = right_time - left_time
            if gap > max_gap_ms:
                return None
            ratio = (time_ms - left_time) / gap
            output = {}
            for key in ("x", "y", "confidence", "radius"):
                if key in left and key in right:
                    output[key] = float(left[key]) + (float(right[key]) - float(left[key])) * ratio
            return output
    return None


def _velocity(points: list[dict[str, float | int]], time_ms: int, max_gap_ms: int) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    pair: tuple[dict[str, float | int], dict[str, float | int]] | None = None
    for left, right in zip(points, points[1:]):
        if int(left["canonical_time_ms"]) <= time_ms <= int(right["canonical_time_ms"]):
            pair = left, right
            break
    if pair is None:
        return None
    left, right = pair
    delta_ms = int(right["canonical_time_ms"]) - int(left["canonical_time_ms"])
    if delta_ms <= 0 or delta_ms > max_gap_ms:
        return None
    scale = 1.0 / delta_ms
    return (
        (float(right["x"]) - float(left["x"])) * scale,
        (float(right["y"]) - float(left["y"])) * scale,
    )


def _acceleration(
    points: list[dict[str, float | int]], time_ms: int, max_gap_ms: int,
) -> tuple[float, float] | None:
    available = [point for point in points if int(point["canonical_time_ms"]) <= time_ms]
    if len(available) < 3:
        return None
    first, middle, last = available[-3:]
    first_time = int(first["canonical_time_ms"])
    middle_time = int(middle["canonical_time_ms"])
    last_time = int(last["canonical_time_ms"])
    first_gap = middle_time - first_time
    second_gap = last_time - middle_time
    midpoint_gap = (last_time - first_time) / 2.0
    if (
        first_gap <= 0 or second_gap <= 0 or midpoint_gap <= 0
        or first_gap > max_gap_ms or second_gap > max_gap_ms
    ):
        return None
    first_velocity = (
        (float(middle["x"]) - float(first["x"])) / first_gap,
        (float(middle["y"]) - float(first["y"])) / first_gap,
    )
    second_velocity = (
        (float(last["x"]) - float(middle["x"])) / second_gap,
        (float(last["y"]) - float(middle["y"])) / second_gap,
    )
    return (
        (second_velocity[0] - first_velocity[0]) / midpoint_gap,
        (second_velocity[1] - first_velocity[1]) / midpoint_gap,
    )


def _fixed_aim_point(points: list[dict[str, float | int]]) -> tuple[float, float]:
    x = float(points[0]["x"])
    y = float(points[0]["y"])
    if any(
        abs(float(point["x"]) - x) > 1e-6 or abs(float(point["y"]) - y) > 1e-6
        for point in points[1:]
    ):
        raise DynamicClickingAnalysisError("crosshair_samples must use fixed_viewport_center")
    return x, y


def _acquisition_time_ms(
    *,
    track_ref: str,
    samples: list[dict[str, float | int]],
    aim_point: tuple[float, float],
    click_time_ms: int,
    window_start_ms: int,
    visual_events: Sequence[Mapping[str, Any]],
) -> tuple[int | None, int | None, str | None]:
    anchors = [
        int(event["start_ms"])
        for event in visual_events
        if event.get("event_kind") in {"target_available", "reacquired"}
        and track_ref in (event.get("actor_refs") or [])
        and isinstance(event.get("start_ms"), int)
        and int(event["start_ms"]) <= click_time_ms
    ]
    if anchors:
        start_ms = max(anchors)
    else:
        start_ms = int(samples[0]["canonical_time_ms"])
        if start_ms <= window_start_ms:
            return None, None, "acquisition_start_window_censored"
    for sample in samples:
        sample_time = int(sample["canonical_time_ms"])
        if not start_ms <= sample_time <= click_time_ms:
            continue
        if hypot(float(sample["x"]) - aim_point[0], float(sample["y"]) - aim_point[1]) <= float(sample["radius"]):
            return start_ms, sample_time - start_ms, None
    return start_ms, None, "acquisition_not_observed_before_click"


def _target_at_click(track: Mapping[str, Any], time_ms: int, max_gap_ms: int) -> tuple[dict[str, float], tuple[float, float] | None] | None:
    samples = track["samples"]
    point = _interpolate(samples, time_ms, max_gap_ms)
    if point is None or "radius" not in point:
        return None
    return point, _velocity(samples, time_ms, max_gap_ms)


def _latest_observation_at_or_before(
    points: Sequence[Mapping[str, float | int]], time_ms: int, max_gap_ms: int,
) -> tuple[int, dict[str, float]] | None:
    for point in reversed(points):
        sample_time = int(point["canonical_time_ms"])
        if sample_time > time_ms:
            continue
        if time_ms - sample_time > max_gap_ms:
            return None
        return sample_time, {
            key: float(point[key])
            for key in ("x", "y", "confidence", "radius")
            if key in point
        }
    return None


def _target_at_observation_frame(
    track: Mapping[str, Any], observation_time_ms: int,
) -> tuple[dict[str, float], tuple[float, float] | None] | None:
    for point in reversed(track["samples"]):
        sample_time = int(point["canonical_time_ms"])
        if sample_time < observation_time_ms:
            return None
        if sample_time == observation_time_ms:
            return {
                key: float(point[key])
                for key in ("x", "y", "confidence", "radius")
                if key in point
            }, None
    return None


def _aim_is_inside_target(
    target: tuple[dict[str, float], tuple[float, float] | None],
    aim: Mapping[str, float],
) -> bool:
    point = target[0]
    return hypot(point["x"] - aim["x"], point["y"] - aim["y"]) <= point["radius"]


def _visual_interval_limitation(
    *,
    visual_events: Sequence[Mapping[str, Any]],
    track_ref: str,
    track_samples: Sequence[Mapping[str, Any]],
    time_ms: int,
) -> str | None:
    if any(
        "target_merge_ambiguous" in (event.get("limitations") or [])
        and isinstance(event.get("start_ms"), int)
        and isinstance(event.get("end_ms"), int)
        and int(event["start_ms"]) <= time_ms <= int(event["end_ms"])
        for event in visual_events
    ):
        return "target_merge_ambiguous_at_click"
    if any(
        event.get("event_kind") == "low_confidence"
        and isinstance(event.get("start_ms"), int)
        and isinstance(event.get("end_ms"), int)
        and int(event["start_ms"]) <= time_ms <= int(event["end_ms"])
        for event in visual_events
    ):
        return "visual_low_confidence_at_click"
    for event in visual_events:
        if (
            event.get("event_kind") != "reacquired"
            or track_ref not in (event.get("actor_refs") or [])
            or not isinstance(event.get("start_ms"), int)
        ):
            continue
        reacquired_ms = int(event["start_ms"])
        previous_times = [
            int(sample["canonical_time_ms"])
            for sample in track_samples
            if int(sample["canonical_time_ms"]) < reacquired_ms
        ]
        if previous_times and max(previous_times) < time_ms < reacquired_ms:
            return "target_occluded_at_click"
    return None


def _metric(
    key: str,
    values: list[float | None],
    *,
    analysis_ref: str,
    unit: str,
    event_refs: list[str],
    segment_refs: list[str],
    condition_refs: list[str],
    limitations: list[str],
    confidence: float,
) -> dict[str, Any]:
    valid = [float(value) for value in values if value is not None and isfinite(value)]
    available = bool(valid)
    value = float(median(valid)) if valid else None
    ordered = sorted(valid)
    distribution = None
    if valid:
        distribution = {
            "min": min(ordered),
            "p10": ordered[max(0, int(round((len(ordered) - 1) * 0.10)))],
            "p25": ordered[max(0, int(round((len(ordered) - 1) * 0.25)))],
            "median": value,
            "p75": ordered[max(0, int(round((len(ordered) - 1) * 0.75)))],
            "p90": ordered[max(0, int(round((len(ordered) - 1) * 0.90)))],
            "max": max(ordered),
            "histogram_bins": [],
        }
    return {
        "schema_version": "metric_record.v1",
        "metric_key": key,
        "metric_version": f"{key}.v1",
        "value": value,
        "unit": unit,
        "availability": "available" if available else "unavailable",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": [f"{analysis_ref}:source:dynamic-analysis"],
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
        "confidence": confidence if available else 0.0,
        "limitations": sorted(set(limitations)),
    }


def _predictability_events(
    payload: Mapping[str, Any], analysis_ref: str, segment_ref: str, event_time_ms: int,
) -> tuple[list[dict], dict[str, dict]]:
    raw = payload.get("predictability_evidence") or []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise DynamicClickingAnalysisError("predictability_evidence must be a list")
    events: list[dict] = []
    accepted: dict[str, dict] = {}
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise DynamicClickingAnalysisError("predictability evidence is invalid")
        evidence_ref = _ref(item.get("evidence_ref"), f"predictability_evidence[{index}].evidence_ref")
        item_segment_ref = _ref(
            item.get("segment_ref"), f"predictability_evidence[{index}].segment_ref",
        )
        if item_segment_ref != segment_ref:
            raise DynamicClickingAnalysisError("predictability evidence is bound to another segment")
        source_refs_raw = item.get("source_refs")
        if (
            isinstance(source_refs_raw, (str, bytes))
            or not isinstance(source_refs_raw, Sequence)
            or not source_refs_raw
        ):
            raise DynamicClickingAnalysisError("predictability source_refs are required")
        source_refs = [
            _ref(source_ref, f"predictability_evidence[{index}].source_refs")
            for source_ref in source_refs_raw
        ]
        attrs = {
            "segment_ref": item_segment_ref,
            "model_ref": _ref(item.get("model_ref"), "predictability.model_ref"),
            "model_version": _ref(item.get("model_version"), "predictability.model_version"),
            "fit_metric": _ref(item.get("fit_metric"), "predictability.fit_metric"),
            "fit_value": _number(item.get("fit_value"), "predictability.fit_value"),
            "threshold_ref": _ref(item.get("threshold_ref"), "predictability.threshold_ref"),
            "acceptance": item.get("acceptance"),
        }
        if attrs["acceptance"] not in {"accepted", "rejected"}:
            raise DynamicClickingAnalysisError("predictability acceptance is invalid")
        event = {
            "event_id": evidence_ref,
            "event_kind": "motion_predictability_evidence",
            "start_ms": event_time_ms,
            "end_ms": event_time_ms,
            "actor_refs": [],
            "source_refs": source_refs,
            "confidence": 1.0 if attrs["acceptance"] == "accepted" else 0.0,
            "attributes": attrs,
            "limitations": [] if attrs["acceptance"] == "accepted" else ["predictability_evidence_rejected"],
        }
        events.append(event)
        if attrs["acceptance"] == "accepted":
            accepted[item_segment_ref] = {"evidence_ref": evidence_ref, **attrs}
    return events, accepted


def analyze_dynamic_clicking_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise DynamicClickingAnalysisError("dynamic clicking input schema is unsupported")
    analysis_ref = _ref(payload.get("analysis_ref"), "analysis_ref")
    window = payload.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise DynamicClickingAnalysisError("canonical_time_window is required")
    start_ms = int(_number(window.get("start_ms"), "canonical_time_window.start_ms", minimum=0))
    end_ms = int(_number(window.get("end_ms"), "canonical_time_window.end_ms", minimum=1))
    if end_ms <= start_ms:
        raise DynamicClickingAnalysisError("canonical_time_window is invalid")
    max_gap_ms = int(payload.get("max_interpolation_gap_ms", _MAX_INTERPOLATION_GAP_MS))
    if not 1 <= max_gap_ms <= 2_000:
        raise DynamicClickingAnalysisError("max_interpolation_gap_ms is invalid")

    visual_quality = payload.get("visual_quality")
    if not isinstance(visual_quality, Mapping):
        raise DynamicClickingAnalysisError("visual_quality is required")
    quality_enabled = (
        visual_quality.get("status") in {"accepted", "limited"}
        and "dynamic_clicking" in (visual_quality.get("enabled_metric_families") or [])
    )
    limitations: list[str] = list(visual_quality.get("limitations") or [])
    if not quality_enabled:
        limitations.append("dynamic_clicking_quality_unavailable")

    tracks_raw = payload.get("target_tracks") or []
    tracks: dict[str, dict] = {}
    for index, raw_track in enumerate(tracks_raw):
        if not isinstance(raw_track, Mapping):
            raise DynamicClickingAnalysisError(f"target_tracks[{index}] is invalid")
        track_ref = _ref(raw_track.get("track_ref"), f"target_tracks[{index}].track_ref")
        if track_ref in tracks:
            raise DynamicClickingAnalysisError("duplicate target track")
        tracks[track_ref] = {
            "track_ref": track_ref,
            "samples": _points(raw_track.get("samples"), f"target_tracks[{index}].samples", radius=True),
            "limitations": list(raw_track.get("limitations") or []),
        }
    crosshair = _points(payload.get("crosshair_samples"), "crosshair_samples")
    aim_point = _fixed_aim_point(crosshair)
    available_channels_raw = payload.get("available_channel_keys") or []
    if isinstance(available_channels_raw, (str, bytes)) or not isinstance(available_channels_raw, Sequence):
        raise DynamicClickingAnalysisError("available_channel_keys must be a list")
    available_channels = [
        _ref(channel, "available_channel_keys") for channel in available_channels_raw
    ]
    click_events_raw = payload.get("click_events") or []
    if isinstance(click_events_raw, (str, bytes)) or not isinstance(click_events_raw, Sequence):
        raise DynamicClickingAnalysisError("click_events must be a list")
    clicks = []
    for index, raw_click in enumerate(click_events_raw):
        if not isinstance(raw_click, Mapping):
            raise DynamicClickingAnalysisError("click event is invalid")
        time_ms = _number(raw_click.get("time_ms"), f"click_events[{index}].time_ms", minimum=start_ms)
        if int(time_ms) != time_ms or int(time_ms) >= end_ms:
            raise DynamicClickingAnalysisError("click event is outside canonical window")
        clicks.append({"event_ref": _ref(raw_click.get("event_ref"), "click event ref"), "time_ms": int(time_ms)})
    clicks.sort(key=lambda item: (item["time_ms"], item["event_ref"]))

    visual_bundle_raw = payload.get("visual_event_bundle")
    if visual_bundle_raw is None:
        visual_bundle = {
            "schema_version": "event_bundle.v1",
            "analysis_ref": analysis_ref,
            "events": [],
            "outcome_associations": [],
        }
    else:
        visual_bundle = validate_event_bundle(visual_bundle_raw)
        if visual_bundle["analysis_ref"] != analysis_ref:
            raise DynamicClickingAnalysisError("visual event bundle is bound to another analysis")
    visual_events = visual_bundle["events"]
    visual_event_kinds = {
        event["event_id"]: event["event_kind"] for event in visual_events
    }
    visual_events_by_id = {event["event_id"]: event for event in visual_events}
    associations_by_click: dict[str, list[dict]] = {}
    for association in visual_bundle["outcome_associations"]:
        associations_by_click.setdefault(association["shot_event_ref"], []).append(association)
    resolution = payload.get("scenario_resolution") or {}
    motion = resolution.get("target_motion") if isinstance(resolution, Mapping) else {}
    motion_class = motion.get("model", "unknown") if isinstance(motion, Mapping) else "unknown"
    if motion_class not in {"predictable", "reactive", "mixed", "static", "unknown"}:
        motion_class = "unknown"
    segment_ref = f"{analysis_ref}:segment:dynamic:1"
    predictability_events, accepted_predictability = _predictability_events(
        payload, analysis_ref, segment_ref, start_ms,
    )
    rows: list[dict[str, Any]] = []
    dynamic_events: list[dict] = []
    error_values: list[float | None] = []
    acquisition_values: list[float | None] = []
    relative_velocity_values: list[float | None] = []
    outcome_values: list[float | None] = []

    for index, click in enumerate(clicks, 1):
        click_ref = click["event_ref"]
        time_ms = click["time_ms"]
        matching_associations = associations_by_click.get(click_ref, [])
        association = matching_associations[0] if len(matching_associations) == 1 else {}
        shot_event = visual_events_by_id.get(association.get("shot_event_ref"))
        association_time_matches = (
            shot_event is not None and shot_event.get("start_ms") == time_ms
        )
        association_kind = association.get("association_kind")
        target_bound_association = (
            quality_enabled
            and association.get("availability") == "available"
            and association_kind in {"directly_observed", "validated_aligned"}
            and association_time_matches
        )
        requested_track = (
            association.get("target_track_ref") if target_bound_association else None
        )
        interpolated_aim = (
            _interpolate(crosshair, time_ms, max_gap_ms) if quality_enabled else None
        )
        geometric_observation = (
            _latest_observation_at_or_before(crosshair, time_ms, max_gap_ms)
            if quality_enabled else None
        )
        geometric_time = (
            geometric_observation[0] if geometric_observation is not None else None
        )
        geometric_aim = (
            geometric_observation[1] if geometric_observation is not None else None
        )
        global_interval_limitation = _visual_interval_limitation(
            visual_events=visual_events,
            track_ref="",
            track_samples=[],
            time_ms=time_ms,
        )
        candidates = []
        geometric_targets: dict[str, tuple[dict[str, float], tuple[float, float] | None]] = {}
        if quality_enabled:
            if requested_track is not None:
                requested_track = _ref(requested_track, "outcome association target_track_ref")
                if requested_track in tracks:
                    candidates = [requested_track]
            elif geometric_time is not None and geometric_aim is not None:
                for track_ref, track in tracks.items():
                    target_at_click = _target_at_observation_frame(
                        track, geometric_time,
                    )
                    if (
                        target_at_click is not None
                        and _aim_is_inside_target(target_at_click, geometric_aim)
                    ):
                        candidates.append(track_ref)
                        geometric_targets[track_ref] = target_at_click
        row_limitations: list[str] = []
        if global_interval_limitation is not None:
            row_limitations.append(global_interval_limitation)
        if len(matching_associations) > 1:
            row_limitations.append("outcome_association_ambiguous")
        elif matching_associations and not association_time_matches:
            row_limitations.append("outcome_association_click_time_mismatch")
        track_ref = candidates[0] if len(candidates) == 1 else None
        target_association_basis = (
            "direct_outcome" if track_ref is not None and association_kind == "directly_observed"
            else "validated_outcome" if track_ref is not None and association_kind == "validated_aligned"
            else "unique_geometric" if track_ref is not None else "unavailable"
        )
        if target_association_basis == "unique_geometric":
            row_limitations.append("click_geometry_visible_radius_conditioned")
        if len(candidates) > 1:
            row_limitations.append("target_click_association_ambiguous")
        elif not candidates:
            row_limitations.append("target_click_association_unavailable")
        if target_bound_association and track_ref is not None:
            target = _target_at_click(tracks[track_ref], time_ms, max_gap_ms)
            aim = interpolated_aim
        else:
            target = geometric_targets.get(track_ref) if track_ref is not None else None
            aim = geometric_aim
        identity_continuity_available = not (
            track_ref and any(
            limitation in {"reentry_identity_unresolved", "identity_crossing_ambiguous"}
            for limitation in tracks[track_ref]["limitations"]
            )
        )
        if not identity_continuity_available:
            row_limitations.append("target_identity_unresolved")
        cross_frame_available = (
            target_association_basis != "unique_geometric"
            and identity_continuity_available
        )
        if track_ref and target is not None:
            interval_limitation = _visual_interval_limitation(
                visual_events=visual_events,
                track_ref=track_ref,
                track_samples=tracks[track_ref]["samples"],
                time_ms=time_ms,
            )
            if interval_limitation is not None:
                row_limitations.append(interval_limitation)
                target = None
                if interval_limitation == "target_merge_ambiguous_at_click":
                    track_ref = None
                    target_association_basis = "unavailable"
                elif not target_bound_association:
                    target_association_basis = "unavailable"
        miss_vector = None
        normalized_error = None
        relative_velocity = None
        signed_lead_lag = None
        lead_lag_descriptor = None
        acquisition_start = None
        acquisition_time = None
        target_speed = None
        target_acceleration = None
        if target is not None and aim is not None:
            target_point, target_velocity = target
            miss_vector = [target_point["x"] - aim["x"], target_point["y"] - aim["y"]]
            normalized_error = hypot(*miss_vector) / target_point["radius"]
            if cross_frame_available:
                aim_velocity = _velocity(crosshair, time_ms, max_gap_ms)
                if target_velocity is not None and aim_velocity is not None:
                    relative_velocity = {
                        "x": target_velocity[0] - aim_velocity[0],
                        "y": target_velocity[1] - aim_velocity[1],
                    }
                    target_speed = hypot(*target_velocity)
                    acceleration = _acceleration(
                        tracks[track_ref]["samples"], time_ms, max_gap_ms,
                    )
                    if acceleration is not None:
                        target_acceleration = hypot(*acceleration)
                    alignment = (
                        miss_vector[0] * target_velocity[0]
                        + miss_vector[1] * target_velocity[1]
                    )
                    speed = hypot(*target_velocity)
                    signed_lead_lag = (
                        alignment / (speed * target_point["radius"])
                        if speed > 0 else 0.0
                    )
                    lead_lag_descriptor = (
                        "lag" if signed_lead_lag > 0.1
                        else "lead" if signed_lead_lag < -0.1 else "aligned"
                    )
                samples = tracks[track_ref]["samples"]
                acquisition_start, acquisition_time, acquisition_limitation = _acquisition_time_ms(
                    track_ref=track_ref,
                    samples=samples,
                    aim_point=aim_point,
                    click_time_ms=time_ms,
                    window_start_ms=start_ms,
                    visual_events=visual_events,
                )
                if acquisition_limitation is not None:
                    row_limitations.append(acquisition_limitation)
        else:
            if quality_enabled and aim is None:
                row_limitations.append("crosshair_interpolation_unavailable")
            if quality_enabled and target is None and not row_limitations:
                row_limitations.append("target_interpolation_unavailable")

        association_available = target is not None
        outcome_kind = visual_event_kinds.get(association.get("outcome_event_ref"))
        outcome_available = (
            target_bound_association
            and outcome_kind in {"hit", "miss"}
            and association_available
            and identity_continuity_available
            and target_association_basis != "unique_geometric"
            and association.get("target_track_ref") == track_ref
        )
        outcome_success = outcome_kind == "hit" if outcome_available else None
        change_state = "steady"
        recent_changes = [
            event for event in visual_events
            if event.get("event_kind") == "target_change_point"
            and not event.get("limitations")
            and isinstance(event.get("start_ms"), (int, float))
            and 0 <= time_ms - int(event["start_ms"]) <= 200
        ]
        if recent_changes:
            change_state = "post_change"
        condition_ref = f"condition:{motion_class}:{change_state}"
        row = {
            "event_ref": f"{analysis_ref}:dynamic-click:{index}",
            "click_ref": click_ref,
            "click_time_ms": time_ms,
            "target_track_ref": track_ref,
            "target_radius": target[0]["radius"] if target else None,
            "target_association_basis": target_association_basis,
            "miss_vector": miss_vector,
            "normalized_click_error": normalized_error,
            "target_speed": target_speed,
            "target_acceleration": target_acceleration,
            "target_relative_velocity": relative_velocity,
            "relative_velocity_magnitude": (
                hypot(relative_velocity["x"], relative_velocity["y"])
                if relative_velocity is not None else None
            ),
            "signed_lead_lag": signed_lead_lag,
            "lead_lag_descriptor": lead_lag_descriptor,
            "acquisition_start_ms": acquisition_start,
            "acquisition_time_ms": acquisition_time,
            "change_state": change_state,
            "target_motion_class": motion_class,
            "association_availability": "available" if association_available else "unavailable",
            "outcome_available": outcome_available,
            "outcome_success": outcome_success,
            "condition_ref": condition_ref,
            "limitations": sorted(set(row_limitations)),
        }
        rows.append(row)
        error_values.append(
            float(normalized_error) if normalized_error is not None else None
        )
        acquisition_values.append(
            float(acquisition_time) if acquisition_time is not None else None
        )
        relative_velocity_values.append(
            hypot(relative_velocity["x"], relative_velocity["y"])
            if relative_velocity is not None else None
        )
        outcome_values.append(
            (1.0 if outcome_success else 0.0) if outcome_available else None
        )
        event_attributes = {
            "click_ref": click_ref,
            "click_time_ms": time_ms,
            "association_availability": row["association_availability"],
            "target_association_basis": target_association_basis,
            "target_motion_class": motion_class,
            "change_state": change_state,
            "condition_ref": condition_ref,
        }
        if track_ref is not None:
            event_attributes["target_track_ref"] = track_ref
        for key, value in (
            ("normalized_click_error", normalized_error),
            ("target_radius", target[0]["radius"] if target else None),
            ("miss_vector_x", miss_vector[0] if miss_vector else None),
            ("miss_vector_y", miss_vector[1] if miss_vector else None),
            ("target_speed", target_speed),
            ("target_acceleration", target_acceleration),
            ("relative_velocity_x", relative_velocity["x"] if relative_velocity else None),
            ("relative_velocity_y", relative_velocity["y"] if relative_velocity else None),
            ("relative_velocity_magnitude", row["relative_velocity_magnitude"]),
            ("signed_lead_lag", signed_lead_lag),
            ("lead_lag_descriptor", lead_lag_descriptor),
            ("acquisition_start_ms", acquisition_start),
            ("acquisition_time_ms", acquisition_time),
            ("outcome_available", outcome_available),
            (
                "outcome_success",
                (1.0 if outcome_success else 0.0) if outcome_success is not None else None,
            ),
        ):
            if value is not None:
                event_attributes[key] = value
        dynamic_events.append({
            "event_id": row["event_ref"],
            "event_kind": "dynamic_click",
            "start_ms": time_ms,
            "end_ms": time_ms,
            "actor_refs": [track_ref] if track_ref else [],
            "source_refs": [f"{analysis_ref}:source:dynamic-analysis"],
            "confidence": min(
                float(target[0]["confidence"]) if target else 0.0,
                float(aim["confidence"]) if aim else 0.0,
            ),
            "attributes": event_attributes,
            "limitations": row["limitations"],
        })

    if not quality_enabled:
        support_status = "outcome_only"
    elif not rows or not any(row["normalized_click_error"] is not None for row in rows):
        support_status = "partial"
    elif any(row["normalized_click_error"] is None for row in rows):
        support_status = "partial"
    else:
        support_status = "supported"

    if motion_class == "predictable" and not accepted_predictability:
        limitations.append("motion_predictability_evidence_unavailable")
    metric_event_refs = [row["event_ref"] for row in rows]
    condition_refs = [row["condition_ref"] for row in rows]
    segment_refs = [segment_ref] if rows else []
    def unavailable_limitations(field: str) -> list[str]:
        return sorted({
            limitation
            for row in rows
            if row.get(field) is None
            for limitation in row["limitations"]
        })
    geometry_conditioned_limitations = (
        ["click_geometry_visible_radius_conditioned"]
        if any(
            row["target_association_basis"] == "unique_geometric"
            and row["normalized_click_error"] is not None
            for row in rows
        )
        else []
    )
    confidence = sum(event["confidence"] for event in dynamic_events) / len(dynamic_events) if dynamic_events else 0.0
    metric_records = [
        _metric(
            "dynamic_clicking.normalized_click_error", error_values,
            analysis_ref=analysis_ref,
            unit="visible_radius", event_refs=metric_event_refs, segment_refs=segment_refs,
            condition_refs=condition_refs,
            limitations=(
                limitations
                + geometry_conditioned_limitations
                + unavailable_limitations("normalized_click_error")
            ),
            confidence=confidence,
        ),
        _metric(
            "dynamic_clicking.acquisition_time_ms", acquisition_values,
            analysis_ref=analysis_ref,
            unit="ms", event_refs=metric_event_refs, segment_refs=segment_refs,
            condition_refs=condition_refs,
            limitations=limitations + unavailable_limitations("acquisition_time_ms"),
            confidence=confidence,
        ),
        _metric(
            "dynamic_clicking.relative_velocity", relative_velocity_values,
            analysis_ref=analysis_ref,
            unit="px_per_ms", event_refs=metric_event_refs, segment_refs=segment_refs,
            condition_refs=condition_refs,
            limitations=limitations + unavailable_limitations("relative_velocity_magnitude"),
            confidence=confidence,
        ),
        _metric(
            "dynamic_clicking.target_state_accuracy", outcome_values,
            analysis_ref=analysis_ref,
            unit="ratio", event_refs=metric_event_refs, segment_refs=segment_refs,
            condition_refs=condition_refs,
            limitations=(
                limitations + ["outcome_association_unavailable"]
                if not any(value is not None for value in outcome_values)
                else limitations
            ),
            confidence=confidence,
        ),
    ]
    for metric in metric_records:
        validate_metric_record_v1(metric)
    accepted_evidence = next(iter(accepted_predictability.values()), None)
    if accepted_evidence and any(row["signed_lead_lag"] is not None for row in rows):
        values = [row["signed_lead_lag"] for row in rows]
        metric_records.append(_metric(
            "dynamic_clicking.predictive_lead", values,
            analysis_ref=analysis_ref,
            unit="visible_radius", event_refs=metric_event_refs,
            segment_refs=segment_refs, condition_refs=[accepted_evidence["evidence_ref"]],
            limitations=limitations, confidence=confidence,
        ))
        validate_metric_record_v1(metric_records[-1])

    segment = {
        "schema_version": "evidence_segment.v1",
        "segment_id": segment_ref,
        "analysis_ref": analysis_ref,
        "analyzer_ref": ANALYSIS_VERSION,
        "segment_kind": "typical",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "focus_start_ms": rows[0]["click_time_ms"] if rows else start_ms,
        "focus_end_ms": min(end_ms, (rows[-1]["click_time_ms"] + 1) if rows else end_ms),
        "title_key": "dynamic_clicking.typical",
        "rank_reason": "typical",
        "issue_refs": [],
        "metric_refs": [
            f"metric:{metric['metric_key']}@{metric['metric_version']}"
            for metric in metric_records
        ],
        "event_refs": metric_event_refs + [event["event_id"] for event in predictability_events],
        "available_channels": available_channels if quality_enabled else [],
        "source_coverage": confidence,
        "confidence": confidence,
        "video_playback": {"availability": "unavailable", "artifact_ref": None, "start_ms": None, "end_ms": None},
        "limitations": sorted(set(limitations)),
    }
    if rows:
        from .analysis_evidence import validate_evidence_segment_v1
        validate_evidence_segment_v1(segment, canonical_window=dict(window))
    table = {
        "schema_version": "processed_event_table.v1",
        "table_ref": f"{analysis_ref}:table:dynamic_click",
        "analysis_ref": analysis_ref,
        "analyzer_ref": ANALYSIS_VERSION,
        "family": "dynamic_clicking",
        "event_kind": "dynamic_click",
        "row_count": len(rows),
        "included_count": len(rows),
        "excluded_count": 0,
        "completeness": "partial" if any(row["limitations"] for row in rows) else "complete",
        "field_catalog": [],
        "index_fields": ["click_ref", "target_track_ref", "click_time_ms", "change_state"],
        "rows_ref": f"{analysis_ref}:table:dynamic_click",
        "limitations": sorted(set(limitations)),
    }
    table["field_catalog"] = dynamic_processed_field_catalog_v1()
    validate_processed_event_table_v1(table)
    event_bundle = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": [*dynamic_events, *predictability_events],
        "outcome_associations": [],
    }
    validate_event_bundle_v1(event_bundle)
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "analysis_ref": analysis_ref,
        "analysis_type": "dynamic_clicking",
        "support_status": support_status,
        "scenario_motion_class": motion_class,
        "metrics": {metric["metric_key"]: metric for metric in metric_records},
        "processed_event_table": table,
        "processed_rows": rows,
        "evidence_segments": [segment] if rows else [],
        "comparison": payload.get("comparison"),
        "limitations": sorted(set(limitations)),
        "evidence_extension": {
            "event_bundle": event_bundle,
            "metric_records": metric_records,
            "evidence_segments": [segment] if rows else [],
            "processed_event_table": table,
            "predictability_events": predictability_events,
        },
    }


def extend_analysis_evidence_with_dynamic_clicking_v1(
    artifact: Mapping[str, Any], analysis_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge validated dynamic facts into the run's immutable evidence artifact."""
    from .analysis_evidence import validate_analysis_evidence_artifact

    projected = validate_analysis_evidence_artifact(artifact)
    if (
        analysis_result.get("schema_version") != SCHEMA_VERSION
        or analysis_result.get("analysis_version") != ANALYSIS_VERSION
        or analysis_result.get("analysis_ref") != projected["analysis_ref"]
    ):
        raise DynamicClickingAnalysisError("dynamic analysis result is incompatible")
    extension = analysis_result.get("evidence_extension")
    if not isinstance(extension, Mapping):
        raise DynamicClickingAnalysisError("dynamic evidence extension is missing")
    event_bundle = validate_event_bundle_v1(extension.get("event_bundle"))
    if event_bundle["analysis_ref"] != projected["analysis_ref"]:
        raise DynamicClickingAnalysisError("dynamic event bundle is bound to another analysis")
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
        requested_channels = set(segment.get("available_channels") or [])
        if not requested_channels <= available_channels:
            raise DynamicClickingAnalysisError(
                "dynamic evidence segment references unavailable channels"
            )
    existing_metric_keys = {
        metric["metric_key"] for metric in projected["metric_records"]
    }
    if existing_metric_keys.intersection(metric["metric_key"] for metric in metrics):
        raise DynamicClickingAnalysisError("dynamic metric already exists")
    projected = deepcopy(projected)
    if event_bundle["events"]:
        projected["event_bundles"].append(event_bundle)
    projected["metric_records"].extend(metrics)
    projected["evidence_segments"].extend(deepcopy(segments))
    for limitation in analysis_result.get("limitations") or []:
        if limitation not in projected["limitations"]:
            projected["limitations"].append(limitation)
    return validate_analysis_evidence_artifact(projected)


__all__ = [
    "ANALYSIS_VERSION",
    "DynamicClickingAnalysisError",
    "analyze_dynamic_clicking_v1",
    "extend_analysis_evidence_with_dynamic_clicking_v1",
]
