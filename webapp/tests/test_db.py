from __future__ import annotations

import asyncio
import os
from pathlib import Path

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


EXPECTED_V13_ANALYSIS_DELETION_TOMBSTONES = """
CREATE TABLE IF NOT EXISTS analysis_deletion_tombstones (
    analysis_session_id INTEGER PRIMARY KEY CHECK(analysis_session_id > 0),
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    cleanup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(cleanup_state IN ('pending', 'failed')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(
        (cleanup_state = 'pending' AND cleanup_attempts = 0
            AND last_error_code IS NULL)
        OR
        (cleanup_state = 'failed' AND cleanup_attempts >= 1
            AND last_error_code IS NOT NULL
            AND last_error_code = 'workspace_cleanup_failed')
    )
);
"""


def _normalized_ddl(value: str) -> str:
    return " ".join(value.strip().rstrip(";").split())


async def _table_exists(conn, table: str) -> bool:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return await cur.fetchone() is not None


async def _coach_messages_column_names(conn) -> set[str]:
    cur = await conn.execute("PRAGMA table_info(coach_messages)")
    return {row[1] for row in await cur.fetchall()}


async def _has_legacy_chat_message_id_unique_index(conn) -> bool:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_coach_messages_legacy_chat_message_id'"
    )
    return await cur.fetchone() is not None


def _isolated_schema_db_path() -> str:
    path = os.path.abspath(db.DB_PATH)
    assert Path(path).parent != Path.cwd().resolve()
    return path


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
async def test_transaction_gate_allows_same_task_nesting_and_releases_after_cancellation():
    conn = await db.get_conn()
    entered = asyncio.Event()

    async def cancelled_transaction() -> None:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute("INSERT INTO sessions(user_id) VALUES('cancelled-owner')")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            if conn.in_transaction:
                await conn.rollback()

    task = asyncio.create_task(cancelled_transaction())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def nested_write() -> None:
        nested_conn = await db.get_conn()
        await nested_conn.execute("BEGIN IMMEDIATE")
        await nested_conn.execute("INSERT INTO sessions(user_id) VALUES('nested-owner')")
        row = await (
            await nested_conn.execute(
                "SELECT user_id FROM sessions WHERE user_id='nested-owner'",
            )
        ).fetchone()
        assert row["user_id"] == "nested-owner"
        await nested_conn.commit()

    await asyncio.wait_for(nested_write(), timeout=1)


@pytest.mark.asyncio
async def test_transaction_gate_rolls_back_abandoned_cancelled_owner():
    conn = await db.get_conn()
    entered = asyncio.Event()

    async def abandoned_transaction() -> None:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute("INSERT INTO sessions(user_id) VALUES('abandoned-owner')")
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(abandoned_transaction())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def write_after_abandonment() -> None:
        await conn.execute("INSERT INTO sessions(user_id) VALUES('after-abandonment')")
        await conn.commit()

    await asyncio.wait_for(write_after_abandonment(), timeout=1)
    rows = await (
        await conn.execute(
            "SELECT user_id FROM sessions WHERE user_id IN "
            "('abandoned-owner', 'after-abandonment') ORDER BY user_id",
        )
    ).fetchall()
    assert [row["user_id"] for row in rows] == ["after-abandonment"]


@pytest.mark.asyncio
async def test_transaction_gate_serializes_normal_concurrent_writes():
    async def write_user(user_id: str) -> None:
        conn = await db.get_conn()
        await conn.execute("INSERT INTO sessions(user_id) VALUES(?)", (user_id,))
        await conn.commit()

    await asyncio.wait_for(
        asyncio.gather(write_user("concurrent-a"), write_user("concurrent-b")),
        timeout=1,
    )
    conn = await db.get_conn()
    rows = await (
        await conn.execute(
            "SELECT user_id FROM sessions WHERE user_id IN ('concurrent-a', 'concurrent-b') "
            "ORDER BY user_id",
        )
    ).fetchall()
    assert [row["user_id"] for row in rows] == ["concurrent-a", "concurrent-b"]


@pytest.mark.asyncio
async def test_init_schema_migrates_v0_to_v13_transactionally():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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
async def test_init_schema_v1_to_v13_idempotent():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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

    # Second init stays at the current target schema.
    await db.close_conn()
    await db.init_schema()
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION


