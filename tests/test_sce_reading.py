"""诊断读图语境层（sce_reading）测试。

先于实现编写。覆盖：
- 真实场景验收：pasu small reload → 动态点击/确认与速度匹配 + 击杀回弹经济；
  1w2ts reload → 训练语义=静态点击（换位式），参数事实层如实保留 Mimic 横跳配置
  （实现机制≠训练语义折算判据，用户裁决 2026-10-07）。
- 引用链纪律：躺尸配置（BotCharacters 调色板里但不在 AddedBots 体验里）不进结论。
- fail-open：.sce 缺失/垃圾字节/名字不匹配 → unavailable，绝不抛异常阻塞分析。
- 空间分布档/经济结构/语义折算矩阵（合成 fixture，边界精确）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kovaak_tracker import sce_reading

# 真实场景验收文件（本机 KovaaK 目录；缺失时对应测试自动 skip）
_PASU_SCE = Path(
    r"E:\SteamLibrary\steamapps\workshop\content\824270\1701254068\pasu small reload.sce"
)
_ONEW2TS_SCE = Path(
    r"E:\SteamLibrary\steamapps\common\FPSAimTrainer\FPSAimTrainer\Saved"
    r"\SaveGames\Scenarios\1w2ts reload.sce"
)


def _load_sce(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _requires_real_sce(path: Path) -> bytes:
    data = _load_sce(path)
    if data is None:
        pytest.skip(f"本机不存在 {path.name}，真实场景验收仅在装有 KovaaK 的机器上运行")
    return data


# ---------------------------------------------------------------------------
# 合成 .sce 工厂（键序仿真实文件；Map Data 用 Reflex 原始缩进格式）
# ---------------------------------------------------------------------------

def _sce_text(
    *,
    name: str = "Synth A",
    added_bots: str = "synth.bot",
    bot_characters: str = "synth.bot",
    player_characters: str = "Player",
    score_per_kill: str = "1.0",
    score_per_damage: str = "0.0",
    score_per_time: str = "0.0",
    score_mult_accuracy: str = "false",
    score_loss_per_miss: str = "0.0",
    score_loss_per_damage_taken: str = "0.0",
    time_refilled_by_kill: str = "0.0",
    magazine_max: str = "4",
    ammo_reloaded_on_kill: str = "4",
    weapon_category: str = "SemiAuto",
    time_between_shots: str = "0.1",
    damage_per_shot: str = "1.0",
    target_max_speed: str = "0.0",
    target_max_health: str = "1.0",
    target_radius: str = "60.0",
    lr_times: tuple[str, str] = ("0.2", "0.5"),
    extra_dodge: str = "",
    extra_bot: str = "",
    bot_dodge_names: str = "synthdodge",
    spawns: str = "0.000000 0.000000 -960.000000",
) -> bytes:
    parts = [
        f"Name={name}",
        f"PlayerCharacters={player_characters}",
        f"BotCharacters={bot_characters}",
        "Timelimit=60.0",
        f"AddedBots={added_bots}",
        "InvincibleBots=false",
        "Timescale=1.0",
        f"TimeRefilledByKill={time_refilled_by_kill}",
        "ScoreToWin=1000.0",
        f"ScorePerDamage={score_per_damage}",
        f"ScorePerKill={score_per_kill}",
        f"ScorePerTime={score_per_time}",
        f"ScoreLossPerDamageTaken={score_loss_per_damage_taken}",
        f"ScoreLossPerMiss={score_loss_per_miss}",
        "ScoreLossPerDeath=0.0",
        "ScoreLossPerMidairDirected=0.0",
        "ScoreLossPerAnyDirected=0.0",
        f"ScoreMultAccuracy={score_mult_accuracy}",
        "",
        "[Aim Profile]",
        "Name=Default",
        "MinReactionTime=0.3",
        "MaxReactionTime=0.4",
        "",
        "[Bot Profile]",
        "Name=synth",
        f"DodgeProfileNames={bot_dodge_names}",
        "AimingProfileNames=Default;Default;Default;Default;Default;Default;Default;Default",
        "UseWeapons=false",
        "CharacterProfile=synthchar",
        "",
        extra_bot,
        "[Dodge Profile]",
        "Name=synthdodge",
        "MaxTargetDistance=2500.0",
        "MinTargetDistance=750.0",
        "ToggleLeftRight=true",
        "ToggleForwardBack=false",
        f"MinLRTimeChange={lr_times[0]}",
        f"MaxLRTimeChange={lr_times[1]}",
        "JumpFrequency=0.5",
        "TargetStrafeOverride=Ignore",
        "",
        extra_dodge,
        "[Character Profile]",
        "Name=synthchar",
        f"MaxHealth={target_max_health}",
        f"MaxSpeed={target_max_speed}",
        "MovementType=Base",
        "Gravity=0.0",
        "JumpVelocity=0.0",
        "IsFlyer=false",
        "HealthRegenPerSec=0.0",
        "MinRespawnDelay=0.001",
        "MaxRespawnDelay=0.001",
        "MainBBType=Spheroid",
        f"MainBBRadius={target_radius}",
        "MainBBHasHead=false",
        "WeaponProfileNames=;;;;;;;",
        "",
        "[Character Profile]",
        "Name=Player",
        "MaxHealth=100.0",
        "MaxSpeed=0.0",
        "MovementType=Base",
        "WeaponProfileNames=BB Gun;;;;;;;",
        "",
        "[Weapon Profile]",
        "Name=BB Gun",
        "Type=Hitscan",
        f"Category={weapon_category}",
        f"TimeBetweenShots={time_between_shots}",
        "ShotsPerClick=1",
        f"DamagePerShot={damage_per_shot}",
        f"MagazineMax={magazine_max}",
        f"AmmoReloadedOnKill={ammo_reloaded_on_kill}",
        "ReloadTimeFromEmpty=0.5",
        "HeadshotMultiplier=2.0",
        "HitscanRadius=0.0",
        "",
        "[Map Data]",
        "reflex map version 8",
        "global",
        "\tentity",
        "\t\ttype PlayerSpawn",
        "\t\tVector3 position 0.000000 0.000000 0.000000",
        "\t\tBool8 teamB 0",
        "\t\ttype PlayerSpawn",
        f"\t\tVector3 position {spawns}",
        "\t\tBool8 teamA 0",
        "\t\ttype WorldSpawn",
        "\t\tVector3 position 0.000000 0.000000 0.000000",
    ]
    return ("\n".join(parts) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# 真实场景验收（本机有 .sce 时运行）
# ---------------------------------------------------------------------------

def test_pasu_small_reload_reads_dynamic_clicking_with_kill_ammo_economy():
    data = _requires_real_sce(_PASU_SCE)
    d = sce_reading.build_scenario_reading_descriptor(
        data, display_name="pasu small reload",
    )
    assert d["availability"] == "available"
    assert d["schema_version"] == "scenario_reading_descriptor.v1"
    # 训练语义：移动靶 + 半自动 + 击杀计分 → 动态点击（确认与速度匹配系）
    assert d["training"]["semantics"] == "dynamic_clicking"
    assert d["training"]["primary_domain"] == "confirm_timing"
    assert "smooth_tracking" in d["training"]["domains"]
    # 经济结构：mag7 + AmmoReloadedOnKill 7 → 弹药经济型（命中即续航）；
    # 弹药回弹必须读 Weapon Profile.AmmoReloadedOnKill，不是 header.TimeRefilledByKill
    assert d["economy"]["scoring_camp"] == "kill"
    assert d["economy"]["structures"] == ["ammo_economy"]
    assert d["economy"]["ammo"]["magazine_max"] == 7
    assert d["economy"]["ammo"]["ammo_reloaded_on_kill"] == 7
    # 参数事实：主靶 react 移动（MaxSpeed 1300）+ 跳跃浮空（Gravity 1 / JumpVelocity 1300）
    target = d["parameter_facts"]["targets"][0]
    assert target["character"]["name"] == "react"
    assert target["character"]["max_speed"] == 1300.0
    assert target["character"]["gravity"] == 1.0
    assert target["character"]["jump_velocity"] == 1300.0
    assert target["dodge"]["lr_time_change_s"] == [2.0, 10.0]
    # 玩家武器 pistol：半自动 0.1s、mag7、击杀回弹 7
    weapon = d["parameter_facts"]["player_weapon"]
    assert weapon["name"] == "pistol"
    assert weapon["category"] == "SemiAuto"
    assert weapon["magazine_max"] == 7
    assert weapon["ammo_reloaded_on_kill"] == 7
    # 空间分布：出生网格 span 760uu → 中等档
    assert d["space"]["tier"] == "medium"
    assert d["space"]["basis"] == "player_spawn_grid"
    assert d["space"]["spawn_count"] == 67
    # 证据链：每条结论挂键名+值；弹药回弹证据指向 AmmoReloadedOnKill
    evidence_keys = {row["key"] for row in d["mechanism_evidence"]}
    assert "Weapon Profile.AmmoReloadedOnKill" in evidence_keys
    assert "Character Profile.MaxSpeed" in evidence_keys
    # 引用链：AddedBots 5 个 test.bot；躺尸配置为空
    assert d["parameter_facts"]["bot_count"] == 5
    assert d["parameter_facts"]["dormant_profiles"] == []


def test_1w2ts_reload_mimic_dodge_is_reposition_static_clicking():
    data = _requires_real_sce(_ONEW2TS_SCE)
    d = sce_reading.build_scenario_reading_descriptor(
        data, display_name="1w2ts reload",
    )
    assert d["availability"] == "available"
    # 折算判据（用户裁决 2026-10-07）：主靶 MaxSpeed=0 + hp1 + 击杀计分——
    # 挂着横跳配置但呈现效果是"点完一个、另一个换位置出现"→ 静态点击（换位式）
    assert d["training"]["semantics"] == "static_clicking"
    assert d["training"]["semantics_basis"] == "reposition_style"
    assert d["training"]["primary_domain"] == "static_positioning"
    # 参数事实层如实保留：主靶引用 Mimic 横跳配置（LR 0.2–0.5 + Jump 0.5 + Mimic）
    target = d["parameter_facts"]["targets"][0]
    assert target["dodge"]["name"] == "Mimic"
    assert target["dodge"]["lr_time_change_s"] == [0.2, 0.5]
    assert target["dodge"]["target_strafe_override"] == "Mimic"
    assert target["dodge"]["jump_frequency"] == 0.5
    assert target["dodge"]["movement_inert"] is True
    assert target["character"]["max_speed"] == 0.0
    assert target["character"]["max_health"] == 1.0
    # 经济结构：mag4 + 击杀回弹 4
    assert d["economy"]["scoring_camp"] == "kill"
    assert d["economy"]["structures"] == ["ammo_economy"]
    assert d["economy"]["ammo"]["magazine_max"] == 4
    assert d["economy"]["ammo"]["ammo_reloaded_on_kill"] == 4
    # 引用链纪律：250ms–500ms 自毁 bot 挂在 BotCharacters 调色板里但不在
    # AddedBots 体验里 → 躺尸配置，其 Self Destruct 不进任何结论
    dormant_names = {
        entry["profile"] for entry in d["parameter_facts"]["dormant_profiles"]
    }
    assert {"250ms", "300ms", "400ms", "500ms"} <= dormant_names
    evidence_text = " ".join(
        str(row.get("claim", "")) for row in d["mechanism_evidence"]
    )
    assert "自毁" not in evidence_text
    # 空间分布：出生网格 span 1920uu → 宽分布（长距离拉枪链）
    assert d["space"]["tier"] == "wide"
    # 语义折算证据挂参数：MaxSpeed=0 + hp1 + 击杀计分
    evidence = {
        row["key"]: row["value"] for row in d["mechanism_evidence"]
    }
    assert evidence["Character Profile.MaxSpeed"] == "0.0"


# ---------------------------------------------------------------------------
# fail-open：缺失/垃圾/名字不匹配 → unavailable，不抛异常
# ---------------------------------------------------------------------------

def test_missing_scenario_returns_unavailable_descriptor(tmp_path):
    d = sce_reading.read_scenario_reading_from_dirs(
        "no such scenario", install_dir=tmp_path,
    )
    assert d["availability"] == "unavailable"
    assert d["reason"] == "sce_not_found"
    assert d["schema_version"] == "scenario_reading_descriptor.v1"
    assert "parameter_facts" not in d


def test_garbage_bytes_fail_open_with_parse_failed_reason():
    d = sce_reading.build_scenario_reading_descriptor(
        b"\xff\xfe not a scenario at all", display_name="Whatever",
    )
    assert d["availability"] == "unavailable"
    assert d["reason"] == "sce_parse_failed"


def test_display_name_mismatch_is_unavailable_not_misread():
    data = _sce_text(name="Synth A")
    d = sce_reading.build_scenario_reading_descriptor(
        data, display_name="Synth B",
    )
    assert d["availability"] == "unavailable"
    assert d["reason"] == "display_name_mismatch"


def test_empty_bytes_fail_open():
    d = sce_reading.build_scenario_reading_descriptor(b"", display_name="X")
    assert d["availability"] == "unavailable"
    assert d["reason"] == "sce_parse_failed"


# ---------------------------------------------------------------------------
# 引用链纪律：躺尸配置不读；AddedBots 是体验锚
# ---------------------------------------------------------------------------

def test_palette_only_dodge_profile_is_dormant_and_unused():
    # synth.bot 只引用 synthdodge；dormantdodge 挂在 BotCharacters 的
    # ghost.bot（未 Added）名下 → 躺尸，结论只允许来自 synthdodge
    extra_dodge = (
        "[Dodge Profile]\n"
        "Name=dormantdodge\n"
        "ToggleLeftRight=true\n"
        "MinLRTimeChange=0.05\n"
        "MaxLRTimeChange=0.05\n"
        "JumpFrequency=0.0\n"
        "TargetStrafeOverride=Ignore\n\n"
    )
    extra_bot = (
        "[Bot Profile]\n"
        "Name=ghost\n"
        "DodgeProfileNames=dormantdodge\n"
        "CharacterProfile=synthchar\n\n"
    )
    data = _sce_text(
        added_bots="synth.bot",
        bot_characters="synth.bot;ghost.bot",
        extra_dodge=extra_dodge,
        extra_bot=extra_bot,
    )
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["availability"] == "available"
    dormant = {e["profile"] for e in d["parameter_facts"]["dormant_profiles"]}
    assert "dormantdodge" in dormant
    assert "ghost" in dormant
    # 结论只挂活动靶的变向参数
    assert len(d["parameter_facts"]["targets"]) == 1
    assert d["parameter_facts"]["targets"][0]["dodge"]["name"] == "synthdodge"


def test_addedbots_activation_from_botcharacters_fallback():
    # AddedBots 为空 → 回退 BotCharacters（fail-open，记录 basis）
    data = _sce_text(added_bots="", bot_characters="synth.bot")
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["availability"] == "available"
    assert d["parameter_facts"]["activation_basis"] == "bot_characters_fallback"
    assert len(d["parameter_facts"]["targets"]) == 1


# ---------------------------------------------------------------------------
# 语义折算矩阵（实现机制≠训练语义）
# ---------------------------------------------------------------------------

def test_moving_target_semi_auto_kill_scores_as_dynamic_clicking():
    data = _sce_text(target_max_speed="1300.0", target_max_health="1000.0")
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["training"]["semantics"] == "dynamic_clicking"
    assert d["training"]["semantics_basis"] == "moving_target_clicking"


def test_moving_target_hold_fire_damage_scores_as_continuous_tracking():
    data = _sce_text(
        target_max_speed="1100.0",
        target_max_health="300.0",
        weapon_category="FullyAuto",
        time_between_shots="0.01",
        score_per_kill="0.0",
        score_per_damage="3.0",
    )
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["training"]["semantics"] == "continuous_tracking"
    assert d["training"]["semantics_basis"] == "sustained_hold_fire"


def test_static_targets_without_kill_scoring_stay_plain_static():
    data = _sce_text(score_per_kill="0.0", score_per_damage="0.0")
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["training"]["semantics"] == "static_clicking"
    assert d["training"]["semantics_basis"] == "static_targets"


# ---------------------------------------------------------------------------
# 空间分布档（词汇表域3：窄 <500 / 中等 500–1500 / 宽 >1500；无网格走 dodge 距离）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("spawn", "expected_tier"),
    [
        ("0.000000 0.000000 0.000000", "narrow"),       # span 0
        ("0.000000 0.000000 400.000000", "narrow"),     # span 400 < 500
        ("0.000000 0.000000 500.000000", "medium"),     # span 500
        ("0.000000 0.000000 1499.000000", "medium"),
        ("0.000000 0.000000 1600.000000", "wide"),      # > 1500
    ],
)
def test_space_tier_boundaries(spawn, expected_tier):
    data = _sce_text(spawns=spawn)
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["space"]["tier"] == expected_tier
    assert d["space"]["basis"] == "player_spawn_grid"


def test_no_spawn_grid_falls_back_to_dodge_distance_band():
    # 无 PlayerSpawn 实体的 Map Data：直接剥掉两个出生网格实体块
    text = _sce_text().decode("utf-8")
    text = text.replace(
        "\t\ttype PlayerSpawn\n\t\tVector3 position 0.000000 0.000000 0.000000\n",
        "",
    ).replace(
        "\t\ttype PlayerSpawn\n\t\tVector3 position 0.000000 0.000000 -960.000000\n",
        "",
    )
    d = sce_reading.build_scenario_reading_descriptor(
        text.encode("utf-8"), display_name="Synth A",
    )
    assert d["space"]["basis"] == "dodge_target_distance"
    assert d["space"]["tier"] == "unknown"
    assert d["space"]["target_distance_band"] == [750.0, 2500.0]


# ---------------------------------------------------------------------------
# 经济结构形态（弹药经济型 / 乘数稀释型 / 无惩罚）
# ---------------------------------------------------------------------------

def test_economy_ammo_bounce_beats_multiplier_dilution():
    data = _sce_text(score_mult_accuracy="true")
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["economy"]["structures"] == ["ammo_economy", "multiplier_dilution"]
    assert d["economy"]["multiplier"] == "accuracy"


def test_economy_multiplier_dilution_only():
    data = _sce_text(
        magazine_max="0", ammo_reloaded_on_kill="0", score_mult_accuracy="true",
    )
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["economy"]["structures"] == ["multiplier_dilution"]


def test_economy_none_when_no_structure_keys():
    data = _sce_text(
        magazine_max="0", ammo_reloaded_on_kill="0", score_mult_accuracy="false",
    )
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["economy"]["structures"] == []
    assert d["economy"]["penalty"] == "none"


def test_economy_miss_penalty_detected():
    data = _sce_text(score_loss_per_miss="0.25")
    d = sce_reading.build_scenario_reading_descriptor(data, display_name="Synth A")
    assert d["economy"]["penalty"] == "miss"


# ---------------------------------------------------------------------------
# 读盘入口：本地 Scenarios 优先，其次 workshop
# ---------------------------------------------------------------------------

def test_read_scenario_reading_from_dirs_prefers_local_then_workshop(tmp_path):
    # 仿真实布局：<root>/steamapps/common/<install> 与 <root>/steamapps/workshop
    install = tmp_path / "steamapps" / "common" / "FPSAimTrainer"
    local = install / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    workshop = (
        tmp_path / "steamapps" / "workshop" / "content" / "824270" / "123"
    )
    local.mkdir(parents=True)
    workshop.mkdir(parents=True)
    (local / "Local One.sce").write_bytes(_sce_text(name="Local One"))
    (workshop / "Local One.sce").write_bytes(_sce_text(name="Local One"))

    found = sce_reading.read_scenario_reading_from_dirs(
        "Local One", install_dir=install,
    )
    assert found["availability"] == "available"
    assert found["source"]["origin"] == "local_scenarios"

    for p in local.glob("*.sce"):
        p.unlink()
    found2 = sce_reading.read_scenario_reading_from_dirs(
        "Local One", install_dir=install,
    )
    assert found2["availability"] == "available"
    assert found2["source"]["origin"] == "workshop"
    assert found2["source"]["workshop_id"] == "123"


# ---------------------------------------------------------------------------
# 施工单④⑤：判读档（reading_scope）与 Coach 摘要（scenario_reading_summary）
# ---------------------------------------------------------------------------

def _descriptor(**kwargs):
    return sce_reading.build_scenario_reading_descriptor(
        _sce_text(**kwargs), display_name=kwargs.get("name", "Synth A"),
    )


def test_reading_scope_static_micro_target_is_precision_terminal():
    d = _descriptor(target_radius="8.0")
    assert sce_reading.reading_scope(d) == sce_reading.READING_SCOPE_PRECISION_TERMINAL


def test_reading_scope_static_giant_target_is_speed_throughput():
    d = _descriptor(target_radius="100.0")
    assert sce_reading.reading_scope(d) == sce_reading.READING_SCOPE_SPEED_THROUGHPUT


def test_reading_scope_mid_target_and_missing_descriptor_are_generic():
    assert sce_reading.reading_scope(_descriptor(target_radius="60.0")) == (
        sce_reading.READING_SCOPE_GENERIC
    )
    assert sce_reading.reading_scope(None) == sce_reading.READING_SCOPE_GENERIC
    assert sce_reading.reading_scope(
        {"availability": "unavailable", "reason": "sce_not_found"},
    ) == sce_reading.READING_SCOPE_GENERIC


def test_reading_scope_tracking_semantics_are_generic():
    d = _descriptor(
        target_max_speed="1300.0",
        weapon_category="FullyAuto",
        time_between_shots="0.01",
        score_per_damage="3.0",
        score_per_kill="0.0",
        target_radius="100.0",
    )
    assert d["training"]["semantics"] == "continuous_tracking"
    assert sce_reading.reading_scope(d) == sce_reading.READING_SCOPE_GENERIC


def test_summary_available_shape_and_lines():
    d = _descriptor(target_radius="100.0")
    s = sce_reading.scenario_reading_summary(d)
    assert s["schema_version"] == "scenario_reading_summary.v1"
    assert s["availability"] == "available"
    assert s["reading_scope"] == "speed_throughput"
    assert s["detail_file"] == "scenario_reading.json"
    assert s["training"]["semantics"] == "static_clicking"
    assert s["economy"]["scoring_camp"] == "kill"
    assert 3 <= len(s["lines"]) <= 5
    assert any("判读档" in line for line in s["lines"])
    en = sce_reading.scenario_reading_summary(d, locale="en-US")
    assert any("Reading scope" in line for line in en["lines"])
    assert not any("判读档" in line for line in en["lines"])


def test_summary_unavailable_fails_open():
    s = sce_reading.scenario_reading_summary(
        {"availability": "unavailable", "reason": "sce_not_found"},
    )
    assert s["availability"] == "unavailable"
    assert s["reading_scope"] == "generic"
    assert s["reason"] == "sce_not_found"
    assert s["lines"] == ["读图语境不可用（sce_not_found）"]
    assert sce_reading.scenario_reading_summary(None)["reason"] == "descriptor_missing"
