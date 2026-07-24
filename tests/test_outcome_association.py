from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from kovaak_tracker.outcome_association import (
    associate_one_shot_kills_v1,
    validate_outcome_association_rule_registry_v1,
)


def _binding() -> dict:
    value = {
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
            "hitbox_inset_px": 1.0,
            "minimum_sample_confidence": 1.0,
        },
        "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
        "fixture_set_ref": "fixture-set:outcome-association@1",
        "annotation_set_ref": "annotation-set:outcome-association@1",
    }
    digest = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {**value, "rule_sha256": digest}


def _registry() -> dict:
    return {
        "schema_version": "outcome_association_rule_registry.v1",
        "registry_version": "fixture.v1",
        "entries": [{"status": "active", "binding": _binding()}],
    }


def _kwargs() -> dict:
    return {
        "analysis_ref": "analysis:7",
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 1_000,
            "end_ms": 3_000,
            "duration_ms": 2_000,
            "window_semantics": "half_open",
            "timebase_version": "test.v1",
            "start_source": "fixture",
            "end_source": "fixture",
            "warnings": [],
        },
        "scenario_profile_ref": "scenario-profile:fixture@1",
        "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
        "raw_input_source_ref": "run:3:trace:def",
        "stats_source_ref": "run:3:stats:abc",
        "stats_parser_version": "kovaak_stats.v1",
        "visual_source_ref": "run:3:video:abc",
        "click_events": [
            {"event_ref": "analysis:7:event:raw-shot:1", "time_ms": 2_000},
        ],
        "stats_kills": [
            {
                "event_ref": "analysis:7:event:stats-kill:1",
                "time_ms": 2_010,
                "kill_index": 1,
                "shots": 1,
                "hits": 1,
                "overshots": 0,
            },
        ],
        "viewport_size": [200, 200],
        "target_tracks": [
            {
                "track_ref": "analysis:7:target-track:1",
                "identity_status": "stable",
                "samples": [
                    {
                        "canonical_time_ms": 1_995, "x": 102.0, "y": 100.0,
                        "radius": 10.0, "confidence": 1.0,
                    },
                ],
            }
        ],
        "rule_registry": _registry(),
    }


