"""Telemetry-truth target_switching analyzer unit tests (registry-aligned)."""

import pytest

import kovaak_tracker.switching_analysis as switching_analysis
from kovaak_tracker.switching_analysis import (
    ACQUISITION_CONE_DEG,
    KILL_CONTACT_PROXIMITY_MS,
    TargetSwitchingAnalysisError,
    analyze_target_switching_telemetry_v1,
    build_switching_chains_from_telemetry_v1,
)

ANALYSIS_REF = "analysis:switching:1"
# f(103°) ≈ 763.63 px/rad：3° 锥 ≈ 40 px（小角近似）；fixture 用
# 30 px（锥内）/ 100 px（锥外，~7.5°）避免边界抖动。
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
        {
            "canonical_time_ms": time_ms,
            "x": 960.0,
            "y": 540.0,
            "confidence": 1.0,
        }
        for time_ms in range(0, end_ms + 1, 25)
    ]


def _track(track_id, offsets):
    """offsets: [(time_ms, x_offset_px)]，目标恒在准星水平线上。"""
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


def _payload(tracks, kills, lives, *, quality_enabled=True, end_ms=1000):
    return {
        "schema_version": "target_switching_telemetry_input.v1",
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(end_ms),
        "scenario_resolution": {"aim_family": "target_switching"},
        "visual_quality": {
            "status": "accepted" if quality_enabled else "rejected",
            "enabled_metric_families": (
                ["target_switching"] if quality_enabled else []
            ),
            "limitations": [],
        },
        "crosshair_samples": _crosshair(end_ms),
        "target_tracks": tracks,
        "target_lives": lives,
        "authoritative_kills": kills,
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


def _lives(track_id, windows):
    return {
        "track_ref": f"{ANALYSIS_REF}:target-track:{track_id}",
        "lives": [list(window) for window in windows],
    }


def _alternation_payload():
    """两目标交替：A 于 200ms 被杀，B 于 600ms 被杀；B 在 250ms 进锥并保持。"""
    track_a = _track("0", [(25, OUTSIDE_OFFSET_PX), (100, OUTSIDE_OFFSET_PX)])
    track_b = _track(
        "1",
        [
            (100, OUTSIDE_OFFSET_PX),
            (125, OUTSIDE_OFFSET_PX),
            (150, OUTSIDE_OFFSET_PX),
            (175, OUTSIDE_OFFSET_PX),
            (200, OUTSIDE_OFFSET_PX),  # leave 时刻：锥外（departure）
            (225, OUTSIDE_OFFSET_PX),
            (250, INSIDE_OFFSET_PX),   # acquire：进锥
            (275, INSIDE_OFFSET_PX),
            (300, INSIDE_OFFSET_PX),
            (325, INSIDE_OFFSET_PX),
            (350, INSIDE_OFFSET_PX),
            (375, INSIDE_OFFSET_PX),
            (400, INSIDE_OFFSET_PX),
            (425, INSIDE_OFFSET_PX),
            (450, INSIDE_OFFSET_PX),
            (475, INSIDE_OFFSET_PX),
            (500, INSIDE_OFFSET_PX),
            (525, INSIDE_OFFSET_PX),
            (550, INSIDE_OFFSET_PX),
            (575, INSIDE_OFFSET_PX),
            (600, INSIDE_OFFSET_PX),  # 死亡时刻在锥内
        ],
    )
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1")],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
    )
    return payload


def test_two_target_alternation_produces_one_observable_chain():
    result = analyze_target_switching_telemetry_v1(_alternation_payload())
    assert result["schema_version"] == "target_switching_analysis.v1"
    assert result["analysis_version"] == "target_switching.v1"
    assert result["analysis_type"] == "target_switching"
    assert result["support_status"] == "supported"
    assert len(result["processed_rows"]) == 1
    row = result["processed_rows"][0]
    assert row["row_kind"] == "switch_chain"
    assert row["classification"] == "observable_target_switch"
    assert row["leave_time_ms"] == 200
    assert row["acquire_time_ms"] == 250
    assert row["settle_time_ms"] == 250
    assert row["transition_time_ms"] == 50
    assert row["settle_duration_ms"] == 0
    # departure=(100,0) → arrival=(30,0)：直线位移 70 px，路径 70 px。
    assert row["transition_distance_px"] == pytest.approx(70.0)
    assert row["path_efficiency"] == pytest.approx(1.0)
    metrics = result["metrics"]
    assert metrics["target_switching.transition_time_ms"]["availability"] == "available"
    assert metrics["target_switching.settle_duration_ms"]["availability"] == "available"
    assert metrics["target_switching.path_efficiency"]["availability"] == "available"
    assert (
        metrics["target_switching.transition_distance_px"]["availability"]
        == "available"
    )


