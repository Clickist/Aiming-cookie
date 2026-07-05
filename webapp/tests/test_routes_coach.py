"""Tests for coach-page endpoints: /video, /timeline, and pinned_frame_sec on /chat."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import db, queue
from webapp.backend.app import app

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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions/99999/video")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_video_404_when_file_absent():
    """video_path 指向不存在的文件 → 404。"""
    sid = await _seed_done_session(video_path="/definitely/not/here.mp4")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_video_200_returns_mp4(tmp_path: Path):
    """video_path 指向真实文件 → FileResponse video/mp4。"""
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"FAKE_MP4_BYTES")
    sid = await _seed_done_session(video_path=str(fake_video))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{sid}/video")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"FAKE_MP4_BYTES"


# ---------------------------------------------------------------------------
# /timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions/99999/timeline")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_timeline_409_when_not_done():
    sid = await queue.enqueue("u1", "/v", "/c")  # status='queued'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_timeline_empty_events_when_no_markers():
    """无 result.timeline 字段 → events=[],但 fps/duration 从 meta 推。"""
    sid = await _seed_done_session(
        report=_fake_report_dict(fps=120, duration_frames=4372),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/sessions/{sid}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 2
    assert body["events"][0]["type"] == "peak"
    assert body["events"][1]["label"] == "脱靶"


@pytest.mark.asyncio
async def test_timeline_defaults_fps_60_when_meta_absent():
    sid = await _seed_done_session(report=_fake_report_dict())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    monkeypatch.setattr(routes_mod, "_load_backend_or_none", lambda: object())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    monkeypatch.setattr(routes_mod, "_load_backend_or_none", lambda: object())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
