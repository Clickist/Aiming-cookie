"""Flicking analysis: segment a CV trajectory into individual flicks and score
each on deceleration quality (tension release).

In KovaaK's the crosshair is locked to screen center, so moving the mouse
translates the whole view. The tracked target's screen-space velocity **is** the
flick motion. A flick is therefore a velocity burst bounded by stillness or by a
CSV kill timestamp (whichever comes first).

Pipeline: per-frame target speed -> valley-based segmentation
(:func:`segment_by_valleys`) -> fair metrics (:func:`compute_fair_metrics`,
cross-distance/speed comparable).

Reused helpers: :func:`kovaak_tracker.analysis.apply_smoothing` and
:func:`calc_derivative` from the tracking module. The flick-specific work is
segmentation (tracking has none) and per-segment metrics (tracking scores the
whole trajectory at once).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .analysis import apply_smoothing, calc_derivative



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




# === Valley segmentation + speed-fair metrics (2026-06-28) ===
# Validated on real 1w6ts data: the earlier threshold-based segmentation
# over-merges fast consecutive flicks, and decel_smoothness scales with peak
# speed (corr~0.76) so it is unfair across players. These replace both for
# reference/no-CSV analysis and cross-player comparison. Rationale + sources:
# docs/aim-kinematics-research.md


@dataclass(frozen=True)
class FlickFairMetrics:
    """Speed-fair, cross-player-comparable flick metrics.

    Decel quality uses normalized/shape metrics that do not scale with peak
    speed. Theoretical anchors in docs/aim-kinematics-research.md §6:

    - ``linearity`` — decel-phase speed vs its *constant-deceleration* (linear)
      fit, /peak. NOT min-jerk (min-jerk decel is a curve). Measures braking
      evenness, not jitter (§6.1).
    - ``sparc`` — decel-phase SPARC (spectral arc length, Balasubramanian 2012);
      the speed-fair gold standard for smoothness and the correct proxy for
      "decel jitter / tense release" (§6.1).
    - ``corrective_count`` / ``submovement_overlap`` — submovement structure
      (Woodworth/Meyer/Novak, §6.2): overlap high = overlapping/fluid, low =
      discrete/two-stage.
    - ``throughput`` — Fitts TP = log2(D/W+1)/MT in bits/s, distance-normalized
      speed (§6.3); NaN when target width is unavailable.

    Path geometry comes from the integrated pan trajectory. Angular quantities
    use ``deg_per_px = FOV / width`` so they are comparable across resolution
    and sensitivity.
    """

    peak_speed_deg: float
    peak_position_pct: float
    linearity: float             # lower = more even (constant-deceleration) brake
    sparc: float                 # higher (≈0) = smoother decel; speed-fair (§6.1)
    reverse_ratio: float         # lower = monotonic decel
    decel_frac: float            # decel-phase length / flick length
    endpoint_peak: float         # valley speed / peak speed
    corrective_count: int        # corrective submovements after initial (§6.2)
    submovement_overlap: float   # 实为 trough depth ratio（谷深/主峰），非 Novak time-overlap 字面义；high=流体融合, low=两阶段 (§6.2, 见 _submovement_structure 命名注)
    path_efficiency: float       # straight / actual path (1 = straight)
    path_length_deg: float
    direction_deg: float         # overall pan direction
    throughput: float            # Fitts bits/s, distance-normalized (§6.3)


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
    player fully stopping between flicks (the failure mode of threshold-based
    segmentation on fast players). Returns ``(start, peak, end,
    peak_v, duration_s)`` tuples indexing into ``speed``.
    """
    if speed.size == 0:
        return []
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
        if peak_v <= 0 or peak_v < prom * 1.5:
            continue
        if (e - s + 1) / fps < min_dur_s:
            continue
        flicks.append((s, p, e, peak_v, (e - s + 1) / fps))
    return flicks


def _segment_sparc(speed: np.ndarray, fps: float, amp_th: float = 0.05) -> float:
    """SPARC (spectral arc length) smoothness of a flick's speed profile.

    Balasubramanian et al. 2012 (IEEE TBME). Computed on the whole flick
    segment (the bell-shaped speed curve), matching the metric's design — a
    decel-only half-bell has a ragged spectrum that inflates the arc length.
    Returns the negative arc length of the DC-normalized speed-magnitude
    spectrum; closer to 0 = smoother. Frequency-domain so dimensionless and
    speed-fair — the gold-standard fix for the decel_smoothness-vs-peak-speed
    coupling (§6.1). NaN for segments too short to resolve a spectrum.

    Note: flicks shorter than roughly 16 frames typically return NaN — the
    ``n < 8`` guard plus the ``fc < 2`` adaptive-cutoff guard together drop
    short segments because their spectrum can't resolve a meaningful arc
    length. At 60fps this means ~0.15-0.25s flicks (9-15 frames) do not
    contribute SPARC samples. This is a known sampling limitation of the
    frequency-domain method, not a bug; a time-domain fallback is research
    scope (not this change).
    """
    n = len(speed)
    if n < 8:
        return float("nan")
    spectrum = np.abs(np.fft.rfft(speed))
    dc = spectrum[0]
    if dc <= 0:
        return float("nan")
    spectrum = spectrum / dc
    freqs = np.fft.rfftfreq(n, d=1.0 / fps)
    # Adaptive cutoff: largest frequency index beyond which amplitude stays < amp_th.
    above = np.where(spectrum > amp_th)[0]
    if above.size == 0 or above.max() < 2:
        return float("nan")
    fc = int(above.max())
    f_v = freqs[1:fc + 1]
    V_v = spectrum[1:fc + 1]
    return float(-np.sum(np.sqrt(np.diff(f_v) ** 2 + np.diff(V_v) ** 2)))