@pytest.mark.asyncio
async def test_init_schema_rejects_newer_user_version():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(V0_SESSIONS_SCHEMA)
    await conn.execute(f"PRAGMA user_version = {db.TARGET_USER_VERSION + 1}")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="user_version"):
        await db.init_schema()


@pytest.mark.asyncio
async def test_init_schema_fresh_user_version_is_v13_with_legacy_column():
    conn = await db.get_conn()
    cur = await conn.execute("PRAGMA user_version")
    assert (await cur.fetchone())[0] == db.TARGET_USER_VERSION
    cols = await _coach_messages_column_names(conn)
    assert "legacy_chat_message_id" in cols
    assert await _has_legacy_chat_message_id_unique_index(conn)


@pytest.mark.asyncio
async def test_init_schema_migrates_v2_to_v13_idempotent():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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
async def test_init_schema_upgrades_v4_to_v13_with_trace_lifecycle_columns():
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
async def test_v16_profile_and_plan_loop_tables_are_present_and_migration_is_transactional():
    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await db._migrate_v16_profile_plan_loop(conn)
        await conn.execute("ROLLBACK")
        for table in (
            "profile_contributions",
            "profile_contribution_revisions",
            "profile_contribution_tombstones",
            "aiming_profile_state",
            "aiming_profile_dimensions",
            "training_plan_items",
            "training_plan_item_statuses",
            "training_plan_executions",
            "training_plan_retests",
        ):
            row = await (
                await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,),
                )
            ).fetchone()
            assert row is None
    finally:
        await conn.close()


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
async def test_init_schema_migrates_v10_to_v13_training_plan_contract_idempotently():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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
async def test_v11_to_v13_adds_persistent_coach_command_contract_idempotently():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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


@pytest.mark.asyncio
async def test_fresh_v14_schema_uses_v13_helper_and_exact_tombstone_ddl(
    monkeypatch,
):
    await db.close_conn()
    db_path = _isolated_schema_db_path()
    if os.path.exists(db_path):
        os.remove(db_path)

    original_migration = db._migrate_v13_analysis_deletion_tombstones
    calls = 0

    async def tracked_migration(conn):
        nonlocal calls
        calls += 1
        await original_migration(conn)

    monkeypatch.setattr(
        db,
        "_migrate_v13_analysis_deletion_tombstones",
        tracked_migration,
    )

    await db.init_schema()
    conn = await db.get_conn()

    assert db.TARGET_USER_VERSION == 16
    assert calls == 1
    assert "analysis_deletion_tombstones" not in db.SCHEMA
    assert _normalized_ddl(db._V13_ANALYSIS_DELETION_TOMBSTONES) == _normalized_ddl(
        EXPECTED_V13_ANALYSIS_DELETION_TOMBSTONES
    )

    row = await (
        await conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='analysis_deletion_tombstones'"
        )
    ).fetchone()
    assert row is not None
    stored_ddl = _normalized_ddl(row[0])
    expected_stored_ddl = _normalized_ddl(
        EXPECTED_V13_ANALYSIS_DELETION_TOMBSTONES
    ).replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)
    assert stored_ddl == expected_stored_ddl

    columns = await (await conn.execute(
        "PRAGMA table_info(analysis_deletion_tombstones)"
    )).fetchall()
    assert [
        (row[1], row[2], row[3], row[4], row[5])
        for row in columns
    ] == [
        ("analysis_session_id", "INTEGER", 0, None, 1),
        ("owner_id", "TEXT", 1, None, 0),
        ("cleanup_state", "TEXT", 1, "'pending'", 0),
        ("cleanup_attempts", "INTEGER", 1, "0", 0),
        ("last_error_code", "TEXT", 0, None, 0),
        ("created_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
        ("updated_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
    ]
    assert not any("path" in row[1].casefold() for row in columns)
    assert await (await conn.execute(
        "PRAGMA foreign_key_list(analysis_deletion_tombstones)"
    )).fetchall() == []
    assert await (await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='analysis_deletion_tombstones'"
    )).fetchall() == []

    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
        "VALUES(1, 'owner-a')"
    )
    pending = await (
        await conn.execute(
            "SELECT cleanup_state, cleanup_attempts, last_error_code, "
            "created_at, updated_at FROM analysis_deletion_tombstones "
            "WHERE analysis_session_id=1"
        )
    ).fetchone()
    assert tuple(pending[:3]) == ("pending", 0, None)
    assert pending[3]
    assert pending[4]

    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones("
        "analysis_session_id, owner_id, cleanup_state, cleanup_attempts, last_error_code"
        ") VALUES(2, 'owner-a', 'failed', 1, 'workspace_cleanup_failed')"
    )


