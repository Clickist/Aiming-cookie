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

# Hypothesis acceptance: enough classified evidence, sane sizes, consistent
# shape. Frame coverage and per-frame presence are deliberately NOT gates —
# single-target short-lived scenarios (switching) legitimately show zero
# targets in most sampled frames (proposal §3.1.1).
MIN_DETECTIONS = 8
MIN_SHAPE_CONSISTENCY = 0.8
MAX_MEDIAN_BLOB_COUNT = 16
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


# --- Letterbox geometry (窗口漂移 replay，病灶 A3) ---------------------------
# Rust 侧对漂移后的视频帧做等比 letterbox 后继续编码进同一 mp4：内容按
# ``scale`` 缩放并放在画布 (dstX, dstY) 起的内容矩形内，其余为黑边，画布
# 尺寸恒为会话启动尺寸。这里消费 capture receipt 落盘的 ``geometryEvents``
# （camelCase），把 blob 坐标/面积从画布空间反变换回内容空间，使检测与
# 分析门槛保持全尺寸语义（坐标 (v − dst)/s、面积 ×1/scale²）。契约字段
# 缺失/不可信时一律 fail-safe 回退为“无变换”，绝不崩溃；规则见各函数
# docstring。检测侧与采样裁边侧都只消费本区实现，不复制换算逻辑。


def _letterbox_positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _letterbox_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _letterbox_event_transform(
    event: Mapping[str, object],
) -> dict | None:
    """Normalize one followed event into a content←canvas transform.

    必需字段：``srcWidth``/``srcHeight``（内容像素尺寸）、``dstX``/``dstY``
    （内容矩形左上角）、``scale``（内容缩放）。``scale`` 键缺席时可由
    dstWidth/srcWidth 或 dstHeight/srcHeight 兜底推导（字段名有出入时的
    最小容错）；``scale`` 显式提供但非法（≤0/NaN/非数值）则视为该条事件
    几何不可信，整段回退无变换。返回 None 表示该事件几何不可反转，调用
    方按恒等段处理。
    """
    src_width = _letterbox_positive_float(event.get("srcWidth"))
    src_height = _letterbox_positive_float(event.get("srcHeight"))
    dst_x = _letterbox_finite_float(event.get("dstX"))
    dst_y = _letterbox_finite_float(event.get("dstY"))
    if (
        src_width is None or src_height is None
        or dst_x is None or dst_y is None
    ):
        return None
    if "scale" in event:
        scale = _letterbox_positive_float(event.get("scale"))
        if scale is None:
            return None
    else:
        dst_width = _letterbox_positive_float(event.get("dstWidth"))
        dst_height = _letterbox_positive_float(event.get("dstHeight"))
        if dst_width is not None:
            scale = dst_width / src_width
        elif dst_height is not None:
            scale = dst_height / src_height
        else:
            return None
        if not math.isfinite(scale) or scale <= 0.0:
            return None
    return {
        "scale": scale,
        "offset_x": dst_x,
        "offset_y": dst_y,
        "src_width": src_width,
        "src_height": src_height,
    }


def build_letterbox_segments(raw_events: object) -> list[dict]:
    """Normalize receipt ``geometryEvents`` into ordered time segments.

    每个段为 ``{"canonical_ms": int, "transform": dict | None}``：
    ``transform`` 为 None 表示该时刻起无变换（``followed`` 显式 false 的
    终态化标记，或几何字段不可反转的降级段），否则内容坐标为
    ``(v − offset)/scale``、面积 ``/scale²``。

    ``followed`` 门控（以 Rust 侧实际落盘为准）：Rust ``GeometryEvent``
    只从其队列的跟随（resize）事件产生——即条目本身就意味着该时刻起内容
    被 letterbox，落盘不带 ``followed`` 字段。因此：字段**缺席**视为跟随
    （应用反变换）；显式 ``true`` 应用；显式``false``（合同定义：该时刻触发
    终态化，不产生后续内容）与任何其他非布尔值一律按无变换降级。绝不因
    合同字段缺席而崩溃或静默漏掉真实漂移。

    有序性假设是显式的：事件在此按 ``canonicalMs`` 升序排序，同一时刻的
    重复条目保留最后一个；下游 :func:`letterbox_segment_at` 依赖该升序。
    时间字段（canonicalMs，int）任一缺失/非法 → 整个列表作废并返回 []：
    无法定位生效边界时宁可全程不变换，也不能把旧变换套到新内容上。
    """
    if not isinstance(raw_events, (list, tuple)) or not raw_events:
        return []
    parsed: list[tuple[int, dict | None]] = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            return []
        canonical_ms = event.get("canonicalMs")
        if isinstance(canonical_ms, bool) or not isinstance(canonical_ms, int):
            return []
        followed = event.get("followed")
        transform = (
            _letterbox_event_transform(event)
            if followed is None or followed is True
            else None
        )
        parsed.append((canonical_ms, transform))
    parsed.sort(key=lambda item: item[0])
    segments: list[dict] = []
    for canonical_ms, transform in parsed:
        segment = {"canonical_ms": canonical_ms, "transform": transform}
        if segments and segments[-1]["canonical_ms"] == canonical_ms:
            segments[-1] = segment
        else:
            segments.append(segment)
    return segments


