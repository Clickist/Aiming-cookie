from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from . import queue
from .coach_context import coerce_coach_diagnostic_context, project_coach_diagnostic_context
from .db import get_conn
from .read_models import build_record_presentation_label


CONTEXT_BUNDLE_SCHEMA_VERSION = "coach_turn_context.v1"
BENCHMARK_SUMMARY_SCHEMA_VERSION = "coach_benchmark_summary.v1"
_BENCHMARK_PROVIDER = "kovaaks-webapp"
_BENCHMARK_REVIEW_CANDIDATE_LIMIT = 8
_ANALYSIS_REF = re.compile(r"^analysis:([1-9][0-9]*)$")
_SAFE_TARGET_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@-]{1,200}$")
_KINDS = {"analysis", "issue", "time_range", "metric", "evidence_segment", "comparison"}


class ContextRefError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _rank(value: object) -> int | None:
    number = _finite_nonnegative(value)
    if number is None or not number.is_integer() or number > 9:
        return None
    return int(number)


def _observed_at(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def project_benchmark_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Project one complete KovaaK snapshot without exposing identity or provider payloads."""
    try:
        from .benchmark_catalog import load_catalog

        catalog = load_catalog()
    except ValueError:
        return None
    scenario_lookup = {
        item["scenario_id"]: (difficulty, item["scenario_name"])
        for difficulty in ("easier", "medium")
        for pair in catalog["pairs"]
        for item in [pair[difficulty]]
    }
    expected_metric_keys = {"score", "scenario_rank", "overall_rank"}
    snapshots: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if (
            record.get("provider") != _BENCHMARK_PROVIDER
            or record.get("catalog_version") != catalog["catalog_version"]
            or record.get("metric_key") not in expected_metric_keys
        ):
            continue
        observed_at = _observed_at(record.get("observed_at"))
        if observed_at is None:
            continue
        snapshots.setdefault(observed_at, []).append(record)

    for observed_at in sorted(snapshots, reverse=True):
        metrics: dict[tuple[str, str], float] = {}
        valid = True
        for record in snapshots[observed_at]:
            scenario_id = record.get("scenario_id")
            metric_key = record.get("metric_key")
            if not isinstance(scenario_id, str) or not isinstance(metric_key, str):
                valid = False
                break
            if metric_key == "overall_rank":
                if scenario_id not in {
                    "benchmark:viscose-s2:easier", "benchmark:viscose-s2:medium",
                }:
                    valid = False
                    break
            elif scenario_id not in scenario_lookup:
                valid = False
                break
            value = _finite_nonnegative(record.get("value"))
            key = (scenario_id, metric_key)
            if value is None or key in metrics:
                valid = False
                break
            metrics[key] = value
        if not valid:
            continue

        scenarios: list[dict[str, Any]] = []
        for difficulty in ("easier", "medium"):
            for pair in catalog["pairs"]:
                scenario = pair[difficulty]
                scenario_id = scenario["scenario_id"]
                score = metrics.get((scenario_id, "score"))
                rank = _rank(metrics.get((scenario_id, "scenario_rank")))
                if score is None or rank is None:
                    valid = False
                    break
                scenarios.append({
                    "difficulty": difficulty,
                    "scenario_name": scenario["scenario_name"],
                    "category": pair["category"],
                    "subcategory": pair["subcategory"],
                    "score": score,
                    "scenario_rank": rank,
                })
            if not valid:
                break
        ranks = {
            difficulty: _rank(metrics.get((f"benchmark:viscose-s2:{difficulty}", "overall_rank")))
            for difficulty in ("easier", "medium")
        }
        if not valid or any(rank is None for rank in ranks.values()) or len(metrics) != 158:
            continue
        candidates = sorted(
            scenarios,
            key=lambda item: (item["scenario_rank"], item["score"], item["difficulty"], item["scenario_name"]),
        )[:_BENCHMARK_REVIEW_CANDIDATE_LIMIT]
        return {
            "schema_version": BENCHMARK_SUMMARY_SCHEMA_VERSION,
            "catalog_ref": catalog["catalog_ref"],
            "catalog_version": catalog["catalog_version"],
            "observed_at": observed_at,
            "completion": {
                difficulty: {
                    "completed": sum(
                        1
                        for item in scenarios
                        if item["difficulty"] == difficulty and item["score"] > 0
                    ),
                    "required": 39,
                }
                for difficulty in ("easier", "medium")
            },
            "provisional_ranks": ranks,
            "scenarios": scenarios,
            "review_candidates": [dict(item) for item in candidates],
        }
    return None


def _coerce_benchmark_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "catalog_ref", "catalog_version", "observed_at", "completion",
        "provisional_ranks", "scenarios", "review_candidates",
    }:
        return None
    if value.get("schema_version") != BENCHMARK_SUMMARY_SCHEMA_VERSION:
        return None
    try:
        from .benchmark_catalog import load_catalog

        catalog = load_catalog()
    except ValueError:
        return None
    if (
        value.get("catalog_ref") != catalog["catalog_ref"]
        or value.get("catalog_version") != catalog["catalog_version"]
        or _observed_at(value.get("observed_at")) is None
    ):
        return None
    completion = value.get("completion")
    ranks = value.get("provisional_ranks")
    scenarios = value.get("scenarios")
    candidates = value.get("review_candidates")
    if not isinstance(completion, Mapping) or not isinstance(ranks, Mapping):
        return None
    if set(completion) != {"easier", "medium"}:
        return None
    for difficulty in ("easier", "medium"):
        item = completion.get(difficulty)
        if (
            not isinstance(item, Mapping)
            or set(item) != {"completed", "required"}
            or not isinstance(item.get("completed"), int)
            or isinstance(item.get("completed"), bool)
            or item.get("required") != 39
            or not 0 <= item["completed"] <= item["required"]
        ):
            return None
    if set(ranks) != {"easier", "medium"} or any(_rank(item) is None for item in ranks.values()):
        return None
    if not isinstance(scenarios, list) or len(scenarios) != 78 or not isinstance(candidates, list) or len(candidates) > _BENCHMARK_REVIEW_CANDIDATE_LIMIT:
        return None
    allowed_scenarios = {
        (difficulty, pair[difficulty]["scenario_name"]): {
            "category": pair["category"],
            "subcategory": pair["subcategory"],
        }
        for difficulty in ("easier", "medium")
        for pair in catalog["pairs"]
    }

    def valid_item(item: object) -> bool:
        if not isinstance(item, Mapping) or set(item) != {
            "difficulty", "scenario_name", "category", "subcategory", "score", "scenario_rank",
        }:
            return False
        source_labels = allowed_scenarios.get((item.get("difficulty"), item.get("scenario_name")))
        return (
            source_labels is not None
            and item.get("category") == source_labels["category"]
            and item.get("subcategory") == source_labels["subcategory"]
            and _finite_nonnegative(item.get("score")) is not None
            and _rank(item.get("scenario_rank")) is not None
        )
    if not all(valid_item(item) for item in scenarios) or not all(valid_item(item) for item in candidates):
        return None
    scenario_items = {
        (item["difficulty"], item["scenario_name"]): dict(item)
        for item in scenarios
    }
    candidate_keys = [
        (item["difficulty"], item["scenario_name"])
        for item in candidates
    ]
    if (
        len(scenario_items) != 78
        or len(candidate_keys) != len(set(candidate_keys))
        or any(scenario_items.get(key) != dict(item) for key, item in zip(candidate_keys, candidates))
    ):
        return None
    return {
        "schema_version": BENCHMARK_SUMMARY_SCHEMA_VERSION,
        "catalog_ref": value["catalog_ref"],
        "catalog_version": value["catalog_version"],
        "observed_at": value["observed_at"],
        "completion": {difficulty: dict(completion[difficulty]) for difficulty in ("easier", "medium")},
        "provisional_ranks": {difficulty: int(ranks[difficulty]) for difficulty in ("easier", "medium")},
        "scenarios": [dict(item) for item in scenarios],
        "review_candidates": [dict(item) for item in candidates],
    }


def coerce_context_bundle(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("schema_version") != CONTEXT_BUNDLE_SCHEMA_VERSION:
        return None
    items = value.get("contexts")
    if not isinstance(items, list) or len(items) > 8:
        return None
    canonical_items: list[dict[str, Any]] = []
    allowed = {
        "context_ref", "kind", "analysis_ref", "comparison_analysis_ref",
        "target_ref", "time_range_ms", "projection", "comparison_projection",
    }
    for item in items:
        if not isinstance(item, Mapping) or set(item) != allowed:
            return None
        if not isinstance(item.get("context_ref"), str) or item.get("kind") not in _KINDS:
            return None
        if _ANALYSIS_REF.fullmatch(str(item.get("analysis_ref") or "")) is None:
            return None
        comparison_ref = item.get("comparison_analysis_ref")
        if comparison_ref is not None and _ANALYSIS_REF.fullmatch(str(comparison_ref)) is None:
            return None
        target_ref = item.get("target_ref")
        if target_ref is not None and (
            not isinstance(target_ref, str) or _SAFE_TARGET_REF.fullmatch(target_ref) is None
        ):
            return None
        time_range = item.get("time_range_ms")
        if time_range is not None and (
            not isinstance(time_range, list)
            or len(time_range) != 2
            or not all(isinstance(part, (int, float)) and part >= 0 for part in time_range)
            or time_range[1] < time_range[0]
        ):
            return None
        projection = coerce_coach_diagnostic_context(item.get("projection"))
        if projection is None:
            return None
        comparison_projection = item.get("comparison_projection")
        if item.get("kind") == "comparison":
            comparison_projection = coerce_coach_diagnostic_context(comparison_projection)
            if comparison_ref is None or comparison_projection is None:
                return None
        elif comparison_ref is not None or comparison_projection is not None:
            return None
        canonical_items.append({
            "context_ref": item["context_ref"],
            "kind": item["kind"],
            "analysis_ref": item["analysis_ref"],
            "comparison_analysis_ref": comparison_ref,
            "target_ref": target_ref,
            "time_range_ms": time_range,
            "projection": projection,
            "comparison_projection": comparison_projection,
        })
    if len({item["context_ref"] for item in canonical_items}) != len(canonical_items):
        return None
    benchmark_summary = _coerce_benchmark_summary(value.get("benchmark_summary"))
    if value.get("benchmark_summary") is not None and benchmark_summary is None:
        return None
    if set(value) not in ({"schema_version", "contexts"}, {"schema_version", "contexts", "benchmark_summary"}):
        return None
    return {
        "schema_version": CONTEXT_BUNDLE_SCHEMA_VERSION,
        "contexts": canonical_items,
        "benchmark_summary": benchmark_summary,
    }


def _analysis_id(value: str | None) -> int:
    match = _ANALYSIS_REF.fullmatch(value or "")
    if match is None:
        raise ContextRefError("invalid_analysis_ref", "Analysis reference is invalid")
    return int(match.group(1))


async def _owned_done_analysis(owner_id: str, ref: str) -> dict[str, Any]:
    session_id = _analysis_id(ref)
    session = await queue.get_session(session_id)
    if session is None or session.get("user_id") != owner_id:
        raise ContextRefError("not_found", "Analysis is unavailable")
    if session.get("status") != "done" or not isinstance(session.get("result"), Mapping):
        raise ContextRefError("analysis_unavailable", "Analysis is not ready for Coach context")
    return session


def _projection(session: Mapping[str, Any]) -> dict[str, Any]:
    projected = project_coach_diagnostic_context(session["result"])
    analysis_ref = projected.get("analysis_ref")
    if isinstance(analysis_ref, dict) and analysis_ref.get("analysis_id") is None:
        analysis_ref["analysis_id"] = f"analysis:{session['id']}"
    canonical = coerce_coach_diagnostic_context(projected)
    if canonical is None:
        raise ContextRefError("context_unavailable", "Coach context projection is unavailable")
    serialized = json.dumps(
        canonical, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    if len(serialized.encode("utf-8")) > 64 * 1024:
        raise ContextRefError("context_too_large", "Coach context exceeds the bounded projection budget")
    return canonical


def _validate_target(kind: str, analysis_ref: str, target_ref: str | None, projection: Mapping[str, Any]) -> str:
    if kind == "analysis":
        if target_ref is not None:
            raise ContextRefError("invalid_target_ref", "Analysis context does not accept a target")
        return analysis_ref
    if kind == "comparison":
        if target_ref is not None:
            raise ContextRefError("invalid_target_ref", "Comparison context does not accept a target")
        return analysis_ref
    if not isinstance(target_ref, str) or not _SAFE_TARGET_REF.fullmatch(target_ref):
        raise ContextRefError("invalid_target_ref", "Context target is invalid")
    if kind == "issue":
        prefix = f"{analysis_ref}:issue:"
        if not target_ref.startswith(prefix):
            raise ContextRefError("invalid_target_ref", "Issue reference does not belong to the Analysis")
        try:
            index = int(target_ref.removeprefix(prefix))
        except ValueError as error:
            raise ContextRefError("invalid_target_ref", "Issue reference is invalid") from error
        issues = (projection.get("diagnosis") or {}).get("issues") or []
        if not 0 <= index < len(issues):
            raise ContextRefError("not_found", "Issue reference is unavailable")
    elif kind == "metric":
        diagnosis = projection.get("diagnosis") or {}
        known = set((diagnosis.get("summary") or {}).keys())
        for issue in diagnosis.get("issues") or []:
            if isinstance(issue, Mapping):
                known.update(str(ref) for ref in issue.get("metric_refs") or [])
        if target_ref not in known:
            raise ContextRefError("not_found", "Metric reference is unavailable")
    elif kind == "evidence_segment":
        refs = set((projection.get("evidence_summary") or {}).get("segment_refs") or [])
        if target_ref not in refs:
            raise ContextRefError("not_found", "EvidenceSegment reference is unavailable")
    return target_ref


def _presentation_label(
    kind: str,
    session: Mapping[str, Any],
    *,
    target_ref: str | None,
    start_ms: float | None,
    end_ms: float | None,
    comparison_session: Mapping[str, Any] | None = None,
) -> str:
    snapshot = session.get("input_snapshot")
    scenario = snapshot.get("scenario") if isinstance(snapshot, Mapping) else None
    if scenario is None:
        scenario = session.get("run_scenario") or session.get("scenario")
    label = build_record_presentation_label(
        scenario=scenario,
        training_at=session.get("training_at"),
        analysis_completed_at=session.get("finished_at"),
    )
    if kind == "analysis":
        return label
    if kind == "time_range":
        if start_ms == end_ms:
            return f"{label} | 片段：{start_ms:g} ms"
        return f"{label} | 片段：{start_ms:g}-{end_ms:g} ms"
    if kind == "comparison":
        if comparison_session is None:
            return f"{label} | 对比分析"
        comparison_snapshot = comparison_session.get("input_snapshot")
        comparison_scenario = (
            comparison_snapshot.get("scenario")
            if isinstance(comparison_snapshot, Mapping)
            else None
        )
        if comparison_scenario is None:
            comparison_scenario = comparison_session.get("run_scenario") or comparison_session.get("scenario")
        comparison_label = build_record_presentation_label(
            scenario=comparison_scenario,
            training_at=comparison_session.get("training_at"),
            analysis_completed_at=comparison_session.get("finished_at"),
        )
        return f"{label} | 对比：{comparison_label}"
    return f"{label} | { {'issue': '问题', 'metric': '数据项', 'evidence_segment': '证据片段'}.get(kind, '分析内容') }"


def _public_label(value: object) -> str:
    if isinstance(value, str) and value and re.search(r"\banalysis:[1-9][0-9]*\b", value) is None:
        return value
    return build_record_presentation_label(
        scenario=None,
        training_at=None,
        analysis_completed_at=None,
    )


def _public(
    row: Mapping[str, Any], *, status: str | None = None, label: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "coach_context_ref.v1",
        "context_ref": row["context_ref"],
        "kind": row["kind"],
        "status": status or row["status"],
        "label": label or _public_label(row.get("label")),
        "analysis_ref": f"analysis:{row['analysis_session_id']}",
        "comparison_analysis_ref": (
            f"analysis:{row['comparison_session_id']}" if row.get("comparison_session_id") else None
        ),
        "target_ref": row.get("target_ref"),
        "time_range_ms": (
            [row["start_ms"], row["end_ms"]] if row.get("start_ms") is not None else None
        ),
        "attached_at": row["attached_at"],
        "detached_at": row.get("detached_at"),
        "deleted_at": row.get("deleted_at"),
    }


async def _public_with_live_label(row: Mapping[str, Any], *, status: str | None = None) -> dict[str, Any]:
    session = await queue.get_session(int(row["analysis_session_id"]))
    if session is None:
        return _public(row, status=status)
    comparison_session = None
    comparison_id = row.get("comparison_session_id")
    if comparison_id is not None:
        comparison_session = await queue.get_session(int(comparison_id))
    return _public(
        row,
        status=status,
        label=_presentation_label(
            str(row["kind"]),
            session,
            target_ref=row.get("target_ref"),
            start_ms=row.get("start_ms"),
            end_ms=row.get("end_ms"),
            comparison_session=comparison_session,
        ),
    )


async def attach_context(
    owner_id: str,
    thread_id: int,
    *,
    kind: str,
    analysis_ref: str,
    target_ref: str | None = None,
    start_ms: float | None = None,
    end_ms: float | None = None,
    comparison_analysis_ref: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if kind not in _KINDS:
        raise ContextRefError("invalid_kind", "Context kind is invalid")
    session = await _owned_done_analysis(owner_id, analysis_ref)
    projection = _projection(session)
    comparison_id = None
    comparison_projection = None
    comparison = None
    if kind == "time_range":
        if start_ms is None:
            raise ContextRefError("invalid_time_range", "Time context requires start_ms")
        if end_ms is None:
            end_ms = start_ms
        if start_ms < 0 or end_ms < start_ms:
            raise ContextRefError("invalid_time_range", "Time range is invalid")
    elif start_ms is not None or end_ms is not None:
        raise ContextRefError("invalid_time_range", "Only time_range context accepts time bounds")
    if kind == "comparison":
        comparison = await _owned_done_analysis(owner_id, comparison_analysis_ref or "")
        comparison_id = int(comparison["id"])
        comparison_projection = _projection(comparison)
    elif comparison_analysis_ref is not None:
        raise ContextRefError("invalid_comparison_ref", "Only comparison context accepts another Analysis")
    target = _validate_target(kind, analysis_ref, target_ref, projection)
    descriptor = {
        "kind": kind,
        "analysis_ref": analysis_ref,
        "target_ref": target,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "comparison_analysis_ref": comparison_analysis_ref,
    }
    dedupe_key = hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    context_ref = f"context:{dedupe_key[:24]}"
    conn = await get_conn()
    existing = await (
        await conn.execute(
            "SELECT * FROM coach_context_refs WHERE thread_id=? AND dedupe_key=?",
            (thread_id, dedupe_key),
        )
    ).fetchone()
    if existing is not None and existing["status"] == "active":
        return "already_attached", _public(dict(existing))
    projection_json = json.dumps(
        projection, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    comparison_projection_json = (
        json.dumps(
            comparison_projection,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if comparison_projection is not None
        else None
    )
    label = _presentation_label(
        kind,
        session,
        target_ref=target,
        start_ms=start_ms,
        end_ms=end_ms,
        comparison_session=comparison,
    )
    await conn.execute(
        "INSERT INTO coach_context_refs(context_ref, thread_id, dedupe_key, kind, "
        "analysis_session_id, comparison_session_id, target_ref, start_ms, end_ms, label, "
        "projection_json, comparison_projection_json, status) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') "
        "ON CONFLICT(thread_id, dedupe_key) DO UPDATE SET status='active', "
        "projection_json=excluded.projection_json, "
        "comparison_projection_json=excluded.comparison_projection_json, "
        "label=excluded.label, detached_at=NULL, "
        "deleted_at=NULL, attached_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
        (
            context_ref, thread_id, dedupe_key, kind, int(session["id"]), comparison_id,
            target, start_ms, end_ms, label, projection_json, comparison_projection_json,
        ),
    )
    await conn.commit()
    row = await (
        await conn.execute(
            "SELECT * FROM coach_context_refs WHERE thread_id=? AND dedupe_key=?",
            (thread_id, dedupe_key),
        )
    ).fetchone()
    return "attached", _public(dict(row), label=label)


async def list_contexts(thread_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
    conn = await get_conn()
    sql = "SELECT * FROM coach_context_refs WHERE thread_id=?"
    params: tuple[Any, ...] = (thread_id,)
    if active_only:
        sql += " AND status='active'"
    rows = await (await conn.execute(sql + " ORDER BY attached_at, context_ref", params)).fetchall()
    return [await _public_with_live_label(dict(row)) for row in rows]


async def unavailable_context_refs(context_refs: Sequence[str]) -> set[str]:
    refs = {ref for ref in context_refs if isinstance(ref, str)}
    if not refs:
        return set()
    conn = await get_conn()
    placeholders = ", ".join("?" for _ in refs)
    rows = await (
        await conn.execute(
            "SELECT context_ref FROM coach_context_refs "
            f"WHERE status!='active' AND context_ref IN ({placeholders})",
            tuple(refs),
        )
    ).fetchall()
    return {str(row["context_ref"]) for row in rows}


async def detach_context(owner_id: str, thread_id: int, context_ref: str) -> tuple[str, dict[str, Any]] | None:
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT c.* FROM coach_context_refs c JOIN coach_threads t ON t.id=c.thread_id "
            "WHERE c.context_ref=? AND c.thread_id=? AND t.user_id=?",
            (context_ref, thread_id, owner_id),
        )
    ).fetchone()
    if row is None:
        return None
    if row["status"] != "active":
        return "already_detached", await _public_with_live_label(dict(row))
    await conn.execute(
        "UPDATE coach_context_refs SET status='detached', detached_at=CURRENT_TIMESTAMP, "
        "updated_at=CURRENT_TIMESTAMP WHERE context_ref=?",
        (context_ref,),
    )
    await conn.commit()
    updated = await (
        await conn.execute("SELECT * FROM coach_context_refs WHERE context_ref=?", (context_ref,))
    ).fetchone()
    return "detached", await _public_with_live_label(dict(updated))


async def build_context_bundle(
    thread_id: int,
    requested_refs: Sequence[str] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT * FROM coach_context_refs WHERE thread_id=? AND status='active' "
            "ORDER BY attached_at, context_ref",
            (thread_id,),
        )
    ).fetchall()
    available = {row["context_ref"]: dict(row) for row in rows}
    refs = list(available) if requested_refs is None else list(requested_refs)
    if len(refs) > 8 or len(refs) != len(set(refs)):
        raise ContextRefError("invalid_context_refs", "Context refs must be unique and bounded")
    if any(ref not in available for ref in refs):
        raise ContextRefError("context_unavailable", "One or more contexts are unavailable")
    contexts = []
    snapshots = []
    for ref in refs:
        row = available[ref]
        try:
            projection = json.loads(row["projection_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ContextRefError("context_unavailable", "Context projection is unavailable") from error
        canonical = coerce_coach_diagnostic_context(projection)
        if canonical is None:
            raise ContextRefError("context_unavailable", "Context projection is invalid")
        comparison_canonical = None
        if row["kind"] == "comparison":
            try:
                comparison_projection = json.loads(row["comparison_projection_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise ContextRefError(
                    "context_unavailable", "Comparison context projection is unavailable",
                ) from error
            comparison_canonical = coerce_coach_diagnostic_context(comparison_projection)
            if comparison_canonical is None:
                raise ContextRefError(
                    "context_unavailable", "Comparison context projection is invalid",
                )
        snapshots.append(await _public_with_live_label(row))
        contexts.append({
            "context_ref": ref,
            "kind": row["kind"],
            "analysis_ref": f"analysis:{row['analysis_session_id']}",
            "comparison_analysis_ref": (
                f"analysis:{row['comparison_session_id']}" if row.get("comparison_session_id") else None
            ),
            "target_ref": row.get("target_ref"),
            "time_range_ms": (
                [row["start_ms"], row["end_ms"]] if row.get("start_ms") is not None else None
            ),
            "projection": canonical,
            "comparison_projection": comparison_canonical,
        })
    thread = await (
        await conn.execute("SELECT user_id FROM coach_threads WHERE id=?", (thread_id,))
    ).fetchone()
    benchmark_summary = None
    if thread is not None:
        try:
            from . import benchmark_catalog, benchmark_store

            catalog = benchmark_catalog.load_catalog()
            records = await benchmark_store.list_latest_snapshot(
                thread["user_id"],
                provider=_BENCHMARK_PROVIDER,
                catalog_version=catalog["catalog_version"],
            )
            benchmark_summary = project_benchmark_summary(records)
        except (AttributeError, ValueError):
            benchmark_summary = None
    bundle = {
        "schema_version": CONTEXT_BUNDLE_SCHEMA_VERSION,
        "contexts": contexts,
        "benchmark_summary": benchmark_summary,
    }
    encoded = json.dumps(bundle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ContextRefError("context_too_large", "Combined Coach context exceeds the budget")
    return bundle, snapshots


async def mark_analysis_deleted(analysis_session_id: int, *, conn=None) -> int:
    owns_commit = conn is None
    if conn is None:
        conn = await get_conn()
    cursor = await conn.execute(
        "UPDATE coach_context_refs SET status='deleted', deleted_at=CURRENT_TIMESTAMP, "
        "updated_at=CURRENT_TIMESTAMP WHERE status='active' AND "
        "(analysis_session_id=? OR comparison_session_id=?)",
        (analysis_session_id, analysis_session_id),
    )
    if owns_commit:
        await conn.commit()
    return int(cursor.rowcount or 0)


async def overlay_snapshot_statuses(snapshots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not snapshots:
        return []
    conn = await get_conn()
    out: list[dict[str, Any]] = []
    for snapshot in snapshots:
        value = dict(snapshot)
        ref = value.get("context_ref")
        row = await (
            await conn.execute(
                "SELECT status, deleted_at FROM coach_context_refs WHERE context_ref=?",
                (ref,),
            )
        ).fetchone()
        if row is not None and row["status"] == "deleted":
            value["status"] = "deleted"
            value["deleted_at"] = row["deleted_at"]
        out.append(value)
    return out
