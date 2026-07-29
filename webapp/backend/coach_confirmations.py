from __future__ import annotations

import asyncio
import json
import math
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
_CANONICAL_REF = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_INTERNAL_REF = re.compile(r"\b[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9]", re.IGNORECASE)
_UNSAFE_DISPLAY_TEXT = re.compile(
    r"(?:https?://|file:(?://)?|[A-Za-z]:[\\/]|\\\\|(?:^|\s)/\S+|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=]|"
    r"\bbearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
_ITEM_PAYLOAD_FIELDS = {
    "diagnosis_ref",
    "knowledge_ref",
    "scenario_profile_ref",
    "baseline_metric_ref",
    "expected_direction",
    "practice_condition",
    "cue",
    "dose_guardrail",
    "matched_retest_ref",
    "near_transfer_retest_ref",
    "review_date",
}
_COMPLETION_LABELS = {
    "completed": "已完成",
    "partial": "部分完成",
    "skipped": "已跳过",
}
_RETEST_KIND_LABELS = {
    "matched": "同条件复测",
    "near_transfer": "近迁移复测",
}
_COMPARABILITY_LABELS = {
    "comparable": "与基线可比",
    "not_comparable": "与基线不可比",
    "unavailable": "可比性暂时无法确认",
}
_RETEST_OUTCOME_LABELS = {
    "coach_retest_outcome.v1:improved": (
        "这次表现有改善",
        "确认后会继续当前训练项",
    ),
    "coach_retest_outcome.v1:unchanged": (
        "这次基本没变化",
        "确认后会把当前训练项退回待安排",
    ),
    "coach_retest_outcome.v1:mixed_or_inconclusive": (
        "结果混合或暂时看不清",
        "确认后会把当前训练项退回待安排",
    ),
    "coach_retest_outcome.v1:worsened": (
        "这次表现变差",
        "确认后会结束当前训练项",
    ),
}
_LIMITATION_LABELS = {
    "metric_change_policy_missing": "该指标还没有可靠的变化阈值",
}
_EXPECTED_DIRECTIONS = {
    "lower_better",
    "higher_better",
    "target_band",
    "descriptive_only",
    "comparison_only",
}
_decision_lock = asyncio.Lock()


class ConfirmationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_display_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > maximum
        or any(ord(char) < 32 for char in text)
        or _INTERNAL_REF.search(text)
        or _UNSAFE_DISPLAY_TEXT.search(text)
    ):
        return None
    return text


def _safe_dose(value: object) -> str | None:
    if not isinstance(value, dict) or set(value) != {"amount", "unit"}:
        return None
    amount = value.get("amount")
    unit = _safe_display_text(value.get("unit"), maximum=40)
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or amount < 0
        or amount > 1_000_000
        or not math.isfinite(float(amount))
        or unit is None
    ):
        return None
    rendered = str(int(amount)) if float(amount).is_integer() else format(float(amount), ".6g")
    return f"{rendered} {unit}"


def _safe_ref_value(value: object, *, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(f"{prefix}:")
        and _CANONICAL_REF.fullmatch(value) is not None
    )


def _bounded_impact(code: str, message: str) -> tuple[str, str] | None:
    return (code, message) if len(message) <= 1000 else None


def _project_item_impact(parameters: dict[str, Any]) -> tuple[str, str] | None:
    allowed_shapes = (
        {"plan_ref", "item_payload"},
        {"plan_ref", "plan_version", "item_payload"},
    )
    if set(parameters) not in allowed_shapes:
        return None
    plan_version = parameters.get("plan_version")
    if (
        not _safe_ref_value(parameters.get("plan_ref"), prefix="plan")
        or (
            "plan_version" in parameters
            and (
                isinstance(plan_version, bool)
                or not isinstance(plan_version, int)
                or plan_version < 1
            )
        )
    ):
        return None
    payload = parameters.get("item_payload")
    if not isinstance(payload, dict) or set(payload) != _ITEM_PAYLOAD_FIELDS:
        return None
    ref_fields = {
        "diagnosis_ref": "diagnosis",
        "knowledge_ref": "knowledge",
        "scenario_profile_ref": "scenario",
        "baseline_metric_ref": "metric",
        "matched_retest_ref": "retest-spec",
        "near_transfer_retest_ref": "retest-spec",
    }
    if (
        any(
            not _safe_ref_value(payload.get(field), prefix=prefix)
            for field, prefix in ref_fields.items()
        )
        or payload.get("expected_direction") not in _EXPECTED_DIRECTIONS
        or _safe_display_text(payload.get("review_date"), maximum=64) is None
    ):
        return None
    practice_condition = _safe_display_text(payload.get("practice_condition"), maximum=240)
    cue = _safe_display_text(payload.get("cue"), maximum=240)
    dose_guardrail = _safe_display_text(payload.get("dose_guardrail"), maximum=240)
    if practice_condition is None or cue is None or dose_guardrail is None:
        return None
    return _bounded_impact(
        "training_plan_item_will_be_added",
        f"将把这项练习加入训练计划：练习条件“{practice_condition}”；"
        f"本轮提示“{cue}”；训练边界“{dose_guardrail}”。",
    )