def letterbox_segment_at(
    segments: Sequence[Mapping[str, object]], canonical_ms: int,
) -> dict | None:
    """Return the segment active at ``canonical_ms`` (latest start ≤ time).

    ``segments`` 必须来自 :func:`build_letterbox_segments`（升序、已归一）。
    一场多次漂移也只有个位数事件，线性倒扫足够。
    """
    for segment in reversed(segments):
        if segment["canonical_ms"] <= canonical_ms:
            return segment
    return None


def invert_letterbox_point(
    transform: Mapping[str, float], x: float, y: float,
) -> tuple[float, float]:
    """Canvas point → content point（换算的单一实现点）。"""
    scale = transform["scale"]
    return (
        (x - transform["offset_x"]) / scale,
        (y - transform["offset_y"]) / scale,
    )


def invert_letterbox_length(
    transform: Mapping[str, float], length: float,
) -> float:
    return length / transform["scale"]


def invert_letterbox_area(
    transform: Mapping[str, float], area: float,
) -> float:
    scale = transform["scale"]
    return area / (scale * scale)


def invert_letterbox_target(
    target: Mapping[str, object], transform: Mapping[str, float] | None,
) -> dict:
    """Copy a detection target with coordinates/areas mapped to content space.

    ``transform=None``（无几何事件）→ 原样浅拷贝，行为与现基线一致；
    ``aspect``/``fill``/``circularity``/``shape`` 无量纲，不参与变换。
    """
    inverted = dict(target)
    if transform is None:
        return inverted
    x, y = invert_letterbox_point(
        transform, float(target["x"]), float(target["y"]),
    )
    inverted.update({
        "x": x,
        "y": y,
        "width": max(1, int(round(
            invert_letterbox_length(transform, float(target["width"])),
        ))),
        "height": max(1, int(round(
            invert_letterbox_length(transform, float(target["height"])),
        ))),
        "visible_radius": invert_letterbox_length(
            transform, float(target["visible_radius"]),
        ),
        "area": invert_letterbox_area(transform, float(target["area"])),
    })
    return inverted


