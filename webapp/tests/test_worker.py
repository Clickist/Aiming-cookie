from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import worker, queue
from webapp.backend.contracts import ANALYSIS_RESULT_V2_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False


@pytest.mark.asyncio
async def test_process_one_happy_path():
    """claim → run_analysis → run_report(含 narration) → mark_done (v2 envelope)。"""
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
         patch("webapp.backend.worker.run_report", return_value=fake_report) as mock_report, \
         patch("webapp.backend.worker._load_backend", return_value=MagicMock()), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["narration"]["status"] == "available"
    assert result["narration"]["text"] == "教练讲解"
    report_summary = mock_report.call_args.args[0]
    assert report_summary["sparc"]["metric_version"] == (
        "flicking_fair_summary.sparc.v2"
    )
    assert float(s["llm_cost_cny"]) == 0.0
    types = sorted(e["type"] for e in result["deterministic"]["timeline"])
    assert types == ["kill", "peak"]


@pytest.mark.asyncio
async def test_video_fallback_narration_uses_selected_profile_not_legacy_provider():
    sid = await queue.enqueue("owner-selected", "/tmp/v.mp4", "/tmp/s.csv")
    profile = {
        "profile_id": 7,
        "provider_id": "custom-selected",
        "provider_name": "Selected Provider",
        "kind": "custom_openai_compatible",
        "base_url": "https://provider.test/v1",
        "model_id": "selected-model",
        "credential": {"type": "api_key", "key": "local-only-key"},
    }
    backend = MagicMock(name="selected-backend")
    fake_report = {"diagnosis": {}, "narration": "selected narration", "notes": []}

    with patch(
        "webapp.backend.provider_store.get_default_runtime_profile",
        new=AsyncMock(return_value=profile),
    ) as get_profile, patch(
        "webapp.backend.provider_store.runtime_profile_configured",
        return_value=True,
    ), patch(
        "webapp.backend.coach_engine.load_backend_for_profile",
        return_value=backend,
    ) as load_selected, patch(
        "webapp.backend.worker._load_backend",
        wraps=worker._load_backend,
    ) as load_selected_adapter, patch(
        "webapp.backend.worker.run_analysis",
        return_value=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    ), patch(
        "webapp.backend.worker.run_report",
        return_value=fake_report,
    ) as run_report:
        assert await worker.process_one() is True

    get_profile.assert_awaited_once_with("owner-selected")
    load_selected.assert_called_once_with(profile)
    load_selected_adapter.assert_called_once_with(profile)
    assert run_report.call_args.args[1] is backend
    session = await queue.get_session(sid)
    assert session["result"]["narration"]["status"] == "available"
    assert session["result"]["narration"]["text"] == "selected narration"
    assert session["llm_cost_cny"] == 0.0


@pytest.mark.asyncio
async def test_process_one_happy_path_writes_analysis_result_v2():
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
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_type"] == "flicking"
    assert result["input_mode"] == "video_fallback"
    assert result["input_snapshot"]["calibration"] == {
        "cm_per_360": 30.0,
        "fov": 90.0,
    }
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
async def test_process_one_without_selected_provider_keeps_v2_narration_null():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [],
                                                  "corrective_frames": []})), \
         patch("webapp.backend.worker.run_report",
               return_value=fake_report_no_llm), \
         patch("webapp.backend.worker._delete_video_safely"):
        handled = await worker.process_one()

    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert float(s["llm_cost_cny"]) == 0.0
    result = s["result"]
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["narration"]["status"] == "not_requested"
    assert result["narration"]["text"] is None
    assert result["deterministic"]["timeline"] == []


