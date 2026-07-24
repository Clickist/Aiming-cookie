from copy import deepcopy
import hashlib
import json

import pytest

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
    build_processed_event_table_catalog_v1,
    validate_analysis_evidence_artifact_v1,
)
from kovaak_tracker.target_switching_analysis import (
    TargetSwitchingAnalysisError,
    analyze_target_switching_v1,
    build_switching_chains_from_visual_outcomes_v1,
    extend_analysis_evidence_with_target_switching_v1,
)
from kovaak_tracker.outcome_association import associate_one_shot_kills_v1


def _association(association_id, shot_ref, outcome_ref, target_ref, kind="directly_observed"):
    return {
        "association_id": association_id,
        "shot_event_ref": shot_ref,
        "outcome_event_ref": outcome_ref,
        "target_track_ref": target_ref,
        "weapon_temporal_model": "hitscan",
        "association_kind": kind,
        "source_refs": ["fixture:visual-source.v1"],
        "availability": "available",
        "confidence": 1.0 if kind == "directly_observed" else 0.9,
        "limitations": [],
    }


def _source_event(event_id, event_kind, time_ms):
    return {
        "event_id": event_id,
        "event_kind": event_kind,
        "start_ms": time_ms,
        "end_ms": time_ms,
        "actor_refs": [],
        "source_refs": ["fixture:visual-source.v1"],
        "confidence": 1.0,
        "attributes": {},
        "limitations": [],
    }


def _attach_source_signals(payload):
    channels = []
    sample_sets = []

    def add_channel(channel_key, samples, field):
        sample_ref = f"analysis:switching:1:samples:{channel_key.replace('.', '-')}"
        channels.append({
            "channel_key": channel_key,
            "source_refs": ["fixture:visual-source.v1"],
            "coordinate_space": "capture_coordinates",
            "unit": "px",
            "sample_rate_semantics": "source_pts_irregular",
            "samples_ref": sample_ref,
            "coverage": 1.0,
            "confidence_summary": 1.0,
            "transform_version": "fixture.v1",
            "limitations": [],
        })
        sample_sets.append({
            "sample_set_id": sample_ref,
            "channel_key": channel_key,
            "unit": "px",
            "points": [
                [sample["canonical_time_ms"], sample[field]]
                for sample in samples
            ],
        })

    add_channel("crosshair.position_x", payload["crosshair_samples"], "x")
    add_channel("crosshair.position_y", payload["crosshair_samples"], "y")
    for track in payload["target_tracks"]:
        track_id = track["track_ref"].rsplit(":", 1)[1]
        add_channel(f"target.{track_id}.position_x", track["samples"], "x")
        add_channel(f"target.{track_id}.position_y", track["samples"], "y")
        radius_samples = [
            {"canonical_time_ms": sample["canonical_time_ms"], "radius": 5.0}
            for sample in track["samples"]
        ]
        add_channel(f"target.{track_id}.visible_radius", radius_samples, "radius")
    payload["source_signal_bundle"] = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": "analysis:switching:1",
        "canonical_time_window_ref": "analysis:switching:1:canonical-window",
        "visual_quality_profile_ref": "profile:fixture.v1",
        "observed_visual_domain": None,
        "channels": channels,
    }
    payload["source_sample_sets"] = sample_sets


