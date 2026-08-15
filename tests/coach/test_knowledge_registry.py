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
    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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
    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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

    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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
    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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
    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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
    wire = json.dumps(
        registry.load_registry(registry_version="2026-07-14.v1"),
        ensure_ascii=False,
    )
    for forbidden in (
        "指标健康区间", "主流健康", "降 sens 5-10%", "70-80 cm/360",
        "每天 10 分钟", "SPARC 是运动平滑度金标准",
    ):
        assert forbidden not in wire


def test_verification_entries_define_retest_and_insufficient_evidence_behavior():
    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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

    loaded = registry.load_registry(registry_version="2026-07-14.v1")
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
    assert result["registry_version"] == "2026-08-16.v8"
    assert all(item["section_refs"] for item in result["entries"])
    assert all(item["claim_refs"] for item in result["entries"])
    assert all(
        len(item["section_refs"]) == len(item["claim_refs"])
        for item in result["entries"]
    )


def test_legacy_source_specific_fetch_is_registry_backed_and_bounded():
    from kovaak_tracker.coach.agent_tools import make_fetch_kinematics

    result = make_fetch_kinematics()("sparc")
    assert result["topic"] == "sparc"
    assert result["entry_ref"].startswith("knowledge:")
    assert result["entry_version"] == 1
    assert result["limitations"]


def test_v4_is_active_while_v1_historical_registry_resolves_exactly():
    active = registry.load_registry()
    historical = registry.load_registry(registry_version="2026-07-14.v1")

    assert active["schema_version"] == "coach_knowledge_registry.v3"
    assert active["registry_version"] != historical["registry_version"]
    assert historical["schema_version"] == "coach_knowledge_registry.v1"
    assert len(historical["entries"]) == 43
    with pytest.raises(registry.KnowledgeRegistryError, match="unknown registry version"):
        registry.load_registry(registry_version="unknown.v99")


def test_historical_entry_resolution_never_guesses_across_registries():
    historical = registry.load_registry(registry_version="2026-07-14.v1")
    historical_ref = registry.entry_ref(historical["entries"][0])
    resolved = registry.resolve_entry(
        registry_version=historical["registry_version"],
        entry_reference=historical_ref,
    )

    assert resolved == historical["entries"][0]
    with pytest.raises(registry.KnowledgeRegistryError, match="unknown knowledge entry"):
        registry.resolve_entry(
            registry_version=registry.load_registry()["registry_version"],
            entry_reference=historical_ref,
        )


def test_v2_schema_registry_and_family_contract_fixture_are_complete():
    root = Path(__file__).resolve().parents[2]
    knowledge_root = root / "knowledge" / "coach"
    schema = json.loads((knowledge_root / "schema.v2.json").read_text(encoding="utf-8"))
    packaged = json.loads((knowledge_root / "registry.v2.json").read_text(encoding="utf-8"))
    fixture = json.loads(
        (root / "tests" / "fixtures" / "coach" / "knowledge" / "family-contracts.v2.json")
        .read_text(encoding="utf-8")
    )

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(packaged),
        key=lambda error: list(error.path),
    )
    assert errors == [], [error.message for error in errors[:5]]
    assert registry.validate_registry(packaged) == registry.load_registry(
        registry_version="2026-07-22.v2"
    )
    assert fixture["schema_version"] == "coach_knowledge_family_contracts.v2"
    contracts = {item["family"]: item for item in fixture["families"]}
    assert set(contracts) == {
        "static_clicking",
        "dynamic_clicking",
        "predictable_tracking",
        "reactive_tracking",
        "control_tracking",
        "target_switching",
        "movement_aiming",
    }
    active_ids = {
        entry["entry_id"] for entry in packaged["entries"] if entry["status"] == "active"
    }
    for family, contract in contracts.items():
        assert contract["required_observations"], family
        assert contract["knowledge_entry_ids"], family
        assert set(contract["knowledge_entry_ids"]) <= active_ids


