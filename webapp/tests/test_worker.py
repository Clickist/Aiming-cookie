from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from kovaak_tracker import scenario_profiles
from webapp.backend import config, db, evidence_store, queue, worker
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    build_analysis_result_v1,
    validate_analysis_result_v2_for_persistence,
)


@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False


def test_worker_attaches_committed_evidence_before_terminal_result(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    analysis_ref = "analysis:77"
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }
    job = {
        "id": 77,
        "user_id": "owner:1",
        "input_snapshot": {
            "canonical_time_window": window,
            "sources": {
                "stats": {
                    "artifact_ref": "run:1:stats",
                    "parser_version": "kovaak_stats.v1",
                },
                "performance": {
                    "artifact_ref": "run:1:performance",
                    "parser_version": "kovaak_performance.v1",
                },
            },
        },
    }
    result = {
        "evidence": {},
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [
                {"id": "run:1:stats"},
                {"id": "run:1:performance"},
            ],
            "owned_outputs": [{"id": analysis_ref}],
        },
    }
    with patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        return_value=b"fixture",
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
        return_value=artifact,
    ):
        updated = worker._maybe_commit_analysis_evidence(job, result)

    safe_ref = updated["evidence"]["derived_artifact"]
    assert evidence_store._artifact_file(
        77, safe_ref["evidence_revision"],
    ).is_file()
    evidence_entry = updated["artifact_manifest"]["owned_outputs"][-1]
    assert evidence_entry["kind"] == "analysis_evidence"
    assert evidence_entry["id"] == safe_ref["artifact_ref"]
    assert evidence_entry["derived_from"] == [
        "run:1:stats", "run:1:performance",
    ]


def test_worker_commits_validated_visual_signals_into_local_evidence(
    monkeypatch, tmp_path,
):
    from kovaak_tracker.visual_signals import (
        build_visual_quality_profile_v2,
        preprocess_visual_signals_v1,
    )

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    analysis_ref = "analysis:79"
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "fixture-hash",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }
    profile = build_visual_quality_profile_v2(
        producer_id="fixture_detector",
        producer_version="fixture_detector.v1",
        annotation_set_ref="annotation-set:fixture.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": "detector-config:fixture.v1",
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["fixture"],
            "annotated_target_appearance_labels": ["sphere"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "tracking": [
                "center_error_median_px", "center_error_p95_px",
                "radius_or_hitbox_error_px", "false_positive_rate",
                "identity_switch_rate", "occlusion_reentry_accuracy",
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
            "minimum_coverage": 0.9,
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
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[],
    )
    visual = preprocess_visual_signals_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=window,
        frame_observations=[{
            "source_pts_ms": 0,
            "crosshair": {"x": 100.0, "y": 100.0},
            "targets": [{
                "detector_ref": "target-1",
                "x": 110.0,
                "y": 100.0,
                "visible_radius": 12.0,
                "confidence": 1.0,
            }],
            "scene": "gameplay",
        }],
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )
    generic_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }
    job = {
        "id": 79,
        "user_id": "owner:visual",
        "input_snapshot": {
            "canonical_time_window": window,
            "sources": {
                "stats": {"artifact_ref": "run:79:stats", "parser_version": "kovaak_stats.v1"},
                "performance": {"artifact_ref": "run:79:performance", "parser_version": "kovaak_performance.v1"},
            },
        },
    }
    result = {
        "evidence": {},
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [{"id": "run:79:stats"}, {"id": "run:79:performance"}],
            "owned_outputs": [{"id": analysis_ref}],
        },
    }
    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"fixture"), \
         patch("kovaak_tracker.csv_parser.parse_stats_bytes", return_value=object()), \
         patch("kovaak_tracker.performance_parser.parse_performance_bytes", return_value=object()), \
         patch(
             "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
             return_value=generic_artifact,
         ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result=visual,
        )

    committed = evidence_store.validate_committed_analysis_evidence(
        session_id=79,
        owner_id="owner:visual",
        safe_ref=updated["evidence"]["derived_artifact"],
    )
    assert committed["signal_bundles"] == [visual["signal_bundle"]]
    assert committed["event_bundles"] == [visual["event_bundle"]]
    assert "local_samples" not in committed


def test_worker_downgrades_visual_summary_when_visual_artifact_commit_fails(
    monkeypatch, tmp_path,
):
    from kovaak_tracker.visual_signals import (
        build_visual_quality_profile_v2,
        preprocess_visual_signals_v1,
    )

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    analysis_ref = "analysis:80"
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "fixture-hash",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }
    profile = build_visual_quality_profile_v2(
        producer_id="fixture_detector",
        producer_version="fixture_detector.v1",
        annotation_set_ref="annotation-set:fixture.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": "detector-config:fixture.v1",
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["fixture"],
            "annotated_target_appearance_labels": ["sphere"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "tracking": [
                "center_error_median_px", "center_error_p95_px",
                "radius_or_hitbox_error_px", "false_positive_rate",
                "identity_switch_rate", "occlusion_reentry_accuracy",
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
            "minimum_coverage": 0.9,
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
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[],
    )
    visual = preprocess_visual_signals_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=window,
        frame_observations=[{
            "source_pts_ms": 0,
            "crosshair": {"x": 100.0, "y": 100.0},
            "targets": [{
                "detector_ref": "target-1",
                "x": 110.0,
                "y": 100.0,
                "visible_radius": 12.0,
                "confidence": 1.0,
            }],
            "scene": "gameplay",
        }],
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )
    generic_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }
    job = {
        "id": 80,
        "user_id": "owner:visual",
        "input_snapshot": {
            "canonical_time_window": window,
            "sources": {
                "stats": {"artifact_ref": "run:80:stats", "parser_version": "kovaak_stats.v1"},
                "performance": {"artifact_ref": "run:80:performance", "parser_version": "kovaak_performance.v1"},
            },
        },
    }
    result = {
        "deterministic": {"visual_validation": visual["safe_summary"]},
        "warnings": [],
        "evidence": {},
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [{"id": "run:80:stats"}, {"id": "run:80:performance"}],
            "owned_outputs": [{"id": analysis_ref}],
        },
    }
    committed_artifacts: list[dict] = []
    real_write = evidence_store.write_analysis_evidence_artifact

    def write_artifact(*, session_id: int, owner_id: str, artifact: dict) -> dict:
        committed_artifacts.append(artifact)
        if len(committed_artifacts) == 1:
            raise OSError("fixture write failure")
        return real_write(session_id=session_id, owner_id=owner_id, artifact=artifact)

    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"fixture"), \
         patch("kovaak_tracker.csv_parser.parse_stats_bytes", return_value=object()), \
         patch("kovaak_tracker.performance_parser.parse_performance_bytes", return_value=object()), \
         patch(
             "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
             return_value=generic_artifact,
         ), \
         patch(
             "webapp.backend.evidence_store.write_analysis_evidence_artifact",
             side_effect=write_artifact,
         ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result=visual,
        )

    assert len(committed_artifacts) == 2
    assert committed_artifacts[0]["signal_bundles"] == [visual["signal_bundle"]]
    assert committed_artifacts[1]["signal_bundles"] == []
    committed = evidence_store.validate_committed_analysis_evidence(
        session_id=80,
        owner_id="owner:visual",
        safe_ref=updated["evidence"]["derived_artifact"],
    )
    assert committed["signal_bundles"] == []
    assert committed["limitations"] == ["visual_artifact_commit_failed"]
    assert updated["deterministic"]["visual_validation"] == {
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
        "limitations": ["visual_artifact_commit_failed"],
    }
    assert updated["warnings"] == [{"code": "visual_artifact_commit_failed"}]


def test_worker_downgrades_visual_summary_when_base_artifact_build_fails():
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    job = {
        "id": 81,
        "user_id": "owner:visual",
        "input_snapshot": {
            "canonical_time_window": window,
            "sources": {
                "stats": {
                    "artifact_ref": "run:81:stats",
                    "parser_version": "kovaak_stats.v1",
                },
                "performance": {
                    "artifact_ref": "run:81:performance",
                    "parser_version": "kovaak_performance.v1",
                },
            },
        },
    }
    result = {
        "deterministic": {
            "visual_validation": {
                "schema_version": "visual_signal_summary.v1",
                "status": "available",
                "quality_status": "accepted",
            },
        },
        "warnings": [],
        "evidence": {},
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [],
            "owned_outputs": [],
        },
    }

    with patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        return_value=b"fixture",
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
        side_effect=ValueError("fixture base artifact failure"),
    ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result={"safe_summary": {"status": "available"}},
        )

    assert updated["deterministic"]["visual_validation"] == (
        worker._unavailable_visual_summary("visual_artifact_commit_failed")
    )
    assert updated["deterministic"]["limitations"] == [
        "visual_artifact_commit_failed"
    ]
    assert "derived_artifact" not in updated["evidence"]


def test_static_native_artifact_projects_stable_flick_refs_and_ranked_segments(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    analysis_ref = "analysis:78"
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    generic_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }
    job = {
        "id": 78,
        "user_id": "owner:static",
        "input_snapshot": {
            "canonical_time_window": window,
            "scenario_resolution": {
                "scenario_profile_ref": "scenario:static.fixture@1",
                "aim_family": "static_clicking",
            },
            "trace": {"artifact_ref": "run:78:trace"},
            "sources": {
                "stats": {
                    "artifact_ref": "run:78:stats",
                    "parser_version": "kovaak_stats.v1",
                },
                "performance": {
                    "artifact_ref": "run:78:performance",
                    "parser_version": "kovaak_performance.v1",
                },
            },
        },
    }
    result = {
        "analysis_version": "native_flicking.v1",
        "evidence": {},
        "deterministic": {
            "diagnosis": {
                "issues": [
                    {
                        "signal": "peak speed variation",
                        "event_refs": ["flick:2"],
                        "metric_refs": ["peak_speed"],
                    }
                ]
            },
            "metrics": {
                "peak_speed": {
                    "key": "peak_speed",
                    "value": 300.0,
                    "unit": "raw_counts_per_second",
                    "availability": "available",
                    "classification": "deterministic",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking.v1",
                    "sample_count": 3,
                    "coverage": 1.0,
                    "limitations": ["target_relative_facts_unavailable"],
                    "sample_refs": ["flick:1", "flick:2", "flick:3"],
                },
            },
            "timeline": [
                {
                    "id": "flick:1",
                    "event_type": "flick",
                    "start_ms": 100.0,
                    "peak_ms": 120.0,
                    "end_ms": 160.0,
                    "settle_end_ms": 180.0,
                    "metrics": {"peak_speed": 100.0, "corrective_count": 3},
                    "limitations": ["target_relative_facts_unavailable"],
                },
                {
                    "id": "flick:2",
                    "event_type": "flick",
                    "start_ms": 300.0,
                    "peak_ms": 320.0,
                    "end_ms": 360.0,
                    "settle_end_ms": 380.0,
                    "metrics": {"peak_speed": 500.0, "corrective_count": 4},
                    "limitations": ["target_relative_facts_unavailable"],
                },
                {
                    "id": "flick:3",
                    "event_type": "flick",
                    "start_ms": 500.0,
                    "peak_ms": 520.0,
                    "end_ms": 560.0,
                    "settle_end_ms": 580.0,
                    "metrics": {"peak_speed": 300.0, "corrective_count": 1},
                    "limitations": ["target_relative_facts_unavailable"],
                },
            ],
        },
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [
                {"id": "run:78:stats"},
                {"id": "run:78:performance"},
            ],
            "owned_outputs": [{"id": analysis_ref}],
        },
    }
    with patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        return_value=b"fixture",
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ), patch(
        "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
        return_value=generic_artifact,
    ):
        updated = worker._maybe_commit_analysis_evidence(job, result)

    artifact = evidence_store.validate_committed_analysis_evidence(
        session_id=78,
        owner_id="owner:static",
        safe_ref=updated["evidence"]["derived_artifact"],
    )
    events = [
        event
        for bundle in artifact["event_bundles"]
        for event in bundle["events"]
    ]
    assert [event["event_id"] for event in events] == [
        "analysis:78:event:static-flick:1",
        "analysis:78:event:static-flick:2",
        "analysis:78:event:static-flick:3",
    ]
    assert {segment["rank_reason"] for segment in artifact["evidence_segments"]} == {
        "typical", "worst", "improved",
    }
    assert all(
        len(segment["event_refs"]) == 1
        and segment["event_refs"][0] in {event["event_id"] for event in events}
        for segment in artifact["evidence_segments"]
    )
    peak_speed = next(
        metric
        for metric in artifact["metric_records"]
        if metric["metric_key"] == "static_clicking.peak_speed"
    )
    assert peak_speed["event_refs"] == [
        "analysis:78:event:static-flick:1",
        "analysis:78:event:static-flick:2",
        "analysis:78:event:static-flick:3",
    ]
    tables = updated["evidence"]["processed_event_tables"]
    assert len(tables) == 1
    assert tables[0]["table_ref"] == "analysis:78:table:static_flick"
    assert tables[0]["row_count"] == 3
    assert tables[0]["completeness"] == "complete"
    assert "rows" not in tables[0] and "attributes" not in tables[0]
    issue = updated["deterministic"]["diagnosis"]["issues"][0]
    assert issue["event_refs"] == ["analysis:78:event:static-flick:2"]
    assert issue["primary_evidence_segment_ref"] == "analysis:78:segment:worst:2"
    assert len(issue["supporting_evidence_segment_refs"]) <= 2
    rendered = json.dumps(artifact, ensure_ascii=False)
    assert "overshoot" not in rendered
    assert "undershoot" not in rendered
    assert "target_relative_error" not in rendered


@pytest.mark.asyncio
async def test_process_one_happy_path():
    """claim → local analysis/report → mark_done, without Analysis narration."""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_summary = {"sparc": {"med": -7.5}}
    fake_extras = {
        "fps": 60, "duration_frames": 100,
        "flicks": [{"start_frame": 10, "peak_frame": 15, "end_frame": 20,
                    "peak_speed_px": 800.0, "duration_s": 0.18}],
        "kill_frames": [18], "corrective_frames": [],
    }
    fake_report = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=(fake_summary, fake_extras)), \
         patch("webapp.backend.worker.run_report", return_value=fake_report) as mock_report, \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["narration"]["status"] == "not_requested"
    assert result["narration"]["text"] is None
    assert len(mock_report.call_args.args) == 1
    report_summary = mock_report.call_args.args[0]
    assert report_summary["sparc"]["metric_version"] == (
        "flicking_fair_summary.sparc.v2"
    )
    assert float(s["llm_cost_cny"]) == 0.0
    types = sorted(e["type"] for e in result["deterministic"]["timeline"])
    assert types == ["kill", "peak"]


