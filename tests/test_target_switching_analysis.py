from copy import deepcopy

import pytest

import kovaak_tracker.target_switching_analysis as target_switching_analysis

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
    validate_analysis_evidence_artifact_v1,
)
from kovaak_tracker.target_switching_analysis import (
    TargetSwitchingAnalysisError,
    analyze_target_switching_v1,
    build_switching_chains_from_stats_kills_v1,
    extend_analysis_evidence_with_target_switching_v1,
)


def _window(end_ms=400):
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": end_ms,
        "duration_ms": end_ms,
        "window_semantics": "half_open",
        "timebase_version": "time_alignment.test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _track(track_id, samples):
    return {
        "track_ref": f"analysis:switching:1:target-track:{track_id}",
        "episode_observable": True,
        "samples": [
            {
                "canonical_time_ms": time_ms,
                "x": x,
                "y": y,
                "visible_radius": 5.0,
                "confidence": 1.0,
            }
            for time_ms, x, y in samples
        ],
    }


def _attach_source_signals(payload):
    channels = []
    sample_sets = []

    def add(channel_key, samples, field):
        sample_set_id = (
            "analysis:switching:1:samples:" + channel_key.replace(".", "-")
        )
        channels.append({
            "channel_key": channel_key,
            "source_refs": ["fixture:video"],
            "coordinate_space": "capture_coordinates",
            "unit": "px",
            "sample_rate_semantics": "source_pts_irregular",
            "samples_ref": sample_set_id,
            "coverage": 1.0,
            "confidence_summary": 1.0,
            "transform_version": "fixture.v1",
            "limitations": [],
        })
        sample_sets.append({
            "sample_set_id": sample_set_id,
            "channel_key": channel_key,
            "unit": "px",
            "points": [
                [sample["canonical_time_ms"], sample[field]]
                for sample in samples
            ],
        })

    add("crosshair.position_x", payload["crosshair_samples"], "x")
    add("crosshair.position_y", payload["crosshair_samples"], "y")
    for track in payload["target_tracks"]:
        track_id = track["track_ref"].rsplit(":", 1)[1]
        add(f"target.{track_id}.position_x", track["samples"], "x")
        add(f"target.{track_id}.position_y", track["samples"], "y")
    payload["source_signal_bundle"] = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": "analysis:switching:1",
        "canonical_time_window_ref": "analysis:switching:1:canonical-window",
        "visual_quality_profile_ref": "visual-quality:fixture@1",
        "observed_visual_domain": None,
        "channels": channels,
    }
    payload["source_sample_sets"] = sample_sets


def _payload(*, visible_at_kill=True):
    next_samples = [
        (100, 40.0, 0.0),
        (125, 30.0, 0.0),
        (150, 20.0, 0.0),
        (175, 0.0, 0.0),
        (200, 0.0, 0.0),
        (225, 0.0, 0.0),
        (250, 0.0, 0.0),
    ]
    if not visible_at_kill:
        next_samples = next_samples[1:]
    payload = {
        "schema_version": "target_switching_input.v1",
        "analysis_ref": "analysis:switching:1",
        "canonical_time_window": _window(),
        "scenario_resolution": {"aim_family": "target_switching"},
        "visual_quality": {
            "status": "accepted",
            "enabled_metric_families": ["target_switching"],
            "limitations": [],
        },
        "crosshair_samples": [
            {"canonical_time_ms": time_ms, "x": 0.0, "y": 0.0, "confidence": 1.0}
            for time_ms in range(0, 400, 25)
        ],
        "target_tracks": [_track("next", next_samples)],
        "stats_kills": [
            {
                "event_ref": "analysis:switching:1:event:stats-kill:1",
                "time_ms": 100,
                "kill_index": 1,
                "source_ref": "fixture:stats",
            },
            {
                "event_ref": "analysis:switching:1:event:stats-kill:2",
                "time_ms": 300,
                "kill_index": 2,
                "source_ref": "fixture:stats",
            },
        ],
        "comparison": None,
    }
    _attach_source_signals(payload)
    payload["episodes"] = build_switching_chains_from_stats_kills_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        crosshair_samples=payload["crosshair_samples"],
        target_tracks=payload["target_tracks"],
        stats_kills=payload["stats_kills"],
    )
    return payload


