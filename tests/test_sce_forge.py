"""SCE 生成器（sce_forge）测试。先于实现编写。

覆盖：
- 8 条内置配方书规则（6 既有 + v0.3 新增 R9.5 gauntlet 计时器 / R4.1-IR 改良换弹）：
  生成 → sce_reading 回读自检全对 → 合法落盘（写入临时目录，
  不触碰真实 KovaaK 目录；目录经参数显式注入，禁止硬编码）。
- 自检铁律：篡改生成文本或篡改处方 → 自检必须 raise 且带明细；
  未通过自检的 .sce 绝不落盘。
- 难度递进坡：R1.1 变向周期阶梯 -0.1s/档语法。
- 命名规范：三种候选命名输出稳定。
- 生成器×读回器闭环互证：semantics（tracking/点击系）、空间分布档（wide/narrow/
  medium/none）、弹药经济结构。
- 已存在文件拒绝覆盖，原文件字节不被改动。
- v0.3 反哺（intent-crosscheck 无冲突项）：负回血计时器机关（R9.5）、
  S5 基准型 FOV 头选项、同屏多靶关碰撞勾选位、改良换弹三键（R4.1-IR）。
  sce_reading 事实层未暴露的键（ScorePerHit/AmmoPerShot/FOV 系/
  DisableCharacterCollision）按 HealthRegenDelay 先例用文本级断言。
"""
from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

import pytest

from kovaak_tracker import sce_forge, sce_reading

ALL_RULES = ["R1.1", "R2.1", "R3.6", "R4.1", "R9.3", "R8.3", "R9.5", "R4.1-IR"]

# 空间分布档判据（capability-vocabulary 域3：宽 >1500 / 窄 <500 / 中等 500–1500）
_WIDE_MIN, _NARROW_MAX = 1500.0, 500.0


def _descriptor(data: bytes, name: str) -> dict:
    desc = sce_reading.build_scenario_reading_descriptor(data, display_name=name)
    assert desc["availability"] == "available", desc
    return desc


# ---------------------------------------------------------------------------
# 内置规则注册表
# ---------------------------------------------------------------------------

def test_builtin_rules_registry():
    assert set(sce_forge.FORGE_PRESETS) == set(ALL_RULES)
    for rule_id in ALL_RULES:
        prescription = sce_forge.FORGE_PRESETS[rule_id](1)
        assert isinstance(prescription, sce_forge.ForgePrescription)
        assert prescription.rule_id == rule_id
        assert prescription.name
        assert prescription.domain
        assert prescription.expected_semantics


# ---------------------------------------------------------------------------
# 每条内置规则：生成 → 回读自检全对 → 合法落盘
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule_id", ALL_RULES)
def test_rule_forge_self_check_and_write(rule_id, tmp_path):
    prescription = sce_forge.FORGE_PRESETS[rule_id](1)
    name = sce_forge.naming_scheme(prescription)
    assert name.startswith("AC-")
    data = sce_forge.forge_sce_text(prescription, name=name)

    # 铁律：自检全对
    report = sce_forge.self_check(data, prescription, name=name)
    assert report["ok"] is True
    assert report["failed"] == []
    assert report["passed"] > 0

    # 落盘（临时目录，显式注入）
    result = sce_forge.forge_and_write(prescription, scenarios_dir=tmp_path)
    assert result["self_check_report"]["ok"] is True
    written_path = Path(result["path"])
    assert written_path.exists()
    assert written_path.name == f"{result['name']}.sce"
    written = written_path.read_bytes()
    # 字节级 UTF-8、CRLF、无 BOM（与真实 .sce 一致）
    assert not written.startswith(b"\xef\xbb\xbf")
    assert b"\n" in written and written.replace(b"\r\n", b"").find(b"\n") == -1
    # 落盘文件同样通过读回器
    assert _descriptor(written, result["name"])["availability"] == "available"


# ---------------------------------------------------------------------------
# 自检抓错：篡改生成文本 / 篡改处方 → 必须 raise
# ---------------------------------------------------------------------------

