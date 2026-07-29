from __future__ import annotations

import math

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import benchmark_store
from webapp.backend import db
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
    payload = record()
    payload.pop("external_identity_ref")
    payload.pop("identity_consent")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        created = await client.post("/api/benchmarks", json=payload)
        listed = await client.get("/api/benchmarks")
    assert created.status_code == 200
    assert listed.status_code == 200
    payload = listed.json()["records"][0]
    assert "secret" not in payload
    assert "path" not in payload
    assert "trace" not in payload


@pytest.mark.asyncio
async def test_public_benchmark_api_rejects_and_never_returns_identity_fields():
    legacy = await benchmark_store.create_record(
        "legacy-owner",
        record(external_identity_ref="76561199033719938", identity_consent=True),
    )
    assert legacy["external_identity_ref"] == "76561199033719938"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "legacy-owner"},
    ) as client:
        rejected = await client.post(
            "/api/benchmarks",
            json=record(
                external_identity_ref="76561199033719938",
                identity_consent=True,
            ),
        )
        listed = await client.get("/api/benchmarks")

    assert rejected.status_code == 422
    assert listed.status_code == 200
    assert len(listed.json()["records"]) == 1
    wire = listed.text.casefold()
    assert "76561199033719938" not in wire
    assert "external_identity_ref" not in wire
    assert "identity_consent" not in wire


@pytest.mark.asyncio
async def test_atomic_snapshot_validates_every_record_before_writing():
    valid = [record(scenario_id=f"scenario:{index}") for index in range(3)]
    created = await benchmark_store.create_records_atomically("u1", valid)
    assert len(created) == 3

    invalid = [
        record(scenario_id="scenario:ok"),
        record(scenario_id="scenario:bad", value=math.inf),
    ]
    with pytest.raises(ValueError, match="finite"):
        await benchmark_store.create_records_atomically("u1", invalid)

    listed = await benchmark_store.list_records("u1")
    assert {item["scenario_id"] for item in listed} == {"scenario:0", "scenario:1", "scenario:2"}


@pytest.mark.asyncio
async def test_latest_snapshot_does_not_mix_benchmark_imports():
    first = [
        record(
            provider="kovaaks-webapp", catalog_version="s2",
            observed_at="2026-07-28T12:00:00Z",
        ),
        record(
            provider="kovaaks-webapp", catalog_version="s2",
            scenario_id="scenario:first-rank", observed_at="2026-07-28T12:00:00Z",
        ),
    ]
    latest = [
        record(
            provider="kovaaks-webapp", catalog_version="s2",
            scenario_id="scenario:latest", observed_at="2026-07-29T12:00:00Z",
        ),
    ]
    await benchmark_store.create_records_atomically("u1", first)
    await benchmark_store.create_records_atomically("u1", latest)

    snapshot = await benchmark_store.list_latest_snapshot(
        "u1", provider="kovaaks-webapp", catalog_version="s2",
    )
    assert [item["scenario_id"] for item in snapshot] == ["scenario:latest"]


@pytest.mark.asyncio
async def test_latest_complete_snapshot_uses_one_bounded_read():
    first_observed_at = "2026-07-28T12:00:00Z"
    latest_observed_at = "2026-07-29T12:00:00Z"
    first_snapshot = [
        record(
            provider="kovaaks-webapp",
            catalog_version="s2",
            scenario_id=f"scenario:first:{index}",
            observed_at=first_observed_at,
        )
        for index in range(158)
    ]
    latest_snapshot = [
        record(
            provider="kovaaks-webapp",
            catalog_version="s2",
            scenario_id=f"scenario:latest:{index}",
            observed_at=latest_observed_at,
        )
        for index in range(158)
    ]
    await benchmark_store.create_records_atomically("u1", first_snapshot)
    await benchmark_store.create_records_atomically("u1", latest_snapshot)
    await benchmark_store.create_records_atomically(
        "u1",
        [
            record(
                provider="kovaaks-webapp",
                catalog_version="s2",
                scenario_id=f"scenario:incomplete:{index}",
                observed_at="2026-07-30T12:00:00Z",
            )
            for index in range(157)
        ],
    )

    statements: list[str] = []
    conn = await db.get_conn()
    await conn.set_trace_callback(statements.append)
    try:
        snapshot = await benchmark_store.list_latest_snapshot(
            "u1",
            provider="kovaaks-webapp",
            catalog_version="s2",
            exact_record_count=158,
        )
    finally:
        await conn.set_trace_callback(None)

    assert len(snapshot) == 158
    assert {item["observed_at"] for item in snapshot} == {latest_observed_at}
    query_statements = [
        statement for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert len(query_statements) == 1


@pytest.mark.asyncio
async def test_kovaak_sync_api_requires_consent_and_does_not_echo_steam_id(monkeypatch):
    from webapp.backend import (
        benchmark_catalog,
        kovaak_benchmark_provider,
        kovaak_connection_store,
    )

    catalog = benchmark_catalog.load_catalog()
    snapshot = {
        "schema_version": "kovaak_benchmark_snapshot.v1",
        "catalog_version": catalog["catalog_version"],
        "difficulties": {
            difficulty: {
                "overall_rank": 3,
                "scenarios": [
                    {
                        "pair_id": pair["pair_id"],
                        "scenario_id": pair[difficulty]["scenario_id"],
                        "scenario_name": pair[difficulty]["scenario_name"],
                        "score": float(index + 1),
                        "scenario_rank": index % 10,
                    }
                    for index, pair in enumerate(catalog["pairs"])
                ],
            }
            for difficulty in ("easier", "medium")
        },
    }

    async def fetch(steam_id: str):
        assert steam_id == "00000000000000000"
        return snapshot

    monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", fetch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        denied = await client.post(
            "/api/benchmarks/sync/kovaaks",
            json={"schema_version": "kovaak_benchmark_sync_request.v1", "steam_id": "00000000000000000", "identity_consent": False},
        )
        invalid = await client.post(
            "/api/benchmarks/sync/kovaaks",
            json={
                "schema_version": "kovaak_benchmark_sync_request.v1",
                "steam_id": "https://steamcommunity.com/id/private-profile/",
                "identity_consent": True,
            },
        )
        synced = await client.post(
            "/api/benchmarks/sync/kovaaks",
            json={
                "schema_version": "kovaak_benchmark_sync_request.v1",
                "steam_id": "https://steamcommunity.com/profiles/00000000000000000/",
                "identity_consent": True,
            },
        )

    assert denied.status_code == 422
    assert invalid.status_code == 422
    assert "private-profile" not in invalid.text
    assert "steamcommunity" not in invalid.text
    assert synced.status_code == 200
    assert synced.json()["imported_score_count"] == 78
    assert synced.json()["difficulty_counts"] == {"easier": 39, "medium": 39}
    assert "00000000000000000" not in synced.text
    records = await benchmark_store.list_records("u1")
    assert len(records) == 158
    assert len({item["observed_at"] for item in records}) == 1
    assert all(item["external_identity_ref"] is None for item in records)
    assert all(item["identity_consent"] is False for item in records)
    assert await kovaak_connection_store.get_connection("u1") is None


