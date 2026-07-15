"""PROGRESS A: analyze_flicking_fair_summary produces same-shape summary as
analyze_flicking_reference. End-to-end needs a real video; here we assert the
function exists and reuses valley segmentation via a monkeypatched trajectory."""
import inspect

import pytest
import kovaak_tracker.pan_tracker as P


def test_video_sparc_is_invariant_to_uniform_time_scaling():
    import numpy as np
    from kovaak_tracker.flicking import _segment_sparc

    profile = np.asarray([2.0, 1.0, 0.0, 1.0, 2.0, 1.0, 0.0, 1.0])

    assert _segment_sparc(profile, 1_000.0) == pytest.approx(
        _segment_sparc(profile, 100.0)
    )


def test_function_exists_and_signature():
    fn = getattr(P, "analyze_flicking_fair_summary", None)
    assert fn is not None, "analyze_flicking_fair_summary missing"
    sig = inspect.signature(fn)
    assert "video_path" in sig.parameters
    assert "csv_path" in sig.parameters


def test_summary_shape_matches_reference(monkeypatch):
    # stub compute_pan_trajectory to return a tiny synthetic trajectory with motion
    import numpy as np, pandas as pd
    fps = 60.0
    t = np.arange(180) / fps
    speed = 1000 * np.exp(-((t - 1.5) ** 2) / (2 * 0.09 ** 2))
    df = pd.DataFrame({
        "frame": np.arange(180), "time_s": t,
        "ball_x": np.cumsum(speed) / fps, "ball_y": np.zeros(180),
    })
    monkeypatch.setattr(
        P, "compute_pan_trajectory",
        lambda *a, **k: (df, np.array([30.0])) if k.get("return_widths") else df,
    )
    monkeypatch.setattr(P, "lock_challenge_window",
                        lambda *a, **k: type("W", (), {"start_frame": 0, "end_frame": 179})())
    # dummy video path can't be opened: stub metadata to match df (fps=60)
    monkeypatch.setattr(P, "get_video_metadata",
                        lambda *a, **k: type("M", (), {"fps": 60.0, "width": 1920})())
    # stats only needs duration_s derivation: make csv parser return a fake
    class _S:
        kills = pd.DataFrame({"time_s": [2.0]})
    monkeypatch.setattr(P, "parse_stats_csv", lambda *a, **k: _S())

    summary = P.analyze_flicking_fair_summary("v.mp4", "c.csv", fov=103.0)
    # same shape as reference: metric -> {med, p75, p90} OR None
    assert "flick_count" in summary
    assert isinstance(summary.get("linearity", None), (dict, type(None)))


def test_summary_uses_supplied_stats_without_reopening_csv(monkeypatch):
    import numpy as np, pandas as pd

    df = pd.DataFrame({
        "frame": np.arange(2),
        "time_s": np.array([0.0, 1 / 60]),
        "ball_x": np.array([0.0, 1.0]),
        "ball_y": np.array([0.0, 0.0]),
    })
    monkeypatch.setattr(
        P, "parse_stats_csv",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("CSV path reopened")),
    )
    monkeypatch.setattr(
        P, "compute_pan_trajectory",
        lambda *a, **k: (df, np.array([])) if k.get("return_widths") else df,
    )
    monkeypatch.setattr(
        P, "lock_challenge_window",
        lambda *a, **k: type("W", (), {"start_frame": 0, "end_frame": 1})(),
    )
    monkeypatch.setattr(
        P, "get_video_metadata",
        lambda *a, **k: type("M", (), {"fps": 60.0, "width": 1920})(),
    )

    class FrozenStats:
        kills = pd.DataFrame({"time_s": [1.0]})

    summary = P.analyze_flicking_fair_summary(
        "v.mp4",
        "/source/changed.csv",
        stats=FrozenStats(),
    )

    assert "flick_count" in summary


# ---------------------------------------------------------------------------
# Fitts throughput wiring (detect_targets width return + compute_fair_metrics)
# ---------------------------------------------------------------------------

