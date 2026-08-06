from __future__ import annotations

import asyncio
import copy
import json

import pytest

from webapp.backend import (
    coach_agent_runs,
    coach_commands,
    coach_store,
    queue,
    teaching_session_store,
    training_plan_store,
)
from webapp.backend.db import get_conn


def _contract() -> dict:
    return {
        "allowed_command": "training_plan.execution.record",
    }


def _plan_payload() -> dict:
    return {
        "title": "Practice one tracking cue",
        "diagnostic_context": {
            "analysis_refs": ["analysis:42"],
            "metric_refs": ["metric:tracking-error"],
            "knowledge_refs": ["knowledge:speed-matching"],
        },
        "prescriptions": [{
            "scenario": "Smoothbot",
            "cue": "Match speed before correcting position.",
            "purpose": "Reduce repeated corrections.",
            "target_metric_refs": ["metric:tracking-error"],
            "expected_direction": "decrease",
            "source_level": "deterministic_rule",
        }],
    }


def _verification_targets() -> list[dict]:
    return [{
        "target_metric": "metric:tracking-error",
        "expected_direction": "decrease",
        "comparable_requirements": ["same scenario", "same sensitivity"],
        "retest_after": "after practice",
        "insufficient_evidence_behavior": "collect another comparable run",
    }]


def _item_payload() -> dict:
    return {
        "diagnosis_ref": "diagnosis:tracking-error@1",
        "knowledge_ref": "knowledge:speed-matching@1",
        "scenario_profile_ref": "scenario:tracking.smoothbot@1",
        "baseline_metric_ref": "metric:tracking-error@v1",
        "expected_direction": "lower_better",
        "practice_condition": "Repeat the reviewed tracking scenario.",
        "cue": "Match speed before correcting position.",
        "dose_guardrail": "Stop after three degraded runs.",
        "matched_retest_ref": "retest-spec:tracking-matched@1",
        "near_transfer_retest_ref": "retest-spec:tracking-transfer@1",
        "review_date": "2026-07-30",
    }


async def _item_status(owner_id: str, item: dict) -> str:
    items = await training_plan_store.list_plan_items(owner_id, item["plan_id"])
    return next(row["status"] for row in items if row["item_ref"] == item["item_ref"])


async def _item_status_history(owner_id: str, item_ref: str) -> list[tuple[str, str | None]]:
    conn = await get_conn()
    rows = await (await conn.execute(
        "SELECT to_status, reason FROM training_plan_item_statuses "
        "WHERE owner_id=? AND item_ref=? ORDER BY rowid",
        (owner_id, item_ref),
    )).fetchall()
    return [(str(row["to_status"]), row["reason"]) for row in rows]


async def _session_with_retest_item(
    owner_id: str,
    *,
    retest_intent: str = "immediate_matched",
) -> tuple[dict, dict]:
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    plan = await training_plan_store.create_draft(
        owner_id,
        _plan_payload(),
        evidence_refs=["analysis:42"],
        verification_targets=_verification_targets(),
    )
    item = await training_plan_store.add_plan_item(
        owner_id,
        plan["plan_id"],
        _item_payload(),
    )
    item = await training_plan_store.set_plan_item_status(
        owner_id,
        item["item_ref"],
        "active",
        reason="test_setup:active",
    )
    state = dict(session["state"])
    state.update({
        "phase": "await_retest_confirmation",
        "active_item_ref": item["item_ref"],
        "retest_intent": retest_intent,
    })
    session = await teaching_session_store.replace_state(
        owner_id,
        session["session_ref"],
        session["version"],
        state,
    )
    return session, item


def _analysis_bundle() -> dict:
    return {
        "schema_version": "coach_turn_context.v1",
        "contexts": [{
            "context_ref": "context:teaching-issue",
            "kind": "issue",
            "analysis_ref": "analysis:42",
            "comparison_analysis_ref": None,
            "target_ref": "analysis:42:issue:1",
            "time_range_ms": None,
            "comparison_projection": None,
            "projection": {
                "diagnosis": {
                    "issues": [
                        {
                            "priority": 1,
                            "plain_language_meaning": "不应选择这条普通 Analysis issue",
                        },
                        {
                            "priority": 2,
                            "plain_language_meaning": "目标减速后出现重复修正",
                            "root_causes": [
                                {"level": "symptom", "text": "重复修正"},
                                {"level": "training", "text": "速度匹配时机"},
                                {"level": "hypothesis", "text": "张力介入"},
                            ],
                            "metric_refs": ["target_time_ratio", "unsafe_ratio"],
                            "verification": {
                                "comparable_requirements": ["相同场景", "相同设置"],
                            },
                            "prescriptions": [{
                                "cue": "看到目标减速时，让自己的移动也开始减速",
                                "dosage": "先保持原场景，只改变这个注意点",
                                "retest_after": "完成当前练习后复测",
                                "source_level": "community_consensus",
                            }],
                        },
                    ],
                    "summary": {
                        "target_time_ratio": {
                            "value": 0.34,
                            "unit": "ratio",
                            "classification": "deterministic",
                            "definition": "目标内时间占比",
                        },
                        "unsafe_ratio": {
                            "value": 0.8,
                            "unit": "ratio",
                            "classification": "deterministic",
                        },
                    },
                },
            },
        }],
    }


def _grounded_plan_bundle(
    *,
    analysis_ref: str,
    scenario_profile_ref: str,
    signal: str,
    metric_ref: str,
) -> dict:
    from kovaak_tracker.coach.knowledge_registry import load_registry, query_registry

    registry = load_registry(registry_version="2026-07-29.v4")
    candidates = query_registry(
        registry,
        issue_signal=signal,
        metric_refs=[metric_ref],
    )
    entry = next(item for item in candidates if signal in item["signals"])
    cue = entry["cue"]["text"]
    dose = entry["dose_guardrail"][0]["text"]
    return {
        "schema_version": "coach_turn_context.v1",
        "contexts": [{
            "context_ref": f"context:{analysis_ref.removeprefix('analysis:')}-issue",
            "kind": "issue",
            "analysis_ref": analysis_ref,
            "comparison_analysis_ref": None,
            "target_ref": f"{analysis_ref}:issue:0",
            "time_range_ms": None,
            "comparison_projection": None,
            "projection": {
                "scenario": {
                    "scenario_profile_ref": scenario_profile_ref,
                    "analyzer_refs": [],
                    "support_status": "supported",
                    "limitations": [],
                },
                "diagnosis": {
                    "issues": [{
                        "signal": signal,
                        "priority": 1,
                        "plain_language_meaning": "A matched Analysis found one repeatable pattern.",
                        "root_causes": [{"level": "training", "text": "Test one technique cue."}],
                        "metric_refs": [metric_ref],
                        "observation_ref": entry["observation_refs"][0],
                        "knowledge_registry_version": registry["registry_version"],
                        "knowledge_entry_refs": [f"knowledge:{entry['entry_id']}@{entry['entry_version']}"],
                        "verification": {"comparable_requirements": ["same exact profile"]},
                        "prescriptions": [{
                            "cue": cue,
                            "dosage": dose,
                            "retest_after": entry["matched_retest"]["text"],
                        }],
                    }],
                    "summary": {
                        metric_ref: {
                            "value": 1.0,
                            "classification": "deterministic",
                        },
                    },
                },
            },
        }],
    }


@pytest.mark.parametrize(
    ("analysis_ref", "scenario_profile_ref", "signal", "metric_ref", "knowledge_ref"),
    [
        (
            "analysis:101",
            "scenario:static.1wall_6targets_small@1",
            "reverse_ratio high",
            "metric:reverse_ratio",
            "knowledge:static.flicking-terminal-control@2",
        ),
        (
            "analysis:102",
            "scenario:dynamic.pasu_small_reload@1",
            "relative velocity mismatch",
            "metric:relative_velocity_gain",
            "knowledge:dynamic.speed-matching-and-reading@2",
        ),
        (
            "analysis:103",
            "scenario:tracking.whj_smooth_strafe_sphere_easy@1",
            "speed mismatch high",
            "metric:tracking_error",
            "knowledge:tracking.predictable-speed-matching@2",
        ),
    ],
)
def test_exact_static_dynamic_and_predictable_tracking_compile_complete_plan_items(
    analysis_ref,
    scenario_profile_ref,
    signal,
    metric_ref,
    knowledge_ref,
):
    bundle = _grounded_plan_bundle(
        analysis_ref=analysis_ref,
        scenario_profile_ref=scenario_profile_ref,
        signal=signal,
        metric_ref=metric_ref,
    )

    prepared = coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    )

    assert prepared is not None
    assert prepared["plan_ref"] == "plan:grounded-coach-plan"
    item = prepared["item"]
    assert set(item) == {
        "diagnosis_ref", "knowledge_ref", "scenario_profile_ref",
        "baseline_metric_ref", "expected_direction", "practice_condition",
        "cue", "dose_guardrail", "matched_retest_ref",
        "near_transfer_retest_ref", "review_date",
    }
    assert item["diagnosis_ref"] == (
        f"diagnosis:analysis-{analysis_ref.removeprefix('analysis:')}.issue-0@1"
    )
    assert item["knowledge_ref"] == knowledge_ref
    assert item["scenario_profile_ref"] == scenario_profile_ref
    assert item["baseline_metric_ref"] == metric_ref


