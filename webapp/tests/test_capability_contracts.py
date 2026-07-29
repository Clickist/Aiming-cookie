from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend.read_models import (
    build_frontend_analysis_data_v1,
    build_capture_status_v1,
    build_product_state_v1,
    build_task_detail_v1,
    build_task_list_v1,
    resolve_calibration_v1,
)
from webapp.backend import db, queue
from webapp.backend.app import app
from webapp.backend.kovaak_run_store import public_analysis_input_snapshot
import webapp.backend.routes as routes_mod


def _tracking_artifact_for_data_projection(analysis_ref: str, *, sample_count: int = 160) -> dict:
    points = list(range(sample_count))
    return {
        "analysis_ref": analysis_ref,
        "canonical_time_window": {"start_ms": 1_000, "end_ms": 1_000 + sample_count},
        "event_bundles": [{
            "events": [{
                "event_id": f"{analysis_ref}:event:{index}",
                "event_kind": "target_change_point",
                "start_ms": 1_000 + index,
            } for index in points],
        }],
        "signal_bundles": [{
            "channels": [{
                "channel_key": key,
                "samples_ref": f"{analysis_ref}:samples:{key}",
            } for key in (
                "crosshair.position_x",
                "crosshair.position_y",
                "target.1.position_x",
                "target.1.position_y",
                "target.1.visible_radius",
            )],
        }],
        "sample_sets": [
            {
                "sample_set_id": f"{analysis_ref}:samples:{key}",
                "points": [[1_000 + index, value] for index in points],
            }
            for key, value in (
                ("crosshair.position_x", 0),
                ("crosshair.position_y", 0),
                ("target.1.position_x", 10),
                ("target.1.position_y", 0),
                ("target.1.visible_radius", 10),
            )
        ],
        "limitations": ["visual_quality_limited"],
        "raw_trace": [{"x": 0, "y": 0}],
        "artifact_path": "C:\\private\\artifact.json",
        "secret": "not-for-frontend",
    }


def test_frontend_analysis_data_projection_is_bounded_and_irreversible():
    projection = build_frontend_analysis_data_v1(
        analysis_ref="analysis:71",
        artifact=_tracking_artifact_for_data_projection("analysis:71"),
    )

    assert projection["schema_version"] == "frontend_analysis_data.v1"
    assert projection["analysis_ref"] == "analysis:71"
    assert len(projection["event_markers"]) == 128
    assert projection["event_distribution"] == [
        {"kind": "target_change_point", "count": 160},
    ]
    assert all(
        set(marker) == {"event_ref", "kind", "relative_ms"}
        for marker in projection["event_markers"]
    )
    error_radius = projection["target_relative_error_radius"]
    assert error_radius["availability"] == "available"
    assert error_radius["reason"] is None
    assert len(error_radius["points"]) == 120
    assert all(
        set(point) == {"relative_ms", "normalized_error_radius"}
        and point["normalized_error_radius"] == 1.0
        for point in error_radius["points"]
    )
    serialized = json.dumps(projection)
    assert "raw_trace" not in serialized
    assert "artifact_path" not in serialized
    assert "C:\\private" not in serialized
    assert "not-for-frontend" not in serialized

    without_radius = _tracking_artifact_for_data_projection("analysis:71", sample_count=3)
    without_radius["signal_bundles"][0]["channels"] = without_radius["signal_bundles"][0]["channels"][:-1]
    unavailable = build_frontend_analysis_data_v1(
        analysis_ref="analysis:71",
        artifact=without_radius,
    )["target_relative_error_radius"]
    assert unavailable == {
        "availability": "unavailable",
        "reason": "target_relative_channels_unavailable",
        "points": [],
    }


