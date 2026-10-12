from __future__ import annotations

import numpy as np
from scipy.signal import savgol_filter


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
    """Compute derivative using centered differences (np.gradient).

    [rb P0-2 2026-10-11] np.gradient 对长度 <2 的数组抛 ValueError；空/截断
    轨迹（损坏视频 cap.read() 首帧失败 → 0 行 DataFrame）在旧实现里从这里
    炸穿整局作业。守卫前移：<2 样本按零导数返回（调用方各自的空轨迹守卫
    随后接管），>=2 样本数值逐位不变。
    """
    if series.size < 2:
        return np.zeros_like(series, dtype=float)
    return np.gradient(series, 1.0 / fps)
