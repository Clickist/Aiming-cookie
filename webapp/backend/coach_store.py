"""Canonical Coach data store (user-owned primary thread).

Not the Pi runtime transcript. Analysis sessions are optional refs only.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import aiosqlite

from .db import get_conn


async def get_or_create_primary_thread(
    user_id: str,
    *,
    conn: Optional[aiosqlite.Connection] = None,
) -> dict[str, Any]:
    owns_commit = conn is None
    if conn is None:
        conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, kind, created_at, updated_at "
        "FROM coach_threads WHERE user_id=? AND kind='primary'",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is not None:
        return dict(row)

    cur = await conn.execute(
        "INSERT INTO coach_threads(user_id, kind) VALUES(?, 'primary') "
        "RETURNING id, user_id, kind, created_at, updated_at",
        (user_id,),
    )
    row = await cur.fetchone()
    if owns_commit:
        await conn.commit()
    return dict(row)


async def append_message(
    thread_id: int,
    role: str,
    content: str,
    *,
    trace: Optional[list] = None,
    legacy_session_id: Optional[int] = None,
) -> int:
    conn = await get_conn()
    trace_json = json.dumps(trace, ensure_ascii=False) if trace else None
    cur = await conn.execute(
        "INSERT INTO coach_messages("
        "thread_id, role, content, trace_json, legacy_session_id"
        ") VALUES(?, ?, ?, ?, ?) RETURNING id",
        (thread_id, role, content, trace_json, legacy_session_id),
    )
    row = await cur.fetchone()
    await conn.execute(
        "UPDATE coach_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (thread_id,),
    )
    await conn.commit()
    return int(row["id"])


async def load_messages(thread_id: int) -> list[dict[str, Any]]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, role, content, created_at, trace_json, legacy_session_id "
        "FROM coach_messages WHERE thread_id=? ORDER BY id",
        (thread_id,),
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        trace: list = []
        if r["trace_json"]:
            try:
                trace = json.loads(r["trace_json"])
            except (json.JSONDecodeError, TypeError):
                trace = []
        out.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "trace": trace,
            "legacy_session_id": r["legacy_session_id"],
        })
    return out


async def attach_analysis_ref(
    thread_id: int,
    analysis_session_id: int,
) -> dict[str, Any]:
    """Idempotent attach of an analysis session as an active ref."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, thread_id, analysis_session_id, status, attached_at, deleted_at "
        "FROM coach_analysis_refs "
        "WHERE thread_id=? AND analysis_session_id=? AND status='active'",
        (thread_id, analysis_session_id),
    )
    row = await cur.fetchone()
    if row is not None:
        return dict(row)

    cur = await conn.execute(
        "INSERT INTO coach_analysis_refs("
        "thread_id, analysis_session_id, status"
        ") VALUES(?, ?, 'active') "
        "RETURNING id, thread_id, analysis_session_id, status, attached_at, deleted_at",
        (thread_id, analysis_session_id),
    )
    row = await cur.fetchone()
    await conn.execute(
        "UPDATE coach_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (thread_id,),
    )
    await conn.commit()
    return dict(row)


async def mark_analysis_refs_deleted(
    analysis_session_id: int,
    *,
    conn: Optional[aiosqlite.Connection] = None,
) -> int:
    """Mark all active refs for a session as deleted. Returns rows updated."""
    owns_commit = conn is None
    if conn is None:
        conn = await get_conn()
    cur = await conn.execute(
        "UPDATE coach_analysis_refs "
        "SET status='deleted', deleted_at=CURRENT_TIMESTAMP "
        "WHERE analysis_session_id=? AND status='active'",
        (analysis_session_id,),
    )
    if owns_commit:
        await conn.commit()
    return int(cur.rowcount or 0)


