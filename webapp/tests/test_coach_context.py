from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from typing import Any

import pytest

from webapp.backend.coach_context import (
    COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
    coerce_coach_diagnostic_context,
    diagnostic_context_to_coach_diagnosis,
    project_coach_diagnostic_context,
    serialize_coach_diagnostic_context,
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
    "sk-live-secret-sentinel",
    "access-token-secret-sentinel",
    "raw-input-dx-sentinel",
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
                "severity": "info",
                "priority": 1,
                "priority_reason": "未校准观察",
                "plain_language_meaning": "减速过程不够连续",
                "claim_level": "experimental",
                "metric_refs": ["sparc", "reverse_ratio"],
                "event_refs": ["flick:37"],
                "limitations": ["threshold_requires_product_calibration"],
                "expected_result": "减速更连续，反向修正减少",
                "verification": {
                    "comparable_requirements": ["相同场景", "相同设置", "相同证据质量"],
                    "success_signals": ["sparc ↑", "reverse_ratio ↓"],
                    "insufficient_evidence_behavior": "样本不足时只记录",
                },
                "root_causes": [{"level": "symptom", "text": "减速不平滑"}],
                "prescriptions": [
                    {
                        "scenario": "Smoothbot",
                        "reason": "练习减速",
                        "cue": "让速度连续下降",
                        "purpose": "减少减速末段波动",
                        "target_metrics": ["sparc", "reverse_ratio"],
                        "expected_direction": ["sparc ↑", "reverse_ratio ↓"],
                        "retest_after": "完成一组后复测",
                        "stop_or_adjust_rule": "准确率明显下降时降低速度",
                        "source_level": "community_consensus",
                    }
                ],
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


def _canonical_context() -> dict:
    return project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {
                "diagnosis": {
                    "profile": {
                        "archetype_id": "decel_jitter",
                        "label": "减速抖动型",
                        "confidence": 0.9,
                        "secondary_tags": ["flicking"],
                    },
                    "issues": [],
                    "summary": {},
                    "comparison": None,
                    "meta": {
                        "summary_type": "flicking",
                        "classification": "deterministic",
                    },
                },
                "metrics": {
                    "distance": {
                        "value": 12.0,
                        "unit": "raw_counts",
                        "metric_version": "native.v1",
                        "classification": "deterministic",
                        "provenance": {
                            "kind": "derived",
                            "sources": ["raw_input"],
                        },
                    },
                },
            },
            "evidence": {
                "availability": {"raw_input": "available"},
                "alignment": {"status": "aligned", "coverage_ratio": 1.0},
                "warnings": [],
            },
            "warnings": [],
        }
    )