def _payload():
    payload = {
        "schema_version": "target_switching_input.v1",
        "analysis_ref": "analysis:switching:1",
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 0,
            "end_ms": 1_000,
            "duration_ms": 1_000,
            "window_semantics": "half_open",
            "timebase_version": "time_alignment.test.v1",
            "start_source": "fixture",
            "end_source": "fixture",
            "warnings": [],
        },
        "scenario_resolution": {"aim_family": "target_switching"},
        "visual_quality": {
            "status": "accepted",
            "enabled_metric_families": ["target_switching"],
            "limitations": [],
        },
        "target_tracks": [
            {
                "track_ref": "analysis:switching:1:target-track:previous",
                "samples": [
                    {"canonical_time_ms": 100, "x": 0.0, "y": 0.0},
                    {"canonical_time_ms": 200, "x": 0.0, "y": 0.0},
                ],
            },
            {
                "track_ref": "analysis:switching:1:target-track:next",
                "samples": [
                    {"canonical_time_ms": 100, "x": 30.0, "y": 40.0},
                    {"canonical_time_ms": 125, "x": 15.0, "y": 20.0},
                    {"canonical_time_ms": 150, "x": 0.0, "y": 0.0},
                    {"canonical_time_ms": 175, "x": -5.0, "y": 0.0},
                    {"canonical_time_ms": 200, "x": 0.0, "y": 0.0},
                ],
            },
            {
                "track_ref": "analysis:switching:1:target-track:other",
                "samples": [
                    {"canonical_time_ms": 100, "x": -10.0, "y": 0.0},
                    {"canonical_time_ms": 200, "x": -10.0, "y": 0.0},
                ],
            },
        ],
        "crosshair_samples": [
            {"canonical_time_ms": 100, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 125, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 150, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 175, "x": 0.0, "y": 0.0},
            {"canonical_time_ms": 200, "x": 0.0, "y": 0.0},
        ],
        "source_event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": "analysis:switching:1",
            "events": [
                _source_event("event:shot:previous", "shot", 90),
                _source_event("event:hit:previous", "hit", 100),
                _source_event("event:shot:next", "shot", 200),
                _source_event("event:hit:next", "hit", 200),
            ],
            "outcome_associations": [
                _association(
                    "association:previous", "event:shot:previous", "event:hit:previous",
                    "analysis:switching:1:target-track:previous",
                ),
                _association(
                    "association:next", "event:shot:next", "event:hit:next",
                    "analysis:switching:1:target-track:next",
                ),
            ],
        },
        "chains": [
            {
                "chain_ref": "chain:1",
                "source_refs": ["fixture:switch-chain.v1"],
                "previous_outcome_association_ref": "association:previous",
                "previous_outcome_time_ms": 100,
                "leave_time_ms": 100,
                "candidate_track_refs": [
                    "analysis:switching:1:target-track:next",
                    "analysis:switching:1:target-track:other",
                ],
                "selection_observation": {
                    "association_kind": "directly_observed",
                    "observation_ref": "fixture:selection:1",
                    "selected_target_track_ref": "analysis:switching:1:target-track:next",
                },
                "next_target_track_ref": "analysis:switching:1:target-track:next",
                "acquire_time_ms": 150,
                "settle_time_ms": 200,
                "first_shot_time_ms": 200,
                "first_damage_association_ref": "association:next",
                "first_damage_time_ms": 200,
                "carry_over_overshoot": {
                    "association_kind": "directly_observed",
                    "observation_ref": "fixture:overshoot:1",
                    "observed": True,
                },
                "terminal_correction": {
                    "association_kind": "directly_observed",
                    "observation_ref": "fixture:terminal-correction:1",
                    "observed": True,
                },
            }
        ],
    }
    _attach_source_signals(payload)
    return payload


def _artifact_with_source_evidence(payload):
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:switching:1",
        canonical_time_window=payload["canonical_time_window"],
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    artifact["signal_bundles"].append(deepcopy(payload["source_signal_bundle"]))
    artifact["event_bundles"].append(payload["source_event_bundle"])
    artifact["sample_sets"].extend(deepcopy(payload["source_sample_sets"]))
    return validate_analysis_evidence_artifact_v1(artifact)


def _visual_chain_input(payload):
    target_tracks = deepcopy(payload["target_tracks"])
    for track in target_tracks:
        track["identity_observable"] = True
        for sample in track["samples"]:
            sample.update({"visible_radius": 5.0, "confidence": 1.0})
    crosshair_samples = deepcopy(payload["crosshair_samples"])
    for sample in crosshair_samples:
        sample["confidence"] = 1.0
    return {
        "analysis_ref": payload["analysis_ref"],
        "canonical_time_window": payload["canonical_time_window"],
        "crosshair_samples": crosshair_samples,
        "target_tracks": target_tracks,
        "source_event_bundle": payload["source_event_bundle"],
    }


