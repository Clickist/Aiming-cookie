from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import db, queue, worker
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    build_analysis_result_v1,
)


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
async def test_run_based_video_fallback_writes_v2_without_raw_provenance(
    tmp_path: Path,
):
    from webapp.backend import kovaak_run_store

    source_video = tmp_path / "clip.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    video_source = _video_source(source_video)
    video_fingerprint = video_source["fingerprint"]
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
            "video": video_source,
        },
        "trace": {
            "artifact_ref": f"run:{run['id']}:trace",
            "availability": "available",
            "path": "/private/trace.bin",
        },
    }
    sid = await queue.enqueue(
        "u1", str(managed_video), "/tmp/s.csv",
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
    assert str(source_video) not in json.dumps(stored["result"])


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


def _video_source(path: Path) -> dict:
    stat = path.stat()
    return {
        "basename": path.name,
        "fingerprint": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "path": str(path),
        "availability": "available",
        "format_version": "mp4",
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
                    "classification": "deterministic",
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
                    "classification": "deterministic",
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
                    "classification": "deterministic",
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
                    "classification": "deterministic",
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
                    "classification": "deterministic",
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
                    "classification": "deterministic",
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


async def _capture_mode_failure(
    job: dict,
    *,
    native_result: dict,
    cv_side_effect=None,
):
    failed: list[dict] = []

    async def mark_failed(_sid, error, *, worker_id):
        failed.append(error)
        return True

    def native(*_args, **kwargs):
        if kwargs.get("return_parsed_stats"):
            return native_result, object()
        return native_result

    mark_done = AsyncMock(return_value=True)
    with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
         patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
         patch("webapp.backend.queue.heartbeat", new=AsyncMock(return_value=True)), \
         patch("webapp.backend.queue.mark_done", new=mark_done), \
         patch("webapp.backend.queue.mark_failed", new=AsyncMock(side_effect=mark_failed)), \
         patch("webapp.backend.worker.run_native_analysis", side_effect=native) as native_mock, \
         patch("webapp.backend.worker.run_analysis", side_effect=cv_side_effect) as cv_mock:
        assert await worker.process_one() is True

    assert len(failed) == 1
    mark_done.assert_not_awaited()
    return failed[0], native_mock, cv_mock


@pytest.mark.asyncio
async def test_process_one_input_native_uses_snapshot_sources_without_cv_or_private_paths():
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = {
        "basename": "ignored.mp4",
        "fingerprint": {"sha256": "ignored", "size": 1, "mtime_ns": 1},
        "path": "/db-private/source/ignored.mp4",
        "availability": "available",
        "format_version": "mp4",
    }
    job = {
        "id": 101,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": "/managed/session/ignored.mp4",
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
    assert "mp4" not in result["evidence"]["sources"]
    assert "video" not in result["input_snapshot"]["sources"]
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
        "metric_version", "coverage", "classification", "limitations",
    }
    for key, metric in public_deterministic["metrics"].items():
        assert required_metric_fields <= set(metric), key
        assert metric["key"] == key
        assert metric["classification"] == "deterministic"

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
    assert all(entry["kind"] != "mp4" for entry in entries.values())
    issue = public_deterministic["diagnosis"]["issues"][0]
    assert issue["signal"] == "decel_frac high"
    assert issue["severity"] == "info"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["decel_frac"]
    assert issue["event_refs"] == ["flick:1"]
    assert issue["prescriptions"][0]["target_metrics"] == ["decel_frac"]


@pytest.mark.asyncio
async def test_process_one_multimodal_keeps_native_result_when_video_cv_fails(tmp_path: Path):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"stable-video")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    video_fingerprint = snapshot["sources"]["video"]["fingerprint"]
    job = {
        "id": 102,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
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
        str(managed_video),
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
        "basename": source_video.name,
        "fingerprint": video_fingerprint,
        "availability": "available",
        "format_version": "mp4",
    }
    assert result["warnings"] == [{"code": "video_cv_unavailable"}]
    assert result["deterministic"]["diagnosis"]["meta"]["input_mode"] == "multimodal"
    assert "target_relative_error" not in result["deterministic"]["metrics"]
    assert "overshoot_distance" not in result["deterministic"]["metrics"]


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
@pytest.mark.parametrize("damage", ["missing", "truncated", "replaced"])
async def test_process_one_run_based_mode_rejects_invalid_managed_video_before_cv(
    tmp_path: Path,
    caplog,
    input_mode: str,
    damage: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    if damage == "truncated":
        managed_video.write_bytes(b"frozen")
    elif damage == "replaced":
        managed_video.write_bytes(b"x" * source_video.stat().st_size)

    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 201,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
    )

    assert error == {
        "schema_version": "error.v1",
        "category": "input_validation",
        "code": "source_unavailable",
        "message": "分析输入源已不可用或已变更，请重新提交分析。",
        "retryable": False,
        "trace_id": None,
        "details": None,
    }
    native_mock.assert_not_called()
    cv_mock.assert_not_called()
    assert str(source_video) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_video) not in json.dumps(error, ensure_ascii=False)
    assert str(source_video) not in caplog.text
    assert str(managed_video) not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
