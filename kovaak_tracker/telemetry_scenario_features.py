"""Telemetry-observed scenario features + discrimination tree (一期).

场景分类一层的观测特征束：从冻结旁车原文（round/views/inputs/bb +
merge_manifest epoch 锚）计算火控/点击误差/节奏/角速度/活性/几何/目标运动
特征，并按已拍板的判别树给出 family+subdomain candidate。

红线与口径（实现时已锁死）：
- 全部特征从旁车原文计算，禁止走 event_bundle（512 事件截断会毁掉节奏特征）；
- kills 只用官方 pairing/matched kills（入参 official_kills，来自
  ExternalTelemetryRun meta.pairing.perf_official.kills）——deaths≠kills，
  禁用 cleaned 消亡计数；官方 kills 缺失时 clicks_per_kill/kill 相关分支置 absent；
- 判别窗口用官方挑战窗（canonical epoch ms 经 merge_manifest.alignment.
  s_epoch_of_t0 映射到旁车 t 域，再裁剪到该轮数据范围），不用 cleaner 轮界
  （轮间 freeplay 污染）；无官方窗时退化为全轮并记 limitation；
- bb.json 缺失时几何特征（ang_radius_med/dist_med）置 absent 并记 limitation，
  不用 30cm 兜底；
- 活性计数做 churn 过滤（生命<200ms 剔除）：场景衍生对象会把裸计数膨胀；
- 角速度 rot 差分丢弃 dt>0.2s 样本；水平方向差分 wrap ±180°；
- 几何投影与 telemetry_signals 同源数学（直接复用其读取与角度函数，不重写）。

阈值（final_0831 六局实测量级，非生产阈值；验收对照表见 Progress）：
- hold_frac>0.6 或 cpm<30 → tracking：Controlsphere 0.981/2.0、HumStrafe
  0.904/5.8；点击类 0.17-0.22/95-142；
- moving_time_share>0.5 → dynamic：pasu 0.504，其余点击类 ≤0.42；
  注意该分支必须先于 alive_mean 并发格分支——地图物件把 pasu 的 alive_mean
  抬到 9.97（>1wall6targets 的 7.95），alive 先行会把 dynamic 误判为 static；
- alive_mean>4 → static_clicking 并发格：bean 4.98、1wall 7.95；
- precision 子域（lt1>0.7 且 cv<0.4 且 ang_radius<1° 且 dist>3000）：
  1wall 1.0/0.178/0.898°/3826 全中；bean 的 ang_radius 1.235° 恰好落出；
- speed 子域（甩枪）：任务原条件 err_p10>3° 且 cv>0.6 在 final_0831 无命中
  样本（Valorant 2.49°/0.304），实测可分离的甩枪签名是点击时刻误差
  err_at_click_p50（Valorant 5.13° vs 点击类 ≤0.67°），两条路径取或；
- 持续火力分支内的转火判定（0906 方向修正，0901/0904 实测 n=1/类）：
  计分结构先验一票否决 + 行为签名。官方计分结构对未命中/时间流失罚分的
  场景（Humanoid Strafe 系：ScorePerTime=1.0 → .perf score 事件逐秒 -1 负
  delta）按住扫射持续失分，理性玩法是打完即停——转火结构不成立，直接落
  tracking；计分中性场景（TileFrenzy 系：ScorePerKill=1.0，.perf score 事件
  零负 delta，miss 上百不扣分）切靶期扫射零代价，按死扫是理性策略——
  官方杀率 >=0.2/s → target_switching（TileFrenzy 1.41/s vs HumStrafe
  0.087/s、Controlsphere 0）；官方窗未采用时退位给误差锯齿率
  err_spike_rate_per_s >= 0.3/s 兜底（TileFrenzy 0.91/s，纯跟枪 0，
  HumStrafe <=0.24/s；弱证据通道保留 hold>=0.97 按死门槛——0904 HumStrafe
  fallback 轮 r7/r8 实测无门槛会误翻）。未收录场景（先验 None）退化为
  行为签名判定；
- 9b5886a 的 hold_frac>=0.97 硬门槛已撤销：转火局打失误也会松手，硬 hold
  门槛误伤真转火；"中间松没松手"是两类图计分规则差异的行为投影，不是类别
  边界；
- 旧 bearing_delta_at_kill_med>60° → target_switching 分支已拆除：0901 实测
  该签名反向（poll addr 池化+物件抖动产生伪消亡，Controlsphere 0 杀被它打出
  75.96°>60°，真转火 TileFrenzy 仅 51.53°）；特征保留在束内只作诊断；
- clicks_per_kill>1.5 且 err_at_click_p50<1° → reclick（血厚）；
- tracking 且官方 kills∈{None,0} → invincible 变体：合同 baseline dispatch
  只允许 descriptive_only，变体以 limitation 表达（claim_ceiling 不再降）。
"""

from __future__ import annotations

import bisect
import copy
import math
import os
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# 与 telemetry_signals 同源的旁车读取与几何数学（复用而非重写）。
from .telemetry_signals import (
    _LIFE_FRAME_EPSILON_S,
    _build_radius_lookup,
    _load_round_target_positions,
    _load_views,
    _read_json,
    _read_jsonl,
    _wrap_pi,
)

SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION = "scenario_observed_profile.v1"

