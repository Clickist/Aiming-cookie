from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import benchmark_catalog, benchmark_store, coach_context_refs, coach_store, kovaak_benchmark_service, kovaak_connection_store
from webapp.backend.app import app


STEAM_ID = "76561199033719938"
PROFILE_URL = f"https://steamcommunity.com/profiles/{STEAM_ID}/"


def complete_snapshot() -> dict:
    catalog = benchmark_catalog.load_catalog()
    return {
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


@pytest.mark.asyncio
async def test_connection_is_owner_scoped_and_public_responses_hide_identity():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        initial = await client.get("/api/kovaak-connection")
        missing_consent = await client.put(
            "/api/kovaak-connection", json={"steam_profile": PROFILE_URL},
        )
        denied = await client.put(
            "/api/kovaak-connection",
            json={"steam_profile": PROFILE_URL, "identity_consent": False},
        )
        saved = await client.put(
            "/api/kovaak-connection",
            json={"steam_profile": PROFILE_URL, "identity_consent": True},
        )
        connected = await client.get("/api/kovaak-connection")
        stored = await kovaak_connection_store.get_connection("u1")
        removed = await client.delete("/api/kovaak-connection")

    for response in (initial, missing_consent, denied, saved, connected, removed):
        wire = response.text.casefold()
        assert STEAM_ID not in wire
        assert "steamcommunity" not in wire
        assert "steam_profile" not in wire
    for response in (initial, saved, connected, removed):
        assert response.status_code == 200, response.text
    assert initial.json() == {"connected": False}
    assert missing_consent.status_code == 422
    assert denied.status_code == 422
    assert saved.json() == {"connected": True}
    assert connected.json() == {"connected": True}
    assert stored is not None
    assert stored["steam_id"] == STEAM_ID
    assert removed.json() == {"deleted": True}
    assert await kovaak_connection_store.get_connection("u1") is None


@pytest.mark.asyncio
async def test_connected_refresh_is_atomic_and_failure_preserves_prior_snapshot(monkeypatch):
    from webapp.backend import kovaak_benchmark_provider

    calls: list[str] = []

    async def fetch(steam_id: str) -> dict:
        calls.append(steam_id)
        return complete_snapshot()

    monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", fetch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        assert (
            await client.put(
                "/api/kovaak-connection",
                json={"steam_profile": PROFILE_URL, "identity_consent": True},
            )
        ).status_code == 200
        refreshed = await client.post("/api/kovaak-connection/refresh")

        async def unavailable(_: str) -> dict:
            raise kovaak_benchmark_provider.KovaaKBenchmarkError("unavailable")

        monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", unavailable)
        failed = await client.post("/api/kovaak-connection/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["imported_score_count"] == 78
    assert STEAM_ID not in refreshed.text
    assert calls == [STEAM_ID]
    assert failed.status_code == 502
    assert len(await benchmark_store.list_records("u1")) == 158


@pytest.mark.asyncio
async def test_temporary_snapshot_projection_never_persists_identity_or_scores(monkeypatch):
    from webapp.backend import kovaak_benchmark_provider

    async def fetch(steam_id: str) -> dict:
        assert steam_id == STEAM_ID
        return complete_snapshot()

    monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", fetch)
    projected = await kovaak_benchmark_service.project_temporary_snapshot(PROFILE_URL)

    assert projected["availability"] == "available"
    assert len(projected["items"]) == 78
    assert await kovaak_connection_store.get_connection("u1") is None
    assert await benchmark_store.list_records("u1") == []


@pytest.mark.asyncio
async def test_switching_connection_waits_for_refresh_and_stales_the_old_snapshot(monkeypatch):
    from webapp.backend import kovaak_benchmark_provider

    replacement_id = "76561198000000000"
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(steam_id: str) -> dict:
        assert steam_id == STEAM_ID
        started.set()
        await release.wait()
        return complete_snapshot()

    monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", fetch)
    await kovaak_connection_store.save_connection("u1", STEAM_ID)
    refresh = asyncio.create_task(kovaak_benchmark_service.refresh_connected_snapshot("u1"))
    await started.wait()
    switch = asyncio.create_task(kovaak_connection_store.save_connection("u1", replacement_id))
    await asyncio.sleep(0)
    assert not switch.done()

    release.set()
    await asyncio.gather(refresh, switch)

    connection = await kovaak_connection_store.get_connection("u1")
    records = await benchmark_store.list_records("u1")
    thread = await coach_store.get_or_create_primary_thread("u1")
    bundle, _ = await coach_context_refs.build_context_bundle(int(thread["id"]), None)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        scores = await client.get("/api/kovaak-scores")

    assert connection is not None
    assert connection["steam_id"] == replacement_id
    assert len(records) == 158
    assert {record["availability"] for record in records} == {"stale"}
    assert bundle["benchmark_summary"] is None
    assert scores.status_code == 200, scores.text
    assert scores.json() == {
        "schema_version": "kovaak_scores.v1",
        "availability": "unavailable",
        "observed_at": None,
        "stages": [],
        "items": [],
    }

    async def replacement_fetch(steam_id: str) -> dict:
        assert steam_id == replacement_id
        return complete_snapshot()

    monkeypatch.setattr(kovaak_benchmark_provider, "fetch_viscose_s2", replacement_fetch)
    await kovaak_benchmark_service.refresh_connected_snapshot("u1")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    ) as client:
        replacement_scores = await client.get("/api/kovaak-scores")

    assert replacement_scores.status_code == 200, replacement_scores.text
    assert replacement_scores.json()["availability"] == "available"
    assert len(replacement_scores.json()["items"]) == 78
