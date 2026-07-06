from __future__ import annotations

import json
from typing import Optional

import aiosqlite

from .config import DB_PATH

_conn: Optional[aiosqlite.Connection] = None


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
    cm_per_360 REAL,              -- 用户填(KovaaK's 实测 cm/360,worker 用于 peak_cm_per_s)
    fov REAL,                     -- 用户填或 CSV fallback(默认 103)
    result TEXT,
    error TEXT,
    llm_cost_cny REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,          -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,             -- 可选, tool 调用 trace(JSON 字符串)
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);
"""


async def init_schema() -> None:
    conn = await get_conn()
    await conn.executescript(SCHEMA)
    # Migration:已存在的旧 sessions 表补加新列(若无)。dev db 兼容旧 schema。
    await _migrate_add_column_if_missing(conn, "sessions", "cm_per_360", "REAL")
    await _migrate_add_column_if_missing(conn, "sessions", "fov", "REAL")
    await conn.commit()


async def _migrate_add_column_if_missing(
    conn: aiosqlite.Connection, table: str, col: str, col_type: str,
) -> None:
    """SQLite ALTER TABLE ADD COLUMN(仅当列不存在时)。CREATE TABLE IF NOT EXISTS
    不会给已存在的表加新列,所以要显式 migrate。

    安全性:table/col/col_type 来自 caller 写死的字面量(init_schema),非用户
    输入。SQLite 的 PRAGMA / ALTER TABLE 不接受 ? 占位符,只能 f-string 拼。
    assert 防御性校验标识符 + 类型白名单,阻断注入路径。
    """
    assert table.isidentifier(), f"非法 table 名: {table}"
    assert col.isidentifier(), f"非法 col 名: {col}"
    assert col_type.upper() in {"REAL", "INTEGER", "TEXT", "BLOB", "NUMERIC"}, (
        f"非法 col_type: {col_type}"
    )
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    existing = {row[1] for row in rows}
    if col not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


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
