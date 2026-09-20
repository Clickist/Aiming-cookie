"""WP-05: the family mapping engine must reproduce the frozen built-ins.

Runs the WP-03 family golden corpora (24 cases) through BOTH the frozen
built-in ``build_*_candidate_advice`` and the production
``mapping_rules.make_family_advice_fn`` closures (the official
``knowledge/mapping/official.v1.json`` through the mapping engine) and
asserts candidate-list deep equality — the C5 output contract behind the
worker wiring (``advice_fn=mapping_rules.make_family_advice_fn(...)``).

The one intentional difference: the dynamic corpus case
``empty_knowledge_refs_dropped_since_wp05`` (a registry query that returns
no entries). The frozen dynamic built-in keeps that candidate with empty
``knowledge_entry_refs``; the unified engine semantics drop it (fail-closed,
decided 2026-09-20, no switch).

Also pins the unified fail-closed semantics beyond the golden corpus: the
family-level ``outcome_only`` and metric-availability gates apply to every
family, an ``expected_entry_ref`` miss drops the rule, a rule-level error
drops only that rule while the analysis succeeds, and an empty family
section / missing / broken mapping falls back to the frozen built-in
builder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kovaak_tracker.advice_dynamic_clicking import (
    build_dynamic_clicking_candidate_advice,
)
from kovaak_tracker.advice_target_switching import (
    build_target_switching_candidate_advice,
)
from kovaak_tracker.advice_tracking import build_tracking_candidate_advice
from kovaak_tracker.coach import knowledge_active, knowledge_registry, mapping_rules

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_MAPPING_PATH = REPO_ROOT / "knowledge" / "mapping" / "official.v1.json"
VOCABULARY_PATH = REPO_ROOT / "knowledge" / "mapping" / "vocabulary.v1.json"
PINNED_REGISTRY_VERSION = "2026-09-12.v12"

_LOSS_COUNT_TRIGGERED = {
    "metrics": {"continuous_tracking.loss_count": {"value": 5.0}},
    "comparison": {
        "comparable": True,
        "baseline_metrics": {"continuous_tracking.loss_count": 2.0},
    },
    "processed_rows": [],
    "limitations": [],
}


@pytest.fixture(autouse=True)
def _pin_knowledge_registry_v12(monkeypatch):
    """Pin every registry load to ``2026-09-12.v12`` (same钉子 as the golden
    harness: v12 ships permanently with the product)."""
    original_load_registry = knowledge_registry.load_registry

    def _pinned_load_registry(path=None, *, registry_version=None):
        return original_load_registry(registry_version=PINNED_REGISTRY_VERSION)

    monkeypatch.setattr(knowledge_registry, "load_registry", _pinned_load_registry)
    monkeypatch.setattr(
        knowledge_active,
        "load_active_registry",
        lambda: original_load_registry(registry_version=PINNED_REGISTRY_VERSION),
    )


# ---------------------------------------------------------------------------
# Engine == built-in over the WP-03 family golden corpora
# ---------------------------------------------------------------------------

_FAMILIES = (
    ("continuous_tracking", "TRACKING_FAMILY_CASES", build_tracking_candidate_advice),
    ("dynamic_clicking", "DYNAMIC_FAMILY_CASES", build_dynamic_clicking_candidate_advice),
    ("target_switching", "SWITCHING_FAMILY_CASES", build_target_switching_candidate_advice),
)


@pytest.mark.parametrize(
    "family,cases_attr,builtin_builder",
    _FAMILIES,
    ids=[family for family, _cases, _builder in _FAMILIES],
)
def test_engine_matches_builtin_over_family_golden_corpus(
    family, cases_attr, builtin_builder, monkeypatch,
):
    from tests.coach import test_mapping_golden as golden

    cases = getattr(golden, cases_attr)
    assert cases
    advice_fn = mapping_rules.make_family_advice_fn(family)
    for case in cases:
        if case.get("simulate_empty_knowledge_refs"):
            with monkeypatch.context() as patch_context:
                patch_context.setattr(
                    golden.diagnosis_module,
                    "resolve_candidate_knowledge_refs",
                    golden._empty_refs_resolver,
                )
                builtin = builtin_builder(case["analysis"])
                engine = advice_fn(case["analysis"])
            # The intentional unification (decided 2026-09-20): the frozen
            # built-in keeps the empty-refs candidate, the engine drops it.
            assert len(builtin) == 1
            assert builtin[0]["knowledge_entry_refs"] == []
            assert engine == [], (
                f"engine must fail-closed drop {case['name']}"
            )
        else:
            builtin = builtin_builder(case["analysis"])
            engine = advice_fn(case["analysis"])
            assert engine == builtin, (
                f"engine != built-in for golden case {case['name']}"
            )


def test_family_golden_corpora_cover_twenty_four_cases():
    from tests.coach import test_mapping_golden as golden

    total = (
        len(golden.TRACKING_FAMILY_CASES)
        + len(golden.DYNAMIC_FAMILY_CASES)
        + len(golden.SWITCHING_FAMILY_CASES)
    )
    assert total == 24


# ---------------------------------------------------------------------------
# Official mapping document contract
# ---------------------------------------------------------------------------


def test_official_mapping_carries_the_eleven_migrated_family_rules():
    doc = json.loads(OFFICIAL_MAPPING_PATH.read_bytes())
    mapping_rules.validate_mapping(
        doc,
        vocabulary=json.loads(VOCABULARY_PATH.read_bytes()),
        registry=knowledge_registry.load_registry(),
    )
    families = doc["families"]
    assert [len(families[family]) for family in mapping_rules.FAMILIES] == [6, 3, 2]
    tracking = families["continuous_tracking"]
    assert [rule["signal"] for rule in tracking] == [
        "tracking lag high",
        "loss count high",
        "off target long",
        "accel mismatch high",
        "correction burden high",
        "sparc low",
    ]
    assert {rule["signal"] for rule in tracking if "guardrails" in rule} == {
        "correction burden high",
        "sparc low",
    }
    dynamic = families["dynamic_clicking"]
    assert [len(rule["blocking_limitations"]) for rule in dynamic] == [5, 4, 3]
    assert all("expected_entry_ref" not in rule for rule in dynamic)
    switching = families["target_switching"]
    assert all(rule["row_filter"] == "observable_switch_chain" for rule in switching)
    assert all(
        "near_transfer_retest" in rule["requested_knowledge_sections"]
        for rule in switching
    )


# ---------------------------------------------------------------------------
# Unified fail-closed semantics beyond the golden corpus
# ---------------------------------------------------------------------------


def test_outcome_only_gate_is_unified_across_families():
    """C1: the family-level ``outcome_only`` precondition is engine
    behavior, uniform for all three families. The frozen tracking built-in
    never checks ``support_status`` (the golden corpus never pins it); the
    engine does."""
    analysis = {"support_status": "outcome_only", **_LOSS_COUNT_TRIGGERED}
    builtin = build_tracking_candidate_advice(analysis)
    assert [candidate["signal"] for candidate in builtin] == ["loss count high"]
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(analysis)
    assert engine == []


def test_metric_availability_gate_is_unified_across_families():
    """C1: ``requires_metric_availability`` defaults to available, so an
    unavailable metric never triggers — including in tracking, whose frozen
    built-in reads the stale value."""
    analysis = {
        "metrics": {
            "continuous_tracking.loss_count": {
                "availability": "unavailable", "value": 5.0,
            },
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {"continuous_tracking.loss_count": 2.0},
        },
        "processed_rows": [],
        "limitations": [],
    }
    builtin = build_tracking_candidate_advice(analysis)
    assert [candidate["signal"] for candidate in builtin] == ["loss count high"]
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(analysis)
    assert engine == []


def test_expected_entry_ref_miss_drops_the_rule(monkeypatch):
    from kovaak_tracker.coach import diagnosis as diagnosis_module

    def _unrelated_refs(*, issue_signal, metric_refs):
        return diagnosis_module.CandidateKnowledgeRefs(
            registry_version=PINNED_REGISTRY_VERSION,
            entry_refs=["knowledge:tracking.some-other-entry@3"],
        )

    analysis = dict(_LOSS_COUNT_TRIGGERED)
    builtin = build_tracking_candidate_advice(analysis)
    assert [candidate["signal"] for candidate in builtin] == ["loss count high"]
    monkeypatch.setattr(
        diagnosis_module, "resolve_candidate_knowledge_refs", _unrelated_refs,
    )
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(analysis)
    assert engine == []  # resolved refs lack the pinned expected entry ref


def test_rule_error_drops_only_that_rule(monkeypatch):
    from kovaak_tracker.coach import diagnosis as diagnosis_module

    real_resolver = diagnosis_module.resolve_candidate_knowledge_refs

    def exploding_resolver(*, issue_signal, metric_refs):
        if issue_signal == "loss count high":
            raise ValueError("boom")
        return real_resolver(issue_signal=issue_signal, metric_refs=metric_refs)

    analysis = {
        "metrics": {
            "continuous_tracking.phase_lag_ms": {"value": 24.0},
            "continuous_tracking.loss_count": {"value": 5.0},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "continuous_tracking.phase_lag_ms": 12.0,
                "continuous_tracking.loss_count": 2.0,
            },
        },
        "processed_rows": [],
        "limitations": [],
    }
    monkeypatch.setattr(
        diagnosis_module, "resolve_candidate_knowledge_refs", exploding_resolver,
    )
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(analysis)
    assert [candidate["signal"] for candidate in engine] == ["tracking lag high"]
    diagnostics = mapping_rules.last_family_diagnostics()
    assert len(diagnostics) == 1
    assert "loss count high" in diagnostics[0]


def test_evaluate_family_matches_dispatch_closure():
    """compile -> evaluate_family is the same engine the closure dispatches."""
    compiled = mapping_rules.compile_mapping(
        json.loads(OFFICIAL_MAPPING_PATH.read_bytes())
    )
    analysis = {
        "support_status": "supported",
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
    direct = mapping_rules.evaluate_family("dynamic_clicking", compiled, analysis)
    dispatched = mapping_rules.make_family_advice_fn("dynamic_clicking")(analysis)
    assert direct == dispatched
    assert [candidate["signal"] for candidate in direct] == [
        "dynamic click error high"
    ]


# ---------------------------------------------------------------------------
# Fail-closed fallback semantics
# ---------------------------------------------------------------------------


def test_missing_mapping_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(
        knowledge_active, "load_active_mapping", lambda: (None, None),
    )
    builtin = build_tracking_candidate_advice(_LOSS_COUNT_TRIGGERED)
    assert builtin
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(
        _LOSS_COUNT_TRIGGERED
    )
    assert engine == builtin
    assert mapping_rules.last_family_diagnostics() == []


def test_empty_family_section_falls_back_to_builtin(monkeypatch):
    empty_doc = {
        "schema_version": "coach_mapping.v1",
        "static_clicking": [],
        "families": {
            "continuous_tracking": [],
            "dynamic_clicking": [],
            "target_switching": [],
        },
        "archetypes": [],
        "root_causes": {},
    }
    monkeypatch.setattr(
        knowledge_active, "load_active_mapping", lambda: (empty_doc, None),
    )
    builtin = build_tracking_candidate_advice(_LOSS_COUNT_TRIGGERED)
    assert builtin
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(
        _LOSS_COUNT_TRIGGERED
    )
    assert engine == builtin
    assert mapping_rules.last_family_diagnostics() == []


def test_broken_mapping_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(
        knowledge_active,
        "load_active_mapping",
        lambda: ({"schema_version": "nope"}, None),
    )
    builtin = build_tracking_candidate_advice(_LOSS_COUNT_TRIGGERED)
    assert builtin
    engine = mapping_rules.make_family_advice_fn("continuous_tracking")(
        _LOSS_COUNT_TRIGGERED
    )
    assert engine == builtin
    diagnostics = mapping_rules.last_family_diagnostics()
    assert len(diagnostics) == 1
    assert "built-in fallback" in diagnostics[0]
