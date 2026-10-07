"""End-to-end: fair summary -> CoachReport. Wires advice + diagnosis +
visualization, with degradation."""
from __future__ import annotations

from ..advice import advise, compare_table
from ..advice_tracking import advise_tracking, _flatten_metrics
from .diagnosis import build_diagnosis, CoachReport
from .standard_anchors import expand_prescriptions

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


def _expand_anchor_prescriptions(findings, meta) -> None:
    """施工单⑥：把能力域级处方按标准答案锚点表扩成场景级处方（就地改写）。

    三路接力：本机已装优先 → 未装标注需订阅 → 无对症保留域处方并提示
    定制/search。install_dir 缺省时全部标 unverified（fail-open，绝不伪造
    "本机已装"）。
    """
    from .labels import catalog

    locale = meta.get("locale") if isinstance(meta.get("locale"), str) else "zh-CN"
    domain_names = catalog(locale).CAPABILITY_DOMAINS
    for finding in findings:
        finding.prescriptions = expand_prescriptions(
            finding.prescriptions,
            install_dir=meta.get("install_dir"),
            locale=locale,
            domain_names=domain_names,
        )


def build_report(summary, reference_summary=None, meta=None) -> CoachReport:
    meta = meta or {}
    summary_type = meta.get("summary_type")
    # B3 i18n：locale 经 meta 传入（worker 侧来自 job locale；缺省 zh-CN）。
    locale = meta.get("locale") if isinstance(meta.get("locale"), str) else "zh-CN"
    scenario_reading = (
        meta.get("scenario_reading")
        if isinstance(meta.get("scenario_reading"), dict)
        else None
    )
    # Route to tracking vs flicking advice (spec §5.2 — explicit > implicit).
    # Fallback when summary_type is unset: probe summary shape.
    if summary_type == "tracking" or (
        summary_type is None and _is_tracking_summary(summary)
    ):
        findings = advise_tracking(
            summary,
            cm_per_360=meta.get("cm_per_360"),
            ball_w=meta.get("ball_w"),
            locale=locale,
        )
        _expand_anchor_prescriptions(findings, meta)
        # Normalize nested metrics.json shape so downstream (visualization)
        # sees a flat scalar dict, matching flicking summary's flatness.
        flat_summary = _flatten_metrics(summary)
        comparison = None  # v1 self-only (spec §8.3)
        diagnosis = build_diagnosis(findings, flat_summary, comparison, meta)
    else:
        findings = advise(
            summary, reference_summary,
            cm_per_360=meta.get("cm_per_360"), locale=locale,
            scenario_reading=scenario_reading,
        )
        _expand_anchor_prescriptions(findings, meta)
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
