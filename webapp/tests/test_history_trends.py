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
    scenario_profile_ref=_MISSING,
    scenario_hash="fixture-hash-a",
    scenario_registry_version="scenario_registry.test.v1",
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
    payload = {
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
    if scenario_profile_ref is not _MISSING:
        payload["input_snapshot"]["scenario_resolution"] = {
            "schema_version": "scenario_resolution.v1",
            "scenario_hash": scenario_hash,
            "display_name": scenario,
            "registry_version": scenario_registry_version,
            "manifest_version": "scenario_manifest.test.v1",
            "scenario_profile_ref": scenario_profile_ref,
            "classification_source": "reviewed_registry",
            "classification_confidence": "confirmed",
            "profile_status": "active",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "source_refs": ["review:fixture"],
            "supersedes": [],
            "manifest_status": "active",
            "fixture_ref": "fixture:scenario",
            "review_source_ref": "review:scenario",
            "manifest_reviewed_at": "2026-07-20T00:00:00Z",
            "family_gate_refs": ["gate:family"],
            "aim_family": "static_clicking",
            "subdomains": ["precision"],
            "target_motion": {"model": "static", "target_count_model": "single"},
            "allowed_analyzers": ["native_flicking.v1"],
            "allowed_metric_families": ["input_kinematics"],
            "claim_ceiling": "family_specific",
            "family_analyzer_dispatch": "allowed",
            "limitations": ["fixture_only"],
        }
    return payload


def dynamic_result(
    *,
    visual_profile="visual-profile:dynamic.v1",
    motion_class="predictable",
    condition_refs=None,
):
    metric_key = "dynamic_clicking.normalized_click_error"
    payload = result(
        scenario="Dynamic Fixture",
        mode="multimodal",
        value=0.9,
        unit="visible_radius",
        version="dynamic_clicking.normalized_click_error.v1",
        calibration=visual_profile,
        metric_key=metric_key,
        scenario_profile_ref="scenario:dynamic.fixture@1",
    )
    payload["analysis_type"] = "dynamic_clicking"
    payload["analysis_version"] = "dynamic_clicking.v1"
    resolution = payload["input_snapshot"]["scenario_resolution"]
    resolution.update({
        "aim_family": "dynamic_clicking",
        "subdomains": ["predictable"],
        "target_motion": {"model": "predictable", "target_count_model": "single"},
        "allowed_analyzers": ["dynamic_clicking.v1"],
        "allowed_metric_families": ["dynamic_clicking"],
    })
    payload["deterministic"].update({
        "scenario_motion_class": motion_class,
        "visual_quality_profile_ref": visual_profile,
    })
    payload["deterministic"]["metrics"][metric_key]["condition_refs"] = list(
        condition_refs or ["condition:predictable:steady"]
    )
    return payload


def tracking_result(
    *,
    visual_profile="visual-profile:tracking.v1",
    calibration=_MISSING,
    motion_class="predictable",
    condition_refs=None,
    unit="pixels",
    version="continuous_tracking.target_relative_error_px.v1",
):
    metric_key = "continuous_tracking.target_relative_error_px"
    payload = result(
        scenario="Tracking Fixture",
        mode="multimodal",
        value=0.9,
        unit=unit,
        version=version,
        calibration=visual_profile if calibration is _MISSING else calibration,
        metric_key=metric_key,
        scenario_profile_ref="scenario:tracking.fixture@1",
    )
    payload["analysis_type"] = "continuous_tracking"
    payload["analysis_version"] = "continuous_tracking.v1"
    resolution = payload["input_snapshot"]["scenario_resolution"]
    resolution.update({
        "aim_family": "continuous_tracking",
        "subdomains": ["predictable"],
        "target_motion": {"model": "predictable", "target_count_model": "single"},
        "allowed_analyzers": ["continuous_tracking.v1"],
        "allowed_metric_families": ["continuous_tracking"],
    })
    payload["deterministic"].update({
        "scenario_motion_class": motion_class,
        "visual_quality_profile_ref": visual_profile,
    })
    payload["deterministic"]["metrics"][metric_key]["condition_refs"] = list(
        condition_refs or ["condition:predictable:steady"]
    )
    return payload


def switching_result(
    *,
    visual_profile="visual-profile:switching.v1",
    motion_class="mixed",
    condition_refs=None,
):
    metric_key = "target_switching.transition_time_ms"
    payload = result(
        scenario="Switching Fixture",
        mode="multimodal",
        value=90.0,
        unit="ms",
        version="target_switching.transition_time_ms.v1",
        calibration=visual_profile,
        metric_key=metric_key,
        scenario_profile_ref="scenario:switching.fixture@1",
    )
    payload["analysis_type"] = "target_switching"
    payload["analysis_version"] = "target_switching.v1"
    resolution = payload["input_snapshot"]["scenario_resolution"]
    resolution.update({
        "aim_family": "target_switching",
        "subdomains": ["mixed"],
        "target_motion": {"model": "mixed", "target_count_model": "concurrent"},
        "allowed_analyzers": ["target_switching.v1"],
        "allowed_metric_families": ["target_switching"],
    })
    payload["deterministic"].update({
        "scenario_motion_class": motion_class,
        "visual_quality_profile_ref": visual_profile,
    })
    payload["deterministic"]["metrics"][metric_key]["condition_refs"] = list(
        condition_refs or ["condition:target_switching:observable_chain"]
    )
    return payload


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


def test_dynamic_compare_requires_visual_profile_and_motion_condition_compatibility():
    metric_key = "dynamic_clicking.normalized_click_error"

    assert history_trends.compare_analysis_results(
        dynamic_result(), dynamic_result(), metric_key,
    )["comparable"] is True
    assert history_trends.compare_analysis_results(
        dynamic_result(visual_profile="visual-profile:dynamic.v2"),
        dynamic_result(),
        metric_key,
    )["reason"] == "visual_quality_profile_mismatch"
    assert history_trends.compare_analysis_results(
        dynamic_result(motion_class="reactive"), dynamic_result(), metric_key,
    )["reason"] == "motion_condition_mismatch"
    assert history_trends.compare_analysis_results(
        dynamic_result(condition_refs=["condition:predictable:post_change"]),
        dynamic_result(),
        metric_key,
    )["reason"] == "metric_condition_mismatch"
    incomplete_crosshair = dynamic_result()
    incomplete_crosshair["evidence"]["coverage"] = 0.75
    assert history_trends.compare_analysis_results(
        incomplete_crosshair,
        dynamic_result(),
        metric_key,
    )["reason"] == "insufficient_evidence_coverage"
    matched = history_trends.build_matched_dynamic_baseline(
        dynamic_result(),
        [(7, dynamic_result())],
        [metric_key],
    )
    assert matched == {
        "comparable": True,
        "reason": None,
        "baseline_analysis_ref": "analysis:7",
        "baseline_metrics": {metric_key: 0.9},
        "metric_comparisons": {
            metric_key: {
                "current": 0.9,
                "baseline": 0.9,
                "delta": 0.0,
                "percent_change": 0.0,
            },
        },
    }


def test_dynamic_compare_accepts_production_visual_profile_reference():
    metric_key = "dynamic_clicking.normalized_click_error"
    production_profile = (
        "visual-quality:visual_signals.round_detector@"
        "visual_round_detector.circularity_0_60_center_overlay_0_50.v2"
    )
    current = dynamic_result(visual_profile=production_profile)
    baseline = dynamic_result(visual_profile=production_profile)

    assert history_trends.compare_analysis_results(
        current, baseline, metric_key,
    )["comparable"] is True

    current["deterministic"]["metrics"][metric_key]["coverage"] = 0.12
    baseline["deterministic"]["metrics"][metric_key]["coverage"] = 0.09
    assert history_trends.compare_analysis_results(
        current, baseline, metric_key,
    ) == {"comparable": False, "reason": "insufficient_metric_coverage"}


def test_tracking_compare_requires_exact_family_compatibility():
    metric_key = "continuous_tracking.target_relative_error_px"

    assert history_trends.compare_analysis_results(
        tracking_result(), tracking_result(), metric_key,
    )["comparable"] is True
    assert history_trends.compare_analysis_results(
        tracking_result(visual_profile="visual-profile:tracking.v2"),
        tracking_result(),
        metric_key,
    )["reason"] == "visual_quality_profile_mismatch"
    assert history_trends.compare_analysis_results(
        tracking_result(motion_class="reactive"), tracking_result(), metric_key,
    )["reason"] == "motion_condition_mismatch"
    assert history_trends.compare_analysis_results(
        tracking_result(condition_refs=["condition:predictable:post_change"]),
        tracking_result(),
        metric_key,
    )["reason"] == "metric_condition_mismatch"
    assert history_trends.compare_analysis_results(
        tracking_result(version="continuous_tracking.target_relative_error_px.v2"),
        tracking_result(),
        metric_key,
    )["reason"] == "metric_version_mismatch"
    assert history_trends.compare_analysis_results(
        tracking_result(unit="visible_radius"), tracking_result(), metric_key,
    )["reason"] == "metric_unit_mismatch"
    assert history_trends.compare_analysis_results(
        tracking_result(calibration="visual-calibration:tracking.v2"),
        tracking_result(),
        metric_key,
    )["reason"] == "calibration_mismatch"
    incomplete = tracking_result()
    incomplete["deterministic"]["metrics"][metric_key]["coverage"] = 0.5
    assert history_trends.compare_analysis_results(
        incomplete, tracking_result(), metric_key,
    )["reason"] == "insufficient_metric_coverage"
    incomplete = tracking_result()
    incomplete["evidence"]["coverage"] = 0.5
    assert history_trends.compare_analysis_results(
        incomplete, tracking_result(), metric_key,
    )["reason"] == "insufficient_evidence_coverage"
    incomplete = tracking_result()
    incomplete["evidence"]["alignment"]["status"] = "partial"
    assert history_trends.compare_analysis_results(
        incomplete, tracking_result(), metric_key,
    )["reason"] == "insufficient_alignment_quality"
    cross_family = tracking_result()
    cross_family["analysis_type"] = "dynamic_clicking"
    assert history_trends.compare_analysis_results(
        tracking_result(), cross_family, metric_key,
    )["reason"] == "analysis_type_mismatch"


def test_tracking_baseline_uses_the_first_exactly_comparable_run():
    metric_key = "continuous_tracking.target_relative_error_px"
    incompatible = tracking_result(motion_class="reactive")
    matched = history_trends.build_matched_tracking_baseline(
        tracking_result(),
        [(9, incompatible), (7, tracking_result())],
        [metric_key],
    )

    assert matched == {
        "comparable": True,
        "reason": None,
        "baseline_analysis_ref": "analysis:7",
        "baseline_metrics": {metric_key: 0.9},
        "metric_comparisons": {
            metric_key: {
                "current": 0.9,
                "baseline": 0.9,
                "delta": 0.0,
                "percent_change": 0.0,
            },
        },
    }
    assert history_trends.build_matched_tracking_baseline(
        dynamic_result(), [(7, tracking_result())], [metric_key],
    ) == {"comparable": False, "reason": "analysis_type_mismatch"}


def test_switching_compare_and_baseline_require_exact_family_compatibility():
    metric_key = "target_switching.transition_time_ms"

    assert history_trends.compare_analysis_results(
        switching_result(), switching_result(), metric_key,
    )["comparable"] is True
    assert history_trends.compare_analysis_results(
        switching_result(visual_profile="visual-profile:switching.v2"),
        switching_result(),
        metric_key,
    )["reason"] == "visual_quality_profile_mismatch"
    assert history_trends.compare_analysis_results(
        switching_result(motion_class="reactive"), switching_result(), metric_key,
    )["reason"] == "motion_condition_mismatch"
    assert history_trends.compare_analysis_results(
        switching_result(condition_refs=["condition:target_switching:partial"]),
        switching_result(),
        metric_key,
    )["reason"] == "metric_condition_mismatch"

    matched = history_trends.build_matched_target_switching_baseline(
        switching_result(),
        [(9, switching_result(motion_class="reactive")), (7, switching_result())],
        [metric_key],
    )
    assert matched == {
        "comparable": True,
        "reason": None,
        "baseline_analysis_ref": "analysis:7",
        "baseline_metrics": {metric_key: 90.0},
        "metric_comparisons": {
            metric_key: {
                "current": 90.0,
                "baseline": 90.0,
                "delta": 0.0,
                "percent_change": 0.0,
            },
        },
    }


def test_compare_uses_frozen_scenario_profile_instead_of_display_name():
    current = result(
        scenario="Renamed Display",
        scenario_profile_ref="scenario:static.fixture@1",
    )
    baseline = result(
        scenario="Original Display",
        scenario_profile_ref="scenario:static.fixture@1",
    )

    assert history_trends.compare_analysis_results(
        current, baseline, "distance",
    )["comparable"] is True
    assert history_trends.compare_analysis_results(
        result(scenario_profile_ref="scenario:static.other@1"),
        baseline,
        "distance",
    )["reason"] == "scenario_profile_ref_mismatch"
    assert history_trends.compare_analysis_results(
        result(
            scenario_profile_ref="scenario:static.fixture@1",
            scenario_hash="fixture-hash-b",
        ),
        baseline,
        "distance",
    )["reason"] == "scenario_hash_mismatch"
    assert history_trends.compare_analysis_results(
        result(
            scenario_profile_ref="scenario:static.fixture@1",
            scenario_registry_version="scenario_registry.test.v2",
        ),
        baseline,
        "distance",
    )["reason"] == "scenario_registry_version_mismatch"


def test_compare_fails_closed_when_only_one_result_has_scenario_resolution():
    assert history_trends.compare_analysis_results(
        result(scenario_profile_ref="scenario:static.fixture@1"),
        result(),
        "distance",
    )["reason"] == "scenario_hash_mismatch"


def test_compare_rejects_two_equally_malformed_scenario_resolutions():
    current = result(scenario_profile_ref="scenario:static.fixture@1")
    baseline = result(scenario_profile_ref="scenario:static.fixture@1")
    for payload in (current, baseline):
        payload["input_snapshot"]["schema_version"] = "analysis_input_snapshot.v3"
        payload["input_snapshot"]["scenario_resolution"].pop("source_refs")

    assert history_trends.compare_analysis_results(
        current, baseline, "distance",
    ) == {"comparable": False, "reason": "scenario_resolution_invalid"}


def test_compare_rejects_v3_without_scenario_resolution():
    current = result()
    baseline = result()
    current["input_snapshot"]["schema_version"] = "analysis_input_snapshot.v3"
    baseline["input_snapshot"]["schema_version"] = "analysis_input_snapshot.v3"

    assert history_trends.compare_analysis_results(
        current, baseline, "distance",
    ) == {"comparable": False, "reason": "scenario_resolution_invalid"}


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


def test_compare_rejects_analysis_and_timebase_version_mismatches():
    current = result()
    baseline = result()
    current["analysis_version"] = "static_clicking_adapter.v2"
    baseline["analysis_version"] = "static_clicking_adapter.v1"
    for payload, version in ((current, "time_alignment.v2"), (baseline, "time_alignment.v1")):
        payload["input_snapshot"]["canonical_time_window"] = {
            "schema_version": "canonical_time_window.v1",
            "timebase_version": version,
        }

    assert history_trends.compare_analysis_results(
        current, baseline, "distance",
    ) == {"comparable": False, "reason": "analysis_version_mismatch"}

    baseline["analysis_version"] = current["analysis_version"]
    assert history_trends.compare_analysis_results(
        current, baseline, "distance",
    ) == {"comparable": False, "reason": "timebase_version_mismatch"}


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
