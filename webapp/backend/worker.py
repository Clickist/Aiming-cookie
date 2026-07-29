from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import re
import socket
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from . import queue
from .config import DESKTOP_LOCAL_PROFILE, HEARTBEAT_INTERVAL_SECONDS
from .contracts import (
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    ANALYSIS_VERSION,
    CONTINUOUS_TRACKING_ANALYSIS_VERSION,
    DYNAMIC_CLICKING_ANALYSIS_VERSION,
    NATIVE_ANALYSIS_VERSION,
    TARGET_SWITCHING_ANALYSIS_VERSION,
    build_analysis_result_v2,
    build_artifact_manifest_v2,
    build_error_v1,
    validate_scenario_resolution_v1,
)
from .read_models import resolve_calibration_v1

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
SCENARIO_OUTCOME_ONLY_VERSION = "scenario_outcome_only.v1"
VISUAL_WORKER_RESPONSE_LIMIT_BYTES = 64 * 1024 * 1024
VISUAL_WORKER_SHUTDOWN_GRACE_SECONDS = 2.0
VISUAL_WORKER_JOB_FIELDS = ("id", "kovaak_run_id", "video_path", "input_snapshot")
VISUAL_WORKER_EVIDENCE_JOB_FIELDS = ("id", "user_id", "input_snapshot")

_REVIEWED_TRACKING_SCENARIO_HASH = "b2ae4a24b710e36afc6e57c61f590ab4"
_REVIEWED_TRACKING_SCENARIO_PROFILE_REF = (
    "scenario:tracking.whj_smooth_strafe_sphere_easy@1"
)
_REVIEWED_DYNAMIC_SCENARIO_HASH = "a37d2ba4f3f33d59ae7018e37445a5e9"
_REVIEWED_DYNAMIC_SCENARIO_PROFILE_REF = "scenario:dynamic.pasu_small_reload@1"
_REVIEWED_SWITCHING_SCENARIO_HASH = "3b42bdfd38a6b194737d650f3f53e8c1"
_REVIEWED_SWITCHING_SCENARIO_PROFILE_REF = "scenario:switching.beants_larger@1"
_REVIEWED_SWITCHING_DETECTOR_CONFIG_REF = (
    "detector-config:sha256:"
    "b3a5ee7add541acfcb172cb5eebcb91af4d506bfcf165f658809912d782cfea5"
)


def _build_reviewed_single_target_tracking_producer() -> dict:
    """Bind the reviewed single-target run to the legacy CSRT adapter."""
    from kovaak_tracker.visual_signals import (
        VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
        VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
        build_visual_quality_profile_v2,
        visual_detector_config_ref_v1,
    )

    detector_config = {
        "schema_version": "visual_target_detector.v1",
        "aim_point_mode": "fixed_viewport_center",
        "target": {
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 255, 80],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    detector_config_ref = visual_detector_config_ref_v1(detector_config)
    visual_quality_profile = build_visual_quality_profile_v2(
        producer_id=VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
        producer_version=VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
        annotation_set_ref=(
            "annotation-set:whj-smooth-strafe-sphere-easy-single-target.v1"
        ),
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": detector_config_ref,
            "hud_mask_version": "visual_hud_mask.task6.v1",
            "annotated_map_or_background_labels": [
                "whj-smooth-strafe-sphere-easy",
            ],
            "annotated_target_appearance_labels": ["round-target"],
        },
        validated_selectors=[{
            "schema_version": "visual_runtime_selector.v1",
            "scenario_hash": _REVIEWED_TRACKING_SCENARIO_HASH,
            "resolution": [1920, 1080],
            "canonical_video_mapping_version": "visual_video_time_mapping.v1",
            "fov": None,
        }],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash",
                "resolution",
                "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "tracking": [
                "center_error_median_px",
                "center_error_p95_px",
                "radius_or_hitbox_error_px",
                "false_positive_rate",
                "identity_switch_rate",
                "minimum_coverage",
            ],
        },
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds={
            "center_error_median_px": 4.0,
            "center_error_p95_px": 7.0,
            "radius_or_hitbox_error_px": 2.0,
            "false_positive_rate": 0.05,
            "identity_switch_rate": 0.01,
            "occlusion_reentry_accuracy": 0.95,
            "minimum_coverage": 0.95,
        },
        validation_results={
            "center_error_median_px": 3.28,
            "center_error_p95_px": 6.03,
            "radius_or_hitbox_error_px": 1.0,
            "false_positive_rate": 0.0,
            "identity_switch_rate": 0.0,
            "occlusion_reentry_accuracy": None,
            "minimum_coverage": 1.0,
        },
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[
            "Exact scenario hash, 1920x1080 resolution and one target bot only.",
            "The reviewed legacy black-ball detector initializes one CSRT identity.",
            "Occlusion re-entry was not observed; no re-entry claim is enabled.",
            "Unknown or multi-target scenarios remain fail-closed.",
        ],
    )
    return {
        "detector_config_ref": detector_config_ref,
        "visual_quality_profile": visual_quality_profile,
        "detector_config": detector_config,
    }


def _build_reviewed_dynamic_clicking_producer() -> dict:
    """Bind the accepted Pasu split to the reviewed round detector."""
    from kovaak_tracker.visual_signals import (
        VISUAL_PRODUCER_ID,
        VISUAL_PRODUCER_VERSION,
        build_visual_quality_profile_v2,
        visual_detector_config_ref_v1,
    )

    detector_config = {
        "schema_version": "visual_target_detector.v2",
        "aim_point_mode": "fixed_viewport_center",
        "excluded_regions": [
            [0.0, 0.0, 0.14, 0.08],
            [0.44, 0.0, 0.56, 0.12],
            [0.85, 0.08, 1.0, 0.17],
            [0.385, 0.765, 0.615, 1.0],
        ],
        "target": {
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 255, 130],
            "min_area": 100,
            "max_area_ratio": 0.02,
            "shape": "round",
        },
    }
    detector_config_ref = visual_detector_config_ref_v1(detector_config)
    visual_quality_profile = build_visual_quality_profile_v2(
        producer_id=VISUAL_PRODUCER_ID,
        producer_version=VISUAL_PRODUCER_VERSION,
        annotation_set_ref=(
            "annotation-set:pasu-small-reload@f7d74af3-72ed691b"
        ),
        annotation_protocol_version="visual_annotation_protocol.v2",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": detector_config_ref,
            "hud_mask_version": "visual_hud_mask.pasu_small_reload.v1",
            "annotated_map_or_background_labels": ["thecube-light-arena"],
            "annotated_target_appearance_labels": ["black-round-moving-target"],
        },
        validated_selectors=[{
            "schema_version": "visual_runtime_selector.v1",
            "scenario_hash": _REVIEWED_DYNAMIC_SCENARIO_HASH,
            "resolution": [1920, 1080],
            "canonical_video_mapping_version": "visual_video_time_mapping.v1",
            "fov": 103.0,
        }],
        required_selector_keys_by_metric_family={
            "dynamic_clicking": [
                "scenario_hash",
                "resolution",
                "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "dynamic_clicking": [
                "center_error_median_px",
                "center_error_p95_px",
                "false_positive_rate",
                "minimum_coverage",
                "radius_or_hitbox_error_px",
            ],
        },
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds={
            "center_error_median_px": 2.0,
            "center_error_p95_px": 4.0,
            "radius_or_hitbox_error_px": 2.0,
            "false_positive_rate": 0.05,
            "identity_switch_rate": 0.01,
            "occlusion_reentry_accuracy": 0.95,
            "minimum_coverage": 0.90,
        },
        validation_results={
            "center_error_median_px": 1.032295,
            "center_error_p95_px": 3.519083,
            "radius_or_hitbox_error_px": 0.749257,
            "false_positive_rate": 0.0,
            "identity_switch_rate": None,
            "occlusion_reentry_accuracy": None,
            "minimum_coverage": 0.992,
        },
        validated_metric_families=["dynamic_clicking"],
        status="accepted",
        limitations=[
            "exact_scenario_hash_resolution_and_video_mapping_only",
            "reticle_merged_and_transition_frames_explicitly_excluded",
            "holdout_small_target_area_99_below_min_area_100",
            "identity_continuity_not_observed",
            "occlusion_reentry_not_observed",
            "outcome_association_not_production_registered",
        ],
    )
    return {
        "detector_config_ref": detector_config_ref,
        "visual_quality_profile": visual_quality_profile,
        "detector_config": detector_config,
    }


def _build_reviewed_target_switching_producer() -> dict:
    """Bind the accepted beanTS split to the event-local episode adapter."""
    from kovaak_tracker.visual_signals import (
        VISUAL_TARGET_EPISODE_PRODUCER_ID,
        VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        build_visual_quality_profile_v2,
        visual_detector_config_ref_v1,
    )

    detector_config = {
        "schema_version": "visual_target_detector.v2",
        "aim_point_mode": "fixed_viewport_center",
        "excluded_regions": [
            [0.505, 0.045, 0.515, 0.060],
            [0.470, 0.960, 0.485, 0.980],
        ],
        "target": {
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 255, 80],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    detector_config_ref = visual_detector_config_ref_v1(detector_config)
    visual_quality_profile = build_visual_quality_profile_v2(
        producer_id=VISUAL_TARGET_EPISODE_PRODUCER_ID,
        producer_version=VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        annotation_set_ref=(
            "annotation-set:beants-larger-switching@0bc0a1ba-169851f6"
        ),
        annotation_protocol_version="visual_annotation_protocol.v2",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": detector_config_ref,
            "hud_mask_version": "visual_hud_mask.beants_larger.v1",
            "annotated_map_or_background_labels": ["thecube-light-arena"],
            "annotated_target_appearance_labels": [
                "black-round-target-with-healthbar",
            ],
        },
        validated_selectors=[{
            "schema_version": "visual_runtime_selector.v1",
            "scenario_hash": _REVIEWED_SWITCHING_SCENARIO_HASH,
            "resolution": [1920, 1080],
            "canonical_video_mapping_version": "visual_video_time_mapping.v1",
            "fov": None,
        }],
        required_selector_keys_by_metric_family={
            "switching": [
                "scenario_hash",
                "resolution",
                "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "switching": ["false_positive_rate", "minimum_coverage"],
        },
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds={
            "center_error_median_px": 4.0,
            "center_error_p95_px": 7.0,
            "radius_or_hitbox_error_px": 4.0,
            "false_positive_rate": 0.0,
            "identity_switch_rate": 0.01,
            "occlusion_reentry_accuracy": 0.95,
            "minimum_coverage": 0.6597938144329897,
        },
        validation_results={
            "center_error_median_px": None,
            "center_error_p95_px": None,
            "radius_or_hitbox_error_px": None,
            "false_positive_rate": 0.0,
            "identity_switch_rate": None,
            "occlusion_reentry_accuracy": None,
            "minimum_coverage": 0.6597938144329897,
        },
        validated_metric_families=["switching"],
        status="accepted",
        limitations=[
            "exact_scenario_hash_resolution_and_video_mapping_only",
            "stats_kill_boundaries_required",
            "local_ambiguity_rejects_only_affected_kill_chain",
            "selection_first_shot_first_damage_identity_and_reentry_unavailable",
            "outcome_association_optional_and_unavailable",
        ],
    )
    return {
        "detector_config_ref": detector_config_ref,
        "visual_quality_profile": visual_quality_profile,
        "detector_config": detector_config,
    }


# Runtime selector facts come from frozen Run facts and the local decoder; they
# are never copied from the profile being evaluated.
_REVIEWED_VISUAL_PRODUCERS: dict[str, dict] = {
    _REVIEWED_TRACKING_SCENARIO_PROFILE_REF: (
        _build_reviewed_single_target_tracking_producer()
    ),
    _REVIEWED_DYNAMIC_SCENARIO_PROFILE_REF: (
        _build_reviewed_dynamic_clicking_producer()
    ),
    _REVIEWED_SWITCHING_SCENARIO_PROFILE_REF: (
        _build_reviewed_target_switching_producer()
    ),
}


def _resolve_reviewed_visual_producer(job: dict) -> dict:
    from kovaak_tracker.visual_signals import (
        VISUAL_PRODUCER_ID,
        VISUAL_PRODUCER_VERSION,
        VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
        VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
        VISUAL_TARGET_EPISODE_PRODUCER_ID,
        VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        VISUAL_TEMPORAL_PRODUCER_ID,
        VISUAL_TEMPORAL_PRODUCER_VERSION,
        visual_detector_config_ref_v1,
    )

    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("visual input snapshot is unavailable")
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping):
        raise ValueError("visual scenario profile is unavailable")
    profile_ref = resolution.get("scenario_profile_ref")
    if not isinstance(profile_ref, str) or not profile_ref:
        raise ValueError("visual scenario profile is unavailable")
    producer = _REVIEWED_VISUAL_PRODUCERS.get(profile_ref)
    if not isinstance(producer, Mapping) or set(producer) != {
        "detector_config_ref", "visual_quality_profile", "detector_config",
    }:
        raise ValueError("visual quality profile is unavailable")
    detector_config_ref = producer["detector_config_ref"]
    quality_profile = producer["visual_quality_profile"]
    calibration_context = (
        quality_profile.get("calibration_context")
        if isinstance(quality_profile, Mapping)
        else None
    )
    if (
        not isinstance(detector_config_ref, str)
        or not detector_config_ref
        or not isinstance(quality_profile, Mapping)
        or quality_profile.get("status") not in {"accepted", "limited"}
        or (
            quality_profile.get("producer_id"),
            quality_profile.get("producer_version"),
        ) not in {
            (VISUAL_PRODUCER_ID, VISUAL_PRODUCER_VERSION),
            (
                VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
                VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
            ),
            (
                VISUAL_TARGET_EPISODE_PRODUCER_ID,
                VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
            ),
            (VISUAL_TEMPORAL_PRODUCER_ID, VISUAL_TEMPORAL_PRODUCER_VERSION),
        }
        or not isinstance(calibration_context, Mapping)
        or calibration_context.get("detector_config_ref") != detector_config_ref
        or visual_detector_config_ref_v1(producer["detector_config"])
        != detector_config_ref
    ):
        raise ValueError("visual quality profile is unavailable")
    return dict(producer)


