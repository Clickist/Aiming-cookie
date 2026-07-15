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

``analyze_flicking_fair_summary`` is the CSV-mode main entry: parse CSV +
video -> global-pan trajectory -> valley segmentation -> fair metrics. No
manual start-frame or colour calibration required.
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
    ui_band=(0.15, 0.90),
    crosshair_radius: int = 30,
    max_width: int = 960,
    return_width: bool = False,
):
    """Detect all target centroids in a frame, colour-agnostic.

    Background = the frame median (dominant) colour; targets = compact blobs
    whose colour distance from the background exceeds an adaptive (high
    percentile, floored) threshold. The top/bottom HUD bands and the central
    crosshair are masked out. Returns an ``(N, 2)`` array of ``(x, y)``
    centroids (empty array if none).

    Runs on a frame downsampled to <= ``max_width`` px wide for speed: the
    full-res ``np.linalg.norm`` was the hot spot (~106ms/frame at 1920x1080);
    at <=960px it drops ~4x. min/max area are scaled with the frame, and
    centroids are mapped back to full-res by ``scale``.

    ``return_width=True`` additionally returns a ``(centroids, widths)`` tuple
    where ``widths`` is a length-N array of per-target bounding-box widths in
    full-res px (``bw * scale``). The bounding box is already computed for the
    aspect-ratio filter, so this is free.
    """
    h, w = frame.shape[:2]
    scale = max(1.0, w / max_width)
    if scale > 1.0:
        sw, sh = max(1, int(round(w / scale))), max(1, int(round(h / scale)))
        small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
    else:
        sw, sh, small = w, h, frame
    bg = np.median(cv2.resize(small, (80, 45)).reshape(-1, 3), axis=0)
    dist = np.linalg.norm(small.astype(np.float32) - bg, axis=2)
    thr = max(dist_floor, float(np.percentile(dist, dist_percentile)))
    mask = (dist > thr).astype(np.uint8) * 255
    mask[: int(sh * ui_band[0]), :] = 0
    mask[int(sh * ui_band[1]) :, :] = 0
    cv2.circle(mask, (sw // 2, sh // 2), max(1, int(crosshair_radius / scale)), 0, -1)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * sw * sh
    max_area = max_area_frac * sw * sh
    pts = []
    widths = []
    for c in contours:
        area = cv2.contourArea(c)
        if not (min_area < area < max_area):
            continue
        x0, y0, bw, bh = cv2.boundingRect(c)
        if bh == 0 or not (aspect[0] < bw / bh < aspect[1]):
            continue
        m = cv2.moments(c)
        if m["m00"]:
            pts.append((m["m10"] / m["m00"] * scale, m["m01"] / m["m00"] * scale))
            widths.append(bw * scale)
    centroids = np.array(pts) if pts else np.empty((0, 2))
    if return_width:
        return centroids, (np.array(widths) if widths else np.empty((0,)))
    return centroids


def compute_pan_trajectory(
    video_path,
    start_frame,
    end_frame,
    *,
    fps=None,
    match_gate_px: float = 200.0,
    progress_callback=None,
    return_widths: bool = False,
):
    """Per-frame view-pan trajectory over the inclusive ``[start_frame, end_frame]``.

    Each frame: detect all targets, nearest-neighbour-match them to the previous
    frame within ``match_gate_px``, and take the median matched displacement as
    the pan (robust to one or two targets despawning/respawning, since the rest
    still translate together). Returns a DataFrame with ``frame, time_s, pan_dx,
    pan_dy, n_targets, ball_x, ball_y`` where ``ball_x/y`` is the pan integrated
    from the screen centre -- a synthetic trajectory whose per-frame speed equals
    the pan magnitude, ready for ``flicking._ball_speed``.

    ``return_widths=True`` returns ``(df, widths_px)`` where ``widths_px`` is a
    flat array of every detected target's bounding-box width across all frames
    (full-res px). Used by :func:`analyze_flicking_fair_summary` to derive a
    representative target width for the Fitts throughput metric — collecting it
    here is free since detection already runs every frame.
    """
    meta = get_video_metadata(video_path)
    fps = fps if fps is not None else meta.fps
    cx0, cy0 = meta.width / 2.0, meta.height / 2.0
    cap = cv2.VideoCapture(video_path)
    rows = []
    prev = None
    all_widths = []
    total = max(1, end_frame - start_frame + 1)
    try:
        # Seek ONCE to start_frame, then read sequentially. Per-frame cap.set is
        # O(frame_no) on H.264 (re-decodes from the last keyframe each call) and was
        # dominating runtime (~10+ min on a 60s clip). Sequential read is O(1)/frame.
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        for off, f in enumerate(range(start_frame, end_frame + 1)):
            ok, frame = cap.read()
            if not ok:
                # cap.read() 提前失败时,下面的 progress_callback 永远到不了 1.0,
                # 等待 100% 切状态的 UI 会卡。补发一次满进度。
                if progress_callback is not None:
                    progress_callback(1.0, None)
                break
            if return_widths:
                cur, ws = detect_targets(frame, return_width=True)
                if len(ws):
                    all_widths.extend(ws.tolist())
            else:
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
    finally:
        # detect_targets / progress_callback 抛异常时 VideoCapture 不释放 → Windows 锁文件。
        cap.release()

    df = pd.DataFrame(rows, columns=["frame", "time_s", "pan_dx", "pan_dy", "n_targets"])
    xx = [cx0]
    yy = [cy0]
    for dx, dy in zip(df["pan_dx"], df["pan_dy"]):
        xx.append(xx[-1] + dx)
        yy.append(yy[-1] + dy)
    df["ball_x"] = xx[1:]
    df["ball_y"] = yy[1:]
    if return_widths:
        return df, np.array(all_widths)
    return df


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
        "peak_speed_deg", "linearity", "sparc", "reverse_ratio", "decel_frac",
        "endpoint_peak", "peak_position_pct", "corrective_count",
        "submovement_overlap", "path_efficiency", "path_length_deg", "throughput",
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
        compute_fair_metrics(
            f, speed, accel, track_df, deg_per_px=deg_per_px, fps=fps
        )
        for f in flicks
    ]
    cm_per_deg = (cm_per_360 / 360.0) if cm_per_360 else None
    summary = _summarize_reference(metrics, cm_per_deg)
    return ReferenceAnalysis(
        flicks=flicks, metrics=metrics, summary=summary, window=window,
        start_frame=start_frame, end_frame=end_frame,
    )


def analyze_flicking_fair_summary(
    video_path,
    csv_path,
    *,
    fov: float = 103.0,
    cm_per_360=None,
    ui_area_frac: float = 0.01,
    output_dir=OUTPUT_DIR,
    progress_callback=None,
    return_extras: bool = False,
    stats=None,
):
    """CSV-mode fair-summary entry (PROGRESS A).

    Same shape as ``analyze_flicking_reference``'s summary, but driven by a
    KovaaK stats CSV (duration from kills) instead of a manual duration. This
    unblocks the user's own CSV recordings for the coaching pipeline.

    ``return_extras=True`` additionally returns a second element: a dict of
    timeline-friendly metadata (fps, duration_frames, per-flick landmarks,
    kill frames, corrective markers) for callers that want to build video
    timeline markers. Defaults to False so existing callers keep getting a
    bare summary dict.

    ``stats`` may carry a caller-verified in-memory Stats object. When present,
    the visual pass must not reopen ``csv_path`` and silently switch revisions.
    """
    import math
    if stats is None:
        stats = parse_stats_csv(csv_path)
    # 全 miss(kills 空)→ max()=NaN → math.ceil(NaN) ValueError。有效 KovaaK 场景需 guard。
    max_t = stats.kills["time_s"].max() if len(stats.kills) > 0 else None
    if max_t is None or pd.isna(max_t):
        raise ValueError(
            "CSV 无 kills 数据,无法确定场景时长。请确认 CSV 含击杀记录或显式传 duration。"
        )
    duration_s = float(math.ceil(max_t))

    meta = get_video_metadata(video_path)
    fps = meta.fps
    deg_per_px = fov / meta.width

    window = lock_challenge_window(video_path, duration_s, fps=fps, ui_area_frac=ui_area_frac)
    track_df, widths_px = compute_pan_trajectory(
        video_path, window.start_frame, window.end_frame,
        fps=fps, progress_callback=progress_callback, return_widths=True,
    )

    speed = _ball_speed(track_df, fps)
    accel = calc_derivative(speed, fps)
    win = max(5, int(fps * 0.05))
    if win % 2 == 0:
        win += 1
    accel = apply_smoothing(accel, win)

    # Representative target width for Fitts throughput: median of all detected
    # target widths (px), converted to degrees. KovaaK click-timing targets are
    # uniform size, so every detection samples the same quantity — median is
    # robust to spurious detections and despawning targets.
    target_width_deg = None
    if len(widths_px) > 0:
        target_width_deg = float(np.median(widths_px)) * deg_per_px

    flicks = segment_by_valleys(speed, fps)
    metrics = [
        compute_fair_metrics(
            f, speed, accel, track_df, deg_per_px=deg_per_px, fps=fps,
            target_width_deg=target_width_deg,
        )
        for f in flicks
    ]
    cm_per_deg = (cm_per_360 / 360.0) if cm_per_360 else None
    summary = _summarize_reference(metrics, cm_per_deg)

    if not return_extras:
        return summary

    # track_df is indexed from window.start_frame; flick tuple indices reference
    # this DataFrame. Map each flick's idx back to the absolute video frame and
    # time so the timeline markers line up with the player's video scrubber.
    frames_col = track_df["frame"].to_numpy() if "frame" in track_df.columns else None
    duration_frames = int(window.end_frame - window.start_frame + 1)

    def _abs(idx: int) -> int:
        if frames_col is not None and 0 <= idx < len(frames_col):
            return int(frames_col[idx])
        return int(window.start_frame + idx)

    flick_records = []
    corrective_frames = []
    for flick, m in zip(flicks, metrics):
        s_idx, p_idx, e_idx, peak_v, dur_s = flick
        flick_records.append({
            "start_frame": _abs(s_idx),
            "peak_frame": _abs(p_idx),
            "end_frame": _abs(e_idx),
            "peak_speed_px": round(float(peak_v), 2),
            "duration_s": round(float(dur_s), 4),
        })
        # Corrective submovements sit in the deceleration tail after the peak.
        # Approximate the corrective marker at the midpoint between peak and end
        # (submovement centroid, not exact) — good enough for a timeline pin.
        if getattr(m, "corrective_count", 0) and m.corrective_count > 0:
            mid = p_idx + max(1, (e_idx - p_idx) // 2)
            corrective_frames.append(_abs(mid))

    # Kills: csv time_s → absolute video frame via the locked window.
    # window.start_frame is the video frame at scenario t=0.
    kill_frames = []
    for t_s in stats.kills["time_s"].tolist():
        try:
            kill_frames.append(int(round(window.start_frame + float(t_s) * fps)))
        except (TypeError, ValueError):
            continue

    extras = {
        "fps": int(fps),
        "duration_frames": duration_frames,
        "flicks": flick_records,
        "kill_frames": kill_frames,
        # corrective_frames 的每个帧是 peak→end 中点(submovement centroid 的粗估,
        # 非精确帧),前端展示时标注为近似定位。
        "corrective_frames": corrective_frames,
        "corrective_frame_estimated": True,
    }
    return summary, extras


__all__ = [
    "detect_targets",
    "compute_pan_trajectory",
    "analyze_flicking_reference",
    "analyze_flicking_fair_summary",
    "ReferenceAnalysis",
]