def _canonical_run_facts(*, oversized: bool = False) -> dict:
    limitations = []
    if oversized:
        limitations = [f"limit-{index:03d}-" + ("x" * 220) for index in range(40)]
    return {
        "schema_version": "canonical_run_facts.v1",
        "analysis_ref": "analysis:42",
        "scenario_profile_ref": None,
        "canonical_time_window_ref": "analysis:42:canonical-window",
        "field_registry_version": "source_field_registry.v1",
        "source_contracts": [
            {
                "source_kind": "stats",
                "source_ref": "run:42:stats",
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
            "stats_kill_rows_ref": "analysis:42:stats-kill-rows",
            "performance_metric_changes_ref": None,
        },
        "completeness": "partial",
        "unknown_field_policy": "excluded",
        "limitations": limitations,
    }


def _v2_context(*, run_facts: dict) -> dict:
    return {
        "schema_version": "coach_diagnostic_context.v2",
        "analysis_ref": {
            "analysis_id": "analysis:42",
            "analysis_result_version": "analysis_result.v2",
            "analysis_type": "flicking",
            "input_mode": "input_native",
        },
        "scenario": {
            "scenario_profile_ref": None,
            "analyzer_refs": ["native_flicking.v1"],
            "support_status": "supported",
            "limitations": [],
        },
        "run_facts": run_facts,
        "diagnosis": {
            "profile": {},
            "issues": [],
            "summary": {},
            "comparison": None,
            "meta": {},
        },
        "evidence_summary": {
            "availability": {"stats": "available"},
            "alignment": {"status": "aligned"},
            "segment_refs": [],
        },
        "trends": [],
        "training": {"active_plan_ref": None, "recent_retest_ref": None},
        "limitations": [],
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
                    "analysis_id": "analysis:42",
                "analysis_type": "flicking",
                "input_mode": "input_native",
                "deterministic": {
                    "diagnosis": _diagnosis(),
                    "metrics": {
                        "sparc": {
                            "med": -7.0,
                            "p90": -5.0,
                            "std": 0.4,
                            "outlier_method": "tukey_1_5_iqr_descriptive",
                            "outlier_refs": ["flick:9"],
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
        assert metric["p90"] == -5.0
        assert metric["std"] == 0.4
        assert metric["outlier_method"] == "tukey_1_5_iqr_descriptive"
        assert metric["outlier_refs"] == ["flick:9"]
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
    issue = context["diagnosis"]["issues"][0]
    assert issue["plain_language_meaning"] == "减速过程不够连续"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["sparc", "reverse_ratio"]
    assert issue["event_refs"] == ["flick:37"]
    assert issue["verification"]["comparable_requirements"] == [
        "相同场景",
        "相同设置",
        "相同证据质量",
    ]
    assert issue["verification"]["success_signals"] == ["sparc ↑", "reverse_ratio ↓"]
    assert issue["prescriptions"][0]["target_metrics"] == ["sparc", "reverse_ratio"]
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
    assert data["issues"][0]["claim_level"] == "experimental"
    assert data["issues"][0]["metric_refs"] == ["sparc", "reverse_ratio"]
    assert data["issues"][0]["verification"]["success_signals"] == [
        "sparc ↑",
        "reverse_ratio ↓",
    ]
    assert data["issues"][0]["verification"]["comparable_requirements"] == [
        "相同场景",
        "相同设置",
        "相同证据质量",
    ]
    assert data["issues"][0]["prescriptions"][0]["cue"] == "让速度连续下降"
    assert "targetInference" not in str(data)
    assert "sensitivity-sentinel" not in str(data)


def test_experimental_claim_and_source_levels_are_not_upgraded():
    diagnosis = _diagnosis()
    diagnosis["issues"][0]["claim_level"] = "experimental"
    diagnosis["issues"][0]["prescriptions"][0]["source_level"] = "experimental"

    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v1",
            "summary_type": "flicking",
            "deterministic": {"diagnosis": diagnosis},
        }
    )
    projected_issue = context["diagnosis"]["issues"][0]

    assert projected_issue["claim_level"] == "experimental"
    assert projected_issue["prescriptions"][0]["source_level"] == "experimental"

    adapted = asdict(diagnostic_context_to_coach_diagnosis(context))
    assert adapted["issues"][0]["claim_level"] == "experimental"
    assert adapted["issues"][0]["prescriptions"][0]["source_level"] == "experimental"


def test_real_advice_to_canonical_context_preserves_explanation_contract():
    from kovaak_tracker.advice import advise
    from kovaak_tracker.coach.diagnosis import build_diagnosis

    findings = advise({"sparc": {"med": -7.0}})
    diagnosis = build_diagnosis(
        findings,
        {"sparc": {"med": -7.0}},
        comparison=None,
        meta={"summary_type": "flicking"},
    )

    context = coerce_coach_diagnostic_context(diagnosis)
    issue = context["diagnosis"]["issues"][0]
    prescription = issue["prescriptions"][0]

    assert issue["claim_level"] == "experimental"
    assert issue["severity"] == "info"
    assert issue["metric_refs"] == ["sparc"]
    assert issue.get("event_refs", []) == []
    assert issue["limitations"] == ["threshold_requires_product_calibration"]
    assert issue["verification"]["comparable_requirements"] == [
        "相同场景",
        "相同设置",
        "相同证据质量",
    ]
    assert prescription["target_metrics"] == ["sparc"]
    assert prescription["expected_direction"] == ["sparc ↑"]
    assert prescription["source_level"] == "community_consensus"


@pytest.mark.parametrize("raw_level", [None, "unknown_weak_level"])
def test_missing_or_unknown_claim_and_source_levels_fail_closed(raw_level: str | None):
    diagnosis = _diagnosis()
    issue = diagnosis["issues"][0]
    prescription = issue["prescriptions"][0]
    if raw_level is None:
        issue.pop("claim_level")
        prescription.pop("source_level")
    else:
        issue["claim_level"] = raw_level
        prescription["source_level"] = raw_level

    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v1",
            "summary_type": "flicking",
            "deterministic": {"diagnosis": diagnosis},
        }
    )
    projected_issue = context["diagnosis"]["issues"][0]

    assert projected_issue["claim_level"] == "experimental"
    assert projected_issue["prescriptions"][0]["source_level"] == "experimental"

    adapted = asdict(diagnostic_context_to_coach_diagnosis(context))
    assert adapted["issues"][0]["claim_level"] == "experimental"
    assert adapted["issues"][0]["prescriptions"][0]["source_level"] == "experimental"


def test_unknown_schema_is_not_coerced_as_legacy_diagnosis():
    assert coerce_coach_diagnostic_context(
        {
            "schema_version": "coach_diagnostic_context.v999",
            "diagnosis": _diagnosis(),
        }
    ) is None


def test_canonical_projection_rejects_secret_raw_and_path_like_values_in_allowed_shapes():
    diagnosis = _diagnosis()
    issue = diagnosis["issues"][0]
    issue["plain_language_meaning"] = "access_token=access-token-secret-sentinel"
    issue["expected_result"] = "../relative/private-result.json"
    issue["prescriptions"][0]["purpose"] = "Bearer sk-live-secret-sentinel"

    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {
                "diagnosis": diagnosis,
                "metrics": {
                    "api_key": {
                        "value": "sk-live-secret-sentinel",
                        "classification": "deterministic",
                    },
                    "raw_input_dx": {
                        "value": "raw-input-dx-sentinel",
                        "classification": "deterministic",
                    },
                    "safe_metric": {
                        "value": "~/private-result.json",
                        "classification": "deterministic",
                    },
                },
            },
            "evidence": {"availability": {"raw_input": "available"}},
        }
    )

    flattened = list(_walk(context))
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in flattened
    assert "api_key" not in context["diagnosis"]["summary"]
    assert "raw_input_dx" not in context["diagnosis"]["summary"]
    assert context["evidence_summary"]["availability"] == {"raw_input": "available"}


