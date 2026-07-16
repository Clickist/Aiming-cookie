from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from webapp.backend import coach_store, config, db, queue
from webapp.backend.config import DEFAULT_MAX_ATTEMPTS, LEASE_TTL_SECONDS
from webapp.backend.contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    ERROR_SCHEMA_VERSION,
    build_error_v1,
    dump_contract_json,
)
from webapp.backend.workspace import remove_session_workspace, session_dir

TEST_WORKER = "test-worker:1"


class _InjectedProcessCrash(BaseException):
    pass


async def _tombstone(session_id: int) -> dict | None:
    conn = await db.get_conn()
    row = await (
        await conn.execute(
            "SELECT analysis_session_id, owner_id, cleanup_state, "
            "cleanup_attempts, last_error_code "
            "FROM analysis_deletion_tombstones WHERE analysis_session_id=?",
            (session_id,),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def _seed_terminal_delete(
    monkeypatch,
    tmp_path: Path,
    *,
    owner_id: str,
    create_workspace: bool = True,
) -> dict:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue(owner_id, "", "")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='done' WHERE id=?", (sid,))
    await conn.commit()
    await db.save_chat_message(sid, "user", "legacy question")
    await db.save_chat_message(sid, "assistant", "legacy answer")
    thread = await coach_store.get_or_create_primary_thread(owner_id)
    ref = await coach_store.attach_analysis_ref(int(thread["id"]), sid)
    workspace = session_dir(sid)
    if create_workspace:
        workspace.mkdir(parents=True)
        (workspace / "analysis-owned.json").write_bytes(b"analysis-owned")
    return {
        "id": sid,
        "owner_id": owner_id,
        "thread_id": int(thread["id"]),
        "ref_id": int(ref["id"]),
        "workspace": workspace,
    }


async def _assert_phase_a_rollback(seed: dict) -> None:
    sid = seed["id"]
    session = await queue.get_session(sid)
    assert session is not None
    assert session["status"] == "done"
    assert (seed["workspace"] / "analysis-owned.json").read_bytes() == b"analysis-owned"
    assert [item["content"] for item in await db.load_chat_history(sid)] == [
        "legacy question",
        "legacy answer",
    ]
    assert await coach_store.load_messages(seed["thread_id"]) == []
    refs = await coach_store.list_analysis_refs(seed["thread_id"])
    assert len(refs) == 1
    assert refs[0]["id"] == seed["ref_id"]
    assert refs[0]["status"] == "active"
    assert refs[0]["deleted_at"] is None
    assert await _tombstone(sid) is None


def _persistent_v2_result(owner_id: str) -> dict:
    return {
        "schema_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
        "analysis_version": "native_flicking.v1",
        "analysis_id": "analysis:1",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "owner_id": owner_id,
        "kovaak_run_ref": "run:1",
        "created_at": "2026-07-14T00:00:00Z",
        "completed_at": "2026-07-14T00:00:01Z",
        "status": "done",
        "input_snapshot": {"scenario": "Scenario"},
        "evidence": {
            "sources": {
                "raw_input": {
                    "source": "raw_input",
                    "role": "kinematics",
                    "availability": "available",
                    "artifact_ref": "run:1:trace",
                    "parser_or_format_version": 1,
                    "alignment": "aligned",
                    "warnings": [],
                },
            },
            "provenance": {"adapter": "native_flicking_analysis"},
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "coverage": 1.0,
            "warnings": [],
        },
        "deterministic": {
            "metrics": {
                "path_length": {
                    "key": "path_length",
                    "value": 10.0,
                    "unit": "raw_counts",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native_flicking.v1",
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "limitations": [],
                },
            },
        },
        "artifact_manifest": {
            "schema_version": "artifact_manifest.v2",
            "analysis_id": "analysis:1",
            "external_inputs": [
                {
                    "id": "run:1:trace",
                    "kind": "raw_input",
                    "source": "raw_input",
                    "availability": "available",
                    "ownership": "kovaak_run",
                    "managed": True,
                    "local_only": True,
                    "status": "available",
                    "format_version": 1,
                    "derived_from": [],
                },
            ],
            "owned_outputs": [
                {
                    "id": "analysis:1",
                    "kind": "analysis_result",
                    "source": "analysis",
                    "availability": "available",
                    "ownership": "analysis",
                    "managed": True,
                    "local_only": True,
                    "status": "available",
                    "format_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
                    "derived_from": ["run:1:trace"],
                },
            ],
        },
        "warnings": [],
        "errors": [],
        "normalization_issues": [],
    }


@pytest.mark.asyncio
async def test_enqueue_returns_id_and_queued_status():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    assert isinstance(sid, int) and sid > 0
    s = await queue.get_session(sid)
    assert s["status"] == "queued"
    assert s["video_path"] == "/tmp/v.mp4"


@pytest.mark.asyncio
async def test_native_enqueue_freezes_snapshot_and_checks_run_owner():
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="native", scenario="Scenario",
    )
    snapshot = {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run["id"],
        "sources": {},
        "trace": None,
    }
    sid = await queue.enqueue(
        "u1", "", "", input_mode="input_native", kovaak_run_id=run["id"],
        input_snapshot=snapshot,
    )
    stored = await queue.get_session(sid)
    assert stored["input_mode"] == "input_native"
    assert stored["kovaak_run_id"] == run["id"]
    assert stored["input_snapshot"] == snapshot

    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed["input_mode"] == "input_native"
    assert claimed["input_snapshot"] == snapshot

    with pytest.raises(PermissionError):
        await queue.enqueue(
            "u2", "", "", input_mode="input_native", kovaak_run_id=run["id"],
            input_snapshot=snapshot,
        )


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
async def test_mark_done_rejects_invalid_analysis_result_v2_before_terminal_write():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(TEST_WORKER)

    with pytest.raises(ValueError, match="analysis_type"):
        await queue.mark_done(
            sid,
            {
                "schema_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
                "analysis_id": "analysis:invalid",
            },
            0.0,
            worker_id=TEST_WORKER,
        )

    session = await queue.get_session(sid)
    assert session["status"] == "running"
    assert session["result"] is None


@pytest.mark.asyncio
async def test_mark_done_v2_requires_matching_owner_and_complete_producer_metadata():
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="producer-metadata", scenario="Scenario",
    )
    sid = await queue.enqueue(
        "u1", "", "",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run["id"],
            "sources": {},
            "trace": None,
        },
    )
    await queue.claim_next(TEST_WORKER)

    def bound_result(owner_id: str) -> dict:
        result = _persistent_v2_result(owner_id)
        analysis_id = f"analysis:{sid}"
        result["analysis_id"] = analysis_id
        result["artifact_manifest"]["analysis_id"] = analysis_id
        result["artifact_manifest"]["owned_outputs"][0]["id"] = analysis_id
        result["kovaak_run_ref"] = f"run:{run['id']}"
        return result

    with pytest.raises(ValueError, match="owner_id"):
        await queue.mark_done(
            sid,
            bound_result("other-owner"),
            0.0,
            worker_id=TEST_WORKER,
        )

    unversioned = bound_result("u1")
    del unversioned["analysis_version"]
    with pytest.raises(ValueError, match="analysis_version"):
        await queue.mark_done(sid, unversioned, 0.0, worker_id=TEST_WORKER)

    incomplete = bound_result("u1")
    del incomplete["deterministic"]["metrics"]["path_length"]["coverage"]
    with pytest.raises(ValueError, match="coverage"):
        await queue.mark_done(sid, incomplete, 0.0, worker_id=TEST_WORKER)

    assert await queue.mark_done(
        sid,
        bound_result("u1"),
        0.0,
        worker_id=TEST_WORKER,
    ) is True
    session = await queue.get_session(sid)
    assert session["status"] == "done"
    assert session["result"]["owner_id"] == "u1"


