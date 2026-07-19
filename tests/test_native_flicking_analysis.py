from copy import deepcopy
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kovaak_tracker.native_flicking_analysis import (
    NativeFlickingAnalysisError,
    _event_sparc,
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


def frozen_fingerprint(path):
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
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


def test_legacy_performance_snapshot_with_pause_is_unavailable():
    paused = performance(
        events=[{"timestamp": 11.484201, "payload_type": "pauseCount", "count": 1}],
    )

    result = align_points_to_challenge(
        [point(1_000, 0, 0), point(1_100, 0, 0)],
        paused,
    )

    assert result["status"] == "unavailable"
    assert "performance_anchor_missing" in result["warnings"]


def test_legacy_performance_snapshot_with_stats_pause_count_is_unavailable():
    paused = performance()
    paused["pause_count"] = 1

    result = align_points_to_challenge(
        [point(1_000, 0, 0), point(1_100, 0, 0)],
        paused,
    )

    assert result["status"] == "unavailable"
    assert "performance_anchor_missing" in result["warnings"]


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


def _flick_events(result):
    return [
        item
        for item in result["deterministic"]["timeline"]
        if item.get("event_type") == "flick"
    ]


def test_click_anchored_flick_event_exposes_time_geometry_and_corrections():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_010, 2, 0),
            point(1_020, 4, 0),
            point(1_030, 6, 0),
            point(1_040, 4, 0),
            point(1_050, 1, 0),
            point(1_060, -3, 0),
            point(1_070, -1, 0),
            point(1_080, 0, 0),
            point(1_090, 0, 0, buttons=1),
            point(1_100, 0, 0, buttons=0),
            point(1_120, 0, 0),
        ],
        performance(duration_ms=120),
    )

    event = _flick_events(result)[0]
    assert event["id"] == "flick:1"
    assert event["source"] == "raw_input"
    assert event["segmentation_basis"] == "left_button_press"
    assert event["start_ms"] == 0.0
    assert event["peak_ms"] == 30.0
    assert event["end_ms"] == 70.0
    assert event["settle_end_ms"] == 90.0
    assert event["relative_ms"] == 90.0
    assert event["metrics"] == {
        "movement_duration_ms": 70.0,
        "time_to_peak_ms": 30.0,
        "accel_duration_ms": 30.0,
        "decel_duration_ms": 40.0,
        "settle_duration_ms": 20.0,
        "decel_frac": pytest.approx(4 / 7),
        "peak_position_pct": pytest.approx(100 * 3 / 7),
        "peak_speed": 600.0,
        "path_length": 21.0,
        "displacement": 13.0,
        "path_efficiency": pytest.approx(13 / 21),
        "straightness": pytest.approx(13 / 21),
        "reverse_ratio": 0.25,
        "direction_reverse_ratio": pytest.approx(4 / 21),
        "corrective_count": 1,
        "submovement_count": 2,
        "trough_depth_ratio": pytest.approx(1 / 6),
        "submovement_overlap": pytest.approx(1 / 6),
        "sparc": None,
    }
    assert "sparc_requires_at_least_eight_resampled_samples" in event["limitations"]
    assert "trough_depth_ratio_not_temporal_overlap" in event["limitations"]
    assert event["metrics"]["submovement_overlap"] == pytest.approx(1 / 6)
    overlap_metric = result["deterministic"]["metrics"]["submovement_overlap"]
    assert overlap_metric["value"] == pytest.approx(1 / 6)
    assert "trough_depth_ratio_not_temporal_overlap" in overlap_metric["limitations"]