def test_event_local_visual_builder_is_not_a_supported_switching_entry_point():
    assert not hasattr(
        target_switching_analysis,
        "build_switching_episodes_from_visual_v1",
    )


def test_rejected_visual_quality_is_outcome_only_without_processed_rows():
    payload = _payload()
    payload["visual_quality"] = {
        "status": "rejected",
        "enabled_metric_families": [],
        "limitations": ["visual_quality_below_threshold:minimum_coverage"],
    }

    result = analyze_target_switching_v1(payload)

    assert result["support_status"] == "outcome_only"
    assert result["processed_rows"] == []


def test_stats_kill_boundary_creates_a_local_switch_without_dead_target_identity():
    result = analyze_target_switching_v1(_payload())

    assert result["support_status"] == "supported"
    assert len(result["processed_rows"]) == 1
    row = result["processed_rows"][0]
    assert row["classification"] == "stats_bounded_switch_chain"
    assert row["leave_time_ms"] == 100
    assert row["acquire_time_ms"] == 175
    assert row["settle_time_ms"] == 225
    assert row["settle_duration_ms"] == 50
    assert row["transition_time_ms"] == 75
    assert row["transition_distance_px"] == 40.0
    assert row["path_efficiency"] == 1.0
    assert row["previous_target_track_ref"] is None
    assert row["previous_outcome_association_ref"] is None
    assert row["selected_target_track_ref"] is None
    assert row["first_shot_event_ref"] is None
    assert row["first_damage_event_ref"] is None
    assert row["terminal_correction_observed"] is None
    assert set(result["metrics"]) == {
        "target_switching.transition_time_ms",
        "target_switching.transition_distance_px",
        "target_switching.path_efficiency",
        "target_switching.settle_duration_ms",
    }
    event_kinds = {
        event["event_kind"]
        for event in result["evidence_extension"]["event_bundle"]["events"]
    }
    assert {"kill", "switch_chain", "transition", "next_target_acquired", "settle"} <= event_kinds
    assert not event_kinds.intersection({
        "target_selected", "switch_first_shot", "first_damage", "switch_previous_outcome",
    })


def test_stats_kill_transition_keeps_time_when_next_target_was_not_visible_at_kill():
    result = analyze_target_switching_v1(_payload(visible_at_kill=False))

    row = result["processed_rows"][0]
    assert row["transition_time_ms"] == 75
    assert row["transition_distance_px"] is None
    assert row["transition_path_length_px"] is None
    assert row["path_efficiency"] is None
    assert result["metrics"]["target_switching.transition_time_ms"]["availability"] == "available"
    assert result["metrics"]["target_switching.transition_distance_px"]["availability"] == "unavailable"


def test_reviewed_detector_confidence_can_form_a_safe_local_contact():
    payload = _payload()
    for sample in payload["target_tracks"][0]["samples"]:
        sample["confidence"] = 0.6
    payload["episodes"] = build_switching_chains_from_stats_kills_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        crosshair_samples=payload["crosshair_samples"],
        target_tracks=payload["target_tracks"],
        stats_kills=payload["stats_kills"],
    )

    result = analyze_target_switching_v1(payload)

    assert len(result["processed_rows"]) == 1
    assert result["processed_rows"][0]["settle_duration_ms"] == 50


def test_stats_kill_switching_accepts_real_epoch_milliseconds():
    payload = _payload()
    epoch_ms = 1_785_152_907_998
    payload["canonical_time_window"]["start_ms"] += epoch_ms
    payload["canonical_time_window"]["end_ms"] += epoch_ms
    for sample in payload["crosshair_samples"]:
        sample["canonical_time_ms"] += epoch_ms
    for track in payload["target_tracks"]:
        for sample in track["samples"]:
            sample["canonical_time_ms"] += epoch_ms
    for kill in payload["stats_kills"]:
        kill["time_ms"] += epoch_ms
    _attach_source_signals(payload)
    payload["episodes"] = build_switching_chains_from_stats_kills_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        crosshair_samples=payload["crosshair_samples"],
        target_tracks=payload["target_tracks"],
        stats_kills=payload["stats_kills"],
    )

    result = analyze_target_switching_v1(payload)

    assert result["processed_rows"][0]["leave_time_ms"] == epoch_ms + 100
    assert result["processed_rows"][0]["transition_time_ms"] == 75


