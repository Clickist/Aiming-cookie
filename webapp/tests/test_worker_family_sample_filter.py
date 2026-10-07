"""纵深防御（第二道防线）聚焦测试：无效几何样本过滤。

producer（telemetry_signals 投影）对视锥外靶子会产出爆炸像素坐标（x 百万级）
且无标记。本文件验证 worker_family_analysis 的三个 payload builder 在装样前
剔除 nan/inf 与画布合理界外样本、计数并以
``nonfinite_or_offscreen_samples_filtered`` 降级标注（可观测），且真实分析器
不再因非法坐标 raise（fail-closed 整场分析）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from webapp.backend import worker_family_analysis
from webapp.backend.worker_family_analysis import (
    MISSING_RADIUS_SAMPLES_FILTERED as MISSING_RADIUS_CODE,
)
from webapp.backend.worker_family_analysis import (
    NONFINITE_OR_OFFSCREEN_SAMPLES_FILTERED as FILTERED_CODE,
)

ANALYSIS_ID = 901
ANALYSIS_REF = f"analysis:{ANALYSIS_ID}"


def _window() -> dict:
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1000,
        "duration_ms": 1000,
        "window_semantics": "half_open",
        "timebase_version": "time_alignment.test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _resolution(aim_family: str) -> dict:
    return {
        "schema_version": "scenario_resolution.v1",
        "aim_family": aim_family,
        "target_motion": {"model": "unknown", "target_count_model": "unknown"},
    }


def _snapshot(aim_family: str, *, with_trace: bool = True) -> dict:
    snapshot = {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 42,
        "canonical_time_window": _window(),
        "scenario_resolution": _resolution(aim_family),
        "sources": {},
    }
    if with_trace:
        # raw input trace 源（字节读取在用例里 patch）。
        snapshot["trace"] = {
            "artifact_ref": "run:42:trace",
            "path": "/db-private/runs/42/trace.bin",
            "availability": "available",
            "format_version": 1,
        }
    return snapshot


def _crosshair() -> list[dict]:
    return [
        {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
        for time_ms in range(0, 1000, 50)
    ]


def _spy_real_analyzer(module, name, captured: list):
    """捕获 payload 且仍调用真实分析器（断言装样过滤 + 分析不 raise）。"""
    real = getattr(module, name)

    def wrapper(payload):
        captured.append(payload)
        return real(payload)

    return patch.object(module, name, wrapper)


# ---- continuous_tracking ----


def _tracking_visual(target_samples: list[dict]) -> dict:
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": _crosshair(),
            "target.1.position": target_samples,
        },
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:1", "limitations": []},
        ],
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "events": [],
            "outcome_associations": [],
        },
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }


def _mixed_tracking_target_samples() -> list[dict]:
    """正常样本与坏样本混合：1e7 级爆炸投影、nan、负向爆炸。"""
    def sample(time_ms: int, x: float, y: float) -> dict:
        return {
            "canonical_time_ms": time_ms,
            "x": x,
            "y": y,
            "visible_radius": 10.0,
            "confidence": 1.0,
        }

    return [
        sample(0, 900.0, 500.0),        # 正常
        sample(50, 1.0e7, 500.0),       # 视锥边界 tan 爆炸（x 百万级）
        sample(100, float("nan"), 500.0),   # 非有限
        sample(150, 500.0, -3.0e6),     # y 向爆炸
        sample(200, 920.0, 520.0),      # 正常
    ]


def test_tracking_filters_mixed_invalid_samples_and_marks_quality():
    from kovaak_tracker import tracking_analysis

    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("continuous_tracking")}
    visual = _tracking_visual(_mixed_tracking_target_samples())
    captured: list = []

    with _spy_real_analyzer(
        tracking_analysis, "analyze_continuous_tracking_v1", captured,
    ):
        result = worker_family_analysis.run_continuous_tracking_analysis(job, visual)

    # 真实分析器跑完（未因非法坐标 raise）。
    assert result["schema_version"] == "continuous_tracking_analysis.v1"
    # 正常样本全保留、坏样本（1e7/nan/负爆炸）被滤。
    samples = captured[0]["target_track"]["samples"]
    assert [(s["canonical_time_ms"], s["x"], s["y"]) for s in samples] == [
        (0, 900.0, 500.0),
        (200, 920.0, 520.0),
    ]
    # crosshair 无坏样本，不受影响。
    assert len(captured[0]["crosshair_samples"]) == len(_crosshair())
    # 降级可观测：limitation 码进入 visual_quality 并透传到结果。
    assert FILTERED_CODE in captured[0]["visual_quality"]["limitations"]
    assert FILTERED_CODE in result["limitations"]


def test_tracking_clean_samples_leave_no_filter_limitation():
    from kovaak_tracker import tracking_analysis

    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("continuous_tracking")}
    clean = [
        s
        for s in _mixed_tracking_target_samples()
        if -1000.0 <= s["x"] <= 2920.0 and -1000.0 <= s["y"] <= 2080.0
    ]
    assert len(clean) == 2
    visual = _tracking_visual(clean)
    captured: list = []

    with _spy_real_analyzer(
        tracking_analysis, "analyze_continuous_tracking_v1", captured,
    ):
        worker_family_analysis.run_continuous_tracking_analysis(job, visual)

    assert len(captured[0]["target_track"]["samples"]) == 2
    assert FILTERED_CODE not in captured[0]["visual_quality"]["limitations"]


# ---- dynamic_clicking ----


def _dynamic_visual(target_samples: list[dict]) -> dict:
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["dynamic_clicking"],
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": _crosshair(),
            "target.1.position": target_samples,
        },
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:1", "limitations": []},
        ],
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }


def test_dynamic_clicking_filters_mixed_invalid_samples_and_marks_quality():
    from kovaak_tracker import dynamic_clicking_analysis

    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("dynamic_clicking")}
    visual = _dynamic_visual(_mixed_tracking_target_samples())
    trace_points = [
        {"timestamp_ms": 0, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": 200, "dx": 0, "dy": 0, "buttons": 1},
    ]
    captured: list = []

    with _spy_real_analyzer(
        dynamic_clicking_analysis, "analyze_dynamic_clicking_v1", captured,
    ), patch(
        "webapp.backend.worker._read_frozen_source_bytes", return_value=b"trace",
    ), patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=trace_points,
    ):
        result = worker_family_analysis.run_dynamic_clicking_analysis(job, visual)

    assert result["analysis_version"] == "dynamic_clicking.v1"
    samples = captured[0]["target_tracks"][0]["samples"]
    assert [(s["canonical_time_ms"], s["x"], s["y"]) for s in samples] == [
        (0, 900.0, 500.0),
        (200, 920.0, 520.0),
    ]
    assert FILTERED_CODE in captured[0]["visual_quality"]["limitations"]
    assert FILTERED_CODE in result["limitations"]


# ---- target_switching（遥测真值路径）----


def _kill(index: int, time_ms: int, track_id: int) -> dict:
    return {
        "event_id": f"{ANALYSIS_REF}:event:telemetry:{index}",
        "event_kind": "kill",
        "start_ms": time_ms,
        "end_ms": time_ms,
        "actor_refs": [f"{ANALYSIS_REF}:target-track:{track_id}"],
        "source_refs": [f"{ANALYSIS_REF}:source:fixture"],
        "confidence": 1.0,
        "attributes": {},
        "limitations": [],
    }


def _telemetry_meta() -> dict:
    return {
        "targets": [
            {"tid": 0, "lives": [{"t_start": 0.0, "t_end": 0.2}]},
            {"tid": 1, "lives": [{"t_start": 0.25, "t_end": 0.6}]},
        ],
    }


def _switching_visual(target_0_samples: list[dict]) -> dict:
    def sample(time_ms: int, x: float) -> dict:
        return {
            "canonical_time_ms": time_ms,
            "x": x,
            "y": 540.0,
            "visible_radius": 5.0,
            "confidence": 1.0,
        }

    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "video_time_mapping": {
            "schema_version": "telemetry_time_mapping.v1",
            "source_pts_origin_ms": 0.0,
            "canonical_origin_ms": 0,
            "mapping_method": "telemetry_sidecar_t_domain",
            "timebase_version": "telemetry.test.v1",
        },
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["target_switching"],
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": _crosshair(),
            "target.0.position": target_0_samples,
            "target.1.position": [sample(time_ms, 990.0) for time_ms in range(275, 601, 25)],
        },
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "events": [_kill(1, 200, 0), _kill(2, 600, 1)],
            "outcome_associations": [],
        },
        "signal_bundle": {
            "schema_version": "signal_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "channels": [],
        },
        "sample_sets": [],
    }


def test_switching_telemetry_filters_mixed_invalid_samples_and_marks_quality():
    from kovaak_tracker import switching_analysis

    job = {
        "id": ANALYSIS_ID,
        "input_snapshot": _snapshot("target_switching", with_trace=False),
    }
    visual = _switching_visual([
        {"canonical_time_ms": 25, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 50, "x": 1.0e7, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 100, "x": float("nan"), "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 150, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
    ])
    meta = _telemetry_meta()
    captured: list = []

    with _spy_real_analyzer(
        switching_analysis, "analyze_target_switching_telemetry_v1", captured,
    ), patch(
        "webapp.backend.worker._external_telemetry_source",
        lambda _job: {"external_run_id": "ext-fixture"},
    ), patch(
        "webapp.backend.external_telemetry_store.load_meta",
        lambda _external_run_id: meta,
    ):
        result = worker_family_analysis.run_target_switching_telemetry_analysis(
            job, visual,
        )

    # 真实分析器跑完（未因非法坐标 raise）。
    assert result["schema_version"] == "target_switching_analysis.v1"
    tracks = {track["track_ref"]: track for track in captured[0]["target_tracks"]}
    samples = tracks[f"{ANALYSIS_REF}:target-track:0"]["samples"]
    assert [(s["canonical_time_ms"], s["x"]) for s in samples] == [
        (25, 990.0),
        (150, 990.0),
    ]
    # 干净轨道不受影响。
    assert len(tracks[f"{ANALYSIS_REF}:target-track:1"]["samples"]) == 14
    # 被清空的轨道不再进 payload，但其生命窗仍参与候选可见性。
    assert f"{ANALYSIS_REF}:target-track:2" not in tracks
    lives = {life["track_ref"] for life in captured[0]["target_lives"]}
    assert f"{ANALYSIS_REF}:target-track:0" in lives
    assert FILTERED_CODE in captured[0]["visual_quality"]["limitations"]
    assert FILTERED_CODE in result["limitations"]


def test_switching_telemetry_clean_samples_leave_no_filter_limitation():
    from kovaak_tracker import switching_analysis

    job = {
        "id": ANALYSIS_ID,
        "input_snapshot": _snapshot("target_switching", with_trace=False),
    }
    visual = _switching_visual([
        {"canonical_time_ms": 25, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 150, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
    ])
    meta = _telemetry_meta()
    captured: list = []

    with _spy_real_analyzer(
        switching_analysis, "analyze_target_switching_telemetry_v1", captured,
    ), patch(
        "webapp.backend.worker._external_telemetry_source",
        lambda _job: {"external_run_id": "ext-fixture"},
    ), patch(
        "webapp.backend.external_telemetry_store.load_meta",
        lambda _external_run_id: meta,
    ):
        worker_family_analysis.run_target_switching_telemetry_analysis(job, visual)

    assert FILTERED_CODE not in captured[0]["visual_quality"]["limitations"]
    assert MISSING_RADIUS_CODE not in captured[0]["visual_quality"]["limitations"]


# ---- 半径缺测防线（bb.json 缺失：兜底帧 visible_radius=None）----


def test_switching_telemetry_filters_missing_radius_samples_and_marks_quality():
    """bb 缺失兜底帧（visible_radius=None/字段缺失）在 require_radius 解析前
    剔除：真实分析器不再 raise，缺测样本不进 payload，标注
    missing_radius_samples_filtered（可观测）。"""
    from kovaak_tracker import switching_analysis

    job = {
        "id": ANALYSIS_ID,
        "input_snapshot": _snapshot("target_switching", with_trace=False),
    }
    visual = _switching_visual([
        {"canonical_time_ms": 25, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 50, "x": 991.0, "y": 540.0, "visible_radius": None, "confidence": 1.0},
        {"canonical_time_ms": 75, "x": 992.0, "y": 540.0, "confidence": 1.0},  # 字段缺失
        {"canonical_time_ms": 150, "x": 993.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
    ])
    meta = _telemetry_meta()
    captured: list = []

    with _spy_real_analyzer(
        switching_analysis, "analyze_target_switching_telemetry_v1", captured,
    ), patch(
        "webapp.backend.worker._external_telemetry_source",
        lambda _job: {"external_run_id": "ext-fixture"},
    ), patch(
        "webapp.backend.external_telemetry_store.load_meta",
        lambda _external_run_id: meta,
    ):
        result = worker_family_analysis.run_target_switching_telemetry_analysis(
            job, visual,
        )

    # 真实分析器跑完（未因 None 半径 raise）。
    assert result["schema_version"] == "target_switching_analysis.v1"
    tracks = {track["track_ref"]: track for track in captured[0]["target_tracks"]}
    samples = tracks[f"{ANALYSIS_REF}:target-track:0"]["samples"]
    assert [(s["canonical_time_ms"], s["x"]) for s in samples] == [
        (25, 990.0),
        (150, 993.0),
    ]
    # 降级可观测：半径缺测码进入 visual_quality 并透传到结果；本例无几何
    # 过滤，几何过滤码不出现。
    assert MISSING_RADIUS_CODE in captured[0]["visual_quality"]["limitations"]
    assert MISSING_RADIUS_CODE in result["limitations"]
    assert FILTERED_CODE not in captured[0]["visual_quality"]["limitations"]


def test_switching_telemetry_all_radius_missing_track_dropped_without_crash():
    """整轨半径全缺测（bb 缺失局）：轨道被既有空轨道过滤剔除、不进 payload，
    生命窗仍参与候选可见性（target_lives）；无权威 kill 引用它 → 分析器照常
    完成，不 crash。"""
    from kovaak_tracker import switching_analysis

    job = {
        "id": ANALYSIS_ID,
        "input_snapshot": _snapshot("target_switching", with_trace=False),
    }
    visual = _switching_visual([
        {"canonical_time_ms": 25, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
        {"canonical_time_ms": 150, "x": 990.0, "y": 540.0, "visible_radius": 5.0, "confidence": 1.0},
    ])
    # tid 2：生命窗整体在 canonical 窗外（to_ms 1200-1600，cross-validation 只
    # 数窗内生命窗终点），无权威 kill；窗内采样帧全部半径缺测。
    meta = {
        "targets": [
            {"tid": 0, "lives": [{"t_start": 0.0, "t_end": 0.2}]},
            {"tid": 1, "lives": [{"t_start": 0.25, "t_end": 0.6}]},
            {"tid": 2, "lives": [{"t_start": 1.2, "t_end": 1.6}]},
        ],
    }
    visual["local_samples"]["target.2.position"] = [
        {"canonical_time_ms": 300, "x": 900.0, "y": 540.0, "visible_radius": None, "confidence": 1.0},
        {"canonical_time_ms": 400, "x": 910.0, "y": 540.0, "visible_radius": None, "confidence": 1.0},
    ]
    captured: list = []

    with _spy_real_analyzer(
        switching_analysis, "analyze_target_switching_telemetry_v1", captured,
    ), patch(
        "webapp.backend.worker._external_telemetry_source",
        lambda _job: {"external_run_id": "ext-fixture"},
    ), patch(
        "webapp.backend.external_telemetry_store.load_meta",
        lambda _external_run_id: meta,
    ):
        result = worker_family_analysis.run_target_switching_telemetry_analysis(
            job, visual,
        )

    assert result["schema_version"] == "target_switching_analysis.v1"
    track_refs = {track["track_ref"] for track in captured[0]["target_tracks"]}
    assert f"{ANALYSIS_REF}:target-track:2" not in track_refs
    # 全缺测轨道的生命窗仍参与候选可见性。
    lives = {life["track_ref"] for life in captured[0]["target_lives"]}
    assert f"{ANALYSIS_REF}:target-track:2" in lives
    assert MISSING_RADIUS_CODE in captured[0]["visual_quality"]["limitations"]


@pytest.mark.parametrize(
    ("x", "y", "valid"),
    [
        (960.0, 540.0, True),
        (0, 0, True),
        (2920.0, 2080.0, True),   # 恰在合理界上（画布外扩 1000px）
        (2920.1, 540.0, False),   # 界外
        (-1000.1, 540.0, False),
        (float("inf"), 540.0, False),
        (float("nan"), 540.0, False),
        (960.0, float("-inf"), False),
        (1.0e7, 540.0, False),
        (None, 540.0, False),
        ("960", 540.0, False),
        (True, 540.0, False),
    ],
)
def test_sample_geometry_valid_boundary_matrix(x, y, valid):
    assert worker_family_analysis._sample_geometry_valid({"x": x, "y": y}) is valid
