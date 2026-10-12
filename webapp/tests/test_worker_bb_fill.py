"""worker bb 懒生成接线（_ensure_round_bb_for_external_run）测试。

覆盖：缺 bb 时经内置表/本机 .sce 现场生成缓存；双 miss 降级不崩不写；
已有 bb.json（含研究产物）绝不覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kovaak_tracker import bb_fill
from kovaak_tracker.telemetry_signals import _build_radius_lookup
from webapp.backend.worker import _ensure_round_bb_for_external_run


def _install_dir_with_sce(tmp_path: Path) -> Path:
    root = tmp_path / "steamapps" / "common" / "FPSAimTrainer"
    scenarios = root / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    scenarios.mkdir(parents=True)
    sce = "\n".join([
        "Name=Synth A",
        "BotCharacters=synth.bot",
        "AddedBots=synth.bot",
        "Timescale=1.0",
        "",
        "[Bot Profile]",
        "Name=synth",
        "CharacterProfile=synthchar",
        "",
        "[Character Profile]",
        "Name=synthchar",
        "MaxHealth=1.0",
        "MainBBType=Spheroid",
        "MainBBRadius=60.0",
        "",
    ])
    (scenarios / "Synth A.sce").write_text(sce, encoding="utf-8")
    return root


def _table_file(tmp_path: Path, radii: list[float]) -> Path:
    table = {
        "schema_version": bb_fill.BB_RADIUS_TABLE_SCHEMA_VERSION,
        "entries": {
            "synth a": {
                "scenario": "Synth A",
                "timescale": 1.0,
                "sce_file": "Synth A.sce",
                "sources": ["test"],
                "bots": [
                    {"profile": "synth", "character": "synthchar",
                     "bb_type": "Spheroid", "radius_cm": r}
                    for r in radii
                ],
            },
        },
    }
    path = tmp_path / "bb-radius.v1.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return path


@pytest.fixture()
def no_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bb_fill, "RADIUS_TABLE_PATH", tmp_path / "missing-table.json")


def _run(
    monkeypatch: pytest.MonkeyPatch,
    install_dir: Path | None,
    round_dir: Path,
    *,
    scenario: str = "Synth A",
) -> None:
    monkeypatch.setattr(
        "webapp.backend.config.resolve_kovaak_install_dir", lambda: install_dir,
    )
    _ensure_round_bb_for_external_run(
        {"scenario": scenario},
        "ext-test",
        round_dir,
        1,
        {"time": {"t_start": 0.0, "t_end": 60.0}},
    )


def test_lazy_fill_generates_from_local_sce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_table: None,
) -> None:
    install_dir = _install_dir_with_sce(tmp_path)
    round_dir = tmp_path / "ext-a"
    round_dir.mkdir()
    _run(monkeypatch, install_dir, round_dir)
    windows, _, limitations = _build_radius_lookup(round_dir)
    assert windows == [(0.0, 60.0, 60.0)]
    assert limitations == []


def test_lazy_fill_generates_from_builtin_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        bb_fill, "RADIUS_TABLE_PATH", _table_file(tmp_path, [42.0]),
    )
    round_dir = tmp_path / "ext-b"
    round_dir.mkdir()
    # 安装目录不存在（conftest 默认 missing-kovaak）：表命中仍出数。
    _run(monkeypatch, tmp_path / "missing-kovaak", round_dir)
    windows, _, _ = _build_radius_lookup(round_dir)
    assert windows == [(0.0, 60.0, 42.0)]


def test_lazy_fill_double_miss_degrades_without_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_table: None,
) -> None:
    round_dir = tmp_path / "ext-c"
    round_dir.mkdir()
    _run(monkeypatch, tmp_path / "missing-kovaak", round_dir)
    assert not (round_dir / "bb.json").exists()


def test_lazy_fill_never_overwrites_existing_bb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        bb_fill, "RADIUS_TABLE_PATH", _table_file(tmp_path, [42.0]),
    )
    round_dir = tmp_path / "ext-d"
    round_dir.mkdir()
    existing = {"schema_version": "round_bb.v1", "challenges": [], "guard": "research"}
    (round_dir / "bb.json").write_text(json.dumps(existing), encoding="utf-8")
    _run(monkeypatch, tmp_path / "missing-kovaak", round_dir)
    assert json.loads((round_dir / "bb.json").read_text(encoding="utf-8")) == existing


def test_lazy_fill_missing_window_degrades_without_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        bb_fill, "RADIUS_TABLE_PATH", _table_file(tmp_path, [42.0]),
    )
    monkeypatch.setattr(
        "webapp.backend.config.resolve_kovaak_install_dir", lambda: None,
    )
    round_dir = tmp_path / "ext-e"
    round_dir.mkdir()
    _ensure_round_bb_for_external_run(
        {"scenario": "Synth A"}, "ext-e", round_dir, 1, {"time": {}},
    )
    assert not (round_dir / "bb.json").exists()
