from __future__ import annotations

from kovaak_tracker.advice_dynamic_clicking import build_dynamic_clicking_candidate_advice


def test_dynamic_advice_uses_matched_comparison_without_absolute_thresholds():
    analysis = {
        "metrics": {
            "dynamic_clicking.normalized_click_error": {
                "availability": "available",
                "value": 0.42,
            },
            "dynamic_clicking.relative_velocity": {
                "availability": "available",
                "value": 0.18,
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "dynamic_clicking.normalized_click_error": 0.20,
                "dynamic_clicking.relative_velocity": 0.08,
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:dynamic-click:1",
            "normalized_click_error": 0.42,
            "relative_velocity_magnitude": 0.18,
        }],
        "limitations": [],
    }

    advice = build_dynamic_clicking_candidate_advice(analysis)

    assert [item["signal"] for item in advice] == [
        "dynamic click error high",
        "relative velocity mismatch",
    ]
    assert all("severity" not in item and "prescriptions" not in item for item in advice)
    assert all(item["supporting_row_refs"] == ["analysis:1:dynamic-click:1"] for item in advice)
    assert all(item["knowledge_entry_refs"] for item in advice)
    assert all("alternative_explanations" in item["requested_knowledge_sections"] for item in advice)
    assert all("matched_retest" in item["requested_knowledge_sections"] for item in advice)


def test_dynamic_advice_does_not_invent_a_problem_without_comparability():
    advice = build_dynamic_clicking_candidate_advice({
        "metrics": {"dynamic_clicking.normalized_click_error": {"value": 0.95}},
        "comparison": {"comparable": False},
        "processed_rows": [],
        "limitations": ["motion_condition_missing"],
    })

    assert advice == []


def test_dynamic_advice_ignores_stale_unavailable_metrics():
    advice = build_dynamic_clicking_candidate_advice({
        "support_status": "supported",
        "metrics": {
            "dynamic_clicking.normalized_click_error": {
                "availability": "unavailable",
                "value": 0.95,
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "dynamic_clicking.normalized_click_error": 0.20,
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:dynamic-click:1",
            "normalized_click_error": 0.95,
        }],
        "limitations": ["dynamic_clicking_quality_unavailable"],
    })

    assert advice == []


def test_dynamic_advice_ignores_outcome_only_analysis_with_stale_values():
    advice = build_dynamic_clicking_candidate_advice({
        "support_status": "outcome_only",
        "metrics": {
            "dynamic_clicking.normalized_click_error": {
                "availability": "available",
                "value": 0.95,
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "dynamic_clicking.normalized_click_error": 0.20,
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:dynamic-click:1",
            "normalized_click_error": 0.95,
        }],
        "limitations": ["dynamic_clicking_quality_unavailable"],
    })

    assert advice == []


def test_dynamic_advice_keeps_visible_radius_conditioned_geometry_descriptive():
    advice = build_dynamic_clicking_candidate_advice({
        "support_status": "partial",
        "metrics": {
            "dynamic_clicking.normalized_click_error": {
                "availability": "available",
                "value": 0.61,
                "limitations": [
                    "click_geometry_visible_radius_conditioned",
                    "identity_continuity_not_observed",
                ],
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "dynamic_clicking.normalized_click_error": 0.58,
            },
        },
        "processed_rows": [{
            "event_ref": "analysis:1:dynamic-click:1",
            "normalized_click_error": 0.61,
            "limitations": ["click_geometry_visible_radius_conditioned"],
        }],
        "limitations": ["identity_continuity_not_observed"],
    })

    assert advice == []
