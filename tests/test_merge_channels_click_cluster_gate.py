# -*- coding: utf-8 -*-
"""merge_channels click_geom 下分位簇门单测（纯逻辑）。

[fix 2026-10-19] 高射速+击杀即刷新场景（1w1ts Advanced，e0d6721b 1019 报障案）
中位被 miss 点击/目标刷新污染而锚健康，click_geom 级加簇门备选：
share_lt_3deg ≥ CLICK_SHARE3_MIN ⇒ grade=click_geom_cluster（非静默）。
数字全部取自该案三局真实回执与既有负对照（2156 R3 错锚、垃圾死亡流）。
"""
from telemetry_capture import merge_channels as mc


def _check(n, median, share3=None, share1=None):
    out = {"n": n, "median_deg": median}
    if share3 is not None:
        out["share_lt_3deg"] = share3
    if share1 is not None:
        out["share_lt_1deg"] = share1
    return out


# ---------------- 真实案：e0d6721b 1019 三局（1w1ts Advanced） ----------------

def test_cluster_gate_passes_e0d6721b_three_cuts():
    # cut-run993/994/995：中位 17.6/16.7/18.0°，<3° 占 0.32/0.31/0.21
    # （xcorr 偏差 1ms、score 72/72、click→death 中位 3.7ms = 锚健康）
    for share3 in (0.32, 0.31, 0.21):
        assert mc._click_geom_grade(
            _check(72, 17.6, share3=share3), True) == "click_geom_cluster"


def test_cluster_gate_minimum_sample_just_above_threshold():
    # 0.21 是案内最小实证样本：0.15 下限留 0.06 裕量；恰在限上也过
    assert mc._click_geom_grade(
        _check(72, 18.0, share3=mc.CLICK_SHARE3_MIN), True) == "click_geom_cluster"


# ---------------- 负对照不回退 ----------------

def test_cluster_gate_rejects_wrong_anchor_negative_control():
    # 2156 R3 错锚 ±1.125s：中位 34~44°、<10° 占 3~10% ⇒ <3°≈0
    assert mc._click_geom_grade(_check(72, 34.3, share3=0.02), True) is None


def test_cluster_gate_rejects_garbage_death_stream():
    # 垃圾死亡流 37°+
    assert mc._click_geom_grade(_check(72, 37.0, share3=0.0), True) is None


def test_cluster_gate_rejects_virtual_screen_domain_offset():
    # 虚拟屏域错位：角误差近均匀，3°/180°≈2%
    assert mc._click_geom_grade(_check(72, 88.0, share3=0.02), True) is None


# ---------------- 原中位门路径零漂移 ----------------

def test_median_gate_unchanged():
    # 0901 验证局形态：中位 0.233°/<1°占98% ⇒ 原 grade click_geom
    assert mc._click_geom_grade(
        _check(118, 0.233, share3=0.98), True) == "click_geom"


def test_median_gate_still_rejects_when_share_missing():
    # 旧 schema check 无 share 键：只走中位门，行为与修复前一致
    assert mc._click_geom_grade(_check(72, 17.6), True) is None
    assert mc._click_geom_grade(_check(72, 0.4), True) == "click_geom"


def test_gates_still_require_semantics_and_n_and_median():
    assert mc._click_geom_grade(_check(72, 0.4, share3=0.9), False) is None
    assert mc._click_geom_grade(
        _check(mc.CLICK_CHECK_MIN_N - 1, 0.4, share3=0.9), True) is None
    assert mc._click_geom_grade(_check(72, None, share3=0.9), True) is None
    assert mc._click_geom_grade(None, True) is None


# ---------------- 拒绝 detail 留档 ----------------

def test_reject_causes_detail_records_share3():
    # 中位超限且簇占比不足被拒时，detail 带 check_share_lt_3deg 供分诊
    causes, detail = mc._reject_causes(
        _check(72, 34.3, share3=0.02),
        {"n": 73, "median_deg": 34.0, "share_lt_10deg": 0.1},
        True, {}, 1.125, round_verdicts=None, verdict_reuse=None, game_fp=None)
    assert "click_geom_median_above_max" in causes
    assert detail["check_share_lt_3deg"] == 0.02
    assert detail["check_median_deg"] == 34.3
