from __future__ import annotations

from copy import deepcopy

import hashlib
import json

import pandas as pd

import pytest

from kovaak_tracker.analysis_evidence import (
    EvidenceKeyRegistry,
    UnsupportedEvidenceContractVersion,
    build_analysis_evidence_artifact_v1,
    build_page_descriptor_v1,
    build_processed_event_table_catalog,
    build_processed_event_table_catalog_v1,
    page_normalized_outcomes,
    source_field_registry_v1,
    validate_analysis_evidence_artifact,
    validate_analysis_evidence_artifact_v2,
    validate_analysis_evidence_artifact_v1,
    validate_canonical_run_facts_v1,
    validate_continuous_lg_event_bundle_v1,
    validate_continuous_lg_rule_binding_v1,
    validate_event_bundle_v2,
    validate_event_bundle_v1,
    validate_evidence_segment_v1,
    validate_metric_record_v1,
    validate_normalized_outcome_timeline_v1,
    validate_signal_bundle_v1,
    validate_source_field_registry_v1,
)
from kovaak_tracker.csv_parser import KovaaKStats
from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceEvent,
    PerformanceHeader,
)


def _window() -> dict:
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 1_000,
        "end_ms": 11_000,
        "duration_ms": 10_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _facts() -> dict:
    return {
        "schema_version": "canonical_run_facts.v1",
        "analysis_ref": "analysis:7",
        "scenario_profile_ref": None,
        "canonical_time_window_ref": "analysis:7:canonical-window",
        "field_registry_version": "source_field_registry.v1",
        "source_contracts": [
            {
                "source_kind": "stats",
                "source_ref": "run:3:stats:abc",
                "parser_version": "kovaak_stats.v1",
                "source_schema_version": None,
                "recognized_schema_status": "recognized",
                "unknown_field_observability": "not_observable",
            }
        ],
        "sections": [
            {
                "section_key": "scenario",
                "facts": {"stats_display_name": "Fixture"},
                "present_field_keys": ["stats.summary.Scenario"],
                "source_absent_field_keys": ["stats.summary.Hash"],
                "omitted_known_fields": [],
                "completeness": "complete_allowlisted",
            }
        ],
        "outcome_record_sets": {
            "stats_kill_rows_ref": "analysis:7:stats-kill-rows",
            "performance_metric_changes_ref": None,
        },
        "completeness": "partial",
        "unknown_field_policy": "excluded",
        "limitations": [],
    }


def _record(index: int, *, canonical_time_ms: int | None = None) -> dict:
    return {
        "canonical_time_ms": canonical_time_ms if canonical_time_ms is not None else index,
        "source_time": {
            "clock_domain": "performance_challenge_relative",
            "value": index / 1000,
            "unit": "seconds",
            "precision": "float32",
        },
        "source_priority": 20,
        "source_event_index": index,
        "values": [
            {
                "metric_key": "performance.shotsFired",
                "value": 1,
                "value_semantics": "count_increment",
                "unit": "count",
            }
        ],
        "source_refs": ["run:3:performance:def"],
    }


def test_processed_static_table_catalog_covers_every_event_without_copying_rows():
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:7",
        canonical_time_window=_window(),
        scenario_profile_ref="scenario:static.fixture@1",
        stats=None,
        performance=None,
        stats_source_ref=None,
        performance_source_ref=None,
    )
    events = []
    for index, corrective_count in enumerate((3, 0, 1), 1):
        events.append({
            "event_id": f"analysis:7:event:static-flick:{index}",
            "event_kind": "static_flick",
            "start_ms": 1_000 + index * 100,
            "end_ms": 1_080 + index * 100,
            "actor_refs": [],
            "source_refs": ["run:3:trace:abc"],
            "confidence": 1.0,
            "attributes": {
                "quality": "available",
                "movement_duration_ms": 80,
                "corrective_count": corrective_count,
                "path_efficiency": 0.8,
                "sparc": -2.0,
            },
            "limitations": ["target_relative_facts_unavailable"],
        })
    artifact["event_bundles"] = [{
        "schema_version": "event_bundle.v1",
        "analysis_ref": "analysis:7",
        "events": events,
        "outcome_associations": [],
    }]

    tables = build_processed_event_table_catalog_v1(
        validate_analysis_evidence_artifact_v1(artifact)
    )

    assert len(tables) == 1
    table = tables[0]
    assert table["schema_version"] == "processed_event_table.v1"
    assert table["table_ref"] == "analysis:7:table:static_flick"
    assert table["row_count"] == 3
    assert table["included_count"] == 3
    assert table["excluded_count"] == 0
    assert table["completeness"] == "complete"
    assert "rows" not in table and "attributes" not in table
    fields = {field["field_key"]: field for field in table["field_catalog"]}
    assert fields["corrective_count"]["unit"] == "count"
    assert fields["sparc"]["metric_version"] == "native_flicking.sparc.v2"
    assert "target_error" not in fields


def test_source_field_registry_v1_has_exact_golden_coverage():
    registry = source_field_registry_v1()
    groups: dict[str, list[dict]] = {}
    for field in registry["fields"]:
        groups.setdefault(field["source_group"], []).append(field)

    assert {key: len(values) for key, values in groups.items()} == {
        "stats.summary": 26,
        "stats.config": 16,
        "stats.kill_row": 13,
        "stats.weapon_aggregate": 5,
        "performance.header": 4,
        "performance.profile": 12,
        "performance.metric_change": 17,
    }
    assert len({field["field_key"] for field in registry["fields"]}) == 93
    assert all(
        {"source_key", "canonical_key", "value_type", "unit", "projection_policy", "presence_policy"}
        <= set(field)
        for field in registry["fields"]
    )
    copy = source_field_registry_v1()
    copy["fields"][0]["source_key"] = "mutated"
    assert source_field_registry_v1()["fields"][0]["source_key"] != "mutated"
    with pytest.raises(ValueError, match="golden"):
        validate_source_field_registry_v1(copy)


