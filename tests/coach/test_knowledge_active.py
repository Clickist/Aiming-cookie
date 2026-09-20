"""Active knowledge resolution layer: official vs pack semantics + fallback
reasons (kb-sdk plan WP-08a). Fixtures build a full DATA_ROOT under tmp_path;
DATA_ROOT is provided via monkeypatch.setenv exactly as the desktop launch
does, so resolve_active() reads it the same way production code does."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kovaak_tracker.coach import knowledge_active, knowledge_registry

PACK_ID = "com.example.pack"
PACK_VERSION = "1.0.0"


def _pack_registry() -> dict:
    """Minimal valid coach_knowledge_registry.v3 document owned by the pack."""
    source_ref = "src.pack-fixture"
    return {
        "schema_version": "coach_knowledge_registry.v3",
        "registry_version": f"{PACK_ID}@{PACK_VERSION}",
        "signal_aliases": {},
        "sources": [
            {
                "source_ref": source_ref,
                "source_level": "product_contract",
                "title": "Pack knowledge source",
                "author_or_org": "Example author",
                "retrieved_at": "2026-09-20",
                "locator": "local pack fixture",
                "applicability": ["all_families"],
                "supports_sections": [
                    "definition", "scope", "expected_direction", "mechanisms",
                ],
            }
        ],
        "entries": [
            {
                "entry_id": "kb.pack-entry.definition",
                "entry_version": 1,
                "status": "active",
                "category": "mechanism",
                "topics": ["pack.topic"],
                "signals": [],
                "metric_refs": [],
                "family_scope": ["static_clicking"],
                "observation_refs": [],
                "quality_prerequisites": [],
                "definition": {
                    "section_ref": "kb.pack-entry.definition.definition",
                    "claim_level": "deterministic_rule",
                    "source_refs": [source_ref],
                    "text": "包装知识库的示例定义条目。",
                },
                "scope": {
                    "section_ref": "kb.pack-entry.definition.scope",
                    "claim_level": "deterministic_rule",
                    "source_refs": [source_ref],
                    "text": "适用于示例场景的知识范围说明。",
                },
                "expected_direction": {
                    "section_ref": "kb.pack-entry.definition.expected-direction",
                    "claim_level": "deterministic_rule",
                    "source_refs": [source_ref],
                    "text": "higher_better",
                },
                "mechanisms": [
                    {
                        "section_ref": "kb.pack-entry.definition.mechanism.example",
                        "claim_level": "deterministic_rule",
                        "source_refs": [source_ref],
                        "text": "示例机制说明，仅用于解析层测试。",
                    }
                ],
                "alternative_explanations": ["其它解释仍需对照官方知识库。"],
                "forbidden_inferences": ["不能据此推断具体设备优劣。"],
                "limitations": ["仅为本机示例数据。"],
                "counterevidence": ["当前没有对照实验数据支持。"],
                "sources": [source_ref],
                "supported_uses": ["explanation_only"],
            }
        ],
    }


def _pack_mapping() -> dict:
    return {"schema_version": "coach_mapping.v1", "static_clicking": []}


def _installed_entry() -> list[dict]:
    return [
        {
            "pack_id": PACK_ID,
            "pack_version": PACK_VERSION,
            "display_name": "Example pack",
            "author": "Example author",
            "installed_at": "2026-09-20T00:00:00Z",
            "has_mapping": True,
        }
    ]


def _write_config(data_root: Path, *, active: str, installed: list[dict] | None = None) -> None:
    config_dir = data_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    doc: dict = {"schema_version": "knowledge_config.v1", "active": active}
    if installed is not None:
        doc["installed"] = installed
    (config_dir / "knowledge.json").write_text(json.dumps(doc), encoding="utf-8")


def _install_pack(
    data_root: Path,
    *,
    registry_text: str | None = None,
    with_mapping: bool = False,
) -> None:
    pack_dir = data_root / "knowledge-packs" / PACK_ID
    (pack_dir / "knowledge").mkdir(parents=True)
    (pack_dir / "knowledge" / "registry.json").write_text(
        registry_text if registry_text is not None else json.dumps(_pack_registry()),
        encoding="utf-8",
    )
    if with_mapping:
        (pack_dir / "mapping.json").write_text(json.dumps(_pack_mapping()), encoding="utf-8")


def test_missing_config_defaults_to_official_without_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    active = knowledge_active.resolve_active()

    assert active.mode == "official"
    assert active.pack_id is None
    assert active.pack_version is None
    assert active.registry_path is None
    assert knowledge_active.last_fallback_reason() is None
    registry = knowledge_active.load_active_registry()
    assert registry["registry_version"] == knowledge_registry.load_registry()["registry_version"]


def test_missing_data_root_env_defaults_to_official(monkeypatch):
    monkeypatch.delenv("DATA_ROOT", raising=False)

    active = knowledge_active.resolve_active()

    assert active.mode == "official"
    assert knowledge_active.last_fallback_reason() is None


@pytest.mark.parametrize(
    "config_text",
    [
        "{not json",
        json.dumps({"schema_version": "wrong", "active": "official"}),
        json.dumps([1, 2]),
    ],
)
def test_broken_config_falls_back_to_official_with_reason(
    tmp_path, monkeypatch, config_text
):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "knowledge.json").write_text(config_text, encoding="utf-8")

    active = knowledge_active.resolve_active()

    assert active.mode == "official"
    reason = knowledge_active.last_fallback_reason()
    assert reason and "config" in reason
    registry = knowledge_active.load_active_registry()
    assert registry["registry_version"] == knowledge_registry.load_registry()["registry_version"]
    assert knowledge_active.last_fallback_reason() == reason


@pytest.mark.parametrize("installed", [None, _installed_entry()])
def test_active_pack_missing_falls_back_to_official_with_reason(
    tmp_path, monkeypatch, installed
):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _write_config(tmp_path, active=PACK_ID, installed=installed)

    active = knowledge_active.resolve_active()

    assert active.mode == "official"
    reason = knowledge_active.last_fallback_reason()
    assert reason and PACK_ID in reason


def test_active_pack_dir_without_registry_falls_back_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / "knowledge-packs" / PACK_ID).mkdir(parents=True)
    _write_config(tmp_path, active=PACK_ID, installed=_installed_entry())

    active = knowledge_active.resolve_active()

    assert active.mode == "official"
    reason = knowledge_active.last_fallback_reason()
    assert reason and "registry" in reason


def test_valid_pack_is_resolved_and_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _install_pack(tmp_path, with_mapping=True)
    _write_config(tmp_path, active=PACK_ID, installed=_installed_entry())

    active = knowledge_active.resolve_active()

    assert active.mode == "pack"
    assert active.pack_id == PACK_ID
    assert active.pack_version == PACK_VERSION
    assert active.registry_path == (
        tmp_path / "knowledge-packs" / PACK_ID / "knowledge" / "registry.json"
    )
    assert active.mapping_path == tmp_path / "knowledge-packs" / PACK_ID / "mapping.json"
    assert knowledge_active.last_fallback_reason() is None

    registry = knowledge_active.load_active_registry()
    assert registry["registry_version"] == f"{PACK_ID}@{PACK_VERSION}"
    assert knowledge_active.last_fallback_reason() is None

    doc, reason = knowledge_active.load_active_mapping()
    assert doc == _pack_mapping()
    assert reason is None


@pytest.mark.parametrize(
    "registry_text",
    [
        "{not json",
        json.dumps({"schema_version": "nope"}),
    ],
)
def test_pack_with_invalid_registry_falls_back_to_official(
    tmp_path, monkeypatch, registry_text
):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _install_pack(tmp_path, registry_text=registry_text)
    _write_config(tmp_path, active=PACK_ID, installed=_installed_entry())

    registry = knowledge_active.load_active_registry()

    assert registry["registry_version"] == knowledge_registry.load_registry()["registry_version"]
    reason = knowledge_active.last_fallback_reason()
    assert reason and PACK_ID in reason


def test_official_mapping_missing_file_is_builtin_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        knowledge_active,
        "_OFFICIAL_MAPPING_PATH",
        tmp_path / "mapping" / "official.v1.json",
    )

    doc, reason = knowledge_active.load_active_mapping()

    assert doc is None
    assert reason is None
    assert knowledge_active.last_fallback_reason() is None


def test_official_mapping_loads_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    mapping_path = tmp_path / "official.v1.json"
    mapping_path.write_text(json.dumps({"schema_version": "coach_mapping.v1"}), encoding="utf-8")
    monkeypatch.setattr(knowledge_active, "_OFFICIAL_MAPPING_PATH", mapping_path)

    doc, reason = knowledge_active.load_active_mapping()

    assert doc == {"schema_version": "coach_mapping.v1"}
    assert reason is None


def test_corrupt_official_mapping_records_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    mapping_path = tmp_path / "official.v1.json"
    mapping_path.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(knowledge_active, "_OFFICIAL_MAPPING_PATH", mapping_path)

    doc, reason = knowledge_active.load_active_mapping()

    assert doc is None
    assert reason
    assert knowledge_active.last_fallback_reason() == reason


def test_pack_without_mapping_uses_builtin_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _install_pack(tmp_path)
    _write_config(tmp_path, active=PACK_ID, installed=_installed_entry())

    doc, reason = knowledge_active.load_active_mapping()

    assert doc is None
    assert reason is None


def test_last_fallback_reason_resets_after_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    config_path = tmp_path / "config" / "knowledge.json"
    config_path.parent.mkdir()
    config_path.write_text("{broken", encoding="utf-8")

    knowledge_active.load_active_registry()
    assert knowledge_active.last_fallback_reason()

    config_path.write_text(
        json.dumps({"schema_version": "knowledge_config.v1", "active": "official"}),
        encoding="utf-8",
    )
    registry = knowledge_active.load_active_registry()
    assert registry["registry_version"] == knowledge_registry.load_registry()["registry_version"]
    assert knowledge_active.last_fallback_reason() is None


def test_active_layer_stays_transparent_to_registry_pin(tmp_path, monkeypatch):
    # The golden harness pins knowledge_registry.load_registry to a fixed
    # version; the active layer must call through the module attribute so the
    # pin propagates in official mode.
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        knowledge_registry,
        "load_registry",
        lambda *args, **kwargs: {"registry_version": "2026-09-12.v12"},
    )

    assert knowledge_active.load_active_registry() == {"registry_version": "2026-09-12.v12"}