@pytest.mark.asyncio
async def test_frontend_analysis_data_route_reads_only_the_owned_committed_revision(monkeypatch):
    session_id = await queue.enqueue("data-owner", "", "")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps({
            "evidence": {"derived_artifact": {
                "artifact_ref": f"analysis:{session_id}:evidence:fixture",
                "evidence_revision": "sha256:fixture",
            }},
        }), session_id),
    )
    await conn.commit()

    async def read_artifact(**kwargs):
        assert kwargs == {
            "owner_id": "data-owner",
            "analysis_ref": f"analysis:{session_id}",
            "artifact_ref": f"analysis:{session_id}:evidence:fixture",
            "evidence_revision": "sha256:fixture",
        }
        return _tracking_artifact_for_data_projection(f"analysis:{session_id}", sample_count=3)

    monkeypatch.setattr(routes_mod.evidence_store, "read_analysis_evidence_artifact", read_artifact)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.get(
            f"/api/sessions/{session_id}/analysis-data",
            headers={"X-User-Id": "data-owner"},
        )
        forbidden = await client.get(
            f"/api/sessions/{session_id}/analysis-data",
            headers={"X-User-Id": "other-owner"},
        )

    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["analysis_ref"] == f"analysis:{session_id}"
    assert "artifact_ref" not in allowed.text
    assert forbidden.status_code == 403


def test_product_state_distinguishes_empty_from_read_failure_and_skip_is_complete():
    empty = build_product_state_v1(
        onboarding_completed=True,
        onboarding_completion_kind="skipped",
        has_pending_runs=False,
        has_runs=False,
        has_analyses=False,
    )
    assert empty["schema_version"] == "product_state.v1"
    assert empty["availability"] == "available"
    assert empty["onboarding_completed"] is True
    assert empty["onboarding_completion_kind"] == "skipped"
    assert empty["has_runs"] is False

    failed = build_product_state_v1(read_error="database_unavailable")
    assert failed["availability"] == "unavailable"
    assert failed["error"]["code"] == "database_unavailable"
    assert failed["has_runs"] is None
    assert failed["onboarding_completed"] is None
    assert failed["has_runs"] != empty["has_runs"]


def test_capture_status_is_pause_fail_closed_and_projects_run_attachments():
    result = build_capture_status_v1(
        native_status={
            "enabled": True,
            "phase": "capturing",
            "kovaakProcessPresent": True,
            "raw": {"state": "capturing", "reason": None},
            "video": {"state": "capturing", "reason": None},
        },
        runs=[
            {
                "run_ref": "run:7",
                "trace_state": "unavailable",
                "video_artifact_ref": None,
                "alignment": {"state": "unavailable", "error_code": "pause_unsupported"},
                "finalization_state": "finalized",
            },
            {
                "run_ref": "run:8",
                "trace_state": "attached",
                "video_artifact_ref": "run:8:video:abc",
                "alignment": {"state": "resolved"},
                "finalization_state": "finalized",
            },
        ],
    )
    assert result["schema_version"] == "capture_status.v1"
    assert result["capture_enabled"] is True
    assert result["replay_buffer_active"] is True
    assert result["raw_input_permission"] == "granted"
    assert result["pause_fail_closed"] is True
    assert result["pause_state"] == "fail_closed"
    assert result["runs"] == [
        {"run_ref": "run:7", "raw_attached": False, "video_attached": False},
        {"run_ref": "run:8", "raw_attached": True, "video_attached": True},
    ]


