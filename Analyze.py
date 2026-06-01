"""
kovaak_tracker/analyze.py
==========================
Step 7.1: Pure Tension Coefficient (PTC) - Raw Measurement Edition
Original Theory & Mathematics by: Jianrui (Jerry) Zhang

Objective:
    - Pure data collection and measurement.
    - NO arbitrary thresholds, NO subjective scoring, NO diagnosis.
    - Establishes the groundwork for empirical data analysis.
"""

import argparse
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.signal import savgol_filter

def load_data(csv_path: str, fps: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df['is_valid'] = df['ball_x'].notna()
    valid_df = df[df['is_valid']].copy().sort_values("frame")
    
    if valid_df.empty:
        raise ValueError("No valid target data detected in the video!")
        
    valid_df['frame_diff'] = valid_df['frame'].diff()
    valid_df['chunk_id'] = (valid_df['frame_diff'] > max(3, fps * 0.01)).cumsum()
    valid_df = valid_df.fillna({'frame_diff': 1})
    return valid_df.reset_index(drop=True)

def apply_smoothing(series: np.ndarray, window_length: int, polyorder: int = 3) -> np.ndarray:
    if window_length % 2 == 0: window_length += 1
    if len(series) < window_length:
        print(f"  WARNING: chunk length ({len(series)}) < window ({window_length}), skipping smoothing")
        return series
    pad = (window_length - 1) // 2
    padded = np.pad(series, pad, mode='edge')
    smoothed = savgol_filter(padded, window_length, polyorder)
    return smoothed[pad:pad + len(series)]

def calculate_vector_kinematics(x: np.ndarray, y: np.ndarray, fps: float) -> tuple:
    """Compute acceleration using centered differences (np.gradient)."""
    vx = np.gradient(x, 1.0 / fps)
    vy = np.gradient(y, 1.0 / fps)
    ax = np.gradient(vx, 1.0 / fps)
    ay = np.gradient(vy, 1.0 / fps)
    return ax, ay

def run_tension_analysis(df: pd.DataFrame, fps: float) -> dict:
    window_sz = max(5, int(fps * 0.1)) 
    
    all_E = []          
    all_a_rel = []    
    all_is_loss = []    
    
    for _, group in df.groupby('chunk_id'):
        if len(group) < window_sz: continue
            
        cx = apply_smoothing(group["cross_x"].values, window_sz)
        cy = apply_smoothing(group["cross_y"].values, window_sz)
        bx = apply_smoothing(group["ball_x"].values, window_sz)
        by = apply_smoothing(group["ball_y"].values, window_sz)
        
        bw = group["ball_w"].values
        bh = group["ball_h"].values

        dx, dy = bx - cx, by - cy
        dist_E = np.hypot(dx, dy)
        is_loss = (np.abs(dx) > (bw / 2 * 1.05)) | (np.abs(dy) > (bh / 2 * 1.05))
        
        ax_target, ay_target = calculate_vector_kinematics(bx, by, fps)
        ax_cross, ay_cross = calculate_vector_kinematics(cx, cy, fps)
        
        a_rel_mag = np.hypot(ax_cross - ax_target, ay_cross - ay_target)

        all_E.extend(dist_E)
        all_a_rel.extend(a_rel_mag)
        all_is_loss.extend(is_loss)

    E_array = np.array(all_E)
    a_rel_array = np.array(all_a_rel)
    loss_mask = np.array(all_is_loss)
    
    critical_E = E_array[loss_mask]
    critical_a_rel = a_rel_array[loss_mask]

    # 🚀 纯粹计算，不加任何评价
    if len(critical_E) == 0: 
        mean_E = float(np.mean(E_array)) # 如果完全没脱靶，用全局误差兜底
        mean_a_rel = 0.0
        ptc = 0.0
    else:
        mean_E = max(float(np.mean(critical_E)), 1.0)
        mean_a_rel = float(np.mean(critical_a_rel))
        ptc = mean_a_rel / mean_E # 物理刚度计算

    return {
        "avg_error_px": round(float(np.mean(E_array)), 2),
        "ptc":          round(ptc, 1),
        "mean_a_rel":   round(mean_a_rel, 0)
    }

def summarize(res: dict, los: dict) -> None:
    print("\n" + "═" * 70)
    print(f"  KovaaK Physics Measurement (Raw Data Edition)")
    print("═" * 70)
    
    print("\n[ 🌊 Physics Metrics (Calculated on Loss-Frames) ]")
    print(f"  Pure Tension Coeff (PTC) : {res['ptc']:>8.1f} Hz²  (Stiffness)")
    print(f"  Mean Relative Accel      : {res['mean_a_rel']:>8.0f} px/s²")
    print(f"  Global Spatial Error     : {res['avg_error_px']:>8.1f} px")
    
    print("\n[ 🎯 Hit Performance ]")
    print(f"  Accuracy (On-Target)     : {los['on_target_pct']:>8.1f} %")
    print(f"  Loss Count / Duration    : {los['loss_count']:>8} times / {los['total_off_time']:.2f} s")
    print("═" * 70 + "\n")

def loss_metrics(df: pd.DataFrame, fps: float) -> dict:
    dx = np.abs(df["cross_x"] - df["ball_x"])
    dy = np.abs(df["cross_y"] - df["ball_y"])
    on_target = (dx <= (df["ball_w"] / 2 * 1.05)) & (dy <= (df["ball_h"] / 2 * 1.05))
    on_target = on_target.values 
    
    segments = []
    if len(on_target) > 0:
        curr = on_target[0]
        cnt = 1
        for i in range(1, len(on_target)):
            if on_target[i] == curr: cnt += 1
            else:
                segments.append((curr, cnt))
                curr = on_target[i]; cnt = 1
        segments.append((curr, cnt))

    off_dur = [c/fps for s, c in segments if not s]
    return {
        "loss_count":     len(off_dur),
        "total_off_time": round(sum(off_dur), 2),
        "on_target_pct":  round(float(on_target.mean() * 100), 1),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--fps", type=float, default=None, help="Video FPS (auto-detected from calib_config.json if not specified)")
    args = parser.parse_args()

    if args.fps is None:
        config_path = Path("output/calib_config.json")
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            args.fps = config["fps"]
            print(f"FPS auto-detected from calib_config.json: {args.fps}")
        else:
            print("ERROR: FPS not specified and output/calib_config.json not found.")
            print("Run with --fps <value> or run app.py calibration first.")
            sys.exit(1)

    df = load_data(args.csv, args.fps)
    res = run_tension_analysis(df, args.fps)
    los = loss_metrics(df, args.fps)
    
    summarize(res, los)
    
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    df["error_px"] = np.hypot(df["ball_x"] - df["cross_x"], df["ball_y"] - df["cross_y"])
    dx = np.abs(df["cross_x"] - df["ball_x"])
    dy = np.abs(df["cross_y"] - df["ball_y"])
    df["on_target"] = ((dx <= (df["ball_w"] / 2 * 1.05)) & (dy <= (df["ball_h"] / 2 * 1.05))).astype(int)
    
    df.to_csv(out_dir / "frame_errors.csv", index=False)
    with open(out_dir / "metrics.json", "w") as f:
        json.dump({"tension": res, "loss": los}, f)

if __name__ == "__main__":
    main()