from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import (
    coach_commands,
    config,
    db,
    kovaak_run_store,
    queue,
    read_models,
    training_plan_store,
)
import webapp.backend.routes as routes_mod
from webapp.backend.app import app
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    build_analysis_result_v1,
    build_analysis_result_v2,
    build_artifact_manifest_v1,
    build_error_v1,
    dump_contract_json,
)
from webapp.backend.workspace import session_dir

TEST_WORKER = "test-worker:routes"

_CURRENT_TRAINING_PLAN_PAYLOAD = {
    "title": "Current training projection fixture",
    "diagnostic_context": {
        "analysis_refs": ["analysis:current-training"],
        "metric_refs": ["metric:terminal_control"],
        "diagnosis_refs": ["diagnosis:current-training"],
    },
    "prescriptions": [{
        "scenario": "Reviewed scenario",
        "cue": "Keep one clear cue.",
        "purpose": "Route projection coverage.",
        "target_metric_refs": ["metric:terminal_control"],
        "expected_direction": "decrease",
        "source_level": "deterministic_rule",
    }],
}

_CURRENT_TRAINING_VERIFICATION_TARGETS = [{
    "target_metric": "metric:terminal_control",
    "expected_direction": "decrease",
    "comparable_requirements": ["same scenario"],
    "retest_after": "after the planned practice",
    "insufficient_evidence_behavior": "keep the plan unchanged",
}]


def _current_training_item(*, scenario_profile_ref: str) -> dict[str, str]:
    return {
        "diagnosis_ref": "diagnosis:internal-only@1",
        "knowledge_ref": "knowledge:internal-only@1",
        "scenario_profile_ref": scenario_profile_ref,
        "baseline_metric_ref": "metric:internal-only",
        "expected_direction": "lower_better",
        "practice_condition": "Use the same reviewed setup.",
        "cue": "Commit once, then observe.",
        "dose_guardrail": "Stop after two short sets.",
        "matched_retest_ref": "retest-spec:internal-matched@1",
        "near_transfer_retest_ref": "retest-spec:internal-transfer@1",
        "review_date": "Review after the next confirmed practice.",
    }


def _minimal_manifest() -> dict:
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "inputs": [],
        "outputs": [],
    }


def _minimal_v1_result() -> dict:
    return build_analysis_result_v1(
        report={
            "diagnosis": {"summary": {"sparc": {"med": -1.0}}},
            "figures": {},
            "notes": [],
            "narration": None,
        },
        timeline=[],
        narration_status="not_requested",
        cm_per_360=40.0,
        fov=103.0,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )


def _minimal_native_v2_result() -> dict:
    return build_analysis_result_v2(
        analysis_id="analysis:1",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="run:1",
        evidence={
            "sources": {},
            "provenance": {},
            "availability": {"raw_input": "available", "mp4": "not_present"},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic={
            "metrics": {},
            "timeline": [
                {
                    "relative_ms": 125.0,
                    "source": "performance",
                    "payload_type": "Kill",
                },
            ],
        },
        artifact_manifest={
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [],
            "owned_outputs": [
                {"id": "analysis:1", "kind": "analysis_result"},
            ],
        },
        input_snapshot={"scenario": "Scenario"},
        created_at="2026-07-13T00:00:00Z",
        completed_at="2026-07-13T00:00:01Z",
        warnings=[],
        errors=[],
    )


@pytest.mark.asyncio
async def test_analyze_returns_session_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"fakevideo", "video/mp4"),
                "csv": ("s.csv", b"frame,time_s\n0,0\n", "text/csv"),
            },
            headers={"X-User-Id": "u1"},
        )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    assert sid > 0


