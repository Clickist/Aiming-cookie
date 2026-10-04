# -*- coding: utf-8 -*-
"""cleaner 窗口模式幽灵豁免验收单测。

[fix 2026-10-04] 按局增量切窗（epoch_window，窗口=[run_start-10s, run_end+5s]）下，
上一局仍在场/池化复用的慢速真目标会命中幽灵三联判据（首帧在场 ∧ 占比>95% ∧
速度<10 u/s）被整轨删除 → assign_rounds 拿到空目标 → 0 轮（用户报障根因，
"larger + slowed - challenge" 类场景实测 0 轮）。

本单测钉死：窗口模式下三联命中者追加真目标生命周期复核（D1a 多段/切段、
D1b 原点残留、D2 bbox 净跨度，任一命中即保留 fail-open）；全程模式行为不变
（同数据仍删除）；CDO 冻结体在窗口模式下仍正确删除。
"""
import json
import os

from telemetry_capture import cleaner

T0_EPOCH = 1760000000.0
DT = 0.02                      # 合成采样 50 Hz（load_frames 不假设固定 dt，取整避免浮点漂移）
ADDR = 0x7FF600000001
HEX = "0x7ff600000001"
WINDOW = (T0_EPOCH - 10.0, T0_EPOCH + 70.0)   # 覆盖全部合成帧的按局切窗


def make_cfg(epoch_window):
    """与 cleaner.main() 的 cfg 同构（不过 argparse，直接喂 clean_file）。"""
    return {"jump_dist": cleaner.JUMP_DIST, "jump_speed": cleaner.JUMP_SPEED,
            "bound": cleaner.BOUND, "origin_eps": cleaner.ORIGIN_EPS,
            "min_life": cleaner.MIN_LIFE, "min_move": cleaner.MIN_MOVE,
            "min_samples": cleaner.MIN_SAMPLES, "birth_gap": cleaner.BIRTH_GAP,
            "dead_gap": cleaner.DEAD_GAP, "phantom_ratio": cleaner.PHANTOM_RATIO,
            "phantom_speed": cleaner.PHANTOM_SPEED,
            "phantom_span_keep": cleaner.PHANTOM_SPAN_KEEP,
            "respawn2_speed": cleaner.RESPAWN2_SPEED, "respawn2_dist": cleaner.RESPAWN2_DIST,
            "lane_cos": cleaner.LANE_COS, "ambient_static_speed": cleaner.AMBIENT_STATIC_SPEED,
            "respawn2_static_dist": cleaner.RESPAWN2_STATIC_DIST,
            "epoch_window": epoch_window}


