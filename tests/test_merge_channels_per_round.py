# -*- coding: utf-8 -*-
"""merge_channels 逐轮部分验收单测（纯逻辑）。

[fix 2026-10-04] session 级双挂时逐轮独立 fail-closed：过验轮写旁车、脏轮
alignment_rejected。这里覆盖轮级判据函数 _per_round_aim_verdicts 的门槛行为。
"""
import pytest

from telemetry_capture import merge_channels as mc


def pr(round_no, n, median, share10):
    return {
        "round": round_no,
        "n": n,
        "median_deg": median,
        "share_lt_5deg": max(0.0, share10 - 0.2),
        "share_lt_10deg": share10,
    }


def test_per_round_accepts_clean_round():
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(1, 12, 1.2, 0.8)]})
    assert verdicts == {"1": True}


def test_per_round_rejects_dirty_round():
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(2, 39, 26.6, 0.256)]})
    assert verdicts == {"2": False}


def test_per_round_partial_mix_picks_clean_only():
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(1, 12, 1.2, 0.8), pr(2, 39, 26.6, 0.256)]})
    assert verdicts == {"1": True, "2": False}


def test_per_round_n_gate_rejects_tiny_sample():
    # n < PER_ROUND_MIN_N：单样本巧合不可作验收证据。
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(1, mc.PER_ROUND_MIN_N - 1, 0.2, 1.0)]})
    assert verdicts == {"1": False}


def test_per_round_share_gate_rejects_split_distribution():
    # 中位达标但 <10° 占比不足（一半样本全崩）⇒ 拒。
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(1, 8, 3.0, 0.49)]})
    assert verdicts == {"1": False}


def test_per_round_median_gate_rejects_elevated_median():
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [pr(1, 3, mc.AIM_MEDIAN_MAX_DEG + 0.1, 0.667)]})
    assert verdicts == {"1": False}


def test_per_round_none_median_rejected():
    verdicts = mc._per_round_aim_verdicts(
        {"per_round": [{"round": 1, "n": 3, "median_deg": None,
                        "share_lt_5deg": None, "share_lt_10deg": None}]})
    assert verdicts == {"1": False}


def test_per_round_empty_input_yields_no_verdicts():
    # 无 per_round（旧回执形状）⇒ 空 dict ⇒ any()=False ⇒ 走整体拒绝。
    assert mc._per_round_aim_verdicts({}) == {}
    assert mc._per_round_aim_verdicts(None) == {}


def test_click_semantics_guard_threshold():
    # 场景守卫口径：死亡-点击配对率 <50% ⇒ click 语义不成立。
    assert (9 / 129 >= mc.CLICK_SEMANTICS_MIN_SHARE) is False   # tracking 局实测
    assert (21 / 42 >= mc.CLICK_SEMANTICS_MIN_SHARE) is True    # 点击局实测
