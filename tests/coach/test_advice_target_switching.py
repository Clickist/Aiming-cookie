from __future__ import annotations

from kovaak_tracker.advice_target_switching import (
    build_target_switching_candidate_advice,
)


def test_switching_advice_keeps_transition_and_terminal_control_separate():
    analysis = {
        "metrics": {
            "target_switching.transition_time_ms": {"value": 180.0},
            "target_switching.settle_duration_ms": {"value": 95.0},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "target_switching.transition_time_ms": {"value": 90.0},
                "target_switching.settle_duration_ms": {"value": 40.0},
            },
        },
        "processed_rows": [
            {
                "event_ref": "analysis:1:switch:slow",
                "row_kind": "switch_chain",
                "classification": "observable_target_switch",
                "transition_time_ms": 180.0,
                "settle_duration_ms": 95.0,
                "limitations": [],
            },
            {
                "event_ref": "analysis:1:switch:controlled",
                "row_kind": "switch_chain",
                "classification": "observable_target_switch",
                "transition_time_ms": 60.0,
                "settle_duration_ms": 20.0,
                "limitations": ["selection_unobservable"],
            },
        ],
        "limitations": ["comparison_is_descriptive_only"],
    }

    advice = build_target_switching_candidate_advice(analysis)

    assert [item["signal"] for item in advice] == [
        "switch transition slow",
        "switch arrival error high",
    ]
    assert all("severity" not in item and "prescriptions" not in item for item in advice)
    assert all(item["supporting_row_refs"] == ["analysis:1:switch:slow"] for item in advice)
    assert all(item["counterexample_row_refs"] == ["analysis:1:switch:controlled"] for item in advice)
    assert [item["knowledge_entry_refs"] for item in advice] == [
        ["knowledge:switching.transition-and-arrival@1"],
        ["knowledge:switching.transition-and-arrival@1"],
    ]
    assert all(item["verification_targets"][0]["condition"] == "matched_comparable_baseline" for item in advice)
    assert all("near_transfer_retest" in item["requested_knowledge_sections"] for item in advice)


def test_switching_advice_never_invents_selection_error_from_selected_target():
    analysis = {
        "metrics": {
            "target_switching.selection_error_ratio": {"value": 0.80},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "target_switching.selection_error_ratio": {"value": 0.20},
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:switch:unknown-rule",
            "row_kind": "switch_chain",
            "classification": "observable_target_switch",
            "selected_target_track_ref": "track:nearest",
            "limitations": [],
        }],
    }

    assert build_target_switching_candidate_advice(analysis) == []


def test_switching_advice_requires_comparable_baseline_and_observable_chain_rows():
    analysis = {
        "metrics": {
            "target_switching.transition_time_ms": {"value": 180.0},
            "target_switching.settle_duration_ms": {"value": 95.0},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "target_switching.transition_time_ms": {"value": 90.0},
                "target_switching.settle_duration_ms": {"value": 40.0},
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:switch:partial",
            "row_kind": "switch_chain",
            "classification": "unclassified_discrete_acquisition",
            "transition_time_ms": 180.0,
            "settle_duration_ms": 95.0,
            "limitations": ["previous_outcome_association_unavailable"],
        }],
    }

    assert build_target_switching_candidate_advice(analysis) == []
    analysis["comparison"]["comparable"] = False
    assert build_target_switching_candidate_advice(analysis) == []