def test_multiple_flicks_produce_stable_distributions_and_descriptive_outliers():
    points = [point(1_000, 0, 0)]
    timestamp = 1_000
    for distance in (1, 2, 3, 4, 5, 20):
        timestamp += 10
        points.append(point(timestamp, distance, 0))
        timestamp += 10
        points.append(point(timestamp, 0, 0))
        timestamp += 10
        points.append(point(timestamp, 0, 0, buttons=1))
        timestamp += 1
        points.append(point(timestamp, 0, 0, buttons=0))
        timestamp += 9
        points.append(point(timestamp, 0, 0))

    result = analyze_native_flicking(
        points,
        performance(duration_ms=timestamp - 1_000),
    )

    events = _flick_events(result)
    assert [event["id"] for event in events] == [f"flick:{i}" for i in range(1, 7)]
    assert [event["metrics"]["peak_speed"] for event in events] == [
        100.0,
        200.0,
        300.0,
        400.0,
        500.0,
        2_000.0,
    ]

    peak_speed = result["deterministic"]["metrics"]["peak_speed"]
    assert result["deterministic"]["metrics"]["flick_count"]["value"] == 6
    assert peak_speed["value"] == 300.0
    assert peak_speed["median"] == 300.0
    assert peak_speed["p25"] == 200.0
    assert peak_speed["p75"] == 500.0
    assert peak_speed["p90"] == 2_000.0
    assert peak_speed["iqr"] == 300.0
    expected_mean = sum((100, 200, 300, 400, 500, 2_000)) / 6
    expected_std = (
        sum((value - expected_mean) ** 2 for value in (100, 200, 300, 400, 500, 2_000))
        / 6
    ) ** 0.5
    assert peak_speed["mean"] == pytest.approx(expected_mean)
    assert peak_speed["std"] == pytest.approx(expected_std)
    assert peak_speed["min"] == 100.0
    assert peak_speed["max"] == 2_000.0
    assert peak_speed["outlier_method"] == "tukey_1_5_iqr_descriptive"
    assert peak_speed["outlier_refs"] == ["flick:6"]
    assert peak_speed["sample_refs"] == [f"flick:{i}" for i in range(1, 7)]
    assert peak_speed["sample_count"] == 6


def test_sparc_uses_an_explicit_uniform_resampling_contract():
    speed_distances = [2, 1, 0, 1, 2, 1, 0, 1]
    points = [point(1_000, 0, 0)]
    for index, distance in enumerate(speed_distances, start=1):
        buttons = 1 if index == len(speed_distances) else 0
        points.append(point(1_000 + index * 1_000, distance, 0, buttons=buttons))
    points.extend([
        point(9_500, 0, 0, buttons=0),
        point(10_000, 0, 0),
    ])

    result = analyze_native_flicking(points, performance(duration_ms=9_000))
    event = _flick_events(result)[0]

    assert event["metrics"]["sparc"] == pytest.approx(-(5 ** 0.5) / 2)
    assert result["deterministic"]["metrics"]["sparc"]["metric_version"] == (
        "native_flicking.sparc.v2"
    )
    assert event["sampling"]["resample_step_ms"] == 1_000
    assert event["sampling"]["resampled_sample_count"] == 8
    assert "sparc_cross_polling_comparability_unverified" in event["limitations"]


def test_native_sparc_is_invariant_to_uniform_time_scaling():
    profile = [2.0, 1.0, 0.0, 1.0, 2.0, 1.0, 0.0, 1.0]

    fast, _, fast_limitation = _event_sparc([
        {"duration_ms": 1, "speed": value}
        for value in profile
    ])
    slow, _, slow_limitation = _event_sparc([
        {"duration_ms": 10, "speed": value}
        for value in profile
    ])

    assert fast == pytest.approx(slow)
    assert fast_limitation == slow_limitation


def test_high_polling_same_millisecond_records_remain_finite_and_preserve_path():
    points = [point(1_000, 0, 0)]
    points.extend(point(1_001, 1, 0) for _ in range(8))
    points.extend([
        point(1_002, 0, 0, buttons=1),
        point(1_003, 0, 0, buttons=0),
        point(1_004, 0, 0),
    ])

    result = analyze_native_flicking(points, performance(duration_ms=4))
    event = _flick_events(result)[0]

    assert event["metrics"]["peak_speed"] == 8_000.0
    assert event["metrics"]["path_length"] == 8.0
    assert event["metrics"]["displacement"] == 8.0
    assert event["metrics"]["path_efficiency"] == 1.0
    assert event["metrics"]["settle_duration_ms"] == 1.0


