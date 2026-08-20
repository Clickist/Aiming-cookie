"""Generic static-clicking analysis: tracks, association, evidence extension."""
from __future__ import annotations

import pytest

from kovaak_tracker.analysis_evidence import (
    build_analysis_evidence_artifact_v1,
)
from kovaak_tracker.generic_static_clicking_analysis import (
    associate_generic_static_clicks_v1,
    build_stationary_target_tracks_v1,
    extend_analysis_evidence_with_generic_static_clicking_v1,
    extract_left_click_rising_edges_v1,
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


def _blob(x: float, y: float, *, width: int = 40, height: int = 30) -> dict:
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "area": width * height * 0.7,
        "visible_radius": 18.0,
        "shape": "sphere",
        "aspect": width / height,
        "fill": 0.7,
        "circularity": 0.7,
        "confidence": 0.7,
    }


def _detection(time_ms: int, blobs: list[dict]) -> dict:
    return {"canonical_time_ms": time_ms, "targets": blobs}


def _track(index: int, **overrides) -> dict:
    track = {
        "track_ref": f"analysis:1:generic-target-track:{index}",
        "birth_ms": 1_000,
        "death_ms": 1_500,
        "shape": "sphere",
        "x": 400.0,
        "y": 300.0,
        "half_width_px": 20.0,
        "half_height_px": 15.0,
        "median_area": 900.0,
        "sample_count": 30,
    }
    track.update(overrides)
    return track


def test_stationary_tracks_rebuild_birth_death_and_position_medians():
    frames = [
        _detection(1_000, [_blob(100, 200), _blob(500, 200)]),
        _detection(1_017, [_blob(102, 200), _blob(500, 201)]),
        _detection(1_033, [_blob(101, 200)]),
        _detection(1_100, [_blob(100, 200)]),
    ]
    tracks = build_stationary_target_tracks_v1(
        frames, analysis_ref="analysis:1",
        merge_radius_px=24.0, max_gap_ms=200.0,
    )
    assert len(tracks) == 2
    right = next(track for track in tracks if track["x"] > 300)
    assert (right["birth_ms"], right["death_ms"]) == (1_000, 1_017)
    assert right["sample_count"] == 2
    left = next(track for track in tracks if track["x"] < 300)
    assert (left["birth_ms"], left["death_ms"]) == (1_000, 1_100)
    assert left["sample_count"] == 4
    assert all(
        track["track_ref"].startswith("analysis:1:generic-target-track:")
        for track in tracks
    )


def test_track_fragments_rebind_only_within_the_window():
    frames = [
        _detection(1_000, [_blob(100, 200)]),
        _detection(1_017, [_blob(100, 200)]),
        # Motion-blur fragment: same place, far below half the median area.
        _detection(1_033, [{**_blob(100, 200), "area": 100.0, "width": 10, "height": 8}]),
        # Full sighting resumes → the fragment was a bridge, not a new target.
        _detection(1_050, [_blob(100, 200)]),
    ]
    tracks = build_stationary_target_tracks_v1(
        frames, analysis_ref="analysis:1",
        merge_radius_px=24.0, max_gap_ms=200.0,
    )
    assert len(tracks) == 1
    assert (tracks[0]["birth_ms"], tracks[0]["death_ms"]) == (1_000, 1_050)

    stale = [
        _detection(1_000, [_blob(100, 200)]),
        _detection(1_017, [_blob(100, 200)]),
        # Fragment 500 ms later: outside the rebind window, and a lone sample
        # never publishes as a track.
        _detection(1_500, [{**_blob(100, 200), "area": 100.0, "width": 10, "height": 8}]),
    ]
    tracks = build_stationary_target_tracks_v1(
        stale, analysis_ref="analysis:1",
        merge_radius_px=24.0, max_gap_ms=200.0,
    )
    assert len(tracks) == 1
    assert tracks[0]["death_ms"] == 1_017


def test_left_click_rising_edges_ignore_held_and_repeated_presses():
    points = [
        {"timestamp_ms": 1_000, "buttons": 0},
        {"timestamp_ms": 1_010, "buttons": 1},
        {"timestamp_ms": 1_020, "buttons": 1},
        {"timestamp_ms": 1_030, "buttons": 0},
        {"timestamp_ms": 1_040, "buttons": 1},
        {"timestamp_ms": 900, "buttons": 1},
    ]
    assert extract_left_click_rising_edges_v1(
        points, start_ms=1_000, end_ms=2_000,
    ) == [1_010, 1_040]


