from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, ProfileMatch, DiagnosisIssue, RootCause,
)
from kovaak_tracker.coach.visualization import build_figures


def _diag():
    summary = {
        "decel_frac": {"med": 0.75}, "linearity": {"med": 0.17},
        "sparc": {"med": -7.5}, "reverse_ratio": {"med": 0.23},
        "path_efficiency": {"med": 0.96}, "peak_speed_deg": {"med": 106},
    }
    comparison = [{"metric": "decel_frac", "self": 0.75, "ref": 0.45, "verdict": "worse"}]
    issue = DiagnosisIssue(
        signal="sparc low", severity="fix",
        root_causes=[RootCause("symptom", "s"), RootCause("physical", "p"), RootCause("training", "t")],
        prescriptions=[], priority=1, priority_reason="[fix]",
    )
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, ["发力不足"]),
        issues=[issue], summary=summary, comparison=comparison, meta={},
    )


def test_build_figures_returns_all_keys():
    figs = build_figures(_diag())
    for k in ("radar", "decel_curve", "comparison", "issue_list", "profile_card"):
        assert k in figs


def test_radar_is_figure_object():
    import plotly.graph_objects as go
    figs = build_figures(_diag())
    assert isinstance(figs["radar"], go.Figure)


def test_comparison_handles_none():
    d = _diag()
    from dataclasses import replace
    d = replace(d, comparison=None)
    figs = build_figures(d)  # must not raise
    assert "comparison" in figs
