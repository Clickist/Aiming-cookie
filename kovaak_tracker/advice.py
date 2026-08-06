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
    cue: str = ""
    purpose: str = ""
    target_metrics: list[str] = field(default_factory=list)
    expected_direction: list[str] = field(default_factory=list)
    retest_after: str = ""
    stop_or_adjust_rule: str = ""
    source_level: str = "community_consensus"


@dataclass
class Finding:
    """One diagnosed issue: signal, severity, statement, prescriptions."""
    signal: str            # e.g. "decel_frac high"
    severity: str          # "info" | "watch" | "fix"
    diagnosis: str         # plain-language statement
    prescriptions: list[Prescription] = field(default_factory=list)
    claim_level: str = "deterministic_rule"
    metric_refs: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    plain_language_meaning: str = ""
    expected_result: str = ""
    verification: dict[str, object] = field(default_factory=dict)


# Initial heuristic thresholds from the research draft. They are not calibrated
# product health bands; advise() therefore emits them as experimental info only.
_UNCALIBRATED_SPARC_V2 = frozenset({
    "native_flicking.sparc.v2",
    "flicking_fair_summary.sparc.v2",
})


THRESHOLDS = {
    "decel_frac_high": 0.65,
    "decel_frac_low": 0.40,
    "linearity_high": 0.13,
    "sparc_low_legacy_unversioned": -5.0,  # old experimental scale only; v2 is uncalibrated
    "reverse_high": 0.20,
    "two_stage_overlap": 0.30,  # corrective/primary overlap < this = discrete two-stage (§6.2)
    "peak_pos_low": 30.0,
    "peak_pos_high": 60.0,
    "path_eff_low": 0.85,
    "peak_below_ref": 0.70,   # self peak / ref peak
    "throughput_below_ref": 0.70,  # self TP / ref TP (§6.3)
    "sens_high_cm360": 25.0,  # uncalibrated trigger for a reversible experiment note
}


_SIGNAL_METRICS = {
    "decel_frac high": ["decel_frac"],
    "decel_frac low": ["decel_frac"],
    "linearity high": ["linearity"],
    "sparc low": ["sparc"],
    "reverse_ratio high": ["reverse_ratio"],
    "submovement two-stage": ["submovement_overlap"],
    "peak_position low": ["peak_position_pct"],
    "peak_position high": ["peak_position_pct"],
    "path_efficiency low": ["path_efficiency"],
    "peak_speed below reference": ["peak_speed_deg"],
    "throughput below reference": ["throughput"],
    "sensitivity high": ["cm_per_360"],
}

_PLAIN_MEANINGS = {
    "decel_frac high": "速度峰值后用了较长时间完成减速",
    "decel_frac low": "速度峰值后留给连续减速的时间较短",
    "linearity high": "减速阶段的速度下降节奏不够均匀",
    "sparc low": "减速阶段的速度轮廓含较多快速波动",
    "reverse_ratio high": "移动收尾时出现了较多反向修正",
    "submovement two-stage": "主要移动与后续修正更像两个分离动作",
    "peak_position low": "速度峰值出现得较早",
    "peak_position high": "速度峰值出现得较晚",
    "path_efficiency low": "实际移动路径比起终点直线距离更绕",
    "peak_speed below reference": "峰值速度低于当前比较参考",
    "throughput below reference": "速度与精度综合效率低于当前比较参考",
    "sensitivity high": "当前 cm/360 较小，是否影响控制仍需实验验证",
}

_EXPECTED_DIRECTIONS = {
    "decel_frac high": ["decel_frac toward individually calibrated target"],
    "decel_frac low": ["decel_frac toward individually calibrated target"],
    "linearity high": ["linearity ↓"],
    "sparc low": ["sparc ↑"],
    "reverse_ratio high": ["reverse_ratio ↓"],
    "submovement two-stage": ["submovement_overlap toward chosen technique"],
    "peak_position low": ["peak_position_pct toward individual baseline"],
    "peak_position high": ["peak_position_pct toward individual baseline"],
    "path_efficiency low": ["path_efficiency ↑"],
    "peak_speed below reference": ["peak_speed_deg ↑ against comparable baseline"],
    "throughput below reference": ["throughput ↑ against comparable baseline"],
    "sensitivity high": ["linearity/reverse_ratio improve after controlled setting experiment"],
}