def _validated_kill_chain_bundle_v2(payload):
    binding = {
        "schema_version": "outcome_association_rule_binding.v1",
        "rule_ref": "outcome-association-rule:switching-fixture@1",
        "scenario_profile_ref": "scenario:switching.fixture@1",
        "canonical_timebase_version": "time_alignment.test.v1",
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
        "visual_quality_profile_ref": "profile:fixture.v1",
        "fixture_set_ref": "fixture-set:switching@1",
        "annotation_set_ref": "annotation-set:switching@1",
    }
    binding["rule_sha256"] = hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    result = associate_one_shot_kills_v1(
        analysis_ref=payload["analysis_ref"],
        canonical_time_window=payload["canonical_time_window"],
        scenario_profile_ref=binding["scenario_profile_ref"],
        visual_quality_profile_ref=binding["visual_quality_profile_ref"],
        raw_input_source_ref="fixture:raw-source.v1",
        stats_source_ref="fixture:stats-source.v1",
        stats_parser_version="kovaak_stats.v1",
        visual_source_ref="fixture:visual-source.v1",
        click_events=[
            {"event_ref": "event:shot:previous", "time_ms": 90},
            {"event_ref": "event:shot:next", "time_ms": 200},
        ],
        stats_kills=[
            {
                "event_ref": "event:kill:previous", "time_ms": 100,
                "kill_index": 1, "shots": 1, "hits": 1, "overshots": 0,
            },
            {
                "event_ref": "event:kill:next", "time_ms": 200,
                "kill_index": 2, "shots": 1, "hits": 1, "overshots": 0,
            },
        ],
        viewport_size=[200, 200],
        target_tracks=[
            {
                "track_ref": "analysis:switching:1:target-track:previous",
                "identity_status": "stable",
                "samples": [{
                    "canonical_time_ms": 90, "x": 100.0, "y": 100.0,
                    "radius": 5.0, "confidence": 1.0,
                }],
            },
            {
                "track_ref": "analysis:switching:1:target-track:next",
                "identity_status": "stable",
                "samples": [{
                    "canonical_time_ms": 200, "x": 100.0, "y": 100.0,
                    "radius": 5.0, "confidence": 1.0,
                }],
            },
        ],
        rule_registry={
            "schema_version": "outcome_association_rule_registry.v1",
            "registry_version": "fixture.v1",
            "entries": [{"status": "active", "binding": binding}],
        },
    )
    assert result["status"] == "available"
    return result["event_bundle"]


def test_switching_chain_emits_transition_acquire_and_first_damage_from_validated_observations():
    payload = _payload()
    assert {
        (sample["x"], sample["y"])
        for sample in payload["crosshair_samples"]
    } == {(0.0, 0.0)}

    result = analyze_target_switching_v1(payload)

    row = result["processed_rows"][0]
    assert row["classification"] == "observable_target_switch"
    assert row["transition_time_ms"] == 50
    assert row["transition_distance_px"] == 50.0
    assert row["transition_direction_deg"] == pytest.approx(53.130102)
    assert row["transition_path_length_px"] == 50.0
    assert row["path_efficiency"] == 1.0
    assert row["first_damage_latency_ms"] == 50
    assert row["carry_over_overshoot"] is True
    assert row["terminal_correction_observed"] is True
    events = result["evidence_extension"]["event_bundle"]["events"]
    assert {event["event_kind"] for event in events} >= {
        "switch_previous_outcome", "leave_previous", "candidate_visible",
        "target_selected", "transition", "next_target_acquired", "settle",
        "switch_first_shot", "first_damage", "switch_chain",
    }
    assert result["metrics"]["target_switching.transition_time_ms"]["value"] == 50.0
    assert result["metrics"]["target_switching.path_efficiency"]["value"] == 1.0


def test_chain_producer_uses_fixed_center_radius_and_direct_outcomes_only():
    payload = _payload()

    chains = build_switching_chains_from_visual_outcomes_v1(
        **_visual_chain_input(payload)
    )

    assert chains == [{
        "chain_ref": "analysis:switching:1:observed-switch-chain:1",
        "source_refs": ["association:next", "association:previous"],
        "previous_outcome_association_ref": "association:previous",
        "previous_outcome_time_ms": 100,
        "leave_time_ms": 100,
        "candidate_track_refs": [
            "analysis:switching:1:target-track:next",
            "analysis:switching:1:target-track:other",
        ],
        "selection_observation": None,
        "next_target_track_ref": "analysis:switching:1:target-track:next",
        "acquire_time_ms": 150,
        "settle_time_ms": 150,
        "first_shot_time_ms": 200,
        "next_outcome_association_ref": "association:next",
        "next_outcome_time_ms": 200,
        "first_damage_association_ref": "association:next",
        "first_damage_time_ms": 200,
        "carry_over_overshoot": None,
        "terminal_correction": None,
    }]


