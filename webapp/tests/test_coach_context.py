from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from webapp.backend.coach_context import (
    COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
    coerce_coach_diagnostic_context,
    diagnostic_context_to_coach_diagnosis,
    project_coach_diagnostic_context,
)


_FORBIDDEN_MARKERS = (
    "raw-trace-sentinel",
    "dx-sentinel",
    "dy-sentinel",
    "button-sentinel",
    "path-sentinel",
    "payload-sentinel",
    "target-inference-sentinel",
    "sensitivity-sentinel",
    "benchmark-sentinel",
    "external-progress-sentinel",
    "/private/aiming-cookie/secret.csv",
)


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def _diagnosis() -> dict:
    return {
        "profile": {
            "archetype_id": "decel_jitter",
            "label": "减速抖动型",
            "confidence": 0.9,
            "secondary_tags": ["flicking"],
        },
        "issues": [
            {
                "signal": "sparc",
                "severity": "fix",
                "priority": 1,
                "priority_reason": "严重",
                "root_causes": [{"level": "symptom", "text": "减速不平滑"}],
                "prescriptions": [{"scenario": "Smoothbot", "reason": "练习减速"}],
                "targetInference": "target-inference-sentinel",
                "sensitivity_heuristic": "sensitivity-sentinel",
            }
        ],
        "summary": {
            "sparc": {"med": -7.0, "unit": "a.u.", "classification": "deterministic"},
            "sensitivity_estimate": {"value": "sensitivity-sentinel"},
        },
        "comparison": {
            "status": "comparable",
            "delta": -0.3,
            "benchmark": "benchmark-sentinel",
            "external_progress": "external-progress-sentinel",
        },
        "meta": {
            "summary_type": "flicking",
            "metric_version": "v1",
            "targetInference": "target-inference-sentinel",
        },
    }


@pytest.mark.parametrize(
    ("source_version", "result"),
    [
        (
            "analysis_result.v1",
            {
                "schema_version": "analysis_result.v1",
                "summary_type": "flicking",
                "deterministic": {
                    "diagnosis": _diagnosis(),
                    "timeline": [{"dx": "dx-sentinel", "buttons": "button-sentinel"}],
                },
                "artifact_manifest": {
                    "inputs": [
                        {
                            "kind": "raw_input",
                            "status": "available",
                            "path": "/private/aiming-cookie/secret.csv",
                        }
                    ]
                },
                "notes": ["payload-sentinel"],
                "source_payload": "payload-sentinel",
            },
        ),
        (
            "analysis_result.v2",
            {
                "schema_version": "analysis_result.v2",
                "analysis_id": "analysis-42",
                "analysis_type": "flicking",
                "input_mode": "input_native",
                "deterministic": {
                    "diagnosis": _diagnosis(),
                    "metrics": {
                        "sparc": {
                            "med": -7.0,
                            "unit": "a.u.",
                            "classification": "deterministic",
                            "sample_count": 42,
                            "coverage": 0.9,
                            "limitations": ["alignment_partial"],
                            "provenance": {"kind": "derived", "sources": ["raw_input"]},
                        },
                        "path_length": {
                            "value": 128.5,
                            "unit": "raw_counts",
                            "classification": "deterministic",
                            "sample_count": 42,
                            "coverage": 0.9,
                            "provenance": {"kind": "derived", "sources": ["raw_input"]},
                        },
                        "path_efficiency": {
                            "value": 0.84,
                            "unit": "ratio",
                            "classification": "deterministic",
                            "sample_count": 42,
                            "coverage": 0.9,
                            "provenance": {"kind": "derived", "sources": ["raw_input"]},
                        },
                        "path": {"value": "path-sentinel"},
                        "source_path": {"value": "path-sentinel"},
                        "snapshotPath": {"value": "path-sentinel"},
                        "unsafe_metric": {
                            "value": "/private/aiming-cookie/secret.csv",
                            "classification": "deterministic",
                        },
                        "raw_trace": "raw-trace-sentinel",
                    },
                    "trajectory": {
                        "points": ["raw-trace-sentinel"],
                        "dx": ["dx-sentinel"],
                        "dy": ["dy-sentinel"],
                        "buttons": ["button-sentinel"],
                    },
                },
                "evidence": {
                    "availability": {"raw_input": "available"},
                    "alignment": {"status": "aligned", "coverage_ratio": 0.9},
                    "sources": {"raw_input": {"path": "/private/aiming-cookie/secret.csv"}},
                    "warnings": [{"code": "trace_partial", "payload": "payload-sentinel"}],
                },
                "warnings": [{"code": "video_cv_unavailable", "message": "payload-sentinel"}],
                "input_snapshot": {
                    "path": "path-sentinel",
                    "source_payload": "payload-sentinel",
                },
                "targetInference": "target-inference-sentinel",
                "external_progress": "external-progress-sentinel",
            },
        ),
    ],
)
def test_projector_supports_analysis_result_v1_and_v2_without_forbidden_data(
    source_version: str,
    result: dict,
):
    context = project_coach_diagnostic_context(result)

    assert context["schema_version"] == COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION
    assert context["analysis_ref"]["analysis_result_version"] == source_version
    assert set(context) == {
        "schema_version",
        "analysis_ref",
        "diagnosis",
        "evidence_summary",
        "warnings",
    }
    assert context["diagnosis"]["summary"]["sparc"]["med"] == -7.0
    if source_version == "analysis_result.v2":
        metric = context["diagnosis"]["summary"]["sparc"]
        assert metric["sample_count"] == 42
        assert metric["coverage"] == 0.9
        assert metric["provenance"] == {"kind": "derived", "sources": ["raw_input"]}
        assert context["diagnosis"]["summary"]["path_length"] == {
            "value": 128.5,
            "unit": "raw_counts",
            "classification": "deterministic",
            "sample_count": 42,
            "coverage": 0.9,
            "provenance": {"kind": "derived", "sources": ["raw_input"]},
        }
        assert context["diagnosis"]["summary"]["path_efficiency"]["value"] == 0.84
        assert "path" not in context["diagnosis"]["summary"]
        assert "source_path" not in context["diagnosis"]["summary"]
        assert "snapshotPath" not in context["diagnosis"]["summary"]
        assert "value" not in context["diagnosis"]["summary"]["unsafe_metric"]
    assert context["diagnosis"]["issues"][0]["signal"] == "sparc"
    assert context["evidence_summary"]["availability"] == {"raw_input": "available"}
    expected_warnings = (
        [{"code": "video_cv_unavailable"}, {"code": "trace_partial"}]
        if source_version == "analysis_result.v2" else []
    )
    assert context["warnings"] == expected_warnings

    flattened = list(_walk(context))
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in flattened
    assert "path" not in context
    assert "source_path" not in flattened
    assert "snapshotPath" not in flattened


