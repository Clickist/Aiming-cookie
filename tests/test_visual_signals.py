"""Tests-first contract for local visual numerical preprocessing.

These fixtures intentionally contain only synthetic detector observations.  The
visual producer may read MP4 frames locally, but its public result must consist
of time-aligned numeric tracks, quality state and events only.
"""

from __future__ import annotations

import math
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import cv2
import numpy as np
import pytest

from kovaak_tracker.analysis_evidence import build_analysis_evidence_artifact_v1
from kovaak_tracker.vision import detect_ball_by_color, detect_color_blobs
from kovaak_tracker.visual_signals import (
    ROUND_DETECTOR_MIN_CIRCULARITY,
    VISUAL_PRODUCER_ID,
    VISUAL_PRODUCER_VERSION,
    VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
    VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
    VISUAL_TEMPORAL_PRODUCER_ID,
    VISUAL_TEMPORAL_PRODUCER_VERSION,
    build_visual_quality_profile_v2,
    build_visual_annotation_ledger_v1,
    detect_color_candidates_v1,
    detect_color_observations_v2,
    evaluate_visual_annotation_quality_v1,
    evaluate_visual_runtime_compatibility_v2,
    extend_analysis_evidence_with_visual_signals_v1,
    preprocess_visual_video_single_target_csrt_v1,
    preprocess_visual_video_temporal_v1,
    preprocess_visual_video_v1,
    preprocess_visual_signals_v1,
    visual_detector_config_ref_v1,
)


def _candidate_dynamic_profile() -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "visual_signals"
        / "dynamic_clicking_candidate_profile.center_overlay.v2.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def _candidate_detector_config() -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "visual_signals"
        / "dynamic_clicking_candidate_detector_config.v2.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def _window() -> dict:
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 1_000,
        "end_ms": 1_080,
        "duration_ms": 80,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _selector(**overrides: object) -> dict:
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "fixture-scenario-hash",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }
    selector.update(overrides)
    return selector


def _profile() -> dict:
    return build_visual_quality_profile_v2(
        producer_id="synthetic_detector",
        producer_version="synthetic_detector.v1",
        annotation_set_ref="annotation-set:synthetic.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": "detector-config:synthetic.v1",
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["synthetic"],
            "annotated_target_appearance_labels": ["red-round"],
        },
        validated_selectors=[_selector()],
        required_selector_keys_by_metric_family={
            family: [
                "scenario_hash",
                "resolution",
                "canonical_video_mapping_version",
            ]
            for family in ("dynamic_clicking", "tracking", "switching")
        },
        required_quality_fields_by_metric_family={
            "dynamic_clicking": [
                "center_error_median_px",
                "center_error_p95_px",
                "radius_or_hitbox_error_px",
                "false_positive_rate",
                "minimum_coverage",
            ],
            "tracking": [
                "center_error_median_px",
                "center_error_p95_px",
                "radius_or_hitbox_error_px",
                "false_positive_rate",
                "identity_switch_rate",
                "occlusion_reentry_accuracy",
                "minimum_coverage",
            ],
            "switching": [
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
            "center_error_median_px": 2.0,
            "center_error_p95_px": 4.0,
            "radius_or_hitbox_error_px": 2.0,
            "false_positive_rate": 0.05,
            "identity_switch_rate": 0.01,
            "occlusion_reentry_accuracy": 0.95,
            "minimum_coverage": 0.90,
        },
        validation_results={
            "center_error_median_px": 1.0,
            "center_error_p95_px": 2.0,
            "radius_or_hitbox_error_px": 1.0,
            "false_positive_rate": 0.0,
            "identity_switch_rate": 0.0,
            "occlusion_reentry_accuracy": 1.0,
            "minimum_coverage": 1.0,
        },
        validated_metric_families=["dynamic_clicking", "tracking", "switching"],
        status="accepted",
        limitations=[],
    )


def _temporal_profile(detector_config: dict, selector: dict) -> dict:
    base = _profile()
    return build_visual_quality_profile_v2(
        producer_id=VISUAL_TEMPORAL_PRODUCER_ID,
        producer_version=VISUAL_TEMPORAL_PRODUCER_VERSION,
        annotation_set_ref="annotation-set:synthetic-temporal.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": visual_detector_config_ref_v1(detector_config),
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["synthetic"],
            "annotated_target_appearance_labels": ["black-round"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "tracking": base["required_quality_fields_by_metric_family"]["tracking"],
        },
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds=base["acceptance_thresholds"],
        validation_results=base["validation_results"],
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[],
    )


def _single_target_csrt_profile(detector_config: dict, selector: dict) -> dict:
    base = _profile()
    return build_visual_quality_profile_v2(
        producer_id=VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID,
        producer_version=VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION,
        annotation_set_ref="annotation-set:synthetic-single-target-csrt.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": visual_detector_config_ref_v1(detector_config),
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["synthetic"],
            "annotated_target_appearance_labels": ["black-round"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
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
        acceptance_thresholds=base["acceptance_thresholds"],
        validation_results=base["validation_results"],
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[],
    )


def _frame(
    pts_ms: int | None,
    *,
    crosshair: tuple[float, float] | None = (100.0, 100.0),
    targets: list[dict] | None = None,
    scene: str = "gameplay",
) -> dict:
    return {
        "source_pts_ms": pts_ms,
        "crosshair": None if crosshair is None else {"x": crosshair[0], "y": crosshair[1]},
        "targets": targets or [],
        "scene": scene,
    }


def _target(detector_ref: str | None, x: float, y: float, radius: float) -> dict:
    return {
        "detector_ref": detector_ref,
        "x": x,
        "y": y,
        "visible_radius": radius,
        "confidence": 1.0,
    }


def _preprocess(frames: list[dict], **overrides: object) -> dict:
    args = {
        "analysis_ref": "analysis:visual-test",
        "canonical_time_window": _window(),
        "frame_observations": frames,
        "visual_quality_profile": _profile(),
        "visual_runtime_selector": _selector(),
        "video_time_mapping": {
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": _window()["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": _window()["timebase_version"],
        },
    }
    args.update(overrides)
    return preprocess_visual_signals_v1(**args)


def _walk(value: object) -> Sequence[object]:
    if isinstance(value, Mapping):
        return [item for pair in value.items() for item in (*pair, *_walk(pair[1]))]
    if isinstance(value, list):
        return [item for entry in value for item in (entry, *_walk(entry))]
    return []


def test_variable_pts_maps_to_canonical_times_and_missing_pts_is_partial():
    result = _preprocess([
        _frame(0, targets=[_target("red-1", 110, 100, 12)]),
        _frame(17, targets=[_target("red-1", 114, 100, 12)]),
        _frame(None, targets=[_target("red-1", 118, 100, 12)]),
        _frame(51, targets=[_target("red-1", 122, 100, 12)]),
    ])

    assert result["schema_version"] == "visual_signal_artifact.v1"
    assert result["canonical_time_window"] == _window()
    assert result["completeness"] == "partial"
    assert "missing_frame_pts" in result["limitations"]
    samples = result["local_samples"]["crosshair.position"]
    assert [sample["canonical_time_ms"] for sample in samples] == [1_000, 1_017, 1_051]
    assert all(_window()["start_ms"] <= sample["canonical_time_ms"] < _window()["end_ms"] for sample in samples)
    assert result["safe_summary"]["quality_status"] == "limited"
    assert result["safe_summary"]["target_coverage"] == pytest.approx(0.75)
    assert result["safe_summary"]["crosshair_coverage"] == pytest.approx(0.75)
    assert all(channel["coverage"] == pytest.approx(0.75) for channel in result["signal_bundle"]["channels"])


def test_video_time_mapping_applies_explicit_pts_offset_and_rejects_duplicates():
    result = _preprocess(
        [
            _frame(100, targets=[_target("red-1", 110, 100, 12)]),
            _frame(100, targets=[_target("red-1", 114, 100, 12)]),
            _frame(117, targets=[_target("red-1", 118, 100, 12)]),
        ],
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 100.0,
            "canonical_origin_ms": 1_020,
            "mapping_method": "validated_fixture_offset",
            "timebase_version": "test.v1",
        },
    )

    assert result["completeness"] == "partial"
    assert "non_monotonic_frame_pts" in result["limitations"]
    assert result["safe_summary"]["crosshair_coverage"] == pytest.approx(2 / 3)
    assert [
        sample["canonical_time_ms"]
        for sample in result["local_samples"]["crosshair.position"]
    ] == [1_020, 1_037]


