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
  "needs calibration"** — the spec explicitly flags
  speed/accel/ptc thresholds as uncalibrated. Those three signals emit at
  ``info`` / ``watch`` severity (interpretive hypothesis, not hard
  diagnosis) per spec §7.
"""
from __future__ import annotations

from typing import Optional

from .advice import Prescription, Finding  # reuse dataclass contract


# Initial empirical thresholds (spec §3.1).
# speed/accel/ptc are None = uncalibrated; advise_tracking skips them when None.
THRESHOLDS = {
    "tracking_accuracy_low": 70.0,        # % on_target_pct
    "tracking_loss_count_high": 60,       # losses per 60s recording (needs calibration)
    "tracking_off_target_long_s": 0.05,   # s per loss (needs calibration)
    "tracking_avg_error_ratio": 0.5,      # avg_error_px / ball_w (needs calibration)
    "tracking_avg_error_abs_px": 30.0,    # fallback when ball_w unknown (needs calibration)
    "tracking_speed_mismatch_high": None, # uncalibrated
    "tracking_accel_mismatch_high": None, # uncalibrated
    "tracking_ptc_high": None,            # uncalibrated (biomechanics hypothesis)
}


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

    # --- A. accuracy_low (community_consensus, severity fix) ---
    if on_target_pct is not None and on_target_pct < THRESHOLDS["tracking_accuracy_low"]:
        findings.append(Finding(
            "accuracy low", "fix",
            f"命中率 {on_target_pct:.1f}%（健康 >{THRESHOLDS['tracking_accuracy_low']:.0f}%）——"
            "整体追踪控制不足。",
            [Prescription("pasu", "连续追踪基础，速度匹配"),
             Prescription("VT Multiclick 30% larger", "落点精度 + 微调")],
        ))

    # --- B. loss_count_high (severity fix; rule-of-thumb community_consensus) ---
    if loss_count is not None and loss_count > THRESHOLDS["tracking_loss_count_high"]:
        per_loss = (total_off_time / max(loss_count, 1)) if total_off_time is not None else None
        per_loss_str = f"，每次回位 {per_loss:.2f}s" if per_loss is not None else ""
        findings.append(Finding(
            "loss count high", "fix",
            f"脱靶 {int(loss_count)} 次（频繁断追踪）{per_loss_str}——"
            "追踪不连续，可能是速度匹配跟不上目标变向。",
            [Prescription("VT reactive tracking", "应对瞬时加速度"),
             Prescription("Clover Raw Control", "速度匹配 + 侧向挤压稳准星")],
        ))

    # --- C. off_target_long (single-loss re-acquire slow) ---
    if total_off_time is not None and loss_count is not None and loss_count > 0:
        off_per = total_off_time / loss_count
        if off_per > THRESHOLDS["tracking_off_target_long_s"]:
            findings.append(Finding(
                "off target long", "watch",
                f"每次脱靶平均 {off_per:.2f}s 才回位——"
                "读回目标慢（可能视觉锁定 / 反应延迟）。",
                [Prescription("VT evasive tracking", "目标逃逸型，练视觉读取"),
                 Prescription("Clover Raw Control", "锁定目标，减少丢失")],
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
            else:
                ctx = "（无 ball_w，用绝对阈值判）"
            findings.append(Finding(
                "avg error high", "fix",
                f"平均误差 {avg_error_px:.1f}px{ctx}——"
                "准星虽在 target 上但偏移大，临界命中多。",
                [Prescription("VT precise tracking", "精度追踪专项"),
                 Prescription("focus on crosshair gap", "带 gap 准星，注意力锁中心")],
            ))

    # --- E. speed_mismatch_high (uncalibrated -> info/watch per spec §7.3) ---
    sm_thresh = THRESHOLDS["tracking_speed_mismatch_high"]
    if speed_mismatch is not None and sm_thresh is not None and speed_mismatch > sm_thresh:
        findings.append(Finding(
            "speed mismatch high", "watch",
            f"miss 段目标屏幕速度 {speed_mismatch:.0f} px/s——"
            "提示高速段可能失手多（合理推断：v_rel 含准星噪声，主导项是目标速度）。",
            [Prescription("VT control tracking", "持续中速追踪"),
             Prescription("Clover Raw Control", "侧向挤压稳准星")],
        ))

    # --- F. accel_mismatch_high (uncalibrated) ---
    am_thresh = THRESHOLDS["tracking_accel_mismatch_high"]
    if accel_mismatch is not None and am_thresh is not None and accel_mismatch > am_thresh:
        findings.append(Finding(
            "accel mismatch high", "watch",
            f"miss 段目标加速度 {accel_mismatch:.0f} px/s²——应对变向吃力。",
            [Prescription("VT reactive tracking", "应对瞬时加速度")],
        ))

    # --- G. ptc_high (biomechanics hypothesis, severity info per spec §7.1) ---
    ptc_thresh = THRESHOLDS["tracking_ptc_high"]
    if ptc is not None and ptc_thresh is not None and ptc > ptc_thresh:
        findings.append(Finding(
            "ptc high", "info",
            f"miss 段加速度密度 PTC={ptc:.0f} Hz²——"
            "可能张力偏大（生物力学假设，未 EMG 验证；结合 SPARC / 反向修正一起读更稳）。",
            [Prescription("暴露疗法：高 sens + 低 FOV 精准追踪", "放大微颤，逼大脑修正张力分配")],
        ))

    return findings


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


__all__ = ["Prescription", "Finding", "advise_tracking", "_flatten_metrics", "THRESHOLDS"]
