from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import worker, queue
from webapp.backend.contracts import ANALYSIS_RESULT_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False


@pytest.mark.asyncio
async def test_process_one_happy_path():
    """claim → run_analysis → run_report(含 narration)→ mark_done (v1 envelope)。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_summary = {"sparc": {"med": -7.5}}
    fake_extras = {
        "fps": 60, "duration_frames": 100,
        "flicks": [{"start_frame": 10, "peak_frame": 15, "end_frame": 20,
                    "peak_speed_px": 800.0, "duration_s": 0.18}],
        "kill_frames": [18], "corrective_frames": [],
    }
    fake_report = {"diagnosis": {"x": 1}, "narration": "教练讲解", "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=(fake_summary, fake_extras)), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._load_backend", return_value=MagicMock()), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert result["narration"]["status"] == "available"
    assert result["narration"]["text"] == "教练讲解"
    assert float(s["llm_cost_cny"]) > 0
    types = sorted(e["type"] for e in result["deterministic"]["timeline"])
    assert types == ["kill", "peak"]


@pytest.mark.asyncio
async def test_process_one_happy_path_writes_analysis_result_v1():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv", cm_per_360=30.0, fov=90.0)
    fake_extras = {
        "fps": 60,
        "flicks": [{"peak_frame": 15}],
        "kill_frames": [18],
        "corrective_frames": [],
    }
    fake_report = {
        "diagnosis": {"x": 1},
        "figures": {},
        "narration": "讲解",
        "notes": [],
    }

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, fake_extras)), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._load_backend", return_value=MagicMock()), \
         patch("webapp.backend.worker._delete_video_safely"):
        await worker.process_one()

    s = await queue.get_session(sid)
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert result["summary_type"] == "flicking"
    assert result["input"] == {"cm_per_360": 30.0, "fov": 90.0}
    assert result["narration"] == {
        "status": "available",
        "text": "讲解",
        "provider": None,
        "model": None,
        "usage": None,
    }
    assert "timeline" not in result
    assert len(result["deterministic"]["timeline"]) == 2
    assert result["created_at"].endswith("Z")
    assert result["completed_at"].endswith("Z")


@pytest.mark.asyncio
async def test_process_one_over_budget_keeps_v1_narration_null():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.llm_budget.check_and_record",
               return_value=False), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report_no_llm), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert float(s["llm_cost_cny"]) == 0.0
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert result["narration"]["status"] == "not_requested"
    assert result["narration"]["text"] is None
    assert result["deterministic"]["timeline"] == []


@pytest.mark.asyncio
async def test_process_one_llm_over_budget_skips_narration():
    """LLM 超额 → run_report(backend=None),narration 跳过;timeline 仍注入 v1。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [], "corrective_frames": []})), \
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
    assert s["result"]["deterministic"]["timeline"] == []
    assert s["result"]["narration"]["status"] == "not_requested"
    args, kwargs = mock_report.call_args
    backend_arg = kwargs.get("backend", args[1] if len(args) > 1 else "MISSING")
    assert backend_arg is None


@pytest.mark.asyncio
async def test_process_one_normalizes_non_finite_values_before_persisting():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    nan = float("nan")
    fake_report = {
        "diagnosis": {"summary": {"sparc": {"med": nan}}},
        "figures": {},
        "narration": "ok",
        "notes": [],
    }

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.worker.run_report", return_value=fake_report), \
         patch("webapp.backend.worker._load_backend", return_value=MagicMock()), \
         patch("webapp.backend.worker._delete_video_safely"):
        await worker.process_one()

    from webapp.backend import db
    conn = await db.get_conn()
    cur = await conn.execute("SELECT result FROM sessions WHERE id=?", (sid,))
    row = await cur.fetchone()
    raw_json = row["result"]
    assert "NaN" not in raw_json

    s = await queue.get_session(sid)
    med = s["result"]["deterministic"]["diagnosis"]["summary"]["sparc"]["med"]
    assert med is None
    issues = s["result"]["normalization_issues"]
    assert any(i.get("code") == "non_finite_number" for i in issues)


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
    assert s["error"]["code"] == "analysis_failed"
    assert "CSRT" not in s["error"]["message"]


