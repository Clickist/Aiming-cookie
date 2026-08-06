from __future__ import annotations

import asyncio

import pytest


def _state(*, phase: str = "intake") -> dict:
    return {
        "schema_version": "teaching_session.v1",
        "phase": phase,
        "observation": {"summary": "减速阶段有重复修正", "source_refs": ["analysis:42"]},
        "primary_candidate": {"label": "速度匹配", "source_refs": ["analysis:42"]},
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
    }


def _contract(session_ref: str, version: int) -> dict:
    return {
        "schema_version": "coach_teaching_turn.v1",
        "session_ref": session_ref,
        "session_version": version,
        "phase": "intake",
        "problem_id": None,
        "problem_label": None,
        "evidence_strength": "limited",
        "supporting_evidence": [],
        "counterevidence_status": "not_observed",
        "counterevidence": [],
        "observation": "减速阶段有重复修正",
        "primary_candidate": "可能是速度匹配",
        "alternatives": [],
        "cue": None,
        "changed_variable": None,
        "active_item_ref": None,
        "prepared_plan_ref": None,
        "prepared_item": None,
        "next_recommendation": None,
        "question_kind": "discriminator",
        "question": "这种重复修正是在目标减速后才出现吗？",
        "allowed_command": None,
        "confirmation_intent": "none",
        "retest": {
            "intent": "none",
            "comparability_required": False,
            "comparability": "unresolved",
            "revision_decision": None,
        },
        "ratio_sources": [],
        "approved_dose": None,
        "discriminator": None,
        "soft_start": False,
    }


def test_legacy_contract_normalization_is_stable_on_a_second_validation():
    from webapp.backend import teaching_session_store as store

    contract = _contract("teaching_session:" + "a" * 32, 3)
    for field in (
        "problem_id", "problem_label", "evidence_strength", "supporting_evidence",
        "counterevidence_status", "counterevidence", "discriminator", "soft_start",
    ):
        contract.pop(field)
    contract["primary_candidate"] = "我先从速度匹配这个方向查起"

    normalized = store.validate_contract(
        contract, session_ref=contract["session_ref"], session_version=3,
    )

    assert store.validate_contract(
        normalized, session_ref=contract["session_ref"], session_version=3,
    ) == normalized


def test_legacy_state_gets_empty_learner_context_and_typed_evidence_keeps_refs():
    from webapp.backend import teaching_session_store as store

    legacy_state = store.validate_state(_state())
    assert "learner_context" not in legacy_state
    state = _state()
    state["learner_context"] = None
    normalized_state = store.validate_state(state)
    assert normalized_state["learner_context"] == {
        "player_problem": None,
        "desired_outcome": None,
        "practice_intent": "unspecified",
        "constraints": [],
    }
    assert store.validate_state(normalized_state) == normalized_state

    contract = _contract("teaching_session:" + "b" * 32, 4)
    contract.update({
        "problem_id": "terminal_control",
        "problem_label": "到点后的收尾修正偏多",
        "supporting_evidence": [{
            "kind": "measured",
            "text": "两条规则化观察都出现了反向修正",
            "refs": ["analysis:42", "context:terminal-control"],
        }],
    })
    normalized = store.validate_contract(
        contract, session_ref=contract["session_ref"], session_version=4,
    )
    assert normalized["supporting_evidence"][0] == contract["supporting_evidence"][0]
    assert store.validate_contract(
        normalized, session_ref=contract["session_ref"], session_version=4,
    ) == normalized


def test_learner_context_is_bounded_and_does_not_accept_unknown_intent():
    from webapp.backend import teaching_session_store as store

    state = _state()
    state["learner_context"] = {
        "player_problem": "实战第一枪后总要补一次修正",
        "desired_outcome": "减少主游戏第一枪后的二次修正",
        "practice_intent": "main_game_transfer",
        "constraints": ["每天最多练二十分钟"],
    }
    assert store.validate_state(state)["learner_context"] == state["learner_context"]

    state["learner_context"]["practice_intent"] = "all_fps_transfer"
    with pytest.raises(ValueError, match="practice_intent"):
        store.validate_state(state)


@pytest.mark.asyncio
async def test_owner_scoped_primary_thread_session_is_unique_and_hidden_from_other_owner():
    from webapp.backend import teaching_session_store as store

    first = await store.get_or_create_primary_session("teaching-owner")
    second = await store.get_or_create_primary_session("teaching-owner")

    assert first["session_ref"] == second["session_ref"]
    assert first["owner_id"] == "teaching-owner"
    assert first["thread_id"] == second["thread_id"]
    assert await store.get_session("other-owner", first["session_ref"]) is None


