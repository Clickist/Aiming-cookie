from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import coach_store, db, history_trends, kovaak_run_store, queue
from webapp.backend.app import app
from webapp.backend.contracts import (
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    build_analysis_result_v1,
    build_analysis_result_v2,
    dump_contract_json,
)
from webapp.backend.queue import (
    SessionForbidden,
    SessionNotDeletable,
    SessionNotFound,
)
from webapp.backend.workspace import session_dir

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


def _v2_history_result(
    *,
    session_id: int,
    run_id: int,
    input_mode: str,
    include_mp4: bool,
    mp4_evidence_availability: str = "available",
) -> dict:
    analysis_ref = f"analysis:{session_id}"
    trace_ref = f"run:{run_id}:trace"
    video_ref = f"analysis:{session_id}:video"
    sources = {
        "raw_input": {
            "source": "raw_input",
            "role": "kinematics",
            "availability": "available",
            "artifact_ref": trace_ref,
            "parser_or_format_version": 1,
            "alignment": "aligned",
            "warnings": [],
        },
    }
    external_inputs = [
        {
            "id": trace_ref,
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
    ]
    snapshot_sources: dict[str, dict] = {}
    if include_mp4:
        sources["mp4"] = {
            "source": "mp4",
            "role": "visual_evidence",
            "availability": mp4_evidence_availability,
            "artifact_ref": video_ref,
            "parser_or_format_version": "mp4",
            "alignment": (
                "aligned" if mp4_evidence_availability == "available" else "failed"
            ),
            "warnings": (
                [] if mp4_evidence_availability == "available"
                else ["video_cv_unavailable"]
            ),
        }
        external_inputs.append({
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
        })
        snapshot_sources["video"] = {
            "artifact_ref": video_ref,
            "availability": "available",
            "format_version": "mp4",
        }
    return build_analysis_result_v2(
        analysis_id=analysis_ref,
        analysis_type="flicking",
        input_mode=input_mode,
        kovaak_run_ref=f"run:{run_id}",
        evidence={
            "sources": sources,
            "provenance": {"adapter": "history-test"},
            "availability": {key: value["availability"] for key, value in sources.items()},
            "alignment": {"status": "aligned", "coverage_ratio": 1.0},
            "coverage": 1.0,
            "warnings": [],
        },
        deterministic={
            "metrics": {
                "distance": {
                    "key": "distance",
                    "value": 10.0,
                    "unit": "raw_counts",
                    "availability": "available",
                    "provenance": {"kind": "derived", "sources": ["raw_input"]},
                    "metric_version": "native.v1",
                    "coverage": 1.0,
                    "classification": "deterministic",
                    "calibration_ref": "calibration:test.v1",
                    "limitations": [],
                },
            },
            "diagnosis": {
                "issues": [{
                    "signal": "test signal",
                    "metric_refs": ["distance"],
                    "event_refs": ["flick:1"],
                }],
            },
            "timeline": [{
                "id": "flick:1",
                "event_type": "flick",
                "source": "raw_input",
                "relative_ms": 100.0,
            }],
        },
        artifact_manifest={
            "schema_version": "artifact_manifest.v2",
            "analysis_id": analysis_ref,
            "external_inputs": external_inputs,
            "owned_outputs": [{
                "id": analysis_ref,
                "kind": "analysis_result",
                "source": "analysis",
                "availability": "available",
                "ownership": "analysis",
                "managed": True,
                "local_only": True,
                "status": "available",
                "format_version": ANALYSIS_RESULT_V2_SCHEMA_VERSION,
                "derived_from": [item["id"] for item in external_inputs],
            }],
        },
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run_id,
            "scenario": "Scenario",
            "scenario_identity_version": "kovaak_scenario.v1",
            "sources": snapshot_sources,
            "trace": {"artifact_ref": trace_ref, "availability": "available"},
        },
        created_at="2026-07-15T00:00:00Z",
        completed_at="2026-07-15T00:00:01Z",
        warnings=[],
        errors=[],
    )


