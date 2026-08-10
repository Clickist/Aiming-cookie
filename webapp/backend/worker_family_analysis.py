from __future__ import annotations

import math
import re
from collections.abc import Mapping

from .worker_source_validation import (
    SourceSnapshotChangedError,
    _read_frozen_source_bytes,
)


def _parse_frozen_stats_for_visual(snapshot: Mapping[str, object]):
    from kovaak_tracker.csv_parser import parse_stats_bytes

    sources = snapshot.get("sources")
    stats_source = sources.get("stats") if isinstance(sources, Mapping) else None
    if not isinstance(stats_source, Mapping):
        raise ValueError("dynamic analysis requires a frozen stats source")
    stats_bytes = _read_frozen_source_bytes("stats", stats_source)
    return parse_stats_bytes(
        stats_bytes,
        file_name=str(stats_source.get("basename") or "stats.csv"),
    )


def _raw_left_button_rising_edges(
    trace_points: list[dict],
    *,
    analysis_ref: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    click_events = []
    previous_pressed: bool | None = None
    for point in trace_points:
        time_ms = int(point["timestamp_ms"])
        if not start_ms <= time_ms < end_ms:
            continue
        pressed = bool(point["buttons"] & 1)
        if previous_pressed is False and pressed:
            click_events.append({
                "event_ref": f"{analysis_ref}:event:raw-shot:{len(click_events) + 1}",
                "time_ms": time_ms,
            })
        previous_pressed = pressed
    return click_events


def _target_switching_episode_tracks(
    visual_result: Mapping[str, object],
    episode_result: Mapping[str, object],
    *,
    analysis_ref: str,
) -> list[dict]:
    """Expose only child-projected local target episodes to the composer."""
    if (
        episode_result.get("schema_version") != "visual_target_episode_artifact.v1"
        or episode_result.get("status") not in {"available", "partial"}
    ):
        raise ValueError("target switching episodes are unavailable")
    summaries = visual_result.get("track_summaries")
    local_samples = visual_result.get("local_samples")
    if not isinstance(summaries, list) or not isinstance(local_samples, Mapping):
        raise ValueError("target switching episode samples are unavailable")
    tracks = []
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("target switching episode summary is invalid")
        track_ref = summary.get("track_ref")
        match = re.fullmatch(
            re.escape(f"{analysis_ref}:target-track:") + r"([1-9][0-9]*)",
            track_ref,
        ) if isinstance(track_ref, str) else None
        samples = local_samples.get(f"target.{match.group(1)}.position") if match else None
        if (
            match is None
            or not isinstance(samples, list)
            or not samples
            or summary.get("observation_source") != "event_local_target_episode"
        ):
            raise ValueError("target switching episode is invalid")
        normalized_samples = []
        for sample in samples:
            if not isinstance(sample, Mapping):
                raise ValueError("target switching episode sample is invalid")
            canonical_time = sample.get("canonical_time_ms")
            if (
                isinstance(canonical_time, bool)
                or not isinstance(canonical_time, int)
            ):
                raise ValueError("target switching episode sample time is invalid")
            normalized_samples.append({
                "canonical_time_ms": canonical_time,
                "x": sample.get("x"),
                "y": sample.get("y"),
                "visible_radius": sample.get("visible_radius"),
                "confidence": sample.get("confidence"),
            })
        tracks.append({
            "track_ref": f"{analysis_ref}:target-track:{match.group(1)}",
            "episode_observable": True,
            "samples": normalized_samples,
        })
    return tracks


def _target_switching_stats_kills(
    *,
    analysis_ref: str,
    snapshot: Mapping[str, object],
    parsed_stats: object,
) -> list[dict]:
    """Project parsed Stats kills without any target association."""
    window = snapshot.get("canonical_time_window")
    sources = snapshot.get("sources")
    stats_source = sources.get("stats") if isinstance(sources, Mapping) else None
    if not isinstance(window, Mapping) or not isinstance(stats_source, Mapping):
        raise ValueError("target switching Stats source context is unavailable")
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    source_ref = stats_source.get("artifact_ref")
    if (
        isinstance(start_ms, bool)
        or not isinstance(start_ms, int)
        or isinstance(end_ms, bool)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
        or not isinstance(source_ref, str)
        or not source_ref
    ):
        raise ValueError("target switching Stats source context is invalid")
    kills = getattr(parsed_stats, "kills", None)
    if kills is None or not hasattr(kills, "iterrows"):
        raise ValueError("target switching Stats kill rows are unavailable")
    projected = []
    for row_index, (_, row) in enumerate(kills.iterrows(), 1):
        try:
            time_ms = start_ms + int(round(float(row["time_s"]) * 1000))
            kill_index = int(row["Kill #"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if start_ms <= time_ms < end_ms and kill_index > 0:
            projected.append({
                "event_ref": f"{analysis_ref}:event:stats-kill:{row_index}",
                "time_ms": time_ms,
                "kill_index": kill_index,
                "source_ref": source_ref,
            })
    return projected


def _build_validated_outcome_association(
    job: Mapping[str, object],
    parsed_stats: object,
    visual_result: Mapping[str, object],
) -> dict | None:
    from kovaak_tracker.outcome_association import (
        associate_one_shot_kills_v1,
        load_outcome_association_rule_registry_v1,
    )
    from . import worker

    registry = load_outcome_association_rule_registry_v1()
    if not registry["entries"]:
        return None
    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("outcome association input snapshot is unavailable")
    window = snapshot.get("canonical_time_window")
    sources = snapshot.get("sources")
    trace = snapshot.get("trace")
    resolution = snapshot.get("scenario_resolution")
    if (
        not isinstance(window, Mapping)
        or not isinstance(sources, Mapping)
        or not isinstance(trace, Mapping)
        or not isinstance(resolution, Mapping)
    ):
        raise ValueError("outcome association source context is unavailable")
    stats_source = sources.get("stats")
    video_source = sources.get("video")
    if not isinstance(stats_source, Mapping) or not isinstance(video_source, Mapping):
        raise ValueError("outcome association frozen sources are unavailable")
    analysis_ref = f"analysis:{job['id']}"
    if (
        visual_result.get("analysis_ref") != analysis_ref
        or visual_result.get("canonical_time_window") != window
    ):
        raise ValueError("outcome association visual result is bound to another analysis")
    quality = visual_result.get("quality")
    if not isinstance(quality, Mapping) or quality.get("status") != "accepted":
        return None

    from .kovaak_run_store import decode_mouse_snapshot_bytes

    trace_points = decode_mouse_snapshot_bytes(
        worker._read_frozen_source_bytes("raw_input", trace),
    )
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        isinstance(start_ms, bool)
        or not isinstance(start_ms, int)
        or isinstance(end_ms, bool)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        raise ValueError("outcome association canonical window is unavailable")
    click_events = _raw_left_button_rising_edges(
        trace_points,
        analysis_ref=analysis_ref,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    kills = getattr(parsed_stats, "kills", None)
    if kills is None or not hasattr(kills, "iterrows"):
        raise ValueError("outcome association Stats kill rows are unavailable")
    stats_kills = []
    for row_index, (_, row) in enumerate(kills.iterrows(), 1):
        try:
            relative_ms = int(round(float(row["time_s"]) * 1000))
            kill = {
                "event_ref": f"{analysis_ref}:event:stats-kill:{row_index}",
                "time_ms": start_ms + relative_ms,
                "kill_index": int(row["Kill #"]),
                "shots": int(row["Shots"]),
                "hits": int(row["Hits"]),
                "overshots": int(row["OverShots"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if start_ms <= kill["time_ms"] < end_ms:
            stats_kills.append(kill)

    local_samples = visual_result.get("local_samples")
    selector = visual_result.get("visual_runtime_selector")
    if not isinstance(local_samples, Mapping) or not isinstance(selector, Mapping):
        raise ValueError("outcome association visual samples are unavailable")
    summaries = {
        summary.get("track_ref"): summary
        for summary in visual_result.get("track_summaries") or []
        if isinstance(summary, Mapping) and isinstance(summary.get("track_ref"), str)
    }
    target_tracks = []
    for sample_key, samples in sorted(local_samples.items()):
        match = re.fullmatch(r"target\.([A-Za-z0-9_-]+)\.position", str(sample_key))
        if match is None or not isinstance(samples, list):
            continue
        track_ref = f"{analysis_ref}:target-track:{match.group(1)}"
        summary = summaries.get(track_ref) or {}
        identity_status = (
            "stable"
            if summary.get("identity_source") == "detector_ref"
            and not list(summary.get("limitations") or [])
            else "unavailable"
        )
        target_tracks.append({
            "track_ref": track_ref,
            "identity_status": identity_status,
            "samples": [
                {
                    "canonical_time_ms": sample["canonical_time_ms"],
                    "x": sample["x"],
                    "y": sample["y"],
                    "radius": sample["visible_radius"],
                    "confidence": sample.get("confidence", 0.0),
                }
                for sample in samples
                if isinstance(sample, Mapping)
                and {
                    "canonical_time_ms", "x", "y", "visible_radius",
                } <= set(sample)
            ],
        })
    result = associate_one_shot_kills_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=window,
        scenario_profile_ref=resolution.get("scenario_profile_ref"),
        visual_quality_profile_ref=visual_result.get("visual_quality_profile_ref"),
        raw_input_source_ref=trace.get("artifact_ref"),
        stats_source_ref=stats_source.get("artifact_ref"),
        stats_parser_version=stats_source.get("parser_version"),
        visual_source_ref=video_source.get("artifact_ref"),
        click_events=click_events,
        stats_kills=stats_kills,
        viewport_size=selector.get("resolution"),
        target_tracks=target_tracks,
        rule_registry=registry,
    )
    return result["event_bundle"] if result["status"] == "available" else None


def run_dynamic_clicking_analysis(
    job: dict,
    visual_result: Mapping[str, object],
    outcome_event_bundle: Mapping[str, object] | None = None,
) -> dict:
    """Combine frozen Raw click anchors with local numerical visual signals."""
    from .kovaak_run_store import decode_mouse_snapshot_bytes
    from kovaak_tracker.dynamic_clicking_analysis import analyze_dynamic_clicking_v1
    from . import worker

    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("dynamic analysis input snapshot is unavailable")
    analysis_ref = f"analysis:{job['id']}"
    window = snapshot.get("canonical_time_window")
    resolution = snapshot.get("scenario_resolution")
    if (
        visual_result.get("analysis_ref") != analysis_ref
        or visual_result.get("canonical_time_window") != window
        or not isinstance(window, Mapping)
        or not isinstance(resolution, Mapping)
    ):
        raise ValueError("dynamic visual result is bound to another analysis")
    trace = snapshot.get("trace")
    trace_bytes = worker._read_frozen_source_bytes("raw_input", trace)
    trace_points = decode_mouse_snapshot_bytes(trace_bytes)
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        isinstance(start_ms, bool) or not isinstance(start_ms, int)
        or isinstance(end_ms, bool) or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        raise ValueError("dynamic canonical window is unavailable")
    click_events = _raw_left_button_rising_edges(
        trace_points,
        analysis_ref=analysis_ref,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    local_samples = visual_result.get("local_samples")
    if not isinstance(local_samples, Mapping):
        raise ValueError("dynamic visual samples are unavailable")
    crosshair_samples = local_samples.get("crosshair.position")
    track_summaries = {
        summary.get("track_ref"): summary
        for summary in visual_result.get("track_summaries") or []
        if isinstance(summary, Mapping) and isinstance(summary.get("track_ref"), str)
    }
    target_tracks = []
    for sample_key, samples in sorted(local_samples.items()):
        match = re.fullmatch(r"target\.([A-Za-z0-9_-]+)\.position", str(sample_key))
        if match is None or not isinstance(samples, list):
            continue
        track_ref = f"{analysis_ref}:target-track:{match.group(1)}"
        summary = track_summaries.get(track_ref) or {}
        target_tracks.append({
            "track_ref": track_ref,
            "samples": [
                {
                    "canonical_time_ms": sample["canonical_time_ms"],
                    "x": sample["x"],
                    "y": sample["y"],
                    "radius": sample["visible_radius"],
                    "confidence": sample.get("confidence", 1.0),
                }
                for sample in samples
                if isinstance(sample, Mapping)
            ],
            "limitations": list(summary.get("limitations") or []),
        })
    signal_bundle = visual_result.get("signal_bundle")
    channels = signal_bundle.get("channels") if isinstance(signal_bundle, Mapping) else []
    available_channel_keys = [
        channel["channel_key"]
        for channel in channels or []
        if isinstance(channel, Mapping) and isinstance(channel.get("channel_key"), str)
    ]
    return analyze_dynamic_clicking_v1({
        "schema_version": "dynamic_clicking_input.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "scenario_resolution": dict(resolution),
        "visual_quality": dict(visual_result.get("quality") or {}),
        "crosshair_samples": crosshair_samples,
        "available_channel_keys": available_channel_keys,
        "target_tracks": target_tracks,
        "click_events": click_events,
        "visual_event_bundle": outcome_event_bundle or visual_result.get("event_bundle"),
        "predictability_evidence": [],
        "comparison": None,
    })


def run_continuous_tracking_analysis(
    job: dict,
    visual_result: Mapping[str, object],
) -> dict:
    """Adapt one reviewed visual target track into the tracking analyzer input."""
    from kovaak_tracker.analysis_evidence import validate_event_bundle_v1
    from kovaak_tracker.tracking_analysis import analyze_continuous_tracking_v1

    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("continuous tracking input snapshot is unavailable")
    analysis_ref = f"analysis:{job['id']}"
    window = snapshot.get("canonical_time_window")
    resolution = snapshot.get("scenario_resolution")
    if (
        visual_result.get("analysis_ref") != analysis_ref
        or visual_result.get("canonical_time_window") != window
        or not isinstance(window, Mapping)
        or not isinstance(resolution, Mapping)
    ):
        raise ValueError("continuous tracking visual result is bound to another analysis")
    local_samples = visual_result.get("local_samples")
    if not isinstance(local_samples, Mapping):
        raise ValueError("continuous tracking visual samples are unavailable")
    crosshair_samples = local_samples.get("crosshair.position")
    target_tracks = [
        (match.group(1), samples)
        for sample_key, samples in local_samples.items()
        if (match := re.fullmatch(r"target\.([A-Za-z0-9_-]+)\.position", str(sample_key)))
        and isinstance(samples, list)
    ]
    if len(target_tracks) != 1:
        raise ValueError("continuous tracking requires one unambiguous target track")
    track_id, target_samples = target_tracks[0]
    track_ref = f"{analysis_ref}:target-track:{track_id}"
    summaries = {
        summary.get("track_ref"): summary
        for summary in visual_result.get("track_summaries") or []
        if isinstance(summary, Mapping) and isinstance(summary.get("track_ref"), str)
    }
    summary = summaries.get(track_ref)
    if not isinstance(summary, Mapping):
        raise ValueError("continuous tracking target track is unvalidated")
    identity_limitations = {
        limitation
        for limitation in [
            *(visual_result.get("limitations") or []),
            *(summary.get("limitations") or []),
        ]
        if limitation in {
            "identity_crossing_ambiguous",
            "reentry_identity_unresolved",
        }
    }
    if identity_limitations:
        raise ValueError("continuous tracking target identity is ambiguous")
    event_bundle = validate_event_bundle_v1(visual_result.get("event_bundle"))
    if event_bundle["analysis_ref"] != analysis_ref:
        raise ValueError("continuous tracking event bundle is bound to another analysis")
    target_change_points = [
        {"event_ref": event["event_id"], "time_ms": event["start_ms"]}
        for event in event_bundle["events"]
        if event["event_kind"] == "target_change_point"
        and event["actor_refs"] == [track_ref]
        and event["end_ms"] == event["start_ms"]
    ]
    channels = (visual_result.get("signal_bundle") or {}).get("channels")
    available_channel_keys = [
        channel["channel_key"]
        for channel in channels or []
        if isinstance(channel, Mapping) and isinstance(channel.get("channel_key"), str)
    ]
    explicit_alignment = visual_result.get("alignment_latency_ms")
    alignment_latency_ms = (
        float(explicit_alignment)
        if isinstance(explicit_alignment, (int, float))
        and not isinstance(explicit_alignment, bool)
        and math.isfinite(float(explicit_alignment))
        else None
    )
    return analyze_continuous_tracking_v1({
        "schema_version": "continuous_tracking_input.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "scenario_resolution": dict(resolution),
        "visual_quality": dict(visual_result.get("quality") or {}),
        "player_motion_status": "unavailable_fixed_viewport_center",
        "target_track": {
            "track_ref": track_ref,
            "samples": [
                {
                    "canonical_time_ms": sample["canonical_time_ms"],
                    "x": sample["x"],
                    "y": sample["y"],
                    "radius": sample.get("visible_radius"),
                    "confidence": sample.get("confidence", 1.0),
                    "measurement_complete": True,
                }
                for sample in target_samples
                if isinstance(sample, Mapping)
            ],
            "limitations": list(summary.get("limitations") or []),
        },
        "crosshair_samples": [
            {
                **dict(sample),
                "measurement_complete": True,
            }
            for sample in crosshair_samples or []
            if isinstance(sample, Mapping)
        ],
        "available_channel_keys": available_channel_keys,
        "target_change_points": target_change_points,
        "predictability_evidence": [],
        "alignment_latency_ms": alignment_latency_ms,
        "comparison": None,
    })


def run_target_switching_analysis(
    job: dict,
    visual_result: Mapping[str, object],
    episode_result: Mapping[str, object],
    parsed_stats: object,
) -> dict:
    """Adapt reviewed local episodes into a target-switching analysis."""
    from kovaak_tracker.target_switching_analysis import (
        analyze_target_switching_v1,
        build_switching_chains_from_stats_kills_v1,
    )

    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("target switching input snapshot is unavailable")
    analysis_ref = f"analysis:{job['id']}"
    window = snapshot.get("canonical_time_window")
    resolution = snapshot.get("scenario_resolution")
    if (
        visual_result.get("analysis_ref") != analysis_ref
        or visual_result.get("canonical_time_window") != window
        or not isinstance(window, Mapping)
        or not isinstance(resolution, Mapping)
    ):
        raise ValueError("target switching visual result is bound to another analysis")
    local_samples = visual_result.get("local_samples")
    if not isinstance(local_samples, Mapping):
        raise ValueError("target switching visual samples are unavailable")
    crosshair_samples = local_samples.get("crosshair.position")
    if not isinstance(crosshair_samples, list) or not crosshair_samples:
        raise ValueError("target switching crosshair samples are unavailable")
    target_tracks = _target_switching_episode_tracks(
        visual_result,
        episode_result,
        analysis_ref=analysis_ref,
    )
    stats_kills = _target_switching_stats_kills(
        analysis_ref=analysis_ref,
        snapshot=snapshot,
        parsed_stats=parsed_stats,
    )
    episodes = build_switching_chains_from_stats_kills_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=window,
        crosshair_samples=crosshair_samples,
        target_tracks=target_tracks,
        stats_kills=stats_kills,
    )
    quality = dict(visual_result.get("quality") or {})
    enabled_families = [
        "target_switching" if family == "switching" else family
        for family in quality.get("enabled_metric_families") or []
    ]
    quality["enabled_metric_families"] = sorted(set(enabled_families))
    return analyze_target_switching_v1({
        "schema_version": "target_switching_input.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "scenario_resolution": dict(resolution),
        "visual_quality": quality,
        "target_tracks": target_tracks,
        "crosshair_samples": crosshair_samples,
        "source_signal_bundle": visual_result.get("signal_bundle"),
        "source_sample_sets": visual_result.get("sample_sets"),
        "stats_kills": stats_kills,
        "episodes": episodes,
        "comparison": None,
    })
