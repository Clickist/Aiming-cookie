"""Dual-source input fusion unit tests (contract: .zcode/fusion-spec-1005.md).

覆盖规格 §三 单测清单：两源沿一致/不一致回填、按住型 unavailable、
角速度发起检测（阈值边界/无运动段）、空 trace。switching 分析器的融合
消费语义（first_shot 出值/按住型 limitation）在此一并覆盖。
"""

import pytest

from kovaak_tracker.input_fusion import (
    MOTION_ONSET_THRESHOLD_DEG_S,
    InputFusionError,
    bearing_toward_target,
    build_angular_position_series,
    build_angular_speed_stream,
    deg_per_count,
    fuse_press_edges,
    left_press_edges,
    movement_onset_ms,
)
from kovaak_tracker.switching_analysis import analyze_target_switching_telemetry_v1

ANALYSIS_REF = "analysis:fusion:1"
FOV = 103.0
INSIDE_OFFSET_PX = 30.0
OUTSIDE_OFFSET_PX = 100.0


def _window(end_ms=1000):
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": end_ms,
        "duration_ms": end_ms,
        "window_semantics": "half_open",
        "timebase_version": "time_alignment.test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _crosshair(end_ms=1000):
    return [
        {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
        for time_ms in range(0, end_ms + 1, 25)
    ]


def _track(track_id, offsets):
    return {
        "track_ref": f"{ANALYSIS_REF}:target-track:{track_id}",
        "samples": [
            {
                "canonical_time_ms": time_ms,
                "x": 960.0 + offset,
                "y": 540.0,
                "visible_radius": 5.0,
                "confidence": 1.0,
            }
            for time_ms, offset in offsets
        ],
    }


def _kill(index, time_ms, track_id, birth_ms=0):
    return {
        "event_ref": f"{ANALYSIS_REF}:event:telemetry:{index}",
        "time_ms": time_ms,
        "kill_index": index,
        "target_track_ref": f"{ANALYSIS_REF}:target-track:{track_id}",
        "target_birth_ms": birth_ms,
    }


def _lives(track_id, windows):
    return {
        "track_ref": f"{ANALYSIS_REF}:target-track:{track_id}",
        "lives": [list(window) for window in windows],
    }


def _alternation_payload():
    """两目标交替：A 于 200ms 被杀，B 于 600ms 被杀；B 在 250ms 进锥。"""
    track_a = _track("0", [(25, OUTSIDE_OFFSET_PX), (100, OUTSIDE_OFFSET_PX)])
    track_b = _track(
        "1",
        [
            (200, OUTSIDE_OFFSET_PX),
            (250, INSIDE_OFFSET_PX),
            *[(time_ms, INSIDE_OFFSET_PX) for time_ms in range(275, 601, 25)],
        ],
    )
    return {
        "schema_version": "target_switching_telemetry_input.v1",
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "scenario_resolution": {"aim_family": "target_switching"},
        "visual_quality": {
            "status": "accepted",
            "enabled_metric_families": ["target_switching"],
            "limitations": [],
        },
        "crosshair_samples": _crosshair(),
        "target_tracks": [track_a, track_b],
        "target_lives": [_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
        "authoritative_kills": [_kill(1, 200, "0"), _kill(2, 600, "1")],
        "source_signal_bundle": {
            "schema_version": "signal_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "channels": [],
        },
        "source_sample_sets": [],
        "viewport_fov_deg": FOV,
        "click_anchor_status": "unreliable",
        "comparison": None,
    }


def _press_fusion(edges, validation):
    return {"press_edges": edges, "press_edge_validation": validation}


# ---------------------------------------------------------------------------
# input_fusion: 按压沿提取与两源互备
# ---------------------------------------------------------------------------


def _points_button_stream(edge_times, *, start=0, end=2000):
    """构造 buttons 状态流点列：每个沿时刻按下，下一毫秒抬起。"""
    points = []
    pressed = 0
    edges = set(edge_times)
    for time_ms in range(start, end):
        if time_ms in edges:
            pressed = 1
        elif time_ms - 1 in edges:
            pressed = 0
        points.append({
            "timestamp_ms": time_ms,
            "dx": 0,
            "dy": 0,
            "buttons": pressed,
        })
    return points


def test_left_press_edges_extracts_rising_edges_only_in_window():
    points = _points_button_stream([10, 50, 120], end=200)
    assert left_press_edges(points, start_ms=0, end_ms=200) == [10, 50, 120]
    # 半开窗：窗外的沿不出现（60 在窗外）。
    assert left_press_edges(points, start_ms=20, end_ms=100) == [50]
    # 空点列 / 无沿。
    assert left_press_edges([], start_ms=0, end_ms=100) == []
    assert left_press_edges(
        [{"timestamp_ms": 5, "dx": 0, "dy": 0, "buttons": 1}],
        start_ms=0, end_ms=100,
    ) == []


def test_fuse_press_edges_matching_prefers_telemetry_with_residual():
    fused = fuse_press_edges([100, 300], [102, 295])
    assert fused["validation"] == {
        "matched": 2,
        "telemetry_only": 0,
        "trace_only": 0,
        "residual_max_ms": 5,
        "residual_median_ms": 1.5,  # sorted([-2, 5]) 偶数个取均值
    }
    # matched 对取遥测时刻，残差 = trace − telemetry。
    assert fused["edges"] == [
        {"time_ms": 102, "source": "telemetry", "matched": True, "residual_ms": -2},
        {"time_ms": 295, "source": "telemetry", "matched": True, "residual_ms": 5},
    ]


def test_fuse_press_edges_trace_backfills_telemetry_gaps_and_vice_versa():
    # 遥测丢一个沿（P1-B 型缺口）→ trace 回填；trace 丢一个 → 遥测保留。
    fused = fuse_press_edges([100, 300, 500], [101, 502])
    edges = fused["edges"]
    assert [edge["source"] for edge in edges] == [
        "telemetry", "trace", "telemetry",
    ]
    assert fused["validation"]["matched"] == 2
    assert fused["validation"]["trace_only"] == 1
    assert fused["validation"]["telemetry_only"] == 0
    backfilled = edges[1]
    assert backfilled == {
        "time_ms": 300, "source": "trace", "matched": False, "residual_ms": None,
    }


def test_fuse_press_edges_beyond_tolerance_stay_unmatched():
    fused = fuse_press_edges([100], [100 + 21], tolerance_ms=20)
    assert fused["validation"]["matched"] == 0
    assert fused["validation"]["trace_only"] == 1
    assert fused["validation"]["telemetry_only"] == 1
    assert fused["validation"]["residual_max_ms"] is None


def test_fuse_press_edges_empty_inputs():
    fused = fuse_press_edges([], [])
    assert fused["edges"] == []
    assert fused["validation"] == {
        "matched": 0,
        "telemetry_only": 0,
        "trace_only": 0,
        "residual_max_ms": None,
        "residual_median_ms": None,
    }


def test_fuse_press_edges_rejects_unsorted_or_negative_tolerance():
    with pytest.raises(InputFusionError):
        fuse_press_edges([300, 100], [])
    with pytest.raises(InputFusionError):
        fuse_press_edges([], [300, 100])
    with pytest.raises(InputFusionError):
        fuse_press_edges([100], [100], tolerance_ms=-1)


# ---------------------------------------------------------------------------
# input_fusion: 角速度流与运动发起
# ---------------------------------------------------------------------------


def _straight_move_points(start_ms, count, *, dx, dy=0, step_ms=1):
    return [
        {"timestamp_ms": start_ms + index * step_ms, "dx": dx, "dy": dy, "buttons": 0}
        for index in range(count)
    ]


def test_build_angular_speed_stream_buckets_and_conversion():
    # 10 counts/ms × 0.1 deg/count = 1000 deg/s，方向 (1, 0)。末桶在下一点
    # 到达时才闭合：60 点 → 两个完整桶（起点 0 与 20），尾部半桶不发射。
    points = _straight_move_points(0, 60, dx=10)
    stream = build_angular_speed_stream(points, deg_per_count_value=0.1, window_ms=20)
    assert [bucket["start_ms"] for bucket in stream] == [0, 20]
    assert stream[0]["deg_per_s"] == pytest.approx(1000.0)
    assert stream[0]["vx"] == pytest.approx(10.0)
    assert stream[0]["vy"] == pytest.approx(0.0)
    # 空点列 → 空流（空 trace 语义）。
    assert build_angular_speed_stream([], deg_per_count_value=0.1) == []


def test_deg_per_count_matches_calibration_math():
    # counts_per_360 = 54.43cm × 1600dpi / 2.54 = 34290 counts。
    assert deg_per_count(54.43, 1600) == pytest.approx(360.0 / (54.43 * 1600 / 2.54))
    with pytest.raises(InputFusionError):
        deg_per_count(0, 1600)
    with pytest.raises(InputFusionError):
        deg_per_count(54.43, 0)


def test_movement_onset_threshold_boundary_is_inclusive():
    points = _straight_move_points(100, 60, dx=5)
    stream = build_angular_speed_stream(
        points, deg_per_count_value=0.1, window_ms=20,
    )  # 500 deg/s 向右
    bearing = (1.0, 0.0)
    # 阈值恰好等于桶角速度（500）：>= 判定为发起。
    assert movement_onset_ms(
        stream, anchor_ms=100, bearing=bearing, threshold_deg_s=500.0,
    ) == 100
    # 阈值略高：无发起。
    assert movement_onset_ms(
        stream, anchor_ms=100, bearing=bearing, threshold_deg_s=500.1,
    ) is None


def test_movement_onset_requires_direction_toward_target():
    points = _straight_move_points(100, 60, dx=5)
    stream = build_angular_speed_stream(points, deg_per_count_value=0.1)
    # 高速向右但目标在左：不算发起。
    assert movement_onset_ms(
        stream, anchor_ms=100, bearing=(-1.0, 0.0),
    ) is None
    # 目标在右：锚后首个达标桶的起点。
    assert movement_onset_ms(
        stream, anchor_ms=100, bearing=(1.0, 0.0),
    ) == 100
    # 锚在首桶之后：从锚后的桶起算（20ms 桶起点量化）。
    assert movement_onset_ms(
        stream, anchor_ms=110, bearing=(1.0, 0.0),
    ) == 120


def test_movement_onset_no_motion_segment_returns_none():
    stream = build_angular_speed_stream(
        _straight_move_points(0, 40, dx=0), deg_per_count_value=0.1,
    )  # 全零位移
    assert movement_onset_ms(stream, anchor_ms=0, bearing=(1.0, 0.0)) is None
    # 空流（空 trace）同样无发起。
    assert movement_onset_ms([], anchor_ms=0, bearing=(1.0, 0.0)) is None
    # 零轴承无方向可判。
    points = _straight_move_points(0, 40, dx=5)
    stream = build_angular_speed_stream(points, deg_per_count_value=0.1)
    assert movement_onset_ms(stream, anchor_ms=0, bearing=(0.0, 0.0)) is None


def test_movement_onset_search_cap_bounds_the_window():
    points = _straight_move_points(0, 40, dx=5)
    stream = build_angular_speed_stream(points, deg_per_count_value=0.1)
    # 锚后 cap 之外的运动不算发起。
    assert movement_onset_ms(
        stream, anchor_ms=2000, bearing=(1.0, 0.0), max_search_ms=100,
    ) is None


# ---------------------------------------------------------------------------
# input_fusion: 角位置序列（tracking 家族运动学输入）
# ---------------------------------------------------------------------------


def test_build_angular_position_series_converts_counts_to_degrees():
    # 10 counts/点 × 0.1 deg/count = 每点 +1 deg；pitch 全零。
    points = _straight_move_points(0, 5, dx=10)
    series = build_angular_position_series(
        points, deg_per_count_value=0.1, start_ms=0, end_ms=100,
    )
    assert [time_ms for time_ms, _, _ in series] == [0, 1, 2, 3, 4]
    assert [yaw for _, yaw, _ in series] == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0])
    assert all(pitch == pytest.approx(0.0) for _, _, pitch in series)
    # 负方向：累计递减。
    series_left = build_angular_position_series(
        _straight_move_points(0, 3, dx=-10), deg_per_count_value=0.1,
        start_ms=0, end_ms=100,
    )
    assert [yaw for _, yaw, _ in series_left] == pytest.approx([-1.0, -2.0, -3.0])


