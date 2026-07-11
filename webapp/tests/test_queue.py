from __future__ import annotations

import json

import pytest

from webapp.backend import db, queue
from webapp.backend.config import DEFAULT_MAX_ATTEMPTS, LEASE_TTL_SECONDS
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    build_error_v1,
    dump_contract_json,
)

TEST_WORKER = "test-worker:1"


@pytest.mark.asyncio
async def test_enqueue_returns_id_and_queued_status():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    assert isinstance(sid, int) and sid > 0
    s = await queue.get_session(sid)
    assert s["status"] == "queued"
    assert s["video_path"] == "/tmp/v.mp4"


@pytest.mark.asyncio
async def test_enqueue_initializes_attempt_defaults():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    s = await queue.get_session(sid)
    assert s["attempts"] == 0
    assert s["max_attempts"] == DEFAULT_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_enqueue_default_max_attempts_is_three():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    s = await queue.get_session(sid)
    assert s["max_attempts"] == 3


@pytest.mark.asyncio
async def test_claim_next_returns_oldest_queued():
    a = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["id"] == a
    assert "created_at" in claimed


@pytest.mark.asyncio
async def test_claim_next_increments_attempt_and_records_worker():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed["id"] == sid
    assert claimed["attempts"] == 1
    assert claimed["worker_id"] == TEST_WORKER
    assert claimed["started_at"] is not None


@pytest.mark.asyncio
async def test_claim_next_skips_running():
    await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    b = await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["id"] == b


@pytest.mark.asyncio
async def test_claim_next_skips_exhausted_job():
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO sessions("
        "user_id, video_path, csv_path, status, attempts, max_attempts"
        ") VALUES('u1', '/ex', '/ex.csv', 'queued', 1, 1)"
    )
    await conn.commit()
    ok = await queue.enqueue("u1", "/ok", "/ok.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["id"] == ok


@pytest.mark.asyncio
async def test_claim_next_empty_returns_none():
    assert await queue.claim_next(TEST_WORKER) is None


@pytest.mark.asyncio
async def test_mark_done_writes_result():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    await queue.mark_done(sid, {"signals": ["x"]}, 0.003, worker_id=TEST_WORKER)
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["signals"] == ["x"]


@pytest.mark.asyncio
async def test_mark_done_records_finished_at():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    await queue.mark_done(sid, {"signals": []}, 0.0, worker_id=TEST_WORKER)
    s = await queue.get_session(sid)
    assert s["finished_at"] is not None


@pytest.mark.asyncio
async def test_mark_done_uses_strict_json_serialization():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    finite = {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "value": 1.5,
        "nested": {"ok": True},
    }
    await queue.mark_done(sid, finite, 0.0, worker_id=TEST_WORKER)
    conn = await db.get_conn()
    cur = await conn.execute("SELECT result FROM sessions WHERE id=?", (sid,))
    row = await cur.fetchone()
    assert row["result"] == dump_contract_json(finite)

    sid2 = await queue.enqueue("u1", "/b", "/b.csv")
    await queue.claim_next(TEST_WORKER)
    with_nan = {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "bad": float("nan"),
    }
    await queue.mark_done(sid2, with_nan, 0.0, worker_id=TEST_WORKER)
    cur = await conn.execute("SELECT result FROM sessions WHERE id=?", (sid2,))
    row2 = await cur.fetchone()
    assert "NaN" not in row2["result"]
    parsed = await queue.get_session(sid2)
    assert parsed["result"]["bad"] is None


@pytest.mark.asyncio
async def test_mark_failed_writes_error():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "boom", worker_id=TEST_WORKER)
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["code"] == "legacy_error"


@pytest.mark.asyncio
async def test_mark_failed_writes_error_v1_without_exception_details():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    err = build_error_v1(
        category="internal_unknown",
        code="analysis_failed",
        message="分析失败，请重试；若持续失败请联系维护者。",
        retryable=False,
        trace_id="550e8400-e29b-41d4-a716-446655440000",
    )
    await queue.mark_failed(sid, err, worker_id=TEST_WORKER)
    conn = await db.get_conn()
    cur = await conn.execute("SELECT error FROM sessions WHERE id=?", (sid,))
    raw = (await cur.fetchone())["error"]
    assert "CSRT" not in raw
    assert "Traceback" not in raw
    parsed = json.loads(raw)
    assert parsed["schema_version"] == ERROR_SCHEMA_VERSION
    assert parsed["code"] == "analysis_failed"
    s = await queue.get_session(sid)
    assert s["error"]["message"] == "分析失败，请重试；若持续失败请联系维护者。"


@pytest.mark.asyncio
async def test_get_session_wraps_legacy_string_error():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='failed', error=? WHERE id=?",
        ("secret traceback internals", sid),
    )
    await conn.commit()
    s = await queue.get_session(sid)
    assert s["error"]["schema_version"] == ERROR_SCHEMA_VERSION
    assert s["error"]["code"] == "legacy_error"
    assert "secret traceback" not in json.dumps(s["error"])