@pytest.mark.parametrize(
    "classification",
    [None, "experimental"],
    ids=["missing", "non-deterministic"],
)
@pytest.mark.asyncio
async def test_mark_done_v2_rejects_missing_or_non_deterministic_metric_classification(
    classification: str | None,
):
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key=f"classification-{classification}",
        scenario="Scenario",
    )
    sid = await queue.enqueue(
        "u1",
        "",
        "",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run["id"],
            "sources": {},
            "trace": None,
        },
    )
    await queue.claim_next(TEST_WORKER)
    result = _persistent_v2_result("u1")
    analysis_id = f"analysis:{sid}"
    result["analysis_id"] = analysis_id
    result["artifact_manifest"]["analysis_id"] = analysis_id
    result["artifact_manifest"]["owned_outputs"][0]["id"] = analysis_id
    result["kovaak_run_ref"] = f"run:{run['id']}"
    metric = result["deterministic"]["metrics"]["path_length"]
    if classification is None:
        del metric["classification"]
    else:
        metric["classification"] = classification

    with pytest.raises(ValueError, match="classification"):
        await queue.mark_done(sid, result, 0.0, worker_id=TEST_WORKER)

    session = await queue.get_session(sid)
    assert session["status"] == "running"
    assert session["result"] is None


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("analysis_id", "analysis:999"),
        ("analysis_type", "tracking"),
        ("input_mode", "multimodal"),
        ("kovaak_run_ref", "run:999"),
    ],
)
@pytest.mark.asyncio
async def test_mark_done_v2_binds_result_identity_to_claimed_session(
    field: str, wrong_value: str,
):
    from webapp.backend import kovaak_run_store

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key=f"binding-{field}", scenario="Scenario",
    )
    snapshot = {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run["id"],
        "sources": {},
        "trace": None,
    }
    sid = await queue.enqueue(
        "u1", "", "",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot=snapshot,
    )
    await queue.claim_next(TEST_WORKER)

    result = _persistent_v2_result("u1")
    analysis_id = f"analysis:{sid}"
    run_ref = f"run:{run['id']}"
    result["analysis_id"] = analysis_id
    result["artifact_manifest"]["analysis_id"] = analysis_id
    result["artifact_manifest"]["owned_outputs"][0]["id"] = analysis_id
    result["kovaak_run_ref"] = run_ref

    if field == "analysis_id":
        result["analysis_id"] = wrong_value
        result["artifact_manifest"]["analysis_id"] = wrong_value
        result["artifact_manifest"]["owned_outputs"][0]["id"] = wrong_value
    else:
        result[field] = wrong_value

    with pytest.raises(ValueError, match=field):
        await queue.mark_done(sid, result, 0.0, worker_id=TEST_WORKER)

    session = await queue.get_session(sid)
    assert session["status"] == "running"
    assert session["result"] is None


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
async def test_delete_phase_a_ref_failure_rolls_back_db_coach_and_workspace(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="phase-a-ref-failure",
    )

    async def fail_ref_update(*_args, **_kwargs):
        raise RuntimeError("injected ref update failure")

    monkeypatch.setattr(coach_store, "mark_analysis_refs_deleted", fail_ref_update)

    with pytest.raises(RuntimeError, match="injected ref update failure"):
        await queue.delete_session(seed["id"], seed["owner_id"])

    await _assert_phase_a_rollback(seed)


