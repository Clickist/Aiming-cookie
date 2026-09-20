"""WP-10: /api/knowledge-packs endpoint integration tests (kb-sdk plan C6).

Pack fixtures build a minimal legal pack inline (construction adapted from
``tests/coach/test_knowledge_pack.py``: manifest + v3 registry with an
explanation_only entry and a full-capability entry carrying an official
scenario_prescription ref, plus a mapping.json referencing the pack's own
active entry). The sidecar TS-parity call is always monkeypatched so tests
never depend on (or race with) a real sidecar on the loopback port; the
degraded Python-only path is covered by simulating an unreachable sidecar.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, routes
from webapp.backend.app import app


PACK_ID = "com.example.aiming"
PACK_VERSION = "1.0.0"
SOURCE_REF = "src.pack-fixture"
OFFICIAL_SCENARIO_REF = "scenario:static.1wall_6targets_small@1"
PRESCRIPTION_ENTRY_ID = "kb.pack-prescription.entry"


# ---------------------------------------------------------------------------
# Pack fixtures (inline; same construction as tests/coach/test_knowledge_pack.py)
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
    entries: list | None = None,
) -> dict:
    return {
        "schema_version": "coach_knowledge_registry.v3",
        "registry_version": registry_version,
        "signal_aliases": {"cool sparc": "sparc low"},
        "sources": [_source()],
        "entries": entries if entries is not None else [
            _entry_explanation(), _entry_prescription(),
        ],
    }


def _manifest(pack_version: str = PACK_VERSION) -> dict:
    return {
        "schema_version": "coach_knowledge_pack.v1",
        "pack_id": PACK_ID,
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


def _write_pack(base: Path, *, registry: dict | None = None) -> Path:
    pack_dir = base / "pack"
    (pack_dir / "knowledge").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8",
    )
    (pack_dir / "knowledge" / "registry.json").write_text(
        json.dumps(registry if registry is not None else _registry(), ensure_ascii=False),
        encoding="utf-8",
    )
    (pack_dir / "mapping.json").write_text(
        json.dumps(_mapping(), ensure_ascii=False), encoding="utf-8",
    )
    (pack_dir / "README.md").write_text("# 示例包\n", encoding="utf-8")
    return pack_dir


def _zip_pack(src_dir: Path, dest: Path, *, extra_entries: dict | None = None) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir).as_posix())
        for name, data in (extra_entries or {}).items():
            zf.writestr(name, data)
    return dest


# ---------------------------------------------------------------------------
# Test doubles / helpers
# ---------------------------------------------------------------------------


def _desktop_headers() -> dict[str, str]:
    return {"X-Aiming-Cookie-Desktop-Token": "test-launch-token"}


@pytest.fixture
def desktop_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "test-launch-token")


def _patch_parity(
    monkeypatch: pytest.MonkeyPatch,
    *,
    valid: bool | None,
    errors: list[str] | None = None,
) -> list[dict]:
    """Replace the sidecar HTTP call; returns the captured call bodies."""
    calls: list[dict] = []

    async def fake(registry_doc: dict):
        calls.append(registry_doc)
        return valid, list(errors or [])

    monkeypatch.setattr(routes, "_sidecar_parity_validate_registry", fake)
    return calls


def _patch_rematerialize(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict | None,
) -> list[dict]:
    """Replace the sidecar rematerialize HTTP call; returns captured calls."""
    calls: list[dict] = []

    async def fake():
        calls.append({})
        return dict(payload) if payload is not None else None

    monkeypatch.setattr(routes, "_sidecar_rematerialize_knowledge", fake)
    return calls


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Import + GET list shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_valid_pack_then_get_list_shape(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        listing = await client.get("/api/knowledge-packs", headers=_desktop_headers())

    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["pack_id"] == PACK_ID
    assert body["pack_version"] == PACK_VERSION
    assert body["warnings"] == []

    assert listing.status_code == 200
    listing_body = listing.json()
    assert set(listing_body) == {"active", "packs"}
    assert listing_body["active"] == "official"
    assert len(listing_body["packs"]) == 1
    pack = listing_body["packs"][0]
    assert set(pack) == {
        "pack_id", "display_name", "author", "pack_version", "homepage",
        "installed_at", "has_mapping", "valid",
    }
    assert pack["pack_id"] == PACK_ID
    assert pack["display_name"] == "示例知识包"
    assert pack["author"] == "Example author"
    assert pack["pack_version"] == PACK_VERSION
    assert pack["homepage"] == "https://example.com/pack"
    assert pack["installed_at"]
    assert pack["has_mapping"] is True
    assert pack["valid"] is True


@pytest.mark.asyncio
async def test_import_invalid_pack_rejected_422_with_readable_details(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    parity_calls = _patch_parity(monkeypatch, valid=True)
    registry = _registry()
    registry["entries"][0]["signals"] = ["totally custom signal"]
    pack_dir = _write_pack(tmp_path, registry=registry)

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        listing = await client.get("/api/knowledge-packs", headers=_desktop_headers())

    assert imported.status_code == 422
    body = imported.json()
    assert body["error_code"] == "knowledge_pack_rejected"
    assert body["details"] and all(isinstance(item, str) for item in body["details"])
    assert any("vocabulary" in item for item in body["details"])
    # Python 校验已拒绝，不再做 sidecar parity，也没有任何安装副作用。
    assert parity_calls == []
    assert listing.json()["packs"] == []


@pytest.mark.asyncio
async def test_import_zip_path_traversal_rejected(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    zip_path = _zip_pack(
        _write_pack(tmp_path / "src"),
        tmp_path / "pack.zip",
        extra_entries={"../evil.json": "x"},
    )

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(zip_path)},
        )

    assert imported.status_code == 422
    body = imported.json()
    assert body["error_code"] == "knowledge_pack_rejected"
    assert any("zip" in item.lower() for item in body["details"])
    assert not (tmp_path / "evil.json").exists()
    assert not (config.DATA_ROOT / "knowledge-packs" / PACK_ID).exists()


@pytest.mark.asyncio
async def test_import_rejects_relative_source_path(desktop_token, tmp_path: Path):
    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": "relative/pack"},
        )

    assert imported.status_code == 400


@pytest.mark.asyncio
async def test_import_zip_source_installs(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    zip_path = _zip_pack(_write_pack(tmp_path / "src"), tmp_path / "pack.zip")

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(zip_path)},
        )

    assert imported.status_code == 200, imported.text
    assert imported.json()["pack_id"] == PACK_ID
    assert (config.DATA_ROOT / "knowledge-packs" / PACK_ID / "manifest.json").is_file()


# ---------------------------------------------------------------------------
# Sidecar parity branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_degrades_to_python_only_when_sidecar_unreachable(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=None)
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        listing = await client.get("/api/knowledge-packs", headers=_desktop_headers())

    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["pack_id"] == PACK_ID
    assert len(body["warnings"]) == 1
    assert "sidecar" in body["warnings"][0]
    assert listing.json()["packs"][0]["valid"] is True


@pytest.mark.asyncio
async def test_import_rejected_when_sidecar_parity_fails(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=False, errors=["TS validator: registry boom"])
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        imported = await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        listing = await client.get("/api/knowledge-packs", headers=_desktop_headers())

    assert imported.status_code == 422
    body = imported.json()
    assert body["error_code"] == "knowledge_pack_parity_rejected"
    assert body["details"] == ["TS validator: registry boom"]
    assert listing.json()["packs"] == []


# ---------------------------------------------------------------------------
# Activate / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_switches_active_and_reports_rematerialization(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    rematerialize_calls = _patch_rematerialize(
        monkeypatch, payload={"ok": True, "mode": "pack", "packId": PACK_ID},
    )
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        activated = await client.post(
            "/api/knowledge-packs/activate",
            headers=_desktop_headers(),
            json={"active": PACK_ID},
        )
        back = await client.post(
            "/api/knowledge-packs/activate",
            headers=_desktop_headers(),
            json={"active": "official"},
        )
        missing = await client.post(
            "/api/knowledge-packs/activate",
            headers=_desktop_headers(),
            json={"active": "com.example.missing"},
        )

    assert activated.status_code == 200, activated.text
    activated_body = activated.json()
    assert activated_body["active"] == PACK_ID
    assert [pack["pack_id"] for pack in activated_body["packs"]] == [PACK_ID]
    # activate 写完 config 后即时调用 sidecar 重物化，切换立刻生效；
    # 两次成功激活各调用一次，404 的未知 pack 不调用。
    assert len(rematerialize_calls) == 2
    assert activated_body["warnings"] == [routes._SIDECAR_APPLIED_WARNING]
    assert back.status_code == 200
    assert back.json()["active"] == "official"
    assert back.json()["warnings"] == [routes._SIDECAR_APPLIED_WARNING]

    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_activate_degrades_to_restart_warning_when_sidecar_unreachable(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    _patch_rematerialize(monkeypatch, payload=None)
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        activated = await client.post(
            "/api/knowledge-packs/activate",
            headers=_desktop_headers(),
            json={"active": PACK_ID},
        )

    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["active"] == PACK_ID
    assert body["warnings"] == [routes._SIDECAR_ACTIVATE_UNREACHABLE_WARNING]


@pytest.mark.asyncio
async def test_delete_active_pack_reverts_to_official(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    _patch_rematerialize(monkeypatch, payload={"ok": True, "mode": "official"})
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        await client.post(
            "/api/knowledge-packs/activate",
            headers=_desktop_headers(),
            json={"active": PACK_ID},
        )
        removed = await client.delete(
            f"/api/knowledge-packs/{PACK_ID}", headers=_desktop_headers(),
        )
        again = await client.delete(
            f"/api/knowledge-packs/{PACK_ID}", headers=_desktop_headers(),
        )

    assert removed.status_code == 200, removed.text
    removed_body = removed.json()
    assert removed_body["active"] == "official"
    assert removed_body["packs"] == []
    assert not (config.DATA_ROOT / "knowledge-packs" / PACK_ID).exists()
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_get_marks_broken_installed_pack_invalid(
    desktop_token, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _patch_parity(monkeypatch, valid=True)
    pack_dir = _write_pack(tmp_path)

    async with await _client() as client:
        await client.post(
            "/api/knowledge-packs/import",
            headers=_desktop_headers(),
            json={"source_path": str(pack_dir)},
        )
        installed_registry = (
            config.DATA_ROOT / "knowledge-packs" / PACK_ID / "knowledge" / "registry.json"
        )
        installed_registry.write_text("{broken", encoding="utf-8")
        listing = await client.get("/api/knowledge-packs", headers=_desktop_headers())

    assert listing.status_code == 200
    pack = listing.json()["packs"][0]
    assert pack["valid"] is False


# ---------------------------------------------------------------------------
# Auth: every endpoint requires the desktop token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_knowledge_pack_endpoints_require_desktop_token(desktop_token):
    async with await _client() as client:
        listing = await client.get("/api/knowledge-packs")
        imported = await client.post(
            "/api/knowledge-packs/import", json={"source_path": "C:/x"},
        )
        activated = await client.post(
            "/api/knowledge-packs/activate", json={"active": "official"},
        )
        removed = await client.delete("/api/knowledge-packs/some.pack")

    assert listing.status_code == 401
    assert imported.status_code == 401
    assert activated.status_code == 401
    assert removed.status_code == 401
