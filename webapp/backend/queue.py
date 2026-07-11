from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import DEFAULT_MAX_ATTEMPTS, LEASE_TTL_SECONDS
from .contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    build_error_v1,
    coerce_analysis_result_v1,
    coerce_error_v1,
    dump_contract_json,
    normalize_json_value,
)
from . import coach_store
from .db import get_conn

log = logging.getLogger(__name__)

_LEGACY_RESULT_KEYS = frozenset(
    {"diagnosis", "figures", "narration", "notes", "timeline"},
)


def sqlite_timestamp_to_wire_utc(value: str | None) -> str | None:
    """SQLite CURRENT_TIMESTAMP (UTC) → YYYY-MM-DDTHH:MM:SSZ."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z") and "T" in s:
        return s
    if " " in s and "T" not in s:
        return f"{s.replace(' ', 'T')}Z"
    if "T" in s and not s.endswith("Z"):
        return f"{s}Z"
    return s


def _should_coerce_analysis_result(stored: dict) -> bool:
    schema_version = stored.get("schema_version")
    if schema_version is not None:
        return True
    return bool(_LEGACY_RESULT_KEYS.intersection(stored.keys()))


def _coerce_or_normalize_v1_read(
    parsed: dict,
    *,
    cm_per_360: float | None,
    fov: float | None,
    created_at: str | None,
    updated_at: str | None,
) -> dict:
    try:
        return coerce_analysis_result_v1(
            parsed,
            cm_per_360=cm_per_360,
            fov=fov,
            created_at=created_at,
            updated_at=updated_at,
        )
    except ValueError:
        if parsed.get("schema_version") != ANALYSIS_RESULT_SCHEMA_VERSION:
            raise
        normalized, issues = normalize_json_value(parsed)
        if not isinstance(normalized, dict):
            return parsed
        out = dict(normalized)
        existing = list(out.get("normalization_issues") or [])
        out["normalization_issues"] = existing + issues
        return out


def _utc_now_sqlite() -> str:
    """UTC wall time as SQLite CURRENT_TIMESTAMP shape: YYYY-MM-DD HH:MM:SS."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_sqlite_utc(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip().replace("T", " ").rstrip("Z")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _lease_expiry_sqlite(now: str | None = None) -> str:
    base = _parse_sqlite_utc(now) or datetime.now(timezone.utc)
    return (base + timedelta(seconds=LEASE_TTL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")


async def enqueue(
    user_id: str, video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
) -> int:
    conn = await get_conn()
    cur = await conn.execute(
        "INSERT INTO sessions("
        "user_id, video_path, csv_path, cm_per_360, fov, attempts, max_attempts"
        ") VALUES(?, ?, ?, ?, ?, 0, ?) RETURNING id",
        (user_id, video_path, csv_path, cm_per_360, fov, DEFAULT_MAX_ATTEMPTS),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row["id"]


async def claim_next(worker_id: str) -> Optional[dict]:
    """Atomically claim the next queued job with attempts remaining + lease."""
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT id FROM sessions WHERE status='queued' "
            "AND attempts < max_attempts "
            "ORDER BY created_at LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            await conn.execute("COMMIT")
            return None
        sid = row["id"]
        now = _utc_now_sqlite()
        lease_exp = _lease_expiry_sqlite(now)
        await conn.execute(
            "UPDATE sessions SET status='running', "
            "attempts = attempts + 1, "
            "worker_id = ?, "
            "started_at = COALESCE(started_at, ?), "
            "heartbeat_at = ?, "
            "lease_expires_at = ?, "
            "updated_at = ? "
            "WHERE id=?",
            (worker_id, now, now, lease_exp, now, sid),
        )
        cur = await conn.execute(
            "SELECT id, user_id, video_path, csv_path, cm_per_360, fov, "
            "created_at, attempts, max_attempts, worker_id, started_at, "
            "lease_expires_at, heartbeat_at "
            "FROM sessions WHERE id=?",
            (sid,),
        )
        claimed = await cur.fetchone()
        await conn.execute("COMMIT")
        return dict(claimed) if claimed else None
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def heartbeat(session_id: int, worker_id: str) -> bool:
    """Extend lease if session is running and owned by worker_id. True if updated."""
    conn = await get_conn()
    now = _utc_now_sqlite()
    lease_exp = _lease_expiry_sqlite(now)
    cur = await conn.execute(
        "UPDATE sessions SET heartbeat_at = ?, lease_expires_at = ?, "
        "updated_at = ? "
        "WHERE id = ? AND status = 'running' AND worker_id = ?",
        (now, lease_exp, now, session_id, worker_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def recover_stale_jobs(now: str | None = None) -> dict:
    """Requeue or fail running jobs whose lease expired (or legacy no-lease stale).

    Returns ``{"requeued": int, "failed": int}``.
    """
    now_s = now or _utc_now_sqlite()
    now_dt = _parse_sqlite_utc(now_s) or datetime.now(timezone.utc)
    stale_before = (now_dt - timedelta(seconds=LEASE_TTL_SECONDS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    requeued = 0
    failed = 0
    try:
        cur = await conn.execute(
            "SELECT id, attempts, max_attempts, lease_expires_at, "
            "started_at, updated_at FROM sessions WHERE status = 'running'"
        )
        rows = await cur.fetchall()
        for row in rows:
            sid = row["id"]
            lease = row["lease_expires_at"]
            if lease is not None and str(lease).strip():
                is_stale = str(lease) < now_s
            else:
                # Legacy running rows without lease: treat as stale if
                # started_at/updated_at older than TTL.
                anchor = row["updated_at"] or row["started_at"]
                is_stale = (
                    anchor is not None
                    and str(anchor).strip() != ""
                    and str(anchor) < stale_before
                )
            if not is_stale:
                continue

            attempts = int(row["attempts"] or 0)
            max_attempts = int(row["max_attempts"] or DEFAULT_MAX_ATTEMPTS)
            if attempts < max_attempts:
                await conn.execute(
                    "UPDATE sessions SET status = 'queued', "
                    "worker_id = NULL, lease_expires_at = NULL, "
                    "heartbeat_at = NULL, finished_at = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (now_s, sid),
                )
                requeued += 1
                log.warning(
                    "stale job requeued session_id=%s attempts=%s/%s",
                    sid, attempts, max_attempts,
                )
            else:
                err = build_error_v1(
                    category="local_cv_runtime",
                    code="stale_lease_exhausted",
                    message="分析中断且重试次数已用尽，请重新提交或点击重试。",
                    retryable=True,
                    trace_id=None,
                )
                await conn.execute(
                    "UPDATE sessions SET status = 'failed', error = ?, "
                    "worker_id = NULL, lease_expires_at = NULL, "
                    "heartbeat_at = NULL, finished_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (dump_contract_json(err), now_s, now_s, sid),
                )
                failed += 1
                log.warning(
                    "stale job failed (attempts exhausted) session_id=%s", sid,
                )
        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise
    return {"requeued": requeued, "failed": failed}


class RetryNotAllowed(Exception):
    """Raised when requeue_for_retry cannot proceed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SessionNotFound(Exception):
    """Raised when delete_session targets a missing session."""


class SessionForbidden(Exception):
    """Raised when delete_session user_id does not own the session."""


class SessionNotDeletable(Exception):
    """Raised when delete_session targets queued or running session."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _summary_label_from_stored_result(status: str, result_raw: str | None) -> str | None:
    if status != "done" or not result_raw:
        return None
    try:
        result_dict = json.loads(result_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(result_dict, dict):
        return None
    try:
        label = result_dict["deterministic"]["diagnosis"]["profile"]["label"]
        return label if isinstance(label, str) else None
    except (KeyError, TypeError):
        pass
    try:
        label = result_dict["diagnosis"]["profile"]["label"]
        return label if isinstance(label, str) else None
    except (KeyError, TypeError):
        return None


async def requeue_for_retry(session_id: int) -> dict:
    """User-initiated retry: failed session with input files still present → queued.

    Resets attempts to 0. Raises RetryNotAllowed on invalid state / missing files.
    """
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT id, status, video_path, csv_path FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await conn.execute("COMMIT")
            raise RetryNotAllowed("not_found", "session 不存在")
        if row["status"] != "failed":
            await conn.execute("COMMIT")
            raise RetryNotAllowed(
                "invalid_status",
                f"仅 failed 状态可重试，当前为 {row['status']}",
            )
        video_path = row["video_path"] or ""
        csv_path = row["csv_path"] or ""
        if not video_path or not os.path.isfile(video_path):
            await conn.execute("COMMIT")
            raise RetryNotAllowed(
                "missing_video",
                "输入视频已不存在，请重新上传分析",
            )
        if not csv_path or not os.path.isfile(csv_path):
            await conn.execute("COMMIT")
            raise RetryNotAllowed(
                "missing_csv",
                "输入 CSV 已不存在，请重新上传分析",
            )
        now = _utc_now_sqlite()
        await conn.execute(
            "UPDATE sessions SET status = 'queued', attempts = 0, "
            "error = NULL, result = NULL, worker_id = NULL, "
            "lease_expires_at = NULL, heartbeat_at = NULL, "
            "started_at = NULL, finished_at = NULL, "
            "llm_cost_cny = 0, updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        await conn.execute("COMMIT")
    except RetryNotAllowed:
        raise
    except Exception:
        await conn.execute("ROLLBACK")
        raise
    s = await get_session(session_id)
    if s is None:
        raise RetryNotAllowed("not_found", "session 不存在")
    return s


async def mark_done(
    session_id: int, result: dict, llm_cost: float, *, worker_id: str,
) -> bool:
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET status='done', result=?, llm_cost_cny=?, "
        "lease_expires_at=NULL, heartbeat_at=NULL, worker_id=NULL, "
        "finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='running' AND worker_id=?",
        (dump_contract_json(result), llm_cost, session_id, worker_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def mark_failed(session_id: int, error: str | dict, *, worker_id: str) -> bool:
    if isinstance(error, dict):
        payload = dump_contract_json(error)
    else:
        payload = error
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET status='failed', error=?, "
        "lease_expires_at=NULL, heartbeat_at=NULL, worker_id=NULL, "
        "finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='running' AND worker_id=?",
        (payload, session_id, worker_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def add_llm_cost(session_id: int, delta: float) -> None:
    """累加 LLM cost 到已 done 的 session(用于 chat 等非 worker 路径记账)。

    worker 路径用 mark_done 一次性设 cost;chat 在 session 已 done 后追加,
    用 UPDATE 累加,这样下次 llm_budget.check_and_record 反映真实累计
    (避免反复调 chat 绕过日预算限制)。
    """
    conn = await get_conn()
    await conn.execute(
        "UPDATE sessions SET llm_cost_cny = COALESCE(llm_cost_cny, 0) + ?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (delta, session_id),
    )
    await conn.commit()


async def has_active(user_id: str) -> bool:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT EXISTS(SELECT 1 FROM sessions "
        "WHERE user_id=? AND status IN ('queued', 'running'))",
        (user_id,),
    )
    row = await cur.fetchone()
    return bool(row[0])


async def list_sessions(user_id: str) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, status, created_at, finished_at, attempts, "
        "max_attempts, llm_cost_cny, result "
        "FROM sessions WHERE user_id=? "
        "ORDER BY created_at DESC, id DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        out.append({
            "id": d["id"],
            "status": d["status"],
            "created_at": sqlite_timestamp_to_wire_utc(d.get("created_at")) or "",
            "finished_at": sqlite_timestamp_to_wire_utc(d.get("finished_at")),
            "attempts": int(d["attempts"] or 0),
            "max_attempts": int(d["max_attempts"] or 1),
            "llm_cost_cny": float(d["llm_cost_cny"] or 0),
            "summary_label": _summary_label_from_stored_result(
                d.get("status") or "",
                d.get("result"),
            ),
        })
    return out


async def delete_session(session_id: int, user_id: str) -> dict:
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    video_path = ""
    csv_path = ""
    try:
        cur = await conn.execute(
            "SELECT id, user_id, status, video_path, csv_path FROM sessions WHERE id=?",
            (session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await conn.execute("ROLLBACK")
            raise SessionNotFound()
        if row["user_id"] != user_id:
            await conn.execute("ROLLBACK")
            raise SessionForbidden()
        status = row["status"] or ""
        if status in ("queued", "running"):
            await conn.execute("ROLLBACK")
            raise SessionNotDeletable(
                code="active",
                message="分析进行中，请等完成或失败后再删除",
            )
        video_path = row["video_path"] or ""
        csv_path = row["csv_path"] or ""

        await coach_store.migrate_session_legacy_messages(
            session_id, conn=conn,
        )
        await coach_store.mark_analysis_refs_deleted(
            session_id, conn=conn,
        )
        await conn.execute(
            "DELETE FROM chat_messages WHERE session_id=?",
            (session_id,),
        )
        await conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await conn.execute("COMMIT")
    except (SessionNotFound, SessionForbidden, SessionNotDeletable):
        raise
    except Exception:
        await conn.execute("ROLLBACK")
        raise

    files_removed: list[str] = []
    for kind, path in (("video", video_path), ("csv", csv_path)):
        if not path or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            files_removed.append(kind)
        except OSError:
            log.warning(
                "delete_session file remove failed session_id=%s kind=%s path=%s",
                session_id,
                kind,
                path,
            )
    return {"deleted": True, "id": session_id, "files_removed": files_removed}


async def get_session(session_id: int) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, status, video_path, csv_path, result, error, "
        "llm_cost_cny, cm_per_360, fov, attempts, max_attempts, worker_id, "
        "started_at, finished_at, created_at, updated_at "
        "FROM sessions WHERE id=?",
        (session_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    d["created_at"] = sqlite_timestamp_to_wire_utc(d.get("created_at")) or ""
    d["started_at"] = sqlite_timestamp_to_wire_utc(d.get("started_at"))
    d["finished_at"] = sqlite_timestamp_to_wire_utc(d.get("finished_at"))

    raw_result = d.get("result")
    if raw_result:
        try:
            parsed = json.loads(raw_result)
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "sessions.result JSON decode failed session_id=%s", session_id,
            )
            d["result"] = None
        else:
            if isinstance(parsed, dict) and _should_coerce_analysis_result(parsed):
                row_created = sqlite_timestamp_to_wire_utc(d.get("created_at"))
                row_updated = sqlite_timestamp_to_wire_utc(d.get("updated_at"))
                d["result"] = _coerce_or_normalize_v1_read(
                    parsed,
                    cm_per_360=d.get("cm_per_360"),
                    fov=d.get("fov"),
                    created_at=row_created,
                    updated_at=row_updated,
                )
            else:
                d["result"] = parsed
    else:
        d["result"] = None

    if d.get("error") is not None:
        d["error"] = coerce_error_v1(d["error"])
    return d