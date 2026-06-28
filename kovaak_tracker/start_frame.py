"""Auto-detect the scenario start frame in a recorded video.

The plan (``docs/flicking-analysis-plan.md`` line 243) specifies: "detect the
UI switch — the transition from non-gameplay to gameplay (menu / countdown
disappears, targets first appear)".

Signal: per-frame content difference (MSE between downsampled grayscale
frames). A menu/loading screen is near-static (low inter-frame difference); the
moment gameplay begins the target moves and the camera pans, so inter-frame
difference jumps. We scan a sampled window, locate the first sustained burst of
high-difference frames, and return that as the suggested start frame. The user
then confirms or nudges it with a slider (see ``app.py``).

A suggested frame on its own is not trustworthy enough to hand the user — we
also return the surrounding difference curve so the UI can show confidence
(spike magnitude, how long the high-difference state holds).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .video import get_video_metadata, read_frame


@dataclass(frozen=True)
class StartFrameSuggestion:
    """Result of auto-detecting the scenario start frame.

    ``frame`` is the detected transition; ``confidence`` is a 0–1 heuristic
    (how sharply the difference spikes and how stably it stays high). ``curve``
    holds the sampled difference signal for UI visualization.
    """

    frame: int
    confidence: float
    curve: list[tuple[int, float]]  # (sampled_frame_idx, diff) pairs


def detect_start_frame(
    video_path: str,
    *,
    end_frame: int | None = None,
    sample_step: int = 6,
    warmup_frames: int = 60,
    hold_frames: int = 12,
    diff_quantile: float = 0.75,
    downsample_width: int = 160,
) -> StartFrameSuggestion:
    """Suggest the frame at which gameplay (the scenario) begins.

    The scan samples every ``sample_step``-th frame from frame 0 up to
    ``end_frame`` (default: whole video), downsampled to ``downsample_width``
    for speed. For each sampled frame it computes the grayscale MSE vs. the
    previous sample. A frame is "active" if its difference exceeds the
    ``diff_quantile`` of the session's differences. The suggested start is the
    first active frame that is followed by ``hold_frames`` more active frames
    within the next samples — i.e. a *sustained* transition, not a one-off
    flicker (which menus also produce).

    ``warmup_frames`` is skipped at the very start so a recording's opening
    black/capture artifacts don't seed a false transition.
    """
    metadata = get_video_metadata(video_path)
    total = end_frame if end_frame is not None else metadata.frame_count
    cap = cv2.VideoCapture(video_path)

    sampled: list[tuple[int, float]] = []  # (frame_idx, diff)
    prev_gray: np.ndarray | None = None
    cap.set(cv2.CAP_PROP_POS_FRAMES, warmup_frames)
    f = warmup_frames
    while f < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            break
        gray = _to_downsampled_gray(frame, downsample_width)
        if prev_gray is not None:
            diff = float(np.mean((gray.astype(np.float32) - prev_gray.astype(np.float32)) ** 2))
            sampled.append((f, diff))
        prev_gray = gray
        f += sample_step
    cap.release()

    if not sampled:
        return StartFrameSuggestion(frame=warmup_frames, confidence=0.0, curve=[])

    diffs = np.array([d for _, d in sampled])
    threshold = float(np.quantile(diffs, diff_quantile))
    active = diffs >= threshold

    # First active index whose next `hold_frames` active-flags (within window)
    # are mostly active — a sustained transition, not a flicker.
    suggested_frame = sampled[-1][0]
    n = len(sampled)
    hold_ratio = 0.6
    for i in range(n):
        if not active[i]:
            continue
        window = active[i : i + hold_frames + 1]
        if window.sum() / len(window) >= hold_ratio:
            suggested_frame = sampled[i][0]
            break

    confidence = _confidence(diffs, active, threshold)
    return StartFrameSuggestion(frame=suggested_frame, confidence=confidence, curve=sampled)


def _to_downsampled_gray(frame: np.ndarray, target_width: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if w > target_width:
        new_h = max(1, int(h * target_width / w))
        gray = cv2.resize(gray, (target_width, new_h), interpolation=cv2.INTER_AREA)
    return gray


def _confidence(diffs: np.ndarray, active: np.ndarray, threshold: float) -> float:
    """Heuristic 0–1: how sharply the difference jumps at the transition and
    how stably the active state holds."""
    if diffs.size == 0 or threshold <= 0:
        return 0.0
    spike = float(diffs.max() / threshold) if threshold > 0 else 0.0
    stability = float(active.mean()) if active.size else 0.0
    # spike≥2 is a clean jump; stability≥0.6 is a durable state. Normalize.
    spike_score = min(1.0, spike / 2.0)
    return round(0.5 * spike_score + 0.5 * stability, 3)


@dataclass(frozen=True)
class ChallengeWindow:
    """The challenge ``[start_frame, end_frame]`` window, locked from UI events.

    ``ui_events`` lists every detected UI-screen interval (the initial load
    screen, each countdown/restart before the challenge, and the results page
    after). ``duration_match`` is True when the selected gameplay segment's
    length is within tolerance of the expected scenario duration — the strongest
    correctness signal. ``confidence`` blends duration-fit with how dominant the
    chosen run is among all gameplay segments.
    """

    start_frame: int
    end_frame: int
    ui_events: tuple
    segment_frames: int
    duration_match: bool
    confidence: float
    note: str


def lock_challenge_window(
    video_path: str,
    duration_s: float,
    *,
    fps: float | None = None,
    ui_area_frac: float = 0.001,
) -> ChallengeWindow:
    """Lock the challenge window robustly via UI-screen event detection.

    A KovaaK recording brackets the timed challenge with UI screens: countdown
    numbers before it (one per restart attempt) and a results page after. These
    are LARGE central elements — far bigger than the targets — so they remain
    detectable even when the small targets are invisible to coarse frame-diff or
    lost by CSRT during fast flicks. We find them with adaptive background
    subtraction (median-color distance, color-agnostic), split frames into
    gameplay runs vs UI runs, and select the gameplay run whose length matches
    ``duration_s``. That run's first frame is the challenge start.

    Robust to arbitrary background/target colors, any number of pre-challenge
    restarts, and countdown screens of varying size/position. ``duration_s``
    (the scenario duration, e.g. from the CSV) disambiguates the real challenge
    from the short restart attempts that precede it.
    """
    meta = get_video_metadata(video_path)
    fps = fps if fps is not None else meta.fps
    total = meta.frame_count
    target_len = int(round(duration_s * fps))
    tol = max(3, int(0.03 * target_len))  # ~3% tolerance

    cap = cv2.VideoCapture(video_path)
    gameplay_runs: list = []
    ui_events: list = []
    g_start = None  # first frame of current gameplay run
    u_start = None  # first frame of current UI run
    # Sample every `stride` frames (~10 Hz) + seek once + read sequentially.
    # Per-frame cap.set is O(frame_no) on H.264 (the original hot loop). UI
    # screens persist >1s, so 10 Hz sampling never misses one; gameplay-run
    # boundaries land within +/- stride of truth, far inside the duration tol.
    stride = max(1, int(round(fps / 10.0)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    for off in range(total):
        ok, frame = cap.read()
        if not ok:
            break
        if off % stride != 0:
            continue
        f = off
        if _has_ui_element(frame, ui_area_frac):
            if g_start is not None:
                gameplay_runs.append((g_start, f - 1)); g_start = None
            if u_start is None:
                u_start = f
        else:
            if u_start is not None:
                ui_events.append((u_start, f - 1)); u_start = None
            if g_start is None:
                g_start = f
    if g_start is not None:
        gameplay_runs.append((g_start, total - 1))
    if u_start is not None:
        ui_events.append((u_start, total - 1))
    cap.release()

    if not gameplay_runs:
        return ChallengeWindow(0, total - 1, tuple(ui_events), 0, False, 0.0,
                               "no gameplay segment detected")

    best = min(gameplay_runs, key=lambda r: abs((r[1] - r[0] + 1) - target_len))
    seg_len = best[1] - best[0] + 1
    duration_match = abs(seg_len - target_len) <= tol
    fit = 1.0 - min(1.0, abs(seg_len - target_len) / max(1, tol))
    longest_share = seg_len / sum(r[1] - r[0] + 1 for r in gameplay_runs)
    confidence = round(0.6 * fit + 0.4 * longest_share, 3)
    note = "duration match" if duration_match else (
        f"closest run ({seg_len} vs {target_len} frames)")
    return ChallengeWindow(
        start_frame=best[0], end_frame=best[1], ui_events=tuple(ui_events),
        segment_frames=seg_len, duration_match=duration_match,
        confidence=confidence, note=note,
    )


def _has_ui_element(frame, area_frac, max_width=960):
    """True if the frame has a large non-background element (a countdown/results UI).

    Color- and position-agnostic: compares each pixel to the frame's median
    (background) color, thresholds the distance map with Otsu (robust to the UI
    occupying any fraction of the frame — a text-heavy results page does not
    inflate the threshold the way a high percentile would), and asks whether ANY
    connected blob exceeds ``area_frac`` of the frame area. Targets are small;
    countdown numbers and results panels are large, so this separates them no
    matter where on screen the UI sits. A 40-distance floor keeps a uniform,
    target-less frame from degenerating Otsu to ~0 and merging the whole frame.

    Runs on a frame downsampled to <= ``max_width`` px wide for speed (the
    full-res distance map was the per-frame hot spot). ``area_frac`` is
    relative, so downscaling preserves the size test.
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
    otsu_thr, _ = cv2.threshold(dist.astype(np.uint8), 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(40.0, float(otsu_thr))
    mask = (dist > thr).astype(np.uint8) * 255
    mask[:int(sh * 0.12), :] = 0  # exclude top HUD
    mask[int(sh * 0.90):, :] = 0  # exclude bottom HUD
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = area_frac * sw * sh
    return any(cv2.contourArea(c) > min_area for c in contours)


__all__ = ["StartFrameSuggestion", "detect_start_frame", "ChallengeWindow", "lock_challenge_window"]
