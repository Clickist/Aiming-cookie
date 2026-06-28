"""End-to-end: fair summary -> CoachReport. Wires advice + diagnosis +
visualization + narrator, with degradation (structured + viz always produced;
narration is best-effort)."""
from __future__ import annotations

from ..advice import advise, compare_table
from .diagnosis import build_diagnosis, CoachReport
from .visualization import build_figures
from .narrator import generate_narration
from .providers import LLMBackend


def build_report(summary, reference_summary=None, meta=None,
                 backend: LLMBackend | None = None) -> CoachReport:
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

    return CoachReport(
        diagnosis=diagnosis, figures=figures, narration=narration, notes=notes,
    )
