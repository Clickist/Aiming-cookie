"""Letterbox geometry consumption (窗口漂移 replay，病灶 A3).

Rust 侧对漂移后的视频帧做等比 letterbox 后继续编码：blob 在画布空间被
缩放 s²、坐标跳变，检测/分析门槛实际收紧。这里锁定消费端行为：

(a) 反变换后坐标/面积回到内容空间真值，门槛语义与全尺寸一致；
(b) 多事件按 canonicalMs 分段，选段正确（含 followed=false 终态化段）；
(c) 无 geometryEvents / 空数组 → 输出与改造前基线逐位一致；
(d) 字段缺失/非法 → fail-safe 无变换，不崩溃。

基线数字（5 轨、289/260/385 跳变等）采集自改造前的同一合成片，见
tests/test_generic_static_clicking_analysis.py 的既有 E2E 先例。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from kovaak_tracker.generic_static_clicking_analysis import (
    _canonical_time_ms,
    run_generic_static_clicking_detection_v1,
)
from kovaak_tracker.generic_visual_detection import (
    build_letterbox_segments,
    crop_letterbox_content,
    detect_generic_targets,
    invert_letterbox_area,
    invert_letterbox_point,
    invert_letterbox_target,
    letterbox_segment_at,
)

CANVAS_W, CANVAS_H = 640, 360
CONTENT_BG = (110, 130, 120)
SPHERE_BGR = (180, 180, 40)
SPHERE_RADIUS = 20
SPHERE_XS = (200, 320, 450)
SPHERE_Y = 180
TOTAL_FRAMES = 120

_MAPPING = {
    "schema_version": "visual_video_time_mapping.v2",
    "source_pts_origin_ms": 0.0,
    "canonical_origin_ms": 10_000,
    "mapping_method": "run_owned_exact_canonical_clip",
    "timebase_version": "test.v1",
    "decode_preroll_ms": 121.97,
}
_WINDOW = {
    "schema_version": "canonical_time_window.v1",
    "start_ms": 10_000,
    "end_ms": 12_000,
    "duration_ms": 2_000,
    "window_semantics": "half_open",
    "timebase_version": "test.v1",
    "start_source": "fixture",
    "end_source": "fixture",
    "warnings": [],
}
_LETTERBOX = {
    "atUtcMs": 1_791_280_000_000,
    "canonicalMs": 11_110,
    "srcWidth": CANVAS_W,
    "srcHeight": CANVAS_H,
    "dstX": 160,
    "dstY": 90,
    "dstWidth": 320,
    "dstHeight": 180,
    "scale": 0.5,
    "followed": True,
}


def _content_frame() -> np.ndarray:
    frame = np.full((CANVAS_H, CANVAS_W, 3), CONTENT_BG, dtype=np.uint8)
    for x in SPHERE_XS:
        cv2.circle(frame, (x, SPHERE_Y), SPHERE_RADIUS, SPHERE_BGR, -1)
    return frame


def _letterbox_frame(
    content: np.ndarray, scale: float, dst_x: int, dst_y: int,
) -> np.ndarray:
    small = cv2.resize(
        content,
        (int(round(CANVAS_W * scale)), int(round(CANVAS_H * scale))),
        interpolation=cv2.INTER_LINEAR,
    )
    canvas = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
    canvas[
        dst_y:dst_y + small.shape[0], dst_x:dst_x + small.shape[1],
    ] = small
    return canvas


def _peak_hypothesis(frame: np.ndarray, *, morphology: str, min_area: float) -> dict:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    saturated = hsv[
        (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100) & (hsv[:, :, 2] <= 245)
    ]
    peak = int(np.argmax(np.bincount(saturated[:, 0], minlength=180)))
    return {
        "name": "fixture_peak",
        "hsv_lower": [(peak - 10) % 180, 100, 100],
        "hsv_upper": [(peak + 10) % 180, 255, 245],
        "morphology": morphology,
        "min_area": min_area,
        "max_area_ratio": 0.05,
    }


def _write_drift_clip(path, *, drift_index: int = 60) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 60, (CANVAS_W, CANVAS_H),
    )
    assert writer.isOpened()
    content = _content_frame()
    for index in range(TOTAL_FRAMES):
        if index < drift_index:
            writer.write(content)
        else:
            writer.write(_letterbox_frame(content, 0.5, 160, 90))
    writer.release()


def _write_two_drift_clip(path) -> None:
    """Frames 0-39 full, 40-79 scale 0.5 at (160,90), 80-119 scale 0.8 at (64,36)."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 60, (CANVAS_W, CANVAS_H),
    )
    assert writer.isOpened()
    content = _content_frame()
    for index in range(TOTAL_FRAMES):
        if index < 40:
            writer.write(content)
        elif index < 80:
            writer.write(_letterbox_frame(content, 0.5, 160, 90))
        else:
            writer.write(_letterbox_frame(content, 0.8, 64, 36))
    writer.release()