def test_same_millisecond_cancellation_preserves_path_but_not_false_straightness():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_001, 3, 0),
            point(1_001, -3, 0),
            point(1_002, 0, 0, buttons=1),
            point(1_003, 0, 0, buttons=0),
            point(1_004, 0, 0),
        ],
        performance(duration_ms=4),
    )
    event = _flick_events(result)[0]

    assert event["metrics"]["path_length"] == 6.0
    assert event["metrics"]["displacement"] == 0.0
    assert event["metrics"]["path_efficiency"] is None
    assert event["metrics"]["straightness"] is None
    assert "zero_net_displacement" in event["limitations"]


def test_performance_target_facts_remain_source_events_not_target_relative_metrics():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_010, 2, 0),
            point(1_020, 0, 0, buttons=1),
            point(1_030, 0, 0, buttons=0),
            point(1_040, 0, 0),
        ],
        performance(
            duration_ms=40,
            events=[
                {"timestamp": 0.015, "payload_type": "overshots", "count": 1},
                {"timestamp": 0.018, "payload_type": "targetSize", "value": 2.0},
            ],
        ),
    )

    performance_events = [
        item
        for item in result["deterministic"]["timeline"]
        if item.get("source") == "performance"
    ]
    assert performance_events == [
        {
            "relative_ms": 15.0,
            "source": "performance",
            "payload_type": "overshots",
            "count": 1,
        },
        {
            "relative_ms": 18.0,
            "source": "performance",
            "payload_type": "targetSize",
            "value": 2.0,
        },
    ]
    metric_keys = set(result["deterministic"]["metrics"])
    event_metric_keys = set(_flick_events(result)[0]["metrics"])
    assert not metric_keys.intersection({
        "target_relative_error",
        "target_error",
        "overshoot_distance",
        "undershoot_distance",
        "throughput",
    })
    assert not event_metric_keys.intersection({
        "target_relative_error",
        "target_error",
        "overshoot_distance",
        "undershoot_distance",
        "throughput",
    })
    assert "target_relative_facts_unavailable" in result["limitations"]


def test_nonuniform_flick_timing_uses_elapsed_time_not_sample_position():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_100, 10, 0),
            point(1_400, 60, 0),
            point(1_500, 10, 0),
            point(1_600, 0, 0, buttons=1),
        ],
        performance(duration_ms=600),
    )
    event = _flick_events(result)[0]

    assert event["start_ms"] == 0.0
    assert event["peak_ms"] == 400.0
    assert event["end_ms"] == 500.0
    assert event["settle_end_ms"] == 600.0
    assert event["metrics"]["peak_speed"] == 200.0
    assert event["metrics"]["movement_duration_ms"] == 500.0
    assert event["metrics"]["time_to_peak_ms"] == 400.0
    assert event["metrics"]["decel_duration_ms"] == 100.0
    assert event["metrics"]["decel_frac"] == pytest.approx(0.2)


def test_partial_alignment_marks_flick_events_and_distributions_limited():
    result = analyze_native_flicking(
        [
            point(1_100, 0, 0),
            point(1_200, 10, 0),
            point(1_300, 0, 0, buttons=1),
            point(1_400, 0, 0, buttons=0),
            point(1_500, 0, 0),
        ],
        performance(start_ms=1_000, duration_ms=1_000),
    )

    assert result["status"] == "partial"
    event = _flick_events(result)[0]
    assert event["coverage"] == pytest.approx(0.4)
    assert "alignment_partial" in event["limitations"]
    assert "alignment_partial" in result["deterministic"]["metrics"]["peak_speed"]["limitations"]


def test_no_left_click_anchor_does_not_invent_flick_events():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_010, 5, 0),
            point(1_020, 0, 0),
            point(1_030, 0, 0),
        ],
        performance(duration_ms=30),
    )

    assert _flick_events(result) == []
    assert "left_click_anchors_missing" in result["limitations"]
    assert result["deterministic"]["metrics"]["flick_count"]["value"] == 0


def test_click_without_movement_is_not_counted_as_a_flick():
    result = analyze_native_flicking(
        [
            point(1_000, 0, 0),
            point(1_010, 0, 0, buttons=1),
            point(1_020, 0, 0, buttons=0),
            point(1_030, 0, 0),
        ],
        performance(duration_ms=30),
    )

    assert _flick_events(result) == []
    assert result["deterministic"]["metrics"]["flick_count"]["value"] == 0
    assert "no_movement_clicks_ignored" in result["limitations"]
    assert "left_click_anchors_missing" not in result["limitations"]


