from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from kovaak_tracker.coach import knowledge_registry as registry


@pytest.fixture
def valid_registry() -> dict:
    return {
        "schema_version": "coach_knowledge_registry.v1",
        "registry_version": "2026-07-14.v1",
        "signal_aliases": {"reverse high": "reverse_ratio high"},
        "entries": [
            {
                "entry_id": "kinematics.reverse-ratio.definition",
                "entry_version": 1,
                "status": "active",
                "category": "metric_definition",
                "topics": ["stopping_corrections"],
                "signals": ["reverse_ratio high"],
                "metric_refs": ["metric:reverse_ratio"],
                "text": "reverse ratio 描述一次移动中反向运动相对总运动的比例。",
                "sources": [{
                    "source_ref": "product:metric:reverse-ratio",
                    "source_level": "product_contract",
                }],
                "max_claim_level": "deterministic_rule",
                "limitations": ["它不能单独证明身体或握持原因。"],
                "counterevidence": [],
                "supported_uses": ["definition", "mechanism"],
            },
            {
                "entry_id": "hypothesis.tension.release",
                "entry_version": 1,
                "status": "active",
                "category": "body_tension_hypothesis",
                "topics": ["body_tension"],
                "signals": ["reverse_ratio high"],
                "metric_refs": ["metric:reverse_ratio"],
                "text": "动作末段的力量释放方式可以作为单变量候选实验。",
                "sources": [{
                    "source_ref": "community:viscose:tension-budget",
                    "source_level": "personal_experience_unverified",
                }],
                "max_claim_level": "experimental",
                "limitations": ["没有 EMG、握力或其它身体传感器测量。"],
                "counterevidence": ["相同轨迹现象可能由多种动作策略产生。"],
                "supported_uses": ["candidate_hypothesis", "training_cue"],
            },
            {
                "entry_id": "retired.old",
                "entry_version": 1,
                "status": "retired",
                "category": "research",
                "topics": ["stopping_corrections"],
                "signals": [],
                "metric_refs": [],
                "text": "旧知识仅用于历史解析。",
                "sources": [{
                    "source_ref": "research:retired",
                    "source_level": "academic_peer_reviewed",
                }],
                "max_claim_level": "research_supported",
                "limitations": ["不进入默认检索。"],
                "counterevidence": [],
                "supported_uses": ["mechanism"],
            },
        ],
    }


def test_loads_packaged_registry_and_resolves_stable_refs():
    loaded = registry.load_registry()
    assert loaded["schema_version"] == "coach_knowledge_registry.v1"
    assert loaded["entries"]
    first = loaded["entries"][0]
    assert registry.entry_ref(first) == f"knowledge:{first['entry_id']}@{first['entry_version']}"


def test_validator_accepts_contract_and_returns_detached_normalized_data(valid_registry):
    normalized = registry.validate_registry(valid_registry)
    assert normalized == valid_registry
    assert normalized is not valid_registry
    normalized["entries"][0]["text"] = "changed"
    assert valid_registry["entries"][0]["text"] != "changed"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version="wrong"), "schema_version"),
        (lambda data: data.update(registry_version=""), "registry_version"),
        (lambda data: data["entries"].append(copy.deepcopy(data["entries"][0])), "duplicate entry version"),
        (lambda data: data["entries"][0].update(status="unknown"), "status"),
        (lambda data: data["entries"][0].update(limitations=[]), "limitations"),
        (lambda data: data["entries"][0].update(max_claim_level="measured"), "max_claim_level"),
        (lambda data: data["entries"][1].update(max_claim_level="community_consensus"), "experimental"),
        (lambda data: data["entries"][0]["sources"][0].update(source_level="community_consensus"), "community"),
        (lambda data: data["entries"][0].update(text="/Users/private/raw.csv"), "unsafe"),
        (lambda data: data["entries"][0].update(text="Bearer sk-secret-sentinel"), "unsafe"),
        (lambda data: data["entries"][0].update(raw_trace=[1, 2]), "fields"),
        (lambda data: data["signal_aliases"].update({"loop": "loop"}), "alias"),
    ],
)
def test_validator_fails_closed(valid_registry, mutation, message):
    mutation(valid_registry)
    with pytest.raises(registry.KnowledgeRegistryError, match=message):
        registry.validate_registry(valid_registry)