def test_field_shaped_static_analysis_selects_the_first_reviewed_issue_and_normalizes_metric_ref():
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:11",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    bundle["contexts"][0]["kind"] = "analysis"
    bundle["contexts"][0]["target_ref"] = "analysis:11"
    projection = bundle["contexts"][0]["projection"]
    reviewed_issue = projection["diagnosis"]["issues"][0]
    reviewed_issue["priority"] = 2
    reviewed_issue["metric_refs"] = ["reverse_ratio"]
    projection["diagnosis"]["issues"] = [{
        "signal": "decel_frac high",
        "priority": 1,
        "plain_language_meaning": "The movement spends a large share of time decelerating.",
        "root_causes": [{"level": "training", "text": "An unreviewed technique candidate."}],
        "metric_refs": ["decel_frac"],
        "verification": {"comparable_requirements": ["same exact profile"]},
        "prescriptions": [],
    }, reviewed_issue]
    projection["diagnosis"]["summary"] = {
        "decel_frac": {"value": 0.6, "classification": "deterministic"},
        "reverse_ratio": {
            "value": 0.34,
            "classification": "deterministic",
        },
    }

    prepared = coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    )

    assert prepared is not None
    assert prepared["item"]["diagnosis_ref"] == "diagnosis:analysis-11.issue-1@1"
    assert prepared["item"]["baseline_metric_ref"] == "metric:reverse_ratio"
    assert prepared["item"]["knowledge_ref"] == (
        "knowledge:static.flicking-terminal-control@2"
    )
    assert coach_agent_runs._lesson_from_bundle(bundle)["cue"] == prepared["item"]["cue"]


def test_lesson_candidates_exclude_symptoms_and_physical_evidence_limits():
    bundle = _analysis_bundle()
    issue = bundle["contexts"][0]["projection"]["diagnosis"]["issues"][1]
    issue["root_causes"].insert(0, {
        "level": "physical",
        "text": "当前证据不能证明是身体原因",
    })

    lesson = coach_agent_runs._lesson_from_bundle(bundle)

    assert lesson is not None
    assert lesson["primary_candidate"] == "我先从速度匹配时机这个方向查起"
    assert lesson["alternatives"] == ["也可能和张力介入有关"]
    assert "身体原因" not in lesson["question"]


def test_unselected_issues_across_multiple_analyses_are_ranked_once_by_priority():
    first = _analysis_bundle()["contexts"][0]
    second = copy.deepcopy(first)
    first["kind"] = "analysis"
    first["context_ref"] = "context:first-analysis"
    first["analysis_ref"] = "analysis:first"
    first["target_ref"] = "analysis:first"
    first["projection"]["diagnosis"]["issues"] = [{
        "priority": 4,
        "plain_language_meaning": "The first context is lower priority.",
    }]
    second["kind"] = "analysis"
    second["context_ref"] = "context:second-analysis"
    second["analysis_ref"] = "analysis:second"
    second["target_ref"] = "analysis:second"
    second["projection"]["diagnosis"]["issues"] = [{
        "priority": 1,
        "plain_language_meaning": "The second context is the higher priority issue.",
    }]

    selected = coach_agent_runs._selected_context_issue({"contexts": [first, second]})

    assert selected is not None
    assert selected[2] == "context:second-analysis"


def test_teachable_registry_scenario_match_wins_before_issue_priority():
    supported = _grounded_plan_bundle(
        analysis_ref="analysis:201",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )["contexts"][0]
    supported["kind"] = "analysis"
    supported["target_ref"] = "analysis:201"
    supported["projection"]["diagnosis"]["issues"][0]["priority"] = 2
    unsupported = copy.deepcopy(supported)
    unsupported["context_ref"] = "context:unsupported-priority-one"
    unsupported["analysis_ref"] = "analysis:202"
    unsupported["target_ref"] = "analysis:202"
    unsupported["projection"]["diagnosis"]["issues"] = [{
        "signal": "unregistered signal",
        "priority": 1,
        "plain_language_meaning": "Higher priority but not teachable.",
        "metric_refs": ["metric:unregistered"],
    }]

    selected = coach_agent_runs._selected_context_issue({
        "contexts": [unsupported, supported],
    })

    assert selected is not None
    assert selected[2] == supported["context_ref"]


def test_unverified_scenario_prioritizes_decel_frac_before_registry_entry():
    registry_only = _grounded_plan_bundle(
        analysis_ref="analysis:203",
        scenario_profile_ref="scenario:unverified@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )["contexts"][0]
    registry_only["kind"] = "analysis"
    registry_only["target_ref"] = "analysis:203"
    registry_only["projection"]["diagnosis"]["issues"][0]["priority"] = 3

    priority_one_decel = copy.deepcopy(registry_only)
    priority_one_decel["context_ref"] = "context:decel-priority-one"
    priority_one_decel["analysis_ref"] = "analysis:204"
    priority_one_decel["target_ref"] = "analysis:204"
    priority_one_decel["projection"]["diagnosis"]["issues"] = [{
        "signal": "decel_frac high",
        "priority": 1,
        "plain_language_meaning": "The movement spends a large share of time decelerating.",
        "metric_refs": ["metric:decel_frac"],
    }]

    selected = coach_agent_runs._selected_context_issue({
        "contexts": [registry_only, priority_one_decel],
    })

    assert selected is not None
    assert selected[2] == priority_one_decel["context_ref"]


def test_prepared_plan_item_fails_closed_without_active_plan_or_one_deterministic_metric():
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:104",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )

    assert coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref=None,
    ) is None

    summary = bundle["contexts"][0]["projection"]["diagnosis"]["summary"]
    summary["metric:reverse_ratio"]["classification"] = "experimental"
    assert coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    ) is None


def test_prepared_plan_item_rejects_legacy_signal_fallback_and_non_prescription_capability():
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:legacy",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    issue = bundle["contexts"][0]["projection"]["diagnosis"]["issues"][0]
    issue.pop("knowledge_registry_version")
    issue.pop("knowledge_entry_refs")
    issue.pop("observation_ref")

    assert coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    ) is None

    issue.update({
        "knowledge_registry_version": "2026-07-29.v4",
        "knowledge_entry_refs": ["knowledge:tracking.reactive-change-response@2"],
        "observation_ref": "event.target_change",
        "metric_refs": ["metric:change_response"],
    })
    bundle["contexts"][0]["projection"]["diagnosis"]["summary"] = {
        "metric:change_response": {"value": 1.0, "classification": "deterministic"},
    }

    assert coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    ) is None


def test_prepared_plan_item_rejects_an_unbound_or_mismatched_scenario():
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:105",
        scenario_profile_ref="scenario:tracking.whj_smooth_strafe_sphere_easy@1",
        signal="speed mismatch high",
        metric_ref="metric:tracking_error",
    )
    bundle["contexts"][0]["projection"]["scenario"]["scenario_profile_ref"] = (
        "scenario:tracking.unreviewed-reactive@1"
    )

    assert coach_agent_runs._compile_prepared_plan_item(
        bundle,
        active_plan_ref="plan:grounded-coach-plan",
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_active_plan", [True, False])
async def test_practice_turn_exposes_only_an_owner_active_prepared_item(
    monkeypatch,
    has_active_plan,
):
    owner_id = f"teaching-prepared-plan-{has_active_plan}-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    state = dict(session["state"])
    state["phase"] = "practice_ready"
    state["retest_intent"] = "immediate_matched"
    await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:106",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return bundle, []

    async def profile_snapshot(_owner_id):
        return {
            "active_plan_ref": "plan:grounded-coach-plan" if has_active_plan else None,
        }

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "按这一个注意点开始练。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(
        coach_agent_runs.aiming_profile_store,
        "get_profile_snapshot",
        profile_snapshot,
    )
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(owner_id, "开始练", context_refs=None)
    task = coach_agent_runs._tasks.get(run["run_ref"])
    if task is not None:
        await task

    detail = await coach_agent_runs.get_run(owner_id, run["run_ref"])
    assert detail["status"] != "failed", detail["error"]
    assert len(captured) == 1
    contract = captured[0]
    if has_active_plan:
        assert contract["allowed_command"] == "training_plan.item.add"
        assert contract["prepared_plan_ref"] == "plan:grounded-coach-plan"
        assert contract["prepared_item"]["knowledge_ref"] == (
            "knowledge:static.flicking-terminal-control@2"
        )
    else:
        assert contract["allowed_command"] is None
        assert contract["prepared_plan_ref"] is None
        assert contract["prepared_item"] is None
    current = await teaching_session_store.get_or_create_primary_session(owner_id)
    assert current["state"]["phase"] == "practice_ready"


@pytest.mark.asyncio
async def test_first_grounded_turn_starts_before_hydrated_retest_state_is_persisted(
    monkeypatch,
):
    owner_id = "teaching-first-grounded-turn-owner"
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:107",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return bundle, []

    async def profile_snapshot(_owner_id):
        return {"active_plan_ref": None}

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "先看这次最稳定出现的动作问题。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(
        coach_agent_runs.aiming_profile_store,
        "get_profile_snapshot",
        profile_snapshot,
    )
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(owner_id, "这次问题在哪", context_refs=None)
    task = coach_agent_runs._tasks.get(run["run_ref"])
    if task is not None:
        await task

    detail = await coach_agent_runs.get_run(owner_id, run["run_ref"])
    assert detail["status"] == "succeeded", detail["error"]
    assert captured[0]["phase"] == "intake"
    assert captured[0]["retest"]["intent"] == "immediate_matched"
    current = await teaching_session_store.get_or_create_primary_session(owner_id)
    assert current["state"]["phase"] == "hypothesize"
    assert current["state"]["retest_intent"] == "immediate_matched"