def test_registry_metric_refs_are_explicitly_unavailable_when_unobservable():
    result = analyze_target_switching_telemetry_v1(_alternation_payload())
    metrics = result["metrics"]
    # 首发沿不可靠（P1-B）→ 占位 unavailable + limitation，不承诺数值。
    first_shot = metrics["target_switching.first_shot_latency_ms"]
    assert first_shot["availability"] == "unavailable"
    assert first_shot["value"] is None
    assert "telemetry_click_anchors_unreliable" in first_shot["limitations"]
    for key in (
        "target_switching.first_damage_latency_ms",
        "target_switching.carry_over_overshoot_ratio",
        "target_switching.terminal_correction_ratio",
    ):
        assert metrics[key]["availability"] == "unavailable"
        assert metrics[key]["value"] is None
    # registry switching.selection-observable-only@3：无 expected-target rule
    # 不产出选靶论断（信号原文作 limitation）。
    assert "selection evidence unavailable" in result["limitations"]


def test_rows_feed_coach_observable_switch_chain_filter():
    from kovaak_tracker.coach.mapping_rules import _observable_switch_chain

    result = analyze_target_switching_telemetry_v1(_alternation_payload())
    row = result["processed_rows"][0]
    assert row["transition_time_ms"] == 50
    assert row["settle_duration_ms"] == 0
    assert _observable_switch_chain(row) is True


def test_settle_captures_reentry_after_leaving_the_cone():
    track_a = _track("0", [(100, OUTSIDE_OFFSET_PX)])
    offsets = [(200, OUTSIDE_OFFSET_PX), (225, OUTSIDE_OFFSET_PX)]
    offsets.append((250, INSIDE_OFFSET_PX))   # 首次进锥（acquire）
    offsets.append((275, INSIDE_OFFSET_PX))
    offsets.append((300, OUTSIDE_OFFSET_PX))  # 掉出锥
    offsets.append((325, OUTSIDE_OFFSET_PX))
    for time_ms in range(350, 601, 25):
        offsets.append((time_ms, INSIDE_OFFSET_PX))  # 重进并保持到击杀
    track_b = _track("1", offsets)
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1")],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["acquire_time_ms"] == 250
    assert row["settle_time_ms"] == 350
    assert row["settle_duration_ms"] == 100
    assert row["transition_time_ms"] == 50


def test_detour_path_lowers_path_efficiency():
    track_a = _track("0", [(100, OUTSIDE_OFFSET_PX)])
    track_b = _track(
        "1",
        [
            (200, OUTSIDE_OFFSET_PX),    # departure：100 px 右侧
            (225, 200.0),                # 先向右绕（远离）
            (250, -INSIDE_OFFSET_PX),    # 再一路扫到左侧锥内
            *[(time_ms, -INSIDE_OFFSET_PX) for time_ms in range(275, 601, 25)],
        ],
    )
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1")],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["transition_time_ms"] == 50
    # 直线位移 |−30 − 100| = 130 px；路径 100 + 230 = 330 px → 效率 < 1。
    assert row["transition_distance_px"] == pytest.approx(130.0)
    assert row["path_efficiency"] == pytest.approx(130.0 / 330.0)


def test_no_cone_contact_keeps_partial_discrete_acquisition_only():
    track_a = _track("0", [(100, OUTSIDE_OFFSET_PX)])
    track_b = _track(
        "1",
        [(time_ms, OUTSIDE_OFFSET_PX) for time_ms in range(200, 601, 25)],
    )
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1")],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    assert result["support_status"] == "partial"
    row = result["processed_rows"][0]
    assert row["row_kind"] == "unclassified_discrete_acquisition"
    assert "switch_contact_unobservable" in row["limitations"]
    # registry 口径：partial chain 不进 transition/settle 指标。
    assert result["metrics"]["target_switching.transition_time_ms"]["value"] is None
    assert (
        result["metrics"]["target_switching.transition_time_ms"]["availability"]
        == "unavailable"
    )


