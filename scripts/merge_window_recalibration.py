#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""merge_window_recalibration.py — merge 验收「窗口口径修正」重标定评估（只读）。

背景：death_aim_check 现役口径 =「死亡前 200ms 窗内 准星→目标最后已知位置 最小夹角」，
已证实带伪影：垂死目标位置停更（死亡动画期检测器持续输出冻结坐标）使样本角误差虚大
（同局同锚对照：点击口径 0.257° vs 死亡窗口径 21.3°）。

本脚本对本地真实语料的每种历史洗法逐 life 计算四种口径并对照：
  deathwin    现役口径（基线复现，应与 merge_manifest.aim_check 一致）
  deathwin_b  候选 b：deathwin + 剔除位置停更(staleness>T)样本（默认 T=0.3s，含敏感性扫描）
  click_a     候选 a：点击锚口径（kill_click_check 同取数：相机取点击时刻帧、
              位置取 t_end 前最后样本，配对窗 250ms）
  click_a2    候选 a 变体：同 a 但位置取样改为点击时刻前最后样本（消 t_end 死亡动画暴露）

另做错锚证伪：相机锚人为偏移 ±0.5s / ±1.125s（2156 实测场景动力学锁偏量级），
任一口径在错锚下放行 = 漏放；基线锚下健康洗被拒 = 误杀。