def _project_execution_impact(parameters: dict[str, Any]) -> tuple[str, str] | None:
    if set(parameters) != {
        "item_ref",
        "scenario_ref",
        "run_refs",
        "planned_dose",
        "completed_dose",
        "completion_status",
        "user_feedback",
    }:
        return None
    run_refs = parameters.get("run_refs")
    if (
        not _safe_ref_value(parameters.get("item_ref"), prefix="plan-item")
        or not _safe_ref_value(parameters.get("scenario_ref"), prefix="scenario")
        or not isinstance(run_refs, list)
        or not 1 <= len(run_refs) <= 16
        or not all(_safe_ref_value(ref, prefix="run") for ref in run_refs)
    ):
        return None
    planned = _safe_dose(parameters.get("planned_dose"))
    completed = _safe_dose(parameters.get("completed_dose"))
    status = _COMPLETION_LABELS.get(parameters.get("completion_status"))
    feedback = _safe_display_text(parameters.get("user_feedback"), maximum=1000)
    if planned is None or completed is None or status is None or feedback is None:
        return None
    return _bounded_impact(
        "training_execution_will_be_recorded",
        f"将记录这次训练：计划 {planned}，实际 {completed}，状态为{status}；"
        f"你的反馈是“{feedback}”。",
    )


def _project_retest_impact(parameters: dict[str, Any]) -> tuple[str, str] | None:
    if set(parameters) != {
        "item_ref",
        "kind",
        "expected_metric_ref",
        "expected_direction",
        "analysis_refs",
        "comparability",
        "result",
        "limitations",
    }:
        return None
    analysis_refs = parameters.get("analysis_refs")
    limitations = parameters.get("limitations")
    if (
        not _safe_ref_value(parameters.get("item_ref"), prefix="plan-item")
        or not _safe_ref_value(parameters.get("expected_metric_ref"), prefix="metric")
        or parameters.get("expected_direction") not in _EXPECTED_DIRECTIONS
        or not isinstance(analysis_refs, list)
        or not 1 <= len(analysis_refs) <= 16
        or not all(_safe_ref_value(ref, prefix="analysis") for ref in analysis_refs)
        or not isinstance(limitations, list)
        or not 1 <= len(limitations) <= 16
        or not all(
            isinstance(item, str)
            and 0 < len(item) <= 160
            and _UNSAFE_DISPLAY_TEXT.search(item) is None
            for item in limitations
        )
    ):
        return None
    kind = _RETEST_KIND_LABELS.get(parameters.get("kind"))
    comparability = _COMPARABILITY_LABELS.get(parameters.get("comparability"))
    if kind is None or comparability is None:
        return None
    policy_missing = "metric_change_policy_missing" in limitations
    if parameters.get("comparability") == "comparable" and policy_missing:
        outcome_text = "数字有变化，但现在还看不出是不是稳定变化"
        effect_text = "确认后只记录这次复测，暂不调整当前训练项"
    elif parameters.get("comparability") == "comparable":
        outcome = _RETEST_OUTCOME_LABELS.get(parameters.get("result"))
        if outcome is None:
            return None
        outcome_text, effect_text = outcome
    else:
        outcome_text = "本次结果暂不用于判断"
        effect_text = "确认后暂不调整当前训练项"
    limitation_labels = [
        _LIMITATION_LABELS[item]
        for item in limitations
        if item in _LIMITATION_LABELS
    ]
    limitation_text = (
        f"；限制是{'、'.join(limitation_labels)}"
        if limitation_labels
        else ""
    )
    return _bounded_impact(
        "training_retest_will_be_recorded",
        f"将记录一次{kind}：{comparability}，{outcome_text}；"
        f"{effect_text}{limitation_text}。",
    )


def _project_product_confirmation_impact(
    command_name: object,
    parameters_json: object,
) -> tuple[str, str]:
    fallback = _ACTION_IMPACTS["coach_side_effect"]
    if not isinstance(command_name, str) or not isinstance(parameters_json, str):
        return fallback
    try:
        parameters = json.loads(parameters_json)
    except (TypeError, json.JSONDecodeError):
        return fallback
    if not isinstance(parameters, dict):
        return fallback
    projector = {
        "training_plan.item.add": _project_item_impact,
        "training_plan.execution.record": _project_execution_impact,
        "training_plan.retest.record": _project_retest_impact,
    }.get(command_name)
    if projector is None:
        return fallback
    return projector(parameters) or fallback


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
                "SELECT confirmation_ref, command_name, parameters_json "
                "FROM coach_command_confirmations "
                "WHERE confirmation_ref=? AND owner_id=? AND user_message_ref=?",
                (confirmation_ref, owner_id, user_message_ref),
            )
        ).fetchone()
        if canonical is None or canonical["command_name"] != row["command_name"]:
            continue
        target_ref = confirmation.get("target_ref")
        if not isinstance(target_ref, str) or _SAFE_REF.fullmatch(target_ref) is None:
            target_ref = result.get("command_id")
        if not isinstance(target_ref, str) or _SAFE_REF.fullmatch(target_ref) is None:
            continue
        impact = _project_product_confirmation_impact(
            canonical["command_name"],
            canonical["parameters_json"],
        )
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
            "command_name": canonical["command_name"],
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