@pytest.mark.asyncio
async def test_has_active_detects_queued_or_running():
    assert await queue.has_active("u1") is False
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    assert await queue.has_active("u1") is True
    await queue.claim_next(TEST_WORKER)
    assert await queue.has_active("u1") is True
    await queue.mark_done(sid, {}, 0, worker_id=TEST_WORKER)
    assert await queue.has_active("u1") is False


@pytest.mark.asyncio
async def test_claim_next_sets_lease_and_heartbeat():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed["id"] == sid
    assert claimed["lease_expires_at"] is not None
    assert claimed["heartbeat_at"] is not None
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT lease_expires_at, heartbeat_at FROM sessions WHERE id=?", (sid,),
    )
    row = await cur.fetchone()
    assert row["lease_expires_at"] > row["heartbeat_at"]


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_for_owner_worker():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    first_lease = claimed["lease_expires_at"]
    ok = await queue.heartbeat(sid, TEST_WORKER)
    assert ok is True
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT lease_expires_at, heartbeat_at FROM sessions WHERE id=?", (sid,),
    )
    row = await cur.fetchone()
    assert row["lease_expires_at"] >= first_lease
    assert row["heartbeat_at"] is not None


@pytest.mark.asyncio
async def test_heartbeat_ignores_wrong_worker_or_non_running():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    assert await queue.heartbeat(sid, "other-worker") is False
    await queue.mark_done(sid, {"signals": []}, 0.0, worker_id=TEST_WORKER)
    assert await queue.heartbeat(sid, TEST_WORKER) is False


@pytest.mark.asyncio
async def test_recover_stale_requeues_when_attempts_remain():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)  # attempts=1, max=3
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET lease_expires_at = '2000-01-01 00:00:00' WHERE id=?",
        (sid,),
    )
    await conn.commit()
    stats = await queue.recover_stale_jobs(now="2026-07-10 12:00:00")
    assert stats["requeued"] == 1
    assert stats["failed"] == 0
    s = await queue.get_session(sid)
    assert s["status"] == "queued"
    assert s["attempts"] == 1
    assert s["worker_id"] is None
    # can be claimed again
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed["id"] == sid
    assert claimed["attempts"] == 2


@pytest.mark.asyncio
async def test_recover_stale_fails_when_attempts_exhausted():
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO sessions("
        "user_id, video_path, csv_path, status, attempts, max_attempts, "
        "worker_id, lease_expires_at"
        ") VALUES('u1', '/a', '/a.csv', 'running', 3, 3, 'dead', "
        "'2000-01-01 00:00:00')"
    )
    await conn.commit()
    cur = await conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
    sid = (await cur.fetchone())["id"]
    stats = await queue.recover_stale_jobs(now="2026-07-10 12:00:00")
    assert stats["failed"] == 1
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["code"] == "stale_lease_exhausted"
    assert s["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_requeue_failed_session_for_retry(tmp_path):
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"x")
    csv.write_text("a,b\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(
        sid,
        build_error_v1(
            category="internal_unknown",
            code="analysis_failed",
            message="分析失败，请重试；若持续失败请联系维护者。",
            retryable=False,
            trace_id=None,
        ),
        worker_id=TEST_WORKER,
    )
    s = await queue.requeue_for_retry(sid)
    assert s["status"] == "queued"
    assert s["attempts"] == 0
    assert s["error"] is None
    assert s["result"] is None

    with pytest.raises(queue.RetryNotAllowed) as ei:
        await queue.requeue_for_retry(sid)
    assert ei.value.code == "invalid_status"

    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "x", worker_id=TEST_WORKER)
    video.unlink()
    with pytest.raises(queue.RetryNotAllowed) as ei2:
        await queue.requeue_for_retry(sid)
    assert ei2.value.code == "missing_video"

WORKER_A = "worker-a:1"
WORKER_B = "worker-b:1"


@pytest.mark.asyncio
async def test_mark_done_requires_running_owner_worker():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    assert await queue.mark_done(
        sid, {"owner": "wrong"}, 0.0, worker_id="other-worker",
    ) is False
    s = await queue.get_session(sid)
    assert s["status"] == "running"
    assert s["worker_id"] == TEST_WORKER
    assert await queue.mark_done(
        sid, {"owner": "right"}, 0.1, worker_id=TEST_WORKER,
    ) is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["owner"] == "right"


@pytest.mark.asyncio
async def test_mark_failed_requires_running_owner_worker():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)
    assert await queue.mark_failed(sid, "nope", worker_id="other-worker") is False
    s = await queue.get_session(sid)
    assert s["status"] == "running"
    assert await queue.mark_failed(sid, "yes", worker_id=TEST_WORKER) is True
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["code"] == "legacy_error"


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_after_reclaim():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(WORKER_A)
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET lease_expires_at = '2000-01-01 00:00:00' WHERE id=?",
        (sid,),
    )
    await conn.commit()
    await queue.recover_stale_jobs(now="2026-07-10 12:00:00")
    await queue.claim_next(WORKER_B)
    assert await queue.mark_done(
        sid, {"winner": "B"}, 0.0, worker_id=WORKER_B,
    ) is True
    assert await queue.mark_done(
        sid, {"winner": "A"}, 0.0, worker_id=WORKER_A,
    ) is False
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["winner"] == "B"