def test_rule_registry_is_exact_and_production_shape_can_remain_empty():
    empty = {
        "schema_version": "outcome_association_rule_registry.v1",
        "registry_version": "2026-07-22.v1",
        "entries": [],
    }
    assert validate_outcome_association_rule_registry_v1(empty) == empty

    invalid = _registry()
    invalid["entries"][0]["binding"]["rule_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        validate_outcome_association_rule_registry_v1(invalid)


def test_unique_raw_stats_visual_match_produces_validated_kill_bundle():
    result = associate_one_shot_kills_v1(**_kwargs())

    assert result["status"] == "available"
    assert result["limitations"] == []
    bundle = result["event_bundle"]
    assert [event["event_kind"] for event in bundle["events"]] == ["shot", "kill"]
    assert bundle["events"][1]["actor_refs"] == []
    association = bundle["outcome_associations"][0]
    assert association["shot_event_ref"] == "analysis:7:event:raw-shot:1"
    assert association["association_kind"] == "validated_aligned"
    assert association["validation"]["stats_kill"]["hits"] == 1
    assert association["validation"]["track_check"] == {
        "identity_status": "stable",
        "sample_gap_ms": 5,
        "sample_confidence": 1.0,
        "center_distance_px": 2.0,
        "effective_radius_px": 9.0,
    }


@pytest.mark.parametrize(
    ("mutate", "limitation"),
    [
        (
            lambda value: value["click_events"].append(
                {"event_ref": "analysis:7:event:raw-shot:2", "time_ms": 2_005}
            ),
            "temporal_candidate_not_unique",
        ),
        (
            lambda value: value["target_tracks"].append({
                "track_ref": "analysis:7:target-track:2",
                "identity_status": "stable",
                "samples": [
                    {
                        "canonical_time_ms": 2_000, "x": 99.0, "y": 100.0,
                        "radius": 10.0, "confidence": 1.0,
                    },
                ],
            }),
            "geometric_candidate_not_unique",
        ),
        (
            lambda value: value["stats_kills"][0].update({"shots": 2}),
            "one_shot_kill_unavailable",
        ),
        (
            lambda value: value["target_tracks"][0].update({"identity_status": "crossing"}),
            "stable_target_identity_unavailable",
        ),
        (
            lambda value: value["target_tracks"][0]["samples"][0].update({"confidence": 0.9}),
            "geometric_candidate_not_unique",
        ),
        (
            lambda value: value["target_tracks"][0]["samples"][0].update({"x": 120.0}),
            "geometric_candidate_not_unique",
        ),
    ],
)
def test_association_producer_fails_closed_without_unique_validated_evidence(
    mutate, limitation,
):
    kwargs = _kwargs()
    mutate(kwargs)

    result = associate_one_shot_kills_v1(**kwargs)

    assert result["status"] == "unavailable"
    assert result["event_bundle"] is None
    assert limitation in result["limitations"]


def test_missing_active_exact_rule_does_not_emit_association():
    kwargs = _kwargs()
    registry = deepcopy(kwargs["rule_registry"])
    registry["entries"][0]["status"] = "retired"
    kwargs["rule_registry"] = registry

    result = associate_one_shot_kills_v1(**kwargs)

    assert result == {
        "schema_version": "outcome_association_result.v1",
        "status": "unavailable",
        "event_bundle": None,
        "limitations": ["outcome_association_rule_unavailable"],
    }


def test_available_bundle_preserves_unmatched_raw_click_as_unavailable():
    kwargs = _kwargs()
    kwargs["click_events"].append({
        "event_ref": "analysis:7:event:raw-shot:2",
        "time_ms": 2_200,
    })

    result = associate_one_shot_kills_v1(**kwargs)

    assert result["status"] == "available"
    assert [event["event_kind"] for event in result["event_bundle"]["events"]] == [
        "shot", "kill", "shot",
    ]
    unavailable = [
        association
        for association in result["event_bundle"]["outcome_associations"]
        if association["availability"] == "unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0]["shot_event_ref"].endswith("raw-shot:2")


def test_real_reviewed_association_replay_uses_only_stable_inner_hit_evidence():
    """Exercise the association producer against the user-reviewed field ledger.

    This is intentionally opt-in and never loads the production rule registry.
    The review ledger is a local, human-reviewed input; it is not a production
    asset and must not be copied into the repository.
    """
    raw_ledger_path = os.environ.get("AIMING_COOKIE_DYNAMIC_ASSOCIATION_LEDGER")
    raw_field_dir = os.environ.get("AIMING_COOKIE_DYNAMIC_FIELD_DIR")
    if not raw_ledger_path or not raw_field_dir:
        pytest.skip(
            "set AIMING_COOKIE_DYNAMIC_ASSOCIATION_LEDGER and "
            "AIMING_COOKIE_DYNAMIC_FIELD_DIR for the local association replay"
        )

    ledger_path = Path(raw_ledger_path)
    field_dir = Path(raw_field_dir)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert ledger["schema_version"] == "dynamic_association_review.v1"
    assert ledger["scenario_hash"] == "a5be19c6e6aeb0d774c5e9d9fb497e91"
    assert ledger["canonical_window"] == {
        "start_ms": 1_784_438_416_265,
        "end_ms": 1_784_438_501_959,
        "duration_ms": 85_694,
    }
    assert ledger["detector_config_ref"] == (
        "detector-config:sha256:5a0a5e2fcc4b324eed0c8b2a230e1cf3da141249752678ad162c20fb9ab7caca"
    )
    expected_source_hashes = {
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
    assert ledger["source_hashes"] == expected_source_hashes
    for name, expected_hash in expected_source_hashes.items():
        assert hashlib.sha256((field_dir / name).read_bytes()).hexdigest() == expected_hash

    candidates = ledger["candidates"]
    assert len(candidates) == 44
    assert {
        row["identity_review_status"] for row in candidates
    } == {"stable", "not_stable"}
    assert sum(row["identity_review_status"] == "stable" for row in candidates) == 41
    assert sum(row["identity_review_status"] == "not_stable" for row in candidates) == 3

    from kovaak_tracker.csv_parser import parse_stats_bytes
    from webapp.backend.kovaak_run_store import decode_mouse_snapshot_bytes
    from webapp.backend.worker import _raw_left_button_rising_edges

    start_ms = ledger["canonical_window"]["start_ms"]
    end_ms = ledger["canonical_window"]["end_ms"]
    analysis_ref = "analysis:task6a-reviewed-field"
    clicks = _raw_left_button_rising_edges(
        decode_mouse_snapshot_bytes(
            (field_dir / "canonical-challenge.acri-v1.bin").read_bytes()
        ),
        analysis_ref=analysis_ref,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    assert len(clicks) == 127
    parsed_stats = parse_stats_bytes(
        (field_dir / "source-stats.csv").read_bytes(),
        file_name="source-stats.csv",
    )
    stats_kills = []
    for row_index, (_, row) in enumerate(parsed_stats.kills.iterrows(), 1):
        kill = {
            "event_ref": f"{analysis_ref}:event:stats-kill:{row_index}",
            "time_ms": start_ms + int(round(float(row["time_s"]) * 1000)),
            "kill_index": int(row["Kill #"]),
            "shots": int(row["Shots"]),
            "hits": int(row["Hits"]),
            "overshots": int(row["OverShots"]),
        }
        if start_ms <= kill["time_ms"] < end_ms:
            stats_kills.append(kill)

    clicks_by_time = {row["time_ms"]: row for row in clicks}
    kills_by_index = {row["kill_index"]: row for row in stats_kills}
    for candidate in candidates:
        assert candidate["click_time_ms"] in clicks_by_time
        kill = kills_by_index[candidate["kill_index"]]
        assert kill["time_ms"] == candidate["kill_time_ms"]
        assert kill["time_ms"] - candidate["click_time_ms"] == candidate[
            "click_to_kill_ms"
        ]
        assert (kill["shots"], kill["hits"], kill["overshots"]) == (1, 1, 0)
        assert candidate["automatic_overlap"]["count"] == 1
        assert candidate["automatic_overlap"]["target"] in candidate["frames"][1][
            "detections"
        ]

    ledger_digest = hashlib.sha256(
        json.dumps(ledger, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    binding = _binding()
    binding.update({
        "rule_ref": "outcome-association-rule:1wall5targets-pasu@candidate",
        "scenario_profile_ref": "scenario:1wall5targets_pasu@candidate",
        "canonical_timebase_version": "time_alignment.v2",
        "visual_quality_profile_ref": (
            "visual-quality:visual_signals.round_detector@"
            "visual_round_detector.circularity_0_60_center_overlay_0_50.v2"
        ),
        "annotation_set_ref": f"annotation-set:task6a-review@{ledger_digest}",
        "fixture_set_ref": "fixture-set:task6a-field@2026-07-22",
    })
    binding.pop("rule_sha256")
    binding["rule_sha256"] = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    registry = {
        "schema_version": "outcome_association_rule_registry.v1",
        "registry_version": "task6a-field-candidate.v1",
        "entries": [{"status": "active", "binding": binding}],
    }

    target_tracks = []
    expected_inner_matches = set()
    for candidate in candidates:
        target = candidate["automatic_overlap"]["target"]
        center_distance = float(candidate["automatic_overlap"]["center_distance_px"])
        radius = float(target["visible_radius"])
        if candidate["identity_review_status"] == "stable" and center_distance <= radius - 1.0:
            expected_inner_matches.add(candidate["kill_index"])
        target_tracks.append({
            "track_ref": f"{analysis_ref}:target-track:{candidate['candidate_id']}",
            "identity_status": candidate["identity_review_status"],
            "samples": [{
                "canonical_time_ms": candidate["click_time_ms"],
                "x": target["x"],
                "y": target["y"],
                "radius": radius,
                # The reviewer selected this exact detector candidate.  The
                # producer still applies the conservative inner-hitbox test.
                "confidence": 1.0,
            }],
        })

    result = associate_one_shot_kills_v1(
        analysis_ref=analysis_ref,
        canonical_time_window={
            "start_ms": start_ms,
            "end_ms": end_ms,
            "timebase_version": "time_alignment.v2",
        },
        scenario_profile_ref="scenario:1wall5targets_pasu@candidate",
        visual_quality_profile_ref=(
            "visual-quality:visual_signals.round_detector@"
            "visual_round_detector.circularity_0_60_center_overlay_0_50.v2"
        ),
        raw_input_source_ref="field:task4-timescale:raw",
        stats_source_ref="field:task4-timescale:stats",
        stats_parser_version="kovaak_stats.v1",
        visual_source_ref="field:task4-timescale:video",
        click_events=clicks,
        stats_kills=stats_kills,
        viewport_size=[1920, 1080],
        target_tracks=target_tracks,
        rule_registry=registry,
    )

    assert result["status"] == "available"
    associations = result["event_bundle"]["outcome_associations"]
    validated = [
        row for row in associations if row["availability"] == "available"
    ]
    assert len(validated) == len(expected_inner_matches) == 36
    assert {
        row["validation"]["stats_kill"]["kill_index"] for row in validated
    } == expected_inner_matches
    assert all(
        row["association_kind"] == "validated_aligned"
        and row["validation"]["track_check"]["identity_status"] == "stable"
        for row in validated
    )
    assert len([row for row in associations if row["availability"] == "unavailable"]) == len(clicks) - 36
    unavailable_shots = {
        row["shot_event_ref"]
        for row in associations
        if row["availability"] == "unavailable"
    }
    assert all(
        clicks_by_time[row["click_time_ms"]]["event_ref"] in unavailable_shots
        for row in candidates
        if row["identity_review_status"] == "not_stable"
    )
    assert "geometric_candidate_not_unique" in result["limitations"]
    assert "one_shot_kill_unavailable" in result["limitations"]

    # The production registry remains empty; this candidate registry exists
    # only inside the opt-in test above.
    for path in (
        "knowledge/scenarios/outcome-association-rules.v1.json",
        "knowledge/scenarios/registry.v1.json",
        "knowledge/scenarios/launch-manifest.v1.json",
    ):
        assert json.loads(Path(path).read_text(encoding="utf-8"))["entries"] == []