@pytest.mark.asyncio
async def test_idle_general_question_uses_the_existing_provider_path_without_a_teaching_turn(
    monkeypatch,
):
    owner_id = "general-question-without-teaching-owner"
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return {"contexts": []}, []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "可以先说说你在什么场景里最容易失误。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(owner_id, "怎么练动态点击?", context_refs=None)
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured == [None]
    assert await teaching_session_store.load_run_contract(owner_id, run["run_ref"]) is None


@pytest.mark.asyncio
async def test_initial_analysis_explanation_does_not_force_a_teaching_turn(monkeypatch):
    owner_id = "analysis-explanation-without-teaching-owner"
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return _analysis_bundle(), []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "减速阶段偏长是当前优先项；反向修正是另一个观察。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(
        owner_id,
        "最重要的问题是什么？请解释优先级和证据。",
        context_refs=None,
    )
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured == [None]
    assert await teaching_session_store.load_run_contract(owner_id, run["run_ref"]) is None
    current = await teaching_session_store.get_or_create_primary_session(owner_id)
    assert current["state"]["phase"] == "intake"


@pytest.mark.asyncio
async def test_idle_conversation_continuation_does_not_start_a_teaching_session(monkeypatch):
    owner_id = "general-continuation-without-teaching-owner"
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return {"contexts": []}, []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "接着解释刚才的问题。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(
        owner_id, "继续解释一下刚才的问题", context_refs=None,
    )
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured == [None]


@pytest.mark.asyncio
async def test_current_steam_profile_is_opaque_before_coach_persistence_and_execution(monkeypatch):
    owner_id = "temporary-steam-profile-owner"
    steam_id = "76561199033719938"
    profile_url = f"https://steamcommunity.com/profiles/{steam_id}/"
    captured: dict = {}

    async def build_bundle(_thread_id, _context_refs):
        return {"contexts": []}, []

    async def execute(**kwargs):
        captured.update(kwargs)
        return {
            "status": "succeeded",
            "reply": f"我会先看这份临时成绩：{profile_url}",
            "notes": [],
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(
        owner_id,
        f"帮我看一下 {profile_url}，也可以按 {steam_id} 查。",
        context_refs=None,
    )
    await coach_agent_runs._tasks[run["run_ref"]]

    thread = await coach_store.get_or_create_primary_thread(owner_id)
    messages = await coach_store.load_messages(int(thread["id"]))
    detail = await coach_agent_runs.get_run(owner_id, run["run_ref"])
    persisted = json.dumps({"messages": messages, "run": detail}, ensure_ascii=False)

    assert steam_id not in persisted
    assert profile_url not in persisted
    assert captured["content"].count("steam_profile:1") == 2
    assert captured["temporary_profile_refs"] == {"steam_profile:1": steam_id}


@pytest.mark.asyncio
async def test_analysis_without_an_issue_still_uses_a_finite_no_lesson_turn(monkeypatch):
    owner_id = "analysis-without-issue-owner"
    captured = []
    bundle = {
        "contexts": [{
            "context_ref": "context:no-issue",
            "kind": "analysis",
            "analysis_ref": "analysis:no-issue",
            "target_ref": "analysis:no-issue",
            "projection": {"diagnosis": {"issues": []}},
        }],
    }

    async def build_bundle(_thread_id, _context_refs):
        return bundle, []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "这次还没有足够信息判断具体问题。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(owner_id, "看看这次分析", context_refs=None)
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured[0] is not None
    assert captured[0]["phase"] == "intake"
    assert captured[0]["primary_candidate"] is None


@pytest.mark.asyncio
async def test_active_teaching_session_continues_without_a_new_analysis(monkeypatch):
    owner_id = "active-teaching-without-analysis-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    state = dict(session["state"])
    state.update({
        "phase": "teach",
        "observation": {"summary": "目标减速后出现重复修正", "source_refs": []},
        "primary_candidate": {"label": "我先从速度匹配时机这个方向查起", "source_refs": []},
        "cue": "目标减速时同步减速",
        "changed_variable": "注意点",
    })
    await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return {"contexts": []}, []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "先只关注目标减速时同步减速。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(owner_id, "继续", context_refs=None)
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured[0] is not None
    assert captured[0]["phase"] == "teach"


@pytest.mark.asyncio
async def test_deleted_analysis_clears_only_its_source_backed_teaching_state(monkeypatch):
    owner_id = "teaching-deleted-analysis-owner"
    session_id = await queue.enqueue(owner_id, "/analysis.mp4", "/analysis.csv")
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    context_ref = "context:deleted-teaching-issue"
    state = dict(session["state"])
    state.update({
        "phase": "teach",
        "observation": {
            "summary": "目标减速后出现重复修正",
            "source_refs": [context_ref],
        },
        "primary_candidate": {
            "label": "我先从速度匹配时机这个方向查起",
            "source_refs": [context_ref],
        },
        "alternatives": [{
            "label": "也可能和张力介入有关",
            "source_refs": [context_ref],
        }],
        "cue": "看到目标减速时，让自己的移动也开始减速",
        "changed_variable": "注意点",
        "retest_intent": "immediate_matched",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    conn = await get_conn()
    await conn.execute("UPDATE sessions SET status='done' WHERE id=?", (session_id,))
    await conn.execute(
        "INSERT INTO coach_context_refs("
        "context_ref, thread_id, dedupe_key, kind, analysis_session_id, label, projection_json"
        ") VALUES(?, ?, ?, 'issue', ?, ?, '{}')",
        (
            context_ref,
            session["thread_id"],
            "deleted-teaching-issue",
            session_id,
            "analysis issue",
        ),
    )
    await conn.commit()

    deleted = await queue.delete_session(session_id, owner_id)
    captured = []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {"status": "succeeded", "reply": "重新确认观察。", "tool_events": [], "error": None}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)
    run = await coach_agent_runs.create_run(owner_id, "继续", context_refs=None)
    await coach_agent_runs._tasks[run["run_ref"]]

    refreshed = await teaching_session_store.get_or_create_primary_session(owner_id)
    assert deleted["deleted"] is True
    assert captured[0] is None
    assert refreshed["state"]["phase"] == "intake"
    assert refreshed["state"]["observation"]["source_refs"] == []
    assert refreshed["state"]["primary_candidate"] is None
    assert refreshed["state"]["alternatives"] == []
    assert refreshed["state"]["cue"] is None
    assert refreshed["state"]["changed_variable"] is None
    assert refreshed["state"]["retest_intent"] == "none"


def test_active_source_backed_teaching_lesson_survives_without_new_analysis():
    state = teaching_session_store._initial_state()
    state.update({
        "phase": "teach",
        "observation": {"summary": "已确认的观察", "source_refs": ["context:active"]},
        "primary_candidate": {
            "label": "已确认的候选",
            "source_refs": ["context:active"],
        },
        "cue": "保留已有提示",
    })

    hydrated = coach_agent_runs._hydrate_teaching_state(
        state,
        {"contexts": []},
        unavailable_source_refs=set(),
    )

    assert hydrated["phase"] == "teach"
    assert hydrated["observation"] == state["observation"]
    assert hydrated["primary_candidate"] == state["primary_candidate"]
    assert hydrated["cue"] == "保留已有提示"


def test_deleted_source_backed_lesson_does_not_hydrate_an_unrelated_context():
    state = teaching_session_store._initial_state()
    state.update({
        "phase": "teach",
        "observation": {"summary": "已删除的观察", "source_refs": ["context:deleted"]},
        "primary_candidate": {
            "label": "已删除的候选",
            "source_refs": ["context:deleted"],
        },
        "cue": "已删除的提示",
        "changed_variable": "注意点",
        "retest_intent": "immediate_matched",
    })
    bundle = _analysis_bundle()
    bundle["contexts"][0]["context_ref"] = "context:unrelated"

    hydrated = coach_agent_runs._hydrate_teaching_state(
        state,
        bundle,
        unavailable_source_refs={"context:deleted"},
    )

    assert hydrated["phase"] == "intake"
    assert hydrated["observation"] == {
        "summary": "尚未选择可重复观察",
        "source_refs": [],
    }
    assert hydrated["primary_candidate"] is None
    assert hydrated["alternatives"] == []
    assert hydrated["cue"] is None
    assert hydrated["changed_variable"] is None
    assert hydrated["retest_intent"] == "none"


@pytest.mark.asyncio
async def test_compiled_item_uses_existing_confirmation_and_session_reconciliation():
    owner_id = "teaching-compiled-item-confirmation-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    plan = await training_plan_store.create_draft(
        owner_id,
        _plan_payload(),
        evidence_refs=["analysis:108"],
        verification_targets=_verification_targets(),
    )
    await training_plan_store.save_plan(owner_id, plan["plan_id"])
    await training_plan_store.activate_plan(owner_id, plan["plan_id"])
    prepared = coach_agent_runs._compile_prepared_plan_item(
        _grounded_plan_bundle(
            analysis_ref="analysis:108",
            scenario_profile_ref="scenario:static.1wall_6targets_small@1",
            signal="reverse_ratio high",
            metric_ref="metric:reverse_ratio",
        ),
        active_plan_ref=plan["plan_id"],
    )
    assert prepared is not None
    command = {
        "command_name": "training_plan.item.add",
        "parameters": {
            "plan_ref": prepared["plan_ref"],
            "item_payload": prepared["item"],
        },
        "idempotency_key": "teaching-compiled-item-confirmation",
    }

    pending = await coach_commands.execute_product_command(
        owner_id,
        command,
        authorization_source="coach_inferred",
        thread_id=session["thread_id"],
    )
    state = dict(session["state"])
    state.update({
        "phase": "practice_ready",
        "pending_confirmation_ref": pending["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    confirmed = await coach_commands.execute_product_command(
        owner_id,
        {**command, "confirmation_ref": pending["confirmation"]["confirmation_ref"]},
        authorization_source="confirmed",
        thread_id=session["thread_id"],
    )

    reconciled = await coach_agent_runs._reconcile_teaching_session(owner_id, session)

    assert confirmed["status"] == "succeeded"
    assert reconciled["state"]["phase"] == "await_execution_confirmation"
    assert reconciled["state"]["active_item_ref"] == confirmed["result_ref"]
    saved = await training_plan_store.list_plan_items(owner_id, plan["plan_id"])
    assert {
        key: saved[0][key]
        for key in prepared["item"]
    } == prepared["item"]


def test_teaching_contract_rejects_out_of_phase_product_command():
    with pytest.raises(coach_agent_runs.AgentRunError, match="out-of-phase"):
        coach_agent_runs._matching_command_events(
            _contract(),
            [{
                "type": "product_command",
                "command_name": "training_plan.retest.record",
                "status": "needs_confirmation",
            }],
        )


def test_teaching_contract_does_not_treat_existing_product_commands_as_training_writes():
    assert coach_agent_runs._matching_command_events(
        _contract(),
        [{
            "type": "product_command",
            "command_name": "analysis.create_from_run",
            "status": "needs_confirmation",
        }],
    ) == []


@pytest.mark.asyncio
async def test_intake_contract_keeps_a_valid_internal_discriminator_for_no_lesson_fallback():
    session = await teaching_session_store.get_or_create_primary_session(
        "teaching-intake-owner",
    )

    contract = coach_agent_runs._teaching_contract(session, {"contexts": []})

    assert contract["question_kind"] == "discriminator"
    assert contract["question"] == (
        "这次分析还没看出一个明确问题。你自己最想先解决哪种失误或哪段动作?"
    )
    assert contract["primary_candidate"] is None


@pytest.mark.asyncio
async def test_missing_evidence_answers_a_mouse_change_request_without_unsolicited_device_advice():
    session = await teaching_session_store.get_or_create_primary_session(
        "teaching-mouse-owner",
    )

    asked = coach_agent_runs._teaching_contract(
        session, {"contexts": []}, "我是不是该换鼠标了?",
    )
    ordinary = coach_agent_runs._teaching_contract(
        session, {"contexts": []}, "开始带我练",
    )
    after = coach_agent_runs._state_after_success(
        session["state"], asked, {"contexts": []},
    )

    assert asked["observation"] == "现在没必要换鼠标。"
    assert asked["question"] == "你最想靠换鼠标解决哪个瞄准问题?"
    assert ordinary["observation"] is None
    assert "鼠标" not in ordinary["question"]
    assert after["phase"] == "intake"


@pytest.mark.asyncio
async def test_teaching_contract_hydrates_one_bounded_lesson_from_the_selected_issue():
    session = await teaching_session_store.get_or_create_primary_session(
        "teaching-analysis-owner",
    )

    contract = coach_agent_runs._teaching_contract(session, _analysis_bundle())
    hydrated = coach_agent_runs._state_after_success(
        session["state"], contract, _analysis_bundle(),
    )

    assert contract["observation"] == "目标减速后出现重复修正"
    assert contract["primary_candidate"] == "我先从速度匹配时机这个方向查起"
    assert contract["alternatives"] == ["也可能和张力介入有关"]
    assert "重复修正" not in contract["primary_candidate"]
    assert contract["cue"] == "看到目标减速时，让自己的移动也开始减速"
    assert contract["changed_variable"] == "注意点"
    assert contract["approved_dose"] == "先保持原场景，只改变这个注意点"
    assert contract["question"] == (
        "这次出现「目标减速后出现重复修正」时，你更明显感觉到"
        "「速度匹配时机」还是「张力介入」?"
    )
    assert contract["ratio_sources"] == [{"label": "目标内时间占比", "value": 0.34}]
    assert contract["retest"] == {
        "intent": "immediate_matched",
        "comparability_required": True,
        "comparability": "unresolved",
        "revision_decision": None,
    }
    assert hydrated["observation"]["source_refs"] == ["context:teaching-issue"]
    assert hydrated["primary_candidate"]["label"] == contract["primary_candidate"]
    assert hydrated["phase"] == "hypothesize"


@pytest.mark.asyncio
async def test_clear_discriminator_answer_promotes_one_existing_candidate_only():
    owner_id = "teaching-candidate-answer-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    contract = coach_agent_runs._teaching_contract(session, _analysis_bundle())
    state = coach_agent_runs._state_after_success(
        session["state"], contract, _analysis_bundle(),
    )
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )

    selected = await coach_agent_runs._prepare_session_for_user_input(
        owner_id, session, "我这次更明显是张力介入",
    )

    assert selected["state"]["primary_candidate"]["label"] == "我先从张力介入这个方向查起"
    assert selected["state"]["alternatives"] == [{
        "label": "也可能和速度匹配时机有关",
        "source_refs": ["context:teaching-issue"],
    }]
    assert selected["state"]["cue"] == "看到目标减速时，让自己的移动也开始减速"

    unchanged = coach_agent_runs._promote_explicit_candidate(
        selected["state"], "速度匹配时机和张力介入都有",
    )
    assert unchanged == selected["state"]

    for negated in ("不是速度匹配时机", "速度匹配时机没感觉"):
        assert coach_agent_runs._promote_explicit_candidate(
            selected["state"], negated,
        ) == selected["state"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "不是速度匹配时机，我说不上来，可能是手臂太僵。",
        "手臂发僵，和你刚才那两个说法不太一样。",
    ],
)
async def test_denied_or_ambiguous_candidate_returns_to_one_clarification_not_teaching(reply):
    owner_id = "teaching-candidate-clarification-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    state = coach_agent_runs._state_after_success(
        session["state"],
        coach_agent_runs._teaching_contract(session, _analysis_bundle()),
        _analysis_bundle(),
    )
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )

    clarified = await coach_agent_runs._prepare_session_for_user_input(
        owner_id,
        session,
        reply,
    )
    contract = coach_agent_runs._teaching_contract(clarified, {"contexts": []})
    advanced = coach_agent_runs._state_after_success(
        clarified["state"], contract, {"contexts": []},
    )

    assert clarified["state"]["phase"] == "intake"
    assert contract["question_kind"] == "discriminator"
    assert "速度匹配时机" in contract["question"]
    assert advanced["phase"] == "hypothesize"


