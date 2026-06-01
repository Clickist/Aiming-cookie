"""
kovaak_tracker/app.py
======================
Calibration + Verification, Streamlit Web Edition (V5 WebM Compatible)
- Sequence Optimization: Trim Video -> Select Frame & Extract Color -> Run Analysis

Usage:
    streamlit run app.py
"""

import os
import streamlit as st
import cv2
import numpy as np
import pandas as pd
import json
import tempfile
from pathlib import Path
from cv_detect import detect_ball_by_color, get_hsv_range

# Set Streamlit page configuration
st.set_page_config(page_title="KovaaK Tracker · Calibration", layout="wide")
st.title("KovaaK Tracking Analyzer")
st.caption("Step 1: Calibration — Video Trimming and Feature Extraction")

# Define and create output directory
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Initialize session state
if 'start_frame' not in st.session_state:
    st.session_state.start_frame = 0
if 'end_frame' not in st.session_state:
    st.session_state.end_frame = None  # Will be set after video loads
if 'prev_temp_file' not in st.session_state:
    st.session_state.prev_temp_file = None

# ── Core Algorithms ──

def frame_to_rgb(frame):
    """Converts BGR frame (OpenCV) to RGB (Streamlit)."""
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

# ── 1. Upload and Load ──
video_file = st.file_uploader("Support mp4 / avi / mov / webm", type=["mp4", "avi", "mov", "webm"])
if video_file is None:
    st.warning("Please upload a video to continue.")
    st.stop()

# Dynamically extract file extension to support WebM
file_extension = Path(video_file.name).suffix

with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp:
    tmp.write(video_file.read())
    video_path = tmp.name

# Cleanup previous temp file from last Streamlit re-run
if st.session_state.prev_temp_file:
    try:
        os.unlink(st.session_state.prev_temp_file)
    except OSError:
        pass
st.session_state.prev_temp_file = video_path

cap = cv2.VideoCapture(video_path)
fps = cap.get(cv2.CAP_PROP_FPS)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
st.success(f"Video Loaded: {width}x{height} @ {fps:.1f}fps")
cap.release()

# ── 2. Video Trimming and Preview ──
st.header("1. Trim Valid Video Clip")
st.caption("Drag the slider to remove 'junk time' at the beginning or end (e.g., menus):")

start_frame, end_frame = st.slider("Select Valid Video Range", 0, total, (0, total))
process_length = end_frame - start_frame
st.info(f"✂️ Processing: From frame {start_frame} to {end_frame} (Total {process_length} frames)")

col1, col2 = st.columns(2)
cap_preview = cv2.VideoCapture(video_path)

cap_preview.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
ret_start, frame_start = cap_preview.read()
if ret_start:
    col1.image(frame_to_rgb(frame_start), caption=f"▶ Start Frame (Idx {start_frame})", use_container_width=True)

cap_preview.set(cv2.CAP_PROP_POS_FRAMES, max(0, end_frame - 1))
ret_end, frame_end = cap_preview.read()
if ret_end:
    col2.image(frame_to_rgb(frame_end), caption=f"⏹ End Frame (Idx {end_frame})", use_container_width=True)

cap_preview.release()

