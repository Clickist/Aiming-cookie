"""Tests for /api/sessions/{id}/chat endpoints."""
from __future__ import annotations

import json
from typing import Any

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
import webapp.backend.coach_engine as coach_engine_mod
import webapp.backend.routes as routes_mod
import kovaak_tracker.coach.agent as agent_mod


# ---------------------------------------------------------------------------
# Fake CoachReport payload stored in sessions.result (mirrors worker.run_report
# output: dataclasses.asdict(CoachReport) → dict with "diagnosis" key)
# ---------------------------------------------------------------------------


def _fake_report_dict() -> dict:
    return {
        "diagnosis": {
            "profile": {
                "archetype_id": "decel_jitter",
                "label": "减速抖动型",
                "confidence": 1.0,
                "secondary_tags": [],
            },
            "issues": [{
                "signal": "sparc low",
                "severity": "fix",
                "priority": 1,
                "priority_reason": "top",
                "root_causes": [{"level": "symptom", "text": "减速段抖动"}],
                "prescriptions": [],
            }],
            "summary": {"sparc": {"med": -7.0}},
            "comparison": None,
            "meta": {"cm_per_360": 48.0},
        },
        "figures": {},
        "narration": "你是减速抖动型。",
        "notes": [],
    }


def _minimal_manifest() -> dict:
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "inputs": [],
        "outputs": [],
    }


def _v1_result_from_legacy_report(legacy: dict) -> dict:
    return build_analysis_result_v1(
        report={
            "diagnosis": legacy["diagnosis"],
            "figures": legacy.get("figures") or {},
            "notes": legacy.get("notes") or [],
            "narration": legacy.get("narration"),
        },
        timeline=list(legacy.get("timeline") or []),
        narration_status="available" if legacy.get("narration") else "not_requested",
        cm_per_360=48.0,
        fov=None,
        artifact_manifest=_minimal_manifest(),
        created_at="2026-07-10T12:00:00Z",
        completed_at="2026-07-10T12:01:00Z",
    )


async def _seed_done_session(user_id: str = "u1") -> int:
    """直接写 sessions 表造一个 status=done 的 session。"""
    sid = await queue.enqueue(user_id, "/v", "/c")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(_fake_report_dict()), sid),
    )
    await conn.commit()
    return sid


async def _seed_done_session_v1(user_id: str = "u1") -> int:
    legacy = _fake_report_dict()
    v1 = _v1_result_from_legacy_report(legacy)
    sid = await queue.enqueue(user_id, "/v", "/c")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (dump_contract_json(v1), sid),
    )
    await conn.commit()
    return sid


def _patch_chat_ok(monkeypatch, fake_fn):
    """统一 patch:chat_with_coach 用 fake,_load_backend_or_none 返回非 None。"""
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")
    monkeypatch.setattr(agent_mod, "chat_with_coach", fake_fn)
    monkeypatch.setattr(coach_engine_mod, "load_backend_or_none", lambda: object())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            "/api/sessions/99999/chat",
            json={"message": "你好"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_409_when_session_not_done():
    sid = await queue.enqueue("u1", "/v", "/c")  # status='queued'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "你好"},
        )
    assert resp.status_code == 409
    assert "未完成" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_chat_404_when_session_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.get("/api/sessions/99999/chat")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_400_on_empty_message():
    sid = await _seed_done_session()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "   "},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_chat_200_with_mocked_backend(monkeypatch):
    """正常对话:mock chat_with_coach 返回固定 reply,验证 user+assistant 持久化。"""
    sid = await _seed_done_session()

    captured: dict[str, Any] = {}

    def fake_chat_with_coach(diagnosis, messages, backend, **kwargs):
        captured["diagnosis_profile"] = diagnosis.profile.archetype_id
        captured["messages_count"] = len(messages)
        captured["last_msg"] = messages[-1].content
        return "测试回复:多练 pasu。"

    _patch_chat_ok(monkeypatch, fake_chat_with_coach)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "我该怎么练?"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "测试回复:多练 pasu。"
    # history 应含本轮 user + assistant(共 2 条,因 session 是新的)
    assert len(body["history"]) == 2
    assert body["history"][0]["role"] == "user"
    assert body["history"][0]["content"] == "我该怎么练?"
    assert body["history"][1]["role"] == "assistant"
    assert body["history"][1]["content"] == "测试回复:多练 pasu。"

    # 验证 chat_with_coach 收到了正确的 diagnosis + history
    assert captured["diagnosis_profile"] == "decel_jitter"
    assert captured["last_msg"] == "我该怎么练?"
    assert captured["messages_count"] == 1  # 只有本轮(无历史)