def _run(path, events: list[dict] | None = None) -> dict:
    mapping = dict(_MAPPING)
    if events is not None:
        mapping["geometry_events"] = events
    return run_generic_static_clicking_detection_v1(
        media_path=str(path),
        analysis_ref="analysis:1",
        canonical_time_window=_WINDOW,
        video_time_mapping=mapping,
    )


# --- (a) 反变换：坐标/面积回到内容空间真值 --------------------------------


def test_inverse_restores_content_coordinates_and_area_thresholds():
    segments = build_letterbox_segments([_LETTERBOX])
    transform = segments[0]["transform"]
    assert invert_letterbox_point(transform, 260.0, 165.0) == pytest.approx(
        (200.0, 150.0),
    )
    assert invert_letterbox_area(transform, 314.16) == pytest.approx(
        1256.64, rel=1e-6,
    )

    content = np.full((CANVAS_H, CANVAS_W, 3), CONTENT_BG, dtype=np.uint8)
    cv2.circle(content, (200, 150), 20, SPHERE_BGR, -1)
    # A speck below the content min_area but whose letterboxed canvas area
    # (≈50) is far below it: only inversion can recover it.
    cv2.circle(content, (500, 300), 8, SPHERE_BGR, -1)
    canvas = _letterbox_frame(content, 0.5, 160, 90)
    hypothesis = _peak_hypothesis(canvas, morphology="none", min_area=100.0)

    # 改造前行为（无变换）：坐标留在画布空间，小目标面积被 s² 缩到门槛下。
    naive = detect_generic_targets(canvas, hypothesis)
    assert len(naive["targets"]) == 1
    assert naive["targets"][0]["x"] == pytest.approx(260.0, abs=1.0)
    assert naive["targets"][0]["area"] == pytest.approx(293.5, rel=0.05)
    assert "area" in naive["rejected"]

    inverted = detect_generic_targets(canvas, hypothesis, letterbox=transform)
    assert len(inverted["targets"]) == 2
    big = next(target for target in inverted["targets"] if target["area"] > 800)
    small = next(target for target in inverted["targets"] if target["area"] <= 800)
    assert big["x"] == pytest.approx(200.0, abs=1.5)
    assert big["y"] == pytest.approx(150.0, abs=1.5)
    assert big["area"] == pytest.approx(np.pi * 400, rel=0.10)
    assert big["shape"] == "sphere"
    assert big["width"] == big["height"] == pytest.approx(40, abs=2)
    assert small["x"] == pytest.approx(500.0, abs=1.5)
    assert small["y"] == pytest.approx(300.0, abs=1.5)
    assert small["area"] == pytest.approx(np.pi * 64, rel=0.25)
    assert small["shape"] == "sphere"


