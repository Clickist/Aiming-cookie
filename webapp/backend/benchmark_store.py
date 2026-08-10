"""Owner-scoped, provider-neutral local Benchmark records."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from .db import get_conn

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


async def create_record(user_id: str, record: dict[str, Any]) -> dict:
    value = _validate_record(record)
    conn = await get_conn()
    cur = await conn.execute(
        "INSERT INTO benchmark_records(user_id, provider, provider_license_note, "
        "catalog_version, scenario_id, metric_key, unit, value, observed_at, "
        "availability, external_identity_ref, identity_consent) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            user_id,
            value["provider"],
            value["provider_license_note"],
            value["catalog_version"],
            value["scenario_id"],
            value["metric_key"],
            value["unit"],
            value["value"],
            value["observed_at"],
            value["availability"],
            value["external_identity_ref"],
            1 if value["identity_consent"] else 0,
        ),
    )
    row = await cur.fetchone()
    await conn.commit()
    return await get_record(int(row["id"]), user_id)


async def create_records_atomically(
    user_id: str,
    records: list[dict[str, Any]],
) -> list[dict]:
    """Validate the complete snapshot before committing any of its records."""
    if not records:
        raise ValueError("records are required")
    values = [_validate_record(record) for record in records]
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        await conn.executemany(
            "INSERT INTO benchmark_records(user_id, provider, provider_license_note, "
            "catalog_version, scenario_id, metric_key, unit, value, observed_at, "
            "availability, external_identity_ref, identity_consent) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    value["provider"],
                    value["provider_license_note"],
                    value["catalog_version"],
                    value["scenario_id"],
                    value["metric_key"],
                    value["unit"],
                    value["value"],
                    value["observed_at"],
                    value["availability"],
                    value["external_identity_ref"],
                    1 if value["identity_consent"] else 0,
                )
                for value in values
            ],
        )
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise
    return values


async def get_record(record_id: int, user_id: str) -> dict | None:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, provider, provider_license_note, catalog_version, scenario_id, "
        "metric_key, unit, value, observed_at, availability, external_identity_ref, "
        "identity_consent, created_at FROM benchmark_records WHERE id=? AND user_id=?",
        (record_id, user_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["identity_consent"] = bool(out["identity_consent"])
    return out


async def list_records(user_id: str) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, provider, provider_license_note, catalog_version, scenario_id, "
        "metric_key, unit, value, observed_at, availability, external_identity_ref, "
        "identity_consent, created_at FROM benchmark_records WHERE user_id=? "
        "ORDER BY observed_at DESC, id DESC",
        (user_id,),
    )
    records = []
    for row in await cur.fetchall():
        out = dict(row)
        out["identity_consent"] = bool(out["identity_consent"])
        records.append(out)
    return records


async def list_latest_snapshot(
    user_id: str,
    *,
    provider: str,
    catalog_version: str,
    exact_record_count: int | None = None,
) -> list[dict]:
    """Return one owner-scoped successful import snapshot, never a mixed history."""
    conn = await get_conn()
    cur = await conn.execute(
        "WITH latest_snapshot AS ("
        "SELECT observed_at FROM benchmark_records "
        "WHERE user_id=? AND provider=? AND catalog_version=? AND availability='available' "
        "GROUP BY observed_at "
        "HAVING ? IS NULL OR COUNT(*)=? "
        "ORDER BY observed_at DESC LIMIT 1"
        ") "
        "SELECT id, provider, provider_license_note, catalog_version, scenario_id, "
        "metric_key, unit, value, observed_at, availability, external_identity_ref, "
        "identity_consent, created_at FROM benchmark_records "
        "WHERE user_id=? AND provider=? AND catalog_version=? "
        "AND availability='available' "
        "AND observed_at=(SELECT observed_at FROM latest_snapshot) "
        "ORDER BY id",
        (
            user_id,
            provider,
            catalog_version,
            exact_record_count,
            exact_record_count,
            user_id,
            provider,
            catalog_version,
        ),
    )
    records = []
    for row in await cur.fetchall():
        record = dict(row)
        record["identity_consent"] = bool(record["identity_consent"])
        records.append(record)
    return records


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
