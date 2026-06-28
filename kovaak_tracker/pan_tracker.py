"""Adaptive target detection + global-pan trajectory for flicking analysis.

For KovaaK click-timing scenarios (e.g. 1w6ts) the targets are static in world
space and the player flicks = pans the view, so the flick velocity IS the view
translation. Tracking a single target with CSRT fails because the target is lost
during fast pans (then forward-filling the gap creates huge fake velocity
spikes). Instead we detect ALL targets each frame with colour-agnostic adaptive
background subtraction, match them frame-to-frame, and take the median matched
displacement as the per-frame pan. Integrating the pan yields a synthetic
trajectory whose speed equals the flick speed -- robust to arbitrary
background/target colours and to individual targets being lost or despawned
mid-flick.

``analyze_flicking_video`` ties the whole pipeline together: parse CSV ->
auto-lock the challenge window -> compute the pan trajectory -> align kills and
score flick metrics. No manual start-frame or colour calibration required.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from .analysis import apply_smoothing, calc_derivative
from .csv_parser import parse_stats_csv
from .flicking import (
    _ball_speed,
    compute_fair_metrics,
    run_flicking_analysis,
    segment_by_valleys,
)
from .settings import OUTPUT_DIR, ensure_output_dir
from .start_frame import lock_challenge_window
from .video import get_video_metadata


def detect_targets(
    frame,
    *,
    dist_percentile: float = 97.5,
    dist_floor: float = 40.0,
    min_area_frac: float = 1e-5,
    max_area_frac: float = 0.02,
    aspect=(0.5, 1.8),
    ui_band=(0.12, 0.90),
    crosshair_radius: int = 30,
):
    """Detect all target centroids in a frame, colour-agnostic.

    Background = the frame median (dominant) colour; targets = compact blobs
    whose colour distance from the background exceeds an adaptive (high
    percentile, floored) threshold. The top/bottom HUD bands and the central
    crosshair are masked out. Returns an ``(N, 2)`` array of ``(x, y)``
    centroids (empty array if none).
    """
    h, w = frame.shape[:2]
    bg = np.median(cv2.resize(frame, (80, 45)).reshape(-1, 3), axis=0)
    dist = np.linalg.norm(frame.astype(np.float32) - bg, axis=2)
    thr = max(dist_floor, float(np.percentile(dist, dist_percentile)))
    mask = (dist > thr).astype(np.uint8) * 255
    mask[: int(h * ui_band[0]), :] = 0
    mask[int(h * ui_band[1]) :, :] = 0
    cv2.circle(mask, (w // 2, h // 2), crosshair_radius, 0, -1)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * w * h
    max_area = max_area_frac * w * h
    pts = []
    for c in contours:
        area = cv2.contourArea(c)
        if not (min_area < area < max_area):
            continue
        x0, y0, bw, bh = cv2.boundingRect(c)
        if bh == 0 or not (aspect[0] < bw / bh < aspect[1]):
            continue
        m = cv2.moments(c)
        if m["m00"]:
            pts.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))
    return np.array(pts) if pts else np.empty((0, 2))


def compute_pan_trajectory(
    video_path,
    start_frame,
    end_frame,
    *,
    fps=None,
    match_gate_px: float = 200.0,
    progress_callback=None,
):
    """Per-frame view-pan trajectory over the inclusive ``[start_frame, end_frame]``.

    Each frame: detect all targets, nearest-neighbour-match them to the previous
    frame within ``match_gate_px``, and take the median matched displacement as
    the pan (robust to one or two targets despawning/respawning, since the rest
    still translate together). Returns a DataFrame with ``frame, time_s, pan_dx,
    pan_dy, n_targets, ball_x, ball_y`` where ``ball_x/y`` is the pan integrated
    from the screen centre -- a synthetic trajectory whose per-frame speed equals
    the pan magnitude, ready for ``flicking._ball_speed``.
    """
    meta = get_video_metadata(video_path)
    fps = fps if fps is not None else meta.fps
    cx0, cy0 = meta.width / 2.0, meta.height / 2.0
    cap = cv2.VideoCapture(video_path)
    rows = []
    prev = None
    total = max(1, end_frame - start_frame + 1)
    for off, f in enumerate(range(start_frame, end_frame + 1)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            break
        cur = detect_targets(frame)
        if prev is not None and len(prev) and len(cur):
            disp = []
            for cx, cy in cur:
                d = np.hypot(prev[:, 0] - cx, prev[:, 1] - cy)
                j = int(d.argmin())
                if d[j] < match_gate_px:
                    disp.append((cx - prev[j, 0], cy - prev[j, 1]))
            disp = np.array(disp) if disp else np.empty((0, 2))
            if len(disp) >= 2:
                pan = np.median(disp, axis=0)
            elif len(disp) == 1:
                pan = disp[0]
            else:
                pan = np.array([0.0, 0.0])
        else:
            pan = np.array([0.0, 0.0])
        rows.append((f, off / fps, float(pan[0]), float(pan[1]), len(cur)))
        prev = cur
        if progress_callback is not None:
            progress_callback((off + 1) / total, None)
    cap.release()

    df = pd.DataFrame(rows, columns=["frame", "time_s", "pan_dx", "pan_dy", "n_targets"])
    xx = [cx0]
    yy = [cy0]
    for dx, dy in zip(df["pan_dx"], df["pan_dy"]):
        xx.append(xx[-1] + dx)
        yy.append(yy[-1] + dy)
    df["ball_x"] = xx[1:]
    df["ball_y"] = yy[1:]
    return df


def analyze_flicking_video(
    video_path,
    csv_path,
    *,
    duration_s=None,
    crosshair=None,
    output_dir=OUTPUT_DIR,
    progress_callback=None,
):
    """End-to-end: KovaaK recording + stats CSV -> flick analysis, fully automated.

    Pipeline: parse CSV -> auto-lock the challenge window (detects countdowns and
    the results screen, matches the scenario duration) -> compute the global-pan
    trajectory over the challenge (Approach A) -> align CSV kills and score flick
    deceleration metrics. No manual start-frame or colour calibration needed.

    ``crosshair`` is passed through to the flick metrics for the overshoot
    calculation; pass ``None`` (the default) to skip it -- the pan trajectory is
    a synthetic integrated point, so overshoot is not meaningful for it. Returns
    ``(FlickAnalysis, ChallengeWindow)``.
    """
    stats = parse_stats_csv(csv_path)
    if duration_s is None:
        duration_s = float(math.ceil(stats.kills["time_s"].max()))
    meta = get_video_metadata(video_path)

    window = lock_challenge_window(video_path, duration_s, fps=meta.fps)
    track_df = compute_pan_trajectory(
        video_path,
        window.start_frame,
        window.end_frame,
        fps=meta.fps,
        progress_callback=progress_callback,
    )
    output_dir = ensure_output_dir(Path(output_dir))
    track_csv = output_dir / "pan_trajectory.csv"
    track_df.to_csv(track_csv, index=False)

    analysis = run_flicking_analysis(
        csv_path,
        track_csv,
        fps=meta.fps,
        start_frame=window.start_frame,
        crosshair=crosshair,
        output_dir=output_dir,
    )
    return analysis, window


@dataclass
class ReferenceAnalysis:
    """Result of no-CSV reference analysis (valley-segmented, speed-fair).

    ``metrics`` is a list of :class:`~flicking.FlickFairMetrics`; ``summary``
    holds median/p75/p90 per metric. ``window`` is the ``ChallengeWindow`` used
    (``None`` when start/end were given manually).
    """

    flicks: list
    metrics: list
    summary: dict
    window: object
    start_frame: int
    end_frame: int


def _summarize_reference(metrics: list, cm_per_deg) -> dict:
    names = (
        "peak_speed_deg", "linearity", "reverse_ratio", "decel_frac",
        "endpoint_peak", "peak_position_pct", "path_efficiency", "path_length_deg",
    )
    out: dict = {}
    for name in names:
        vals = [
            getattr(m, name) for m in metrics
            if not (isinstance(getattr(m, name), float) and np.isnan(getattr(m, name)))
        ]
        if not vals:
            out[name] = None
            continue
        a = np.array(vals, dtype=float)
        out[name] = {
            "med": round(float(np.median(a)), 3),
            "p75": round(float(np.percentile(a, 75)), 3),
            "p90": round(float(np.percentile(a, 90)), 3),
        }
    if cm_per_deg:
        peaks = [
            m.peak_speed_deg for m in metrics
            if not (isinstance(m.peak_speed_deg, float) and np.isnan(m.peak_speed_deg))
        ]
        if peaks:
            out["peak_cm_per_s"] = round(float(np.median(peaks)) * cm_per_deg, 2)
    out["flick_count"] = len(metrics)
    return out


def analyze_flicking_reference(
    video_path,
    *,
    duration_s: float = 60.0,
    start_frame=None,
    end_frame=None,
    fov: float = 103.0,
    cm_per_360=None,
    ui_area_frac: float = 0.01,
    output_dir=OUTPUT_DIR,
    progress_callback=None,
):
    """No-CSV reference analysis: video only -> valley-segmented speed-fair metrics.

    For downloaded reference gameplay where no KovaaK stats CSV exists. Uses
    valley segmentation + fair metrics so results are comparable across players
    and resolutions (see docs/aim-kinematics-research.md). Auto-locks the
    challenge window unless ``start_frame``/``end_frame`` are given — pass them
    manually when the auto-lock is unreliable (e.g. a clipped video with a
    persistent HUD that trips the UI detector, which the higher default
    ``ui_area_frac`` here mitigates).

    Returns a :class:`ReferenceAnalysis`. Writes ``ref_pan_trajectory.csv``.
    """
    meta = get_video_metadata(video_path)
    fps = meta.fps
    deg_per_px = fov / meta.width

    window = None
    if start_frame is None or end_frame is None:
        window = lock_challenge_window(
            video_path, duration_s, fps=fps, ui_area_frac=ui_area_frac
        )
        start_frame = window.start_frame
        end_frame = window.end_frame

    track_df = compute_pan_trajectory(
        video_path, start_frame, end_frame, fps=fps, progress_callback=progress_callback
    )
    output_dir = ensure_output_dir(Path(output_dir))
    track_df.to_csv(output_dir / "ref_pan_trajectory.csv", index=False)

    speed = _ball_speed(track_df, fps)
    accel = calc_derivative(speed, fps)
    win = max(5, int(fps * 0.05))
    if win % 2 == 0:
        win += 1
    accel = apply_smoothing(accel, win)

    flicks = segment_by_valleys(speed, fps)
    metrics = [
        compute_fair_metrics(f, speed, accel, track_df, deg_per_px=deg_per_px)
        for f in flicks
    ]
    cm_per_deg = (cm_per_360 / 360.0) if cm_per_360 else None
    summary = _summarize_reference(metrics, cm_per_deg)
    return ReferenceAnalysis(
        flicks=flicks, metrics=metrics, summary=summary, window=window,
        start_frame=start_frame, end_frame=end_frame,
    )


__all__ = [
    "detect_targets",
    "compute_pan_trajectory",
    "analyze_flicking_video",
    "analyze_flicking_reference",
    "ReferenceAnalysis",
]
