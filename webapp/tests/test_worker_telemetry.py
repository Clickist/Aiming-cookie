"""worker 接入外部遥测 producer 的行为合同（telemetry_multimodal 切片）。

覆盖：遥测源 available → producer 被调用且 CV 子进程不触发；producer 抛错/
对齐回执不达标 → 回退 CV（warnings 可观测）；无视频可回退 → outcome_only；
无遥测源 → 行为与改动前完全一致。旁车数据为手工构造的最小 1 轮 fixture
（不依赖真实大文件；真实数据链路由 kovaak_tracker/test_telemetry_signals 覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import file_store, worker
from webapp.backend.contracts import ANALYSIS_RESULT_V2_SCHEMA_VERSION


def _data_root() -> Path:
    """conftest 的隔离测试数据根（每测重置）。"""
    return file_store._data_root()


CANONICAL_WINDOW = {
    "schema_version": "canonical_time_window.v1",
    "timebase_version": "time_alignment.v2",
    "start_ms": 1_000,
    "end_ms": 2_000,
    "duration_ms": 1_000,
    "start_source": "test_start",
    "end_source": "test_end",
    "stats_anchor_status": "missing",
    "stats_time_of_day_ms": None,
    "stats_local_to_utc_mapping": None,
    "warnings": [],
    "window_semantics": "half_open",
}

EXTERNAL_RUN_ID = "ext-workerfix01"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _scenario_resolution() -> dict:
    """continuous_tracking 的 active dispatch 分辨率（照抄 test_worker 夹具口径）。"""
    return {
        "schema_version": "scenario_resolution.v1",
        "scenario_hash": "fixture-hash",
        "display_name": "Fixture Tracking",
        "registry_version": "scenario_registry.test.v1",
        "manifest_version": "scenario_manifest.test.v1",
        "scenario_profile_ref": "scenario:tracking.fixture@1",
        "classification_source": "reviewed_registry",
        "classification_confidence": "confirmed",
        "profile_status": "active",
        "reviewed_at": "2026-07-20T00:00:00Z",
        "source_refs": ["review:fixture"],
        "supersedes": [],
        "manifest_status": "active",
        "fixture_ref": "fixture:scenario",
        "review_source_ref": "review:scenario",
        "manifest_reviewed_at": "2026-07-20T00:00:00Z",
        "family_gate_refs": ["gate:family"],
        "aim_family": "continuous_tracking",
        "subdomains": ["precision"],
        "target_motion": {"model": "predictable", "target_count_model": "single"},
        "allowed_analyzers": ["continuous_tracking.v1"],
        "allowed_metric_families": ["continuous_tracking"],
        "claim_ceiling": "family_specific",
        "family_analyzer_dispatch": "allowed",
        "limitations": [],
    }


def _sidecar_payloads() -> dict[str, bytes]:
    """1 轮最小旁车：静止单目标死在视口中心，一次点击，0.5s @~60Hz。"""
    views = b"".join(
        (
            '{"t": %.3f, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": 103.0}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    frames = b"".join(
        (
            '{"ev": "frame", "t": %.3f, "targets": [[0, 4000.0, 0.0, 0.0]]}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    inputs = b'{"t": 0.200, "btn": ["L_down"]}\n'
    bb = (
        b'{"challenges": [{"window_t": [0.0, 1.0], '
        b'"bots": [{"character": {"bb": {"radius": 60.0}}}]}]}\n'
    )
    return {
        "round.jsonl": frames,
        "views_01.jsonl": views,
        "inputs_01.jsonl": inputs,
        "bb.json": bb,
    }


def _write_frozen_external_run(
    tmp_data_root: Path,
    *,
    alignment_accepted: bool = True,
) -> Path:
    """在 DATA_ROOT 里落一个冻结 ext 目录 + meta.json（对齐回执可切换）。"""
    ext_dir = tmp_data_root / "external" / EXTERNAL_RUN_ID
    ext_dir.mkdir(parents=True)
    payloads = _sidecar_payloads()
    for name, payload in payloads.items():
        (ext_dir / name).write_bytes(payload)
    index = {
        "format_version": 1,
        "generator": "fixture",
        "params": {},
        "sources": [{
            "source": "upstream/fixture/round_01.jsonl",
            "outdir": str(ext_dir),
            "rounds": [{
                "round": 1,
                "file": "round_01.jsonl",
                "t_start": 0.0,
                "t_end": 0.5,
                "duration": 0.5,
                "n_frames": 32,
                "n_targets": 1,
                "n_moving_targets": 0,
                "targets": [{
                    "tid": 0,
                    "addr": 0,
                    "addr_hex": "0x0",
                    "motion": "static",
                    "n_lives": 1,
                    "lives": [{"t_start": 0.0, "t_end": 0.5, "n": 32}],
                }],
            }],
        }],
    }
    (ext_dir / "rounds_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8",
    )
    manifest = {
        "schema_version": "merge_manifest.v1",
        "generated": "2026-08-31T00:00:00",
        "round_dir": str(ext_dir),
        "rounds_index": str(ext_dir / "rounds_index.json"),
        "alignment": {"method": "fixture", "accepted": alignment_accepted},
        "check": {"median_deg": 0.4, "n": 1},
    }
    (ext_dir / "merge_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
    )
    meta = {
        "schema_version": "external_run.v1",
        "external_run_id": EXTERNAL_RUN_ID,
        "user_id": "u1",
        "origin": {
            "source_file": "fixture_0831_000000.jsonl",
            "round": 1,
            "round_file": "upstream/fixture/round_01.jsonl",
            "index_file": "upstream/fixture/rounds_index.json",
            "generator": "fixture",
            "format_version": 1,
            "params": {},
        },
        "fingerprints": {
            "round_sha256": _sha256(payloads["round.jsonl"]),
            "round_size": len(payloads["round.jsonl"]),
            "round_mtime_ns": 0,
            "import_parser_version": "external_run_import.v1",
        },
        "time": {"t_start": 0.0, "t_end": 0.5, "duration": 0.5},
        "scenario_proposal": {"source": "pending"},
        "pairing": {
            "matched_run_ids": [42],
            "pair_confidence": "coarse",
        },
        "sidecars": {
            "views": {
                "present": True,
                "sha256": _sha256(payloads["views_01.jsonl"]),
                "size": len(payloads["views_01.jsonl"]),
            },
            "inputs": {
                "present": True,
                "sha256": _sha256(payloads["inputs_01.jsonl"]),
                "size": len(payloads["inputs_01.jsonl"]),
            },
            "bb": {
                "present": True,
                "sha256": _sha256(payloads["bb.json"]),
                "size": len(payloads["bb.json"]),
            },
            "merge_manifest": {
                "present": True,
                "sha256": _sha256(
                    (ext_dir / "merge_manifest.json").read_bytes(),
                ),
                "size": (ext_dir / "merge_manifest.json").stat().st_size,
            },
        },
        "frames_path": f"external/{EXTERNAL_RUN_ID}/round.jsonl",
        "imported_at": "2026-08-31T00:00:00Z",
        "revisions": [],
    }
    (ext_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8",
    )
    return ext_dir


def _telemetry_source(ext_dir: Path) -> dict:
    return {
        "artifact_ref": f"external:{EXTERNAL_RUN_ID}",
        "availability": "available",
        "external_run_id": EXTERNAL_RUN_ID,
        "round": 1,
        "frames_path": str(ext_dir / "round.jsonl"),
        "sidecars": {},
        "pairing_confidence": "coarse",
        "reason": None,
    }


def _snapshot(ext_dir: Path, *, with_telemetry: bool = True) -> dict:
    sources: dict = {
        "stats": {
            "artifact_ref": "run:42:stats",
            "availability": "available",
        },
        "performance": {
            "artifact_ref": "run:42:performance",
            "availability": "available",
        },
    }
    if with_telemetry:
        sources["external_telemetry"] = _telemetry_source(ext_dir)
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 42,
        "canonical_time_window": dict(CANONICAL_WINDOW),
        "scenario_resolution": _scenario_resolution(),
        "sources": sources,
    }


def _telemetry_job(ext_dir: Path, *, with_telemetry: bool = True) -> dict:
    return {
        "id": 901,
        "user_id": "u1",
        "analysis_type": "continuous_tracking",
        "input_mode": "telemetry_multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": _snapshot(ext_dir, with_telemetry=with_telemetry),
        "video_path": None,
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-08-31 12:00:00",
    }


def _cv_visual() -> dict:
    """CV 子进程产物的最小同形桩（family 分析在用例里另行 patch）。"""
    return {
        "schema_version": "visual_signal_artifact.v1",
        "analysis_ref": "analysis:901",
        "canonical_time_window": dict(CANONICAL_WINDOW),
        "visual_quality_profile_ref": "visual-quality:cv-fixture@1",
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "limitations": [],
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "producer_version": "cv_fixture.v1",
            "quality_status": "accepted",
            "enabled_metric_families": ["tracking"],
            "track_count": 1,
            "observation_count": 3,
            "target_coverage": 1.0,
            "crosshair_coverage": 1.0,
            "completeness": "complete",
            "event_counts": {},
            "limitations": [],
        },
    }


def _family_fixture() -> dict:
    metric_key = "continuous_tracking.target_relative_error_px"
    return {
        "schema_version": "continuous_tracking_analysis.v1",
        "analysis_version": "continuous_tracking.v1",
        "analysis_ref": "analysis:901",
        "analysis_type": "continuous_tracking",
        "support_status": "supported",
        "scenario_motion_class": "predictable",
        "metrics": {
            metric_key: {
                "schema_version": "metric_record.v1",
                "metric_key": metric_key,
                "metric_version": f"{metric_key}.v1",
                "value": 8.0,
                "unit": "px",
                "availability": "available",
                "classification": "deterministic",
                "provenance": {"kind": "derived", "source_refs": []},
                "population": {"sample_count": 3, "valid_count": 3, "excluded_count": 0},
                "distribution": None,
                "condition_refs": [],
                "event_refs": [],
                "evidence_segment_refs": [],
                "coverage": 1.0,
                "confidence": 1.0,
                "limitations": [],
            },
        },
        "processed_rows": [],
        "comparison": None,
        "limitations": [],
    }


async def _run_process_one(
    job: dict,
    *,
    telemetry_builder=None,
    cv_pipeline=None,
    tracking_analysis=None,
    native_analysis=None,
    dynamic_analysis=None,
):
    """跑一次 process_one，返回 result 与各 mock。queue 与重活全部隔离；
    传 None 的分析函数不 patch（走真实实现）。"""
    from contextlib import ExitStack

    completed: list[dict] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def identity_evidence_commit(_job, result, **_kwargs):
        return result

    always = {
        "claim_next": patch(
            "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
        ),
        "heartbeat": patch(
            "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
        ),
        "mark_done": patch(
            "webapp.backend.queue.mark_done",
            new=AsyncMock(side_effect=mark_done),
        ),
        "parse_stats": patch(
            "webapp.backend.worker._parse_frozen_stats_for_visual",
            return_value=MagicMock(cm_per_360=None, fov=None),
        ),
        # 视频合同校验（冻结 MP4 指纹）不在本测范围（另有专测）；job 里的
        # video_path 只是“可回退 CV”的标记。
        "video_contract": patch(
            "webapp.backend.worker._assert_managed_video_matches_snapshot",
            return_value=None,
        ),
        "evidence_commit": patch(
            "webapp.backend.worker._maybe_commit_analysis_evidence",
            side_effect=identity_evidence_commit,
        ),
        "evidence_child": patch(
            "webapp.backend.worker.commit_continuous_tracking_evidence_isolated",
            new=AsyncMock(side_effect=lambda job, result, *a: result),
        ),
        "cv_plain": patch(
            "webapp.backend.worker.run_visual_preprocessing_isolated",
            new=AsyncMock(),
        ),
        # 默认也 patch CV tracking 管线：供“CV 不被触发”断言；需要回退行为的
        # 用例通过 cv_pipeline 参数提供 side_effect。
        "cv_pipeline": patch(
            "webapp.backend.worker.run_continuous_tracking_pipeline_isolated",
            new=AsyncMock(),
        ),
    }
    if cv_pipeline is not None:
        always["cv_pipeline"] = patch(
            "webapp.backend.worker.run_continuous_tracking_pipeline_isolated",
            new=AsyncMock(side_effect=cv_pipeline),
        )
    conditional = []
    if tracking_analysis is not None:
        conditional.append(("tracking", patch(
            "webapp.backend.worker.run_continuous_tracking_analysis",
            side_effect=tracking_analysis,
        )))
    if native_analysis is not None:
        conditional.append(("native", patch(
            "webapp.backend.worker.run_native_analysis",
            side_effect=native_analysis,
        )))
    if dynamic_analysis is not None:
        conditional.append(("dynamic", patch(
            "webapp.backend.worker.run_dynamic_clicking_analysis",
            side_effect=dynamic_analysis,
        )))
    if telemetry_builder is not None:
        conditional.append(("builder", patch(
            "webapp.backend.worker._build_external_telemetry_visual_result",
            side_effect=telemetry_builder,
        )))
    mocks: dict = {}
    with ExitStack() as stack:
        for name, patcher in [*always.items(), *conditional]:
            mocks[name] = stack.enter_context(patcher)
        assert await worker.process_one() is True
    assert len(completed) == 1
    return {"result": completed[0], **mocks}


@pytest.mark.asyncio
async def test_available_telemetry_uses_producer_without_cv():
    """①遥测源 available：producer 产出 visual_result，CV 子进程不被触发，
    tracking family adapter 消费 producer 产物并给出真实 metric records。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)

    # spy：保留真实 producer / adapter，仅记录调用与入参（接缝断言）。
    real_builder = worker._build_external_telemetry_visual_result
    builder_jobs: list[dict] = []

    def spy_builder(spy_job):
        builder_jobs.append(spy_job)
        return real_builder(spy_job)
    real_adapter = worker.run_continuous_tracking_analysis
    adapter_visuals: list[dict] = []

    def spy_adapter(spy_job, visual):
        adapter_visuals.append(visual)
        return real_adapter(spy_job, visual)

    mocks = await _run_process_one(
        job,
        telemetry_builder=spy_builder,
        tracking_analysis=spy_adapter,
    )

    assert len(builder_jobs) == 1
    mocks["cv_plain"].assert_not_called()
    mocks["cv_pipeline"].assert_not_called()
    mocks["evidence_child"].assert_not_called()

    # 接缝：producer 产物原样进入 family adapter（绑定到 analysis:{job_id}）。
    visual = adapter_visuals[0]
    assert visual["analysis_ref"] == "analysis:901"
    assert visual["canonical_time_window"] == job["input_snapshot"]["canonical_time_window"]
    assert visual["safe_summary"]["producer_version"] == "telemetry_signals.v1"
    assert set(visual["local_samples"]) == {
        "crosshair.position", "target.0.position",
    }

    result = mocks["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_version"] == "continuous_tracking.v1"
    metrics = result["deterministic"]["metrics"]
    assert any(
        key.startswith("continuous_tracking.") and metric.get("availability") == "available"
        for key, metric in metrics.items()
    ), f"available tracking metrics missing: {sorted(metrics)}"
    # 来源标记：producer 身份按 reviewed producer 同款通道进入结果。
    assert (
        result["deterministic"]["visual_validation"]["producer_version"]
        == "telemetry_signals.v1"
    )
    assert result["deterministic"]["visual_quality_profile_ref"] == (
        "visual-quality:external_telemetry@telemetry_signals.v1:fov103"
    )
    assert not [
        warning for warning in result["warnings"]
        if str(warning.get("code", "")).startswith("external_telemetry_unavailable")
    ]


