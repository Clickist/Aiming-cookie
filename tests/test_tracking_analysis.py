from __future__ import annotations

from copy import deepcopy
from math import pi, sin

import pytest

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
    build_processed_event_table_catalog_v1,
    validate_analysis_evidence_artifact_v1,
)
from kovaak_tracker.tracking_analysis import (
    TrackingAnalysisError,
    analyze_continuous_tracking_v1,
    extend_analysis_evidence_with_continuous_tracking_v1,
)


def _payload(*, radius: float | None = 15.0) -> dict:
    target_samples = [
        {"canonical_time_ms": time_ms, "x": float(time_ms // 10), "y": 0.0,
         "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    if radius is not None:
        for sample in target_samples:
            sample["radius"] = radius
    return {
        "schema_version": "continuous_tracking_input.v1",
        "analysis_ref": "analysis:tracking:1",
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 0,
            "end_ms": 400,
            "duration_ms": 400,
            "window_semantics": "half_open",
            "timebase_version": "time_alignment.test.v1",
            "start_source": "fixture",
            "end_source": "fixture",
            "warnings": [],
        },
        "scenario_resolution": {
            "aim_family": "continuous_tracking",
            "target_motion": {"model": "predictable"},
        },
        "visual_quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "player_motion_status": "available_shared_trajectory",
        "alignment_latency_ms": 12.0,
        "target_track": {"track_ref": "analysis:tracking:1:target-track:1", "samples": target_samples},
        "crosshair_samples": [
            {"canonical_time_ms": time_ms, "x": float(time_ms // 10 - 10), "y": 0.0,
             "confidence": 1.0}
            for time_ms in (0, 100, 200, 300)
        ],
        "target_change_points": [],
        "predictability_evidence": [],
    }


def test_tracking_recovers_known_geometric_lag_and_separate_alignment_latency():
    result = analyze_continuous_tracking_v1(_payload())

    assert result["support_status"] == "supported"
    assert result["metrics"]["continuous_tracking.relative_lag_ms"]["value"] == pytest.approx(100.0)
    assert result["metrics"]["continuous_tracking.velocity_gain"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.alignment_latency_ms"]["value"] == 12.0
    assert result["metrics"]["continuous_tracking.observed_change_response_ms"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.time_in_radius_ratio"]["value"] == 1.0


def test_fixed_viewport_center_keeps_geometry_and_withholds_player_motion_metrics():
    payload = _payload()
    payload["player_motion_status"] = "unavailable_fixed_viewport_center"
    for sample in payload["target_track"]["samples"]:
        sample["confidence"] = 0.89
        sample["measurement_complete"] = True

    result = analyze_continuous_tracking_v1(payload)

    assert result["support_status"] == "partial"
    assert result["metrics"]["continuous_tracking.target_relative_error_px"][
        "availability"
    ] == "available"
    assert result["metrics"]["continuous_tracking.time_in_radius_ratio"][
        "availability"
    ] == "available"
    for metric_key in (
        "continuous_tracking.relative_lag_ms",
        "continuous_tracking.phase_lag_ms",
        "continuous_tracking.coherence",
        "continuous_tracking.velocity_gain",
        "continuous_tracking.observed_change_response_ms",
        "continuous_tracking.correction_direction_reversal_count",
        "continuous_tracking.smoothness_acceleration_rms",
        "continuous_tracking.sparc",
    ):
        metric = result["metrics"][metric_key]
        assert metric["availability"] == "unavailable"
        assert "player_aim_motion_unavailable_fixed_viewport_center" in metric[
            "limitations"
        ]
    episode = next(
        row for row in result["processed_rows"]
        if row["row_kind"] == "tracking_episode"
    )
    assert episode["correction_burden"] is None
    assert episode["sparc"] is None


def test_tracking_emits_complete_distinct_rows_for_episode_and_fixed_window():
    result = analyze_continuous_tracking_v1(_payload())

    kinds = {row["row_kind"] for row in result["processed_rows"]}
    assert {"tracking_episode", "tracking_fixed_window"} <= kinds
    assert {table["event_kind"] for table in result["processed_event_tables"]} == kinds
    assert all(table["row_count"] > 0 for table in result["processed_event_tables"])
    assert all("rows" not in table for table in result["processed_event_tables"])


def test_tracking_detects_loss_and_reacquisition_without_severity_claims():
    payload = _payload(radius=5.0)
    payload["target_track"]["samples"] = [
        {"canonical_time_ms": time_ms, "x": 0.0, "y": 0.0, "radius": 5.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    payload["crosshair_samples"] = [
        {"canonical_time_ms": 0, "x": 0.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 100, "x": 10.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 200, "x": 10.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 300, "x": 0.0, "y": 0.0, "confidence": 1.0},
    ]

    result = analyze_continuous_tracking_v1(payload)

    assert result["metrics"]["continuous_tracking.loss_count"]["value"] == 1.0
    assert result["metrics"]["continuous_tracking.reacquisition_latency_ms"]["value"] == 200.0
    assert {"tracking_loss", "tracking_reacquisition"} <= {
        row["row_kind"] for row in result["processed_rows"]
    }
    assert "severity" not in result


def test_tracking_change_response_is_observed_and_not_human_reaction_time():
    payload = _payload(radius=15.0)
    payload["target_track"]["samples"] = [
        {"canonical_time_ms": 0, "x": 0.0, "y": 0.0, "radius": 15.0, "confidence": 1.0},
        {"canonical_time_ms": 100, "x": 10.0, "y": 0.0, "radius": 15.0, "confidence": 1.0},
        {"canonical_time_ms": 200, "x": 0.0, "y": 0.0, "radius": 15.0, "confidence": 1.0},
        {"canonical_time_ms": 300, "x": -10.0, "y": 0.0, "radius": 15.0, "confidence": 1.0},
    ]
    payload["crosshair_samples"] = [
        {"canonical_time_ms": 0, "x": 0.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 100, "x": 0.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 200, "x": 0.0, "y": 0.0, "confidence": 1.0},
        {"canonical_time_ms": 300, "x": -10.0, "y": 0.0, "confidence": 1.0},
    ]
    payload["target_change_points"] = [{"event_ref": "analysis:tracking:1:change:1", "time_ms": 200}]

    result = analyze_continuous_tracking_v1(payload)

    assert result["metrics"]["continuous_tracking.observed_change_response_ms"]["value"] == 100.0
    assert result["metrics"]["continuous_tracking.human_response_latency_ms"]["availability"] == "unavailable"
    assert any(row["row_kind"] == "tracking_change_response" for row in result["processed_rows"])


def test_time_in_radius_fails_closed_when_radius_is_missing():
    result = analyze_continuous_tracking_v1(_payload(radius=None))

    metric = result["metrics"]["continuous_tracking.time_in_radius_ratio"]
    assert metric["availability"] == "unavailable"
    assert "target_radius_unavailable" in metric["limitations"]
    assert result["metrics"]["continuous_tracking.loss_count"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.loss_duration_ms"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.reacquisition_latency_ms"]["availability"] == "unavailable"
    assert not {
        "tracking_loss", "tracking_reacquisition",
    }.intersection(row["row_kind"] for row in result["processed_rows"])


def test_partial_radius_coverage_withholds_all_loss_and_reacquisition_facts():
    payload = _payload(radius=5.0)
    payload["target_track"]["samples"][2].pop("radius")
    payload["crosshair_samples"] = [
        {"canonical_time_ms": time_ms, "x": 20.0, "y": 0.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]

    result = analyze_continuous_tracking_v1(payload)

    for metric_key in (
        "continuous_tracking.time_in_radius_ratio",
        "continuous_tracking.loss_count",
        "continuous_tracking.loss_duration_ms",
        "continuous_tracking.reacquisition_latency_ms",
    ):
        assert result["metrics"][metric_key]["availability"] == "unavailable"
        assert "target_radius_unavailable" in result["metrics"][metric_key]["limitations"]
    assert not {
        "tracking_loss", "tracking_reacquisition",
    }.intersection(row["row_kind"] for row in result["processed_rows"])


def test_predictive_lead_requires_accepted_motion_predictability_evidence():
    payload = _payload()
    descriptive = analyze_continuous_tracking_v1(payload)
    assert "continuous_tracking.predictive_lead_ms" not in descriptive["metrics"]

    payload["predictability_evidence"] = [{
        "schema_version": "motion_predictability_evidence.v1",
        "evidence_ref": "analysis:tracking:1:predictability:1",
        "segment_ref": "analysis:tracking:1:segment:tracking:1",
        "kind": "known_script",
        "model_ref": "script:tracking.fixture.v1",
        "model_version": "motion_model.v1",
        "fit_metric": "r2",
        "fit_metric_version": "r2.v1",
        "fit_value": 0.99,
        "threshold_ref": "threshold:tracking.predictability.fixture.v1",
        "acceptance": "accepted",
        "source_refs": ["analysis:tracking:1:source:fixture"],
        "availability": "available",
        "confidence": 1.0,
        "limitations": [],
    }]
    accepted = analyze_continuous_tracking_v1(payload)
    assert accepted["metrics"]["continuous_tracking.predictive_lead_ms"]["condition_refs"] == [
        "analysis:tracking:1:predictability:1"
    ]
    assert "analysis:tracking:1:predictability:1" in accepted["evidence_segments"][0]["event_refs"]
    assert any(
        event["event_kind"] == "motion_predictability_evidence"
        for event in accepted["evidence_extension"]["event_bundle"]["events"]
    )

    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:tracking:1",
        canonical_time_window=payload["canonical_time_window"],
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    extended = extend_analysis_evidence_with_continuous_tracking_v1(artifact, accepted)
    broken = deepcopy(extended)
    tracking_bundle = next(
        bundle for bundle in broken["event_bundles"]
        if any(event["event_kind"] == "tracking_episode" for event in bundle["events"])
    )
    tracking_bundle["events"] = [
        event for event in tracking_bundle["events"]
        if event["event_kind"] != "motion_predictability_evidence"
    ]
    with pytest.raises(ValueError, match="accepted predictability evidence"):
        validate_analysis_evidence_artifact_v1(broken)


def test_incomplete_predictability_declaration_cannot_unlock_predictive_lead():
    payload = _payload()
    payload["predictability_evidence"] = [{
        "evidence_ref": "analysis:tracking:1:predictability:1",
        "segment_ref": "analysis:tracking:1:segment:tracking:1",
        "acceptance": "accepted",
        "source_refs": ["analysis:tracking:1:source:fixture"],
    }]

    with pytest.raises(TrackingAnalysisError, match="predictability"):
        analyze_continuous_tracking_v1(payload)


def test_low_confidence_samples_are_excluded_and_quality_fails_closed():
    payload = _payload()
    payload["crosshair_samples"][1]["confidence"] = 0.2

    result = analyze_continuous_tracking_v1(payload)

    assert result["support_status"] == "partial"
    assert "low_confidence_or_occluded_samples_excluded" in result["limitations"]
    assert result["metrics"]["continuous_tracking.target_relative_error_px"]["population"]["excluded_count"] == 1
    assert result["metrics"]["continuous_tracking.loss_count"]["coverage"] < 1.0


def _periodic_payload(*, lag_ms: int, gain: float) -> dict:
    step_ms = 20
    sample_count = 256
    times = [index * step_ms for index in range(sample_count)]
    duration_ms = sample_count * step_ms
    payload = _payload(radius=100.0)
    payload["canonical_time_window"].update({
        "end_ms": duration_ms,
        "duration_ms": duration_ms,
    })
    payload["target_track"]["samples"] = [{
        "canonical_time_ms": time_ms,
        "x": 40.0 * sin(2 * pi * time_ms / 1_000.0),
        "y": 0.0,
        "radius": 100.0,
        "confidence": 1.0,
    } for time_ms in times]
    payload["crosshair_samples"] = [{
        "canonical_time_ms": time_ms,
        "x": gain * 40.0 * sin(2 * pi * (time_ms - lag_ms) / 1_000.0),
        "y": 0.0,
        "confidence": 1.0,
    } for time_ms in times]
    return payload


@pytest.mark.parametrize(("lag_ms", "gain"), [(0, 1.0), (100, 0.5), (100, 1.5)])
def test_frequency_metrics_require_long_uniform_steady_tracking(lag_ms, gain):
    result = analyze_continuous_tracking_v1(
        _periodic_payload(lag_ms=lag_ms, gain=gain)
    )

    assert result["metrics"]["continuous_tracking.phase_lag_ms"]["value"] == pytest.approx(
        lag_ms, abs=12.0,
    )
    assert result["metrics"]["continuous_tracking.velocity_gain"]["value"] == pytest.approx(
        gain, rel=0.05,
    )
    assert result["metrics"]["continuous_tracking.coherence"]["value"] > 0.95


def test_frequency_metrics_fail_closed_for_nonstationary_tracking():
    payload = _periodic_payload(lag_ms=0, gain=1.0)
    sample_count = len(payload["target_track"]["samples"])
    for index, (target, crosshair) in enumerate(zip(
        payload["target_track"]["samples"], payload["crosshair_samples"],
    )):
        drift = 240.0 * index / (sample_count - 1)
        target["x"] += drift
        crosshair["x"] += drift

    result = analyze_continuous_tracking_v1(payload)

    for metric_key in (
        "continuous_tracking.phase_lag_ms",
        "continuous_tracking.velocity_gain",
        "continuous_tracking.coherence",
    ):
        assert result["metrics"][metric_key]["availability"] == "unavailable"
        assert (
            "frequency_metrics_require_long_uniform_steady_segment"
            in result["metrics"][metric_key]["limitations"]
        )


def test_frequency_metrics_fail_closed_for_amplitude_drift():
    payload = _periodic_payload(lag_ms=0, gain=1.0)
    sample_count = len(payload["target_track"]["samples"])
    for index, (target, crosshair) in enumerate(zip(
        payload["target_track"]["samples"], payload["crosshair_samples"],
    )):
        scale = 1.0 + index / (sample_count - 1)
        target["x"] *= scale
        crosshair["x"] *= scale

    result = analyze_continuous_tracking_v1(payload)

    assert result["metrics"]["continuous_tracking.phase_lag_ms"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.velocity_gain"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.coherence"]["availability"] == "unavailable"


def test_frequency_metrics_fail_closed_when_dominant_frequency_changes():
    payload = _periodic_payload(lag_ms=0, gain=1.0)
    for index, (target, crosshair) in enumerate(zip(
        payload["target_track"]["samples"], payload["crosshair_samples"],
    )):
        local_time_s = (index % 64) * 0.02
        frequency_hz = 0.78125 if index < 128 else 1.5625
        value = 40.0 * sin(2 * pi * frequency_hz * local_time_s)
        target["x"] = value
        crosshair["x"] = value

    result = analyze_continuous_tracking_v1(payload)

    assert result["metrics"]["continuous_tracking.phase_lag_ms"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.velocity_gain"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.coherence"]["availability"] == "unavailable"


def test_frequency_metrics_fail_closed_for_zero_player_motion_without_nan():
    payload = _periodic_payload(lag_ms=0, gain=1.0)
    for sample in payload["crosshair_samples"]:
        sample["x"] = 0.0
        sample["y"] = 0.0

    result = analyze_continuous_tracking_v1(payload)

    assert result["metrics"]["continuous_tracking.phase_lag_ms"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.velocity_gain"]["availability"] == "unavailable"
    assert result["metrics"]["continuous_tracking.coherence"]["availability"] == "unavailable"


def test_tracking_extension_round_trips_through_shared_evidence_contract():
    payload = _payload()
    payload["crosshair_samples"][1]["confidence"] = 0.2
    result = analyze_continuous_tracking_v1(payload)
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:tracking:1",
        canonical_time_window=payload["canonical_time_window"],
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )

    extended = extend_analysis_evidence_with_continuous_tracking_v1(
        artifact,
        result,
    )
    tables = build_processed_event_table_catalog_v1(extended)

    assert {table["event_kind"] for table in tables} == {
        row["row_kind"] for row in result["processed_rows"]
    }
    assert sum(table["row_count"] for table in tables) == len(result["processed_rows"])
    assert all(table["completeness"] == "partial" for table in tables)


def test_rejects_predictability_evidence_for_another_segment():
    payload = _payload()
    payload["predictability_evidence"] = [{
        "schema_version": "motion_predictability_evidence.v1",
        "evidence_ref": "analysis:tracking:1:predictability:1",
        "segment_ref": "analysis:tracking:1:segment:other",
        "kind": "known_script",
        "model_ref": "script:tracking.fixture.v1",
        "model_version": "motion_model.v1",
        "fit_metric": "r2",
        "fit_metric_version": "r2.v1",
        "fit_value": 0.99,
        "threshold_ref": "threshold:tracking.predictability.fixture.v1",
        "acceptance": "accepted",
        "source_refs": ["analysis:tracking:1:source:fixture"],
        "availability": "available",
        "confidence": 1.0,
        "limitations": [],
    }]

    with pytest.raises(TrackingAnalysisError, match="another segment"):
        analyze_continuous_tracking_v1(payload)