def _synthetic_target_frame(size=400):
    """Grey frame with two bright circles — detect_targets should find them."""
    import cv2
    import numpy as np
    frame = np.full((size, size, 3), 128, dtype=np.uint8)
    # Two bright targets in the playable area, away from the center crosshair
    cv2.circle(frame, (100, size // 2), 15, (0, 0, 255), -1)
    cv2.circle(frame, (size - 100, size // 2), 15, (0, 0, 255), -1)
    return frame


def test_detect_targets_returns_widths():
    """return_width=True returns (centroids, widths) with non-empty widths."""
    import numpy as np
    frame = _synthetic_target_frame()
    centroids, widths = P.detect_targets(frame, return_width=True)
    assert centroids.shape[1] == 2
    assert len(centroids) >= 2, f"expected >=2 targets, got {len(centroids)}"
    assert len(widths) == len(centroids)
    assert np.all(widths > 0), f"widths should be positive, got {widths}"


def test_detect_targets_default_unchanged():
    """Default call still returns a bare (N,2) centroids array (no tuple)."""
    import numpy as np
    frame = _synthetic_target_frame()
    result = P.detect_targets(frame)
    assert isinstance(result, np.ndarray)
    assert result.shape[1] == 2


def test_compute_fair_metrics_throughput():
    """target_width_deg > 0 yields non-NaN throughput > 0; None leaves it NaN."""
    import math
    import numpy as np
    import pandas as pd
    from kovaak_tracker.flicking import compute_fair_metrics

    fps = 60.0
    n = 120
    t = np.arange(n) / fps
    # Bell-shaped speed burst centred at frame 30
    speed = 800 * np.exp(-((t - 0.5) ** 2) / (2 * 0.08 ** 2))
    accel = np.gradient(speed)
    # Monotonic ball_x so straight_px > 0
    df = pd.DataFrame({
        "ball_x": np.cumsum(speed) / fps,
        "ball_y": np.zeros(n),
    })
    # Flick tuple: (start, peak, end, peak_speed, duration_s)
    flick = (0, 30, 90, float(speed[30]), 90 / fps)
    deg_per_px = 103.0 / 1920

    m_with = compute_fair_metrics(
        flick, speed, accel, df, deg_per_px=deg_per_px, fps=fps,
        target_width_deg=2.0,
    )
    assert not math.isnan(m_with.throughput), "throughput should not be NaN"
    assert m_with.throughput > 0, f"throughput should be > 0, got {m_with.throughput}"

    m_without = compute_fair_metrics(
        flick, speed, accel, df, deg_per_px=deg_per_px, fps=fps,
    )
    assert math.isnan(m_without.throughput), "throughput should be NaN without target_width_deg"


def test_fair_summary_throughput_live(monkeypatch):
    """End-to-end: analyze_flicking_fair_summary produces non-NaN throughput."""
    import numpy as np
    import pandas as pd
    fps = 60.0
    t = np.arange(180) / fps
    speed = 1000 * np.exp(-((t - 1.5) ** 2) / (2 * 0.09 ** 2))
    df = pd.DataFrame({
        "frame": np.arange(180), "time_s": t,
        "ball_x": np.cumsum(speed) / fps, "ball_y": np.zeros(180),
    })
    # Return (df, widths) — non-empty widths so throughput is computed
    monkeypatch.setattr(
        P, "compute_pan_trajectory",
        lambda *a, **k: (df, np.array([30.0, 32.0, 28.0]))
        if k.get("return_widths") else df,
    )
    monkeypatch.setattr(P, "lock_challenge_window",
                        lambda *a, **k: type("W", (), {"start_frame": 0, "end_frame": 179})())
    monkeypatch.setattr(P, "get_video_metadata",
                        lambda *a, **k: type("M", (), {"fps": 60.0, "width": 1920})())
    class _S:
        kills = pd.DataFrame({"time_s": [2.0]})
    monkeypatch.setattr(P, "parse_stats_csv", lambda *a, **k: _S())

    summary = P.analyze_flicking_fair_summary("v.mp4", "c.csv", fov=103.0)
    tp = summary.get("throughput")
    assert tp is not None, "throughput key should exist and be non-None"
    med = tp["med"] if isinstance(tp, dict) else tp
    assert not (isinstance(med, float) and np.isnan(med)), \
        f"throughput median should not be NaN, got {med}"
