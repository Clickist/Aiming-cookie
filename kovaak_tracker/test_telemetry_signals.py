"""telemetry_signals 端到端测试。

真实数据在另一个仓库（FPSAimTrainer analysis/external/cleaned，SIDECARS 合同），
只读消费、不入 git；目录不存在时整测 SKIP。几何自检是 producer 正确性的
黄金验收：击杀点击瞬间准心->垂死目标的角误差中位 <1.0°（SIDECARS §5/§7 实测
final_0831 ~0.466°、verify0831 ~0.231°）。
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from pathlib import Path

import pytest

from kovaak_tracker.analysis_evidence import (
    validate_event_bundle_v1,
    validate_signal_bundle_v1,
)
from kovaak_tracker.telemetry_signals import (
    TELEMETRY_PRODUCER_VERSION,
    VIEWPORT_HEIGHT_PX,
    VIEWPORT_WIDTH_PX,
    build_telemetry_visual_result,
    project_world_to_viewport,
)

# 真实数据只读，位于另一仓库；不能进本仓库 git。
DATA_ROOT = Path(
    r"C:\Users\袜子\Desktop\FPSAimTrainer\analysis\external\cleaned"
)
SESSIONS = (
    DATA_ROOT / "final_0831" / "target_poll_out_0831_003140",
    DATA_ROOT / "verify0831" / "target_poll_out_0831_192538",
)


# ---------- 纯几何单测（不依赖外部数据，永远运行） ----------


def test_projection_center_and_axes():
    # 相机在原点、yaw=0 朝 +x；正前方目标必须落在视口中心。
    px_x, px_y, radius = project_world_to_viewport(
        target=(4000.0, 0.0, 0.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(0.0, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert (px_x, px_y) == (VIEWPORT_WIDTH_PX / 2.0, VIEWPORT_HEIGHT_PX / 2.0)
    # 像素半径 = atan(r/d) * f（针孔焦距，横竖轴共用）。
    focal_px = (VIEWPORT_WIDTH_PX / 2.0) / math.tan(math.radians(103.0 / 2.0))
    expected_radius = math.atan2(60.0, 4000.0) * focal_px
    assert radius == pytest.approx(expected_radius)


def test_projection_follows_pinhole_model():
    # rectilinear 针孔投影：px = f*tan(θ)，f = (W/2)/tan(hfov/2)。
    # 103° 时 f≈763.6（5° 偏轴 ≈66.8px；线性角度投影会给出 ~76.5px）。
    focal_px = (VIEWPORT_WIDTH_PX / 2.0) / math.tan(math.radians(103.0 / 2.0))
    px_x, px_y, _ = project_world_to_viewport(
        target=(4000.0, 4000.0 * math.tan(math.radians(5.0)), 0.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(0.0, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert px_x == pytest.approx(
        VIEWPORT_WIDTH_PX / 2.0 + focal_px * math.tan(math.radians(5.0)),
    )
    px_x2, _, _ = project_world_to_viewport(
        target=(4000.0, 4000.0 * math.tan(math.radians(30.0)), 0.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(0.0, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert px_x2 == pytest.approx(
        VIEWPORT_WIDTH_PX / 2.0 + focal_px * math.tan(math.radians(30.0)),
    )


def test_projection_pitch_yaw_directions():
    # UE4 约定：pitch 向上为正 -> 目标偏上时 py 减小；yaw 增大 -> px 增大。
    _ , up_y, _ = project_world_to_viewport(
        target=(4000.0, 0.0, 500.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(0.0, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert up_y < VIEWPORT_HEIGHT_PX / 2.0
    right_x, _, _ = project_world_to_viewport(
        target=(4000.0, 500.0, 0.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(0.0, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert right_x > VIEWPORT_WIDTH_PX / 2.0
    # 相机自身俯仰抬高后，同一目标回到接近中心。
    pitch_deg = math.degrees(math.atan2(500.0, 4000.0))
    centered_x, centered_y, _ = project_world_to_viewport(
        target=(4000.0, 0.0, 500.0),
        camera_pos=(0.0, 0.0, 0.0),
        rot=(pitch_deg, 0.0, 0.0),
        horizontal_fov_deg=103.0,
        target_radius_cm=60.0,
    )
    assert centered_y == pytest.approx(VIEWPORT_HEIGHT_PX / 2.0, abs=1e-6)


def test_projection_rejects_targets_behind_camera():
    with pytest.raises(ValueError):
        project_world_to_viewport(
            target=(-4000.0, 0.0, 0.0),
            camera_pos=(0.0, 0.0, 0.0),
            rot=(0.0, 0.0, 0.0),
            horizontal_fov_deg=103.0,
            target_radius_cm=60.0,
        )


# ---------- 真实旁车端到端（目录缺失则 SKIP） ----------


def _load_json(path: Path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
    return records


def _index_entry(round_dir: Path, round_number: int) -> tuple[dict | None, dict | None]:
    """(rounds_index 该轮条目, rounds_index 全文)；与 manifest 的绝对路径按 basename 匹配。"""
    manifest = _load_json(round_dir / "merge_manifest.json")
    index = _load_json(Path(manifest["rounds_index"]))
    base = Path(manifest["round_dir"]).name
    for source in index.get("sources", []):
        if Path(source.get("outdir", "")).name != base:
            continue
        for entry in source.get("rounds", []):
            if entry.get("round") == round_number:
                return entry, index
    return None, index


def _canonical_window(round_dir: Path, round_number: int) -> tuple[float, float]:
    entry, _ = _index_entry(round_dir, round_number)
    if entry is None:
        return (0.0, 600_000.0)
    duration_ms = (float(entry["t_end"]) - float(entry["t_start"]) + 1.0) * 1000.0
    return (0.0, duration_ms)


def _available_cases() -> list[tuple[Path, int]]:
    cases: list[tuple[Path, int]] = []
    for session in SESSIONS:
        if not session.is_dir():
            continue
        manifest = _load_json(session / "merge_manifest.json")
        index = _load_json(Path(manifest["rounds_index"]))
        base = Path(manifest["round_dir"]).name
        for source in index.get("sources", []):
            if Path(source.get("outdir", "")).name != base:
                continue
            for entry in source.get("rounds", []):
                cases.append((session, int(entry["round"])))
    return cases


def _round_ids(round_dir: Path) -> list[int]:
    return [round_number for session_dir, round_number in _available_cases() if session_dir == round_dir]


@pytest.fixture(scope="module")
def built_results():
    """module 级缓存：{(round_dir, round_number): visual_result}，避免重复投影。"""
    cache: dict[tuple[Path, int], dict] = {}
    cases = _available_cases()
    if not cases:
        pytest.skip("external telemetry sidecar data is not available on this machine")
    for round_dir, round_number in cases:
        cache[(round_dir, round_number)] = build_telemetry_visual_result(
            round_dir,
            round_number,
            canonical_window=_canonical_window(round_dir, round_number),
        )
    return cache


def test_visual_result_shape_and_evidence_validation(built_results):
    for (round_dir, round_number), result in built_results.items():
        validate_signal_bundle_v1(result["signal_bundle"])
        validate_event_bundle_v1(result["event_bundle"])
        assert result["schema_version"] == "visual_signal_artifact.v1"
        window = result["canonical_time_window"]
        assert window["duration_ms"] == window["end_ms"] - window["start_ms"]
        # local_samples：准心恒视口中心，target track 样本字段齐全且有限。
        crosshair = result["local_samples"]["crosshair.position"]
        assert crosshair
        for sample in crosshair:
            assert (sample["x"], sample["y"]) == (
                VIEWPORT_WIDTH_PX / 2.0,
                VIEWPORT_HEIGHT_PX / 2.0,
            )
        for key, samples in result["local_samples"].items():
            if not key.startswith("target."):
                continue
            assert samples
            for sample in samples:
                assert {
                    "canonical_time_ms", "x", "y", "visible_radius", "confidence",
                } <= set(sample)
                assert math.isfinite(sample["x"]) and math.isfinite(sample["y"])
                assert sample["visible_radius"] > 0.0
        # quality 合同：status / enabled_metric_families / limitations。
        quality = result["quality"]
        assert quality["status"] in {"accepted", "limited"}
        assert set(quality["enabled_metric_families"]) <= {
            "dynamic_clicking", "tracking", "switching",
        }
        assert quality["limitations"]
        assert result["safe_summary"]["producer_version"] == TELEMETRY_PRODUCER_VERSION
        assert result["safe_summary"]["track_count"] == len([
            key for key in result["local_samples"] if key.startswith("target.")
        ])
        # 事件时间全部落在 canonical window 内且 id 唯一。
        start_ms, end_ms = window["start_ms"], window["end_ms"]
        event_ids = [event["event_id"] for event in result["event_bundle"]["events"]]
        assert len(event_ids) == len(set(event_ids))
        assert all(
            start_ms <= event["start_ms"] < end_ms
            for event in result["event_bundle"]["events"]
        )


def test_track_counts_match_rounds_index(built_results):
    for (round_dir, round_number), result in built_results.items():
        entry, _ = _index_entry(round_dir, round_number)
        if entry is None:
            continue
        track_keys = [
            key for key in result["local_samples"] if key.startswith("target.")
        ]
        assert len(track_keys) == int(entry["n_targets"])
        assert {int(key.split(".")[1]) for key in track_keys} == {
            int(target["tid"]) for target in entry["targets"]
        }


def _click_canonical_ms(round_dir: Path, round_number: int, result: dict) -> list[int]:
    """inputs 的 L_down 沿 -> canonical ms（用产物声明的 time mapping，验证其可用性）。"""
    mapping = result["video_time_mapping"]
    origin = mapping["canonical_origin_ms"] - mapping["source_pts_origin_ms"]
    clicks = []
    for record in _load_jsonl(round_dir / f"inputs_{round_number:02d}.jsonl"):
        buttons = record.get("btn") or []
        if any(button == "L_down" for button in buttons):
            clicks.append(int(round(float(record["t"]) * 1000.0)) + origin)
    clicks.sort()
    return clicks


def test_geometry_self_check_kill_click_median_below_one_degree(built_results):
    """黄金验收：击杀点击瞬间 准心->垂死目标 角误差（producer 投影数据复算）。

    死亡 = lives t_end；点击 = 死亡前 <=250ms 最近的 L_down；样本 = 该 track 在
    生命窗内离点击最近的一条投影；px 偏移按 selector fov 的针孔焦距 atan 反推
    回角度（与 producer 的 px=f*tan(θ) 投影互逆）。
    验收口径与 SIDECARS §5 相同：全会话池化中位 <1.0°（个别热身轮如 final_0831
    round 1 单轮中位 ~10° 属玩家行为，不改变池化结论）。
    """
    session_errors: dict[Path, list[float]] = {}
    for (round_dir, round_number), result in built_results.items():
        entry, _ = _index_entry(round_dir, round_number)
        if entry is None:
            continue
        mapping = result["video_time_mapping"]
        origin = mapping["canonical_origin_ms"] - mapping["source_pts_origin_ms"]
        errors = session_errors.setdefault(round_dir, [])
        selector = result["visual_runtime_selector"]
        hfov = float(selector["fov"])
        focal_px = (VIEWPORT_WIDTH_PX / 2.0) / math.tan(math.radians(hfov / 2.0))
        clicks = _click_canonical_ms(round_dir, round_number, result)
        for target in entry["targets"]:
            track = result["local_samples"][f"target.{int(target['tid'])}.position"]
            times = [sample["canonical_time_ms"] for sample in track]
            for life in target.get("lives", []):
                death_ms = int(round(float(life["t_end"]) * 1000.0)) + origin
                click_index = bisect.bisect_right(clicks, death_ms) - 1
                if click_index < 0 or death_ms - clicks[click_index] > 250:
                    continue
                click_ms = clicks[click_index]
                # 只在该生命窗内取样本（生命窗外目标不存在）。
                life_start_ms = int(round(float(life["t_start"]) * 1000.0)) + origin
                lo = bisect.bisect_left(times, life_start_ms)
                hi = bisect.bisect_right(times, death_ms)
                window_times = times[lo:hi]
                if not window_times:
                    continue
                nearest = min(window_times, key=lambda item: abs(item - click_ms))
                sample = track[bisect.bisect_left(times, nearest)]
                offset_x = abs(sample["x"] - VIEWPORT_WIDTH_PX / 2.0)
                offset_y = abs(sample["y"] - VIEWPORT_HEIGHT_PX / 2.0)
                deg_x = math.degrees(math.atan(offset_x / focal_px))
                deg_y = math.degrees(math.atan(offset_y / focal_px))
                errors.append(math.hypot(deg_x, deg_y))
    assert session_errors, "no kill-click pairs found in any session"
    for round_dir, errors in session_errors.items():
        assert errors, f"no kill-click pairs for session {round_dir.name}"
        # 与 SIDECARS §5/§7 的几何回执同口径：池化中位 <1.0°。
        assert statistics.median(errors) < 1.0, (
            f"{round_dir.name}: pooled median {statistics.median(errors):.3f} deg"
        )
        print(
            f"geometry check {round_dir.name}: n={len(errors)} "
            f"median={statistics.median(errors):.3f}deg "
            f"p25={sorted(errors)[len(errors) // 4]:.3f} "
            f"share<1deg={sum(1 for e in errors if e < 1.0) / len(errors):.3f}"
        )
    assert sum(len(errors) for errors in session_errors.values()) >= 100


# ---------- _interpolate_in_life：切片 oracle 等价 + 性能防回归（纯本地，永远运行） ----------


def test_interpolate_in_life_matches_slicing_oracle():
    """新实现（有界二分）与旧切片实现在 2000+ 组样本上逐位等价。

    覆盖：随机点序列（含重复时间戳、乱序后排序）、t 恰好等于首/尾点时间、
    t 在窗外、空窗（life_start > life_end）、单点序列及窗内相邻点插值。
    """
    import random

    from kovaak_tracker.telemetry_signals import (
        _LIFE_FRAME_EPSILON_S,
        _interpolate_in_life,
    )

    def oracle(points, life_start, life_end, t):
        """旧实现（切片版）原样复制，仅作为等价性 oracle。"""
        lo = bisect.bisect_left(points, (life_start - _LIFE_FRAME_EPSILON_S,))
        hi = bisect.bisect_right(points, (life_end + _LIFE_FRAME_EPSILON_S, math.inf))
        segment = points[lo:hi]
        if not segment:
            return None
        index = bisect.bisect_right(segment, (t, math.inf, math.inf, math.inf)) - 1
        if index < 0:
            first = segment[0]
            return first[1], first[2], first[3]
        if index >= len(segment) - 1:
            last = segment[index]
            return last[1], last[2], last[3]
        left, right = segment[index], segment[index + 1]
        span = right[0] - left[0]
        weight = (t - left[0]) / span if span > 0 else 0.0
        return (
            left[1] + (right[1] - left[1]) * weight,
            left[2] + (right[2] - left[2]) * weight,
            left[3] + (right[3] - left[3]) * weight,
        )

    def assert_same(points, life_start, life_end, t):
        expected = oracle(points, life_start, life_end, t)
        actual = _interpolate_in_life(points, life_start, life_end, t)
        if expected is None or actual is None:
            assert actual is None and expected is None, (life_start, life_end, t)
            return
        assert actual[0] == expected[0], (life_start, life_end, t)
        assert actual[1] == expected[1], (life_start, life_end, t)
        assert actual[2] == expected[2], (life_start, life_end, t)

    # 确定性边界样本：单点序列（t 在窗前/点上/窗后）。
    singleton = [(1.0, 10.0, 20.0, 30.0)]
    for t in (0.0, 1.0, 2.0):
        assert_same(singleton, 0.0, 2.0, t)
    # 单点序列 + 空窗（窗完全在点之后）。
    assert_same(singleton, 5.0, 9.0, 6.0)
    # 重复时间戳（同刻多点，t 恰好落在该刻）。
    duplicated = [(1.0, 5.0, 0.0, 0.0), (1.0, 7.0, 0.0, 0.0), (2.0, 9.0, 0.0, 0.0)]
    for t in (1.0, 1.5, 2.0, 3.0):
        assert_same(duplicated, 0.0, 3.0, t)
    # 空窗 life_start > life_end。
    assert_same(duplicated, 2.5, 0.5, 1.0)

    rng = random.Random(0x5EED2026)
    groups = 0
    comparisons = 0
    for case_index in range(2500):
        n = 1 if case_index % 50 == 0 else rng.randint(2, 30)
        times = sorted(rng.uniform(0.0, 60.0) for _ in range(n))
        for i in range(1, n):
            if rng.random() < 0.25:
                times[i] = times[i - 1]  # 重复时间戳
        points_c = [
            (
                times[i],
                rng.uniform(-2000.0, 2000.0),
                rng.uniform(-2000.0, 2000.0),
                rng.uniform(-200.0, 200.0),
            )
            for i in range(n)
        ]
        rng.shuffle(points_c)
        points_c.sort()  # 乱序后排序，满足 bisect 输入合同

        if case_index % 50 == 1:
            life_start, life_end = 30.0, 10.0  # 空窗：life_start > life_end
        else:
            life_start = rng.uniform(0.0, 60.0)
            life_end = life_start + rng.uniform(0.0, 60.0)

        query_ts = [
            life_start - 1.0,  # t 在窗外（窗下侧）
            life_end + 1.0,  # t 在窗外（窗上侧）
            life_start,
            life_end,
            points_c[0][0],  # t 恰好等于序列首点时间
            points_c[-1][0],  # t 恰好等于序列尾点时间
            rng.uniform(life_start, life_end),
        ]
        lo = bisect.bisect_left(points_c, (life_start - _LIFE_FRAME_EPSILON_S,))
        hi = bisect.bisect_right(points_c, (life_end + _LIFE_FRAME_EPSILON_S, math.inf))
        if lo < hi:
            query_ts.append(points_c[lo][0])  # t 恰好等于窗内首点时间
            query_ts.append(points_c[hi - 1][0])  # t 恰好等于窗内尾点时间
            if hi - lo >= 2:
                inside = rng.randrange(lo, hi - 1)
                if points_c[inside + 1][0] > points_c[inside][0]:
                    query_ts.append(
                        (points_c[inside][0] + points_c[inside + 1][0]) / 2.0
                    )  # 段内插值路径
        for t in query_ts:
            assert_same(points_c, life_start, life_end, t)
            comparisons += 1
        groups += 1

    assert groups >= 2000
    assert comparisons >= 2000


def test_interpolate_in_life_large_series_performance():
    """30 万点序列查询 200 次 < 1s（旧切片实现需复制整段，防回归）。"""
    import time

    from kovaak_tracker.telemetry_signals import _interpolate_in_life

    points = [
        (i * 0.002, float(i % 1920), float(i % 1080), 0.0)
        for i in range(300_000)
    ]
    life_start, life_end = 10.0, 590.0
    queries = [
        life_start + (life_end - life_start) * k / 200.0 for k in range(200)
    ]
    start = time.perf_counter()
    for t in queries:
        assert _interpolate_in_life(points, life_start, life_end, t) is not None
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"200 queries over 300000 points took {elapsed:.3f}s"
    print(f"interpolate perf: 200 queries over 300000 points in {elapsed * 1000:.1f}ms")


# ---------- 冻结路径身份透传（纯本地 fixture，永远运行） ----------
#
# 跨局污染事故根因：冻结副本的 merge_manifest 指向所有局共享的、活的
# rounds_index.json（新局导入会覆盖它），旧局重放按 (round, file) 兜底"唯一
# 命中"把新局索引误认成旧局身份 -> 轨道错认 + 时间起点错位。冻结路径只认
# 本局 meta（frozen_round_meta 透传），绝不读任何共享索引。


def _polluted_shared_index() -> dict:
    """被"别的局"覆盖过的共享活索引：同 round 号、同 file 名，但 t_start 与
    targets 与本局完全不同（tid 7 / addr 0xFFFFFFFF / 生命窗 55.5s 起）。"""
    return {
        "format_version": 1,
        "generator": "polluted",
        "params": {},
        "sources": [{
            "source": "other_session/round_01.jsonl",
            "outdir": "E:/ACData/other_session",
            "rounds": [{
                "round": 1,
                "file": "round_01.jsonl",
                "t_start": 55.5,
                "t_end": 56.0,
                "n_frames": 32,
                "n_targets": 1,
                "targets": [{
                    "tid": 7,
                    "addr": 4294967295,
                    "addr_hex": "0xffffffff",
                    "n_lives": 1,
                    "lives": [{"t_start": 55.5, "t_end": 56.0, "n": 32}],
                }],
            }],
        }],
    }


def _write_local_sidecars(
    round_dir: Path,
    *,
    frames_name: str,
    views_name: str,
    inputs_name: str,
    shared_index: Path,
) -> None:
    """最小 2 目标旁车（addr 0 与 addr 100 全程在帧），0.48s @~60Hz，
    首帧 t=0.032（与 meta t_start=0.0 错开，让 origin 回退语义可观测）。

    merge_manifest 的 rounds_index 指向 shared_index（ext 目录之外的共享
    活索引），内容为 _polluted_shared_index。
    """
    round_dir.mkdir(parents=True)
    views = b"".join(
        (
            '{"t": %.3f, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": 103.0}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(32, 532, 16)
    )
    frames = b"".join(
        (
            '{"ev": "frame", "t": %.3f, "targets": '
            '[[0, 4000.0, 10.0, 0.0], [100, 4200.0, -10.0, 0.0]]}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(32, 532, 16)
    )
    (round_dir / frames_name).write_bytes(frames)
    (round_dir / views_name).write_bytes(views)
    (round_dir / inputs_name).write_bytes(b'{"t": 0.200, "btn": ["L_down"]}\n')
    (round_dir / "bb.json").write_bytes(
        b'{"challenges": [{"window_t": [0.0, 1.0], '
        b'"bots": [{"character": {"bb": {"radius": 60.0}}}]}]}\n'
    )
    shared_index.parent.mkdir(parents=True, exist_ok=True)
    shared_index.write_text(
        json.dumps(_polluted_shared_index(), ensure_ascii=False), encoding="utf-8",
    )
    (round_dir / "merge_manifest.json").write_text(json.dumps({
        "schema_version": "merge_manifest.v1",
        "generated": "2026-10-05T00:00:00",
        "round_dir": str(round_dir),
        "rounds_index": str(shared_index),
        "alignment": {"method": "fixture", "accepted": True},
    }), encoding="utf-8")


def _frozen_meta() -> dict:
    """本局正确身份（ingest meta 的 targets/time 子集，同 build_targets 形状）：
    addr 0 全程存活；addr 100 生命窗 [0.25, 0.5]。"""
    return {
        "targets": [
            {
                "tid": 0,
                "addr_hex": "0x0",
                "motion": "static",
                "n_lives": 1,
                "lives": [{"t_start": 0.0, "t_end": 0.5, "n": 30, "path_cm": 0.0}],
            },
            {
                "tid": 1,
                "addr_hex": "0x64",
                "motion": "static",
                "n_lives": 1,
                "lives": [{"t_start": 0.25, "t_end": 0.5, "n": 16, "path_cm": 0.0}],
            },
        ],
        "t_start": 0.0,
    }


def _frozen_build(round_dir: Path, meta: dict) -> dict:
    return build_telemetry_visual_result(
        round_dir,
        1,
        canonical_window=(1000.0, 2000.0),
        file_names={"round": "round.jsonl", "views": "views_01.jsonl", "inputs": "inputs_01.jsonl"},
        frozen_round_meta=meta,
    )


def _track_keys(result: dict) -> set[str]:
    return {
        key for key in result["local_samples"] if key.startswith("target.")
    }


def test_frozen_meta_ignores_overwritten_shared_rounds_index(tmp_path):
    """跨局污染回归：manifest 指向的共享活索引已被别的局覆盖（同 round 号、
    同 file 名、不同 t_start 与 targets）——身份只来自本局 meta，且输出与
    共享索引文件不存在时逐位一致。"""
    shared = tmp_path / "shared" / "rounds_index.json"
    round_dir = tmp_path / "ext-fixture01"
    _write_local_sidecars(
        round_dir,
        frames_name="round.jsonl",
        views_name="views_01.jsonl",
        inputs_name="inputs_01.jsonl",
        shared_index=shared,
    )
    assert shared.is_file()

    polluted = _frozen_build(round_dir, _frozen_meta())

    # 身份来自 meta：2 条轨道（tid 0/1），不是污染索引的 tid 7 / addr 0xFFFFFFFF。
    assert _track_keys(polluted) == {"target.0.position", "target.1.position"}
    # origin 取 meta t_start（0.0），不是污染索引的 55.5。
    assert polluted["video_time_mapping"]["source_pts_origin_ms"] == 0.0
    # 生命窗语义保持：tid0 全窗 30 样本；tid1 只在 [0.25, 0.5] 内 16 样本
    #（首样本 0.256s -> canonical 1256ms）。
    tid0 = polluted["local_samples"]["target.0.position"]
    tid1 = polluted["local_samples"]["target.1.position"]
    assert len(tid0) == 30 and len(tid1) == 16
    assert min(sample["canonical_time_ms"] for sample in tid1) == 1256
    # 事件：两条生命的出生 + 死亡 + 1 发 shot。
    kinds = polluted["safe_summary"]["event_counts"]
    assert kinds["kill"] == 2
    assert kinds["target_change_point"] == 2
    assert kinds["shot"] == 1

    # 共享索引文件不存在（重放时上游 cleaned 目录已演化/被清理）：输出逐位一致。
    shared.unlink()
    clean = _frozen_build(round_dir, _frozen_meta())
    assert json.dumps(clean, sort_keys=True) == json.dumps(polluted, sort_keys=True)


def test_frozen_meta_empty_targets_walks_whole_round_fallback(tmp_path):
    """meta.targets 空/缺失：lives_unavailable_whole_round_window + 整轮兜底，
    不抛错、不回读共享索引（污染索引里的 tid 7 / addr 0xFFFFFFFF 不得出现）。"""
    shared = tmp_path / "shared" / "rounds_index.json"
    round_dir = tmp_path / "ext-fixture02"
    _write_local_sidecars(
        round_dir,
        frames_name="round.jsonl",
        views_name="views_01.jsonl",
        inputs_name="inputs_01.jsonl",
        shared_index=shared,
    )

    result = _frozen_build(round_dir, {"targets": [], "t_start": 0.0})

    assert "lives_unavailable_whole_round_window" in result["limitations"]
    assert not [
        code for code in result["limitations"] if code.startswith("rounds_index_")
    ]
    # 整轮兜底：按地址首次出现顺序编号（addr 0/100 -> tid 0/1），各一条整轮生命。
    assert _track_keys(result) == {"target.0.position", "target.1.position"}
    for key in _track_keys(result):
        samples = result["local_samples"][key]
        assert len(samples) == 32, key


def test_frozen_meta_build_is_deterministic(tmp_path):
    """确定性：同输入连续两次 build，规范化 JSON 完全一致；t_start 缺失时
    origin 回落轮帧首帧 t（保持既有语义）。"""
    shared = tmp_path / "shared" / "rounds_index.json"
    round_dir = tmp_path / "ext-fixture03"
    _write_local_sidecars(
        round_dir,
        frames_name="round.jsonl",
        views_name="views_01.jsonl",
        inputs_name="inputs_01.jsonl",
        shared_index=shared,
    )

    first = _frozen_build(round_dir, _frozen_meta())
    second = _frozen_build(round_dir, _frozen_meta())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    no_t_start = _frozen_build(round_dir, {"targets": _frozen_meta()["targets"]})
    assert no_t_start["video_time_mapping"]["source_pts_origin_ms"] == pytest.approx(32.0)


def test_frozen_meta_origin_t_anchor_corrected_padded_round(tmp_path):
    """[fix 2026-10-05e] 前垫场景：rounds_index 轮起点（t_start=8.0）比真实
    局开始（canonical 窗 [10000,16000)ms 的帧域起点 10.0）早 2s。origin_t
    （worker 用对齐回执锚换算）优先作 canonical 映射原点：窗内 kill = 真实
    击杀数，局前残留段的自然收尾（9.99s）与局末存活段（16.05s）不混入；
    origin_t 缺失回退 t_start 旧语义（窗整体偏早：残留自然收尾误判 kill、
    局末真实击杀丢窗外）。"""
    round_dir = tmp_path / "ext-padded"
    round_dir.mkdir(parents=True)
    # 旁车 t 域 [8.0, 16.1]：A=局前残留（自然收尾 9.99）、B=局内目标两条命
    # （真实击杀 11.3 / 12.0）、C=局内目标（局末真实击杀 15.9）、D=局末存活段
    # （16.05 自然收尾）。
    lives = {
        0: (8.1, 9.99),        # addr 0：局前残留，无死亡收尾
        100: (10.5, 12.0),     # addr 100：两条命，真实击杀 11.3 / 12.0
        200: (12.0, 15.9),     # addr 200：局末真实击杀
        300: (15.9, 16.05),    # addr 300：局末存活段
    }
    grid = [round(8.0 + i * 0.032, 3) for i in range(int(8.1 / 0.032) + 1)]
    views = b"".join(
        ('{"t": %.3f, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": 103.0}\n'
         % t).encode("utf-8") for t in grid)
    frames = b"".join(
        ('{"ev": "frame", "t": %.3f, "targets": [%s]}\n' % (
            t,
            ", ".join('[%d, 4000.0, %d.0, 0.0]' % (a, a // 100)
                      for a, (lo, hi) in lives.items() if lo <= t <= hi),
        )).encode("utf-8") for t in grid)
    (round_dir / "round.jsonl").write_bytes(frames)
    (round_dir / "views_01.jsonl").write_bytes(views)
    (round_dir / "inputs_01.jsonl").write_bytes(b"")
    (round_dir / "bb.json").write_bytes(
        b'{"challenges": [{"window_t": [8.0, 16.1], '
        b'"bots": [{"character": {"bb": {"radius": 60.0}}}]}]}\n')
    (round_dir / "merge_manifest.json").write_text(json.dumps({
        "schema_version": "merge_manifest.v1",
        "generated": "2026-10-05T00:00:00",
        "round_dir": str(round_dir),
        "alignment": {
            "method": "index_t0_epoch+xcorr_verify", "accepted": True,
            "t0_epoch_from_index": 0.0,   # 锚=0 → 帧域即 epoch 秒
        },
    }), encoding="utf-8")

    meta_targets = [
        {"tid": 0, "addr_hex": "0x0", "n_lives": 1,
         "lives": [{"t_start": 8.1, "t_end": 9.99, "n": 10, "path_cm": 0.0}]},
        {"tid": 1, "addr_hex": "0x64", "n_lives": 2,
         "lives": [{"t_start": 10.5, "t_end": 11.3, "n": 20, "path_cm": 0.0},
                   {"t_start": 11.3, "t_end": 12.0, "n": 20, "path_cm": 0.0}]},
        {"tid": 2, "addr_hex": "0xc8", "n_lives": 1,
         "lives": [{"t_start": 12.0, "t_end": 15.9, "n": 60, "path_cm": 0.0}]},
        {"tid": 3, "addr_hex": "0x12c", "n_lives": 1,
         "lives": [{"t_start": 15.9, "t_end": 16.05, "n": 3, "path_cm": 0.0}]},
    ]

    def build(meta):
        return build_telemetry_visual_result(
            round_dir, 1,
            canonical_window=(10000.0, 16000.0),
            file_names={"round": "round.jsonl", "views": "views_01.jsonl",
                        "inputs": "inputs_01.jsonl"},
            frozen_round_meta=meta,
        )

    corrected = build({
        "targets": meta_targets,
        "t_start": 8.0,        # rounds_index 轮起点（带 2s 局前垫）
        "origin_t": 10.0,      # 锚校正：canonical 10000ms − 锚 0s
    })
    # origin_t 被消费（不是 t_start=8.0）
    assert corrected["video_time_mapping"]["source_pts_origin_ms"] == 10000.0
    kills = sorted(
        event["start_ms"] for event in corrected["event_bundle"]["events"]
        if event["event_kind"] == "kill")
    # 窗内 kill = 真实击杀：11.3 / 12.0 / 15.9 → 11300/12000/15900ms；
    # 局前残留自然收尾 9.99（→9990，窗外）与局末存活段 16.05（→16050，窗外）
    # 不混入。
    assert kills == [11300, 12000, 15900]
    assert corrected["safe_summary"]["event_counts"]["kill"] == 3

    # origin_t 缺失（旧 cleaner/worker 产物）→ 回退 t_start=8.0：窗整体偏早
    # 2s——残留自然收尾 9.99 误判 kill（→11990），局末真实击杀 15.9 丢窗外
    # （→17900）。
    legacy = build({"targets": meta_targets, "t_start": 8.0})
    assert legacy["video_time_mapping"]["source_pts_origin_ms"] == 8000.0
    kills_legacy = sorted(
        event["start_ms"] for event in legacy["event_bundle"]["events"]
        if event["event_kind"] == "kill")
    assert kills_legacy == [11990, 13300, 14000]


def test_index_round_file_fallback_retired_fail_closed(tmp_path):
    """index_round_file 兜底退役：参数不复存在；目录 basename 与索引 sources
    不匹配时，(round, file) 唯一命中也不被采纳——fail-closed 落
    rounds_index_missing + 整轮兜底，绝不把共享索引条目误认成本局身份。"""
    import inspect

    parameters = inspect.signature(build_telemetry_visual_result).parameters
    assert "index_round_file" not in parameters
    assert "frozen_round_meta" in parameters

    shared = tmp_path / "shared" / "rounds_index.json"
    round_dir = tmp_path / "renamed-frozen-copy"  # basename 必然不匹配索引 sources
    _write_local_sidecars(
        round_dir,
        frames_name="round_01.jsonl",
        views_name="views_01.jsonl",
        inputs_name="inputs_01.jsonl",
        shared_index=shared,
    )

    result = build_telemetry_visual_result(
        round_dir, 1, canonical_window=(1000.0, 2000.0),
    )
    limitations = result["limitations"]
    assert "rounds_index_missing_addr_dedup_identity" in limitations
    assert "lives_unavailable_whole_round_window" in limitations
    # 身份来自整轮兜底（addr 0/100 -> tid 0/1），不是索引条目的 tid 7；
    # origin 回落轮帧首帧 t（0.032s），不是索引条目的 55.5s。
    assert _track_keys(result) == {"target.0.position", "target.1.position"}
    assert result["video_time_mapping"]["source_pts_origin_ms"] == pytest.approx(32.0)