def _parser_sources(*, cheated: int = 0, resolution: str = "1920x1080", rgba: str = "#AABBCCDD"):
    kill = {
        "Kill #": 1,
        "Timestamp": "00:00:01.500",
        "Bot": "Bot One",
        "Weapon": "Rifle",
        "TTK": 0.25,
        "Shots": 2,
        "Hits": 1,
        "Accuracy": 0.5,
        "Damage Done": 100.0,
        "Damage Possible": 100.0,
        "Efficiency": 1.0,
        "Cheated": cheated,
        "OverShots": 0,
        "time_s": 0.5,
    }
    stats = KovaaKStats(
        kills=pd.DataFrame([kill]),
        summary={
            "Scenario": "Fixture Scenario",
            "Hash": "hash-1",
            "Challenge Start": "00:00:01.000",
        },
        config={
            "Crosshair": "C:\\private\\crosshairs\\secret.png",
            "Crosshair Color": rgba,
            "Resolution": resolution,
        },
        file_name="stats.csv",
        weapon_aggregates=(
            {"Weapon": "Rifle", "Shots": 2, "Hits": 1, "Damage Done": 100.0, "Damage Possible": 100.0},
        ),
        field_presence={
            "summary": ("Scenario", "Hash", "Challenge Start"),
            "config": ("Crosshair", "Crosshair Color", "Resolution"),
            "weapon_aggregates": ("Weapon", "Shots", "Hits", "Damage Done", "Damage Possible"),
        },
    )
    profile_fields = {
        key: "source_absent"
        for key in (
            "time_limit", "player_profile", "added_bots", "player_max_lives",
            "bot_max_lives", "player_team", "bot_teams", "map_name", "map_scale",
            "timescale", "end_challenge_after_kills", "end_challenge_after_damage",
        )
    }
    performance = PerformanceData(
        header=PerformanceHeader(
            scenario_name="Fixture Scenario",
            scenario_hash="hash-1",
            challenge_start_utc=1_000,
            schema_version=1,
            challenge_profile=ChallengeProfile(field_presence=profile_fields),
            field_presence={
                "scenario_name": "present",
                "scenario_hash": "present",
                "challenge_start_utc": "present",
                "schema_version": "present",
                "challenge_profile": "present",
            },
        ),
        events=(
            PerformanceEvent(
                timestamp=0.5,
                payload_type="shotsFired",
                count=1,
                timestamp_ms=500,
                source_event_index=0,
            ),
        ),
        source_event_count=1,
    )
    return stats, performance


def test_parser_projection_preserves_allowlisted_facts_and_source_order():
    stats, performance = _parser_sources()
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:7",
        canonical_time_window=_window(),
        scenario_profile_ref=None,
        stats=stats,
        performance=performance,
        stats_source_ref="run:3:stats:abc",
        performance_source_ref="run:3:performance:def",
    )
    facts = artifact["canonical_run_facts"]
    assert facts["completeness"] == "complete_allowlisted"
    challenge = next(section for section in facts["sections"] if section["section_key"] == "challenge_configuration")
    calibration = next(section for section in facts["sections"] if section["section_key"] == "input_and_calibration")
    assert challenge["facts"]["crosshair_asset_configured"] is True
    assert challenge["facts"]["crosshair_color_rgba"] == "AABBCCDD"
    assert calibration["facts"]["resolution_width"] == 1920
    assert calibration["facts"]["resolution_height"] == 1080
    assert "private" not in json.dumps(artifact)
    records = artifact["normalized_outcome_records"]
    assert [(item["canonical_time_ms"], item["source_priority"]) for item in records] == [
        (1_500, 10),
        (1_500, 20),
    ]
    assert not any(value["metric_key"].startswith("stats.shot") for value in records[0]["values"])


def test_parser_projection_marks_invalid_known_values_partial_without_defaults():
    stats, performance = _parser_sources(cheated=2, resolution="bad", rgba="GGGGGGGG")
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:7",
        canonical_time_window=_window(),
        scenario_profile_ref=None,
        stats=stats,
        performance=performance,
        stats_source_ref="run:3:stats:abc",
        performance_source_ref="run:3:performance:def",
    )
    facts = artifact["canonical_run_facts"]
    assert facts["completeness"] == "partial"
    omitted = {
        item["field_key"]
        for section in facts["sections"]
        for item in section["omitted_known_fields"]
    }
    assert {
        "stats.config.Resolution",
        "stats.config.Crosshair Color",
        "stats.kill_row.Cheated",
    } <= omitted
    dumped = json.dumps(artifact)
    assert "resolution_width" not in dumped
    assert '"cheated"' not in dumped


