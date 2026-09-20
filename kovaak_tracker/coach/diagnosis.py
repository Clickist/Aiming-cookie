"""CoachDiagnosis contract + builder. Consumes advice.findings, produces the
structured diagnosis that visualization and agent both consume."""
from __future__ import annotations

import math
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
    plain_language_meaning: str = ""
    claim_level: str = "deterministic_rule"
    metric_refs: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    expected_result: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    primary_evidence_segment_ref: str | None = None
    supporting_evidence_segment_refs: list[str] = field(default_factory=list)
    observation_ref: str | None = None
    knowledge_registry_version: str | None = None
    knowledge_entry_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.supporting_evidence_segment_refs) > 2:
            raise ValueError("supporting evidence segments must be at most two")


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


@dataclass(frozen=True)
class CandidateKnowledgeRefs:
    registry_version: str
    entry_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.entry_refs) > 3 or any(
            not isinstance(ref, str) or not ref.startswith("knowledge:")
            for ref in self.entry_refs
        ):
            raise ValueError("candidate knowledge refs are invalid")


def resolve_candidate_knowledge_refs(
    *,
    issue_signal: str | None,
    metric_refs: list[str],
) -> CandidateKnowledgeRefs:
    """Attach v2 explanations after an analyzer has already produced a fact."""
    from .knowledge_active import load_active_registry
    from .knowledge_registry import entry_ref, query_registry

    registry = load_active_registry()
    entries = query_registry(
        registry,
        issue_signal=issue_signal,
        metric_refs=metric_refs,
    )
    # query_registry is bounded by MAX_RESULTS (8); candidate annotations are
    # capped at 3 by the frozen diagnosis contract, so keep the top-ranked 3.
    return CandidateKnowledgeRefs(
        registry_version=registry["registry_version"],
        entry_refs=[entry_ref(entry) for entry in entries[:3]],
    )


from . import profiles

_MATCH_THRESHOLD = 0.5
_SEVERITY_WEIGHT = {"fix": 3, "watch": 2, "info": 1}
_STATIC_OBSERVATION_REFS = {
    "reverse_ratio high": "metric.terminal_control",
    "submovement two-stage": "metric.terminal_control",
    "sparc low": "metric.terminal_control",
}


def _active_archetypes() -> list[dict]:
    """Archetypes from the active mapping's ``archetypes`` section, shaped
    exactly like ``profiles.ARCHETYPES`` (WP-06: the data source moved to the
    mapping; the weighted-match algorithm below is unchanged). A missing
    mapping file (the normal built-in fallback state) or an empty/unusable
    section falls back to the frozen ``profiles`` constants."""
    from .knowledge_active import load_active_mapping

    try:
        doc, _reason = load_active_mapping()
        if doc is None:
            return profiles.ARCHETYPES
        raw = doc["archetypes"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, list) or not raw:
            return profiles.ARCHETYPES
        archetypes = []
        for entry in raw:
            conditions_raw = entry["conditions"]
            if isinstance(conditions_raw, (str, bytes)) or not isinstance(conditions_raw, dict):
                raise ValueError("archetype conditions must be an object")
            conditions: dict[str, float] = {}
            for signal, weight in conditions_raw.items():
                if (
                    not isinstance(signal, str)
                    or isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(weight)
                ):
                    raise ValueError("archetype weight must be a finite number")
                conditions[signal] = float(weight)
            if not isinstance(entry["id"], str) or not isinstance(entry["label"], str):
                raise ValueError("archetype id/label must be text")
            archetypes.append({
                "id": entry["id"],
                "label": entry["label"],
                "conditions": conditions,
            })
        return archetypes
    except Exception:
        return profiles.ARCHETYPES


