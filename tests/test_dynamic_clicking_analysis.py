from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kovaak_tracker.analysis_evidence import (
    EvidenceKeyRegistry,
    build_analysis_evidence_artifact_v1,
    validate_analysis_evidence_artifact,
    validate_analysis_evidence_artifact_v1,
    validate_event_bundle_v1,
    validate_metric_record_v1,
    validate_processed_event_table_v1,
)
from kovaak_tracker.dynamic_clicking_analysis import (
    DynamicClickingAnalysisError,
    analyze_dynamic_clicking_v1,
    extend_analysis_evidence_with_dynamic_clicking_v1,
)
from kovaak_tracker.advice_dynamic_clicking import (
    build_dynamic_clicking_candidate_advice,
)
from kovaak_tracker.visual_signals import preprocess_visual_video_v1


def _payload(*, multiple_targets: bool = False, quality_families=None):
    tracks = [
        {
            "track_ref": "analysis:1:target-track:1",
            "samples": [
                {"canonical_time_ms": 0, "x": 120.0, "y": 100.0, "radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 100, "x": 114.5, "y": 100.0, "radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 200, "x": 109.0, "y": 100.0, "radius": 10.0, "confidence": 1.0},
            ],
            "limitations": [],
        },
    ]
    if multiple_targets:
        tracks.append({
            "track_ref": "analysis:1:target-track:2",
            "samples": [
                {"canonical_time_ms": 0, "x": 120.0, "y": 100.0, "radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 100, "x": 114.5, "y": 100.0, "radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 200, "x": 109.0, "y": 100.0, "radius": 10.0, "confidence": 1.0},
            ],
            "limitations": [],
        })
    return {
        "schema_version": "dynamic_clicking_input.v1",
        "analysis_ref": "analysis:1",
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 0,
            "end_ms": 300,
            "duration_ms": 300,
            "start_source": "stats",
            "end_source": "performance",
            "timebase_version": "time_alignment.v2",
            "window_semantics": "half_open",
            "warnings": [],
        },
        "scenario_resolution": {
            "aim_family": "dynamic_clicking",
            "target_motion": {"model": "predictable", "target_count_model": "single"},
            "scenario_hash": "scenario-hash",
            "scenario_profile_ref": "scenario:dynamic.fixture@1",
        },
        "visual_quality": {
            "status": "accepted",
            "enabled_metric_families": (
                ["dynamic_clicking"] if quality_families is None else quality_families
            ),
            "completeness": "complete",
            "limitations": [],
        },
        "crosshair_samples": [
            {"canonical_time_ms": 0, "x": 100.0, "y": 100.0, "confidence": 1.0},
            {"canonical_time_ms": 100, "x": 100.0, "y": 100.0, "confidence": 1.0},
            {"canonical_time_ms": 200, "x": 100.0, "y": 100.0, "confidence": 1.0},
        ],
        "available_channel_keys": [
            "crosshair.position_x", "crosshair.position_y",
            "target.1.position_x", "target.1.position_y", "target.1.visible_radius",
        ],
        "target_tracks": tracks,
        "click_events": [{"event_ref": "analysis:1:shot:1", "time_ms": 200}],
        "visual_event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": "analysis:1",
            "events": [
                {
                    "event_id": "analysis:1:shot:1", "event_kind": "shot",
                    "start_ms": 200, "end_ms": 200, "actor_refs": [],
                    "source_refs": ["analysis:1:source:fixture"], "confidence": 1.0,
                    "attributes": {}, "limitations": [],
                },
                {
                    "event_id": "analysis:1:hit:1", "event_kind": "hit",
                    "start_ms": 200, "end_ms": 200, "actor_refs": [],
                    "source_refs": ["analysis:1:source:fixture"], "confidence": 1.0,
                    "attributes": {}, "limitations": [],
                },
                {
                    "event_id": "analysis:1:target-available:1",
                    "event_kind": "target_available",
                    "start_ms": 0,
                    "end_ms": 0,
                    "actor_refs": ["analysis:1:target-track:1"],
                    "source_refs": ["analysis:1:source:fixture"],
                    "confidence": 1.0,
                    "attributes": {},
                    "limitations": [],
                },
            ],
            "outcome_associations": [] if multiple_targets else [{
                "association_id": "analysis:1:association:1",
                "shot_event_ref": "analysis:1:shot:1",
                "outcome_event_ref": "analysis:1:hit:1",
                "target_track_ref": "analysis:1:target-track:1",
                "weapon_temporal_model": "hitscan",
                "association_kind": "directly_observed",
                "source_refs": ["analysis:1:source:fixture"],
                "confidence": 1.0,
                "availability": "available",
                "limitations": [],
            }],
        },
        "predictability_evidence": [],
    }


