from __future__ import annotations

import json

import pytest

from webapp.backend import history_trends
from webapp.backend.db import get_conn


_MISSING = object()


def result(
    *,
    scenario="Scenario",
    scenario_version="kovaak_scenario.v1",
    mode="input_native",
    value=10.0,
    unit="raw_counts",
    coverage=1.0,
    evidence_coverage=1.0,
    alignment="aligned",
    version="native.v1",
    classification="deterministic",
    calibration="mouse-calibration:v1",
    metric_key="distance",
    availability="available",
):
    metric = {
        "key": metric_key,
        "value": value,
        "unit": unit,
        "metric_version": version,
        "availability": availability,
        "coverage": coverage,
        "calibration_ref": calibration,
    }
    if classification is not _MISSING:
        metric["classification"] = classification
    return {
        "schema_version": "analysis_result.v2",
        "analysis_type": "flicking",
        "input_mode": mode,
        "input_snapshot": {
            "scenario": scenario,
            "scenario_identity_version": scenario_version,
        },
        "evidence": {
            "alignment": {"status": alignment},
            "coverage": evidence_coverage,
        },
        "deterministic": {"metrics": {metric_key: metric}},
    }


def test_compare_requires_full_deterministic_compatibility():
    compared = history_trends.compare_analysis_results(
        result(value=12), result(value=10), "distance",
    )
    assert compared["comparable"] is True
    assert compared["delta"] == 2.0
    assert compared["percent_change"] == 20.0

    assert history_trends.compare_analysis_results(
        result(mode="multimodal"), result(), "distance",
    )["reason"] == "input_mode_mismatch"
    assert history_trends.compare_analysis_results(
        result(scenario="Other Scenario"), result(), "distance",
    )["reason"] == "scenario_mismatch"
    other_analysis_type = result()
    other_analysis_type["analysis_type"] = "tracking"
    assert history_trends.compare_analysis_results(
        other_analysis_type, result(), "distance",
    )["reason"] == "analysis_type_mismatch"
    assert history_trends.compare_analysis_results(
        result(scenario_version="kovaak_scenario.v2"), result(), "distance",
    )["reason"] == "scenario_identity_version_mismatch"
    assert history_trends.compare_analysis_results(
        result(unit="degrees"), result(), "distance",
    )["reason"] == "metric_unit_mismatch"
    assert history_trends.compare_analysis_results(
        result(version="native_flicking.sparc.v2", metric_key="sparc"),
        result(version="native_flicking.v1", metric_key="sparc"),
        "sparc",
    )["reason"] == "metric_version_mismatch"
    assert history_trends.compare_analysis_results(
        result(calibration="mouse-calibration:v2"), result(), "distance",
    )["reason"] == "calibration_mismatch"
    assert history_trends.compare_analysis_results(
        result(classification="experimental"), result(), "distance",
    )["reason"] == "metric_not_deterministic"
    assert history_trends.compare_analysis_results(
        result(coverage=0.5), result(), "distance",
    )["reason"] == "insufficient_metric_coverage"


def test_compare_fails_closed_without_identity_or_calibration_metadata():
    assert history_trends.compare_analysis_results(
        result(scenario_version=None), result(), "distance",
    )["reason"] == "scenario_identity_version_mismatch"
    assert history_trends.compare_analysis_results(
        result(calibration=None), result(), "distance",
    )["reason"] == "calibration_compatibility_missing"


@pytest.mark.parametrize(
    ("field", "malformed_value", "reason"),
    [
        ("analysis_type", 7, "analysis_type_mismatch"),
        ("scenario", r"C:\private\Scenario", "scenario_mismatch"),
        (
            "scenario_identity_version",
            7,
            "scenario_identity_version_mismatch",
        ),
        ("input_mode", {"mode": "input_native"}, "input_mode_mismatch"),
        ("metric_key", "file:///private/distance", "metric_key_mismatch"),
        ("metric_version", ["native.v1"], "metric_version_mismatch"),
        ("metric_unit", {"unit": "raw_counts"}, "metric_unit_mismatch"),
        (
            "calibration_ref",
            ["mouse-calibration:v1"],
            "calibration_compatibility_missing",
        ),
    ],
)
def test_compare_rejects_equal_malformed_identity_metadata(
    field: str,
    malformed_value: object,
    reason: str,
):
    current = result()
    baseline = result()
    metric_key = "distance"

    for payload in (current, baseline):
        if field in {"analysis_type", "input_mode"}:
            payload[field] = malformed_value
        elif field in {"scenario", "scenario_identity_version"}:
            payload["input_snapshot"][field] = malformed_value
        elif field == "metric_key":
            metric = payload["deterministic"]["metrics"].pop(metric_key)
            metric["key"] = malformed_value
            payload["deterministic"]["metrics"][malformed_value] = metric
        else:
            metric_field = {
                "metric_version": "metric_version",
                "metric_unit": "unit",
                "calibration_ref": "calibration_ref",
            }[field]
            payload["deterministic"]["metrics"][metric_key][metric_field] = malformed_value

    if field == "metric_key":
        metric_key = str(malformed_value)
    compared = history_trends.compare_analysis_results(current, baseline, metric_key)

    assert compared == {"comparable": False, "reason": reason}