@pytest.mark.asyncio
async def test_claim_and_release_use_version_cas_and_one_active_run():
    from webapp.backend import coach_store, teaching_session_store as store
    from webapp.backend.db import get_conn

    session = await store.get_or_create_primary_session("cas-owner")
    thread = await coach_store.get_or_create_primary_thread("cas-owner")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content) "
        "VALUES(?, ?, ?, 1, 'queued', 'queued', 'lesson')",
        ("agent_run:one", "cas-owner", int(thread["id"])),
    )
    await conn.commit()
    contract = _contract(session["session_ref"], session["version"])
    claimed = await store.claim_active_run(
        "cas-owner", session["session_ref"], session["version"], "agent_run:one", contract,
    )

    assert claimed["active_run_ref"] == "agent_run:one"
    assert await store.load_run_contract("cas-owner", "agent_run:one") == contract
    with pytest.raises(store.TeachingSessionConflictError):
        await store.claim_active_run(
            "cas-owner", session["session_ref"], session["version"], "agent_run:two", contract,
        )
    with pytest.raises(store.TeachingSessionConflictError):
        await store.release_active_run(
            "cas-owner", session["session_ref"], session["version"] + 1, "agent_run:one",
        )

    next_state = _state(phase="await_teach_back")
    released = await store.release_active_run(
        "cas-owner", session["session_ref"], session["version"], "agent_run:one",
        next_state=next_state,
    )
    assert released["active_run_ref"] is None
    assert released["version"] == session["version"] + 1
    assert released["state"] == next_state


@pytest.mark.asyncio
async def test_failed_claim_rolls_back_active_run_and_contract_snapshot():
    from webapp.backend import coach_store, teaching_session_store as store
    from webapp.backend.db import get_conn

    session = await store.get_or_create_primary_session("rollback-owner")
    thread = await coach_store.get_or_create_primary_thread("rollback-owner")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content) "
        "VALUES(?, ?, ?, 1, 'queued', 'queued', 'lesson')",
        ("agent_run:rollback", "rollback-owner", int(thread["id"])),
    )
    await conn.commit()
    invalid = {**_contract(session["session_ref"], session["version"]), "raw_evidence": "C:/private.mp4"}

    with pytest.raises(ValueError, match="TeachingTurnContract"):
        await store.claim_active_run(
            "rollback-owner", session["session_ref"], session["version"], "agent_run:rollback", invalid,
        )

    assert (await store.get_session("rollback-owner", session["session_ref"]))["active_run_ref"] is None
    row = await (await conn.execute(
        "SELECT teaching_session_ref, teaching_state_version, teaching_contract_json "
        "FROM coach_agent_runs WHERE run_ref='agent_run:rollback'"
    )).fetchone()
    assert tuple(row) == (None, None, None)


@pytest.mark.asyncio
async def test_concurrent_claims_have_one_winner():
    from webapp.backend import coach_store, teaching_session_store as store
    from webapp.backend.db import get_conn

    session = await store.get_or_create_primary_session("concurrent-owner")
    thread = await coach_store.get_or_create_primary_thread("concurrent-owner")
    conn = await get_conn()
    await conn.executemany(
        "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content) "
        "VALUES(?, ?, ?, 1, 'queued', 'queued', 'lesson')",
        [
            ("agent_run:first", "concurrent-owner", int(thread["id"])),
            ("agent_run:second", "concurrent-owner", int(thread["id"])),
        ],
    )
    await conn.commit()
    contract = _contract(session["session_ref"], session["version"])

    async def claim(run_ref: str):
        try:
            return await store.claim_active_run(
                "concurrent-owner", session["session_ref"], session["version"], run_ref, contract,
            )
        except store.TeachingSessionConflictError:
            return None

    results = await asyncio.gather(claim("agent_run:first"), claim("agent_run:second"))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["active_run_ref"] in {"agent_run:first", "agent_run:second"}


@pytest.mark.asyncio
async def test_store_rejects_unbounded_state_and_contract_and_replays_bound_contract():
    from webapp.backend import coach_store, teaching_session_store as store
    from webapp.backend.db import get_conn

    session = await store.get_or_create_primary_session("contract-owner")
    with pytest.raises(ValueError, match="TeachingSession state"):
        await store.replace_state(
            "contract-owner", session["session_ref"], session["version"],
            {**_state(), "raw_evidence": {"path": "C:/private.mp4"}},
        )

    thread = await coach_store.get_or_create_primary_thread("contract-owner")
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO coach_agent_runs(run_ref, owner_id, thread_id, attempt, status, phase, content) "
        "VALUES(?, ?, ?, 1, 'queued', 'queued', 'lesson')",
        ("agent_run:contract", "contract-owner", int(thread["id"])),
    )
    await conn.commit()
    contract = _contract(session["session_ref"], session["version"])
    await store.bind_run_contract(
        "contract-owner", "agent_run:contract", session["session_ref"], session["version"], contract,
    )

    replay = await store.load_run_contract("contract-owner", "agent_run:contract")
    assert replay == contract
    with pytest.raises(ValueError, match="TeachingTurnContract"):
        await store.bind_run_contract(
            "contract-owner", "agent_run:contract", session["session_ref"], session["version"],
            {**contract, "raw_evidence": "C:/private.mp4"},
        )


