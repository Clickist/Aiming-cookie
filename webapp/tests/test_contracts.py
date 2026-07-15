from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    ANALYSIS_VERSION,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    SUMMARY_TYPE,
    UnsupportedContractVersion,
    analysis_result_to_coach_report,
    build_analysis_result_v1,
    build_analysis_result_v2,
    build_artifact_manifest_v1,
    build_artifact_manifest_v2,
    build_error_v1,
    coerce_analysis_result,
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
    assert coerce_analysis_result(result) == result
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

@pytest.mark.parametrize("input_mode", ["input_native", "multimodal", "video_fallback"])
def test_build_analysis_result_v2_supports_explicit_input_modes(input_mode: str):
    manifest = build_artifact_manifest_v2(
        external_inputs=[{"id": "kovaak-run-42", "kind": "kovaak_run"}],
        owned_outputs=[{"id": "analysis-42", "kind": "analysis_result"}],
    )
    result = build_analysis_result_v2(
        analysis_version="native_flicking.v1",
        analysis_id="analysis-42",
        analysis_type="flicking",
        input_mode=input_mode,
        owner_id="desktop-local",
        local_profile="desktop-local",
        kovaak_run_ref="kovaak-run-42",
        evidence={
            "sources": [{"ref": "kovaak-run-42"}],
            "provenance": {"collector": "kovaak"},
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic={"metrics": {"score": float("nan")}},
        artifact_manifest=manifest,
        input_snapshot={"scenario": "Tile Frenzy"},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )

    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_id"] == "analysis-42"
    assert result["analysis_version"] == "native_flicking.v1"
    assert result["analysis_type"] == "flicking"
    assert result["input_mode"] == input_mode
    assert result["owner_id"] == "desktop-local"
    assert result["local_profile"] == "desktop-local"
    assert result["status"] == "done"
    assert result["kovaak_run_ref"] == "kovaak-run-42"
    assert result["evidence"]["availability"] == {"raw_input": "available"}
    assert result["deterministic"]["metrics"]["score"] is None
    assert result["normalization_issues"] == [
        {
            "location": "$.deterministic.metrics.score",
            "code": "non_finite_number",
            "original": "nan",
        }
    ]
    assert coerce_analysis_result(result) == result
    assert result["artifact_manifest"] == {**manifest, "analysis_id": "analysis-42"}
    assert result["artifact_manifest"]["schema_version"] == ARTIFACT_MANIFEST_V2_SCHEMA_VERSION
    assert set(result["artifact_manifest"]) == {
        "schema_version",
        "analysis_id",
        "external_inputs",
        "owned_outputs",
    }


def test_analysis_result_v2_allows_metric_names_that_describe_paths():
    result = build_analysis_result_v2(
        analysis_id="analysis-42",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="kovaak-run-42",
        evidence={
            "sources": [],
            "provenance": {},
            "availability": {},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic={
            "metrics": {
                "path_length": {"value": 10.0, "unit": "raw_counts"},
                "calibrated_path_length": {"value": 2.0, "unit": "cm"},
                "path_efficiency": {"value": 0.96},
                "straightness": {"value": 0.96},
            }
        },
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[{"id": "analysis-42", "kind": "analysis_result"}],
        ),
        input_snapshot={},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )

    assert set(result["deterministic"]["metrics"]) == {
        "path_length",
        "calibrated_path_length",
        "path_efficiency",
        "straightness",
    }
    assert coerce_analysis_result(result) == result


def test_analysis_result_v2_round_trips_native_distributions_and_flick_events():
    timeline = [
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
            "metrics": {
                "peak_speed": {"value": 2000.0, "unit": "raw_counts_per_second"},
                "sparc": {"value": -3.2, "unit": "dimensionless"},
            },
            "limitations": [],
        }
    ]
    deterministic = {
        "status": "available",
        "metrics": {
            "path_length": {
                "key": "path_length",
                "value": 10.0,
                "unit": "raw_counts",
                "distribution": {
                    "median": 10.0,
                    "p75": 12.0,
                    "outlier_refs": ["flick:9"],
                },
            },
            "path_efficiency": {"key": "path_efficiency", "value": 0.9},
            "straightness": {"key": "straightness", "value": 0.9},
        },
        "timeline": timeline,
        "limitations": [],
    }
    result = build_analysis_result_v2(
        analysis_id="analysis-42",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="run:42",
        evidence={
            "sources": [],
            "provenance": {},
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic=deterministic,
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[{"id": "analysis-42", "kind": "analysis_result"}],
        ),
        input_snapshot={},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )

    assert result["deterministic"] == deterministic
    assert coerce_analysis_result(result) == result


def test_analysis_result_v2_normalizes_nested_flick_event_non_finite_values():
    result = build_analysis_result_v2(
        analysis_id="analysis-42",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="run:42",
        evidence={
            "sources": [],
            "provenance": {},
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic={
            "timeline": [
                {
                    "id": "flick:1",
                    "event_type": "flick",
                    "metrics": {"sparc": {"value": float("nan")}},
                }
            ]
        },
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[{"id": "analysis-42", "kind": "analysis_result"}],
        ),
        input_snapshot={},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )

    assert result["deterministic"]["timeline"][0]["metrics"]["sparc"]["value"] is None
    assert result["normalization_issues"] == [
        {
            "location": "$.deterministic.timeline[0].metrics.sparc.value",
            "code": "non_finite_number",
            "original": "nan",
        }
    ]
    assert coerce_analysis_result(result) == result


