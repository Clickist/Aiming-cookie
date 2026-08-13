"""Owner-scoped local KovaaK account connection (JSON file backed)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from . import file_store
from .kovaak_benchmark_provider import validate_steam_id

_CONNECTION_PATH = "config/kovaak-connection.json"
_owner_locks: dict[str, asyncio.Lock] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@asynccontextmanager
async def lock_owner_connection(owner_id: str):
    lock = _owner_locks.setdefault(owner_id, asyncio.Lock())
    async with lock:
        yield


async def get_connection(owner_id: str) -> dict | None:
    data = file_store.read_json(_CONNECTION_PATH)
    if data is None:
        return None
    return {
        "owner_id": owner_id,
        "steam_id": data.get("steam_id"),
        "connected_at": data.get("connected_at"),
        "updated_at": data.get("updated_at"),
    }


async def save_connection(owner_id: str, steam_id: str) -> None:
    normalized = validate_steam_id(steam_id)
    async with lock_owner_connection(owner_id):
        existing = await get_connection(owner_id)
        record = {
            "steam_id": normalized,
            "connected_at": (existing.get("connected_at") if existing else _utc_now()),
            "updated_at": _utc_now(),
        }
        file_store.write_json(_CONNECTION_PATH, record)
        if existing is not None and existing.get("steam_id") != normalized:
            # Mark existing benchmark records as stale
            from . import benchmark_store
            records = await benchmark_store.list_records(owner_id)
            for r in records:
                if (
                    r.get("provider") == "kovaaks-webapp"
                    and r.get("availability") == "available"
                ):
                    r["availability"] = "stale"
            if records:
                file_store.write_json("training/scores.json", records)


async def delete_connection(owner_id: str) -> bool:
    async with lock_owner_connection(owner_id):
        return file_store.delete_file(_CONNECTION_PATH)