def test_private_bundle_contracts_round_trip_and_reject_inline_samples():
    signal = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": "analysis:7",
        "canonical_time_window_ref": "analysis:7:canonical-window",
        "visual_quality_profile_ref": None,
        "observed_visual_domain": None,
        "channels": [
            {
                "channel_key": "mouse.speed",
                "source_refs": ["run:3:trace"],
                "coordinate_space": "mouse_counts",
                "unit": "counts_per_second",
                "sample_rate_semantics": "source_native",
                "samples_ref": "analysis:7:samples:mouse-speed",
                "coverage": 1.0,
                "confidence_summary": 1.0,
                "transform_version": "mouse-speed.v1",
                "limitations": [],
            }
        ],
    }
    assert validate_signal_bundle_v1(signal) == signal
    unsafe = deepcopy(signal)
    unsafe["channels"][0]["samples"] = [[0, 1.0]]
    with pytest.raises(ValueError, match="fields"):
        validate_signal_bundle_v1(unsafe)

    events = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": "analysis:7",
        "events": [
            {
                "event_id": "event:kill:1",
                "event_kind": "kill",
                "start_ms": 2_000,
                "end_ms": 2_001,
                "actor_refs": [],
                "source_refs": ["run:3:stats:abc"],
                "confidence": 1.0,
                "attributes": {"kill_index": 1},
                "limitations": [],
            }
        ],
        "outcome_associations": [],
    }
    assert validate_event_bundle_v1(events) == events
    bad = deepcopy(events)
    bad["events"][0]["attributes"]["raw_payload"] = "private"
    with pytest.raises(ValueError, match="attributes"):
        validate_event_bundle_v1(bad)


def _outcome_association_bundle() -> dict:
    def event(event_id: str, event_kind: str) -> dict:
        return {
            "event_id": event_id,
            "event_kind": event_kind,
            "start_ms": 2_000,
            "end_ms": 2_000,
            "actor_refs": [],
            "source_refs": ["run:3:video:abc"],
            "confidence": 1.0,
            "attributes": {},
            "limitations": [],
        }

    return {
        "schema_version": "event_bundle.v1",
        "analysis_ref": "analysis:7",
        "events": [
            event("event:shot:1", "shot"),
            event("event:hit:1", "hit"),
        ],
        "outcome_associations": [
            {
                "association_id": "association:visual:1",
                "shot_event_ref": "event:shot:1",
                "outcome_event_ref": "event:hit:1",
                "target_track_ref": "analysis:7:target-track:1",
                "weapon_temporal_model": "hitscan",
                "association_kind": "directly_observed",
                "source_refs": ["run:3:video:abc"],
                "confidence": 1.0,
                "availability": "available",
                "limitations": [],
            }
        ],
    }


@pytest.mark.parametrize(
    "association_patch",
    [
        {"association_kind": "inferred", "availability": "available"},
        {"association_kind": "validated_aligned"},
        {"association_kind": "directly_observed", "availability": "partial"},
        {"limitations": ["outcome_association_inferred"]},
        {"limitations": ["outcome_association_unavailable"]},
        {
            "association_kind": "inferred",
            "availability": "partial",
            "confidence": 0.5,
            "limitations": [],
        },
        {
            "association_kind": "inferred",
            "availability": "partial",
            "confidence": 0.5,
            "limitations": [
                "outcome_association_inferred",
                "outcome_association_unavailable",
            ],
        },
        {
            "association_kind": "inferred",
            "availability": "partial",
            "confidence": 1.0,
            "limitations": ["outcome_association_inferred"],
        },
        {
            "association_kind": "inferred",
            "availability": "unavailable",
            "outcome_event_ref": None,
            "confidence": 0.5,
            "limitations": ["outcome_association_unavailable"],
        },
        {
            "association_kind": "inferred",
            "availability": "unavailable",
            "confidence": 0.0,
            "limitations": ["outcome_association_unavailable"],
        },
        {
            "association_kind": "inferred",
            "availability": "unavailable",
            "outcome_event_ref": None,
            "confidence": 0.0,
            "limitations": [
                "outcome_association_inferred",
                "outcome_association_unavailable",
            ],
        },
        {"target_track_ref": None},
        {"target_track_ref": "analysis:8:target-track:1"},
        {"target_track_ref": "unrelated:target-track:1"},
        {"outcome_event_ref": None},
        {"outcome_event_ref": "event:shot:1"},
        {
            "association_kind": "validated_aligned",
            "source_refs": ["run:3:video:abc", "run:3:trace:def"],
        },
        {
            "association_kind": "validated_aligned",
            "source_refs": ["run:3:video:abc", "association-rule:fixture"],
        },
        {
            "association_kind": "validated_aligned",
            "source_refs": ["run:3:video:abc", "association-rule:fixture.v1"],
        },
    ],
)
def test_outcome_association_validator_rejects_inconsistent_cross_field_state(
    association_patch,
):
    bundle = _outcome_association_bundle()
    bundle["outcome_associations"][0].update(association_patch)

    with pytest.raises(ValueError, match="outcome association"):
        validate_event_bundle_v1(bundle)


def test_outcome_association_validator_accepts_inferred_and_unavailable_states():
    inferred = _outcome_association_bundle()
    inferred["outcome_associations"][0].update({
        "association_kind": "inferred",
        "availability": "partial",
        "confidence": 0.5,
        "limitations": ["outcome_association_inferred"],
    })
    assert validate_event_bundle_v1(inferred) == inferred

    unavailable = _outcome_association_bundle()
    unavailable["outcome_associations"][0].update({
        "association_kind": "inferred",
        "availability": "unavailable",
        "outcome_event_ref": None,
        "confidence": 0.0,
        "limitations": ["outcome_association_unavailable"],
    })
    assert validate_event_bundle_v1(unavailable) == unavailable

    first_damage = _outcome_association_bundle()
    first_damage["events"][1]["event_kind"] = "first_damage"
    assert validate_event_bundle_v1(first_damage) == first_damage


