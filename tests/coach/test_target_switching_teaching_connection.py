from __future__ import annotations

from kovaak_tracker.advice_target_switching import (
    build_target_switching_candidate_advice,
)
from kovaak_tracker.coach.knowledge_registry import load_registry, query_registry


_SCENARIO_REF = "scenario:switching.beants_larger@1"
_TRANSITION_METRIC = "target_switching.transition_time_ms"
_SETTLE_METRIC = "target_switching.settle_duration_ms"


def _comparison_input(*, current_transition: float, current_settle: float) -> dict:
    return {
        "metrics": {
            _TRANSITION_METRIC: {"value": current_transition},
            _SETTLE_METRIC: {"value": current_settle},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                _TRANSITION_METRIC: 90.0,
                _SETTLE_METRIC: 40.0,
            },
        },
        "processed_rows": [
            {
                "event_ref": "analysis:25:event:switch-chain:1",
                "row_kind": "switch_chain",
                "classification": "stats_bounded_switch_chain",
                "transition_time_ms": current_transition,
                "settle_duration_ms": current_settle,
                "limitations": [],
            },
        ],
        "limitations": [],
    }


def test_reviewed_switching_entry_binds_real_metrics_but_not_selection():
    registry = load_registry(registry_version="2026-07-28.v3")
    entries = {entry["entry_id"]: entry for entry in registry["entries"]}

    transition = entries["switching.transition-and-arrival"]
    assert transition["scenario_prescription"]["scenario_profile_ref"] == _SCENARIO_REF
    assert transition["expected_direction"]["text"] == "comparison_only"
    assert {
        f"metric:{_TRANSITION_METRIC}",
        f"metric:{_SETTLE_METRIC}",
    } <= set(transition["metric_refs"])
    assert entries["switching.selection-observable-only"]["scenario_prescription"] == (
        "not_applicable"
    )

    for signal, metric_ref in (
        ("switch transition slow", _TRANSITION_METRIC),
        ("switch arrival error high", _SETTLE_METRIC),
    ):
        matched = query_registry(
            registry,
            issue_signal=signal,
            metric_refs=[metric_ref],
        )
        assert [entry["entry_id"] for entry in matched] == [
            "switching.transition-and-arrival"
        ]


def test_switching_advice_uses_matched_worsening_and_does_not_invent_a_problem():
    worse = build_target_switching_candidate_advice(
        _comparison_input(current_transition=180.0, current_settle=95.0),
    )
    assert [candidate["signal"] for candidate in worse] == [
        "switch transition slow",
        "switch arrival error high",
    ]

    better = build_target_switching_candidate_advice(
        _comparison_input(current_transition=85.0, current_settle=35.0),
    )
    assert better == []

    non_chain = _comparison_input(current_transition=180.0, current_settle=95.0)
    non_chain["processed_rows"][0]["row_kind"] = "unclassified_discrete_acquisition"
    assert build_target_switching_candidate_advice(non_chain) == []
