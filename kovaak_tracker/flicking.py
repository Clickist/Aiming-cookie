"""Flicking analysis: segment a CV trajectory into individual flicks and score
each on deceleration quality (tension release).

In KovaaK's the crosshair is locked to screen center, so moving the mouse
translates the whole view. The tracked target's screen-space velocity **is** the
flick motion. A flick is therefore a velocity burst bounded by stillness or by a
CSV kill timestamp (whichever comes first).

Pipeline::

    trajectory --extract_flicks--> segments --compute_metrics--> scored flicks

Reused helpers: :func:`kovaak_tracker.analysis.apply_smoothing` and
:func:`calc_derivative` from the tracking module. The flick-specific work is
segmentation (tracking has none) and per-segment metrics (tracking scores the
whole trajectory at once).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .aligner import Alignment, align
from .analysis import apply_smoothing, calc_derivative
from .csv_parser import parse_stats_csv
from .settings import OUTPUT_DIR, ensure_output_dir


@dataclass(frozen=True)
class FlickSegment:
    """One flick as a window into the CV trajectory.

    ``start_idx``/``end_idx`` index into the aligned track DataFrame.
    ``peak_idx``/``accel_end_idx`` mark velocity-shape landmarks inside.
    """

    start_idx: int
    end_idx: int  # inclusive
    peak_idx: int
    accel_end_idx: int
    peak_speed: float
    duration_s: float

    @property
    def n_frames(self) -> int:
        return self.end_idx - self.start_idx + 1


@dataclass(frozen=True)
class FlickMetrics:
    """Deceleration-quality metrics for one flick.

    All NaN if the flick is too short to compute (the segment is kept for
    counting but not scored).
    """

    peak_speed: float
    peak_position_pct: float
    accel_decel_ratio: float
    decel_smoothness: float
    endpoint_speed: float
    overshoot: float | None
    is_two_stage: bool


@dataclass
class FlickAnalysis:
    segments: list[FlickSegment]
    metrics: list[FlickMetrics]
    summary: dict = field(default_factory=dict)


def extract_flicks(
    track_df: pd.DataFrame,
    fps: float,
    *,
    speed_threshold: float | None = None,
    min_duration_s: float = 0.08,
    lookback_s: float = 0.20,
    kill_idxs: list[int] | None = None,
) -> list[FlickSegment]:
    """Segment a CV trajectory into individual flicks.

    A flick is a window of motion bounded by stillness. The threshold-based
    scan first finds raw moving-runs, then a merge pass glues adjacent runs
    whose inter-run gap is shorter than ``lookback_s`` — this keeps a two-stage
    flick (Bardpill: fast flick → brief dip → micro-correction) as one segment
    instead of splitting it into two. If ``kill_idxs`` are given, a flick is
    also cut at the first kill index it contains (the committing click).
    """
    df = track_df.reset_index(drop=True)
    speed = _ball_speed(df, fps)
    if speed.size == 0:
        return []

    peak = float(np.nanmax(speed))
    if speed_threshold is None:
        speed_threshold = max(peak * 0.05, 1.0)
    moving = speed >= speed_threshold

    # 1) Raw runs of contiguous motion.
    runs: list[tuple[int, int]] = []
    i, n = 0, len(df)
    while i < n:
        if not moving[i]:
            i += 1
            continue
        start = i
        while i + 1 < n and moving[i + 1]:
            i += 1
        runs.append((start, i))
        i += 1

    # 2) Merge adjacent runs separated by a gap shorter than lookback_s.
    lookback_frames = max(1, int(lookback_s * fps))
    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= lookback_frames:
            merged[-1][1] = run[1]
        else:
            merged.append([run[0], run[1]])

    # 3) Build segments, optionally cut at the first kill index inside.
    kill_set = set(kill_idxs) if kill_idxs else set()
    segments: list[FlickSegment] = []
    for start, end in merged:
        if any(k in kill_set for k in range(start, end + 1)):
            kill_inside = next(k for k in range(start, end + 1) if k in kill_set)
            if kill_inside < end:
                end = kill_inside

        peak_idx = start + int(np.argmax(speed[start : end + 1]))
        # Inflection: first frame after peak where speed drops below the
        # peak-to-end midpoint — marks the start of the deceleration phase.
        post = speed[peak_idx : end + 1]
        if post.size:
            midpoint = (speed[peak_idx] + speed[end]) / 2.0
            below = np.where(post < midpoint)[0]
            accel_end_idx = peak_idx + int(below[0]) if below.size else peak_idx
        else:
            accel_end_idx = peak_idx

        duration = (end - start + 1) / fps
        if duration >= min_duration_s:
            segments.append(
                FlickSegment(
                    start_idx=start,
                    end_idx=end,
                    peak_idx=peak_idx,
                    accel_end_idx=accel_end_idx,
                    peak_speed=float(speed[peak_idx]),
                    duration_s=duration,
                )
            )

    return segments


def compute_metrics(
    segment: FlickSegment,
    track_df: pd.DataFrame,
    fps: float,
    *,
    crosshair: tuple[float, float] | None = None,
    two_stage_dip_ratio: float = 0.4,
) -> FlickMetrics:
    """Score one flick on the deceleration-quality dimensions in the plan.

    Returns NaN-filled metrics for flicks shorter than ~4 frames (not enough
    resolution to characterize a velocity curve).
    """
    df = track_df.reset_index(drop=True)
    speed = _ball_speed(df, fps)
    accel = calc_derivative(speed, fps)

    s, e = segment.start_idx, segment.end_idx
    p = segment.peak_idx
    seg_speed = speed[s : e + 1]
    if seg_speed.size < 4:
        nan = float("nan")
        return FlickMetrics(nan, nan, nan, nan, nan, None, False)

    peak_speed = float(seg_speed.max())
    duration = segment.duration_s
    peak_position_pct = round(100.0 * (p - s) / max(1, (e - s)), 1)

    # Acceleration vs deceleration phase *area* (speed·time ≈ distance covered).
    accel_area = float(np.trapz(speed[s : p + 1], dx=1.0 / fps))
    decel_area = float(np.trapz(speed[p : e + 1], dx=1.0 / fps))
    accel_decel_ratio = round(accel_area / decel_area, 3) if decel_area > 0 else float("nan")

    # Deceleration smoothness: std of acceleration in the decel phase. High std
    # = jerky release (tension not let go smoothly). See plan "减速段平滑度".
    decel_accel = accel[p : e + 1]
    decel_smoothness = round(float(np.std(decel_accel)), 3) if decel_accel.size else float("nan")

    # Endpoint speed at click: ideal near zero but not zero (plan "端点精度").
    endpoint_speed = float(seg_speed[-1])

    # Overshoot (directional): signed distance from target-center to crosshair.
    # Positive = target past center (overshot), negative = short of center.
    overshoot: float | None = None
    if crosshair is not None and "ball_x" in df.columns and "ball_y" in df.columns:
        bx = float(df["ball_x"].iloc[e])
        by = float(df["ball_y"].iloc[e])
        cx, cy = crosshair
        # Screen Y grows downward; keep raw, caller interprets sign per axis.
        overshoot = round(float(np.hypot(bx - cx, by - cy)), 2)

    # Two-stage (Bardpill) vs fluid (Zeonlo): two distinct speed peaks with a
    # local minimum between them. A single bell decays monotonically off its
    #

    is_two_stage = False
    if seg_speed.size >= 5:
        prom = peak_speed * two_stage_dip_ratio
        peaks, _ = find_peaks(seg_speed, prominence=prom)
        is_two_stage = len(peaks) >= 2
    return FlickMetrics(
        peak_speed=round(peak_speed, 2),
        peak_position_pct=peak_position_pct,
        accel_decel_ratio=accel_decel_ratio,
        decel_smoothness=decel_smoothness,
        endpoint_speed=round(endpoint_speed, 2),
        overshoot=overshoot,
        is_two_stage=is_two_stage,
    )


def analyze_flicks(
    alignment: Alignment,
    *,
    crosshair: tuple[float, float] | None = None,
    speed_threshold: float | None = None,
) -> FlickAnalysis:
    """End-to-end: segment the aligned trajectory and score each flick.

    ``crosshair`` is the screen-center the KovaaK's crosshair is locked to;
    pass ``None`` to skip the overshoot metric.
    """
    df = alignment.track_df
    # Map kill events onto track row indices by nearest frame.
    kill_frames = {k.kill_frame for k in alignment.kills}
    frame_to_idx = {int(f): i for i, f in enumerate(df["frame"].to_numpy())}
    kill_idxs = [frame_to_idx[f] for f in kill_frames if f in frame_to_idx]

    segments = extract_flicks(
        df,
        alignment.fps,
        speed_threshold=speed_threshold,
        kill_idxs=kill_idxs,
    )
    cross = crosshair
    metrics = [compute_metrics(seg, df, alignment.fps, crosshair=cross) for seg in segments]
    summary = _summarize(segments, metrics)
    return FlickAnalysis(segments=segments, metrics=metrics, summary=summary)


def _ball_speed(df: pd.DataFrame, fps: float) -> np.ndarray:
    """Per-frame target speed (px/s) in screen space, with smoothing.

    Uses ``ball_x/ball_y``. Frames where the target wasn't detected
    (``ball_w == 0``) are forward-filled so a single drop doesn't create a
    spurious velocity spike.
    """
    if "ball_x" not in df.columns or "ball_y" not in df.columns:
        return np.array([])
    x = df["ball_x"].astype(float).to_numpy()
    y = df["ball_y"].astype(float).to_numpy()
    # Gap = position NaN (real tracking writes None on loss) or ball_w==0 (synthetic).
    bad = np.isnan(x) | np.isnan(y)
    if "ball_w" in df.columns:
        bad = bad | (df["ball_w"].to_numpy() == 0)
    if bad.any() and not bad.all():
        # forward-fill from last detected frame so a single drop doesn't
        # create a spurious velocity spike.
        x = x.copy()
        y = y.copy()
        last_good = None
        for i in range(len(x)):
            if bad[i]:
                if last_good is not None:
                    x[i] = x[last_good]
                    y[i] = y[last_good]
            else:
                last_good = i
    # Speed via centered differences on positions, then smooth.
    vx = calc_derivative(x, fps)
    vy = calc_derivative(y, fps)
    raw = np.hypot(vx, vy)
    win = max(5, int(fps * 0.05))
    if win % 2 == 0:
        win += 1
    return apply_smoothing(raw, win)


def _summarize(segments: list[FlickSegment], metrics: list[FlickMetrics]) -> dict:
    if not segments:
        return {"flick_count": 0}
    scored = [m for m in metrics if not np.isnan(m.peak_speed)]
    two_stage = sum(1 for m in scored if m.is_two_stage)
    decel = [m.decel_smoothness for m in scored if not np.isnan(m.decel_smoothness)]
    peak_pct = [m.peak_position_pct for m in scored if not np.isnan(m.peak_position_pct)]
    return {
        "flick_count": len(segments),
        "scored_count": len(scored),
        "two_stage_count": two_stage,
        "two_stage_pct": round(100.0 * two_stage / len(scored), 1) if scored else 0.0,
        "median_decel_smoothness": round(float(np.median(decel)), 3) if decel else None,
        "median_peak_position_pct": round(float(np.median(peak_pct)), 1) if peak_pct else None,
        "avg_peak_speed": round(float(np.mean([m.peak_speed for m in scored])), 2) if scored else None,
    }


def export_flicking(analysis: FlickAnalysis, output_dir: Path) -> dict:
    """Persist flicking artifacts parallel to ``export_analysis``.

    Writes ``flicking_metrics.json`` (summary + per-flick records) and
    ``flicking_segments.csv`` (one row per scored flick). Returns the JSON
    payload so callers can forward it to a dashboard.
    """
    output_dir = ensure_output_dir(Path(output_dir))
    rows = []
    for seg, m in zip(analysis.segments, analysis.metrics):
        rows.append(
            {
                "start_frame": seg.start_idx,
                "end_frame": seg.end_idx,
                "peak_frame": seg.peak_idx,
                "duration_s": round(seg.duration_s, 4),
                "peak_speed": m.peak_speed,
                "peak_position_pct": m.peak_position_pct,
                "accel_decel_ratio": m.accel_decel_ratio,
                "decel_smoothness": m.decel_smoothness,
                "endpoint_speed": m.endpoint_speed,
                "overshoot": m.overshoot if m.overshoot is not None else "",
                "is_two_stage": bool(m.is_two_stage),
            }
        )
    segments_df = pd.DataFrame(rows)
    segments_df.to_csv(output_dir / "flicking_segments.csv", index=False)

    payload = {"summary": analysis.summary, "flicks": rows}
    with open(output_dir / "flicking_metrics.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload


def run_flicking_analysis(
    csv_path: str | Path,
    track_csv: str | Path,
    fps: float,
    start_frame: int,
    crosshair: tuple[float, float] | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> FlickAnalysis:
    """End-to-end CLI pipeline: CSV + CV track → aligned flick analysis → artifacts.

    ``csv_path`` is the KovaaK's stats CSV; ``track_csv`` is the
    ``calibration_raw.csv`` produced by :mod:`kovaak_tracker.tracking`;
    ``start_frame`` is the user-pinned video frame at scenario start.
    """
    stats = parse_stats_csv(csv_path)
    track_df = pd.read_csv(track_csv)
    alignment = align(stats, track_df, fps=fps, start_frame=start_frame)
    analysis = analyze_flicks(alignment, crosshair=crosshair)
    export_flicking(analysis, output_dir)
    return analysis


# === Valley segmentation + speed-fair metrics (2026-06-28) ===
# Validated on real 1w6ts data: still-gap segmentation (extract_flicks above)
# over-merges fast consecutive flicks, and decel_smoothness scales with peak
# speed (corr~0.76) so it is unfair across players. These replace both for
# reference/no-CSV analysis and cross-player comparison. Rationale + sources:
# docs/aim-kinematics-research.md


@dataclass(frozen=True)
class FlickFairMetrics:
    """Speed-fair, cross-player-comparable flick metrics.

    Decel quality uses normalized/shape metrics that do not scale with peak
    speed: ``linearity`` (decel-phase speed vs its linear fit, /peak),
    ``reverse_ratio`` (positive-accel fraction in decel phase), ``decel_frac``.
    Path geometry comes from the integrated pan trajectory. Angular quantities
    use ``deg_per_px = FOV / width`` so they are comparable across resolution
    and sensitivity.
    """

    peak_speed_deg: float
    peak_position_pct: float
    linearity: float        # lower = cleaner brake
    reverse_ratio: float    # lower = monotonic decel
    decel_frac: float       # decel-phase length / flick length
    endpoint_peak: float    # valley speed / peak speed
    path_efficiency: float  # straight / actual path (1 = straight)
    path_length_deg: float
    direction_deg: float    # overall pan direction


def segment_by_valleys(
    speed: np.ndarray,
    fps: float,
    *,
    prom_frac: float = 0.15,
    min_gap_s: float = 0.08,
    min_dur_s: float = 0.06,
) -> list[tuple]:
    """Segment flicks by speed valleys (robust to fast consecutive flicks).

    Each flick is bounded by adjacent speed valleys, so it does not rely on the
    player fully stopping between flicks — the failure mode of
    :func:`extract_flicks` on fast players. Returns ``(start, peak, end,
    peak_v, duration_s)`` tuples indexing into ``speed``.
    """
    peakmax = float(np.nanmax(speed))
    prom = peakmax * prom_frac
    dist = max(1, int(fps * min_gap_s))
    valleys, _ = find_peaks(-speed, prominence=prom, distance=dist)
    bounds = [0] + list(valleys) + [len(speed) - 1]
    flicks = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        seg = speed[s:e + 1]
        if len(seg) < 4:
            continue
        p = s + int(np.argmax(seg))
        peak_v = float(seg[p - s])
        if peak_v < prom * 1.5:
            continue
        if (e - s + 1) / fps < min_dur_s:
            continue
        flicks.append((s, p, e, peak_v, (e - s + 1) / fps))
    return flicks


def compute_fair_metrics(
    flick: tuple,
    speed: np.ndarray,
    accel: np.ndarray,
    df: pd.DataFrame,
    *,
    deg_per_px: float,
) -> FlickFairMetrics:
    """Speed-fair metrics for one valley-segmented flick.

    ``accel`` should be lightly smoothed (so ``reverse_ratio`` is not noise).
    ``deg_per_px`` converts px-native pan quantities to visual degrees
    (``deg = px * FOV / width``).
    """
    s, p, e, peak_v, _ = flick
    decel = speed[p:e + 1]
    if len(decel) >= 3:
        t = np.arange(len(decel))
        fit = np.polyfit(t, decel, 1)
        resid = decel - np.polyval(fit, t)
        linearity = float(np.sqrt(np.mean(resid ** 2)) / peak_v)
    else:
        linearity = float("nan")
    da = accel[p:e + 1]
    reverse = float(np.mean(da > 0)) if len(da) else float("nan")
    decfrac = (e - p) / max(1.0, (e - s))
    endpk = float(speed[e] / peak_v)
    peak_pos = round(100.0 * (p - s) / max(1, (e - s)), 1)
    path_eff = path_len_deg = direction = float("nan")
    if "ball_x" in df.columns and "ball_y" in df.columns and e > s:
        xs = df["ball_x"].astype(float).to_numpy()[s:e + 1]
        ys = df["ball_y"].astype(float).to_numpy()[s:e + 1]
        seg_len = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
        straight = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
        if seg_len > 0:
            path_eff = straight / seg_len
            path_len_deg = seg_len * deg_per_px
        if straight > 0:
            direction = float(np.degrees(np.arctan2(ys[-1] - ys[0], xs[-1] - xs[0])))

    def rnd(v, n):
        return round(v, n) if not np.isnan(v) else float("nan")

    return FlickFairMetrics(
        peak_speed_deg=round(peak_v * deg_per_px, 2),
        peak_position_pct=peak_pos,
        linearity=rnd(linearity, 4),
        reverse_ratio=rnd(reverse, 3),
        decel_frac=round(decfrac, 3),
        endpoint_peak=round(endpk, 3),
        path_efficiency=rnd(path_eff, 3),
        path_length_deg=rnd(path_len_deg, 2),
        direction_deg=rnd(direction, 1),
    )


__all__ = [
    "FlickSegment",
    "FlickMetrics",
    "FlickAnalysis",
    "extract_flicks",
    "compute_metrics",
    "analyze_flicks",
    "export_flicking",
    "run_flicking_analysis",
    "FlickFairMetrics",
    "segment_by_valleys",
    "compute_fair_metrics",
]
