# -*- coding: utf-8 -*-
"""test_death_flags.py — 目标死亡标志位遥测离线自测（不需要游戏运行）。

[flags 2026-10-05] 覆盖两段：
  A. target_poll2 侧死亡标志状态机（纯逻辑直测）：
     sanitize_flags 错读防护①（hp 有限性/非负、dc 非负）+
     update_flag_state（dc 增量主判据 / hp 连续 2 帧==0 次判据 / dc 单调回退
     弃标志 / hp 复活重新武装 / 首帧播种不触发）。错读防护③（指针有效性）
     由读失败→None 与既有 serial 检查兜底，test_poll_perf 的采样循环全链覆盖。
  B. cleaner 侧 death 行消费 + 三道门（合成 JSONL 喂真实 clean_file）：
     life t_end 取 death 行权威边界、death_event 标记、deaths_source、
     单点坏点迟滞、段最短时长门、轨道 valid-ratio 门、旧文件回退路径零影响。
  C. deaths_summary 减法口径（[flags 2026-10-05b]）。
  D. [lives 2026-10-05d] 死亡账本主导生命窗重组：多段并一、局末存活、
     开局残留甄别（切窗 carryover / 全文件不甄别）、无步证伪迹甄别、
     无死亡 addr 回退坐标切分、有账本无行并回单生命、单段单死亡、
     旧数据无重组审计键。

风格同 test_poll_perf.py：无 pytest 依赖，python test_death_flags.py 直跑；
test_* 函数无参，也可被 pytest 收集。
"""
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                      # import target_poll2（同目录模块）
sys.path.insert(0, os.path.dirname(_HERE))     # from telemetry_capture import cleaner

from telemetry_capture import cleaner  # noqa: E402
import target_poll2 as tp2             # noqa: E402

ADDR = 0x7FF600000001
T0 = 1790000000.0
CHR_LF = chr(10)   # 换行（避免源码内嵌转义字面量）
DT = 0.02          # 50Hz 合成步长


# ---------------- 公共小件 ----------------

def make_cfg():
    """与 cleaner.main() 的 cfg 同构（同 test_cleaner_window_phantom.make_cfg，
    另含 [flags 2026-10-05] 三道门/配对窗常量）。"""
    return {"jump_dist": cleaner.JUMP_DIST, "jump_speed": cleaner.JUMP_SPEED,
            "bound": cleaner.BOUND, "origin_eps": cleaner.ORIGIN_EPS,
            "min_life": cleaner.MIN_LIFE, "min_move": cleaner.MIN_MOVE,
            "min_samples": cleaner.MIN_SAMPLES, "birth_gap": cleaner.BIRTH_GAP,
            "dead_gap": cleaner.DEAD_GAP, "phantom_ratio": cleaner.PHANTOM_RATIO,
            "phantom_speed": cleaner.PHANTOM_SPEED,
            "phantom_span_keep": cleaner.PHANTOM_SPAN_KEEP,
            "respawn2_speed": cleaner.RESPAWN2_SPEED,
            "respawn2_dist": cleaner.RESPAWN2_DIST,
            "lane_cos": cleaner.LANE_COS,
            "ambient_static_speed": cleaner.AMBIENT_STATIC_SPEED,
            "respawn2_static_dist": cleaner.RESPAWN2_STATIC_DIST,
            "epoch_window": None,
            "bad_streak_cut": cleaner.BAD_STREAK_CUT,
            "min_seg_dur": cleaner.MIN_SEG_DUR,
            "track_valid_ratio": cleaner.TRACK_VALID_RATIO,
            "track_min_segments": cleaner.TRACK_MIN_SEGMENTS,
            "death_pair_after": cleaner.DEATH_PAIR_AFTER}


def write_jsonl(path, frames, deaths=(), flag_reflection=None):
    """frames: [(t, [[addr,x,y,z,(hp,dc)],...]), ...]；deaths: [(t,addr,dc)]。"""
    cm = {"ev": "clock_map", "t": T0}
    if flag_reflection:
        cm["flag_reflection"] = flag_reflection
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(cm) + "\n")
        for t, targets in frames:
            f.write(json.dumps({"ev": "frame", "t": t, "targets": targets}) + "\n")
        for t, addr, dc in deaths:
            f.write(json.dumps({"ev": "death", "t": t, "addr": addr, "dc": dc}) + "\n")


def clean_tmp(tmp_path, frames, deaths=(), epoch_window=None, name="synth"):
    path = os.path.join(tmp_path, name + ".jsonl")
    write_jsonl(path, frames, deaths)
    outdir = os.path.join(tmp_path, "out")
    cfg = make_cfg()
    cfg["epoch_window"] = epoch_window
    return cleaner.clean_file(path, outdir, cfg)


def run_frames(duration, pos_at, targets_at=None):
    """duration 秒、50Hz 帧流；pos_at(t) → [x,y,z] 或 None（该帧无此目标）。"""
    frames = []
    t = 0.0
    while t <= duration + 1e-9:
        ents = []
        p = pos_at(t)
        if p is not None:
            ents.append([ADDR, p[0], p[1], p[2]])
        if targets_at:
            ents.extend(targets_at(t))
        frames.append((round(t, 6), ents))
        t = round(t + DT, 6)
    return frames


# ---------------- A. 采集侧状态机 ----------------

