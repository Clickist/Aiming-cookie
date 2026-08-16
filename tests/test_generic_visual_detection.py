"""Synthetic-scene tests for the untrained generic target detector."""
from __future__ import annotations

import numpy as np
import cv2

from kovaak_tracker.generic_visual_detection import (
    classify_target_shape,
    select_color_hypothesis,
)

_WIDTH, _HEIGHT = 640, 360


def _frame(bg_bgr: tuple[int, int, int]) -> np.ndarray:
    return np.full((_HEIGHT, _WIDTH, 3), bg_bgr, dtype=np.uint8)


def _draw_sphere(frame, cx, cy, radius, bgr) -> None:
    cv2.circle(frame, (cx, cy), radius, bgr, -1)


def _draw_capsule(frame, cx, cy, width, height, bgr) -> None:
    """Vertical pill: rounded caps + body, all one color."""
    half = width // 2
    top = cy - height // 2
    cv2.rectangle(frame, (cx - half, top + half), (cx + half, top + height - half), bgr, -1)
    cv2.ellipse(frame, (cx, top + half), (half, half), 0, 180, 360, bgr, -1)
    cv2.ellipse(frame, (cx, top + height - half), (half, half), 0, 0, 180, bgr, -1)


def _draw_humanoid(frame, cx, cy, bgr) -> None:
    """Chunky head + torso + legs: irregular outline, sub-sphere circularity."""
    head_r = 13
    cv2.circle(frame, (cx, cy - 40), head_r, bgr, -1)
    cv2.rectangle(frame, (cx - 22, cy - 29), (cx + 22, cy + 12), bgr, -1)
    cv2.rectangle(frame, (cx - 20, cy + 14), (cx - 5, cy + 58), bgr, -1)
    cv2.rectangle(frame, (cx + 5, cy + 14), (cx + 20, cy + 58), bgr, -1)


def test_shape_classifier_covers_the_closed_signature_set():
    assert classify_target_shape(
        aspect=1.31, fill=0.66, circularity=0.66, area=450,
    ) == "sphere"
    assert classify_target_shape(
        aspect=0.47, fill=0.70, circularity=0.60, area=1700,
    ) == "capsule"
    assert classify_target_shape(
        aspect=0.56, fill=0.55, circularity=0.52, area=1600,
    ) == "humanoid"
    assert classify_target_shape(
        aspect=4.2, fill=0.9, circularity=0.9, area=900,
    ) is None
    assert classify_target_shape(
        aspect=1.0, fill=0.4, circularity=0.9, area=3000,
    ) is None


def test_cyan_spheres_on_desaturated_background_pick_the_hue_peak():
    frames = []
    for index in range(10):
        frame = _frame((110, 130, 120))
        for cx in (120 + index, 320, 520):
            _draw_sphere(frame, cx, 180, 20, (180, 180, 40))
        frames.append(frame)

    selection = select_color_hypothesis(frames)

    assert selection is not None
    assert selection["hypothesis"]["name"] == "saturated_hue_peak"
    assert selection["shape"] == "sphere"
    assert selection["shape_consistency"] == 1.0


def test_dark_spheres_on_saturated_green_background_pick_the_dark_cluster():
    frames = []
    for index in range(10):
        frame = _frame((60, 180, 80))
        for cx in (150 + index, 480 - index):
            _draw_sphere(frame, cx, 180, 22, (25, 25, 25))
        frames.append(frame)

    selection = select_color_hypothesis(frames)

    assert selection is not None
    assert selection["hypothesis"]["name"] == "dark_cluster"
    assert selection["shape"] == "sphere"
    # The green background wins the saturation peak, so that hypothesis must
    # fail closed instead of winning on sheer pixel volume.
    considered = {
        entry["name"]: entry for entry in selection["considered"]
    }
    assert considered["saturated_hue_peak"]["passes"] is False


def test_red_humanoids_pick_the_hue_peak_with_humanoid_signature():
    frames = []
    for index in range(10):
        frame = _frame((150, 160, 150))
        for cx in (160 + index, 480 - index):
            _draw_humanoid(frame, cx, 180, (40, 40, 200))
        frames.append(frame)

    selection = select_color_hypothesis(frames)

    assert selection is not None
    assert selection["hypothesis"]["name"] == "saturated_hue_peak"
    assert selection["shape"] == "humanoid"


def test_dark_capsule_on_white_background_needs_the_low_saturation_cluster():
    frames = []
    for index in range(10):
        frame = _frame((235, 235, 235))
        # V=85: past the V<=80 dark cluster, inside the V<=90 low-saturation
        # window that carries no opening morphology (proposal §3.1.1).
        _draw_capsule(frame, 320 + index, 180, 28, 72, (85, 85, 85))
        frames.append(frame)

    selection = select_color_hypothesis(frames)

    assert selection is not None
    assert selection["hypothesis"]["name"] == "low_saturation_dark"
    assert selection["shape"] == "capsule"


def test_empty_background_fails_closed():
    frames = [_frame((110, 130, 120)) for _ in range(10)]

    assert select_color_hypothesis(frames) is None
