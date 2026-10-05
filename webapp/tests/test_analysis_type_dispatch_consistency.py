"""1.3.10 热修回归：static_clicking 请求类型与分发/落盘的三方一致性。

1.3.9 P0（报障包 20261005-191602 定罪）：家族判型把 static_clicking 场景的
请求类型映射为 "static_clicking"，但 worker 分发端 static native 分支无条件
先行（2026-10-04 精选档案层退役），native 分析器产出固定 "flicking" →
mark_done 落盘校验 analysis_result.v2 analysis_type 与请求比对必炸 →
结果永不落盘，新用户冷启动首分析永远停在"排队中"。

回归锁三方一致：请求类型（analysis_service 映射）== 分发引擎（worker
_scenario_dispatch）实际产出（native flicking）== 落盘校验期望；并锁
"native_flicking.v1" 与 "flicking" 的配对（read_models 家族明细投影的
分发键）不被拆散。中毒会话重试必须归正继承类型（queue.requeue_for_retry）。
"""

from __future__ import annotations

import pytest

from webapp.backend import analysis_service, config, queue
from webapp.backend.contracts import validate_scenario_resolution_v1
from kovaak_tracker.scenario_profiles import _family_baseline_resolution


def _static_clicking_resolution() -> dict:
    resolution = _family_baseline_resolution(
        scenario_hash="hotfix-hash",
        display_name="1wall 6targets small",
        registry_version="test",
        manifest_version="test",
        aim_family="static_clicking",
        classification_source="name_heuristic",
        classification_confidence="candidate",
        target_motion={"model": "static", "target_count_model": "concurrent"},
        # unlisted 场景 limitations 不允许为空（contracts allow_empty 仅限 active）。
        limitations=["hotfix_regression_candidate"],
    )
    assert "static_clicking.baseline.v1" in resolution["allowed_analyzers"]
    validate_scenario_resolution_v1(resolution)
    return resolution


def _snapshot(resolution: dict) -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "scenario_resolution": resolution,
    }


def test_static_clicking_family_resolution_requests_flicking():
    # 1.3.9 的映射在 allowed_analyzers 含 baseline 时返回 "static_clicking"，
    # 而该 baseline 分支在 worker 分发端不可达 → 冷启动必炸。请求类型必须
    # 与分发引擎真实产出（native flicking → "flicking"）一致。
    snapshot = _snapshot(_static_clicking_resolution())
    assert analysis_service._analysis_type_for_snapshot(snapshot) == "flicking"


def test_static_clicking_dispatch_is_native_flicking_and_keypair_intact():
    from webapp.backend import worker

    resolution = _static_clicking_resolution()
    job = {
        "analysis_type": "flicking",
        "input_snapshot": _snapshot(resolution),
    }
    # native 分支无条件先行，baseline 对 static_clicking 不可达。
    assert worker._scenario_dispatch(job, "multimodal") == worker.NATIVE_ANALYSIS_VERSION
    assert worker._scenario_dispatch(job, "input_native") == worker.NATIVE_ANALYSIS_VERSION
    # read_models 家族明细投影按 (analysis_type, analysis_version) 分发，
    # 该配对一旦拆散，静态局家族明细整体变 unsupported。
    assert worker.NATIVE_ANALYSIS_VERSION == "native_flicking.v1"
    assert analysis_service._analysis_type_for_snapshot(job["input_snapshot"]) == "flicking"


@pytest.mark.asyncio
async def test_retry_coerces_poisoned_static_clicking_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    sid = await queue.enqueue(
        "poison-owner", str(video), "",
        analysis_type="static_clicking",
        input_mode="multimodal",
        input_snapshot=_snapshot(_static_clicking_resolution()),
    )
    await queue.claim_next("retry-worker")
    await queue.mark_failed(sid, "analysis_result.v2 analysis_type must match", worker_id="retry-worker")

    retried = await queue.requeue_for_retry(sid)
    assert retried is not None
    # 中毒类型原样继承会让热修后的重试继续必炸；必须归正为分发引擎产出。
    assert retried["analysis_type"] == "flicking"