@pytest.mark.asyncio
async def test_producer_failure_falls_back_to_cv_with_warning():
    """②producer 抛错 + video 可用：回退 CV 管线，结果 warnings/limitations
    带 external_telemetry_unavailable:<code>。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)
    job["video_path"] = "managed.mp4"  # 遥测档的视频是可选增强；回退 CV 需要 it

    def _boom(_job):
        raise worker.TelemetryPipelineError("telemetry_projection_failed")

    mocks = await _run_process_one(
        job,
        telemetry_builder=_boom,
        cv_pipeline=lambda _job: (_cv_visual(), _family_fixture()),
        tracking_analysis=lambda _job, _visual: _family_fixture(),
    )

    mocks["cv_pipeline"].assert_called_once()
    mocks["builder"].assert_called_once()
    result = mocks["result"]
    marker = "external_telemetry_unavailable:telemetry_projection_failed"
    assert {"code": marker} in result["warnings"]
    assert marker in result["deterministic"]["limitations"]
    # 指标仍然来自 CV 兜底路径（family fixture 的 metric 保留）。
    assert result["deterministic"]["metrics"], "CV fallback metrics missing"


@pytest.mark.asyncio
async def test_alignment_rejected_manifest_falls_back_to_cv():
    """③merge_manifest.alignment.accepted=false：真实 producer 闸门拒绝，同回退。"""
    ext_dir = _write_frozen_external_run(_data_root(), alignment_accepted=False)
    job = _telemetry_job(ext_dir)
    job["video_path"] = "managed.mp4"

    real_builder = worker._build_external_telemetry_visual_result
    builder_calls: list[dict] = []

    def spy_builder(spy_job):
        builder_calls.append(spy_job)
        return real_builder(spy_job)

    mocks = await _run_process_one(
        job,
        telemetry_builder=spy_builder,
        cv_pipeline=lambda _job: (_cv_visual(), _family_fixture()),
        tracking_analysis=lambda _job, _visual: _family_fixture(),
    )

    assert len(builder_calls) == 1
    mocks["cv_pipeline"].assert_called_once()
    marker = "external_telemetry_unavailable:telemetry_alignment_not_accepted"
    result = mocks["result"]
    assert {"code": marker} in result["warnings"]
    assert marker in result["deterministic"]["limitations"]


@pytest.mark.asyncio
async def test_producer_failure_without_video_lands_outcome_only():
    """producer 抛错且无 video：不空转 CV，直接 outcome_only + 降级标记。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)  # video_path 保持 None

    def _boom(_job):
        raise worker.TelemetryPipelineError("telemetry_projection_failed")

    mocks = await _run_process_one(job, telemetry_builder=_boom)

    mocks["cv_pipeline"].assert_not_called()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == "scenario_outcome_only.v1"
    assert result["deterministic"]["support_status"] == "outcome_only"
    marker = "external_telemetry_unavailable:telemetry_projection_failed"
    assert result["deterministic"]["limitations"] == [marker]
    assert {"code": marker} in result["warnings"]


