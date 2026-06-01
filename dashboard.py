"""
dashboard.py
============
Streamlit dashboard combining:
  - Tension Quadrant (original)
  - Error Timeline (v8)
  - VOD Review with frame scrubber (v8)
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from kovaak_tracker.dashboard_data import (
    build_error_timeline,
    load_dashboard_data,
    render_review_frame,
)
from kovaak_tracker.video import save_uploaded_video

OUTPUT_DIR = Path("output")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Aim Tension Dashboard", layout="wide")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
metrics, df = load_dashboard_data(OUTPUT_DIR)
if metrics is None or df is None:
    st.warning("Analysis data not found. Run Analyze.py first.")
    st.stop()

t = metrics.get("tension", {})
l = metrics.get("loss", {})

# ---------------------------------------------------------------------------
# Top metrics row
# ---------------------------------------------------------------------------
st.title("Aim Tension & Speed Matching Analysis")
st.caption(
    "Diagnostics from tracking error, speed mismatch, and acceleration mismatch."
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("PTC (Hz²)", f"{t.get('ptc', 0):.1f}")
m2.metric("Avg Error (px)", f"{t.get('avg_error_px', 0):.1f}")
m3.metric("Accuracy (%)", f"{l.get('on_target_pct', 0):.1f}")
m4.metric("Loss Count", f"{l.get('loss_count', 0)}")
m5.metric("Off-Target Time (s)", f"{l.get('total_off_time', 0):.2f}")

# Optional kinematics row if speed/accel mismatch are available
if "speed_mismatch" in t or "accel_mismatch" in t:
    st.divider()
    k1, k2 = st.columns(2)
    k1.metric(
        "Speed Mismatch (px/s)",
        f"{t.get('speed_mismatch', 0):.1f}",
        help="Magnitude of velocity difference between crosshair and target.",
    )
    k2.metric(
        "Accel Mismatch (px/s²)",
        f"{t.get('accel_mismatch', 0):.0f}",
        help="Magnitude of acceleration difference.",
    )

# ---------------------------------------------------------------------------
# Tabs: Overview / Timeline / VOD Review
# ---------------------------------------------------------------------------
tab_overview, tab_timeline, tab_vod = st.tabs(
    ["Overview", "Error Timeline", "VOD Review"]
)

# -- Tab 1: Overview -- Tension Quadrant --
with tab_overview:
    st.subheader("Tension Quadrant")

    ptc = t.get("ptc", 0)
    avg_err = t.get("avg_error_px", 0)

    fig = go.Figure()

    # Quadrant boundary lines
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.6)
    fig.add_vline(x=50, line_dash="dash", line_color="gray", opacity=0.6)

    # Quadrant labels
    quadrant_labels = [
        (0.25, 0.95, "Over-tension", "orange"),
        (0.75, 0.95, "Tense + Inaccurate", "red"),
        (0.25, 0.05, "Ideal Tracking", "green"),
        (0.75, 0.05, "Under-tension / Lagging", "purple"),
    ]
    for x, y, text, color in quadrant_labels:
        fig.add_annotation(
            x=x,
            y=y,
            xref="paper",
            yref="paper",
            text=text,
            showarrow=False,
            font=dict(size=12, color=color),
        )

    # Data point
    fig.add_trace(
        go.Scatter(
            x=[avg_err],
            y=[ptc],
            mode="markers+text",
            marker=dict(size=20, color="cyan", line=dict(width=2, color="white")),
            text=[f"PTC={ptc:.1f}\nErr={avg_err:.1f}"],
            textposition="top right",
            textfont=dict(size=11, color="white"),
            name="Current Session",
        )
    )

    # Dynamic axis ranges with padding
    x_max = max(100, avg_err * 1.5)
    y_max = max(200, ptc * 1.5)

    fig.update_layout(
        title=f"Tension Quadrant — PTC: {ptc:.1f} Hz², Error: {avg_err:.1f} px",
        xaxis_title="Average Error (px)",
        yaxis_title="Pure Tension Coefficient (Hz²)",
        xaxis=dict(range=[0, x_max]),
        yaxis=dict(range=[0, y_max]),
        showlegend=False,
        height=500,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

# -- Tab 2: Error Timeline --
with tab_timeline:
    st.subheader("Spatial Error Timeline")
    st.plotly_chart(build_error_timeline(df), use_container_width=True)

# -- Tab 3: VOD Review --
with tab_vod:
    st.subheader("Precision VOD Review")

    # Check for a video already in output/
    existing_videos = list(OUTPUT_DIR.glob("*.mp4")) + list(OUTPUT_DIR.glob("*.avi"))
    video_path: str | None = None

    if existing_videos:
        st.info(f"Found video in output/: {existing_videos[0].name}")
        video_path = str(existing_videos[0])
    else:
        video_file = st.file_uploader(
            "Upload raw video (mp4 / avi / mov / webm)",
            type=["mp4", "avi", "mov", "webm"],
        )
        if video_file:
            video_path = save_uploaded_video(video_file, Path(video_file.name).suffix)

    if video_path:
        max_frame_idx = len(df) - 1
        frame_idx = st.slider(
            "Scrub to view tracked frames",
            min_value=0,
            max_value=max_frame_idx,
            value=0,
        )
        row = df.iloc[frame_idx]
        frame = render_review_frame(video_path, row)
        if frame is not None:
            st.image(frame, use_container_width=True)
            # Show frame info
            on_target_str = "ON TARGET" if row.get("on_target", 0) == 1 else "OFF TARGET"
            st.caption(
                f"Frame {int(row['frame'])} | "
                f"t={row['time_s']:.3f}s | "
                f"Error={row['error_px']:.1f}px | "
                f"{on_target_str}"
            )
        else:
            st.warning("Could not render frame from video.")