def _finalize_uncalibrated_findings(findings: list[Finding]) -> list[Finding]:
    """Attach an actionable explanation while keeping initial thresholds honest."""
    for finding in findings:
        metrics = list(_SIGNAL_METRICS.get(finding.signal, []))
        directions = list(_EXPECTED_DIRECTIONS.get(finding.signal, []))
        finding.severity = "info"
        finding.claim_level = "experimental"
        finding.metric_refs = metrics
        finding.limitations = ["threshold_requires_product_calibration"]
        finding.plain_language_meaning = _PLAIN_MEANINGS.get(
            finding.signal, finding.diagnosis
        )
        finding.expected_result = "；".join(directions)
        finding.verification = {
            "comparable_requirements": ["相同场景", "相同设置", "相同证据质量"],
            "success_signals": directions,
            "insufficient_evidence_behavior": "样本或可比条件不足时只记录观察，不判定改善或退步",
        }
        for prescription in finding.prescriptions:
            if not prescription.cue:
                prescription.cue = prescription.reason
            if not prescription.purpose:
                prescription.purpose = finding.plain_language_meaning
            if not prescription.target_metrics:
                prescription.target_metrics = list(metrics)
            if not prescription.expected_direction:
                prescription.expected_direction = list(directions)
            if not prescription.retest_after:
                prescription.retest_after = "在相同场景、设置和证据质量下复测"
            if not prescription.stop_or_adjust_rule:
                prescription.stop_or_adjust_rule = (
                    "若目标指标未改善或准确率明显恶化，停止调整并恢复原练法"
                )
    return findings


