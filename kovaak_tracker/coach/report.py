"""End-to-end: fair summary -> CoachReport, with history persistence +
progress reports (scope B progress loop). Wires advice + diagnosis +
visualization + agent (tool-use), with degradation."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..advice import advise, compare_table
from ..advice_tracking import advise_tracking, _flatten_metrics
from .diagnosis import build_diagnosis, CoachReport
from .planning import build_plan
from .progress import (
    save_session, load_history, build_trend, build_comparison, ProgressReport,
)

if TYPE_CHECKING:
    from .providers import ToolUseBackend

# The agent (Provider tool-use) and visualization (numpy/plotly) stacks are
# imported lazily at their call sites: the deterministic report path
# (backend=None, as used by the analysis worker) does not functionally depend
# on them, so an import failure there must degrade the report, not kill it.


def _is_tracking_summary(summary) -> bool:
    """Heuristic: tracking metrics.json shape has tension/loss groups or
    tracking-specific scalars."""
    if not isinstance(summary, dict):
        return False
    if "tension" in summary or "loss" in summary:
        return True
    return any(k in summary for k in ("on_target_pct", "ptc", "loss_count"))


def build_report(summary, reference_summary=None, meta=None,
                 backend: ToolUseBackend | None = None, history_path=None) -> CoachReport:
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
        # Normalize nested metrics.json shape so downstream (visualization /
        # history) sees a flat scalar dict, matching flicking summary's flatness.
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

    narration = None
    if backend is not None:
        try:
            from .agent import narrate_diagnosis
            narration = narrate_diagnosis(diagnosis, backend)
        except Exception as e:  # narration is best-effort; never block the report
            notes.append(f"讲解不可用: {e}")
        if narration is None and not any("讲解不可用" in n for n in notes):
            # agent loop exhausted turns / hit exception path without raising
            notes.append("讲解不可用: agent 未在限定轮次内产出文本")

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
                          meta=None, backend: ToolUseBackend | None = None) -> ProgressReport:
    """Trend + comparison + plan + narrations over saved history."""
    meta = meta or {}
    history = load_history(history_path)
    trend = build_trend(history)
    comparison = build_comparison(history, current_summary, ref_summary)

    # 处方场景池：现跑 advise（自包含，纯函数开销可忽略）
    findings = advise(current_summary, ref_summary, cm_per_360=meta.get("cm_per_360"))
    plan = build_plan(trend, comparison, history, findings)

    notes: list[str] = []
    if not history:
        notes.append("首次分析，无历史可比")

    progress_narration = None
    plan_narration = None
    if backend is not None:
        try:
            from .agent import narrate_progress, narrate_plan
        except Exception as e:
            notes.append(f"讲解不可用: {e}")
        else:
            try:
                progress_narration = narrate_progress(trend, comparison, backend)
            except Exception as e:
                notes.append(f"进步讲解不可用: {e}")
            if progress_narration is None and not any("进步讲解不可用" in n for n in notes):
                notes.append("进步讲解不可用: agent 未在限定轮次内产出文本")
            try:
                plan_narration = narrate_plan(plan, backend)
            except Exception as e:
                notes.append(f"计划讲解不可用: {e}")
            if plan_narration is None and not any("计划讲解不可用" in n for n in notes):
                notes.append("计划讲解不可用: agent 未在限定轮次内产出文本")

    try:
        from .visualization import build_trend_figure, build_comparison_figure
        trend_figure = build_trend_figure(trend)
        comparison_figure = build_comparison_figure(comparison)
    except Exception as e:  # presentation-only; degrade, don't fail
        notes.append(f"图表不可用: {e}")
        trend_figure = None
        comparison_figure = None

    return ProgressReport(
        trend_figure=trend_figure,
        comparison_figure=comparison_figure,
        comparison_table=comparison,
        progress_narration=progress_narration,
        plan=plan,
        plan_narration=plan_narration,
        notes=notes,
    )