def test_public_contracts_round_trip_and_unknown_versions_fail_closed():
    assert validate_canonical_run_facts_v1(_facts()) == _facts()
    timeline = {
        "schema_version": "normalized_outcome_timeline.v1",
        "analysis_ref": "analysis:7",
        "scope": "whole_run",
        "segment_ref": None,
        "canonical_time_window_ref": "analysis:7:canonical-window",
        "mode": "exact_page",
        "resolution": "source_native",
        "selected_series": ["performance.shotsFired"],
        "overview_series": None,
        "records": [_record(1)],
        "event_refs": [],
        "completeness": "complete",
        "next_cursor": None,
        "limitations": [],
    }
    assert validate_normalized_outcome_timeline_v1(timeline) == timeline
    changed = deepcopy(timeline)
    changed["schema_version"] = "normalized_outcome_timeline.v99"
    with pytest.raises(UnsupportedEvidenceContractVersion):
        validate_normalized_outcome_timeline_v1(changed)

    injected = _facts()
    injected["sections"][0]["facts"]["private_parser_value"] = 1
    with pytest.raises(ValueError, match="allow-listed"):
        validate_canonical_run_facts_v1(injected)

    bad_record = deepcopy(timeline)
    bad_record["records"][0]["values"][0]["metric_key"] = "performance.privateField"
    with pytest.raises(ValueError, match="not registered"):
        validate_normalized_outcome_timeline_v1(bad_record)

    wrong_unit = deepcopy(timeline)
    wrong_unit["records"][0]["values"][0]["unit"] = "seconds"
    with pytest.raises(ValueError, match="unit"):
        validate_normalized_outcome_timeline_v1(wrong_unit)

    wrong_semantics = deepcopy(timeline)
    wrong_semantics["records"][0]["values"][0]["value_semantics"] = "instantaneous"
    with pytest.raises(ValueError, match="semantics"):
        validate_normalized_outcome_timeline_v1(wrong_semantics)

    too_many = deepcopy(timeline)
    too_many["selected_series"] = [
        "performance.shotsFired",
        "performance.shotsHit",
        "performance.shotsMissed",
        "performance.kills",
        "performance.deaths",
        "performance.overshots",
        "performance.reloads",
        "performance.pauseCount",
        "performance.score",
    ]
    with pytest.raises(ValueError, match="at most 8"):
        validate_normalized_outcome_timeline_v1(too_many)


def test_metric_and_segment_require_registered_keys_and_valid_bounds():
    registry = EvidenceKeyRegistry()
    registry.register_extension(
        {
            "schema_version": "analysis_evidence_extension.v1",
            "extension_ref": "test-family@1",
            "channel_keys": ["test.error"],
            "event_kinds": {"test_event": ["label"]},
            "metric_keys": ["test.metric"],
            "segment_kinds": ["test_typical"],
        }
    )
    metric = {
        "schema_version": "metric_record.v1",
        "metric_key": "test.metric",
        "metric_version": "test.metric.v1",
        "value": 2.0,
        "unit": "ratio",
        "availability": "available",
        "classification": "deterministic",
        "provenance": {"kind": "derived", "source_refs": ["run:3:trace"]},
        "population": {"sample_count": 3, "valid_count": 2, "excluded_count": 1},
        "distribution": {"min": 1.0, "p10": 1.1, "p25": 1.25, "median": 1.5, "p75": 1.75, "p90": 1.9, "max": 2.0, "histogram_bins": []},
        "condition_refs": [],
        "event_refs": [],
        "evidence_segment_refs": ["segment:typical:1"],
        "coverage": 2 / 3,
        "confidence": 0.9,
        "limitations": [],
    }
    assert validate_metric_record_v1(metric, registry=registry) == metric
    unregistered = deepcopy(metric)
    unregistered["metric_key"] = "test.unregistered"
    with pytest.raises(ValueError, match="registered"):
        validate_metric_record_v1(unregistered, registry=registry)

    segment = {
        "schema_version": "evidence_segment.v1",
        "segment_id": "segment:typical:1",
        "analysis_ref": "analysis:7",
        "analyzer_ref": "test-analyzer.v1",
        "segment_kind": "test_typical",
        "start_ms": 2_000,
        "end_ms": 5_000,
        "focus_start_ms": 2_500,
        "focus_end_ms": 4_000,
        "title_key": "evidence.test.typical",
        "rank_reason": "typical",
        "issue_refs": [],
        "metric_refs": ["test.metric@test.metric.v1"],
        "event_refs": [],
        "available_channels": ["test.error"],
        "source_coverage": 1.0,
        "confidence": 0.9,
        "video_playback": {"availability": "unavailable", "artifact_ref": None, "start_ms": None, "end_ms": None},
        "limitations": [],
    }
    assert validate_evidence_segment_v1(segment, canonical_window=_window(), registry=registry) == segment
    outside = deepcopy(segment)
    outside["end_ms"] = 12_000
    with pytest.raises(ValueError, match="canonical"):
        validate_evidence_segment_v1(outside, canonical_window=_window(), registry=registry)


