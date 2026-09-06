#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cleaner.py — KovaaK's 外部遥测（ReadProcessMemory 采样）目标轨迹清洗器 + 轮次切分器。

输入: target_poll2.py 采出的 JSONL（{"ev":"frame","t":秒,"targets":[[addr,x,y,z],...]}）
输出: <outdir>/<输入名>/round_NN.jsonl（每轮清洗后帧） + <outdir>/rounds_index.json（轮次元数据 + 丢弃报表）

清洗规则（阈值全部来自 2026-08-30 三份验证数据的实测，详见 FORMAT.md）:
  1. NaN/Inf 点剔除；|坐标| > 8192 判为野值点（场景域 ≤±4096）；
  2. 轨道按对象地址重建；段内单帧位移 >2000 或单帧速度 >5000 u/s 处切段
     （= 瞬移/击杀重生；速度阈值兜住 676~1923 u 的小距离重生跳，且对采样停顿鲁棒）；
  2b. [fix 2026-08-30] 短距重生二级判据：速度 2100~5000 u/s 且位移 60~2000 u 的帧间跳变，
     若方向学上是"换道跳"（与段内前 3 步平均滑行方向夹角 >78°，即 cos<0.2；静态目标则要求
     位移 >100 u；与相邻步构成"跳起-回落"弧对者除外）亦判重生切段。修复对拍发现的
     "死亡+短距重生落在一帧采样间隙内被并成一段 life"问题（beanClick Valorant 实测重生跳
     v≈2330~3019 u/s、d≈72~97 u，低于主阈值；详见 analysis/external/cleaner_fix_0830.md）。
     方向学条件用于豁免两类同量级合法移动：采样/引擎卡顿的同向前窜（cos≈+1）与
     跳跃动画的起落弧（前后相邻步与跳变反向等幅）。
  3. 全原点段（死亡残留）丢弃；整轨寿命 <2s 且总位移 ≈0 的轨道丢弃；
  4. 幽灵轨道剔除（首帧即出现 + 出现帧占比 >95% + 近零移动，如 CDO/预览体）；
  5. 轮次切分：按"一批新目标同时出生"聚类——出生事件间隔 >10s，或全灭持续 >0.05s
     （约 2 帧）后再有出生 → 新一轮（同 10 秒窗口内出生算同轮，池化地址复用也算出生）。

用法:
    PYTHONIOENCODING=utf-8 ~/AppData/Local/Programs/Python/Python39/python cleaner.py <input.jsonl> [more.jsonl ...]
    可选: --outdir cleaned  --jump-dist 2000  --jump-speed 5000  --bound 8192
                       --birth-gap 10  --dead-gap 0.2  --min-life 2