@pytest.mark.asyncio
async def test_chat_accepts_v1_result_via_contract_adapter(monkeypatch):
    """sessions.result 存 AnalysisResult v1 时 chat 仍能通过 adapter 拿到 diagnosis。"""
    sid = await _seed_done_session_v1()

    captured: dict[str, Any] = {}

    def fake_chat_with_coach(diagnosis, messages, backend, **kwargs):
        captured["diagnosis_profile"] = diagnosis.profile.archetype_id
        return "v1 ok"

    _patch_chat_ok(monkeypatch, fake_chat_with_coach)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "hello"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "v1 ok"
    assert captured["diagnosis_profile"] == "decel_jitter"


@pytest.mark.asyncio
async def test_chat_history_persists_across_calls(monkeypatch):
    """连续两次 POST 后 GET,应拉到完整 4 条对话。"""
    sid = await _seed_done_session()

    replies = iter(["第一条回复", "第二条回复"])

    def fake_chat_with_coach(diagnosis, messages, backend, **kwargs):
        return next(replies)

    _patch_chat_ok(monkeypatch, fake_chat_with_coach)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        await client.post(f"/api/sessions/{sid}/chat",
                          json={"message": "第一个问题"})
        await client.post(f"/api/sessions/{sid}/chat",
                          json={"message": "第二个问题"})
        resp = await client.get(f"/api/sessions/{sid}/chat")

    assert resp.status_code == 200
    body = resp.json()
    # 4 条:user, assistant, user, assistant
    assert len(body["history"]) == 4
    assert body["history"][0]["content"] == "第一个问题"
    assert body["history"][1]["content"] == "第一条回复"
    assert body["history"][2]["content"] == "第二个问题"
    assert body["history"][3]["content"] == "第二条回复"


@pytest.mark.asyncio
async def test_chat_history_passed_to_agent_on_second_turn(monkeypatch):
    """第二次对话时,history 必须传给 chat_with_coach(验证多轮上下文)。"""
    sid = await _seed_done_session()

    captured_history_lengths: list[int] = []

    def fake_chat_with_coach(diagnosis, messages, backend, **kwargs):
        captured_history_lengths.append(len(messages))
        return f"回复 {len(messages)}"

    _patch_chat_ok(monkeypatch, fake_chat_with_coach)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        await client.post(f"/api/sessions/{sid}/chat",
                          json={"message": "第一轮"})
        await client.post(f"/api/sessions/{sid}/chat",
                          json={"message": "第二轮"})

    # 第一次:历史 0 条 + 本轮 1 条 = 1;第二次:历史 2 条 + 本轮 1 条 = 3
    assert captured_history_lengths == [1, 3]


@pytest.mark.asyncio
async def test_chat_backend_none_degrades_gracefully(monkeypatch):
    """load_backend 失败 → reply=None,notes 含降级提示,但 user 消息已存。"""
    sid = await _seed_done_session()

    def fake_load_backend_fail(provider: str = "deepseek", **kw):
        raise RuntimeError("no api key")

    import kovaak_tracker.coach.providers as prov_mod
    monkeypatch.setattr(prov_mod, "load_backend", fake_load_backend_fail)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "u1"}) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "你好"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] is None
    assert any("LLM" in n or "不可用" in n for n in body["notes"])
    # user + 占位 assistant 都持久化了
    assert len(body["history"]) == 2

@pytest.mark.asyncio
async def test_session_chat_writes_to_primary_thread(monkeypatch):
    """旧 session chat 写入进入 primary thread(coach_messages)。"""
    from webapp.backend import coach_store

    sid = await _seed_done_session()

    def fake_chat_with_coach(diagnosis, messages, backend, **kwargs):
        return "primary 路径"

    _patch_chat_ok(monkeypatch, fake_chat_with_coach)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.post(
            f"/api/sessions/{sid}/chat",
            json={"message": "session 侧提问"},
        )

    assert resp.status_code == 200, resp.text
    thread = await coach_store.get_or_create_primary_thread("u1")
    msgs = await coach_store.load_messages(int(thread["id"]))
    assert len(msgs) == 2
    assert msgs[0]["content"] == "session 侧提问"
    assert msgs[0]["legacy_session_id"] == sid
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["legacy_session_id"] == sid

    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT COUNT(*) AS c FROM chat_messages WHERE session_id=?",
        (sid,),
    )
    row = await cur.fetchone()
    assert int(row["c"]) == 0
