from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import queue
from webapp.backend.app import app


@pytest.mark.asyncio
async def test_analyze_returns_session_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["status"] == "queued"


@pytest.mark.asyncio
async def test_get_session_404_when_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions/99999")
    assert resp.status_code == 404