def test_exact_pages_sort_same_time_records_and_limit_by_records():
    records = [_record(index, canonical_time_ms=5_000) for index in range(121)]
    records.reverse()
    descriptor = build_page_descriptor_v1(
        owner_id="owner:1",
        analysis_ref="analysis:7",
        evidence_revision="sha256:" + "a" * 64,
        scope="whole_run",
        segment_ref=None,
        selected_series=["performance.shotsFired"],
        offset=0,
    )
    page = page_normalized_outcomes(
        records,
        analysis_ref="analysis:7",
        canonical_time_window_ref="analysis:7:canonical-window",
        descriptor=descriptor,
    )
    timeline = page["timeline"]
    assert 0 < len(timeline["records"]) <= 120
    assert [record["source_event_index"] for record in timeline["records"]] == list(
        range(len(timeline["records"])),
    )
    assert timeline["completeness"] == "paged"
    assert page["next_page_descriptor"]["offset"] == len(timeline["records"])
    assert page["next_page_descriptor"]["query_digest"] == descriptor["query_digest"]
    assert len(
        json.dumps(
            page,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ) <= 24 * 1024

    with pytest.raises(ValueError, match="budget"):
        page_normalized_outcomes(
            records,
            analysis_ref="analysis:7",
            canonical_time_window_ref="analysis:7:canonical-window",
            descriptor=descriptor,
            byte_limit=24 * 1024 + 1,
        )


def test_segment_page_requires_authorized_bounds_and_filters_records():
    records = [_record(1, canonical_time_ms=2_000), _record(2, canonical_time_ms=8_000)]
    descriptor = build_page_descriptor_v1(
        owner_id="owner:1",
        analysis_ref="analysis:7",
        evidence_revision="sha256:" + "a" * 64,
        scope="evidence_segment",
        segment_ref="segment:1",
        selected_series=["performance.shotsFired"],
        offset=0,
    )
    with pytest.raises(ValueError, match="authorized bounds"):
        page_normalized_outcomes(
            records,
            analysis_ref="analysis:7",
            canonical_time_window_ref="analysis:7:canonical-window",
            descriptor=descriptor,
        )
    page = page_normalized_outcomes(
        records,
        analysis_ref="analysis:7",
        canonical_time_window_ref="analysis:7:canonical-window",
        descriptor=descriptor,
        segment_bounds=(1_000, 5_000),
    )
    assert [
        record["canonical_time_ms"]
        for record in page["timeline"]["records"]
    ] == [2_000]

def test_analysis_evidence_artifact_rejects_raw_or_path_payloads():
    artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": "analysis:7",
        "canonical_time_window": _window(),
        "canonical_run_facts": _facts(),
        "normalized_outcome_records": [_record(1, canonical_time_ms=1_001)],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }
    assert validate_analysis_evidence_artifact_v1(artifact) == artifact
    bad = deepcopy(artifact)
    bad["source_path"] = "C:\\private\\stats.csv"
    with pytest.raises(ValueError):
        validate_analysis_evidence_artifact_v1(bad)

    outside = deepcopy(artifact)
    outside["normalized_outcome_records"][0]["canonical_time_ms"] = 11_000
    with pytest.raises(ValueError, match="outside canonical window"):
        validate_analysis_evidence_artifact_v1(outside)

    missing_samples = deepcopy(artifact)
    missing_samples["signal_bundles"] = [
        {
            "schema_version": "signal_bundle.v1",
            "analysis_ref": "analysis:7",
            "canonical_time_window_ref": "analysis:7:canonical-window",
            "visual_quality_profile_ref": None,
            "observed_visual_domain": None,
            "channels": [
                {
                    "channel_key": "mouse.speed",
                    "source_refs": ["run:3:trace"],
                    "coordinate_space": "mouse_counts",
                    "unit": "counts_per_second",
                    "sample_rate_semantics": "source_native",
                    "samples_ref": "analysis:7:samples:missing",
                    "coverage": 1.0,
                    "confidence_summary": 1.0,
                    "transform_version": "mouse-speed.v1",
                    "limitations": [],
                }
            ],
        }
    ]
    with pytest.raises(ValueError, match="missing local sample"):
        validate_analysis_evidence_artifact_v1(missing_samples)


def _outcome_rule_binding() -> dict:
    binding = {
        "schema_version": "outcome_association_rule_binding.v1",
        "rule_ref": "outcome-association-rule:fixture@1",
        "scenario_profile_ref": "scenario-profile:fixture@1",
        "canonical_timebase_version": "test.v1",
        "raw_click_extractor_version": "raw-left-rising-edge.v1",
        "stats_parser_version": "kovaak_stats.v1",
        "outcome_semantics": "one_shot_kill",
        "weapon_temporal_model": "hitscan",
        "stats_predicate": {
            "shots_equals": 1,
            "hits_equals": 1,
            "overshots_equals": 0,
        },
        "timing_window_ms": {"minimum": 0, "maximum": 50},
        "track_predicate": {
            "identity_status": "stable",
            "max_sample_gap_ms": 20,
            "require_inner_hitbox": True,
            "hitbox_inset_px": 0.0,
            "minimum_sample_confidence": 1.0,
        },
        "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
        "fixture_set_ref": "fixture-set:outcome-association@1",
        "annotation_set_ref": "annotation-set:outcome-association@1",
    }
    digest = hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {**binding, "rule_sha256": digest}