@pytest.mark.asyncio
async def test_session_response_projects_error_details():
    session_id = await queue.enqueue("public-owner", "", "")
    result = _minimal_native_v2_result()
    error = build_error_v1(
        category="internal_unknown",
        code="analysis_failed",
        message="analysis failed",
        retryable=False,
        trace_id=None,
        details={"secret": "do-not-return"},
    )
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=?, error=? WHERE id=?",
        (dump_contract_json(result), dump_contract_json(error), session_id),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "public-owner"},
    ) as client:
        response = await client.get(f"/api/sessions/{session_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["error"]["code"] == "analysis_failed"
    assert "details" not in payload["error"]


@pytest.mark.asyncio
async def test_analyze_writes_files_under_session_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    from webapp.backend import routes

    destinations = []
    original_stream = routes.stream_upload_to_path

    async def record_temp_destination(*args, **kwargs):
        destination = args[1]
        destinations.append(destination)
        assert destination.name.endswith(".tmp")
        assert not Path(str(destination)[:-4]).exists()
        return await original_stream(*args, **kwargs)

    monkeypatch.setattr(routes, "stream_upload_to_path", record_temp_destination)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_ws"},
    ) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("clip.mp4", b"fakevideo", "video/mp4"),
                "csv": ("stats.csv", b"frame,time_s\n0,0\n", "text/csv"),
            },
            headers={"X-User-Id": "u_ws"},
        )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    ws = session_dir(sid)
    assert ws.is_dir()
    video_path = ws / "video.mp4"
    csv_path = ws / "stats.csv"
    assert video_path.is_file()
    assert csv_path.is_file()
    row = await queue.get_session(sid)
    assert row is not None
    assert row["video_path"] == str(video_path)
    assert row["csv_path"] == str(csv_path)
    assert destinations == [ws / "video.mp4.tmp", ws / "stats.csv.tmp"]
    assert list(ws.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_analyze_is_claimable_only_after_upload_finishes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    from webapp.backend import routes

    original_stream = routes.stream_upload_to_path
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()
    calls = 0

    async def paused_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            upload_started.set()
            await release_upload.wait()
        return await original_stream(*args, **kwargs)

    monkeypatch.setattr(routes, "stream_upload_to_path", paused_stream)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_uploading"},
    ) as client:
        request = asyncio.create_task(
            client.post(
                "/api/analyze",
                files={
                    "video": ("v.mp4", b"fakevideo", "video/mp4"),
                    "csv": ("s.csv", b"frame,time_s\n0,0\n", "text/csv"),
                },
            )
        )
        await upload_started.wait()
        assert await queue.has_active("u_uploading") is True
        assert await queue.claim_next(TEST_WORKER) is None

        release_upload.set()
        resp = await request

    assert resp.status_code == 200
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["id"] == resp.json()["session_id"]


@pytest.mark.asyncio
async def test_analyze_stream_limit_cleans_workspace_and_session(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(config, "MAX_CSV_BYTES", 16)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_abort"},
    ) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"ok", "video/mp4"),
                "csv": ("s.csv", b"x" * 32, "text/csv"),
            },
            headers={"X-User-Id": "u_abort"},
        )
    assert resp.status_code == 413
    sessions_root = tmp_path / "sessions"
    if sessions_root.exists():
        assert list(sessions_root.iterdir()) == []
    rows = await queue.list_sessions("u_abort")
    assert rows == []


@pytest.mark.asyncio
async def test_analyze_rejects_low_disk_before_enqueue(monkeypatch, tmp_path):
    """DATA_ROOT 盘空闲低于阈值 → 507，不入队、不写工作区。"""
    from collections import namedtuple

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    min_free = 500 * 1024 * 1024
    monkeypatch.setattr(config, "MIN_FREE_DISK_BYTES", min_free)
    _Disk = namedtuple("_Disk", ("total", "used", "free"))

    def _low_free_usage(_path):
        return _Disk(1_000_000_000, 999_000_000, min_free - 1)

    monkeypatch.setattr("webapp.backend.routes.shutil.disk_usage", _low_free_usage)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_disk"},
    ) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"fakevideo", "video/mp4"),
                "csv": ("s.csv", b"frame,time_s\n0,0\n", "text/csv"),
            },
            headers={"X-User-Id": "u_disk"},
        )
    assert resp.status_code == 507
    assert "空间" in resp.json()["detail"]
    sessions_root = tmp_path / "sessions"
    assert not sessions_root.exists() or list(sessions_root.iterdir()) == []
    assert await queue.list_sessions("u_disk") == []


@pytest.mark.asyncio
async def test_analyze_rejects_when_active_job_exists():
    """单用户已有 uploading/queued/running job → 429。"""
    await queue.enqueue("u1", "/a", "/a.csv")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"x", "video/mp4"),
                "csv": ("s.csv", b"y", "text/csv"),
            },
            headers={"X-User-Id": "u1"},
        )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_analyze_rejects_oversized_video():
    """视频 > 100MB → 413。"""
    big = b"x" * (101 * 1024 * 1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", big, "video/mp4"),
                "csv": ("s.csv", b"y", "text/csv"),
            },
            headers={"X-User-Id": "u2"},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_analyze_rejects_oversized_csv():
    """CSV > MAX_CSV_BYTES(10MB)→ 413。"""
    from webapp.backend.config import MAX_CSV_BYTES
    big_csv = b"x" * (MAX_CSV_BYTES + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"fakevideo", "video/mp4"),
                "csv": ("s.csv", big_csv, "text/csv"),
            },
            headers={"X-User-Id": "u_csv_oversize"},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_get_session_returns_queued_status():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["status"] == "queued"