async def _seed_history_run(tmp_path: Path, *, user_id: str) -> tuple[dict, Path, Path]:
    stats = tmp_path / "private Stats.csv"
    stats.write_text("seed", encoding="utf-8")
    trace = tmp_path / "private-trace.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=f"history-{user_id}",
        scenario="Scenario",
        stats_path=str(stats),
        stats_summary={"source": kovaak_run_store._source_metadata(
            stats, kovaak_run_store.STATS_PARSER_VERSION,
        )},
        mouse_trace_path=str(trace),
    )
    return run, stats, trace


def _private_history_snapshot(run: dict, stats: Path, trace: Path) -> dict:
    stats_source = run["stats_summary"]["source"]
    trace_source = kovaak_run_store._source_metadata(
        trace,
        f"raw_input_snapshot.v{kovaak_run_store.SNAPSHOT_VERSION}",
    )
    return {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run["id"],
        "scenario": "Scenario",
        "scenario_identity_version": "kovaak_scenario.v1",
        "sources": {
            "stats": {
                "artifact_ref": kovaak_run_store.public_kovaak_run(run)[
                    "stats_source_ref"
                ],
                "path": str(stats.resolve()),
                "basename": stats.name,
                "fingerprint": {
                    key: stats_source[key] for key in ("sha256", "size", "mtime_ns")
                },
                "availability": "available",
            },
        },
        "trace": {
            "artifact_ref": f"run:{run['id']}:trace",
            "path": str(trace.resolve()),
            "fingerprint": {
                key: trace_source[key] for key in ("sha256", "size", "mtime_ns")
            },
            "availability": "available",
            "format_version": kovaak_run_store.SNAPSHOT_VERSION,
        },
    }


async def _seed_v2_history_analysis(
    tmp_path: Path,
    *,
    user_id: str,
    input_mode: str,
    include_mp4: bool,
    mp4_evidence_availability: str = "available",
    remove_video: bool = False,
) -> tuple[int, dict, Path, Path, Path]:
    run, stats, trace = await _seed_history_run(tmp_path, user_id=user_id)
    private_snapshot = _private_history_snapshot(run, stats, trace)
    sid = await queue.enqueue(
        user_id,
        "",
        "",
        input_mode=input_mode,
        kovaak_run_id=run["id"],
        input_snapshot=private_snapshot,
    )
    managed_dir = session_dir(sid)
    managed_dir.mkdir(parents=True, exist_ok=True)
    video = managed_dir / "video.mp4"
    video.write_bytes(b"0123456789")
    video_source = kovaak_run_store._source_metadata(video, "mp4")
    private_snapshot["sources"]["video"] = {
        "artifact_ref": f"analysis:{sid}:video",
        "path": str(video.resolve()),
        "basename": video.name,
        "fingerprint": {
            key: video_source[key] for key in ("sha256", "size", "mtime_ns")
        },
        "availability": "available",
        "format_version": "mp4",
    }
    result = _v2_history_result(
        session_id=sid,
        run_id=run["id"],
        input_mode=input_mode,
        include_mp4=include_mp4,
        mp4_evidence_availability=mp4_evidence_availability,
    )
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', video_path=?, input_snapshot_json=?, result=? "
        "WHERE id=?",
        (
            str(video),
            json.dumps(private_snapshot, ensure_ascii=False, separators=(",", ":")),
            dump_contract_json(result),
            sid,
        ),
    )
    await conn.commit()
    if remove_video:
        video.unlink()
    return sid, run, stats, trace, video


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
async def test_list_sessions_includes_path_free_scenario_from_input_snapshot():
    sid = await queue.enqueue(
        "u1",
        "/private/source/video.mp4",
        "/private/source/stats.csv",
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "scenario": "1wall 6targets small",
            "sources": {
                "stats": {"path": "/private/source/stats.csv"},
            },
        },
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    item = next(row for row in resp.json()["sessions"] if row["id"] == sid)
    assert item["scenario"] == "1wall 6targets small"
    assert "/private" not in str(item)