def _validated_kill_bundle_v2() -> dict:
    binding = {
        "schema_version": "outcome_association_rule_binding.v1",
        "rule_ref": "outcome-association-rule:dynamic-fixture@1",
        "scenario_profile_ref": "scenario:dynamic.fixture@1",
        "canonical_timebase_version": "time_alignment.v2",
        "raw_click_extractor_version": "raw-left-rising-edge.v1",
        "stats_parser_version": "kovaak_stats.v1",
        "outcome_semantics": "one_shot_kill",
        "weapon_temporal_model": "hitscan",
        "stats_predicate": {"shots_equals": 1, "hits_equals": 1, "overshots_equals": 0},
        "timing_window_ms": {"minimum": 0, "maximum": 50},
        "track_predicate": {
            "identity_status": "stable",
            "max_sample_gap_ms": 20,
            "require_inner_hitbox": True,
            "hitbox_inset_px": 0.0,
            "minimum_sample_confidence": 1.0,
        },
        "visual_quality_profile_ref": "visual-quality-profile:dynamic-fixture@1",
        "fixture_set_ref": "fixture-set:dynamic@1",
        "annotation_set_ref": "annotation-set:dynamic@1",
    }
    binding["rule_sha256"] = hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    shot = _payload()["visual_event_bundle"]["events"][0]
    kill = {
        **_payload()["visual_event_bundle"]["events"][1],
        "event_id": "analysis:1:kill:1",
        "event_kind": "kill",
        "source_refs": ["analysis:1:source:stats"],
        "attributes": {"kill_index": 1, "shots": 1, "hits": 1, "overshots": 0},
    }
    return {
        "schema_version": "event_bundle.v2",
        "analysis_ref": "analysis:1",
        "events": [shot, kill],
        "outcome_association_rule_bindings": [binding],
        "outcome_associations": [{
            "association_id": "analysis:1:association:kill:1",
            "shot_event_ref": "analysis:1:shot:1",
            "outcome_event_ref": "analysis:1:kill:1",
            "target_track_ref": "analysis:1:target-track:1",
            "weapon_temporal_model": "hitscan",
            "association_kind": "validated_aligned",
            "source_refs": [
                "analysis:1:source:fixture",
                "analysis:1:source:stats",
                "analysis:1:source:video",
            ],
            "validation": {
                "schema_version": "outcome_association_validation.v1",
                "rule_ref": binding["rule_ref"],
                "rule_sha256": binding["rule_sha256"],
                "scenario_profile_ref": binding["scenario_profile_ref"],
                "canonical_time_window_ref": "analysis:1:canonical-window",
                "raw_input_source_ref": "analysis:1:source:fixture",
                "stats_source_ref": "analysis:1:source:stats",
                "visual_source_ref": "analysis:1:source:video",
                "visual_quality_profile_ref": binding["visual_quality_profile_ref"],
                "click_time_ms": 200,
                "outcome_time_ms": 200,
                "click_to_outcome_ms": 0,
                "temporal_candidate_count": 1,
                "geometric_candidate_count": 1,
                "stats_kill": {"kill_index": 1, "shots": 1, "hits": 1, "overshots": 0},
                "track_check": {
                    "identity_status": "stable",
                    "sample_gap_ms": 0,
                    "sample_confidence": 1.0,
                    "center_distance_px": 9.0,
                    "effective_radius_px": 10.0,
                },
            },
            "confidence": 1.0,
            "availability": "available",
            "limitations": [],
        }],
    }