@pytest.mark.asyncio
async def test_candidate_clarification_still_uses_a_teaching_turn_without_new_analysis(
    monkeypatch,
):
    owner_id = "teaching-candidate-clarification-route-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    state = coach_agent_runs._state_after_success(
        session["state"],
        coach_agent_runs._teaching_contract(session, _analysis_bundle()),
        _analysis_bundle(),
    )
    await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    captured = []

    async def build_bundle(_thread_id, _context_refs):
        return {"contexts": []}, []

    async def execute(**kwargs):
        captured.append(kwargs["teaching_turn"])
        return {
            "status": "succeeded",
            "reply": "我再确认一下你更接近哪一个方向。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    run = await coach_agent_runs.create_run(
        owner_id,
        "不是速度匹配时机，我觉得可能是手臂太僵。",
        context_refs=None,
    )
    await coach_agent_runs._tasks[run["run_ref"]]

    assert captured[0] is not None
    assert captured[0]["phase"] == "intake"
    assert "速度匹配时机" in captured[0]["question"]


@pytest.mark.asyncio
async def test_existing_lesson_fields_override_new_bundle_and_unsafe_ratio_fails_closed():
    session = await teaching_session_store.get_or_create_primary_session(
        "teaching-existing-lesson-owner",
    )
    state = dict(session["state"])
    state.update({
        "observation": {"summary": "用户已确认的观察", "source_refs": []},
        "primary_candidate": {"label": "待验证候选：用户反馈", "source_refs": []},
        "cue": "保持已经确认的提示",
        "changed_variable": "注意点",
    })
    session = {**session, "state": state}
    bundle = _analysis_bundle()
    bundle["contexts"][0]["projection"]["diagnosis"]["summary"]["target_time_ratio"].pop("definition")

    contract = coach_agent_runs._teaching_contract(session, bundle)

    assert contract["observation"] == "用户已确认的观察"
    assert contract["primary_candidate"] == "我先从用户反馈这个方向查起"
    assert contract["cue"] == "保持已经确认的提示"
    assert contract["ratio_sources"] == []

    derived = coach_agent_runs._hydrate_teaching_state(
        teaching_session_store._initial_state(), _analysis_bundle(),
    )
    stale_session = {**session, "state": derived}
    stale_contract = coach_agent_runs._teaching_contract(
        stale_session, {"contexts": []},
    )
    assert stale_contract["observation"] == "目标减速后出现重复修正"
    assert stale_contract["primary_candidate"] == "我先从速度匹配时机这个方向查起"
    assert stale_contract["cue"] == "看到目标减速时，让自己的移动也开始减速"
    assert stale_contract["retest"]["intent"] == "immediate_matched"


