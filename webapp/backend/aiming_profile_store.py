"""Owner-scoped, deterministic aiming profile persistence.

The store accepts only validated deterministic contributions.  It deliberately
does not expose a generic write method for Coach/LLM output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .db import get_conn


class ProfileError(Exception):
    """Base class for aiming profile persistence errors."""


class ProfileForbidden(ProfileError):
    pass


class ProfileNotFound(ProfileError):
    pass


class InvalidProfileContribution(ProfileError):
    pass


_WRITE_LOCK = asyncio.Lock()
_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_DIMENSION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?:\.[a-z][a-z0-9_-]{0,63})+$")
_CONFIDENCE = {"high", "medium", "low"}
_COMPARABILITY = {"comparable", "not_comparable", "unavailable"}
_DIRECTIONS = {
    "lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only",
}
_METRIC_DIRECTIONS = {
    "static_clicking.corrective_count": "lower_better",
    "static_clicking.peak_speed": "comparison_only",
    "dynamic_clicking.acquisition_time_ms": "lower_better",
    "dynamic_clicking.normalized_click_error": "lower_better",
    "dynamic_clicking.predictive_lead": "comparison_only",
    "dynamic_clicking.relative_velocity": "comparison_only",
    "dynamic_clicking.target_state_accuracy": "higher_better",
    "continuous_tracking.alignment_latency_ms": "lower_better",
    "continuous_tracking.coherence": "higher_better",
    "continuous_tracking.correction_direction_reversal_count": "lower_better",
    "continuous_tracking.human_response_latency_ms": "lower_better",
    "continuous_tracking.loss_count": "lower_better",
    "continuous_tracking.loss_duration_ms": "lower_better",
    "continuous_tracking.observed_change_response_ms": "lower_better",
    "continuous_tracking.phase_lag_ms": "comparison_only",
    "continuous_tracking.predictive_lead_ms": "comparison_only",
    "continuous_tracking.reacquisition_latency_ms": "lower_better",
    "continuous_tracking.relative_lag_ms": "comparison_only",
    "continuous_tracking.smoothness_acceleration_rms": "comparison_only",
    "continuous_tracking.sparc": "higher_better",
    "continuous_tracking.target_relative_error_px": "lower_better",
    "continuous_tracking.time_in_radius_ratio": "higher_better",
    "continuous_tracking.velocity_gain": "target_band",
    "target_switching.carry_over_overshoot_ratio": "lower_better",
    "target_switching.first_damage_latency_ms": "lower_better",
    "target_switching.first_shot_latency_ms": "lower_better",
    "target_switching.path_efficiency": "higher_better",
    "target_switching.settle_duration_ms": "lower_better",
    "target_switching.terminal_correction_ratio": "lower_better",
    "target_switching.transition_distance_px": "comparison_only",
    "target_switching.transition_time_ms": "lower_better",
}
_NATIVE_STATIC_PROFILE_METRIC_KEYS = {
    "corrective_count": "static_clicking.corrective_count",
    "peak_speed": "static_clicking.peak_speed",
}


def _text(value: Any, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise InvalidProfileContribution(f"{field} must be a bounded non-empty string")
    text = value.strip()
    if "\x00" in text or re.search(r"(?:^|[\\/])(?:users?|appdata|private|raw|video)[\\/]", text, re.I):
        raise InvalidProfileContribution(f"{field} contains an unsafe path")
    return text


def _owner(value: Any) -> str:
    return _text(value, "owner_id", max_length=128)


def _ref(value: Any, field: str, prefix: str | None = None) -> str:
    ref = _text(value, field, max_length=192)
    if not _REF_RE.fullmatch(ref) or (prefix is not None and not ref.startswith(f"{prefix}:")):
        raise InvalidProfileContribution(f"{field} is not a stable {prefix or 'product'} reference")
    return ref


def _refs(value: Any, field: str, *, required: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidProfileContribution(f"{field} must be a list of references")
    if len(value) > 16 or (required and not value):
        raise InvalidProfileContribution(f"{field} has an invalid length")
    out = [_ref(item, field) for item in value]
    return list(dict.fromkeys(out))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _validate_dimension(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise InvalidProfileContribution("profile dimension must be an object")
    allowed = {
        "dimension_key", "scope", "scenario_profile_ref", "normalization_ref", "metric_ref",
        "metric_value", "unit", "expected_direction", "confidence", "comparability",
        "supporting_metric_refs", "counterexample_refs", "candidate_hypothesis_refs",
    }
    if set(raw) - allowed:
        raise InvalidProfileContribution("profile dimension contains unsupported fields")
    key = _text(raw.get("dimension_key"), "dimension_key", max_length=96)
    if not _DIMENSION_RE.fullmatch(key):
        raise InvalidProfileContribution("dimension_key is invalid")
    scope = raw.get("scope")
    if scope not in {"exact_scenario", "cross_scenario_normalized"}:
        raise InvalidProfileContribution("profile dimension scope is invalid")
    scenario_ref = raw.get("scenario_profile_ref")
    normalization_ref = raw.get("normalization_ref")
    if scope == "exact_scenario":
        scenario_ref = _ref(scenario_ref, "scenario_profile_ref", "scenario")
        if normalization_ref is not None:
            raise InvalidProfileContribution("exact scenario dimension cannot contain normalization_ref")
        scope_ref = scenario_ref
    else:
        normalization_ref = _ref(normalization_ref, "normalization_ref", "normalization")
        if scenario_ref is not None:
            raise InvalidProfileContribution("normalized dimension cannot contain scenario_profile_ref")
        scope_ref = normalization_ref
    metric_ref = _ref(raw.get("metric_ref"), "metric_ref", "metric")
    value = raw.get("metric_value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise InvalidProfileContribution("metric_value must be finite")
    direction = raw.get("expected_direction")
    if direction not in _DIRECTIONS:
        raise InvalidProfileContribution("expected_direction is invalid")
    confidence = raw.get("confidence")
    if confidence not in _CONFIDENCE:
        raise InvalidProfileContribution("confidence is invalid")
    comparability = raw.get("comparability")
    if comparability not in _COMPARABILITY:
        raise InvalidProfileContribution("comparability is invalid")
    return {
        "dimension_key": key,
        "scope": scope,
        "scope_ref": scope_ref,
        **({"scenario_profile_ref": scenario_ref} if scenario_ref else {"normalization_ref": normalization_ref}),
        "metric_ref": metric_ref,
        "metric_value": float(value),
        "unit": _text(raw.get("unit"), "unit", max_length=80),
        "expected_direction": direction,
        "confidence": confidence,
        "comparability": comparability,
        "supporting_metric_refs": _refs(raw.get("supporting_metric_refs"), "supporting_metric_refs", required=True),
        "counterexample_refs": _refs(raw.get("counterexample_refs", []), "counterexample_refs"),
        "candidate_hypothesis_refs": _refs(raw.get("candidate_hypothesis_refs", []), "candidate_hypothesis_refs"),
    }


def _validate_contribution(owner_id: str, analysis_ref: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    _owner(owner_id)
    _ref(analysis_ref, "analysis_ref", "analysis")
    if not isinstance(payload, Mapping):
        raise InvalidProfileContribution("profile contribution must be an object")
    if set(payload) != {"schema_version", "source_kind", "dimensions"}:
        raise InvalidProfileContribution("profile contribution shape is invalid")
    if payload.get("schema_version") != "profile_contribution.v1":
        raise InvalidProfileContribution("unsupported profile contribution schema")
    if payload.get("source_kind") != "deterministic":
        raise InvalidProfileContribution("profile contribution must be deterministic")
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, (str, bytes)) or not isinstance(dimensions, Sequence) or not dimensions or len(dimensions) > 24:
        raise InvalidProfileContribution("profile contribution dimensions are invalid")
    return {
        "schema_version": "profile_contribution.v1",
        "source_kind": "deterministic",
        "dimensions": [_validate_dimension(item) for item in dimensions],
    }


def _frozen_profile_scenario_resolution(
    result: Mapping[str, Any], scenario: Mapping[str, Any],
) -> tuple[str, set[str]] | None:
    input_snapshot = result.get("input_snapshot")
    resolution = (
        input_snapshot.get("scenario_resolution")
        if isinstance(input_snapshot, Mapping)
        else None
    )
    if not isinstance(resolution, Mapping):
        return None
    scenario_ref = scenario.get("scenario_profile_ref")
    aim_family = scenario.get("aim_family")
    analysis_version = result.get("analysis_version")
    analyzer_refs = scenario.get("analyzer_refs")
    allowed_analyzers = resolution.get("allowed_analyzers")
    allowed_metric_families = resolution.get("allowed_metric_families")
    if (
        not isinstance(scenario_ref, str)
        or not isinstance(aim_family, str)
        or not isinstance(analysis_version, str)
        or isinstance(analyzer_refs, (str, bytes))
        or not isinstance(analyzer_refs, Sequence)
        or isinstance(allowed_analyzers, (str, bytes))
        or not isinstance(allowed_analyzers, Sequence)
        or isinstance(allowed_metric_families, (str, bytes))
        or not isinstance(allowed_metric_families, Sequence)
        or resolution.get("scenario_profile_ref") != scenario_ref
        or resolution.get("aim_family") != aim_family
        or analysis_version not in analyzer_refs
        or analysis_version not in allowed_analyzers
    ):
        return None
    families = {
        family for family in allowed_metric_families
        if isinstance(family, str) and family
    }
    return (scenario_ref, families) if families else None


def build_contribution_from_analysis_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project only formal, evidence-backed metrics into an exact-scenario profile."""
    if not isinstance(result, Mapping) or result.get("schema_version") != "analysis_result.v2":
        return None
    scenario = result.get("scenario")
    deterministic = result.get("deterministic")
    evidence = result.get("evidence")
    if not all(isinstance(value, Mapping) for value in (scenario, deterministic, evidence)):
        return None
    scenario_ref = scenario.get("scenario_profile_ref")
    if (
        scenario.get("support_status") not in {"supported", "partial"}
        or deterministic.get("support_status") not in {"supported", "partial"}
        or not isinstance(scenario_ref, str)
        or not scenario_ref.startswith("scenario:")
        or not isinstance(evidence.get("derived_artifact"), Mapping)
    ):
        return None
    frozen_resolution = _frozen_profile_scenario_resolution(result, scenario)
    if frozen_resolution is None:
        return None
    _, allowed_metric_families = frozen_resolution
    metrics = deterministic.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    dimensions: list[dict[str, Any]] = []
    for raw_key, raw_metric in sorted(metrics.items()):
        if not isinstance(raw_key, str) or not isinstance(raw_metric, Mapping):
            continue
        metric_key = raw_key
        if (
            result.get("analysis_version") == "native_flicking.v1"
            and scenario.get("aim_family") == "static_clicking"
            and "native_flicking.v1" in (scenario.get("analyzer_refs") or [])
        ):
            metric_key = _NATIVE_STATIC_PROFILE_METRIC_KEYS.get(raw_key, raw_key)
        direction = _METRIC_DIRECTIONS.get(metric_key)
        if direction is None:
            continue
        metric_family, _, _ = metric_key.partition(".")
        if metric_family not in allowed_metric_families:
            return None
        value = raw_metric.get("value")
        provenance = raw_metric.get("provenance")
        if (
            raw_metric.get("availability") != "available"
            or raw_metric.get("classification") != "deterministic"
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not isinstance(provenance, Mapping)
            or provenance.get("kind") not in {"derived", "measured"}
        ):
            continue
        version = raw_metric.get("metric_version")
        unit = raw_metric.get("unit")
        if not isinstance(version, str) or not version or not isinstance(unit, str) or not unit:
            continue
        coverage = raw_metric.get("coverage")
        limitations = raw_metric.get("limitations")
        limitations = limitations if isinstance(limitations, list) else []
        if (
            isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and math.isfinite(float(coverage))
            and float(coverage) >= 0.95
            and not limitations
        ):
            confidence = "high"
        elif (
            isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and math.isfinite(float(coverage))
            and float(coverage) >= 0.75
        ):
            confidence = "medium"
        else:
            confidence = "low"
        metric_ref = f"metric:{metric_key}@{version}"
        dimensions.append({
            "dimension_key": metric_key,
            "scope": "exact_scenario",
            "scenario_profile_ref": scenario_ref,
            "metric_ref": metric_ref,
            "metric_value": float(value),
            "unit": unit,
            "expected_direction": direction,
            "confidence": confidence,
            "comparability": (
                "comparable" if confidence in {"high", "medium"} else "not_comparable"
            ),
            "supporting_metric_refs": [metric_ref],
            "counterexample_refs": [],
            "candidate_hypothesis_refs": [],
        })
    if not dimensions:
        return None
    return {
        "schema_version": "profile_contribution.v1",
        "source_kind": "deterministic",
        "dimensions": dimensions[:24],
    }


