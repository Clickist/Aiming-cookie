"""Aim advice rule engine: flick fair-metric summary -> diagnosis + prescriptions.

Rules derived from ``docs/aim-kinematics-research.md`` (min-jerk golden standard
+ Becker 2020 deceleration findings + Voltaic/KovaaK community consensus). Each
finding pairs a plain-language diagnosis with concrete scenario prescriptions.

The engine is summary-driven and source-agnostic: feed it the fair-metric
summary from :func:`pan_tracker.analyze_flicking_reference` (or any equivalent
producing the same ``{metric: {med, p75, p90}}`` shape) for self and optionally
a reference player.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Prescription:
    """A training scenario + why it helps this finding."""
    scenario: str
    reason: str


@dataclass
class Finding:
    """One diagnosed issue: signal, severity, statement, prescriptions."""
    signal: str            # e.g. "decel_frac high"
    severity: str          # "info" | "watch" | "fix"
    diagnosis: str         # plain-language statement
    prescriptions: list[Prescription] = field(default_factory=list)


# Health bands from docs/aim-kinematics-research.md §2
THRESHOLDS = {
    "decel_frac_high": 0.65,
    "decel_frac_low": 0.40,
    "linearity_high": 0.13,
    "sparc_low": -5.0,        # SPARC < this (more negative) = decel not smooth (§6.1, calibrate on real data)
    "reverse_high": 0.20,
    "two_stage_overlap": 0.30,  # corrective/primary overlap < this = discrete two-stage (§6.2)
    "peak_pos_low": 30.0,
    "peak_pos_high": 60.0,
    "path_eff_low": 0.85,
    "peak_below_ref": 0.70,   # self peak / ref peak
    "throughput_below_ref": 0.70,  # self TP / ref TP (§6.3)
    "sens_high_cm360": 25.0,  # < this = sensitivity too high (too fast)
}


def _med(summary: dict, metric: str) -> Optional[float]:
    """Pull a median from a summary that may store {med,p75,p90} or a scalar."""
    v = summary.get(metric)
    if isinstance(v, dict):
        v = v.get("med")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def advise(
    self_summary: dict,
    reference_summary: dict | None = None,
    cm_per_360: float | None = None,
) -> list[Finding]:
    """Rule engine: fair-metric summary -> diagnosis + prescriptions.

    ``self_summary`` is your fair-metric summary (from
    :func:`analyze_flicking_reference` or equivalent). ``reference_summary`` is
    an optional high-level player's summary for relative comparison.
    ``cm_per_360`` enables a sensitivity note when it is outside the mainstream
    28-43 cm/360 band.
    """
    f: list[Finding] = []

    decfrac = _med(self_summary, "decel_frac")
    if decfrac is not None:
        if decfrac > THRESHOLDS["decel_frac_high"]:
            f.append(Finding(
                "decel_frac high", "fix",
                f"减速段占整个 flick 的 {decfrac*100:.0f}%（健康 50-65%）——"
                "急加速 + 长减速型，冲得快但减速在'蹭'，效率低。",
                [Prescription("pasu", "练完整的加速→减速，减速果断一次到位"),
                 Prescription("1w4ts Voltaic", "acc 90%+，逼你把单次 flick 加减速打完整")],
            ))
        elif decfrac < THRESHOLDS["decel_frac_low"]:
            f.append(Finding(
                "decel_frac low", "watch",
                f"减速段只占 {decfrac*100:.0f}%，减速不足 / 撞墙式制动。",
                [Prescription("pasu", "练匀减速，把减速段当一次独立动作")],
            ))

    linearity = _med(self_summary, "linearity")
    if linearity is not None and linearity > THRESHOLDS["linearity_high"]:
        f.append(Finding(
            "linearity high", "fix",
            f"减速段速度曲线偏离匀减速直线 {linearity:.2f}（健康 <0.12）——"
            "制动不匀（恒定制动线性度差）。注：这度量的是制动节奏，不是抖动；"
            "抖动看 SPARC。",
            [Prescription("pasu", "clean lines，减速段匀速制动一次到位"),
             Prescription("1w4ts 30% larger", "减速段精度专项")],
        ))

    sparc = _med(self_summary, "sparc")
    if sparc is not None and sparc < THRESHOLDS["sparc_low"]:
        f.append(Finding(
            "sparc low", "fix",
            f"减速段平滑度 SPARC={sparc:.1f}（健康 >-5）——张力释放抖动、频域高频"
            "成分多。这才是'减速抖动'的理论正解（SPARC 频域、无量纲、跨速度公平）。",
            [Prescription("pasu", "clean lines，减速段走平滑钟形，张力匀速释放"),
             Prescription("1w4ts 30% larger", "减速段精度专项")],
        ))

    reverse = _med(self_summary, "reverse_ratio")
    if reverse is not None and reverse > THRESHOLDS["reverse_high"]:
        f.append(Finding(
            "reverse_ratio high", "fix",
            f"减速段有 {reverse*100:.0f}% 的帧在反向加速（锯齿 / 反复修正），"
            "不是单调制动。",
            [Prescription("pasu", "转流体派：减速段即微调，别 readjust"),
             Prescription("Multiclick", "落点精度，减少二次修正")],
        ))

    overlap = _med(self_summary, "submovement_overlap")
    if overlap is not None and overlap < THRESHOLDS["two_stage_overlap"]:
        f.append(Finding(
            "submovement two-stage", "watch",
            f"corrective 与 primary submovement 重叠度 {overlap:.2f}（低=离散两段式）——"
            "flick→急停→独立 micro 修正，有延迟（Bardpill 模式，§6.2）。",
            [Prescription("pasu", "转流体派：corrective 与 primary 重叠（overlapping submovements），减速段即微调"),
             Prescription("Multiclick", "落点精度，减少二次修正")],
        ))

    peak_pos = _med(self_summary, "peak_position_pct")
    if peak_pos is not None:
        if peak_pos < THRESHOLDS["peak_pos_low"]:
            f.append(Finding(
                "peak_position low", "watch",
                f"峰位 {peak_pos:.0f}%（偏前），加速过急、减速段拖沓。",
                [Prescription("pasu", "平衡加减速，把峰往中段靠")],
            ))
        elif peak_pos > THRESHOLDS["peak_pos_high"]:
            f.append(Finding(
                "peak_position high", "watch",
                f"峰位 {peak_pos:.0f}%（偏后），加速拖沓、来不及减速。",
                [Prescription("Tile Frenzy", "练果断加速、提速")],
            ))

    path_eff = _med(self_summary, "path_efficiency")
    if path_eff is not None and path_eff < THRESHOLDS["path_eff_low"]:
        f.append(Finding(
            "path_efficiency low", "fix",
            f"flick 路径直线效率 {path_eff:.2f}（健康 >0.85），路径绕。",
            [Prescription("linetrace", "练直线 flick，走最短路径"),
             Prescription("clean lines", "意识：flick 走直线，不画弧")],
        ))

    if reference_summary is not None:
        self_peak = _med(self_summary, "peak_speed_deg")
        ref_peak = _med(reference_summary, "peak_speed_deg")
        if self_peak and ref_peak:
            ratio = self_peak / ref_peak
            if ratio < THRESHOLDS["peak_below_ref"]:
                f.append(Finding(
                    "peak_speed below reference", "fix",
                    f"甩枪角速度 {self_peak:.0f}°/s 只有参考的 {ratio*100:.0f}%"
                    f"（{ref_peak:.0f}°/s），甩得偏慢、发力不足。",
                    [Prescription("Tile Frenzy", "练 arm 发力与动态速度"),
                     Prescription("speed 类场景", "大胆加速，先求速度再收精度")],
                ))

        self_tp = _med(self_summary, "throughput")
        ref_tp = _med(reference_summary, "throughput")
        if self_tp and ref_tp:
            tp_ratio = self_tp / ref_tp
            if tp_ratio < THRESHOLDS["throughput_below_ref"]:
                f.append(Finding(
                    "throughput below reference", "fix",
                    f"Fitts throughput {self_tp:.1f} bits/s 只有参考的 {tp_ratio*100:.0f}%"
                    f"（{ref_tp:.1f}）——跨距离发力能力不足（已按目标距离/宽度归一化，"
                    "比峰值速度更公平，§6.3）。",
                    [Prescription("Tile Frenzy", "练 arm 发力与动态速度"),
                     Prescription("speed 类场景", "先求速度再收精度")],
                ))

    if cm_per_360 is not None and cm_per_360 < THRESHOLDS["sens_high_cm360"]:
        f.append(Finding(
            "sensitivity high", "watch",
            f"灵敏度 {cm_per_360:.1f} cm/360 偏快（主流 28-43），flicking 制动难、"
            "手抖被放大。",
            [Prescription("降 sens 5-10%（cm/360 ↑）", "制动辅助实验；复测 linearity/reverse 是否下降，没降就调回")],
        ))

    return f


# metrics where lower is better (cleaner / more stopped / shorter decel);
# the rest are higher-is-better (faster / straighter / smoother-sparc).
_LOWER_BETTER = {"linearity", "reverse_ratio", "endpoint_peak"}
# non-monotone metrics: no simple better/worse (band- or context-dependent)
_NO_VERDICT = {
    "peak_position_pct", "path_length_deg",
    "submovement_overlap", "corrective_count",  # fluid vs two-stage is style, not strictly better
    # decel_frac is band-shaped (healthy [0.40, 0.65], per THRESHOLDS);
    # advise() handles band diagnosis, progress._decel_frac_verdict carries
    # the health-band monotone trend verdict. Simple lower/higher would mark
    # a pathological brake-slam (0.30) "better" than a healthy 0.50.
    "decel_frac",
}


def compare_table(self_summary: dict, reference_summary: dict) -> list[dict]:
    """Per-metric self-vs-reference comparison rows for reporting.

    Each row: ``{metric, self, ref, delta, verdict}`` where verdict is
    ``"better"`` / ``"worse"`` / ``"same"`` / ``"info"`` from your perspective
    (±10% band). Non-monotone metrics (peak position, path length) are marked
    ``"info"`` — :func:`advise` handles their band-aware diagnosis.
    """
    metrics = (
        "peak_speed_deg", "throughput", "linearity", "sparc", "reverse_ratio",
        "decel_frac", "peak_position_pct", "path_efficiency", "path_length_deg",
        "endpoint_peak", "submovement_overlap", "corrective_count",
    )
    rows = []
    for m in metrics:
        s = _med(self_summary, m)
        r = _med(reference_summary, m)
        if s is None or r is None:
            continue
        if m in _NO_VERDICT:
            verdict = "info"
        else:
            verdict = "same"
            if m in _LOWER_BETTER:
                if s < r * 0.9:
                    verdict = "better"
                elif s > r * 1.1:
                    verdict = "worse"
            elif s > r * 1.1:
                verdict = "better"
            elif s < r * 0.9:
                verdict = "worse"
        rows.append({
            "metric": m, "self": round(s, 3), "ref": round(r, 3),
            "delta": round(s - r, 3), "verdict": verdict,
        })
    return rows


__all__ = ["Prescription", "Finding", "advise", "compare_table", "THRESHOLDS"]
