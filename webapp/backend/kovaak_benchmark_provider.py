"""Narrow, validated adapter for KovaaK's Viscose S2 benchmark endpoint."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from typing import Any

import httpx

from . import benchmark_catalog


_STEAM_ID = re.compile(r"^[0-9]{17}$")
_STEAM_PROFILE_URL = re.compile(
    r"^https://steamcommunity\.com/profiles/([0-9]{17})/$",
)
_STEAM_VANITY_URL = re.compile(
    r"^https://steamcommunity\.com/id/([A-Za-z0-9_-]{1,64})/?$",
)
_PROFILE_PAGE_STEAMID = re.compile(
    r'g_rgProfileData\s*=\s*\{[^;]*?"steamid"\s*:\s*"([0-9]{17})"',
)
_BENCHMARK_URL = (
    "https://kovaaks.com/webapp-backend/benchmarks/"
    "player-progress-rank-benchmark"
)
_DIFFICULTIES = ("easier", "medium")
_RANK_MAX = 9


class KovaaKBenchmarkError(ValueError):
    """The upstream response was unavailable or did not match the course catalog."""


def validate_steam_id(steam_id: str) -> str:
    if not isinstance(steam_id, str) or _STEAM_ID.fullmatch(steam_id) is None:
        raise ValueError("Steam ID must be exactly 17 digits")
    return steam_id


def normalize_steam_profile_input(value: str) -> str:
    """Accept an exact Steam ID, canonical numeric profile URL, or vanity URL."""
    if not isinstance(value, str):
        raise ValueError("Steam profile input is invalid")
    if _STEAM_ID.fullmatch(value) is not None:
        return value
    match = _STEAM_PROFILE_URL.fullmatch(value)
    if match is not None:
        return match.group(1)
    if _STEAM_VANITY_URL.fullmatch(value) is not None:
        # Vanity URLs keep their form; resolving the name to a 17-digit ID
        # is a network round trip, done by resolve_steam_profile_input.
        return value
    raise ValueError("Steam profile input is invalid")


def normalize_numeric_steam_input(value: str) -> str:
    """Numeric forms only; endpoints that cannot resolve vanity URLs use this."""
    normalized = normalize_steam_profile_input(value)
    if _STEAM_ID.fullmatch(normalized) is None:
        raise ValueError("Steam profile input is invalid")
    return normalized


async def resolve_steam_profile_input(
    value: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Turn any accepted profile input into the 17-digit Steam ID.

    Vanity URLs are resolved by fetching the public profile page; missing or
    unavailable profiles surface as ValueError without echoing the input.
    """
    normalized = normalize_steam_profile_input(value)
    if _STEAM_ID.fullmatch(normalized) is not None:
        return normalized

    async def resolve_with(active_client: httpx.AsyncClient) -> str:
        try:
            response = await active_client.get(
                # Canonical form ends in "/", so a slash-less paste still hits
                # the real page instead of depending on a Steam redirect.
                normalized if normalized.endswith("/") else f"{normalized}/",
                headers={"User-Agent": "Mozilla/5.0"},
            )
        except httpx.HTTPError as error:
            raise ValueError("Steam profile could not be resolved") from error
        if response.status_code != 200:
            raise ValueError("Steam profile could not be resolved")
        # steamcommunity serves missing profiles as HTTP 200 error pages
        # without the g_rgProfileData script block, so extraction is the
        # existence check, not the status code.
        match = _PROFILE_PAGE_STEAMID.search(response.text)
        if match is None:
            raise ValueError("Steam profile could not be resolved")
        return validate_steam_id(match.group(1))

    if client is not None:
        return await resolve_with(client)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        follow_redirects=True,
    ) as owned_client:
        return await resolve_with(owned_client)


def _rank(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _RANK_MAX:
        raise KovaaKBenchmarkError(f"invalid {field}")
    return value


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KovaaKBenchmarkError("invalid score")
    score = float(value)
    if not math.isfinite(score) or score < 0:
        raise KovaaKBenchmarkError("invalid score")
    return score / 100


def _normalize_difficulty(
    payload: object,
    difficulty: str,
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise KovaaKBenchmarkError("invalid benchmark payload")
    categories = payload.get("categories")
    if not isinstance(categories, Mapping):
        raise KovaaKBenchmarkError("benchmark categories are unavailable")

    raw_scenarios: dict[str, object] = {}
    for category in categories.values():
        if not isinstance(category, Mapping):
            raise KovaaKBenchmarkError("invalid benchmark category")
        scenarios = category.get("scenarios")
        if not isinstance(scenarios, Mapping):
            raise KovaaKBenchmarkError("benchmark scenarios are unavailable")
        for name, entry in scenarios.items():
            if not isinstance(name, str) or name in raw_scenarios:
                raise KovaaKBenchmarkError("duplicate benchmark scenario")
            raw_scenarios[name] = entry

    expected = {
        pair[difficulty]["scenario_name"]: (pair, pair[difficulty])
        for pair in catalog["pairs"]
    }
    if set(raw_scenarios) != set(expected):
        raise KovaaKBenchmarkError("benchmark scenarios do not match Viscose S2")

    scenarios: list[dict[str, Any]] = []
    for pair in catalog["pairs"]:
        scenario = pair[difficulty]
        raw = raw_scenarios[scenario["scenario_name"]]
        if not isinstance(raw, Mapping):
            raise KovaaKBenchmarkError("invalid benchmark scenario")
        scenarios.append({
            "pair_id": pair["pair_id"],
            "scenario_id": scenario["scenario_id"],
            "scenario_name": scenario["scenario_name"],
            "score": _score(raw.get("score")),
            "scenario_rank": _rank(raw.get("scenario_rank"), "scenario rank"),
        })

    return {
        "overall_rank": _rank(payload.get("overall_rank"), "overall rank"),
        "scenarios": scenarios,
    }


async def _get_benchmark(
    client: httpx.AsyncClient,
    benchmark_id: int,
    steam_id: str,
) -> object:
    try:
        response = await client.get(
            _BENCHMARK_URL,
            params={
                "benchmarkId": benchmark_id,
                "steamId": steam_id,
                "page": 0,
                "max": 100,
            },
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise KovaaKBenchmarkError("KovaaK benchmark service is unavailable") from error


async def fetch_viscose_s2(
    steam_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch and reduce both S2 difficulties without retaining identity metadata."""
    validated_id = validate_steam_id(steam_id)
    catalog = benchmark_catalog.load_catalog()

    async def fetch_with(active_client: httpx.AsyncClient) -> dict[str, Any]:
        payloads = await asyncio.gather(*(
            _get_benchmark(active_client, catalog["benchmark_ids"][difficulty], validated_id)
            for difficulty in _DIFFICULTIES
        ))
        return {
            "schema_version": "kovaak_benchmark_snapshot.v1",
            "catalog_version": catalog["catalog_version"],
            "difficulties": {
                difficulty: _normalize_difficulty(payload, difficulty, catalog)
                for difficulty, payload in zip(_DIFFICULTIES, payloads)
            },
        }

    if client is not None:
        return await fetch_with(client)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as owned_client:
        return await fetch_with(owned_client)
