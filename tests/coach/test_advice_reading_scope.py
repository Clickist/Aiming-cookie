"""施工单⑤验收：场景读图判读档（reading_scope）驱动的 reverse_ratio 分支。

审计标的（.zcode/route-mining/coach-chain-audit-tilefrenzy.md 第五节）：
Tile Frenzy（速度吞吐型点击图）局——命中 95.5%、约 0.29s/杀、收尾反向修正
2/5 甩枪——修后 advice 必须肯定吞吐、给节奏/节拍配速方向，settle 类处方
绝迹；精瞄图语义（1w6ts extra small 类）下既有判读照常可用（条件化分支，
不是全局封禁）。

描述符用 sce_reading.build_scenario_reading_descriptor 从合成 .sce 构建，
保证验收走的是真实描述符形状而非手拼 dict。
"""
from __future__ import annotations

import pytest

from kovaak_tracker.advice import advise
from kovaak_tracker.coach import mapping_rules
from kovaak_tracker.coach.report import build_report
from kovaak_tracker.advice_tracking import build_tracking_candidate_advice
from kovaak_tracker.sce_reading import (
    READING_SCOPE_GENERIC,
    READING_SCOPE_PRECISION_TERMINAL,
    READING_SCOPE_SPEED_THROUGHPUT,
    build_scenario_reading_descriptor,
    reading_scope,
)

# 审计第五节验收特征：高命中 + 快节奏 + 收尾反向修正形态。
_TILE_FRENZY_SUMMARY = {
    "reverse_ratio": {"med": 0.40},   # 2/5 甩枪形态 → 减速段反向加速帧占比
    "decel_frac": {"med": 0.45},      # 速度图减速段短
}

_SETTLE_WORDS = ("停稳", "停住", "刹住", "落定", "确认再点", "settled")


def _tile_frenzy_sce() -> bytes:
    """合成 Tile Frenzy 形状 .sce：r100 方块墙靶、一发杀、击杀×准确率计分、
    半自动 0.1s、即时重生（指纹库 workshop/1352676992 的参数骨架）。"""
    return (
        "Name=Tile Frenzy\n"
        "PlayerCharacters=Player\n"
        "BotCharacters=tile.bot\n"
        "Timelimit=30.0\n"
        "AddedBots=tile.bot\n"
        "InvincibleBots=false\n"
        "Timescale=1.0\n"
        "TimeRefilledByKill=0.0\n"
        "ScoreToWin=1000.0\n"
        "ScorePerDamage=0.0\n"
        "ScorePerKill=1.0\n"
        "ScorePerTime=0.0\n"
        "ScoreLossPerDamageTaken=0.0\n"
        "ScoreLossPerMiss=0.0\n"
        "ScoreMultAccuracy=true\n"
        "\n"
        "[Bot Profile]\n"
        "Name=tile\n"
        "DodgeProfileNames=still\n"
        "CharacterProfile=tilechar\n"
        "\n"
        "[Dodge Profile]\n"
        "Name=still\n"
        "ToggleLeftRight=true\n"
        "MinLRTimeChange=0.2\n"
        "MaxLRTimeChange=0.5\n"
        "\n"
        "[Character Profile]\n"
        "Name=tilechar\n"
        "MaxHealth=1.0\n"
        "MaxSpeed=0.0\n"
        "MovementType=Base\n"
        "MainBBRadius=100.0\n"
        "MainBBType=Cuboid\n"
        "MinRespawnDelay=0.001\n"
        "\n"
        "[Character Profile]\n"
        "Name=Player\n"
        "MaxHealth=100.0\n"
        "WeaponProfileNames=pistol\n"
        "\n"
        "[Weapon Profile]\n"
        "Name=pistol\n"
        "Category=SemiAuto\n"
        "TimeBetweenShots=0.1\n"
        "MagazineMax=0.0\n"
        "DamagePerShot=25.0\n"
    ).encode("utf-8")