def test_build_angular_position_series_wraps_yaw_to_plus_minus_180():
    def _one(dx):
        return [{"timestamp_ms": 0, "dx": dx, "dy": 0, "buttons": 0}]

    # 恰好 +180 归 -180（半开 [-180, 180)）；190 → -170；-190 → +170。
    assert build_angular_position_series(
        _one(1800), deg_per_count_value=0.1, start_ms=0, end_ms=10,
    )[0][1] == pytest.approx(-180.0)
    assert build_angular_position_series(
        _one(1900), deg_per_count_value=0.1, start_ms=0, end_ms=10,
    )[0][1] == pytest.approx(-170.0)
    assert build_angular_position_series(
        _one(-1900), deg_per_count_value=0.1, start_ms=0, end_ms=10,
    )[0][1] == pytest.approx(170.0)
    # 跨步累计同样 wrap：+170 再 +30 → -160。
    across = build_angular_position_series(
        [{"timestamp_ms": 0, "dx": 1700, "dy": 0, "buttons": 0},
         {"timestamp_ms": 1, "dx": 300, "dy": 0, "buttons": 0}],
        deg_per_count_value=0.1, start_ms=0, end_ms=10,
    )
    assert [yaw for _, yaw, _ in across] == pytest.approx([170.0, -160.0])