def test_tracks_preserve_crosshair_target_radius_and_known_change_point():
    result = _preprocess([
        _frame(0, crosshair=(100, 100), targets=[_target("red-1", 110, 100, 12)]),
        _frame(16, crosshair=(102, 100), targets=[_target("red-1", 118, 100, 12)]),
        _frame(32, crosshair=(104, 100), targets=[_target("red-1", 112, 100, 12)]),
    ])

    track = result["track_summaries"][0]
    assert track["track_ref"] == "analysis:visual-test:target-track:1"
    assert track["identity_source"] == "detector_ref"
    assert track["visible_radius_px"] == 12.0
    assert track["sample_count"] == 3
    channels = {channel["channel_key"] for channel in result["signal_bundle"]["channels"]}
    assert {"crosshair.position_x", "crosshair.position_y", "target.1.position_x", "target.1.position_y", "target.1.visible_radius"} <= channels
    change_points = [event for event in result["event_bundle"]["events"] if event["event_kind"] == "target_change_point"]
    assert [(event["start_ms"], event["attributes"]["change_kind"]) for event in change_points] == [(1_032, "direction_reversal")]
    assert change_points[0]["limitations"] == [
        "change_point_not_independently_validated",
    ]


def test_change_points_ignore_validated_localization_noise_and_stay_bounded():
    window = {
        **_window(),
        "end_ms": 2_000,
        "duration_ms": 1_000,
    }
    mapping = {
        "schema_version": "visual_video_time_mapping.v1",
        "source_pts_origin_ms": 0.0,
        "canonical_origin_ms": window["start_ms"],
        "mapping_method": "run_owned_exact_canonical_clip",
        "timebase_version": window["timebase_version"],
    }
    noisy = _preprocess(
        [
            _frame(
                pts_ms,
                targets=[_target("red-1", 100 + (pts_ms % 2), 100, 12)],
            )
            for pts_ms in range(515)
        ],
        canonical_time_window=window,
        video_time_mapping=mapping,
    )
    assert not [
        event
        for event in noisy["event_bundle"]["events"]
        if event["event_kind"] == "target_change_point"
    ]

    high_frequency = _preprocess(
        [
            _frame(
                pts_ms,
                targets=[_target("red-1", 100 + 10 * (pts_ms % 2), 100, 12)],
            )
            for pts_ms in range(600)
        ],
        canonical_time_window=window,
        video_time_mapping=mapping,
    )
    assert len(high_frequency["event_bundle"]["events"]) == 512
    assert high_frequency["completeness"] == "partial"
    assert high_frequency["quality"]["status"] == "limited"
    assert "visual_event_budget_exceeded" in high_frequency["limitations"]


def test_multiple_target_state_events_coalesce_when_detector_order_changes():
    window = {
        **_window(),
        "end_ms": 2_000,
        "duration_ms": 1_000,
    }
    result = _preprocess(
        [
            _frame(
                pts_ms,
                targets=(
                    [
                        _target("red-1", 110, 100, 12),
                        _target("red-2", 150, 100, 12),
                    ]
                    if index % 2 == 0
                    else [
                        _target("red-2", 150, 100, 12),
                        _target("red-1", 110, 100, 12),
                    ]
                ),
            )
            for index, pts_ms in enumerate(range(0, 1_920, 16))
        ],
        canonical_time_window=window,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )

    candidate_events = [
        event
        for event in result["event_bundle"]["events"]
        if event["event_kind"] == "candidate_visible"
    ]
    assert len(candidate_events) == 1
    assert candidate_events[0]["actor_refs"] == []
    assert "visual_event_budget_exceeded" not in result["limitations"]


def test_annotation_recall_threshold_is_not_reused_as_target_presence_rate():
    result = _preprocess(
        [
            _frame(0, targets=[_target("red-1", 110, 100, 12)]),
            _frame(16, targets=[]),
        ],
        canonical_time_window={
            **_window(),
            "end_ms": 1_040,
            "duration_ms": 40,
        },
    )

    assert result["safe_summary"]["target_coverage"] == 0.5
    assert "dynamic_clicking" in result["quality"]["enabled_metric_families"]
    assert "visual_quality_below_threshold:target_coverage" not in result[
        "limitations"
    ]


def test_fixed_viewport_aim_point_requires_complete_frame_coverage():
    result = _preprocess([
        _frame(0, crosshair=(100, 100), targets=[_target("red-1", 110, 100, 12)]),
        _frame(16, crosshair=None, targets=[_target("red-1", 110, 100, 12)]),
    ])

    assert result["safe_summary"]["crosshair_coverage"] == 0.5
    assert result["quality"]["enabled_metric_families"] == []
    assert "visual_quality_below_threshold:crosshair_coverage" in result[
        "limitations"
    ]


@pytest.mark.parametrize(
    ("frames", "expected_event", "expected_limitation"),
    [
        ([_frame(0, targets=[_target("red-1", 110, 100, 12)]), _frame(16, targets=[]), _frame(32, targets=[_target("red-1", 114, 100, 12)])], "reacquired", "target_occlusion"),
        ([_frame(0, targets=[]), _frame(16, targets=[])], "low_confidence", "no_target_visible"),
        ([_frame(0, targets=[_target("red-1", 110, 100, 12), _target("red-2", 150, 100, 12)])], "candidate_visible", "multiple_targets_visible"),
    ],
)
def test_occlusion_no_target_and_multiple_target_states_are_explicit(frames, expected_event, expected_limitation):
    result = _preprocess(frames)

    assert expected_limitation in result["limitations"]
    assert expected_event in {event["event_kind"] for event in result["event_bundle"]["events"]}
    assert all(event["confidence"] < 1.0 for event in result["event_bundle"]["events"] if expected_limitation in event["limitations"])


def test_contiguous_frame_states_are_coalesced_into_bounded_intervals():
    window = {
        **_window(),
        "end_ms": 2_000,
        "duration_ms": 1_000,
    }
    result = _preprocess(
        [_frame(pts_ms, targets=[]) for pts_ms in range(600)],
        canonical_time_window=window,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )

    low_confidence = [
        event
        for event in result["event_bundle"]["events"]
        if event["event_kind"] == "low_confidence"
    ]
    assert len(low_confidence) == 1
    assert (low_confidence[0]["start_ms"], low_confidence[0]["end_ms"]) == (
        1_000,
        1_599,
    )


def test_state_intervals_do_not_bridge_unobserved_frame_gaps():
    window = {
        **_window(),
        "end_ms": 2_000,
        "duration_ms": 1_000,
    }
    result = _preprocess(
        [_frame(0, targets=[]), _frame(500, targets=[])],
        canonical_time_window=window,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )

    low_confidence = [
        event
        for event in result["event_bundle"]["events"]
        if event["event_kind"] == "low_confidence"
    ]
    assert [(event["start_ms"], event["end_ms"]) for event in low_confidence] == [
        (1_000, 1_000),
        (1_500, 1_500),
    ]
    assert result["completeness"] == "partial"
    assert result["quality"]["status"] == "limited"
    assert "visual_frame_gap" in result["limitations"]


@pytest.mark.parametrize("frame_pts", [[0, 16], [48, 64]])
def test_window_boundary_frame_gaps_are_partial_and_disable_metric_families(
    frame_pts,
):
    result = _preprocess([
        _frame(pts_ms, targets=[_target("red-1", 110 + index, 100, 12)])
        for index, pts_ms in enumerate(frame_pts)
    ])

    assert result["completeness"] == "partial"
    assert result["quality"]["status"] == "limited"
    assert result["quality"]["enabled_metric_families"] == []
    assert "visual_frame_boundary_gap" in result["limitations"]
    assert "visual_quality_below_threshold:frame_coverage" in result["quality"]["limitations"]


def test_one_missing_boundary_frame_cannot_remain_complete():
    window = {**_window(), "end_ms": 1_064, "duration_ms": 64}
    result = _preprocess(
        [
            _frame(pts_ms, targets=[_target("red-1", 110 + index, 100, 12)])
            for index, pts_ms in enumerate([0, 16, 32])
        ],
        canonical_time_window=window,
    )

    assert result["completeness"] == "partial"
    assert result["quality"]["enabled_metric_families"] == []
    assert "visual_frame_boundary_gap" in result["limitations"]


