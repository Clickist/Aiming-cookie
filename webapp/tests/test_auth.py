"""Tests for TRUST_PROXY_USER / get_request_user_id."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

import webapp.backend.config as config_mod
from webapp.backend import db, queue
from webapp.backend.app import app


async def _seed_done_session_u1() -> int:
    sid = await queue.enqueue("u1", "/v.mp4", "/c.csv")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps({"diagnosis": {"profile": {}, "issues": [], "summary": {}, "meta": {}}}), sid),
    )
    await conn.commit()
    return sid


@pytest.mark.asyncio
async def test_default_mode_uses_x_user_id_header():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_empty"},
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trust_mode_missing_proxy_user_401(monkeypatch):
    monkeypatch.setattr(config_mod, "TRUST_PROXY_USER", "1")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trust_mode_ignores_spoofed_x_user_id_for_idor(monkeypatch):
    """Forged X-User-Id cannot impersonate owner; proxy header wins."""
    monkeypatch.setattr(config_mod, "TRUST_PROXY_USER", "1")
    sid = await _seed_done_session_u1()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "X-User-Id": "u1",
            "X-Forwarded-User": "intruder",
        },
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_trust_mode_forwarded_user_can_access_own_session(monkeypatch):
    monkeypatch.setattr(config_mod, "TRUST_PROXY_USER", "1")
    sid = await _seed_done_session_u1()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Forwarded-User": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sid


@pytest.mark.asyncio
async def test_trust_mode_remote_user_fallback(monkeypatch):
    monkeypatch.setattr(config_mod, "TRUST_PROXY_USER", "1")
    sid = await _seed_done_session_u1()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Remote-User": "u1"},
    ) as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200