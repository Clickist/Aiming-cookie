from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from . import (
    aiming_profile_store,
    benchmark_catalog,
    coach_commands,
    coach_context,
    coach_runtime,
    coach_store,
    teaching_session_store,
    training_plan_store,
)
from .coach_context_refs import ContextRefError, build_context_bundle, unavailable_context_refs
from .db import get_conn


_tasks: dict[str, asyncio.Task[None]] = {}
_provider_turns: set[str] = set()
_STOP_SETTLE_TIMEOUT_SECONDS = 3.0
_SAFE_ERROR_DOMAINS = {"network", "model", "permission", "tool"}
_EMPTY_OBSERVATION = "尚未选择可重复观察"
_RAW_REFERENCE = re.compile(r"\b(?:analysis|run|event|segment|table|metric):", re.IGNORECASE)
_PERIPHERAL_CHANGE_REQUEST = re.compile(
    r"(?:(?:换|买|升级).{0,8}(?:鼠标|外设)|(?:鼠标|外设).{0,8}(?:换|买|升级))",
)
_DISCOMFORT = re.compile(r"(?:疼痛|疼|麻木|发麻|刺痛|无力|持续不适)")
_NO_DISCOMFORT = re.compile(r"(?:没有|没|不)(?:有)?(?:疼痛|疼|麻木|发麻|刺痛|无力|不适)")
_REFUSAL = re.compile(
    r"(?:先不练|不练了|不想继续|暂停训练|先暂停|算了|不要记录|先不记录|"
    r"(?:别|不要|不想|不再).{0,6}(?:重新复测|重新测|再复测|再测|重测|复测))",
)
_RESUME = re.compile(r"^(?:继续|继续练|恢复训练|重新开始)[。.!！ ]*$")
_TEACHING_INTENT = re.compile(r"(?:带(?:我)?练|开始(?:带我)?练|按计划练|安排练习)")
_CLARIFICATION = re.compile(r"(?:[?？]|是不是|所以|也就是说|意思是|换句话说|那我就|对吗)")
_RETEST_RETRY = re.compile(r"(?:重新复测|重新测|再复测|再测|重测|按原条件)")
_RETEST_OUTCOME_DECISIONS = {
    "coach_retest_outcome.v1:improved": "retain",
    "coach_retest_outcome.v1:unchanged": "lower",
    "coach_retest_outcome.v1:mixed_or_inconclusive": "lower",
    "coach_retest_outcome.v1:worsened": "reject",
}
_TEACHING_WRITE_COMMANDS = frozenset({
    "training_plan.item.add",
    "training_plan.execution.record",
    "training_plan.retest.record",
})
_REVISION_ITEM_STATUSES = {
    "retain": "active",
    "lower": "planned",
    "reject": "cancelled",
}
_CONFIRMED_EXECUTION_STATUS_REASON = "coach_teaching_item_status.v1:confirmed_execution"
_REVISION_STATUS_REASON_PREFIX = "coach_teaching_revision.v1:"
_ANALYSIS_REF = re.compile(r"^analysis:([A-Za-z0-9._@-]+)$")
_NO_GROUNDED_ISSUE_QUESTION = (
    "这次分析还没看出一个明确问题。你自己最想先解决哪种失误或哪段动作?"
)
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
    # `steam_profile:N` is an in-memory Coach reference, not a file path.
    # Mask it before the generic `file:` detector sees the `profile:` suffix.
    safe_for_path_check = re.sub(r"\bsteam_profile:[1-9][0-9]*\b", "steam_ref", value)
    if _PATH_OR_SECRET.search(safe_for_path_check):
        raise AgentRunError("unsafe_content", "Coach content contains disallowed local or secret data")
    return value.strip()


def _safe_tool_events(value: object) -> list[dict[str, Any]]:
    try:
        events = coach_runtime._validate_tool_events(value)  # Shared runtime allow-list.
    except coach_runtime.CoachRuntimeError as error:
        raise AgentRunError("unsafe_tool_event", "Coach tool event was rejected") from error
    encoded = json.dumps(events, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if _PATH_OR_SECRET.search(encoded) or coach_commands.contains_temporary_steam_profile(encoded):
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
        teaching_turn=kwargs.get("teaching_turn"),
        temporary_profile_refs=kwargs.get("temporary_profile_refs"),
    )
    return {
        "status": result.status,
        "reply": result.reply,
        "notes": result.notes,
        "tool_events": result.tool_events,
        "error": result.error,
    }


def _bounded_lesson_text(value: object, *, maximum: int = 480) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > maximum
        or _PATH_OR_SECRET.search(text)
        or _RAW_REFERENCE.search(text)
    ):
        return None
    return text


def _selected_context_issue(
    bundle: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], str] | None:
    contexts = bundle.get("contexts")
    if not isinstance(contexts, list):
        return None

    def issue_at(item: Mapping[str, Any], index: int) -> tuple[Mapping[str, Any], Mapping[str, Any], str] | None:
        projection = item.get("projection")
        diagnosis = projection.get("diagnosis") if isinstance(projection, Mapping) else None
        issues = diagnosis.get("issues") if isinstance(diagnosis, Mapping) else None
        context_ref = item.get("context_ref")
        if (
            not isinstance(issues, list)
            or not 0 <= index < len(issues)
            or not isinstance(issues[index], Mapping)
            or not isinstance(context_ref, str)
        ):
            return None
        return issues[index], projection, context_ref

    # An explicit issue target is a user-selected context and therefore wins.
    for item in contexts:
        if not isinstance(item, Mapping) or item.get("kind") != "issue":
            continue
        analysis_ref = item.get("analysis_ref")
        target_ref = item.get("target_ref")
        prefix = f"{analysis_ref}:issue:"
        if not isinstance(analysis_ref, str) or not isinstance(target_ref, str) or not target_ref.startswith(prefix):
            continue
        try:
            index = int(target_ref.removeprefix(prefix))
        except ValueError:
            continue
        if selected := issue_at(item, index):
            return selected

    candidates: list[tuple[tuple[Any, ...], Mapping[str, Any], Mapping[str, Any], str]] = []
    for context_index, item in enumerate(contexts):
        if not isinstance(item, Mapping):
            continue
        projection = item.get("projection")
        diagnosis = projection.get("diagnosis") if isinstance(projection, Mapping) else None
        issues = diagnosis.get("issues") if isinstance(diagnosis, Mapping) else None
        if not isinstance(issues, list):
            continue
        issues_in_context = [
            (index, issue)
            for index, issue in enumerate(issues)
            if isinstance(issue, Mapping)
        ]
        if not issues_in_context:
            continue
        context_ref = item.get("context_ref")
        if not isinstance(context_ref, str):
            continue
        scenario = projection.get("scenario")
        scenario_ref = (
            scenario.get("scenario_profile_ref")
            if isinstance(scenario, Mapping)
            else None
        )
        for issue_index, issue in issues_in_context:
            entry = coach_context.resolve_registry_teaching_entry(issue)
            prescription = entry.get("scenario_prescription") if entry else None
            scenario_matches = (
                entry is not None
                and "scenario_prescription" in entry.get("supported_uses", [])
                and isinstance(prescription, Mapping)
                and prescription.get("scenario_profile_ref") == scenario_ref
            )
            priority = issue.get("priority")
            candidates.append((
                (
                    0 if scenario_matches else 1,
                    0 if entry is not None else 1,
                    priority
                    if isinstance(priority, (int, float)) and not isinstance(priority, bool)
                    else math.inf,
                    context_index,
                    issue_index,
                ),
                issue,
                projection,
                context_ref,
            ))
    if not candidates:
        return None
    _, issue, projection, context_ref = min(candidates, key=lambda candidate: candidate[0])
    return issue, projection, context_ref


