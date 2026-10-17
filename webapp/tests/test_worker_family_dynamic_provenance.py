"""聚焦测试：dynamic clicking 遥测来源标注（mouse_trajectory_*）跨层传递。

无原生 Raw trace 时 dynamic 路径用遥测 inputs 合成等价点击输入并获得来源
标注；本文件用真实链路（temp DATA_ROOT 冻结 inputs/meta/manifest，无
adapter/analyzer/投影 stub）验证该标注经 family → worker 投影（含
overview）保留：

① 遥测 inputs 合成轨迹 → 每条 metric 与 deterministic/scenario/overview
   各承载面均带来源标注，且既有 quality limitation 不被覆盖；
② 冻结旁车内坏行/回跳记录被跳过 → 除来源标注外，坏行 skip 标注同样跨层保留；
③ 原生 raw trace 在场 → 走 trace 分支（点列来自 trace），不加任何遥测标注。

fixture 复用 test_telemetry_input_adapter 的冻结 ext 目录。
"""

from __future__ import annotations

import json

import pytest

from webapp.backend import worker as worker_mod
from webapp.backend import worker_family_analysis
from webapp.backend.analysis_output import _build_overview
from webapp.backend.telemetry_input_adapter import (
    MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
    MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED,
)
from webapp.tests.test_telemetry_input_adapter import (
    CANONICAL_WINDOW,
    START_MS,
    _sha256,
    _snapshot,
    _write_frozen_external_run,
)

ANALYSIS_ID = 4242
ANALYSIS_REF = f"analysis:{ANALYSIS_ID}"

# 遥测旁车 L_down 沿落在窗口起点 +650ms（test_telemetry_input_adapter 的
# _input_records 口径）；原生 trace 用例改用 +700ms 以区分点列来源。
TELEMETRY_CLICK_MS = START_MS + 650
RAW_TRACE_CLICK_MS = START_MS + 700

_RESOLUTION = {
    "schema_version": "scenario_resolution.v1",
    "scenario_hash": "unreviewed-hash",
    "display_name": "Review Fixture Dynamic",
    "registry_version": "scenario_registry.test.v1",
    "manifest_version": "scenario_manifest.test.v1",
    "scenario_profile_ref": None,
    "classification_source": "local_scenario_definition",
    "classification_confidence": "confirmed",
    "profile_status": "unknown",
    "reviewed_at": None,
    "source_refs": [],
    "supersedes": [],
    "manifest_status": "unlisted",
    "fixture_ref": None,
    "review_source_ref": None,
    "manifest_reviewed_at": None,
    "family_gate_refs": [],
    "aim_family": "dynamic_clicking",
    "subdomains": ["reactive", "control"],
    "target_motion": {"model": "reactive", "target_count_model": "concurrent"},
    "allowed_analyzers": ["dynamic_clicking.baseline.v1", "dynamic_clicking.v1"],
    "allowed_metric_families": ["outcome", "input_kinematics", "dynamic_clicking"],
    "claim_ceiling": "descriptive_only",
    "family_analyzer_dispatch": "allowed",
    "limitations": [
        "exact_visual_profile_unavailable",
        "target_relative_facts_unavailable",
    ],
}


def _dynamic_job(ext_dir) -> dict:
    """telemetry-only 作业：无 raw trace、有可用外部遥测源 + dynamic 分辨率。"""
    snapshot = _snapshot(ext_dir)
    snapshot["scenario_resolution"] = dict(_RESOLUTION)
    return {
        "id": ANALYSIS_ID,
        "user_id": "local",
        "analysis_type": "dynamic_clicking",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
    }


def _visual() -> dict:
    """固定准星 + 单目标轨道（+500/+650/+800ms 采样，准星在目标圆内）。"""
    crosshair = [
        {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
        for time_ms in (START_MS + 500, START_MS + 650, START_MS + 800)
    ]
    target = [
        {
            "canonical_time_ms": time_ms,
            "x": 940.0,
            "y": 540.0,
            "visible_radius": 20.0,
            "confidence": 1.0,
        }
        for time_ms in (START_MS + 500, START_MS + 650, START_MS + 800)
    ]
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": dict(CANONICAL_WINDOW),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["dynamic_clicking"],
            # 生产者既有 quality limitation（bb 缺失兜底半径）：合并来源标注
            # 时不得覆盖/替换它。
            "limitations": ["bb_missing_default_radius"],
        },
        "local_samples": {
            "crosshair.position": crosshair,
            "target.1.position": target,
        },
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:1", "limitations": []},
        ],
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
    }


def _run_real_chain(job: dict, visual: dict) -> tuple[dict, dict]:
    """真实 family 适配（含真实 adapter/analyzer）+ 真实 worker 结果投影。"""
    family_result = worker_family_analysis.run_dynamic_clicking_analysis(
        job, visual, None,
    )
    result = worker_mod._build_dynamic_result_v2(
        job,
        family_result,
        visual,
        created_at="2026-10-17T00:00:00Z",
        completed_at="2026-10-17T00:00:01Z",
    )
    return family_result, result