def _run_owned_visual_video_time_mapping(job: dict) -> dict:
    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("visual input snapshot is unavailable")
    if snapshot.get("schema_version") != "analysis_input_snapshot.v3":
        raise ValueError("run-owned visual video is unavailable")
    run_id = job.get("kovaak_run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("run-owned visual video is unavailable")
    if snapshot.get("run_id") != run_id:
        raise ValueError("run-owned visual video is unavailable")
    window = snapshot.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise ValueError("visual canonical window is unavailable")
    sources = snapshot.get("sources")
    video = sources.get("video") if isinstance(sources, Mapping) else None
    if not isinstance(video, Mapping):
        raise ValueError("run-owned visual video is unavailable")
    expected_ref_prefix = f"run:{run_id}:video:"
    if (
        video.get("ownership") != "run"
        or video.get("availability") != "available"
        or video.get("format_version") != "mp4"
        or not isinstance(video.get("artifact_ref"), str)
        or not video["artifact_ref"].startswith(expected_ref_prefix)
    ):
        raise ValueError("run-owned visual video is unavailable")
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    timebase_version = window.get("timebase_version")
    if (
        isinstance(start_ms, bool)
        or not isinstance(start_ms, int)
        or isinstance(end_ms, bool)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
        or not isinstance(timebase_version, str)
        or not timebase_version
    ):
        raise ValueError("visual canonical window is unavailable")
    return {
        "schema_version": "visual_video_time_mapping.v1",
        "source_pts_origin_ms": 0.0,
        "canonical_origin_ms": start_ms,
        "mapping_method": "run_owned_exact_canonical_clip",
        "timebase_version": timebase_version,
    }


def _visual_runtime_selector(
    job: dict,
    *,
    parsed_stats,
    video_time_mapping: Mapping[str, object],
) -> dict:
    snapshot = job.get("input_snapshot")
    scenario_resolution = (
        snapshot.get("scenario_resolution")
        if isinstance(snapshot, Mapping)
        else None
    )
    scenario_resolution = validate_scenario_resolution_v1(scenario_resolution)
    scenario_hash = scenario_resolution.get("scenario_hash")
    if not isinstance(scenario_hash, str) or not scenario_hash:
        raise ValueError("visual scenario hash is unavailable")
    stats_resolution = getattr(parsed_stats, "resolution", None)
    match = (
        re.fullmatch(r"\s*([1-9][0-9]*)\s*[xX]\s*([1-9][0-9]*)\s*", stats_resolution)
        if isinstance(stats_resolution, str)
        else None
    )
    if match is None:
        raise ValueError("visual resolution is unavailable")
    try:
        raw_fov = getattr(parsed_stats, "fov", None)
        fov = float(raw_fov) if raw_fov is not None else None
        if fov is not None and (not math.isfinite(fov) or fov <= 0):
            fov = None
    except (AttributeError, KeyError, TypeError, ValueError):
        fov = None
    return {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": scenario_hash,
        "resolution": [int(match.group(1)), int(match.group(2))],
        "canonical_video_mapping_version": video_time_mapping["schema_version"],
        "fov": fov,
    }


def run_visual_preprocessing(job: dict, *, parsed_stats=None) -> dict:
    """Resolve a reviewed visual producer without guessing a detector profile."""
    from kovaak_tracker.visual_signals import (
        VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
        VISUAL_TEMPORAL_PRODUCER_ID,
        VisualPreprocessingUnavailable,
        preprocess_visual_video_single_target_csrt_v1,
        preprocess_visual_video_temporal_v1,
        preprocess_visual_video_v1,
    )

    try:
        producer = _resolve_reviewed_visual_producer(job)
        video_time_mapping = _run_owned_visual_video_time_mapping(job)
        visual_runtime_selector = _visual_runtime_selector(
            job,
            parsed_stats=parsed_stats,
            video_time_mapping=video_time_mapping,
        )
        _assert_managed_video_matches_snapshot(job, "multimodal")
        snapshot = job["input_snapshot"]
        producer_id = producer["visual_quality_profile"]["producer_id"]
        if producer_id == VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID:
            preprocessor = preprocess_visual_video_single_target_csrt_v1
        elif producer_id == VISUAL_TEMPORAL_PRODUCER_ID:
            preprocessor = preprocess_visual_video_temporal_v1
        else:
            preprocessor = preprocess_visual_video_v1
        return preprocessor(
            media_path=str(job["video_path"]),
            analysis_ref=f"analysis:{job['id']}",
            canonical_time_window=snapshot["canonical_time_window"],
            visual_quality_profile=producer["visual_quality_profile"],
            visual_runtime_selector=visual_runtime_selector,
            video_time_mapping=video_time_mapping,
            detector_config=producer["detector_config"],
            source_ref=snapshot["sources"]["video"]["artifact_ref"],
        )
    except VisualPreprocessingUnavailable:
        raise
    except SourceSnapshotChangedError:
        raise
    except (KeyError, TypeError, ValueError):
        raise VisualPreprocessingUnavailable("visual_quality_profile_unavailable") from None


async def _stop_visual_worker_process(process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(
            process.wait(), timeout=VISUAL_WORKER_SHUTDOWN_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


class ContinuousTrackingAnalysisProcessError(RuntimeError):
    """The isolated Tracking postprocessor failed after visual preprocessing."""

    def __init__(self, code: str, visual_result: dict) -> None:
        super().__init__(code)
        self.code = code
        self.visual_result = visual_result


async def _run_isolated_analysis_request(payload: dict) -> dict:
    from .visual_worker_process import build_child_environment
    from kovaak_tracker.visual_signals import VisualPreprocessingUnavailable

    request = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "webapp.backend.visual_worker_process",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,
        env=build_child_environment(),
    )
    try:
        stdout, _ = await process.communicate(request)
    except asyncio.CancelledError:
        await _stop_visual_worker_process(process)
        raise
    if process.returncode != 0 or len(stdout) > VISUAL_WORKER_RESPONSE_LIMIT_BYTES:
        raise RuntimeError("visual_preprocessing_failed")
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("visual_preprocessing_failed") from error
    if not isinstance(response, dict) or response.get("ok") is not True:
        failure = response.get("error") if isinstance(response, dict) else None
        kind = failure.get("kind") if isinstance(failure, dict) else None
        code = failure.get("code") if isinstance(failure, dict) else None
        if kind == "source_snapshot_changed" and isinstance(code, str):
            raise SourceSnapshotChangedError(code)
        if kind == "visual_preprocessing_unavailable" and isinstance(code, str):
            raise VisualPreprocessingUnavailable(code)
        if kind == "family_analysis_failed" and isinstance(code, str):
            visual_result = response.get("visual_result")
            if isinstance(visual_result, dict):
                raise ContinuousTrackingAnalysisProcessError(code, visual_result)
        raise RuntimeError("visual_preprocessing_failed")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("visual_preprocessing_failed")
    return result


async def _run_visual_worker_request(
    job: dict,
    *,
    postprocess: str | None = None,
) -> dict:
    child_job = {
        field: job[field]
        for field in VISUAL_WORKER_JOB_FIELDS
        if field in job
    }
    payload = {"job": child_job}
    if postprocess is not None:
        payload["postprocess"] = postprocess
    return await _run_isolated_analysis_request(payload)


async def run_visual_preprocessing_isolated(job: dict) -> dict:
    """Run reviewed CV outside the API/heartbeat process without changing output."""
    return await _run_visual_worker_request(job)


async def run_continuous_tracking_pipeline_isolated(job: dict) -> tuple[dict, dict | None]:
    """Keep reviewed Tracking CV and its numeric postprocessor in one child process."""
    result = await _run_visual_worker_request(job, postprocess="continuous_tracking")
    if set(result) != {"visual_result", "family_result"}:
        raise RuntimeError("visual_preprocessing_failed")
    visual_result = result["visual_result"]
    family_result = result["family_result"]
    if not isinstance(visual_result, dict) or (
        family_result is not None and not isinstance(family_result, dict)
    ):
        raise RuntimeError("visual_preprocessing_failed")
    return visual_result, family_result


async def run_target_switching_pipeline_isolated(job: dict) -> tuple[dict, dict]:
    """Keep reviewed Switching CV and local episode projection in one child."""
    result = await _run_visual_worker_request(job, postprocess="target_switching")
    if set(result) != {"visual_result", "family_result"}:
        raise RuntimeError("visual_preprocessing_failed")
    visual_result = result["visual_result"]
    episode_result = result["family_result"]
    if not isinstance(visual_result, dict) or not isinstance(episode_result, dict):
        raise RuntimeError("visual_preprocessing_failed")
    return visual_result, episode_result


async def commit_continuous_tracking_evidence_isolated(
    job: dict,
    result: dict,
    visual_result: dict,
    tracking_result: dict,
) -> dict:
    """Build and commit Tracking evidence without occupying the desktop runtime."""
    child_job = {
        field: job[field]
        for field in VISUAL_WORKER_EVIDENCE_JOB_FIELDS
        if field in job
    }
    return await _run_isolated_analysis_request({
        "operation": "commit_continuous_tracking_evidence",
        "job": child_job,
        "result": result,
        "visual_result": visual_result,
        "tracking_result": tracking_result,
    })


def _unavailable_visual_summary(limitation: str) -> dict:
    return {
        "schema_version": "visual_signal_summary.v1",
        "status": "unavailable",
        "quality_status": "unavailable",
        "producer_version": None,
        "enabled_metric_families": [],
        "track_count": 0,
        "observation_count": 0,
        "target_coverage": None,
        "crosshair_coverage": None,
        "completeness": "unavailable",
        "event_counts": {},
        "limitations": [limitation],
    }


def _mark_visual_artifact_unavailable(result: dict) -> dict:
    """Remove an uncommitted visual claim while retaining native/outcome facts."""
    updated = dict(result)
    deterministic = dict(updated.get("deterministic") or {})
    deterministic["visual_validation"] = _unavailable_visual_summary(
        "visual_artifact_commit_failed"
    )
    limitations = list(deterministic.get("limitations") or [])
    if "visual_artifact_commit_failed" not in limitations:
        limitations.append("visual_artifact_commit_failed")
    deterministic["limitations"] = limitations
    updated["deterministic"] = deterministic
    warnings = list(updated.get("warnings") or [])
    warning = {"code": "visual_artifact_commit_failed"}
    if warning not in warnings:
        warnings.append(warning)
    updated["warnings"] = warnings
    return updated


def _artifact_with_visual_commit_limitation(artifact: dict) -> dict:
    fallback = copy.deepcopy(artifact)
    limitations = list(fallback.get("limitations") or [])
    if "visual_artifact_commit_failed" not in limitations:
        limitations.append("visual_artifact_commit_failed")
    fallback["limitations"] = limitations
    return fallback


def _downgrade_dynamic_evidence_result(job: dict, result: dict) -> dict:
    return _build_outcome_only_result_v2(
        job,
        created_at=str(result["created_at"]),
        completed_at=str(result["completed_at"]),
        limitations_override=["dynamic_clicking_evidence_artifact_unavailable"],
        visual_validation=_unavailable_visual_summary(
            "dynamic_clicking_evidence_artifact_unavailable"
        ),
        extra_warnings=[{"code": "visual_artifact_commit_failed"}],
        analysis_type_override="dynamic_clicking",
    )


def _downgrade_tracking_evidence_result(job: dict, result: dict) -> dict:
    return _build_outcome_only_result_v2(
        job,
        created_at=str(result["created_at"]),
        completed_at=str(result["completed_at"]),
        limitations_override=["continuous_tracking_evidence_artifact_unavailable"],
        visual_validation=_unavailable_visual_summary(
            "continuous_tracking_evidence_artifact_unavailable"
        ),
        extra_warnings=[{"code": "visual_artifact_commit_failed"}],
        analysis_type_override="continuous_tracking",
    )


def _downgrade_switching_evidence_result(job: dict, result: dict) -> dict:
    return _build_outcome_only_result_v2(
        job,
        created_at=str(result["created_at"]),
        completed_at=str(result["completed_at"]),
        limitations_override=["target_switching_evidence_artifact_unavailable"],
        visual_validation=_unavailable_visual_summary(
            "target_switching_evidence_artifact_unavailable"
        ),
        extra_warnings=[{"code": "visual_artifact_commit_failed"}],
        analysis_type_override="target_switching",
    )


def _maybe_commit_analysis_evidence(
    job: dict,
    result: dict,
    *,
    parsed_stats=None,
    native_result: dict | None = None,
    visual_result: dict | None = None,
    dynamic_result: dict | None = None,
    tracking_result: dict | None = None,
    switching_result: dict | None = None,
    outcome_event_bundle: dict | None = None,
) -> dict:
    """Best-effort local L1/L2 projection before the terminal result commit.

    A legacy video-only request or a Run without a canonical window remains
    readable without this derived artifact.  Once a frozen Stats/Performance
    pair and window exist, failure is fail-closed so a partial artifact cannot
    be advertised as complete.
    """
    snapshot = job.get("input_snapshot") or {}
    window = snapshot.get("canonical_time_window")
    if not isinstance(window, dict):
        return result
    analysis_id = f"analysis:{job['id']}"
    sources = snapshot.get("sources") or {}
    stats_source = sources.get("stats") if isinstance(sources, dict) else None
    performance_source = sources.get("performance") if isinstance(sources, dict) else None
    if not isinstance(stats_source, dict) or not isinstance(performance_source, dict):
        return result
    try:
        from kovaak_tracker.analysis_evidence import (
            build_analysis_evidence_artifact_v1,
            build_processed_event_table_catalog,
            validate_analysis_evidence_artifact_v2,
        )
        from kovaak_tracker.csv_parser import parse_stats_bytes
        from kovaak_tracker.performance_parser import parse_performance_bytes
        from . import evidence_store

        if parsed_stats is None:
            parsed_stats = parse_stats_bytes(
                _read_frozen_source_bytes("stats", stats_source),
                file_name=str(stats_source.get("basename") or "stats.csv"),
            )
        performance = parse_performance_bytes(
            _read_frozen_source_bytes("performance", performance_source),
        )
        artifact = build_analysis_evidence_artifact_v1(
            analysis_ref=analysis_id,
            canonical_time_window=window,
            scenario_profile_ref=(snapshot.get("scenario_resolution") or {}).get("scenario_profile_ref"),
            stats=parsed_stats,
            performance=performance,
            stats_source_ref=stats_source.get("artifact_ref"),
            performance_source_ref=performance_source.get("artifact_ref"),
            stats_parser_version=str(stats_source.get("parser_version") or "kovaak_stats.v1"),
            performance_parser_version=str(performance_source.get("parser_version") or "kovaak_performance.v1"),
        )
        resolution = snapshot.get("scenario_resolution")
        active_static = (
            isinstance(resolution, dict)
            and resolution.get("aim_family") == "static_clicking"
            and result.get("analysis_version") == NATIVE_ANALYSIS_VERSION
        )
        if active_static:
            from kovaak_tracker.native_flicking_analysis import (
                build_native_static_evidence_extension,
            )

            adapter_input = native_result or {
                "deterministic": result.get("deterministic") or {},
                "evidence": result.get("evidence") or {},
            }
            artifact = build_native_static_evidence_extension(
                artifact,
                adapter_input,
                raw_source_ref=(snapshot.get("trace") or {}).get("artifact_ref"),
                scenario_profile_ref=resolution.get("scenario_profile_ref"),
            )
            result = _bind_static_evidence_to_diagnosis(result, artifact, analysis_id)
        base_artifact = copy.deepcopy(artifact)
    except (SourceSnapshotChangedError, ValueError, OSError) as error:
        log.warning(
            "analysis evidence projection unavailable session=%s error=%s",
            job.get("id"),
            type(error).__name__,
        )
        if dynamic_result is not None:
            return _downgrade_dynamic_evidence_result(job, result)
        if tracking_result is not None:
            return _downgrade_tracking_evidence_result(job, result)
        if switching_result is not None:
            return _downgrade_switching_evidence_result(job, result)
        return _mark_visual_artifact_unavailable(result) if visual_result is not None else result
    if visual_result is not None:
        try:
            from kovaak_tracker.visual_signals import (
                extend_analysis_evidence_with_visual_signals_v1,
            )

            artifact = extend_analysis_evidence_with_visual_signals_v1(
                base_artifact,
                visual_result,
            )
            if outcome_event_bundle is not None:
                artifact = copy.deepcopy(artifact)
                artifact["schema_version"] = "analysis_evidence_artifact.v2"
                artifact["event_bundles"].append(copy.deepcopy(outcome_event_bundle))
                artifact = validate_analysis_evidence_artifact_v2(artifact)
            if dynamic_result is not None:
                from kovaak_tracker.dynamic_clicking_analysis import (
                    extend_analysis_evidence_with_dynamic_clicking_v1,
                )

                artifact = extend_analysis_evidence_with_dynamic_clicking_v1(
                    artifact,
                    dynamic_result,
                )
            if tracking_result is not None:
                from kovaak_tracker.tracking_analysis import (
                    extend_analysis_evidence_with_continuous_tracking_v1,
                )

                artifact = extend_analysis_evidence_with_continuous_tracking_v1(
                    artifact,
                    tracking_result,
                )
            if switching_result is not None:
                from kovaak_tracker.target_switching_analysis import (
                    extend_analysis_evidence_with_target_switching_v1,
                )

                artifact = extend_analysis_evidence_with_target_switching_v1(
                    artifact,
                    switching_result,
                )
            processed_event_tables = build_processed_event_table_catalog(artifact)
            safe_ref = evidence_store.write_analysis_evidence_artifact(
                session_id=int(job["id"]),
                owner_id=str(job["user_id"]),
                artifact=artifact,
            )
        except (ValueError, OSError) as error:
            log.warning(
                "visual evidence artifact unavailable session=%s error=%s",
                job.get("id"),
                type(error).__name__,
            )
            result = (
                _downgrade_dynamic_evidence_result(job, result)
                if dynamic_result is not None
                else _mark_visual_artifact_unavailable(result)
            )
            if tracking_result is not None:
                result = _downgrade_tracking_evidence_result(job, result)
            if switching_result is not None:
                result = _downgrade_switching_evidence_result(job, result)
            artifact = _artifact_with_visual_commit_limitation(base_artifact)
            try:
                processed_event_tables = build_processed_event_table_catalog(artifact)
                safe_ref = evidence_store.write_analysis_evidence_artifact(
                    session_id=int(job["id"]),
                    owner_id=str(job["user_id"]),
                    artifact=artifact,
                )
            except (SourceSnapshotChangedError, ValueError, OSError) as fallback_error:
                log.warning(
                    "analysis evidence fallback unavailable session=%s error=%s",
                    job.get("id"),
                    type(fallback_error).__name__,
                )
                return result
    else:
        artifact = base_artifact
        try:
            processed_event_tables = build_processed_event_table_catalog(artifact)
            safe_ref = evidence_store.write_analysis_evidence_artifact(
                session_id=int(job["id"]),
                owner_id=str(job["user_id"]),
                artifact=artifact,
            )
        except (SourceSnapshotChangedError, ValueError, OSError) as error:
            log.warning(
                "analysis evidence projection unavailable session=%s error=%s",
                job.get("id"),
                type(error).__name__,
            )
            return result
    result = dict(result)
    result["evidence"] = {
        **(result.get("evidence") or {}),
        "derived_artifact": safe_ref,
        **(
            {"processed_event_tables": processed_event_tables}
            if processed_event_tables
            else {}
        ),
    }
    manifest = dict(result.get("artifact_manifest") or {})
    owned_outputs = list(manifest.get("owned_outputs") or [])
    external_ids = {
        entry.get("id")
        for entry in list(manifest.get("external_inputs") or [])
        if isinstance(entry, dict)
    }
    owned_outputs.append(
        evidence_store.analysis_evidence_manifest_entry(
            safe_ref,
            derived_from=[
                ref for ref in (stats_source.get("artifact_ref"), performance_source.get("artifact_ref"))
                if ref in external_ids
            ],
        )
    )
    result["artifact_manifest"] = {**manifest, "owned_outputs": owned_outputs}
    return result


def _build_profile_contribution_payload(result: Mapping[str, object]) -> dict | None:
    """Compatibility wrapper around the profile store's canonical projector."""
    from .aiming_profile_store import build_contribution_from_analysis_result

    return build_contribution_from_analysis_result(result)


async def _record_profile_contribution(job: Mapping[str, object], result: Mapping[str, object]) -> None:
    payload = _build_profile_contribution_payload(result)
    if payload is None:
        return
    from . import aiming_profile_store

    await aiming_profile_store.record_deterministic_contribution(
        str(job["user_id"]),
        f"analysis:{job['id']}",
        payload,
    )


def _bind_static_evidence_to_diagnosis(
    result: dict,
    artifact: dict,
    analysis_ref: str,
) -> dict:
    """Attach analysis-scoped segment refs without changing native findings."""
    deterministic = dict(result.get("deterministic") or {})
    diagnosis = dict(deterministic.get("diagnosis") or {})
    issues = []
    segments = list(artifact.get("evidence_segments") or [])
    events = {
        event["event_id"]: event
        for bundle in artifact.get("event_bundles") or []
        for event in bundle.get("events") or []
    }
    legacy_events = {
        event.get("attributes", {}).get("legacy_event_ref"): event_id
        for event_id, event in events.items()
        if isinstance(event.get("attributes", {}).get("legacy_event_ref"), str)
    }
    rank_order = {"worst": 0, "typical": 1, "improved": 2}
    for index, issue in enumerate(diagnosis.get("issues") or [], 1):
        if not isinstance(issue, dict):
            continue
        issue = dict(issue)
        event_refs = list(issue.get("event_refs") or [])
        mapped_event_refs = list(dict.fromkeys(
            legacy_events.get(ref, ref)
            for ref in event_refs
            if isinstance(ref, str)
        ))
        mapped_events = set(mapped_event_refs)
        if mapped_event_refs:
            issue["event_refs"] = mapped_event_refs
        metric_refs = {
            ref if ref.startswith("static_clicking.") else f"static_clicking.{ref}"
            for ref in issue.get("metric_refs") or []
            if isinstance(ref, str)
        }
        matching = [
            segment for segment in segments
            if mapped_events.intersection(segment.get("event_refs") or [])
            or any(
                any(metric_ref in ref for metric_ref in metric_refs)
                for ref in segment.get("metric_refs") or []
            )
        ]
        matching.sort(key=lambda segment: rank_order.get(segment.get("rank_reason"), 99))
        if matching:
            primary = next(
                (segment for segment in matching if segment.get("rank_reason") == "worst"),
                matching[0],
            )
            supporting = [
                segment["segment_id"]
                for segment in matching
                if segment["segment_id"] != primary["segment_id"]
            ][:2]
            issue["primary_evidence_segment_ref"] = primary["segment_id"]
            issue["supporting_evidence_segment_refs"] = supporting
            issue_ref = f"{analysis_ref}:issue:{index}"
            primary["issue_refs"] = list(dict.fromkeys([*primary.get("issue_refs", []), issue_ref]))
            for segment in matching:
                if segment["segment_id"] in supporting:
                    segment["issue_refs"] = list(dict.fromkeys([*segment.get("issue_refs", []), issue_ref]))
        issues.append(issue)
    if issues:
        diagnosis["issues"] = issues
        deterministic["diagnosis"] = diagnosis
        result = dict(result)
        result["deterministic"] = deterministic
    return result


class SourceSnapshotChangedError(ValueError):
    """Frozen Analysis source is missing, unidentified, or no longer the same revision."""


def _read_frozen_source_bytes(kind: str, source: object) -> bytes:
    if not isinstance(source, dict):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, dict) or any(
        fingerprint.get(field) is None for field in ("sha256", "size", "mtime_ns")
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    expected = {
        "sha256": fingerprint["sha256"],
        "size": fingerprint["size"],
        "mtime_ns": fingerprint["mtime_ns"],
    }
    if (
        not isinstance(expected["sha256"], str)
        or isinstance(expected["size"], bool)
        or not isinstance(expected["size"], int)
        or expected["size"] < 0
        or isinstance(expected["mtime_ns"], bool)
        or not isinstance(expected["mtime_ns"], int)
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    path = source.get("path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} path missing")
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(expected["size"] + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} file missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} changed while reading"
        )
    actual = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mtime_ns": after.st_mtime_ns,
    }
    if actual != expected:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} revision changed")
    return data


