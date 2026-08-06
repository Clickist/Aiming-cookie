from __future__ import annotations

import copy

import pytest

from webapp.backend import training_plan_store as store
from webapp.backend import aiming_profile_store


PLAN_PAYLOAD = {
    "title": "Stabilize first-shot correction",
    "diagnostic_context": {
        "analysis_refs": ["analysis:run-42"],
        "metric_refs": ["metric:first_shot_correction"],
        "diagnosis_refs": ["diagnosis:overshoot-after-pause"],
    },
    "prescriptions": [
        {
            "scenario": "Sixshot",
            "cue": "Pause, then commit once.",
            "purpose": "Reduce corrective reversal.",
            "target_metric_refs": ["metric:first_shot_correction"],
            "expected_direction": "decrease",
            "source_level": "deterministic_rule",
        },
    ],
}

VERIFICATION_TARGETS = [
    {
        "target_metric": "metric:first_shot_correction",
        "expected_direction": "decrease",
        "comparable_requirements": [
            "same scenario",
            "same sensitivity profile",
        ],
        "retest_after": "after three focused sessions",
        "insufficient_evidence_behavior": "keep the plan unchanged and collect another comparable run",
    },
]


@pytest.mark.asyncio
async def test_draft_save_activate_pause_and_confirmed_replacement_are_owner_scoped():
    first = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    second = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    other_owner = await store.create_draft(
        "owner-b", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )

    assert first["plan_id"].startswith("plan:")
    assert first["version"] == 1
    assert first["version_ref"] == f"{first['plan_id']}:v1"
    assert first["status"] == "draft"
    assert (await store.list_plans("owner-b"))[0]["plan_id"] == other_owner["plan_id"]

    await store.save_plan("owner-a", first["plan_id"])
    await store.save_plan("owner-a", second["plan_id"])
    active = await store.activate_plan("owner-a", first["plan_id"])
    assert active["status"] == "active"

    with pytest.raises(store.ActivePlanReplacementRequired) as replacement:
        await store.activate_plan("owner-a", second["plan_id"])
    assert replacement.value.active_plan_id == first["plan_id"]
    assert replacement.value.target_plan_id == second["plan_id"]

    activated = await store.activate_plan(
        "owner-a", second["plan_id"], replace_active=True,
    )
    assert activated["status"] == "active"
    assert (await store.get_plan("owner-a", first["plan_id"]))["status"] == "paused"
    assert (await store.pause_plan("owner-a", second["plan_id"]))["status"] == "paused"

    with pytest.raises(store.InvalidTransition):
        await store.save_plan("owner-a", first["plan_id"])
    with pytest.raises(store.PlanForbidden):
        await store.get_plan("owner-b", first["plan_id"])

    transitions = await store.list_transitions("owner-a", second["plan_id"])
    assert [(row["event"], row["from_status"], row["to_status"]) for row in transitions] == [
        ("generated", None, "draft"),
        ("saved", "draft", "saved"),
        ("activated", "saved", "active"),
        ("paused", "active", "paused"),
    ]


@pytest.mark.asyncio
async def test_adjust_creates_immutable_incrementing_version_and_review_is_read_only():
    draft = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    await store.save_plan("owner-a", draft["plan_id"])

    revised_payload = copy.deepcopy(PLAN_PAYLOAD)
    revised_payload["prescriptions"][0]["cue"] = "Land once; do not chase the target."
    revised = await store.adjust_plan(
        "owner-a",
        draft["plan_id"],
        revised_payload,
        adjustment_reason="Comparable runs still show a late corrective reversal.",
        evidence_refs=["analysis:run-43", "metric:first_shot_correction"],
        verification_targets=VERIFICATION_TARGETS,
    )

    assert revised["status"] == "saved"
    assert revised["version"] == 2
    assert revised["adjustment_reason"] == "Comparable runs still show a late corrective reversal."
    original = await store.get_plan_version("owner-a", draft["plan_id"], 1)
    assert original["plan_payload"]["prescriptions"][0]["cue"] == "Pause, then commit once."
    assert original["adjustment_reason"] is None
    assert (await store.get_plan_version("owner-a", draft["plan_id"], 2))["evidence_refs"] == [
        "analysis:run-43", "metric:first_shot_correction",
    ]

    before = await store.list_transitions("owner-a", draft["plan_id"])
    review = await store.review_plan("owner-a", draft["plan_id"])
    after = await store.list_transitions("owner-a", draft["plan_id"])
    assert review["version"] == 2
    assert review["status"] == "saved"
    assert before == after


