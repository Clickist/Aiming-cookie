"""Owner-scoped local KovaaK account connection."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .db import get_conn
from .kovaak_benchmark_provider import validate_steam_id


_owner_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def lock_owner_connection(owner_id: str):
    lock = _owner_locks.setdefault(owner_id, asyncio.Lock())
    async with lock:
        yield


async def get_connection(owner_id: str) -> dict | None:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT owner_id, steam_id, connected_at, updated_at "
            "FROM kovaak_connections WHERE owner_id=?",
            (owner_id,),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def save_connection(owner_id: str, steam_id: str) -> None:
    normalized = validate_steam_id(steam_id)
    async with lock_owner_connection(owner_id):
        existing = await get_connection(owner_id)
        conn = await get_conn()
        await conn.execute(
            "INSERT INTO kovaak_connections(owner_id, steam_id) VALUES(?, ?) "
            "ON CONFLICT(owner_id) DO UPDATE SET steam_id=excluded.steam_id, "
            "updated_at=CURRENT_TIMESTAMP",
            (owner_id, normalized),
        )
        if existing is not None and existing["steam_id"] != normalized:
            await conn.execute(
                "UPDATE benchmark_records SET availability='stale' "
                "WHERE user_id=? AND provider='kovaaks-webapp' AND availability='available'",
                (owner_id,),
            )
        await conn.commit()


async def delete_connection(owner_id: str) -> bool:
    async with lock_owner_connection(owner_id):
        conn = await get_conn()
        result = await conn.execute(
            "DELETE FROM kovaak_connections WHERE owner_id=?",
            (owner_id,),
        )
        await conn.commit()
        return bool(result.rowcount)
