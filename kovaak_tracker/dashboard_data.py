from __future__ import annotations

import json
from pathlib import Path

import cv2
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .settings import OUTPUT_DIR


def load_dashboard_data(output_dir: Path = OUTPUT_DIR) -> tuple[dict | None, pd.DataFrame | None]:
    metrics_path = output_dir / "metrics.json"
    csv_path = output_dir / "frame_errors.csv"
    if not metrics_path.exists() or not csv_path.exists():
        return None, None

    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    df = pd.read_csv(csv_path)
    if "chunk_id" not in df.columns:
        df["chunk_id"] = 0
    return metrics, df


def build_error_timeline(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    colors = px.colors.qualitative.Plotly

    for chunk_id, group in df.groupby("chunk_id"):
        color = colors[int(chunk_id) % len(colors)]
        fig.add_trace(
            go.Scatter(
                x=group["time_s"],
                y=group["error_px"],
                mode="lines",
                name=f"Target {int(chunk_id) + 1}",
                line=dict(color=color, width=2),
            )
        )

    off_target_df = df[df["on_target"] == 0]
    if not off_target_df.empty:
        fig.add_trace(
            go.Scatter(
                x=off_target_df["time_s"],
                y=off_target_df["error_px"],
                mode="markers",
                name="Target Lost",
                marker=dict(color="#EF233C", size=5, symbol="x"),
            )
        )

    fig.update_layout(
        xaxis_title="Time (s)",
        yaxis_title="Spatial Error (px)",
        hovermode="x unified",
        height=400,
        margin=dict(l=0, r=0, t=30, b=0),
    )
    return fig


def render_review_frame(video_path: str, row: pd.Series):
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, row["frame"])
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None

    vis = frame.copy()
    cross_pos = (int(row["cross_x"]), int(row["cross_y"]))
    cv2.drawMarker(vis, cross_pos, (0, 180, 255), cv2.MARKER_CROSS, 20, 2)

    if pd.notna(row["ball_w"]):
        cx, cy = int(row["ball_x"]), int(row["ball_y"])
        width, height = int(row["ball_w"]), int(row["ball_h"])
        top_left = (int(cx - width / 2), int(cy - height / 2))
        bottom_right = (int(cx + width / 2), int(cy + height / 2))
        color = (0, 255, 0) if row["on_target"] == 1 else (0, 0, 255)

        cv2.rectangle(vis, top_left, bottom_right, color, 2)
        cv2.circle(vis, (cx, cy), 2, color, -1)
        cv2.line(vis, (cx, cy), cross_pos, color, 1)

    return cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)

