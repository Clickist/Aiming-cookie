"""CoachDiagnosis contract + builder. Consumes advice.findings, produces the
structured diagnosis that visualization and narrator both consume."""
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