def _managed_video_contract(job: dict, input_mode: str) -> tuple[str, str, int] | None:
    if input_mode not in {"multimodal", "video_fallback"}:
        return None
    if job.get("kovaak_run_id") is None:
        return None
    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    video = sources.get("video")
    if not isinstance(video, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    if "fingerprint" not in video:
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    fingerprint = video.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if (
        not isinstance(expected_sha, str)
        or not expected_sha
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    path = job.get("video_path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError("source_unavailable: managed video missing")
    return path, expected_sha, expected_size


def _assert_managed_video_matches_snapshot(job: dict, input_mode: str) -> None:
    contract = _managed_video_contract(job, input_mode)
    if contract is None:
        return
    path, expected_sha, expected_size = contract
    digest = hashlib.sha256()
    observed_size = 0
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != expected_size:
                raise SourceSnapshotChangedError(
                    "source_unavailable: managed video revision changed"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                observed_size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except SourceSnapshotChangedError:
        raise
    except OSError as exc:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video changed while reading"
        )
    if observed_size != expected_size or digest.hexdigest() != expected_sha:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video revision changed"
        )


async def _heartbeat_loop(session_id: int, stop: asyncio.Event) -> None:
    """Renew lease until stop is set. First beat immediately, then every interval."""
    while True:
        try:
            await queue.heartbeat(session_id, WORKER_ID)
        except Exception:
            log.exception(
                "heartbeat failed session=%s worker=%s", session_id, WORKER_ID,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            continue


# --- 包装 kovaak_tracker(隔离 + 便于 mock)---

def run_analysis(
    video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
    *, stats=None, profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
) -> tuple[dict, dict]:
    """调 kovaak_tracker.analyze_flicking_fair_summary,返回 (summary, extras)。

    cm_per_360 / fov 优先用 caller 传的(用户填);若 None,从 CSV fallback:
      - fov:csv_parser stats.fov(KovaaK CSV 的 FOV 字段)
      - cm_per_360:csv_parser stats.cm_per_360(DPI + Horiz Sens + Sens Scale yaw 表)
    传给 analyze_flicking_fair_summary 影响 deg_per_px(fov) + peak_cm_per_s(cm/360)。
    """
    from kovaak_tracker.csv_parser import parse_stats_csv
    from kovaak_tracker.pan_tracker import analyze_flicking_fair_summary

    # CSV fallback(若 caller 没传):从 KovaaK CSV config 块读真实值
    if stats is None:
        stats = parse_stats_csv(csv_path)
    stats_values = {
        "cm_per_360": getattr(stats, "cm_per_360", None) if stats is not None else None,
        "fov": getattr(stats, "fov", None) if stats is not None else None,
    }
    manual_override = _manual_override_or_legacy(
        manual_override, cm_per_360=cm_per_360, fov=fov,
    )
    calibration = resolve_calibration_v1(
        stats=stats_values,
        manual_override=manual_override,
        profile_default=profile_default,
    )
    cm_per_360 = calibration["cm_per_360"]["value"]
    fov = calibration["fov"]["value"]

    summary, extras = analyze_flicking_fair_summary(
        video_path,
        csv_path,
        fov=fov,
        cm_per_360=cm_per_360,
        stats=stats,
        return_extras=True,
    )
    if isinstance(extras, dict):
        extras["calibration"] = calibration
    return summary, extras


def _build_timeline(extras: dict) -> list[dict]:
    """把 analyze_flicking_fair_summary 的 extras 转成 timeline events 列表。

    schema(routes.get_session_timeline 消费):
        {"frame": int, "time_s": float, "type": str, "label": str}
    types: "kill" | "peak" | "corrective"。flicking pipeline 没有 miss 概念
    (那是 tracking 的事),所以这里不产 miss markers。
    """
    if not isinstance(extras, dict):
        return []
    fps = extras.get("fps") or 60
    if fps <= 0:
        fps = 60
    events: list[dict] = []

    def _add(frame: int, type_: str, label: str) -> None:
        if frame is None or frame < 0:
            return
        events.append({
            "frame": int(frame),
            "time_s": round(frame / fps, 3),
            "type": type_,
            "label": label,
        })

    for flick in extras.get("flicks") or []:
        peak_frame = flick.get("peak_frame")
        if peak_frame is not None:
            _add(peak_frame, "peak", "速度峰值")
    for frame in extras.get("corrective_frames") or []:
        _add(frame, "corrective", "修正")
    for frame in extras.get("kill_frames") or []:
        _add(frame, "kill", "击杀")

    # 按 frame 升序排,方便前端顺序渲染。
    events.sort(key=lambda e: e["frame"])
    return events


def run_report(summary: dict) -> dict:
    """Build the deterministic local report without invoking a Provider."""
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report
    report = build_report(summary, backend=None)
    d = asdict(report) if is_dataclass(report) else {"_raw": str(report)}
    # plotly Figure 不可 JSON 序列化 → 转 dict
    figures = d.get("figures")
    if isinstance(figures, dict):
        d["figures"] = {
            k: (f.to_dict() if hasattr(f, "to_dict") else f)
            for k, f in figures.items()
        }
    return d


def _sqlite_created_at_to_iso_z(created_at: str | None) -> str:
    """SQLite ``YYYY-MM-DD HH:MM:SS`` → ``YYYY-MM-DDTHH:MM:SSZ`` (UTC)."""
    if not created_at or not str(created_at).strip():
        return _utc_now_iso_z()
    s = str(created_at).strip()
    if "T" in s:
        return s if s.endswith("Z") else f"{s}Z"
    return s.replace(" ", "T") + "Z"


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _delete_video_safely(path) -> None:
    """失败路径清理临时文件(视频/CSV)。

    用户上传的视频/CSV 是可再生副本(源在用户本地),属 CLAUDE.md §5 例外
    (regenerable 临时文件可 hard remove 而非走 Recycle Bin);且 worker
    批量清理场景下 os.remove 比 SendToRecycleBin 快千倍。函数名沿用历史。
    """
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删临时文件失败 %s: %s", path, e)


def run_native_analysis(
    snapshot: dict,
    cm_per_360: float | None = None,
    fov: float | None = None,
    *,
    return_parsed_stats: bool = False,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
):
    """Load a frozen Run snapshot and invoke the native flicking adapter."""
    from .kovaak_run_store import decode_mouse_snapshot_bytes
    from kovaak_tracker.csv_parser import parse_stats_bytes
    from kovaak_tracker.native_flicking_analysis import analyze_native_flicking
    from kovaak_tracker.performance_parser import parse_performance_bytes

    sources = snapshot.get("sources") or {}
    trace = snapshot.get("trace") or {}
    stats_path = (sources.get("stats") or {}).get("path")
    performance_path = (sources.get("performance") or {}).get("path")
    trace_path = trace.get("path")
    if not isinstance(stats_path, str) or not isinstance(performance_path, str):
        raise ValueError("native analysis requires stats and performance sources")
    if not isinstance(trace_path, str):
        raise ValueError("native analysis requires a raw input trace")
    canonical_window = snapshot.get("canonical_time_window")
    if snapshot.get("schema_version") in {
        "analysis_input_snapshot.v2", "analysis_input_snapshot.v3",
    } and not isinstance(canonical_window, dict):
        raise ValueError("source_unavailable: canonical time window missing")

    stats_bytes = _read_frozen_source_bytes("stats", sources.get("stats"))
    performance_bytes = _read_frozen_source_bytes(
        "performance", sources.get("performance"),
    )
    trace_bytes = _read_frozen_source_bytes("raw_input", trace)
    parsed_stats = parse_stats_bytes(stats_bytes, file_name=Path(stats_path).name)
    manual_override = _manual_override_or_legacy(
        manual_override, cm_per_360=cm_per_360, fov=fov,
    )
    calibration = resolve_calibration_v1(
        stats={"cm_per_360": parsed_stats.cm_per_360, "fov": parsed_stats.fov},
        manual_override=manual_override,
        profile_default=profile_default,
    )
    stats = {
        "summary": dict(parsed_stats.summary),
        "config": dict(parsed_stats.config),
        "scenario": parsed_stats.scenario,
        "cm_per_360": calibration["cm_per_360"]["value"],
        "fov": calibration["fov"]["value"],
        "calibration": calibration,
        "kill_count": int(len(parsed_stats.kills.index))
        if hasattr(parsed_stats, "kills")
        else None,
        "weapon_aggregates": list(
            getattr(parsed_stats, "weapon_aggregates", ()) or ()
        ),
        "field_presence": dict(
            getattr(parsed_stats, "field_presence", {}) or {}
        ),
    }
    trace_points = decode_mouse_snapshot_bytes(trace_bytes)
    performance = parse_performance_bytes(performance_bytes)
    result = analyze_native_flicking(
        trace_points,
        performance,
        stats=stats,
        canonical_window=canonical_window,
    )
    if isinstance(result, dict):
        result["calibration"] = calibration
    return (result, parsed_stats) if return_parsed_stats else result


def _manual_override_or_legacy(
    manual_override: Mapping[str, object] | None,
    *,
    cm_per_360: float | None,
    fov: float | None,
) -> Mapping[str, object] | None:
    """Treat pre-contract flat values as manual input only when override is absent."""
    if isinstance(manual_override, Mapping):
        return manual_override
    if cm_per_360 is None and fov is None:
        return None
    return {"cm_per_360": cm_per_360, "fov": fov}


def _freeze_job_calibration(job: dict, parsed_stats: object) -> dict:
    request = job.get("calibration_request")
    manual = request.get("manual_override") if isinstance(request, Mapping) else None
    profile = request.get("profile_default") if isinstance(request, Mapping) else None
    manual = _manual_override_or_legacy(
        manual,
        cm_per_360=job.get("cm_per_360"),
        fov=job.get("fov"),
    )
    calibration = resolve_calibration_v1(
        stats={
            "cm_per_360": getattr(parsed_stats, "cm_per_360", None),
            "fov": getattr(parsed_stats, "fov", None),
        },
        manual_override=manual,
        profile_default=profile,
    )
    snapshot = job.get("input_snapshot")
    if isinstance(snapshot, dict):
        snapshot["calibration"] = calibration
    job["calibration_snapshot"] = calibration
    return calibration


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
        _read_frozen_source_bytes("raw_input", trace),
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
    trace_bytes = _read_frozen_source_bytes("raw_input", trace)
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


def _native_deterministic_v2(
    native_result: dict,
    *,
    input_mode: str | None = None,
) -> dict:
    """Adapt the native payload to v2's path-safe public contract."""
    deterministic = native_result.get("deterministic") or {}
    # Preserve native metric keys and envelopes; v2 path safety rejects path
    # fields, not metric names such as path_length or path_efficiency.
    metrics: dict[str, object] = {}
    for key, value in (deterministic.get("metrics") or {}).items():
        if not isinstance(value, dict):
            metrics[key] = value
            continue
        metric = dict(value)
        metric.setdefault("classification", "deterministic")
        metrics[key] = metric
    quality = _native_quality_projection(native_result)
    _project_metric_quality(metrics, quality)
    trajectory = deterministic.get("trajectory") or {}
    public_trajectory = {
        "unit": trajectory.get("unit", "raw_counts"),
        "point_count": int(trajectory.get("point_count") or 0),
    }
    diagnosis = _native_diagnosis(
        metrics,
        input_mode=input_mode or native_result.get("input_mode") or "input_native",
        quality=quality,
    )
    return {
        "status": native_result.get("status", "unavailable"),
        "summary": dict(diagnosis.get("summary") or {}),
        "trajectory": public_trajectory,
        "metrics": metrics,
        "timeline": list(deterministic.get("timeline") or []),
        "diagnosis": diagnosis,
        "figures": {},
        "limitations": quality["limitations"],
    }


def _native_quality_projection(native_result: Mapping[str, object]) -> dict:
    evidence = native_result.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    alignment = evidence.get("alignment")
    alignment = alignment if isinstance(alignment, Mapping) else {}
    coverage = evidence.get("coverage")
    coverage = (
        float(coverage)
        if isinstance(coverage, (int, float))
        and not isinstance(coverage, bool)
        and math.isfinite(float(coverage))
        and 0.0 <= float(coverage) <= 1.0
        else 0.0
    )
    limitations = [
        value for value in native_result.get("limitations") or []
        if isinstance(value, str) and value
    ]
    if alignment.get("status") != "aligned" or coverage < 1.0:
        limitations.append("alignment_partial")
    timeline = (native_result.get("deterministic") or {}).get("timeline")
    has_flick_evidence = isinstance(timeline, list) and any(
        isinstance(item, Mapping) and item.get("event_type") == "flick"
        for item in timeline
    )
    if not has_flick_evidence:
        limitations.append("left_click_anchors_missing")
    complete = (
        native_result.get("status") == "available"
        and alignment.get("status") == "aligned"
        and coverage >= 1.0
        and has_flick_evidence
    )
    quality_limitations = list(dict.fromkeys(limitations)) if not complete else []
    return {
        "status": "available" if complete else "limited",
        "coverage": coverage,
        "limitations": quality_limitations,
    }


def _visual_quality_projection(visual_result: Mapping[str, object]) -> dict:
    limitations = [
        value for value in visual_result.get("limitations") or []
        if value in {
            "missing_frame_pts",
            "non_monotonic_frame_pts",
            "frame_pts_outside_canonical_window",
        }
    ]
    summary = visual_result.get("safe_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    coverages = [
        value for value in (
            summary.get("target_coverage"), summary.get("crosshair_coverage"),
        )
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    ]
    return {
        "status": "limited" if limitations else "available",
        "coverage": min(float(value) for value in coverages) if coverages else None,
        "limitations": list(dict.fromkeys(limitations)),
    }


def _project_metric_quality(metrics: Mapping[str, object], quality: Mapping[str, object]) -> None:
    limitations = quality.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        return
    coverage_cap = quality.get("coverage")
    for metric in metrics.values():
        if not isinstance(metric, dict):
            continue
        coverage = metric.get("coverage")
        if (
            isinstance(coverage_cap, (int, float))
            and not isinstance(coverage_cap, bool)
            and isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and math.isfinite(float(coverage))
        ):
            metric["coverage"] = min(float(coverage), float(coverage_cap))
        metric["limitations"] = list(dict.fromkeys([
            *(metric.get("limitations") or []), *limitations,
        ]))


def _native_diagnosis(
    metrics: dict,
    *,
    input_mode: str = "input_native",
    quality: Mapping[str, object] | None = None,
) -> dict:
    """Build deterministic Coach issues from available native distributions."""
    from dataclasses import asdict
    from kovaak_tracker.advice import advise
    from kovaak_tracker.coach.diagnosis import build_diagnosis

    supported = {
        "decel_frac",
        "linearity",
        "sparc",
        "reverse_ratio",
        "submovement_overlap",
        "peak_position_pct",
        "path_efficiency",
        "peak_speed_deg",
        "throughput",
    }
    summary: dict[str, dict[str, float]] = {}
    for key in supported:
        metric = metrics.get(key)
        if not isinstance(metric, dict) or metric.get("availability") == "unavailable":
            continue
        value = metric.get("med", metric.get("median", metric.get("value")))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        summary[key] = {
            "med": float(value),
            "metric_version": metric.get("metric_version"),
        }

    findings = advise(summary)
    for finding in findings:
        event_refs: list[str] = []
        for metric_key in finding.metric_refs:
            metric = metrics.get(metric_key)
            if not isinstance(metric, dict):
                continue
            refs = metric.get("outlier_refs") or metric.get("sample_refs") or []
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, str) and ref not in event_refs:
                    event_refs.append(ref)
                if len(event_refs) >= 3:
                    break
            if len(event_refs) >= 3:
                break
        finding.event_refs = event_refs
    diagnosis = build_diagnosis(
        findings,
        summary,
        comparison=None,
        meta={
            "summary_type": "flicking",
            "input_mode": input_mode,
            "quality_status": (quality or {}).get("status"),
            "quality_limitations": list((quality or {}).get("limitations") or []),
        },
    )
    projection = asdict(diagnosis)
    for issue in projection.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue.pop("root_causes", None)
        issue.pop("prescriptions", None)
        if issue.get("observation_ref") is None:
            issue.pop("observation_ref", None)
        if issue.get("knowledge_registry_version") is None:
            issue.pop("knowledge_registry_version", None)
        if not issue.get("knowledge_entry_refs"):
            issue.pop("knowledge_entry_refs", None)
    return projection


def _result_owner(job: dict) -> tuple[str, str | None]:
    owner_id = job.get("user_id")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("analysis job requires user_id")
    return owner_id, owner_id if owner_id == DESKTOP_LOCAL_PROFILE else None


def _source_parser_version(kind: str, source: dict) -> str:
    version = source.get("parser_version")
    if isinstance(version, str) and version:
        return version
    from .kovaak_run_store import PERFORMANCE_PARSER_VERSION, STATS_PARSER_VERSION

    return {
        "stats": STATS_PARSER_VERSION,
        "performance": PERFORMANCE_PARSER_VERSION,
    }.get(kind, f"{kind}.v1")


def _artifact_entry(
    *,
    artifact_id: str,
    kind: str,
    source: str,
    availability: str,
    ownership: str,
    managed: bool,
    local_only: bool,
    derived_from: list[str],
    parser_version: str | None = None,
    format_version: str | int | None = None,
    checksum: str | None = None,
) -> dict:
    entry = {
        "id": artifact_id,
        "kind": kind,
        "source": source,
        "availability": availability,
        "ownership": ownership,
        "managed": managed,
        "local_only": local_only,
        "status": availability,
        "derived_from": derived_from,
    }
    if parser_version is not None:
        entry["parser_version"] = parser_version
    if format_version is not None:
        entry["format_version"] = format_version
    if checksum:
        entry["checksum"] = checksum
    return entry


def _native_source_contract(
    kind: str,
    source: dict,
    snapshot: dict,
) -> dict:
    if kind == "raw_input":
        snapshot_source = snapshot.get("trace") or {}
        version = snapshot_source.get("format_version", 1)
    else:
        snapshot_source = (snapshot.get("sources") or {}).get(kind) or {}
        version = _source_parser_version(kind, snapshot_source)
    out = dict(source)
    out["artifact_ref"] = snapshot_source.get("artifact_ref")
    out["parser_or_format_version"] = version
    return out


def _native_v2_evidence(
    native_result: dict,
    *,
    run_ref: str,
    snapshot: dict,
    analysis_id: str,
    video_availability: str | None = None,
) -> dict:
    native_evidence = native_result.get("evidence") or {}
    source_values = native_evidence.get("sources") or {}
    sources = {
        key: _native_source_contract(key, value, snapshot)
        for key, value in source_values.items()
        if isinstance(value, dict)
    }
    if video_availability is not None:
        sources["mp4"] = {
            "source": "mp4",
            "role": "visual_evidence",
            "availability": video_availability,
            "artifact_ref": f"{analysis_id}:video",
            "parser_or_format_version": "mp4",
            "alignment": "not_required",
            "warnings": [],
        }
    availability = {
        key: value.get("availability", "unavailable")
        for key, value in sources.items()
    }
    return {
        "sources": sources,
        "provenance": {
            "kovaak_run_ref": run_ref,
            "adapter": "native_flicking_analysis",
            "adapter_version": NATIVE_ANALYSIS_VERSION,
        },
        "availability": availability,
        "alignment": dict(native_evidence.get("alignment") or {"status": "unavailable"}),
        "coverage": native_evidence.get("coverage"),
        "warnings": list(native_evidence.get("warnings") or []),
    }


def _native_artifact_manifest_v2(
    job: dict,
    snapshot: dict,
    *,
    include_video: bool,
) -> dict:
    analysis_id = f"analysis:{job['id']}"
    external_inputs: list[dict] = []
    for kind, source in (snapshot.get("sources") or {}).items():
        if kind == "video":
            continue
        if isinstance(source, dict) and source.get("artifact_ref"):
            fingerprint = source.get("fingerprint") or {}
            external_inputs.append(_artifact_entry(
                artifact_id=source["artifact_ref"],
                kind=kind,
                source=kind,
                availability=source.get("availability", "unavailable"),
                ownership="user_source",
                managed=False,
                local_only=True,
                parser_version=_source_parser_version(kind, source),
                checksum=fingerprint.get("sha256") if isinstance(fingerprint, dict) else None,
                derived_from=[],
            ))
    trace = snapshot.get("trace")
    if isinstance(trace, dict) and trace.get("artifact_ref"):
        fingerprint = trace.get("fingerprint") or {}
        external_inputs.append(_artifact_entry(
            artifact_id=trace["artifact_ref"],
            kind="raw_input",
            source="raw_input",
            availability=trace.get("availability", "unavailable"),
            ownership="kovaak_run",
            managed=True,
            local_only=True,
            format_version=trace.get("format_version", 1),
            checksum=fingerprint.get("sha256") if isinstance(fingerprint, dict) else None,
            derived_from=[],
        ))
    if include_video:
        video_source = (snapshot.get("sources") or {}).get("video") or {}
        video_fingerprint = video_source.get("fingerprint") or {}
        external_inputs.append(_artifact_entry(
            artifact_id=f"{analysis_id}:video",
            kind="mp4",
            source="mp4",
            availability="available" if job.get("video_path") else "missing",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version="mp4",
            checksum=(
                video_fingerprint.get("sha256")
                if isinstance(video_fingerprint, dict)
                else None
            ),
            derived_from=[],
        ))
    return build_artifact_manifest_v2(
        external_inputs=external_inputs,
        owned_outputs=[_artifact_entry(
            artifact_id=analysis_id,
            kind="analysis_result",
            source="analysis",
            availability="available",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version=ANALYSIS_RESULT_V2_SCHEMA_VERSION,
            derived_from=[entry["id"] for entry in external_inputs],
        )],
    )


def _target_switching_production_gate(
    resolution: Mapping[str, object],
) -> bool:
    """Require only the exact reviewed visual episode producer."""
    from kovaak_tracker.visual_signals import (
        VISUAL_TARGET_EPISODE_PRODUCER_ID,
        VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        visual_detector_config_ref_v1,
    )

    profile_ref = resolution.get("scenario_profile_ref")
    if not isinstance(profile_ref, str):
        return False
    producer = _REVIEWED_VISUAL_PRODUCERS.get(profile_ref)
    if not isinstance(producer, Mapping):
        return False
    quality = producer.get("visual_quality_profile")
    if not isinstance(quality, Mapping):
        return False
    detector_config = producer.get("detector_config")
    calibration_context = quality.get("calibration_context")
    expected_profile_ref = (
        f"visual-quality:{VISUAL_TARGET_EPISODE_PRODUCER_ID}@"
        f"{VISUAL_TARGET_EPISODE_PRODUCER_VERSION}"
    )
    if not (
        quality.get("status") == "accepted"
        and quality.get("producer_id") == VISUAL_TARGET_EPISODE_PRODUCER_ID
        and quality.get("producer_version")
        == VISUAL_TARGET_EPISODE_PRODUCER_VERSION
        and quality.get("profile_ref") == expected_profile_ref
        and "switching" in (quality.get("validated_metric_families") or [])
        and (
            quality.get("quality_status_by_metric_family") or {}
        ).get("switching") == "accepted"
        and producer.get("detector_config_ref")
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
        and isinstance(detector_config, Mapping)
        and visual_detector_config_ref_v1(detector_config)
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
        and isinstance(calibration_context, Mapping)
        and calibration_context.get("detector_config_ref")
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
    ):
        return False
    return True


def _scenario_dispatch(job: dict, input_mode: str) -> str:
    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution")
    if resolution is None:
        snapshot_version = snapshot.get("schema_version")
        if (
            snapshot_version in {
                "analysis_input_snapshot.v1", "analysis_input_snapshot.v2",
            }
            and (job.get("analysis_type") or "flicking") == "flicking"
        ):
            return "legacy_static_compatibility"
        if (
            not snapshot
            and input_mode == "video_fallback"
            and job.get("kovaak_run_id") is None
            and (job.get("analysis_type") or "flicking") == "flicking"
        ):
            return "legacy_static_compatibility"
        if snapshot_version == "analysis_input_snapshot.v3":
            raise ValueError("analysis_input_snapshot.v3 requires scenario resolution")
        raise SourceSnapshotChangedError(
            "source_unavailable: unsupported input snapshot",
        )
    if not isinstance(resolution, dict):
        raise ValueError("scenario resolution is invalid")
    resolution = validate_scenario_resolution_v1(resolution)
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "static_clicking"
        and input_mode in {"input_native", "multimodal"}
        and NATIVE_ANALYSIS_VERSION in (resolution.get("allowed_analyzers") or [])
        and "static_clicking" in (resolution.get("allowed_metric_families") or [])
    ):
        return NATIVE_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "dynamic_clicking"
        and input_mode == "multimodal"
        and DYNAMIC_CLICKING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "dynamic_clicking" in (resolution.get("allowed_metric_families") or [])
    ):
        return DYNAMIC_CLICKING_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "continuous_tracking"
        and input_mode == "multimodal"
        and CONTINUOUS_TRACKING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "continuous_tracking"
        in (resolution.get("allowed_metric_families") or [])
    ):
        return CONTINUOUS_TRACKING_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "target_switching"
        and input_mode == "multimodal"
        and TARGET_SWITCHING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "target_switching"
        in (resolution.get("allowed_metric_families") or [])
        and _target_switching_production_gate(resolution)
    ):
        return TARGET_SWITCHING_ANALYSIS_VERSION
    return "outcome_only"