def test_validator_rejects_multiple_active_versions(valid_registry):
    newer = copy.deepcopy(valid_registry["entries"][0])
    newer["entry_version"] = 2
    valid_registry["entries"].append(newer)
    with pytest.raises(registry.KnowledgeRegistryError, match="multiple active"):
        registry.validate_registry(valid_registry)


def test_retrieval_is_bounded_stable_and_normalizes_alias(valid_registry):
    loaded = registry.validate_registry(valid_registry)
    results = registry.query_registry(
        loaded,
        topic="stopping_corrections",
        issue_signal="reverse high",
        metric_refs=["metric:reverse_ratio"],
        supported_use="definition",
    )
    assert [item["entry_id"] for item in results] == [
        "kinematics.reverse-ratio.definition",
        "hypothesis.tension.release",
    ]
    assert all(item["status"] == "active" for item in results)
    assert len(results) <= 3


def test_retrieval_requires_a_condition_and_never_falls_back_to_full_registry(valid_registry):
    loaded = registry.validate_registry(valid_registry)
    with pytest.raises(registry.KnowledgeRegistryError, match="query condition"):
        registry.query_registry(loaded)
    assert registry.query_registry(loaded, topic="unknown-topic") == []


def test_retrieval_limit_is_product_owned(valid_registry):
    loaded = registry.validate_registry(valid_registry)
    assert len(registry.query_registry(loaded, issue_signal="reverse_ratio high")) == 2
    with pytest.raises(TypeError):
        registry.query_registry(loaded, issue_signal="reverse_ratio high", limit=99)


def test_schema_accepts_the_valid_fixture_and_packaged_registry(valid_registry):
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v1.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v1.json").read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for value in (valid_registry, packaged):
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        assert errors == [], [error.message for error in errors[:5]]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["entries"][0].update(status="unknown"),
        lambda data: data["entries"][0].update(limitations=[]),
        lambda data: data["entries"][0].update(unexpected="value"),
        lambda data: data["entries"][1].update(max_claim_level="community_consensus"),
    ],
)
def test_schema_rejects_invalid_registry_shapes(valid_registry, mutation):
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v1.json").read_text(encoding="utf-8"))
    mutation(valid_registry)

    assert list(Draft202012Validator(schema).iter_errors(valid_registry))


def test_schema_and_migration_assets_are_json_and_versioned():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v1.json").read_text(encoding="utf-8"))
    audit = json.loads((root / "migration-audit.v1.json").read_text(encoding="utf-8"))
    assert schema["$id"] == "coach_knowledge_registry.schema.v1"
    assert audit["schema_version"] == "coach_knowledge_migration_audit.v1"


def _load_audit() -> dict:
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    return json.loads((root / "migration-audit.v1.json").read_text(encoding="utf-8"))


def test_full_migration_audit_covers_every_legacy_asset_exactly_once():
    from kovaak_tracker.coach.knowledge import KNOWLEDGE

    audit = _load_audit()
    rows = audit["sources"]
    actual = [(row["source_kind"], row["source_key"]) for row in rows]
    assert len(actual) == len(set(actual)) == 37 + 19 + 11
    expected = {
        *((row["source_kind"], row["source_key"]) for row in rows
          if row["source_kind"] == "python_agent_kb"),
        *(('python_signal_knowledge', signal) for signal in KNOWLEDGE),
        *(('typescript_seed', topic) for topic in {
            "movement_timing", "braking_linearity", "smoothness_sparc",
            "stopping_corrections", "submovements", "path_geometry",
            "speed_precision", "tracking_control", "practice_structure",
            "issue_training_cues", "settings_experiment",
        }),
    }
    assert set(actual) == expected


def test_migration_audit_actions_and_targets_are_valid():
    loaded = registry.load_registry()
    entry_ids = {entry["entry_id"] for entry in loaded["entries"]}
    allowed_actions = {"migrate", "rewrite", "merge", "experimental_only", "reject"}
    for row in _load_audit()["sources"]:
        assert row["action"] in allowed_actions
        assert isinstance(row["reason"], str) and row["reason"].strip()
        targets = row["target_entry_ids"]
        if row["action"] == "reject":
            assert targets == []
        else:
            assert targets and set(targets) <= entry_ids