if st.button("👁️ Preview Dynamic Playback", type="secondary"):
    preview_placeholder = st.empty()
    cap_play = cv2.VideoCapture(video_path)
    cap_play.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    
    max_preview_frames = 150 
    step = max(1, process_length // max_preview_frames)
    
    for i in range(0, process_length, step):
        ret, p_frame = cap_play.read()
        if not ret: break
        
        current_f = start_frame + i
        cv2.putText(p_frame, f"Preview: Frame {current_f}", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        preview_placeholder.image(frame_to_rgb(p_frame), use_container_width=True)
        
        if step > 1:
            cap_play.set(cv2.CAP_PROP_POS_FRAMES, current_f + step)

    cap_play.release()
    preview_placeholder.empty()

# ── 3. Select Template Frame and Extract Color ──
st.header("2. Extract Target Color")
st.caption("Within your trimmed range, find a frame where the target is clearly visible:")

template_frame_idx = st.slider("Slide to find the target ball", start_frame, max(start_frame, end_frame - 1), start_frame)

cap2 = cv2.VideoCapture(video_path)
cap2.set(cv2.CAP_PROP_POS_FRAMES, template_frame_idx)
ret2, selected_frame = cap2.read()
cap2.release()

st.image(frame_to_rgb(selected_frame), use_container_width=True)

st.caption("🚨 Note: Do not select the entire ball! Only box a small **pure color area** at the center.")
col1, col2, col3, col4 = st.columns(4)
x1 = col1.number_input("Left x1", 0, width,  width//2 - 10)
y1 = col2.number_input("Top y1", 0, height, height//2 - 10)
x2 = col3.number_input("Right x2", 0, width,  width//2 + 10)
y2 = col4.number_input("Bottom y2", 0, height, height//2 + 10)

if x2 <= x1 or y2 <= y1:
    st.warning("Invalid ROI: x2 must be > x1 and y2 must be > y1.")
    st.stop()

template = selected_frame[int(y1):int(y2), int(x1):int(x2)]
median_color = np.median(template.reshape(-1, 3), axis=0).astype(np.uint8)
ball_bgr = median_color.tolist()
b_lo, b_hi = get_hsv_range(ball_bgr)

preview = selected_frame.copy()
cv2.rectangle(preview, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
st.image(frame_to_rgb(preview), caption=f"Extracted RGB: {ball_bgr[::-1]}")

if st.button("🔍 Preview Detection on This Frame"):
    preview_frame = selected_frame.copy()
    ball_pos, bw, bh = detect_ball_by_color(preview_frame, b_lo, b_hi)
    if ball_pos:
        cx, cy = ball_pos
        cv2.rectangle(preview_frame, (cx - bw//2, cy - bh//2), (cx + bw//2, cy + bh//2), (0, 220, 80), 2)
        cv2.circle(preview_frame, (cx, cy), 2, (0, 220, 80), -1)
        st.image(frame_to_rgb(preview_frame), caption=f"Detection OK — Ball at ({cx}, {cy}), size {bw}x{bh}")
    else:
        st.warning("No target detected with current color range. Try a different sample area.")
        st.image(frame_to_rgb(preview_frame), caption="No detection")

# ── 4. Run Analysis ──
st.header("3. Start Analysis")

if st.button("▶ Run Deep Analysis", type="primary"):
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    
    results, preview_frames = [], []
    ball_found = 0
    # 准星固定为画面中心，不追踪实际游戏准星；PTC 实际测量的是目标相对于画面中心的运动
    cross_pos = (width // 2, height // 2)
    progress = st.progress(0, text="Extracting visual features...")

    for i in range(process_length):
        ret, frame = cap.read()
        if not ret: break

        vis = frame.copy()
        absolute_frame_idx = start_frame + i 
        
        ball_pos, ball_w, ball_h = detect_ball_by_color(frame, b_lo, b_hi)
        cv2.drawMarker(vis, cross_pos, (0, 180, 255), cv2.MARKER_CROSS, 18, 2)

        if ball_pos:
            ball_found += 1
            cx, cy = ball_pos
            w, h = ball_w, ball_h
            top_left = (cx - w // 2, cy - h // 2)
            bottom_right = (cx + w // 2, cy + h // 2)
            
            cv2.rectangle(vis, top_left, bottom_right, (0, 220, 80), 2)
            cv2.circle(vis, (cx, cy), 2, (0, 220, 80), -1)
            cv2.putText(vis, f"[{w}x{h} px]", (top_left[0], top_left[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 80), 1)
            cv2.line(vis, ball_pos, cross_pos, (255, 200, 0), 1)

        results.append({
            "frame": absolute_frame_idx, 
            "time_s": round(absolute_frame_idx / fps, 3),
            "ball_x": ball_pos[0] if ball_pos else None,
            "ball_y": ball_pos[1] if ball_pos else None,
            "ball_w": ball_w if ball_w else None,  
            "ball_h": ball_h if ball_h else None,
            "cross_x": cross_pos[0], "cross_y": cross_pos[1],
        })

        if i % 20 == 0: preview_frames.append(frame_to_rgb(vis))
        progress.progress((i + 1) / process_length)

    cap.release()
    progress.empty()

    ball_rate = ball_found / process_length * 100
    st.metric("Recognition Rate", f"{ball_rate:.1f}%", delta="✓" if ball_rate > 80 else "⚠")
    
    cols = st.columns(len(preview_frames))
    for col, img in zip(cols, preview_frames): col.image(img)

    config = {
        "ball_bgr": ball_bgr, "ball_hsv_lo": b_lo.tolist(), "ball_hsv_hi": b_hi.tolist(),
        "crosshair_mode": "center",  # 准星位置硬编码为画面中心，非实际游戏准星追踪
        "fps": fps, "resolution": [width, height],
    }
    with open(OUTPUT_DIR / "calib_config.json", "w") as f: json.dump(config, f)
    pd.DataFrame(results).to_csv(OUTPUT_DIR / "calibration_raw.csv", index=False)

    st.success("✨ Data Saved! You can now run Analyze.py in your terminal to generate the final report.")