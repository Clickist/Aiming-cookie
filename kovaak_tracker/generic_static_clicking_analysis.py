"""Generic static-clicking visual analysis.

Pipeline (proposal static-cv-pipeline-proposal-2026-08-16 §3.1, no-training
route): decode the run-owned MP4, pick a color hypothesis by the three-shape
prior, detect every frame, rebuild stationary targets as birth/death tracks,
then associate raw clicks and Stats kills geometrically against those tracks.
The crosshair is the viewport center (spike-verified), so the video only has
to answer "where were the targets"; the raw trace already answers "where the
aim went". Every stage fails closed: no hypothesis, no preroll receipt, or a
failed quality gate leaves the existing input-only baseline untouched.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from .generic_visual_detection import (
    GENERIC_VISUAL_DETECTOR_VERSION,
    detect_generic_targets,
    select_color_hypothesis,
)

GENERIC_STATIC_CLICKING_ANALYSIS_VERSION = "static_clicking.generic_visual.v1"
GENERIC_STATIC_VISUAL_RESULT_SCHEMA = "generic_static_visual_result.v1"
GENERIC_STATIC_ASSOCIATION_SCHEMA = "generic_static_association.v1"

# Detection knobs (real-scenario spike measurements, 2026-08-16).
HYPOTHESIS_SAMPLE_FRAMES = 25
HYPOTHESIS_SAMPLE_MAX_WIDTH = 640
FRAME_BUDGET_PER_SECOND = 240
FRAME_BUDGET_CAP = 250_000
BLOB_MIN_AREA_RATIO = 2e-5
TRACK_MERGE_RADIUS_PX = 24.0
TRACK_MAX_GAP_MS = 200.0
TRACK_SHRINK_GUARD = 0.5
TRACK_FRAGMENT_REBIND_MS = 150.0
TRACK_MIN_SAMPLES = 2
HIT_MARGIN_PX = 10.0
# The shape-classified detector drops motion-blurred blobs, so a target's
# track ends when the approach flick starts — typically 100-200 ms before the
# click. The engaged window must look back across the whole flick, not just
# a few frames (spike §2.4 failure mode 1).
HIT_LOOKBACK_MS = 200.0
KILL_PAIR_BACK_WINDOW_MS = 200.0
KILL_PAIR_WINDOW_MS = 250.0

# Quality gate: measurement validity needs kills to pair with target deaths.
GATE_MIN_KILLS = 5
GATE_MIN_KILL_PAIRING_RATE = 0.5
GATE_MIN_FRAME_COVERAGE = 0.25


class GenericVisualPreprocessingUnavailable(ValueError):
    """The untrained generic visual path is unavailable for this Run."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_time_ms(
    time_mapping: Mapping[str, object], source_pts_ms: float,
) -> int:
    return int(time_mapping["canonical_origin_ms"] + round(
        source_pts_ms
        - time_mapping["source_pts_origin_ms"]
        + time_mapping.get("decode_preroll_ms", 0.0)
    ))


def extract_left_click_rising_edges_v1(
    points: Sequence[Mapping[str, int]],
    *,
    start_ms: int,
    end_ms: int,
) -> list[int]:
    """Return canonical-window timestamps for left-button rising edges."""
    anchors: list[int] = []
    previous_pressed = False
    for point in points:
        pressed = bool(point["buttons"] & 1)
        if (
            pressed
            and not previous_pressed
            and start_ms <= point["timestamp_ms"] < end_ms
        ):
            anchors.append(point["timestamp_ms"])
        previous_pressed = pressed
    return anchors