def _artifact_with_visual_channels() -> dict:
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:1",
        canonical_time_window=_payload()["canonical_time_window"],
        scenario_profile_ref="scenario:dynamic.fixture@1",
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    channel_keys = [
        "crosshair.position_x", "crosshair.position_y",
        "target.1.position_x", "target.1.position_y", "target.1.visible_radius",
    ]
    artifact["sample_sets"] = [
        {
            "sample_set_id": f"analysis:1:samples:visual:{index}",
            "channel_key": channel_key,
            "unit": "px",
            "points": [[0, 100.0], [100, 100.0], [200, 100.0]],
        }
        for index, channel_key in enumerate(channel_keys, 1)
    ]
    artifact["signal_bundles"] = [{
        "schema_version": "signal_bundle.v1",
        "analysis_ref": "analysis:1",
        "canonical_time_window_ref": "analysis:1:canonical-window",
        "visual_quality_profile_ref": "profile:synthetic.v1",
        "observed_visual_domain": None,
        "channels": [
            {
                "channel_key": channel_key,
                "source_refs": ["analysis:1:source:visual-fixture"],
                "coordinate_space": "capture_pixels",
                "unit": "px",
                "sample_rate_semantics": "source_native",
                "samples_ref": f"analysis:1:samples:visual:{index}",
                "coverage": 1.0,
                "confidence_summary": 1.0,
                "transform_version": "visual_transform.v1",
                "limitations": [],
            }
            for index, channel_key in enumerate(channel_keys, 1)
        ],
    }]
    return validate_analysis_evidence_artifact_v1(artifact)


def test_dynamic_clicking_computes_error_acquisition_and_relative_velocity():
    result = analyze_dynamic_clicking_v1(_payload())

    assert result["support_status"] == "supported"
    row = result["processed_rows"][0]
    assert row["normalized_click_error"] == pytest.approx(0.9)
    assert row["miss_vector"] == pytest.approx([9.0, 0.0])
    assert row["acquisition_time_ms"] == 200
    assert row["target_relative_velocity"]["x"] == pytest.approx(-0.055)
    assert row["target_acceleration"] == pytest.approx(0.0)
    assert row["outcome_available"] is True
    assert row["outcome_success"] is True
    assert row["condition_ref"] == "condition:predictable:steady"
    assert result["metrics"][
        "dynamic_clicking.normalized_click_error"
    ]["condition_refs"] == ["condition:predictable:steady"]
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"]["value"] == 1.0
    dynamic_event = result["evidence_extension"]["event_bundle"]["events"][0]
    assert dynamic_event["attributes"]["outcome_success"] == 1.0


def test_target_state_accuracy_reports_success_ratio_not_binary_median():
    payload = _payload()
    target_available = payload["visual_event_bundle"]["events"][2]
    payload["click_events"] = []
    payload["visual_event_bundle"]["events"] = [target_available]
    payload["visual_event_bundle"]["outcome_associations"] = []

    for index, (time_ms, outcome_kind) in enumerate(
        ((100, "hit"), (150, "hit"), (200, "miss")), 1,
    ):
        shot_ref = f"analysis:1:shot:{index}"
        outcome_ref = f"analysis:1:{outcome_kind}:{index}"
        payload["click_events"].append({"event_ref": shot_ref, "time_ms": time_ms})
        payload["visual_event_bundle"]["events"].extend((
            {
                "event_id": shot_ref, "event_kind": "shot",
                "start_ms": time_ms, "end_ms": time_ms, "actor_refs": [],
                "source_refs": ["analysis:1:source:fixture"], "confidence": 1.0,
                "attributes": {}, "limitations": [],
            },
            {
                "event_id": outcome_ref, "event_kind": outcome_kind,
                "start_ms": time_ms, "end_ms": time_ms, "actor_refs": [],
                "source_refs": ["analysis:1:source:fixture"], "confidence": 1.0,
                "attributes": {}, "limitations": [],
            },
        ))
        payload["visual_event_bundle"]["outcome_associations"].append({
            "association_id": f"analysis:1:association:{index}",
            "shot_event_ref": shot_ref,
            "outcome_event_ref": outcome_ref,
            "target_track_ref": "analysis:1:target-track:1",
            "weapon_temporal_model": "hitscan",
            "association_kind": "directly_observed",
            "source_refs": ["analysis:1:source:fixture"],
            "confidence": 1.0,
            "availability": "available",
            "limitations": [],
        })

    result = analyze_dynamic_clicking_v1(payload)

    metric = result["metrics"]["dynamic_clicking.target_state_accuracy"]
    assert metric["value"] == pytest.approx(2 / 3)
    assert metric["population"]["valid_count"] == 3