def test_single_frame_window_has_unverifiable_boundary_coverage():
    window = {**_window(), "end_ms": 1_010, "duration_ms": 10}
    result = _preprocess(
        [_frame(0, targets=[_target("red-1", 110, 100, 12)])],
        canonical_time_window=window,
    )

    assert result["completeness"] == "partial"
    assert result["quality"]["enabled_metric_families"] == []
    assert "visual_frame_boundary_unverifiable" in result["limitations"]


def test_crossing_and_reentry_have_deterministic_identity_without_selection_claim():
    frames = [
        _frame(0, targets=[_target("left", 80, 100, 10), _target("right", 120, 100, 10)]),
        _frame(16, targets=[_target("left", 105, 100, 10), _target("right", 95, 100, 10)]),
        _frame(32, targets=[]),
        _frame(48, targets=[_target("left", 130, 100, 10), _target("right", 70, 100, 10)]),
    ]
    first = _preprocess(frames)
    second = _preprocess(frames)

    assert first["track_summaries"] == second["track_summaries"]
    assert [track["track_ref"] for track in first["track_summaries"]] == [
        "analysis:visual-test:target-track:1", "analysis:visual-test:target-track:2",
    ]
    assert "identity_crossing_ambiguous" in first["limitations"]
    assert "target_selected" not in {event["event_kind"] for event in first["event_bundle"]["events"]}
    assert "reacquired" in {event["event_kind"] for event in first["event_bundle"]["events"]}
    assert first["quality"]["status"] == "limited"
    assert first["quality"]["enabled_metric_families"] == []


def test_runtime_identity_ambiguity_only_disables_dependent_metric_families():
    result = _preprocess([
        _frame(0, targets=[_target("left", 80, 100, 10), _target("right", 120, 100, 10)]),
        _frame(16, targets=[_target("left", 90, 100, 10), _target("right", 110, 100, 10)]),
        _frame(32, targets=[_target("left", 105, 100, 10), _target("right", 95, 100, 10)]),
        _frame(48, targets=[_target("left", 115, 100, 10), _target("right", 85, 100, 10)]),
        _frame(64, targets=[_target("left", 125, 100, 10), _target("right", 75, 100, 10)]),
    ])

    assert "identity_crossing_ambiguous" in result["limitations"]
    assert result["quality"]["status"] == "limited"
    assert result["quality"]["enabled_metric_families"] == ["dynamic_clicking"]
    assert (
        "visual_quality_below_threshold:tracking:identity_continuity"
        in result["quality"]["limitations"]
    )
    assert (
        "visual_quality_below_threshold:switching:identity_continuity"
        in result["quality"]["limitations"]
    )


def test_unkeyed_target_reentry_after_occlusion_never_claims_reliable_identity():
    result = _preprocess([
        _frame(0, targets=[_target(None, 110, 100, 12)]),
        _frame(16, targets=[]),
        _frame(32, targets=[_target(None, 114, 100, 12)]),
    ])

    assert "reentry_identity_unresolved" in result["limitations"]
    assert "reacquired" not in {event["event_kind"] for event in result["event_bundle"]["events"]}
    assert result["quality"]["status"] == "limited"
    assert result["quality"]["enabled_metric_families"] == []
    assert "visual_quality_below_threshold:occlusion_reentry" in result["quality"]["limitations"]
    assert all(
        "reentry_identity_unresolved" in track["limitations"]
        for track in result["track_summaries"]
    )


def test_non_gameplay_scene_breaks_unkeyed_target_identity_continuity():
    result = _preprocess([
        _frame(0, targets=[_target(None, 110, 100, 12)]),
        _frame(16, targets=[], scene="results_ui"),
        _frame(32, targets=[_target(None, 114, 100, 12)]),
    ])

    assert len(result["track_summaries"]) == 2
    assert "non_gameplay_scene" in result["limitations"]
    assert "reentry_identity_unresolved" in result["limitations"]
    assert result["quality"]["status"] == "limited"
    assert result["quality"]["enabled_metric_families"] == []


def test_quality_profile_and_runtime_selector_gate_each_metric_family_fail_closed():
    profile = _profile()
    assert profile["schema_version"] == "visual_quality_profile.v2"
    assert profile["annotation_protocol_version"] == "visual_annotation_protocol.v1"
    assert profile["validated_metric_families"] == ["dynamic_clicking", "tracking", "switching"]

    accepted = evaluate_visual_runtime_compatibility_v2(profile, _selector())
    assert accepted == {
        "status": "accepted",
        "enabled_metric_families": ["dynamic_clicking", "tracking", "switching"],
        "limitations": [],
    }
    mismatched = evaluate_visual_runtime_compatibility_v2(
        profile, _selector(scenario_hash="another-scenario-hash")
    )
    assert mismatched["status"] in {"limited", "rejected"}
    assert mismatched["enabled_metric_families"] == []
    assert all(
        f"visual_selector_mismatch:{family}:scenario_hash" in mismatched["limitations"]
        for family in ("dynamic_clicking", "tracking", "switching")
    )

    fov_profile = build_visual_quality_profile_v2(
        producer_id=profile["producer_id"],
        producer_version=profile["producer_version"],
        annotation_set_ref=profile["annotation_set_ref"],
        annotation_protocol_version=profile["annotation_protocol_version"],
        coordinate_space=profile["coordinate_space"],
        calibration_context=profile["calibration_context"],
        validated_selectors=profile["validated_selectors"],
        required_selector_keys_by_metric_family={
            "dynamic_clicking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version", "fov",
            ],
            "switching": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family=(
            profile["required_quality_fields_by_metric_family"]
        ),
        compatibility_predicate_version=profile["compatibility_predicate_version"],
        acceptance_thresholds=profile["acceptance_thresholds"],
        validation_results=profile["validation_results"],
        validated_metric_families=profile["validated_metric_families"],
        status="accepted",
        limitations=[],
    )
    missing_fov = evaluate_visual_runtime_compatibility_v2(
        fov_profile, _selector(fov=None)
    )
    assert missing_fov["status"] == "limited"
    assert missing_fov["enabled_metric_families"] == [
        "dynamic_clicking", "switching",
    ]
    assert missing_fov["limitations"] == [
        "visual_selector_mismatch:tracking:fov",
    ]

    bad_results = dict(profile["validation_results"])
    bad_results["identity_switch_rate"] = 0.2
    with pytest.raises(ValueError, match="status disagrees"):
        build_visual_quality_profile_v2(
            producer_id=profile["producer_id"],
            producer_version=profile["producer_version"],
            annotation_set_ref=profile["annotation_set_ref"],
            annotation_protocol_version=profile["annotation_protocol_version"],
            coordinate_space=profile["coordinate_space"],
            calibration_context=profile["calibration_context"],
            validated_selectors=profile["validated_selectors"],
            required_selector_keys_by_metric_family=(
                profile["required_selector_keys_by_metric_family"]
            ),
            required_quality_fields_by_metric_family=(
                profile["required_quality_fields_by_metric_family"]
            ),
            compatibility_predicate_version=profile["compatibility_predicate_version"],
            acceptance_thresholds=profile["acceptance_thresholds"],
            validation_results=bad_results,
            validated_metric_families=profile["validated_metric_families"],
            status="accepted",
            limitations=[],
        )


def test_field_reviewed_candidate_profile_is_dynamic_only_and_detector_versioned():
    profile = _candidate_dynamic_profile()

    assert ROUND_DETECTOR_MIN_CIRCULARITY == pytest.approx(0.60)
    assert profile["producer_id"] == VISUAL_PRODUCER_ID
    assert profile["producer_version"] == VISUAL_PRODUCER_VERSION
    detector_config = _candidate_detector_config()
    assert visual_detector_config_ref_v1(detector_config) == profile[
        "calibration_context"
    ]["detector_config_ref"]
    detector_config["target"]["hsv_upper"][2] = 131
    assert visual_detector_config_ref_v1(detector_config) != profile[
        "calibration_context"
    ]["detector_config_ref"]
    assert profile["validated_metric_families"] == ["dynamic_clicking"]
    assert set(profile["required_quality_fields_by_metric_family"]) == {
        "dynamic_clicking",
    }
    assert "identity_switch_rate" not in profile[
        "required_quality_fields_by_metric_family"
    ]["dynamic_clicking"]
    assert "occlusion_reentry_accuracy" not in profile[
        "required_quality_fields_by_metric_family"
    ]["dynamic_clicking"]

    accepted = evaluate_visual_runtime_compatibility_v2(
        profile,
        profile["validated_selectors"][0],
    )
    assert accepted == {
        "status": "accepted",
        "enabled_metric_families": ["dynamic_clicking"],
        "limitations": profile["limitations"],
    }
    assert evaluate_visual_runtime_compatibility_v2(
        profile,
        {
            **profile["validated_selectors"][0],
            "scenario_hash": "different-scenario-hash",
        },
    )["enabled_metric_families"] == []


