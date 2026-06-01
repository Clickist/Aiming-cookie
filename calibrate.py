"""
kovaak_tracker/calibrate.py
============================
第一步：校准脚本 (V2 优化版 - 颜色特征提取)
- 从视频提取第一帧
- 用户点选小球颜色 (免疫背景干扰)
- 用户点选准星颜色
- 在前100帧跑检测，输出可视化验证视频

用法：
    python calibrate.py --video your_recording.mp4
"""

import cv2
import numpy as np
import argparse
import json
from pathlib import Path

import pandas as pd

from cv_detect import get_hsv_range, detect_ball_by_color, detect_crosshair_by_color

# ── 交互式颜色采样 ──

def mouse_pick_color(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        bgr = param["frame"][y, x].tolist()
        param["picked"] = bgr
        print(f"  ✓ 采样成功 BGR={bgr}")

def select_color_interactive(frame, title_msg, is_crosshair=False):
    win = title_msg
    param = {"frame": frame.copy(), "picked": None}
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, mouse_pick_color, param)
    cv2.imshow(win, cv2.resize(frame, None, fx=1.5, fy=1.5))

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == 13 and param["picked"]: # Enter 确认
            cv2.destroyWindow(win)
            bgr = param["picked"]
            lo, hi = get_hsv_range(bgr)
            return bgr, lo, hi
        elif key == 27: # Esc 取消
            cv2.destroyAllWindows()
            raise SystemExit("用户取消")

# ── 主流程 ──

def run_calibration(video_path, max_frames=100, output_dir="output"):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"无法打开视频: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    ret, first_frame = cap.read()
    if not ret:
        raise RuntimeError("无法读取视频第一帧")

    print("\n=== 第一步：点选【小球】颜色 ===")
    ball_bgr, b_lo, b_hi = select_color_interactive(first_frame, "【步骤1】点击小球采样颜色，按 Enter 确认", False)
    
    print("\n=== 第二步：点选【准星】颜色 ===")
    cross_bgr, c_lo, c_hi = select_color_interactive(first_frame, "【步骤2】点击准星采样颜色，按 Enter 确认", True)

    config = {
        "ball_bgr": ball_bgr,
        "ball_hsv_lo": b_lo.tolist(),
        "ball_hsv_hi": b_hi.tolist(),
        "crosshair_bgr": cross_bgr,
        "crosshair_hsv_lo": c_lo.tolist(),
        "crosshair_hsv_hi": c_hi.tolist(),
        "fps": fps,
        "resolution": [w, h],
    }
    
    with open(out_dir / "calib_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n=== 第三步：验证前 {max_frames} 帧检测效果 ===")
    out_path = str(out_dir / "calibration_check.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (w, h))

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    results = []
    b_found, c_found = 0, 0

    for i in range(max_frames):
        ret, frame = cap.read()
        if not ret: break

        vis = frame.copy()
        ball_pos, ball_w, ball_h = detect_ball_by_color(frame, b_lo, b_hi)
        cross_pos, _, _ = detect_crosshair_by_color(frame, c_lo, c_hi)

        if ball_pos:
            b_found += 1
            cv2.circle(vis, ball_pos, 25, (0, 220, 80), 2)
            cv2.putText(vis, "Target", (ball_pos[0]+28, ball_pos[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 80), 1)

        if cross_pos:
            c_found += 1
            cv2.drawMarker(vis, cross_pos, (0, 180, 255), cv2.MARKER_CROSS, 20, 2)

        if ball_pos and cross_pos:
            cv2.line(vis, ball_pos, cross_pos, (255, 200, 0), 1)
            dist = np.hypot(ball_pos[0]-cross_pos[0], ball_pos[1]-cross_pos[1])
            mid = ((ball_pos[0]+cross_pos[0])//2, (ball_pos[1]+cross_pos[1])//2)
            cv2.putText(vis, f"{dist:.0f}px", mid, cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 200, 0), 1)

        results.append({
            "frame": i, "time_s": round(i/fps, 3),
            "ball_x": ball_pos[0] if ball_pos else None,
            "ball_y": ball_pos[1] if ball_pos else None,
            "ball_w": ball_w if ball_w else None,
            "ball_h": ball_h if ball_h else None,
            "cross_x": cross_pos[0] if cross_pos else None,
            "cross_y": cross_pos[1] if cross_pos else None,
        })
        writer.write(vis)

    writer.release()
    cap.release()

    print(f"\n── 检测结果 ──")
    print(f"  小球识别率:  {b_found}/{max_frames} ({b_found/max_frames*100:.1f}%)")
    pd.DataFrame(results).to_csv(out_dir / "calibration_raw.csv", index=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--frames", type=int, default=100)
    args = parser.parse_args()
    run_calibration(args.video, args.frames)