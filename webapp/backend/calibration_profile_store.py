from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from . import file_store

_CALIBRATION_PATH = "config/calibration.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _profile(owner_id: str, row: Mapping[str, Any] | None = None, *, deletion_state: str | None = None) -> dict[str, Any]:
    configured = row is not None
    result: dict[str, Any] = {
        "schema_version": "calibration_profile.v1",
        "configured": configured,
        "values": {
            "cm_per_360": row.get("cm_per_360") if row is not None else None,
            "fov": row.get("fov") if row is not None else None,
        },
        "dpi": None,
        "sensitivity": None,
        "adoption_priority": [
            "stats", "manual_override", "profile_default", "undetermined",
        ],
        "updated_at": row.get("updated_at") if row is not None else None,
    }
    if deletion_state is not None:
        result["deletion_state"] = deletion_state
    return result


async def get_profile(owner_id: str) -> dict[str, Any]:
    data = file_store.read_json(_CALIBRATION_PATH)
    if data is None:
        return _profile(owner_id)
    return _profile(owner_id, data)


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
    record = {"cm_per_360": cm_per_360, "fov": fov, "updated_at": _utc_now()}
    file_store.write_json(_CALIBRATION_PATH, record)
    return await get_profile(owner_id)


async def delete_profile(owner_id: str) -> dict[str, Any]:
    existed = file_store.delete_file(_CALIBRATION_PATH)
    return _profile(
        owner_id,
        deletion_state="completed" if existed else "already_absent",
    )