def test_sanitize_flags_gates():
    # hp：NaN/Inf/负/超域 → None；正常值透传
    assert tp2.sanitize_flags(float("nan"), 3) == (None, 3)
    assert tp2.sanitize_flags(float("inf"), 3) == (None, 3)
    assert tp2.sanitize_flags(-0.5, 3) == (None, 3)
    assert tp2.sanitize_flags(1e7, 3) == (None, 3)
    assert tp2.sanitize_flags(54.0, 3) == (54.0, 3)
    assert tp2.sanitize_flags(0.0, 3) == (0.0, 3)
    # dc：负值 → None；读失败 None 透传
    assert tp2.sanitize_flags(1.0, -5) == (1.0, None)
    assert tp2.sanitize_flags(1.0, None) == (1.0, None)
    # [flags 2026-10-05c] dc 上限门：float 比特错位（1.0f=0x3F800000、448.0f）
    # 读 i32 的垃圾值 → None（不可信冻结，mini#2 语料实证）
    assert tp2.sanitize_flags(1.0, 1065353216) == (1.0, None)
    assert tp2.sanitize_flags(1.0, 1135869952) == (1.0, None)
    assert tp2.sanitize_flags(1.0, 10 ** 6) == (1.0, 10 ** 6)      # 边界内放行
    assert tp2.sanitize_flags(1.0, 10 ** 6 + 1) == (1.0, None)


def test_update_flag_state_dc_edge_primary():
    fs = {}
    # 首帧播种：不触发（防附着前旧值当边沿）
    assert tp2.update_flag_state(fs, 1, 1.0, 137) == (1.0, 137, False)
    # 主判据：dc 增量 → death
    assert tp2.update_flag_state(fs, 1, 0.0, 138) == (0.0, 138, True)
    # hp 保持 0 不重复触发（fired 门）
    assert tp2.update_flag_state(fs, 1, 0.0, 138) == (0.0, 138, False)
    # hp 复活 → 重新武装
    assert tp2.update_flag_state(fs, 1, 1.0, 138) == (1.0, 138, False)
    # 重生后 hp 单帧 0（瞬态）不触发
    assert tp2.update_flag_state(fs, 1, 0.0, 138) == (0.0, 138, False)
    # 连续第 2 帧 hp=0：dc 无增量，次判据触发
    assert tp2.update_flag_state(fs, 1, 0.0, 138) == (0.0, 138, True)


def test_update_flag_state_dc_regression_drops():
    fs = {}
    tp2.update_flag_state(fs, 1, 1.0, 100)
    # dc 回退 + hp 可信 = 计数器复位（换图/重开）：重新播种基线，不当死亡
    assert tp2.update_flag_state(fs, 1, 1.0, 42) == (1.0, 42, False)
    assert fs[1][0] == 42
    # dc 回退 + hp 不可信（None）= 自由内存复读：本帧标志整体弃用，状态冻结
    tp2.update_flag_state(fs, 1, 1.0, 50)
    assert tp2.update_flag_state(fs, 1, None, 7) == (None, None, False)
    assert fs[1][0] == 50
    # 恢复到 prev 值：无回退、无边沿
    assert tp2.update_flag_state(fs, 1, 1.0, 50) == (1.0, 50, False)
    # 无标志帧（读失败）状态不动
    assert tp2.update_flag_state(fs, 1, None, None) == (None, None, False)
    assert fs[1][0] == 50


def test_update_flag_state_dc_jump_rebaselines_not_fires():
    """1005 冒烟幻影（半路发现的池化对象 dc 3→158）：跳变>1 重播种不当死亡。"""
    fs = {}
    tp2.update_flag_state(fs, 1, 1.0, 3)
    assert tp2.update_flag_state(fs, 1, 1.0, 158) == (1.0, 158, False)
    assert fs[1][0] == 158
    # 重播种后新身份的真实死亡照常触发
    assert tp2.update_flag_state(fs, 1, 0.0, 159) == (0.0, 159, True)


def test_update_flag_state_first_frame_zero_primes_not_fires():
    fs = {}
    # 新附着目标首帧 hp=0：播种 streak=1，不触发
    assert tp2.update_flag_state(fs, 1, 0.0, 7) == (0.0, 7, False)
    # 第 2 帧仍 0：次判据成立
    assert tp2.update_flag_state(fs, 1, 0.0, 7) == (0.0, 7, True)


# ---------------- B. cleaner：death 行权威边界 + 三道门 ----------------

def _moving(t):
    """300 u/s 匀速滑行（> 幽灵速度上限 10，避开 phantom 判据）。"""
    return [100.0 + 300.0 * t, 500.0, 100.0]


def _respawned(t):
    """重生位置（+2000 u 平移）：与 t≤2 的轨距 >2000 → 重生跳切段；
    全程 |x|<8192 留在场景域内。"""
    return [2100.0 + 300.0 * t, 500.0, 100.0]


def test_death_row_authoritative_t_end():
    """life 的 t_end 取 death 行（权威），死亡残留原点/坏点不再决定边界。"""
    frames = run_frames(5.0, _moving)
    # 死亡帧起 3 帧原点残留 + 2 帧出界坏点（坐标垃圾全部降级为普通坏点）
    for k in range(3):
        frames.append((round(5.02 + k * DT, 6), [[ADDR, 0.0, 0.0, 0.0]]))
    frames.append((5.10, [[ADDR, float("nan"), 1e9, 0.0]]))
    frames.append((5.12, [[ADDR, float("nan"), 1e9, 0.0]]))
    deaths = [(5.0, ADDR, 1)]
    src = clean_tmp(tempfile.mkdtemp(prefix="df_auth_"), frames, deaths)
    assert src["deaths_source"] == "flag"
    assert src["n_death_events"] == 1 and src["n_death_events_unpaired"] == 0
    tm = src["rounds"][0]["targets"][0]
    assert len(tm["lives"]) == 1
    lv = tm["lives"][0]
    assert lv["t_end"] == 5.0, lv            # 权威边界=death 行 t
    assert lv["death_event"] is True
    # t_end 之后的复读点已被裁掉：n = 5.0s 内的有效点数（50Hz → 251 点）
    assert lv["n"] == 251, lv


