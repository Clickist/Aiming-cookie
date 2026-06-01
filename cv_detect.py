"""
cv_detect.py - Shared CV detection module for Tension-Aware-Aim-Analyzer.

Provides color-based object detection functions used by both the calibration
pipeline (app.py) and the command-line calibrator (calibrate.py). Centralizes
HSV range generation, ball detection, and crosshair detection so that fixes
and tuning parameters live in one place.

Dependencies: numpy, opencv-python (cv2)
"""

import numpy as np
import cv2


def get_hsv_range(bgr_color):
    """Generate an adaptive HSV range from a sampled BGR color.

    The range adapts to three luminance/saturation regimes:
      - Dark objects (v < 40): wide H and S, capped V upper bound.
      - Bright unsaturated objects (s < 30, v > 200): wide H, low S cap.
      - Standard colored objects: symmetric H/S/V bands.

    For the standard-color branch, if the H-channel range would wrap around
    0/179, the returned hi H value will be *lower* than lo H. Callers that
    pass both arrays into cv2.inRange should detect this (hsv_lo[0] > hsv_hi[0])
    and create two masks (one low-wrap, one high-wrap) that are OR'd together.

    Args:
        bgr_color: A length-3 sequence (B, G, R) representing the sampled color.

    Returns:
        (hsv_lo, hsv_hi): Two numpy arrays of shape (3,), dtype uint8,
        suitable for cv2.inRange. Note that hsv_lo[0] may exceed hsv_hi[0]
        when H-channel wraparound occurs.
    """
    hsv = cv2.cvtColor(np.uint8([[bgr_color]]), cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])

    if v < 40:
        # Dark objects: generous S range, V capped at v+25
        return np.array([0, 0, 0]), np.array([179, 255, v + 25])
    elif s < 30 and v > 200:
        # Bright, near-white objects
        return np.array([0, 0, max(0, v - 50)]), np.array([179, 50, 255])
    else:
        # Standard colored objects: +/- 10 on H, +/- 50 on S and V
        lo_h = max(0, h - 10)
        hi_h = min(179, h + 10)
        lo = np.array([lo_h, max(0, s - 50), max(0, v - 50)])
        hi = np.array([hi_h, min(255, s + 50), min(255, v + 50)])
        return lo, hi


def _apply_morphology(mask):
    """Apply open-then-close morphological cleanup to a binary mask.

    Args:
        mask: Single-channel binary image (uint8, 0/255).

    Returns:
        Cleaned binary mask.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def _make_mask(hsv, hsv_lo, hsv_hi):
    """Create a binary mask, handling H-channel wraparound.

    When hsv_lo[0] > hsv_hi[0], the hue range wraps around 0/179.
    Two masks are created (low slice + high slice) and OR'd together.

    Args:
        hsv: HSV image (uint8).
        hsv_lo: Lower HSV bound (length-3 array).
        hsv_hi: Upper HSV bound (length-3 array).

    Returns:
        Binary mask (uint8, 0/255).
    """
    if hsv_lo[0] > hsv_hi[0]:
        # H-channel wraparound: split into two ranges
        mask_lo = cv2.inRange(hsv,
                              np.array([0, hsv_lo[1], hsv_lo[2]]),
                              np.array([hsv_hi[0], hsv_hi[1], hsv_hi[2]]))
        mask_hi = cv2.inRange(hsv,
                              np.array([hsv_lo[0], hsv_lo[1], hsv_lo[2]]),
                              np.array([179, hsv_hi[1], hsv_hi[2]]))
        return cv2.bitwise_or(mask_lo, mask_hi)
    else:
        return cv2.inRange(hsv, hsv_lo, hsv_hi)


def detect_ball_by_color(frame, hsv_lo, hsv_hi):
    """Detect a target ball by color masking and contour analysis.

    Algorithm:
      1. Convert frame to HSV and build a binary mask (with H wraparound).
      2. Morphological open+close to remove noise.
      3. Find external contours.
      4. Filter by area (50 -- 5% of frame pixels), aspect ratio (0.7--1.3),
         and sky deadzone (top 12% of frame, width > 60px).
      5. Among valid contours, pick the one whose centroid is closest to the
         frame center.

    Args:
        frame: BGR image (numpy array, uint8).
        hsv_lo: Lower HSV bound (length-3 array).
        hsv_hi: Upper HSV bound (length-3 array).

    Returns:
        (pos, w, h) where pos is (cx, cy) tuple, or (None, None, None) if
        no valid contour is found.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = _make_mask(hsv, hsv_lo, hsv_hi)
    mask = _apply_morphology(mask)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None, None

    h_img, w_img = frame.shape[:2]
    center_x, center_y = w_img // 2, h_img // 2
    max_valid_area = w_img * h_img * 0.05  # 5% of frame

    best_pos, best_w, best_h = None, None, None
    min_dist = float('inf')

    for c in contours:
        area = cv2.contourArea(c)
        if area < 50 or area > max_valid_area:
            continue

        _, _, w, h = cv2.boundingRect(c)
        if h == 0:
            continue

        # Bidirectional aspect ratio filter
        aspect_ratio = w / float(h)
        if not (0.7 < aspect_ratio < 1.3):
            continue

        M = cv2.moments(c)
        if M["m00"] == 0:
            continue

        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])

        # Sky deadzone: top 12% of frame, wide blobs are likely false positives
        if cy < h_img * 0.12 and w > 60:
            continue

        dist = (cx - center_x) ** 2 + (cy - center_y) ** 2
        if dist < min_dist:
            min_dist = dist
            best_pos = (cx, cy)
            best_w, best_h = w, h

    return best_pos, best_w, best_h


def detect_crosshair_by_color(frame, hsv_lo, hsv_hi):
    """Detect a crosshair by color masking and contour analysis.

    A simpler variant of detect_ball_by_color tailored for crosshair shapes:
      - Minimum area lowered to 5px (crosshairs are small).
      - No sky deadzone filter (crosshairs can be anywhere).
      - No aspect ratio filter (crosshairs can be thin vertical/horizontal lines).

    Args:
        frame: BGR image (numpy array, uint8).
        hsv_lo: Lower HSV bound (length-3 array).
        hsv_hi: Upper HSV bound (length-3 array).

    Returns:
        (pos, w, h) where pos is (cx, cy) tuple, or (None, None, None) if
        no valid contour is found.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = _make_mask(hsv, hsv_lo, hsv_hi)
    mask = _apply_morphology(mask)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None, None

    h_img, w_img = frame.shape[:2]
    center_x, center_y = w_img // 2, h_img // 2
    max_valid_area = w_img * h_img * 0.05

    best_pos, best_w, best_h = None, None, None
    min_dist = float('inf')

    for c in contours:
        area = cv2.contourArea(c)
        if area < 5 or area > max_valid_area:
            continue

        _, _, w, h = cv2.boundingRect(c)
        if h == 0:
            continue

        M = cv2.moments(c)
        if M["m00"] == 0:
            continue

        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])

        dist = (cx - center_x) ** 2 + (cy - center_y) ** 2
        if dist < min_dist:
            min_dist = dist
            best_pos = (cx, cy)
            best_w, best_h = w, h

    return best_pos, best_w, best_h