def test_association_classifies_clicks_and_pairs_kills_with_residuals():
    visual = {
        "tracks": [
            _track(1, x=955.0, y=543.0, death_ms=1_100, sample_count=6),
            _track(2, x=400.0, y=300.0, death_ms=1_500),
        ],
        "frame_coverage": 0.9,
    }
    association = associate_generic_static_clicks_v1(
        analysis_ref="analysis:1",
        generic_visual_result=visual,
        click_times_ms=[1_050, 1_400, 1_800],
        kill_records=[{"canonical_time_ms": 1_090, "kill_index": 1}],
        viewport_size=[1920, 1080],
        deg_per_px=103.0 / 1920.0,
    )
    outcomes = {
        outcome["click_time_ms"]: outcome["outcome"]
        for outcome in association["click_outcomes"]
    }
    # 1_050: the near-center target is alive → hit. 1_400: only the far
    # target is engaged (the killed one died 300 ms earlier) → miss with a
    # vector. 1_800: nothing engaged within the lookback → no_target.
    assert outcomes == {1_050: "hit", 1_400: "miss", 1_800: "no_target"}
    miss = next(
        outcome for outcome in association["click_outcomes"]
        if outcome["outcome"] == "miss"
    )
    assert miss["miss_vector_px"]["x"] == pytest.approx(-560.0)
    assert miss["miss_vector_px"]["y"] == pytest.approx(-240.0)
    assert miss["miss_vector_deg"]["x"] == pytest.approx(-560.0 * 103.0 / 1920.0)
    assert association["kills_paired"] == 1
    residual = association["kill_residuals"][0]
    assert residual["residual_px"] == {
        "x": pytest.approx(-5.0),
        "y": pytest.approx(3.0),
        "distance": pytest.approx((25 + 9) ** 0.5),
    }
    assert residual["target_track_ref"] == "analysis:1:generic-target-track:1"
    assert association["gate"]["passed"] is True


def test_association_gate_fails_closed_on_low_kill_pairing():
    visual = {
        "tracks": [_track(1, death_ms=1_100)],
        "frame_coverage": 0.1,
    }
    kills = [
        {"canonical_time_ms": 1_050 + 80 * index, "kill_index": index}
        for index in range(10)
    ]
    association = associate_generic_static_clicks_v1(
        analysis_ref="analysis:1",
        generic_visual_result=visual,
        click_times_ms=[1_050],
        kill_records=kills,
        viewport_size=[1920, 1080],
        deg_per_px=None,
    )
    assert association["kill_pairing_rate"] is not None
    assert association["kill_pairing_rate"] < 0.5
    assert association["gate"]["passed"] is False
    assert "kill_pairing_rate_below_threshold" in association["gate"]["reasons"]
    assert "frame_coverage_below_threshold" in association["gate"]["reasons"]
    assert "angular_calibration_unavailable" in association["limitations"]


def test_evidence_extension_appends_generic_bundles_and_metrics():
    artifact = build_analysis_evidence_artifact_v1(
        analysis_ref="analysis:7",
        canonical_time_window=_WINDOW,
        scenario_profile_ref=None,
        stats=None,
        performance=None,
        stats_source_ref="run:7:stats",
        performance_source_ref="run:7:performance",
    )
    visual = {
        "schema_version": "generic_static_visual_result.v1",
        "analysis_ref": "analysis:7",
        "tracks": [
            _track(1, x=955.0, y=543.0, death_ms=1_100, sample_count=6),
        ],
        "frame_coverage": 0.9,
        "detector": {"shape_consistency": 0.95},
        "limitations": [],
    }
    association = associate_generic_static_clicks_v1(
        analysis_ref="analysis:7",
        generic_visual_result=visual,
        click_times_ms=[1_050, 1_200],
        kill_records=[{"canonical_time_ms": 1_090, "kill_index": 1}],
        viewport_size=[1920, 1080],
        deg_per_px=103.0 / 1920.0,
    )
    extended = extend_analysis_evidence_with_generic_static_clicking_v1(
        artifact,
        visual,
        association,
        video_source_ref="run:7:video:abcdef0123456789",
    )
    kinds = {
        event["event_kind"]
        for bundle in extended["event_bundles"]
        for event in bundle["events"]
    }
    assert {
        "generic_target_track", "generic_click_outcome", "generic_kill_residual",
    } <= kinds
    metric_keys = {
        record["metric_key"] for record in extended["metric_records"]
    }
    assert {
        "static_clicking.generic.hit_clicks",
        "static_clicking.generic.miss_distance_deg",
        "static_clicking.generic.kill_pairing_rate",
        "static_clicking.generic.frame_coverage",
    } <= metric_keys


def _write_synthetic_clip(path, *, frames: int = 120) -> None:
    """640x360@60fps: left+center spheres die at frame 60, right survives."""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 60, (640, 360),
    )
    assert writer.isOpened()
    for index in range(frames):
        frame = np.full((360, 640, 3), (110, 130, 120), dtype=np.uint8)
        cv2.circle(frame, (450, 180), 20, (180, 180, 40), -1)
        if index < 60:
            cv2.circle(frame, (200, 180), 20, (180, 180, 40), -1)
            cv2.circle(frame, (320, 180), 20, (180, 180, 40), -1)
        writer.write(frame)
    writer.release()


