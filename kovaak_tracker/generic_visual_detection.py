"""Untrained generic target detection: color hypotheses + three-shape prior.

KovaaK targets come in exactly three shapes (sphere, vertical capsule,
humanoid), so a closed contour-feature classifier replaces any learned
detector. Target color varies by skin but is constant within one scenario,
so the first sampled frames enumerate a small hypothesis set — dominant
saturated hue, dark cluster, bright low-saturation cluster, low-saturation
dark cluster — and the hypothesis whose detections are count-sane,
size-sane and shape-consistent wins. Every hypothesis failing those tests
fails closed; see docs proposal static-cv-pipeline-proposal-2026-08-16 §3.1.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import cv2
import numpy as np

from .vision import _apply_morphology, _make_mask

GENERIC_VISUAL_DETECTOR_VERSION = "generic_static_clicking.v1"

# Three-shape signatures (medians from the four reviewed real scenarios;
# proposal §3.1.0). Capsule is checked first because its window overlaps the
# humanoid one; the capsule's higher rectangularity separates the two.
SHAPE_SIGNATURES = {
    "sphere": {"aspect": (0.7, 1.6), "circularity": 0.60, "fill": 0.55},
    "capsule": {"aspect": (0.35, 0.75), "circularity": 0.55, "fill": 0.65},
    "humanoid": {"aspect": (0.35, 1.6), "circularity": 0.30, "fill": 0.0,
                 "min_area": 400.0},
}

# Hypothesis acceptance: enough classified evidence, sane counts and sizes.
# Frame coverage is deliberately NOT a gate — single-target short-lived
# scenarios sit at ~9% while detecting perfectly (proposal §3.1.1).
MIN_DETECTIONS = 8
MIN_SHAPE_CONSISTENCY = 0.8
MEDIAN_COUNT_RANGE = (1, 16)
MEDIAN_AREA_RANGE = (30.0, 0.02)
HUE_PEAK_MIN_PIXEL_SHARE = 0.05 / 100.0
HUE_PEAK_MIN_BIN_SHARE = 0.4
HUE_PEAK_HALF_WINDOW = 10
MAX_BLOB_WIDTH_RATIO = 0.5
# Crosshair-exemption fallback: the approach smear leaves only a handful of
# raw mask pixels at the viewport center, and the 5x5 opening wipes them.
# Real-scenario probes measured 20-160 surviving pixels at kill frames.
CENTER_FRAGMENT_ROI_PX = 120
CENTER_FRAGMENT_MIN_AREA = 15.0


def classify_target_shape(
    *, aspect: float, fill: float, circularity: float, area: float,
) -> str | None:
    """Map one blob's contour features onto the closed three-shape set."""
    capsule = SHAPE_SIGNATURES["capsule"]
    if (
        capsule["aspect"][0] <= aspect <= capsule["aspect"][1]
        and circularity >= capsule["circularity"]
        and fill >= capsule["fill"]
    ):
        return "capsule"
    sphere = SHAPE_SIGNATURES["sphere"]
    if (
        sphere["aspect"][0] <= aspect <= sphere["aspect"][1]
        and circularity >= sphere["circularity"]
        and fill >= sphere["fill"]
    ):
        return "sphere"
    humanoid = SHAPE_SIGNATURES["humanoid"]
    if (
        humanoid["aspect"][0] <= aspect <= humanoid["aspect"][1]
        and circularity >= humanoid["circularity"]
        and area >= humanoid["min_area"]
        and (circularity < sphere["circularity"] or aspect < sphere["aspect"][0])
    ):
        return "humanoid"
    return None


def enumerate_color_hypotheses(
    sample_frames: Sequence[np.ndarray],
) -> list[dict]:
    """Build the fixed clusters plus the frame-derived saturated hue peak."""
    hypotheses: list[dict] = [
        {
            "name": "dark_cluster",
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 255, 80],
            "morphology": "open_close",
            "min_area": 40.0,
            "max_area_ratio": 0.05,
        },
        {
            "name": "bright_low_saturation",
            "hsv_lower": [0, 0, 200],
            "hsv_upper": [179, 60, 255],
            "morphology": "open_close",
            "min_area": 40.0,
            "max_area_ratio": 0.05,
        },
        {
            "name": "low_saturation_dark",
            "hsv_lower": [0, 0, 0],
            "hsv_upper": [179, 60, 90],
            "morphology": "none",
            "min_area": 40.0,
            "max_area_ratio": 0.05,
        },
    ]
    saturated_hues: list[np.ndarray] = []
    total_pixels = 0
    for frame in sample_frames:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        total_pixels += hsv.shape[0] * hsv.shape[1]
        saturated = hsv[
            (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100) & (hsv[:, :, 2] <= 245)
        ]
        saturated_hues.append(saturated[:, 0])
    hues = np.concatenate(saturated_hues) if saturated_hues else np.array([])
    if total_pixels and hues.size >= HUE_PEAK_MIN_PIXEL_SHARE * total_pixels:
        histogram = np.bincount(hues, minlength=180)
        peak_hue = int(np.argmax(histogram))
        if histogram[peak_hue] >= HUE_PEAK_MIN_BIN_SHARE * hues.size:
            hypotheses.insert(0, {
                "name": "saturated_hue_peak",
                "hsv_lower": [
                    (peak_hue - HUE_PEAK_HALF_WINDOW) % 180, 100, 100,
                ],
                "hsv_upper": [
                    (peak_hue + HUE_PEAK_HALF_WINDOW) % 180, 255, 245,
                ],
                "morphology": "open_close",
                "min_area": 40.0,
                "max_area_ratio": 0.05,
            })
    return hypotheses


