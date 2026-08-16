"""Generic aim-family layers: dynamic clicking, switching, tracking."""
from __future__ import annotations

import pytest

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
)
from kovaak_tracker.generic_aim_family_analysis import (
    associate_generic_dynamic_clicks_v1,
    associate_generic_switching_v1,
    associate_generic_tracking_v1,
    build_generic_family_metric_records_v1,
    extend_analysis_evidence_with_generic_family_v1,
)

_WINDOW = {
    "schema_version": "canonical_time_window.v1",
    "start_ms": 1_000,
    "end_ms": 2_000,
    "duration_ms": 1_000,
    "window_semantics": "half_open",
    "timebase_version": "test.v1",
    "start_source": "fixture",
    "end_source": "fixture",
    "warnings": [],
}
DEG_PER_PX = 103.0 / 1920.0


def _ftrack(index: int, *, path: list[tuple[int, float, float]], **overrides) -> dict:
    xs = [x for _, x, _ in path]
    ys = [y for _, _, y in path]
    track = {
        "track_ref": f"analysis:1:generic-target-track:{index}",
        "birth_ms": path[0][0],
        "death_ms": path[-1][0],
        "shape": "sphere",
        "x": sorted(xs)[len(xs) // 2],
        "y": sorted(ys)[len(ys) // 2],
        "end_x": xs[-1],
        "end_y": ys[-1],
        "half_width_px": 20.0,
        "half_height_px": 15.0,
        "median_area": 900.0,
        "sample_count": len(path),
        "real_sample_count": len(path),
        "path": [
            {"t": t, "x": x, "y": y} for t, x, y in path
        ],
    }
    track.update(overrides)
    return track


def test_dynamic_clicking_adds_target_speed_to_hit_clicks():
    visual = {
        "tracks": [
            # Sweeps through the crosshair around t=1_050 at ~294 px/s.
            _ftrack(
                1,
                path=[(1_000, 900.0, 540.0), (1_050, 955.0, 543.0), (1_100, 1_010.0, 543.0)],
            ),
            _ftrack(2, path=[(1_000, 400.0, 300.0), (1_500, 420.0, 310.0)]),
        ],
        "frame_coverage": 0.9,
    }
    association = associate_generic_dynamic_clicks_v1(
        analysis_ref="analysis:1",
        generic_visual_result=visual,
        click_times_ms=[1_050, 1_400],
        kill_records=[{"canonical_time_ms": 1_090, "kill_index": 1}],
        viewport_size=[1920, 1080],
        deg_per_px=DEG_PER_PX,
    )
    hit = next(
        outcome for outcome in association["click_outcomes"]
        if outcome["outcome"] == "hit"
    )
    assert "target_speed_deg_per_s" in hit
    # 55 px over 50 ms = 1100 px/s → ×(103/1920) deg per px.
    assert hit["target_speed_deg_per_s"] == pytest.approx(
        55 / 50 * 1000 * DEG_PER_PX, rel=0.2,
    )
    summary = association["target_speed_summary"]
    assert summary["hit_count_with_speed"] == 1


def test_switching_episodes_measure_transition_time():
    visual = {
        "tracks": [
            _ftrack(1, path=[(1_000, 955.0, 543.0), (1_100, 955.0, 543.0)]),
            _ftrack(2, path=[(1_560, 700.0, 540.0), (1_700, 700.0, 540.0)]),
        ],
        "frame_coverage": 0.9,
    }
    association = associate_generic_switching_v1(
        analysis_ref="analysis:1",
        generic_visual_result=visual,
        click_times_ms=[1_050, 1_120, 1_620],
        kill_records=[
            {"canonical_time_ms": 1_090, "kill_index": 1},
            {"canonical_time_ms": 1_600, "kill_index": 2},
        ],
        viewport_size=[1920, 1080],
        deg_per_px=DEG_PER_PX,
    )
    episodes = association["switch_episodes"]
    assert len(episodes) == 2
    assert episodes[0]["transition_ms"] == 30   # 1_120 − 1_090
    assert episodes[0]["next_target_track_ref"] == (
        "analysis:1:generic-target-track:2"
    )
    assert episodes[1]["transition_ms"] == 20   # 1_620 − 1_600


def test_tracking_error_series_and_gate():
    visual = {
        "tracks": [
            # Drifts from on-target to off-target across the window.
            _ftrack(
                1,
                path=[
                    (1_000 + 50 * step, 940.0 + 3.0 * step, 540.0)
                    for step in range(20)
                ],
            ),
        ],
        "frame_coverage": 0.95,
    }
    association = associate_generic_tracking_v1(
        analysis_ref="analysis:1",
        generic_visual_result=visual,
        canonical_time_window=_WINDOW,
        viewport_size=[1920, 1080],
        deg_per_px=DEG_PER_PX,
    )
    assert association["coverage"] == pytest.approx(1.0)
    assert association["gate"]["passed"] is True
    assert association["error_median_deg"] is not None
    assert 0.0 < association["in_target_ratio"] < 1.0
    assert association["error_p90_deg"] >= association["error_median_deg"]

    sparse = {
        "tracks": [
            _ftrack(1, path=[(1_000, 940.0, 540.0), (1_050, 950.0, 540.0)]),
        ],
        "frame_coverage": 0.2,
    }
    failed = associate_generic_tracking_v1(
        analysis_ref="analysis:1",
        generic_visual_result=sparse,
        canonical_time_window=_WINDOW,
        viewport_size=[1920, 1080],
        deg_per_px=DEG_PER_PX,
    )
    assert failed["gate"]["passed"] is False
    assert "tracking_coverage_below_threshold" in failed["gate"]["reasons"]


def test_family_metric_records_and_evidence_extension():
    visual = {
        "tracks": [
            _ftrack(1, path=[(1_000, 955.0, 543.0), (1_100, 955.0, 543.0)]),
            _ftrack(2, path=[(1_560, 700.0, 540.0), (1_700, 700.0, 540.0)]),
        ],
        "frame_coverage": 0.9,
    }
    switching = associate_generic_switching_v1(
        analysis_ref="analysis:7",
        generic_visual_result=visual,
        click_times_ms=[1_050, 1_120, 1_620],
        kill_records=[
            {"canonical_time_ms": 1_090, "kill_index": 1},
            {"canonical_time_ms": 1_600, "kill_index": 2},
        ],
        viewport_size=[1920, 1080],
        deg_per_px=DEG_PER_PX,
    )
    records = build_generic_family_metric_records_v1(
        switching, aim_family="switching", source_ref="run:7:video:aa",
    )
    keys = {record["metric_key"] for record in records}
    assert {
        "switching.generic.episode_count",
        "switching.generic.transition_time_ms",
        "switching.generic.hit_clicks",
        "switching.generic.kill_pairing_rate",
    } <= keys
    assert not any(key.startswith("static_clicking.") for key in keys)

    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:7",
        canonical_time_window=_WINDOW,
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref="run:7:stats",
        performance_source_ref="run:7:performance",
    )
    extended = extend_analysis_evidence_with_generic_family_v1(
        artifact,
        visual,
        switching,
        aim_family="switching",
        video_source_ref="run:7:video:abcdef0123456789",
    )
    kinds = {
        event["event_kind"]
        for bundle in extended["event_bundles"]
        for event in bundle["events"]
    }
    assert "generic_switch_episode" in kinds