@pytest.mark.asyncio
async def test_video_fallback_does_not_read_or_load_selected_provider():
    sid = await queue.enqueue("owner-selected", "/tmp/v.mp4", "/tmp/s.csv")
    profile = {
        "profile_id": 7,
        "provider_id": "custom-selected",
        "provider_name": "Selected Provider",
        "kind": "custom_openai_compatible",
        "base_url": "https://provider.test/v1",
        "model_id": "selected-model",
        "credential": {"type": "api_key", "key": "local-only-key"},
    }
    fake_report = {"diagnosis": {}, "narration": None, "notes": []}

    with patch(
        "webapp.backend.provider_store.get_default_runtime_profile",
        new=AsyncMock(return_value=profile),
    ) as get_profile, patch(
        "webapp.backend.provider_store.runtime_profile_configured",
        return_value=True,
    ), patch(
        "webapp.backend.worker.run_analysis",
        return_value=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    ), patch(
        "webapp.backend.worker.run_report",
        return_value=fake_report,
    ) as run_report:
        assert await worker.process_one() is True

    get_profile.assert_not_awaited()
    assert len(run_report.call_args.args) == 1
    session = await queue.get_session(sid)
    assert session["result"]["narration"]["status"] == "not_requested"
    assert session["result"]["narration"]["text"] is None
    assert session["llm_cost_cny"] == 0.0


@pytest.mark.asyncio
async def test_process_one_happy_path_writes_analysis_result_v2():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv", cm_per_360=30.0, fov=90.0)
    fake_extras = {
        "fps": 60,
        "flicks": [{"peak_frame": 15}],
        "kill_frames": [18],
        "corrective_frames": [],
    }
    fake_report = {
        "diagnosis": {"x": 1},
        "figures": {},
        "narration": None,
        "notes": [],
    }

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, fake_extras)), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._delete_video_safely"):
        await worker.process_one()

    s = await queue.get_session(sid)
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_type"] == "flicking"
    assert result["input_mode"] == "video_fallback"
    assert result["input_snapshot"]["calibration"] == {
        "cm_per_360": {"value": 30.0, "source": "manual_override"},
        "fov": {"value": 90.0, "source": "manual_override"},
    }
    assert result["narration"] == {
        "status": "not_requested",
        "text": None,
        "provider": None,
        "model": None,
        "usage": None,
    }
    assert "timeline" not in result
    assert len(result["deterministic"]["timeline"]) == 2
    assert result["created_at"].endswith("Z")
    assert result["completed_at"].endswith("Z")


def test_video_fallback_explicit_manual_override_wins_over_legacy_flat_values():
    stats = MagicMock(cm_per_360=None, fov=None)
    with patch(
        "kovaak_tracker.pan_tracker.analyze_flicking_fair_summary",
        return_value=({}, {}),
    ) as analyze:
        _summary, extras = worker.run_analysis(
            "video.mp4",
            "stats.csv",
            cm_per_360=30.0,
            fov=90.0,
            stats=stats,
            manual_override={"cm_per_360": 40.0, "fov": 100.0},
        )

    assert analyze.call_args.kwargs["cm_per_360"] == 40.0
    assert analyze.call_args.kwargs["fov"] == 100.0
    assert extras["calibration"] == {
        "cm_per_360": {"value": 40.0, "source": "manual_override"},
        "fov": {"value": 100.0, "source": "manual_override"},
    }


@pytest.mark.asyncio
async def test_process_one_without_selected_provider_keeps_v2_narration_null():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report_no_llm), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert float(s["llm_cost_cny"]) == 0.0
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["narration"]["status"] == "not_requested"
    assert result["narration"]["text"] is None
    assert result["deterministic"]["timeline"] == []


@pytest.mark.asyncio
async def test_process_one_calls_local_report_without_backend():
    """The deterministic report has no Provider/backend argument."""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [], "corrective_frames": []})), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report_no_llm) as mock_report, \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert float(s["llm_cost_cny"]) == 0.0
    assert s["result"]["deterministic"]["timeline"] == []
    assert s["result"]["narration"]["status"] == "not_requested"
    assert mock_report.call_args.args
    assert len(mock_report.call_args.args) == 1
    assert mock_report.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_process_one_normalizes_non_finite_values_before_persisting():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    nan = float("nan")
    fake_report = {
        "diagnosis": {"summary": {"sparc": {"med": nan}}},
        "figures": {},
        "narration": None,
        "notes": [],
    }

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._delete_video_safely"):
        await worker.process_one()

    from webapp.backend import db
    conn = await db.get_conn()
    cur = await conn.execute("SELECT result FROM sessions WHERE id=?", (sid,))
    row = await cur.fetchone()
    raw_json = row["result"]
    assert "NaN" not in raw_json

    s = await queue.get_session(sid)
    med = s["result"]["deterministic"]["diagnosis"]["summary"]["sparc"]["med"]
    assert med is None
    issues = s["result"]["normalization_issues"]
    assert any(i.get("code") == "non_finite_number" for i in issues)


