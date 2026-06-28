from datetime import datetime, timedelta

from kovaak_tracker.advice import Finding, Prescription
from kovaak_tracker.coach.planning import (
    build_plan, TrainingPlan, PlanAdjustment, N_MIN, REST_GAP_DAYS,
)
from kovaak_tracker.coach.progress import Session


def _session(summary, ts):
    return Session(ts, "v.mp4", 48.0, summary, {}, [], None)


def _row(metric, verdict):
    return {"metric": metric, "current": 1.0, "baseline": 1.0,
            "last": 1.0, "ref": None, "verdict": verdict}


def _finding(signal, severity="fix"):
    return Finding(signal, severity, "diag", [Prescription("pasu", "r"), Prescription("1w6ts", "r2")])


# --- 骨架 + schedule_note ---
def test_build_plan_returns_schedule_note():
    plan = build_plan({}, [], [], [])
    assert isinstance(plan, TrainingPlan)
    assert "每周" in plan.schedule_note or "间隔" in plan.schedule_note


# --- 数据不足降级 ---
def test_build_plan_insufficient_history_note():
    hist = [_session({}, "2026-06-01")]
    plan = build_plan({}, [], hist, [])
    assert any("不足" in n or "N_MIN" in n for n in plan.notes)


# --- stall -> interleave ---
def test_build_plan_stall_triggers_interleave():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]  # 4 sessions >= N_MIN=3
    comp = [_row("sparc", "same")]
    findings = [_finding("sparc low")]
    plan = build_plan({}, comp, hist, findings)
    assert "sparc" in plan.focus_metrics
    inter = [a for a in plan.adjustments if a.kind == "interleave"]
    assert len(inter) == 1 and inter[0].target_metric == "sparc"
    # 元认知对抗话术
    assert "感觉进步快" in inter[0].reason or "过度自信" in inter[0].reason
    # 渐进 hybrid + 元认知锚点
    assert any("hybrid" in a or "渐进" in a for a in plan.evidence_anchors) or \
           any("元认知" in a for a in plan.evidence_anchors)


# --- worse -> regress_focus ---
def test_build_plan_regress_triggers_regress_focus():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("linearity", "worse")]
    findings = [_finding("linearity high")]
    plan = build_plan({}, comp, hist, findings)
    assert "linearity" in plan.focus_metrics
    reg = [a for a in plan.adjustments if a.kind == "regress_focus"]
    assert len(reg) == 1 and reg[0].target_metric == "linearity"


# --- better -> maintain, scenarios 可空 ---
def test_build_plan_better_maintains():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("decel_frac", "better")]
    plan = build_plan({}, comp, hist, [])  # no finding -> scenarios empty
    maint = [a for a in plan.adjustments if a.kind == "maintain"]
    assert len(maint) == 1
    assert maint[0].scenarios == []  # decel_frac in health band -> no finding -> empty
    assert "decel_frac" not in plan.focus_metrics


# --- rest: 间隔 < REST_GAP_DAYS ---
def test_build_plan_rest_high_frequency():
    # 两次 session 同一天（间隔 0 天 < 1.0）
    hist = [
        _session({}, "2026-06-29T10:00:00"),
        _session({}, "2026-06-29T18:00:00"),
        _session({}, "2026-06-29T20:00:00"),
    ]
    plan = build_plan({}, [], hist, [])
    rests = [a for a in plan.adjustments if a.kind == "rest"]
    assert len(rests) == 1
    assert rests[0].target_metric is None


def test_build_plan_no_rest_when_spaced():
    hist = [_session({}, f"2026-06-{d:02d}") for d in (1, 5, 10)]  # 间隔 >=4 天
    plan = build_plan({}, [], hist, [])
    assert not any(a.kind == "rest" for a in plan.adjustments)


# --- focus 按 severity 排序 ---
def test_build_plan_focus_severity_order():
    hist = [_session({}, f"2026-06-0{i}") for i in range(1, 5)]
    comp = [_row("reverse_ratio", "same"), _row("sparc", "same")]
    findings = [
        _finding("reverse_ratio high", "watch"),  # weight 2
        _finding("sparc low", "fix"),              # weight 3 — 应排前
    ]
    plan = build_plan({}, comp, hist, findings)
    # sparc(fix) 在 reverse_ratio(watch) 前
    assert plan.focus_metrics.index("sparc") < plan.focus_metrics.index("reverse_ratio")