def test_kill_outcome_anchors_transition_without_becoming_first_damage():
    payload = _payload()
    payload["source_event_bundle"]["events"][3]["event_kind"] = "kill"

    chains = build_switching_chains_from_visual_outcomes_v1(
        **_visual_chain_input(payload)
    )

    assert len(chains) == 1
    assert chains[0]["next_outcome_association_ref"] == "association:next"
    assert chains[0]["first_damage_association_ref"] is None
    assert chains[0]["first_damage_time_ms"] is None

    payload["chains"] = chains
    result = analyze_target_switching_v1(payload)
    row = result["processed_rows"][0]
    assert row["first_shot_event_ref"] == "event:shot:next"
    assert row["first_damage_event_ref"] is None
    assert row["first_damage_latency_ms"] is None
    assert "first_damage_not_observed" in row["limitations"]
    assert "first_damage" not in {
        event["event_kind"]
        for event in result["evidence_extension"]["event_bundle"]["events"]
    }


def test_kill_can_be_previous_outcome_for_a_later_direct_damage_chain():
    payload = _payload()
    payload["source_event_bundle"]["events"][1]["event_kind"] = "kill"

    chains = build_switching_chains_from_visual_outcomes_v1(
        **_visual_chain_input(payload)
    )

    assert len(chains) == 1
    assert chains[0]["previous_outcome_association_ref"] == "association:previous"
    assert chains[0]["first_damage_association_ref"] == "association:next"


def test_validated_stats_kills_support_switch_identity_without_first_damage_claim():
    payload = _payload()
    chain_input = _visual_chain_input(payload)
    chain_input["source_event_bundle"] = _validated_kill_chain_bundle_v2(payload)

    chains = build_switching_chains_from_visual_outcomes_v1(**chain_input)

    assert len(chains) == 1
    assert chains[0]["previous_outcome_association_ref"].endswith("one-shot-kill:1")
    assert chains[0]["next_outcome_association_ref"].endswith("one-shot-kill:2")
    assert chains[0]["first_damage_association_ref"] is None


def test_chain_producer_rejects_inferred_outcomes_and_ambiguous_identity():
    payload = _payload()
    chain_input = _visual_chain_input(payload)
    associations = chain_input["source_event_bundle"]["outcome_associations"]
    associations[0].update({
        "association_kind": "inferred",
        "availability": "partial",
        "confidence": 0.5,
        "limitations": ["outcome_association_inferred"],
    })

    assert build_switching_chains_from_visual_outcomes_v1(**chain_input) == []

    chain_input = _visual_chain_input(payload)
    chain_input["target_tracks"][1]["identity_observable"] = False

    assert build_switching_chains_from_visual_outcomes_v1(**chain_input) == []


def test_concurrent_candidate_selection_is_observable_only_with_direct_or_validated_observation():
    payload = _payload()
    payload["chains"][0]["selection_observation"] = None

    row = analyze_target_switching_v1(payload)["processed_rows"][0]

    assert row["selected_target_track_ref"] is None
    assert "selection_unobservable" in row["limitations"]
    assert "target_selected" not in {
        event["event_kind"]
        for event in analyze_target_switching_v1(payload)["evidence_extension"]["event_bundle"]["events"]
    }