@pytest.mark.asyncio
async def test_process_one_load_backend_failure_degrades_gracefully():
    """_load_backend 失败(无 API key 等)→ 降级 backend=None,不 fail job。
    CV 结果保留, narration unavailable。
    """
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.worker._load_backend",
               side_effect=RuntimeError("no api key")), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report) as mock_report, \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["narration"]["status"] == "unavailable"
    assert s["result"]["narration"]["text"] is None
    args, kwargs = mock_report.call_args
    backend_arg = kwargs.get("backend", args[1] if len(args) > 1 else "MISSING")
    assert backend_arg is None


def test_build_timeline_combines_peaks_correctives_kills():
    """flick peak / corrective / kill 都进 timeline,按 frame 升序。"""
    extras = {
        "fps": 60,
        "duration_frames": 1000,
        "flicks": [
            {"start_frame": 10, "peak_frame": 15, "end_frame": 25,
             "peak_speed_px": 800.0, "duration_s": 0.25},
            {"start_frame": 60, "peak_frame": 65, "end_frame": 75,
             "peak_speed_px": 700.0, "duration_s": 0.25},
        ],
        "corrective_frames": [70],
        "kill_frames": [20, 80],
    }
    events = worker._build_timeline(extras)
    types_by_frame = {e["frame"]: e["type"] for e in events}
    assert types_by_frame[15] == "peak"
    assert types_by_frame[65] == "peak"
    assert types_by_frame[70] == "corrective"
    assert types_by_frame[20] == "kill"
    assert types_by_frame[80] == "kill"
    frames = [e["frame"] for e in events]
    assert frames == sorted(frames)
    assert events[0]["time_s"] == round(15 / 60, 3)


def test_build_timeline_handles_empty_and_garbage():
    """extras 空 / 非 dict → 返回 [];fps 缺失走默认 60。"""
    assert worker._build_timeline({}) == []
    assert worker._build_timeline(None) == []
    events = worker._build_timeline({"flicks": [{"peak_frame": 60}]})
    assert events == [{"frame": 60, "time_s": 1.0, "type": "peak", "label": "速度峰值"}]


@pytest.mark.asyncio
async def test_process_one_runs_recover_before_claim():
    order: list[str] = []

    async def fake_recover(*_a, **_k):
        order.append("recover")
        return {"requeued": 0, "failed": 0}

    async def fake_claim(*_a, **_k):
        order.append("claim")
        return None

    with patch("webapp.backend.queue.recover_stale_jobs", side_effect=fake_recover), \
         patch("webapp.backend.queue.claim_next", side_effect=fake_claim):
        assert await worker.process_one() is False
    assert order == ["recover", "claim"]


@pytest.mark.asyncio
async def test_process_one_heartbeats_during_analysis():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    beats: list[int] = []

    def slow_analysis(*_a, **_k):
        # Block the worker thread long enough for heartbeat loop ticks.
        time.sleep(0.2)
        return (
            {"a": {"med": 1}},
            {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
        )

    real_heartbeat = queue.heartbeat

    async def counting_hb(session_id, worker_id):
        beats.append(session_id)
        return await real_heartbeat(session_id, worker_id)

    with patch("webapp.backend.worker.HEARTBEAT_INTERVAL_SECONDS", 0.05), \
         patch("webapp.backend.worker.run_analysis", side_effect=slow_analysis), \
         patch("webapp.backend.worker.run_report",
               return_value={"diagnosis": {}, "narration": None, "notes": []}), \
         patch("webapp.backend.worker._load_backend", return_value=None), \
         patch("webapp.backend.queue.heartbeat", side_effect=counting_hb):
        await worker.process_one()

    assert sid in beats
    assert len(beats) >= 2
    s = await queue.get_session(sid)
    assert s["status"] == "done"


@pytest.mark.asyncio
async def test_process_one_failure_keeps_input_files(tmp_path: Path):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"video")
    csv.write_text("csv")
    sid = await queue.enqueue("u1", str(video), str(csv))
    with patch("webapp.backend.worker.run_analysis",
               side_effect=RuntimeError("boom")):
        await worker.process_one()
    assert video.is_file()
    assert csv.is_file()
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_run_loop_recovers_stale_when_idle():
    recover = AsyncMock(return_value={"requeued": 0, "failed": 0})

    async def process_once_false():
        return False

    sleep_calls = 0

    async def sleep_then_stop(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError()

    with patch("webapp.backend.worker.process_one", side_effect=process_once_false), \
         patch("webapp.backend.queue.recover_stale_jobs", recover), \
         patch("asyncio.sleep", side_effect=sleep_then_stop):
        with pytest.raises(asyncio.CancelledError):
            await worker._run_loop_async()

    recover.assert_awaited()
    assert sleep_calls == 1