@pytest.mark.asyncio
async def test_queue_list_sessions_never_selects_full_result_blob(monkeypatch):
    sid = await queue.enqueue("u_light", "/private/video.mp4", "/private/stats.csv")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps({"sentinel": "FULL_RESULT_MUST_NOT_BE_READ"}), sid),
    )
    await conn.commit()

    class GuardedConnection:
        async def execute(self, sql: str, params=()):
            normalized = " ".join(sql.lower().split())
            if normalized.startswith("select ") and " from sessions" in normalized:
                selected = normalized.split(" from sessions", 1)[0].replace(",", " ")
                assert "result" not in selected.split(), sql
            return await conn.execute(sql, params)

    async def guarded_get_conn():
        return GuardedConnection()

    monkeypatch.setattr(queue, "get_conn", guarded_get_conn)
    rows = await queue.list_sessions("u_light")

    assert [row["id"] for row in rows] == [sid]


@pytest.mark.asyncio
async def test_analysis_list_exposes_light_source_trace_and_mode_read_model(
    monkeypatch,
    tmp_path: Path,
):
    user_id = "u_analysis_list"
    run, stats, trace = await _seed_history_run(tmp_path, user_id=user_id)
    sid = await queue.enqueue(
        user_id,
        "",
        "",
        input_mode="input_native",
        kovaak_run_id=run["id"],
        input_snapshot=_private_history_snapshot(run, stats, trace),
    )
    current_stats = tmp_path / "run-current Stats.csv"
    current_stats.write_text("current run revision", encoding="utf-8")
    current_trace = tmp_path / "run-current-trace.bin"
    kovaak_run_store.write_mouse_snapshot(current_trace, [
        {"timestamp_ms": 2_000, "dx": 3, "dy": 4, "buttons": 0},
    ])
    await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=run["source_key"],
        stats_path=str(current_stats),
        stats_summary={"source": kovaak_run_store._source_metadata(
            current_stats, kovaak_run_store.STATS_PARSER_VERSION,
        )},
        mouse_trace_path=str(current_trace),
    )
    await kovaak_run_store.mark_mouse_trace_unavailable(
        run["id"],
        user_id,
        "trace_snapshot_failed",
    )
    stats.write_text("changed source revision", encoding="utf-8")

    def reject_content_hashing(*_args, **_kwargs):
        raise AssertionError("Analysis list must not hash frozen source contents")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        with monkeypatch.context() as guarded:
            guarded.setattr(history_trends.hashlib, "sha256", reject_content_hashing)
            response = await client.get("/api/sessions")
        detail_response = await client.get(f"/api/sessions/{sid}")

    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["sessions"] if row["id"] == sid)
    assert item["analysis_ref"] == f"analysis:{sid}"
    assert item["run_ref"] == f"run:{run['id']}"
    assert item["input_mode"] == "input_native"
    assert item["source_availability"]["stats"] == "invalid"
    assert item["trace_quality"] == {
        "state": "attached",
        "availability": "available",
        "alignment_status": None,
        "coverage": None,
    }
    assert "result" not in item
    assert "history" not in item
    assert "evidence_refs" not in item
    assert "stats_source_ref" not in item
    assert "trace_artifact_ref" not in item
    assert str(stats) not in response.text
    assert str(trace) not in response.text
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()["history"]
    assert detail["source_availability"]["stats"] == "invalid"
    assert detail["trace_quality"] == item["trace_quality"]


@pytest.mark.asyncio
async def test_analysis_detail_treats_non_object_snapshot_as_unavailable():
    user_id = "u_non_object_snapshot"
    sid = await queue.enqueue(user_id, "", "")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET input_snapshot_json=? WHERE id=?",
        ("[1]", sid),
    )
    await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        response = await client.get(f"/api/sessions/{sid}")

    assert response.status_code == 200, response.text
    history = response.json()["history"]
    assert history["scenario"] is None
    assert history["source_availability"] == {}
    assert history["trace_quality"] == {
        "state": "none",
        "availability": "not_present",
        "alignment_status": None,
        "coverage": None,
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "file:///C:/Users/dot/private/Stats.csv",
        r"C:\Users\dot\private\Stats.csv",
        r"\\server\share\private\Stats.csv",
    ],
)
@pytest.mark.asyncio
async def test_history_scenario_rejects_file_uri_windows_and_unc_paths(scenario: str):
    sid = await queue.enqueue(
        "u_path_sentinel",
        "/private/video.mp4",
        "/private/stats.csv",
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "scenario": scenario,
        },
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_path_sentinel"},
    ) as client:
        response = await client.get("/api/sessions")

    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["sessions"] if row["id"] == sid)
    assert item["scenario"] is None
    assert scenario not in response.text


