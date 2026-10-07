"""S3 装样缝聚焦测试：tracking 运动学接进 payload（player_motion_samples）。

raw input trace 角位置（cm_per_360 + DPI 校准）换 px 域准星运动样本后装进
continuous_tracking payload（供分析器的 crosshair_velocity/频谱/修正线性
插值）。trace 取数源 = ``_tracking_frozen_trace_source``：快照 trace 冻结
指纹优先，缺件回落 kovaak_run 的 runs 目录冻结 trace bin。本文件验证：
- 取数源解析（runs 目录回退为核心新路径；快照 trace 冻结契约保留）；
- 缺件降级（无 trace 源 / 无校准 / 解码失败 / 窗内无点）→ payload 无该字段，
  分析器行为与既有逐字节不变（不拖垮整场）；
- 有 trace + 校准 → player_motion_samples 出现、时间戳窗内严格递增（codec
  允许的同毫秒按钮沿折叠保留最后一个点）、px 值与 f*tan 换算一致（方向同
  投影：鼠标右移 → x 增、鼠标上移 → y 减）；
- ``SourceSnapshotChangedError`` 原样上抛（fail-closed 契约，不吞）。
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kovaak_tracker.input_fusion import deg_per_count
from kovaak_tracker.telemetry_signals import _focal_length_px
from webapp.backend import config, worker_family_analysis
from webapp.backend.kovaak_run_store import write_mouse_snapshot
from webapp.backend.worker_source_validation import SourceSnapshotChangedError

ANALYSIS_ID = 931
ANALYSIS_REF = f"analysis:{ANALYSIS_ID}"
RUN_ID = 42

CM_PER_360 = 27.0
DPI = 800
FOV_DEG = 90.0


def _tan_deg(deg: float) -> float:
    return math.tan(math.radians(deg))


def _window() -> dict:
    return {
        "schema_version": "canonical_time_window.v1",
        "start_ms": 0,
        "end_ms": 1000,
        "duration_ms": 1000,
        "window_semantics": "half_open",
        "timebase_version": "time_alignment.test.v1",
        "start_source": "fixture",
        "end_source": "fixture",
        "warnings": [],
    }


def _resolution() -> dict:
    return {
        "schema_version": "scenario_resolution.v1",
        "aim_family": "continuous_tracking",
        "target_motion": {"model": "unknown", "target_count_model": "unknown"},
    }


def _snapshot(*, with_trace: bool = True) -> dict:
    snapshot = {
        "schema_version": "analysis_input_snapshot.v3",
        "run_id": RUN_ID,
        "canonical_time_window": _window(),
        "scenario_resolution": _resolution(),
        "sources": {},
    }
    if with_trace:
        # raw input trace 源（字节读取在用例里 patch）。
        snapshot["trace"] = {
            "artifact_ref": f"run:{RUN_ID}:trace",
            "path": "/db-private/runs/42/trace.bin",
            "availability": "available",
            "format_version": 1,
        }
    return snapshot


def _job(*, with_trace: bool = True, with_calibration: bool = True) -> dict:
    job = {
        "id": ANALYSIS_ID,
        "kovaak_run_id": RUN_ID,
        "input_snapshot": _snapshot(with_trace=with_trace),
    }
    if with_calibration:
        job["calibration_snapshot"] = {
            "cm_per_360": {"value": CM_PER_360, "source": "fixture"},
        }
    return job


def _write_run_trace() -> Path:
    """在测试 DATA_ROOT 造 kovaak_run 冻结 trace bin（runs/{RUN_ID}/）。"""
    run_dir = Path(config.DATA_ROOT) / "runs" / str(RUN_ID)
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace-700dfixture.bin"
    write_mouse_snapshot(trace_path, _trace_points())
    return trace_path


def _crosshair(step_ms: int = 4) -> list[dict]:
    return [
        {"canonical_time_ms": time_ms, "x": 960.0, "y": 540.0, "confidence": 1.0}
        for time_ms in range(0, 1001, step_ms)
    ]


def _tracking_visual() -> dict:
    return {
        "analysis_ref": ANALYSIS_REF,
        "canonical_time_window": _window(),
        "quality": {
            "status": "accepted",
            "enabled_metric_families": ["tracking"],
            "limitations": [],
        },
        "local_samples": {
            "crosshair.position": _crosshair(),
            "target.1.position": [
                {
                    "canonical_time_ms": time_ms,
                    "x": 960.0,
                    "y": 540.0,
                    "visible_radius": 10.0,
                    "confidence": 1.0,
                }
                for time_ms in range(0, 1001, 4)
            ],
        },
        "track_summaries": [
            {"track_ref": f"{ANALYSIS_REF}:target-track:1", "limitations": []},
        ],
        "event_bundle": {
            "schema_version": "event_bundle.v1",
            "analysis_ref": ANALYSIS_REF,
            "events": [],
            "outcome_associations": [],
        },
        "signal_bundle": {"channels": [{"channel_key": "crosshair.position_x"}]},
        "visual_runtime_selector": {
            "resolution": [1920, 1080],
            "fov": FOV_DEG,
        },
    }


def _trace_points() -> list[dict]:
    """含窗内移动、同毫秒按钮沿帧（时间戳重复）与窗外点。"""
    return [
        {"timestamp_ms": 0, "dx": 100, "dy": -50, "buttons": 0},
        {"timestamp_ms": 200, "dx": 200, "dy": 0, "buttons": 1},
        # 同毫秒第二点（按钮沿帧）：codec 只校验非递减，装样须折叠。
        {"timestamp_ms": 200, "dx": 50, "dy": 10, "buttons": 0},
        {"timestamp_ms": 400, "dx": 0, "dy": 0, "buttons": 0},
        # 窗外点：不参与。
        {"timestamp_ms": 5000, "dx": 999, "dy": 999, "buttons": 0},
    ]


def _spy_real_analyzer(captured: list):
    """捕获 payload 且仍调用真实分析器（断言装样 + 分析不 raise）。"""
    from kovaak_tracker import tracking_analysis

    real = tracking_analysis.analyze_continuous_tracking_v1

    def wrapper(payload):
        captured.append(payload)
        return real(payload)

    return patch.object(tracking_analysis, "analyze_continuous_tracking_v1", wrapper)


def _run(
    job: dict,
    visual: dict,
    *,
    captured: list | None = None,
    decode=None,
    decode_error: Exception | None = None,
    frozen_sources: list | None = None,
):
    from contextlib import ExitStack

    def _capture_read_frozen(kind, source):
        if frozen_sources is not None:
            frozen_sources.append((kind, source))
        return b"trace"

    context_managers = [
        patch(
            "webapp.backend.worker_family_analysis._parse_frozen_stats_for_visual",
            return_value=SimpleNamespace(dpi=DPI),
        ),
        patch(
            "webapp.backend.worker._read_frozen_source_bytes",
            side_effect=_capture_read_frozen,
        ),
        patch(
            "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
            side_effect=decode_error,
            return_value=_trace_points() if decode is None else decode,
        ),
    ]
    if captured is not None:
        context_managers.append(_spy_real_analyzer(captured))
    with ExitStack() as stack:
        for manager in context_managers:
            stack.enter_context(manager)
        return worker_family_analysis.run_continuous_tracking_analysis(job, visual)


# ---- 有 trace + 校准：样本出现、窗内严格递增、px 值同 f*tan 换算 ----


def test_player_motion_samples_present_in_window_ordered_and_px_converted():
    # 新取数源形态：快照无 trace，runs 目录冻结 trace bin 回退供源。
    trace_path = _write_run_trace()
    job = _job(with_trace=False)
    visual = _tracking_visual()
    captured: list = []
    frozen_sources: list = []

    result = _run(job, visual, captured=captured, frozen_sources=frozen_sources)

    # 取数源确实是 runs 目录的冻结 trace bin（回退路径端到端供源；
    # 装样缝与融合 alignment 各读一次，首条即装样缝读取）。
    kind, source = frozen_sources[0]
    assert kind == "raw_input"
    assert source["path"] == str(trace_path)
    assert source["availability"] == "available"

    samples = captured[0]["player_motion_samples"]
    # 时间戳：窗内、int、严格递增（同毫秒按钮沿帧折叠）。
    times = [sample["canonical_time_ms"] for sample in samples]
    assert times == [0, 200, 400]
    assert all(
        isinstance(time_ms, int) and 0 <= time_ms < 1000 for time_ms in times
    )
    # px 值与 f*tan 换算一致；方向同投影：鼠标右移 → x 增、鼠标上移 → y 减。
    dpc = deg_per_count(CM_PER_360, float(DPI))
    focal = _focal_length_px(FOV_DEG)
    assert samples[0]["x"] == pytest.approx(focal * _tan_deg(100 * dpc))
    assert samples[0]["y"] == pytest.approx(focal * _tan_deg(-50 * dpc))
    assert samples[0]["x"] > 0.0
    assert samples[0]["y"] < 0.0
    # 同毫秒折叠保留最后一个点：累计位移含两次移动（100+200+50 counts）。
    assert samples[1]["x"] == pytest.approx(focal * _tan_deg(350 * dpc))
    assert samples[1]["y"] == pytest.approx(focal * _tan_deg(-40 * dpc))
    # player_motion_status 保持既有 unavailable 语义不动。
    assert captured[0]["player_motion_status"] == "unavailable_fixed_viewport_center"
    # 真实分析器接受该组合并跑完。
    assert result["schema_version"] == "continuous_tracking_analysis.v1"


def test_player_motion_still_reads_snapshot_frozen_trace_when_available():
    # 快照 trace available：冻结指纹契约优先，路径原样透传（行为不变）。
    job = _job(with_trace=True)
    visual = _tracking_visual()
    captured: list = []
    frozen_sources: list = []

    _run(job, visual, captured=captured, frozen_sources=frozen_sources)

    kind, source = frozen_sources[0]
    assert (kind, source["path"]) == (
        "raw_input",
        "/db-private/runs/42/trace.bin",
    )
    assert captured[0]["player_motion_samples"]


# ---- 核心新用例：input_snapshot 缺席但 runs 目录有 trace → 照样产出 ----


def test_player_motion_inputs_without_input_snapshot_use_run_dir_trace():
    trace_path = _write_run_trace()
    # 真实遥测路径的队列 job 形态：input_snapshot 缺席，run 标识在 job 上。
    job = {
        "id": ANALYSIS_ID,
        "kovaak_run_id": RUN_ID,
        "calibration_snapshot": {
            "cm_per_360": {"value": CM_PER_360, "source": "fixture"},
        },
    }
    frozen_sources: list = []

    with patch(
        "webapp.backend.worker_family_analysis._parse_frozen_stats_for_visual",
        return_value=SimpleNamespace(dpi=DPI),
    ), patch(
        "webapp.backend.worker._read_frozen_source_bytes",
        side_effect=lambda kind, source: (
            frozen_sources.append((kind, source)) or b"trace"
        ),
    ), patch(
        "webapp.backend.kovaak_run_store.decode_mouse_snapshot_bytes",
        return_value=_trace_points(),
    ):
        inputs = worker_family_analysis._tracking_player_motion_inputs(
            job, _tracking_visual(), _window(),
        )

    assert frozen_sources[0][1]["path"] == str(trace_path)
    times = [
        sample["canonical_time_ms"]
        for sample in inputs["player_motion_samples"]
    ]
    assert times == [0, 200, 400]


# ---- 缺件降级：payload 无该字段（分析器行为与既有逐字节不变）----


def _assert_no_player_motion_samples(captured: list) -> None:
    assert "player_motion_samples" not in captured[0]
    assert captured[0]["player_motion_status"] == "unavailable_fixed_viewport_center"


def test_player_motion_absent_without_any_trace_source():
    # 快照无 trace 且 runs 目录无 trace bin：无任何取数源 → 缺件降级。
    job = _job(with_trace=False)
    captured: list = []

    _run(job, _tracking_visual(), captured=captured)

    _assert_no_player_motion_samples(captured)


def test_player_motion_absent_without_run_identity():
    # 无 run 标识（job 与快照都没有）：无法定位 runs 目录 → 缺件降级。
    job = _job(with_trace=False)
    del job["kovaak_run_id"]
    job["input_snapshot"].pop("run_id")
    captured: list = []

    _run(job, _tracking_visual(), captured=captured)

    _assert_no_player_motion_samples(captured)


def test_player_motion_absent_with_ambiguous_run_dir_traces():
    # runs 目录出现多个 trace bin（非唯一冻结产物）：不猜测，缺件降级。
    _write_run_trace()
    (Path(config.DATA_ROOT) / "runs" / str(RUN_ID) / "trace-second.bin").write_bytes(
        b"not-a-real-trace",
    )
    job = _job(with_trace=False)
    captured: list = []

    _run(job, _tracking_visual(), captured=captured)

    _assert_no_player_motion_samples(captured)


def test_player_motion_absent_without_calibration():
    # runs 目录有 trace、唯一缺校准 → 缺件降级（校准取数源保持不动）。
    _write_run_trace()
    job = _job(with_trace=False, with_calibration=False)
    captured: list = []

    _run(job, _tracking_visual(), captured=captured)

    _assert_no_player_motion_samples(captured)


def test_player_motion_absent_on_decode_failure():
    _write_run_trace()
    job = _job(with_trace=False)
    captured: list = []

    _run(
        job,
        _tracking_visual(),
        captured=captured,
        decode_error=ValueError("invalid raw input snapshot"),
    )

    _assert_no_player_motion_samples(captured)


def test_player_motion_absent_when_trace_points_outside_window():
    _write_run_trace()
    job = _job(with_trace=False)
    captured: list = []
    outside = [
        {"timestamp_ms": 5000, "dx": 100, "dy": 50, "buttons": 0},
        {"timestamp_ms": 5100, "dx": 100, "dy": 50, "buttons": 0},
    ]

    _run(job, _tracking_visual(), captured=captured, decode=outside)

    _assert_no_player_motion_samples(captured)


# ---- SourceSnapshotChangedError 原样上抛（fail-closed 契约）----


def test_player_motion_source_snapshot_changed_raises():
    job = _job()

    def _raise_changed(_snapshot):
        raise SourceSnapshotChangedError("stats source changed mid-analysis")

    with patch(
        "webapp.backend.worker_family_analysis._parse_frozen_stats_for_visual",
        side_effect=_raise_changed,
    ):
        with pytest.raises(SourceSnapshotChangedError):
            worker_family_analysis.run_continuous_tracking_analysis(
                job, _tracking_visual(),
            )
