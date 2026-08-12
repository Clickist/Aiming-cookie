"""Owner-scoped persistence for deterministic Coach teaching state."""
from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from typing import Any

from . import coach_store, training_plan_store
from .db import get_conn


class TeachingSessionConflictError(RuntimeError):
    """A lesson changed or is already active while this turn was in flight."""


_SESSION_SCHEMA = "teaching_session.v1"
_CONTRACT_SCHEMA = "coach_teaching_turn.v1"
_PHASES = {
    "intake", "hypothesize", "teach", "await_teach_back", "teach_back_repair",
    "practice_ready", "await_execution_confirmation", "retest_ready",
    "await_retest_confirmation", "revise", "follow_up", "paused", "stopped_for_discomfort",
}
_QUESTION_KINDS = {
    "none", "discriminator", "teach_back", "teach_back_repair", "follow_up",
}
_COMMANDS = {
    "training_plan.item.add", "training_plan.execution.record", "training_plan.retest.record",
}
_CONFIRMATION_INTENTS = {"none", "execution", "retest"}
_RETEST_INTENTS = {"none", "immediate_matched", "delayed_matched", "near_transfer"}
_COMPARABILITY = {"unresolved", "comparable", "not_comparable", "not_requested"}
_REVISION_DECISIONS = {None, "retain", "lower", "reject"}
_PAUSE_REASONS = {None, "user_refused", "discomfort", "awaiting_confirmation"}
_FORBIDDEN_DIRECT_UPDATE_FIELDS = frozenset({
    "active_run_ref", "pending_confirmation_ref", "schema_version", "version", "phase",
})
_EVIDENCE_STRENGTHS = {"limited", "supported", "repeated"}
_COUNTEREVIDENCE_STATUSES = {"not_observed", "observed"}
_EVIDENCE_KINDS = {"measured", "self_reported", "observed", "inferred", "external"}
_PRACTICE_INTENTS = {"warm_up", "practice", "benchmark", "main_game_transfer", "unspecified"}
_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9._:@-]{1,159}$")
_SESSION_REF_RE = re.compile(r"^teaching_session:[a-f0-9]{32}$")
_RUN_REF_RE = re.compile(r"^agent_run:[A-Za-z0-9_-]{1,64}$")
_ITEM_REF_RE = re.compile(r"^plan-item:[A-Za-z0-9._:@-]{1,159}$")
_PROBLEM_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")
_FORBIDDEN_TEXT = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file:|https?://|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=])",
    re.IGNORECASE,
)
_RAW_REFERENCE = re.compile(r"\b(?:analysis|run|event|segment|table|metric):", re.IGNORECASE)


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    text = value.strip()
    if _FORBIDDEN_TEXT.search(text):
        raise ValueError(f"{field} must not contain paths, URLs, or secrets")
    return text


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    return None if value is None else _text(value, field, maximum=maximum)


def _session_ref(value: object) -> str:
    if not isinstance(value, str) or not _SESSION_REF_RE.fullmatch(value):
        raise ValueError("session_ref is invalid")
    return value


def _contract_text(value: object, field: str) -> str | None:
    text = _optional_text(value, field, maximum=480)
    if text is not None and _RAW_REFERENCE.search(text):
        raise ValueError(f"{field} must not contain raw references")
    return text


def _diagnostic_text(value: object, field: str) -> str | None:
    text = _contract_text(value, field)
    if text is not None and len(text) > 240:
        raise ValueError(f"{field} is invalid")
    return text


def _problem_id(value: object) -> str | None:
    if value is None:
        return None
    problem_id = _text(value, "contract.problem_id", maximum=96)
    if _PROBLEM_ID_RE.fullmatch(problem_id) is None:
        raise ValueError("contract.problem_id is invalid")
    return problem_id