@pytest.mark.asyncio
async def test_without_telemetry_source_behavior_is_unchanged():
    """④无遥测源：不碰 producer，行为与改动前完全一致（CV 管线 + 无降级标记）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir, with_telemetry=False)
    job["video_path"] = "managed.mp4"

    mocks = await _run_process_one(
        job,
        telemetry_builder=MagicMock(),  # 不该被调用；仅作断言桩
        cv_pipeline=lambda _job: (_cv_visual(), _family_fixture()),
        tracking_analysis=lambda _job, _visual: _family_fixture(),
    )

    mocks["builder"].assert_not_called()
    mocks["cv_pipeline"].assert_called_once()
    result = mocks["result"]
    assert result["deterministic"]["metrics"]
    assert not [
        warning for warning in result["warnings"]
        if str(warning.get("code", "")).startswith("external_telemetry_unavailable")
    ]
    assert "external_telemetry_unavailable" not in str(result["deterministic"])
    # 结果来源标记是 CV 桩的 producer，不是遥测。
    assert (
        result["deterministic"]["visual_validation"]["producer_version"]
        == "cv_fixture.v1"
    )


def test_history_trends_treats_telemetry_and_cv_profiles_incomparable():
    """档位归一后遥测与 CV 不混档：producer 档案 ref 不同即判不可比，
    同为遥测（同 ref）时档案层不阻拦。"""
    from webapp.backend.history_trends import _family_comparability_reason

    metric = {"condition_refs": ["c1"]}
    missing = "continuous_tracking_comparability_missing"

    def _result(profile_ref: str) -> dict:
        return {
            "deterministic": {
                "visual_quality_profile_ref": profile_ref,
                "scenario_motion_class": "predictable",
            },
        }

    telemetry = _result("visual-quality:external_telemetry@telemetry_signals.v1")
    cv = _result("visual-quality:reviewed_tracking@cv.v1")
    reason = _family_comparability_reason(
        telemetry, cv, metric, dict(metric), missing,
    )
    assert reason == "visual_quality_profile_mismatch"

    same_ref = _result("visual-quality:external_telemetry@telemetry_signals.v1")
    assert _family_comparability_reason(
        telemetry, same_ref, metric, dict(metric), missing,
    ) is None


def _static_scenario_resolution() -> dict:
    """native_flicking.v1 的 active dispatch 分辨率（static_clicking family）。"""
    resolution = _scenario_resolution()
    resolution.update({
        "aim_family": "static_clicking",
        "display_name": "Fixture Static",
        "allowed_analyzers": [worker.NATIVE_ANALYSIS_VERSION],
        "allowed_metric_families": ["static_clicking"],
        "target_motion": {"model": "static", "target_count_model": "single"},
    })
    return resolution


def _native_result_stub() -> dict:
    """run_native_analysis 的最小桩：deterministic available（partial 判定前提）。"""
    return {
        "status": "available",
        "analysis_type": "flicking",
        "deterministic": {"metrics": {}, "timeline": []},
        "evidence": {
            "sources": {},
            # contracts 校验要求 alignment 与冻结 canonical window 一致。
            "alignment": {
                "status": "aligned",
                "challenge_start_epoch_ms": CANONICAL_WINDOW["start_ms"],
                "challenge_end_epoch_ms": CANONICAL_WINDOW["end_ms"],
            },
            "coverage": 1.0,
            "warnings": [],
        },
    }


@pytest.mark.asyncio
async def test_telemetry_without_video_records_missing_mp4_and_partial_outcome():
    """P1-2：telemetry_multimodal 档 native 分支 + 未挂视频 + producer 成功——
    evidence mp4 不得假标 available（按真实情况记 missing，与 artifact manifest
    同词汇），queue 的 partial_outcome 判定（读 evidence mp4）据此正确落 partial。"""
    from webapp.backend import queue

    ext_dir = _write_frozen_external_run(_data_root())
    sid = await queue.enqueue("u1", "", "", input_mode="telemetry_multimodal")
    # 真实租约：session 进入 running 且 worker_id 与 worker.WORKER_ID 匹配，
    # 让走真实 queue.mark_done（partial_outcome 判定在其中）。session 侧补
    # kovaak_run_id（测试 seeding，同 test_queue._rewrite_session 惯例），
    # 使 persistence 校验的 run_ref 与 fixture snapshot 的 run:42 匹配。
    session_path = file_store._data_root() / "sessions" / f"{sid}.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["kovaak_run_id"] = 42
    session_path.write_text(json.dumps(session), encoding="utf-8")
    assert await queue.claim_next(worker.WORKER_ID) is not None
    job = _telemetry_job(ext_dir)
    job["id"] = sid
    job["input_snapshot"]["scenario_resolution"] = _static_scenario_resolution()

    def identity_evidence_commit(_job, result, **_kwargs):
        return result

    with patch(
        "webapp.backend.queue.claim_next", new=AsyncMock(return_value=job),
    ), patch(
        "webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True),
    ), patch(
        "webapp.backend.worker._assert_managed_video_matches_snapshot",
        return_value=None,
    ), patch(
        "webapp.backend.worker.run_native_analysis",
        return_value=(_native_result_stub(), None),  # multimodal 档带 parsed_stats
    ), patch(
        "webapp.backend.worker._maybe_commit_analysis_evidence",
        side_effect=identity_evidence_commit,
    ):
        assert await worker.process_one() is True

    result = file_store.read_json(f"sessions/{sid}.json")["result"]
    # evidence mp4 与 artifact manifest 一致地如实记录 missing（而非 available）。
    assert result["evidence"]["availability"]["mp4"] == "missing"
    assert result["evidence"]["sources"]["mp4"]["availability"] == "missing"
    assert result["artifact_manifest"] is not None
    # partial 判定输入齐全（deterministic available + mp4 missing）→ 落 partial。
    session = file_store.read_json(f"sessions/{sid}.json")
    assert session["partial_outcome"] == {
        "status": "partial",
        "native_preserved": True,
        "visual_status": "unavailable",
        "reason_code": "video_unavailable",
    }


@pytest.mark.asyncio
async def test_tracking_adapter_timeout_falls_back_to_outcome_only():
    """P2-6：本进程 tracking adapter 挂死不得超过 CV 子进程同量级时限；
    超时沿 ContinuousTrackingAnalysisProcessError 回退 outcome_only 且可观测。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)

    def slow_adapter(_job, _visual):
        time.sleep(0.3)  # to_thread 线程内阻塞，模拟 adapter 挂死
        return _family_fixture()

    with patch.object(worker, "VISUAL_WORKER_TIMEOUT_SECONDS", 0.05):
        mocks = await _run_process_one(job, tracking_analysis=slow_adapter)

    # 超时不触发 CV 管线：沿 outcome_only 回退链落点。
    mocks["cv_pipeline"].assert_not_called()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == "scenario_outcome_only.v1"
    assert result["deterministic"]["support_status"] == "outcome_only"
    assert result["deterministic"]["limitations"] == [
        "continuous_tracking_analysis_unavailable",
    ]
    assert {"code": "continuous_tracking_analyzer_unavailable"} in result["warnings"]