def _limitation_surfaces(family_result: dict, result: dict) -> dict[str, set]:
    """非指标级 limitations 承载面（含 overview）。"""
    overview = _build_overview(ANALYSIS_ID, result)
    return {
        "family": set(family_result["limitations"]),
        "deterministic": set(result["deterministic"]["limitations"]),
        "scenario": set(result["scenario"]["limitations"]),
        "overview_scenario": set(overview["scenario_info"]["limitations"]),
        "overview_data_quality": set(overview["data_quality"]["limitations"]),
    }


def _assert_marker_everywhere(family_result: dict, result: dict, marker: str) -> None:
    """marker 必须出现在每个承载面，且每条 metric 各自携带（非并集漏检）。"""
    for name, limitations in _limitation_surfaces(family_result, result).items():
        assert marker in limitations, name
    assert family_result["metrics"], "expected family metric records"
    for key, metric in family_result["metrics"].items():
        assert marker in (metric.get("limitations") or []), f"family metric {key}"
    for key, metric in result["deterministic"]["metrics"].items():
        assert marker in (metric.get("limitations") or []), f"deterministic metric {key}"


def _assert_no_telemetry_marker(family_result: dict, result: dict) -> None:
    """任何承载面、任何 metric 都不得出现 mouse_trajectory_* 标注。"""
    def offending(limitations) -> list[str]:
        return [
            item for item in (limitations or [])
            if isinstance(item, str) and item.startswith("mouse_trajectory_")
        ]

    for name, limitations in _limitation_surfaces(family_result, result).items():
        assert not offending(limitations), name
    for key, metric in family_result["metrics"].items():
        assert not offending(metric.get("limitations")), f"family metric {key}"
    for key, metric in result["deterministic"]["metrics"].items():
        assert not offending(metric.get("limitations")), f"deterministic metric {key}"


# ---------------------------------------------------------------- ① 来源标注


def test_telemetry_provenance_marker_survives_to_result_and_overview():
    ext_dir = _write_frozen_external_run()
    job = _dynamic_job(ext_dir)
    visual = _visual()

    family_result, result = _run_real_chain(job, visual)

    # 真实链路锚：adapter 合成点击（+650ms）→ 分析器产出可算行。
    assert family_result["analysis_version"] == "dynamic_clicking.v1"
    assert family_result["support_status"] == "supported"
    row = family_result["processed_rows"][0]
    assert row["click_time_ms"] == TELEMETRY_CLICK_MS
    assert row["normalized_click_error"] == pytest.approx(1.0)

    _assert_marker_everywhere(
        family_result, result, MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
    )
    # 合并是追加而非覆盖：生产者既有 quality limitation 原样保留。
    assert "bb_missing_default_radius" in family_result["limitations"]
    assert "bb_missing_default_radius" in result["deterministic"]["limitations"]


# ---------------------------------------------------------------- ② 坏行 skip


def test_bad_row_skip_annotation_survives_to_result():
    ext_dir = _write_frozen_external_run()
    inputs_path = ext_dir / "inputs_01.jsonl"
    lines = inputs_path.read_text(encoding="utf-8").splitlines()
    # 旁车包含坏 JSON 与时间回跳记录；本用例只验证跳过标注的传播。
    lines.append("{not-json")
    lines.append(json.dumps({"t": 1.0, "dx": 9, "dy": 9, "btn": []}))
    inputs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    job = _dynamic_job(ext_dir)
    visual = _visual()

    family_result, result = _run_real_chain(job, visual)

    assert family_result["processed_rows"][0]["click_time_ms"] == TELEMETRY_CLICK_MS
    _assert_marker_everywhere(
        family_result, result, MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
    )
    _assert_marker_everywhere(
        family_result, result, MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED,
    )


# ---------------------------------------------------------------- ③ 原生 raw


def test_native_raw_trace_path_carries_no_telemetry_marker(tmp_path):
    from webapp.backend.kovaak_snapshot_codec import write_mouse_snapshot

    ext_dir = _write_frozen_external_run()
    job = _dynamic_job(ext_dir)
    snapshot = job["input_snapshot"]

    trace_path = tmp_path / "trace.bin"
    write_mouse_snapshot(trace_path, [
        {"timestamp_ms": START_MS + 500, "dx": 5, "dy": 0, "buttons": 0},
        {"timestamp_ms": RAW_TRACE_CLICK_MS, "dx": 3, "dy": 4, "buttons": 1},
        {"timestamp_ms": START_MS + 800, "dx": 0, "dy": 0, "buttons": 0},
    ])
    trace_bytes = trace_path.read_bytes()
    stat = trace_path.stat()
    snapshot["trace"] = {
        "artifact_ref": "run:42:trace",
        "path": str(trace_path),
        "availability": "available",
        "format_version": 2,
        "fingerprint": {
            "sha256": _sha256(trace_bytes),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
    }
    visual = _visual()

    family_result, result = _run_real_chain(job, visual)

    # 点列来自 trace（点击 +700ms），而非遥测旁车（+650ms）。
    assert family_result["processed_rows"][0]["click_time_ms"] == RAW_TRACE_CLICK_MS
    _assert_no_telemetry_marker(family_result, result)