def test_python_adapter_consumes_projected_context_only():
    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v1",
            "summary_type": "flicking",
            "deterministic": {"diagnosis": _diagnosis()},
        }
    )

    diagnosis = diagnostic_context_to_coach_diagnosis(context)
    data = asdict(diagnosis)

    assert data["profile"]["label"] == "减速抖动型"
    assert data["issues"][0]["signal"] == "sparc"
    assert "targetInference" not in str(data)
    assert "sensitivity-sentinel" not in str(data)


def test_canonical_looking_context_is_reprojected_before_reaching_sinks():
    context = coerce_coach_diagnostic_context(
        {
            "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
            "analysis_ref": {
                "analysis_id": "analysis-42",
                "analysis_result_version": "analysis_result.v2",
                "analysis_type": "flicking",
                "input_mode": "input_native",
                "source_path": "/private/aiming-cookie/secret.csv",
            },
            "diagnosis": _diagnosis(),
            "evidence_summary": {
                "availability": {"raw_input": "available"},
                "alignment": {"status": "aligned", "coverage_ratio": 0.9},
                "raw_trace": "raw-trace-sentinel",
            },
            "warnings": [
                {
                    "code": "trace_partial",
                    "payload": "payload-sentinel",
                    "evidence_ref": {
                        "id": "evidence:trace-window",
                        "source": "raw_input",
                        "artifact_id": "run:1:trace",
                        "challenge_time_range_ms": [120, 860],
                        "alignment_status": "partial",
                        "availability": "available",
                        "local_only": True,
                        "metric_keys": ["distance_raw_counts"],
                        "path": "/private/aiming-cookie/secret.csv",
                        "points": ["raw-trace-sentinel"],
                    },
                },
            ],
            "benchmark": "benchmark-sentinel",
        }
    )

    assert context is not None
    assert set(context) == {
        "schema_version",
        "analysis_ref",
        "diagnosis",
        "evidence_summary",
        "warnings",
    }
    assert context["analysis_ref"] == {
        "analysis_id": "analysis-42",
        "analysis_result_version": "analysis_result.v2",
        "analysis_type": "flicking",
        "input_mode": "input_native",
    }
    assert context["warnings"] == [
        {
            "code": "trace_partial",
            "evidence_ref": {
                "id": "evidence:trace-window",
                "source": "raw_input",
                "artifact_id": "run:1:trace",
                "challenge_time_range_ms": [120, 860],
                "alignment_status": "partial",
                "availability": "available",
                "local_only": True,
                "metric_keys": ["distance_raw_counts"],
            },
        }
    ]
    flattened = list(_walk(context))
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in flattened

@pytest.mark.asyncio
async def test_chat_storage_persists_same_sanitized_context_for_both_messages(monkeypatch):
    from types import SimpleNamespace

    from webapp.backend import coach_service, coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    result = {
        "schema_version": "analysis_result.v2",
        "analysis_id": "analysis:1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "deterministic": {
            "metrics": {
                "distance": {
                    "value": 12.0,
                    "unit": "raw_counts",
                    "metric_version": "native.v1",
                    "classification": "deterministic",
                },
            },
            "trajectory": {"points": [{"dx": 1, "dy": 2, "buttons": 1}]},
        },
        "evidence": {
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned", "coverage_ratio": 1.0},
            "warnings": [],
        },
        "warnings": [],
        "input_snapshot": {"source_path": "/private/secret"},
    }

    async def allow_budget(*_args, **_kwargs):
        return True

    async def complete(turn):
        assert turn.diagnostic_context["schema_version"] == "coach_diagnostic_context.v1"
        return SimpleNamespace(reply="ok", notes=[])

    monkeypatch.setattr(coach_service.llm_budget, "check_and_record", allow_budget)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)
    await coach_service.run_chat_turn(
        x_user_id="u1",
        thread_id=thread["id"],
        prior_messages=[],
        user_msg_to_store="help",
        diagnosis=result,
        legacy_session_id=1,
        cost_session_id=None,
    )

    messages = await coach_store.load_messages(thread["id"])
    assert len(messages) == 2
    assert messages[0]["context"] == messages[1]["context"]
    serialized = str(messages[0]["context"])
    assert "dx" not in serialized
    assert "/private" not in serialized
    assert "source_path" not in serialized