def test_death_unpaired_during_gap_audited():
    """死亡落在目标缺席（无坐标条目）的间隙且超出配对窗 → unpaired 审计，
    其他 life 边界不受影响（保持坐标推导 + death_event=False）。"""
    def pos(t):
        if t < 2.0:
            return _moving(t)
        if t < 3.5:                       # 目标完全缺席（出视野/漏采）
            return None
        return _respawned(t)              # 重现：位置跳变切段
    frames = run_frames(4.0, pos)
    deaths = [(3.0, ADDR, 1)]             # 死亡发生在缺席间隙中部
    src = clean_tmp(tempfile.mkdtemp(prefix="df_unp_"), frames, deaths)
    assert src["n_death_events"] == 1
    assert src["n_death_events_unpaired"] == 1
    tm = src["rounds"][0]["targets"][0]
    assert all(lv["death_event"] is False for lv in tm["lives"])


def test_gate1_single_bad_point_no_cut():
    """门1 迟滞：单个原点/坏点夹在有效点之间不切段。"""
    def pos(t):
        if abs(t - 2.0) < 1e-6:
            return [0.0, 0.0, 0.0]        # 单点原点
        if abs(t - 3.0) < 1e-6:
            return [float("nan"), float("nan"), float("nan")]   # 单点 NaN
        return _moving(t)
    frames = run_frames(4.0, pos)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_g1_"), frames)
    tm = src["rounds"][0]["targets"][0]
    assert tm["n_lives"] == 1, tm["lives"]       # 旧行为：单点即切 → 3 段
    st = src["per_addr_cut_stats"]["0x7ff600000001"]
    assert st["origin_points"] == 1 and st["nan_or_bound_points"] == 1


def test_gate1_two_consecutive_bad_points_cut():
    """门1 迟滞：连续 ≥2 坏点仍切段（真实死亡残留多帧，语义保持）。"""
    def pos(t):
        if 2.0 <= t <= 2.04:
            return [0.0, 0.0, 0.0]        # 连续 3 帧原点
        return _respawned(t)              # 重生跳（>2000 → 切）
    frames = run_frames(4.0, pos)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_g1b_"), frames)
    # 连续 3 帧原点仍切段；段间全灭 0.08s > dead_gap 会再切轮，跨轮统计
    assert sum(tm["n_lives"] for r in src["rounds"]
               for tm in r["targets"]) == 2


def test_gate2_min_segment_duration():
    """门2：时长 <0.15s 的微段丢弃（旧 MIN_SAMPLES=3 在 50Hz 下会保留 4 帧=0.06s 段）。"""
    def pos(t):
        if 2.0 <= t <= 2.06:              # 4 帧=0.06s 的"闪烁"段（重生跳隔开）
            return _respawned(t)
        if 2.08 <= t <= 2.16:             # 连续坏点（切段 + 隔开）
            return [0.0, 0.0, 0.0]
        return _moving(t)
    frames = run_frames(4.0, pos)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_g2_"), frames)
    # 主轨前后两段保留（轮切不合并计数），闪烁微段被门2 丢弃
    assert sum(tm["n_lives"] for r in src["rounds"]
               for tm in r["targets"]) == 2
    assert src["discarded"]["short_segments"] == 1


def test_gate3_track_valid_ratio():
    """门3：valid 占比 <40% 且段数 ≥3 的池化幽灵轨整轨丢弃入审计。
    周期 23 帧=0.46s：9 帧有效段（0.16s，过门2）+ 14 帧原点 → 占比 39.1%。"""
    def targets_at(t):
        out = []
        if int(round(t / DT)) % 23 < 9:
            out.append([0x7FF600000002, 2000.0 + 5.0 * t, 800.0, 100.0])
        else:
            out.append([0x7FF600000002, 0.0, 0.0, 0.0])
        return out
    frames = run_frames(30.0, _moving, targets_at)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_g3_"), frames)
    hex2 = "0x7ff600000002"
    assert hex2 in src["discarded"]["low_valid_tracks"], src["discarded"]
    info = src["discarded"]["low_valid_tracks"][hex2]
    assert info["valid_ratio"] < 0.40
    assert info["n_lives"] >= 3
    assert all(tm["addr"] != 0x7FF600000002
               for r in src["rounds"] for tm in r["targets"])


def test_legacy_file_without_deaths_unchanged():
    """回退路径：无 death 行 → 坐标推导现行为，无 death_event/deaths_source 新语义。"""
    def pos(t):
        if 2.0 <= t <= 2.04:
            return [0.0, 0.0, 0.0]        # 连续坏点切段（现行为）
        return _respawned(t)
    frames = run_frames(4.0, pos)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_leg_"), frames, deaths=())
    assert src["deaths_source"] == "coords"
    assert src["n_death_events"] == 0
    assert sum(tm["n_lives"] for r in src["rounds"]
               for tm in r["targets"]) == 2
    for r in src["rounds"]:
        for tm in r["targets"]:
            for lv in tm["lives"]:
                assert "death_event" not in lv    # 旧 schema 逐字节保持
    assert "n_death_events_unpaired" in src   # additive 元数据存在但计数为 0


def test_death_rows_respect_epoch_window():
    """epoch_window 切窗时窗外的 death 行被过滤 → 退回坐标路径（fail-closed 一致）。"""
    frames = run_frames(4.0, _moving)
    deaths = [(1.0, ADDR, 1)]
    # 窗 [T0+2, T0+8] 只含 t≥2 的帧；death@1.0 在窗外
    src = clean_tmp(tempfile.mkdtemp(prefix="df_win_"), frames, deaths,
                    epoch_window=(T0 + 2.0, T0 + 8.0))
    assert src["n_death_events"] == 0
    assert src["deaths_source"] == "coords"


