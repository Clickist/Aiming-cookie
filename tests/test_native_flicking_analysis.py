from copy import deepcopy

import pytest

from kovaak_tracker.native_flicking_analysis import (
    NativeFlickingAnalysisError,
    align_points_to_challenge,
    analyze_native_flicking,
    derive_trajectory,
)


def point(timestamp_ms, dx, dy, buttons=0):
    return {
        "timestamp_ms": timestamp_ms,
        "dx": dx,
        "dy": dy,
        "buttons": buttons,
    }


def performance(start_ms=1_000, duration_ms=200, events=()):
    return {
        "challenge_start_utc": start_ms,
        "time_limit_ms": duration_ms,
        "events": events,
    }


def test_derived_trajectory_uses_prefix_sums_without_mutating_raw_points():
    raw_points = [point(1_000, 3, -2), point(1_010, -1, 5, buttons=1)]
    original_points = deepcopy(raw_points)

    trajectory = derive_trajectory(raw_points)

    assert trajectory == [
        {
            "timestamp_ms": 1_000,
            "dx": 3,
            "dy": -2,
            "buttons": 0,
            "x_raw_counts": 3,
            "y_raw_counts": -2,
        },
        {
            "timestamp_ms": 1_010,
            "dx": -1,
            "dy": 5,
            "buttons": 1,
            "x_raw_counts": 2,
            "y_raw_counts": 3,
        },
    ]
    assert raw_points == original_points


def test_trajectory_accepts_same_millisecond_records_but_rejects_invalid_timestamps():
    assert [item["x_raw_counts"] for item in derive_trajectory([
        point(1_000, 1, 0),
        point(1_000, 2, 0),
    ])] == [1, 3]

    with pytest.raises(NativeFlickingAnalysisError, match="not monotonic"):
        derive_trajectory([point(1_001, 1, 0), point(1_000, 1, 0)])

    with pytest.raises(NativeFlickingAnalysisError, match="timestamp_ms"):
        derive_trajectory([point("not-an-epoch", 1, 0)])


def test_alignment_reports_aligned_partial_failed_and_unavailable_with_coverage():
    perf = performance()

    aligned = align_points_to_challenge(
        [point(1_000, 0, 0), point(1_100, 0, 0), point(1_200, 0, 0)],
        perf,
    )
    assert aligned["status"] == "aligned"
    assert aligned["coverage_ratio"] == 1.0
    assert [item["timestamp_ms"] for item in aligned["points"]] == [1_000, 1_100, 1_200]

    partial = align_points_to_challenge(
        [point(950, 0, 0), point(1_100, 0, 0)],
        perf,
    )
    assert partial["status"] == "partial"
    assert partial["coverage_ratio"] == 0.5
    assert [item["timestamp_ms"] for item in partial["points"]] == [1_100]

    failed = align_points_to_challenge([point(1_300, 0, 0)], perf)
    assert failed["status"] == "failed"
    assert failed["coverage_ratio"] == 0.0
    assert failed["points"] == []

    unavailable = align_points_to_challenge([point(1_000, 0, 0)], None)
    assert unavailable["status"] == "unavailable"
    assert unavailable["coverage_ratio"] is None


def test_metrics_stay_in_raw_counts_without_calibration_and_keep_provenance():
    result = analyze_native_flicking(
        [point(1_000, 3, 4), point(1_100, 0, 10), point(1_200, 0, 20)],
        performance(),
        stats={"kills": 2, "scenario": "test"},
    )

    metrics = result["deterministic"]["metrics"]
    assert result["input_mode"] == "input_native"
    assert result["status"] == "available"
    assert metrics["path_length"] == {
        "key": "path_length",
        "value": 35.0,
        "unit": "raw_counts",
        "availability": "available",
        "provenance": {"kind": "derived", "sources": ["raw_input"]},
        "metric_version": "native_flicking.v1",
        "sample_count": 3,
        "coverage": 1.0,
        "limitations": [],
    }
    assert metrics["mean_speed"] ["unit"] == "raw_counts_per_second"
    assert metrics["mean_speed"]["sample_count"] == 2
    assert metrics["mean_acceleration"]["unit"] == "raw_counts_per_second_squared"
    assert metrics["mean_acceleration"]["sample_count"] == 1
    assert result["evidence"]["sources"]["stats"]["facts"] == {"kills": 2, "scenario": "test"}
    assert "targetInference" not in repr(result)
    assert "degree" not in repr(result).lower()
    assert "cm" not in repr(result).lower()