def crop_letterbox_content(
    frame: np.ndarray, segment: Mapping[str, object] | None,
) -> np.ndarray:
    """Slice the content rect out of a letterboxed canvas frame.

    用于颜色假设采样帧：黑边是低饱和暗区，先裁掉再进颜色假设/打分，避免
    稀释色相峰值份额并污染暗簇统计。裁切矩形由与反变换同一 ``scale`` 推导
    （offset + scale×src 尺寸），两处几何不会互相矛盾。fail-safe：无变换
    段、矩形非法或越界为空时原样返回输入帧。
    """
    transform = (
        segment.get("transform") if isinstance(segment, Mapping) else None
    )
    if not isinstance(transform, Mapping):
        return frame
    height, width = frame.shape[:2]
    left = int(round(float(transform["offset_x"])))
    top = int(round(float(transform["offset_y"])))
    rect_width = int(round(
        float(transform["scale"]) * float(transform["src_width"]),
    ))
    rect_height = int(round(
        float(transform["scale"]) * float(transform["src_height"]),
    ))
    right = min(width, left + rect_width)
    bottom = min(height, top + rect_height)
    left = max(0, min(left, width))
    top = max(0, min(top, height))
    if right - left <= 0 or bottom - top <= 0:
        return frame
    return frame[top:bottom, left:right]


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
    letterbox: Mapping[str, float] | None = None,
) -> dict:
    """Run one color hypothesis over one frame with shape classification.

    Wide flat components (HUD bars) are rejected by width; everything else
    carries its contour features and three-shape class so callers can gate.
    With ``crosshair_exemption`` a blob whose box covers the frame center is
    kept as ``shape="degraded"`` even when blurred out of every signature —
    the crosshair-covered component is by definition the aimed target (the
    crosshair is the viewport center and HUD never covers it), and the
    approach flick smears exactly that target.

    ``letterbox``（build_letterbox_segments 的段 transform）表示帧内容被
    缩放/偏移进画布（窗口漂移 replay）：每个 blob 先反变换回内容空间，再
    做面积/宽度/中心门控与形状分类，门槛因此保持全尺寸语义，跨漂移时刻
    坐标不跳变。帧本身不做重采样；``letterbox=None`` 时所有门控取值与
    现基线逐位一致（内容空间 == 画布空间）。
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
    if letterbox is None:
        content_width = width
        content_height = height
        content_center_x = center_x
        content_center_y = center_y
    else:
        content_width = float(letterbox["src_width"])
        content_height = float(letterbox["src_height"])
        content_center_x = content_width / 2.0
        content_center_y = content_height / 2.0
    max_area = content_width * content_height * float(hypothesis["max_area_ratio"])
    min_area = float(hypothesis["min_area"])
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    targets: list[dict] = []
    rejected: list[str] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        box_x, box_y, box_w, box_h = cv2.boundingRect(contour)
        if letterbox is None:
            content_area = area
            content_box_x, content_box_y = float(box_x), float(box_y)
            content_box_w, content_box_h = float(box_w), float(box_h)
        else:
            content_area = invert_letterbox_area(letterbox, area)
            content_box_x, content_box_y = invert_letterbox_point(
                letterbox, float(box_x), float(box_y),
            )
            content_box_w = invert_letterbox_length(letterbox, float(box_w))
            content_box_h = invert_letterbox_length(letterbox, float(box_h))
        if (
            content_area < min_area
            or content_area > max_area
            or box_h == 0
            or box_w == 0
        ):
            rejected.append("area")
            continue
        covers_center = (
            content_box_x <= content_center_x <= content_box_x + content_box_w
            and content_box_y <= content_center_y <= content_box_y + content_box_h
        )
        if (
            content_box_w > MAX_BLOB_WIDTH_RATIO * content_width
            and not covers_center
        ):
            rejected.append("hud_width")
            continue
        perimeter = float(cv2.arcLength(contour, True))
        circularity = (
            4.0 * math.pi * area / (perimeter * perimeter) if perimeter else 0.0
        )
        fill = content_area / (content_box_w * content_box_h)
        aspect = content_box_w / content_box_h
        shape = classify_target_shape(
            aspect=aspect, fill=fill, circularity=circularity, area=content_area,
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
        centroid_x = float(moments["m10"] / moments["m00"])
        centroid_y = float(moments["m01"] / moments["m00"])
        if letterbox is None:
            target_x, target_y = centroid_x, centroid_y
        else:
            target_x, target_y = invert_letterbox_point(
                letterbox, centroid_x, centroid_y,
            )
        targets.append({
            "x": target_x,
            "y": target_y,
            "visible_radius": math.sqrt(content_area / math.pi),
            "width": max(1, int(round(content_box_w))),
            "height": max(1, int(round(content_box_h))),
            "area": content_area,
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
            target["x"] - target["width"] / 2.0 <= content_center_x
            <= target["x"] + target["width"] / 2.0
            and target["y"] - target["height"] / 2.0 <= content_center_y
            <= target["y"] + target["height"] / 2.0
            for target in targets
        )
    ):
        # 中心碎片在画布 raw mask 上选取（黑边不进入 ROI），其大小上限必须
        # 用画布面积基准；取回后再按段反变换到内容空间。
        fragment_max_area = (
            max_area if letterbox is None
            else width * height * float(hypothesis["max_area_ratio"])
        )
        fragment = _center_fragment_from_raw_mask(
            raw_mask,
            center_x=center_x,
            center_y=center_y,
            max_area=fragment_max_area,
        )
        if fragment is not None:
            if letterbox is not None:
                fragment = invert_letterbox_target(fragment, letterbox)
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
    if median_count > MAX_MEDIAN_BLOB_COUNT:
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
    "build_letterbox_segments",
    "classify_target_shape",
    "crop_letterbox_content",
    "detect_generic_targets",
    "enumerate_color_hypotheses",
    "invert_letterbox_area",
    "invert_letterbox_length",
    "invert_letterbox_point",
    "invert_letterbox_target",
    "letterbox_segment_at",
    "score_color_hypothesis",
    "select_color_hypothesis",
]