def _outcome_only_evidence(
    job: dict,
    snapshot: dict,
    *,
    include_video: bool,
) -> dict:
    analysis_id = f"analysis:{job['id']}"
    sources: dict[str, dict] = {}
    roles = {
        "stats": "outcome_source",
        "performance": "event_anchor",
        "raw_input": "input_kinematics_source",
        "mp4": "visual_source_not_analyzed",
    }
    for kind, source in (snapshot.get("sources") or {}).items():
        if not isinstance(source, dict) or kind == "video":
            continue
        artifact_ref = source.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            continue
        sources[kind] = {
            "source": kind,
            "role": roles.get(kind, "source"),
            "availability": source.get("availability", "unavailable"),
            "artifact_ref": artifact_ref,
            "parser_or_format_version": _source_parser_version(kind, source),
            "alignment": "not_evaluated",
            "warnings": [],
        }
    trace = snapshot.get("trace")
    if isinstance(trace, dict) and isinstance(trace.get("artifact_ref"), str):
        sources["raw_input"] = {
            "source": "raw_input",
            "role": roles["raw_input"],
            "availability": trace.get("availability", "unavailable"),
            "artifact_ref": trace["artifact_ref"],
            "parser_or_format_version": trace.get("format_version", 1),
            "alignment": "not_evaluated",
            "warnings": [],
        }
    if include_video:
        video_source = (snapshot.get("sources") or {}).get("video") or {}
        if isinstance(video_source, dict):
            sources["mp4"] = {
                "source": "mp4",
                "role": roles["mp4"],
                "availability": "available" if job.get("video_path") else "missing",
                "artifact_ref": f"{analysis_id}:video",
                "parser_or_format_version": "mp4",
                "alignment": "not_evaluated",
                "warnings": [],
            }
    window = snapshot.get("canonical_time_window")
    if isinstance(window, dict):
        alignment = {
            "status": "aligned",
            "challenge_start_epoch_ms": window.get("start_ms"),
            "challenge_end_epoch_ms": window.get("end_ms"),
            "window_semantics": window.get("window_semantics", "half_open"),
        }
    else:
        alignment = {"status": "unavailable"}
    return {
        "sources": sources,
        "provenance": {
            "kovaak_run_ref": (
                f"run:{job.get('kovaak_run_id') or snapshot.get('run_id')}"
            ),
            "adapter": "scenario_dispatch_gate",
            "adapter_version": SCENARIO_OUTCOME_ONLY_VERSION,
        },
        "availability": {
            kind: source["availability"] for kind, source in sources.items()
        },
        "alignment": alignment,
        "coverage": None,
        "warnings": [{"code": "family_analyzer_not_dispatched"}],
    }


