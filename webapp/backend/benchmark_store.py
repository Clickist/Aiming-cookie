"""Owner-scoped, provider-neutral local Benchmark records (JSON file backed)."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from . import file_store

_SCORES_PATH = "training/scores.json"
_AVAILABILITY = {"available", "stale", "unavailable"}
_REQUIRED_TEXT = (
    "provider",
    "provider_license_note",
    "catalog_version",
    "scenario_id",
    "metric_key",
    "unit",
    "observed_at",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    for field in _REQUIRED_TEXT:
        value = out.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        out[field] = value.strip()
    try:
        datetime.fromisoformat(out["observed_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("observed_at must be ISO-8601") from error
    value = out.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be numeric")
    out["value"] = float(value)
    if not math.isfinite(out["value"]):
        raise ValueError("value must be finite")
    availability = out.get("availability", "available")
    if availability not in _AVAILABILITY:
        raise ValueError("invalid availability")
    out["availability"] = availability
    consent = bool(out.get("identity_consent", False))
    identity = out.get("external_identity_ref")
    if identity and not consent:
        raise ValueError("external identity requires explicit consent")
    if identity and (not isinstance(identity, str) or not identity.strip()):
        raise ValueError("external_identity_ref must be an opaque string")
    if identity and ("/" in identity or "\\" in identity):
        raise ValueError("external_identity_ref must not be a path")
    out["identity_consent"] = consent
    out["external_identity_ref"] = identity.strip() if isinstance(identity, str) else None
    return out


def _load_all() -> list[dict[str, Any]]:
    data = file_store.read_json(_SCORES_PATH)
    if data is None:
        return []
    return data if isinstance(data, list) else []


def _save_all(records: list[dict[str, Any]]) -> None:
    file_store.write_json(_SCORES_PATH, records)


def _next_id(records: list[dict[str, Any]]) -> int:
    return max((r.get("id", 0) for r in records), default=0) + 1


async def create_record(user_id: str, record: dict[str, Any]) -> dict:
    value = _validate_record(record)
    records = _load_all()
    value["id"] = _next_id(records)
    value["user_id"] = user_id
    value["created_at"] = _utc_now()
    records.append(value)
    _save_all(records)
    return await get_record(value["id"], user_id)


async def create_records_atomically(
    user_id: str,
    records: list[dict[str, Any]],
) -> list[dict]:
    if not records:
        raise ValueError("records are required")
    values = [_validate_record(record) for record in records]
    existing = _load_all()
    next_id = _next_id(existing)
    now = _utc_now()
    for value in values:
        value["id"] = next_id
        next_id += 1
        value["user_id"] = user_id
        value["created_at"] = now
        existing.append(value)
    _save_all(existing)
    return values


async def get_record(record_id: int, user_id: str) -> dict | None:
    for record in _load_all():
        if record.get("id") == record_id and record.get("user_id") == user_id:
            out = dict(record)
            out["identity_consent"] = bool(out.get("identity_consent"))
            return out
    return None


async def list_records(user_id: str) -> list[dict]:
    records = []
    for record in _load_all():
        if record.get("user_id") != user_id:
            continue
        out = dict(record)
        out["identity_consent"] = bool(out.get("identity_consent"))
        records.append(out)
    records.sort(key=lambda r: (r.get("observed_at", ""), r.get("id", 0)), reverse=True)
    return records


async def list_latest_snapshot(
    user_id: str,
    *,
    provider: str,
    catalog_version: str,
    exact_record_count: int | None = None,
) -> list[dict]:
    all_records = _load_all()
    matching = [
        r for r in all_records
        if r.get("user_id") == user_id
        and r.get("provider") == provider
        and r.get("catalog_version") == catalog_version
        and r.get("availability") == "available"
    ]
    if not matching:
        return []
    observed_at_values = {}
    for r in matching:
        observed_at_values.setdefault(r["observed_at"], []).append(r)
    sorted_dates = sorted(observed_at_values.keys(), reverse=True)
    for latest_date in sorted_dates:
        group = observed_at_values[latest_date]
        if exact_record_count is None or len(group) == exact_record_count:
            result = []
            for record in sorted(group, key=lambda r: r.get("id", 0)):
                out = dict(record)
                out["identity_consent"] = bool(out.get("identity_consent"))
                result.append(out)
            return result
    return []


def comparable(left: dict, right: dict) -> bool:
    fields = ("provider", "catalog_version", "scenario_id", "metric_key", "unit")
    if not all(left.get(field) == right.get(field) for field in fields):
        return False
    if left.get("availability") != "available" or right.get("availability") != "available":
        return False
    return all(
        isinstance(record.get("value"), (int, float))
        and not isinstance(record.get("value"), bool)
        and math.isfinite(float(record["value"]))
        for record in (left, right)
    )