@pytest.mark.asyncio
async def test_get_session_404_when_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get("/api/sessions/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_returns_v1_result_for_new_row():
    sid = await queue.enqueue("u1", "/a", "/a.csv", cm_per_360=40.0, fov=103.0)
    v1 = _minimal_v1_result()
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(v1), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert body["result"]["input"] == {"cm_per_360": 40.0, "fov": 103.0}
    assert "diagnosis" in body["result"]["deterministic"]


@pytest.mark.asyncio
async def test_get_session_wraps_legacy_result_as_v1():
    legacy = {
        "diagnosis": {"summary": {"x": 1}},
        "figures": {},
        "narration": "旧版讲解",
        "notes": [],
        "timeline": [],
    }
    sid = await queue.enqueue("u1", "/a", "/a.csv", cm_per_360=30.0, fov=90.0)
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(legacy), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["schema_version"] == ANALYSIS_RESULT_SCHEMA_VERSION
    assert body["result"]["analysis_version"] == LEGACY_ANALYSIS_VERSION
    assert body["result"]["deterministic"]["diagnosis"] == legacy["diagnosis"]
    assert body["result"]["narration"]["status"] == "available"
    assert body["result"]["narration"]["text"] == "旧版讲解"


@pytest.mark.asyncio
async def test_get_session_returns_error_v1():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    err = build_error_v1(
        category="internal_unknown",
        code="analysis_failed",
        message="分析失败，请重试；若持续失败请联系维护者。",
        retryable=False,
        trace_id="550e8400-e29b-41d4-a716-446655440000",
    )
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='failed', error=? WHERE id=?",
        (dump_contract_json(err), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["schema_version"] == ERROR_SCHEMA_VERSION
    assert body["error"]["code"] == "analysis_failed"
    assert isinstance(body["error"]["message"], str)


@pytest.mark.asyncio
async def test_session_status_exposes_job_state_foundation_fields():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["created_at"].endswith("Z")
    assert "T" in body["created_at"]
    assert body["attempts"] == 1
    assert body["max_attempts"] == 3
    assert body["worker_id"] == TEST_WORKER
    assert body["started_at"] is not None
    assert body["started_at"].endswith("Z")
    assert body["finished_at"] is None


@pytest.mark.asyncio
async def test_retry_requires_idempotency_key(tmp_path):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"x")
    csv.write_text("a\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "x", worker_id=TEST_WORKER)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")

    assert resp.status_code == 400
    assert "idempotency_key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_retry_failed_session_requeues_once_for_same_idempotency_key(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"x")
    csv.write_text("a\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(
        sid,
        build_error_v1(
            category="internal_unknown",
            code="analysis_failed",
            message="分析失败，请重试；若持续失败请联系维护者。",
            retryable=True,
            trace_id=None,
        ),
        worker_id=TEST_WORKER,
    )

    calls = 0
    original_retry = coach_commands.retry_analysis

    async def counted_retry(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_retry(*args, **kwargs)

    monkeypatch.setattr(coach_commands, "retry_analysis", counted_retry)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "Idempotency-Key": "retry-failed-session",
        },
    ) as client:
        first = await client.post(f"/api/sessions/{sid}/retry")
        replay = await client.post(f"/api/sessions/{sid}/retry")
    assert first.status_code == 200
    assert replay.status_code == 200
    body = first.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0
    assert body["error"] is None
    assert replay.json() == body
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_rejects_reused_idempotency_key_for_different_session(tmp_path):
    session_ids = []
    for index in range(2):
        video = tmp_path / f"v-{index}.mp4"
        csv = tmp_path / f"s-{index}.csv"
        video.write_bytes(b"x")
        csv.write_text("a\n")
        sid = await queue.enqueue("u1", str(video), str(csv))
        await queue.claim_next(TEST_WORKER)
        await queue.mark_failed(sid, "x", worker_id=TEST_WORKER)
        session_ids.append(sid)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "Idempotency-Key": "retry-conflict",
        },
    ) as client:
        first = await client.post(f"/api/sessions/{session_ids[0]}/retry")
        conflict = await client.post(f"/api/sessions/{session_ids[1]}/retry")

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "idempotency" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_retry_preserves_owner_isolation(tmp_path):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"x")
    csv.write_text("a\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "x", worker_id=TEST_WORKER)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u2",
            "Idempotency-Key": "retry-other-owner",
        },
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_retry_rejects_done_or_running():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "Idempotency-Key": "retry-queued-session",
        },
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 409

    await queue.claim_next(TEST_WORKER)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "Idempotency-Key": "retry-running-session",
        },
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retry_rejects_missing_files(tmp_path):
    video = tmp_path / "gone.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"x")
    csv.write_text("a\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "x", worker_id=TEST_WORKER)
    video.unlink()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "Idempotency-Key": "retry-missing-files",
        },
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 409
    assert "视频" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_input_native_timeline_preserves_relative_time_without_fake_video_fields():
    sid = await queue.enqueue(
        "u1", "", "", input_mode="input_native",
    )
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(_minimal_native_v2_result()), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fps"] is None
    assert body["duration_frames"] is None
    assert body["events"] == [
        {
            "frame": None,
            "time_s": None,
            "relative_ms": 125.0,
            "type": "Kill",
            "label": "Kill",
            "source": "performance",
        }
    ]