def write_jsonl(path, frames):
    """frames: [(t_rel, [[addr,x,y,z],...]), ...] → target_poll2 产物形 JSONL（含 clock_map 锚）。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "clock_map", "t": T0_EPOCH}) + "\n")
        for t, targets in frames:
            f.write(json.dumps({"ev": "frame", "t": t, "targets": targets}) + "\n")


def clean_tmp(tmp_path, frames, epoch_window=WINDOW, name="synth"):
    path = str(tmp_path / (name + ".jsonl"))
    write_jsonl(path, frames)
    outdir = str(tmp_path / "out")
    return cleaner.clean_file(path, outdir, make_cfg(epoch_window))


def pooled_slow_frames(duration=60.0, speed=6.0, jump_every=8.0):
    """池化慢速真目标：全程在场、6 u/s 滑行（<幽灵速度上限 10）、每 8s 换道重生跳
    （一帧 424 u / 21213 u/s → 主阈值切段）。
    旧行为（无复核）：幽灵三联判据整轨删除 → 0 轮，即报障场景的最小复现。"""
    frames = []
    x, y = 500.0, 500.0
    next_jump = jump_every
    for i in range(int(round(duration / DT)) + 1):
        frames.append((round(i * DT, 6), [[ADDR, x, y, 100.0]]))
        if (i + 1) * DT >= next_jump - 1e-9:
            x, y = x + 300.0, y - 300.0     # 换道重生：300,−300 → d≈424 u 一帧
            next_jump += jump_every
        else:
            x += speed * DT
    return frames


def test_window_keeps_slow_pooled_target_with_respawns(tmp_path):
    # 窗口模式：三联命中的池化慢目标被 D1a（多段）复核保留 → ≥1 轮且轮含该目标
    src = clean_tmp(tmp_path, pooled_slow_frames())
    assert src["n_rounds"] >= 1
    assert src["rounds"][0]["targets"][0]["addr"] == ADDR
    assert src["rounds"][0]["targets"][0]["phantom_ambiguous"] is True
    # 轮文件真实含该目标（首帧即轮起点）
    with open(os.path.join(str(tmp_path), "out", "synth", "round_01.jsonl"),
              encoding="utf-8") as f:
        first = json.loads(f.readline())
    assert any(e[0] == ADDR for e in first["targets"])
    # 幽灵闸审计：不进 phantom_tracks，进 kept_ambiguous（自描述诊断）
    assert src["discarded"]["phantom_tracks"] == {}
    gate = src["discarded"]["phantom_gate"]
    assert gate["mode"] == "window" and gate["rule_version"] == 2
    info = gate["kept_ambiguous"][HEX]
    assert "D1a" in info["reason"] and info["n_lives"] > 1


def test_window_still_deletes_frozen_cdo(tmp_path):
    # 坐标恒定真 CDO：复核三判据全不命中 → 仍整轨删除，kept 空，0 轮（轮不含它）
    frames = [(round(i * DT, 6), [[ADDR, 100.0, 200.0, 50.0]])
              for i in range(int(round(60.0 / DT)) + 1)]
    src = clean_tmp(tmp_path, frames, name="cdo")
    assert HEX in src["discarded"]["phantom_tracks"]
    assert src["discarded"]["phantom_tracks"][HEX]["speed_ups"] == 0.0
    assert src["discarded"]["phantom_gate"]["kept_ambiguous"] == {}
    assert src["n_rounds"] == 0


def test_window_keeps_slow_drifter_without_deaths(tmp_path):
    # D2 路径：4 u/s 单调漂移、零切段、单段——跨度 240u ≥ 50 判真目标保留
    frames = [(round(i * DT, 6), [[ADDR, 500.0 + 4.0 * i * DT, 500.0, 100.0]])
              for i in range(int(round(60.0 / DT)) + 1)]
    src = clean_tmp(tmp_path, frames, name="drift")
    info = src["discarded"]["phantom_gate"]["kept_ambiguous"][HEX]
    assert info["reason"] == "D2" and info["n_lives"] == 1 and info["bbox_span"] >= 50.0
    assert src["discarded"]["phantom_tracks"] == {}
    assert src["n_rounds"] == 1
    assert src["rounds"][0]["targets"][0]["phantom_ambiguous"] is True


def test_window_keeps_target_with_origin_residue(tmp_path):
    # D1b 路径：窗首死亡读回原点后池化重生。死亡前仅 2 帧有效点（切段后作噪声丢弃
    # → 保留侧单段、零切段，D1a 不触发），只走原点残留判据
    frames = [(0.0, [[ADDR, 500.0, 500.0, 100.0]]),
              (0.02, [[ADDR, 500.12, 500.0, 100.0]])]
    for k in range(5):      # 死亡残留：RootComponent 读回精确 (0,0,0)
        frames.append((round(0.04 + k * DT, 6), [[ADDR, 0.0, 0.0, 0.0]]))
    for i in range(7, int(round(60.0 / DT)) + 1):   # 0.14s 起重生
        t = round(i * DT, 6)
        # 重生后 4 u/s 漂移仅 5s（20u，跨度 <50 使 D2 不触发）后驻停至 60s，
        # 速度远 <10 仍命中幽灵三联 → 唯一命中 D1b
        x = 800.0 + 4.0 * min(t - 0.14, 5.0)
        frames.append((t, [[ADDR, x, 500.0, 100.0]]))
    src = clean_tmp(tmp_path, frames, name="residue")
    info = src["discarded"]["phantom_gate"]["kept_ambiguous"][HEX]
    assert info["reason"] == "D1b" and info["origin_points"] == 5 and info["n_lives"] == 1
    assert src["discarded"]["phantom_tracks"] == {}
    assert src["n_rounds"] == 1


def test_full_session_mode_unchanged(tmp_path):
    # 同用例 1 数据、全程模式（epoch_window=None）：复核分支不进入，仍整轨删除
    # → 0 轮（旧行为即报障根因，钉死回归面）
    src = clean_tmp(tmp_path, pooled_slow_frames(), epoch_window=None, name="full")
    assert HEX in src["discarded"]["phantom_tracks"]
    assert src["discarded"]["phantom_gate"]["mode"] == "full"
    assert src["discarded"]["phantom_gate"]["kept_ambiguous"] == {}
    assert src["n_rounds"] == 0


def test_windowed_normal_multitarget_regression(tmp_path):
    # 正常多目标局（快目标 300 u/s + 两波出生）：切轮/目标数不变、无幽灵误删、无 kept 条目
    a, b, c = 0x7FF60000000A, 0x7FF60000000B, 0x7FF60000000C
    spans = [(a, 1.0, 6.0, (500.0, 500.0, 100.0), (300.0, 0.0, 0.0)),
             (b, 1.5, 5.5, (1000.0, 2000.0, 100.0), (0.0, 300.0, 0.0)),
             (c, 20.0, 25.0, (2000.0, 500.0, 100.0), (300.0, 0.0, 0.0))]
    frames = []
    for i in range(int(round(30.0 / DT)) + 1):
        t = round(i * DT, 6)
        targets = [[ad, p[0] + v[0] * (t - b0), p[1] + v[1] * (t - b0), p[2] + v[2] * (t - b0)]
                   for ad, b0, d0, p, v in spans if b0 <= t <= d0]
        frames.append((t, targets))
    src = clean_tmp(tmp_path, frames, name="multi")
    assert src["n_rounds"] == 2
    assert src["rounds"][0]["n_targets"] == 2 and src["rounds"][1]["n_targets"] == 1
    assert src["discarded"]["phantom_tracks"] == {}
    assert src["discarded"]["phantom_gate"]["kept_ambiguous"] == {}
    for r in src["rounds"]:
        for tm in r["targets"]:
            assert "phantom_ambiguous" not in tm


def test_empty_frames_h1_fingerprint(tmp_path):
    # H1 指纹（poll 全程看不到目标）：frames_total>0 但 frames_with_targets==0、0 轮
    frames = [(round(i * DT, 6), []) for i in range(int(round(10.0 / DT)) + 1)]
    src = clean_tmp(tmp_path, frames, name="empty")
    assert src["frames_total"] == 501
    assert src["frames_with_targets"] == 0
    assert src["first_frame_target_count"] == 0
    assert src["n_rounds"] == 0
