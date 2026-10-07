"""旁车 bb.json 自动生成（bb_fill + sce_reading 半径层）测试。

覆盖：
- .sce 两级匹配（文件名主干 / Name= 字段）与逐出生实例半径；
- 三层解析顺序：内置表命中 → 表未命中落本机 .sce → 双 miss 诚实降级；
- round_bb.v1 写盘与消费端（telemetry_signals._build_radius_lookup）round-trip；
- 已有 bb.json（含研究产物）绝不覆盖；窗口缺失/写盘失败不崩不写。
- 仓库内置半径表（随应用分发的打包资源）可加载、形状合法。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kovaak_tracker import bb_fill, sce_reading
from kovaak_tracker import telemetry_signals


# ---------------------------------------------------------------------------
# 合成 .sce（最小形状：header + Bot/Character Profile，键序仿真实文件）
# ---------------------------------------------------------------------------

def _sce_bytes(
    *,
    name: str = "Synth A",
    added_bots: str = "synth.bot",
    bot_profile_name: str = "synth",
    char_profile_name: str = "synthchar",
    radius: str = "60.0",
) -> bytes:
    parts = [
        f"Name={name}",
        f"BotCharacters={added_bots}",
        f"AddedBots={added_bots}",
        "Timescale=1.0",
        "",
        "[Bot Profile]",
        f"Name={bot_profile_name}",
        "CharacterProfile=synthchar",
        "",
        "[Character Profile]",
        f"Name={char_profile_name}",
        "MaxHealth=1.0",
        "MaxSpeed=0.0",
        "MovementType=None",
        "MainBBType=Spheroid",
        f"MainBBRadius={radius}",
        "",
    ]
    return ("\n".join(parts) + "\n").encode("utf-8")


@pytest.fixture()
def install_dir(tmp_path: Path) -> Path:
    # 布局仿 Steam：<root>/steamapps/common/<KovaaK 根>（workshop 在 parents[1]）。
    root = tmp_path / "steamapps" / "common" / "FPSAimTrainer"
    scenarios = root / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    scenarios.mkdir(parents=True)
    return root


def _write_sce(install_dir: Path, filename: str, data: bytes) -> Path:
    path = install_dir / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios" / filename
    path.write_bytes(data)
    return path


def _table_entry(radii: list[float]) -> dict:
    return {
        "schema_version": bb_fill.BB_RADIUS_TABLE_SCHEMA_VERSION,
        "entries": {
            "synth a": {
                "scenario": "Synth A",
                "timescale": 1.0,
                "sce_file": "Synth A.sce",
                "sources": ["local_scenarios:x"],
                "bots": [
                    {"profile": "synth", "character": "synthchar",
                     "bb_type": "Spheroid", "radius_cm": r}
                    for r in radii
                ],
            },
        },
    }


# ---------------------------------------------------------------------------
# sce_reading：两级匹配 + 逐实例半径
# ---------------------------------------------------------------------------

def test_bounding_radius_matches_by_file_stem(install_dir: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes())
    result = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert result["availability"] == "available"
    assert result["match_level"] == "file_stem"
    assert result["origin"] == "local_scenarios"
    assert result["radii_cm"] == [60.0]
    assert result["sce_file"] == "Synth A.sce"


def test_bounding_radius_matches_by_header_name(install_dir: Path):
    # stats 场景名（.sce Name= 字段）与文件名主干不同：两级匹配的第二级。
    _write_sce(install_dir, "Odd Stem Name.sce", _sce_bytes(name="Synth A"))
    result = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert result["availability"] == "available"
    assert result["match_level"] == "header_name"
    assert result["radii_cm"] == [60.0]


def test_bounding_radius_matching_is_case_and_whitespace_insensitive(install_dir: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(name="synth  a"))
    result = sce_reading.resolve_scenario_bounding_radius(
        "SYNTH   A", install_dir=install_dir,
    )
    assert result["availability"] == "available"
    assert result["match_level"] == "file_stem"


def test_bounding_radius_expands_per_spawned_instance(install_dir: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(added_bots="synth.bot;synth.bot;synth.bot"))
    result = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert result["radii_cm"] == [60.0, 60.0, 60.0]
    assert len(result["bots"]) == 3
    assert result["bots"][0]["profile"] == "synth"


def test_bounding_radius_fail_open(install_dir: Path):
    missing = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert missing == {
        "availability": "unavailable", "scenario": "Synth A", "reason": "sce_not_found",
    }
    invalid = sce_reading.resolve_scenario_bounding_radius(
        "../evil", install_dir=install_dir,
    )
    assert invalid["availability"] == "unavailable"
    assert invalid["reason"] == "invalid_scenario_name"
    # 解析失败（垃圾字节）不抛异常。
    _write_sce(install_dir, "Synth A.sce", b"not a scenario file at all")
    broken = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert broken["availability"] == "unavailable"


def test_bounding_radius_matched_file_without_radius_keeps_searching(install_dir: Path):
    # 同名文件无半径（悬空引用）→ 记住 no_active_radius，但不阻塞后续池匹配。
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(char_profile_name="missing"))
    result = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert result["availability"] == "unavailable"
    assert result["reason"] == "no_active_radius"


def test_bounding_radius_falls_back_to_workshop_pool(install_dir: Path):
    # local Scenarios 目录存在但为空 → 落到 workshop 池（parents[1] 语义）。
    workshop_root = (
        install_dir.parents[1] / "workshop" / "content" / "824270" / "123456"
    )
    workshop_root.mkdir(parents=True)
    (workshop_root / "Synth A.sce").write_bytes(_sce_bytes(radius="99.0"))
    result = sce_reading.resolve_scenario_bounding_radius(
        "Synth A", install_dir=install_dir,
    )
    assert result["availability"] == "available"
    assert result["origin"] == "workshop"
    assert result["workshop_id"] == "123456"
    assert result["radii_cm"] == [99.0]


# ---------------------------------------------------------------------------
# bb_fill：三层解析顺序
# ---------------------------------------------------------------------------

def test_resolve_prefers_builtin_table_over_local_sce(install_dir: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(radius="60.0"))
    result = bb_fill.resolve_target_radii(
        "SYNTH  a", install_dir=install_dir, table=_table_entry([42.0]),
    )
    assert result["availability"] == "available"
    assert result["source"] == "builtin-table"
    assert result["radii_cm"] == [42.0]


def test_resolve_falls_to_local_sce_on_table_miss(install_dir: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(radius="60.0"))
    result = bb_fill.resolve_target_radii(
        "Synth A", install_dir=install_dir, table={"schema_version": "x", "entries": {}},
    )
    assert result["availability"] == "available"
    assert result["source"] == "local-sce"
    assert result["radii_cm"] == [60.0]
    assert result["sce_path"] is not None


def test_resolve_double_miss_degrades_honestly(install_dir: Path):
    result = bb_fill.resolve_target_radii(
        "Synth A", install_dir=install_dir, table={"schema_version": "x", "entries": {}},
    )
    assert result["availability"] == "unavailable"
    assert result["reason"] == "table_miss+sce_not_found"
    assert result["radii_cm"] == []


def test_resolve_without_install_reports_install_unavailable():
    result = bb_fill.resolve_target_radii(
        "Synth A", install_dir=None, table={"schema_version": "x", "entries": {}},
    )
    assert result["availability"] == "unavailable"
    assert result["reason"] == "table_miss+install_unavailable"


# ---------------------------------------------------------------------------
# ensure_round_bb：写盘、不覆盖、round-trip
# ---------------------------------------------------------------------------

def test_ensure_round_bb_roundtrip_with_radius_lookup(install_dir: Path, tmp_path: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(radius="60.0"))
    round_dir = tmp_path / "ext-roundtrip"
    round_dir.mkdir()
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A",
        install_dir=install_dir,
        round_number=3,
        window_t=(10.0, 70.5),
        table={"schema_version": "x", "entries": {}},
    )
    assert outcome["status"] == "written"
    assert outcome["source"] == "local-sce"
    # 消费端 round-trip：_build_radius_lookup 读出正确半径窗（中位 60）。
    windows, fallback, limitations = telemetry_signals._build_radius_lookup(round_dir)
    assert windows == [(10.0, 70.5, 60.0)]
    assert fallback == 60.0
    assert limitations == []
    radius, measured = telemetry_signals._radius_at(windows, fallback, 35.0)
    assert (radius, measured) == (60.0, True)
    # 写盘产物形状：round_bb.v1 + 挑战窗 + 逐实例 bots。
    document = json.loads((round_dir / "bb.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == "round_bb.v1"
    challenge = document["challenges"][0]
    assert challenge["window_t"] == [10.0, 70.5]
    assert challenge["scenario"] == "Synth A"
    assert challenge["bots"][0]["character"]["bb"]["radius"] == 60.0


def test_ensure_round_bb_mixed_radii_median_with_limitation(install_dir: Path, tmp_path: Path):
    # 两个不同半径的出生 bot：消费端取中位并记 bb_radius_mixed limitation。
    parts = _sce_bytes(added_bots="synth.bot;synthbig.bot").decode("utf-8")
    parts += (
        "[Bot Profile]\n"
        "Name=synthbig\n"
        "CharacterProfile=bigchar\n"
        "\n"
        "[Character Profile]\n"
        "Name=bigchar\n"
        "MaxHealth=1.0\n"
        "MainBBType=Spheroid\n"
        "MainBBRadius=120.0\n"
        "\n"
    )
    _write_sce(install_dir, "Synth A.sce", parts.encode("utf-8"))
    round_dir = tmp_path / "ext-mixed"
    round_dir.mkdir()
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A", install_dir=install_dir,
        round_number=1, window_t=(0.0, 60.0),
        table={"schema_version": "x", "entries": {}},
    )
    assert outcome["status"] == "written"
    windows, fallback, limitations = telemetry_signals._build_radius_lookup(round_dir)
    assert windows == [(0.0, 60.0, 90.0)]
    assert fallback == 90.0
    assert limitations == ["bb_radius_mixed_within_challenge_median"]


def test_ensure_round_bb_table_hit_writes_cached_bb(tmp_path: Path):
    round_dir = tmp_path / "ext-table"
    round_dir.mkdir()
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A",
        install_dir=tmp_path / "missing-kovaak",
        round_number=1,
        window_t=(5.0, 65.0),
        table=_table_entry([42.0]),
    )
    assert outcome["status"] == "written"
    assert outcome["source"] == "builtin-table"
    # 本机 .sce 不存在也出数：表命中即可写缓存。
    windows, _, _ = telemetry_signals._build_radius_lookup(round_dir)
    assert windows == [(5.0, 65.0, 42.0)]


def test_ensure_round_bb_never_overwrites_existing(install_dir: Path, tmp_path: Path):
    _write_sce(install_dir, "Synth A.sce", _sce_bytes(radius="60.0"))
    round_dir = tmp_path / "ext-existing"
    round_dir.mkdir()
    existing = {"schema_version": "round_bb.v1", "challenges": [], "guard": "research"}
    (round_dir / "bb.json").write_text(json.dumps(existing), encoding="utf-8")
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A", install_dir=install_dir,
        round_number=1, window_t=(0.0, 60.0), table=None,
    )
    assert outcome["status"] == "already_present"
    assert not outcome["written"]
    assert json.loads((round_dir / "bb.json").read_text(encoding="utf-8")) == existing


def test_ensure_round_bb_window_missing_degrades_without_write(tmp_path: Path):
    round_dir = tmp_path / "ext-nowindow"
    round_dir.mkdir()
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A", install_dir=tmp_path,
        round_number=1, window_t=(None, None), table=_table_entry([42.0]),
    )
    assert outcome == {
        "status": "unavailable", "written": False, "reason": "round_window_unavailable",
    }
    assert not (round_dir / "bb.json").exists()


def test_ensure_round_bb_double_miss_degrades_without_write(install_dir: Path, tmp_path: Path):
    round_dir = tmp_path / "ext-miss"
    round_dir.mkdir()
    outcome = bb_fill.ensure_round_bb(
        round_dir, "Synth A", install_dir=install_dir,
        round_number=1, window_t=(0.0, 60.0),
        table={"schema_version": "x", "entries": {}},
    )
    assert outcome["status"] == "unavailable"
    assert outcome["reason"] == "table_miss+sce_not_found"
    assert not (round_dir / "bb.json").exists()


def test_write_round_bb_failure_is_swallowed(tmp_path: Path):
    # round_dir 是文件 → 写盘必败：返回 False、不抛、不留 tmp。
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    assert bb_fill.write_round_bb(blocker, {"schema_version": "round_bb.v1"}) is False


# ---------------------------------------------------------------------------
# 随应用分发的内置半径表（本仓库打包资源）
# ---------------------------------------------------------------------------

def test_bundled_radius_table_loads_and_is_wellformed():
    table = bb_fill.load_radius_table()
    assert table is not None, "knowledge/scenarios/bb-radius.v1.json 必须随仓库分发且可加载"
    assert table["schema_version"] == bb_fill.BB_RADIUS_TABLE_SCHEMA_VERSION
    entries = table["entries"]
    assert isinstance(entries, dict) and entries
    for key, entry in entries.items():
        assert key == " ".join(key.split()).casefold()
        assert entry["scenario"]
        radii = [b["radius_cm"] for b in entry["bots"]]
        assert radii and all(isinstance(r, (int, float)) and r > 0 for r in radii)


def test_bundled_radius_table_known_scenarios():
    table = bb_fill.load_radius_table()
    entries = table["entries"]
    # 与研究期验证产物对账过的真值锚（bb.json 样例：Tile Frenzy 124 / 1w2ts 60）。
    assert [b["radius_cm"] for b in entries["tile frenzy 180 strafing tracking"]["bots"]] == [124.0] * 5
    assert [b["radius_cm"] for b in entries["1w2ts reload"]["bots"]] == [60.0] * 2
