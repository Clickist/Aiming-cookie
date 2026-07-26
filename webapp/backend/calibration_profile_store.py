from __future__ import annotations

from typing import Any, Mapping

from .db import get_conn


def _profile(owner_id: str, row: Mapping[str, Any] | None = None, *, deletion_state: str | None = None) -> dict[str, Any]:
    configured = row is not None
    result: dict[str, Any] = {
        "schema_version": "calibration_profile.v1",
        "configured": configured,
        "values": {
            "cm_per_360": row["cm_per_360"] if row is not None else None,
            "fov": row["fov"] if row is not None else None,
        },
        "dpi": None,
        "sensitivity": None,
        "adoption_priority": [
            "stats", "manual_override", "profile_default", "undetermined",
        ],
        "updated_at": row["updated_at"] if row is not None else None,
    }
    if deletion_state is not None:
        result["deletion_state"] = deletion_state
    return result


async def get_profile(owner_id: str) -> dict[str, Any]:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT cm_per_360, fov, updated_at FROM calibration_profiles WHERE owner_id=?",
            (owner_id,),
        )
    ).fetchone()
    return _profile(owner_id, row)


async def save_profile(
    owner_id: str,
    *,
    cm_per_360: float | None,
    fov: float | None,
) -> dict[str, Any]:
    if cm_per_360 is None and fov is None:
        raise ValueError("cm_per_360 and fov cannot both be empty")
    if cm_per_360 is not None and not 0 < cm_per_360 <= 1000:
        raise ValueError("cm_per_360 must be between 0 and 1000")
    if fov is not None and not 0 < fov <= 180:
        raise ValueError("fov must be between 0 and 180")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO calibration_profiles(owner_id, cm_per_360, fov) VALUES(?, ?, ?) "
        "ON CONFLICT(owner_id) DO UPDATE SET cm_per_360=excluded.cm_per_360, "
        "fov=excluded.fov, updated_at=CURRENT_TIMESTAMP",
        (owner_id, cm_per_360, fov),
    )
    await conn.commit()
    return await get_profile(owner_id)


async def delete_profile(owner_id: str) -> dict[str, Any]:
    conn = await get_conn()
    cursor = await conn.execute(
        "DELETE FROM calibration_profiles WHERE owner_id=?", (owner_id,),
    )
    await conn.commit()
    return _profile(
        owner_id,
        deletion_state="completed" if cursor.rowcount else "already_absent",
    )
