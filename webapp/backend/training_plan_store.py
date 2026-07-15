from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .db import get_conn


class TrainingPlanError(Exception):
    """Base exception for owner-scoped Training Plan operations."""


class PlanNotFound(TrainingPlanError):
    pass


class PlanForbidden(TrainingPlanError):
    pass


class InvalidTrainingPlan(TrainingPlanError):
    pass


class InvalidTransition(TrainingPlanError):
    pass


class ActivePlanReplacementRequired(TrainingPlanError):
    def __init__(self, active_plan_id: str, target_plan_id: str) -> None:
        self.active_plan_id = active_plan_id
        self.target_plan_id = target_plan_id
        super().__init__(
            f"activating {target_plan_id} requires replacing active plan {active_plan_id}"
        )


_WRITE_LOCK = asyncio.Lock()
_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_RE = re.compile(r"(?:^/|^[A-Za-z]:[\\/]|^\\\\)")
_FORBIDDEN_KEY_RE = re.compile(
    r"(?:credential|secret|password|token|api[_-]?key|authorization|private[_-]?key|"
    r"raw[_-]?trace|(?:^|_)trace(?:_|$)|path)",
    re.IGNORECASE,
)
_MAX_PAYLOAD_BYTES = 16_384
_MAX_TEXT_LENGTH = 500
_DIAGNOSTIC_REF_PREFIXES = {
    "analysis_refs": "analysis",
    "metric_refs": "metric",
    "diagnosis_refs": "diagnosis",
    "prescription_refs": "prescription",
    "knowledge_refs": "knowledge",
    "evidence_refs": "evidence",
}
_VERSION_EVIDENCE_REF_PREFIXES = frozenset({"analysis", "metric", "knowledge"})


