"""Dual-source input fusion: raw-input trace + external telemetry inputs.

融合模块（合同：``.zcode/fusion-spec-1005.md``）。输入 Raw Input trace 点列
（``decode_mouse_snapshot_bytes`` 解码；``timestamp_ms`` 为 epoch 毫秒，与
canonical_time_window 天然同域）与遥测事件，输出两类事实供 family 分析器
消费：

- **按压沿序列**（带来源标注）：遥测 inputs L_down 沿（producer 的 shot
  事件）优先，trace buttons bit0 上升沿回填/交叉验证。两源沿时刻差
  |telemetry − trace| <= ``PRESS_EDGE_MATCH_TOLERANCE_MS`` 记为 matched
  （取遥测时刻，残差 = trace − telemetry 进指标 limitation）；仅单源出现的
  沿按其来源保留。实测（run 54101，133:133 沿）：残差中位 2ms、max 18ms，
  20ms 容差下 1:1 全配对。
- **瞄准运动发起时刻序列**：trace dx/dy × ``deg_per_count``（由
  ``cm_per_360`` 与 DPI 反推 counts_per_360 = cm_per_360 × DPI / 2.54）在
  ``MOTION_WINDOW_MS`` 桶上聚合为角速度流；锚事件后的运动发起 = 首个
  （角速度 >= 阈值 且 速度方向朝目标）桶的起点。

方向朝目标的方位用投影角域（px 偏移 -> atan，与 telemetry_signals 的
rectilinear 投影同一焦距语义；mouse dy 正 = 向下 = pitch 下降，故
pitch 分量取反）。px 线性差在大偏角段会扭曲线性组合的方向判定、产生
数百 ms 量级的长尾发起（run 54095 链锚实测），必须走 atan 角域
（同局 atan 角域 p90 = 108ms）。

模块纯计算、无 IO、结果确定；空输入给空结果（消费方按 unavailable 降级），
参数非法抛 ``InputFusionError``。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


class InputFusionError(ValueError):
    """Invalid fusion input parameters (fail-closed for tampered payloads)."""


FUSION_VERSION = "input_fusion.v1"

# 按压沿两源配对容差：run 54101 实测残差中位 2ms / max 18ms；典型点射局
# 相邻按压间隔 >= 100ms，20ms 不会把两发不同的按压并成一对。
PRESS_EDGE_MATCH_TOLERANCE_MS = 20
# Raw Input buttons 位掩码的左键位（与 kovaak_snapshot_codec 合同一致）。
LEFT_BUTTON_BIT = 1
# 角速度流聚合窗（ms）：~1kHz trace 点聚成 20ms 桶，吸收逐点抖动；窗起点
# 即发起时刻的量化精度。
MOTION_WINDOW_MS = 20
# 运动发起角速度阈值（deg/s）：run 54095 实测全窗角速度 p50=37 / p75=96，
# 50 deg/s 落在持枪微抖（<30）与转火摆臂（>90）之间。
MOTION_ONSET_THRESHOLD_DEG_S = 50.0
# 锚事件后的发起搜索上限（ms）：超过即记无发起（无运动段），不外推。
MOTION_ONSET_MAX_SEARCH_MS = 1500
# px -> 角域方位所需的中心准星兜底（虚拟视口中心，producer 投影合同）。
VIEWPORT_CENTER_X_PX = 960.0
VIEWPORT_CENTER_Y_PX = 540.0


def counts_per_360(cm_per_360: float, dpi: float) -> float:
    """cm/360 + DPI -> 转 360° 所需鼠标计数（counts = 英寸 × DPI）。"""
    cm = float(cm_per_360)
    dots = float(dpi)
    if not math.isfinite(cm) or not math.isfinite(dots) or cm <= 0 or dots <= 0:
        raise InputFusionError("cm_per_360 and dpi must be positive finite numbers")
    return cm * dots / 2.54


def deg_per_count(cm_per_360: float, dpi: float) -> float:
    """每个鼠标计数对应的角度（deg）：360 / counts_per_360。"""
    return 360.0 / counts_per_360(cm_per_360, dpi)


def left_press_edges(
    points: Sequence[Mapping[str, int]],
    *,
    start_ms: int,
    end_ms: int,
) -> list[int]:
    """trace 点列 -> 窗口内左键（bit0）上升沿时刻，升序。

    与既有 ``_raw_left_button_rising_edges`` 同语义：窗内前一点未按、当前
    点已按即一个沿；窗界外的点不参与状态传递（窗首点无法构成沿）。
    """
    edges: list[int] = []
    previous_pressed: bool | None = None
    for point in points:
        time_ms = int(point["timestamp_ms"])
        if not start_ms <= time_ms < end_ms:
            continue
        pressed = bool(int(point["buttons"]) & LEFT_BUTTON_BIT)
        if previous_pressed is False and pressed:
            edges.append(time_ms)
        previous_pressed = pressed
    return edges


def fuse_press_edges(
    trace_edge_ms: Sequence[int],
    telemetry_edge_ms: Sequence[int],
    *,
    tolerance_ms: int = PRESS_EDGE_MATCH_TOLERANCE_MS,
) -> dict:
    """两源按压沿互备融合：遥测优先，trace 回填/交叉验证。

    配对为确定性贪心：按遥测沿时序，各取容差内最近的未用 trace 沿（并列
    取更早的 trace 沿）。matched 对取遥测时刻（来源 ``telemetry``），残差
    = trace − telemetry；仅 trace 出现的沿作为回填（来源 ``trace``）。

    Returns ``{"edges": [...], "validation": {...}}``；edges 按 time_ms 升序，
    edge = ``{time_ms, source, matched, residual_ms}``（residual_ms 仅
    matched 的 telemetry 沿携带）。
    """
    if tolerance_ms < 0:
        raise InputFusionError("tolerance_ms must be non-negative")
    trace_edges = [int(value) for value in trace_edge_ms]
    telemetry_edges = [int(value) for value in telemetry_edge_ms]
    if trace_edges != sorted(trace_edges) or telemetry_edges != sorted(telemetry_edges):
        raise InputFusionError("press edge series must be sorted")
    used = [False] * len(trace_edges)
    residuals: list[int] = []
    edges: list[dict] = []
    matched_count = 0
    for telemetry_time in telemetry_edges:
        best_index: int | None = None
        best_distance: int | None = None
        for index, trace_time in enumerate(trace_edges):
            if used[index]:
                continue
            distance = abs(trace_time - telemetry_time)
            if distance > tolerance_ms:
                continue
            if best_distance is None or distance < best_distance:
                best_index = index
                best_distance = distance
        if best_index is None:
            edges.append({
                "time_ms": telemetry_time,
                "source": "telemetry",
                "matched": False,
                "residual_ms": None,
            })
            continue
        used[best_index] = True
        matched_count += 1
        residual = trace_edges[best_index] - telemetry_time
        residuals.append(residual)
        edges.append({
            "time_ms": telemetry_time,
            "source": "telemetry",
            "matched": True,
            "residual_ms": residual,
        })
    for index, trace_time in enumerate(trace_edges):
        if not used[index]:
            edges.append({
                "time_ms": trace_time,
                "source": "trace",
                "matched": False,
                "residual_ms": None,
            })
    edges.sort(key=lambda edge: (edge["time_ms"], edge["source"]))
    ordered_residuals = sorted(residuals)
    middle = len(ordered_residuals) // 2
    residual_median = (
        float(
            ordered_residuals[middle]
            if len(ordered_residuals) % 2 == 1
            else (
                ordered_residuals[middle - 1] + ordered_residuals[middle]
            ) / 2.0
        )
        if ordered_residuals
        else None
    )
    validation = {
        "matched": matched_count,
        "telemetry_only": len(telemetry_edges) - matched_count,
        "trace_only": len(trace_edges) - matched_count,
        "residual_max_ms": max(residuals) if residuals else None,
        "residual_median_ms": residual_median,
    }
    return {"edges": edges, "validation": validation}


def bearing_toward_target(
    *,
    crosshair_x: float,
    crosshair_y: float,
    target_x: float,
    target_y: float,
    focal_px: float,
) -> tuple[float, float] | None:
    """准星 -> 目标的方位向量（鼠标计数角域，非单位向量）。

    鼠标 dx 正 = 向右 = yaw 增；dy 正 = 向下 = pitch 降。要缩短准星-目标
    角误差，鼠标运动应与 ``(dyaw_err, -dpitch_err)`` 同向，其中
    ``dyaw_err = atan2(target_x - crosshair_x, f)``、
    ``dpitch_err = atan2(crosshair_y - target_y, f)``（目标在上方为正）。
    目标与准星重合（模长为 0）时返回 None（无方向可判）。
    """
    focal = float(focal_px)
    if not math.isfinite(focal) or focal <= 0:
        raise InputFusionError("focal_px must be a positive finite number")
    bearing = (
        math.atan2(float(target_x) - float(crosshair_x), focal),
        -math.atan2(float(crosshair_y) - float(target_y), focal),
    )
    norm = math.hypot(*bearing)
    if norm <= 0.0:
        return None
    return bearing


def build_angular_speed_stream(
    points: Sequence[Mapping[str, int]],
    *,
    deg_per_count_value: float,
    window_ms: int = MOTION_WINDOW_MS,
) -> list[dict[str, float]]:
    """trace 点列 -> 角速度流（固定窗聚合）。

    每个桶 = ``{start_ms, deg_per_s, vx, vy}``：``start_ms`` 桶起点（当前
    点与上一桶起点的间隔 >= window_ms 时开新桶，与点密度无关），角速度为
    桶内位移角度 / 桶实际时长，``(vx, vy)`` 为桶内平均计数速度（counts/ms，
    方向即鼠标运动方向）。点列须时间单调不减（codec 已校验）。
    """
    per_count = float(deg_per_count_value)
    window = int(window_ms)
    if not math.isfinite(per_count) or per_count <= 0:
        raise InputFusionError("deg_per_count must be a positive finite number")
    if window <= 0:
        raise InputFusionError("window_ms must be positive")
    stream: list[dict[str, float]] = []
    window_start: int | None = None
    degrees = 0.0
    dx_sum = 0
    dy_sum = 0
    for point in points:
        time_ms = int(point["timestamp_ms"])
        if window_start is None:
            window_start = time_ms
            degrees = 0.0
            dx_sum = 0
            dy_sum = 0
        elif time_ms - window_start >= window:
            duration_ms = time_ms - window_start
            stream.append({
                "start_ms": float(window_start),
                "deg_per_s": degrees / duration_ms * 1000.0,
                "vx": dx_sum / duration_ms,
                "vy": dy_sum / duration_ms,
            })
            window_start = time_ms
            degrees = 0.0
            dx_sum = 0
            dy_sum = 0
        degrees += math.hypot(int(point["dx"]), int(point["dy"])) * per_count
        dx_sum += int(point["dx"])
        dy_sum += int(point["dy"])
    return stream


def movement_onset_ms(
    stream: Sequence[Mapping[str, float]],
    *,
    anchor_ms: int,
    bearing: Sequence[float],
    threshold_deg_s: float = MOTION_ONSET_THRESHOLD_DEG_S,
    max_search_ms: int = MOTION_ONSET_MAX_SEARCH_MS,
) -> int | None:
    """锚事件后首个（角速度 >= 阈值 且 方向朝目标）桶的起点；无则 None。

    只看 ``start_ms >= anchor_ms`` 的桶（运动完全在锚之后；锚时已在进行的
    摆臂会被首个后继桶在 <= window_ms 内捕获）。阈值取 >=（边界值算发起）；
    方向判定为鼠标速度单位向量与方位向量的点积 > 0。轴承为零向量由调用方
    先行排除（``bearing_toward_target`` 返回 None 的情形）。
    """
    threshold = float(threshold_deg_s)
    search_limit = int(max_search_ms)
    if not math.isfinite(threshold) or threshold < 0:
        raise InputFusionError("threshold_deg_s must be a non-negative finite number")
    if search_limit < 0:
        raise InputFusionError("max_search_ms must be non-negative")
    bearing_x = float(bearing[0])
    bearing_y = float(bearing[1])
    bearing_norm = math.hypot(bearing_x, bearing_y)
    if bearing_norm <= 0.0:
        return None
    bearing_x /= bearing_norm
    bearing_y /= bearing_norm
    for bucket in stream:
        start_ms = int(bucket["start_ms"])
        if start_ms < anchor_ms:
            continue
        if start_ms - anchor_ms > search_limit:
            break
        if float(bucket["deg_per_s"]) < threshold:
            continue
        vx = float(bucket["vx"])
        vy = float(bucket["vy"])
        velocity_norm = math.hypot(vx, vy)
        if velocity_norm <= 0.0:
            continue
        if (vx / velocity_norm) * bearing_x + (vy / velocity_norm) * bearing_y > 0.0:
            return start_ms
    return None


__all__ = [
    "FUSION_VERSION",
    "LEFT_BUTTON_BIT",
    "MOTION_ONSET_MAX_SEARCH_MS",
    "MOTION_ONSET_THRESHOLD_DEG_S",
    "MOTION_WINDOW_MS",
    "PRESS_EDGE_MATCH_TOLERANCE_MS",
    "VIEWPORT_CENTER_X_PX",
    "VIEWPORT_CENTER_Y_PX",
    "InputFusionError",
    "bearing_toward_target",
    "build_angular_speed_stream",
    "counts_per_360",
    "deg_per_count",
    "fuse_press_edges",
    "left_press_edges",
    "movement_onset_ms",
]