def _baseline_scenario_resolution(aim_family: str) -> dict:
    """baseline 档分辨率（telemetry_observed 分类、unlisted——0901 真机 33/34
    session 快照口径）：dispatch 落 {family}.baseline.v1。"""
    resolution = _scenario_resolution()
    resolution.update({
        "aim_family": aim_family,
        "display_name": f"Fixture {aim_family}",
        "scenario_profile_ref": None,
        "classification_source": "telemetry_observed",
        "classification_confidence": "candidate",
        "profile_status": "unknown",
        "reviewed_at": None,
        "source_refs": [],
        "supersedes": [],
        "manifest_status": "unlisted",
        "fixture_ref": None,
        "review_source_ref": None,
        "manifest_reviewed_at": None,
        "family_gate_refs": [],
        "allowed_analyzers": [f"{aim_family}.baseline.v1"],
        "allowed_metric_families": ["outcome", "input_kinematics"],
        "claim_ceiling": "descriptive_only",
        "target_motion": {"model": "static", "target_count_model": "concurrent"},
        "limitations": ["exact_visual_profile_unavailable"],
    })
    return resolution


def _dynamic_family_fixture() -> dict:
    """run_dynamic_clicking_analysis 的最小桩（形状对齐 _family_fixture）。"""
    metric_key = "dynamic_clicking.normalized_click_error"
    fixture = _family_fixture()
    fixture.update({
        "schema_version": "dynamic_clicking_analysis.v1",
        "analysis_version": "dynamic_clicking.v1",
        "analysis_type": "dynamic_clicking",
    })
    metric = next(iter(fixture["metrics"].values()))
    metric.update({
        "metric_key": metric_key,
        "metric_version": f"{metric_key}.v1",
    })
    fixture["metrics"] = {metric_key: metric}
    return fixture