def test_retest_ready_advances_only_with_a_bounded_retest_intent():
    state = teaching_session_store._initial_state()
    state.update({
        "phase": "retest_ready",
        "active_item_ref": "plan-item:teaching-retest",
        "retest_intent": "immediate_matched",
    })
    session = {
        "session_ref": "teaching_session:0123456789abcdef0123456789abcdef",
        "version": 4,
        "state": state,
    }
    contract = coach_agent_runs._teaching_contract(session, {"contexts": []})

    advanced = coach_agent_runs._state_after_success(state, contract, {"contexts": []})

    assert advanced["phase"] == "await_retest_confirmation"


@pytest.mark.asyncio
async def test_refusal_pauses_and_discomfort_stops_without_provider_inference():
    refused = await teaching_session_store.get_or_create_primary_session(
        "teaching-refusal-owner",
    )
    stopped = await teaching_session_store.get_or_create_primary_session(
        "teaching-discomfort-owner",
    )
    comfortable = await teaching_session_store.get_or_create_primary_session(
        "teaching-no-discomfort-owner",
    )

    refused = await coach_agent_runs._prepare_session_for_user_input(
        "teaching-refusal-owner", refused, "今天先不练了",
    )
    stopped = await coach_agent_runs._prepare_session_for_user_input(
        "teaching-discomfort-owner", stopped, "手开始发麻而且有点无力",
    )
    comfortable = await coach_agent_runs._prepare_session_for_user_input(
        "teaching-no-discomfort-owner", comfortable, "现在没有疼痛",
    )

    assert refused["state"]["phase"] == "paused"
    assert refused["state"]["pause_reason"] == "user_refused"
    assert stopped["state"]["phase"] == "stopped_for_discomfort"
    assert stopped["state"]["pause_reason"] == "discomfort"
    assert comfortable["state"]["phase"] == "intake"


