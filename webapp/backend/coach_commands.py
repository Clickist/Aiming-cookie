"""Owner-aware application commands shared by Coach tools and UI routes.

This module deliberately contains no FastAPI types.  It is the product boundary
between an untrusted Coach tool payload and the existing owner-scoped stores.
The persistent journal schema is owned by the DB integration task; callers can
install that implementation with :func:`set_command_journal` without changing
command behaviour.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import os
import re
import secrets
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from . import coach_store, history_trends, kovaak_run_store, queue
from .workspace import copy_path_to_path, remove_session_workspace, session_dir

RESULT_SCHEMA_VERSION = "coach_product_command_result.v1"
TOOL_BRIDGE_ENDPOINT = "/api/coach/tools/execute"
_TOOL_BRIDGE_PAYLOAD_KEYS = frozenset({
    "command_id", "command_name", "parameters", "idempotency_key",
})

_QUERY_COMMANDS = {
    "run.list",
    "run.get",
    "history.list",
    "history.trend",
    "analysis.get",
    "analysis.compare",
    "navigation.open",
    "training_plan.review",
}
_WRITE_COMMANDS = {
    "analysis.create_from_run",
    "analysis.retry",
    "training_plan.generate_draft",
    "training_plan.save",
    "training_plan.activate",
    "training_plan.pause",
    "training_plan.adjust",
}
_COMMANDS = _QUERY_COMMANDS | _WRITE_COMMANDS
_FORBIDDEN_MODEL_KEYS = {
    "owner",
    "owner_id",
    "owner_scope",
    "risk",
    "path",
    "video_path",
    "url",
    "credential",
    "credentials",
    "api_key",
    "token",
    "password",
    "secret",
    "raw_trace",
    "trace_path",
}
_REF_RE = re.compile(r"^(?P<kind>run|analysis):(?P<id>[1-9][0-9]*)$")
_PATH_OR_URL_TEXT_RE = re.compile(
    r'''(?:https?://|file:(?://)?|(?:^|[\s"'`()\[\]{}=,:])'''
    r'''(?:/|~[\\/]|\.{1,2}[\\/]|[A-Za-z]:[\\/]|\\\\))''',
    re.IGNORECASE,
)


class ProductCommandError(Exception):
    """A stable application error that HTTP can map without importing FastAPI."""

    def __init__(
        self, code: str, message: str, *, kind: str = "failed", result_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind
        self.result_ref = result_ref


class CommandJournal(Protocol):
    async def lookup(self, owner_id: str, command_name: str, idempotency_key: str) -> dict[str, Any] | None: ...

    async def claim(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        reservation: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def record(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        result: dict[str, Any],
    ) -> None: ...

    async def audit(self, owner_id: str, result: dict[str, Any]) -> None: ...

    async def confirm_and_reserve(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        confirmation_ref: str,
        reservation: dict[str, Any],
    ) -> bool: ...


class InMemoryCommandJournal:
    """Small integration fallback and focused-test journal.

    It is process-local by design.  Production persistence is supplied by the
    DB owner through ``set_command_journal``; no command handler invents DB
    columns or writes arbitrary SQL.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def lookup(self, owner_id: str, command_name: str, idempotency_key: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._records.get((owner_id, command_name, idempotency_key))
            return dict(row) if row is not None else None

    async def claim(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        reservation: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._lock:
            prior = self._records.get((owner_id, command_name, idempotency_key))
            if prior is None:
                self._records[(owner_id, command_name, idempotency_key)] = {
                    "digest": digest,
                    "result": _copy_json(reservation),
                }
                return None
            if prior.get("digest") != digest:
                raise coach_store.CommandIdempotencyConflictError(
                    "idempotency key was already used with different parameters"
                )
            return {
                "digest": prior["digest"],
                "result": _copy_json(prior["result"]),
            }

    async def record(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        result: dict[str, Any],
    ) -> None:
        async with self._lock:
            prior = self._records.get((owner_id, command_name, idempotency_key))
            if prior is not None and prior.get("digest") != digest:
                raise coach_store.CommandIdempotencyConflictError(
                    "idempotency key was already used with different parameters"
                )
            self._records[(owner_id, command_name, idempotency_key)] = {
                "digest": digest,
                "result": _copy_json(result),
            }

    async def audit(self, owner_id: str, result: dict[str, Any]) -> None:
        async with self._lock:
            self.audit_events.append({"owner_id": owner_id, "result": _copy_json(result)})

    async def confirm_and_reserve(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        confirmation_ref: str,
        reservation: dict[str, Any],
    ) -> bool:
        async with self._lock:
            prior = self._records.get((owner_id, command_name, idempotency_key))
            if prior is not None and prior.get("digest") != digest:
                raise coach_store.CommandIdempotencyConflictError(
                    "idempotency key was already used with different parameters"
                )
        consumed = await coach_store.consume_command_confirmation(
            owner_id,
            command_name,
            digest,
            confirmation_ref,
        )
        if consumed:
            async with self._lock:
                self._records[(owner_id, command_name, idempotency_key)] = {
                    "digest": digest,
                    "result": _copy_json(reservation),
                }
        return consumed


class SqliteCommandJournal:
    """Persistent journal delegated to the canonical Coach store."""

    async def lookup(self, owner_id: str, command_name: str, idempotency_key: str) -> dict[str, Any] | None:
        return await coach_store.lookup_command_idempotency(owner_id, command_name, idempotency_key)

    async def claim(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        reservation: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await coach_store.claim_command_idempotency(
            owner_id, command_name, idempotency_key, digest, reservation,
        )

    async def record(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        result: dict[str, Any],
    ) -> None:
        await coach_store.store_command_idempotency(
            owner_id, command_name, idempotency_key, digest, result,
        )

    async def audit(self, owner_id: str, result: dict[str, Any]) -> None:
        await coach_store.append_command_audit(owner_id, result, _audit_context.get())

    async def confirm_and_reserve(
        self,
        owner_id: str,
        command_name: str,
        idempotency_key: str,
        digest: str,
        confirmation_ref: str,
        reservation: dict[str, Any],
    ) -> bool:
        return await coach_store.consume_command_confirmation_and_store_reservation(
            owner_id,
            command_name,
            digest,
            confirmation_ref,
            idempotency_key,
            reservation,
        )


async def _create_confirmation(
    owner_id: str,
    command_name: str,
    parameters: Mapping[str, Any],
    risk: str,
) -> dict[str, Any]:
    confirmation_ref = f"confirmation:{uuid.uuid4().hex}"
    digest = _idempotency_digest(command_name, dict(parameters))
    stored = await coach_store.create_command_confirmation(
        owner_id,
        command_name,
        digest,
        risk,
        _safe_parameter_summary(parameters),
        confirmation_ref,
    )
    return {**stored, "parameters_digest": digest}


async def _consume_confirmation(
    owner_id: str,
    command_name: str,
    parameters: Mapping[str, Any],
    confirmation_ref: Any,
) -> bool:
    if not isinstance(confirmation_ref, str) or not confirmation_ref.startswith("confirmation:"):
        return False
    return await coach_store.consume_command_confirmation(
        owner_id,
        command_name,
        _idempotency_digest(command_name, dict(parameters)),
        confirmation_ref,
    )



_audit_context: ContextVar[dict[str, Any]] = ContextVar("coach_command_audit_context", default={})


_journal_override: CommandJournal | None = None
_fallback_journal = SqliteCommandJournal()
_write_command_lock = asyncio.Lock()


def set_command_journal(journal: CommandJournal | None) -> None:
    """Install the DB-backed journal once its owner-scoped schema is available."""
    global _journal_override
    _journal_override = journal


def _journal() -> CommandJournal:
    return _journal_override or _fallback_journal


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _audit_ref() -> str:
    return f"audit:{uuid.uuid4().hex}"


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _risk_for(command_name: str) -> str:
    if command_name == "navigation.open":
        return "navigation"
    if command_name in _QUERY_COMMANDS:
        return "query"
    return "reversible_write"


def _safe_parameter_summary(parameters: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in {"plan_payload", "verification_targets", "evidence_refs"}:
            summary[key] = f"{key}:present"
        elif isinstance(value, (str, bool, int, float)) or value is None:
            summary[key] = value
    return summary


def _result(
    command_id: str,
    status: Literal["succeeded", "failed", "cancelled", "needs_confirmation", "unavailable"],
    *,
    result_ref: str | None = None,
    result: dict[str, Any] | list[dict[str, Any]] | None = None,
    ui_event: dict[str, Any] | None = None,
    confirmation: dict[str, Any] | None = None,
    warning_or_error: dict[str, str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": status,
        "audit_ref": _audit_ref(),
    }
    if result_ref is not None:
        out["result_ref"] = result_ref
    if result is not None:
        out["result"] = result
    if ui_event is not None:
        out["ui_event"] = ui_event
    if confirmation is not None:
        out["confirmation"] = confirmation
    if warning_or_error is not None:
        out["warning_or_error"] = warning_or_error
    return out


async def _finish(owner_id: str, result: dict[str, Any]) -> dict[str, Any]:
    # Every terminal outcome emits an audit callback.  The callback receives
    # only the already safe canonical result, never raw tool input/token/path.
    await _journal().audit(owner_id, result)
    return result


def _idempotency_conflict_result(command_id: str) -> dict[str, Any]:
    return _result(
        command_id,
        "failed",
        warning_or_error=_error(
            "idempotency_conflict",
            "idempotency key was already used with different parameters",
        ),
    )


async def _replay_idempotent_result(
    owner_id: str,
    command_id: str,
    prior: Mapping[str, Any],
) -> dict[str, Any]:
    replay = _copy_json(prior["result"])
    replay["command_id"] = command_id
    replay["audit_ref"] = _audit_ref()
    return await _finish(owner_id, replay)


def _command_id(value: Any) -> str:
    if isinstance(value, str) and value and len(value) <= 128:
        return value
    return f"command:{uuid.uuid4().hex}"


def _contains_forbidden_model_data(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_MODEL_KEYS or "path" in normalized or "credential" in normalized:
                return True
            if _contains_forbidden_model_data(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_model_data(item) for item in value)
    if isinstance(value, str):
        return bool(_PATH_OR_URL_TEXT_RE.search(value))
    return False


def _parse_ref(value: Any, expected_kind: str) -> tuple[int, str]:
    if not isinstance(value, str):
        raise ProductCommandError("invalid_reference", f"{expected_kind}_ref must be a stable reference")
    match = _REF_RE.fullmatch(value)
    if match is None or match.group("kind") != expected_kind:
        raise ProductCommandError("invalid_reference", f"{expected_kind}_ref must be a {expected_kind}: reference")
    return int(match.group("id")), value


def _require_mapping(value: Any, field: str = "parameters") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductCommandError("invalid_parameters", f"{field} must be an object")
    return dict(value)


def _idempotency_digest(command_name: str, parameters: dict[str, Any]) -> str:
    wire = json.dumps(
        {"command_name": command_name, "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _matches_frozen_copy(
    path: Path,
    fingerprint: object,
    *,
    source: Path | None = None,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    expected_mtime_ns = fingerprint.get("mtime_ns")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        return False
    if source is not None:
        try:
            source_stat = source.stat()
        except OSError:
            return False
        if (
            source_stat.st_size != expected_size
            or source_stat.st_mtime_ns != expected_mtime_ns
        ):
            return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha


def _freeze_video_source(path: Path) -> dict[str, object]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ProductCommandError(
            "source_unavailable",
            "Video source unavailable",
            kind="unavailable",
        ) from exc
    before_revision = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_revision = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_revision != after_revision:
        raise ProductCommandError(
            "source_unavailable",
            "Video source revision changed while freezing",
            kind="unavailable",
        )
    return {
        "sha256": digest.hexdigest(),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


def _safe_run(run: dict[str, Any]) -> dict[str, Any]:
    return kovaak_run_store.public_kovaak_run(run)


def _safe_analysis(session: dict[str, Any]) -> dict[str, Any]:
    error = session.get("error")
    safe_error = None
    if isinstance(error, dict):
        safe_error = {
            key: error[key]
            for key in ("schema_version", "category", "code", "message", "retryable")
            if key in error
        }
    return {
        "analysis_ref": f"analysis:{session['id']}",
        "id": session["id"],
        "status": session.get("status"),
        "analysis_type": session.get("analysis_type", "flicking"),
        "input_mode": session.get("input_mode", "video_fallback"),
        "run_ref": f"run:{session['kovaak_run_id']}" if session.get("kovaak_run_id") else None,
        "created_at": session.get("created_at"),
        "started_at": session.get("started_at"),
        "finished_at": session.get("finished_at"),
        "error": safe_error,
    }


def _safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    # training_plan_store already rejects secrets, paths and raw trace inputs.
    return {
        key: plan[key]
        for key in (
            "plan_id",
            "plan_ref",
            "status",
            "version",
            "version_ref",
            "plan_payload",
            "adjustment_reason",
            "evidence_refs",
            "verification_targets",
            "created_at",
            "updated_at",
        )
        if key in plan
    }


async def list_runs(owner_id: str) -> list[dict[str, Any]]:
    runs = await kovaak_run_store.list_kovaak_runs(owner_id)
    return [_safe_run(run) for run in runs]


async def get_run(owner_id: str, run_id: int) -> dict[str, Any]:
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    return _safe_run(run)


async def list_history(owner_id: str) -> list[dict[str, Any]]:
    rows = await queue.list_sessions(owner_id)
    metadata = await history_trends.session_history_metadata(owner_id)
    return [
        {
            key: value
            for key, value in {**row, **metadata.get(int(row["id"]), {})}.items()
            if key not in {"video_path", "csv_path", "input_snapshot", "result", "error"}
        }
        for row in rows
    ]


async def history_trend(owner_id: str, metric_key: str) -> dict[str, Any]:
    if not isinstance(metric_key, str) or not metric_key or len(metric_key) > 128:
        raise ProductCommandError("invalid_metric_key", "metric_key is required")
    return await history_trends.recent_trend_for_user(owner_id, metric_key)


async def _known_deleted_analysis(thread_id: int | None, analysis_id: int) -> bool:
    if thread_id is None:
        return False
    refs = await coach_store.list_analysis_refs(thread_id)
    return any(
        ref.get("analysis_session_id") == analysis_id and ref.get("status") == "deleted"
        for ref in refs
    )


async def get_analysis(owner_id: str, analysis_id: int, *, thread_id: int | None = None) -> dict[str, Any]:
    session = await queue.get_session(analysis_id)
    if session is None:
        if await _known_deleted_analysis(thread_id, analysis_id):
            raise ProductCommandError("deleted", "Analysis 已删除", kind="unavailable")
        raise ProductCommandError("not_found", "Analysis 不存在", kind="unavailable")
    if session.get("user_id") != owner_id:
        raise ProductCommandError("forbidden", "无权访问此 Analysis")
    return _safe_analysis(session)


async def compare_analyses(owner_id: str, current_id: int, baseline_id: int, metric_key: str, *, thread_id: int | None = None) -> dict[str, Any]:
    current = await get_analysis(owner_id, current_id, thread_id=thread_id)
    baseline = await get_analysis(owner_id, baseline_id, thread_id=thread_id)
    current_row = await queue.get_session(current_id)
    baseline_row = await queue.get_session(baseline_id)
    assert current_row is not None and baseline_row is not None
    if current_row.get("status") != "done" or baseline_row.get("status") != "done":
        raise ProductCommandError("analysis_not_ready", "Analysis 尚未完成", kind="unavailable")
    current_result = current_row.get("result")
    baseline_result = baseline_row.get("result")
    if not isinstance(current_result, dict) or not isinstance(baseline_result, dict):
        raise ProductCommandError("analysis_result_missing", "Analysis 结果不可用", kind="unavailable")
    compared = history_trends.compare_analysis_results(current_result, baseline_result, metric_key)
    return {
        **compared,
        "current_analysis_ref": current["analysis_ref"],
        "baseline_analysis_ref": baseline["analysis_ref"],
    }


async def create_analysis_from_run(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["input_native", "multimodal", "video_fallback"] | None = "input_native",
    cm_per_360: float | None = None,
    fov: float | None = None,
    managed_video_source: Path | None = None,
    managed_video_fingerprint: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Freeze a registered run and enqueue an Analysis through managed stores.

    Coach command calls use only ``input_native`` and never provide a source
    path.  The pre-existing desktop UI may opt into its separately validated
    multimodal/video-fallback capability through this same orchestration.
    """
    active = await queue.get_active_session(owner_id)
    if active is not None:
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    try:
        snapshot = await kovaak_run_store.build_analysis_input_snapshot(run_id, owner_id)
    except (LookupError, ValueError) as exc:
        raise ProductCommandError("input_unavailable", str(exc), kind="unavailable") from exc

    native_ready = bool(
        snapshot["sources"].get("stats")
        and snapshot["sources"].get("performance")
        and snapshot.get("trace")
    )
    if input_mode is None:
        input_mode = "multimodal" if native_ready and managed_video_source is not None else (
            "input_native" if native_ready else "video_fallback"
        )
    if input_mode == "input_native" and not native_ready:
        raise ProductCommandError("native_input_unavailable", "input_native 需要 Stats、Performance 和 Raw Input trace", kind="unavailable")
    if input_mode == "multimodal" and (not native_ready or managed_video_source is None):
        raise ProductCommandError("input_unavailable", "multimodal 需要完整 native evidence 和视频", kind="unavailable")
    if input_mode == "video_fallback" and managed_video_source is None:
        raise ProductCommandError("input_unavailable", "video_fallback 需要视频", kind="unavailable")

    video_fingerprint = None
    if managed_video_source is not None:
        video_fingerprint = (
            dict(managed_video_fingerprint)
            if isinstance(managed_video_fingerprint, Mapping)
            else _freeze_video_source(managed_video_source)
        )
        snapshot["sources"]["video"] = {
            "basename": managed_video_source.name,
            "fingerprint": video_fingerprint,
            "path": str(managed_video_source.resolve()),
            "availability": "available",
            "format_version": "mp4",
        }

    session_id = await queue.enqueue(
        owner_id,
        "",
        "",
        cm_per_360=cm_per_360,
        fov=fov,
        analysis_type="flicking",
        input_mode=input_mode,
        kovaak_run_id=run_id,
        input_snapshot=snapshot,
        status="uploading",
    )
    try:
        managed_video = ""
        managed_csv = ""
        workspace = session_dir(session_id)
        if managed_video_source is not None:
            video_destination = workspace / "video.mp4"
            try:
                copy_path_to_path(managed_video_source, video_destination)
            except OSError as exc:
                try:
                    observed_fingerprint = _freeze_video_source(managed_video_source)
                except ProductCommandError as source_exc:
                    raise source_exc from exc
                if observed_fingerprint != video_fingerprint:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Video source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not _matches_frozen_copy(
                video_destination,
                video_fingerprint,
                source=managed_video_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Video source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_video = str(video_destination)
        if input_mode == "video_fallback":
            stats_source = snapshot["sources"].get("stats")
            if not isinstance(stats_source, Mapping):
                raise ProductCommandError("input_unavailable", "Stats source unavailable", kind="unavailable")
            stats_path = stats_source.get("path")
            if not isinstance(stats_path, str) or not Path(stats_path).is_file():
                raise ProductCommandError("input_unavailable", "Stats source unavailable", kind="unavailable")
            csv_destination = workspace / "stats.csv"
            stats_source_path = Path(stats_path)
            copy_path_to_path(stats_source_path, csv_destination)
            if not _matches_frozen_copy(
                csv_destination,
                stats_source.get("fingerprint"),
                source=stats_source_path,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_csv = str(csv_destination)
        await queue.set_session_input_paths(session_id, owner_id, managed_video, managed_csv)
        if not await queue.finish_upload(session_id):
            raise ProductCommandError("upload_state_lost", "分析输入状态已失效，请重新提交", kind="unavailable")
    except ProductCommandError:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        raise
    except Exception as exc:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        raise ProductCommandError(
            "input_setup_failed",
            "无法建立分析输入快照",
        ) from exc
    return {"session_id": session_id, "analysis_ref": f"analysis:{session_id}"}


async def retry_analysis(owner_id: str, analysis_id: int, *, thread_id: int | None = None) -> dict[str, Any]:
    await get_analysis(owner_id, analysis_id, thread_id=thread_id)
    active = await queue.get_active_session(owner_id)
    if active is not None and int(active["id"]) != analysis_id:
        raise ProductCommandError(
            "active_analysis",
            "已有其它 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    try:
        updated = await queue.requeue_for_retry(analysis_id)
    except queue.RetryNotAllowed as exc:
        kind = "unavailable" if exc.code in {"not_found", "missing_video", "missing_csv", "missing_snapshot"} else "failed"
        raise ProductCommandError(exc.code, exc.message, kind=kind) from exc
    return _safe_analysis(updated)


async def _training_plan_store() -> Any:
    return importlib.import_module("webapp.backend.training_plan_store")


async def _execute_training_plan(owner_id: str, command_name: str, parameters: dict[str, Any], *, confirmed: bool) -> tuple[dict[str, Any], str]:
    store = await _training_plan_store()
    plan_ref = parameters.get("plan_ref")
    try:
        if command_name == "training_plan.generate_draft":
            plan = await store.generate_draft(
                owner_id,
                parameters.get("plan_payload"),
                evidence_refs=parameters.get("evidence_refs", ()),
                verification_targets=parameters.get("verification_targets"),
            )
        elif command_name == "training_plan.save":
            plan = await store.save_plan(owner_id, plan_ref)
        elif command_name == "training_plan.activate":
            plan = await store.activate_plan(owner_id, plan_ref, replace_active=confirmed)
        elif command_name == "training_plan.pause":
            plan = await store.pause_plan(owner_id, plan_ref)
        elif command_name == "training_plan.adjust":
            plan = await store.adjust_plan(
                owner_id,
                plan_ref,
                parameters.get("plan_payload"),
                adjustment_reason=parameters.get("adjustment_reason"),
                evidence_refs=parameters.get("evidence_refs"),
                verification_targets=parameters.get("verification_targets"),
            )
        elif command_name == "training_plan.review":
            plan = await store.review_plan(owner_id, plan_ref)
        else:  # pragma: no cover - caller validates whitelist
            raise ProductCommandError("unsupported_command", "unsupported Training Plan command")
    except getattr(store, "ActivePlanReplacementRequired") as exc:
        raise ProductCommandError(
            "active_plan_replacement_required",
            f"active plan {exc.active_plan_id} would be paused",
            kind="needs_confirmation",
        ) from exc
    except getattr(store, "PlanForbidden") as exc:
        raise ProductCommandError("forbidden", "无权访问此 Training Plan") from exc
    except getattr(store, "PlanNotFound") as exc:
        raise ProductCommandError("not_found", "Training Plan 不存在", kind="unavailable") from exc
    except getattr(store, "TrainingPlanError") as exc:
        raise ProductCommandError("invalid_training_plan", str(exc)) from exc
    safe_plan = _safe_plan(plan)
    return safe_plan, str(safe_plan.get("plan_ref") or safe_plan.get("plan_id"))


def _confirmation(
    command_name: str,
    parameters: dict[str, Any],
    *,
    reason: str | None = None,
    confirmation_ref: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    target_ref = next(
        (
            value
            for key, value in parameters.items()
            if key in {"run_ref", "analysis_ref", "plan_ref"} and isinstance(value, str)
        ),
        None,
    )
    return {
        "schema_version": "coach_product_command_confirmation.v1",
        "confirmation_ref": confirmation_ref,
        "command_name": command_name,
        "target_ref": target_ref,
        "parameters": {
            key: value
            for key, value in parameters.items()
            if key not in {"plan_payload"}
        },
        "changes": reason or "This action changes your local product state.",
        "irreversible_effects": [],
        "cancel_action": {"type": "cancel_command"},
        "expires_at": expires_at,
    }


def _navigation_event(parameters: dict[str, Any]) -> dict[str, Any]:
    target = parameters.get("target")
    if target not in {"history", "analysis", "analysis_section", "flick_event", "evidence", "video_time"}:
        raise ProductCommandError("invalid_navigation_target", "unsupported navigation target")
    if target == "history":
        return {"schema_version": "coach_ui_event.v1", "kind": "history"}

    analysis_ref = parameters.get("analysis_ref") or parameters.get("ref")
    _parse_ref(analysis_ref, "analysis")
    if target in {"analysis", "analysis_section"}:
        event: dict[str, Any] = {
            "schema_version": "coach_ui_event.v1",
            "kind": "analysis",
            "analysis_ref": analysis_ref,
        }
        section = parameters.get("section")
        if section is not None:
            if section not in {"overview", "metrics", "diagnosis", "training", "evidence"}:
                raise ProductCommandError("invalid_navigation_target", "unsupported Analysis section")
            event["section"] = section
        return event
    if target == "flick_event":
        event_ref = parameters.get("event_ref")
        if not isinstance(event_ref, str) or not re.fullmatch(r"event:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", event_ref):
            raise ProductCommandError("invalid_navigation_target", "event_ref must be a stable event reference")
        return {
            "schema_version": "coach_ui_event.v1",
            "kind": "flick",
            "analysis_ref": analysis_ref,
            "event_ref": event_ref,
        }
    if target == "evidence":
        evidence_ref = parameters.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not re.fullmatch(r"evidence:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", evidence_ref):
            raise ProductCommandError("invalid_navigation_target", "evidence_ref must be a stable evidence reference")
        return {
            "schema_version": "coach_ui_event.v1",
            "kind": "evidence",
            "analysis_ref": analysis_ref,
            "evidence_ref": evidence_ref,
        }
    value = parameters.get("time_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise ProductCommandError("invalid_navigation_target", "video time must be a non-negative number")
    return {
        "schema_version": "coach_ui_event.v1",
        "kind": "video_time",
        "analysis_ref": analysis_ref,
        "time_ms": float(value),
    }


async def execute_product_command(
    owner_id: str,
    envelope: Mapping[str, Any],
    *,
    authorization_source: Literal["explicit_user_request", "confirmed", "system_safe", "coach_inferred"] = "coach_inferred",
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Execute one allow-listed product command and return the canonical result."""
    command_id = _command_id(envelope.get("command_id") if isinstance(envelope, Mapping) else None)
    if not isinstance(owner_id, str) or not owner_id.strip():
        return await _finish("", _result(command_id, "failed", warning_or_error=_error("invalid_owner", "owner is required")))
    if not isinstance(envelope, Mapping):
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("invalid_command", "command must be an object")))
    envelope_keys = {
        key.strip().lower().replace("-", "_")
        for key in envelope
        if isinstance(key, str)
    }
    if _FORBIDDEN_MODEL_KEYS & envelope_keys:
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("untrusted_field", "model may not provide owner, risk, paths, URLs or credentials")))
    command_name = envelope.get("command_name")
    if not isinstance(command_name, str) or command_name not in _COMMANDS:
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("unsupported_command", "command is not allowed")))
    try:
        parameters = _require_mapping(envelope.get("parameters", {}))
    except ProductCommandError as exc:
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error(exc.code, exc.message)))
    if _contains_forbidden_model_data(parameters):
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("untrusted_field", "paths, URLs, credentials and raw traces are not accepted")))

    idempotency_key = envelope.get("idempotency_key")
    digest = None
    token = _audit_context.set({
        "thread_id": thread_id,
        "user_message_ref": envelope.get("user_message_ref"),
        "command_name": command_name,
        "risk": _risk_for(command_name),
        "authorization_source": authorization_source,
        "idempotency_key": idempotency_key if isinstance(idempotency_key, str) else None,
        "parameters_digest": None,
        "safe_parameters_summary": _safe_parameter_summary(parameters),
    })
    try:
        if command_name in _WRITE_COMMANDS:
            async with _write_command_lock:
                return await _execute_product_command_inner(
                    owner_id,
                    envelope,
                    command_id=command_id,
                    command_name=command_name,
                    parameters=parameters,
                    authorization_source=authorization_source,
                    thread_id=thread_id,
                    idempotency_key=idempotency_key,
                )
        return await _execute_product_command_inner(
            owner_id,
            envelope,
            command_id=command_id,
            command_name=command_name,
            parameters=parameters,
            authorization_source=authorization_source,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
        )
    finally:
        _audit_context.reset(token)


