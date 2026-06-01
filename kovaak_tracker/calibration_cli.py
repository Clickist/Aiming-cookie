from __future__ import annotations

import json
from pathlib import Path

import cv2
import pandas as pd

from .settings import OUTPUT_DIR, ensure_output_dir
from .video import get_video_metadata
from .vision import detect_ball_by_color, detect_point_by_color, get_hsv_range


def mouse_pick_color(event, x, y, flags, param) -> None:
    if event == cv2.EVENT_LBUTTONDOWN:
        bgr = param["frame"][y, x].tolist()
        param["picked"] = bgr
        print(f"Sampled BGR={bgr}")


def select_color_interactive(frame, title: str, is_crosshair: bool = False):
    param = {"frame": frame.copy(), "picked": None}
    cv2.namedWindow(title)
    cv2.setMouseCallback(title, mouse_pick_color, param)
    cv2.imshow(title, cv2.resize(frame, None, fx=1.5, fy=1.5))

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == 13 and param["picked"]:
            cv2.destroyWindow(title)
            bgr = param["picked"]
            lo, hi = get_hsv_range(bgr, is_crosshair)
            return bgr, lo, hi
        if key == 27:
            cv2.destroyAllWindows()
            raise SystemExit("Calibration canceled.")


def run_calibration(video_path: str, max_frames: int = 100, output_dir: Path = OUTPUT_DIR) -> None:
    output_dir = ensure_output_dir(output_dir)
    metadata = get_video_metadata(video_path)

    cap = cv2.VideoCapture(video_path)
    ok, first_frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError("Cannot read the first video frame.")

    print("\nStep 1: click target color, then press Enter.")
    ball_bgr, ball_lo, ball_hi = select_color_interactive(first_frame, "Pick target color", False)

    print("\nStep 2: click crosshair color, then press Enter.")
    cross_bgr, cross_lo, cross_hi = select_color_interactive(first_frame, "Pick crosshair color", True)

    config = {
        "ball_bgr": ball_bgr,
        "ball_hsv_lo": ball_lo.tolist(),
        "ball_hsv_hi": ball_hi.tolist(),
        "crosshair_bgr": cross_bgr,
        "crosshair_hsv_lo": cross_lo.tolist(),
        "crosshair_hsv_hi": cross_hi.tolist(),
        "fps": metadata.fps,
        "resolution": [metadata.width, metadata.height],
    }

    with open(output_dir / "calib_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    out_path = output_dir / "calibration_check.mp4"
    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        metadata.fps,
        (metadata.width, metadata.height),
    )

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    results = []
    ball_found = 0
    cross_found = 0

    for idx in range(max_frames):
        ok, frame = cap.read()
        if not ok:
            break

        vis = frame.copy()
        ball_pos, ball_w, ball_h = detect_ball_by_color(frame, ball_lo, ball_hi)
        cross_pos, _, _ = detect_point_by_color(frame, cross_lo, cross_hi, min_area=5)

        if ball_pos:
            ball_found += 1
            cv2.circle(vis, ball_pos, 25, (0, 220, 80), 2)
            cv2.putText(vis, "Target", (ball_pos[0] + 28, ball_pos[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 80), 1)

        if cross_pos:
            cross_found += 1
            cv2.drawMarker(vis, cross_pos, (0, 180, 255), cv2.MARKER_CROSS, 20, 2)

        if ball_pos and cross_pos:
            cv2.line(vis, ball_pos, cross_pos, (255, 200, 0), 1)

        results.append(
            {
                "frame": idx,
                "time_s": round(idx / metadata.fps, 3),
                "ball_x": ball_pos[0] if ball_pos else None,
                "ball_y": ball_pos[1] if ball_pos else None,
                "ball_w": ball_w if ball_pos else None,
                "ball_h": ball_h if ball_pos else None,
                "cross_x": cross_pos[0] if cross_pos else None,
                "cross_y": cross_pos[1] if cross_pos else None,
            }
        )
        writer.write(vis)

    writer.release()
    cap.release()

    pd.DataFrame(results).to_csv(output_dir / "calibration_raw.csv", index=False)
    print(f"\nTarget detection: {ball_found}/{max_frames} ({ball_found / max_frames * 100:.1f}%)")
    print(f"Crosshair detection: {cross_found}/{max_frames} ({cross_found / max_frames * 100:.1f}%)")
    print(f"Check video: {out_path}")
