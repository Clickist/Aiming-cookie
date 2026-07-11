from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config
from webapp.backend.app import app


@pytest.mark.asyncio
async def test_healthz_returns_200_ok():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_readyz_db_ok_returns_200(monkeypatch):
    monkeypatch.setattr(config, "COACH_SIDECAR_URL", "")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_readyz_configured_sidecar_failure_returns_503(monkeypatch):
    monkeypatch.setattr(config, "COACH_SIDECAR_URL", "http://sidecar.test")
    with patch(
        "webapp.backend.health.check_sidecar_ready",
        new_callable=AsyncMock,
        return_value=False,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "sidecar": False}


@pytest.mark.asyncio
async def test_readyz_db_failure_returns_503():
    with patch(
        "webapp.backend.health.check_db_ready",
        new_callable=AsyncMock,
        return_value=False,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False