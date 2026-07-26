"""Tests for coach-page endpoints: /video, /timeline, and pinned_frame_sec on /chat."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import db, provider_store, queue, training_plan_store
from webapp.backend.app import app
from webapp.backend.contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    build_analysis_result_v1,
    dump_contract_json,
)
from webapp.backend.workspace import session_dir


import webapp.backend.config as config_mod
import webapp.backend.coach_runtime as coach_runtime_mod
import webapp.backend.coach_engine as coach_engine_mod
import webapp.backend.routes as routes_mod
import kovaak_tracker.coach.agent as agent_mod


def _fake_report_dict(*, fps=None, duration_frames=None, timeline=None) -> dict:
    meta: dict = {"cm_per_360": 48.0}
    if fps is not None:
        meta["fps"] = fps
    if duration_frames is not None:
        meta["duration_frames"] = duration_frames
    report = {
        "diagnosis": {
            "profile": {
                "archetype_id": "decel_jitter",
                "label": "减速抖动型",
                "confidence": 1.0,
                "secondary_tags": [],
            },
            "issues": [],
            "summary": {},
            "comparison": None,
            "meta": meta,
        },
        "figures": {},
        "narration": "测试。",
        "notes": [],
    }
    if timeline is not None:
        report["timeline"] = timeline
    return report


async def _seed_default_provider(user_id: str = "u1") -> dict:
    return await provider_store.create_profile(user_id, {
        "name": "Selected Provider",
        "provider_id": "selected-provider",
        "kind": "custom_openai_compatible",
        "base_url": "https://selected.test/v1",
        "model_id": "selected-model",
        "api_key": "selected-secret-key",
        "is_default": True,
    })


async def _seed_done_session(
    *,
    video_path: str = "/nonexistent/video.mp4",
    report: dict | None = None,
) -> int:
    sid = await queue.enqueue("u1", video_path, "/c")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(report or _fake_report_dict()), sid),
    )
    await conn.commit()
    return sid


async def _seed_task11_plan(owner_id: str) -> dict:
    return await training_plan_store.generate_draft(
        owner_id,
        {
            "title": "Tracking speed match",
            "diagnostic_context": {
                "analysis_refs": ["analysis:5"],
                "metric_refs": ["metric:continuous_tracking.target_relative_error_px@v1"],
                "knowledge_refs": ["knowledge:tracking-speed-matching@1"],
            },
            "prescriptions": [{
                "scenario": "WHJ SmoothStrafeSphere Easy",
                "cue": "Match speed before correcting position.",
                "purpose": "Reduce target-relative error.",
                "target_metric_refs": ["metric:continuous_tracking.target_relative_error_px@v1"],
                "expected_direction": "lower_better",
                "source_level": "deterministic_rule",
            }],
        },
        evidence_refs=["analysis:5"],
        verification_targets=[{
            "target_metric": "metric:continuous_tracking.target_relative_error_px@v1",
            "expected_direction": "lower_better",
            "comparable_requirements": ["same scenario"],
            "retest_after": "after three sessions",
            "insufficient_evidence_behavior": "collect another comparable run",
        }],
    )


# ---------------------------------------------------------------------------
# /video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task11_plan_fact_routes_are_owner_scoped_and_idempotent():
    owner_id = "route-plan-owner"
    plan = await _seed_task11_plan(owner_id)
    headers = {"X-User-Id": owner_id}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        item = await client.post(
            f"/api/training-plans/{plan['plan_ref']}/items",
            headers={"Idempotency-Key": "route-item-1"},
            json={
                "plan_version": 1,
                "item_payload": {
                    "diagnosis_ref": "diagnosis:tracking-error@1",
                    "knowledge_ref": "knowledge:tracking-speed-matching@1",
                    "scenario_profile_ref": "scenario:tracking.whj@1",
                    "baseline_metric_ref": "metric:continuous_tracking.target_relative_error_px@v1",
                    "expected_direction": "lower_better",
                    "practice_condition": "Repeat the same scenario.",
                    "cue": "Match speed before correcting position.",
                    "dose_guardrail": "Stop after three degraded runs.",
                    "matched_retest_ref": "retest-spec:tracking-matched@1",
                    "near_transfer_retest_ref": "retest-spec:tracking-transfer@1",
                    "review_date": "2026-07-30",
                },
            },
        )
        assert item.status_code == 200, item.text
        item_ref = item.json()["result_ref"]
        replay = await client.post(
            f"/api/training-plans/{plan['plan_ref']}/items",
            headers={"Idempotency-Key": "route-item-1"},
            json=item.request.content and json.loads(item.request.content),
        )
        assert replay.status_code == 200
        assert replay.json()["result_ref"] == item_ref

        execution = await client.post(
            f"/api/training-plan-items/{item_ref}/executions",
            headers={"Idempotency-Key": "route-execution-1"},
            json={
                "scenario_ref": "scenario:tracking.whj@1",
                "run_refs": ["run:52207"],
                "planned_dose": {"amount": 3, "unit": "runs"},
                "completed_dose": {"amount": 2, "unit": "runs"},
                "completion_status": "partial",
                "user_feedback": "Fatigue increased.",
            },
        )
        assert execution.status_code == 200, execution.text

        retest = await client.post(
            f"/api/training-plan-items/{item_ref}/retests",
            headers={"Idempotency-Key": "route-retest-1"},
            json={
                "kind": "matched",
                "expected_metric_ref": "metric:continuous_tracking.target_relative_error_px@v1",
                "expected_direction": "lower_better",
                "analysis_refs": ["analysis:5"],
                "comparability": "comparable",
                "result": "improved",
                "limitations": ["one comparable retest"],
            },
        )
        assert retest.status_code == 200, retest.text

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "other-owner"},
    ) as other:
        forbidden = await other.post(
            f"/api/training-plan-items/{item_ref}/executions",
            headers={"Idempotency-Key": "route-execution-other"},
            json={
                "scenario_ref": "scenario:tracking.whj@1",
                "run_refs": ["run:52207"],
                "planned_dose": {"amount": 1, "unit": "runs"},
                "completed_dose": {"amount": 1, "unit": "runs"},
                "completion_status": "completed",
                "user_feedback": "other",
            },
        )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_done_session_context_includes_owner_scoped_active_plan_and_recent_retest():
    owner_id = "context-plan-owner"
    plan = await _seed_task11_plan(owner_id)
    await training_plan_store.save_plan(owner_id, plan["plan_ref"])
    await training_plan_store.activate_plan(owner_id, plan["plan_ref"])
    item = await training_plan_store.add_plan_item(
        owner_id,
        plan["plan_ref"],
        {
            "diagnosis_ref": "diagnosis:tracking-error@1",
            "knowledge_ref": "knowledge:tracking-speed-matching@1",
            "scenario_profile_ref": "scenario:tracking.whj@1",
            "baseline_metric_ref": "metric:continuous_tracking.target_relative_error_px@v1",
            "expected_direction": "lower_better",
            "practice_condition": "Repeat the same scenario.",
            "cue": "Match speed first.",
            "dose_guardrail": "Stop after three degraded runs.",
            "matched_retest_ref": "retest-spec:tracking-matched@1",
            "near_transfer_retest_ref": "retest-spec:tracking-transfer@1",
            "review_date": "2026-07-30",
        },
    )
    retest = await training_plan_store.record_retest(
        owner_id,
        item["item_ref"],
        kind="matched",
        expected_metric_ref="metric:continuous_tracking.target_relative_error_px@v1",
        expected_direction="lower_better",
        analysis_refs=["analysis:5"],
        comparability="comparable",
        result="improved",
        limitations=["one comparable retest"],
    )
    result = {
        "schema_version": "analysis_result.v2",
        "analysis_version": "continuous_tracking.v1",
        "analysis_id": "analysis:5",
        "analysis_type": "continuous_tracking",
        "input_mode": "multimodal",
        "scenario": {
            "scenario_profile_ref": "scenario:tracking.whj@1",
            "analyzer_refs": ["continuous_tracking.v1"],
            "support_status": "partial",
            "limitations": [],
        },
        "deterministic": {
            "support_status": "partial",
            "metrics": {"continuous_tracking.target_relative_error_px": {
                "value": 12.5,
                "unit": "px",
                "metric_version": "continuous_tracking.target_relative_error_px.v1",
                "classification": "deterministic",
                "provenance": {"kind": "derived", "sources": ["analysis:5:source:tracking"]},
            }},
            "diagnosis": {
                "profile": {},
                "issues": [],
                "summary": {"continuous_tracking.target_relative_error_px": {
                    "value": 12.5,
                    "unit": "px",
                    "metric_version": "continuous_tracking.target_relative_error_px.v1",
                    "classification": "deterministic",
                    "provenance": {"kind": "derived", "sources": ["analysis:5:source:tracking"]},
                }},
                "comparison": None,
                "meta": {"summary_type": "continuous_tracking", "classification": "deterministic"},
            },
        },
        "evidence": {
            "availability": {"mp4": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
            "derived_artifact": {
                "artifact_ref": "analysis:5:evidence:abc",
                "evidence_revision": "sha256:abc",
            },
        },
        "warnings": [],
    }

    context = await routes_mod._diagnosis_from_done_session({
        "id": 5,
        "user_id": owner_id,
        "result": result,
    })

    assert context["training"] == {
        "active_plan_ref": plan["plan_ref"],
        "recent_retest_ref": retest["retest_ref"],
    }


@pytest.mark.asyncio
async def test_video_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get("/api/sessions/99999/video")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_segment_route_returns_only_bounded_seek_contract(monkeypatch):
    sid = 42
    result = {
        "schema_version": "analysis_result.v2",
        "evidence": {
            "derived_artifact": {
                "artifact_ref": f"analysis:{sid}:evidence:abc",
                "evidence_revision": "sha256:" + ("a" * 64),
            },
        },
    }
    snapshot = {
        "canonical_time_window": {"start_ms": 1000, "end_ms": 7000},
    }

    async def get_owned_session(_session_id: int, _owner_id: str):
        return {
            "id": sid,
            "user_id": "u1",
            "status": "done",
            "input_mode": "multimodal",
            "video_path": "",
            "result": result,
            "input_snapshot": snapshot,
        }

    monkeypatch.setattr(routes_mod, "_get_owned_session", get_owned_session)
    async def read_artifact(**_kwargs):
        return {
            "evidence_segments": [{
                "segment_id": f"analysis:{sid}:segment:1",
                "analysis_ref": f"analysis:{sid}",
                "analyzer_ref": "continuous_tracking.v1",
                "segment_kind": "tracking_episode",
                "start_ms": 1500,
                "end_ms": 2500,
                "focus_start_ms": 1800,
                "focus_end_ms": 2200,
                "title_key": "tracking.error",
                "rank_reason": "largest error",
                "issue_refs": [],
                "metric_refs": ["continuous_tracking.target_relative_error_px"],
                "event_refs": [],
                "available_channels": [],
                "source_coverage": 1.0,
                "confidence": 0.9,
                "limitations": [],
            }],
        }

    monkeypatch.setattr(
        routes_mod.evidence_store,
        "read_analysis_evidence_artifact",
        read_artifact,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        response = await client.get(f"/api/sessions/{sid}/evidence-segments")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "frontend_evidence_segments.v1"
    assert body["video_availability"] == "unavailable"
    assert body["segments"][0]["playback"]["availability"] == "unavailable"
    assert body["segments"][0]["playback"]["relative_start_ms"] is None
    assert "path" not in json.dumps(body)


@pytest.mark.asyncio
async def test_video_returns_versioned_unavailable_when_file_absent():
    """A missing managed video is an unavailable capability, not an empty route."""
    sid = await _seed_done_session(video_path="/definitely/not/here.mp4")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 410
    assert resp.json()["schema_version"] == "managed_video_unavailable.v1"
    assert resp.json()["availability"] == "unavailable"
    assert "/definitely/not/here.mp4" not in resp.text


@pytest.mark.asyncio
async def test_video_200_returns_mp4():
    """Unversioned legacy video remains readable only from its managed workspace."""
    sid = await _seed_done_session(video_path="")
    managed_dir = session_dir(sid)
    managed_dir.mkdir(parents=True, exist_ok=True)
    fake_video = managed_dir / "video.mp4"
    fake_video.write_bytes(b"FAKE_MP4_BYTES")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET video_path=? WHERE id=?",
        (str(fake_video), sid),
    )
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"FAKE_MP4_BYTES"


@pytest.mark.asyncio
async def test_video_404_when_unversioned_legacy_path_is_outside_managed_workspace(
    tmp_path: Path,
):
    external_video = tmp_path / "external.mp4"
    external_video.write_bytes(b"EXTERNAL_VIDEO_MUST_NOT_BE_READ")
    sid = await _seed_done_session(video_path=str(external_video))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        response = await client.get(f"/api/sessions/{sid}/video")

    assert response.status_code == 410
    assert b"EXTERNAL_VIDEO_MUST_NOT_BE_READ" not in response.content


# ---------------------------------------------------------------------------
# /timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get("/api/sessions/99999/timeline")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_timeline_409_when_not_done():
    sid = await queue.enqueue("u1", "/v", "/c")  # status='queued'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_timeline_empty_events_when_no_markers():
    """无 result.timeline 字段 → events=[],但 fps/duration 从 meta 推。"""
    sid = await _seed_done_session(
        report=_fake_report_dict(fps=120, duration_frames=4372),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fps"] == 120
    assert body["duration_frames"] == 4372
    assert body["events"] == []


@pytest.mark.asyncio
async def test_timeline_returns_persisted_events():
    """result.timeline 字段存在 → events 透传。"""
    tl = [
        {"frame": 775, "time_s": 12.9, "type": "peak", "label": "速度峰值"},
        {"frame": 800, "time_s": 13.3, "type": "miss", "label": "脱靶"},
    ]
    sid = await _seed_done_session(
        report=_fake_report_dict(fps=60, duration_frames=2000, timeline=tl),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 2
    assert body["events"][0]["type"] == "peak"
    assert body["events"][1]["label"] == "脱靶"


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_timeline_accepts_v1_result_via_contract_adapter():
    """timeline 从 v1 deterministic.timeline 读取,不依赖顶层 timeline 字段。"""
    tl = [
        {"frame": 100, "time_s": 1.6, "type": "peak", "label": "v1 peak"},
    ]
    legacy_shape = _fake_report_dict(fps=60, duration_frames=500, timeline=tl)
    v1 = build_analysis_result_v1(
        report={
            "diagnosis": legacy_shape["diagnosis"],
            "figures": legacy_shape.get("figures") or {},
            "notes": legacy_shape.get("notes") or [],
            "narration": legacy_shape.get("narration"),
        },
        timeline=tl,
        narration_status="available",
        cm_per_360=48.0,
        fov=None,
        artifact_manifest={
            "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
            "inputs": [],
            "outputs": [],
        },
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )
    sid = await queue.enqueue("u1", "/v", "/c")
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
        resp = await client.get(f"/api/sessions/{sid}/timeline")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "peak"
    assert body["events"][0]["label"] == "v1 peak"


@pytest.mark.asyncio
async def test_timeline_defaults_fps_60_when_meta_absent():
    sid = await _seed_done_session(report=_fake_report_dict())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 200
    assert resp.json()["fps"] == 60


# ---------------------------------------------------------------------------
# /chat pinned_frame_sec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_pinned_frame_sec_prepended_to_message(monkeypatch):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")
    """pinned_frame_sec=23.4 → user message 存储为 "[锁定 0:23] 我的问题"。"""
    sid = await _seed_done_session()

    captured: list[str] = []

    def fake_chat(diagnosis, messages, backend, **kw):
        captured.append(messages[-1].content)
        return "回复"

    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "我的问题", "pinned_frame_sec": 23.4},
        )

    assert resp.status_code == 200, resp.text
    # agent 收到的最后一条 user message 带锁定前缀
    assert captured == ["[锁定 0:23] 我的问题"]
    # history 持久化的也是带前缀的版本
    body = resp.json()
    assert body["history"][0]["content"] == "[锁定 0:23] 我的问题"


@pytest.mark.asyncio
async def test_chat_without_pinned_frame_unchanged(monkeypatch):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")
    """不传 pinned_frame_sec → message 原样存储。"""
    sid = await _seed_done_session()

    def fake_chat(diagnosis, messages, backend, **kw):
        return "ok"

    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "普通问题"},
        )

    assert resp.status_code == 200
    assert resp.json()["history"][0]["content"] == "普通问题"


# ---------------------------------------------------------------------------
# /analyze 安全:user_id 校验 + 扩展名白名单(路径穿越防护)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_rejects_path_traversal_user_id():
    """X-User-Id 含 ../ → 400,不写文件不入队(防路径穿越)。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"fake", "video/mp4"),
                "csv": ("s.csv", b"fake", "text/csv"),
            },
            headers={"X-User-Id": "../../evil"},
        )
    assert resp.status_code == 400
    assert "非法" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_non_mp4_video_extension():
    """视频扩展名非白名单 → 400。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("evil.exe", b"fake", "application/octet-stream"),
                "csv": ("s.csv", b"fake", "text/csv"),
            },
            headers={"X-User-Id": "test_ext_user"},
        )
    assert resp.status_code == 400
    assert "mp4" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# IDOR 防护:跨用户访问他人 session → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idor_cross_user_forbidden_403():
    """u1 的 session,其他用户访问各端点 → 403(防 session_id 枚举读他人数据/花他人 budget)。

    v1 最小 ownership 校验(X-User-Id 自报,无签名);切片 3 换 Clerk 验签后此测试仍应成立。
    """
    sid = await _seed_done_session()  # owner = u1
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "intruder"},
    ) as client:
        assert (await client.get(f"/api/sessions/{sid}")).status_code == 403
        assert (await client.get(f"/api/sessions/{sid}/video")).status_code == 403
        assert (await client.get(f"/api/sessions/{sid}/timeline")).status_code == 403
        assert (await client.get(f"/api/sessions/{sid}/chat")).status_code == 403
        assert (await client.post(
            f"/api/sessions/{sid}/chat", json={"message": "hi"},
        )).status_code == 403


# ---------------------------------------------------------------------------
# /api/coach/primary (persistent coach)
# ---------------------------------------------------------------------------


def _patch_chat_ok(monkeypatch, fake_fn):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_fn)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())


@pytest.mark.asyncio
async def test_get_primary_lazy_creates_thread():
    """GET /api/coach/primary 惰性创建 primary thread。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "coach-user"},
    ) as client:
        resp = await client.get("/api/coach/primary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread"]["kind"] == "primary"
    assert body["thread"]["user_id"] == "coach-user"
    assert body["messages"] == []
    assert body["refs"] == []


