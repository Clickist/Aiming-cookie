from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import coach_commands, config, db, queue
from webapp.backend.app import app
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    build_analysis_result_v1,
    build_analysis_result_v2,
    build_error_v1,
    dump_contract_json,
)
from webapp.backend.workspace import session_dir

TEST_WORKER = "test-worker:routes"


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
async def test_analyze_writes_files_under_session_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
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
    video = tmp_path / "seek.mp4"
    video.write_bytes(b"0123456789")
    sid = await queue.enqueue("u1", str(video), str(tmp_path / "stats.csv"))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1", "Range": "bytes=2-5"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")

    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 2-5/10"
    assert resp.content == b"2345"