def test_ambiguous_identity_degrades_to_unclassified_discrete_acquisition_without_selection_guess():
    payload = _payload()
    chain = payload["chains"][0]
    payload["target_tracks"][1]["identity_observable"] = False

    result = analyze_target_switching_v1(payload)
    row = result["processed_rows"][0]

    assert row["classification"] == "unclassified_discrete_acquisition"
    assert row["selected_target_track_ref"] is None
    assert row["transition_time_ms"] is None
    assert "selection_matches_expected" not in row
    assert any(
        event["event_kind"] == "unclassified_discrete_acquisition"
        for event in result["evidence_extension"]["event_bundle"]["events"]
    )
    assert [segment["title_key"] for segment in result["evidence_segments"]] == [
        "target_switching.unclassified_acquisition"
    ]
    assert all(
        metric["availability"] == "unavailable"
        for metric in result["metrics"].values()
    )


def test_switching_metrics_are_bound_to_the_observable_chain_condition():
    result = analyze_target_switching_v1(_payload())

    for metric in result["metrics"].values():
        assert metric["condition_refs"] == [
            "condition:target_switching:observable_chain"
        ]
        assert metric["event_refs"] == ["analysis:switching:1:switch-chain:1"]
        assert all("unclassified-acquisition" not in ref for ref in metric["evidence_segment_refs"])


def test_first_damage_and_previous_outcome_require_direct_or_validated_association():
    payload = _payload()
    associations = payload["source_event_bundle"]["outcome_associations"]
    for association in associations:
        association["association_kind"] = "inferred"
        association["availability"] = "partial"
        association["confidence"] = 0.5
        association["limitations"] = ["outcome_association_inferred"]

    row = analyze_target_switching_v1(payload)["processed_rows"][0]

    assert row["classification"] == "unclassified_discrete_acquisition"
    assert row["first_damage_latency_ms"] is None
    assert "previous_outcome_association_unavailable" in row["limitations"]
    assert "first_damage_association_unavailable" in row["limitations"]


def test_previous_outcome_target_missing_from_analyzer_tracks_degrades_without_crashing():
    payload = _payload()
    previous = payload["source_event_bundle"]["outcome_associations"][0]
    previous["target_track_ref"] = "analysis:switching:1:target-track:not-provided"

    result = analyze_target_switching_v1(payload)
    row = result["processed_rows"][0]

    assert row["classification"] == "unclassified_discrete_acquisition"
    assert row["previous_outcome_association_ref"] is None
    assert row["previous_target_track_ref"] is None
    assert "previous_target_identity_unavailable" in row["limitations"]


def test_first_damage_for_another_target_does_not_leak_shot_or_damage_refs():
    payload = _payload()
    next_association = payload["source_event_bundle"]["outcome_associations"][1]
    next_association["target_track_ref"] = "analysis:switching:1:target-track:other"

    result = analyze_target_switching_v1(payload)
    row = result["processed_rows"][0]

    assert row["classification"] == "observable_target_switch"
    assert row["first_shot_event_ref"] is None
    assert row["first_shot_latency_ms"] is None
    assert row["first_damage_event_ref"] is None
    assert row["first_damage_latency_ms"] is None
    assert "first_damage_association_unavailable" in row["limitations"]
    assert result["evidence_extension"]["required_outcome_associations"] == [
        payload["source_event_bundle"]["outcome_associations"][0]
    ]


def test_segments_and_tables_cover_every_processed_row_with_bounded_contracts():
    result = analyze_target_switching_v1(_payload())

    assert len(result["processed_rows"]) == 1
    assert {segment["title_key"] for segment in result["evidence_segments"]} == {
        "target_switching.selection", "target_switching.transition",
        "target_switching.acquisition", "target_switching.terminal_control",
    }
    for segment in result["evidence_segments"]:
        assert "analysis:switching:1:switch-chain:1" in segment["event_refs"]
    assert result["processed_event_tables"][0]["row_count"] == 1
    assert "rows" not in result["processed_event_tables"][0]


def test_validated_aligned_association_without_registered_rule_fails_closed():
    payload = _payload()
    association = payload["source_event_bundle"]["outcome_associations"][1]
    association["association_kind"] = "validated_aligned"
    association["confidence"] = 0.9

    with pytest.raises(TargetSwitchingAnalysisError, match="source outcome evidence"):
        analyze_target_switching_v1(payload)


def test_visual_quality_gate_returns_outcome_only_without_derived_rows():
    payload = _payload()
    payload["visual_quality"]["enabled_metric_families"] = []

    result = analyze_target_switching_v1(payload)

    assert result["support_status"] == "outcome_only"
    assert result["processed_rows"] == []
    assert result["metrics"] == {}


