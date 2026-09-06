#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""merge_channels.py — 把同会话的相机(通道B)/OS输入(通道C)并进 cleaned 轮次目录（旁车）。

产出（写在 --round-dir 内，全部原子写 tmp+os.replace；文件名刻意不以 round_ 开头——
Aiming-cookie 导入器按 round_*.jsonl glob 轮文件，旁车不得匹配该模式）:
  views_NN.jsonl       每行 {"t","pos","rot","fov"}，t 域 = 轮文件 t 域（源文件相对秒）
  inputs_NN.jsonl      每行 {"t","dx","dy","btn"}
  merge_manifest.json  对齐回执 + 每轮覆盖统计（--check 时含击杀几何回执）

对齐算法（FORMAT.md §5.3 两步法，全部已有实证）:
  1) 粗锚: 轮目录名 target_poll_out_<MMDD_HHMMSS> 墙钟（±1s，--year 补年）；
     若 rounds_index 的 source 条目带 t0_epoch（cleaner 配套补丁透传），直接用精确锚
  2) 细化: 池化 click↔death 互相关（±3s, 2ms 步；score = 死亡时刻前 ≤250ms 存在
     L_down 点击的死亡数），峰值 ≥5×随机基线 p95 才接受（FORMAT §5.3 判据）；
     相机/输入各自有文件内 clock_map epoch 锚，换算源 t 域: t_src = epoch - s
  3) fail-closed: 无 t0_epoch 且互相关不达峰 → 拒绝写任何旁车（退出码 2），
     宁可无旁车不可错对齐

用法:
  python merge_channels.py --round-dir cleaned/final_0831/target_poll_out_0831_003140 \
      --camera camera_probe_out_0831_003138.jsonl --input input_log_final_0831.jsonl \
      [--year 2026] [--check]

