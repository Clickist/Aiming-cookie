"""Fail-closed Raw click, Stats kill and reviewed visual-track association."""

from __future__ import annotations

from copy import deepcopy
import json
from math import hypot, isfinite
from pathlib import Path
import re
from typing import Mapping, Sequence

from .analysis_evidence import (
    validate_event_bundle_v2,
    validate_outcome_association_rule_binding_v1,
)


RULE_REGISTRY_SCHEMA_VERSION = "outcome_association_rule_registry.v1"
RESULT_SCHEMA_VERSION = "outcome_association_result.v1"
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,239}$")


def _ref(value: object, field: str) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise ValueError(f"{field} must be a stable ref")
    return value


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer")
    return value


def _number(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field} is invalid")
    return result


def _unavailable(*limitations: str) -> dict:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "unavailable",
        "event_bundle": None,
        "limitations": sorted(set(limitations)),
    }


def _validate_rule_registry_binding(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("outcome association rule binding must be a dict")
    schema_version = value.get("schema_version")
    if schema_version == "outcome_association_rule_binding.v1":
        return validate_outcome_association_rule_binding_v1(value)
    raise ValueError("outcome association rule binding schema version is unsupported")


def validate_outcome_association_rule_registry_v1(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "registry_version", "entries",
    }:
        raise ValueError("outcome association rule registry fields are invalid")
    if value["schema_version"] != RULE_REGISTRY_SCHEMA_VERSION:
        raise ValueError("outcome association rule registry version is unsupported")
    registry_version = value["registry_version"]
    if (
        not isinstance(registry_version, str)
        or not registry_version
        or len(registry_version) > 80
        or any(ord(character) < 32 for character in registry_version)
    ):
        raise ValueError("outcome association rule registry version is invalid")
    entries = value["entries"]
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("outcome association rule registry entries are invalid")
    rule_refs: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"status", "binding"}:
            raise ValueError(f"outcome association rule registry entry {index} is invalid")
        if entry["status"] not in {"active", "retired"}:
            raise ValueError("outcome association rule registry status is invalid")
        binding = _validate_rule_registry_binding(entry["binding"])
        if binding["rule_ref"] in rule_refs:
            raise ValueError("outcome association rule registry refs must be unique")
        rule_refs.add(binding["rule_ref"])
    return deepcopy(value)


def load_outcome_association_rule_registry_v1(path: str | Path | None = None) -> dict:
    registry_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parent.parent
        / "knowledge" / "scenarios" / "outcome-association-rules.v1.json"
    )
    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("outcome association rule registry is unavailable") from exc
    return validate_outcome_association_rule_registry_v1(decoded)


def _select_binding(
    registry: dict,
    *,
    scenario_profile_ref: str,
    visual_quality_profile_ref: str,
    canonical_timebase_version: str,
    stats_parser_version: str,
) -> dict | None:
    matches = [
        entry["binding"]
        for entry in registry["entries"]
        if entry["status"] == "active"
        and entry["binding"]["scenario_profile_ref"] == scenario_profile_ref
        and entry["binding"]["visual_quality_profile_ref"] == visual_quality_profile_ref
        and entry["binding"]["canonical_timebase_version"] == canonical_timebase_version
        and entry["binding"]["stats_parser_version"] == stats_parser_version
    ]
    return deepcopy(matches[0]) if len(matches) == 1 else None


def _nearest_track_sample(
    samples: object, click_time_ms: int,
) -> tuple[dict, int] | None:
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise ValueError("target track samples must be a list")
    candidates: list[tuple[int, int, dict]] = []
    for index, raw in enumerate(samples):
        if not isinstance(raw, Mapping):
            raise ValueError("target track sample is invalid")
        time_ms = _integer(raw.get("canonical_time_ms"), "target sample time")
        sample = {
            "canonical_time_ms": time_ms,
            "x": _number(raw.get("x"), "target sample x"),
            "y": _number(raw.get("y"), "target sample y"),
            "radius": _number(raw.get("radius"), "target sample radius", minimum=0.01),
            "confidence": _number(
                raw.get("confidence"), "target sample confidence", minimum=0.0,
            ),
        }
        if sample["confidence"] > 1.0:
            raise ValueError("target sample confidence is invalid")
        candidates.append((abs(time_ms - click_time_ms), index, sample))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][2], candidates[0][0]


