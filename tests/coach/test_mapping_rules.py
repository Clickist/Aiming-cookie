"""WP-02: coach_mapping.v1 schema validator + compiler tests (plan C1).

Fixtures build a minimal vocabulary and registry in-test so the suite never
depends on the parallel v13 session's registry state. Coverage: the valid
full-field document (rewritten from the four C1 examples), per-category
missing-required fields, out-of-vocabulary tokens, dangling / inactive
expected_entry_ref, inverted bands, scale caps (static 32 / per-family 32 /
total 128), depth overrun, field smuggling, schema_version mismatch, unknown
family keys, malformed root_cause triples, and compile-cache key hit plus
invalidation on file change.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from kovaak_tracker.coach import mapping_rules
from kovaak_tracker.coach.mapping_rules import MappingValidationError


# ---------------------------------------------------------------------------
# Fixtures (minimal, self-contained)
# ---------------------------------------------------------------------------


def _vocabulary() -> dict:
    return {
        "schema_version": "coach_mapping_vocabulary.v1",
        "signals": ["decel_frac high", "sparc low", "dynamic click error high"],
        "metric_keys": [
            "cm_per_360",
            "continuous_tracking.sparc",
            "continuous_tracking.target_relative_error_px",
            "continuous_tracking.time_in_radius_ratio",
            "decel_frac",
            "dynamic_clicking.normalized_click_error",
            "sparc",
        ],
        "knowledge_metric_tokens": ["metric:normalized_click_error", "metric:sparc"],
        "observation_refs": ["event.dynamic_click", "metric.smoothness", "metric.terminal_control"],
        "limitation_tokens": [
            "click_geometry_visible_radius_conditioned",
            "threshold_requires_product_calibration",
        ],
        "row_fields": ["normalized_click_error", "sparc"],
        "row_classifications": ["observable_target_switch"],
        "row_filters": ["observable_switch_chain"],
        "enums": {
            "severity": ["info", "watch", "fix"],
            "direction": ["higher", "lower", "absolute_higher"],
            "claim_level": [
                "deterministic_rule", "research_supported", "community_practice",
                "community_consensus", "experimental",
            ],
            "op": [">", "<", ">=", "<=", "in_band", "ratio_to_ref_lt", "metric_version_not_in"],
            "input": ["self_summary", "reference_summary", "settings"],
        },
    }


def _registry() -> dict:
    return {
        "registry_version": "fixture.v1",
        "entries": [
            {"entry_id": "example.entry", "entry_version": 3, "status": "active"},
            {"entry_id": "example.entry", "entry_version": 2, "status": "retired"},
        ],
    }


def _doc() -> dict:
    """Valid full-field document, rewritten from the four C1 example rules."""
    return {
        "schema_version": "coach_mapping.v1",
        "static_clicking": [
            {  # C1 example 1 (static threshold), extended with a settings channel
                "signal": "decel_frac high",
                "severity": "fix",
                "text": "减速占比 0.65+：速度峰值后用了较长时间完成减速。",
                "plain_language_meaning": "速度峰值后用了较长时间完成减速",
                "expected_result": "decel_frac 向个体校准目标靠近",
                "claim_level": "experimental",
                "metric_refs": ["decel_frac"],
                "limitations": ["threshold_requires_product_calibration"],
                "observation_ref": "metric.terminal_control",
                "conditions": [
                    {"input": "self_summary", "metric": "decel_frac",
                     "stat": "med", "op": ">", "value": 0.65},
                    {"input": "settings", "metric": "cm_per_360", "op": "<", "value": 25.0},
                ],
                "prescriptions": [
                    {
                        "scenario": "pasu",
                        "reason": "练完整的加速→减速，接近目标时果断完成制动",
                        "cue": "把减速段当一次独立动作完成",
                        "purpose": "改善减速段占比",
                        "target_metrics": ["decel_frac"],
                        "expected_direction": ["decel_frac 向个体校准目标靠近"],
                        "retest_after": "在相同场景、设置和证据质量下复测",
                        "stop_or_adjust_rule": "若目标指标未改善或准确率明显恶化，停止调整并恢复原练法",
                        "source_level": "community_consensus",
                    }
                ],
            },
            {  # C1 example 2 (sparc version gate)
                "signal": "sparc low",
                "severity": "fix",
                "text": "减速段平滑度 SPARC 偏低：速度轮廓快速波动较多。",
                "claim_level": "experimental",
                "metric_refs": ["sparc"],
                "observation_ref": "metric.terminal_control",
                "conditions": [
                    {"input": "self_summary", "metric": "sparc", "op": "<", "value": -5.0},
                    {"input": "self_summary", "metric": "sparc",
                     "op": "metric_version_not_in", "value": ["native_flicking.sparc.v2"]},
                ],
            },
        ],
        "families": {
            "continuous_tracking": [
                {  # C1 example 3 (tracking candidate + composite guardrails)
                    "signal": "sparc low",
                    "metric": "continuous_tracking.sparc",
                    "row_field": "sparc",
                    "direction": "lower",
                    "knowledge_metric_ref": "metric:sparc",
                    "expected_entry_ref": "knowledge:example.entry@3",
                    "observation_ref": "metric.smoothness",
                    "requires_metric_availability": "available",
                    "blocking_limitations": ["threshold_requires_product_calibration"],
                    "guardrails": {"all": [
                        {"metric": "continuous_tracking.target_relative_error_px", "op": "<=baseline"},
                        {"metric": "continuous_tracking.time_in_radius_ratio", "op": ">=baseline"},
                    ]},
                    "claim_level": "research_supported",
                    "requested_knowledge_sections": ["definition", "cue"],
                }
            ],
            "dynamic_clicking": [
                {  # C1 example 4 (dynamic candidate + blocking limitations)
                    "signal": "dynamic click error high",
                    "metric": "dynamic_clicking.normalized_click_error",
                    "row_field": "normalized_click_error",
                    "direction": "higher",
                    "knowledge_metric_ref": "metric:normalized_click_error",
                    "observation_ref": "event.dynamic_click",
                    "blocking_limitations": ["click_geometry_visible_radius_conditioned"],
                }
            ],
            "target_switching": [],
        },
        "archetypes": [
            {"id": "long_decel", "label": "急加速-长减速型",
             "conditions": {"decel_frac high": 1.0, "sparc low": 0.5}, "positive": False},
            {"id": "fluid_precise", "label": "流体精度型", "conditions": {}, "positive": True},
        ],
        "root_causes": {
            "decel_frac high": [
                "减速阶段偏长",
                "证据只能说明减速阶段偏长",
                "练完整加减速，接近目标时果断完成制动",
            ],
        },
    }


def _mutated(mutator) -> dict:
    doc = _doc()
    mutator(doc)
    return doc


def _validated(doc: dict) -> None:
    return mapping_rules.validate_mapping(doc, vocabulary=_vocabulary(), registry=_registry())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_full_mapping_passes():
    assert _validated(_doc()) is None


def test_compile_normalizes_conditions_and_defaults():
    doc = _doc()
    compiled = mapping_rules.compile_mapping(doc)

    assert compiled.schema_version == "coach_mapping.v1"
    assert compiled.total_rule_count == 4
    assert len(compiled.static_rules) == 2

    first = compiled.static_rules[0]
    # settings-channel condition carries no stat -> defaulted to "med"
    assert first.conditions[1].input == "settings"
    assert first.conditions[1].stat == "med"
    assert first.conditions[1].value == 25.0
    assert first.conditions[0].stat == "med"
    prescription = first.prescriptions[0]
    assert prescription.source_level == "community_consensus"
    assert prescription.target_metrics == ("decel_frac",)

    second = compiled.static_rules[1]
    assert second.plain_language_meaning == ""
    assert second.observation_ref == "metric.terminal_control"
    assert second.prescriptions == ()
    assert second.limitations == ()

    tracking = compiled.family_rules["continuous_tracking"][0]
    assert tracking.claim_level == "research_supported"
    assert tracking.expected_entry_ref == "knowledge:example.entry@3"
    assert tracking.guardrails == (
        ("continuous_tracking.target_relative_error_px", "<=baseline"),
        ("continuous_tracking.time_in_radius_ratio", ">=baseline"),
    )
    assert tracking.requested_knowledge_sections == ("definition", "cue")

    dynamic = compiled.family_rules["dynamic_clicking"][0]
    assert dynamic.family == "dynamic_clicking"
    assert dynamic.claim_level == "deterministic_rule"
    assert dynamic.expected_entry_ref is None
    assert dynamic.requires_metric_availability == "available"
    assert dynamic.guardrails == ()
    assert dynamic.row_filter is None
    assert dynamic.blocking_limitations == frozenset(
        {"click_geometry_visible_radius_conditioned"}
    )
    assert dynamic.requested_knowledge_sections == (
        mapping_rules.DEFAULT_REQUESTED_KNOWLEDGE_SECTIONS
    )

    assert compiled.family_rules["target_switching"] == ()
    assert compiled.archetypes[1].positive is True
    assert compiled.archetypes[1].conditions == {}
    assert compiled.archetypes[0].conditions == {"decel_frac high": 1.0, "sparc low": 0.5}
    assert compiled.root_causes["decel_frac high"] == (
        "减速阶段偏长", "证据只能说明减速阶段偏长", "练完整加减速，接近目标时果断完成制动",
    )
    assert compiled.source_path is None


def test_compile_prefers_in_band_tuple_shape():
    doc = _doc()
    doc["static_clicking"][0]["conditions"] = [
        {"input": "self_summary", "metric": "decel_frac", "op": "in_band", "value": [0.4, 0.65]},
    ]
    assert _validated(doc) is None
    compiled = mapping_rules.compile_mapping(doc)
    condition = compiled.static_rules[0].conditions[0]
    assert condition.value == (0.4, 0.65)


# ---------------------------------------------------------------------------
# Missing required fields (one test per category)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutator", [
    pytest.param(lambda d: d["static_clicking"][0].pop("signal"), id="static-signal"),
    pytest.param(lambda d: d["static_clicking"][0].pop("conditions"), id="static-conditions"),
    pytest.param(lambda d: d["families"]["continuous_tracking"][0].pop("row_field"),
                 id="family-row_field"),
    pytest.param(lambda d: d["families"]["dynamic_clicking"][0].pop("observation_ref"),
                 id="family-observation_ref"),
    pytest.param(lambda d: d["static_clicking"][0]["conditions"][0].pop("op"), id="condition-op"),
    pytest.param(lambda d: d["static_clicking"][0]["prescriptions"][0].pop("reason"),
                 id="prescription-reason"),
    pytest.param(lambda d: d["archetypes"][0].pop("positive"), id="archetype-positive"),
    pytest.param(lambda d: d.pop("root_causes"), id="top-root_causes"),
])
def test_missing_required_fields_rejected(mutator):
    with pytest.raises(MappingValidationError, match="missing required fields"):
        _validated(_mutated(mutator))


def test_unknown_fields_rejected():
    def add_field(doc: dict) -> None:
        doc["static_clicking"][0]["mode"] = "static"

    with pytest.raises(MappingValidationError, match="unknown fields"):
        _validated(_mutated(add_field))


# ---------------------------------------------------------------------------
# Vocabulary membership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutator", [
    pytest.param(lambda d: d["static_clicking"][0].__setitem__("signal", "made-up signal"),
                 id="signal"),
    pytest.param(lambda d: d["static_clicking"][0].__setitem__("metric_refs", ["not_a_metric"]),
                 id="metric_refs"),
    pytest.param(lambda d: d["static_clicking"][0].__setitem__("limitations", ["not_a_limitation"]),
                 id="static-limitations"),
    pytest.param(lambda d: d["static_clicking"][0].__setitem__("observation_ref", "metric.unknown"),
                 id="static-observation_ref"),
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0].__setitem__(
            "knowledge_metric_ref", "metric:unknown"),
        id="knowledge_metric_ref",
    ),
    pytest.param(lambda d: d["families"]["continuous_tracking"][0].__setitem__(
        "row_field", "not_a_row_field"), id="row_field"),
    pytest.param(lambda d: d["families"]["continuous_tracking"][0].__setitem__(
        "row_filter", "not_a_filter"), id="row_filter"),
    pytest.param(lambda d: d["families"]["dynamic_clicking"][0].__setitem__(
        "blocking_limitations", ["not_a_limitation"]), id="blocking_limitations"),
    pytest.param(lambda d: d["families"]["continuous_tracking"][0]["guardrails"]["all"][0]
                 .__setitem__("metric", "not_a_metric"), id="guardrail-metric"),
    pytest.param(lambda d: d["archetypes"][0]["conditions"].__setitem__("made-up signal", 1.0),
                 id="archetype-condition-signal"),
    pytest.param(lambda d: d["root_causes"].__setitem__("made-up signal", ["a", "b", "c"]),
                 id="root-cause-key"),
])
def test_out_of_vocabulary_tokens_rejected(mutator):
    with pytest.raises(MappingValidationError, match="frozen vocabulary"):
        _validated(_mutated(mutator))


def test_condition_metric_must_be_known():
    def set_metric(doc: dict) -> None:
        doc["static_clicking"][0]["conditions"][0]["metric"] = "not_a_metric"

    with pytest.raises(MappingValidationError, match="frozen vocabulary"):
        _validated(_mutated(set_metric))


def test_vocabulary_enum_drift_rejected():
    vocab = _vocabulary()
    vocab["enums"]["severity"] = ["info", "watch"]
    with pytest.raises(MappingValidationError, match="enums"):
        mapping_rules.validate_mapping(_doc(), vocabulary=vocab, registry=_registry())


# ---------------------------------------------------------------------------
# expected_entry_ref cross-check
# ---------------------------------------------------------------------------


def test_dangling_expected_entry_ref_rejected():
    def set_dangling(doc: dict) -> None:
        doc["families"]["continuous_tracking"][0]["expected_entry_ref"] = (
            "knowledge:example.entry@99"
        )

    with pytest.raises(MappingValidationError, match="cannot be resolved"):
        _validated(_mutated(set_dangling))


def test_retired_expected_entry_ref_rejected():
    def set_retired(doc: dict) -> None:
        doc["families"]["continuous_tracking"][0]["expected_entry_ref"] = (
            "knowledge:example.entry@2"
        )

    with pytest.raises(MappingValidationError, match="not an active registry entry"):
        _validated(_mutated(set_retired))


def test_malformed_expected_entry_ref_rejected():
    def set_malformed(doc: dict) -> None:
        doc["families"]["continuous_tracking"][0]["expected_entry_ref"] = "example.entry@3"

    with pytest.raises(MappingValidationError, match="knowledge entry reference"):
        _validated(_mutated(set_malformed))


# ---------------------------------------------------------------------------
# Numeric sanity
# ---------------------------------------------------------------------------


def test_band_inverted_rejected():
    def invert(doc: dict) -> None:
        doc["static_clicking"][0]["conditions"] = [
            {"input": "self_summary", "metric": "decel_frac",
             "op": "in_band", "value": [0.65, 0.40]},
        ]

    with pytest.raises(MappingValidationError, match="lo < hi"):
        _validated(_mutated(invert))


def test_band_degenerate_rejected():
    def degenerate(doc: dict) -> None:
        doc["static_clicking"][0]["conditions"] = [
            {"input": "self_summary", "metric": "decel_frac",
             "op": "in_band", "value": [0.5, 0.5]},
        ]

    with pytest.raises(MappingValidationError, match="lo < hi"):
        _validated(_mutated(degenerate))


@pytest.mark.parametrize("mutator, match", [
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"][0].__setitem__("value", "0.65"),
        "value must be a finite number", id="comparison-string"),
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"][0].__setitem__("value", None),
        "value must be a finite number", id="comparison-null"),
    pytest.param(
        lambda d: d["static_clicking"][1]["conditions"][1].__setitem__(
            "value", "native_flicking.sparc.v2"),
        "must be a list", id="version-gate-string"),
    pytest.param(
        lambda d: d["static_clicking"][1]["conditions"][1].__setitem__("value", []),
        "must not be empty", id="version-gate-empty"),
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"][0].__setitem__("stat", "p75"),
        "stat must", id="stat-p75"),
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"][0].__setitem__("op", "=="),
        "not a v1 operator", id="unknown-op"),
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"][0].__setitem__("input", "baseline"),
        "not a v1 input channel", id="unknown-input"),
    pytest.param(
        lambda d: d["static_clicking"][0]["conditions"].append(
            {"input": "reference_summary", "metric": "sparc",
             "op": "ratio_to_ref_lt", "value": -0.5}),
        "positive number", id="ratio-nonpositive"),
])
def test_condition_value_sanity_rejected(mutator, match):
    with pytest.raises(MappingValidationError, match=match):
        _validated(_mutated(mutator))


# ---------------------------------------------------------------------------
# Scale caps
# ---------------------------------------------------------------------------


def _static_template() -> dict:
    return copy.deepcopy(_doc()["static_clicking"][1])


def _family_template() -> dict:
    return copy.deepcopy(_doc()["families"]["dynamic_clicking"][0])


def test_static_rules_over_32_rejected():
    doc = _doc()
    doc["static_clicking"] = [_static_template() for _ in range(33)]
    with pytest.raises(MappingValidationError, match="static_clicking exceeds"):
        _validated(doc)


def test_family_rules_over_32_rejected():
    doc = _doc()
    doc["families"]["continuous_tracking"] = [_family_template() for _ in range(33)]
    with pytest.raises(MappingValidationError, match="families.continuous_tracking exceeds"):
        _validated(doc)


def test_total_rules_over_128_rejected():
    doc = _doc()
    doc["static_clicking"] = [_static_template() for _ in range(32)]
    doc["families"]["continuous_tracking"] = [_family_template() for _ in range(33)]
    doc["families"]["dynamic_clicking"] = [_family_template() for _ in range(32)]
    doc["families"]["target_switching"] = [_family_template() for _ in range(32)]
    with pytest.raises(MappingValidationError, match="total cap"):
        _validated(doc)


def test_total_rules_at_128_boundary_passes():
    doc = _doc()
    doc["static_clicking"] = [_static_template() for _ in range(32)]
    for family in ("continuous_tracking", "dynamic_clicking", "target_switching"):
        doc["families"][family] = [_family_template() for _ in range(32)]
    assert _validated(doc) is None


def test_archetypes_over_32_rejected():
    doc = _doc()
    doc["archetypes"] = [
        {"id": f"arch_{i}", "label": f"型{i}", "conditions": {}, "positive": True}
        for i in range(33)
    ]
    with pytest.raises(MappingValidationError, match="archetypes exceeds"):
        _validated(doc)


def test_root_causes_over_64_rejected():
    vocab = _vocabulary()
    vocab["signals"] = list(vocab["signals"]) + [f"generated signal {i}" for i in range(70)]
    doc = _doc()
    doc["root_causes"] = {f"generated signal {i}": ["a", "b", "c"] for i in range(65)}
    with pytest.raises(MappingValidationError, match="64-key cap"):
        mapping_rules.validate_mapping(doc, vocabulary=vocab, registry=_registry())


def test_conditions_and_prescriptions_count_caps():
    doc = _doc()
    doc["static_clicking"][0]["conditions"] = []
    with pytest.raises(MappingValidationError, match="conditions must carry"):
        _validated(doc)

    doc = _doc()
    base = doc["static_clicking"][0]["conditions"][0]
    doc["static_clicking"][0]["conditions"] = [copy.deepcopy(base) for _ in range(5)]
    with pytest.raises(MappingValidationError, match="conditions must carry"):
        _validated(doc)

    doc = _doc()
    template = doc["static_clicking"][0]["prescriptions"][0]
    doc["static_clicking"][0]["prescriptions"] = [copy.deepcopy(template) for _ in range(9)]
    with pytest.raises(MappingValidationError, match="prescription cap"):
        _validated(doc)


def test_duplicate_metric_refs_rejected():
    def duplicate(doc: dict) -> None:
        doc["static_clicking"][0]["metric_refs"] = ["decel_frac", "decel_frac"]

    with pytest.raises(MappingValidationError, match="duplicates"):
        _validated(_mutated(duplicate))


# ---------------------------------------------------------------------------
# Depth + smuggling
# ---------------------------------------------------------------------------


def test_depth_exceeded_rejected():
    doc = _doc()
    deep = ["bottom"]
    for _ in range(5):
        deep = [deep]
    doc["static_clicking"][0]["text"] = deep
    with pytest.raises(MappingValidationError, match="nesting limit"):
        _validated(doc)


@pytest.mark.parametrize("mutator, match", [
    pytest.param(
        lambda d: d["static_clicking"][0].__setitem__("command", "do something"),
        "unsafe fields", id="instruction-key"),
    pytest.param(
        lambda d: d["static_clicking"][0]["prescriptions"][0].__setitem__(
            "reason", "api_key: supersecretvalue123"),
        "unsafe text", id="secret-text"),
    pytest.param(
        lambda d: d["static_clicking"][0].__setitem__("text", "/absolute/path/instruction"),
        "unsafe text", id="path-text"),
    pytest.param(
        lambda d: d["static_clicking"][0].__setitem__("text", "长" * 1300),
        "overlong text", id="overlong-text"),
])
def test_field_smuggling_rejected(mutator, match):
    with pytest.raises(MappingValidationError, match=match):
        _validated(_mutated(mutator))


# ---------------------------------------------------------------------------
# Schema / families / root causes
# ---------------------------------------------------------------------------


def test_schema_version_mismatch_rejected():
    doc = _doc()
    doc["schema_version"] = "coach_mapping.v2"
    with pytest.raises(MappingValidationError, match="schema_version"):
        _validated(doc)


def test_unknown_family_key_rejected():
    doc = _doc()
    doc["families"]["flicking"] = []
    with pytest.raises(MappingValidationError, match="exactly the v1 family keys"):
        _validated(doc)


def test_missing_family_key_rejected():
    doc = _doc()
    del doc["families"]["dynamic_clicking"]
    with pytest.raises(MappingValidationError, match="exactly the v1 family keys"):
        _validated(doc)


@pytest.mark.parametrize("triple, match", [
    pytest.param(["只有一层"], "three copy strings", id="one-layer"),
    pytest.param(["a", "b", "c", "d"], "three copy strings", id="four-layers"),
    pytest.param("纯文本", "three copy strings", id="plain-string"),
    pytest.param(["症状", "物理", 3], "non-empty text", id="non-string-item"),
])
def test_root_cause_value_must_be_triple_text(triple, match):
    doc = _doc()
    doc["root_causes"]["decel_frac high"] = triple
    with pytest.raises(MappingValidationError, match=match):
        _validated(doc)


def test_root_cause_overlong_item_rejected():
    doc = _doc()
    doc["root_causes"]["decel_frac high"] = ["a", "b", "长" * 401]
    with pytest.raises(MappingValidationError, match="character limit"):
        _validated(doc)


# ---------------------------------------------------------------------------
# Family-specific combination sanity
# ---------------------------------------------------------------------------


def test_requires_metric_availability_only_accepts_available():
    def set_unavailable(doc: dict) -> None:
        doc["families"]["continuous_tracking"][0]["requires_metric_availability"] = "unavailable"

    with pytest.raises(MappingValidationError, match="requires_metric_availability"):
        _validated(_mutated(set_unavailable))


@pytest.mark.parametrize("mutator, match", [
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0]["guardrails"].__setitem__(
            "some", []),
        "guardrails must", id="missing-all"),
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0]["guardrails"].__setitem__(
            "all", []),
        "non-empty list", id="empty-all"),
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0]["guardrails"]["all"][0]
        .__setitem__("op", "<baseline"),
        "baseline operator", id="bad-guardrail-op"),
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0]["guardrails"]["all"][0]
        .__setitem__("metric", "continuous_tracking.sparc"),
        "own metric", id="circular-guardrail"),
    pytest.param(
        lambda d: d["families"]["continuous_tracking"][0]["guardrails"]["all"].append(
            {"metric": "continuous_tracking.sparc", "op": "<=baseline", "extra": 1}),
        "fields are invalid", id="guardrail-extra-field"),
])
def test_guardrail_combination_sanity_rejected(mutator, match):
    with pytest.raises(MappingValidationError, match=match):
        _validated(_mutated(mutator))


# ---------------------------------------------------------------------------
# Archetype sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutator, match", [
    pytest.param(
        lambda d: d["archetypes"][0]["conditions"].__setitem__("decel_frac high", 0.0),
        "0 < w <= 1", id="weight-zero"),
    pytest.param(
        lambda d: d["archetypes"][0]["conditions"].__setitem__("decel_frac high", 1.5),
        "0 < w <= 1", id="weight-over-one"),
    pytest.param(
        lambda d: d["archetypes"][1]["conditions"].__setitem__("decel_frac high", 1.0),
        "exactly when conditions are empty", id="positive-with-conditions"),
    pytest.param(
        lambda d: (d["archetypes"][0].__setitem__("conditions", {}),
                   d["archetypes"][0].__setitem__("positive", False)),
        "exactly when conditions are empty", id="empty-conditions-not-positive"),
    pytest.param(
        lambda d: d["archetypes"].append(copy.deepcopy(d["archetypes"][0])),
        "duplicates an earlier archetype", id="duplicate-id"),
])
def test_archetype_sanity_rejected(mutator, match):
    with pytest.raises(MappingValidationError, match=match):
        _validated(_mutated(mutator))


# ---------------------------------------------------------------------------
# Compile cache (key = path + mtime_ns + size)
# ---------------------------------------------------------------------------


def test_compile_cache_hit_and_invalidation(tmp_path: Path):
    mapping_path = tmp_path / "mapping.json"
    doc = _doc()
    mapping_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    first = mapping_rules.compile_mapping(doc, path=mapping_path)
    second = mapping_rules.compile_mapping(doc, path=mapping_path)
    assert first is second

    # Same byte size, different content, bumped mtime_ns -> cache must miss.
    # The replacement text is shorter, then an ASCII-space pad on an archetype
    # label restores the exact original byte size so only mtime_ns differs.
    changed = _doc()
    changed["static_clicking"][0]["text"] = "减速占比 0.65+：内容已变更。"
    payload = json.dumps(changed, ensure_ascii=False)
    pad = (
        len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        - len(payload.encode("utf-8"))
    )
    assert pad >= 0
    changed["archetypes"][0]["label"] = "急加速-长减速型" + " " * pad
    payload = json.dumps(changed, ensure_ascii=False)
    assert len(payload.encode("utf-8")) == len(
        json.dumps(doc, ensure_ascii=False).encode("utf-8")
    )
    mapping_path.write_text(payload, encoding="utf-8")
    stat_result = mapping_path.stat()
    os.utime(
        mapping_path,
        ns=(stat_result.st_atime_ns + 1_000_000, stat_result.st_mtime_ns + 1_000_000),
    )
    recompiled = mapping_rules.compile_mapping(changed, path=mapping_path)
    assert recompiled is not first
    assert recompiled.static_rules[0].text == "减速占比 0.65+：内容已变更。"

    # Growing the file (size change) invalidates too.
    changed["root_causes"]["sparc low"] = ["症状", "物理", "训练"]
    mapping_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    grew = mapping_rules.compile_mapping(changed, path=mapping_path)
    assert grew is not recompiled
    assert "sparc low" in grew.root_causes

    # Without a path there is no caching: fresh objects every call.
    no_path_a = mapping_rules.compile_mapping(doc)
    no_path_b = mapping_rules.compile_mapping(doc)
    assert no_path_a is not no_path_b