async def execute_trusted_analysis_create(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["input_native", "multimodal", "video_fallback"] | None,
    cm_per_360: float | None,
    fov: float | None,
    managed_video_source: Path | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Execute the validated desktop Analysis write through the shared journal."""
    command_name = "analysis.create_from_run"
    command_id = _command_id(None)
    if not isinstance(owner_id, str) or not owner_id.strip():
        return await _finish("", _result(command_id, "failed", warning_or_error=_error("invalid_owner", "owner is required")))

    source_identity = None
    video_fingerprint = None
    if managed_video_source is not None:
        try:
            video_fingerprint = _freeze_video_source(managed_video_source)
        except ProductCommandError as exc:
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "unavailable",
                    warning_or_error=_error(exc.code, exc.message),
                ),
            )
        source_identity = hashlib.sha256(
            json.dumps(
                video_fingerprint,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        ).hexdigest()
    parameters = {
        "run_ref": f"run:{run_id}",
        "input_mode": input_mode,
        "cm_per_360": cm_per_360,
        "fov": fov,
        "video_source_identity": source_identity,
    }
    token = _audit_context.set({
        "thread_id": None,
        "user_message_ref": None,
        "command_name": command_name,
        "risk": _risk_for(command_name),
        "authorization_source": "explicit_user_request",
        "idempotency_key": idempotency_key if isinstance(idempotency_key, str) else None,
        "parameters_digest": None,
        "safe_parameters_summary": {
            "run_ref": f"run:{run_id}",
            "input_mode": input_mode,
            "cm_per_360": cm_per_360,
            "fov": fov,
            "has_video_source": managed_video_source is not None,
        },
    })
    try:
        async with _write_command_lock:
            return await _execute_product_command_inner(
                owner_id,
                {},
                command_id=command_id,
                command_name=command_name,
                parameters=parameters,
                authorization_source="explicit_user_request",
                thread_id=None,
                idempotency_key=idempotency_key,
                trusted_analysis_args={
                    "input_mode": input_mode,
                    "cm_per_360": cm_per_360,
                    "fov": fov,
                    "managed_video_source": managed_video_source,
                    "managed_video_fingerprint": video_fingerprint,
                },
            )
    finally:
        _audit_context.reset(token)


async def _execute_product_command_inner(
    owner_id: str,
    envelope: Mapping[str, Any],
    *,
    command_id: str,
    command_name: str,
    parameters: dict[str, Any],
    authorization_source: Literal["explicit_user_request", "confirmed", "system_safe", "coach_inferred"],
    thread_id: int | None,
    idempotency_key: Any,
    trusted_analysis_args: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    digest = None
    if command_name in _WRITE_COMMANDS:
        if idempotency_key is None:
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(
                        "idempotency_key_required",
                        "write commands require an idempotency_key",
                    ),
                ),
            )
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
            return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("invalid_idempotency_key", "idempotency_key is invalid")))
        try:
            digest = _idempotency_digest(command_name, parameters)
            context = dict(_audit_context.get())
            context["parameters_digest"] = digest
            _audit_context.set(context)
        except (TypeError, ValueError):
            return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("invalid_parameters", "parameters are not JSON-safe")))
        prior = await _journal().lookup(owner_id, command_name, idempotency_key)
        if prior is not None:
            if prior.get("digest") != digest:
                return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("idempotency_conflict", "idempotency key was already used with different parameters")))
            replay = _copy_json(prior["result"])
            if not (
                authorization_source == "confirmed"
                and replay.get("status") == "needs_confirmation"
            ):
                return await _replay_idempotent_result(owner_id, command_id, prior)

    reservation = None
    reservation_recorded = False
    if command_name in _WRITE_COMMANDS and isinstance(idempotency_key, str) and digest is not None:
        reservation = _result(
            command_id,
            "unavailable",
            warning_or_error=_error(
                "idempotency_outcome_unknown",
                "a previous command attempt may have completed; inspect current state before retrying",
            ),
        )

    if authorization_source == "confirmed":
        confirmation_ref = envelope.get("confirmation_ref")
        if (
            reservation is not None
            and isinstance(confirmation_ref, str)
            and confirmation_ref.startswith("confirmation:")
        ):
            try:
                consumed = await _journal().confirm_and_reserve(
                    owner_id,
                    command_name,
                    idempotency_key,
                    digest,
                    confirmation_ref,
                    reservation,
                )
            except coach_store.CommandIdempotencyConflictError:
                return await _finish(owner_id, _idempotency_conflict_result(command_id))
            reservation_recorded = consumed
        else:
            consumed = await _consume_confirmation(
                owner_id, command_name, parameters, confirmation_ref,
            )
        if not consumed:
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "failed",
                    warning_or_error=_error("invalid_confirmation", "confirmation is missing, expired or already used"),
                ),
            )

    if command_name in _WRITE_COMMANDS and authorization_source == "coach_inferred":
        pending = await _create_confirmation(owner_id, command_name, parameters, _risk_for(command_name))
        result = _result(
            command_id,
            "needs_confirmation",
            confirmation=_confirmation(
                command_name,
                parameters,
                confirmation_ref=pending["confirmation_ref"],
                expires_at=pending["expires_at"],
            ),
            warning_or_error=_error("confirmation_required", "Coach-inferred writes require user confirmation"),
        )
        try:
            prior = await _journal().claim(
                owner_id, command_name, idempotency_key, digest, result,
            )
        except coach_store.CommandIdempotencyConflictError:
            return await _finish(owner_id, _idempotency_conflict_result(command_id))
        if prior is not None:
            return await _replay_idempotent_result(owner_id, command_id, prior)
        return await _finish(owner_id, result)

    if reservation is not None and not reservation_recorded:
        try:
            prior = await _journal().claim(
                owner_id, command_name, idempotency_key, digest, reservation,
            )
        except coach_store.CommandIdempotencyConflictError:
            return await _finish(owner_id, _idempotency_conflict_result(command_id))
        if prior is not None:
            return await _replay_idempotent_result(owner_id, command_id, prior)

    try:
        if command_name == "run.list":
            result = _result(command_id, "succeeded", result=await list_runs(owner_id))
        elif command_name == "run.get":
            run_id, ref = _parse_ref(parameters.get("run_ref"), "run")
            result = _result(command_id, "succeeded", result_ref=ref, result=await get_run(owner_id, run_id))
        elif command_name == "history.list":
            result = _result(command_id, "succeeded", result=await list_history(owner_id))
        elif command_name == "history.trend":
            trend = await history_trend(owner_id, parameters.get("metric_key"))
            result = _result(command_id, "succeeded", result=trend)
        elif command_name == "analysis.get":
            analysis_id, ref = _parse_ref(parameters.get("analysis_ref"), "analysis")
            result = _result(command_id, "succeeded", result_ref=ref, result=await get_analysis(owner_id, analysis_id, thread_id=thread_id))
        elif command_name == "analysis.compare":
            current_id, _ = _parse_ref(parameters.get("current_analysis_ref"), "analysis")
            baseline_id, _ = _parse_ref(parameters.get("baseline_analysis_ref"), "analysis")
            compared = await compare_analyses(owner_id, current_id, baseline_id, parameters.get("metric_key"), thread_id=thread_id)
            result = _result(command_id, "succeeded", result=compared)
        elif command_name == "navigation.open":
            result = _result(command_id, "succeeded", ui_event=_navigation_event(parameters))
        elif command_name == "analysis.create_from_run":
            run_id, _ = _parse_ref(parameters.get("run_ref"), "run")
            if trusted_analysis_args is None:
                created = await create_analysis_from_run(
                    owner_id,
                    run_id,
                    input_mode="input_native",
                    cm_per_360=parameters.get("cm_per_360"),
                    fov=parameters.get("fov"),
                )
            else:
                created = await create_analysis_from_run(
                    owner_id,
                    run_id,
                    **trusted_analysis_args,
                )
            analysis_ref = created.get("analysis_ref") or f"analysis:{created['session_id']}"
            result = _result(
                command_id,
                "succeeded",
                result_ref=analysis_ref,
                result={**created, "analysis_ref": analysis_ref},
                ui_event={
                    "schema_version": "coach_ui_event.v1",
                    "kind": "analysis",
                    "analysis_ref": analysis_ref,
                },
            )
        elif command_name == "analysis.retry":
            analysis_id, ref = _parse_ref(parameters.get("analysis_ref"), "analysis")
            retried = await retry_analysis(owner_id, analysis_id, thread_id=thread_id)
            result = _result(command_id, "succeeded", result_ref=ref, result=retried)
        else:
            safe_plan, plan_ref = await _execute_training_plan(
                owner_id,
                command_name,
                parameters,
                confirmed=authorization_source == "confirmed",
            )
            result = _result(command_id, "succeeded", result_ref=plan_ref, result=safe_plan)
    except ProductCommandError as exc:
        if exc.kind == "needs_confirmation":
            pending = await _create_confirmation(owner_id, command_name, parameters, _risk_for(command_name))
            result = _result(
                command_id,
                "needs_confirmation",
                confirmation=_confirmation(
                    command_name,
                    parameters,
                    reason=exc.message,
                    confirmation_ref=pending["confirmation_ref"],
                    expires_at=pending["expires_at"],
                ),
                warning_or_error=_error(exc.code, exc.message),
            )
        elif exc.kind == "unavailable":
            result = _result(
                command_id,
                "unavailable",
                result_ref=exc.result_ref,
                warning_or_error=_error(exc.code, exc.message),
            )
        else:
            result = _result(command_id, "failed", warning_or_error=_error(exc.code, exc.message))
    except Exception:
        # Do not leak store internals, input payloads, paths, credentials or traces.
        result = _result(command_id, "failed", warning_or_error=_error("internal_error", "product command could not be completed"))
    return await _record_and_finish(owner_id, command_name, idempotency_key, digest, result)


async def _record_and_finish(
    owner_id: str,
    command_name: str,
    idempotency_key: Any,
    digest: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    if command_name in _WRITE_COMMANDS and isinstance(idempotency_key, str) and digest is not None:
        try:
            await _journal().record(owner_id, command_name, idempotency_key, digest, result)
        except coach_store.CommandIdempotencyConflictError:
            result = _idempotency_conflict_result(result["command_id"])
    return await _finish(owner_id, result)


@dataclass(frozen=True)
class _ToolBridge:
    owner_id: str
    thread_id: int
    user_message_ref: str
    expires_at: float
    max_calls: int
    calls: int


_tool_bridges: dict[str, _ToolBridge] = {}
_tool_bridge_lock = asyncio.Lock()


def _bridge_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_tool_bridge(
    owner_id: str,
    thread_id: int,
    user_message_ref: str,
    endpoint: str,
    desktop_token: str | None = None,
    ttl_seconds: int = 300,
    max_calls: int = 8,
) -> dict[str, Any]:
    """Issue an in-memory, turn-scoped bearer bridge for one Coach turn.

    The endpoint is restricted to the fixed loopback product route.  An
    optional desktop token is a transport gate for the desktop middleware, not
    a product principal; it is not retained by this registry or audit journal.
    """
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.path != TOOL_BRIDGE_ENDPOINT
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("tool bridge endpoint must be the fixed loopback product route")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(thread_id, int) or thread_id < 1:
        raise ValueError("owner_id and thread_id are required")
    if not isinstance(user_message_ref, str) or not user_message_ref or len(user_message_ref) > 160:
        raise ValueError("user_message_ref is required")
    if desktop_token is not None and (not isinstance(desktop_token, str) or not desktop_token):
        raise ValueError("desktop_token is invalid")
    if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 900:
        raise ValueError("ttl_seconds must be between 1 and 900")
    if not isinstance(max_calls, int) or not 1 <= max_calls <= 32:
        raise ValueError("max_calls must be between 1 and 32")
    token = secrets.token_urlsafe(32)  # 256 random bits before URL-safe encoding.
    expires_at = time.time() + ttl_seconds
    _tool_bridges[_bridge_digest(token)] = _ToolBridge(
        owner_id=owner_id,
        thread_id=thread_id,
        user_message_ref=user_message_ref,
        expires_at=expires_at,
        max_calls=max_calls,
        calls=0,
    )
    bridge: dict[str, Any] = {
        "schema_version": "coach_tool_bridge.v1",
        "turn_id": f"turn:{uuid.uuid4().hex}",
        "endpoint": endpoint,
        "bearer_token": token,
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        "user_message_ref": user_message_ref,
    }
    if desktop_token is not None:
        bridge["desktop_token"] = desktop_token
    return bridge


async def execute_tool_bridge(bearer_token: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute an untrusted tool payload under its short-lived bridge principal."""
    if not isinstance(bearer_token, str) or not bearer_token:
        return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    digest = _bridge_digest(bearer_token)
    async with _tool_bridge_lock:
        bridge = _tool_bridges.get(digest)
        if bridge is None or bridge.expires_at <= time.time() or bridge.calls >= bridge.max_calls:
            _tool_bridges.pop(digest, None)
            return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
        _tool_bridges[digest] = _ToolBridge(
            owner_id=bridge.owner_id,
            thread_id=bridge.thread_id,
            user_message_ref=bridge.user_message_ref,
            expires_at=bridge.expires_at,
            max_calls=bridge.max_calls,
            calls=bridge.calls + 1,
        )
    if (
        not isinstance(payload, Mapping)
        or any(not isinstance(key, str) for key in payload)
        or set(payload) - _TOOL_BRIDGE_PAYLOAD_KEYS
    ):
        return _result(
            "command:bridge",
            "failed",
            warning_or_error=_error(
                "untrusted_field",
                "Coach tool may not provide authorization or confirmation fields",
            ),
        )
    trusted_payload = {
        key: payload[key]
        for key in _TOOL_BRIDGE_PAYLOAD_KEYS
        if key in payload
    }
    trusted_payload["user_message_ref"] = bridge.user_message_ref
    result = await execute_product_command(
        bridge.owner_id,
        trusted_payload,
        authorization_source="coach_inferred",
        thread_id=bridge.thread_id,
    )
    safe_result = _copy_json(result)
    confirmation = safe_result.get("confirmation")
    if isinstance(confirmation, dict):
        confirmation.pop("confirmation_ref", None)
    return safe_result


async def revoke_tool_bridge(bearer_token: str) -> bool:
    if not isinstance(bearer_token, str) or not bearer_token:
        return False
    async with _tool_bridge_lock:
        return _tool_bridges.pop(_bridge_digest(bearer_token), None) is not None