@pytest.mark.asyncio
async def test_acceptance_goes_directly_to_practice_and_clear_confusion_gets_one_reexplanation(monkeypatch):
    phases: list[str] = []
    commands: list[str | None] = []

    async def execute(**kwargs):
        phases.append(kwargs["teaching_turn"]["phase"])
        commands.append(kwargs["teaching_turn"]["allowed_command"])
        return {
            "status": "succeeded",
            "reply": "继续当前这一步。",
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    async def session_at_phase(owner_id: str, phase: str) -> dict:
        session = await teaching_session_store.get_or_create_primary_session(owner_id)
        state = dict(session["state"])
        state.update({
            "phase": phase,
            "observation": {"summary": "目标减速后出现重复修正", "source_refs": []},
            "primary_candidate": {"label": "我先从速度匹配时机这个方向查起", "source_refs": []},
            "cue": "目标减速时，我也跟着减速",
            "changed_variable": "注意点",
            "retest_intent": "immediate_matched",
        })
        return await teaching_session_store.replace_state(
            owner_id, session["session_ref"], session["version"], state,
        )

    await session_at_phase("teaching-direct-practice-owner", "teach")
    teaching = await coach_agent_runs.create_run(
        "teaching-direct-practice-owner", "继续", context_refs=None,
    )
    await coach_agent_runs._tasks[teaching["run_ref"]]
    ready = await teaching_session_store.get_or_create_primary_session(
        "teaching-direct-practice-owner",
    )
    accepted = await coach_agent_runs.create_run(
        "teaching-direct-practice-owner", "明白了，开始吧", context_refs=None,
    )
    await coach_agent_runs._tasks[accepted["run_ref"]]

    assert ready["state"]["phase"] == "practice_ready"
    assert phases[:2] == ["teach", "practice_ready"]
    assert commands[:2] == [None, None]
    assert "await_teach_back" not in phases

    await session_at_phase("teaching-one-clarification-owner", "practice_ready")
    clarification = await coach_agent_runs.create_run(
        "teaching-one-clarification-owner", "所以我要把手绷紧吗?", context_refs=None,
    )
    await coach_agent_runs._tasks[clarification["run_ref"]]
    clarified = await teaching_session_store.get_or_create_primary_session(
        "teaching-one-clarification-owner",
    )
    continue_to_practice = await coach_agent_runs.create_run(
        "teaching-one-clarification-owner", "好，开始吧", context_refs=None,
    )
    await coach_agent_runs._tasks[continue_to_practice["run_ref"]]

    assert clarified["state"]["phase"] == "practice_ready"
    assert phases[-2:] == ["teach", "practice_ready"]

    await session_at_phase("teaching-legacy-back-owner", "await_teach_back")
    legacy = await coach_agent_runs.create_run(
        "teaching-legacy-back-owner", "好，开始吧", context_refs=None,
    )
    await coach_agent_runs._tasks[legacy["run_ref"]]
    assert phases[-1] == "practice_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion_status", "expected_phase", "expected_item_status"),
    [
        ("partial", "retest_ready", "active"),
        ("skipped", "paused", "planned"),
    ],
)
async def test_confirmed_plan_item_and_execution_advance_teaching_phases(
    completion_status,
    expected_phase,
    expected_item_status,
):
    owner_id = f"teaching-confirmed-item-{completion_status}-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    draft = await coach_commands.execute_product_command(
        owner_id,
        {
            "command_name": "training_plan.generate_draft",
            "parameters": {
                "plan_payload": _plan_payload(),
                "evidence_refs": ["analysis:42"],
                "verification_targets": _verification_targets(),
            },
            "idempotency_key": f"teaching-confirmed-item-plan-{completion_status}",
        },
        authorization_source="explicit_user_request",
        thread_id=session["thread_id"],
    )
    command = {
        "command_name": "training_plan.item.add",
        "parameters": {
            "plan_ref": draft["result_ref"],
            "item_payload": _item_payload(),
        },
        "idempotency_key": f"teaching-confirmed-item-{completion_status}",
    }
    pending = await coach_commands.execute_product_command(
        owner_id,
        command,
        authorization_source="coach_inferred",
        thread_id=session["thread_id"],
    )
    state = dict(session["state"])
    state.update({
        "phase": "practice_ready",
        "observation": {"summary": "重复修正", "source_refs": ["context:teaching-issue"]},
        "primary_candidate": {
            "label": "我先从速度匹配时机这个方向查起",
            "source_refs": ["context:teaching-issue"],
        },
        "alternatives": [{
            "label": "也可能和张力介入有关",
            "source_refs": ["context:teaching-issue"],
        }],
        "cue": "看到目标减速时，让自己的移动也开始减速",
        "changed_variable": "注意点",
        "pending_confirmation_ref": pending["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    confirmed = await coach_commands.execute_product_command(
        owner_id,
        {
            **command,
            "confirmation_ref": pending["confirmation"]["confirmation_ref"],
        },
        authorization_source="confirmed",
        thread_id=session["thread_id"],
    )

    reconciled = await coach_agent_runs._reconcile_teaching_session(owner_id, session)

    assert confirmed["status"] == "succeeded"
    assert reconciled["state"]["phase"] == "await_execution_confirmation"
    assert reconciled["state"]["active_item_ref"] == confirmed["result_ref"]
    assert reconciled["state"]["pending_confirmation_ref"] is None

    execution_command = {
        "command_name": "training_plan.execution.record",
        "parameters": {
            "item_ref": confirmed["result_ref"],
            "scenario_ref": "scenario:tracking.smoothbot@1",
            "run_refs": ["run:42"],
            "planned_dose": {"amount": 3, "unit": "runs"},
            "completed_dose": {
                "amount": 0 if completion_status == "skipped" else 2,
                "unit": "runs",
            },
            "completion_status": completion_status,
            "user_feedback": (
                "这组里张力介入更明显"
                if completion_status == "partial"
                else "The cue was manageable."
            ),
        },
        "idempotency_key": f"teaching-confirmed-execution-{completion_status}",
    }
    pending_execution = await coach_commands.execute_product_command(
        owner_id,
        execution_command,
        authorization_source="coach_inferred",
        thread_id=reconciled["thread_id"],
    )
    execution_state = dict(reconciled["state"])
    execution_state.update({
        "pending_confirmation_ref": pending_execution["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    execution_session = await teaching_session_store.replace_state(
        owner_id,
        reconciled["session_ref"],
        reconciled["version"],
        execution_state,
    )
    confirmed_execution = await coach_commands.execute_product_command(
        owner_id,
        {
            **execution_command,
            "confirmation_ref": pending_execution["confirmation"]["confirmation_ref"],
        },
        authorization_source="confirmed",
        thread_id=reconciled["thread_id"],
    )
    await training_plan_store.record_user_execution(
        owner_id,
        confirmed["result_ref"],
        scenario_ref="scenario:tracking.smoothbot@1",
        run_refs=["run:43"],
        planned_dose={"amount": 3, "unit": "runs"},
        completed_dose={"amount": 0, "unit": "runs"},
        completion_status="skipped",
        user_feedback="A later unrelated execution row.",
    )

    after_execution = await coach_agent_runs._reconcile_teaching_session(
        owner_id, execution_session,
    )

    assert confirmed_execution["status"] == "succeeded"
    assert after_execution["state"]["phase"] == expected_phase
    assert after_execution["state"]["active_item_ref"] == confirmed["result_ref"]
    if completion_status == "partial":
        assert after_execution["state"]["primary_candidate"]["label"] == (
            "我先从张力介入这个方向查起"
        )
        assert after_execution["state"]["cue"] == (
            "看到目标减速时，让自己的移动也开始减速"
        )
    confirmed_item = next(
        row
        for row in await training_plan_store.list_plan_items(owner_id, draft["result_ref"])
        if row["item_ref"] == confirmed["result_ref"]
    )
    assert confirmed_item["status"] == expected_item_status
    history = await _item_status_history(owner_id, confirmed["result_ref"])
    if completion_status == "skipped":
        assert history == [("planned", None)]
    else:
        assert history[-1] == (
            "active",
            "coach_teaching_item_status.v1:confirmed_execution",
        )
    replayed = await coach_agent_runs._reconcile_teaching_session(owner_id, after_execution)
    assert replayed == after_execution
    assert await _item_status_history(owner_id, confirmed["result_ref"]) == history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "comparability",
        "result",
        "limitations",
        "expected_comparability",
        "expected_decision",
        "expected_status",
    ),
    [
        ("comparable", "coach_retest_outcome.v1:improved", ["confirmed learner fact"], "comparable", "retain", "active"),
        ("comparable", "coach_retest_outcome.v1:unchanged", ["confirmed learner fact"], "comparable", "lower", "planned"),
        (
            "comparable",
            "coach_retest_outcome.v1:mixed_or_inconclusive",
            ["confirmed learner fact"],
            "comparable",
            "lower",
            "planned",
        ),
        (
            "comparable",
            "coach_retest_outcome.v1:mixed_or_inconclusive",
            ["metric_change_policy_missing"],
            "comparable",
            None,
            "active",
        ),
        ("comparable", "coach_retest_outcome.v1:worsened", ["confirmed learner fact"], "comparable", "reject", "cancelled"),
        (
            "not_comparable",
            "coach_retest_outcome.v1:mixed_or_inconclusive",
            ["different settings"],
            "not_comparable",
            None,
            "active",
        ),
        (
            "unavailable",
            "coach_retest_outcome.v1:mixed_or_inconclusive",
            ["analysis unavailable"],
            "unresolved",
            None,
            "active",
        ),
        ("comparable", "legacy free-text result", ["legacy fact"], "comparable", None, "active"),
    ],
)
async def test_confirmed_retest_maps_only_versioned_outcomes(
    comparability,
    result,
    limitations,
    expected_comparability,
    expected_decision,
    expected_status,
):
    owner_id = (
        "teaching-retest-map-"
        + result.rsplit(":", 1)[-1].replace("_", "-")
        + comparability
        + limitations[0].replace("_", "-").replace(" ", "-")
    )
    session, item = await _session_with_retest_item(owner_id)
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": item["item_ref"],
            "kind": "matched",
            "expected_metric_ref": "metric:tracking-error@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:43"],
            "comparability": comparability,
            "result": result,
            "limitations": limitations,
        },
        "idempotency_key": "confirmed-retest-" + owner_id,
    }
    pending = await coach_commands.execute_product_command(
        owner_id,
        command,
        authorization_source="coach_inferred",
        thread_id=session["thread_id"],
    )
    state = dict(session["state"])
    state.update({
        "pending_confirmation_ref": pending["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    confirmed = await coach_commands.execute_product_command(
        owner_id,
        {**command, "confirmation_ref": pending["confirmation"]["confirmation_ref"]},
        authorization_source="confirmed",
        thread_id=session["thread_id"],
    )

    reconciled = await coach_agent_runs._reconcile_teaching_session(owner_id, session)

    assert confirmed["status"] == "succeeded"
    assert reconciled["state"]["phase"] == "revise"
    assert reconciled["state"]["retest_comparability"] == expected_comparability
    assert reconciled["state"]["revision_decision"] == expected_decision
    assert await _item_status(owner_id, item) == expected_status
    history = await _item_status_history(owner_id, item["item_ref"])
    if expected_decision in {"lower", "reject"}:
        assert history[-1] == (
            expected_status,
            f"coach_teaching_revision.v1:{expected_decision}",
        )
    replayed = await coach_agent_runs._reconcile_teaching_session(owner_id, reconciled)
    assert replayed == reconciled
    assert await _item_status_history(owner_id, item["item_ref"]) == history


@pytest.mark.asyncio
async def test_confirmed_retest_uses_exact_fact_ref_instead_of_latest_item_row():
    owner_id = "teaching-retest-exact-fact-owner"
    session, item = await _session_with_retest_item(owner_id)
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": item["item_ref"],
            "kind": "matched",
            "expected_metric_ref": "metric:tracking-error@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:43"],
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:improved",
            "limitations": ["confirmed learner fact"],
        },
        "idempotency_key": "confirmed-retest-exact-fact",
    }
    pending = await coach_commands.execute_product_command(
        owner_id,
        command,
        authorization_source="coach_inferred",
        thread_id=session["thread_id"],
    )
    state = dict(session["state"])
    state.update({
        "pending_confirmation_ref": pending["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    await coach_commands.execute_product_command(
        owner_id,
        {**command, "confirmation_ref": pending["confirmation"]["confirmation_ref"]},
        authorization_source="confirmed",
        thread_id=session["thread_id"],
    )
    await training_plan_store.record_retest(
        owner_id,
        item["item_ref"],
        kind="matched",
        expected_metric_ref="metric:tracking-error@v1",
        expected_direction="lower_better",
        analysis_refs=["analysis:44"],
        comparability="not_comparable",
        result="legacy later row",
        limitations=["different settings"],
    )

    reconciled = await coach_agent_runs._reconcile_teaching_session(owner_id, session)

    assert reconciled["state"]["retest_comparability"] == "comparable"
    assert reconciled["state"]["revision_decision"] == "retain"


@pytest.mark.parametrize(
    ("comparability", "result", "limitations"),
    [
        ("comparable", "coach_retest_outcome.v1:unchanged", ["confirmed learner fact"]),
        ("comparable", "coach_retest_outcome.v1:worsened", ["confirmed learner fact"]),
        ("comparable", "coach_retest_outcome.v1:mixed_or_inconclusive", ["confirmed learner fact"]),
        ("not_comparable", "coach_retest_outcome.v1:improved", ["confirmed learner fact"]),
        ("comparable", "coach_retest_outcome.v1:improved", ["metric_change_policy_missing"]),
    ],
)
def test_viscose_progression_requires_confirmed_improved_comparable_retest(
    monkeypatch: pytest.MonkeyPatch,
    comparability: str,
    result: str,
    limitations: list[str],
) -> None:
    from webapp.backend import benchmark_catalog

    item = {**_item_payload(), "scenario_profile_ref": "scenario:viscose.easier@1"}
    monkeypatch.setattr(
        benchmark_catalog,
        "pair_for_scenario_profile",
        lambda profile_ref: {
            "easier": {"scenario_profile_ref": profile_ref, "scenario_name": "Easier exact"},
            "medium": {"scenario_profile_ref": None, "scenario_name": "Medium paired"},
        },
    )

    assert coach_agent_runs._next_recommendation_for_confirmed_retest(
        item,
        comparability=comparability,
        result=result,
        limitations=limitations,
    ) is None


def test_viscose_progression_projects_paired_medium_as_stress_test_not_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webapp.backend import benchmark_catalog

    item = {**_item_payload(), "scenario_profile_ref": "scenario:viscose.easier@1"}
    monkeypatch.setattr(
        benchmark_catalog,
        "pair_for_scenario_profile",
        lambda profile_ref: {
            "easier": {"scenario_profile_ref": profile_ref, "scenario_name": "Easier exact"},
            "medium": {"scenario_profile_ref": None, "scenario_name": "Medium paired"},
        },
    )

    recommendation = coach_agent_runs._next_recommendation_for_confirmed_retest(
        item,
        comparability="comparable",
        result="coach_retest_outcome.v1:improved",
        limitations=["confirmed learner fact"],
    )

    assert recommendation == {
        "scenario_name": "Medium paired",
        "scenario_profile_ref": None,
        "message": (
            "下一项可以尝试 Medium paired（Medium），把它当作更难的压力测试和新的基线；"
            "它本身不证明迁移。"
        ),
    }


@pytest.mark.parametrize(
    "pair",
    [
        None,
        {
            "easier": {"scenario_profile_ref": "scenario:other.easier@1"},
            "medium": {"scenario_profile_ref": None, "scenario_name": "Medium paired"},
        },
    ],
)
def test_viscose_progression_requires_catalog_pair_for_the_current_exact_easier_profile(
    monkeypatch: pytest.MonkeyPatch,
    pair: dict | None,
) -> None:
    from webapp.backend import benchmark_catalog

    monkeypatch.setattr(
        benchmark_catalog,
        "pair_for_scenario_profile",
        lambda _profile_ref: pair,
    )

    assert coach_agent_runs._next_recommendation_for_confirmed_retest(
        {**_item_payload(), "scenario_profile_ref": "scenario:viscose.easier@1"},
        comparability="comparable",
        result="coach_retest_outcome.v1:improved",
        limitations=["confirmed learner fact"],
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_fact", ["failed", "foreign"])
async def test_failed_or_foreign_retest_fact_does_not_mutate_item(invalid_fact):
    owner_id = f"teaching-retest-{invalid_fact}-fact-owner"
    session, item = await _session_with_retest_item(owner_id)
    idempotency_key = f"confirmed-retest-{invalid_fact}-fact"
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": item["item_ref"],
            "kind": "matched",
            "expected_metric_ref": "metric:tracking-error@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:43"],
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:worsened",
            "limitations": ["confirmed learner fact"],
        },
        "idempotency_key": idempotency_key,
    }
    pending = await coach_commands.execute_product_command(
        owner_id,
        command,
        authorization_source="coach_inferred",
        thread_id=session["thread_id"],
    )
    state = dict(session["state"])
    state.update({
        "pending_confirmation_ref": pending["confirmation"]["confirmation_ref"],
        "pause_reason": "awaiting_confirmation",
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    await coach_commands.execute_product_command(
        owner_id,
        {**command, "confirmation_ref": pending["confirmation"]["confirmation_ref"]},
        authorization_source="confirmed",
        thread_id=session["thread_id"],
    )

    if invalid_fact == "foreign":
        _, foreign_item = await _session_with_retest_item("teaching-retest-foreign-source-owner")
        foreign = await training_plan_store.record_retest(
            "teaching-retest-foreign-source-owner",
            foreign_item["item_ref"],
            kind="matched",
            expected_metric_ref="metric:tracking-error@v1",
            expected_direction="lower_better",
            analysis_refs=["analysis:44"],
            comparability="comparable",
            result="coach_retest_outcome.v1:worsened",
            limitations=["foreign owner fact"],
        )
        invalid_result = {
            "status": "succeeded",
            "result_ref": foreign["retest_ref"],
        }
    else:
        invalid_result = {"status": "failed", "result_ref": None}

    conn = await get_conn()
    await conn.execute(
        "UPDATE coach_command_idempotency SET result_json=? "
        "WHERE owner_id=? AND command_name='training_plan.retest.record' AND idempotency_key=?",
        (json.dumps(invalid_result), owner_id, idempotency_key),
    )
    await conn.commit()
    history = await _item_status_history(owner_id, item["item_ref"])

    with pytest.raises(coach_agent_runs.AgentRunError) as error:
        await coach_agent_runs._reconcile_teaching_session(owner_id, session)

    assert error.value.code == "teaching_retest_missing"
    assert await _item_status(owner_id, item) == "active"
    assert await _item_status_history(owner_id, item["item_ref"]) == history


@pytest.mark.asyncio
async def test_user_can_retry_a_retest_that_did_not_produce_a_decision():
    owner_id = "teaching-retest-retry-owner"
    session, _ = await _session_with_retest_item(owner_id)
    state = dict(session["state"])
    state.update({
        "phase": "revise",
        "retest_comparability": "not_comparable",
        "revision_decision": None,
    })
    session = await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )

    retried = await coach_agent_runs._prepare_session_for_user_input(
        owner_id, session, "好，按原条件重新测",
    )

    assert retried["state"]["phase"] == "retest_ready"
    assert retried["state"]["retest_comparability"] == "unresolved"
    assert retried["state"]["revision_decision"] is None


@pytest.mark.asyncio
async def test_retest_retry_accepts_brief_consent_but_never_negated_language():
    consent_owner = "teaching-retest-brief-consent-owner"
    consent, _ = await _session_with_retest_item(consent_owner)
    consent_state = dict(consent["state"])
    consent_state.update({
        "phase": "revise",
        "retest_comparability": "unresolved",
        "revision_decision": None,
    })
    consent = await teaching_session_store.replace_state(
        consent_owner, consent["session_ref"], consent["version"], consent_state,
    )

    accepted = await coach_agent_runs._prepare_session_for_user_input(
        consent_owner, consent, "好",
    )
    assert accepted["state"]["phase"] == "retest_ready"

    refusal_owner = "teaching-retest-negated-owner"
    refused, _ = await _session_with_retest_item(refusal_owner)
    refused_state = dict(refused["state"])
    refused_state.update({
        "phase": "revise",
        "retest_comparability": "not_comparable",
        "revision_decision": None,
    })
    refused = await teaching_session_store.replace_state(
        refusal_owner, refused["session_ref"], refused["version"], refused_state,
    )

    rejected = await coach_agent_runs._prepare_session_for_user_input(
        refusal_owner, refused, "别再测了",
    )
    assert rejected["state"]["phase"] == "paused"
    assert rejected["state"]["pause_reason"] == "user_refused"

@pytest.mark.asyncio
async def test_stale_teaching_release_never_persists_assistant_reply(monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def execute(**_kwargs):
        started.set()
        await finish.wait()
        return {
            "status": "succeeded",
            "reply": "This reply must not be persisted.",
            "tool_events": [],
            "error": None,
        }

    async def stale_release(*_args, **_kwargs):
        raise teaching_session_store.TeachingSessionConflictError("stale teaching state")

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)
    run = await coach_agent_runs.create_run(
        "teaching-stale-release-owner", "开始带练", context_refs=None,
    )
    await started.wait()
    monkeypatch.setattr(teaching_session_store, "release_active_run", stale_release)
    finish.set()
    await coach_agent_runs._tasks[run["run_ref"]]

    detail = await coach_agent_runs.get_run(
        "teaching-stale-release-owner", run["run_ref"],
    )
    session = await teaching_session_store.get_or_create_primary_session(
        "teaching-stale-release-owner",
    )
    messages = await coach_store.load_messages(session["thread_id"])

    assert detail["status"] == "failed"
    assert detail["error"]["code"] == "teaching_session_conflict"
    assert [message["role"] for message in messages] == ["user"]


@pytest.mark.asyncio
async def test_concurrent_teaching_runs_dispatch_only_the_claimed_turn(monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()
    calls: list[dict] = []

    async def execute(**kwargs):
        calls.append(kwargs)
        started.set()
        await finish.wait()
        return {"status": "succeeded", "reply": "继续", "tool_events": [], "error": None}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    first = await coach_agent_runs.create_run(
        "teaching-agent-owner",
        "开始带练",
        context_refs=None,
    )
    await started.wait()
    second = await coach_agent_runs.create_run(
        "teaching-agent-owner",
        "同时再发一条",
        context_refs=None,
    )

    assert second["status"] == "failed"
    assert second["error"]["code"] == "teaching_session_busy"
    assert len(calls) == 1
    assert calls[0]["teaching_turn"]["schema_version"] == "coach_teaching_turn.v1"

    task = coach_agent_runs._tasks[first["run_ref"]]
    finish.set()
    await task


@pytest.mark.asyncio
async def test_retry_reuses_the_parent_user_message_and_contract(monkeypatch):
    calls: list[dict] = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "status": "failed",
                "reply": None,
                "tool_events": [],
                "error": {
                    "domain": "model",
                    "code": "transient",
                    "message": "try again",
                    "retryable": True,
                },
            }
        return {"status": "succeeded", "reply": "继续", "tool_events": [], "error": None}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    first = await coach_agent_runs.create_run(
        "teaching-retry-owner", "重试同一条", context_refs=None,
    )
    await coach_agent_runs._tasks[first["run_ref"]]
    retry = await coach_agent_runs.retry_run("teaching-retry-owner", first["run_ref"])
    assert retry is not None
    await coach_agent_runs._tasks[retry["run_ref"]]

    assert len(calls) == 2
    assert calls[0]["user_message_id"] == calls[1]["user_message_id"]
    assert calls[0]["teaching_turn"] == calls[1]["teaching_turn"]


@pytest.mark.asyncio
@pytest.mark.parametrize("teaching_note", ["teaching_fallback", "teaching_hold"])
async def test_teaching_fallback_or_hold_releases_without_advancing_or_clearing_pause(
    monkeypatch,
    teaching_note,
):
    async def execute(**kwargs):
        return {
            "status": "succeeded",
            "reply": "请先确认这一组的注意点。",
            "notes": [teaching_note],
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)
    run = await coach_agent_runs.create_run(
        "teaching-fallback-owner", "继续", context_refs=None,
    )
    await coach_agent_runs._tasks[run["run_ref"]]

    session = await teaching_session_store.get_or_create_primary_session("teaching-fallback-owner")
    assert session["state"]["phase"] == "intake"
    assert session["active_run_ref"] is None

    paused_run = await coach_agent_runs.create_run(
        "teaching-fallback-paused-owner", "今天先不练了", context_refs=None,
    )
    await coach_agent_runs._tasks[paused_run["run_ref"]]
    paused = await teaching_session_store.get_or_create_primary_session(
        "teaching-fallback-paused-owner",
    )
    assert paused["state"]["phase"] == "paused"
    assert paused["state"]["pause_reason"] == "user_refused"

    stopped_run = await coach_agent_runs.create_run(
        "teaching-fallback-stopped-owner", "手开始发麻", context_refs=None,
    )
    await coach_agent_runs._tasks[stopped_run["run_ref"]]
    stopped = await teaching_session_store.get_or_create_primary_session(
        "teaching-fallback-stopped-owner",
    )
    assert stopped["state"]["phase"] == "stopped_for_discomfort"
    assert stopped["state"]["pause_reason"] == "discomfort"

    revise = await teaching_session_store.get_or_create_primary_session(
        "teaching-fallback-revise-owner",
    )
    revise_state = dict(revise["state"])
    revise_state.update({
        "phase": "revise",
        "active_item_ref": "plan-item:guided-loop",
        "retest_intent": "immediate_matched",
        "retest_comparability": "not_comparable",
        "revision_decision": None,
    })
    await teaching_session_store.replace_state(
        "teaching-fallback-revise-owner",
        revise["session_ref"],
        revise["version"],
        revise_state,
    )
    revise_run = await coach_agent_runs.create_run(
        "teaching-fallback-revise-owner", "看这次结果", context_refs=None,
    )
    await coach_agent_runs._tasks[revise_run["run_ref"]]
    preserved_revise = await teaching_session_store.get_or_create_primary_session(
        "teaching-fallback-revise-owner",
    )
    assert preserved_revise["state"]["phase"] == "revise"
    assert preserved_revise["state"]["retest_comparability"] == "not_comparable"


@pytest.mark.asyncio
async def test_grounded_intake_fallback_advances_once_without_tools(monkeypatch):
    owner_id = "teaching-grounded-fallback-owner"
    calls = []

    async def build_bundle(_thread_id, _context_refs):
        return _analysis_bundle(), []

    async def execute(**kwargs):
        calls.append(kwargs)
        return {
            "status": "succeeded",
            "reply": "本地安全回复。",
            "notes": ["teaching_fallback"],
            "tool_events": [],
            "error": None,
        }

    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "execute_turn", execute)

    first = await coach_agent_runs.create_run(owner_id, "开始吧", context_refs=None)
    await coach_agent_runs._tasks[first["run_ref"]]
    after_first = await teaching_session_store.get_or_create_primary_session(owner_id)

    second = await coach_agent_runs.create_run(owner_id, "继续", context_refs=None)
    await coach_agent_runs._tasks[second["run_ref"]]
    after_second = await teaching_session_store.get_or_create_primary_session(owner_id)

    assert calls[0]["teaching_turn"]["phase"] == "intake"
    assert calls[0]["teaching_turn"]["allowed_command"] is None
    assert calls[1]["teaching_turn"]["phase"] == "hypothesize"
    assert after_first["state"]["phase"] == "hypothesize"
    assert after_second["state"]["phase"] == "hypothesize"


