from __future__ import annotations

import json

import pytest

from webapp.backend import history_trends
from webapp.backend.db import get_conn


def result(*, scenario="Scenario", scenario_version="kovaak_scenario.v1", mode="input_native", value=10.0, unit="raw_counts", coverage=1.0, alignment="aligned", version="native.v1", classification="deterministic", calibration="mouse-calibration:v1", metric_key="distance"):
    return {
        "schema_version": "analysis_result.v2",
        "analysis_type": "flicking",
        "input_mode": mode,
        "input_snapshot": {
            "scenario": scenario,
            "scenario_identity_version": scenario_version,
        },
        "evidence": {"alignment": {"status": alignment}},
        "deterministic": {"metrics": {metric_key: {
            "key": metric_key,
            "value": value,
            "unit": unit,
            "metric_version": version,
            "availability": "available",
            "coverage": coverage,
            "classification": classification,
            "calibration_ref": calibration,
        }}},
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
        result(scenario_version="kovaak_scenario.v2"), result(), "distance",
    )["reason"] == "scenario_identity_version_mismatch"
    assert history_trends.compare_analysis_results(
        result(unit="degrees"), result(), "distance",
    )["reason"] == "metric_unit_mismatch"
    assert history_trends.compare_analysis_results(
        result(version="native_flicking.sparc.v2", metric_key="sparc"),
        result(version="native_flicking.v1", metric_key="sparc"),
        "sparc",
    )["reason"] == "metric_metric_version_mismatch"
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
