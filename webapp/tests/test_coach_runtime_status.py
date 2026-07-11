from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config as config_mod
from webapp.backend.app import app


@pytest.mark.asyncio
async def test_runtime_status_python_mode(monkeypatch):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "python")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/coach/runtime-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["runtime"] == "python"
    assert data["sidecar"] == "n/a"
    assert data["ready_for_fast_path"] is True
    assert "Python" in data["message"]


@pytest.mark.asyncio
async def test_runtime_status_pi_sidecar_up(monkeypatch):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    with patch(
        "webapp.backend.health.check_sidecar_ready",
        new_callable=AsyncMock,
        return_value=True,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/coach/runtime-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime"] == "pi"
    assert data["sidecar"] == "up"
    assert data["ready_for_fast_path"] is True
    assert data["message"]


@pytest.mark.asyncio
async def test_runtime_status_pi_sidecar_down(monkeypatch):
    monkeypatch.setattr(config_mod, "COACH_RUNTIME", "pi")
    with patch(
        "webapp.backend.health.check_sidecar_ready",
        new_callable=AsyncMock,
        return_value=False,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/coach/runtime-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime"] == "pi"
    assert data["sidecar"] == "down"
    assert data["ready_for_fast_path"] is False
    assert data["message"]