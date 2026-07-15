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


async def _coach_messages_column_names(conn) -> set[str]:
    cur = await conn.execute("PRAGMA table_info(coach_messages)")
    return {row[1] for row in await cur.fetchall()}


async def _has_legacy_chat_message_id_unique_index(conn) -> bool:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_coach_messages_legacy_chat_message_id'"
    )
    return await cur.fetchone() is not None


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
async def test_init_schema_migrates_v0_to_v12_transactionally():
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
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION

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

    assert "legacy_chat_message_id" in await _coach_messages_column_names(conn)
    assert await _has_legacy_chat_message_id_unique_index(conn)

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
async def test_init_schema_v1_to_v12_idempotent():
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
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
    for table in ("coach_threads", "coach_messages", "coach_analysis_refs"):
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert await cur.fetchone() is not None

    # second init stays at 11
    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION


@pytest.mark.asyncio
async def test_init_schema_rejects_newer_user_version():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute("PRAGMA user_version = 13")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="user_version"):
        await db.init_schema()


@pytest.mark.asyncio
async def test_init_schema_fresh_user_version_is_v12_with_legacy_column():
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
    cols = await _coach_messages_column_names(conn)
    assert "legacy_chat_message_id" in cols
    assert await _has_legacy_chat_message_id_unique_index(conn)


@pytest.mark.asyncio
async def test_init_schema_migrates_v2_to_v12_idempotent():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

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
        CREATE TABLE coach_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'primary',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, kind)
        );
        CREATE TABLE coach_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            trace_json TEXT,
            legacy_session_id INTEGER
        );
        CREATE TABLE coach_analysis_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            analysis_session_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            attached_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT
        );
        """
    )
    await conn.execute("PRAGMA user_version = 2")
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
    cols = await _coach_messages_column_names(conn)
    assert "legacy_chat_message_id" in cols
    assert await _has_legacy_chat_message_id_unique_index(conn)

    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION


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

    cur = await conn.execute(
        "SELECT id FROM chat_messages WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    legacy_ids = [int(r[0]) for r in await cur.fetchall()]
    assert len(legacy_ids) == 2

    cur = await conn.execute(
        "SELECT legacy_chat_message_id FROM coach_messages "
        "WHERE legacy_chat_message_id IS NOT NULL ORDER BY legacy_chat_message_id"
    )
    migrated_legacy_ids = [int(r[0]) for r in await cur.fetchall()]
    assert migrated_legacy_ids == legacy_ids

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

    third = await coach_store.migrate_session_legacy_messages(session_id)
    assert third["messages_copied"] == 0


@pytest.mark.asyncio
async def test_init_schema_creates_kovaak_runs_table_and_index():
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kovaak_runs'"
    )
    assert await cur.fetchone() is not None
    cur = await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_kovaak_runs_user_created'"
    )
    assert await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_init_schema_upgrades_v4_to_v12_with_trace_lifecycle_columns():
    conn = await db.get_conn()
    await conn.execute("DROP TABLE kovaak_runs")
    await conn.execute("PRAGMA user_version = 4")
    await conn.commit()
    await db.close_conn()

    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kovaak_runs'"
    )
    assert await cur.fetchone() is not None
    cur = await conn.execute("PRAGMA table_info(kovaak_runs)")
    columns = {row[1] for row in await cur.fetchall()}
    assert {"trace_state", "pending_trace_path", "trace_error"} <= columns

@pytest.mark.asyncio
async def test_v9_schema_contains_benchmark_and_coach_context_contracts():
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_records'"
    )
    assert await cur.fetchone() is not None
    cur = await conn.execute("PRAGMA table_info(coach_messages)")
    coach_columns = {row[1] for row in await cur.fetchall()}
    assert "context_json" in coach_columns
    cur = await conn.execute("PRAGMA table_info(sessions)")
    session_columns = {row[1] for row in await cur.fetchall()}
    assert {"analysis_type", "input_mode", "kovaak_run_id", "input_snapshot_json"} <= session_columns


@pytest.mark.asyncio
async def test_v9_schema_contains_owner_scoped_provider_profiles_contract():
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA table_info(provider_profiles)")
    columns = {row[1] for row in await cur.fetchall()}
    assert {
        "id",
        "owner_id",
        "name",
        "provider_id",
        "kind",
        "base_url",
        "model_id",
        "api_key",
        "is_default",
        "created_at",
        "updated_at",
    } <= columns

    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_provider_profiles_owner_default'"
    )
    assert await cur.fetchone() is not None

    cur = await conn.execute("SELECT COUNT(*) FROM provider_profiles")
    assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_connection_enforces_foreign_keys_for_relational_contracts():
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA foreign_keys")
    assert (await cur.fetchone())[0] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO training_plan_versions("
            "plan_id, version, plan_payload_json"
            ") VALUES('missing-plan', 1, '{}')"
        )


@pytest.mark.asyncio
async def test_v11_v12_migration_helpers_respect_caller_transaction():
    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await db._migrate_v11_training_plans(conn)
        await db._migrate_v12_coach_commands(conn)
        await conn.execute("ROLLBACK")

        for table in (
            "training_plans",
            "training_plan_versions",
            "training_plan_transitions",
            "coach_product_commands",
            "coach_command_idempotency",
            "coach_command_confirmations",
        ):
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert await cur.fetchone() is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_init_schema_migrates_v10_to_v12_training_plan_contract_idempotently():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute("PRAGMA user_version = 10")
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION


@pytest.mark.asyncio
async def test_v11_to_v12_adds_persistent_coach_command_contract_idempotently():
    await db.close_conn()
    db_path = "./aiming_cookie_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute("PRAGMA user_version = 11")
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    for table in (
        "coach_product_commands",
        "coach_command_idempotency",
        "coach_command_confirmations",
    ):
        row = await (
            await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,),
            )
        ).fetchone()
        assert row is not None

    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    for table in (
        "training_plans",
        "training_plan_versions",
        "training_plan_transitions",
    ):
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert await cur.fetchone() is not None
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_training_plans_one_active_per_owner'"
    )
    assert await cur.fetchone() is not None

    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