def test_worker_uses_unchanged_frozen_native_snapshot(tmp_path):
    from webapp.backend import worker

    stats_path = tmp_path / "stats.csv"
    performance_path = tmp_path / "performance.perf"
    trace_path = tmp_path / "trace.bin"
    for path, payload in (
        (stats_path, b"stats"),
        (performance_path, b"performance"),
        (trace_path, b"trace"),
    ):
        path.write_bytes(payload)
    snapshot = {
        "sources": {
            "stats": {"path": str(stats_path), "fingerprint": frozen_fingerprint(stats_path)},
            "performance": {
                "path": str(performance_path),
                "fingerprint": frozen_fingerprint(performance_path),
            },
        },
        "trace": {"path": str(trace_path), "fingerprint": frozen_fingerprint(trace_path)},
    }
    parsed_stats = SimpleNamespace(
        summary={}, config={}, scenario="Scenario", cm_per_360=None, fov=None,
    )

    with patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=[point(1_000, 0, 0), point(1_200, 1, 0)],
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes", return_value=parsed_stats,
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=performance(),
    ):
        result = worker.run_native_analysis(snapshot)

    assert result["status"] == "available"


@pytest.mark.parametrize("changed_source", ["stats", "performance", "raw_input"])
def test_worker_rejects_native_source_changed_after_submission(tmp_path, changed_source):
    from webapp.backend import worker

    paths = {
        "stats": tmp_path / "stats.csv",
        "performance": tmp_path / "performance.perf",
        "raw_input": tmp_path / "trace.bin",
    }
    for kind, path in paths.items():
        path.write_bytes(f"original-{kind}".encode())
    snapshot = {
        "sources": {
            "stats": {
                "path": str(paths["stats"]),
                "fingerprint": frozen_fingerprint(paths["stats"]),
            },
            "performance": {
                "path": str(paths["performance"]),
                "fingerprint": frozen_fingerprint(paths["performance"]),
            },
        },
        "trace": {
            "path": str(paths["raw_input"]),
            "fingerprint": frozen_fingerprint(paths["raw_input"]),
        },
    }
    paths[changed_source].write_bytes(f"changed-{changed_source}".encode())

    with pytest.raises(worker.SourceSnapshotChangedError, match=changed_source):
        worker.run_native_analysis(snapshot)


def test_worker_does_not_reopen_source_after_frozen_read(
    monkeypatch, tmp_path,
):
    from webapp.backend import worker

    stats_path = tmp_path / "stats.csv"
    performance_path = tmp_path / "performance.perf"
    trace_path = tmp_path / "trace.bin"
    for path, payload in (
        (stats_path, b"original-stats"),
        (performance_path, b"original-performance"),
        (trace_path, b"original-trace"),
    ):
        path.write_bytes(payload)
    snapshot = {
        "sources": {
            "stats": {"path": str(stats_path), "fingerprint": frozen_fingerprint(stats_path)},
            "performance": {
                "path": str(performance_path),
                "fingerprint": frozen_fingerprint(performance_path),
            },
        },
        "trace": {"path": str(trace_path), "fingerprint": frozen_fingerprint(trace_path)},
    }
    original_read = worker._read_frozen_source_bytes
    replaced = False

    def read_then_replace(kind, source):
        nonlocal replaced
        data = original_read(kind, source)
        if kind == "stats" and not replaced:
            replaced = True
            stats_path.write_bytes(b"changed-after-initial-verify")
        return data

    monkeypatch.setattr(worker, "_read_frozen_source_bytes", read_then_replace)
    parsed_stats = SimpleNamespace(
        summary={}, config={}, scenario="Scenario", cm_per_360=None, fov=None,
    )
    with patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=[point(1_000, 0, 0), point(1_200, 1, 0)],
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes", return_value=parsed_stats,
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=performance(),
    ):
        result = worker.run_native_analysis(snapshot)

    assert result["status"] == "available"
    assert stats_path.read_bytes() == b"changed-after-initial-verify"


