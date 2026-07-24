from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np


Point = tuple[int, int]


def _make_mask(hsv: np.ndarray, hsv_lo: np.ndarray, hsv_hi: np.ndarray) -> np.ndarray:
    """Create a binary mask, handling H-channel wraparound.

    When hsv_lo[0] > hsv_hi[0], the hue range wraps around 0/179.
    Two masks are created (low slice + high slice) and OR'd together.
    """
    if hsv_lo[0] > hsv_hi[0]:
        mask_lo = cv2.inRange(hsv,
                              np.array([0, hsv_lo[1], hsv_lo[2]], dtype=np.uint8),
                              np.array([hsv_hi[0], hsv_hi[1], hsv_hi[2]], dtype=np.uint8))
        mask_hi = cv2.inRange(hsv,
                              np.array([hsv_lo[0], hsv_lo[1], hsv_lo[2]], dtype=np.uint8),
                              np.array([179, hsv_hi[1], hsv_hi[2]], dtype=np.uint8))
        return cv2.bitwise_or(mask_lo, mask_hi)
    return cv2.inRange(hsv, hsv_lo, hsv_hi)


def _apply_morphology(mask: np.ndarray) -> np.ndarray:
    """Apply open-then-close morphological cleanup to a binary mask."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def get_hsv_range(
    bgr_color: list[int] | tuple[int, int, int] | np.ndarray,
    is_crosshair: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Build an adaptive HSV range from a sampled BGR color."""
    hsv = cv2.cvtColor(np.uint8([[bgr_color]]), cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])

    if v < 40:
        return np.array([0, 0, 0]), np.array([179, 255, min(255, v + 50)])

    if s < 30 and v > 200:
        return np.array([0, 0, max(0, v - 50)]), np.array([179, 50, 255])

    tolerance_h = 15 if is_crosshair else 10
    tolerance_sv = 60 if is_crosshair else 50
    lo_h = h - tolerance_h
    hi_h = h + tolerance_h
    # 色相环绕:跨 0 或 179 时产生 lo_h > hi_h,触发 _make_mask 环绕双区间
    # (否则红/品红 H≈0/179 只覆盖一侧,同色目标漏检——_make_mask 环绕分支成 dead code)。
    if lo_h < 0:
        lo_h += 180
    elif hi_h > 179:
        hi_h -= 180
    return (
        np.array([lo_h, max(0, s - tolerance_sv), max(0, v - tolerance_sv)]),
        np.array([hi_h, min(255, s + tolerance_sv), min(255, v + tolerance_sv)]),
    )


def detect_point_by_color(
    frame: np.ndarray,
    hsv_lo: np.ndarray,
    hsv_hi: np.ndarray,
    *,
    min_area: float = 50,
    max_area_ratio: float = 0.05,
    max_aspect_ratio: float | None = None,
    ignore_top_ui: bool = False,
    ignore_bottom_ui: bool = False,
) -> tuple[Point | None, int | None, int | None]:
    """Find the color blob closest to the screen center."""
    candidates = detect_color_blobs(
        frame,
        hsv_lo,
        hsv_hi,
        min_area=min_area,
        max_area_ratio=max_area_ratio,
        max_aspect_ratio=max_aspect_ratio,
        ignore_top_ui=ignore_top_ui,
        ignore_bottom_ui=ignore_bottom_ui,
    )
    if not candidates:
        return None, None, None

    h_img, w_img = frame.shape[:2]
    center_x, center_y = w_img // 2, h_img // 2
    best = min(
        candidates,
        key=lambda item: (
            (item["x"] - center_x) ** 2 + (item["y"] - center_y) ** 2,
            item["x"],
            item["y"],
        ),
    )
    return (
        (int(best["x"]), int(best["y"])),
        int(best["width"]),
        int(best["height"]),
    )