@pytest.mark.asyncio
async def test_delete_phase_a_begin_crash_rolls_back_connection_and_state(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="phase-a-begin-crash",
    )
    conn = await db.get_conn()
    original_execute = conn.execute

    async def crash_after_begin(sql, *args):
        cursor = await original_execute(sql, *args)
        if sql == "BEGIN IMMEDIATE":
            raise _InjectedProcessCrash("injected crash after begin")
        return cursor

    monkeypatch.setattr(conn, "execute", crash_after_begin)

    with pytest.raises(_InjectedProcessCrash, match="crash after begin"):
        await queue.delete_session(seed["id"], seed["owner_id"])

    assert conn.in_transaction is False
    await _assert_phase_a_rollback(seed)


@pytest.mark.asyncio
async def test_delete_phase_a_commit_failure_rolls_back_all_sqlite_and_workspace(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="phase-a-commit-failure",
    )
    conn = await db.get_conn()
    await conn.executescript(
        """
        CREATE TABLE deletion_commit_parent(id INTEGER PRIMARY KEY);
        CREATE TABLE deletion_commit_child(
            parent_id INTEGER,
            FOREIGN KEY(parent_id) REFERENCES deletion_commit_parent(id)
                DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TRIGGER inject_deletion_commit_failure
        AFTER INSERT ON analysis_deletion_tombstones
        BEGIN
            INSERT INTO deletion_commit_child(parent_id) VALUES(999999);
        END;
        """
    )
    await conn.commit()

    with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
        await queue.delete_session(seed["id"], seed["owner_id"])

    await _assert_phase_a_rollback(seed)
    child_count = await (
        await conn.execute("SELECT COUNT(*) FROM deletion_commit_child")
    ).fetchone()
    assert child_count[0] == 0