def test_canonical_looking_context_is_reprojected_before_reaching_sinks():
    context = coerce_coach_diagnostic_context(
        {
            "schema_version": COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
            "analysis_ref": {
                "analysis_id": "analysis:42",
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
        "analysis_id": "analysis:42",
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analysis_id", 42),
        ("analysis_id", "analysis-42"),
        ("analysis_result_version", "analysis_result.v99"),
        ("input_mode", "unknown"),
        ("analysis_type", 7),
        ("analysis_type", r"C:\Users\point\private\trace.csv"),
        ("analysis_type", "file:C:/private/trace.csv"),
        ("analysis_type", "https://example.invalid/private"),
        ("analysis_type", "api_key=sk-analysis-type-secret"),
    ],
)
def test_canonical_context_rejects_invalid_analysis_ref(field: str, value: object):
    context = _canonical_context()
    context["analysis_ref"][field] = value

    assert coerce_coach_diagnostic_context(context) is None


@pytest.mark.parametrize("analysis_id", [None, "analysis:7"])
def test_v1_context_preserves_stable_or_null_ref_with_unknown_mode(analysis_id: str | None):
    result = {
        "schema_version": "analysis_result.v1",
        "analysis_id": analysis_id,
        "summary_type": "flicking",
        "deterministic": {
            "diagnosis": {
                "summary": {"legacy_metric": {"value": 1.0, "unit": "ratio"}},
            },
        },
        "artifact_manifest": {"inputs": []},
    }

    context = project_coach_diagnostic_context(result)

    assert context["analysis_ref"] == {
        "analysis_id": analysis_id,
        "analysis_result_version": "analysis_result.v1",
        "analysis_type": "flicking",
        "input_mode": "unknown",
    }
    assert context["diagnosis"]["summary"]["legacy_metric"]["value"] == 1.0
    assert coerce_coach_diagnostic_context(context) == context


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://",
        "https://example.invalid/private",
        "custom+scheme://private/resource",
        "custom+scheme://",
        "https://[C:/Users/point/private/trace.csv",
    ],
)
def test_python_projector_rejects_network_url_values(url: str):
    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {
                "diagnosis": {
                    "profile": {"label": url},
                    "issues": [],
                    "summary": {},
                    "comparison": None,
                    "meta": {},
                },
            },
            "evidence": {"availability": {}, "alignment": {}},
            "warnings": [],
        }
    )

    assert "label" not in context["diagnosis"]["profile"]
    assert url not in list(_walk(context))


