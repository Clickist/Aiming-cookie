"""
kovaak_tracker/analyze.py
==========================
CLI wrapper for the physics analysis pipeline.

Usage:
    python Analyze.py --csv output/calibration_raw.csv [--fps 360]
"""

import argparse
from kovaak_tracker.analysis import run_analysis


def main():
    parser = argparse.ArgumentParser(description="KovaaK Physics Analysis")
    parser.add_argument("--csv", required=True, help="Path to calibration_raw.csv")
    parser.add_argument("--fps", type=float, default=None,
                        help="Video FPS (auto-detected from calib_config.json if not specified)")
    args = parser.parse_args()

    run_analysis(csv_path=args.csv, fps=args.fps)


if __name__ == "__main__":
    main()