@pytest.mark.asyncio
async def test_analysis_detail_exposes_path_safe_history_locators_and_seekable_mp4(
    tmp_path: Path,
):
    user_id = "u_analysis_detail"
    sid, run, stats, trace, video = await _seed_v2_history_analysis(
        tmp_path,
        user_id=user_id,
        input_mode="multimodal",
        include_mp4=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        response = await client.get(f"/api/sessions/{sid}")

    assert response.status_code == 200, response.text
    history = response.json()["history"]
    assert history["analysis_ref"] == f"analysis:{sid}"
    assert history["run_ref"] == f"run:{run['id']}"
    assert history["source_availability"]["stats"] == "available"
    assert history["trace_quality"] == {
        "state": "attached",
        "availability": "available",
        "alignment_status": "aligned",
        "coverage": 1.0,
    }
    assert history["visual_replay"] == {
        "kind": "seekable_mp4",
        "available": True,
        "seekable": True,
        "endpoint": f"/api/sessions/{sid}/video",
        "artifact_ref": f"analysis:{sid}:video",
        "reason": None,
    }
    assert history["diagnosis_locator"] == {
        "analysis_ref": f"analysis:{sid}",
        "section": "diagnosis",
    }
    refs = {item["id"]: item for item in history["evidence_refs"]}
    assert refs[f"evidence:analysis:{sid}:raw_input"]["artifact_id"] == f"run:{run['id']}:trace"
    assert refs[f"evidence:analysis:{sid}:mp4"]["artifact_id"] == f"analysis:{sid}:video"
    allowed_ref_fields = {
        "id",
        "source",
        "artifact_id",
        "challenge_time_range_ms",
        "alignment_status",
        "availability",
        "local_only",
        "metric_keys",
    }
    assert all(set(item) <= allowed_ref_fields for item in history["evidence_refs"])
    assert str(stats) not in response.text
    assert str(trace) not in response.text
    assert str(video) not in response.text


@pytest.mark.asyncio
async def test_visual_evidence_failure_does_not_disable_managed_mp4_replay(
    tmp_path: Path,
):
    user_id = "u_visual_failure_replay"
    sid, _, _, _, _ = await _seed_v2_history_analysis(
        tmp_path,
        user_id=user_id,
        input_mode="multimodal",
        include_mp4=True,
        mp4_evidence_availability="unavailable",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        detail_response = await client.get(f"/api/sessions/{sid}")
        video_response = await client.get(f"/api/sessions/{sid}/video")

    assert detail_response.status_code == 200, detail_response.text
    history = detail_response.json()["history"]
    assert history["visual_replay"] == {
        "kind": "seekable_mp4",
        "available": True,
        "seekable": True,
        "endpoint": f"/api/sessions/{sid}/video",
        "artifact_ref": f"analysis:{sid}:video",
        "reason": None,
    }
    mp4_ref = next(
        ref for ref in history["evidence_refs"] if ref["source"] == "mp4"
    )
    assert mp4_ref["availability"] == "unavailable"
    assert video_response.status_code == 200


@pytest.mark.parametrize(
    (
        "input_mode", "include_mp4", "remove_video", "expected_kind",
        "expected_reason",
    ),
    [
        pytest.param(
            "input_native", True, False, "native_only",
            "input_native_has_no_visual_replay", id="native-only",
        ),
        pytest.param(
            "multimodal", True, True, "unavailable",
            "run_owned_video_unavailable", id="missing-mp4",
        ),
        pytest.param(
            "multimodal", False, False, "unavailable",
            "run_owned_video_unavailable", id="uncontracted-mp4",
        ),
    ],
)
@pytest.mark.asyncio
async def test_analysis_detail_replay_gate_rejects_non_seekable_cases(
    tmp_path: Path,
    input_mode: str,
    include_mp4: bool,
    remove_video: bool,
    expected_kind: str,
    expected_reason: str,
):
    user_id = f"u_replay_{expected_kind}_{include_mp4}_{remove_video}"
    sid, _, _, _, _ = await _seed_v2_history_analysis(
        tmp_path,
        user_id=user_id,
        input_mode=input_mode,
        include_mp4=include_mp4,
        remove_video=remove_video,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        detail = await client.get(f"/api/sessions/{sid}")
        video_response = await client.get(f"/api/sessions/{sid}/video")

    replay = detail.json()["history"]["visual_replay"]
    assert replay["kind"] == expected_kind
    assert replay["available"] is False
    assert replay["seekable"] is False
    assert replay["endpoint"] is None
    assert replay["reason"] is not None
    if input_mode == "input_native" or not include_mp4:
        assert replay["artifact_ref"] is None
    assert video_response.status_code == 410
    assert video_response.json() == {
        "schema_version": "managed_video_unavailable.v1",
        "availability": "unavailable",
        "reason": expected_reason,
    }


@pytest.mark.asyncio
async def test_history_trend_api_omits_values_for_insufficient_history():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_no_trend"},
    ) as client:
        response = await client.get("/api/history/trends/distance")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "comparable": False,
        "reason": "insufficient_history",
    }


@pytest.mark.asyncio
async def test_delete_session_removes_row_chat_and_preserves_source_files():
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
    assert body["files_removed"] == []
    assert body["cleanup_failed"] == []

    assert await queue.get_session(sid) is None
    history = await db.load_chat_history(sid)
    assert history == []

    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]

    assert video.read_bytes() == b"vid"
    assert csv.read_bytes() == b"csv"


