"""Progressive-disclosure output tests: decode preroll reaches Coach anchors."""
from __future__ import annotations

import pytest

from webapp.backend.analysis_output import _build_overview


def _result_with_timeline(timeline: list[dict], issues: list[dict], **extra) -> dict:
    return {
        "analysis_id": "analysis:9",
        "input_mode": "multimodal",
        "completed_at": "2026-08-16T00:00:00Z",
        "deterministic": {
            "metrics": {},
            "timeline": timeline,
            "diagnosis": {"issues": issues},
        },
        "evidence": {"sources": {}, "coverage": 1.0},
        "input_snapshot": {"scenario": "fixture"},
        "scenario": {},
        **extra,
    }


def test_overview_anchor_times_subtract_the_decode_preroll():
    issue = {
        "signal": "overshoot",
        "event_refs": ["analysis:9:event:flick:1"],
        "metric_refs": ["path_length"],
    }
    timeline = [
        {
            "id": "flick:1",
            "peak_ms": 500.0,
            "relative_ms": 480.0,
            "metrics": {"path_length": 120.0},
        },
    ]

    stamped = _build_overview(
        9, _result_with_timeline(
            timeline, [issue], video_decode_preroll_ms=121.97,
        ),
    )
    unstamped = _build_overview(9, _result_with_timeline(timeline, [issue]))

    assert stamped["video_decode_preroll_ms"] == pytest.approx(121.97)
    assert "video_decode_preroll_ms" not in unstamped
    stamped_anchor = stamped["diagnosis"]["issues"][0]["time_anchors"][0]
    unstamped_anchor = unstamped["diagnosis"]["issues"][0]["time_anchors"][0]
    # peak_ms wins over relative_ms; the preroll shifts the video anchor left.
    assert unstamped_anchor["ms"] == pytest.approx(500.0)
    assert stamped_anchor["ms"] == pytest.approx(378.03)
    assert stamped_anchor["path_length"] == pytest.approx(120.0)


def test_overview_anchor_clamps_at_zero_when_preroll_exceeds_the_event_time():
    issue = {
        "signal": "overshoot",
        "event_refs": ["analysis:9:event:flick:1"],
        "metric_refs": [],
    }
    timeline = [{"id": "flick:1", "relative_ms": 80.0}]
    overview = _build_overview(
        9,
        _result_with_timeline(
            timeline, [issue], video_decode_preroll_ms=121.97,
        ),
    )
    anchor = overview["diagnosis"]["issues"][0]["time_anchors"][0]
    assert anchor["ms"] == 0.0


def test_overview_exposes_video_evidence_unavailability_reason():
    # Coach 读 overview.json：录制失败原因必须在这里可见，
    # 否则只能按“用户没录视频”自顾自降级讲解。
    unavailable = _result_with_timeline([], [])
    unavailable["input_snapshot"]["sources"] = {
        "video": {
            "artifact_ref": "run:42:video",
            "availability": "unavailable",
            "reason": "video_coverage_gap",
            "ownership": "run",
        },
    }
    overview = _build_overview(9, unavailable)
    assert overview["video_evidence"] == {
        "availability": "unavailable",
        "reason": "video_coverage_gap",
    }

    # 无视频源或 legacy 结果不输出该键。
    assert "video_evidence" not in _build_overview(
        9, _result_with_timeline([], []),
    )


def test_metrics_and_summary_carry_the_generic_knowledge_refs():
    """generic 视觉指标的知识桥必须透传到 metrics.json 和 overview 摘要。"""
    from webapp.backend.analysis_output import _build_metrics

    result = _result_with_timeline([], []).copy()
    result["deterministic"]["metrics"] = {
        "path_length": {"value": 100.0, "unit": "raw_counts"},
        "tracking.generic.error_median_deg": {
            "value": 1.5,
            "unit": "degrees",
            "metric_version": "generic_aim_families.v1",
            "classification": "deterministic",
            "availability": "available",
            "knowledge_refs": ["metric:tracking_error"],
        },
    }

    metrics = _build_metrics(result)
    assert metrics["tracking.generic.error_median_deg"]["knowledge_refs"] == [
        "metric:tracking_error",
    ]
    # 不带桥的指标保持原形态。
    assert "knowledge_refs" not in metrics["path_length"]

    overview = _build_overview(9, result)
    assert overview["metrics_summary"]["tracking.generic.error_median_deg"][
        "knowledge_refs"
    ] == ["metric:tracking_error"]


def test_overview_passes_through_data_quality_signals(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """[lives 2026-10-05d] data_quality 透传：外部遥测源 known_issues + 结果层/
    指标层 limitations（事实信号字段原样透传，Coach 据此说"这局数字别当真"）；
    无遥测源/无 limitations 时退化为空列表，绝不抛错。"""
    from webapp.backend import config, external_telemetry_store as telemetry_store

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    run_id = "ext-0123456789abcdef"
    telemetry_store.save_meta(run_id, {
        "schema_version": telemetry_store.SCHEMA_VERSION,
        "external_run_id": run_id,
        "quality": {"known_issues": ["cleaner_short_respawn_merge"], "gates": {}},
    })
    result = _result_with_timeline([], [], input_snapshot={
        "scenario": "fixture",
        "sources": {"external_telemetry": {
            "availability": "available", "external_run_id": run_id,
        }},
    })
    result["deterministic"]["limitations"] = ["mouse_trajectory_from_external_telemetry"]
    result["deterministic"]["metrics"] = {
        "t2k_p50": {"value": 0.5, "unit": "s",
                    "limitations": ["mouse_trajectory_from_external_telemetry",
                                    "alignment_partial"]},
    }

    data_quality = _build_overview(9, result)["data_quality"]
    assert data_quality["known_issues"] == ["cleaner_short_respawn_merge"]
    # 去重并保持首次出现顺序
    assert data_quality["limitations"] == [
        "mouse_trajectory_from_external_telemetry", "alignment_partial",
    ]

    # 无外部遥测源、无 limitations → 空列表（确定性形状，不抛错）
    bare = _build_overview(9, _result_with_timeline([], []))
    assert bare["data_quality"] == {"known_issues": [], "limitations": []}
