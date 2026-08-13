from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import file_store

_PERIPHERAL_PATH = "config/peripheral.json"
_GRIP_TYPES = frozenset({"fingertip", "fingertip_claw", "claw", "claw_palm", "palm"})
_WRIST_POSITIONS = frozenset({"suspended", "on_pad"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _profile(owner_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    configured = row is not None
    return {
        "schema_version": "peripheral_profile.v1",
        "configured": configured,
        "grip_type": row.get("grip_type") if row else None,
        "hand_length_cm": row.get("hand_length_cm") if row else None,
        "wrist_position": row.get("wrist_position") if row else None,
        "grip_preference": row.get("grip_preference") if row else None,
        "current_mouse_brand": row.get("current_mouse_brand") if row else None,
        "current_mouse_model": row.get("current_mouse_model") if row else None,
        "current_mousepad": row.get("current_mousepad") if row else None,
        "budget": row.get("budget") if row else None,
        "updated_at": row.get("updated_at") if row else None,
    }


async def get_profile(owner_id: str) -> dict[str, Any]:
    data = file_store.read_json(_PERIPHERAL_PATH)
    if data is None:
        return _profile(owner_id, None)
    return _profile(owner_id, data)


_ALLOWED_FIELDS = frozenset({
    "grip_type", "hand_length_cm", "wrist_position", "grip_preference",
    "current_mouse_brand", "current_mouse_model", "current_mousepad", "budget",
})


def _validate(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "grip_type" and value not in _GRIP_TYPES:
        raise ValueError(f"grip_type must be one of {sorted(_GRIP_TYPES)}")
    if field == "wrist_position" and value not in _WRIST_POSITIONS:
        raise ValueError(f"wrist_position must be one of {sorted(_WRIST_POSITIONS)}")
    if field == "hand_length_cm":
        v = float(value)
        if not 5 <= v <= 30:
            raise ValueError("hand_length_cm must be between 5 and 30")
        return v
    if field in ("current_mouse_brand", "current_mouse_model", "current_mousepad",
                 "grip_preference", "budget"):
        s = str(value).strip()
        return s[:200] if s else None
    return value


async def update_profile(owner_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for field in _ALLOWED_FIELDS:
        if field in updates:
            validated[field] = _validate(field, updates[field])
    if not validated:
        raise ValueError("at least one field must be provided")

    existing = file_store.read_json(_PERIPHERAL_PATH) or {}
    existing.update(validated)
    existing["updated_at"] = _utc_now()
    file_store.write_json(_PERIPHERAL_PATH, existing)
    return await get_profile(owner_id)