只读 相机/输入/轮次源文件；副作用仅限 round-dir 内旁车件。输入日志可达数十 MB：
两遍流式（一遍取 clock_map+点击，一遍按窗分桶），不整载入内存。
"""
import argparse
import bisect
import json
import os
import random
import re
import statistics
import sys
import time

CLICK_CLUSTER_S = 0.05      # 与 crosscheck 系列同口径（连点 50ms 内并簇）
POOL_HALF, POOL_STEP = 3.0, 0.002
CLICK2DEATH_MAX = 0.25      # 死亡前 ≤250ms 的点击算关联（§5.3）
BASELINE_N = 100
BASELINE_RANGE_S = (50.0, 950.0)   # 随机偏移抽样区间（远离峰值）
ACCEPT_RATIO = 5.0          # 峰值 ≥ 5×基线 p95
# [fix 2026-09-01 验证局] 精确 index t0_epoch 锚路径的 xcorr 偏差宽容限：
# 峰与锚偏差实测 50.8ms（远小于本限），限值取"平台宽 + 2×帧周期"量级上界。
# 密集点击（~1.3 kills/s）下互相关平台天然变宽（本局 184ms），平台宽判据失效——
# 与 0831 修"基线 p95 口径失效"同族。此限只用于精确锚 click_geom 级；旧路径不用它。
XCORR_DEV_MAX_S = 0.250
# [fix 2026-09-01 四场景波] 跟枪/hold-fire 会话点击稀疏（2156 TileFrenzy tracking
# 全程仅 18 次 L_down），click↔death 互相关峰被场景动力学锁偏：峰偏 ≈ −平均击杀延迟
#（2021 flick −51ms≈TTK45ms 同构；2156 反向锁到"死亡簇→下一轮开火"= +1.125s）。
# 双录 target_poll2×2 + 相机 yaw↔输入幅度 + 目标方位角↔相机 yaw 三方对账实证三通道
# 钟互差 ≤±40ms（峰 +0.000s/−0.020s），1.125s 与钟无关。故精确锚路径分级验收：
#   click_geom   —— 点击富集局：click 几何回执 + xcorr 偏差门（原判据）
#   tracking_aim —— 跟枪局：死亡前 200ms 窗准星→垂死目标最小夹角（点击无关，
#                     直接验证锚；负对照：正确锚中位 1.75°，错位 ±1.125s → 34°/44°）
CLICK_CHECK_MIN_N = 5       # click_geom 级：click 几何回执最少配对数
AIM_CHECK_MIN_N = 5         # tracking_aim 级：aim-at-death 回执最少 life 数
AIM_MEDIAN_MAX_DEG = 5.0    # tracking_aim 级：窗最小夹角中位上限
AIM_SHARE10_MIN = 0.5       # tracking_aim 级：窗最小夹角 <10° 占比下限
GAP_MS = 200.0              # 视角轨迹空洞标注阈值（终验口径 >200ms 计空洞）


# ---------------- 定位 rounds_index 与 source 条目 ----------------

def find_index_and_source(round_dir):
    idx_path = None
    for cand in (os.path.join(round_dir, "rounds_index.json"),
                 os.path.join(os.path.dirname(round_dir), "rounds_index.json")):
        if os.path.isfile(cand):
            idx_path = cand
            break
    if idx_path is None:
        raise SystemExit("!! 找不到 rounds_index.json（round-dir 内/父目录均无）")
    idx = json.load(open(idx_path, encoding="utf-8"))
    name = os.path.basename(os.path.normpath(round_dir))
    entry = None
    for src in idx.get("sources", []):
        if src.get("outdir") == name or str(src.get("source", "")).startswith(name + "."):
            entry = src
            break
    if entry is None:
        raise SystemExit("!! rounds_index 里没有 source 匹配目录名 %r" % name)
    return idx_path, idx, entry


# ---------------- 相机 / 输入 通道读取 ----------------

def load_camera(path):
    """首行 clock_map(epoch 锚) + cam 帧流。null 行只计数（无 t，§2.4-2）。"""
    epoch = None
    frames = []          # (t, pos, rot, fov)
    nulls = 0
    extra_maps = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                nulls += 1
                continue
            if rec is None:
                nulls += 1
            elif rec.get("ev") == "clock_map":
                if epoch is None:
                    epoch = float(rec["t"])
                else:
                    extra_maps += 1
            elif rec.get("ev") == "cam":
                frames.append((float(rec["t"]), rec["pos"], rec["rot"], rec["fov"]))
    if epoch is None:
        raise SystemExit("!! 相机文件缺首行 clock_map（无法定 epoch 锚）")
    frames.sort(key=lambda r: r[0])
    dts = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    return {
        "path": path, "epoch": epoch, "frames": frames, "n_null": nulls,
        "n_extra_map": extra_maps,
        "dt": {"median_ms": round(statistics.median(dts) * 1000, 2),
               "p95_ms": round(sorted(dts)[int(len(dts) * 0.95)] * 1000, 2),
               "max_ms": round(max(dts) * 1000, 2)} if dts else {},
        "span_s": [frames[0][0], frames[-1][0]] if frames else None,
    }


def load_input_maps_clicks(path):
    """第一遍流式：clock_map（epoch↔perf 换算）+ L_down 点击时刻（perf 域）。"""
    deltas, clicks = [], []
    n_events = 0
    first_t = last_t = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev = rec.get("ev")
            if ev == "clock_map":
                deltas.append(float(rec["t_unix"]) - float(rec["t"]))
            elif ev == "m":
                n_events += 1
                t = float(rec["t"])
                first_t = t if first_t is None else first_t
                last_t = t
                if any(b == "L_down" for b in rec.get("btn", [])):
                    clicks.append(t)
    if not deltas:
        raise SystemExit("!! 输入日志缺 clock_map（无法换 epoch）")
    delta = sum(deltas) / len(deltas)
    drift = (max(deltas) - min(deltas)) if deltas else 0.0
    clicks.sort()
    clustered = []
    for t in clicks:
        if not clustered or t - clustered[-1] > CLICK_CLUSTER_S:
            clustered.append(t)
    return {
        "path": path, "delta": delta, "drift_s": drift, "n_events": n_events,
        "n_clicks_raw": len(clicks), "n_clicks_clustered": len(clustered),
        "clicks_perf": clustered,
        "span_perf": [first_t, last_t] if first_t is not None else None,
    }


# ---------------- 对齐：池化 click↔death 互相关 ----------------

def _score_at(death_t_src, click_epochs, off):
    """score(off) = 死亡时刻(t域+off→epoch) 前 ≤250ms 存在点击的死亡数。"""
    n = 0
    for d in death_t_src:
        de = d + off
        j = bisect.bisect_right(click_epochs, de) - 1
        if j >= 0 and 0.0 <= de - click_epochs[j] <= CLICK2DEATH_MAX:
            n += 1
    return n


def correlate(death_t_src, click_epochs, seed):
    """返回互相关回执。s = 源 t=0 的 epoch。"""
    grid = [seed + i * POOL_STEP
            for i in range(-int(POOL_HALF / POOL_STEP), int(POOL_HALF / POOL_STEP) + 1)]
    scores = [_score_at(death_t_src, click_epochs, s) for s in grid]
    best = max(range(len(grid)), key=lambda i: scores[i])
    b = scores[best]
    lo = best
    while lo > 0 and scores[lo - 1] >= b:
        lo -= 1
    hi = best
    while hi < len(grid) - 1 and scores[hi + 1] >= b:
        hi += 1
    rng = random.Random(20260831)
    base = [_score_at(death_t_src, click_epochs,
                      seed + rng.uniform(*BASELINE_RANGE_S)) for _ in range(BASELINE_N)]
    base_sorted = sorted(base)
    lat = []
    s = grid[best]
    for d in death_t_src:
        de = d + s
        j = bisect.bisect_right(click_epochs, de) - 1
        if j >= 0 and 0.0 <= de - click_epochs[j] <= CLICK2DEATH_MAX:
            lat.append((de - click_epochs[j]) * 1000.0)
    return {
        "s": round(s, 3), "score": b, "plateau_s": [round(grid[lo], 3), round(grid[hi], 3)],
        "baseline": {"n": BASELINE_N, "mean": round(statistics.mean(base), 2),
                     "p95": base_sorted[int(len(base) * 0.95)],
                     "max": max(base)},
        "n_deaths": len(death_t_src),
        "click_to_death_latency_ms": {
            "n": len(lat),
            "median": round(statistics.median(lat), 1) if lat else None,
            "p90": round(sorted(lat)[int(len(lat) * 0.9)], 1) if lat else None},
    }


# ---------------- 旁车写出 ----------------

def atomic_write_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="相机/输入并入 cleaned 轮次（旁车）")
    ap.add_argument("--round-dir", required=True)
    ap.add_argument("--camera", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--year", type=int, default=time.localtime().tm_year,
                    help="文件名锚补年（默认当前年）")
    ap.add_argument("--check", action="store_true", help="击杀点击几何回执（角误差分布）")
    args = ap.parse_args()

    round_dir = os.path.abspath(args.round_dir)
    idx_path, _idx, entry = find_index_and_source(round_dir)
    rounds = sorted(entry.get("rounds", []), key=lambda r: r["t_start"])
    if not rounds:
        raise SystemExit("!! source 条目无 rounds")

    t0_epoch = entry.get("t0_epoch")   # cleaner 配套补丁透传；旧 index 无此字段

    cam = load_camera(args.camera)
    inp = load_input_maps_clicks(args.input)
    click_epochs = sorted(t + inp["delta"] for t in inp["clicks_perf"])

    # 粗锚：目录名墙钟
    m = re.match(r".*?(\d{4})_(\d{6})$", os.path.basename(round_dir))
    if not m:
        raise SystemExit("!! 轮目录名不带 MMDD_HHMMSS 且 index 无 t0_epoch，无法定粗锚")
    mmdd, hhmmss = m.group(1), m.group(2)
    seed = time.mktime((args.year, int(mmdd[:2]), int(mmdd[2:]),
                        int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:]),
                        0, 0, -1))

    deaths_t = [lv["t_end"]
                for r in entry.get("rounds", [])
                for tgt in r.get("targets", [])
                for lv in tgt.get("lives", [])]
    xcr = correlate(deaths_t, click_epochs, seed)
    print("[align] 粗锚 seed=%.3f  互相关 s*=%.3f  plateau=[%.3f, %.3f]  "
          "score=%d/%d  基线 mean=%.1f p95=%d max=%d  click→death 中位=%sms" % (
              seed, xcr["s"], xcr["plateau_s"][0], xcr["plateau_s"][1],
              xcr["score"], xcr["n_deaths"], xcr["baseline"]["mean"],
              xcr["baseline"]["p95"], xcr["baseline"]["max"],
              xcr["click_to_death_latency_ms"]["median"]))

    accepted = xcr["score"] >= ACCEPT_RATIO * max(xcr["baseline"]["mean"], 1) \
        and (xcr["plateau_s"][1] - xcr["plateau_s"][0]) <= 0.050
    # 判据说明: FORMAT §5.3 的"≥5×基线p95"按稀疏会话标定；连续点击的密集会话里
    # 250ms 窗 × ~1点击/秒 使随机偏移也高概率命中（实测本会话 p95=213/833≈26%），
    # 故改用 峰值≥5×基线均值 + 平台宽≤50ms（锐度才是判别量；实测平台 4ms）。
    if t0_epoch is not None:
        s = float(t0_epoch)
        print("[align] 使用 index t0_epoch=%.3f（互相关仅诊断，偏差 %.3fs）"
              % (s, xcr["s"] - s))
        method = "index_t0_epoch+xcorr_verify"
    elif accepted:
        s = xcr["s"]
        method = "pooled_click_death_xcorr"
    else:
        print("!! 互相关不达峰（%d < %.1f×基线mean=%.1f 或平台过宽）且无 t0_epoch "
              "—— 拒绝写旁车" % (xcr["score"], ACCEPT_RATIO,
                                 xcr["baseline"]["mean"]))
        return 2

    # 相机帧 → 源 t 域
    cam_ts = [cam["epoch"] + t - s for (t, _p, _r, _f) in cam["frames"]]

    # [fix 2026-09-01 验证局→四场景波] 精确 index t0_epoch 锚路径：分级验收
    # （无 t0_epoch 的旧 xcorr 路径一字不动，见上 accepted 计算）。
    #   click_geom   —— ① kill-click 几何回执（n≥5 且中位 ≤1°，实战依据：0901
    #                     验证局中位 0.233°/<1°占98%，与 verify0831 黄金 0.231° 一致）
    #                    ② xcorr 峰与锚偏差 ≤250ms（密集点击平台宽失效，见
    #                     XCORR_DEV_MAX_S 注）；
    #   tracking_aim —— 跟枪/hold-fire 局：click 稀疏使互相关峰被场景动力学锁偏
    #                    （2156 实测 +1.125s，三方钟差对账证明钟互差 ≤±40ms），改用
    #                    death_aim_check（死亡前 200ms 窗准星→垂死目标最小夹角，
    #                    点击无关、直接验证锚；负对照见常量注）。
    #   两级都不过 ⇒ fail-closed。aim 回执在 index 锚上评估，锚真错位时它必炸，
    #   故 tracking_aim 不会静默放过坏锚。
    check_result = None
    aim_result = None
    accept_grade = None
    if t0_epoch is not None:
        check_result = kill_click_check(round_dir, rounds, cam, cam_ts, s,
                                        click_epochs)
        aim_result = death_aim_check(round_dir, rounds, cam, cam_ts)
        xcorr_dev_s = abs(xcr["s"] - s)
        click_geom_ok = (check_result["n"] >= CLICK_CHECK_MIN_N
                         and check_result["median_deg"] is not None
                         and check_result["median_deg"] <= 1.0
                         and xcorr_dev_s <= XCORR_DEV_MAX_S)
        aim_ok = (aim_result["n"] >= AIM_CHECK_MIN_N
                  and aim_result["median_deg"] is not None
                  and aim_result["median_deg"] <= AIM_MEDIAN_MAX_DEG
                  and aim_result["share_lt_10deg"] >= AIM_SHARE10_MIN)
        if click_geom_ok:
            accept_grade = "click_geom"
        elif aim_ok:
            accept_grade = "tracking_aim"
        accepted = accept_grade is not None
        print("[align] 精确锚分级验收: click_geom(n=%d 中位=%s°, xcorr偏差=%.0fms)"
              "=%s | tracking_aim(n=%d 窗最小中位=%s°, <10°占%.0f%%)=%s"
              " => grade=%s accepted=%s"
              % (check_result["n"], check_result["median_deg"],
                 xcorr_dev_s * 1000.0, click_geom_ok,
                 aim_result["n"], aim_result["median_deg"],
                 100.0 * (aim_result["share_lt_10deg"] or 0.0), aim_ok,
                 accept_grade, accepted))
        if not accepted:
            print("!! 精确锚对齐验收未过（click 几何与 aim-at-death 双回执均不达标）"
                  "—— 拒绝写旁车")
            return 2

    # 输入第二遍：先收集 (t_src, dx, dy, btn)（轮窗可能重叠——cleaner 的轮按出生
    # 事件切分，长寿命目标可跨轮——事件归属"所有包含它的轮"，与 views 同语义）
    ev_rows = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ev") != "m":
                continue
            ev_rows.append((float(rec["t"]) + inp["delta"] - s,
                            rec["dx"], rec["dy"], tuple(rec.get("btn", []))))
    ev_rows.sort(key=lambda r: r[0])
    ev_ts = [r[0] for r in ev_rows]

    manifest_rounds = []
    for i, r in enumerate(rounds):
        nn = r["round"]
        lo, hi = r["t_start"], r["t_end"]
        a = bisect.bisect_left(cam_ts, lo)
        b = bisect.bisect_right(cam_ts, hi)
        views = [{"t": round(cam_ts[j], 3), "pos": cam["frames"][j][1],
                  "rot": cam["frames"][j][2], "fov": cam["frames"][j][3]}
                 for j in range(a, b)]
        gaps = 0
        if len(views) >= 2:
            gaps = sum(1 for x, y in zip(views, views[1:])
                       if y["t"] - x["t"] > GAP_MS / 1000.0)
        a2 = bisect.bisect_left(ev_ts, lo)
        b2 = bisect.bisect_right(ev_ts, hi)
        irows = [{"t": round(t, 3), "dx": dx, "dy": dy, "btn": list(btn)}
                 for (t, dx, dy, btn) in ev_rows[a2:b2]]
        if views:
            atomic_write_jsonl(os.path.join(round_dir, "views_%02d.jsonl" % nn), views)
        if irows:
            atomic_write_jsonl(os.path.join(round_dir, "inputs_%02d.jsonl" % nn), irows)
        manifest_rounds.append({
            "round": nn, "file": r.get("file", "round_%02d.jsonl" % nn),
            "t_start": lo, "t_end": hi,
            "n_views": len(views), "view_gaps_gt_200ms": gaps,
            "n_inputs": len(irows)})
        print("  round %2d  [%7.1f, %7.1f]s  views=%-6d gaps>200ms=%-3d inputs=%-6d" % (
            nn, lo, hi, len(views), gaps, len(irows)))

    aln = {"method": method, "seed_epoch": round(seed, 3),
           "s_epoch_of_t0": s, "xcorr": xcr, "accepted": accepted,
           "t0_epoch_from_index": t0_epoch}
    if t0_epoch is not None:   # [fix 2026-09-01] 分级验收语义自描述，供 AC 侧审计
        aln["accept_grade"] = accept_grade
        aln["accept_rule"] = (
            "click_geom: check.n>=%d and check.median_deg<=1.0 and "
            "|xcorr.s-index_s|<=%.3fs | tracking_aim: aim_check.n>=%d and "
            "aim_check.median_deg<=%.1f and aim_check.share_lt_10deg>=%.2f "
            "(死亡前200ms窗最小夹角; xcorr 峰在 click 稀疏局被场景动力学锁偏, 仅诊断)"
            % (CLICK_CHECK_MIN_N, XCORR_DEV_MAX_S, AIM_CHECK_MIN_N,
               AIM_MEDIAN_MAX_DEG, AIM_SHARE10_MIN))
    manifest = {
        "schema_version": "round_merge.v1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "round_dir": round_dir, "rounds_index": os.path.abspath(idx_path),
        "rounds_index_mtime_ns": os.stat(idx_path).st_mtime_ns,
        "source": entry.get("source"),
        "camera": {"path": os.path.abspath(args.camera), "epoch_anchor": cam["epoch"],
                   "n_cam": len(cam["frames"]), "n_null": cam["n_null"],
                   "n_extra_map": cam["n_extra_map"], "dt": cam["dt"],
                   "span_s": cam["span_s"]},
        "input": {"path": os.path.abspath(args.input),
                  "delta_epoch_perf": round(inp["delta"], 6),
                  "drift_s": inp["drift_s"], "n_events": inp["n_events"],
                  "n_clicks_raw": inp["n_clicks_raw"],
                  "n_clicks_clustered": inp["n_clicks_clustered"],
                  "span_perf": inp["span_perf"]},
        "alignment": aln,
        "t_domain_note": ("sidecar 与轮文件同 t 域（源文件相对秒）；"
                          "epoch = s_epoch_of_t0 + t"),
        "rounds": manifest_rounds,
    }

    if check_result is not None:   # 精确锚路径：双回执是验收证据，无论 --check 都入 manifest
        manifest["check"] = check_result
        manifest["aim_check"] = aim_result
    elif args.check:
        manifest["check"] = kill_click_check(round_dir, rounds, cam, cam_ts, s,
                                             click_epochs)

    dst = os.path.join(round_dir, "merge_manifest.json")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    print("[done] %d 轮旁车 + merge_manifest.json → %s" % (len(rounds), round_dir))
    print("[note] rounds_index 若重洗（mtime 变化）本旁车即过期，需重跑本工具")
    return 0


# ---------------- --check：击杀点击几何回执 ----------------

def kill_click_check(round_dir, rounds, cam, cam_ts, s, click_epochs):
    """复用 §5.4 的判据做几何验收：击杀点击瞬间，准心→垂死目标角误差应收敛。
    实测参照（final_0831 R03）：中位 ~0.4°、~70% <1°。误差大 = 对齐或几何链路出问题。"""
    import math
    errs_all = []
    per_round = []
    clicks_src = [e - s for e in click_epochs]
    for r in rounds:
        rf = os.path.join(round_dir, r.get("file", "round_%02d.jsonl" % r["round"]))
        if not os.path.isfile(rf):
            continue
        pos_by_addr = {}
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
                for e in rec.get("targets") or []:
                    pos_by_addr.setdefault(int(e[0]), []).append(
                        (float(rec["t"]), float(e[1]), float(e[2]), float(e[3])))
        errs = []
        for tgt in r.get("targets", []):
            pts = pos_by_addr.get(tgt.get("addr"))
            if not pts:
                continue
            pts.sort()
            pt_ts = [p[0] for p in pts]
            for lv in tgt.get("lives", []):
                t_end = lv["t_end"]
                j = bisect.bisect_right(clicks_src, t_end) - 1
                if j < 0 or t_end - clicks_src[j] > CLICK2DEATH_MAX:
                    continue
                k = bisect.bisect_right(pt_ts, t_end) - 1
                if k < 0:
                    continue
                _t, x, y, z = pts[k]
                c = bisect.bisect_left(cam_ts, clicks_src[j])
                c = min(max(c, 0), len(cam_ts) - 1)
                _ct, pos, rot, _fov = cam["frames"][c]
                dx, dy, dz = (x - pos[0], y - pos[1], z - pos[2])
                yaw_t = math.degrees(math.atan2(dy, dx))
                pitch_t = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
                dyaw = ((yaw_t - rot[1] + 180.0) % 360.0) - 180.0
                errs.append(math.hypot(dyaw, pitch_t - rot[0]))
        if errs:
            errs.sort()
            errs_all.extend(errs)
            per_round.append({
                "round": r["round"], "n": len(errs),
                "median_deg": round(statistics.median(errs), 3),
                "p25_deg": round(errs[len(errs) // 4], 3),
                "p75_deg": round(errs[3 * len(errs) // 4], 3)})
    errs_all.sort()
    out = {
        "note": "击杀点击瞬间 准心→目标 角误差（几何+对齐总验收）",
        "n": len(errs_all),
        "median_deg": round(statistics.median(errs_all), 3) if errs_all else None,
        "p25_deg": round(errs_all[len(errs_all) // 4], 3) if errs_all else None,
        "p75_deg": round(errs_all[3 * len(errs_all) // 4], 3) if errs_all else None,
        "share_lt_1deg": round(sum(1 for e in errs_all if e < 1.0) / max(len(errs_all), 1), 3),
        "share_lt_3deg": round(sum(1 for e in errs_all if e < 3.0) / max(len(errs_all), 1), 3),
        "per_round": per_round,
    }
    print("[check] 击杀点击角误差: n=%d 中位=%s° p25=%s° p75=%s°  <1°占%.0f%%  <3°占%.0f%%" % (
        out["n"], out["median_deg"], out["p25_deg"], out["p75_deg"],
        100 * (out["share_lt_1deg"] or 0), 100 * (out["share_lt_3deg"] or 0)))
    return out


def death_aim_check(round_dir, rounds, cam, cam_ts, win_ms=200):
    """[fix 2026-09-01 四场景波] 跟枪/hold-fire 兼容回执：死亡前 win_ms 窗内
    准星→垂死目标 最小夹角（与点击无关）。跟踪场景死亡瞬间准星应在靶上；
    该角在锚真错位时数量级恶化，故直接充当锚验收（2156 R3 负对照：
    正确锚中位 1.75°/<10°占61%，错位 ±1.125s → 34.3°/10% 与 44.3°/3%）。
    覆盖统计与 kill_click_check 同构（rounds_index addr ↔ 轮文件轨迹配对）。"""
    import math
    win = win_ms / 1000.0
    errs_all = []
    per_round = []
    for r in rounds:
        rf = os.path.join(round_dir, r.get("file", "round_%02d.jsonl" % r["round"]))
        if not os.path.isfile(rf):
            continue
        pos_by_addr = {}
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
                for e in rec.get("targets") or []:
                    pos_by_addr.setdefault(int(e[0]), []).append(
                        (float(rec["t"]), float(e[1]), float(e[2]), float(e[3])))
        errs = []
        for tgt in r.get("targets", []):
            pts = pos_by_addr.get(tgt.get("addr"))
            if not pts:
                continue
            pts.sort()
            pt_ts = [p[0] for p in pts]
            for lv in tgt.get("lives", []):
                t_end = lv["t_end"]
                k = bisect.bisect_right(pt_ts, t_end) - 1
                if k < 0:
                    continue
                _t, x, y, z = pts[k]
                lo = bisect.bisect_left(cam_ts, t_end - win)
                hi = bisect.bisect_right(cam_ts, t_end)
                best = None
                for c in range(lo, hi):
                    _ct, pos, rot, _fov = cam["frames"][c]
                    dx, dy, dz = x - pos[0], y - pos[1], z - pos[2]
                    yaw_t = math.degrees(math.atan2(dy, dx))
                    pitch_t = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
                    dyaw = ((yaw_t - rot[1] + 180.0) % 360.0) - 180.0
                    e = math.hypot(dyaw, pitch_t - rot[0])
                    if best is None or e < best:
                        best = e
                if best is not None:
                    errs.append(best)
        if errs:
            errs.sort()
            errs_all.extend(errs)
            per_round.append({
                "round": r["round"], "n": len(errs),
                "median_deg": round(statistics.median(errs), 3)})
    errs_all.sort()
    out = {
        "note": "死亡前 %dms 窗内 准星→垂死目标 最小夹角（跟枪兼容锚验收；锚错位⇒数量级恶化）"
                % win_ms,
        "window_ms": win_ms,
        "n": len(errs_all),
        "median_deg": round(statistics.median(errs_all), 3) if errs_all else None,
        "p25_deg": round(errs_all[len(errs_all) // 4], 3) if errs_all else None,
        "p75_deg": round(errs_all[3 * len(errs_all) // 4], 3) if errs_all else None,
        "share_lt_5deg": round(sum(1 for e in errs_all if e < 5.0) / max(len(errs_all), 1), 3),
        "share_lt_10deg": round(sum(1 for e in errs_all if e < 10.0) / max(len(errs_all), 1), 3),
        "per_round": per_round,
    }
    print("[aim_check] 死亡前%dms窗最小夹角: n=%d 中位=%s° p25=%s° p75=%s°  "
          "<5°占%.0f%%  <10°占%.0f%%"
          % (win_ms, out["n"], out["median_deg"], out["p25_deg"], out["p75_deg"],
             100 * (out["share_lt_5deg"] or 0), 100 * (out["share_lt_10deg"] or 0)))
    return out


if __name__ == "__main__":
    sys.exit(main())
