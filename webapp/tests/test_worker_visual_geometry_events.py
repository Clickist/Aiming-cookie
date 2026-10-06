"""Capture receipt letterbox geometry events ride the v2 mapping bus.

病灶 A3 消费链的 worker 段：Rust 落盘 receipt（新增 ``geometryEvents``），
worker 把事件原样附加进 ``visual_video_time_mapping.v2``（加性字段，不升
v3），由 ``kovaak_tracker.generic_visual_detection`` 单点归一消费。
字段缺席/空/非列表 → 不附加，旧形状与旧行为不变。
"""
from __future__ import annotations

import pytest

from webapp.backend.worker_visual_producers import (
    _run_owned_visual_video_time_mapping_v2,
    video_geometry_events,
)

_GEOMETRY_EVENT = {
    "atUtcMs": 1_791_280_000_000,
    "canonicalMs": 123_456,
    "srcWidth": 1920,
    "srcHeight": 1080,
    "dstX": 320,
    "dstY": 0,
    "dstWidth": 1280,
    "dstHeight": 1600,
    "scale": 0.667,
    "followed": True,
}


def _job(video_receipt) -> dict:
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 5_000,
        "end_ms": 6_000,
        "duration_ms": 1_000,
        "window_semantics": "half_open",
        "timebase_version": "test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }
    return {
        "id": 79,
        "kovaak_run_id": 79,
        "input_snapshot": {
            "schema_version": "analysis_input_snapshot.v3",
            "run_id": 79,
            "canonical_time_window": window,
            "sources": {
                "video": {
                    "ownership": "run",
                    "availability": "available",
                    "format_version": "mp4",
                    "artifact_ref": "run:79:video:abcdef0123456789",
                },
            },
        },
        "video_receipt": video_receipt,
    }


def test_geometry_events_ride_the_v2_mapping_verbatim():
    job = _job({
        "replay": {
            "decodePreroll100ns": 1_219_700,
            "geometryEvents": [_GEOMETRY_EVENT],
        },
    })
    mapping = _run_owned_visual_video_time_mapping_v2(job)
    # Receipt 事件字段保持 camelCase 原样搭车，消费端单点归一。
    assert mapping["geometry_events"] == [_GEOMETRY_EVENT]
    # v2 加性扩展：既有字段与旧形状不变。
    assert mapping["schema_version"] == "visual_video_time_mapping.v2"
    assert mapping["decode_preroll_ms"] == pytest.approx(121.97)
    assert mapping["canonical_origin_ms"] == 5_000
    assert mapping["mapping_method"] == "run_owned_exact_canonical_clip"


def test_root_level_geometry_events_are_read_too():
    """Rust 侧最终落点可能在 receipt 根级或 replay 内，两处都读。"""
    root_job = _job({
        "geometryEvents": [_GEOMETRY_EVENT],
        "replay": {"decodePreroll100ns": 1_219_700},
    })
    assert video_geometry_events(root_job) == [_GEOMETRY_EVENT]
    assert _run_owned_visual_video_time_mapping_v2(root_job)[
        "geometry_events"
    ] == [_GEOMETRY_EVENT]

    replay_job = root_job | {
        "video_receipt": {
            "geometryEvents": [],
            "replay": {
                "decodePreroll100ns": 1_219_700,
                "geometryEvents": [_GEOMETRY_EVENT],
            },
        },
    }
    assert video_geometry_events(replay_job) == [_GEOMETRY_EVENT]


def test_absent_or_empty_events_keep_the_old_mapping_shape():
    # 无 preroll 的 receipt 依旧 fail closed（旧行为不变），事件不改变这一点。
    for receipt in (None, {"geometryEvents": {}}):
        job = _job(receipt)
        assert video_geometry_events(job) is None
        with pytest.raises(ValueError):
            _run_owned_visual_video_time_mapping_v2(job)

    # 有 preroll 但事件缺席/空/非列表 → 不附加字段，映射形状与旧版一致。
    for receipt in (
        {"replay": {"decodePreroll100ns": 1_219_700}},
        {"replay": {"decodePreroll100ns": 1_219_700}, "geometryEvents": []},
        {"replay": {"decodePreroll100ns": 1_219_700}, "geometryEvents": "nope"},
    ):
        assert video_geometry_events(_job(receipt)) is None
        mapping = _run_owned_visual_video_time_mapping_v2(_job(receipt))
        assert "geometry_events" not in mapping
        assert mapping["schema_version"] == "visual_video_time_mapping.v2"


def test_malformed_event_fields_still_board_the_bus():
    """单事件字段非法不在这里拦：搭车原样，消费端 fail-safe 为无变换。"""
    malformed = {"canonicalMs": None, "followed": True}
    job = _job({
        "replay": {
            "decodePreroll100ns": 1_219_700,
            "geometryEvents": [malformed],
        },
    })
    mapping = _run_owned_visual_video_time_mapping_v2(job)
    assert mapping["geometry_events"] == [malformed]
