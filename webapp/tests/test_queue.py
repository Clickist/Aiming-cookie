from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from webapp.backend import aiming_profile_store, config, file_store, queue
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


@pytest.mark.asyncio
async def test_enqueue_require_no_active_is_atomic_and_default_still_allows_parallel():
    async def enqueue_restricted():
        try:
            return await queue.enqueue(
                "atomic-owner", "", "", require_no_active=True,
            )
        except queue.ActiveSessionExists:
            return "active"

    restricted = await asyncio.gather(enqueue_restricted(), enqueue_restricted())
    assert sum(isinstance(result, int) for result in restricted) == 1
    assert restricted.count("active") == 1

    parallel = await asyncio.gather(
        queue.enqueue("parallel-owner", "", ""),
        queue.enqueue("parallel-owner", "", ""),
    )
    assert len(set(parallel)) == 2


async def _tombstone(session_id: int) -> dict | None:
    tombstones = file_store.read_json("sessions/_deletion_tombstones.json") or []
    for entry in tombstones:
        if entry.get("analysis_session_id") == session_id:
            return dict(entry)
    return None


async def _seed_terminal_delete(
    monkeypatch,
    tmp_path: Path,
    *,
    owner_id: str,
    create_workspace: bool = True,
) -> dict:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue(owner_id, "", "", input_mode="input_native")
    await queue.claim_next(TEST_WORKER)
    result = _persistent_v2_result(owner_id)
    analysis_id = f"analysis:{sid}"
    result["analysis_id"] = analysis_id
    result["artifact_manifest"]["analysis_id"] = analysis_id
    result["artifact_manifest"]["owned_outputs"][0]["id"] = analysis_id
    result["kovaak_run_ref"] = None
    await queue.mark_done(
        sid,
        result,
        0.0,
        worker_id=TEST_WORKER,
    )
    workspace = session_dir(sid)
    if create_workspace:
        workspace.mkdir(parents=True)
        (workspace / "analysis-owned.json").write_bytes(b"analysis-owned")
    return {
        "id": sid,
        "owner_id": owner_id,
        "workspace": workspace,
    }


async def _rewrite_session(session_id: int, **fields) -> None:
    """Mutate one persisted session file in place (test seeding helper)."""
    path = f"sessions/{session_id}.json"
    session = file_store.read_json(path)
    assert isinstance(session, dict)
    session.update(fields)
    file_store.write_json(path, session)


def _seed_tombstone(session_id: int, owner_id: str) -> None:
    """Append one deletion tombstone (test seeding helper)."""
    tombstones = file_store.read_json("sessions/_deletion_tombstones.json") or []
    tombstones.append({"analysis_session_id": session_id, "owner_id": owner_id})
    file_store.write_json("sessions/_deletion_tombstones.json", tombstones)


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
    sid = await queue.enqueue("u1", "/ex", "/ex.csv")
    await _rewrite_session(sid, status="queued", attempts=1, max_attempts=1)
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
    raw = file_store.read_json(f"sessions/{sid}.json")["result"]
    assert raw == json.loads(dump_contract_json(finite))

    sid2 = await queue.enqueue("u1", "/b", "/b.csv")
    await queue.claim_next(TEST_WORKER)
    with_nan = {
        "schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
        "bad": float("nan"),
    }
    await queue.mark_done(sid2, with_nan, 0.0, worker_id=TEST_WORKER)
    raw2 = file_store.read_json(f"sessions/{sid2}.json")["result"]
    assert "NaN" not in json.dumps(raw2)
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
    raw = json.dumps(file_store.read_json(f"sessions/{sid}.json")["error"])
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
    await _rewrite_session(sid, status="failed", error="secret traceback internals")
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
    s = await queue.get_session(sid)
    assert s["lease_expires_at"] > s["heartbeat_at"]


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_for_owner_worker():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    claimed = await queue.claim_next(TEST_WORKER)
    first_lease = claimed["lease_expires_at"]
    ok = await queue.heartbeat(sid, TEST_WORKER)
    assert ok is True
    s = await queue.get_session(sid)
    assert s["lease_expires_at"] >= first_lease
    assert s["heartbeat_at"] is not None


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
    await _rewrite_session(sid, lease_expires_at="2000-01-01 00:00:00")
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
async def test_recover_stale_fails_when_attempts_exhausted(monkeypatch):
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await _rewrite_session(
        sid,
        status="running",
        attempts=3,
        max_attempts=3,
        worker_id="dead",
        lease_expires_at="2000-01-01 00:00:00",
        task_phase="generating_diagnostics",
    )
    stats = await queue.recover_stale_jobs(now="2099-01-01 00:00:00")
    assert stats["failed"] == 1
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"]["code"] == "stale_lease_exhausted"
    assert s["error"]["retryable"] is True
    assert s["failure_domain"] == "kinematics"
    assert s["task_phase"] is None
    assert s["finished_at"] is not None
    assert s["updated_at"] is not None


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
    retry_id = s["id"]
    assert retry_id != sid
    assert s["status"] == "queued"
    assert s["attempts"] == 0
    assert s["error"] is None
    assert s["result"] is None

    with pytest.raises(queue.RetryNotAllowed) as ei:
        await queue.requeue_for_retry(sid)
    assert ei.value.code == "invalid_status"

    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(retry_id, "x", worker_id=TEST_WORKER)
    Path(s["video_path"]).unlink()
    with pytest.raises(queue.RetryNotAllowed) as ei2:
        await queue.requeue_for_retry(retry_id)
    assert ei2.value.code == "missing_video"