def test_v2_entries_are_complete_independent_coaching_records():
    loaded = registry.load_registry(registry_version="2026-07-28.v3")
    singular_sections = {
        "definition",
        "scope",
        "expected_direction",
        "cue",
        "matched_retest",
        "near_transfer_retest",
    }
    repeated_sections = {"mechanisms", "dose_guardrail", "stop_adjust_rule"}
    for entry in loaded["entries"]:
        if entry["status"] != "active":
            continue
        assert entry["family_scope"]
        assert entry["observation_refs"]
        assert entry["quality_prerequisites"]
        source_refs = set(entry["sources"])
        sections = []
        for name in singular_sections:
            section = entry[name]
            if section != "not_applicable":
                sections.append((name, section))
        for name in repeated_sections:
            value = entry[name]
            if value != "not_applicable":
                sections.extend((name, section) for section in value)
        for name, section in sections:
            assert section["section_ref"].startswith(f"{entry['entry_id']}.")
            assert registry.claim_ref(section) == f"claim:{section['section_ref']}"
            assert section["source_refs"]
            assert set(section["source_refs"]) <= source_refs
            assert section["text"], (entry["entry_id"], name)


def test_v2_research_claims_use_primary_sources_and_sources_cover_entry_families():
    loaded = registry.load_registry(registry_version="2026-07-28.v3")
    assert all(
        source["source_ref"] != "research.task10-assessment"
        for source in loaded["sources"]
    )
    assert all(
        source["author_or_org"] != "Aiming Cookie research assessment"
        for source in loaded["sources"]
        if source["source_level"] == "academic_peer_reviewed"
    )

    invalid = copy.deepcopy(loaded)
    source = next(
        item for item in invalid["sources"]
        if item["source_ref"] == "community.rawinput-tracking"
    )
    source["applicability"] = ["predictable_tracking"]
    with pytest.raises(registry.KnowledgeRegistryError, match="family scope"):
        registry.validate_registry(invalid)


def test_v2_migration_audit_disposes_every_v1_entry_once():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    v1 = json.loads((root / "registry.v1.json").read_text(encoding="utf-8"))
    v2 = json.loads((root / "registry.v2.json").read_text(encoding="utf-8"))
    audit = json.loads((root / "migration-audit.v2.json").read_text(encoding="utf-8"))
    rows = audit["entries"]

    assert audit["schema_version"] == "coach_knowledge_migration_audit.v2"
    assert len(rows) == len(v1["entries"]) == 43
    assert len({(row["v1_entry_id"], row["v1_entry_version"]) for row in rows}) == 43
    assert {row["action"] for row in rows} <= {
        "carry_forward", "rewrite", "split", "retire", "reject",
    }
    v2_ids = {entry["entry_id"] for entry in v2["entries"]}
    for row in rows:
        assert row["reason"].strip()
        if row["action"] in {"retire", "reject"}:
            assert row["target_entry_ids"] == []
        else:
            assert row["target_entry_ids"]
            assert set(row["target_entry_ids"]) <= v2_ids


def test_v2_movement_outcome_only_cannot_prescribe_or_imply_measured_facts():
    entries = registry.query_registry(
        registry.load_registry(registry_version="2026-07-28.v3"),
        topic="movement_aiming",
    )
    assert entries
    for entry in entries:
        for section_name in (
            "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
        ):
            assert entry[section_name] == "not_applicable"
        assert "severity" not in json.dumps(entry)
        assert "measured" not in {
            section["claim_level"]
            for section in (entry["definition"], entry["scope"], entry["expected_direction"])
        }


def test_v3_scenario_prescriptions_are_explicit_and_limited_to_reviewed_families():
    loaded = registry.load_registry(registry_version="2026-07-28.v3")

    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v2.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v3.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(packaged)) == []

    assert loaded["registry_version"] == "2026-07-28.v3"
    expected = {
        "static.flicking-terminal-control": "scenario:static.1wall_6targets_small@1",
        "dynamic.click-error-and-acquisition": "scenario:dynamic.pasu_small_reload@1",
        "dynamic.speed-matching-and-reading": "scenario:dynamic.pasu_small_reload@1",
        "tracking.predictable-speed-matching": "scenario:tracking.whj_smooth_strafe_sphere_easy@1",
        "switching.transition-and-arrival": "scenario:switching.beants_larger@1",
    }
    for entry in loaded["entries"]:
        prescription = entry["scenario_prescription"]
        if entry["entry_id"] in expected:
            assert prescription["scenario_profile_ref"] == expected[entry["entry_id"]]
            assert prescription["practice_condition"]
            assert prescription["review_after"] == "next comparable practice session"
            assert prescription["source_refs"]
            assert prescription["claim_level"] == "experimental"
        else:
            assert prescription == "not_applicable"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["entries"][0].pop("scenario_prescription"), "scenario_prescription"),
        (lambda data: data["entries"][0]["scenario_prescription"].update(scenario_profile_ref="scenario:bad/ref"), "scenario_profile_ref"),
        (lambda data: data["entries"][0]["scenario_prescription"].update(source_refs=["unknown.source"]), "source_refs"),
        (lambda data: data["entries"][0]["scenario_prescription"].update(review_after="later"), "review_after"),
        (lambda data: data["entries"][0]["scenario_prescription"].update(claim_level="research_supported"), "ceiling"),
        (lambda data: data["sources"][0]["supports_sections"].remove("scenario_prescription"), "support"),
    ],
)
def test_v3_rejects_invalid_scenario_prescription_contract(mutation, message):
    invalid = copy.deepcopy(
        registry.load_registry(registry_version="2026-07-28.v3")
    )
    mutation(invalid)

    with pytest.raises(registry.KnowledgeRegistryError, match=message):
        registry.validate_registry(invalid)


