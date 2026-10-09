# -*- coding: utf-8 -*-
"""merge_channels 拒绝 cause 码 + 局末存活过滤单测（纯逻辑）。

[fix 2026-10-09] 拒绝路径机读化：_reject_causes 的码映射穷举；
kill_click_check 的 death_event=False（局末存活）过滤与旧 schema（无键）
透传行为。
"""
import json
import os

import pytest

from telemetry_capture import merge_channels as mc


def _check(n, median):
    return {"n": n, "median_deg": median}


def _aim(n, median, share):
    return {"n": n, "median_deg": median, "share_lt_10deg": share}


def test_reject_causes_click_geom_starved():
    # n 不足 + 中位爆表 + 语义低 → 三码齐挂；xcorr 偏差进 detail（诊断留档）
    causes, detail = mc._reject_causes(
        _check(3, 12.0), _aim(0, None, None), False,
        {"click_to_death_latency_ms": {"n": 2}}, 1.125,
        round_verdicts=None, verdict_reuse=None, game_fp="fp1")
    assert causes == ["click_geom_n_below_min", "click_geom_median_above_max",
                      "click_semantics_low", "aim_n_below_min",
                      "aim_median_above_band", "aim_share_below_min",
                      "no_prior_verdict_reuse"]
    assert detail["check_n"] == 3 and detail["check_median_deg"] == 12.0
    assert detail["xcorr_dev_s"] == 1.125 and detail["game_fingerprint"] == "fp1"


def test_reject_causes_aim_band_only():
    # click 几何健康、aim 中位落在降级带上界之外（>7°）→ 仅 aim 侧码
    causes, _ = mc._reject_causes(
        _check(9, 0.4), _aim(12, mc.AIM_MEDIAN_DEGRADED_MAX_DEG + 0.5, 0.8),
        True, {}, 0.05, round_verdicts=None, verdict_reuse=None, game_fp=None)
    assert causes == ["aim_median_above_band", "no_prior_verdict_reuse"]


def test_reject_causes_share_below_min():
    causes, detail = mc._reject_causes(
        _check(9, 0.4), _aim(12, 3.0, 0.4), True, {}, 0.0,
        round_verdicts=None, verdict_reuse={}, game_fp=None)
    assert causes == ["aim_share_below_min"]
    assert detail["aim_share_lt_10deg"] == 0.4


def test_reject_causes_per_round_all_failed():
    causes, _ = mc._reject_causes(
        _check(3, 9.0), _aim(2, 20.0, 0.1), True, {}, 0.0,
        round_verdicts={"1": False, "2": False}, verdict_reuse={},
        game_fp=None)
    assert "per_round_all_failed" in causes


def test_reject_causes_none_check_kept_out():
    # camera 零帧等无回执路径不产生几何码（该路径直接挂 camera_zero_frames）
    causes, _ = mc._reject_causes(
        None, None, False, None, 0.0, round_verdicts=None,
        verdict_reuse=None, game_fp=None)
    assert causes == ["click_semantics_low", "no_prior_verdict_reuse"]


# ---------------- §3.3 局末存活过滤（kill_click_check） ----------------

class _TmpChdir:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.old = os.getcwd()
        os.chdir(self.path)

    def __exit__(self, *exc):
        os.chdir(self.old)


def _round_fixture(tmp_path, lives):
    """最小轮目录：rounds_index + 轮文件（单目标、位置恒定）+ 相机/输入小件。
    lives: 传给 rounds_index targets[0].lives 的原样条目。"""
    (tmp_path / "rounds_index.json").write_text(json.dumps({
        "sources": [{"source": "synth.jsonl", "outdir": ".",
                     "t0_epoch": 1790000000.0,
                     "rounds": [{"round": 1, "file": "round_01.jsonl",
                                 "t_start": 0.0, "t_end": 5.0,
                                 "targets": [{"addr": "0x7ff600000001",
                                              "lives": lives}]}]}]}),
        encoding="utf-8")
    pts = []
    t = 0.0
    while t <= 5.0 + 1e-9:
        pts.append(json.dumps({"ev": "frame", "t": round(t, 3),
                               "targets": [[0x7FF600000001,
                                            1000.0 + 10.0 * t, 500.0, 100.0]]}))
        t = round(t + 0.02, 3)
    (tmp_path / "round_01.jsonl").write_text("\n".join(pts), encoding="utf-8")
    return tmp_path


def _cam_inp():
    cam = {"frames": [(0.02 * k, [940.0 + 10.0 * 0.02 * k, 440.0, 60.0],
                       (0.0, 0.0, 0.0), 90.0) for k in range(251)],
           "epoch": 1790000000.0, "n_null": 0, "n_extra_map": {},
           "dt": 0.02, "span_s": 5.0}
    inp = {"delta": 0.0, "drift_s": 0.0, "n_events": 0, "n_clicks_raw": 0,
           "n_clicks_clustered": 0, "span_perf": (0.0, 5.0),
           "clicks_perf": [2.0, 5.0]}
    return cam, inp


def _run_kill_click(tmp_path, lives):
    cam, inp = _cam_inp()
    rounds = [{"round": 1, "file": "round_01.jsonl", "t_start": 0.0,
               "t_end": 5.0,
               "targets": [{"addr": 0x7FF600000001, "lives": lives}]}]
    cam_ts = [cam["epoch"] + t for (t, _p, _r, _f) in cam["frames"]]
    return mc.kill_click_check(
        str(tmp_path), rounds, cam, cam_ts, 1790000000.0,
        [c + 1790000000.0 for c in inp["clicks_perf"]])


def test_kill_click_skips_survival_tail_life(tmp_path):
    # 局末存活 life（death_event=False，t_end=5.0=窗尾）：不得与窗尾点击假配对
    lives = [{"t_start": 0.0, "t_end": 2.0, "death_event": True},
             {"t_start": 2.02, "t_end": 5.0, "death_event": False}]
    result = _run_kill_click(_round_fixture(tmp_path, lives), lives)
    assert result["n"] == 1          # 只有 @2.0 的真死亡参与配对
    assert result["median_deg"] is not None


def test_kill_click_legacy_schema_keeps_all_lives(tmp_path):
    # 旧 index（lives 无 death_event 键）：行为不变，全部计入
    lives = [{"t_start": 0.0, "t_end": 2.0},
             {"t_start": 2.02, "t_end": 5.0}]
    result = _run_kill_click(_round_fixture(tmp_path, lives), lives)
    assert result["n"] >= 2