@pytest.mark.asyncio
async def test_delete_crash_after_phase_a_commit_leaves_pending_tombstone(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="post-commit-crash",
    )

    def crash_before_cleanup(_session_id):
        raise _InjectedProcessCrash("simulated process exit before cleanup")

    monkeypatch.setattr(queue, "remove_session_workspace", crash_before_cleanup)

    with pytest.raises(_InjectedProcessCrash):
        await queue.delete_session(seed["id"], seed["owner_id"])

    assert await queue.get_session(seed["id"]) is None
    assert seed["workspace"].is_dir()
    assert await db.load_chat_history(seed["id"]) == []
    messages = await coach_store.load_messages(seed["thread_id"])
    assert [item["content"] for item in messages] == [
        "legacy question",
        "legacy answer",
    ]
    refs = await coach_store.list_analysis_refs(seed["thread_id"])
    assert len(refs) == 1
    assert refs[0]["id"] == seed["ref_id"]
    assert refs[0]["status"] == "deleted"
    assert refs[0]["deleted_at"] is not None
    assert await _tombstone(seed["id"]) == {
        "analysis_session_id": seed["id"],
        "owner_id": seed["owner_id"],
        "cleanup_state": "pending",
        "cleanup_attempts": 0,
        "last_error_code": None,
    }

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()
    await queue.reconcile_analysis_deletions()

    assert not seed["workspace"].exists()
    assert await _tombstone(seed["id"]) is None
    messages_after = await coach_store.load_messages(seed["thread_id"])
    refs_after = await coach_store.list_analysis_refs(seed["thread_id"])
    assert [item["id"] for item in messages_after] == [item["id"] for item in messages]
    assert [item["id"] for item in refs_after] == [item["id"] for item in refs]
    assert refs_after[0]["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_partial_cleanup_failure_is_logically_deleted_and_path_free(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="partial-cleanup",
    )
    remaining = seed["workspace"] / "windows-locked.bin"
    remaining.write_bytes(b"locked")
    private_error = f"busy private path {seed['workspace']}"

    def partial_cleanup(_session_id):
        (seed["workspace"] / "analysis-owned.json").unlink()
        raise OSError(private_error)

    monkeypatch.setattr(queue, "remove_session_workspace", partial_cleanup)

    result = await queue.delete_session(seed["id"], seed["owner_id"])

    assert result["deleted"] is True
    assert result["id"] == seed["id"]
    assert result["cleanup_failed"] == ["workspace"]
    assert await queue.get_session(seed["id"]) is None
    assert remaining.read_bytes() == b"locked"
    tombstone = await _tombstone(seed["id"])
    assert tombstone == {
        "analysis_session_id": seed["id"],
        "owner_id": seed["owner_id"],
        "cleanup_state": "failed",
        "cleanup_attempts": 1,
        "last_error_code": "workspace_cleanup_failed",
    }
    refs = await coach_store.list_analysis_refs(seed["thread_id"])
    assert len(refs) == 1
    assert refs[0]["status"] == "deleted"
    public_state = json.dumps(
        {"response": result, "tombstone": tombstone},
        ensure_ascii=False,
    )
    assert str(seed["workspace"]) not in public_state
    assert private_error not in public_state


@pytest.mark.asyncio
async def test_cleanup_failure_record_commit_error_keeps_pending_tombstone(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="cleanup-record-commit-failure",
    )
    conn = await db.get_conn()
    original_commit = conn.commit

    def fail_cleanup(_session_id):
        raise OSError("injected workspace cleanup failure")

    async def fail_record_commit():
        raise RuntimeError("injected cleanup record commit failure")

    monkeypatch.setattr(queue, "remove_session_workspace", fail_cleanup)
    monkeypatch.setattr(conn, "commit", fail_record_commit)

    result = await queue.delete_session(seed["id"], seed["owner_id"])

    assert result["deleted"] is True
    assert result["cleanup_failed"] == ["workspace"]
    assert conn.in_transaction is False
    assert (await _tombstone(seed["id"]))["cleanup_state"] == "pending"

    monkeypatch.setattr(conn, "commit", original_commit)
    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()
    assert await _tombstone(seed["id"]) is None