def test_frame_hp_dc_columns_tolerated():
    """6 列帧条目（hp/dc 列）被 cleaner 正常消费（只取 e[0..3]），回退语义不炸。"""
    frames = []
    t = 0.0
    while t <= 2.0 + 1e-9:
        frames.append((round(t, 6), [[ADDR, 100.0 + 300.0 * t, 500.0, 100.0,
                                      54.0 if t < 1.0 else 0.0, 1]]))
        t = round(t + DT, 6)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_cols_"), frames)
    tm = src["rounds"][0]["targets"][0]
    assert tm["n_lives"] == 1
    assert src["deaths_source"] == "coords"   # 无 death 行 → 坐标路径


# ---------------- C. deaths_summary 减法口径（[flags 2026-10-05b]） ----------------

def test_dc_slot_deaths_trust_rule():
    f = cleaner.dc_slot_deaths
    # 空 → None
    assert f([]) is None
    # 干净 +1 步进 → trusted，deaths=差分
    r = f([(0.0, 0), (0.1, 1), (0.2, 2), (0.3, 3)])
    assert r["trusted"] and r["deaths"] == 3
    assert r["start_dc"] == 0 and r["end_dc"] == 3
    # 恒定 → trusted，0 死
    r = f([(0.0, 158), (1.0, 158)])
    assert r["trusted"] and r["deaths"] == 0
    # 首步即跳变（dc 0→7）→ clean-prefix=0，deaths=0、trusted=False
    r = f([(0.0, 0), (0.3, 126)])
    assert not r["trusted"] and r["deaths"] == 0 and r["prefix_only"]
    # 中段跳变 → clean-prefix 截断：前缀仍计入（换绑前的账本可信）
    r = f([(0.0, 5), (0.1, 6), (0.2, 7), (0.3, 19), (0.4, 572)])
    assert not r["trusted"] and r["prefix_only"]
    assert r["deaths"] == 2 and r["untrusted_from"] == 0.3
    # 回退 = 计数器复位/换绑 → 截断
    r = f([(0.0, 10), (0.1, 11), (0.2, 3)])
    assert not r["trusted"] and r["deaths"] == 1 and r["prefix_only"]


def test_dc_slot_deaths_anomaly_resync():
    """[flags 2026-10-05c] 异常重同步：坏步后 ≥3 步干净 +1 → 回溯信任。
    mini#2 e020 实证形态：观测断层 11.2s，dc 9→19（断层期累积 10 死），
    跳后 33 步全 +1 —— 跳变差值回溯计入。"""
    f = cleaner.dc_slot_deaths
    # 跳变 + 回溯：deaths = 跳变差值 + 跳后 +1 步
    r = f([(0.0, 9), (11.2, 19), (11.3, 20), (11.4, 21), (11.5, 22)])
    assert r["trusted"] and r["deaths"] == 13, r     # 10（断层）+ 3
    assert not r["prefix_only"]
    # drop 重同步（1wall/TF180 实证形态）：carryover 基值掉 0 后整局干净爬升
    r = f([(0.0, 202), (0.5, 0), (0.6, 1), (0.7, 2), (0.8, 3)])
    assert r["trusted"] and r["deaths"] == 3, r      # 复位差值为负不计入
    # 跳后游程不足（<3）→ 保守截断
    r = f([(0.0, 0), (0.1, 10), (0.2, 11), (0.3, 12)])
    assert not r["trusted"] and r["deaths"] == 0
    # 尾部跳变无前瞻观测 → 截断
    r = f([(0.0, 9), (5.0, 19)])
    assert not r["trusted"] and r["deaths"] == 0
    # 幻影零复活（约束：smoke1 dc=158 不得在任何局复活计入）：
    # 跳后冻结（游程 0）→ 截断 0；恒值序列 → 0
    r = f([(0.0, 0), (0.1, 158), (0.2, 158), (0.3, 158)])
    assert not r["trusted"] and r["deaths"] == 0
    r = f([(0.0, 158), (1.0, 158)])
    assert r["trusted"] and r["deaths"] == 0
    # smoke2 0→126 首步跳变、无后继 → 0
    r = f([(0.0, 0), (0.3, 126)])
    assert not r["trusted"] and r["deaths"] == 0
    # 三连形态（TF180/200%#1 实证）：carryover 基值 → 垃圾尖峰 → 掉 0 →
    # 整局干净爬升。垃圾尖峰前瞻（遇到回退）失败 → 跳过冻结区；掉 0 的
    # 回退重同步回收爬升段；carryover 前缀（另一身份）不计入。
    r = f([(0.0, 108), (0.1, 572), (0.2, 0), (0.3, 1), (0.4, 2),
           (0.5, 3), (0.6, 3), (0.7, 4)])
    assert not r["trusted"] and r["deaths"] == 4, r   # 爬升段 4，尖峰前缀不计
    # 双坏步全失败（垃圾槽）→ 0
    r = f([(0.0, 199), (0.1, 100), (0.2, 20)])
    assert not r["trusted"] and r["deaths"] == 0


def test_sanitize_dc_max_in_dc_obs():
    """cleaner 侧 DC_MAX：账本丢弃超限观测（帧条目与 flag 行同门）。"""
    A, B = 0x7FF600000001, 0x7FF600000002
    frames = []
    t = 0.0
    while t <= 3.0 + 1e-9:
        ents = []
        # A：正常 +1 序列，中途 1 帧垃圾 dc=1065353216（应被丢弃，账本不断）
        dcv = int(t * 10)
        if abs(t - 1.5) < 1e-6:
            dcv = 1065353216
        ents.append([A, 100.0 + 300.0 * t, 500.0, 100.0, 54.0, dcv])
        # B：全垃圾 dc（>DC_MAX）→ 槽位无账本
        ents.append([B, 2000.0, 800.0, 100.0, 54.0, 1135869952])
        frames.append((round(t, 6), ents))
        t = round(t + DT, 6)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_dcmax_"), frames)
    ds = src["deaths_summary"]
    sa = ds["slots"]["0x%x" % A]
    assert sa["trusted"] and sa["deaths"] == 30, sa   # 垃圾帧剔除后步进连续
    assert "0x%x" % B not in ds["slots"]               # 全垃圾槽无账本