def _extra_small_sce() -> bytes:
    """合成 1w6ts extra small 形状 .sce：r8 微靶（精瞄域）。"""
    return (
        "Name=1wall 6targets extra small\n"
        "PlayerCharacters=Player\n"
        "BotCharacters=xs.bot\n"
        "Timelimit=60.0\n"
        "AddedBots=xs.bot\n"
        "InvincibleBots=false\n"
        "Timescale=1.0\n"
        "TimeRefilledByKill=0.0\n"
        "ScoreToWin=1000.0\n"
        "ScorePerDamage=0.0\n"
        "ScorePerKill=10.0\n"
        "ScorePerTime=0.0\n"
        "ScoreLossPerDamageTaken=0.0\n"
        "ScoreLossPerMiss=0.0\n"
        "ScoreMultAccuracy=true\n"
        "\n"
        "[Bot Profile]\n"
        "Name=xs\n"
        "DodgeProfileNames=still\n"
        "CharacterProfile=xschar\n"
        "\n"
        "[Dodge Profile]\n"
        "Name=still\n"
        "ToggleLeftRight=true\n"
        "MinLRTimeChange=0.2\n"
        "MaxLRTimeChange=0.5\n"
        "\n"
        "[Character Profile]\n"
        "Name=xschar\n"
        "MaxHealth=1.0\n"
        "MaxSpeed=0.0\n"
        "MovementType=Base\n"
        "MainBBRadius=8.0\n"
        "\n"
        "[Character Profile]\n"
        "Name=Player\n"
        "MaxHealth=100.0\n"
        "WeaponProfileNames=pistol\n"
        "\n"
        "[Weapon Profile]\n"
        "Name=pistol\n"
        "Category=SemiAuto\n"
        "TimeBetweenShots=0.01\n"
        "MagazineMax=0.0\n"
        "DamagePerShot=25.0\n"
    ).encode("utf-8")


@pytest.fixture(scope="module")
def tile_frenzy_reading() -> dict:
    descriptor = build_scenario_reading_descriptor(
        _tile_frenzy_sce(), display_name="Tile Frenzy",
    )
    assert descriptor["availability"] == "available"
    return descriptor


@pytest.fixture(scope="module")
def extra_small_reading() -> dict:
    descriptor = build_scenario_reading_descriptor(
        _extra_small_sce(), display_name="1wall 6targets extra small",
    )
    assert descriptor["availability"] == "available"
    return descriptor


def test_tile_frenzy_descriptor_is_speed_throughput(tile_frenzy_reading):
    """速度吞吐语义：静态点击 + 巨型靶（r100）→ speed_throughput。"""
    assert tile_frenzy_reading["training"]["semantics"] == "static_clicking"
    assert reading_scope(tile_frenzy_reading) == READING_SCOPE_SPEED_THROUGHPUT


def test_extra_small_descriptor_is_precision(extra_small_reading):
    """精瞄语义：静态点击 + 微型靶（r8）→ precision_terminal（既有判读）。"""
    assert "micro_adjust" in extra_small_reading["training"]["domains"]
    assert reading_scope(extra_small_reading) == READING_SCOPE_PRECISION_TERMINAL


def test_tile_frenzy_acceptance_speed_branch(tile_frenzy_reading):
    """验收主用例：速度吞吐语义下——判读改写为节奏代价、处方=节拍配速 +
    果断提速，settle 类处方绝迹。"""
    findings = advise(
        _TILE_FRENZY_SUMMARY, None, None, "zh-CN",
        scenario_reading=tile_frenzy_reading,
    )
    reverse = [f for f in findings if f.signal == "reverse_ratio high"]
    assert len(reverse) == 1
    finding = reverse[0]
    # 判读方向：节奏代价框架，不是欠控病灶
    assert "节奏代价" in finding.diagnosis
    assert "欠控病灶" in finding.diagnosis
    assert finding.plain_language_meaning == (
        "速度吞吐图上收尾的小幅反向修正，多属于快中带控的节奏代价"
    )
    # 处方方向：节拍配速（metronome）+ 果断提速
    domains = [p.scenario for p in finding.prescriptions]
    assert domains == ["pressure_pacing", "static_positioning"]
    assert "BPM" in finding.prescriptions[0].reason
    # 红线：settle 类话术绝迹
    for prescription in finding.prescriptions:
        for word in _SETTLE_WORDS:
            assert word not in prescription.reason
            assert word not in prescription.cue


def test_tile_frenzy_small_corrections_do_not_fire(tile_frenzy_reading):
    """速度吞吐档阈值分支：0.20 < reverse_ratio ≤ 0.35 的小幅修正=快中带控，
    不再触发 reverse_ratio finding。"""
    findings = advise(
        {"reverse_ratio": {"med": 0.25}}, None, None, "zh-CN",
        scenario_reading=tile_frenzy_reading,
    )
    assert all(f.signal != "reverse_ratio high" for f in findings)


def test_precision_semantics_keep_existing_judgment(extra_small_reading):
    """反向判据（审计第五节第5条）：精瞄语义下既有判读照常可用——
    同样的修正形态仍触发 reverse_ratio，且保留收尾修正训练方向
    （条件化分支，不是全局封禁）。"""
    findings = advise(
        _TILE_FRENZY_SUMMARY, None, None, "zh-CN",
        scenario_reading=extra_small_reading,
    )
    reverse = [f for f in findings if f.signal == "reverse_ratio high"]
    assert len(reverse) == 1
    finding = reverse[0]
    assert finding.diagnosis.startswith("减速段有 40% 的帧在反向加速")
    assert any("把修正并入减速过程" in p.reason for p in finding.prescriptions)