@pytest.mark.parametrize("changed_source", ["stats", "performance", "raw_input"])
def test_worker_consumes_the_same_bytes_it_fingerprinted(
    monkeypatch, tmp_path, changed_source,
):
    from webapp.backend import worker

    paths = {
        "stats": tmp_path / "stats.csv",
        "performance": tmp_path / "performance.perf",
        "raw_input": tmp_path / "trace.bin",
    }
    original_bytes = {
        kind: f"original-{kind}".encode()
        for kind in paths
    }
    replacement_bytes = {
        kind: f"replacement-{kind}".encode()
        for kind in paths
    }
    original_times = {}
    for kind, path in paths.items():
        path.write_bytes(original_bytes[kind])
        stat = path.stat()
        original_times[kind] = (stat.st_atime_ns, stat.st_mtime_ns)
    snapshot = {
        "sources": {
            "stats": {
                "path": str(paths["stats"]),
                "fingerprint": frozen_fingerprint(paths["stats"]),
            },
            "performance": {
                "path": str(paths["performance"]),
                "fingerprint": frozen_fingerprint(paths["performance"]),
            },
        },
        "trace": {
            "path": str(paths["raw_input"]),
            "fingerprint": frozen_fingerprint(paths["raw_input"]),
        },
    }
    stats_a = SimpleNamespace(
        summary={}, config={}, scenario="original", cm_per_360=None, fov=None,
    )
    stats_b = SimpleNamespace(
        summary={}, config={}, scenario="replacement", cm_per_360=None, fov=None,
    )
    performance_a = performance(start_ms=1_000)
    performance_b = performance(start_ms=2_000)
    trace_a = [point(1_000, 1, 0), point(1_200, 2, 0)]
    trace_b = [point(1_000, 9, 0), point(1_200, 8, 0)]
    captured = {}

    def transient_value(kind, original, replacement):
        if changed_source != kind:
            return original
        path = paths[kind]
        path.write_bytes(replacement_bytes[kind])
        try:
            return replacement
        finally:
            path.write_bytes(original_bytes[kind])
            os.utime(path, ns=original_times[kind])

    def analyze(trace_points, parsed_performance, *, stats):
        captured.update({
            "trace": trace_points,
            "performance": parsed_performance,
            "stats": stats,
        })
        return {"status": "available"}

    with patch(
        "kovaak_tracker.csv_parser.parse_stats_csv",
        side_effect=lambda _path: transient_value("stats", stats_a, stats_b),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_file",
        side_effect=lambda _path: transient_value(
            "performance", performance_a, performance_b,
        ),
    ), patch(
        "webapp.backend.kovaak_run_store.read_mouse_snapshot",
        side_effect=lambda _path: transient_value("raw_input", trace_a, trace_b),
    ), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        create=True,
        side_effect=lambda data, **_kwargs: stats_a
        if data == original_bytes["stats"] else stats_b,
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        side_effect=lambda data: performance_a
        if data == original_bytes["performance"] else performance_b,
    ), patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        create=True,
        side_effect=lambda data: trace_a
        if data == original_bytes["raw_input"] else trace_b,
    ), patch(
        "kovaak_tracker.native_flicking_analysis.analyze_native_flicking",
        side_effect=analyze,
    ):
        worker.run_native_analysis(snapshot)

    assert captured["stats"]["scenario"] == "original"
    assert captured["performance"]["challenge_start_utc"] == 1_000
    assert captured["trace"] == trace_a


def test_worker_rejects_native_snapshot_without_trace_fingerprint(tmp_path):
    from webapp.backend import worker

    stats_path = tmp_path / "stats.csv"
    performance_path = tmp_path / "performance.perf"
    trace_path = tmp_path / "trace.bin"
    for path in (stats_path, performance_path, trace_path):
        path.write_bytes(b"source")
    snapshot = {
        "sources": {
            "stats": {"path": str(stats_path), "fingerprint": frozen_fingerprint(stats_path)},
            "performance": {
                "path": str(performance_path),
                "fingerprint": frozen_fingerprint(performance_path),
            },
        },
        "trace": {"path": str(trace_path)},
    }

    with pytest.raises(worker.SourceSnapshotChangedError, match="raw_input identity missing"):
        worker.run_native_analysis(snapshot)
