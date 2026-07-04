from __future__ import annotations

import pytest

from webapp.backend import queue


@pytest.mark.asyncio
async def test_enqueue_returns_id_and_queued_status():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    assert isinstance(sid, int) and sid > 0
    s = await queue.get_session(sid)
    assert s["status"] == "queued"
    assert s["video_path"] == "/tmp/v.mp4"


@pytest.mark.asyncio
async def test_claim_next_returns_oldest_queued():
    a = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed["id"] == a


@pytest.mark.asyncio
async def test_claim_next_skips_running():
    await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()  # a → running
    b = await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed["id"] == b


@pytest.mark.asyncio
async def test_claim_next_empty_returns_none():
    assert await queue.claim_next() is None


@pytest.mark.asyncio
async def test_mark_done_writes_result():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_done(sid, {"signals": ["x"]}, 0.003)
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["signals"] == ["x"]


@pytest.mark.asyncio
async def test_mark_failed_writes_error():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_failed(sid, "boom")
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"] == "boom"


@pytest.mark.asyncio
async def test_has_active_detects_queued_or_running():
    assert await queue.has_active("u1") is False
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    assert await queue.has_active("u1") is True
    await queue.claim_next()
    assert await queue.has_active("u1") is True  # running
    await queue.mark_done(sid, {}, 0)
    assert await queue.has_active("u1") is False