def _submovement_structure(
    speed: np.ndarray, fps: float, peak_idx: int, peak_v: float,
    *, window_s: float = 0.4, corr_frac: float = 0.7,
) -> tuple[int, float]:
    """Corrective submovements in the deceleration tail of a flick (§6.2).

    Scans a fixed window after the primary peak — independent of valley
    segmentation, which can split a two-stage flick into separate segments and
    hide the corrective inside another segment. A corrective submovement is a
    secondary speed peak shorter than ``corr_frac`` x primary rising within
    ``window_s`` after the peak: the "急停 micro" of a Bardpill two-stage flick
    (Schwartze 2024: corrective submovements have smaller magnitude than initial).

    ``overlap`` = lowest trough between primary peak and first corrective /
    peak_v: high = fused/overlapping (fluid), low = discrete (two-stage).
    **命名注**：实为 *trough depth ratio*（谷深 / 主峰速度），非 Novak 2002
    time-overlap 的字面实现——命名沿用便于下游消费，但语义是"减速段谷有多深"
    （高 = 两阶段界限清晰、低 = 流体融合），与 tracking PTC 同型的"实现合理
    但名字误导"。Returns ``(corrective_count, overlap)``; overlap is NaN when
    no corrective is found (a single clean bell = fully fluid).
    """
    if peak_v <= 0 or not (0 <= peak_idx < len(speed)):
        return 0, float("nan")
    hi = min(len(speed), peak_idx + int(window_s * fps))
    tail = speed[peak_idx:hi]
    if len(tail) < 5:
        return 0, float("nan")
    peaks, _ = find_peaks(
        tail, prominence=peak_v * 0.2, distance=max(1, int(0.08 * fps))
    )
    # keep only peaks shorter than the primary (correctives are smaller)
    peaks = [pk for pk in peaks if tail[pk] < peak_v * corr_frac]
    if not peaks:
        return 0, float("nan")
    first = peaks[0]
    trough = float(tail[1:first].min()) if first > 1 else float(tail[0])
    return len(peaks), float(trough / peak_v)


def compute_fair_metrics(
    flick: tuple,
    speed: np.ndarray,
    accel: np.ndarray,
    df: pd.DataFrame,
    *,
    deg_per_px: float,
    fps: float,
    target_width_deg: float | None = None,
) -> FlickFairMetrics:
    """Speed-fair metrics for one valley-segmented flick.

    ``accel`` should be lightly smoothed (so ``reverse_ratio`` is not noise).
    ``deg_per_px`` converts px-native pan quantities to visual degrees
    (``deg = px * FOV / width``). ``fps`` drives the SPARC and submovement
    analyses. ``target_width_deg`` enables the Fitts ``throughput`` metric
    (§6.3); omit it (e.g. no-CSV reference mode without target detection) to
    leave throughput NaN.
    """
    s, p, e, peak_v, duration_s = flick
    seg_speed = speed[s:e + 1]
    decel = speed[p:e + 1]
    if len(decel) >= 3:
        t = np.arange(len(decel))
        fit = np.polyfit(t, decel, 1)
        resid = decel - np.polyval(fit, t)
        linearity = float(np.sqrt(np.mean(resid ** 2)) / peak_v)
    else:
        linearity = float("nan")
    sparc = _segment_sparc(seg_speed, fps)
    corrective, overlap = _submovement_structure(speed, fps, p, peak_v)
    da = accel[p:e + 1]
    reverse = float(np.mean(da > 0)) if len(da) else float("nan")
    decfrac = (e - p) / max(1.0, (e - s))
    endpk = float(speed[e] / peak_v)
    peak_pos = round(100.0 * (p - s) / max(1, (e - s)), 1)
    path_eff = path_len_deg = direction = float("nan")
    throughput = float("nan")
    straight_px = 0.0
    if "ball_x" in df.columns and "ball_y" in df.columns and e > s:
        xs = df["ball_x"].astype(float).to_numpy()[s:e + 1]
        ys = df["ball_y"].astype(float).to_numpy()[s:e + 1]
        seg_len = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
        straight_px = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
        if seg_len > 0:
            path_eff = straight_px / seg_len
            path_len_deg = seg_len * deg_per_px
        if straight_px > 0:
            direction = float(np.degrees(np.arctan2(ys[-1] - ys[0], xs[-1] - xs[0])))
    # Fitts throughput: TP = log2(D/W + 1) / MT, with D the start->end amplitude
    # (deg), W the target width (deg), MT the flick duration. Distance-normalized
    # speed proxy (§6.3); needs target width, else NaN.
    if (target_width_deg and target_width_deg > 0 and duration_s > 0
            and straight_px > 0):
        D_deg = straight_px * deg_per_px
        throughput = float(np.log2(D_deg / target_width_deg + 1)) / duration_s

    def rnd(v, n):
        return round(v, n) if not np.isnan(v) else float("nan")

    return FlickFairMetrics(
        peak_speed_deg=round(peak_v * deg_per_px, 2),
        peak_position_pct=peak_pos,
        linearity=rnd(linearity, 4),
        sparc=rnd(sparc, 3),
        reverse_ratio=rnd(reverse, 3),
        decel_frac=round(decfrac, 3),
        endpoint_peak=round(endpk, 3),
        corrective_count=int(corrective),
        submovement_overlap=rnd(overlap, 3),
        path_efficiency=rnd(path_eff, 3),
        path_length_deg=rnd(path_len_deg, 2),
        direction_deg=rnd(direction, 1),
        throughput=rnd(throughput, 3),
    )


__all__ = [
    "FlickFairMetrics",
    "segment_by_valleys",
    "compute_fair_metrics",
]