@pytest.mark.asyncio
async def test_process_one_without_selected_provider_passes_none_to_report():
    """未选择 Provider → deterministic report 保留，narration 不请求。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_report_no_llm = {"diagnosis": {"x": 1}, "narration": None, "notes": []}

    with patch("webapp.backend.worker.run_analysis",
               return_value=({"a": {"med": 1}}, {"fps": 60, "flicks": [],
                                                  "kill_frames": [], "corrective_frames": []})), \
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
async def test_native_source_disappearance_uses_stable_non_retryable_error(caplog):
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="owner-source", source_key="source-error", scenario="Scenario",
    )
    sid = await queue.enqueue(
        "owner-source", "", "",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run["id"],
            "sources": {},
            "trace": None,
        },
    )

    with patch(
        "webapp.backend.worker.run_native_analysis",
        side_effect=worker.SourceSnapshotChangedError(
            "source_unavailable: stats /private/user/source.csv missing or unreadable"
        ),
    ):
        assert await worker.process_one() is True

    session = await queue.get_session(sid)
    assert session["status"] == "failed"
    assert session["error"] == {
        "schema_version": "error.v1",
        "category": "input_validation",
        "code": "source_unavailable",
        "message": "分析输入源已不可用或已变更，请重新提交分析。",
        "retryable": False,
        "trace_id": None,
        "details": None,
    }
    assert "/private/user/source.csv" not in json.dumps(
        session["error"], ensure_ascii=False
    )
    assert "/private/user/source.csv" not in caplog.text


@pytest.mark.asyncio
async def test_run_based_video_fallback_writes_v2_without_raw_provenance():
    from webapp.backend import kovaak_run_store

    video_fingerprint = {
        "sha256": "a" * 64,
        "size": 123,
        "mtime_ns": 456,
    }
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="fallback-run", scenario="Scenario",
    )
    snapshot = {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run["id"],
        "scenario": "Scenario",
        "sources": {
            "stats": {
                "artifact_ref": f"run:{run['id']}:stats:abc",
                "basename": "stats.csv",
                "availability": "available",
                "path": "/private/stats.csv",
            },
            "video": {
                "basename": "clip.mp4",
                "fingerprint": video_fingerprint,
                "availability": "available",
                "format_version": "mp4",
                "path": "/private/video.mp4",
            },
        },
        "trace": {
            "artifact_ref": f"run:{run['id']}:trace",
            "availability": "available",
            "path": "/private/trace.bin",
        },
    }
    sid = await queue.enqueue(
        "u1", "/tmp/v.mp4", "/tmp/s.csv",
        input_mode="video_fallback",
        kovaak_run_id=run["id"],
        input_snapshot=snapshot,
    )
    fake_report = {
        "diagnosis": {"summary": {"ok": True}},
        "figures": {},
        "narration": None,
        "notes": [],
    }

    with patch(
        "webapp.backend.worker.run_analysis",
        return_value=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    ), patch("webapp.backend.worker.run_report", return_value=fake_report), patch(
        "webapp.backend.worker._load_backend", side_effect=RuntimeError("offline")
    ):
        await worker.process_one()

    stored = await queue.get_session(sid)
    assert stored["result"]["schema_version"] == "analysis_result.v2"
    assert stored["result"]["input_mode"] == "video_fallback"
    assert "raw_input" not in stored["result"]["evidence"]["sources"]
    assert stored["result"]["input_snapshot"]["trace"] is None
    assert stored["result"]["input_snapshot"]["sources"]["video"]["fingerprint"] == video_fingerprint
    video_artifact = next(
        artifact
        for artifact in stored["result"]["artifact_manifest"]["external_inputs"]
        if artifact["kind"] == "mp4"
    )
    assert video_artifact["checksum"] == video_fingerprint["sha256"]
    assert "/private" not in json.dumps(stored["result"])


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

def _native_snapshot() -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": 42,
        "scenario": "Tile Frenzy",
        "sources": {
            "stats": {
                "artifact_ref": "run:42:stats",
                "basename": "stats.csv",
                "fingerprint": {"sha256": "stats-sha", "size": 1, "mtime_ns": 1},
                "parser_version": "kovaak_stats.v1",
                "path": "/db-private/runs/42/stats.csv",
                "availability": "available",
            },
            "performance": {
                "artifact_ref": "run:42:performance",
                "basename": "performance.perf",
                "fingerprint": {"sha256": "performance-sha", "size": 2, "mtime_ns": 2},
                "parser_version": "kovaak_performance.v1",
                "path": "/db-private/runs/42/performance.perf",
                "availability": "available",
            },
        },
        "trace": {
            "artifact_ref": "run:42:trace",
            "path": "/db-private/runs/42/trace.bin",
            "availability": "available",
            "format_version": 1,
        },
    }


def _native_adapter_result() -> dict:
    return {
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "status": "available",
        "evidence": {
            "sources": {
                "raw_input": {
                    "source": "raw_input",
                    "role": "kinematics",
                    "availability": "available",
                    "alignment": "aligned",
                    "warnings": [],
                },
                "performance": {
                    "source": "performance",
                    "role": "event_anchor",
                    "availability": "available",
                    "alignment": "aligned",
                    "warnings": [],
                },
                "stats": {
                    "source": "stats",
                    "role": "scenario_config",
                    "availability": "available",
                    "alignment": "not_required",
                    "warnings": [],
                },
            },
            "alignment": {"status": "aligned", "coverage_ratio": 1.0},
            "coverage": 1.0,
            "warnings": [],
        },
        "deterministic": {
            "trajectory": {"unit": "raw_counts", "point_count": 2, "points": [{"x": 1}]},
            "metrics": {
                "path_length": {
                    "key": "path_length",
                    "value": 10.0,
                    "unit": "raw_counts",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "limitations": [],
                },
                "calibrated_path_length": {
                    "key": "calibrated_path_length",
                    "value": 2.0,
                    "unit": "cm",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input", "calibration"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "limitations": [],
                },
                "path_efficiency": {
                    "key": "path_efficiency",
                    "value": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "limitations": [],
                },
                "straightness": {
                    "key": "straightness",
                    "value": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 2,
                    "coverage": 1.0,
                    "limitations": [],
                },
                "decel_frac": {
                    "key": "decel_frac",
                    "value": 0.8,
                    "median": 0.8,
                    "med": 0.8,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking_segment.v1",
                    "sample_count": 1,
                    "coverage": 1.0,
                    "limitations": ["descriptive_distribution_not_health_threshold"],
                    "sample_refs": ["flick:1"],
                },
                "sparc": {
                    "key": "sparc",
                    "value": -7.0,
                    "median": -7.0,
                    "med": -7.0,
                    "unit": "dimensionless",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking.sparc.v2",
                    "sample_count": 1,
                    "coverage": 1.0,
                    "limitations": [
                        "descriptive_distribution_not_health_threshold",
                        "sparc_cross_polling_comparability_unverified",
                    ],
                    "sample_refs": ["flick:1"],
                },
            },
            "timeline": [
                {
                    "id": "flick:1",
                    "event_type": "flick",
                    "source": "raw_input",
                    "start_ms": 10.0,
                    "peak_ms": 20.0,
                    "end_ms": 40.0,
                    "settle_end_ms": 50.0,
                    "quality": "available",
                    "coverage": 1.0,
                    "limitations": [],
                    "metrics": {
                        "peak_speed": {
                            "value": 2000.0,
                            "unit": "raw_counts_per_second",
                        }
                    },
                }
            ],
        },
        "limitations": [],
    }


def test_real_native_metrics_feed_deterministic_explanation_contract():
    from kovaak_tracker.native_flicking_analysis import analyze_native_flicking

    native_result = analyze_native_flicking(
        [
            {"timestamp_ms": 1_000, "dx": 0, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_010, "dx": 6, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_020, "dx": 5, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_030, "dx": 4, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_040, "dx": 3, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_050, "dx": 2, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_060, "dx": 1, "dy": 0, "buttons": 0},
            {"timestamp_ms": 1_070, "dx": 0, "dy": 0, "buttons": 1},
            {"timestamp_ms": 1_080, "dx": 0, "dy": 0, "buttons": 0},
        ],
        {"challenge_start_utc": 1_000, "time_limit_ms": 80, "events": []},
    )

    deterministic = worker._native_deterministic_v2(native_result)
    issue = deterministic["diagnosis"]["issues"][0]

    assert issue["signal"] == "decel_frac high"
    assert issue["severity"] == "info"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["decel_frac"]
    assert issue["event_refs"] == ["flick:1"]
    assert issue["limitations"] == ["threshold_requires_product_calibration"]
    assert issue["prescriptions"][0]["target_metrics"] == ["decel_frac"]
    assert deterministic["diagnosis"]["summary"]["decel_frac"]["med"] == pytest.approx(5 / 6)


async def _capture_mode_result(
    job: dict,
    *,
    native_result: dict,
    parsed_stats=None,
    cv_result=None,
    cv_error=None,
):
    completed: list[dict] = []
    calls: list[str] = []

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    def native(*_args, **kwargs):
        calls.append("native")
        if kwargs.get("return_parsed_stats"):
            return native_result, parsed_stats
        return native_result

    def cv(*_args, **_kwargs):
        calls.append("cv")
        if cv_error is not None:
            raise cv_error
        return cv_result

    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done)), \
         patch("webapp.backend.worker.run_native_analysis", side_effect=native) as native_mock, \
         patch("webapp.backend.worker.run_analysis", side_effect=cv) as cv_mock:
        assert await worker.process_one() is True

    assert len(completed) == 1
    return completed[0], calls, native_mock, cv_mock


@pytest.mark.asyncio
async def test_process_one_input_native_uses_snapshot_sources_without_cv_or_private_paths():
    snapshot = _native_snapshot()
    job = {
        "id": 101,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "",
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-07-13 12:00:00",
    }

    native_result = _native_adapter_result()
    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=native_result,
    )

    assert calls == ["native"]
    native_mock.assert_called_once_with(snapshot, 30.0, 90.0)
    cv_mock.assert_not_called()
    assert result["schema_version"] == "analysis_result.v2"
    assert result["analysis_version"] == "native_flicking.v1"
    assert result["owner_id"] == "u1"
    assert "local_profile" not in result
    assert result["input_mode"] == "input_native"
    assert result["kovaak_run_ref"] == "run:42"
    assert set(result["evidence"]) == {
        "sources", "provenance", "availability", "alignment", "coverage", "warnings",
    }
    assert result["evidence"]["coverage"] == 1.0
    assert result["evidence"]["sources"]["raw_input"]["artifact_ref"] == "run:42:trace"
    assert result["evidence"]["sources"]["raw_input"]["parser_or_format_version"] == 1
    assert result["evidence"]["sources"]["stats"]["parser_or_format_version"] == "kovaak_stats.v1"
    assert result["evidence"]["sources"]["performance"]["parser_or_format_version"] == "kovaak_performance.v1"
    assert result["input_snapshot"]["trace"] == {
        "artifact_ref": "run:42:trace",
        "availability": "available",
        "format_version": 1,
    }
    public_deterministic = result["deterministic"]
    source_deterministic = native_result["deterministic"]
    assert public_deterministic["metrics"] == source_deterministic["metrics"]
    assert public_deterministic["metrics"]["sparc"]["metric_version"] == (
        "native_flicking.sparc.v2"
    )
    assert all(
        issue["signal"] != "sparc low"
        for issue in public_deterministic["diagnosis"]["issues"]
    )
    assert public_deterministic["timeline"] == source_deterministic["timeline"]
    assert public_deterministic["trajectory"] == {
        "unit": "raw_counts",
        "point_count": 2,
    }
    assert "points" not in public_deterministic["trajectory"]
    assert "/db-private/" not in str(result)
    required_metric_fields = {
        "key", "value", "unit", "availability", "provenance",
        "metric_version", "coverage", "limitations",
    }
    for key, metric in public_deterministic["metrics"].items():
        assert required_metric_fields <= set(metric), key
        assert metric["key"] == key

    manifest = result["artifact_manifest"]
    assert manifest["analysis_id"] == "analysis:101"
    entries = {
        entry["id"]: entry
        for entry in [*manifest["external_inputs"], *manifest["owned_outputs"]]
    }
    assert entries["run:42:trace"] == {
        "id": "run:42:trace",
        "kind": "raw_input",
        "source": "raw_input",
        "availability": "available",
        "ownership": "kovaak_run",
        "managed": True,
        "local_only": True,
        "status": "available",
        "format_version": 1,
        "derived_from": [],
    }
    assert entries["analysis:101"]["ownership"] == "analysis"
    assert entries["analysis:101"]["managed"] is True
    assert entries["analysis:101"]["local_only"] is True
    assert entries["analysis:101"]["format_version"] == "analysis_result.v2"
    assert set(entries["analysis:101"]["derived_from"]) == {
        "run:42:stats", "run:42:performance", "run:42:trace",
    }
    issue = public_deterministic["diagnosis"]["issues"][0]
    assert issue["signal"] == "decel_frac high"
    assert issue["severity"] == "info"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["decel_frac"]
    assert issue["event_refs"] == ["flick:1"]
    assert issue["prescriptions"][0]["target_metrics"] == ["decel_frac"]


@pytest.mark.asyncio
async def test_process_one_multimodal_keeps_native_result_when_video_cv_fails():
    snapshot = _native_snapshot()
    video_fingerprint = {
        "sha256": "video-sha",
        "size": 3,
        "mtime_ns": 3,
    }
    snapshot["sources"]["video"] = {
        "basename": "video.mp4",
        "fingerprint": video_fingerprint,
        "path": "/db-private/source/video.mp4",
        "availability": "available",
        "format_version": "mp4",
    }
    job = {
        "id": 102,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "/managed/session/video.mp4",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    native_result = _native_adapter_result()
    parsed_stats = object()
    result, calls, _native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=native_result,
        parsed_stats=parsed_stats,
        cv_error=RuntimeError("video decoder unavailable"),
    )

    assert calls == ["native", "cv"]
    cv_mock.assert_called_once_with(
        "/managed/session/video.mp4",
        "/db-private/runs/42/stats.csv",
        None,
        None,
        stats=parsed_stats,
    )
    assert result["schema_version"] == "analysis_result.v2"
    assert result["analysis_version"] == "native_flicking.v1"
    assert result["owner_id"] == "u1"
    assert result["input_mode"] == "multimodal"
    assert result["deterministic"]["status"] == "available"
    assert result["deterministic"]["metrics"] == native_result["deterministic"]["metrics"]
    assert result["deterministic"]["timeline"] == native_result["deterministic"]["timeline"]
    assert result["evidence"]["availability"]["mp4"] == "unavailable"
    assert result["evidence"]["sources"]["mp4"]["parser_or_format_version"] == "mp4"
    video_artifact = next(
        entry for entry in result["artifact_manifest"]["external_inputs"]
        if entry["kind"] == "mp4"
    )
    assert video_artifact["ownership"] == "analysis"
    assert video_artifact["managed"] is True
    assert video_artifact["local_only"] is True
    assert video_artifact["format_version"] == "mp4"
    assert video_artifact["checksum"] == video_fingerprint["sha256"]
    assert result["input_snapshot"]["sources"]["video"] == {
        "artifact_ref": "analysis:102:video",
        "basename": "video.mp4",
        "fingerprint": video_fingerprint,
        "availability": "available",
        "format_version": "mp4",
    }
    assert result["warnings"] == [{"code": "video_cv_unavailable"}]
    assert "target_relative_error" not in result["deterministic"]["metrics"]
    assert "overshoot_distance" not in result["deterministic"]["metrics"]


@pytest.mark.asyncio
async def test_process_one_multimodal_reuses_stats_frozen_by_native_analysis():
    parsed_stats = object()
    snapshot = _native_snapshot()
    job = {
        "id": 104,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "/managed/session/video.mp4",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    cv_result = (
        {},
        {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
    )

    _result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_adapter_result(),
        parsed_stats=parsed_stats,
        cv_result=cv_result,
    )

    assert calls == ["native", "cv"]
    assert native_mock.call_args.kwargs["return_parsed_stats"] is True
    assert cv_mock.call_args.kwargs["stats"] is parsed_stats


@pytest.mark.asyncio
async def test_process_one_video_fallback_writes_v2_without_raw_provenance():
    completed: list[dict] = []
    job = {
        "id": 103,
        "user_id": "u1",
        "input_mode": "video_fallback",
        "kovaak_run_id": None,
        "input_snapshot": None,
        "video_path": "/managed/session/video.mp4",
        "csv_path": "/managed/session/stats.csv",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-07-13 12:00:00",
    }

    async def mark_done(_sid, result, _cost, *, worker_id):
        completed.append(result)
        return True

    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=AsyncMock(side_effect=mark_done)), \
         patch("webapp.backend.worker.run_native_analysis") as native_mock, \
         patch(
             "webapp.backend.worker.run_analysis",
             return_value=(
                 {"a": {"med": 1}, "sparc": {"med": -1.2}},
                 {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
             ),
         ), \
         patch(
             "webapp.backend.worker.run_report",
             return_value={"diagnosis": {}, "narration": None, "notes": []},
         ), \
         patch("webapp.backend.worker._load_backend", return_value=None):
        assert await worker.process_one() is True

    assert len(completed) == 1
    result = completed[0]
    native_mock.assert_not_called()
    assert result["schema_version"] == ANALYSIS_RESULT_V2_SCHEMA_VERSION
    assert result["analysis_version"] == "flicking_fair_summary.v1"
    assert result["owner_id"] == "u1"
    assert result["input_mode"] == "video_fallback"
    assert "kovaak_run_ref" not in result
    assert "raw_input" not in result["evidence"]["sources"]
    assert result["evidence"]["availability"] == {"stats": "available", "mp4": "available"}
    assert result["evidence"]["coverage"] is None
    metric = result["deterministic"]["metrics"]["a"]
    assert metric["key"] == "a"
    assert metric["value"] == 1
    assert metric["unit"] == "unknown"
    assert metric["provenance"] == {"kind": "fused", "sources": ["mp4", "stats"]}
    assert metric["metric_version"] == "flicking_fair_summary.v1"
    assert result["deterministic"]["metrics"]["sparc"]["metric_version"] == (
        "flicking_fair_summary.sparc.v2"
    )
    assert metric["coverage"] is None
    assert "coverage_not_recorded" in metric["limitations"]
    assert "unit_not_registered" in metric["limitations"]
    assert all(
        {
            "source", "availability", "ownership", "managed", "local_only",
            "status", "derived_from",
        } <= set(entry)
        for entry in [
            *result["artifact_manifest"]["external_inputs"],
            *result["artifact_manifest"]["owned_outputs"],
        ]
    )
    assert "/managed" not in json.dumps(result)


def test_native_result_marks_desktop_owner_as_local_profile():
    from webapp.backend import config

    result = worker._build_native_result_v2(
        {
            "id": 104,
            "user_id": config.DESKTOP_LOCAL_PROFILE,
            "input_mode": "input_native",
            "kovaak_run_id": 42,
            "input_snapshot": _native_snapshot(),
        },
        _native_adapter_result(),
        created_at="2026-07-13T12:00:00Z",
        completed_at="2026-07-13T12:00:01Z",
    )

    assert result["owner_id"] == config.DESKTOP_LOCAL_PROFILE
    assert result["local_profile"] == config.DESKTOP_LOCAL_PROFILE