def _validated_outcome_bundle_v2() -> dict:
    binding = _outcome_rule_binding()
    shot_ref = "analysis:7:event:raw-shot:1"
    kill_ref = "analysis:7:event:stats-kill:1"

    def event(event_id, event_kind, time_ms, source_ref, attributes):
        return {
            "event_id": event_id,
            "event_kind": event_kind,
            "start_ms": time_ms,
            "end_ms": time_ms,
            "actor_refs": [],
            "source_refs": [source_ref],
            "confidence": 1.0,
            "attributes": attributes,
            "limitations": [],
        }

    return {
        "schema_version": "event_bundle.v2",
        "analysis_ref": "analysis:7",
        "events": [
            event(shot_ref, "shot", 2_000, "run:3:trace:def", {}),
            event(
                kill_ref,
                "kill",
                2_010,
                "run:3:stats:abc",
                {"kill_index": 1, "shots": 1, "hits": 1, "overshots": 0},
            ),
        ],
        "outcome_association_rule_bindings": [binding],
        "outcome_associations": [
            {
                "association_id": "analysis:7:association:one-shot-kill:1",
                "shot_event_ref": shot_ref,
                "outcome_event_ref": kill_ref,
                "target_track_ref": "analysis:7:target-track:1",
                "weapon_temporal_model": "hitscan",
                "association_kind": "validated_aligned",
                "source_refs": [
                    "run:3:trace:def",
                    "run:3:stats:abc",
                    "run:3:video:abc",
                ],
                "validation": {
                    "schema_version": "outcome_association_validation.v1",
                    "rule_ref": binding["rule_ref"],
                    "rule_sha256": binding["rule_sha256"],
                    "scenario_profile_ref": "scenario-profile:fixture@1",
                    "canonical_time_window_ref": "analysis:7:canonical-window",
                    "raw_input_source_ref": "run:3:trace:def",
                    "stats_source_ref": "run:3:stats:abc",
                    "visual_source_ref": "run:3:video:abc",
                    "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
                    "click_time_ms": 2_000,
                    "outcome_time_ms": 2_010,
                    "click_to_outcome_ms": 10,
                    "temporal_candidate_count": 1,
                    "geometric_candidate_count": 1,
                    "stats_kill": {
                        "kill_index": 1,
                        "shots": 1,
                        "hits": 1,
                        "overshots": 0,
                    },
                    "track_check": {
                        "identity_status": "stable",
                        "sample_gap_ms": 0,
                        "sample_confidence": 1.0,
                        "center_distance_px": 2.0,
                        "effective_radius_px": 10.0,
                    },
                },
                "confidence": 1.0,
                "availability": "available",
                "limitations": [],
            }
        ],
    }


