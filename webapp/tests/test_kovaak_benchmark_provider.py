from __future__ import annotations

import json

import httpx
import pytest


def _payload(catalog: dict, difficulty: str, *, score: int = 12345) -> dict:
    scenarios = {}
    for index, pair in enumerate(catalog["pairs"]):
        item = pair[difficulty]
        scenarios[item["scenario_name"]] = {
            "score": score + index,
            "leaderboard_rank": index + 1,
            "scenario_rank": index % 10,
            "rank_maxes": list(range(1, 10)),
            "leaderboard_id": 1000 + index,
        }
    return {
        "benchmark_progress": 100.0,
        "overall_rank": 3,
        "categories": {
            "fixture": {
                "benchmark_progress": 100.0,
                "category_rank": 3,
                "rank_maxes": list(range(1, 10)),
                "scenarios": scenarios,
            }
        },
        "ranks": [{"name": f"rank-{index}"} for index in range(10)],
    }


@pytest.mark.asyncio
async def test_fetch_viscose_s2_validates_and_normalizes_scores() -> None:
    from webapp.backend import benchmark_catalog, kovaak_benchmark_provider as provider

    catalog = benchmark_catalog.load_catalog()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/webapp-backend/benchmarks/player-progress-rank-benchmark"
        )
        benchmark_id = int(request.url.params["benchmarkId"])
        difficulty = "easier" if benchmark_id == 2335 else "medium"
        assert request.url.params["steamId"] == "00000000000000000"
        assert request.url.params["page"] == "0"
        assert request.url.params["max"] == "100"
        return httpx.Response(200, json=_payload(catalog, difficulty), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await provider.fetch_viscose_s2(
            "00000000000000000", client=client,
        )

    assert snapshot["schema_version"] == "kovaak_benchmark_snapshot.v1"
    assert set(snapshot["difficulties"]) == {"easier", "medium"}
    assert len(snapshot["difficulties"]["easier"]["scenarios"]) == 39
    first = snapshot["difficulties"]["easier"]["scenarios"][0]
    assert first["score"] == 123.45
    assert first["scenario_rank"] == 0
    assert "leaderboard_rank" not in first
    assert "steam" not in json.dumps(snapshot).casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "duplicate", "unknown", "bad_score", "bad_rank"])
async def test_fetch_viscose_s2_rejects_incomplete_or_invalid_payloads(failure: str) -> None:
    from webapp.backend import benchmark_catalog, kovaak_benchmark_provider as provider

    catalog = benchmark_catalog.load_catalog()

    async def handler(request: httpx.Request) -> httpx.Response:
        difficulty = "easier" if int(request.url.params["benchmarkId"]) == 2335 else "medium"
        payload = _payload(catalog, difficulty)
        scenarios = payload["categories"]["fixture"]["scenarios"]
        first_name = next(iter(scenarios))
        if failure == "missing":
            scenarios.pop(first_name)
        elif failure == "duplicate":
            payload["categories"]["second"] = {
                "scenarios": {first_name: scenarios[first_name]},
            }
        elif failure == "unknown":
            scenarios["unknown scenario"] = scenarios.pop(first_name)
        elif failure == "bad_score":
            scenarios[first_name]["score"] = float("inf")
        else:
            scenarios[first_name]["scenario_rank"] = 11
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(provider.KovaaKBenchmarkError):
            await provider.fetch_viscose_s2("00000000000000000", client=client)


@pytest.mark.asyncio
async def test_fetch_viscose_s2_rejects_network_failure_without_identity_in_error() -> None:
    from webapp.backend import kovaak_benchmark_provider as provider

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(provider.KovaaKBenchmarkError) as captured:
            await provider.fetch_viscose_s2("00000000000000000", client=client)

    assert "00000000000000000" not in str(captured.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00000000000000000", "00000000000000000"),
        (
            "https://steamcommunity.com/profiles/00000000000000000/",
            "00000000000000000",
        ),
    ],
)
def test_steam_profile_input_normalizes_to_a_canonical_id(value: str, expected: str) -> None:
    from webapp.backend import kovaak_benchmark_provider as provider

    assert provider.normalize_steam_profile_input(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "123",
        "0000000000000000x",
        " 00000000000000000 ",
        "http://steamcommunity.com/profiles/00000000000000000/",
        "https://example.com/profiles/00000000000000000/",
        "https://steamcommunity.com/id/name/",
        "https://steamcommunity.com/profiles/00000000000000000",
        "https://steamcommunity.com/profiles/00000000000000000/extra",
        "https://steamcommunity.com/profiles/00000000000000000/?page=1",
        "https://steamcommunity.com/profiles/00000000000000000/#profile",
    ],
)
def test_steam_profile_input_rejects_noncanonical_or_identifying_forms(value: str) -> None:
    from webapp.backend import kovaak_benchmark_provider as provider

    with pytest.raises(ValueError, match="Steam profile"):
        provider.normalize_steam_profile_input(value)
