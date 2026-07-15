"""Tests for tracking advice rule engine + report routing.

Covers:
- each signal fires above threshold and stays silent below
- healthy metrics -> 0 findings
- _flatten_metrics correctness (nested / flat / edge cases)
- build_report routes flicking vs tracking by meta["summary_type"]
- profile pick (fluid_tracker vs fluid_precise) by summary_type
"""
from __future__ import annotations

from kovaak_tracker.advice_tracking import (
    advise_tracking, _flatten_metrics, THRESHOLDS, Finding,
)
from kovaak_tracker.coach.report import build_report


# ---- fixtures ----

def _healthy_tracking_summary():
    """metrics.json shape, all signals below thresholds."""
    return {
        "tension": {
            "avg_error_px": 8.0,
            "speed_mismatch": 200.0,
            "accel_mismatch": 500.0,
            "ptc": 50.0,
        },
        "loss": {
            "on_target_pct": 90.0,
            "loss_count": 20,
            "total_off_time": 0.5,
        },
    }


def _pathological_tracking_summary():
    """metrics.json shape, triggers accuracy/loss/off-target/avg-error."""
    return {
        "tension": {
            "avg_error_px": 50.0,
            "speed_mismatch": 600.0,
            "accel_mismatch": 1500.0,
            "ptc": 2000.0,
        },
        "loss": {
            "on_target_pct": 40.0,
            "loss_count": 100,
            "total_off_time": 10.0,  # 10 / 100 = 0.1s per loss -> off_target_long fires
        },
    }


# ---- _flatten_metrics ----

def test_flatten_metrics_nested():
    m = {"tension": {"a": 1, "b": 2}, "loss": {"c": 3}}
    flat = _flatten_metrics(m)
    assert flat == {"a": 1, "b": 2, "c": 3}


def test_flatten_metrics_already_flat_passthrough():
    m = {"on_target_pct": 80.0, "ptc": 100.0}
    flat = _flatten_metrics(m)
    assert flat == {"on_target_pct": 80.0, "ptc": 100.0}


def test_flatten_metrics_empty():
    assert _flatten_metrics({}) == {}


def test_flatten_metrics_non_dict_returns_empty():
    assert _flatten_metrics(None) == {}  # type: ignore[arg-type]


def test_flatten_metrics_partial_groups():
    m = {"tension": {"ptc": 1.0}, "loss": {}}
    flat = _flatten_metrics(m)
    assert flat == {"ptc": 1.0}


# ---- healthy -> 0 findings ----

def test_healthy_tracking_no_findings():
    assert advise_tracking(_healthy_tracking_summary()) == []


# ---- A. accuracy_low ----

def test_accuracy_low_fires_below_threshold():
    m = _healthy_tracking_summary()
    m["loss"]["on_target_pct"] = THRESHOLDS["tracking_accuracy_low"] - 5  # 65
    findings = advise_tracking(m)
    sigs = [f.signal for f in findings]
    assert "accuracy low" in sigs
    acc = next(f for f in findings if f.signal == "accuracy low")
    assert acc.severity == "info"
    assert acc.claim_level == "experimental"


def test_accuracy_at_threshold_does_not_fire():
    """Strict < threshold: at-boundary should not fire."""
    m = _healthy_tracking_summary()
    m["loss"]["on_target_pct"] = THRESHOLDS["tracking_accuracy_low"]
    findings = advise_tracking(m)
    assert "accuracy low" not in [f.signal for f in findings]


# ---- B. loss_count_high ----

def test_loss_count_high_fires_above_threshold():
    m = _healthy_tracking_summary()
    m["loss"]["loss_count"] = THRESHOLDS["tracking_loss_count_high"] + 10
    findings = advise_tracking(m)
    assert "loss count high" in [f.signal for f in findings]


def test_loss_count_at_threshold_does_not_fire():
    m = _healthy_tracking_summary()
    m["loss"]["loss_count"] = THRESHOLDS["tracking_loss_count_high"]
    findings = advise_tracking(m)
    assert "loss count high" not in [f.signal for f in findings]


# ---- C. off_target_long ----

def test_off_target_long_fires():
    # total_off_time / loss_count > 0.05s
    m = _healthy_tracking_summary()
    m["loss"]["loss_count"] = 10
    m["loss"]["total_off_time"] = 1.0  # 0.1s per loss -> fires
    findings = advise_tracking(m)
    assert "off target long" in [f.signal for f in findings]


def test_off_target_long_does_not_fire_when_quick():
    m = _healthy_tracking_summary()
    m["loss"]["loss_count"] = 100
    m["loss"]["total_off_time"] = 1.0  # 0.01s per loss -> no fire
    findings = advise_tracking(m)
    assert "off target long" not in [f.signal for f in findings]


def test_off_target_long_no_divide_by_zero_when_no_losses():
    m = _healthy_tracking_summary()
    m["loss"]["loss_count"] = 0
    m["loss"]["total_off_time"] = 0.0
    findings = advise_tracking(m)
    assert "off target long" not in [f.signal for f in findings]