def test_predictable_motion_without_segment_evidence_only_has_lead_lag_descriptor():
    result = analyze_dynamic_clicking_v1(_payload())

    assert "dynamic_clicking.predictive_lead" not in result["metrics"]
    assert result["processed_rows"][0]["lead_lag_descriptor"] in {"lead", "lag", "aligned"}
    assert "motion_predictability_evidence_unavailable" in result["limitations"]


def test_accepted_predictability_evidence_unlocks_predictive_lead_metric():
    payload = _payload()
    payload["predictability_evidence"] = [{
        "evidence_ref": "analysis:1:predictability:1",
        "segment_ref": "analysis:1:segment:dynamic:1",
        "model_ref": "script:linear.v1",
        "model_version": "motion_model.v1",
        "fit_metric": "r2",
        "fit_value": 0.99,
        "threshold_ref": "calibration:predictability.v1",
        "acceptance": "accepted",
        "source_refs": ["analysis:1:source:script-fixture"],
    }]
    result = analyze_dynamic_clicking_v1(payload)

    assert result["metrics"]["dynamic_clicking.predictive_lead"]["availability"] == "available"
    assert "analysis:1:predictability:1" in result["metrics"]["dynamic_clicking.predictive_lead"]["condition_refs"]
    assert result["evidence_extension"]["predictability_events"][0]["attributes"]["acceptance"] == "accepted"


def test_unique_geometric_hitbox_overlap_binds_click_without_outcome():
    payload = _payload(multiple_targets=True)
    payload["target_tracks"][1]["samples"][-1]["x"] = 140.0

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_track_ref"] == "analysis:1:target-track:1"
    assert row["target_association_basis"] == "unique_geometric"
    assert row["association_availability"] == "available"
    assert row["miss_vector"] == pytest.approx([9.0, 0.0])
    assert row["normalized_click_error"] == pytest.approx(0.9)
    assert row["target_speed"] is None
    assert row["target_acceleration"] is None
    assert row["target_relative_velocity"] is None
    assert row["relative_velocity_magnitude"] is None
    assert row["acquisition_start_ms"] is None
    assert row["acquisition_time_ms"] is None
    assert row["signed_lead_lag"] is None
    assert row["lead_lag_descriptor"] is None
    assert "click_geometry_visible_radius_conditioned" in row["limitations"]
    assert row["outcome_available"] is False
    assert "click_geometry_visible_radius_conditioned" in result["metrics"][
        "dynamic_clicking.normalized_click_error"
    ]["limitations"]
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"][
        "availability"
    ] == "unavailable"


def test_unique_geometric_rejects_target_from_an_older_observation_frame():
    payload = _payload(multiple_targets=True)
    payload["visual_event_bundle"]["outcome_associations"] = []
    payload["target_tracks"][0]["samples"] = [
        *payload["target_tracks"][0]["samples"][:-1],
        {
            "canonical_time_ms": 190,
            "x": 109.0,
            "y": 100.0,
            "radius": 10.0,
            "confidence": 1.0,
        },
    ]
    payload["target_tracks"][1]["samples"][-1]["x"] = 140.0

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_track_ref"] is None
    assert row["target_association_basis"] == "unavailable"
    assert row["normalized_click_error"] is None
    assert "target_click_association_unavailable" in row["limitations"]


def test_unique_geometric_rejects_a_stale_crosshair_observation_frame():
    payload = _payload()
    payload["visual_event_bundle"]["outcome_associations"] = []
    payload["crosshair_samples"] = payload["crosshair_samples"][:-1]
    payload["target_tracks"][0]["samples"] = payload["target_tracks"][0]["samples"][:-1]
    payload["max_interpolation_gap_ms"] = 99

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_track_ref"] is None
    assert row["normalized_click_error"] is None
    assert "crosshair_interpolation_unavailable" in row["limitations"]