@pytest.mark.asyncio
async def test_session_video_supports_range_requests_for_seek(tmp_path):
    sid = await queue.enqueue("u1", "", str(tmp_path / "stats.csv"))
    managed_dir = session_dir(sid)
    managed_dir.mkdir(parents=True, exist_ok=True)
    video = managed_dir / "video.mp4"
    video.write_bytes(b"0123456789")
    result = build_analysis_result_v1(
        report={
            "diagnosis": {},
            "figures": {},
            "notes": [],
            "narration": None,
        },
        timeline=[],
        narration_status="not_requested",
        cm_per_360=None,
        fov=None,
        artifact_manifest=build_artifact_manifest_v1(
            video_path=str(video),
            csv_path=None,
            created_at="2026-07-15T00:00:00Z",
        ),
        created_at="2026-07-15T00:00:00Z",
        completed_at="2026-07-15T00:00:01Z",
    )
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', video_path=?, result=? WHERE id=?",
        (str(video), dump_contract_json(result), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1", "Range": "bytes=2-5"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
        detail = await client.get(f"/api/sessions/{sid}")

    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 2-5/10"
    assert resp.content == b"2345"
    assert detail.json()["history"]["visual_replay"] == {
        "kind": "seekable_mp4",
        "available": True,
        "seekable": True,
        "endpoint": f"/api/sessions/{sid}/video",
        "artifact_ref": "input-video",
        "reason": None,
    }


async def _seed_route_video_run(tmp_path: Path, source_key: str) -> tuple[dict, Path]:
    stats = tmp_path / f"{source_key} Stats.csv"
    performance = tmp_path / f"{source_key} Performance.perf"
    raw = tmp_path / f"{source_key} Raw.bin"
    stats.write_bytes(f"stats-{source_key}".encode())
    performance.write_bytes(f"performance-{source_key}".encode())
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key=source_key,
        scenario="Scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        mouse_trace_path=str(raw),
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
    run = await kovaak_run_store.set_run_alignment(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
        state="resolved",
        summary={
            "start_ms": 1_000,
            "end_ms": 7_000,
            "duration_ms": 6_000,
            "start_source": "test_start",
            "end_source": "test_end",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        start_epoch_ms=1_000,
        end_epoch_ms=7_000,
    ) or run
    video = tmp_path / "data" / "runs" / str(run["id"]) / "video-auto.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(f"video-{source_key}".encode())
    fingerprint = {
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "size": video.stat().st_size,
    }
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET video_path=?, video_state='attached', "
        "video_receipt_json=?, video_summary_json=?, "
        "finalization_state='finalized' WHERE id=?",
        (
            str(video.resolve()),
            json.dumps({"version": "capture_receipt.v1"}),
            json.dumps({
                "availability": "available",
                "fingerprint": fingerprint,
                "packetCount": 60,
                "visibleDuration100ns": 10_000_000,
                "timebaseVersion": "time_alignment.v2",
            }),
            run["id"],
        ),
    )
    await conn.commit()
    return await kovaak_run_store.get_kovaak_run(
        run["id"], config.DESKTOP_LOCAL_PROFILE,
    ), video


def _run_owned_video_result(session_id: int, run_id: int) -> dict:
    analysis_ref = f"analysis:{session_id}"
    video_ref = f"{analysis_ref}:video"
    result = build_analysis_result_v2(
        analysis_id=analysis_ref,
        analysis_type="flicking",
        input_mode="video_fallback",
        kovaak_run_ref=f"run:{run_id}",
        evidence={
            "sources": {
                "mp4": {
                    "source": "mp4",
                    "role": "visual_evidence",
                    "availability": "available",
                    "artifact_ref": video_ref,
                    "parser_or_format_version": "mp4",
                    "alignment": "aligned",
                    "warnings": [],
                },
            },
            "provenance": {"adapter": "run-owned-video-test"},
            "availability": {"mp4": "available"},
            "alignment": {"status": "aligned", "coverage_ratio": 1.0},
            "coverage": 1.0,
            "warnings": [],
        },
        deterministic={
            "metrics": {},
            "timeline": [],
            "diagnosis": {"profile": {"label": "Run-owned video review"}},
        },
        artifact_manifest={
            "schema_version": "artifact_manifest.v2",
            "analysis_id": analysis_ref,
            "external_inputs": [{
                "id": video_ref,
                "kind": "mp4",
                "source": "mp4",
                "availability": "available",
                "ownership": "analysis",
                "managed": True,
                "local_only": True,
                "status": "available",
                "format_version": "mp4",
                "derived_from": [],
            }],
            "owned_outputs": [{
                "id": analysis_ref,
                "kind": "analysis_result",
                "source": "analysis",
                "availability": "available",
                "ownership": "analysis",
                "managed": True,
                "local_only": True,
                "status": "available",
                "format_version": "analysis_result.v2",
                "derived_from": [video_ref],
            }],
        },
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run_id,
            "scenario": "Scenario",
            "sources": {
                "video": {
                    "artifact_ref": video_ref,
                    "availability": "available",
                    "format_version": "mp4",
                },
            },
            "canonical_time_window": {
                "start_ms": 1000,
                "end_ms": 7000,
                "timebase_version": "time_alignment.v2",
            },
        },
        created_at="2026-07-25T00:00:00Z",
        completed_at="2026-07-25T00:00:01Z",
        warnings=[],
        errors=[],
    )
    result["evidence"]["derived_artifact"] = {
        "artifact_ref": f"{analysis_ref}:evidence:abc",
        "evidence_revision": "sha256:" + ("a" * 64),
        "contract_version": "analysis_evidence_artifact.v1",
        "checksum_sha256": "a" * 64,
        "size_bytes": 10,
    }
    return result