def _build_outcome_only_result_v2(
    job: dict,
    *,
    created_at: str,
    completed_at: str,
    limitations_override: list[str] | None = None,
    visual_validation: dict | None = None,
    extra_warnings: list[dict] | None = None,
    analysis_type_override: str | None = None,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution") or {}
    input_mode = job.get("input_mode") or "video_fallback"
    include_video = input_mode in {"multimodal", "video_fallback"}
    public_snapshot = public_analysis_input_snapshot(snapshot)
    if input_mode == "input_native":
        public_snapshot.get("sources", {}).pop("video", None)
    owner_id, local_profile = _result_owner(job)
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("outcome-only analysis requires kovaak_run_id")
    limitations = list(
        limitations_override
        if limitations_override is not None
        else resolution.get("limitations") or ["scenario_not_in_active_manifest"]
    )
    deterministic = {
        "support_status": "outcome_only",
        "metrics": {},
        "limitations": limitations,
    }
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    result = build_analysis_result_v2(
        analysis_version=SCENARIO_OUTCOME_ONLY_VERSION,
        analysis_id=f"analysis:{job['id']}",
        analysis_type=analysis_type_override or job.get("analysis_type") or "flicking",
        input_mode=input_mode,
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=_outcome_only_evidence(
            job,
            snapshot,
            include_video=include_video,
        ),
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job,
            snapshot,
            include_video=include_video,
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=[{"code": "scenario_outcome_only"}, *(extra_warnings or [])],
        errors=[],
    )
    return result


def _build_dynamic_result_v2(
    job: dict,
    dynamic_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot
    from kovaak_tracker.advice_dynamic_clicking import (
        build_dynamic_clicking_candidate_advice,
    )

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("dynamic analysis requires kovaak_run_id")
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    metrics = {
        metric_key: {
            "key": metric_key,
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "provenance": {
                "kind": (metric.get("provenance") or {}).get("kind", "derived"),
                "sources": list((metric.get("provenance") or {}).get("source_refs") or []),
            },
            "metric_version": metric.get("metric_version"),
            "coverage": metric.get("coverage"),
            "classification": metric.get("classification"),
            "limitations": list(metric.get("limitations") or []),
            "condition_refs": list(metric.get("condition_refs") or []),
        }
        for metric_key, metric in (dynamic_result.get("metrics") or {}).items()
        if isinstance(metric, Mapping)
    }
    visual_summary = dict(visual_result.get("safe_summary") or {})
    visual_quality = _visual_quality_projection(visual_result)
    _project_metric_quality(metrics, visual_quality)
    visual_quality_profile_ref = visual_result.get("visual_quality_profile_ref")
    if isinstance(visual_quality_profile_ref, str) and visual_quality_profile_ref:
        for metric in metrics.values():
            metric["calibration_ref"] = visual_quality_profile_ref
    candidate_observations = build_dynamic_clicking_candidate_advice(dynamic_result)
    diagnosis_issues = [
        {
            "signal": candidate["signal"],
            "priority": index,
            "priority_reason": "matched comparison candidate",
            "plain_language_meaning": (
                "A matched prior Run differs on the referenced dynamic metric; "
                "mechanism and training guidance require the referenced knowledge entry."
            ),
            "claim_level": candidate["claim_level"],
            "metric_refs": list(candidate["metric_refs"]),
            "observation_ref": candidate["observation_ref"],
            "knowledge_registry_version": candidate["knowledge_registry_version"],
            "knowledge_entry_refs": list(candidate["knowledge_entry_refs"]),
            "event_refs": [
                *candidate["supporting_row_refs"],
                *candidate["counterexample_row_refs"],
            ],
            "limitations": list(candidate["limitations"]),
            "verification": {
                "comparable_requirements": [
                    "same scenario profile", "same visual quality profile",
                    "same motion condition", "same metric version",
                ],
                "success_signals": ["move toward the matched baseline without outcome collapse"],
                "insufficient_evidence_behavior": "keep the observation descriptive and collect another matched Run",
            },
        }
        for index, candidate in enumerate(candidate_observations, 1)
    ]
    deterministic = {
        "support_status": dynamic_result.get("support_status"),
        "scenario_motion_class": dynamic_result.get("scenario_motion_class"),
        "metrics": metrics,
        "candidate_observations": candidate_observations,
        "diagnosis": {
            "profile": {},
            "issues": diagnosis_issues,
            "summary": metrics,
            "comparison": None,
            "meta": {
                "summary_type": "dynamic_clicking",
                "classification": "deterministic",
            },
        },
        "visual_validation": visual_summary,
        "visual_quality_profile_ref": visual_quality_profile_ref,
        "limitations": list(dict.fromkeys([
            *(dynamic_result.get("limitations") or []),
            *visual_quality["limitations"],
        ])),
    }
    evidence = _outcome_only_evidence(job, snapshot, include_video=True)
    evidence["provenance"] = {
        "kovaak_run_ref": f"run:{run_id}",
        "adapter": "dynamic_clicking",
        "adapter_version": DYNAMIC_CLICKING_ANALYSIS_VERSION,
    }
    visual_coverages = (
        visual_summary.get("target_coverage"),
        visual_summary.get("crosshair_coverage"),
    )
    processed_rows = dynamic_result.get("processed_rows")
    processed_table = dynamic_result.get("processed_event_table")
    click_row_count = (
        processed_table.get("row_count")
        if isinstance(processed_table, Mapping)
        else None
    )
    click_anchor_coverage = (
        1.0
        if isinstance(processed_rows, list)
        and processed_rows
        and isinstance(click_row_count, int)
        and not isinstance(click_row_count, bool)
        and click_row_count == len(processed_rows)
        else None
    )
    coverage_components = [*visual_coverages, click_anchor_coverage]
    evidence["coverage"] = (
        min(float(value) for value in coverage_components)
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
            for value in coverage_components
        )
        else None
    )
    evidence["warnings"] = []
    if "mp4" in evidence["sources"]:
        evidence["sources"]["mp4"]["role"] = "visual_kinematics_source"
        evidence["sources"]["mp4"]["alignment"] = "aligned"
    if "raw_input" in evidence["sources"]:
        evidence["sources"]["raw_input"]["role"] = "click_anchor_source"
        evidence["sources"]["raw_input"]["alignment"] = "aligned"
    result = build_analysis_result_v2(
        analysis_version=DYNAMIC_CLICKING_ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type="dynamic_clicking",
        input_mode="multimodal",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=evidence,
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job, snapshot, include_video=True,
        ),
        input_snapshot=public_analysis_input_snapshot(snapshot),
        created_at=created_at,
        completed_at=completed_at,
        warnings=[],
        errors=[],
    )
    resolution = snapshot.get("scenario_resolution") or {}
    result["scenario"] = {
        "scenario_profile_ref": resolution.get("scenario_profile_ref"),
        "aim_family": "dynamic_clicking",
        "analyzer_refs": [DYNAMIC_CLICKING_ANALYSIS_VERSION],
        "support_status": dynamic_result.get("support_status", "unavailable"),
        "limitations": list(dynamic_result.get("limitations") or []),
    }
    return result