def test_zero_geometric_overlap_keeps_intended_target_unavailable():
    payload = _payload()
    payload["visual_event_bundle"]["outcome_associations"] = []
    payload["target_tracks"][0]["samples"][-1]["x"] = 140.0

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_track_ref"] is None
    assert row["target_association_basis"] == "unavailable"
    assert row["normalized_click_error"] is None
    assert row["outcome_available"] is False
    assert "target_click_association_unavailable" in row["limitations"]
    assert "click_geometry_visible_radius_conditioned" not in row["limitations"]
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"][
        "availability"
    ] == "unavailable"


@pytest.mark.parametrize(
    "identity_limitation",
    ["reentry_identity_unresolved", "identity_crossing_ambiguous"],
)
def test_identity_ambiguity_keeps_instant_geometry_but_withholds_cross_frame_claims(
    identity_limitation,
):
    payload = _payload()
    payload["visual_event_bundle"]["outcome_associations"] = []
    payload["target_tracks"][0]["limitations"] = [identity_limitation]

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_association_basis"] == "unique_geometric"
    assert row["association_availability"] == "available"
    assert row["miss_vector"] == pytest.approx([9.0, 0.0])
    assert row["normalized_click_error"] == pytest.approx(0.9)
    assert row["target_speed"] is None
    assert row["target_acceleration"] is None
    assert row["target_relative_velocity"] is None
    assert row["relative_velocity_magnitude"] is None
    assert row["acquisition_start_ms"] is None
    assert row["acquisition_time_ms"] is None
    assert row["signed_lead_lag"] is None
    assert row["lead_lag_descriptor"] is None
    assert row["outcome_available"] is False
    assert "target_identity_unresolved" in row["limitations"]
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"][
        "availability"
    ] == "unavailable"


def test_merged_target_ambiguity_interval_blocks_geometric_binding():
    payload = _payload()
    payload["visual_event_bundle"]["outcome_associations"] = []
    payload["visual_event_bundle"]["events"].append({
        "event_id": "analysis:1:merge:1",
        "event_kind": "candidate_visible",
        "start_ms": 195,
        "end_ms": 205,
        "actor_refs": [],
        "source_refs": ["analysis:1:source:fixture"],
        "confidence": 0.0,
        "attributes": {},
        "limitations": ["target_merge_ambiguous"],
    })

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_track_ref"] is None
    assert row["association_availability"] == "unavailable"
    assert row["normalized_click_error"] is None
    assert "target_merge_ambiguous_at_click" in row["limitations"]


def test_ambiguous_target_identity_keeps_click_row_but_withholds_target_relative_claims():
    result = analyze_dynamic_clicking_v1(_payload(multiple_targets=True))

    row = result["processed_rows"][0]
    assert row["association_availability"] == "unavailable"
    assert row["normalized_click_error"] is None
    assert "target_click_association_ambiguous" in row["limitations"]
    assert result["support_status"] == "partial"
    metric = result["metrics"]["dynamic_clicking.normalized_click_error"]
    assert metric["population"] == {
        "sample_count": 1, "valid_count": 0, "excluded_count": 1,
    }
    assert metric["coverage"] == 0.0


def test_click_relative_error_survives_missing_outcome_association_but_accuracy_does_not():
    payload = _payload()
    payload["visual_event_bundle"]["outcome_associations"][0].update({
        "association_kind": "inferred",
        "availability": "partial",
        "confidence": 0.5,
        "limitations": ["outcome_association_inferred"],
    })
    result = analyze_dynamic_clicking_v1(payload)

    assert result["processed_rows"][0]["normalized_click_error"] is not None
    assert result["processed_rows"][0]["outcome_available"] is False
    accuracy = result["metrics"]["dynamic_clicking.target_state_accuracy"]
    assert accuracy["availability"] == "unavailable"
    assert accuracy["population"]["excluded_count"] == 1


def test_validated_kill_can_bind_target_but_does_not_invent_hit_accuracy():
    payload = _payload()
    payload["visual_event_bundle"] = _validated_kill_bundle_v2()

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["target_association_basis"] == "validated_outcome"
    assert row["normalized_click_error"] == pytest.approx(0.9)
    assert row["outcome_available"] is False
    assert row["outcome_success"] is None
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"]["availability"] == "unavailable"