def run_generic_static_clicking_detection_v1(
    *,
    media_path: str,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
) -> dict:
    """Detect stationary targets across the whole clip under one hypothesis.

    Hypothesis selection runs on evenly spread, downscaled samples so the
    scoring sees the whole clip without holding full frames in memory; the
    detection pass then decodes every frame at native resolution.
    """
    import cv2
    import numpy as np

    if video_time_mapping.get("schema_version") != "visual_video_time_mapping.v2":
        raise GenericVisualPreprocessingUnavailable(
            "generic_video_time_mapping_v2_required",
        )
    window_start = int(canonical_time_window["start_ms"])
    window_end = int(canonical_time_window["end_ms"])
    frame_budget = min(
        FRAME_BUDGET_CAP,
        max(
            1,
            int((window_end - window_start) / 1_000 * FRAME_BUDGET_PER_SECOND)
            + FRAME_BUDGET_PER_SECOND,
        ),
    )

    capture = cv2.VideoCapture(media_path)
    try:
        if not capture.isOpened():
            raise GenericVisualPreprocessingUnavailable("generic_media_unreadable")
        total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        sample_stride = max(
            1, int(total_frames // HYPOTHESIS_SAMPLE_FRAMES) if total_frames > 0 else 1,
        )
        samples: list[np.ndarray] = []
        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % sample_stride == 0 and len(samples) < HYPOTHESIS_SAMPLE_FRAMES:
                height, width = frame.shape[:2]
                if width > HYPOTHESIS_SAMPLE_MAX_WIDTH:
                    scale = HYPOTHESIS_SAMPLE_MAX_WIDTH / width
                    frame = cv2.resize(
                        frame, (HYPOTHESIS_SAMPLE_MAX_WIDTH, int(height * scale)),
                    )
                samples.append(frame)
            frame_index += 1
        selection = select_color_hypothesis(samples)
        if selection is None:
            raise GenericVisualPreprocessingUnavailable(
                "generic_color_hypothesis_unavailable",
            )
    finally:
        capture.release()

    hypothesis = selection["hypothesis"]
    height, width = samples[0].shape[:2]
    sample_area = width * height
    # Area bounds are ratios, so the downscale used for selection and the
    # native-resolution detection pass agree on what "a target-sized blob" is.
    sample_min_area = max(12.0, BLOB_MIN_AREA_RATIO * sample_area)
    native_hypothesis = {
        **hypothesis,
        "min_area": sample_min_area,
    }

    capture = cv2.VideoCapture(media_path)
    frame_detections: list[dict] = []
    frames_decoded = 0
    frames_with_detection = 0
    resolution: list[int] | None = None
    try:
        if not capture.isOpened():
            raise GenericVisualPreprocessingUnavailable("generic_media_unreadable")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if len(frame_detections) >= frame_budget:
                raise GenericVisualPreprocessingUnavailable(
                    "generic_frame_budget_exceeded",
                )
            if resolution is None:
                resolution = [int(frame.shape[1]), int(frame.shape[0])]
                native_hypothesis["min_area"] = max(
                    40.0, BLOB_MIN_AREA_RATIO * resolution[0] * resolution[1],
                )
            pts = capture.get(cv2.CAP_PROP_POS_MSEC)
            pts_value = (
                float(pts)
                if isinstance(pts, (int, float)) and math.isfinite(float(pts))
                else None
            )
            frames_decoded += 1
            if pts_value is None:
                continue
            canonical_ms = _canonical_time_ms(video_time_mapping, pts_value)
            if not window_start <= canonical_ms < window_end:
                continue
            result = detect_generic_targets(
                frame, native_hypothesis, crosshair_exemption=True,
            )
            if result["targets"]:
                frames_with_detection += 1
            frame_detections.append({
                "canonical_time_ms": canonical_ms,
                "targets": result["targets"],
            })
    finally:
        capture.release()

    if not frame_detections:
        raise GenericVisualPreprocessingUnavailable("generic_no_frames_in_window")

    tracks = build_stationary_target_tracks_v1(
        frame_detections,
        analysis_ref=analysis_ref,
        merge_radius_px=TRACK_MERGE_RADIUS_PX,
        max_gap_ms=TRACK_MAX_GAP_MS,
    )
    return {
        "schema_version": GENERIC_STATIC_VISUAL_RESULT_SCHEMA,
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(canonical_time_window),
        "video_time_mapping": dict(video_time_mapping),
        "detector": {
            "detector_version": GENERIC_VISUAL_DETECTOR_VERSION,
            **selection,
        },
        "resolution": resolution,
        "frames_decoded": frames_decoded,
        "frames_in_window": len(frame_detections),
        "frames_with_detection": frames_with_detection,
        "frame_coverage": (
            frames_with_detection / len(frame_detections) if frame_detections else 0.0
        ),
        "tracks": tracks,
        "limitations": [],
    }


def build_stationary_target_tracks_v1(
    frame_detections: Sequence[Mapping[str, object]],
    *,
    analysis_ref: str,
    merge_radius_px: float,
    max_gap_ms: float,
) -> list[dict]:
    """Organize detections into stationary birth/death tracks.

    No per-frame identity: a detection extends the nearest open track when it
    lands within ``merge_radius_px`` of that track's median position and keeps
    at least half its median area (death animations shrink and fragment).
    Tracks silent for ``max_gap_ms`` close with their last stable frame.
    """
    open_tracks: list[dict] = []
    closed: list[dict] = []
    for frame in frame_detections:
        time_ms = int(frame["canonical_time_ms"])
        for track in open_tracks:
            track["_pending"] = False
        for target in frame["targets"]:
            best: dict | None = None
            best_distance = merge_radius_px
            for track in open_tracks:
                # Match against the last sample: stationary targets keep their
                # position, and fragments never extend a track (shrink guard),
                # so the last sample is always a full-size sighting. Degraded
                # crosshair-covered samples bypass the shrink guard — the
                # aimed target must survive its own approach smear.
                distance = max(
                    abs(target["x"] - track["last_x"]),
                    abs(target["y"] - track["last_y"]),
                )
                if distance < best_distance and (
                    target["shape"] == "degraded"
                    or target["area"] >= TRACK_SHRINK_GUARD * track["last_area"]
                ):
                    best = track
                    best_distance = distance
            if best is not None:
                best["samples"].append(target)
                best["last_ms"] = time_ms
                best["last_x"] = target["x"]
                best["last_y"] = target["y"]
                best["last_area"] = target["area"]
                best["_pending"] = True
                continue
            open_tracks.append({
                "track_ref": None,
                "birth_ms": time_ms,
                "last_ms": time_ms,
                "samples": [target],
                "last_x": target["x"],
                "last_y": target["y"],
                "last_area": target["area"],
                "_pending": True,
            })
        still_open: list[dict] = []
        for track in open_tracks:
            if time_ms - track["last_ms"] > max_gap_ms:
                closed.append(track)
            else:
                still_open.append(track)
        open_tracks = still_open
    closed.extend(open_tracks)
    closed = _rebind_track_fragments_v1(closed, merge_radius_px=merge_radius_px)

    tracks: list[dict] = []
    for index, track in enumerate(closed, 1):
        samples = track["samples"]
        if len(samples) < TRACK_MIN_SAMPLES:
            continue
        xs = sorted(sample["x"] for sample in samples)
        ys = sorted(sample["y"] for sample in samples)
        widths = sorted(sample["width"] for sample in samples)
        heights = sorted(sample["height"] for sample in samples)
        areas = sorted(sample["area"] for sample in samples)
        shapes = [
            sample["shape"] for sample in samples
            if sample["shape"] != "degraded"
        ] or [sample["shape"] for sample in samples]
        tracks.append({
            "track_ref": f"{analysis_ref}:generic-target-track:{index}",
            "birth_ms": track["birth_ms"],
            "death_ms": track["last_ms"],
            "shape": max(set(shapes), key=shapes.count),
            "x": xs[len(xs) // 2],
            "y": ys[len(ys) // 2],
            # The end sighting is the closest thing to "where the target was
            # when it died": camera flicks sweep every target across the
            # screen, so the dwell median and the death position differ.
            "end_x": samples[-1]["x"],
            "end_y": samples[-1]["y"],
            "half_width_px": max(4.0, widths[len(widths) // 2] / 2.0),
            "half_height_px": max(4.0, heights[len(heights) // 2] / 2.0),
            "median_area": areas[len(areas) // 2],
            "sample_count": len(samples),
            "real_sample_count": sum(
                1 for sample in samples if sample["shape"] != "degraded"
            ),
        })
    tracks.sort(key=lambda track: (track["birth_ms"], track["x"], track["y"]))
    for index, track in enumerate(tracks, 1):
        track["track_ref"] = f"{analysis_ref}:generic-target-track:{index}"
    return tracks


def _rebind_track_fragments_v1(
    closed: list[dict], *, merge_radius_px: float,
) -> list[dict]:
    """Merge fragments of one stationary target split by motion blur.

    A fast approach smears the target and the shrink guard closes its track
    mid-life; the same-position target reappearing within the rebind window
    is one target, not two. Operates on raw tracks (before the median
    projection strips the sample lists) and chains greedily in birth order.
    """
    merged: list[dict] = []
    for track in sorted(closed, key=lambda item: item["birth_ms"]):
        for candidate in reversed(merged):
            gap = track["birth_ms"] - candidate["last_ms"]
            distance = max(
                abs(track["last_x"] - candidate["last_x"]),
                abs(track["last_y"] - candidate["last_y"]),
            )
            if 0 <= gap <= TRACK_FRAGMENT_REBIND_MS and distance < merge_radius_px:
                candidate["samples"].extend(track["samples"])
                candidate["last_ms"] = track["last_ms"]
                candidate["last_x"] = track["last_x"]
                candidate["last_y"] = track["last_y"]
                candidate["last_area"] = track["last_area"]
                break
        else:
            merged.append(track)
    return merged


def associate_generic_static_clicks_v1(
    *,
    analysis_ref: str,
    generic_visual_result: Mapping[str, object],
    click_times_ms: Sequence[int],
    kill_records: Sequence[Mapping[str, object]],
    viewport_size: Sequence[int],
    deg_per_px: float | None,
) -> dict:
    """Geometric hit/miss per raw click plus kill pairing and residuals."""
    tracks = [
        track for track in generic_visual_result["tracks"]
        if track["sample_count"] >= TRACK_MIN_SAMPLES
        # Degraded-only tracks are crosshair fallbacks, not targets: a real
        # target always has at least one shape-classified sighting.
        and track.get("real_sample_count", track["sample_count"]) >= 1
    ]
    width, height = int(viewport_size[0]), int(viewport_size[1])
    crosshair_x = width / 2.0
    crosshair_y = height / 2.0

    def _engaged(time_ms: int) -> list[Mapping[str, object]]:
        # The approach flick smears the target before the click and the
        # shape-classified detector drops the smeared blobs, so a target
        # counts when its track overlaps the whole flick window, not only
        # when it is still alive at the exact click timestamp.
        return [
            track for track in tracks
            if track["birth_ms"] <= time_ms
            and track["death_ms"] >= time_ms - HIT_LOOKBACK_MS
        ]

    def _hit(track: Mapping[str, object]) -> bool:
        return (
            abs(track["x"] - crosshair_x) <= track["half_width_px"] + HIT_MARGIN_PX
            and abs(track["y"] - crosshair_y) <= track["half_height_px"] + HIT_MARGIN_PX
        )

    click_outcomes: list[dict] = []
    miss_vectors: list[dict] = []
    for index, click_ms in enumerate(click_times_ms, 1):
        engaged = _engaged(click_ms)
        hitting = [track for track in engaged if _hit(track)]
        outcome: dict = {
            "event_id": f"{analysis_ref}:generic-click:{index}",
            "click_time_ms": click_ms,
            "outcome": "hit" if hitting else ("miss" if engaged else "no_target"),
        }
        if not hitting and engaged:
            nearest = min(
                engaged,
                key=lambda track: (
                    (track["x"] - crosshair_x) ** 2
                    + (track["y"] - crosshair_y) ** 2
                ),
            )
            vector_px = {
                "x": nearest["x"] - crosshair_x,
                "y": nearest["y"] - crosshair_y,
                "distance": (
                    (nearest["x"] - crosshair_x) ** 2
                    + (nearest["y"] - crosshair_y) ** 2
                ) ** 0.5,
            }
            outcome["miss_vector_px"] = vector_px
            if deg_per_px is not None:
                outcome["miss_vector_deg"] = {
                    key: value * deg_per_px for key, value in vector_px.items()
                }
            miss_vectors.append(outcome)
        click_outcomes.append(outcome)

    kill_residuals: list[dict] = []
    kills_paired = 0
    for kill in kill_records:
        kill_ms = int(kill["canonical_time_ms"])
        # A killed target's track may end before the Stats kill timestamp
        # (approach blur cuts it) or after it (death animation residue),
        # so pair across both sides and let center proximity decide.
        candidates = [
            track for track in tracks
            if (
                -KILL_PAIR_BACK_WINDOW_MS
                <= track["death_ms"] - kill_ms
                <= KILL_PAIR_WINDOW_MS
            )
        ]
        if candidates:
            # The killed target's final dwell segment ends closest to the
            # crosshair: the camera flick sweeps it toward center before the
            # smear cuts the track. Distance first, death timing breaks ties.
            paired = min(
                candidates,
                key=lambda track: (
                    (track.get("end_x", track["x"]) - crosshair_x) ** 2
                    + (track.get("end_y", track["y"]) - crosshair_y) ** 2,
                    abs(track["death_ms"] - kill_ms),
                ),
            )
            kills_paired += 1
            end_x = paired.get("end_x", paired["x"])
            end_y = paired.get("end_y", paired["y"])
            # The end sighting precedes the approach smear, so this residual
            # measures the last clear sighting, not the landing point.
            residual_px = {
                "x": end_x - crosshair_x,
                "y": end_y - crosshair_y,
                "distance": (
                    (end_x - crosshair_x) ** 2 + (end_y - crosshair_y) ** 2
                ) ** 0.5,
            }
            residual: dict = {
                "event_id": (
                    f"{analysis_ref}:generic-kill-residual:{kill['kill_index']}"
                ),
                "kill_time_ms": kill_ms,
                "kill_index": kill["kill_index"],
                "target_track_ref": paired["track_ref"],
                "residual_px": residual_px,
            }
            if deg_per_px is not None:
                residual["residual_deg"] = {
                    key: value * deg_per_px for key, value in residual_px.items()
                }
            kill_residuals.append(residual)

    kill_total = len(kill_records)
    pairing_rate = kills_paired / kill_total if kill_total else None
    frame_coverage = float(generic_visual_result["frame_coverage"])
    gate_reasons: list[str] = []
    if kill_total >= GATE_MIN_KILLS and (
        pairing_rate is None or pairing_rate < GATE_MIN_KILL_PAIRING_RATE
    ):
        gate_reasons.append("kill_pairing_rate_below_threshold")
    if frame_coverage < GATE_MIN_FRAME_COVERAGE and not (
        kill_total >= GATE_MIN_KILLS
        and pairing_rate is not None
        and pairing_rate >= GATE_MIN_KILL_PAIRING_RATE
    ):
        gate_reasons.append("frame_coverage_below_threshold")

    return {
        "schema_version": GENERIC_STATIC_ASSOCIATION_SCHEMA,
        "analysis_ref": analysis_ref,
        "click_count": len(click_outcomes),
        "hit_count": sum(
            1 for outcome in click_outcomes if outcome["outcome"] == "hit"
        ),
        "miss_count": sum(
            1 for outcome in click_outcomes if outcome["outcome"] == "miss"
        ),
        "no_target_count": sum(
            1 for outcome in click_outcomes if outcome["outcome"] == "no_target"
        ),
        "kills_total": kill_total,
        "kills_paired": kills_paired,
        "kill_pairing_rate": pairing_rate,
        "deg_per_px": deg_per_px,
        "gate": {
            "passed": not gate_reasons,
            "reasons": gate_reasons,
            "frame_coverage": frame_coverage,
        },
        "click_outcomes": click_outcomes,
        "kill_residuals": kill_residuals,
        "limitations": [
            "kill_residual_is_pre_smear_sighting",
            *([] if deg_per_px is not None else ["angular_calibration_unavailable"]),
        ],
    }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return float(sorted_values[index])


def _distribution(values: Sequence[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p10": _percentile(ordered, 0.10),
        "p25": _percentile(ordered, 0.25),
        "median": _percentile(ordered, 0.50),
        "p75": _percentile(ordered, 0.75),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
        "histogram_bins": [],
    }


def build_generic_static_metric_records_v1(
    association: Mapping[str, object],
    *,
    source_ref: str,
) -> list[dict]:
    """Project the association summary into registered metric records."""
    metric_version = GENERIC_STATIC_CLICKING_ANALYSIS_VERSION
    provenance = {"kind": "measured", "source_refs": [source_ref]}
    gate = association["gate"]

    def _count_record(key: str, value: int) -> dict:
        return {
            "schema_version": "metric_record.v1",
            "metric_key": key,
            "metric_version": metric_version,
            "value": float(value),
            "unit": "count",
            "availability": "available",
            "classification": "deterministic",
            "provenance": provenance,
            "population": {
                "sample_count": association["click_count"],
                "valid_count": association["click_count"],
                "excluded_count": 0,
            },
            "distribution": None,
            "condition_refs": [],
            "event_refs": [],
            "evidence_segment_refs": [],
            "coverage": None,
            "confidence": None,
            "limitations": list(association["limitations"]),
        }

    def _value_record(key: str, value: float | None, unit: str) -> dict:
        available = value is not None
        return {
            "schema_version": "metric_record.v1",
            "metric_key": key,
            "metric_version": metric_version,
            "value": value,
            "unit": unit,
            "availability": "available" if available else "unavailable",
            "classification": "deterministic",
            "provenance": provenance,
            "population": {
                "sample_count": association["click_count"],
                "valid_count": association["click_count"] if available else 0,
                "excluded_count": 0,
            },
            "distribution": None,
            "condition_refs": [],
            "event_refs": [],
            "evidence_segment_refs": [],
            "coverage": None,
            "confidence": None,
            "limitations": list(association["limitations"]),
        }

    def _distribution_record(
        key: str, values: Sequence[float], unit: str,
    ) -> dict:
        record = _value_record(key, None, unit)
        distribution = _distribution(values)
        if distribution is not None:
            record["value"] = distribution["median"]
            record["availability"] = "available"
            record["distribution"] = distribution
        return record

    deg_available = association["deg_per_px"] is not None
    miss_vectors = [
        outcome["miss_vector_deg"]
        for outcome in association["click_outcomes"]
        if "miss_vector_deg" in outcome
    ]
    residuals = [
        residual["residual_deg"]
        for residual in association["kill_residuals"]
        if "residual_deg" in residual
    ]
    records = [
        _count_record("static_clicking.generic.click_count", association["click_count"]),
        _count_record("static_clicking.generic.hit_clicks", association["hit_count"]),
        _count_record("static_clicking.generic.miss_clicks", association["miss_count"]),
        _count_record(
            "static_clicking.generic.no_target_clicks", association["no_target_count"],
        ),
        _distribution_record(
            "static_clicking.generic.miss_distance_deg",
            [vector["distance"] for vector in miss_vectors],
            "degrees",
        ) if deg_available else _value_record(
            "static_clicking.generic.miss_distance_deg", None, "degrees",
        ),
        _distribution_record(
            "static_clicking.generic.miss_vector_x_deg",
            [vector["x"] for vector in miss_vectors],
            "degrees",
        ) if deg_available else _value_record(
            "static_clicking.generic.miss_vector_x_deg", None, "degrees",
        ),
        _distribution_record(
            "static_clicking.generic.miss_vector_y_deg",
            [vector["y"] for vector in miss_vectors],
            "degrees",
        ) if deg_available else _value_record(
            "static_clicking.generic.miss_vector_y_deg", None, "degrees",
        ),
        _distribution_record(
            "static_clicking.generic.kill_residual_distance_deg",
            [residual["distance"] for residual in residuals],
            "degrees",
        ) if deg_available else _value_record(
            "static_clicking.generic.kill_residual_distance_deg", None, "degrees",
        ),
        _value_record(
            "static_clicking.generic.kill_pairing_rate",
            association["kill_pairing_rate"],
            "ratio",
        ),
        _value_record(
            "static_clicking.generic.frame_coverage",
            gate["frame_coverage"],
            "ratio",
        ),
    ]
    return records


def extend_analysis_evidence_with_generic_static_clicking_v1(
    artifact: Mapping[str, Any],
    generic_visual_result: Mapping[str, Any],
    association: Mapping[str, Any],
    *,
    video_source_ref: str,
) -> dict:
    """Append generic track/click/kill events and metrics to the artifact."""
    from .analysis_evidence import validate_analysis_evidence_artifact_v1

    projected = copy.deepcopy(dict(artifact))
    analysis_ref = projected["analysis_ref"]
    window = projected["canonical_time_window"]
    window_start = int(window["start_ms"])
    window_end = int(window["end_ms"])

    def _clamp(time_ms: int) -> int:
        return max(window_start, min(window_end - 1, int(time_ms)))

    events: list[dict] = []
    for track in generic_visual_result["tracks"]:
        events.append({
            "event_id": track["track_ref"],
            "event_kind": "generic_target_track",
            "start_ms": _clamp(track["birth_ms"]),
            "end_ms": _clamp(track["death_ms"]),
            "actor_refs": [],
            "source_refs": [video_source_ref],
            "confidence": float(
                generic_visual_result["detector"].get("shape_consistency", 0.0),
            ),
            "attributes": {
                "birth_ms": int(track["birth_ms"]),
                "death_ms": int(track["death_ms"]),
                "shape": str(track["shape"]),
                "median_x_px": float(track["x"]),
                "median_y_px": float(track["y"]),
                "half_width_px": float(track["half_width_px"]),
                "half_height_px": float(track["half_height_px"]),
                "sample_count": int(track["sample_count"]),
            },
            "limitations": [],
        })
    for outcome in association["click_outcomes"]:
        attributes: dict[str, Any] = {
            "click_time_ms": int(outcome["click_time_ms"]),
            "outcome": outcome["outcome"],
        }
        if "miss_vector_px" in outcome:
            attributes.update({
                "miss_vector_x_px": outcome["miss_vector_px"]["x"],
                "miss_vector_y_px": outcome["miss_vector_px"]["y"],
                "miss_distance_px": outcome["miss_vector_px"]["distance"],
            })
            if "miss_vector_deg" in outcome:
                attributes.update({
                    "miss_vector_x_deg": outcome["miss_vector_deg"]["x"],
                    "miss_vector_y_deg": outcome["miss_vector_deg"]["y"],
                })
        events.append({
            "event_id": outcome["event_id"],
            "event_kind": "generic_click_outcome",
            "start_ms": _clamp(outcome["click_time_ms"]),
            "end_ms": _clamp(outcome["click_time_ms"]),
            "actor_refs": [],
            "source_refs": [video_source_ref],
            "confidence": 0.9,
            "attributes": attributes,
            "limitations": [],
        })
    for residual in association["kill_residuals"]:
        attributes = {
            "kill_time_ms": int(residual["kill_time_ms"]),
            "kill_index": int(residual["kill_index"]),
            "target_track_ref": residual["target_track_ref"],
            "residual_x_px": residual["residual_px"]["x"],
            "residual_y_px": residual["residual_px"]["y"],
            "residual_distance_px": residual["residual_px"]["distance"],
        }
        if "residual_deg" in residual:
            attributes.update({
                "residual_x_deg": residual["residual_deg"]["x"],
                "residual_y_deg": residual["residual_deg"]["y"],
            })
        events.append({
            "event_id": residual["event_id"],
            "event_kind": "generic_kill_residual",
            "start_ms": _clamp(residual["kill_time_ms"]),
            "end_ms": _clamp(residual["kill_time_ms"]),
            "actor_refs": [],
            "source_refs": [video_source_ref],
            "confidence": 0.9,
            "attributes": attributes,
            "limitations": [],
        })
    events.sort(key=lambda event: (event["start_ms"], event["event_id"]))
    projected["event_bundles"].append({
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_associations": [],
    })
    projected["metric_records"].extend(
        build_generic_static_metric_records_v1(
            association, source_ref=video_source_ref,
        ),
    )
    for limitation in association["limitations"]:
        if limitation not in projected["limitations"]:
            projected["limitations"].append(limitation)
    return validate_analysis_evidence_artifact_v1(projected)


__all__ = [
    "GENERIC_STATIC_CLICKING_ANALYSIS_VERSION",
    "GENERIC_STATIC_VISUAL_RESULT_SCHEMA",
    "GENERIC_STATIC_ASSOCIATION_SCHEMA",
    "GenericVisualPreprocessingUnavailable",
    "associate_generic_static_clicks_v1",
    "build_generic_static_metric_records_v1",
    "build_stationary_target_tracks_v1",
    "extend_analysis_evidence_with_generic_static_clicking_v1",
    "extract_left_click_rising_edges_v1",
    "run_generic_static_clicking_detection_v1",
]