def test_v2_requires_deterministic_classification_but_v1_metric_remains_readable():
    v2_context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {
                "diagnosis": {
                    "comparison": {"status": "comparable", "delta": 1.0},
                },
                "metrics": {
                    "classified": {
                        "value": 1.0,
                        "classification": "deterministic",
                        "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    },
                    "missing_classification": {
                        "value": 2.0,
                        "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    },
                    "null_classification": {
                        "value": 3.0,
                        "classification": None,
                        "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    },
                },
            },
            "evidence": {"availability": {}, "alignment": {}},
            "warnings": [],
        }
    )
    summary = v2_context["diagnosis"]["summary"]
    assert set(summary) == {"classified"}
    assert v2_context["diagnosis"]["comparison"] is None

    v1_context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v1",
            "summary_type": "flicking",
            "deterministic": {
                "diagnosis": {
                    "summary": {"legacy_metric": {"value": 4.0, "unit": "ratio"}},
                },
            },
            "artifact_manifest": {"inputs": []},
        }
    )
    assert v1_context["diagnosis"]["summary"]["legacy_metric"] == {
        "value": 4.0,
        "unit": "ratio",
    }


def test_inferred_metrics_provenance_and_timestamp_samples_are_not_coach_context():
    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {
                "metrics": {
                    "safe_metric": {
                        "value": 12.0,
                        "unit": "raw_counts",
                        "classification": "deterministic",
                        "provenance": {
                            "kind": "derived",
                            "sources": ["raw_input"],
                        },
                    },
                    "implicit_inferred_metric": {
                        "value": 99.0,
                        "unit": "ratio",
                        "provenance": {
                            "kind": "inferred",
                            "sources": ["raw_input"],
                        },
                    },
                    "explicit_inferred_metric": {
                        "value": 98.0,
                        "unit": "ratio",
                        "classification": "deterministic",
                        "provenance": {
                            "kind": "inferred",
                            "sources": ["raw_input"],
                        },
                    },
                    "string_inferred_metric": {
                        "value": 97.0,
                        "unit": "ratio",
                        "classification": "deterministic",
                        "provenance": "inferred",
                    },
                    "timestamps": {
                        "value": "timestamp-sample-sentinel",
                        "classification": "deterministic",
                    },
                },
            },
            "evidence": {
                "availability": {"raw_input": "available"},
                "alignment": {"status": "aligned"},
                "warnings": [],
            },
            "warnings": [],
        }
    )

    summary = context["diagnosis"]["summary"]
    assert summary["safe_metric"]["value"] == 12.0
    assert "implicit_inferred_metric" not in summary
    assert "explicit_inferred_metric" not in summary
    assert "string_inferred_metric" not in summary
    assert "timestamps" not in summary
    assert "timestamp-sample-sentinel" not in list(_walk(context))


@pytest.mark.asyncio
async def test_store_reprojects_context_on_append_and_load():
    import json

    from webapp.backend import coach_store, db

    canonical = _canonical_context()
    poisoned = {
        **canonical,
        "raw_trace": "raw-store-sentinel",
        "benchmark": "benchmark-store-sentinel",
        "diagnosis": {
            **canonical["diagnosis"],
            "payload": "payload-store-sentinel",
            "profile": {
                **canonical["diagnosis"]["profile"],
                "label": r"C:\Users\point\private\trace.csv",
            },
        },
    }
    expected = coerce_coach_diagnostic_context(poisoned)
    assert expected is not None

    thread = await coach_store.get_or_create_primary_thread("context-store-owner")
    message_id = await coach_store.append_message(
        int(thread["id"]),
        "user",
        "persist canonical context",
        context=poisoned,
    )

    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT context_json FROM coach_messages WHERE id=?",
        (message_id,),
    )
    persisted = await cur.fetchone()
    assert json.loads(persisted["context_json"]) == expected

    await conn.execute(
        "UPDATE coach_messages SET context_json=? WHERE id=?",
        (json.dumps(poisoned, ensure_ascii=False), message_id),
    )
    await conn.commit()
    loaded = await coach_store.load_messages(int(thread["id"]))
    assert loaded[0]["context"] == expected
    serialized = json.dumps(loaded[0]["context"], ensure_ascii=False)
    for sentinel in (
        "raw-store-sentinel",
        "benchmark-store-sentinel",
        "payload-store-sentinel",
        r"C:\Users\point\private\trace.csv",
    ):
        assert sentinel not in serialized


