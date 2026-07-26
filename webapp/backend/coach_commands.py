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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from kovaak_tracker.analysis_evidence import (
    EvidenceKeyRegistry,
    build_processed_event_table_catalog,
    build_page_descriptor_v1,
    page_normalized_outcomes,
    validate_normalized_outcome_timeline_v1,
)

from . import coach_store, evidence_store, history_trends, kovaak_run_store, queue
from .contracts import project_evidence_segment
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
    "profile.aiming.snapshot",
    "navigation.open",
    "training_plan.review",
}
_EVIDENCE_QUERY_COMMANDS = frozenset({
    "analysis.metrics.distribution",
    "analysis.evidence.list",
    "analysis.evidence.signal_window",
    "analysis.evidence.compare",
    "analysis.run_facts.get",
    "analysis.outcomes.timeline",
    "analysis.events.list",
    "analysis.events.get",
    "analysis.events.rank",
    "analysis.events.filter",
    "analysis.events.aggregate",
    "analysis.events.co_occurrence",
    "analysis.events.sequence",
})
_WRITE_COMMANDS = {
    "analysis.create_from_run",
    "analysis.retry",
    "training_plan.generate_draft",
    "training_plan.save",
    "training_plan.activate",
    "training_plan.pause",
    "training_plan.adjust",
}
_EXPLICIT_USER_FACT_COMMANDS = {
    "training_plan.item.add",
    "training_plan.execution.record",
    "training_plan.retest.record",
}
_WRITE_COMMANDS |= _EXPLICIT_USER_FACT_COMMANDS
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
    context = _audit_context.get()
    stored = await coach_store.create_command_confirmation(
        owner_id,
        command_name,
        digest,
        risk,
        _safe_parameter_summary(parameters),
        parameters,
        confirmation_ref,
        idempotency_key=(
            context.get("idempotency_key")
            if isinstance(context.get("idempotency_key"), str)
            else None
        ),
        thread_id=(context.get("thread_id") if isinstance(context.get("thread_id"), int) else None),
        user_message_ref=(
            context.get("user_message_ref")
            if isinstance(context.get("user_message_ref"), str)
            else None
        ),
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
        if key == "cursor":
            continue
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


def _matches_frozen_hard_link(
    path: Path,
    source: Path,
    fingerprint: object,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
    ):
        return False
    try:
        if path.is_symlink() or not path.is_file() or not path.samefile(source):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha
    except OSError:
        return False


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
    return await kovaak_run_store.list_kovaak_run_summaries(owner_id)


async def get_run(owner_id: str, run_id: int) -> dict[str, Any]:
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    return _safe_run(run)


async def list_history(owner_id: str) -> list[dict[str, Any]]:
    return await queue.list_sessions(owner_id)


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


def _analysis_type_for_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Keep the persisted request type aligned with the reviewed dispatch family."""
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping):
        return "flicking"
    family = resolution.get("aim_family")
    return {
        "dynamic_clicking": "dynamic_clicking",
        "continuous_tracking": "continuous_tracking",
        "target_switching": "target_switching",
        "static_clicking": "flicking",
    }.get(family, "flicking")


async def create_analysis_from_run(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["input_native", "multimodal", "video_fallback"] | None = "input_native",
    cm_per_360: float | None = None,
    fov: float | None = None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
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
    run_video = snapshot["sources"].get("video")
    run_video_source = None
    if managed_video_source is None and isinstance(run_video, Mapping):
        run_video_path = run_video.get("path")
        run_video_fingerprint = run_video.get("fingerprint")
        if (
            isinstance(run_video_path, str)
            and Path(run_video_path).is_file()
            and isinstance(run_video_fingerprint, Mapping)
        ):
            run_video_source = Path(run_video_path)
    video_ready = managed_video_source is not None or run_video_source is not None
    if input_mode is None:
        input_mode = "multimodal" if native_ready and video_ready else (
            "input_native" if native_ready else "video_fallback"
        )
    if input_mode == "input_native" and not native_ready:
        raise ProductCommandError("native_input_unavailable", "input_native 需要 Stats、Performance 和 Raw Input trace", kind="unavailable")
    if input_mode == "multimodal" and (not native_ready or not video_ready):
        raise ProductCommandError("input_unavailable", "multimodal 需要完整 native evidence 和视频", kind="unavailable")
    if input_mode == "video_fallback" and (
        not snapshot["sources"].get("stats") or not video_ready
    ):
        raise ProductCommandError("input_unavailable", "video_fallback 需要视频", kind="unavailable")

    video_fingerprint = None
    if managed_video_source is not None:
        video_fingerprint = (
            dict(managed_video_fingerprint)
            if isinstance(managed_video_fingerprint, Mapping)
            else _freeze_video_source(managed_video_source)
        )
        run_video_fingerprint = (
            run_video.get("fingerprint") if isinstance(run_video, Mapping) else None
        )
        preserves_run_identity = (
            isinstance(run_video, Mapping)
            and isinstance(run_video_fingerprint, Mapping)
            and run_video_fingerprint.get("sha256") == video_fingerprint.get("sha256")
            and run_video_fingerprint.get("size") == video_fingerprint.get("size")
        )
        snapshot["sources"]["video"] = {
            **(dict(run_video) if preserves_run_identity else {}),
            "basename": managed_video_source.name,
            "fingerprint": video_fingerprint,
            "path": str(managed_video_source.resolve()),
            "availability": "available",
            "format_version": "mp4",
        }
    elif run_video_source is not None and isinstance(run_video, Mapping):
        video_fingerprint = dict(run_video["fingerprint"])

    if input_mode == "input_native":
        snapshot["sources"].pop("video", None)
    elif input_mode == "video_fallback":
        snapshot["sources"] = {
            kind: source
            for kind, source in snapshot["sources"].items()
            if kind in {"stats", "video"}
        }
        snapshot["trace"] = None

    session_id = await queue.enqueue(
        owner_id,
        "",
        "",
        cm_per_360=cm_per_360,
        fov=fov,
        profile_default=dict(profile_default) if isinstance(profile_default, Mapping) else None,
        manual_override=dict(manual_override) if isinstance(manual_override, Mapping) else None,
        analysis_type=_analysis_type_for_snapshot(snapshot),
        input_mode=input_mode,
        kovaak_run_id=run_id,
        input_snapshot=snapshot,
        status="uploading",
    )
    try:
        managed_video = ""
        managed_csv = ""
        workspace = session_dir(session_id)
        if managed_video_source is not None and input_mode in {
            "multimodal", "video_fallback",
        }:
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
        elif run_video_source is not None and input_mode in {
            "multimodal", "video_fallback",
        }:
            video_destination = workspace / "video.mp4"
            workspace.mkdir(parents=True, exist_ok=True)
            os.link(run_video_source, video_destination)
            if not _matches_frozen_hard_link(
                video_destination,
                run_video_source,
                run_video_fingerprint,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Run video revision changed before managed link",
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
        kind = "unavailable" if exc.code in {
            "active_analysis", "not_found", "missing_video", "missing_csv", "missing_snapshot",
        } else "failed"
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


async def _execute_training_plan_fact(
    owner_id: str,
    command_name: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    store = await _training_plan_store()
    try:
        if command_name == "training_plan.item.add":
            value = await store.add_plan_item(
                owner_id,
                parameters.get("plan_ref"),
                parameters.get("item_payload"),
                plan_version=parameters.get("plan_version"),
            )
            result_ref = value["item_ref"]
        elif command_name == "training_plan.execution.record":
            value = await store.record_user_execution(
                owner_id,
                parameters.get("item_ref"),
                scenario_ref=parameters.get("scenario_ref"),
                run_refs=parameters.get("run_refs"),
                planned_dose=parameters.get("planned_dose"),
                completed_dose=parameters.get("completed_dose"),
                completion_status=parameters.get("completion_status"),
                user_feedback=parameters.get("user_feedback"),
            )
            result_ref = value["execution_ref"]
        elif command_name == "training_plan.retest.record":
            value = await store.record_retest(
                owner_id,
                parameters.get("item_ref"),
                kind=parameters.get("kind"),
                expected_metric_ref=parameters.get("expected_metric_ref"),
                expected_direction=parameters.get("expected_direction"),
                analysis_refs=parameters.get("analysis_refs"),
                comparability=parameters.get("comparability"),
                result=parameters.get("result"),
                limitations=parameters.get("limitations"),
            )
            result_ref = value["retest_ref"]
        else:  # pragma: no cover - caller validates whitelist
            raise ProductCommandError("unsupported_command", "unsupported Training Plan fact command")
    except getattr(store, "PlanForbidden") as exc:
        raise ProductCommandError("forbidden", "无权访问此 Training Plan") from exc
    except getattr(store, "PlanNotFound") as exc:
        raise ProductCommandError("not_found", "Training Plan 条目不存在", kind="unavailable") from exc
    except getattr(store, "TrainingPlanError") as exc:
        raise ProductCommandError("invalid_training_plan", str(exc)) from exc
    safe_value = _copy_json(value)
    if isinstance(safe_value, dict):
        safe_value.pop("owner_id", None)
    return safe_value, str(result_ref)


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
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    idempotency_key: str | None = None,
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
        "profile_default": dict(profile_default) if isinstance(profile_default, Mapping) else None,
        "manual_override": dict(manual_override) if isinstance(manual_override, Mapping) else None,
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
                    "profile_default": dict(profile_default) if isinstance(profile_default, Mapping) else None,
                    "manual_override": dict(manual_override) if isinstance(manual_override, Mapping) else None,
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
    if (
        command_name in _EXPLICIT_USER_FACT_COMMANDS
        and authorization_source != "explicit_user_request"
    ):
        return await _finish(
            owner_id,
            _result(
                command_id,
                "failed",
                warning_or_error=_error(
                    "explicit_user_required",
                    "执行记录和复测只能由用户明确提交",
                ),
            ),
        )
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
        elif command_name == "profile.aiming.snapshot":
            if parameters:
                raise ProductCommandError(
                    "invalid_parameters", "profile snapshot does not accept parameters",
                )
            from . import aiming_profile_store

            snapshot = await aiming_profile_store.get_profile_snapshot(owner_id)
            result = _result(
                command_id,
                "succeeded",
                result_ref=snapshot["profile_ref"],
                result=snapshot,
            )
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
            result = _result(
                command_id,
                "succeeded",
                result_ref=retried.get("analysis_ref") or ref,
                result=retried,
            )
        elif command_name in _EXPLICIT_USER_FACT_COMMANDS:
            fact, fact_ref = await _execute_training_plan_fact(
                owner_id, command_name, parameters,
            )
            result = _result(
                command_id, "succeeded", result_ref=fact_ref, result=fact,
            )
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


_EVIDENCE_QUERY_RESULT_SCHEMA = "coach_evidence_query_result.v1"
_EVIDENCE_AUDIT_SCHEMA = "coach_evidence_audit.v1"
_OUTCOME_SERIES_MAX = 8
_METRIC_KEYS_MAX = 8
_LIST_MAX = 20
_SIGNAL_CHANNEL_MAX = 4
_SIGNAL_POINTS_PER_CHANNEL = 600
_FACT_SECTION_MAX = 8
_FORBIDDEN_EVIDENCE_PARAMETER_KEYS = frozenset({
    "start_ms", "end_ms", "time_ms", "frame", "frame_index", "artifact_ref",
    "path", "sql", "python", "code", "query",
})


def _canonical_wire_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _evidence_result(command_id: str, *, result_ref: str, result: dict[str, Any]) -> dict[str, Any]:
    return _result(
        command_id,
        "succeeded",
        result_ref=result_ref,
        result={"schema_version": _EVIDENCE_QUERY_RESULT_SCHEMA, **result},
    )


def _stable_ref_list(value: object, *, field_name: str, max_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", f"{field_name} must be a bounded list")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 240:
            raise ProductCommandError("invalid_parameters", f"{field_name} contains an invalid ref")
        values.append(item)
    return values


def _string_list(value: object, *, field_name: str, max_items: int, allow_empty: bool = True) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", f"{field_name} must be a bounded list")
    if not all(isinstance(item, str) and item.strip() and len(item) <= 160 for item in value):
        raise ProductCommandError("invalid_parameters", f"{field_name} contains an invalid key")
    return [item.strip() for item in value]


def _exact_parameters(parameters: dict[str, Any], allowed: set[str]) -> None:
    if set(parameters) - allowed:
        raise ProductCommandError("invalid_parameters", "unsupported evidence query parameters")


def _require_reachable(bridge: _ToolBridge, ref: str) -> None:
    if ref not in bridge.reachable_refs:
        raise ProductCommandError("unreachable_ref", "reference was not reached in this Coach turn")


def _analysis_ref_from_segment(ref: str) -> str:
    if ":segment:" not in ref:
        raise ProductCommandError("invalid_reference", "segment_ref is invalid")
    analysis_ref = ref.split(":segment:", 1)[0]
    _parse_ref(analysis_ref, "analysis")
    return analysis_ref


async def _load_evidence_for_bridge(
    bridge: _ToolBridge,
    analysis_ref: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    _require_reachable(bridge, analysis_ref)
    analysis_id, _ = _parse_ref(analysis_ref, "analysis")
    session = await queue.get_session(analysis_id)
    if session is None or session.get("user_id") != bridge.owner_id:
        raise ProductCommandError("forbidden", "evidence is unavailable", kind="unavailable")
    if session.get("status") != "done":
        raise ProductCommandError("analysis_not_ready", "Analysis 尚未完成", kind="unavailable")
    raw_result = session.get("result")
    if not isinstance(raw_result, dict):
        raise ProductCommandError("analysis_result_missing", "Analysis 结果不可用", kind="unavailable")
    safe_ref = ((raw_result.get("evidence") or {}).get("derived_artifact"))
    if not isinstance(safe_ref, dict):
        raise ProductCommandError("evidence_unavailable", "Analysis 没有可查询的 evidence", kind="unavailable")
    artifact = await evidence_store.read_analysis_evidence_artifact(
        owner_id=bridge.owner_id,
        analysis_ref=analysis_ref,
        artifact_ref=safe_ref.get("artifact_ref"),
        evidence_revision=safe_ref.get("evidence_revision"),
    )
    return artifact, safe_ref, analysis_ref


def _segment(artifact: dict[str, Any], segment_ref: str) -> dict[str, Any]:
    for item in artifact.get("evidence_segments", []):
        if item.get("segment_id") == segment_ref:
            return item
    raise ProductCommandError("not_found", "EvidenceSegment 不存在", kind="unavailable")


def _safe_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return project_evidence_segment(segment)


def _safe_metric(metric: dict[str, Any]) -> dict[str, Any]:
    distribution = metric.get("distribution")
    if isinstance(distribution, dict):
        distribution = {
            key: value
            for key, value in distribution.items()
            if key != "histogram_bins" or isinstance(value, list) and len(value) <= 16
        }
    return {
        key: value
        for key, value in {
            "metric_key": metric.get("metric_key"),
            "metric_version": metric.get("metric_version"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "classification": metric.get("classification"),
            "provenance": metric.get("provenance"),
            "population": metric.get("population"),
            "distribution": distribution,
            "condition_refs": metric.get("condition_refs", []),
            "event_refs": metric.get("event_refs", []),
            "evidence_segment_refs": metric.get("evidence_segment_refs", []),
            "coverage": metric.get("coverage"),
            "confidence": metric.get("confidence"),
            "limitations": metric.get("limitations", []),
        }.items()
        if value is not None
    }


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in ("event_id", "event_kind", "start_ms", "end_ms", "actor_refs", "source_refs", "confidence", "attributes", "limitations")
        if key in event
    }


def _analysis_ref_from_table(ref: str) -> str:
    if ":table:" not in ref:
        raise ProductCommandError("invalid_reference", "table_ref is invalid")
    analysis_ref = ref.split(":table:", 1)[0]
    if not _REF_RE.fullmatch(analysis_ref) or not analysis_ref.startswith("analysis:"):
        raise ProductCommandError("invalid_reference", "table_ref is invalid")
    return analysis_ref


def _analysis_ref_from_event(ref: str) -> str:
    if ":event:" not in ref:
        raise ProductCommandError("invalid_reference", "event_ref is invalid")
    analysis_ref = ref.split(":event:", 1)[0]
    if not _REF_RE.fullmatch(analysis_ref) or not analysis_ref.startswith("analysis:"):
        raise ProductCommandError("invalid_reference", "event_ref is invalid")
    return analysis_ref


def _processed_table_events(
    artifact: dict[str, Any],
    table_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table = next(
        (
            item for item in build_processed_event_table_catalog(artifact)
            if item["table_ref"] == table_ref
        ),
        None,
    )
    if table is None:
        raise ProductCommandError(
            "table_not_found", "ProcessedEventTable 不存在", kind="unavailable",
        )
    events = [
        event
        for bundle in artifact.get("event_bundles", [])
        for event in bundle.get("events", [])
        if event.get("event_kind") == table["event_kind"]
    ]
    events.sort(
        key=lambda item: (
            item.get("start_ms", 0), item.get("end_ms", 0), item.get("event_id", ""),
        )
    )
    if len(events) != table["row_count"]:
        raise ProductCommandError(
            "table_incomplete", "ProcessedEventTable row count is inconsistent", kind="unavailable",
        )
    return table, events


async def _load_processed_table_for_bridge(
    bridge: "_ToolBridge",
    table_ref: object,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(table_ref, str):
        raise ProductCommandError("invalid_reference", "table_ref is required")
    _require_reachable(bridge, table_ref)
    analysis_ref = _analysis_ref_from_table(table_ref)
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    table, events = _processed_table_events(artifact, table_ref)
    return artifact, table, events


_EVENT_VALUE_MISSING = object()


def _event_field_value(event: Mapping[str, Any], field: str) -> object:
    if field in {"event_id", "start_ms", "end_ms", "confidence", "limitations"}:
        return event.get(field, _EVENT_VALUE_MISSING)
    attributes = event.get("attributes")
    if isinstance(attributes, Mapping):
        return attributes.get(field, _EVENT_VALUE_MISSING)
    return _EVENT_VALUE_MISSING


def _table_field(table: Mapping[str, Any], field: object) -> dict[str, Any]:
    if not isinstance(field, str):
        raise ProductCommandError("invalid_parameters", "processed event field is required")
    for definition in table.get("field_catalog", []):
        if definition.get("field_key") == field:
            return definition
    raise ProductCommandError("invalid_parameters", "processed event field is not registered")


def _predicate(value: object, table: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductCommandError("invalid_parameters", "event predicate must be an object")
    operator = value.get("operator")
    if operator not in {"eq", "lt", "lte", "gt", "gte", "between", "available", "unavailable"}:
        raise ProductCommandError("invalid_parameters", "event predicate operator is invalid")
    expected = {"field", "operator"} if operator in {"available", "unavailable"} else {"field", "operator", "value"}
    if set(value) != expected:
        raise ProductCommandError("invalid_parameters", "event predicate fields are invalid")
    field = value.get("field")
    definition = _table_field(table, field)
    if operator in {"lt", "lte", "gt", "gte", "between"} and definition["value_type"] != "number":
        raise ProductCommandError("invalid_parameters", "ordered predicate requires a numeric field")
    operand = value.get("value")
    if operator == "between":
        if (
            not isinstance(operand, list) or len(operand) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in operand)
        ):
            raise ProductCommandError("invalid_parameters", "between requires two finite numbers")
        if float(operand[0]) > float(operand[1]):
            raise ProductCommandError("invalid_parameters", "between bounds are reversed")
    elif operator in {"lt", "lte", "gt", "gte"}:
        if isinstance(operand, bool) or not isinstance(operand, (int, float)) or not math.isfinite(float(operand)):
            raise ProductCommandError("invalid_parameters", "ordered predicate requires a finite number")
    elif operator == "eq":
        if not isinstance(operand, (str, int, float, bool)):
            raise ProductCommandError("invalid_parameters", "equality predicate value is invalid")
        if isinstance(operand, float) and not math.isfinite(operand):
            raise ProductCommandError("invalid_parameters", "equality predicate number must be finite")
    return dict(value)


def _predicates(value: object, table: Mapping[str, Any], *, max_items: int = 4) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", "event predicates must be a bounded list")
    return [_predicate(item, table) for item in value]


def _predicate_matches(event: Mapping[str, Any], predicate: Mapping[str, Any]) -> bool:
    current = _event_field_value(event, str(predicate["field"]))
    operator = predicate["operator"]
    if operator == "available":
        return current is not _EVENT_VALUE_MISSING and current is not None
    if operator == "unavailable":
        return current is _EVENT_VALUE_MISSING or current is None
    if current is _EVENT_VALUE_MISSING or current is None:
        return False
    operand = predicate.get("value")
    if operator == "eq":
        return current == operand
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
        return False
    number = float(current)
    if operator == "lt":
        return number < float(operand)
    if operator == "lte":
        return number <= float(operand)
    if operator == "gt":
        return number > float(operand)
    if operator == "gte":
        return number >= float(operand)
    return float(operand[0]) <= number <= float(operand[1])


def _matching_events(
    events: list[dict[str, Any]],
    predicates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event for event in events
        if all(_predicate_matches(event, predicate) for predicate in predicates)
    ]


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("nearest rank requires values")
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _event_distribution(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        float(value)
        for event in events
        for value in [_event_field_value(event, field)]
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not values:
        return {
            "count": len(events), "valid_count": 0, "excluded_count": len(events),
            "availability": "unavailable",
        }
    return {
        "count": len(events),
        "valid_count": len(values),
        "excluded_count": len(events) - len(values),
        "availability": "available",
        "min": min(values),
        "p10": _nearest_rank(values, 0.10),
        "p25": _nearest_rank(values, 0.25),
        "median": _nearest_rank(values, 0.50),
        "p75": _nearest_rank(values, 0.75),
        "p90": _nearest_rank(values, 0.90),
        "max": max(values),
        "mean": math.fsum(values) / len(values),
    }


def _table_metric_field(table: Mapping[str, Any], metric_key: str) -> dict[str, Any]:
    matches = [
        field for field in table.get("field_catalog", [])
        if field.get("metric_key") == metric_key
    ]
    if len(matches) != 1:
        raise ProductCommandError(
            "not_comparable",
            "requested metric does not map to one processed event field",
            kind="unavailable",
        )
    return matches[0]


def _processed_metric_record(
    table: Mapping[str, Any],
    events: list[dict[str, Any]],
    metric_key: str,
    *,
    evidence_ref: str,
) -> dict[str, Any]:
    definition = _table_metric_field(table, metric_key)
    field = definition["field_key"]
    values = [
        float(value)
        for event in events
        for value in [_event_field_value(event, field)]
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not values:
        raise ProductCommandError(
            "not_comparable", "processed event metric is unavailable", kind="unavailable",
        )
    value = values[0] if len(values) == 1 else _nearest_rank(values, 0.5)
    event_refs = [event["event_id"] for event in events]
    return {
        "schema_version": "metric_record.v1",
        "metric_key": metric_key,
        "metric_version": definition["metric_version"],
        "value": value,
        "unit": definition["unit"],
        "availability": "available",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": sorted({
                ref for event in events for ref in event.get("source_refs", [])
            }),
        },
        "population": {
            "sample_count": len(events),
            "valid_count": len(values),
            "excluded_count": len(events) - len(values),
        },
        "condition_refs": [],
        "event_refs": event_refs,
        "evidence_segment_refs": [evidence_ref] if ":segment:" in evidence_ref else [],
        "coverage": min(float(event.get("confidence") or 0.0) for event in events),
        "confidence": min(float(event.get("confidence") or 0.0) for event in events),
        "limitations": [
            *definition.get("limitations", []),
            *([] if len(values) == 1 else ["segment_value_is_median_of_processed_rows"]),
        ],
    }


def _facts_section_summaries(analysis_ref: str, facts: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for section in facts.get("sections", []):
        section_key = section.get("section_key")
        if not isinstance(section_key, str):
            continue
        summaries.append({
            "section_key": section_key,
            "section_ref": f"{analysis_ref}:facts:{section_key}",
            "completeness": section.get("completeness"),
            "present_field_count": len(section.get("present_field_keys", [])),
            "source_absent_field_count": len(section.get("source_absent_field_keys", [])),
            "omitted_known_field_count": len(section.get("omitted_known_fields", [])),
        })
    return summaries


def _new_cursor(bridge: _ToolBridge, *, command_name: str, state: dict[str, Any]) -> str:
    cursor = f"cursor:{secrets.token_urlsafe(24)}"
    bridge.cursors[cursor] = {"command_name": command_name, **state}
    return cursor


def _cursor_state(bridge: _ToolBridge, cursor: object, command_name: str) -> dict[str, Any]:
    if not isinstance(cursor, str):
        raise ProductCommandError("cursor_not_valid", "cursor is invalid")
    state = bridge.cursors.get(cursor)
    if state is None or state.get("command_name") != command_name:
        raise ProductCommandError("cursor_not_valid", "cursor is invalid")
    return dict(state)


def _audit_evidence_result(
    result: dict[str, Any],
    *,
    command_name: str,
    parameters: dict[str, Any],
    bridge: _ToolBridge,
    response_bytes: int,
    signal_points: int,
) -> dict[str, Any]:
    refs = sorted(ref for ref in _stable_reachable_refs(result) if not ref.startswith("cursor:"))
    requested_event_fields: list[str] = []
    for value in [parameters.get("field"), *(parameters.get("fields") or [])]:
        if isinstance(value, str) and value not in requested_event_fields:
            requested_event_fields.append(value)
    for predicate in [
        *(parameters.get("predicates") or []),
        parameters.get("left"),
        parameters.get("right"),
    ]:
        if isinstance(predicate, Mapping):
            field = predicate.get("field")
            if isinstance(field, str) and field not in requested_event_fields:
                requested_event_fields.append(field)
    audit_payload = {
        "schema_version": _EVIDENCE_AUDIT_SCHEMA,
        "command_name": command_name,
        "analysis_refs": sorted(
            ref for ref in refs
            if ref.startswith("analysis:")
            and not any(marker in ref for marker in (":table:", ":event:", ":segment:", ":facts:"))
        ),
        "table_refs": sorted(ref for ref in refs if ":table:" in ref),
        "event_refs": sorted(ref for ref in refs if ":event:" in ref),
        "segment_refs": sorted(ref for ref in refs if ":segment:" in ref),
        "requested_metric_keys": parameters.get("metric_keys", []),
        "requested_channel_keys": parameters.get("channel_keys", []),
        "requested_event_kinds": parameters.get("event_kinds", []),
        "requested_segment_kinds": parameters.get("segment_kinds", []),
        "requested_issue_refs": parameters.get("issue_refs", []),
        "requested_event_fields": requested_event_fields,
        "query_digest": _idempotency_digest(command_name, {
            key: value for key, value in parameters.items() if key != "cursor"
        }),
        "budget_used": {
            "response_bytes": response_bytes,
            "signal_points": signal_points,
        },
        "budget_remaining": {
            "response_bytes": _MAX_BRIDGE_BYTES - bridge.bytes_used - response_bytes,
            "signal_points": _MAX_SIGNAL_POINTS - bridge.signal_points_used - signal_points,
        },
        "status": result.get("status"),
    }
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": result["command_id"],
        "status": result["status"],
        "audit_ref": result["audit_ref"],
        "result_ref": result.get("result_ref"),
        "result": audit_payload,
    }


def _result_cursors(value: object) -> set[str]:
    cursors: set[str] = set()
    if isinstance(value, str) and value.startswith("cursor:"):
        cursors.add(value)
    elif isinstance(value, Mapping):
        for child in value.values():
            cursors.update(_result_cursors(child))
    elif isinstance(value, list):
        for child in value:
            cursors.update(_result_cursors(child))
    return cursors


def _discard_result_cursors(bridge: _ToolBridge, result: dict[str, Any]) -> None:
    for cursor in _result_cursors(result):
        bridge.cursors.pop(cursor, None)


async def _finish_evidence_result(
    bridge: _ToolBridge,
    result: dict[str, Any],
    *,
    command_name: str,
    parameters: dict[str, Any],
    signal_points: int = 0,
) -> dict[str, Any]:
    if result.get("status") != "succeeded":
        return result
    size = _canonical_wire_size(result)
    if size > _MAX_SINGLE_RESULT_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("response_too_large", "evidence response exceeds the per-response budget"))
    is_signal = command_name == "analysis.evidence.signal_window"
    async with _tool_bridge_lock:
        current = _tool_bridges.get(bridge.token_digest)
        if current is not bridge or bridge.expires_at <= time.time():
            _tool_bridges.pop(bridge.token_digest, None)
            bridge.cursors.clear()
            return _result(result["command_id"], "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    if bridge.bytes_used + size > _MAX_BRIDGE_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("budget_exhausted", "Coach evidence byte budget is exhausted"))
    if bridge.signal_points_used + signal_points > _MAX_SIGNAL_POINTS:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_point_budget_exhausted", "Coach signal point budget is exhausted"))
    if is_signal and bridge.signal_bytes_used + size > _MAX_SIGNAL_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted"))
    audit = _audit_evidence_result(
        result,
        command_name=command_name,
        parameters=parameters,
        bridge=bridge,
        response_bytes=size,
        signal_points=signal_points,
    )
    token = _audit_context.set({
        "thread_id": bridge.thread_id,
        "user_message_ref": bridge.user_message_ref,
        "command_name": command_name,
        "risk": "query",
        "authorization_source": "coach_inferred",
        "idempotency_key": None,
        "parameters_digest": audit["result"]["query_digest"],
        "safe_parameters_summary": _safe_parameter_summary(parameters),
    })
    try:
        try:
            await _journal().audit(bridge.owner_id, audit)
        except Exception:
            _discard_result_cursors(bridge, result)
            return _result(result["command_id"], "unavailable", warning_or_error=_error("audit_unavailable", "evidence audit is unavailable"))
        async with _tool_bridge_lock:
            current = _tool_bridges.get(bridge.token_digest)
            if current is not bridge or bridge.expires_at <= time.time():
                _tool_bridges.pop(bridge.token_digest, None)
                bridge.cursors.clear()
                return _result(result["command_id"], "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
            if bridge.bytes_used + size > _MAX_BRIDGE_BYTES:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("budget_exhausted", "Coach evidence byte budget is exhausted"))
            if bridge.signal_points_used + signal_points > _MAX_SIGNAL_POINTS:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_point_budget_exhausted", "Coach signal point budget is exhausted"))
            if is_signal and bridge.signal_bytes_used + size > _MAX_SIGNAL_BYTES:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted"))
            bridge.bytes_used += size
            bridge.signal_points_used += signal_points
            if is_signal:
                bridge.signal_bytes_used += size
            bridge.reachable_refs.update(_stable_reachable_refs(result))
        return result
    finally:
        _audit_context.reset(token)


async def _execute_evidence_bridge(bridge: _ToolBridge, envelope: Mapping[str, Any]) -> dict[str, Any]:
    command_id = _command_id(envelope.get("command_id"))
    command_name = envelope.get("command_name")
    if not isinstance(command_name, str) or command_name not in _EVIDENCE_QUERY_COMMANDS:
        return _result(command_id, "failed", warning_or_error=_error("unsupported_command", "command is not allowed"))
    try:
        parameters = _require_mapping(envelope.get("parameters", {}))
        if _contains_forbidden_model_data(parameters) or set(parameters) & _FORBIDDEN_EVIDENCE_PARAMETER_KEYS:
            raise ProductCommandError("untrusted_field", "paths, URLs, credentials and raw traces are not accepted")
        if command_name == "analysis.metrics.distribution":
            result, points = await _query_metric_distribution(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.list":
            result, points = await _query_evidence_list(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.signal_window":
            result, points = await _query_signal_window(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.compare":
            result, points = await _query_evidence_compare(bridge, command_id, parameters)
        elif command_name == "analysis.run_facts.get":
            result, points = await _query_run_facts(bridge, command_id, parameters)
        elif command_name == "analysis.outcomes.timeline":
            result, points = await _query_outcomes_timeline(bridge, command_id, parameters)
        elif command_name == "analysis.events.list":
            result, points = await _query_events(bridge, command_id, parameters)
        elif command_name == "analysis.events.get":
            result, points = await _query_event_get(bridge, command_id, parameters)
        elif command_name == "analysis.events.rank":
            result, points = await _query_event_rank(bridge, command_id, parameters)
        elif command_name == "analysis.events.filter":
            result, points = await _query_event_filter(bridge, command_id, parameters)
        elif command_name == "analysis.events.aggregate":
            result, points = await _query_event_aggregate(bridge, command_id, parameters)
        elif command_name == "analysis.events.co_occurrence":
            result, points = await _query_event_co_occurrence(bridge, command_id, parameters)
        else:
            result, points = await _query_event_sequence(bridge, command_id, parameters)
        return await _finish_evidence_result(
            bridge, result, command_name=command_name, parameters=parameters, signal_points=points,
        )
    except ProductCommandError as exc:
        status = "unavailable" if exc.kind == "unavailable" else "failed"
        return _result(command_id, status, result_ref=exc.result_ref, warning_or_error=_error(exc.code, exc.message))
    except (ValueError, TypeError, KeyError):
        return _result(command_id, "failed", warning_or_error=_error("invalid_parameters", "evidence query parameters are invalid"))
    except Exception:
        return _result(command_id, "unavailable", warning_or_error=_error("evidence_unavailable", "evidence query could not be completed"))


async def _query_metric_distribution(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "metric_keys"})
    analysis_ref = parameters.get("analysis_ref")
    if not isinstance(analysis_ref, str):
        raise ProductCommandError("invalid_reference", "analysis_ref is required")
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    keys = _string_list(parameters.get("metric_keys"), field_name="metric_keys", max_items=_METRIC_KEYS_MAX, allow_empty=False)
    available_keys = {metric.get("metric_key") for metric in artifact.get("metric_records", [])}
    if not set(keys) <= available_keys:
        raise ProductCommandError("invalid_parameters", "metric is not available in this analysis")
    metrics = [_safe_metric(metric) for metric in artifact.get("metric_records", []) if metric.get("metric_key") in keys]
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:metrics", result={"analysis_ref": analysis_ref, "metrics": metrics}), 0


async def _query_evidence_list(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "segment_kinds", "issue_refs", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.evidence.list")
        analysis_ref = state["analysis_ref"]
        offset = int(state["offset"])
        limit = int(state["limit"])
        segment_kinds = list(state["segment_kinds"])
        issue_refs = list(state["issue_refs"])
    else:
        analysis_ref = parameters.get("analysis_ref")
        if not isinstance(analysis_ref, str):
            raise ProductCommandError("invalid_reference", "analysis_ref is required")
        offset = 0
        limit = parameters.get("limit", _LIST_MAX)
        segment_kinds = _string_list(
            parameters.get("segment_kinds"),
            field_name="segment_kinds",
            max_items=8,
        )
        if not all(EvidenceKeyRegistry().allows_segment(kind) for kind in segment_kinds):
            raise ProductCommandError("invalid_parameters", "segment kind is not registered")
        issue_refs = (
            _stable_ref_list(parameters.get("issue_refs"), field_name="issue_refs", max_items=8)
            if "issue_refs" in parameters
            else []
        )
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    segments = [
        segment
        for segment in artifact.get("evidence_segments", [])
        if (not segment_kinds or segment.get("segment_kind") in segment_kinds)
        and (not issue_refs or set(segment.get("issue_refs", [])) & set(issue_refs))
    ]
    selected = segments[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(segments):
        next_cursor = _new_cursor(bridge, command_name="analysis.evidence.list", state={
            "analysis_ref": analysis_ref,
            "offset": offset + len(selected),
            "limit": limit,
            "segment_kinds": list(segment_kinds),
            "issue_refs": list(issue_refs),
        })
    result = _evidence_result(command_id, result_ref=f"{analysis_ref}:evidence:list:{offset}", result={
        "analysis_ref": analysis_ref,
        "segments": [_safe_segment(segment) for segment in selected],
        "next_cursor": next_cursor,
    })
    return result, 0


def _downsample_points(points: list[list[float]], limit: int) -> list[list[float]]:
    if len(points) <= limit:
        return [list(point) for point in points]
    if limit <= 0:
        return []
    last_index = len(points) - 1
    global_min = min(range(len(points)), key=lambda index: (points[index][1], index))
    global_max = max(range(len(points)), key=lambda index: (points[index][1], -index))
    mandatory = {0, last_index, global_min, global_max}
    if limit < len(mandatory):
        raise ValueError("point limit cannot preserve endpoints and extrema")
    if limit <= 2:
        return [list(points[0]), list(points[-1])][:limit]
    if limit == 3:
        midpoint = (float(points[0][1]) + float(points[-1][1])) / 2.0
        candidates = [index for index in {global_min, global_max} if index not in {0, last_index}]
        middle = max(
            candidates or [last_index // 2],
            key=lambda index: (abs(float(points[index][1]) - midpoint), -index),
        )
        return [list(points[index]) for index in sorted({0, middle, last_index})]
    selected = set(mandatory)
    interior_count = max(1, (limit - 2) // 2)
    for bucket in range(interior_count):
        start = 1 + (bucket * (len(points) - 2)) // interior_count
        end = 1 + ((bucket + 1) * (len(points) - 2)) // interior_count
        if start >= end:
            continue
        bucket_indices = range(start, end)
        selected.add(min(bucket_indices, key=lambda index: (points[index][1], index)))
        selected.add(max(bucket_indices, key=lambda index: (points[index][1], -index)))

    if len(selected) > limit:
        optional = sorted(selected - mandatory)
        remaining = max(0, limit - len(mandatory))
        if remaining < len(optional):
            optional = [
                optional[round(index * (len(optional) - 1) / max(1, remaining - 1))]
                for index in range(remaining)
            ] if remaining else []
        selected = mandatory | set(optional)
    if len(selected) < limit:
        available = [index for index in range(1, last_index) if index not in selected]
        remaining = min(limit - len(selected), len(available))
        if remaining:
            selected.update(
                available[round(index * (len(available) - 1) / max(1, remaining - 1))]
                for index in range(remaining)
            )
    return [list(points[index]) for index in sorted(selected)[:limit]]


async def _query_signal_window(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"segment_ref", "channel_keys"})
    segment_ref = parameters.get("segment_ref")
    if not isinstance(segment_ref, str):
        raise ProductCommandError("invalid_reference", "segment_ref is required")
    _require_reachable(bridge, segment_ref)
    analysis_ref = _analysis_ref_from_segment(segment_ref)
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    segment = _segment(artifact, segment_ref)
    focus_start = segment.get("focus_start_ms")
    focus_end = segment.get("focus_end_ms")
    if (
        isinstance(focus_start, bool)
        or not isinstance(focus_start, int)
        or isinstance(focus_end, bool)
        or not isinstance(focus_end, int)
        or focus_start >= focus_end
        or focus_end - focus_start > 12_000
    ):
        raise ProductCommandError("signal_window_unavailable", "segment focus window exceeds the 12 second limit", kind="unavailable")
    channel_keys = _string_list(parameters.get("channel_keys"), field_name="channel_keys", max_items=_SIGNAL_CHANNEL_MAX, allow_empty=False)
    allowed_channels = set(segment.get("available_channels", []))
    if not set(channel_keys) <= allowed_channels:
        raise ProductCommandError("invalid_parameters", "channel is not available in this segment")
    samples_by_channel = {
        sample.get("channel_key"): sample
        for sample in artifact.get("sample_sets", [])
        if sample.get("channel_key") in channel_keys
    }
    channel_metadata = {
        channel.get("channel_key"): channel
        for bundle in artifact.get("signal_bundles", [])
        for channel in bundle.get("channels", [])
        if channel.get("channel_key") in channel_keys
    }
    source_points: list[tuple[str, dict[str, Any], dict[str, Any], list[list[float]]]] = []
    for channel_key in channel_keys:
        sample = samples_by_channel.get(channel_key)
        metadata = channel_metadata.get(channel_key)
        if sample is None or metadata is None:
            raise ProductCommandError("evidence_unavailable", "signal channel samples are unavailable", kind="unavailable")
        points = [
            point for point in sample.get("points", [])
            if focus_start <= point[0] < focus_end
        ]
        if not points:
            raise ProductCommandError("evidence_unavailable", "signal channel has no samples in the segment focus window", kind="unavailable")
        source_points.append((channel_key, sample, metadata, points))

    remaining_points = _MAX_SIGNAL_POINTS - bridge.signal_points_used
    available_bytes = min(
        _MAX_SINGLE_RESULT_BYTES,
        _MAX_BRIDGE_BYTES - bridge.bytes_used,
        _MAX_SIGNAL_BYTES - bridge.signal_bytes_used,
    )
    minimum_per_channel = max(
        len({
            0,
            len(points) - 1,
            min(range(len(points)), key=lambda index: (points[index][1], index)),
            max(range(len(points)), key=lambda index: (points[index][1], -index)),
        })
        for _, _, _, points in source_points
    )
    if remaining_points < minimum_per_channel * len(source_points):
        raise ProductCommandError("signal_point_budget_exhausted", "Coach signal point budget is exhausted", kind="unavailable")
    if available_bytes <= 0:
        raise ProductCommandError("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted", kind="unavailable")
    max_per_channel = min(
        _SIGNAL_POINTS_PER_CHANNEL,
        max(1, remaining_points // len(source_points)),
    )

    def build_result(point_limit: int) -> tuple[dict[str, Any], int]:
        channels: list[dict[str, Any]] = []
        point_count = 0
        truncated = False
        for channel_key, sample, metadata, points in source_points:
            sampled = _downsample_points(points, point_limit)
            point_count += len(sampled)
            truncated = truncated or len(sampled) < len(points)
            channels.append({
                "channel_key": channel_key,
                "unit": sample.get("unit"),
                "points": sampled,
                "source_coverage": metadata.get("coverage"),
                "confidence": metadata.get("confidence_summary"),
            })
        body = {
            "schema_version": "signal_window.v1",
            "analysis_ref": analysis_ref,
            "segment_ref": segment_ref,
            "focus_range_ms": [focus_start, focus_end],
            "channels": channels,
            "downsample_version": "deterministic_extrema.v1",
            "point_count": point_count,
            "truncated": truncated,
            "budget_used": {"response_bytes": 0, "signal_points": point_count},
            "budget_remaining": {
                "response_bytes": 0,
                "signal_response_bytes": 0,
                "signal_points": remaining_points - point_count,
            },
            "limitations": ["deterministic_extrema_downsampled"] if truncated else [],
        }
        result = _evidence_result(
            command_id,
            result_ref=f"{segment_ref}:signal-window",
            result=body,
        )
        for _ in range(4):
            response_bytes = _canonical_wire_size(result)
            body["budget_used"]["response_bytes"] = response_bytes
            body["budget_remaining"]["response_bytes"] = max(
                0, _MAX_BRIDGE_BYTES - bridge.bytes_used - response_bytes,
            )
            body["budget_remaining"]["signal_response_bytes"] = max(
                0, _MAX_SIGNAL_BYTES - bridge.signal_bytes_used - response_bytes,
            )
        return result, point_count

    best: tuple[dict[str, Any], int] | None = None
    low, high = minimum_per_channel, max_per_channel
    while low <= high:
        middle = (low + high) // 2
        candidate = build_result(middle)
        if _canonical_wire_size(candidate[0]) <= available_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ProductCommandError("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted", kind="unavailable")
    return best


async def _query_evidence_compare(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"evidence_refs", "metric_keys"})
    refs = _stable_ref_list(parameters.get("evidence_refs"), field_name="evidence_refs", max_items=4)
    if not 2 <= len(refs) <= 4:
        raise ProductCommandError("invalid_parameters", "evidence_refs must contain 2 to 4 refs")
    keys = _string_list(parameters.get("metric_keys"), field_name="metric_keys", max_items=_METRIC_KEYS_MAX, allow_empty=False)
    rows: list[dict[str, Any]] = []
    comparison_scope: str | None = None
    comparison_contracts: list[dict[str, Any]] = []
    comparison_limitations: list[str] = []
    for ref in refs:
        _require_reachable(bridge, ref)
        segment = None
        event = None
        table = None
        if ":segment:" in ref:
            analysis_ref = _analysis_ref_from_segment(ref)
            scope = "segment"
        elif ":event:" in ref:
            analysis_ref = _analysis_ref_from_event(ref)
            scope = "event"
        elif _REF_RE.fullmatch(ref) and ref.startswith("analysis:"):
            analysis_ref = ref
            scope = "analysis"
        else:
            raise ProductCommandError("invalid_reference", "comparison refs must be analysis, segment or processed event refs")
        if comparison_scope is None:
            comparison_scope = scope
        elif comparison_scope != scope:
            raise ProductCommandError("not_comparable", "analysis and segment evidence cannot be mixed", kind="unavailable")
        artifact, artifact_ref, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
        if scope == "segment":
            segment = _segment(artifact, ref)
        facts = artifact.get("canonical_run_facts") or {}
        available_keys = {metric.get("metric_key") for metric in artifact.get("metric_records", [])}
        if not set(keys) <= available_keys:
            raise ProductCommandError("invalid_parameters", "metric is not available for comparison")
        if scope == "analysis":
            predicate_version = "analysis_metric_comparability.v1"
            metrics = [
                _safe_metric(metric)
                for metric in artifact.get("metric_records", [])
                if metric.get("metric_key") in keys
            ]
        else:
            processed_tables = build_processed_event_table_catalog(artifact)
            if scope == "segment" and not processed_tables:
                predicate_version = "legacy_segment_metric_comparability.v1"
                metrics = [
                    _safe_metric(metric)
                    for metric in artifact.get("metric_records", [])
                    if metric.get("metric_key") in keys
                    and ref in metric.get("evidence_segment_refs", [])
                ]
                comparison_limitations.append(
                    "legacy_segment_compare_uses_linked_metric_record"
                )
            elif scope == "event":
                predicate_version = "processed_event_metric_comparability.v1"
                matches = []
                for candidate in processed_tables:
                    _, candidate_events = _processed_table_events(
                        artifact, candidate["table_ref"],
                    )
                    candidate_event = next(
                        (item for item in candidate_events if item.get("event_id") == ref),
                        None,
                    )
                    if candidate_event is not None:
                        matches.append((candidate, [candidate_event]))
                if len(matches) != 1:
                    raise ProductCommandError(
                        "not_comparable",
                        "event ref is not a unique processed event",
                        kind="unavailable",
                    )
                table, selected_events = matches[0]
                event = selected_events[0]
            else:
                predicate_version = "processed_event_metric_comparability.v1"
                matches = []
                for candidate in processed_tables:
                    if not all(
                        any(field.get("metric_key") == key for field in candidate["field_catalog"])
                        for key in keys
                    ):
                        continue
                    _, candidate_events = _processed_table_events(
                        artifact, candidate["table_ref"],
                    )
                    selected_events = [
                        item for item in candidate_events
                        if segment["start_ms"] <= item.get("start_ms", -1) < segment["end_ms"]
                    ]
                    if selected_events:
                        matches.append((candidate, selected_events))
                if len(matches) != 1:
                    raise ProductCommandError(
                        "not_comparable",
                        "segment does not resolve to one processed event table",
                        kind="unavailable",
                    )
                table, selected_events = matches[0]
            if not (scope == "segment" and not processed_tables):
                metrics = [
                    _safe_metric(
                        _processed_metric_record(
                            table, selected_events, key, evidence_ref=ref,
                        )
                    )
                    for key in keys
                ]
        if {metric.get("metric_key") for metric in metrics} != set(keys):
            raise ProductCommandError("not_comparable", "requested metrics are not linked to every comparison ref", kind="unavailable")
        raw_metrics = {
            metric.get("metric_key"): metric for metric in metrics
        }
        comparison_contracts.append({
            "predicate_version": predicate_version,
            "artifact_contract_version": artifact_ref.get("contract_version"),
            "scenario_profile_ref": facts.get("scenario_profile_ref"),
            "timebase_version": (artifact.get("canonical_time_window") or {}).get("timebase_version"),
            "analyzer_ref": (
                segment.get("analyzer_ref")
                if segment is not None
                else table.get("analyzer_ref") if table is not None else None
            ),
            "metrics": {
                key: {
                    "metric_version": raw_metrics[key].get("metric_version"),
                    "unit": raw_metrics[key].get("unit"),
                    "classification": raw_metrics[key].get("classification"),
                    "provenance_kind": (raw_metrics[key].get("provenance") or {}).get("kind"),
                    "condition_refs": sorted(raw_metrics[key].get("condition_refs", [])),
                }
                for key in keys
            },
        })
        rows.append({
            "evidence_ref": ref,
            "scope": scope,
            "segment": _safe_segment(segment) if segment is not None else None,
            "event": _safe_event(event) if event is not None else None,
            "metrics": metrics,
        })
    if any(contract != comparison_contracts[0] for contract in comparison_contracts[1:]):
        raise ProductCommandError("not_comparable", "versioned comparison contracts do not match", kind="unavailable")
    baseline = {
        metric["metric_key"]: metric.get("value")
        for metric in rows[0]["metrics"]
    }
    for row in rows:
        row["deltas_from_first"] = {
            metric["metric_key"]: (
                float(metric["value"]) - float(baseline[metric["metric_key"]])
                if isinstance(metric.get("value"), (int, float))
                and not isinstance(metric.get("value"), bool)
                and isinstance(baseline.get(metric["metric_key"]), (int, float))
                and not isinstance(baseline.get(metric["metric_key"]), bool)
                else None
            )
            for metric in row["metrics"]
        }
    return _evidence_result(
        command_id,
        result_ref="evidence:compare:" + hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()[:24],
        result={
            "scope": comparison_scope,
            "comparability": "comparable",
            "comparability_predicate_version": comparison_contracts[0]["predicate_version"],
            "metric_keys": keys,
            "comparisons": rows,
            "limitations": list(dict.fromkeys(comparison_limitations)),
        },
    ), 0


async def _query_run_facts(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "sections"})
    analysis_ref = parameters.get("analysis_ref")
    if not isinstance(analysis_ref, str):
        raise ProductCommandError("invalid_reference", "analysis_ref is required")
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    facts = artifact.get("canonical_run_facts")
    if not isinstance(facts, dict):
        raise ProductCommandError("facts_unavailable", "CanonicalRunFacts 不可用", kind="unavailable")
    summaries = _facts_section_summaries(analysis_ref, facts)
    requested = parameters.get("sections", "all")
    if requested != "all":
        requested_keys = _string_list(requested, field_name="sections", max_items=_FACT_SECTION_MAX, allow_empty=False)
        selected = [section for section in facts.get("sections", []) if section.get("section_key") in requested_keys]
        if len(selected) != len(requested_keys):
            raise ProductCommandError("invalid_parameters", "unknown facts section")
        facts = {**facts, "sections": selected}
    if _canonical_wire_size(facts) <= 8 * 1024 and _canonical_wire_size(facts) <= _MAX_SINGLE_RESULT_BYTES:
        run_facts = {"mode": "inline", "field_registry_version": facts.get("field_registry_version"), "facts": facts, "section_summaries": [], "limitations": facts.get("limitations", [])}
    else:
        run_facts = {"mode": "section_refs", "field_registry_version": facts.get("field_registry_version"), "section_summaries": summaries, "limitations": ["facts_over_inline_budget"]}
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:facts", result={"analysis_ref": analysis_ref, "run_facts": run_facts}), 0


def _overview_timeline(
    records: list[dict[str, Any]],
    *,
    analysis_ref: str,
    scope: str,
    segment_ref: str | None,
    series: list[str],
    segment_bounds: tuple[int, int] | None,
) -> dict[str, Any]:
    by_series: dict[str, list[dict[str, Any]]] = {key: [] for key in series}
    for record in records:
        time_ms = record.get("canonical_time_ms")
        if segment_bounds is not None and not (segment_bounds[0] <= time_ms < segment_bounds[1]):
            continue
        for value in record.get("values", []):
            metric_key = value.get("metric_key")
            if metric_key not in by_series:
                continue
            numeric = value.get("value")
            if isinstance(numeric, bool) or not isinstance(numeric, (int, float)):
                raise ProductCommandError("overview_unavailable", "overview only supports numeric outcome series", kind="unavailable")
            by_series[metric_key].append({
                "time_ms": time_ms,
                "value": float(numeric),
                "semantics": value.get("value_semantics"),
                "unit": value.get("unit"),
                "source_refs": list(record.get("source_refs", [])),
            })

    overview_series: list[dict[str, Any]] = []
    for metric_key in series:
        values = sorted(by_series[metric_key], key=lambda item: item["time_ms"])
        if not values:
            raise ProductCommandError("overview_unavailable", "requested outcome series has no records", kind="unavailable")
        bucket_count = min(120, len(values))
        points: list[list[float]] = []
        source_refs: set[str] = set()
        for bucket in range(bucket_count):
            start = (bucket * len(values)) // bucket_count
            end = ((bucket + 1) * len(values)) // bucket_count
            bucket_values = values[start:end]
            source_refs.update(
                ref
                for item in bucket_values
                for ref in item["source_refs"]
                if isinstance(ref, str)
            )
            semantics = bucket_values[0]["semantics"]
            if any(item["semantics"] != semantics for item in bucket_values):
                raise ProductCommandError("overview_unavailable", "outcome value semantics are inconsistent", kind="unavailable")
            if semantics in {"count_increment", "delta"}:
                bucket_value = sum(item["value"] for item in bucket_values)
            elif semantics == "instantaneous":
                bucket_value = bucket_values[-1]["value"]
            else:
                bucket_value = sum(item["value"] for item in bucket_values) / len(bucket_values)
            points.append([bucket_values[-1]["time_ms"], bucket_value])
        overview_series.append({
            "metric_key": metric_key,
            "unit": values[0]["unit"],
            "points": points,
            "source_refs": sorted(source_refs),
        })
    timeline = {
        "schema_version": "normalized_outcome_timeline.v1",
        "analysis_ref": analysis_ref,
        "scope": scope,
        "segment_ref": segment_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "mode": "overview",
        "resolution": "deterministic_binned",
        "selected_series": list(series),
        "overview_series": overview_series,
        "records": None,
        "event_refs": [],
        "completeness": "downsampled",
        "next_cursor": None,
        "limitations": ["deterministic_binned_overview"],
    }
    return validate_normalized_outcome_timeline_v1(timeline)


async def _query_outcomes_timeline(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "scope", "segment_ref", "mode", "series", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.outcomes.timeline")
        analysis_ref = state["analysis_ref"]
        scope = state["scope"]
        segment_ref = state["segment_ref"]
        series = state["series"]
        offset = state["offset"]
        mode = "exact_page"
    else:
        analysis_ref = parameters.get("analysis_ref")
        scope = parameters.get("scope")
        segment_ref = parameters.get("segment_ref")
        mode = parameters.get("mode")
        series = _string_list(parameters.get("series"), field_name="series", max_items=_OUTCOME_SERIES_MAX, allow_empty=False)
        offset = 0
        if not isinstance(analysis_ref, str) or scope not in {"whole_run", "evidence_segment"} or mode not in {"overview", "exact_page"}:
            raise ProductCommandError("invalid_parameters", "timeline requires a bounded scope and overview/exact_page mode")
        if scope == "evidence_segment":
            if not isinstance(segment_ref, str):
                raise ProductCommandError("invalid_reference", "segment_ref is required")
            _require_reachable(bridge, segment_ref)
        elif segment_ref is not None:
            raise ProductCommandError("invalid_parameters", "whole_run cannot include segment_ref")
    artifact, safe_ref, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    segment_bounds = None
    if scope == "evidence_segment":
        segment = _segment(artifact, segment_ref)
        segment_bounds = (segment["start_ms"], segment["end_ms"])
    if mode == "overview":
        timeline = _overview_timeline(
            artifact.get("normalized_outcome_records", []),
            analysis_ref=analysis_ref,
            scope=scope,
            segment_ref=segment_ref,
            series=series,
            segment_bounds=segment_bounds,
        )
        return _evidence_result(
            command_id,
            result_ref=f"{analysis_ref}:timeline:overview",
            result={"analysis_ref": analysis_ref, "timeline": timeline, "next_cursor": None},
        ), 0

    descriptor = build_page_descriptor_v1(
        owner_id=bridge.owner_id,
        analysis_ref=analysis_ref,
        evidence_revision=safe_ref["evidence_revision"],
        scope=scope,
        segment_ref=segment_ref,
        selected_series=series,
        offset=offset,
    )
    page = page_normalized_outcomes(
        artifact.get("normalized_outcome_records", []),
        analysis_ref=analysis_ref,
        canonical_time_window_ref=f"{analysis_ref}:canonical-window",
        descriptor=descriptor,
        byte_limit=min(
            20 * 1024,
            max(1, _MAX_BRIDGE_BYTES - bridge.bytes_used - 2 * 1024),
        ),
        segment_bounds=segment_bounds,
    )
    next_cursor = None
    if page.get("next_page_descriptor") is not None:
        next_cursor = _new_cursor(bridge, command_name="analysis.outcomes.timeline", state={
            "analysis_ref": analysis_ref, "scope": scope, "segment_ref": segment_ref,
            "series": list(series), "offset": page["next_page_descriptor"]["offset"],
        })
    timeline = page["timeline"]
    timeline["next_cursor"] = next_cursor
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:timeline:{offset}", result={"analysis_ref": analysis_ref, "timeline": timeline, "next_cursor": next_cursor}), 0


async def _query_events(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "scope", "segment_ref", "event_kinds", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.events.list")
        analysis_ref = state["analysis_ref"]
        scope = state["scope"]
        segment_ref = state["segment_ref"]
        event_kinds = state["event_kinds"]
        offset = state["offset"]
        limit = state["limit"]
    else:
        analysis_ref = parameters.get("analysis_ref")
        scope = parameters.get("scope")
        segment_ref = parameters.get("segment_ref")
        event_kinds = _string_list(parameters.get("event_kinds"), field_name="event_kinds", max_items=16, allow_empty=False)
        if not all(EvidenceKeyRegistry().allows_event(kind) for kind in event_kinds):
            raise ProductCommandError("invalid_parameters", "event kind is not registered")
        offset = 0
        if not isinstance(analysis_ref, str) or scope not in {"whole_run", "evidence_segment"}:
            raise ProductCommandError("invalid_parameters", "events requires a bounded scope")
        if scope == "evidence_segment":
            if not isinstance(segment_ref, str):
                raise ProductCommandError("invalid_reference", "segment_ref is required")
            _require_reachable(bridge, segment_ref)
        elif segment_ref is not None:
            raise ProductCommandError("invalid_parameters", "whole_run cannot include segment_ref")
        limit = parameters.get("limit", _LIST_MAX)
    artifact, _, _ = await _load_evidence_for_bridge(bridge, analysis_ref)
    bounds = None
    if scope == "evidence_segment":
        segment = _segment(artifact, segment_ref)
        bounds = (segment["start_ms"], segment["end_ms"])
    events: list[dict[str, Any]] = []
    stats_kill_fields = ("kill_index", "shots", "hits", "overshots")
    materialized_stats_kills: set[tuple[Any, ...]] = set()

    def stats_kill_key(
        time_ms: object,
        source_refs: object,
        values: object,
    ) -> tuple[Any, ...] | None:
        if not isinstance(source_refs, list) or not isinstance(values, dict):
            return None
        if not all(field in values for field in stats_kill_fields):
            return None
        return (
            time_ms,
            tuple(sorted(source_refs)),
            *(values[field] for field in stats_kill_fields),
        )

    for bundle in artifact.get("event_bundles", []):
        for event in bundle.get("events", []):
            if event.get("event_kind") not in event_kinds:
                continue
            if bounds is not None and not (bounds[0] <= event.get("start_ms", -1) < bounds[1]):
                continue
            events.append(_safe_event(event))
            if event.get("event_kind") == "kill":
                key = stats_kill_key(
                    event.get("start_ms"),
                    event.get("source_refs"),
                    event.get("attributes"),
                )
                if key is not None:
                    materialized_stats_kills.add(key)
    normalized_kind_by_metric = {
        "performance.shotsFired": "shot",
        "performance.shotsHit": "hit",
        "performance.shotsMissed": "miss",
        "performance.kills": "kill",
    }
    for record in artifact.get("normalized_outcome_records", []):
        start_ms = record.get("canonical_time_ms")
        if bounds is not None and not (bounds[0] <= start_ms < bounds[1]):
            continue
        record_kinds = {
            "kill"
            if value.get("metric_key", "").startswith("stats.kill.")
            else normalized_kind_by_metric.get(value.get("metric_key"))
            for value in record.get("values", [])
        }
        for event_kind in sorted(kind for kind in record_kinds if kind in event_kinds):
            if event_kind == "kill":
                stats_values = {
                    value.get("metric_key", "").removeprefix("stats.kill."): value.get("value")
                    for value in record.get("values", [])
                    if value.get("metric_key", "").startswith("stats.kill.")
                }
                if stats_kill_key(
                    start_ms, record.get("source_refs"), stats_values,
                ) in materialized_stats_kills:
                    continue
            events.append({
                "event_id": (
                    f"{analysis_ref}:normalized:{event_kind}:"
                    f"{record.get('source_priority')}:{record.get('source_event_index')}"
                ),
                "event_kind": event_kind,
                "start_ms": start_ms,
                "end_ms": start_ms,
                "source_time": record.get("source_time"),
                "source_priority": record.get("source_priority"),
                "source_event_index": record.get("source_event_index"),
                "values": list(record.get("values", [])),
                "source_refs": list(record.get("source_refs", [])),
                "confidence": None,
                "association": {
                    "status": "unavailable",
                    "limitations": ["target_association_not_observed"],
                },
                "limitations": ["timing_confidence_not_quantified"],
            })
    events.sort(key=lambda item: (item.get("start_ms", 0), item.get("end_ms", 0), item.get("event_id", "")))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    selected = events[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(events):
        next_cursor = _new_cursor(bridge, command_name="analysis.events.list", state={
            "analysis_ref": analysis_ref, "scope": scope, "segment_ref": segment_ref,
            "event_kinds": list(event_kinds), "offset": offset + len(selected), "limit": limit,
        })
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:events:{offset}", result={"analysis_ref": analysis_ref, "scope": scope, "records": selected, "event_refs": [event["event_id"] for event in selected], "next_cursor": next_cursor}), 0


async def _query_event_get(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "event_ref"})
    table_ref = parameters.get("table_ref")
    event_ref = parameters.get("event_ref")
    if not isinstance(event_ref, str):
        raise ProductCommandError("invalid_reference", "event_ref is required")
    _require_reachable(bridge, event_ref)
    _, table, events = await _load_processed_table_for_bridge(bridge, table_ref)
    event = next((item for item in events if item.get("event_id") == event_ref), None)
    if event is None:
        raise ProductCommandError(
            "event_not_in_table",
            "event_ref is not a member of the requested ProcessedEventTable",
            kind="unavailable",
        )
    return _evidence_result(
        command_id,
        result_ref=f"{event_ref}:detail",
        result={"table": table, "event": _safe_event(event)},
    ), 0


async def _query_event_rank(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "field", "direction", "predicates", "limit"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    field = parameters.get("field")
    definition = _table_field(table, field)
    if definition["value_type"] != "number":
        raise ProductCommandError("invalid_parameters", "rank field must be numeric")
    direction = parameters.get("direction")
    if direction not in {"asc", "desc"}:
        raise ProductCommandError("invalid_parameters", "rank direction is invalid")
    predicates = _predicates(parameters.get("predicates"), table)
    limit = parameters.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    filtered = _matching_events(events, predicates)
    ranked = [
        event for event in filtered
        if isinstance(_event_field_value(event, str(field)), (int, float))
        and not isinstance(_event_field_value(event, str(field)), bool)
        and math.isfinite(float(_event_field_value(event, str(field))))
    ]
    ranked.sort(
        key=lambda event: (
            (
                -float(_event_field_value(event, str(field)))
                if direction == "desc"
                else float(_event_field_value(event, str(field)))
            ),
            event.get("start_ms", 0),
            event.get("event_id", ""),
        ),
    )
    selected = ranked[:limit]
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:rank:{field}:{direction}",
        result={
            "table_ref": table["table_ref"],
            "field": field,
            "direction": direction,
            "evaluated_count": len(events),
            "predicate_match_count": len(filtered),
            "included_count": len(ranked),
            "excluded_count": len(events) - len(ranked),
            "rows": [_safe_event(event) for event in selected],
            "event_refs": [event["event_id"] for event in selected],
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


async def _query_event_filter(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "predicates", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.events.filter")
        table_ref = state["table_ref"]
        predicate_values = state["predicates"]
        limit = state["limit"]
        offset = state["offset"]
    else:
        table_ref = parameters.get("table_ref")
        predicate_values = parameters.get("predicates")
        limit = parameters.get("limit", _LIST_MAX)
        offset = 0
    _, table, events = await _load_processed_table_for_bridge(bridge, table_ref)
    predicates = _predicates(predicate_values, table)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    matched = _matching_events(events, predicates)
    selected = matched[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(matched):
        next_cursor = _new_cursor(
            bridge,
            command_name="analysis.events.filter",
            state={
                "table_ref": table["table_ref"],
                "predicates": predicates,
                "limit": limit,
                "offset": offset + len(selected),
            },
        )
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:filter:{offset}",
        result={
            "table_ref": table["table_ref"],
            "evaluated_count": len(events),
            "matched_count": len(matched),
            "excluded_count": len(events) - len(matched),
            "rows": [_safe_event(event) for event in selected],
            "event_refs": [event["event_id"] for event in selected],
            "next_cursor": next_cursor,
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


def _run_phase_groups(events: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = {"early": [], "middle": [], "late": []}
    total = len(events)
    for index, event in enumerate(events):
        phase_index = min(2, (index * 3) // max(1, total))
        groups[("early", "middle", "late")[phase_index]].append(event)
    return [(phase, rows) for phase, rows in groups.items() if rows]


def _aggregate_groups(
    events: list[dict[str, Any]],
    fields: list[str],
    group_by: str | None,
    table: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if group_by is None:
        grouped: list[tuple[object, list[dict[str, Any]]]] = [("all", events)]
    elif group_by == "run_phase":
        grouped = list(_run_phase_groups(events))
    else:
        group_definition = _table_field(table, group_by)
        if (
            group_definition["role"] not in {"condition", "quality", "outcome"}
            or group_definition["value_type"] not in {"string", "boolean"}
        ):
            raise ProductCommandError(
                "invalid_parameters",
                "group_by must be run_phase or a registered categorical condition",
            )
        values: dict[str, tuple[object, list[dict[str, Any]]]] = {}
        for event in events:
            value = _event_field_value(event, group_by)
            if value is _EVENT_VALUE_MISSING or value is None:
                value = "unavailable"
            key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            values.setdefault(key, (value, []))[1].append(event)
        grouped = [values[key] for key in sorted(values)]
    output: list[dict[str, Any]] = []
    for label, rows in grouped:
        item: dict[str, Any] = {
            "count": len(rows),
            "fields": {field: _event_distribution(rows, field) for field in fields},
        }
        if group_by == "run_phase":
            item["phase"] = label
        else:
            item["group_value"] = label
        output.append(item)
    return output


async def _query_event_aggregate(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "fields", "group_by"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    fields = _string_list(
        parameters.get("fields"), field_name="fields", max_items=8, allow_empty=False,
    )
    for field in fields:
        if _table_field(table, field)["value_type"] != "number":
            raise ProductCommandError("invalid_parameters", "aggregate fields must be numeric")
    group_by = parameters.get("group_by")
    if group_by is not None and not isinstance(group_by, str):
        raise ProductCommandError("invalid_parameters", "group_by is invalid")
    groups = _aggregate_groups(events, fields, group_by, table)
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:aggregate",
        result={
            "table_ref": table["table_ref"],
            "evaluated_count": len(events),
            "group_by": group_by,
            "groups": groups,
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


async def _query_event_co_occurrence(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "left", "right", "relation"})
    if parameters.get("relation") != "same_event":
        raise ProductCommandError("invalid_parameters", "only same_event relation is supported")
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    left = _predicate(parameters.get("left"), table)
    right = _predicate(parameters.get("right"), table)
    buckets = {"both": [], "left_only": [], "right_only": [], "neither": []}
    for event in events:
        left_match = _predicate_matches(event, left)
        right_match = _predicate_matches(event, right)
        key = (
            "both" if left_match and right_match
            else "left_only" if left_match
            else "right_only" if right_match
            else "neither"
        )
        buckets[key].append(event)
    left_total = len(buckets["both"]) + len(buckets["left_only"])
    right_total = len(buckets["both"]) + len(buckets["right_only"])
    counterexamples = [*buckets["left_only"], *buckets["right_only"]]
    if not counterexamples:
        counterexamples = buckets["neither"]
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:co-occurrence",
        result={
            "table_ref": table["table_ref"],
            "relation": "same_event",
            "evaluated_count": len(events),
            "counts": {key: len(rows) for key, rows in buckets.items()},
            "rates": {
                "right_given_left": len(buckets["both"]) / left_total if left_total else None,
                "left_given_right": len(buckets["both"]) / right_total if right_total else None,
            },
            "supporting_event_refs": [event["event_id"] for event in buckets["both"][:20]],
            "counterexample_event_refs": [event["event_id"] for event in counterexamples[:20]],
            "completeness": table["completeness"],
            "limitations": [*table["limitations"], "co_occurrence_does_not_establish_causation"],
        },
    ), 0


async def _query_event_sequence(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "fields", "mode"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    fields = _string_list(
        parameters.get("fields"), field_name="fields", max_items=4, allow_empty=False,
    )
    for field in fields:
        if _table_field(table, field)["value_type"] != "number":
            raise ProductCommandError("invalid_parameters", "sequence fields must be numeric")
    mode = parameters.get("mode")
    if mode == "early_middle_late":
        groups = _aggregate_groups(events, fields, "run_phase", table)
        body: dict[str, Any] = {"groups": groups}
    elif mode == "run_decile":
        deciles: list[dict[str, Any]] = []
        for decile in range(10):
            rows = [
                event for index, event in enumerate(events)
                if min(9, (index * 10) // max(1, len(events))) == decile
            ]
            if rows:
                deciles.append({
                    "decile": decile + 1,
                    "count": len(rows),
                    "fields": {field: _event_distribution(rows, field) for field in fields},
                })
        body = {"groups": deciles}
    elif mode == "adjacent":
        adjacent: dict[str, Any] = {}
        for field in fields:
            deltas: list[float] = []
            for previous, current in zip(events, events[1:]):
                before = _event_field_value(previous, field)
                after = _event_field_value(current, field)
                if all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in (before, after)
                ):
                    deltas.append(float(after) - float(before))
            adjacent[field] = (
                {
                    "pair_count": len(deltas),
                    "min": min(deltas),
                    "median": _nearest_rank(deltas, 0.5),
                    "max": max(deltas),
                    "mean": math.fsum(deltas) / len(deltas),
                }
                if deltas
                else {"pair_count": 0, "availability": "unavailable"}
            )
        body = {"adjacent_fields": adjacent}
    else:
        raise ProductCommandError("invalid_parameters", "sequence mode is invalid")
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:sequence:{mode}",
        result={
            "table_ref": table["table_ref"],
            "mode": mode,
            "evaluated_count": len(events),
            **body,
            "completeness": table["completeness"],
            "limitations": [*table["limitations"], "chronological_pattern_does_not_establish_learning_or_causation"],
        },
    ), 0


@dataclass
class _ToolBridge:
    bridge_id: str
    turn_id: str
    token_digest: str
    owner_id: str
    thread_id: int
    user_message_ref: str
    expires_at: float
    max_calls: int
    calls: int
    bytes_used: int = 0
    signal_points_used: int = 0
    signal_bytes_used: int = 0
    reachable_refs: set[str] = field(default_factory=set)
    cursors: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_tool_bridges: dict[str, _ToolBridge] = {}
_tool_bridge_lock = asyncio.Lock()
_MAX_BRIDGE_BYTES = 64 * 1024
_MAX_SIGNAL_BYTES = 32 * 1024
_MAX_SIGNAL_POINTS = 2_400
_MAX_SINGLE_RESULT_BYTES = 24 * 1024


def _bridge_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stable_reachable_refs(value: object) -> set[str]:
    refs: set[str] = set()

    def visit(child: object, key: str | None = None) -> None:
        if isinstance(child, str):
            if (
                key is not None
                and (
                    key.endswith("_ref")
                    or key.endswith("_refs")
                    or key in {"analysis_id", "segment_id", "event_id", "evidence_id", "id"}
                )
                and child.startswith(("analysis:", "run:", "segment:", "event:", "metric:", "evidence:"))
                and not child.startswith("cursor:")
            ):
                refs.add(child)
            return
        if isinstance(child, Mapping):
            for nested_key, nested_value in child.items():
                if isinstance(nested_key, str):
                    visit(nested_value, nested_key)
            return
        if isinstance(child, list):
            for nested_value in child:
                visit(nested_value, key)

    visit(value)
    return refs


def issue_tool_bridge(
    owner_id: str,
    thread_id: int,
    user_message_ref: str,
    endpoint: str,
    desktop_token: str | None = None,
    ttl_seconds: int = 300,
    max_calls: int = 6,
    *,
    reachable_refs: set[str] | None = None,
) -> dict[str, Any]:
    """Issue an in-memory, turn-scoped bearer bridge for one Coach turn."""
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
    if not isinstance(max_calls, int) or not 1 <= max_calls <= 6:
        raise ValueError("max_calls must be between 1 and 6")
    token = secrets.token_urlsafe(32)
    token_digest = _bridge_digest(token)
    expires_at = time.time() + ttl_seconds
    bridge_id = f"bridge:{uuid.uuid4().hex}"
    turn_id = f"turn:{uuid.uuid4().hex}"
    _tool_bridges[token_digest] = _ToolBridge(
        bridge_id=bridge_id,
        turn_id=turn_id,
        token_digest=token_digest,
        owner_id=owner_id,
        thread_id=thread_id,
        user_message_ref=user_message_ref,
        expires_at=expires_at,
        max_calls=max_calls,
        calls=0,
        reachable_refs=set(reachable_refs or ()),
    )
    bridge: dict[str, Any] = {
        "schema_version": "coach_tool_bridge.v1",
        "turn_id": turn_id,
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
        if bridge is None:
            return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    async with bridge.lock:
        async with _tool_bridge_lock:
            current = _tool_bridges.get(digest)
            if current is not bridge or bridge.expires_at <= time.time() or bridge.calls >= bridge.max_calls:
                _tool_bridges.pop(digest, None)
                bridge.cursors.clear()
                return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
            bridge.calls += 1
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
        command_name = trusted_payload.get("command_name")
        if command_name in _EVIDENCE_QUERY_COMMANDS:
            return await _execute_evidence_bridge(bridge, trusted_payload)
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
        bridge = _tool_bridges.pop(_bridge_digest(bearer_token), None)
        if bridge is not None:
            bridge.cursors.clear()
        return bridge is not None
