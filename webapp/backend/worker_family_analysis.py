from __future__ import annotations

import bisect
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
    telemetry_limitations: list[str] = []
    if isinstance(trace, Mapping):
        trace_points = decode_mouse_snapshot_bytes(
            worker._read_frozen_source_bytes("raw_input", trace),
        )
    else:
        # 遥测-only 运行（无 raw input trace）：从冻结的外部遥测 inputs 旁车
        # 合成等价点击锚输入；不可用时落 unavailable 语义的 ValueError，由
        # worker 分支降级 outcome_only（受控），不让整场分析 crash。
        from .telemetry_input_adapter import telemetry_mouse_trace_points

        trace_points, telemetry_limitations = telemetry_mouse_trace_points(snapshot)
    if trace_points is None:
        raise ValueError(
            "dynamic analysis requires a raw input trace or telemetry inputs",
        )
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
    if telemetry_limitations:
        analysis["limitations"] = list(dict.fromkeys([
            *(analysis.get("limitations") or []), *telemetry_limitations,
        ]))
    return analysis


_MULTI_TARGET_MIN_TRACK_SAMPLES = 200
# 样本数低于该阈值的轨道是路过/半遮挡噪声片段（如 34/65/128 样本轨），
# 多目标场景下不进入分析；单轨道输入不受影响（保持 v1 行为）。


def _tracking_identity_limitations(
    visual_result: Mapping[str, object],
    summary: Mapping[str, object] | None,
) -> set[str]:
    """Identity ambiguity markers that disqualify a track from measurement."""
    return {
        limitation
        for limitation in [
            *(visual_result.get("limitations") or []),
            *((summary or {}).get("limitations") or []),
        ]
        if limitation in {
            "identity_crossing_ambiguous",
            "reentry_identity_unresolved",
        }
    }


def _tracking_change_points_for_track(
    event_bundle: Mapping[str, object],
    track_ref: str,
) -> list[dict]:
    return [
        {"event_ref": event["event_id"], "time_ms": event["start_ms"]}
        for event in event_bundle["events"]
        if event["event_kind"] == "target_change_point"
        and event["actor_refs"] == [track_ref]
        and event["end_ms"] == event["start_ms"]
    ]


def _continuous_tracking_track_payload(
    *,
    analysis_ref: str,
    window: Mapping[str, object],
    resolution: Mapping[str, object],
    quality: Mapping[str, object],
    track_ref: str,
    target_samples: list,
    summary: Mapping[str, object],
    target_change_points: list[dict],
    available_channel_keys: list[str],
    alignment_latency_ms: float | None,
    crosshair_samples: list | None,
) -> dict:
    """Build the unchanged single-track analyzer input for one target track."""
    return {
        "schema_version": "continuous_tracking_input.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "scenario_resolution": dict(resolution),
        "visual_quality": dict(quality or {}),
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
    }


