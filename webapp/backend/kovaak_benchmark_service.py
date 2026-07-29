"""Shared KovaaK score import and identity-free projection helpers."""

from __future__ import annotations

import datetime
from typing import Any, Mapping

from . import (
    benchmark_catalog,
    benchmark_store,
    coach_context_refs,
    kovaak_benchmark_provider,
    kovaak_connection_store,
)


class KovaaKConnectionNotFound(ValueError):
    pass


def _observed_at() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _records_from_snapshot(snapshot: Mapping[str, Any], observed_at: str) -> list[dict]:
    records: list[dict] = []
    difficulties = snapshot["difficulties"]
    for difficulty, details in difficulties.items():
        for scenario in details["scenarios"]:
            common = {
                "provider": "kovaaks-webapp",
                "provider_license_note": "User-authorized KovaaK benchmark score import.",
                "catalog_version": snapshot["catalog_version"],
                "scenario_id": scenario["scenario_id"],
                "observed_at": observed_at,
                "availability": "available",
                "external_identity_ref": None,
                "identity_consent": False,
            }
            records.extend((
                {**common, "metric_key": "score", "unit": "points", "value": scenario["score"]},
                {
                    **common,
                    "metric_key": "scenario_rank",
                    "unit": "rank",
                    "value": scenario["scenario_rank"],
                },
            ))
        records.append({
            "provider": "kovaaks-webapp",
            "provider_license_note": "User-authorized KovaaK benchmark score import.",
            "catalog_version": snapshot["catalog_version"],
            "scenario_id": f"benchmark:viscose-s2:{difficulty}",
            "metric_key": "overall_rank",
            "unit": "rank",
            "value": details["overall_rank"],
            "observed_at": observed_at,
            "availability": "available",
            "external_identity_ref": None,
            "identity_consent": False,
        })
    if len(records) != 158:
        raise ValueError("KovaaK benchmark snapshot is incomplete")
    return records


def unavailable_score_projection() -> dict:
    return {
        "schema_version": "kovaak_scores.v1",
        "availability": "unavailable",
        "observed_at": None,
        "stages": [],
        "items": [],
    }


def project_score_summary(summary: object) -> dict:
    if not isinstance(summary, dict):
        return unavailable_score_projection()
    try:
        catalog = benchmark_catalog.load_catalog()
        completion = summary["completion"]
        ranks = summary["provisional_ranks"]
        observed_at = summary["observed_at"]
        scenarios = summary["scenarios"]
        if not isinstance(completion, dict) or not isinstance(ranks, dict):
            return unavailable_score_projection()
        if not isinstance(observed_at, str) or not isinstance(scenarios, list):
            return unavailable_score_projection()

        stages: list[dict] = []
        for stage in ("easier", "medium"):
            progress = completion.get(stage)
            rank = ranks.get(stage)
            if (
                not isinstance(progress, dict)
                or isinstance(rank, bool)
                or not isinstance(rank, int)
                or not 0 <= rank < len(catalog["rank_names"])
                or not isinstance(progress.get("completed"), int)
                or not isinstance(progress.get("required"), int)
            ):
                return unavailable_score_projection()
            stages.append({
                "stage": stage,
                "completed": progress["completed"],
                "required": progress["required"],
                "rank": rank,
                "rank_name": catalog["rank_names"][rank],
            })

        items: list[dict] = []
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                return unavailable_score_projection()
            stage = scenario.get("difficulty")
            rank = scenario.get("scenario_rank")
            category = scenario.get("category")
            subcategory = scenario.get("subcategory")
            if (
                stage not in {"easier", "medium"}
                or isinstance(rank, bool)
                or not isinstance(rank, int)
                or not 0 <= rank < len(catalog["rank_names"])
                or not all(
                    isinstance(scenario.get(field), str) and scenario[field]
                    for field in ("scenario_name", "category", "subcategory")
                )
                or not isinstance(scenario.get("score"), (int, float))
                or isinstance(scenario.get("score"), bool)
            ):
                return unavailable_score_projection()
            items.append({
                "stage": stage,
                "name": scenario["scenario_name"],
                "category": category,
                "subcategory": subcategory,
                "score": float(scenario["score"]),
                "item_rank": rank,
                "item_rank_name": catalog["rank_names"][rank],
                "completed": scenario["score"] > 0,
            })
    except (KeyError, TypeError, ValueError):
        return unavailable_score_projection()
    return {
        "schema_version": "kovaak_scores.v1",
        "availability": "available",
        "observed_at": observed_at,
        "stages": stages,
        "items": items,
    }


async def write_snapshot(owner_id: str, snapshot: Mapping[str, Any]) -> dict:
    observed_at = _observed_at()
    records = _records_from_snapshot(snapshot, observed_at)
    await benchmark_store.create_records_atomically(owner_id, records)
    return {
        "schema_version": "kovaak_benchmark_sync_result.v1",
        "imported_score_count": 78,
        "difficulty_counts": {"easier": 39, "medium": 39},
        "observed_at": observed_at,
    }


async def sync_owner_snapshot(owner_id: str, steam_id: str) -> dict:
    snapshot = await kovaak_benchmark_provider.fetch_viscose_s2(steam_id)
    return await write_snapshot(owner_id, snapshot)


async def refresh_connected_snapshot(owner_id: str) -> dict:
    async with kovaak_connection_store.lock_owner_connection(owner_id):
        connection = await kovaak_connection_store.get_connection(owner_id)
        if connection is None:
            raise KovaaKConnectionNotFound("KovaaK account is not connected")
        return await sync_owner_snapshot(owner_id, connection["steam_id"])


async def refresh_connected_score_summary(owner_id: str) -> dict:
    await refresh_connected_snapshot(owner_id)
    catalog = benchmark_catalog.load_catalog()
    return project_score_summary(
        coach_context_refs.project_benchmark_summary(
            await benchmark_store.list_latest_snapshot(
                owner_id,
                provider="kovaaks-webapp",
                catalog_version=catalog["catalog_version"],
                exact_record_count=158,
            ),
        ),
    )


async def project_temporary_snapshot(steam_profile_input: str) -> dict:
    steam_id = kovaak_benchmark_provider.normalize_steam_profile_input(steam_profile_input)
    snapshot = await kovaak_benchmark_provider.fetch_viscose_s2(steam_id)
    records = _records_from_snapshot(snapshot, _observed_at())
    return project_score_summary(coach_context_refs.project_benchmark_summary(records))