@pytest.mark.asyncio
async def test_same_canonical_context_crosses_python_pi_tool_sqlite_and_api_exactly(monkeypatch):
    import json
    import os
    from pathlib import Path
    import subprocess

    from httpx import ASGITransport, AsyncClient

    from webapp.backend import coach_runtime, coach_store, config
    from webapp.backend.app import app
    from webapp.backend.coach_engine import CoachTurn

    canonical = _canonical_context()
    wire = serialize_coach_diagnostic_context(canonical)
    assert wire is not None
    python_turn = CoachTurn(
        prior_messages=[],
        user_message="explain",
        diagnostic_context=canonical,
    )
    assert python_turn.diagnostic_context == canonical
    pi_request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="context-equality-owner",
        profile={
            "provider_id": "anthropic",
            "provider_name": "Anthropic",
            "kind": "builtin",
            "model_id": "claude-haiku-4-5",
        },
        messages=[{"role": "user", "content": "explain"}],
        analysis_summary=wire,
        system_prompt=None,
    )
    assert pi_request["analysis_summary"] == wire

    script = (
        'import { createAnalysisSummaryTool } from '
        '"./webapp/coach-runtime/src/analysis-summary-tool.ts";'
        'if ("AIMING_COOKIE_DESKTOP_TOKEN" in process.env) {'
        'throw new Error("desktop launch token reached Node");}'
        'const input = await new Promise((resolve) => {'
        'let data=""; process.stdin.setEncoding("utf8");'
        'process.stdin.on("data", chunk => data += chunk);'
        'process.stdin.on("end", () => resolve(data));});'
        'const result = await createAnalysisSummaryTool(String(input)).execute();'
        'process.stdout.write(result.content[0]?.text ?? "");'
    )
    monkeypatch.setenv("AIMING_COOKIE_DESKTOP_TOKEN", "desktop-token-sentinel")
    child_env = os.environ.copy()
    child_env.pop("AIMING_COOKIE_DESKTOP_TOKEN", None)
    completed = subprocess.run(
        [
            "node",
            f"--import={config.COACH_RUNTIME_TSX_LOADER.resolve().as_uri()}",
            "--input-type=module",
            "--eval",
            script,
        ],
        input=wire,
        capture_output=True,
        encoding="utf-8",
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env={
            **child_env,
            "PI_SOURCE_DIR": str(config.PI_SOURCE_DIR.resolve()),
            "TSX_TSCONFIG_PATH": str(
                (config.PI_SOURCE_DIR / "tsconfig.json").resolve()
            ),
        },
    )
    assert completed.stdout == wire

    thread = await coach_store.get_or_create_primary_thread("context-equality-owner")
    await coach_store.append_message(
        int(thread["id"]),
        "user",
        "stored exact context",
        context=canonical,
    )
    stored = await coach_store.load_messages(int(thread["id"]))
    assert stored[0]["context"] == canonical

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "context-equality-owner"},
    ) as client:
        response = await client.get("/api/coach/primary")
    assert response.status_code == 200, response.text
    assert response.json()["messages"][0]["context"] == canonical
    assert json.loads(pi_request["analysis_summary"]) == stored[0]["context"]


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

    async def complete(turn):
        assert turn.diagnostic_context["schema_version"] == "coach_diagnostic_context.v1"
        return SimpleNamespace(reply="ok", notes=[])

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


def test_historical_v1_context_is_read_exactly_without_v2_upgrade():
    context = _canonical_context()

    assert coerce_coach_diagnostic_context(context) == context
    assert context["schema_version"] == "coach_diagnostic_context.v1"
    assert "run_facts" not in context
    assert json.loads(serialize_coach_diagnostic_context(context)) == context


def test_v2_inline_context_preserves_allowlisted_facts_and_stays_within_budget():
    facts = _canonical_run_facts()
    context = _v2_context(
        run_facts={
            "mode": "inline",
            "field_registry_version": "source_field_registry.v1",
            "facts": facts,
            "limitations": [],
        }
    )

    canonical = coerce_coach_diagnostic_context(context)

    assert canonical == context
    assert set(canonical) == {
        "schema_version",
        "analysis_ref",
        "scenario",
        "run_facts",
        "diagnosis",
        "evidence_summary",
        "trends",
        "training",
        "limitations",
    }
    assert canonical["schema_version"] == "coach_diagnostic_context.v2"
    assert canonical["run_facts"]["mode"] == "inline"
    facts_wire = json.dumps(
        facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    context_wire = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert len(facts_wire) <= 8 * 1024
    assert len(context_wire) <= 32 * 1024


def test_new_analysis_with_committed_evidence_projects_to_v2_without_upgrading_legacy_results():
    context = project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {"diagnosis": {}, "metrics": {}},
            "evidence": {
                "availability": {"stats": "available"},
                "alignment": {"status": "aligned"},
                "warnings": [],
                "derived_artifact": {
                    "artifact_ref": "analysis:42:evidence:abc",
                    "evidence_revision": "sha256:abc",
                    "contract_version": "analysis_evidence_artifact.v1",
                    "checksum_sha256": "abc",
                    "size_bytes": 1,
                },
            },
            "warnings": [],
        }
    )

    assert context["schema_version"] == "coach_diagnostic_context.v2"
    assert context["run_facts"] == {
        "mode": "unavailable",
        "limitations": ["canonical_run_facts_not_inline_available"],
    }
    assert context["evidence_summary"]["artifact_ref"] == "analysis:42:evidence:abc"


