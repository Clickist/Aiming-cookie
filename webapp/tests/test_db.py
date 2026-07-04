from __future__ import annotations

import pytest

from webapp.backend import db


@pytest.mark.asyncio
async def test_init_schema_creates_sessions_table():
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["name"] == "sessions"


@pytest.mark.asyncio
async def test_init_schema_creates_index():
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_sessions_user_status'"
    )
    row = await cur.fetchone()
    assert row is not None
