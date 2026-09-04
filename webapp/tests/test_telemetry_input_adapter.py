"""遥测输入适配（telemetry_input_adapter）的合同测试。

覆盖今晚定位的缺口：遥测-only 运行（无 raw input trace、无视频）在 native
分析器处的崩溃。旁车数据为手工构造的最小 fixture（不依赖真实大文件与
cv2）；真实数据链路由 dev 数据根副本的现场验证覆盖。

① 遥测-only：冻结 inputs 旁车 → 合成等价轨迹点列（结构/时间基/按钮沿），
   run_native_analysis 完成且轨迹通道带 mouse_trajectory_from_external_telemetry
   来源标注；
② 旁车不可用（缺文件/指纹不符/对齐未接受/无遥测源/坏行）→ (None, unavailable)
   受控降级，run_native_analysis 落 unavailable + limitation，绝不抛异常；
③ 有 raw trace 的既有路径行为不变（回归：点列来自 trace、无遥测标注）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.backend import file_store, worker
from webapp.backend.telemetry_input_adapter import (
    MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
    MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE,
    MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED,
    telemetry_mouse_trace_points,
)


def _data_root() -> Path:
    """conftest 的隔离测试数据根（每测重置）。"""
    return file_store._data_root()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# 真机同量级的对齐锚与 canonical 窗（epoch ms）。
ANCHOR_EPOCH_S = 1_788_536_697.7387
START_MS = 1_788_537_436_285
END_MS = START_MS + 30_048
# sidecar t 域里 canonical 窗起点对应的 t（epoch = s_epoch_of_t0 + t）。
T0_S = START_MS / 1000.0 - ANCHOR_EPOCH_S

CANONICAL_WINDOW = {
    "schema_version": "canonical_time_window.v1",
    "timebase_version": "time_alignment.v2",
    "start_ms": START_MS,
    "end_ms": END_MS,
    "duration_ms": END_MS - START_MS,
    "start_source": "stats_challenge_start",
    "end_source": "stats_event",
    "stats_anchor_status": "missing",
    "stats_time_of_day_ms": None,
    "stats_local_to_utc_mapping": None,
    "warnings": [],
    "window_semantics": "half_open",
}

EXTERNAL_RUN_ID = "ext-inputadapter01"


def _input_records() -> list[dict]:
    """(t 偏移秒 相对 canonical 窗起点, dx, dy, btn) —— 覆盖窗外/窗内/按钮沿。"""
    return [
        (-10.0, 1, 0, []),
        (-0.5, 0, 0, []),
        (0.05, 5, 0, []),
        (0.45, 3, 4, []),
        (0.65, 0, 0, ["L_down"]),
        (0.95, 1, 0, ["L_up"]),
        (31.0, 0, 0, []),
    ]


def _inputs_payload(*, epoch_anchor: float | None = ANCHOR_EPOCH_S) -> bytes:
    if epoch_anchor is not None:
        t0 = START_MS / 1000.0 - epoch_anchor
    else:
        t0 = T0_S
    lines = []
    for offset, dx, dy, btn in _input_records():
        record = {"t": round(t0 + offset, 4), "dx": dx, "dy": dy, "btn": btn}
        lines.append(json.dumps(record))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_frozen_external_run(
    *,
    with_inputs: bool = True,
    alignment_accepted: bool = True,
    manifest_anchor: float | None = ANCHOR_EPOCH_S,
    t_start: float | None = None,
) -> Path:
    """在 DATA_ROOT 落一个最小冻结 ext 目录（views/round/bb 按
    test_worker_telemetry 口径，inputs 可携带 dx/dy 轨迹）。"""
    ext_dir = _data_root() / "external" / EXTERNAL_RUN_ID
    ext_dir.mkdir(parents=True)
    views = b"".join(
        (
            '{"t": %.3f, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": 103.0}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    frames = b"".join(
        (
            '{"ev": "frame", "t": %.3f, "targets": [[0, 4000.0, 0.0, 0.0]]}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    inputs = _inputs_payload(epoch_anchor=manifest_anchor)
    bb = (
        b'{"challenges": [{"window_t": [0.0, 1.0], '
        b'"bots": [{"character": {"bb": {"radius": 60.0}}}]}]}\n'
    )
    for name, payload in (
        ("round.jsonl", frames),
        ("views_01.jsonl", views),
        ("bb.json", bb),
    ):
        (ext_dir / name).write_bytes(payload)
    if with_inputs:
        (ext_dir / "inputs_01.jsonl").write_bytes(inputs)
    else:
        inputs = b""
    alignment: dict = {"method": "fixture", "accepted": alignment_accepted}
    if manifest_anchor is not None:
        alignment["s_epoch_of_t0"] = manifest_anchor
    manifest = {
        "schema_version": "merge_manifest.v1",
        "generated": "2026-09-04T00:00:00",
        "round_dir": str(ext_dir),
        "alignment": alignment,
    }
    (ext_dir / "merge_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
    )
    meta = {
        "schema_version": "external_run.v1",
        "external_run_id": EXTERNAL_RUN_ID,
        "user_id": "u1",
        "origin": {
            "source_file": "fixture_0904_000000.jsonl",
            "round": 1,
            "round_file": "upstream/fixture/round_01.jsonl",
            "index_file": "upstream/fixture/rounds_index.json",
            "generator": "fixture",
            "format_version": 1,
            "params": {},
        },
        "fingerprints": {
            "round_sha256": _sha256(frames),
            "round_size": len(frames),
            "round_mtime_ns": 0,
            "import_parser_version": "external_run_import.v1",
        },
        "time": {"t_start": t_start if t_start is not None else T0_S, "t_end": T0_S + 32.0},
        "scenario_proposal": {"source": "pending"},
        "pairing": {"matched_run_ids": [42], "pair_confidence": "coarse"},
        "sidecars": {
            "views": {"present": True, "sha256": _sha256(views), "size": len(views)},
            "inputs": {"present": True, "sha256": _sha256(inputs), "size": len(inputs)},
            "bb": {"present": True, "sha256": _sha256(bb), "size": len(bb)},
            "merge_manifest": {
                "present": True,
                "sha256": _sha256(
                    (ext_dir / "merge_manifest.json").read_bytes(),
                ),
                "size": (ext_dir / "merge_manifest.json").stat().st_size,
            },
        },
        "frames_path": f"external/{EXTERNAL_RUN_ID}/round.jsonl",
        "imported_at": "2026-09-04T00:00:00Z",
        "revisions": [],
    }
    (ext_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8",
    )
    return ext_dir


def _telemetry_source(ext_dir: Path, *, inputs_sha: str | None = None) -> dict:
    sidecars: dict = {}
    if inputs_sha is not None:
        inputs_payload = (ext_dir / "inputs_01.jsonl").read_bytes()
        sidecars["inputs"] = {
            "present": True,
            "sha256": inputs_sha or _sha256(inputs_payload),
            "size": len(inputs_payload),
        }
    return {
        "artifact_ref": f"external:{EXTERNAL_RUN_ID}",
        "availability": "available",
        "external_run_id": EXTERNAL_RUN_ID,
        "round": 1,
        "frames_path": str(ext_dir / "round.jsonl"),
        "sidecars": sidecars,
        "pairing_confidence": "coarse",
        "reason": None,
    }


def _snapshot(ext_dir: Path | None) -> dict:
    """stats/performance 齐备 + 可选遥测源 + 绝无 raw trace 的快照。"""
    sources: dict = {
        "stats": {
            "artifact_ref": "run:42:stats",
            "availability": "available",
            "path": "/db-private/runs/42/stats.csv",
        },
        "performance": {
            "artifact_ref": "run:42:performance",
            "availability": "available",
            "path": "/db-private/runs/42/performance.perf",
        },
    }
    if ext_dir is not None:
        sources["external_telemetry"] = _telemetry_source(
            ext_dir, inputs_sha=None,
        )
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 42,
        "canonical_time_window": dict(CANONICAL_WINDOW),
        "sources": sources,
        "trace": None,
    }


# ---------------------------------------------------------------- ① 合成路径


def test_synthesizes_points_with_manifest_epoch_anchor():
    """merge_manifest 对齐锚（s_epoch_of_t0）→ epoch ms 点列；按钮沿维护
    buttons 位掩码；轨迹带来源标注。"""
    ext_dir = _write_frozen_external_run()
    points, limitations = telemetry_mouse_trace_points(_snapshot(ext_dir))

    assert limitations == [MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY]
    assert points is not None and len(points) == len(_input_records())
    for point, (offset, dx, dy, _btn) in zip(points, _input_records()):
        assert set(point) == {"timestamp_ms", "dx", "dy", "buttons"}
        assert point["timestamp_ms"] == round((T0_S + ANCHOR_EPOCH_S + offset) * 1000.0)
        assert point["timestamp_ms"] == START_MS + int(offset * 1000.0)
        assert point["dx"] == dx and point["dy"] == dy
    # 按钮沿：L_down 记录置 bit0，L_up 记录清除；后续记录保持状态。
    buttons = [point["buttons"] for point in points]
    assert buttons == [0, 0, 0, 0, 1, 0, 0]
    # 时间戳单调不减（同毫秒允许）。
    assert all(
        a["timestamp_ms"] <= b["timestamp_ms"]
        for a, b in zip(points, points[1:])
    )


def test_synthesizes_points_with_round_relative_anchor_fallback():
    """锚缺失（对齐仍 accepted）→ 回退 round 相对映射（与遥测视觉 producer
    同口径：meta.time.t_start → canonical window_start）。"""
    ext_dir = _write_frozen_external_run(manifest_anchor=None)
    points, limitations = telemetry_mouse_trace_points(_snapshot(ext_dir))

    assert limitations == [MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY]
    assert points is not None
    for point, (offset, *_rest) in zip(points, _input_records()):
        assert point["timestamp_ms"] == START_MS + int(offset * 1000.0)


def test_skips_bad_and_non_monotonic_records_observably():
    """坏行与时间回跳记录被跳过且可观测（skipped limitation），合法记录保留。"""
    ext_dir = _write_frozen_external_run()
    inputs_path = ext_dir / "inputs_01.jsonl"
    lines = inputs_path.read_text(encoding="utf-8").splitlines()
    # 追加一行坏 JSON 与一行时间回跳（t 远小于锚域）。
    lines.append("{not-json")
    lines.append(json.dumps({"t": 1.0, "dx": 9, "dy": 9, "btn": []}))
    inputs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    source = _snapshot(ext_dir)
    source["sources"]["external_telemetry"]["sidecars"]["inputs"] = {
        "present": True,
        "sha256": _sha256(inputs_path.read_bytes()),
        "size": inputs_path.stat().st_size,
    }

    points, limitations = telemetry_mouse_trace_points(source)

    assert points is not None and len(points) == len(_input_records())
    assert limitations == [
        MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
        MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED,
    ]


# ---------------------------------------------------------------- ② 降级路径


def test_unavailable_without_telemetry_source():
    _write_frozen_external_run()
    points, limitations = telemetry_mouse_trace_points(_snapshot(None))
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def test_unavailable_when_telemetry_source_marked_unavailable():
    ext_dir = _write_frozen_external_run()
    snapshot = _snapshot(ext_dir)
    snapshot["sources"]["external_telemetry"]["availability"] = "unavailable"
    points, limitations = telemetry_mouse_trace_points(snapshot)
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def test_unavailable_when_inputs_sidecar_missing():
    ext_dir = _write_frozen_external_run(with_inputs=False)
    points, limitations = telemetry_mouse_trace_points(_snapshot(ext_dir))
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def test_unavailable_when_frozen_inputs_fingerprint_mismatch():
    ext_dir = _write_frozen_external_run()
    snapshot = _snapshot(ext_dir)
    snapshot["sources"]["external_telemetry"]["sidecars"]["inputs"] = {
        "present": True,
        "sha256": "0" * 64,
        "size": 1,
    }
    points, limitations = telemetry_mouse_trace_points(snapshot)
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def test_unavailable_when_alignment_not_accepted():
    ext_dir = _write_frozen_external_run(alignment_accepted=False)
    points, limitations = telemetry_mouse_trace_points(_snapshot(ext_dir))
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def test_unavailable_when_meta_missing():
    _write_frozen_external_run()
    (_data_root() / "external" / EXTERNAL_RUN_ID / "meta.json").unlink()
    points, limitations = telemetry_mouse_trace_points(
        _snapshot(_data_root() / "external" / EXTERNAL_RUN_ID),
    )
    assert points is None
    assert limitations == [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


# ------------------------------------------------- run_native_analysis 装配


def _parsed_stats_stub() -> MagicMock:
    stub = MagicMock(cm_per_360=None, fov=None, kills=MagicMock(index=[]))
    # stats dict 会进 evidence（JSON 持久化）：属性全部换成可序列化值。
    stub.summary = {}
    stub.config = {}
    stub.scenario = "Fixture"
    stub.weapon_aggregates = []
    stub.field_presence = {}
    return stub


def _patch_frozen_sources(mapping: dict[str, bytes]):
    """按 kind 打桩 worker._read_frozen_source_bytes；未登记 kind 即失败。"""

    def _read(kind: str, _source: object) -> bytes:
        assert kind in mapping, f"unexpected frozen source read: {kind}"
        return mapping[kind]

    return patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        side_effect=_read,
    )


def test_run_native_analysis_completes_from_telemetry_inputs_with_marker():
    """①遥测-only：无 raw trace，轨迹通道来自冻结 inputs 旁车，来源标注进
    limitations，点击锚产生 flick 事件——不抛异常。"""
    ext_dir = _write_frozen_external_run()
    snapshot = _snapshot(ext_dir)

    with _patch_frozen_sources({
        "stats": b"stats-bytes",
        "performance": b"perf-bytes",
    }), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=_parsed_stats_stub(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ):
        result = worker.run_native_analysis(snapshot)

    assert result["status"] in {"available", "partial"}
    trajectory = result["deterministic"]["trajectory"]
    assert trajectory["unit"] == "raw_counts"
    assert trajectory["point_count"] == len(_input_records())
    assert MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY in result["limitations"]
    # L_down 沿 → 点击锚 → 有移动的 flick 事件。
    flicks = [
        item for item in result["deterministic"]["timeline"]
        if item.get("event_type") == "flick"
    ]
    assert len(flicks) == 1
    assert result["deterministic"]["metrics"]["flick_count"]["value"] == 1.0


def test_run_native_analysis_degrades_when_telemetry_inputs_unusable():
    """②旁车不可用：受控降级为 unavailable + limitation，绝不抛异常。"""
    snapshot = _snapshot(None)  # 无遥测源 → 合成不可用
    with _patch_frozen_sources({
        "stats": b"stats-bytes",
        "performance": b"perf-bytes",
    }), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=_parsed_stats_stub(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ):
        result = worker.run_native_analysis(snapshot)

    assert result["status"] == "unavailable"
    assert result["deterministic"]["metrics"] == {}
    assert result["limitations"] == [
        "raw_input_missing",
        MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE,
    ]


def test_run_native_analysis_raw_trace_path_unchanged(tmp_path: Path):
    """③回归：raw trace 在场时优先消费冻结 trace，行为与标注与改动前一致。"""
    from webapp.backend.kovaak_snapshot_codec import write_mouse_snapshot

    start = CANONICAL_WINDOW["start_ms"]
    trace_path = tmp_path / "trace.bin"
    raw_points = [
        {"timestamp_ms": start + 10, "dx": 5, "dy": 0, "buttons": 0},
        {"timestamp_ms": start + 20, "dx": 3, "dy": 4, "buttons": 1},
        {"timestamp_ms": start + 30, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": start + 40, "dx": 0, "dy": 2, "buttons": 0},
    ]
    write_mouse_snapshot(trace_path, raw_points)
    snapshot = _snapshot(_write_frozen_external_run())
    snapshot["trace"] = {
        "artifact_ref": "run:42:trace",
        "path": str(trace_path),
        "availability": "available",
        "format_version": 2,
        "fingerprint": {
            "sha256": _sha256(trace_path.read_bytes()),
            "size": trace_path.stat().st_size,
            "mtime_ns": trace_path.stat().st_mtime_ns,
        },
    }

    with _patch_frozen_sources({
        "stats": b"stats-bytes",
        "performance": b"perf-bytes",
        "raw_input": trace_path.read_bytes(),
    }), patch(
        "kovaak_tracker.csv_parser.parse_stats_bytes",
        return_value=_parsed_stats_stub(),
    ), patch(
        "kovaak_tracker.performance_parser.parse_performance_bytes",
        return_value=object(),
    ):
        result = worker.run_native_analysis(snapshot)

    assert result["status"] in {"available", "partial"}
    # 点列来自 raw trace（写盘时经 canonicalize 的字节原样解码），不是遥测旁车。
    from webapp.backend.kovaak_snapshot_codec import decode_mouse_snapshot_bytes

    expected_points = decode_mouse_snapshot_bytes(trace_path.read_bytes())
    assert len(expected_points) != len(_input_records())
    assert result["deterministic"]["trajectory"]["point_count"] == len(expected_points)
    assert MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY not in result["limitations"]
    assert MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE not in str(result)


# ------------------------------------------------- process_one 会话级（tmp）


@pytest.mark.asyncio
async def test_process_one_telemetry_only_session_completes_with_trajectory():
    """①会话级：遥测-only 作业走真实 run_native_analysis，session 落 done，
    轨迹通道在场且带来源标注（回归锚：改动前此处 worker.py:842 硬抛 →
    internal_unknown/analysis_failed）。"""
    from webapp.backend import queue

    # E2E fixture：窗口 [1000, 2000)、t 域 [0, 1)、epoch 锚 1.0s。
    ext_dir = _data_root() / "external" / EXTERNAL_RUN_ID
    ext_dir.mkdir(parents=True)
    views = b"".join(
        (
            '{"t": %.3f, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "fov": 103.0}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    frames = b"".join(
        (
            '{"ev": "frame", "t": %.3f, "targets": [[0, 4000.0, 0.0, 0.0]]}\n'
            % (t / 1000.0)
        ).encode("utf-8")
        for t in range(0, 500, 16)
    )
    bb = (
        b'{"challenges": [{"window_t": [0.0, 1.0], '
        b'"bots": [{"character": {"bb": {"radius": 60.0}}}]}]}\n'
    )
    inputs = "".join(
        json.dumps(record) + "\n"
        for record in (
            {"t": 0.0, "dx": 0, "dy": 0, "btn": []},
            {"t": 0.1, "dx": 6, "dy": 0, "btn": []},
            {"t": 0.15, "dx": 2, "dy": 8, "btn": []},
            {"t": 0.2, "dx": 0, "dy": 0, "btn": ["L_down"]},
            {"t": 0.3, "dx": 1, "dy": 1, "btn": ["L_up"]},
            {"t": 1.0, "dx": 0, "dy": 0, "btn": []},
        )
    ).encode("utf-8")
    for name, payload in (
        ("round.jsonl", frames),
        ("views_01.jsonl", views),
        ("bb.json", bb),
        ("inputs_01.jsonl", inputs),
    ):
        (ext_dir / name).write_bytes(payload)
    manifest = {
        "schema_version": "merge_manifest.v1",
        "generated": "2026-09-04T00:00:00",
        "round_dir": str(ext_dir),
        "alignment": {
            "method": "fixture",
            "accepted": True,
            "s_epoch_of_t0": 1.0,
        },
    }
    (ext_dir / "merge_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    meta = {
        "schema_version": "external_run.v1",
        "external_run_id": EXTERNAL_RUN_ID,
        "user_id": "u1",
        "origin": {
            "source_file": "fixture_0904_000000.jsonl",
            "round": 1,
            "round_file": "upstream/fixture/round_01.jsonl",
            "index_file": "upstream/fixture/rounds_index.json",
            "generator": "fixture",
            "format_version": 1,
            "params": {},
        },
        "fingerprints": {
            "round_sha256": _sha256(frames),
            "round_size": len(frames),
            "round_mtime_ns": 0,
            "import_parser_version": "external_run_import.v1",
        },
        "time": {"t_start": 0.0, "t_end": 1.0},
        "scenario_proposal": {"source": "pending"},
        "pairing": {"matched_run_ids": [42], "pair_confidence": "coarse"},
        "sidecars": {
            "views": {"present": True, "sha256": _sha256(views), "size": len(views)},
            "inputs": {"present": True, "sha256": _sha256(inputs), "size": len(inputs)},
            "bb": {"present": True, "sha256": _sha256(bb), "size": len(bb)},
            "merge_manifest": {
                "present": True,
                "sha256": _sha256(
                    (ext_dir / "merge_manifest.json").read_bytes(),
                ),
                "size": (ext_dir / "merge_manifest.json").stat().st_size,
            },
        },
        "frames_path": f"external/{EXTERNAL_RUN_ID}/round.jsonl",
        "imported_at": "2026-09-04T00:00:00Z",
        "revisions": [],
    }
    (ext_dir / "meta.json").write_text(
        json.dumps(meta), encoding="utf-8",
    )

    window = {
        "schema_version": "canonical_time_window.v1",
        "timebase_version": "time_alignment.v2",
        "start_ms": 1_000,
        "end_ms": 2_000,
        "duration_ms": 1_000,
        "start_source": "test_start",
        "end_source": "test_end",
        "stats_anchor_status": "missing",
        "stats_time_of_day_ms": None,
        "stats_local_to_utc_mapping": None,
        "warnings": [],
        "window_semantics": "half_open",
    }
    snapshot = {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": 42,
        "canonical_time_window": window,
        "scenario_resolution": {
            "schema_version": "scenario_resolution.v1",
            "scenario_hash": "fixture-hash",
            "display_name": "Fixture Static",
            "registry_version": "scenario_registry.test.v1",
            "manifest_version": "scenario_manifest.test.v1",
            "scenario_profile_ref": "scenario:static.fixture@1",
            "classification_source": "reviewed_registry",
            "classification_confidence": "confirmed",
            "profile_status": "active",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "source_refs": ["review:fixture"],
            "supersedes": [],
            "manifest_status": "active",
            "fixture_ref": "fixture:scenario",
            "review_source_ref": "review:scenario",
            "manifest_reviewed_at": "2026-07-20T00:00:00Z",
            "family_gate_refs": ["gate:family"],
            "aim_family": "static_clicking",
            "subdomains": ["precision"],
            "target_motion": {"model": "static", "target_count_model": "single"},
            "allowed_analyzers": [worker.NATIVE_ANALYSIS_VERSION],
            "allowed_metric_families": ["static_clicking"],
            "claim_ceiling": "family_specific",
            "family_analyzer_dispatch": "allowed",
            "limitations": [],
        },
        "sources": {
            "stats": {
                "artifact_ref": "run:42:stats",
                "availability": "available",
                "path": "/db-private/runs/42/stats.csv",
            },
            "performance": {
                "artifact_ref": "run:42:performance",
                "availability": "available",
                "path": "/db-private/runs/42/performance.perf",
            },
            "video": {
                "artifact_ref": "run:42:video",
                "availability": "unavailable",
                "reason": "video_window_invalid",
                "ownership": "run",
            },
            "external_telemetry": _telemetry_source(ext_dir),
        },
        "trace": None,
    }
    sid = await queue.enqueue("u1", "", "", input_mode="telemetry_multimodal")
    # 真实租约 + session 侧补 kovaak_run_id（persistence 校验的 run_ref 匹配）。
    session_path = file_store._data_root() / "sessions" / f"{sid}.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["kovaak_run_id"] = 42
    session_path.write_text(json.dumps(session), encoding="utf-8")
    assert await queue.claim_next(worker.WORKER_ID) is not None
    job = {
        "id": sid,
        "user_id": "u1",
        "analysis_type": "flicking",
        "input_mode": "telemetry_multimodal",
        "kovaak_run_id": 42,
        "input_snapshot": snapshot,
        "video_path": None,
        "csv_path": "",
        "cm_per_360": 30.0,
        "fov": 90.0,
        "created_at": "2026-09-04 12:00:00",
    }

    def _read_frozen(kind: str, _source: object) -> bytes:
        assert kind in {"stats", "performance"}, f"unexpected frozen read: {kind}"
        return f"{kind}-bytes".encode("utf-8")

    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch(
            "webapp.backend.queue.claim_next",
            new=AsyncMock(return_value=job),
        ))
        stack.enter_context(patch(
            "webapp.backend.queue.heartbeat",
            new=AsyncMock(return_value=True),
        ))
        stack.enter_context(patch(
            "webapp.backend.worker._read_frozen_source_bytes",
            side_effect=_read_frozen,
        ))
        stack.enter_context(patch(
            "kovaak_tracker.csv_parser.parse_stats_bytes",
            return_value=_parsed_stats_stub(),
        ))
        stack.enter_context(patch(
            "kovaak_tracker.performance_parser.parse_performance_bytes",
            return_value=object(),
        ))
        stack.enter_context(patch(
            "webapp.backend.worker._maybe_commit_analysis_evidence",
            side_effect=lambda _job, result, **_kwargs: result,
        ))
        assert await worker.process_one() is True

    session = file_store.read_json(f"sessions/{sid}.json")
    assert session["status"] == "done", session.get("error")
    result = session["result"]
    deterministic = result["deterministic"]
    assert deterministic["status"] == "available"
    # mouse 轨迹通道来自遥测 inputs（6 个合成点）且带来源标注。
    assert deterministic["trajectory"]["point_count"] == 6
    assert MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY in deterministic["limitations"]
    assert deterministic["metrics"]["flick_count"]["value"] == 1.0
    path_length = deterministic["metrics"]["path_length"]
    assert path_length["availability"] == "available"
    assert path_length["limitations"] == [
        MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY,
    ]
