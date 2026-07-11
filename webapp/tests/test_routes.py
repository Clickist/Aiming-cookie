from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import db, queue
from webapp.backend.app import app
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    LEGACY_ANALYSIS_VERSION,
    build_analysis_result_v1,
    build_error_v1,
    dump_contract_json,
)

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
async def test_analyze_rejects_when_active_job_exists():
    """单用户已有 queued/running job → 429。"""
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
async def test_retry_failed_session_requeues(tmp_path):
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0
    assert body["error"] is None


@pytest.mark.asyncio
async def test_retry_rejects_done_or_running():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 409

    await queue.claim_next(TEST_WORKER)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
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
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(f"/api/sessions/{sid}/retry")
    assert resp.status_code == 409
    assert "视频" in resp.json()["detail"]