def test_invert_target_maps_box_radius_and_keeps_dimensionless_features():
    transform = build_letterbox_segments([_LETTERBOX])[0]["transform"]
    target = {
        "x": 260.0, "y": 165.0, "width": 20, "height": 18,
        "visible_radius": 10.0, "area": 314.0,
        "aspect": 20 / 18, "fill": 0.87, "circularity": 0.9,
        "shape": "sphere", "confidence": 0.9,
    }
    inverted = invert_letterbox_target(target, transform)
    assert inverted["x"] == pytest.approx(200.0)
    assert inverted["y"] == pytest.approx(150.0)
    assert inverted["width"] == 40 and inverted["height"] == 36
    assert inverted["visible_radius"] == pytest.approx(20.0)
    assert inverted["area"] == pytest.approx(1256.0)
    assert inverted["shape"] == "sphere"
    assert inverted["aspect"] == target["aspect"] == 20 / 18
    assert inverted["fill"] == target["fill"]
    # None（无事件）→ 浅拷贝走现行为。
    untouched = invert_letterbox_target(target, None)
    assert untouched == target
    assert untouched is not target


# --- (b) 多事件分段 --------------------------------------------------------


def _event(canonical_ms: int, *, followed: bool = True, scale=None, **overrides):
    event = {
        "canonicalMs": canonical_ms,
        "srcWidth": 640, "srcHeight": 360,
        "dstX": 160, "dstY": 90, "dstWidth": 320, "dstHeight": 180,
        "scale": 0.5, "followed": followed,
    }
    if scale is not None:
        event["scale"] = scale
    event.update(overrides)
    return event


def test_segments_sort_by_canonical_ms_and_select_the_active_one():
    events = [
        _event(5_000, scale=2.0, dstX=0, dstY=0),
        _event(2_000, scale=1.0, dstX=0, dstY=0),
        _event(4_000, followed=False),
        _event(3_000),
    ]
    segments = build_letterbox_segments(events)
    assert [segment["canonical_ms"] for segment in segments] == [
        2_000, 3_000, 4_000, 5_000,
    ]
    # Before the first event: identity (无变换)。
    assert letterbox_segment_at(segments, 1_999) is None
    assert letterbox_segment_at(segments, 2_000)["transform"]["scale"] == 1.0
    assert letterbox_segment_at(segments, 3_500)["transform"]["scale"] == 0.5
    # followed=false 段：该时刻触发终态化，不产生后续内容 → 无变换。
    assert letterbox_segment_at(segments, 4_500)["transform"] is None
    assert letterbox_segment_at(segments, 9_000)["transform"]["scale"] == 2.0
    # Duplicate timestamps: the later entry wins.
    duplicated = build_letterbox_segments([_event(3_000, scale=0.25), _event(3_000)])
    assert duplicated == [{"canonical_ms": 3_000, "transform": {
        "scale": 0.5, "offset_x": 160.0, "offset_y": 90.0,
        "src_width": 640.0, "src_height": 360.0,
    }}]


def test_followed_absent_is_treated_as_followed_actual_rust_wire():
    """Rust 实际落盘不带 followed（队列只记录已生效的跟随事件）→ 缺省视为跟随。

    显式 false（合同：终态化标记）与非布尔值仍按无变换降级。
    """
    rust_shaped = {
        "canonicalMs": 11_110,
        "srcWidth": 640, "srcHeight": 360,
        "dstX": 160, "dstY": 90, "dstWidth": 320, "dstHeight": 180,
        "scale": 0.5,
    }
    (segment,) = build_letterbox_segments([rust_shaped])
    assert segment["transform"]["scale"] == pytest.approx(0.5)
    # 显式 false 仍是终态化 → 无变换；显式 true 应用。
    assert build_letterbox_segments(
        [_event(1_000, followed=False)],
    )[0]["transform"] is None
    assert build_letterbox_segments(
        [_event(1_000, followed=True)],
    )[0]["transform"]["scale"] == pytest.approx(0.5)


