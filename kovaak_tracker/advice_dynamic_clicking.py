"""Comparison-only candidate observations for dynamic clicking."""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


_CANDIDATES = (
    (
        "dynamic_clicking.normalized_click_error",
        "normalized_click_error",
        "dynamic click error high",
        "event.dynamic_click",
    ),
    (
        "dynamic_clicking.acquisition_time_ms",
        "acquisition_time_ms",
        "dynamic acquisition slow",
        "event.dynamic_click",
    ),
    (
        "dynamic_clicking.relative_velocity",
        "relative_velocity_magnitude",
        "relative velocity mismatch",
        "field.relative_velocity",
    ),
)

_ADVICE_BLOCKING_LIMITATIONS = {
    "dynamic_clicking.normalized_click_error": frozenset({
        "click_geometry_visible_radius_conditioned",
        "target_identity_unresolved",
        "identity_continuity_not_observed",
        "outcome_association_unavailable",
        "target_click_association_unavailable",
    }),
    "dynamic_clicking.acquisition_time_ms": frozenset({
        "target_identity_unresolved",
        "identity_continuity_not_observed",
        "outcome_association_unavailable",
        "target_click_association_unavailable",
    }),
    "dynamic_clicking.relative_velocity": frozenset({
        "target_identity_unresolved",
        "identity_continuity_not_observed",
        "target_click_association_unavailable",
    }),
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def build_dynamic_clicking_candidate_advice(
    analysis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return observations only when a matched baseline supports comparison."""
    if analysis.get("support_status") == "outcome_only":
        return []
    comparison = analysis.get("comparison")
    if not isinstance(comparison, Mapping) or comparison.get("comparable") is not True:
        return []
    metrics = analysis.get("metrics")
    baseline = comparison.get("baseline_metrics")
    if not isinstance(metrics, Mapping) or not isinstance(baseline, Mapping):
        return []
    rows = [
        row for row in analysis.get("processed_rows") or []
        if isinstance(row, Mapping) and isinstance(row.get("event_ref"), str)
    ]
    candidates = []
    for metric_key, row_field, signal, observation_ref in _CANDIDATES:
        metric = metrics.get(metric_key)
        current = (
            _number(metric.get("value"))
            if isinstance(metric, Mapping) and metric.get("availability") == "available"
            else None
        )
        reference = _number(baseline.get(metric_key))
        metric_limitations = (
            set(metric.get("limitations") or [])
            if isinstance(metric, Mapping)
            else set()
        )
        if metric_limitations.intersection(
            _ADVICE_BLOCKING_LIMITATIONS.get(metric_key, frozenset())
        ):
            continue
        if current is None or reference is None or current <= reference:
            continue
        supporting_refs = [
            row["event_ref"] for row in rows
            if (_number(row.get(row_field)) or float("-inf")) > reference
        ]
        counterexample_refs = [
            row["event_ref"] for row in rows
            if _number(row.get(row_field)) is not None
            and float(row[row_field]) <= reference
        ]
        from .coach.diagnosis import resolve_candidate_knowledge_refs

        knowledge = resolve_candidate_knowledge_refs(
            issue_signal=signal,
            # Registry tokens are "metric:"-prefixed; the family-qualified
            # pipeline key would never intersect them.
            metric_refs=[f"metric:{row_field}"],
        )
        candidates.append({
            "signal": signal,
            "claim_level": "deterministic_rule",
            "metric_refs": [metric_key],
            "observation": {
                "current": current,
                "matched_baseline": reference,
                "delta": current - reference,
            },
            "supporting_row_refs": supporting_refs,
            "counterexample_row_refs": counterexample_refs,
            "observation_ref": observation_ref,
            "knowledge_registry_version": knowledge.registry_version,
            "knowledge_entry_refs": knowledge.entry_refs,
            "requested_knowledge_sections": [
                "definition", "mechanisms", "alternative_explanations",
                "cue", "dose_guardrail", "matched_retest", "stop_adjust_rule",
            ],
            "limitations": list(analysis.get("limitations") or []),
        })
    return candidates


__all__ = ["build_dynamic_clicking_candidate_advice"]
