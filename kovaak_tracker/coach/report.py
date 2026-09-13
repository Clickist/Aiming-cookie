"""End-to-end: fair summary -> CoachReport. Wires advice + diagnosis +
visualization, with degradation."""
from __future__ import annotations

from ..advice import advise, compare_table
from ..advice_tracking import advise_tracking, _flatten_metrics
from .diagnosis import build_diagnosis, CoachReport

# visualization (numpy/plotly) is imported lazily at its call site: the
# deterministic report path (as used by the analysis worker) must degrade
# rather than die if that import fails.


def _is_tracking_summary(summary) -> bool:
    """Heuristic: tracking metrics.json shape has tension/loss groups or
    tracking-specific scalars."""
    if not isinstance(summary, dict):
        return False
    if "tension" in summary or "loss" in summary:
        return True
    return any(k in summary for k in ("on_target_pct", "ptc", "loss_count"))


def build_report(summary, reference_summary=None, meta=None) -> CoachReport:
    meta = meta or {}
    summary_type = meta.get("summary_type")
    # Route to tracking vs flicking advice (spec §5.2 — explicit > implicit).
    # Fallback when summary_type is unset: probe summary shape.
    if summary_type == "tracking" or (
        summary_type is None and _is_tracking_summary(summary)
    ):
        findings = advise_tracking(
            summary,
            cm_per_360=meta.get("cm_per_360"),
            ball_w=meta.get("ball_w"),
        )
        # Normalize nested metrics.json shape so downstream (visualization)
        # sees a flat scalar dict, matching flicking summary's flatness.
        flat_summary = _flatten_metrics(summary)
        comparison = None  # v1 self-only (spec §8.3)
        diagnosis = build_diagnosis(findings, flat_summary, comparison, meta)
    else:
        findings = advise(summary, reference_summary, cm_per_360=meta.get("cm_per_360"))
        comparison = compare_table(summary, reference_summary) if reference_summary else None
        diagnosis = build_diagnosis(findings, summary, comparison, meta)

    figures: dict = {}
    notes: list[str] = []
    try:
        from .visualization import build_figures
        figures = build_figures(diagnosis)
    except Exception as e:  # figures are presentation-only; degrade, don't fail
        notes.append(f"图表不可用: {e}")

    report = CoachReport(
        diagnosis=diagnosis, figures=figures, narration=None, notes=notes,
    )
    return report
