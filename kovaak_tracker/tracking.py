from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pandas as pd

from .settings import OUTPUT_DIR, ensure_output_dir
from .video import get_video_metadata
from .vision import detect_ball_by_color, frame_to_rgb, get_tracker


ProgressCallback = Callable[[float, str | None], None]


@dataclass(frozen=True)
class TrackingRun:
    results: pd.DataFrame
    preview_frames: list[np.ndarray]
    stats: dict[str, int]
    config: dict
    raw_csv_path: Path
    config_path: Path


def run_tracking_analysis(
    *,
    video_path: str,
    start_frame: int,
    end_frame: int,
    ball_bgr: list[int],
    ball_hsv_lo: np.ndarray,
    ball_hsv_hi: np.ndarray,
    output_dir: Path = OUTPUT_DIR,
    preview_stride: int = 20,
    progress_callback: ProgressCallback | None = None,
    warn_callback: Callable[[str], None] | None = None,
) -> TrackingRun:
    output_dir = ensure_output_dir(output_dir)
    metadata = get_video_metadata(video_path)
    process_length = max(0, end_frame - start_frame)
    if process_length == 0:
        raise ValueError("Selected video range is empty.")

    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    results: list[dict] = []
    preview_frames: list[np.ndarray] = []
    cross_pos = (metadata.width // 2, metadata.height // 2)
    tracking_active = False
    tracker = None
    stats = {"frames_detected": 0, "frames_tracked": 0, "frames_lost": 0}

    for offset in range(process_length):
        ok, frame = cap.read()
        if not ok:
            break

        vis = frame.copy()
        absolute_frame_idx = start_frame + offset
        cv2.drawMarker(vis, cross_pos, (0, 180, 255), cv2.MARKER_CROSS, 18, 2)

        ball_pos = None
        ball_w = None
        ball_h = None
        box_color = (0, 0, 255)

        if not tracking_active:
            detected_pos, detected_w, detected_h = detect_ball_by_color(frame, ball_hsv_lo, ball_hsv_hi)
            if detected_pos and detected_w and detected_h:
                cx, cy = detected_pos
                bbox = (int(cx - detected_w / 2), int(cy - detected_h / 2), int(detected_w), int(detected_h))
                tracker = get_tracker(warn_callback)
                tracker.init(frame, bbox)
                tracking_active = True

                ball_pos, ball_w, ball_h = detected_pos, detected_w, detected_h
                stats["frames_detected"] += 1
                box_color = (0, 255, 255)
            else:
                stats["frames_lost"] += 1
        else:
            success, bbox = tracker.update(frame)
            if success:
                x, y, w, h = [int(v) for v in bbox]
                ball_pos = (int(x + w / 2), int(y + h / 2))
                ball_w, ball_h = w, h
                stats["frames_tracked"] += 1
                box_color = (0, 220, 80)
            else:
                tracking_active = False
                tracker = None
                stats["frames_lost"] += 1

        if ball_pos:
            cx, cy = ball_pos
            top_left = (int(cx - ball_w / 2), int(cy - ball_h / 2))
            bottom_right = (int(cx + ball_w / 2), int(cy + ball_h / 2))
            cv2.rectangle(vis, top_left, bottom_right, box_color, 2)
            cv2.circle(vis, (cx, cy), 2, box_color, -1)
            cv2.line(vis, ball_pos, cross_pos, (255, 200, 0), 1)

        results.append(
            {
                "frame": absolute_frame_idx,
                "time_s": round(absolute_frame_idx / metadata.fps, 3),
                "ball_x": ball_pos[0] if ball_pos else None,
                "ball_y": ball_pos[1] if ball_pos else None,
                "ball_w": ball_w if ball_pos else None,
                "ball_h": ball_h if ball_pos else None,
                "cross_x": cross_pos[0],
                "cross_y": cross_pos[1],
            }
        )

        if offset % preview_stride == 0:
            preview_frames.append(frame_to_rgb(vis))

        if progress_callback is not None:
            progress_callback((offset + 1) / process_length, None)

    cap.release()

    config = {
        "ball_bgr": ball_bgr,
        "ball_hsv_lo": ball_hsv_lo.tolist(),
        "ball_hsv_hi": ball_hsv_hi.tolist(),
        "crosshair_mode": "center",
        "fps": metadata.fps,
        "resolution": [metadata.width, metadata.height],
    }

    raw_csv_path = output_dir / "calibration_raw.csv"
    config_path = output_dir / "calib_config.json"
    results_df = pd.DataFrame(results)
    results_df.to_csv(raw_csv_path, index=False)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return TrackingRun(results_df, preview_frames, stats, config, raw_csv_path, config_path)
