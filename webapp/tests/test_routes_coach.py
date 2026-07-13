"""Tests for coach-page endpoints: /video, /timeline, and pinned_frame_sec on /chat."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import db, queue
from webapp.backend.app import app
from webapp.backend.contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    build_analysis_result_v1,
    dump_contract_json,
)


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


# ---------------------------------------------------------------------------
# /video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get("/api/sessions/99999/video")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_video_404_when_file_absent():
    """video_path 指向不存在的文件 → 404。"""
    sid = await _seed_done_session(video_path="/definitely/not/here.mp4")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_video_200_returns_mp4(tmp_path: Path):
    """video_path 指向真实文件 → FileResponse video/mp4。"""
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"FAKE_MP4_BYTES")
    sid = await _seed_done_session(video_path=str(fake_video))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"FAKE_MP4_BYTES"


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
    assert resp.json()["reply"] == "带诊断回复"
    assert captured["profile"] == "decel_jitter"

    from webapp.backend import coach_store

    thread = await coach_store.get_or_create_primary_thread("u1")
    refs = await coach_store.list_analysis_refs(int(thread["id"]))
    assert any(r["analysis_session_id"] == sid and r["status"] == "active" for r in refs)


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
        del_resp = await client.delete(f"/api/sessions/{sid}")
        assert del_resp.status_code == 200
        resp = await client.post(
            "/api/coach/primary/messages",
            json={"content": "还能聊这次分析吗", "analysis_session_id": sid},
        )
        primary = await client.get("/api/coach/primary")

    assert resp.status_code == 409
    deleted_refs = [
        r for r in primary.json()["refs"]
        if r["analysis_session_id"] == sid and r["status"] == "deleted"
    ]
    assert len(deleted_refs) == 1
    assert [m["content"] for m in primary.json()["messages"]] == [
        "先保存这次分析",
        "x",
    ]

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
    """COACH_RUNTIME=pi 时调 run_pi_coach_turn，不调 chat_with_coach。"""
    sid = await _seed_done_session()
    pi_calls: list[dict] = []
    chat_calls: list[int] = []

    def fake_pi(**kwargs):
        pi_calls.append(kwargs)
        return "pi 教练回复"

    def fake_chat(diagnosis, messages, backend, **kwargs):
        chat_calls.append(1)
        return "不应调用"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn", fake_pi)
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
    """pi 失败且 COACH_RUNTIME_FALLBACK_PYTHON=1 时回退 python。"""
    from webapp.backend.coach_runtime import CoachRuntimeError

    def fake_pi(**kwargs):
        raise CoachRuntimeError("mock pi down")

    def fake_chat(diagnosis, messages, backend, **kwargs):
        return "fallback 回复"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config_mod, "COACH_RUNTIME_FALLBACK_PYTHON", "1")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn", fake_pi)
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_chat)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())

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

    chat_calls: list[int] = []

    def fake_pi(**kwargs):
        raise CoachRuntimeError("mock pi down")

    def fake_chat(diagnosis, messages, backend, **kwargs):
        chat_calls.append(1)
        return "不应调用"

    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config_mod, "COACH_RUNTIME_FALLBACK_PYTHON", "0")
    monkeypatch.setattr(coach_runtime_mod, "run_pi_coach_turn", fake_pi)
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