def detect_color_blobs(
    frame: np.ndarray,
    hsv_lo: np.ndarray,
    hsv_hi: np.ndarray,
    *,
    min_area: float = 50,
    max_area_ratio: float = 0.05,
    max_aspect_ratio: float | None = None,
    ignore_top_ui: bool = False,
    ignore_bottom_ui: bool = False,
    min_circularity: float | None = None,
    include_contours: bool = False,
) -> list[dict[str, object]]:
    """Return every color blob accepted by the shared legacy vision filters."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = _make_mask(hsv, hsv_lo, hsv_hi)
    mask = _apply_morphology(mask)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h_img, w_img = frame.shape[:2]
    max_valid_area = w_img * h_img * max_area_ratio
    candidates: list[dict[str, object]] = []

    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_valid_area:
            continue

        _, _, width, height = cv2.boundingRect(contour)
        if height == 0:
            continue

        if max_aspect_ratio is not None:
            aspect_ratio = width / float(height)
            if not (1.0 / max_aspect_ratio < aspect_ratio < max_aspect_ratio):
                continue

        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue

        cx = int(moments["m10"] / moments["m00"])
        cy = int(moments["m01"] / moments["m00"])

        if ignore_top_ui and cy < h_img * 0.12 and width > 60:
            continue
        if ignore_bottom_ui and cy > h_img * 0.88:
            continue

        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            4.0 * math.pi * area / (perimeter * perimeter)
            if perimeter
            else 0.0
        )
        if min_circularity is not None and circularity < min_circularity:
            continue
        candidate: dict[str, object] = {
            "x": float(moments["m10"] / moments["m00"]),
            "y": float(moments["m01"] / moments["m00"]),
            "width": int(width),
            "height": int(height),
            "area": area,
            "visible_radius": math.sqrt(area / math.pi),
            "circularity": circularity,
            "confidence": (
                min(1.0, max(0.0, circularity))
                if min_circularity is not None
                else 1.0
            ),
        }
        if include_contours:
            candidate["_contour"] = contour.copy()
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (item["x"], item["y"], item["visible_radius"])
    )
    return candidates


def detect_ball_by_color(
    frame: np.ndarray,
    hsv_lo: np.ndarray,
    hsv_hi: np.ndarray,
) -> tuple[Point | None, int | None, int | None]:
    """Detect the target ball with stricter filtering than a generic color blob."""
    return detect_point_by_color(
        frame,
        hsv_lo,
        hsv_hi,
        min_area=50,
        max_area_ratio=0.05,
        max_aspect_ratio=1.3,
        ignore_top_ui=True,
        ignore_bottom_ui=True,
    )


def detect_crosshair_by_color(
    frame: np.ndarray,
    hsv_lo: np.ndarray,
    hsv_hi: np.ndarray,
) -> tuple[Point | None, int | None, int | None]:
    """Detect a crosshair by color masking and contour analysis.

    A simpler variant tailored for crosshair shapes:
      - Minimum area lowered to 5px (crosshairs are small).
      - No sky deadzone filter (crosshairs can be anywhere).
      - No aspect ratio filter (crosshairs can be thin vertical/horizontal lines).
    """
    return detect_point_by_color(
        frame,
        hsv_lo,
        hsv_hi,
        min_area=5,
        max_area_ratio=0.05,
    )


def get_tracker(warn_callback: Callable[[str], None] | None = None):
    """Create the preferred OpenCV tracker, falling back when contrib APIs are absent."""
    try:
        return cv2.TrackerCSRT_create()
    except AttributeError:
        if warn_callback is not None:
            warn_callback("CSRT tracker not found; falling back to KCF. Install opencv-contrib-python for CSRT.")
        return cv2.TrackerKCF_create()


def frame_to_rgb(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def sample_median_bgr(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> list[int]:
    template = frame[int(y1) : int(y2), int(x1) : int(x2)]
    median_color = np.median(template.reshape(-1, 3), axis=0).astype(np.uint8)
    return median_color.tolist()