@pytest.mark.parametrize(
    ("observation", "candidate"),
    [
        (None, "我先从速度匹配时机这个方向查起"),
        ("目标减速后出现重复修正", ""),
    ],
)
def test_teaching_fallback_without_a_grounded_observation_or_candidate_cannot_advance(
    observation,
    candidate,
):
    contract = {
        "phase": "intake",
        "observation": observation,
        "primary_candidate": candidate,
        "allowed_command": None,
        "confirmation_intent": "none",
    }

    assert not coach_agent_runs._may_advance_teaching_fallback(contract, [])


@pytest.mark.asyncio
async def test_no_grounded_issue_contract_is_terminal_and_never_accepts_free_text_as_a_candidate():
    owner_id = "teaching-no-lesson-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)

    contract = coach_agent_runs._teaching_contract(session, {"contexts": []})
    advanced = coach_agent_runs._state_after_success(session["state"], contract, {"contexts": []})
    after_free_text = await coach_agent_runs._prepare_session_for_user_input(
        owner_id, session, "我觉得是手紧，帮我安排练习",
    )

    assert contract["question_kind"] == "discriminator"
    assert contract["question"] == (
        "这次分析还没看出一个明确问题。你自己最想先解决哪种失误或哪段动作?"
    )
    assert contract["primary_candidate"] is None
    assert advanced["phase"] == "intake"
    assert after_free_text["state"]["primary_candidate"] is None