@pytest.mark.asyncio
async def test_post_primary_message_without_analysis(monkeypatch):
    """无 analysis_session_id 也可聊；不注入指标诊断上下文。"""
    captured: dict = {}

    def fake_chat(diagnosis, messages, backend, **kwargs):
        captured["issues_len"] = len(diagnosis.issues)
        captured["summary"] = diagnosis.summary
        return "通用回复"

    _patch_chat_ok(monkeypatch, fake_chat)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "没分析也能问吗"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "通用回复"
    assert captured["issues_len"] == 0
    assert captured["summary"] == {}

    from webapp.backend import coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "没分析也能问吗"
    assert msgs[0]["legacy_session_id"] is None


@pytest.mark.asyncio
async def test_post_primary_message_with_done_analysis(monkeypatch):
    """POST message + done analysis(owner 匹配)注入诊断。"""
    sid = await _seed_done_session()

    captured: dict = {}

    def fake_chat(diagnosis, messages, backend, **kwargs):
        captured["profile"] = diagnosis.profile.archetype_id
        return "带诊断回复"

    _patch_chat_ok(monkeypatch, fake_chat)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "解读一下", "analysis_session_id": sid},
        )

    assert resp.status_code == 200, resp.text
    response_body = resp.json()
    assert response_body["reply"] == "带诊断回复"
    assert captured["profile"] == "decel_jitter"

    from webapp.backend import coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    refs = await coach_store.list_analysis_refs(int(thread["id"]))
    assert any(r["analysis_session_id"] == sid and r["status"] == "active" for r in refs)
    stored_messages = await coach_store.load_messages(int(thread["id"]))
    response_contexts = [message["context"] for message in response_body["messages"]]
    stored_contexts = [message["context"] for message in stored_messages[-2:]]
    assert response_contexts == stored_contexts
    assert response_contexts[0] == response_contexts[1]
    assert response_contexts[0]["schema_version"] == "coach_diagnostic_context.v1"
    assert response_contexts[0]["analysis_ref"]["analysis_id"] == f"analysis:{sid}"


