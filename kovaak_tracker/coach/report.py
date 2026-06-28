"""End-to-end: fair summary -> CoachReport, with history persistence +
progress reports (scope B progress loop). Wires advice + diagnosis +
visualization + narrator, with degradation."""
from __future__ import annotations

from ..advice import advise, compare_table
from .diagnosis import build_diagnosis, CoachReport
from .visualization import build_figures, build_trend_figure, build_comparison_figure
from .narrator import generate_narration, generate_progress_narration
from .progress import (
    save_session, load_history, build_trend, build_comparison, ProgressReport,
)
from .providers import LLMBackend


def build_report(summary, reference_summary=None, meta=None,
                 backend: LLMBackend | None = None, history_path=None) -> CoachReport:
    meta = meta or {}
    findings = advise(summary, reference_summary, cm_per_360=meta.get("cm_per_360"))
    comparison = compare_table(summary, reference_summary) if reference_summary else None
    diagnosis = build_diagnosis(findings, summary, comparison, meta)

    figures = build_figures(diagnosis)

    narration = None
    notes: list[str] = []
    if backend is not None:
        try:
            narration = generate_narration(diagnosis, backend)
        except Exception as e:  # narration is best-effort; never block the report
            notes.append(f"讲解不可用: {e}")

    report = CoachReport(
        diagnosis=diagnosis, figures=figures, narration=narration, notes=notes,
    )
    if history_path is not None:
        try:
            save_session(report, meta, history_path)
        except Exception as e:  # history save is best-effort too
            report.notes.append(f"历史保存失败: {e}")
    return report


def build_progress_report(history_path, current_summary, ref_summary=None,
                          meta=None, backend: LLMBackend | None = None) -> ProgressReport:
    """Trend + comparison + progress narration over saved history."""
    history = load_history(history_path)
    trend = build_trend(history)
    comparison = build_comparison(history, current_summary, ref_summary)

    notes: list[str] = []
    if not history:
        notes.append("首次分析，无历史可比")

    narration = None
    if backend is not None:
        try:
            narration = generate_progress_narration(trend, comparison, backend)
        except Exception as e:
            notes.append(f"进步讲解不可用: {e}")

    return ProgressReport(
        trend_figure=build_trend_figure(trend),
        comparison_figure=build_comparison_figure(comparison),
        comparison_table=comparison,
        progress_narration=narration,
        notes=notes,
    )