def _cv_visual_stub(*, enabled_families: tuple[str, ...] = ("tracking",)) -> dict:
    """CV 子进程产物桩（形状同 _cv_visual）；enabled_families 供各 family
    质量门调整，避免与模块级 _cv_visual 混用。"""
    visual = _cv_visual()
    visual["quality"]["enabled_metric_families"] = list(enabled_families)
    visual["safe_summary"]["enabled_metric_families"] = list(enabled_families)
    return visual


@pytest.mark.asyncio
async def test_baseline_dispatch_with_telemetry_uses_producer():
    """baseline 档 + 遥测源：producer 被调用（真值投影进 evidence 链），
    CV 子进程不触发，结果保持 baseline 档且无降级标记（0901 真机 33/34
    静默跳过遥测的回归）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)
    job["input_snapshot"]["scenario_resolution"] = _baseline_scenario_resolution(
        "dynamic_clicking",
    )

    mocks = await _run_process_one(
        job,
        telemetry_builder=MagicMock(return_value=_cv_visual()),
        native_analysis=lambda *_args, **_kwargs: (_native_result_stub(), None),
    )

    mocks["builder"].assert_called_once()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == "dynamic_clicking.baseline.v1"
    assert result["analysis_type"] == "dynamic_clicking"
    assert not [
        warning for warning in result["warnings"]
        if str(warning.get("code", "")).startswith("external_telemetry_unavailable")
    ]


@pytest.mark.asyncio
async def test_baseline_producer_failure_falls_back_to_cv_with_marker():
    """baseline 档 + producer 失败 + video 可用：回退 CV 子进程，结果带
    external_telemetry_unavailable 降级标记（_mark_telemetry_fallback）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)
    job["input_snapshot"]["scenario_resolution"] = _baseline_scenario_resolution(
        "continuous_tracking",
    )
    job["video_path"] = "managed.mp4"

    def _boom(_job):
        raise worker.TelemetryPipelineError("telemetry_projection_failed")

    mocks = await _run_process_one(
        job,
        telemetry_builder=_boom,
        native_analysis=lambda *_args, **_kwargs: (_native_result_stub(), None),
    )

    mocks["builder"].assert_called_once()
    mocks["cv_plain"].assert_called_once()
    result = mocks["result"]
    assert result["analysis_version"] == "continuous_tracking.baseline.v1"
    marker = "external_telemetry_unavailable:telemetry_projection_failed"
    assert {"code": marker} in result["warnings"]
    assert marker in result["deterministic"]["limitations"]