@pytest.mark.asyncio
async def test_init_schema_upgrades_v12_to_v15_with_tombstone_contract():
    conn = await db.get_conn()
    await conn.execute("DROP TABLE IF EXISTS analysis_deletion_tombstones")
    await conn.execute("PRAGMA user_version = 12")
    await conn.commit()
    await db.close_conn()

    await db.init_schema()
    conn = await db.get_conn()

    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    assert await _table_exists(conn, "analysis_deletion_tombstones")


@pytest.mark.asyncio
async def test_init_schema_v15_second_call_is_idempotent_and_preserves_tombstone():
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
        "VALUES(101, 'owner-idempotent')"
    )
    await conn.commit()
    await db.close_conn()

    await db.init_schema()
    conn = await db.get_conn()

    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    row = await (
        await conn.execute(
            "SELECT owner_id, cleanup_state, cleanup_attempts, last_error_code "
            "FROM analysis_deletion_tombstones WHERE analysis_session_id=101"
        )
    ).fetchone()
    assert tuple(row) == ("owner-idempotent", "pending", 0, None)


@pytest.mark.asyncio
async def test_v13_migration_helper_respects_caller_transaction_rollback():
    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await db._migrate_v13_analysis_deletion_tombstones(conn)
        assert await _table_exists(conn, "analysis_deletion_tombstones")

        await conn.execute("ROLLBACK")

        assert not await _table_exists(conn, "analysis_deletion_tombstones")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_v12_to_v13_init_schema_rolls_back_table_and_version_on_failure(
    monkeypatch,
):
    conn = await db.get_conn()
    await conn.execute("DROP TABLE IF EXISTS analysis_deletion_tombstones")
    await conn.execute("PRAGMA user_version = 12")
    await conn.commit()
    original_migration = db._migrate_v13_analysis_deletion_tombstones

    async def fail_after_table_creation(migration_conn):
        await original_migration(migration_conn)
        assert await _table_exists(
            migration_conn,
            "analysis_deletion_tombstones",
        )
        raise RuntimeError("injected failure after v13 table creation")

    monkeypatch.setattr(
        db,
        "_migrate_v13_analysis_deletion_tombstones",
        fail_after_table_creation,
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        await db.init_schema()

    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == 12
    assert not await _table_exists(conn, "analysis_deletion_tombstones")


@pytest.mark.parametrize(
    ("analysis_session_id", "owner_id", "state", "attempts", "error_code"),
    [
        (0, "owner-a", "pending", 0, None),
        (-1, "owner-a", "pending", 0, None),
        (1, "", "pending", 0, None),
        (1, "   ", "pending", 0, None),
        (1, "owner-a", "complete", 0, None),
        (1, "owner-a", "pending", -1, None),
        (1, "owner-a", "pending", 1, None),
        (1, "owner-a", "pending", 0, "workspace_cleanup_failed"),
        (1, "owner-a", "failed", 0, "workspace_cleanup_failed"),
        (1, "owner-a", "failed", 1, None),
        (1, "owner-a", "failed", 1, "raw OSError or path"),
    ],
)
@pytest.mark.asyncio
async def test_v13_tombstone_rejects_invalid_identity_and_cleanup_state_combinations(
    analysis_session_id: int,
    owner_id: str,
    state: str,
    attempts: int,
    error_code: str | None,
):
    conn = await db.get_conn()

    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO analysis_deletion_tombstones("
            "analysis_session_id, owner_id, cleanup_state, cleanup_attempts, "
            "last_error_code) VALUES(?, ?, ?, ?, ?)",
            (analysis_session_id, owner_id, state, attempts, error_code),
        )


@pytest.mark.asyncio
async def test_v14_to_v15_adds_run_evidence_tombstones_without_changing_runs():
    conn = await db.get_conn()
    cur = await conn.execute(
        "INSERT INTO kovaak_runs(user_id, source_key, video_state, video_path) "
        "VALUES('u1', 'migration-run', 'attached', '/private/video.mp4') RETURNING id"
    )
    run_id = (await cur.fetchone())[0]
    await conn.execute("PRAGMA user_version = 14")
    await conn.commit()
    await db.close_conn()

    await db.init_schema()
    conn = await db.get_conn()
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    row = await (
        await conn.execute(
            "SELECT user_id, source_key, video_state, video_path FROM kovaak_runs "
            "WHERE id=?", (run_id,),
        )
    ).fetchone()
    assert tuple(row) == ("u1", "migration-run", "attached", "/private/video.mp4")
    assert await _table_exists(conn, "run_evidence_deletion_tombstones")
    columns = await (
        await conn.execute("PRAGMA table_info(run_evidence_deletion_tombstones)")
    ).fetchall()
    assert [row[1] for row in columns] == [
        "run_id", "evidence_kind", "owner_id", "artifact_relpath",
        "expected_sha256", "expected_size", "cleanup_state", "cleanup_attempts",
        "last_error_code", "created_at", "updated_at",
    ]


@pytest.mark.asyncio
async def test_init_schema_migrates_v13_to_v15_preserving_run_and_session_rows():
    await db.close_conn()
    db_path = _isolated_schema_db_path()
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
            result TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE kovaak_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'desktop-local',
            source_key TEXT NOT NULL,
            scenario TEXT,
            stats_path TEXT,
            performance_path TEXT,
            mouse_trace_path TEXT,
            trace_state TEXT NOT NULL DEFAULT 'none',
            pending_trace_path TEXT,
            trace_error TEXT,
            stats_summary TEXT,
            performance_summary TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, source_key)
        );
        INSERT INTO sessions(id, user_id, status, video_path, csv_path)
        VALUES(7, 'owner-v13', 'succeeded', '/user/video.mp4', '/user/stats.csv');
        INSERT INTO kovaak_runs(
            id, user_id, source_key, scenario, stats_path, performance_path,
            mouse_trace_path, trace_state, stats_summary, performance_summary
        ) VALUES(
            9, 'owner-v13', 'source-v13', 'Scenario', '/user/Stats.csv',
            '/user/Performance.perf', '/managed/trace.bin', 'attached',
            '{"score":123}', '{"event_count":4}'
        );
        PRAGMA user_version = 13;
        """
    )
    await conn.commit()
    await conn.close()

    await db.init_schema()
    conn = await db.get_conn()

    assert db.TARGET_USER_VERSION == 16
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == db.TARGET_USER_VERSION
    session = await (
        await conn.execute(
            "SELECT id, user_id, status, video_path, csv_path FROM sessions WHERE id=7"
        )
    ).fetchone()
    assert tuple(session) == (
        7,
        "owner-v13",
        "succeeded",
        "/user/video.mp4",
        "/user/stats.csv",
    )
    run = await (
        await conn.execute(
            "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
            "mouse_trace_path, trace_state, stats_summary, performance_summary "
            "FROM kovaak_runs WHERE id=9"
        )
    ).fetchone()
    assert tuple(run) == (
        9,
        "owner-v13",
        "source-v13",
        "Scenario",
        "/user/Stats.csv",
        "/user/Performance.perf",
        "/managed/trace.bin",
        "attached",
        '{"score":123}',
        '{"event_count":4}',
    )
    columns = {
        row[1]
        for row in await (await conn.execute("PRAGMA table_info(kovaak_runs)")).fetchall()
    }
    assert {
        "capture_session_id",
        "window_start_epoch_ms",
        "window_end_epoch_ms",
        "alignment_state",
        "alignment_summary",
        "finalization_state",
        "finalization_error",
        "video_path",
        "video_state",
        "pending_video_path",
        "video_request_digest",
        "video_receipt_json",
        "video_summary_json",
        "video_error",
    } <= columns