def _active_root_causes() -> dict[str, tuple[str, str, str]]:
    """Root-cause copy from the active mapping's ``root_causes`` section
    (signal -> symptom/physical/training). Same fallback semantics as
    :func:`_active_archetypes`: a missing mapping/section or unusable data
    falls back to the frozen ``profiles`` constants."""
    from .knowledge_active import load_active_mapping

    try:
        doc, _reason = load_active_mapping()
        if doc is None:
            return profiles.ROOT_CAUSES
        raw = doc["root_causes"]
        if not isinstance(raw, dict) or not raw:
            return profiles.ROOT_CAUSES
        causes: dict[str, tuple[str, str, str]] = {}
        for signal, triple in raw.items():
            if (
                not isinstance(signal, str)
                or isinstance(triple, (str, bytes))
                or not isinstance(triple, list)
                or len(triple) != 3
                or any(not isinstance(layer, str) for layer in triple)
            ):
                raise ValueError("root cause entry must be three copy strings")
            causes[signal] = (triple[0], triple[1], triple[2])
        return causes
    except Exception:
        return profiles.ROOT_CAUSES


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
    quality_status = meta.get("quality_status")
    signals = {f.signal for f in findings}
    archetypes = _active_archetypes()
    best, best_score = None, 0.0
    for arch in archetypes:
        conds = arch["conditions"]
        if not conds:
            continue
        hit_w = sum(w for sig, w in conds.items() if sig in signals)
        total_w = sum(conds.values())
        score = hit_w / total_w if total_w else 0.0
        if score > best_score:
            best, best_score = arch, score
    secondary = [
        a["label"] for a in archetypes
        if a is not best and a["conditions"]
        and any(s in signals for s in a["conditions"])
    ]
    # fluid_precise / fluid_tracker: positive profile, matched when no negative
    # signals fire. Pick by summary_type (flicking vs tracking).
    if (best is None or best_score < _MATCH_THRESHOLD) and not signals:
        if quality_status not in {None, "available", "accepted"}:
            return ProfileMatch("unclassified", "未分类", 0.0, [])
        if summary_type == "tracking":
            fluid = next(
                (a for a in archetypes if a["id"] == "fluid_tracker"),
                None,
            )
        else:
            fluid = next(
                (a for a in archetypes if a["id"] == "fluid_precise"),
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
        observation_ref = _STATIC_OBSERVATION_REFS.get(f.signal)
        registry_version = None
        knowledge_entry_refs: list[str] = []
        if observation_ref is not None:
            knowledge = resolve_candidate_knowledge_refs(
                issue_signal=f.signal,
                # Registry tokens are "metric:"-prefixed; bare analyzer
                # metric names would never intersect them.
                metric_refs=[
                    ref if ref.startswith("metric:") else f"metric:{ref}"
                    for ref in f.metric_refs
                ],
            )
            # 撤销"只留 terminal-control"的冻结期前缀过滤（09-10 拍板）：静态
            # 标注恢复匹配器 top-3。v11 起匹配承接面扩大，过滤只会丢对症条目
            # （sparc low 本应同时带上平滑度条目）。
            knowledge_entry_refs = list(knowledge.entry_refs)
            if knowledge_entry_refs:
                registry_version = knowledge.registry_version
            else:
                observation_ref = None
        if f.claim_level == "experimental" or f.severity == "info":
            priority_reason = "本次优先观察项"
        else:
            priority_reason = "本次优先处理项"
        issues.append(DiagnosisIssue(
            signal=f.signal,
            severity=f.severity,
            root_causes=rcs,
            prescriptions=list(f.prescriptions),
            priority=rank,
            priority_reason=priority_reason,
            plain_language_meaning=f.plain_language_meaning or f.diagnosis,
            claim_level=f.claim_level,
            metric_refs=list(f.metric_refs),
            event_refs=list(f.event_refs),
            limitations=list(f.limitations),
            expected_result=f.expected_result,
            verification=dict(f.verification),
            observation_ref=observation_ref,
            knowledge_registry_version=registry_version,
            knowledge_entry_refs=knowledge_entry_refs,
        ))
    return issues


def _root_causes_for(finding):
    triple = _active_root_causes().get(finding.signal)
    if not triple:
        return [RootCause("symptom", finding.diagnosis)]
    return [
        RootCause("symptom", triple[0]),
        RootCause("physical", triple[1]),
        RootCause("training", triple[2]),
    ]
