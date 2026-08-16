from __future__ import annotations

import math
import re
from collections.abc import Mapping

from .contracts import validate_scenario_resolution_v1
from .worker_source_validation import (
    SourceSnapshotChangedError,
    _assert_managed_video_matches_snapshot,
)

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


def video_decode_preroll_ms(job: dict) -> float | None:
    """Extract the capture receipt decode preroll in ms, if the job carries it."""
    receipt = job.get("video_receipt")
    replay = receipt.get("replay") if isinstance(receipt, Mapping) else None
    preroll_100ns = (
        replay.get("decodePreroll100ns") if isinstance(replay, Mapping) else None
    )
    if (
        isinstance(preroll_100ns, bool)
        or not isinstance(preroll_100ns, int)
        or preroll_100ns < 0
    ):
        return None
    return preroll_100ns / 10_000.0


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


def _run_owned_visual_video_time_mapping_v2(job: dict) -> dict:
    """Build the preroll-carrying mapping for the generic visual producer.

    Requires the capture receipt preroll: without it the visible window origin
    is unproven, so the caller fails closed instead of guessing an offset.
    """
    mapping = _run_owned_visual_video_time_mapping(job)
    preroll_ms = video_decode_preroll_ms(job)
    if preroll_ms is None:
        raise ValueError("video decode preroll is unavailable")
    return {
        **mapping,
        "schema_version": "visual_video_time_mapping.v2",
        "decode_preroll_ms": preroll_ms,
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