def test_dynamic_extension_preserves_v2_outcome_binding_in_artifact():
    artifact = deepcopy(_artifact_with_visual_channels())
    signal_bundle = artifact["signal_bundles"][0]
    signal_bundle["visual_quality_profile_ref"] = (
        "visual-quality-profile:dynamic-fixture@1"
    )
    signal_bundle["observed_visual_domain"] = {"resolution": [200, 200]}
    for channel in signal_bundle["channels"]:
        channel["source_refs"] = ["analysis:1:source:video"]
    samples_by_channel = {
        sample_set["channel_key"]: sample_set
        for sample_set in artifact["sample_sets"]
    }
    samples_by_channel["target.1.position_x"]["points"][-1][1] = 109.0
    samples_by_channel["target.1.position_y"]["points"][-1][1] = 100.0
    samples_by_channel["target.1.visible_radius"]["points"][-1][1] = 10.0
    artifact["normalized_outcome_records"] = [{
        "canonical_time_ms": 200,
        "source_time": {
            "clock_domain": "stats_local_time_of_day",
            "value": 200,
            "unit": "HH:MM:SS.mmm",
            "precision": "milliseconds",
        },
        "source_priority": 10,
        "source_event_index": 0,
        "values": [
            {
                "metric_key": f"stats.kill.{key}",
                "value": value,
                "value_semantics": "aggregate_within_kill_row",
                "unit": "count",
            }
            for key, value in (
                ("kill_index", 1), ("shots", 1), ("hits", 1), ("overshots", 0),
            )
        ],
        "source_refs": ["analysis:1:source:stats"],
    }]
    artifact["canonical_run_facts"] = {
        "schema_version": "canonical_run_facts.v1",
        "analysis_ref": "analysis:1",
        "scenario_profile_ref": "scenario:dynamic.fixture@1",
        "canonical_time_window_ref": "analysis:1:canonical-window",
        "field_registry_version": "source_field_registry.v1",
        "source_contracts": [{
            "source_kind": "stats",
            "source_ref": "analysis:1:source:stats",
            "parser_version": "kovaak_stats.v1",
            "source_schema_version": None,
            "recognized_schema_status": "recognized",
            "unknown_field_observability": "not_observable",
        }],
        "sections": [{
            "section_key": "scenario",
            "facts": {"stats_display_name": "Dynamic Fixture"},
            "present_field_keys": ["stats.summary.Scenario"],
            "source_absent_field_keys": ["stats.summary.Hash"],
            "omitted_known_fields": [],
            "completeness": "complete_allowlisted",
        }],
        "outcome_record_sets": {
            "stats_kill_rows_ref": "analysis:1:stats-kill-rows",
            "performance_metric_changes_ref": None,
        },
        "completeness": "partial",
        "unknown_field_policy": "excluded",
        "limitations": [],
    }
    artifact["schema_version"] = "analysis_evidence_artifact.v2"
    artifact["event_bundles"].append(_validated_kill_bundle_v2())
    artifact = validate_analysis_evidence_artifact(artifact)

    result = analyze_dynamic_clicking_v1({
        **_payload(),
        "visual_event_bundle": artifact["event_bundles"][-1],
    })
    extended = extend_analysis_evidence_with_dynamic_clicking_v1(artifact, result)

    assert extended["schema_version"] == "analysis_evidence_artifact.v2"
    assert any(
        bundle["schema_version"] == "event_bundle.v2"
        for bundle in extended["event_bundles"]
    )


def test_quality_gate_disabled_degrades_to_outcome_only():
    result = analyze_dynamic_clicking_v1(_payload(quality_families=[]))

    assert result["support_status"] == "outcome_only"
    row = result["processed_rows"][0]
    assert row["target_track_ref"] is None
    assert row["target_association_basis"] == "unavailable"
    assert row["association_availability"] == "unavailable"
    assert row["outcome_available"] is False
    assert row["outcome_success"] is None
    assert row["normalized_click_error"] is None
    accuracy = result["metrics"]["dynamic_clicking.target_state_accuracy"]
    assert accuracy["availability"] == "unavailable"
    assert accuracy["value"] is None
    assert accuracy["coverage"] == 0.0
    assert "outcome_success" not in result["evidence_extension"]["event_bundle"][
        "events"
    ][0]["attributes"]
    assert "dynamic_clicking_quality_unavailable" in result["limitations"]


