from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from webapp.backend import worker, queue


@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False


@pytest.mark.asyncio
async def test_process_one_happy_path():
    """claim → run_analysis → run_report(含 narration)→ mark_done。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_summary = {"sparc": {"med": -7.5}}
    fake_report = {"diagnosis": {"x": 1}, "narration": "教练讲解", "notes": []}

    with patch("webapp.backend.worker.run_analysis", return_value=fake_summary), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._load_backend", return_value=MagicMock()), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["narration"] == "教练讲解"
    assert float(s["llm_cost_cny"]) > 0  # narration 非空 → 有成本


@pytest.mark.asyncio
async def test_process_one_llm_over_budget_skips_narration():
    """LLM 超额 → run_report(backend=None),narration 跳过。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value={"a": {"med": 1}}), \
         patch("webapp.backend.llm_budget.check_and_record",
               return_value=False), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report_no_llm) as mock_report, \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert float(s["llm_cost_cny"]) == 0.0
    # run_report 被以 backend=None 调(超额)
    args, kwargs = mock_report.call_args
    backend_arg = kwargs.get("backend", args[1] if len(args) > 1 else "MISSING")
    assert backend_arg is None


@pytest.mark.asyncio
async def test_process_one_analysis_failure_marks_failed():
    """分析崩(目标检测失败等)→ job failed,记录 error。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    with patch("webapp.backend.worker.run_analysis",
               side_effect=RuntimeError("CSRT 丢失目标")), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert "CSRT 丢失目标" in s["error"]
