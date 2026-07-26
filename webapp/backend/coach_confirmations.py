from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from . import coach_store
from .db import get_conn


_ACTION_IMPACTS = {
    "analysis_delete": (
        "analysis_becomes_unavailable",
        "删除后 Analysis 不再可用；Coach 消息保留，但相关引用会标记为已删除。",
    ),
    "overwrite": (
        "existing_value_replaced",
        "现有值将被替换，旧值不会由本操作自动恢复。",
    ),
    "provider_credential_change": (
        "provider_credential_replaced",
        "本地 Provider credential 将发生变化，现有 Coach 连接可能需要重新测试。",
    ),
    "provider_oauth_authorize": (
        "provider_authorization_starts",
        "将打开 Provider 官方授权流程；Aiming Cookie 不会创建产品账号。",
    ),
    "provider_oauth_revoke": (
        "local_provider_credential_removed",
        "将移除本地 credential；远端 token 是否撤销取决于 Provider。",
    ),
    "upload_share": (
        "data_leaves_local_device",
        "确认后指定数据可能离开本机；未确认前不会上传或分享。",
    ),
    "external_purchase": (
        "external_purchase_link_opens",
        "将打开外部购买页面；购买关系属于用户与外部商家。",
    ),
    "coach_side_effect": (
        "coach_inferred_write_executes",
        "将执行 Coach 推断的副作用操作；取消不会改变当前数据。",
    ),
}
_SAFE_REF = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:@-]{1,160}$")
_decision_lock = asyncio.Lock()


class ConfirmationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _out(
    row,
    audit_ref: str | None = None,
    execution: dict[str, Any] | None = None,
    audit_state: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "coach_confirmation.v1",
        "confirmation_ref": row["confirmation_ref"],
        "action": row["action"],
        "target_ref": row["target_ref"],
        "status": row["status"],
        "impact": {
            "code": row["impact_code"],
            "message": row["impact_message"],
        },
        "audit_ref": audit_ref,
        "audit_state": audit_state,
        "execution": execution,
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


async def create_confirmation(owner_id: str, action: str, target_ref: str) -> dict[str, Any]:
    impact = _ACTION_IMPACTS.get(action)
    if impact is None or action == "coach_side_effect":
        raise ValueError("confirmation_not_required")
    if not _SAFE_REF.fullmatch(target_ref):
        raise ValueError("invalid_target_ref")
    confirmation_ref = f"confirmation:{uuid.uuid4().hex}"
    conn = await get_conn()
    row = await (
        await conn.execute(
            "INSERT INTO coach_confirmation_requests(confirmation_ref, owner_id, action, "
            "target_ref, impact_code, impact_message) VALUES(?, ?, ?, ?, ?, ?) "
            "RETURNING confirmation_ref, action, target_ref, status, impact_code, "
            "impact_message, created_at, decided_at",
            (confirmation_ref, owner_id, action, target_ref, impact[0], impact[1]),
        )
    ).fetchone()
    await conn.commit()
    return _out(row)


async def sync_product_command_confirmations(
    owner_id: str,
    user_message_ref: str,
) -> list[dict[str, Any]]:
    """Project trusted pending command confirmations into UI agent-run events."""
    if not isinstance(user_message_ref, str) or not user_message_ref:
        return []
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT command_name, result_json FROM coach_product_commands "
            "WHERE owner_id=? AND user_message_ref=? AND status='needs_confirmation' "
            "ORDER BY audit_id",
            (owner_id, user_message_ref),
        )
    ).fetchall()
    events_by_confirmation: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict):
            continue
        confirmation = result.get("confirmation")
        if not isinstance(confirmation, dict):
            continue
        confirmation_ref = confirmation.get("confirmation_ref")
        if not isinstance(confirmation_ref, str) or not confirmation_ref.startswith("confirmation:"):
            continue
        canonical = await (
            await conn.execute(
                "SELECT confirmation_ref FROM coach_command_confirmations "
                "WHERE confirmation_ref=? AND owner_id=? AND user_message_ref=?",
                (confirmation_ref, owner_id, user_message_ref),
            )
        ).fetchone()
        if canonical is None:
            continue
        target_ref = confirmation.get("target_ref")
        if not isinstance(target_ref, str) or _SAFE_REF.fullmatch(target_ref) is None:
            target_ref = result.get("command_id")
        if not isinstance(target_ref, str) or _SAFE_REF.fullmatch(target_ref) is None:
            continue
        impact = _ACTION_IMPACTS["coach_side_effect"]
        await conn.execute(
            "INSERT INTO coach_confirmation_requests(confirmation_ref, owner_id, action, "
            "target_ref, impact_code, impact_message) VALUES(?, ?, 'coach_side_effect', ?, ?, ?) "
            "ON CONFLICT(confirmation_ref) DO NOTHING",
            (confirmation_ref, owner_id, target_ref, impact[0], impact[1]),
        )
        request_row = await (
            await conn.execute(
                "SELECT confirmation_ref, action, target_ref, status, impact_code, "
                "impact_message, created_at, decided_at FROM coach_confirmation_requests "
                "WHERE confirmation_ref=? AND owner_id=?",
                (confirmation_ref, owner_id),
            )
        ).fetchone()
        if request_row is None:
            continue
        event = {
            "type": "product_command",
            "command_id": result.get("command_id"),
            "command_name": row["command_name"],
            "status": result.get("status"),
            "result_ref": result.get("result_ref"),
            "audit_ref": result.get("audit_ref"),
            "ui_event": result.get("ui_event"),
            "warning_or_error": result.get("warning_or_error"),
            "confirmation": _out(request_row),
        }
        events_by_confirmation[confirmation_ref] = event
    await conn.commit()
    return list(events_by_confirmation.values())