def _mixed_artifact_v2() -> dict:
    facts = _facts()
    facts["scenario_profile_ref"] = "scenario-profile:fixture@1"
    return {
        "schema_version": "analysis_evidence_artifact.v2",
        "analysis_ref": "analysis:7",
        "canonical_time_window": _window(),
        "canonical_run_facts": facts,
        "normalized_outcome_records": [{
            "canonical_time_ms": 2_010,
            "source_time": {
                "clock_domain": "stats_local_time_of_day",
                "value": 2.01,
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
            "source_refs": ["run:3:stats:abc"],
        }],
        "signal_bundles": [
            {
                "schema_version": "signal_bundle.v1",
                "analysis_ref": "analysis:7",
                "canonical_time_window_ref": "analysis:7:canonical-window",
                "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
                "observed_visual_domain": {"resolution": [200, 200]},
                "channels": [
                    {
                        "channel_key": f"target.1.{part}",
                        "source_refs": ["run:3:video:abc"],
                        "coordinate_space": "viewport_pixels",
                        "unit": unit,
                        "sample_rate_semantics": "video_frames",
                        "samples_ref": f"analysis:7:samples:target-1-{part}",
                        "coverage": 1.0,
                        "confidence_summary": 1.0,
                        "transform_version": "visual-target.v1",
                        "limitations": [],
                    }
                    for part, unit in (
                        ("position_x", "pixels"),
                        ("position_y", "pixels"),
                        ("visible_radius", "pixels"),
                    )
                ],
            }
        ],
        "event_bundles": [
            {
                "schema_version": "event_bundle.v1",
                "analysis_ref": "analysis:7",
                "events": [],
                "outcome_associations": [],
            },
            _validated_outcome_bundle_v2(),
        ],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [
            {
                "sample_set_id": f"analysis:7:samples:target-1-{part}",
                "channel_key": f"target.1.{part}",
                "unit": unit,
                "points": [[2_000, value]],
            }
            for part, unit, value in (
                ("position_x", "pixels", 102.0),
                ("position_y", "pixels", 100.0),
                ("visible_radius", "pixels", 10.0),
            )
        ],
        "limitations": [],
    }


def test_v2_artifact_mixes_frozen_visual_v1_and_validated_outcome_v2():
    artifact = _mixed_artifact_v2()

    assert validate_event_bundle_v2(artifact["event_bundles"][1]) == artifact["event_bundles"][1]
    assert validate_analysis_evidence_artifact_v2(artifact) == artifact
    assert validate_analysis_evidence_artifact(artifact) == artifact
    assert build_processed_event_table_catalog(artifact) == []

    legacy = deepcopy(artifact)
    legacy["schema_version"] = "analysis_evidence_artifact.v1"
    with pytest.raises(ValueError):
        validate_analysis_evidence_artifact_v1(legacy)

    falsified_geometry = deepcopy(artifact)
    falsified_geometry["sample_sets"][0]["points"][0][1] = 120.0
    with pytest.raises(ValueError, match="track calculation"):
        validate_analysis_evidence_artifact_v2(falsified_geometry)

    missing_facts = deepcopy(artifact)
    missing_facts["canonical_run_facts"] = None
    with pytest.raises(ValueError, match="canonical facts"):
        validate_analysis_evidence_artifact_v2(missing_facts)

    duplicate_association = deepcopy(artifact)
    copied = deepcopy(duplicate_association["event_bundles"][1]["outcome_associations"][0])
    copied["association_id"] = "analysis:7:association:one-shot-kill:duplicate"
    duplicate_association["event_bundles"][1]["outcome_associations"].append(copied)
    with pytest.raises(ValueError, match="one-to-one"):
        validate_analysis_evidence_artifact_v2(duplicate_association)

    extra_raw_candidate = deepcopy(artifact)
    extra_raw_candidate["event_bundles"][1]["events"].append({
        "event_id": "analysis:7:event:raw-shot:2",
        "event_kind": "shot",
        "start_ms": 2_005,
        "end_ms": 2_005,
        "actor_refs": [],
        "source_refs": ["run:3:trace:def"],
        "confidence": 1.0,
        "attributes": {},
        "limitations": [],
    })
    with pytest.raises(ValueError, match="temporal candidate"):
        validate_analysis_evidence_artifact_v2(extra_raw_candidate)

    overlapping_target = deepcopy(artifact)
    signal_bundle = overlapping_target["signal_bundles"][0]
    for part, unit, value in (
        ("position_x", "pixels", 100.0),
        ("position_y", "pixels", 100.0),
        ("visible_radius", "pixels", 10.0),
    ):
        sample_ref = f"analysis:7:samples:target-2-{part}"
        signal_bundle["channels"].append({
            "channel_key": f"target.2.{part}",
            "source_refs": ["run:3:video:abc"],
            "coordinate_space": "viewport_pixels",
            "unit": unit,
            "sample_rate_semantics": "video_frames",
            "samples_ref": sample_ref,
            "coverage": 1.0,
            "confidence_summary": 1.0,
            "transform_version": "visual-target.v1",
            "limitations": [],
        })
        overlapping_target["sample_sets"].append({
            "sample_set_id": sample_ref,
            "channel_key": f"target.2.{part}",
            "unit": unit,
            "points": [[2_000, value]],
        })
    with pytest.raises(ValueError, match="geometric candidate"):
        validate_analysis_evidence_artifact_v2(overlapping_target)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("outcome_association_rule_bindings", 0, "rule_sha256"), "0" * 64),
        (("outcome_associations", 0, "weapon_temporal_model"), "projectile"),
        (("outcome_associations", 0, "validation", "temporal_candidate_count"), 2),
        (("outcome_associations", 0, "validation", "geometric_candidate_count"), 0),
        (("outcome_associations", 0, "validation", "stats_kill", "shots"), 2),
        (("outcome_associations", 0, "validation", "track_check", "identity_status"), "crossing"),
        (("outcome_associations", 0, "validation", "track_check", "sample_gap_ms"), 21),
        (("outcome_associations", 0, "validation", "track_check", "sample_confidence"), 0.9),
        (("outcome_associations", 0, "validation", "track_check", "center_distance_px"), 11.0),
    ],
)
def test_event_bundle_v2_fails_closed_on_unreplayable_validation(path, value):
    bundle = _validated_outcome_bundle_v2()
    cursor = bundle
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value

    with pytest.raises(ValueError):
        validate_event_bundle_v2(bundle)


