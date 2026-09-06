"""telemetry_scenario_features 单元测试（合成旁车 fixture，小而快）。

判别树每个分支 + 特征计算单元（churn 过滤、dt 丢弃、wrap、官方窗映射）。
持续火力转火分支有 0901 真实样本支撑（TileFrenzy r3 翻转、Controlsphere
零翻转，见文末真实数据用例）；合成用例覆盖阈值两侧与归因规则。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from kovaak_tracker.scenario_profiles import resolve_scenario_profile
from kovaak_tracker.telemetry_scenario_features import (
    SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION,
    WINDOW_FULL_ROUND,
    WINDOW_OFFICIAL,
    build_scenario_observed_profile,
    validate_scenario_observed_profile,
)

FOV = 103.0
RATE_HZ = 25.0
DT = 1.0 / RATE_HZ


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _write_sidecar(
    round_dir: Path,
    *,
    views: list[dict],
    frames: list[dict],
    inputs: list[dict],
    bb_bots_radius: float | None = 60.0,
    s_epoch_of_t0: float | None = None,
) -> Path:
    round_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(round_dir / "views_01.jsonl", views)
    _write_jsonl(round_dir / "round_01.jsonl", frames)
    _write_jsonl(round_dir / "inputs_01.jsonl", inputs)
    if bb_bots_radius is not None:
        (round_dir / "bb.json").write_text(json.dumps({
            "schema_version": "round_bb.v1",
            "challenges": [{
                "scenario": "synthetic",
                "rounds": [1],
                "window_t": [views[0]["t"], views[-1]["t"]],
                "bots": [{"character": {"bb": {"radius": bb_bots_radius}}}],
            }],
        }), encoding="utf-8")
    if s_epoch_of_t0 is not None:
        (round_dir / "merge_manifest.json").write_text(json.dumps({
            "schema_version": "round_merge.v1",
            "alignment": {
                "method": "synthetic",
                "s_epoch_of_t0": s_epoch_of_t0,
                "accepted": True,
            },
        }), encoding="utf-8")
    return round_dir


def _views(
    duration_s: float,
    *,
    yaw_at,
    pos=(0.0, 0.0, 0.0),
    pitch=0.0,
) -> list[dict]:
    """duration_s 长、RATE_HZ 的 views 流；yaw_at(t) 给 yaw（度）。"""
    out = []
    t = 0.0
    steps = int(duration_s * RATE_HZ) + 1
    for index in range(steps):
        t = round(index * DT, 4)
        out.append({
            "t": t,
            "pos": list(pos),
            "rot": [pitch, yaw_at(t), 0.0],
            "fov": FOV,
        })
    return out


def _frames_from_lives(lives: list[tuple[float, float, float, float, float, int]]) -> list[dict]:
    """lives: [(t_start, t_end, x, y, z, addr)] -> 轮帧（按 RATE_HZ 采样）。"""
    frames: list[dict] = []
    if not lives:
        return frames
    t0 = min(life[0] for life in lives)
    t1 = max(life[1] for life in lives)
    steps = int((t1 - t0) * RATE_HZ) + 1
    for index in range(steps):
        t = round(t0 + index * DT, 4)
        targets = [
            [addr, x, y, z]
            for start, end, x, y, z, addr in lives
            if start <= t <= end
        ]
        frames.append({"ev": "frame", "t": t, "targets": targets})
    return frames


def _inputs_from_clicks(clicks: list[float], hold_s: float = 0.05) -> list[dict]:
    records: list[dict] = []
    for click in clicks:
        records.append({"t": click, "dx": 0, "dy": 0, "btn": ["L_down"]})
        records.append({
            "t": round(click + hold_s, 4),
            "dx": 0, "dy": 0, "btn": ["L_up"],
        })
    records.sort(key=lambda item: item["t"])
    return records


def _static_target_lives(
    duration_s: float,
    *,
    count: int,
    dist: float,
    yaw_spread_deg: float = 0.0,
    radius_offsets=(0.0, 0.0, 0.0),
) -> list[tuple[float, float, float, float, float, int]]:
    """count 个全程存活（删失）的静止目标，绕 yaw 均匀展开。"""
    lives = []
    for index in range(count):
        yaw = 0.0 if count == 1 else (
            -yaw_spread_deg / 2.0 + yaw_spread_deg * index / (count - 1)
        )
        rad = math.radians(yaw)
        lives.append((
            0.0, duration_s,
            dist * math.cos(rad),
            dist * math.sin(rad),
            radius_offsets[2],
            1000 + index,
        ))
    return lives


_PROFILE_ONLY_ARGS = (
    "official_kills", "official_window_epoch_ms", "official_window_t",
    "official_scoring_penalizes_fire",
)


def _profile(tmp_path: Path, name: str, **kwargs) -> dict:
    """写旁车 + 构建 profile；official_* 参数直传 build，不写文件。"""
    profile_args = {
        key: kwargs.pop(key) for key in list(kwargs) if key in _PROFILE_ONLY_ARGS
    }
    round_dir = _write_sidecar(tmp_path / name, **kwargs)
    return build_scenario_observed_profile(round_dir, 1, **profile_args)


# ---------------------------------------------------------------- 判别树分支


def test_static_concurrent_grid(tmp_path):
    duration = 16.0
    clicks = [round(1.0 + 0.5 * i, 4) for i in range(30)]
    profile = _profile(
        tmp_path, "grid",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=6, dist=2000.0)),
        inputs=_inputs_from_clicks(clicks),
    )
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "static_clicking"
    assert verdict["subdomains"] == []
    assert verdict["basis"] == "telemetry_observed_basis_concurrent_targets"
    assert verdict["target_motion"] == {"model": "static", "target_count_model": "concurrent"}
    # dist 2000 -> ang_radius ≈1.72°，落出 precision 条件。
    assert profile["features"]["ang_radius_med"] > 1.0
    assert profile["features"]["alive_mean"] > 4.0


def test_static_precision_subdomain(tmp_path):
    duration = 16.0
    clicks = [round(1.0 + 0.5 * i, 4) for i in range(28)]
    profile = _profile(
        tmp_path, "precision",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=2, dist=4000.0)),
        inputs=_inputs_from_clicks(clicks),
    )
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "static_clicking"
    assert verdict["subdomains"] == ["precision"]
    assert verdict["basis"] == "telemetry_observed_basis_precision_static"


def test_dynamic_moving_target(tmp_path):
    duration = 16.0
    clicks = [round(1.0 + 0.5 * i, 4) for i in range(28)]

    def yaw_at(t: float) -> float:
        phase = (t % 4.0) / 4.0
        return 20.0 * math.sin(2.0 * math.pi * phase)

    # 目标在 y 方向以 ~300cm/s 连续移动（振幅 200cm、周期 4s 附近取 sin 段），
    # 与准星同相位，误差保持小。
    def target_y(t: float) -> float:
        return 4000.0 * math.tan(math.radians(yaw_at(t)))

    lives = [(0.0, duration, 4000.0, 0.0, 0.0, 7)]
    frames = []
    steps = int(duration * RATE_HZ) + 1
    for index in range(steps):
        t = round(index * DT, 4)
        frames.append({
            "ev": "frame", "t": t,
            "targets": [[7, 4000.0, target_y(t), 0.0]],
        })
    profile = _profile(
        tmp_path, "dynamic",
        views=_views(duration, yaw_at=yaw_at),
        frames=frames,
        inputs=_inputs_from_clicks(clicks),
    )
    del lives
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "dynamic_clicking"
    assert verdict["basis"] == "telemetry_observed_basis_target_motion_share"
    assert profile["features"]["moving_time_share"] > 0.5
    assert profile["features"]["alive_mean"] <= 4.0


def test_tracking_sustained_hold_zero_kill_variant(tmp_path):
    duration = 16.0
    # 缓慢横移目标 + 全程按住（一个 L_down，无 L_up → 删失于窗末）。
    def yaw_at(t: float) -> float:
        return 5.0 * math.sin(2.0 * math.pi * t / 8.0)

    frames = []
    steps = int(duration * RATE_HZ) + 1
    for index in range(steps):
        t = round(index * DT, 4)
        y = 4000.0 * math.tan(math.radians(yaw_at(t)))
        frames.append({"ev": "frame", "t": t, "targets": [[9, 4000.0, y, 0.0]]})
    profile = _profile(
        tmp_path, "tracking",
        views=_views(duration, yaw_at=yaw_at),
        frames=frames,
        inputs=[{"t": 0.2, "dx": 0, "dy": 0, "btn": ["L_down"]}],
        bb_bots_radius=30.0,
    )
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "continuous_tracking"
    assert verdict["zero_kill_variant"] is True
    assert profile["official_kills"] is None
    assert profile["features"]["hold_frac"] > 0.6


def test_heldfire_switch_by_kill_rate(tmp_path):
    """①持续火力 + 官方杀率 >=0.2/s → 转火（主签名，0901 TileFrenzy 实证）。"""
    duration = 16.0
    # 全程按住（删失于窗末）+ 4 条顺序消亡的静止目标；官方窗 16s、kills=4
    # → 0.25/s。
    lives = [
        (round(2.0 + 2.5 * index, 4), round(4.0 + 2.5 * index, 4), 2000.0, 0.0, 0.0, 300 + index)
        for index in range(4)
    ]
    profile = _profile(
        tmp_path, "heldfire_switch_kr",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(lives),
        inputs=[{"t": 0.2, "dx": 0, "dy": 0, "btn": ["L_down"]}],
        official_window_t=(0.0, duration),
        official_kills=4,
    )
    verdict = profile["verdict"]
    assert profile["features"]["hold_frac"] > 0.6
    assert verdict["aim_family"] == "target_switching"
    assert verdict["basis"] == "telemetry_observed_basis_heldfire_switch_by_kill_rate"
    assert verdict["basis_values"] == {"official_kill_rate_per_s": 0.25}
    assert verdict["target_motion"] == {"model": "unknown", "target_count_model": "sequential"}
    assert verdict["zero_kill_variant"] is False
    assert validate_scenario_observed_profile(profile) is not None


def test_heldfire_zero_kill_rate_stays_tracking(tmp_path):
    """②持续火力 + 官方杀率 0 → 仍 tracking（invincible 变体不回归）。"""
    duration = 16.0
    lives = [
        (round(2.0 + 2.5 * index, 4), round(4.0 + 2.5 * index, 4), 2000.0, 0.0, 0.0, 300 + index)
        for index in range(4)
    ]
    profile = _profile(
        tmp_path, "heldfire_kr0",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(lives),
        inputs=[{"t": 0.2, "dx": 0, "dy": 0, "btn": ["L_down"]}],
        official_window_t=(0.0, duration),
        official_kills=0,
    )
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "continuous_tracking"
    assert verdict["basis"] == "telemetry_observed_basis_sustained_hold_or_slow_fire"
    assert verdict["zero_kill_variant"] is True


def test_heldfire_switch_by_err_sawtooth_fallback(tmp_path):
    """③持续火力 + 官方杀率缺失 + 误差锯齿率 >=0.3/s → 转火（兜底签名）。"""
    duration = 12.0
    # 准星贴住目标（误差≈0），每 2s 甩开 >15° 达 0.2s 再收回：4 次 excursion
    # / 12s = 0.333/s。official_window 缺失 → 杀率 None，只走锯齿兜底。
    swings = (2.0, 4.0, 6.0, 8.0)

    def yaw_at(t: float) -> float:
        return 30.0 if any(start <= t < start + 0.2 for start in swings) else 0.0

    profile = _profile(
        tmp_path, "heldfire_sawtooth",
        views=_views(duration, yaw_at=yaw_at),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=[{"t": 0.2, "dx": 0, "dy": 0, "btn": ["L_down"]}],
    )
    features = profile["features"]
    assert features["err_spike_rate_per_s"] == pytest.approx(4 / 12.0, abs=0.01)
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "target_switching"
    assert verdict["basis"] == "telemetry_observed_basis_heldfire_switch_by_err_sawtooth"
    assert verdict["basis_values"]["err_spike_rate_per_s"] == pytest.approx(0.3333, abs=0.001)
    assert verdict["target_motion"]["target_count_model"] == "sequential"
    assert validate_scenario_observed_profile(profile) is not None


def test_flick_speed_via_click_error(tmp_path):
    duration = 16.0
    # 两个相距 30° 的目标；点击时刻准星停在正中（对任一目标误差 ~15°）——甩枪签名。
    clicks = [round(1.0 + 0.45 * i, 4) for i in range(30)]
    profile = _profile(
        tmp_path, "flick",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=2, dist=3000.0, yaw_spread_deg=30.0)),
        inputs=_inputs_from_clicks(clicks),
        bb_bots_radius=40.0,
    )
    verdict = profile["verdict"]
    assert verdict["aim_family"] == "static_clicking"
    assert verdict["subdomains"] == ["speed"]
    assert verdict["basis"] == "telemetry_observed_basis_flick_speed"
    assert profile["features"]["err_at_click_p50"] > 2.0


def test_flick_speed_via_rest_error_and_irregular_rhythm(tmp_path):
    """任务原条件路径：err_p10>3° 且 inter_click_cv>0.6（点击时已到位）。"""
    duration = 16.0
    # 节奏散：间隔 0.2/0.5/1.3 交替 → cv 明显 >0.6；休息位偏向目标旁 >3°。
    intervals = [0.2, 0.5, 1.3] * 7
    clicks = []
    t = 1.0
    for interval in intervals:
        clicks.append(round(t, 4))
        t += interval
    profile = _profile(
        tmp_path, "flick_rest",
        views=_views(duration, yaw_at=lambda t: 8.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=3000.0)),
        inputs=_inputs_from_clicks(clicks),
        bb_bots_radius=40.0,
    )
    verdict = profile["verdict"]
    assert profile["features"]["inter_click_cv"] > 0.6
    assert profile["features"]["err_p10"] > 3.0
    assert verdict["aim_family"] == "static_clicking"
    assert verdict["subdomains"] == ["speed"]


def test_bearing_delta_feature_kept_but_no_branch(tmp_path):
    """⑤bearing_delta>60° 不再触发转火：branch-6 已拆除（0901 实测签名反向）。

    特征本身保留在束内（诊断价值）；该合成场景（交替 ±40° 方位、bearing_delta
    远超旧 60° 阈值）在新树中无分支命中 → verdict None。
    """
    duration = 12.0
    lives = []
    click_targets = []
    slot = 3.0
    for index in range(3):
        yaw = 40.0 if index % 2 == 0 else -40.0
        rad = math.radians(yaw)
        start = round(index * slot + 0.5, 4)
        end = round(start + slot - 0.5, 4)
        lives.append((start, end, 1500.0 * math.cos(rad), 1500.0 * math.sin(rad), 0.0, 500 + index))
        click_targets.append((start + slot - 0.9, yaw))
    # 每条生命 3 发点击（避免 cpm<30 误入持续火力分支）；准星持续贴住当前
    # 存活目标（误差≈0），确保旧树里只有 bearing 信号能命中该分支。
    inputs = []
    for (t, _yaw) in click_targets:
        for shot in range(3):
            click = round(t + shot * 0.25, 4)
            inputs.append({"t": click, "dx": 0, "dy": 0, "btn": ["L_down"]})
            inputs.append({"t": round(click + 0.05, 4), "dx": 0, "dy": 0, "btn": ["L_up"]})
    inputs.sort(key=lambda item: item["t"])

    # 准星跟随当前存活目标（逐段贴住 ±40° 方位）。
    def yaw_at(t: float) -> float:
        current = 0.0
        for index, (start, end, _x, _y, _z, _addr) in enumerate(lives):
            if start <= t <= end:
                return 40.0 if index % 2 == 0 else -40.0
            if t >= end:
                current = 40.0 if index % 2 == 0 else -40.0
        return current

    profile = _profile(
        tmp_path, "switching",
        views=_views(duration, yaw_at=yaw_at),
        frames=_frames_from_lives(lives),
        inputs=inputs,
        bb_bots_radius=50.0,
    )
    assert profile["features"]["bearing_delta_at_kill_med"] > 60.0
    assert profile["verdict"] is None


def test_reclick_thick_target(tmp_path):
    """血厚目标：每杀 3 发、点击时已到位 → reclick。"""
    duration = 16.0
    lives = []
    clicks = []
    t = 0.5
    kill_index = 0
    while t < duration - 1.0:
        # 一条 1.5s 生命，期间 3 发点击（发发指向目标），生命中途死。
        life_end = round(t + 1.2, 4)
        lives.append((t, life_end, 1500.0, 0.0, 0.0, 800 + kill_index))
        for shot in range(3):
            clicks.append(round(t + 0.2 + shot * 0.3, 4))
        t = round(life_end + 0.3, 4)
        kill_index += 1
    profile = _profile(
        tmp_path, "reclick",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(lives),
        inputs=_inputs_from_clicks(clicks, hold_s=0.04),
        official_kills=len(lives),
    )
    verdict = profile["verdict"]
    assert profile["features"]["clicks_per_kill"] > 1.5
    assert profile["features"]["err_at_click_p50"] < 1.0
    assert verdict["aim_family"] == "dynamic_clicking"
    assert verdict["subdomains"] == ["control"]
    assert verdict["basis"] == "telemetry_observed_basis_reclick_thick_target"


def test_no_branch_match_returns_none_and_waterfall_falls_through(tmp_path):
    duration = 16.0
    clicks = [round(1.0 + 0.5 * i, 4) for i in range(20)]
    profile = _profile(
        tmp_path, "undecided",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=2, dist=2000.0)),
        inputs=_inputs_from_clicks(clicks),
    )
    # 官方 kills 缺失 → clicks_per_kill absent；2 目标 dist 2000：并发格不触发
    # （alive=2）、precision 不触发（dist<3000）、无运动、误差居中（准星在目标
    # 上，误差 ~0 → speed/reclick 都不触发，cpk absent）→ 无分支命中。
    profile["features"]["err_at_click_p50"] = 1.5
    assert profile["verdict"] is None
    resolution = resolve_scenario_profile(
        "unreviewed-hash", display_name="Mega Target Grader", observed_profile=profile,
    )
    assert resolution["classification_source"] == "name_heuristic"


# ---------------------------------------------------------------- 特征单元


def test_churn_filter_excludes_short_lives_from_alive_mean(tmp_path):
    duration = 10.0
    base = _static_target_lives(duration, count=1, dist=2000.0)
    views = _views(duration, yaw_at=lambda t: 0.0)
    frames = _frames_from_lives(base)
    # 场景衍生对象：0.1s 闪现（<200ms），必须被 churn 过滤。
    frames += [
        {"ev": "frame", "t": round(5.0 + index * DT, 4), "targets": [[99, 2000.0, 0.0, 0.0]]}
        for index in range(2)
    ]
    frames.sort(key=lambda item: item["t"])
    profile = _profile(
        tmp_path, "churn", views=views, frames=frames,
        inputs=_inputs_from_clicks([2.0, 4.0]),
    )
    assert profile["features"]["alive_mean"] == pytest.approx(1.0, abs=0.01)


def test_omega_drops_samples_with_dt_above_limit(tmp_path):
    # yaw 在 t<1 保持 0，t=1.5 跳到 30°（views 断流 0.5s > 0.2s 上限），
    # 之后 5°/s 慢转。跳变若未丢弃，omega_p99 会被 60°/s 抬高。
    views = []
    for index in range(25):
        views.append({"t": round(index * DT, 4), "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": FOV})
    for index in range(38, 63):
        t = round(index * DT, 4)
        yaw = 30.0 + 5.0 * (t - 1.52)
        views.append({"t": t, "pos": [0.0, 0.0, 0.0], "rot": [0.0, yaw, 0.0], "fov": FOV})
    frames = _frames_from_lives(_static_target_lives(2.6, count=1, dist=4000.0))
    profile = _profile(
        tmp_path, "dt_drop", views=views, frames=frames, inputs=[],
    )
    assert profile["features"]["omega_p99"] < 10.0


def test_yaw_wrap_keeps_small_omega_and_no_false_flip(tmp_path):
    # yaw 179.9 → -179.9（wrap ±180 后实际差 0.2°）。
    views = [
        {"t": 0.0, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 179.9, 0.0], "fov": FOV},
        {"t": 0.04, "pos": [0.0, 0.0, 0.0], "rot": [0.0, -179.9, 0.0], "fov": FOV},
        {"t": 0.08, "pos": [0.0, 0.0, 0.0], "rot": [0.0, -179.7, 0.0], "fov": FOV},
    ]
    frames = _frames_from_lives([(0.0, 0.1, 4000.0, 0.0, 0.0, 3)])
    profile = _profile(
        tmp_path, "wrap", views=views,
        frames=frames, inputs=[],
        bb_bots_radius=None,
    )
    features = profile["features"]
    assert features["omega_p99"] < 10.0
    assert features["dir_flips_per_s"] == 0.0


def test_official_window_epoch_mapping_and_clipping(tmp_path):
    duration = 20.0
    clicks = [1.0] + [round(6.0 + 0.5 * i, 4) for i in range(20)]
    round_dir = _write_sidecar(
        tmp_path / "window",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=_inputs_from_clicks(clicks),
        s_epoch_of_t0=1_000_000.0,
    )
    profile = build_scenario_observed_profile(
        round_dir, 1,
        official_window_epoch_ms=(1_000_003_000, 1_000_013_000),
        official_kills=10,
    )
    assert profile["window"]["kind"] == WINDOW_OFFICIAL
    assert profile["window"]["t_start"] == pytest.approx(3.0, abs=0.01)
    assert profile["window"]["t_end"] == pytest.approx(13.0, abs=0.01)
    # 窗外点击不计：窗内 6.0..13.0 共 15 发。
    assert profile["features"]["clicks_per_min"] == pytest.approx(15 / (10.0 / 60.0), rel=0.01)
    assert profile["features"]["clicks_per_kill"] == pytest.approx(1.5, rel=0.01)


def test_official_window_absent_falls_back_to_full_round(tmp_path):
    duration = 10.0
    profile = _profile(
        tmp_path, "fallback",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=_inputs_from_clicks([2.0, 4.0, 6.0]),
    )
    assert profile["window"]["kind"] == WINDOW_FULL_ROUND
    assert "observed_window_full_round_fallback" in profile["limitations"]


def test_official_window_too_short_falls_back(tmp_path):
    duration = 10.0
    round_dir = _write_sidecar(
        tmp_path / "short",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=_inputs_from_clicks([2.0, 4.0]),
        s_epoch_of_t0=1_000_000.0,
    )
    profile = build_scenario_observed_profile(
        round_dir, 1,
        official_window_epoch_ms=(1_000_001_000, 1_000_003_000),  # 2s < 5s 下限
    )
    assert profile["window"]["kind"] == WINDOW_FULL_ROUND


def test_bb_absent_geometry_absent_no_fallback(tmp_path):
    duration = 10.0
    profile = _profile(
        tmp_path, "nobb",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=_inputs_from_clicks([2.0, 4.0]),
        bb_bots_radius=None,
    )
    features = profile["features"]
    assert features["ang_radius_med"] is None
    assert features["dist_med"] is None
    assert "observed_bb_absent_geometry_features_absent" in profile["limitations"]
    # 几何缺失时 precision 分支不可能命中。
    assert profile["verdict"] is None or profile["verdict"]["subdomains"] != ["precision"]


def test_hold_frac_mean_hold_and_cpm(tmp_path):
    duration = 10.0
    profile = _profile(
        tmp_path, "holds",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=_inputs_from_clicks([2.0, 2.6, 5.0], hold_s=0.1),
    )
    features = profile["features"]
    assert features["hold_frac"] == pytest.approx(0.03, abs=0.001)  # 3×0.1s / 10s
    assert features["mean_hold_ms"] == pytest.approx(100.0, abs=0.5)
    assert features["clicks_per_min"] == pytest.approx(3 / (10.0 / 60.0), rel=0.01)


def test_validator_rejects_corrupt_profile(tmp_path):
    duration = 6.0
    profile = _profile(
        tmp_path, "valid",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
        inputs=[],
    )
    assert validate_scenario_observed_profile(profile) is not None
    extra = dict(profile, unexpected=1)
    assert validate_scenario_observed_profile(extra) is None
    bad_family = json.loads(json.dumps(profile))
    bad_family["verdict"]["aim_family"] = "flicking"
    assert validate_scenario_observed_profile(bad_family) is None
    bad_schema = dict(profile, schema_version="scenario_observed_profile.v2")
    assert validate_scenario_observed_profile(bad_schema) is None
    assert validate_scenario_observed_profile(None) is None


def test_switch_scoring_prior_veto_and_no_hold_gate():
    """0906 方向修正：转火判定=计分结构先验一票否决+行为签名，无 hold 硬门槛。

    - Humanoid Strafe 真实形态（hold 0.9455、中间松手过、锯齿 0.4992/s）：
      罚分结构先验 True → 一票否决落 tracking（新 basis）；锯齿兜底在先验
      True 下也不生效。
    - 真转火打失误也会松手：hold 0.90 + 官方杀率 1.4/s + 计分中性 → 转火
      （9b5886a 的 hold>=0.97 硬门槛撤销后放行；杀率签名只看杀率）。
    - 无先验（None）时退化为行为签名：同样放行杀率/锯齿路径。
    """
    from kovaak_tracker.telemetry_scenario_features import (
        decide_scenario_family_verdict,
    )

    hum_strafe = {
        "hold_frac": 0.9455,
        "clicks_per_min": 8.0,
        "err_spike_rate_per_s": 0.4992,
    }
    vetoed = decide_scenario_family_verdict(
        hum_strafe,
        official_kills=None,
        official_window_duration_s=None,
        official_scoring_penalizes_fire=True,
    )
    assert vetoed is not None
    assert vetoed["aim_family"] == "continuous_tracking"
    assert vetoed["basis"] == "telemetry_observed_basis_sustained_hold_scoring_penalizes_fire"
    assert vetoed["basis_values"] == {"hold_frac": 0.9455}

    vetoed_held = decide_scenario_family_verdict(
        dict(hum_strafe, hold_frac=0.981),
        official_kills=85,
        official_window_duration_s=60.5,
        official_scoring_penalizes_fire=True,
    )
    assert vetoed_held["aim_family"] == "continuous_tracking"
    assert vetoed_held["zero_kill_variant"] is False

    relaxed = decide_scenario_family_verdict(
        {"hold_frac": 0.90, "clicks_per_min": 2.0},
        official_kills=85,
        official_window_duration_s=60.5,
    )
    assert relaxed is not None
    assert relaxed["aim_family"] == "target_switching"
    assert relaxed["basis"] == "telemetry_observed_basis_heldfire_switch_by_kill_rate"
    assert relaxed["basis_values"] == {"official_kill_rate_per_s": pytest.approx(1.405, abs=0.001)}

    # 锯齿兜底是弱证据通道（无官方窗/kills）：保留按死形态门槛——0904
    # HumStrafe 局 fallback 轮（hold 0.97+、锯齿 0.3+/s）无门槛会误翻。
    sawtooth_below_gate = decide_scenario_family_verdict(
        {"hold_frac": 0.90, "clicks_per_min": 2.0, "err_spike_rate_per_s": 0.91},
        official_kills=None,
        official_window_duration_s=None,
    )
    assert sawtooth_below_gate["aim_family"] == "continuous_tracking"
    sawtooth_above_gate = decide_scenario_family_verdict(
        {"hold_frac": 0.98, "clicks_per_min": 2.0, "err_spike_rate_per_s": 0.91},
        official_kills=None,
        official_window_duration_s=None,
    )
    assert sawtooth_above_gate["aim_family"] == "target_switching"
    assert sawtooth_above_gate["basis"] == "telemetry_observed_basis_heldfire_switch_by_err_sawtooth"


def test_scenario_scoring_prior_lookup():
    """先验表查询：casefold+strip 匹配；未收录/空名 → None（无先验）。"""
    from kovaak_tracker.telemetry_scenario_features import (
        SCENARIO_SCORING_PRIOR,
        scenario_scoring_penalizes_fire,
    )

    assert scenario_scoring_penalizes_fire("Tile Frenzy 180 Strafing Tracking") is False
    assert scenario_scoring_penalizes_fire("  humanoid strafe FLAT ") is True
    assert scenario_scoring_penalizes_fire("1w2ts reload") is None
    assert scenario_scoring_penalizes_fire("") is None
    assert scenario_scoring_penalizes_fire(None) is None
    # 表内条目只允许 bool（先验语义无第三态）。
    assert all(isinstance(v, bool) for v in SCENARIO_SCORING_PRIOR.values())


def test_build_rejects_non_bool_scoring_prior(tmp_path):
    duration = 6.0
    with pytest.raises(ValueError, match="official_scoring_penalizes_fire"):
        _profile(
            tmp_path, "bad_prior",
            views=_views(duration, yaw_at=lambda t: 0.0),
            frames=_frames_from_lives(_static_target_lives(duration, count=1, dist=2000.0)),
            inputs=[],
            official_scoring_penalizes_fire="yes",
        )


def test_profile_schema_version():
    assert SCENARIO_OBSERVED_PROFILE_SCHEMA_VERSION == "scenario_observed_profile.v1"


# ---------------------------------------------------------------- 瀑布接入


def test_resolution_watermark_precedence(tmp_path):
    duration = 16.0
    clicks = [round(1.0 + 0.5 * i, 4) for i in range(28)]
    profile = _profile(
        tmp_path, "waterfall",
        views=_views(duration, yaw_at=lambda t: 0.0),
        frames=_frames_from_lives(_static_target_lives(duration, count=2, dist=4000.0)),
        inputs=_inputs_from_clicks(clicks),
    )
    assert profile["verdict"]["aim_family"] == "static_clicking"
    assert profile["verdict"]["subdomains"] == ["precision"]

    # observed 层产出 candidate resolution，descriptive only。
    resolution = resolve_scenario_profile(
        "unreviewed-hash", display_name="Precision Far Targets", observed_profile=profile,
    )
    assert resolution["classification_source"] == "telemetry_observed"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["subdomains"] == ["precision"]
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert "telemetry_observed_is_a_statistical_candidate_not_an_identity" in resolution["limitations"]
    assert any(
        item.startswith("telemetry_observed_basis_")
        for item in resolution["limitations"]
    )
    assert validate_scenario_observed_profile(profile) is not None

    # reviewed profile 与本地 .sce 优先于 observed 层（此处用 reviewed hash）。
    exact = resolve_scenario_profile(
        "b2ae4a24b710e36afc6e57c61f590ab4",
        display_name="WHJ SmoothStrafeSphere Easy",
        observed_profile=profile,
    )
    assert exact["classification_source"] == "reviewed_registry"

    # observed 层高于 challenge_shape 与 name/default：shape 只在 observed
    # 让位（verdict None）时才被咨询。
    shape = {
        "schema_version": "scenario_challenge_shape.v1",
        "kills": 0,
        "duration_ms": 30_000,
        "button_samples_held": 12_000,
    }
    refined = resolve_scenario_profile(
        "unreviewed-hash",
        display_name="Smooth Strafe Sphere",
        challenge_shape=shape,
        observed_profile=profile,
    )
    assert refined["classification_source"] == "telemetry_observed"


# ---------------------------------------------------------------- 真实数据验收
#
# final_0831 六局对照（上游 FPSAimTrainer analysis/external/cleaned，只读；
# 目录不存在时整段 SKIP，与 test_telemetry_signals.py 同组织方式）。
# 官方 kills 来自上游 perf 回执（crosscheck_final_report.json，in_window 条目；
# Controlsphere 无 kills 记录 → None）。

_REAL_CLEANED = Path(
    r"C:\Users\袜子\Desktop\FPSAimTrainer\analysis\external\cleaned"
)
_REAL_SESSION = _REAL_CLEANED / "final_0831" / "target_poll_out_0831_003140"

_REAL_CASES = [
    # (局名, 轮号, bb window_t, 官方 kills, 计分先验, 预期 family, 预期 subdomains)
    ("beanClick", 2, (83.967, 144.45), 121, None, "static_clicking", []),
    ("1wall6targets", 3, (196.892, 257.312), 111, None, "static_clicking", ["precision"]),
    ("pasu", 3, (271.312, 357.511), 109, None, "dynamic_clicking", []),
    ("Controlsphere", 3, (366.632, 427.122), None, None, "continuous_tracking", []),
    ("Humanoid Strafe", 6, (442.932, 557.892), 10, True, "continuous_tracking", []),
    ("Valorant Flick", 12, (581.372, 641.855), 47, None, "static_clicking", ["speed"]),
]

_requires_real_sidecars = pytest.mark.skipif(
    not _REAL_SESSION.is_dir(),
    reason="external telemetry sidecar data is not available on this machine",
)


@_requires_real_sidecars
@pytest.mark.parametrize(
    ("name", "round_number", "window_t", "kills", "scoring_prior", "want_family", "want_subdomains"),
    _REAL_CASES,
)
def test_final0831_real_challenge_windows(
    name: str,
    round_number: int,
    window_t: tuple[float, float],
    kills: int | None,
    scoring_prior: bool | None,
    want_family: str,
    want_subdomains: list[str],
):
    profile = build_scenario_observed_profile(
        _REAL_SESSION,
        round_number,
        official_window_t=window_t,
        official_kills=kills,
        official_scoring_penalizes_fire=scoring_prior,
    )
    assert validate_scenario_observed_profile(profile) is not None
    verdict = profile["verdict"]
    features = {
        key: profile["features"][key]
        for key in (
            "hold_frac", "clicks_per_min", "clicks_per_kill", "err_at_click_p50",
            "err_at_click_lt1deg_share", "err_p10", "inter_click_cv", "alive_mean",
            "bearing_delta_at_kill_med", "ang_radius_med", "dist_med",
            "moving_time_share", "omega_p99",
        )
    }
    print(f"\n{name}: basis={verdict['basis'] if verdict else None}")
    print(f"  features={json.dumps(features, ensure_ascii=False)}")
    assert verdict is not None, f"{name}: no verdict"
    assert verdict["aim_family"] == want_family, (
        f"{name}: family {verdict['aim_family']} != {want_family}; features={features}"
    )
    assert verdict["subdomains"] == want_subdomains, (
        f"{name}: subdomains {verdict['subdomains']} != {want_subdomains}"
    )
    if name == "Controlsphere":
        assert verdict["zero_kill_variant"] is True
    if name == "Humanoid Strafe":
        assert verdict["zero_kill_variant"] is False
    # 真实 profile 驱动 waterfall 层产出可通过合同的 resolution。
    resolution = resolve_scenario_profile(
        f"real-{name.casefold()}", display_name=name, observed_profile=profile,
    )
    assert resolution["classification_source"] == "telemetry_observed"
    assert resolution["aim_family"] == want_family


# session_0901 两局：持续火力转火判定的真实样本（官方窗+kills 来自上游
# crosscheck_session_0901_* 回执，只读）。TileFrenzy r3 是本分支要捕捉的翻转
# （tracking→switching），Controlsphere r3 是零杀纯跟枪对照（不得翻转）。
# 计分先验来自场景级表（.perf score 事件实测：TileFrenzy 零负 delta → False）。

_REAL_SESSIONS_0901 = [
    # (局名, 旁车目录, 轮号, bb window_t, 官方 kills, 计分先验, 预期 family)
    (
        "Controlsphere 0901",
        _REAL_CLEANED / "session_0901_2132" / "target_poll_out_0901_213256",
        3, (275.052, 335.538), 0, None, "continuous_tracking",
    ),
    (
        "Tile Frenzy 0901",
        _REAL_CLEANED / "session_0901_2156" / "target_poll_out_0901_215050",
        3, (262.157, 322.635), 85, False, "target_switching",
    ),
]

_requires_real_0901 = pytest.mark.skipif(
    not all(session_dir.is_dir() for _n, session_dir, *_rest in _REAL_SESSIONS_0901),
    reason="session_0901 telemetry sidecar data is not available on this machine",
)


@_requires_real_0901
@pytest.mark.parametrize(
    ("name", "session_dir", "round_number", "window_t", "kills", "scoring_prior", "want_family"),
    _REAL_SESSIONS_0901,
)
def test_session0901_heldfire_switching(
    name: str,
    session_dir: Path,
    round_number: int,
    window_t: tuple[float, float],
    kills: int,
    scoring_prior: bool | None,
    want_family: str,
):
    profile = build_scenario_observed_profile(
        session_dir,
        round_number,
        official_window_t=window_t,
        official_kills=kills,
        official_scoring_penalizes_fire=scoring_prior,
    )
    assert validate_scenario_observed_profile(profile) is not None
    verdict = profile["verdict"]
    print(f"\n{name}: family={verdict['aim_family']} basis={verdict['basis']}")
    print(f"  err_spike_rate_per_s={profile['features']['err_spike_rate_per_s']}")
    assert verdict["aim_family"] == want_family
    if want_family == "target_switching":
        assert verdict["basis"] == "telemetry_observed_basis_heldfire_switch_by_kill_rate"
        assert verdict["target_motion"]["target_count_model"] == "sequential"
    else:
        assert verdict["zero_kill_variant"] is True


# session_0904 三局（官方窗由 perf start_unix - merge_manifest t0 折算，
# crosscheck_session_0904 回执提供 kills，只读）。TileFrenzy r2 是转火正例
# （84 杀/60.5s、计分中性）；Pasu r5 是动态点击对照；1w2ts r11 是静态点击
# 对照。Humanoid Strafe 0904 局的旁车映射失败（members=-），不进轮级断言，
# 其罚分结构由 0831 Flat 局与先验表单测覆盖。

_REAL_SESSION_0904 = _REAL_CLEANED / "session_0904_2359" / "target_poll_out_0904_234457"

_REAL_SESSIONS_0904 = [
    # (局名, 轮号, 官方窗 t 域, 官方 kills, 计分先验, 预期 family)
    (
        "Tile Frenzy 0904",
        2, (151.261, 211.744), 84, False, "target_switching",
    ),
    (
        "Pasu SuperbAim 0904",
        5, (582.261, 642.734), 69, None, "dynamic_clicking",
    ),
    (
        "1w2ts reload 0904",
        11, (806.261, 866.721), 92, None, "static_clicking",
    ),
]

_requires_real_0904 = pytest.mark.skipif(
    not _REAL_SESSION_0904.is_dir(),
    reason="session_0904 telemetry sidecar data is not available on this machine",
)


@_requires_real_0904
@pytest.mark.parametrize(
    ("name", "round_number", "window_t", "kills", "scoring_prior", "want_family"),
    _REAL_SESSIONS_0904,
)
def test_session0904_challenge_windows(
    name: str,
    round_number: int,
    window_t: tuple[float, float],
    kills: int,
    scoring_prior: bool | None,
    want_family: str,
):
    profile = build_scenario_observed_profile(
        _REAL_SESSION_0904,
        round_number,
        official_window_t=window_t,
        official_kills=kills,
        official_scoring_penalizes_fire=scoring_prior,
    )
    assert validate_scenario_observed_profile(profile) is not None
    verdict = profile["verdict"]
    print(f"\n{name}: family={verdict['aim_family']} basis={verdict['basis']}")
    print(f"  features={json.dumps({k: profile['features'][k] for k in ('hold_frac', 'clicks_per_min', 'moving_time_share', 'alive_mean', 'err_spike_rate_per_s')})}")
    assert verdict is not None, f"{name}: no verdict"
    assert verdict["aim_family"] == want_family, (
        f"{name}: family {verdict['aim_family']} != {want_family}"
    )
    if want_family == "target_switching":
        assert verdict["basis"] == "telemetry_observed_basis_heldfire_switch_by_kill_rate"
        assert verdict["target_motion"]["target_count_model"] == "sequential"
