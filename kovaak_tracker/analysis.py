from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from .settings import OUTPUT_DIR, ensure_output_dir


def load_tracking_data(csv_path: str | Path, fps: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["is_valid"] = df["ball_x"].notna()
    valid_df = df[df["is_valid"]].copy().sort_values("frame")

    if valid_df.empty:
        raise ValueError("Invalid video data: no target detected.")

    valid_df["frame_diff"] = valid_df["frame"].diff()
    valid_df["chunk_id"] = (valid_df["frame_diff"] > max(3, fps * 0.01)).cumsum()
    valid_df = valid_df.fillna({"frame_diff": 1})
    return valid_df.reset_index(drop=True)


def apply_smoothing(series: np.ndarray, window_length: int, polyorder: int = 3) -> np.ndarray:
    if window_length % 2 == 0:
        window_length += 1
    if len(series) < window_length or window_length <= polyorder:
        return series
    pad = (window_length - 1) // 2
    padded = np.pad(series, pad, mode='edge')
    smoothed = savgol_filter(padded, window_length, polyorder)
    return smoothed[pad:pad + len(series)]


def calc_derivative(series: np.ndarray, fps: float) -> np.ndarray:
    """Compute derivative using centered differences (np.gradient)."""
    return np.gradient(series, 1.0 / fps)


def extract_kinematics(df: pd.DataFrame, fps: float) -> pd.DataFrame:
    window_size = max(5, int(fps * 0.1))
    processed_chunks: list[pd.DataFrame] = []

    for chunk_id, group in df.groupby("chunk_id"):
        if len(group) < window_size:
            continue

        cx = apply_smoothing(group["cross_x"].values, window_size)
        cy = apply_smoothing(group["cross_y"].values, window_size)
        bx = apply_smoothing(group["ball_x"].values, window_size)
        by = apply_smoothing(group["ball_y"].values, window_size)
        bw = group["ball_w"].values
        bh = group["ball_h"].values

        dx = bx - cx
        dy = by - cy
        error_px = np.hypot(dx, dy)
        is_miss = ((np.abs(dx) > (bw / 2 * 1.35)) | (np.abs(dy) > (bh / 2 * 1.35))).astype(float)

        v_tx = calc_derivative(bx, fps)
        v_ty = calc_derivative(by, fps)
        a_tx = calc_derivative(v_tx, fps)
        a_ty = calc_derivative(v_ty, fps)

        v_cx = calc_derivative(cx, fps)
        v_cy = calc_derivative(cy, fps)
        a_cx = calc_derivative(v_cx, fps)
        a_cy = calc_derivative(v_cy, fps)

        v_rel = np.hypot(v_cx - v_tx, v_cy - v_ty)
        a_rel = np.hypot(a_cx - a_tx, a_cy - a_ty)

        processed_chunks.append(
            pd.DataFrame(
                {
                    "frame": group["frame"].values,
                    "time_s": group["time_s"].values,
                    "chunk_id": chunk_id,
                    "error_px": error_px,
                    "is_miss": is_miss,
                    "speed_t": np.hypot(v_tx, v_ty),
                    "accel_t": np.hypot(a_tx, a_ty),
                    "speed_c": np.hypot(v_cx, v_cy),
                    "accel_c": np.hypot(a_cx, a_cy),
                    "v_rel": v_rel,
                    "a_rel": a_rel,
                    "cross_x": group["cross_x"].values,
                    "cross_y": group["cross_y"].values,
                    "ball_x": group["ball_x"].values,
                    "ball_y": group["ball_y"].values,
                    "ball_w": bw,
                    "ball_h": bh,
                }
            )
        )

    if not processed_chunks:
        raise ValueError("Not enough continuous tracking data to analyze.")

    return pd.concat(processed_chunks, ignore_index=True)


def evaluate_mechanics(kdf: pd.DataFrame) -> dict:
    miss_mask = kdf["is_miss"].values
    velocity_mismatch = kdf["v_rel"].values
    acceleration_mismatch = kdf["a_rel"].values
    error = kdf["error_px"].values

    total_miss_frames = np.sum(miss_mask)
    total_frames = len(kdf)
    accuracy = (1 - (total_miss_frames / total_frames)) * 100 if total_frames > 0 else 0

    if total_miss_frames > 0:
        mean_v_mismatch = np.dot(miss_mask, velocity_mismatch) / total_miss_frames
        mean_a_mismatch = np.dot(miss_mask, acceleration_mismatch) / total_miss_frames
        mean_e_mismatch = np.dot(miss_mask, error) / total_miss_frames
        ptc = mean_a_mismatch / max(mean_e_mismatch, 1.0)
    else:
        mean_v_mismatch = 0.0
        mean_a_mismatch = 0.0
        ptc = 0.0

    loss_events = int((kdf["is_miss"].diff() > 0).sum())
    inferred_fps = 1 / kdf["time_s"].diff().mean() if total_frames > 1 else 60
    total_off_time = total_miss_frames / inferred_fps

    return {
        "accuracy": round(float(accuracy), 1),
        "speed_mismatch": round(float(mean_v_mismatch), 1),
        "accel_mismatch": round(float(mean_a_mismatch), 0),
        "ptc": round(float(ptc), 1),
        "avg_error_px": round(float(np.mean(error)), 2),
        "loss_count": loss_events,
        "total_off_time": round(float(total_off_time), 2),
    }


def export_analysis(kdf: pd.DataFrame, metrics: dict, output_dir: Path = OUTPUT_DIR) -> dict:
    output_dir = ensure_output_dir(output_dir)
    kdf = kdf.copy()
    kdf["on_target"] = 1 - kdf["is_miss"]
    kdf.to_csv(output_dir / "frame_errors.csv", index=False)

    export_format = {
        "tension": {
            "avg_error_px": float(metrics["avg_error_px"]),
            "speed_mismatch": float(metrics["speed_mismatch"]),
            "accel_mismatch": float(metrics["accel_mismatch"]),
            "ptc": float(metrics["ptc"]),
        },
        "loss": {
            "on_target_pct": float(metrics["accuracy"]),
            "loss_count": int(metrics["loss_count"]),
            "total_off_time": float(metrics["total_off_time"]),
        },
    }

    with open(output_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(export_format, f, indent=2)

    return export_format


def _print_report(metrics: dict) -> None:
    """Print analysis results to terminal."""
    print("\n" + "═" * 70)
    print("  KovaaK Physics Measurement")
    print("═" * 70)
    print("\n[ 🌊 Physics Metrics ]")
    print(f"  Pure Tension Coeff (PTC) : {metrics['ptc']:>8.1f} Hz²")
    print(f"  Speed Mismatch           : {metrics['speed_mismatch']:>8.1f} px/s")
    print(f"  Accel Mismatch           : {metrics['accel_mismatch']:>8.0f} px/s²")
    print(f"  Global Spatial Error     : {metrics['avg_error_px']:>8.1f} px")
    print("\n[ 🎯 Hit Performance ]")
    print(f"  Accuracy (On-Target)     : {metrics['accuracy']:>8.1f} %")
    print(f"  Loss Count / Duration    : {metrics['loss_count']:>8} times / {metrics['total_off_time']:.2f} s")
    print("═" * 70 + "\n")


def run_analysis(csv_path: str | Path, fps: float | None = None, output_dir: Path = OUTPUT_DIR) -> tuple[pd.DataFrame, dict, dict]:
    if fps is None:
        config_path = Path("output/calib_config.json")
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            fps = config["fps"]
        else:
            print("ERROR: FPS not specified and output/calib_config.json not found.")
            print("Pass fps= explicitly or run app.py calibration first.")
            sys.exit(1)

    df = load_tracking_data(csv_path, fps)
    kdf = extract_kinematics(df, fps)
    metrics = evaluate_mechanics(kdf)
    _print_report(metrics)
    dashboard_metrics = export_analysis(kdf, metrics, output_dir)
    return kdf, metrics, dashboard_metrics