def _lesson_from_bundle(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    selected = _selected_context_issue(bundle)
    if selected is None:
        return None
    issue, projection, context_ref = selected
    observation = _bounded_lesson_text(issue.get("plain_language_meaning"), maximum=1200)

    candidate_texts: list[str] = []
    for cause in issue.get("root_causes") or []:
        if (
            not isinstance(cause, Mapping)
            or cause.get("level") not in {"training", "hypothesis"}
        ):
            continue
        text = _bounded_lesson_text(cause.get("text"), maximum=130)
        if text is not None and text not in candidate_texts:
            candidate_texts.append(text)
        if len(candidate_texts) == 3:
            break

    cue = None
    approved_dose = None
    retest_intent = "none"
    for prescription in issue.get("prescriptions") or []:
        if not isinstance(prescription, Mapping):
            continue
        candidate_cue = _bounded_lesson_text(prescription.get("cue"), maximum=240)
        if candidate_cue is None or candidate_cue.casefold() == "not_applicable":
            continue
        cue = candidate_cue
        approved_dose = _bounded_lesson_text(
            prescription.get("dosage"), maximum=480,
        )
        if not candidate_texts:
            purpose = _bounded_lesson_text(prescription.get("purpose"), maximum=130)
            if purpose is not None:
                candidate_texts.append(purpose)
        verification = issue.get("verification")
        comparable = (
            verification.get("comparable_requirements")
            if isinstance(verification, Mapping)
            else None
        )
        if (
            isinstance(comparable, list)
            and comparable
            and _bounded_lesson_text(prescription.get("retest_after")) is not None
        ):
            retest_intent = "immediate_matched"
        break

    ratios: list[dict[str, Any]] = []
    diagnosis = projection.get("diagnosis")
    summary = diagnosis.get("summary") if isinstance(diagnosis, Mapping) else None
    for metric_ref in issue.get("metric_refs") or []:
        metric = summary.get(metric_ref) if isinstance(summary, Mapping) and isinstance(metric_ref, str) else None
        if not isinstance(metric, Mapping):
            continue
        value = metric.get("value")
        label = _bounded_lesson_text(metric.get("definition"), maximum=160)
        if (
            metric.get("classification") == "deterministic"
            and metric.get("unit") == "ratio"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1
            and label is not None
        ):
            ratios.append({"label": label, "value": value})
        if len(ratios) == 3:
            break

    question = _NO_GROUNDED_ISSUE_QUESTION
    if observation is not None and len(candidate_texts) >= 2:
        question = (
            f"这次出现「{observation}」时，你更明显感觉到"
            f"「{candidate_texts[0]}」还是「{candidate_texts[1]}」?"
        )
    elif observation is not None and candidate_texts:
        question = f"这次出现「{observation}」时，你自己最先感觉卡在哪一步?"

    return {
        "context_ref": context_ref,
        "observation": observation,
        "primary_candidate": (
            f"我先从{candidate_texts[0]}这个方向查起"
            if candidate_texts else None
        ),
        "alternatives": [f"也可能和{text}有关" for text in candidate_texts[1:3]],
        "cue": cue,
        "changed_variable": "注意点" if cue is not None else None,
        "approved_dose": approved_dose,
        "question": question,
        "retest_intent": retest_intent,
        "ratio_sources": ratios,
    }


def _compile_prepared_plan_item(
    bundle: Mapping[str, Any],
    *,
    active_plan_ref: str | None,
) -> dict[str, Any] | None:
    if not isinstance(active_plan_ref, str) or not active_plan_ref.startswith("plan:"):
        return None
    selected = _selected_context_issue(bundle)
    if selected is None:
        return None
    issue, projection, context_ref = selected
    contexts = bundle.get("contexts")
    context = next(
        (
            item
            for item in contexts
            if isinstance(item, Mapping) and item.get("context_ref") == context_ref
        ),
        None,
    ) if isinstance(contexts, list) else None
    if not isinstance(context, Mapping):
        return None
    analysis_ref = context.get("analysis_ref")
    match = _ANALYSIS_REF.fullmatch(analysis_ref) if isinstance(analysis_ref, str) else None
    diagnosis = projection.get("diagnosis")
    issues = diagnosis.get("issues") if isinstance(diagnosis, Mapping) else None
    if match is None or not isinstance(issues, list):
        return None
    issue_index = next(
        (index for index, candidate in enumerate(issues) if candidate is issue),
        None,
    )
    if issue_index is None:
        return None

    entry = coach_context.resolve_registry_teaching_entry(issue)
    scenario_prescription = entry.get("scenario_prescription") if entry else None
    scenario = projection.get("scenario")
    scenario_ref = scenario.get("scenario_profile_ref") if isinstance(scenario, Mapping) else None
    if (
        entry is None
        or "scenario_prescription" not in entry.get("supported_uses", [])
        or not isinstance(scenario_prescription, Mapping)
        or scenario_prescription.get("scenario_profile_ref") != scenario_ref
    ):
        return None
    try:
        from kovaak_tracker.scenario_profiles import active_scenario_profile_refs

        if scenario_ref not in active_scenario_profile_refs():
            return None
    except (KeyError, TypeError, ValueError, OSError):
        return None

    summary = diagnosis.get("summary")
    issue_metrics = issue.get("metric_refs")
    if (
        not isinstance(summary, Mapping)
        or not isinstance(issue_metrics, list)
    ):
        return None
    deterministic_metrics = []
    for metric_ref in issue_metrics:
        metric = summary.get(metric_ref) if isinstance(metric_ref, str) else None
        value = metric.get("value") if isinstance(metric, Mapping) else None
        if (
            isinstance(metric, Mapping)
            and metric.get("classification") == "deterministic"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            deterministic_metrics.append(
                metric_ref if metric_ref.startswith("metric:") else f"metric:{metric_ref}"
            )
    if len(deterministic_metrics) != 1:
        return None

    cue = entry.get("cue")
    dose = entry.get("dose_guardrail")
    direction = entry.get("expected_direction")
    matched_retest = entry.get("matched_retest")
    near_retest = entry.get("near_transfer_retest")
    if (
        not isinstance(cue, Mapping)
        or not isinstance(dose, list)
        or len(dose) != 1
        or not isinstance(dose[0], Mapping)
        or not isinstance(direction, Mapping)
        or not isinstance(matched_retest, Mapping)
        or not isinstance(near_retest, Mapping)
    ):
        return None
    from kovaak_tracker.coach.knowledge_registry import entry_ref

    item = {
        "diagnosis_ref": f"diagnosis:analysis-{match.group(1)}.issue-{issue_index}@1",
        "knowledge_ref": entry_ref(entry),
        "scenario_profile_ref": scenario_ref,
        "baseline_metric_ref": deterministic_metrics[0],
        "expected_direction": direction.get("text"),
        "practice_condition": scenario_prescription.get("practice_condition"),
        "cue": cue.get("text"),
        "dose_guardrail": dose[0].get("text"),
        "matched_retest_ref": (
            f"retest-spec:{matched_retest.get('section_ref')}@{entry['entry_version']}"
        ),
        "near_transfer_retest_ref": (
            f"retest-spec:{near_retest.get('section_ref')}@{entry['entry_version']}"
        ),
        "review_date": scenario_prescription.get("review_after"),
    }
    try:
        normalized_item = training_plan_store._validate_plan_item(item)
    except (KeyError, TypeError, ValueError, training_plan_store.TrainingPlanError):
        return None
    return {"plan_ref": active_plan_ref, "item": normalized_item}


def _teaching_source_refs(state: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for item in (
        state.get("observation"),
        state.get("primary_candidate"),
        *(state.get("alternatives") or []),
    ):
        if isinstance(item, Mapping):
            refs.update(ref for ref in item.get("source_refs") or [] if isinstance(ref, str))
    return refs


def _clear_unavailable_teaching_lesson(hydrated: dict[str, Any]) -> None:
    hydrated.update({
        "phase": "intake",
        "observation": {"summary": _EMPTY_OBSERVATION, "source_refs": []},
        "primary_candidate": None,
        "alternatives": [],
        "cue": None,
        "changed_variable": None,
        "pending_confirmation_ref": None,
        "retest_intent": "none",
        "retest_comparability": "unresolved",
        "revision_decision": None,
        "pause_reason": None,
        "next_recommendation": None,
    })


def _hydrate_teaching_state(
    state: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    unavailable_source_refs: set[str] | None = None,
) -> dict[str, Any]:
    hydrated = json.loads(json.dumps(state, ensure_ascii=False))
    contexts = bundle.get("contexts")
    active_refs = {
        item["context_ref"]
        for item in contexts
        if isinstance(item, Mapping) and isinstance(item.get("context_ref"), str)
    } if isinstance(contexts, list) else set()
    source_refs = _teaching_source_refs(hydrated)
    if (
        source_refs
        and (
            (unavailable_source_refs is not None and source_refs & unavailable_source_refs)
            or (active_refs and not source_refs.issubset(active_refs))
        )
    ):
        _clear_unavailable_teaching_lesson(hydrated)
        return hydrated

    lesson = _lesson_from_bundle(bundle)
    if lesson is None:
        return hydrated
    source = [lesson["context_ref"]]
    if hydrated["observation"]["summary"] == _EMPTY_OBSERVATION and lesson["observation"] is not None:
        hydrated["observation"] = {"summary": lesson["observation"], "source_refs": source}
    if hydrated.get("primary_candidate") is None and lesson["primary_candidate"] is not None:
        hydrated["primary_candidate"] = {"label": lesson["primary_candidate"], "source_refs": source}
    if not hydrated.get("alternatives"):
        hydrated["alternatives"] = [
            {"label": label, "source_refs": source}
            for label in lesson["alternatives"]
        ]
    if hydrated.get("cue") is None and lesson["cue"] is not None:
        hydrated["cue"] = lesson["cue"]
    if hydrated.get("changed_variable") is None and lesson["changed_variable"] is not None:
        hydrated["changed_variable"] = lesson["changed_variable"]
    if hydrated.get("retest_intent") == "none" and lesson["retest_intent"] != "none":
        hydrated["retest_intent"] = lesson["retest_intent"]
    return hydrated


def _is_practice_acceptance(content: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！?？]", "", content)
    return normalized in {
        "好", "好的", "明白", "明白了", "懂了", "开始", "开始吧", "继续",
        "可以开始", "可以开始了", "可以开始吗", "我试试", "好开始吧", "好我试试",
        "明白了开始吧",
    }


def _requests_clarification(content: str) -> bool:
    return not _is_practice_acceptance(content) and _CLARIFICATION.search(content) is not None


def _humanize_primary_candidate(value: object) -> str | None:
    label = _bounded_lesson_text(value, maximum=160)
    if label is None:
        return None
    if label.startswith("待验证候选："):
        return f"我先从{label.removeprefix('待验证候选：')}这个方向查起"
    return label


def _humanize_alternative(value: object) -> str | None:
    label = _bounded_lesson_text(value, maximum=160)
    if label is None:
        return None
    if label.startswith("待验证候选："):
        return f"也可能和{label.removeprefix('待验证候选：')}有关"
    return label


def _candidate_core(value: object) -> str | None:
    label = _bounded_lesson_text(value, maximum=160)
    if label is None:
        return None
    wrappers = (
        ("我先从", "这个方向查起"),
        ("也可能和", "有关"),
        ("待验证候选：", ""),
    )
    for prefix, suffix in wrappers:
        if label.startswith(prefix) and (not suffix or label.endswith(suffix)):
            end = -len(suffix) if suffix else None
            core = label[len(prefix):end].strip(" ：:，,。.!！")
            return core or None
    return label.strip(" ：:，,。.!！") or None


def _candidate_record(item: Mapping[str, Any], *, primary: bool) -> dict[str, Any] | None:
    core = _candidate_core(item.get("label"))
    source_refs = item.get("source_refs")
    if core is None or not isinstance(source_refs, list):
        return None
    return {
        "label": (
            f"我先从{core}这个方向查起"
            if primary else f"也可能和{core}有关"
        ),
        "source_refs": list(source_refs),
    }


def _candidate_is_negated(content: str, core: str) -> bool:
    escaped = re.escape(core)
    negation = r"(?:不是|并非|不像|不太像|没有|没感觉|不明显|排除|不考虑|别从|不要从)"
    return re.search(rf"{negation}.{{0,4}}{escaped}|{escaped}.{{0,4}}{negation}", content) is not None


def _unique_candidate_index(state: Mapping[str, Any], content: str) -> int | None:
    primary = state.get("primary_candidate")
    alternatives = state.get("alternatives")
    if not isinstance(primary, Mapping) or not isinstance(alternatives, list):
        return None
    candidates = [primary, *[item for item in alternatives if isinstance(item, Mapping)]]
    matches = [
        index
        for index, item in enumerate(candidates)
        if (
            (core := _candidate_core(item.get("label"))) is not None
            and core in content
            and not _candidate_is_negated(content, core)
        )
    ]
    normalized = re.sub(r"[\s，,。.!！?？]", "", content)
    if not matches and len(candidates) >= 2:
        if normalized in {"后者", "第二个", "第二种"}:
            matches = [1]
        elif normalized in {"前者", "第一个", "第一种"}:
            matches = [0]
    return matches[0] if len(matches) == 1 else None


def _needs_candidate_clarification(state: Mapping[str, Any], content: str) -> bool:
    primary = state.get("primary_candidate")
    alternatives = state.get("alternatives")
    if not isinstance(primary, Mapping) or not isinstance(alternatives, list):
        return False
    return not _is_practice_acceptance(content)


def _promote_explicit_candidate(
    state: Mapping[str, Any], content: str,
) -> dict[str, Any]:
    updated = json.loads(json.dumps(state, ensure_ascii=False))
    primary = updated.get("primary_candidate")
    alternatives = updated.get("alternatives")
    if not isinstance(primary, Mapping) or not isinstance(alternatives, list):
        return updated
    selected_index = _unique_candidate_index(updated, content)
    if selected_index is None or selected_index == 0:
        return updated
    candidates = [primary, *[item for item in alternatives if isinstance(item, Mapping)]]
    selected = _candidate_record(candidates[selected_index], primary=True)
    remaining = [
        record
        for index, item in enumerate(candidates)
        if index != selected_index
        and (record := _candidate_record(item, primary=False)) is not None
    ][:2]
    if selected is None:
        return updated
    updated["primary_candidate"] = selected
    updated["alternatives"] = remaining
    return updated


async def _prepare_session_for_user_input(
    owner_id: str,
    session: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    state = json.loads(json.dumps(session["state"], ensure_ascii=False))
    if _DISCOMFORT.search(content) and _NO_DISCOMFORT.search(content) is None:
        state.update({
            "phase": "stopped_for_discomfort",
            "pending_confirmation_ref": None,
            "pause_reason": "discomfort",
        })
    elif state["phase"] == "paused" and _RESUME.fullmatch(content.strip()):
        state.update({"phase": "intake", "pending_confirmation_ref": None, "pause_reason": None})
    elif _REFUSAL.search(content):
        state.update({
            "phase": "paused",
            "pending_confirmation_ref": None,
            "pause_reason": "user_refused",
        })
    elif (
        state["phase"] == "revise"
        and state.get("revision_decision") is None
        and (_is_practice_acceptance(content) or _RETEST_RETRY.search(content))
    ):
        state.update({
            "phase": "retest_ready",
            "pending_confirmation_ref": None,
            "retest_comparability": "unresolved",
            "pause_reason": None,
        })
    elif state["phase"] in {"await_teach_back", "teach_back_repair"}:
        state["phase"] = "teach" if _requests_clarification(content) else "practice_ready"
    elif state["phase"] == "practice_ready" and _requests_clarification(content):
        state["phase"] = "teach"
    elif state["phase"] == "hypothesize":
        if (
            _unique_candidate_index(state, content) is None
            and _needs_candidate_clarification(state, content)
        ):
            # Do not infer a synonym as an existing explanation. Ask once more
            # instead of letting the old candidate advance into teaching.
            state["phase"] = "intake"
        else:
            state = _promote_explicit_candidate(state, content)
    elif state["phase"] == "intake" and isinstance(state.get("primary_candidate"), Mapping):
        if _unique_candidate_index(state, content) is not None:
            state = _promote_explicit_candidate(state, content)
            state["phase"] = "hypothesize"
    if state == session["state"]:
        return session
    return await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )


def _candidate_discriminator_question(state: Mapping[str, Any]) -> str | None:
    primary = state.get("primary_candidate")
    alternatives = state.get("alternatives")
    if not isinstance(primary, Mapping) or not isinstance(alternatives, list):
        return None
    labels = [
        core
        for item in [primary, *alternatives]
        if isinstance(item, Mapping)
        and (core := _candidate_core(item.get("label"))) is not None
    ]
    if len(labels) >= 2:
        return f"刚才还不能确定你说的是哪一个方向。你更接近「{labels[0]}」还是「{labels[1]}」?"
    if labels:
        return f"刚才还不能确定你的意思。你说的是「{labels[0]}」这个方向吗?"
    return None


def _bundle_has_analysis_context(bundle: Mapping[str, Any]) -> bool:
    contexts = bundle.get("contexts")
    return isinstance(contexts, list) and any(
        isinstance(item, Mapping)
        and (
            item.get("kind") in {"analysis", "issue"}
            or isinstance(item.get("analysis_ref"), str)
        )
        for item in contexts
    )


def _requires_teaching_turn(
    session: Mapping[str, Any], bundle: Mapping[str, Any], content: str,
) -> bool:
    state = session.get("state")
    return (
        isinstance(state, Mapping)
        and (
            state.get("phase") != "intake"
            or isinstance(state.get("primary_candidate"), Mapping)
        )
    ) or session.get("active_run_ref") is not None or _bundle_has_analysis_context(bundle) or _TEACHING_INTENT.search(content) is not None


def _teaching_contract(
    session: Mapping[str, Any],
    bundle: Mapping[str, Any],
    user_content: str | None = None,
    prepared_plan_item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = _hydrate_teaching_state(session["state"], bundle)
    lesson = _lesson_from_bundle(bundle)
    phase = state["phase"]
    prepared = prepared_plan_item if phase == "practice_ready" else None
    question_kind, question = {
        "intake": (
            "discriminator",
            lesson["question"] if lesson is not None else _NO_GROUNDED_ISSUE_QUESTION,
        ),
        "await_teach_back": ("teach_back", "这组练习的注意点是什么?"),
        "teach_back_repair": ("teach_back_repair", "用自己的话再说一次这组只改变什么?"),
        "follow_up": ("follow_up", "下一次你准备在哪个相近任务里复测?"),
    }.get(phase, ("none", None))
    if phase == "intake" and lesson is None:
        question = _candidate_discriminator_question(state) or question
    peripheral_intake = (
        phase == "intake"
        and isinstance(user_content, str)
        and _PERIPHERAL_CHANGE_REQUEST.search(user_content) is not None
    )
    if peripheral_intake:
        question = "你最想靠换鼠标解决哪个瞄准问题?"
    command = {
        "practice_ready": (
            "training_plan.item.add" if prepared is not None else None
        ),
        "await_execution_confirmation": "training_plan.execution.record",
        "await_retest_confirmation": "training_plan.retest.record",
    }.get(phase)
    confirmation_intent = (
        "execution" if phase == "await_execution_confirmation"
        else "retest" if phase == "await_retest_confirmation" else "none"
    )
    observation = state["observation"]["summary"]
    if observation == _EMPTY_OBSERVATION:
        observation = None
    if peripheral_intake and observation is None:
        observation = "现在没必要换鼠标。"
    primary = state.get("primary_candidate")
    alternatives = [
        label
        for item in state["alternatives"]
        if isinstance(item, Mapping)
        and (label := _humanize_alternative(item.get("label"))) is not None
    ]
    return {
        "schema_version": "coach_teaching_turn.v1",
        "session_ref": session["session_ref"],
        "session_version": session["version"],
        "phase": phase,
        "observation": observation,
        "primary_candidate": (
            _humanize_primary_candidate(primary.get("label"))
            if isinstance(primary, Mapping) else None
        ),
        "alternatives": alternatives,
        "cue": state["cue"],
        "changed_variable": state["changed_variable"],
        "active_item_ref": state["active_item_ref"],
        "question_kind": question_kind,
        "question": question,
        "allowed_command": command,
        "prepared_plan_ref": (
            prepared.get("plan_ref") if prepared is not None else None
        ),
        "prepared_item": (
            prepared.get("item") if prepared is not None else None
        ),
        "confirmation_intent": confirmation_intent,
        "retest": {
            "intent": state["retest_intent"],
            "comparability_required": state["retest_intent"] != "none",
            "comparability": state["retest_comparability"],
            "revision_decision": state["revision_decision"],
        },
        "ratio_sources": (
            lesson["ratio_sources"]
            if lesson is not None
            and lesson["context_ref"] in state["observation"]["source_refs"]
            else []
        ),
        "approved_dose": (
            lesson["approved_dose"]
            if lesson is not None
            and lesson["context_ref"] in state["observation"]["source_refs"]
            else None
        ),
        "next_recommendation": state["next_recommendation"],
    }


def _state_after_success(
    state: Mapping[str, Any],
    contract: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    next_phase = {
        "intake": "hypothesize",
        "hypothesize": "teach",
        "teach": "practice_ready",
        "await_teach_back": "practice_ready",
        "teach_back_repair": "practice_ready",
        "retest_ready": "await_retest_confirmation",
        "revise": "follow_up",
        "follow_up": "intake",
    }.get(contract["phase"], contract["phase"])
    if contract["phase"] == "intake" and contract.get("primary_candidate") is None:
        next_phase = "intake"
    if contract["phase"] == "retest_ready" and contract["retest"]["intent"] == "none":
        next_phase = "retest_ready"
    next_state = _hydrate_teaching_state(state, bundle)
    next_state["phase"] = next_phase
    next_state["pending_confirmation_ref"] = None
    next_state["pause_reason"] = None
    if contract["phase"] == "follow_up":
        next_state.update({
            "observation": {"summary": _EMPTY_OBSERVATION, "source_refs": []},
            "primary_candidate": None,
            "alternatives": [],
            "cue": None,
            "changed_variable": None,
            "active_item_ref": None,
            "retest_intent": "none",
            "retest_comparability": "unresolved",
            "revision_decision": None,
            "next_recommendation": None,
        })
    return next_state


def _may_advance_teaching_fallback(
    contract: Mapping[str, Any], tool_events: Sequence[Mapping[str, Any]],
) -> bool:
    return (
        contract.get("phase") == "intake"
        and _bounded_lesson_text(contract.get("observation")) is not None
        and _bounded_lesson_text(contract.get("primary_candidate")) is not None
        and contract.get("allowed_command") is None
        and contract.get("confirmation_intent") == "none"
        and not _teaching_command_events(tool_events)
    )


async def _pending_confirmation_ref(
    owner_id: str,
    thread_id: int,
    user_message_id: int,
    command_name: str,
) -> str | None:
    conn = await get_conn()
    row = await (await conn.execute(
        "SELECT confirmation_ref FROM coach_command_confirmations "
        "WHERE owner_id=? AND command_name=? AND thread_id=? AND user_message_ref=? "
        "AND status='pending' ORDER BY created_at DESC, confirmation_ref DESC LIMIT 1",
        (owner_id, command_name, thread_id, f"coach_message:{user_message_id}"),
    )).fetchone()
    return None if row is None else str(row["confirmation_ref"])


def _teaching_command_events(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        event
        for event in events
        if event.get("type") == "product_command"
        and event.get("command_name") in _TEACHING_WRITE_COMMANDS
    ]


def _matching_command_events(contract: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    product_events = _teaching_command_events(events)
    allowed = contract["allowed_command"]
    if not product_events:
        return []
    if allowed is None:
        raise AgentRunError("teaching_command_out_of_phase", "Teaching turn emitted an out-of-phase product command")
    for event in product_events:
        if event.get("command_name") != allowed or event.get("status") != "needs_confirmation":
            raise AgentRunError("teaching_command_out_of_phase", "Teaching turn emitted an out-of-phase product command")
    if len(product_events) != 1:
        raise AgentRunError("teaching_command_out_of_phase", "Teaching turn emitted more than one product command")
    return product_events


async def _release_teaching_run(
    owner_id: str,
    run_ref: str,
    *,
    next_state: Mapping[str, Any] | None = None,
) -> bool:
    contract = await teaching_session_store.load_run_contract(owner_id, run_ref)
    if contract is None:
        return False
    try:
        await teaching_session_store.release_active_run(
            owner_id,
            contract["session_ref"],
            contract["session_version"],
            run_ref,
            next_state=next_state,
        )
    except teaching_session_store.TeachingSessionConflictError:
        # A stopped/failed sibling must never overwrite a newer lesson state.
        return False
    return True


async def _confirmed_fact_ref(
    conn: Any,
    owner_id: str,
    confirmation_row: Mapping[str, Any],
    *,
    prefix: str,
) -> str | None:
    idempotency_key = confirmation_row["idempotency_key"]
    command_name = confirmation_row["command_name"]
    if not isinstance(idempotency_key, str) or not idempotency_key:
        return None
    stored = await (await conn.execute(
        "SELECT result_json FROM coach_command_idempotency "
        "WHERE owner_id=? AND command_name=? AND idempotency_key=?",
        (owner_id, command_name, idempotency_key),
    )).fetchone()
    try:
        command_result = json.loads(stored["result_json"]) if stored is not None else None
    except (TypeError, json.JSONDecodeError):
        return None
    result_ref = (
        command_result.get("result_ref")
        if isinstance(command_result, Mapping)
        and command_result.get("status") == "succeeded"
        else None
    )
    return result_ref if isinstance(result_ref, str) and result_ref.startswith(prefix) else None


def _next_recommendation_for_confirmed_retest(
    item: Mapping[str, Any],
    *,
    comparability: str,
    result: str,
    limitations: object,
) -> dict[str, Any] | None:
    if (
        comparability != "comparable"
        or result != "coach_retest_outcome.v1:improved"
        or not isinstance(limitations, list)
        or "metric_change_policy_missing" in limitations
    ):
        return None
    scenario_profile_ref = item.get("scenario_profile_ref")
    if not isinstance(scenario_profile_ref, str):
        return None
    try:
        pair = benchmark_catalog.pair_for_scenario_profile(scenario_profile_ref)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(pair, Mapping):
        return None
    easier = pair.get("easier")
    medium = pair.get("medium")
    if (
        not isinstance(easier, Mapping)
        or easier.get("scenario_profile_ref") != scenario_profile_ref
        or not isinstance(medium, Mapping)
        or not isinstance(medium.get("scenario_name"), str)
        or not medium["scenario_name"].strip()
    ):
        return None
    medium_profile_ref = medium.get("scenario_profile_ref")
    if medium_profile_ref is not None and (
        not isinstance(medium_profile_ref, str)
        or not medium_profile_ref.startswith("scenario:")
    ):
        return None
    scenario_name = medium["scenario_name"].strip()
    return {
        "scenario_name": scenario_name,
        "scenario_profile_ref": medium_profile_ref,
        "message": (
            f"下一项可以尝试 {scenario_name}（Medium），把它当作更难的压力测试和新的基线；"
            "它本身不证明迁移。"
        ),
    }


async def _reconcile_teaching_session(owner_id: str, session: dict[str, Any]) -> dict[str, Any]:
    pending_ref = session["state"].get("pending_confirmation_ref")
    if not isinstance(pending_ref, str):
        return session
    conn = await get_conn()
    row = await (await conn.execute(
        "SELECT status, command_name, parameters_json, idempotency_key "
        "FROM coach_command_confirmations "
        "WHERE confirmation_ref=? AND owner_id=?",
        (pending_ref, owner_id),
    )).fetchone()
    if row is None or row["status"] == "pending":
        raise AgentRunError("teaching_confirmation_pending", "Teaching confirmation is still pending")
    state = json.loads(json.dumps(session["state"], ensure_ascii=False))
    if row["status"] == "cancelled":
        state.update({"phase": "paused", "pending_confirmation_ref": None, "pause_reason": "user_refused"})
    elif row["status"] == "consumed":
        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, json.JSONDecodeError):
            raise AgentRunError("teaching_confirmation_invalid", "Teaching confirmation is invalid")
        item_ref = parameters.get("item_ref") if isinstance(parameters, Mapping) else None
        if row["command_name"] == "training_plan.item.add":
            confirmed_item_ref = await _confirmed_fact_ref(
                conn, owner_id, row, prefix="plan-item:",
            )
            item = await (await conn.execute(
                "SELECT 1 FROM training_plan_items WHERE owner_id=? AND item_ref=?",
                (owner_id, confirmed_item_ref),
            )).fetchone()
            if item is None:
                raise AgentRunError(
                    "teaching_item_missing", "Confirmed plan item fact is unavailable",
                )
            state.update({
                "phase": "await_execution_confirmation",
                "active_item_ref": confirmed_item_ref,
                "pending_confirmation_ref": None,
                "pause_reason": None,
            })
        elif row["command_name"] == "training_plan.execution.record":
            execution_ref = await _confirmed_fact_ref(
                conn, owner_id, row, prefix="plan-execution:",
            )
            execution = await (await conn.execute(
                "SELECT completion_status, user_feedback FROM training_plan_executions "
                "WHERE execution_ref=? AND owner_id=? AND item_ref=?",
                (execution_ref, owner_id, item_ref),
            )).fetchone()
            if execution_ref is None or item_ref != state.get("active_item_ref") or execution is None:
                raise AgentRunError(
                    "teaching_execution_missing", "Confirmed execution fact is unavailable",
                )
            feedback = str(execution["user_feedback"] or "")
            if _DISCOMFORT.search(feedback) and _NO_DISCOMFORT.search(feedback) is None:
                state.update({
                    "phase": "stopped_for_discomfort",
                    "pending_confirmation_ref": None,
                    "pause_reason": "discomfort",
                })
            elif execution["completion_status"] == "skipped":
                state.update({
                    "phase": "paused",
                    "pending_confirmation_ref": None,
                    "pause_reason": "user_refused",
                })
            else:
                state = _promote_explicit_candidate(state, feedback)
                await training_plan_store.set_plan_item_status(
                    owner_id,
                    item_ref,
                    "active",
                    reason=_CONFIRMED_EXECUTION_STATUS_REASON,
                )
                state.update({
                    "phase": "retest_ready",
                    "pending_confirmation_ref": None,
                    "pause_reason": None,
                })
        elif row["command_name"] == "training_plan.retest.record":
            retest_ref = await _confirmed_fact_ref(
                conn, owner_id, row, prefix="retest:",
            )
            retest = await (await conn.execute(
                "SELECT comparability, result, limitations_json FROM training_plan_retests "
                "WHERE retest_ref=? AND owner_id=? AND item_ref=?",
                (retest_ref, owner_id, item_ref),
            )).fetchone()
            if retest_ref is None or retest is None:
                raise AgentRunError("teaching_retest_missing", "Confirmed retest fact is unavailable")
            if item_ref != state.get("active_item_ref"):
                raise AgentRunError("teaching_retest_missing", "Confirmed retest fact is unavailable")
            comparability = {
                "comparable": "comparable",
                "not_comparable": "not_comparable",
                "unavailable": "unresolved",
            }.get(retest["comparability"], "unresolved")
            try:
                limitations = json.loads(retest["limitations_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                limitations = None
            policy_missing = (
                isinstance(limitations, list)
                and "metric_change_policy_missing" in limitations
            )
            revision_decision = (
                _RETEST_OUTCOME_DECISIONS.get(retest["result"])
                if comparability == "comparable" and limitations is not None and not policy_missing
                else None
            )
            item_payload = None
            item_row = await (await conn.execute(
                "SELECT item_payload_json FROM training_plan_items WHERE owner_id=? AND item_ref=?",
                (owner_id, item_ref),
            )).fetchone()
            if item_row is not None:
                try:
                    parsed_item = json.loads(item_row["item_payload_json"])
                except (TypeError, json.JSONDecodeError):
                    parsed_item = None
                if isinstance(parsed_item, Mapping):
                    item_payload = parsed_item
            if revision_decision is not None:
                await training_plan_store.set_plan_item_status(
                    owner_id,
                    item_ref,
                    _REVISION_ITEM_STATUSES[revision_decision],
                    reason=f"{_REVISION_STATUS_REASON_PREFIX}{revision_decision}",
                )
            state.update({
                "phase": "revise",
                "pending_confirmation_ref": None,
                "pause_reason": None,
                "retest_comparability": comparability,
                "revision_decision": revision_decision,
                "next_recommendation": (
                    _next_recommendation_for_confirmed_retest(
                        item_payload,
                        comparability=comparability,
                        result=retest["result"],
                        limitations=limitations,
                    )
                    if isinstance(item_payload, Mapping)
                    else None
                ),
            })
        else:
            state.update({"pending_confirmation_ref": None, "pause_reason": None})
    else:
        raise AgentRunError("teaching_confirmation_invalid", "Teaching confirmation is invalid")
    return await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )


async def _load_or_append_user_message(
    run_ref: str,
    thread_id: int,
    content: str,
    snapshots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    conn = await get_conn()
    row = await (await conn.execute(
        "SELECT user_message_id FROM coach_agent_runs WHERE run_ref=?", (run_ref,),
    )).fetchone()
    existing_id = row["user_message_id"] if row is not None else None
    messages = await coach_store.load_messages(thread_id)
    if existing_id is not None:
        prior = [message for message in messages if message["id"] != existing_id]
        if len(prior) == len(messages):
            raise AgentRunError("teaching_message_missing", "Stored Coach user message is unavailable")
        return prior, int(existing_id)
    user_message_id = await coach_store.append_message(
        thread_id, "user", content, context_refs=snapshots,
    )
    cursor = await conn.execute(
        "UPDATE coach_agent_runs SET user_message_id=? WHERE run_ref=? AND user_message_id IS NULL",
        (user_message_id, run_ref),
    )
    await conn.commit()
    if cursor.rowcount != 1:
        raise AgentRunError("teaching_message_conflict", "Coach user message changed before execution")
    return messages, user_message_id


async def _complete_teaching_turn(
    owner_id: str,
    run_ref: str,
    thread_id: int,
    user_message_id: int,
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> bool:
    tool_events = outcome["tool_events"]
    teaching_notes = set(outcome["notes"])
    if teaching_notes & {"teaching_fallback", "teaching_hold"}:
        if _teaching_command_events(tool_events):
            raise AgentRunError(
                "teaching_command_out_of_phase",
                "A non-advancing teaching turn may not execute a product command",
            )
        if (
            "teaching_fallback" in teaching_notes
            and _may_advance_teaching_fallback(contract, tool_events)
        ):
            session = await teaching_session_store.get_session(owner_id, contract["session_ref"])
            if session is None:
                raise AgentRunError("teaching_session_missing", "TeachingSession is unavailable")
            return await _release_teaching_run(
                owner_id,
                run_ref,
                next_state=_state_after_success(session["state"], contract, bundle),
            )
        return await _release_teaching_run(owner_id, run_ref)

    matching_events = _matching_command_events(contract, tool_events)
    if matching_events:
        session = await teaching_session_store.get_session(owner_id, contract["session_ref"])
        if session is None:
            raise AgentRunError("teaching_session_missing", "TeachingSession is unavailable")
        confirmation_ref = await _pending_confirmation_ref(
            owner_id, thread_id, user_message_id, contract["allowed_command"],
        )
        if confirmation_ref is None:
            raise AgentRunError("teaching_confirmation_missing", "Teaching confirmation is unavailable")
        next_state = json.loads(json.dumps(session["state"], ensure_ascii=False))
        next_state["pending_confirmation_ref"] = confirmation_ref
        next_state["pause_reason"] = "awaiting_confirmation"
        return await _release_teaching_run(owner_id, run_ref, next_state=next_state)
    if any(event.get("type") == "product_command" for event in tool_events):
        return await _release_teaching_run(owner_id, run_ref)
    session = await teaching_session_store.get_session(owner_id, contract["session_ref"])
    if session is None:
        raise AgentRunError("teaching_session_missing", "TeachingSession is unavailable")
    return await _release_teaching_run(
        owner_id,
        run_ref,
        next_state=_state_after_success(session["state"], contract, bundle),
    )


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


async def _stop_requested(run_ref: str) -> bool:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT stop_requested FROM coach_agent_runs WHERE run_ref=?",
            (run_ref,),
        )
    ).fetchone()
    return row is not None and int(row["stop_requested"]) == 1


async def _mark_stopped(run_ref: str, *, partial_text: str | None = None) -> bool:
    conn = await get_conn()
    cursor = await conn.execute(
        "UPDATE coach_agent_runs SET status='stopped', phase='completed', partial_text=?, "
        "error_json=NULL, finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
        "WHERE run_ref=? AND stop_requested=1 AND status IN ('queued', 'running')",
        (partial_text, run_ref),
    )
    await conn.commit()
    if cursor.rowcount != 1:
        return False
    await _append_event(
        run_ref,
        event_type="status",
        phase="completed",
        code="run_stopped",
        message="Coach run stopped by the user",
    )
    return True


def _normalize_outcome(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid result")
    status = value.get("status")
    if status not in {"succeeded", "failed", "stopped"}:
        raise AgentRunError("invalid_runner_result", "Coach runner returned an invalid status")
    reply = value.get("reply")
    safe_reply = (
        _safe_text(coach_commands.redact_temporary_steam_profiles(reply))
        if isinstance(reply, str) and reply.strip()
        else None
    )
    notes = value.get("notes", [])
    if not isinstance(notes, list) or len(notes) > 16:
        raise AgentRunError("invalid_runner_result", "Coach runner returned invalid notes")
    safe_notes = [_safe_text(note, max_length=500) for note in notes]
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
    return {
        "status": status,
        "reply": safe_reply,
        "notes": safe_notes,
        "tool_events": events,
        "error": safe_error,
    }


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
    temporary_profile_refs: Mapping[str, str] | None = None,
) -> None:
    try:
        contract = await teaching_session_store.load_run_contract(owner_id, run_ref)
        await _set_run(run_ref, status="running", phase="text_generation", started=True)
        await _append_event(
            run_ref, event_type="phase", phase="text_generation",
            code="text_generation_started", message="Coach is generating a response",
        )
        if await _stop_requested(run_ref):
            await _mark_stopped(run_ref)
            return
        prior, user_message_id = await _load_or_append_user_message(
            run_ref, thread_id, content, snapshots,
        )
        if await _stop_requested(run_ref):
            await _mark_stopped(run_ref)
            return
        _provider_turns.add(run_ref)
        try:
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
                teaching_turn=contract,
                temporary_profile_refs=temporary_profile_refs,
            ))
        finally:
            _provider_turns.discard(run_ref)
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
        status = "stopped" if await _stop_requested(run_ref) else outcome["status"]
        if status == "succeeded":
            if contract is not None and not await _complete_teaching_turn(
                owner_id,
                run_ref,
                thread_id,
                user_message_id,
                bundle,
                contract,
                outcome,
            ):
                raise AgentRunError(
                    "teaching_session_conflict",
                    "TeachingSession changed before this turn completed",
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
            await _set_run(
                run_ref, status="succeeded", phase="completed",
                partial_text=reply, finished=True,
            )
            await _append_event(
                run_ref, event_type="status", phase="completed",
                code="run_succeeded", message="Coach run completed",
            )
        elif status == "stopped":
            await _release_teaching_run(owner_id, run_ref)
            await _mark_stopped(run_ref, partial_text=reply)
        else:
            await _release_teaching_run(owner_id, run_ref)
            await _set_run(
                run_ref, status="failed", phase="completed",
                partial_text=reply, error=outcome["error"], finished=True,
            )
            await _append_event(
                run_ref, event_type="error", phase="completed",
                code=outcome["error"]["code"], message=outcome["error"]["message"],
            )
    except asyncio.CancelledError:
        await _release_teaching_run(owner_id, run_ref)
        await _mark_stopped(run_ref)
        raise
    except (AgentRunError, ContextRefError) as error:
        await _release_teaching_run(owner_id, run_ref)
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
        await _release_teaching_run(owner_id, run_ref)
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
        _provider_turns.discard(run_ref)
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
    _retry_contract: Mapping[str, Any] | None = None,
    _retry_user_message_id: int | None = None,
) -> dict[str, Any]:
    safe_content, temporary_profile_refs = coach_commands.prepare_temporary_steam_profiles(content)
    safe_content = _safe_text(safe_content)
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    session = await _reconcile_teaching_session(owner_id, session)
    session = await _prepare_session_for_user_input(owner_id, session, safe_content)
    thread_id = int(session["thread_id"])
    bundle, snapshots = await build_context_bundle(thread_id, context_refs)
    unavailable_refs = await unavailable_context_refs(_teaching_source_refs(session["state"]))
    hydrated_state = _hydrate_teaching_state(
        session["state"], bundle, unavailable_source_refs=unavailable_refs,
    )
    if hydrated_state != session["state"]:
        session = await teaching_session_store.replace_state(
            owner_id,
            session["session_ref"],
            session["version"],
            hydrated_state,
        )
    needs_teaching_turn = (
        _retry_contract is not None
        or _requires_teaching_turn(session, bundle, safe_content)
    )
    prepared_plan_item = None
    if needs_teaching_turn and _retry_contract is None:
        profile = await aiming_profile_store.get_profile_snapshot(owner_id)
        prepared_plan_item = _compile_prepared_plan_item(
            bundle,
            active_plan_ref=profile.get("active_plan_ref"),
        )
    contract = (
        dict(_retry_contract)
        if _retry_contract is not None
        else _teaching_contract(
            session,
            bundle,
            safe_content,
            prepared_plan_item,
        )
        if needs_teaching_turn
        else None
    )
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
            "attempt, status, phase, content, user_message_id, context_refs_json) "
            "VALUES(?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)",
            (
                run_ref, owner_id, thread_id, parent_run_ref, attempt, safe_content,
                _retry_user_message_id,
                json.dumps(snapshots, ensure_ascii=False, allow_nan=False, sort_keys=True),
            ),
        )
        await conn.commit()
    except Exception:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise
    if contract is not None:
        try:
            await teaching_session_store.claim_active_run(
                owner_id, session["session_ref"], session["version"], run_ref, contract,
            )
        except teaching_session_store.TeachingSessionConflictError:
            failure = {
                "domain": "tool",
                "code": "teaching_session_busy",
                "message": "TeachingSession changed before this turn could start",
                "retryable": True,
            }
            await _set_run(run_ref, status="failed", phase="completed", error=failure, finished=True)
            await _append_event(
                run_ref, event_type="error", phase="completed",
                code=failure["code"], message=failure["message"],
            )
            return await get_run(owner_id, run_ref)
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
        temporary_profile_refs=temporary_profile_refs,
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
        "WHERE run_ref=? AND owner_id=? AND status IN ('queued', 'running')",
        (run_ref, owner_id),
    )
    await conn.commit()
    current = await get_run(owner_id, run_ref)
    if current is None or current["status"] in {"succeeded", "failed", "stopped"}:
        return current
    task = _tasks.get(run_ref)
    if task is not None:
        await coach_runtime.stop_pi_coach_turn(run_ref)
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=_STOP_SETTLE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if run_ref not in _provider_turns:
                # SQLite writes are short and must reach their next cooperative
                # stop check without task cancellation interrupting a statement.
                return current
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
    else:
        await _mark_stopped(run_ref)
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
            "SELECT content, context_refs_json, user_message_id, teaching_contract_json "
            "FROM coach_agent_runs WHERE run_ref=? AND owner_id=?",
            (run_ref, owner_id),
        )
    ).fetchone()
    snapshots = json.loads(row["context_refs_json"] or "[]")
    refs = [item["context_ref"] for item in snapshots]
    contract = await teaching_session_store.load_run_contract(owner_id, run_ref)
    return await create_run(
        owner_id,
        row["content"],
        context_refs=refs,
        parent_run_ref=run_ref,
        attempt=int(detail["attempt"]) + 1,
        tool_bridge_endpoint=tool_bridge_endpoint,
        desktop_token=desktop_token,
        _retry_contract=contract,
        _retry_user_message_id=row["user_message_id"],
    )
