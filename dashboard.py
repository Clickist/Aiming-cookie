"""
kovaak_tracker/dashboard.py
============================
Step 3: Interactive Visualizer
Displays the Tension Quadrant and Frame-by-Frame VOD review.
"""

import streamlit as st
import json
import plotly.graph_objects as go
from pathlib import Path

st.set_page_config(page_title="Aim Performance Dashboard", layout="wide")

# Load metrics
metrics_path = Path("output/metrics.json")
if not metrics_path.exists():
    st.error("metrics.json not found. Run Analyze.py first.")
    st.stop()
metrics = json.loads(metrics_path.read_text())

st.title("Aim Tension & Speed Matching Analysis")
t = metrics['tension']
l = metrics['loss']

col1, col2 = st.columns([1, 2])
with col1:
    st.metric("Pure Tension Coefficient", f"{t['ptc']} Hz²")
    st.metric("Mean Relative Acceleration", f"{t['mean_a_rel']} px/s²")

    st.divider()

    st.metric("On-Target Accuracy", f"{l['on_target_pct']}%")
    st.metric("Loss Count", l['loss_count'])
    st.metric("Total Off-Target Time", f"{l['total_off_time']} s")

with col2:
    # Quadrant Chart: Visualizing the PTC vs Error relationship
    fig = go.Figure()

    # Quadrant boundary lines
    fig.add_hline(y=100, line_dash="dash", line_color="gray")
    fig.add_vline(x=50, line_dash="dash", line_color="gray")

    # Quadrant labels (paper coordinates: 0-1 fraction of plot area)
    fig.add_annotation(x=0.25, y=0.95, xref="paper", yref="paper",
                       text="Over-tension", showarrow=False,
                       font=dict(size=12, color="orange"))
    fig.add_annotation(x=0.75, y=0.95, xref="paper", yref="paper",
                       text="Tense + Inaccurate", showarrow=False,
                       font=dict(size=12, color="red"))
    fig.add_annotation(x=0.25, y=0.05, xref="paper", yref="paper",
                       text="Ideal Tracking", showarrow=False,
                       font=dict(size=12, color="green"))
    fig.add_annotation(x=0.75, y=0.05, xref="paper", yref="paper",
                       text="Under-tension / Lagging", showarrow=False,
                       font=dict(size=12, color="purple"))

    # Data point with label
    fig.add_trace(go.Scatter(
        x=[t['avg_error_px']],
        y=[t['ptc']],
        mode='markers+text',
        marker=dict(size=20, color='cyan', line=dict(width=2, color='white')),
        text=[f"PTC={t['ptc']:.1f}\nErr={t['avg_error_px']:.1f}"],
        textposition="top right",
        textfont=dict(size=11, color='white'),
        name='Current Session'
    ))

    # Axis ranges with padding
    x_max = max(100, t['avg_error_px'] * 1.5)
    y_max = max(200, t['ptc'] * 1.5)

    fig.update_layout(
        title=f"Tension Quadrant — PTC: {t['ptc']:.1f} Hz², Error: {t['avg_error_px']:.1f} px",
        xaxis_title="Average Error (px)",
        yaxis_title="Pure Tension Coefficient (Hz²)",
        xaxis=dict(range=[0, x_max]),
        yaxis=dict(range=[0, y_max]),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
