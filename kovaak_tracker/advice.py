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


# 施工单⑤：速度吞吐判读档下 reverse_ratio 的触发阈值分支。速度吞吐图上
# 小幅收尾反向修正 = 正常"快中带控"技术，只有更明显的修正形态才升格为
# 改写判读（节奏代价框架）；精瞄/中间档维持 reverse_high 原阈值。初始值
# 未校准，与 reverse_high 同样以实验口径出（limitations 不变）。
THRESHOLDS = {
    "decel_frac_high": 0.65,
    "decel_frac_low": 0.40,
    "linearity_high": 0.13,
    "sparc_low_legacy_unversioned": -5.0,  # old experimental scale only; v2 is uncalibrated
    "reverse_high": 0.20,
    "reverse_high_speed_throughput": 0.35,
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

# B3 i18n：_PLAIN_MEANINGS 与各 Finding 的中文模板/处方文案已迁移到
# ``coach/labels`` 目录（zh 侧逐字搬运，en 侧平行变体；advise 按 locale 取）。

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


def _finalize_uncalibrated_findings(
    findings: list[Finding], locale: str = "zh-CN",
) -> list[Finding]:
    """Attach an actionable explanation while keeping initial thresholds honest.

    B3 i18n：填充文案按 *locale* 从 ``coach/labels`` 目录取；zh-CN 与迁移前
    逐字节一致（golden 契约）。
    """
    from .coach.labels import catalog

    cat = catalog(locale)
    for finding in findings:
        metrics = list(_SIGNAL_METRICS.get(finding.signal, []))
        directions = list(_EXPECTED_DIRECTIONS.get(finding.signal, []))
        finding.severity = "info"
        finding.claim_level = "experimental"
        finding.metric_refs = metrics
        finding.limitations = ["threshold_requires_product_calibration"]
        finding.plain_language_meaning = cat.PLAIN_MEANINGS.get(
            finding.signal, finding.diagnosis
        )
        finding.expected_result = "；".join(directions)
        finding.verification = {
            "comparable_requirements": list(cat.VERIFICATION["comparable_requirements"]),
            "success_signals": directions,
            "insufficient_evidence_behavior": cat.VERIFICATION[
                "insufficient_evidence_behavior"
            ],
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
                prescription.retest_after = cat.VERIFICATION["retest_after"]
            if not prescription.stop_or_adjust_rule:
                prescription.stop_or_adjust_rule = cat.VERIFICATION[
                    "stop_or_adjust_rule"
                ]
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
    locale: str = "zh-CN",
    scenario_reading: dict | None = None,
) -> list[Finding]:
    """Rule engine: fair-metric summary -> diagnosis + prescriptions.

    ``self_summary`` is your fair-metric summary (from
    :func:`analyze_flicking_reference` or equivalent). ``reference_summary`` is an
    optional high-level player's summary for relative comparison.
    ``cm_per_360`` enables an experimental sensitivity note. The trigger is not
    a calibrated health band and cannot establish sensitivity as a root cause.
    ``scenario_reading`` is the optional scenario reading descriptor
    (``sce_reading.build_scenario_reading_descriptor`` shape); when its
    reading scope is speed_throughput, the reverse_ratio judgment takes the
    pacing-cost branch (施工单⑤) instead of the terminal-control framing.
    B3 i18n: finding copy comes from the ``coach/labels`` catalog for *locale*.
    """
    from .coach.labels import catalog

    cat = catalog(locale)

    def _copy_prescriptions(signal: str) -> list[Prescription]:
        return [
            Prescription(scenario, reason)
            for scenario, reason in cat.FINDING_PRESCRIPTIONS.get(signal, ())
        ]

    f: list[Finding] = []

    decfrac = _med(self_summary, "decel_frac")
    if decfrac is not None:
        if decfrac > THRESHOLDS["decel_frac_high"]:
            f.append(Finding(
                "decel_frac high", "fix",
                cat.FINDING_DIAGNOSES["decel_frac high"].format(
                    decel_frac_pct=decfrac * 100,
                ),
                _copy_prescriptions("decel_frac high"),
            ))
        elif decfrac < THRESHOLDS["decel_frac_low"]:
            f.append(Finding(
                "decel_frac low", "watch",
                cat.FINDING_DIAGNOSES["decel_frac low"].format(
                    decel_frac_pct=decfrac * 100,
                ),
                _copy_prescriptions("decel_frac low"),
            ))

    linearity = _med(self_summary, "linearity")
    if linearity is not None and linearity > THRESHOLDS["linearity_high"]:
        f.append(Finding(
            "linearity high", "fix",
            cat.FINDING_DIAGNOSES["linearity high"].format(linearity=linearity),
            _copy_prescriptions("linearity high"),
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
            cat.FINDING_DIAGNOSES["sparc low"].format(sparc=sparc),
            _copy_prescriptions("sparc low"),
        ))

    reverse = _med(self_summary, "reverse_ratio")
    if reverse is not None and reverse > THRESHOLDS["reverse_high"]:
        f.append(Finding(
            "reverse_ratio high", "fix",
            cat.FINDING_DIAGNOSES["reverse_ratio high"].format(
                reverse_pct=reverse * 100,
            ),
            _copy_prescriptions("reverse_ratio high"),
        ))

    overlap = _med(self_summary, "submovement_overlap")
    if overlap is not None and overlap < THRESHOLDS["two_stage_overlap"]:
        f.append(Finding(
            "submovement two-stage", "watch",
            cat.FINDING_DIAGNOSES["submovement two-stage"],
            _copy_prescriptions("submovement two-stage"),
        ))

    peak_pos = _med(self_summary, "peak_position_pct")
    if peak_pos is not None:
        if peak_pos < THRESHOLDS["peak_pos_low"]:
            f.append(Finding(
                "peak_position low", "watch",
                cat.FINDING_DIAGNOSES["peak_position low"].format(peak_pos=peak_pos),
                _copy_prescriptions("peak_position low"),
            ))
        elif peak_pos > THRESHOLDS["peak_pos_high"]:
            f.append(Finding(
                "peak_position high", "watch",
                cat.FINDING_DIAGNOSES["peak_position high"].format(peak_pos=peak_pos),
                _copy_prescriptions("peak_position high"),
            ))

    path_eff = _med(self_summary, "path_efficiency")
    if path_eff is not None and path_eff < THRESHOLDS["path_eff_low"]:
        f.append(Finding(
            "path_efficiency low", "fix",
            cat.FINDING_DIAGNOSES["path_efficiency low"].format(path_eff=path_eff),
            _copy_prescriptions("path_efficiency low"),
        ))

    if reference_summary is not None:
        self_peak = _med(self_summary, "peak_speed_deg")
        ref_peak = _med(reference_summary, "peak_speed_deg")
        if self_peak and ref_peak:
            ratio = self_peak / ref_peak
            if ratio < THRESHOLDS["peak_below_ref"]:
                f.append(Finding(
                    "peak_speed below reference", "fix",
                    cat.FINDING_DIAGNOSES["peak_speed below reference"].format(
                        self_peak=self_peak, ratio_pct=ratio * 100, ref_peak=ref_peak,
                    ),
                    _copy_prescriptions("peak_speed below reference"),
                ))

        self_tp = _med(self_summary, "throughput")
        ref_tp = _med(reference_summary, "throughput")
        if self_tp and ref_tp:
            tp_ratio = self_tp / ref_tp
            if tp_ratio < THRESHOLDS["throughput_below_ref"]:
                f.append(Finding(
                    "throughput below reference", "fix",
                    cat.FINDING_DIAGNOSES["throughput below reference"].format(
                        self_tp=self_tp, tp_ratio_pct=tp_ratio * 100, ref_tp=ref_tp,
                    ),
                    _copy_prescriptions("throughput below reference"),
                ))

    if cm_per_360 is not None and cm_per_360 < THRESHOLDS["sens_high_cm360"]:
        f.append(Finding(
            "sensitivity high", "watch",
            cat.FINDING_DIAGNOSES["sensitivity high"].format(cm_per_360=cm_per_360),
            _copy_prescriptions("sensitivity high"),
        ))

    findings = _finalize_uncalibrated_findings(f, locale)
    return apply_reading_scope(findings, self_summary, scenario_reading, locale)


def _speed_throughput_reverse_finding(
    reverse: float, locale: str = "zh-CN",
) -> Finding:
    """速度吞吐判读档下的 reverse_ratio 改写 finding（施工单⑤）。

    文案/处方来自 labels 的 SPEED_* 层（metronome-pacing-method 口径 + 既有
    "练果断加速、提速"同向处方）；经统一 finalize 补全契约字段后，把
    plain_language_meaning 覆写为速度档语义。此档下 settle/停稳类处方绝迹。
    """
    from .coach.labels import catalog

    cat = catalog(locale)
    finding = Finding(
        "reverse_ratio high",
        "watch",
        cat.SPEED_FINDING_DIAGNOSES["reverse_ratio high"].format(
            reverse_pct=reverse * 100,
        ),
        [
            Prescription(scenario, reason)
            for scenario, reason in cat.SPEED_FINDING_PRESCRIPTIONS[
                "reverse_ratio high"
            ]
        ],
    )
    finding = _finalize_uncalibrated_findings([finding], locale)[0]
    finding.plain_language_meaning = cat.SPEED_PLAIN_MEANINGS["reverse_ratio high"]
    return finding


def apply_reading_scope(
    findings: list[Finding],
    summary: dict,
    scenario_reading: dict | None,
    locale: str = "zh-CN",
) -> list[Finding]:
    """按场景读图判读档改写 findings（施工单⑤；幂等，整字段替换）。

    - speed_throughput 档：reverse_ratio 触发阈值升为
      ``reverse_high_speed_throughput``（小幅反向修正=快中带控，不触发），
      超阈值时以节奏代价框架整字段替换该 finding——肯定吞吐有余量、
      处方=节拍配速+果断提速，settle/停稳类处方绝迹。
    - precision_terminal / generic / 描述符缺失 → 原样返回（精瞄语义与
      未接读图的旧数据维持既有判读，不全局封禁）。

    供 :func:`advise` 与 ``mapping_rules.dispatch_static``（映射引擎路径）
    共用，保证两条静态判读轨道行为一致。
    """
    from .sce_reading import (
        READING_SCOPE_SPEED_THROUGHPUT,
        reading_scope as _scope_of,
    )

    if _scope_of(scenario_reading) != READING_SCOPE_SPEED_THROUGHPUT:
        return findings
    reverse = _med(summary, "reverse_ratio")
    speed_trigger = THRESHOLDS["reverse_high_speed_throughput"]
    rewritten = []
    for finding in findings:
        if finding.signal == "reverse_ratio high":
            if reverse is not None and reverse > speed_trigger:
                rewritten.append(
                    _speed_throughput_reverse_finding(reverse, locale),
                )
            # 阈值内：速度吞吐档下视为正常快中带控，直接不再触发。
            continue
        rewritten.append(finding)
    return rewritten


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


__all__ = [
    "Prescription", "Finding", "advise", "apply_reading_scope", "compare_table",
    "THRESHOLDS",
]
