from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_VERSION,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    SUMMARY_TYPE,
    UnsupportedContractVersion,
    analysis_result_to_coach_report,
    build_analysis_result_v1,
    build_artifact_manifest_v1,
    build_error_v1,
    coerce_analysis_result_v1,
    coerce_error_v1,
    dump_contract_json,
    normalize_json_value,
)


def _minimal_manifest() -> dict:
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "inputs": [],
        "outputs": [],
    }


def test_build_analysis_result_v1_exact_shape():
    report = {
        "diagnosis": {"summary": {"sparc": {"med": -7.0}}},
        "figures": {"fig1": {"data": []}},
        "notes": ["note-a"],
        "narration": "讲解文本",
    }
    timeline = [{"frame": 1, "time_s": 0.1, "type": "peak", "label": "p"}]
    manifest = _minimal_manifest()
    result = build_analysis_result_v1(
        report=report,
        timeline=timeline,
        narration_status="available",
        cm_per_360=34.5,
        fov=103.0,
        artifact_manifest=manifest,
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )
    assert result["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert result["analysis_version"] == ANALYSIS_VERSION
    assert result["summary_type"] == SUMMARY_TYPE
    assert result["created_at"] == "2026-07-10T12:00:00Z"
    assert result["completed_at"] == "2026-07-10T12:01:00Z"
    assert result["input"] == {"cm_per_360": 34.5, "fov": 103.0}
    assert result["deterministic"]["diagnosis"] == report["diagnosis"]
    assert result["deterministic"]["figures"] == report["figures"]
    assert result["deterministic"]["timeline"] == timeline
    assert result["narration"] == {
        "status": "available",
        "text": "讲解文本",
        "provider": None,
        "model": None,
        "usage": None,
    }
    assert result["artifact_manifest"] == manifest
    assert result["notes"] == ["note-a"]
    assert result["normalization_issues"] == []


def test_non_finite_numbers_become_null_with_issue_paths():
    report = {
        "diagnosis": {"summary": {"sparc": {"med": float("nan")}}},
        "figures": {},
        "notes": [],
        "narration": None,
    }
    result = build_analysis_result_v1(
        report=report,
        timeline=[],
        narration_status="not_requested",
        cm_per_360=float("inf"),
        fov=None,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )
    assert result["deterministic"]["diagnosis"]["summary"]["sparc"]["med"] is None
    assert result["input"]["cm_per_360"] is None
    issues = result["normalization_issues"]
    paths = {i["path"] for i in issues}
    assert "$.deterministic.diagnosis.summary.sparc.med" in paths
    assert "$.input.cm_per_360" in paths
    for issue in issues:
        assert issue["code"] == "non_finite_number"
        assert issue["original"] in ("nan", "+infinity", "-infinity")


def test_dump_contract_json_rejects_remaining_non_json_values():
    normalized, _ = normalize_json_value({"ok": 1})
    dump_contract_json(normalized)
    with pytest.raises((TypeError, ValueError)):
        dump_contract_json({"bad": {1, 2}})


def test_legacy_report_is_wrapped_without_db_rewrite():
    legacy = {
        "diagnosis": {"x": 1},
        "figures": {"f": {}},
        "narration": "旧讲解",
        "notes": ["n1"],
        "timeline": [{"frame": 0, "time_s": 0.0, "type": "kill", "label": "k"}],
    }
    wrapped = coerce_analysis_result_v1(
        legacy,
        cm_per_360=30.0,
        fov=90.0,
        created_at="2026-07-09T10:00:00Z",
        updated_at="2026-07-09T10:05:00Z",
    )
    assert wrapped is not None
    assert wrapped["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert wrapped["analysis_version"] == LEGACY_ANALYSIS_VERSION
    assert wrapped["summary_type"] == SUMMARY_TYPE
    assert wrapped["input"] == {"cm_per_360": 30.0, "fov": 90.0}
    assert wrapped["deterministic"]["diagnosis"] == {"x": 1}
    assert wrapped["deterministic"]["timeline"] == legacy["timeline"]
    assert wrapped["narration"]["status"] == "available"
    assert wrapped["narration"]["text"] == "旧讲解"
    assert wrapped["artifact_manifest"]["schema_version"] == ARTIFACT_MANIFEST_SCHEMA_VERSION
    assert wrapped["artifact_manifest"]["inputs"] == []
    assert legacy == {
        "diagnosis": {"x": 1},
        "figures": {"f": {}},
        "narration": "旧讲解",
        "notes": ["n1"],
        "timeline": [{"frame": 0, "time_s": 0.0, "type": "kill", "label": "k"}],
    }


def test_unknown_schema_version_is_rejected():
    with pytest.raises(UnsupportedContractVersion):
        coerce_analysis_result_v1({"schema_version": "analysis_result.v99", "deterministic": {}})


def test_analysis_result_to_coach_report_restores_internal_shape():
    v1 = build_analysis_result_v1(
        report={
            "diagnosis": {"d": 1},
            "figures": {"g": 2},
            "notes": ["a"],
            "narration": "txt",
        },
        timeline=[{"frame": 5, "time_s": 0.2, "type": "miss", "label": "m"}],
        narration_status="available",
        cm_per_360=None,
        fov=None,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )
    coach = analysis_result_to_coach_report(v1)
    assert coach == {
        "diagnosis": {"d": 1},
        "figures": {"g": 2},
        "narration": "txt",
        "notes": ["a"],
        "timeline": [{"frame": 5, "time_s": 0.2, "type": "miss", "label": "m"}],
    }
    v1["narration"]["status"] = "not_requested"
    v1["narration"]["text"] = None
    coach2 = analysis_result_to_coach_report(v1)
    assert coach2["narration"] is None


def test_build_artifact_manifest_does_not_expose_paths():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
        vf.write(b"0123456789")
        video_path = vf.name
    try:
        manifest = build_artifact_manifest_v1(
            video_path=video_path,
            csv_path=None,
            created_at="2026-07-10T12:00:00Z",
        )
        dumped = json.dumps(manifest)
        assert "path" not in dumped
        assert video_path not in dumped
        assert len(manifest["inputs"]) == 1
        entry = manifest["inputs"][0]
        assert entry["id"] == "input-video"
        assert entry["kind"] == "input_video"
        assert entry["media_type"] == "video/mp4"
        assert entry["status"] == "available"
        assert entry["size_bytes"] == 10
        assert entry["checksum_sha256"] is None
        assert manifest["outputs"] == []
    finally:
        Path(video_path).unlink(missing_ok=True)


def test_build_artifact_manifest_marks_missing_input():
    manifest = build_artifact_manifest_v1(
        video_path="/nonexistent/aiming_cookie_missing_video.mp4",
        csv_path="/nonexistent/aiming_cookie_missing.csv",
        created_at=None,
    )
    assert len(manifest["inputs"]) == 2
    for entry in manifest["inputs"]:
        assert entry["status"] == "missing"
        assert entry["size_bytes"] is None
    kinds = {e["kind"] for e in manifest["inputs"]}
    assert kinds == {"input_video", "input_stats_csv"}


def test_narration_status_must_match_text():
    report = {"diagnosis": {}, "figures": {}, "notes": [], "narration": ""}
    with pytest.raises(ValueError):
        build_analysis_result_v1(
            report=report,
            timeline=[],
            narration_status="available",
            cm_per_360=None,
            fov=None,
            artifact_manifest=_minimal_manifest(),
            created_at="2026-07-10T12:00:00Z",
            completed_at="2026-07-10T12:01:00Z",
        )
    result = build_analysis_result_v1(
        report={**report, "narration": "有内容"},
        timeline=[],
        narration_status="not_requested",
        cm_per_360=None,
        fov=None,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )
    assert result["narration"]["status"] == "not_requested"
    assert result["narration"]["text"] is None


def test_legacy_error_is_wrapped_as_safe_error_v1():
    legacy_msg = "Traceback (most recent call last): secret internals"
    err = coerce_error_v1(legacy_msg)
    assert err is not None
    assert err["schema_version"] == ERROR_SCHEMA_VERSION
    assert err["category"] == "internal_unknown"
    assert err["code"] == "legacy_error"
    assert err["message"] == "分析失败，请重试；若持续失败请联系维护者。"
    assert err["retryable"] is False
    assert err["trace_id"] is None
    assert err["details"] is None
    assert legacy_msg not in json.dumps(err)

    built = build_error_v1(
        category="internal_unknown",
        code="analysis_failed",
        message="分析失败，请重试；若持续失败请联系维护者。",
        retryable=False,
        trace_id="550e8400-e29b-41d4-a716-446655440000",
    )
    roundtrip = coerce_error_v1(json.dumps(built))
    assert roundtrip == built

    with pytest.raises(UnsupportedContractVersion):
        coerce_error_v1(json.dumps({"schema_version": "error.v99"}))