def test_analysis_result_v2_allows_analysis_without_kovaak_run_reference():
    result = build_analysis_result_v2(
        analysis_id="analysis-42",
        analysis_type="flicking",
        input_mode="video_fallback",
        kovaak_run_ref=None,
        evidence={
            "sources": [],
            "provenance": {},
            "availability": {},
            "alignment": {"status": "not_required"},
            "warnings": [],
        },
        deterministic={},
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[{"id": "analysis-42", "kind": "analysis_result"}],
        ),
        input_snapshot={},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )

    assert "kovaak_run_ref" not in result


def test_coerce_analysis_result_v2_keeps_old_unversioned_drafts_readable():
    result = build_analysis_result_v2(
        analysis_id="analysis:legacy-v2",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref=None,
        evidence={
            "sources": {},
            "provenance": {},
            "availability": {},
            "alignment": {"status": "unavailable"},
            "warnings": [],
        },
        deterministic={"metrics": {}},
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[
                {"id": "analysis:legacy-v2", "kind": "analysis_result"},
            ],
        ),
        input_snapshot={},
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
        warnings=[],
        errors=[],
    )
    del result["analysis_version"]

    coerced = coerce_analysis_result(result)

    assert coerced is not None
    assert coerced["analysis_version"] == LEGACY_ANALYSIS_VERSION


def test_artifact_manifest_v2_rejects_duplicate_or_misowned_artifacts():
    with pytest.raises(ValueError, match="raw_input"):
        build_artifact_manifest_v2(
            external_inputs=[],
            owned_outputs=[{"id": "run:1:trace", "kind": "raw_input"}],
        )

    with pytest.raises(ValueError, match="duplicate"):
        build_artifact_manifest_v2(
            external_inputs=[{"id": "artifact:1", "kind": "stats"}],
            owned_outputs=[{"id": "artifact:1", "kind": "analysis_result"}],
        )


def test_analysis_result_v2_requires_owned_result_artifact_to_match_analysis_id():
    with pytest.raises(ValueError, match="analysis_id"):
        build_analysis_result_v2(
            analysis_id="analysis:42",
            analysis_type="flicking",
            input_mode="input_native",
            kovaak_run_ref="run:42",
            evidence={
                "sources": [],
                "provenance": {},
                "availability": {"raw_input": "available"},
                "alignment": {"status": "aligned"},
                "warnings": [],
            },
            deterministic={},
            artifact_manifest=build_artifact_manifest_v2(
                external_inputs=[],
                owned_outputs=[
                    {"id": "analysis:other", "kind": "analysis_result"},
                ],
            ),
            input_snapshot={},
            created_at="2026-07-13T12:00:00Z",
            completed_at="2026-07-13T12:01:00Z",
            warnings=[],
            errors=[],
        )


def test_coerce_analysis_result_reads_v1_and_legacy_shapes():
    v1 = build_analysis_result_v1(
        report={"diagnosis": {"ok": True}, "figures": {}, "notes": [], "narration": None},
        timeline=[],
        narration_status="not_requested",
        cm_per_360=None,
        fov=None,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:01:00Z",
    )
    assert coerce_analysis_result(v1) == v1

    legacy = {"diagnosis": {"ok": True}, "figures": {}, "notes": [], "timeline": []}
    wrapped = coerce_analysis_result(
        legacy,
        cm_per_360=28.0,
        fov=100.0,
        created_at="2026-07-13T12:00:00Z",
        updated_at="2026-07-13T12:01:00Z",
    )
    assert wrapped is not None
    assert wrapped["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert wrapped["analysis_version"] == LEGACY_ANALYSIS_VERSION


def test_coerce_analysis_result_rejects_unknown_version():
    with pytest.raises(UnsupportedContractVersion):
        coerce_analysis_result({"schema_version": "analysis_result.v99"})


@pytest.mark.parametrize(
    "artifact_manifest",
    [
        {
            "schema_version": ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
            "external_inputs": [{"id": "input-1", "path": "/private/input.csv"}],
            "owned_outputs": [],
        },
        {
            "schema_version": ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
            "external_inputs": [{"id": "input-1", "source_path": "relative.csv"}],
            "owned_outputs": [],
        },
        {
            "schema_version": ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
            "external_inputs": [{"id": "input-1", "snapshotPath": "relative.bin"}],
            "owned_outputs": [],
        },
        {
            "schema_version": ARTIFACT_MANIFEST_V2_SCHEMA_VERSION,
            "external_inputs": [{"id": "input-1", "ref": "/private/input.csv"}],
            "owned_outputs": [],
        },
    ],
)
def test_analysis_result_v2_rejects_path_keys_and_absolute_paths(artifact_manifest: dict):
    with pytest.raises(ValueError):
        build_analysis_result_v2(
            analysis_id="analysis-42",
            analysis_type="flicking",
            input_mode="input_native",
            kovaak_run_ref="kovaak-run-42",
            evidence={
                "sources": [],
                "provenance": {},
                "availability": {},
                "alignment": {},
                "warnings": [],
            },
            deterministic={},
            artifact_manifest=artifact_manifest,
            input_snapshot={},
            created_at="2026-07-13T12:00:00Z",
            completed_at="2026-07-13T12:01:00Z",
            warnings=[],
            errors=[],
        )


def test_analysis_result_v2_rejects_path_like_run_reference():
    with pytest.raises(ValueError):
        build_analysis_result_v2(
            analysis_id="analysis-42",
            analysis_type="flicking",
            input_mode="input_native",
            kovaak_run_ref="/private/kovaak-run-42",
            evidence={
                "sources": [],
                "provenance": {},
                "availability": {},
                "alignment": {},
                "warnings": [],
            },
            deterministic={},
            artifact_manifest=build_artifact_manifest_v2(
                external_inputs=[],
                owned_outputs=[],
            ),
            input_snapshot={},
            created_at="2026-07-13T12:00:00Z",
            completed_at="2026-07-13T12:01:00Z",
            warnings=[],
            errors=[],
        )
