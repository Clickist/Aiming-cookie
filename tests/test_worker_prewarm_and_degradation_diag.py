"""[regression 2026-10-05] 降级日志可诊断性 + 科学栈预热门移除回归锁。

背景纠正：打包版 outcome_only 降级曾归因于「预热门动态引用失效」，该定论
已证伪——实测根因是父管道 read 与 SciPy 导入冲突（修复在父管道侧）；
子进程预热只能热缓存、不能修主进程 DLL 锁。lifespan 自动 spawn 与 worker
领分析前最长 1500s 的预热等待门已整体移除（句柄/常量同删）。本文件保留：
1. 回归锁：process_one 领取前不得再出现预热等待或子进程 spawn。
2. 降级 warning 必须带 __cause__ 类型与摘要（600s 超时 vs 分析器真错误
   在报障包里可分辨）——该诊断保留。
3. 遥测 producer 实际运行时留耗时打点（报障包时序）。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess

import webapp.backend.config as config
import webapp.backend.worker as worker

_WORKER_LOGGER = "webapp.backend.worker"


async def _no_op_async(*args, **kwargs):
    return None


async def _async_result(value):
    return value


def test_process_one_claims_without_science_prewarm_gate(monkeypatch, caplog):
    """回归锁（1005 移除预热门）：领取前不得再有预热等待或子进程 spawn。

    历史实现：lifespan spawn 预热子进程并把句柄写入 config，process_one 在
    queue.claim_next 前等它退出（最长 1500s 兜底）。该门已随根因修复移除：
    实测死锁根因是父管道 read 与 SciPy 导入冲突（修复在父管道侧），子进程
    预热不能修主进程 DLL 锁。本测试模拟最坏情形——config 上仍有残留句柄、
    且 Popen 被禁用：若门被重新引入，wait 会先于 claim 出现（事件顺序断言
    失败），或发生 spawn（Popen 探针记录）；二者都让本测试失败。
    """
    events: list[str] = []

    class _PrewarmHandleSpy:
        def wait(self, timeout) -> int:
            events.append(f"prewarm_wait:{timeout}")
            return 0

    # raising=False：常量已删，这里专门模拟“未来有人把句柄重新写进 config”。
    monkeypatch.setattr(
        config, "SCIENCE_PREWARM_CHILD", _PrewarmHandleSpy(), raising=False,
    )

    def _forbidden_popen(*args, **kwargs):
        events.append("popen")
        raise AssertionError("process_one 领取前不得 spawn 子进程（科学栈预热门）")

    monkeypatch.setattr(subprocess, "Popen", _forbidden_popen)

    async def _fake_claim_next(worker_id):
        events.append("claim_next")
        return None

    monkeypatch.setattr(worker.queue, "claim_next", _fake_claim_next)

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        handled = asyncio.run(asyncio.wait_for(worker.process_one(), timeout=15))

    assert handled is False
    # 唯一允许的事件是领取本身：无 wait 阻塞、无 spawn。
    assert events == ["claim_next"]
    assert "science prewarm gate waited" not in caplog.text


def test_telemetry_visual_producer_logs_timing(monkeypatch, caplog):
    """遥测视觉 producer 实际运行时要留耗时打点（INFO 一行）。"""
    monkeypatch.setattr(
        worker, "_external_telemetry_source", lambda job: {"availability": "available"},
    )
    monkeypatch.setattr(
        worker, "_build_external_telemetry_visual_result", lambda job: {"safe_summary": {}},
    )

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        visual, code = asyncio.run(worker._external_telemetry_visual_or_none({"id": 7}))

    assert visual == {"safe_summary": {}}
    assert code is None
    assert any(
        "external telemetry visual producer" in record.getMessage()
        and "took=" in record.getMessage()
        for record in caplog.records
    )


def test_continuous_tracking_degradation_warning_includes_cause(
    monkeypatch, caplog,
):
    """adapter 失败走 outcome_only 降级时，warning 必须可查底层异常类型。"""
    job = {
        "id": 101,
        "input_mode": "multimodal",
        "kovaak_run_id": 555,
        "input_snapshot": {
            "schema_version": "analysis_input_snapshot.v1",
            "sources": {},
        },
    }

    async def _fake_telemetry_visual(job_):
        return {"safe_summary": {}}, None

    def _boom(job_, visual_result):
        raise ValueError("simulated cold import wedge")

    # _maybe_commit_analysis_evidence 经 asyncio.to_thread 调用，是同步函数。
    def _passthrough_commit(job_, result, **kwargs):
        return result

    # 只保留被测路径需要的真实逻辑，其余旁路 mock（与该分支直接相关）。
    monkeypatch.setattr(worker, "_heartbeat_loop", _no_op_async)
    monkeypatch.setattr(
        worker, "_assert_managed_video_matches_snapshot", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        worker,
        "_scenario_dispatch",
        lambda job_, mode: worker.CONTINUOUS_TRACKING_ANALYSIS_VERSION,
    )
    monkeypatch.setattr(
        worker, "_parse_frozen_stats_for_visual", lambda snapshot: object(),
    )
    monkeypatch.setattr(worker, "_external_telemetry_visual_or_none", _fake_telemetry_visual)
    monkeypatch.setattr(worker, "run_continuous_tracking_analysis", _boom)
    monkeypatch.setattr(
        worker,
        "_build_outcome_only_result_v2",
        lambda *a, **k: {"warnings": [], "limitations": []},
    )
    monkeypatch.setattr(worker, "_maybe_commit_analysis_evidence", _passthrough_commit)
    monkeypatch.setattr(worker, "_record_profile_contribution", _no_op_async)
    monkeypatch.setattr(worker.queue, "set_task_phase", _no_op_async)
    monkeypatch.setattr(worker.queue, "mark_done", lambda *a, **k: _async_result(True))
    monkeypatch.setattr(
        worker.analysis_output, "write_progressive_disclosure", lambda *a, **k: None,
    )

    with caplog.at_level(logging.WARNING, logger=_WORKER_LOGGER):
        asyncio.run(worker._execute_claimed_job(job, job["id"]))

    degradation_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "continuous tracking analysis unavailable" in record.getMessage()
    ]
    assert degradation_warnings, "降级 warning 缺失"
    message = degradation_warnings[0].getMessage()
    assert "continuous_tracking_analysis_unavailable" in message
    assert "ValueError" in message
    assert "simulated cold import wedge" in message
