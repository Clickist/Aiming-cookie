from __future__ import annotations

import asyncio
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
from . import file_store, history_trends
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

_QUEUE_LOCK = asyncio.Lock()
_SESSIONS_DIR = "sessions"
_COUNTER_PATH = "sessions/_counter.json"
_ONBOARDING_PATH = "config/onboarding.json"
_ANALYSES_TOMBSTONES = "sessions/_deletion_tombstones.json"


class ActiveSessionExists(RuntimeError):
    pass


class RetryNotAllowed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SessionNotFound(Exception):
    pass


class SessionForbidden(Exception):
    pass


class SessionNotDeletable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- timestamp helpers ----

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.rstrip("Z").replace("T", " "), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def timestamp_to_wire_utc(value: str | None) -> str | None:
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


def _lease_expiry() -> str:
    base = datetime.now(timezone.utc)
    return (base + timedelta(seconds=LEASE_TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- session file I/O ----

def _session_path(session_id: int) -> str:
    return f"{_SESSIONS_DIR}/{session_id}.json"


def _next_session_id() -> int:
    data = file_store.read_json(_COUNTER_PATH)
    if data is None:
        # Scan existing session files for the max id
        max_id = 0
        for p in file_store.list_dir(_SESSIONS_DIR, "*.json"):
            try:
                max_id = max(max_id, int(p.stem))
            except ValueError:
                continue
        data = {"next_id": max_id + 1}
    next_id = int(data.get("next_id", 1))
    data["next_id"] = next_id + 1
    file_store.write_json(_COUNTER_PATH, data)
    return next_id


def _load_session(session_id: int) -> dict | None:
    return file_store.read_json(_session_path(session_id))


def _save_session(session: dict) -> None:
    file_store.write_json(_session_path(session["id"]), session)


def _delete_session_file(session_id: int) -> None:
    file_store.delete_file(_session_path(session_id))


def _all_sessions(user_id: str | None = None) -> list[dict]:
    sessions = []
    for p in file_store.list_dir(_SESSIONS_DIR, "*.json"):
        try:
            int(p.stem)
        except ValueError:
            continue
        try:
            data = file_store.read_json(f"{_SESSIONS_DIR}/{p.name}")
        except (OSError, ValueError):
            log.warning("skipping unreadable session %s", f"{_SESSIONS_DIR}/{p.name}")
            continue
        if data is None:
            continue
        if user_id is None or data.get("user_id") == user_id:
            sessions.append(data)
    sessions.sort(key=lambda s: (s.get("created_at", ""), s.get("id", 0)), reverse=True)
    return sessions


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


# ---- public API ----

async def enqueue(
    user_id: str, video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
    *, status: str = "queued", analysis_type: str = "flicking",
    input_mode: str = "video_fallback", kovaak_run_id: int | None = None,
    input_snapshot: dict | None = None,
    profile_default: dict | None = None,
    manual_override: dict | None = None,
    require_no_active: bool = False,
    video_receipt: dict | None = None,
) -> int:
    if input_mode not in _INPUT_MODES:
        raise ValueError(f"unsupported input_mode: {input_mode}")
    if kovaak_run_id is not None:
        from . import kovaak_run_store
        run = await kovaak_run_store.get_kovaak_run(kovaak_run_id, user_id)
        if run is None:
            raise PermissionError("kovaak run is not owned by this user")
    if manual_override is None and (cm_per_360 is not None or fov is not None):
        manual_override = {"cm_per_360": cm_per_360, "fov": fov}
    calibration_request = {
        "profile_default": dict(profile_default) if isinstance(profile_default, dict) else None,
        "manual_override": dict(manual_override) if isinstance(manual_override, dict) else None,
    }
    async with _QUEUE_LOCK:
        if require_no_active:
            for s in _all_sessions(user_id):
                if s.get("status") in ("uploading", "queued", "running"):
                    raise ActiveSessionExists("owner already has an active analysis")
        now = _utc_now()
        session_id = _next_session_id()
        initial_task_state = "importing" if status == "uploading" else (
            "queued" if status == "queued" else status
        )
        session = {
            "id": session_id,
            "user_id": user_id,
            "status": status,
            "video_path": video_path,
            "csv_path": csv_path,
            "cm_per_360": cm_per_360,
            "fov": fov,
            "analysis_type": analysis_type,
            "input_mode": input_mode,
            "kovaak_run_id": kovaak_run_id,
            "input_snapshot": input_snapshot,
            "video_receipt": video_receipt,
            "result": None,
            "error": None,
            "llm_cost_cny": 0,
            "attempts": 0,
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "worker_id": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
            "task_group_ref": f"task:{session_id}",
            "parent_session_id": None,
            "attempt_number": 1,
            "task_state": initial_task_state,
            "task_phase": None,
            "failure_domain": None,
            "partial_outcome": None,
            "calibration_request": calibration_request,
            "calibration_snapshot": None,
        }
        _save_session(session)
    return session_id


async def finish_upload(session_id: int) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None or session.get("status") != "uploading":
            return False
        session["status"] = "queued"
        session["task_state"] = "queued"
        session["task_phase"] = "preparing_training_record"
        session["updated_at"] = _utc_now()
        _save_session(session)
    return True


async def claim_next(worker_id: str) -> Optional[dict]:
    async with _QUEUE_LOCK:
        candidates = [
            s for s in _all_sessions()
            if s.get("status") == "queued"
            and int(s.get("attempts", 0)) < int(s.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
        ]
        if not candidates:
            return None
        # FIFO: pick the oldest queued job (created_at, then id ascending).
        candidate = min(candidates, key=lambda s: (s.get("created_at", ""), s.get("id", 0)))
        if not isinstance(candidate.get("input_snapshot"), dict):
            candidate["input_snapshot"] = None
        now = _utc_now()
        lease_exp = _lease_expiry()
        candidate["status"] = "running"
        candidate["task_state"] = "running"
        candidate["task_phase"] = "preparing_training_record"
        candidate["attempts"] = int(candidate.get("attempts", 0)) + 1
        candidate["worker_id"] = worker_id
        if not candidate.get("started_at"):
            candidate["started_at"] = now
        candidate["heartbeat_at"] = now
        candidate["lease_expires_at"] = lease_exp
        candidate["updated_at"] = now
        _save_session(candidate)
        return candidate


async def heartbeat(session_id: int, worker_id: str) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return False
        if session.get("status") != "running" or session.get("worker_id") != worker_id:
            return False
        now = _utc_now()
        session["heartbeat_at"] = now
        session["lease_expires_at"] = _lease_expiry()
        session["updated_at"] = now
        _save_session(session)
    return True


async def recover_stale_jobs(now: str | None = None) -> dict:
    requeued = 0
    failed = 0
    async with _QUEUE_LOCK:
        now_dt = datetime.now(timezone.utc)
        for session in _all_sessions():
            if session.get("status") != "running":
                continue
            lease = session.get("lease_expires_at")
            if lease:
                is_stale = str(lease) < _utc_now()
            else:
                anchor = session.get("updated_at") or session.get("started_at")
                if anchor:
                    anchor_dt = _parse_utc(anchor)
                    is_stale = anchor_dt is not None and (now_dt - anchor_dt).total_seconds() > LEASE_TTL_SECONDS
                else:
                    is_stale = False
            if not is_stale:
                continue
            attempts = int(session.get("attempts", 0))
            max_attempts = int(session.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
            if attempts < max_attempts:
                session["status"] = "queued"
                session["task_state"] = "queued"
                session["task_phase"] = "preparing_training_record"
                session["worker_id"] = None
                session["lease_expires_at"] = None
                session["heartbeat_at"] = None
                session["finished_at"] = None
                session["updated_at"] = _utc_now()
                _save_session(session)
                requeued += 1
                log.warning("stale job requeued session_id=%s", session["id"])
            else:
                err = build_error_v1(
                    category="local_cv_runtime",
                    code="stale_lease_exhausted",
                    message="分析中断且重试次数已用尽，请重新提交或点击重试。",
                    retryable=True,
                    trace_id=None,
                )
                session["status"] = "failed"
                session["task_state"] = "failed"
                session["task_phase"] = None
                session["error"] = err
                session["failure_domain"] = "kinematics"
                session["worker_id"] = None
                session["lease_expires_at"] = None
                session["heartbeat_at"] = None
                session["finished_at"] = _utc_now()
                session["updated_at"] = _utc_now()
                _save_session(session)
                failed += 1
                log.warning("stale job failed session_id=%s", session["id"])
    return {"requeued": requeued, "failed": failed}


async def requeue_for_retry(session_id: int) -> dict:
    async with _QUEUE_LOCK:
        source = _load_session(session_id)
        if source is None:
            raise RetryNotAllowed("not_found", "session 不存在")
        if source["status"] != "failed":
            raise RetryNotAllowed(
                "invalid_status",
                f"仅 failed 状态可重试，当前为 {source['status']}",
            )
        # Check no existing retry
        for s in _all_sessions(source["user_id"]):
            if s.get("parent_session_id") == session_id:
                raise RetryNotAllowed("invalid_status", "this failed attempt already has a retry attempt")
        # Check no active
        for s in _all_sessions(source["user_id"]):
            if s.get("status") in ("uploading", "queued", "running"):
                raise RetryNotAllowed("active_analysis", "已有其它 Analysis 正在进行")

        input_mode = source.get("input_mode") or "video_fallback"
        if input_mode == "video_fallback":
            video_path = source.get("video_path") or ""
            csv_path = source.get("csv_path") or ""
            if not video_path or not os.path.isfile(video_path):
                raise RetryNotAllowed("missing_video", "输入视频已不存在，请重新上传分析")
            if not csv_path or not os.path.isfile(csv_path):
                raise RetryNotAllowed("missing_csv", "输入 CSV 已不存在，请重新上传分析")
        elif not isinstance(source.get("input_snapshot"), dict):
            raise RetryNotAllowed("missing_snapshot", "分析输入快照不存在，请重新提交分析")

        parent_group = source.get("task_group_ref") or f"task:{session_id}"
        next_attempt = int(source.get("attempt_number", 1)) + 1
        now = _utc_now()
        new_id = _next_session_id()
        new_session = {
            "id": new_id,
            "user_id": source["user_id"],
            "status": "uploading",
            "video_path": source.get("video_path"),
            "csv_path": source.get("csv_path"),
            "cm_per_360": source.get("cm_per_360"),
            "fov": source.get("fov"),
            "analysis_type": source.get("analysis_type"),
            "input_mode": source.get("input_mode"),
            "kovaak_run_id": source.get("kovaak_run_id"),
            "input_snapshot": source.get("input_snapshot"),
            "result": None,
            "error": None,
            "llm_cost_cny": 0,
            "attempts": 0,
            "max_attempts": source.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
            "worker_id": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
            "task_group_ref": parent_group,
            "parent_session_id": session_id,
            "attempt_number": next_attempt,
            "task_state": "retrying",
            "task_phase": "preparing_training_record",
            "failure_domain": None,
            "partial_outcome": None,
            "calibration_request": source.get("calibration_request"),
            "calibration_snapshot": None,
        }
        _save_session(new_session)

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
        async with _QUEUE_LOCK:
            session = _load_session(new_id)
            if session is None or session["status"] != "uploading":
                raise RuntimeError("retry attempt reservation was lost")
            if copied_video:
                session["video_path"] = str(copied_video)
            if copied_csv:
                session["csv_path"] = str(copied_csv)
            session["status"] = "queued"
            session["updated_at"] = _utc_now()
            _save_session(session)
    except Exception:
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
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return False
        if session.get("status") != "running" or session.get("worker_id") != worker_id:
            return False
        if result.get("schema_version") == ANALYSIS_RESULT_V2_SCHEMA_VERSION:
            run_id = session.get("kovaak_run_id")
            result = validate_analysis_result_v2_for_persistence(
                result,
                owner_id=session["user_id"],
                analysis_id=f"analysis:{session_id}",
                analysis_type=session.get("analysis_type") or "flicking",
                input_mode=session.get("input_mode") or "video_fallback",
                kovaak_run_ref=f"run:{run_id}" if run_id is not None else None,
                require_local_profile=session["user_id"] == DESKTOP_LOCAL_PROFILE,
            )
        partial_outcome = None
        calibration_snapshot = None
        if isinstance(result.get("input_snapshot"), dict):
            candidate = result["input_snapshot"].get("calibration")
            if isinstance(candidate, dict):
                calibration_snapshot = candidate
        if (
            session.get("input_mode") == "multimodal"
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
        session["status"] = "done"
        session["task_state"] = "done"
        session["task_phase"] = None
        session["partial_outcome"] = partial_outcome
        session["calibration_snapshot"] = calibration_snapshot
        session["result"] = result
        session["llm_cost_cny"] = llm_cost
        session["lease_expires_at"] = None
        session["heartbeat_at"] = None
        session["worker_id"] = None
        session["finished_at"] = _utc_now()
        session["updated_at"] = _utc_now()
        _save_session(session)
    return True


async def mark_failed(
    session_id: int,
    error: str | dict,
    *,
    worker_id: str,
    failure_domain: str | None = None,
) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return False
        if session.get("status") != "running" or session.get("worker_id") != worker_id:
            return False
        session["status"] = "failed"
        session["task_state"] = "failed"
        session["task_phase"] = None
        if failure_domain is not None:
            session["failure_domain"] = failure_domain
        session["error"] = error
        session["lease_expires_at"] = None
        session["heartbeat_at"] = None
        session["worker_id"] = None
        session["finished_at"] = _utc_now()
        session["updated_at"] = _utc_now()
        _save_session(session)
    return True


async def add_llm_cost(session_id: int, delta: float) -> None:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return
        session["llm_cost_cny"] = float(session.get("llm_cost_cny", 0) or 0) + delta
        session["updated_at"] = _utc_now()
        _save_session(session)


async def get_active_session(user_id: str) -> Optional[dict]:
    for s in _all_sessions(user_id):
        if s.get("status") in ("uploading", "queued", "running"):
            return s
    return None


async def get_run_analysis_states(user_id: str, run_id: int) -> list[dict]:
    return [
        {"id": s["id"], "status": s.get("status"), "kovaak_run_id": s.get("kovaak_run_id")}
        for s in _all_sessions(user_id)
        if s.get("kovaak_run_id") == run_id
    ]


async def has_active(user_id: str) -> bool:
    return await get_active_session(user_id) is not None


async def set_session_input_paths(
    session_id: int, user_id: str, video_path: str, csv_path: str,
) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None or session.get("user_id") != user_id or session.get("status") != "uploading":
            return False
        session["video_path"] = video_path
        session["csv_path"] = csv_path
        _save_session(session)
    return True


async def abort_uploading_session(session_id: int, user_id: str) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None or session.get("user_id") != user_id or session.get("status") != "uploading":
            return False
        _delete_session_file(session_id)
    return True


async def reconcile_stale_uploads() -> dict[str, int]:
    cleaned = 0
    failed = 0
    processed = 0
    async with _QUEUE_LOCK:
        for session in _all_sessions():
            if session.get("status") != "uploading":
                continue
            processed += 1
            try:
                remove_session_workspace(session["id"])
            except OSError:
                failed += 1
                continue
            _delete_session_file(session["id"])
            cleaned += 1
    return {"processed": processed, "cleaned": cleaned, "failed": failed}


async def list_storage_sessions(user_id: str) -> list[dict]:
    return [
        {
            "session_id": s["id"],
            "status": s.get("status"),
            "created_at": timestamp_to_wire_utc(s.get("created_at")) or "",
            "workspace_bytes": workspace_size_bytes(s["id"]),
        }
        for s in _all_sessions(user_id)
    ]


async def list_sessions(user_id: str) -> list[dict]:
    out: list[dict] = []
    for session in _all_sessions(user_id):
        item = dict(session)
        item["created_at"] = timestamp_to_wire_utc(item.get("created_at")) or ""
        item["finished_at"] = timestamp_to_wire_utc(item.get("finished_at"))
        snapshot = item.get("input_snapshot")
        snapshot_scenario = snapshot.get("scenario") if isinstance(snapshot, dict) else None
        item["scenario"] = snapshot_scenario if snapshot_scenario is not None else item.get("run_scenario")
        _flatten_snapshot_sources(item, snapshot)
        item["training_at"] = _resolve_training_at(item)
        item["summary_label"] = _resolve_summary_label(item)
        projected = history_trends.analysis_list_item(item)
        projected["training_at"] = item["training_at"]
        projected["analysis_completed_at"] = projected.get("finished_at")
        projected["presentation_label"] = build_record_presentation_label(
            scenario=projected.get("scenario"),
            training_at=projected["training_at"],
            analysis_completed_at=projected["analysis_completed_at"],
        )
        out.append(projected)
    return out


def _flatten_snapshot_sources(item: dict, snapshot: object) -> None:
    """Copy frozen input-snapshot source fields onto the row for read projections."""
    if not isinstance(snapshot, dict):
        return
    top_trace = snapshot.get("trace")
    if isinstance(top_trace, dict):
        _flatten_snapshot_source(item, "trace", top_trace)
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        return
    for kind in ("stats", "performance", "video", "trace"):
        _flatten_snapshot_source(item, kind, sources.get(kind))


def _flatten_snapshot_source(item: dict, kind: str, source: object) -> None:
    if not isinstance(source, dict):
        return
    path = source.get("path")
    fingerprint = source.get("fingerprint")
    if isinstance(path, str):
        item[f"snapshot_{kind}_path"] = path
    if isinstance(fingerprint, dict):
        item[f"snapshot_{kind}_size"] = fingerprint.get("size")
        item[f"snapshot_{kind}_mtime_ns"] = fingerprint.get("mtime_ns")


def _resolve_training_at(item: dict) -> str | None:
    run_id = item.get("kovaak_run_id")
    if run_id is None:
        return None
    from . import kovaak_run_store
    run = kovaak_run_store._load_run(int(run_id))
    if run is None:
        return None
    return timestamp_to_wire_utc(run.get("created_at"))


def _resolve_summary_label(item: dict) -> str | None:
    result = item.get("result")
    if not isinstance(result, dict):
        return None
    diagnosis = result.get("diagnosis")
    if not isinstance(diagnosis, dict):
        deterministic = result.get("deterministic")
        diagnosis = (
            deterministic.get("diagnosis")
            if isinstance(deterministic, dict)
            else None
        )
    if not isinstance(diagnosis, dict):
        return None
    profile = diagnosis.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("label"), str):
        return profile["label"]
    return None


async def delete_session(session_id: int, user_id: str) -> dict:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            raise SessionNotFound()
        if session.get("user_id") != user_id:
            raise SessionForbidden()
        status = session.get("status") or ""
        if status not in ("done", "failed"):
            raise SessionNotDeletable(
                code="active",
                message="分析进行中，请等完成或失败后再删除",
            )
        _delete_session_file(session_id)
        # Record tombstone
        tombstones = file_store.read_json(_ANALYSES_TOMBSTONES) or []
        tombstones.append({"analysis_session_id": session_id, "owner_id": user_id})
        file_store.write_json(_ANALYSES_TOMBSTONES, tombstones)

    try:
        await _invalidate_profile_for_deleted_analysis(session_id, user_id)
    except Exception as exc:
        log.warning("profile invalidation unavailable session=%s error=%s", session_id, type(exc).__name__)

    try:
        workspace_removed = remove_session_workspace(session_id)
    except OSError:
        return {
            "deleted": True,
            "id": session_id,
            "files_removed": [],
            "cleanup_failed": ["workspace"],
        }

    return {
        "deleted": True,
        "id": session_id,
        "files_removed": ["workspace"] if workspace_removed else [],
        "cleanup_failed": [],
    }


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
        log.warning("profile contribution invalidation unavailable session=%s error=%s", session_id, type(exc).__name__)
        return False
    return True


async def reconcile_analysis_deletions() -> dict[str, int]:
    tombstones = file_store.read_json(_ANALYSES_TOMBSTONES) or []
    cleaned = 0
    failed = 0
    remaining = []
    for entry in tombstones:
        session_id = entry.get("analysis_session_id")
        owner_id = entry.get("owner_id")
        if session_id is None or owner_id is None:
            continue
        if not await _invalidate_profile_for_deleted_analysis(session_id, owner_id):
            failed += 1
            remaining.append(entry)
            continue
        try:
            remove_session_workspace(session_id)
        except OSError:
            failed += 1
            remaining.append(entry)
            continue
        cleaned += 1
    file_store.write_json(_ANALYSES_TOMBSTONES, remaining)

    # Rebuild profile contributions from completed sessions
    try:
        from . import aiming_profile_store
        for session in _all_sessions():
            if session.get("status") != "done" or session.get("result") is None:
                continue
            try:
                payload = aiming_profile_store.build_contribution_from_analysis_result(
                    session["result"]
                )
                if payload is not None:
                    await aiming_profile_store.record_deterministic_contribution(
                        str(session["user_id"]), f"analysis:{session['id']}", payload,
                    )
            except (TypeError, ValueError):
                continue
        await aiming_profile_store.reconcile_profiles()
    except Exception as exc:
        log.warning("aiming profile reconciliation unavailable error=%s", type(exc).__name__)

    return {
        "processed": len(tombstones),
        "cleaned": cleaned,
        "failed": failed,
    }


async def list_task_rows(user_id: str) -> list[dict]:
    rows = []
    for session in _all_sessions(user_id):
        rows.append(_task_row(session))
    return rows


async def get_task_rows(task_ref: str, user_id: str) -> list[dict]:
    rows = []
    for session in _all_sessions(user_id):
        if session.get("task_group_ref") == task_ref:
            rows.append(_task_row(session))
    rows.sort(key=lambda r: (r.get("attempt_number", 1), r.get("id", 0)))
    return rows


def _task_row(session: dict) -> dict:
    item = dict(session)
    for key in ("created_at", "started_at", "finished_at", "training_at"):
        item[key] = timestamp_to_wire_utc(item.get(key))
    snapshot = item.get("input_snapshot")
    if isinstance(snapshot, dict):
        item["scenario"] = snapshot.get("scenario")
    else:
        item["scenario"] = None
    return item


async def set_task_phase(
    session_id: int,
    phase: str,
    *,
    worker_id: str | None = None,
) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return False
        if worker_id is not None:
            if session.get("status") != "running" or session.get("worker_id") != worker_id:
                return False
        session["task_phase"] = phase
        session["task_state"] = "running"
        session["updated_at"] = _utc_now()
        _save_session(session)
    return True


async def set_failure_domain(session_id: int, failure_domain: str) -> bool:
    async with _QUEUE_LOCK:
        session = _load_session(session_id)
        if session is None:
            return False
        session["failure_domain"] = failure_domain
        session["updated_at"] = _utc_now()
        _save_session(session)
    return True


async def get_product_state(user_id: str) -> dict:
    onboarding = file_store.read_json(_ONBOARDING_PATH) or {}
    sessions = _all_sessions(user_id)
    run_count = 0
    try:
        from . import kovaak_run_store
        run_count = len(await kovaak_run_store.list_kovaak_run_summaries(user_id))
    except Exception:
        pass
    analysis_count = sum(1 for s in sessions if s.get("status") != "uploading")
    pending_runs = False
    if run_count:
        try:
            from . import kovaak_run_store
            for run in await kovaak_run_store.list_kovaak_run_summaries(user_id):
                if run.get("readiness_state") in {"pending_analysis", "incomplete_evidence"}:
                    pending_runs = True
                    break
        except Exception:
            pass
    return {
        "onboarding_completed": bool(onboarding.get("onboarding_completed")),
        "onboarding_completion_kind": onboarding.get("onboarding_completion_kind"),
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
    file_store.write_json(_ONBOARDING_PATH, {
        "onboarding_completed": True,
        "onboarding_completion_kind": completion_kind,
        "updated_at": _utc_now(),
    })
    return await get_product_state(user_id)


async def get_session(session_id: int) -> Optional[dict]:
    session = _load_session(session_id)
    if session is None:
        return None
    d = dict(session)
    d["created_at"] = timestamp_to_wire_utc(d.get("created_at")) or ""
    d["started_at"] = timestamp_to_wire_utc(d.get("started_at"))
    d["finished_at"] = timestamp_to_wire_utc(d.get("finished_at"))
    d["training_at"] = timestamp_to_wire_utc(d.get("training_at"))
    snapshot = d.get("input_snapshot")
    if not isinstance(snapshot, dict):
        d["input_snapshot"] = None
        snapshot = None
    snapshot_scenario = snapshot.get("scenario") if isinstance(snapshot, dict) else None
    d["scenario"] = snapshot_scenario if snapshot_scenario is not None else d.get("run_scenario")
    d["analysis_completed_at"] = d.get("finished_at")
    d["presentation_label"] = build_record_presentation_label(
        scenario=d.get("scenario"),
        training_at=d.get("training_at"),
        analysis_completed_at=d.get("analysis_completed_at"),
    )

    raw_result = d.get("result")
    if raw_result:
        if isinstance(raw_result, dict) and _should_coerce_analysis_result(raw_result):
            d["result"] = _coerce_or_normalize_v1_read(
                raw_result,
                cm_per_360=d.get("cm_per_360"),
                fov=d.get("fov"),
                created_at=d.get("created_at"),
                updated_at=d.get("updated_at"),
            )
        elif not isinstance(raw_result, dict):
            d["result"] = None
    else:
        d["result"] = None

    if d.get("error") is not None:
        d["error"] = coerce_error_v1(d["error"])
    return d