def detect_generic_targets(
    frame: np.ndarray,
    hypothesis: Mapping[str, object],
    *,
    crosshair_exemption: bool = False,
) -> dict:
    """Run one color hypothesis over one frame with shape classification.

    Wide flat components (HUD bars) are rejected by width; everything else
    carries its contour features and three-shape class so callers can gate.
    With ``crosshair_exemption`` a blob whose box covers the frame center is
    kept as ``shape="degraded"`` even when blurred out of every signature —
    the crosshair-covered component is by definition the aimed target (the
    crosshair is the viewport center and HUD never covers it), and the
    approach flick smears exactly that target.
    """
    hsv_lower = np.asarray(hypothesis["hsv_lower"], dtype=np.uint8)
    hsv_upper = np.asarray(hypothesis["hsv_upper"], dtype=np.uint8)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    raw_mask = _make_mask(hsv, hsv_lower, hsv_upper)
    if hypothesis["morphology"] == "open_close":
        mask = _apply_morphology(raw_mask)
    else:
        mask = raw_mask
    height, width = frame.shape[:2]
    center_x = width / 2.0
    center_y = height / 2.0
    max_area = width * height * float(hypothesis["max_area_ratio"])
    min_area = float(hypothesis["min_area"])
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    targets: list[dict] = []
    rejected: list[str] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        box_x, box_y, box_w, box_h = cv2.boundingRect(contour)
        w, h = box_w, box_h
        if area < min_area or area > max_area or h == 0 or w == 0:
            rejected.append("area")
            continue
        covers_center = (
            box_x <= center_x <= box_x + box_w
            and box_y <= center_y <= box_y + box_h
        )
        if w > MAX_BLOB_WIDTH_RATIO * width and not covers_center:
            rejected.append("hud_width")
            continue
        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            4.0 * math.pi * area / (perimeter * perimeter) if perimeter else 0.0
        )
        fill = area / float(w * h)
        aspect = w / float(h)
        shape = classify_target_shape(
            aspect=aspect, fill=fill, circularity=circularity, area=area,
        )
        if shape is None:
            if crosshair_exemption and covers_center:
                shape = "degraded"
            else:
                rejected.append("shape")
                continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            rejected.append("degenerate")
            continue
        targets.append({
            "x": float(moments["m10"] / moments["m00"]),
            "y": float(moments["m01"] / moments["m00"]),
            "visible_radius": math.sqrt(area / math.pi),
            "width": int(w),
            "height": int(h),
            "area": area,
            "aspect": aspect,
            "fill": fill,
            "circularity": circularity,
            "shape": shape,
            "confidence": min(1.0, max(0.0, circularity)),
        })
    targets.sort(key=lambda item: (item["x"], item["y"], item["visible_radius"]))
    if (
        crosshair_exemption
        and not any(
            target["x"] - target["width"] / 2.0 <= center_x
            <= target["x"] + target["width"] / 2.0
            and target["y"] - target["height"] / 2.0 <= center_y
            <= target["y"] + target["height"] / 2.0
            for target in targets
        )
    ):
        fragment = _center_fragment_from_raw_mask(
            raw_mask,
            center_x=center_x,
            center_y=center_y,
            max_area=max_area,
        )
        if fragment is not None:
            targets.append(fragment)
            targets.sort(
                key=lambda item: (item["x"], item["y"], item["visible_radius"]),
            )
    return {"targets": targets, "rejected": rejected}


