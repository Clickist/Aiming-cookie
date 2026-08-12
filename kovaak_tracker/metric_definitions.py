"""Chinese display labels for publicly projected analysis metrics.

This module is a display dictionary only. It does not encode expected direction,
diagnosis, causality, or training advice; those claims belong to the versioned
knowledge and evidence contracts.
"""
from __future__ import annotations

from typing import Mapping


METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    # Canonical static-clicking metrics.
    "static_clicking.path_length": {"name": "路径长度", "description": "一次鼠标移动累计经过的原始输入距离"},
    "static_clicking.mean_speed": {"name": "平均速度", "description": "分析窗口内鼠标速度的平均值"},
    "static_clicking.mean_acceleration": {"name": "平均加速度", "description": "分析窗口内鼠标加速度的平均值"},
    "static_clicking.calibrated_path_length": {"name": "校准路径长度", "description": "按当前校准比例换算后的鼠标路径长度"},
    "static_clicking.flick_count": {"name": "Flick 数量", "description": "纳入汇总的 Flick 事件数量"},
    "static_clicking.movement_duration_ms": {"name": "移动时长", "description": "从移动开始到移动结束的时长"},
    "static_clicking.time_to_peak_ms": {"name": "到峰值速度时长", "description": "从移动开始到速度峰值的时长"},
    "static_clicking.accel_duration_ms": {"name": "加速时长", "description": "从移动开始到速度峰值的时长"},
    "static_clicking.decel_duration_ms": {"name": "减速时长", "description": "从速度峰值到移动结束的时长"},
    "static_clicking.settle_duration_ms": {"name": "移动结束后时长", "description": "从移动结束到该 Flick 分析锚点的时长"},
    "static_clicking.decel_frac": {"name": "减速占比", "description": "减速时长占本次移动时长的比例"},
    "static_clicking.peak_position_pct": {"name": "速度峰值位置", "description": "速度峰值出现在整次移动中的相对位置"},
    "static_clicking.peak_speed": {"name": "峰值速度", "description": "一次移动中的最大瞬时速度"},
    "static_clicking.flick_path_length": {"name": "Flick 路径长度", "description": "纳入汇总的 Flick 事件路径长度"},
    "static_clicking.displacement": {"name": "位移", "description": "从移动起点到终点的直线距离"},
    "static_clicking.path_efficiency": {"name": "路径效率", "description": "直线位移与实际路径长度的比值"},
    "static_clicking.straightness": {"name": "直线度", "description": "直线位移与实际路径长度的比值"},
    "static_clicking.reverse_ratio": {"name": "减速阶段再加速比例", "description": "速度峰值后的采样中，速度再次增加所占的比例"},
    "static_clicking.direction_reverse_ratio": {"name": "方向反转比例", "description": "原始路径中方向符号发生反转的距离比例"},
    "static_clicking.corrective_count": {"name": "修正次数", "description": "按离散方向变化识别出的修正段数量"},
    "static_clicking.submovement_count": {"name": "子动作数量", "description": "一次移动中识别出的子动作数量"},
    "static_clicking.trough_depth_ratio": {"name": "速度谷深度比例", "description": "速度谷值相对速度峰值的比例"},
    "static_clicking.submovement_overlap": {"name": "子动作重叠代理值", "description": "当前使用速度谷深度比例作为子动作重叠的代理值"},
    "static_clicking.sparc": {"name": "运动平滑度（SPARC）", "description": "由速度轨迹频谱弧长计算的平滑度指标"},

    # Legacy bare keys emitted by the input-native static analysis. They retain
    # only the local observable meaning and do not imply target-relative facts.
    "flick_count": {"name": "Flick 数量", "description": "纳入汇总的 Flick 事件数量"},
    "mean_speed": {"name": "平均速度", "description": "分析窗口内鼠标速度的平均值"},
    "mean_acceleration": {"name": "平均加速度", "description": "分析窗口内鼠标加速度的平均值"},
    "movement_duration_ms": {"name": "移动时长", "description": "从移动开始到移动结束的时长"},
    "time_to_peak_ms": {"name": "到峰值速度时长", "description": "从移动开始到速度峰值的时长"},
    "accel_duration_ms": {"name": "加速时长", "description": "从移动开始到速度峰值的时长"},
    "decel_duration_ms": {"name": "减速时长", "description": "从速度峰值到移动结束的时长"},
    "settle_duration_ms": {"name": "移动结束后时长", "description": "从移动结束到该 Flick 分析锚点的时长"},
    "decel_frac": {"name": "减速占比", "description": "减速时长占本次移动时长的比例"},
    "peak_position_pct": {"name": "速度峰值位置", "description": "速度峰值出现在整次移动中的相对位置"},
    "peak_speed": {"name": "峰值速度", "description": "一次移动中的最大瞬时速度"},
    "path_length": {"name": "路径长度", "description": "一次鼠标移动累计经过的原始输入距离"},
    "flick_path_length": {"name": "Flick 路径长度", "description": "纳入汇总的 Flick 事件路径长度"},
    "displacement": {"name": "位移", "description": "从移动起点到终点的直线距离"},
    "path_efficiency": {"name": "路径效率", "description": "直线位移与实际路径长度的比值"},
    "straightness": {"name": "直线度", "description": "直线位移与实际路径长度的比值"},
    "reverse_ratio": {"name": "减速阶段再加速比例", "description": "速度峰值后的采样中，速度再次增加所占的比例"},
    "direction_reverse_ratio": {"name": "方向反转比例", "description": "原始路径中方向符号发生反转的距离比例"},
    "corrective_count": {"name": "修正次数", "description": "按离散方向变化识别出的修正段数量"},
    "submovement_count": {"name": "子动作数量", "description": "一次移动中识别出的子动作数量"},
    "trough_depth_ratio": {"name": "速度谷深度比例", "description": "速度谷值相对速度峰值的比例"},
    "submovement_overlap": {"name": "子动作重叠代理值", "description": "当前使用速度谷深度比例作为子动作重叠的代理值"},
    "sparc": {"name": "运动平滑度（SPARC）", "description": "由速度轨迹频谱弧长计算的平滑度指标"},
    "linearity": {"name": "减速线性度", "description": "减速阶段速度变化与线性变化的偏离程度"},
    "peak_speed_deg": {"name": "峰值角速度", "description": "一次移动中的最大瞬时角速度"},
    "throughput": {"name": "吞吐量", "description": "按任务难度与移动时间计算的输出指标"},
    "endpoint_peak": {"name": "末端速度比", "description": "移动末端速度与本次峰值速度的比值"},
    "path_length_deg": {"name": "路径角长度", "description": "移动路径累计经过的角度长度"},

    "continuous_tracking.target_relative_error_px": {"name": "目标相对误差", "description": "鼠标位置与目标中心之间的距离"},
    "continuous_tracking.time_in_radius_ratio": {"name": "目标范围内时间占比", "description": "鼠标位于目标半径内的时间比例"},
    "continuous_tracking.loss_count": {"name": "离开目标次数", "description": "鼠标离开目标半径的次数"},
    "continuous_tracking.loss_duration_ms": {"name": "离开目标时长", "description": "每次离开目标半径的持续时长"},
    "continuous_tracking.reacquisition_latency_ms": {"name": "重新捕获耗时", "description": "离开目标后重新进入目标半径所用的时长"},
    "continuous_tracking.correction_burden": {"name": "修正负担", "description": "跟踪过程中记录到的修正方向变化数量"},
    "continuous_tracking.correction_direction_reversal_count": {"name": "修正方向反转次数", "description": "跟踪过程中修正方向发生反转的次数"},
    "continuous_tracking.smoothness_acceleration_rms": {"name": "加速度均方根", "description": "跟踪过程中加速度的均方根"},
    "continuous_tracking.sparc": {"name": "运动平滑度（SPARC）", "description": "由跟踪速度轨迹频谱弧长计算的平滑度指标"},
    "continuous_tracking.relative_lag_ms": {"name": "相对滞后", "description": "鼠标运动相对目标运动的时间偏移"},
    "continuous_tracking.phase_lag_ms": {"name": "相位滞后", "description": "鼠标运动与目标运动之间的相位时间偏移"},
    "continuous_tracking.coherence": {"name": "跟踪相干性", "description": "鼠标运动与目标运动在频域中的相干程度"},
    "continuous_tracking.velocity_gain": {"name": "速度增益", "description": "鼠标速度与目标速度的比值"},
    "continuous_tracking.alignment_latency_ms": {"name": "采集对齐延迟", "description": "采集数据之间估计出的时间对齐偏移，不代表人的反应时间"},
    "continuous_tracking.observed_change_response_ms": {"name": "观测到的变向响应时长", "description": "目标变向后到记录到鼠标响应之间的时长"},
    "continuous_tracking.human_response_latency_ms": {"name": "人工响应延迟", "description": "当前分析不从跟踪样本或采集对齐结果推断此指标"},
    "continuous_tracking.predictive_lead_ms": {"name": "有条件的领先/滞后时间", "description": "在可用运动可预测性证据条件下测得的鼠标相对目标时间偏移"},

    "target_switching.transition_time_ms": {"name": "切换移动时长", "description": "从上一目标的 Stats 击杀时刻到捕获下一目标的时长"},
    "target_switching.transition_distance_px": {"name": "切换相对位移", "description": "离开与捕获时刻的目标相对误差向量变化量"},
    "target_switching.path_efficiency": {"name": "切换路径效率", "description": "目标相对误差变化量与切换期间相对运动路径长度的比值"},
    "target_switching.settle_duration_ms": {"name": "捕获确认时长", "description": "从首次接触下一目标到满足最短连续接触条件的时长"},
    "target_switching.first_shot_latency_ms": {"name": "首次射击时长", "description": "从捕获下一目标到首次射击事件的时长"},
    "target_switching.first_damage_latency_ms": {"name": "首次伤害时长", "description": "从捕获下一目标到首次伤害事件的时长"},
    "target_switching.carry_over_overshoot_ratio": {"name": "携带过冲比例", "description": "被标记为携带过冲的切换链所占比例"},
    "target_switching.terminal_correction_ratio": {"name": "末端修正比例", "description": "被标记为末端修正的切换链所占比例"},

    "dynamic_clicking.normalized_click_error": {"name": "归一化点击误差", "description": "点击位置与关联目标中心的距离，按目标可见半径归一化"},
    "dynamic_clicking.acquisition_time_ms": {"name": "捕获时长", "description": "从记录的捕获开始时刻到准星首次进入目标可见半径的时长"},
    "dynamic_clicking.relative_velocity": {"name": "相对速度", "description": "鼠标与目标之间相对速度向量的大小"},
    "dynamic_clicking.target_state_accuracy": {"name": "关联目标成功比例", "description": "已关联且具有结果的点击中，记录为成功的比例"},
    "dynamic_clicking.predictive_lead": {"name": "有条件的领先/滞后", "description": "在可用运动可预测性证据条件下，点击误差相对目标运动方向的带符号偏移"},
}


def get_metric_definition(metric_key: str) -> Mapping[str, str] | None:
    """Return the display definition for *metric_key*, or ``None`` if unknown."""
    return METRIC_DEFINITIONS.get(metric_key)


__all__ = ["METRIC_DEFINITIONS", "get_metric_definition"]