def test_scale_falls_back_to_dst_over_src_and_invalid_geometry_is_identity():
    without_scale = {
        "canonicalMs": 1_000, "followed": True,
        "srcWidth": 640, "srcHeight": 360,
        "dstX": 0, "dstY": 0, "dstWidth": 320, "dstHeight": 180,
    }
    # scale 缺席 → 由 dstWidth/srcWidth 兜底推导（0.5）。
    assert build_letterbox_segments([without_scale])[0]["transform"]["scale"] == (
        pytest.approx(0.5)
    )
    # srcWidth 缺失 → 该段几何不可反转 → transform None（无变换）。
    missing_src = _event(1_000)
    missing_src.pop("srcWidth")
    assert build_letterbox_segments([missing_src])[0]["transform"] is None
    # dstX 缺失同理。
    missing_dst = _event(1_000)
    missing_dst.pop("dstX")
    assert build_letterbox_segments([missing_dst])[0]["transform"] is None
    # scale 非法（0/NaN）→ 同样回退无变换，不抛错。
    for bad_scale in (0, -0.5, float("nan"), "0.5"):
        assert build_letterbox_segments(
            [_event(1_000) | {"scale": bad_scale}],
        )[0]["transform"] is None


def test_crop_content_rect_removes_black_bars_and_fails_safe():
    content = _content_frame()
    canvas = _letterbox_frame(content, 0.5, 160, 90)
    segment = build_letterbox_segments([_LETTERBOX])[0]
    cropped = crop_letterbox_content(canvas, segment)
    assert cropped.shape[:2] == (180, 320)
    # 裁切矩形内就是缩放后的内容：球心内容 (200,180) → 帧内 (100,90)。
    assert cropped[90, 100].tolist() == list(SPHERE_BGR)
    assert cropped[10, 10].tolist() == list(CONTENT_BG)
    # 画布左上角原本是黑边，裁掉后不再出现在输入帧里。
    assert canvas[10, 10].tolist() == [0, 0, 0]
    assert crop_letterbox_content(canvas, None) is canvas
    assert crop_letterbox_content(canvas, {"canonical_ms": 0, "transform": None}) is canvas
    # 越界矩形 → 原样返回，不崩溃。
    out_of_bounds = {"canonical_ms": 0, "transform": {
        "scale": 1.0, "offset_x": 10_000.0, "offset_y": 10_000.0,
        "src_width": 640.0, "src_height": 360.0,
    }}
    assert crop_letterbox_content(canvas, out_of_bounds) is canvas


# --- (d) 缺失字段 fail-safe ------------------------------------------------


def test_missing_fields_fail_safe_to_no_transform():
    assert build_letterbox_segments(None) == []
    assert build_letterbox_segments([]) == []
    assert build_letterbox_segments("not-a-list") == []
    assert build_letterbox_segments([{"note": "no canonicalMs"}]) == []
    assert build_letterbox_segments(["not-a-dict"]) == []
    assert build_letterbox_segments([123]) == []
    assert build_letterbox_segments(
        [{"canonicalMs": True}],  # bool 不是合法时间锚
    ) == []
    # canonicalMs 合法但 followed 显式非布尔（"yes"）→ 无变换段（不崩溃）。
    assert build_letterbox_segments([_event(1_000, followed="yes")])[0][
        "transform"
    ] is None
    # followed 字段整体缺席不是错误：Rust 实际线形不带该字段，按跟随处理。
    without_followed = _event(1_000)
    without_followed.pop("followed")
    assert build_letterbox_segments([without_followed])[0][
        "transform"
    ]["scale"] == pytest.approx(0.5)
    # 单个事件的 canonicalMs 非法 → 整个列表作废：边界不可定位时宁可全程无变换。
    valid = _event(1_000)
    invalid = _event(2_000)
    invalid["canonicalMs"] = None
    assert build_letterbox_segments([valid, invalid]) == []


# --- 端到端：漂移前基线锁 + 漂移后修复 ------------------------------------


