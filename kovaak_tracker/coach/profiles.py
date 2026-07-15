"""Archetype definitions + root-cause mapping (DATA, not logic).

Edit here to tune the coach's vocabulary. Theory anchors in
docs/aim-kinematics-research.md. Signal keys must match advice.py Finding.signal.
"""
from __future__ import annotations

# Each archetype: id, label, weighted signal conditions (signal -> weight).
# score = sum(weights of hit signals) / sum(all weights).
ARCHETYPES = [
    {
        "id": "long_decel",
        "label": "急加速-长减速型",
        "conditions": {"decel_frac high": 1.0, "peak_position low": 0.5},
    },
    {
        "id": "decel_jitter",
        "label": "减速抖动型",
        "conditions": {"sparc low": 1.0, "reverse_ratio high": 0.7},
    },
    {
        "id": "two_stage",
        "label": "两段式型",
        "conditions": {"submovement two-stage": 1.0},
    },
    {
        "id": "underpowered",
        "label": "参考速度效率偏低型",
        "conditions": {
            "peak_speed below reference": 1.0,
            "throughput below reference": 1.0,
        },
    },
    {
        "id": "inefficient_path",
        "label": "路径低效型",
        "conditions": {"path_efficiency low": 1.0},
    },
    {
        "id": "fluid_precise",
        "label": "流体精度型",
        "conditions": {},  # positive profile: matched when no negative signals fire
    },
    # ============================================================
    # Tracking archetypes (spec 2026-07-05-tracking-coach-design §4.1)
    # signal keys use space-separated form to match advice_tracking.finding.signal
    # ============================================================
    {
        "id": "tension_locked",
        "label": "高 PTC 观察型",
        "conditions": {"ptc high": 1.0, "accuracy low": 0.5},
    },
    {
        "id": "reactive_loser",
        "label": "反应滞后型",
        "conditions": {"loss count high": 1.0, "off target long": 0.7},
    },
    {
        "id": "precision_borderline",
        "label": "临界精度型",
        "conditions": {"avg error high": 1.0},
    },
    {
        "id": "speed_overmatched",
        "label": "速度超纲型",
        "conditions": {"speed mismatch high": 1.0, "accel mismatch high": 0.7},
    },
    {
        "id": "fluid_tracker",
        "label": "流体追踪型",
        "conditions": {},  # positive profile: matched when no negative signals fire
    },
]

# signal -> (symptom, physical, training) three-layer root cause.
# Covers every signal advice.advise can emit.
ROOT_CAUSES = {
    "decel_frac high": ("减速段占比过高，在「蹭」", "输入数据能观察到减速段偏长，但不能单独证明是制动释放不果断", "减速一次到位的意识"),
    "decel_frac low": ("减速段占比过低，撞墙式制动", "输入数据能观察到减速段被压缩，但不能单独证明减速不足或制动粗暴", "练匀减速，把减速段当独立动作"),
    "sparc low": ("减速速度轮廓有较多快速波动", "输入数据支持减速轮廓不够连续，但不能单独证明握持张力或其他身体原因", "减速段控制稳定性"),
    "reverse_ratio high": ("减速段反复修正", "输入数据能观察到反向修正偏多，但不能单独证明制动方向不稳的身体原因", "单次制动 + 流体修正"),
    "submovement two-stage": ("flick→急停→独立 micro", "输入数据能观察到 corrective 与 primary 分离，但不能单独证明其由某种身体原因造成", "转流体派（overlapping submovements）"),
    "peak_speed below reference": ("峰值速度低于当前参考", "具体动作原因未被输入数据直接测量", "在可控精度下逐步提高速度"),
    "throughput below reference": ("速度-精度综合效率低于当前参考", "输入数据能观察到参考效率偏低，但不能单独证明具体动作原因", "在可比场景中练速度-精度转换"),
    "linearity high": ("制动不匀", "输入数据能观察到减速节奏不匀，但不能单独证明具体的身体控制原因", "匀速制动练习"),
    "path_efficiency low": ("flick 路径绕", "输入数据能观察到 flick 路径效率偏低，但不能单独证明动作不直的身体原因", "linetrace 直线练习"),
    "peak_position low": ("峰值位置偏前", "输入数据能观察到速度峰值出现较早，但不能单独证明加速过猛或减速段拖沓", "平衡加减速"),
    "peak_position high": ("峰值位置偏后", "输入数据能观察到速度峰值出现较晚，但不能单独证明加速不足或启动拖沓", "果断加速"),
    "sensitivity high": ("当前 cm/360 较小", "输入数据能记录当前灵敏度，但不能单独证明它是否造成控制问题", "只做可撤销的 sens 实验并复测"),
    # --- tracking signals (spec §4.2) ---
    "accuracy low":          ("命中率低",         "输入数据能观察到命中率偏低，但不能单独证明是速度匹配或微调精度的哪一项造成", "pasu + VT Multiclick 落点"),
    "loss count high":       ("频繁脱靶",         "输入数据能观察到脱靶次数偏多，但不能单独证明是目标变向读取或速度匹配造成", "VT reactive tracking"),
    "off target long":       ("脱靶后回位慢",     "输入数据能观察到脱靶持续时间偏长，但不能单独证明是视觉重新锁定延迟", "VT evasive + Clover Raw Control"),
    "avg error high":        ("误差大",           "输入数据能观察到平均误差偏大，但不能单独证明具体的准星控制原因", "VT precise tracking + crosshair gap 意识"),
    "speed mismatch high":   ("高速段失手",       "输入数据能观察到高速段误差增加，但不能单独证明 speed matching 的身体或视觉原因", "VT control tracking"),
    "accel mismatch high":   ("变向段失手",       "输入数据能观察到加速度变化段误差增加，但不能单独证明 reactive tracking 的具体原因", "VT reactive tracking"),
    "ptc high":              ("可能张力偏大",     "输入数据只能显示 PTC 偏高；发力密集或张力偏大是未被 EMG 验证的假设", "暴露疗法 + 侧向挤压"),
}