@pytest.mark.asyncio
async def test_baseline_without_telemetry_keeps_previous_path():
    """baseline 档无遥测源：不碰 producer 也不碰 CV 视觉管线，行为与改动前
    完全一致（generic 视觉链路不在本测范围）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir, with_telemetry=False)
    job["input_snapshot"]["scenario_resolution"] = _baseline_scenario_resolution(
        "dynamic_clicking",
    )

    mocks = await _run_process_one(
        job,
        telemetry_builder=MagicMock(),  # 不该被调用；仅作断言桩
        native_analysis=lambda *_args, **_kwargs: (_native_result_stub(), None),
    )

    mocks["builder"].assert_not_called()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == "dynamic_clicking.baseline.v1"
    assert "external_telemetry_unavailable" not in str(result["warnings"])


@pytest.mark.asyncio
async def test_static_native_without_telemetry_keeps_cv_path():
    """static（native_flicking.v1）无遥测源：producer 不被调用，visual 走
    CV 子进程——与接线前行为一致。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir, with_telemetry=False)
    job["input_snapshot"]["scenario_resolution"] = _static_scenario_resolution()
    job["video_path"] = "managed.mp4"

    mocks = await _run_process_one(
        job,
        telemetry_builder=MagicMock(),  # 不该被调用；仅作断言桩
        native_analysis=lambda *_args, **_kwargs: (_native_result_stub(), None),
    )

    mocks["builder"].assert_not_called()
    mocks["cv_plain"].assert_called_once()
    result = mocks["result"]
    assert result["analysis_version"] == worker.NATIVE_ANALYSIS_VERSION
    assert "external_telemetry_unavailable" not in str(result["warnings"])