def test_outcome_rule_v1_rejects_non_exact_sample_confidence_threshold():
    bundle = _validated_outcome_bundle_v2()
    binding = bundle["outcome_association_rule_bindings"][0]
    binding["track_predicate"]["minimum_sample_confidence"] = 0.9
    digest_payload = {key: value for key, value in binding.items() if key != "rule_sha256"}
    binding["rule_sha256"] = hashlib.sha256(json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    bundle["outcome_associations"][0]["validation"]["rule_sha256"] = binding["rule_sha256"]

    with pytest.raises(ValueError, match="exact sample confidence"):
        validate_event_bundle_v2(bundle)


def _continuous_lg_binding() -> dict:
    binding = {
        "schema_version": "continuous_lg_kill_chain_rule_binding.v1",
        "rule_ref": "outcome-association-rule:continuous-lg-fixture@1",
        "scenario_profile_ref": "scenario-profile:continuous-lg-fixture@1",
        "canonical_timebase_version": "test.v1",
        "raw_hold_extractor_version": "raw-left-held-interval.v1",
        "stats_parser_version": "kovaak_stats.v1",
        "outcome_semantics": "continuous_lg_kill_chain",
        "weapon_temporal_model": "hitscan",
        "maximum_post_release_outcome_ms": 10,
        "track_predicate": {
            "identity_status": "stable",
            "max_sample_gap_ms": 20,
            "minimum_candidate_time_margin_ms": 16,
            "require_inner_hitbox": True,
            "hitbox_inset_px": 1.0,
            "minimum_sample_confidence": 1.0,
        },
        "visual_quality_profile_ref": "visual-quality-profile:continuous-lg@1",
        "fixture_set_ref": "fixture-set:continuous-lg@1",
        "annotation_set_ref": "annotation-set:continuous-lg@1",
    }
    digest = hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {**binding, "rule_sha256": digest}


def _continuous_lg_bundle() -> dict:
    binding = _continuous_lg_binding()
    return {
        "schema_version": "continuous_lg_event_bundle.v1",
        "analysis_ref": "analysis:continuous-lg:7",
        "rule_binding": binding,
        "events": [
            {
                "event_id": "analysis:continuous-lg:7:event:raw-hold:1",
                "event_kind": "input_hold",
                "start_ms": 2_000,
                "end_ms": 2_600,
                "source_refs": ["run:3:trace:def"],
                "confidence": 1.0,
                "attributes": {},
                "limitations": [],
            },
            {
                "event_id": "analysis:continuous-lg:7:event:stats-kill:1",
                "event_kind": "kill",
                "start_ms": 2_100,
                "end_ms": 2_100,
                "source_refs": ["run:3:stats:abc"],
                "confidence": 1.0,
                "attributes": {"kill_index": 1},
                "limitations": [],
            },
            {
                "event_id": "analysis:continuous-lg:7:event:stats-kill:2",
                "event_kind": "kill",
                "start_ms": 2_400,
                "end_ms": 2_400,
                "source_refs": ["run:3:stats:abc"],
                "confidence": 1.0,
                "attributes": {"kill_index": 2},
                "limitations": [],
            },
        ],
        "outcome_associations": [
            {
                "association_id": "analysis:continuous-lg:7:association:continuous-lg-kill:1",
                "held_interval_ref": "analysis:continuous-lg:7:event:raw-hold:1",
                "outcome_event_ref": "analysis:continuous-lg:7:event:stats-kill:1",
                "target_track_ref": "analysis:continuous-lg:7:target-track:1",
                "weapon_temporal_model": "hitscan",
                "association_kind": "validated_continuous_lg",
                "source_refs": ["run:3:trace:def", "run:3:stats:abc", "run:3:video:abc"],
                "validation": {
                    "schema_version": "continuous_lg_outcome_association_validation.v1",
                    "rule_ref": binding["rule_ref"],
                    "rule_sha256": binding["rule_sha256"],
                    "scenario_profile_ref": binding["scenario_profile_ref"],
                    "canonical_time_window_ref": "analysis:continuous-lg:7:canonical-window",
                    "raw_input_source_ref": "run:3:trace:def",
                    "stats_source_ref": "run:3:stats:abc",
                    "visual_source_ref": "run:3:video:abc",
                    "visual_quality_profile_ref": binding["visual_quality_profile_ref"],
                    "held_interval_start_ms": 2_000,
                    "held_interval_end_ms": 2_600,
                    "outcome_time_ms": 2_100,
                    "geometric_candidate_count": 1,
                    "track_check": {
                        "identity_status": "stable",
                        "sample_gap_ms": 0,
                        "sample_confidence": 1.0,
                        "center_distance_px": 1.0,
                        "effective_radius_px": 9.0,
                    },
                },
                "confidence": 1.0,
                "availability": "available",
                "limitations": [],
            },
            {
                "association_id": "analysis:continuous-lg:7:association:continuous-lg-kill:2",
                "held_interval_ref": "analysis:continuous-lg:7:event:raw-hold:1",
                "outcome_event_ref": "analysis:continuous-lg:7:event:stats-kill:2",
                "target_track_ref": "analysis:continuous-lg:7:target-track:2",
                "weapon_temporal_model": "hitscan",
                "association_kind": "validated_continuous_lg",
                "source_refs": ["run:3:trace:def", "run:3:stats:abc", "run:3:video:abc"],
                "validation": {
                    "schema_version": "continuous_lg_outcome_association_validation.v1",
                    "rule_ref": binding["rule_ref"],
                    "rule_sha256": binding["rule_sha256"],
                    "scenario_profile_ref": binding["scenario_profile_ref"],
                    "canonical_time_window_ref": "analysis:continuous-lg:7:canonical-window",
                    "raw_input_source_ref": "run:3:trace:def",
                    "stats_source_ref": "run:3:stats:abc",
                    "visual_source_ref": "run:3:video:abc",
                    "visual_quality_profile_ref": binding["visual_quality_profile_ref"],
                    "held_interval_start_ms": 2_000,
                    "held_interval_end_ms": 2_600,
                    "outcome_time_ms": 2_400,
                    "geometric_candidate_count": 1,
                    "track_check": {
                        "identity_status": "stable",
                        "sample_gap_ms": 0,
                        "sample_confidence": 1.0,
                        "center_distance_px": 1.0,
                        "effective_radius_px": 9.0,
                    },
                },
                "confidence": 1.0,
                "availability": "available",
                "limitations": [],
            },
        ],
    }


def test_continuous_lg_contract_allows_multiple_kills_for_one_held_interval():
    binding = _continuous_lg_binding()
    bundle = _continuous_lg_bundle()

    assert validate_continuous_lg_rule_binding_v1(binding) == binding
    assert validate_continuous_lg_event_bundle_v1(bundle) == bundle


@pytest.mark.parametrize(
    "mutate",
    [
        lambda bundle: bundle["outcome_associations"][1].update({
            "outcome_event_ref": bundle["outcome_associations"][0]["outcome_event_ref"],
        }),
        lambda bundle: bundle["outcome_associations"][0].update({
            "shot_event_ref": "analysis:continuous-lg:7:event:fake-shot",
        }),
        lambda bundle: bundle["events"][0].update({"event_kind": "shot"}),
        lambda bundle: bundle["outcome_associations"][0]["validation"]["track_check"].update({
            "identity_status": "ambiguous",
        }),
    ],
)
def test_continuous_lg_contract_rejects_duplicate_outcomes_fake_shots_and_unstable_identity(mutate):
    bundle = _continuous_lg_bundle()
    mutate(bundle)

    with pytest.raises(ValueError):
        validate_continuous_lg_event_bundle_v1(bundle)