def test_candidate_profile_cannot_claim_unobserved_identity_quality():
    profile = _candidate_dynamic_profile()
    profile["validated_metric_families"] = ["dynamic_clicking", "tracking"]
    profile["required_selector_keys_by_metric_family"]["tracking"] = [
        "scenario_hash",
        "resolution",
        "canonical_video_mapping_version",
    ]
    profile["required_quality_fields_by_metric_family"]["tracking"] = [
        "center_error_median_px",
        "center_error_p95_px",
        "radius_or_hitbox_error_px",
        "false_positive_rate",
        "identity_switch_rate",
        "occlusion_reentry_accuracy",
        "minimum_coverage",
    ]
    profile["quality_status_by_metric_family"]["tracking"] = "rejected"
    profile["status"] = "limited"

    with pytest.raises(ValueError, match="status disagrees"):
        # The profile builder is the canonical validator for this fixture.
        build_visual_quality_profile_v2(
            producer_id=profile["producer_id"],
            producer_version=profile["producer_version"],
            annotation_set_ref=profile["annotation_set_ref"],
            annotation_protocol_version=profile["annotation_protocol_version"],
            coordinate_space=profile["coordinate_space"],
            calibration_context=profile["calibration_context"],
            validated_selectors=profile["validated_selectors"],
            required_selector_keys_by_metric_family=(
                profile["required_selector_keys_by_metric_family"]
            ),
            required_quality_fields_by_metric_family=(
                profile["required_quality_fields_by_metric_family"]
            ),
            compatibility_predicate_version=profile[
                "compatibility_predicate_version"
            ],
            acceptance_thresholds=profile["acceptance_thresholds"],
            validation_results=profile["validation_results"],
            validated_metric_families=profile["validated_metric_families"],
            status="accepted",
            limitations=profile["limitations"],
        )


def test_quality_profile_gates_each_metric_family_by_its_required_quality_fields():
    profile = _profile()
    bad_results = dict(profile["validation_results"])
    bad_results["identity_switch_rate"] = 0.2
    bad_results["occlusion_reentry_accuracy"] = None
    quality_fields = {
        "dynamic_clicking": [
            "center_error_median_px",
            "center_error_p95_px",
            "radius_or_hitbox_error_px",
            "false_positive_rate",
            "minimum_coverage",
        ],
        "tracking": sorted(profile["acceptance_thresholds"]),
        "switching": [
            "center_error_median_px",
            "center_error_p95_px",
            "radius_or_hitbox_error_px",
            "false_positive_rate",
            "identity_switch_rate",
            "minimum_coverage",
        ],
    }
    partial = build_visual_quality_profile_v2(
        producer_id=profile["producer_id"],
        producer_version=profile["producer_version"],
        annotation_set_ref=profile["annotation_set_ref"],
        annotation_protocol_version=profile["annotation_protocol_version"],
        coordinate_space=profile["coordinate_space"],
        calibration_context=profile["calibration_context"],
        validated_selectors=profile["validated_selectors"],
        required_selector_keys_by_metric_family=(
            profile["required_selector_keys_by_metric_family"]
        ),
        required_quality_fields_by_metric_family=quality_fields,
        compatibility_predicate_version=profile["compatibility_predicate_version"],
        acceptance_thresholds=profile["acceptance_thresholds"],
        validation_results=bad_results,
        validated_metric_families=profile["validated_metric_families"],
        status="limited",
        limitations=[],
    )

    assert partial["quality_status_by_metric_family"] == {
        "dynamic_clicking": "accepted",
        "tracking": "limited",
        "switching": "limited",
    }
    result = evaluate_visual_runtime_compatibility_v2(partial, _selector())
    assert result["status"] == "limited"
    assert result["enabled_metric_families"] == ["dynamic_clicking"]
    assert result["limitations"] == [
        "visual_quality_below_threshold:tracking:identity_switch_rate",
        "visual_quality_below_threshold:tracking:occlusion_reentry_accuracy",
        "visual_quality_below_threshold:switching:identity_switch_rate",
    ]


def test_color_detector_excludes_only_reviewed_normalized_regions():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cv2.circle(image, (40, 50), 10, (0, 0, 255), -1)
    cv2.circle(image, (160, 50), 10, (0, 0, 255), -1)

    candidates = detect_color_candidates_v1(
        image,
        hsv_lower=[170, 180, 180],
        hsv_upper=[10, 255, 255],
        min_area=50,
        max_area_ratio=0.05,
        shape="round",
        excluded_regions=[[0.0, 0.0, 0.5, 1.0]],
    )

    assert len(candidates) == 1
    assert candidates[0]["x"] == pytest.approx(160.0, abs=1.0)
    with pytest.raises(ValueError, match="excluded region"):
        detect_color_candidates_v1(
            image,
            hsv_lower=[170, 180, 180],
            hsv_upper=[10, 255, 255],
            min_area=50,
            max_area_ratio=0.05,
            shape="round",
            excluded_regions=[[0.5, 0.0, 0.4, 1.0]],
        )


