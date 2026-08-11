"""EloShapes mouse catalog query for Coach peripheral recommendations."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_PATH = _REPO_ROOT / "artifacts" / "eloshapes" / "snapshots" / "eloshapes_mouse_catalog_2026-07-31T211736Z.json"
_MAPPING_PATH = _REPO_ROOT / "artifacts" / "eloshapes" / "marketplace-mapping" / "marketplace-mapping.json"

_OUTPUT_FIELDS = (
    "brand", "model", "variant", "shape", "weight", "length", "width", "height",
    "size_category", "front_flare", "side_curvature", "hump_placement",
    "thumb_rest", "ring_finger_rest", "hand_compatibility",
    "is_wired", "is_wireless_2_4_ghz", "is_bluetooth",
)


@lru_cache(maxsize=1)
def _load_catalog() -> list[dict[str, Any]]:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_mapping() -> dict[int, dict[str, Any]]:
    with open(_MAPPING_PATH, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", []) if isinstance(data, dict) else data
    return {
        e["eloshapes_id"]: e
        for e in entries
        if isinstance(e, dict) and e.get("mapping_status") == "identity_verified_candidate"
        and "eloshapes_id" in e
    }


def _project(mouse: dict[str, Any], eloshapes_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "eloshapes_id": eloshapes_id,
        "brand": " ".join(mouse.get("general__brand_names", []) or []),
        "model": mouse.get("general__model"),
        "variant": mouse.get("general__variant"),
    }
    for label in (
        "shape", "weight", "length", "width", "height",
        "size_category", "front_flare", "side_curvature", "hump_placement",
        "thumb_rest", "ring_finger_rest", "hand_compatibility",
        "is_wired", "is_wireless_2_4_ghz", "is_bluetooth",
    ):
        key = f"mouse__{label}"
        result[label] = mouse.get(key)
    return result


def query_mice(
    *,
    weight_max: float | None = None,
    size_category: str | list[str] | None = None,
    shape: str | None = None,
    front_flare: str | None = None,
    side_curvature: str | None = None,
    hump_placement: str | None = None,
    hand_compatibility: str | None = None,
    brand_search: str | None = None,
    model_search: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    catalog = _load_catalog()
    mapping = _load_mapping()

    size_set: set[str] | None = None
    if size_category:
        size_set = {size_category} if isinstance(size_category, str) else set(size_category)

    brand_lower = brand_search.lower().strip() if brand_search else None
    model_lower = model_search.lower().strip() if model_search else None

    candidates: list[dict[str, Any]] = []
    for mouse in catalog:
        if mouse.get("general__category") != "mouse":
            continue
        weight = mouse.get("mouse__weight")
        if weight_max is not None and (weight is None or weight > weight_max):
            continue
        if size_set and mouse.get("mouse__size_category") not in size_set:
            continue
        if shape and mouse.get("mouse__shape") != shape:
            continue
        if front_flare and mouse.get("mouse__front_flare") != front_flare:
            continue
        if side_curvature and mouse.get("mouse__side_curvature") != side_curvature:
            continue
        if hump_placement and mouse.get("mouse__hump_placement") != hump_placement:
            continue
        if hand_compatibility and mouse.get("mouse__hand_compatibility") != hand_compatibility:
            continue

        brand = " ".join(mouse.get("general__brand_names", []) or [])
        model = mouse.get("general__model", "")
        if brand_lower and brand_lower not in brand.lower():
            continue
        if model_lower and model_lower not in f"{brand} {model}".lower():
            continue

        elo_id = mouse.get("general__id")
        projected = _project(mouse, elo_id)
        jd_entry = mapping.get(elo_id)
        if jd_entry:
            projected["jd_product_id"] = jd_entry.get("product_id")
            projected["jd_canonical_url"] = jd_entry.get("canonical_url")
            projected["jd_match_confidence"] = jd_entry.get("match_confidence")
        else:
            projected["jd_product_id"] = None
        candidates.append(projected)

    candidates.sort(key=lambda m: (m.get("weight") or 999, m.get("brand", "")))

    return {
        "schema_version": "eloshapes_query.v1",
        "total_matches": len(candidates),
        "returned": min(len(candidates), limit),
        "snapshot_source": "eloshapes_mouse_catalog_2026-07-31T211736Z",
        "mice": candidates[:limit],
    }
