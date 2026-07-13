"""Comparable deterministic History trends.

This module compares only versioned deterministic metrics from AnalysisResult
v2. It deliberately ignores Benchmark data, inferred/experimental metrics, raw
trace records, and all filesystem paths.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from .db import get_conn

_MIN_COVERAGE = 0.8


def _metric(result: dict, key: str) -> dict | None:
    metrics = ((result.get("deterministic") or {}).get("metrics") or {})
    value = metrics.get(key)
    return value if isinstance(value, dict) else None


def _scenario(result: dict) -> str | None:
    value = (result.get("input_snapshot") or {}).get("scenario")
    return str(value) if value else None


def _scenario_identity_version(result: dict) -> str | None:
    value = (result.get("input_snapshot") or {}).get("scenario_identity_version")
    return str(value) if value else None


def _quality_reason(result: dict, metric: dict) -> str | None:
    if metric.get("classification", "deterministic") != "deterministic":
        return "metric_not_deterministic"
    if metric.get("availability", "available") != "available":
        return "metric_unavailable"
    coverage = metric.get("coverage")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or coverage < _MIN_COVERAGE
    ):
        return "insufficient_metric_coverage"
    alignment = ((result.get("evidence") or {}).get("alignment") or {}).get("status")
    if alignment not in {"aligned", "not_required"}:
        return "insufficient_alignment_quality"
    return None


def compare_analysis_results(current: dict, baseline: dict, metric_key: str) -> dict:
    """Compare two v2 deterministic metrics or return one explicit reason."""
    if current.get("schema_version") != "analysis_result.v2" or baseline.get(
        "schema_version"
    ) != "analysis_result.v2":
        return {"comparable": False, "reason": "analysis_result_version_mismatch"}
    predicates = (
        ("analysis_type", current.get("analysis_type"), baseline.get("analysis_type")),
        ("scenario", _scenario(current), _scenario(baseline)),
        (
            "scenario_identity_version",
            _scenario_identity_version(current),
            _scenario_identity_version(baseline),
        ),
        ("input_mode", current.get("input_mode"), baseline.get("input_mode")),
    )
    for name, left, right in predicates:
        if not left or left != right:
            return {"comparable": False, "reason": f"{name}_mismatch"}
    current_metric = _metric(current, metric_key)
    baseline_metric = _metric(baseline, metric_key)
    if current_metric is None or baseline_metric is None:
        return {"comparable": False, "reason": "metric_missing"}
    for result, metric in ((current, current_metric), (baseline, baseline_metric)):
        reason = _quality_reason(result, metric)
        if reason:
            return {"comparable": False, "reason": reason}
    for field in ("metric_version", "unit"):
        if not current_metric.get(field) or current_metric.get(field) != baseline_metric.get(field):
            return {"comparable": False, "reason": f"metric_{field}_mismatch"}
    current_calibration = current_metric.get("calibration_ref")
    baseline_calibration = baseline_metric.get("calibration_ref")
    if not current_calibration or not baseline_calibration:
        return {"comparable": False, "reason": "calibration_compatibility_missing"}
    if current_calibration != baseline_calibration:
        return {"comparable": False, "reason": "calibration_mismatch"}
    current_value = current_metric.get("value")
    baseline_value = baseline_metric.get("value")
    if (
        isinstance(current_value, bool)
        or isinstance(baseline_value, bool)
        or not isinstance(current_value, (int, float))
        or not isinstance(baseline_value, (int, float))
        or not math.isfinite(float(current_value))
        or not math.isfinite(float(baseline_value))
    ):
        return {"comparable": False, "reason": "metric_value_invalid"}
    delta = float(current_value) - float(baseline_value)
    percent = None if baseline_value == 0 else delta / abs(float(baseline_value)) * 100
    return {
        "comparable": True,
        "reason": None,
        "metric_key": metric_key,
        "unit": current_metric["unit"],
        "metric_version": current_metric["metric_version"],
        "current": float(current_value),
        "baseline": float(baseline_value),
        "delta": delta,
        "percent_change": percent,
    }


def _safe_scenario_from_snapshot(raw_snapshot: object) -> str | None:
    if not isinstance(raw_snapshot, str) or not raw_snapshot:
        return None
    try:
        snapshot = json.loads(raw_snapshot)
    except json.JSONDecodeError:
        return None
    scenario = snapshot.get("scenario") if isinstance(snapshot, dict) else None
    if not isinstance(scenario, str) or not scenario.strip():
        return None
    value = scenario.strip()
    if os.path.isabs(value) or value.startswith("\\"):
        return None
    return value


async def session_history_metadata(user_id: str) -> dict[int, dict[str, str | None]]:
    """Read only the small input snapshot column needed by the History list."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, input_snapshot_json FROM sessions WHERE user_id=?",
        (user_id,),
    )
    return {
        int(row["id"]): {
            "scenario": _safe_scenario_from_snapshot(row["input_snapshot_json"]),
        }
        for row in await cur.fetchall()
    }


async def recent_trend_for_user(user_id: str, metric_key: str) -> dict:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, result FROM sessions WHERE user_id=? AND status='done' "
        "AND result IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 100",
        (user_id,),
    )
    rows = await cur.fetchall()
    parsed: list[tuple[int, dict]] = []
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("schema_version") == "analysis_result.v2":
            parsed.append((int(row["id"]), result))
    if len(parsed) < 2:
        return {"comparable": False, "reason": "insufficient_history"}
    current_id, current = parsed[0]
    last_reason = "no_comparable_baseline"
    for baseline_id, baseline in parsed[1:]:
        comparison = compare_analysis_results(current, baseline, metric_key)
        if comparison["comparable"]:
            return {**comparison, "current_session_id": current_id, "baseline_session_id": baseline_id}
        last_reason = comparison["reason"]
    return {"comparable": False, "reason": last_reason, "current_session_id": current_id}