@pytest.mark.asyncio
async def test_attach_analysis_idempotent():
    """POST attach 幂等。"""
    sid = await _seed_done_session()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        r1 = await client.post(
            "/api/coach/primary/attach",
            json={"analysis_session_id": sid},
        )
        r2 = await client.post(
            "/api/coach/primary/attach",
            json={"analysis_session_id": sid},
        )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["ref"]["analysis_session_id"] == sid
    assert r2.json()["ref"]["analysis_session_id"] == sid

    from webapp.backend import coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    refs = await coach_store.list_analysis_refs(int(thread["id"]))
    active = [r for r in refs if r["analysis_session_id"] == sid and r["status"] == "active"]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_deleted_analysis_not_active_context(monkeypatch):
    """已删除分析不可作为 active 上下文，但历史消息仍保留。"""
    sid = await _seed_done_session()
    _patch_chat_ok(monkeypatch, lambda *a, **k: "x")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        chat_resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "先保存这次分析", "analysis_session_id": sid},
        )
        assert chat_resp.status_code == 200, chat_resp.text
        await client.post(
            "/api/coach/primary/attach",
            json={"analysis_session_id": sid},
        )
        before_delete = await client.get("/api/coach/primary")
        del_resp = await client.delete(f"/api/sessions/{sid}")
        assert del_resp.status_code == 200
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "还能聊这次分析吗", "analysis_session_id": sid},
        )
        reattach = await client.post(
            "/api/coach/primary/attach",
            json={"analysis_session_id": sid},
        )
        detail = await client.get(f"/api/sessions/{sid}")
        video = await client.get(f"/api/sessions/{sid}/video")
        timeline = await client.get(f"/api/sessions/{sid}/timeline")
        primary = await client.get("/api/coach/primary")

    assert resp.status_code == 409
    assert reattach.status_code in {404, 409}
    assert detail.status_code == 404
    assert video.status_code == 404
    assert timeline.status_code == 404
    unavailable_refs = [
        r for r in primary.json()["refs"]
        if r["analysis_session_id"] == sid and r["status"] == "unavailable"
    ]
    assert len(unavailable_refs) == 1
    assert [m["content"] for m in primary.json()["messages"]] == [
        "先保存这次分析",
        "x",
    ]
    assert primary.json()["messages"] == before_delete.json()["messages"]
    contexts = [message["context"] for message in primary.json()["messages"]]
    assert contexts[0] == contexts[1]
    assert contexts[0]["schema_version"] == "coach_diagnostic_context.v1"
    assert contexts[0]["analysis_ref"]["analysis_id"] == f"analysis:{sid}"