@pytest.mark.asyncio
async def test_state_rejects_revision_decision_without_comparable_retest_and_bad_session_ref():
    from webapp.backend import teaching_session_store as store

    for comparability in ("unresolved", "not_requested"):
        invalid = {**_state(), "retest_comparability": comparability, "revision_decision": "retain"}
        with pytest.raises(ValueError, match="TeachingSession state"):
            store.validate_state(invalid)
    not_comparable = store.validate_state({
        **_state(), "retest_comparability": "not_comparable", "revision_decision": None,
    })
    assert not_comparable["retest_comparability"] == "not_comparable"
    with pytest.raises(ValueError, match="session_ref"):
        await store.get_session("owner", "not-a-teaching-session")


def test_state_and_contract_bound_the_confirmed_training_item_and_retest_outcome():
    from webapp.backend import teaching_session_store as store

    item_ref = "plan-item:confirmed-lesson-item"
    state = store.validate_state({
        **_state(phase="revise"),
        "active_item_ref": item_ref,
        "retest_intent": "immediate_matched",
        "retest_comparability": "not_comparable",
    })
    contract = {
        **_contract("teaching_session:0123456789abcdef0123456789abcdef", 3),
        "phase": "revise",
        "question_kind": "none",
        "question": None,
        "active_item_ref": item_ref,
        "retest": {
            "intent": "immediate_matched",
            "comparability_required": True,
            "comparability": "not_comparable",
            "revision_decision": None,
        },
    }

    assert state["active_item_ref"] == item_ref
    assert store.validate_contract(
        contract,
        session_ref=contract["session_ref"],
        session_version=3,
    )["retest"]["comparability"] == "not_comparable"
    with pytest.raises(ValueError, match="active_item_ref"):
        store.validate_state({**_state(), "active_item_ref": r"C:\private\item"})
    with pytest.raises(ValueError, match="TeachingTurnContract"):
        store.validate_contract(
            {
                **contract,
                "retest": {**contract["retest"], "revision_decision": "retain"},
            },
            session_ref=contract["session_ref"],
            session_version=3,
        )


def test_contract_bounds_compiled_problem_fields_and_normalizes_legacy_v1():
    from webapp.backend import teaching_session_store as store

    session_ref = "teaching_session:0123456789abcdef0123456789abcdef"
    compiled = {
        **_contract(session_ref, 3),
        "problem_id": "tracking.speed_matching",
        "problem_label": "Late speed matching",
        "evidence_strength": "supported",
        "supporting_evidence": [
            "Corrections start after the target slows.",
            "The same pattern occurs in a comparable segment.",
        ],
        "primary_candidate": "A possible explanation is late speed matching.",
        "alternatives": ["It may instead be delayed target reading."],
        "question": "Does the late correction happen after the target slows?",
        "discriminator": {
            "kind": "question",
            "prompt": "Does the late correction happen after the target slows?",
        },
        "soft_start": True,
    }

    normalized = store.validate_contract(compiled, session_ref=session_ref, session_version=3)
    assert normalized["evidence_strength"] == "supported"
    assert normalized["discriminator"] == compiled["discriminator"]
    assert normalized["soft_start"] is True

    with pytest.raises(ValueError, match="TeachingTurnContract"):
        store.validate_contract(
            {**compiled, "counterevidence_status": "observed", "counterevidence": []},
            session_ref=session_ref,
            session_version=3,
        )
    with pytest.raises(ValueError, match="TeachingTurnContract"):
        store.validate_contract(
            {**compiled, "evidence_strength": "limited", "supporting_evidence": []},
            session_ref=session_ref,
            session_version=3,
        )
    with pytest.raises(ValueError, match="TeachingTurnContract"):
        store.validate_contract(
            {**compiled, "primary_candidate": "This definitely is late speed matching."},
            session_ref=session_ref,
            session_version=3,
        )

    legacy = dict(compiled)
    for field in (
        "problem_id", "problem_label", "evidence_strength", "supporting_evidence",
        "counterevidence_status", "counterevidence", "discriminator", "soft_start",
    ):
        legacy.pop(field)
    legacy_normalized = store.validate_contract(legacy, session_ref=session_ref, session_version=3)
    assert legacy_normalized["problem_id"] is None
    assert legacy_normalized["evidence_strength"] == "limited"
    assert legacy_normalized["discriminator"] is None
    assert legacy_normalized["soft_start"] is False