def test_duplicate_switch_chain_ref_is_rejected_before_aggregation():
    payload = _payload()
    payload["chains"].append(dict(payload["chains"][0]))

    with pytest.raises(TargetSwitchingAnalysisError, match="duplicate switch chain ref"):
        analyze_target_switching_v1(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("previous_outcome_time_ms", 90), ("first_damage_time_ms", 300)],
)
def test_formal_outcome_times_must_match_source_events(field, value):
    payload = _payload()
    payload["chains"][0][field] = value

    with pytest.raises(TargetSwitchingAnalysisError, match="does not match source evidence"):
        analyze_target_switching_v1(payload)


def test_analyzer_trajectory_must_match_source_signal_samples():
    payload = _payload()
    payload["crosshair_samples"][1]["x"] += 1.0

    with pytest.raises(TargetSwitchingAnalysisError, match="crosshair samples do not match"):
        analyze_target_switching_v1(payload)


@pytest.mark.parametrize("change_channel_unit", [False, True])
def test_source_position_units_must_match_and_be_pixels(change_channel_unit):
    payload = _payload()
    sample_set = next(
        value for value in payload["source_sample_sets"]
        if value["channel_key"] == "crosshair.position_x"
    )
    sample_set["unit"] = "ms"
    if change_channel_unit:
        channel = next(
            value for value in payload["source_signal_bundle"]["channels"]
            if value["channel_key"] == "crosshair.position_x"
        )
        channel["unit"] = "ms"

    with pytest.raises(TargetSwitchingAnalysisError, match="source signal|unit must be px"):
        analyze_target_switching_v1(payload)


def test_switching_extension_round_trips_through_shared_evidence_contract():
    payload = _payload()
    result = analyze_target_switching_v1(payload)
    artifact = _artifact_with_source_evidence(payload)

    extended = extend_analysis_evidence_with_target_switching_v1(artifact, result)
    tables = build_processed_event_table_catalog_v1(extended)

    assert [table["event_kind"] for table in tables] == ["switch_chain"]
    assert tables[0]["row_count"] == len(result["processed_rows"])


def test_switching_extension_requires_exact_source_association_not_only_same_id():
    payload = _payload()
    result = analyze_target_switching_v1(payload)
    artifact = deepcopy(_artifact_with_source_evidence(payload))
    next_association = next(
        association
        for association in artifact["event_bundles"][0]["outcome_associations"]
        if association["association_id"] == "association:next"
    )
    next_association.update({
        "shot_event_ref": "event:shot:previous",
        "outcome_event_ref": "event:hit:previous",
        "target_track_ref": "analysis:switching:1:target-track:previous",
    })
    artifact = validate_analysis_evidence_artifact_v1(artifact)

    with pytest.raises(TargetSwitchingAnalysisError, match="does not match"):
        extend_analysis_evidence_with_target_switching_v1(artifact, result)


def test_switching_extension_requires_exact_source_signal_samples():
    payload = _payload()
    result = analyze_target_switching_v1(payload)
    artifact = deepcopy(_artifact_with_source_evidence(payload))
    crosshair_x = next(
        sample_set for sample_set in artifact["sample_sets"]
        if sample_set["channel_key"] == "crosshair.position_x"
    )
    crosshair_x["points"][1][1] += 1.0
    artifact = validate_analysis_evidence_artifact_v1(artifact)

    with pytest.raises(TargetSwitchingAnalysisError, match="sample set does not match"):
        extend_analysis_evidence_with_target_switching_v1(artifact, result)


def test_switching_extension_requires_exact_canonical_window_contents():
    payload = _payload()
    result = analyze_target_switching_v1(payload)
    artifact = deepcopy(_artifact_with_source_evidence(payload))
    artifact["canonical_time_window"]["end_ms"] = 2_000
    artifact["canonical_time_window"]["duration_ms"] = 2_000
    artifact = validate_analysis_evidence_artifact_v1(artifact)

    with pytest.raises(TargetSwitchingAnalysisError, match="canonical window does not match"):
        extend_analysis_evidence_with_target_switching_v1(artifact, result)