def test_missing_stats_kills_stays_outcome_only_even_with_safe_visual_contact():
    payload = _payload()
    payload["stats_kills"] = []
    payload["episodes"] = []

    result = analyze_target_switching_v1(payload)

    assert result["support_status"] == "outcome_only"
    assert result["processed_rows"] == []
    assert result["metrics"] == {}
    assert result["limitations"] == ["stats_kill_boundary_unavailable"]


def test_local_ambiguity_drops_only_its_kill_window():
    payload = _payload()
    payload["canonical_time_window"] = _window(800)
    payload["crosshair_samples"] = [
        {"canonical_time_ms": time_ms, "x": 0.0, "y": 0.0, "confidence": 1.0}
        for time_ms in range(0, 800, 25)
    ]
    payload["target_tracks"] = [
        _track("first", [(100, 40, 0), (150, 20, 0), (175, 0, 0), (225, 0, 0), (275, 0, 0), (350, 0, 0)]),
        _track("ambiguous-a", [(425, 0, 0), (475, 0, 0), (525, 0, 0), (575, 0, 0)]),
        _track("ambiguous-b", [(425, 0, 0), (475, 0, 0), (525, 0, 0), (575, 0, 0)]),
        _track("last", [(700, 20, 0), (725, 0, 0), (750, 0, 0), (775, 0, 0)]),
    ]
    payload["stats_kills"] = [
        {"event_ref": f"analysis:switching:1:event:stats-kill:{index}", "time_ms": time_ms, "kill_index": index, "source_ref": "fixture:stats"}
        for index, time_ms in enumerate((100, 400, 650), 1)
    ]
    _attach_source_signals(payload)
    payload["episodes"] = build_switching_chains_from_stats_kills_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        crosshair_samples=payload["crosshair_samples"],
        target_tracks=payload["target_tracks"],
        stats_kills=payload["stats_kills"],
    )

    result = analyze_target_switching_v1(payload)

    assert [row["leave_time_ms"] for row in result["processed_rows"]] == [100, 650]
    assert [row["next_target_track_ref"].rsplit(":", 1)[1] for row in result["processed_rows"]] == ["first", "last"]


def test_analyzer_rejects_tampered_episodes_instead_of_reconstructing_them():
    payload = _payload()
    payload["episodes"] = deepcopy(payload["episodes"])
    payload["episodes"][0]["acquire_time_ms"] = 150

    with pytest.raises(TargetSwitchingAnalysisError, match="do not match"):
        analyze_target_switching_v1(payload)


def test_limited_visual_quality_can_only_produce_partial_switching_result():
    payload = _payload()
    payload["visual_quality"]["status"] = "limited"
    payload["visual_quality"]["limitations"] = ["local_target_episode_boundary"]

    result = analyze_target_switching_v1(payload)

    assert result["support_status"] == "partial"
    assert result["processed_rows"]


def test_switching_extension_round_trips_without_outcome_associations():
    payload = _payload()
    result = analyze_target_switching_v1(payload)
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    artifact["signal_bundles"].append(deepcopy(payload["source_signal_bundle"]))
    artifact["sample_sets"].extend(deepcopy(payload["source_sample_sets"]))
    artifact = validate_analysis_evidence_artifact_v1(artifact)

    extended = extend_analysis_evidence_with_target_switching_v1(artifact, result)

    assert len(extended["event_bundles"]) == 1
    assert extended["event_bundles"][0]["outcome_associations"] == []
    assert {metric["metric_key"] for metric in extended["metric_records"]} == set(result["metrics"])