@pytest.mark.parametrize("scenario_profile_ref", [
    "scenario:movement.unreviewed@99",
    "scenario:static.retired@1",
    "scenario:static.1wall_6targets_small@99",
])
def test_v3_rejects_scenario_prescriptions_outside_active_scenario_profiles(
    scenario_profile_ref,
):
    invalid = copy.deepcopy(
        registry.load_registry(registry_version="2026-07-28.v3")
    )
    invalid["entries"][0]["scenario_prescription"]["scenario_profile_ref"] = (
        scenario_profile_ref
    )

    with pytest.raises(registry.KnowledgeRegistryError, match="active scenario"):
        registry.validate_registry(invalid)


_RAWINPUT_V4_ENTRY_IDS = {
    "community.friction-and-surface",
    "community.task-specific-sensitivity",
    "community.linear-clicking-strategy",
    "community.flick-stopping-strategies",
    "community.adaptive-mouse-grip",
    "community.score-farming-context",
    "community.aim-trainer-transfer",
    "community.sensitivity-variation",
    "community.mouse-acceleration-context",
}


def test_v4_schema_python_and_article_sources_are_complete():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v3.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v4.json").read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(packaged),
        key=lambda error: list(error.path),
    )
    assert errors == [], [error.message for error in errors[:5]]
    validated = registry.load_registry(registry_version="2026-07-29.v4")
    assert validated == registry.validate_registry(packaged)
    assert validated["schema_version"] == "coach_knowledge_registry.v3"
    article_entries = {
        entry["entry_id"]: entry
        for entry in validated["entries"]
        if entry["entry_id"] in _RAWINPUT_V4_ENTRY_IDS
    }
    assert set(article_entries) == _RAWINPUT_V4_ENTRY_IDS
    article_sources = {
        source["author_or_org"]
        for source in validated["sources"]
        if source["source_ref"].startswith("community.rawinput.article.")
    }
    assert article_sources == {"immie", "Keeah", "MattyOW", "pinguefy / Viscose", "Viscose"}


def test_v5_adds_revisable_mouse_fit_and_input_latency_differential_intake():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v3.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v5.json").read_text(encoding="utf-8"))

    errors = sorted(
        Draft202012Validator(schema).iter_errors(packaged),
        key=lambda error: list(error.path),
    )
    assert errors == [], [error.message for error in errors[:5]]
    loaded = registry.load_registry(registry_version="2026-08-06.v5")
    sources = {source["source_ref"]: source for source in loaded["sources"]}
    assert sources["research.mouse-shape-ergonomics"]["supports_sections"] == ["mechanisms"]
    assert sources["research.cursor-latency-tracking"]["supports_sections"] == ["mechanisms"]
    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}
    for entry_id in (
        "hypothesis.mouse-fit-differential-intake",
        "hypothesis.input-latency-differential-intake",
    ):
        entry = entries[entry_id]
        assert entry["supported_uses"] == [
            "explanation_only", "diagnosis_support", "candidate_experiment",
        ]
        assert "user_report_available" in entry["quality_prerequisites"]
        assert entry["alternative_explanations"]
        assert entry["counterevidence"]
        assert entry["cue"] != "not_applicable"
        assert entry["matched_retest"] != "not_applicable"
        assert entry["stop_adjust_rule"] != "not_applicable"
        assert "pain" in " ".join(
            section["text"] for section in entry["stop_adjust_rule"]
        ).lower()
        assert entry["definition"]["source_refs"] == ["product.problem-hypothesis-spec"]

    assert entries["hypothesis.input-latency-differential-intake"]["definition"][
        "text"
    ].startswith("Cursor or visual-feedback delay")