def associate_one_shot_kills_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    scenario_profile_ref: str,
    visual_quality_profile_ref: str,
    raw_input_source_ref: str,
    stats_source_ref: str,
    stats_parser_version: str,
    visual_source_ref: str,
    click_events: Sequence[Mapping[str, object]],
    stats_kills: Sequence[Mapping[str, object]],
    viewport_size: Sequence[int],
    target_tracks: Sequence[Mapping[str, object]],
    rule_registry: Mapping[str, object],
) -> dict:
    analysis_ref = _ref(analysis_ref, "analysis_ref")
    scenario_profile_ref = _ref(scenario_profile_ref, "scenario_profile_ref")
    visual_quality_profile_ref = _ref(
        visual_quality_profile_ref, "visual_quality_profile_ref",
    )
    raw_input_source_ref = _ref(raw_input_source_ref, "raw_input_source_ref")
    stats_source_ref = _ref(stats_source_ref, "stats_source_ref")
    if not isinstance(stats_parser_version, str) or not stats_parser_version:
        raise ValueError("stats_parser_version is required")
    visual_source_ref = _ref(visual_source_ref, "visual_source_ref")
    if not isinstance(canonical_time_window, Mapping):
        raise ValueError("canonical_time_window is required")
    start_ms = _integer(canonical_time_window.get("start_ms"), "window start")
    end_ms = _integer(canonical_time_window.get("end_ms"), "window end")
    if end_ms <= start_ms or canonical_time_window.get("timebase_version") is None:
        raise ValueError("canonical_time_window is invalid")
    timebase_version = str(canonical_time_window["timebase_version"])
    registry = validate_outcome_association_rule_registry_v1(dict(rule_registry))
    binding = _select_binding(
        registry,
        scenario_profile_ref=scenario_profile_ref,
        visual_quality_profile_ref=visual_quality_profile_ref,
        canonical_timebase_version=timebase_version,
        stats_parser_version=stats_parser_version,
    )
    if binding is None:
        return _unavailable("outcome_association_rule_unavailable")
    if binding["raw_click_extractor_version"] != "raw-left-rising-edge.v1":
        return _unavailable("raw_click_extractor_unsupported")

    if (
        isinstance(viewport_size, (str, bytes))
        or not isinstance(viewport_size, Sequence)
        or len(viewport_size) != 2
    ):
        raise ValueError("viewport_size must be [width, height]")
    width = _integer(viewport_size[0], "viewport width", minimum=1)
    height = _integer(viewport_size[1], "viewport height", minimum=1)
    crosshair_x = width / 2.0
    crosshair_y = height / 2.0

    clicks: list[dict] = []
    click_refs: set[str] = set()
    for raw in click_events:
        if not isinstance(raw, Mapping) or set(raw) != {"event_ref", "time_ms"}:
            raise ValueError("click event fields are invalid")
        event_ref = _ref(raw["event_ref"], "click event ref")
        time_ms = _integer(raw["time_ms"], "click event time")
        if event_ref in click_refs or not start_ms <= time_ms < end_ms:
            raise ValueError("click event is duplicated or outside canonical window")
        click_refs.add(event_ref)
        clicks.append({"event_ref": event_ref, "time_ms": time_ms})

    kills: list[dict] = []
    kill_refs: set[str] = set()
    for raw in stats_kills:
        if not isinstance(raw, Mapping) or set(raw) != {
            "event_ref", "time_ms", "kill_index", "shots", "hits", "overshots",
        }:
            raise ValueError("Stats kill fields are invalid")
        kill = {
            "event_ref": _ref(raw["event_ref"], "Stats kill event ref"),
            "time_ms": _integer(raw["time_ms"], "Stats kill time"),
            "kill_index": _integer(raw["kill_index"], "Stats kill index"),
            "shots": _integer(raw["shots"], "Stats kill shots"),
            "hits": _integer(raw["hits"], "Stats kill hits"),
            "overshots": _integer(raw["overshots"], "Stats kill overshots"),
        }
        if kill["event_ref"] in kill_refs or not start_ms <= kill["time_ms"] < end_ms:
            raise ValueError("Stats kill is duplicated or outside canonical window")
        kill_refs.add(kill["event_ref"])
        kills.append(kill)

    if isinstance(target_tracks, (str, bytes)) or not isinstance(target_tracks, Sequence):
        raise ValueError("target_tracks must be a list")
    normalized_tracks: list[dict] = []
    saw_unstable_identity = False
    track_refs: set[str] = set()
    for raw in target_tracks:
        if not isinstance(raw, Mapping) or set(raw) != {
            "track_ref", "identity_status", "samples",
        }:
            raise ValueError("target track fields are invalid")
        track_ref = _ref(raw["track_ref"], "target track ref")
        if not track_ref.startswith(f"{analysis_ref}:target-track:") or track_ref in track_refs:
            raise ValueError("target track ref is duplicated or analysis-unbound")
        track_refs.add(track_ref)
        identity_status = raw["identity_status"]
        if identity_status != "stable":
            saw_unstable_identity = True
        normalized_tracks.append({
            "track_ref": track_ref,
            "identity_status": identity_status,
            "samples": raw["samples"],
        })

    timing = binding["timing_window_ms"]
    predicate = binding["stats_predicate"]
    track_predicate = binding["track_predicate"]
    limitations: set[str] = set()
    provisional: list[dict] = []
    for kill in kills:
        if any(kill[field] != predicate[f"{field}_equals"] for field in (
            "shots", "hits", "overshots",
        )):
            limitations.add("one_shot_kill_unavailable")
            continue
        temporal = [
            click
            for click in clicks
            if timing["minimum"] <= kill["time_ms"] - click["time_ms"] <= timing["maximum"]
        ]
        if len(temporal) != 1:
            limitations.add("temporal_candidate_not_unique")
            continue
        click = temporal[0]
        geometric: list[dict] = []
        for track in normalized_tracks:
            if track["identity_status"] != track_predicate["identity_status"]:
                continue
            nearest = _nearest_track_sample(track["samples"], click["time_ms"])
            if nearest is None:
                continue
            sample, sample_gap = nearest
            if sample_gap > track_predicate["max_sample_gap_ms"]:
                continue
            if sample["confidence"] < track_predicate["minimum_sample_confidence"]:
                continue
            effective_radius = sample["radius"] - track_predicate["hitbox_inset_px"]
            center_distance = hypot(sample["x"] - crosshair_x, sample["y"] - crosshair_y)
            if effective_radius > 0 and center_distance <= effective_radius:
                geometric.append({
                    "track_ref": track["track_ref"],
                    "sample_gap_ms": sample_gap,
                    "sample_confidence": sample["confidence"],
                    "center_distance_px": center_distance,
                    "effective_radius_px": effective_radius,
                })
        if len(geometric) != 1:
            limitations.add(
                "stable_target_identity_unavailable"
                if saw_unstable_identity and not any(
                    track["identity_status"] == "stable" for track in normalized_tracks
                )
                else "geometric_candidate_not_unique"
            )
            continue
        provisional.append({"click": click, "kill": kill, "track": geometric[0]})

    click_usage: dict[str, int] = {}
    for match in provisional:
        ref = match["click"]["event_ref"]
        click_usage[ref] = click_usage.get(ref, 0) + 1
    if any(count != 1 for count in click_usage.values()):
        return _unavailable(*limitations, "temporal_candidate_not_unique")
    if not provisional:
        if not kills:
            limitations.add("one_shot_kill_unavailable")
        return _unavailable(*limitations)

    events: list[dict] = [
        {
            "event_id": click["event_ref"],
            "event_kind": "shot",
            "start_ms": click["time_ms"],
            "end_ms": click["time_ms"],
            "actor_refs": [],
            "source_refs": [raw_input_source_ref],
            "confidence": 1.0,
            "attributes": {},
            "limitations": [],
        }
        for click in clicks
    ]
    associations: list[dict] = []
    used_click_refs: set[str] = set()
    for index, match in enumerate(provisional, 1):
        click = match["click"]
        kill = match["kill"]
        track = match["track"]
        used_click_refs.add(click["event_ref"])
        events.append({
            "event_id": kill["event_ref"],
            "event_kind": "kill",
            "start_ms": kill["time_ms"],
            "end_ms": kill["time_ms"],
            "actor_refs": [],
            "source_refs": [stats_source_ref],
            "confidence": 1.0,
            "attributes": {
                field: kill[field]
                for field in ("kill_index", "shots", "hits", "overshots")
            },
            "limitations": [],
        })
        latency = kill["time_ms"] - click["time_ms"]
        associations.append({
            "association_id": f"{analysis_ref}:association:one-shot-kill:{index}",
            "shot_event_ref": click["event_ref"],
            "outcome_event_ref": kill["event_ref"],
            "target_track_ref": track["track_ref"],
            "weapon_temporal_model": "hitscan",
            "association_kind": "validated_aligned",
            "source_refs": [raw_input_source_ref, stats_source_ref, visual_source_ref],
            "validation": {
                "schema_version": "outcome_association_validation.v1",
                "rule_ref": binding["rule_ref"],
                "rule_sha256": binding["rule_sha256"],
                "scenario_profile_ref": scenario_profile_ref,
                "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
                "raw_input_source_ref": raw_input_source_ref,
                "stats_source_ref": stats_source_ref,
                "visual_source_ref": visual_source_ref,
                "visual_quality_profile_ref": visual_quality_profile_ref,
                "click_time_ms": click["time_ms"],
                "outcome_time_ms": kill["time_ms"],
                "click_to_outcome_ms": latency,
                "temporal_candidate_count": 1,
                "geometric_candidate_count": 1,
                "stats_kill": {
                    field: kill[field]
                    for field in ("kill_index", "shots", "hits", "overshots")
                },
                "track_check": {
                    "identity_status": "stable",
                    "sample_gap_ms": track["sample_gap_ms"],
                    "sample_confidence": track["sample_confidence"],
                    "center_distance_px": track["center_distance_px"],
                    "effective_radius_px": track["effective_radius_px"],
                },
            },
            "confidence": 1.0,
            "availability": "available",
            "limitations": [],
        })
    for index, click in enumerate(
        (item for item in clicks if item["event_ref"] not in used_click_refs),
        1,
    ):
        associations.append({
            "association_id": f"{analysis_ref}:association:unavailable-click:{index}",
            "shot_event_ref": click["event_ref"],
            "outcome_event_ref": None,
            "target_track_ref": None,
            "weapon_temporal_model": "unknown",
            "association_kind": "inferred",
            "source_refs": [raw_input_source_ref],
            "validation": None,
            "confidence": 0.0,
            "availability": "unavailable",
            "limitations": ["outcome_association_unavailable"],
        })
    events.sort(key=lambda event: (event["start_ms"], event["event_id"]))
    bundle = validate_event_bundle_v2({
        "schema_version": "event_bundle.v2",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_association_rule_bindings": [binding],
        "outcome_associations": associations,
    })
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "available",
        "event_bundle": bundle,
        "limitations": sorted(limitations),
    }


__all__ = [
    "RULE_REGISTRY_SCHEMA_VERSION", "RESULT_SCHEMA_VERSION",
    "associate_one_shot_kills_v1", "load_outcome_association_rule_registry_v1",
    "validate_outcome_association_rule_registry_v1",
]