def _center_fragment_from_raw_mask(
    raw_mask: np.ndarray,
    *,
    center_x: float,
    center_y: float,
    max_area: float,
) -> dict | None:
    """Recover the smeared aimed target from the pre-morphology mask.

    The opening that cleans stationary targets also deletes the approach
    smear, so the crosshair fallback works on the raw mask inside a small
    center ROI and only accepts the component covering the exact center.
    """
    height, width = raw_mask.shape[:2]
    left = max(0, int(center_x) - CENTER_FRAGMENT_ROI_PX // 2)
    right = min(width, int(center_x) + CENTER_FRAGMENT_ROI_PX // 2)
    top = max(0, int(center_y) - CENTER_FRAGMENT_ROI_PX // 2)
    bottom = min(height, int(center_y) + CENTER_FRAGMENT_ROI_PX // 2)
    roi = raw_mask[top:bottom, left:right]
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        roi, connectivity=8,
    )
    local_cx = int(center_x) - left
    local_cy = int(center_y) - top
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < CENTER_FRAGMENT_MIN_AREA or area > max_area:
            continue
        box_x = stats[index, cv2.CC_STAT_LEFT]
        box_y = stats[index, cv2.CC_STAT_TOP]
        box_w = stats[index, cv2.CC_STAT_WIDTH]
        box_h = stats[index, cv2.CC_STAT_HEIGHT]
        if not (
            box_x <= local_cx < box_x + box_w
            and box_y <= local_cy < box_y + box_h
        ):
            continue
        return {
            "x": float(centroids[index][0]) + left,
            "y": float(centroids[index][1]) + top,
            "visible_radius": math.sqrt(area / math.pi),
            "width": int(box_w),
            "height": int(box_h),
            "area": area,
            "aspect": box_w / float(box_h) if box_h else 1.0,
            "fill": area / float(max(1, box_w * box_h)),
            "circularity": 0.0,
            "shape": "degraded",
            "confidence": 0.0,
        }
    return None


def score_color_hypothesis(
    sample_frames: Sequence[np.ndarray],
    hypothesis: Mapping[str, object],
) -> dict:
    """Score one hypothesis by count sanity, size sanity, shape consistency."""
    per_frame_counts: list[int] = []
    areas: list[float] = []
    shapes: list[str] = []
    frames_with_target = 0
    for frame in sample_frames:
        result = detect_generic_targets(frame, hypothesis)
        targets = result["targets"]
        per_frame_counts.append(len(targets))
        if targets:
            frames_with_target += 1
        for target in targets:
            areas.append(target["area"])
            shapes.append(target["shape"])
    detection_count = len(shapes)
    if not shapes:
        return {
            "hypothesis": dict(hypothesis),
            "detection_count": 0,
            "frame_coverage": 0.0,
            "median_blob_count": 0.0,
            "median_area": 0.0,
            "shape": None,
            "shape_consistency": 0.0,
            "passes": False,
            "rejections": [
                "no_shape_classified_detections",
            ],
        }
    modal_shape = max(set(shapes), key=shapes.count)
    consistency = shapes.count(modal_shape) / len(shapes)
    median_count = float(np.median(per_frame_counts))
    median_area = float(np.median(areas))
    height, width = sample_frames[0].shape[:2]
    median_area_limit = MEDIAN_AREA_RANGE[1] * width * height
    rejections: list[str] = []
    if detection_count < MIN_DETECTIONS:
        rejections.append("insufficient_detections")
    if not (
        MEDIAN_COUNT_RANGE[0] <= median_count <= MEDIAN_COUNT_RANGE[1]
    ):
        rejections.append("median_blob_count_out_of_range")
    if not (
        MEDIAN_AREA_RANGE[0] <= median_area <= median_area_limit
    ):
        rejections.append("median_area_out_of_range")
    if consistency < MIN_SHAPE_CONSISTENCY:
        rejections.append("shape_consistency_below_threshold")
    return {
        "hypothesis": dict(hypothesis),
        "detection_count": detection_count,
        "frame_coverage": frames_with_target / len(sample_frames),
        "median_blob_count": median_count,
        "median_area": median_area,
        "shape": modal_shape,
        "shape_consistency": consistency,
        "passes": not rejections,
        "rejections": rejections,
    }


def select_color_hypothesis(
    sample_frames: Sequence[np.ndarray],
) -> dict | None:
    """Pick the best passing hypothesis or fail closed with None."""
    if not len(sample_frames):
        return None
    scored = [
        score_color_hypothesis(sample_frames, hypothesis)
        for hypothesis in enumerate_color_hypotheses(sample_frames)
    ]
    passing = [score for score in scored if score["passes"]]
    if not passing:
        return None
    passing.sort(
        key=lambda score: (
            score["shape_consistency"],
            score["frame_coverage"],
            score["detection_count"],
        ),
        reverse=True,
    )
    return {
        "detector_version": GENERIC_VISUAL_DETECTOR_VERSION,
        **passing[0],
        "considered": [
            {
                "name": score["hypothesis"]["name"],
                "passes": score["passes"],
                "rejections": score["rejections"],
            }
            for score in scored
        ],
    }


__all__ = [
    "GENERIC_VISUAL_DETECTOR_VERSION",
    "SHAPE_SIGNATURES",
    "classify_target_shape",
    "detect_generic_targets",
    "enumerate_color_hypotheses",
    "score_color_hypothesis",
    "select_color_hypothesis",
]
