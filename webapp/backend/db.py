from __future__ import annotations

import json
from typing import Optional

import aiosqlite

from .config import DB_PATH

_conn: Optional[aiosqlite.Connection] = None

TARGET_USER_VERSION = 2


async def get_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.commit()
    return _conn


async def close_conn() -> None:
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
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
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS coach_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'primary',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, kind)
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,
    legacy_session_id INTEGER,
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_messages_thread
    ON coach_messages(thread_id, id);

CREATE TABLE IF NOT EXISTS coach_analysis_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    analysis_session_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    attached_at TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_refs_thread
    ON coach_analysis_refs(thread_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_refs_thread_session_active
    ON coach_analysis_refs(thread_id, analysis_session_id)
    WHERE status = 'active' AND analysis_session_id IS NOT NULL;
"""

_V1_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cm_per_360", "REAL"),
    ("fov", "REAL"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("max_attempts", "INTEGER NOT NULL DEFAULT 1"),
    ("worker_id", "TEXT"),
    ("lease_expires_at", "TEXT"),
    ("heartbeat_at", "TEXT"),
    ("started_at", "TEXT"),
    ("finished_at", "TEXT"),
)


async def init_schema() -> None:
    conn = await get_conn()
    cur = await conn.execute("PRAGMA user_version")
    row = await cur.fetchone()
    user_version = int(row[0]) if row else 0

    if user_version > TARGET_USER_VERSION:
        raise RuntimeError(
            f"数据库 PRAGMA user_version={user_version} 高于本应用支持的 "
            f"version {TARGET_USER_VERSION}；请升级应用，不得静默降级。"
        )

    await conn.executescript(SCHEMA)

    if user_version >= TARGET_USER_VERSION:
        await conn.commit()
        return

    await conn.execute("BEGIN IMMEDIATE")
    try:
        if user_version == 0:
            for col, col_def in _V1_MIGRATION_COLUMNS:
                await _migrate_add_column_if_missing(conn, "sessions", col, col_def)
        # user_version 0 or 1 → 2: coach tables already created via SCHEMA
        await conn.execute(f"PRAGMA user_version = {TARGET_USER_VERSION}")
        await conn.commit()
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def _migrate_add_column_if_missing(
    conn: aiosqlite.Connection, table: str, col: str, col_def: str,
) -> None:
    """SQLite ALTER TABLE ADD COLUMN(仅当列不存在时)。"""
    assert table.isidentifier(), f"非法 table 名: {table}"
    assert col.isidentifier(), f"非法 col 名: {col}"
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    existing = {row[1] for row in rows}
    if col not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")


async def save_chat_message(
    session_id: int,
    role: str,
    content: str,
    trace: Optional[list] = None,
) -> int:
    """Append one chat message. ``trace`` is the agent loop's trace list
    (one entry per tool call) — JSON-serialized for observability."""
    conn = await get_conn()
    trace_json = json.dumps(trace, ensure_ascii=False) if trace else None
    cur = await conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, trace_json) "
        "VALUES(?, ?, ?, ?) RETURNING id",
        (session_id, role, content, trace_json),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row["id"]


async def load_chat_history(session_id: int) -> list[dict]:
    """Load chat history ordered by id (chronological).

    Returns list of ``{role, content, created_at, trace}`` dicts. ``trace``
    is parsed from ``trace_json`` when present (else empty list).
    """
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT role, content, created_at, trace_json FROM chat_messages "
        "WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        trace: list = []
        if r["trace_json"]:
            try:
                trace = json.loads(r["trace_json"])
            except (json.JSONDecodeError, TypeError):
                trace = []
        out.append({
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "trace": trace,
        })
    return out