def test_same_millisecond_records_contribute_to_time_based_metrics():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_100, 3, 0),
            point(1_100, 0, 4),
            point(1_200, 0, 0),
        ],
        performance(),
    )

    metrics = result["deterministic"]["metrics"]
    assert metrics["path_length"]["value"] == 7.0
    assert metrics["mean_speed"]["value"] == 35.0
    assert metrics["mean_speed"]["sample_count"] == 2
    assert metrics["mean_acceleration"]["value"] == -700.0
    assert metrics["mean_acceleration"]["sample_count"] == 1


def test_nonuniform_sampling_uses_duration_weighted_means():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_100, 10, 0),
            point(1_400, 60, 0),
            point(1_500, 10, 0),
        ],
        performance(duration_ms=500),
    )

    metrics = result["deterministic"]["metrics"]
    assert metrics["mean_speed"]["value"] == pytest.approx(160.0)
    assert metrics["mean_speed"]["sample_count"] == 3
    assert metrics["mean_acceleration"]["value"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["mean_acceleration"]["sample_count"] == 2


def test_explicit_calibration_contract_adds_only_its_declared_unit():
    result = analyze_native_flicking(
        [point(1_000, 3, 4), point(1_200, 0, 0)],
        performance(),
        calibration={
            "raw_counts_per_unit": 5,
            "unit": "cm",
            "provenance": "user_input",
            "availability": "available",
        },
    )

    calibrated = result["deterministic"]["metrics"]["calibrated_path_length"]
    assert calibrated["value"] == 1.0
    assert calibrated["unit"] == "cm"
    assert calibrated["calibration_ref"] == "calibration:user_input"
    assert calibrated["provenance"] == {
        "kind": "derived",
        "sources": ["raw_input", "calibration:user_input"],
    }


def test_missing_required_evidence_is_explicit_and_video_is_not_required():
    missing_raw = analyze_native_flicking(None, performance())
    assert missing_raw["status"] == "unavailable"
    assert missing_raw["evidence"]["sources"]["raw_input"]["availability"] == "missing"
    assert missing_raw["evidence"]["alignment"]["status"] == "unavailable"

    missing_anchor = analyze_native_flicking([point(1_000, 1, 0)], None)
    assert missing_anchor["status"] == "unavailable"
    assert missing_anchor["evidence"]["alignment"]["status"] == "unavailable"
    assert "performance_anchor_missing" in missing_anchor["limitations"]

    native_without_video = analyze_native_flicking(
        [point(1_000, 1, 0), point(1_200, 1, 0)],
        performance(),
    )
    assert native_without_video["status"] == "available"
    assert "video" not in native_without_video["evidence"]["sources"]


def test_output_is_deterministic_for_identical_evidence():
    points = [point(1_000, 3, 4), point(1_100, 0, 10), point(1_200, 0, 20)]
    perf = performance(events=[{"timestamp": 0.1, "payload_type": "shotsHit", "count": 1}])

    first = analyze_native_flicking(points, perf, stats={"kills": 1})
    second = analyze_native_flicking(deepcopy(points), deepcopy(perf), stats={"kills": 1})

    assert first == second
    assert first["deterministic"]["timeline"] == [
        {
            "relative_ms": 100.0,
            "source": "performance",
            "payload_type": "shotsHit",
            "count": 1,
        }
    ]
