from __future__ import annotations

import copy

import pytest

from webapp.backend import aiming_profile_store as store
from webapp.backend import db


def _contribution(*, value: float, confidence: str = "high") -> dict:
    return {
        "schema_version": "profile_contribution.v1",
        "source_kind": "deterministic",
        "dimensions": [
            {
                "dimension_key": "static_clicking.terminal_control",
                "scope": "exact_scenario",
                "scenario_profile_ref": "scenario:sixshot@1",
                "metric_ref": "metric:terminal_control",
                "metric_value": value,
                "unit": "normalized_error",
                "expected_direction": "lower_better",
                "confidence": confidence,
                "comparability": "comparable",
                "supporting_metric_refs": ["metric:terminal_control"],
                "counterexample_refs": ["evidence:counterexample-1"],
                "candidate_hypothesis_refs": ["diagnosis:late-correction@2"],
            },
        ],
    }


def _partial_analysis_result() -> dict:
    return {
        "schema_version": "analysis_result.v2",
        "scenario": {
            "scenario_profile_ref": "scenario:tracking.whj@1",
            "support_status": "partial",
        },
        "deterministic": {
            "support_status": "partial",
            "metrics": {
                "continuous_tracking.target_relative_error_px": {
                    "value": 12.5,
                    "unit": "px",
                    "availability": "available",
                    "classification": "deterministic",
                    "metric_version": "continuous_tracking.target_relative_error_px.v1",
                    "coverage": 1.0,
                    "limitations": ["player_aim_motion_unavailable_fixed_viewport_center"],
                    "provenance": {"kind": "derived", "sources": ["analysis:1:source:tracking"]},
                },
                "continuous_tracking.phase_lag_ms": {
                    "value": None,
                    "unit": "ms",
                    "availability": "unavailable",
                    "classification": "deterministic",
                    "metric_version": "continuous_tracking.phase_lag_ms.v1",
                    "coverage": 0.0,
                    "limitations": ["player_aim_motion_unavailable_fixed_viewport_center"],
                    "provenance": {"kind": "derived", "sources": ["analysis:1:source:tracking"]},
                },
            },
        },
        "evidence": {
            "derived_artifact": {"artifact_ref": "analysis:1:evidence:1"},
        },
    }


def test_partial_analysis_projects_each_available_metric_without_whole_result_gate():
    payload = store.build_contribution_from_analysis_result(_partial_analysis_result())

    assert payload is not None
    assert [item["dimension_key"] for item in payload["dimensions"]] == [
        "continuous_tracking.target_relative_error_px",
    ]
    assert payload["dimensions"][0]["confidence"] == "medium"


@pytest.mark.asyncio
async def test_deterministic_contributions_are_owner_scoped_idempotent_and_materialize_trends():
    first = await store.record_deterministic_contribution(
        "owner-a", "analysis:1", _contribution(value=0.8),
    )
    retry = await store.record_deterministic_contribution(
        "owner-a", "analysis:1", _contribution(value=0.8),
    )
    second = await store.record_deterministic_contribution(
        "owner-a", "analysis:2", _contribution(value=0.6),
    )

    assert first["contribution_ref"] == retry["contribution_ref"]
    assert retry["revision"] == 1
    assert second["revision"] == 1
    assert len(await store.list_contributions("owner-a")) == 2

    profile = await store.get_profile_snapshot("owner-a")
    dimension = profile["dimensions"][0]
    assert dimension["current_metric_value"] == 0.6
    assert dimension["trend_direction"] == "improving"
    assert dimension["supporting_metric_refs"] == ["metric:terminal_control"]
    assert dimension["counterexample_refs"] == ["evidence:counterexample-1"]
    assert dimension["candidate_hypothesis_refs"] == ["diagnosis:late-correction@2"]

    with pytest.raises(store.ProfileForbidden):
        await store.get_contribution("owner-b", first["contribution_ref"])