def test_self_check_catches_tampered_text():
    prescription = sce_forge.forge_R1_1(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    assert b"MaxHealth=300.0" in data
    tampered = data.replace(b"MaxHealth=300.0", b"MaxHealth=999.0")
    with pytest.raises(sce_forge.SelfCheckError) as excinfo:
        sce_forge.self_check(tampered, prescription, name=name)
    message = str(excinfo.value)
    assert "target.max_health" in message and "999.0" in message and "字节未放行" in message


def test_self_check_catches_tampered_prescription():
    prescription = sce_forge.forge_R1_1(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    wrong = dataclasses.replace(prescription, target_radius=88.0)
    with pytest.raises(sce_forge.SelfCheckError):
        sce_forge.self_check(data, wrong, name=name)


def test_failed_self_check_never_writes(tmp_path):
    """铁律：未通过自检的字节绝不落盘。

    forge_and_write 内部强制"生成→自检→写盘"顺序；此处验证两条失败路径
    都不产生任何文件：处方非法（生成阶段 raise）与处方/字节不一致（自检 raise）。
    """
    prescription = sce_forge.forge_R3_6(1)
    # 路径一：处方非法 → 生成阶段即 raise
    invalid = dataclasses.replace(prescription, space_tier="bogus")
    with pytest.raises(sce_forge.ForgeError):
        sce_forge.forge_and_write(invalid, scenarios_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    # 路径二：字节自 A 处方生成、按 B 处方比对 → 自检抓错不放行
    name = sce_forge.naming_scheme(prescription)
    data_from_broken = sce_forge.forge_sce_text(
        dataclasses.replace(prescription, target_max_health=55.0), name=name
    )
    with pytest.raises(sce_forge.SelfCheckError):
        sce_forge.self_check(data_from_broken, prescription, name=name)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# 难度递进坡：变向周期阶梯 -0.1s/档
# ---------------------------------------------------------------------------

def test_difficulty_ladder_r11():
    p1 = sce_forge.forge_R1_1(1)
    p2 = sce_forge.forge_R1_1(2)
    p4 = sce_forge.forge_R1_1(4)
    assert p1.lr_time_change == (0.4, 0.8)
    assert p2.lr_time_change == (0.3, 0.7)
    assert p4.lr_time_change == (0.1, 0.5)
    assert p1.tier_label == "A1" and p4.tier_label == "A4"
    # 每一档都通过回读自检
    for prescription in (p1, p2, p4):
        name = sce_forge.naming_scheme(prescription)
        data = sce_forge.forge_sce_text(prescription, name=name)
        assert sce_forge.self_check(data, prescription, name=name)["ok"] is True
    # 命名随档位区分
    assert sce_forge.naming_scheme(p2) == "AC-VarResp-A2"


def test_ladder_floor_never_below_reaction_band():
    for tier in range(1, 9):
        prescription = sce_forge.forge_R1_1(tier)
        min_lr, max_lr = prescription.lr_time_change
        assert min_lr >= sce_forge.LR_FLOOR
        assert min_lr <= max_lr


# ---------------------------------------------------------------------------
# 命名规范
# ---------------------------------------------------------------------------

def test_naming_scheme_stable():
    prescription = sce_forge.forge_R1_1(1)
    assert sce_forge.naming_scheme(prescription, "code") == "AC-VarResp-A1"
    assert sce_forge.naming_scheme(prescription, "cn_tier") == "AC 变向响应 A1档"
    assert (
        sce_forge.naming_scheme(prescription, "cn_dated", date=date(2026, 10, 7))
        == "AC 变向响应 A1档·20261007"
    )
    # 幂等：重复调用输出一致（code/cn_tier 不含日期）
    for style in ("code", "cn_tier"):
        assert sce_forge.naming_scheme(prescription, style) == sce_forge.naming_scheme(
            prescription, style
        )


def test_naming_candidates_three_styles():
    prescription = sce_forge.forge_R1_1(1)
    candidates = sce_forge.naming_candidates(prescription, date=date(2026, 10, 7))
    assert [c["style"] for c in candidates] == ["code", "cn_tier", "cn_dated"]
    assert candidates[0]["name"] == "AC-VarResp-A1"
    assert candidates[2]["name"] == "AC 变向响应 A1档·20261007"
    for candidate in candidates:
        assert candidate["note"]  # 每个样例附 KovaaK 列表效果说明
        assert not re.search(r'[<>:"/\\|?*]', candidate["name"])


# ---------------------------------------------------------------------------
# 已存在文件拒绝覆盖
# ---------------------------------------------------------------------------

def test_refuse_overwrite(tmp_path):
    prescription = sce_forge.forge_R2_1(1)
    first = sce_forge.forge_and_write(prescription, scenarios_dir=tmp_path)
    original = Path(first["path"]).read_bytes()
    with pytest.raises(sce_forge.ForgeWriteError) as excinfo:
        sce_forge.forge_and_write(prescription, scenarios_dir=tmp_path)
    assert first["name"] in str(excinfo.value)
    # 原文件字节未被改动
    assert Path(first["path"]).read_bytes() == original


# ---------------------------------------------------------------------------
# 生成器 × 读回器闭环互证
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rule_id", "expected_semantics"),
    [
        ("R1.1", "continuous_tracking"),
        ("R2.1", "continuous_tracking"),
        ("R9.3", "continuous_tracking"),
        ("R8.3", "continuous_tracking"),
        ("R9.5", "continuous_tracking"),
        ("R3.6", "static_clicking"),
        ("R4.1", "dynamic_clicking"),
        ("R4.1-IR", "static_clicking"),
    ],
)
def test_training_semantics_closed_loop(rule_id, expected_semantics):
    prescription = sce_forge.FORGE_PRESETS[rule_id](1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    assert descriptor["training"]["semantics"] == expected_semantics


def test_space_tiers_closed_loop():
    # R3.6 宽分布 → wide tier（span > 1500）
    wide = sce_forge.forge_R3_6(1)
    name = sce_forge.naming_scheme(wide)
    desc = _descriptor(sce_forge.forge_sce_text(wide, name=name), name)
    assert desc["space"]["tier"] == "wide"
    assert desc["space"]["max_span_uu"] > _WIDE_MIN
    # R4.1 纯微调场 → narrow tier（span < 500，微调场默认窄分布形态）
    narrow = sce_forge.forge_R4_1(1)
    name = sce_forge.naming_scheme(narrow)
    desc = _descriptor(sce_forge.forge_sce_text(narrow, name=name), name)
    assert desc["space"]["tier"] == "narrow"
    assert desc["space"]["max_span_uu"] < _NARROW_MAX
    # R8.3 生存计分 → medium tier（500–1500）
    medium = sce_forge.forge_R8_3(1)
    name = sce_forge.naming_scheme(medium)
    desc = _descriptor(sce_forge.forge_sce_text(medium, name=name), name)
    assert desc["space"]["tier"] == "medium"
    assert _NARROW_MAX <= desc["space"]["max_span_uu"] <= _WIDE_MIN
    # R1.1 tracking 无出生网格 → 分布轴由 dodge dist 承担（D6）
    none_tier = sce_forge.forge_R1_1(1)
    name = sce_forge.naming_scheme(none_tier)
    desc = _descriptor(sce_forge.forge_sce_text(none_tier, name=name), name)
    assert desc["space"]["basis"] == "dodge_target_distance"
    assert desc["space"]["target_distance_band"] == [750.0, 2500.0]


def test_ammo_economy_closed_loop():
    """R4.1 限弹+杀后回弹 → 读回器应报 ammo_economy 结构（弹药回弹真键）。"""
    prescription = sce_forge.forge_R4_1(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    assert "ammo_economy" in descriptor["economy"]["structures"]
    weapon = descriptor["parameter_facts"]["player_weapon"]
    assert weapon["magazine_max"] == 3
    assert weapon["ammo_reloaded_on_kill"] == 3


def test_regen_pressure_closed_loop():
    """R9.3 回血靶真实键值：Delay=0.03s、Regen=62.5/s（原册 0.3s 笔误已修正口径）。

    Regen 率经读回器闭环；Delay 不在 sce_reading 事实层暴露面内 → 文本级断言。
    """
    prescription = sce_forge.forge_R9_3(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    character = descriptor["parameter_facts"]["targets"][0]["character"]
    assert character["health_regen_per_sec"] == pytest.approx(62.5)
    assert b"HealthRegenDelay=0.03" in data


def test_multi_bot_instances():
    """R8.3 多 bot 同屏（Ground Plaza 变异位）：AddedBots 实例数进读回器 bot_count。"""
    prescription = sce_forge.forge_R8_3(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    assert descriptor["parameter_facts"]["bot_count"] == 3


# ---------------------------------------------------------------------------
# v0.3 反哺（intent-crosscheck 无冲突项）
# ---------------------------------------------------------------------------

def test_gauntlet_timer_closed_loop():
    """R9.5 gauntlet 计时器：负回血=固定时长机关（官方原文 S5 blog §1.8 + PGT 指纹）。

    存活时长=MaxHealth÷|regen| 经读回器闭环（regen/血量均在事实层暴露面）；
    ScorePerHit / respawn 1.48s 不在事实层 → 文本级断言（HealthRegenDelay 先例）。
    """
    prescription = sce_forge.forge_R9_5(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    character = descriptor["parameter_facts"]["targets"][0]["character"]
    # 官方 Novice 档：MaxHealth=1900 × HealthRegenPerSec=-100 = 恰 19s（逐值命中）
    assert character["health_regen_per_sec"] == pytest.approx(-100.0)
    assert character["max_health"] == pytest.approx(1900.0)
    assert character["max_health"] / abs(character["health_regen_per_sec"]) == pytest.approx(19.0)
    # 时长可指定：25s 档 → 血量 2500，同样通过回读自检
    p25 = sce_forge.forge_R9_5(1, target_duration_s=25.0)
    name25 = sce_forge.naming_scheme(p25)
    report = sce_forge.self_check(sce_forge.forge_sce_text(p25, name=name25), p25, name=name25)
    assert report["ok"] is True
    # 文本级：按命中计分 + 序列接续节奏（respawn 1.48s 取自 PGT 原文键）
    assert b"ScorePerHit=1.0" in data
    assert b"MinRespawnDelay=1.48" in data
    # 序列制单活靶：单 bot 实例、靶角色碰撞保持 false（纪律 22 分工）
    assert descriptor["parameter_facts"]["bot_count"] == 1
    assert data.count(b"DisableCharacterCollision=true") == 0


def test_fov_lock_s5_option():
    """S5 基准型 FOV 头选项（纪律 19 官方原文 §1.2 + 指纹 103–140 Clamped Horizontal）。

    默认预设保持 lock=false + 60/120（模板默认对，写了未启用）；S5 选项开启时写
    true/103.0/140.0/Clamped Horizontal 四键组。FOV 键不在 sce_reading 事实层 → 文本级断言。
    """
    default = sce_forge.forge_sce_text(sce_forge.forge_R1_1(1), name="AC-VarResp-A1")
    assert b"LockFOVRange=false" in default
    assert b"LockedFOVMin=60.0" in default and b"LockedFOVMax=120.0" in default
    # 选项对任意预设可用
    opted = sce_forge.forge_sce_text(
        dataclasses.replace(sce_forge.forge_R1_1(1), lock_fov_s5=True), name="AC-VarResp-A1"
    )
    assert b"LockFOVRange=true" in opted
    assert b"LockedFOVMin=103.0" in opted
    assert b"LockedFOVMax=140.0" in opted
    assert b"LockedFOVScale=Clamped Horizontal" in opted
    # S5 出处预设（R9.5 gauntlet / R4.1-IR 改良换弹）默认带 S5 头
    for factory in (sce_forge.forge_R9_5, sce_forge.forge_R4_1_IR):
        prescription = factory(1)
        name = sce_forge.naming_scheme(prescription)
        data = sce_forge.forge_sce_text(prescription, name=name)
        assert b"LockFOVRange=true" in data
        assert b"LockedFOVMin=103.0" in data and b"LockedFOVMax=140.0" in data


def test_disable_character_collision_option():
    """同屏多靶关碰撞勾选位（纪律 22：官方原文 §1.7 + 指纹三 true/三 false 分工）。

    ≥2 同屏靶才允许勾选；勾选位写靶角色 DisableCharacterCollision=true。
    键不在 sce_reading 事实层 → 文本级断言（HealthRegenDelay 先例）。
    """
    prescription = sce_forge.forge_R4_1_IR(1)   # 同屏 5 靶 → 默认开
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    assert descriptor["parameter_facts"]["bot_count"] == 5
    assert data.count(b"DisableCharacterCollision=true") == 1   # 仅靶角色；玩家角色 false
    # 序列/单靶预设保持 false
    single = sce_forge.forge_sce_text(sce_forge.forge_R1_1(1), name="AC-VarResp-A1")
    assert b"DisableCharacterCollision=true" not in single
    # 纪律守卫：同屏 <2 靶时勾选属非法处方
    invalid = dataclasses.replace(prescription, bot_instances=1)
    with pytest.raises(sce_forge.ForgeError):
        sce_forge.forge_sce_text(invalid, name=name)


def test_improved_reload_ammo_triple():
    """R4.1-IR 改良换弹三键（官方原文 S5 blog §1.1：clip100/耗30/返37）。

    MagazineMax/AmmoReloadedOnKill 在读回器事实层闭环；AmmoPerShot 不在暴露面
    → 文本级断言（HealthRegenDelay 先例）。附 mag=0 躺尸对陷阱的机制面验证
    （纪律 10 方法论警示：只看 AmmoReloadedOnKill 会误判）。
    """
    prescription = sce_forge.forge_R4_1_IR(1)
    name = sce_forge.naming_scheme(prescription)
    data = sce_forge.forge_sce_text(prescription, name=name)
    descriptor = _descriptor(data, name)
    weapon = descriptor["parameter_facts"]["player_weapon"]
    assert weapon["magazine_max"] == 100
    assert weapon["ammo_reloaded_on_kill"] == 37
    assert b"AmmoPerShot=30" in data
    assert "ammo_economy" in descriptor["economy"]["structures"]
    # 改良换弹与平方根精度计分二选一（官方原文 §1.1）→ 准确率乘数保持关闭
    assert descriptor["economy"]["multiplier"] == "none"
    # 陷阱机制面：mag=0 时弹药对变躺尸——economy 不再报 ammo_economy 结构
    dormant = dataclasses.replace(prescription, magazine_max=0)
    dormant_desc = _descriptor(sce_forge.forge_sce_text(dormant, name=name), name)
    assert "ammo_economy" not in dormant_desc["economy"]["structures"]


# ---------------------------------------------------------------------------
# 目录发现：不硬编码，无目录来源时报错
# ---------------------------------------------------------------------------

def test_write_without_directory_source_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("KOVAAK_INSTALL_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    prescription = sce_forge.forge_R1_1(1)
    with pytest.raises(sce_forge.ForgeWriteError):
        sce_forge.forge_and_write(prescription)


def test_write_via_install_dir_override(monkeypatch, tmp_path):
    """KOVAAK_INSTALL_DIR env 与现有 config 发现逻辑同源（env 优先契约）。"""
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(tmp_path))
    prescription = sce_forge.forge_R1_1(1)
    result = sce_forge.forge_and_write(prescription)
    expected = tmp_path / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    assert Path(result["path"]).parent == expected
    assert Path(result["path"]).exists()
