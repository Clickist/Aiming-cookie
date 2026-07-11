from __future__ import annotations

import os

import aiosqlite
import pytest

from webapp.backend import coach_store, db


V0_SESSIONS_SCHEMA = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'dev',
    status TEXT NOT NULL DEFAULT 'queued',
    video_path TEXT,
    csv_path TEXT,
    result TEXT,
    error TEXT,
    llm_cost_cny REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sessions_user_status ON sessions(user_id, status);
"""


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


@pytest.mark.asyncio
async def test_init_schema_migrates_v0_to_v2_transactionally():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute("PRAGMA user_version = 0")
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == 2

    cur = await conn.execute("PRAGMA table_info(sessions)")
    cols = {row[1] for row in await cur.fetchall()}
    for name in (
        "cm_per_360",
        "fov",
        "attempts",
        "max_attempts",
        "worker_id",
        "lease_expires_at",
        "heartbeat_at",
        "started_at",
        "finished_at",
    ):
        assert name in cols

    for table in ("coach_threads", "coach_messages", "coach_analysis_refs"):
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert await cur.fetchone() is not None

    cur = await conn.execute(
        "INSERT INTO sessions(user_id, video_path, csv_path) VALUES('u', '/v', '/c')"
    )
    await conn.commit()
    cur = await conn.execute(
        "SELECT attempts, max_attempts FROM sessions WHERE user_id='u'"
    )
    row = await cur.fetchone()
    assert row[0] == 0
    assert row[1] == 1


@pytest.mark.asyncio
async def test_init_schema_v1_to_v2_idempotent():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    # Build a v1-shaped DB (sessions+chat only, user_version=1)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'dev',
            status TEXT NOT NULL DEFAULT 'queued',
            video_path TEXT,
            csv_path TEXT,
            cm_per_360 REAL,
            fov REAL,
            result TEXT,
            error TEXT,
            llm_cost_cny REAL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            worker_id TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            trace_json TEXT
        );
        """
    )
    await conn.execute("PRAGMA user_version = 1")
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == 2
    for table in ("coach_threads", "coach_messages", "coach_analysis_refs"):
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert await cur.fetchone() is not None

    # second init stays at 2
    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_init_schema_rejects_newer_user_version():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute("PRAGMA user_version = 3")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="user_version"):
        await db.init_schema()


@pytest.mark.asyncio
async def test_primary_thread_unique_and_messages_order():
    t1 = await coach_store.get_or_create_primary_thread("user-a")
    t2 = await coach_store.get_or_create_primary_thread("user-a")
    assert t1["id"] == t2["id"]
    assert t1["kind"] == "primary"

    await coach_store.append_message(t1["id"], "user", "hello")
    await coach_store.append_message(t1["id"], "assistant", "hi")
    msgs = await coach_store.load_messages(t1["id"])
    assert [m["content"] for m in msgs] == ["hello", "hi"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_attach_ref_idempotent_and_mark_deleted():
    thread = await coach_store.get_or_create_primary_thread("user-b")
    conn = await db.get_conn()
    cur = await conn.execute(
        "INSERT INTO sessions(user_id, status, video_path, csv_path) "
        "VALUES('user-b', 'done', '/v', '/c') RETURNING id"
    )
    session_id = int((await cur.fetchone())[0])
    await conn.commit()

    r1 = await coach_store.attach_analysis_ref(thread["id"], session_id)
    r2 = await coach_store.attach_analysis_ref(thread["id"], session_id)
    assert r1["id"] == r2["id"]
    assert r1["status"] == "active"

    n = await coach_store.mark_analysis_refs_deleted(session_id)
    assert n == 1
    refs = await coach_store.list_analysis_refs(thread["id"])
    assert len(refs) == 1
    assert refs[0]["status"] == "deleted"
    assert refs[0]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_migrate_legacy_chat_messages_idempotent():
    conn = await db.get_conn()
    cur = await conn.execute(
        "INSERT INTO sessions(user_id, status, video_path, csv_path) "
        "VALUES('user-c', 'done', '/v2', '/c2') RETURNING id"
    )
    session_id = int((await cur.fetchone())[0])
    await conn.execute(
        "INSERT INTO chat_messages(session_id, role, content) VALUES(?, 'user', 'legacy1')",
        (session_id,),
    )
    await conn.execute(
        "INSERT INTO chat_messages(session_id, role, content) VALUES(?, 'assistant', 'legacy2')",
        (session_id,),
    )
    await conn.commit()

    first = await coach_store.migrate_legacy_chat_messages()
    assert first["messages_copied"] >= 2
    assert first["refs_created"] >= 1

    second = await coach_store.migrate_legacy_chat_messages()
    assert second["messages_copied"] == 0

    thread = await coach_store.get_or_create_primary_thread("user-c")
    msgs = await coach_store.load_messages(thread["id"])
    contents = [m["content"] for m in msgs]
    assert "legacy1" in contents
    assert "legacy2" in contents
    assert all(
        m["legacy_session_id"] == session_id
        for m in msgs
        if m["content"] in ("legacy1", "legacy2")
    )
