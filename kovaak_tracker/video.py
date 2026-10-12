from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoMetadata:
    fps: float
    width: int
    height: int
    frame_count: int


def save_uploaded_video(uploaded_file: BinaryIO, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.read())
        return tmp.name


def get_video_metadata(video_path: str | Path) -> VideoMetadata:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            raise ValueError(
                f"Invalid video dimensions ({width}x{height}) for {video_path}; "
                f"file may be corrupted or codec unsupported."
            )
        # [rb P1-1 2026-10-11] 旧实现 fps=0/非有限值静默兜 60：px/s 速度与
        # SPARC 频域全部静默错数且无任何标注，违反「降级必须可观测」合同。
        # 与上方宽高校验同口径 fail-closed：容器读不出 fps 即按损坏输入失败，
        # 由调用方的既有 typed 错误路径呈现，不再产出看似正常的时间基错数。
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0.0:
            raise ValueError(
                f"Invalid video fps ({fps}) for {video_path}; file may be "
                f"corrupted or its container does not declare a frame rate."
            )
        return VideoMetadata(
            fps=fps,
            width=width,
            height=height,
            frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def read_frame(
    video_path: str | Path,
    frame_idx: int,
    cap: cv2.VideoCapture | None = None,
) -> np.ndarray:
    """Read a single frame from a video file.

    Args:
        video_path: Path to video file.
        cap: Optional pre-opened VideoCapture (caller manages lifecycle).
             If None, opens and closes one internally.
    """
    own_cap = cap is None
    if own_cap:
        cap = cv2.VideoCapture(str(video_path))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Cannot read frame {frame_idx} from {video_path}")
        return frame
    finally:
        if own_cap:
            cap.release()

