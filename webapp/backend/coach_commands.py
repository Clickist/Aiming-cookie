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

from . import calibration_profile_store, coach_context_refs, coach_guidance, coach_retest_decision, coach_store, evidence_store, history_trends, kovaak_connection_store, kovaak_run_store, queue
from .coach_steam_profiles import (
    _STEAM_ID,
    _STEAM_ID_IN_TEXT,
    _STEAM_PROFILE_REF,
    _STEAM_PROFILE_URL_IN_TEXT,
    contains_temporary_steam_profile,
    prepare_temporary_steam_profiles,
    redact_temporary_steam_profiles,
)
from .contracts import project_evidence_segment
from .source_requirements import validate_source_requirements
from .workspace import copy_path_to_path, remove_session_workspace, session_dir

RESULT_SCHEMA_VERSION = "coach_product_command_result.v1"
TOOL_BRIDGE_ENDPOINT = "/api/coach/tools/execute"
_TOOL_BRIDGE_PAYLOAD_KEYS = frozenset({
    "command_id", "command_name", "parameters", "idempotency_key", "instruction_quote",
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
    "kovaak_scores.lookup",
    "kovaak_scores.refresh_connected",
    "product.readiness.get",
    "calibration.get",
    "kovaak.connection.get",
    "coach.session.list",
    "eloshapes.query",
    "peripheral_profile.get",
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
    "analysis.delete",
    "training_plan.generate_draft",
    "training_plan.save",
    "training_plan.activate",
    "training_plan.pause",
    "training_plan.adjust",
    "calibration.save",
    "calibration.delete",
    "kovaak.connection.disconnect",
    "coach.session.create",
    "coach.session.rename",
    "coach.session.archive",
    "coach.session.delete",
    "coach.context.detach",
}
_EXPLICIT_USER_FACT_COMMANDS = {
    "training_plan.item.add",
    "training_plan.execution.record",
    "training_plan.retest.record",
}
_WRITE_COMMANDS |= _EXPLICIT_USER_FACT_COMMANDS
_COACH_INFERRED_CONFIRMATION_COMMANDS = _EXPLICIT_USER_FACT_COMMANDS | {
    "analysis.create_from_run",
    "analysis.delete",
    "analysis.retry",
}
_DIRECT_WRITE_COMMANDS = {
    "teaching_session.update",
    "peripheral_profile.update",
}
_COMMANDS = _QUERY_COMMANDS | _WRITE_COMMANDS | _DIRECT_WRITE_COMMANDS
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
    "authorization_source",
    "instruction_grant",
    "grant_metadata",
}
_REF_RE = re.compile(r"^(?P<kind>run|analysis):(?P<id>[1-9][0-9]*)$")
_PATH_OR_URL_TEXT_RE = re.compile(
    r'''(?:https?://|file:(?://)?|(?:^|[\s"'`()\[\]{}=,:])'''
    r'''(?:/|~[\\/]|\.{1,2}[\\/]|[A-Za-z]:[\\/]|\\\\))''',
    re.IGNORECASE,
)
_KOVAAK_SCORE_COMMANDS = frozenset({
    "kovaak_scores.lookup",
    "kovaak_scores.refresh_connected",
})


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
            self.audit_events.append({
                "owner_id": owner_id,
                "result": _copy_json(result),
                "context": _copy_json(_audit_context.get()),
            })

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


def _unavailable_kovaak_score_summary() -> dict[str, Any]:
    return {
        "schema_version": "kovaak_scores.v1",
        "availability": "unavailable",
        "observed_at": None,
        "stages": [],
        "items": [],
    }