def test_tasks_expose_real_phases_failure_domains_partial_and_attempt_history():
    failed = {
        "id": 12,
        "task_group_ref": "task:12",
        "status": "failed",
        "task_phase": "analyzing_video",
        "failure_domain": "video",
        "error": json.dumps({
            "schema_version": "error.v1",
            "code": "video_cv_unavailable",
            "message": "video unavailable",
            "retryable": True,
            "trace_id": "hidden-trace",
            "details": {"path": "C:/private/clip.mp4"},
        }),
        "input_mode": "multimodal",
        "attempt_number": 1,
        "created_at": "2026-07-25T01:00:00Z",
    }
    retry = {
        **failed,
        "id": 13,
        "status": "running",
        "task_phase": "computing_kinematics",
        "attempt_number": 2,
        "parent_session_id": 12,
        "error": None,
    }
    task = build_task_detail_v1(
        retry,
        attempts=[failed, retry],
    )
    assert task["schema_version"] == "task_detail.v1"
    assert task["state"] == "running"
    assert task["phase"] == "computing_kinematics"
    assert task["can_delete"] is False
    assert [item["attempt_number"] for item in task["attempt_history"]] == [1, 2]
    assert task["attempt_history"][0]["failure"]["domain"] == "video"
    assert "hidden-trace" not in json.dumps(task)
    assert "C:/private" not in json.dumps(task)

    partial = build_task_detail_v1({
        "id": 14,
        "task_group_ref": "task:14",
        "status": "done",
        "input_mode": "multimodal",
        "attempt_number": 1,
        "result": {
            "schema_version": "analysis_result.v2",
            "evidence": {"availability": {"raw_input": "available", "mp4": "unavailable"}},
            "deterministic": {"status": "available"},
        },
    })
    assert partial["state"] == "done"
    assert partial["partial_outcome"] == {
        "status": "partial",
        "native_preserved": True,
        "visual_status": "unavailable",
        "reason_code": "video_unavailable",
    }
    assert partial["can_delete"] is True

    native = build_task_list_v1([{
        "id": 15,
        "task_group_ref": "task:15",
        "status": "running",
        "input_mode": "input_native",
    }])
    assert native["tasks"][0]["phase"] != "analyzing_video"


@pytest.mark.parametrize(
    ("stats", "manual", "profile", "expected_source", "expected_value"),
    [
        ({"cm_per_360": 34.0, "fov": 103.0}, {"cm_per_360": 40.0, "fov": 90.0}, {"cm_per_360": 50.0, "fov": 80.0}, "stats", 34.0),
        ({"cm_per_360": None, "fov": 103.0}, {"cm_per_360": 40.0, "fov": 90.0}, {"cm_per_360": 50.0, "fov": 80.0}, "manual_override", 40.0),
        ({"cm_per_360": None, "fov": None}, {"cm_per_360": None, "fov": 90.0}, {"cm_per_360": 50.0, "fov": 80.0}, "profile_default", 50.0),
        ({"cm_per_360": None, "fov": None}, None, None, "undetermined", None),
    ],
)
def test_calibration_selection_is_stats_then_override_then_profile_then_undetermined(
    stats, manual, profile, expected_source, expected_value,
):
    selected = resolve_calibration_v1(
        stats=stats,
        manual_override=manual,
        profile_default=profile,
    )
    assert selected["cm_per_360"]["source"] == expected_source
    assert selected["cm_per_360"]["value"] == expected_value
    assert set(selected) == {"cm_per_360", "fov"}
    assert all("path" not in json.dumps(value) for value in selected.values())


@pytest.mark.asyncio
async def test_retry_creates_new_attempt_without_overwriting_failed_row(tmp_path):
    video = tmp_path / "clip.mp4"
    stats = tmp_path / "stats.csv"
    video.write_bytes(b"video")
    stats.write_text("stats", encoding="utf-8")
    original_id = await queue.enqueue("retry-owner", str(video), str(stats))
    await queue.claim_next("contract-test-worker")
    await queue.mark_failed(
        original_id,
        {
            "schema_version": "error.v1",
            "category": "internal_unknown",
            "code": "analysis_failed",
            "message": "failed",
            "retryable": True,
            "trace_id": None,
        },
        worker_id="contract-test-worker",
        failure_domain="kinematics",
    )
    retried = await queue.requeue_for_retry(original_id)
    assert retried["id"] != original_id
    assert retried["status"] == "queued"
    assert retried["task_state"] == "retrying"
    assert retried["video_path"] != str(video)
    assert retried["csv_path"] != str(stats)
    assert retried["attempt_number"] == 2
    original = await queue.get_session(original_id)
    assert original["status"] == "failed"
    assert original["error"]["code"] == "analysis_failed"


