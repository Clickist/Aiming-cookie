"""Owner-scoped Training Plan persistence (JSON file backed)."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from . import file_store


class TrainingPlanError(Exception):
    pass


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
_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
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

_PLAN_PATH = "training/plan.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


_LEARNER_RESPONSE_FIELDS = {
    "cue_clarity", "felt_control", "felt_stiffness", "fatigue_or_discomfort",
    "willing_to_continue", "notes",
}


def _normalize_user_feedback(value: Any) -> str:
    if isinstance(value, Mapping):
        if set(value) - _LEARNER_RESPONSE_FIELDS:
            raise InvalidTrainingPlan("user_feedback contains unsupported learner response fields")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "notes":
                normalized[key] = _required_text(item, "user_feedback.notes", max_length=1000)
            elif not isinstance(item, (str, bool)) and item is not None:
                raise InvalidTrainingPlan(f"user_feedback.{key} is invalid")
            elif isinstance(item, str) and len(item) > 120:
                raise InvalidTrainingPlan(f"user_feedback.{key} is invalid")
            else:
                normalized[key] = item
        return json.dumps(normalized, ensure_ascii=False)
    return _required_text(value, "user_feedback", max_length=1000)


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
        "scenario", "cue", "purpose", "dosage", "target_metric_refs",
        "expected_direction", "retest_after", "stop_or_adjust_rule", "source_level",
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
        "target_metric", "expected_direction", "comparable_requirements",
        "retest_after", "insufficient_evidence_behavior",
    }
    normalized: list[dict[str, Any]] = []
    for index, target in enumerate(value):
        if not isinstance(target, Mapping) or set(target) - allowed:
            raise InvalidTrainingPlan("verification target contains unsupported fields")
        required = {
            "target_metric", "expected_direction", "comparable_requirements",
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


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


# ---- File-backed data model ----

def _load_doc() -> dict[str, Any]:
    data = file_store.read_json(_PLAN_PATH)
    if data is None:
        return {"plans": {}, "transitions": [], "items": {}, "executions": [], "retests": []}
    data.setdefault("plans", {})
    data.setdefault("transitions", [])
    data.setdefault("items", {})
    data.setdefault("executions", [])
    data.setdefault("retests", [])
    return data


def _save_doc(doc: dict[str, Any]) -> None:
    file_store.write_json(_PLAN_PATH, doc)


def _plan_dict(plan_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    version = int(plan.get("current_version", 1))
    versions = plan.get("versions", {})
    current_version = versions.get(str(version)) or versions.get(version) or {}
    return {
        "plan_id": plan_id,
        "plan_ref": plan_id,
        "status": plan.get("status", "draft"),
        "version": version,
        "version_ref": f"{plan_id}:v{version}",
        "plan_payload": current_version.get("plan_payload", {}),
        "adjustment_reason": current_version.get("adjustment_reason"),
        "evidence_refs": current_version.get("evidence_refs", []),
        "verification_targets": current_version.get("verification_targets", []),
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
    }


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
    async with _WRITE_LOCK:
        doc = _load_doc()
        now = _utc_now()
        doc["plans"][plan_id] = {
            "owner_id": owner_id,
            "status": "draft",
            "current_version": 1,
            "versions": {
                "1": {
                    "plan_payload": payload,
                    "adjustment_reason": None,
                    "evidence_refs": refs,
                    "verification_targets": targets,
                    "created_at": now,
                }
            },
            "created_at": now,
            "updated_at": now,
        }
        doc["transitions"].append({
            "owner_id": owner_id,
            "plan_id": plan_id,
            "version": 1,
            "event": "generated",
            "from_status": None,
            "to_status": "draft",
            "reason": None,
            "created_at": now,
        })
        _save_doc(doc)
    return await get_plan(owner_id, plan_id)


generate_draft = create_draft


async def get_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    doc = _load_doc()
    plan = doc["plans"].get(plan_id)
    if plan is None:
        raise PlanNotFound(plan_id)
    if plan.get("owner_id") != owner_id:
        raise PlanForbidden(plan_id)
    return _plan_dict(plan_id, plan)


async def get_plan_version(owner_id: str, plan_id: str, version: int) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    if not isinstance(version, int) or version < 1:
        raise PlanNotFound(f"{plan_id}:v{version}")
    doc = _load_doc()
    plan = doc["plans"].get(plan_id)
    if plan is None:
        raise PlanNotFound(plan_id)
    if plan.get("owner_id") != owner_id:
        raise PlanForbidden(plan_id)
    version_data = plan.get("versions", {}).get(str(version))
    if version_data is None:
        raise PlanNotFound(f"{plan_id}:v{version}")
    return {
        "plan_id": plan_id,
        "plan_ref": plan_id,
        "status": plan.get("status"),
        "version": version,
        "version_ref": f"{plan_id}:v{version}",
        "plan_payload": version_data.get("plan_payload", {}),
        "adjustment_reason": version_data.get("adjustment_reason"),
        "evidence_refs": version_data.get("evidence_refs", []),
        "verification_targets": version_data.get("verification_targets", []),
        "created_at": version_data.get("created_at"),
    }


async def list_plans(owner_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    if status is not None and status not in {"draft", "saved", "active", "paused"}:
        raise InvalidTrainingPlan("unknown Training Plan status")
    doc = _load_doc()
    result = []
    for plan_id, plan in doc["plans"].items():
        if plan.get("owner_id") != owner_id:
            continue
        if status is not None and plan.get("status") != status:
            continue
        result.append(_plan_dict(plan_id, plan))
    result.sort(key=lambda p: (p.get("updated_at", ""), p.get("plan_id", "")), reverse=True)
    return result


async def save_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    return await _transition(owner_id, plan_id, expected_status="draft", to_status="saved", event="saved")


async def activate_plan(
    owner_id: str, plan_id: str, *, replace_active: bool = False,
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    async with _WRITE_LOCK:
        doc = _load_doc()
        plan = doc["plans"].get(plan_id)
        if plan is None:
            raise PlanNotFound(plan_id)
        if plan.get("owner_id") != owner_id:
            raise PlanForbidden(plan_id)
        from_status = plan.get("status")
        if from_status not in {"saved", "paused"}:
            raise InvalidTransition(f"cannot activate a {from_status} plan")
        active_plan_id = None
        for pid, p in doc["plans"].items():
            if p.get("owner_id") == owner_id and p.get("status") == "active" and pid != plan_id:
                active_plan_id = pid
                break
        if active_plan_id is not None and not replace_active:
            raise ActivePlanReplacementRequired(active_plan_id, plan_id)
        now = _utc_now()
        if active_plan_id is not None:
            doc["plans"][active_plan_id]["status"] = "paused"
            doc["plans"][active_plan_id]["updated_at"] = now
            doc["transitions"].append({
                "owner_id": owner_id, "plan_id": active_plan_id,
                "version": int(doc["plans"][active_plan_id].get("current_version", 1)),
                "event": "paused", "from_status": "active", "to_status": "paused",
                "reason": f"replaced_by:{plan_id}", "created_at": now,
            })
        plan["status"] = "active"
        plan["updated_at"] = now
        doc["transitions"].append({
            "owner_id": owner_id, "plan_id": plan_id,
            "version": int(plan.get("current_version", 1)),
            "event": "activated", "from_status": from_status, "to_status": "active",
            "reason": None, "created_at": now,
        })
        _save_doc(doc)
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
    async with _WRITE_LOCK:
        doc = _load_doc()
        plan = doc["plans"].get(plan_id)
        if plan is None:
            raise PlanNotFound(plan_id)
        if plan.get("owner_id") != owner_id:
            raise PlanForbidden(plan_id)
        if plan.get("status") != expected_status:
            raise InvalidTransition(
                f"cannot {event} a {plan['status']} plan; expected {expected_status}"
            )
        now = _utc_now()
        plan["status"] = to_status
        plan["updated_at"] = now
        doc["transitions"].append({
            "owner_id": owner_id, "plan_id": plan_id,
            "version": int(plan.get("current_version", 1)),
            "event": event, "from_status": expected_status, "to_status": to_status,
            "reason": None, "created_at": now,
        })
        _save_doc(doc)
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
    async with _WRITE_LOCK:
        doc = _load_doc()
        plan = doc["plans"].get(plan_id)
        if plan is None:
            raise PlanNotFound(plan_id)
        if plan.get("owner_id") != owner_id:
            raise PlanForbidden(plan_id)
        if plan.get("status") == "draft":
            raise InvalidTransition("cannot adjust a draft plan before it is saved")
        next_version = int(plan.get("current_version", 1)) + 1
        now = _utc_now()
        plan.setdefault("versions", {})[str(next_version)] = {
            "plan_payload": payload,
            "adjustment_reason": reason,
            "evidence_refs": refs,
            "verification_targets": targets,
            "created_at": now,
        }
        plan["current_version"] = next_version
        plan["updated_at"] = now
        doc["transitions"].append({
            "owner_id": owner_id, "plan_id": plan_id,
            "version": next_version, "event": "adjusted",
            "from_status": plan["status"], "to_status": plan["status"],
            "reason": reason, "created_at": now,
        })
        _save_doc(doc)
    return await get_plan(owner_id, plan_id)


async def review_plan(owner_id: str, plan_id: str) -> dict[str, Any]:
    return await get_plan(owner_id, plan_id)


async def list_transitions(owner_id: str, plan_id: str) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    doc = _load_doc()
    if plan_id not in doc["plans"]:
        raise PlanNotFound(plan_id)
    result = []
    for i, t in enumerate(doc.get("transitions", [])):
        if t.get("owner_id") == owner_id and t.get("plan_id") == plan_id:
            result.append({
                "id": i + 1,
                "plan_id": plan_id,
                "version": int(t.get("version", 1)),
                "event": t.get("event"),
                "from_status": t.get("from_status"),
                "to_status": t.get("to_status"),
                "reason": t.get("reason"),
                "created_at": t.get("created_at"),
            })
    return result


# ---- Plan items (simplified for file-backed storage) ----

_ITEM_STATUSES = {"planned", "active", "completed", "cancelled"}
_EXECUTION_STATUSES = {"completed", "partial", "skipped"}
_RETEST_KINDS = {"matched", "near_transfer"}
_RETEST_COMPARABILITY = {"comparable", "not_comparable", "unavailable"}


def _validate_plan_item(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise InvalidTrainingPlan("plan item must be an object")
    required = {
        "diagnosis_ref", "knowledge_ref", "scenario_profile_ref", "baseline_metric_ref",
        "expected_direction", "practice_condition", "cue", "dose_guardrail",
        "matched_retest_ref", "near_transfer_retest_ref", "review_date",
    }
    if set(value) != required:
        raise InvalidTrainingPlan("plan item requires the complete prescription contract")
    refs = {
        "diagnosis_ref": "diagnosis",
        "knowledge_ref": "knowledge",
        "scenario_profile_ref": "scenario",
        "baseline_metric_ref": "metric",
        "matched_retest_ref": "retest-spec",
        "near_transfer_retest_ref": "retest-spec",
    }
    normalized = {
        field: _validate_ref(value[field], field, required_prefix=prefix)
        for field, prefix in refs.items()
    }
    direction = _required_text(value["expected_direction"], "expected_direction", max_length=64)
    if direction not in {"lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only"}:
        raise InvalidTrainingPlan("expected_direction is invalid")
    for field in ("practice_condition", "cue", "dose_guardrail", "review_date"):
        normalized[field] = _required_text(value[field], field)
    normalized["expected_direction"] = direction
    return normalized


def _validate_dose(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"amount", "unit"}:
        raise InvalidTrainingPlan(f"{field} must contain amount and unit")
    amount = value["amount"]
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        raise InvalidTrainingPlan(f"{field}.amount is invalid")
    return {"amount": float(amount), "unit": _required_text(value["unit"], f"{field}.unit", max_length=40)}


def _validate_ref_list(value: Sequence[str], field: str, prefix: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value or len(value) > 16:
        raise InvalidTrainingPlan(f"{field} must contain 1 to 16 references")
    return [_validate_ref(ref, field, required_prefix=prefix) for ref in value]


async def add_plan_item(
    owner_id: str,
    plan_id: str,
    item_payload: Mapping[str, Any],
    *,
    plan_version: int | None = None,
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    payload = _validate_plan_item(item_payload)
    async with _WRITE_LOCK:
        doc = _load_doc()
        plan = doc["plans"].get(plan_id)
        if plan is None:
            raise PlanNotFound(plan_id)
        if plan.get("owner_id") != owner_id:
            raise PlanForbidden(plan_id)
        version = int(plan.get("current_version", 1)) if plan_version is None else plan_version
        if not isinstance(version, int) or version < 1:
            raise InvalidTrainingPlan("plan_version is invalid")
        item_ref = f"plan-item:{uuid.uuid4().hex}"
        now = _utc_now()
        doc["items"][item_ref] = {
            "owner_id": owner_id,
            "plan_id": plan_id,
            "plan_version": version,
            "item_revision": 1,
            "status": "planned",
            "item_payload": payload,
            "created_at": now,
            "updated_at": now,
        }
        _save_doc(doc)
    return _item_projection(doc["items"][item_ref], item_ref)


async def list_plan_items(owner_id: str, plan_id: str) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    plan_id = _required_plan_id(plan_id)
    doc = _load_doc()
    if plan_id not in doc["plans"]:
        raise PlanNotFound(plan_id)
    if doc["plans"][plan_id].get("owner_id") != owner_id:
        raise PlanForbidden(plan_id)
    result = []
    for item_ref, item in sorted(doc.get("items", {}).items()):
        if item.get("owner_id") == owner_id and item.get("plan_id") == plan_id:
            result.append(_item_projection(item, item_ref))
    return result


async def set_plan_item_status(
    owner_id: str, item_ref: str, status: str, *, reason: str | None = None,
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    item_ref = _validate_ref(item_ref, "item_ref", required_prefix="plan-item")
    if status not in _ITEM_STATUSES:
        raise InvalidTrainingPlan("unknown plan item status")
    if reason is not None:
        reason = _required_text(reason, "reason")
    changed = False
    async with _WRITE_LOCK:
        doc = _load_doc()
        item = doc["items"].get(item_ref)
        if item is None:
            raise PlanNotFound(item_ref)
        if item.get("owner_id") != owner_id:
            raise PlanForbidden(item_ref)
        if item["status"] != status:
            item["status"] = status
            item["status_ref"] = f"plan-item-status:{uuid.uuid4().hex}"
            item["updated_at"] = _utc_now()
            _save_doc(doc)
            changed = True
    projection = _item_projection(doc["items"][item_ref], item_ref)
    if not changed:
        projection["status_ref"] = None
    return projection


def _item_projection(item: dict[str, Any], item_ref: str) -> dict[str, Any]:
    item_revision = int(item.get("item_revision", 1))
    return {
        "item_ref": item_ref,
        "plan_id": item.get("plan_id"),
        "plan_revision": int(item.get("plan_version", 1)),
        "plan_revision_ref": f"{item.get('plan_id', '')}:v{item.get('plan_version', 1)}",
        "item_revision": item_revision,
        "item_revision_ref": f"{item_ref}:v{item_revision}",
        "status": item.get("status"),
        "status_ref": item.get("status_ref"),
        **item.get("item_payload", {}),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


async def record_user_execution(
    owner_id: str,
    item_ref: str,
    *,
    scenario_ref: str,
    run_refs: Sequence[str],
    planned_dose: Mapping[str, Any],
    completed_dose: Mapping[str, Any],
    completion_status: str,
    user_feedback: str | Mapping[str, Any],
    recorded_by: str = "user",
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    item_ref = _validate_ref(item_ref, "item_ref", required_prefix="plan-item")
    scenario_ref = _validate_ref(scenario_ref, "scenario_ref", required_prefix="scenario")
    refs = _validate_ref_list(run_refs, "run_refs", "run")
    planned = _validate_dose(planned_dose, "planned_dose")
    completed = _validate_dose(completed_dose, "completed_dose")
    if completion_status not in _EXECUTION_STATUSES:
        raise InvalidTrainingPlan("unknown execution status")
    if recorded_by != "user":
        raise InvalidTrainingPlan("plan execution must be recorded by the user")
    feedback = _normalize_user_feedback(user_feedback)
    async with _WRITE_LOCK:
        doc = _load_doc()
        item = doc["items"].get(item_ref)
        if item is None:
            raise PlanNotFound(item_ref)
        if item.get("owner_id") != owner_id:
            raise PlanForbidden(item_ref)
        execution_ref = f"plan-execution:{uuid.uuid4().hex}"
        doc["executions"].append({
            "execution_ref": execution_ref,
            "owner_id": owner_id,
            "item_ref": item_ref,
            "plan_id": item.get("plan_id"),
            "plan_version": item.get("plan_version"),
            "item_revision": item.get("item_revision"),
            "scenario_ref": scenario_ref,
            "run_refs": refs,
            "planned_dose": planned,
            "completed_dose": completed,
            "completion_status": completion_status,
            "user_feedback": feedback,
        })
        _save_doc(doc)
    return {
        "execution_ref": execution_ref, "owner_id": owner_id, "item_ref": item_ref,
        "plan_revision_ref": f"{item.get('plan_id', '')}:v{item.get('plan_version', 1)}",
        "item_revision_ref": f"{item_ref}:v{item.get('item_revision', 1)}",
        "scenario_ref": scenario_ref, "run_refs": refs, "planned_dose": planned,
        "completed_dose": completed, "completion_status": completion_status,
        "user_feedback": feedback,
    }


async def list_plan_executions(owner_id: str, item_ref: str) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    item_ref = _validate_ref(item_ref, "item_ref", required_prefix="plan-item")
    doc = _load_doc()
    if item_ref not in doc.get("items", {}):
        raise PlanNotFound(item_ref)
    result = []
    for execution in doc.get("executions", []):
        if execution.get("owner_id") == owner_id and execution.get("item_ref") == item_ref:
            result.append({
                "execution_ref": execution["execution_ref"],
                "item_ref": item_ref,
                "plan_revision_ref": f"{execution.get('plan_id', '')}:v{execution.get('plan_version', 1)}",
                "item_revision_ref": f"{item_ref}:v{execution.get('item_revision', 1)}",
                "scenario_ref": execution.get("scenario_ref"),
                "run_refs": execution.get("run_refs"),
                "planned_dose": execution.get("planned_dose"),
                "completed_dose": execution.get("completed_dose"),
                "completion_status": execution.get("completion_status"),
                "user_feedback": execution.get("user_feedback"),
            })
    return result


async def record_retest(
    owner_id: str,
    item_ref: str,
    *,
    kind: str,
    expected_metric_ref: str,
    expected_direction: str,
    analysis_refs: Sequence[str],
    comparability: str,
    result: str,
    limitations: Sequence[str],
) -> dict[str, Any]:
    owner_id = _required_owner(owner_id)
    item_ref = _validate_ref(item_ref, "item_ref", required_prefix="plan-item")
    if kind not in _RETEST_KINDS:
        raise InvalidTrainingPlan("unknown retest kind")
    metric_ref = _validate_ref(expected_metric_ref, "expected_metric_ref", required_prefix="metric")
    if expected_direction not in {"lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only"}:
        raise InvalidTrainingPlan("expected_direction is invalid")
    refs = _validate_ref_list(analysis_refs, "analysis_refs", "analysis")
    if comparability not in _RETEST_COMPARABILITY:
        raise InvalidTrainingPlan("unknown retest comparability")
    result = _required_text(result, "result")
    if isinstance(limitations, (str, bytes)) or not isinstance(limitations, Sequence) or not limitations or len(limitations) > 16:
        raise InvalidTrainingPlan("limitations must contain 1 to 16 entries")
    normalized_limitations = [_required_text(item, "limitations") for item in limitations]
    async with _WRITE_LOCK:
        doc = _load_doc()
        item = doc["items"].get(item_ref)
        if item is None:
            raise PlanNotFound(item_ref)
        if item.get("owner_id") != owner_id:
            raise PlanForbidden(item_ref)
        retest_ref = f"retest:{uuid.uuid4().hex}"
        doc["retests"].append({
            "retest_ref": retest_ref,
            "owner_id": owner_id,
            "item_ref": item_ref,
            "plan_id": item.get("plan_id"),
            "plan_version": item.get("plan_version"),
            "item_revision": item.get("item_revision"),
            "kind": kind,
            "expected_metric_ref": metric_ref,
            "expected_direction": expected_direction,
            "analysis_refs": refs,
            "comparability": comparability,
            "result": result,
            "limitations": normalized_limitations,
        })
        _save_doc(doc)
    return {
        "retest_ref": retest_ref, "owner_id": owner_id, "item_ref": item_ref,
        "plan_revision_ref": f"{item.get('plan_id', '')}:v{item.get('plan_version', 1)}",
        "item_revision_ref": f"{item_ref}:v{item.get('item_revision', 1)}", "kind": kind,
        "expected_metric_ref": metric_ref, "expected_direction": expected_direction,
        "analysis_refs": refs, "comparability": comparability, "result": result,
        "limitations": normalized_limitations,
    }


async def list_retests(owner_id: str, item_ref: str) -> list[dict[str, Any]]:
    owner_id = _required_owner(owner_id)
    item_ref = _validate_ref(item_ref, "item_ref", required_prefix="plan-item")
    doc = _load_doc()
    if item_ref not in doc.get("items", {}):
        raise PlanNotFound(item_ref)
    result = []
    for retest in doc.get("retests", []):
        if retest.get("owner_id") == owner_id and retest.get("item_ref") == item_ref:
            result.append({
                "retest_ref": retest["retest_ref"], "item_ref": item_ref,
                "plan_revision_ref": f"{retest.get('plan_id', '')}:v{retest.get('plan_version', 1)}",
                "item_revision_ref": f"{item_ref}:v{retest.get('item_revision', 1)}",
                "kind": retest.get("kind"),
                "expected_metric_ref": retest.get("expected_metric_ref"),
                "expected_direction": retest.get("expected_direction"),
                "analysis_refs": retest.get("analysis_refs"),
                "comparability": retest.get("comparability"),
                "result": retest.get("result"),
                "limitations": retest.get("limitations"),
            })
    return result


async def get_recent_retest_ref(owner_id: str) -> str | None:
    owner_id = _required_owner(owner_id)
    doc = _load_doc()
    for retest in reversed(doc.get("retests", [])):
        if retest.get("owner_id") == owner_id:
            return str(retest.get("retest_ref"))
    return None
