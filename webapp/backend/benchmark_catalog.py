"""Validated Viscose S2 course pairing shared by sync and Coach."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "knowledge"
    / "benchmarks"
    / "viscose-s2.v1.json"
)
_DIFFICULTIES = ("easier", "medium")
_PAIR_ID = re.compile(r"^viscose-s2:pair:(0[1-9]|[1-3][0-9])$")
_SCENARIO_ID = re.compile(r"^viscose-s2:(easier|medium):(0[1-9]|[1-3][0-9])$")
_SCENARIO_PROFILE_REF = re.compile(r"^scenario:[A-Za-z0-9._-]+@[1-9][0-9]*$")


def _text(value: object, field: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"benchmark catalog {field} is invalid")
    return value.strip()


def validate_catalog(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "catalog_ref", "catalog_version", "source_refs",
        "benchmark_ids", "rank_names", "pairs",
    }:
        raise ValueError("benchmark catalog shape is invalid")
    if value.get("schema_version") != "benchmark_course_catalog.v1":
        raise ValueError("benchmark catalog version is invalid")
    if value.get("catalog_ref") != "benchmark-catalog:viscose-s2@1":
        raise ValueError("benchmark catalog ref is invalid")
    catalog_version = _text(value.get("catalog_version"), "catalog_version")
    source_refs = value.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs or len(source_refs) > 8:
        raise ValueError("benchmark catalog sources are invalid")
    normalized_sources = [_text(item, "source_ref") for item in source_refs]
    benchmark_ids = value.get("benchmark_ids")
    if not isinstance(benchmark_ids, Mapping) or dict(benchmark_ids) != {
        "easier": 2335, "medium": 2336,
    }:
        raise ValueError("benchmark catalog benchmark IDs are invalid")
    rank_names = value.get("rank_names")
    if not isinstance(rank_names, list) or len(rank_names) != 10:
        raise ValueError("benchmark catalog ranks are invalid")
    normalized_ranks = [_text(item, "rank_name", maximum=40) for item in rank_names]
    pairs = value.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 39:
        raise ValueError("benchmark catalog must contain 39 pairs")

    normalized_pairs: list[dict[str, Any]] = []
    pair_ids: set[str] = set()
    scenario_ids = {difficulty: set() for difficulty in _DIFFICULTIES}
    scenario_names = {difficulty: set() for difficulty in _DIFFICULTIES}
    exact_refs: set[str] = set()
    for index, raw_pair in enumerate(pairs, 1):
        if not isinstance(raw_pair, Mapping) or set(raw_pair) != {
            "pair_id", "category", "subcategory", "easier", "medium",
        }:
            raise ValueError("benchmark catalog pair is invalid")
        pair_id = _text(raw_pair.get("pair_id"), "pair_id")
        if not _PAIR_ID.fullmatch(pair_id) or pair_id in pair_ids:
            raise ValueError("benchmark catalog pair identity is invalid")
        pair_ids.add(pair_id)
        normalized_pair: dict[str, Any] = {
            "pair_id": pair_id,
            "category": _text(raw_pair.get("category"), "category", maximum=64),
            "subcategory": _text(raw_pair.get("subcategory"), "subcategory", maximum=64),
        }
        for difficulty in _DIFFICULTIES:
            raw_scenario = raw_pair.get(difficulty)
            if not isinstance(raw_scenario, Mapping) or set(raw_scenario) != {
                "scenario_id", "scenario_name", "scenario_profile_ref",
            }:
                raise ValueError("benchmark catalog scenario is invalid")
            scenario_id = _text(raw_scenario.get("scenario_id"), "scenario_id")
            match = _SCENARIO_ID.fullmatch(scenario_id)
            if (
                match is None
                or match.group(1) != difficulty
                or int(match.group(2)) != index
                or scenario_id in scenario_ids[difficulty]
            ):
                raise ValueError("benchmark catalog scenario identity is invalid")
            scenario_name = _text(raw_scenario.get("scenario_name"), "scenario_name")
            if scenario_name in scenario_names[difficulty]:
                raise ValueError("benchmark catalog scenario name is ambiguous")
            profile_ref = raw_scenario.get("scenario_profile_ref")
            if profile_ref is not None:
                if (
                    not isinstance(profile_ref, str)
                    or _SCENARIO_PROFILE_REF.fullmatch(profile_ref) is None
                    or profile_ref in exact_refs
                ):
                    raise ValueError("benchmark catalog exact scenario ref is invalid")
                exact_refs.add(profile_ref)
            scenario_ids[difficulty].add(scenario_id)
            scenario_names[difficulty].add(scenario_name)
            normalized_pair[difficulty] = {
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "scenario_profile_ref": profile_ref,
            }
        normalized_pairs.append(normalized_pair)
    return {
        "schema_version": "benchmark_course_catalog.v1",
        "catalog_ref": "benchmark-catalog:viscose-s2@1",
        "catalog_version": catalog_version,
        "source_refs": normalized_sources,
        "benchmark_ids": {"easier": 2335, "medium": 2336},
        "rank_names": normalized_ranks,
        "pairs": normalized_pairs,
    }


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    source = CATALOG_PATH if path is None else path
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("benchmark catalog is unavailable") from error
    return validate_catalog(raw)


def pair_for_scenario_profile(
    scenario_profile_ref: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    source = validate_catalog(catalog) if catalog is not None else load_catalog()
    for pair in source["pairs"]:
        if any(pair[difficulty]["scenario_profile_ref"] == scenario_profile_ref for difficulty in _DIFFICULTIES):
            return pair
    return None

def scenario_by_name(
    difficulty: str,
    scenario_name: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if difficulty not in _DIFFICULTIES:
        return None
    source = validate_catalog(catalog) if catalog is not None else load_catalog()
    for pair in source["pairs"]:
        if pair[difficulty]["scenario_name"] == scenario_name:
            return pair, pair[difficulty]
    return None


# ── Benchmark summary projection ────────────────────────────────────────
#
# Migrated from the deleted coach_context_refs module (2026-08-13 rewrite);
# projects one complete KovaaK snapshot for /api/kovaak-scores.

BENCHMARK_SUMMARY_SCHEMA_VERSION = "coach_benchmark_summary.v1"
_BENCHMARK_PROVIDER = "kovaaks-webapp"
_BENCHMARK_REVIEW_CANDIDATE_LIMIT = 8


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _rank(value: object) -> int | None:
    number = _finite_nonnegative(value)
    if number is None or not number.is_integer() or number > 9:
        return None
    return int(number)


def _observed_at(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def project_benchmark_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Project one complete KovaaK snapshot without exposing identity or provider payloads."""
    try:
        catalog = load_catalog()
    except ValueError:
        return None
    scenario_lookup = {
        item["scenario_id"]: (difficulty, item["scenario_name"])
        for difficulty in ("easier", "medium")
        for pair in catalog["pairs"]
        for item in [pair[difficulty]]
    }
    expected_metric_keys = {"score", "scenario_rank", "overall_rank"}
    snapshots: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if (
            record.get("provider") != _BENCHMARK_PROVIDER
            or record.get("catalog_version") != catalog["catalog_version"]
            or record.get("metric_key") not in expected_metric_keys
        ):
            continue
        observed_at = _observed_at(record.get("observed_at"))
        if observed_at is None:
            continue
        snapshots.setdefault(observed_at, []).append(record)

    for observed_at in sorted(snapshots, reverse=True):
        metrics: dict[tuple[str, str], float] = {}
        valid = True
        for record in snapshots[observed_at]:
            scenario_id = record.get("scenario_id")
            metric_key = record.get("metric_key")
            if not isinstance(scenario_id, str) or not isinstance(metric_key, str):
                valid = False
                break
            if metric_key == "overall_rank":
                if scenario_id not in {
                    "benchmark:viscose-s2:easier", "benchmark:viscose-s2:medium",
                }:
                    valid = False
                    break
            elif scenario_id not in scenario_lookup:
                valid = False
                break
            value = _finite_nonnegative(record.get("value"))
            key = (scenario_id, metric_key)
            if value is None or key in metrics:
                valid = False
                break
            metrics[key] = value
        if not valid:
            continue

        scenarios: list[dict[str, Any]] = []
        for difficulty in ("easier", "medium"):
            for pair in catalog["pairs"]:
                scenario = pair[difficulty]
                scenario_id = scenario["scenario_id"]
                score = metrics.get((scenario_id, "score"))
                rank = _rank(metrics.get((scenario_id, "scenario_rank")))
                if score is None or rank is None:
                    valid = False
                    break
                scenarios.append({
                    "difficulty": difficulty,
                    "scenario_name": scenario["scenario_name"],
                    "category": pair["category"],
                    "subcategory": pair["subcategory"],
                    "score": score,
                    "scenario_rank": rank,
                })
            if not valid:
                break
        ranks = {
            difficulty: _rank(metrics.get((f"benchmark:viscose-s2:{difficulty}", "overall_rank")))
            for difficulty in ("easier", "medium")
        }
        if not valid or any(rank is None for rank in ranks.values()) or len(metrics) != 158:
            continue
        candidates = sorted(
            scenarios,
            key=lambda item: (item["scenario_rank"], item["score"], item["difficulty"], item["scenario_name"]),
        )[:_BENCHMARK_REVIEW_CANDIDATE_LIMIT]
        return {
            "schema_version": BENCHMARK_SUMMARY_SCHEMA_VERSION,
            "catalog_ref": catalog["catalog_ref"],
            "catalog_version": catalog["catalog_version"],
            "observed_at": observed_at,
            "completion": {
                difficulty: {
                    "completed": sum(
                        1
                        for item in scenarios
                        if item["difficulty"] == difficulty and item["score"] > 0
                    ),
                    "required": 39,
                }
                for difficulty in ("easier", "medium")
            },
            "provisional_ranks": ranks,
            "scenarios": scenarios,
            "review_candidates": [dict(item) for item in candidates],
        }
    return None