def _build_continuous_tracking_result_v2(
    job: dict,
    tracking_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot
    from kovaak_tracker.advice_tracking import build_tracking_candidate_advice

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("continuous tracking analysis requires kovaak_run_id")
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    metrics = {
        metric_key: {
            "key": metric_key,
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "provenance": {
                "kind": (metric.get("provenance") or {}).get("kind", "derived"),
                "sources": list((metric.get("provenance") or {}).get("source_refs") or []),
            },
            "metric_version": metric.get("metric_version"),
            "coverage": metric.get("coverage"),
            "classification": metric.get("classification"),
            "limitations": list(metric.get("limitations") or []),
            "condition_refs": list(metric.get("condition_refs") or []),
        }
        for metric_key, metric in (tracking_result.get("metrics") or {}).items()
        if isinstance(metric, Mapping)
    }
    visual_summary = dict(visual_result.get("safe_summary") or {})
    visual_quality = _visual_quality_projection(visual_result)
    _project_metric_quality(metrics, visual_quality)
    visual_quality_profile_ref = visual_result.get("visual_quality_profile_ref")
    if isinstance(visual_quality_profile_ref, str) and visual_quality_profile_ref:
        for metric in metrics.values():
            metric["calibration_ref"] = visual_quality_profile_ref
    candidate_observations = build_tracking_candidate_advice(tracking_result)
    diagnosis_issues = [
        {
            "signal": candidate["signal"],
            "priority": index,
            "priority_reason": "matched comparison candidate",
            "plain_language_meaning": (
                "A matched prior Run differs on the referenced tracking metric; "
                "mechanism and training guidance require the referenced knowledge entry."
            ),
            "claim_level": candidate["claim_level"],
            "metric_refs": list(candidate["metric_refs"]),
            "observation_ref": candidate["observation_ref"],
            "knowledge_registry_version": candidate["knowledge_registry_version"],
            "knowledge_entry_refs": list(candidate["knowledge_entry_refs"]),
            "event_refs": [
                *candidate["supporting_row_refs"],
                *candidate["counterexample_row_refs"],
            ],
            "limitations": list(candidate["limitations"]),
            "verification": {
                "comparable_requirements": [
                    "same scenario profile", "same visual quality profile",
                    "same motion condition", "same metric version",
                ],
                "success_signals": ["move toward the matched baseline without outcome collapse"],
                "insufficient_evidence_behavior": "keep the observation descriptive and collect another matched Run",
            },
        }
        for index, candidate in enumerate(candidate_observations, 1)
    ]
    deterministic = {
        "support_status": tracking_result.get("support_status"),
        "scenario_motion_class": tracking_result.get("scenario_motion_class"),
        "metrics": metrics,
        "candidate_observations": candidate_observations,
        "diagnosis": {
            "profile": {},
            "issues": diagnosis_issues,
            "summary": metrics,
            "comparison": tracking_result.get("comparison"),
            "meta": {
                "summary_type": "continuous_tracking",
                "classification": "deterministic",
            },
        },
        "visual_validation": visual_summary,
        "visual_quality_profile_ref": visual_quality_profile_ref,
        "limitations": list(dict.fromkeys([
            *(tracking_result.get("limitations") or []),
            *visual_quality["limitations"],
        ])),
    }
    evidence = _outcome_only_evidence(job, snapshot, include_video=True)
    evidence["provenance"] = {
        "kovaak_run_ref": f"run:{run_id}",
        "adapter": "continuous_tracking",
        "adapter_version": CONTINUOUS_TRACKING_ANALYSIS_VERSION,
    }
    coverages = [
        visual_summary.get("target_coverage"),
        visual_summary.get("crosshair_coverage"),
    ]
    evidence["coverage"] = (
        min(float(value) for value in coverages)
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
            for value in coverages
        )
        else None
    )
    evidence["warnings"] = []
    if "mp4" in evidence["sources"]:
        evidence["sources"]["mp4"]["role"] = "visual_kinematics_source"
        evidence["sources"]["mp4"]["alignment"] = "aligned"
    result = build_analysis_result_v2(
        analysis_version=CONTINUOUS_TRACKING_ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type="continuous_tracking",
        input_mode="multimodal",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=evidence,
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job, snapshot, include_video=True,
        ),
        input_snapshot=public_analysis_input_snapshot(snapshot),
        created_at=created_at,
        completed_at=completed_at,
        warnings=[],
        errors=[],
    )
    resolution = snapshot.get("scenario_resolution") or {}
    result["scenario"] = {
        "scenario_profile_ref": resolution.get("scenario_profile_ref"),
        "analyzer_refs": [CONTINUOUS_TRACKING_ANALYSIS_VERSION],
        "support_status": tracking_result.get("support_status", "unavailable"),
        "limitations": list(tracking_result.get("limitations") or []),
    }
    return result