def test_generic_scope_without_descriptor_unchanged():
    """未接读图的旧数据/描述符缺失 → 既有判读逐字节不变。"""
    findings = advise(_TILE_FRENZY_SUMMARY, None, None, "zh-CN")
    assert reading_scope(None) == READING_SCOPE_GENERIC
    assert len(findings) == 1
    assert findings[0].plain_language_meaning == "移动收尾时出现了较多反向修正"


def test_unavailable_descriptor_unchanged():
    findings = advise(
        _TILE_FRENZY_SUMMARY, None, None, "zh-CN",
        scenario_reading={"availability": "unavailable", "reason": "sce_not_found"},
    )
    assert len(findings) == 1
    assert findings[0].diagnosis.startswith("减速段有 40% 的帧在反向加速")


def test_engine_and_builtin_agree_under_speed_scope(tile_frenzy_reading):
    """映射引擎与内置回退在速度档下输出 deep-equal（同一 apply_reading_scope）。"""
    engine = mapping_rules.dispatch_static(
        _TILE_FRENZY_SUMMARY, scenario_reading=tile_frenzy_reading,
    )
    builtin = advise(
        _TILE_FRENZY_SUMMARY, None, None, "zh-CN",
        scenario_reading=tile_frenzy_reading,
    )
    assert engine == builtin


def test_apply_reading_scope_is_idempotent(tile_frenzy_reading):
    once = advise(
        _TILE_FRENZY_SUMMARY, None, None, "zh-CN",
        scenario_reading=tile_frenzy_reading,
    )
    from kovaak_tracker.advice import apply_reading_scope

    twice = apply_reading_scope(once, _TILE_FRENZY_SUMMARY, tile_frenzy_reading)
    assert [f.diagnosis for f in once] == [f.diagnosis for f in twice]
    assert [
        [(p.scenario, p.reason) for p in f.prescriptions] for f in once
    ] == [[(p.scenario, p.reason) for p in f.prescriptions] for f in twice]


def test_report_speed_branch_carries_pacing_prescriptions(tile_frenzy_reading):
    """report 消费端：速度档下 issue 处方经锚点扩展后仍无 settle 话术，
    且节拍配速处方在场。"""
    report = build_report(_TILE_FRENZY_SUMMARY, None, {
        "summary_type": "flicking",
        "scenario_reading": tile_frenzy_reading,
    })
    reverse_issues = [
        issue for issue in report.diagnosis.issues
        if issue.signal == "reverse_ratio high"
    ]
    assert len(reverse_issues) == 1
    joined = " ".join(
        f"{p.scenario} {p.reason} {p.cue or ''}"
        for p in reverse_issues[0].prescriptions
    )
    for word in _SETTLE_WORDS:
        assert word not in joined
    assert any("节拍配速" in p.reason for p in reverse_issues[0].prescriptions)
    # 锚点扩展：能力域 → 场景名 + 状态标注
    pacing = next(
        p for p in reverse_issues[0].prescriptions if "节拍配速" in p.reason
    )
    assert pacing.scenario == "Tile Frenzy"
    assert "speed_throughput" not in pacing.reason  # 内部档名不外泄
    assert "标准答案" in pacing.reason
    assert pacing.source_level == "coach_first_party"


def test_tracking_candidate_guard_under_speed_scope():
    """advice_tracking 同原则分支：速度吞吐档下"反向修正负担"候选不触发。"""
    analysis = {
        "metrics": {
            "continuous_tracking.correction_direction_reversal_count": {"value": 7.0},
            "continuous_tracking.target_relative_error_px": {"value": 8.0},
            "continuous_tracking.time_in_radius_ratio": {"value": 0.90},
        },
        "comparison": {
            "comparable": True,
            "baseline_metrics": {
                "continuous_tracking.correction_direction_reversal_count": 3.0,
                "continuous_tracking.target_relative_error_px": 8.0,
                "continuous_tracking.time_in_radius_ratio": 0.90,
            },
        },
        "processed_rows": [],
        "limitations": [],
    }
    speed_reading = {
        "availability": "available",
        "training": {"semantics": "static_clicking"},
        "parameter_facts": {"targets": [{"character": {"main_bb_radius": 100.0}}]},
    }
    generic = build_tracking_candidate_advice(analysis)
    assert any(c["signal"] == "correction burden high" for c in generic)
    guarded = build_tracking_candidate_advice(analysis, scenario_reading=speed_reading)
    assert all(c["signal"] != "correction burden high" for c in guarded)
