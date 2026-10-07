from __future__ import annotations

from copy import deepcopy
from math import isfinite, pi, sin

import pytest

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
    build_processed_event_table_catalog_v1,
    validate_analysis_evidence_artifact_v1,
)
from kovaak_tracker.tracking_analysis import (
    TrackingAnalysisError,
    aggregate_continuous_tracking_multi_target_v1,
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


def test_time_in_radius_metric_reports_sample_ratio_not_binary_median():
    payload = _payload()
    payload["crosshair_samples"][-1]["x"] = 50.0

    result = analyze_continuous_tracking_v1(payload)

    metric = result["metrics"]["continuous_tracking.time_in_radius_ratio"]
    assert metric["value"] == pytest.approx(0.75)
    assert metric["population"]["valid_count"] == 4


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


def _multi_target_payload(track_id: str, target_x: float, *, radius: float | None = 15.0) -> dict:
    samples = [
        {"canonical_time_ms": time_ms, "x": float(target_x), "y": 0.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    if radius is not None:
        for sample in samples:
            sample["radius"] = radius
    payload = _payload()
    payload["analysis_ref"] = "analysis:multi:1"
    payload["target_track"] = {
        "track_ref": f"analysis:multi:1:target-track:{track_id}",
        "samples": samples,
    }
    payload["crosshair_samples"] = [
        {"canonical_time_ms": time_ms, "x": 0.0, "y": 0.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    return payload


def _multi_target_entries(**kwargs) -> list[dict]:
    entries = []
    for track_id, target_x in (("1", 0.0), ("2", 100.0)):
        payload = _multi_target_payload(track_id, target_x, **kwargs)
        entries.append({
            "track_ref": payload["target_track"]["track_ref"],
            "payload": payload,
            "analysis": analyze_continuous_tracking_v1(payload),
        })
    return entries


def test_multi_target_aggregate_omits_time_in_radius_and_keeps_weighted_error():
    aggregate = aggregate_continuous_tracking_multi_target_v1(_multi_target_entries())

    assert aggregate["schema_version"] == "continuous_tracking_analysis.v1"
    assert aggregate["support_status"] == "supported"
    # 多目标局不产出在靶率（产品拍板 2026-10-07）：顶层无该指标，聚合结果
    # 标注 limitation；逐轨数值保留在 per_target。
    assert "continuous_tracking.time_in_radius_ratio" not in aggregate["metrics"]
    assert "time_in_radius_requires_single_target" in aggregate["limitations"]
    error = aggregate["metrics"]["continuous_tracking.target_relative_error_px"]
    assert error["value"] == pytest.approx(50.0)
    assert "multi_target_time_weighted_average" in error["limitations"]
    assert len(aggregate["per_target"]) == 2
    assert aggregate["per_target"][0]["track_ref"] == "analysis:multi:1:target-track:1"
    assert aggregate["per_target"][0]["support_status"] == "supported"
    assert aggregate["per_target"][1]["result"]["metrics"][
        "continuous_tracking.time_in_radius_ratio"
    ]["value"] == pytest.approx(0.0)
    assert "multi_target_union_of_target_tracks" in aggregate["limitations"]


def test_multi_target_aggregate_without_radius_still_keeps_weighted_error():
    aggregate = aggregate_continuous_tracking_multi_target_v1(
        _multi_target_entries(radius=None),
    )

    assert "continuous_tracking.time_in_radius_ratio" not in aggregate["metrics"]
    assert "time_in_radius_requires_single_target" in aggregate["limitations"]
    error = aggregate["metrics"]["continuous_tracking.target_relative_error_px"]
    assert error["availability"] == "available"
    assert error["value"] == pytest.approx(50.0)


def test_multi_target_aggregate_round_trips_through_evidence_extension():
    aggregate = aggregate_continuous_tracking_multi_target_v1(_multi_target_entries())
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:multi:1",
        canonical_time_window=_multi_target_payload("1", 0.0)["canonical_time_window"],
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )

    extended = extend_analysis_evidence_with_continuous_tracking_v1(artifact, aggregate)

    keys = [metric["metric_key"] for metric in extended["metric_records"]]
    assert "continuous_tracking.time_in_radius_ratio" not in keys
    assert keys.count("continuous_tracking.target_relative_error_px") == 1


def test_multi_target_aggregate_requires_two_bound_entries():
    with pytest.raises(TrackingAnalysisError, match="at least two"):
        aggregate_continuous_tracking_multi_target_v1([])
    entries = _multi_target_entries()
    other = _multi_target_payload("3", 0.0)
    other["analysis_ref"] = "analysis:multi:2"
    entries.append({
        "track_ref": other["target_track"]["track_ref"],
        "payload": other,
        "analysis": analyze_continuous_tracking_v1(other),
    })
    with pytest.raises(TrackingAnalysisError, match="another analysis or window"):
        aggregate_continuous_tracking_multi_target_v1(entries)


# ---------------------------------------------------------------------------
# fused raw input 运动样本（player_motion_samples）：真实鼠标运动缝
# ---------------------------------------------------------------------------


def _fused_motion_payload(*, motion_lag_ms: int = 0, radius: float = 100.0) -> dict:
    """钉死视口中心准星 + fused 运动样本（真实 tracking 遥测形态）。

    目标 1Hz 正弦（40px），运动样本按 motion_lag_ms 滞后跟随；准星样本
    全部钉在 (0, 0)（unavailable_fixed_viewport_center 语义）。
    """
    payload = _periodic_payload(lag_ms=0, gain=1.0)
    payload["player_motion_status"] = "unavailable_fixed_viewport_center"
    for sample in payload["crosshair_samples"]:
        sample["x"] = 0.0
        sample["y"] = 0.0
    for sample in payload["target_track"]["samples"]:
        sample["radius"] = radius
    payload["player_motion_samples"] = [
        {
            "canonical_time_ms": time_ms,
            "x": 40.0 * sin(2 * pi * (time_ms - motion_lag_ms) / 1_000.0),
            "y": 0.0,
        }
        for time_ms in range(0, 256 * 20, 20)
    ]
    return payload


def test_fused_player_motion_samples_unlock_tracking_kinematics():
    payload = _fused_motion_payload(motion_lag_ms=100)

    result = analyze_continuous_tracking_v1(payload)

    assert result["support_status"] == "supported"
    for metric_key in (
        "continuous_tracking.sparc",
        "continuous_tracking.smoothness_acceleration_rms",
        "continuous_tracking.correction_direction_reversal_count",
        "continuous_tracking.relative_lag_ms",
        "continuous_tracking.phase_lag_ms",
        "continuous_tracking.velocity_gain",
        "continuous_tracking.coherence",
    ):
        metric = result["metrics"][metric_key]
        assert metric["availability"] == "available", metric_key
        assert metric["value"] is not None and isfinite(metric["value"]), metric_key
    # 频谱由插值运动序列驱动：滞后 100ms / 增益 1 的跟随者可复原。
    assert result["metrics"]["continuous_tracking.phase_lag_ms"]["value"] == pytest.approx(
        100.0, abs=12.0,
    )
    assert result["metrics"]["continuous_tracking.velocity_gain"]["value"] == pytest.approx(
        1.0, rel=0.05,
    )
    assert result["metrics"]["continuous_tracking.coherence"]["value"] > 0.95
    # provenance limitation：result 与受影响 metric record 携带，钉死口径退场。
    assert "player_aim_motion_from_fused_raw_input_trace" in result["limitations"]
    assert "player_aim_motion_unavailable_fixed_viewport_center" not in result["limitations"]
    assert "player_aim_motion_from_fused_raw_input_trace" in (
        result["metrics"]["continuous_tracking.sparc"]["limitations"]
    )
    assert "player_aim_motion_from_fused_raw_input_trace" in (
        result["metrics"]["continuous_tracking.smoothness_acceleration_rms"]["limitations"]
    )
    episode = next(
        row for row in result["processed_rows"]
        if row["row_kind"] == "tracking_episode"
    )
    assert episode["sparc"] is not None
    assert episode["correction_burden"] is not None


def test_removing_player_motion_samples_keeps_geometry_records_byte_identical():
    payload_with = _fused_motion_payload(motion_lag_ms=100, radius=15.0)
    payload_without = deepcopy(payload_with)
    payload_without.pop("player_motion_samples")

    with_motion = analyze_continuous_tracking_v1(payload_with)
    without_motion = analyze_continuous_tracking_v1(payload_without)

    # error/time_in_radius 及其派生（loss/reacquisition/fixed window 几何）
    # record 逐字节不变：运动学序列绝不进准星位置几何。
    for metric_key in (
        "continuous_tracking.target_relative_error_px",
        "continuous_tracking.time_in_radius_ratio",
        "continuous_tracking.loss_count",
        "continuous_tracking.loss_duration_ms",
        "continuous_tracking.reacquisition_latency_ms",
    ):
        assert with_motion["metrics"][metric_key] == without_motion["metrics"][metric_key]
    geometry_fields = (
        "start_ms", "end_ms", "sample_count", "usable_sample_count",
        "target_relative_error_px", "time_in_radius_ratio",
    )

    def _fixed_geometry(result):
        return [
            {field: row[field] for field in geometry_fields}
            for row in result["processed_rows"]
            if row["row_kind"] == "tracking_fixed_window"
        ]

    assert _fixed_geometry(with_motion) == _fixed_geometry(without_motion)
    outcome_kinds = {"tracking_loss", "tracking_reacquisition"}
    assert [
        row for row in with_motion["processed_rows"] if row["row_kind"] in outcome_kinds
    ] == [
        row for row in without_motion["processed_rows"] if row["row_kind"] in outcome_kinds
    ]
    # 缺字段回落既有口径：运动学 unavailable + 钉死 limitation。
    assert without_motion["metrics"]["continuous_tracking.sparc"]["availability"] == "unavailable"
    assert (
        "player_aim_motion_unavailable_fixed_viewport_center"
        in without_motion["limitations"]
    )
    assert with_motion["metrics"]["continuous_tracking.sparc"]["availability"] == "available"


def test_motion_samples_never_enter_crosshair_position_geometry():
    payload = _payload()
    payload["player_motion_status"] = "unavailable_fixed_viewport_center"
    # 离谱运动值若泄入准星位置，误差几何立即变形（钉死准星恒差 10px）。
    payload["player_motion_samples"] = [
        {"canonical_time_ms": time_ms, "x": 9999.0, "y": -9999.0}
        for time_ms in (0, 100, 200, 300)
    ]

    result = analyze_continuous_tracking_v1(payload)

    error = result["metrics"]["continuous_tracking.target_relative_error_px"]
    assert error["value"] == pytest.approx(10.0)
    assert result["metrics"]["continuous_tracking.time_in_radius_ratio"]["value"] == 1.0
    episode = next(
        row for row in result["processed_rows"]
        if row["row_kind"] == "tracking_episode"
    )
    assert episode["target_relative_error_px"] == pytest.approx(10.0)


def test_fused_motion_velocity_interpolates_and_takes_precedence():
    # 匀速 0.1 px/ms 直线运动（密采样覆盖 shared_times 内点）：线性插值回
    # 0/100/200/300 后速度仍恒定 → 加速度 rms=0、修正反转=0。
    payload = _payload()
    payload["player_motion_status"] = "unavailable_fixed_viewport_center"
    payload["player_motion_samples"] = [
        {"canonical_time_ms": time_ms, "x": time_ms * 0.1, "y": 0.0}
        for time_ms in range(0, 401, 40)
    ]

    result = analyze_continuous_tracking_v1(payload)

    rms = result["metrics"]["continuous_tracking.smoothness_acceleration_rms"]
    assert rms["availability"] == "available"
    assert rms["value"] == pytest.approx(0.0, abs=1e-9)
    reversal = result["metrics"]["continuous_tracking.correction_direction_reversal_count"]
    assert reversal["availability"] == "available"
    assert reversal["value"] == 0.0

    # precedence：status available（准星样本匀速 → rms 0）与运动样本并存时，
    # 速度取自运动序列（速度 0→1→0 → 加速度 1,-1 → rms=1.0）。
    payload_both = _payload()
    payload_both["player_motion_samples"] = [
        {"canonical_time_ms": time_ms, "x": 0.0 if time_ms <= 100 else 100.0, "y": 0.0}
        for time_ms in (0, 100, 200, 300)
    ]
    result_both = analyze_continuous_tracking_v1(payload_both)
    rms_both = result_both["metrics"]["continuous_tracking.smoothness_acceleration_rms"]
    assert rms_both["availability"] == "available"
    assert rms_both["value"] == pytest.approx(1.0)


def test_invalid_player_motion_samples_fail_closed():
    base = _payload()
    invalid_series = (
        [],
        "not-a-list",
        ["nope"],
        [{"canonical_time_ms": -5, "x": 0.0, "y": 0.0}],
        [{"canonical_time_ms": 0.5, "x": 0.0, "y": 0.0}],
        [{"canonical_time_ms": 0, "x": "bad", "y": 0.0}],
        [{"canonical_time_ms": 0},],
        # 时间戳乱序 / 重复：显式拒绝。
        [
            {"canonical_time_ms": 100, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 50, "x": 1.0, "y": 0.0},
        ],
        [
            {"canonical_time_ms": 100, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 100, "x": 1.0, "y": 0.0},
        ],
    )
    for bad in invalid_series:
        payload = deepcopy(base)
        payload["player_motion_samples"] = bad
        with pytest.raises(TrackingAnalysisError):
            analyze_continuous_tracking_v1(payload)


# ---------------------------------------------------------------------------
# 多目标聚合的运动学指标合并浮面（样本量加权，gated 门控 fail-closed）
# ---------------------------------------------------------------------------


_MOTION_KINEMATIC_KEYS = (
    "continuous_tracking.relative_lag_ms",
    "continuous_tracking.phase_lag_ms",
    "continuous_tracking.velocity_gain",
    "continuous_tracking.coherence",
    "continuous_tracking.correction_direction_reversal_count",
    "continuous_tracking.smoothness_acceleration_rms",
    "continuous_tracking.sparc",
)
_GATED_MOTION_KINEMATIC_KEYS = (
    "continuous_tracking.relative_lag_ms",
    "continuous_tracking.phase_lag_ms",
    "continuous_tracking.velocity_gain",
    "continuous_tracking.coherence",
)


def _fused_motion_multi_target_entries():
    """2 轨各带 player_motion_samples 的多目标聚合入口。

    两轨共用 analysis_ref 与 canonical window；跟随滞后不同（100ms vs 0ms），
    第二轨目标在末段驻留一个样本（该样本 lag 不可判定），保证自由合并项的
    "仅 available 轨计入" 语义有真实分轨差异。
    """
    entries = []
    for track_id, motion_lag_ms in (("1", 100), ("2", 0)):
        payload = _fused_motion_payload(motion_lag_ms=motion_lag_ms)
        payload["analysis_ref"] = "analysis:multi:motion:1"
        payload["target_track"]["track_ref"] = (
            f"analysis:multi:motion:1:target-track:{track_id}"
        )
        if track_id == "2":
            stationary = payload["target_track"]["samples"][5]
            stationary["x"] = payload["target_track"]["samples"][4]["x"]
        entries.append({
            "track_ref": payload["target_track"]["track_ref"],
            "payload": payload,
            "analysis": analyze_continuous_tracking_v1(payload),
        })
    return entries


def _linear_motion_track_payload(track_id: str, *, offset_px: float, hold_last: bool) -> dict:
    """线性目标 + 同步运动样本的钉死准星 payload（lag 可手算：t + offset/slope）。"""
    payload = _payload(radius=200.0)
    payload["analysis_ref"] = "analysis:multi:linear:1"
    payload["player_motion_status"] = "unavailable_fixed_viewport_center"
    samples = [
        {"canonical_time_ms": time_ms, "x": offset_px + 0.5 * time_ms, "y": 0.0,
         "radius": 200.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    if hold_last:
        samples[3]["x"] = samples[2]["x"]
    payload["target_track"] = {
        "track_ref": f"analysis:multi:linear:1:target-track:{track_id}",
        "samples": samples,
    }
    payload["crosshair_samples"] = [
        {"canonical_time_ms": time_ms, "x": 0.0, "y": 0.0, "confidence": 1.0}
        for time_ms in (0, 100, 200, 300)
    ]
    payload["player_motion_samples"] = [
        {"canonical_time_ms": sample["canonical_time_ms"], "x": sample["x"], "y": 0.0}
        for sample in samples
    ]
    return payload


def test_multi_target_aggregate_merges_motion_kinematics_to_top_level():
    # 手算口径：lag = t + offset/slope。轨 A（offset 0）→ 有效 lag
    # {100,200,300}（t=0 无前序运动）median 200、valid_count 3；轨 B
    # （offset 50、末样本驻留）→ 有效 lag {200,300}（t=0 与驻留样本不可
    # 判定）median 250、valid_count 2。
    entry_a = {
        "track_ref": "analysis:multi:linear:1:target-track:1",
        "payload": _linear_motion_track_payload("1", offset_px=0.0, hold_last=False),
    }
    entry_b = {
        "track_ref": "analysis:multi:linear:1:target-track:2",
        "payload": _linear_motion_track_payload("2", offset_px=50.0, hold_last=True),
    }
    for entry in (entry_a, entry_b):
        entry["analysis"] = analyze_continuous_tracking_v1(entry["payload"])
    aggregate = aggregate_continuous_tracking_multi_target_v1([entry_a, entry_b])

    # 样本量加权合并浮面：value = (200*3 + 250*2) / 5 = 220（≠ 简单均值
    # 225），limitation 码在。
    merged = aggregate["metrics"]["continuous_tracking.relative_lag_ms"]
    assert merged["availability"] == "available"
    assert merged["value"] == pytest.approx(220.0)
    assert merged["value"] != pytest.approx(225.0)
    assert "multi_target_union_motion_kinematics" in merged["limitations"]
    assert merged["distribution"]["min"] == pytest.approx(200.0)
    assert merged["distribution"]["max"] == pytest.approx(250.0)

    # 门控项（频域）：两轨均不可判定（样本过短）→ 顶层 unavailable。
    for metric_key in (
        "continuous_tracking.phase_lag_ms",
        "continuous_tracking.velocity_gain",
        "continuous_tracking.coherence",
    ):
        merged = aggregate["metrics"][metric_key]
        assert merged["availability"] == "unavailable", metric_key
        assert "multi_target_union_motion_kinematics" in merged["limitations"]

    # 自由合并项：匀速运动 → 反转 0、rms 为两轨均值；两轨 sparc 均不可判定
    # （样本过短）→ 顶层 unavailable（全 unavailable → unavailable）。
    reversal = aggregate["metrics"]["continuous_tracking.correction_direction_reversal_count"]
    assert reversal["availability"] == "available"
    assert reversal["value"] == 0.0
    rms = aggregate["metrics"]["continuous_tracking.smoothness_acceleration_rms"]
    assert rms["availability"] == "available"
    track_rms = [
        entry["analysis"]["metrics"]["continuous_tracking.smoothness_acceleration_rms"]["value"]
        for entry in (entry_a, entry_b)
    ]
    assert rms["value"] == pytest.approx(sum(track_rms) / 2)
    sparc = aggregate["metrics"]["continuous_tracking.sparc"]
    assert sparc["availability"] == "unavailable"
    assert sparc["value"] is None
    assert "multi_target_union_motion_kinematics" in sparc["limitations"]

    # 逐轨明细保留：per_target 各自携带运动学 record（B 的逐轨 lag=250）。
    assert len(aggregate["per_target"]) == 2
    assert aggregate["per_target"][1]["result"]["metrics"][
        "continuous_tracking.relative_lag_ms"
    ]["value"] == pytest.approx(250.0)


def test_multi_target_aggregate_kinematics_fail_closed_when_any_track_unavailable():
    entries = _fused_motion_multi_target_entries()
    entries[1]["payload"].pop("player_motion_samples")
    entries[1]["analysis"] = analyze_continuous_tracking_v1(entries[1]["payload"])

    aggregate = aggregate_continuous_tracking_multi_target_v1(entries)

    # 门控项：任一轨 unavailable → 顶层 unavailable（fail-closed）。
    for metric_key in _GATED_MOTION_KINEMATIC_KEYS:
        merged = aggregate["metrics"][metric_key]
        assert merged["availability"] == "unavailable", metric_key
        assert merged["value"] is None, metric_key
        assert "multi_target_union_motion_kinematics" in merged["limitations"]

    # 自由合并项：仅 available 轨计入权重，值 = 该轨值。
    for metric_key in (
        "continuous_tracking.correction_direction_reversal_count",
        "continuous_tracking.smoothness_acceleration_rms",
        "continuous_tracking.sparc",
    ):
        merged = aggregate["metrics"][metric_key]
        assert merged["availability"] == "available", metric_key
        assert merged["value"] == pytest.approx(
            entries[0]["analysis"]["metrics"][metric_key]["value"],
        ), metric_key


def test_single_target_kinematics_records_are_untouched_by_multi_target_merge():
    result = analyze_continuous_tracking_v1(_fused_motion_payload(motion_lag_ms=100))

    assert "per_target" not in result
    for metric_key in _MOTION_KINEMATIC_KEYS:
        record = result["metrics"][metric_key]
        assert record["availability"] == "available", metric_key
        assert "multi_target_union_motion_kinematics" not in record["limitations"]
    assert result["metrics"]["continuous_tracking.relative_lag_ms"][
        "population"
    ]["sample_count"] == 256