def test_new_analysis_with_processed_table_projects_v3_directory_without_rows():
    table = {
        "schema_version": "processed_event_table.v1",
        "table_ref": "analysis:42:table:static_flick",
        "analysis_ref": "analysis:42",
        "analyzer_ref": "native_flicking.v1",
        "family": "static_clicking",
        "event_kind": "static_flick",
        "row_count": 73,
        "included_count": 73,
        "excluded_count": 0,
        "completeness": "complete",
        "field_catalog": [{
            "field_key": "corrective_count",
            "role": "metric",
            "value_type": "number",
            "unit": "count",
            "metric_key": "static_clicking.corrective_count",
            "metric_version": "native_flicking.v1",
            "expected_direction": "comparison_only",
            "limitations": [],
        }],
        "index_fields": ["corrective_count"],
        "rows_ref": "analysis:42:table:static_flick",
        "limitations": [],
    }
    context = project_coach_diagnostic_context({
        "schema_version": "analysis_result.v2",
        "analysis_id": "analysis:42",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "deterministic": {"diagnosis": {}, "metrics": {}},
        "evidence": {
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
            "derived_artifact": {
                "artifact_ref": "analysis:42:evidence:abc",
                "evidence_revision": "sha256:abc",
                "contract_version": "analysis_evidence_artifact.v1",
                "checksum_sha256": "abc",
                "size_bytes": 1,
            },
            "processed_event_tables": [table],
        },
        "warnings": [],
    })

    assert context["schema_version"] == "coach_diagnostic_context.v3"
    assert context["processed_events"] == {
        "mode": "table_refs",
        "tables": [table],
        "query_capabilities": [
            "analysis.events.list",
            "analysis.events.get",
            "analysis.events.rank",
            "analysis.events.filter",
            "analysis.events.aggregate",
            "analysis.events.co_occurrence",
            "analysis.events.sequence",
            "analysis.evidence.compare",
        ],
        "limitations": [],
    }
    wire = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "compact_rows" not in wire
    assert '"attributes"' not in wire
    assert len(wire.encode("utf-8")) <= 32 * 1024
    from webapp.backend.coach_service import _reachable_context_refs

    assert "analysis:42:table:static_flick" in _reachable_context_refs(context)
    assert coerce_coach_diagnostic_context(_v2_context(
        run_facts={"mode": "unavailable", "limitations": []}
    ))["schema_version"] == "coach_diagnostic_context.v2"