def test_deaths_summary_subtraction_end_to_end():
    """减法口径：每槽 deaths = 轮窗起止 dc 差分；跳变槽 untrusted 不计入 total；
    采样空洞（flag 行补采/稀疏条目）不影响差分。"""
    A, B, C = 0x7FF600000001, 0x7FF600000002, 0x7FF600000003
    frames, deaths = [], []
    t = 0.0
    dc_a = 0
    while t <= 10.0 + 1e-9:
        ents = []
        if t >= 0.5:   # 槽 A：300u/s 滑行 + 每秒死 1（dc 0→10，含 2s 观测空洞）
            ents.append([A, 100.0 + 300.0 * t, 500.0, 100.0, 54.0, dc_a])
        if t >= 0.5 and abs((t * 50) % 25) < 1e-6:
            pass
        ents.append([B, 2000.0 + t, 800.0, 100.0, 54.0, 0])   # 恒 dc=0
        # 槽 C：窗内稀疏残留（全原点坐标），dc 跳变（0→7 一次跳），untrusted
        if 1.0 <= t <= 1.3:
            ents.append([C, 0.0, 0.0, 0.0, 0.0, 7 if t >= 1.2 else 0])
        frames.append((round(t, 6), ents))
        t = round(t + DT, 6)
    for k in range(10):   # 10 条 death 行（仅作 life 边界，不参与总数）
        deaths.append((1.0 + k, A, k + 1))
    # 槽 A 的 dc 序列：dc_a 随 death 行同步 +1（模拟 dc 在场推进）
    frames2 = []
    for t2, ents2 in frames:
        e2 = []
        for e in ents2:
            if e[0] == A:
                k = min(10, max(0, int(t2 - 1.0) + 1)) if t2 >= 1.0 else 0
                e = [A, e[1], e[2], e[3], 54.0, k]
            e2.append(e)
        frames2.append((t2, e2))
    src = clean_tmp(tempfile.mkdtemp(prefix="df_sum_"), frames2, deaths)
    ds = src["deaths_summary"]
    assert ds["method"] == "dc_subtraction"
    hexa, hexb, hexc = "0x%x" % A, "0x%x" % B, "0x%x" % C
    sa = ds["slots"][hexa]
    assert sa["trusted"] and sa["deaths"] == 10, sa   # 0→10，空洞免疫
    assert ds["slots"][hexb]["deaths"] == 0            # 恒 0：trusted、0 死
    assert not ds["slots"][hexc]["trusted"]            # 跳变槽：clean-prefix=0
    assert ds["slots"][hexc]["deaths"] == 0
    assert ds["total"] == 10
    assert src["deaths_total"] == 10


def test_deaths_summary_absent_for_legacy_file():
    """旧文件（无 dc 观测）不写 deaths_summary 键——54075 现行为不变。"""
    frames = run_frames(3.0, _moving)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_nosum_"), frames)
    assert "deaths_summary" not in src
    assert "deaths_total" not in src


def test_flag_rows_fill_dc_holes():
    """ev=="flag" 行（坐标缺席帧的 dc 补采）进入减法账本：
    条目缺席时段的 dc 差分照样计入（覆盖修的验收面）。"""
    A = 0x7FF600000001
    frames, flags = [], []
    t = 0.0

    def ndead(tt):          # 每 1s 一死：t≥k 后 dc=k（自洽账本）
        return int(tt) if tt >= 1.0 else 0

    while t <= 6.0 + 1e-9:
        if 2.0 < t < 4.0:
            # 目标完全缺席（出视野）：poller 走 flag 行补 dc 观测
            if abs((t * 50) % 25) < 1e-6:      # 每 0.5s 一条 flag 行
                flags.append((round(t, 6), A, ndead(t)))
        else:
            frames.append((round(t, 6), [[A, 100.0 + 300.0 * t, 500.0, 100.0,
                                          54.0, ndead(t)]]))
        t = round(t + DT, 6)
    path = os.path.join(tempfile.mkdtemp(prefix="df_flag_"), "synth.jsonl")
    write_jsonl(path, frames,
                deaths=[(k, A, k) for k in range(1, 7)])
    with open(path, "a", encoding="utf-8") as f:
        for t2, a, d2 in flags:
            f.write(json.dumps({"ev": "flag", "t": t2, "addr": a,
                                "hp": 54.0, "dc": d2}) + CHR_LF)
    outdir = os.path.join(os.path.dirname(path), "out")
    cfg = make_cfg()
    src = cleaner.clean_file(path, outdir, cfg)
    ds = src["deaths_summary"]
    info = ds["slots"]["0x%x" % A]
    # t∈(2,4) 条目全缺席，flag 行补采：差分照样数完 6 死（空洞免疫）
    assert info["trusted"] and info["deaths"] == 6, info
    assert ds["total"] == 6


# ---------------- D. [lives 2026-10-05d] 死亡账本主导生命窗重组 ----------------

def _frames_dc(duration, addrs):
    """合成 6 列帧流（hp/dc 齐备，供死亡账本）。

    addrs: {addr: (pos_at, dc_at, first_t)}；pos_at(t)→[x,y,z] 或 None（缺席），
    dc_at(t)→int（账本观测值）；t < first_t 的帧不含该 addr 条目。"""
    frames = []
    t = 0.0
    while t <= duration + 1e-9:
        ents = []
        for a, (pos_at, dc_at, first_t) in sorted(addrs.items()):
            if t < first_t - 1e-9:
                continue
            p = pos_at(t)
            if p is not None:
                ents.append([a, p[0], p[1], p[2], 54.0, dc_at(t)])
        frames.append((round(t, 6), ents))
        t = round(t + DT, 6)
    return frames