@pytest.mark.asyncio
async def test_run_owned_video_route_analyzes_one_run_and_leaves_other_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    selected, selected_video = await _seed_route_video_run(tmp_path, "selected")
    waiting, _waiting_video = await _seed_route_video_run(tmp_path, "waiting")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
    ) as client:
        created = await client.post(
            f"/api/kovaak-runs/{selected['id']}/analyze",
            headers={"Idempotency-Key": "auto-run-owned-video"},
            json={"input_mode": "multimodal"},
        )
        listed = await client.get("/api/kovaak-runs")
        detail = await client.get(f"/api/kovaak-runs/{selected['id']}")

    assert created.status_code == 200, created.text
    assert listed.status_code == detail.status_code == 200
    by_id = {item["id"]: item for item in listed.json()["runs"]}
    assert by_id[selected["id"]]["readiness_state"] == "analyzed"
    assert by_id[waiting["id"]]["readiness_state"] == "pending_analysis"
    assert detail.json()["video_artifact_ref"].startswith(
        f"run:{selected['id']}:video:"
    )
    assert detail.json()["analysis_count"] == 1
    assert str(tmp_path) not in listed.text
    assert str(tmp_path) not in detail.text
    session = await queue.get_session(created.json()["session_id"])
    managed_video = Path(session["video_path"])
    assert managed_video == session_dir(created.json()["session_id"]) / "video.mp4"
    assert managed_video.samefile(selected_video)
    conn = await db.get_conn()
    assert (await (await conn.execute("SELECT COUNT(*) FROM sessions")).fetchone())[0] == 1


