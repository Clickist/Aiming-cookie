"""Plotly figures for the coach report (frontend-agnostic: returns go.Figure
objects, never binds Streamlit)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from .diagnosis import CoachDiagnosis

# radar dims: (key, label, inverted?). inverted = lower-is-better (so we flip).
_RADAR_DIMS = [
    ("decel_frac", "减速占比", True),
    ("linearity", "制动线性度", True),
    ("sparc", "减速平滑", False),
    ("reverse_ratio", "反向加速", True),
    ("path_efficiency", "路径效率", False),
    ("peak_speed_deg", "峰值速度", False),
]


def build_figures(diagnosis: CoachDiagnosis) -> dict:
    return {
        "profile_card": _profile_card(diagnosis),
        "radar": _radar(diagnosis),
        "decel_curve": _decel_curve(diagnosis),
        "comparison": _comparison(diagnosis),
        "issue_list": _issue_list(diagnosis),
    }


def _med(summary, key):
    v = summary.get(key)
    if isinstance(v, dict):
        v = v.get("med")
    return v if isinstance(v, (int, float)) and not _isnan(v) else None


def _isnan(x):
    return isinstance(x, float) and x != x


def _profile_card(diagnosis):
    p = diagnosis.profile
    tags = "、".join(p.secondary_tags) if p.secondary_tags else "无"
    return (f"画像：{p.label}（匹配度 {p.confidence:.2f}）\n"
            f"次要特征：{tags}")


def _radar(diagnosis):
    fig = go.Figure()
    cats = [d[1] for d in _RADAR_DIMS]
    self_vals = []
    for key, _label, inv in _RADAR_DIMS:
        v = _med(diagnosis.summary, key)
        self_vals.append(_normalize(v, key, inv))
    fig.add_trace(go.Scatterpolar(r=self_vals, theta=cats, fill="toself", name="你"))
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 1])), showlegend=False,
                      title="指标雷达（归一化，外=好）")
    return fig


def _normalize(v, key, inv):
    """Map a metric to 0-1 by rough health band (spec §6). None -> 0."""
    if v is None:
        return 0.0
    bands = {
        "decel_frac": (0.50, 0.65), "linearity": (0.0, 0.12),
        "sparc": (-4.0, 0.0), "reverse_ratio": (0.0, 0.18),
        "path_efficiency": (0.85, 1.0), "peak_speed_deg": (100, 140),
    }
    lo, hi = bands.get(key, (0.0, 1.0))
    t = (v - lo) / (hi - lo) if hi != lo else 0.5
    t = max(0.0, min(1.0, t))
    return (1 - t) if inv else t


def _decel_curve(diagnosis):
    """Ideal min-jerk decel half vs placeholder self-curve (synthetic, since
    per-flick trajectory isn't in summary). Annotated for coaching."""
    fig = go.Figure()
    tau = np.linspace(0, 1, 50)
    mj = 30 * tau ** 2 * (1 - tau) ** 2 / max(30 * 0.5 ** 2 * 0.5 ** 2, 1e-9)
    fig.add_trace(go.Scatter(x=tau, y=mj, name="理想 min-jerk", mode="lines"))
    fig.update_layout(title="减速段速度曲线（理想 vs 实际见录像）",
                      xaxis_title="归一化时间", yaxis_title="归一化速度")
    return fig


def _comparison(diagnosis):
    if not diagnosis.comparison:
        return go.Figure().update_layout(title="对比（无参考数据）")
    rows = diagnosis.comparison
    metrics = [r["metric"] for r in rows]
    self_v = [r["self"] for r in rows]
    ref_v = [r["ref"] for r in rows]
    fig = go.Figure(data=[
        go.Bar(name="你", x=metrics, y=self_v, marker_color="#636"),
        go.Bar(name="参考", x=metrics, y=ref_v, marker_color="#aaa"),
    ])
    fig.update_layout(barmode="group", title="指标对比（self vs 参考）")
    return fig


def _issue_list(diagnosis):
    lines = []
    for i in diagnosis.issues:
        lines.append(f"#{i.priority} [{i.severity}] {i.signal} — {i.priority_reason}")
        for rc in i.root_causes:
            lines.append(f"    {rc.level}: {rc.text}")
        for p in i.prescriptions:
            lines.append(f"    → {p.scenario}: {p.reason}")
    return "\n".join(lines) if lines else "无明显问题"