def _tm_of(src, addr):
    for r in src["rounds"]:
        for tm in r["targets"]:
            if tm["addr"] == addr:
                return tm
    raise AssertionError("target not found: 0x%x" % addr)


def test_reorg_segments_merge_into_lives():
    """多段并一 + 局末存活：相邻死亡之间的坐标碎段（坏点 streak 切碎）并回
    一条生命，t_end 权威 = 死亡时刻；局末无死亡收尾 = 存活（death_event=False）。"""
    A = ADDR

    def pos(t):
        if t <= 2.0:
            return [100.0 + 300.0 * t, 500.0, 100.0]
        if 2.6 <= t <= 2.64 or 3.0 <= t <= 3.04:
            return [0.0, 0.0, 0.0]        # 连续原点 streak → 坐标切碎段
        if t <= 4.0:
            return [2100.0 + 300.0 * t, 500.0, 100.0]   # 重生跳（>2000 切）
        if t <= 5.0:
            return [4100.0 + 300.0 * t, 500.0, 100.0]   # 第二次重生 → 局末存活段
        return None

    def dc(t):
        return 0 if t < 2.0 else (1 if t < 4.0 else 2)

    frames = _frames_dc(5.0, {A: (pos, dc, 0.0)})
    deaths = [(2.0, A, 1), (4.0, A, 2)]
    src = clean_tmp(tempfile.mkdtemp(prefix="df_rg_"), frames, deaths)
    assert src["deaths_source"] == "flag"
    assert src["n_death_events_unpaired"] == 0
    assert src["reorg_audit"]["mode"] == "full"
    assert src["reorg_audit"]["ok"] is True
    tm = _tm_of(src, A)
    # 坐标切分视角是 5 段（0~2 / 2.02~2.58 / 2.66~2.98 / 3.06~4.0 / 4.02~5.0）；
    # 重组后 3 条生命：两死夹的三个碎段并回一条 + 局末存活。
    assert tm["n_lives"] == 3, tm["lives"]
    lv0, lv1, lv2 = tm["lives"]
    assert lv0["t_end"] == 2.0 and lv0["death_event"] is True
    assert lv1["t_start"] == 2.02 and lv1["t_end"] == 4.0
    assert lv1["death_event"] is True
    assert lv2["t_start"] == 4.02 and lv2["t_end"] == 5.0   # 自然坐标末尾
    assert lv2["death_event"] is False
    st = src["per_addr_cut_stats"]["0x%x" % A]
    assert st["death_rows"] == 2 and st["reorg_deaths_bound"] == 2
    assert st["reorg_lives"] == 3 and st["reorg_ok"] is True
    assert st["split_segments"] == 5                       # 门3 口径：重组前段数


def test_reorg_carryover_rejected_in_window_mode():
    """开局残留甄别（切窗模式，[carryover v2 2026-10-09] stats 锚判据、生产
    几何）：窗 lo = 本局 stats 锚 − 前垫 10s；锚前关账的残留死亡（上一局尾）
    不作边界；锚后关账的局中死亡（含换绑重生后的新身份）全保留；全文件模式
    同一行照常是边界。"""
    A, B = 0x7FF600000001, 0x7FF600000002

    def pos_a(t):
        if t < 2.0:
            return [100.0 + 300.0 * t, 500.0, 100.0]   # 残留身份（上一局尾巴）
        return [3000.0 + 300.0 * t, 500.0, 100.0]      # 换绑重生（>2000 跳）

    def dc_a(t):
        if t < 1.0:
            return 0
        if t < 2.0:
            return 1     # 残留身份死亡 @1.0（账本在换绑 @2.0 关账）
        if t < 5.0:
            return 0     # 换绑复位 @2.0（本局开始）；局中死亡 @5.0/5.5/6.0
        if t < 5.5:
            return 1
        if t < 6.0:
            return 2
        return 3

    def pos_b(t):
        return [3000.0 + 300.0 * (t - 3.5), 900.0, 100.0]

    def dc_b(t):
        return 0 if t < 4.6 else 1

    frames = _frames_dc(6.5, {A: (pos_a, dc_a, 0.0), B: (pos_b, dc_b, 3.5)})
    deaths = [(1.0, A, 1), (5.0, A, 1), (5.5, A, 2), (6.0, A, 3), (4.6, B, 1)]
    # 生产切窗几何：stats 锚=本局开始（源相对 3.0），窗 lo=锚−前垫 10 →
    # (T0-7, T0+7)；carryover 锚换算回源相对 t = -7+10 = 3.0。
    src = clean_tmp(tempfile.mkdtemp(prefix="df_cow_"), frames, deaths,
                    epoch_window=(T0 - 7.0, T0 + 7.0))
    assert src["deaths_source"] == "flag"
    # v2 锚 = stats 锚 3.0（spawn_wave 键名保留、语义=stats 锚）
    assert src["reorg_audit"]["spawn_wave"] == 3.0
    assert src["reorg_audit"]["carryover_rule"] == 2
    assert src["reorg_audit"]["mode"] == "window"
    assert src["n_death_rows_rejected_carryover"] == 1
    assert src["reorg_audit"]["ok"] is True
    tmA, tmB = _tm_of(src, A), _tm_of(src, B)
    # A：残留死亡 @1.0 甄别掉 → 3 边界 → 4 生命（含头部并回 + 局末存活）
    assert tmA["n_lives"] == 4, tmA["lives"]
    assert sum(1 for l in tmA["lives"] if l["death_event"]) == 3
    assert tmA["lives"][0]["t_start"] == 0.0 and tmA["lives"][0]["t_end"] == 5.0
    assert tmB["n_lives"] == 2
    assert sum(1 for l in tmB["lives"] if l["death_event"]) == 1

    # 全文件模式（无 epoch 窗）：无锚、不做 carryover 甄别，@1.0 照常是边界
    src2 = clean_tmp(tempfile.mkdtemp(prefix="df_cof_"), frames, deaths)
    assert src2["reorg_audit"]["mode"] == "full"
    assert src2["reorg_audit"]["spawn_wave"] is None
    assert src2["reorg_audit"]["carryover_rule"] == 2
    assert src2["n_death_rows_rejected_carryover"] == 0
    tmA2 = _tm_of(src2, A)
    assert tmA2["n_lives"] == 5
    assert tmA2["lives"][0]["t_end"] == 1.0 and tmA2["lives"][0]["death_event"] is True