def test_color_detector_accepts_rasterized_round_targets_but_rejects_thin_glyphs():
    image = np.full((120, 220, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (60, 60), (22, 8), 0, 0, 360, (0, 0, 0), -1)
    cv2.rectangle(image, (140, 57), (168, 63), (0, 0, 0), -1)

    candidates = detect_color_candidates_v1(
        image,
        hsv_lower=[0, 0, 0],
        hsv_upper=[179, 255, 130],
        min_area=100,
        max_area_ratio=0.05,
        shape="round",
    )

    assert len(candidates) == 1
    assert candidates[0]["x"] == pytest.approx(60.0, abs=1.0)
    assert candidates[0]["y"] == pytest.approx(60.0, abs=1.0)


def _center_overlay_target_image(*, center_x: int = 100) -> np.ndarray:
    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    points = []
    for index in range(20):
        angle = -math.pi / 2 + index * math.pi / 10
        radius = 18 if index % 2 == 0 else 11
        points.append([
            round(center_x + radius * math.cos(angle)),
            round(100 + radius * math.sin(angle)),
        ])
    cv2.fillPoly(image, [np.asarray(points, dtype=np.int32)], (0, 0, 0))
    cv2.line(image, (93, 100), (107, 100), (255, 255, 0), 2)
    cv2.line(image, (100, 93), (100, 107), (255, 255, 0), 2)
    return image


def _round_observations(image: np.ndarray) -> dict:
    return detect_color_observations_v2(
        image,
        hsv_lower=[0, 0, 0],
        hsv_upper=[179, 255, 130],
        min_area=50,
        max_area_ratio=0.05,
        shape="round",
    )


def test_center_overlay_single_peak_recovers_low_circularity_target():
    result = _round_observations(_center_overlay_target_image())

    assert result["target_ambiguities"] == []
    assert len(result["targets"]) == 1
    assert result["targets"][0]["x"] == pytest.approx(100.0, abs=2.0)
    assert result["targets"][0]["y"] == pytest.approx(100.0, abs=2.0)
    assert 0.50 <= result["targets"][0]["confidence"] < 0.60


def test_center_overlay_does_not_turn_crosshair_or_noncenter_blob_into_target():
    crosshair_only = np.full((200, 200, 3), 255, dtype=np.uint8)
    cv2.line(crosshair_only, (93, 100), (107, 100), (255, 255, 0), 2)
    cv2.line(crosshair_only, (100, 93), (100, 107), (255, 255, 0), 2)

    assert _round_observations(crosshair_only) == {
        "targets": [],
        "target_ambiguities": [],
    }
    assert _round_observations(_center_overlay_target_image(center_x=50)) == {
        "targets": [],
        "target_ambiguities": [],
    }


def test_center_overlay_merge_is_typed_but_never_trackable():
    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    cv2.circle(image, (91, 100), 18, (0, 0, 0), -1)
    cv2.circle(image, (109, 100), 18, (0, 0, 0), -1)
    cv2.line(image, (88, 100), (112, 100), (255, 255, 0), 3)
    cv2.line(image, (100, 88), (100, 112), (255, 255, 0), 3)

    result = _round_observations(image)

    assert result["targets"] == []
    assert len(result["target_ambiguities"]) == 1
    assert result["target_ambiguities"][0]["ambiguity_kind"] == (
        "merged_target_component"
    )
    assert result["target_ambiguities"][0]["confidence"] == 0.0


def test_merge_breaks_identity_and_reentry_stays_unresolved():
    result = _preprocess([
        _frame(
            0,
            targets=[_target(None, 80, 100, 12), _target(None, 120, 100, 12)],
        ),
        {
            **_frame(16, targets=[]),
            "target_ambiguities": [{
                "ambiguity_kind": "merged_target_component",
                "x": 100.0,
                "y": 100.0,
                "visible_radius": 24.0,
                "confidence": 0.0,
            }],
        },
        _frame(
            32,
            targets=[_target(None, 78, 100, 12), _target(None, 122, 100, 12)],
        ),
    ])

    assert "target_merge_ambiguous" in result["limitations"]
    assert "reentry_identity_unresolved" in result["limitations"]
    assert all(
        "reentry_identity_unresolved" in track["limitations"]
        for track in result["track_summaries"]
    )
    merge_events = [
        event
        for event in result["event_bundle"]["events"]
        if "target_merge_ambiguous" in event["limitations"]
    ]
    assert len(merge_events) == 1
    assert merge_events[0]["event_kind"] == "candidate_visible"
    assert merge_events[0]["actor_refs"] == []
    assert merge_events[0]["confidence"] == 0.0


def test_annotation_quality_evaluator_measures_errors_false_positives_and_identity():
    annotations = [
        {
            "frame_index": 0,
            "targets": [
                {"target_id": "a", "x": 10.0, "y": 10.0, "visible_radius": 5.0},
                {"target_id": "b", "x": 100.0, "y": 100.0, "visible_radius": 8.0},
            ],
        },
        {
            "frame_index": 1,
            "targets": [
                {"target_id": "a", "x": 12.0, "y": 10.0, "visible_radius": 5.0},
                {"target_id": "b", "x": 102.0, "y": 100.0, "visible_radius": 8.0},
            ],
        },
        {
            "frame_index": 2,
            "targets": [
                {"target_id": "b", "x": 104.0, "y": 100.0, "visible_radius": 8.0},
            ],
        },
        {
            "frame_index": 3,
            "targets": [
                {"target_id": "a", "x": 14.0, "y": 10.0, "visible_radius": 5.0},
                {"target_id": "b", "x": 106.0, "y": 100.0, "visible_radius": 8.0},
            ],
        },
    ]
    predictions = [
        {
            "frame_index": 0,
            "targets": [
                {"track_id": "track-a", "x": 11.0, "y": 10.0, "visible_radius": 5.5},
                {"track_id": "track-b", "x": 101.0, "y": 100.0, "visible_radius": 8.5},
                {"track_id": "hud", "x": 50.0, "y": 180.0, "visible_radius": 4.0},
            ],
        },
        {
            "frame_index": 1,
            "targets": [
                {"track_id": "track-a", "x": 13.0, "y": 10.0, "visible_radius": 5.5},
                {"track_id": "track-b", "x": 103.0, "y": 100.0, "visible_radius": 8.5},
            ],
        },
        {
            "frame_index": 2,
            "targets": [
                {"track_id": "track-b", "x": 105.0, "y": 100.0, "visible_radius": 8.5},
            ],
        },
        {
            "frame_index": 3,
            "targets": [
                {"track_id": "track-a", "x": 15.0, "y": 10.0, "visible_radius": 5.5},
                {"track_id": "track-b-new", "x": 107.0, "y": 100.0, "visible_radius": 8.5},
            ],
        },
    ]

    result = evaluate_visual_annotation_quality_v1(
        annotations=annotations,
        predictions=predictions,
        maximum_match_distance_px=10.0,
    )

    assert result["metrics"] == {
        "center_error_median_px": 1.0,
        "center_error_p95_px": 1.0,
        "radius_or_hitbox_error_px": 0.5,
        "false_positive_rate": 0.125,
        "identity_switch_rate": 0.25,
        "occlusion_reentry_accuracy": 1.0,
        "minimum_coverage": 1.0,
    }
    assert result["counts"] == {
        "annotated_targets": 7,
        "predicted_targets": 8,
        "matched_targets": 7,
        "false_positives": 1,
        "identity_comparisons": 4,
        "identity_switches": 1,
        "occlusion_reentries": 1,
        "correct_occlusion_reentries": 1,
    }
    assert result["limitations"] == []


def test_annotation_ledger_is_bounded_reproducible_and_path_free():
    ledger = build_visual_annotation_ledger_v1(
        source_ref="field:task6-normal@2026-07-19",
        source_sha256="a" * 64,
        annotation_protocol_version="visual_annotation_protocol.v1",
        reviewer_ref="reviewer:independent-1",
        review_round=1,
        frames=[{
            "frame_index": 5,
            "targets": [
                {"target_id": "target-1", "x": 100.0, "y": 80.0, "visible_radius": 12.0},
            ],
        }],
    )

    assert ledger == {
        "schema_version": "visual_annotation_ledger.v1",
        "source_ref": "field:task6-normal@2026-07-19",
        "source_sha256": "a" * 64,
        "annotation_protocol_version": "visual_annotation_protocol.v1",
        "reviewer_ref": "reviewer:independent-1",
        "review_round": 1,
        "frames": [{
            "frame_index": 5,
            "targets": [
                {"target_id": "target-1", "x": 100.0, "y": 80.0, "visible_radius": 12.0},
            ],
        }],
    }
    with pytest.raises(ValueError, match="source_ref"):
        build_visual_annotation_ledger_v1(
            source_ref="C:/private/fixture.mp4",
            source_sha256="a" * 64,
            annotation_protocol_version="visual_annotation_protocol.v1",
            reviewer_ref="reviewer:independent-1",
            review_round=1,
            frames=ledger["frames"],
        )


def test_annotation_quality_evaluator_does_not_invent_unobserved_identity_quality():
    result = evaluate_visual_annotation_quality_v1(
        annotations=[{
            "frame_index": 0,
            "targets": [
                {"target_id": "a", "x": 10.0, "y": 10.0, "visible_radius": 5.0},
            ],
        }],
        predictions=[{
            "frame_index": 0,
            "targets": [
                {"track_id": "track-a", "x": 10.0, "y": 10.0, "visible_radius": 5.0},
            ],
        }],
        maximum_match_distance_px=10.0,
    )

    assert result["metrics"]["identity_switch_rate"] is None
    assert result["metrics"]["occlusion_reentry_accuracy"] is None
    assert result["limitations"] == [
        "identity_continuity_not_observed",
        "occlusion_reentry_not_observed",
    ]


def test_annotation_quality_evaluator_maximizes_feasible_matches_before_distance():
    result = evaluate_visual_annotation_quality_v1(
        annotations=[{
            "frame_index": 0,
            "targets": [
                {"target_id": "a", "x": 0.0, "y": 0.0, "visible_radius": 2.0},
                {"target_id": "b", "x": 5.0, "y": 0.0, "visible_radius": 2.0},
            ],
        }],
        predictions=[{
            "frame_index": 0,
            "targets": [
                {"track_id": "track-a", "x": 0.0, "y": 0.0, "visible_radius": 2.0},
                {
                    "track_id": "track-b",
                    "x": 0.5,
                    "y": math.sqrt(15.75),
                    "visible_radius": 2.0,
                },
            ],
        }],
        maximum_match_distance_px=5.0,
    )

    assert result["counts"]["matched_targets"] == 2
    assert result["counts"]["false_positives"] == 0
    assert result["metrics"]["minimum_coverage"] == 1.0


@pytest.mark.parametrize(
    ("requested_level", "association_kind", "availability"),
    [
        ("directly_observed", "directly_observed", "available"),
        ("inferred", "inferred", "partial"),
        ("unavailable", "inferred", "unavailable"),
    ],
)
def test_outcome_association_preserves_supported_v1_evidence_levels(
    requested_level,
    association_kind,
    availability,
):
    result = _preprocess(
        [_frame(0, targets=[_target("red-1", 110, 100, 12)])],
        outcome_observations=[{
            "shot_pts_ms": 0,
            "outcome_pts_ms": 0,
            "detector_ref": "red-1",
            "weapon_temporal_model": "hitscan",
            "association_kind": requested_level,
        }],
    )

    association = result["event_bundle"]["outcome_associations"][0]
    assert association["association_kind"] == association_kind
    assert association["availability"] == availability


def test_validated_aligned_requires_a_future_registered_rule_contract():
    with pytest.raises(ValueError, match="validated aligned outcome association"):
        _preprocess(
            [_frame(0, targets=[_target("red-1", 110, 100, 12)])],
            outcome_observations=[{
                "shot_pts_ms": 0,
                "outcome_pts_ms": 0,
                "detector_ref": "red-1",
                "weapon_temporal_model": "hitscan",
                "association_kind": "validated_aligned",
                "validation_ref": "association-rule:fixture.v1",
            }],
        )


def test_outcome_observations_share_the_video_pts_offset_mapping():
    result = _preprocess(
        [
            _frame(100, targets=[_target("red-1", 110, 100, 12)]),
            _frame(117, targets=[_target("red-1", 114, 100, 12)]),
        ],
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 100.0,
            "canonical_origin_ms": 1_020,
            "mapping_method": "validated_fixture_offset",
            "timebase_version": "test.v1",
        },
        outcome_observations=[{
            "shot_pts_ms": 100,
            "outcome_pts_ms": 117,
            "detector_ref": "red-1",
            "weapon_temporal_model": "hitscan",
            "association_kind": "directly_observed",
        }],
    )

    assert [
        (event["event_kind"], event["start_ms"])
        for event in result["event_bundle"]["events"]
        if event["event_kind"] in {"shot", "hit"}
    ] == [("shot", 1_020), ("hit", 1_037)]
    assert result["event_bundle"]["outcome_associations"][0]["availability"] == "available"