@pytest.mark.asyncio
async def test_retry_copy_failure_rolls_back_and_cleans_workspace(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"video")
    csv.write_text("a,b\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "original failure", worker_id=TEST_WORKER)
    original_before = await queue.get_session(sid)
    touched_workspaces: list[Path] = []

    def interrupted_copy(source: Path, destination: Path) -> int:
        del source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        touched_workspaces.append(destination.parent)
        raise OSError("injected retry copy failure")

    monkeypatch.setattr(queue, "copy_path_to_path", interrupted_copy)

    with pytest.raises(OSError, match="injected retry copy failure"):
        await queue.requeue_for_retry(sid)

    original = await queue.get_session(sid)
    assert original is not None
    assert original["status"] == "failed"
    assert original["error"] == original_before["error"]
    sessions = await queue.list_sessions("u1")
    assert [session["id"] for session in sessions] == [sid]
    assert len(touched_workspaces) == 1
    assert not touched_workspaces[0].exists()


@pytest.mark.asyncio
async def test_concurrent_retries_preserve_single_active_analysis_per_owner(tmp_path):
    failed_ids = []
    for index in range(2):
        video = tmp_path / f"v{index}.mp4"
        csv = tmp_path / f"s{index}.csv"
        video.write_bytes(b"video")
        csv.write_text("a,b\n")
        failed_ids.append(await queue.enqueue("u1", str(video), str(csv)))
        claimed = await queue.claim_next(f"worker-{index}")
        assert claimed is not None
        await queue.mark_failed(
            claimed["id"], "original failure", worker_id=f"worker-{index}",
        )

    results = await asyncio.gather(
        *(queue.requeue_for_retry(session_id) for session_id in failed_ids),
        return_exceptions=True,
    )

    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, queue.RetryNotAllowed)]
    assert len(failures) == 1
    assert failures[0].code == "active_analysis"
    active = await queue.get_active_session("u1")
    assert active is not None