@pytest.mark.asyncio
async def test_process_one_analysis_failure_marks_failed():
    """分析崩(目标检测失败等)→ job failed,记录 error。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    with patch("webapp.backend.worker.run_analysis",
               side_effect=RuntimeError("CSRT 丢失目标")), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["code"] == "analysis_failed"
    assert "CSRT" not in s["error"]["message"]


@pytest.mark.asyncio
async def test_native_source_disappearance_uses_stable_non_retryable_error(caplog):
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="owner-source", source_key="source-error", scenario="Scenario",
    )
    sid = await queue.enqueue(
        "owner-source", "", "",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run["id"],
            "sources": {},
            "trace": None,
        },
    )

    with patch(
        "webapp.backend.worker.run_native_analysis",
        side_effect=worker.SourceSnapshotChangedError(
            "source_unavailable: stats /private/user/source.csv missing or unreadable"
        ),
    ):
        assert await worker.process_one() is True

    session = await queue.get_session(sid)
    assert session["status"] == "failed"
    assert session["error"] == {
        "schema_version": "error.v1",
        "category": "input_validation",
        "code": "source_unavailable",
        "message": "分析输入源已不可用或已变更，请重新提交分析。",
        "retryable": False,
        "trace_id": None,
        "details": None,
    }
    assert "/private/user/source.csv" not in json.dumps(
        session["error"], ensure_ascii=False
    )
    assert "/private/user/source.csv" not in caplog.text


@pytest.mark.asyncio
async def test_run_based_video_fallback_writes_v2_without_raw_provenance(
    tmp_path: Path,
):
    from webapp.backend import kovaak_run_store

    source_video = tmp_path / "clip.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    video_source = _video_source(source_video)
    video_fingerprint = video_source["fingerprint"]
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="fallback-run", scenario="Scenario",
    )
    snapshot = {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run["id"],
        "scenario": "Scenario",
        "sources": {
            "stats": {
                "artifact_ref": f"run:{run['id']}:stats:abc",
                "basename": "stats.csv",
                "availability": "available",
                "path": "/private/stats.csv",
            },
            "video": video_source,
        },
        "trace": {
            "artifact_ref": f"run:{run['id']}:trace",
            "availability": "available",
            "path": "/private/trace.bin",
        },
    }
    sid = await queue.enqueue(
        "u1", str(managed_video), "/tmp/s.csv",
        input_mode="video_fallback",
        kovaak_run_id=run["id"],
        input_snapshot=snapshot,
    )
    fake_report = {
        "diagnosis": {"summary": {"ok": True}},
        "figures": {},
        "narration": None,
        "notes": [],
    }

    with patch(
        "webapp.backend.worker.run_analysis",
        return_value=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    ), patch("webapp.backend.worker.run_report", return_value=fake_report):
        await worker.process_one()

    stored = await queue.get_session(sid)
    assert stored["result"]["schema_version"] == "analysis_result.v2"
    assert stored["result"]["input_mode"] == "video_fallback"
    assert "raw_input" not in stored["result"]["evidence"]["sources"]
    assert stored["result"]["input_snapshot"]["trace"] is None
    assert stored["result"]["input_snapshot"]["sources"]["video"]["fingerprint"] == video_fingerprint
    video_artifact = next(
        artifact
        for artifact in stored["result"]["artifact_manifest"]["external_inputs"]
        if artifact["kind"] == "mp4"
    )
    assert video_artifact["checksum"] == video_fingerprint["sha256"]
    assert "/private" not in json.dumps(stored["result"])
    assert str(source_video) not in json.dumps(stored["result"])


@pytest.mark.asyncio
async def test_process_one_never_loads_narration_backend():
    """Analysis keeps the deterministic report and never touches Provider setup."""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.provider_store.get_default_runtime_profile",
               new=AsyncMock(side_effect=RuntimeError("must not read"))) as get_profile, \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report) as mock_report, \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["narration"]["status"] == "not_requested"
    assert s["result"]["narration"]["text"] is None
    assert len(mock_report.call_args.args) == 1
    get_profile.assert_not_awaited()


def test_build_timeline_combines_peaks_correctives_kills():
    """flick peak / corrective / kill 都进 timeline,按 frame 升序。"""
    extras = {
        "fps": 60,
        "duration_frames": 1000,
        "flicks": [
            {"start_frame": 10, "peak_frame": 15, "end_frame": 25,
             "peak_speed_px": 800.0, "duration_s": 0.25},
            {"start_frame": 60, "peak_frame": 65, "end_frame": 75,
             "peak_speed_px": 700.0, "duration_s": 0.25},
        ],
        "corrective_frames": [70],
        "kill_frames": [20, 80],
    }
    events = worker._build_timeline(extras)
    types_by_frame = {e["frame"]: e["type"] for e in events}
    assert types_by_frame[15] == "peak"
    assert types_by_frame[65] == "peak"
    assert types_by_frame[70] == "corrective"
    assert types_by_frame[20] == "kill"
    assert types_by_frame[80] == "kill"
    frames = [e["frame"] for e in events]
    assert frames == sorted(frames)
    assert events[0]["time_s"] == round(15 / 60, 3)


def test_build_timeline_handles_empty_and_garbage():
    """extras 空 / 非 dict → 返回 [];fps 缺失走默认 60。"""
    assert worker._build_timeline({}) == []
    assert worker._build_timeline(None) == []
    events = worker._build_timeline({"flicks": [{"peak_frame": 60}]})
    assert events == [{"frame": 60, "time_s": 1.0, "type": "peak", "label": "速度峰值"}]


@pytest.mark.asyncio
async def test_process_one_runs_recover_before_claim():
    order: list[str] = []

    async def fake_recover(*_a, **_k):
        order.append("recover")
        return {"requeued": 0, "failed": 0}

    async def fake_claim(*_a, **_k):
        order.append("claim")
        return None

    with patch("webapp.backend.queue.recover_stale_jobs", side_effect=fake_recover), \
         patch("webapp.backend.queue.claim_next", side_effect=fake_claim):
        assert await worker.process_one() is False
    assert order == ["recover", "claim"]


@pytest.mark.asyncio
async def test_process_one_heartbeats_during_analysis():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    beats: list[int] = []

    def slow_analysis(*_a, **_k):
        # Block the worker thread long enough for heartbeat loop ticks.
        time.sleep(0.2)
        return (
            {"a": {"med": 1}},
            {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
        )

    real_heartbeat = queue.heartbeat

    async def counting_hb(session_id, worker_id):
        beats.append(session_id)
        return await real_heartbeat(session_id, worker_id)

    with patch("webapp.backend.worker.HEARTBEAT_INTERVAL_SECONDS", 0.05), \
         patch("webapp.backend.worker.run_analysis", side_effect=slow_analysis), \
         patch("webapp.backend.worker.run_report",
               return_value={"diagnosis": {}, "narration": None, "notes": []}), \
         patch("webapp.backend.queue.heartbeat", side_effect=counting_hb):
        await worker.process_one()

    assert sid in beats
    assert len(beats) >= 2
    s = await queue.get_session(sid)
    assert s["status"] == "done"


@pytest.mark.asyncio
async def test_process_one_failure_keeps_input_files(tmp_path: Path):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"video")
    csv.write_text("csv")
    sid = await queue.enqueue("u1", str(video), str(csv))
    with patch("webapp.backend.worker.run_analysis",
               side_effect=RuntimeError("boom")):
        await worker.process_one()
    assert video.is_file()
    assert csv.is_file()
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_run_loop_recovers_stale_when_idle():
    recover = AsyncMock(return_value={"requeued": 0, "failed": 0})

    async def process_once_false():
        return False

    sleep_calls = 0

    async def sleep_then_stop(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError()

    with patch("webapp.backend.worker.process_one", side_effect=process_once_false), \
         patch("webapp.backend.queue.recover_stale_jobs", recover), \
         patch("asyncio.sleep", side_effect=sleep_then_stop):
        with pytest.raises(asyncio.CancelledError):
            await worker._run_loop_async()

    recover.assert_awaited()
    assert sleep_calls == 1

def _native_snapshot() -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": 42,
        "scenario": "Tile Frenzy",
        "sources": {
            "stats": {
                "artifact_ref": "run:42:stats",
                "basename": "stats.csv",
                "fingerprint": {"sha256": "stats-sha", "size": 1, "mtime_ns": 1},
                "parser_version": "kovaak_stats.v1",
                "path": "/db-private/runs/42/stats.csv",
                "availability": "available",
            },
            "performance": {
                "artifact_ref": "run:42:performance",
                "basename": "performance.perf",
                "fingerprint": {"sha256": "performance-sha", "size": 2, "mtime_ns": 2},
                "parser_version": "kovaak_performance.v1",
                "path": "/db-private/runs/42/performance.perf",
                "availability": "available",
            },
        },
        "trace": {
            "artifact_ref": "run:42:trace",
            "path": "/db-private/runs/42/trace.bin",
            "availability": "available",
            "format_version": 1,
        },
    }


def _native_v2_snapshot() -> dict:
    snapshot = _native_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v2"
    snapshot["sources"]["stats"]["parser_version"] = "kovaak_stats.v2"
    snapshot["sources"]["performance"]["parser_version"] = "kovaak_performance.v2"
    snapshot["canonical_time_window"] = {
        "schema_version": "canonical_time_window.v1",
        "timebase_version": "time_alignment.v2",
        "start_ms": 1_699_897_600_797,
        "end_ms": 1_699_897_660_797,
        "duration_ms": 60_000,
        "start_source": "stats_challenge_start",
        "end_source": "timer_profile",
        "stats_anchor_status": "mapped_local_time",
        "stats_time_of_day_ms": 6_400_797,
        "stats_local_to_utc_mapping": {
            "version": "stats_local_to_utc.v1",
            "source": "fixture",
            "utc_offset_minutes": 480,
        },
        "warnings": [],
        "window_semantics": "half_open",
    }
    return snapshot


def _scenario_resolution(
    *,
    manifest_status: str = "unlisted",
    dispatch: str = "none",
    aim_family: str = "static_clicking",
    allowed_analyzers: list[str] | None = None,
) -> dict:
    active = manifest_status == "active"
    listed = manifest_status != "unlisted"
    return {
        "schema_version": "scenario_resolution.v1",
        "scenario_hash": "fixture-hash",
        "display_name": "Tile Frenzy",
        "registry_version": "scenario_registry.test.v1",
        "manifest_version": "scenario_manifest.test.v1",
        "scenario_profile_ref": "scenario:static.fixture@1" if listed else None,
        "classification_source": "reviewed_registry" if listed else "unknown",
        "classification_confidence": "confirmed" if listed else "unknown",
        "profile_status": (
            "retired" if manifest_status == "retired" else "active"
        ) if listed else "unknown",
        "reviewed_at": "2026-07-20T00:00:00Z" if listed else None,
        "source_refs": ["review:fixture"] if listed else [],
        "supersedes": [],
        "manifest_status": manifest_status,
        "fixture_ref": "fixture:scenario" if listed else None,
        "review_source_ref": "review:scenario" if listed else None,
        "manifest_reviewed_at": "2026-07-20T00:00:00Z" if listed else None,
        "family_gate_refs": ["gate:family"] if listed else [],
        "aim_family": aim_family if listed else "unknown",
        "subdomains": ["precision"] if listed else [],
        "target_motion": {
            "model": (
                "predictable" if aim_family == "dynamic_clicking" else "static"
            ) if listed else "unknown",
            "target_count_model": "single" if listed else "unknown",
        },
        "allowed_analyzers": (
            allowed_analyzers
            if allowed_analyzers is not None
            else ["native_flicking.v1"]
        ) if listed else [],
        "allowed_metric_families": (
            ["dynamic_clicking"]
            if aim_family == "dynamic_clicking"
            else ["input_kinematics", "static_clicking"]
        ) if listed else [],
        "claim_ceiling": "family_specific" if active else "outcome_only",
        "family_analyzer_dispatch": dispatch,
        "limitations": [] if active else ["scenario_not_in_active_manifest"],
    }


def _video_source(path: Path) -> dict:
    stat = path.stat()
    return {
        "basename": path.name,
        "fingerprint": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "path": str(path),
        "availability": "available",
        "format_version": "mp4",
    }


def _dynamic_visual_summary(*, enabled: bool) -> dict:
    return {
        "visual_quality_profile_ref": "visual-profile:dynamic-fixture.v1",
        "quality": {
            "status": "accepted" if enabled else "limited",
            "enabled_metric_families": ["dynamic_clicking"] if enabled else [],
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "quality_status": "accepted" if enabled else "limited",
            "producer_version": "fixture_detector.v1",
            "enabled_metric_families": ["dynamic_clicking"] if enabled else [],
            "track_count": 1,
            "observation_count": 3,
            "target_coverage": 1.0,
            "crosshair_coverage": 1.0,
            "completeness": "complete",
            "event_counts": {},
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
    }


def _dynamic_analysis_summary() -> dict:
    metric = {
        "schema_version": "metric_record.v1",
        "metric_key": "dynamic_clicking.normalized_click_error",
        "metric_version": "dynamic_clicking.normalized_click_error.v1",
        "value": 0.9,
        "unit": "visible_radius",
        "availability": "available",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": ["analysis:121:source:dynamic-analysis"],
        },
        "population": {"sample_count": 1, "valid_count": 1, "excluded_count": 0},
        "distribution": None,
        "condition_refs": [],
        "event_refs": ["analysis:121:dynamic-click:1"],
        "evidence_segment_refs": ["analysis:121:segment:dynamic:1"],
        "coverage": 1.0,
        "confidence": 1.0,
        "limitations": [],
    }
    return {
        "schema_version": "dynamic_clicking_analysis.v1",
        "analysis_version": "dynamic_clicking.v1",
        "analysis_ref": "analysis:121",
        "analysis_type": "dynamic_clicking",
        "support_status": "supported",
        "scenario_motion_class": "predictable",
        "metrics": {metric["metric_key"]: metric},
        "processed_rows": [{
            "event_ref": "analysis:121:dynamic-click:1",
            "normalized_click_error": 0.9,
        }],
        "processed_event_table": {
            "row_count": 1,
        },
        "comparison": None,
        "limitations": ["motion_predictability_evidence_unavailable"],
    }


def _tracking_visual_summary(*, enabled: bool) -> dict:
    return {
        "visual_quality_profile_ref": "visual-profile:tracking-fixture.v1",
        "quality": {
            "status": "accepted" if enabled else "limited",
            "enabled_metric_families": ["tracking"] if enabled else [],
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "quality_status": "accepted" if enabled else "limited",
            "producer_version": "fixture_detector.v1",
            "enabled_metric_families": ["tracking"] if enabled else [],
            "track_count": 1,
            "observation_count": 3,
            "target_coverage": 1.0,
            "crosshair_coverage": 1.0,
            "completeness": "complete",
            "event_counts": {},
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
    }


def _tracking_analysis_summary() -> dict:
    metric_key = "continuous_tracking.target_relative_error_px"
    metric = {
        "schema_version": "metric_record.v1",
        "metric_key": metric_key,
        "metric_version": f"{metric_key}.v1",
        "value": 8.0,
        "unit": "px",
        "availability": "available",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": ["analysis:123:source:tracking-analysis"],
        },
        "population": {"sample_count": 3, "valid_count": 3, "excluded_count": 0},
        "distribution": None,
        "condition_refs": ["analysis:123:condition:predictable"],
        "event_refs": ["analysis:123:tracking-episode:1"],
        "evidence_segment_refs": ["analysis:123:segment:tracking:1"],
        "coverage": 1.0,
        "confidence": 1.0,
        "limitations": [],
    }
    return {
        "schema_version": "continuous_tracking_analysis.v1",
        "analysis_version": "continuous_tracking.v1",
        "analysis_ref": "analysis:123",
        "analysis_type": "continuous_tracking",
        "support_status": "supported",
        "scenario_motion_class": "predictable",
        "metrics": {metric_key: metric},
        "processed_rows": [{
            "event_ref": "analysis:123:tracking-episode:1",
            "row_kind": "tracking_episode",
            "target_relative_error_px": 8.0,
        }],
        "comparison": None,
        "limitations": [],
    }


def _switching_visual_summary(*, enabled: bool) -> dict:
    return {
        "visual_quality_profile_ref": "visual-profile:switching-fixture.v1",
        "quality": {
            "status": "accepted" if enabled else "limited",
            "enabled_metric_families": ["target_switching"] if enabled else [],
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "quality_status": "accepted" if enabled else "limited",
            "producer_version": "fixture_detector.v1",
            "enabled_metric_families": ["target_switching"] if enabled else [],
            "track_count": 3,
            "observation_count": 10,
            "target_coverage": 1.0,
            "crosshair_coverage": 1.0,
            "completeness": "complete",
            "event_counts": {"shot": 2, "kill": 2},
            "limitations": [] if enabled else ["fixture_quality_gate"],
        },
    }


def _switching_analysis_summary() -> dict:
    metric_key = "target_switching.transition_time_ms"
    metric = {
        "schema_version": "metric_record.v1",
        "metric_key": metric_key,
        "metric_version": f"{metric_key}.v1",
        "value": 120.0,
        "unit": "ms",
        "availability": "available",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": ["analysis:124:source:target-switching-analysis"],
        },
        "population": {"sample_count": 1, "valid_count": 1, "excluded_count": 0},
        "distribution": None,
        "condition_refs": ["condition:target_switching:observable_chain"],
        "event_refs": ["analysis:124:switch-chain:1"],
        "evidence_segment_refs": ["analysis:124:segment:switching:1"],
        "coverage": 1.0,
        "confidence": 1.0,
        "limitations": ["comparison_only_no_static_threshold"],
    }
    return {
        "schema_version": "target_switching_analysis.v1",
        "analysis_version": "target_switching.v1",
        "analysis_ref": "analysis:124",
        "analysis_type": "target_switching",
        "support_status": "supported",
        "metrics": {metric_key: metric},
        "processed_rows": [{
            "event_ref": "analysis:124:switch-chain:1",
            "row_kind": "switch_chain",
            "classification": "observable_target_switch",
            "transition_time_ms": 120.0,
            "limitations": [],
        }],
        "comparison": None,
        "limitations": [],
    }


def test_dynamic_result_uses_joint_target_crosshair_and_click_coverage():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="dynamic_clicking",
        allowed_analyzers=["dynamic_clicking.v1"],
    )
    job = {
        "id": 121,
        "user_id": "u1",
        "analysis_type": "dynamic_clicking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "managed.mp4",
    }
    visual = _dynamic_visual_summary(enabled=True)
    visual["safe_summary"]["crosshair_coverage"] = 0.75

    result = worker._build_dynamic_result_v2(
        job,
        _dynamic_analysis_summary(),
        visual,
        created_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
    )

    assert result["evidence"]["coverage"] == pytest.approx(0.75)


def test_dynamic_evidence_merge_failure_downgrades_public_result(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="dynamic_clicking",
        allowed_analyzers=["dynamic_clicking.v1"],
    )
    job = {
        "id": 121,
        "user_id": "u1",
        "analysis_type": "dynamic_clicking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "managed.mp4",
    }
    visual = _dynamic_visual_summary(enabled=True)
    dynamic = _dynamic_analysis_summary()
    result = worker._build_dynamic_result_v2(
        job,
        dynamic,
        visual,
        created_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
    )
    base_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": "analysis:121",
        "canonical_time_window": snapshot["canonical_time_window"],
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }

    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"fixture"), \
         patch("kovaak_tracker.csv_parser.parse_stats_bytes", return_value=object()), \
         patch("kovaak_tracker.performance_parser.parse_performance_bytes", return_value=object()), \
         patch(
             "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.visual_signals.extend_analysis_evidence_with_visual_signals_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.dynamic_clicking_analysis.extend_analysis_evidence_with_dynamic_clicking_v1",
             side_effect=ValueError("fixture dynamic merge failure"),
         ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result=visual,
            dynamic_result=dynamic,
        )

    assert updated["analysis_version"] == "scenario_outcome_only.v1"
    assert updated["analysis_type"] == "dynamic_clicking"
    assert updated["deterministic"]["support_status"] == "outcome_only"
    assert updated["deterministic"]["metrics"] == {}
    assert "diagnosis" not in updated["deterministic"]
    assert updated["deterministic"]["limitations"] == [
        "dynamic_clicking_evidence_artifact_unavailable"
    ]
    assert "derived_artifact" in updated["evidence"]


def test_tracking_evidence_merge_failure_downgrades_public_result(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="continuous_tracking",
        allowed_analyzers=["continuous_tracking.v1"],
    )
    snapshot["scenario_resolution"]["allowed_metric_families"] = [
        "continuous_tracking"
    ]
    job = {
        "id": 123,
        "user_id": "u1",
        "analysis_type": "continuous_tracking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "managed.mp4",
    }
    visual = _tracking_visual_summary(enabled=True)
    tracking = _tracking_analysis_summary()
    result = worker._build_continuous_tracking_result_v2(
        job,
        tracking,
        visual,
        created_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
    )
    base_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": "analysis:123",
        "canonical_time_window": snapshot["canonical_time_window"],
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }

    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"fixture"), \
         patch("kovaak_tracker.csv_parser.parse_stats_bytes", return_value=object()), \
         patch("kovaak_tracker.performance_parser.parse_performance_bytes", return_value=object()), \
         patch(
             "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.visual_signals.extend_analysis_evidence_with_visual_signals_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.tracking_analysis.extend_analysis_evidence_with_continuous_tracking_v1",
             side_effect=ValueError("fixture tracking merge failure"),
         ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result=visual,
            tracking_result=tracking,
        )

    assert updated["analysis_version"] == "scenario_outcome_only.v1"
    assert updated["analysis_type"] == "continuous_tracking"
    assert updated["deterministic"]["support_status"] == "outcome_only"
    assert updated["deterministic"]["metrics"] == {}
    assert "diagnosis" not in updated["deterministic"]
    assert updated["deterministic"]["limitations"] == [
        "continuous_tracking_evidence_artifact_unavailable"
    ]
    assert "derived_artifact" in updated["evidence"]


def test_switching_evidence_merge_failure_downgrades_public_result(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="target_switching",
        allowed_analyzers=["target_switching.v1"],
    )
    snapshot["scenario_resolution"].update({
        "target_motion": {"model": "mixed", "target_count_model": "concurrent"},
        "allowed_metric_families": ["target_switching"],
    })
    job = {
        "id": 124,
        "user_id": "u1",
        "analysis_type": "target_switching",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "managed.mp4",
    }
    visual = _switching_visual_summary(enabled=True)
    switching = _switching_analysis_summary()
    result = worker._build_target_switching_result_v2(
        job,
        switching,
        visual,
        created_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
    )
    base_artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": "analysis:124",
        "canonical_time_window": snapshot["canonical_time_window"],
        "canonical_run_facts": None,
        "normalized_outcome_records": [],
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": [],
    }

    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"fixture"), \
         patch("kovaak_tracker.csv_parser.parse_stats_bytes", return_value=object()), \
         patch("kovaak_tracker.performance_parser.parse_performance_bytes", return_value=object()), \
         patch(
             "kovaak_tracker.analysis_evidence.build_analysis_evidence_artifact_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.visual_signals.extend_analysis_evidence_with_visual_signals_v1",
             return_value=base_artifact,
         ), \
         patch(
             "kovaak_tracker.target_switching_analysis.extend_analysis_evidence_with_target_switching_v1",
             side_effect=ValueError("fixture switching merge failure"),
         ):
        updated = worker._maybe_commit_analysis_evidence(
            job,
            result,
            visual_result=visual,
            switching_result=switching,
        )

    assert updated["analysis_version"] == "scenario_outcome_only.v1"
    assert updated["analysis_type"] == "target_switching"
    assert updated["deterministic"]["support_status"] == "outcome_only"
    assert updated["deterministic"]["metrics"] == {}
    assert "diagnosis" not in updated["deterministic"]
    assert updated["deterministic"]["limitations"] == [
        "target_switching_evidence_artifact_unavailable"
    ]
    assert "derived_artifact" in updated["evidence"]


def _native_adapter_result() -> dict:
    return {
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "status": "available",
        "evidence": {
            "sources": {
                "raw_input": {
                    "source": "raw_input",
                    "role": "kinematics",
                    "availability": "available",
                    "alignment": "aligned",
                    "warnings": [],
                },
                "performance": {
                    "source": "performance",
                    "role": "event_anchor",
                    "availability": "available",
                    "alignment": "aligned",
                    "warnings": [],
                },
                "stats": {
                    "source": "stats",
                    "role": "scenario_config",
                    "availability": "available",
                    "alignment": "not_required",
                    "warnings": [],
                },
            },
            "alignment": {"status": "aligned", "coverage_ratio": 1.0},
            "coverage": 1.0,
            "warnings": [],
        },
        "deterministic": {
            "trajectory": {"unit": "raw_counts", "point_count": 2, "points": [{"x": 1}]},
            "metrics": {
                "path_length": {
                    "key": "path_length",
                    "value": 10.0,
                    "unit": "raw_counts",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [],
                },
                "calibrated_path_length": {
                    "key": "calibrated_path_length",
                    "value": 2.0,
                    "unit": "cm",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input", "calibration"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [],
                },
                "path_efficiency": {
                    "key": "path_efficiency",
                    "value": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [],
                },
                "straightness": {
                    "key": "straightness",
                    "value": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [],
                },
                "decel_frac": {
                    "key": "decel_frac",
                    "value": 0.8,
                    "median": 0.8,
                    "med": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 1,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": ["descriptive_distribution_not_health_threshold"],
                    "sample_refs": ["flick:1"],
                },
                "sparc": {
                    "key": "sparc",
                    "value": -7.0,
                    "median": -7.0,
                    "med": -7.0,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking.sparc.v2",
                    "sample_count": 1,
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [
                        "descriptive_distribution_not_health_threshold",
                        "sparc_cross_polling_comparability_unverified",
                    ],
                    "sample_refs": ["flick:1"],
                },
            },
            "timeline": [
                {
                    "id": "flick:1",
                    "event_type": "flick",
                    "source": "raw_input",
                    "start_ms": 10.0,
                    "peak_ms": 20.0,
                    "end_ms": 40.0,
                    "settle_end_ms": 50.0,
                    "quality": "available",
                    "coverage": 1.0,
                    "limitations": [],
                    "metrics": {
                        "peak_speed": {
                            "value": 2000.0,
                            "unit": "raw_counts_per_second",
                        }
                    },
                }
            ],
        },
        "limitations": [],
    }


def _native_v2_adapter_result() -> dict:
    result = _native_adapter_result()
    result["evidence"]["alignment"].update(
        {
            "challenge_start_epoch_ms": 1_699_897_600_797,
            "challenge_end_epoch_ms": 1_699_897_660_797,
            "window_semantics": "half_open",
        }
    )
    return result


def test_real_native_metrics_feed_deterministic_explanation_contract():
    from kovaak_tracker.native_flicking_analysis import analyze_native_flicking

    native_result = analyze_native_flicking(
        [
            {"timestamp_ms": 1_000, "dx": 0, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_010, "dx": 6, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_020, "dx": 5, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_030, "dx": 4, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_040, "dx": 3, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_050, "dx": 2, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_060, "dx": 1, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_070, "dx": 0, "dy": 0, "buttons": 1},
            {"timestamp_ms": 1_080, "dx": 0, "dy": 0, "buttons": 0},
        ],
        {"challenge_start_utc": 1_000, "time_limit_ms": 80, "events": []},
    )

    deterministic = worker._native_deterministic_v2(native_result)
    issue = deterministic["diagnosis"]["issues"][0]

    assert issue["signal"] == "decel_frac high"
    assert issue["severity"] == "info"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["decel_frac"]
    assert issue["event_refs"] == ["flick:1"]
    assert issue["limitations"] == ["threshold_requires_product_calibration"]
    assert "root_causes" not in issue and "prescriptions" not in issue
    assert "observation_ref" not in issue
    assert "knowledge_registry_version" not in issue
    assert "knowledge_entry_refs" not in issue
    assert deterministic["diagnosis"]["summary"]["decel_frac"]["med"] == pytest.approx(5 / 6)


def test_native_projection_keeps_registry_backed_static_issue_without_legacy_teaching_text():
    diagnosis = worker._native_diagnosis({
        "reverse_ratio": {
            "med": 0.30,
            "metric_version": "native_flicking.reverse_ratio.v1",
            "sample_refs": ["flick:1"],
        },
    })

    issue = diagnosis["issues"][0]
    assert issue["observation_ref"] == "metric.terminal_control"
    assert issue["knowledge_registry_version"] == "2026-08-06.v6"
    assert issue["knowledge_entry_refs"] == [
        "knowledge:static.flicking-terminal-control@2"
    ]
    assert "root_causes" not in issue and "prescriptions" not in issue


def test_partial_native_alignment_is_unclassified_and_keeps_metrics_limited():
    native_result = {
        "input_mode": "input_native",
        "status": "partial",
        "evidence": {
            "alignment": {"status": "partial"},
            "coverage": 0.5,
        },
        "deterministic": {
            "trajectory": {"unit": "raw_counts", "point_count": 2},
            "metrics": {
                "path_length": {
                    "key": "path_length",
                    "value": 5.0,
                    "unit": "raw_counts",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking.v1",
                    "sample_count": 2,
                    "coverage": 0.5,
                    "limitations": [],
                },
            },
            "timeline": [],
        },
        "limitations": ["alignment_partial", "left_click_anchors_missing"],
    }

    deterministic = worker._native_deterministic_v2(native_result)

    assert deterministic["diagnosis"]["profile"]["archetype_id"] == "unclassified"
    assert deterministic["diagnosis"]["profile"]["confidence"] == 0.0
    metric = deterministic["metrics"]["path_length"]
    assert metric["coverage"] == pytest.approx(0.5)
    assert "alignment_partial" in metric["limitations"]


def test_visual_pts_quality_caps_metric_coverage_without_dropping_value():
    metrics = {
        "continuous_tracking.target_relative_error_px": {
            "value": 8.0,
            "coverage": 1.0,
            "limitations": [],
        },
    }
    quality = worker._visual_quality_projection({
        "limitations": ["missing_frame_pts"],
        "safe_summary": {"target_coverage": 0.75, "crosshair_coverage": 0.8},
    })

    worker._project_metric_quality(metrics, quality)

    metric = metrics["continuous_tracking.target_relative_error_px"]
    assert metric["value"] == 8.0
    assert metric["coverage"] == pytest.approx(0.75)
    assert metric["limitations"] == ["missing_frame_pts"]


async def _capture_mode_result(
    job: dict,
    *,
    native_result: dict,
    parsed_stats=None,
    cv_result=None,
    cv_error=None,
):
    completed: list[dict] = []
    calls: list[str] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def native(*_args, **kwargs):
        calls.append("native")
        if kwargs.get("return_parsed_stats"):
            return native_result, parsed_stats
        return native_result

    def cv(*_args, **_kwargs):
        calls.append("cv")
        if cv_error is not None:
            raise cv_error
        return cv_result

    async def isolated_cv(*_args, **_kwargs):
        if cv_error is not None:
            raise cv_error
        if cv_result is not None:
            return cv_result
        from kovaak_tracker.visual_signals import VisualPreprocessingUnavailable

        raise VisualPreprocessingUnavailable("visual_quality_profile_unavailable")

    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done)), \
         patch("webapp.backend.worker.run_native_analysis", side_effect=native) as native_mock, \
         patch(
             "webapp.backend.worker.run_visual_preprocessing_isolated",
             new=AsyncMock(side_effect=isolated_cv),
         ), \
         patch("webapp.backend.worker.run_analysis", side_effect=cv) as cv_mock:
        assert await worker.process_one() is True

    assert len(completed) == 1
    return completed[0], calls, native_mock, cv_mock


async def _capture_mode_failure(
    job: dict,
    *,
    native_result: dict,
    cv_side_effect=None,
):
    failed: list[dict] = []

    async def mark_failed(_sid, error, *, worker_id):
        failed.append(error)
        return True

    def native(*_args, **kwargs):
        if kwargs.get("return_parsed_stats"):
            return native_result, object()
        return native_result

    mark_done = AsyncMock(return_value=True)
    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=mark_done), \
         patch("webapp.backend.queue.mark_failed", new=AsyncMock(side_effect=mark_failed)), \
         patch("webapp.backend.worker.run_native_analysis", side_effect=native) as native_mock, \
         patch("webapp.backend.worker.run_analysis", side_effect=cv_side_effect) as cv_mock:
        assert await worker.process_one() is True

    assert len(failed) == 1
    mark_done.assert_not_awaited()
    return failed[0], native_mock, cv_mock


@pytest.mark.asyncio
async def test_process_one_input_native_uses_snapshot_sources_without_cv_or_private_paths():
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = {
        "basename": "ignored.mp4",
        "fingerprint": {"sha256": "ignored", "size": 1, "mtime_ns": 1},
        "path": "/db-private/source/ignored.mp4",
        "availability": "available",
        "format_version": "mp4",
    }
    job = {
        "id": 101,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "/managed/session/ignored.mp4",
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-07-13 12:00:00",
    }

    native_result = _native_adapter_result()
    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=native_result,
    )

    assert calls == ["native"]
    native_mock.assert_called_once_with(
        snapshot,
        30.0,
        90.0,
        profile_default=None,
        manual_override=None,
    )
    cv_mock.assert_not_called()
    assert result["schema_version"] == "analysis_result.v2"
    assert result["analysis_version"] == "native_flicking.v1"
    assert result["owner_id"] == "u1"
    assert "local_profile" not in result
    assert result["input_mode"] == "input_native"
    assert result["kovaak_run_ref"] == "run:42"
    assert set(result["evidence"]) == {
        "sources", "provenance", "availability", "alignment", "coverage", "warnings",
    }
    assert result["evidence"]["coverage"] == 1.0
    assert result["evidence"]["sources"]["raw_input"]["artifact_ref"] == "run:42:trace"
    assert result["evidence"]["sources"]["raw_input"]["parser_or_format_version"] == 1
    assert result["evidence"]["sources"]["stats"]["parser_or_format_version"] == "kovaak_stats.v1"
    assert result["evidence"]["sources"]["performance"]["parser_or_format_version"] == "kovaak_performance.v1"
    assert "mp4" not in result["evidence"]["sources"]
    assert "video" not in result["input_snapshot"]["sources"]
    assert result["input_snapshot"]["trace"] == {
        "artifact_ref": "run:42:trace",
        "availability": "available",
        "format_version": 1,
    }
    public_deterministic = result["deterministic"]
    source_deterministic = native_result["deterministic"]
    assert public_deterministic["metrics"] == source_deterministic["metrics"]
    assert public_deterministic["metrics"]["sparc"]["metric_version"] == (
        "native_flicking.sparc.v2"
    )
    assert all(
        issue["signal"] != "sparc low"
        for issue in public_deterministic["diagnosis"]["issues"]
    )
    assert public_deterministic["timeline"] == source_deterministic["timeline"]
    assert public_deterministic["trajectory"] == {
        "unit": "raw_counts",
        "point_count": 2,
    }
    assert "points" not in public_deterministic["trajectory"]
    assert "/db-private/" not in str(result)
    required_metric_fields = {
        "key", "value", "unit", "availability", "provenance",
        "metric_version", "coverage", "classification", "limitations",
    }
    for key, metric in public_deterministic["metrics"].items():
        assert required_metric_fields <= set(metric), key
        assert metric["key"] == key
        assert metric["classification"] == "deterministic"

    manifest = result["artifact_manifest"]
    assert manifest["analysis_id"] == "analysis:101"
    entries = {
        entry["id"]: entry
        for entry in [*manifest["external_inputs"], *manifest["owned_outputs"]]
    }
    assert entries["run:42:trace"] == {
        "id": "run:42:trace",
        "kind": "raw_input",
        "source": "raw_input",
        "availability": "available",
        "ownership": "kovaak_run",
        "managed": True,
        "local_only": True,
        "status": "available",
        "format_version": 1,
        "derived_from": [],
    }
    assert entries["analysis:101"]["ownership"] == "analysis"
    assert entries["analysis:101"]["managed"] is True
    assert entries["analysis:101"]["local_only"] is True
    assert entries["analysis:101"]["format_version"] == "analysis_result.v2"
    assert set(entries["analysis:101"]["derived_from"]) == {
        "run:42:stats", "run:42:performance", "run:42:trace",
    }
    assert all(entry["kind"] != "mp4" for entry in entries.values())
    issue = public_deterministic["diagnosis"]["issues"][0]
    assert issue["signal"] == "decel_frac high"
    assert issue["severity"] == "info"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["decel_frac"]
    assert issue["event_refs"] == ["flick:1"]
    assert "root_causes" not in issue and "prescriptions" not in issue
    assert "observation_ref" not in issue
    assert "knowledge_registry_version" not in issue
    assert "knowledge_entry_refs" not in issue


@pytest.mark.asyncio
async def test_process_one_v2_native_projects_frozen_window_into_result():
    snapshot = _native_v2_snapshot()
    job = {
        "id": 107,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, _native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_v2_adapter_result(),
    )

    assert calls == ["native"]
    cv_mock.assert_not_called()
    assert result["input_snapshot"]["canonical_time_window"] == (
        snapshot["canonical_time_window"]
    )
    assert result["evidence"]["alignment"]["challenge_start_epoch_ms"] == (
        snapshot["canonical_time_window"]["start_ms"]
    )
    assert result["evidence"]["alignment"]["challenge_end_epoch_ms"] == (
        snapshot["canonical_time_window"]["end_ms"]
    )
    assert result["evidence"]["sources"]["stats"]["parser_or_format_version"] == (
        "kovaak_stats.v2"
    )
    assert result["evidence"]["sources"]["performance"]["parser_or_format_version"] == (
        "kovaak_performance.v2"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest_status", ["unlisted", "pending_gate", "retired"])
async def test_process_one_non_active_scenario_is_outcome_only_without_family_analyzer(
    manifest_status: str,
):
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status=manifest_status,
    )
    job = {
        "id": 108,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    parsed_stats = MagicMock(cm_per_360=34.0, fov=103.0)
    with patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual",
        return_value=parsed_stats,
    ) as parse_stats:
        result, calls, native_mock, cv_mock = await _capture_mode_result(
            job,
            native_result=_native_v2_adapter_result(),
        )

    assert calls == []
    native_mock.assert_not_called()
    cv_mock.assert_not_called()
    assert result["analysis_version"] == "scenario_outcome_only.v1"
    assert result["deterministic"] == {
        "support_status": "outcome_only",
        "metrics": {},
        "limitations": ["scenario_not_in_active_manifest"],
    }
    assert "diagnosis" not in result["deterministic"]
    assert result["input_snapshot"]["scenario_resolution"] == (
        snapshot["scenario_resolution"]
    )
    parse_stats.assert_called_once_with(snapshot)
    assert result["input_snapshot"]["calibration"] == {
        "cm_per_360": {"value": 34.0, "source": "stats"},
        "fov": {"value": 103.0, "source": "stats"},
    }
    assert {warning["code"] for warning in result["warnings"]} == {
        "scenario_outcome_only"
    }
    validated = validate_analysis_result_v2_for_persistence(
        result,
        owner_id="u1",
        analysis_id="analysis:108",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="run:42",
    )
    assert validated["deterministic"]["metrics"] == {}


@pytest.mark.asyncio
async def test_outcome_only_legacy_flat_calibration_is_frozen_into_result_and_terminal_snapshot():
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="legacy-flat-outcome-only", scenario="Scenario",
    )
    snapshot = _native_v2_snapshot()
    snapshot["run_id"] = run["id"]
    snapshot["scenario_resolution"] = _scenario_resolution(manifest_status="unlisted")
    sid = await queue.enqueue(
        "u1", "", "",
        cm_per_360=34.0,
        fov=103.0,
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot=snapshot,
    )
    # Simulate a pre-calibration-request job that only has legacy flat columns.
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET calibration_request_json=NULL WHERE id=?", (sid,),
    )
    await conn.commit()

    parsed_stats = MagicMock(cm_per_360=None, fov=None)
    with patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual",
        return_value=parsed_stats,
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=lambda _job, result, **_kwargs: result,
    ):
        assert await worker.process_one() is True

    session = await queue.get_session(sid)
    expected = {
        "cm_per_360": {"value": 34.0, "source": "manual_override"},
        "fov": {"value": 103.0, "source": "manual_override"},
    }
    assert session["status"] == "done"
    assert session["result"]["input_snapshot"]["calibration"] == expected
    assert session["calibration_snapshot"] == expected


@pytest.mark.parametrize("mode", ["video", "native"])
def test_legacy_flat_values_do_not_override_explicit_manual_calibration(mode: str):
    stats = MagicMock(cm_per_360=None, fov=None)
    if mode == "video":
        with patch(
            "kovaak_tracker.pan_tracker.analyze_flicking_fair_summary",
            return_value=({}, {}),
        ) as analyze:
            _summary, extras = worker.run_analysis(
                "video.mp4",
                "stats.csv",
                cm_per_360=30.0,
                fov=90.0,
                stats=stats,
                manual_override={"cm_per_360": 40.0, "fov": 100.0},
            )
        assert (analyze.call_args.kwargs["cm_per_360"], analyze.call_args.kwargs["fov"]) == (
            40.0, 100.0,
        )
    else:
        with patch(
            "webapp.backend.worker._read_frozen_source_bytes",
            return_value=b"fixture",
        ), patch(
            "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
            return_value=[],
        ), patch(
            "kovaak_tracker.csv_parser.parse_stats_bytes",
            return_value=stats,
        ), patch(
            "kovaak_tracker.performance_parser.parse_performance_bytes",
            return_value=object(),
        ), patch(
            "kovaak_tracker.native_flicking_analysis.analyze_native_flicking",
            return_value={},
        ) as analyze:
            result = worker.run_native_analysis(
                _native_snapshot(),
                cm_per_360=30.0,
                fov=90.0,
                manual_override={"cm_per_360": 40.0, "fov": 100.0},
            )
        assert result["calibration"] == {
            "cm_per_360": {"value": 40.0, "source": "manual_override"},
            "fov": {"value": 100.0, "source": "manual_override"},
        }
        assert analyze.call_args.kwargs["stats"]["cm_per_360"] == 40.0
        assert analyze.call_args.kwargs["stats"]["fov"] == 100.0


@pytest.mark.asyncio
async def test_process_one_active_exact_hash_dispatches_only_frozen_allowed_analyzer():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
    )
    job = {
        "id": 109,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_v2_adapter_result(),
    )

    assert calls == ["native"]
    native_mock.assert_called_once()
    cv_mock.assert_not_called()
    assert result["analysis_version"] == "native_flicking.v1"


@pytest.mark.asyncio
async def test_process_one_local_dynamic_baseline_keeps_native_facts_without_static_diagnosis():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = {
        **_scenario_resolution(manifest_status="unlisted", dispatch="none"),
        "classification_source": "local_scenario_definition",
        "classification_confidence": "confirmed",
        "aim_family": "dynamic_clicking",
        "subdomains": ["reactive", "control"],
        "target_motion": {"model": "reactive", "target_count_model": "concurrent"},
        "allowed_analyzers": ["dynamic_clicking.baseline.v1"],
        "allowed_metric_families": ["outcome", "input_kinematics"],
        "claim_ceiling": "descriptive_only",
        "family_analyzer_dispatch": "allowed",
        "limitations": [
            "exact_visual_profile_unavailable",
            "target_relative_facts_unavailable",
            "outcome_association_unavailable",
            "scenario_prescription_unavailable",
        ],
    }
    job = {
        "id": 110,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_v2_adapter_result(),
    )

    assert calls == ["native"]
    native_mock.assert_called_once()
    cv_mock.assert_not_called()
    assert result["analysis_version"] == "dynamic_clicking.baseline.v1"
    assert result["analysis_type"] == "dynamic_clicking"
    assert result["scenario"]["support_status"] == "partial"
    assert result["deterministic"]["diagnosis"]["issues"] == []
    assert "target_relative_facts_unavailable" in result["deterministic"]["limitations"]


def test_exact_packaged_static_v3_snapshot_dispatches_native_flicking():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = scenario_profiles.resolve_scenario_profile(
        "7378a811f430b6072d052a75896afb98",
        display_name="1wall 6targets small",
    )

    assert worker._scenario_dispatch(
        {"analysis_type": "flicking", "input_snapshot": snapshot},
        "input_native",
    ) == "native_flicking.v1"

    metric_restricted = {
        **snapshot,
        "scenario_resolution": {
            **snapshot["scenario_resolution"],
            "allowed_metric_families": ["input_kinematics"],
        },
    }
    assert worker._scenario_dispatch(
        {"analysis_type": "flicking", "input_snapshot": metric_restricted},
        "input_native",
    ) == "outcome_only"


def test_local_dynamic_baseline_dispatches_native_facts_without_exact_visual_profile():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    descriptor = scenario_profiles.parse_local_scenario_behavior_descriptor(
        b"""Name=1wall5targets_pasu