@pytest.mark.parametrize(
    "observation",
    [
        {
            "shot_pts_ms": 0,
            "outcome_pts_ms": None,
            "detector_ref": "red-1",
            "weapon_temporal_model": "hitscan",
            "association_kind": "directly_observed",
        },
        {
            "shot_pts_ms": 0,
            "outcome_pts_ms": 0,
            "detector_ref": None,
            "weapon_temporal_model": "hitscan",
            "association_kind": "validated_aligned",
            "validation_ref": "association-rule:fixture.v1",
        },
    ],
)
def test_available_outcome_association_requires_target_and_outcome_evidence(observation):
    with pytest.raises(ValueError, match="available outcome association"):
        _preprocess(
            [_frame(0, targets=[_target("red-1", 110, 100, 12)])],
            outcome_observations=[observation],
        )


def test_public_visual_result_contains_no_frame_video_or_path_payload():
    result = _preprocess([_frame(0, targets=[_target("red-1", 110, 100, 12)])])

    forbidden = {"frame", "image", "video", "path", "bytes", "pixels"}
    keys = {value.lower() for value in _walk(result) if isinstance(value, str)}
    assert not any(key in forbidden or key.endswith(("_frame", "_image", "_video", "_path")) for key in keys)
    assert "local_samples" not in result["safe_summary"]
    assert result["safe_summary"]["track_count"] == 1
    assert result["visual_runtime_selector"] == _selector()
    assert result["signal_bundle"]["observed_visual_domain"] == _selector()
    assert not {
        "ui_scale",
        "theme",
        "map_or_background_class",
        "target_appearance_class",
        "capture_transform_version",
    } & set(result["signal_bundle"]["observed_visual_domain"])


def test_visual_result_extends_generic_local_evidence_without_copying_images():
    base = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:visual-test",
        canonical_time_window=_window(),
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    visual = _preprocess([
        _frame(0, targets=[_target("red-1", 110, 100, 12)]),
        _frame(16, targets=[_target("red-1", 114, 100, 12)]),
    ])

    extended = extend_analysis_evidence_with_visual_signals_v1(base, visual)

    assert extended["signal_bundles"] == [visual["signal_bundle"]]
    assert extended["event_bundles"] == [visual["event_bundle"]]
    assert extended["sample_sets"] == visual["sample_sets"]
    assert "local_samples" not in extended


def test_visual_extension_rejects_an_unreachable_same_analysis_target_track():
    base = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:visual-test",
        canonical_time_window=_window(),
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    visual = _preprocess(
        [_frame(0, targets=[_target("red-1", 110, 100, 12)])],
        outcome_observations=[{
            "shot_pts_ms": 0,
            "outcome_pts_ms": 0,
            "detector_ref": "red-1",
            "weapon_temporal_model": "hitscan",
            "association_kind": "directly_observed",
        }],
    )
    visual["event_bundle"]["outcome_associations"][0]["target_track_ref"] = (
        "analysis:visual-test:target-track:999"
    )

    with pytest.raises(ValueError, match="target track ref is not reachable"):
        extend_analysis_evidence_with_visual_signals_v1(base, visual)


def test_synthetic_color_detector_returns_all_candidates_without_center_guess():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cv2.circle(image, (40, 50), 10, (0, 0, 255), -1)
    cv2.circle(image, (160, 50), 12, (0, 0, 255), -1)

    candidates = detect_color_candidates_v1(
        image,
        hsv_lower=[170, 180, 180],
        hsv_upper=[10, 255, 255],
        min_area=50,
        max_area_ratio=0.05,
        shape="round",
    )

    assert [(round(item["x"]), round(item["y"])) for item in candidates] == [
        (40, 50),
        (160, 50),
    ]
    assert [item["visible_radius"] for item in candidates] == pytest.approx(
        [9.6, 11.7], abs=0.8
    )


def test_visual_and_legacy_tracking_share_the_same_color_blob_primitive():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cv2.circle(image, (90, 50), 10, (0, 0, 255), -1)
    cv2.circle(image, (160, 50), 12, (0, 0, 255), -1)
    hsv_lower = np.array([170, 180, 180], dtype=np.uint8)
    hsv_upper = np.array([10, 255, 255], dtype=np.uint8)

    candidates = detect_color_blobs(
        image,
        hsv_lower,
        hsv_upper,
        min_area=50,
        max_area_ratio=0.05,
        min_circularity=0.65,
    )
    visual_candidates = detect_color_candidates_v1(
        image,
        hsv_lower=hsv_lower.tolist(),
        hsv_upper=hsv_upper.tolist(),
        min_area=50,
        max_area_ratio=0.05,
        shape="round",
    )
    legacy_position, _, _ = detect_ball_by_color(image, hsv_lower, hsv_upper)

    assert visual_candidates == [
        {
            "x": candidate["x"],
            "y": candidate["y"],
            "visible_radius": candidate["visible_radius"],
            "confidence": candidate["confidence"],
        }
        for candidate in candidates
    ]
    assert legacy_position == (90, 50)


