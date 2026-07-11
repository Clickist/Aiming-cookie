from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import coach_store, db, queue
from webapp.backend.app import app
from webapp.backend.contracts import build_analysis_result_v1, dump_contract_json
from webapp.backend.queue import (
    SessionForbidden,
    SessionNotDeletable,
    SessionNotFound,
)

TEST_WORKER = "test-worker:history"


def _v1_result_with_profile_label(label: str) -> dict:
    return build_analysis_result_v1(
        report={
            "diagnosis": {
                "profile": {"label": label, "archetype_id": "test"},
                "summary": {},
            },
            "figures": {},
            "notes": [],
            "narration": None,
        },
        timeline=[],
        narration_status="not_requested",
        cm_per_360=40.0,
        fov=103.0,
        artifact_manifest={
            "schema_version": "artifact_manifest.v1",
            "inputs": [],
            "outputs": [],
        },
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )


@pytest.mark.asyncio
async def test_list_sessions_empty():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_empty"},
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


@pytest.mark.asyncio
async def test_list_sessions_newest_first_owner_only():
    s_old = await queue.enqueue("u1", "/a", "/a.csv")
    s_new = await queue.enqueue("u1", "/b", "/b.csv")
    await queue.enqueue("u2", "/c", "/c.csv")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 2
    ids = [s["id"] for s in sessions]
    assert ids == [s_new, s_old]


@pytest.mark.asyncio
async def test_list_sessions_omits_full_result():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    v1 = _v1_result_with_profile_label("标签")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(v1), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    item = resp.json()["sessions"][0]
    assert "result" not in item


@pytest.mark.asyncio
async def test_list_sessions_summary_label_from_profile():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    label = "减速不足型"
    v1 = _v1_result_with_profile_label(label)
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(v1), sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    assert resp.json()["sessions"][0]["summary_label"] == label


@pytest.mark.asyncio
async def test_delete_session_removes_row_chat_and_files():
    tmp = Path(tempfile.gettempdir()) / "aiming_cookie_test"
    tmp.mkdir(parents=True, exist_ok=True)
    video = tmp / "del_video.mp4"
    csv = tmp / "del_stats.csv"
    video.write_bytes(b"vid")
    csv.write_bytes(b"csv")

    sid = await queue.enqueue("u1", str(video), str(csv))
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='failed' WHERE id=?",
        (sid,),
    )
    await conn.commit()
    await db.save_chat_message(sid, "user", "hello")
    await db.save_chat_message(sid, "assistant", "hi")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert body["id"] == sid
    assert set(body["files_removed"]) == {"video", "csv"}

    assert await queue.get_session(sid) is None
    history = await db.load_chat_history(sid)
    assert history == []

    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]

    assert not video.exists()
    assert not csv.exists()


@pytest.mark.asyncio
async def test_delete_session_404():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.delete("/api/sessions/99999")

    assert resp.status_code == 404

    with pytest.raises(SessionNotFound):
        await queue.delete_session(99999, "u1")


@pytest.mark.asyncio
async def test_delete_session_forbidden_other_user():
    sid = await queue.enqueue("u1", "/a", "/a.csv")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "intruder"},
    ) as client:
        resp = await client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 403
    assert await queue.get_session(sid) is not None

    with pytest.raises(SessionForbidden):
        await queue.delete_session(sid, "intruder")


@pytest.mark.asyncio
async def test_delete_running_session_rejected():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["id"] == sid

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 409
    assert "分析进行中" in resp.json()["detail"]
    assert await queue.get_session(sid) is not None

    with pytest.raises(SessionNotDeletable) as exc_info:
        await queue.delete_session(sid, "u1")
    assert exc_info.value.code == "active"


@pytest.mark.asyncio
async def test_delete_queued_session_rejected():
    sid = await queue.enqueue("u1", "/a", "/a.csv")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 409
    assert "分析进行中" in resp.json()["detail"]
    assert await queue.get_session(sid) is not None

    with pytest.raises(SessionNotDeletable):
        await queue.delete_session(sid, "u1")