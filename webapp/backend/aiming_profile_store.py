"""Owner-scoped, deterministic aiming profile persistence (JSON file backed).

The store accepts only validated deterministic contributions.  It deliberately
does not expose a generic write method for Coach/LLM output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from . import file_store

log = logging.getLogger(__name__)


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

_PROFILE_PATH = "profile.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return (
        item.get("comparability") == "comparable"
        and item.get("confidence") in {"high", "medium"}
    )


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _projection(group: list[dict[str, Any]], key: tuple[str, str, str]) -> dict[str, Any]:
    values = [item for item in group if _dimension_included(item["dimension"])]
    metric_values = [item["dimension"]["metric_value"] for item in values]
    sorted_values = sorted(metric_values)
    if not sorted_values:
        return {
            "dimension_key": key[0],
            "scope": key[1],
            "scope_ref": key[2],
            "observation_count": 0,
            "metric_values": [],
            "metric_summary": {},
            "current_metric_value": None,
            "trend_direction": "unknown",
            "analysis_refs": [],
            "supporting_metric_refs": [],
            "counterexample_refs": [],
            "candidate_hypothesis_refs": [],
            "limitations": ["no_comparable_observations"],
        }
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 0:
        median = (sorted_values[mid - 1] + sorted_values[mid]) / 2
    else:
        median = sorted_values[mid]
    import statistics
    has_change_policy = any(
        item["dimension"].get("metric_change_policy") for item in values
    )
    limitations: list[str] = []
    if not has_change_policy:
        limitations.append("metric_change_policy_missing")
    elif n < 3:
        limitations.append("insufficient_observations")
    return {
        "dimension_key": key[0],
        "scope": key[1],
        "scope_ref": key[2],
        "observation_count": n,
        "metric_values": metric_values,
        "metric_summary": {
            "min": min(metric_values),
            "max": max(metric_values),
            "mean": statistics.mean(metric_values),
            "median": median,
            "stdev": statistics.stdev(metric_values) if n > 1 else 0.0,
        },
        "current_metric_value": metric_values[-1] if metric_values else None,
        "trend_direction": "unknown",
        "analysis_refs": _dedupe([item["analysis_ref"] for item in values]),
        "supporting_metric_refs": _dedupe([ref for item in values for ref in item["dimension"]["supporting_metric_refs"]]),
        "counterexample_refs": _dedupe([ref for item in values for ref in item["dimension"]["counterexample_refs"]]),
        "candidate_hypothesis_refs": _dedupe([ref for item in values for ref in item["dimension"]["candidate_hypothesis_refs"]]),
        "limitations": limitations,
    }


# ---- File-backed persistence ----

def _load_profile_doc() -> dict[str, Any]:
    data = file_store.read_json(_PROFILE_PATH)
    if data is None:
        return {"contributions": {}, "tombstones": [], "dimensions": [], "state": {}}
    data.setdefault("contributions", {})
    data.setdefault("tombstones", [])
    data.setdefault("dimensions", [])
    data.setdefault("state", {})
    return data


def _save_profile_doc(doc: dict[str, Any]) -> None:
    file_store.write_json(_PROFILE_PATH, doc)


def _rebuild_owner(doc: dict[str, Any], owner_id: str) -> None:
    doc["state"][owner_id] = {"rebuild_state": "pending", "updated_at": _utc_now()}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for analysis_ref, contrib in doc["contributions"].items():
        if contrib.get("status") != "active":
            continue
        if contrib.get("owner_id") != owner_id:
            continue
        revisions = contrib.get("revisions", [])
        if not revisions:
            continue
        latest = max(revisions, key=lambda r: r.get("revision", 0))
        try:
            payload = latest.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidProfileContribution("stored profile contribution is malformed") from exc
        if not isinstance(payload, dict):
            continue
        for dimension in payload.get("dimensions", []):
            if _dimension_included(dimension):
                key = (dimension["dimension_key"], dimension["scope"], dimension["scope_ref"])
                groups[key].append({"analysis_ref": analysis_ref, "dimension": dimension})
    doc["dimensions"] = []
    for key in sorted(groups):
        projection = _projection(groups[key], key)
        doc["dimensions"].append({
            "dimension_key": key[0],
            "scope": key[1],
            "scope_ref": key[2],
            "projection": projection,
        })
    doc["state"][owner_id] = {"rebuild_state": "clean", "updated_at": _utc_now()}


async def record_deterministic_contribution(
    owner_id: str, analysis_ref: str, payload: Mapping[str, Any],
) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    analysis_ref = _ref(analysis_ref, "analysis_ref", "analysis")
    normalized = _validate_contribution(owner_id, analysis_ref, payload)
    encoded = _json(normalized)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    async with _WRITE_LOCK:
        doc = _load_profile_doc()
        contrib = doc["contributions"].get(analysis_ref)
        if contrib is None:
            contribution_ref = f"profile-contribution:{uuid.uuid4().hex}"
            revision = 1
            contrib = {
                "contribution_ref": contribution_ref,
                "owner_id": owner_id,
                "current_revision": 1,
                "status": "active",
                "revisions": [],
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            doc["contributions"][analysis_ref] = contrib
        else:
            contribution_ref = contrib["contribution_ref"]
            revision = int(contrib["current_revision"])
            old_revisions = [r for r in contrib.get("revisions", []) if r.get("revision") == revision]
            if old_revisions and old_revisions[0].get("payload_digest") == digest and contrib["status"] == "active":
                return {
                    "contribution_ref": contribution_ref,
                    "analysis_ref": analysis_ref,
                    "revision": revision,
                    "status": "active",
                    "idempotent": True,
                    "included_in_current_profile": any(_dimension_included(item) for item in normalized["dimensions"]),
                }
            revision += 1
            contrib["current_revision"] = revision
            contrib["status"] = "active"
        contrib["revisions"].append({"revision": revision, "payload": normalized, "payload_digest": digest})
        contrib["updated_at"] = _utc_now()
        _rebuild_owner(doc, owner_id)
        _save_profile_doc(doc)
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
    doc = _load_profile_doc()
    result: list[dict[str, Any]] = []
    for analysis_ref, contrib in sorted(doc["contributions"].items(), key=lambda x: (x[1].get("created_at", ""), x[0])):
        revisions = contrib.get("revisions", [])
        latest = max(revisions, key=lambda r: r.get("revision", 0)) if revisions else None
        included = False
        if latest:
            payload = latest.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                included = any(_dimension_included(item) for item in payload.get("dimensions", []))
        result.append({
            "contribution_ref": contrib["contribution_ref"],
            "analysis_ref": analysis_ref,
            "revision": int(contrib["current_revision"]),
            "status": contrib["status"],
            "included_in_current_profile": included and contrib["status"] == "active",
            "created_at": contrib.get("created_at"),
            "updated_at": contrib.get("updated_at"),
        })
    return result


async def get_contribution(owner_id: str, contribution_ref: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    contribution_ref = _ref(contribution_ref, "contribution_ref", "profile-contribution")
    doc = _load_profile_doc()
    for analysis_ref, contrib in doc["contributions"].items():
        if contrib["contribution_ref"] == contribution_ref:
            if contrib.get("owner_id") != owner_id:
                raise ProfileForbidden("contribution owner mismatch")
            revisions = contrib.get("revisions", [])
            latest = max(revisions, key=lambda r: r.get("revision", 0)) if revisions else None
            payload = None
            if latest:
                payload = latest.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
            return {
                "contribution_ref": contribution_ref,
                "analysis_ref": analysis_ref,
                "revision": int(contrib["current_revision"]),
                "status": contrib["status"],
                "payload": payload,
                "created_at": contrib.get("created_at"),
                "updated_at": contrib.get("updated_at"),
            }
    raise ProfileNotFound(contribution_ref)


async def invalidate_analysis_contribution(owner_id: str, analysis_ref: str, *, reason: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    analysis_ref = _ref(analysis_ref, "analysis_ref", "analysis")
    reason = _text(reason, "reason", max_length=160)
    async with _WRITE_LOCK:
        doc = _load_profile_doc()
        contrib = doc["contributions"].get(analysis_ref)
        if contrib is None:
            raise ProfileNotFound(analysis_ref)
        existing_tombstone = None
        for t in doc.get("tombstones", []):
            if t.get("analysis_ref") == analysis_ref and t.get("invalidated_revision") == contrib["current_revision"]:
                existing_tombstone = t
                break
        if contrib["status"] == "invalidated" and existing_tombstone is not None:
            return {
                "contribution_ref": contrib["contribution_ref"],
                "analysis_ref": analysis_ref,
                "revision": int(contrib["current_revision"]),
                "status": "invalidated",
                "tombstone_ref": existing_tombstone["tombstone_ref"],
                "idempotent": True,
            }
        tombstone_ref = existing_tombstone["tombstone_ref"] if existing_tombstone else f"profile-tombstone:{uuid.uuid4().hex}"
        if existing_tombstone is None:
            doc["tombstones"].append({
                "tombstone_ref": tombstone_ref,
                "analysis_ref": analysis_ref,
                "invalidated_revision": int(contrib["current_revision"]),
                "reason": reason,
            })
        contrib["status"] = "invalidated"
        contrib["updated_at"] = _utc_now()
        _rebuild_owner(doc, owner_id)
        _save_profile_doc(doc)
    return {
        "contribution_ref": contrib["contribution_ref"],
        "analysis_ref": analysis_ref,
        "revision": int(contrib["current_revision"]),
        "status": "invalidated",
        "tombstone_ref": tombstone_ref,
        "idempotent": False,
    }


async def get_profile_snapshot(owner_id: str) -> dict[str, Any]:
    owner_id = _owner(owner_id)
    doc = _load_profile_doc()
    state = doc.get("state", {}).get(owner_id, {})
    dimensions = [d["projection"] for d in doc.get("dimensions", [])[:24]]
    contribution_refs = [
        c["contribution_ref"]
        for c in sorted(
            (c for c in doc["contributions"].values() if c.get("status") == "active"),
            key=lambda c: (c.get("updated_at", ""), c.get("contribution_ref", "")),
            reverse=True,
        )[:24]
    ]
    active_plan_ref, next_retest_refs = await _training_plan_projection(owner_id)
    return {
        "schema_version": "aiming_profile.v1",
        "owner_ref": owner_id,
        "profile_ref": f"profile-aiming:{owner_id}",
        "status": state.get("rebuild_state", "clean"),
        "dimensions": dimensions,
        "contribution_refs": contribution_refs,
        "next_retest_refs": next_retest_refs,
        "active_plan_ref": active_plan_ref,
        "updated_at": state.get("updated_at"),
    }


async def _training_plan_projection(owner_id: str) -> tuple[str | None, list[str]]:
    """Resolve the owner's active training plan and its pending retest refs."""
    try:
        from . import training_plan_store

        plans = await training_plan_store.list_plans(owner_id, status="active")
        if not plans:
            return None, []
        plan_ref = plans[0]["plan_id"]
        items = await training_plan_store.list_plan_items(owner_id, plan_ref)
        seen: set[str] = set()
        next_retest_refs: list[str] = []
        for item in items:
            for ref_key in ("matched_retest_ref", "near_transfer_retest_ref"):
                ref = item.get(ref_key)
                if isinstance(ref, str) and ref and ref not in seen:
                    seen.add(ref)
                    next_retest_refs.append(ref)
        return plan_ref, next_retest_refs
    except Exception:
        log.warning("training plan projection unavailable owner=%s", owner_id)
        return None, []


async def reconcile_profiles() -> dict[str, int]:
    async with _WRITE_LOCK:
        doc = _load_profile_doc()
        owners = [
            owner_id for owner_id, state in doc.get("state", {}).items()
            if state.get("rebuild_state") == "pending"
        ]
        if not owners:
            return {"owners_rebuilt": 0}
        for owner_id in owners:
            _rebuild_owner(doc, owner_id)
        _save_profile_doc(doc)
    return {"owners_rebuilt": len(owners)}


__all__ = [
    "ProfileError", "ProfileForbidden", "ProfileNotFound", "InvalidProfileContribution",
    "build_contribution_from_analysis_result", "get_contribution", "get_profile_snapshot",
    "invalidate_analysis_contribution",
    "list_contributions", "reconcile_profiles", "record_deterministic_contribution",
]