def _dimension_included(item: Mapping[str, Any]) -> bool:
    return item["comparability"] == "comparable" and item["confidence"] in {"high", "medium"}


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _projection(group: list[dict[str, Any]], key: tuple[str, str, str]) -> dict[str, Any]:
    values = [item for item in group if _dimension_included(item["dimension"])]
    if not values:
        raise AssertionError("empty profile group")
    first = values[0]
    latest = values[-1]
    direction = first["dimension"]["expected_direction"]
    directions = {item["dimension"]["expected_direction"] for item in values}
    trend = "unknown"
    limitations: list[str] = []
    if len(values) > 1 and len(directions) == 1 and direction in {"lower_better", "higher_better"}:
        before = float(first["dimension"]["metric_value"])
        after = float(latest["dimension"]["metric_value"])
        if after == before:
            trend = "stable"
        else:
            limitations.append("metric_change_policy_missing")
    if len(values) < 2:
        limitations.append("insufficient_comparable_history")
    if len(directions) > 1:
        limitations.append("expected_direction_conflict")
    confidence = "high" if all(item["dimension"]["confidence"] == "high" for item in values) else "medium"
    return {
        "dimension_key": key[0],
        "scope": key[1],
        "scope_ref": key[2],
        "current_metric_value": latest["dimension"]["metric_value"],
        "unit": latest["dimension"]["unit"],
        "expected_direction": direction if len(directions) == 1 else "comparison_only",
        "trend_direction": trend,
        "confidence": confidence,
        "observation_count": len(values),
        "analysis_refs": _dedupe([item["analysis_ref"] for item in values]),
        "supporting_metric_refs": _dedupe([ref for item in values for ref in item["dimension"]["supporting_metric_refs"]]),
        "counterexample_refs": _dedupe([ref for item in values for ref in item["dimension"]["counterexample_refs"]]),
        "candidate_hypothesis_refs": _dedupe([ref for item in values for ref in item["dimension"]["candidate_hypothesis_refs"]]),
        "limitations": limitations,
    }