@pytest.mark.asyncio
async def test_plan_payload_and_verification_targets_reject_unsafe_or_unbounded_inputs():
    unsafe_path = copy.deepcopy(PLAN_PAYLOAD)
    unsafe_path["prescriptions"][0]["cue"] = "/Users/private/raw-input.csv"
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft("owner-a", unsafe_path, verification_targets=VERIFICATION_TARGETS)

    unsafe_trace = copy.deepcopy(PLAN_PAYLOAD)
    unsafe_trace["raw_trace"] = [1, 2, 3]
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft("owner-a", unsafe_trace, verification_targets=VERIFICATION_TARGETS)

    unsafe_credential = copy.deepcopy(PLAN_PAYLOAD)
    unsafe_credential["api_key"] = "not-a-real-key"
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft("owner-a", unsafe_credential, verification_targets=VERIFICATION_TARGETS)

    absolute_threshold = copy.deepcopy(VERIFICATION_TARGETS)
    absolute_threshold[0]["absolute_threshold"] = 0.2
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft(
            "owner-a", PLAN_PAYLOAD, verification_targets=absolute_threshold,
        )

    missing_context = {"title": "No deterministic context", "prescriptions": []}
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft(
            "owner-a", missing_context, verification_targets=VERIFICATION_TARGETS,
        )

    unbounded_refs = copy.deepcopy(PLAN_PAYLOAD)
    unbounded_refs["diagnostic_context"]["analysis_refs"] = [
        f"analysis:run-{index}" for index in range(33)
    ]
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft(
            "owner-a", unbounded_refs, verification_targets=VERIFICATION_TARGETS,
        )


@pytest.mark.asyncio
async def test_training_plan_references_enforce_their_declared_kinds():
    mismatched_context = copy.deepcopy(PLAN_PAYLOAD)
    mismatched_context["diagnostic_context"]["analysis_refs"] = [
        "metric:first_shot_correction"
    ]
    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft(
            "owner-a",
            mismatched_context,
            verification_targets=VERIFICATION_TARGETS,
        )

    with pytest.raises(store.InvalidTrainingPlan):
        await store.create_draft(
            "owner-a",
            PLAN_PAYLOAD,
            evidence_refs=["run:42"],
            verification_targets=VERIFICATION_TARGETS,
        )


PLAN_ITEM = {
    "diagnosis_ref": "diagnosis:late-correction@2",
    "knowledge_ref": "knowledge:terminal-control@2",
    "scenario_profile_ref": "scenario:sixshot@1",
    "baseline_metric_ref": "metric:terminal_control",
    "expected_direction": "lower_better",
    "practice_condition": "same sensitivity and target size",
    "cue": "Land once; do not chase the target.",
    "dose_guardrail": "three sets of two minutes",
    "matched_retest_ref": "retest-spec:sixshot-matched@1",
    "near_transfer_retest_ref": "retest-spec:sixshot-size-transfer@1",
    "review_date": "after three completed sessions",
}