def test_full_registry_covers_all_flicking_and_tracking_signals():
    from kovaak_tracker.coach.knowledge import KNOWLEDGE

    loaded = registry.load_registry()
    for signal in KNOWLEDGE:
        results = registry.query_registry(loaded, issue_signal=signal)
        assert results, signal
    tracking_signals = {
        "accuracy low", "loss count high", "off target long", "avg error high",
        "speed mismatch high", "accel mismatch high", "ptc high",
    }
    assert tracking_signals <= {
        signal for entry in loaded["entries"] for signal in entry["signals"]
    }


def test_full_registry_contains_required_knowledge_domains():
    loaded = registry.load_registry()
    categories = {entry["category"] for entry in loaded["entries"] if entry["status"] == "active"}
    assert {
        "metric_definition", "kinematic_mechanism", "diagnostic_scope", "research",
        "training_cue", "prescription_verification", "practice_structure",
        "body_tension_hypothesis", "settings_experiment", "limitation_counterevidence",
    } <= categories
    topics = {topic for entry in loaded["entries"] for topic in entry["topics"]}
    assert {
        "movement_timing", "braking_linearity", "smoothness_sparc",
        "stopping_corrections", "submovements", "path_geometry", "speed_precision",
        "tracking_control", "practice_structure", "issue_training_cues",
        "settings_experiment", "body_tension", "prescription_verification",
    } <= topics


def test_body_tension_and_settings_are_experimental_with_counterevidence():
    loaded = registry.load_registry()
    entries = [
        entry for entry in loaded["entries"]
        if entry["category"] in {"body_tension_hypothesis", "settings_experiment"}
    ]
    assert entries
    for entry in entries:
        assert entry["max_claim_level"] == "experimental"
        assert entry["limitations"]
        assert entry["counterevidence"]


def test_registry_rewrites_old_absolute_threshold_and_fixed_setting_claims():
    wire = json.dumps(registry.load_registry(), ensure_ascii=False)
    for forbidden in (
        "指标健康区间", "主流健康", "降 sens 5-10%", "70-80 cm/360",
        "每天 10 分钟", "SPARC 是运动平滑度金标准",
    ):
        assert forbidden not in wire


def test_verification_entries_define_retest_and_insufficient_evidence_behavior():
    loaded = registry.load_registry()
    entries = registry.query_registry(
        loaded,
        topic="prescription_verification",
        supported_use="verification",
    )
    assert entries
    text = " ".join(entry["text"] for entry in entries)
    assert "相同场景" in text
    assert "证据不足" in text


def test_legacy_python_knowledge_modules_are_registry_backed():
    from kovaak_tracker.coach.agent_kb import BY_TOPIC, KB
    from kovaak_tracker.coach.knowledge import KNOWLEDGE

    loaded = registry.load_registry()
    active = [entry for entry in loaded["entries"] if entry["status"] == "active"]
    assert len(KB) == len(active)
    assert all(chunk["entry_ref"].startswith("knowledge:") for chunk in KB)
    assert set(KNOWLEDGE) == {
        signal for entry in active for signal in entry["signals"]
    }
    assert "sparc" in BY_TOPIC
    assert BY_TOPIC["sparc"][0]["source_ref"]


def test_legacy_signal_fetch_returns_versioned_registry_entries():
    from kovaak_tracker.coach.agent_tools import make_fetch_knowledge

    result = make_fetch_knowledge()("reverse high")
    assert result["signal"] == "reverse_ratio high"
    assert 1 <= len(result["entries"]) <= 3
    assert all(item["entry_ref"].startswith("knowledge:") for item in result["entries"])
    assert all(item["max_claim_level"] != "measured" for item in result["entries"])
    assert result["registry_version"]


def test_legacy_source_specific_fetch_is_registry_backed_and_bounded():
    from kovaak_tracker.coach.agent_tools import make_fetch_kinematics

    result = make_fetch_kinematics()("sparc")
    assert result["topic"] == "sparc"
    assert result["entry_ref"].startswith("knowledge:")
    assert result["entry_version"] == 1
    assert result["limitations"]