@pytest.mark.asyncio
async def test_delete_terminal_analysis_preserves_run_owned_video(tmp_path: Path):
    user_id = "u_run_video_delete"
    stats = tmp_path / "Stats.csv"
    stats.write_bytes(b"stats")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key="run-video-delete",
        stats_path=str(stats),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
    )
    run_video = tmp_path / "runs" / str(run["id"]) / "video-owned.mp4"
    run_video.parent.mkdir(parents=True)
    run_video.write_bytes(b"run-owned-video")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET video_path=?, video_state='attached' WHERE id=?",
        (str(run_video.resolve()), run["id"]),
    )
    await conn.commit()

    sid = await queue.enqueue(
        user_id,
        str(run_video.resolve()),
        str(stats.resolve()),
        input_mode="video_fallback",
        kovaak_run_id=run["id"],
        input_snapshot={
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": run["id"],
            "sources": {
                "video": {
                    "path": str(run_video.resolve()),
                    "ownership": "run",
                },
            },
        },
    )
    analysis_workspace = session_dir(sid)
    analysis_workspace.mkdir(parents=True)
    analysis_video = analysis_workspace / "video.mp4"
    os.link(run_video, analysis_video)
    (analysis_workspace / "result.json").write_bytes(b"analysis-owned")
    await conn.execute(
        "UPDATE sessions SET status='done', video_path=? WHERE id=?",
        (str(analysis_video.resolve()), sid),
    )
    await conn.commit()
    assert analysis_video.samefile(run_video)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    ) as client:
        response = await client.delete(f"/api/sessions/{sid}")

    assert response.status_code == 200, response.text
    assert response.json()["files_removed"] == ["workspace"]
    assert run_video.read_bytes() == b"run-owned-video"
    assert not analysis_workspace.exists()
    persisted_run = await kovaak_run_store.get_kovaak_run(run["id"], user_id)
    assert persisted_run is not None
    assert persisted_run["video_path"] == str(run_video.resolve())


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