@pytest.mark.asyncio
async def test_run_owned_analysis_video_and_segments_are_owner_scoped_and_degrade_on_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, run_video = await _seed_route_video_run(tmp_path, "playback")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
    ) as client:
        created = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers={"Idempotency-Key": "run-owned-playback"},
            json={"input_mode": "multimodal"},
        )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    session = await queue.get_session(session_id)
    private_snapshot = dict(session["input_snapshot"])
    private_snapshot["canonical_time_window"] = {
        "start_ms": 1000,
        "end_ms": 7000,
        "timebase_version": "time_alignment.v2",
    }
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', input_snapshot_json=?, result=? WHERE id=?",
        (
            json.dumps(private_snapshot, ensure_ascii=False, separators=(",", ":")),
            dump_contract_json(_run_owned_video_result(session_id, run["id"])),
            session_id,
        ),
    )
    await conn.commit()

    async def read_artifact(**_kwargs):
        return {"evidence_segments": [{
            "segment_id": f"analysis:{session_id}:segment:1",
            "analysis_ref": f"analysis:{session_id}",
            "segment_kind": "flick",
            "focus_start_ms": 1800,
            "focus_end_ms": 2200,
            "title_key": "flick.overshoot",
            "rank_reason": "representative",
            "issue_refs": ["issue:overshoot"],
            "metric_refs": ["metric:decel"],
            "event_refs": ["flick:1"],
            "available_channels": ["mp4"],
            "source_coverage": 1.0,
            "confidence": 0.9,
            "limitations": [],
        }]}

    monkeypatch.setattr(
        routes_mod.evidence_store,
        "read_analysis_evidence_artifact",
        read_artifact,
    )
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "")
    owner_headers = {"X-User-Id": config.DESKTOP_LOCAL_PROFILE}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        replay = await client.get(
            f"/api/sessions/{session_id}/video",
            headers={**owner_headers, "Range": "bytes=2-6"},
        )
        segments = await client.get(
            f"/api/sessions/{session_id}/evidence-segments",
            headers=owner_headers,
        )
        forbidden = await client.get(
            f"/api/sessions/{session_id}/video",
            headers={"X-User-Id": "other-owner"},
        )

    assert replay.status_code == 206
    assert replay.content == run_video.read_bytes()[2:7]
    assert segments.status_code == 200, segments.text
    segment_body = segments.json()
    assert segment_body["schema_version"] == "frontend_evidence_segments.v1"
    assert segment_body["video_availability"] == "available"
    assert segment_body["segments"][0]["playback"] == {
        "schema_version": "evidence_segment_playback.v1",
        "availability": "available",
        "video_route": f"/api/sessions/{session_id}/video",
        "relative_start_ms": 800,
        "relative_end_ms": 1200,
        "limitations": [],
    }
    assert forbidden.status_code == 403
    assert str(tmp_path) not in replay.text + segments.text + forbidden.text

    removed = await kovaak_run_store.remove_run_evidence(
        run["id"], config.DESKTOP_LOCAL_PROFILE, "video", tmp_path / "data",
    )
    assert removed["removal_state"] == "completed"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=owner_headers,
    ) as client:
        unavailable = await client.get(f"/api/sessions/{session_id}/video")
        detail = await client.get(f"/api/sessions/{session_id}")
        stale_segment = await client.get(
            f"/api/sessions/{session_id}/evidence-segments"
        )

    assert unavailable.status_code == 410
    assert unavailable.json() == {
        "schema_version": "managed_video_unavailable.v1",
        "availability": "unavailable",
        "reason": "run_owned_video_unavailable",
    }
    history = detail.json()["history"]
    assert history["visual_replay"]["kind"] == "unavailable"
    mp4_ref = next(ref for ref in history["evidence_refs"] if ref["source"] == "mp4")
    assert mp4_ref["availability"] == "unavailable"
    coach_context = await routes_mod._diagnosis_from_done_session(
        await queue.get_session(session_id)
    )
    assert coach_context["evidence_summary"]["availability"]["mp4"] == "unavailable"
    stale_body = stale_segment.json()
    assert stale_body["segments"][0]["segment_id"] == f"analysis:{session_id}:segment:1"
    assert stale_body["video_availability"] == "unavailable"
    assert stale_body["segments"][0]["playback"]["availability"] == "unavailable"
    assert str(tmp_path) not in unavailable.text + detail.text + stale_segment.text


@pytest.mark.asyncio
async def test_video_fallback_without_derived_segments_keeps_run_owned_playback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, run_video = await _seed_route_video_run(tmp_path, "video-fallback-empty")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
    ) as client:
        created = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers={"Idempotency-Key": "video-fallback-empty"},
            json={"input_mode": "multimodal"},
        )

    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    result = _run_owned_video_result(session_id, run["id"])
    result["evidence"].pop("derived_artifact")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(result), session_id),
    )
    await conn.commit()

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "")
    owner_headers = {"X-User-Id": config.DESKTOP_LOCAL_PROFILE}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        replay = await client.get(
            f"/api/sessions/{session_id}/video",
            headers={**owner_headers, "Range": "bytes=2-6"},
        )
        segments = await client.get(
            f"/api/sessions/{session_id}/evidence-segments",
            headers=owner_headers,
        )

    assert replay.status_code == 206
    assert replay.content == run_video.read_bytes()[2:7]
    assert segments.status_code == 200, segments.text
    assert segments.json()["schema_version"] == "frontend_evidence_segments.v1"
    assert segments.json()["video_availability"] == "available"
    assert segments.json()["video_route"] == f"/api/sessions/{session_id}/video"
    assert segments.json()["segments"] == []
    assert str(tmp_path) not in replay.text + segments.text

    invalid_result = _run_owned_video_result(session_id, run["id"])
    await conn.execute(
        "UPDATE sessions SET result=? WHERE id=?",
        (dump_contract_json(invalid_result), session_id),
    )
    await conn.commit()

    async def reject_invalid_artifact(**_kwargs):
        raise ValueError("invalid evidence artifact")

    monkeypatch.setattr(
        routes_mod.evidence_store,
        "read_analysis_evidence_artifact",
        reject_invalid_artifact,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=owner_headers,
    ) as client:
        invalid = await client.get(
            f"/api/sessions/{session_id}/evidence-segments"
        )

    assert invalid.status_code == 404
    assert str(tmp_path) not in invalid.text