def test_detection_end_to_end_uses_preroll_and_rebuilds_tracks(tmp_path):
    from kovaak_tracker.generic_static_clicking_analysis import (
        run_generic_static_clicking_detection_v1,
    )

    media = tmp_path / "clip.mp4"
    _write_synthetic_clip(media)
    mapping = {
        "schema_version": "visual_video_time_mapping.v2",
        "source_pts_origin_ms": 0.0,
        "canonical_origin_ms": 10_000,
        "mapping_method": "run_owned_exact_canonical_clip",
        "timebase_version": "test.v1",
        "decode_preroll_ms": 121.97,
    }
    result = run_generic_static_clicking_detection_v1(
        media_path=str(media),
        analysis_ref="analysis:1",
        canonical_time_window=_WINDOW | {"end_ms": 12_000, "duration_ms": 2_000},
        video_time_mapping=mapping,
    )
    assert result["detector"]["hypothesis"]["name"] == "saturated_hue_peak"
    assert result["detector"]["shape"] == "sphere"
    assert result["frame_coverage"] == pytest.approx(1.0)
    tracks = {round(track["x"]): track for track in result["tracks"]}
    # Three stationary targets; the first frame maps to canonical 10 122 via
    # the decode preroll, not to the window start.
    assert set(tracks) == {200, 320, 450}
    assert tracks[200]["birth_ms"] == pytest.approx(10_122, abs=34)
    assert tracks[200]["death_ms"] == pytest.approx(11_105, abs=34)
    assert tracks[320]["death_ms"] == pytest.approx(11_105, abs=34)
    assert tracks[450]["death_ms"] >= 11_900

    association = associate_generic_static_clicks_v1(
        analysis_ref="analysis:1",
        generic_visual_result=result,
        click_times_ms=[10_200, 11_500],
        kill_records=[{"canonical_time_ms": 11_100, "kill_index": 1}],
        viewport_size=[640, 360],
        deg_per_px=None,
    )
    outcomes = {
        outcome["click_time_ms"]: outcome["outcome"]
        for outcome in association["click_outcomes"]
    }
    assert outcomes == {10_200: "hit", 11_500: "miss"}
    miss = next(
        outcome for outcome in association["click_outcomes"]
        if outcome["outcome"] == "miss"
    )
    assert miss["miss_vector_px"]["x"] == pytest.approx(130.0, abs=2.0)
    assert association["kills_paired"] == 1
    residual = association["kill_residuals"][0]
    assert residual["residual_px"]["distance"] == pytest.approx(0.0, abs=3.0)


def test_moving_target_keeps_one_track_with_the_velocity_gate():
    frames = []
    for index in range(30):
        frames.append(_detection(
            1_000 + 17 * index,
            [
                _blob(100.0 + 8.0 * index, 200.0),
                _blob(100.0 + 2.0 * index, 400.0),
            ],
        ))
    tracks = build_stationary_target_tracks_v1(
        frames, analysis_ref="analysis:1",
        merge_radius_px=24.0, max_gap_ms=200.0,
    )
    assert len(tracks) == 2
    fast = max(tracks, key=lambda track: track["end_x"])
    slow = min(tracks, key=lambda track: track["end_x"])
    assert fast["sample_count"] == 30
    assert slow["sample_count"] == 30
    assert fast["end_x"] == pytest.approx(100.0 + 8.0 * 29, abs=2.0)
    assert fast["birth_ms"] == 1_000
    assert fast["death_ms"] == 1_000 + 17 * 29
    # The bounded path series keeps the motion for family metric layers.
    assert fast["path"][0]["t"] == 1_000
    assert fast["path"][-1]["x"] == pytest.approx(100.0 + 8.0 * 29, abs=2.0)


def test_hypothesis_pass_grabs_unsampled_frames_instead_of_decoding_them(monkeypatch):
    import numpy as np
    from kovaak_tracker.generic_static_clicking_analysis import (
        GenericVisualPreprocessingUnavailable,
        HYPOTHESIS_SAMPLE_FRAMES,
        run_generic_static_clicking_detection_v1,
    )

    total_frames = HYPOTHESIS_SAMPLE_FRAMES * 3
    counts = {"grab": 0, "retrieve": 0}

    class _FakeCapture:
        def __init__(self, _path):
            self._index = 0

        def isOpened(self):
            return True

        def get(self, _prop):
            return float(total_frames)

        def grab(self):
            if self._index >= total_frames:
                return False
            counts["grab"] += 1
            self._index += 1
            return True

        def retrieve(self):
            counts["retrieve"] += 1
            return True, np.zeros((32, 32, 3), dtype=np.uint8)

        def release(self):
            return None

    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    monkeypatch.setattr(
        "kovaak_tracker.generic_static_clicking_analysis.select_color_hypothesis",
        lambda _samples: None,
    )

    with pytest.raises(GenericVisualPreprocessingUnavailable, match="generic_color_hypothesis_unavailable"):
        run_generic_static_clicking_detection_v1(
            media_path="unused.mp4",
            analysis_ref="analysis:1",
            canonical_time_window=_WINDOW,
            video_time_mapping={
                "schema_version": "visual_video_time_mapping.v2",
                "offset_ms": 0,
                "scale": 1.0,
            },
        )

    assert counts["grab"] == total_frames
    assert counts["retrieve"] == HYPOTHESIS_SAMPLE_FRAMES
    assert counts["retrieve"] < counts["grab"]
