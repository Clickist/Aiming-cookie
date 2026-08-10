from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .config import DEFAULT_MAX_ATTEMPTS, DESKTOP_LOCAL_PROFILE, LEASE_TTL_SECONDS
from .contracts import (
    ANALYSIS_RESULT_SCHEMA_VERSION,
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    build_error_v1,
    coerce_analysis_result,
    coerce_error_v1,
    decode_input_snapshot_json,
    dump_contract_json,
    normalize_json_value,
    validate_analysis_result_v2_for_persistence,
)
from . import coach_store, history_trends
from .db import get_conn
from .read_models import build_record_presentation_label
from .workspace import (
    copy_path_to_path,
    remove_session_workspace,
    session_dir,
    workspace_size_bytes,
)

log = logging.getLogger(__name__)

_LEGACY_RESULT_KEYS = frozenset(
    {"diagnosis", "figures", "narration", "notes", "timeline"},
)
_INPUT_MODES = frozenset({"input_native", "multimodal", "video_fallback"})


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
        return coerce_analysis_result(
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
    *, status: str = "queued", analysis_type: str = "flicking",
    input_mode: str = "video_fallback", kovaak_run_id: int | None = None,
    input_snapshot: dict | None = None,
    profile_default: dict | None = None,
    manual_override: dict | None = None,
) -> int:
    if input_mode not in _INPUT_MODES:
        raise ValueError(f"unsupported input_mode: {input_mode}")
    conn = await get_conn()
    if kovaak_run_id is not None:
        cur = await conn.execute(
            "SELECT id FROM kovaak_runs WHERE id=? AND user_id=?",
            (kovaak_run_id, user_id),
        )
        if await cur.fetchone() is None:
            raise PermissionError("kovaak run is not owned by this user")
    if manual_override is None and (cm_per_360 is not None or fov is not None):
        manual_override = {"cm_per_360": cm_per_360, "fov": fov}
    calibration_request = {
        "profile_default": dict(profile_default) if isinstance(profile_default, dict) else None,
        "manual_override": dict(manual_override) if isinstance(manual_override, dict) else None,
    }
    initial_task_state = "importing" if status == "uploading" else (
        "queued" if status == "queued" else status
    )
    cur = await conn.execute(
        "INSERT INTO sessions("
        "user_id, status, video_path, csv_path, cm_per_360, fov, analysis_type, "
        "input_mode, kovaak_run_id, input_snapshot_json, attempts, max_attempts, "
        "task_group_ref, attempt_number, task_state, calibration_request_json"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, 1, ?, ?) RETURNING id",
        (
            user_id,
            status,
            video_path,
            csv_path,
            cm_per_360,
            fov,
            analysis_type,
            input_mode,
            kovaak_run_id,
            json.dumps(input_snapshot, ensure_ascii=False, separators=(",", ":"))
            if input_snapshot is not None else None,
            DEFAULT_MAX_ATTEMPTS,
            initial_task_state,
            json.dumps(calibration_request, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    row = await cur.fetchone()
    session_id = row["id"]
    await conn.execute(
        "UPDATE sessions SET task_group_ref=? WHERE id=?",
        (f"task:{session_id}", session_id),
    )
    await conn.commit()
    return session_id


async def finish_upload(session_id: int) -> bool:
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET status='queued', task_state='queued', "
        "task_phase='preparing_training_record', updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='uploading'",
        (session_id,),
    )
    await conn.commit()
    return cur.rowcount > 0


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
            "UPDATE sessions SET status='running', task_state='running', "
            "task_phase='preparing_training_record', "
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
            "SELECT id, user_id, video_path, csv_path, cm_per_360, fov, analysis_type, "
            "input_mode, kovaak_run_id, input_snapshot_json, task_group_ref, "
            "parent_session_id, attempt_number, task_state, task_phase, failure_domain, "
            "partial_outcome_json, calibration_request_json, calibration_snapshot_json, "
            "created_at, attempts, max_attempts, worker_id, started_at, "
            "lease_expires_at, heartbeat_at "
            "FROM sessions WHERE id=?",
            (sid,),
        )
        claimed = await cur.fetchone()
        await conn.execute("COMMIT")
        if not claimed:
            return None
        result = dict(claimed)
        raw_snapshot = result.pop("input_snapshot_json", None)
        result["input_snapshot"] = decode_input_snapshot_json(raw_snapshot)
        raw_calibration = result.pop("calibration_request_json", None)
        try:
            result["calibration_request"] = (
                json.loads(raw_calibration) if raw_calibration else None
            )
        except (TypeError, json.JSONDecodeError):
            result["calibration_request"] = None
        return result
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
    write_now_s = _utc_now_sqlite()
    now_s = now or write_now_s
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
                    "UPDATE sessions SET status = 'queued', task_state='queued', "
                    "task_phase='preparing_training_record', "
                    "worker_id = NULL, lease_expires_at = NULL, "
                    "heartbeat_at = NULL, finished_at = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (write_now_s, sid),
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
                    "UPDATE sessions SET status = 'failed', task_state='failed', "
                    "task_phase=NULL, error = ?, failure_domain='kinematics', "
                    "worker_id = NULL, lease_expires_at = NULL, "
                    "heartbeat_at = NULL, finished_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (dump_contract_json(err), write_now_s, write_now_s, sid),
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