# ---- 判别树阈值（final_0831 实测量级，非生产阈值） ----
HOLD_FRAC_TRACKING_MAX = 0.6
CLICKS_PER_MIN_TRACKING_MAX = 30.0
MOVING_TIME_SHARE_DYNAMIC_MIN = 0.5
ALIVE_MEAN_CONCURRENT_MIN = 4.0
ERR_AT_CLICK_LT1_PRECISION_MIN = 0.7
INTER_CLICK_CV_PRECISION_MAX = 0.4
ANG_RADIUS_PRECISION_MAX_DEG = 1.0
DIST_PRECISION_MIN_CM = 3000.0
ERR_AT_CLICK_FLICK_MIN_DEG = 2.0
ERR_P10_FLICK_MIN_DEG = 3.0
INTER_CLICK_CV_FLICK_MIN = 0.6
# 持续火力分支内转火判定（0906 方向修正；0901/0904 实测，n=1/类，非生产阈值）：
# - 计分结构先验一票否决（下方 SCENARIO_SCORING_PRIOR）：罚分结构不得判转火；
# - 官方杀率主签名：TileFrenzy 1.41/s 在上 7 倍，HumStrafe 0.087/s 在下 2.3 倍；
# - 误差锯齿兜底（官方窗未采用时）：TileFrenzy 0.91/s，HumStrafe <=0.24/s，
#   纯跟枪（Controlsphere）0；
# - 9b5886a 的 hold_frac>=0.97 硬门槛已从杀率主签名撤销：真转火打失误也会
#   松手（TileFrenzy hold 0.981、HumStrafe 0.9455 的差距是计分规则差异的
#   行为投影，不是类别边界），硬 hold 门槛误伤；误翻由计分结构先验拦截。
# - 锯齿兜底保留按死形态门槛（0904 实测回归依据）：无官方窗的弱证据场景里，
#   HumStrafe 0904 局的旁车 fallback 轮（r7/r8）hold 0.97+、锯齿 0.3+/s，
#   无门槛会误翻成转火——弱证据通道保守，强证据（官方杀率）才放宽。
KILL_RATE_SWITCH_MIN_PER_S = 0.2
ERR_SPIKE_RATE_SWITCH_MIN = 0.3
HELD_SWITCH_SAWTOOTH_MIN_HOLD_FRAC = 0.97
ERR_SPIKE_EXCURSION_MIN_DEG = 15.0
CLICKS_PER_KILL_RECLICK_MIN = 1.5
ERR_AT_CLICK_RECLICK_MAX_DEG = 1.0

# ---- 特征口径参数 ----
CHURN_MIN_LIFE_S = 0.2          # 生命<200ms 剔除
_LIFE_GAP_SPLIT_S = 1.0         # 轮帧 addr 断流 >1s 视为一条生命结束
OMEGA_DT_MAX_S = 0.2            # rot 差分 dt 上限
CLICK_NEIGHBORHOOD_S = 0.06     # 点击时刻 ±60ms 邻近样本
NONCLICK_RADIUS_S = 0.25        # 非点击期 = 距最近 L_down >250ms
MOVING_SPEED_MIN_CM_S = 15.0    # 目标速度阈值
MIN_FEATURE_WINDOW_S = 5.0      # 判别窗低于该时长不产出特征结论
_TIMEOUT_CENSOR_EPS_S = 0.05    # 生命止于数据末端视为 timeout/删失，非击杀
_FLIP_SPEED_MIN_DEG_S = 30.0    # 方向翻转计入阈值（>30°/s）

WINDOW_OFFICIAL = "official_pairing_window"
WINDOW_FULL_ROUND = "full_round_fallback"
LIMITATION_FULL_ROUND_FALLBACK = "observed_window_full_round_fallback"
LIMITATION_GEOMETRY_ABSENT = "observed_bb_absent_geometry_features_absent"

# ---- 场景级计分结构先验（"未命中惩罚结构"，0906 方向修正） ----
# 键为 casefold 场景名；值：True = 罚分结构（计分对未命中/时间流失罚分），
# False = 中性结构（击杀是唯一得分源），未收录场景无先验（退化为行为签名）。
# 依据（.sce 官方计分字段 + .perf 逐事件 score 负 delta 实测，2026-08-31/09-01/09-04 真机）：
# - TileFrenzy 0901/0904：.sce ScorePerKill=1.0、无罚分字段；.perf score 事件
#   负 delta 0 个（59/59 正，miss 181/185 不扣分）→ 中性，转火结构成立；
# - Humanoid Strafe 0904 / Humanoid Strafe Flat 0831：.sce ScorePerTime=1.0
#   （时间罚分；ScoreLossPerMiss 显式 0），.perf score 事件逐秒 -1 负 delta
#   （各 13 个）→ 罚分结构，按住扫射持续失分，转火结构不成立；
# - Controlsphere 0831/0901（未收录对照）：score 负 delta 0 个、纯伤害分，
#   0 杀由杀率签名挡转火，无需先验。
SCENARIO_SCORING_PRIOR = {
    "tile frenzy 180 strafing tracking": False,
    "humanoid strafe": True,
    "humanoid strafe flat": True,
}
# 罚分结构下的持续火力判定依据（machine-readable basis 后缀）。
BASIS_SCORING_PENALIZES_FIRE = "sustained_hold_scoring_penalizes_fire"


def scenario_scoring_penalizes_fire(display_name: str | None) -> bool | None:
    """场景级计分结构先验查询；未收录/空名返回 None（无先验）。"""
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    return SCENARIO_SCORING_PRIOR.get(display_name.strip().casefold())

_FEATURE_KEYS = (
    "hold_frac",
    "mean_hold_ms",
    "clicks_per_min",
    "clicks_per_kill",
    "err_at_click_p50",
    "err_at_click_lt1deg_share",
    "err_p10",
    "err_spike_rate_per_s",
    "inter_click_cv",
    "omega_p99",
    "omega_frac_20_200",
    "dir_flips_per_s",
    "alive_mean",
    "bearing_delta_at_kill_med",
    "ang_radius_med",
    "dist_med",
    "moving_time_share",
    "target_speed_p50",
)


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _percentile(sorted_values: Sequence[float], share: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, int(share * len(sorted_values))))
    return float(sorted_values[index])