AddedBots=test.bot;test.bot;test.bot;test.bot;test.bot
PlayerCharacters=player
[Bot Profile]
Name=test
DodgeProfileNames=test
CharacterProfile=react
[Character Profile]
Name=react
MaxSpeed=1300
[Dodge Profile]
Name=test
ToggleLeftRight=true
ToggleForwardBack=true
[Character Profile]
Name=player
WeaponProfileNames=pistol
[Weapon Profile]
Name=pistol
Type=Hitscan
ShotsPerClick=1
DamagePerShot=1000
Category=SemiAuto
""",
        expected_display_name="1wall5targets_pasu",
    )
    snapshot["scenario_resolution"] = scenario_profiles.resolve_scenario_profile(
        "a5be19c6e6aeb0d774c5e9d9fb497e91",
        display_name="1wall5targets_pasu",
        behavior_descriptor=descriptor,
    )

    assert worker._scenario_dispatch(
        {"analysis_type": "flicking", "input_snapshot": snapshot},
        "input_native",
    ) == "dynamic_clicking.baseline.v1"
    assert worker._scenario_dispatch(
        {"analysis_type": "flicking", "input_snapshot": snapshot},
        "video_fallback",
    ) == "outcome_only"


def test_local_static_baseline_dispatches_native_facts_without_exact_visual_profile():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    descriptor = scenario_profiles.parse_local_scenario_behavior_descriptor(
        b"""Name=unknown static