# ---------------------------------------------------------------------------
# COACH_RUNTIME branching (coach_engine)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coach_runtime_python_uses_chat_with_coach(monkeypatch):
    """COACH_RUNTIME=python 时走 chat_with_coach，不调 run_pi_coach_turn。"""
    pi_calls: list[dict] = []
    chat_calls: list[int] = []

    def fake_pi(**kwargs):
        pi_calls.append(kwargs)
        return "pi"

    def fake_chat(diagnosis, messages, backend, **kwargs):
        chat_calls.append(len(messages))
        return "python 回复"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn", fake_pi)
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "python 路径"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "python 回复"
    assert chat_calls == [1]
    assert pi_calls == []


@pytest.mark.asyncio
async def test_coach_runtime_pi_uses_run_pi_coach_turn(monkeypatch):
    """COACH_RUNTIME=pi 时使用当前用户默认 profile。"""
    sid = await _seed_done_session()
    await _seed_default_provider()
    pi_calls: list[dict] = []
    chat_calls: list[int] = []

    async def fake_pi(**kwargs):
        pi_calls.append(kwargs)
        return coach_runtime_mod.PiCoachTurnResult("pi 教练回复", [], [])

    def fake_chat(diagnosis, messages, backend, **kwargs):
        chat_calls.append(1)
        return "不应调用"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn_async", fake_pi)
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "pi 路径", "analysis_session_id": sid},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "pi 教练回复"
    assert chat_calls == []
    assert len(pi_calls) == 1
    assert pi_calls[0]["user_id"] == "u1"
    assert pi_calls[0]["profile"] == {
        "profile_id": 1,
        "provider_id": "selected-provider",
        "provider_name": "Selected Provider",
        "kind": "custom_openai_compatible",
        "base_url": "https://selected.test/v1",
        "model_id": "selected-model",
        "credential": {"type": "api_key", "key": "selected-secret-key"},
    }
    assert pi_calls[0]["messages"][-1] == {
        "role": "user",
        "content": "pi 路径",
    }
    assert pi_calls[0]["analysis_summary"] is not None
    assert "decel_jitter" in pi_calls[0]["analysis_summary"] or "减速抖动" in (
        pi_calls[0]["analysis_summary"] or ""
    )

    from webapp.backend import coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "pi 教练回复"