def test_spawn_waiting_next_target_marks_geometry_unavailable():
    track_a = _track("0", [(100, OUTSIDE_OFFSET_PX)])
    # B 于 leave 之后出生（300ms）：acquire 可观察，departure 几何不可得。
    track_b = _track(
        "1",
        [(time_ms, INSIDE_OFFSET_PX) for time_ms in range(300, 601, 25)],
    )
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1", birth_ms=300)],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(300, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["row_kind"] == "switch_chain"
    assert row["acquire_time_ms"] == 300
    assert row["transition_time_ms"] == 100
    assert row["transition_distance_px"] is None
    assert "transition_geometry_unavailable" in row["limitations"]


def test_offscreen_geometry_is_rejected_with_viewport_limitation():
    track_a = _track("0", [(100, OUTSIDE_OFFSET_PX)])
    track_b = _track(
        "1",
        [
            (200, 2600.0),  # 视口外（px 域 tan 放大，几何失真）
            (225, 400.0),
            (250, INSIDE_OFFSET_PX),
            *[(time_ms, INSIDE_OFFSET_PX) for time_ms in range(275, 601, 25)],
        ],
    )
    payload = _payload(
        tracks=[track_a, track_b],
        kills=[_kill(1, 200, "0"), _kill(2, 600, "1")],
        lives=[_lives("0", [(0, 200)]), _lives("1", [(0, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    row = result["processed_rows"][0]
    assert row["transition_distance_px"] is None
    assert "transition_geometry_outside_viewport" in row["limitations"]


def test_rejected_visual_quality_returns_outcome_only():
    payload = _alternation_payload()
    payload["visual_quality"] = {
        "status": "rejected",
        "enabled_metric_families": [],
        "limitations": ["visual_frame_gap"],
    }
    result = analyze_target_switching_telemetry_v1(payload)
    assert result["support_status"] == "outcome_only"
    assert result["processed_rows"] == []
    assert (
        "target_switching_visual_quality_unavailable" in result["limitations"]
    )


def test_tampered_kill_series_fails_closed():
    payload = _alternation_payload()
    payload["authoritative_kills"][1]["time_ms"] = 199  # 乱序（前置死亡）
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload)

    payload = _alternation_payload()
    payload["authoritative_kills"][1]["target_birth_ms"] = 700  # 死于出生前
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload)

    payload = _alternation_payload()
    payload["authoritative_kills"][1]["kill_index"] = 1  # kill_index 重复
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload)

    payload = _alternation_payload()
    payload["authoritative_kills"][1]["time_ms"] = 5000  # 出窗
    with pytest.raises(TargetSwitchingAnalysisError):
        analyze_target_switching_telemetry_v1(payload)


def test_kill_without_any_kill_before_it_is_not_a_chain():
    # 只有一杀（无前一杀）不构成转火段：无链、outcome_only。
    track_b = _track(
        "1",
        [(time_ms, INSIDE_OFFSET_PX) for time_ms in range(0, 601, 25)],
    )
    payload = _payload(
        tracks=[track_b],
        kills=[_kill(1, 600, "1")],
        lives=[_lives("1", [(0, 600)])],
    )
    result = analyze_target_switching_telemetry_v1(payload)
    assert result["support_status"] == "outcome_only"
    assert (
        "telemetry_switching_no_observable_chain" in result["limitations"]
    )


def test_state_events_are_truncated_deterministically_under_budget(monkeypatch):
    monkeypatch.setattr(switching_analysis, "_EVENT_BUNDLE_LIMIT", 3)
    result = analyze_target_switching_telemetry_v1(_alternation_payload())
    # 权威击杀(2) + 链行(1) 占满预算：状态事件整组截断并记 limitation。
    events = result["evidence_extension"]["event_bundle"]["events"]
    assert len(events) == 3
    assert "switching_state_events_truncated" in result["limitations"]
    # 截断后证据段的 event_refs 只引用已发事件。
    for segment in result["evidence_segments"]:
        for ref in segment["event_refs"]:
            assert any(event["event_id"] == ref for event in events)


def test_constants_are_versioned_contract():
    # 到位锥与击杀近旁窗是指标定义的一部分（改动需升 metric 版本）。
    assert ACQUISITION_CONE_DEG == 3.0
    assert KILL_CONTACT_PROXIMITY_MS == 200