def _metric_version(summary: dict, metric: str) -> str | None:
    value = summary.get(metric)
    if not isinstance(value, dict):
        return None
    version = value.get("metric_version")
    return version if isinstance(version, str) and version else None


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
    ``cm_per_360`` enables an experimental sensitivity note. The trigger is not
    a calibrated health band and cannot establish sensitivity as a root cause.
    """
    f: list[Finding] = []

    decfrac = _med(self_summary, "decel_frac")
    if decfrac is not None:
        if decfrac > THRESHOLDS["decel_frac_high"]:
            f.append(Finding(
                "decel_frac high", "fix",
                f"减速段占整个 flick 的 {decfrac*100:.0f}%——"
                "速度峰值后用了较长时间完成减速；该阈值仍需真实产品数据校准。",
                [Prescription("pasu", "练完整的加速→减速，接近目标时果断完成制动"),
                 Prescription("1w4ts Voltaic", "保持 90%+ 准确率，练完整 flick 的加减速")],
            ))
        elif decfrac < THRESHOLDS["decel_frac_low"]:
            f.append(Finding(
                "decel_frac low", "watch",
                f"减速段只占 {decfrac*100:.0f}%，连续减速时间较短；"
                "是否属于制动问题仍需结合 settle/reverse 和个体历史验证。",
                [Prescription("pasu", "练匀减速，把减速段当一次独立动作")],
            ))

    linearity = _med(self_summary, "linearity")
    if linearity is not None and linearity > THRESHOLDS["linearity_high"]:
        f.append(Finding(
            "linearity high", "fix",
            f"减速段速度曲线偏离匀减速直线 {linearity:.2f}——"
            "速度下降节奏不够均匀。注：这度量的是制动节奏，不是抖动；"
            "抖动看 SPARC。",
             [Prescription("pasu", "把减速段练成干净、连贯的制动"),
             Prescription("1w4ts 30% larger", "减速段精度专项")],
        ))

    sparc = _med(self_summary, "sparc")
    sparc_version = _metric_version(self_summary, "sparc")
    if (
        sparc is not None
        and sparc_version not in _UNCALIBRATED_SPARC_V2
        and sparc < THRESHOLDS["sparc_low_legacy_unversioned"]
    ):
        f.append(Finding(
            "sparc low", "fix",
            f"减速段平滑度 SPARC={sparc:.1f}——速度轮廓中的快速波动较多。"
            "SPARC 描述运动轮廓，不直接测量握持张力；绝对阈值仍需产品校准。",
            [Prescription("pasu", "clean lines，让减速速度连续下降，避免突然硬停"),
             Prescription("1w4ts 30% larger", "减速段精度专项")],
        ))

    reverse = _med(self_summary, "reverse_ratio")
    if reverse is not None and reverse > THRESHOLDS["reverse_high"]:
        f.append(Finding(
            "reverse_ratio high", "fix",
            f"减速段有 {reverse*100:.0f}% 的帧在反向加速，表现为反复修正。",
            [Prescription("pasu", "把修正并入减速过程，避免停住后再二次修正"),
             Prescription("Multiclick", "落点精度，减少二次修正")],
        ))

    overlap = _med(self_summary, "submovement_overlap")
    if overlap is not None and overlap < THRESHOLDS["two_stage_overlap"]:
        f.append(Finding(
            "submovement two-stage", "watch",
            "主要移动和后续修正呈较分离的两个阶段。",
            [Prescription("pasu", "尝试让主要移动和收尾修正保持衔接，减少停住后再单独修正"),
             Prescription("Multiclick", "落点精度，减少二次修正")],
        ))

    peak_pos = _med(self_summary, "peak_position_pct")
    if peak_pos is not None:
        if peak_pos < THRESHOLDS["peak_pos_low"]:
            f.append(Finding(
                "peak_position low", "watch",
                f"峰位 {peak_pos:.0f}%（偏前），速度峰值较早出现；"
                "具体动作原因未被输入数据直接测量。",
                [Prescription("pasu", "平衡加减速，把峰往中段靠")],
            ))
        elif peak_pos > THRESHOLDS["peak_pos_high"]:
            f.append(Finding(
                "peak_position high", "watch",
                f"峰位 {peak_pos:.0f}%（偏后），速度峰值较晚出现；"
                "具体动作原因未被输入数据直接测量。",
                [Prescription("Tile Frenzy", "练果断加速、提速")],
            ))

    path_eff = _med(self_summary, "path_efficiency")
    if path_eff is not None and path_eff < THRESHOLDS["path_eff_low"]:
        f.append(Finding(
            "path_efficiency low", "fix",
            f"flick 路径直线效率 {path_eff:.2f}，实际路径相对终点直线距离更绕；"
            "绝对阈值仍需产品校准。",
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
                    f"（{ref_peak:.0f}°/s）；峰值速度低于当前参考，身体原因未被输入数据直接测量。",
                    [Prescription("Tile Frenzy", "在可控精度下逐步提高动态速度"),
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
                    f"（{ref_tp:.1f}）；速度-精度综合效率低于当前参考，身体原因未被输入数据"
                    "直接测量（throughput 已按目标距离/宽度归一化，§6.3）。",
                    [Prescription("Tile Frenzy", "在可控精度下逐步提高动态速度"),
                     Prescription("speed 类场景", "先求速度再收精度")],
                ))

    if cm_per_360 is not None and cm_per_360 < THRESHOLDS["sens_high_cm360"]:
        f.append(Finding(
            "sensitivity high", "watch",
            f"当前灵敏度为 {cm_per_360:.1f} cm/360。较小 cm/360 可能放大控制输入，"
            "但不能单凭设置值判定动作问题，只能作为受控实验假设。",
            [Prescription("降 sens 5-10%（cm/360 ↑）", "制动辅助实验；复测 linearity/reverse 是否下降，没降就调回")],
        ))

    return _finalize_uncalibrated_findings(f)


# metrics where lower is better (cleaner / more stopped / shorter decel);
# the rest are higher-is-better (faster / straighter / smoother-sparc).
_LOWER_BETTER = {"linearity", "reverse_ratio", "endpoint_peak"}
# non-monotone metrics: no simple better/worse (band- or context-dependent)
_NO_VERDICT = {
    "peak_position_pct", "path_length_deg",
    "submovement_overlap", "corrective_count",  # fluid vs two-stage is style, not strictly better
    # decel_frac is band-shaped (initial heuristic [0.40, 0.65], per THRESHOLDS);
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
