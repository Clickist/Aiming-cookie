from kovaak_tracker.coach import build_report


def _summary():
    return {k: {"med": v} for k, v in {
        "peak_speed_deg": 106, "linearity": 0.17, "sparc": -7.5,
        "reverse_ratio": 0.23, "decel_frac": 0.75, "endpoint_peak": 0.2,
        "peak_position_pct": 35, "path_efficiency": 0.96, "path_length_deg": 12,
        "corrective_count": 1.5, "submovement_overlap": 0.25, "throughput": 3.2,
    }.items()}


def test_e2e_full_pipeline_no_llm():
    r = build_report(_summary(), None, {"cm_per_360": 48.0})
    assert r.diagnosis.profile.confidence > 0
    assert len(r.diagnosis.issues) >= 1
    assert {"radar", "decel_curve", "comparison", "issue_list", "profile_card"} <= set(r.figures)
    assert r.narration is None


def test_e2e_real_user_data_matches_known_profile():
    # the user's known data (PROGRESS baseline) decel_frac 0.75 + reverse 0.23 + sparc low
    r = build_report(_summary(), None, {})
    labels = r.diagnosis.profile.label
    assert "长减速" in labels or "抖动" in labels  # hits long_decel or decel_jitter
