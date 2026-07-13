from __future__ import annotations

import math

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import benchmark_store
from webapp.backend.app import app


def record(**overrides):
    value = {
        "provider": "manual-local",
        "provider_license_note": "User-entered data; no provider content bundled.",
        "catalog_version": "2026-07",
        "scenario_id": "scenario:tile-frenzy",
        "metric_key": "score",
        "unit": "points",
        "value": 123.0,
        "observed_at": "2026-07-13T12:00:00Z",
        "availability": "available",
        "external_identity_ref": None,
        "identity_consent": False,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_benchmark_records_are_owner_scoped_and_require_consent():
    created = await benchmark_store.create_record("u1", record())
    assert created["provider"] == "manual-local"
    assert await benchmark_store.get_record(created["id"], "u2") is None
    assert len(await benchmark_store.list_records("u1")) == 1
    assert await benchmark_store.list_records("u2") == []

    with pytest.raises(ValueError, match="consent"):
        await benchmark_store.create_record(
            "u1", record(external_identity_ref="opaque-user", identity_consent=False),
        )
    with pytest.raises(ValueError, match="path"):
        await benchmark_store.create_record(
            "u1", record(external_identity_ref="/secret/id", identity_consent=True),
        )
    with pytest.raises(ValueError, match="finite"):
        await benchmark_store.create_record("u1", record(value=math.inf))


def test_benchmark_comparability_is_exact_and_provider_neutral():
    left = record()
    assert benchmark_store.comparable(left, record(value=200)) is True
    assert benchmark_store.comparable(left, record(catalog_version="other")) is False
    assert benchmark_store.comparable(left, record(provider="other")) is False
    assert benchmark_store.comparable(left, record(unit="percent")) is False
    assert benchmark_store.comparable(left, record(availability="stale")) is False
    assert benchmark_store.comparable(left, record(availability="unavailable")) is False


@pytest.mark.asyncio
async def test_benchmark_api_never_requires_or_returns_provider_secrets():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        created = await client.post("/api/benchmarks", json=record())
        listed = await client.get("/api/benchmarks")
    assert created.status_code == 200
    assert listed.status_code == 200
    payload = listed.json()["records"][0]
    assert "secret" not in payload
    assert "path" not in payload
    assert "trace" not in payload
