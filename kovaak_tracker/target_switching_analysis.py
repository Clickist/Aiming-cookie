"""Deterministic target-switching analysis over validated visual facts."""

from __future__ import annotations

from copy import deepcopy
from math import atan2, degrees, hypot, isfinite
from statistics import median
from typing import Any, Mapping, Sequence


ANALYSIS_VERSION = "target_switching.v1"
SCHEMA_VERSION = "target_switching_analysis.v1"
INPUT_SCHEMA_VERSION = "target_switching_input.v1"
_REF_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._@+-")


class TargetSwitchingAnalysisError(ValueError):
    pass


def _ref(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or any(character not in _REF_CHARS for character in value)
    ):
        raise TargetSwitchingAnalysisError(f"{field} is invalid")
    return value


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise TargetSwitchingAnalysisError(f"{field} is invalid")
    result = float(value)
    if minimum is not None and result < minimum:
        raise TargetSwitchingAnalysisError(f"{field} is invalid")
    return result


def _time(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    result = _number(value, field, minimum=minimum)
    if int(result) != result or result >= maximum:
        raise TargetSwitchingAnalysisError(f"{field} is outside canonical window")
    return int(result)


def _samples(value: Any, field: str) -> list[dict[str, float | int]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise TargetSwitchingAnalysisError(f"{field} must be a non-empty list")
    result: list[dict[str, float | int]] = []
    previous: int | None = None
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"{field}[{index}] is invalid")
        timestamp = _number(
            raw.get("canonical_time_ms"), f"{field}[{index}].canonical_time_ms", minimum=0,
        )
        if int(timestamp) != timestamp or (previous is not None and int(timestamp) <= previous):
            raise TargetSwitchingAnalysisError(f"{field} timestamps are not strictly ordered")
        result.append({
            "canonical_time_ms": int(timestamp),
            "x": _number(raw.get("x"), f"{field}[{index}].x"),
            "y": _number(raw.get("y"), f"{field}[{index}].y"),
        })
        previous = int(timestamp)
    return result


def _point_at(
    samples: Sequence[Mapping[str, float | int]], time_ms: int,
) -> tuple[float, float] | None:
    for sample in samples:
        if sample["canonical_time_ms"] == time_ms:
            return float(sample["x"]), float(sample["y"])
    for left, right in zip(samples, samples[1:]):
        left_time = int(left["canonical_time_ms"])
        right_time = int(right["canonical_time_ms"])
        if left_time < time_ms < right_time:
            ratio = (time_ms - left_time) / (right_time - left_time)
            return (
                float(left["x"]) + (float(right["x"]) - float(left["x"])) * ratio,
                float(left["y"]) + (float(right["y"]) - float(left["y"])) * ratio,
            )
    return None


def _relative_path_between(
    crosshair_samples: Sequence[Mapping[str, float | int]],
    target_samples: Sequence[Mapping[str, float | int]],
    start_ms: int,
    end_ms: int,
) -> float | None:
    times = sorted({
        start_ms,
        end_ms,
        *(
            int(sample["canonical_time_ms"])
            for sample in crosshair_samples
            if start_ms < int(sample["canonical_time_ms"]) < end_ms
        ),
        *(
            int(sample["canonical_time_ms"])
            for sample in target_samples
            if start_ms < int(sample["canonical_time_ms"]) < end_ms
        ),
    })
    relative_points: list[tuple[float, float]] = []
    for time_ms in times:
        crosshair = _point_at(crosshair_samples, time_ms)
        target = _point_at(target_samples, time_ms)
        if crosshair is None or target is None:
            return None
        relative_points.append((
            target[0] - crosshair[0],
            target[1] - crosshair[1],
        ))
    if len(relative_points) < 2:
        return None
    return sum(
        hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(relative_points, relative_points[1:])
    )


def _associations(
    value: Any, analysis_ref: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from .analysis_evidence import validate_event_bundle

    try:
        source_bundle = validate_event_bundle(value)
    except ValueError as exc:
        raise TargetSwitchingAnalysisError("source outcome evidence is invalid") from exc
    if source_bundle["analysis_ref"] != analysis_ref:
        raise TargetSwitchingAnalysisError("source outcome evidence is bound to another analysis")
    events_by_id = {event["event_id"]: event for event in source_bundle["events"]}
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(source_bundle["outcome_associations"]):
        association_id = _ref(
            raw.get("association_id"), f"outcome_associations[{index}].association_id",
        )
        if association_id in result:
            raise TargetSwitchingAnalysisError("duplicate outcome association")
        limitations = raw.get("limitations", [])
        if isinstance(limitations, (str, bytes)) or not isinstance(limitations, Sequence):
            raise TargetSwitchingAnalysisError("outcome association limitations must be a list")
        normalized = {
            "association_id": association_id,
            "association_kind": raw.get("association_kind"),
            "availability": raw.get("availability"),
            "confidence": _number(raw.get("confidence", 0.0), "outcome association confidence"),
            "limitations": [_ref(item, "outcome association limitation") for item in limitations],
            "target_track_ref": (
                _ref(raw.get("target_track_ref"), "outcome association target_track_ref")
                if raw.get("target_track_ref") is not None else None
            ),
            "shot_event_ref": (
                _ref(raw.get("shot_event_ref"), "outcome association shot_event_ref")
                if raw.get("shot_event_ref") is not None else None
            ),
            "outcome_event_ref": (
                _ref(raw.get("outcome_event_ref"), "outcome association outcome_event_ref")
                if raw.get("outcome_event_ref") is not None else None
            ),
            "canonical": deepcopy(raw),
        }
        shot_event = events_by_id.get(normalized["shot_event_ref"])
        outcome_event = events_by_id.get(normalized["outcome_event_ref"])
        normalized["shot_time_ms"] = (
            int(shot_event["start_ms"])
            if shot_event is not None and shot_event["start_ms"] == shot_event["end_ms"]
            else None
        )
        normalized["outcome_time_ms"] = (
            int(outcome_event["start_ms"])
            if outcome_event is not None and outcome_event["start_ms"] == outcome_event["end_ms"]
            else None
        )
        normalized["outcome_event_kind"] = (
            outcome_event.get("event_kind") if outcome_event is not None else None
        )
        normalized["trusted"] = (
            normalized["association_kind"] in {"directly_observed", "validated_aligned"}
            and normalized["availability"] == "available"
            and normalized["confidence"] == 1.0
            and not normalized["limitations"]
            and normalized["target_track_ref"] is not None
            and normalized["shot_event_ref"] is not None
            and normalized["outcome_event_ref"] is not None
            and normalized["shot_time_ms"] is not None
            and normalized["outcome_time_ms"] is not None
        )
        result[association_id] = normalized
    return source_bundle, result


def _source_signals(
    bundle_value: Any,
    sample_sets_value: Any,
    analysis_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[list[float]]]]:
    from .analysis_evidence import validate_signal_bundle_v1

    try:
        bundle = validate_signal_bundle_v1(bundle_value)
    except ValueError as exc:
        raise TargetSwitchingAnalysisError("source signal evidence is invalid") from exc
    if bundle["analysis_ref"] != analysis_ref:
        raise TargetSwitchingAnalysisError("source signal evidence is bound to another analysis")
    if isinstance(sample_sets_value, (str, bytes)) or not isinstance(sample_sets_value, Sequence):
        raise TargetSwitchingAnalysisError("source sample sets must be a list")
    sample_sets: list[dict[str, Any]] = []
    by_ref: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(sample_sets_value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "sample_set_id", "channel_key", "unit", "points",
        }:
            raise TargetSwitchingAnalysisError(f"source_sample_sets[{index}] is invalid")
        sample_ref = _ref(raw.get("sample_set_id"), "source sample_set_id")
        if sample_ref in by_ref:
            raise TargetSwitchingAnalysisError("duplicate source sample set")
        channel_key = _ref(raw.get("channel_key"), "source sample channel_key")
        _ref(raw.get("unit"), "source sample unit")
        points = raw.get("points")
        if isinstance(points, (str, bytes)) or not isinstance(points, Sequence) or not points:
            raise TargetSwitchingAnalysisError("source sample points must be non-empty")
        normalized_points: list[list[float]] = []
        previous_time: float | None = None
        for point in points:
            if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
                raise TargetSwitchingAnalysisError("source sample point is invalid")
            time_ms = _number(point[0], "source sample time", minimum=0)
            value = _number(point[1], "source sample value")
            if previous_time is not None and time_ms <= previous_time:
                raise TargetSwitchingAnalysisError("source sample times are not strictly ordered")
            normalized_points.append([time_ms, value])
            previous_time = time_ms
        normalized = {
            "sample_set_id": sample_ref,
            "channel_key": channel_key,
            "unit": raw["unit"],
            "points": normalized_points,
        }
        by_ref[sample_ref] = normalized
        sample_sets.append(normalized)
    channel_points: dict[str, list[list[float]]] = {}
    for channel in bundle["channels"]:
        sample_set = by_ref.get(channel["samples_ref"])
        if (
            sample_set is None
            or sample_set["channel_key"] != channel["channel_key"]
            or sample_set["unit"] != channel["unit"]
        ):
            raise TargetSwitchingAnalysisError("source signal channel is not bound to its sample set")
        if (
            channel["channel_key"] in {"crosshair.position_x", "crosshair.position_y"}
            or channel["channel_key"].startswith("target.")
            and channel["channel_key"].endswith((".position_x", ".position_y"))
        ) and channel["unit"] != "px":
            raise TargetSwitchingAnalysisError("source position channel unit must be px")
        channel_points[channel["channel_key"]] = sample_set["points"]
    if set(by_ref) != {channel["samples_ref"] for channel in bundle["channels"]}:
        raise TargetSwitchingAnalysisError("source sample sets contain unreferenced data")
    return bundle, sample_sets, channel_points


def _xy_samples_from_channels(
    channels: Mapping[str, list[list[float]]], x_key: str, y_key: str,
) -> list[dict[str, float | int]]:
    x_points = channels.get(x_key)
    y_points = channels.get(y_key)
    if x_points is None or y_points is None or len(x_points) != len(y_points):
        raise TargetSwitchingAnalysisError("source position channels are incomplete")
    result = []
    for x_point, y_point in zip(x_points, y_points):
        if x_point[0] != y_point[0] or int(x_point[0]) != x_point[0]:
            raise TargetSwitchingAnalysisError("source position channels are not aligned")
        result.append({
            "canonical_time_ms": int(x_point[0]),
            "x": float(x_point[1]),
            "y": float(y_point[1]),
        })
    return result


def _direct_observation(
    value: Any, *, field: str, target_field: str | None = None,
) -> tuple[Any | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping) or value.get("association_kind") != "directly_observed":
        return None, None
    observation_ref = _ref(value.get("observation_ref"), f"{field}.observation_ref")
    if target_field is None:
        observed = value.get("observed")
        if not isinstance(observed, bool):
            raise TargetSwitchingAnalysisError(f"{field}.observed is invalid")
        return observed, observation_ref
    target_ref = _ref(value.get(target_field), f"{field}.{target_field}")
    return target_ref, observation_ref


def _visual_chain_samples(
    value: Any,
    field: str,
    *,
    require_radius: bool,
) -> list[dict[str, float | int]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise TargetSwitchingAnalysisError(f"{field} must be a non-empty list")
    samples: list[dict[str, float | int]] = []
    previous_time: int | None = None
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"{field}[{index}] is invalid")
        time_ms = _number(
            raw.get("canonical_time_ms"),
            f"{field}[{index}].canonical_time_ms",
            minimum=0,
        )
        if int(time_ms) != time_ms or (
            previous_time is not None and int(time_ms) <= previous_time
        ):
            raise TargetSwitchingAnalysisError(f"{field} timestamps are not strictly ordered")
        sample: dict[str, float | int] = {
            "canonical_time_ms": int(time_ms),
            "x": _number(raw.get("x"), f"{field}[{index}].x"),
            "y": _number(raw.get("y"), f"{field}[{index}].y"),
            "confidence": _number(
                raw.get("confidence"), f"{field}[{index}].confidence", minimum=0,
            ),
        }
        if not 0 <= float(sample["confidence"]) <= 1:
            raise TargetSwitchingAnalysisError(f"{field}[{index}].confidence is invalid")
        if require_radius:
            sample["visible_radius"] = _number(
                raw.get("visible_radius"),
                f"{field}[{index}].visible_radius",
                minimum=0.01,
            )
        samples.append(sample)
        previous_time = int(time_ms)
    return samples


def build_switching_chains_from_visual_outcomes_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, Any],
    crosshair_samples: Sequence[Mapping[str, Any]],
    target_tracks: Sequence[Mapping[str, Any]],
    source_event_bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Derive observable chains without inferring selection or outcome identity."""
    analysis_ref = _ref(analysis_ref, "analysis_ref")
    if not isinstance(canonical_time_window, Mapping):
        raise TargetSwitchingAnalysisError("canonical_time_window is required")
    start_ms = int(_number(
        canonical_time_window.get("start_ms"),
        "canonical_time_window.start_ms",
        minimum=0,
    ))
    end_ms = int(_number(
        canonical_time_window.get("end_ms"),
        "canonical_time_window.end_ms",
        minimum=start_ms + 1,
    ))
    if end_ms <= start_ms:
        raise TargetSwitchingAnalysisError("canonical_time_window is invalid")
    crosshair = _visual_chain_samples(
        crosshair_samples, "crosshair_samples", require_radius=False,
    )
    crosshair_by_time = {
        int(sample["canonical_time_ms"]): sample for sample in crosshair
    }
    if isinstance(target_tracks, (str, bytes)) or not isinstance(target_tracks, Sequence):
        raise TargetSwitchingAnalysisError("target_tracks must be a list")
    tracks: dict[str, list[dict[str, float | int]]] = {}
    stable_tracks: set[str] = set()
    target_prefix = f"{analysis_ref}:target-track:"
    for index, raw in enumerate(target_tracks):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"target_tracks[{index}] is invalid")
        track_ref = _ref(raw.get("track_ref"), f"target_tracks[{index}].track_ref")
        if not track_ref.startswith(target_prefix) or track_ref in tracks:
            raise TargetSwitchingAnalysisError("target track is invalid")
        tracks[track_ref] = _visual_chain_samples(
            raw.get("samples"), f"target_tracks[{index}].samples", require_radius=True,
        )
        if raw.get("identity_observable") is True:
            stable_tracks.add(track_ref)
    _, associations = _associations(source_event_bundle, analysis_ref)
    trusted = sorted(
        (
            association for association in associations.values()
            if association["trusted"]
            and association["target_track_ref"] in stable_tracks
            and start_ms <= association["shot_time_ms"] < end_ms
            and start_ms <= association["outcome_time_ms"] < end_ms
            and association["shot_time_ms"] <= association["outcome_time_ms"]
        ),
        key=lambda association: (
            association["outcome_time_ms"], association["association_id"],
        ),
    )
    chains: list[dict[str, Any]] = []
    for previous, current in zip(trusted, trusted[1:]):
        previous_track = previous["target_track_ref"]
        next_track = current["target_track_ref"]
        if previous_track == next_track:
            continue
        leave_time = int(previous["outcome_time_ms"])
        shot_time = int(current["shot_time_ms"])
        outcome_time = int(current["outcome_time_ms"])
        if shot_time < leave_time:
            continue
        states: list[tuple[int, bool]] = []
        for sample in tracks[next_track]:
            time_ms = int(sample["canonical_time_ms"])
            crosshair_sample = crosshair_by_time.get(time_ms)
            if (
                not leave_time <= time_ms <= shot_time
                or crosshair_sample is None
                or float(sample["confidence"]) != 1.0
                or float(crosshair_sample["confidence"]) != 1.0
            ):
                continue
            inside = hypot(
                float(sample["x"]) - float(crosshair_sample["x"]),
                float(sample["y"]) - float(crosshair_sample["y"]),
            ) <= float(sample["visible_radius"])
            states.append((time_ms, inside))
        inside_indexes = [index for index, (_, inside) in enumerate(states) if inside]
        if not inside_indexes or not states[-1][1]:
            continue
        acquire_index = inside_indexes[0]
        settle_index = len(states) - 1
        while settle_index > 0 and states[settle_index - 1][1]:
            settle_index -= 1
        acquire_time = states[acquire_index][0]
        settle_time = states[settle_index][0]
        if acquire_time > shot_time or settle_time > shot_time:
            continue
        candidates = sorted(
            track_ref for track_ref in stable_tracks
            if track_ref != previous_track
            and any(
                int(sample["canonical_time_ms"]) == leave_time
                and float(sample["confidence"]) == 1.0
                for sample in tracks[track_ref]
            )
        )
        chains.append({
            "chain_ref": f"{analysis_ref}:observed-switch-chain:{len(chains) + 1}",
            "source_refs": sorted([
                previous["association_id"], current["association_id"],
            ]),
            "previous_outcome_association_ref": previous["association_id"],
            "previous_outcome_time_ms": leave_time,
            "leave_time_ms": leave_time,
            "candidate_track_refs": candidates,
            "selection_observation": None,
            "next_target_track_ref": next_track,
            "acquire_time_ms": acquire_time,
            "settle_time_ms": settle_time,
            "first_shot_time_ms": shot_time,
            "next_outcome_association_ref": current["association_id"],
            "next_outcome_time_ms": outcome_time,
            "first_damage_association_ref": (
                current["association_id"]
                if current["outcome_event_kind"] in {"hit", "first_damage"}
                else None
            ),
            "first_damage_time_ms": (
                outcome_time
                if current["outcome_event_kind"] in {"hit", "first_damage"}
                else None
            ),
            "carry_over_overshoot": None,
            "terminal_correction": None,
        })
    return chains


def _metric_record(
    key: str,
    values: Sequence[float | None],
    *,
    unit: str,
    analysis_ref: str,
    event_refs: Sequence[str],
    segment_refs: Sequence[str],
    condition_refs: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    valid = [float(value) for value in values if value is not None and isfinite(float(value))]
    ordered = sorted(valid)
    distribution = None
    if ordered:
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
        "availability": "available" if valid else "unavailable",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": [f"{analysis_ref}:source:target-switching-analysis"],
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
        "confidence": 1.0 if valid else 0.0,
        "limitations": sorted(set(limitations)),
    }


def _processed_tables(analysis_ref: str, rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    from .analysis_evidence import (
        target_switching_processed_field_catalog_v1,
        validate_processed_event_table_v1,
    )

    tables = []
    for row_kind in sorted({row["row_kind"] for row in rows}):
        kind_rows = [row for row in rows if row["row_kind"] == row_kind]
        limitations = sorted({
            limitation for row in kind_rows for limitation in row["limitations"]
        })
        table = {
            "schema_version": "processed_event_table.v1",
            "table_ref": f"{analysis_ref}:table:{row_kind}",
            "analysis_ref": analysis_ref,
            "analyzer_ref": ANALYSIS_VERSION,
            "family": "target_switching",
            "event_kind": row_kind,
            "row_count": len(kind_rows),
            "included_count": len(kind_rows),
            "excluded_count": 0,
            "completeness": "partial" if limitations else "complete",
            "field_catalog": target_switching_processed_field_catalog_v1(row_kind),
            "index_fields": ["start_ms", "end_ms", "previous_target_track_ref", "next_target_track_ref"],
            "rows_ref": f"{analysis_ref}:table:{row_kind}",
            "limitations": limitations,
        }
        tables.append(validate_processed_event_table_v1(table))
    return tables


def _segment(
    *,
    analysis_ref: str,
    window: Mapping[str, Any],
    segment_id: str,
    title_key: str,
    start_ms: int,
    end_ms: int,
    focus_ms: int,
    metric_refs: Sequence[str],
    event_refs: Sequence[str],
    limitations: Sequence[str],
) -> dict[str, Any]:
    from .analysis_evidence import validate_evidence_segment_v1

    return validate_evidence_segment_v1({
        "schema_version": "evidence_segment.v1",
        "segment_id": segment_id,
        "analysis_ref": analysis_ref,
        "analyzer_ref": ANALYSIS_VERSION,
        "segment_kind": "typical",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "focus_start_ms": max(start_ms, focus_ms - 250),
        "focus_end_ms": min(end_ms, focus_ms + 250),
        "title_key": title_key,
        "rank_reason": "typical",
        "issue_refs": [],
        "metric_refs": list(metric_refs),
        "event_refs": list(event_refs),
        "available_channels": [],
        "source_coverage": 1.0,
        "confidence": 1.0,
        "video_playback": {
            "availability": "unavailable",
            "artifact_ref": None,
            "start_ms": None,
            "end_ms": None,
        },
        "limitations": sorted(set(limitations)),
    }, canonical_window=dict(window))


def analyze_target_switching_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise TargetSwitchingAnalysisError("target switching input schema is unsupported")
    analysis_ref = _ref(payload.get("analysis_ref"), "analysis_ref")
    window = payload.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise TargetSwitchingAnalysisError("canonical_time_window is required")
    start_ms = int(_number(window.get("start_ms"), "canonical_time_window.start_ms", minimum=0))
    end_ms = int(_number(window.get("end_ms"), "canonical_time_window.end_ms", minimum=start_ms + 1))
    if end_ms <= start_ms:
        raise TargetSwitchingAnalysisError("canonical_time_window is invalid")
    resolution = payload.get("scenario_resolution")
    if not isinstance(resolution, Mapping) or resolution.get("aim_family") != "target_switching":
        raise TargetSwitchingAnalysisError("scenario_resolution must be target_switching")
    quality = payload.get("visual_quality")
    if not isinstance(quality, Mapping):
        raise TargetSwitchingAnalysisError("visual_quality is required")
    if not (
        quality.get("status") in {"accepted", "limited"}
        and "target_switching" in (quality.get("enabled_metric_families") or [])
    ):
        return {
            "schema_version": SCHEMA_VERSION,
            "analysis_version": ANALYSIS_VERSION,
            "analysis_ref": analysis_ref,
            "analysis_type": "target_switching",
            "support_status": "outcome_only",
            "processed_rows": [],
            "processed_event_tables": [],
            "metrics": {},
            "evidence_segments": [],
            "comparison": payload.get("comparison"),
            "limitations": ["target_switching_visual_quality_unavailable"],
            "evidence_extension": {
                "event_bundle": {
                    "schema_version": "event_bundle.v1",
                    "analysis_ref": analysis_ref,
                    "events": [],
                    "outcome_associations": [],
                },
                "metric_records": [],
                "evidence_segments": [],
                "processed_event_tables": [],
                "required_outcome_associations": [],
                "required_signal_bundle": None,
                "required_sample_sets": [],
                "required_canonical_time_window": dict(window),
            },
        }

    tracks_raw = payload.get("target_tracks")
    if isinstance(tracks_raw, (str, bytes)) or not isinstance(tracks_raw, Sequence):
        raise TargetSwitchingAnalysisError("target_tracks must be a list")
    tracks: dict[str, list[dict[str, float | int]]] = {}
    ambiguous_tracks: set[str] = set()
    for index, raw in enumerate(tracks_raw):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"target_tracks[{index}] is invalid")
        track_ref = _ref(raw.get("track_ref"), f"target_tracks[{index}].track_ref")
        if track_ref in tracks:
            raise TargetSwitchingAnalysisError("duplicate target track")
        tracks[track_ref] = _samples(raw.get("samples"), f"target_tracks[{index}].samples")
        if raw.get("identity_observable", True) is not True:
            ambiguous_tracks.add(track_ref)
    crosshair = _samples(payload.get("crosshair_samples"), "crosshair_samples")
    source_signal_bundle, source_sample_sets, source_channel_points = _source_signals(
        payload.get("source_signal_bundle"),
        payload.get("source_sample_sets"),
        analysis_ref,
    )
    if crosshair != _xy_samples_from_channels(
        source_channel_points, "crosshair.position_x", "crosshair.position_y",
    ):
        raise TargetSwitchingAnalysisError("crosshair samples do not match source signals")
    target_prefix = f"{analysis_ref}:target-track:"
    for track_ref, track_samples in tracks.items():
        if not track_ref.startswith(target_prefix):
            raise TargetSwitchingAnalysisError("target track is not analysis-bound")
        track_id = track_ref[len(target_prefix):]
        if track_samples != _xy_samples_from_channels(
            source_channel_points,
            f"target.{track_id}.position_x",
            f"target.{track_id}.position_y",
        ):
            raise TargetSwitchingAnalysisError("target samples do not match source signals")
    source_event_bundle, associations = _associations(
        payload.get("source_event_bundle"), analysis_ref,
    )
    chains_raw = payload.get("chains")
    if isinstance(chains_raw, (str, bytes)) or not isinstance(chains_raw, Sequence):
        raise TargetSwitchingAnalysisError("chains must be a list")

    rows: list[dict[str, Any]] = []
    state_events: list[dict[str, Any]] = []
    segment_specs: list[dict[str, Any]] = []
    required_associations: dict[str, dict[str, Any]] = {}
    seen_chain_refs: set[str] = set()
    for index, raw in enumerate(chains_raw, 1):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"chains[{index - 1}] is invalid")
        chain_ref = _ref(raw.get("chain_ref"), f"chains[{index - 1}].chain_ref")
        if chain_ref in seen_chain_refs:
            raise TargetSwitchingAnalysisError("duplicate switch chain ref")
        seen_chain_refs.add(chain_ref)
        source_refs_raw = raw.get("source_refs")
        if isinstance(source_refs_raw, (str, bytes)) or not isinstance(source_refs_raw, Sequence) or not source_refs_raw:
            raise TargetSwitchingAnalysisError("switch chain source_refs are required")
        source_refs = [_ref(value, "switch chain source_ref") for value in source_refs_raw]
        leave_time = _time(raw.get("leave_time_ms"), "leave_time_ms", minimum=start_ms, maximum=end_ms)
        acquire_time = _time(raw.get("acquire_time_ms"), "acquire_time_ms", minimum=leave_time, maximum=end_ms)
        settle_time = _time(raw.get("settle_time_ms"), "settle_time_ms", minimum=acquire_time, maximum=end_ms)
        event_ref = f"{analysis_ref}:switch-chain:{index}"
        limitations: list[str] = []

        previous = associations.get(raw.get("previous_outcome_association_ref"))
        previous_track = previous["target_track_ref"] if previous and previous["trusted"] else None
        previous_outcome_time = raw.get("previous_outcome_time_ms")
        if previous_track is None:
            previous_outcome_time = None
            limitations.append("previous_outcome_association_unavailable")
        elif previous_track not in tracks:
            previous_track = None
            previous_outcome_time = None
            limitations.append("previous_target_identity_unavailable")
        else:
            requested_previous_time = _time(
                previous_outcome_time, "previous_outcome_time_ms", minimum=start_ms, maximum=end_ms,
            )
            if requested_previous_time != previous["outcome_time_ms"]:
                raise TargetSwitchingAnalysisError("previous outcome time does not match source evidence")
            previous_outcome_time = previous["outcome_time_ms"]
            required_associations[previous["association_id"]] = previous["canonical"]
            if previous_outcome_time > leave_time:
                raise TargetSwitchingAnalysisError("previous outcome cannot follow leave")
            if previous_track in ambiguous_tracks:
                previous_track = None
                limitations.append("previous_target_identity_ambiguous")

        requested_next = raw.get("next_target_track_ref")
        next_track = _ref(requested_next, "next_target_track_ref") if requested_next is not None else None
        if next_track not in tracks:
            next_track = None
            limitations.append("next_target_identity_unavailable")
        elif next_track in ambiguous_tracks:
            next_track = None
            limitations.append("next_target_identity_ambiguous")

        candidates_raw = raw.get("candidate_track_refs", [])
        if isinstance(candidates_raw, (str, bytes)) or not isinstance(candidates_raw, Sequence):
            raise TargetSwitchingAnalysisError("candidate_track_refs must be a list")
        candidates = [_ref(value, "candidate_track_ref") for value in candidates_raw]
        if len(set(candidates)) != len(candidates) or any(value not in tracks for value in candidates):
            raise TargetSwitchingAnalysisError("candidate_track_refs are invalid")
        selected_track, selection_ref = _direct_observation(
            raw.get("selection_observation"),
            field="selection_observation",
            target_field="selected_target_track_ref",
        )
        if selected_track is not None and selected_track not in tracks:
            raise TargetSwitchingAnalysisError("selection references an unknown target")
        if selected_track in ambiguous_tracks:
            selected_track = None
            selection_ref = None
            limitations.append("selected_target_identity_ambiguous")
        if selected_track is not None and candidates and selected_track not in candidates:
            raise TargetSwitchingAnalysisError("selected target is not an observed candidate")
        if selected_track is None and len(candidates) > 1:
            limitations.append("selection_unobservable")

        crosshair_at_leave = _point_at(crosshair, leave_time)
        crosshair_at_acquire = _point_at(crosshair, acquire_time)
        next_at_leave = _point_at(tracks[next_track], leave_time) if next_track else None
        next_at_acquire = _point_at(tracks[next_track], acquire_time) if next_track else None
        path_length = (
            _relative_path_between(crosshair, tracks[next_track], leave_time, acquire_time)
            if next_track else None
        )
        departure_error = (
            (next_at_leave[0] - crosshair_at_leave[0], next_at_leave[1] - crosshair_at_leave[1])
            if next_at_leave is not None and crosshair_at_leave is not None else None
        )
        arrival_error = (
            (next_at_acquire[0] - crosshair_at_acquire[0], next_at_acquire[1] - crosshair_at_acquire[1])
            if next_at_acquire is not None and crosshair_at_acquire is not None else None
        )
        transition_distance = (
            hypot(
                arrival_error[0] - departure_error[0],
                arrival_error[1] - departure_error[1],
            )
            if departure_error is not None and arrival_error is not None else None
        )
        transition_direction = (
            degrees(atan2(departure_error[1], departure_error[0]))
            if departure_error is not None and transition_distance is not None
            and transition_distance > 0 else None
        )
        path_efficiency = (
            transition_distance / path_length
            if transition_distance is not None and path_length is not None and path_length > 0 else None
        )
        if transition_distance is None or path_length is None:
            limitations.append("transition_geometry_unavailable")

        next_outcome_ref = raw.get("next_outcome_association_ref")
        if next_outcome_ref is None:
            next_outcome_ref = raw.get("first_damage_association_ref")
        next_association = associations.get(next_outcome_ref)
        first_damage_time = None
        if (
            next_association is not None
            and next_association["trusted"]
            and next_association["target_track_ref"] == next_track
            and raw.get("first_shot_time_ms") is not None
        ):
            requested_shot_time = _time(
                raw.get("first_shot_time_ms"), "first_shot_time_ms", minimum=acquire_time, maximum=end_ms,
            )
            requested_outcome_time = raw.get("next_outcome_time_ms")
            if requested_outcome_time is None:
                requested_outcome_time = raw.get("first_damage_time_ms")
            if requested_outcome_time is not None:
                requested_outcome_time = _time(
                    requested_outcome_time,
                    "next_outcome_time_ms",
                    minimum=acquire_time,
                    maximum=end_ms,
                )
            if (
                requested_shot_time != next_association["shot_time_ms"]
                or requested_outcome_time is not None
                and requested_outcome_time != next_association["outcome_time_ms"]
            ):
                raise TargetSwitchingAnalysisError("first shot or outcome time does not match source evidence")
            first_shot_time = next_association["shot_time_ms"]
            required_associations[next_association["association_id"]] = next_association["canonical"]
            if next_association["outcome_event_kind"] in {"hit", "first_damage"}:
                requested_damage_ref = raw.get("first_damage_association_ref")
                requested_damage_time = raw.get("first_damage_time_ms")
                if (
                    requested_damage_ref != next_association["association_id"]
                    or requested_damage_time is None
                    or _time(
                        requested_damage_time,
                        "first_damage_time_ms",
                        minimum=acquire_time,
                        maximum=end_ms,
                    ) != next_association["outcome_time_ms"]
                ):
                    raise TargetSwitchingAnalysisError(
                        "first damage does not match source evidence"
                    )
                first_damage_time = next_association["outcome_time_ms"]
                if first_shot_time > first_damage_time:
                    raise TargetSwitchingAnalysisError("first shot cannot follow first damage")
            else:
                limitations.append("first_damage_not_observed")
        else:
            first_shot_time = None
            if next_outcome_ref is not None:
                limitations.append("first_damage_association_unavailable")

        carry_over_overshoot, overshoot_ref = _direct_observation(
            raw.get("carry_over_overshoot"), field="carry_over_overshoot",
        )
        terminal_correction, terminal_ref = _direct_observation(
            raw.get("terminal_correction"), field="terminal_correction",
        )
        full_chain = (
            previous_track is not None
            and previous_outcome_time is not None
            and next_track is not None
            and transition_distance is not None
            and path_length is not None
        )
        classification = "observable_target_switch" if full_chain else "unclassified_discrete_acquisition"
        if not full_chain:
            selected_track = None
            selection_ref = None
        end_time = max(
            settle_time,
            first_shot_time if first_shot_time is not None else settle_time,
            first_damage_time if first_damage_time is not None else settle_time,
        )
        row = {
            "event_ref": event_ref,
            "row_kind": "switch_chain" if full_chain else "unclassified_discrete_acquisition",
            "start_ms": previous_outcome_time if previous_outcome_time is not None else leave_time,
            "end_ms": end_time,
            "chain_ref": chain_ref,
            "classification": classification,
            "previous_outcome_association_ref": (
                previous["association_id"]
                if previous_track is not None and previous_outcome_time is not None
                else None
            ),
            "previous_target_track_ref": previous_track,
            "previous_outcome_time_ms": previous_outcome_time,
            "leave_time_ms": leave_time,
            "candidate_count": len(candidates),
            "selection_observation_ref": selection_ref,
            "selected_target_track_ref": selected_track,
            "next_target_track_ref": next_track,
            "acquire_time_ms": acquire_time,
            "settle_time_ms": settle_time,
            "transition_time_ms": acquire_time - leave_time if full_chain else None,
            "transition_distance_px": transition_distance if full_chain else None,
            "transition_direction_deg": transition_direction if full_chain else None,
            "transition_path_length_px": path_length if full_chain else None,
            "path_efficiency": path_efficiency if full_chain else None,
            "settle_duration_ms": settle_time - acquire_time,
            "first_shot_event_ref": (
                next_association["shot_event_ref"]
                if first_shot_time is not None
                else None
            ),
            "first_shot_latency_ms": first_shot_time - acquire_time if first_shot_time is not None else None,
            "first_damage_event_ref": (
                next_association["outcome_event_ref"]
                if first_damage_time is not None
                else None
            ),
            "first_damage_latency_ms": first_damage_time - acquire_time if first_damage_time is not None else None,
            "carry_over_overshoot": carry_over_overshoot,
            "carry_over_overshoot_observation_ref": overshoot_ref,
            "terminal_correction_observed": terminal_correction,
            "terminal_correction_observation_ref": terminal_ref,
            "limitations": sorted(set(limitations)),
        }
        rows.append(row)

        def state(kind: str, time_ms: int, actor_refs: Sequence[str], extra_sources: Sequence[str] = ()) -> str:
            state_ref = f"{event_ref}:{kind}"
            state_events.append({
                "event_id": state_ref,
                "event_kind": kind,
                "start_ms": time_ms,
                "end_ms": time_ms,
                "actor_refs": list(actor_refs),
                "source_refs": sorted(set([*source_refs, *extra_sources])),
                "confidence": 1.0,
                "attributes": {"row_ref": event_ref},
                "limitations": list(row["limitations"]),
            })
            return state_ref

        row_state_refs: dict[str, str] = {}
        if full_chain:
            row_state_refs["previous"] = state(
                "switch_previous_outcome", previous_outcome_time, [previous_track],
                [previous["association_id"]],
            )
            row_state_refs["leave"] = state("leave_previous", leave_time, [previous_track])
            if candidates:
                row_state_refs["candidates"] = state("candidate_visible", leave_time, candidates)
            if selected_track is not None and selection_ref is not None:
                row_state_refs["selection"] = state(
                    "target_selected", leave_time, [selected_track], [selection_ref],
                )
            row_state_refs["transition"] = state(
                "transition", leave_time, [previous_track, next_track],
            )
            row_state_refs["acquire"] = state("next_target_acquired", acquire_time, [next_track])
            row_state_refs["settle"] = state("settle", settle_time, [next_track])
            if first_shot_time is not None:
                row_state_refs["shot"] = state(
                    "switch_first_shot", first_shot_time, [next_track],
                    [next_association["shot_event_ref"]],
                )
            if first_damage_time is not None:
                row_state_refs["damage"] = state(
                    "first_damage", first_damage_time, [next_track],
                    [next_association["outcome_event_ref"]],
                )
        if not full_chain:
            segment_specs.append({
                "id": f"{event_ref}:segment:unclassified-acquisition",
                "title": "target_switching.unclassified_acquisition",
                "focus": acquire_time,
                "events": [event_ref],
                "metrics": [],
                "row": row,
            })
            continue
        segment_specs.extend([
            {
                "id": f"{event_ref}:segment:selection", "title": "target_switching.selection",
                "focus": leave_time, "events": [event_ref, *([row_state_refs["selection"]] if "selection" in row_state_refs else [])],
                "metrics": [], "row": row,
            },
            {
                "id": f"{event_ref}:segment:transition", "title": "target_switching.transition",
                "focus": leave_time, "events": [event_ref, *([row_state_refs["transition"]] if "transition" in row_state_refs else [])],
                "metrics": [
                    "metric:target_switching.transition_time_ms@target_switching.transition_time_ms.v1",
                    "metric:target_switching.path_efficiency@target_switching.path_efficiency.v1",
                ], "row": row,
            },
            {
                "id": f"{event_ref}:segment:acquisition", "title": "target_switching.acquisition",
                "focus": acquire_time, "events": [event_ref, *([row_state_refs["acquire"]] if "acquire" in row_state_refs else [])],
                "metrics": ["metric:target_switching.transition_time_ms@target_switching.transition_time_ms.v1"], "row": row,
            },
            {
                "id": f"{event_ref}:segment:terminal", "title": "target_switching.terminal_control",
                "focus": settle_time, "events": [event_ref, *[row_state_refs[key] for key in ("settle", "shot", "damage") if key in row_state_refs]],
                "metrics": [
                    "metric:target_switching.settle_duration_ms@target_switching.settle_duration_ms.v1",
                    "metric:target_switching.first_damage_latency_ms@target_switching.first_damage_latency_ms.v1",
                ], "row": row,
            },
        ])

    rows.sort(key=lambda row: (row["start_ms"], row["event_ref"]))
    row_event_refs = [row["event_ref"] for row in rows]
    observable_rows = [row for row in rows if row["row_kind"] == "switch_chain"]
    observable_event_refs = [row["event_ref"] for row in observable_rows]
    observable_segment_refs = [
        spec["id"]
        for spec in segment_specs
        if spec["row"]["row_kind"] == "switch_chain"
    ]
    metric_specs = {
        "target_switching.transition_time_ms": ("ms", "transition_time_ms"),
        "target_switching.transition_distance_px": ("px", "transition_distance_px"),
        "target_switching.path_efficiency": ("ratio", "path_efficiency"),
        "target_switching.settle_duration_ms": ("ms", "settle_duration_ms"),
        "target_switching.first_shot_latency_ms": ("ms", "first_shot_latency_ms"),
        "target_switching.first_damage_latency_ms": ("ms", "first_damage_latency_ms"),
        "target_switching.carry_over_overshoot_ratio": ("ratio", "carry_over_overshoot"),
        "target_switching.terminal_correction_ratio": ("ratio", "terminal_correction_observed"),
    }
    metrics: dict[str, dict[str, Any]] = {}
    for metric_key, (unit, field) in metric_specs.items():
        values = [
            (1.0 if row[field] is True else 0.0 if row[field] is False else row[field])
            for row in observable_rows
        ]
        metrics[metric_key] = _metric_record(
            metric_key,
            values,
            unit=unit,
            analysis_ref=analysis_ref,
            event_refs=observable_event_refs,
            segment_refs=observable_segment_refs,
            condition_refs=["condition:target_switching:observable_chain"],
            limitations=["comparison_only_no_static_threshold"],
        )

    row_events = []
    for row in rows:
        attributes = {
            key: value for key, value in row.items()
            if key not in {"event_ref", "row_kind", "start_ms", "end_ms", "limitations"}
            and value is not None
        }
        row_events.append({
            "event_id": row["event_ref"],
            "event_kind": row["row_kind"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "actor_refs": [
                value for value in (
                    row["previous_target_track_ref"], row["next_target_track_ref"],
                ) if value is not None
            ],
            "source_refs": [f"{analysis_ref}:source:target-switching-analysis"],
            "confidence": 1.0,
            "attributes": attributes,
            "limitations": list(row["limitations"]),
        })
    event_bundle = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": sorted(
            [*row_events, *state_events],
            key=lambda event: (event["start_ms"], event["event_kind"], event["event_id"]),
        ),
        "outcome_associations": [],
    }
    from .analysis_evidence import validate_event_bundle_v1, validate_metric_record_v1
    event_bundle = validate_event_bundle_v1(event_bundle)
    for metric in metrics.values():
        validate_metric_record_v1(metric)

    evidence_segments = [
        _segment(
            analysis_ref=analysis_ref,
            window=window,
            segment_id=spec["id"],
            title_key=spec["title"],
            start_ms=spec["row"]["start_ms"],
            end_ms=spec["row"]["end_ms"],
            focus_ms=spec["focus"],
            metric_refs=spec["metrics"],
            event_refs=spec["events"],
            limitations=spec["row"]["limitations"],
        )
        for spec in segment_specs
    ]
    processed_tables = _processed_tables(analysis_ref, rows)
    support_status = (
        "supported"
        if rows and all(row["row_kind"] == "switch_chain" and not row["limitations"] for row in rows)
        else "partial"
    )
    limitations = sorted({
        limitation for row in rows for limitation in row["limitations"]
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "analysis_ref": analysis_ref,
        "analysis_type": "target_switching",
        "support_status": support_status,
        "processed_rows": rows,
        "processed_event_tables": processed_tables,
        "metrics": metrics,
        "evidence_segments": evidence_segments,
        "comparison": payload.get("comparison"),
        "limitations": limitations,
        "evidence_extension": {
            "event_bundle": event_bundle,
            "metric_records": list(metrics.values()),
            "evidence_segments": evidence_segments,
            "processed_event_tables": processed_tables,
                "required_outcome_associations": [
                    required_associations[key] for key in sorted(required_associations)
                ],
                "required_signal_bundle": source_signal_bundle,
                "required_sample_sets": source_sample_sets,
                "required_canonical_time_window": dict(window),
            },
    }


def extend_analysis_evidence_with_target_switching_v1(
    artifact: Mapping[str, Any], analysis_result: Mapping[str, Any],
) -> dict[str, Any]:
    from .analysis_evidence import (
        validate_analysis_evidence_artifact,
        validate_event_bundle,
        validate_metric_record_v1,
    )

    projected = validate_analysis_evidence_artifact(artifact)
    if (
        analysis_result.get("schema_version") != SCHEMA_VERSION
        or analysis_result.get("analysis_version") != ANALYSIS_VERSION
        or analysis_result.get("analysis_ref") != projected["analysis_ref"]
    ):
        raise TargetSwitchingAnalysisError("target switching result is incompatible")
    extension = analysis_result.get("evidence_extension")
    if not isinstance(extension, Mapping):
        raise TargetSwitchingAnalysisError("target switching evidence extension is missing")
    event_bundle = validate_event_bundle(extension.get("event_bundle"))
    metrics = [validate_metric_record_v1(value) for value in extension.get("metric_records") or []]
    segments = list(extension.get("evidence_segments") or [])
    required_associations = extension.get("required_outcome_associations")
    if extension.get("required_canonical_time_window") != projected["canonical_time_window"]:
        raise TargetSwitchingAnalysisError(
            "target switching canonical window does not match the evidence artifact"
        )
    if (
        isinstance(required_associations, (str, bytes))
        or not isinstance(required_associations, Sequence)
    ):
        raise TargetSwitchingAnalysisError("required outcome associations are missing")
    available_associations = {
        association["association_id"]: association
        for bundle in projected["event_bundles"]
        for association in bundle["outcome_associations"]
        if association["availability"] == "available"
        and association["association_kind"] in {"directly_observed", "validated_aligned"}
    }
    for required in required_associations:
        if not isinstance(required, Mapping):
            raise TargetSwitchingAnalysisError("required outcome association is invalid")
        association_id = _ref(required.get("association_id"), "required outcome association id")
        if available_associations.get(association_id) != required:
            raise TargetSwitchingAnalysisError(
                "target switching outcome association does not match the evidence artifact"
            )
    required_signal_bundle = extension.get("required_signal_bundle")
    required_sample_sets = extension.get("required_sample_sets")
    if not isinstance(required_signal_bundle, Mapping):
        raise TargetSwitchingAnalysisError("required signal bundle is missing")
    if not any(bundle == required_signal_bundle for bundle in projected["signal_bundles"]):
        raise TargetSwitchingAnalysisError(
            "target switching signal bundle does not match the evidence artifact"
        )
    if (
        isinstance(required_sample_sets, (str, bytes))
        or not isinstance(required_sample_sets, Sequence)
    ):
        raise TargetSwitchingAnalysisError("required sample sets are missing")
    available_sample_sets = {
        sample_set["sample_set_id"]: sample_set
        for sample_set in projected["sample_sets"]
    }
    for required in required_sample_sets:
        if not isinstance(required, Mapping):
            raise TargetSwitchingAnalysisError("required sample set is invalid")
        sample_ref = _ref(required.get("sample_set_id"), "required sample_set_id")
        if available_sample_sets.get(sample_ref) != required:
            raise TargetSwitchingAnalysisError(
                "target switching sample set does not match the evidence artifact"
            )
    existing_metric_keys = {metric["metric_key"] for metric in projected["metric_records"]}
    if existing_metric_keys.intersection(metric["metric_key"] for metric in metrics):
        raise TargetSwitchingAnalysisError("target switching metric already exists")
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
    "INPUT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "TargetSwitchingAnalysisError",
    "analyze_target_switching_v1",
    "build_switching_chains_from_visual_outcomes_v1",
    "extend_analysis_evidence_with_target_switching_v1",
]
