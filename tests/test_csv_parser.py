"""Tests for csv_parser cm/360 calculation (per-game yaw table).

验证 cm/360 = 914.4 / (yaw × Horiz_Sens × DPI) 在各 game yaw 下算对,
以及未知 game / 缺字段 / "cm/360" scale 的 fallback 行为。

信源:Gemini grounding search 2026-07-05(Valorant yaw=0.07 经用户真实 CSV 验证 51.03)。
"""
from __future__ import annotations

import pandas as pd

from kovaak_tracker.csv_parser import KovaaKStats


def _make_stats(config: dict[str, str]) -> KovaaKStats:
    """Construct minimal KovaaKStats for property testing (no real CSV needed)."""
    return KovaaKStats(
        kills=pd.DataFrame(),
        summary={},
        config=config,
        file_name="test",
    )


# ---------------------------------------------------------------------------
# 各 game yaw → cm/360 正确计算
# ---------------------------------------------------------------------------


def test_cm_per_360_valorant():
    """Valorant yaw=0.07: DPI 1600 × Horiz 0.16 → 51.03 cm/360。

    用户真实 CSV 验证(memory user-aim-config 已更新 51)。
    """
    s = _make_stats({"Sens Scale": "Valorant", "DPI": "1600", "Horiz Sens": "0.16"})
    assert s.sens_scale == "Valorant"
    assert s.yaw == 0.07
    assert abs(s.cm_per_360 - 51.03) < 0.01


def test_cm_per_360_source_engine():
    """CSGO/Source yaw=0.022: DPI 800 × Horiz 1.0 → 51.95 cm/360。

    社区共识:CSGO 1.0 sens @ 800 DPI ≈ 52 cm/360。
    """
    s = _make_stats({"Sens Scale": "Source", "DPI": "800", "Horiz Sens": "1.0"})
    assert s.yaw == 0.022
    # 914.4 / (0.022 × 1.0 × 800) = 51.954...
    assert abs(s.cm_per_360 - 51.95) < 0.01


def test_cm_per_360_overwatch():
    """Overwatch yaw=0.0066: 不同 game 不同 yaw(验证 yaw 表区分 game)。"""
    s = _make_stats({"Sens Scale": "Overwatch", "DPI": "1600", "Horiz Sens": "5.0"})
    assert s.yaw == 0.0066
    # 914.4 / (0.0066 × 5.0 × 1600) = 17.318...
    assert abs(s.cm_per_360 - 17.32) < 0.01


# ---------------------------------------------------------------------------
# Fallback:未知 game / 缺字段 / 特殊 scale
# ---------------------------------------------------------------------------


def test_cm_per_360_unknown_game_returns_none():
    """未知 game(如 R6 XFactorAiming)→ yaw=None, cm/360=None(用户需手填)。

    保守策略:不用错公式猜,让用户填。
    """
    s = _make_stats({"Sens Scale": "Rainbow Six Siege", "DPI": "1600", "Horiz Sens": "0.16"})
    assert s.yaw is None
    assert s.cm_per_360 is None


def test_cm_per_360_scale_is_cm360_uses_horiz_directly():
    """KovaaK's 'cm/360' scale: Horiz Sens 直接是 cm/360 物理值(最可靠来源)。

    用户在 KovaaK's 选 'cm/360' scale 时,Horiz Sens 就是实测 cm/360。
    """
    s = _make_stats({"Sens Scale": "cm/360", "Horiz Sens": "48.0"})
    assert s.cm_per_360 == 48.0


def test_cm_per_360_missing_dpi_returns_none():
    """缺 DPI 字段 → 无法算 → None(不崩)。"""
    s = _make_stats({"Sens Scale": "Valorant", "Horiz Sens": "0.16"})
    # dpi property 会 KeyError;cm_per_360 的 try/except 捕获 → None
    assert s.cm_per_360 is None


def test_cm_per_360_missing_horiz_sens_returns_none():
    """缺 Horiz Sens → 无法算 → None(不崩)。"""
    s = _make_stats({"Sens Scale": "Valorant", "DPI": "1600"})
    assert s.cm_per_360 is None


def test_cm_per_360_empty_config_returns_none():
    """空 config → sens_scale='' 不在表里 → None(不崩)。"""
    s = _make_stats({})
    assert s.sens_scale == ""
    assert s.yaw is None
    assert s.cm_per_360 is None
