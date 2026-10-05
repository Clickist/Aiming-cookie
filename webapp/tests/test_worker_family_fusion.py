"""Adapter-level fusion wiring tests (worker_family_analysis).

覆盖融合接线（不重复 tests/test_input_fusion.py 的模块级用例）：
- ``_switching_press_fusion_inputs``：trace 缺席返回 ``{}``；trace + 遥测
  shot 沿组装出融合沿与交叉验证摘要。
- ``_apply_fused_alignment_metric``：kill < 2 不动指标；齐备时替换
  alignment 记录并同步 evidence_extension。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from kovaak_tracker.analysis_evidence import validate_event_bundle_v1
from webapp.backend import worker_family_analysis as wfa
from webapp.backend.kovaak_run_store import write_mouse_snapshot

ANALYSIS_REF = "analysis:fusion-adapter:1"


def _frozen_trace(path: Path) -> dict:
    data = path.read_bytes()
    stat = path.stat()
    return {
        "artifact_ref": "run:1:trace",
        "path": str(path.resolve()),
        "availability": "available",
        "format_version": 2,
        "fingerprint": {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mtime_ns": stat.st_mtime_ns,
        },
    }


def _trace_points_button_taps(edge_times, end_ms):
    # 写入侧 canonicalize 会丢弃全零位移点，窗内先放一个 buttons=0 的运动
    # 点，保证首个按压沿之前存在"未按压"状态（left_press_edges 沿既有
    # "窗首状态未知不构成沿"口径）。
    points = [{
        "timestamp_ms": 1_000_000, "dx": 1, "dy": 0, "buttons": 0,
    }]
    pressed = 0
    edges = set(edge_times)
    for time_ms in range(1, end_ms):
        if time_ms in edges:
            pressed = 1
        elif time_ms - 1 in edges:
            pressed = 0
        points.append({
            "timestamp_ms": 1_000_000 + time_ms, "dx": 0, "dy": 0,
            "buttons": pressed,
        })
    return points


def test_switching_press_fusion_inputs_skip_without_trace(tmp_path):
    snapshot = {"sources": {}, "trace": None}
    job = {"id": 1, "input_snapshot": snapshot}
    window = {"start_ms": 1_000_000, "end_ms": 1_001_000}
    assert wfa._switching_press_fusion_inputs(job, {}, window) == {}
    snapshot["trace"] = {"availability": "missing"}
    assert wfa._switching_press_fusion_inputs(job, {}, window) == {}


def test_switching_press_fusion_inputs_fuses_trace_and_shot_events(tmp_path):
    trace_path = tmp_path / "trace.bin"
    write_mouse_snapshot(
        trace_path,
        _trace_points_button_taps([30, 80, 150], end_ms=200),
    )
    job = {
        "id": 1,
        "input_snapshot": {"sources": {}, "trace": _frozen_trace(trace_path)},
    }
    window = {"start_ms": 1_000_000, "end_ms": 1_000_200}
    visual_result = {"event_bundle": validate_event_bundle_v1({
        "schema_version": "event_bundle.v1",
        "analysis_ref": ANALYSIS_REF,
        "events": [
            {
                "event_id": f"{ANALYSIS_REF}:event:telemetry:{index}",
                "event_kind": "shot",
                "start_ms": 1_000_000 + time_ms,
                "end_ms": 1_000_000 + time_ms,
                "actor_refs": [],
                "source_refs": [ANALYSIS_REF],
                "confidence": 1.0,
                "attributes": {},
                "limitations": [],
            }
            for index, time_ms in enumerate((32, 78), 1)
        ],
        "outcome_associations": [],
    })}
    fused = wfa._switching_press_fusion_inputs(job, visual_result, window)
    edges = fused["press_edges"]
    # 遥测 32/78 与 trace 30/80 配对（残差 ±2，取遥测时刻）；trace 150 回填。
    assert [edge["time_ms"] for edge in edges] == [1_000_032, 1_000_078, 1_000_150]
    assert [edge["source"] for edge in edges] == [
        "telemetry", "telemetry", "trace",
    ]
    assert fused["press_edge_validation"]["matched"] == 2
    assert fused["press_edge_validation"]["trace_only"] == 1
    assert fused["press_edge_validation"]["residual_max_ms"] == 2


def _descriptor_result() -> dict:
    descriptor = {
        "schema_version": "metric_record.v1",
        "metric_key": "continuous_tracking.alignment_latency_ms",
        "metric_version": "continuous_tracking.alignment_latency_ms.v1",
        "value": None,
        "unit": "ms",
        "availability": "unavailable",
        "classification": "deterministic",
        "provenance": {"kind": "derived", "source_refs": [ANALYSIS_REF]},
        "population": {
            "sample_count": 1, "valid_count": 0, "excluded_count": 1,
        },
        "distribution": None,
        "condition_refs": [],
        "event_refs": [],
        "evidence_segment_refs": [],
        "coverage": 0.0,
        "confidence": 0.0,
        "limitations": ["capture_alignment_descriptor_not_human_response"],
    }
    return {
        "schema_version": "continuous_tracking_analysis.v1",
        "metrics": {
            "continuous_tracking.alignment_latency_ms": dict(descriptor),
        },
        "evidence_extension": {"metric_records": [dict(descriptor)]},
    }


def test_alignment_fusion_keeps_descriptor_without_two_kills(tmp_path, monkeypatch):
    trace_path = tmp_path / "trace.bin"
    write_mouse_snapshot(trace_path, [
        {"timestamp_ms": 1_000_000 + t, "dx": 5, "dy": 0, "buttons": 0}
        for t in range(0, 100)
    ])
    job = {
        "id": 1,
        "input_snapshot": {"sources": {}, "trace": _frozen_trace(trace_path)},
        "calibration_snapshot": {"cm_per_360": {"value": 54.43, "source": "stats"}},
    }
    monkeypatch.setattr(
        wfa, "_parse_frozen_stats_for_visual",
        lambda snapshot: SimpleNamespace(dpi=1600),
    )
    visual_result = {"event_bundle": {"events": []}}
    result = _descriptor_result()
    wfa._apply_fused_alignment_metric(
        job, visual_result, result,
        analysis_ref=ANALYSIS_REF,
        window={"start_ms": 1_000_000, "end_ms": 1_000_100},
    )
    metric = result["metrics"]["continuous_tracking.alignment_latency_ms"]
    assert metric["value"] is None
    assert metric["availability"] == "unavailable"
    assert "capture_alignment_descriptor_not_human_response" in metric["limitations"]
    assert "alignment_latency_from_fused_movement_onset" not in metric["limitations"]


def test_alignment_fusion_replaces_metric_and_syncs_extension(tmp_path, monkeypatch):
    trace_path = tmp_path / "trace.bin"
    # leave=50ms 后从 60ms 起向右 10 counts/ms。
    points = [
        {"timestamp_ms": 1_000_000 + t, "dx": 0, "dy": 0, "buttons": 0}
        for t in range(0, 60)
    ] + [
        {"timestamp_ms": 1_000_000 + t, "dx": 10, "dy": 0, "buttons": 0}
        for t in range(60, 160)
    ]
    write_mouse_snapshot(trace_path, points)
    job = {
        "id": 1,
        "input_snapshot": {"sources": {}, "trace": _frozen_trace(trace_path)},
        "calibration_snapshot": {"cm_per_360": {"value": 54.43, "source": "stats"}},
    }
    monkeypatch.setattr(
        wfa, "_parse_frozen_stats_for_visual",
        lambda snapshot: SimpleNamespace(dpi=1600),
    )
    track_ref = f"{ANALYSIS_REF}:target-track:0"
    events = [
        {
            "event_id": f"{ANALYSIS_REF}:event:telemetry:{index}",
            "event_kind": kind,
            "start_ms": 1_000_000 + time_ms,
            "end_ms": 1_000_000 + time_ms,
            "actor_refs": [track_ref],
            "source_refs": [ANALYSIS_REF],
            "confidence": 1.0,
            "attributes": {},
            "limitations": [],
        }
        for index, (kind, time_ms) in enumerate(
            (("kill", 50), ("target_change_point", 52), ("kill", 120)), 1,
        )
    ]
    visual_result = {
        "event_bundle": validate_event_bundle_v1({
            "schema_version": "event_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "events": events,
            "outcome_associations": [],
        }),
        "local_samples": {
            "crosshair.position": [
                {"canonical_time_ms": 1_000_000, "x": 960.0, "y": 540.0},
            ],
            "target.0.position": [
                {"canonical_time_ms": 1_000_052, "x": 1060.0, "y": 540.0},
            ],
        },
        "visual_runtime_selector": {"fov": 103.0},
    }
    result = _descriptor_result()
    wfa._apply_fused_alignment_metric(
        job, visual_result, result,
        analysis_ref=ANALYSIS_REF,
        window={"start_ms": 1_000_000, "end_ms": 1_000_160},
    )
    metric = result["metrics"]["continuous_tracking.alignment_latency_ms"]
    assert metric["availability"] == "available"
    # anchor = max(50, 52) = 52；首个 >= 阈值且朝目标的桶在 60ms → 8ms。
    assert metric["value"] == 8.0
    assert "alignment_latency_from_fused_movement_onset" in metric["limitations"]
    assert "alignment_anchor_authoritative_kill_to_next_birth" in metric["limitations"]
    extension_records = {
        record["metric_key"]
        for record in result["evidence_extension"]["metric_records"]
    }
    assert extension_records == {"continuous_tracking.alignment_latency_ms"}
    fused_in_extension = next(
        record
        for record in result["evidence_extension"]["metric_records"]
        if record["metric_key"] == "continuous_tracking.alignment_latency_ms"
    )
    assert fused_in_extension["value"] == 8.0