def _apply_fused_alignment_metric(
    job: Mapping[str, object],
    visual_result: Mapping[str, object],
    result: dict,
    *,
    analysis_ref: str,
    window: Mapping[str, object],
) -> None:
    """双输入源融合 alignment_latency（合同 ``.zcode/fusion-spec-1005.md`` §二.2）。

    锚事件 = 权威击杀 N（producer kill 事件）→ 下一受害目标出生（同轨道
    kill 前最近一次出生事件，孰晚）；值 = 锚后 trace 角速度流（cm_per_360
    + DPI 换算）首个（角速度 >= 阈值 且 方向朝目标）桶与锚之差。不需要
    按压沿。

    前提不满足（kill < 2 / trace 缺席或不可解码 / 校准缺 cm_per_360 或
    DPI / 无可判方位段）时不改指标——既有 capture 描述子语义原样保留。
    ``SourceSnapshotChangedError`` 原样上抛（fail-closed 契约）。
    """
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        isinstance(start_ms, bool) or isinstance(end_ms, bool)
        or not isinstance(start_ms, int) or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        return
    snapshot = job.get("input_snapshot")
    trace = snapshot.get("trace") if isinstance(snapshot, Mapping) else None
    if not isinstance(trace, Mapping) or trace.get("availability") != "available":
        return
    calibration = job.get("calibration_snapshot")
    cm_per_360 = (
        calibration.get("cm_per_360", {}).get("value")
        if isinstance(calibration, Mapping)
        and isinstance(calibration.get("cm_per_360"), Mapping)
        else None
    )
    if not isinstance(cm_per_360, (int, float)) or isinstance(cm_per_360, bool):
        return
    try:
        parsed_stats = _parse_frozen_stats_for_visual(snapshot)
        dpi = getattr(parsed_stats, "dpi", None)
    except SourceSnapshotChangedError:
        raise
    except ValueError:
        return
    if not isinstance(dpi, int) or dpi <= 0:
        return
    event_bundle = visual_result.get("event_bundle")
    events = event_bundle.get("events") if isinstance(event_bundle, Mapping) else None
    if not isinstance(events, list):
        return
    kills = sorted(
        (int(event["start_ms"]), str(event["actor_refs"][0]))
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_kind") == "kill"
        and len(event.get("actor_refs") or []) == 1
        and event.get("start_ms") == event.get("end_ms")
        and start_ms <= int(event["start_ms"]) < end_ms
    )
    if len(kills) < 2:
        return
    births_by_track: dict[str, list[int]] = {}
    for event in events:
        if (
            isinstance(event, Mapping)
            and event.get("event_kind") == "target_change_point"
            and len(event.get("actor_refs") or []) == 1
            and start_ms <= int(event["start_ms"]) < end_ms
        ):
            births_by_track.setdefault(str(event["actor_refs"][0]), []).append(
                int(event["start_ms"]),
            )
    for times in births_by_track.values():
        times.sort()

    def victim_birth_ms(track_ref: str, death_ms: int) -> int:
        """该轨道 death 前最近一次出生（无出生事件时回退 leave 锚）。"""
        times = births_by_track.get(track_ref) or []
        index = bisect.bisect_right(times, death_ms)
        return times[index - 1] if index > 0 else death_ms

    from . import worker
    from .kovaak_run_store import decode_mouse_snapshot_bytes
    from kovaak_tracker.input_fusion import (
        MOTION_ONSET_MAX_SEARCH_MS,
        MOTION_ONSET_THRESHOLD_DEG_S,
        MOTION_WINDOW_MS,
        bearing_toward_target,
        build_angular_speed_stream,
        deg_per_count,
        movement_onset_ms,
    )

    try:
        trace_points = decode_mouse_snapshot_bytes(
            worker._read_frozen_source_bytes("raw_input", trace),
        )
    except ValueError:
        return
    if not trace_points:
        return
    stream = build_angular_speed_stream(
        trace_points,
        deg_per_count_value=deg_per_count(float(cm_per_360), float(dpi)),
    )
    local_samples = visual_result.get("local_samples")
    if not isinstance(local_samples, Mapping):
        return
    crosshair_samples = local_samples.get("crosshair.position")
    if not isinstance(crosshair_samples, list) or not crosshair_samples:
        return
    selector = visual_result.get("visual_runtime_selector")
    fov = (
        selector.get("fov")
        if isinstance(selector, Mapping)
        and isinstance(selector.get("fov"), (int, float))
        and not isinstance(selector.get("fov"), bool)
        else 103.0
    )
    from kovaak_tracker.telemetry_signals import VIEWPORT_WIDTH_PX as _FUSION_VIEWPORT_WIDTH_PX

    focal_px = (_FUSION_VIEWPORT_WIDTH_PX / 2.0) / math.tan(math.radians(float(fov)) / 2.0)

    def crosshair_at(anchor_ms: int):
        position = None
        for sample in crosshair_samples:
            if not isinstance(sample, Mapping):
                continue
            time_ms = sample.get("canonical_time_ms")
            if not isinstance(time_ms, int) or isinstance(time_ms, bool):
                continue
            if time_ms <= anchor_ms:
                position = sample
            else:
                break
        if position is None:
            position = next(
                (s for s in crosshair_samples if isinstance(s, Mapping)), None,
            )
        if position is None:
            return None
        return float(position["x"]), float(position["y"])

    latencies: list[float | None] = []
    missing_onset = 0
    for (leave_ms, _previous_ref), (death_ms, victim_ref) in zip(kills, kills[1:]):
        if death_ms <= leave_ms:
            continue
        match = re.fullmatch(
            re.escape(f"{analysis_ref}:target-track:") + r"([A-Za-z0-9_-]+)",
            victim_ref,
        )
        if match is None:
            continue
        anchor_ms = max(leave_ms, victim_birth_ms(victim_ref, death_ms))
        samples = local_samples.get(f"target.{match.group(1)}.position")
        if not isinstance(samples, list):
            continue
        bearing_samples = [
            sample for sample in samples
            if isinstance(sample, Mapping)
            and isinstance(sample.get("canonical_time_ms"), int)
            and not isinstance(sample.get("canonical_time_ms"), bool)
            and anchor_ms <= int(sample["canonical_time_ms"]) <= anchor_ms + 150
        ] or [
            sample for sample in samples
            if isinstance(sample, Mapping)
            and isinstance(sample.get("canonical_time_ms"), int)
            and not isinstance(sample.get("canonical_time_ms"), bool)
            and int(sample["canonical_time_ms"]) >= anchor_ms
        ][:6]
        crosshair = crosshair_at(anchor_ms)
        if not bearing_samples or crosshair is None:
            continue
        bearing = bearing_toward_target(
            crosshair_x=crosshair[0],
            crosshair_y=crosshair[1],
            target_x=float(
                sum(float(s["x"]) for s in bearing_samples) / len(bearing_samples),
            ),
            target_y=float(
                sum(float(s["y"]) for s in bearing_samples) / len(bearing_samples),
            ),
            focal_px=focal_px,
        )
        if bearing is None:
            continue
        onset = movement_onset_ms(stream, anchor_ms=anchor_ms, bearing=bearing)
        if onset is None:
            missing_onset += 1
            latencies.append(None)
        else:
            latencies.append(float(onset - anchor_ms))
    if not latencies:
        return
    from kovaak_tracker.tracking_analysis import build_fused_alignment_metric_v1

    threshold_label = (
        str(int(MOTION_ONSET_THRESHOLD_DEG_S))
        if float(MOTION_ONSET_THRESHOLD_DEG_S).is_integer()
        else f"{MOTION_ONSET_THRESHOLD_DEG_S:g}"
    )
    limitations = [
        "alignment_latency_from_fused_movement_onset",
        "alignment_anchor_authoritative_kill_to_next_birth",
        f"movement_onset_threshold_{threshold_label}_deg_per_s",
        f"movement_onset_bucket_{MOTION_WINDOW_MS}ms",
        f"movement_onset_search_cap_{MOTION_ONSET_MAX_SEARCH_MS}ms",
    ]
    if missing_onset:
        limitations.append(f"movement_onset_missing_{missing_onset}")
    record = build_fused_alignment_metric_v1(
        analysis_ref=analysis_ref,
        latencies=latencies,
        limitations=limitations,
    )
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        return
    metrics["continuous_tracking.alignment_latency_ms"] = record
    extension = result.get("evidence_extension")
    if isinstance(extension, Mapping) and isinstance(
        extension.get("metric_records"), list,
    ):
        existing = [
            record if isinstance(item, Mapping)
            and item.get("metric_key") == "continuous_tracking.alignment_latency_ms"
            else item
            for item in extension["metric_records"]
        ]
        if not any(
            isinstance(item, Mapping)
            and item.get("metric_key") == "continuous_tracking.alignment_latency_ms"
            for item in existing
        ):
            existing.append(record)
        extension["metric_records"] = existing