def test_dynamic_context_projects_frozen_profile_analyzer_and_table_without_rows():
    table = {
        "schema_version": "processed_event_table.v1",
        "table_ref": "analysis:43:table:dynamic_click",
        "analysis_ref": "analysis:43",
        "analyzer_ref": "dynamic_clicking.v1",
        "family": "dynamic_clicking",
        "event_kind": "dynamic_click",
        "row_count": 118,
        "included_count": 118,
        "excluded_count": 0,
        "completeness": "complete",
        "field_catalog": [{
            "field_key": "normalized_click_error",
            "role": "metric",
            "value_type": "number",
            "unit": "visible_radius",
            "metric_key": "dynamic_clicking.normalized_click_error",
            "metric_version": "dynamic_clicking.normalized_click_error.v1",
            "expected_direction": "comparison_only",
            "limitations": [],
        }],
        "index_fields": ["normalized_click_error"],
        "rows_ref": "analysis:43:table:dynamic_click",
        "limitations": [],
    }
    context = project_coach_diagnostic_context({
        "schema_version": "analysis_result.v2",
        "analysis_version": "dynamic_clicking.v1",
        "analysis_id": "analysis:43",
        "analysis_type": "dynamic_clicking",
        "input_mode": "multimodal",
        "input_snapshot": {
            "scenario_resolution": {
                "scenario_profile_ref": "scenario:dynamic.fixture@1",
            },
        },
        "deterministic": {
            "support_status": "supported",
            "limitations": ["motion_predictability_evidence_unavailable"],
            "metrics": {},
            "diagnosis": {
                "profile": {},
                "issues": [{
                    "signal": "relative velocity mismatch",
                    "priority": 1,
                    "priority_reason": "matched comparison candidate",
                    "claim_level": "deterministic_rule",
                    "metric_refs": ["dynamic_clicking.relative_velocity"],
                    "event_refs": ["analysis:43:dynamic-click:7"],
                    "limitations": ["motion_predictability_evidence_unavailable"],
                }],
                "summary": {},
                "comparison": None,
                "meta": {
                    "summary_type": "dynamic_clicking",
                    "classification": "deterministic",
                },
            },
        },
        "evidence": {
            "availability": {"raw_input": "available", "mp4": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
            "derived_artifact": {
                "artifact_ref": "analysis:43:evidence:abc",
                "evidence_revision": "sha256:abc",
                "contract_version": "analysis_evidence_artifact.v1",
                "checksum_sha256": "abc",
                "size_bytes": 1,
            },
            "processed_event_tables": [table],
        },
        "warnings": [],
    })

    assert context["schema_version"] == "coach_diagnostic_context.v3"
    assert context["scenario"] == {
        "scenario_profile_ref": "scenario:dynamic.fixture@1",
        "analyzer_refs": ["dynamic_clicking.v1"],
        "support_status": "supported",
        "limitations": ["motion_predictability_evidence_unavailable"],
    }
    assert context["processed_events"]["tables"] == [table]
    assert context["diagnosis"]["issues"][0]["signal"] == (
        "relative velocity mismatch"
    )
    assert context["diagnosis"]["issues"][0]["event_refs"] == [
        "analysis:43:dynamic-click:7"
    ]
    wire = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "processed_rows" not in wire
    assert '"attributes"' not in wire


def test_tracking_context_uses_generic_processed_table_directory_without_rows():
    table = {
        "schema_version": "processed_event_table.v1",
        "table_ref": "analysis:44:table:tracking_episode",
        "analysis_ref": "analysis:44",
        "analyzer_ref": "continuous_tracking.v1",
        "family": "continuous_tracking",
        "event_kind": "tracking_episode",
        "row_count": 1,
        "included_count": 1,
        "excluded_count": 0,
        "completeness": "complete",
        "field_catalog": [{
            "field_key": "target_relative_error_px",
            "role": "metric",
            "value_type": "number",
            "unit": "px",
            "metric_key": "continuous_tracking.target_relative_error_px",
            "metric_version": "continuous_tracking.target_relative_error_px.v1",
            "expected_direction": "comparison_only",
            "limitations": [],
        }],
        "index_fields": ["target_relative_error_px"],
        "rows_ref": "analysis:44:table:tracking_episode",
        "limitations": [],
    }
    context = project_coach_diagnostic_context({
        "schema_version": "analysis_result.v2",
        "analysis_version": "continuous_tracking.v1",
        "analysis_id": "analysis:44",
        "analysis_type": "continuous_tracking",
        "input_mode": "multimodal",
        "scenario": {
            "scenario_profile_ref": "scenario:tracking.fixture@1",
            "analyzer_refs": ["continuous_tracking.v1"],
            "support_status": "supported",
            "limitations": [],
        },
        "input_snapshot": {
            "scenario_resolution": {
                "scenario_profile_ref": "scenario:tracking.fixture@1",
            },
        },
        "deterministic": {
            "support_status": "supported",
            "limitations": [],
            "metrics": {},
            "diagnosis": {
                "profile": {},
                "issues": [],
                "summary": {},
                "comparison": None,
                "meta": {
                    "summary_type": "continuous_tracking",
                    "classification": "deterministic",
                },
            },
        },
        "evidence": {
            "availability": {"mp4": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
            "derived_artifact": {
                "artifact_ref": "analysis:44:evidence:abc",
                "evidence_revision": "sha256:abc",
                "contract_version": "analysis_evidence_artifact.v1",
                "checksum_sha256": "abc",
                "size_bytes": 1,
            },
            "processed_event_tables": [table],
        },
        "warnings": [],
    })

    assert context["schema_version"] == "coach_diagnostic_context.v3"
    assert context["scenario"] == {
        "scenario_profile_ref": "scenario:tracking.fixture@1",
        "analyzer_refs": ["continuous_tracking.v1"],
        "support_status": "supported",
        "limitations": [],
    }
    assert context["processed_events"]["tables"] == [table]
    wire = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "processed_rows" not in wire
    assert '"attributes"' not in wire


def test_processed_table_projection_failure_does_not_silently_fall_back_to_v2():
    table = {
        "schema_version": "processed_event_table.v1",
        "table_ref": "analysis:42:table:static_flick",
        "analysis_ref": "analysis:42",
        "analyzer_ref": "native_flicking.v1",
        "family": "static_clicking",
        "event_kind": "static_flick",
        "row_count": 1,
        "included_count": 1,
        "excluded_count": 0,
        "completeness": "complete",
        "field_catalog": [{
            "field_key": "corrective_count",
            "role": "metric",
            "value_type": "number",
            "unit": "count",
            "metric_key": "static_clicking.corrective_count",
            "metric_version": "not-a-version",
            "expected_direction": "comparison_only",
            "limitations": [],
        }],
        "index_fields": ["corrective_count"],
        "rows_ref": "analysis:42:table:static_flick",
        "limitations": [],
    }

    with pytest.raises(ValueError, match="cannot be projected safely"):
        project_coach_diagnostic_context({
            "schema_version": "analysis_result.v2",
            "analysis_id": "analysis:42",
            "analysis_type": "flicking",
            "input_mode": "input_native",
            "deterministic": {"diagnosis": {}, "metrics": {}},
            "evidence": {
                "availability": {"raw_input": "available"},
                "alignment": {"status": "aligned"},
                "warnings": [],
                "derived_artifact": {
                    "artifact_ref": "analysis:42:evidence:abc",
                    "evidence_revision": "sha256:abc",
                    "contract_version": "analysis_evidence_artifact.v1",
                    "checksum_sha256": "abc",
                    "size_bytes": 1,
                },
                "processed_event_tables": [table],
            },
            "warnings": [],
        })


def test_v2_inline_facts_over_8k_are_rejected_instead_of_silent_truncation():
    facts = _canonical_run_facts(oversized=True)
    facts_wire = json.dumps(
        facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert len(facts_wire) > 8 * 1024

    context = _v2_context(
        run_facts={
            "mode": "inline",
            "field_registry_version": "source_field_registry.v1",
            "facts": facts,
            "limitations": [],
        }
    )

    assert coerce_coach_diagnostic_context(context) is None


def test_v2_section_refs_are_bounded_and_do_not_rehydrate_facts():
    context = _v2_context(
        run_facts={
            "mode": "section_refs",
            "field_registry_version": "source_field_registry.v1",
            "section_summaries": [
                {
                    "section_key": "scenario",
                    "section_ref": "analysis:42:facts:scenario",
                    "completeness": "complete_allowlisted",
                    "present_field_count": 1,
                    "source_absent_field_count": 1,
                    "omitted_known_field_count": 0,
                }
            ],
            "limitations": ["facts_over_inline_budget"],
        }
    )

    canonical = coerce_coach_diagnostic_context(context)

    assert canonical == context
    assert canonical["run_facts"]["mode"] == "section_refs"
    assert "facts" not in canonical["run_facts"]
    assert len(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) <= 32 * 1024


def test_v2_issue_segment_refs_are_projected_and_seed_context_reachability():
    context = _v2_context(
        run_facts={"mode": "unavailable", "limitations": []}
    )
    primary = "analysis:42:segment:worst:1"
    supporting = [
        "analysis:42:segment:typical:1",
        "analysis:42:segment:improved:1",
    ]
    context["diagnosis"]["issues"] = [
        {
            "signal": "sparc low",
            "severity": "info",
            "claim_level": "experimental",
            "primary_evidence_segment_ref": primary,
            "supporting_evidence_segment_refs": supporting,
        }
    ]

    canonical = coerce_coach_diagnostic_context(context)

    assert canonical["diagnosis"]["issues"][0]["primary_evidence_segment_ref"] == primary
    assert canonical["diagnosis"]["issues"][0]["supporting_evidence_segment_refs"] == supporting
    assert canonical["evidence_summary"]["segment_refs"] == [primary, *supporting]

    too_many = deepcopy(context)
    too_many["diagnosis"]["issues"][0]["supporting_evidence_segment_refs"].append(
        "analysis:42:segment:extra:1"
    )
    assert coerce_coach_diagnostic_context(too_many) is None
