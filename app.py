"""
kovaak_tracker/app.py
======================
Calibration + Verification, Streamlit Web Edition
- Sequence Optimization: Trim Video -> Select Frame & Extract Color -> Run Analysis

Usage:
    streamlit run app.py
"""

import os
import tempfile
from pathlib import Path

import cv2
import streamlit as st

from kovaak_tracker.vision import get_hsv_range, detect_ball_by_color, frame_to_rgb, sample_median_bgr
from kovaak_tracker.tracking import run_tracking_analysis
from kovaak_tracker.video import get_video_metadata, read_frame
from kovaak_tracker.settings import ensure_output_dir

# Set Streamlit page configuration
st.set_page_config(page_title="KovaaK Tracker - Calibration", layout="wide")
st.title("KovaaK Tracking Analyzer")
st.caption("Step 1: Calibration - Video Trimming and Feature Extraction")

ensure_output_dir()

# Initialize session state
if 'start_frame' not in st.session_state:
    st.session_state.start_frame = 0
if 'end_frame' not in st.session_state:
    st.session_state.end_frame = None  # Will be set after video loads
if 'prev_temp_file' not in st.session_state:
    st.session_state.prev_temp_file = None

# -- 1. Upload and Load --
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

metadata = get_video_metadata(video_path)
fps = metadata.fps
width = metadata.width
height = metadata.height
total = metadata.frame_count
st.success(f"Video Loaded: {width}x{height} @ {fps:.1f}fps")

# -- 2. Video Trimming and Preview --
st.header("1. Trim Valid Video Clip")
st.caption("Drag the slider to remove 'junk time' at the beginning or end (e.g., menus):")

start_frame, end_frame = st.slider("Select Valid Video Range", 0, total, (0, total))
process_length = end_frame - start_frame
st.info(f"Processing: From frame {start_frame} to {end_frame} (Total {process_length} frames)")

col1, col2 = st.columns(2)

frame_start = read_frame(video_path, start_frame)
col1.image(frame_to_rgb(frame_start), caption=f"Start Frame (Idx {start_frame})", use_container_width=True)

if end_frame > 0:
    frame_end = read_frame(video_path, max(0, end_frame - 1))
    col2.image(frame_to_rgb(frame_end), caption=f"End Frame (Idx {end_frame})", use_container_width=True)

if st.button("Preview Dynamic Playback", type="secondary"):
    preview_placeholder = st.empty()
    cap_play = cv2.VideoCapture(video_path)
    cap_play.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    max_preview_frames = 150
    step = max(1, process_length // max_preview_frames)

    for i in range(0, process_length, step):
        ret, p_frame = cap_play.read()
        if not ret:
            break

        current_f = start_frame + i
        cv2.putText(p_frame, f"Preview: Frame {current_f}", (30, 50),
                     cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        preview_placeholder.image(frame_to_rgb(p_frame), use_container_width=True)

        if step > 1:
            cap_play.set(cv2.CAP_PROP_POS_FRAMES, current_f + step)

    cap_play.release()
    preview_placeholder.empty()

# -- 3. Select Template Frame and Extract Color --
st.header("2. Extract Target Color")
st.caption("Within your trimmed range, find a frame where the target is clearly visible:")

template_frame_idx = st.slider(
    "Slide to find the target ball", start_frame, max(start_frame, end_frame - 1), start_frame
)

selected_frame = read_frame(video_path, template_frame_idx)

st.image(frame_to_rgb(selected_frame), use_container_width=True)

st.caption("Note: Do not select the entire ball! Only box a small pure color area at the center.")
col1, col2, col3, col4 = st.columns(4)
x1 = col1.number_input("Left x1", 0, width, width // 2 - 10)
y1 = col2.number_input("Top y1", 0, height, height // 2 - 10)
x2 = col3.number_input("Right x2", 0, width, width // 2 + 10)
y2 = col4.number_input("Bottom y2", 0, height, height // 2 + 10)

if x2 <= x1 or y2 <= y1:
    st.warning("Invalid ROI: x2 must be > x1 and y2 must be > y1.")
    st.stop()

ball_bgr = sample_median_bgr(selected_frame, int(x1), int(y1), int(x2), int(y2))
b_lo, b_hi = get_hsv_range(ball_bgr)

preview = selected_frame.copy()
cv2.rectangle(preview, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
st.image(frame_to_rgb(preview), caption=f"Extracted RGB: {ball_bgr[::-1]}")

if st.button("Preview Detection on This Frame"):
    preview_frame = selected_frame.copy()
    ball_pos, bw, bh = detect_ball_by_color(preview_frame, b_lo, b_hi)
    if ball_pos:
        cx, cy = ball_pos
        cv2.rectangle(preview_frame, (cx - bw // 2, cy - bh // 2), (cx + bw // 2, cy + bh // 2), (0, 220, 80), 2)
        cv2.circle(preview_frame, (cx, cy), 2, (0, 220, 80), -1)
        st.image(frame_to_rgb(preview_frame), caption=f"Detection OK - Ball at ({cx}, {cy}), size {bw}x{bh}")
    else:
        st.warning("No target detected with current color range. Try a different sample area.")
        st.image(frame_to_rgb(preview_frame), caption="No detection")

# -- 4. Run Analysis --
st.header("3. Start Analysis")

if st.button("Run Deep Analysis", type="primary"):
    progress = st.progress(0, text="Initializing tracking engine...")

    def update_progress(value: float, text: str | None = None) -> None:
        progress.progress(value, text=text)

    tracking_run = run_tracking_analysis(
        video_path=video_path,
        start_frame=start_frame,
        end_frame=end_frame,
        ball_bgr=ball_bgr,
        ball_hsv_lo=b_lo,
        ball_hsv_hi=b_hi,
        progress_callback=update_progress,
        warn_callback=st.warning,
    )
    progress.empty()

    # Display CSRT tracking stats
    stats = tracking_run.stats
    st.subheader("Tracking Engine Performance")
    col1, col2, col3 = st.columns(3)
    col1.metric("High-Speed Tracked Frames", stats["frames_tracked"],
                help="Frames handled by OpenCV CSRT tracker")
    col2.metric("Color Detected Frames", stats["frames_detected"],
                help="Frames requiring full color search")
    col3.metric("Target Lost Frames", stats["frames_lost"],
                delta="-", delta_color="inverse")

    if tracking_run.preview_frames:
        cols = st.columns(len(tracking_run.preview_frames))
        for col, img in zip(cols, tracking_run.preview_frames):
            col.image(img)

    st.success(
        "Data Saved! You can now run Analyze.py in your terminal:\n"
        "python Analyze.py --csv output/calibration_raw.csv"
    )