@pytest.mark.parametrize(
    ("knowledge_ref", "scenario_profile_ref", "practice_condition"),
    [
        (
            "knowledge:static.flicking-terminal-control@2",
            "scenario:static.1wall_6targets_small@1",
            "保持完全相同的静态场景条件，只测试一个终点控制提示。",
        ),
        (
            "knowledge:dynamic.click-error-and-acquisition@2",
            "scenario:dynamic.pasu_small_reload@1",
            "保持完全相同的动态场景条件，每次只改变一个易于辨认的运动变量。",
        ),
        (
            "knowledge:dynamic.speed-matching-and-reading@2",
            "scenario:dynamic.pasu_small_reload@1",
            "保持相同的动态场景条件，每次只改变一个运动特征。",
        ),
        (
            "knowledge:tracking.predictable-speed-matching@2",
            "scenario:tracking.whj_smooth_strafe_sphere_easy@1",
            "保持完全相同的可预测运动条件，只测试稳定的速度匹配。",
        ),
        (
            "knowledge:switching.transition-and-arrival@2",
            "scenario:switching.beants_larger@1",
            "保持完全相同的 beanTS Larger 场景条件；每个训练组只关注切换移动或到达稳定中的一项。",
        ),
    ],
)
def test_current_training_projection_localizes_all_reviewed_plan_items(
    knowledge_ref: str,
    scenario_profile_ref: str,
    practice_condition: str,
):
    item = _current_training_item(scenario_profile_ref=scenario_profile_ref)
    item.update({"knowledge_ref": knowledge_ref, "status": "planned"})

    body = read_models.build_current_training_v1(
        plan={"status": "active"},
        items=[item],
    )

    projected = body["items"][0]
    assert projected["practice_condition"] == practice_condition
    for field in ("practice_condition", "cue", "dose_guardrail", "retest"):
        assert any("\u4e00" <= char <= "\u9fff" for char in projected[field])