def run_continuous_tracking_analysis(
    job: dict,
    visual_result: Mapping[str, object],
) -> dict:
    """Adapt reviewed visual target tracks into the tracking analyzer input.

    Single-track inputs keep the v1 contract exactly: the unchanged single-track
    analyzer result is returned as-is.  Multi-track inputs first drop noise
    tracks (fewer than ``_MULTI_TARGET_MIN_TRACK_SAMPLES`` samples: pass-by or
    half-occluded fragments), then analyze each surviving track independently
    with the unchanged single-track analyzer and return the multi-target
    aggregate from ``aggregate_continuous_tracking_multi_target_v1`` (union
    time-in-radius, duration-weighted error, per-track detail under
    ``per_target``).  Tracks with a missing summary or ambiguous identity are
    excluded from the multi-target set; if nothing analyzable remains the
    single-track "unambiguous target" error is raised, and a single surviving
    track is returned in the plain v1 shape.
    """
    from kovaak_tracker.analysis_evidence import validate_event_bundle_v1
    from kovaak_tracker.tracking_analysis import (
        aggregate_continuous_tracking_multi_target_v1,
        analyze_continuous_tracking_v1,
    )

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
    if not target_tracks:
        raise ValueError("continuous tracking requires one unambiguous target track")
    summaries = {
        summary.get("track_ref"): summary
        for summary in visual_result.get("track_summaries") or []
        if isinstance(summary, Mapping) and isinstance(summary.get("track_ref"), str)
    }
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
    quality = visual_result.get("quality") or {}

    if len(target_tracks) > 1:
        # 多目标路径：噪声过滤（样本数阈值见常量注释），逐轨校验后独立分析。
        # 汇总语义（union 时间占比 / 时长加权误差 / 逐轨事实不合并）见
        # aggregate_continuous_tracking_multi_target_v1 的 docstring。
        candidates = [
            (track_id, samples)
            for track_id, samples in sorted(target_tracks, key=lambda item: str(item[0]))
            if len(samples) >= _MULTI_TARGET_MIN_TRACK_SAMPLES
        ]
        analyzable = []
        for track_id, target_samples in candidates:
            track_ref = f"{analysis_ref}:target-track:{track_id}"
            summary = summaries.get(track_ref)
            if not isinstance(summary, Mapping):
                continue  # 未验证轨道不进入多目标分析
            if _tracking_identity_limitations(visual_result, summary):
                continue  # 身份歧义轨道剔除，保留干净轨道
            analyzable.append((track_ref, target_samples, summary))
        if not analyzable:
            raise ValueError("continuous tracking requires one unambiguous target track")
        event_bundle = validate_event_bundle_v1(visual_result.get("event_bundle"))
        if event_bundle["analysis_ref"] != analysis_ref:
            raise ValueError("continuous tracking event bundle is bound to another analysis")
        entries = []
        for track_ref, target_samples, summary in analyzable:
            payload = _continuous_tracking_track_payload(
                analysis_ref=analysis_ref,
                window=window,
                resolution=resolution,
                quality=quality,
                track_ref=track_ref,
                target_samples=target_samples,
                summary=summary,
                target_change_points=_tracking_change_points_for_track(
                    event_bundle, track_ref,
                ),
                available_channel_keys=available_channel_keys,
                alignment_latency_ms=alignment_latency_ms,
                crosshair_samples=crosshair_samples,
            )
            entries.append({
                "track_ref": track_ref,
                "payload": payload,
                "analysis": analyze_continuous_tracking_v1(payload),
            })
        if len(entries) == 1:
            result = entries[0]["analysis"]
        else:
            result = aggregate_continuous_tracking_multi_target_v1(entries)
        _apply_fused_alignment_metric(
            job, visual_result, result, analysis_ref=analysis_ref, window=window,
        )
        return result

    track_id, target_samples = target_tracks[0]
    track_ref = f"{analysis_ref}:target-track:{track_id}"
    summary = summaries.get(track_ref)
    if not isinstance(summary, Mapping):
        raise ValueError("continuous tracking target track is unvalidated")
    if _tracking_identity_limitations(visual_result, summary):
        raise ValueError("continuous tracking target identity is ambiguous")
    event_bundle = validate_event_bundle_v1(visual_result.get("event_bundle"))
    if event_bundle["analysis_ref"] != analysis_ref:
        raise ValueError("continuous tracking event bundle is bound to another analysis")
    payload = _continuous_tracking_track_payload(
        analysis_ref=analysis_ref,
        window=window,
        resolution=resolution,
        quality=quality,
        track_ref=track_ref,
        target_samples=target_samples,
        summary=summary,
        target_change_points=_tracking_change_points_for_track(event_bundle, track_ref),
        available_channel_keys=available_channel_keys,
        alignment_latency_ms=alignment_latency_ms,
        crosshair_samples=crosshair_samples,
    )
    result = analyze_continuous_tracking_v1(payload)
    _apply_fused_alignment_metric(
        job, visual_result, result, analysis_ref=analysis_ref, window=window,
    )
    return result


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