@pytest.mark.parametrize("missing", ["snapshot", "video", "fingerprint"])
async def test_process_one_run_based_mode_requires_frozen_video_identity(
    tmp_path: Path,
    input_mode: str,
    missing: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    if missing == "snapshot":
        input_snapshot = None
    else:
        input_snapshot = snapshot
        if missing == "video":
            snapshot["sources"].pop("video")
        elif missing == "fingerprint":
            snapshot["sources"]["video"].pop("fingerprint")
    job = {
        "id": 204,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": input_snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
    )

    native_mock.assert_not_called()
    cv_mock.assert_not_called()
    assert error["category"] == "input_validation"
    assert error["code"] == "source_unavailable"
    assert error["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["multimodal", "video_fallback"])
async def test_process_one_rejects_managed_video_changed_during_cv(
    tmp_path: Path,
    input_mode: str,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 202,
        "user_id": "u1",
        "input_mode": input_mode,
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(tmp_path / "managed-stats.csv"),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    def mutate_video(*_args, **_kwargs):
        managed_video.write_bytes(b"changed-during-cv")
        return {}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}

    provider_lookup = AsyncMock(return_value=None)
    with patch(
        "webapp.backend.provider_store.get_default_runtime_profile",
        new=provider_lookup,
    ), patch("webapp.backend.worker._load_backend", return_value=None), patch(
        "webapp.backend.worker.run_report",
        return_value={"diagnosis": {}, "narration": None, "notes": []},
    ) as report_mock:
        error, _native_mock, cv_mock = await _capture_mode_failure(
            job,
            native_result=_native_adapter_result(),
            cv_side_effect=mutate_video,
        )

    cv_mock.assert_called_once()
    assert error["category"] == "input_validation"
    assert error["code"] == "source_unavailable"
    assert error["retryable"] is False
    if input_mode == "video_fallback":
        provider_lookup.assert_not_awaited()
        report_mock.assert_not_called()


@pytest.mark.asyncio
async def test_video_fallback_cv_failure_does_not_log_input_paths(
    tmp_path: Path,
    caplog,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    managed_stats = tmp_path / "managed-stats.csv"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    managed_stats.write_text("stats")
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 206,
        "user_id": "u1",
        "input_mode": "video_fallback",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": str(managed_stats),
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    private_message = f"decoder failed video={managed_video} csv={managed_stats}"

    error, native_mock, cv_mock = await _capture_mode_failure(
        job,
        native_result=_native_adapter_result(),
        cv_side_effect=RuntimeError(private_message),
    )

    native_mock.assert_not_called()
    cv_mock.assert_called_once()
    assert error["category"] == "internal_unknown"
    assert error["code"] == "analysis_failed"
    assert error["retryable"] is True
    assert str(managed_video) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_stats) not in json.dumps(error, ensure_ascii=False)
    assert str(managed_video) not in caplog.text
    assert str(managed_stats) not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_process_one_accepts_managed_video_with_different_mtime(
    tmp_path: Path,
):
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    source = _video_source(source_video)
    managed_stat = managed_video.stat()
    os.utime(
        managed_video,
        ns=(managed_stat.st_atime_ns, source["fingerprint"]["mtime_ns"] + 2_000_000_000),
    )
    snapshot = _native_snapshot()
    snapshot["sources"]["video"] = source
    job = {
        "id": 203,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }

    result, calls, _native_mock, _cv_mock = await _capture_mode_result(
        job,
        native_result=_native_adapter_result(),
        parsed_stats=object(),
        cv_result=({}, {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []}),
    )

    assert calls == ["native", "cv"]
    assert result["input_mode"] == "multimodal"
    assert result["evidence"]["availability"]["mp4"] == "available"


@pytest.mark.asyncio
async def test_managed_video_verification_does_not_block_heartbeat():
    verification_started = threading.Event()
    verification_finished = threading.Event()
    release_verification = threading.Event()
    heartbeat_seen = asyncio.Event()
    verification_calls = 0

    def blocking_verifier(_job, _input_mode):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            verification_started.set()
            try:
                release_verification.wait(timeout=1)
            finally:
                verification_finished.set()

    async def heartbeat(_sid, _worker_id):
        heartbeat_seen.set()
        return True

    job = {
        "id": 205,
        "user_id": "u1",
        "input_mode": "input_native",
        "kovaak_run_id": 42,
        "input_snapshot": _native_snapshot(),
        "video_path": "",
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    process_task = None
    try:
        with patch("webapp.backend.queue.recover_stale_jobs", new=AsyncMock()), \
             patch("webapp.backend.queue.claim_next", new=AsyncMock(return_value=job)), \
             patch("webapp.backend.queue.heartbeat", new=AsyncMock(side_effect=heartbeat)), \
             patch("webapp.backend.queue.mark_done", new=AsyncMock(return_value=True)), \
             patch("webapp.backend.worker.run_native_analysis", return_value=_native_adapter_result()), \
             patch(
                 "webapp.backend.worker._assert_managed_video_matches_snapshot",
                 side_effect=blocking_verifier,
             ):
            process_task = asyncio.create_task(worker.process_one())
            assert await asyncio.to_thread(verification_started.wait, 2)
            assert not verification_finished.is_set()
            await asyncio.wait_for(heartbeat_seen.wait(), timeout=1)
            assert not release_verification.is_set()
            release_verification.set()
            assert await process_task is True
    finally:
        release_verification.set()
        if process_task is not None and not process_task.done():
            await process_task

    assert verification_calls == 2


@pytest.mark.asyncio
async def test_process_one_multimodal_reuses_stats_frozen_by_native_analysis(
    tmp_path: Path,
):
    parsed_stats = object()
    snapshot = _native_snapshot()
    source_video = tmp_path / "source.mp4"
    managed_video = tmp_path / "managed.mp4"
    source_video.write_bytes(b"frozen-video-revision")
    managed_video.write_bytes(source_video.read_bytes())
    snapshot["sources"]["video"] = _video_source(source_video)
    job = {
        "id": 104,
        "user_id": "u1",
        "input_mode": "multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": str(managed_video),
        "csv_path": "",
        "cm_per_360": None,
        "fov": None,
        "created_at": "2026-07-13 12:00:00",
    }
    cv_result = (
        {},
        {"fps": 60, "flicks": [], "kill_frames": [], "corrective_frames": []},
    )

    result, calls, native_mock, cv_mock = await _capture_mode_result(
        job,
        native_result=_native_adapter_result(),
        parsed_stats=parsed_stats,
        cv_result=cv_result,
    )

    assert calls == ["native", "cv"]
    assert native_mock.call_args.kwargs["return_parsed_stats"] is True
    assert cv_mock.call_args.kwargs["stats"] is parsed_stats
    assert result["input_mode"] == "multimodal"
    assert result["deterministic"]["metrics"] == _native_adapter_result()["deterministic"]["metrics"]
    assert result["deterministic"]["timeline"] == _native_adapter_result()["deterministic"]["timeline"]
    assert result["deterministic"]["visual_validation"] == {
        "status": "available",
        "timeline": [],
    }
    assert result["evidence"]["availability"]["mp4"] == "available"
    assert result["warnings"] == []
    assert result["deterministic"]["diagnosis"]["meta"]["input_mode"] == "multimodal"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["v1", "legacy"])
async def test_get_session_still_reads_v1_and_unversioned_legacy_results(
    result_kind: str,
):
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    if result_kind == "v1":
        stored_result = build_analysis_result_v1(
            report={"diagnosis": {"ok": True}, "figures": {}, "notes": [], "narration": None},
            timeline=[],
            narration_status="not_requested",
            cm_per_360=None,
            fov=None,
            artifact_manifest={
                "schema_version": "artifact_manifest.v1",
                "inputs": [],
                "outputs": [],
            },
            created_at="2026-07-13T12:00:00Z",
            completed_at="2026-07-13T12:01:00Z",
        )
    else:
        stored_result = {
            "diagnosis": {"ok": True},
            "figures": {},
            "notes": [],
            "timeline": [],
        }
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(stored_result), sid),
    )
    await conn.commit()

    session = await queue.get_session(sid)

    assert session is not None
    assert session["result"]["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    if result_kind == "v1":
        assert session["result"] == stored_result
    else:
        assert session["result"]["analysis_version"] == LEGACY_ANALYSIS_VERSION


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