def _required_text(value: Any, field: str, *, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTrainingPlan(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > max_length:
        raise InvalidTrainingPlan(f"{field} exceeds {max_length} characters")
    if _PATH_RE.search(text):
        raise InvalidTrainingPlan(f"{field} must not contain an absolute path")
    if text.lower().startswith(("sk-", "bearer ")):
        raise InvalidTrainingPlan(f"{field} must not contain credential-like text")
    return text


def _required_owner(owner_id: str) -> str:
    return _required_text(owner_id, "owner_id", max_length=128)


def _required_plan_id(plan_id: str) -> str:
    value = _required_text(plan_id, "plan_id", max_length=160)
    if not value.startswith("plan:"):
        raise PlanNotFound(plan_id)
    return value


def _validate_ref(value: Any, field: str, *, required_prefix: str | None = None) -> str:
    ref = _required_text(value, field, max_length=160)
    if not _REF_RE.fullmatch(ref):
        raise InvalidTrainingPlan(f"{field} must be a stable product reference")
    if required_prefix is not None and not ref.startswith(f"{required_prefix}:"):
        raise InvalidTrainingPlan(f"{field} must be a {required_prefix}: reference")
    return ref


def _validate_refs(
    value: Any,
    field: str,
    *,
    required_prefix: str | None = None,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidTrainingPlan(f"{field} must be a list of stable references")
    if not value or len(value) > 32:
        raise InvalidTrainingPlan(f"{field} must contain 1 to 32 references")
    return [
        _validate_ref(item, field, required_prefix=required_prefix)
        for item in value
    ]


def _validate_version_evidence_refs(value: Any, field: str) -> list[str]:
    refs = _validate_refs(value, field)
    if any(ref.split(":", 1)[0] not in _VERSION_EVIDENCE_REF_PREFIXES for ref in refs):
        raise InvalidTrainingPlan(
            f"{field} must contain only analysis, metric, or knowledge references"
        )
    return refs


def _reject_forbidden_keys(value: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise InvalidTrainingPlan("plan payload exceeds the safe nesting limit")
    if isinstance(value, Mapping):
        if len(value) > 24:
            raise InvalidTrainingPlan("plan payload has too many fields")
        for key, nested in value.items():
            if not isinstance(key, str):
                raise InvalidTrainingPlan("plan payload keys must be strings")
            if _FORBIDDEN_KEY_RE.search(key):
                raise InvalidTrainingPlan(f"unsafe field is not allowed: {key}")
            _reject_forbidden_keys(nested, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > 32:
            raise InvalidTrainingPlan("plan payload has too many list values")
        for nested in value:
            _reject_forbidden_keys(nested, depth=depth + 1)
    elif isinstance(value, str):
        _required_text(value, "plan payload text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise InvalidTrainingPlan("plan payload contains an unsupported value")


def _validate_diagnostic_context(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise InvalidTrainingPlan("diagnostic_context must be an object")
    allowed = set(_DIAGNOSTIC_REF_PREFIXES)
    if not value or set(value) - allowed:
        raise InvalidTrainingPlan("diagnostic_context contains unsupported fields")
    normalized: dict[str, list[str]] = {}
    for key, refs in value.items():
        normalized[key] = _validate_refs(
            refs,
            f"diagnostic_context.{key}",
            required_prefix=_DIAGNOSTIC_REF_PREFIXES[key],
        )
    if not normalized:
        raise InvalidTrainingPlan("diagnostic_context must include canonical references")
    return normalized


def _validate_prescriptions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidTrainingPlan("prescriptions must be a list")
    if not value or len(value) > 16:
        raise InvalidTrainingPlan("prescriptions must contain 1 to 16 entries")
    allowed = {
        "scenario",
        "cue",
        "purpose",
        "dosage",
        "target_metric_refs",
        "expected_direction",
        "retest_after",
        "stop_or_adjust_rule",
        "source_level",
    }
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) - allowed:
            raise InvalidTrainingPlan("prescription contains unsupported fields")
        required = ("scenario", "cue", "purpose", "source_level")
        if any(field not in item for field in required):
            raise InvalidTrainingPlan("prescription requires scenario, cue, purpose, and source_level")
        entry: dict[str, Any] = {
            field: _required_text(item[field], f"prescriptions[{index}].{field}")
            for field in required
        }
        for field in ("dosage", "expected_direction", "retest_after", "stop_or_adjust_rule"):
            if field in item:
                entry[field] = _required_text(item[field], f"prescriptions[{index}].{field}")
        if "target_metric_refs" in item:
            entry["target_metric_refs"] = [
                _validate_ref(ref, "prescription target metric", required_prefix="metric")
                for ref in _validate_refs(item["target_metric_refs"], "target_metric_refs")
            ]
        normalized.append(entry)
    return normalized


def _validate_plan_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidTrainingPlan("plan_payload must be an object")
    _reject_forbidden_keys(value)
    allowed = {"title", "diagnostic_context", "prescriptions"}
    if set(value) - allowed or "diagnostic_context" not in value or "prescriptions" not in value:
        raise InvalidTrainingPlan(
            "plan_payload requires diagnostic_context and prescriptions only"
        )
    normalized: dict[str, Any] = {
        "diagnostic_context": _validate_diagnostic_context(value["diagnostic_context"]),
        "prescriptions": _validate_prescriptions(value["prescriptions"]),
    }
    if "title" in value:
        normalized["title"] = _required_text(value["title"], "title")
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise InvalidTrainingPlan("plan_payload exceeds the safe size limit")
    return normalized


def _validate_verification_targets(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidTrainingPlan("verification_targets must be a list")
    if not value or len(value) > 16:
        raise InvalidTrainingPlan("verification_targets must contain 1 to 16 entries")
    allowed = {
        "target_metric",
        "expected_direction",
        "comparable_requirements",
        "retest_after",
        "insufficient_evidence_behavior",
    }
    normalized: list[dict[str, Any]] = []
    for index, target in enumerate(value):
        if not isinstance(target, Mapping) or set(target) - allowed:
            raise InvalidTrainingPlan("verification target contains unsupported fields")
        required = {
            "target_metric",
            "expected_direction",
            "comparable_requirements",
            "insufficient_evidence_behavior",
        }
        if required - set(target):
            raise InvalidTrainingPlan("verification target is missing a required field")
        comparable = target["comparable_requirements"]
        if isinstance(comparable, (str, bytes)) or not isinstance(comparable, Sequence):
            raise InvalidTrainingPlan("comparable_requirements must be a list")
        if not comparable or len(comparable) > 12:
            raise InvalidTrainingPlan("comparable_requirements must contain 1 to 12 entries")
        entry: dict[str, Any] = {
            "target_metric": _validate_ref(
                target["target_metric"],
                f"verification_targets[{index}].target_metric",
                required_prefix="metric",
            ),
            "expected_direction": _required_text(
                target["expected_direction"],
                f"verification_targets[{index}].expected_direction",
            ),
            "comparable_requirements": [
                _required_text(item, f"verification_targets[{index}].comparable_requirements")
                for item in comparable
            ],
            "insufficient_evidence_behavior": _required_text(
                target["insufficient_evidence_behavior"],
                f"verification_targets[{index}].insufficient_evidence_behavior",
            ),
        }
        if "retest_after" in target:
            entry["retest_after"] = _required_text(
                target["retest_after"], f"verification_targets[{index}].retest_after"
            )
        normalized.append(entry)
    return normalized


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode(value: str) -> Any:
    return json.loads(value)


def _plan_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    version = int(row["current_version"])
    return {
        "plan_id": row["plan_id"],
        "plan_ref": row["plan_id"],
        "status": row["status"],
        "version": version,
        "version_ref": f"{row['plan_id']}:v{version}",
        "plan_payload": _decode(row["plan_payload_json"]),
        "adjustment_reason": row["adjustment_reason"],
        "evidence_refs": _decode(row["evidence_refs_json"]),
        "verification_targets": _decode(row["verification_targets_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _select_plan_row(conn: Any, owner_id: str, plan_id: str) -> Any:
    cur = await conn.execute(
        "SELECT p.plan_id, p.owner_id, p.status, p.current_version, p.created_at, p.updated_at, "
        "v.plan_payload_json, v.adjustment_reason, v.evidence_refs_json, "
        "v.verification_targets_json "
        "FROM training_plans p JOIN training_plan_versions v "
        "ON v.plan_id=p.plan_id AND v.version=p.current_version "
        "WHERE p.owner_id=? AND p.plan_id=?",
        (owner_id, plan_id),
    )
    row = await cur.fetchone()
    if row is not None:
        return row
    cur = await conn.execute("SELECT 1 FROM training_plans WHERE plan_id=?", (plan_id,))
    if await cur.fetchone() is not None:
        raise PlanForbidden(plan_id)
    raise PlanNotFound(plan_id)


async def _select_version_row(conn: Any, owner_id: str, plan_id: str, version: int) -> Any:
    cur = await conn.execute(
        "SELECT p.plan_id, p.owner_id, p.status, p.current_version, p.created_at, p.updated_at, "
        "v.version, v.plan_payload_json, v.adjustment_reason, v.evidence_refs_json, "
        "v.verification_targets_json, v.created_at AS version_created_at "
        "FROM training_plans p JOIN training_plan_versions v ON v.plan_id=p.plan_id "
        "WHERE p.owner_id=? AND p.plan_id=? AND v.version=?",
        (owner_id, plan_id, version),
    )
    row = await cur.fetchone()
    if row is not None:
        return row
    await _select_plan_row(conn, owner_id, plan_id)
    raise PlanNotFound(f"{plan_id}:v{version}")


async def _append_transition(
    conn: Any,
    *,
    owner_id: str,
    plan_id: str,
    version: int,
    event: str,
    from_status: str | None,
    to_status: str,
    reason: str | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO training_plan_transitions("
        "owner_id, plan_id, version, event, from_status, to_status, reason) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (owner_id, plan_id, version, event, from_status, to_status, reason),
    )


async def create_draft(
    owner_id: str,
    plan_payload: Mapping[str, Any],
    *,
    evidence_refs: Sequence[str] = (),
    verification_targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    payload = _validate_plan_payload(plan_payload)
    refs = (
        []
        if not evidence_refs
        else _validate_version_evidence_refs(evidence_refs, "evidence_refs")
    )
    targets = _validate_verification_targets(verification_targets)
    plan_id = f"plan:{uuid.uuid4().hex}"

    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute(
                "INSERT INTO training_plans(plan_id, owner_id, status, current_version) "
                "VALUES(?, ?, 'draft', 1)",
                (plan_id, owner_id),
            )
            await conn.execute(
                "INSERT INTO training_plan_versions("
                "plan_id, version, plan_payload_json, adjustment_reason, evidence_refs_json, "
                "verification_targets_json) VALUES(?, 1, ?, NULL, ?, ?)",
                (plan_id, _json(payload), _json(refs), _json(targets)),
            )
            await _append_transition(
                conn,
                owner_id=owner_id,
                plan_id=plan_id,
                version=1,
                event="generated",
                from_status=None,
                to_status="draft",
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_plan(owner_id, plan_id)


generate_draft = create_draft


async def get_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    conn = await get_conn()
    return _plan_from_row(await _select_plan_row(conn, owner_id, plan_id))


async def get_plan_version(owner_id: str, plan_id: str, version: int) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    if not isinstance(version, int) or version < 1:
        raise PlanNotFound(f"{plan_id}:v{version}")
    conn = await get_conn()
    row = await _select_version_row(conn, owner_id, plan_id, version)
    return {
        "plan_id": row["plan_id"],
        "plan_ref": row["plan_id"],
        "status": row["status"],
        "version": int(row["version"]),
        "version_ref": f"{row['plan_id']}:v{row['version']}",
        "plan_payload": _decode(row["plan_payload_json"]),
        "adjustment_reason": row["adjustment_reason"],
        "evidence_refs": _decode(row["evidence_refs_json"]),
        "verification_targets": _decode(row["verification_targets_json"]),
        "created_at": row["version_created_at"],
    }


async def list_plans(owner_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    if status is not None and status not in {"draft", "saved", "active", "paused"}:
        raise InvalidTrainingPlan("unknown Training Plan status")
    conn = await get_conn()
    where = "WHERE p.owner_id=?"
    params: tuple[Any, ...] = (owner_id,)
    if status is not None:
        where += " AND p.status=?"
        params += (status,)
    cur = await conn.execute(
        "SELECT p.plan_id, p.owner_id, p.status, p.current_version, p.created_at, p.updated_at, "
        "v.plan_payload_json, v.adjustment_reason, v.evidence_refs_json, "
        "v.verification_targets_json "
        "FROM training_plans p JOIN training_plan_versions v "
        "ON v.plan_id=p.plan_id AND v.version=p.current_version "
        f"{where} ORDER BY p.updated_at DESC, p.plan_id DESC",
        params,
    )
    return [_plan_from_row(row) for row in await cur.fetchall()]


async def save_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    return await _transition(owner_id, plan_id, expected_status="draft", to_status="saved", event="saved")


async def activate_plan(
    owner_id: str, plan_id: str, *, replace_active: bool = False,
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            target = await _select_plan_row(conn, owner_id, plan_id)
            from_status = target["status"]
            if from_status not in {"saved", "paused"}:
                raise InvalidTransition(f"cannot activate a {from_status} plan")
            cur = await conn.execute(
                "SELECT plan_id, current_version FROM training_plans "
                "WHERE owner_id=? AND status='active' AND plan_id<>?",
                (owner_id, plan_id),
            )
            active = await cur.fetchone()
            if active is not None and not replace_active:
                raise ActivePlanReplacementRequired(active["plan_id"], plan_id)
            if active is not None:
                await conn.execute(
                    "UPDATE training_plans SET status='paused', updated_at=CURRENT_TIMESTAMP "
                    "WHERE owner_id=? AND plan_id=? AND status='active'",
                    (owner_id, active["plan_id"]),
                )
                await _append_transition(
                    conn,
                    owner_id=owner_id,
                    plan_id=active["plan_id"],
                    version=int(active["current_version"]),
                    event="paused",
                    from_status="active",
                    to_status="paused",
                    reason=f"replaced_by:{plan_id}",
                )
            await conn.execute(
                "UPDATE training_plans SET status='active', updated_at=CURRENT_TIMESTAMP "
                "WHERE owner_id=? AND plan_id=? AND status=?",
                (owner_id, plan_id, from_status),
            )
            await _append_transition(
                conn,
                owner_id=owner_id,
                plan_id=plan_id,
                version=int(target["current_version"]),
                event="activated",
                from_status=from_status,
                to_status="active",
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_plan(owner_id, plan_id)


async def pause_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    return await _transition(owner_id, plan_id, expected_status="active", to_status="paused", event="paused")


async def _transition(
    owner_id: str,
    plan_id: str,
    *,
    expected_status: str,
    to_status: str,
    event: str,
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _select_plan_row(conn, owner_id, plan_id)
            if row["status"] != expected_status:
                raise InvalidTransition(
                    f"cannot {event} a {row['status']} plan; expected {expected_status}"
                )
            await conn.execute(
                "UPDATE training_plans SET status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE owner_id=? AND plan_id=? AND status=?",
                (to_status, owner_id, plan_id, expected_status),
            )
            await _append_transition(
                conn,
                owner_id=owner_id,
                plan_id=plan_id,
                version=int(row["current_version"]),
                event=event,
                from_status=expected_status,
                to_status=to_status,
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_plan(owner_id, plan_id)


async def adjust_plan(
    owner_id: str,
    plan_id: str,
    plan_payload: Mapping[str, Any],
    *,
    adjustment_reason: str,
    evidence_refs: Sequence[str],
    verification_targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    payload = _validate_plan_payload(plan_payload)
    reason = _required_text(adjustment_reason, "adjustment_reason")
    refs = _validate_version_evidence_refs(evidence_refs, "evidence_refs")
    targets = _validate_verification_targets(verification_targets)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _select_plan_row(conn, owner_id, plan_id)
            if row["status"] == "draft":
                raise InvalidTransition("cannot adjust a draft plan before it is saved")
            next_version = int(row["current_version"]) + 1
            await conn.execute(
                "INSERT INTO training_plan_versions("
                "plan_id, version, plan_payload_json, adjustment_reason, evidence_refs_json, "
                "verification_targets_json) VALUES(?, ?, ?, ?, ?, ?)",
                (plan_id, next_version, _json(payload), reason, _json(refs), _json(targets)),
            )
            await conn.execute(
                "UPDATE training_plans SET current_version=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE owner_id=? AND plan_id=?",
                (next_version, owner_id, plan_id),
            )
            await _append_transition(
                conn,
                owner_id=owner_id,
                plan_id=plan_id,
                version=next_version,
                event="adjusted",
                from_status=row["status"],
                to_status=row["status"],
                reason=reason,
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_plan(owner_id, plan_id)


async def review_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    """Read the current immutable version without changing lifecycle state."""
    return await get_plan(owner_id, plan_id)


async def list_transitions(owner_id: str, plan_id: str) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    conn = await get_conn()
    await _select_plan_row(conn, owner_id, plan_id)
    cur = await conn.execute(
        "SELECT id, plan_id, version, event, from_status, to_status, reason, created_at "
        "FROM training_plan_transitions WHERE owner_id=? AND plan_id=? ORDER BY id",
        (owner_id, plan_id),
    )
    return [
        {
            "id": int(row["id"]),
            "plan_id": row["plan_id"],
            "version": int(row["version"]),
            "event": row["event"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }
        for row in await cur.fetchall()
    ]