def test_build_angular_position_series_pitch_accumulates_without_wrap():
    # dy 正 = 向下 = pitch 增（屏幕约定，不 wrap、不取反）；负 dy 递减。
    points = [
        {"timestamp_ms": 0, "dx": 0, "dy": 500, "buttons": 0},
        {"timestamp_ms": 1, "dx": 0, "dy": -200, "buttons": 0},
    ]
    series = build_angular_position_series(
        points, deg_per_count_value=0.1, start_ms=0, end_ms=10,
    )
    assert [pitch for _, _, pitch in series] == pytest.approx([50.0, 30.0])


def test_build_angular_position_series_clips_to_half_open_window():
    # 半开窗 [3, 7)：窗外点不参与；原点 = 窗内首点（窗内相对角位移）。
    points = _straight_move_points(0, 10, dx=10)
    series = build_angular_position_series(
        points, deg_per_count_value=0.1, start_ms=3, end_ms=7,
    )
    assert [time_ms for time_ms, _, _ in series] == [3, 4, 5, 6]
    assert [yaw for _, yaw, _ in series] == pytest.approx([1.0, 2.0, 3.0, 4.0])
    # 空点列 / 全窗外 → 空序列。
    assert build_angular_position_series(
        [], deg_per_count_value=0.1, start_ms=0, end_ms=10,
    ) == []
    assert build_angular_position_series(
        points, deg_per_count_value=0.1, start_ms=100, end_ms=200,
    ) == []