def test_v6_adds_reviewed_community_practice_without_changing_metric_contract():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v3.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v6.json").read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(packaged),
        key=lambda error: list(error.path),
    )
    assert errors == [], [error.message for error in errors[:5]]
    loaded = registry.load_registry(registry_version="2026-08-06.v6")
    assert loaded == registry.validate_registry(packaged)
    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}
    assert entries["community.aim-efficiency-framework"]["supported_uses"] == [
        "explanation_only",
    ]
    practice = entries["community.practice-intent-and-autopilot"]
    assert practice["supported_uses"] == ["explanation_only"]
    assert "warm-up" in practice["definition"]["text"]
    difficulty = entries["community.difficulty-refinement-and-stress-test"]
    assert "harder" in difficulty["definition"]["text"]
    tempo = entries["community.qiluno.distance-adaptive-click-tempo"]
    assert tempo["supported_uses"] == ["explanation_only"]
    assert "near targets" in tempo["definition"]["text"].lower()
    timing = entries["community.qiluno.confirmation-timing-schools"]
    timing_text = json.dumps(timing, ensure_ascii=False).lower()
    assert "settled" in timing_text and "deceleration" in timing_text
    assert "single universally correct" in timing_text
    reset = entries["community.qiluno.reset-as-continuity"]
    assert reset["supported_uses"] == ["explanation_only"]
    assert "user report" in " ".join(reset["forbidden_inferences"]).lower()
    for entry in (tempo, timing, reset):
        for field in (
            "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
            "stop_adjust_rule", "scenario_prescription",
        ):
            assert field not in entry
    tracking = entries["tracking.reactive-change-response"]
    assert "has not changed" in tracking["scope"]["text"]
    smoothness = entries["tracking.control-smoothness"]
    assert "continuous reading" in smoothness["mechanisms"][-1]["text"]
    sources = {source["source_ref"]: source for source in loaded["sources"]}
    static_source = sources["community.qiluno.bilibili.static-guide"]
    assert static_source["source_level"] == "coach_first_party"
    assert static_source["author_or_org"] == "天才烧酒琪露诺"
    assert static_source["published_at"] == "2024-03-10"
    assert "BV1Xt421L72J" in static_source["locator"]
    for entry_id in (
        "dynamic.speed-matching-and-reading",
        "tracking.predictable-speed-matching",
        "tracking.reactive-change-response",
        "tracking.control-smoothness",
    ):
        assert entries[entry_id]["entry_version"] == 2
    tension = entries["hypothesis.tension-management"]
    assert tension["entry_version"] == 3
    assert any("arm" in section["text"].lower() and "wrist" in section["text"].lower()
               for section in tension["mechanisms"])
    assert all("model" not in source["locator"].lower()
               for source in loaded["sources"] if source["source_ref"].startswith("community.viscose.youtube."))
    wire = json.dumps(loaded, ensure_ascii=False).lower()
    for forbidden in ("90%", "94%", "转化率97", "one war"):
        assert forbidden not in wire


def test_v6_remains_backward_compatible_with_v5():
    assert registry.load_registry(registry_version="2026-08-06.v5")["registry_version"] == "2026-08-06.v5"


@pytest.mark.parametrize(
    ("case", "expected_valid"),
    [
        ("257_sources", True),
        ("33_topics", True),
        ("unsafe_text", False),
        ("blank_text", False),
        ("501_char_list_text", False),
        ("invalid_alias", False),
    ],
)
def test_v4_json_schema_and_python_validator_boundary_parity(case, expected_valid):
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v3.json").read_text(encoding="utf-8"))
    candidate = json.loads((root / "registry.v4.json").read_text(encoding="utf-8"))

    if case == "257_sources":
        template = candidate["sources"][0]
        while len(candidate["sources"]) < 257:
            source = copy.deepcopy(template)
            source["source_ref"] = f"community.synthetic.source-{len(candidate['sources'])}"
            candidate["sources"].append(source)
    elif case == "33_topics":
        candidate["entries"][0]["topics"] = [f"topic.{index}" for index in range(33)]
    elif case == "unsafe_text":
        candidate["entries"][0]["definition"]["text"] = "api_key=supersecret"
    elif case == "blank_text":
        candidate["entries"][0]["definition"]["text"] = "   "
    elif case == "501_char_list_text":
        candidate["entries"][0]["alternative_explanations"][0] = "x" * 501
    else:
        candidate["signal_aliases"]["bad?alias"] = "canonical.signal"

    schema_valid = not list(Draft202012Validator(schema).iter_errors(candidate))
    try:
        registry.validate_registry(candidate)
    except registry.KnowledgeRegistryError:
        python_valid = False
    else:
        python_valid = True

    assert schema_valid is expected_valid
    assert python_valid is expected_valid