async def _rebuild_owner(conn: Any, owner_id: str) -> None:
    await conn.execute(
        "INSERT INTO aiming_profile_state(owner_id, rebuild_state) VALUES(?, 'pending') "
        "ON CONFLICT(owner_id) DO UPDATE SET rebuild_state='pending', updated_at=CURRENT_TIMESTAMP",
        (owner_id,),
    )
    cur = await conn.execute(
        "SELECT c.analysis_ref, r.payload_json FROM profile_contributions c "
        "JOIN profile_contribution_revisions r ON r.owner_id=c.owner_id "
        "AND r.analysis_ref=c.analysis_ref AND r.revision=c.current_revision "
        "WHERE c.owner_id=? AND c.status='active' ORDER BY c.created_at, c.analysis_ref",
        (owner_id,),
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in await cur.fetchall():
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidProfileContribution("stored profile contribution is malformed") from exc
        for dimension in payload["dimensions"]:
            if _dimension_included(dimension):
                key = (dimension["dimension_key"], dimension["scope"], dimension["scope_ref"])
                groups[key].append({"analysis_ref": row["analysis_ref"], "dimension": dimension})
    await conn.execute("DELETE FROM aiming_profile_dimensions WHERE owner_id=?", (owner_id,))
    for key in sorted(groups):
        projection = _projection(groups[key], key)
        await conn.execute(
            "INSERT INTO aiming_profile_dimensions(owner_id, dimension_key, scope, scope_ref, projection_json) "
            "VALUES(?, ?, ?, ?, ?)",
            (owner_id, key[0], key[1], key[2], _json(projection)),
        )
    await conn.execute(
        "UPDATE aiming_profile_state SET rebuild_state='clean', updated_at=CURRENT_TIMESTAMP WHERE owner_id=?",
        (owner_id,),
    )


def _contribution_ref(row: Mapping[str, Any]) -> str:
    return str(row["contribution_ref"])


async def record_deterministic_contribution(
    owner_id: str, analysis_ref: str, payload: Mapping[str, Any],
) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    analysis_ref = _ref(analysis_ref, "analysis_ref", "analysis")
    normalized = _validate_contribution(owner_id, analysis_ref, payload)
    encoded = _json(normalized)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await (
                await conn.execute(
                    "SELECT contribution_ref, current_revision, status FROM profile_contributions "
                    "WHERE owner_id=? AND analysis_ref=?", (owner_id, analysis_ref),
                )
            ).fetchone()
            if row is None:
                contribution_ref = f"profile-contribution:{uuid.uuid4().hex}"
                revision = 1
                await conn.execute(
                    "INSERT INTO profile_contributions(owner_id, analysis_ref, contribution_ref, current_revision, status) "
                    "VALUES(?, ?, ?, 1, 'active')",
                    (owner_id, analysis_ref, contribution_ref),
                )
            else:
                contribution_ref = _contribution_ref(row)
                revision = int(row["current_revision"])
                old = await (
                    await conn.execute(
                        "SELECT payload_digest FROM profile_contribution_revisions WHERE owner_id=? AND analysis_ref=? AND revision=?",
                        (owner_id, analysis_ref, revision),
                    )
                ).fetchone()
                if old is not None and old["payload_digest"] == digest and row["status"] == "active":
                    await conn.execute("COMMIT")
                    return {
                        "contribution_ref": contribution_ref,
                        "analysis_ref": analysis_ref,
                        "revision": revision,
                        "status": "active",
                        "idempotent": True,
                        "included_in_current_profile": any(_dimension_included(item) for item in normalized["dimensions"]),
                    }
                revision += 1
                await conn.execute(
                    "UPDATE profile_contributions SET current_revision=?, status='active', updated_at=CURRENT_TIMESTAMP "
                    "WHERE owner_id=? AND analysis_ref=?",
                    (revision, owner_id, analysis_ref),
                )
            await conn.execute(
                "INSERT INTO profile_contribution_revisions(owner_id, analysis_ref, revision, payload_json, payload_digest) "
                "VALUES(?, ?, ?, ?, ?)",
                (owner_id, analysis_ref, revision, encoded, digest),
            )
            await _rebuild_owner(conn, owner_id)
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return {
        "contribution_ref": contribution_ref,
        "analysis_ref": analysis_ref,
        "revision": revision,
        "status": "active",
        "idempotent": False,
        "included_in_current_profile": any(_dimension_included(item) for item in normalized["dimensions"]),
    }


async def list_contributions(owner_id: str) -> list[dict[str, Any]]:
    owner_id = _owner(owner_id)
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT contribution_ref, analysis_ref, current_revision, status, created_at, updated_at "
        "FROM profile_contributions WHERE owner_id=? ORDER BY created_at, analysis_ref", (owner_id,),
    )
    rows = await cur.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        revision = await (
            await conn.execute(
                "SELECT payload_json FROM profile_contribution_revisions WHERE owner_id=? AND analysis_ref=? AND revision=?",
                (owner_id, row["analysis_ref"], row["current_revision"]),
            )
        ).fetchone()
        included = False
        if revision is not None:
            payload = json.loads(revision["payload_json"])
            included = any(_dimension_included(item) for item in payload["dimensions"])
        result.append({
            "contribution_ref": row["contribution_ref"], "analysis_ref": row["analysis_ref"],
            "revision": int(row["current_revision"]), "status": row["status"],
            "included_in_current_profile": included and row["status"] == "active",
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return result


async def get_contribution(owner_id: str, contribution_ref: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    contribution_ref = _ref(contribution_ref, "contribution_ref", "profile-contribution")
    conn = await get_conn()
    row = await (
        await conn.execute(
            "SELECT contribution_ref, analysis_ref, current_revision, status, created_at, updated_at "
            "FROM profile_contributions WHERE owner_id=? AND contribution_ref=?",
            (owner_id, contribution_ref),
        )
    ).fetchone()
    if row is None:
        other = await (
            await conn.execute("SELECT 1 FROM profile_contributions WHERE contribution_ref=?", (contribution_ref,))
        ).fetchone()
        if other is not None:
            raise ProfileForbidden(contribution_ref)
        raise ProfileNotFound(contribution_ref)
    revision = await (
        await conn.execute(
            "SELECT payload_json FROM profile_contribution_revisions WHERE owner_id=? AND analysis_ref=? AND revision=?",
            (owner_id, row["analysis_ref"], row["current_revision"]),
        )
    ).fetchone()
    return {
        "contribution_ref": row["contribution_ref"], "analysis_ref": row["analysis_ref"],
        "revision": int(row["current_revision"]), "status": row["status"],
        "payload": json.loads(revision["payload_json"]) if revision else None,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


async def invalidate_analysis_contribution(owner_id: str, analysis_ref: str, *, reason: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    analysis_ref = _ref(analysis_ref, "analysis_ref", "analysis")
    reason = _text(reason, "reason", max_length=160)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await (
                await conn.execute(
                    "SELECT contribution_ref, current_revision, status FROM profile_contributions "
                    "WHERE owner_id=? AND analysis_ref=?", (owner_id, analysis_ref),
                )
            ).fetchone()
            if row is None:
                other = await (
                    await conn.execute("SELECT 1 FROM profile_contributions WHERE analysis_ref=?", (analysis_ref,))
                ).fetchone()
                if other is not None:
                    raise ProfileForbidden(analysis_ref)
                raise ProfileNotFound(analysis_ref)
            tombstone = await (
                await conn.execute(
                    "SELECT tombstone_ref FROM profile_contribution_tombstones WHERE owner_id=? AND analysis_ref=? "
                    "AND invalidated_revision=?", (owner_id, analysis_ref, row["current_revision"]),
                )
            ).fetchone()
            if row["status"] == "invalidated" and tombstone is not None:
                await conn.execute("COMMIT")
                return {
                    "contribution_ref": row["contribution_ref"], "analysis_ref": analysis_ref,
                    "revision": int(row["current_revision"]), "status": "invalidated",
                    "tombstone_ref": tombstone["tombstone_ref"], "idempotent": True,
                }
            tombstone_ref = tombstone["tombstone_ref"] if tombstone else f"profile-tombstone:{uuid.uuid4().hex}"
            await conn.execute(
                "INSERT OR IGNORE INTO profile_contribution_tombstones "
                "(tombstone_ref, owner_id, analysis_ref, invalidated_revision, reason) VALUES(?, ?, ?, ?, ?)",
                (tombstone_ref, owner_id, analysis_ref, row["current_revision"], reason),
            )
            await conn.execute(
                "UPDATE profile_contributions SET status='invalidated', updated_at=CURRENT_TIMESTAMP "
                "WHERE owner_id=? AND analysis_ref=?", (owner_id, analysis_ref),
            )
            await conn.execute(
                "DELETE FROM profile_contribution_revisions WHERE owner_id=? AND analysis_ref=?",
                (owner_id, analysis_ref),
            )
            await _rebuild_owner(conn, owner_id)
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return {
        "contribution_ref": row["contribution_ref"], "analysis_ref": analysis_ref,
        "revision": int(row["current_revision"]), "status": "invalidated",
        "tombstone_ref": tombstone_ref, "idempotent": False,
    }


async def get_profile_snapshot(owner_id: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    conn = await get_conn()
    state = await (
        await conn.execute("SELECT rebuild_state, updated_at FROM aiming_profile_state WHERE owner_id=?", (owner_id,))
    ).fetchone()
    cur = await conn.execute(
        "SELECT projection_json FROM aiming_profile_dimensions WHERE owner_id=? "
        "ORDER BY dimension_key, scope, scope_ref LIMIT 24", (owner_id,),
    )
    dimensions = [json.loads(row["projection_json"]) for row in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT contribution_ref FROM profile_contributions WHERE owner_id=? AND status='active' "
        "ORDER BY updated_at DESC, analysis_ref LIMIT 24", (owner_id,),
    )
    contribution_refs = [row["contribution_ref"] for row in await cur.fetchall()]
    active_plan = await (
        await conn.execute(
            "SELECT plan_id FROM training_plans WHERE owner_id=? AND status='active'",
            (owner_id,),
        )
    ).fetchone()
    next_retest_refs: list[str] = []
    if active_plan is not None:
        cur = await conn.execute(
            "SELECT item_payload_json FROM training_plan_items WHERE owner_id=? AND plan_id=? "
            "AND status IN ('planned', 'active') ORDER BY item_ref LIMIT 12",
            (owner_id, active_plan["plan_id"]),
        )
        for row in await cur.fetchall():
            payload = json.loads(row["item_payload_json"])
            next_retest_refs.extend((
                payload["matched_retest_ref"],
                payload["near_transfer_retest_ref"],
            ))
    return {
        "schema_version": "aiming_profile.v1",
        "owner_ref": owner_id,
        "profile_ref": f"profile-aiming:{owner_id}",
        "status": state["rebuild_state"] if state else "clean",
        "dimensions": dimensions,
        "contribution_refs": contribution_refs,
        "next_retest_refs": _dedupe(next_retest_refs)[:24],
        "active_plan_ref": active_plan["plan_id"] if active_plan else None,
        "updated_at": state["updated_at"] if state else None,
    }


async def reconcile_profiles() -> dict[str, int]:
    conn = await get_conn()
    async with _WRITE_LOCK:
        cur = await conn.execute("SELECT owner_id FROM aiming_profile_state WHERE rebuild_state='pending' ORDER BY owner_id")
        owners = [row["owner_id"] for row in await cur.fetchall()]
        if not owners:
            return {"owners_rebuilt": 0}
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for owner_id in owners:
                await _rebuild_owner(conn, owner_id)
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return {"owners_rebuilt": len(owners)}


__all__ = [
    "ProfileError", "ProfileForbidden", "ProfileNotFound", "InvalidProfileContribution",
    "build_contribution_from_analysis_result", "get_contribution", "get_profile_snapshot",
    "invalidate_analysis_contribution",
    "list_contributions", "reconcile_profiles", "record_deterministic_contribution",
]
