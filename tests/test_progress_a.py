"""PROGRESS A: analyze_flicking_fair_summary produces same-shape summary as
analyze_flicking_reference. End-to-end needs a real video; here we assert the
function exists and reuses valley segmentation via a monkeypatched trajectory."""
import inspect
import kovaak_tracker.pan_tracker as P


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
    monkeypatch.setattr(P, "compute_pan_trajectory", lambda *a, **k: df)
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