def test_analysis_input_snapshot_freezes_selected_calibration_without_paths():
    snapshot = public_analysis_input_snapshot({
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 4,
        "sources": {
            "stats": {
                "path": "C:/private/Stats.csv",
                "artifact_ref": "run:4:stats:abc",
                "availability": "available",
            },
        },
        "trace": None,
        "calibration": {
            "cm_per_360": {"value": 34.0, "source": "stats"},
            "fov": {"value": 90.0, "source": "manual_override"},
        },
    })
    assert snapshot["calibration"] == {
        "cm_per_360": {"value": 34.0, "source": "stats"},
        "fov": {"value": 90.0, "source": "manual_override"},
    }
    assert "C:/private" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_product_state_route_persists_explicit_skip_and_reports_read_failure(monkeypatch):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "product-owner"},
    ) as client:
        empty = await client.get("/api/product-state")
        skipped = await client.post(
            "/api/product-state/onboarding",
            json={"completed": True, "completion_kind": "skipped"},
        )
    assert empty.json()["onboarding_completed"] is False
    assert skipped.json()["onboarding_completed"] is True
    assert skipped.json()["onboarding_completion_kind"] == "skipped"

    async def fail_read(_owner: str):
        raise RuntimeError("private database error")

    monkeypatch.setattr(queue, "get_product_state", fail_read)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "product-owner"},
    ) as client:
        failed = await client.get("/api/product-state")
    assert failed.status_code == 200
    assert failed.json()["availability"] == "unavailable"
    assert failed.json()["has_runs"] is None


@pytest.mark.asyncio
async def test_product_state_onboarding_write_failure_is_versioned_unavailable(monkeypatch):
    async def fail_write(*_args, **_kwargs):
        raise RuntimeError("private database error")

    monkeypatch.setattr(queue, "set_onboarding_state", fail_write)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "product-owner"},
    ) as client:
        response = await client.post(
            "/api/product-state/onboarding",
            json={"completed": True, "completion_kind": "skipped"},
        )

    assert response.status_code == 200
    assert response.json()["schema_version"] == "product_state.v1"
    assert response.json()["availability"] == "unavailable"
    assert response.json()["onboarding_completed"] is None


@pytest.mark.asyncio
async def test_product_state_onboarding_value_error_remains_client_error(monkeypatch):
    async def reject_write(*_args, **_kwargs):
        raise ValueError("invalid onboarding completion kind")

    monkeypatch.setattr(queue, "set_onboarding_state", reject_write)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "product-owner"},
    ) as client:
        response = await client.post(
            "/api/product-state/onboarding",
            json={"completed": True, "completion_kind": "connected"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_task_detail_read_failure_is_versioned_unavailable(monkeypatch):
    async def fail_read(*_args, **_kwargs):
        raise RuntimeError("private database error")

    monkeypatch.setattr(queue, "get_task_rows", fail_read)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "task-owner"},
    ) as client:
        response = await client.get("/api/tasks/task:1")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "task_detail.v1"
    assert response.json()["availability"] == "unavailable"
    assert response.json()["task_ref"] is None


@pytest.mark.asyncio
async def test_task_detail_missing_task_remains_not_found():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "task-owner"},
    ) as client:
        response = await client.get("/api/tasks/task:does-not-exist")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_tasks_route_groups_attempts_and_never_returns_input_paths(tmp_path):
    video = tmp_path / "route.mp4"
    stats = tmp_path / "route.csv"
    video.write_bytes(b"video")
    stats.write_text("stats", encoding="utf-8")
    original_id = await queue.enqueue("task-owner", str(video), str(stats))
    await queue.claim_next("task-route-worker")
    await queue.mark_failed(
        original_id,
        "failed",
        worker_id="task-route-worker",
        failure_domain="kinematics",
    )
    retry = await queue.requeue_for_retry(original_id)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "task-owner"},
    ) as client:
        response = await client.get("/api/tasks")
    assert response.status_code == 200
    body = response.json()
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["state"] == "retrying"
    assert [item["attempt_number"] for item in body["tasks"][0]["attempt_history"]] == [1, 2]
    assert f"analysis:{retry['id']}" == body["tasks"][0]["analysis_ref"]
    assert str(tmp_path) not in json.dumps(body)
