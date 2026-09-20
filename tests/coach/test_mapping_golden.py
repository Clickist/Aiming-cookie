"""WP-03 golden consistency harness: freeze the BUILT-IN coach mapping outputs.

The knowledge-SDK migration (``.zcode/kb-sdk-impl-plan-2026-09-20.md``) replaces
the built-in rule engines with a data-driven mapping engine. The engine's
output must be deep-equal to the frozen built-in implementation:

- static: ``advice.advise`` -> ``list[Finding]``
- families: ``advice_tracking.build_tracking_candidate_advice``,
  ``advice_dynamic_clicking.build_dynamic_clicking_candidate_advice``,
  ``advice_target_switching.build_target_switching_candidate_advice``
  -> candidate observation dicts
- projection: ``diagnosis.build_diagnosis`` -> ``CoachDiagnosis.profile`` +
  ``issues`` (``DiagnosisIssue``)

How this file works
-------------------
- Corpus case INPUTS (summaries / analyses) are defined below; expected outputs
  live in ``fixtures/mapping_golden/*.json`` and were RECORDED from the built-in
  implementation, never hand-written.
- Re-record with ``AIMING_COOKIE_RECORD_GOLDEN=1 pytest tests/coach/test_mapping_golden.py``
  (overwrites the ``expected`` blocks). Verify by running without the flag.
- Determinism pin: an autouse fixture monkeypatches
  ``knowledge_registry.load_registry`` so every registry load resolves to
  ``2026-09-12.v12``. v12 ships permanently with the product, so the golden is
  unaffected by the parallel v13 registry work. If a family candidate or
  projected issue carries knowledge annotations, its
  ``knowledge_registry_version`` must equal the pinned version (asserted per
  item below).
- Normalization: none is needed or applied. JSON floats round-trip exactly
  (shortest-repr guarantee), and every numeric interpolation inside diagnosis
  text is already formatted to a string by the implementation. Inputs contain
  no timestamps, wall-clock values or filesystem paths, so fixture comparison
  is plain deep-equality.
- "Unavailable" static metrics: the worker drops metrics with
  ``availability == "unavailable"`` before calling ``advise``, so the golden
  expresses unavailability as keys absent from the summary
  (``empty_summary_silent`` and the single-metric cases).

Intentional change applied at WP-05 (unified fail-closed, 2026-09-20)
---------------------------------------------------------------------
``family_dynamic_clicking.json`` case ``empty_knowledge_refs_dropped_since_wp05``
simulates a registry query that returns no matching entries. The frozen
built-in dynamic builder KEEPS the candidate with empty
``knowledge_entry_refs``; the unified engine semantics DROP it (decided,
no switch). The fixture expectation was updated to the new semantics and
the case is annotated as the one intentional change.

Since WP-05 the family golden cases evaluate through the production mapping
engine (``mapping_rules.make_family_advice_fn``); that the engine output
equals the frozen built-in for this corpus is proven separately in
``test_mapping_family_engine.py``.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from kovaak_tracker.advice import Finding, Prescription, advise
from kovaak_tracker.advice_tracking import advise_tracking
from kovaak_tracker.coach import diagnosis as diagnosis_module
from kovaak_tracker.coach import mapping_rules
from kovaak_tracker.coach.diagnosis import (
    DiagnosisIssue,
    Prescription as DiagnosisPrescription,
    RootCause,
    build_diagnosis,
)

PINNED_REGISTRY_VERSION = "2026-09-12.v12"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mapping_golden"
RECORD_MODE = os.environ.get("AIMING_COOKIE_RECORD_GOLDEN") == "1"


# --------------------------------------------------------------------------
# Determinism pin
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pin_knowledge_registry_v12(monkeypatch):
    """Pin every registry load to ``2026-09-12.v12``.

    ``resolve_candidate_knowledge_refs`` imports ``load_registry`` at call time,
    so patching the module attribute is sufficient today. Once WP-08a rewires
    ``diagnosis.py`` to ``knowledge_active.load_active_registry()``, the second
    hook below keeps the pin in force (the module does not exist yet; the guard
    makes this file land before it without speculative coupling).
    """
    from kovaak_tracker.coach import knowledge_registry

    original_load_registry = knowledge_registry.load_registry

    def _pinned_load_registry(path=None, *, registry_version=None):
        return original_load_registry(registry_version=PINNED_REGISTRY_VERSION)

    monkeypatch.setattr(knowledge_registry, "load_registry", _pinned_load_registry)
    try:
        from kovaak_tracker.coach import knowledge_active
    except ImportError:
        return
    if hasattr(knowledge_active, "load_active_registry"):
        monkeypatch.setattr(
            knowledge_active,
            "load_active_registry",
            lambda: original_load_registry(registry_version=PINNED_REGISTRY_VERSION),
        )


def _empty_refs_resolver(*, issue_signal, metric_refs):
    """Stands in for a registry query with zero matches (v12 version label)."""
    return diagnosis_module.CandidateKnowledgeRefs(
        registry_version=PINNED_REGISTRY_VERSION, entry_refs=[],
    )


# --------------------------------------------------------------------------
# Serialization helpers (fixtures store plain JSON; tests reconstruct the
# dataclasses so equality is real dataclass deep-equality, which also guards
# the constructor/field shapes)
# --------------------------------------------------------------------------


def _finding_from_dict(data: dict) -> Finding:
    payload = dict(data)
    payload["prescriptions"] = [
        Prescription(**item) for item in data["prescriptions"]
    ]
    return Finding(**payload)


def _issue_from_dict(data: dict) -> DiagnosisIssue:
    payload = dict(data)
    payload["root_causes"] = [RootCause(**item) for item in data["root_causes"]]
    payload["prescriptions"] = [
        DiagnosisPrescription(**item) for item in data["prescriptions"]
    ]
    return DiagnosisIssue(**payload)


def _load_expected(fixture_name: str) -> dict:
    doc = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    assert doc["pinned_knowledge_registry_version"] == PINNED_REGISTRY_VERSION, (
        f"{fixture_name} was recorded under a different pinned registry version"
    )
    return doc["expected"]


def _record_expected(fixture_name: str, expected: dict) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "pinned_knowledge_registry_version": PINNED_REGISTRY_VERSION,
        "expected": expected,
    }
    (FIXTURE_DIR / fixture_name).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _assert_case_names_covered(cases: list[dict], expected: dict, fixture_name: str) -> None:
    assert {case["name"] for case in cases} == set(expected), (
        f"{fixture_name} cases and recorded expectations have drifted; "
        "re-record with AIMING_COOKIE_RECORD_GOLDEN=1"
    )


def _run_golden(fixture_name: str, cases: list[dict], compute) -> None:
    expected = {} if RECORD_MODE else _load_expected(fixture_name)
    if not RECORD_MODE:
        _assert_case_names_covered(cases, expected, fixture_name)
    actual_by_name = {}
    for case in cases:
        actual_by_name[case["name"]] = compute(case)
    if RECORD_MODE:
        _record_expected(fixture_name, actual_by_name)
        pytest.skip(f"recorded {fixture_name}; re-run without AIMING_COOKIE_RECORD_GOLDEN=1 to verify")
    for name, actual in actual_by_name.items():
        assert actual == expected[name], f"golden mismatch: {fixture_name}::{name}"


# --------------------------------------------------------------------------
# Static flicking corpus (advice.advise)
# --------------------------------------------------------------------------


def _m(value, version=None):
    """Metric entry in the worker summary shape: {med, metric_version}."""
    return {"med": value, "metric_version": version}


STATIC_FLICKING_CASES = [
    # --- decel_frac band: high > 0.65, low < 0.40, +-epsilon boundaries ---
    {
        "name": "decel_frac_high_above_epsilon_fires",
        "self_summary": {"decel_frac": _m(0.66)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "decel_frac_high_at_threshold_silent",
        "self_summary": {"decel_frac": _m(0.65)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "decel_frac_band_between_thresholds_silent",
        "self_summary": {"decel_frac": _m(0.50)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "decel_frac_low_below_epsilon_fires",
        "self_summary": {"decel_frac": _m(0.39)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "decel_frac_low_at_threshold_silent",
        "self_summary": {"decel_frac": _m(0.40)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- linearity > 0.13 ---
    {
        "name": "linearity_above_epsilon_fires",
        "self_summary": {"linearity": _m(0.14)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "linearity_at_threshold_silent",
        "self_summary": {"linearity": _m(0.13)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- sparc < -5.0 with the v2 version gate (legacy unversioned scale) ---
    {
        "name": "sparc_v2_native_version_gate_blocks",
        "self_summary": {"sparc": _m(-5.5, "native_flicking.sparc.v2")},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "sparc_v2_fair_summary_version_gate_blocks",
        "self_summary": {"sparc": _m(-5.5, "flicking_fair_summary.sparc.v2")},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "sparc_unversioned_below_threshold_fires",
        "self_summary": {"sparc": _m(-5.5, None)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "sparc_legacy_v1_version_below_threshold_fires",
        "self_summary": {"sparc": _m(-5.5, "native_flicking.sparc.v1")},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "sparc_above_threshold_silent",
        "self_summary": {"sparc": _m(-4.9, None)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- reverse_ratio > 0.20 ---
    {
        "name": "reverse_ratio_above_epsilon_fires",
        "self_summary": {"reverse_ratio": _m(0.21)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "reverse_ratio_at_threshold_silent",
        "self_summary": {"reverse_ratio": _m(0.20)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- submovement_overlap < 0.30 (two-stage) ---
    {
        "name": "submovement_overlap_below_epsilon_fires",
        "self_summary": {"submovement_overlap": _m(0.29)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "submovement_overlap_at_threshold_silent",
        "self_summary": {"submovement_overlap": _m(0.30)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- peak_position_pct: low < 30, high > 60, band in between ---
    {
        "name": "peak_position_low_below_epsilon_fires",
        "self_summary": {"peak_position_pct": _m(29.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "peak_position_low_at_threshold_silent",
        "self_summary": {"peak_position_pct": _m(30.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "peak_position_band_silent",
        "self_summary": {"peak_position_pct": _m(45.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "peak_position_high_at_threshold_silent",
        "self_summary": {"peak_position_pct": _m(60.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "peak_position_high_above_epsilon_fires",
        "self_summary": {"peak_position_pct": _m(61.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- path_efficiency < 0.85 ---
    {
        "name": "path_efficiency_below_epsilon_fires",
        "self_summary": {"path_efficiency": _m(0.84)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    {
        "name": "path_efficiency_at_threshold_silent",
        "self_summary": {"path_efficiency": _m(0.85)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- reference ratios < 0.70 (peak speed / throughput) ---
    {
        "name": "peak_speed_below_reference_ratio_fires",
        "self_summary": {"peak_speed_deg": _m(6.9)},
        "reference_summary": {"peak_speed_deg": _m(10.0)},
        "cm_per_360": None,
    },
    {
        "name": "peak_speed_at_reference_ratio_silent",
        "self_summary": {"peak_speed_deg": _m(7.0)},
        "reference_summary": {"peak_speed_deg": _m(10.0)},
        "cm_per_360": None,
    },
    {
        "name": "peak_speed_reference_metric_missing_silent",
        "self_summary": {"peak_speed_deg": _m(6.9)},
        "reference_summary": {},
        "cm_per_360": None,
    },
    {
        "name": "throughput_below_reference_ratio_fires",
        "self_summary": {"throughput": _m(6.9)},
        "reference_summary": {"throughput": _m(10.0)},
        "cm_per_360": None,
    },
    {
        "name": "throughput_at_reference_ratio_silent",
        "self_summary": {"throughput": _m(7.0)},
        "reference_summary": {"throughput": _m(10.0)},
        "cm_per_360": None,
    },
    {
        "name": "no_reference_relative_signals_silent",
        "self_summary": {"peak_speed_deg": _m(5.0), "throughput": _m(5.0)},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- settings channel: cm_per_360 < 25 ---
    {
        "name": "settings_cm360_below_threshold_fires",
        "self_summary": {},
        "reference_summary": None,
        "cm_per_360": 24.9,
    },
    {
        "name": "settings_cm360_at_threshold_silent",
        "self_summary": {},
        "reference_summary": None,
        "cm_per_360": 25.0,
    },
    {
        "name": "settings_cm360_missing_silent",
        "self_summary": {},
        "reference_summary": None,
        "cm_per_360": None,
    },
    # --- unavailable metrics: keys absent from the summary -> no findings ---
    {
        "name": "empty_summary_silent",
        "self_summary": {},
        "reference_summary": None,
        "cm_per_360": None,
    },
]


def test_static_flicking_golden():
    def _compute(case):
        findings = advise(
            case["self_summary"],
            case["reference_summary"],
            case["cm_per_360"],
        )
        return [asdict(finding) for finding in findings]

    _run_golden("static_flicking.json", STATIC_FLICKING_CASES, _compute)


# --------------------------------------------------------------------------
# Family corpus: continuous_tracking
# --------------------------------------------------------------------------


_TRACKING_FULL_METRICS = {
    "continuous_tracking.phase_lag_ms": {"value": 24.0},
    "continuous_tracking.loss_count": {"value": 5.0},
    "continuous_tracking.reacquisition_latency_ms": {"value": 210.0},
    "continuous_tracking.observed_change_response_ms": {"value": 0.60},
    "continuous_tracking.correction_direction_reversal_count": {"value": 7.0},
    "continuous_tracking.sparc": {"value": -6.0},
    "continuous_tracking.target_relative_error_px": {"value": 8.0},
    "continuous_tracking.time_in_radius_ratio": {"value": 0.90},
}

_TRACKING_FULL_BASELINE = {
    "continuous_tracking.phase_lag_ms": 12.0,
    "continuous_tracking.loss_count": 2.0,
    "continuous_tracking.reacquisition_latency_ms": 90.0,
    "continuous_tracking.observed_change_response_ms": 0.25,
    "continuous_tracking.correction_direction_reversal_count": 3.0,
    "continuous_tracking.sparc": -4.0,
    "continuous_tracking.target_relative_error_px": 8.0,
    "continuous_tracking.time_in_radius_ratio": 0.90,
}

_TRACKING_ROWS = [
    {
        "event_ref": "analysis:1:tracking:failure",
        "phase_lag_ms": 30.0,
        "loss_count": 6.0,
        "reacquisition_latency_ms": 260.0,
        "observed_change_response_ms": 0.8,
        "correction_burden": 9.0,
        "sparc": -7.0,
    },
    {
        "event_ref": "analysis:1:tracking:recovery",
        "phase_lag_ms": 8.0,
        "loss_count": 1.0,
        "reacquisition_latency_ms": 60.0,
        "observed_change_response_ms": 0.1,
        "correction_burden": 2.0,
        "sparc": -3.0,
    },
]

TRACKING_FAMILY_CASES = [
    {
        "name": "comparable_all_candidates_with_guardrails_holding",
        "note": "six candidates; smoothness candidates pass both guardrails",
        "analysis": {
            "metrics": _TRACKING_FULL_METRICS,
            "comparison": {
                "comparable": True,
                "baseline_metrics": _TRACKING_FULL_BASELINE,
            },
            "processed_rows": _TRACKING_ROWS,
            "limitations": ["comparison_is_descriptive_only"],
        },
    },
    {
        "name": "not_comparable_returns_empty",
        "analysis": {
            "metrics": _TRACKING_FULL_METRICS,
            "comparison": {
                "comparable": False,
                "baseline_metrics": _TRACKING_FULL_BASELINE,
            },
            "processed_rows": _TRACKING_ROWS,
            "limitations": [],
        },
    },
    {
        "name": "comparison_missing_returns_empty",
        "analysis": {
            "metrics": _TRACKING_FULL_METRICS,
            "processed_rows": _TRACKING_ROWS,
            "limitations": [],
        },
    },
    {
        "name": "baseline_metric_missing_skips_that_candidate",
        "note": "phase_lag has no baseline -> skipped; loss_count kept",
        "analysis": {
            "metrics": {
                "continuous_tracking.phase_lag_ms": {"value": 24.0},
                "continuous_tracking.loss_count": {"value": 5.0},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {"continuous_tracking.loss_count": 2.0},
            },
            "processed_rows": _TRACKING_ROWS,
            "limitations": [],
        },
    },
    {
        "name": "guardrail_failure_drops_smoothness_candidates",
        "note": "error worse + coverage worse -> correction_burden and sparc dropped",
        "analysis": {
            "metrics": {
                "continuous_tracking.loss_count": {"value": 5.0},
                "continuous_tracking.correction_direction_reversal_count": {"value": 7.0},
                "continuous_tracking.sparc": {"value": -6.0},
                "continuous_tracking.target_relative_error_px": {"value": 12.0},
                "continuous_tracking.time_in_radius_ratio": {"value": 0.70},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "continuous_tracking.loss_count": 2.0,
                    "continuous_tracking.correction_direction_reversal_count": 3.0,
                    "continuous_tracking.sparc": -4.0,
                    "continuous_tracking.target_relative_error_px": 8.0,
                    "continuous_tracking.time_in_radius_ratio": 0.90,
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "row_field_missing_yields_empty_row_refs",
        "note": "rows carry event_ref but lack the sparc row field",
        "analysis": {
            "metrics": {
                "continuous_tracking.sparc": {"value": -6.0},
                "continuous_tracking.target_relative_error_px": {"value": 8.0},
                "continuous_tracking.time_in_radius_ratio": {"value": 0.90},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "continuous_tracking.sparc": -4.0,
                    "continuous_tracking.target_relative_error_px": 8.0,
                    "continuous_tracking.time_in_radius_ratio": 0.90,
                },
            },
            "processed_rows": [
                {"event_ref": "analysis:1:tracking:nofield", "loss_count": 9.0},
            ],
            "limitations": [],
        },
    },
    {
        "name": "equal_to_baseline_skips",
        "analysis": {
            "metrics": {"continuous_tracking.loss_count": {"value": 5.0}},
            "comparison": {
                "comparable": True,
                "baseline_metrics": {"continuous_tracking.loss_count": 5.0},
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "improved_lower_better_metric_skips",
        "note": "sparc improved (-3.9 > -4.0); lower-is-better -> not worse",
        "analysis": {
            "metrics": {
                "continuous_tracking.sparc": {"value": -3.9},
                "continuous_tracking.target_relative_error_px": {"value": 8.0},
                "continuous_tracking.time_in_radius_ratio": {"value": 0.90},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "continuous_tracking.sparc": -4.0,
                    "continuous_tracking.target_relative_error_px": 8.0,
                    "continuous_tracking.time_in_radius_ratio": 0.90,
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
]


def test_family_tracking_golden():
    advice_fn = mapping_rules.make_family_advice_fn("continuous_tracking")
    expected = {} if RECORD_MODE else _load_expected("family_tracking.json")
    if not RECORD_MODE:
        _assert_case_names_covered(TRACKING_FAMILY_CASES, expected, "family_tracking.json")
    actual_by_name = {}
    for case in TRACKING_FAMILY_CASES:
        candidates = advice_fn(case["analysis"])
        for candidate in candidates:
            assert candidate["knowledge_registry_version"] == PINNED_REGISTRY_VERSION
        actual_by_name[case["name"]] = candidates
    if RECORD_MODE:
        _record_expected("family_tracking.json", actual_by_name)
        pytest.skip("recorded family_tracking.json")
    for name, actual in actual_by_name.items():
        assert actual == expected[name], f"golden mismatch: family_tracking.json::{name}"
        for actual_candidate, expected_candidate in zip(actual, expected[name]):
            assert (
                actual_candidate["knowledge_entry_refs"]
                == expected_candidate["knowledge_entry_refs"]
            )
            assert (
                actual_candidate["knowledge_registry_version"]
                == expected_candidate["knowledge_registry_version"]
            )


# --------------------------------------------------------------------------
# Family corpus: dynamic_clicking
# --------------------------------------------------------------------------

DYNAMIC_FAMILY_CASES = [
    {
        "name": "comparable_hits_all_three_candidates",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.42,
                },
                "dynamic_clicking.acquisition_time_ms": {
                    "availability": "available", "value": 850.0,
                },
                "dynamic_clicking.relative_velocity": {
                    "availability": "available", "value": 0.18,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                    "dynamic_clicking.acquisition_time_ms": 400.0,
                    "dynamic_clicking.relative_velocity": 0.08,
                },
            },
            "processed_rows": [
                {
                    "event_ref": "analysis:1:dynamic-click:1",
                    "normalized_click_error": 0.42,
                    "acquisition_time_ms": 850.0,
                    "relative_velocity_magnitude": 0.18,
                },
                {
                    "event_ref": "analysis:1:dynamic-click:2",
                    "normalized_click_error": 0.10,
                    "acquisition_time_ms": 300.0,
                    "relative_velocity_magnitude": 0.05,
                },
            ],
            "limitations": [],
        },
    },
    {
        "name": "not_comparable_returns_empty",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.95,
                },
            },
            "comparison": {"comparable": False},
            "processed_rows": [],
            "limitations": ["motion_condition_missing"],
        },
    },
    {
        "name": "outcome_only_returns_empty",
        "analysis": {
            "support_status": "outcome_only",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.95,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "baseline_metric_missing_skips_that_candidate",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.42,
                },
                "dynamic_clicking.acquisition_time_ms": {
                    "availability": "available", "value": 850.0,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "unavailable_metric_skips",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "unavailable", "value": 0.95,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [],
            "limitations": ["dynamic_clicking_quality_unavailable"],
        },
    },
    {
        "name": "blocking_limitations_drops_only_affected_candidate",
        "note": "blocking sets are per metric: click_geometry... blocks "
                "normalized_click_error but not relative_velocity",
        "analysis": {
            "support_status": "partial",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available",
                    "value": 0.61,
                    "limitations": ["click_geometry_visible_radius_conditioned"],
                },
                "dynamic_clicking.acquisition_time_ms": {
                    "availability": "available",
                    "value": 900.0,
                    "limitations": ["outcome_association_unavailable"],
                },
                "dynamic_clicking.relative_velocity": {
                    "availability": "available",
                    "value": 0.18,
                    "limitations": ["click_geometry_visible_radius_conditioned"],
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.58,
                    "dynamic_clicking.acquisition_time_ms": 400.0,
                    "dynamic_clicking.relative_velocity": 0.08,
                },
            },
            "processed_rows": [
                {
                    "event_ref": "analysis:1:dynamic-click:1",
                    "relative_velocity_magnitude": 0.18,
                },
            ],
            "limitations": [],
        },
    },
    {
        "name": "row_field_missing_yields_empty_row_refs",
        "note": "dynamic keeps the candidate even without supporting rows",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.42,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [{"event_ref": "analysis:1:dynamic-click:nofield"}],
            "limitations": [],
        },
    },
    {
        "name": "equal_to_baseline_skips",
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.20,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "empty_knowledge_refs_dropped_since_wp05",
        "note": "已按拍板统一为 fail-closed（有意变更，2026-09-20）：the frozen "
                "built-in dynamic builder KEPT the candidate when the registry "
                "query returned no entries; the unified engine DROPs it. The "
                "official registry always matches these signals, so the empty "
                "state is simulated by stubbing "
                "resolve_candidate_knowledge_refs during record and verify.",
        "simulate_empty_knowledge_refs": True,
        "analysis": {
            "support_status": "supported",
            "metrics": {
                "dynamic_clicking.normalized_click_error": {
                    "availability": "available", "value": 0.42,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "dynamic_clicking.normalized_click_error": 0.20,
                },
            },
            "processed_rows": [
                {
                    "event_ref": "analysis:1:dynamic-click:1",
                    "normalized_click_error": 0.42,
                },
            ],
            "limitations": [],
        },
    },
]


def test_family_dynamic_clicking_golden(monkeypatch):
    advice_fn = mapping_rules.make_family_advice_fn("dynamic_clicking")
    expected = {} if RECORD_MODE else _load_expected("family_dynamic_clicking.json")
    if not RECORD_MODE:
        _assert_case_names_covered(DYNAMIC_FAMILY_CASES, expected, "family_dynamic_clicking.json")
    actual_by_name = {}
    for case in DYNAMIC_FAMILY_CASES:
        if case.get("simulate_empty_knowledge_refs"):
            with monkeypatch.context() as patch_context:
                patch_context.setattr(
                    diagnosis_module,
                    "resolve_candidate_knowledge_refs",
                    _empty_refs_resolver,
                )
                candidates = advice_fn(case["analysis"])
        else:
            candidates = advice_fn(case["analysis"])
        for candidate in candidates:
            assert candidate["knowledge_registry_version"] == PINNED_REGISTRY_VERSION
        actual_by_name[case["name"]] = candidates
    if RECORD_MODE:
        _record_expected("family_dynamic_clicking.json", actual_by_name)
        pytest.skip("recorded family_dynamic_clicking.json")
    for name, actual in actual_by_name.items():
        assert actual == expected[name], f"golden mismatch: family_dynamic_clicking.json::{name}"
        for actual_candidate, expected_candidate in zip(actual, expected[name]):
            assert (
                actual_candidate["knowledge_entry_refs"]
                == expected_candidate["knowledge_entry_refs"]
            )
            assert (
                actual_candidate["knowledge_registry_version"]
                == expected_candidate["knowledge_registry_version"]
            )


# --------------------------------------------------------------------------
# Family corpus: target_switching
# --------------------------------------------------------------------------


_SWITCH_ROW_SLOW = {
    "event_ref": "analysis:1:switch:slow",
    "row_kind": "switch_chain",
    "classification": "observable_target_switch",
    "transition_time_ms": 180.0,
    "settle_duration_ms": 95.0,
    "limitations": [],
}
_SWITCH_ROW_CONTROLLED = {
    "event_ref": "analysis:1:switch:controlled",
    "row_kind": "switch_chain",
    "classification": "observable_target_switch",
    "transition_time_ms": 60.0,
    "settle_duration_ms": 20.0,
    "limitations": ["selection_unobservable"],
}
_SWITCH_ROW_UNOBSERVABLE = {
    "event_ref": "analysis:1:switch:hidden",
    "row_kind": "switch_chain",
    "classification": "unclassified_discrete_acquisition",
    "transition_time_ms": 500.0,
    "settle_duration_ms": 500.0,
    "limitations": [],
}

SWITCHING_FAMILY_CASES = [
    {
        "name": "comparable_hits_transition_and_settle",
        "note": "unobservable rows are excluded from supporting/counterexample refs",
        "analysis": {
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
                _SWITCH_ROW_SLOW,
                _SWITCH_ROW_CONTROLLED,
                _SWITCH_ROW_UNOBSERVABLE,
            ],
            "limitations": ["comparison_is_descriptive_only"],
        },
    },
    {
        "name": "not_comparable_returns_empty",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 180.0},
            },
            "comparison": {"comparable": False},
            "processed_rows": [],
            "limitations": [],
        },
    },
    {
        "name": "baseline_metric_missing_skips_that_candidate",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 180.0},
                "target_switching.settle_duration_ms": {"value": 95.0},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "target_switching.transition_time_ms": {"value": 90.0},
                },
            },
            "processed_rows": [_SWITCH_ROW_SLOW],
            "limitations": [],
        },
    },
    {
        "name": "unavailable_metric_skips",
        "note": "settle marked unavailable; its baseline is present but ignored",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 180.0},
                "target_switching.settle_duration_ms": {
                    "availability": "unavailable", "value": 95.0,
                },
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "target_switching.transition_time_ms": {"value": 90.0},
                    "target_switching.settle_duration_ms": {"value": 40.0},
                },
            },
            "processed_rows": [_SWITCH_ROW_SLOW],
            "limitations": [],
        },
    },
    {
        "name": "no_supporting_rows_drops_candidate",
        "note": "unlike dynamic/tracking, switching requires supporting rows",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 180.0},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "target_switching.transition_time_ms": {"value": 90.0},
                },
            },
            "processed_rows": [
                {
                    "event_ref": "analysis:1:switch:fast",
                    "row_kind": "switch_chain",
                    "classification": "observable_target_switch",
                    "transition_time_ms": 60.0,
                    "limitations": [],
                },
            ],
            "limitations": [],
        },
    },
    {
        "name": "row_field_missing_drops_candidate",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 180.0},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "target_switching.transition_time_ms": {"value": 90.0},
                },
            },
            "processed_rows": [
                {
                    "event_ref": "analysis:1:switch:nofield",
                    "row_kind": "switch_chain",
                    "classification": "observable_target_switch",
                    "limitations": [],
                },
            ],
            "limitations": [],
        },
    },
    {
        "name": "equal_to_baseline_skips",
        "analysis": {
            "metrics": {
                "target_switching.transition_time_ms": {"value": 90.0},
            },
            "comparison": {
                "comparable": True,
                "baseline_metrics": {
                    "target_switching.transition_time_ms": {"value": 90.0},
                },
            },
            "processed_rows": [],
            "limitations": [],
        },
    },
]


def test_family_target_switching_golden():
    advice_fn = mapping_rules.make_family_advice_fn("target_switching")
    expected = {} if RECORD_MODE else _load_expected("family_target_switching.json")
    if not RECORD_MODE:
        _assert_case_names_covered(SWITCHING_FAMILY_CASES, expected, "family_target_switching.json")
    actual_by_name = {}
    for case in SWITCHING_FAMILY_CASES:
        candidates = advice_fn(case["analysis"])
        for candidate in candidates:
            assert candidate["knowledge_registry_version"] == PINNED_REGISTRY_VERSION
        actual_by_name[case["name"]] = candidates
    if RECORD_MODE:
        _record_expected("family_target_switching.json", actual_by_name)
        pytest.skip("recorded family_target_switching.json")
    for name, actual in actual_by_name.items():
        assert actual == expected[name], f"golden mismatch: family_target_switching.json::{name}"
        for actual_candidate, expected_candidate in zip(actual, expected[name]):
            assert (
                actual_candidate["knowledge_entry_refs"]
                == expected_candidate["knowledge_entry_refs"]
            )
            assert (
                actual_candidate["knowledge_registry_version"]
                == expected_candidate["knowledge_registry_version"]
            )


# --------------------------------------------------------------------------
# Diagnosis projection corpus (build_diagnosis -> profile + issues)
# --------------------------------------------------------------------------


_PROJECTION_KITCHEN_SINK_SUMMARY = {
    "decel_frac": _m(0.72),
    "linearity": _m(0.20),
    "sparc": _m(-6.5, "native_flicking.sparc.v1"),
    "reverse_ratio": _m(0.24),
    "submovement_overlap": _m(0.25),
    "peak_position_pct": _m(25.0),
    "path_efficiency": _m(0.80),
    "peak_speed_deg": _m(5.0),
    "throughput": _m(5.0),
}
_PROJECTION_KITCHEN_SINK_REFERENCE = {
    "peak_speed_deg": _m(10.0),
    "throughput": _m(10.0),
}

PROJECTION_CASES = [
    {
        "name": "flicking_kitchen_sink",
        "note": "ten signals fire; exercises root causes, observation refs and "
                "top-3 knowledge annotations under the pinned v12 registry",
        "kind": "flicking",
        "summary": _PROJECTION_KITCHEN_SINK_SUMMARY,
        "reference_summary": _PROJECTION_KITCHEN_SINK_REFERENCE,
        "cm_per_360": 22.0,
        "meta": {},
    },
    {
        "name": "flicking_clean_fluid_precise",
        "kind": "flicking",
        "summary": {"decel_frac": _m(0.50), "sparc": _m(-1.0, None)},
        "reference_summary": None,
        "cm_per_360": None,
        "meta": {"summary_type": "flicking"},
    },
    {
        "name": "tracking_pathological",
        "note": "advise_tracking findings projected through build_diagnosis",
        "kind": "tracking",
        "summary": {
            "tension": {
                "avg_error_px": 50.0,
                "speed_mismatch": 600.0,
                "accel_mismatch": 1500.0,
                "ptc": 2000.0,
            },
            "loss": {
                "on_target_pct": 40.0,
                "loss_count": 100,
                "total_off_time": 10.0,
            },
        },
        "meta": {"summary_type": "tracking"},
    },
]


def test_diagnosis_projection_golden():
    expected = {} if RECORD_MODE else _load_expected("diagnosis_projection.json")
    if not RECORD_MODE:
        _assert_case_names_covered(PROJECTION_CASES, expected, "diagnosis_projection.json")
    actual_by_name = {}
    for case in PROJECTION_CASES:
        if case["kind"] == "flicking":
            findings = advise(
                case["summary"], case["reference_summary"], case["cm_per_360"],
            )
        else:
            findings = advise_tracking(case["summary"])
        diagnosis = build_diagnosis(findings, case["summary"], [], case["meta"])
        for issue in diagnosis.issues:
            assert issue.knowledge_registry_version in (None, PINNED_REGISTRY_VERSION)
        actual_by_name[case["name"]] = {
            "findings": [asdict(finding) for finding in findings],
            "profile": asdict(diagnosis.profile),
            "issues": [asdict(issue) for issue in diagnosis.issues],
        }
    if RECORD_MODE:
        _record_expected("diagnosis_projection.json", actual_by_name)
        pytest.skip("recorded diagnosis_projection.json")
    for name, actual in actual_by_name.items():
        expected_case = expected[name]
        assert [_finding_from_dict(item) for item in actual["findings"]] == [
            _finding_from_dict(item) for item in expected_case["findings"]
        ], f"golden mismatch (findings): diagnosis_projection.json::{name}"
        assert actual["profile"] == expected_case["profile"], (
            f"golden mismatch (profile): diagnosis_projection.json::{name}"
        )
        assert [_issue_from_dict(item) for item in actual["issues"]] == [
            _issue_from_dict(item) for item in expected_case["issues"]
        ], f"golden mismatch (issues): diagnosis_projection.json::{name}"
