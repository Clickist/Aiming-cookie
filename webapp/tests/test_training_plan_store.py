from __future__ import annotations

import copy

import pytest

from webapp.backend import training_plan_store as store


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