@pytest.mark.asyncio
async def test_static_native_with_telemetry_uses_producer():
    """static（native_flicking.v1）+ 遥测源：producer 被调用、CV 子进程不触发
    （既有接线的分支级回归锚点）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)
    job["input_snapshot"]["scenario_resolution"] = _static_scenario_resolution()

    mocks = await _run_process_one(
        job,
        telemetry_builder=MagicMock(return_value=_cv_visual()),
        native_analysis=lambda *_args, **_kwargs: (_native_result_stub(), None),
    )

    mocks["builder"].assert_called_once()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == worker.NATIVE_ANALYSIS_VERSION
    assert not [
        warning for warning in result["warnings"]
        if str(warning.get("code", "")).startswith("external_telemetry_unavailable")
    ]


@pytest.mark.asyncio
async def test_dynamic_dispatch_with_telemetry_uses_producer():
    """dynamic（dynamic_clicking.v1）+ 遥测源：producer 被调用、CV 子进程不
    触发，family adapter 消费 producer 产物（既有接线的分支级回归锚点）。"""
    ext_dir = _write_frozen_external_run(_data_root())
    job = _telemetry_job(ext_dir)
    resolution = _static_scenario_resolution()
    resolution.update({
        "aim_family": "dynamic_clicking",
        "display_name": "Fixture Dynamic",
        "allowed_analyzers": [worker.DYNAMIC_CLICKING_ANALYSIS_VERSION],
        "allowed_metric_families": ["dynamic_clicking"],
        "claim_ceiling": "family_specific",
        "target_motion": {"model": "reactive", "target_count_model": "concurrent"},
    })
    job["input_snapshot"]["scenario_resolution"] = resolution

    async def _no_baseline(*_args, **_kwargs):
        return {"comparable": False, "reason": "no_baseline"}

    with patch(
        "webapp.backend.history_trends.matched_dynamic_baseline_for_user",
        new=AsyncMock(side_effect=_no_baseline),
    ):
        mocks = await _run_process_one(
            job,
            telemetry_builder=MagicMock(
                return_value=_cv_visual_stub(enabled_families=("dynamic_clicking",)),
            ),
            dynamic_analysis=lambda _job, _visual, _bundle: _dynamic_family_fixture(),
        )

    mocks["builder"].assert_called_once()
    mocks["cv_plain"].assert_not_called()
    result = mocks["result"]
    assert result["analysis_version"] == worker.DYNAMIC_CLICKING_ANALYSIS_VERSION
    assert result["analysis_type"] == "dynamic_clicking"
    assert not [
        warning for warning in result["warnings"]
        if str(warning.get("code", "")).startswith("external_telemetry_unavailable")
    ]