# ---- D. avg_error_high ----

def test_avg_error_high_fires_with_ball_w_ratio():
    m = _healthy_tracking_summary()
    m["tension"]["avg_error_px"] = 60.0
    # ball_w=100 -> ratio=0.6 > 0.5 threshold
    findings = advise_tracking(m, ball_w=100.0)
    assert "avg error high" in [f.signal for f in findings]


def test_avg_error_high_does_not_fire_with_ball_w_small_ratio():
    m = _healthy_tracking_summary()
    m["tension"]["avg_error_px"] = 20.0
    # ball_w=100 -> ratio=0.2 < 0.5
    findings = advise_tracking(m, ball_w=100.0)
    assert "avg error high" not in [f.signal for f in findings]


def test_avg_error_high_falls_back_to_absolute_threshold():
    m = _healthy_tracking_summary()
    m["tension"]["avg_error_px"] = THRESHOLDS["tracking_avg_error_abs_px"] + 10
    findings = advise_tracking(m)  # no ball_w
    assert "avg error high" in [f.signal for f in findings]


def test_avg_error_high_no_fire_below_absolute_when_no_ball_w():
    m = _healthy_tracking_summary()
    # avg_error_px = 8 (well below abs threshold)
    findings = advise_tracking(m)
    assert "avg error high" not in [f.signal for f in findings]


# ---- E/F/G. uncalibrated signals (thresholds None) ----

def test_speed_mismatch_does_not_fire_when_threshold_none():
    """speed/accel/ptc thresholds are None (uncalibrated) -> never fire."""
    m = _pathological_tracking_summary()
    findings = advise_tracking(m)
    sigs = [f.signal for f in findings]
    assert "speed mismatch high" not in sigs
    assert "accel mismatch high" not in sigs
    assert "ptc high" not in sigs


def test_speed_mismatch_fires_when_threshold_set():
    m = _healthy_tracking_summary()
    m["tension"]["speed_mismatch"] = 1000.0
    orig = THRESHOLDS["tracking_speed_mismatch_high"]
    try:
        THRESHOLDS["tracking_speed_mismatch_high"] = 500.0
        findings = advise_tracking(m)
        assert "speed mismatch high" in [f.signal for f in findings]
    finally:
        THRESHOLDS["tracking_speed_mismatch_high"] = orig


def test_accel_mismatch_fires_when_threshold_set():
    m = _healthy_tracking_summary()
    m["tension"]["accel_mismatch"] = 2000.0
    orig = THRESHOLDS["tracking_accel_mismatch_high"]
    try:
        THRESHOLDS["tracking_accel_mismatch_high"] = 1000.0
        findings = advise_tracking(m)
        assert "accel mismatch high" in [f.signal for f in findings]
    finally:
        THRESHOLDS["tracking_accel_mismatch_high"] = orig


def test_ptc_high_fires_at_info_severity_when_threshold_set():
    """ptc_high is interpretive hypothesis -> severity=info per spec §7.1."""
    m = _healthy_tracking_summary()
    m["tension"]["ptc"] = 2000.0
    orig = THRESHOLDS["tracking_ptc_high"]
    try:
        THRESHOLDS["tracking_ptc_high"] = 1000.0
        findings = advise_tracking(m)
        ptc_f = next(f for f in findings if f.signal == "ptc high")
        assert ptc_f.severity == "info"
        assert ptc_f.claim_level == "experimental"
        assert "不直接测量肌肉张力" in ptc_f.diagnosis
    finally:
        THRESHOLDS["tracking_ptc_high"] = orig


# ---- accepts flat dict input ----

def test_advise_tracking_accepts_flat_dict():
    flat = {
        "on_target_pct": 50.0,
        "loss_count": 100,
        "total_off_time": 10.0,
        "avg_error_px": 8.0,
        "speed_mismatch": 200.0,
        "accel_mismatch": 500.0,
        "ptc": 50.0,
    }
    findings = advise_tracking(flat)
    sigs = [f.signal for f in findings]
    assert "accuracy low" in sigs
    assert "loss count high" in sigs


# ---- build_report routing ----

def test_build_report_routes_tracking_by_summary_type():
    """Smoke: pathological tracking metrics via build_report produces issues."""
    m = _pathological_tracking_summary()
    r = build_report(m, meta={"summary_type": "tracking"}, backend=None)
    assert r.diagnosis.issues  # non-empty
    sigs = [i.signal for i in r.diagnosis.issues]
    assert "accuracy low" in sigs
    assert r.diagnosis.meta.get("summary_type") == "tracking"


def test_build_report_routes_flicking_by_summary_type():
    """flicking summary_type still goes through advice.advise (smoke)."""
    flick_summary = {k: {"med": v} for k, v in {
        "decel_frac": 0.75, "sparc": -7.5, "linearity": 0.18,
        "reverse_ratio": 0.25, "peak_position_pct": 30,
        "path_efficiency": 0.96,
    }.items()}
    r = build_report(flick_summary, meta={"summary_type": "flicking"}, backend=None)
    # flicking advice should fire on these values
    assert any(i.signal == "decel_frac high" for i in r.diagnosis.issues)


