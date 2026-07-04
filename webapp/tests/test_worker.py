from __future__ import annotations

import pytest
from unittest.mock import patch

from webapp.backend import worker, queue


@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False


@pytest.mark.asyncio
async def test_process_one_runs_analysis_and_marks_done():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_summary = {"signals": {"sparc low": {"severity": "fix"}}}

    with patch("webapp.backend.worker.run_analysis", return_value=fake_summary), \
         patch("webapp.backend.worker.build_report",
               return_value={"diagnosis": {"x": 1}}), \
         patch("webapp.backend.worker.call_llm",
               return_value=("教练讲解文本", 0.003)), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["narration"] == "教练讲解文本"
    assert float(s["llm_cost_cny"]) == 0.003


@pytest.mark.asyncio
async def test_process_one_llm_failure_degrades_gracefully():
    """LLM 失败 → 降级:有诊断无 narration,job 仍 done。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    with patch("webapp.backend.worker.run_analysis", return_value={"signals": {}}), \
         patch("webapp.backend.worker.build_report",
               return_value={"diagnosis": {"a": 1}}), \
         patch("webapp.backend.worker.call_llm",
               side_effect=RuntimeError("LLM 超时")), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert "narration" not in (s["result"] or {})  # 降级:无 narration
    assert s["result"]["diagnosis"] == {"a": 1}  # 但有结构化诊断


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