def _build_target_switching_result_v2(
    job: dict,
    switching_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot
    from kovaak_tracker.advice_target_switching import (
        build_target_switching_candidate_advice,
    )

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("target switching analysis requires kovaak_run_id")
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    metrics = {
        metric_key: {
            "key": metric_key,
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "provenance": {
                "kind": (metric.get("provenance") or {}).get("kind", "derived"),
                "sources": list((metric.get("provenance") or {}).get("source_refs") or []),
            },
            "metric_version": metric.get("metric_version"),
            "coverage": metric.get("coverage"),
            "classification": metric.get("classification"),
            "limitations": list(metric.get("limitations") or []),
            "condition_refs": list(metric.get("condition_refs") or []),
        }
        for metric_key, metric in (switching_result.get("metrics") or {}).items()
        if isinstance(metric, Mapping)
    }
    visual_summary = dict(visual_result.get("safe_summary") or {})
    visual_quality = _visual_quality_projection(visual_result)
    _project_metric_quality(metrics, visual_quality)
    visual_quality_profile_ref = visual_result.get("visual_quality_profile_ref")
    if isinstance(visual_quality_profile_ref, str) and visual_quality_profile_ref:
        for metric in metrics.values():
            metric["calibration_ref"] = visual_quality_profile_ref
    candidate_observations = build_target_switching_candidate_advice(switching_result)
    diagnosis_issues = [
        {
            "signal": candidate["signal"],
            "priority": index,
            "priority_reason": "matched comparison candidate",
            "plain_language_meaning": (
                "A matched prior Run differs on the referenced target-switching "
                "metric; mechanism and training guidance require the referenced "
                "knowledge entry."
            ),
            "claim_level": candidate["claim_level"],
            "metric_refs": list(candidate["metric_refs"]),
            "observation_ref": candidate["observation_ref"],
            "knowledge_registry_version": candidate["knowledge_registry_version"],
            "knowledge_entry_refs": list(candidate["knowledge_entry_refs"]),
            "event_refs": [
                *candidate["supporting_row_refs"],
                *candidate["counterexample_row_refs"],
            ],
            "limitations": list(candidate["limitations"]),
            "verification": {
                "comparable_requirements": [
                    "same scenario profile", "same visual quality profile",
                    "same motion condition", "same metric condition",
                    "same metric version",
                ],
                "success_signals": [
                    "move toward the matched baseline without outcome collapse",
                ],
                "insufficient_evidence_behavior": (
                    "keep the observation descriptive and collect another matched Run"
                ),
            },
        }
        for index, candidate in enumerate(candidate_observations, 1)
    ]
    resolution = snapshot.get("scenario_resolution") or {}
    target_motion = resolution.get("target_motion") or {}
    deterministic = {
        "support_status": switching_result.get("support_status"),
        "scenario_motion_class": target_motion.get("model"),
        "metrics": metrics,
        "candidate_observations": candidate_observations,
        "diagnosis": {
            "profile": {},
            "issues": diagnosis_issues,
            "summary": metrics,
            "comparison": switching_result.get("comparison"),
            "meta": {
                "summary_type": "target_switching",
                "classification": "deterministic",
            },
        },
        "visual_validation": visual_summary,
        "visual_quality_profile_ref": visual_quality_profile_ref,
        "limitations": list(dict.fromkeys([
            *(switching_result.get("limitations") or []),
            *visual_quality["limitations"],
        ])),
    }
    evidence = _outcome_only_evidence(job, snapshot, include_video=True)
    evidence["provenance"] = {
        "kovaak_run_ref": f"run:{run_id}",
        "adapter": "target_switching",
        "adapter_version": TARGET_SWITCHING_ANALYSIS_VERSION,
    }
    processed_rows = switching_result.get("processed_rows")
    processed_tables = switching_result.get("processed_event_tables")
    row_count = sum(
        int(table.get("row_count", 0))
        for table in processed_tables or []
        if isinstance(table, Mapping)
    )
    coverage_components = [
        visual_summary.get("target_coverage"),
        visual_summary.get("crosshair_coverage"),
        (
            1.0
            if isinstance(processed_rows, list)
            and processed_rows
            and row_count == len(processed_rows)
            else None
        ),
    ]
    evidence["coverage"] = (
        min(float(value) for value in coverage_components)
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
            for value in coverage_components
        )
        else None
    )
    evidence["warnings"] = []
    if "mp4" in evidence["sources"]:
        evidence["sources"]["mp4"]["role"] = "visual_kinematics_source"
        evidence["sources"]["mp4"]["alignment"] = "aligned"
    result = build_analysis_result_v2(
        analysis_version=TARGET_SWITCHING_ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type="target_switching",
        input_mode="multimodal",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=evidence,
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job, snapshot, include_video=True,
        ),
        input_snapshot=public_analysis_input_snapshot(snapshot),
        created_at=created_at,
        completed_at=completed_at,
        warnings=[],
        errors=[],
    )
    result["scenario"] = {
        "scenario_profile_ref": resolution.get("scenario_profile_ref"),
        "analyzer_refs": [TARGET_SWITCHING_ANALYSIS_VERSION],
        "support_status": switching_result.get("support_status", "unavailable"),
        "limitations": list(switching_result.get("limitations") or []),
    }
    return result


