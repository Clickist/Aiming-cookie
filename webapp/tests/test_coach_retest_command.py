from __future__ import annotations

import pytest

from webapp.backend import coach_commands, coach_retest_decision


def _result(value: float) -> dict:
    return {
        "schema_version": "analysis_result.v2",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "input_snapshot": {"scenario": "Fixture", "scenario_identity_version": "fixture.v1"},
        "evidence": {"alignment": {"status": "aligned"}, "coverage": 1.0},
        "deterministic": {
            "metrics": {
                "distance": {
                    "key": "distance",
                    "value": value,
                    "unit": "raw_counts",
                    "metric_version": "distance.v1",
                    "classification": "deterministic",
                    "availability": "available",
                    "coverage": 1.0,
                    "calibration_ref": "fixture-calibration.v1",
                }
            }
        },
    }


def _session(owner_id: str, value: float) -> dict:
    return {"user_id": owner_id, "status": "done", "result": _result(value)}


@pytest.mark.asyncio
async def test_coach_inferred_two_analysis_retest_normalizes_before_confirmation_and_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = coach_commands.InMemoryCommandJournal()
    coach_commands.set_command_journal(journal)
    writes: list[dict] = []
    sessions = {1: _session("owner-a", 10.0), 2: _session("owner-a", 8.0)}

    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    async def write_fact(owner_id: str, command_name: str, parameters: dict) -> tuple[dict, str]:
        writes.append(parameters)
        return {"item_ref": parameters["item_ref"]}, "retest:fixture"

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)
    monkeypatch.setattr(coach_commands, "_execute_training_plan_fact", write_fact)
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": "plan-item:fixture",
            "kind": "matched",
            "expected_metric_ref": "metric:distance@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:1", "analysis:2"],
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:improved",
            "limitations": ["provider_claim"],
        },
        "idempotency_key": "two-analysis-retest",
    }
    try:
        pending = await coach_commands.execute_product_command(
            "owner-a", command, authorization_source="coach_inferred",
        )
        normalized = pending["confirmation"]["parameters"]
        assert pending["status"] == "needs_confirmation"
        assert normalized["comparability"] == "comparable"
        assert normalized["result"] == "coach_retest_outcome.v1:mixed_or_inconclusive"
        assert normalized["limitations"] == ["provider_claim", "metric_change_policy_missing"]
        sessions.clear()
        normalized_command = {**command, "parameters": normalized}

        confirmed = await coach_commands.execute_product_command(
            "owner-a",
            {
                **normalized_command,
                "confirmation_ref": pending["confirmation"]["confirmation_ref"],
            },
            authorization_source="confirmed",
        )
        replay = await coach_commands.execute_product_command(
            "owner-a",
            {
                **normalized_command,
                "confirmation_ref": pending["confirmation"]["confirmation_ref"],
            },
            authorization_source="confirmed",
        )

        assert confirmed["status"] == "succeeded"
        assert replay["status"] == "succeeded"
        assert writes == [{
            **command["parameters"],
            "result": "coach_retest_outcome.v1:mixed_or_inconclusive",
            "limitations": ["provider_claim", "metric_change_policy_missing"],
        }]
    finally:
        coach_commands.set_command_journal(None)


@pytest.mark.asyncio
async def test_single_analysis_explicit_retest_keeps_user_confirmed_fact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = coach_commands.InMemoryCommandJournal()
    coach_commands.set_command_journal(journal)
    writes: list[dict] = []

    async def write_fact(owner_id: str, command_name: str, parameters: dict) -> tuple[dict, str]:
        writes.append(parameters)
        return {"item_ref": parameters["item_ref"]}, "retest:fixture"

    monkeypatch.setattr(coach_commands, "_execute_training_plan_fact", write_fact)
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": "plan-item:fixture",
            "kind": "matched",
            "expected_metric_ref": "metric:distance@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:1"],
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:improved",
            "limitations": ["learner_confirmed"],
        },
        "idempotency_key": "one-analysis-user-fact",
    }
    try:
        result = await coach_commands.execute_product_command(
            "owner-a", command, authorization_source="explicit_user_request",
        )
        assert result["status"] == "succeeded"
        assert writes == [command["parameters"]]
    finally:
        coach_commands.set_command_journal(None)


@pytest.mark.asyncio
async def test_coach_inferred_two_analysis_retest_does_not_confirm_missing_or_foreign_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = coach_commands.InMemoryCommandJournal()
    coach_commands.set_command_journal(journal)
    sessions = {1: _session("owner-a", 10.0), 2: _session("owner-b", 8.0)}

    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)
    command = {
        "command_name": "training_plan.retest.record",
        "parameters": {
            "item_ref": "plan-item:fixture",
            "kind": "matched",
            "expected_metric_ref": "metric:distance@v1",
            "expected_direction": "lower_better",
            "analysis_refs": ["analysis:1", "analysis:2"],
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:improved",
            "limitations": ["provider_claim"],
        },
        "idempotency_key": "foreign-two-analysis-retest",
    }
    try:
        result = await coach_commands.execute_product_command(
            "owner-a", command, authorization_source="coach_inferred",
        )
        assert result["status"] == "failed"
        assert result["warning_or_error"]["code"] == "forbidden"
        assert "confirmation" not in result
    finally:
        coach_commands.set_command_journal(None)