@pytest.mark.asyncio
async def test_cleanup_failure_record_crash_rolls_back_before_propagating(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="cleanup-record-crash",
    )
    conn = await db.get_conn()

    def fail_cleanup(_session_id):
        raise OSError("injected workspace cleanup failure")

    async def crash_record_commit():
        raise _InjectedProcessCrash("injected cleanup record crash")

    monkeypatch.setattr(queue, "remove_session_workspace", fail_cleanup)
    monkeypatch.setattr(conn, "commit", crash_record_commit)

    with pytest.raises(_InjectedProcessCrash, match="cleanup record crash"):
        await queue.delete_session(seed["id"], seed["owner_id"])

    assert conn.in_transaction is False
    assert (await _tombstone(seed["id"]))["cleanup_state"] == "pending"


@pytest.mark.asyncio
async def test_delete_absent_workspace_succeeds_and_removes_tombstone(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="absent-workspace",
        create_workspace=False,
    )

    result = await queue.delete_session(seed["id"], seed["owner_id"])

    assert result == {
        "deleted": True,
        "id": seed["id"],
        "files_removed": [],
        "cleanup_failed": [],
    }
    assert await queue.get_session(seed["id"]) is None
    assert await _tombstone(seed["id"]) is None


@pytest.mark.asyncio
async def test_cleanup_success_then_crash_before_tombstone_delete_reconciles(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="cleanup-finalize-crash",
    )

    def cleanup_then_crash(session_id):
        assert remove_session_workspace(session_id) is True
        raise _InjectedProcessCrash("simulated exit before tombstone finalize")

    monkeypatch.setattr(queue, "remove_session_workspace", cleanup_then_crash)

    with pytest.raises(_InjectedProcessCrash):
        await queue.delete_session(seed["id"], seed["owner_id"])

    assert await queue.get_session(seed["id"]) is None
    assert not seed["workspace"].exists()
    assert (await _tombstone(seed["id"]))["cleanup_state"] == "pending"

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()

    assert await _tombstone(seed["id"]) is None


@pytest.mark.asyncio
async def test_cleanup_finalize_commit_failure_rolls_back_and_reconciles(
    monkeypatch,
    tmp_path: Path,
):
    seed = await _seed_terminal_delete(
        monkeypatch,
        tmp_path,
        owner_id="cleanup-finalize-commit-failure",
    )
    conn = await db.get_conn()
    original_commit = conn.commit

    async def fail_finalize_commit():
        raise RuntimeError("injected tombstone finalize commit failure")

    monkeypatch.setattr(conn, "commit", fail_finalize_commit)

    result = await queue.delete_session(seed["id"], seed["owner_id"])

    assert result == {
        "deleted": True,
        "id": seed["id"],
        "files_removed": ["workspace"],
        "cleanup_failed": [],
    }
    assert await queue.get_session(seed["id"]) is None
    assert not seed["workspace"].exists()
    assert conn.in_transaction is False
    assert (await _tombstone(seed["id"]))["cleanup_state"] == "pending"

    monkeypatch.setattr(conn, "commit", original_commit)
    await queue.reconcile_analysis_deletions()

    assert await _tombstone(seed["id"]) is None


@pytest.mark.asyncio
async def test_reconcile_deletions_isolates_failures_and_is_idempotent(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    failing_id = 90_001
    successful_id = 90_002
    unknown_id = 90_003
    failing_workspace = session_dir(failing_id)
    successful_workspace = session_dir(successful_id)
    unknown_workspace = session_dir(unknown_id)
    for workspace in (failing_workspace, successful_workspace, unknown_workspace):
        workspace.mkdir(parents=True)
        (workspace / "artifact.bin").write_bytes(b"artifact")

    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
        "VALUES(?, 'owner-pending')",
        (failing_id,),
    )
    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones("
        "analysis_session_id, owner_id, cleanup_state, cleanup_attempts, last_error_code"
        ") VALUES(?, 'owner-failed', 'failed', 1, 'workspace_cleanup_failed')",
        (successful_id,),
    )
    await conn.commit()
    private_error = f"locked {failing_workspace}"

    def fail_one(session_id):
        if int(session_id) == failing_id:
            raise OSError(private_error)
        return remove_session_workspace(session_id)

    monkeypatch.setattr(queue, "remove_session_workspace", fail_one)

    first = await queue.reconcile_analysis_deletions()

    failed = await _tombstone(failing_id)
    assert failed["cleanup_state"] == "failed"
    assert failed["cleanup_attempts"] == 1
    assert failed["last_error_code"] == "workspace_cleanup_failed"
    assert await _tombstone(successful_id) is None
    assert failing_workspace.is_dir()
    assert not successful_workspace.exists()
    assert unknown_workspace.is_dir()
    assert str(failing_workspace) not in str(first)
    assert private_error not in str(first)

    await queue.reconcile_analysis_deletions()
    failed_again = await _tombstone(failing_id)
    assert failed_again["cleanup_attempts"] == 2

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()
    await queue.reconcile_analysis_deletions()

    assert await _tombstone(failing_id) is None
    assert not failing_workspace.exists()
    assert unknown_workspace.is_dir()