AddedBots=target.bot;target.bot
PlayerCharacters=player
[Bot Profile]
Name=target
CharacterProfile=target
[Character Profile]
Name=target
MaxSpeed=0
[Character Profile]
Name=player
WeaponProfileNames=pistol
[Weapon Profile]
Name=pistol
Type=Hitscan
ShotsPerClick=1
DamagePerShot=1
Category=SemiAuto
""",
        expected_display_name="unknown static",
    )
    snapshot["scenario_resolution"] = scenario_profiles.resolve_scenario_profile(
        "unknown-static-hash",
        display_name="unknown static",
        behavior_descriptor=descriptor,
    )

    assert worker._scenario_dispatch(
        {"analysis_type": "flicking", "input_snapshot": snapshot},
        "input_native",
    ) == "static_clicking.baseline.v1"


def test_tracking_worker_adapter_requires_one_target_and_passes_only_validated_changes():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["canonical_time_window"] = {
        **snapshot["canonical_time_window"],
        "start_ms": 0,
        "end_ms": 300,
        "duration_ms": 300,
    }
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="continuous_tracking",
        allowed_analyzers=["continuous_tracking.v1"],
    )
    snapshot["scenario_resolution"]["allowed_metric_families"] = [
        "continuous_tracking"
    ]
    job = {"id": 122, "input_snapshot": snapshot}
    samples = [
        {"canonical_time_ms": time_ms, "x": 100.0, "y": 100.0, "confidence": 1.0}
        for time_ms in (0, 100, 200)
    ]
    visual = {
        "analysis_ref": "analysis:122",
        "canonical_time_window": snapshot["canonical_time_window"],
        "quality": {"status": "accepted", "enabled_metric_families": ["tracking"]},
        "local_samples": {
            "crosshair.position": samples,
            "target.1.position": [
                {**sample, "visible_radius": 10.0} for sample in samples
            ],
        },
        "track_summaries": [{"track_ref": "analysis:122:target-track:1", "limitations": []}],
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": "analysis:122",
            "events": [
                {
                    "event_id": "analysis:122:target-change:1",
                    "event_kind": "target_change_point",
                    "start_ms": 100,
                    "end_ms": 100,
                    "actor_refs": ["analysis:122:target-track:1"],
                    "source_refs": ["analysis:122:source:fixture"],
                    "confidence": 1.0,
                    "attributes": {"change_kind": "direction_reversal"},
                    "limitations": [],
                },
            ],
            "outcome_associations": [],
        },
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }
    captured = {}

    def analyze(payload):
        captured.update(payload)
        return {"analysis_version": "continuous_tracking.v1"}

    with patch("kovaak_tracker.tracking_analysis.analyze_continuous_tracking_v1", analyze):
        assert worker.run_continuous_tracking_analysis(job, visual) == {
            "analysis_version": "continuous_tracking.v1"
        }

    assert captured["target_track"]["track_ref"] == "analysis:122:target-track:1"
    assert captured["player_motion_status"] == "unavailable_fixed_viewport_center"
    assert all(
        sample["measurement_complete"]
        for sample in captured["target_track"]["samples"]
    )
    assert all(
        sample["measurement_complete"]
        for sample in captured["crosshair_samples"]
    )
    assert captured["target_change_points"] == [{
        "event_ref": "analysis:122:target-change:1", "time_ms": 100,
    }]
    assert captured["alignment_latency_ms"] is None
    visual["local_samples"]["target.1.position"][0].pop("visible_radius")
    with patch("kovaak_tracker.tracking_analysis.analyze_continuous_tracking_v1", analyze):
        worker.run_continuous_tracking_analysis(job, visual)
    assert captured["target_track"]["samples"][0]["radius"] is None
    visual["local_samples"]["target.1.position"][0]["visible_radius"] = 10.0
    visual["limitations"] = ["reentry_identity_unresolved"]
    with pytest.raises(ValueError, match="identity"):
        worker.run_continuous_tracking_analysis(job, visual)
    visual.pop("limitations")
    visual["local_samples"]["target.2.position"] = visual["local_samples"]["target.1.position"]
    with pytest.raises(ValueError, match="unambiguous"):
        worker.run_continuous_tracking_analysis(job, visual)


def test_tracking_public_result_projects_profile_and_analyzer_to_coach_context():
    from webapp.backend.coach_context import project_coach_diagnostic_context

    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="continuous_tracking",
        allowed_analyzers=["continuous_tracking.v1"],
    )
    snapshot["scenario_resolution"]["allowed_metric_families"] = [
        "continuous_tracking"
    ]
    job = {
        "id": 123,
        "user_id": "u1",
        "analysis_type": "continuous_tracking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "managed.mp4",
    }
    result = worker._build_continuous_tracking_result_v2(
        job,
        {
            "support_status": "supported",
            "scenario_motion_class": "predictable",
            "metrics": {},
            "processed_rows": [],
            "comparison": None,
            "limitations": [],
        },
        {
            "safe_summary": {
                "target_coverage": 1.0,
                "crosshair_coverage": 1.0,
            },
        },
        created_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
    )
    result["evidence"]["derived_artifact"] = {
        "artifact_ref": "analysis:123:evidence:abc",
        "evidence_revision": "sha256:abc",
        "contract_version": "analysis_evidence_artifact.v1",
        "checksum_sha256": "abc",
        "size_bytes": 1,
    }

    context = project_coach_diagnostic_context(result)

    assert result["scenario"] == {
        "scenario_profile_ref": "scenario:static.fixture@1",
        "analyzer_refs": ["continuous_tracking.v1"],
        "support_status": "supported",
        "limitations": [],
    }
    assert context["schema_version"] == "coach_diagnostic_context.v2"
    assert context["scenario"] == result["scenario"]
    assert "processed_rows" not in json.dumps(result)
    assert "local_samples" not in json.dumps(result)


def test_dynamic_worker_adapter_uses_raw_clicks_and_visual_numeric_signals():
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["canonical_time_window"] = {
        "schema_version": "canonical_time_window.v1",
        "timebase_version": "time_alignment.v2",
        "start_ms": 0,
        "end_ms": 300,
        "duration_ms": 300,
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
        "window_semantics": "half_open",
    }
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="dynamic_clicking",
        allowed_analyzers=["dynamic_clicking.v1"],
    )
    job = {"id": 120, "input_snapshot": snapshot}
    visual = {
        "analysis_ref": "analysis:120",
        "canonical_time_window": snapshot["canonical_time_window"],
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["dynamic_clicking"],
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": [
                {"canonical_time_ms": time_ms, "x": 100.0, "y": 100.0, "confidence": 1.0}
                for time_ms in (0, 100, 200)
            ],
            "target.1.position": [
                {"canonical_time_ms": 0, "x": 120.0, "y": 100.0, "visible_radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 100, "x": 114.5, "y": 100.0, "visible_radius": 10.0, "confidence": 1.0},
                {"canonical_time_ms": 200, "x": 109.0, "y": 100.0, "visible_radius": 10.0, "confidence": 1.0},
            ],
        },
        "track_summaries": [{
            "track_ref": "analysis:120:target-track:1",
            "limitations": [],
        }],
        "signal_bundle": {
            "channels": [
                {"channel_key": key}
                for key in (
                    "crosshair.position_x", "crosshair.position_y",
                    "target.1.position_x", "target.1.position_y",
                    "target.1.visible_radius",
                )
            ],
        },
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": "analysis:120",
            "events": [{
                "event_id": "analysis:120:target-available:1",
                "event_kind": "target_available",
                "start_ms": 0,
                "end_ms": 0,
                "actor_refs": ["analysis:120:target-track:1"],
                "source_refs": ["analysis:120:source:fixture"],
                "confidence": 1.0,
                "attributes": {},
                "limitations": [],
            }],
            "outcome_associations": [],
        },
    }
    trace_points = [
        {"timestamp_ms": 0, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": 200, "dx": 0, "dy": 0, "buttons": 1},
    ]

    with patch("webapp.backend.worker._read_frozen_source_bytes", return_value=b"trace"), patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=trace_points,
    ):
        result = worker.run_dynamic_clicking_analysis(job, visual)

    assert result["analysis_version"] == "dynamic_clicking.v1"
    assert result["processed_rows"][0]["click_time_ms"] == 200
    assert result["processed_rows"][0]["click_ref"] == (
        "analysis:120:event:raw-shot:1"
    )
    assert result["processed_rows"][0]["normalized_click_error"] == pytest.approx(0.9)
    assert result["metrics"]["dynamic_clicking.target_state_accuracy"]["availability"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quality_enabled", "expected_version"),
    [(True, "dynamic_clicking.v1"), (False, "scenario_outcome_only.v1")],
)
async def test_process_one_dynamic_never_falls_back_to_static_and_gates_visual_quality(
    tmp_path: Path,
    quality_enabled: bool,
    expected_version: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="dynamic_clicking",
        allowed_analyzers=["dynamic_clicking.v1"],
    )
    video_source = _video_source(source_video)
    video_source.update({
        "ownership": "run",
        "artifact_ref": "run:42:video:fixture",
    })
    snapshot["sources"]["video"] = video_source
    job = {
        "id": 121,
        "user_id": "u1",
        "analysis_type": "dynamic_clicking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    completed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    visual_result = _dynamic_visual_summary(enabled=quality_enabled)
    dynamic_mock = MagicMock(return_value=_dynamic_analysis_summary())
    native_mock = MagicMock()
    baseline_mock = AsyncMock(return_value={
        "comparable": True,
        "reason": None,
        "baseline_analysis_ref": "analysis:119",
        "baseline_metrics": {
            "dynamic_clicking.normalized_click_error": 0.4,
        },
        "metric_comparisons": {},
    })
    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done),
    ), patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual", return_value=object(),
    ), patch(
        "webapp.backend.worker.run_visual_preprocessing_isolated",
        new=AsyncMock(return_value=visual_result),
    ), patch(
        "webapp.backend.worker.run_dynamic_clicking_analysis", dynamic_mock,
    ), patch(
        "webapp.backend.worker.run_native_analysis", native_mock,
    ), patch(
        "webapp.backend.history_trends.matched_dynamic_baseline_for_user",
        new=baseline_mock,
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=lambda _job, result, **_kwargs: result,
    ):
        assert await worker.process_one() is True

    assert len(completed) == 1
    result = completed[0]
    assert result["analysis_version"] == expected_version
    assert result["analysis_type"] == "dynamic_clicking"
    native_mock.assert_not_called()
    if quality_enabled:
        dynamic_mock.assert_called_once_with(job, visual_result, None)
        assert result["scenario"] == {
            "scenario_profile_ref": snapshot["scenario_resolution"][
                "scenario_profile_ref"
            ],
            "aim_family": "dynamic_clicking",
            "analyzer_refs": ["dynamic_clicking.v1"],
            "support_status": "supported",
            "limitations": list(dynamic_mock.return_value["limitations"]),
        }
        assert result["deterministic"]["metrics"][
            "dynamic_clicking.normalized_click_error"
        ]["value"] == pytest.approx(0.9)
        issue = result["deterministic"]["diagnosis"]["issues"][0]
        assert issue["signal"] == "dynamic click error high"
        assert "severity" not in issue and "prescriptions" not in issue
        assert issue["observation_ref"] == "event.dynamic_click"
        assert issue["knowledge_registry_version"] == "2026-08-06.v6"
        assert issue["knowledge_entry_refs"] == [
            "knowledge:dynamic.click-error-and-acquisition@2"
        ]
        assert result["deterministic"]["candidate_observations"][0][
            "knowledge_entry_refs"
        ] == ["knowledge:dynamic.click-error-and-acquisition@2"]
        assert "processed_rows" not in json.dumps(result)
    else:
        dynamic_mock.assert_not_called()
        assert result["deterministic"]["metrics"] == {}
        assert result["deterministic"]["limitations"] == [
            "dynamic_clicking_visual_quality_unavailable"
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quality_enabled", "expected_version"),
    [(True, "continuous_tracking.v1"), (False, "scenario_outcome_only.v1")],
)
async def test_process_one_tracking_uses_only_tracking_analyzer_after_quality_gate(
    tmp_path: Path,
    quality_enabled: bool,
    expected_version: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="continuous_tracking",
        allowed_analyzers=["continuous_tracking.v1"],
    )
    snapshot["scenario_resolution"]["allowed_metric_families"] = [
        "continuous_tracking"
    ]
    video_source = _video_source(source_video)
    video_source.update({
        "ownership": "run",
        "artifact_ref": "run:42:video:fixture",
    })
    snapshot["sources"]["video"] = video_source
    job = {
        "id": 123,
        "user_id": "u1",
        "analysis_type": "continuous_tracking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    completed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    visual_result = _tracking_visual_summary(enabled=quality_enabled)
    tracking_result = _tracking_analysis_summary() if quality_enabled else None
    pipeline_mock = AsyncMock(return_value=(visual_result, tracking_result))
    evidence_mock = AsyncMock(side_effect=lambda _job, result, *_args: result)
    native_mock = MagicMock()
    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done),
    ), patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual", return_value=object(),
    ), patch(
        "webapp.backend.worker.run_continuous_tracking_pipeline_isolated",
        new=pipeline_mock,
    ), patch(
        "webapp.backend.worker.run_native_analysis", native_mock,
    ), patch(
        "webapp.backend.history_trends.matched_tracking_baseline_for_user",
        new=AsyncMock(return_value={"comparable": False, "reason": "no_comparable_baseline"}),
    ), patch(
        "webapp.backend.worker.commit_continuous_tracking_evidence_isolated",
        new=evidence_mock,
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=lambda _job, result, **_kwargs: result,
    ):
        assert await worker.process_one() is True

    result = completed[0]
    assert result["analysis_version"] == expected_version
    assert result["analysis_type"] == "continuous_tracking"
    native_mock.assert_not_called()
    pipeline_mock.assert_awaited_once_with(job)
    if quality_enabled:
        evidence_mock.assert_awaited_once_with(
            job,
            result,
            visual_result,
            tracking_result,
        )
        assert result["deterministic"]["metrics"][
            "continuous_tracking.target_relative_error_px"
        ]["value"] == 8.0
        assert result["scenario"]["analyzer_refs"] == ["continuous_tracking.v1"]
        assert "processed_rows" not in json.dumps(result)
    else:
        evidence_mock.assert_not_awaited()
        assert result["deterministic"]["metrics"] == {}
        assert result["deterministic"]["limitations"] == [
            "continuous_tracking_visual_quality_unavailable"
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quality_enabled", "expected_version"),
    [(True, "target_switching.v1"), (False, "scenario_outcome_only.v1")],
)
async def test_process_one_switching_requires_quality_and_formal_chain(
    tmp_path: Path,
    quality_enabled: bool,
    expected_version: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="target_switching",
        allowed_analyzers=["target_switching.v1"],
    )
    snapshot["scenario_resolution"].update({
        "target_motion": {"model": "mixed", "target_count_model": "concurrent"},
        "allowed_metric_families": ["target_switching"],
    })
    video_source = _video_source(source_video)
    video_source.update({
        "ownership": "run",
        "artifact_ref": "run:42:video:fixture",
    })
    snapshot["sources"]["video"] = video_source
    job = {
        "id": 124,
        "user_id": "u1",
        "analysis_type": "target_switching",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    completed: list[dict] = []
    committed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def commit(_job, result, **kwargs):
        committed.append(kwargs)
        return result

    visual_result = _switching_visual_summary(enabled=quality_enabled)
    episode_result = {
        "schema_version": "visual_target_episode_artifact.v1",
        "status": "available",
    }
    pipeline_mock = AsyncMock(return_value=(visual_result, episode_result))
    switching_mock = MagicMock(return_value=_switching_analysis_summary())
    frozen_stats = object()
    native_mock = MagicMock()
    baseline_mock = AsyncMock(return_value={
        "comparable": True,
        "reason": None,
        "baseline_analysis_ref": "analysis:120",
        "baseline_metrics": {"target_switching.transition_time_ms": 90.0},
        "metric_comparisons": {},
    })
    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done),
    ), patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual", return_value=frozen_stats,
    ), patch(
        "webapp.backend.worker.run_target_switching_pipeline_isolated",
        new=pipeline_mock,
    ), patch(
        "webapp.backend.worker._target_switching_production_gate", return_value=True,
    ), patch(
        "webapp.backend.worker.run_target_switching_analysis", switching_mock,
    ), patch(
        "webapp.backend.worker.run_native_analysis", native_mock,
    ), patch(
        "webapp.backend.history_trends.matched_target_switching_baseline_for_user",
        new=baseline_mock,
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=commit,
    ):
        assert await worker.process_one() is True

    result = completed[0]
    assert len(committed) == 1
    pipeline_mock.assert_awaited_once_with(job)
    assert committed[0]["visual_result"] == visual_result
    assert result["analysis_version"] == expected_version
    assert result["analysis_type"] == "target_switching"
    native_mock.assert_not_called()
    if quality_enabled:
        switching_mock.assert_called_once_with(
            job, visual_result, episode_result, frozen_stats,
        )
        assert result["deterministic"]["metrics"][
            "target_switching.transition_time_ms"
        ]["value"] == 120.0
        assert result["scenario"]["analyzer_refs"] == ["target_switching.v1"]
        assert committed[0]["switching_result"]["analysis_version"] == (
            "target_switching.v1"
        )
        assert "processed_rows" not in json.dumps(result)
    else:
        switching_mock.assert_not_called()
        baseline_mock.assert_not_called()
        assert result["deterministic"]["metrics"] == {}
        assert result["deterministic"]["limitations"] == [
            "target_switching_visual_quality_unavailable"
        ]
        assert committed[0]["switching_result"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["scenario_profile_ref", "scenario_hash"])
async def test_process_one_rejects_forged_active_resolution_before_family_analyzer(
    missing_field: str,
):
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
    )
    snapshot["scenario_resolution"][missing_field] = None
    job = {
        "id": 110,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    _error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_v2_adapter_result(),
    )

    native_mock.assert_not_called()
    cv_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_version", ["analysis_input_snapshot.v4", None])
async def test_process_one_unknown_snapshot_version_cannot_enter_legacy_flicking(
    snapshot_version: str | None,
):
    snapshot = _native_v2_snapshot()
    if snapshot_version is None:
        snapshot.pop("schema_version")
    else:
        snapshot["schema_version"] = snapshot_version
    job = {
        "id": 111,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    _error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_v2_adapter_result(),
    )

    native_mock.assert_not_called()
    cv_mock.assert_not_called()


@pytest.mark.asyncio
async def test_native_retry_reuses_the_same_frozen_canonical_window():
    snapshot = _native_v2_snapshot()
    sid = await queue.enqueue(
        "u1",
        "",
        "",
        input_mode="input_native",
        input_snapshot=snapshot,
    )

    first = await queue.claim_next("first-worker")
    assert first is not None
    assert first["input_snapshot"]["canonical_time_window"] == (
        snapshot["canonical_time_window"]
    )
    assert await queue.mark_failed(sid, "retryable failure", worker_id="first-worker")
    await queue.requeue_for_retry(sid)

    second = await queue.claim_next("second-worker")
    assert second is not None
    assert second["input_snapshot"] == first["input_snapshot"]


@pytest.mark.asyncio
async def test_process_one_multimodal_keeps_native_result_when_video_cv_fails(tmp_path: Path):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    video_fingerprint = snapshot["sources"]["video"]["fingerprint"]
    job = {
        "id": 102,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    native_result = _native_adapter_result()
    parsed_stats = object()
    result, calls, _native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=native_result,
        parsed_stats=parsed_stats,
        cv_error=RuntimeError("video decoder unavailable"),
    )

    assert calls == ["native", "cv"]
    cv_mock.assert_called_once_with(
        str(managed_video),
        "/db-private/runs/42/stats.csv",
        None,
        None,
        stats=parsed_stats,
    )
    assert result["schema_version"] == "analysis_result.v2"
    assert result["analysis_version"] == "native_flicking.v1"
    assert result["owner_id"] == "u1"
    assert result["input_mode"] == "multimodal"
    assert result["deterministic"]["status"] == "available"
    assert result["deterministic"]["metrics"] == native_result["deterministic"]["metrics"]
    assert result["deterministic"]["timeline"] == native_result["deterministic"]["timeline"]
    assert result["evidence"]["availability"]["mp4"] == "unavailable"
    assert result["evidence"]["sources"]["mp4"]["parser_or_format_version"] == "mp4"
    video_artifact = next(
        entry for entry in result["artifact_manifest"]["external_inputs"]
        if entry["kind"] == "mp4"
    )
    assert video_artifact["ownership"] == "analysis"
    assert video_artifact["managed"] is True
    assert video_artifact["local_only"] is True
    assert video_artifact["format_version"] == "mp4"
    assert video_artifact["checksum"] == video_fingerprint["sha256"]
    assert result["input_snapshot"]["sources"]["video"] == {
        "artifact_ref": "analysis:102:video",
        "basename": source_video.name,
        "fingerprint": video_fingerprint,
        "availability": "available",
        "format_version": "mp4",
    }
    assert result["warnings"] == [
        {"code": "video_cv_unavailable"},
        {"code": "legacy_static_compatibility"},
    ]
    assert result["deterministic"]["diagnosis"]["meta"]["input_mode"] == "multimodal"
    assert "target_relative_error" not in result["deterministic"]["metrics"]
    assert "overshoot_distance" not in result["deterministic"]["metrics"]


@pytest.mark.asyncio
async def test_v2_multimodal_does_not_run_visual_analyzer_without_reviewed_profile(
    tmp_path: Path,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 108,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, _native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_v2_adapter_result(),
        parsed_stats=object(),
    )

    assert calls == ["native"]
    cv_mock.assert_not_called()
    assert result["evidence"]["availability"]["mp4"] == "available"
    assert result["deterministic"]["visual_validation"] == {
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
        "limitations": ["visual_quality_profile_unavailable"],
    }
    assert result["warnings"] == [
        {"code": "video_cv_unavailable"},
        {"code": "legacy_static_compatibility"},
    ]


def test_visual_preprocessing_uses_only_registered_run_owned_video(
    tmp_path: Path,
):
    from kovaak_tracker.visual_signals import (
        VISUAL_PRODUCER_ID,
        VISUAL_PRODUCER_VERSION,
        VisualPreprocessingUnavailable,
        visual_detector_config_ref_v1,
    )

    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
    )
    video = _video_source(source_video)
    video.update({
        "ownership": "run",
        "artifact_ref": "run:42:video:fixturedigest",
    })
    snapshot["sources"]["video"] = video
    job = {
        "id": 113,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "fov": 55.0,
    }
    detector_config = {
        "schema_version": "visual_target_detector.v2",
        "aim_point_mode": "fixed_viewport_center",
        "excluded_regions": [],
        "target": {
            "hsv_lower": [170, 180, 180],
            "hsv_upper": [10, 255, 255],
            "min_area": 50,
            "max_area_ratio": 0.05,
            "shape": "round",
        },
    }
    detector_config_ref = visual_detector_config_ref_v1(detector_config)
    producer = {
        "detector_config_ref": detector_config_ref,
        "visual_quality_profile": {
            "status": "accepted",
            "producer_id": VISUAL_PRODUCER_ID,
            "producer_version": VISUAL_PRODUCER_VERSION,
            "calibration_context": {
                "detector_config_ref": detector_config_ref,
            },
        },
        "detector_config": detector_config,
    }
    expected = {"safe_summary": {"status": "available"}}
    parsed_stats = MagicMock()
    parsed_stats.resolution = "1920x1080"
    parsed_stats.fov = 103.0

    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), patch(
        "kovaak_tracker.visual_signals.preprocess_visual_video_v1",
        return_value=expected,
    ) as preprocess:
        assert worker.run_visual_preprocessing(job, parsed_stats=parsed_stats) == expected

    assert preprocess.call_args.kwargs == {
        "media_path": str(managed_video),
        "analysis_ref": "analysis:113",
        "canonical_time_window": snapshot["canonical_time_window"],
        "visual_quality_profile": producer["visual_quality_profile"],
        "visual_runtime_selector": {
            "schema_version": "visual_runtime_selector.v1",
            "scenario_hash": "fixture-hash",
            "resolution": [1920, 1080],
            "canonical_video_mapping_version": "visual_video_time_mapping.v1",
            "fov": 103.0,
        },
        "video_time_mapping": {
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": snapshot["canonical_time_window"]["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": "time_alignment.v2",
        },
        "detector_config": producer["detector_config"],
        "source_ref": "run:42:video:fixturedigest",
    }

    snapshot["sources"]["video"]["ownership"] = "analysis"
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable) as failure:
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)

    assert failure.value.code == "visual_quality_profile_unavailable"

    snapshot["sources"]["video"]["ownership"] = "run"
    snapshot["schema_version"] = "analysis_input_snapshot.v2"
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"]["scenario_hash"] = None
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)

    snapshot["scenario_resolution"]["scenario_hash"] = "fixture-hash"
    parsed_stats.resolution = "invalid"
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)

    parsed_stats.resolution = "1920x1080"
    producer["detector_config"]["target"]["hsv_upper"][2] = 254
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)

    producer["detector_config"]["target"]["hsv_upper"][2] = 255
    producer["visual_quality_profile"]["producer_version"] = (
        "visual_round_detector.circularity_0_60.v2"
    )
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)

    producer["visual_quality_profile"]["producer_version"] = VISUAL_PRODUCER_VERSION
    producer["detector_config_ref"] = "detector-config:other.v1"
    with patch.dict(
        worker._REVIEWED_VISUAL_PRODUCERS,
        {"scenario:static.fixture@1": producer},
        clear=True,
    ), pytest.raises(VisualPreprocessingUnavailable):
        worker.run_visual_preprocessing(job, parsed_stats=parsed_stats)


def test_reviewed_single_target_tracking_profile_uses_reviewed_legacy_csrt_producer(
    tmp_path: Path,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
        aim_family="continuous_tracking",
        allowed_analyzers=["continuous_tracking.v1"],
    )
    snapshot["scenario_resolution"].update({
        "scenario_hash": "b2ae4a24b710e36afc6e57c61f590ab4",
        "display_name": "WHJ SmoothStrafeSphere Easy",
        "scenario_profile_ref": "scenario:tracking.whj_smooth_strafe_sphere_easy@1",
        "target_motion": {"model": "predictable", "target_count_model": "single"},
        "allowed_metric_families": ["continuous_tracking"],
    })
    video = _video_source(source_video)
    video.update({
        "ownership": "run",
        "artifact_ref": "run:42:video:fixturedigest",
    })
    snapshot["sources"]["video"] = video
    job = {
        "id": 114,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "fov": None,
    }
    parsed_stats = MagicMock()
    parsed_stats.resolution = "1920x1080"
    parsed_stats.fov = None
    expected = {"safe_summary": {"status": "available"}}

    with patch(
        "kovaak_tracker.visual_signals.preprocess_visual_video_single_target_csrt_v1",
        return_value=expected,
    ) as single_target_csrt, patch(
        "kovaak_tracker.visual_signals.preprocess_visual_video_temporal_v1",
    ) as temporal, patch(
        "kovaak_tracker.visual_signals.preprocess_visual_video_v1",
    ) as non_temporal:
        assert worker.run_visual_preprocessing(job, parsed_stats=parsed_stats) == expected

    single_target_csrt.assert_called_once()
    temporal.assert_not_called()
    non_temporal.assert_not_called()
    quality_profile = single_target_csrt.call_args.kwargs["visual_quality_profile"]
    assert quality_profile["validation_results"] == {
        "center_error_median_px": 3.28,
        "center_error_p95_px": 6.03,
        "false_positive_rate": 0.0,
        "identity_switch_rate": 0.0,
        "minimum_coverage": 1.0,
        "occlusion_reentry_accuracy": None,
        "radius_or_hitbox_error_px": 1.0,
    }
    assert "occlusion_reentry_accuracy" not in quality_profile[
        "required_quality_fields_by_metric_family"
    ]["tracking"]
    assert any(
        "Occlusion re-entry was not observed" in limitation
        for limitation in quality_profile["limitations"]
    )
    assert single_target_csrt.call_args.kwargs["visual_runtime_selector"] == {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "b2ae4a24b710e36afc6e57c61f590ab4",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": None,
    }
    assert single_target_csrt.call_args.kwargs["detector_config"]["target"] == {
        "hsv_lower": [0, 0, 0],
        "hsv_upper": [179, 255, 80],
        "min_area": 50,
        "max_area_ratio": 0.05,
        "shape": "round",
    }


def test_reviewed_dynamic_profile_uses_exact_split_quality_and_hud_mask():
    producer = worker._REVIEWED_VISUAL_PRODUCERS[
        "scenario:dynamic.pasu_small_reload@1"
    ]
    assert producer["detector_config_ref"] == (
        "detector-config:sha256:"
        "4ab84f03e409d95af53c273253ffca6c778bd908e1a33694ec5423be27923876"
    )
    assert producer["detector_config"]["excluded_regions"] == [
        [0.0, 0.0, 0.14, 0.08],
        [0.44, 0.0, 0.56, 0.12],
        [0.85, 0.08, 1.0, 0.17],
        [0.385, 0.765, 0.615, 1.0],
    ]
    profile = producer["visual_quality_profile"]
    assert profile["status"] == "accepted"
    assert profile["validated_metric_families"] == ["dynamic_clicking"]
    assert profile["validated_selectors"] == [{
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "a37d2ba4f3f33d59ae7018e37445a5e9",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }]
    assert profile["validation_results"] == {
        "center_error_median_px": 1.032295,
        "center_error_p95_px": 3.519083,
        "false_positive_rate": 0.0,
        "identity_switch_rate": None,
        "minimum_coverage": 0.992,
        "occlusion_reentry_accuracy": None,
        "radius_or_hitbox_error_px": 0.749257,
    }
    assert profile["required_quality_fields_by_metric_family"] == {
        "dynamic_clicking": [
            "center_error_median_px",
            "center_error_p95_px",
            "false_positive_rate",
            "minimum_coverage",
            "radius_or_hitbox_error_px",
        ],
    }
    assert "identity_continuity_not_observed" in profile["limitations"]
    assert "holdout_small_target_area_99_below_min_area_100" in profile[
        "limitations"
    ]


def test_profile_contribution_uses_only_supported_evidence_backed_metrics():
    metric_key = "continuous_tracking.target_relative_error_px"
    result = {
        "schema_version": "analysis_result.v2",
        "analysis_version": "continuous_tracking.v1",
        "analysis_type": "continuous_tracking",
        "scenario": {
            "scenario_profile_ref": "scenario:tracking.fixture@1",
            "aim_family": "continuous_tracking",
            "analyzer_refs": ["continuous_tracking.v1"],
            "support_status": "supported",
        },
        "input_snapshot": {
            "scenario_resolution": {
                "scenario_profile_ref": "scenario:tracking.fixture@1",
                "aim_family": "continuous_tracking",
                "allowed_analyzers": ["continuous_tracking.v1"],
                "allowed_metric_families": ["continuous_tracking"],
            },
        },
        "deterministic": {
            "support_status": "supported",
            "metrics": {
                metric_key: {
                    "value": 8.0,
                    "unit": "px",
                    "availability": "available",
                    "classification": "deterministic",
                    "provenance": {"kind": "derived", "sources": ["run:42"]},
                    "metric_version": f"{metric_key}.v1",
                    "coverage": 1.0,
                    "limitations": [],
                },
                "continuous_tracking.phase_lag_ms": {
                    "value": 12.0,
                    "unit": "ms",
                    "availability": "available",
                    "classification": "model_inferred",
                    "provenance": {"kind": "inferred", "sources": ["run:42"]},
                    "metric_version": "continuous_tracking.phase_lag_ms.v1",
                    "coverage": 1.0,
                    "limitations": [],
                },
            },
        },
        "evidence": {"derived_artifact": {"artifact_ref": "analysis:1:evidence:abc"}},
    }

    payload = worker._build_profile_contribution_payload(result)

    assert payload == {
        "schema_version": "profile_contribution.v1",
        "source_kind": "deterministic",
        "dimensions": [{
            "dimension_key": metric_key,
            "scope": "exact_scenario",
            "scenario_profile_ref": "scenario:tracking.fixture@1",
            "metric_ref": f"metric:{metric_key}@{metric_key}.v1",
            "metric_value": 8.0,
            "unit": "px",
            "expected_direction": "lower_better",
            "confidence": "high",
            "comparability": "comparable",
            "supporting_metric_refs": [f"metric:{metric_key}@{metric_key}.v1"],
            "counterexample_refs": [],
            "candidate_hypothesis_refs": [],
        }],
    }
    result["scenario"]["support_status"] = "outcome_only"
    assert worker._build_profile_contribution_payload(result) is None


@pytest.mark.asyncio
async def test_v3_multimodal_commits_validated_visual_result_before_terminal_write(
    tmp_path: Path,
):
    from kovaak_tracker.visual_signals import (
        build_visual_quality_profile_v2,
        preprocess_visual_signals_v1,
    )

    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_v2_snapshot()
    snapshot["schema_version"] = "analysis_input_snapshot.v3"
    snapshot["scenario_resolution"] = _scenario_resolution(
        manifest_status="active",
        dispatch="allowed",
    )
    snapshot["sources"]["video"] = _video_source(source_video)
    window = snapshot["canonical_time_window"]
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": "fixture-hash",
        "resolution": [1920, 1080],
        "canonical_video_mapping_version": "visual_video_time_mapping.v1",
        "fov": 103.0,
    }
    profile = build_visual_quality_profile_v2(
        producer_id="fixture_detector",
        producer_version="fixture_detector.v1",
        annotation_set_ref="annotation-set:fixture.v1",
        annotation_protocol_version="visual_annotation_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": "detector-config:fixture.v1",
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["fixture"],
            "annotated_target_appearance_labels": ["sphere"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            "tracking": [
                "scenario_hash", "resolution", "canonical_video_mapping_version",
            ],
        },
        required_quality_fields_by_metric_family={
            "tracking": [
                "center_error_median_px", "center_error_p95_px",
                "radius_or_hitbox_error_px", "false_positive_rate",
                "identity_switch_rate", "occlusion_reentry_accuracy",
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
            "minimum_coverage": 0.9,
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
        validated_metric_families=["tracking"],
        status="accepted",
        limitations=[],
    )
    visual = preprocess_visual_signals_v1(
        analysis_ref="analysis:112",
        canonical_time_window=window,
        frame_observations=[{
            "source_pts_ms": 0,
            "crosshair": {"x": 100.0, "y": 100.0},
            "targets": [{
                "detector_ref": "target-1",
                "x": 110.0,
                "y": 100.0,
                "visible_radius": 12.0,
                "confidence": 1.0,
            }],
            "scene": "gameplay",
        }],
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping={
            "schema_version": "visual_video_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": window["start_ms"],
            "mapping_method": "run_owned_exact_canonical_clip",
            "timebase_version": window["timebase_version"],
        },
    )
    job = {
        "id": 112,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    completed: list[dict] = []
    committed_visual: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def commit(_job, result, **kwargs):
        committed_visual.append(kwargs["visual_result"])
        return result

    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done)), \
         patch(
             "webapp.backend.worker.run_native_analysis",
             return_value=(_native_v2_adapter_result(), object()),
         ), \
         patch(
             "webapp.backend.worker.run_visual_preprocessing_isolated",
             new=AsyncMock(return_value=visual),
         ), \
         patch("webapp.backend.worker._maybe_commit_analysis_evidence", side_effect=commit):
        assert await worker.process_one() is True

    assert committed_visual == [visual]
    assert completed[0]["deterministic"]["visual_validation"] == visual["safe_summary"]
    assert "local_samples" not in json.dumps(completed[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
@pytest.mark.parametrize("damage", ["missing", "truncated", "replaced"])
async def test_process_one_run_based_mode_rejects_invalid_managed_video_before_cv(
    tmp_path: Path,
    caplog,
    input_mode: str,
    damage: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    if damage == "truncated":
        managed_video.write_bytes(b"frozen")
    elif damage == "replaced":
        managed_video.write_bytes(b"x" * source_video.stat().st_size)

    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 201,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
    )

    assert error == {
        "schema_version": "error.v1",
        "category": "input_validation",
        "code": "source_unavailable",
        "message": "分析输入源已不可用或已变更，请重新提交分析。",
        "retryable": False,
        "trace_id": None,
        "details": None,
    }
    native_mock.assert_not_called()
    cv_mock.assert_not_called()
    assert str(source_video) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_video) not in json.dumps(error, ensure_ascii=False)
    assert str(source_video) not in caplog.text
    assert str(managed_video) not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
@pytest.mark.parametrize("missing", ["snapshot", "video", "fingerprint"])
async def test_process_one_run_based_mode_requires_frozen_video_identity(
    tmp_path: Path,
    input_mode: str,
    missing: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    if missing == "snapshot":
        input_snapshot = None
    else:
        input_snapshot = snapshot
        if missing == "video":
            snapshot["sources"].pop("video")
        elif missing == "fingerprint":
            snapshot["sources"]["video"].pop("fingerprint")
    job = {
        "id": 204,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": input_snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
    )

    native_mock.assert_not_called()
    cv_mock.assert_not_called()
    assert error["category"] == "input_validation"
    assert error["code"] == "source_unavailable"
    assert error["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
async def test_process_one_rejects_managed_video_changed_during_cv(
    tmp_path: Path,
    input_mode: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 202,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    def mutate_video(*_args, **_kwargs):
        managed_video.write_bytes(b"changed-during-cv")
        return {}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}

    provider_lookup = AsyncMock(return_value=None)
    with patch(
        "webapp.backend.provider_store.get_default_runtime_profile",
        new=provider_lookup,
    ), patch(
        "webapp.backend.worker.run_report",
        return_value={"diagnosis": {}, "narration": None, "notes": []},
    ) as report_mock:
        error, _native_mock, cv_mock = await _capture_mode_failure(
            job,
            native_result=_native_adapter_result(),
            cv_side_effect=mutate_video,
        )

    cv_mock.assert_called_once()
    assert error["category"] == "input_validation"
    assert error["code"] == "source_unavailable"
    assert error["retryable"] is False
    if input_mode == "video_fallback":
        provider_lookup.assert_not_awaited()
        report_mock.assert_not_called()


@pytest.mark.asyncio
async def test_video_fallback_cv_failure_does_not_log_input_paths(
    tmp_path: Path,
    caplog,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    managed_stats = tmp_path / "managed-stats.csv"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    managed_stats.write_text("stats")
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 206,
        "user_id": "u1",
        "input_mode": "video_fallback",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(managed_stats),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    private_message = f"decoder failed video={managed_video} csv={managed_stats}"

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
        cv_side_effect=RuntimeError(private_message),
    )

    native_mock.assert_not_called()
    cv_mock.assert_called_once()
    assert error["category"] == "internal_unknown"
    assert error["code"] == "analysis_failed"
    assert error["retryable"] is True
    assert str(managed_video) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_stats) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_video) not in caplog.text
    assert str(managed_stats) not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_process_one_accepts_run_owned_video_without_mtime_fingerprint(
    tmp_path: Path,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    source = _video_source(source_video)
    source_mtime_ns = source["fingerprint"].pop("mtime_ns")
    managed_stat = managed_video.stat()
    os.utime(
        managed_video,
        ns=(managed_stat.st_atime_ns, source_mtime_ns + 2_000_000_000),
    )
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = source
    job = {
        "id": 203,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, _native_mock, _cv_mock = await _capture_mode_result(
        job,
        native_result=_native_adapter_result(),
        parsed_stats=object(),
        cv_result=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    )

    assert calls == ["native", "cv"]
    assert result["input_mode"] == "multimodal"
    assert result["evidence"]["availability"]["mp4"] == "available"


@pytest.mark.asyncio
async def test_managed_video_verification_does_not_block_heartbeat():
    verification_started = threading.Event()
    verification_finished = threading.Event()
    release_verification = threading.Event()
    heartbeat_seen = asyncio.Event()
    verification_calls = 0

    def blocking_verifier(_job, _input_mode):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            verification_started.set()
            try:
                release_verification.wait(timeout=1)
            finally:
                verification_finished.set()

    async def heartbeat(_sid, _worker_id):
        heartbeat_seen.set()
        return True

    job = {
        "id": 205,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": _native_snapshot(),
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    process_task = None
    try:
        with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
             patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
             patch("webapp.backend.queue.heartbeat", new=AsyncMock(side_effect=heartbeat)), \
             patch("webapp.backend.queue.mark_done", new=AsyncMock(return_value=True)), \
             patch("webapp.backend.worker.run_native_analysis", return_value=_native_adapter_result()), \
             patch(
                 "webapp.backend.worker._assert_managed_video_matches_snapshot",
                 side_effect=blocking_verifier,
             ):
            process_task = asyncio.create_task(worker.process_one())
            assert await asyncio.to_thread(verification_started.wait, 2)
            assert not verification_finished.is_set()
            await asyncio.wait_for(heartbeat_seen.wait(), timeout=1)
            assert not release_verification.is_set()
            release_verification.set()
            assert await process_task is True
    finally:
        release_verification.set()
        if process_task is not None and not process_task.done():
            await process_task

    assert verification_calls == 2


@pytest.mark.asyncio
async def test_process_one_multimodal_reuses_stats_frozen_by_native_analysis(
    tmp_path: Path,
):
    parsed_stats = object()
    snapshot = _native_snapshot()
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 104,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    cv_result = (
        {},
        {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
    )

    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_adapter_result(),
        parsed_stats=parsed_stats,
        cv_result=cv_result,
    )

    assert calls == ["native", "cv"]
    assert native_mock.call_args.kwargs["return_parsed_stats"] is True
    assert cv_mock.call_args.kwargs["stats"] is parsed_stats
    assert result["input_mode"] == "multimodal"
    assert result["deterministic"]["metrics"] == _native_adapter_result()["deterministic"]["metrics"]
    assert result["deterministic"]["timeline"] == _native_adapter_result()["deterministic"]["timeline"]
    assert result["deterministic"]["visual_validation"] == {
        "status": "available",
        "timeline": [],
    }
    assert result["evidence"]["availability"]["mp4"] == "available"
    assert result["warnings"] == [{"code": "legacy_static_compatibility"}]
    assert result["deterministic"]["diagnosis"]["meta"]["input_mode"] == "multimodal"


@pytest.mark.asyncio
async def test_process_one_video_fallback_writes_v2_without_raw_provenance():
    completed: list[dict] = []
    job = {
        "id": 103,
        "user_id": "u1",
        "input_mode": "video_fallback",
        "kovaak_run_id": None,
        "input_snapshot": None,
        "video_path": "/managed/session/video.mp4",
        "csv_path": "/managed/session/stats.csv",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-07-13 12:00:00",
    }

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done)), \
         patch("webapp.backend.worker.run_native_analysis") as native_mock, \
         patch(
             "webapp.backend.worker.run_analysis",
             return_value=(
                 {"a": {"med": 1}, "sparc": {"med": -1.2}},
                 {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
             ),
         ), \
         patch(
             "webapp.backend.worker.run_report",
             return_value={"diagnosis": {}, "narration": None, "notes": []},
         ):
        assert await worker.process_one() is True

    assert len(completed) == 1
    result = completed[0]
    native_mock.assert_not_called()
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_version"] == "flicking_fair_summary.v1"
    assert result["owner_id"] == "u1"
    assert result["input_mode"] == "video_fallback"
    assert result["input_snapshot"]["calibration"] == {
        "cm_per_360": {"value": 30.0, "source": "manual_override"},
        "fov": {"value": 90.0, "source": "manual_override"},
    }
    assert "kovaak_run_ref" not in result
    assert "raw_input" not in result["evidence"]["sources"]
    assert result["evidence"]["availability"] == {"stats": "available", "mp4": "available"}
    assert result["evidence"]["coverage"] is None
    metric = result["deterministic"]["metrics"]["a"]
    assert metric["key"] == "a"
    assert metric["value"] == 1
    assert metric["unit"] == "unknown"
    assert metric["provenance"] == {"kind": "fused", "sources": ["mp4", "stats"]}
    assert metric["metric_version"] == "flicking_fair_summary.v1"
    assert result["deterministic"]["metrics"]["sparc"]["metric_version"] == (
        "flicking_fair_summary.sparc.v2"
    )
    assert metric["coverage"] is None
    assert "coverage_not_recorded" in metric["limitations"]
    assert "unit_not_registered" in metric["limitations"]
    assert all(
        {
            "source", "availability", "ownership", "managed", "local_only",
            "status", "derived_from",
        } <= set(entry)
        for entry in [
            *result["artifact_manifest"]["external_inputs"],
            *result["artifact_manifest"]["owned_outputs"],
        ]
    )
    assert "/managed" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["v1", "legacy"])
async def test_get_session_still_reads_v1_and_unversioned_legacy_results(
    result_kind: str,
):
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    if result_kind == "v1":
        stored_result = build_analysis_result_v1(
            report={"diagnosis": {"ok": True}, "figures": {}, "notes": [], "narration": None},
            timeline=[],
            narration_status="not_requested",
            cm_per_360=None,
            fov=None,
            artifact_manifest={
                "schema_version": "artifact_manifest.v1",
                "inputs": [],
                "outputs": [],
            },
            created_at="2026-07-13T12:00:00Z",
            completed_at="2026-07-13T12:01:00Z",
        )
    else:
        stored_result = {
            "diagnosis": {"ok": True},
            "figures": {},
            "notes": [],
            "timeline": [],
        }
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(stored_result), sid),
    )
    await conn.commit()

    session = await queue.get_session(sid)

    assert session is not None
    assert session["result"]["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    if result_kind == "v1":
        assert session["result"] == stored_result
    else:
        assert session["result"]["analysis_version"] == LEGACY_ANALYSIS_VERSION


def test_native_result_marks_desktop_owner_as_local_profile():
    from webapp.backend import config

    result = worker._build_native_result_v2(
        {
            "id": 104,
            "user_id": config.DESKTOP_LOCAL_PROFILE,
            "input_mode": "input_native",
            "kovaak_run_id": 42,
            "input_snapshot": _native_snapshot(),
        },
        _native_adapter_result(),
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:00:01Z",
    )

    assert result["owner_id"] == config.DESKTOP_LOCAL_PROFILE
    assert result["local_profile"] == config.DESKTOP_LOCAL_PROFILE


def test_worker_maps_frozen_raw_stats_and_reviewed_tracks_into_outcome_producer():
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 1_000,
        "end_ms": 3_000,
        "duration_ms": 2_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    job = {
        "id": 700,
        "input_snapshot": {
            "canonical_time_window": window,
            "trace": {"artifact_ref": "run:7:trace:abc"},
            "sources": {
                "stats": {
                    "artifact_ref": "run:7:stats:abc",
                    "parser_version": "kovaak_stats.v1",
                },
                "video": {"artifact_ref": "run:7:video:abc"},
            },
            "scenario_resolution": {
                "scenario_profile_ref": "scenario-profile:fixture@1",
            },
        },
    }
    parsed_stats = MagicMock()
    parsed_stats.kills = pd.DataFrame([{
        "Kill #": 1,
        "time_s": 1.01,
        "Shots": 1,
        "Hits": 1,
        "OverShots": 0,
    }])
    visual = {
        "analysis_ref": "analysis:700",
        "canonical_time_window": window,
        "quality": {"status": "accepted"},
        "visual_quality_profile_ref": "visual-quality-profile:fixture@1",
        "visual_runtime_selector": {"resolution": [200, 200]},
        "local_samples": {
            "target.1.position": [{
                "canonical_time_ms": 1_995,
                "x": 102.0,
                "y": 100.0,
                "visible_radius": 10.0,
                "confidence": 1.0,
            }],
        },
        "track_summaries": [{
            "track_ref": "analysis:700:target-track:1",
            "identity_source": "detector_ref",
            "limitations": [],
        }],
    }
    raw_points = [
        {"timestamp_ms": 1_990, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": 2_000, "dx": 0, "dy": 0, "buttons": 1},
        {"timestamp_ms": 2_005, "dx": 0, "dy": 0, "buttons": 1},
    ]
    registry = {
        "schema_version": "outcome_association_rule_registry.v1",
        "registry_version": "fixture.v1",
        "entries": [{"status": "active", "binding": {}}],
    }
    event_bundle = {"schema_version": "event_bundle.v2"}
    captured = {}

    def associate(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "outcome_association_result.v1",
            "status": "available",
            "event_bundle": event_bundle,
            "limitations": [],
        }

    with patch(
        "kovaak_tracker.outcome_association.load_outcome_association_rule_registry_v1",
        return_value=registry,
    ), patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        return_value=b"trace",
    ), patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=raw_points,
    ), patch(
        "kovaak_tracker.outcome_association.associate_one_shot_kills_v1",
        side_effect=associate,
    ):
        assert worker._build_validated_outcome_association(
            job, parsed_stats, visual,
        ) is event_bundle

    assert captured["click_events"] == [
        {"event_ref": "analysis:700:event:raw-shot:1", "time_ms": 2_000},
    ]
    assert captured["stats_kills"] == [{
        "event_ref": "analysis:700:event:stats-kill:1",
        "time_ms": 2_010,
        "kill_index": 1,
        "shots": 1,
        "hits": 1,
        "overshots": 0,
    }]
    assert captured["stats_parser_version"] == "kovaak_stats.v1"
    assert captured["target_tracks"][0]["identity_status"] == "stable"


def test_worker_does_not_read_private_sources_when_rule_registry_is_empty():
    with patch(
        "kovaak_tracker.outcome_association.load_outcome_association_rule_registry_v1",
        return_value={
            "schema_version": "outcome_association_rule_registry.v1",
            "registry_version": "fixture.v1",
            "entries": [],
        },
    ), patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        side_effect=AssertionError("private source should not be read"),
    ):
        assert worker._build_validated_outcome_association({}, object(), {}) is None


def test_raw_click_edge_requires_observed_release_before_press():
    held_at_boundary = [
        {"timestamp_ms": 1_000, "buttons": 1},
        {"timestamp_ms": 1_010, "buttons": 0},
    ]
    assert worker._raw_left_button_rising_edges(
        held_at_boundary,
        analysis_ref="analysis:1",
        start_ms=1_000,
        end_ms=2_000,
    ) == []

    observed_edge = [
        {"timestamp_ms": 1_000, "buttons": 0},
        {"timestamp_ms": 1_010, "buttons": 1},
    ]
    assert worker._raw_left_button_rising_edges(
        observed_edge,
        analysis_ref="analysis:1",
        start_ms=1_000,
        end_ms=2_000,
    ) == [{
        "event_ref": "analysis:1:event:raw-shot:1",
        "time_ms": 1_010,
    }]
