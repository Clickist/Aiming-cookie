"""真实数据全链路验收（worker 遥测接线切片）。

上游数据在另一个仓库（FPSAimTrainer analysis/external/cleaned，SIDECARS 合同），
只读消费、不入 git；目录不存在时整测 SKIP。链路：

1. 临时 DATA_ROOT 里先建配对 KovaaK run（epoch 窗口只与 final_0831 round 3 相交），
   再 ExternalTelemetryWatcher.scan_once 导入 cleaned 轮次（含旁车冻结）；
2. build_analysis_input_snapshot 产出可用 external_telemetry 源 + telemetry_multimodal 档；
3. process_one（queue 隔离、CV 不触发）产出 continuous_tracking 指标与 producer 标记；
4. producer 几何自检（击杀点击角误差）与 merge_manifest.check 同量级（<1°）。
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import (
    config,
    external_telemetry_store,
    file_store,
    kovaak_run_store,
    worker,
)
from webapp.backend.contracts import ANALYSIS_RESULT_V2_SCHEMA_VERSION
from webapp.backend.external_telemetry_ingest import (
    ExternalTelemetryWatcher,
    epoch_anchor_for_source,
)
from webapp.tests.test_worker_telemetry import _scenario_resolution


def _dynamic_scenario_resolution() -> dict:
    resolution = _scenario_resolution()
    resolution.update({
        "aim_family": "dynamic_clicking",
        "display_name": "target_poll_out_0831_003140",
        "allowed_analyzers": ["dynamic_clicking.v1"],
        "allowed_metric_families": ["dynamic_clicking"],
        "target_motion": {"model": "predictable", "target_count_model": "concurrent"},
    })
    return resolution

REAL_CLEANED = Path(r"C:\Users\袜子\Desktop\FPSAimTrainer\analysis\external\cleaned")
SESSION = REAL_CLEANED / "final_0831"
SESSION_DIR = SESSION / "target_poll_out_0831_003140"
SOURCE_FILE = "target_poll_out_0831_003140.jsonl"
PIPELINE_ROUND = 7    # 单目标单生命轮：continuous_tracking family 的真实样本
GEOMETRY_ROUND = 3    # manifest.check 有回执的多目标点击轮

if not SESSION_DIR.is_dir():
    pytest.skip(
        "external telemetry sidecar data is not available on this machine",
        allow_module_level=True,
    )


def _data_root() -> Path:
    return file_store._data_root()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _round_entry(round_number: int) -> dict:
    index = _load_json(SESSION / "rounds_index.json")
    for source in index.get("sources") or []:
        for entry in source.get("rounds") or []:
            if entry.get("round") == round_number:
                return entry
    raise AssertionError(f"round {round_number} missing in rounds_index")


def _all_rounds() -> list[dict]:
    index = _load_json(SESSION / "rounds_index.json")
    return [
        entry
        for source in index.get("sources") or []
        for entry in source.get("rounds") or []
    ]


def _pair_window_ms(round_number: int) -> tuple[int, int]:
    """配对窗口取该轮 epoch 窗口内收 1.5s，并让开所有重叠轮的边界（±1s
    配对容差之上），保证只有目标轮的 meta 配对到本测试的 KovaaK run。"""
    anchor = epoch_anchor_for_source(SOURCE_FILE)
    assert anchor.get("epoch_start_est") is not None
    rounds = _all_rounds()
    entry = next(item for item in rounds if item["round"] == round_number)
    # 轮范围可能相互重叠（如 r4 覆盖 r5/r6 的时间段）：边界要对全体轮收敛。
    earlier_ends = [
        float(item["t_end"]) for item in rounds if item["t_start"] < entry["t_start"]
    ]
    later_starts = [
        float(item["t_start"]) for item in rounds if item["t_start"] > entry["t_start"]
    ]
    start_s = float(entry["t_start"]) + 1.5
    if earlier_ends:
        start_s = max(start_s, max(earlier_ends) + 1.05)
    end_s = float(entry["t_end"]) - 1.5
    if later_starts:
        end_s = min(end_s, min(later_starts) - 1.05)
    assert end_s > start_s
    start = int((anchor["epoch_start_est"] + start_s) * 1000)
    end = int((anchor["epoch_start_est"] + end_s) * 1000)
    return start, end


async def _import_real_session(round_number: int = PIPELINE_ROUND):
    """配对 KovaaK run + watcher.scan_once 导入（含旁车冻结）到临时 DATA_ROOT。"""
    start_ms, end_ms = _pair_window_ms(round_number)
    stats = _data_root() / "e2e-stats.csv"
    performance = _data_root() / "e2e-performance.perf"
    trace = _data_root() / "e2e-trace.bin"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    # 整窗 2Hz 交替按键 -> ~280 个左键 rising edge（动态点击的 click 锚点）。
    trace_points = []
    for offset in range(0, (end_ms - start_ms), 500):
        trace_points.append({
            "timestamp_ms": start_ms + offset,
            "dx": 1,
            "dy": 1,
            "buttons": (offset // 500) % 2,
        })
    kovaak_run_store.write_mouse_snapshot(trace, trace_points)
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="e2e-final0831",
        scenario=f"{SOURCE_FILE.removesuffix('.jsonl')}#round{round_number}",
        stats_path=str(stats),
        performance_path=str(performance),
        mouse_trace_path=str(trace),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
    )
    duration = end_ms - start_ms
    run = await kovaak_run_store.set_run_alignment(
        run["id"],
        config.DESKTOP_LOCAL_PROFILE,
        state="resolved",
        summary={
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": duration,
            "start_source": "e2e_pair_window",
            "end_source": "e2e_pair_window",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        start_epoch_ms=start_ms,
        end_epoch_ms=end_ms,
    ) or run
    watcher = ExternalTelemetryWatcher(SESSION, stable_scans=1, source="e2e")
    summary = watcher.scan_once()
    return run, summary


@pytest.mark.asyncio
async def test_full_worker_pipeline_on_real_final0831_round7():
    """真实 final_0831 数据：导入 → 配对 → 快照可用 → process_one 产出指标。

    用单目标轮（round 7）走 continuous_tracking family：multi-target 点击轮
    （round 1-3）在 event_bundle.v1 的 512 事件预算下会按 CV 同款合同
    fail-closed（visual_event_budget_exceeded -> 全 family 停用 -> outcome_only）。
    """
    run, summary = await _import_real_session(PIPELINE_ROUND)

    # 导入门：13 轮全部成功导入且无失败。
    assert summary["failed"] == 0
    assert summary["imported"] >= 13

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
    )
    source = snapshot["sources"]["external_telemetry"]
    assert source["availability"] == "available"
    assert source["round"] == PIPELINE_ROUND
    start_ms, end_ms = _pair_window_ms(PIPELINE_ROUND)
    assert snapshot["canonical_time_window"]["start_ms"] == start_ms

    from webapp.backend.source_requirements import validate_source_requirements

    gate = validate_source_requirements(snapshot)
    assert gate["selected_mode"] == "telemetry_multimodal"

    job = {
        "id": 424242,
        "user_id": config.DESKTOP_LOCAL_PROFILE,
        "analysis_type": "continuous_tracking",
        "input_mode": "telemetry_multimodal",
        "kovaak_run_id": run["id"],
        "input_snapshot": {
            **snapshot,
            "scenario_resolution": _scenario_resolution(),
        },
        "video_path": None,
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-08-31 12:00:00",
    }

    completed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def identity_evidence(_job, result, **_kwargs):
        return result

    with patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.queue.mark_done",
        new=AsyncMock(side_effect=mark_done),
    ), patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual",
        return_value=MagicMock(cm_per_360=None, fov=None),
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=identity_evidence,
    ), patch(
        "webapp.backend.worker.run_visual_preprocessing_isolated",
        new=AsyncMock(),
    ) as cv_plain, patch(
        "webapp.backend.worker.run_continuous_tracking_pipeline_isolated",
        new=AsyncMock(),
    ) as cv_pipeline:
        assert await worker.process_one() is True

    # CV 子进程一次都不该被触发。
    cv_plain.assert_not_called()
    cv_pipeline.assert_not_called()

    assert len(completed) == 1
    result = completed[0]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_version"] == "continuous_tracking.v1", (
        result["analysis_version"],
        result.get("warnings"),
        (result.get("deterministic") or {}).get("limitations"),
    )
    metrics = result["deterministic"]["metrics"]
    available = {
        key: metric
        for key, metric in metrics.items()
        if metric.get("availability") == "available"
    }
    assert available, f"no available tracking metrics: {sorted(metrics)}"
    print(
        "e2e real-data metrics:",
        {key: round(float(metric["value"]), 3) for key, metric in available.items()},
    )
    assert (
        result["deterministic"]["visual_validation"]["producer_version"]
        == "telemetry_signals.v1"
    )
    assert result["deterministic"]["visual_quality_profile_ref"] == (
        "visual-quality:external_telemetry@telemetry_signals.v1:fov103"
    )


@pytest.mark.asyncio
async def test_mega_round_fails_closed_to_observable_outcome_only():
    """多目标点击大轮（round 3，事件数 >512）按 CV 同款合同 fail-closed：
    producer 产物质量门停用全部 family -> 落 outcome_only 且可观测。
    这就是 71 轮 target_poll 回填数据在当前合同下的真实产物形态。"""
    run, _summary = await _import_real_session(GEOMETRY_ROUND)
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
    )
    job = {
        "id": 424243,
        "user_id": config.DESKTOP_LOCAL_PROFILE,
        "analysis_type": "dynamic_clicking",
        "input_mode": "telemetry_multimodal",
        "kovaak_run_id": run["id"],
        "input_snapshot": {
            **snapshot,
            "scenario_resolution": _dynamic_scenario_resolution(),
        },
        "video_path": None,
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-08-31 12:00:00",
    }
    completed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def identity_evidence(_job, result, **_kwargs):
        return result

    with patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.queue.mark_done",
        new=AsyncMock(side_effect=mark_done),
    ), patch(
        "webapp.backend.worker._parse_frozen_stats_for_visual",
        return_value=MagicMock(cm_per_360=None, fov=None),
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=identity_evidence,
    ), patch(
        "webapp.backend.worker.run_visual_preprocessing_isolated",
        new=AsyncMock(),
    ):
        assert await worker.process_one() is True

    assert len(completed) == 1
    result = completed[0]
    assert result["analysis_version"] == "scenario_outcome_only.v1"
    limitations = (result.get("deterministic") or {}).get("limitations") or []
    assert "dynamic_clicking_visual_quality_unavailable" in limitations
    # producer 视觉产物本身仍如实进入结果（来源可区分，降级可观测）。
    assert (
        result["deterministic"]["visual_validation"]["producer_version"]
        == "telemetry_signals.v1"
    )
    assert "visual_event_budget_exceeded" in str(
        result["deterministic"]["visual_validation"].get("limitations")
    )


@pytest.mark.asyncio
async def test_producer_geometry_matches_manifest_check_magnitude():
    """producer 几何自检 vs merge_manifest.check（round 3）：同量级且 <1°。"""
    # 复用导入链路拿到冻结副本（ext id 由 dedup key 派生，直接扫临时根）。
    await _import_real_session(GEOMETRY_ROUND)
    external_run_id = _find_round_meta_external_id(GEOMETRY_ROUND)
    start_ms, end_ms = _pair_window_ms(GEOMETRY_ROUND)
    snapshot = {
        "schema_version": "analysis_input_snapshot.v3",
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "timebase_version": "time_alignment.v2",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "start_source": "e2e",
            "end_source": "e2e",
            "stats_anchor_status": "missing",
            "warnings": [],
            "window_semantics": "half_open",
        },
        "sources": {
            "external_telemetry": {
                "availability": "available",
                "external_run_id": external_run_id,
                "round": GEOMETRY_ROUND,
                "frames_path": str(
                    _data_root() / "external" / external_run_id / "round.jsonl"
                ),
            },
        },
    }
    job = {"id": 424243, "input_snapshot": snapshot}
    visual = worker._build_external_telemetry_visual_result(job)

    errors_deg = _kill_click_angle_errors(visual)
    assert len(errors_deg) >= 50, f"too few kill-click pairs: {len(errors_deg)}"
    producer_median = statistics.median(errors_deg)

    manifest = _load_json(SESSION_DIR / "merge_manifest.json")
    manifest_round = next(
        entry
        for entry in manifest["check"]["per_round"]
        if entry["round"] == GEOMETRY_ROUND
    )
    print(
        f"geometry check round {GEOMETRY_ROUND}: producer n={len(errors_deg)} "
        f"median={producer_median:.3f}deg | manifest median="
        f"{manifest_round['median_deg']}deg (n={manifest_round['n']})"
    )
    # SIDECARS §5/§7 同一口径：池化中位 <1.0°，且与 manifest 回执同量级。
    assert producer_median < 1.0
    assert producer_median < manifest_round["median_deg"] * 3 + 0.1


def _find_round_meta_external_id(round_number: int) -> str:
    for path in file_store.list_subdirs("external"):
        if not path.name.startswith("ext-"):
            continue
        meta = external_telemetry_store.load_meta(path.name)
        if meta is None:
            continue
        if (meta.get("origin") or {}).get("round") == round_number:
            return str(meta["external_run_id"])
    raise AssertionError(f"imported round-{round_number} meta not found")


def _kill_click_angle_errors(visual: dict) -> list[float]:
    """击杀点击瞬间 准心->目标 角误差（与 kovaak_tracker 测试同口径复算）。"""
    round_number = GEOMETRY_ROUND
    entry = _round_entry(round_number)
    mapping = visual["video_time_mapping"]
    origin = mapping["canonical_origin_ms"] - mapping["source_pts_origin_ms"]
    selector = visual["visual_runtime_selector"]
    hfov = float(selector["fov"])
    focal_px = (1920.0 / 2.0) / math.tan(math.radians(hfov / 2.0))
    clicks: list[int] = []
    for line in (SESSION_DIR / f"inputs_{round_number:02d}.jsonl").read_text(
        encoding="utf-8",
    ).splitlines():
        record = json.loads(line)
        if "L_down" in (record.get("btn") or []):
            clicks.append(int(round(float(record["t"]) * 1000.0)) + origin)
    clicks.sort()

    errors: list[float] = []
    for target in entry["targets"]:
        track_key = f"target.{int(target['tid'])}.position"
        track = visual["local_samples"].get(track_key) or []
        times = [sample["canonical_time_ms"] for sample in track]
        for life in target.get("lives") or []:
            death_ms = int(round(float(life["t_end"]) * 1000.0)) + origin
            click_index = bisect.bisect_right(clicks, death_ms) - 1
            if click_index < 0 or death_ms - clicks[click_index] > 250:
                continue
            click_ms = clicks[click_index]
            life_start_ms = int(round(float(life["t_start"]) * 1000.0)) + origin
            lo = bisect.bisect_left(times, life_start_ms)
            hi = bisect.bisect_right(times, death_ms)
            window_times = times[lo:hi]
            if not window_times:
                continue
            nearest = min(window_times, key=lambda item: abs(item - click_ms))
            sample = track[bisect.bisect_left(times, nearest)]
            offset_x = abs(sample["x"] - 1920.0 / 2.0)
            offset_y = abs(sample["y"] - 1080.0 / 2.0)
            deg_x = math.degrees(math.atan(offset_x / focal_px))
            deg_y = math.degrees(math.atan(offset_y / focal_px))
            errors.append(math.hypot(deg_x, deg_y))
    return errors