def test_reorg_unbacked_row_rejected():
    """无步证伪迹甄别：dc 从未步进的行（如重绑瞬态 dc=0 复读）不作边界、入审计；
    账本背书"从未死亡" → 全部坐标段并回单条生命。"""
    A = ADDR
    frames = _frames_dc(4.0, {A: (lambda t: [100.0 + 300.0 * t, 500.0, 100.0],
                                        lambda t: 0, 0.0)})
    deaths = [(1.0, A, 0)]
    src = clean_tmp(tempfile.mkdtemp(prefix="df_ub_"), frames, deaths)
    assert src["n_death_rows_rejected_unbacked"] == 1
    assert src["n_death_events_unpaired"] == 0
    tm = _tm_of(src, A)
    assert tm["n_lives"] == 1
    assert all(l["death_event"] is False for l in tm["lives"])
    st = src["per_addr_cut_stats"]["0x%x" % A]
    assert st["death_rows_rejected_unbacked"] == 1
    assert st["reorg_deaths_ledger"] == 0 and st["reorg_ok"] is True


def test_reorg_no_evidence_addr_falls_back_to_coordinate_cuts():
    """无死亡 addr 回退坐标切分：无行、无 dc 账本（旧式 4 列条目）的 addr
    保持 split_track 段形（不并回）；有账本的邻 addr 照常重组（驱动 flag_path）。"""
    A, X = 0x7FF600000001, 0x7FF600000002
    frames = []
    t = 0.0
    while t <= 4.0 + 1e-9:
        ents = [[A, 100.0 + 300.0 * t, 500.0, 100.0, 54.0, 1 if t >= 2.0 else 0]]
        if 2.0 <= t <= 2.04:
            x = [0.0, 0.0, 0.0]                    # 连续原点 → 坐标切段
        else:
            x = [5000.0 + 50.0 * t, 800.0, 100.0]  # 50 u/s 滑行（避开幽灵判据）
        ents.append([X, x[0], x[1], x[2]])         # 4 列：无 dc 观测
        frames.append((round(t, 6), ents))
        t = round(t + DT, 6)
    deaths = [(2.0, A, 1)]
    src = clean_tmp(tempfile.mkdtemp(prefix="df_fb_"), frames, deaths)
    stX = src["per_addr_cut_stats"]["0x%x" % X]
    assert stX.get("reorg_fallback") is True
    assert "reorg_ok" not in stX
    tmX = _tm_of(src, X)
    assert tmX["n_lives"] == 2, tmX["lives"]        # 坐标切分原样保留
    tmA = _tm_of(src, A)
    assert tmA["n_lives"] == 2                      # A：1 死 + 局末存活


def test_reorg_never_died_addr_merges_to_single_life():
    """有账本、无死亡行的 addr（账本背书从未死亡）：全部坐标碎段并回单条生命。"""
    A, X = 0x7FF600000001, 0x7FF600000002

    def pos(t):
        if 2.0 <= t <= 2.04:
            return [0.0, 0.0, 0.0]
        return [100.0 + 300.0 * t, 500.0, 100.0]

    frames = _frames_dc(4.0, {A: (pos, lambda t: 0, 0.0)})
    # X：驱动 flag_path（有行有账本）；A：无行、账本恒 0 → 并回单生命
    frames2 = []
    for t, ents in frames:
        ents2 = list(ents)
        ents2.append([X, 3000.0 + 300.0 * t, 700.0, 100.0, 54.0,
                      1 if t >= 3.0 else 0])
        frames2.append((t, ents2))
    src = clean_tmp(tempfile.mkdtemp(prefix="df_nd_"), frames2,
                    deaths=[(3.0, X, 1)])
    tmA = _tm_of(src, A)
    assert tmA["n_lives"] == 1, tmA["lives"]
    assert tmA["lives"][0]["death_event"] is False
    stA = src["per_addr_cut_stats"]["0x%x" % A]
    assert stA["death_rows"] == 0 and stA["reorg_lives"] == 1
    assert stA["reorg_deaths_ledger"] == 0 and stA["reorg_ok"] is True


def test_reorg_single_segment_single_death():
    """单段单死亡：一条生命、t_end 权威 = 死亡时刻、death_event=True、无局末段。"""
    A = ADDR
    frames = _frames_dc(2.0, {A: (lambda t: [100.0 + 300.0 * t, 500.0, 100.0],
                                        lambda t: 0 if t < 2.0 else 1, 0.0)})
    src = clean_tmp(tempfile.mkdtemp(prefix="df_ss_"), frames,
                    deaths=[(2.0, A, 1)])
    tm = _tm_of(src, A)
    assert tm["n_lives"] == 1, tm["lives"]
    assert tm["lives"][0]["t_end"] == 2.0
    assert tm["lives"][0]["death_event"] is True
    assert tm["lives"][0]["n"] == 101               # 0~2.0s 全部有效点


def test_legacy_file_has_no_reorg_audit():
    """旧数据（无 death 行）零漂移：不写重组审计键（schema 逐字节保持）。"""
    frames = run_frames(3.0, _moving)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_lga_"), frames, deaths=())
    assert "reorg_audit" not in src
    assert "n_death_rows_rejected_unbacked" not in src
    assert "n_death_rows_rejected_carryover" not in src
    for st in src["per_addr_cut_stats"].values():
        assert "reorg_ok" not in st and "reorg_fallback" not in st
        assert "split_segments" in st               # additive：门3 口径字段


