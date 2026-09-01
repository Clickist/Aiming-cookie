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
