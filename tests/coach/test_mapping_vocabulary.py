"""Anti-drift tests for the frozen mapping vocabulary (WP-01, plan C7).

The vocabulary at ``knowledge/mapping/vocabulary.v1.json`` is the authoring
contract for ``coach_mapping.v1``. These tests pin three properties:

1. the committed file is exactly what ``scripts/export_mapping_vocabulary.py``
   derives from current code constants (a new signal/metric/token in code
   without regenerating the file turns the suite red);
2. running the exporter is idempotent (byte-identical output);
3. every frozen value is traceable to a real code constant (nothing invented).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "export_mapping_vocabulary.py"
VOCAB_PATH = REPO_ROOT / "knowledge" / "mapping" / "vocabulary.v1.json"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_mapping_vocabulary", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_doc() -> dict:
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


def _active_registry_facts() -> tuple[set[str], set[str]]:
    from kovaak_tracker.coach.knowledge_registry import load_registry

    signals: set[str] = set()
    tokens: set[str] = set()
    for entry in load_registry()["entries"]:
        if entry.get("status") != "active":
            continue
        signals.update(s for s in entry.get("signals") or [] if isinstance(s, str))
        tokens.update(t for t in entry.get("metric_refs") or [] if isinstance(t, str))
    return signals, tokens


def test_vocabulary_file_matches_code_derivation():
    exporter = _load_exporter()
    doc = exporter.build_vocabulary()
    assert _load_doc() == doc


def test_export_script_is_idempotent():
    exporter = _load_exporter()
    before = VOCAB_PATH.read_bytes()
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert VOCAB_PATH.read_bytes() == before


def test_structure_follows_c7_contract():
    doc = _load_doc()
    assert doc["schema_version"] == "coach_mapping_vocabulary.v1"
    assert set(doc) == {
        "schema_version",
        "signals",
        "metric_keys",
        "knowledge_metric_tokens",
        "observation_refs",
        "limitation_tokens",
        "row_fields",
        "row_classifications",
        "row_filters",
        "enums",
    }
    assert set(doc["enums"]) == {"severity", "direction", "claim_level", "op", "input"}
    # C1 operator/value enums; membership is the contract, not ordering.
    assert set(doc["enums"]["severity"]) == {"info", "watch", "fix"}
    assert set(doc["enums"]["direction"]) == {"higher", "lower", "absolute_higher"}
    assert set(doc["enums"]["claim_level"]) == {
        "deterministic_rule",
        "research_supported",
        "community_practice",
        "community_consensus",
        "experimental",
    }
    assert set(doc["enums"]["op"]) == {
        ">",
        "<",
        ">=",
        "<=",
        "in_band",
        "ratio_to_ref_lt",
        "metric_version_not_in",
    }
    assert set(doc["enums"]["input"]) == {"self_summary", "reference_summary", "settings"}
    for section in (
        "signals",
        "metric_keys",
        "knowledge_metric_tokens",
        "observation_refs",
        "limitation_tokens",
        "row_fields",
        "row_classifications",
        "row_filters",
    ):
        values = doc[section]
        assert isinstance(values, list) and values, section
        assert len(values) == len(set(values)), section


def test_signals_cover_analyzer_and_registry_domains():
    from kovaak_tracker import advice, advice_tracking
    from kovaak_tracker.coach import profiles

    doc = _load_doc()
    signals = set(doc["signals"])

    expected = set(advice._SIGNAL_METRICS) | set(advice_tracking._PLAIN_MEANINGS)
    for archetype in profiles.ARCHETYPES:
        expected.update(archetype["conditions"])
    expected.update(profiles.ROOT_CAUSES)
    registry_signals, _ = _active_registry_facts()
    expected |= registry_signals

    missing = expected - signals
    assert not missing, f"signals missing from vocabulary: {sorted(missing)}"


def test_metric_keys_cover_definitions_settings_and_family_tables():
    from kovaak_tracker import advice_dynamic_clicking, advice_tracking, metric_definitions

    exporter = _load_exporter()
    doc = _load_doc()
    keys = set(doc["metric_keys"])

    expected = set(metric_definitions.METRIC_DEFINITIONS) | {"cm_per_360"}
    for spec in advice_tracking._TRACKING_CANDIDATES:
        expected.add(spec[0])
    for spec in advice_dynamic_clicking._CANDIDATES:
        expected.add(spec[0])
    for spec in exporter._switching_candidate_specs():
        expected.add(spec[0])

    missing = expected - keys
    assert not missing, f"metric keys missing from vocabulary: {sorted(missing)}"


def test_knowledge_metric_tokens_cover_registry_and_family_tokens():
    exporter = _load_exporter()
    doc = _load_doc()
    _, registry_tokens = _active_registry_facts()
    expected = registry_tokens | exporter._candidate_facts()["knowledge_metric_tokens"]
    missing = expected - set(doc["knowledge_metric_tokens"])
    assert not missing, f"knowledge metric tokens missing: {sorted(missing)}"


def test_observation_refs_cover_diagnosis_and_family_constants():
    from kovaak_tracker.coach.diagnosis import _STATIC_OBSERVATION_REFS

    exporter = _load_exporter()
    doc = _load_doc()
    refs = set(doc["observation_refs"])
    assert set(_STATIC_OBSERVATION_REFS.values()) <= refs
    assert exporter._candidate_facts()["observation_refs"] <= refs


def test_limitation_tokens_appear_verbatim_in_source_modules():
    exporter = _load_exporter()
    doc = _load_doc()
    corpora = {
        rel_path: (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for rel_path in exporter.LIMITATION_SOURCES
    }
    for token in doc["limitation_tokens"]:
        assert any(token in text for text in corpora.values()), f"untraceable token: {token}"
    for token, rel_path in exporter.EXTRA_LIMITATION_TOKENS.items():
        assert token in (REPO_ROOT / rel_path).read_text(encoding="utf-8"), token


def test_row_sections_match_family_constants():
    exporter = _load_exporter()
    doc = _load_doc()
    facts = exporter._candidate_facts()

    assert set(doc["row_fields"]) == facts["row_fields"]
    assert set(doc["row_filters"]) == {"observable_switch_chain"}

    corpora = [
        (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for rel_path in exporter.ROW_CLASSIFICATION_SOURCES
    ]
    for value in doc["row_classifications"]:
        assert any(value in text for text in corpora), f"untraceable classification: {value}"


def test_row_key_field_names_are_not_frozen_as_domain_values():
    # Regression pin: the row-key exclusion set {"event_ref", "row_kind",
    # "start_ms", "end_ms", "limitations"} (target_switching_analysis.py)
    # must never leak its member field names into the frozen domains.
    doc = _load_doc()
    field_names = {"start_ms", "end_ms", "event_ref", "row_kind"}
    assert not field_names & set(doc["limitation_tokens"])
    assert not field_names & set(doc["row_classifications"])