async def requeue_for_retry(session_id: int) -> dict:
    """Create a new attempt from a failed session and copy its managed inputs."""
    conn = await get_conn()
    new_id: int | None = None
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
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
        cur = await conn.execute(
            "SELECT 1 FROM sessions WHERE parent_session_id=? LIMIT 1",
            (session_id,),
        )
        if await cur.fetchone() is not None:
            await conn.execute("COMMIT")
            raise RetryNotAllowed(
                "invalid_status", "this failed attempt already has a retry attempt",
            )
        cur = await conn.execute(
            "SELECT id FROM sessions WHERE user_id=? "
            "AND status IN ('uploading', 'queued', 'running') LIMIT 1",
            (row["user_id"],),
        )
        if await cur.fetchone() is not None:
            await conn.execute("COMMIT")
            raise RetryNotAllowed(
                "active_analysis", "已有其它 Analysis 正在进行",
            )
        input_mode = row["input_mode"] or "video_fallback"
        if input_mode == "video_fallback":
            video_path = row["video_path"] or ""
            csv_path = row["csv_path"] or ""
            if not video_path or not os.path.isfile(video_path):
                await conn.execute("COMMIT")
                raise RetryNotAllowed("missing_video", "输入视频已不存在，请重新上传分析")
            if not csv_path or not os.path.isfile(csv_path):
                await conn.execute("COMMIT")
                raise RetryNotAllowed("missing_csv", "输入 CSV 已不存在，请重新上传分析")
        elif decode_input_snapshot_json(row["input_snapshot_json"]) is None:
            await conn.execute("COMMIT")
            raise RetryNotAllowed("missing_snapshot", "分析输入快照不存在，请重新提交分析")
        source = dict(row)
        parent_group = source.get("task_group_ref") or f"task:{session_id}"
        next_attempt = int(source.get("attempt_number") or 1) + 1
        cur = await conn.execute(
            "INSERT INTO sessions("
            "user_id, status, video_path, csv_path, cm_per_360, fov, analysis_type, "
            "input_mode, kovaak_run_id, input_snapshot_json, attempts, max_attempts, "
            "task_group_ref, parent_session_id, attempt_number, task_state, task_phase, "
            "calibration_request_json"
            ") VALUES(?, 'uploading', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, "
            "'retrying', 'preparing_training_record', ?) RETURNING id",
            (
                source["user_id"], source.get("video_path"), source.get("csv_path"),
                source.get("cm_per_360"), source.get("fov"), source.get("analysis_type"),
                source.get("input_mode"), source.get("kovaak_run_id"),
                source.get("input_snapshot_json"), source.get("max_attempts") or DEFAULT_MAX_ATTEMPTS,
                parent_group, session_id, next_attempt, source.get("calibration_request_json"),
            ),
        )
        new_id = int((await cur.fetchone())["id"])
        await conn.execute("COMMIT")
    except RetryNotAllowed:
        raise
    except Exception:
        await conn.execute("ROLLBACK")
        if new_id is not None:
            try:
                remove_session_workspace(new_id)
            except OSError:
                pass
        raise

    try:
        copied_video = None
        copied_csv = None
        if input_mode in {"video_fallback", "multimodal"}:
            if source.get("video_path"):
                copied_video = session_dir(new_id) / "video.mp4"
                copy_path_to_path(Path(source["video_path"]), copied_video)
            if input_mode == "video_fallback" and source.get("csv_path"):
                copied_csv = session_dir(new_id) / "stats.csv"
                copy_path_to_path(Path(source["csv_path"]), copied_csv)
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "UPDATE sessions SET status='queued', video_path=?, csv_path=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='uploading'",
            (
                str(copied_video) if copied_video else source.get("video_path"),
                str(copied_csv) if copied_csv else source.get("csv_path"),
                new_id,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("retry attempt reservation was lost")
        await conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        try:
            remove_session_workspace(new_id)
        except OSError:
            pass
        await abort_uploading_session(new_id, source["user_id"])
        raise
    s = await get_session(new_id)
    if s is None:
        raise RetryNotAllowed("not_found", "session 不存在")
    return s


async def mark_done(
    session_id: int, result: dict, llm_cost: float, *, worker_id: str,
) -> bool:
    conn = await get_conn()
    row = None
    if result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
        cur = await conn.execute(
            "SELECT user_id, analysis_type, input_mode, kovaak_run_id FROM sessions "
            "WHERE id=? AND status='running' AND worker_id=?",
            (session_id, worker_id),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        run_id = row["kovaak_run_id"]
        result = validate_analysis_result_v2_for_persistence(
            result,
            owner_id=row["user_id"],
            analysis_id=f"analysis:{session_id}",
            analysis_type=row["analysis_type"] or "flicking",
            input_mode=row["input_mode"] or "video_fallback",
            kovaak_run_ref=f"run:{run_id}" if run_id is not None else None,
            require_local_profile=row["user_id"] == DESKTOP_LOCAL_PROFILE,
        )
    partial_outcome = None
    calibration_snapshot = None
    if isinstance(result.get("input_snapshot"), dict):
        candidate = result["input_snapshot"].get("calibration")
        if isinstance(candidate, dict):
            calibration_snapshot = candidate
    if (
        row is not None
        and row["input_mode"] == "multimodal"
        and isinstance(result.get("evidence"), dict)
        and isinstance(result["evidence"].get("availability"), dict)
        and result["evidence"]["availability"].get("mp4") in {
            "unavailable", "missing", "not_present",
        }
        and isinstance(result.get("deterministic"), dict)
        and result["deterministic"].get("status") in {"available", "limited"}
    ):
        partial_outcome = {
            "status": "partial",
            "native_preserved": True,
            "visual_status": "unavailable",
            "reason_code": "video_unavailable",
        }
    cur = await conn.execute(
        "UPDATE sessions SET status='done', task_state='done', task_phase=NULL, "
        "partial_outcome_json=?, calibration_snapshot_json=?, result=?, llm_cost_cny=?, "
        "lease_expires_at=NULL, heartbeat_at=NULL, worker_id=NULL, "
        "finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='running' AND worker_id=?",
        (
            json.dumps(partial_outcome, ensure_ascii=False, separators=(",", ":"))
            if partial_outcome else None,
            json.dumps(calibration_snapshot, ensure_ascii=False, separators=(",", ":"))
            if calibration_snapshot else None,
            dump_contract_json(result), llm_cost, session_id, worker_id,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def mark_failed(
    session_id: int,
    error: str | dict,
    *,
    worker_id: str,
    failure_domain: str | None = None,
) -> bool:
    if isinstance(error, dict):
        payload = dump_contract_json(error)
    else:
        payload = error
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET status='failed', task_state='failed', "
        "task_phase=NULL, failure_domain=COALESCE(?, failure_domain), error=?, "
        "lease_expires_at=NULL, heartbeat_at=NULL, worker_id=NULL, "
        "finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='running' AND worker_id=?",
        (failure_domain, payload, session_id, worker_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def add_llm_cost(session_id: int, delta: float) -> None:
    """Legacy compatibility accumulator for historical CNY cost records.

    Active selected-provider turns do not call this without a provider-specific
    usage and currency contract; existing rows remain readable.
    """
    conn = await get_conn()
    await conn.execute(
        "UPDATE sessions SET llm_cost_cny = COALESCE(llm_cost_cny, 0) + ?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (delta, session_id),
    )
    await conn.commit()


async def get_active_session(user_id: str) -> Optional[dict]:
    """Return one active owner-scoped Analysis for command conflict reporting."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT * FROM sessions WHERE user_id=? AND status IN ('uploading', 'queued', 'running') "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_run_analysis_states(user_id: str, run_id: int) -> list[dict]:
    """Return existing Analysis rows for one owner-scoped Run.

    This is intentionally a small projection used by batch submission to make
    repeated confirmations idempotent without introducing a second analysis
    registry.
    """
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, status, kovaak_run_id FROM sessions "
        "WHERE user_id=? AND kovaak_run_id=? ORDER BY id DESC",
        (user_id, run_id),
    )
    return [dict(row) for row in await cur.fetchall()]


async def has_active(user_id: str) -> bool:
    return await get_active_session(user_id) is not None


async def set_session_input_paths(
    session_id: int, user_id: str, video_path: str, csv_path: str,
) -> bool:
    """Set only managed workspace paths for an uploading owner-scoped session."""
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET video_path=?, csv_path=? "
        "WHERE id=? AND user_id=? AND status='uploading'",
        (video_path, csv_path, session_id, user_id),
    )
    await conn.commit()
    return cur.rowcount == 1


async def abort_uploading_session(session_id: int, user_id: str) -> bool:
    """Discard an incomplete owner-scoped upload after managed-workspace cleanup."""
    conn = await get_conn()
    cur = await conn.execute(
        "DELETE FROM sessions WHERE id=? AND user_id=? AND status='uploading'",
        (session_id, user_id),
    )
    await conn.commit()
    return cur.rowcount == 1


async def reconcile_stale_uploads() -> dict[str, int]:
    """Remove startup-left managed upload workspaces before unlocking owners."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id FROM sessions WHERE status='uploading' ORDER BY id",
    )
    session_ids = [int(row["id"]) for row in await cur.fetchall()]
    cleaned = 0
    failed = 0

    for session_id in session_ids:
        try:
            remove_session_workspace(session_id)
        except OSError:
            failed += 1
            continue

        cur = await conn.execute(
            "DELETE FROM sessions WHERE id=? AND status='uploading'",
            (session_id,),
        )
        await conn.commit()
        cleaned += cur.rowcount

    return {
        "processed": len(session_ids),
        "cleaned": cleaned,
        "failed": failed,
    }


async def list_storage_sessions(user_id: str) -> list[dict]:
    """Return per-session managed storage accounting for one local profile."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, status, created_at FROM sessions WHERE user_id=? "
        "ORDER BY created_at DESC, id DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    return [
        {
            "session_id": int(row["id"]),
            "status": row["status"],
            "created_at": sqlite_timestamp_to_wire_utc(row["created_at"]) or "",
            "workspace_bytes": workspace_size_bytes(row["id"]),
        }
        for row in rows
    ]


async def list_sessions(user_id: str) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT s.id, s.status, s.created_at, s.finished_at, s.attempts, "
        "s.max_attempts, s.llm_cost_cny, s.analysis_type, s.input_mode, "
        "s.kovaak_run_id, "
        "CASE WHEN json_valid(s.result) THEN COALESCE("
        "json_extract(s.result, '$.deterministic.diagnosis.profile.label'), "
        "json_extract(s.result, '$.diagnosis.profile.label')) END AS summary_label, "
        "COALESCE(CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.scenario') END, kr.scenario) AS scenario, "
        "kr.created_at AS training_at, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.stats.path') END AS snapshot_stats_path, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.stats.fingerprint.sha256') END "
        "AS snapshot_stats_sha256, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.stats.fingerprint.size') END "
        "AS snapshot_stats_size, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.stats.fingerprint.mtime_ns') END "
        "AS snapshot_stats_mtime_ns, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.performance.path') END "
        "AS snapshot_performance_path, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.performance.fingerprint.sha256') END "
        "AS snapshot_performance_sha256, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.performance.fingerprint.size') END "
        "AS snapshot_performance_size, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.performance.fingerprint.mtime_ns') END "
        "AS snapshot_performance_mtime_ns, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.video.path') END AS snapshot_video_path, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.video.fingerprint.sha256') END "
        "AS snapshot_video_sha256, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.video.fingerprint.size') END "
        "AS snapshot_video_size, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.sources.video.fingerprint.mtime_ns') END "
        "AS snapshot_video_mtime_ns, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.trace.path') END AS snapshot_trace_path, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.trace.fingerprint.sha256') END "
        "AS snapshot_trace_sha256, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.trace.fingerprint.size') END "
        "AS snapshot_trace_size, "
        "CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.trace.fingerprint.mtime_ns') END "
        "AS snapshot_trace_mtime_ns, "
        "CASE WHEN json_valid(s.result) THEN "
        "json_extract(s.result, '$.evidence.alignment.status') END AS alignment_status, "
        "CASE WHEN json_valid(s.result) THEN "
        "json_extract(s.result, '$.evidence.coverage') END AS evidence_coverage, "
        "kr.trace_state AS run_trace_state "
        "FROM sessions AS s LEFT JOIN kovaak_runs AS kr "
        "ON kr.id=s.kovaak_run_id AND kr.user_id=s.user_id "
        "WHERE s.user_id=? ORDER BY s.created_at DESC, s.id DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["created_at"] = sqlite_timestamp_to_wire_utc(item.get("created_at")) or ""
        item["finished_at"] = sqlite_timestamp_to_wire_utc(item.get("finished_at"))
        item["training_at"] = sqlite_timestamp_to_wire_utc(item.get("training_at"))
        projected = history_trends.analysis_list_item(item)
        projected["training_at"] = item["training_at"]
        projected["analysis_completed_at"] = projected["finished_at"]
        projected["presentation_label"] = build_record_presentation_label(
            scenario=projected["scenario"],
            training_at=projected["training_at"],
            analysis_completed_at=projected["analysis_completed_at"],
        )
        out.append(projected)
    return out


async def _rollback_without_masking(conn) -> None:
    if not conn.in_transaction:
        return
    try:
        await conn.execute("ROLLBACK")
    except Exception:
        pass


async def _finalize_analysis_cleanup(session_id: int) -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "DELETE FROM analysis_deletion_tombstones WHERE analysis_session_id=?",
            (session_id,),
        )
        await conn.commit()
    except BaseException:
        await _rollback_without_masking(conn)
        raise


async def _record_analysis_cleanup_failure(session_id: int) -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE analysis_deletion_tombstones "
            "SET cleanup_state='failed', cleanup_attempts=cleanup_attempts + 1, "
            "last_error_code='workspace_cleanup_failed', "
            "updated_at=CURRENT_TIMESTAMP WHERE analysis_session_id=?",
            (session_id,),
        )
        await conn.commit()
    except BaseException as exc:
        await _rollback_without_masking(conn)
        if not isinstance(exc, Exception):
            raise


async def _invalidate_profile_for_deleted_analysis(
    session_id: int, user_id: str,
) -> bool:
    try:
        from . import aiming_profile_store

        await aiming_profile_store.invalidate_analysis_contribution(
            user_id,
            f"analysis:{session_id}",
            reason="analysis_deleted",
        )
    except aiming_profile_store.ProfileNotFound:
        return True
    except Exception as exc:
        # Deletion remains authoritative; startup reconciliation retries the
        # profile tombstone without blocking source/workspace cleanup.
        log.warning(
            "profile contribution invalidation unavailable session=%s error=%s",
            session_id,
            type(exc).__name__,
        )
        return False
    return True


async def delete_session(session_id: int, user_id: str) -> dict:
    conn = await get_conn()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "SELECT id, user_id, status FROM sessions WHERE id=?",
            (session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise SessionNotFound()
        if row["user_id"] != user_id:
            raise SessionForbidden()
        status = row["status"] or ""
        if status not in ("done", "failed"):
            raise SessionNotDeletable(
                code="active",
                message="分析进行中，请等完成或失败后再删除",
            )

        await coach_store.migrate_session_legacy_messages(
            session_id, conn=conn,
        )
        await coach_store.mark_analysis_refs_deleted(
            session_id, conn=conn,
        )
        await conn.execute(
            "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
            "VALUES(?, ?)",
            (session_id, user_id),
        )
        await conn.execute(
            "DELETE FROM chat_messages WHERE session_id=?",
            (session_id,),
        )
        await conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await conn.execute("COMMIT")
    except BaseException:
        await _rollback_without_masking(conn)
        raise

    profile_invalidated = await _invalidate_profile_for_deleted_analysis(
        session_id, user_id,
    )

    try:
        workspace_removed = remove_session_workspace(session_id)
    except OSError:
        await _record_analysis_cleanup_failure(session_id)
        return {
            "deleted": True,
            "id": session_id,
            "files_removed": [],
            "cleanup_failed": ["workspace"],
        }

    if profile_invalidated:
        try:
            await _finalize_analysis_cleanup(session_id)
        except Exception:
            pass
    return {
        "deleted": True,
        "id": session_id,
        "files_removed": ["workspace"] if workspace_removed else [],
        "cleanup_failed": [],
    }


async def reconcile_analysis_deletions() -> dict[str, int]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT analysis_session_id, owner_id FROM analysis_deletion_tombstones "
        "ORDER BY analysis_session_id",
    )
    tombstones = [
        (int(row["analysis_session_id"]), str(row["owner_id"]))
        for row in await cur.fetchall()
    ]
    cleaned = 0
    failed = 0

    for session_id, owner_id in tombstones:
        if not await _invalidate_profile_for_deleted_analysis(session_id, owner_id):
            failed += 1
            continue
        try:
            remove_session_workspace(session_id)
        except OSError:
            await _record_analysis_cleanup_failure(session_id)
            failed += 1
            continue

        await _finalize_analysis_cleanup(session_id)
        cleaned += 1

    try:
        conn = await get_conn()
        cur = await conn.execute(
            "SELECT id, user_id, result FROM sessions WHERE status='done' AND result IS NOT NULL "
            "ORDER BY id",
        )
        rows = await cur.fetchall()
        from . import aiming_profile_store

        for row in rows:
            try:
                parsed = json.loads(row["result"])
                payload = aiming_profile_store.build_contribution_from_analysis_result(
                    parsed
                )
                if payload is not None:
                    await aiming_profile_store.record_deterministic_contribution(
                        str(row["user_id"]), f"analysis:{row['id']}", payload,
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        await aiming_profile_store.reconcile_profiles()
    except Exception as exc:
        log.warning("aiming profile reconciliation unavailable error=%s", type(exc).__name__)

    return {
        "processed": len(tombstones),
        "cleaned": cleaned,
        "failed": failed,
    }


def _task_row_from_db(row) -> dict:
    item = dict(row)
    for json_key, public_key in (
        ("error", "error"),
        ("result", "result"),
        ("partial_outcome_json", "partial_outcome_json"),
        ("calibration_request_json", "calibration_request"),
        ("calibration_snapshot_json", "calibration_snapshot"),
    ):
        raw_value = item.get(json_key)
        if json_key in {"error", "result"}:
            try:
                item[public_key] = json.loads(raw_value) if raw_value else None
            except (TypeError, json.JSONDecodeError):
                item[public_key] = raw_value
        else:
            try:
                item[public_key] = json.loads(raw_value) if raw_value else None
            except (TypeError, json.JSONDecodeError):
                item[public_key] = None
        if public_key != json_key:
            item.pop(json_key, None)
    for key in ("created_at", "started_at", "finished_at", "training_at"):
        item[key] = sqlite_timestamp_to_wire_utc(item.get(key))
    return item


async def list_task_rows(user_id: str) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT s.id, s.user_id, s.status, s.analysis_type, s.input_mode, s.kovaak_run_id, "
        "task_group_ref, parent_session_id, attempt_number, task_state, task_phase, "
        "failure_domain, partial_outcome_json, error, result, calibration_request_json, "
        "calibration_snapshot_json, s.created_at, s.started_at, s.finished_at, "
        "COALESCE(CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.scenario') END, kr.scenario) AS scenario, "
        "kr.created_at AS training_at "
        "FROM sessions AS s LEFT JOIN kovaak_runs AS kr ON kr.id=s.kovaak_run_id "
        "AND kr.user_id=s.user_id WHERE s.user_id=? ORDER BY s.created_at DESC, s.id DESC",
        (user_id,),
    )
    return [_task_row_from_db(row) for row in await cur.fetchall()]


async def get_task_rows(task_ref: str, user_id: str) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT s.id, s.user_id, s.status, s.analysis_type, s.input_mode, s.kovaak_run_id, "
        "task_group_ref, parent_session_id, attempt_number, task_state, task_phase, "
        "failure_domain, partial_outcome_json, error, result, calibration_request_json, "
        "calibration_snapshot_json, s.created_at, s.started_at, s.finished_at, "
        "COALESCE(CASE WHEN json_valid(s.input_snapshot_json) THEN "
        "json_extract(s.input_snapshot_json, '$.scenario') END, kr.scenario) AS scenario, "
        "kr.created_at AS training_at "
        "FROM sessions AS s LEFT JOIN kovaak_runs AS kr ON kr.id=s.kovaak_run_id "
        "AND kr.user_id=s.user_id WHERE s.user_id=? AND s.task_group_ref=? "
        "ORDER BY s.attempt_number, s.id",
        (user_id, task_ref),
    )
    return [_task_row_from_db(row) for row in await cur.fetchall()]


async def set_task_phase(
    session_id: int,
    phase: str,
    *,
    worker_id: str | None = None,
) -> bool:
    conn = await get_conn()
    where = "id=?"
    parameters: list[object] = [session_id]
    if worker_id is not None:
        where += " AND status='running' AND worker_id=?"
        parameters.append(worker_id)
    cur = await conn.execute(
        f"UPDATE sessions SET task_phase=?, task_state='running', updated_at=CURRENT_TIMESTAMP "
        f"WHERE {where}",
        [phase, *parameters],
    )
    await conn.commit()
    return cur.rowcount == 1


async def set_failure_domain(session_id: int, failure_domain: str) -> bool:
    conn = await get_conn()
    cur = await conn.execute(
        "UPDATE sessions SET failure_domain=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (failure_domain, session_id),
    )
    await conn.commit()
    return cur.rowcount == 1


async def get_product_state(user_id: str) -> dict:
    """Read owner-scoped onboarding and existence flags without private fields."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT onboarding_completed, onboarding_completion_kind "
        "FROM product_state WHERE owner_id=?",
        (user_id,),
    )
    row = await cur.fetchone()
    cur = await conn.execute(
        "SELECT COUNT(*) AS count FROM kovaak_runs WHERE user_id=?", (user_id,),
    )
    run_count = int((await cur.fetchone())["count"])
    cur = await conn.execute(
        "SELECT COUNT(*) AS count FROM sessions WHERE user_id=? AND status <> 'uploading'",
        (user_id,),
    )
    analysis_count = int((await cur.fetchone())["count"])
    pending_runs = False
    if run_count:
        from . import kovaak_run_store

        for run in await kovaak_run_store.list_kovaak_run_summaries(user_id):
            if run.get("readiness_state") in {"pending_analysis", "incomplete_evidence"}:
                pending_runs = True
                break
    return {
        "onboarding_completed": bool(row and row["onboarding_completed"]),
        "onboarding_completion_kind": row["onboarding_completion_kind"] if row else None,
        "has_pending_runs": pending_runs,
        "has_runs": run_count > 0,
        "has_analyses": analysis_count > 0,
    }


async def set_onboarding_state(
    user_id: str,
    *,
    completed: bool,
    completion_kind: str,
) -> dict:
    if not completed:
        raise ValueError("onboarding can only be marked completed")
    if completion_kind not in {"connected", "legacy"}:
        raise ValueError("invalid onboarding completion kind")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO product_state(owner_id, onboarding_completed, onboarding_completion_kind) "
        "VALUES(?, 1, ?) ON CONFLICT(owner_id) DO UPDATE SET "
        "onboarding_completed=1, onboarding_completion_kind=excluded.onboarding_completion_kind, "
        "updated_at=CURRENT_TIMESTAMP",
        (user_id, completion_kind),
    )
    await conn.commit()
    return await get_product_state(user_id)


async def get_session(session_id: int) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT s.id, s.user_id, s.status, s.video_path, s.csv_path, s.analysis_type, s.input_mode, "
        "s.kovaak_run_id, s.input_snapshot_json, s.result, s.error, "
        "llm_cost_cny, cm_per_360, fov, attempts, max_attempts, worker_id, "
        "task_group_ref, parent_session_id, attempt_number, task_state, task_phase, "
        "failure_domain, partial_outcome_json, calibration_request_json, "
        "calibration_snapshot_json, "
        "s.started_at, s.finished_at, s.created_at, s.updated_at, kr.scenario AS run_scenario, "
        "kr.created_at AS training_at "
        "FROM sessions AS s LEFT JOIN kovaak_runs AS kr ON kr.id=s.kovaak_run_id "
        "AND kr.user_id=s.user_id WHERE s.id=?",
        (session_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    d["input_snapshot"] = decode_input_snapshot_json(d.get("input_snapshot_json"))
    d.pop("input_snapshot_json", None)
    for json_key, public_key in (
        ("partial_outcome_json", "partial_outcome"),
        ("calibration_request_json", "calibration_request"),
        ("calibration_snapshot_json", "calibration_snapshot"),
    ):
        raw_value = d.pop(json_key, None)
        try:
            d[public_key] = json.loads(raw_value) if raw_value else None
        except (TypeError, json.JSONDecodeError):
            d[public_key] = None
    d["created_at"] = sqlite_timestamp_to_wire_utc(d.get("created_at")) or ""
    d["started_at"] = sqlite_timestamp_to_wire_utc(d.get("started_at"))
    d["finished_at"] = sqlite_timestamp_to_wire_utc(d.get("finished_at"))
    d["training_at"] = sqlite_timestamp_to_wire_utc(d.get("training_at"))
    snapshot = d.get("input_snapshot")
    snapshot_scenario = snapshot.get("scenario") if isinstance(snapshot, dict) else None
    d["scenario"] = snapshot_scenario if snapshot_scenario is not None else d.get("run_scenario")
    d["analysis_completed_at"] = d["finished_at"]
    d["presentation_label"] = build_record_presentation_label(
        scenario=d["scenario"],
        training_at=d["training_at"],
        analysis_completed_at=d["analysis_completed_at"],
    )

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