def test_v4_capabilities_are_ordered_and_gate_prescription_fields():
    loaded = registry.load_registry(registry_version="2026-07-29.v4")
    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}

    explanation = entries["community.score-farming-context"]
    assert explanation["supported_uses"] == ["explanation_only"]
    for name in (
        "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
        "stop_adjust_rule", "scenario_prescription",
    ):
        assert name not in explanation

    experiment = entries["community.friction-and-surface"]
    assert experiment["supported_uses"] == [
        "explanation_only", "diagnosis_support", "candidate_experiment",
    ]
    assert experiment["cue"] != "not_applicable"
    assert experiment["dose_guardrail"] != "not_applicable"
    assert experiment["matched_retest"] != "not_applicable"
    assert experiment["stop_adjust_rule"] != "not_applicable"
    assert "near_transfer_retest" not in experiment
    assert "scenario_prescription" not in experiment

    prescribed = entries["static.flicking-terminal-control"]
    assert prescribed["supported_uses"] == [
        "explanation_only", "diagnosis_support", "candidate_experiment",
        "scenario_prescription",
    ]
    assert prescribed["scenario_prescription"] != "not_applicable"
    assert prescribed["near_transfer_retest"] != "not_applicable"
    assert registry.validate_registry(loaded) == loaded


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["entries"][0].update(
            supported_uses=["candidate_experiment"],
        ),
        lambda data: next(
            entry for entry in data["entries"]
            if entry["entry_id"] == "community.score-farming-context"
        ).update(cue={
            "section_ref": "community.score-farming-context.cue",
            "claim_level": "community_practice",
            "source_refs": ["community.rawinput.article.scorefarm"],
            "text": "unexpected cue",
        }),
        lambda data: next(
            entry for entry in data["entries"]
            if entry["entry_id"] == "community.friction-and-surface"
        ).pop("matched_retest"),
        lambda data: next(
            entry for entry in data["entries"]
            if entry["entry_id"] == "static.flicking-terminal-control"
        ).update(supported_uses=[
            "explanation_only", "diagnosis_support", "candidate_experiment",
        ]),
    ],
)
def test_v4_rejects_capability_and_field_mismatches(mutate):
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    invalid = json.loads((root / "registry.v4.json").read_text(encoding="utf-8"))
    mutate(invalid)

    with pytest.raises(registry.KnowledgeRegistryError, match="capability|supported_uses|required|forbidden"):
        registry.validate_registry(invalid)


def test_v4_migration_audit_accounts_for_v3_and_nine_new_entries():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    v3 = json.loads((root / "registry.v3.json").read_text(encoding="utf-8"))
    v4 = json.loads((root / "registry.v4.json").read_text(encoding="utf-8"))
    audit = json.loads(
        (root / "migrations" / "2026-07-29-v3-to-v4-audit.json")
        .read_text(encoding="utf-8")
    )

    assert audit["schema_version"] == "coach_knowledge_migration_audit.v3"
    assert audit["source_registry_version"] == "2026-07-28.v3"
    assert audit["target_registry_version"] == "2026-07-29.v4"
    assert {
        row["source_entry_ref"] for row in audit["migrated_entries"]
    } == {
        registry.entry_ref(entry) for entry in v3["entries"]
    }
    migrated_ids = {entry["entry_id"] for entry in v3["entries"]}
    assert {
        row["target_entry_ref"] for row in audit["migrated_entries"]
    } == {
        f"knowledge:{entry_id}@2" for entry_id in migrated_ids
    }
    assert {
        registry.entry_ref(entry) for entry in v4["entries"]
        if entry["entry_id"] in migrated_ids
    } == {
        f"knowledge:{entry_id}@2" for entry_id in migrated_ids
    }
    assert not (
        {registry.entry_ref(entry) for entry in v3["entries"]}
        & {registry.entry_ref(entry) for entry in v4["entries"]}
    )
    assert {
        row["target_entry_ref"] for row in audit["new_entries"]
    } == {f"knowledge:{entry_id}@1" for entry_id in _RAWINPUT_V4_ENTRY_IDS}


