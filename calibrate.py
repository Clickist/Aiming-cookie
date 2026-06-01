"""
kovaak_tracker/calibrate.py
============================
CLI wrapper for interactive color calibration.

Usage:
    python calibrate.py --video your_recording.mp4 [--frames 100]
"""

import argparse
from kovaak_tracker.calibration_cli import run_calibration


def main():
    parser = argparse.ArgumentParser(description="KovaaK Color Calibration")
    parser.add_argument("--video", required=True, help="Path to video file")
    parser.add_argument("--frames", type=int, default=100, help="Number of frames to verify")
    args = parser.parse_args()

    run_calibration(args.video, args.frames)


if __name__ == "__main__":
    main()