def test_unvalidated_visual_change_point_does_not_create_post_change_condition():
    payload = _payload()
    payload["visual_event_bundle"]["events"].append({
        "event_id": "analysis:1:change:1",
        "event_kind": "target_change_point",
        "start_ms": 100,
        "end_ms": 100,
        "actor_refs": ["analysis:1:target-track:1"],
        "source_refs": ["analysis:1:source:fixture"],
        "confidence": 1.0,
        "attributes": {"change_kind": "direction_reversal"},
        "limitations": ["change_point_not_independently_validated"],
    })

    result = analyze_dynamic_clicking_v1(payload)

    assert result["processed_rows"][0]["change_state"] == "steady"


def test_real_dynamic_field_replay_stays_bounded_and_fail_closed():
    from webapp.backend.worker import run_dynamic_clicking_analysis

    raw_field_dir = os.environ.get("AIMING_COOKIE_DYNAMIC_FIELD_DIR")
    if not raw_field_dir:
        pytest.skip("set AIMING_COOKIE_DYNAMIC_FIELD_DIR for the local field replay")
    field_dir = Path(raw_field_dir)
    expected_hashes = {
        "canonical-challenge.mp4": (
            "a97a33160d0932ad9c9c3032ecb9638f52178ff64dc6540eb006b6b42cd5c730"
        ),
        "canonical-challenge.acri-v1.bin": (
            "0d5ee02d3ca6c9a7fc51726f1c8608b21a8d82f8e9aa4db21d098ea439cc80b1"
        ),
        "source-stats.csv": (
            "10d0f5231ac15832b6bce3fc6c941c38664613eae5c9932b1059fef02d83c4b5"
        ),
        "source-performance.perf": (
            "ec7c070e7e41e48f328e16bc042926b2d1b5088a96cbe31d9b1a727953dcb10a"
        ),
    }
    for name, expected_hash in expected_hashes.items():
        assert hashlib.sha256((field_dir / name).read_bytes()).hexdigest() == expected_hash

    fixture_dir = Path(__file__).parent / "fixtures" / "visual_signals"
    profile = json.loads(
        (
            fixture_dir
            / "dynamic_clicking_candidate_profile.center_overlay.v2.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    detector_config = json.loads(
        (
            fixture_dir / "dynamic_clicking_candidate_detector_config.v2.json"
        ).read_text(encoding="utf-8")
    )
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 1_784_438_416_265,
        "end_ms": 1_784_438_501_959,
        "duration_ms": 85_694,
        "window_semantics": "half_open",
        "timebase_version": "time_alignment.v2",
        "start_source": "stats_challenge_start",
        "end_source": "performance_event",
        "warnings": ["filename_time_is_coarse_hint"],
    }
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "a5be19c6e6aeb0d774c5e9d9fb497e91",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }
    visual = preprocess_visual_video_v1(
        media_path=str(field_dir / "canonical-challenge.mp4"),
        analysis_ref="analysis:70007",
        canonical_time_window=window,
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": "time_alignment.v2",
        },
        detector_config=detector_config,
        source_ref="field:task4-timescale@2026-07-19",
    )
    assert visual["safe_summary"]["observation_count"] == 5_142
    assert visual["safe_summary"]["completeness"] == "complete"
    assert "dynamic_clicking" in visual["quality"]["enabled_metric_families"]
    assert "visual_event_budget_exceeded" not in visual["limitations"]

    job = {
        "id": 70007,
        "input_snapshot": {
            "canonical_time_window": window,
            "scenario_resolution": {
                "scenario_hash": selector["scenario_hash"],
                "scenario_profile_ref": "scenario:1wall5targets_pasu@candidate",
                "aim_family": "dynamic_clicking",
                "target_motion": {"model": "unknown"},
            },
            "trace": {"artifact_ref": "field:task4-timescale:raw"},
        },
    }
    with patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        return_value=(field_dir / "canonical-challenge.acri-v1.bin").read_bytes(),
    ):
        dynamic = run_dynamic_clicking_analysis(job, visual)

    assert len(dynamic["processed_rows"]) == 127
    assert dynamic["metrics"]["dynamic_clicking.target_state_accuracy"][
        "availability"
    ] == "unavailable"
    assert build_dynamic_clicking_candidate_advice(dynamic) == []