@pytest.mark.asyncio
async def test_current_training_projection_is_owner_scoped_bounded_and_launch_ref_safe():
    plan = await training_plan_store.create_draft(
        "current-training-owner",
        _CURRENT_TRAINING_PLAN_PAYLOAD,
        verification_targets=_CURRENT_TRAINING_VERIFICATION_TARGETS,
    )
    real_ref = "scenario:static.1wall_6targets_small@1"
    planned_payload = _current_training_item(scenario_profile_ref=real_ref)
    planned_payload["knowledge_ref"] = "knowledge:static.flicking-terminal-control@2"
    planned = await training_plan_store.add_plan_item(
        "current-training-owner", plan["plan_id"],
        planned_payload,
    )
    active = await training_plan_store.add_plan_item(
        "current-training-owner", plan["plan_id"],
        _current_training_item(scenario_profile_ref=real_ref),
    )
    completed_payload = _current_training_item(scenario_profile_ref="scenario:missing@1")
    completed_payload["review_date"] = "Review metric:internal-only after practice."
    completed = await training_plan_store.add_plan_item(
        "current-training-owner", plan["plan_id"],
        completed_payload,
    )
    cancelled = await training_plan_store.add_plan_item(
        "current-training-owner", plan["plan_id"],
        _current_training_item(scenario_profile_ref=real_ref),
    )
    await training_plan_store.set_plan_item_status(
        "current-training-owner", active["item_ref"], "active",
        reason="test:current-training-active",
    )
    await training_plan_store.set_plan_item_status(
        "current-training-owner", completed["item_ref"], "completed",
        reason="test:current-training-completed",
    )
    await training_plan_store.set_plan_item_status(
        "current-training-owner", cancelled["item_ref"], "cancelled",
        reason="test:current-training-cancelled",
    )
    await training_plan_store.save_plan("current-training-owner", plan["plan_id"])
    await training_plan_store.activate_plan("current-training-owner", plan["plan_id"])
    transitions_before = await training_plan_store.list_transitions(
        "current-training-owner", plan["plan_id"],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get(
            "/api/current-training", headers={"X-User-Id": "no-current-training"},
        )
        current = await client.get(
            "/api/current-training", headers={"X-User-Id": "current-training-owner"},
        )
        forbidden_owner = await client.get(
            "/api/current-training", headers={"X-User-Id": "another-owner"},
        )

    assert missing.status_code == forbidden_owner.status_code == 200
    assert missing.json() == {
        "schema_version": "current_training.v1",
        "availability": "unavailable",
        "reason": "no_current_plan",
        "plan_status": None,
        "total_item_count": 0,
        "visible_item_count": 0,
        "limitations": [],
        "items": [],
    }
    body = current.json()
    assert current.status_code == 200, current.text
    assert body["schema_version"] == "current_training.v1"
    assert body["availability"] == "available"
    assert body["plan_status"] == "active"
    assert body["total_item_count"] == 4
    assert body["visible_item_count"] == len(body["items"]) == 3
    assert "items_limited_to_three" in body["limitations"]
    assert {item["status"] for item in body["items"]} == {
        "planned", "active", "completed",
    }
    assert body["items"][0]["display_name"] == "1wall 6targets small"
    assert body["items"][0]["scenario_profile_ref"] == real_ref
    planned_item = next(item for item in body["items"] if item["status"] == "planned")
    assert planned_item["practice_condition"] == (
        "保持完全相同的静态场景条件，只测试一个终点控制提示。"
    )
    assert planned_item["cue"] == (
        "只使用一个动作效果提示：先受控地到达目标，再让点击跟随已经稳定的瞄点。"
    )
    assert planned_item["dose_guardrail"] == (
        "使用能够清楚判断表现的难度版本，每次只改变一个任务变量；"
        "如果出现不适，或与提示无关的表现质量明显下降，就停止或降低难度。"
    )
    assert planned_item["retest"] == "下一次可比训练后复查。"
    unavailable = next(item for item in body["items"] if item["status"] == "completed")
    assert unavailable["display_name"] is None
    assert unavailable["scenario_availability"] == "unavailable"
    assert unavailable["observation"] is None
    assert unavailable["retest"] is None
    active_item = next(item for item in body["items"] if item["status"] == "active")
    assert active_item["retest"] == "Review after the next confirmed practice."
    serialized = current.text
    for forbidden in (
        "diagnosis_ref", "knowledge_ref", "baseline_metric_ref",
        "matched_retest_ref", "near_transfer_retest_ref", "internal-only",
    ):
        assert forbidden not in serialized
    assert await training_plan_store.list_transitions(
        "current-training-owner", plan["plan_id"],
    ) == transitions_before

    await training_plan_store.pause_plan("current-training-owner", plan["plan_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        paused = await client.get(
            "/api/current-training", headers={"X-User-Id": "current-training-owner"},
        )
    assert paused.status_code == 200
    assert paused.json()["plan_status"] == "paused"
    assert "plan_paused" in paused.json()["limitations"]


@pytest.mark.asyncio
async def test_storage_and_run_evidence_removal_are_desktop_only_and_path_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_ROOT", data_root)
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "storage-token")
    run, video = await _seed_route_video_run(tmp_path, "storage-removal")
    video_bytes = video.stat().st_size
    sid = await queue.enqueue(
        config.DESKTOP_LOCAL_PROFILE, "", "", status="failed",
    )
    analysis_file = session_dir(sid) / "analysis.bin"
    analysis_file.parent.mkdir(parents=True)
    analysis_file.write_bytes(b"analysis-bytes")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        denied_storage = await client.get("/api/storage")
        denied_remove = await client.delete(
            f"/api/kovaak-runs/{run['id']}/evidence/video"
        )
        headers = {"X-Aiming-Cookie-Desktop-Token": "storage-token"}
        storage = await client.get("/api/storage", headers=headers)
        removed = await client.delete(
            f"/api/kovaak-runs/{run['id']}/evidence/video", headers=headers,
        )

    assert denied_storage.status_code in {401, 403}
    assert denied_remove.status_code in {401, 403}
    assert storage.status_code == 200, storage.text
    assert storage.json()["categories"] == {
        "analysis_artifacts_bytes": len(b"analysis-bytes"),
        "run_video_bytes": video_bytes,
        "run_raw_bytes": 0,
        "incomplete_recovery_bytes": 0,
    }
    assert storage.json()["total_bytes"] == len(b"analysis-bytes") + video_bytes
    assert removed.status_code == 200, removed.text
    assert removed.json()["run_ref"] == f"run:{run['id']}"
    assert removed.json()["evidence_kind"] == "video"
    assert removed.json()["affected_modes"] == ["multimodal", "video_fallback"]
    assert str(tmp_path) not in removed.text
    assert await queue.get_session(sid) is not None
    assert analysis_file.is_file()
    route_paths = {
        route.path for route in app.routes if hasattr(route, "path")
    }
    assert "/api/kovaak-runs/{run_id}/evidence" not in route_paths
    assert "/api/kovaak-runs/evidence/clear" not in route_paths
