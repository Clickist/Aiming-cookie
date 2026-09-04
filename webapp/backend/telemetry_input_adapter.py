"""Telemetry-only input adaptation: frozen inputs sidecar -> raw-trace points.

遥测-only 运行（无 raw input trace、无视频，如 dev 应用停机期间打的局）在
native 分析器处原本硬性缺轨迹输入；本模块把配对 ExternalTelemetryRun 冻结的
``inputs_NN.jsonl`` 旁车（RE 输入记录器：~1kHz 鼠标 dx/dy raw counts 增量 +
``L_down``/``L_up`` 按钮沿，t 域与 views/round 旁车一致）合成为 native 分析器
消费的等价点列 ``{timestamp_ms, dx, dy, buttons}``——时间戳为纪元毫秒、
单调不减、dx/dy 为原始计数、buttons 为位掩码（bit0=左键），与
``kovaak_snapshot_codec`` 解码出的 Raw Input trace 结构逐字段同形。

时间基（对齐锚）：merge_manifest.alignment.accepted=true 时以
``s_epoch_of_t0`` 为权威锚（manifest t_domain_note：``epoch = s_epoch_of_t0 + t``；
该锚经 xcorr 校验，接受规则要求 |xcorr.s - index_s| <= 0.250s）。锚缺失时回退
round 相对映射（与 telemetry_signals producer 同口径：``t_origin =
meta.time.t_start`` 映射到 canonical window_start），保证与遥测视觉束不相互错位。

单位语义：dx/dy 与 Raw Input trace 同为鼠标 raw counts，量纲一致；但采集
路径与采样不同（RE 记录器 vs Raw Input 事件流），合成轨迹必须携带
``mouse_trajectory_from_external_telemetry`` limitation 标注来源，绝不与原生
轨迹无声混同。

失败语义：本模块对"旁车缺失/指纹不符/对齐未接受/锚不可用"一律返回
(None, [mouse_trajectory_telemetry_inputs_unavailable])，不抛异常——调用方
（worker 装配点）据此走受控降级（unavailable + limitation），绝不让整场
分析 crash。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from . import external_telemetry_store as telemetry_store
from . import file_store
from .kovaak_snapshot_codec import MAX_SNAPSHOT_POINTS

# 合成轨迹的来源标注：出现在 result/metric limitations 里，区分遥测合成轨迹
# 与原生 Raw Input 轨迹。
MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY = "mouse_trajectory_from_external_telemetry"
# 轨迹通道不可用的统一降级码（旁车缺失/指纹不符/对齐未接受/锚不可用/无记录）。
MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE = "mouse_trajectory_telemetry_inputs_unavailable"
# 个别坏行/乱序记录被跳过时的可观测标注（不否定整条通道）。
MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED = "mouse_trajectory_telemetry_records_skipped"

# inputs 记录 btn token -> Raw Input buttons 位掩码（bit0=左, bit1=右, bit2=中）。
_BUTTON_BITS = {
    "L_down": (1, True),
    "L_up": (1, False),
    "R_down": (2, True),
    "R_up": (2, False),
    "M_down": (4, True),
    "M_up": (4, False),
}

_INT32_BOUNDS = (-(2**31), 2**31 - 1)


def _unavailable() -> tuple[list[dict[str, int]] | None, list[str]]:
    return None, [MOUSE_TRAJECTORY_TELEMETRY_INPUTS_UNAVAILABLE]


def _telemetry_source(snapshot: Mapping) -> Mapping | None:
    sources = snapshot.get("sources")
    source = sources.get("external_telemetry") if isinstance(sources, Mapping) else None
    if (
        isinstance(source, Mapping)
        and source.get("availability") == "available"
        and isinstance(source.get("external_run_id"), str)
        and source["external_run_id"]
    ):
        return source
    return None


def _to_ms_converter(
    manifest: Mapping | None,
    meta: Mapping,
    snapshot: Mapping,
):
    """Return a callable source_t_seconds -> int epoch ms, or None when unusable."""
    alignment = (
        manifest.get("alignment") if isinstance(manifest, Mapping) else None
    )
    if not isinstance(alignment, Mapping) or alignment.get("accepted") is not True:
        return None
    anchor = alignment.get("s_epoch_of_t0")
    if (
        isinstance(anchor, (int, float))
        and not isinstance(anchor, bool)
        and math.isfinite(float(anchor))
    ):
        return lambda source_t: round((float(anchor) + source_t) * 1000.0)
    # 锚缺失的回退：round 相对映射（producer 同口径）。t_origin 取导入时登记的
    # 该轮 t_start（与 rounds_index 条目同源）；canonical 窗起点来自快照。
    time_meta = meta.get("time") if isinstance(meta.get("time"), Mapping) else {}
    t_origin = time_meta.get("t_start")
    window = snapshot.get("canonical_time_window")
    start_ms = window.get("start_ms") if isinstance(window, Mapping) else None
    if (
        isinstance(t_origin, (int, float))
        and not isinstance(t_origin, bool)
        and math.isfinite(float(t_origin))
        and isinstance(start_ms, int)
        and not isinstance(start_ms, bool)
    ):
        return (
            lambda source_t: start_ms + round((source_t - float(t_origin)) * 1000.0)
        )
    return None


def telemetry_mouse_trace_points(
    snapshot: Mapping,
) -> tuple[list[dict[str, int]] | None, list[str]]:
    """Synthesize raw-trace-equivalent points from the frozen inputs sidecar.

    Returns ``(points, limitations)``。points 为 None 表示轨迹通道不可用
    （limitations 说明原因）；成功时 limitations 至少含
    ``mouse_trajectory_from_external_telemetry`` 来源标注。不抛异常。
    """
    source = _telemetry_source(snapshot)
    if source is None:
        return _unavailable()
    external_id = str(source["external_run_id"])
    meta = telemetry_store.load_meta(external_id)
    if meta is None:
        return _unavailable()
    origin = meta.get("origin") if isinstance(meta.get("origin"), Mapping) else {}
    try:
        round_number = int(origin.get("round"))
    except (TypeError, ValueError):
        return _unavailable()
    round_file = str(origin.get("round_file") or "").replace("\\", "/").rsplit("/", 1)[-1]
    inputs_name = (
        telemetry_store.sidecar_source_name("inputs", round_file)
        or f"inputs_{round_number:02d}.jsonl"
    )
    data_root = file_store._data_root()
    try:
        payload = (data_root / telemetry_store.sidecar_path(external_id, inputs_name)).read_bytes()
    except OSError:
        return _unavailable()
    sidecars = source.get("sidecars") if isinstance(source.get("sidecars"), Mapping) else {}
    fingerprint = sidecars.get("inputs") if isinstance(sidecars.get("inputs"), Mapping) else None
    if isinstance(fingerprint, Mapping) and fingerprint.get("present") is True:
        # 冻结副本必须与导入清单指纹一致（sha256+size）；不符即不可用
        # （与 _external_telemetry_source 的 sidecars_stale 同一 fail-closed 语义，
        # 但这里按轨迹通道降级处理，不升级成整场失败）。
        if (
            hashlib.sha256(payload).hexdigest() != fingerprint.get("sha256")
            or len(payload) != fingerprint.get("size")
        ):
            return _unavailable()
    try:
        manifest = file_store.read_json(
            telemetry_store.sidecar_path(external_id, "merge_manifest.json"),
        )
    except (OSError, ValueError):
        manifest = None
    to_ms = _to_ms_converter(manifest, meta, snapshot)
    if to_ms is None:
        # 对齐回执不达标或锚不可换算：与遥测视觉 producer 同一 fail-closed 门。
        return _unavailable()

    points: list[dict[str, int]] = []
    limitations = [MOUSE_TRAJECTORY_FROM_EXTERNAL_TELEMETRY]
    skipped = False
    buttons = 0
    previous_ms: int | None = None
    for line in payload.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped = True
            continue
        if not isinstance(record, Mapping):
            skipped = True
            continue
        try:
            source_t = float(record["t"])
            dx = int(record.get("dx") or 0)
            dy = int(record.get("dy") or 0)
        except (KeyError, TypeError, ValueError):
            skipped = True
            continue
        timestamp_ms = to_ms(source_t)
        if (
            not -(2**63) <= timestamp_ms < 2**63
            or not _INT32_BOUNDS[0] <= dx <= _INT32_BOUNDS[1]
            or not _INT32_BOUNDS[0] <= dy <= _INT32_BOUNDS[1]
            or (previous_ms is not None and timestamp_ms < previous_ms)
        ):
            skipped = True
            continue
        for token in record.get("btn") or []:
            bit_spec = _BUTTON_BITS.get(token) if isinstance(token, str) else None
            if bit_spec is None:
                continue
            bit, pressed = bit_spec
            if pressed:
                buttons |= bit
            else:
                buttons &= ~bit
        points.append({
            "timestamp_ms": timestamp_ms,
            "dx": dx,
            "dy": dy,
            "buttons": buttons,
        })
        previous_ms = timestamp_ms
        if len(points) >= MAX_SNAPSHOT_POINTS:
            skipped = True
            break
    if not points:
        return _unavailable()
    if skipped:
        limitations.append(MOUSE_TRAJECTORY_TELEMETRY_RECORDS_SKIPPED)
    return points, limitations