@pytest.mark.asyncio
async def test_reconcile_finalize_db_failure_propagates_with_clean_connection(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    session_id = 90_004
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
        "VALUES(?, 'finalize-db-failure')",
        (session_id,),
    )
    await conn.commit()
    original_commit = conn.commit

    async def fail_finalize_commit():
        raise RuntimeError("injected reconciliation finalize db failure")

    monkeypatch.setattr(conn, "commit", fail_finalize_commit)

    with pytest.raises(RuntimeError, match="reconciliation finalize db failure"):
        await queue.reconcile_analysis_deletions()

    assert conn.in_transaction is False
    assert (await _tombstone(session_id))["cleanup_state"] == "pending"

    monkeypatch.setattr(conn, "commit", original_commit)
    await queue.reconcile_analysis_deletions()
    assert await _tombstone(session_id) is None


@pytest.mark.parametrize("status", ["queued", "running", "uploading"])
@pytest.mark.asyncio
async def test_delete_nonterminal_never_touches_workspace_or_tombstone(
    status: str,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue("active-owner", "", "", status=status)
    workspace = session_dir(sid)
    workspace.mkdir(parents=True)
    (workspace / "keep.bin").write_bytes(b"keep")

    with pytest.raises(queue.SessionNotDeletable) as exc_info:
        await queue.delete_session(sid, "active-owner")

    assert exc_info.value.code == "active"
    assert (workspace / "keep.bin").read_bytes() == b"keep"
    assert await _tombstone(sid) is None


@pytest.mark.asyncio
async def test_delete_wrong_owner_never_touches_workspace_or_tombstone(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue("real-owner", "", "")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='done' WHERE id=?", (sid,))
    await conn.commit()
    workspace = session_dir(sid)
    workspace.mkdir(parents=True)
    (workspace / "keep.bin").write_bytes(b"keep")

    with pytest.raises(queue.SessionForbidden):
        await queue.delete_session(sid, "other-owner")

    assert (workspace / "keep.bin").read_bytes() == b"keep"
    assert await _tombstone(sid) is None


@pytest.mark.asyncio
async def test_delete_preserves_run_trace_and_all_user_sources(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sources = tmp_path / "user-sources"
    sources.mkdir()
    stats = sources / "stats.csv"
    performance = sources / "performance.json"
    source_mp4 = sources / "recording.mp4"
    run_trace = config.DATA_ROOT / "runs" / "fixture-run" / "run-trace.bin"
    run_trace.parent.mkdir(parents=True)
    expected_bytes = {
        stats: b"stats-user-owned",
        performance: b"performance-user-owned",
        source_mp4: b"mp4-user-owned",
        run_trace: b"trace-run-owned",
    }
    for path, payload in expected_bytes.items():
        path.write_bytes(payload)

    conn = await db.get_conn()
    run_id = int((await (
        await conn.execute(
            "INSERT INTO kovaak_runs("
            "user_id, source_key, scenario, stats_path, performance_path, "
            "mouse_trace_path, trace_state"
            ") VALUES(?, ?, ?, ?, ?, ?, 'attached') RETURNING id",
            (
                "artifact-owner",
                "delete-boundary-run",
                "Scenario",
                str(stats),
                str(performance),
                str(run_trace),
            ),
        )
    ).fetchone())[0])
    sid = await queue.enqueue("artifact-owner", str(source_mp4), str(stats))
    await conn.execute(
        "UPDATE sessions SET status='done', kovaak_run_id=? WHERE id=?",
        (run_id, sid),
    )
    await conn.commit()
    workspace = session_dir(sid)
    workspace.mkdir(parents=True)
    (workspace / "analysis-output.json").write_bytes(b"analysis-owned")
    run_before = dict((await (
        await conn.execute("SELECT * FROM kovaak_runs WHERE id=?", (run_id,))
    ).fetchone()))

    await queue.delete_session(sid, "artifact-owner")

    run_after = dict((await (
        await conn.execute("SELECT * FROM kovaak_runs WHERE id=?", (run_id,))
    ).fetchone()))
    assert run_after == run_before
    for path, payload in expected_bytes.items():
        assert path.read_bytes() == payload
    assert not workspace.exists()




@pytest.mark.asyncio
async def test_delete_done_session_keeps_coach_messages_marks_ref_deleted():
    from webapp.backend import coach_store

    sid = await queue.enqueue("u1", "/a", "/a.csv")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='done' WHERE id=?", (sid,))
    await conn.commit()

    thread = await coach_store.get_or_create_primary_thread("u1")
    tid = int(thread["id"])
    await coach_store.append_message(tid, "user", "keep me")
    await coach_store.append_message(tid, "assistant", "still here")
    await coach_store.attach_analysis_ref(tid, sid)

    out = await queue.delete_session(sid, "u1")
    assert out["deleted"] is True
    assert await queue.get_session(sid) is None

    msgs = await coach_store.load_messages(tid)
    assert [m["content"] for m in msgs] == ["keep me", "still here"]

    refs = await coach_store.list_analysis_refs(tid)
    assert len(refs) == 1
    assert refs[0]["analysis_session_id"] == sid
    assert refs[0]["status"] == "deleted"
    assert refs[0]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_delete_session_rejects_queued_and_running():
    sid_q = await queue.enqueue("u1", "/q", "/q.csv")
    with pytest.raises(queue.SessionNotDeletable) as exc_q:
        await queue.delete_session(sid_q, "u1")
    assert exc_q.value.code == "active"

    sid_r = await queue.enqueue("u1", "/r", "/r.csv")
    await queue.claim_next(TEST_WORKER)
    with pytest.raises(queue.SessionNotDeletable):
        await queue.delete_session(sid_r, "u1")


@pytest.mark.asyncio
async def test_delete_session_migrates_legacy_chat_before_removing_session():
    from webapp.backend import coach_store

    sid = await queue.enqueue("u1", "/a", "/a.csv")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='failed' WHERE id=?", (sid,))
    await conn.commit()
    await db.save_chat_message(sid, "user", "legacy hello")
    await db.save_chat_message(sid, "assistant", "legacy reply")

    thread_before = await coach_store.get_or_create_primary_thread("u1")
    assert await coach_store.load_messages(int(thread_before["id"])) == []

    await queue.delete_session(sid, "u1")
    assert await queue.get_session(sid) is None

    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert [(m["role"], m["content"], m["legacy_session_id"]) for m in msgs] == [
        ("user", "legacy hello", sid),
        ("assistant", "legacy reply", sid),
    ]


@pytest.mark.asyncio
async def test_legacy_chat_migration_does_not_copy_unsafe_trace_payloads():
    from webapp.backend import coach_store

    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await db.save_chat_message(
        sid,
        "assistant",
        "legacy unsafe trace",
        trace=[
            {
                "raw_trace": "legacy-raw-trace-sentinel",
                "path": r"C:\Users\point\private\trace.csv",
                "payload": "legacy-payload-sentinel",
                "timestamps": [1_000, 1_001],
            },
        ],
    )

    migrated = await coach_store.migrate_session_legacy_messages(sid)
    assert migrated["messages_copied"] == 1
    thread = await coach_store.get_or_create_primary_thread("u1")
    messages = await coach_store.load_messages(int(thread["id"]))
    assert [message["content"] for message in messages] == ["legacy unsafe trace"]

    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT trace_json FROM coach_messages WHERE legacy_session_id=?",
        (sid,),
    )
    row = await cur.fetchone()
    serialized = f"{row['trace_json']} {json.dumps(messages[0]['trace'], ensure_ascii=False)}"
    for sentinel in (
        "legacy-raw-trace-sentinel",
        r"C:\Users\point\private\trace.csv",
        "legacy-payload-sentinel",
        "timestamps",
    ):
        assert sentinel not in serialized


@pytest.mark.asyncio
async def test_delete_session_forbidden_wrong_user():
    sid = await queue.enqueue("owner", "/a", "/a.csv")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='done' WHERE id=?", (sid,))
    await conn.commit()

    with pytest.raises(queue.SessionForbidden):
        await queue.delete_session(sid, "intruder")
    assert await queue.get_session(sid) is not None

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
