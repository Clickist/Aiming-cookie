"""Evidence query subsystem and tool bridge infrastructure.

Extracted from :mod:`coach_commands` to isolate the evidence query domain
(metric distribution, event predicates, signal windows, outcome timelines)
and the in-memory tool bridge principal (bearer-scoped execution sandbox).

This module imports shared helpers from :mod:`coach_commands`; the parent
module re-exports the public surface back so external callers are unaffected.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from kovaak_tracker.analysis_evidence import (
    EvidenceKeyRegistry,
    build_processed_event_table_catalog,
    build_page_descriptor_v1,
    page_normalized_outcomes,
    validate_normalized_outcome_timeline_v1,
)

from . import coach_context_refs, coach_commands, evidence_store, queue
from .coach_commands import (
    RESULT_SCHEMA_VERSION,
    TOOL_BRIDGE_ENDPOINT,
    ProductCommandError,
    _EVIDENCE_QUERY_COMMANDS,
    _KOVAAK_SCORE_COMMANDS,
    _REF_RE,
    _STEAM_ID,
    _STEAM_PROFILE_REF,
    _TOOL_BRIDGE_PAYLOAD_KEYS,
    _WRITE_COMMANDS,
    _audit_context,
    _bounded_kovaak_score_summary,
    _command_id,
    _contains_forbidden_model_data,
    _copy_json,
    _error,
    _exact_parameters,
    _finish,
    _idempotency_digest,
    _journal,
    _parse_ref,
    _require_mapping,
    _result,
    _risk_for,
    _safe_parameter_summary,
)
from .contracts import project_evidence_segment


_EVIDENCE_QUERY_RESULT_SCHEMA = "coach_evidence_query_result.v1"
_EVIDENCE_AUDIT_SCHEMA = "coach_evidence_audit.v1"
_OUTCOME_SERIES_MAX = 8
_METRIC_KEYS_MAX = 8
_LIST_MAX = 20
_SIGNAL_CHANNEL_MAX = 4
_SIGNAL_POINTS_PER_CHANNEL = 600
_FACT_SECTION_MAX = 8
_FORBIDDEN_EVIDENCE_PARAMETER_KEYS = frozenset({
    "start_ms", "end_ms", "time_ms", "frame", "frame_index", "artifact_ref",
    "path", "sql", "python", "code", "query",
})


def _canonical_wire_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _evidence_result(command_id: str, *, result_ref: str, result: dict[str, Any]) -> dict[str, Any]:
    return _result(
        command_id,
        "succeeded",
        result_ref=result_ref,
        result={"schema_version": _EVIDENCE_QUERY_RESULT_SCHEMA, **result},
    )


def _stable_ref_list(value: object, *, field_name: str, max_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", f"{field_name} must be a bounded list")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 240:
            raise ProductCommandError("invalid_parameters", f"{field_name} contains an invalid ref")
        values.append(item)
    return values


def _string_list(value: object, *, field_name: str, max_items: int, allow_empty: bool = True) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", f"{field_name} must be a bounded list")
    if not all(isinstance(item, str) and item.strip() and len(item) <= 160 for item in value):
        raise ProductCommandError("invalid_parameters", f"{field_name} contains an invalid key")
    return [item.strip() for item in value]


def _require_reachable(bridge: _ToolBridge, ref: str) -> None:
    if ref not in bridge.reachable_refs:
        raise ProductCommandError("unreachable_ref", "reference was not reached in this Coach turn")


def _analysis_ref_from_segment(ref: str) -> str:
    if ":segment:" not in ref:
        raise ProductCommandError("invalid_reference", "segment_ref is invalid")
    analysis_ref = ref.split(":segment:", 1)[0]
    _parse_ref(analysis_ref, "analysis")
    return analysis_ref


async def _load_evidence_for_bridge(
    bridge: _ToolBridge,
    analysis_ref: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    _require_reachable(bridge, analysis_ref)
    analysis_id, _ = _parse_ref(analysis_ref, "analysis")
    session = await queue.get_session(analysis_id)
    if session is None or session.get("user_id") != bridge.owner_id:
        raise ProductCommandError("forbidden", "evidence is unavailable", kind="unavailable")
    if session.get("status") != "done":
        raise ProductCommandError("analysis_not_ready", "Analysis 尚未完成", kind="unavailable")
    raw_result = session.get("result")
    if not isinstance(raw_result, dict):
        raise ProductCommandError("analysis_result_missing", "Analysis 结果不可用", kind="unavailable")
    safe_ref = ((raw_result.get("evidence") or {}).get("derived_artifact"))
    if not isinstance(safe_ref, dict):
        raise ProductCommandError("evidence_unavailable", "Analysis 没有可查询的 evidence", kind="unavailable")
    artifact = await evidence_store.read_analysis_evidence_artifact(
        owner_id=bridge.owner_id,
        analysis_ref=analysis_ref,
        artifact_ref=safe_ref.get("artifact_ref"),
        evidence_revision=safe_ref.get("evidence_revision"),
    )
    return artifact, safe_ref, analysis_ref


def _segment(artifact: dict[str, Any], segment_ref: str) -> dict[str, Any]:
    for item in artifact.get("evidence_segments", []):
        if item.get("segment_id") == segment_ref:
            return item
    raise ProductCommandError("not_found", "EvidenceSegment 不存在", kind="unavailable")


def _safe_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return project_evidence_segment(segment)


def _safe_metric(metric: dict[str, Any]) -> dict[str, Any]:
    distribution = metric.get("distribution")
    if isinstance(distribution, dict):
        distribution = {
            key: value
            for key, value in distribution.items()
            if key != "histogram_bins" or isinstance(value, list) and len(value) <= 16
        }
    return {
        key: value
        for key, value in {
            "metric_key": metric.get("metric_key"),
            "metric_version": metric.get("metric_version"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "classification": metric.get("classification"),
            "provenance": metric.get("provenance"),
            "population": metric.get("population"),
            "distribution": distribution,
            "condition_refs": metric.get("condition_refs", []),
            "event_refs": metric.get("event_refs", []),
            "evidence_segment_refs": metric.get("evidence_segment_refs", []),
            "coverage": metric.get("coverage"),
            "confidence": metric.get("confidence"),
            "limitations": metric.get("limitations", []),
        }.items()
        if value is not None
    }


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in ("event_id", "event_kind", "start_ms", "end_ms", "actor_refs", "source_refs", "confidence", "attributes", "limitations")
        if key in event
    }


def _analysis_ref_from_table(ref: str) -> str:
    if ":table:" not in ref:
        raise ProductCommandError("invalid_reference", "table_ref is invalid")
    analysis_ref = ref.split(":table:", 1)[0]
    if not _REF_RE.fullmatch(analysis_ref) or not analysis_ref.startswith("analysis:"):
        raise ProductCommandError("invalid_reference", "table_ref is invalid")
    return analysis_ref


def _analysis_ref_from_event(ref: str) -> str:
    if ":event:" not in ref:
        raise ProductCommandError("invalid_reference", "event_ref is invalid")
    analysis_ref = ref.split(":event:", 1)[0]
    if not _REF_RE.fullmatch(analysis_ref) or not analysis_ref.startswith("analysis:"):
        raise ProductCommandError("invalid_reference", "event_ref is invalid")
    return analysis_ref


def _processed_table_events(
    artifact: dict[str, Any],
    table_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table = next(
        (
            item for item in build_processed_event_table_catalog(artifact)
            if item["table_ref"] == table_ref
        ),
        None,
    )
    if table is None:
        raise ProductCommandError(
            "table_not_found", "ProcessedEventTable 不存在", kind="unavailable",
        )
    events = [
        event
        for bundle in artifact.get("event_bundles", [])
        for event in bundle.get("events", [])
        if event.get("event_kind") == table["event_kind"]
    ]
    events.sort(
        key=lambda item: (
            item.get("start_ms", 0), item.get("end_ms", 0), item.get("event_id", ""),
        )
    )
    if len(events) != table["row_count"]:
        raise ProductCommandError(
            "table_incomplete", "ProcessedEventTable row count is inconsistent", kind="unavailable",
        )
    return table, events


async def _load_processed_table_for_bridge(
    bridge: "_ToolBridge",
    table_ref: object,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(table_ref, str):
        raise ProductCommandError("invalid_reference", "table_ref is required")
    _require_reachable(bridge, table_ref)
    analysis_ref = _analysis_ref_from_table(table_ref)
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    table, events = _processed_table_events(artifact, table_ref)
    return artifact, table, events


_EVENT_VALUE_MISSING = object()


def _event_field_value(event: Mapping[str, Any], field: str) -> object:
    if field in {"event_id", "start_ms", "end_ms", "confidence", "limitations"}:
        return event.get(field, _EVENT_VALUE_MISSING)
    attributes = event.get("attributes")
    if isinstance(attributes, Mapping):
        return attributes.get(field, _EVENT_VALUE_MISSING)
    return _EVENT_VALUE_MISSING


def _table_field(table: Mapping[str, Any], field: object) -> dict[str, Any]:
    if not isinstance(field, str):
        raise ProductCommandError("invalid_parameters", "processed event field is required")
    for definition in table.get("field_catalog", []):
        if definition.get("field_key") == field:
            return definition
    raise ProductCommandError("invalid_parameters", "processed event field is not registered")


def _predicate(value: object, table: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductCommandError("invalid_parameters", "event predicate must be an object")
    operator = value.get("operator")
    if operator not in {"eq", "lt", "lte", "gt", "gte", "between", "available", "unavailable"}:
        raise ProductCommandError("invalid_parameters", "event predicate operator is invalid")
    expected = {"field", "operator"} if operator in {"available", "unavailable"} else {"field", "operator", "value"}
    if set(value) != expected:
        raise ProductCommandError("invalid_parameters", "event predicate fields are invalid")
    field = value.get("field")
    definition = _table_field(table, field)
    if operator in {"lt", "lte", "gt", "gte", "between"} and definition["value_type"] != "number":
        raise ProductCommandError("invalid_parameters", "ordered predicate requires a numeric field")
    operand = value.get("value")
    if operator == "between":
        if (
            not isinstance(operand, list) or len(operand) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in operand)
        ):
            raise ProductCommandError("invalid_parameters", "between requires two finite numbers")
        if float(operand[0]) > float(operand[1]):
            raise ProductCommandError("invalid_parameters", "between bounds are reversed")
    elif operator in {"lt", "lte", "gt", "gte"}:
        if isinstance(operand, bool) or not isinstance(operand, (int, float)) or not math.isfinite(float(operand)):
            raise ProductCommandError("invalid_parameters", "ordered predicate requires a finite number")
    elif operator == "eq":
        if not isinstance(operand, (str, int, float, bool)):
            raise ProductCommandError("invalid_parameters", "equality predicate value is invalid")
        if isinstance(operand, float) and not math.isfinite(operand):
            raise ProductCommandError("invalid_parameters", "equality predicate number must be finite")
    return dict(value)


def _predicates(value: object, table: Mapping[str, Any], *, max_items: int = 4) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ProductCommandError("invalid_parameters", "event predicates must be a bounded list")
    return [_predicate(item, table) for item in value]


def _predicate_matches(event: Mapping[str, Any], predicate: Mapping[str, Any]) -> bool:
    current = _event_field_value(event, str(predicate["field"]))
    operator = predicate["operator"]
    if operator == "available":
        return current is not _EVENT_VALUE_MISSING and current is not None
    if operator == "unavailable":
        return current is _EVENT_VALUE_MISSING or current is None
    if current is _EVENT_VALUE_MISSING or current is None:
        return False
    operand = predicate.get("value")
    if operator == "eq":
        return current == operand
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
        return False
    number = float(current)
    if operator == "lt":
        return number < float(operand)
    if operator == "lte":
        return number <= float(operand)
    if operator == "gt":
        return number > float(operand)
    if operator == "gte":
        return number >= float(operand)
    return float(operand[0]) <= number <= float(operand[1])


def _matching_events(
    events: list[dict[str, Any]],
    predicates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event for event in events
        if all(_predicate_matches(event, predicate) for predicate in predicates)
    ]


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("nearest rank requires values")
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _event_distribution(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        float(value)
        for event in events
        for value in [_event_field_value(event, field)]
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not values:
        return {
            "count": len(events), "valid_count": 0, "excluded_count": len(events),
            "availability": "unavailable",
        }
    return {
        "count": len(events),
        "valid_count": len(values),
        "excluded_count": len(events) - len(values),
        "availability": "available",
        "min": min(values),
        "p10": _nearest_rank(values, 0.10),
        "p25": _nearest_rank(values, 0.25),
        "median": _nearest_rank(values, 0.50),
        "p75": _nearest_rank(values, 0.75),
        "p90": _nearest_rank(values, 0.90),
        "max": max(values),
        "mean": math.fsum(values) / len(values),
    }


def _table_metric_field(table: Mapping[str, Any], metric_key: str) -> dict[str, Any]:
    matches = [
        field for field in table.get("field_catalog", [])
        if field.get("metric_key") == metric_key
    ]
    if len(matches) != 1:
        raise ProductCommandError(
            "not_comparable",
            "requested metric does not map to one processed event field",
            kind="unavailable",
        )
    return matches[0]


def _processed_metric_record(
    table: Mapping[str, Any],
    events: list[dict[str, Any]],
    metric_key: str,
    *,
    evidence_ref: str,
) -> dict[str, Any]:
    definition = _table_metric_field(table, metric_key)
    field = definition["field_key"]
    values = [
        float(value)
        for event in events
        for value in [_event_field_value(event, field)]
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not values:
        raise ProductCommandError(
            "not_comparable", "processed event metric is unavailable", kind="unavailable",
        )
    value = values[0] if len(values) == 1 else _nearest_rank(values, 0.5)
    event_refs = [event["event_id"] for event in events]
    return {
        "schema_version": "metric_record.v1",
        "metric_key": metric_key,
        "metric_version": definition["metric_version"],
        "value": value,
        "unit": definition["unit"],
        "availability": "available",
        "classification": "deterministic",
        "provenance": {
            "kind": "derived",
            "source_refs": sorted({
                ref for event in events for ref in event.get("source_refs", [])
            }),
        },
        "population": {
            "sample_count": len(events),
            "valid_count": len(values),
            "excluded_count": len(events) - len(values),
        },
        "condition_refs": [],
        "event_refs": event_refs,
        "evidence_segment_refs": [evidence_ref] if ":segment:" in evidence_ref else [],
        "coverage": min(float(event.get("confidence") or 0.0) for event in events),
        "confidence": min(float(event.get("confidence") or 0.0) for event in events),
        "limitations": [
            *definition.get("limitations", []),
            *([] if len(values) == 1 else ["segment_value_is_median_of_processed_rows"]),
        ],
    }


def _facts_section_summaries(analysis_ref: str, facts: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for section in facts.get("sections", []):
        section_key = section.get("section_key")
        if not isinstance(section_key, str):
            continue
        summaries.append({
            "section_key": section_key,
            "section_ref": f"{analysis_ref}:facts:{section_key}",
            "completeness": section.get("completeness"),
            "present_field_count": len(section.get("present_field_keys", [])),
            "source_absent_field_count": len(section.get("source_absent_field_keys", [])),
            "omitted_known_field_count": len(section.get("omitted_known_fields", [])),
        })
    return summaries


def _new_cursor(bridge: _ToolBridge, *, command_name: str, state: dict[str, Any]) -> str:
    cursor = f"cursor:{secrets.token_urlsafe(24)}"
    bridge.cursors[cursor] = {"command_name": command_name, **state}
    return cursor


def _cursor_state(bridge: _ToolBridge, cursor: object, command_name: str) -> dict[str, Any]:
    if not isinstance(cursor, str):
        raise ProductCommandError("cursor_not_valid", "cursor is invalid")
    state = bridge.cursors.get(cursor)
    if state is None or state.get("command_name") != command_name:
        raise ProductCommandError("cursor_not_valid", "cursor is invalid")
    return dict(state)


def _audit_evidence_result(
    result: dict[str, Any],
    *,
    command_name: str,
    parameters: dict[str, Any],
    bridge: _ToolBridge,
    response_bytes: int,
    signal_points: int,
) -> dict[str, Any]:
    refs = sorted(ref for ref in _stable_reachable_refs(result) if not ref.startswith("cursor:"))
    requested_event_fields: list[str] = []
    for value in [parameters.get("field"), *(parameters.get("fields") or [])]:
        if isinstance(value, str) and value not in requested_event_fields:
            requested_event_fields.append(value)
    for predicate in [
        *(parameters.get("predicates") or []),
        parameters.get("left"),
        parameters.get("right"),
    ]:
        if isinstance(predicate, Mapping):
            field = predicate.get("field")
            if isinstance(field, str) and field not in requested_event_fields:
                requested_event_fields.append(field)
    audit_payload = {
        "schema_version": _EVIDENCE_AUDIT_SCHEMA,
        "command_name": command_name,
        "analysis_refs": sorted(
            ref for ref in refs
            if ref.startswith("analysis:")
            and not any(marker in ref for marker in (":table:", ":event:", ":segment:", ":facts:"))
        ),
        "table_refs": sorted(ref for ref in refs if ":table:" in ref),
        "event_refs": sorted(ref for ref in refs if ":event:" in ref),
        "segment_refs": sorted(ref for ref in refs if ":segment:" in ref),
        "requested_metric_keys": parameters.get("metric_keys", []),
        "requested_channel_keys": parameters.get("channel_keys", []),
        "requested_event_kinds": parameters.get("event_kinds", []),
        "requested_segment_kinds": parameters.get("segment_kinds", []),
        "requested_issue_refs": parameters.get("issue_refs", []),
        "requested_event_fields": requested_event_fields,
        "query_digest": _idempotency_digest(command_name, {
            key: value for key, value in parameters.items() if key != "cursor"
        }),
        "budget_used": {
            "response_bytes": response_bytes,
            "signal_points": signal_points,
        },
        "budget_remaining": {
            "response_bytes": coach_commands._MAX_BRIDGE_BYTES - bridge.bytes_used - response_bytes,
            "signal_points": _MAX_SIGNAL_POINTS - bridge.signal_points_used - signal_points,
        },
        "status": result.get("status"),
    }
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": result["command_id"],
        "status": result["status"],
        "audit_ref": result["audit_ref"],
        "result_ref": result.get("result_ref"),
        "result": audit_payload,
    }


def _result_cursors(value: object) -> set[str]:
    cursors: set[str] = set()
    if isinstance(value, str) and value.startswith("cursor:"):
        cursors.add(value)
    elif isinstance(value, Mapping):
        for child in value.values():
            cursors.update(_result_cursors(child))
    elif isinstance(value, list):
        for child in value:
            cursors.update(_result_cursors(child))
    return cursors


def _discard_result_cursors(bridge: _ToolBridge, result: dict[str, Any]) -> None:
    for cursor in _result_cursors(result):
        bridge.cursors.pop(cursor, None)


async def _finish_evidence_result(
    bridge: _ToolBridge,
    result: dict[str, Any],
    *,
    command_name: str,
    parameters: dict[str, Any],
    signal_points: int = 0,
) -> dict[str, Any]:
    if result.get("status") != "succeeded":
        return result
    size = _canonical_wire_size(result)
    if size > coach_commands._MAX_SINGLE_RESULT_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("response_too_large", "evidence response exceeds the per-response budget"))
    is_signal = command_name == "analysis.evidence.signal_window"
    async with _tool_bridge_lock:
        current = _tool_bridges.get(bridge.token_digest)
        if current is not bridge or bridge.expires_at <= time.time():
            _tool_bridges.pop(bridge.token_digest, None)
            bridge.cursors.clear()
            return _result(result["command_id"], "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    if bridge.bytes_used + size > coach_commands._MAX_BRIDGE_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("budget_exhausted", "Coach evidence byte budget is exhausted"))
    if bridge.signal_points_used + signal_points > _MAX_SIGNAL_POINTS:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_point_budget_exhausted", "Coach signal point budget is exhausted"))
    if is_signal and bridge.signal_bytes_used + size > _MAX_SIGNAL_BYTES:
        _discard_result_cursors(bridge, result)
        return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted"))
    audit = _audit_evidence_result(
        result,
        command_name=command_name,
        parameters=parameters,
        bridge=bridge,
        response_bytes=size,
        signal_points=signal_points,
    )
    token = _audit_context.set({
        "thread_id": bridge.thread_id,
        "user_message_ref": bridge.user_message_ref,
        "command_name": command_name,
        "risk": "query",
        "authorization_source": "coach_inferred",
        "idempotency_key": None,
        "parameters_digest": audit["result"]["query_digest"],
        "safe_parameters_summary": _safe_parameter_summary(parameters),
    })
    try:
        try:
            await _journal().audit(bridge.owner_id, audit)
        except Exception:
            _discard_result_cursors(bridge, result)
            return _result(result["command_id"], "unavailable", warning_or_error=_error("audit_unavailable", "evidence audit is unavailable"))
        async with _tool_bridge_lock:
            current = _tool_bridges.get(bridge.token_digest)
            if current is not bridge or bridge.expires_at <= time.time():
                _tool_bridges.pop(bridge.token_digest, None)
                bridge.cursors.clear()
                return _result(result["command_id"], "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
            if bridge.bytes_used + size > coach_commands._MAX_BRIDGE_BYTES:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("budget_exhausted", "Coach evidence byte budget is exhausted"))
            if bridge.signal_points_used + signal_points > _MAX_SIGNAL_POINTS:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_point_budget_exhausted", "Coach signal point budget is exhausted"))
            if is_signal and bridge.signal_bytes_used + size > _MAX_SIGNAL_BYTES:
                _discard_result_cursors(bridge, result)
                return _result(result["command_id"], "unavailable", warning_or_error=_error("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted"))
            bridge.bytes_used += size
            bridge.signal_points_used += signal_points
            if is_signal:
                bridge.signal_bytes_used += size
            bridge.reachable_refs.update(_stable_reachable_refs(result))
        return result
    finally:
        _audit_context.reset(token)


async def _execute_evidence_bridge(bridge: _ToolBridge, envelope: Mapping[str, Any]) -> dict[str, Any]:
    command_id = _command_id(envelope.get("command_id"))
    command_name = envelope.get("command_name")
    if not isinstance(command_name, str) or command_name not in _EVIDENCE_QUERY_COMMANDS:
        return _result(command_id, "failed", warning_or_error=_error("unsupported_command", "command is not allowed"))
    try:
        parameters = _require_mapping(envelope.get("parameters", {}))
        if _contains_forbidden_model_data(parameters) or set(parameters) & _FORBIDDEN_EVIDENCE_PARAMETER_KEYS:
            raise ProductCommandError("untrusted_field", "paths, URLs, credentials and raw traces are not accepted")
        if command_name == "analysis.metrics.distribution":
            result, points = await _query_metric_distribution(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.list":
            result, points = await _query_evidence_list(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.signal_window":
            result, points = await _query_signal_window(bridge, command_id, parameters)
        elif command_name == "analysis.evidence.compare":
            result, points = await _query_evidence_compare(bridge, command_id, parameters)
        elif command_name == "analysis.run_facts.get":
            result, points = await _query_run_facts(bridge, command_id, parameters)
        elif command_name == "analysis.outcomes.timeline":
            result, points = await _query_outcomes_timeline(bridge, command_id, parameters)
        elif command_name == "analysis.events.list":
            result, points = await _query_events(bridge, command_id, parameters)
        elif command_name == "analysis.events.get":
            result, points = await _query_event_get(bridge, command_id, parameters)
        elif command_name == "analysis.events.rank":
            result, points = await _query_event_rank(bridge, command_id, parameters)
        elif command_name == "analysis.events.filter":
            result, points = await _query_event_filter(bridge, command_id, parameters)
        elif command_name == "analysis.events.aggregate":
            result, points = await _query_event_aggregate(bridge, command_id, parameters)
        elif command_name == "analysis.events.co_occurrence":
            result, points = await _query_event_co_occurrence(bridge, command_id, parameters)
        else:
            result, points = await _query_event_sequence(bridge, command_id, parameters)
        return await _finish_evidence_result(
            bridge, result, command_name=command_name, parameters=parameters, signal_points=points,
        )
    except ProductCommandError as exc:
        status = "unavailable" if exc.kind == "unavailable" else "failed"
        return _result(command_id, status, result_ref=exc.result_ref, warning_or_error=_error(exc.code, exc.message))
    except (ValueError, TypeError, KeyError):
        return _result(command_id, "failed", warning_or_error=_error("invalid_parameters", "evidence query parameters are invalid"))
    except Exception:
        return _result(command_id, "unavailable", warning_or_error=_error("evidence_unavailable", "evidence query could not be completed"))


async def _query_metric_distribution(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "metric_keys"})
    analysis_ref = parameters.get("analysis_ref")
    if not isinstance(analysis_ref, str):
        raise ProductCommandError("invalid_reference", "analysis_ref is required")
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    keys = _string_list(parameters.get("metric_keys"), field_name="metric_keys", max_items=_METRIC_KEYS_MAX, allow_empty=False)
    available_keys = {metric.get("metric_key") for metric in artifact.get("metric_records", [])}
    if not set(keys) <= available_keys:
        raise ProductCommandError("invalid_parameters", "metric is not available in this analysis")
    metrics = [_safe_metric(metric) for metric in artifact.get("metric_records", []) if metric.get("metric_key") in keys]
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:metrics", result={"analysis_ref": analysis_ref, "metrics": metrics}), 0


async def _query_evidence_list(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "segment_kinds", "issue_refs", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.evidence.list")
        analysis_ref = state["analysis_ref"]
        offset = int(state["offset"])
        limit = int(state["limit"])
        segment_kinds = list(state["segment_kinds"])
        issue_refs = list(state["issue_refs"])
    else:
        analysis_ref = parameters.get("analysis_ref")
        if not isinstance(analysis_ref, str):
            raise ProductCommandError("invalid_reference", "analysis_ref is required")
        offset = 0
        limit = parameters.get("limit", _LIST_MAX)
        segment_kinds = _string_list(
            parameters.get("segment_kinds"),
            field_name="segment_kinds",
            max_items=8,
        )
        if not all(EvidenceKeyRegistry().allows_segment(kind) for kind in segment_kinds):
            raise ProductCommandError("invalid_parameters", "segment kind is not registered")
        issue_refs = (
            _stable_ref_list(parameters.get("issue_refs"), field_name="issue_refs", max_items=8)
            if "issue_refs" in parameters
            else []
        )
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    segments = [
        segment
        for segment in artifact.get("evidence_segments", [])
        if (not segment_kinds or segment.get("segment_kind") in segment_kinds)
        and (not issue_refs or set(segment.get("issue_refs", [])) & set(issue_refs))
    ]
    selected = segments[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(segments):
        next_cursor = _new_cursor(bridge, command_name="analysis.evidence.list", state={
            "analysis_ref": analysis_ref,
            "offset": offset + len(selected),
            "limit": limit,
            "segment_kinds": list(segment_kinds),
            "issue_refs": list(issue_refs),
        })
    result = _evidence_result(command_id, result_ref=f"{analysis_ref}:evidence:list:{offset}", result={
        "analysis_ref": analysis_ref,
        "segments": [_safe_segment(segment) for segment in selected],
        "next_cursor": next_cursor,
    })
    return result, 0


def _downsample_points(points: list[list[float]], limit: int) -> list[list[float]]:
    if len(points) <= limit:
        return [list(point) for point in points]
    if limit <= 0:
        return []
    last_index = len(points) - 1
    global_min = min(range(len(points)), key=lambda index: (points[index][1], index))
    global_max = max(range(len(points)), key=lambda index: (points[index][1], -index))
    mandatory = {0, last_index, global_min, global_max}
    if limit < len(mandatory):
        raise ValueError("point limit cannot preserve endpoints and extrema")
    if limit <= 2:
        return [list(points[0]), list(points[-1])][:limit]
    if limit == 3:
        midpoint = (float(points[0][1]) + float(points[-1][1])) / 2.0
        candidates = [index for index in {global_min, global_max} if index not in {0, last_index}]
        middle = max(
            candidates or [last_index // 2],
            key=lambda index: (abs(float(points[index][1]) - midpoint), -index),
        )
        return [list(points[index]) for index in sorted({0, middle, last_index})]
    selected = set(mandatory)
    interior_count = max(1, (limit - 2) // 2)
    for bucket in range(interior_count):
        start = 1 + (bucket * (len(points) - 2)) // interior_count
        end = 1 + ((bucket + 1) * (len(points) - 2)) // interior_count
        if start >= end:
            continue
        bucket_indices = range(start, end)
        selected.add(min(bucket_indices, key=lambda index: (points[index][1], index)))
        selected.add(max(bucket_indices, key=lambda index: (points[index][1], -index)))

    if len(selected) > limit:
        optional = sorted(selected - mandatory)
        remaining = max(0, limit - len(mandatory))
        if remaining < len(optional):
            optional = [
                optional[round(index * (len(optional) - 1) / max(1, remaining - 1))]
                for index in range(remaining)
            ] if remaining else []
        selected = mandatory | set(optional)
    if len(selected) < limit:
        available = [index for index in range(1, last_index) if index not in selected]
        remaining = min(limit - len(selected), len(available))
        if remaining:
            selected.update(
                available[round(index * (len(available) - 1) / max(1, remaining - 1))]
                for index in range(remaining)
            )
    return [list(points[index]) for index in sorted(selected)[:limit]]


async def _query_signal_window(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"segment_ref", "channel_keys"})
    segment_ref = parameters.get("segment_ref")
    if not isinstance(segment_ref, str):
        raise ProductCommandError("invalid_reference", "segment_ref is required")
    _require_reachable(bridge, segment_ref)
    analysis_ref = _analysis_ref_from_segment(segment_ref)
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    segment = _segment(artifact, segment_ref)
    focus_start = segment.get("focus_start_ms")
    focus_end = segment.get("focus_end_ms")
    if (
        isinstance(focus_start, bool)
        or not isinstance(focus_start, int)
        or isinstance(focus_end, bool)
        or not isinstance(focus_end, int)
        or focus_start >= focus_end
        or focus_end - focus_start > 12_000
    ):
        raise ProductCommandError("signal_window_unavailable", "segment focus window exceeds the 12 second limit", kind="unavailable")
    channel_keys = _string_list(parameters.get("channel_keys"), field_name="channel_keys", max_items=_SIGNAL_CHANNEL_MAX, allow_empty=False)
    allowed_channels = set(segment.get("available_channels", []))
    if not set(channel_keys) <= allowed_channels:
        raise ProductCommandError("invalid_parameters", "channel is not available in this segment")
    samples_by_channel = {
        sample.get("channel_key"): sample
        for sample in artifact.get("sample_sets", [])
        if sample.get("channel_key") in channel_keys
    }
    channel_metadata = {
        channel.get("channel_key"): channel
        for bundle in artifact.get("signal_bundles", [])
        for channel in bundle.get("channels", [])
        if channel.get("channel_key") in channel_keys
    }
    source_points: list[tuple[str, dict[str, Any], dict[str, Any], list[list[float]]]] = []
    for channel_key in channel_keys:
        sample = samples_by_channel.get(channel_key)
        metadata = channel_metadata.get(channel_key)
        if sample is None or metadata is None:
            raise ProductCommandError("evidence_unavailable", "signal channel samples are unavailable", kind="unavailable")
        points = [
            point for point in sample.get("points", [])
            if focus_start <= point[0] < focus_end
        ]
        if not points:
            raise ProductCommandError("evidence_unavailable", "signal channel has no samples in the segment focus window", kind="unavailable")
        source_points.append((channel_key, sample, metadata, points))

    remaining_points = _MAX_SIGNAL_POINTS - bridge.signal_points_used
    available_bytes = min(
        coach_commands._MAX_SINGLE_RESULT_BYTES,
        coach_commands._MAX_BRIDGE_BYTES - bridge.bytes_used,
        _MAX_SIGNAL_BYTES - bridge.signal_bytes_used,
    )
    minimum_per_channel = max(
        len({
            0,
            len(points) - 1,
            min(range(len(points)), key=lambda index: (points[index][1], index)),
            max(range(len(points)), key=lambda index: (points[index][1], -index)),
        })
        for _, _, _, points in source_points
    )
    if remaining_points < minimum_per_channel * len(source_points):
        raise ProductCommandError("signal_point_budget_exhausted", "Coach signal point budget is exhausted", kind="unavailable")
    if available_bytes <= 0:
        raise ProductCommandError("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted", kind="unavailable")
    max_per_channel = min(
        _SIGNAL_POINTS_PER_CHANNEL,
        max(1, remaining_points // len(source_points)),
    )

    def build_result(point_limit: int) -> tuple[dict[str, Any], int]:
        channels: list[dict[str, Any]] = []
        point_count = 0
        truncated = False
        for channel_key, sample, metadata, points in source_points:
            sampled = _downsample_points(points, point_limit)
            point_count += len(sampled)
            truncated = truncated or len(sampled) < len(points)
            channels.append({
                "channel_key": channel_key,
                "unit": sample.get("unit"),
                "points": sampled,
                "source_coverage": metadata.get("coverage"),
                "confidence": metadata.get("confidence_summary"),
            })
        body = {
            "schema_version": "signal_window.v1",
            "analysis_ref": analysis_ref,
            "segment_ref": segment_ref,
            "focus_range_ms": [focus_start, focus_end],
            "channels": channels,
            "downsample_version": "deterministic_extrema.v1",
            "point_count": point_count,
            "truncated": truncated,
            "budget_used": {"response_bytes": 0, "signal_points": point_count},
            "budget_remaining": {
                "response_bytes": 0,
                "signal_response_bytes": 0,
                "signal_points": remaining_points - point_count,
            },
            "limitations": ["deterministic_extrema_downsampled"] if truncated else [],
        }
        result = _evidence_result(
            command_id,
            result_ref=f"{segment_ref}:signal-window",
            result=body,
        )
        for _ in range(4):
            response_bytes = _canonical_wire_size(result)
            body["budget_used"]["response_bytes"] = response_bytes
            body["budget_remaining"]["response_bytes"] = max(
                0, coach_commands._MAX_BRIDGE_BYTES - bridge.bytes_used - response_bytes,
            )
            body["budget_remaining"]["signal_response_bytes"] = max(
                0, _MAX_SIGNAL_BYTES - bridge.signal_bytes_used - response_bytes,
            )
        return result, point_count

    best: tuple[dict[str, Any], int] | None = None
    low, high = minimum_per_channel, max_per_channel
    while low <= high:
        middle = (low + high) // 2
        candidate = build_result(middle)
        if _canonical_wire_size(candidate[0]) <= available_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ProductCommandError("signal_byte_budget_exhausted", "Coach signal byte budget is exhausted", kind="unavailable")
    return best


async def _query_evidence_compare(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"evidence_refs", "metric_keys"})
    refs = _stable_ref_list(parameters.get("evidence_refs"), field_name="evidence_refs", max_items=4)
    if not 2 <= len(refs) <= 4:
        raise ProductCommandError("invalid_parameters", "evidence_refs must contain 2 to 4 refs")
    keys = _string_list(parameters.get("metric_keys"), field_name="metric_keys", max_items=_METRIC_KEYS_MAX, allow_empty=False)
    rows: list[dict[str, Any]] = []
    comparison_scope: str | None = None
    comparison_contracts: list[dict[str, Any]] = []
    comparison_limitations: list[str] = []
    for ref in refs:
        _require_reachable(bridge, ref)
        segment = None
        event = None
        table = None
        if ":segment:" in ref:
            analysis_ref = _analysis_ref_from_segment(ref)
            scope = "segment"
        elif ":event:" in ref:
            analysis_ref = _analysis_ref_from_event(ref)
            scope = "event"
        elif _REF_RE.fullmatch(ref) and ref.startswith("analysis:"):
            analysis_ref = ref
            scope = "analysis"
        else:
            raise ProductCommandError("invalid_reference", "comparison refs must be analysis, segment or processed event refs")
        if comparison_scope is None:
            comparison_scope = scope
        elif comparison_scope != scope:
            raise ProductCommandError("not_comparable", "analysis and segment evidence cannot be mixed", kind="unavailable")
        artifact, artifact_ref, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
        if scope == "segment":
            segment = _segment(artifact, ref)
        facts = artifact.get("canonical_run_facts") or {}
        available_keys = {metric.get("metric_key") for metric in artifact.get("metric_records", [])}
        if not set(keys) <= available_keys:
            raise ProductCommandError("invalid_parameters", "metric is not available for comparison")
        if scope == "analysis":
            predicate_version = "analysis_metric_comparability.v1"
            metrics = [
                _safe_metric(metric)
                for metric in artifact.get("metric_records", [])
                if metric.get("metric_key") in keys
            ]
        else:
            processed_tables = build_processed_event_table_catalog(artifact)
            if scope == "segment" and not processed_tables:
                predicate_version = "legacy_segment_metric_comparability.v1"
                metrics = [
                    _safe_metric(metric)
                    for metric in artifact.get("metric_records", [])
                    if metric.get("metric_key") in keys
                    and ref in metric.get("evidence_segment_refs", [])
                ]
                comparison_limitations.append(
                    "legacy_segment_compare_uses_linked_metric_record"
                )
            elif scope == "event":
                predicate_version = "processed_event_metric_comparability.v1"
                matches = []
                for candidate in processed_tables:
                    _, candidate_events = _processed_table_events(
                        artifact, candidate["table_ref"],
                    )
                    candidate_event = next(
                        (item for item in candidate_events if item.get("event_id") == ref),
                        None,
                    )
                    if candidate_event is not None:
                        matches.append((candidate, [candidate_event]))
                if len(matches) != 1:
                    raise ProductCommandError(
                        "not_comparable",
                        "event ref is not a unique processed event",
                        kind="unavailable",
                    )
                table, selected_events = matches[0]
                event = selected_events[0]
            else:
                predicate_version = "processed_event_metric_comparability.v1"
                matches = []
                for candidate in processed_tables:
                    if not all(
                        any(field.get("metric_key") == key for field in candidate["field_catalog"])
                        for key in keys
                    ):
                        continue
                    _, candidate_events = _processed_table_events(
                        artifact, candidate["table_ref"],
                    )
                    selected_events = [
                        item for item in candidate_events
                        if segment["start_ms"] <= item.get("start_ms", -1) < segment["end_ms"]
                    ]
                    if selected_events:
                        matches.append((candidate, selected_events))
                if len(matches) != 1:
                    raise ProductCommandError(
                        "not_comparable",
                        "segment does not resolve to one processed event table",
                        kind="unavailable",
                    )
                table, selected_events = matches[0]
            if not (scope == "segment" and not processed_tables):
                metrics = [
                    _safe_metric(
                        _processed_metric_record(
                            table, selected_events, key, evidence_ref=ref,
                        )
                    )
                    for key in keys
                ]
        if {metric.get("metric_key") for metric in metrics} != set(keys):
            raise ProductCommandError("not_comparable", "requested metrics are not linked to every comparison ref", kind="unavailable")
        raw_metrics = {
            metric.get("metric_key"): metric for metric in metrics
        }
        comparison_contracts.append({
            "predicate_version": predicate_version,
            "artifact_contract_version": artifact_ref.get("contract_version"),
            "scenario_profile_ref": facts.get("scenario_profile_ref"),
            "timebase_version": (artifact.get("canonical_time_window") or {}).get("timebase_version"),
            "analyzer_ref": (
                segment.get("analyzer_ref")
                if segment is not None
                else table.get("analyzer_ref") if table is not None else None
            ),
            "metrics": {
                key: {
                    "metric_version": raw_metrics[key].get("metric_version"),
                    "unit": raw_metrics[key].get("unit"),
                    "classification": raw_metrics[key].get("classification"),
                    "provenance_kind": (raw_metrics[key].get("provenance") or {}).get("kind"),
                    "condition_refs": sorted(raw_metrics[key].get("condition_refs", [])),
                }
                for key in keys
            },
        })
        rows.append({
            "evidence_ref": ref,
            "scope": scope,
            "segment": _safe_segment(segment) if segment is not None else None,
            "event": _safe_event(event) if event is not None else None,
            "metrics": metrics,
        })
    if any(contract != comparison_contracts[0] for contract in comparison_contracts[1:]):
        raise ProductCommandError("not_comparable", "versioned comparison contracts do not match", kind="unavailable")
    baseline = {
        metric["metric_key"]: metric.get("value")
        for metric in rows[0]["metrics"]
    }
    for row in rows:
        row["deltas_from_first"] = {
            metric["metric_key"]: (
                float(metric["value"]) - float(baseline[metric["metric_key"]])
                if isinstance(metric.get("value"), (int, float))
                and not isinstance(metric.get("value"), bool)
                and isinstance(baseline.get(metric["metric_key"]), (int, float))
                and not isinstance(baseline.get(metric["metric_key"]), bool)
                else None
            )
            for metric in row["metrics"]
        }
    return _evidence_result(
        command_id,
        result_ref="evidence:compare:" + hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()[:24],
        result={
            "scope": comparison_scope,
            "comparability": "comparable",
            "comparability_predicate_version": comparison_contracts[0]["predicate_version"],
            "metric_keys": keys,
            "comparisons": rows,
            "limitations": list(dict.fromkeys(comparison_limitations)),
        },
    ), 0


async def _query_run_facts(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "sections"})
    analysis_ref = parameters.get("analysis_ref")
    if not isinstance(analysis_ref, str):
        raise ProductCommandError("invalid_reference", "analysis_ref is required")
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    facts = artifact.get("canonical_run_facts")
    if not isinstance(facts, dict):
        raise ProductCommandError("facts_unavailable", "CanonicalRunFacts 不可用", kind="unavailable")
    summaries = _facts_section_summaries(analysis_ref, facts)
    requested = parameters.get("sections", "all")
    if requested != "all":
        requested_keys = _string_list(requested, field_name="sections", max_items=_FACT_SECTION_MAX, allow_empty=False)
        selected = [section for section in facts.get("sections", []) if section.get("section_key") in requested_keys]
        if len(selected) != len(requested_keys):
            raise ProductCommandError("invalid_parameters", "unknown facts section")
        facts = {**facts, "sections": selected}
    if _canonical_wire_size(facts) <= 8 * 1024 and _canonical_wire_size(facts) <= coach_commands._MAX_SINGLE_RESULT_BYTES:
        run_facts = {"mode": "inline", "field_registry_version": facts.get("field_registry_version"), "facts": facts, "section_summaries": [], "limitations": facts.get("limitations", [])}
    else:
        run_facts = {"mode": "section_refs", "field_registry_version": facts.get("field_registry_version"), "section_summaries": summaries, "limitations": ["facts_over_inline_budget"]}
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:facts", result={"analysis_ref": analysis_ref, "run_facts": run_facts}), 0


def _overview_timeline(
    records: list[dict[str, Any]],
    *,
    analysis_ref: str,
    scope: str,
    segment_ref: str | None,
    series: list[str],
    segment_bounds: tuple[int, int] | None,
) -> dict[str, Any]:
    by_series: dict[str, list[dict[str, Any]]] = {key: [] for key in series}
    for record in records:
        time_ms = record.get("canonical_time_ms")
        if segment_bounds is not None and not (segment_bounds[0] <= time_ms < segment_bounds[1]):
            continue
        for value in record.get("values", []):
            metric_key = value.get("metric_key")
            if metric_key not in by_series:
                continue
            numeric = value.get("value")
            if isinstance(numeric, bool) or not isinstance(numeric, (int, float)):
                raise ProductCommandError("overview_unavailable", "overview only supports numeric outcome series", kind="unavailable")
            by_series[metric_key].append({
                "time_ms": time_ms,
                "value": float(numeric),
                "semantics": value.get("value_semantics"),
                "unit": value.get("unit"),
                "source_refs": list(record.get("source_refs", [])),
            })

    overview_series: list[dict[str, Any]] = []
    for metric_key in series:
        values = sorted(by_series[metric_key], key=lambda item: item["time_ms"])
        if not values:
            raise ProductCommandError("overview_unavailable", "requested outcome series has no records", kind="unavailable")
        bucket_count = min(120, len(values))
        points: list[list[float]] = []
        source_refs: set[str] = set()
        for bucket in range(bucket_count):
            start = (bucket * len(values)) // bucket_count
            end = ((bucket + 1) * len(values)) // bucket_count
            bucket_values = values[start:end]
            source_refs.update(
                ref
                for item in bucket_values
                for ref in item["source_refs"]
                if isinstance(ref, str)
            )
            semantics = bucket_values[0]["semantics"]
            if any(item["semantics"] != semantics for item in bucket_values):
                raise ProductCommandError("overview_unavailable", "outcome value semantics are inconsistent", kind="unavailable")
            if semantics in {"count_increment", "delta"}:
                bucket_value = sum(item["value"] for item in bucket_values)
            elif semantics == "instantaneous":
                bucket_value = bucket_values[-1]["value"]
            else:
                bucket_value = sum(item["value"] for item in bucket_values) / len(bucket_values)
            points.append([bucket_values[-1]["time_ms"], bucket_value])
        overview_series.append({
            "metric_key": metric_key,
            "unit": values[0]["unit"],
            "points": points,
            "source_refs": sorted(source_refs),
        })
    timeline = {
        "schema_version": "normalized_outcome_timeline.v1",
        "analysis_ref": analysis_ref,
        "scope": scope,
        "segment_ref": segment_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "mode": "overview",
        "resolution": "deterministic_binned",
        "selected_series": list(series),
        "overview_series": overview_series,
        "records": None,
        "event_refs": [],
        "completeness": "downsampled",
        "next_cursor": None,
        "limitations": ["deterministic_binned_overview"],
    }
    return validate_normalized_outcome_timeline_v1(timeline)


async def _query_outcomes_timeline(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "scope", "segment_ref", "mode", "series", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.outcomes.timeline")
        analysis_ref = state["analysis_ref"]
        scope = state["scope"]
        segment_ref = state["segment_ref"]
        series = state["series"]
        offset = state["offset"]
        mode = "exact_page"
    else:
        analysis_ref = parameters.get("analysis_ref")
        scope = parameters.get("scope")
        segment_ref = parameters.get("segment_ref")
        mode = parameters.get("mode")
        series = _string_list(parameters.get("series"), field_name="series", max_items=_OUTCOME_SERIES_MAX, allow_empty=False)
        offset = 0
        if not isinstance(analysis_ref, str) or scope not in {"whole_run", "evidence_segment"} or mode not in {"overview", "exact_page"}:
            raise ProductCommandError("invalid_parameters", "timeline requires a bounded scope and overview/exact_page mode")
        if scope == "evidence_segment":
            if not isinstance(segment_ref, str):
                raise ProductCommandError("invalid_reference", "segment_ref is required")
            _require_reachable(bridge, segment_ref)
        elif segment_ref is not None:
            raise ProductCommandError("invalid_parameters", "whole_run cannot include segment_ref")
    artifact, safe_ref, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    segment_bounds = None
    if scope == "evidence_segment":
        segment = _segment(artifact, segment_ref)
        segment_bounds = (segment["start_ms"], segment["end_ms"])
    if mode == "overview":
        timeline = _overview_timeline(
            artifact.get("normalized_outcome_records", []),
            analysis_ref=analysis_ref,
            scope=scope,
            segment_ref=segment_ref,
            series=series,
            segment_bounds=segment_bounds,
        )
        return _evidence_result(
            command_id,
            result_ref=f"{analysis_ref}:timeline:overview",
            result={"analysis_ref": analysis_ref, "timeline": timeline, "next_cursor": None},
        ), 0

    descriptor = build_page_descriptor_v1(
        owner_id=bridge.owner_id,
        analysis_ref=analysis_ref,
        evidence_revision=safe_ref["evidence_revision"],
        scope=scope,
        segment_ref=segment_ref,
        selected_series=series,
        offset=offset,
    )
    page = page_normalized_outcomes(
        artifact.get("normalized_outcome_records", []),
        analysis_ref=analysis_ref,
        canonical_time_window_ref=f"{analysis_ref}:canonical-window",
        descriptor=descriptor,
        byte_limit=min(
            20 * 1024,
            max(1, coach_commands._MAX_BRIDGE_BYTES - bridge.bytes_used - 2 * 1024),
        ),
        segment_bounds=segment_bounds,
    )
    next_cursor = None
    if page.get("next_page_descriptor") is not None:
        next_cursor = _new_cursor(bridge, command_name="analysis.outcomes.timeline", state={
            "analysis_ref": analysis_ref, "scope": scope, "segment_ref": segment_ref,
            "series": list(series), "offset": page["next_page_descriptor"]["offset"],
        })
    timeline = page["timeline"]
    timeline["next_cursor"] = next_cursor
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:timeline:{offset}", result={"analysis_ref": analysis_ref, "timeline": timeline, "next_cursor": next_cursor}), 0


async def _query_events(bridge: _ToolBridge, command_id: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"analysis_ref", "scope", "segment_ref", "event_kinds", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.events.list")
        analysis_ref = state["analysis_ref"]
        scope = state["scope"]
        segment_ref = state["segment_ref"]
        event_kinds = state["event_kinds"]
        offset = state["offset"]
        limit = state["limit"]
    else:
        analysis_ref = parameters.get("analysis_ref")
        scope = parameters.get("scope")
        segment_ref = parameters.get("segment_ref")
        event_kinds = _string_list(parameters.get("event_kinds"), field_name="event_kinds", max_items=16, allow_empty=False)
        if not all(EvidenceKeyRegistry().allows_event(kind) for kind in event_kinds):
            raise ProductCommandError("invalid_parameters", "event kind is not registered")
        offset = 0
        if not isinstance(analysis_ref, str) or scope not in {"whole_run", "evidence_segment"}:
            raise ProductCommandError("invalid_parameters", "events requires a bounded scope")
        if scope == "evidence_segment":
            if not isinstance(segment_ref, str):
                raise ProductCommandError("invalid_reference", "segment_ref is required")
            _require_reachable(bridge, segment_ref)
        elif segment_ref is not None:
            raise ProductCommandError("invalid_parameters", "whole_run cannot include segment_ref")
        limit = parameters.get("limit", _LIST_MAX)
    artifact, _, _ = await coach_commands._load_evidence_for_bridge(bridge, analysis_ref)
    bounds = None
    if scope == "evidence_segment":
        segment = _segment(artifact, segment_ref)
        bounds = (segment["start_ms"], segment["end_ms"])
    events: list[dict[str, Any]] = []
    stats_kill_fields = ("kill_index", "shots", "hits", "overshots")
    materialized_stats_kills: set[tuple[Any, ...]] = set()

    def stats_kill_key(
        time_ms: object,
        source_refs: object,
        values: object,
    ) -> tuple[Any, ...] | None:
        if not isinstance(source_refs, list) or not isinstance(values, dict):
            return None
        if not all(field in values for field in stats_kill_fields):
            return None
        return (
            time_ms,
            tuple(sorted(source_refs)),
            *(values[field] for field in stats_kill_fields),
        )

    for bundle in artifact.get("event_bundles", []):
        for event in bundle.get("events", []):
            if event.get("event_kind") not in event_kinds:
                continue
            if bounds is not None and not (bounds[0] <= event.get("start_ms", -1) < bounds[1]):
                continue
            events.append(_safe_event(event))
            if event.get("event_kind") == "kill":
                key = stats_kill_key(
                    event.get("start_ms"),
                    event.get("source_refs"),
                    event.get("attributes"),
                )
                if key is not None:
                    materialized_stats_kills.add(key)
    normalized_kind_by_metric = {
        "performance.shotsFired": "shot",
        "performance.shotsHit": "hit",
        "performance.shotsMissed": "miss",
        "performance.kills": "kill",
    }
    for record in artifact.get("normalized_outcome_records", []):
        start_ms = record.get("canonical_time_ms")
        if bounds is not None and not (bounds[0] <= start_ms < bounds[1]):
            continue
        record_kinds = {
            "kill"
            if value.get("metric_key", "").startswith("stats.kill.")
            else normalized_kind_by_metric.get(value.get("metric_key"))
            for value in record.get("values", [])
        }
        for event_kind in sorted(kind for kind in record_kinds if kind in event_kinds):
            if event_kind == "kill":
                stats_values = {
                    value.get("metric_key", "").removeprefix("stats.kill."): value.get("value")
                    for value in record.get("values", [])
                    if value.get("metric_key", "").startswith("stats.kill.")
                }
                if stats_kill_key(
                    start_ms, record.get("source_refs"), stats_values,
                ) in materialized_stats_kills:
                    continue
            events.append({
                "event_id": (
                    f"{analysis_ref}:normalized:{event_kind}:"
                    f"{record.get('source_priority')}:{record.get('source_event_index')}"
                ),
                "event_kind": event_kind,
                "start_ms": start_ms,
                "end_ms": start_ms,
                "source_time": record.get("source_time"),
                "source_priority": record.get("source_priority"),
                "source_event_index": record.get("source_event_index"),
                "values": list(record.get("values", [])),
                "source_refs": list(record.get("source_refs", [])),
                "confidence": None,
                "association": {
                    "status": "unavailable",
                    "limitations": ["target_association_not_observed"],
                },
                "limitations": ["timing_confidence_not_quantified"],
            })
    events.sort(key=lambda item: (item.get("start_ms", 0), item.get("end_ms", 0), item.get("event_id", "")))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    selected = events[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(events):
        next_cursor = _new_cursor(bridge, command_name="analysis.events.list", state={
            "analysis_ref": analysis_ref, "scope": scope, "segment_ref": segment_ref,
            "event_kinds": list(event_kinds), "offset": offset + len(selected), "limit": limit,
        })
    return _evidence_result(command_id, result_ref=f"{analysis_ref}:events:{offset}", result={"analysis_ref": analysis_ref, "scope": scope, "records": selected, "event_refs": [event["event_id"] for event in selected], "next_cursor": next_cursor}), 0


async def _query_event_get(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "event_ref"})
    table_ref = parameters.get("table_ref")
    event_ref = parameters.get("event_ref")
    if not isinstance(event_ref, str):
        raise ProductCommandError("invalid_reference", "event_ref is required")
    _require_reachable(bridge, event_ref)
    _, table, events = await _load_processed_table_for_bridge(bridge, table_ref)
    event = next((item for item in events if item.get("event_id") == event_ref), None)
    if event is None:
        raise ProductCommandError(
            "event_not_in_table",
            "event_ref is not a member of the requested ProcessedEventTable",
            kind="unavailable",
        )
    return _evidence_result(
        command_id,
        result_ref=f"{event_ref}:detail",
        result={"table": table, "event": _safe_event(event)},
    ), 0


async def _query_event_rank(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "field", "direction", "predicates", "limit"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    field = parameters.get("field")
    definition = _table_field(table, field)
    if definition["value_type"] != "number":
        raise ProductCommandError("invalid_parameters", "rank field must be numeric")
    direction = parameters.get("direction")
    if direction not in {"asc", "desc"}:
        raise ProductCommandError("invalid_parameters", "rank direction is invalid")
    predicates = _predicates(parameters.get("predicates"), table)
    limit = parameters.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    filtered = _matching_events(events, predicates)
    ranked = [
        event for event in filtered
        if isinstance(_event_field_value(event, str(field)), (int, float))
        and not isinstance(_event_field_value(event, str(field)), bool)
        and math.isfinite(float(_event_field_value(event, str(field))))
    ]
    ranked.sort(
        key=lambda event: (
            (
                -float(_event_field_value(event, str(field)))
                if direction == "desc"
                else float(_event_field_value(event, str(field)))
            ),
            event.get("start_ms", 0),
            event.get("event_id", ""),
        ),
    )
    selected = ranked[:limit]
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:rank:{field}:{direction}",
        result={
            "table_ref": table["table_ref"],
            "field": field,
            "direction": direction,
            "evaluated_count": len(events),
            "predicate_match_count": len(filtered),
            "included_count": len(ranked),
            "excluded_count": len(events) - len(ranked),
            "rows": [_safe_event(event) for event in selected],
            "event_refs": [event["event_id"] for event in selected],
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


async def _query_event_filter(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "predicates", "limit", "cursor"})
    if "cursor" in parameters:
        _exact_parameters(parameters, {"cursor"})
        state = _cursor_state(bridge, parameters.get("cursor"), "analysis.events.filter")
        table_ref = state["table_ref"]
        predicate_values = state["predicates"]
        limit = state["limit"]
        offset = state["offset"]
    else:
        table_ref = parameters.get("table_ref")
        predicate_values = parameters.get("predicates")
        limit = parameters.get("limit", _LIST_MAX)
        offset = 0
    _, table, events = await _load_processed_table_for_bridge(bridge, table_ref)
    predicates = _predicates(predicate_values, table)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _LIST_MAX:
        raise ProductCommandError("invalid_parameters", "limit must be between 1 and 20")
    matched = _matching_events(events, predicates)
    selected = matched[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(matched):
        next_cursor = _new_cursor(
            bridge,
            command_name="analysis.events.filter",
            state={
                "table_ref": table["table_ref"],
                "predicates": predicates,
                "limit": limit,
                "offset": offset + len(selected),
            },
        )
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:filter:{offset}",
        result={
            "table_ref": table["table_ref"],
            "evaluated_count": len(events),
            "matched_count": len(matched),
            "excluded_count": len(events) - len(matched),
            "rows": [_safe_event(event) for event in selected],
            "event_refs": [event["event_id"] for event in selected],
            "next_cursor": next_cursor,
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


def _run_phase_groups(events: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = {"early": [], "middle": [], "late": []}
    total = len(events)
    for index, event in enumerate(events):
        phase_index = min(2, (index * 3) // max(1, total))
        groups[("early", "middle", "late")[phase_index]].append(event)
    return [(phase, rows) for phase, rows in groups.items() if rows]


def _aggregate_groups(
    events: list[dict[str, Any]],
    fields: list[str],
    group_by: str | None,
    table: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if group_by is None:
        grouped: list[tuple[object, list[dict[str, Any]]]] = [("all", events)]
    elif group_by == "run_phase":
        grouped = list(_run_phase_groups(events))
    else:
        group_definition = _table_field(table, group_by)
        if (
            group_definition["role"] not in {"condition", "quality", "outcome"}
            or group_definition["value_type"] not in {"string", "boolean"}
        ):
            raise ProductCommandError(
                "invalid_parameters",
                "group_by must be run_phase or a registered categorical condition",
            )
        values: dict[str, tuple[object, list[dict[str, Any]]]] = {}
        for event in events:
            value = _event_field_value(event, group_by)
            if value is _EVENT_VALUE_MISSING or value is None:
                value = "unavailable"
            key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            values.setdefault(key, (value, []))[1].append(event)
        grouped = [values[key] for key in sorted(values)]
    output: list[dict[str, Any]] = []
    for label, rows in grouped:
        item: dict[str, Any] = {
            "count": len(rows),
            "fields": {field: _event_distribution(rows, field) for field in fields},
        }
        if group_by == "run_phase":
            item["phase"] = label
        else:
            item["group_value"] = label
        output.append(item)
    return output


async def _query_event_aggregate(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "fields", "group_by"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    fields = _string_list(
        parameters.get("fields"), field_name="fields", max_items=8, allow_empty=False,
    )
    for field in fields:
        if _table_field(table, field)["value_type"] != "number":
            raise ProductCommandError("invalid_parameters", "aggregate fields must be numeric")
    group_by = parameters.get("group_by")
    if group_by is not None and not isinstance(group_by, str):
        raise ProductCommandError("invalid_parameters", "group_by is invalid")
    groups = _aggregate_groups(events, fields, group_by, table)
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:aggregate",
        result={
            "table_ref": table["table_ref"],
            "evaluated_count": len(events),
            "group_by": group_by,
            "groups": groups,
            "completeness": table["completeness"],
            "limitations": list(table["limitations"]),
        },
    ), 0


async def _query_event_co_occurrence(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "left", "right", "relation"})
    if parameters.get("relation") != "same_event":
        raise ProductCommandError("invalid_parameters", "only same_event relation is supported")
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    left = _predicate(parameters.get("left"), table)
    right = _predicate(parameters.get("right"), table)
    buckets = {"both": [], "left_only": [], "right_only": [], "neither": []}
    for event in events:
        left_match = _predicate_matches(event, left)
        right_match = _predicate_matches(event, right)
        key = (
            "both" if left_match and right_match
            else "left_only" if left_match
            else "right_only" if right_match
            else "neither"
        )
        buckets[key].append(event)
    left_total = len(buckets["both"]) + len(buckets["left_only"])
    right_total = len(buckets["both"]) + len(buckets["right_only"])
    counterexamples = [*buckets["left_only"], *buckets["right_only"]]
    if not counterexamples:
        counterexamples = buckets["neither"]
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:co-occurrence",
        result={
            "table_ref": table["table_ref"],
            "relation": "same_event",
            "evaluated_count": len(events),
            "counts": {key: len(rows) for key, rows in buckets.items()},
            "rates": {
                "right_given_left": len(buckets["both"]) / left_total if left_total else None,
                "left_given_right": len(buckets["both"]) / right_total if right_total else None,
            },
            "supporting_event_refs": [event["event_id"] for event in buckets["both"][:20]],
            "counterexample_event_refs": [event["event_id"] for event in counterexamples[:20]],
            "completeness": table["completeness"],
            "limitations": [*table["limitations"], "co_occurrence_does_not_establish_causation"],
        },
    ), 0


async def _query_event_sequence(
    bridge: _ToolBridge,
    command_id: str,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    _exact_parameters(parameters, {"table_ref", "fields", "mode"})
    _, table, events = await _load_processed_table_for_bridge(
        bridge, parameters.get("table_ref"),
    )
    fields = _string_list(
        parameters.get("fields"), field_name="fields", max_items=4, allow_empty=False,
    )
    for field in fields:
        if _table_field(table, field)["value_type"] != "number":
            raise ProductCommandError("invalid_parameters", "sequence fields must be numeric")
    mode = parameters.get("mode")
    if mode == "early_middle_late":
        groups = _aggregate_groups(events, fields, "run_phase", table)
        body: dict[str, Any] = {"groups": groups}
    elif mode == "run_decile":
        deciles: list[dict[str, Any]] = []
        for decile in range(10):
            rows = [
                event for index, event in enumerate(events)
                if min(9, (index * 10) // max(1, len(events))) == decile
            ]
            if rows:
                deciles.append({
                    "decile": decile + 1,
                    "count": len(rows),
                    "fields": {field: _event_distribution(rows, field) for field in fields},
                })
        body = {"groups": deciles}
    elif mode == "adjacent":
        adjacent: dict[str, Any] = {}
        for field in fields:
            deltas: list[float] = []
            for previous, current in zip(events, events[1:]):
                before = _event_field_value(previous, field)
                after = _event_field_value(current, field)
                if all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in (before, after)
                ):
                    deltas.append(float(after) - float(before))
            adjacent[field] = (
                {
                    "pair_count": len(deltas),
                    "min": min(deltas),
                    "median": _nearest_rank(deltas, 0.5),
                    "max": max(deltas),
                    "mean": math.fsum(deltas) / len(deltas),
                }
                if deltas
                else {"pair_count": 0, "availability": "unavailable"}
            )
        body = {"adjacent_fields": adjacent}
    else:
        raise ProductCommandError("invalid_parameters", "sequence mode is invalid")
    return _evidence_result(
        command_id,
        result_ref=f"{table['table_ref']}:sequence:{mode}",
        result={
            "table_ref": table["table_ref"],
            "mode": mode,
            "evaluated_count": len(events),
            **body,
            "completeness": table["completeness"],
            "limitations": [*table["limitations"], "chronological_pattern_does_not_establish_learning_or_causation"],
        },
    ), 0


@dataclass
class _ToolBridge:
    bridge_id: str
    turn_id: str
    token_digest: str
    owner_id: str
    thread_id: int
    user_message_ref: str
    expires_at: float
    max_calls: int
    calls: int
    current_user_message: str | None = None
    bytes_used: int = 0
    signal_points_used: int = 0
    signal_bytes_used: int = 0
    reachable_refs: set[str] = field(default_factory=set)
    reference_descriptors: dict[str, dict[str, str]] = field(default_factory=dict)
    temporary_profile_refs: dict[str, str] = field(default_factory=dict)
    instruction_grants: dict[str, "_InstructionGrant"] = field(default_factory=dict)
    cursors: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class _InstructionGrant:
    schema_version: Literal["instruction_grant.v1"]
    bridge_id: str
    owner_id: str
    thread_id: int
    user_message_ref: str
    command_name: str
    target_ref: str
    parameters_digest: str
    expires_at: float

    def audit_projection(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "message_ref": self.user_message_ref,
            "command_name": self.command_name,
            "target_ref": self.target_ref,
            "status": "issued",
        }


_tool_bridges: dict[str, _ToolBridge] = {}
_tool_bridge_lock = asyncio.Lock()
_MAX_SIGNAL_BYTES = 32 * 1024
_MAX_SIGNAL_POINTS = 2_400
_ANALYSIS_WORKFLOW_TIMEOUT_SECONDS = 240.0
_ANALYSIS_WORKFLOW_POLL_SECONDS = 0.5


def _bridge_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stable_reachable_refs(value: object) -> set[str]:
    refs: set[str] = set()

    def visit(child: object, key: str | None = None) -> None:
        if isinstance(child, str):
            if (
                key is not None
                and (
                    key.endswith("_ref")
                    or key.endswith("_refs")
                    or key in {"analysis_id", "segment_id", "event_id", "evidence_id", "id"}
                )
                and child.startswith(("analysis:", "run:", "plan:", "plan-item:", "segment:", "event:", "metric:", "evidence:"))
                and not child.startswith("cursor:")
            ):
                refs.add(child)
            return
        if isinstance(child, Mapping):
            for nested_key, nested_value in child.items():
                if isinstance(nested_key, str):
                    visit(nested_value, nested_key)
            return
        if isinstance(child, list):
            for nested_value in child:
                visit(nested_value, key)

    visit(value)
    return refs


def _remember_reachable_result(bridge: _ToolBridge, value: object) -> None:
    bridge.reachable_refs.update(_stable_reachable_refs(value))

    def visit(child: object) -> None:
        if isinstance(child, Mapping):
            run_ref = child.get("run_ref")
            if isinstance(run_ref, str) and run_ref.startswith("run:"):
                descriptor = {
                    key: item
                    for key in ("scenario", "created_at", "readiness_state")
                    if isinstance((item := child.get(key)), str) and item
                }
                if descriptor:
                    bridge.reference_descriptors[run_ref] = descriptor
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)


_INSTRUCTION_TARGETS: dict[str, tuple[str, str]] = {
    "analysis.create_from_run": ("run_ref", "run"),
    "analysis.retry": ("analysis_ref", "analysis"),
    "analysis.delete": ("analysis_ref", "analysis"),
    "training_plan.save": ("plan_ref", "plan"),
    "training_plan.activate": ("plan_ref", "plan"),
    "training_plan.pause": ("plan_ref", "plan"),
    "training_plan.adjust": ("plan_ref", "plan"),
    "training_plan.item.add": ("plan_ref", "plan"),
    "training_plan.execution.record": ("item_ref", "plan-item"),
    "training_plan.retest.record": ("item_ref", "plan-item"),
}
_INSTRUCTION_ACTIONS: dict[str, tuple[str, ...]] = {
    "analysis.create_from_run": ("analyse", "analyze", "分析"),
    "analysis.retry": ("retry", "重试"),
    "analysis.delete": ("delete", "remove", "删除", "移除"),
    "training_plan.generate_draft": ("generate", "draft", "生成", "制定"),
    "training_plan.save": ("save", "保存"),
    "training_plan.activate": ("activate", "start", "启用", "开始"),
    "training_plan.pause": ("pause", "暂停"),
    "training_plan.adjust": ("adjust", "update", "调整", "更新"),
    "training_plan.item.add": ("add", "添加"),
    "training_plan.execution.record": ("record", "完成", "记录"),
    "training_plan.retest.record": ("retest", "复测", "记录"),
}


def _instruction_action_matches(command_name: str, quote: str) -> bool:
    return any(token in quote.lower() for token in _INSTRUCTION_ACTIONS.get(command_name, ()))


def _compatible_instruction_refs(bridge: _ToolBridge, kind: str) -> set[str]:
    prefix = f"{kind}:"
    return {ref for ref in bridge.reachable_refs if ref.startswith(prefix)}


def _quote_ref_candidates(quote: str, refs: set[str], kind: str) -> set[str]:
    candidates: set[str] = set()
    for ref in refs:
        ref_id = ref.partition(":")[2]
        if ref in quote or re.search(rf"(?<![A-Za-z0-9]){re.escape(kind)}[\\s:#-]*{re.escape(ref_id)}(?![0-9])", quote, re.IGNORECASE):
            candidates.add(ref)
    return candidates


def _normalize_instruction_target(
    bridge: _ToolBridge,
    command_name: str,
    parameters: Mapping[str, Any],
    quote: str,
) -> tuple[dict[str, Any], str] | None:
    target = _INSTRUCTION_TARGETS.get(command_name)
    if target is None:
        return None
    field_name, kind = target
    candidates = _compatible_instruction_refs(bridge, kind)
    if not candidates:
        return None
    quoted = _quote_ref_candidates(quote, candidates, kind)
    if len(quoted) == 1:
        resolved = next(iter(quoted))
    elif command_name == "analysis.create_from_run":
        scored = {
            ref: sum(
                1
                for key in ("scenario", "created_at")
                if (value := bridge.reference_descriptors.get(ref, {}).get(key))
                and value.casefold() in quote.casefold()
            )
            for ref in candidates
        }
        best_score = max(scored.values(), default=0)
        best = {ref for ref, score in scored.items() if score == best_score and score > 0}
        pending = {
            ref for ref in candidates
            if bridge.reference_descriptors.get(ref, {}).get("readiness_state") == "pending_analysis"
        }
        if len(best) == 1:
            resolved = next(iter(best))
        elif best_score == 0 and len(pending) == 1:
            resolved = next(iter(pending))
        else:
            return None
    elif len(candidates) == 1:
        resolved = next(iter(candidates))
    else:
        return None
    supplied = parameters.get(field_name)
    if kind in {"analysis", "run"} and isinstance(supplied, str) and supplied.isdecimal():
        supplied = f"{kind}:{supplied}"
    if supplied is not None and supplied != resolved:
        return None
    normalized = dict(parameters)
    normalized[field_name] = resolved
    return normalized, resolved


def _instruction_scalars_are_stated(parameters: Mapping[str, Any], quote: str) -> bool:
    """Reject model-supplied scalar facts which the exact user quote does not state."""
    for key, value in parameters.items():
        if key.endswith("_ref") or key.endswith("_refs") or key in {"plan_payload", "evidence_refs", "verification_targets"}:
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)) and str(value).lower() not in quote.lower():
            return False
        if isinstance(value, (list, dict)):
            return False
    return True


def _issue_instruction_grant(
    bridge: _ToolBridge,
    command_name: object,
    parameters: object,
    quote: object,
) -> tuple[_InstructionGrant, dict[str, Any]] | None:
    if (
        not isinstance(command_name, str)
        or command_name not in _WRITE_COMMANDS
        or not isinstance(parameters, Mapping)
        or not isinstance(quote, str)
        or not quote
        or len(quote) > 512
        or bridge.current_user_message is None
        or quote not in bridge.current_user_message
        or not _instruction_action_matches(command_name, quote)
    ):
        return None
    resolved = _normalize_instruction_target(bridge, command_name, parameters, quote)
    if resolved is None:
        return None
    normalized, target_ref = resolved
    if not _instruction_scalars_are_stated(normalized, quote):
        return None
    try:
        parameters_digest = _idempotency_digest(command_name, normalized)
    except (TypeError, ValueError):
        return None
    grant = _InstructionGrant(
        schema_version="instruction_grant.v1",
        bridge_id=bridge.bridge_id,
        owner_id=bridge.owner_id,
        thread_id=bridge.thread_id,
        user_message_ref=bridge.user_message_ref,
        command_name=command_name,
        target_ref=target_ref,
        parameters_digest=parameters_digest,
        expires_at=bridge.expires_at,
    )
    bridge.instruction_grants[parameters_digest] = grant
    return grant, normalized


def issue_tool_bridge(
    owner_id: str,
    thread_id: int,
    user_message_ref: str,
    endpoint: str,
    desktop_token: str | None = None,
    ttl_seconds: int = 300,
    max_calls: int = 6,
    *,
    reachable_refs: set[str] | None = None,
    temporary_profile_refs: Mapping[str, str] | None = None,
    current_user_message: str | None = None,
) -> dict[str, Any]:
    """Issue an in-memory, turn-scoped bearer bridge for one Coach turn."""
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.path != TOOL_BRIDGE_ENDPOINT
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("tool bridge endpoint must be the fixed loopback product route")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(thread_id, int) or thread_id < 1:
        raise ValueError("owner_id and thread_id are required")
    if not isinstance(user_message_ref, str) or not user_message_ref or len(user_message_ref) > 160:
        raise ValueError("user_message_ref is required")
    if current_user_message is not None and (
        not isinstance(current_user_message, str) or not current_user_message or len(current_user_message) > 16_384
    ):
        raise ValueError("current_user_message is invalid")
    if desktop_token is not None and (not isinstance(desktop_token, str) or not desktop_token):
        raise ValueError("desktop_token is invalid")
    if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 900:
        raise ValueError("ttl_seconds must be between 1 and 900")
    if not isinstance(max_calls, int) or not 1 <= max_calls <= 6:
        raise ValueError("max_calls must be between 1 and 6")
    safe_profile_refs: dict[str, str] = {}
    if temporary_profile_refs is not None:
        if not isinstance(temporary_profile_refs, Mapping):
            raise ValueError("temporary profile refs are invalid")
        for profile_ref, steam_id in temporary_profile_refs.items():
            if (
                not isinstance(profile_ref, str)
                or _STEAM_PROFILE_REF.fullmatch(profile_ref) is None
                or not isinstance(steam_id, str)
                or _STEAM_ID.fullmatch(steam_id) is None
            ):
                raise ValueError("temporary profile refs are invalid")
            safe_profile_refs[profile_ref] = steam_id
    token = secrets.token_urlsafe(32)
    token_digest = _bridge_digest(token)
    expires_at = time.time() + ttl_seconds
    bridge_id = f"bridge:{uuid.uuid4().hex}"
    turn_id = f"turn:{uuid.uuid4().hex}"
    _tool_bridges[token_digest] = _ToolBridge(
        bridge_id=bridge_id,
        turn_id=turn_id,
        token_digest=token_digest,
        owner_id=owner_id,
        thread_id=thread_id,
        user_message_ref=user_message_ref,
        expires_at=expires_at,
        max_calls=max_calls,
        calls=0,
        current_user_message=current_user_message,
        reachable_refs=set(reachable_refs or ()),
        temporary_profile_refs=safe_profile_refs,
    )
    bridge: dict[str, Any] = {
        "schema_version": "coach_tool_bridge.v1",
        "turn_id": turn_id,
        "endpoint": endpoint,
        "bearer_token": token,
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        "user_message_ref": user_message_ref,
    }
    if desktop_token is not None:
        bridge["desktop_token"] = desktop_token
    return bridge


async def _execute_kovaak_scores_bridge(
    bridge: _ToolBridge,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    command_id = _command_id(payload.get("command_id"))
    command_name = payload.get("command_name")
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping):
        return _result(
            command_id,
            "failed",
            warning_or_error=_error("invalid_parameters", "parameters must be an object"),
        )
    if command_name == "kovaak_scores.lookup":
        if set(parameters) != {"profile_ref"}:
            return _result(
                command_id,
                "failed",
                warning_or_error=_error(
                    "invalid_parameters", "kovaak_scores.lookup accepts only profile_ref",
                ),
            )
        profile_ref = parameters.get("profile_ref")
        steam_id = (
            bridge.temporary_profile_refs.get(profile_ref)
            if isinstance(profile_ref, str) and _STEAM_PROFILE_REF.fullmatch(profile_ref)
            else None
        )
        if steam_id is None:
            return _result(
                command_id,
                "unavailable",
                warning_or_error=_error(
                    "temporary_profile_unavailable",
                    "this temporary profile is not available in the current Coach turn",
                ),
            )
        try:
            from . import kovaak_benchmark_service

            snapshot = await kovaak_benchmark_service.project_temporary_snapshot(steam_id)
        except Exception:
            return _result(
                command_id,
                "unavailable",
                warning_or_error=_error(
                    "kovaak_scores_unavailable", "KovaaK scores are temporarily unavailable",
                ),
            )
        summary = _bounded_kovaak_score_summary(snapshot)
        if summary["availability"] != "available":
            return _result(
                command_id,
                "unavailable",
                warning_or_error=_error(
                    "kovaak_scores_unavailable", "KovaaK scores are temporarily unavailable",
                ),
            )
        # This result stays in the bridge response only: no journal/audit write.
        return _result(
            command_id,
            "succeeded",
            result_ref="kovaak_scores:temporary",
            result=summary,
        )
    if command_name == "kovaak_scores.refresh_connected":
        if parameters:
            return _result(
                command_id,
                "failed",
                warning_or_error=_error(
                    "invalid_parameters", "kovaak_scores.refresh_connected accepts no parameters",
                ),
            )
        audit_token = _audit_context.set({
            "thread_id": bridge.thread_id,
            "user_message_ref": bridge.user_message_ref,
            "command_name": "kovaak_scores.refresh_connected",
            "risk": _risk_for("kovaak_scores.refresh_connected"),
            "authorization_source": "coach_inferred",
            "idempotency_key": None,
            "parameters_digest": None,
            "safe_parameters_summary": {},
        })

        async def finish_refresh(result: dict[str, Any]) -> dict[str, Any]:
            try:
                return await _finish(bridge.owner_id, result)
            finally:
                _audit_context.reset(audit_token)

        try:
            from . import kovaak_benchmark_service

            snapshot = await kovaak_benchmark_service.refresh_connected_score_summary(
                bridge.owner_id,
            )
        except Exception as error:
            try:
                from . import kovaak_benchmark_service

                missing = isinstance(error, kovaak_benchmark_service.KovaaKConnectionNotFound)
            except (AttributeError, ImportError):
                missing = False
            return await finish_refresh(
                _result(
                    command_id,
                    "unavailable",
                    warning_or_error=_error(
                        "connected_account_unavailable" if missing else "kovaak_scores_unavailable",
                        "connect your KovaaK profile first" if missing else "KovaaK scores are temporarily unavailable",
                    ),
                ),
            )
        summary = _bounded_kovaak_score_summary(snapshot)
        if summary["availability"] != "available":
            return await finish_refresh(
                _result(
                    command_id,
                    "unavailable",
                    warning_or_error=_error(
                        "kovaak_scores_unavailable", "KovaaK scores are temporarily unavailable",
                    ),
                ),
            )
        return await finish_refresh(
            _result(
                command_id,
                "succeeded",
                result_ref="kovaak_scores:connected",
                result=summary,
            ),
        )
    return _result(
        command_id,
        "failed",
        warning_or_error=_error("unsupported_command", "command is not allowed"),
    )


async def _wait_for_created_analysis(
    bridge: _ToolBridge,
    result: dict[str, Any],
) -> dict[str, Any]:
    analysis_ref = result.get("result_ref")
    try:
        analysis_id, analysis_ref = _parse_ref(analysis_ref, "analysis")
    except ProductCommandError:
        return result

    deadline = time.monotonic() + _ANALYSIS_WORKFLOW_TIMEOUT_SECONDS
    while True:
        session = await queue.get_session(analysis_id)
        if session is None or session.get("user_id") != bridge.owner_id:
            failed = _copy_json(result)
            failed["status"] = "unavailable"
            failed["warning_or_error"] = _error(
                "analysis_unavailable", "Analysis is unavailable after creation",
            )
            return failed

        status = str(session.get("status") or "")
        if status == "done":
            _, context = await coach_context_refs.attach_context(
                bridge.owner_id,
                bridge.thread_id,
                kind="analysis",
                analysis_ref=analysis_ref,
            )
            completed = _copy_json(result)
            created = completed.get("result")
            completed["result"] = {
                **(created if isinstance(created, dict) else {}),
                "analysis_ref": analysis_ref,
                "analysis_status": "done",
                "context_ref": context["context_ref"],
            }
            return completed
        if status == "failed":
            failed = _copy_json(result)
            created = failed.get("result")
            failed["status"] = "unavailable"
            failed["result"] = {
                **(created if isinstance(created, dict) else {}),
                "analysis_ref": analysis_ref,
                "analysis_status": "failed",
            }
            failed["warning_or_error"] = _error(
                "analysis_failed", "Analysis failed before Coach could inspect it",
            )
            return failed
        if time.monotonic() >= deadline:
            waiting = _copy_json(result)
            created = waiting.get("result")
            waiting["status"] = "unavailable"
            waiting["result"] = {
                **(created if isinstance(created, dict) else {}),
                "analysis_ref": analysis_ref,
                "analysis_status": status or "pending",
            }
            waiting["warning_or_error"] = _error(
                "analysis_wait_timeout", "Analysis is still running",
            )
            return waiting
        await asyncio.sleep(_ANALYSIS_WORKFLOW_POLL_SECONDS)


async def execute_tool_bridge(bearer_token: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute an untrusted tool payload under its short-lived bridge principal."""
    if not isinstance(bearer_token, str) or not bearer_token:
        return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    digest = _bridge_digest(bearer_token)
    async with _tool_bridge_lock:
        bridge = _tool_bridges.get(digest)
        if bridge is None:
            return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
    async with bridge.lock:
        async with _tool_bridge_lock:
            current = _tool_bridges.get(digest)
            if current is not bridge or bridge.expires_at <= time.time() or bridge.calls >= bridge.max_calls:
                _tool_bridges.pop(digest, None)
                bridge.cursors.clear()
                return _result("command:bridge", "unavailable", warning_or_error=_error("bridge_unavailable", "tool bridge is unavailable"))
            bridge.calls += 1
        if (
            not isinstance(payload, Mapping)
            or any(not isinstance(key, str) for key in payload)
            or set(payload) - _TOOL_BRIDGE_PAYLOAD_KEYS
        ):
            return _result(
                "command:bridge",
                "failed",
                warning_or_error=_error(
                    "untrusted_field",
                    "Coach tool may not provide authorization or confirmation fields",
                ),
            )
        trusted_payload = {
            key: payload[key]
            for key in _TOOL_BRIDGE_PAYLOAD_KEYS
            if key in payload
        }
        trusted_payload["user_message_ref"] = bridge.user_message_ref
        command_name = trusted_payload.get("command_name")
        parameters = trusted_payload.get("parameters")
        instruction_quote = trusted_payload.pop("instruction_quote", None)
        if command_name in _KOVAAK_SCORE_COMMANDS:
            return await _execute_kovaak_scores_bridge(bridge, trusted_payload)
        if command_name == "analysis.delete":
            command_id = _command_id(trusted_payload.get("command_id"))
            if not isinstance(parameters, Mapping) or set(parameters) != {"analysis_ref"}:
                return _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(
                        "invalid_parameters",
                        "analysis.delete accepts only analysis_ref",
                    ),
                )
            analysis_ref = parameters["analysis_ref"]
            if isinstance(analysis_ref, str) and re.fullmatch(r"[1-9][0-9]*", analysis_ref):
                analysis_ref = f"analysis:{analysis_ref}"
            try:
                _parse_ref(analysis_ref, "analysis")
            except ProductCommandError as error:
                return _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(error.code, error.message),
                )
            if analysis_ref not in bridge.reachable_refs:
                return _result(
                    command_id,
                    "failed",
                    warning_or_error=_error(
                        "unreachable_ref",
                        "reference was not reached in this Coach turn",
                    ),
                )
            trusted_payload["parameters"] = {"analysis_ref": analysis_ref}
        if command_name in _EVIDENCE_QUERY_COMMANDS:
            return await _execute_evidence_bridge(bridge, trusted_payload)
        instruction_grant: _InstructionGrant | None = None
        if command_name in _WRITE_COMMANDS:
            issued = _issue_instruction_grant(
                bridge, command_name, trusted_payload.get("parameters"), instruction_quote,
            )
            if issued is not None:
                instruction_grant, normalized_parameters = issued
                trusted_payload["parameters"] = normalized_parameters
        result = await coach_commands.execute_product_command(
            bridge.owner_id,
            trusted_payload,
            authorization_source=(
                "explicit_user_request" if instruction_grant is not None else "coach_inferred"
            ),
            thread_id=bridge.thread_id,
            instruction_grant=instruction_grant,
        )
        if (
            command_name == "analysis.create_from_run"
            and instruction_grant is not None
            and result.get("status") == "succeeded"
        ):
            result = await _wait_for_created_analysis(bridge, result)
        safe_result = _copy_json(result)
        safe_result["authorization_source"] = (
            "explicit_user_request" if instruction_grant is not None else "coach_inferred"
        )
        confirmation = safe_result.get("confirmation")
        if isinstance(confirmation, dict):
            confirmation.pop("confirmation_ref", None)
        _remember_reachable_result(bridge, safe_result)
        return safe_result


async def revoke_tool_bridge(bearer_token: str) -> bool:
    if not isinstance(bearer_token, str) or not bearer_token:
        return False
    async with _tool_bridge_lock:
        bridge = _tool_bridges.pop(_bridge_digest(bearer_token), None)
        if bridge is not None:
            bridge.cursors.clear()
        return bridge is not None