@pytest.fixture(scope="module")
def drift_clip(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("letterbox") / "drift.mp4"
    _write_drift_clip(path)
    return str(path)


def test_no_events_output_matches_pre_change_baseline_bit_for_bit(drift_clip):
    """(c) 无 geometryEvents：与改造前的同一合成片输出一致。"""
    result = _run(drift_clip)
    assert result["detector"]["hypothesis"]["name"] == "saturated_hue_peak"
    assert result["detector"]["shape"] == "sphere"
    assert result["frames_decoded"] == 120
    assert result["frames_in_window"] == 113
    assert result["frames_with_detection"] == 113
    assert result["frame_coverage"] == pytest.approx(1.0)
    tracks = {
        round(track["x"]): track for track in result["tracks"]
    }
    # 改造前基线：漂移把左/中/右三条轨道打断成 5 条，坐标跳变。
    assert set(tracks) == {200, 320, 450, 260, 385}
    assert tracks[200]["death_ms"] == pytest.approx(11_105, abs=34)
    assert tracks[200]["sample_count"] == 60
    assert tracks[260]["birth_ms"] == pytest.approx(11_122, abs=34)
    assert tracks[260]["sample_count"] == 53
    assert tracks[320]["sample_count"] == 113
    assert tracks[450]["sample_count"] == 60
    assert tracks[385]["sample_count"] == 53

    # 空数组与非法字段同样回退为该基线（不崩溃、不改变输出）。
    empty = _run(drift_clip, events=[])
    malformed = _run(drift_clip, events=[{"note": "canonicalMs 未实现"}])
    for alt in (empty, malformed):
        assert alt["tracks"] == result["tracks"]
        assert alt["frames_decoded"] == result["frames_decoded"]
        assert alt["frames_in_window"] == result["frames_in_window"]
        assert alt["frames_with_detection"] == result["frames_with_detection"]


def test_single_drift_event_keeps_tracks_and_content_coordinates(drift_clip):
    result = _run(drift_clip, events=[_LETTERBOX])
    assert len(result["tracks"]) == 3
    tracks = {round(track["x"]): track for track in result["tracks"]}
    assert set(tracks) == {200, 320, 450}
    for track in tracks.values():
        assert track["y"] == pytest.approx(180.0, abs=1.5)
        assert track["sample_count"] >= 110
        assert track["median_area"] == pytest.approx(1_200.0, abs=160)
        assert track["birth_ms"] == pytest.approx(10_122, abs=34)
        assert track["death_ms"] == pytest.approx(11_989, abs=34)

    # Rust 实际线形（GeometryEvent 落盘：无 followed/atUtcMs）必须产生同一修复。
    rust_shaped = {
        key: value for key, value in _LETTERBOX.items()
        if key not in {"followed", "atUtcMs"}
    }
    rust_result = _run(drift_clip, events=[rust_shaped])
    assert rust_result["tracks"] == result["tracks"]


def test_two_drift_events_select_segment_per_frame(tmp_path):
    """(b) 端到端多段：两次漂移后坐标/面积仍与全尺寸一致。"""
    path = tmp_path / "two_drifts.mp4"
    _write_two_drift_clip(path)
    events = [
        _LETTERBOX | {"canonicalMs": 10_780},
        _LETTERBOX | {
            "canonicalMs": 11_447, "scale": 0.8, "dstX": 64, "dstY": 36,
            "dstWidth": 512, "dstHeight": 288,
        },
    ]
    result = _run(path, events=events)
    assert len(result["tracks"]) == 3
    tracks = {round(track["x"]): track for track in result["tracks"]}
    assert set(tracks) == {200, 320, 450}
    for track in tracks.values():
        assert track["sample_count"] >= 110
        assert track["median_area"] == pytest.approx(1_200.0, abs=160)


def test_canonical_timeline_matches_pinned_event_boundaries(drift_clip):
    """事件边界与实测 PTS 对齐（保证上面的分段断言是活边界而非死区）。"""
    capture = cv2.VideoCapture(drift_clip)
    try:
        times = []
        while True:
            ok, _ = capture.read()
            if not ok:
                break
            times.append(_canonical_time_ms(
                _MAPPING, float(capture.get(cv2.CAP_PROP_POS_MSEC)),
            ))
    finally:
        capture.release()
    assert len(times) == TOTAL_FRAMES
    assert times[59] < _LETTERBOX["canonicalMs"] <= times[60]
    assert times[39] < 10_780 <= times[40]
    assert times[79] < 11_447 <= times[80]