def _bounded_kovaak_score_summary(snapshot: object) -> dict[str, Any]:
    """Keep a lookup tool result small and free of provider identity data."""
    if not isinstance(snapshot, Mapping) or snapshot.get("availability") != "available":
        return _unavailable_kovaak_score_summary()
    observed_at = snapshot.get("observed_at")
    stages = snapshot.get("stages")
    items = snapshot.get("items")
    if (
        snapshot.get("schema_version") != "kovaak_scores.v1"
        or not isinstance(observed_at, str)
        or len(observed_at) > 40
        or not isinstance(stages, list)
        or not isinstance(items, list)
        or len(stages) > 2
    ):
        return _unavailable_kovaak_score_summary()
    safe_stages: list[dict[str, Any]] = []
    for stage in stages:
        if (
            not isinstance(stage, Mapping)
            or stage.get("stage") not in {"easier", "medium"}
            or not isinstance(stage.get("completed"), int)
            or isinstance(stage.get("completed"), bool)
            or not isinstance(stage.get("required"), int)
            or isinstance(stage.get("required"), bool)
            or not isinstance(stage.get("rank"), int)
            or isinstance(stage.get("rank"), bool)
            or not isinstance(stage.get("rank_name"), str)
        ):
            return _unavailable_kovaak_score_summary()
        safe_stages.append({
            "stage": stage["stage"],
            "completed": stage["completed"],
            "required": stage["required"],
            "rank": stage["rank"],
            "rank_name": stage["rank_name"],
        })
    safe_items: list[dict[str, Any]] = []
    for item in items:
        if (
            not isinstance(item, Mapping)
            or item.get("stage") not in {"easier", "medium"}
            or not all(isinstance(item.get(field), str) and item[field] for field in (
                "name", "category", "subcategory", "item_rank_name",
            ))
            or not isinstance(item.get("score"), (int, float))
            or isinstance(item.get("score"), bool)
            or not math.isfinite(float(item["score"]))
            or not isinstance(item.get("item_rank"), int)
            or isinstance(item.get("item_rank"), bool)
            or not isinstance(item.get("completed"), bool)
        ):
            return _unavailable_kovaak_score_summary()
        safe_items.append({
            "stage": item["stage"],
            "name": item["name"],
            "category": item["category"],
            "subcategory": item["subcategory"],
            "score": float(item["score"]),
            "item_rank": item["item_rank"],
            "item_rank_name": item["item_rank_name"],
            "completed": item["completed"],
        })
    candidates = sorted(
        safe_items,
        key=lambda item: (item["item_rank"], item["score"], item["stage"], item["name"]),
    )[:8]
    return {
        "schema_version": "kovaak_scores.v1",
        "availability": "available",
        "observed_at": observed_at,
        "stages": safe_stages,
        "items": candidates,
    }


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
        "static_clicking": (
            "static_clicking"
            if "static_clicking.baseline.v1"
            in (resolution.get("allowed_analyzers") or [])
            else "flicking"
        ),
    }.get(family, "flicking")