def test_build_report_tracking_meta_uses_fluid_tracker_when_clean():
    """Healthy tracking summary -> fluid_tracker profile (not fluid_precise)."""
    r = build_report(
        _healthy_tracking_summary(),
        meta={"summary_type": "tracking"},
        backend=None,
    )
    assert r.diagnosis.profile.archetype_id == "fluid_tracker"


def test_build_report_flicking_meta_uses_fluid_precise_when_clean():
    """Healthy flicking summary -> fluid_precise profile."""
    healthy_flick = {"decel_frac": {"med": 0.55}, "sparc": {"med": -0.3}}
    r = build_report(
        healthy_flick, meta={"summary_type": "flicking"}, backend=None
    )
    assert r.diagnosis.profile.archetype_id == "fluid_precise"


def test_build_report_fallback_heuristic_routes_tracking_shape():
    """Without summary_type, probe summary shape to route tracking."""
    r = build_report(
        _pathological_tracking_summary(), meta={}, backend=None
    )
    assert r.diagnosis.issues
    assert "accuracy low" in [i.signal for i in r.diagnosis.issues]


def test_build_report_fallback_heuristic_routes_flicking_shape():
    """Without summary_type, flicking-shaped summary routes to flicking advice."""
    flick_summary = {"decel_frac": {"med": 0.80}, "sparc": {"med": -8.0}}
    r = build_report(flick_summary, meta={}, backend=None)
    assert any(i.signal == "decel_frac high" for i in r.diagnosis.issues)


# ---- Finding structure sanity ----

def test_each_tracking_finding_has_prescriptions():
    """Every fired Finding must carry at least one Prescription."""
    findings = advise_tracking(_pathological_tracking_summary())
    assert findings, "expected findings on pathological summary"
    for f in findings:
        assert isinstance(f, Finding)
        assert f.prescriptions, f"signal {f.signal!r} has no prescriptions"


def _assert_explanation_contract(finding: Finding) -> None:
    assert finding.diagnosis
    assert finding.severity == "info"
    assert finding.claim_level == "experimental"
    assert finding.metric_refs
    assert finding.event_refs == []
    assert "threshold_requires_product_calibration" in finding.limitations
    assert finding.plain_language_meaning
    assert finding.expected_result
    assert finding.verification["comparable_requirements"]
    assert finding.verification["success_signals"]
    assert finding.verification["insufficient_evidence_behavior"]
    for prescription in finding.prescriptions:
        assert prescription.cue
        assert prescription.purpose
        assert prescription.target_metrics
        assert prescription.expected_direction
        assert prescription.retest_after
        assert prescription.stop_or_adjust_rule
        assert prescription.source_level


def test_real_tracking_findings_satisfy_explanation_contract():
    findings = advise_tracking(_pathological_tracking_summary())
    assert {finding.signal for finding in findings} == {
        "accuracy low",
        "loss count high",
        "off target long",
        "avg error high",
    }
    for finding in findings:
        _assert_explanation_contract(finding)


def test_uncalibrated_tracking_thresholds_never_emit_health_or_formal_severity():
    findings = advise_tracking(_pathological_tracking_summary())
    assert findings
    assert all(finding.severity == "info" for finding in findings)
    assert all(finding.claim_level == "experimental" for finding in findings)
    assert all("健康" not in finding.diagnosis for finding in findings)
    assert all("异常" not in finding.diagnosis for finding in findings)


def test_tracking_findings_do_not_claim_unmeasured_body_or_visual_causes():
    findings = {
        finding.signal: finding
        for finding in advise_tracking(_pathological_tracking_summary())
    }
    assert "不能单独确定" in findings["loss count high"].diagnosis
    assert "不能单独证明" in findings["off target long"].diagnosis


def test_enabled_experimental_tracking_signals_satisfy_safe_contract():
    m = _healthy_tracking_summary()
    m["tension"].update({
        "speed_mismatch": 1000.0,
        "accel_mismatch": 2000.0,
        "ptc": 2000.0,
    })
    keys = (
        "tracking_speed_mismatch_high",
        "tracking_accel_mismatch_high",
        "tracking_ptc_high",
    )
    originals = {key: THRESHOLDS[key] for key in keys}
    try:
        THRESHOLDS.update({
            "tracking_speed_mismatch_high": 500.0,
            "tracking_accel_mismatch_high": 1000.0,
            "tracking_ptc_high": 1000.0,
        })
        findings = {finding.signal: finding for finding in advise_tracking(m)}
        assert set(findings) == {
            "speed mismatch high",
            "accel mismatch high",
            "ptc high",
        }
        for finding in findings.values():
            _assert_explanation_contract(finding)
        assert "不能单独确定身体或视觉原因" in findings["speed mismatch high"].diagnosis
        assert "不能单独确定身体或视觉原因" in findings["accel mismatch high"].diagnosis
        assert "不直接测量肌肉张力" in findings["ptc high"].diagnosis
    finally:
        THRESHOLDS.update(originals)
