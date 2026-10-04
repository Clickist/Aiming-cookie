"""分析任务级 deadline 与阶段僵尸清扫（[fix 2026-10-04] A 项）。

覆盖三件事：
1. process_one 的 wait_for 总预算：挂死的分析协程被取消、作业标为
   analysis_deadline_exceeded（retryable），消费循环不受影响继续运转；
2. queue.expire_stalled_analyses：阶段起点超预算的 running 作业被旁路清扫为
   analysis_stalled（retryable），租约清空后原 worker 的晚到终态写不进去；
3. recover_stale_jobs requeue 时阶段/总时钟归零，清扫不会拿旧 attempt 的
   起点误杀 requeue 后刚重启的健康 attempt。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from webapp.backend import queue, worker

TEST_WORKER = "test-worker:deadline"


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )


@pytest.mark.asyncio
async def test_process_one_enforces_total_budget_and_loop_survives(monkeypatch):
    """分析协程挂死超总预算：标 analysis_deadline_exceeded，消费循环继续。"""
    release = threading.Event()

    def _hanging_analysis(*args, **kwargs):
        # 模拟证据落盘/分析挂死。事件在 finally 释放，避免线程池在事件循环
        # 关闭时等待整个 sleep 时长（施工单的 sleep(9999) 会挂死测试进程）。
        release.wait(timeout=10)
        return ({"sparc": {}}, {})

    monkeypatch.setattr(worker, "ANALYSIS_TOTAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(worker, "run_analysis", _hanging_analysis)

    sid = await queue.enqueue("u-deadline", "/tmp/v.mp4", "/tmp/s.csv")
    try:
        handled = await worker.process_one()
        # True=处理了（无论成败）：TimeoutError 在 process_one 内收敛，不泄漏。
        assert handled is True
        session = await queue.get_session(sid)
        assert session["status"] == "failed"
        assert session["error"]["code"] == "analysis_deadline_exceeded"
        assert session["error"]["retryable"] is True
        # 租约清空：可被用户重试，原 worker 的晚到终态写不进去。
        assert session["worker_id"] is None
        assert session["lease_expires_at"] is None
        assert session["failure_domain"] == "kinematics"
        # 队列空，消费循环正常空转退出本轮。
        assert await worker.process_one() is False
    finally:
        release.set()


@pytest.mark.asyncio
async def test_expire_stalled_analyses_fails_phase_expired_and_blocks_late_write():
    """阶段起点超预算的 running 作业被清扫为 analysis_stalled，晚到终态被挡。"""
    sid = await queue.enqueue("u-stalled", "", "")
    job = await queue.claim_next(TEST_WORKER)
    assert job is not None and job["id"] == sid
    session = queue._load_session(sid)
    assert isinstance(session, dict)
    # analyzing_video 预算 900s：把阶段起点拨回 901s 前，模拟本阶段挂死。
    session["task_phase"] = "analyzing_video"
    session["phase_started_at"] = _iso(901)
    queue._save_session(session)

    assert await queue.expire_stalled_analyses() == {"stalled": 1}
    swept = await queue.get_session(sid)
    assert swept["status"] == "failed"
    assert swept["error"]["code"] == "analysis_stalled"
    assert swept["error"]["retryable"] is True
    assert swept["worker_id"] is None
    assert swept["lease_expires_at"] is None
    # 租约已被清扫清空：原 worker 晚到的 mark_failed 必须被租约检查挡住。
    assert await queue.mark_failed(sid, "late zombie", worker_id=TEST_WORKER) is False


@pytest.mark.asyncio
async def test_expire_stalled_analyses_spares_fresh_and_sweeps_total_budget():
    """预算内不动；阶段快速轮换的活僵尸由 started_at 总预算兜底清扫。"""
    fresh = await queue.enqueue("u-fresh", "", "")
    fresh_job = await queue.claim_next(TEST_WORKER)
    assert fresh_job is not None and fresh_job["id"] == fresh

    zombie = await queue.enqueue("u-zombie", "", "")
    zombie_job = await queue.claim_next(TEST_WORKER)
    assert zombie_job is not None and zombie_job["id"] == zombie
    # 阶段时钟不断被 set_task_phase 重置（看似在前进），但任务总时长已爆表。
    session = queue._load_session(zombie)
    assert isinstance(session, dict)
    session["phase_started_at"] = _iso(10)
    session["started_at"] = _iso(1900)
    queue._save_session(session)

    assert await queue.expire_stalled_analyses() == {"stalled": 1}
    # 预算内的健康作业不受影响；活僵尸被总预算兜底清扫。
    assert (await queue.get_session(fresh))["status"] == "running"
    assert (await queue.get_session(zombie))["status"] == "failed"
    assert (await queue.get_session(zombie))["error"]["code"] == "analysis_stalled"


@pytest.mark.asyncio
async def test_recover_stale_requeue_resets_attempt_clocks():
    """requeue 归零 phase_started_at/started_at：新 attempt 不继承旧起点。"""
    sid = await queue.enqueue("u-requeue", "", "")
    job = await queue.claim_next(TEST_WORKER)
    assert job is not None and job["id"] == sid
    session = queue._load_session(sid)
    assert isinstance(session, dict)
    # 租约过期 + attempts 未耗尽 → requeue 路径。
    session["lease_expires_at"] = _iso(1)
    session["phase_started_at"] = _iso(1200)
    session["started_at"] = _iso(1900)
    queue._save_session(session)

    result = await queue.recover_stale_jobs()
    assert result == {"requeued": 1, "failed": 0}
    requeued = await queue.get_session(sid)
    assert requeued["status"] == "queued"
    assert requeued["phase_started_at"] is None
    assert requeued["started_at"] is None
    # 下次 claim 重建双时钟，清扫不会拿旧 attempt 的起点误判僵尸。
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None and claimed["id"] == sid
    reclaimed = await queue.get_session(sid)
    assert reclaimed["started_at"] is not None
    assert reclaimed["phase_started_at"] is not None
    assert await queue.expire_stalled_analyses() == {"stalled": 0}
