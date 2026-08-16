"""Progressive-disclosure output tests: decode preroll reaches Coach anchors."""
from __future__ import annotations

import pytest

from webapp.backend.analysis_output import _build_overview


def _result_with_timeline(timeline: list[dict], issues: list[dict], **extra) -> dict:
    return {
        "analysis_id": "analysis:9",
        "input_mode": "multimodal",
        "completed_at": "2026-08-16T00:00:00Z",
        "deterministic": {
            "metrics": {},
            "timeline": timeline,
            "diagnosis": {"issues": issues},
        },
        "evidence": {"sources": {}, "coverage": 1.0},
        "input_snapshot": {"scenario": "fixture"},
        "scenario": {},
        **extra,
    }


def test_overview_anchor_times_subtract_the_decode_preroll():
    issue = {
        "signal": "overshoot",
        "event_refs": ["analysis:9:event:flick:1"],
        "metric_refs": ["path_length"],
    }
    timeline = [
        {
            "id": "flick:1",
            "peak_ms": 500.0,
            "relative_ms": 480.0,
            "metrics": {"path_length": 120.0},
        },
    ]

    stamped = _build_overview(
        9, _result_with_timeline(
            timeline, [issue], video_decode_preroll_ms=121.97,
        ),
    )
    unstamped = _build_overview(9, _result_with_timeline(timeline, [issue]))

    assert stamped["video_decode_preroll_ms"] == pytest.approx(121.97)
    assert "video_decode_preroll_ms" not in unstamped
    stamped_anchor = stamped["diagnosis"]["issues"][0]["time_anchors"][0]
    unstamped_anchor = unstamped["diagnosis"]["issues"][0]["time_anchors"][0]
    # peak_ms wins over relative_ms; the preroll shifts the video anchor left.
    assert unstamped_anchor["ms"] == pytest.approx(500.0)
    assert stamped_anchor["ms"] == pytest.approx(378.03)
    assert stamped_anchor["path_length"] == pytest.approx(120.0)


def test_overview_anchor_clamps_at_zero_when_preroll_exceeds_the_event_time():
    issue = {
        "signal": "overshoot",
        "event_refs": ["analysis:9:event:flick:1"],
        "metric_refs": [],
    }
    timeline = [{"id": "flick:1", "relative_ms": 80.0}]
    overview = _build_overview(
        9,
        _result_with_timeline(
            timeline, [issue], video_decode_preroll_ms=121.97,
        ),
    )
    anchor = overview["diagnosis"]["issues"][0]["time_anchors"][0]
    assert anchor["ms"] == 0.0