纯本地只读工具：只读输入 JSONL，不触碰游戏进程。
"""
import argparse
import json
import math
import os
import sys

# ---------------- 默认参数（实测校准，勿随意改；详见 FORMAT.md） ----------------
JUMP_DIST = 2000.0      # 单帧位移阈值（任务规则：>2000 单位判瞬移）
JUMP_SPEED = 5000.0     # 单帧速度阈值 u/s（实测最小重生跳速 ≈8450 u/s；正常移动 ≤~2000 u/s）
# ---- [fix 2026-08-30] 短距重生二级判据常量（取证见 analysis/external/cleaner_fix_0830.md） ----
RESPAWN2_SPEED = 2100.0   # 二级判据速度下限 u/s（实测短距重生跳 2330~4156；滑行/走位 ≤~2000）
RESPAWN2_DIST = 60.0      # 二级判据位移下限 u（实测重生跳 68~128；滑行步 ≤36、卡顿前窜 60~69）
LANE_COS = 0.2            # 跳变方向 vs 段内滑行方向：cos < 该值(夹角>78°)判换道重生
                          # （实测换道重生 cos −0.64~−0.99；同向卡顿前窜 cos≈+1；跳跃动画 +0.34）
AMBIENT_STATIC_SPEED = 50.0   # 段内前 3 步均速 < 该值判"静态目标"（实测静态轨步进恒 0）
RESPAWN2_STATIC_DIST = 100.0  # 静态目标的二级判据位移下限 u（墙靶重生实测 128；动画跳 ≤90）
BOUND = 8192.0          # 坐标出界阈值（KovaaK's 场景域实测 ≤±4608，含裕量）
ORIGIN_EPS = 1.0        # 距原点 <1 单位判为死亡残留点（死亡后 RootComponent 读回 0）
MIN_MOVE = 1.0          # “无移动”判定：总位移 < 该值
MIN_LIFE = 2.0          # 整轨寿命 < 该值且无移动 → 丢弃（任务规则）
MIN_SAMPLES = 3         # 段最少样本数（单帧/双帧段视为噪声）
BIRTH_GAP = 10.0        # 出生事件间隔 > 该值 → 新一轮（任务规则：同 10s 窗口算同轮）
DEAD_GAP = 0.05         # 全灭持续 > 该值后再有出生 → 新一轮。
                        # 实测：局内重生空窗 ≤1 帧(0.031s)，换局空窗 ≥2 帧(0.063s)，取中间。
PHANTOM_RATIO = 0.95    # 出现帧占比 > 该值 → 幽灵候选
PHANTOM_SPEED = 10.0    # 幽灵轨道速度上限 u/s（实测幽灵 0~2.8 u/s；真目标 ≥90 u/s）
COORD_DP = 3            # 输出坐标小数位（float32 在 4096 量级分辨率 ≈0.0005）


def fmt_addr(a):
    return "0x%x" % a


# ---------------- 1. 读入 ----------------
def load_frames(path):
    """读 JSONL → 按时间排序的帧列表 [{t, targets:[(addr,x,y,z)]}]。
    [v2.1] 跳过 ev 非 frame/None 的行（target_poll2 的 clock_map/scale 旁线），
    并带出 clock_map 的 epoch 锚（→ rounds_index source 的 t0_epoch）。
    注意：旧版 cleaner 吃新录制文件会把 clock_map 的 epoch 值当 t 排到末尾——
    两个补丁必须配套升级。"""
    frames = []
    bad = 0
    clock_map = None
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if rec.get("ev") not in (None, "frame"):
                if rec.get("ev") == "clock_map" and clock_map is None and "t" in rec:
                    clock_map = rec
                continue
            t = rec.get("t")
            ents = rec.get("targets") or []
            pts = []
            seen = set()
            for e in ents:
                try:
                    a = int(e[0])
                    x, y, z = float(e[1]), float(e[2]), float(e[3])
                except (TypeError, ValueError, IndexError):
                    bad += 1
                    continue
                if a in seen:          # 同帧同地址重复（采集端不应出现，防御）
                    continue
                seen.add(a)
                pts.append((a, x, y, z))
            frames.append({"t": float(t), "targets": pts})
    frames.sort(key=lambda fr: fr["t"])
    return frames, bad, clock_map


def is_bad_coord(x, y, z, bound):
    """野值判定：NaN/Inf 或出界。"""
    for v in (x, y, z):
        if math.isnan(v) or math.isinf(v) or abs(v) > bound:
            return True
    return False


def is_origin(x, y, z, eps):
    return (x * x + y * y + z * z) < eps * eps


# ---------------- 2. 轨道重建 + 清洗切段 ----------------
def build_tracks(frames):
    tracks = {}
    for fr in frames:
        for a, x, y, z in fr["targets"]:
            tracks.setdefault(a, []).append((fr["t"], x, y, z))
    return tracks


def _is_lane_shift(cur, jump_vec, jump_d, cfg):
    """[fix 2026-08-30] 二级短距重生判据的方向学检查：跳变是否为"换道跳"。

    cur 为当前段（末点 = 跳变前一点）；jump_vec/jump_d 为跳变位移向量/长度。
    环境运动取段内最后 ≤3 步：
      - 均速 < ambient_static_speed（静态目标，如墙靶）：位移 > respawn2_static_dist 判重生
        （实测墙靶短距重生 128 u；跳跃动画起落 ≤90 u，不切）；
      - 否则（滑行目标）：跳变方向与平均滑行方向的 cos < lane_cos（夹角 >78°）判重生
        （实测换道重生 cos −0.64~−0.99；同向卡顿前窜 cos≈+1，不切）。
    段内无历史步，或平均滑行向量退化（|mean| < 2 u，方向不可信，如减速到停）→ False。
    """
    n = min(3, len(cur) - 1)
    if n < 1:
        return False
    sx = sy = sz = 0.0
    path = 0.0
    dts = 0.0
    for i in range(len(cur) - n, len(cur)):
        p0, p1 = cur[i - 1], cur[i]
        vx, vy, vz = p1[1] - p0[1], p1[2] - p0[2], p1[3] - p0[3]
        path += math.sqrt(vx * vx + vy * vy + vz * vz)
        sx += vx
        sy += vy
        sz += vz
        dts += max(p1[0] - p0[0], 1e-6)
    if path / dts < cfg["ambient_static_speed"]:
        return jump_d > cfg["respawn2_static_dist"]
    mv = math.sqrt(sx * sx + sy * sy + sz * sz)
    if mv < 2.0:       # 平均滑行向量退化（方向不可信）→ 保守不切
        return False
    cosang = (sx * jump_vec[0] + sy * jump_vec[1] + sz * jump_vec[2]) / (mv * jump_d)
    return cosang < cfg["lane_cos"]


def _is_hop_arc(pts, i, jump_d):
    """[fix 2026-08-30] 跳跃动画保护：跳变的前/后相邻一步若与跳变反向平行且幅度相近，
    判为"跳起-落下"弧的第二/第一腿（实测弧腿对：|73~80| u 两腿 31 ms 内成对、点积<0），
    不是重生。换道重生的后继步沿新方向滑行（与跳变点积 >0），不受影响。
    """
    def _vec(k):
        return (pts[k][1] - pts[k - 1][1], pts[k][2] - pts[k - 1][2], pts[k][3] - pts[k - 1][3])
    jv = _vec(i)
    if i + 1 < len(pts):                       # 后看：本步是弧的起跳腿
        nv = _vec(i + 1)
        nm = math.sqrt(nv[0] * nv[0] + nv[1] * nv[1] + nv[2] * nv[2])
        if nm > 0.5 * jump_d and (jv[0] * nv[0] + jv[1] * nv[1] + jv[2] * nv[2]) < 0:
            return True
    if i >= 2:                                 # 前看：本步是弧的回落腿
        pv = _vec(i - 1)
        pm = math.sqrt(pv[0] * pv[0] + pv[1] * pv[1] + pv[2] * pv[2])
        if pm > 0.5 * jump_d and (pv[0] * jv[0] + pv[1] * jv[1] + pv[2] * jv[2]) < 0:
            return True
    return False


def split_track(pts, cfg):
    """单地址轨道 → (有效段列表, 统计)。

    切段时机：野值/原点点、单帧位移 > jump_dist、单帧速度 > jump_speed、
    [fix 2026-08-30] 二级短距重生判据（速度/位移低于主阈值但方向学为换道跳，见 2b）。
    返回的段只含有效点；全原点/野值段整体丢弃（计入 stats）。
    """
    lives = []
    cur = []
    n_bad = 0        # 野值/NaN 点数
    n_origin = 0     # 原点残留点数
    n_jump_cut = 0   # 因跳变切段次数
    n_respawn2_cut = 0   # [fix 2026-08-30] 二级短距重生判据切段次数
    prev = None
    for pi, p in enumerate(pts):
        t, x, y, z = p
        if is_bad_coord(x, y, z, cfg["bound"]):
            n_bad += 1
            if cur:
                lives.append(cur)
                cur = []
            prev = None
            continue
        if is_origin(x, y, z, cfg["origin_eps"]):
            n_origin += 1
            if cur:
                lives.append(cur)
                cur = []
            prev = None
            continue
        cut = False
        if prev is not None and cur:
            d = math.dist(prev[1:4], (x, y, z))
            dt = max(t - prev[0], 1e-6)
            v = d / dt
            if d > cfg["jump_dist"] or v > cfg["jump_speed"]:
                cut = True
                n_jump_cut += 1
            elif v > cfg["respawn2_speed"] and d > cfg["respawn2_dist"] \
                    and not _is_hop_arc(pts, pi, d):
                # [fix 2026-08-30] 短距重生合并修复：死亡+重生落在同一采样间隙、
                # 位移/速度低于主阈值的情形（详见 cleaner_fix_0830.md）
                if _is_lane_shift(cur, (x - prev[1], y - prev[2], z - prev[3]), d, cfg):
                    cut = True
                    n_respawn2_cut += 1
        if cut:
            lives.append(cur)
            cur = []
        cur.append(p)
        prev = p
    if cur:
        lives.append(cur)
    # 丢噪声段与无效段
    kept = []
    n_noise = 0
    for seg in lives:
        if len(seg) < cfg["min_samples"]:
            n_noise += 1
            continue
        kept.append(seg)
    stats = {"nan_or_bound_points": n_bad, "origin_points": n_origin,
             "jump_cuts": n_jump_cut, "noise_segments": n_noise,
             "respawn2_cuts": n_respawn2_cut}   # [fix 2026-08-30]
    return kept, stats


def seg_stats(seg):
    path = 0.0
    for i in range(1, len(seg)):
        path += math.dist(seg[i - 1][1:4], seg[i][1:4])
    xs = [p[1] for p in seg]
    ys = [p[2] for p in seg]
    zs = [p[3] for p in seg]
    dur = seg[-1][0] - seg[0][0]
    return {
        "t_start": seg[0][0], "t_end": seg[-1][0],
        "n": len(seg), "path": path, "duration": dur,
        "domain": [min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)],
    }


# ---------------- 3. 幽灵轨道判定 ----------------
def find_phantoms(frames, cleaned, cfg):
    """幽灵轨道：首帧(首个非空帧)即出现 + 出现帧占比>阈值 + 近零移动。

    机制：TargetHudComponent owner 扫描会把 CDO/预览体等非目标对象一并捞进来，
    它们从采样开始就存在、贯穿全部帧、几乎不动。真目标不可能贯穿局间空窗。
    """
    total = len(frames)
    if total == 0:
        return {}
    first_addrs = set()
    for fr in frames:
        if fr["targets"]:
            first_addrs = {e[0] for e in fr["targets"]}
            break
    counts = {a: 0 for a in cleaned}
    for fr in frames:
        for a, *_ in fr["targets"]:
            if a in counts:
                counts[a] += 1
    phantoms = {}
    for a, lives in cleaned.items():
        if not lives:
            continue
        path = sum(s["path"] for s in (seg_stats(l) for l in lives))
        life = lives[-1][-1][0] - lives[0][0][0]
        speed = path / life if life > 0 else 0.0
        ratio = counts.get(a, 0) / total
        if a in first_addrs and ratio > cfg["phantom_ratio"] and speed < cfg["phantom_speed"]:
            phantoms[a] = {"presence_ratio": round(ratio, 4),
                           "speed_ups": round(speed, 2), "path": round(path, 1)}
    return phantoms


# ---------------- 4. 轮次切分 ----------------
def assign_rounds(targets, cfg):
    """按出生波次聚类。targets: {addr: [life_seg,...]}（已清洗、已剔幽灵）。

    新一轮条件（满足其一）:
      a) 与上一个出生事件间隔 > birth_gap（任务规则：同 10s 窗口算同轮）；
      b) 此前的段已全部结束 ≥ dead_gap（“全灭”→ 换局；兜住 10s 内连续两波出生，
         如验证文件 1 中上一局残尾 20.68s / 新局 22.34s）。
    返回 rounds: [ {t_start, t_end, {addr: lives}} ]（按 t_start 升序）。
    """
    births = []  # (t, addr, seg_idx)
    for a, lives in targets.items():
        for i, seg in enumerate(lives):
            births.append((seg[0][0], a, i))
    births.sort()
    rounds = []
    cur = None       # dict addr -> lives
    prev_birth = None
    max_end = None   # 已出生段的最晚结束时间
    for t, a, i in births:
        new_round = False
        if cur is None:
            new_round = True
        else:
            if prev_birth is not None and t - prev_birth > cfg["birth_gap"]:
                new_round = True
            elif max_end is not None and t - max_end > cfg["dead_gap"]:
                new_round = True
        if new_round:
            cur = {}
            rounds.append(cur)
        cur.setdefault(a, []).append(targets[a][i])
        seg = targets[a][i]
        end = seg[-1][0]
        max_end = end if max_end is None else max(max_end, end)
        prev_birth = t
    return rounds


# ---------------- 5. 主流程 ----------------
def clean_file(path, outdir, cfg):
    name = os.path.splitext(os.path.basename(path))[0]
    frames, bad_recs, t0_map = load_frames(path)
    tracks = build_tracks(frames)

    discarded = {
        "malformed_records": bad_recs,
        "phantom_tracks": {},     # 幽灵轨道（HUD 扫描误捞的非目标对象）
        "origin_ghost_tracks": {},  # 全程只有原点读数的轨道（死亡残留/CDO）
        "static_ghost_tracks": {},  # 寿命<2s 且无移动（任务规则）
        "garbage_points": 0,      # NaN/出界/原点点数合计
        "noise_segments": 0,
    }

    cleaned = {}       # addr -> [life_seg,...]
    per_addr_stats = {}
    for a, pts in tracks.items():
        lives, st = split_track(pts, cfg)
        per_addr_stats[a] = st
        discarded["garbage_points"] += st["nan_or_bound_points"] + st["origin_points"]
        discarded["noise_segments"] += st["noise_segments"]
        if lives:
            cleaned[a] = lives
        elif st["origin_points"] > 0:
            # 没有任何有效段、只有原点读数 → 全原点幽灵（审计留痕）
            discarded["origin_ghost_tracks"][fmt_addr(a)] = {"points": len(pts)}

    # 整轨规则：寿命 < min_life 且总位移 < min_move → 丢
    for a in list(cleaned):
        lives = cleaned[a]
        track_path = sum(seg_stats(l)["path"] for l in lives)
        track_life = lives[-1][-1][0] - lives[0][0][0]
        if track_life < cfg["min_life"] and track_path < cfg["min_move"]:
            discarded["static_ghost_tracks"][fmt_addr(a)] = {
                "life": round(track_life, 3), "path": round(track_path, 1)}
            del cleaned[a]

    # 幽灵轨道 → 丢
    for a, info in find_phantoms(frames, cleaned, cfg).items():
        info["addr"] = fmt_addr(a)
        discarded["phantom_tracks"][fmt_addr(a)] = info
        cleaned.pop(a, None)

    # 轮次切分
    rounds = assign_rounds(cleaned, cfg)
    os.makedirs(os.path.join(outdir, name), exist_ok=True)

    index_rounds = []
    for ri, rmap in enumerate(sorted(rounds, key=lambda m: min(s[0][0] for ls in m.values() for s in ls)), 1):
        t_start = min(s[0][0] for ls in rmap.values() for s in ls)
        t_end = max(s[-1][0] for ls in rmap.values() for s in ls)
        addrs = sorted(rmap, key=lambda a: rmap[a][0][0])  # 按出生先后定 tid
        tid_of = {a: i for i, a in enumerate(addrs)}

        # 每目标元数据
        tmeta = []
        n_moving = 0
        for a in addrs:
            lives = rmap[a]
            segs = [seg_stats(l) for l in lives]
            total_path = sum(s["path"] for s in segs)
            life = segs[-1]["t_end"] - segs[0]["t_start"]
            move_speed = sum(s["path"] for s in segs if s["duration"] > 0) / \
                max(sum(s["duration"] for s in segs), 1e-9)
            moving = move_speed > 50.0   # 静态局段内位移≈0；移动局 ≥~600 u/s
            n_moving += moving
            xs = [s["domain"] for s in segs]
            tmeta.append({
                "tid": tid_of[a],
                "addr": a,
                "addr_hex": fmt_addr(a),
                "motion": "moving" if moving else "static",
                "birth": round(segs[0]["t_start"], 4),
                "death": round(segs[-1]["t_end"], 4),
                "alive_window": [round(segs[0]["t_start"], 4), round(segs[-1]["t_end"], 4)],
                "n_samples": sum(s["n"] for s in segs),
                "n_lives": len(segs),   # 段数 = 出生(含池化重生)次数
                "path_length": round(total_path, 1),  # 总移动（段内累计位移，不含重生跳）
                "lives": [{"t_start": round(s["t_start"], 4), "t_end": round(s["t_end"], 4),
                           "n": s["n"], "path": round(s["path"], 1)} for s in segs],
                "domain": {"x": [round(min(d[0] for d in xs), 1), round(max(d[1] for d in xs), 1)],
                           "y": [round(min(d[2] for d in xs), 1), round(max(d[3] for d in xs), 1)],
                           "z": [round(min(d[4] for d in xs), 1), round(max(d[5] for d in xs), 1)]},
            })

        # 写轮次帧文件
        fname = "round_%02d.jsonl" % ri
        fpath = os.path.join(outdir, name, fname)
        n_frames = 0
        with open(fpath, "w", encoding="utf-8") as f:
            for fr in frames:
                t = fr["t"]
                if t < t_start - 1e-9 or t > t_end + 1e-9:
                    continue
                ents = []
                for a, x, y, z in fr["targets"]:
                    if a in tid_of and not is_bad_coord(x, y, z, cfg["bound"]) \
                            and not is_origin(x, y, z, cfg["origin_eps"]):
                        ents.append([a, round(x, COORD_DP), round(y, COORD_DP), round(z, COORD_DP)])
                ents.sort(key=lambda e: tid_of[e[0]])
                f.write(json.dumps({"ev": "frame", "t": round(t, 4),
                                    "tr": round(t - t_start, 4), "targets": ents},
                                   ensure_ascii=False) + "\n")
                n_frames += 1

        index_rounds.append({
            "round": ri,
            "file": fname,
            "t_start": round(t_start, 4),
            "t_end": round(t_end, 4),
            "duration": round(t_end - t_start, 4),
            "n_frames": n_frames,
            "n_targets": len(addrs),
            "n_moving_targets": n_moving,
            "motion_mix": "moving" if n_moving == len(addrs) else
                          ("static" if n_moving == 0 else "mixed"),
            "targets": tmeta,
        })

    src_meta = {
        "source": os.path.basename(path),
        "outdir": name,
        "t_min": round(frames[0]["t"], 4) if frames else None,
        "t_max": round(frames[-1]["t"], 4) if frames else None,
        "frames_total": len(frames),
        "n_rounds": len(index_rounds),
        "rounds": index_rounds,
        "discarded": discarded,
        "per_addr_cut_stats": {fmt_addr(a): st for a, st in per_addr_stats.items()},
    }
    if t0_map is not None:
        # [v2.1] 精确 epoch 锚（录制器首行 clock_map）：epoch(t) = t0_epoch + t，
        # 替代下游"文件名墙钟 ±1s"粗锚（merge_channels / AC 导入侧直接消费）
        src_meta["t0_epoch"] = round(float(t0_map["t"]), 4)
    print("[ok] %s → %d 轮, %s" % (name, len(index_rounds),
          ", ".join("R%d:%d目标[%.1f~%.1fs]" % (r["round"], r["n_targets"], r["t_start"], r["t_end"])
                    for r in index_rounds) or "无轮次"))
    return src_meta


def main():
    ap = argparse.ArgumentParser(description="KovaaK's 遥测清洗器")
    ap.add_argument("inputs", nargs="+", help="输入 JSONL（target_poll2.py 产物）")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 <首个输入目录>/cleaned）")
    ap.add_argument("--jump-dist", type=float, default=JUMP_DIST)
    ap.add_argument("--jump-speed", type=float, default=JUMP_SPEED)
    ap.add_argument("--bound", type=float, default=BOUND)
    ap.add_argument("--birth-gap", type=float, default=BIRTH_GAP)
    ap.add_argument("--dead-gap", type=float, default=DEAD_GAP)
    ap.add_argument("--min-life", type=float, default=MIN_LIFE)
    args = ap.parse_args()
    outdir = args.outdir or os.path.join(os.path.dirname(os.path.abspath(args.inputs[0])), "cleaned")
    os.makedirs(outdir, exist_ok=True)
    cfg = {"jump_dist": args.jump_dist, "jump_speed": args.jump_speed, "bound": args.bound,
           "origin_eps": ORIGIN_EPS, "min_life": args.min_life, "min_move": MIN_MOVE,
           "min_samples": MIN_SAMPLES, "birth_gap": args.birth_gap,
           "dead_gap": args.dead_gap, "phantom_ratio": PHANTOM_RATIO,
           "phantom_speed": PHANTOM_SPEED,
           # [fix 2026-08-30] 二级短距重生判据（常量，未开 CLI；见文件头 2b 与 cleaner_fix_0830.md）
           "respawn2_speed": RESPAWN2_SPEED, "respawn2_dist": RESPAWN2_DIST,
           "lane_cos": LANE_COS, "ambient_static_speed": AMBIENT_STATIC_SPEED,
           "respawn2_static_dist": RESPAWN2_STATIC_DIST}
    index = {
        "format_version": 1,
        "generator": "cleaner.py",
        "note": "坐标为 UE4 世界坐标（cm）。addr=对象指针可被复用，轮内身份以 tid 为准。"
                "清洗与切分规则见 FORMAT.md。",
        "params": {k: v for k, v in cfg.items()},
        "sources": [clean_file(p, outdir, cfg) for p in args.inputs],
    }
    ipath = os.path.join(outdir, "rounds_index.json")
    with open(ipath, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("[done] %s" % ipath)


if __name__ == "__main__":
    main()