async def create_analysis_from_run(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["multimodal"] = "multimodal",
    allow_parallel: bool = False,
    cm_per_360: float | None = None,
    fov: float | None = None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    managed_video_fingerprint: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Freeze a Run and enqueue its highest valid automatic evidence tier.

    ``input_mode`` remains an internal compatibility argument for older callers.
    New Run Analysis never trusts it: the frozen snapshot is the sole source
    of the selected tier.
    """
    existing = await queue.get_run_analysis_states(owner_id, run_id)
    completed = next((item for item in existing if item.get("status") == "done"), None)
    if completed is not None:
        session_id = int(completed["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    run_active = next(
        (item for item in existing if item.get("status") in {"uploading", "queued", "running"}),
        None,
    )
    if run_active is not None:
        session_id = int(run_active["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    active = await queue.get_active_session(owner_id)
    if active is not None and not allow_parallel:
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

    source_gate = validate_source_requirements(snapshot)
    if not source_gate["ready"]:
        missing = ", ".join(str(item) for item in source_gate["missing"])
        raise ProductCommandError(
            "input_unavailable",
            f"required Run sources are unavailable: {missing}",
            kind="unavailable",
        )
    selected_mode = source_gate["selected_mode"]
    if not isinstance(selected_mode, str):  # guarded by ready; keeps the queue contract strict
        raise ProductCommandError("input_unavailable", "Run has no supported analysis tier", kind="unavailable")
    snapshot["source_requirements_version"] = "automatic_quality_tier.v1"

    try:
        session_id = await queue.enqueue(
            owner_id,
            "",
            "",
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default=dict(profile_default) if isinstance(profile_default, Mapping) else None,
            manual_override=dict(manual_override) if isinstance(manual_override, Mapping) else None,
            analysis_type=_analysis_type_for_snapshot(snapshot),
            input_mode=selected_mode,
            kovaak_run_id=run_id,
            input_snapshot=snapshot,
            status="uploading",
            require_no_active=not allow_parallel,
        )
    except queue.ActiveSessionExists as exc:
        active = await queue.get_active_session(owner_id)
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}" if active is not None else None,
        ) from exc
    try:
        managed_video = ""
        managed_csv = ""
        workspace = session_dir(session_id)
        uses_video = selected_mode in {"multimodal", "video_fallback"}
        if uses_video and managed_video_source is not None:
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
        elif uses_video and run_video_source is not None:
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
        if selected_mode == "video_fallback":
            run_stats = snapshot["sources"].get("stats")
            stats_path = run_stats.get("path") if isinstance(run_stats, Mapping) else None
            stats_fingerprint = (
                run_stats.get("fingerprint") if isinstance(run_stats, Mapping) else None
            )
            if not isinstance(stats_path, str) or not isinstance(stats_fingerprint, Mapping):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source identity is unavailable",
                    kind="unavailable",
                )
            stats_source = Path(stats_path)
            stats_destination = workspace / "stats.csv"
            try:
                copy_path_to_path(stats_source, stats_destination)
            except OSError as exc:
                try:
                    source_matches = _matches_frozen_copy(stats_source, stats_fingerprint)
                except OSError:
                    source_matches = False
                if not source_matches:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Stats source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not _matches_frozen_copy(
                stats_destination,
                stats_fingerprint,
                source=stats_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_csv = str(stats_destination)
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
    return {
        "session_id": session_id,
        "analysis_ref": f"analysis:{session_id}",
        "input_mode": selected_mode,
        "limitations": [
            item for item in source_gate["missing"]
            if isinstance(item, str)
        ],
    }


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
    instruction_grant: _InstructionGrant | None = None,
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
    if command_name in _KOVAAK_SCORE_COMMANDS:
        return await _finish(
            owner_id,
            _result(
                command_id,
                "failed",
                warning_or_error=_error(
                    "bridge_required",
                    "KovaaK score commands are available only during the active Coach turn",
                ),
            ),
        )
    try:
        parameters = _require_mapping(envelope.get("parameters", {}))
    except ProductCommandError as exc:
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error(exc.code, exc.message)))
    if _contains_forbidden_model_data(parameters):
        return await _finish(owner_id, _result(command_id, "failed", warning_or_error=_error("untrusted_field", "paths, URLs, credentials and raw traces are not accepted")))
    if instruction_grant is not None:
        try:
            grant_digest = _idempotency_digest(command_name, parameters)
        except (TypeError, ValueError):
            grant_digest = None
        if (
            authorization_source != "explicit_user_request"
            or instruction_grant.owner_id != owner_id
            or instruction_grant.thread_id != thread_id
            or instruction_grant.user_message_ref != envelope.get("user_message_ref")
            or instruction_grant.command_name != command_name
            or instruction_grant.parameters_digest != grant_digest
            or instruction_grant.expires_at <= time.time()
        ):
            return await _finish(
                owner_id,
                _result(command_id, "failed", warning_or_error=_error("invalid_instruction_grant", "instruction grant is unavailable")),
            )

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
        **({"instruction_grant": instruction_grant.audit_projection()} if instruction_grant is not None else {}),
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
    cm_per_360: float | None,
    fov: float | None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    idempotency_key: str | None = None,
    allow_parallel: bool = False,
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
                    **({"allow_parallel": True} if allow_parallel else {}),
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
        command_name == "training_plan.retest.record"
        and authorization_source == "coach_inferred"
        and isinstance(parameters.get("analysis_refs"), list)
        and len(parameters["analysis_refs"]) == 2
    ):
        limitations = parameters.get("limitations")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) for item in limitations
        ):
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(
                        "invalid_parameters", "limitations must be a list of strings",
                    ),
                ),
            )
        try:
            decision = await coach_retest_decision.decide_two_analysis_retest(
                owner_id,
                parameters["analysis_refs"],
                parameters.get("expected_metric_ref"),
            )
        except coach_retest_decision.AnalysisForbidden:
            return await _finish(
                owner_id,
                _result(command_id, "failed", warning_or_error=_error("forbidden", "无权访问该 Analysis")),
            )
        except coach_retest_decision.AnalysisUnavailable:
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "unavailable",
                    warning_or_error=_error("analysis_unavailable", "Analysis 结果不可用"),
                ),
            )
        except coach_retest_decision.AnalysisInvalid as exc:
            return await _finish(
                owner_id,
                _result(command_id, "failed", warning_or_error=_error("invalid_parameters", str(exc))),
            )
        parameters = {
            **parameters,
            "comparability": decision["comparability"],
            "result": decision["result"],
            "limitations": list(dict.fromkeys([*limitations, *decision["limitations"]])),
        }
    if command_name == "analysis.delete":
        try:
            _exact_parameters(parameters, {"analysis_ref"})
            _parse_ref(parameters.get("analysis_ref"), "analysis")
        except ProductCommandError as exc:
            return await _finish(
                owner_id,
                _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(exc.code, exc.message),
                ),
            )
    if (
        command_name in _EXPLICIT_USER_FACT_COMMANDS
        and authorization_source == "system_safe"
    ):
        return await _finish(
            owner_id,
            _result(
                command_id,
                "failed",
                warning_or_error=_error(
                    "explicit_user_required",
                    "执行记录和复测必须由用户确认或明确提交",
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

    if reservation is not None and not reservation_recorded:
        try:
            prior = await _journal().claim(
                owner_id, command_name, idempotency_key, digest, reservation,
            )
        except coach_store.CommandIdempotencyConflictError:
            return await _finish(owner_id, _idempotency_conflict_result(command_id))
        if prior is not None:
            return await _replay_idempotent_result(owner_id, command_id, prior)

    if (
        authorization_source == "coach_inferred"
        and command_name in _COACH_INFERRED_CONFIRMATION_COMMANDS
    ):
        pending_confirmation = await _create_confirmation(
            owner_id, command_name, parameters, _risk_for(command_name),
        )
        result = _result(
            command_id,
            "needs_confirmation",
            confirmation=_confirmation(
                command_name,
                parameters,
                reason="Coach 推断的操作需要用户确认后执行",
                confirmation_ref=pending_confirmation["confirmation_ref"],
                expires_at=pending_confirmation["expires_at"],
            ),
        )
        return await _record_and_finish(
            owner_id, command_name, idempotency_key, digest, result,
        )

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
        elif command_name == "product.readiness.get":
            if parameters:
                raise ProductCommandError("invalid_parameters", "product readiness does not accept parameters")
            result = _result(command_id, "succeeded", result=await coach_guidance.get_product_readiness(owner_id))
        elif command_name == "calibration.get":
            if parameters:
                raise ProductCommandError("invalid_parameters", "calibration.get does not accept parameters")
            profile = await calibration_profile_store.get_profile(owner_id)
            result = _result(command_id, "succeeded", result_ref="calibration:current", result={"calibration_ref": "calibration:current", **profile})
        elif command_name == "kovaak.connection.get":
            if parameters:
                raise ProductCommandError("invalid_parameters", "kovaak.connection.get does not accept parameters")
            connected = await kovaak_connection_store.get_connection(owner_id) is not None
            result = _result(command_id, "succeeded", result_ref="kovaak_connection:current", result={"connection_ref": "kovaak_connection:current", "connected": connected})
        elif command_name == "coach.session.list":
            if parameters:
                raise ProductCommandError("invalid_parameters", "coach.session.list does not accept parameters")
            result = _result(command_id, "succeeded", result=await coach_store.list_sessions(owner_id))
        elif command_name == "eloshapes.query":
            from . import eloshapes_query

            allowed = (
                "weight_max", "size_category", "shape", "front_flare",
                "side_curvature", "hump_placement", "hand_compatibility",
                "brand_search", "model_search", "limit",
            )
            filtered = {k: v for k, v in parameters.items() if k in allowed and v is not None}
            query_result = eloshapes_query.query_mice(**filtered)
            result = _result(command_id, "succeeded", result=query_result)
        elif command_name == "peripheral_profile.get":
            if parameters:
                raise ProductCommandError("invalid_parameters", "peripheral_profile.get does not accept parameters")
            from . import peripheral_profile_store

            profile = await peripheral_profile_store.get_profile(owner_id)
            result = _result(command_id, "succeeded", result_ref="peripheral_profile:current", result=profile)
        elif command_name == "peripheral_profile.update":
            from . import peripheral_profile_store

            profile = await peripheral_profile_store.update_profile(owner_id, parameters)
            result = _result(command_id, "succeeded", result_ref="peripheral_profile:current", result=profile)
        elif command_name == "analysis.create_from_run":
            run_id, _ = _parse_ref(parameters.get("run_ref"), "run")
            if trusted_analysis_args is None:
                created = await create_analysis_from_run(
                    owner_id,
                    run_id,
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
        elif command_name == "analysis.delete":
            analysis_id, ref = _parse_ref(parameters.get("analysis_ref"), "analysis")
            try:
                deleted = await queue.delete_session(analysis_id, owner_id)
            except queue.SessionNotFound as exc:
                raise ProductCommandError(
                    "not_found", "Analysis 不存在", kind="unavailable", result_ref=ref,
                ) from exc
            except queue.SessionForbidden as exc:
                raise ProductCommandError("forbidden", "无权访问此 Analysis") from exc
            except queue.SessionNotDeletable as exc:
                raise ProductCommandError(
                    exc.code, exc.message, kind="unavailable", result_ref=ref,
                ) from exc
            result = _result(
                command_id,
                "succeeded",
                result_ref=ref,
                result={
                    "analysis_ref": ref,
                    "deleted": bool(deleted.get("deleted")),
                    "cleanup_pending": bool(deleted.get("cleanup_failed")),
                },
            )
        elif command_name == "calibration.save":
            saved = await calibration_profile_store.save_profile(owner_id, cm_per_360=parameters.get("cm_per_360"), fov=parameters.get("fov"))
            result = _result(command_id, "succeeded", result_ref="calibration:current", result={"calibration_ref": "calibration:current", **saved})
        elif command_name == "calibration.delete":
            deleted = await calibration_profile_store.delete_profile(owner_id)
            result = _result(command_id, "succeeded", result_ref="calibration:current", result={"calibration_ref": "calibration:current", **deleted})
        elif command_name == "kovaak.connection.disconnect":
            deleted = await kovaak_connection_store.delete_connection(owner_id)
            result = _result(command_id, "succeeded", result_ref="kovaak_connection:current", result={"connection_ref": "kovaak_connection:current", "disconnected": True, "was_connected": deleted})
        elif command_name == "coach.session.create":
            created = await coach_store.create_session(owner_id, title=parameters.get("title"))
            result = _result(command_id, "succeeded", result_ref=f"session:{created['id']}", result=created)
        elif command_name in {"coach.session.rename", "coach.session.archive", "coach.session.delete"}:
            session_ref = parameters.get("session_ref")
            if not isinstance(session_ref, str) or not session_ref.startswith("session:"):
                raise ProductCommandError("invalid_parameters", "session_ref is required")
            try:
                session_id = int(session_ref.split(":", 1)[1])
            except ValueError as exc:
                raise ProductCommandError("invalid_parameters", "session_ref is invalid") from exc
            if command_name == "coach.session.rename":
                changed = await coach_store.rename_session(owner_id, session_id, str(parameters.get("title", "")))
            elif command_name == "coach.session.archive":
                changed = await coach_store.archive_session(owner_id, session_id)
            else:
                changed = await coach_store.soft_delete_session(owner_id, session_id)
            if changed is None:
                raise ProductCommandError("not_found", "Coach session is unavailable")
            result = _result(command_id, "succeeded", result_ref=session_ref, result=changed)
        elif command_name == "coach.context.detach":
            context_ref = parameters.get("context_ref")
            thread_id = parameters.get("session_id")
            if not isinstance(context_ref, str) or not isinstance(thread_id, int):
                raise ProductCommandError("invalid_parameters", "context_ref and session_id are required")
            detached = await coach_context_refs.detach_context(owner_id, thread_id, context_ref)
            if detached is None:
                raise ProductCommandError("not_found", "Coach context is unavailable")
            result = _result(command_id, "succeeded", result_ref=context_ref, result={"context_ref": context_ref, "status": detached[0]})
        elif command_name == "teaching_session.update":
            from . import teaching_session_store as session_store
            session_ref_param = parameters.get("session_ref")
            expected_version_param = parameters.get("expected_version")
            next_phase_param = parameters.get("next_phase")
            updates_param = parameters.get("updates", {})
            if not isinstance(session_ref_param, str):
                raise ProductCommandError("invalid_parameters", "session_ref is required")
            if isinstance(expected_version_param, bool) or not isinstance(expected_version_param, int) or expected_version_param < 0:
                raise ProductCommandError("invalid_parameters", "expected_version must be a non-negative integer")
            if next_phase_param is not None and not isinstance(next_phase_param, str):
                raise ProductCommandError("invalid_parameters", "next_phase must be a string or null")
            if not isinstance(updates_param, Mapping):
                raise ProductCommandError("invalid_parameters", "updates must be an object")
            try:
                updated = await session_store.update_state_partial(
                    owner_id, session_ref_param, expected_version_param,
                    next_phase_param, dict(updates_param),
                )
            except session_store.TeachingSessionConflictError as exc:
                raise ProductCommandError(
                    "session_conflict", "TeachingSession changed or is unavailable",
                    kind="unavailable", result_ref=session_ref_param,
                ) from exc
            except ValueError as exc:
                raise ProductCommandError("invalid_parameters", str(exc)) from exc
            result = _result(
                command_id, "succeeded", result_ref=session_ref_param,
                result={
                    key: updated[key]
                    for key in ("session_ref", "version", "state", "active_run_ref")
                },
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
                confirmed=True,
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


def _exact_parameters(parameters: dict[str, Any], allowed: set[str]) -> None:
    if set(parameters) - allowed:
        raise ProductCommandError("invalid_parameters", "unsupported evidence query parameters")


from .coach_evidence_bridge import (
    _InstructionGrant,
    _ToolBridge,
    _downsample_points,
    _load_evidence_for_bridge,
    _processed_table_events,
    execute_tool_bridge,
    issue_tool_bridge,
    revoke_tool_bridge,
)
