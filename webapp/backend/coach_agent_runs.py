from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from . import coach_runtime, coach_store
from .coach_context_refs import ContextRefError, build_context_bundle
from .db import get_conn


_tasks: dict[str, asyncio.Task[None]] = {}
_SAFE_ERROR_DOMAINS = {"network", "model", "permission", "tool"}
_PATH_OR_SECRET = re.compile(
    r"(?:[A-Za-z]:[\\/]|(?:^|\s)/(?:[^\s]+)|\\\\|file:|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=]|"
    r"\bbearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)


class AgentRunError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_text(value: object, *, max_length: int = 12_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise AgentRunError("invalid_text", "Coach text is invalid")
    if _PATH_OR_SECRET.search(value):
        raise AgentRunError("unsafe_content", "Coach content contains disallowed local or secret data")
    return value.strip()


def _safe_tool_events(value: object) -> list[dict[str, Any]]:
    try:
        events = coach_runtime._validate_tool_events(value)  # Shared runtime allow-list.
    except coach_runtime.CoachRuntimeError as error:
        raise AgentRunError("unsafe_tool_event", "Coach tool event was rejected") from error
    encoded = json.dumps(events, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if _PATH_OR_SECRET.search(encoded):
        raise AgentRunError("unsafe_tool_event", "Coach tool event was rejected")
    return events


async def execute_turn(**kwargs) -> dict[str, Any]:
    """Production turn adapter; tests replace this function with a cancellable fake."""
    from .coach_service import run_chat_turn

    result = await run_chat_turn(
        x_user_id=kwargs["owner_id"],
        thread_id=kwargs["thread_id"],
        prior_messages=kwargs["prior_messages"],
        user_msg_to_store=kwargs["content"],
        diagnosis=None,
        diagnostic_context=kwargs["context_bundle"],
        context_refs=kwargs["context_snapshots"],
        legacy_session_id=None,
        cost_session_id=None,
        tool_bridge_endpoint=kwargs.get("tool_bridge_endpoint"),
        desktop_token=kwargs.get("desktop_token"),
        persist=False,
        user_message_id=kwargs["user_message_id"],
        agent_run_ref=kwargs["run_ref"],
    )
    return {
        "status": result.status,
        "reply": result.reply,
        "tool_events": result.tool_events,
        "error": result.error,
    }


async def _append_event(
    run_ref: str,
    *,
    event_type: str,
    phase: str,
    code: str,
    message: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM coach_agent_run_events WHERE run_ref=?",
            (run_ref,),
        )
    ).fetchone()
    sequence = int(row["next_sequence"])
    payload_json = None
    if payload is not None:
        payload_json = json.dumps(
            dict(payload), ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        if _PATH_OR_SECRET.search(payload_json):
            raise AgentRunError("unsafe_tool_event", "Agent event payload was rejected")
    await conn.execute(
        "INSERT INTO coach_agent_run_events(event_ref, run_ref, sequence, event_type, "
        "phase, code, message, payload_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"agent_event:{uuid.uuid4().hex}", run_ref, sequence, event_type,
            phase, code, message, payload_json,
        ),
    )
    await conn.commit()


async def _set_run(
    run_ref: str,
    *,
    status: str,
    phase: str,
    partial_text: str | None = None,
    error: Mapping[str, Any] | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE coach_agent_runs SET status=?, phase=?, partial_text=?, error_json=?, "
        "started_at=CASE WHEN ? THEN COALESCE(started_at, CURRENT_TIMESTAMP) ELSE started_at END, "
        "finished_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at END, "
        "updated_at=CURRENT_TIMESTAMP WHERE run_ref=?",
        (
            status, phase, partial_text,
            json.dumps(dict(error), ensure_ascii=False, sort_keys=True) if error else None,
            int(started), int(finished), run_ref,
        ),
    )
    await conn.commit()


def _normalize_outcome(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid result")
    status = value.get("status")
    if status not in {"succeeded", "failed", "stopped"}:
        raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid status")
    reply = value.get("reply")
    safe_reply = _safe_text(reply) if isinstance(reply, str) and reply.strip() else None
    events = _safe_tool_events(value.get("tool_events", []))
    error = value.get("error")
    safe_error = None
    if error is not None:
        if not isinstance(error, Mapping):
            raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid error")
        domain = error.get("domain")
        code = error.get("code")
        message = error.get("message")
        retryable = error.get("retryable")
        if (
            domain not in _SAFE_ERROR_DOMAINS
            or not isinstance(code, str)
            or not isinstance(message, str)
            or not isinstance(retryable, bool)
        ):
            raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid error")
        safe_error = {
            "domain": domain,
            "code": _safe_text(code, max_length=120),
            "message": _safe_text(message, max_length=500),
            "retryable": retryable,
        }
    if status == "failed" and safe_error is None:
        safe_error = {
            "domain": "model",
            "code": "generation_failed",
            "message": "Coach generation failed",
            "retryable": True,
        }
    return {"status": status, "reply": safe_reply, "tool_events": events, "error": safe_error}


async def _run_agent(
    run_ref: str,
    *,
    owner_id: str,
    thread_id: int,
    content: str,
    bundle: dict[str, Any],
    snapshots: list[dict[str, Any]],
    tool_bridge_endpoint: str | None,
    desktop_token: str | None,
) -> None:
    try:
        await _set_run(run_ref, status="running", phase="text_generation", started=True)
        await _append_event(
            run_ref, event_type="phase", phase="text_generation",
            code="text_generation_started", message="Coach is generating a response",
        )
        prior = await coach_store.load_messages(thread_id)
        user_message_id = await coach_store.append_message(
            thread_id, "user", content, context_refs=snapshots,
        )
        outcome = _normalize_outcome(await execute_turn(
            run_ref=run_ref,
            owner_id=owner_id,
            thread_id=thread_id,
            content=content,
            context_bundle=bundle,
            context_snapshots=snapshots,
            prior_messages=prior,
            user_message_id=user_message_id,
            tool_bridge_endpoint=tool_bridge_endpoint,
            desktop_token=desktop_token,
        ))
        for tool_event in outcome["tool_events"]:
            await _append_event(
                run_ref,
                event_type=(
                    "confirmation"
                    if tool_event.get("status") == "needs_confirmation"
                    else "tool"
                ),
                phase="tool_execution",
                code=str(tool_event.get("status") or "tool_event"),
                message="Coach product tool event",
                payload=tool_event,
            )
        reply = outcome["reply"]
        if reply is not None:
            await coach_store.append_message(
                thread_id,
                "assistant",
                reply,
                trace=outcome["tool_events"],
                context_refs=snapshots,
            )
            await _append_event(
                run_ref, event_type="text", phase="text_generation",
                code="text_available", message="Coach response text is available",
            )
        status = outcome["status"]
        if status == "succeeded":
            await _set_run(
                run_ref, status="succeeded", phase="completed",
                partial_text=reply, finished=True,
            )
            await _append_event(
                run_ref, event_type="status", phase="completed",
                code="run_succeeded", message="Coach run completed",
            )
        elif status == "stopped":
            await _set_run(
                run_ref, status="stopped", phase="completed",
                partial_text=reply, finished=True,
            )
            await _append_event(
                run_ref, event_type="status", phase="completed",
                code="run_stopped", message="Coach run stopped by the user",
            )
        else:
            await _set_run(
                run_ref, status="failed", phase="completed",
                partial_text=reply, error=outcome["error"], finished=True,
            )
            await _append_event(
                run_ref, event_type="error", phase="completed",
                code=outcome["error"]["code"], message=outcome["error"]["message"],
            )
    except asyncio.CancelledError:
        await _set_run(run_ref, status="stopped", phase="completed", finished=True)
        await _append_event(
            run_ref, event_type="status", phase="completed",
            code="run_stopped", message="Coach run stopped by the user",
        )
        raise
    except (AgentRunError, ContextRefError) as error:
        failure = {
            "domain": "permission" if error.code.startswith("unsafe") else "tool",
            "code": error.code,
            "message": "Coach run output was rejected" if error.code.startswith("unsafe") else str(error),
            "retryable": False,
        }
        await _set_run(
            run_ref, status="failed", phase="completed", error=failure, finished=True,
        )
        await _append_event(
            run_ref, event_type="error", phase="completed",
            code=failure["code"], message=failure["message"],
        )
    except Exception:
        failure = {
            "domain": "model",
            "code": "generation_failed",
            "message": "Coach generation failed",
            "retryable": True,
        }
        await _set_run(
            run_ref, status="failed", phase="completed", error=failure, finished=True,
        )
        await _append_event(
            run_ref, event_type="error", phase="completed",
            code=failure["code"], message=failure["message"],
        )
    finally:
        _tasks.pop(run_ref, None)


async def create_run(
    owner_id: str,
    content: str,
    *,
    context_refs: Sequence[str] | None,
    parent_run_ref: str | None = None,
    attempt: int = 1,
    tool_bridge_endpoint: str | None = None,
    desktop_token: str | None = None,
) -> dict[str, Any]:
    safe_content = _safe_text(content)
    thread = await coach_store.get_or_create_primary_thread(owner_id)
    thread_id = int(thread["id"])
    bundle, snapshots = await build_context_bundle(thread_id, context_refs)
    run_ref = f"agent_run:{uuid.uuid4().hex}"
    conn = await get_conn()
    # Retry requests can arrive concurrently. Keep the dedupe read and insert
    # in one write transaction so only one child attempt is created.
    await conn.execute("BEGIN IMMEDIATE")
    try:
        if parent_run_ref is not None:
            existing = await conn.execute(
                "SELECT run_ref FROM coach_agent_runs "
                "WHERE owner_id=? AND parent_run_ref=? AND attempt=? "
                "ORDER BY created_at, run_ref LIMIT 1",
                (owner_id, parent_run_ref, attempt),
            )
            existing_row = await existing.fetchone()
            if existing_row is not None:
                await conn.execute("ROLLBACK")
                return await get_run(owner_id, existing_row["run_ref"])
        await conn.execute(
            "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, parent_run_ref, "
            "attempt, status, phase, content, context_refs_json) "
            "VALUES(?, ?, ?, ?, ?, 'queued', 'queued', ?, ?)",
            (
                run_ref, owner_id, thread_id, parent_run_ref, attempt, safe_content,
                json.dumps(snapshots, ensure_ascii=False, allow_nan=False, sort_keys=True),
            ),
        )
        await conn.commit()
    except Exception:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise
    await _append_event(
        run_ref, event_type="status", phase="queued",
        code="run_queued", message="Coach run queued",
    )
    task = asyncio.create_task(_run_agent(
        run_ref,
        owner_id=owner_id,
        thread_id=thread_id,
        content=safe_content,
        bundle=bundle,
        snapshots=snapshots,
        tool_bridge_endpoint=tool_bridge_endpoint,
        desktop_token=desktop_token,
    ))
    _tasks[run_ref] = task
    return await get_run(owner_id, run_ref)


async def _events(run_ref: str) -> list[dict[str, Any]]:
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT event_ref, sequence, event_type, phase, code, message, payload_json, "
            "created_at FROM coach_agent_run_events WHERE run_ref=? ORDER BY sequence",
            (run_ref,),
        )
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
        result.append({
            "schema_version": "coach_agent_run_event.v1",
            "event_ref": row["event_ref"],
            "sequence": row["sequence"],
            "type": row["event_type"],
            "phase": row["phase"],
            "code": row["code"],
            "message": row["message"],
            "payload": payload,
            "created_at": row["created_at"],
        })
    return result


async def get_run(owner_id: str, run_ref: str) -> dict[str, Any] | None:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT * FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
            (run_ref, owner_id),
        )
    ).fetchone()
    if row is None:
        return None
    contexts = json.loads(row["context_refs_json"] or "[]")
    error = json.loads(row["error_json"]) if row["error_json"] else None
    return {
        "schema_version": "coach_agent_run.v1",
        "run_ref": row["run_ref"],
        "parent_run_ref": row["parent_run_ref"],
        "attempt": row["attempt"],
        "status": row["status"],
        "phase": row["phase"],
        "partial_text": row["partial_text"],
        "error": error,
        "contexts": contexts,
        "events": await _events(run_ref),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


async def stop_run(owner_id: str, run_ref: str) -> dict[str, Any] | None:
    detail = await get_run(owner_id, run_ref)
    if detail is None:
        return None
    if detail["status"] in {"succeeded", "failed", "stopped"}:
        return detail
    conn = await get_conn()
    await conn.execute(
        "UPDATE coach_agent_runs SET stop_requested=1, updated_at=CURRENT_TIMESTAMP "
        "WHERE run_ref=? AND owner_id=?",
        (run_ref, owner_id),
    )
    await conn.commit()
    task = _tasks.get(run_ref)
    if task is not None:
        stopped_remotely = await coach_runtime.stop_pi_coach_turn(run_ref)
        if stopped_remotely:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
            except asyncio.TimeoutError:
                task.cancel()
            except asyncio.CancelledError:
                pass
        else:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    else:
        await _set_run(run_ref, status="stopped", phase="completed", finished=True)
        await _append_event(
            run_ref, event_type="status", phase="completed",
            code="run_stopped", message="Coach run stopped by the user",
        )
    return await get_run(owner_id, run_ref)


async def retry_run(
    owner_id: str,
    run_ref: str,
    *,
    tool_bridge_endpoint: str | None = None,
    desktop_token: str | None = None,
) -> dict[str, Any] | None:
    detail = await get_run(owner_id, run_ref)
    if detail is None:
        return None
    if detail["status"] != "failed" or not detail.get("error", {}).get("retryable"):
        raise AgentRunError("retry_not_allowed", "Coach run is not retryable")
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT content, context_refs_json FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
            (run_ref, owner_id),
        )
    ).fetchone()
    snapshots = json.loads(row["context_refs_json"] or "[]")
    refs = [item["context_ref"] for item in snapshots]
    return await create_run(
        owner_id,
        row["content"],
        context_refs=refs,
        parent_run_ref=run_ref,
        attempt=int(detail["attempt"]) + 1,
        tool_bridge_endpoint=tool_bridge_endpoint,
        desktop_token=desktop_token,
    )
