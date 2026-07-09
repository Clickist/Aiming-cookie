"""CoachDiagnosis contract + builder. Consumes advice.findings, produces the
structured diagnosis that visualization and agent both consume."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..advice import Finding, Prescription


@dataclass(frozen=True)
class RootCause:
    level: str   # "symptom" | "physical" | "training"
    text: str


@dataclass(frozen=True)
class ProfileMatch:
    archetype_id: str
    label: str
    confidence: float
    secondary_tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisIssue:
    signal: str
    severity: str
    root_causes: list[RootCause]
    prescriptions: list[Prescription]
    priority: int
    priority_reason: str


@dataclass(frozen=True)
class CoachDiagnosis:
    profile: ProfileMatch
    issues: list[DiagnosisIssue]
    summary: dict
    comparison: list[dict] | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CoachReport:
    diagnosis: CoachDiagnosis
    figures: dict[str, Any]
    narration: str | None
    notes: list[str] = field(default_factory=list)


from . import profiles

_MATCH_THRESHOLD = 0.5
_SEVERITY_WEIGHT = {"fix": 3, "watch": 2, "info": 1}


def build_diagnosis(findings, summary, comparison, meta):
    return CoachDiagnosis(
        profile=_match_profile(findings, meta),
        issues=_build_issues(findings),
        summary=summary,
        comparison=comparison,
        meta=meta or {},
    )


def _match_profile(findings, meta=None):
    meta = meta or {}
    summary_type = meta.get("summary_type")
    signals = {f.signal for f in findings}
    best, best_score = None, 0.0
    for arch in profiles.ARCHETYPES:
        conds = arch["conditions"]
        if not conds:
            continue
        hit_w = sum(w for sig, w in conds.items() if sig in signals)
        total_w = sum(conds.values())
        score = hit_w / total_w if total_w else 0.0
        if score > best_score:
            best, best_score = arch, score
    secondary = [
        a["label"] for a in profiles.ARCHETYPES
        if a is not best and a["conditions"]
        and any(s in signals for s in a["conditions"])
    ]
    # fluid_precise / fluid_tracker: positive profile, matched when no negative
    # signals fire. Pick by summary_type (flicking vs tracking).
    if (best is None or best_score < _MATCH_THRESHOLD) and not signals:
        if summary_type == "tracking":
            fluid = next(
                (a for a in profiles.ARCHETYPES if a["id"] == "fluid_tracker"),
                None,
            )
        else:
            fluid = next(
                (a for a in profiles.ARCHETYPES if a["id"] == "fluid_precise"),
                None,
            )
        if fluid is not None:
            return ProfileMatch(fluid["id"], fluid["label"], 1.0, [])
    if best is None or best_score < _MATCH_THRESHOLD:
        return ProfileMatch("unclassified", "未分类", round(best_score, 2), secondary)
    return ProfileMatch(best["id"], best["label"], round(best_score, 2), secondary)


def _build_issues(findings):
    enriched = [(f, _root_causes_for(f)) for f in findings]
    # priority by severity weight (deviation left to advice thresholds; YAGNI this version)
    enriched.sort(key=lambda x: (-_SEVERITY_WEIGHT.get(x[0].severity, 1),))
    issues = []
    for rank, (f, rcs) in enumerate(enriched, 1):
        issues.append(DiagnosisIssue(
            signal=f.signal,
            severity=f.severity,
            root_causes=rcs,
            prescriptions=list(f.prescriptions),
            priority=rank,
            priority_reason=f"[{f.severity}] 严重度排序第 {rank}",
        ))
    return issues


def _root_causes_for(finding):
    triple = profiles.ROOT_CAUSES.get(finding.signal)
    if not triple:
        return [RootCause("symptom", finding.diagnosis)]
    return [
        RootCause("symptom", triple[0]),
        RootCause("physical", triple[1]),
        RootCause("training", triple[2]),
    ]