def test_build_angular_position_series_rejects_invalid_params():
    with pytest.raises(InputFusionError):
        build_angular_position_series([], deg_per_count_value=0.0, start_ms=0, end_ms=10)
    with pytest.raises(InputFusionError):
        build_angular_position_series([], deg_per_count_value=-0.1, start_ms=0, end_ms=10)
    with pytest.raises(InputFusionError):
        build_angular_position_series([], deg_per_count_value=0.1, start_ms=10, end_ms=10)


def test_bearing_toward_target_sign_conventions():
    # 目标在右侧：yaw 误差为正 → 鼠标 dx 应为正（bx > 0）。
    right = bearing_toward_target(
        crosshair_x=960.0, crosshair_y=540.0, target_x=1060.0, target_y=540.0,
        focal_px=763.0,
    )
    assert right is not None and right[0] > 0 and abs(right[1]) == pytest.approx(0.0)
    # 目标在上方：pitch 误差为正（抬头）→ 鼠标 dy 应为负（by < 0）。
    up = bearing_toward_target(
        crosshair_x=960.0, crosshair_y=540.0, target_x=960.0, target_y=440.0,
        focal_px=763.0,
    )
    assert up is not None and up[1] < 0 and abs(up[0]) == pytest.approx(0.0)
    # 目标与准星重合：无方位。
    assert bearing_toward_target(
        crosshair_x=960.0, crosshair_y=540.0, target_x=960.0, target_y=540.0,
        focal_px=763.0,
    ) is None


# ---------------------------------------------------------------------------
# switching 分析器消费：first_shot 出值 / 按住型 unavailable / 无融合回退
# ---------------------------------------------------------------------------


def _validation(**overrides):
    summary = {
        "matched": 0,
        "telemetry_only": 0,
        "trace_only": 0,
        "residual_max_ms": None,
        "residual_median_ms": None,
    }
    summary.update(overrides)
    return summary


def test_first_shot_latency_emits_values_from_fused_edges():
    payload = _alternation_payload()
    # acquire=250；沿 260 落在 [250, 600] 链窗内。
    payload.update(_press_fusion(
        [
            {"time_ms": 260, "source": "telemetry", "matched": True, "residual_ms": 2},
            {"time_ms": 700, "source": "trace", "matched": False, "residual_ms": None},
        ],
        _validation(matched=1, trace_only=1, residual_max_ms=2, residual_median_ms=2.0),
    ))
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["first_shot_latency_ms"] == 10
    assert row["first_shot_press_source"] == "telemetry"
    assert row["first_shot_event_ref"] == f"{ANALYSIS_REF}:event:fusion-press:1"
    metric = result["metrics"]["target_switching.first_shot_latency_ms"]
    assert metric["availability"] == "available"
    assert metric["value"] == 10.0
    assert "first_shot_press_source_fused_telemetry_preferred" in metric["limitations"]
    assert "press_edge_trace_backfilled_1" in metric["limitations"]
    assert "press_edge_cross_residual_max_2ms" in metric["limitations"]


