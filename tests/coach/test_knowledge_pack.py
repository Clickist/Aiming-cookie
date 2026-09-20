"""WP-08b: coach_knowledge_pack.v1 validator + local store tests (plan C2/C3).

Fixtures build a minimal legal pack inline (manifest + v3 registry with an
explanation_only entry and a full-capability entry carrying an official
scenario_prescription ref, plus a mapping.json referencing the pack's own
active entry). The official scenario registry, launch manifest, and mapping
vocabulary are consumed read-only from the repository; no fixture depends on
the parallel v13 registry content beyond its scenario-ref resolution role.
Coverage: valid pack (dir/zip/wrapper zip), manifest failure forms, registry
version namespace, forbidden source levels, out-of-vocabulary signals and
metric refs, fake scenario refs, dangling and inactive expected_entry_ref,
size caps, entry-cap overrun, manifest smuggling, zip path traversal, safe
install/overwrite/uninstall semantics, active fallback to official, config
recovery, and the CLI (in-process + subprocess module entry).
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from kovaak_tracker.coach import knowledge_pack
from kovaak_tracker.coach.knowledge_pack import (
    MAX_MANIFEST_BYTES,
    MAX_REGISTRY_BYTES,
    KnowledgePackError,
    KnowledgePackRejected,
    REGISTRY_NAME,
    validate_pack,
)
from kovaak_tracker.coach.mapping_rules import MAX_FILE_BYTES

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ID = "com.example.aiming"
PACK_VERSION = "1.0.0"
SOURCE_REF = "src.pack-fixture"
OFFICIAL_SCENARIO_REF = "scenario:static.1wall_6targets_small@1"
PRESCRIPTION_ENTRY_ID = "kb.pack-prescription.entry"


# ---------------------------------------------------------------------------
# Pack fixtures (inline; self-contained)
# ---------------------------------------------------------------------------


def _section(entry_id: str, name: str, text: str) -> dict:
    return {
        "section_ref": f"{entry_id}.{name}",
        "claim_level": "community_practice",
        "source_refs": [SOURCE_REF],
        "text": text,
    }


def _source(source_level: str = "community_organization") -> dict:
    return {
        "source_ref": SOURCE_REF,
        "source_level": source_level,
        "title": "Pack knowledge source",
        "author_or_org": "Example author",
        "retrieved_at": "2026-09-20",
        "locator": "local pack fixture",
        "applicability": ["all_families"],
        "supports_sections": [
            "definition", "scope", "expected_direction", "mechanisms", "cue",
            "dose_guardrail", "matched_retest", "near_transfer_retest",
            "stop_adjust_rule", "scenario_prescription",
        ],
    }


def _entry_explanation() -> dict:
    entry_id = "kb.pack-definition.entry"
    return {
        "entry_id": entry_id,
        "entry_version": 1,
        "status": "active",
        "category": "mechanism",
        "topics": ["pack.topic"],
        "signals": ["sparc low"],
        "metric_refs": ["sparc"],
        "family_scope": ["static_clicking"],
        "observation_refs": [],
        "quality_prerequisites": [],
        "definition": _section(entry_id, "definition", "官方口径之外的补充解释。"),
        "scope": _section(entry_id, "scope", "仅适用于本包声明的范围。"),
        "expected_direction": _section(entry_id, "expected-direction", "higher_better"),
        "mechanisms": [_section(entry_id, "mechanism.example", "示例机制说明。")],
        "alternative_explanations": ["其他解释仍以官方知识库为准。"],
        "forbidden_inferences": ["不得据此推断设备或设置的优劣。"],
        "limitations": ["仅为本机示例数据。"],
        "counterevidence": ["暂无对照实验支持。"],
        "sources": [SOURCE_REF],
        "supported_uses": ["explanation_only"],
    }


def _entry_prescription(scenario_ref: str = OFFICIAL_SCENARIO_REF) -> dict:
    return {
        "entry_id": PRESCRIPTION_ENTRY_ID,
        "entry_version": 1,
        "status": "active",
        "category": "training_cue",
        "topics": ["pack.topic"],
        "signals": ["sparc low"],
        "metric_refs": ["sparc"],
        "family_scope": ["static_clicking"],
        "observation_refs": ["metric.terminal_control"],
        "quality_prerequisites": ["pack.quality-prereq"],
        "definition": _section(PRESCRIPTION_ENTRY_ID, "definition", "处方条目的定义。"),
        "scope": _section(PRESCRIPTION_ENTRY_ID, "scope", "处方条目的适用范围。"),
        "expected_direction": _section(PRESCRIPTION_ENTRY_ID, "expected-direction", "lower_better"),
        "mechanisms": [_section(PRESCRIPTION_ENTRY_ID, "mechanism.example", "处方机制说明。")],
        "alternative_explanations": ["仍需排除设置与设备因素。"],
        "forbidden_inferences": ["不得据此断言因果关系。"],
        "limitations": ["阈值为社区经验。"],
        "counterevidence": ["个体差异可能显著。"],
        "cue": _section(PRESCRIPTION_ENTRY_ID, "cue", "减速段当作独立动作完成。"),
        "dose_guardrail": [_section(PRESCRIPTION_ENTRY_ID, "dose-guardrail.main", "单次练习不超过二十分钟。")],
        "matched_retest": _section(PRESCRIPTION_ENTRY_ID, "matched-retest", "同场景同设置复测。"),
        "near_transfer_retest": _section(PRESCRIPTION_ENTRY_ID, "near-transfer-retest", "近似场景复测一次。"),
        "stop_adjust_rule": [_section(PRESCRIPTION_ENTRY_ID, "stop-adjust-rule.main", "指标未改善即停止。")],
        "sources": [SOURCE_REF],
        "supported_uses": [
            "explanation_only", "diagnosis_support", "candidate_experiment",
            "scenario_prescription",
        ],
        "scenario_prescription": {
            "scenario_profile_ref": scenario_ref,
            "practice_condition": "在同等灵敏度与场景下练习。",
            "review_after": "next matched retest",
            "source_refs": [SOURCE_REF],
            "claim_level": "community_practice",
        },
    }


def _registry(
    registry_version: str = f"{PACK_ID}@{PACK_VERSION}",
    *,
    source_level: str = "community_organization",
    entries: list | None = None,
) -> dict:
    return {
        "schema_version": "coach_knowledge_registry.v3",
        "registry_version": registry_version,
        "signal_aliases": {"cool sparc": "sparc low"},
        "sources": [_source(source_level)],
        "entries": entries if entries is not None else [
            _entry_explanation(), _entry_prescription(),
        ],
    }


def _manifest(
    pack_id: str = PACK_ID,
    pack_version: str = PACK_VERSION,
) -> dict:
    return {
        "schema_version": "coach_knowledge_pack.v1",
        "pack_id": pack_id,
        "display_name": "示例知识包",
        "author": "Example author",
        "homepage": "https://example.com/pack",
        "pack_version": pack_version,
        "license": "MIT",
        "ac_compat": {
            "knowledge_schema": ["coach_knowledge_registry.v3"],
            "mapping_schema": ["coach_mapping.v1"],
        },
    }


def _mapping(expected_entry_ref: str = f"knowledge:{PRESCRIPTION_ENTRY_ID}@1") -> dict:
    return {
        "schema_version": "coach_mapping.v1",
        "static_clicking": [
            {
                "signal": "sparc low",
                "severity": "fix",
                "text": "减速段平滑度偏低：速度轮廓快速波动较多。",
                "claim_level": "experimental",
                "metric_refs": ["sparc"],
                "observation_ref": "metric.terminal_control",
                "conditions": [
                    {"input": "self_summary", "metric": "sparc", "op": "<", "value": -5.0},
                ],
                "prescriptions": [
                    {
                        "scenario": "1wall6targets_small",
                        "reason": "练习小目标快速定位与减速。",
                        "target_metrics": ["sparc"],
                        "expected_direction": ["sparc 提高"],
                    }
                ],
            }
        ],
        "families": {
            "continuous_tracking": [],
            "dynamic_clicking": [],
            "target_switching": [
                {
                    "signal": "sparc low",
                    "metric": "continuous_tracking.sparc",
                    "row_field": "sparc",
                    "direction": "lower",
                    "knowledge_metric_ref": "metric:sparc",
                    "expected_entry_ref": expected_entry_ref,
                    "observation_ref": "metric.smoothness",
                }
            ],
        },
        "archetypes": [],
        "root_causes": {},
    }


def _write_pack(
    base: Path,
    *,
    manifest: dict | None = None,
    registry: dict | None = None,
    mapping: dict | None | object = ...,
    readme: bool = True,
) -> Path:
    pack_dir = base / "pack"
    (pack_dir / "knowledge").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest if manifest is not None else _manifest(), ensure_ascii=False),
        encoding="utf-8",
    )
    (pack_dir / "knowledge" / "registry.json").write_text(
        json.dumps(registry if registry is not None else _registry(), ensure_ascii=False),
        encoding="utf-8",
    )
    if mapping is ...:
        mapping = _mapping()
    if mapping is not None:
        (pack_dir / "mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
        )
    if readme:
        (pack_dir / "README.md").write_text("# 示例包\n", encoding="utf-8")
    return pack_dir


def _zip_pack(src_dir: Path, dest: Path, *, wrapper: str | None = None, extra_entries: dict | None = None) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(src_dir).as_posix()
                zf.write(path, f"{wrapper}/{relative}" if wrapper else relative)
        for name, data in (extra_entries or {}).items():
            zf.writestr(name, data)
    return dest


def _error_codes(source) -> set[str]:
    return {item["code"] for item in validate_pack(source)["errors"]}


def _config_path(data_root: Path) -> Path:
    return data_root / "config" / "knowledge.json"


# ---------------------------------------------------------------------------
# Validation: legal packs
# ---------------------------------------------------------------------------


def test_valid_minimal_pack_passes(tmp_path):
    pack_dir = _write_pack(tmp_path)

    result = validate_pack(pack_dir)

    assert result["valid"] is True, result["errors"]
    assert result["errors"] == []
    pack = result["pack"]
    assert pack["pack_id"] == PACK_ID
    assert pack["pack_version"] == PACK_VERSION
    assert pack["has_mapping"] is True


def test_valid_pack_without_mapping_and_with_readme(tmp_path):
    manifest = _manifest()
    del manifest["ac_compat"]["mapping_schema"]
    pack_dir = _write_pack(tmp_path, manifest=manifest, mapping=None)

    result = validate_pack(pack_dir)

    assert result["valid"] is True, result["errors"]
    assert result["pack"]["has_mapping"] is False


def test_zip_pack_validates(tmp_path):
    pack_dir = _write_pack(tmp_path)
    zip_path = _zip_pack(pack_dir, tmp_path / "pack.zip")

    assert validate_pack(zip_path)["valid"] is True


def test_zip_pack_with_wrapper_folder_validates(tmp_path):
    pack_dir = _write_pack(tmp_path)
    zip_path = _zip_pack(pack_dir, tmp_path / "pack.zip", wrapper="my-aiming-kb")

    assert validate_pack(zip_path)["valid"] is True


@pytest.mark.parametrize(
    "entry_name",
    ["../evil.json", "..\\evil.json", "/abs/evil.json", "C:/evil.json"],
)
def test_zip_path_traversal_rejected(tmp_path, entry_name):
    pack_dir = _write_pack(tmp_path)
    zip_path = _zip_pack(pack_dir, tmp_path / "pack.zip", extra_entries={entry_name: "x"})

    codes = _error_codes(zip_path)

    assert "zip_unsafe_entry" in codes
    assert not (tmp_path / "evil.json").exists()


def test_unknown_file_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path)
    (pack_dir / "notes.txt").write_text("junk", encoding="utf-8")

    assert "pack_unknown_file" in _error_codes(pack_dir)


# ---------------------------------------------------------------------------
# Validation: manifest forms
# ---------------------------------------------------------------------------


def test_missing_manifest_rejected(tmp_path):
    pack_dir = tmp_path / "pack"
    (pack_dir / "knowledge").mkdir(parents=True)
    (pack_dir / "knowledge" / "registry.json").write_text("{}", encoding="utf-8")

    codes = _error_codes(pack_dir)

    assert "manifest_missing" in codes


def test_invalid_manifest_json(tmp_path):
    pack_dir = _write_pack(tmp_path)
    (pack_dir / "manifest.json").write_text("{not json", encoding="utf-8")

    assert "pack_invalid_json" in _error_codes(pack_dir)


def test_oversized_manifest_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path)
    (pack_dir / "manifest.json").write_text(
        json.dumps(_manifest(), ensure_ascii=False) + " " * (MAX_MANIFEST_BYTES + 1),
        encoding="utf-8",
    )

    assert "pack_file_too_large" in _error_codes(pack_dir)


@pytest.mark.parametrize(
    "mutate, expected_code",
    [
        (lambda m: m.pop("author"), "manifest_field_missing"),
        (lambda m: m.update({"extra": 1}), "manifest_field_unknown"),
        (lambda m: m.update({"schema_version": "coach_knowledge_pack.v2"}), "manifest_schema_version_invalid"),
        (lambda m: m.update({"pack_id": "My Pack"}), "manifest_pack_id_invalid"),
        (lambda m: m.update({"pack_id": ".."}), "manifest_pack_id_invalid"),
        (lambda m: m.update({"pack_id": "-lead"}), "manifest_pack_id_invalid"),
        (lambda m: m.update({"pack_version": "1.0"}), "manifest_pack_version_invalid"),
        (lambda m: m.update({"display_name": "x" * 121}), "manifest_value_invalid"),
        (lambda m: m.update({"homepage": "ftp://example.com"}), "manifest_value_invalid"),
        (lambda m: m["ac_compat"].pop("knowledge_schema"), "ac_compat_invalid"),
        (lambda m: m["ac_compat"].update({"knowledge_schema": ["coach_knowledge_registry.v2"]}), "ac_compat_invalid"),
        (lambda m: m["ac_compat"].pop("mapping_schema"), "ac_compat_mapping_mismatch"),
        (lambda m: m.update({"script": "do things"}), "manifest_unsafe_content"),
        (lambda m: m.update({"display_name": "/etc/passwd"}), "manifest_unsafe_content"),
    ],
)
def test_manifest_failure_forms(tmp_path, mutate, expected_code):
    manifest = _manifest()
    mutate(manifest)
    pack_dir = _write_pack(tmp_path, manifest=manifest)

    assert expected_code in _error_codes(pack_dir)


def test_declared_mapping_schema_without_mapping_file(tmp_path):
    manifest = _manifest()
    pack_dir = _write_pack(tmp_path, manifest=manifest, mapping=None)

    assert "ac_compat_mapping_mismatch" in _error_codes(pack_dir)


# ---------------------------------------------------------------------------
# Validation: registry narrowing rules (C2 1-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "registry_version",
    ["2026-09-12.v12", f"{PACK_ID}@9.9.9", "no-at-sign"],
)
def test_registry_version_namespace(tmp_path, registry_version):
    pack_dir = _write_pack(tmp_path, registry=_registry(registry_version))

    codes = _error_codes(pack_dir)

    assert "registry_version_mismatch" in codes


@pytest.mark.parametrize("source_level", ["product_contract", "coach_first_party"])
def test_forbidden_source_levels(tmp_path, source_level):
    pack_dir = _write_pack(tmp_path, registry=_registry(source_level=source_level))

    codes = _error_codes(pack_dir)

    assert "source_level_forbidden" in codes


def test_signal_out_of_vocabulary(tmp_path):
    registry = _registry()
    registry["entries"][0]["signals"] = ["totally custom signal"]
    pack_dir = _write_pack(tmp_path, registry=registry)

    codes = _error_codes(pack_dir)

    assert "signal_out_of_vocabulary" in codes


def test_metric_out_of_vocabulary(tmp_path):
    registry = _registry()
    registry["entries"][0]["metric_refs"] = ["custom_metric"]
    pack_dir = _write_pack(tmp_path, registry=registry)

    assert "metric_out_of_vocabulary" in _error_codes(pack_dir)


def test_fake_scenario_ref_rejected(tmp_path):
    registry = _registry()
    registry["entries"][1] = _entry_prescription(scenario_ref="scenario:fake.custom@1")
    pack_dir = _write_pack(tmp_path, registry=registry)

    codes = _error_codes(pack_dir)

    assert "scenario_ref_not_official" in codes


def test_registry_structurally_invalid_reported(tmp_path):
    registry = _registry()
    registry["entries"] = [{}] * 513  # over the 512-entry cap, checked before normalization
    pack_dir = _write_pack(tmp_path, registry=registry)

    codes = _error_codes(pack_dir)

    assert "registry_invalid" in codes


def _v1_registry(registry_version: str = f"{PACK_ID}@{PACK_VERSION}") -> dict:
    """Minimal legal ``coach_knowledge_registry.v1`` doc (v1 field set per
    ``knowledge_registry._validate_registry_v1``): no top-level ``sources`` and
    an inline entry ``source_level`` that would dodge the pack ceiling."""
    return {
        "schema_version": "coach_knowledge_registry.v1",
        "registry_version": registry_version,
        "signal_aliases": {},
        "entries": [{
            "entry_id": "kb.pack-v1.entry",
            "entry_version": 1,
            "status": "active",
            "category": "metric_definition",
            "topics": ["pack.topic"],
            "signals": ["sparc low"],
            "metric_refs": ["sparc"],
            "text": "v1 形状条目：无顶层 sources，条目内联 source_level。",
            "sources": [{"source_ref": SOURCE_REF, "source_level": "product_contract"}],
            "max_claim_level": "community_consensus",
            "limitations": ["示例限制。"],
            "counterevidence": ["示例反证。"],
            "supported_uses": ["definition"],
        }],
    }


def test_v1_shaped_registry_rejected_with_schema_version_error(tmp_path):
    pack_dir = _write_pack(tmp_path, registry=_v1_registry())

    result = validate_pack(pack_dir)
    errors = result["errors"]

    assert result["valid"] is False
    assert "registry_schema_version_invalid" in {item["code"] for item in errors}
    schema_error = next(
        item for item in errors if item["code"] == "registry_schema_version_invalid"
    )
    assert schema_error["path"] == REGISTRY_NAME
    assert "coach_knowledge_registry.v3" in schema_error["message"]
    assert "sdk/knowledge-pack/SPEC.md" in schema_error["message"]


def test_registry_too_large_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path)
    (pack_dir / "knowledge" / "registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False) + " " * (MAX_REGISTRY_BYTES + 1),
        encoding="utf-8",
    )

    codes = _error_codes(pack_dir)

    assert "pack_file_too_large" in codes


# ---------------------------------------------------------------------------
# Validation: mapping cross-check (C2 rule 5)
# ---------------------------------------------------------------------------


def test_dangling_expected_entry_ref_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, mapping=_mapping("knowledge:kb.missing.entry@1"))

    result = validate_pack(pack_dir)

    assert result["valid"] is False
    assert "mapping_invalid" in _error_codes(pack_dir)
    assert any("cannot be resolved" in item["message"] for item in result["errors"])


def test_inactive_expected_entry_ref_rejected(tmp_path):
    registry = _registry()
    registry["entries"][1]["status"] = "retired"
    pack_dir = _write_pack(tmp_path, registry=registry)

    result = validate_pack(pack_dir)

    assert "mapping_invalid" in _error_codes(pack_dir)
    assert any("not an active" in item["message"] for item in result["errors"])


def test_mapping_too_large_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path)
    (pack_dir / "mapping.json").write_text(
        json.dumps(_mapping(), ensure_ascii=False) + " " * (MAX_FILE_BYTES + 1),
        encoding="utf-8",
    )

    assert "pack_file_too_large" in _error_codes(pack_dir)


# ---------------------------------------------------------------------------
# Store: install / list / activate / uninstall (C3)
# ---------------------------------------------------------------------------


def test_install_registers_config_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    pack_dir = _write_pack(tmp_path)

    installed = knowledge_pack.install_pack(pack_dir, tmp_path)

    assert installed["pack_id"] == PACK_ID
    assert installed["pack_version"] == PACK_VERSION
    assert installed["has_mapping"] is True
    dest = tmp_path / "knowledge-packs" / PACK_ID
    assert (dest / "knowledge" / "registry.json").is_file()
    assert (dest / "mapping.json").is_file()
    config = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert config["schema_version"] == "knowledge_config.v1"
    assert config["active"] == "official"
    assert [item["pack_id"] for item in config["installed"]] == [PACK_ID]
    assert knowledge_pack.list_installed(tmp_path)[0]["pack_id"] == PACK_ID
    assert validate_pack(dest)["valid"] is True


def test_zip_install(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    pack_dir = _write_pack(tmp_path)
    zip_path = _zip_pack(pack_dir, tmp_path / "pack.zip", wrapper="my-aiming-kb")

    installed = knowledge_pack.install_pack(zip_path, tmp_path)

    assert installed["pack_id"] == PACK_ID
    assert (tmp_path / "knowledge-packs" / PACK_ID / "manifest.json").is_file()


def test_install_invalid_pack_rejected_without_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    registry = _registry()
    registry["entries"][0]["signals"] = ["totally custom signal"]
    pack_dir = _write_pack(tmp_path, registry=registry)

    with pytest.raises(KnowledgePackRejected) as excinfo:
        knowledge_pack.install_pack(pack_dir, tmp_path)

    codes = {item["code"] for item in excinfo.value.result["errors"]}
    assert "signal_out_of_vocabulary" in codes
    assert not (tmp_path / "knowledge-packs" / PACK_ID).exists()
    assert knowledge_pack.list_installed(tmp_path) == []


def test_install_same_pack_id_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    knowledge_pack.install_pack(_write_pack(tmp_path / "a"), tmp_path)
    manifest = _manifest(pack_version="1.1.0")
    updated = _write_pack(
        tmp_path / "b",
        manifest=manifest,
        registry=_registry(f"{PACK_ID}@1.1.0"),
    )

    knowledge_pack.install_pack(updated, tmp_path)

    installed = knowledge_pack.list_installed(tmp_path)
    assert [item["pack_version"] for item in installed] == ["1.1.0"]
    assert (tmp_path / "knowledge-packs" / PACK_ID / "manifest.json").read_text(
        encoding="utf-8"
    ).find("1.1.0") != -1


def test_uninstall_active_reverts_to_official(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    knowledge_pack.install_pack(_write_pack(tmp_path), tmp_path)
    knowledge_pack.set_active(PACK_ID, tmp_path)

    result = knowledge_pack.uninstall_pack(PACK_ID, tmp_path)

    assert result == {"removed": PACK_ID, "active_reverted_to_official": True}
    config = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert config["active"] == "official"
    assert config["installed"] == []
    assert not (tmp_path / "knowledge-packs" / PACK_ID).exists()


def test_uninstall_non_active_keeps_active_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    other_id = "com.example.other"
    knowledge_pack.install_pack(_write_pack(tmp_path / "a"), tmp_path)
    knowledge_pack.install_pack(
        _write_pack(
            tmp_path / "b",
            manifest=_manifest(pack_id=other_id),
            registry=_registry(f"{other_id}@{PACK_VERSION}"),
        ),
        tmp_path,
    )
    knowledge_pack.set_active(PACK_ID, tmp_path)

    knowledge_pack.uninstall_pack(other_id, tmp_path)

    config = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert config["active"] == PACK_ID
    assert [item["pack_id"] for item in config["installed"]] == [PACK_ID]


def test_uninstall_unknown_pack_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    with pytest.raises(KnowledgePackError):
        knowledge_pack.uninstall_pack("com.example.missing", tmp_path)


def test_set_active_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    knowledge_pack.install_pack(_write_pack(tmp_path), tmp_path)

    with pytest.raises(KnowledgePackError):
        knowledge_pack.set_active("com.example.missing", tmp_path)

    config = knowledge_pack.set_active(PACK_ID, tmp_path)
    assert config["active"] == PACK_ID
    config = knowledge_pack.set_active("official", tmp_path)
    assert config["active"] == "official"


def test_corrupt_config_is_recovered_as_fresh_official(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _config_path(tmp_path).parent.mkdir(parents=True)
    _config_path(tmp_path).write_text("{broken", encoding="utf-8")

    assert knowledge_pack.list_installed(tmp_path) == []

    knowledge_pack.install_pack(_write_pack(tmp_path), tmp_path)
    assert [item["pack_id"] for item in knowledge_pack.list_installed(tmp_path)] == [PACK_ID]


# ---------------------------------------------------------------------------
# CLI: python -m kovaak_tracker.coach.knowledge_pack validate <path>
# ---------------------------------------------------------------------------


def test_cli_validate_valid_pack(tmp_path, capsys):
    pack_dir = _write_pack(tmp_path)

    exit_code = knowledge_pack.main(["validate", str(pack_dir)])

    assert exit_code == 0
    assert f"OK {PACK_ID}@{PACK_VERSION}" in capsys.readouterr().out


def test_cli_validate_invalid_pack(tmp_path, capsys):
    registry = _registry()
    registry["entries"][1] = _entry_prescription(scenario_ref="scenario:fake.custom@1")
    pack_dir = _write_pack(tmp_path, registry=registry)

    exit_code = knowledge_pack.main(["validate", str(pack_dir)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ERROR scenario_ref_not_official" in out
    assert "FAILED with" in out


@pytest.mark.parametrize("make_invalid", [False, True])
def test_cli_subprocess_module_entry(tmp_path, make_invalid):
    pack_dir = _write_pack(tmp_path)
    if make_invalid:
        registry = _registry()
        registry["entries"][0]["signals"] = ["totally custom signal"]
        (pack_dir / "knowledge" / "registry.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8"
        )

    proc = subprocess.run(
        [sys.executable, "-m", "kovaak_tracker.coach.knowledge_pack", "validate", str(pack_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    expected = 1 if make_invalid else 0
    assert proc.returncode == expected, proc.stdout + proc.stderr
    if make_invalid:
        assert "ERROR signal_out_of_vocabulary" in proc.stdout
    else:
        assert proc.stdout.startswith(f"OK {PACK_ID}@{PACK_VERSION}")


# ---------------------------------------------------------------------------
# WP-15: the shipped SDK template pack (sdk/knowledge-pack/template) must stay
# valid against the frozen validators, so validator changes cannot silently
# break the template authors copy.
# ---------------------------------------------------------------------------

SDK_TEMPLATE_DIR = REPO_ROOT / "sdk" / "knowledge-pack" / "template"


def test_sdk_template_pack_passes_validate_pack():
    result = validate_pack(SDK_TEMPLATE_DIR)

    assert result["valid"] is True, result["errors"]
    assert result["errors"] == []
    pack = result["pack"]
    assert pack["pack_id"] == "com.example.my-kb"
    assert pack["pack_version"] == "1.0.0"
    assert pack["has_mapping"] is True


def test_sdk_template_mapping_passes_validate_mapping():
    from kovaak_tracker.coach import knowledge_registry, mapping_rules

    vocabulary = json.loads(
        (REPO_ROOT / "knowledge" / "mapping" / "vocabulary.v1.json").read_bytes()
    )
    registry = knowledge_registry.validate_registry(
        json.loads((SDK_TEMPLATE_DIR / "knowledge" / "registry.json").read_bytes())
    )
    mapping = json.loads((SDK_TEMPLATE_DIR / "mapping.json").read_bytes())

    mapping_rules.validate_mapping(mapping, vocabulary=vocabulary, registry=registry)
