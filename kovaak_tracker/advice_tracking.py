"""Tracking advice rule engine: tracking summary -> diagnosis + prescriptions.

Parallel to :mod:`kovaak_tracker.advice` (flicking). Reuses
``Prescription`` / ``Finding`` dataclasses from advice.py so downstream
(diagnosis / visualization / agent) consume a uniform Finding shape.

Differences from flicking advice (see spec
``docs/superpowers/specs/2026-07-05-tracking-coach-design.md`` §5.1):

- flicking summary is ``{metric: {med, p75, p90}}`` per-flick distribution;
  tracking summary is **single-segment scalar aggregates** written by
  :func:`kovaak_tracker.analysis.evaluate_mechanics` to metrics.json in two
  nested groups (``tension`` / ``loss``). ``_flatten_metrics`` normalizes
  the nested shape into a flat dict before rule evaluation.
- tracking rule thresholds are **initial empirical values marked
  "needs calibration"**. Enabled thresholds produce experimental
  observations rather than calibrated health judgments; thresholds set to
  ``None`` remain disabled.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Optional

from .advice import Prescription, Finding  # reuse dataclass contract


# Initial empirical thresholds (spec §3.1); none are calibrated product bands.
# speed/accel/ptc are None = disabled until a threshold is chosen; skipped when None.
THRESHOLDS = {
    "tracking_accuracy_low": 70.0,        # % on_target_pct (needs calibration)
    "tracking_loss_count_high": 60,       # losses per 60s recording (needs calibration)
    "tracking_off_target_long_s": 0.05,   # s per loss (needs calibration)
    "tracking_avg_error_ratio": 0.5,      # avg_error_px / ball_w (needs calibration)
    "tracking_avg_error_abs_px": 30.0,    # fallback when ball_w unknown (needs calibration)
    "tracking_speed_mismatch_high": None, # uncalibrated
    "tracking_accel_mismatch_high": None, # uncalibrated
    "tracking_ptc_high": None,            # uncalibrated (biomechanics hypothesis)
}


_PLAIN_MEANINGS = {
    "accuracy low": "本次记录中准星位于目标范围内的时间比例较低",
    "loss count high": "本次记录中追踪中断次数较多",
    "off target long": "每次追踪中断后回到目标范围所需时间较长",
    "avg error high": "本次记录中准星相对目标中心的平均偏移较大",
    "speed mismatch high": "失手片段中的目标与准星平均速度差较大",
    "accel mismatch high": "失手片段中的目标与准星平均加速度差较大",
    "ptc high": "失手片段中的加速度误差相对空间误差较高",
}


_EXPECTED_DIRECTIONS = {
    "accuracy low": ["on_target_pct ↑"],
    "loss count high": ["loss_count ↓"],
    "off target long": ["total_off_time/loss_count ↓"],
    "avg error high": ["avg_error_px ↓"],
    "speed mismatch high": ["speed_mismatch ↓ under comparable target motion"],
    "accel mismatch high": ["accel_mismatch ↓ under comparable target motion"],
    "ptc high": ["ptc ↓ as an exploratory signal", "on_target_pct not worse"],
}


def _finalize_tracking_findings(findings: list[Finding]) -> list[Finding]:
    """Complete the Coach contract without overstating uncalibrated rules."""
    for finding in findings:
        directions = list(_EXPECTED_DIRECTIONS[finding.signal])
        finding.severity = "info"
        finding.claim_level = "experimental"
        finding.limitations = ["threshold_requires_product_calibration"]
        finding.plain_language_meaning = _PLAIN_MEANINGS[finding.signal]
        finding.expected_result = "；".join(directions)
        finding.verification = {
            "comparable_requirements": [
                "相同场景",
                "相同设置",
                "相同记录时长",
                "相同证据质量",
            ],
            "success_signals": directions,
            "insufficient_evidence_behavior": (
                "样本或可比条件不足时只记录观察，不判定改善或退步"
            ),
        }
        for prescription in finding.prescriptions:
            if not prescription.cue:
                prescription.cue = prescription.reason
            if not prescription.purpose:
                prescription.purpose = finding.plain_language_meaning
            if not prescription.target_metrics:
                prescription.target_metrics = list(finding.metric_refs)
            if not prescription.expected_direction:
                prescription.expected_direction = list(directions)
            if not prescription.retest_after:
                prescription.retest_after = (
                    "在相同场景、设置、记录时长和证据质量下复测"
                )
            if not prescription.stop_or_adjust_rule:
                prescription.stop_or_adjust_rule = (
                    "若目标指标未改善或 on_target_pct 明显恶化，停止调整并恢复原练法"
                )
            if not prescription.source_level:
                prescription.source_level = "experimental"
    return findings


def _flatten_metrics(metrics_json: dict) -> dict:
    """Flatten metrics.json shape {tension:{...}, loss:{...}} -> flat scalar dict.

    Output keys: avg_error_px, speed_mismatch, accel_mismatch, ptc,
    on_target_pct, loss_count, total_off_time.

    Idempotent on already-flat dicts (pass-through).
    """
    if not isinstance(metrics_json, dict):
        return {}
    tension = metrics_json.get("tension", {})
    loss = metrics_json.get("loss", {})
    # If caller already passed a flat dict, both groups are empty and we
    # return the original.
    if not tension and not loss:
        return dict(metrics_json)
    out = {}
    if isinstance(tension, dict):
        out.update(tension)
    if isinstance(loss, dict):
        out.update(loss)
    return out


def advise_tracking(
    self_summary: dict,
    reference_summary: Optional[dict] = None,
    cm_per_360: Optional[float] = None,
    ball_w: Optional[float] = None,
) -> list[Finding]:
    """Tracking summary (scalar dict) -> diagnosis + prescriptions.

    ``self_summary`` accepts either the raw metrics.json shape
    (``{"tension": {...}, "loss": {...}}``) or a pre-flattened dict of the
    seven scalars. ``ball_w`` (target width in px) enables ratio-based
    ``avg_error_high``; when omitted the rule falls back to an absolute
    pixel threshold. ``reference_summary`` and ``cm_per_360`` are accepted
    for signature symmetry with :func:`advice.advise` but v1 is self-only
    (no reference comparison; sensitivity note is handled by flicking
    advice when shared meta is provided).
    """
    flat = _flatten_metrics(self_summary)
    findings: list[Finding] = []

    on_target_pct = _scalar(flat, "on_target_pct")
    loss_count = _scalar(flat, "loss_count")
    total_off_time = _scalar(flat, "total_off_time")
    avg_error_px = _scalar(flat, "avg_error_px")
    speed_mismatch = _scalar(flat, "speed_mismatch")
    accel_mismatch = _scalar(flat, "accel_mismatch")
    ptc = _scalar(flat, "ptc")

    # --- A. accuracy_low (enabled empirical threshold; needs calibration) ---
    if on_target_pct is not None and on_target_pct < THRESHOLDS["tracking_accuracy_low"]:
        findings.append(Finding(
            "accuracy low", "info",
            f"命中率 {on_target_pct:.1f}% 低于当前经验参考线 "
            f"{THRESHOLDS['tracking_accuracy_low']:.0f}%——"
            "这只说明本次记录的在靶时间比例较低；参考线仍需产品数据校准。",
            [Prescription("pasu", "持续跟随目标速度，避免在目标后方连续追赶"),
             Prescription("VT Multiclick 30% larger", "优先保持落点稳定，再观察在靶比例")],
            metric_refs=["on_target_pct"],
        ))

    # --- B. loss_count_high (enabled empirical threshold; needs calibration) ---
    if loss_count is not None and loss_count > THRESHOLDS["tracking_loss_count_high"]:
        per_loss = (total_off_time / max(loss_count, 1)) if total_off_time is not None else None
        per_loss_str = f"，每次回位 {per_loss:.2f}s" if per_loss is not None else ""
        findings.append(Finding(
            "loss count high", "info",
            f"本次记录脱靶 {int(loss_count)} 次{per_loss_str}——"
            "追踪中断次数较多；该指标不能单独确定是速度匹配、视觉读取或身体控制造成。",
            [Prescription("VT reactive tracking", "目标变向时保持连续跟随，不提前猜下一次方向"),
             Prescription("Clover Raw Control", "脱靶后用一次连续修正回到目标，避免来回补偿")],
            metric_refs=(
                ["loss_count", "total_off_time"]
                if total_off_time is not None
                else ["loss_count"]
            ),
        ))

    # --- C. off_target_long (single-loss re-acquire slow) ---
    if total_off_time is not None and loss_count is not None and loss_count > 0:
        off_per = total_off_time / loss_count
        if off_per > THRESHOLDS["tracking_off_target_long_s"]:
            findings.append(Finding(
                "off target long", "info",
                f"每次脱靶平均 {off_per:.2f}s 才回到目标范围——"
                "本次记录的离靶持续时间较长；该指标不能单独证明视觉锁定或反应延迟。",
                [Prescription("VT evasive tracking", "脱靶后保持一次连续回位，不连续急停重启"),
                 Prescription("Clover Raw Control", "回到目标后先恢复连续贴合，再提高速度")],
                metric_refs=["total_off_time", "loss_count"],
            ))

    # --- D. avg_error_high (ratio if ball_w else absolute fallback) ---
    if avg_error_px is not None:
        ratio = (avg_error_px / ball_w) if (ball_w and ball_w > 0) else None
        breached = (
            ratio is not None and ratio > THRESHOLDS["tracking_avg_error_ratio"]
        ) or (
            ratio is None and avg_error_px > THRESHOLDS["tracking_avg_error_abs_px"]
        )
        if breached:
            if ratio is not None:
                ctx = f"（{ratio:.0%} 目标宽）"
                metric_refs = ["avg_error_px", "ball_w"]
            else:
                ctx = "（无 ball_w，使用当前未校准绝对参考线）"
                metric_refs = ["avg_error_px"]
            findings.append(Finding(
                "avg error high", "info",
                f"平均误差 {avg_error_px:.1f}px{ctx}——"
                "本次记录中准星相对目标中心的平均偏移较大；该指标不能单独确定身体或视觉原因。",
                [Prescription("VT precise tracking", "以目标中心为参照，优先缩小持续偏移"),
                 Prescription("focus on crosshair gap", "观察准星与目标中心的间距变化，减少长期偏在一侧")],
                metric_refs=metric_refs,
            ))

    # --- E. speed_mismatch_high (uncalibrated -> info/watch per spec §7.3) ---
    sm_thresh = THRESHOLDS["tracking_speed_mismatch_high"]
    if speed_mismatch is not None and sm_thresh is not None and speed_mismatch > sm_thresh:
        findings.append(Finding(
            "speed mismatch high", "info",
            f"miss 段平均速度差 {speed_mismatch:.0f} px/s——"
            "失手片段中的目标与准星速度差较大；该指标不能单独确定身体或视觉原因。",
            [Prescription("VT control tracking", "跟随目标速度变化，避免突然追赶"),
             Prescription("Clover Raw Control", "用连续移动贴合目标，减少急停后重新加速")],
            metric_refs=["speed_mismatch"],
        ))

    # --- F. accel_mismatch_high (uncalibrated) ---
    am_thresh = THRESHOLDS["tracking_accel_mismatch_high"]
    if accel_mismatch is not None and am_thresh is not None and accel_mismatch > am_thresh:
        findings.append(Finding(
            "accel mismatch high", "info",
            f"miss 段平均加速度差 {accel_mismatch:.0f} px/s²——"
            "失手片段中的目标与准星加速度差较大；该指标不能单独确定身体或视觉原因。",
            [Prescription("VT reactive tracking", "目标变向时保持连续跟随，不提前猜下一次方向")],
            metric_refs=["accel_mismatch"],
        ))

    # --- G. ptc_high (biomechanics hypothesis, severity info per spec §7.1) ---
    ptc_thresh = THRESHOLDS["tracking_ptc_high"]
    if ptc is not None and ptc_thresh is not None and ptc > ptc_thresh:
        findings.append(Finding(
            "ptc high", "info",
            f"miss 段 PTC={ptc:.0f} Hz²——"
            "它描述加速度误差相对空间误差的比值，不直接测量肌肉张力，"
            "也不能单独确定身体原因。",
            [Prescription("暴露疗法：高 sens + 低 FOV 精准追踪", "减少连续来回补偿，只把 PTC 变化当探索信号")],
            metric_refs=["ptc"],
        ))

    return _finalize_tracking_findings(findings)


def _scalar(d: dict, key: str) -> Optional[float]:
    """Pull a numeric scalar from a (possibly-nested) tracking summary.

    Accepts both flat values and {med,...} dicts (defensive: if someone
    passes a flicking-style summary by mistake we still extract a number).
    """
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("med")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_TRACKING_CANDIDATES = (
    (
        "continuous_tracking.phase_lag_ms",
        "phase_lag_ms",
        "tracking lag high",
        "metric:phase_lag",
        "absolute_higher",
        "knowledge:tracking.predictable-speed-matching@3",
        "episode.tracking",
    ),
    (
        "continuous_tracking.loss_count",
        "loss_count",
        "loss count high",
        "metric:loss_count",
        "higher",
        "knowledge:tracking.reactive-change-response@3",
        "event.loss",
    ),
    (
        "continuous_tracking.reacquisition_latency_ms",
        "reacquisition_latency_ms",
        "off target long",
        "metric:reacquisition_time",
        "higher",
        "knowledge:tracking.reactive-change-response@3",
        "event.reacquisition",
    ),
    (
        "continuous_tracking.observed_change_response_ms",
        "observed_change_response_ms",
        "accel mismatch high",
        "metric:change_response",
        "higher",
        "knowledge:tracking.reactive-change-response@3",
        "event.target_change",
    ),
    (
        "continuous_tracking.correction_direction_reversal_count",
        "correction_burden",
        "correction burden high",
        "metric:correction_burden",
        "higher",
        "knowledge:tracking.control-smoothness@3",
        "metric.smoothness",
    ),
    (
        "continuous_tracking.sparc",
        "sparc",
        "sparc low",
        "metric:sparc",
        "lower",
        "knowledge:tracking.control-smoothness@3",
        "metric.smoothness",
    ),
)


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _is_worse_than_baseline(current: float, baseline: float, direction: str) -> bool:
    if direction == "higher":
        return current > baseline
    if direction == "absolute_higher":
        return abs(current) > abs(baseline)
    return current < baseline


def _tracking_control_guardrails_hold(
    metrics: Mapping[str, Any], baseline: Mapping[str, Any],
) -> bool:
    error = metrics.get("continuous_tracking.target_relative_error_px")
    current_error = _finite_number(error.get("value")) if isinstance(error, Mapping) else None
    baseline_error = _finite_number(
        baseline.get("continuous_tracking.target_relative_error_px")
    )
    coverage = metrics.get("continuous_tracking.time_in_radius_ratio")
    current_coverage = (
        _finite_number(coverage.get("value")) if isinstance(coverage, Mapping) else None
    )
    baseline_coverage = _finite_number(
        baseline.get("continuous_tracking.time_in_radius_ratio")
    )
    return (
        current_error is not None
        and baseline_error is not None
        and current_coverage is not None
        and baseline_coverage is not None
        and current_error <= baseline_error
        and current_coverage >= baseline_coverage
    )


def _tracking_knowledge_refs(
    signal: str,
    knowledge_metric_ref: str,
    expected_entry_ref: str,
) -> tuple[str, list[str]]:
    from .coach.diagnosis import resolve_candidate_knowledge_refs

    knowledge = resolve_candidate_knowledge_refs(
        issue_signal=signal,
        metric_refs=[knowledge_metric_ref],
    )
    return (
        knowledge.registry_version,
        [ref for ref in knowledge.entry_refs if ref == expected_entry_ref],
    )


def build_tracking_candidate_advice(
    analysis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return comparison-only candidate observations from tracking_analysis.v1."""
    comparison = analysis.get("comparison")
    if not isinstance(comparison, Mapping) or comparison.get("comparable") is not True:
        return []
    metrics = analysis.get("metrics")
    baseline = comparison.get("baseline_metrics")
    if not isinstance(metrics, Mapping) or not isinstance(baseline, Mapping):
        return []
    rows = [
        row for row in analysis.get("processed_rows") or []
        if isinstance(row, Mapping) and isinstance(row.get("event_ref"), str)
    ]
    candidates = []
    for (
        metric_key,
        row_field,
        signal,
        knowledge_metric_ref,
        direction,
        expected_entry_ref,
        observation_ref,
    ) in _TRACKING_CANDIDATES:
        metric = metrics.get(metric_key)
        current = _finite_number(metric.get("value")) if isinstance(metric, Mapping) else None
        reference = _finite_number(baseline.get(metric_key))
        if (
            current is None
            or reference is None
            or not _is_worse_than_baseline(current, reference, direction)
        ):
            continue
        if metric_key in {
            "continuous_tracking.correction_direction_reversal_count",
            "continuous_tracking.sparc",
        } and not _tracking_control_guardrails_hold(metrics, baseline):
            continue
        registry_version, knowledge_entry_refs = _tracking_knowledge_refs(
            signal,
            knowledge_metric_ref,
            expected_entry_ref,
        )
        if not knowledge_entry_refs:
            continue
        supporting_refs = [
            row["event_ref"] for row in rows
            if (value := _finite_number(row.get(row_field))) is not None
            and _is_worse_than_baseline(value, reference, direction)
        ]
        counterexample_refs = [
            row["event_ref"] for row in rows
            if (value := _finite_number(row.get(row_field))) is not None
            and not _is_worse_than_baseline(value, reference, direction)
        ]
        candidates.append({
            "signal": signal,
            "claim_level": "deterministic_rule",
            "metric_refs": [metric_key],
            "observation": {
                "current": current,
                "matched_baseline": reference,
                "delta": current - reference,
            },
            "supporting_row_refs": supporting_refs,
            "counterexample_row_refs": counterexample_refs,
            "observation_ref": observation_ref,
            "knowledge_registry_version": registry_version,
            "knowledge_entry_refs": knowledge_entry_refs,
            "requested_knowledge_sections": [
                "definition", "mechanisms", "alternative_explanations",
                "cue", "dose_guardrail", "matched_retest", "stop_adjust_rule",
            ],
            "limitations": list(analysis.get("limitations") or []),
        })
    return candidates


__all__ = [
    "Prescription", "Finding", "advise_tracking", "build_tracking_candidate_advice",
    "_flatten_metrics", "THRESHOLDS",
]