@pytest.mark.parametrize("missing_side", ["current", "baseline"])
def test_compare_requires_explicit_deterministic_classification_on_both_sides(
    missing_side: str,
):
    current = result(classification=_MISSING if missing_side == "current" else "deterministic")
    baseline = result(classification=_MISSING if missing_side == "baseline" else "deterministic")

    compared = history_trends.compare_analysis_results(current, baseline, "distance")

    assert compared == {"comparable": False, "reason": "metric_not_deterministic"}


@pytest.mark.parametrize(
    ("current", "baseline", "reason"),
    [
        (result(coverage=0.999), result(), "insufficient_metric_coverage"),
        (result(coverage=None), result(), "insufficient_metric_coverage"),
        (result(evidence_coverage=0.999), result(), "insufficient_evidence_coverage"),
        (result(evidence_coverage=None), result(), "insufficient_evidence_coverage"),
        (result(alignment="partial"), result(), "insufficient_alignment_quality"),
        (result(availability="unavailable"), result(), "metric_unavailable"),
        (result(value=float("inf")), result(), "metric_value_invalid"),
        (result(calibration=None), result(), "calibration_compatibility_missing"),
        (
            result(calibration="mouse-calibration:v2"),
            result(),
            "calibration_mismatch",
        ),
    ],
)
def test_compare_requires_full_coverage_alignment_and_calibration(
    current: dict,
    baseline: dict,
    reason: str,
):
    compared = history_trends.compare_analysis_results(current, baseline, "distance")

    assert compared == {"comparable": False, "reason": reason}


def test_compare_accepts_full_quality_with_not_required_alignment():
    compared = history_trends.compare_analysis_results(
        result(value=12.0, alignment="not_required"),
        result(value=10.0, alignment="not_required"),
        "distance",
    )

    assert compared["comparable"] is True
    assert compared["delta"] == 2.0


@pytest.mark.asyncio
async def test_recent_trend_uses_newest_comparable_baseline_only():
    conn = await get_conn()
    for value, mode in ((8.0, "input_native"), (99.0, "multimodal"), (10.0, "input_native")):
        await conn.execute(
            "INSERT INTO sessions(user_id, status, result, input_mode) VALUES(?, 'done', ?, ?)",
            ("u1", json.dumps(result(value=value, mode=mode)), mode),
        )
    await conn.commit()

    trend = await history_trends.recent_trend_for_user("u1", "distance")
    assert trend["comparable"] is True
    assert trend["current"] == 10.0
    assert trend["baseline"] == 8.0


@pytest.mark.asyncio
async def test_recent_trend_omits_values_with_fewer_than_two_results():
    empty = await history_trends.recent_trend_for_user("u1", "distance")
    assert empty == {"comparable": False, "reason": "insufficient_history"}

    conn = await get_conn()
    await conn.execute(
        "INSERT INTO sessions(user_id, status, result, input_mode) VALUES(?, 'done', ?, ?)",
        ("u1", json.dumps(result()), "input_native"),
    )
    await conn.commit()

    single = await history_trends.recent_trend_for_user("u1", "distance")
    assert single == {"comparable": False, "reason": "insufficient_history"}


@pytest.mark.asyncio
async def test_recent_trend_omits_values_when_no_comparable_baseline():
    conn = await get_conn()
    for item in (
        result(mode="multimodal", value=8.0),
        result(version="native.v2", value=9.0),
        result(value=10.0),
    ):
        await conn.execute(
            "INSERT INTO sessions(user_id, status, result, input_mode) VALUES(?, 'done', ?, ?)",
            ("u1", json.dumps(item), item["input_mode"]),
        )
    await conn.commit()

    trend = await history_trends.recent_trend_for_user("u1", "distance")

    assert trend["comparable"] is False
    assert trend["reason"] == "no_comparable_baseline"
    assert isinstance(trend["current_session_id"], int)
    assert set(trend) == {"comparable", "reason", "current_session_id"}
    assert not {
        "current", "baseline", "delta", "percent_change", "baseline_session_id",
    }.intersection(trend)
