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
     [fix 2026-10-04] 窗口模式（--epoch-min/max 按局切窗）下三联命中者追加真目标生命周期
     复核（多段/切段、原点残留、bbox 跨度任一命中即保留，fail-open）：切窗使上一局仍在场/
     池化复用的真目标同样命中三联判据，直接整轨删除会令 assign_rounds 拿到空目标 → 0 轮。
  5. 轮次切分：按"一批新目标同时出生"聚类——出生事件间隔 >10s，或全灭持续 >0.05s
     （约 2 帧）后再有出生 → 新一轮（同 10 秒窗口内出生算同轮，池化地址复用也算出生）。
  6. [flags 2026-10-05] 三道门（对所有数据生效，阈值标定见常量块注释）：
     连续坏点迟滞（单点野值/原点不切段）、段最短时长 0.15s、轨道 valid-ratio<40%
     且段数≥3 整轨丢弃；有 ev=="death" 行（target_poll2 标志位）时 life 的 t_end
     以 death 行为权威边界，无 death 行（旧文件）回退坐标推导现行为。
  7. [flags 2026-10-05b] deaths_summary（减法口径，源级）：每槽死亡总数 = 源窗
     起止 dc 差分（观测源=帧条目 dc 列 + ev=="flag" 补采行，对采样空洞免疫），
     clean-prefix 信任规则（换绑/跨身份累计步截断，前缀仍可信）；death 行只
     负责 life 边界与时刻，不参与总数。

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
# [fix 2026-10-04] 窗口模式幽灵豁免（D2 判据）：bbox 净跨度 ≥ 该值判真目标保留。
# 慢速真目标也会单调漂移（5 u/s × 60s = 300u），CDO/预览体变换冻结、跨度 ≈0 —— 二者可判别。
PHANTOM_SPAN_KEEP = 50.0
# ---- [flags 2026-10-05] 三道门（数据侧防御，对所有数据生效）+ death 行消费 ----
# 阈值依据 .zcode/anchor-research-1005/REPORT.md 三局标定（54075 31Hz 健康 /
# 54077 200Hz 温和池化 / 54078 200Hz 池化事故）：
BAD_STREAK_CUT = 2        # 门1 连续坏点迟滞：连续 ≥2 个野值/原点点才切段，单点不切。
                          # 200Hz 把死亡瞬态拆成 valid→origin→garbage 逐态采样，
                          # 单点即切把一个死亡周期切成多段（54078: 315 lives/215 垃圾，
                          # 31Hz 同场景仅 41）；真实死亡的原点残留持续多帧，不受影响。
MIN_SEG_DUR = 0.15        # 门2 段最短时长 s：替代 Hz 相关的 MIN_SAMPLES=3（200Hz 下
                          # 3 帧=15ms 的闪烁微段全部成为合法 life，54078 垃圾微段
                          # 中位 18 样本/0.09s）；真实 strafing life p25≥1.2s，裕量大。
TRACK_VALID_RATIO = 0.40  # 门3 轨道级 valid-ratio：有效点(非野值/原点)占比 < 该值且
                          # 段数 ≥ TRACK_MIN_SEGMENTS → 整轨丢弃入审计。标定：池化
                          # 幽灵 tid15 valid 2331/13046=18%，真目标 >80%（REPORT E7）。
TRACK_MIN_SEGMENTS = 3    # 门3 联合条件"段数多"：54078 幽灵轨 13~84 段，
                          # 真目标偶发 1~2 段（局末清场/出窗）不波及。
DEATH_PAIR_AFTER = 0.25   # 有 death 行时 life 权威边界的后垫配对窗 s：死亡帧坐标
                          # 可能已坏使坐标段提前 1~2 帧结束；窗值与 merge 的
                          # CLICK2DEATH_MAX(0.25) 同族，E2 实测配对残差 ±150ms 内。