# ---------------- E. [carryover v2 2026-10-09] Sixshot 家族塌缩回归 ----------------

def _piecewise_dc(events):
    """events: [(t, dc_after)] 时间升序 → dc_at(t) 阶梯函数。"""
    def dc(t):
        value = 0
        for et, v in events:
            if t >= et - 1e-9:
                value = v
        return value
    return dc


def test_static_respawn_no_carryover_collapse():
    """Sixshot 式静态连续重生（v2 主回归）：6 静态槽位局中连杀（dc 同身份内
    爬升，真实语料形态），其一局中换绑一次（掉步+重同步新身份段，换绑后继续
    爬升）；外加一个局中掉步、局尾才重同步的垃圾槽（旧全局 wave 规则的毒源
    ——wave 被它拖到 60，把全窗死亡整批误拒塌缩成单 life）。v2 stats 锚判据
    下：锚后关账的死亡全保留，无塌缩。"""
    base = 0x7FF600000001
    addrs = {}
    deaths = []
    # 5 个普通槽位：出生 10.0（=stats 锚），12+i 起三连杀（dc 爬升 1/2/3）
    for i in (0, 1, 3, 4, 5):
        a = base + i
        t1 = 12.0 + i
        addrs[a] = (
            lambda t, i=i: [2000.0 + 300.0 * i, 500.0, 100.0],
            _piecewise_dc([(t1, 1), (t1 + 1.0, 2), (t1 + 2.0, 3)]),
            10.0,
        )
        deaths += [(t1, a, 1), (t1 + 1.0, a, 2), (t1 + 2.0, a, 3)]
    # 第三槽位：三连杀后局中换绑（dc 掉 0，+1 三连重同步），换绑后继续三连杀
    a3 = base + 2
    addrs[a3] = (
        lambda t: [2000.0 + 300.0 * 2, 500.0, 100.0],
        _piecewise_dc([(14.0, 1), (14.2, 2), (14.4, 3), (15.0, 0),
                       (15.2, 1), (15.4, 2), (15.6, 3),
                       (16.0, 4), (16.2, 5), (16.4, 6)]),
        10.0,
    )
    deaths += [(14.0, a3, 1), (14.2, a3, 2), (14.4, a3, 3),
               (15.2, a3, 1), (15.4, a3, 2), (15.6, a3, 3),
               (16.0, a3, 4), (16.2, a3, 5), (16.4, a3, 6)]
    # 垃圾槽：55 掉步、60 掉 0、68~69.5 三连 +1 重同步——旧规则下 wave=60，
    # 全窗死亡（<60）整批误拒（本测试的"塌缩负样本"来源）
    g = base + 6
    addrs[g] = (
        lambda t: [6000.0, 800.0, 100.0],
        _piecewise_dc([(55.0, 1), (60.0, 0), (68.0, 1), (69.0, 2), (69.5, 3)]),
        10.0,
    )
    deaths += [(55.0, g, 1), (68.0, g, 1), (69.0, g, 2), (69.5, g, 3)]

    frames = _frames_dc(70.0, addrs)
    src = clean_tmp(tempfile.mkdtemp(prefix="df_six_"), frames, deaths,
                    epoch_window=(T0 + 0.0, T0 + 70.0))

    assert src["reorg_audit"]["mode"] == "window"
    assert src["reorg_audit"]["carryover_rule"] == 2
    assert src["reorg_audit"]["spawn_wave"] == 10.0      # stats 锚
    assert src["n_death_rows_rejected_carryover"] == 0   # 塌缩消失
    assert src["n_death_rows_rejected_unbacked"] == 0
    assert src["reorg_audit"]["ok"] is True
    # 每槽 lives = 死亡数 + 局末存活；死亡边界逐条 death_event
    for i in (0, 1, 3, 4, 5):
        tm = _tm_of(src, base + i)
        assert tm["n_lives"] == 4, (base + i, tm["lives"])
        assert sum(1 for l in tm["lives"] if l["death_event"]) == 3
    tm3 = _tm_of(src, a3)
    assert tm3["n_lives"] == 10, tm3["lives"]
    assert sum(1 for l in tm3["lives"] if l["death_event"]) == 9
    assert src["survivor_check"]["status"] == "ok"


def test_survivor_check_floor():
    """幸存者 sanity floor：D≥10 且 L<0.5D → collapsed+cause；低击杀局
    （D<10）与健康局 → ok（fail-open，只标注不拦）。"""
    A = ADDR

    def synth(deaths_n, rows_n):
        step = 0.2
        events = [(round(step * k, 3), k) for k in range(1, deaths_n + 1)]
        duration = round(step * deaths_n + 0.5, 3)
        frames = _frames_dc(duration, {A: (
            lambda t: [100.0 + 300.0 * t, 500.0, 100.0], _piecewise_dc(events), 0.0)})
        rows = [(t, A, dc) for (t, dc) in events[:rows_n]]
        return clean_tmp(tempfile.mkdtemp(prefix="df_sur_"), frames, rows)

    src = synth(deaths_n=30, rows_n=2)
    check = src["survivor_check"]
    assert check["status"] == "collapsed"
    assert check["cause"] == "cleaner_life_starvation"
    assert check["ledger_deaths"] == 30 and check["death_event_lives"] == 2

    src = synth(deaths_n=8, rows_n=2)          # 低击杀局：D<10 不触发
    assert src["survivor_check"]["status"] == "ok"
    assert src["survivor_check"]["cause"] == ""

    src = synth(deaths_n=30, rows_n=28)        # 健康局
    assert src["survivor_check"]["status"] == "ok"
    assert src["survivor_check"]["death_event_lives"] == 28


def main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("[PASS] %s" % fn.__name__)
    print("[done] %d tests 全部通过" % len(fns))


if __name__ == "__main__":
    main()