def test_first_shot_press_and_hold_weapon_is_explicit_unavailable():
    payload = _alternation_payload()
    # 按住型：整窗沿 <= 1（TF180 LG 打法事实，数据前提不满足而非数据坏）。
    payload.update(_press_fusion(
        [{"time_ms": 10, "source": "telemetry", "matched": True, "residual_ms": 1}],
        _validation(matched=1, residual_max_ms=1, residual_median_ms=1.0),
    ))
    result = analyze_target_switching_telemetry_v1(payload)
    metric = result["metrics"]["target_switching.first_shot_latency_ms"]
    assert metric["availability"] == "unavailable"
    assert metric["value"] is None
    assert "first_shot_press_and_hold_weapon" in metric["limitations"]
    assert "first_shot_press_source_fused_telemetry_preferred" in metric["limitations"]
    assert all(
        row["first_shot_latency_ms"] is None
        for row in result["processed_rows"]
    )


def test_first_shot_without_press_edges_keeps_placeholder_semantics():
    payload = _alternation_payload()
    result = analyze_target_switching_telemetry_v1(payload)
    metric = result["metrics"]["target_switching.first_shot_latency_ms"]
    assert metric["availability"] == "unavailable"
    assert "telemetry_click_anchors_unreliable" in metric["limitations"]
    assert "first_shot_press_and_hold_weapon" not in metric["limitations"]


def test_first_shot_edge_before_acquire_is_not_matched():
    payload = _alternation_payload()
    # 沿在 acquire(250) 之前 → 该链无首发值，指标 unavailable。
    payload.update(_press_fusion(
        [
            {"time_ms": 100, "source": "telemetry", "matched": True, "residual_ms": 0},
            {"time_ms": 400, "source": "telemetry", "matched": True, "residual_ms": 1},
        ],
        _validation(matched=2, residual_max_ms=1, residual_median_ms=0.5),
    ))
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["first_shot_latency_ms"] == 150  # 400 − 250
    assert row["first_shot_press_source"] == "telemetry"


def test_incomplete_or_invalid_press_fusion_inputs_fail_closed():
    payload = _alternation_payload()
    payload["press_edges"] = [
        {"time_ms": 260, "source": "telemetry", "matched": True, "residual_ms": 2},
    ]
    from kovaak_tracker.switching_analysis import TargetSwitchingAnalysisError

    # 缺 press_edge_validation：成对性校验 fail-closed。
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload)
    # 未知来源 / matched 与 residual 配对不一致：拒绝。
    payload2 = _alternation_payload()
    payload2.update(_press_fusion(
        [{"time_ms": 260, "source": "keyboard", "matched": True, "residual_ms": 2}],
        _validation(matched=1, residual_max_ms=2, residual_median_ms=2.0),
    ))
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload2)
    payload3 = _alternation_payload()
    payload3.update(_press_fusion(
        [{"time_ms": 260, "source": "trace", "matched": True, "residual_ms": 2}],
        _validation(matched=1, residual_max_ms=2, residual_median_ms=2.0),
    ))
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload3)
    # 窗外沿：拒绝。
    payload4 = _alternation_payload()
    payload4.update(_press_fusion(
        [{"time_ms": 5000, "source": "telemetry", "matched": True, "residual_ms": 2}],
        _validation(matched=1, residual_max_ms=2, residual_median_ms=2.0),
    ))
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload4)


# ---------------------------------------------------------------------------
# tracking 融合 alignment 记录构造
# ---------------------------------------------------------------------------


def test_build_fused_alignment_metric_v1_record_shape():
    from kovaak_tracker.tracking_analysis import build_fused_alignment_metric_v1

    record = build_fused_alignment_metric_v1(
        analysis_ref=ANALYSIS_REF,
        latencies=[10.0, None, 30.0],
        limitations=["alignment_latency_from_fused_movement_onset"],
    )
    assert record["metric_key"] == "continuous_tracking.alignment_latency_ms"
    assert record["metric_version"] == (
        "continuous_tracking.alignment_latency_ms.v1"
    )
    assert record["availability"] == "available"
    assert record["value"] == 20.0  # median
    assert record["population"] == {
        "sample_count": 3, "valid_count": 2, "excluded_count": 1,
    }
    empty = build_fused_alignment_metric_v1(
        analysis_ref=ANALYSIS_REF, latencies=[], limitations=[],
    )
    assert empty["availability"] == "unavailable"
    assert empty["value"] is None