@pytest.mark.asyncio
async def test_retry_process_crash_is_recoverable_as_stale_upload(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    video = tmp_path / "v.mp4"
    csv = tmp_path / "s.csv"
    video.write_bytes(b"video")
    csv.write_text("a,b\n")
    sid = await queue.enqueue("u1", str(video), str(csv))
    await queue.claim_next(TEST_WORKER)
    await queue.mark_failed(sid, "original failure", worker_id=TEST_WORKER)
    touched_workspaces: list[Path] = []

    def crash_during_copy(source: Path, destination: Path) -> int:
        del source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        touched_workspaces.append(destination.parent)
        raise _InjectedProcessCrash()

    monkeypatch.setattr(queue, "copy_path_to_path", crash_during_copy)

    with pytest.raises(_InjectedProcessCrash):
        await queue.requeue_for_retry(sid)

    summary = await queue.reconcile_stale_uploads()
    assert summary["cleaned"] == 1
    assert len(touched_workspaces) == 1
    assert not touched_workspaces[0].exists()
    original = await queue.get_session(sid)
    assert original is not None
    assert original["status"] == "failed"


@pytest.mark.asyncio
async def test_non_object_input_snapshot_is_hidden_from_reads_and_rejected_by_retry():
    session_id = await queue.enqueue(
        "snapshot-owner", "", "", input_mode="input_native", input_snapshot=[],
    )

    assert (await queue.get_session(session_id))["input_snapshot"] is None
    claimed = await queue.claim_next(TEST_WORKER)
    assert claimed is not None
    assert claimed["input_snapshot"] is None
    await queue.mark_failed(session_id, "failed", worker_id=TEST_WORKER)

    with pytest.raises(queue.RetryNotAllowed) as exc_info:
        await queue.requeue_for_retry(session_id)

    assert exc_info.value.code == "missing_snapshot"

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
    assert await _tombstone(seed["id"]) == {
        "analysis_session_id": seed["id"],
        "owner_id": seed["owner_id"],
    }

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()
    await queue.reconcile_analysis_deletions()

    assert not seed["workspace"].exists()
    assert await _tombstone(seed["id"]) is None


@pytest.mark.asyncio
async def test_reconcile_invalidates_profile_using_tombstone_owner_before_cleanup(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    session_id = 90_101
    workspace = session_dir(session_id)
    workspace.mkdir(parents=True)
    payload = {
        "schema_version": "profile_contribution.v1",
        "source_kind": "deterministic",
        "dimensions": [{
            "dimension_key": "static_clicking.terminal_control",
            "scope": "exact_scenario",
            "scenario_profile_ref": "scenario:sixshot@1",
            "metric_ref": "metric:terminal_control",
            "metric_value": 0.4,
            "unit": "normalized_error",
            "expected_direction": "lower_better",
            "confidence": "high",
            "comparability": "comparable",
            "supporting_metric_refs": ["metric:terminal_control"],
            "counterexample_refs": [],
            "candidate_hypothesis_refs": [],
        }],
    }
    await aiming_profile_store.record_deterministic_contribution(
        "tombstone-owner", f"analysis:{session_id}", payload,
    )
    _seed_tombstone(session_id, "tombstone-owner")

    summary = await queue.reconcile_analysis_deletions()

    assert summary["cleaned"] == 1
    assert not workspace.exists()
    assert await _tombstone(session_id) is None
    assert (await aiming_profile_store.list_contributions("tombstone-owner"))[0]["status"] == "invalidated"
    assert (await aiming_profile_store.get_profile_snapshot("tombstone-owner"))["dimensions"] == []


@pytest.mark.asyncio
async def test_reconcile_keeps_tombstone_when_profile_invalidation_fails(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    session_id = 90_102
    workspace = session_dir(session_id)
    workspace.mkdir(parents=True)

    _seed_tombstone(session_id, "profile-failure-owner")

    async def fail_invalidation(_session_id: int, _owner_id: str) -> bool:
        return False

    monkeypatch.setattr(queue, "_invalidate_profile_for_deleted_analysis", fail_invalidation)

    summary = await queue.reconcile_analysis_deletions()

    assert summary["failed"] == 1
    assert workspace.exists()
    assert await _tombstone(session_id) is not None


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
    }
    public_state = json.dumps(
        {"response": result, "tombstone": tombstone},
        ensure_ascii=False,
    )
    assert str(seed["workspace"]) not in public_state
    assert private_error not in public_state
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
    assert await _tombstone(seed["id"]) is not None
    await queue.reconcile_analysis_deletions()
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
    assert await _tombstone(seed["id"]) is not None

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
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

    _seed_tombstone(failing_id, "owner-pending")
    _seed_tombstone(successful_id, "owner-failed")
    private_error = f"locked {failing_workspace}"

    def fail_one(session_id):
        if int(session_id) == failing_id:
            raise OSError(private_error)
        return remove_session_workspace(session_id)

    monkeypatch.setattr(queue, "remove_session_workspace", fail_one)

    first = await queue.reconcile_analysis_deletions()

    assert await _tombstone(failing_id) is not None
    assert await _tombstone(successful_id) is None
    assert failing_workspace.is_dir()
    assert not successful_workspace.exists()
    assert unknown_workspace.is_dir()
    assert str(failing_workspace) not in str(first)
    assert private_error not in str(first)

    await queue.reconcile_analysis_deletions()
    assert await _tombstone(failing_id) is not None

    monkeypatch.setattr(queue, "remove_session_workspace", remove_session_workspace)
    await queue.reconcile_analysis_deletions()
    await queue.reconcile_analysis_deletions()

    assert await _tombstone(failing_id) is None
    assert not failing_workspace.exists()
    assert unknown_workspace.is_dir()
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
async def test_reconcile_stale_uploads_removes_only_uploading_workspace_and_unblocks_owner(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    stale_id = await queue.enqueue("stale-owner", "", "", status="uploading")
    stale_workspace = session_dir(stale_id)
    stale_workspace.mkdir(parents=True)
    (stale_workspace / "video.mp4.tmp").write_bytes(b"partial")

    terminal_id = await queue.enqueue("terminal-owner", "", "")
    await _rewrite_session(terminal_id, status="done")
    terminal_workspace = session_dir(terminal_id)
    terminal_workspace.mkdir(parents=True)
    (terminal_workspace / "keep.bin").write_bytes(b"terminal")

    summary = await queue.reconcile_stale_uploads()

    assert summary == {"processed": 1, "cleaned": 1, "failed": 0}
    assert await queue.get_session(stale_id) is None
    assert await queue.has_active("stale-owner") is False
    assert not stale_workspace.exists()
    assert (await queue.get_session(terminal_id))["status"] == "done"
    assert (terminal_workspace / "keep.bin").read_bytes() == b"terminal"


@pytest.mark.asyncio
async def test_reconcile_stale_uploads_keeps_recoverable_workspace_when_cleanup_fails(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    session_id = await queue.enqueue("locked-owner", "", "", status="uploading")
    workspace = session_dir(session_id)
    workspace.mkdir(parents=True)
    (workspace / "video.mp4.tmp").write_bytes(b"partial")

    def fail_cleanup(actual_session_id: int) -> bool:
        assert actual_session_id == session_id
        raise OSError("workspace locked")

    monkeypatch.setattr(queue, "remove_session_workspace", fail_cleanup)

    summary = await queue.reconcile_stale_uploads()

    assert summary == {"processed": 1, "cleaned": 0, "failed": 1}
    assert (await queue.get_session(session_id))["status"] == "uploading"
    assert await queue.has_active("locked-owner") is True
    assert (workspace / "video.mp4.tmp").read_bytes() == b"partial"


@pytest.mark.asyncio
async def test_delete_wrong_owner_never_touches_workspace_or_tombstone(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue("real-owner", "", "")
    await _rewrite_session(sid, status="done")
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

    run_id = 900_001
    run_meta = {
        "id": run_id,
        "user_id": "artifact-owner",
        "source_key": "delete-boundary-run",
        "scenario": "Scenario",
        "stats_path": str(stats),
        "performance_path": str(performance),
        "mouse_trace_path": str(run_trace),
        "trace_state": "attached",
        "pending_trace_path": None,
        "trace_error": None,
        "capture_session_id": None,
        "window_start_epoch_ms": None,
        "window_end_epoch_ms": None,
        "alignment_state": "unresolved",
        "alignment_summary": None,
        "finalization_state": "finalized",
        "finalization_error": None,
        "video_path": None,
        "video_state": "none",
        "pending_video_path": None,
        "video_request_digest": None,
        "video_receipt": None,
        "video_summary": None,
        "video_error": None,
        "stats_summary": None,
        "performance_summary": None,
        "created_at": "2026-07-15T00:00:00Z",
        "updated_at": "2026-07-15T00:00:00Z",
    }
    file_store.write_json(f"runs/{run_id}/meta.json", run_meta)
    sid = await queue.enqueue(
        "artifact-owner",
        str(source_mp4),
        str(stats),
        kovaak_run_id=run_id,
    )
    await queue.claim_next("test-worker:run-trace")
    await queue.mark_failed(sid, "fixture failure", worker_id="test-worker:run-trace")
    workspace = session_dir(sid)
    workspace.mkdir(parents=True)
    (workspace / "analysis-output.json").write_bytes(b"analysis-owned")
    run_before = file_store.read_json(f"runs/{run_id}/meta.json")

    await queue.delete_session(sid, "artifact-owner")

    run_after = file_store.read_json(f"runs/{run_id}/meta.json")
    assert run_after == run_before
    for path, payload in expected_bytes.items():
        assert path.read_bytes() == payload
    assert not workspace.exists()




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
async def test_delete_session_forbidden_wrong_user():
    sid = await queue.enqueue("owner", "/a", "/a.csv")
    await _rewrite_session(sid, status="done")

    with pytest.raises(queue.SessionForbidden):
        await queue.delete_session(sid, "intruder")
    assert await queue.get_session(sid) is not None

@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_after_reclaim():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next(WORKER_A)
    await _rewrite_session(sid, lease_expires_at="2000-01-01 00:00:00")
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
