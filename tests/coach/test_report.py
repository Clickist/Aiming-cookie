from kovaak_tracker.coach.report import build_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


def test_build_report_without_backend():
    r = build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None)
    assert r.diagnosis.profile.archetype_id in ("long_decel", "decel_jitter", "unclassified")
    assert r.narration is None
    assert "radar" in r.figures
    assert r.notes == []


def test_build_report_llm_failure_degrades():
    class _Boom:
        def messages_create(self, *, system, messages, tools, max_tokens=2048):
            raise RuntimeError("network down")
    r = build_report(_summary(), None, {}, backend=_Boom())
    assert r.narration is None
    assert any("讲解不可用" in n for n in r.notes)


def test_sparc_v2_does_not_use_the_legacy_absolute_threshold():
    from kovaak_tracker.advice import advise

    findings = advise({
        "sparc": {
            "med": -7.0,
            "metric_version": "native_flicking.sparc.v2",
        },
    })

    assert all(finding.signal != "sparc low" for finding in findings)


def test_build_report_with_reference():
    ref = _summary()
    ref["decel_frac"] = {"med": 0.45}
    r = build_report(_summary(), ref, {}, backend=None)
    assert r.diagnosis.comparison is not None
    assert len(r.diagnosis.comparison) > 0


def test_uncalibrated_threshold_finding_is_not_formal_severity():
    """Initial absolute thresholds remain hypotheses until product calibration."""
    from kovaak_tracker.advice import advise

    findings = advise({"sparc": {"med": -7.0}})
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert findings[0].claim_level == "experimental"
    assert "threshold_requires_product_calibration" in findings[0].limitations
    assert "张力释放抖动" not in findings[0].diagnosis
    assert "不直接测量握持张力" in findings[0].diagnosis
    assert findings[0].verification["comparable_requirements"] == [
        "相同场景",
        "相同设置",
        "相同证据质量",
    ]


def test_reference_comparison_does_not_claim_measured_physical_cause():
    """Relative performance gaps must not be narrated as measured body causes."""
    from kovaak_tracker.advice import advise

    findings = advise(
        {
            "peak_speed_deg": {"med": 500.0},
            "throughput": {"med": 2.0},
        },
        {
            "peak_speed_deg": {"med": 1000.0},
            "throughput": {"med": 4.0},
        },
    )

    assert {finding.signal for finding in findings} == {
        "peak_speed below reference",
        "throughput below reference",
    }
    assert all(finding.claim_level == "experimental" for finding in findings)
    assert all("发力不足" not in finding.diagnosis for finding in findings)
    assert all("身体原因" in finding.diagnosis for finding in findings)


def test_compare_table_decel_frac_pathological_not_better():
    """decel_frac 病态值（self=0.30 刹车太急）不该被判 better。

    回归保护：decel_frac 是带状指标（健康 [0.40, 0.65]），compare_table
    标 info 让 advise() 的带状判定主导，而非把病态值误判为进步。
    与 coach/progress._decel_frac_verdict 语义一致。
    """
    from kovaak_tracker.advice import compare_table
    self_sum = {"decel_frac": {"med": 0.30}}   # < 0.40 pathological brake-slam
    ref_sum = {"decel_frac": {"med": 0.50}}    # healthy
    rows = {r["metric"]: r for r in compare_table(self_sum, ref_sum)}
    assert rows["decel_frac"]["verdict"] == "info"


# --- progress loop tests (Task 4) ---
from kovaak_tracker.coach.report import build_progress_report


def test_build_report_persists_history(tmp_path):
    p = tmp_path / "sessions.jsonl"
    build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None, history_path=p)
    build_report(_summary(), None, {"cm_per_360": 48.0}, backend=None, history_path=p)
    from kovaak_tracker.coach.progress import load_history
    assert len(load_history(p)) == 2


def test_build_report_no_history_path_no_save(tmp_path):
    p = tmp_path / "sessions.jsonl"
    build_report(_summary(), None, {}, backend=None)  # no history_path
    assert not p.exists()


def test_build_progress_report_end_to_end(tmp_path):
    p = tmp_path / "sessions.jsonl"
    # seed one older, worse session directly as a JSONL line
    p.write_text(
        '{"timestamp":"2026-06-01","video_ref":"old.mp4","cm_per_360":48,'
        '"summary":{"linearity":{"med":0.25},"sparc":{"med":-9.0},'
        '"decel_frac":{"med":0.80},"reverse_ratio":{"med":0.30},'
        '"peak_speed_deg":{"med":90}},'
        '"profile":{},"issues":[],"narration":null}\n',
        encoding="utf-8",
    )
    cur = {k: {"med": v} for k, v in {
        "linearity": 0.17, "sparc": -6.0, "decel_frac": 0.74,
        "reverse_ratio": 0.22, "peak_speed_deg": 110,
    }.items()}
    rep = build_progress_report(p, cur, ref_summary=None, backend=None)
    assert rep.progress_narration is None
    assert len(rep.comparison_table) == 5
    assert any(r["metric"] == "linearity" and r["verdict"] == "better"
               for r in rep.comparison_table)
    assert rep.trend_figure is not None and rep.comparison_figure is not None
    assert not any("首次" in n for n in rep.notes)  # history was seeded


def test_build_progress_report_empty_history(tmp_path):
    p = tmp_path / "nope.jsonl"
    rep = build_progress_report(p, _summary(), backend=None)
    assert any("首次" in n for n in rep.notes)


# --- plan integration tests (④ Task 4) ---

def test_build_progress_report_includes_plan(tmp_path):
    """build_progress_report 应产出 TrainingPlan + schedule_note。"""
    p = tmp_path / "sessions.jsonl"
    p.write_text(
        '{"timestamp":"2026-06-01","video_ref":"old.mp4","cm_per_360":48,'
        '"summary":{"linearity":{"med":0.25},"sparc":{"med":-9.0},'
        '"decel_frac":{"med":0.80},"reverse_ratio":{"med":0.30},'
        '"peak_speed_deg":{"med":90}},'
        '"profile":{},"issues":[],"narration":null}\n',
        encoding="utf-8",
    )
    cur = {k: {"med": v} for k, v in {
        "linearity": 0.17, "sparc": -6.0, "decel_frac": 0.74,
        "reverse_ratio": 0.22, "peak_speed_deg": 110,
    }.items()}
    rep = build_progress_report(p, cur, ref_summary=None, backend=None)
    assert rep.plan is not None
    assert "每周" in rep.plan.schedule_note or "间隔" in rep.plan.schedule_note
    assert rep.plan_narration is None  # backend=None


def test_build_progress_report_plan_narration_best_effort(tmp_path):
    """backend 失败时 plan_narration=None + note，plan 结构照常返回。"""
    p = tmp_path / "sessions.jsonl"
    p.write_text(
        '{"timestamp":"2026-06-01","video_ref":"v.mp4","cm_per_360":48,'
        '"summary":{"linearity":{"med":0.20}},'
        '"profile":{},"issues":[],"narration":null}\n',
        encoding="utf-8",
    )
    class _Boom:
        def messages_create(self, *, system, messages, tools, max_tokens=2048):
            raise RuntimeError("down")
    rep = build_progress_report(p, {"linearity": {"med": 0.18}}, backend=_Boom())
    assert rep.plan is not None
    assert any("不可用" in n for n in rep.notes)