def _decode_execution(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_unknown_command_outcome(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict) or value.get("status") != "unavailable":
        return False
    warning = value.get("warning_or_error")
    return isinstance(warning, dict) and warning.get("code") == "idempotency_outcome_unknown"


async def _execute_confirmed_command(
    owner_id: str,
    confirmation_ref: str,
) -> dict[str, Any]:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT command_name, parameters_json, idempotency_key, thread_id "
            "FROM coach_command_confirmations WHERE confirmation_ref=? AND owner_id=?",
            (confirmation_ref, owner_id),
        )
    ).fetchone()
    if row is None or not row["parameters_json"] or not row["idempotency_key"]:
        raise ConfirmationError(
            "confirmation_execution_unavailable",
            "Confirmed Coach action is no longer executable",
        )
    try:
        parameters = json.loads(row["parameters_json"])
    except json.JSONDecodeError as error:
        raise ConfirmationError(
            "confirmation_execution_unavailable",
            "Confirmed Coach action is no longer executable",
        ) from error
    if not isinstance(parameters, dict):
        raise ConfirmationError(
            "confirmation_execution_unavailable",
            "Confirmed Coach action is no longer executable",
        )
    from . import coach_commands

    return await coach_commands.execute_product_command(
        owner_id,
        {
            "command_name": row["command_name"],
            "parameters": parameters,
            "idempotency_key": row["idempotency_key"],
            "confirmation_ref": confirmation_ref,
        },
        authorization_source="confirmed",
        thread_id=row["thread_id"],
    )


async def _read_confirmation_audit(
    conn,
    confirmation_ref: str,
    owner_id: str,
):
    return await (
        await conn.execute(
            "SELECT audit_ref, decision, result_status, audit_state, execution_result_json "
            "FROM coach_confirmation_audits WHERE confirmation_ref=? AND owner_id=?",
            (confirmation_ref, owner_id),
        )
    ).fetchone()


async def _create_pending_confirmation_audit(
    conn,
    confirmation_ref: str,
    owner_id: str,
    decision: str,
) -> str | None:
    """Durably record the decision before a Coach side effect can begin."""
    await conn.execute("BEGIN IMMEDIATE")
    try:
        request = await (
            await conn.execute(
                "SELECT status FROM coach_confirmation_requests "
                "WHERE confirmation_ref=? AND owner_id=?",
                (confirmation_ref, owner_id),
            )
        ).fetchone()
        if request is None or request["status"] != "pending":
            await conn.execute("ROLLBACK")
            return None
        audit_ref = f"confirmation_audit:{uuid.uuid4().hex}"
        status = "confirmed" if decision == "confirm" else "rejected"
        await conn.execute(
            "INSERT INTO coach_confirmation_audits(audit_ref, confirmation_ref, owner_id, "
            "decision, result_status, audit_state) VALUES(?, ?, ?, ?, ?, 'pending')",
            (audit_ref, confirmation_ref, owner_id, decision, status),
        )
        cursor = await conn.execute(
            "UPDATE coach_confirmation_requests SET status=?, decided_at=CURRENT_TIMESTAMP "
            "WHERE confirmation_ref=? AND owner_id=? AND status='pending'",
            (status, confirmation_ref, owner_id),
        )
        if cursor.rowcount != 1:
            await conn.execute("ROLLBACK")
            return None
        await conn.commit()
        return audit_ref
    except Exception:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise


async def _complete_confirmation_audit(
    confirmation_ref: str,
    owner_id: str,
    execution: dict[str, Any] | None,
) -> bool:
    """Finalize the pending audit only after execution has returned a safe result."""
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = await conn.execute(
            "UPDATE coach_confirmation_audits SET audit_state='completed', "
            "execution_result_json=? WHERE confirmation_ref=? AND owner_id=? "
            "AND audit_state='pending'",
            (
                json.dumps(execution, ensure_ascii=False, sort_keys=True) if execution else None,
                confirmation_ref,
                owner_id,
            ),
        )
        if cursor.rowcount != 1:
            await conn.execute("ROLLBACK")
            return False
        await conn.commit()
        return True
    except Exception:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise


async def _stored_command_execution(
    owner_id: str,
    confirmation_ref: str,
) -> dict[str, Any] | None:
    """Read a completed canonical command result without replaying its side effect."""
    conn = await get_conn()
    confirmation = await (
        await conn.execute(
            "SELECT command_name, idempotency_key FROM coach_command_confirmations "
            "WHERE confirmation_ref=? AND owner_id=?",
            (confirmation_ref, owner_id),
        )
    ).fetchone()
    if confirmation is None or not confirmation["idempotency_key"]:
        return None
    row = await (
        await conn.execute(
            "SELECT result_json FROM coach_command_idempotency "
            "WHERE owner_id=? AND command_name=? AND idempotency_key=?",
            (
                owner_id,
                confirmation["command_name"],
                confirmation["idempotency_key"],
            ),
        )
    ).fetchone()
    if row is None:
        return None
    return _decode_execution(row["result_json"])


async def _reconcile_pending_confirmation(
    owner_id: str,
    confirmation_ref: str,
) -> bool:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT request.action, audit.decision, audit.audit_state "
            "FROM coach_confirmation_audits AS audit "
            "JOIN coach_confirmation_requests AS request "
            "ON request.confirmation_ref=audit.confirmation_ref "
            "AND request.owner_id=audit.owner_id "
            "WHERE audit.confirmation_ref=? AND audit.owner_id=?",
            (confirmation_ref, owner_id),
        )
    ).fetchone()
    if row is None or row["audit_state"] != "pending":
        return row is not None

    execution = None
    if row["action"] == "coach_side_effect" and row["decision"] == "confirm":
        execution = await _stored_command_execution(owner_id, confirmation_ref)
        if execution is None:
            execution = await _execute_confirmed_command(owner_id, confirmation_ref)
        if _is_unknown_command_outcome(execution):
            return False
    elif row["action"] == "coach_side_effect":
        await coach_store.cancel_command_confirmation(owner_id, confirmation_ref)
    return await _complete_confirmation_audit(confirmation_ref, owner_id, execution)


async def reconcile_pending_confirmations(owner_id: str | None = None) -> dict[str, int]:
    """Complete audits that survived a process stop after their pending record."""
    async with _decision_lock:
        conn = await get_conn()
        query = (
            "SELECT confirmation_ref, owner_id FROM coach_confirmation_audits "
            "WHERE audit_state='pending'"
        )
        params: tuple[str, ...] = ()
        if owner_id is not None:
            query += " AND owner_id=?"
            params = (owner_id,)
        rows = await (await conn.execute(query, params)).fetchall()
        summary = {"processed": 0, "completed": 0, "failed": 0}
        for row in rows:
            summary["processed"] += 1
            try:
                completed = await _reconcile_pending_confirmation(
                    row["owner_id"], row["confirmation_ref"],
                )
            except Exception:
                summary["failed"] += 1
                continue
            if completed:
                summary["completed"] += 1
            else:
                summary["failed"] += 1
        return summary


async def decide_confirmation(
    owner_id: str,
    confirmation_ref: str,
    decision: str,
) -> dict[str, Any] | None:
    if decision not in {"confirm", "reject"}:
        raise ValueError("invalid_decision")
    async with _decision_lock:
        conn = await get_conn()
        row = await (
            await conn.execute(
                "SELECT confirmation_ref, action, target_ref, status, impact_code, "
                "impact_message, created_at, decided_at FROM coach_confirmation_requests "
                "WHERE confirmation_ref=? AND owner_id=?",
                (confirmation_ref, owner_id),
            )
        ).fetchone()
        if row is None:
            return None
        audit = await _read_confirmation_audit(conn, confirmation_ref, owner_id)
        if audit is not None:
            if audit["decision"] != decision:
                return _out(
                    row, audit["audit_ref"],
                    _decode_execution(audit["execution_result_json"]),
                    audit["audit_state"],
                )
            if audit["audit_state"] == "pending":
                await _reconcile_pending_confirmation(owner_id, confirmation_ref)
                audit = await _read_confirmation_audit(conn, confirmation_ref, owner_id)
            if audit is None:
                return None
            return _out(
                row, audit["audit_ref"],
                _decode_execution(audit["execution_result_json"]),
                audit["audit_state"],
            )

        audit_ref = await _create_pending_confirmation_audit(
            conn, confirmation_ref, owner_id, decision,
        )
        if audit_ref is None:
            return None

        execution = None
        if row["action"] == "coach_side_effect":
            if decision == "confirm":
                execution = await _execute_confirmed_command(owner_id, confirmation_ref)
            else:
                await coach_store.cancel_command_confirmation(owner_id, confirmation_ref)
        await _complete_confirmation_audit(confirmation_ref, owner_id, execution)
        current = await (
            await conn.execute(
                "SELECT confirmation_ref, action, target_ref, status, impact_code, "
                "impact_message, created_at, decided_at FROM coach_confirmation_requests "
                "WHERE confirmation_ref=? AND owner_id=?",
                (confirmation_ref, owner_id),
            )
        ).fetchone()
        return _out(current, audit_ref, execution, "completed")