def test_dynamic_evidence_extension_is_registered_and_typed():
    result = analyze_dynamic_clicking_v1(_payload())
    extension = result["evidence_extension"]
    registry = EvidenceKeyRegistry()

    validate_event_bundle_v1(extension["event_bundle"], registry=registry)
    for metric in extension["metric_records"]:
        validate_metric_record_v1(metric, registry=registry)
    validate_processed_event_table_v1(extension["processed_event_table"])
    fields = {
        field["field_key"]
        for field in extension["processed_event_table"]["field_catalog"]
    }
    assert {
        "event_id", "confidence", "limitations", "target_radius",
        "target_association_basis", "acquisition_start_ms",
    } <= fields
    assert all(
        ref.startswith("metric:dynamic_clicking.")
        for ref in extension["evidence_segments"][0]["metric_refs"]
    )


def test_crosshair_samples_must_remain_at_fixed_viewport_center():
    payload = _payload()
    payload["crosshair_samples"][1]["x"] = 101.0

    with pytest.raises(DynamicClickingAnalysisError, match="fixed_viewport_center"):
        analyze_dynamic_clicking_v1(payload)


def test_window_censored_track_does_not_invent_acquisition_time():
    payload = _payload()
    payload["visual_event_bundle"]["events"] = [
        event for event in payload["visual_event_bundle"]["events"]
        if event["event_kind"] != "target_available"
    ]

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["acquisition_time_ms"] is None
    assert "acquisition_start_window_censored" in row["limitations"]


def test_low_confidence_interval_blocks_cross_occlusion_interpolation():
    payload = _payload()
    payload["max_interpolation_gap_ms"] = 250
    payload["target_tracks"][0]["samples"] = [
        payload["target_tracks"][0]["samples"][0],
        payload["target_tracks"][0]["samples"][2],
    ]
    payload["click_events"][0]["time_ms"] = 100
    payload["visual_event_bundle"]["events"].append({
        "event_id": "analysis:1:low-confidence:1",
        "event_kind": "low_confidence",
        "start_ms": 100,
        "end_ms": 100,
        "actor_refs": [],
        "source_refs": ["analysis:1:source:fixture"],
        "confidence": 0.0,
        "attributes": {},
        "limitations": ["target_occlusion"],
    })

    result = analyze_dynamic_clicking_v1(payload)

    row = result["processed_rows"][0]
    assert row["normalized_click_error"] is None
    assert "visual_low_confidence_at_click" in row["limitations"]
    assert "outcome_association_click_time_mismatch" in row["limitations"]


def test_dynamic_extension_round_trips_and_predictive_claim_requires_same_segment_evidence():
    payload = _payload()
    payload["predictability_evidence"] = [{
        "evidence_ref": "analysis:1:predictability:1",
        "segment_ref": "analysis:1:segment:dynamic:1",
        "model_ref": "script:linear.v1",
        "model_version": "motion_model.v1",
        "fit_metric": "r2",
        "fit_value": 0.99,
        "threshold_ref": "calibration:predictability.v1",
        "acceptance": "accepted",
        "source_refs": ["analysis:1:source:script-fixture"],
    }]
    result = analyze_dynamic_clicking_v1(payload)
    merged = extend_analysis_evidence_with_dynamic_clicking_v1(
        _artifact_with_visual_channels(), result,
    )

    assert any(
        metric["metric_key"] == "dynamic_clicking.predictive_lead"
        for metric in merged["metric_records"]
    )
    broken = deepcopy(merged)
    dynamic_bundle = next(
        bundle for bundle in broken["event_bundles"]
        if any(event["event_kind"] == "dynamic_click" for event in bundle["events"])
    )
    dynamic_bundle["events"] = [
        event for event in dynamic_bundle["events"]
        if event["event_kind"] != "motion_predictability_evidence"
    ]
    with pytest.raises(ValueError, match="accepted predictability evidence"):
        validate_analysis_evidence_artifact_v1(broken)