@pytest.mark.asyncio
async def test_low_confidence_or_noncomparable_contribution_is_preserved_but_cannot_override_profile():
    await store.record_deterministic_contribution(
        "owner-a", "analysis:1", _contribution(value=0.8),
    )
    low_confidence = await store.record_deterministic_contribution(
        "owner-a", "analysis:2", _contribution(value=0.1, confidence="low"),
    )
    noncomparable = _contribution(value=0.2)
    noncomparable["dimensions"][0]["comparability"] = "not_comparable"
    await store.record_deterministic_contribution("owner-a", "analysis:3", noncomparable)

    profile = await store.get_profile_snapshot("owner-a")
    assert profile["dimensions"][0]["current_metric_value"] == 0.8
    assert profile["dimensions"][0]["trend_direction"] == "unknown"
    assert low_confidence["included_in_current_profile"] is False
    assert len(await store.list_contributions("owner-a")) == 3


@pytest.mark.asyncio
async def test_contribution_revisions_tombstone_deleted_analysis_and_rebuild_profile():
    original = _contribution(value=0.8)
    await store.record_deterministic_contribution("owner-a", "analysis:1", original)
    revised = copy.deepcopy(original)
    revised["dimensions"][0]["metric_value"] = 0.7
    replacement = await store.record_deterministic_contribution(
        "owner-a", "analysis:1", revised,
    )
    await store.record_deterministic_contribution(
        "owner-a", "analysis:2", _contribution(value=0.6),
    )

    assert replacement["revision"] == 2
    removed = await store.invalidate_analysis_contribution(
        "owner-a", "analysis:2", reason="analysis_deleted",
    )
    assert removed["status"] == "invalidated"
    assert removed["tombstone_ref"].startswith("profile-tombstone:")

    profile = await store.get_profile_snapshot("owner-a")
    assert profile["dimensions"][0]["current_metric_value"] == 0.7
    assert (await store.list_contributions("owner-a"))[1]["status"] == "invalidated"
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE aiming_profile_state SET rebuild_state='pending' WHERE owner_id='owner-a'",
    )
    await conn.execute(
        "DELETE FROM aiming_profile_dimensions WHERE owner_id='owner-a'",
    )
    await conn.commit()
    assert (await store.reconcile_profiles()) == {"owners_rebuilt": 1}
    assert (await store.get_profile_snapshot("owner-a"))["dimensions"][0]["current_metric_value"] == 0.7


@pytest.mark.asyncio
async def test_profile_rejects_llm_non_deterministic_and_cross_scenario_without_normalization():
    llm = _contribution(value=0.8)
    llm["source_kind"] = "llm"
    with pytest.raises(store.InvalidProfileContribution):
        await store.record_deterministic_contribution("owner-a", "analysis:1", llm)

    inferred = _contribution(value=0.8)
    inferred["dimensions"][0]["confidence"] = "model_inferred"
    with pytest.raises(store.InvalidProfileContribution):
        await store.record_deterministic_contribution("owner-a", "analysis:1", inferred)

    normalized = _contribution(value=0.8)
    normalized["dimensions"][0]["scope"] = "cross_scenario_normalized"
    normalized["dimensions"][0].pop("scenario_profile_ref")
    with pytest.raises(store.InvalidProfileContribution):
        await store.record_deterministic_contribution("owner-a", "analysis:1", normalized)


@pytest.mark.asyncio
async def test_exact_and_validated_normalized_dimensions_remain_separate():
    await store.record_deterministic_contribution(
        "owner-a", "analysis:1", _contribution(value=0.8),
    )
    normalized = _contribution(value=0.5)
    dimension = normalized["dimensions"][0]
    dimension["scope"] = "cross_scenario_normalized"
    dimension.pop("scenario_profile_ref")
    dimension["normalization_ref"] = "normalization:static-clicking@1"
    await store.record_deterministic_contribution("owner-a", "analysis:2", normalized)

    dimensions = (await store.get_profile_snapshot("owner-a"))["dimensions"]
    assert [(item["scope"], item["scope_ref"]) for item in dimensions] == [
        ("cross_scenario_normalized", "normalization:static-clicking@1"),
        ("exact_scenario", "scenario:sixshot@1"),
    ]
