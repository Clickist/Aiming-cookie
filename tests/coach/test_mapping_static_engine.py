"""WP-04: the static mapping engine must reproduce the frozen built-in engine.

Runs the WP-03 static golden corpus through BOTH ``advice.advise`` (built-in
fallback, frozen) and ``mapping_rules.dispatch_static`` (the official
``knowledge/mapping/official.v1.json`` via the mapping engine) and asserts
``Finding``-list deep equality — the C5 output-shape contract that lets
``worker._native_diagnosis`` switch tracks without changing any output.

Also pins: the validate+compile+evaluate seam, the fail-closed fallback
semantics (missing mapping / broken mapping / rule-level error), the settings
pass-through shape, and the ``make_family_advice_fn`` wiring contract.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from kovaak_tracker.advice import advise
from kovaak_tracker.coach import knowledge_active, knowledge_registry, mapping_rules
from kovaak_tracker.coach import profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_MAPPING_PATH = REPO_ROOT / "knowledge" / "mapping" / "official.v1.json"
VOCABULARY_PATH = REPO_ROOT / "knowledge" / "mapping" / "vocabulary.v1.json"


def _official_doc() -> dict:
    return json.loads(OFFICIAL_MAPPING_PATH.read_bytes())


def _settings(case: dict) -> dict | None:
    return {"cm_per_360": case["cm_per_360"]} if case["cm_per_360"] is not None else None


# ---------------------------------------------------------------------------
# Engine == built-in over the WP-03 static golden corpus
# ---------------------------------------------------------------------------


def test_engine_matches_builtin_over_static_golden_corpus():
    from tests.coach.test_mapping_golden import STATIC_FLICKING_CASES

    assert len(STATIC_FLICKING_CASES) == 33
    for case in STATIC_FLICKING_CASES:
        builtin = advise(
            case["self_summary"], case["reference_summary"], case["cm_per_360"],
        )
        engine = mapping_rules.dispatch_static(
            case["self_summary"],
            case["reference_summary"],
            _settings(case),
        )
        assert [asdict(f) for f in engine] == [asdict(f) for f in builtin], (
            f"engine != built-in for golden case {case['name']}"
        )


def test_engine_matches_builtin_on_ten_signal_kitchen_sink():
    """One summary firing ten signals at once: order + every field."""
    from tests.coach.test_mapping_golden import (
        _PROJECTION_KITCHEN_SINK_REFERENCE,
        _PROJECTION_KITCHEN_SINK_SUMMARY,
    )

    builtin = advise(
        _PROJECTION_KITCHEN_SINK_SUMMARY,
        _PROJECTION_KITCHEN_SINK_REFERENCE,
        22.0,
    )
    engine = mapping_rules.dispatch_static(
        _PROJECTION_KITCHEN_SINK_SUMMARY,
        _PROJECTION_KITCHEN_SINK_REFERENCE,
        {"cm_per_360": 22.0},
    )
    assert len(engine) == 10
    assert [asdict(f) for f in engine] == [asdict(f) for f in builtin]


def test_evaluate_static_matches_dispatch_path():
    """validate -> compile -> evaluate_static is the same engine dispatch runs."""
    compiled = mapping_rules.compile_mapping(_official_doc())
    summary = {"decel_frac": {"med": 0.72}, "sparc": {"med": -6.5}}
    engine = mapping_rules.evaluate_static(compiled, summary)
    dispatched = mapping_rules.dispatch_static(summary)
    assert [asdict(f) for f in engine] == [asdict(f) for f in dispatched]
    assert [finding.signal for finding in engine] == [
        "decel_frac high",
        "sparc low",
    ]


# ---------------------------------------------------------------------------
# Official mapping document contract
# ---------------------------------------------------------------------------


def test_official_mapping_passes_validation_against_packaged_vocabulary():
    """Acceptance cross-check: coach_mapping.v1 validator on the official doc
    with the packaged vocabulary and the default official registry."""
    mapping_rules.validate_mapping(
        _official_doc(),
        vocabulary=json.loads(VOCABULARY_PATH.read_bytes()),
        registry=knowledge_registry.load_registry(),
    )


def test_official_mapping_covers_all_twelve_builtin_threshold_keys():
    doc = _official_doc()
    assert len(doc["static_clicking"]) == 12
    assert {rule["signal"] for rule in doc["static_clicking"]} == {
        "decel_frac high",
        "decel_frac low",
        "linearity high",
        "sparc low",
        "reverse_ratio high",
        "submovement two-stage",
        "peak_position low",
        "peak_position high",
        "path_efficiency low",
        "peak_speed below reference",
        "throughput below reference",
        "sensitivity high",
    }
    # Family sections filled by WP-05 (6 tracking + 3 dynamic + 2 switching).
    families = doc["families"]
    assert [len(families[family]) for family in
            ("continuous_tracking", "dynamic_clicking", "target_switching")] == [6, 3, 2]
    assert {rule["signal"] for rule in families["continuous_tracking"]} == {
        "tracking lag high",
        "loss count high",
        "off target long",
        "accel mismatch high",
        "correction burden high",
        "sparc low",
    }
    assert {rule["signal"] for rule in families["dynamic_clicking"]} == {
        "dynamic click error high",
        "dynamic acquisition slow",
        "relative velocity mismatch",
    }
    assert {rule["signal"] for rule in families["target_switching"]} == {
        "switch transition slow",
        "switch arrival error high",
    }
    # Sections filled by WP-06: migrated verbatim from profiles.py, so the
    # mapping data and the frozen fallback constants stay identical.
    assert len(doc["archetypes"]) == len(profiles.ARCHETYPES)
    for archetype, builtin in zip(doc["archetypes"], profiles.ARCHETYPES):
        assert archetype["id"] == builtin["id"]
        assert archetype["label"] == builtin["label"]
        assert archetype["conditions"] == builtin["conditions"]
        assert archetype["positive"] == (not builtin["conditions"])
    assert doc["root_causes"] == {
        signal: list(triple) for signal, triple in profiles.ROOT_CAUSES.items()
    }


# ---------------------------------------------------------------------------
# Fail-closed fallback semantics
# ---------------------------------------------------------------------------


def test_missing_mapping_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(
        knowledge_active, "load_active_mapping", lambda: (None, None),
    )
    summary = {"decel_frac": {"med": 0.72}}
    assert mapping_rules.dispatch_static(summary) == advise(summary)
    assert mapping_rules.last_static_diagnostics() == []


def test_broken_mapping_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(
        knowledge_active,
        "load_active_mapping",
        lambda: ({"schema_version": "nope"}, None),
    )
    summary = {"decel_frac": {"med": 0.72}}
    assert mapping_rules.dispatch_static(summary) == advise(summary)
    diagnostics = mapping_rules.last_static_diagnostics()
    assert len(diagnostics) == 1
    assert "built-in fallback" in diagnostics[0]


def test_rule_level_error_drops_only_that_rule(monkeypatch):
    real_slots = mapping_rules._static_text_slots

    def exploding_slots(signal, **kwargs):
        if signal == "decel_frac high":
            raise ValueError("boom")
        return real_slots(signal, **kwargs)

    monkeypatch.setattr(mapping_rules, "_static_text_slots", exploding_slots)
    summary = {"decel_frac": {"med": 0.72}, "reverse_ratio": {"med": 0.5}}
    engine = mapping_rules.dispatch_static(summary)
    builtin = advise(summary)
    assert [finding.signal for finding in engine] == [
        finding.signal for finding in builtin if finding.signal != "decel_frac high"
    ]
    assert engine == [f for f in builtin if f.signal != "decel_frac high"]
    assert any("decel_frac high" in item for item in mapping_rules.last_static_diagnostics())


def test_settings_channel_passes_through_dispatch():
    summary: dict = {}
    assert len(mapping_rules.dispatch_static(summary, None, {"cm_per_360": 24.9})) == 1
    assert mapping_rules.dispatch_static(summary, None, {"cm_per_360": 25.0}) == []
    assert mapping_rules.dispatch_static(summary, None, None) == []


# ---------------------------------------------------------------------------
# C5 wiring contract
# ---------------------------------------------------------------------------


def test_make_family_advice_fn_wiring_contract(monkeypatch):
    """WP-05: the factory returns engine closures with the built-in builder
    signature; a missing mapping file is the normal frozen built-in
    fallback. Detailed family-engine behavior lives in
    ``test_mapping_family_engine.py``."""
    from kovaak_tracker.advice_dynamic_clicking import (
        build_dynamic_clicking_candidate_advice,
    )

    closures = {
        family: mapping_rules.make_family_advice_fn(family)
        for family in ("dynamic_clicking", "continuous_tracking", "target_switching")
    }
    assert all(callable(closure) for closure in closures.values())
    with pytest.raises(ValueError, match="unknown family"):
        mapping_rules.make_family_advice_fn("static_clicking")

    analysis = {
        "metrics": {
            "dynamic_clicking.normalized_click_error": {
                "availability": "available", "value": 0.42,
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {"dynamic_clicking.normalized_click_error": 0.20},
        },
        "processed_rows": [],
        "limitations": [],
    }
    monkeypatch.setattr(
        knowledge_active, "load_active_mapping", lambda: (None, None),
    )
    assert closures["dynamic_clicking"](analysis) == (
        build_dynamic_clicking_candidate_advice(analysis)
    )
    assert mapping_rules.last_family_diagnostics() == []