只读所有输入；输出 JSON 落 .zcode/merge-recal-1006/results.json。
用法: python scripts/merge_window_recalibration.py [--staleness 0.3]
"""
import argparse
import bisect
import json
import math
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from telemetry_capture.merge_channels import (  # noqa: E402
    load_camera, load_input_maps_clicks, CLICK2DEATH_MAX,
    AIM_CHECK_MIN_N, AIM_MEDIAN_MAX_DEG, AIM_MEDIAN_DEGRADED_MAX_DEG,
    AIM_SHARE10_MIN, PER_ROUND_MIN_N,
)

WIN_S = 0.2
SHIFTS = (0.0, 0.5, -0.5, 1.125, -1.125)

WASHES = [
    # (label, round_dir, camera, input)  round_dir = 含 round_*.jsonl 的目录
    ("54075_w1", "E:/ACData/external-capture/cleaned/incr/cut-run54075-1791088182490/target_poll_out_1004_122757",
     "E:/ACData/external-capture/sessions/session-261004-122545/camera_probe_out_1004_122755.jsonl",
     "E:/ACData/external-capture/sessions/session-261004-122545/input_log.jsonl"),
    ("54077_w1", "E:/ACData/external-capture/cleaned/incr/cut-run54077-1791102814828/target_poll_out_1004_154724",
     "E:/ACData/external-capture/sessions/session-261004-154715/camera_probe_out_1004_154721.jsonl",
     "E:/ACData/external-capture/sessions/session-261004-154715/input_log.jsonl"),
    ("54078_w1", "E:/ACData/external-capture/cleaned/incr/cut-run54078-1791128943621/target_poll_out_1004_234745",
     "E:/ACData/external-capture/sessions/session-261004-233656/camera_probe_out_1004_234741.jsonl",
     "E:/ACData/external-capture/sessions/session-261004-233656/input_log.jsonl"),
    ("152319_w54096", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54096-1791185181694/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54097", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54097-1791185286697/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54098", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54098-1791185370494/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54099", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54099-1791185444939/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54100", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54100-1791185538499/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54101", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54101-1791185634444/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
    ("152319_w54102", "C:/Users/袜子/Desktop/Aiming-cookie/.zcode/verify-152319/cut-run54102-1791185706895/target_poll_out_1005_152319",
     "E:/ACData/external-capture/sessions/session-261005-140049/camera_probe_out_1005_152317.jsonl",
     "E:/ACData/external-capture/sessions/session-261005-140049/input_log.jsonl"),
]


def load_rounds_and_files(round_dir):
    """rounds_index 条目 + 各轮 pos_by_addr（与 merge_channels 取数一致）。"""
    idx_path = None
    for cand in (os.path.join(round_dir, "rounds_index.json"),
                 os.path.join(os.path.dirname(round_dir), "rounds_index.json")):
        if os.path.isfile(cand):
            idx_path = cand
            break
    idx = json.load(open(idx_path, encoding="utf-8"))
    name = os.path.basename(os.path.normpath(round_dir))
    entry = None
    for src in idx.get("sources", []):
        if src.get("outdir") == name or str(src.get("source", "")).startswith(name + "."):
            entry = src
            break
    if entry is None:
        raise SystemExit("!! source 条目未匹配: %s" % round_dir)
    rounds = sorted(entry.get("rounds", []), key=lambda r: r["t_start"])
    per_round_pos = {}
    for r in rounds:
        rf = os.path.join(round_dir, r.get("file", "round_%02d.jsonl" % r["round"]))
        pos_by_addr = {}
        if os.path.isfile(rf):
            with open(rf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("ev") != "frame":
                        continue
                    t = float(rec["t"])
                    for e in rec.get("targets") or []:
                        pos_by_addr.setdefault(int(e[0]), []).append(
                            (t, float(e[1]), float(e[2]), float(e[3])))
        for v in pos_by_addr.values():
            v.sort()
        per_round_pos[r["round"]] = pos_by_addr
    return idx_path, entry, rounds, per_round_pos


def angle_deg(pos, rot, xyz):
    """merge_channels 同公式：世界系 准星→目标 夹角。"""
    dx, dy, dz = xyz[0] - pos[0], xyz[1] - pos[1], xyz[2] - pos[2]
    yaw_t = math.degrees(math.atan2(dy, dx))
    pitch_t = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    dyaw = ((yaw_t - rot[1] + 180.0) % 360.0) - 180.0
    return math.hypot(dyaw, pitch_t - rot[0])


def collect_lives(entry, rounds, per_round_pos, cam, inp, s):
    """逐 life 采样点：t_end / 采样停更 / 值冻结(窗内位移速度) / 位置样本 / 点击配对。

    伪影机制是「位置值冻结」：死亡动画期检测器每帧重复吐同一坐标，采样间隔照常，
    采样级 staleness 检测不到——必须量窗内位移速度（cleaner ambient_static_speed
    =50 cm/s 同源阈值）。"""
    click_epochs = sorted(t + inp["delta"] for t in inp["clicks_perf"])
    clicks_src = [e - s for e in click_epochs]
    lives = []
    for r in rounds:
        pos_by_addr = per_round_pos[r["round"]]
        for tgt in r.get("targets", []):
            pts = pos_by_addr.get(tgt.get("addr"))
            if not pts:
                continue
            pt_ts = [p[0] for p in pts]
            for lv in tgt.get("lives", []):
                t_end = lv["t_end"]
                k = bisect.bisect_right(pt_ts, t_end) - 1
                if k < 0:
                    continue
                staleness = t_end - pt_ts[k]
                # 值冻结：[t_end-0.3, t_end] 窗内位置最大位移 → 速度
                w_lo = bisect.bisect_left(pt_ts, t_end - 0.3)
                seg = pts[w_lo:k + 1]
                move = 0.0
                if len(seg) >= 2:
                    for a, b in zip(seg, seg[1:]):
                        move = max(move, math.dist(a[1:], b[1:]))
                    if len(seg) == 1:
                        move = 0.0
                win_span = (pt_ts[k] - pt_ts[w_lo]) if len(seg) >= 2 else 0.0
                speed = (move / win_span) if win_span > 0.05 else (0.0 if len(seg) >= 2 else None)
                j = bisect.bisect_right(clicks_src, t_end) - 1
                click_t = None
                if j >= 0 and t_end - clicks_src[j] <= CLICK2DEATH_MAX:
                    click_t = clicks_src[j]
                k2 = bisect.bisect_right(pt_ts, click_t) - 1 if click_t is not None else None
                lives.append({
                    "round": r["round"], "addr": tgt.get("addr"), "t_end": t_end,
                    "pos_tend": pts[k][1:], "staleness": staleness,
                    "win_move_cm": move, "win_speed_cms": speed,
                    "click_t": click_t,
                    "pos_at_click": pts[k2][1:] if k2 is not None and k2 >= 0 else None,
                })
    return lives


def min_angle_deathwin(cam, cam_ts_shift, life):
    """现役口径：死亡前 200ms 窗最小夹角（相机时间轴按 shift 偏移）。"""
    t_end = life["t_end"]
    lo = bisect.bisect_left(cam_ts_shift, t_end - WIN_S)
    hi = bisect.bisect_right(cam_ts_shift, t_end)
    best = None
    for c in range(lo, hi):
        e = angle_deg(cam["frames"][c][1], cam["frames"][c][2], life["pos_tend"])
        if best is None or e < best:
            best = e
    return best


def angle_at_click(cam, cam_ts_shift, life, use_pos_at_click):
    """候选 a/a2：点击锚。相机帧取点击时刻最近帧；位置按变体选样。"""
    ct = life["click_t"]
    if ct is None:
        return None
    pos = life["pos_at_click"] if use_pos_at_click else life["pos_tend"]
    if pos is None:
        return None
    c = bisect.bisect_left(cam_ts_shift, ct)
    c = min(max(c, 0), len(cam_ts_shift) - 1)
    return angle_deg(cam["frames"][c][1], cam["frames"][c][2], pos)


def agg(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return {"n": 0, "median_deg": None, "p25_deg": None, "p75_deg": None,
                "share_lt_5deg": None, "share_lt_10deg": None}
    n = len(vals)
    return {
        "n": n,
        "median_deg": round(statistics.median(vals), 3),
        "p25_deg": round(vals[n // 4], 3),
        "p75_deg": round(vals[3 * n // 4], 3),
        "share_lt_5deg": round(sum(1 for e in vals if e < 5.0) / n, 3),
        "share_lt_10deg": round(sum(1 for e in vals if e < 10.0) / n, 3),
    }


def grade_of(a):
    """现役分级门（n/share/median 5/7）应用到任一口径的聚合结果。"""
    if a["n"] < AIM_CHECK_MIN_N or a["median_deg"] is None or a["share_lt_10deg"] < AIM_SHARE10_MIN:
        return "fail"
    if a["median_deg"] <= AIM_MEDIAN_MAX_DEG:
        return "pass"
    if a["median_deg"] <= AIM_MEDIAN_DEGRADED_MAX_DEG:
        return "degraded"
    return "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staleness", type=float, default=0.3)
    ap.add_argument("--sweep", action="store_true", help="停更阈值敏感性扫描")
    args = ap.parse_args()
    T = args.staleness

    out = {"staleness_T": T, "washes": {}}
    for label, round_dir, cam_path, inp_path in WASHES:
        if not os.path.isdir(round_dir):
            print("[skip] %s 轮目录缺失" % label)
            continue
        _ip, entry, rounds, per_round_pos = load_rounds_and_files(round_dir)
        s = entry.get("t0_epoch")
        if s is None:
            print("[skip] %s 无 t0_epoch（旧 index）" % label)
            continue
        cam = load_camera(cam_path)
        inp = load_input_maps_clicks(inp_path)
        lives = collect_lives(entry, rounds, per_round_pos, cam, inp, s)
        cam_ts = [cam["epoch"] + t - s for (t, _p, _r, _f) in cam["frames"]]

        rec = {"source": entry.get("source"), "t0_epoch": s,
               "n_rounds": len(rounds), "n_lives": len(lives), "shifts": {}}
        stal_all = [l["staleness"] for l in lives]
        spd = [l["win_speed_cms"] for l in lives if l["win_speed_cms"] is not None]
        frozen_thr = 50.0  # cleaner ambient_static_speed 同源
        rec["staleness_s"] = {
            "p50": round(statistics.median(stal_all), 3) if stal_all else None,
            "max": round(max(stal_all), 3) if stal_all else None,
            "share_gt_0.3": round(sum(1 for x in stal_all if x > 0.3) / len(stal_all), 3),
        } if stal_all else None
        rec["frozen"] = {
            "thr_cms": frozen_thr,
            "speed_p50": round(statistics.median(spd), 1) if spd else None,
            "share_frozen": round(sum(1 for x in spd if x < frozen_thr) / len(spd), 3) if spd else None,
        } if spd else None

        for shift in SHIFTS:
            cam_ts_s = [t + shift for t in cam_ts]
            dw, dwe, dwb, ca, ca2 = [], [], [], [], []
            for l in lives:
                a_dw = min_angle_deathwin(cam, cam_ts_s, l)
                dw.append(a_dw)
                ca.append(angle_at_click(cam, cam_ts_s, l, False))
                ca2.append(angle_at_click(cam, cam_ts_s, l, True))
                if l["staleness"] <= T:
                    dwe.append(a_dw)
                spd = l["win_speed_cms"]
                if spd is None or spd >= frozen_thr:
                    dwb.append(a_dw)
            sd = {"deathwin": agg(dw), "deathwin_stalexcl": agg(dwe),
                  "deathwin_b_frozen": agg(dwb),
                  "click_a": agg(ca), "click_a2": agg(ca2)}
            sd["verdicts"] = {k: grade_of(v) for k, v in sd.items()
                              if isinstance(v, dict) and "n" in v}
            sd["n_excluded_stale"] = sum(1 for l in lives if l["staleness"] > T)
            sd["n_excluded_frozen"] = sum(
                1 for l in lives
                if l["win_speed_cms"] is not None and l["win_speed_cms"] < frozen_thr)
            rec["shifts"][str(shift)] = sd

        if args.sweep:
            rec["sweep"] = {}
            for t_thr in (0.1, 0.2, 0.3, 0.5, 1.0, 2.0):
                keep = [min_angle_deathwin(cam, cam_ts, l)
                        for l in lives if l["staleness"] <= t_thr]
                rec["sweep"][str(t_thr)] = {"n": len(keep), **agg(keep)}
            rec["frozen_sweep"] = {}
            for f_thr in (10.0, 20.0, 50.0, 100.0, 200.0):
                keep = [min_angle_deathwin(cam, cam_ts, l) for l in lives
                        if l["win_speed_cms"] is None or l["win_speed_cms"] >= f_thr]
                rec["frozen_sweep"][str(f_thr)] = {"n": len(keep), **agg(keep)}

        out["washes"][label] = rec
        v0 = rec["shifts"]["0.0"]
        print("[%s] lives=%d frozen(<%.0fcm/s)=%.0f%% | base: deathwin n=%d med=%s %s"
              " | b_frozen n=%d med=%s %s | click_a n=%d med=%s %s"
              % (label, rec["n_lives"], frozen_thr,
                 100 * (rec["frozen"]["share_frozen"] if rec.get("frozen") else 0),
                 v0["deathwin"]["n"], v0["deathwin"]["median_deg"], v0["verdicts"]["deathwin"],
                 v0["deathwin_b_frozen"]["n"], v0["deathwin_b_frozen"]["median_deg"],
                 v0["verdicts"]["deathwin_b_frozen"],
                 v0["click_a"]["n"], v0["click_a"]["median_deg"], v0["verdicts"]["click_a"]))
        for shift in SHIFTS[1:]:
            vs = rec["shifts"][str(shift)]["verdicts"]
            md = rec["shifts"][str(shift)]["deathwin"]["median_deg"]
            mb = rec["shifts"][str(shift)]["deathwin_b_frozen"]["median_deg"]
            print("    shift %+.3fs: deathwin med=%s %s | b_frozen med=%s %s | click_a %s"
                  % (shift, md, vs["deathwin"], mb, vs["deathwin_b_frozen"], vs["click_a"]))

    od = os.path.join(REPO, ".zcode", "merge-recal-1006")
    os.makedirs(od, exist_ok=True)
    op = os.path.join(od, "results.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[done] ->", op)


if __name__ == "__main__":
    main()