_X76_WIKI_V8_NEW_ENTRY_IDS = {
    "community.bardpill-accuracy-anchored-progression",
    "community.speed-vs-evasive-switching",
    "community.reading-vs-execution-decomposition",
    "community.accuracy-multiplied-scoring",
    "community.edge-tracking-underaim",
    "community.overshoot-sensitivity-trigger",
    "community.vrt-response-floor",
    "community.target-angular-demand-math",
    "community.strafe-relative-speed-ladder",
}

_X76_WIKI_V8_RECALL_QUERIES = {
    "community.bardpill-accuracy-anchored-progression": {
        "issue_signal": "speed up accuracy down",
    },
    "community.speed-vs-evasive-switching": {
        "issue_signal": "switch transition slow",
    },
    "community.reading-vs-execution-decomposition": {
        "issue_signal": "post change error high",
    },
    "community.accuracy-multiplied-scoring": {
        "issue_signal": "score up acc down",
    },
    "community.edge-tracking-underaim": {
        "issue_signal": "tracking lag high",
    },
    "community.overshoot-sensitivity-trigger": {
        "metric_refs": ["metric:reverse_ratio"],
    },
    "community.vrt-response-floor": {
        "metric_refs": ["metric:reacquisition_time"],
    },
    "community.target-angular-demand-math": {
        "topic": "sensitivity",
    },
    "community.strafe-relative-speed-ladder": {
        "issue_signal": "movement telemetry unavailable",
    },
    "research.speed-precision.fitts": {
        "issue_signal": "throughput below reference",
    },
}

_V8_FOLD_IN_VERSIONS = {
    "hypothesis.tension-management": 4,
    "community.qiluno.confirmation-timing-schools": 2,
    "community.qiluno.reset-as-continuity": 2,
    "dynamic.speed-matching-and-reading": 3,
    "tracking.predictable-speed-matching": 3,
    "community.adaptive-mouse-grip": 2,
    "community.difficulty-refinement-and-stress-test": 2,
    "community.score-farming-context": 2,
    "community.aim-trainer-transfer": 2,
    "static.flicking-terminal-control": 3,
    "dynamic.click-error-and-acquisition": 3,
    "switching.transition-and-arrival": 3,
}


def test_v8_adds_x76_wiki_knowledge_with_schema_and_validator_agreement():
    root = Path(__file__).resolve().parents[2] / "knowledge" / "coach"
    schema = json.loads((root / "schema.v3.json").read_text(encoding="utf-8"))
    packaged = json.loads((root / "registry.v8.json").read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(packaged),
        key=lambda error: list(error.path),
    )
    assert errors == [], [error.message for error in errors[:5]]
    loaded = registry.load_registry(registry_version="2026-08-16.v8")
    assert loaded == registry.validate_registry(packaged)
    assert registry.load_registry()["registry_version"] == "2026-08-16.v8"
    assert registry.MAX_RESULTS == 8
    assert len(loaded["entries"]) == 37

    sources = {source["source_ref"]: source for source in loaded["sources"]}
    wiki = sources["community.x76-wiki"]
    assert wiki["source_level"] == "community_organization"
    assert wiki["retrieved_at"] == "2026-08-15"
    assert "https://x76.gg/wiki/" in wiki["locator"]
    assert (
        "88614b2c6627141c29c18f8c3239f4196b8bd1988b12660995812cf5613bc081"
        in wiki["locator"]
    )
    assert loaded["signal_aliases"]["score up acc down"] == "score up accuracy down"

    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}
    assert _X76_WIKI_V8_NEW_ENTRY_IDS <= set(entries)
    for entry_id in (
        "community.bardpill-accuracy-anchored-progression",
        "community.speed-vs-evasive-switching",
    ):
        assert entries[entry_id]["supported_uses"] == [
            "explanation_only", "diagnosis_support", "candidate_experiment",
            "scenario_prescription",
        ]
        assert entries[entry_id]["scenario_prescription"] != "not_applicable"
    for entry_id in (
        "community.edge-tracking-underaim",
        "community.vrt-response-floor",
        "community.target-angular-demand-math",
        "community.strafe-relative-speed-ladder",
    ):
        entry = entries[entry_id]
        assert entry["supported_uses"] == ["explanation_only"]
        for name in (
            "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
            "stop_adjust_rule", "scenario_prescription",
        ):
            assert name not in entry

    # community_organization source ceiling: wiki-cited sections stay at
    # community_practice or below.
    for entry in loaded["entries"]:
        section_values = [entry["definition"], entry["scope"], entry["expected_direction"]]
        section_values.extend(entry["mechanisms"])
        for section_value in section_values:
            if "community.x76-wiki" in section_value["source_refs"]:
                assert section_value["claim_level"] in {
                    "community_practice", "experimental",
                }


