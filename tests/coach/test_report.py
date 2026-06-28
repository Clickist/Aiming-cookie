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
        def generate(self, s, u):
            raise RuntimeError("network down")
    r = build_report(_summary(), None, {}, backend=_Boom())
    assert r.narration is None
    assert any("讲解不可用" in n for n in r.notes)


def test_build_report_with_reference():
    ref = _summary()
    ref["decel_frac"] = {"med": 0.45}
    r = build_report(_summary(), ref, {}, backend=None)
    assert r.diagnosis.comparison is not None
    assert len(r.diagnosis.comparison) > 0
