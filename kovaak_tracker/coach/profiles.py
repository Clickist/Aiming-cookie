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
        "label": "发力不足型",
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
]

# signal -> (symptom, physical, training) three-layer root cause.
# Covers every signal advice.advise can emit.
ROOT_CAUSES = {
    "decel_frac high": ("减速段占比过高，在「蹭」", "制动释放不果断", "减速一次到位的意识"),
    "sparc low": ("减速段抖动", "张力释放不平滑（高频成分多）", "减速段控制稳定性"),
    "reverse_ratio high": ("减速段反复修正", "制动方向不稳", "单次制动 + 流体修正"),
    "submovement two-stage": ("flick→急停→独立 micro", "corrective 与 primary 分离", "转流体派（overlapping submovements）"),
    "peak_speed below reference": ("甩得偏慢", "发力不足（手腕主导）", "arm 发力 + speed 场景"),
    "throughput below reference": ("跨距离发力不足", "发力-速度换算弱", "arm 发力 + speed 场景"),
    "linearity high": ("制动不匀", "减速节奏不稳", "匀速制动练习"),
    "path_efficiency low": ("flick 路径绕", "flick 几何不直", "linetrace 直线练习"),
    "peak_position low": ("加速过急", "加速段过猛", "平衡加减速"),
    "peak_position high": ("加速拖沓", "加速不足", "果断加速"),
    "sensitivity high": ("灵敏度偏快", "cm/360 偏小，制动放大手抖", "降 sens 5-10% 实验 + 复测"),
}