DC_MAX = 1000000         # [flags 2026-10-05c] dc 合理上限：身份换绑后 float 比特被
                          # 当 i32 读的垃圾值 ≥0x3F800000(=1065353216，1.0f 比特)，
                          # 1e6 上界全覆盖（真实死亡计数会话内远小于此）；超限观测
                          # 视为无观测丢弃（mini#2 语料 0x23c3b19e020/0x53020100/
                          # 0x5302c040 实证）。与 target_poll2.DC_MAX 同值勿单边改。
RESYNC_MIN_RUN = 3       # [flags 2026-10-05c] 异常重同步前瞻：坏步（跳变≥2/回退）
                          # 之后连续干净 +1 步数达该值 → 判定坏步=身份边界（读断层
                          # 累积/换绑复位），从坏步后新基线续计；不足 → 截断。
                          # 全语料（smoke1/2/3 共 14 局）标定：唯一需回溯的真实跳变
                          # （mini#2 e020 断层 +10）跳后 +1 游程=33；全部幻影跳变
                          # （158/572/126/59 冻结类）跳后游程=0——区分度充分。
COORD_DP = 3            # 输出坐标小数位（float32 在 4096 量级分辨率 ≈0.0005）


def fmt_addr(a):
    return "0x%x" % a


# ---------------- 1. 读入 ----------------
def load_frames(path, epoch_window=None):
    """读 JSONL → 按时间排序的帧列表 [{t, targets:[(addr,x,y,z)]}]。
    [v2.1] 跳过 ev 非 frame/None 的行（target_poll2 的 clock_map/scale 旁线），
    并带出 clock_map 的 epoch 锚（→ rounds_index source 的 t0_epoch）。
    注意：旧版 cleaner 吃新录制文件会把 clock_map 的 epoch 值当 t 排到末尾——
    两个补丁必须配套升级。
    [v2.2] epoch_window=(lo, hi)（绝对纪元秒）：按 epoch(t)=clock_map.t+t 只保留
    窗内帧（按局增量切窗用）；文件缺 clock_map 锚时返回空（该源记零轮次）。
    [flags 2026-10-05] 带出 ev=="death" 事件行（target_poll2 标志位死亡边沿）
    → [{t, addr, dc}]，同样受 epoch_window 过滤；帧 targets 条目的第 5/6 列
    （hp/dc）此处不消费——权威边界只吃 death 行，hp 列留给伤害/TTK 分析。
    返回 (frames, bad, clock_map, deaths)；deaths 为空 = 旧文件，走坐标推导
    现行为（逐字节不变）。
    [flags 2026-10-05b] 帧条目第 6 列 dc 与 ev=="flag" 观测行（坐标缺席帧的
    dc 补采，target_poll2 配套）→ dc_obs：{addr: [(t, dc)] 升序}，供
    deaths_summary 减法口径（窗口起止 dc 差分，对采样空洞免疫）。"""
    frames = []
    deaths = []
    dc_obs = {}
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
                elif rec.get("ev") == "death":
                    try:
                        deaths.append({"t": float(rec["t"]),
                                       "addr": int(rec["addr"]),
                                       "dc": int(rec["dc"])})
                    except (KeyError, TypeError, ValueError):
                        bad += 1
                elif rec.get("ev") == "flag":
                    try:
                        dcv = int(rec["dc"])
                        if 0 <= dcv <= DC_MAX:
                            dc_obs.setdefault(int(rec["addr"]), []).append(
                                (float(rec["t"]), dcv))
                    except (KeyError, TypeError, ValueError):
                        bad += 1
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
                # [flags 2026-10-05b] dc 列观测（与坐标有效性无关——死亡计数
                # 不依赖坐标；坐标 NaN/野值帧的 dc 照样入账）。
                # [flags 2026-10-05c] DC_MAX 域外观测（float 比特错位垃圾）视为
                # 无观测丢弃——账本里不留错位值，前后真实观测的步进语义不变。
                if len(e) >= 6 and e[5] is not None:
                    try:
                        dcv = int(e[5])
                        if 0 <= dcv <= DC_MAX:
                            dc_obs.setdefault(a, []).append((float(t), dcv))
                    except (TypeError, ValueError):
                        bad += 1
            frames.append({"t": float(t), "targets": pts})
    frames.sort(key=lambda fr: fr["t"])
    deaths.sort(key=lambda d: d["t"])
    for a in dc_obs:
        dc_obs[a].sort()
    if epoch_window is not None:
        if clock_map is None:
            return [], bad, None, [], {}
        lo, hi = epoch_window
        t0 = float(clock_map["t"])
        frames = [fr for fr in frames if lo <= t0 + fr["t"] <= hi]
        deaths = [d for d in deaths if lo <= t0 + d["t"] <= hi]
        dc_obs = {a: [(t, d) for t, d in s if lo <= t0 + t <= hi]
                  for a, s in dc_obs.items()}
        dc_obs = {a: s for a, s in dc_obs.items() if s}
    return frames, bad, clock_map, deaths, dc_obs


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
    [flags 2026-10-05] 三道门之一/之二（对所有数据生效）：
      门1 连续坏点迟滞：连续 ≥ bad_streak_cut 个野值/原点点才切段，单点不切——
          跳过坏点、段与 prev 保持，下一个有效点仍走跳变判据（重生跳经坏点不漏切，
          传感器单帧闪坏不再把一个 life 切成两段）；
      门2 段最短时长：kept 需 duration ≥ min_seg_dur（MIN_SAMPLES 保留兜底）。
    """
    lives = []
    cur = []
    n_bad = 0        # 野值/NaN 点数
    n_origin = 0     # 原点残留点数
    n_jump_cut = 0   # 因跳变切段次数
    n_respawn2_cut = 0   # [fix 2026-08-30] 二级短距重生判据切段次数
    prev = None
    bad_streak = 0
    cut_streak = max(1, int(cfg.get("bad_streak_cut", BAD_STREAK_CUT)))
    for pi, p in enumerate(pts):
        t, x, y, z = p
        if is_bad_coord(x, y, z, cfg["bound"]):
            n_bad += 1
            bad_streak += 1
            if bad_streak >= cut_streak and cur:
                lives.append(cur)
                cur = []
                prev = None
            continue
        if is_origin(x, y, z, cfg["origin_eps"]):
            n_origin += 1
            bad_streak += 1
            if bad_streak >= cut_streak and cur:
                lives.append(cur)
                cur = []
                prev = None
            continue
        bad_streak = 0
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
    n_short = 0
    min_dur = float(cfg.get("min_seg_dur", MIN_SEG_DUR))
    for seg in lives:
        if len(seg) < cfg["min_samples"]:
            n_noise += 1
            continue
        if seg[-1][0] - seg[0][0] < min_dur:
            n_short += 1       # 门2：时长不足的闪烁微段（additive 统计）
            continue
        kept.append(seg)
    stats = {"nan_or_bound_points": n_bad, "origin_points": n_origin,
             "jump_cuts": n_jump_cut, "noise_segments": n_noise,
             "respawn2_cuts": n_respawn2_cut,   # [fix 2026-08-30]
             "short_segments": n_short}   # [flags 2026-10-05] additive
    return kept, stats


def bind_deaths(segs, death_ts, pair_after=DEATH_PAIR_AFTER):
    """[flags 2026-10-05] 用 death 行做 life 权威边界（原位修改 segs，点为
    (t,x,y,z) 元组列表、按时间升序）。每个 death 配对到覆盖它的段：
    seg.t_start ≤ t ≤ seg.t_end + pair_after（后垫吸收死亡帧坐标已坏导致
    坐标段提前 1~2 帧结束的情形；窗值来源见 DEATH_PAIR_AFTER 注释）。
    配对成功的段：丢弃 t > death 的点（死亡后复读帧），末点时间戳改写为
    death t —— t_end 从此权威，不再靠坐标消失猜。段内 path/n 不变（只动
    末点时刻）。返回未配对的 death 数（审计）。"""
    remaining = sorted(death_ts)
    for seg in segs:
        if not remaining:
            break
        t_end = seg[-1][0]
        mine = [d for d in remaining if seg[0][0] <= d <= t_end + pair_after]
        if not mine:
            continue
        for d in mine:
            remaining.remove(d)
        death = max(mine)
        seg[:] = [p for p in seg if p[0] <= death]
        if seg:
            seg[-1] = (death,) + seg[-1][1:]
    return len(remaining)


def dc_slot_deaths(series, resync_run=RESYNC_MIN_RUN):
    """[flags 2026-10-05b/c] 单槽减法口径：分段差分求和（对采样空洞免疫）。

    series: [(t, dc)] 升序（同槽，源窗内）。账本按"可信段"累计：
      步长 ∈ {0, +1} = 干净（观测空洞免疫——dc 单调计数器，段内首末即差分）；
      坏步（跳变 ≥2 或回退 <0）= 身份更替/类型错位/换绑复位，处理按前瞻：
        坏步后 +1 游程（0 步不打断）≥ resync_run → 坏步是身份边界，从坏步后
          新基线续计（新段）——
          跳变续计 = 同槽读断层累积（mini#2 e020：断层 +10，跳后 33 步全 +1）；
          回退续计 = 换绑复位后同地址干净爬升（TF180/1wall：carryover 基值
          掉 0 后整局 +N 全 +1，6 目标 22+18+17+17+18+14=106 严丝合缝）；
        游程不足 → 截断（trusted=False，前缀段照计）——全部幻影跳变
          （smoke1 dc=158 冻结、572、smoke2 0→126、mini#2 1→59）跳后游程=0，
          在全语料上零复活。
    deaths = Σ 各可信段（段末-段基）；trusted=False 表示存在未通过前瞻的坏步
    （untrusted_from=截断时刻），前缀段已计入。返回 dict 或 None（series 空）。"""
    if not series:
        return None
    n = len(series)
    t0, d0 = series[0]
    base = d0              # 当前段基线
    last_dc = d0           # 最近一次可信观测值
    last_t = t0
    deaths = 0
    trusted = True
    cut = None
    i = 1
    while i < n:
        t, dc = series[i]
        step = dc - last_dc
        if step in (0, 1):
            deaths += step
            last_dc, last_t = dc, t
            i += 1
            continue
        # 坏步：前瞻 +1 游程（0 步不打断游程计数，到下一坏步为止）
        run = 0
        j = i + 1
        while j < n:
            s2 = series[j][1] - series[j - 1][1]
            if s2 == 1:
                run += 1
                j += 1
            elif s2 == 0:
                j += 1
            else:
                break
        if run >= resync_run:
            # 身份边界：新段从坏步后观测重新起基（坏步帧本身弃读）。
            # 跳变重同步：跳变差值=观测断层期累积的真实死亡，回溯计入；
            # 回退重同步：计数器复位，新基线从 0 起算（差值为负不计入）。
            if step >= 2:
                deaths += step
            base = dc
            last_dc, last_t = dc, t
            i += 1
            continue
        # 前瞻失败：本异常判垃圾（池化换绑/类型错位，冻延续无账）——跳过其
        # 冻结延续到下一个坏步重评（典型三连：carryover 基值→垃圾尖峰→掉 0
        # →整局干净爬升；爬升段由下一个坏步的重同步回收）。
        trusted = False
        if cut is None:
            cut = t
        if j > i:
            last_dc, last_t = series[j - 1][1], series[j - 1][0]
            i = j
        else:
            i += 1
    return {"start_dc": d0, "end_dc": series[-1][1],
            "deaths": deaths,
            "trusted": trusted,
            "prefix_only": not trusted,
            "untrusted_from": round(cut, 4) if cut is not None else None,
            "first_t": round(t0, 4), "last_t": round(last_t, 4),
            "n_obs": len(series)}


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
def find_phantoms(frames, cleaned, cfg, cut_stats=None):
    """幽灵轨道：首帧(首个非空帧)即出现 + 出现帧占比>阈值 + 近零移动。

    机制：TargetHudComponent owner 扫描会把 CDO/预览体等非目标对象一并捞进来，
    它们从采样开始就存在、贯穿全部帧、几乎不动。
    [fix 2026-10-04] 窗口模式（epoch_window 非空）下，"真目标不可能贯穿局间空窗"的
    前提不成立：切窗把多局内容裁进同一段轨，上一局仍在场/池化复用的真目标（target_poll2
    校准注释即按此假设建模）会以同样的三联特征命中判据，整轨删除会让 assign_rounds
    拿到空目标 → 0 轮。故窗口模式对命中三联判据的轨道追加"真目标生命周期复核"，
    任一命中即保留（fail-open），返回 (phantoms, kept_ambiguous)；全程模式复核分支
    不进入、行为不变。判别原理——CDO/预览体是类默认对象，变换冻结、永不死亡：
      D1a 多段（len(lives)>1）或发生过跳变/二级重生切段：真目标被击杀必有切段，
          CDO 恒单段零切段；
      D1b 出现过原点残留点：真目标死亡时 RootComponent 读回 0 留原点残留
          （FORMAT §1.3.4），CDO 永不读原点；
      D2  bbox 净跨度 ≥ phantom_span_keep：慢速真目标也单调漂移（5 u/s × 60s = 300u），
          CDO 冻结跨度 ≈0。
    cut_stats：clean_file 的 per_addr_stats（addr → {jump_cuts, respawn2_cuts,
    origin_points, ...}），供 D1 判据取段统计；None 时判据退化为仅用 lives 形状。
    """
    total = len(frames)
    if total == 0:
        return {}, {}
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
    window_mode = cfg.get("epoch_window") is not None   # [fix 2026-10-04]
    span_keep = cfg.get("phantom_span_keep", PHANTOM_SPAN_KEEP)
    phantoms = {}
    kept_ambiguous = {}
    for a, lives in cleaned.items():
        if not lives:
            continue
        path = sum(s["path"] for s in (seg_stats(l) for l in lives))
        life = lives[-1][-1][0] - lives[0][0][0]
        speed = path / life if life > 0 else 0.0
        ratio = counts.get(a, 0) / total
        if not (a in first_addrs and ratio > cfg["phantom_ratio"] and speed < cfg["phantom_speed"]):
            continue
        info = {"presence_ratio": round(ratio, 4),
                "speed_ups": round(speed, 2), "path": round(path, 1)}
        if not window_mode:
            phantoms[a] = info
            continue
        # [fix 2026-10-04] 窗口模式真目标生命周期复核（fail-open：任一命中即保留）
        st = (cut_stats or {}).get(a, {})
        xs = [p[1] for seg in lives for p in seg]
        ys = [p[2] for seg in lives for p in seg]
        zs = [p[3] for seg in lives for p in seg]
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
        n_cuts = st.get("jump_cuts", 0) + st.get("respawn2_cuts", 0)
        reasons = []
        if len(lives) > 1 or n_cuts > 0:
            reasons.append("D1a")
        if st.get("origin_points", 0) > 0:
            reasons.append("D1b")
        if span >= span_keep:
            reasons.append("D2")
        if reasons:
            info.update({"n_lives": len(lives),
                         "jump_cuts": st.get("jump_cuts", 0),
                         "respawn2_cuts": st.get("respawn2_cuts", 0),
                         "origin_points": st.get("origin_points", 0),
                         "bbox_span": round(span, 1), "reason": "+".join(reasons)})
            kept_ambiguous[a] = info
        else:
            phantoms[a] = info
    return phantoms, kept_ambiguous


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
    frames, bad_recs, t0_map, deaths, dc_obs = load_frames(
        path, epoch_window=cfg.get("epoch_window"))
    # [flags 2026-10-05] death 行 → per-addr 权威死亡时刻。有 death 行才启用标志
    # 路径（t_end 权威 + death_event 标记）；无 death 行（旧文件）零影响。
    deaths_by_addr = {}
    for d in deaths:
        deaths_by_addr.setdefault(d["addr"], []).append(d["t"])
    flag_path = bool(deaths)
    tracks = build_tracks(frames)

    discarded = {
        "malformed_records": bad_recs,
        "phantom_tracks": {},     # 幽灵轨道（HUD 扫描误捞的非目标对象）
        "origin_ghost_tracks": {},  # 全程只有原点读数的轨道（死亡残留/CDO）
        "static_ghost_tracks": {},  # 寿命<2s 且无移动（任务规则）
        "low_valid_tracks": {},   # [flags 2026-10-05] 门3：valid-ratio 过低的池化幽灵轨
        "garbage_points": 0,      # NaN/出界/原点点数合计
        "noise_segments": 0,
        "short_segments": 0,      # [flags 2026-10-05] 门2：时长不足微段数
    }

    cleaned = {}       # addr -> [life_seg,...]
    per_addr_stats = {}
    n_death_unpaired = 0
    for a, pts in tracks.items():
        lives, st = split_track(pts, cfg)
        per_addr_stats[a] = st
        discarded["garbage_points"] += st["nan_or_bound_points"] + st["origin_points"]
        discarded["noise_segments"] += st["noise_segments"]
        discarded["short_segments"] += st["short_segments"]
        if flag_path and deaths_by_addr.get(a):
            # [flags 2026-10-05] 标志路径：权威边界改写（坐标垃圾段由此降级为
            # 普通坏点——死亡后的复读帧/坏点不再决定 life 形状）
            un = bind_deaths(lives, deaths_by_addr[a],
                             pair_after=float(cfg.get("death_pair_after",
                                                      DEATH_PAIR_AFTER)))
            n_death_unpaired += un
            st["death_events_bound"] = len(deaths_by_addr[a]) - un
            st["death_events_unpaired"] = un
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

    # [flags 2026-10-05] 三道门之三：轨道级 valid-ratio。有效点占比过低且段数多
    # = 池化幽灵复读已释放内存（54078 tid15：valid 2331/13046=18%、84 段）；
    # 真目标即便多死 valid 占比也 >80%。整轨丢弃入审计。
    for a in list(cleaned):
        st = per_addr_stats[a]
        total = len(tracks[a])
        valid = total - st["nan_or_bound_points"] - st["origin_points"]
        ratio = (valid / total) if total else 0.0
        if total and ratio < float(cfg.get("track_valid_ratio", TRACK_VALID_RATIO)) \
                and len(cleaned[a]) >= int(cfg.get("track_min_segments",
                                                   TRACK_MIN_SEGMENTS)):
            discarded["low_valid_tracks"][fmt_addr(a)] = {
                "valid_ratio": round(ratio, 4), "valid_points": valid,
                "total_points": total, "n_lives": len(cleaned[a])}
            del cleaned[a]

    # 幽灵轨道 → 丢；[fix 2026-10-04] 窗口模式下生命周期复核存疑的轨道保留并留审计痕
    phantoms, kept_ambiguous = find_phantoms(frames, cleaned, cfg, cut_stats=per_addr_stats)
    for a, info in phantoms.items():
        info["addr"] = fmt_addr(a)
        discarded["phantom_tracks"][fmt_addr(a)] = info
        cleaned.pop(a, None)
    # [fix 2026-10-04] 幽灵闸审计块：kept_ambiguous 是"命中幽灵三联判据、但被真目标生命周期
    # 复核保留"的轨道。保留轨不是丢弃，但闸决策属于丢弃报表语义，故挂在 discarded 下自描述；
    # webapp ingest 侧把 discarded 逐字透传进 meta.quality.discarded，诊断链路零改动即可读。
    discarded["phantom_gate"] = {
        "mode": "window" if cfg.get("epoch_window") is not None else "full",
        "rule_version": 2,
        "kept_ambiguous": {fmt_addr(a): info for a, info in kept_ambiguous.items()},
    }

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
            tm = {
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
                "domain": {"x": [round(min(d[0] for d in xs), 1), round(max(d[1] for d in xs), 1)],
                           "y": [round(min(d[2] for d in xs), 1), round(max(d[3] for d in xs), 1)],
                           "z": [round(min(d[4] for d in xs), 1), round(max(d[5] for d in xs), 1)]},
            }
            if flag_path:
                # [flags 2026-10-05] 标志路径：逐 life 标记 t_end 是否由 death 行
                # 权威改写（False = 坐标推导边界：出窗/局末清场/无标志家族）。
                # 旧文件（无 death 行）不写该键，lives 条目 schema 逐字节不变。
                dmom = deaths_by_addr.get(a, ())
                tm["lives"] = [
                    {"t_start": round(s["t_start"], 4), "t_end": round(s["t_end"], 4),
                     "n": s["n"], "path": round(s["path"], 1),
                     "death_event": any(abs(d - s["t_end"]) < 1e-6 for d in dmom)}
                    for s in segs]
            else:
                tm["lives"] = [{"t_start": round(s["t_start"], 4), "t_end": round(s["t_end"], 4),
                                "n": s["n"], "path": round(s["path"], 1)} for s in segs]
            if a in kept_ambiguous:
                # [fix 2026-10-04] 命中幽灵三联判据但被窗口模式生命周期复核保留的目标打标
                tm["phantom_ambiguous"] = True
            tmeta.append(tm)

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


    # [fix 2026-10-04] 观测计数（additive）：供报障包定罪 H1——poll 全程看不到目标时
    # frames_total>0 而 frames_with_targets==0。"首帧"取首个非空帧，与幽灵判据同义。
    n_frames_with_targets = sum(1 for fr in frames if fr["targets"])
    first_targets = next((fr["targets"] for fr in frames if fr["targets"]), [])
    src_meta = {
        "source": os.path.basename(path),
        "outdir": name,
        "t_min": round(frames[0]["t"], 4) if frames else None,
        "t_max": round(frames[-1]["t"], 4) if frames else None,
        "frames_total": len(frames),
        "frames_with_targets": n_frames_with_targets,
        "first_frame_target_count": len(first_targets),
        "n_rounds": len(index_rounds),
        "rounds": index_rounds,
        "discarded": discarded,
        "per_addr_cut_stats": {fmt_addr(a): st for a, st in per_addr_stats.items()},
    }
    if t0_map is not None:
        # [v2.1] 精确 epoch 锚（录制器首行 clock_map）：epoch(t) = t0_epoch + t，
        # 替代下游"文件名墙钟 ±1s"粗锚（merge_channels / AC 导入侧直接消费）
        src_meta["t0_epoch"] = round(float(t0_map["t"]), 4)
        # [flags 2026-10-05] 死亡信号来源与降级可观测（additive）：
        #   deaths_source "flag"=death 行权威边界 / "coords"=坐标推导（旧文件）；
        #   flag_reflection 透传采集器 clock_map 的 ok/degraded/unavailable。
        src_meta["deaths_source"] = "flag" if flag_path else "coords"
        src_meta["n_death_events"] = len(deaths)
        src_meta["n_death_events_unpaired"] = n_death_unpaired
        if isinstance(t0_map.get("flag_reflection"), str):
            src_meta["flag_reflection"] = t0_map["flag_reflection"]
        # [flags 2026-10-05b] deaths_summary（减法口径，源级）：每槽死亡总数 =
        # 源窗起止 dc 差分（观测源 = 帧条目 dc 列 + ev=="flag" 坐标缺席帧补采，
        # 对采样空洞免疫）。轮切分是出生聚类、会 challenges 内碎裂，dc 却跨轮
        # 连续——按轮做差分再求和会丢轮间空隙的账，故挂在源级（切窗流程下一
        # 源=一局）。只计 clean-prefix 可信槽位（点点合同「官方 85 我们就 85」）；
        # death 行仍负责 life 边界与时刻，不参与总数。无 dc 观测（旧文件）不写键。
        if dc_obs:
            slots = {}
            total = 0
            for a, series in dc_obs.items():
                info = dc_slot_deaths(series)
                if info is None:
                    continue
                slots[fmt_addr(a)] = info
                total += info["deaths"]
            src_meta["deaths_summary"] = {
                "method": "dc_subtraction",
                "window": [round(frames[0]["t"], 4) if frames else None,
                           round(frames[-1]["t"], 4) if frames else None],
                "note": "每槽死亡总数=源窗起止 dc 差分（clean-prefix：换绑/跨身份"
                        "累计步截断，前缀仍是可信账本）；death 行不参与总数",
                "total": total,
                "n_slots": len(slots),
                "n_slots_tainted": sum(1 for s in slots.values() if not s["trusted"]),
                "slots": slots,
            }
            src_meta["deaths_total"] = total   # 便捷别名（= deaths_summary.total）
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
    ap.add_argument("--epoch-min", type=float, default=None,
                    help="按局增量切窗：只保留 epoch(t)≥该值的帧（绝对纪元秒）")
    ap.add_argument("--epoch-max", type=float, default=None,
                    help="按局增量切窗：只保留 epoch(t)≤该值的帧（绝对纪元秒）")
    args = ap.parse_args()
    if (args.epoch_min is None) != (args.epoch_max is None):
        ap.error("--epoch-min 与 --epoch-max 必须成对使用")
    outdir = args.outdir or os.path.join(os.path.dirname(os.path.abspath(args.inputs[0])), "cleaned")
    os.makedirs(outdir, exist_ok=True)
    cfg = {"jump_dist": args.jump_dist, "jump_speed": args.jump_speed, "bound": args.bound,
           "origin_eps": ORIGIN_EPS, "min_life": args.min_life, "min_move": MIN_MOVE,
           "min_samples": MIN_SAMPLES, "birth_gap": args.birth_gap,
           "dead_gap": args.dead_gap, "phantom_ratio": PHANTOM_RATIO,
           "phantom_speed": PHANTOM_SPEED,
           # [fix 2026-10-04] 窗口模式幽灵豁免跨度阈值（additive 随 params 入 index；
           # format_version 保持 1 不动——ingest 对版本 fail-closed）
           "phantom_span_keep": PHANTOM_SPAN_KEEP,
           # [fix 2026-08-30] 二级短距重生判据（常量，未开 CLI；见文件头 2b 与 cleaner_fix_0830.md）
           "respawn2_speed": RESPAWN2_SPEED, "respawn2_dist": RESPAWN2_DIST,
           "lane_cos": LANE_COS, "ambient_static_speed": AMBIENT_STATIC_SPEED,
           "respawn2_static_dist": RESPAWN2_STATIC_DIST,
           # [flags 2026-10-05] 三道门 + death 行配对窗（常量，未开 CLI；阈值来源
           # 见常量块注释，标定数据 .zcode/anchor-research-1005/REPORT.md）
           "bad_streak_cut": BAD_STREAK_CUT, "min_seg_dur": MIN_SEG_DUR,
           "track_valid_ratio": TRACK_VALID_RATIO,
           "track_min_segments": TRACK_MIN_SEGMENTS,
           "death_pair_after": DEATH_PAIR_AFTER,
           "epoch_window": (args.epoch_min, args.epoch_max)
           if args.epoch_min is not None else None}
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