def _build_native_result_v2(
    job: dict,
    native_result: dict,
    *,
    created_at: str,
    completed_at: str,
    video_availability: str | None = None,
    warnings: list[dict] | None = None,
    visual_validation: dict | None = None,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("native analysis requires kovaak_run_id")
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    run_ref = f"run:{run_id}"
    input_mode = job.get("input_mode") or "input_native"
    deterministic = _native_deterministic_v2(native_result, input_mode=input_mode)
    resolution = snapshot.get("scenario_resolution")
    active_static = (
        isinstance(resolution, Mapping)
        and resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "static_clicking"
        and NATIVE_ANALYSIS_VERSION in (resolution.get("allowed_analyzers") or [])
        and "static_clicking" in (resolution.get("allowed_metric_families") or [])
    )
    if active_static:
        deterministic["support_status"] = {
            "available": "supported",
            "partial": "partial",
            "limited": "partial",
        }.get(str(deterministic.get("status")), "unavailable")
    result_warnings = list(warnings or [])
    if not isinstance(snapshot.get("scenario_resolution"), dict):
        deterministic.setdefault("limitations", []).append(
            "legacy_static_compatibility"
        )
        result_warnings.append({"code": "legacy_static_compatibility"})
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    public_snapshot = public_analysis_input_snapshot(snapshot)
    calibration = native_result.get("calibration")
    if isinstance(calibration, Mapping):
        public_snapshot["calibration"] = dict(calibration)
    if input_mode == "input_native":
        public_snapshot.get("sources", {}).pop("video", None)
    elif video_availability is not None:
        video_source = dict(public_snapshot.get("sources", {}).get("video") or {})
        video_source.update({
            "artifact_ref": f"{analysis_id}:video",
            "availability": "available" if job.get("video_path") else "missing",
        })
        public_snapshot.setdefault("sources", {})["video"] = video_source
    result = build_analysis_result_v2(
        analysis_version=NATIVE_ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type=native_result.get("analysis_type", "flicking"),
        input_mode=input_mode,
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=run_ref,
        evidence=_native_v2_evidence(
            native_result,
            run_ref=run_ref,
            snapshot=snapshot,
            analysis_id=analysis_id,
            video_availability=video_availability,
        ),
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job,
            snapshot,
            include_video=video_availability is not None,
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=result_warnings,
        errors=[],
    )
    if active_static:
        result["scenario"] = {
            "scenario_profile_ref": resolution.get("scenario_profile_ref"),
            "aim_family": "static_clicking",
            "analyzer_refs": [NATIVE_ANALYSIS_VERSION],
            "support_status": deterministic["support_status"],
            "limitations": list(deterministic.get("limitations") or []),
        }
    return result


_VIDEO_FALLBACK_SPARC_METRIC_VERSION = "flicking_fair_summary.sparc.v2"


_VIDEO_FALLBACK_METRIC_UNITS = {
    "peak_speed_deg": "degrees_per_second",
    "linearity": "dimensionless",
    "sparc": "dimensionless",
    "reverse_ratio": "dimensionless",
    "decel_frac": "dimensionless",
    "endpoint_peak": "dimensionless",
    "peak_position_pct": "percent",
    "corrective_count": "count",
    "submovement_overlap": "dimensionless",
    "path_efficiency": "dimensionless",
    "path_length_deg": "degrees",
    "throughput": "bits_per_second",
    "peak_cm_per_s": "centimeters_per_second",
    "flick_count": "count",
}


def _video_fallback_metrics(summary: dict) -> dict:
    metrics: dict[str, dict] = {}
    flick_count = summary.get("flick_count")
    sample_count = (
        int(flick_count)
        if isinstance(flick_count, (int, float))
        and not isinstance(flick_count, bool)
        and math.isfinite(float(flick_count))
        else None
    )
    for key, raw_value in summary.items():
        distribution = raw_value if isinstance(raw_value, dict) else {}
        value = distribution.get("med") if distribution else raw_value
        available = (
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        unit = _VIDEO_FALLBACK_METRIC_UNITS.get(key, "unknown")
        limitations = ["raw_input_not_used"]
        if distribution:
            limitations.append("descriptive_distribution_not_health_threshold")
        limitations.append("coverage_not_recorded")
        if unit == "unknown":
            limitations.append("unit_not_registered")
        if not available:
            limitations.append("metric_value_unavailable")
        metric = {
            "key": key,
            "value": value if available else None,
            "unit": unit,
            "availability": "available" if available else "unavailable",
            "provenance": {"kind": "fused", "sources": ["mp4", "stats"]},
            "metric_version": (
                _VIDEO_FALLBACK_SPARC_METRIC_VERSION
                if key == "sparc"
                else ANALYSIS_VERSION
            ),
            "sample_count": sample_count,
            "coverage": None,
            "classification": "deterministic",
            "limitations": limitations,
        }
        for distribution_key in ("med", "p75", "p90"):
            if distribution_key in distribution:
                metric[distribution_key] = distribution[distribution_key]
        metrics[key] = metric
    return metrics


def _build_video_fallback_result_v2(
    job: dict,
    summary: dict,
    report: dict,
    timeline: list[dict],
    *,
    created_at: str,
    completed_at: str,
    narration_status: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    run_ref = f"run:{run_id}" if run_id is not None else None
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    stats_ref = f"analysis:{job['id']}:stats"
    video_ref = f"analysis:{job['id']}:video"

    if run_ref is not None:
        public_snapshot = public_analysis_input_snapshot(snapshot)
        public_snapshot["sources"] = {
            key: value
            for key, value in public_snapshot.get("sources", {}).items()
            if key in {"stats", "video"}
        }
        stats_source = public_snapshot["sources"].get("stats")
        if stats_source and stats_source.get("artifact_ref"):
            stats_ref = stats_source["artifact_ref"]
    else:
        stats_source = {
            "artifact_ref": stats_ref,
            "availability": "available" if job.get("csv_path") else "missing",
        }
        public_snapshot = {
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": None,
            "scenario": None,
            "sources": {"stats": stats_source},
            "trace": None,
        }

    video_source = dict(public_snapshot["sources"].get("video") or {})
    video_source.update({
        "artifact_ref": video_ref,
        "availability": "available" if job.get("video_path") else "missing",
    })
    public_snapshot["sources"]["video"] = video_source
    public_snapshot["trace"] = None
    calibration = job.get("calibration_snapshot")
    if isinstance(calibration, Mapping):
        public_snapshot["calibration"] = dict(calibration)
    stats_availability = (
        "available"
        if stats_source and stats_source.get("availability") == "available"
        else "missing"
    )
    sources = {
        "stats": {
            "source": "stats",
            "role": "scenario_config",
            "availability": stats_availability,
            "artifact_ref": stats_ref,
            "parser_or_format_version": _source_parser_version("stats", stats_source or {}),
            "alignment": "not_required",
            "warnings": [],
        },
        "mp4": {
            "source": "mp4",
            "role": "visual_evidence",
            "availability": "available" if job.get("video_path") else "missing",
            "artifact_ref": video_ref,
            "parser_or_format_version": "mp4",
            "alignment": "not_required",
            "warnings": [],
        },
    }
    external_inputs = [
        _artifact_entry(
            artifact_id=stats_ref,
            kind="stats",
            source="stats",
            availability=stats_availability,
            ownership="user_source" if run_ref is not None else "analysis",
            managed=run_ref is None,
            local_only=True,
            parser_version=_source_parser_version("stats", stats_source or {}),
            derived_from=[],
        ),
        _artifact_entry(
            artifact_id=video_ref,
            kind="mp4",
            source="mp4",
            availability="available" if job.get("video_path") else "missing",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version="mp4",
            checksum=(
                video_source.get("fingerprint", {}).get("sha256")
                if isinstance(video_source.get("fingerprint"), dict)
                else None
            ),
            derived_from=[],
        ),
    ]
    narration = report.get("narration") if narration_status == "available" else None
    provenance = {
        "adapter": "video_flicking_fair_summary",
        "adapter_version": ANALYSIS_VERSION,
    }
    if run_ref is not None:
        provenance["kovaak_run_ref"] = run_ref
    result = build_analysis_result_v2(
        analysis_version=ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type="flicking",
        input_mode="video_fallback",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=run_ref,
        evidence={
            "sources": sources,
            "provenance": provenance,
            "availability": {
                key: value["availability"] for key, value in sources.items()
            },
            "alignment": {"status": "not_required"},
            "coverage": None,
            "warnings": [],
        },
        deterministic={
            "status": "available",
            "summary": summary,
            "metrics": _video_fallback_metrics(summary),
            "diagnosis": report.get("diagnosis", {}),
            "figures": report.get("figures", {}),
            "timeline": timeline,
            "limitations": ["raw_input_not_used"],
        },
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=external_inputs,
            owned_outputs=[_artifact_entry(
                artifact_id=analysis_id,
                kind="analysis_result",
                source="analysis",
                availability="available",
                ownership="analysis",
                managed=True,
                local_only=True,
                format_version=ANALYSIS_RESULT_V2_SCHEMA_VERSION,
                derived_from=[entry["id"] for entry in external_inputs],
            )],
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=[{"code": "raw_input_not_used"}],
        errors=[],
    )
    result["narration"] = {
        "status": narration_status,
        "text": narration,
        "provider": None,
        "model": None,
        "usage": None,
    }
    return result


# --- 编排 ---

async def process_one() -> bool:
    """处理一个 job。True=处理了(无论成败),False=队列空。"""
    await queue.recover_stale_jobs()
    job = await queue.claim_next(WORKER_ID)
    if job is None:
        return False
    sid = job["id"]
    stop_hb = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(sid, stop_hb))
    try:
        input_mode = job.get("input_mode") or "video_fallback"
        created_at_iso = _sqlite_created_at_to_iso_z(job.get("created_at"))
        completed_at_iso = _utc_now_iso_z()
        frozen_stats = None
        visual_result = None
        dynamic_result = None
        tracking_result = None
        switching_result = None
        outcome_event_bundle = None
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )
        calibration_request = job.get("calibration_request")
        profile_default = (
            calibration_request.get("profile_default")
            if isinstance(calibration_request, Mapping) else None
        )
        manual_override = (
            calibration_request.get("manual_override")
            if isinstance(calibration_request, Mapping) else None
        )
        await queue.set_task_phase(sid, "aligning_input_events", worker_id=WORKER_ID)

        scenario_dispatch = _scenario_dispatch(job, input_mode)
        if scenario_dispatch == "outcome_only":
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                job.get("input_snapshot") or {},
            )
            _freeze_job_calibration(job, frozen_stats)
            result = _build_outcome_only_result_v2(
                job,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
            )
            cost = 0.0
        elif scenario_dispatch == DYNAMIC_CLICKING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result = await run_visual_preprocessing_isolated(job)
            except SourceSnapshotChangedError:
                raise
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="dynamic_clicking",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "dynamic_clicking"
                    in (quality.get("enabled_metric_families") or [])
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["dynamic_clicking_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "dynamic_clicking_analyzer_unavailable"}],
                        analysis_type_override="dynamic_clicking",
                    )
                else:
                    try:
                        outcome_event_bundle = await asyncio.to_thread(
                            _build_validated_outcome_association,
                            job,
                            frozen_stats,
                            visual_result,
                        )
                    except Exception as error:
                        log.warning(
                            "dynamic outcome association unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                    try:
                        dynamic_result = await asyncio.to_thread(
                            run_dynamic_clicking_analysis,
                            job,
                            visual_result,
                            outcome_event_bundle,
                        )
                    except SourceSnapshotChangedError:
                        raise
                    except Exception as error:
                        log.warning(
                            "dynamic clicking analysis unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                        result = _build_outcome_only_result_v2(
                            job,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                            limitations_override=["dynamic_clicking_analysis_unavailable"],
                            visual_validation=visual_validation,
                            extra_warnings=[{"code": "dynamic_clicking_analyzer_unavailable"}],
                            analysis_type_override="dynamic_clicking",
                        )
                    else:
                        result = _build_dynamic_result_v2(
                            job,
                            dynamic_result,
                            visual_result,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                        )
                        try:
                            from .history_trends import matched_dynamic_baseline_for_user

                            comparison = await matched_dynamic_baseline_for_user(
                                str(job["user_id"]),
                                result,
                                list(dynamic_result.get("metrics") or {}),
                            )
                        except Exception as error:
                            log.warning(
                                "dynamic baseline unavailable session=%s error=%s",
                                sid,
                                type(error).__name__,
                            )
                        else:
                            if comparison.get("comparable") is True:
                                dynamic_result = copy.deepcopy(dynamic_result)
                                dynamic_result["comparison"] = comparison
                                result = _build_dynamic_result_v2(
                                    job,
                                    dynamic_result,
                                    visual_result,
                                    created_at=created_at_iso,
                                    completed_at=completed_at_iso,
                                )
            cost = 0.0
        elif scenario_dispatch == CONTINUOUS_TRACKING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result, tracking_result = (
                    await run_continuous_tracking_pipeline_isolated(job)
                )
            except SourceSnapshotChangedError:
                raise
            except ContinuousTrackingAnalysisProcessError as error:
                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                visual_result = error.visual_result
                visual_validation = dict(visual_result.get("safe_summary") or {})
                log.warning(
                    "continuous tracking analysis unavailable session=%s error=%s",
                    sid,
                    error.code,
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=["continuous_tracking_analysis_unavailable"],
                    visual_validation=visual_validation,
                    extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                    analysis_type_override="continuous_tracking",
                )
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="continuous_tracking",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "tracking" in (quality.get("enabled_metric_families") or [])
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["continuous_tracking_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                        analysis_type_override="continuous_tracking",
                    )
                elif tracking_result is None:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["continuous_tracking_analysis_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                        analysis_type_override="continuous_tracking",
                    )
                else:
                    result = _build_continuous_tracking_result_v2(
                        job,
                        tracking_result,
                        visual_result,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                    )
                    try:
                        from .history_trends import matched_tracking_baseline_for_user

                        comparison = await matched_tracking_baseline_for_user(
                            str(job["user_id"]),
                            result,
                            list(tracking_result.get("metrics") or {}),
                        )
                    except Exception as error:
                        log.warning(
                            "continuous tracking baseline unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                    else:
                        if comparison.get("comparable") is True:
                            tracking_result = copy.deepcopy(tracking_result)
                            tracking_result["comparison"] = comparison
                            result = _build_continuous_tracking_result_v2(
                                job,
                                tracking_result,
                                visual_result,
                                created_at=created_at_iso,
                                completed_at=completed_at_iso,
                            )
            cost = 0.0
        elif scenario_dispatch == TARGET_SWITCHING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result, episode_result = (
                    await run_target_switching_pipeline_isolated(job)
                )
            except SourceSnapshotChangedError:
                raise
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="target_switching",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                enabled_families = (
                    set(quality.get("enabled_metric_families") or [])
                    if isinstance(quality, Mapping)
                    else set()
                )
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "target_switching" in enabled_families
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["target_switching_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "target_switching_analyzer_unavailable"}],
                        analysis_type_override="target_switching",
                    )
                else:
                    try:
                        switching_result = await asyncio.to_thread(
                            run_target_switching_analysis,
                            job,
                            visual_result,
                            episode_result,
                            frozen_stats,
                        )
                    except SourceSnapshotChangedError:
                        raise
                    except Exception as error:
                        log.warning(
                            "target switching analysis unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                        result = _build_outcome_only_result_v2(
                            job,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                            limitations_override=["target_switching_analysis_unavailable"],
                            visual_validation=visual_validation,
                            extra_warnings=[{"code": "target_switching_analyzer_unavailable"}],
                            analysis_type_override="target_switching",
                        )
                    else:
                        result = _build_target_switching_result_v2(
                            job,
                            switching_result,
                            visual_result,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                        )
                        try:
                            from .history_trends import (
                                matched_target_switching_baseline_for_user,
                            )

                            comparison = await matched_target_switching_baseline_for_user(
                                str(job["user_id"]),
                                result,
                                list(switching_result.get("metrics") or {}),
                            )
                        except Exception as error:
                            log.warning(
                                "target switching baseline unavailable session=%s error=%s",
                                sid,
                                type(error).__name__,
                            )
                        else:
                            if comparison.get("comparable") is True:
                                switching_result = copy.deepcopy(switching_result)
                                switching_result["comparison"] = comparison
                                result = _build_target_switching_result_v2(
                                    job,
                                    switching_result,
                                    visual_result,
                                    created_at=created_at_iso,
                                    completed_at=completed_at_iso,
                                )
            cost = 0.0
        elif input_mode in {"input_native", "multimodal"}:
            await queue.set_task_phase(sid, "computing_kinematics", worker_id=WORKER_ID)
            if input_mode == "multimodal":
                native_result, frozen_stats = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                    return_parsed_stats=True,
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
            else:
                native_result = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
                frozen_stats = None
            if isinstance(native_result, Mapping) and isinstance(
                native_result.get("calibration"), Mapping
            ):
                job["calibration_snapshot"] = dict(native_result["calibration"])
                snapshot = job.get("input_snapshot")
                if isinstance(snapshot, dict):
                    snapshot["calibration"] = dict(native_result["calibration"])
            video_availability = None
            warnings: list[dict] = []
            visual_validation = None
            if input_mode == "multimodal":
                await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
                snapshot = job.get("input_snapshot") or {}
                if snapshot.get("schema_version") in {
                    "analysis_input_snapshot.v2", "analysis_input_snapshot.v3",
                }:
                    video_availability = "available"
                    try:
                        visual_result = await run_visual_preprocessing_isolated(job)
                        visual_validation = visual_result["safe_summary"]
                    except Exception as error:
                        from kovaak_tracker.visual_signals import (
                            VisualPreprocessingUnavailable,
                        )

                        await asyncio.to_thread(
                            _assert_managed_video_matches_snapshot,
                            job,
                            input_mode,
                        )
                        if isinstance(error, VisualPreprocessingUnavailable):
                            limitation = error.code
                        else:
                            limitation = "visual_preprocessing_failed"
                        visual_validation = _unavailable_visual_summary(limitation)
                        warnings.append({"code": "video_cv_unavailable"})
                        log.warning(
                            "multimodal visual preprocessing unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                else:
                    try:
                        stats_path = (snapshot.get("sources") or {}).get(
                            "stats", {}
                        ).get("path")
                        if not isinstance(stats_path, str):
                            raise ValueError("multimodal analysis requires stats source")
                        _, visual_extras = await asyncio.to_thread(
                            run_analysis,
                            job["video_path"],
                            stats_path,
                            job.get("cm_per_360"),
                            job.get("fov"),
                            stats=frozen_stats,
                        )
                        video_availability = "available"
                        visual_validation = {
                            "status": "available",
                            "timeline": _build_timeline(visual_extras),
                        }
                    except SourceSnapshotChangedError:
                        raise
                    except Exception:
                        await asyncio.to_thread(
                            _assert_managed_video_matches_snapshot,
                            job,
                            input_mode,
                        )
                        log.warning("multimodal video validation unavailable session=%s", sid)
                        video_availability = "unavailable"
                        warnings.append({"code": "video_cv_unavailable"})
            result = _build_native_result_v2(
                job,
                native_result,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                video_availability=video_availability,
                warnings=warnings,
                visual_validation=visual_validation,
            )
            cost = 0.0
        else:
            await queue.set_task_phase(sid, "computing_kinematics", worker_id=WORKER_ID)
            try:
                summary, extras = await asyncio.to_thread(
                    run_analysis,
                    job["video_path"],
                    job["csv_path"],
                    job.get("cm_per_360"),
                    job.get("fov"),
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
                if isinstance(extras, Mapping) and isinstance(
                    extras.get("calibration"), Mapping
                ):
                    job["calibration_snapshot"] = dict(extras["calibration"])
                    snapshot = job.get("input_snapshot")
                    if isinstance(snapshot, dict):
                        snapshot["calibration"] = dict(extras["calibration"])
                else:
                    job["calibration_snapshot"] = resolve_calibration_v1(
                        stats=None,
                        manual_override=_manual_override_or_legacy(
                            manual_override,
                            cm_per_360=job.get("cm_per_360"),
                            fov=job.get("fov"),
                        ),
                        profile_default=profile_default,
                    )
            except Exception as error:
                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                log.warning(
                    "video fallback analysis unavailable session=%s error=%s",
                    sid,
                    type(error).__name__,
                )
                raise RuntimeError("video fallback analysis failed") from None
            await asyncio.to_thread(
                _assert_managed_video_matches_snapshot,
                job,
                input_mode,
            )
            summary = dict(summary)
            sparc_distribution = summary.get("sparc")
            if isinstance(sparc_distribution, dict):
                summary["sparc"] = {
                    **sparc_distribution,
                    "metric_version": _VIDEO_FALLBACK_SPARC_METRIC_VERSION,
                }
            timeline_events = _build_timeline(extras)
            report_dict = await asyncio.to_thread(run_report, summary)
            cost = 0.0

            result = _build_video_fallback_result_v2(
                job,
                summary,
                report_dict,
                timeline_events,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                narration_status="not_requested",
            )
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )
        await queue.set_task_phase(sid, "generating_diagnostics", worker_id=WORKER_ID)
        if (
            scenario_dispatch == CONTINUOUS_TRACKING_ANALYSIS_VERSION
            and isinstance(visual_result, dict)
            and isinstance(tracking_result, dict)
        ):
            result = await commit_continuous_tracking_evidence_isolated(
                job,
                result,
                visual_result,
                tracking_result,
            )
        else:
            result = await asyncio.to_thread(
                _maybe_commit_analysis_evidence,
                job,
                result,
                parsed_stats=frozen_stats,
                native_result=(native_result if "native_result" in locals() else None),
                visual_result=visual_result,
                dynamic_result=dynamic_result,
                tracking_result=tracking_result,
                switching_result=switching_result,
                outcome_event_bundle=outcome_event_bundle,
            )
        marked_done = await queue.mark_done(sid, result, cost, worker_id=WORKER_ID)
        if not marked_done:
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        else:
            try:
                await _record_profile_contribution(job, result)
            except Exception as error:
                log.warning(
                    "aiming profile contribution unavailable session=%s error=%s",
                    sid,
                    type(error).__name__,
                )
        # 视频保留——coach 回放 + 失败重试；用户删除走 History 删除语义。
    except SourceSnapshotChangedError:
        log.warning("analysis source unavailable session=%s code=source_unavailable", sid)
        error_v1 = build_error_v1(
            category="input_validation",
            code="source_unavailable",
            message="分析输入源已不可用或已变更，请重新提交分析。",
            retryable=False,
            trace_id=None,
        )
        await queue.set_failure_domain(sid, "source_file")
        if not await queue.mark_failed(sid, error_v1, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
    except Exception:
        trace_id = str(uuid.uuid4())
        log.exception("分析失败 session=%s trace_id=%s", sid, trace_id)
        error_v1 = build_error_v1(
            category="internal_unknown",
            code="analysis_failed",
            message="分析失败，请重试；若持续失败请联系维护者。",
            retryable=True,
            trace_id=trace_id,
        )
        domain = "video" if input_mode == "multimodal" else "kinematics"
        await queue.set_failure_domain(sid, domain)
        if not await queue.mark_failed(sid, error_v1, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        # 不删输入文件：支持用户 retry；与「用户自己删」产品决定一致。
    finally:
        stop_hb.set()
        try:
            await hb_task
        except Exception:
            log.exception("heartbeat task join failed session=%s", sid)
    return True

async def _run_loop_async() -> None:
    """单 event loop 跑消费循环(db._conn 不跨 loop)。"""
    while True:
        try:
            handled = await process_one()
        except Exception:
            log.exception("process_one 异常")
            handled = False
        if not handled:
            try:
                await queue.recover_stale_jobs()
            except Exception:
                log.exception("idle recover_stale_jobs 失败")
            await asyncio.sleep(2)


def run_loop() -> None:
    """阻塞消费循环入口(worker 进程 main)。"""
    asyncio.run(_run_loop_async())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    log.info("Aiming Cookie worker 启动")
    run_loop()