def _diagnostic_list(value: object, field: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    result: list[str] = []
    for item in value:
        text = _diagnostic_text(item, field)
        if text is None:
            raise ValueError(f"{field} is invalid")
        result.append(text)
    return result


def _typed_evidence_list(value: object, field: str, *, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    result: list[Any] = []
    for item in value:
        if isinstance(item, str):
            text = _diagnostic_text(item, field)
            if text is None:
                raise ValueError(f"{field} is invalid")
            result.append(text)
            continue
        if not isinstance(item, Mapping) or set(item) != {"kind", "text", "refs"}:
            raise ValueError(f"{field} is invalid")
        kind = item.get("kind")
        text = _diagnostic_text(item.get("text"), f"{field}.text")
        refs = _source_refs(item.get("refs"), f"{field}.refs")
        if kind not in _EVIDENCE_KINDS or text is None:
            raise ValueError(f"{field} is invalid")
        result.append({"kind": kind, "text": text, "refs": refs})
    return result


def _learner_context(value: object) -> dict[str, Any]:
    if value is None:
        return {
            "player_problem": None,
            "desired_outcome": None,
            "practice_intent": "unspecified",
            "constraints": [],
        }
    if not isinstance(value, Mapping) or set(value) != {
        "player_problem", "desired_outcome", "practice_intent", "constraints",
    }:
        raise ValueError("learner_context is invalid")
    if value.get("practice_intent") not in _PRACTICE_INTENTS:
        raise ValueError("learner_context.practice_intent is invalid")
    constraints = value.get("constraints")
    if not isinstance(constraints, list) or len(constraints) > 6:
        raise ValueError("learner_context.constraints is invalid")
    return {
        "player_problem": _optional_text(value.get("player_problem"), "learner_context.player_problem", maximum=480),
        "desired_outcome": _optional_text(value.get("desired_outcome"), "learner_context.desired_outcome", maximum=480),
        "practice_intent": value.get("practice_intent"),
        "constraints": [_text(item, "learner_context.constraints", maximum=240) for item in constraints],
    }


def _discriminator(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"kind", "prompt"}:
        raise ValueError("contract.discriminator is invalid")
    kind = value.get("kind")
    prompt = _diagnostic_text(value.get("prompt"), "contract.discriminator.prompt")
    if kind not in {"question", "experiment"} or prompt is None:
        raise ValueError("contract.discriminator is invalid")
    question_count = len(re.findall(r"[?？]", prompt))
    if (kind == "question" and question_count != 1) or (kind == "experiment" and question_count != 0):
        raise ValueError("contract.discriminator is invalid")
    return {"kind": kind, "prompt": prompt}


def _source_refs(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError(f"{field} is invalid")
    refs: list[str] = []
    for ref in value:
        if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
            raise ValueError(f"{field} is invalid")
        refs.append(ref)
    return refs


def _candidate(value: object, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"label", "source_refs"}:
        raise ValueError(f"{field} is invalid")
    return {
        "label": _text(value["label"], f"{field}.label", maximum=160),
        "source_refs": _source_refs(value["source_refs"], f"{field}.source_refs"),
    }


def _active_item_ref(value: object, field: str = "active_item_ref") -> str | None:
    if value is None:
        return None
    item_ref = _text(value, field, maximum=170)
    if _ITEM_REF_RE.fullmatch(item_ref) is None:
        raise ValueError(f"{field} is invalid")
    return item_ref


def _next_recommendation(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "scenario_name", "scenario_profile_ref", "message",
    }:
        raise ValueError("next_recommendation is invalid")
    scenario_profile_ref = value.get("scenario_profile_ref")
    if scenario_profile_ref is not None:
        scenario_profile_ref = _text(
            scenario_profile_ref,
            "next_recommendation.scenario_profile_ref",
            maximum=180,
        )
        if not scenario_profile_ref.startswith("scenario:") or _REF_RE.fullmatch(scenario_profile_ref) is None:
            raise ValueError("next_recommendation is invalid")
    scenario_name = _contract_text(value.get("scenario_name"), "next_recommendation.scenario_name")
    message = _contract_text(value.get("message"), "next_recommendation.message")
    if (
        scenario_name is None
        or message is None
        or scenario_name not in message
    ):
        raise ValueError("next_recommendation is invalid")
    return {
        "scenario_name": scenario_name,
        "scenario_profile_ref": scenario_profile_ref,
        "message": message,
    }


def validate_state(value: object) -> dict[str, Any]:
    """Return the bounded v1 lesson state or reject untrusted expansion."""
    if not isinstance(value, Mapping):
        raise ValueError("TeachingSession state is invalid")
    required = {
        "schema_version", "phase", "observation", "primary_candidate", "alternatives",
        "cue", "changed_variable", "active_item_ref", "pending_confirmation_ref", "retest_intent",
        "retest_comparability", "revision_decision", "pause_reason", "next_recommendation",
    }
    legacy_required = required - {"next_recommendation"}
    extended_required = required | {"learner_context"}
    extended_legacy_required = legacy_required | {"learner_context"}
    if set(value) not in {
        frozenset(required), frozenset(legacy_required),
        frozenset(extended_required), frozenset(extended_legacy_required),
    } or value.get("schema_version") != _SESSION_SCHEMA:
        raise ValueError("TeachingSession state is invalid")
    phase = value.get("phase")
    if phase not in _PHASES:
        raise ValueError("TeachingSession state is invalid")
    observation = value.get("observation")
    if not isinstance(observation, Mapping) or set(observation) != {"summary", "source_refs"}:
        raise ValueError("TeachingSession state is invalid")
    alternatives = value.get("alternatives")
    if not isinstance(alternatives, list) or len(alternatives) > 2:
        raise ValueError("TeachingSession state is invalid")
    pending = _optional_text(value.get("pending_confirmation_ref"), "pending_confirmation_ref", maximum=160)
    if value.get("retest_intent") not in _RETEST_INTENTS or value.get("retest_comparability") not in _COMPARABILITY:
        raise ValueError("TeachingSession state is invalid")
    if value.get("revision_decision") not in _REVISION_DECISIONS or value.get("pause_reason") not in _PAUSE_REASONS:
        raise ValueError("TeachingSession state is invalid")
    if value.get("retest_comparability") != "comparable" and value.get("revision_decision") is not None:
        raise ValueError("TeachingSession state is invalid")
    normalized = {
        "schema_version": _SESSION_SCHEMA,
        "phase": phase,
        "observation": {
            "summary": _text(observation["summary"], "observation.summary", maximum=1200),
            "source_refs": _source_refs(observation["source_refs"], "observation.source_refs"),
        },
        "primary_candidate": _candidate(value.get("primary_candidate"), "primary_candidate"),
        "alternatives": [_candidate(item, "alternatives") for item in alternatives],
        "cue": _optional_text(value.get("cue"), "cue", maximum=240),
        "changed_variable": _optional_text(value.get("changed_variable"), "changed_variable", maximum=160),
        "active_item_ref": _active_item_ref(value.get("active_item_ref")),
        "pending_confirmation_ref": pending,
        "retest_intent": value.get("retest_intent"),
        "retest_comparability": value.get("retest_comparability"),
        "revision_decision": value.get("revision_decision"),
        "pause_reason": value.get("pause_reason"),
        "next_recommendation": _next_recommendation(value.get("next_recommendation")),
    }
    if "learner_context" in value:
        normalized["learner_context"] = _learner_context(value.get("learner_context"))
    return normalized


def validate_contract(value: object, *, session_ref: str, session_version: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("TeachingTurnContract is invalid")
    legacy_required = {
        "schema_version", "session_ref", "session_version", "phase", "observation",
        "primary_candidate", "alternatives", "cue", "changed_variable", "active_item_ref", "question_kind",
        "question", "allowed_command", "confirmation_intent", "retest", "ratio_sources",
        "approved_dose", "prepared_plan_ref", "prepared_item", "next_recommendation",
    }
    diagnostic_fields = {
        "problem_id", "problem_label", "evidence_strength", "supporting_evidence",
        "counterevidence_status", "counterevidence", "discriminator", "soft_start",
    }
    required = legacy_required | diagnostic_fields
    if frozenset(value) not in {frozenset(legacy_required), frozenset(required)} or value.get("schema_version") != _CONTRACT_SCHEMA:
        raise ValueError("TeachingTurnContract is invalid")
    has_diagnostic_fields = frozenset(value) == frozenset(required)
    _session_ref(session_ref)
    if value.get("session_ref") != session_ref or value.get("session_version") != session_version:
        raise ValueError("TeachingTurnContract is invalid")
    if not isinstance(session_version, int) or isinstance(session_version, bool) or session_version < 0:
        raise ValueError("TeachingTurnContract is invalid")
    phase = value.get("phase")
    if phase not in _PHASES or value.get("question_kind") not in _QUESTION_KINDS:
        raise ValueError("TeachingTurnContract is invalid")
    expected_question_kind = {
        "intake": "discriminator", "await_teach_back": "teach_back",
        "teach_back_repair": "teach_back_repair", "follow_up": "follow_up",
    }.get(phase, "none")
    if value.get("question_kind") != expected_question_kind:
        raise ValueError("TeachingTurnContract is invalid")
    question = _contract_text(value.get("question"), "contract.question")
    if (expected_question_kind != "none") != (question is not None):
        raise ValueError("TeachingTurnContract is invalid")
    if question is not None and len(re.findall(r"[?？]", question)) != 1:
        raise ValueError("TeachingTurnContract is invalid")
    problem_id = _problem_id(value.get("problem_id")) if has_diagnostic_fields else None
    problem_label = _diagnostic_text(value.get("problem_label"), "contract.problem_label") if has_diagnostic_fields else None
    if (problem_id is None) != (problem_label is None):
        raise ValueError("TeachingTurnContract is invalid")
    evidence_strength = value.get("evidence_strength") if has_diagnostic_fields else "limited"
    supporting_evidence = (
        _typed_evidence_list(value.get("supporting_evidence"), "contract.supporting_evidence", maximum=4)
        if has_diagnostic_fields else []
    )
    counterevidence_status = value.get("counterevidence_status") if has_diagnostic_fields else "not_observed"
    counterevidence = (
        _diagnostic_list(value.get("counterevidence"), "contract.counterevidence", maximum=2)
        if has_diagnostic_fields else []
    )
    discriminator = _discriminator(value.get("discriminator")) if has_diagnostic_fields else None
    soft_start = value.get("soft_start") if has_diagnostic_fields else False
    if (
        evidence_strength not in _EVIDENCE_STRENGTHS
        or (problem_id is not None and not supporting_evidence)
        or counterevidence_status not in _COUNTEREVIDENCE_STATUSES
        or (counterevidence_status == "observed") != bool(counterevidence)
        or not isinstance(soft_start, bool)
    ):
        raise ValueError("TeachingTurnContract is invalid")
    if discriminator is not None and discriminator["kind"] == "question" and (
        phase != "intake" or question != discriminator["prompt"]
    ):
        raise ValueError("TeachingTurnContract is invalid")
    command = value.get("allowed_command")
    if command is not None and command not in _COMMANDS:
        raise ValueError("TeachingTurnContract is invalid")
    prepared_plan_ref = value.get("prepared_plan_ref")
    prepared_item = value.get("prepared_item")
    if (prepared_plan_ref is None) != (prepared_item is None):
        raise ValueError("TeachingTurnContract is invalid")
    if prepared_plan_ref is not None:
        if phase != "practice_ready":
            raise ValueError("TeachingTurnContract is invalid")
        try:
            prepared_plan_ref = training_plan_store._required_plan_id(prepared_plan_ref)
            prepared_item = training_plan_store._validate_plan_item(prepared_item)
        except (TypeError, ValueError, training_plan_store.TrainingPlanError) as error:
            raise ValueError("TeachingTurnContract is invalid") from error
    expected_command = {
        "practice_ready": (
            "training_plan.item.add" if prepared_item is not None else None
        ),
        "await_execution_confirmation": "training_plan.execution.record",
        "await_retest_confirmation": "training_plan.retest.record",
    }.get(phase)
    if command != expected_command:
        raise ValueError("TeachingTurnContract is invalid")
    expected_confirmation = (
        "execution" if phase == "await_execution_confirmation"
        else "retest" if phase == "await_retest_confirmation" else "none"
    )
    if value.get("confirmation_intent") != expected_confirmation:
        raise ValueError("TeachingTurnContract is invalid")
    if soft_start and (
        phase != "intake" or command is not None or expected_confirmation != "none"
    ):
        raise ValueError("TeachingTurnContract is invalid")
    alternatives = value.get("alternatives")
    retest = value.get("retest")
    ratios = value.get("ratio_sources")
    if (
        not isinstance(alternatives, list)
        or len(alternatives) > 2
        or not isinstance(retest, Mapping)
        or set(retest) != {
            "intent", "comparability_required", "comparability", "revision_decision",
        }
    ):
        raise ValueError("TeachingTurnContract is invalid")
    next_recommendation = _next_recommendation(value.get("next_recommendation"))
    if next_recommendation is not None and (
        phase != "revise"
        or retest.get("comparability") != "comparable"
        or retest.get("revision_decision") != "retain"
    ):
        raise ValueError("TeachingTurnContract is invalid")
    if (
        retest.get("intent") not in _RETEST_INTENTS
        or not isinstance(retest.get("comparability_required"), bool)
        or retest.get("comparability_required") != (retest.get("intent") != "none")
        or retest.get("comparability") not in _COMPARABILITY
        or retest.get("revision_decision") not in _REVISION_DECISIONS
        or (
            retest.get("comparability") != "comparable"
            and retest.get("revision_decision") is not None
        )
    ):
        raise ValueError("TeachingTurnContract is invalid")
    if not isinstance(ratios, list) or len(ratios) > 3:
        raise ValueError("TeachingTurnContract is invalid")
    normalized_alternatives: list[str] = []
    for item in alternatives:
        alternative = _contract_text(item, "contract.alternatives")
        if alternative is None:
            raise ValueError("TeachingTurnContract is invalid")
        normalized_alternatives.append(alternative)
    primary_candidate = _contract_text(value.get("primary_candidate"), "contract.primary_candidate")
    normalized_ratios: list[dict[str, Any]] = []
    for ratio in ratios:
        if not isinstance(ratio, Mapping) or set(ratio) != {"label", "value"}:
            raise ValueError("TeachingTurnContract is invalid")
        ratio_value = ratio.get("value")
        if isinstance(ratio_value, bool) or not isinstance(ratio_value, (int, float)) or not math.isfinite(ratio_value) or not 0 <= ratio_value <= 1:
            raise ValueError("TeachingTurnContract is invalid")
        label = _contract_text(ratio.get("label"), "contract.ratio_sources.label")
        if label is None:
            raise ValueError("TeachingTurnContract is invalid")
        normalized_ratios.append({"label": label, "value": ratio_value})
    return {
        "schema_version": _CONTRACT_SCHEMA,
        "session_ref": session_ref,
        "session_version": session_version,
        "phase": phase,
        "problem_id": problem_id,
        "problem_label": problem_label,
        "evidence_strength": evidence_strength,
        "supporting_evidence": supporting_evidence,
        "counterevidence_status": counterevidence_status,
        "counterevidence": counterevidence,
        "observation": _contract_text(value.get("observation"), "contract.observation"),
        "primary_candidate": primary_candidate,
        "alternatives": normalized_alternatives,
        "cue": _contract_text(value.get("cue"), "contract.cue"),
        "changed_variable": _contract_text(value.get("changed_variable"), "contract.changed_variable"),
        "active_item_ref": _active_item_ref(value.get("active_item_ref"), "contract.active_item_ref"),
        "prepared_plan_ref": prepared_plan_ref,
        "prepared_item": prepared_item,
        "question_kind": value["question_kind"],
        "question": question,
        "allowed_command": command,
        "confirmation_intent": expected_confirmation,
        "retest": {
            "intent": retest["intent"],
            "comparability_required": retest["comparability_required"],
            "comparability": retest["comparability"],
            "revision_decision": retest["revision_decision"],
        },
        "ratio_sources": normalized_ratios,
        "approved_dose": _contract_text(value.get("approved_dose"), "contract.approved_dose"),
        "next_recommendation": next_recommendation,
        "discriminator": discriminator,
        "soft_start": soft_start,
    }


def _initial_state() -> dict[str, Any]:
    return {
        "schema_version": _SESSION_SCHEMA,
        "phase": "intake",
        "observation": {"summary": "尚未选择可重复观察", "source_refs": []},
        "primary_candidate": None,
        "alternatives": [],
        "cue": None,
        "changed_variable": None,
        "active_item_ref": None,
        "pending_confirmation_ref": None,
        "retest_intent": "none",
        "retest_comparability": "unresolved",
        "revision_decision": None,
        "pause_reason": None,
        "next_recommendation": None,
        "learner_context": _learner_context(None),
    }


def _row_to_session(row: Any) -> dict[str, Any]:
    try:
        state = validate_state(json.loads(row["state_json"]))
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("stored TeachingSession state is invalid") from exc
    return {
        "session_ref": row["session_ref"],
        "owner_id": row["owner_id"],
        "thread_id": int(row["thread_id"]),
        "state": state,
        "version": int(row["version"]),
        "active_run_ref": row["active_run_ref"],
        "pending_confirmation_ref": row["pending_confirmation_ref"],
        "pause_reason": row["pause_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def get_or_create_primary_session(owner_id: str) -> dict[str, Any]:
    owner_id = _text(owner_id, "owner_id", maximum=128)
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        thread = await coach_store.get_or_create_primary_thread(owner_id, conn=conn)
        row = await (await conn.execute(
            "SELECT * FROM teaching_sessions WHERE owner_id=? AND thread_id=?",
            (owner_id, int(thread["id"])),
        )).fetchone()
        if row is None:
            state = _initial_state()
            session_ref = f"teaching_session:{uuid.uuid4().hex}"
            await conn.execute(
                "INSERT INTO teaching_sessions(session_ref, owner_id, thread_id, state_json, "
                "pending_confirmation_ref, pause_reason) VALUES(?, ?, ?, ?, ?, ?)",
                (session_ref, owner_id, int(thread["id"]), _wire(state), None, None),
            )
            row = await (await conn.execute(
                "SELECT * FROM teaching_sessions WHERE session_ref=?", (session_ref,),
            )).fetchone()
        await conn.commit()
        return _row_to_session(row)
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


async def get_session(owner_id: str, session_ref: str) -> dict[str, Any] | None:
    session_ref = _session_ref(session_ref)
    conn = await get_conn()
    row = await (await conn.execute(
        "SELECT * FROM teaching_sessions WHERE session_ref=? AND owner_id=?",
        (session_ref, owner_id),
    )).fetchone()
    return None if row is None else _row_to_session(row)


async def _owned_session_for_update(conn: Any, owner_id: str, session_ref: str) -> dict[str, Any]:
    row = await (await conn.execute(
        "SELECT session.* FROM teaching_sessions AS session "
        "JOIN coach_threads AS thread ON thread.id=session.thread_id "
        "WHERE session.session_ref=? AND session.owner_id=? "
        "AND thread.user_id=? AND thread.kind='primary'",
        (session_ref, owner_id, owner_id),
    )).fetchone()
    if row is None:
        raise TeachingSessionConflictError("TeachingSession is unavailable")
    return _row_to_session(row)


async def claim_active_run(
    owner_id: str,
    session_ref: str,
    expected_version: int,
    run_ref: str,
    contract: object,
) -> dict[str, Any]:
    if not isinstance(expected_version, int) or expected_version < 0 or not _RUN_REF_RE.fullmatch(run_ref):
        raise ValueError("TeachingSession claim is invalid")
    session_ref = _session_ref(session_ref)
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        session = await _owned_session_for_update(conn, owner_id, session_ref)
        normalized_contract = validate_contract(
            contract, session_ref=session_ref, session_version=expected_version,
        )
        run = await (await conn.execute(
            "SELECT run.run_ref, run.thread_id, run.teaching_session_ref, "
            "run.teaching_state_version, run.teaching_contract_json "
            "FROM coach_agent_runs AS run "
            "JOIN coach_threads AS thread ON thread.id=run.thread_id "
            "WHERE run.run_ref=? AND run.owner_id=? AND thread.user_id=? "
            "AND thread.status <> 'deleted'",
            (run_ref, owner_id, owner_id),
        )).fetchone()
        if run is None:
            raise TeachingSessionConflictError("Coach run is unavailable")
        if (
            session["version"] != expected_version
            or session["active_run_ref"] is not None
            or normalized_contract["phase"] != session["state"]["phase"]
            or normalized_contract["active_item_ref"] != session["state"]["active_item_ref"]
            or normalized_contract["retest"]["intent"] != session["state"]["retest_intent"]
            or normalized_contract["retest"]["comparability"] != session["state"]["retest_comparability"]
            or normalized_contract["retest"]["revision_decision"] != session["state"]["revision_decision"]
        ):
            raise TeachingSessionConflictError("TeachingSession changed before this run could start")
        encoded_contract = _wire(normalized_contract)
        if run["teaching_contract_json"] is not None:
            if (
                run["teaching_session_ref"] != session_ref
                or run["teaching_state_version"] != expected_version
                or run["teaching_contract_json"] != encoded_contract
            ):
                raise TeachingSessionConflictError("TeachingTurnContract is immutable")
        else:
            cursor = await conn.execute(
                "UPDATE coach_agent_runs SET teaching_session_ref=?, teaching_state_version=?, "
                "teaching_contract_json=? WHERE run_ref=? AND owner_id=? AND thread_id=? "
                "AND teaching_session_ref IS NULL AND teaching_state_version IS NULL "
                "AND teaching_contract_json IS NULL",
                (
                    session_ref, expected_version, encoded_contract, run_ref, owner_id,
                    int(run["thread_id"]),
                ),
            )
            if cursor.rowcount != 1:
                raise TeachingSessionConflictError("TeachingTurnContract is immutable")
        cursor = await conn.execute(
            "UPDATE teaching_sessions SET active_run_ref=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref IS NULL",
            (run_ref, session_ref, owner_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise TeachingSessionConflictError("TeachingSession changed before this run could start")
        row = await (await conn.execute(
            "SELECT * FROM teaching_sessions WHERE session_ref=?", (session_ref,),
        )).fetchone()
        await conn.commit()
        return _row_to_session(row)
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


async def replace_state(owner_id: str, session_ref: str, expected_version: int, state: object) -> dict[str, Any]:
    return await release_active_run(
        owner_id, session_ref, expected_version, None, next_state=state, require_active=False,
    )


async def release_active_run(
    owner_id: str,
    session_ref: str,
    expected_version: int,
    run_ref: str | None,
    *,
    next_state: object | None = None,
    require_active: bool = True,
) -> dict[str, Any]:
    if not isinstance(expected_version, int) or expected_version < 0:
        raise ValueError("TeachingSession release is invalid")
    session_ref = _session_ref(session_ref)
    if require_active and (not isinstance(run_ref, str) or not _RUN_REF_RE.fullmatch(run_ref)):
        raise ValueError("TeachingSession release is invalid")
    normalized = validate_state(next_state) if next_state is not None else None
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        session = await _owned_session_for_update(conn, owner_id, session_ref)
        if session["version"] != expected_version or (require_active and session["active_run_ref"] != run_ref):
            raise TeachingSessionConflictError("TeachingSession changed before this run completed")
        if normalized is None:
            cursor = await conn.execute(
                "UPDATE teaching_sessions SET active_run_ref=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref IS ?",
                (session_ref, owner_id, expected_version, run_ref),
            )
        else:
            cursor = await conn.execute(
                "UPDATE teaching_sessions SET state_json=?, version=version+1, active_run_ref=NULL, "
                "pending_confirmation_ref=?, pause_reason=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE session_ref=? AND owner_id=? AND version=? AND active_run_ref IS ?",
                (
                    _wire(normalized), normalized["pending_confirmation_ref"], normalized["pause_reason"],
                    session_ref, owner_id, expected_version, run_ref,
                ),
            )
        if cursor.rowcount != 1:
            raise TeachingSessionConflictError("TeachingSession changed before this run completed")
        row = await (await conn.execute(
            "SELECT * FROM teaching_sessions WHERE session_ref=?", (session_ref,),
        )).fetchone()
        await conn.commit()
        return _row_to_session(row)
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


async def update_state_partial(
    owner_id: str,
    session_ref: str,
    expected_version: int,
    next_phase: str | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge partial updates into TeachingSession state with optimistic versioning."""
    if (
        not isinstance(expected_version, int)
        or isinstance(expected_version, bool)
        or expected_version < 0
    ):
        raise ValueError("TeachingSession update is invalid")
    session_ref = _session_ref(session_ref)
    if not isinstance(updates, Mapping) or set(updates) & _FORBIDDEN_DIRECT_UPDATE_FIELDS:
        raise ValueError("TeachingSession update contains forbidden fields")
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        session = await _owned_session_for_update(conn, owner_id, session_ref)
        if session["version"] != expected_version:
            raise TeachingSessionConflictError("TeachingSession changed before this update could apply")
        merged = dict(session["state"])
        if next_phase is not None:
            merged["phase"] = next_phase
        merged.update(updates)
        normalized = validate_state(merged)
        cursor = await conn.execute(
            "UPDATE teaching_sessions SET state_json=?, version=version+1, "
            "pending_confirmation_ref=?, pause_reason=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE session_ref=? AND owner_id=? AND version=?",
            (
                _wire(normalized), normalized["pending_confirmation_ref"],
                normalized["pause_reason"], session_ref, owner_id, expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise TeachingSessionConflictError("TeachingSession changed before this update could apply")
        row = await (await conn.execute(
            "SELECT * FROM teaching_sessions WHERE session_ref=?", (session_ref,),
        )).fetchone()
        await conn.commit()
        return _row_to_session(row)
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


async def bind_run_contract(
    owner_id: str,
    run_ref: str,
    session_ref: str,
    session_version: int,
    contract: object,
) -> None:
    if not _RUN_REF_RE.fullmatch(run_ref):
        raise ValueError("TeachingTurnContract run_ref is invalid")
    session_ref = _session_ref(session_ref)
    normalized = validate_contract(contract, session_ref=session_ref, session_version=session_version)
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        session = await _owned_session_for_update(conn, owner_id, session_ref)
        row = await (await conn.execute(
            "SELECT teaching_session_ref, teaching_state_version, teaching_contract_json, thread_id "
            "FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
            (run_ref, owner_id),
        )).fetchone()
        if row is None or int(row["thread_id"]) != session["thread_id"]:
            raise TeachingSessionConflictError("Coach run is unavailable")
        encoded = _wire(normalized)
        if row["teaching_contract_json"] is not None:
            if (
                row["teaching_session_ref"] != session_ref
                or row["teaching_state_version"] != session_version
                or row["teaching_contract_json"] != encoded
            ):
                raise TeachingSessionConflictError("TeachingTurnContract is immutable")
            await conn.commit()
            return
        cursor = await conn.execute(
            "UPDATE coach_agent_runs SET teaching_session_ref=?, teaching_state_version=?, "
            "teaching_contract_json=? WHERE run_ref=? AND owner_id=? "
            "AND teaching_contract_json IS NULL",
            (session_ref, session_version, encoded, run_ref, owner_id),
        )
        if cursor.rowcount != 1:
            raise TeachingSessionConflictError("TeachingTurnContract is immutable")
        await conn.commit()
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


async def load_run_contract(owner_id: str, run_ref: str) -> dict[str, Any] | None:
    conn = await get_conn()
    row = await (await conn.execute(
        "SELECT run.teaching_session_ref, run.teaching_state_version, run.teaching_contract_json "
        "FROM coach_agent_runs AS run JOIN teaching_sessions AS session "
        "ON session.session_ref=run.teaching_session_ref "
        "WHERE run.run_ref=? AND run.owner_id=? AND session.owner_id=?",
        (run_ref, owner_id, owner_id),
    )).fetchone()
    if row is None or row["teaching_contract_json"] is None:
        return None
    try:
        return validate_contract(
            json.loads(row["teaching_contract_json"]),
            session_ref=row["teaching_session_ref"],
            session_version=int(row["teaching_state_version"]),
        )
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("stored TeachingTurnContract is invalid") from exc
