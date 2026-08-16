"""Generic aim-family analysis layers: dynamic clicking, switching, tracking.

All three reuse the family-agnostic machinery from the static-clicking P0:
color-hypothesis detection, velocity-adaptive birth/death tracks and the
geometric click/kill association. Each layer adds only what its family
actually measures — target motion for dynamic clicking, kill-bounded
episodes for switching, a crosshair-to-target error series for tracking.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .generic_static_clicking_analysis import (
    DYNAMIC_HIT_LOOKBACK_MS,
    associate_generic_static_clicks_v1,
)

GENERIC_DYNAMIC_CLICKING_ANALYSIS_VERSION = "dynamic_clicking.generic_visual.v1"
GENERIC_SWITCHING_ANALYSIS_VERSION = "switching.generic_visual.v1"
GENERIC_TRACKING_ANALYSIS_VERSION = "tracking.generic_visual.v1"

TRACKING_SAMPLE_MS = 50.0
TRACKING_MAX_INTERP_GAP_MS = 120.0
TRACKING_GATE_MIN_COVERAGE = 0.5
# Circle-switch targets are small and spawn fast: the tracker registers
# their death noticeably after the Stats kill timestamp.
SWITCHING_KILL_PAIR_FORWARD_MS = 400.0

GENERIC_DYNAMIC_ASSOCIATION_SCHEMA = "generic_dynamic_association.v1"
GENERIC_SWITCHING_ASSOCIATION_SCHEMA = "generic_switching_association.v1"
GENERIC_TRACKING_ASSOCIATION_SCHEMA = "generic_tracking_association.v1"

_FAMILY_VERSIONS = {
    "dynamic_clicking": GENERIC_DYNAMIC_CLICKING_ANALYSIS_VERSION,
    "switching": GENERIC_SWITCHING_ANALYSIS_VERSION,
    "continuous_tracking": GENERIC_TRACKING_ANALYSIS_VERSION,
}


def generic_family_analysis_version(aim_family: str) -> str | None:
    return _FAMILY_VERSIONS.get(aim_family)


def _track_position_at(
    track: Mapping[str, object], time_ms: int,
) -> tuple[float, float] | None:
    path = track.get("path") or []
    best: Mapping[str, object] | None = None
    best_gap = TRACKING_MAX_INTERP_GAP_MS
    for point in path:
        gap = abs(float(point["t"]) - time_ms)
        if gap <= best_gap:
            best = point
            best_gap = gap
    if best is None:
        return None
    return float(best["x"]), float(best["y"])


def _track_speed_deg_per_s(
    track: Mapping[str, object],
    time_ms: int,
    *,
    deg_per_px: float | None,
) -> float | None:
    path = track.get("path") or []
    before = [
        point for point in path if float(point["t"]) <= time_ms
    ][-2:]
    if len(before) < 2 or deg_per_px is None:
        return None
    dt = float(before[1]["t"]) - float(before[0]["t"])
    if dt <= 0:
        return None
    distance = (
        (float(before[1]["x"]) - float(before[0]["x"])) ** 2
        + (float(before[1]["y"]) - float(before[0]["y"])) ** 2
    ) ** 0.5
    return distance / dt * 1000.0 * deg_per_px


def associate_generic_dynamic_clicks_v1(
    *,
    analysis_ref: str,
    generic_visual_result: Mapping[str, object],
    click_times_ms: Sequence[int],
    kill_records: Sequence[Mapping[str, object]],
    viewport_size: Sequence[int],
    deg_per_px: float | None,
) -> dict:
    """Click/kill association plus target-motion facts for moving targets."""
    association = associate_generic_static_clicks_v1(
        analysis_ref=analysis_ref,
        generic_visual_result=generic_visual_result,
        click_times_ms=click_times_ms,
        kill_records=kill_records,
        viewport_size=viewport_size,
        deg_per_px=deg_per_px,
        hit_lookback_ms=DYNAMIC_HIT_LOOKBACK_MS,
        allow_degraded_hit_position=False,
    )
    hit_speeds: list[float] = []
    for outcome in association["click_outcomes"]:
        if outcome["outcome"] != "hit":
            continue
        engaged = _engaged_tracks_at(
            generic_visual_result["tracks"], outcome["click_time_ms"],
        )
        if not engaged:
            continue
        nearest = min(
            engaged,
            key=lambda track: (
                (track["x"] - viewport_size[0] / 2.0) ** 2
                + (track["y"] - viewport_size[1] / 2.0) ** 2
            ),
        )
        speed = _track_speed_deg_per_s(
            nearest, outcome["click_time_ms"], deg_per_px=deg_per_px,
        )
        if speed is not None:
            outcome["target_speed_deg_per_s"] = speed
            hit_speeds.append(speed)
    association["schema_version"] = GENERIC_DYNAMIC_ASSOCIATION_SCHEMA
    association["target_speed_summary"] = {
        "hit_count_with_speed": len(hit_speeds),
    }
    if hit_speeds:
        ordered = sorted(hit_speeds)
        association["target_speed_summary"]["median_deg_per_s"] = ordered[
            len(ordered) // 2
        ]
    return association


def _engaged_tracks_at(
    tracks: Sequence[Mapping[str, object]], time_ms: int,
) -> list[Mapping[str, object]]:
    return [
        track for track in tracks
        if track["birth_ms"] <= time_ms
        and track["death_ms"] >= time_ms - DYNAMIC_HIT_LOOKBACK_MS
    ]


def associate_generic_switching_v1(
    *,
    analysis_ref: str,
    generic_visual_result: Mapping[str, object],
    click_times_ms: Sequence[int],
    kill_records: Sequence[Mapping[str, object]],
    viewport_size: Sequence[int],
    deg_per_px: float | None,
) -> dict:
    """Kill-bounded switch episodes: transition time and first-shot latency.

    Under hold-fire (kills vastly outnumber button rising edges — circle
    switches are played with the button held) per-click outcomes are
    meaningless, so they are dropped with a limitation and the transition is
    measured kill → next kill. Small fast-spawning switch targets also die
    on the tracker noticeably after the Stats kill, so the pairing window
    stretches forward.
    """
    kills_sorted = sorted(
        (int(kill["canonical_time_ms"]), int(kill["kill_index"]))
        for kill in kill_records
    )
    held_fire = 4 * len(click_times_ms) < len(kills_sorted)
    association = associate_generic_static_clicks_v1(
        analysis_ref=analysis_ref,
        generic_visual_result=generic_visual_result,
        click_times_ms=(() if held_fire else click_times_ms),
        kill_records=kill_records,
        viewport_size=viewport_size,
        deg_per_px=deg_per_px,
        kill_pair_forward_ms=SWITCHING_KILL_PAIR_FORWARD_MS,
    )
    if held_fire:
        association["click_outcomes"] = []
        association["limitations"] = list(dict.fromkeys([
            *association["limitations"],
            "held_fire_click_outcomes_unavailable",
        ]))
    tracks = generic_visual_result["tracks"]
    clicks = sorted(click_times_ms)
    episodes: list[dict] = []
    for index, (kill_ms, kill_index) in enumerate(kills_sorted):
        next_kill_ms = (
            kills_sorted[index + 1][0] if index + 1 < len(kills_sorted) else None
        )
        if held_fire:
            if next_kill_ms is None:
                continue
            transition_end = next_kill_ms
        else:
            first_click = next(
                (click for click in clicks if click > kill_ms), None,
            )
            if first_click is None:
                continue
            transition_end = first_click
        next_target = None
        for track in tracks:
            if track["birth_ms"] > kill_ms and (
                next_kill_ms is None or track["birth_ms"] < next_kill_ms
            ):
                if next_target is None or (
                    track["birth_ms"] < next_target["birth_ms"]
                ):
                    next_target = track
        episodes.append({
            "event_id": f"{analysis_ref}:generic-switch-episode:{index + 1}",
            "from_kill_ms": kill_ms,
            "kill_index": kill_index,
            "first_click_ms": transition_end,
            "transition_ms": transition_end - kill_ms,
            "next_target_track_ref": (
                next_target["track_ref"] if next_target else None
            ),
        })
    association["schema_version"] = GENERIC_SWITCHING_ASSOCIATION_SCHEMA
    association["switch_episodes"] = episodes
    association["held_fire"] = held_fire
    return association


def associate_generic_tracking_v1(
    *,
    analysis_ref: str,
    generic_visual_result: Mapping[str, object],
    canonical_time_window: Mapping[str, object],
    viewport_size: Sequence[int],
    deg_per_px: float | None,
) -> dict:
    """Crosshair-to-aimed-target error series for tracking scenarios.

    The aimed target is whichever track is closest to the crosshair at each
    sample time — tracking scenarios keep one visible target, so proximity
    to center is identity.
    """
    window_start = int(canonical_time_window["start_ms"])
    window_end = int(canonical_time_window["end_ms"])
    width, height = int(viewport_size[0]), int(viewport_size[1])
    crosshair_x = width / 2.0
    crosshair_y = height / 2.0
    tracks = [
        track for track in generic_visual_result["tracks"]
        if track.get("real_sample_count", track["sample_count"]) >= 1
    ]

    samples: list[dict] = []
    errors_deg: list[float] = []
    in_target_count = 0
    time = window_start
    while time < window_end:
        candidates = [
            (track, _track_position_at(track, time)) for track in tracks
        ]
        candidates = [
            (track, position) for track, position in candidates
            if position is not None
        ]
        if candidates:
            track, (target_x, target_y) = min(
                candidates,
                key=lambda item: (
                    (item[1][0] - crosshair_x) ** 2
                    + (item[1][1] - crosshair_y) ** 2
                ),
            )
            error_px = (
                (target_x - crosshair_x) ** 2
                + (target_y - crosshair_y) ** 2
            ) ** 0.5
            error_deg = (
                error_px * deg_per_px if deg_per_px is not None else None
            )
            inside = (
                abs(target_x - crosshair_x)
                <= track["half_width_px"] + 10.0
                and abs(target_y - crosshair_y)
                <= track["half_height_px"] + 10.0
            )
            samples.append({
                "t": time,
                "target_track_ref": track["track_ref"],
                "error_px": error_px,
                "in_target": inside,
            })
            if error_deg is not None:
                errors_deg.append(error_deg)
            if inside:
                in_target_count += 1
        time += TRACKING_SAMPLE_MS

    coverage = (
        len(samples) / max(1, int((window_end - window_start) / TRACKING_SAMPLE_MS))
    )
    # Loss segments: sample gaps larger than twice the sampling interval.
    loss_count = 0
    previous_t: int | None = None
    for sample in samples:
        if previous_t is not None and sample["t"] - previous_t > 2 * TRACKING_SAMPLE_MS:
            loss_count += 1
        previous_t = sample["t"]

    ordered = sorted(errors_deg)
    error_median = ordered[len(ordered) // 2] if ordered else None
    error_p90 = (
        ordered[int(len(ordered) * 0.9)] if ordered else None
    )
    in_target_ratio = (
        in_target_count / len(samples) if samples else None
    )
    gate_reasons: list[str] = []
    if coverage < TRACKING_GATE_MIN_COVERAGE:
        gate_reasons.append("tracking_coverage_below_threshold")
    return {
        "schema_version": GENERIC_TRACKING_ASSOCIATION_SCHEMA,
        "analysis_ref": analysis_ref,
        "sample_count": len(samples),
        "coverage": coverage,
        "error_median_deg": error_median,
        "error_p90_deg": error_p90,
        "in_target_ratio": in_target_ratio,
        "loss_count": loss_count,
        "deg_per_px": deg_per_px,
        "gate": {
            "passed": not gate_reasons,
            "reasons": gate_reasons,
            "frame_coverage": float(generic_visual_result["frame_coverage"]),
        },
        "limitations": (
            [] if deg_per_px is not None else ["angular_calibration_unavailable"]
        ),
    }


def build_generic_family_metric_records_v1(
    association: Mapping[str, object],
    *,
    aim_family: str,
    source_ref: str,
) -> list[dict]:
    """Project a family association into registered metric records."""
    from .generic_static_clicking_analysis import (
        build_generic_static_metric_records_v1,
    )

    records = []
    if aim_family == "dynamic_clicking":
        base = build_generic_static_metric_records_v1(
            association, source_ref=source_ref,
        )
        for record in base:
            record["metric_key"] = record["metric_key"].replace(
                "static_clicking.generic.", "dynamic_clicking.generic.",
            )
        records.extend(base)
        summary = association.get("target_speed_summary") or {}
        if summary.get("median_deg_per_s") is not None:
            records.append(_metric_record(
                "dynamic_clicking.generic.target_speed_deg_per_s",
                summary["median_deg_per_s"],
                "degrees_per_second",
                source_ref,
            ))
    elif aim_family == "switching":
        episodes = association.get("switch_episodes") or []
        transitions = sorted(
            episode["transition_ms"] for episode in episodes
        )
        records.append(_metric_record(
            "switching.generic.episode_count",
            float(len(episodes)),
            "count",
            source_ref,
        ))
        if transitions:
            records.append(_distribution_metric_record(
                "switching.generic.transition_time_ms",
                [float(value) for value in transitions],
                "ms",
                source_ref,
            ))
        base = build_generic_static_metric_records_v1(
            association, source_ref=source_ref,
        )
        for record in base:
            record["metric_key"] = record["metric_key"].replace(
                "static_clicking.generic.", "switching.generic.",
            )
        records.extend(base)
    elif aim_family == "continuous_tracking":
        records.append(_metric_record(
            "tracking.generic.coverage",
            association["coverage"],
            "ratio",
            source_ref,
        ))
        for key, unit in (
            ("error_median_deg", "degrees"),
            ("error_p90_deg", "degrees"),
            ("in_target_ratio", "ratio"),
        ):
            value = association.get(key)
            records.append(_metric_record(
                f"tracking.generic.{key}",
                value,
                unit,
                source_ref,
                availability="unavailable" if value is None else "available",
            ))
        records.append(_metric_record(
            "tracking.generic.loss_count",
            float(association["loss_count"]),
            "count",
            source_ref,
        ))
    else:
        raise ValueError(f"unsupported generic aim family: {aim_family}")
    return records


def _metric_record(
    key: str,
    value: float | None,
    unit: str,
    source_ref: str,
    *,
    availability: str | None = None,
) -> dict:
    available = (
        availability
        or ("available" if value is not None else "unavailable")
    )
    return {
        "schema_version": "metric_record.v1",
        "metric_key": key,
        "metric_version": "generic_aim_families.v1",
        "value": value,
        "unit": unit,
        "availability": available,
        "classification": "deterministic",
        "provenance": {"kind": "measured", "source_refs": [source_ref]},
        "population": {
            "sample_count": 1, "valid_count": 1 if available == "available" else 0,
            "excluded_count": 0,
        },
        "distribution": None,
        "condition_refs": [],
        "event_refs": [],
        "evidence_segment_refs": [],
        "coverage": None,
        "confidence": None,
        "limitations": [],
    }


def _distribution_metric_record(
    key: str,
    values: list[float],
    unit: str,
    source_ref: str,
) -> dict:
    record = _metric_record(key, None, unit, source_ref)
    ordered = sorted(values)
    record["value"] = ordered[len(ordered) // 2]
    record["availability"] = "available"
    record["population"] = {
        "sample_count": len(values),
        "valid_count": len(values),
        "excluded_count": 0,
    }
    record["distribution"] = {
        "min": ordered[0],
        "p10": ordered[int(len(ordered) * 0.1)],
        "p25": ordered[int(len(ordered) * 0.25)],
        "median": ordered[len(ordered) // 2],
        "p75": ordered[int(len(ordered) * 0.75)],
        "p90": ordered[int(len(ordered) * 0.9)],
        "max": ordered[-1],
        "histogram_bins": [],
    }
    return record


def extend_analysis_evidence_with_generic_family_v1(
    artifact: Mapping[str, Any],
    generic_visual_result: Mapping[str, object],
    association: Mapping[str, object],
    *,
    aim_family: str,
    video_source_ref: str,
) -> dict:
    """Append generic family events and metrics to the evidence artifact."""
    from .analysis_evidence import validate_analysis_evidence_artifact_v1
    from .generic_static_clicking_analysis import (
        extend_analysis_evidence_with_generic_static_clicking_v1,
    )

    if aim_family == "continuous_tracking":
        projected = copy.deepcopy(dict(artifact))
        projected["metric_records"].extend(
            build_generic_family_metric_records_v1(
                association, aim_family=aim_family, source_ref=video_source_ref,
            ),
        )
        return validate_analysis_evidence_artifact_v1(projected)
    projected = extend_analysis_evidence_with_generic_static_clicking_v1(
        artifact,
        generic_visual_result,
        association,
        video_source_ref=video_source_ref,
    )
    # The static extension appends its own metric records; the family layer
    # replaces them with its prefix instead of duplicating every value.
    projected["metric_records"] = [
        record
        for record in projected["metric_records"]
        if not record["metric_key"].startswith("static_clicking.generic.")
    ] + build_generic_family_metric_records_v1(
        association, aim_family=aim_family, source_ref=video_source_ref,
    )
    if aim_family == "switching":
        projected["event_bundles"][-1]["events"].extend(
            _switch_episode_events(
                association.get("switch_episodes") or [],
                video_source_ref,
            ),
        )
        projected["event_bundles"][-1]["events"].sort(
            key=lambda event: (event["start_ms"], event["event_id"]),
        )
    return validate_analysis_evidence_artifact_v1(projected)


def _switch_episode_events(
    episodes: Sequence[Mapping[str, object]],
    video_source_ref: str,
) -> list[dict]:
    events: list[dict] = []
    for episode in episodes:
        attributes: dict[str, Any] = {
            "from_kill_ms": int(episode["from_kill_ms"]),
            "kill_index": int(episode["kill_index"]),
            "first_click_ms": int(episode["first_click_ms"]),
            "transition_ms": int(episode["transition_ms"]),
        }
        if episode.get("next_target_track_ref"):
            attributes["target_track_ref"] = episode["next_target_track_ref"]
        events.append({
            "event_id": episode["event_id"],
            "event_kind": "generic_switch_episode",
            "start_ms": int(episode["from_kill_ms"]),
            "end_ms": int(episode["first_click_ms"]),
            "actor_refs": [],
            "source_refs": [video_source_ref],
            "confidence": 0.9,
            "attributes": attributes,
            "limitations": [],
        })
    return events


__all__ = [
    "GENERIC_DYNAMIC_CLICKING_ANALYSIS_VERSION",
    "GENERIC_SWITCHING_ANALYSIS_VERSION",
    "GENERIC_TRACKING_ANALYSIS_VERSION",
    "associate_generic_dynamic_clicks_v1",
    "associate_generic_switching_v1",
    "associate_generic_tracking_v1",
    "build_generic_family_metric_records_v1",
    "extend_analysis_evidence_with_generic_family_v1",
    "generic_family_analysis_version",
]
