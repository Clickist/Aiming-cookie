from __future__ import annotations

import copy

import pytest

from kovaak_tracker.scenario_profiles import resolve_scenario_profile
from webapp.backend import coach_agent_runs, history_trends


_SCENARIO_HASH = "3b42bdfd38a6b194737d650f3f53e8c1"
_SCENARIO_REF = "scenario:switching.beants_larger@1"
_VISUAL_PROFILE_REF = (
    "visual-quality:visual_signals.event_local_target_episode@"
    "visual_target_episode.local_unique_match.v1"
)
_CONDITION_REF = "condition:target_switching:stats_kill_bounded_chain"


def _switching_result(
    *,
    analysis_ref: str,
    run_ref: str,
    value: float,
    metric_key: str = "target_switching.transition_time_ms",
    evidence_coverage: float = 0.67,
    condition_ref: str = _CONDITION_REF,
    alignment: str = "aligned",
) -> dict:
    resolution = resolve_scenario_profile(_SCENARIO_HASH, "beanTS Larger")
    metric_version = f"{metric_key}.v1"
    return {
        "schema_version": "analysis_result.v2",
        "analysis_version": "target_switching.v1",
        "analysis_id": analysis_ref,
        "analysis_type": "target_switching",
        "input_mode": "multimodal",
        "kovaak_run_ref": run_ref,
        "input_snapshot": {
            "scenario": "beanTS Larger",
            "scenario_resolution": resolution,
        },
        "evidence": {
            "alignment": {"status": alignment},
            "coverage": evidence_coverage,
        },
        "deterministic": {
            "support_status": "supported",
            "scenario_motion_class": "mixed",
            "visual_quality_profile_ref": _VISUAL_PROFILE_REF,
            "metrics": {
                metric_key: {
                    "key": metric_key,
                    "value": value,
                    "unit": "ms",
                    "metric_version": metric_version,
                    "availability": "available",
                    "classification": "deterministic",
                    "coverage": 1.0,
                    "calibration_ref": _VISUAL_PROFILE_REF,
                    "condition_refs": [condition_ref],
                },
            },
        },
    }


def _grounded_switching_bundle(*, signal: str, metric_ref: str) -> dict:
    return {
        "schema_version": "coach_turn_context.v1",
        "contexts": [
            {
                "context_ref": "context:switching-issue",
                "kind": "issue",
                "analysis_ref": "analysis:25",
                "comparison_analysis_ref": "analysis:20",
                "target_ref": "analysis:25:issue:0",
                "time_range_ms": None,
                "comparison_projection": None,
                "projection": {
                    "scenario": {
                        "scenario_profile_ref": _SCENARIO_REF,
                        "analyzer_refs": ["target_switching.v1"],
                        "support_status": "supported",
                        "limitations": [],
                    },
                    "diagnosis": {
                        "issues": [
                            {
                                "signal": signal,
                                "priority": 1,
                                "plain_language_meaning": (
                                    "The matched comparison found one repeatable "
                                    "target-switching pattern."
                                ),
                                "metric_refs": [metric_ref],
                                "observation_ref": "event.switch_chain",
                                "knowledge_registry_version": "2026-07-29.v4",
                                "knowledge_entry_refs": [
                                    "knowledge:switching.transition-and-arrival@2",
                                ],
                                "verification": {
                                    "comparable_requirements": [
                                        "same exact scenario and metric condition",
                                    ],
                                },
                            },
                        ],
                        "summary": {
                            metric_ref: {
                                "value": 180.0,
                                "classification": "deterministic",
                            },
                        },
                    },
                },
            },
        ],
    }


def test_stats_bounded_switching_metric_uses_metric_scoped_coverage():
    current = _switching_result(
        analysis_ref="analysis:25",
        run_ref="run:1038",
        value=180.0,
    )
    baseline = _switching_result(
        analysis_ref="analysis:20",
        run_ref="run:1036",
        value=90.0,
    )

    compared = history_trends.compare_analysis_results(
        current,
        baseline,
        "target_switching.transition_time_ms",
    )
    assert compared["comparable"] is True
    assert compared["delta"] == 90.0

    wrong_condition = copy.deepcopy(current)
    wrong_condition["deterministic"]["metrics"][
        "target_switching.transition_time_ms"
    ]["condition_refs"] = ["condition:target_switching:partial"]
    wrong_condition_baseline = copy.deepcopy(baseline)
    wrong_condition_baseline["deterministic"]["metrics"][
        "target_switching.transition_time_ms"
    ]["condition_refs"] = ["condition:target_switching:partial"]
    assert history_trends.compare_analysis_results(
        wrong_condition,
        wrong_condition_baseline,
        "target_switching.transition_time_ms",
    )["reason"] == "insufficient_evidence_coverage"

    unaligned = copy.deepcopy(current)
    unaligned["evidence"]["alignment"]["status"] = "partial"
    assert history_trends.compare_analysis_results(
        unaligned,
        baseline,
        "target_switching.transition_time_ms",
    )["reason"] == "insufficient_alignment_quality"


def test_switching_baseline_skips_reanalysis_of_the_same_run():
    current = _switching_result(
        analysis_ref="analysis:25",
        run_ref="run:1038",
        value=180.0,
    )
    same_run = _switching_result(
        analysis_ref="analysis:21",
        run_ref="run:1038",
        value=175.0,
    )
    independent = _switching_result(
        analysis_ref="analysis:20",
        run_ref="run:1036",
        value=90.0,
    )

    matched = history_trends.build_matched_target_switching_baseline(
        current,
        [(21, same_run), (20, independent)],
        ["target_switching.transition_time_ms"],
    )

    assert matched["baseline_analysis_ref"] == "analysis:20"
    assert matched["baseline_metrics"] == {
        "target_switching.transition_time_ms": 90.0,
    }


@pytest.mark.parametrize(
    ("signal", "metric_ref"),
    [
        ("switch transition slow", "target_switching.transition_time_ms"),
        ("switch arrival error high", "target_switching.settle_duration_ms"),
    ],
)
def test_switching_issue_compiles_the_existing_complete_plan_item(signal, metric_ref):
    prepared = coach_agent_runs._compile_prepared_plan_item(
        _grounded_switching_bundle(signal=signal, metric_ref=metric_ref),
        active_plan_ref="plan:grounded-coach-plan",
    )

    assert prepared is not None
    assert prepared["plan_ref"] == "plan:grounded-coach-plan"
    item = prepared["item"]
    assert set(item) == {
        "diagnosis_ref",
        "knowledge_ref",
        "scenario_profile_ref",
        "baseline_metric_ref",
        "expected_direction",
        "practice_condition",
        "cue",
        "dose_guardrail",
        "matched_retest_ref",
        "near_transfer_retest_ref",
        "review_date",
    }
    assert item["knowledge_ref"] == "knowledge:switching.transition-and-arrival@2"
    assert item["scenario_profile_ref"] == _SCENARIO_REF
    assert item["baseline_metric_ref"] == f"metric:{metric_ref}"
    assert item["expected_direction"] == "comparison_only"


def test_switching_issue_without_an_active_plan_compiles_nothing():
    assert coach_agent_runs._compile_prepared_plan_item(
        _grounded_switching_bundle(
            signal="switch transition slow",
            metric_ref="target_switching.transition_time_ms",
        ),
        active_plan_ref=None,
    ) is None
