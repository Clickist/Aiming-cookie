"""Comparison-only candidate observations for target switching."""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


_TRANSITION_ENTRY_REF = "knowledge:switching.transition-and-arrival@3"


def _number(value: Any) -> float | None:
    if isinstance(value, Mapping):
        if value.get("availability") == "unavailable":
            return None
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _observable_chain(row: Mapping[str, Any]) -> bool:
    classification = row.get("classification")
    if classification == "stats_bounded_switch_chain":
        return row.get("row_kind") == "switch_chain"
    if classification is not None:
        return classification == "observable_target_switch"
    return row.get("row_kind") in {"target_switch_chain", "switch_chain"}


def _knowledge_refs(
    issue_signal: str,
    knowledge_metric_ref: str,
    expected_entry_ref: str,
) -> tuple[str, list[str]]:
    from .coach.diagnosis import resolve_candidate_knowledge_refs

    knowledge = resolve_candidate_knowledge_refs(
        issue_signal=issue_signal,
        metric_refs=[knowledge_metric_ref],
    )
    return (
        knowledge.registry_version,
        [ref for ref in knowledge.entry_refs if ref == expected_entry_ref],
    )


def _candidate(
    *,
    analysis: Mapping[str, Any],
    metric_key: str,
    signal: str,
    knowledge_signal: str,
    knowledge_metric_ref: str,
    expected_entry_ref: str,
    current: float,
    baseline: float,
    supporting_rows: list[Mapping[str, Any]],
    counterexample_rows: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    registry_version, knowledge_entry_refs = _knowledge_refs(
        knowledge_signal, knowledge_metric_ref, expected_entry_ref,
    )
    if not knowledge_entry_refs:
        return None
    row_limitations = [
        limitation
        for row in [*supporting_rows, *counterexample_rows]
        for limitation in row.get("limitations", [])
        if isinstance(limitation, str)
    ]
    return {
        "signal": signal,
        "claim_level": "deterministic_rule",
        "metric_refs": [metric_key],
        "observation": {
            "current": current,
            "matched_baseline": baseline,
            "delta": current - baseline,
        },
        "supporting_row_refs": [row["event_ref"] for row in supporting_rows],
        "counterexample_row_refs": [row["event_ref"] for row in counterexample_rows],
        "observation_ref": "event.switch_chain",
        "knowledge_registry_version": registry_version,
        "knowledge_entry_refs": knowledge_entry_refs,
        "requested_knowledge_sections": [
            "definition", "mechanisms", "alternative_explanations",
            "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
            "stop_adjust_rule",
        ],
        "verification_targets": [{
            "metric_ref": metric_key,
            "expected_direction": "lower_better",
            "condition": "matched_comparable_baseline",
        }],
        "limitations": sorted(set([
            *[item for item in analysis.get("limitations", []) if isinstance(item, str)],
            *row_limitations,
        ])),
    }


def build_target_switching_candidate_advice(
    analysis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return comparison-only switching candidates with traceable row evidence."""
    comparison = analysis.get("comparison")
    if not isinstance(comparison, Mapping) or comparison.get("comparable") is not True:
        return []
    metrics = analysis.get("metrics")
    baseline_metrics = comparison.get("baseline_metrics")
    if not isinstance(metrics, Mapping) or not isinstance(baseline_metrics, Mapping):
        return []
    rows = [
        row for row in analysis.get("processed_rows") or []
        if isinstance(row, Mapping) and isinstance(row.get("event_ref"), str)
    ]
    observable_rows = [row for row in rows if _observable_chain(row)]
    candidates: list[dict[str, Any]] = []

    for metric_key, row_field, signal, knowledge_metric_ref in (
        (
            "target_switching.transition_time_ms",
            "transition_time_ms",
            "switch transition slow",
            "metric:inter_target_transition",
        ),
        (
            "target_switching.settle_duration_ms",
            "settle_duration_ms",
            "switch arrival error high",
            "metric:next_target_acquisition",
        ),
    ):
        current = _number(metrics.get(metric_key))
        baseline = _number(baseline_metrics.get(metric_key))
        if current is None or baseline is None or current <= baseline:
            continue
        supporting_rows = [
            row for row in observable_rows
            if (value := _number(row.get(row_field))) is not None and value > baseline
        ]
        counterexample_rows = [
            row for row in observable_rows
            if (value := _number(row.get(row_field))) is not None and value <= baseline
        ]
        if not supporting_rows:
            continue
        candidate = _candidate(
            analysis=analysis,
            metric_key=metric_key,
            signal=signal,
            knowledge_signal=signal,
            knowledge_metric_ref=knowledge_metric_ref,
            expected_entry_ref=_TRANSITION_ENTRY_REF,
            current=current,
            baseline=baseline,
            supporting_rows=supporting_rows,
            counterexample_rows=counterexample_rows,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


__all__ = ["build_target_switching_candidate_advice"]
