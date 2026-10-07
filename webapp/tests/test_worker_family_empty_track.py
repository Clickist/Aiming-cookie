"""聚焦测试：空样本目标轨道不进家族分析器（P0：dynamic_clicking 整族 unavailable）。

遥测旁车按整个 session 声明目标 tid（窗外生命期的轨道在局内 canonical 窗内
保持 0 样本）。适配层若把空轨道装给分析器，dynamic 分析器对空 samples 抛
DynamicClickingAnalysisError（target_tracks[0].samples must be non-empty），
整场分析降级 outcome_only（dynamic_clicking_analysis_unavailable）。

本文件验证：
- dynamic：空轨道被跳过（语义对齐 switching 家族先例），正常轨道照常分析
  并产出 4 条 metric record；全空时 tracks=[]，分析器仍照常产出 outcome 类
  结果不 crash；
- tracking：空轨道先滤除；全空时走 "requires one unambiguous target track"
  受控 ValueError → worker 分支落 outcome_only（清晰降级，不是 crash）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from webapp.backend import worker_family_analysis
from webapp.tests.test_worker_telemetry import (
    _data_root,
    _run_process_one,
    _telemetry_job,
    _write_frozen_external_run,
)

ANALYSIS_ID = 911
ANALYSIS_REF = f"analysis:{ANALYSIS_ID}"

_DYNAMIC_METRIC_KEYS = {
    "dynamic_clicking.normalized_click_error",
    "dynamic_clicking.acquisition_time_ms",
    "dynamic_clicking.relative_velocity",
    "dynamic_clicking.target_state_accuracy",
}


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


def _snapshot(aim_family: str) -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 42,
        "canonical_time_window": _window(),
        "scenario_resolution": _resolution(aim_family),
        "sources": {},
        # raw input trace 源（字节读取在用例里 patch）。
        "trace": {
            "artifact_ref": "run:42:trace",
            "path": "/db-private/runs/42/trace.bin",
            "availability": "available",
            "format_version": 1,
        },
    }


def _crosshair(step_ms: int = 50) -> list[dict]:
    return [
        {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
        for time_ms in range(0, 1001, step_ms)
    ]


def _sample(time_ms: int, x: float = 960.0, y: float = 540.0) -> dict:
    # 默认与固定准星 (960, 540) 重合：几何关联要求准星在目标圆内。
    return {
        "canonical_time_ms": time_ms,
        "x": x,
        "y": y,
        "visible_radius": 10.0,
        "confidence": 1.0,
    }


def _trace_points_with_click() -> list[dict]:
    return [
        {"timestamp_ms": 0, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": 200, "dx": 0, "dy": 0, "buttons": 1},
    ]


def _spy_real_analyzer(module, name, captured: list):
    """捕获 payload 且仍调用真实分析器（断言装样 + 分析不 raise）。"""
    real = getattr(module, name)

    def wrapper(payload):
        captured.append(payload)
        return real(payload)

    return patch.object(module, name, wrapper)


# ---- dynamic_clicking（适配层）----


def _dynamic_visual(local_targets: dict[str, list[dict]]) -> dict:
    """合成 telemetry 形状 visual_result：每个声明 tid 一个 local_samples 键，
    窗外生命期的 tid 保持空样本列表（0 样本轨道）。"""
    local_samples: dict[str, list] = {"crosshair.position": _crosshair()}
    for tid, samples in local_targets.items():
        local_samples[f"target.{tid}.position"] = samples
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["dynamic_clicking"],
            "limitations": [],
        },
        "local_samples": local_samples,
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:{tid}", "limitations": []}
            for tid in local_targets
        ],
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }


def _run_dynamic(visual: dict, *, captured: list | None = None):
    from contextlib import ExitStack

    from kovaak_tracker import dynamic_clicking_analysis

    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("dynamic_clicking")}
    context_managers = [
        patch(
            "webapp.backend.worker._read_frozen_source_bytes",
            return_value=b"trace",
        ),
        patch(
            "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
            return_value=_trace_points_with_click(),
        ),
    ]
    if captured is not None:
        context_managers.append(
            _spy_real_analyzer(
                dynamic_clicking_analysis,
                "analyze_dynamic_clicking_v1",
                captured,
            ),
        )
    with ExitStack() as stack:
        for manager in context_managers:
            stack.enter_context(manager)
        return worker_family_analysis.run_dynamic_clicking_analysis(job, visual)


def test_dynamic_skips_empty_track_and_produces_metric_records():
    """空样本轨道 + 正常轨道：空轨道不进 payload，分析不抛且产出 4 条 metric
    record；窗内点击对正常轨道的归一化点击误差按语义可出。"""
    from contextlib import ExitStack

    from kovaak_tracker import dynamic_clicking_analysis

    visual = _dynamic_visual({
        "1": [],  # 窗外生命期：producer 声明 tid 但局内窗 0 样本
        "2": [_sample(time_ms) for time_ms in range(0, 1001, 50)],
    })
    captured: list = []
    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("dynamic_clicking")}

    with ExitStack() as stack:
        stack.enter_context(patch(
            "webapp.backend.worker._read_frozen_source_bytes",
            return_value=b"trace",
        ))
        stack.enter_context(patch(
            "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
            return_value=_trace_points_with_click(),
        ))
        stack.enter_context(_spy_real_analyzer(
            dynamic_clicking_analysis, "analyze_dynamic_clicking_v1", captured,
        ))
        result = worker_family_analysis.run_dynamic_clicking_analysis(job, visual)

    # 空轨道被跳过，只有正常轨道进入 payload。
    assert [track["track_ref"] for track in captured[0]["target_tracks"]] == [
        f"{ANALYSIS_REF}:target-track:2",
    ]
    # 分析器跑完（未因空轨道 raise）并产出 4 条 metric record。
    assert result["analysis_version"] == "dynamic_clicking.v1"
    assert set(result["metrics"]) == _DYNAMIC_METRIC_KEYS
    assert len(result["evidence_extension"]["metric_records"]) == 4
    # 窗内点击 + 唯一正常轨道 → 归一化点击误差按语义可出。
    error_metric = result["metrics"]["dynamic_clicking.normalized_click_error"]
    assert error_metric["availability"] == "available"


def test_dynamic_all_tracks_empty_still_produces_outcome_class_result():
    """全空：tracks=[]，分析器照常产出 outcome 类结果（4 条 metric record 全
    unavailable），不 crash。"""
    visual = _dynamic_visual({"1": [], "2": []})
    result = _run_dynamic(visual)

    assert result["analysis_version"] == "dynamic_clicking.v1"
    assert set(result["metrics"]) == _DYNAMIC_METRIC_KEYS
    assert all(
        metric["availability"] == "unavailable"
        for metric in result["metrics"].values()
    )


# ---- continuous_tracking（适配层）----


def _tracking_visual(local_targets: dict[str, list[dict]]) -> dict:
    local_samples: dict[str, list] = {"crosshair.position": _crosshair(step_ms=4)}
    for tid, samples in local_targets.items():
        local_samples[f"target.{tid}.position"] = samples
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "local_samples": local_samples,
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:{tid}", "limitations": []}
            for tid in local_targets
        ],
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "events": [],
            "outcome_associations": [],
        },
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }


def test_tracking_all_tracks_empty_raises_unambiguous_track_error():
    """tracking 全空轨道：枚举先滤空 → 既有 "requires one unambiguous target
    track" 受控 ValueError（worker 分支据此落 outcome_only），而不是在分析器
    内对空样本 crash。"""
    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("continuous_tracking")}
    visual = _tracking_visual({"1": []})

    with pytest.raises(ValueError, match="requires one unambiguous target track"):
        worker_family_analysis.run_continuous_tracking_analysis(job, visual)


def test_tracking_skips_empty_track_and_analyzes_surviving_track():
    """空轨道 + 正常轨道（≥200 样本）：空轨道不进多目标候选，正常轨道独立
    分析跑完。"""
    from kovaak_tracker import tracking_analysis

    job = {"id": ANALYSIS_ID, "input_snapshot": _snapshot("continuous_tracking")}
    visual = _tracking_visual({
        "1": [],  # 窗外生命期空轨道
        "2": [_sample(time_ms) for time_ms in range(0, 1001, 4)],
    })
    captured: list = []

    with _spy_real_analyzer(
        tracking_analysis, "analyze_continuous_tracking_v1", captured,
    ):
        result = worker_family_analysis.run_continuous_tracking_analysis(job, visual)

    assert captured[0]["target_track"]["track_ref"] == (
        f"{ANALYSIS_REF}:target-track:2"
    )
    assert result["schema_version"] == "continuous_tracking_analysis.v1"


# ---- worker 分支（端到端受控降级）----


def _telemetry_visual_with_empty_track(job: dict) -> dict:
    """producer 产物形状：声明的 tid 在局内 canonical 窗 0 样本。"""
    window = job["input_snapshot"]["canonical_time_window"]
    analysis_ref = f"analysis:{job['id']}"
    return {
        "schema_version": "visual_signal_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": dict(window),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "limitations": [],
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "producer_version": "telemetry_signals.v1",
            "quality_status": "accepted",
            "enabled_metric_families": ["tracking"],
            "track_count": 1,
            "observation_count": 0,
            "target_coverage": 0.0,
            "crosshair_coverage": 1.0,
            "completeness": "complete",
            "event_counts": {},
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": [
                {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
                for time_ms in range(1000, 2001, 50)
            ],
            # 窗外生命期：producer 声明 tid，但局内 canonical 窗 0 样本。
            "target.0.position": [],
        },
        "track_summaries": [
            {"track_ref": f"{analysis_ref}:target-track:0", "limitations": []},
        ],
    }


@pytest.mark.asyncio
async def test_tracking_empty_producer_tracks_degrade_to_outcome_only():
    """端到端：producer 只产出空轨道 → 真实适配层滤空后走受控 ValueError →
    worker 分支落 outcome_only（不 crash、不触发 CV 兜底）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)

    mocks = await _run_process_one(
        job,
        telemetry_builder=_telemetry_visual_with_empty_track,
    )

    mocks["cv_pipeline"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == "scenario_outcome_only.v1"
    assert result["deterministic"]["support_status"] == "outcome_only"
    assert result["deterministic"]["limitations"] == [
        "continuous_tracking_analysis_unavailable",
    ]
    assert {"code": "continuous_tracking_analyzer_unavailable"} in result["warnings"]