@pytest.mark.asyncio
async def test_plan_items_executions_and_both_retest_kinds_are_owner_scoped_append_only():
    draft = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    item = await store.add_plan_item("owner-a", draft["plan_id"], PLAN_ITEM)
    assert item["item_ref"].startswith("plan-item:")
    assert item["plan_revision_ref"] == f"{draft['plan_id']}:v1"
    assert item["status"] == "planned"
    await store.save_plan("owner-a", draft["plan_id"])
    await store.activate_plan("owner-a", draft["plan_id"])
    snapshot = await aiming_profile_store.get_profile_snapshot("owner-a")
    assert snapshot["active_plan_ref"] == draft["plan_id"]
    assert snapshot["next_retest_refs"] == [
        "retest-spec:sixshot-matched@1",
        "retest-spec:sixshot-size-transfer@1",
    ]

    execution = await store.record_user_execution(
        "owner-a",
        item["item_ref"],
        scenario_ref="scenario:sixshot@1",
        run_refs=["run:42"],
        planned_dose={"amount": 6, "unit": "minutes"},
        completed_dose={"amount": 5, "unit": "minutes"},
        completion_status="partial",
        user_feedback="Late corrections appeared after the second set.",
    )
    matched = await store.record_retest(
        "owner-a",
        item["item_ref"],
        kind="matched",
        expected_metric_ref="metric:terminal_control",
        expected_direction="lower_better",
        analysis_refs=["analysis:43"],
        comparability="comparable",
        result="improved",
        limitations=["one comparable retest"],
    )
    transfer = await store.record_retest(
        "owner-a",
        item["item_ref"],
        kind="near_transfer",
        expected_metric_ref="metric:terminal_control",
        expected_direction="lower_better",
        analysis_refs=["analysis:44"],
        comparability="not_comparable",
        result="inconclusive",
        limitations=["target size changed"],
    )

    assert execution["execution_ref"].startswith("plan-execution:")
    assert execution["item_revision_ref"] == item["item_revision_ref"]
    assert matched["kind"] == "matched"
    assert transfer["kind"] == "near_transfer"
    assert len(await store.list_plan_executions("owner-a", item["item_ref"])) == 1
    assert [entry["kind"] for entry in await store.list_retests("owner-a", item["item_ref"])] == [
        "matched", "near_transfer",
    ]

    structured = await store.record_user_execution(
        "owner-a",
        item["item_ref"],
        scenario_ref="scenario:sixshot@1",
        run_refs=["run:43"],
        planned_dose={"amount": 6, "unit": "minutes"},
        completed_dose={"amount": 6, "unit": "minutes"},
        completion_status="completed",
        user_feedback={
            "cue_clarity": "clear",
            "felt_control": "easier",
            "felt_stiffness": "no",
            "fatigue_or_discomfort": "none",
            "willing_to_continue": True,
            "notes": "动作更容易控制",
        },
    )
    assert '"felt_control":"easier"' in structured["user_feedback"]

    with pytest.raises(store.PlanForbidden):
        await store.list_plan_items("owner-b", draft["plan_id"])


@pytest.mark.asyncio
async def test_plan_item_requires_stable_refs_and_user_execution_rejects_llm_writer():
    draft = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    missing = copy.deepcopy(PLAN_ITEM)
    missing.pop("near_transfer_retest_ref")
    with pytest.raises(store.InvalidTrainingPlan):
        await store.add_plan_item("owner-a", draft["plan_id"], missing)

    item = await store.add_plan_item("owner-a", draft["plan_id"], PLAN_ITEM)
    with pytest.raises(store.InvalidTrainingPlan):
        await store.record_user_execution(
            "owner-a",
            item["item_ref"],
            scenario_ref="scenario:sixshot@1",
            run_refs=["run:42"],
            planned_dose={"amount": 6, "unit": "minutes"},
            completed_dose={"amount": 6, "unit": "minutes"},
            completion_status="completed",
            user_feedback="Finished.",
            recorded_by="llm",
        )


@pytest.mark.asyncio
async def test_plan_item_status_changes_are_owner_scoped_and_idempotent():
    draft = await store.create_draft(
        "owner-a", PLAN_PAYLOAD, verification_targets=VERIFICATION_TARGETS,
    )
    item = await store.add_plan_item("owner-a", draft["plan_id"], PLAN_ITEM)

    activated = await store.set_plan_item_status(
        "owner-a",
        item["item_ref"],
        "active",
        reason="coach_teaching_item_status.v1:confirmed_execution",
    )
    replay = await store.set_plan_item_status(
        "owner-a",
        item["item_ref"],
        "active",
        reason="coach_teaching_item_status.v1:confirmed_execution",
    )

    assert activated["status"] == "active"
    assert activated["status_ref"].startswith("plan-item-status:")
    assert replay["status"] == "active"
    assert replay["status_ref"] is None
    with pytest.raises(store.PlanForbidden):
        await store.set_plan_item_status(
            "owner-b",
            item["item_ref"],
            "cancelled",
            reason="coach_teaching_revision.v1:reject",
        )
    assert (await store.list_plan_items("owner-a", draft["plan_id"]))[0]["status"] == "active"