def _view_direction(rot: Sequence[float]) -> tuple[float, float, float]:
    """UE4 约定（rot[0]=pitch, rot[1]=yaw）的视线单位向量（与 telemetry_signals 同源）。"""
    pitch = math.radians(float(rot[0]))
    yaw = math.radians(float(rot[1]))
    return (
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch),
    )


def _angle_between_deg(a: Sequence[float], b: Sequence[float]) -> float:
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    return math.degrees(math.acos(dot))


def _load_button_edges(
    round_dir: Path,
    round_number: int,
    inputs_name: str | None = None,
) -> tuple[list[float], list[float]]:
    """inputs_NN.jsonl 的 L_down/L_up 逐发沿时刻（源 t 域，秒），各自升序。"""
    downs: list[float] = []
    ups: list[float] = []
    for record in _read_jsonl(round_dir / (inputs_name or f"inputs_{round_number:02d}.jsonl")):
        buttons = record.get("btn")
        if not isinstance(buttons, Sequence) or isinstance(buttons, str):
            continue
        try:
            edge_t = float(record["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(button == "L_down" for button in buttons):
            downs.append(edge_t)
        if any(button == "L_up" for button in buttons):
            ups.append(edge_t)
    downs.sort()
    ups.sort()
    return downs, ups


def _presence_lives(
    positions: Mapping[int, Sequence[tuple[float, float, float, float]]],
) -> list[tuple[int, float, float]]:
    """轮帧 addr 连续在场段 -> [(addr, t_start, t_end)]（不做 churn 过滤）。

    addr 会被 cleaner 池化复用（非身份），按断流拆段才接近真实生命窗。
    """
    lives: list[tuple[int, float, float]] = []
    for addr, points in positions.items():
        start: float | None = None
        prev_t: float | None = None
        for point in points:
            frame_t = point[0]
            if start is None:
                start = frame_t
            elif frame_t - prev_t > _LIFE_GAP_SPLIT_S:
                lives.append((addr, start, prev_t))
                start = frame_t
            prev_t = frame_t
        if start is not None and prev_t is not None:
            lives.append((addr, start, prev_t))
    lives.sort(key=lambda item: (item[1], item[0]))
    return lives


class _ObservedWorld:
    """一轮旁车在判别窗内的轻量查询视图（生命窗内插值/最近目标/活性计数）。"""

    def __init__(
        self,
        positions: Mapping[int, Sequence[tuple[float, float, float, float]]],
        views: Sequence[tuple[float, Sequence[float], Sequence[float], float | None]],
        radius_windows: Sequence[tuple[float, float, float]],
    ) -> None:
        self._positions = positions
        self.lives = [
            life for life in _presence_lives(positions)
            if life[2] - life[1] >= CHURN_MIN_LIFE_S
        ]
        self.radius_windows = list(radius_windows)
        # view 索引 -> 该帧时刻存活的过滤后目标 [(addr, x, y, z)]。
        self._alive_by_view: dict[int, list[tuple[int, float, float, float]]] = {}
        for index, (frame_t, _pos, _rot, _fov) in enumerate(views):
            alive = []
            for addr, life_t0, life_t1 in self.lives:
                if not life_t0 <= frame_t <= life_t1:
                    continue
                point = self._interp_in_life(addr, life_t0, life_t1, frame_t)
                if point is not None:
                    alive.append((addr, point[0], point[1], point[2]))
            self._alive_by_view[index] = alive

    def _interp_in_life(
        self,
        addr: int,
        life_t0: float,
        life_t1: float,
        t: float,
    ) -> tuple[float, float, float] | None:
        """与 telemetry_signals._interpolate_in_life 同口径：只在一条生命窗内插值。"""
        points = self._positions.get(addr, ())
        lo = bisect.bisect_left(points, (life_t0 - _LIFE_FRAME_EPSILON_S,))
        hi = bisect.bisect_right(points, (life_t1 + _LIFE_FRAME_EPSILON_S, math.inf))
        segment = points[lo:hi]
        if not segment:
            return None
        index = bisect.bisect_right(segment, (t, math.inf, math.inf, math.inf)) - 1
        if index < 0:
            point = segment[0]
            return point[1], point[2], point[3]
        if index >= len(segment) - 1:
            point = segment[-1]
            return point[1], point[2], point[3]
        left, right = segment[index], segment[index + 1]
        span = right[0] - left[0]
        weight = (t - left[0]) / span if span > 0 else 0.0
        return (
            left[1] + (right[1] - left[1]) * weight,
            left[2] + (right[2] - left[2]) * weight,
            left[3] + (right[3] - left[3]) * weight,
        )

    def alive_at(self, view_index: int) -> list[tuple[int, float, float, float]]:
        return self._alive_by_view.get(view_index, ())

    def alive_mean(self, view_indices: Sequence[int]) -> float | None:
        if not view_indices:
            return None
        total = sum(len(self._alive_by_view.get(index, ())) for index in view_indices)
        return total / len(view_indices)

    def dying_target_bearing_deg(
        self,
        addr: int,
        life_t0: float,
        life_t1: float,
        window_views: Sequence[tuple[int, float, Sequence[float], Sequence[float]]],
        *,
        neighborhood_s: float = 0.25,
    ) -> float | None:
        """死亡目标在死亡时刻附近的方位角（度）。

        在死亡时刻 ±neighborhood_s 内找该目标仍存活（生命窗未结束）的最近
        view 采样——死亡时刻之后的 view 目标已消失，不能用作击杀方位。
        """
        best: tuple[float, float] | None = None
        for index, frame_t, pos, rot in window_views:
            if abs(frame_t - life_t1) > neighborhood_s:
                continue
            if not any(target_addr == addr for target_addr, *_rest in self.alive_at(index)):
                continue
            point = self._interp_in_life(addr, life_t0, life_t1, frame_t)
            if point is None:
                continue
            bearing = math.degrees(math.atan2(point[1] - pos[1], point[0] - pos[0]))
            if best is None or abs(frame_t - life_t1) < best[0]:
                best = (abs(frame_t - life_t1), bearing)
        return best[1] if best is not None else None

    def radius_at(self, t: float) -> float | None:
        for lo, hi, radius in self.radius_windows:
            if lo <= t <= hi:
                return radius
        return None


def _min_angular_error_deg(
    world: _ObservedWorld,
    view_index: int,
    view_pos: Sequence[float],
    view_rot: Sequence[float],
) -> float | None:
    """准心->最近存活目标的 3D 角误差（px 投影同源几何的角度域等价形式）。"""
    forward = _view_direction(view_rot)
    best: float | None = None
    for _addr, x, y, z in world.alive_at(view_index):
        dx = x - view_pos[0]
        dy = y - view_pos[1]
        dz = z - view_pos[2]
        norm = math.sqrt(dx * dx + dy * dy + dz * dz)
        if norm <= 0:
            continue
        error = _angle_between_deg(forward, (dx / norm, dy / norm, dz / norm))
        if best is None or error < best:
            best = error
    return best


def compute_observed_features(
    *,
    views: Sequence[tuple[float, Sequence[float], Sequence[float], float | None]],
    downs: Sequence[float],
    ups: Sequence[float],
    positions: Mapping[int, Sequence[tuple[float, float, float, float]]],
    radius_windows: Sequence[tuple[float, float, float]],
    window: tuple[float, float],
    official_kills: int | None,
) -> dict[str, float | None]:
    """判别窗内的全部观测特征；不可计算的特征置 None（absent 语义）。"""
    t_lo, t_hi = window
    duration = t_hi - t_lo
    features: dict[str, float | None] = {key: None for key in _FEATURE_KEYS}
    if duration <= 0:
        return features
    world = _ObservedWorld(positions, views, radius_windows)
    window_views = [
        (index, frame_t, pos, rot)
        for index, (frame_t, pos, rot, _fov) in enumerate(views)
        if t_lo <= frame_t <= t_hi
    ]
    if not window_views:
        return features
    window_downs = [t for t in downs if t_lo <= t <= t_hi]
    window_ups = [t for t in ups if t_lo <= t <= t_hi]

    # ---- 火控 ----
    hold_spans: list[float] = []
    current_down: float | None = None
    for edge_t, kind in sorted(
        [(t, "down") for t in window_downs] + [(t, "up") for t in window_ups]
    ):
        if kind == "down":
            if current_down is None:
                current_down = edge_t
        elif current_down is not None:
            hold_spans.append(max(0.0, edge_t - current_down))
            current_down = None
    if current_down is not None:
        # 按住删失于窗末：只计窗内时长。
        hold_spans.append(max(0.0, t_hi - current_down))
    features["hold_frac"] = _round(
        sum(min(span, duration) for span in hold_spans) / duration
    )
    features["mean_hold_ms"] = _round(
        1000.0 * statistics.mean(hold_spans) if hold_spans else None, 2
    )
    features["clicks_per_min"] = _round(len(window_downs) / (duration / 60.0), 3)
    features["clicks_per_kill"] = (
        _round(len(window_downs) / official_kills, 4)
        if official_kills is not None and official_kills > 0
        else None
    )

    # ---- 点击时刻误差（±60ms 邻近样本，误差取邻近最优） ----
    click_errors: list[float] = []
    for click_t in window_downs:
        candidates = [
            error
            for index, frame_t, pos, rot in window_views
            if abs(frame_t - click_t) <= CLICK_NEIGHBORHOOD_S
            for error in (_min_angular_error_deg(world, index, pos, rot),)
            if error is not None
        ]
        if candidates:
            click_errors.append(min(candidates))
    if click_errors:
        features["err_at_click_p50"] = _round(statistics.median(click_errors), 3)
        features["err_at_click_lt1deg_share"] = _round(
            sum(1 for error in click_errors if error < 1.0) / len(click_errors)
        )

    # ---- 休息位误差（非点击期误差下沿 p10） ----
    rest_errors: list[float] = []
    for index, frame_t, pos, rot in window_views:
        if window_downs and min(abs(frame_t - click) for click in window_downs) <= NONCLICK_RADIUS_S:
            continue
        error = _min_angular_error_deg(world, index, pos, rot)
        if error is not None:
            rest_errors.append(error)
    rest_errors.sort()
    features["err_p10"] = _round(_percentile(rest_errors, 0.1), 3)

    # ---- 误差锯齿率（持续火力转火的遥测兜底签名，0901 实证） ----
    # 逐帧“准心->最近存活目标最小角误差”序列中 >15° excursion 次数/s；
    # 口径与 heldfire_analysis.py 研究脚本一致（>15° 进入、<=15° 退出计一次，
    # 窗末仍未退出的 excursion 不计；无存活目标帧跳过）。
    err_spikes = 0
    in_excursion = False
    err_sample_count = 0
    for index, _frame_t, pos, rot in window_views:
        error = _min_angular_error_deg(world, index, pos, rot)
        if error is None:
            continue
        err_sample_count += 1
        if error > ERR_SPIKE_EXCURSION_MIN_DEG:
            in_excursion = True
        elif in_excursion:
            in_excursion = False
            err_spikes += 1
    if err_sample_count:
        features["err_spike_rate_per_s"] = _round(err_spikes / duration, 4)

    # ---- 节奏 ----
    gaps = [
        later - earlier
        for earlier, later in zip(window_downs, window_downs[1:])
        if later - earlier > 0.01
    ]
    if len(gaps) >= 2 and statistics.mean(gaps) > 0:
        features["inter_click_cv"] = _round(
            statistics.pstdev(gaps) / statistics.mean(gaps)
        )

    # ---- 角速度（rot 差分，dt>0.2s 丢弃；3D 角差自带 yaw wrap ±180 语义） ----
    omegas: list[float] = []
    flips = 0
    previous_sign = 0
    for (_a, frame_a, _pos_a, rot_a), (_b, frame_b, _pos_b, rot_b) in zip(
        window_views, window_views[1:]
    ):
        dt = frame_b - frame_a
        if dt <= 0 or dt > OMEGA_DT_MAX_S:
            continue
        omegas.append(
            _angle_between_deg(_view_direction(rot_a), _view_direction(rot_b)) / dt
        )
        dyaw = math.degrees(
            _wrap_pi(math.radians(float(rot_b[1])) - math.radians(float(rot_a[1])))
        )
        if abs(dyaw) / dt > _FLIP_SPEED_MIN_DEG_S:
            sign = 1 if dyaw > 0 else -1
            if previous_sign and sign != previous_sign:
                flips += 1
            previous_sign = sign
    omegas.sort()
    features["omega_p99"] = _round(_percentile(omegas, 0.99), 2)
    features["omega_frac_20_200"] = _round(
        sum(1 for omega in omegas if 20.0 <= omega <= 200.0) / len(omegas)
    )
    features["dir_flips_per_s"] = _round(flips / duration, 4)

    # ---- 活性（churn 过滤后） ----
    features["alive_mean"] = _round(
        world.alive_mean([index for index, _t, _p, _r in window_views]), 3
    )

    # ---- 击杀方位角差 ----
    # kill 时点取非删失生命窗的结束（删失 = 活到数据末端，timeout/换局），
    # 方位角取"刚死亡的目标"在死亡时刻附近仍可见的最近采样。
    # deaths≠kills 的计数红线只约束 kills 数值（clicks_per_kill 用官方 kills），
    # 这里只取击杀时刻的方位序列；无存活佐证（官方 kills 缺失）时分支仍要求
    # bearing 强信号。
    data_end = window_views[-1][1]
    bearings: list[float] = []
    for addr, life_start, life_end in world.lives:
        if not t_lo <= life_end < data_end - _TIMEOUT_CENSOR_EPS_S:
            continue
        bearing = world.dying_target_bearing_deg(addr, life_start, life_end, window_views)
        if bearing is not None:
            bearings.append(bearing)
    bearing_deltas = [
        abs(math.degrees(_wrap_pi(math.radians(later) - math.radians(earlier))))
        for earlier, later in zip(bearings, bearings[1:])
    ]
    if len(bearing_deltas) >= 2:
        features["bearing_delta_at_kill_med"] = _round(
            statistics.median(bearing_deltas), 2
        )

    # ---- 几何（bb 缺失 → absent，不用 30cm 兜底） ----
    if radius_windows:
        dists: list[float] = []
        angular_radii: list[float] = []
        for index, frame_t, pos, _rot in window_views:
            best_dist: float | None = None
            for _addr, x, y, z in world.alive_at(index):
                dist = math.dist((x, y, z), pos)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
            if best_dist is None or best_dist <= 0:
                continue
            dists.append(best_dist)
            radius = world.radius_at(frame_t)
            if radius is not None:
                angular_radii.append(math.degrees(math.atan(radius / best_dist)))
        if dists:
            features["dist_med"] = _round(statistics.median(dists), 1)
        if angular_radii:
            features["ang_radius_med"] = _round(statistics.median(angular_radii), 3)

    # ---- 目标运动（过滤后生命的轮帧差分，cm/s） ----
    speeds: list[float] = []
    for addr, life_start, life_end in world.lives:
        points = [
            point for point in positions.get(addr, ())
            if life_start <= point[0] <= life_end and t_lo <= point[0] <= t_hi
        ]
        for left, right in zip(points, points[1:]):
            dt = right[0] - left[0]
            if dt <= 0:
                continue
            speeds.append(math.dist(left[1:], right[1:]) / dt)
    if speeds:
        features["moving_time_share"] = _round(
            sum(1 for speed in speeds if speed > MOVING_SPEED_MIN_CM_S) / len(speeds)
        )
        features["target_speed_p50"] = _round(statistics.median(speeds), 2)

    return features


def decide_scenario_family_verdict(
    features: Mapping[str, float | None],
    *,
    official_kills: int | None,
    official_window_duration_s: float | None = None,
    official_scoring_penalizes_fire: bool | None = None,
) -> dict[str, Any] | None:
    """判别树：按序短路；全部不满足 → None（瀑布走下一级）。

    official_window_duration_s：官方配对窗时长（秒，裁剪前）。仅持续火力
    分支的转火杀率用它做分母——pairing meta 的 kills 是挑战级总数，归属于
    官方窗而非被轮数据裁剪后的子窗（HumStrafe：10 杀/114.96s=0.087/s；
    若除以 12-21s 子轮窗会虚高 5-9 倍越过阈值）。官方窗未采用（回退全轮）
    时 kills 不可归因到本轮判别窗，杀率置 None，只走锯齿兜底。

    official_scoring_penalizes_fire：官方计分结构对未命中/时间流失是否罚分
    （场景级先验，scenario_scoring_penalizes_fire 查询）。True = 罚分结构
    （如 Humanoid Strafe 的 ScorePerTime 逐秒 -1）：按住扫射持续失分，转火
    结构不成立，一票否决落回 tracking；False = 中性（如 TileFrenzy：miss
    不扣分）；None = 未收录场景，退化为行为签名判定。
    """
    hold_frac = features.get("hold_frac")
    clicks_per_min = features.get("clicks_per_min")
    moving_share = features.get("moving_time_share")
    alive_mean = features.get("alive_mean")
    err_at_click = features.get("err_at_click_p50")
    err_lt1_share = features.get("err_at_click_lt1deg_share")
    err_p10 = features.get("err_p10")
    inter_click_cv = features.get("inter_click_cv")
    ang_radius = features.get("ang_radius_med")
    dist = features.get("dist_med")
    clicks_per_kill = features.get("clicks_per_kill")

    def verdict(
        aim_family: str,
        *,
        subdomains: Sequence[str] = (),
        basis: str,
        basis_values: Mapping[str, float | None],
        model: str = "unknown",
        target_count_model: str = "unknown",
        zero_kill_variant: bool = False,
    ) -> dict[str, Any]:
        return {
            "aim_family": aim_family,
            "subdomains": list(subdomains),
            "target_motion": {"model": model, "target_count_model": target_count_model},
            "basis": f"telemetry_observed_basis_{basis}",
            "basis_values": {key: _round(value) for key, value in basis_values.items()},
            "zero_kill_variant": zero_kill_variant,
        }

    # 1. 持续火力（held-fire）：先查计分结构先验一票否决，再查转火行为签名
    #    （0901 实证，n=1/类，阈值处注释有样本量 caveat），无签名才落
    #    continuous_tracking（含 invincible 零杀变体）。
    tracking_reason: dict[str, float | None] | None = None
    if hold_frac is not None and hold_frac > HOLD_FRAC_TRACKING_MAX:
        tracking_reason = {"hold_frac": hold_frac}
    elif clicks_per_min is not None and clicks_per_min < CLICKS_PER_MIN_TRACKING_MAX:
        tracking_reason = {"clicks_per_min": clicks_per_min}
    if tracking_reason is not None:
        # 计分结构先验一票否决（0906 方向修正）：罚分结构下按住扫射持续失分，
        # 理性玩法是打完即停——即使行为上全程按死、杀率不高，也不得判转火。
        if official_scoring_penalizes_fire is True:
            return verdict(
                "continuous_tracking",
                basis=BASIS_SCORING_PENALIZES_FIRE,
                basis_values=tracking_reason,
                zero_kill_variant=official_kills in (None, 0),
            )
        # 主签名：官方杀率 >=0.2/s（计分中性或无先验时）。按住火力且目标持续
        # 死亡——每次死亡必然伴随一次切靶（因果签名）。不设 hold 上限门槛：
        # 真转火打失误也会松手（9b5886a 硬门槛已撤销）。
        kill_rate = (
            official_kills / official_window_duration_s
            if official_kills is not None and official_window_duration_s
            else None
        )
        if kill_rate is not None and kill_rate >= KILL_RATE_SWITCH_MIN_PER_S:
            return verdict(
                "target_switching",
                basis="heldfire_switch_by_kill_rate",
                basis_values={"official_kill_rate_per_s": kill_rate},
                target_count_model="sequential",
            )
        # 兜底签名：官方杀率缺失（官方窗未采用）时用误差锯齿率——只需
        # views+targets，无击杀时刻依赖。弱证据通道保留按死形态门槛：
        # 0904 HumStrafe 局的 fallback 轮（无官方窗、先验未接线时）hold
        # 0.97+ 且锯齿超阈，无门槛会误翻（0904 r7/r8 实测回归）。
        err_spike_rate = features.get("err_spike_rate_per_s")
        if (
            kill_rate is None
            and (hold_frac is None or hold_frac >= HELD_SWITCH_SAWTOOTH_MIN_HOLD_FRAC)
            and err_spike_rate is not None
            and err_spike_rate >= ERR_SPIKE_RATE_SWITCH_MIN
        ):
            return verdict(
                "target_switching",
                basis="heldfire_switch_by_err_sawtooth",
                basis_values={"err_spike_rate_per_s": err_spike_rate},
                target_count_model="sequential",
            )
        return verdict(
            "continuous_tracking",
            basis="sustained_hold_or_slow_fire",
            basis_values=tracking_reason,
            zero_kill_variant=official_kills in (None, 0),
        )

    precision_match = (
        err_lt1_share is not None and err_lt1_share > ERR_AT_CLICK_LT1_PRECISION_MIN
        and inter_click_cv is not None and inter_click_cv < INTER_CLICK_CV_PRECISION_MAX
        and ang_radius is not None and ang_radius < ANG_RADIUS_PRECISION_MAX_DEG
        and dist is not None and dist > DIST_PRECISION_MIN_CM
    )

    # 2. dynamic：目标持续运动（final_0831 校准：必须先于并发格分支）
    if moving_share is not None and moving_share > MOVING_TIME_SHARE_DYNAMIC_MIN:
        return verdict(
            "dynamic_clicking",
            basis="target_motion_share",
            basis_values={"moving_time_share": moving_share},
        )

    # 3. static_clicking 并发格（命中 precision 条件时细化子域）
    if alive_mean is not None and alive_mean > ALIVE_MEAN_CONCURRENT_MIN:
        return verdict(
            "static_clicking",
            subdomains=["precision"] if precision_match else (),
            basis="precision_static" if precision_match else "concurrent_targets",
            basis_values={"alive_mean": alive_mean},
            model="static",
            target_count_model="concurrent",
        )

    # 4. static_clicking + precision 子域（远、小、静目标）
    if precision_match:
        return verdict(
            "static_clicking",
            subdomains=["precision"],
            basis="precision_static",
            basis_values={
                "err_at_click_lt1deg_share": err_lt1_share,
                "inter_click_cv": inter_click_cv,
                "ang_radius_med": ang_radius,
                "dist_med": dist,
            },
            model="static",
        )

    # 5. 点击家族 + speed 子域（甩枪风格）：点击时刻尚未到位，或任务原条件
    #    （休息位误差大 + 节奏散）。
    flick_by_click_error = (
        err_at_click is not None and err_at_click > ERR_AT_CLICK_FLICK_MIN_DEG
    )
    flick_by_rest_error = (
        err_p10 is not None and err_p10 > ERR_P10_FLICK_MIN_DEG
        and inter_click_cv is not None and inter_click_cv > INTER_CLICK_CV_FLICK_MIN
    )
    if flick_by_click_error or flick_by_rest_error:
        return verdict(
            "static_clicking",
            subdomains=["speed"],
            basis="flick_speed",
            basis_values={
                "err_at_click_p50": err_at_click,
                "err_p10": err_p10,
                "inter_click_cv": inter_click_cv,
            },
        )

    # 6. reclick（血厚）：多发才一杀且点击时已到位。原 bearing_delta>60° →
    #    target_switching 分支已拆除（0901 实测签名反向：poll addr 池化与物件
    #    抖动产生的伪消亡使 Controlsphere 0 杀打出 75.96°>60°，而真转火
    #    TileFrenzy 仅 51.53°）；bearing_delta_at_kill_med 特征保留在束内仅作
    #    诊断，不进入任何判定分支。
    if (
        clicks_per_kill is not None and clicks_per_kill > CLICKS_PER_KILL_RECLICK_MIN
        and err_at_click is not None and err_at_click < ERR_AT_CLICK_RECLICK_MAX_DEG
    ):
        return verdict(
            "dynamic_clicking",
            subdomains=["control"],
            basis="reclick_thick_target",
            basis_values={"clicks_per_kill": clicks_per_kill},
        )
    return None


def build_scenario_observed_profile(
    round_dir: str | os.PathLike[str],
    round_number: int,
    *,
    file_names: Mapping[str, str] | None = None,
    official_window_epoch_ms: tuple[int, int] | None = None,
    official_window_t: tuple[float, float] | None = None,
    official_kills: int | None = None,
    official_scoring_penalizes_fire: bool | None = None,
) -> dict[str, Any]:
    """冻结旁车目录 -> scenario_observed_profile.v1（特征束 + 判别结论）。

    official_window_epoch_ms：官方挑战窗（canonical epoch ms，snapshot 的
    canonical_time_window），经 merge_manifest.alignment.s_epoch_of_t0 映射到
    旁车 t 域；official_window_t：已折算好的 t 域窗口（bb.json window_t 同域，
    供测试/上游直算）。两者都缺或映射后过短 → 全轮 + limitation。
    official_scoring_penalizes_fire：场景级计分结构先验（bool|None），透传
    判别树的转火一票否决，见 decide_scenario_family_verdict。
    """
    directory = Path(round_dir)
    if official_kills is not None and (isinstance(official_kills, bool) or official_kills < 0):
        raise ValueError("official_kills must be a non-negative int or None")
    if official_scoring_penalizes_fire is not None and not isinstance(
        official_scoring_penalizes_fire, bool
    ):
        raise ValueError("official_scoring_penalizes_fire must be a bool or None")
    name_overrides = {
        key: value
        for key, value in (file_names or {}).items()
        if isinstance(value, str) and value
    }
    frames_name = name_overrides.get("round", f"round_{round_number:02d}.jsonl")
    views_name = name_overrides.get("views", f"views_{round_number:02d}.jsonl")
    inputs_name = name_overrides.get("inputs", f"inputs_{round_number:02d}.jsonl")

    views = _load_views(directory, round_number, views_name)
    if not views:
        raise ValueError("telemetry sidecar views are unavailable")
    downs, ups = _load_button_edges(directory, round_number, inputs_name)
    positions, _first_t = _load_round_target_positions(directory, round_number, frames_name)
    radius_windows, _fallback_radius, _bb_limitations = _build_radius_lookup(directory)

    data_lo = views[0][0]
    data_hi = views[-1][0]
    requested_window: tuple[float, float] | None = None
    if official_window_t is not None:
        requested_window = (float(official_window_t[0]), float(official_window_t[1]))
    elif official_window_epoch_ms is not None:
        anchor = None
        manifest = _read_json(directory / "merge_manifest.json")
        if isinstance(manifest, Mapping):
            alignment = manifest.get("alignment")
            if isinstance(alignment, Mapping):
                try:
                    anchor = float(alignment["s_epoch_of_t0"])
                except (KeyError, TypeError, ValueError):
                    anchor = None
        if anchor is not None:
            requested_window = (
                official_window_epoch_ms[0] / 1000.0 - anchor,
                official_window_epoch_ms[1] / 1000.0 - anchor,
            )

    limitations: list[str] = []
    window_kind = WINDOW_OFFICIAL
    window_t: tuple[float, float] | None = None
    if requested_window is not None and requested_window[1] > requested_window[0]:
        lo = max(requested_window[0], data_lo)
        hi = min(requested_window[1], data_hi)
        if hi - lo >= MIN_FEATURE_WINDOW_S:
            window_t = (lo, hi)
    if window_t is None:
        window_kind = WINDOW_FULL_ROUND
        limitations.append(LIMITATION_FULL_ROUND_FALLBACK)
        window_t = (data_lo, data_hi)
    if not radius_windows:
        limitations.append(LIMITATION_GEOMETRY_ABSENT)

    features = compute_observed_features(
        views=views,
        downs=downs,
        ups=ups,
        positions=positions,
        radius_windows=radius_windows,
        window=window_t,
        official_kills=official_kills,
    )
    # 官方窗被采用时，pairing 挑战级 kills 才可归因（转火杀率分母用裁剪前的
    # 官方窗时长）；回退全轮时置 None，见 decide docstring 的归因规则。
    verdict = decide_scenario_family_verdict(
        features,
        official_kills=official_kills,
        official_window_duration_s=(
            requested_window[1] - requested_window[0]
            if window_kind == WINDOW_OFFICIAL
            else None
        ),
        official_scoring_penalizes_fire=official_scoring_penalizes_fire,
    )
    return {
        "schema_version": SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION,
        "round_dir_name": directory.name,
        "round": int(round_number),
        "window": {
            "kind": window_kind,
            "official_window_t": (
                [_round(requested_window[0], 3), _round(requested_window[1], 3)]
                if requested_window is not None
                else None
            ),
            "t_start": _round(window_t[0], 3),
            "t_end": _round(window_t[1], 3),
            "duration_s": _round(window_t[1] - window_t[0], 3),
        },
        "official_kills": official_kills,
        "features": features,
        "limitations": limitations,
        "verdict": verdict,
    }


# --- 合同校验（scenario_profiles 与测试复用；bounded、path-free） ---

_WINDOW_FIELDS = {"kind", "official_window_t", "t_start", "t_end", "duration_s"}
_VERDICT_FIELDS = {
    "aim_family", "subdomains", "target_motion", "basis", "basis_values",
    "zero_kill_variant",
}
_PROFILE_FIELDS = {
    "schema_version", "round_dir_name", "round", "window", "official_kills",
    "features", "limitations", "verdict",
}
_MAX_FEATURES = 64
_MAX_LIMITATIONS = 32


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def validate_scenario_observed_profile(value: object) -> dict[str, Any] | None:
    """校验 scenario_observed_profile.v1；不合法返回 None（该层静默让位）。"""
    if not isinstance(value, Mapping) or set(value) != _PROFILE_FIELDS:
        return None
    if value.get("schema_version") != SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION:
        return None
    round_dir_name = value.get("round_dir_name")
    round_number = value.get("round")
    if (
        not isinstance(round_dir_name, str)
        or not round_dir_name
        or len(round_dir_name) > 240
        or any(ord(char) < 32 for char in round_dir_name)
        or not isinstance(round_number, int)
        or isinstance(round_number, bool)
        or not 0 <= round_number <= 10_000
    ):
        return None
    window = value.get("window")
    if not isinstance(window, Mapping) or set(window) != _WINDOW_FIELDS:
        return None
    if window.get("kind") not in {WINDOW_OFFICIAL, WINDOW_FULL_ROUND}:
        return None
    for key in ("t_start", "t_end", "duration_s"):
        if not _finite_number(window.get(key)):
            return None
    official_span = window.get("official_window_t")
    if official_span is not None and (
        not isinstance(official_span, Sequence)
        or isinstance(official_span, str)
        or len(official_span) != 2
        or not all(item is None or _finite_number(item) for item in official_span)
    ):
        return None
    official_kills = value.get("official_kills")
    if official_kills is not None and (
        isinstance(official_kills, bool)
        or not isinstance(official_kills, int)
        or official_kills < 0
    ):
        return None
    features = value.get("features")
    if (
        not isinstance(features, Mapping)
        or len(features) > _MAX_FEATURES
        or not all(
            isinstance(key, str) and (item is None or _finite_number(item))
            for key, item in features.items()
        )
    ):
        return None
    limitations = value.get("limitations")
    if (
        not isinstance(limitations, Sequence)
        or isinstance(limitations, str)
        or len(limitations) > _MAX_LIMITATIONS
        or not all(isinstance(item, str) and item for item in limitations)
    ):
        return None
    verdict = value.get("verdict")
    if verdict is not None:
        if not isinstance(verdict, Mapping) or set(verdict) != _VERDICT_FIELDS:
            return None
        if verdict.get("aim_family") not in {
            "static_clicking", "dynamic_clicking", "continuous_tracking",
            "target_switching",
        }:
            return None
        subdomains = verdict.get("subdomains")
        if (
            not isinstance(subdomains, Sequence)
            or isinstance(subdomains, str)
            or len(subdomains) > 8
            or set(subdomains) - {
                "precision", "speed", "smooth", "reactive", "predictable",
                "control", "mixed",
            }
        ):
            return None
        motion = verdict.get("target_motion")
        if (
            not isinstance(motion, Mapping)
            or set(motion) != {"model", "target_count_model"}
            or motion.get("model") not in {"static", "predictable", "reactive", "mixed", "unknown"}
            or motion.get("target_count_model") not in {"single", "sequential", "concurrent", "unknown"}
        ):
            return None
        basis = verdict.get("basis")
        basis_values = verdict.get("basis_values")
        if (
            not isinstance(basis, str)
            or not basis.startswith("telemetry_observed_basis_")
            or len(basis) > 120
            or not isinstance(basis_values, Mapping)
            or len(basis_values) > 16
            or not all(
                isinstance(key, str) and (item is None or _finite_number(item))
                for key, item in basis_values.items()
            )
        ):
            return None
        if not isinstance(verdict.get("zero_kill_variant"), bool):
            return None
    return copy.deepcopy(dict(value))


__all__ = [
    "SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION",
    "SCENARIO_SCORING_PRIOR",
    "WINDOW_FULL_ROUND",
    "WINDOW_OFFICIAL",
    "build_scenario_observed_profile",
    "compute_observed_features",
    "decide_scenario_family_verdict",
    "scenario_scoring_penalizes_fire",
    "validate_scenario_observed_profile",
]