# producer 的 canonical 映射（to_canonical_ms）经 source_pts_origin_ms 反推会带
# 浮点 ulp 误差；权威击杀 ↔ 生命窗终点配对允许的量化容差。
_TELEMETRY_LIFE_MATCH_TOLERANCE_MS = 2


def _switching_press_fusion_inputs(
    job: Mapping[str, object],
    visual_result: Mapping[str, object],
    window: Mapping[str, object],
) -> dict:
    """双源按压沿融合（合同 ``.zcode/fusion-spec-1005.md`` §二.1）。

    遥测 inputs L_down 沿（producer 的 shot 事件，canonical ms）优先，
    trace buttons 沿回填/交叉验证（``kovaak_tracker.input_fusion``）。
    trace 缺席/不可解码/事件束无 shot 沿 → 返回 ``{}``（分析器保持既有
    占位语义，不让融合缺陷拖垮整场分析）；``SourceSnapshotChangedError``
    原样上抛（冻结源中途变更是 fail-closed 契约，不归融合降级）。

    producer 事件束截断（``visual_event_budget_exceeded``）会使 quality
    停用全部 metric family → 分析器 outcome_only，截断的 shot 序列到不了
    融合路径，无需另设降级。
    """
    snapshot = job.get("input_snapshot")
    trace = snapshot.get("trace") if isinstance(snapshot, Mapping) else None
    if not isinstance(trace, Mapping) or trace.get("availability") != "available":
        return {}
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        isinstance(start_ms, bool) or isinstance(end_ms, bool)
        or not isinstance(start_ms, int) or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        return {}
    from . import worker
    from .kovaak_run_store import decode_mouse_snapshot_bytes
    from kovaak_tracker.input_fusion import fuse_press_edges, left_press_edges

    trace_bytes = worker._read_frozen_source_bytes("raw_input", trace)
    try:
        trace_points = decode_mouse_snapshot_bytes(trace_bytes)
    except ValueError:
        return {}
    event_bundle = visual_result.get("event_bundle")
    events = event_bundle.get("events") if isinstance(event_bundle, Mapping) else None
    if not isinstance(events, list):
        return {}
    telemetry_edges = sorted(
        int(event["start_ms"])
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_kind") == "shot"
        and start_ms <= int(event["start_ms"]) < end_ms
    )
    fused = fuse_press_edges(
        left_press_edges(trace_points, start_ms=start_ms, end_ms=end_ms),
        telemetry_edges,
    )
    return {
        "press_edges": fused["edges"],
        "press_edge_validation": fused["validation"],
    }