@pytest.mark.asyncio
async def test_coach_runtime_pi_failure_fallback_python(monkeypatch):
    """pi 失败且 COACH_RUNTIME_FALLBACK_PYTHON=1 时回退同一 selected profile。"""
    from webapp.backend.coach_runtime import CoachRuntimeError

    await _seed_default_provider()

    async def fake_pi(**kwargs):
        raise CoachRuntimeError("mock pi down")

    def fake_chat(diagnosis, messages, backend, **kwargs):
        return "fallback 回复"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config_mod, "COACH_RUNTIME_FALLBACK_PYTHON", "1")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn_async", fake_pi)
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_for_profile", lambda profile: object())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "需要 fallback"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "fallback 回复"
    assert any("Pi coach-runtime" in n or "pi" in n.lower() for n in body["notes"])
    assert any("回退" in n for n in body["notes"])


@pytest.mark.asyncio
async def test_coach_runtime_pi_failure_no_fallback(monkeypatch):
    """pi 失败且 fallback=0 时不调 python，notes 记失败。"""
    from webapp.backend.coach_runtime import CoachRuntimeError

    await _seed_default_provider()

    chat_calls: list[int] = []

    async def fake_pi(**kwargs):
        raise CoachRuntimeError("mock pi down")

    def fake_chat(diagnosis, messages, backend, **kwargs):
        chat_calls.append(1)
        return "不应调用"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config_mod, "COACH_RUNTIME_FALLBACK_PYTHON", "0")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn_async", fake_pi)
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "无 fallback"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] is None
    assert chat_calls == []
    assert any("Pi coach-runtime" in n or "mock pi" in n for n in body["notes"])
    assert not any("回退" in n for n in body["notes"])