@pytest.mark.asyncio
async def test_analysis_soft_start_is_idempotent_and_writes_no_user_message(monkeypatch):
    owner_id = "analysis-soft-start-owner"
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:401",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    bundle["contexts"][0]["projection"]["scenario"]["analyzer_refs"] = [
        "static_clicking.v1",
    ]
    bundle["contexts"][0]["kind"] = "analysis"
    bundle["contexts"][0]["target_ref"] = "analysis:401"
    snapshot = {
        "schema_version": "coach_context_ref.v1",
        "context_ref": bundle["contexts"][0]["context_ref"],
        "kind": "analysis",
        "status": "active",
        "label": "analysis:401",
        "analysis_ref": "analysis:401",
        "comparison_analysis_ref": None,
        "target_ref": None,
        "time_range_ms": None,
        "attached_at": "2026-08-06 00:00:00",
        "detached_at": None,
        "deleted_at": None,
    }

    async def attach(owner, thread_id, **kwargs):
        assert owner == owner_id
        assert thread_id > 0
        assert kwargs == {"kind": "analysis", "analysis_ref": "analysis:401"}
        return "attached", snapshot

    async def build_bundle(_thread_id, context_refs):
        assert context_refs == [snapshot["context_ref"]]
        return bundle, [snapshot]

    async def profile(_owner_id):
        return {"schema_version": "aiming_profile.v1", "dimensions": []}

    monkeypatch.setattr(coach_agent_runs, "attach_context", attach)
    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(
        coach_agent_runs.aiming_profile_store, "get_profile_snapshot", profile,
    )
    before = await teaching_session_store.get_or_create_primary_session(owner_id)

    results = await asyncio.gather(*[
        coach_agent_runs.create_analysis_soft_start(
            owner_id, analysis_session_id=401,
        )
        for _ in range(6)
    ])

    assert len({item["run_ref"] for item in results}) == 1
    assert all(item["status"] == "succeeded" for item in results)
    assert all(not any(event["type"] == "tool" for event in item["events"]) for item in results)
    messages = await coach_store.load_messages(before["thread_id"])
    assert [message["role"] for message in messages] == ["assistant"]
    assert messages[0]["content"].count("?") + messages[0]["content"].count("？") == 1
    assert "我先看" in messages[0]["content"]
    assert "还不能确定原因" in messages[0]["content"]
    assert "我先说当前最值得处理的问题" not in messages[0]["content"]
    assert "依据是" not in messages[0]["content"]
    assert "先只问一个问题" not in messages[0]["content"]
    after = await teaching_session_store.get_or_create_primary_session(owner_id)
    assert after["state"]["phase"] == before["state"]["phase"] == "intake"
    conn = await get_conn()
    rows = await (await conn.execute(
        "SELECT initiator, trigger_ref, user_message_id FROM coach_agent_runs "
        "WHERE owner_id=?",
        (owner_id,),
    )).fetchall()
    assert [tuple(row) for row in rows] == [("system", "analysis:401", None)]


@pytest.mark.asyncio
async def test_teaching_turn_keeps_profile_counterevidence_after_soft_start():
    owner_id = "teaching-profile-counterevidence-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    bundle = _grounded_plan_bundle(
        analysis_ref="analysis:404",
        scenario_profile_ref="scenario:static.1wall_6targets_small@1",
        signal="reverse_ratio high",
        metric_ref="metric:reverse_ratio",
    )
    bundle["contexts"][0]["projection"]["scenario"]["analyzer_refs"] = [
        "static_clicking.v1",
    ]
    profile = {
        "schema_version": "aiming_profile.v1",
        "dimensions": [{
            "dimension_key": "static_clicking.terminal_control",
            "counterexample_refs": ["analysis:399"],
        }],
    }

    contract = coach_agent_runs._teaching_contract(
        session, bundle, "继续排查", profile=profile,
    )

    assert contract["evidence_strength"] == "supported"
    assert contract["counterevidence_status"] == "observed"
    assert contract["counterevidence"]


@pytest.mark.asyncio
async def test_analysis_soft_start_fails_closed_while_another_problem_is_active(monkeypatch):
    owner_id = "analysis-soft-start-busy-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    state = copy.deepcopy(session["state"])
    state["primary_candidate"] = {
        "label": "可能与当前问题有关",
        "source_refs": ["analysis:400"],
    }
    await teaching_session_store.replace_state(
        owner_id, session["session_ref"], session["version"], state,
    )
    called = False

    async def attach(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("busy soft start must not attach a context")

    monkeypatch.setattr(coach_agent_runs, "attach_context", attach)

    with pytest.raises(coach_agent_runs.AgentRunError) as error:
        await coach_agent_runs.create_analysis_soft_start(
            owner_id, analysis_session_id=402,
        )

    assert error.value.code == "teaching_session_busy"
    assert called is False


@pytest.mark.asyncio
async def test_analysis_soft_start_detaches_a_new_context_when_no_problem_is_grounded(monkeypatch):
    owner_id = "analysis-soft-start-no-problem-owner"
    session = await teaching_session_store.get_or_create_primary_session(owner_id)
    snapshot = {
        "schema_version": "coach_context_ref.v1",
        "context_ref": "context:no-problem",
        "kind": "analysis",
        "status": "active",
        "label": "analysis:403",
        "analysis_ref": "analysis:403",
        "comparison_analysis_ref": None,
        "target_ref": None,
        "time_range_ms": None,
        "attached_at": "2026-08-06 00:00:00",
        "detached_at": None,
        "deleted_at": None,
    }
    detached: list[tuple[str, int, str]] = []

    async def attach(*_args, **_kwargs):
        return "attached", snapshot

    async def build_bundle(_thread_id, _context_refs):
        return {"schema_version": "coach_turn_context.v1", "contexts": []}, [snapshot]

    async def profile(_owner_id):
        return {"schema_version": "aiming_profile.v1", "dimensions": []}

    async def detach(owner, thread_id, context_ref):
        detached.append((owner, thread_id, context_ref))
        return "detached", snapshot

    monkeypatch.setattr(coach_agent_runs, "attach_context", attach)
    monkeypatch.setattr(coach_agent_runs, "build_context_bundle", build_bundle)
    monkeypatch.setattr(coach_agent_runs, "detach_context", detach, raising=False)
    monkeypatch.setattr(
        coach_agent_runs.aiming_profile_store, "get_profile_snapshot", profile,
    )

    with pytest.raises(coach_agent_runs.AgentRunError) as error:
        await coach_agent_runs.create_analysis_soft_start(
            owner_id, analysis_session_id=403,
        )

    assert error.value.code == "no_grounded_problem"
    assert detached == [(owner_id, session["thread_id"], "context:no-problem")]
    assert await coach_store.load_messages(session["thread_id"]) == []
