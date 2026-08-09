"""Canonical Coach data store (user-owned primary thread).

Not the Pi runtime transcript. Analysis sessions are optional refs only.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiosqlite

from .db import get_conn


class CommandIdempotencyConflictError(RuntimeError):
    """A journal key already belongs to a different parameter digest."""


def _json_wire(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def _store_command_idempotency_row(
    conn: aiosqlite.Connection,
    owner_id: str,
    command_name: str,
    idempotency_key: str,
    parameters_digest: str,
    result: Mapping[str, Any],
) -> None:
    cur = await conn.execute(
        "INSERT INTO coach_command_idempotency("
        "owner_id, command_name, idempotency_key, parameters_digest, result_json, latest_audit_ref"
        ") VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(owner_id, command_name, idempotency_key) DO UPDATE SET "
        "result_json=excluded.result_json, latest_audit_ref=excluded.latest_audit_ref, "
        "updated_at=CURRENT_TIMESTAMP "
        "WHERE coach_command_idempotency.parameters_digest=excluded.parameters_digest",
        (
            owner_id,
            command_name,
            idempotency_key,
            parameters_digest,
            _json_wire(result),
            result["audit_ref"],
        ),
    )
    if cur.rowcount != 1:
        raise CommandIdempotencyConflictError(
            "idempotency key was already used with different parameters"
        )


def _command_idempotency_record(row: aiosqlite.Row) -> dict[str, Any]:
    try:
        result = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("stored Coach command result is invalid") from exc
    if not isinstance(result, dict):
        raise RuntimeError("stored Coach command result is invalid")
    return {"digest": row["parameters_digest"], "result": result}


async def lookup_command_idempotency(
    owner_id: str,
    command_name: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT parameters_digest, result_json FROM coach_command_idempotency "
        "WHERE owner_id=? AND command_name=? AND idempotency_key=?",
        (owner_id, command_name, idempotency_key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _command_idempotency_record(row)


async def claim_command_idempotency(
    owner_id: str,
    command_name: str,
    idempotency_key: str,
    parameters_digest: str,
    reservation: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Insert one pre-side-effect reservation or return the existing record."""
    conn = await get_conn()
    try:
        cur = await conn.execute(
            "INSERT INTO coach_command_idempotency("
            "owner_id, command_name, idempotency_key, parameters_digest, result_json, latest_audit_ref"
            ") VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, command_name, idempotency_key) DO NOTHING",
            (
                owner_id,
                command_name,
                idempotency_key,
                parameters_digest,
                _json_wire(reservation),
                reservation["audit_ref"],
            ),
        )
        if cur.rowcount == 1:
            await conn.commit()
            return None
        row = await (
            await conn.execute(
                "SELECT parameters_digest, result_json FROM coach_command_idempotency "
                "WHERE owner_id=? AND command_name=? AND idempotency_key=?",
                (owner_id, command_name, idempotency_key),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("stored Coach command reservation is missing")
        prior = _command_idempotency_record(row)
        if prior["digest"] != parameters_digest:
            raise CommandIdempotencyConflictError(
                "idempotency key was already used with different parameters"
            )
        await conn.commit()
        return prior
    except Exception:
        await conn.rollback()
        raise


async def store_command_idempotency(
    owner_id: str,
    command_name: str,
    idempotency_key: str,
    parameters_digest: str,
    result: Mapping[str, Any],
) -> None:
    conn = await get_conn()
    try:
        await _store_command_idempotency_row(
            conn,
            owner_id,
            command_name,
            idempotency_key,
            parameters_digest,
            result,
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def append_command_audit(
    owner_id: str,
    result: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Append one safe audit row; never overwrite a prior invocation."""
    conn = await get_conn()
    warning = result.get("warning_or_error")
    await conn.execute(
        "INSERT INTO coach_product_commands("
        "audit_ref, command_id, owner_id, thread_id, user_message_ref, command_name, "
        "risk, authorization_source, idempotency_key, parameters_digest, "
        "safe_parameters_summary_json, status, result_ref, ui_event_json, "
        "warning_code, result_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            result["audit_ref"],
            result["command_id"],
            owner_id,
            context.get("thread_id"),
            context.get("user_message_ref"),
            context.get("command_name", "unknown"),
            context.get("risk", "query"),
            context.get("authorization_source", "system_safe"),
            context.get("idempotency_key"),
            context.get("parameters_digest"),
            _json_wire(context.get("safe_parameters_summary", {})),
            result["status"],
            result.get("result_ref"),
            _json_wire(result.get("ui_event")) if result.get("ui_event") is not None else None,
            warning.get("code") if isinstance(warning, Mapping) else None,
            _json_wire(result),
        ),
    )
    idempotency_key = context.get("idempotency_key")
    if isinstance(idempotency_key, str):
        await conn.execute(
            "UPDATE coach_command_idempotency SET latest_audit_ref=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE owner_id=? AND command_name=? AND idempotency_key=?",
            (result["audit_ref"], owner_id, context.get("command_name"), idempotency_key),
        )
    await conn.commit()


async def create_command_confirmation(
    owner_id: str,
    command_name: str,
    parameters_digest: str,
    risk: str,
    safe_summary: Mapping[str, Any],
    parameters: Mapping[str, Any],
    confirmation_ref: str,
    *,
    idempotency_key: str | None,
    thread_id: int | None,
    user_message_ref: str | None,
    ttl_seconds: int = 15 * 60,
) -> dict[str, str]:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    expires_wire = expires_at.isoformat().replace("+00:00", "Z")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO coach_command_confirmations("
        "confirmation_ref, owner_id, command_name, parameters_digest, risk, "
        "safe_summary_json, parameters_json, idempotency_key, thread_id, "
        "user_message_ref, status, expires_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            confirmation_ref,
            owner_id,
            command_name,
            parameters_digest,
            risk,
            _json_wire(safe_summary),
            _json_wire(parameters),
            idempotency_key,
            thread_id,
            user_message_ref,
            expires_wire,
        ),
    )
    await conn.commit()
    return {"confirmation_ref": confirmation_ref, "expires_at": expires_wire}


async def cancel_command_confirmation(owner_id: str, confirmation_ref: str) -> bool:
    conn = await get_conn()
    cursor = await conn.execute(
        "UPDATE coach_command_confirmations SET status='cancelled' "
        "WHERE confirmation_ref=? AND owner_id=? AND status='pending'",
        (confirmation_ref, owner_id),
    )
    await conn.commit()
    return cursor.rowcount == 1


async def consume_command_confirmation(
    owner_id: str,
    command_name: str,
    parameters_digest: str,
    confirmation_ref: str,
) -> bool:
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT status, expires_at, parameters_digest FROM coach_command_confirmations "
            "WHERE confirmation_ref=? AND owner_id=? AND command_name=?",
            (confirmation_ref, owner_id, command_name),
        )
        row = await cur.fetchone()
        if row is None or row["status"] != "pending" or row["parameters_digest"] != parameters_digest:
            await conn.execute("ROLLBACK")
            return False
        expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if expires_at <= datetime.now(timezone.utc):
            await conn.execute(
                "UPDATE coach_command_confirmations SET status='cancelled' WHERE confirmation_ref=?",
                (confirmation_ref,),
            )
            await conn.commit()
            return False
        cur = await conn.execute(
            "UPDATE coach_command_confirmations SET status='consumed', consumed_at=CURRENT_TIMESTAMP "
            "WHERE confirmation_ref=? AND status='pending'",
            (confirmation_ref,),
        )
        await conn.commit()
        return cur.rowcount == 1
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def consume_command_confirmation_and_store_reservation(
    owner_id: str,
    command_name: str,
    parameters_digest: str,
    confirmation_ref: str,
    idempotency_key: str,
    reservation: Mapping[str, Any],
) -> bool:
    """Atomically consume one confirmation and persist its unknown-outcome guard."""
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT status, expires_at, parameters_digest FROM coach_command_confirmations "
            "WHERE confirmation_ref=? AND owner_id=? AND command_name=?",
            (confirmation_ref, owner_id, command_name),
        )
        row = await cur.fetchone()
        if row is None or row["status"] != "pending" or row["parameters_digest"] != parameters_digest:
            await conn.execute("ROLLBACK")
            return False
        expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if expires_at <= datetime.now(timezone.utc):
            await conn.execute(
                "UPDATE coach_command_confirmations SET status='cancelled' WHERE confirmation_ref=?",
                (confirmation_ref,),
            )
            await conn.commit()
            return False
        cur = await conn.execute(
            "UPDATE coach_command_confirmations SET status='consumed', consumed_at=CURRENT_TIMESTAMP "
            "WHERE confirmation_ref=? AND status='pending'",
            (confirmation_ref,),
        )
        if cur.rowcount != 1:
            await conn.execute("ROLLBACK")
            return False
        await _store_command_idempotency_row(
            conn,
            owner_id,
            command_name,
            idempotency_key,
            parameters_digest,
            reservation,
        )
        await conn.commit()
        return True
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def get_or_create_primary_thread(
    user_id: str,
    *,
    conn: Optional[aiosqlite.Connection] = None,
) -> dict[str, Any]:
    owns_commit = conn is None
    if conn is None:
        conn = await get_conn()
    # A partial unique index protects one primary per owner while allowing
    # multiple conversation rows. INSERT OR IGNORE targets that index without
    # requiring the removed table-level (user_id, kind) uniqueness contract.
    await conn.execute(
        "UPDATE coach_threads SET status='active', deleted_at=NULL, "
        "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND kind='primary' "
        "AND status='deleted'",
        (user_id,),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO coach_threads(user_id, kind) VALUES(?, 'primary')",
        (user_id,),
    )
    cur = await conn.execute(
        "SELECT id, user_id, kind, created_at, updated_at "
        "FROM coach_threads WHERE user_id=? AND kind='primary' "
        "AND status <> 'deleted'",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("primary Coach thread is missing after insert")
    if owns_commit:
        await conn.commit()
    return dict(row)


async def get_primary_thread(
    user_id: str,
    *,
    conn: Optional[aiosqlite.Connection] = None,
) -> dict[str, Any] | None:
    """Read the primary thread without creating an empty session."""
    if conn is None:
        conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, kind, created_at, updated_at "
        "FROM coach_threads WHERE user_id=? AND kind='primary' "
        "AND status <> 'deleted'",
        (user_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _session_title(value: str | None) -> str | None:
    if value is None:
        return None
    title = value.strip()
    if not title:
        raise ValueError("session title cannot be empty")
    return title[:120]


async def _session_summary(
    conn: aiosqlite.Connection,
    row: aiosqlite.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a path-free, owner-independent summary for the session rail."""
    result = dict(row)
    cur = await conn.execute(
        "SELECT analysis_session_id FROM coach_analysis_refs "
        "WHERE thread_id=? AND status='active' AND analysis_session_id IS NOT NULL "
        "ORDER BY id",
        (int(result["id"]),),
    )
    result["analysis_session_ids"] = [
        int(item["analysis_session_id"]) for item in await cur.fetchall()
    ]
    result.setdefault("title", None)
    result.setdefault("status", "active")
    result.setdefault("deleted_at", None)
    return result


async def get_session(
    user_id: str,
    session_id: int,
    *,
    include_deleted: bool = False,
) -> dict[str, Any] | None:
    """Read one owner-scoped Coach session without creating it."""
    conn = await get_conn()
    where = "t.user_id=? AND t.id=?"
    params: list[Any] = [user_id, int(session_id)]
    if not include_deleted:
        where += " AND status <> 'deleted'"
    cur = await conn.execute(
        "SELECT t.id, t.user_id, t.kind, t.title, t.status, t.deleted_at, "
        "t.created_at, t.updated_at, COUNT(m.id) AS message_count, "
        "(SELECT content FROM coach_messages lm WHERE lm.thread_id=t.id "
        "ORDER BY lm.id DESC LIMIT 1) AS last_message_preview "
        "FROM coach_threads t LEFT JOIN coach_messages m ON m.thread_id=t.id "
        f"WHERE {where} GROUP BY t.id",
        params,
    )
    row = await cur.fetchone()
    return await _session_summary(conn, row) if row is not None else None


async def list_sessions(
    user_id: str,
    *,
    query: str | None = None,
    include_archived: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List active owner sessions ordered by recent activity.

    The primary thread is omitted until its first message. Explicitly created
    conversation threads remain visible even when empty so a new-session action
    has a stable target. Analysis refs are metadata on the same thread and never
    cause content-based conversation splitting.
    """
    conn = await get_conn()
    limit = max(1, min(int(limit), 200))
    statuses = ("active", "archived") if include_archived else ("active",)
    placeholders = ",".join("?" for _ in statuses)
    conditions = [
        "t.user_id=?",
        f"t.status IN ({placeholders})",
        "(t.kind <> 'primary' OR EXISTS "
        "(SELECT 1 FROM coach_messages pm WHERE pm.thread_id=t.id))",
    ]
    params: list[Any] = [user_id, *statuses]
    if query and query.strip():
        needle = f"%{query.strip()}%"
        conditions.append(
            "(COALESCE(t.title, '') LIKE ? OR EXISTS "
            "(SELECT 1 FROM coach_messages qm WHERE qm.thread_id=t.id "
            "AND qm.content LIKE ?))"
        )
        params.extend([needle, needle])
    params.append(limit)
    cur = await conn.execute(
        "SELECT t.id, t.user_id, t.kind, t.title, t.status, t.deleted_at, "
        "t.created_at, t.updated_at, COUNT(m.id) AS message_count, "
        "(SELECT content FROM coach_messages lm WHERE lm.thread_id=t.id "
        "ORDER BY lm.id DESC LIMIT 1) AS last_message_preview "
        "FROM coach_threads t LEFT JOIN coach_messages m ON m.thread_id=t.id "
        f"WHERE {' AND '.join(conditions)} GROUP BY t.id "
        "ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
        params,
    )
    return [await _session_summary(conn, row) for row in await cur.fetchall()]


async def create_session(
    user_id: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """Create an empty unclassified Coach conversation container."""
    conn = await get_conn()
    normalized_title = _session_title(title) if title is not None else "新对话"
    cur = await conn.execute(
        "INSERT INTO coach_threads(user_id, kind, title, status) "
        "VALUES(?, 'conversation', ?, 'active') "
        "RETURNING id, user_id, kind, title, status, deleted_at, created_at, updated_at",
        (user_id, normalized_title),
    )
    row = await cur.fetchone()
    await conn.commit()
    return await _session_summary(conn, row)


async def rename_session(
    user_id: str,
    session_id: int,
    title: str,
) -> dict[str, Any] | None:
    normalized_title = _session_title(title)
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE coach_threads SET title=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND user_id=? AND status <> 'deleted'",
        (normalized_title, int(session_id), user_id),
    )
    if cur.rowcount != 1:
        await conn.commit()
        return None
    await conn.commit()
    return await get_session(user_id, session_id)


async def archive_session(
    user_id: str,
    session_id: int,
) -> dict[str, Any] | None:
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE coach_threads SET status='archived', updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND user_id=? AND status <> 'deleted'",
        (int(session_id), user_id),
    )
    if cur.rowcount != 1:
        await conn.commit()
        return None
    await conn.commit()
    return await get_session(user_id, session_id)


async def soft_delete_session(
    user_id: str,
    session_id: int,
) -> dict[str, Any] | None:
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE coach_threads SET status='deleted', deleted_at=CURRENT_TIMESTAMP, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status <> 'deleted'",
        (int(session_id), user_id),
    )
    if cur.rowcount != 1:
        await conn.commit()
        # DELETE is intentionally idempotent for a local UI retry, while the
        # row remains hidden from normal list/get calls.
        return await get_session(user_id, session_id, include_deleted=True)
    await conn.commit()
    return await get_session(user_id, session_id, include_deleted=True)


async def append_message(
    thread_id: int,
    role: str,
    content: str,
    *,
    trace: Optional[list] = None,
    legacy_session_id: Optional[int] = None,
    context: Optional[dict] = None,
    context_refs: Optional[list[dict[str, Any]]] = None,
) -> int:
    from .coach_context import (
        COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
        COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
        coerce_coach_diagnostic_context,
    )

    conn = await get_conn()
    trace_json = json.dumps(trace, ensure_ascii=False) if trace else None
    canonical_context = None
    if (
        isinstance(context, dict)
        and context.get("schema_version") in {
            COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
            COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
            COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
        }
    ):
        canonical_context = coerce_coach_diagnostic_context(context)
    context_json = json.dumps(
        canonical_context,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) if canonical_context is not None else None
    context_refs_json = json.dumps(
        context_refs or [],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    cur = await conn.execute(
        "INSERT INTO coach_messages("
        "thread_id, role, content, trace_json, legacy_session_id, context_json, context_refs_json"
        ") VALUES(?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            thread_id, role, content, trace_json, legacy_session_id, context_json,
            context_refs_json,
        ),
    )
    row = await cur.fetchone()
    await conn.execute(
        "UPDATE coach_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (thread_id,),
    )
    await conn.commit()
    return int(row["id"])


async def load_messages(thread_id: int) -> list[dict[str, Any]]:
    from .coach_context import (
        COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
        COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
        COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
        coerce_coach_diagnostic_context,
    )

    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, role, content, created_at, trace_json, legacy_session_id, context_json, "
        "context_refs_json "
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
        context = None
        if r["context_json"]:
            try:
                value = json.loads(r["context_json"])
                if (
                    isinstance(value, dict)
                    and value.get("schema_version")
                    in {
                        COACH_DIAGNOSTIC_CONTEXT_SCHEMA_VERSION,
                        COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA_VERSION,
                        COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA_VERSION,
                    }
                ):
                    context = coerce_coach_diagnostic_context(value)
            except (json.JSONDecodeError, TypeError):
                context = None
        context_refs: list[dict[str, Any]] = []
        if r["context_refs_json"]:
            try:
                parsed_refs = json.loads(r["context_refs_json"])
                if isinstance(parsed_refs, list) and all(
                    isinstance(item, dict) for item in parsed_refs
                ):
                    from .coach_context_refs import overlay_snapshot_statuses

                    context_refs = await overlay_snapshot_statuses(parsed_refs)
            except (json.JSONDecodeError, TypeError):
                context_refs = []
        out.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "trace": trace,
            "legacy_session_id": r["legacy_session_id"],
            "context": context,
            "context_refs": context_refs,
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
    from .coach_context_refs import mark_analysis_deleted

    await mark_analysis_deleted(analysis_session_id, conn=conn)
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
                None,
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
