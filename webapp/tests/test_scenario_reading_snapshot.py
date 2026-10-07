"""诊断读图语境层的快照挂钩测试。

覆盖两处挂钩：
- kovaak_run_store._local_scenario_reading：本机 .sce 解析（本地优先/workshop/
  未装 KovaaK fail-open），独立新键、不参与判型；
- kovaak_run_projection.public_analysis_input_snapshot：新键随快照透出。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from webapp.backend import kovaak_run_store  # noqa: F401 - 先导入以解循环引用
from webapp.backend import kovaak_run_projection
from kovaak_tracker.sce_reading import SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION

_SCE = (
    "Name=Store Synth\n"
    "PlayerCharacters=Player\n"
    "BotCharacters=synth.bot\n"
    "Timelimit=60.0\n"
    "AddedBots=synth.bot\n"
    "InvincibleBots=false\n"
    "Timescale=1.0\n"
    "TimeRefilledByKill=0.0\n"
    "ScoreToWin=1000.0\n"
    "ScorePerDamage=0.0\n"
    "ScorePerKill=1.0\n"
    "ScorePerTime=0.0\n"
    "ScoreLossPerDamageTaken=0.0\n"
    "ScoreLossPerMiss=0.0\n"
    "ScoreMultAccuracy=false\n"
    "\n"
    "[Bot Profile]\n"
    "Name=synth\n"
    "DodgeProfileNames=still\n"
    "CharacterProfile=synthchar\n"
    "\n"
    "[Dodge Profile]\n"
    "Name=still\n"
    "ToggleLeftRight=true\n"
    "MinLRTimeChange=0.2\n"
    "MaxLRTimeChange=0.5\n"
    "\n"
    "[Character Profile]\n"
    "Name=synthchar\n"
    "MaxHealth=1.0\n"
    "MaxSpeed=0.0\n"
    "MovementType=Base\n"
    "\n"
    "[Character Profile]\n"
    "Name=Player\n"
    "MaxHealth=100.0\n"
    "WeaponProfileNames=;;;;;;;\n"
    "\n"
    "[Map Data]\n"
    "reflex map version 8\n"
).encode("utf-8")


def _make_install(tmp_path: Path) -> Path:
    install = tmp_path / "steamapps" / "common" / "FPSAimTrainer"
    scenarios = install / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "Store Synth.sce").write_bytes(_SCE)
    return install


def test_local_scenario_reading_reads_local_sce(tmp_path, monkeypatch):
    install = _make_install(tmp_path)
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(install))
    reading = kovaak_run_store._local_scenario_reading("Store Synth")
    assert reading["availability"] == "available"
    assert reading["schema_version"] == SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION
    # 语义折算：MaxSpeed=0 + 击杀计分 → 静态点击（换位式）
    assert reading["training"]["semantics"] == "static_clicking"
    assert reading["training"]["semantics_basis"] == "reposition_style"
    assert reading["source"]["origin"] == "local_scenarios"


def test_local_scenario_reading_fail_open_when_install_missing(monkeypatch):
    monkeypatch.setenv(
        "KOVAAK_INSTALL_DIR", str(Path(__file__).parent / "no-such-kovaak"),
    )
    reading = kovaak_run_store._local_scenario_reading("Store Synth")
    assert reading["availability"] == "unavailable"
    assert reading["reason"] == "sce_not_found"


def test_local_scenario_reading_fail_open_without_install(monkeypatch):
    # 直接短路安装目录发现：返回 None 模拟"没装 KovaaK / 探测不到"
    monkeypatch.setattr(
        "webapp.backend.config.resolve_kovaak_install_dir", lambda: None,
    )
    reading = kovaak_run_store._local_scenario_reading("Anything")
    assert reading["availability"] == "unavailable"
    assert reading["reason"] == "install_unavailable"


def test_local_scenario_reading_rejects_unsafe_scenario_name(monkeypatch, tmp_path):
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(_make_install(tmp_path)))
    reading = kovaak_run_store._local_scenario_reading(r"..\\evil")
    assert reading["availability"] == "unavailable"
    assert reading["reason"] == "invalid_scenario_name"


def test_public_snapshot_exposes_scenario_reading_descriptor():
    descriptor = {
        "schema_version": SCENARIO_READING_DESCRIPTOR_SCHEMA_VERSION,
        "display_name": "Store Synth",
        "availability": "available",
        "training": {"semantics": "static_clicking"},
    }
    snapshot = {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 1,
        "scenario": "Store Synth",
        "scenario_reading_descriptor": descriptor,
    }
    public = kovaak_run_projection.public_analysis_input_snapshot(snapshot)
    assert public["scenario_reading_descriptor"] == descriptor


def test_public_snapshot_omits_absent_reading_descriptor():
    public = kovaak_run_projection.public_analysis_input_snapshot(
        {"schema_version": "analysis_input_snapshot.v3", "run_id": 1},
    )
    assert "scenario_reading_descriptor" not in public


# ---------------------------------------------------------------------------
# 端到端：build_analysis_input_snapshot 落盘新键（v1 判型输入保持原样）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analysis_input_snapshot_carries_reading_descriptor(
    tmp_path, monkeypatch,
):
    import hashlib

    install = _make_install(tmp_path)
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(install))

    def source_summary(path: Path, parser_version: str) -> dict:
        stat = path.stat()
        return {
            "source": {
                "path": str(path.resolve()),
                "basename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "parser_version": parser_version,
                "availability": "available",
            },
        }

    stats = tmp_path / "Stats.csv"
    performance_file = tmp_path / "Performance.perf"
    stats.write_bytes(b"stats")
    performance_file.write_bytes(b"performance")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="reading-snapshot-run",
        scenario="Store Synth",
        stats_path=str(stats),
        performance_path=str(performance_file),
        stats_summary=source_summary(stats, "kovaak_stats.v1"),
        performance_summary=source_summary(performance_file, "kovaak_performance.v1"),
    )
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    reading = snapshot["scenario_reading_descriptor"]
    assert reading["availability"] == "available"
    assert reading["training"]["semantics"] == "static_clicking"
    # v1 判型输入与语境层并列且互不影响
    assert "scenario_behavior_descriptor" in snapshot
    assert "scenario_resolution" in snapshot
    # 公共投影透出新键
    public = kovaak_run_projection.public_analysis_input_snapshot(snapshot)
    assert public["scenario_reading_descriptor"]["availability"] == "available"


@pytest.mark.asyncio
async def test_analysis_input_snapshot_reading_descriptor_unavailable_without_sce(
    tmp_path, monkeypatch,
):
    import hashlib

    install = tmp_path / "steamapps" / "common" / "FPSAimTrainer"
    (install / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios").mkdir(
        parents=True,
    )
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(install))

    def source_summary(path: Path, parser_version: str) -> dict:
        stat = path.stat()
        return {
            "source": {
                "path": str(path.resolve()),
                "basename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "parser_version": parser_version,
                "availability": "available",
            },
        }

    stats = tmp_path / "Stats.csv"
    performance_file = tmp_path / "Performance.perf"
    stats.write_bytes(b"stats")
    performance_file.write_bytes(b"performance")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="reading-missing-sce-run",
        scenario="No Such Map",
        stats_path=str(stats),
        performance_path=str(performance_file),
        stats_summary=source_summary(stats, "kovaak_stats.v1"),
        performance_summary=source_summary(performance_file, "kovaak_performance.v1"),
    )
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    reading = snapshot["scenario_reading_descriptor"]
    # fail-open：没这张图的 .sce 也不阻塞快照，unavailable 事实入盘
    assert reading["availability"] == "unavailable"
    assert reading["reason"] == "sce_not_found"