def _telemetry_switching_inputs(
    job: Mapping[str, object],
    visual_result: Mapping[str, object],
) -> dict:
    """遥测真值 visual_result -> target_switching_telemetry_input.v1 组装。

    权威击杀 = producer event_bundle 的 kill 事件（生命窗 t_end）；出生与
    候选可见性 = 冻结 meta 的目标生命窗（producer 同一身份源，按 track+时间
    配对，失配 fail-closed）。几何样本 = producer 投影的 local_samples。
    """
    from kovaak_tracker.analysis_evidence import validate_event_bundle_v1
    from . import external_telemetry_store as telemetry_store
    from .worker import _external_telemetry_source

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
        or resolution.get("aim_family") != "target_switching"
    ):
        raise ValueError("target switching visual result is bound to another analysis")
    source = _external_telemetry_source(dict(job))
    if source is None:
        raise ValueError("target switching telemetry source is unavailable")
    meta = telemetry_store.load_meta(str(source.get("external_run_id")))
    if not isinstance(meta, Mapping):
        raise ValueError("target switching telemetry meta is unavailable")
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        isinstance(start_ms, bool) or isinstance(end_ms, bool)
        or not isinstance(start_ms, int) or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        raise ValueError("target switching canonical window is unavailable")
    mapping = visual_result.get("video_time_mapping")
    origin_ms = (
        mapping.get("source_pts_origin_ms")
        if isinstance(mapping, Mapping) else None
    )
    if not isinstance(origin_ms, (int, float)) or isinstance(origin_ms, bool):
        raise ValueError("target switching telemetry time origin is unavailable")
    origin_t = float(origin_ms) / 1000.0

    def to_ms(source_t: float) -> int:
        return start_ms + int(round((source_t - origin_t) * 1000.0))

    # 冻结 meta 的生命窗（producer 的身份来源，同源不回读共享索引）。
    lives_by_tid: dict[int, list[tuple[float, float]]] = {}
    for item in meta.get("targets") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            tid = int(item["tid"])
        except (KeyError, TypeError, ValueError):
            continue
        windows: list[tuple[float, float]] = []
        for life in item.get("lives") or []:
            if not isinstance(life, Mapping):
                continue
            try:
                windows.append((float(life["t_start"]), float(life["t_end"])))
            except (KeyError, TypeError, ValueError):
                continue
        lives_by_tid[tid] = sorted(windows)

    prefix = f"{analysis_ref}:target-track:"
    event_bundle = validate_event_bundle_v1(visual_result.get("event_bundle"))
    if event_bundle["analysis_ref"] != analysis_ref:
        raise ValueError("target switching event bundle is bound to another analysis")
    kill_events = sorted(
        (
            event for event in event_bundle["events"]
            if event["event_kind"] == "kill" and len(event["actor_refs"]) == 1
            and event["start_ms"] == event["end_ms"]
        ),
        key=lambda event: (event["start_ms"], event["event_id"]),
    )
    authoritative_kills: list[dict] = []
    for event in kill_events:
        track_ref = event["actor_refs"][0]
        if not isinstance(track_ref, str) or not track_ref.startswith(prefix):
            raise ValueError("target switching kill actor is invalid")
        try:
            tid = int(track_ref[len(prefix):])
        except ValueError as error:
            raise ValueError("target switching kill actor is invalid") from error
        death_ms = int(event["start_ms"])
        matches = [
            (to_ms(life_start), to_ms(life_end))
            for life_start, life_end in lives_by_tid.get(tid, ())
            if abs(to_ms(life_end) - death_ms) <= _TELEMETRY_LIFE_MATCH_TOLERANCE_MS
        ]
        if len(matches) != 1:
            # 权威击杀必须唯一对齐一条生命窗（同源身份 + 时间双键）。
            raise ValueError("target switching kill has no unique life window")
        birth_ms, _ = matches[0]
        authoritative_kills.append({
            "event_ref": event["event_id"],
            "time_ms": death_ms,
            "kill_index": len(authoritative_kills) + 1,
            "target_track_ref": track_ref,
            "target_birth_ms": birth_ms,
        })
    if not authoritative_kills:
        raise ValueError("target switching has no authoritative kills")
    # 交叉验证：窗口内生命窗终点与 kill 事件 1:1（超时/截断/篡改 fail-closed）。
    expected_deaths = sorted(
        to_ms(life_end)
        for windows in lives_by_tid.values()
        for _, life_end in windows
        if start_ms <= to_ms(life_end) < end_ms
    )
    observed_deaths = sorted(kill["time_ms"] for kill in authoritative_kills)
    if len(expected_deaths) != len(observed_deaths) or any(
        abs(expected - observed) > _TELEMETRY_LIFE_MATCH_TOLERANCE_MS
        for expected, observed in zip(expected_deaths, observed_deaths)
    ):
        raise ValueError("target switching kills do not cover the life windows")

    local_samples = visual_result.get("local_samples")
    if not isinstance(local_samples, Mapping):
        raise ValueError("target switching visual samples are unavailable")
    crosshair_samples = local_samples.get("crosshair.position")
    if not isinstance(crosshair_samples, list) or not crosshair_samples:
        raise ValueError("target switching crosshair samples are unavailable")
    target_tracks = [
        (match.group(1), samples)
        for sample_key, samples in local_samples.items()
        if (match := re.fullmatch(r"target\.([A-Za-z0-9_-]+)\.position", str(sample_key)))
        and isinstance(samples, list)
    ]
    tracks = [
        {
            "track_ref": f"{analysis_ref}:target-track:{track_id}",
            "samples": [
                sample for sample in samples if isinstance(sample, Mapping)
            ],
        }
        # 空样本轨道（窗内从不可见，如始终在相机后方）无几何可分析；
        # 它的生命窗仍参与候选可见性（target_lives）。
        for track_id, samples in sorted(target_tracks, key=lambda item: str(item[0]))
        if samples
    ]
    target_lives = [
        {
            "track_ref": f"{analysis_ref}:target-track:{tid}",
            "lives": [
                [to_ms(life_start), to_ms(life_end)]
                for life_start, life_end in windows
            ],
        }
        for tid, windows in sorted(lives_by_tid.items())
        if windows
    ]
    quality = dict(visual_result.get("quality") or {})
    enabled_families = [
        "target_switching" if family == "switching" else family
        for family in quality.get("enabled_metric_families") or []
    ]
    quality["enabled_metric_families"] = sorted(set(enabled_families))
    selector = visual_result.get("visual_runtime_selector")
    viewport_fov_deg = (
        selector.get("fov")
        if isinstance(selector, Mapping) and isinstance(selector.get("fov"), (int, float))
        else 103.0
    )
    payload = {
        "schema_version": "target_switching_telemetry_input.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "scenario_resolution": dict(resolution),
        "visual_quality": quality,
        "target_tracks": tracks,
        "target_lives": target_lives,
        "authoritative_kills": authoritative_kills,
        "crosshair_samples": crosshair_samples,
        "source_signal_bundle": visual_result.get("signal_bundle"),
        "source_sample_sets": visual_result.get("sample_sets"),
        "viewport_fov_deg": viewport_fov_deg,
        # P1-B 已知缺陷：inputs 沿（L_down 上升沿）丢失 ~90%，首发相关指标
        # 只做占位 + 数据质量标注（registry: metric:first_shot_error 不承诺数值）。
        "click_anchor_status": "unreliable",
        "comparison": None,
    }
    payload.update(_switching_press_fusion_inputs(job, visual_result, window))
    return payload


def run_target_switching_telemetry_analysis(
    job: dict,
    visual_result: Mapping[str, object],
) -> dict:
    """遥测真值 target_switching 分析（本进程，纯数值，无 CV 子进程）。"""
    from kovaak_tracker.switching_analysis import analyze_target_switching_telemetry_v1

    return analyze_target_switching_telemetry_v1(
        _telemetry_switching_inputs(job, visual_result),
    )
