from kovaak_tracker.coach.report import build_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


def test_build_report_without_backend():
    r = build_report(_summary(), None, {"cm_per_360": 48.0})
    assert r.diagnosis.profile.archetype_id in ("long_decel", "decel_jitter", "unclassified")
    assert r.narration is None
    assert "radar" in r.figures
    assert r.notes == []


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
    r = build_report(_summary(), ref, {})
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


def test_static_clicking_prescriptions_keep_the_training_direction_without_faction_labels():
    from kovaak_tracker.advice import advise

    findings = advise({
        "decel_frac": {"med": 0.8},
        "reverse_ratio": {"med": 0.3},
        "submovement_overlap": {"med": 0.2},
    })
    reasons = [
        prescription.reason
        for finding in findings
        for prescription in finding.prescriptions
    ]

    assert all(finding.claim_level == "experimental" for finding in findings)
    assert all("流体派" not in reason for reason in reasons)
    assert all("overlapping submovements" not in reason for reason in reasons)
    assert "练完整的加速→减速，接近目标时果断完成制动" in reasons
    assert "把修正并入减速过程，避免停住后再二次修正" in reasons
    assert "尝试让主要移动和收尾修正保持衔接，减少停住后再单独修正" in reasons
    assert all("可比条件下复测" not in reason for reason in reasons)


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
    """
    from kovaak_tracker.advice import compare_table
    self_sum = {"decel_frac": {"med": 0.30}}   # < 0.40 pathological brake-slam
    ref_sum = {"decel_frac": {"med": 0.50}}    # healthy
    rows = {r["metric"]: r for r in compare_table(self_sum, ref_sum)}
    assert rows["decel_frac"]["verdict"] == "info"


def test_build_report_survives_visualization_import_failure(monkeypatch):
    # The deterministic report must not die when the numpy/plotly stack is
    # unavailable: figures degrade to {} with a note instead.
    import sys
    monkeypatch.setitem(sys.modules, "kovaak_tracker.coach.visualization", None)
    r = build_report(_summary(), None, {"cm_per_360": 48.0})
    assert r.diagnosis is not None
    assert r.figures == {}
    assert any("图表不可用" in n for n in r.notes)