@pytest.mark.asyncio
async def test_kovaak_scores_api_is_identity_free_and_requires_a_complete_snapshot():
    from webapp.backend import benchmark_catalog

    catalog = benchmark_catalog.load_catalog()
    observed_at = "2026-07-29T10:15:00Z"
    records = []
    for difficulty in ("easier", "medium"):
        for index, pair in enumerate(catalog["pairs"]):
            scenario = pair[difficulty]
            records.extend((
                record(
                    provider="kovaaks-webapp",
                    catalog_version=catalog["catalog_version"],
                    scenario_id=scenario["scenario_id"],
                    metric_key="score",
                    value=float(index + 1),
                    observed_at=observed_at,
                ),
                record(
                    provider="kovaaks-webapp",
                    catalog_version=catalog["catalog_version"],
                    scenario_id=scenario["scenario_id"],
                    metric_key="scenario_rank",
                    unit="rank",
                    value=float(index % 10),
                    observed_at=observed_at,
                ),
            ))
        records.append(record(
            provider="kovaaks-webapp",
            catalog_version=catalog["catalog_version"],
            scenario_id=f"benchmark:viscose-s2:{difficulty}",
            metric_key="overall_rank",
            unit="rank",
            value=3.0,
            observed_at=observed_at,
        ))
    await benchmark_store.create_records_atomically("u1", records)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        available = await client.get("/api/kovaak-scores")

    assert available.status_code == 200, available.text
    payload = available.json()
    assert payload["schema_version"] == "kovaak_scores.v1"
    assert payload["availability"] == "available"
    assert payload["observed_at"] == observed_at
    assert payload["stages"][0] == {
        "stage": "easier",
        "completed": 39,
        "required": 39,
        "rank": 3,
        "rank_name": "Ermine",
    }
    assert payload["items"][0] == {
        "stage": "easier",
        "name": catalog["pairs"][0]["easier"]["scenario_name"],
        "category": catalog["pairs"][0]["category"],
        "subcategory": catalog["pairs"][0]["subcategory"],
        "score": 1.0,
        "item_rank": 0,
        "item_rank_name": "No Rank",
        "completed": True,
    }
    wire = available.text
    for forbidden in (
        "steam", "identity", "provider", "source_ref", "catalog_ref", "consent",
        "external_identity_ref", "payload", "https://",
    ):
        assert forbidden not in wire.casefold()

    await benchmark_store.create_records_atomically(
        "u1",
        [
            {**item, "observed_at": "2026-07-30T10:15:00Z"}
            for item in records[:-1]
        ],
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        fallback = await client.get("/api/kovaak-scores")

    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["availability"] == "available"
    assert fallback.json()["observed_at"] == observed_at

    partial = records[:-1]
    await benchmark_store.create_records_atomically(
        "u2",
        [
            {**item, "observed_at": "2026-07-30T10:15:00Z"}
            for item in partial
        ],
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u2"},
    ) as client:
        unavailable = await client.get("/api/kovaak-scores")

    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json() == {
        "schema_version": "kovaak_scores.v1",
        "availability": "unavailable",
        "observed_at": None,
        "stages": [],
        "items": [],
    }
