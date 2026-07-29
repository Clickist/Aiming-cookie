from __future__ import annotations

import pytest

from webapp.backend import coach_retest_decision


def _result(value: float, *, scenario: str = "Fixture") -> dict:
    return {
        "schema_version": "analysis_result.v2",
        "analysis_type": "flicking",
        "input_mode": "input_native",
        "input_snapshot": {
            "scenario": scenario,
            "scenario_identity_version": "fixture.v1",
        },
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


def _session(owner_id: str, value: float, *, scenario: str = "Fixture") -> dict:
    return {"user_id": owner_id, "status": "done", "result": _result(value, scenario=scenario)}


@pytest.mark.asyncio
async def test_two_analysis_retest_equal_values_becomes_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = {1: _session("owner-a", 10.0), 2: _session("owner-a", 10.0)}

    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)

    decision = await coach_retest_decision.decide_two_analysis_retest(
        "owner-a",
        ["analysis:1", "analysis:2"],
        "metric:distance@v1",
    )

    assert decision == {
        "comparability": "comparable",
        "result": "coach_retest_outcome.v1:unchanged",
        "limitations": ["analysis_comparison_equal"],
    }


@pytest.mark.asyncio
async def test_two_analysis_retest_nonzero_delta_without_policy_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {1: _session("owner-a", 10.0), 2: _session("owner-a", 8.0)}

    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)

    decision = await coach_retest_decision.decide_two_analysis_retest(
        "owner-a",
        ["analysis:1", "analysis:2"],
        "metric:distance@v1",
    )

    assert decision == {
        "comparability": "comparable",
        "result": "coach_retest_outcome.v1:mixed_or_inconclusive",
        "limitations": ["metric_change_policy_missing"],
    }


@pytest.mark.asyncio
async def test_two_analysis_retest_incompatible_results_is_not_comparable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {
        1: _session("owner-a", 10.0),
        2: _session("owner-a", 8.0, scenario="Other Fixture"),
    }

    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)

    decision = await coach_retest_decision.decide_two_analysis_retest(
        "owner-a",
        ["analysis:1", "analysis:2"],
        "metric:distance@v1",
    )

    assert decision == {
        "comparability": "not_comparable",
        "result": "coach_retest_outcome.v1:mixed_or_inconclusive",
        "limitations": ["analysis_not_comparable:scenario_mismatch"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sessions", "error_type"),
    [
        ({1: _session("owner-a", 10.0)}, coach_retest_decision.AnalysisUnavailable),
        ({1: _session("owner-a", 10.0), 2: _session("owner-b", 8.0)}, coach_retest_decision.AnalysisForbidden),
    ],
)
async def test_two_analysis_retest_fails_closed_for_missing_or_foreign_analysis(
    monkeypatch: pytest.MonkeyPatch,
    sessions: dict[int, dict],
    error_type: type[Exception],
) -> None:
    async def get_session(analysis_id: int) -> dict | None:
        return sessions.get(analysis_id)

    monkeypatch.setattr(coach_retest_decision.queue, "get_session", get_session)

    with pytest.raises(error_type):
        await coach_retest_decision.decide_two_analysis_retest(
            "owner-a",
            ["analysis:1", "analysis:2"],
            "metric:distance@v1",
        )