def test_local_video_decoder_uses_pts_and_does_not_return_media_path(monkeypatch):
    frames = []
    for target_x in (120, 125):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        cv2.circle(image, (target_x, 50), 10, (0, 0, 255), -1)
        frames.append(image)

    class FakeCapture:
        def __init__(self, _source):
            self.index = 0

        def isOpened(self):
            return True

        def read(self):
            if self.index >= len(frames):
                return False, None
            image = frames[self.index]
            self.index += 1
            return True, image

        def get(self, prop):
            if prop == cv2.CAP_PROP_POS_MSEC:
                return (0.0, 17.0)[max(0, self.index - 1)]
            return 0.0

        def release(self):
            return None

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    selector = _selector(resolution=[200, 100])
    detector_config = {
        "schema_version": "visual_target_detector.v2",
        "aim_point_mode": "fixed_viewport_center",
        "excluded_regions": [[0.0, 0.0, 0.1, 0.1]],
        "target": {
            "hsv_lower": [170, 180, 180],
            "hsv_upper": [10, 255, 255],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    profile = build_visual_quality_profile_v2(
        producer_id=VISUAL_PRODUCER_ID,
        producer_version=VISUAL_PRODUCER_VERSION,
        annotation_set_ref="annotation-set:synthetic-color.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": visual_detector_config_ref_v1(detector_config),
            "hud_mask_version": "visual_hud_mask.synthetic.v1",
            "annotated_map_or_background_labels": ["synthetic"],
            "annotated_target_appearance_labels": ["red-round"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            family: [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ]
            for family in ("dynamic_clicking", "tracking", "switching")
        },
        required_quality_fields_by_metric_family=(
            _profile()["required_quality_fields_by_metric_family"]
        ),
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds=_profile()["acceptance_thresholds"],
        validation_results=_profile()["validation_results"],
        validated_metric_families=["dynamic_clicking", "tracking", "switching"],
        status="accepted",
        limitations=[],
    )
    result = preprocess_visual_video_v1(
        media_path="C:/private/fixture.mp4",
        analysis_ref="analysis:visual-test",
        canonical_time_window=_window(),
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": _window()["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": _window()["timebase_version"],
        },
        detector_config=detector_config,
    )

    assert result["local_samples"]["crosshair.position"] == [
        {
            "canonical_time_ms": 1_000,
            "x": 100.0,
            "y": 50.0,
            "confidence": 1.0,
        },
        {
            "canonical_time_ms": 1_017,
            "x": 100.0,
            "y": 50.0,
            "confidence": 1.0,
        },
    ]
    assert result["local_samples"]["crosshair.position"][1]["canonical_time_ms"] == 1_017
    assert result["track_summaries"][0]["sample_count"] == 2
    assert "C:/private" not in repr(result)

    detector_config["target"]["hsv_upper"][2] = 254
    with pytest.raises(ValueError, match="does not match quality profile"):
        preprocess_visual_video_v1(
            media_path="C:/private/fixture.mp4",
            analysis_ref="analysis:visual-test",
            canonical_time_window=_window(),
            visual_quality_profile=profile,
            visual_runtime_selector=selector,
            video_time_mapping={
                "schema_version": "visual_video_time_mapping.v1",
                "source_pts_origin_ms": 0.0,
                "canonical_origin_ms": _window()["start_ms"],
                "mapping_method": "run_owned_exact_canonical_clip",
                "timebase_version": _window()["timebase_version"],
            },
            detector_config=detector_config,
        )


def _run_temporal_video_fixture(
    monkeypatch,
    tracker_centers: Sequence[float],
    *,
    target_positions: Sequence[float] = (80, 90, 100, 110, 120),
    pts_values: Sequence[float] = (0.0, 17.0, 34.0, 51.0, 68.0),
    window: Mapping[str, object] | None = None,
    hidden_indices: Sequence[int] = (2,),
    extra_targets_by_index: Mapping[int, Sequence[float]] | None = None,
    detector_observations_by_index: Mapping[int, Mapping[str, object]] | None = None,
    tracker_init_result: bool = True,
    patch_csrt: bool = True,
) -> dict:
    assert len(target_positions) == len(pts_values)
    window = dict(window or _window())
    frames = []
    for index, target_x in enumerate(target_positions):
        image = np.full((100, 200, 3), 160, dtype=np.uint8)
        if index not in hidden_indices:
            cv2.circle(image, (int(target_x), 50), 10, (0, 0, 0), -1)
        for extra_target_x in (extra_targets_by_index or {}).get(index, ()):
            cv2.circle(image, (int(extra_target_x), 50), 10, (0, 0, 0), -1)
        frames.append(image)

    class FakeCapture:
        def __init__(self, _source):
            self.index = 0

        def isOpened(self):
            return True

        def read(self):
            if self.index >= len(frames):
                return False, None
            image = frames[self.index]
            self.index += 1
            return True, image

        def get(self, prop):
            if prop == cv2.CAP_PROP_POS_MSEC:
                return pts_values[self.index - 1]
            return 0.0

        def release(self):
            return None

    class FakeTracker:
        def __init__(self):
            self.index = 0

        def init(self, _image, _bbox):
            assert all(isinstance(value, int) for value in _bbox)
            return tracker_init_result

        def update(self, _image):
            center = tracker_centers[self.index]
            self.index += 1
            return True, (center - 10.0, 40.0, 20.0, 20.0)

    detector_index = 0

    def detect_with_overrides(image, **detector_config):
        nonlocal detector_index
        override = (detector_observations_by_index or {}).get(detector_index)
        detector_index += 1
        if override is not None:
            return {
                "targets": [dict(item) for item in override["targets"]],
                "target_ambiguities": [
                    dict(item) for item in override["target_ambiguities"]
                ],
            }
        return detect_color_observations_v2(image, **detector_config)

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    if patch_csrt:
        monkeypatch.setattr(cv2, "TrackerCSRT_create", lambda: FakeTracker())
    monkeypatch.setattr(
        "kovaak_tracker.visual_signals.detect_color_observations_v2",
        detect_with_overrides,
    )
    selector = _selector(resolution=[200, 100])
    detector_config = {
        "schema_version": "visual_target_detector.v2",
        "aim_point_mode": "fixed_viewport_center",
        "excluded_regions": [],
        "target": {
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 255, 80],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    return preprocess_visual_video_temporal_v1(
        media_path="C:/private/fixture.mp4",
        analysis_ref="analysis:visual-test",
        canonical_time_window=window,
        visual_quality_profile=_temporal_profile(detector_config, selector),
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
        detector_config=detector_config,
    )


def test_temporal_video_decoder_recovers_only_a_detector_guarded_short_gap(monkeypatch):
    result = _run_temporal_video_fixture(monkeypatch, (90, 100, 110, 120))

    assert len(result["track_summaries"]) == 1
    assert result["track_summaries"][0]["identity_source"] == "detector_ref"
    assert result["track_summaries"][0]["sample_count"] == 5
    assert result["track_summaries"][0]["limitations"] == []
    assert result["quality"]["enabled_metric_families"] == ["tracking"]
    assert "reentry_identity_unresolved" not in result["limitations"]
    assert [
        sample["measurement_source"]
        for sample in result["local_samples"]["target.1.position"]
    ] == [
        "direct_detector",
        "direct_detector",
        "temporal_tracker_confirmed",
        "direct_detector",
        "direct_detector",
    ]
    assert result["temporal_measurement_counts"] == {
        "direct_detector": 4,
        "temporal_tracker_confirmed": 1,
        "rejected": 0,
    }


def test_temporal_video_decoder_discards_a_drifting_tracker_gap(monkeypatch):
    result = _run_temporal_video_fixture(monkeypatch, (90, 165, 165, 120))

    assert sum(item["sample_count"] for item in result["track_summaries"]) == 4
    assert "reentry_identity_unresolved" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_rejects_a_gap_with_intermediate_tracker_drift(monkeypatch):
    result = _run_temporal_video_fixture(
        monkeypatch,
        (90, 165, 110, 120),
        hidden_indices=(2, 3),
    )

    assert sum(item["sample_count"] for item in result["track_summaries"]) == 3
    assert result["temporal_measurement_counts"]["rejected"] == 2
    assert "temporal_tracker_gap_rejected" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_never_overwrites_a_multi_target_frame(monkeypatch):
    result = _run_temporal_video_fixture(
        monkeypatch,
        (90, 100, 110, 120),
        hidden_indices=(),
        extra_targets_by_index={2: (150,)},
    )

    samples_at_multi_target_frame = [
        sample
        for sample_key, samples in result["local_samples"].items()
        if sample_key.startswith("target.")
        for sample in samples
        if sample["canonical_time_ms"] == 1_034
    ]
    assert len(samples_at_multi_target_frame) == 2
    assert result["temporal_measurement_counts"]["temporal_tracker_confirmed"] == 0
    assert "temporal_target_set_ambiguous" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_never_overwrites_a_merged_target_ambiguity(monkeypatch):
    ambiguity = {
        "ambiguity_kind": "merged_target_component",
        "x": 100.0,
        "y": 50.0,
        "visible_radius": 14.0,
        "confidence": 0.5,
    }
    result = _run_temporal_video_fixture(
        monkeypatch,
        (90, 100, 110, 120),
        detector_observations_by_index={
            2: {"targets": [], "target_ambiguities": [ambiguity]},
        },
    )

    assert result["temporal_measurement_counts"]["temporal_tracker_confirmed"] == 0
    assert result["temporal_measurement_counts"]["rejected"] == 0
    assert "target_merge_ambiguous" in result["limitations"]
    assert "temporal_target_set_ambiguous" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_rejects_an_unconfirmed_eof_gap(monkeypatch):
    target_positions = tuple(range(60, 80))
    result = _run_temporal_video_fixture(
        monkeypatch,
        target_positions[1:],
        target_positions=target_positions,
        pts_values=tuple(float(index * 4) for index in range(20)),
        hidden_indices=(19,),
    )

    assert result["safe_summary"]["target_coverage"] == 0.95
    assert sum(item["sample_count"] for item in result["track_summaries"]) == 19
    assert result["temporal_measurement_counts"]["rejected"] == 1
    assert result["completeness"] == "partial"
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_fails_closed_when_tracker_initialization_fails(monkeypatch):
    result = _run_temporal_video_fixture(
        monkeypatch,
        (),
        hidden_indices=(),
        tracker_init_result=False,
    )

    assert result["safe_summary"]["target_coverage"] == 1.0
    assert result["temporal_measurement_counts"]["temporal_tracker_confirmed"] == 0
    assert "temporal_tracker_unavailable" in result["limitations"]
    assert result["completeness"] == "partial"
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_rejects_a_late_detector_reentry(monkeypatch):
    long_window = {
        **_window(),
        "end_ms": 2_000,
        "duration_ms": 1_000,
    }
    result = _run_temporal_video_fixture(
        monkeypatch,
        (90, 100, 110, 120),
        pts_values=(0.0, 17.0, 34.0, 400.0, 417.0),
        window=long_window,
    )

    assert result["temporal_measurement_counts"]["temporal_tracker_confirmed"] == 0
    assert result["temporal_measurement_counts"]["rejected"] == 1
    assert "temporal_tracker_gap_rejected" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_temporal_video_decoder_does_not_fall_back_to_kcf(monkeypatch):
    def unavailable_csrt():
        raise AttributeError("CSRT unavailable")

    monkeypatch.setattr(cv2, "TrackerCSRT_create", unavailable_csrt)
    monkeypatch.setattr(cv2.legacy, "TrackerCSRT_create", unavailable_csrt)
    monkeypatch.setattr(
        "kovaak_tracker.vision.get_tracker",
        lambda: pytest.fail("temporal producer must not fall back to KCF"),
    )

    result = _run_temporal_video_fixture(
        monkeypatch,
        (),
        hidden_indices=(),
        tracker_init_result=False,
        patch_csrt=False,
    )

    assert "temporal_tracker_unavailable" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_reviewed_single_target_csrt_adapts_legacy_tracker_to_numeric_artifact(
    monkeypatch,
):
    frames = [np.full((100, 200, 3), 160, dtype=np.uint8) for _ in range(5)]
    pts_values = (0.0, 17.0, 34.0, 51.0, 68.0)

    class FakeCapture:
        def __init__(self, _source):
            self.index = 0

        def isOpened(self):
            return True

        def read(self):
            if self.index >= len(frames):
                return False, None
            image = frames[self.index]
            self.index += 1
            return True, image

        def get(self, prop):
            return pts_values[self.index - 1] if prop == cv2.CAP_PROP_POS_MSEC else 0.0

        def release(self):
            return None

    class FakeTracker:
        def init(self, _image, bbox):
            assert bbox == (70, 40, 20, 20)
            return True

        def update(self, _image):
            return True, (80.0, 40.0, 20.0, 20.0)

    detector_calls = 0

    def detect_once(_image, _lower, _upper):
        nonlocal detector_calls
        detector_calls += 1
        return (80, 50), 20, 20

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(cv2, "TrackerCSRT_create", lambda: FakeTracker())
    monkeypatch.setattr(
        "kovaak_tracker.vision.detect_ball_by_color", detect_once,
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
    selector = _selector(resolution=[200, 100])
    result = preprocess_visual_video_single_target_csrt_v1(
        media_path="C:/private/fixture.mp4",
        analysis_ref="analysis:visual-test",
        canonical_time_window=_window(),
        visual_quality_profile=_single_target_csrt_profile(
            detector_config, selector,
        ),
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": _window()["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": _window()["timebase_version"],
        },
        detector_config=detector_config,
    )

    assert detector_calls == 1
    assert result["quality"]["enabled_metric_families"] == ["tracking"]
    assert result["safe_summary"]["target_coverage"] == 1.0
    assert result["track_summaries"] == [{
        "track_ref": "analysis:visual-test:target-track:1",
        "identity_source": "detector_ref",
        "visible_radius_px": 10.0,
        "sample_count": 5,
        "coverage": 1.0,
        "limitations": [],
    }]
    assert [
        sample["measurement_source"]
        for sample in result["local_samples"]["target.1.position"]
    ] == ["direct_detector", *(["temporal_tracker_confirmed"] * 4)]


def test_reviewed_single_target_csrt_never_falls_back_to_kcf(monkeypatch):
    image = np.full((100, 200, 3), 160, dtype=np.uint8)

    class FakeCapture:
        def __init__(self, _source):
            self.read_count = 0

        def isOpened(self):
            return True

        def read(self):
            self.read_count += 1
            return (True, image) if self.read_count == 1 else (False, None)

        def get(self, prop):
            return 0.0

        def release(self):
            return None

    def unavailable_csrt():
        raise AttributeError("CSRT unavailable")

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(cv2, "TrackerCSRT_create", unavailable_csrt)
    monkeypatch.setattr(cv2.legacy, "TrackerCSRT_create", unavailable_csrt)
    monkeypatch.setattr(
        cv2, "TrackerKCF_create",
        lambda: pytest.fail("single-target producer must not fall back to KCF"),
    )
    monkeypatch.setattr(
        "kovaak_tracker.vision.detect_ball_by_color",
        lambda *_args: ((80, 50), 20, 20),
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
    selector = _selector(resolution=[200, 100])
    result = preprocess_visual_video_single_target_csrt_v1(
        media_path="C:/private/fixture.mp4",
        analysis_ref="analysis:visual-test",
        canonical_time_window=_window(),
        visual_quality_profile=_single_target_csrt_profile(
            detector_config, selector,
        ),
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": _window()["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": _window()["timebase_version"],
        },
        detector_config=detector_config,
    )

    assert "single_target_csrt_unavailable" in result["limitations"]
    assert result["quality"]["enabled_metric_families"] == []


def test_local_video_decoder_enforces_deterministic_frame_budget(monkeypatch):
    image = np.zeros((10, 20, 3), dtype=np.uint8)
    captures = []

    class FakeCapture:
        def __init__(self, _source):
            self.read_count = 0
            self.released = False
            captures.append(self)

        def isOpened(self):
            return True

        def read(self):
            self.read_count += 1
            if self.read_count > 261:
                return False, None
            return True, image

        def get(self, _prop):
            return float(self.read_count - 1)

        def release(self):
            self.released = True

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    selector = _selector(resolution=[20, 10])
    detector_config = {
        "schema_version": "visual_target_detector.v1",
        "aim_point_mode": "fixed_viewport_center",
        "target": {
            "hsv_lower": [170, 180, 180],
            "hsv_upper": [10, 255, 255],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    profile = build_visual_quality_profile_v2(
        producer_id=VISUAL_PRODUCER_ID,
        producer_version=VISUAL_PRODUCER_VERSION,
        annotation_set_ref="annotation-set:synthetic-budget.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": visual_detector_config_ref_v1(detector_config),
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["synthetic"],
            "annotated_target_appearance_labels": ["red-round"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            family: [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ]
            for family in ("dynamic_clicking", "tracking", "switching")
        },
        required_quality_fields_by_metric_family=(
            _profile()["required_quality_fields_by_metric_family"]
        ),
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds=_profile()["acceptance_thresholds"],
        validation_results=_profile()["validation_results"],
        validated_metric_families=["dynamic_clicking", "tracking", "switching"],
        status="accepted",
        limitations=[],
    )

    with pytest.raises(ValueError, match="local media exceeds the visual frame budget"):
        preprocess_visual_video_v1(
            media_path="C:/private/over-budget.mp4",
            analysis_ref="analysis:visual-test",
            canonical_time_window=_window(),
            visual_quality_profile=profile,
            visual_runtime_selector=selector,
            video_time_mapping={
                "schema_version": "visual_video_time_mapping.v1",
                "source_pts_origin_ms": 0.0,
                "canonical_origin_ms": _window()["start_ms"],
                "mapping_method": "run_owned_exact_canonical_clip",
                "timebase_version": _window()["timebase_version"],
            },
            detector_config=detector_config,
        )

    assert captures[0].read_count == 261
    assert captures[0].released is True