@pytest.mark.asyncio
async def test_coach_runtime_pi_without_default_profile_is_recoverably_unconfigured(monkeypatch):
    pi_calls: list[dict] = []

    async def fake_pi(**kwargs):
        pi_calls.append(kwargs)
        return coach_runtime_mod.PiCoachTurnResult("must not run", [], [])

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn_async", fake_pi)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "unconfigured-user"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "我还没配置 provider"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] is None
    assert any("未配置" in note for note in body["notes"])
    assert pi_calls == []


@pytest.mark.asyncio
async def test_selected_profile_secret_does_not_enter_coach_messages_or_context(monkeypatch):
    await _seed_default_provider("secret-user")

    async def fake_pi(**kwargs):
        assert kwargs["profile"]["credential"]["key"] == "selected-secret-key"
        return coach_runtime_mod.PiCoachTurnResult("安全回复", [], [])

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn_async", fake_pi)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "secret-user"},
    ) as client:
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "请给建议"},
        )

    assert resp.status_code == 200, resp.text
    assert "selected-secret-key" not in resp.text
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT content, context_json, trace_json FROM coach_messages "
        "WHERE thread_id=(SELECT id FROM coach_threads WHERE user_id=?)",
        ("secret-user",),
    )
    persisted = [dict(row) for row in await cur.fetchall()]
    assert persisted
    assert "selected-secret-key" not in json.dumps(persisted, ensure_ascii=False)