def test_v8_x76_entries_are_retrievable_by_signal_metric_or_topic():
    loaded = registry.load_registry(registry_version="2026-08-16.v8")
    for entry_id, query in _X76_WIKI_V8_RECALL_QUERIES.items():
        results = registry.query_registry(loaded, **query)
        assert entry_id in [item["entry_id"] for item in results], (entry_id, query)


_V8_CONSISTENCY_FIX_VERSIONS = {
    "tracking.control-smoothness": 3,
    "tracking.reactive-change-response": 3,
    "switching.selection-observable-only": 3,
}


def test_v8_consistency_fixes_from_the_2026_08_16_audit():
    loaded = registry.load_registry(registry_version="2026-08-16.v8")
    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}
    for entry_id, version in _V8_CONSISTENCY_FIX_VERSIONS.items():
        assert entries[entry_id]["entry_version"] == version, entry_id

    wire = json.dumps(loaded, ensure_ascii=False)
    # M4: the static settle token matches the pipeline's real static key
    assert "metric:settle_time_ms" not in wire
    static = entries["static.flicking-terminal-control"]
    assert "metric:settle_duration_ms" in static["metric_refs"]
    # M5: path geometry is documented as indirect context
    assert "indirect" in static["definition"]["text"].lower()
    # M1: input-native retrieval stays explanation-only for control smoothness
    control = entries["tracking.control-smoothness"]
    assert "explanation layer only" in control["scope"]["text"]
    assert "explanation-only" in " ".join(control["limitations"]).lower()
    # M3: the stop rule relies on matched retest only
    tension_stop = entries["hypothesis.tension-management"]["stop_adjust_rule"][0]["text"]
    assert "matched-retest" in tension_stop and "near-transfer" not in tension_stop
    # M7: dead or drifted tokens are gone or renamed
    assert "metric:click_pacing" not in wire
    assert "metric:post_change_stability" not in wire
    assert "metric:target_state_outcome" not in wire
    assert "metric:target_switching.selection_error_ratio" in wire
    assert "metric:dynamic_clicking.target_state_accuracy" in wire

    # M2: alias canonicals never dangle (v1->v2 contraction left one behind)
    active_signals = {
        signal
        for entry in loaded["entries"] if entry["status"] == "active"
        for signal in entry["signals"]
    }
    assert set(loaded["signal_aliases"].values()) <= active_signals
    fitts = entries["research.speed-precision.fitts"]
    assert fitts["supported_uses"] == ["explanation_only", "diagnosis_support"]
    assert fitts["expected_direction"]["text"] == "comparison_only"


def test_v8_fold_ins_bump_entry_versions_and_cite_the_wiki_source():
    loaded = registry.load_registry(registry_version="2026-08-16.v8")
    entries = {entry["entry_id"]: entry for entry in loaded["entries"]}
    for entry_id, version in _V8_FOLD_IN_VERSIONS.items():
        entry = entries[entry_id]
        assert entry["entry_version"] == version, entry_id
        assert "community.x76-wiki" in entry["sources"], entry_id
        assert any(
            ".x76-" in section["section_ref"] for section in entry["mechanisms"]
        ), entry_id


def test_v8_remains_backward_compatible_with_v7():
    previous = registry.load_registry(registry_version="2026-08-15.v7")
    assert previous["registry_version"] == "2026-08-15.v7"
    assert len(previous["entries"]) == 27