async def list_analysis_refs(thread_id: int) -> list[dict[str, Any]]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, thread_id, analysis_session_id, status, attached_at, deleted_at "
        "FROM coach_analysis_refs WHERE thread_id=? ORDER BY id",
        (thread_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def _legacy_chat_id_migrated(
    conn: aiosqlite.Connection,
    legacy_chat_message_id: int,
) -> bool:
    cur = await conn.execute(
        "SELECT id FROM coach_messages WHERE legacy_chat_message_id=?",
        (legacy_chat_message_id,),
    )
    return await cur.fetchone() is not None


async def migrate_session_legacy_messages(
    session_id: int,
    *,
    conn: Optional[aiosqlite.Connection] = None,
) -> dict[str, int]:
    """Migrate chat_messages for one session into the user's primary thread.

    Idempotent via ``legacy_chat_message_id`` (chat_messages.id).
    Returns ``{messages_copied, refs_created}``.
    """
    owns_commit = conn is None
    if conn is None:
        conn = await get_conn()

    cur = await conn.execute(
        "SELECT user_id FROM sessions WHERE id=?",
        (session_id,),
    )
    session_row = await cur.fetchone()
    if session_row is None:
        user_id = "orphan"
        session_exists = False
    else:
        user_id = session_row["user_id"]
        session_exists = True

    thread = await get_or_create_primary_thread(user_id, conn=conn)
    thread_id = int(thread["id"])

    cur = await conn.execute(
        "SELECT id, role, content, created_at, trace_json "
        "FROM chat_messages WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    legacy_rows = await cur.fetchall()

    messages_copied = 0
    for lr in legacy_rows:
        legacy_id = int(lr["id"])
        if await _legacy_chat_id_migrated(conn, legacy_id):
            continue
        await conn.execute(
            "INSERT INTO coach_messages("
            "thread_id, role, content, created_at, trace_json, "
            "legacy_session_id, legacy_chat_message_id"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                lr["role"],
                lr["content"],
                lr["created_at"],
                lr["trace_json"],
                session_id,
                legacy_id,
            ),
        )
        messages_copied += 1

    refs_created = 0
    cur = await conn.execute(
        "SELECT id FROM coach_analysis_refs "
        "WHERE thread_id=? AND analysis_session_id=?",
        (thread_id, session_id),
    )
    if await cur.fetchone() is None:
        if session_exists:
            await conn.execute(
                "INSERT INTO coach_analysis_refs("
                "thread_id, analysis_session_id, status"
                ") VALUES(?, ?, 'active')",
                (thread_id, session_id),
            )
        else:
            await conn.execute(
                "INSERT INTO coach_analysis_refs("
                "thread_id, analysis_session_id, status, deleted_at"
                ") VALUES(?, ?, 'deleted', CURRENT_TIMESTAMP)",
                (thread_id, session_id),
            )
        refs_created = 1

    if owns_commit:
        await conn.commit()

    return {"messages_copied": messages_copied, "refs_created": refs_created}


async def migrate_legacy_chat_messages() -> dict[str, int]:
    """Idempotent migration from session-bound chat_messages (all sessions).

    Returns counts: {sessions_seen, messages_copied, refs_created}.
    """
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT DISTINCT session_id FROM chat_messages ORDER BY session_id"
    )
    session_ids = [int(r[0]) for r in await cur.fetchall()]

    messages_copied = 0
    refs_created = 0
    sessions_seen = 0

    for session_id in session_ids:
        sessions_seen += 1
        result = await migrate_session_legacy_messages(
            session_id, conn=conn,
        )
        messages_copied += result["messages_copied"]
        refs_created += result["refs_created"]

    await conn.commit()
    return {
        "sessions_seen": sessions_seen,
        "messages_copied": messages_copied,
        "refs_created": refs_created,
    }


async def ensure_legacy_session_messages_migrated(session_id: int) -> int:
    """Thin wrapper: migrate one session before session row removal."""
    if session_id <= 0:
        return 0
    cur_conn = await get_conn()
    cur = await cur_conn.execute(
        "SELECT id FROM sessions WHERE id=?",
        (session_id,),
    )
    if await cur.fetchone() is None:
        return 0
    result = await migrate_session_legacy_messages(session_id)
    return int(result["messages_copied"])