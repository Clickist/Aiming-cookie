"""Versioned, hash-first ScenarioProfile registry resolution."""
from __future__ import annotations

import copy
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = "scenario_profile_registry.v1"
MANIFEST_SCHEMA_VERSION = "launch_scenario_manifest.v1"
RESOLUTION_SCHEMA_VERSION = "scenario_resolution.v1"
SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "scenarios"
REGISTRY_PATH = SCENARIOS_DIR / "registry.v1.json"
MANIFEST_PATH = SCENARIOS_DIR / "launch-manifest.v1.json"
MAX_REGISTRY_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ENTRIES = 512
MAX_LIST_LENGTH = 64
MAX_DEPTH = 8

_ENTRY_FIELDS = {
    "entry_id", "entry_version", "status", "scenario_hash", "display_name",
    "taxonomy_source", "reviewed_at", "source_refs", "supersedes", "aim_family",
    "subdomains", "target_motion", "allowed_analyzers",
    "allowed_metric_families", "classification_confidence", "limitations",
}
_MANIFEST_ENTRY_FIELDS = {
    "scenario_hash", "scenario_profile_ref", "fixture_ref", "review_source_ref",
    "reviewed_at", "family_gate_refs", "status",
}
_ENTRY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_HASH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_VERSIONED_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$")
_PATH_RE = re.compile(r"^(?:/|\\|~/|\.\.[/\\]|[A-Za-z]:[/\\]|file://)", re.I)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}"
)
_STATUSES = {"active", "superseded", "retired"}
_MANIFEST_STATUSES = {"pending_gate", "active", "retired"}
_TAXONOMY_SOURCES = {"reviewed_registry", "official_metadata", "unknown"}
_CLASSIFICATION_CONFIDENCES = {"confirmed", "candidate", "unknown"}
_AIM_FAMILIES = {
    "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
    "movement_aiming", "unknown",
}
_SUBDOMAINS = {"precision", "speed", "smooth", "reactive", "predictable", "control", "mixed"}
_MOTION_MODELS = {"static", "predictable", "reactive", "mixed", "unknown"}
_TARGET_COUNT_MODELS = {"single", "sequential", "concurrent", "unknown"}
_METRIC_FAMILIES = {
    "outcome", "input_kinematics", "static_clicking", "dynamic_clicking",
    "continuous_tracking", "target_switching",
}


class ScenarioProfileError(ValueError):
    """Scenario registry or launch manifest violates the frozen contract."""


def _required_text(value: Any, field: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioProfileError(f"{field} must be non-empty text")
    text = value.strip()
    if len(text) > max_length or any(ord(char) < 32 for char in text):
        raise ScenarioProfileError(f"{field} exceeds safe text bounds")
    if _PATH_RE.search(text) or _SECRET_RE.search(text):
        raise ScenarioProfileError(f"{field} contains unsafe text")
    return text


def _optional_display_name(value: Any) -> str | None:
    try:
        return _required_text(value, "display_name", max_length=240)
    except ScenarioProfileError:
        return None


def _scenario_hash(value: Any, field: str = "scenario_hash") -> str:
    text = _required_text(value, field, max_length=160)
    if not _HASH_RE.fullmatch(text):
        raise ScenarioProfileError(f"{field} is invalid")
    return text


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ScenarioProfileError(f"{field} must be a list")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        raise ScenarioProfileError(f"{field} has invalid length")
    result = [_required_text(item, field) for item in value]
    if len(set(result)) != len(result):
        raise ScenarioProfileError(f"{field} contains duplicates")
    return result


def _reject_unsafe_shape(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ScenarioProfileError("asset exceeds nesting limit")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ScenarioProfileError("asset keys must be strings")
            compact = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(marker in compact for marker in (
                "rawtrace", "payload", "credential", "apikey", "accesstoken",
                "refreshtoken", "password", "secret", "authorization",
            )) or compact.endswith("path"):
                raise ScenarioProfileError(f"asset contains unsafe fields: {key}")
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > MAX_ENTRIES:
            raise ScenarioProfileError("asset list exceeds size limit")
        for child in value:
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, str):
        if _PATH_RE.search(value) or _SECRET_RE.search(value):
            raise ScenarioProfileError("asset contains unsafe text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ScenarioProfileError("asset contains unsupported value")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ScenarioProfileError("asset contains non-finite number")


def _reviewed_at(value: Any, field: str) -> str:
    text = _required_text(value, field, max_length=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScenarioProfileError(f"{field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ScenarioProfileError(f"{field} must include a timezone")
    return text


def scenario_profile_ref(entry: Mapping[str, Any]) -> str:
    return f"scenario:{entry['entry_id']}@{entry['entry_version']}"


def _normalize_entry(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _ENTRY_FIELDS:
        raise ScenarioProfileError(f"entry[{index}] fields are invalid")
    entry_id = _required_text(raw["entry_id"], f"entry[{index}].entry_id", max_length=160)
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise ScenarioProfileError(f"entry[{index}].entry_id is invalid")
    entry_version = raw["entry_version"]
    if isinstance(entry_version, bool) or not isinstance(entry_version, int) or entry_version < 1:
        raise ScenarioProfileError(f"entry[{index}].entry_version is invalid")
    status = raw["status"]
    if status not in _STATUSES:
        raise ScenarioProfileError(f"entry[{index}].status is invalid")
    taxonomy_source = raw["taxonomy_source"]
    if taxonomy_source not in _TAXONOMY_SOURCES:
        raise ScenarioProfileError(f"entry[{index}].taxonomy_source is invalid")
    confidence = raw["classification_confidence"]
    if confidence not in _CLASSIFICATION_CONFIDENCES or confidence != "confirmed":
        raise ScenarioProfileError(f"entry[{index}].classification_confidence is invalid")
    aim_family = raw["aim_family"]
    if aim_family not in _AIM_FAMILIES - {"unknown"}:
        raise ScenarioProfileError(f"entry[{index}].aim_family is invalid")
    subdomains = _string_list(raw["subdomains"], f"entry[{index}].subdomains", allow_empty=False)
    if set(subdomains) - _SUBDOMAINS:
        raise ScenarioProfileError(f"entry[{index}].subdomains is invalid")
    target_motion = raw["target_motion"]
    if not isinstance(target_motion, Mapping) or set(target_motion) != {
        "model", "target_count_model",
    }:
        raise ScenarioProfileError(f"entry[{index}].target_motion fields are invalid")
    motion_model = target_motion["model"]
    target_count_model = target_motion["target_count_model"]
    if motion_model not in _MOTION_MODELS or target_count_model not in _TARGET_COUNT_MODELS:
        raise ScenarioProfileError(f"entry[{index}].target_motion enum is invalid")
    analyzers = _string_list(raw["allowed_analyzers"], f"entry[{index}].allowed_analyzers")
    metrics = _string_list(raw["allowed_metric_families"], f"entry[{index}].allowed_metric_families")
    if not all(_VERSIONED_TOKEN_RE.fullmatch(item) for item in analyzers) or set(metrics) - _METRIC_FAMILIES:
        raise ScenarioProfileError(f"entry[{index}] allowed enum is invalid")
    if not analyzers or not metrics:
        raise ScenarioProfileError(f"entry[{index}] allowed lists must be non-empty")
    return {
        "entry_id": entry_id,
        "entry_version": entry_version,
        "status": status,
        "scenario_hash": _scenario_hash(raw["scenario_hash"], f"entry[{index}].scenario_hash"),
        "display_name": _required_text(raw["display_name"], f"entry[{index}].display_name", max_length=240),
        "taxonomy_source": taxonomy_source,
        "reviewed_at": _reviewed_at(raw["reviewed_at"], f"entry[{index}].reviewed_at"),
        "source_refs": _string_list(raw["source_refs"], f"entry[{index}].source_refs", allow_empty=False),
        "supersedes": _string_list(raw["supersedes"], f"entry[{index}].supersedes"),
        "aim_family": aim_family,
        "subdomains": subdomains,
        "target_motion": {
            "model": motion_model,
            "target_count_model": target_count_model,
        },
        "allowed_analyzers": analyzers,
        "allowed_metric_families": metrics,
        "classification_confidence": confidence,
        "limitations": _string_list(raw["limitations"], f"entry[{index}].limitations", allow_empty=False),
    }


def validate_registry(raw: Any) -> dict[str, Any]:
    _reject_unsafe_shape(raw)
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "registry_version", "entries"}:
        raise ScenarioProfileError("registry fields are invalid")
    if raw["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ScenarioProfileError("unsupported registry schema_version")
    registry_version = _required_text(raw["registry_version"], "registry_version", max_length=80)
    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise ScenarioProfileError("entries must be a list")
    if len(entries_raw) > MAX_ENTRIES:
        raise ScenarioProfileError("entries exceeds size limit")
    entries = [_normalize_entry(entry, index) for index, entry in enumerate(entries_raw)]
    seen_versions: set[tuple[str, int]] = set()
    hash_entry_ids: dict[str, str] = {}
    entry_hashes: dict[str, str] = {}
    active_hashes: set[str] = set()
    for entry in entries:
        version_key = (entry["entry_id"], entry["entry_version"])
        if version_key in seen_versions:
            raise ScenarioProfileError("duplicate entry version")
        seen_versions.add(version_key)
        previous_entry_id = hash_entry_ids.setdefault(entry["scenario_hash"], entry["entry_id"])
        if previous_entry_id != entry["entry_id"]:
            raise ScenarioProfileError("ambiguous hash across entry ids")
        previous_hash = entry_hashes.setdefault(entry["entry_id"], entry["scenario_hash"])
        if previous_hash != entry["scenario_hash"]:
            raise ScenarioProfileError("entry history must keep one scenario hash")
        if entry["status"] == "active":
            if entry["scenario_hash"] in active_hashes:
                raise ScenarioProfileError("multiple active versions for hash")
            active_hashes.add(entry["scenario_hash"])
    profiles = {scenario_profile_ref(entry): entry for entry in entries}
    for entry in entries:
        for superseded_ref in entry["supersedes"]:
            previous = profiles.get(superseded_ref)
            if (
                previous is None
                or previous["entry_id"] != entry["entry_id"]
                or previous["scenario_hash"] != entry["scenario_hash"]
                or previous["entry_version"] >= entry["entry_version"]
            ):
                raise ScenarioProfileError("supersedes must reference an earlier profile version")
    return copy.deepcopy({
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_version": registry_version,
        "entries": entries,
    })


def _normalize_manifest_entry(raw: Any, index: int, profiles: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _MANIFEST_ENTRY_FIELDS:
        raise ScenarioProfileError(f"manifest entry[{index}] fields are invalid")
    scenario_hash = _scenario_hash(raw["scenario_hash"], f"manifest entry[{index}].scenario_hash")
    profile_ref = _required_text(raw["scenario_profile_ref"], f"manifest entry[{index}].scenario_profile_ref", max_length=200)
    profile = profiles.get(profile_ref)
    if profile is None or profile["scenario_hash"] != scenario_hash:
        raise ScenarioProfileError(f"manifest entry[{index}] profile ref is not an exact registry profile")
    status = raw["status"]
    if status not in _MANIFEST_STATUSES:
        raise ScenarioProfileError(f"manifest entry[{index}].status is invalid")
    if status in {"active", "pending_gate"} and profile["status"] != "active":
        raise ScenarioProfileError(f"manifest entry[{index}] requires an active profile")
    if status in {"active", "pending_gate"} and profile["taxonomy_source"] == "unknown":
        raise ScenarioProfileError(f"manifest entry[{index}] requires reviewed taxonomy")
    return {
        "scenario_hash": scenario_hash,
        "scenario_profile_ref": profile_ref,
        "fixture_ref": _required_text(raw["fixture_ref"], f"manifest entry[{index}].fixture_ref", max_length=240),
        "review_source_ref": _required_text(raw["review_source_ref"], f"manifest entry[{index}].review_source_ref", max_length=240),
        "reviewed_at": _reviewed_at(raw["reviewed_at"], f"manifest entry[{index}].reviewed_at"),
        "family_gate_refs": _string_list(raw["family_gate_refs"], f"manifest entry[{index}].family_gate_refs", allow_empty=False),
        "status": status,
    }


def validate_launch_manifest(raw: Any, *, registry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _reject_unsafe_shape(raw)
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "manifest_version", "entries"}:
        raise ScenarioProfileError("launch manifest fields are invalid")
    if raw["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ScenarioProfileError("unsupported launch manifest schema_version")
    manifest_version = _required_text(raw["manifest_version"], "manifest_version", max_length=80)
    data = validate_registry(registry) if registry is not None else load_registry()
    profiles = {scenario_profile_ref(entry): entry for entry in data["entries"]}
    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise ScenarioProfileError("manifest entries must be a list")
    if len(entries_raw) > MAX_ENTRIES:
        raise ScenarioProfileError("manifest entries exceeds size limit")
    entries = [_normalize_manifest_entry(entry, index, profiles) for index, entry in enumerate(entries_raw)]
    if len({entry["scenario_hash"] for entry in entries}) != len(entries):
        raise ScenarioProfileError("manifest contains duplicate scenario hashes")
    return copy.deepcopy({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_version": manifest_version,
        "entries": entries,
    })


def _load_json(path: str | os.PathLike[str], *, max_bytes: int, label: str) -> Any:
    asset_path = Path(path)
    try:
        raw_bytes = asset_path.read_bytes()
    except OSError as exc:
        raise ScenarioProfileError(f"{label} is unavailable") from exc
    if len(raw_bytes) > max_bytes:
        raise ScenarioProfileError(f"{label} exceeds size limit")
    try:
        return json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioProfileError(f"{label} is invalid JSON") from exc


def load_registry(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return validate_registry(_load_json(path or REGISTRY_PATH, max_bytes=MAX_REGISTRY_BYTES, label="scenario registry"))


def load_launch_manifest(
    path: str | os.PathLike[str] | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = validate_registry(registry) if registry is not None else load_registry()
    raw = _load_json(path or MANIFEST_PATH, max_bytes=MAX_MANIFEST_BYTES, label="launch manifest")
    return validate_launch_manifest(raw, registry=data)


def _outcome_only_resolution(
    *,
    scenario_hash: str | None,
    display_name: str | None,
    registry_version: str,
    manifest_version: str,
    classification_source: str = "unknown",
    classification_confidence: str = "unknown",
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "scenario_hash": scenario_hash,
        "display_name": display_name,
        "registry_version": registry_version,
        "manifest_version": manifest_version,
        "scenario_profile_ref": None,
        "classification_source": classification_source,
        "classification_confidence": classification_confidence,
        "profile_status": "unknown",
        "reviewed_at": None,
        "source_refs": [],
        "supersedes": [],
        "manifest_status": "unlisted",
        "fixture_ref": None,
        "review_source_ref": None,
        "manifest_reviewed_at": None,
        "family_gate_refs": [],
        "aim_family": "unknown",
        "subdomains": [],
        "target_motion": {
            "model": "unknown",
            "target_count_model": "unknown",
        },
        "allowed_analyzers": [],
        "allowed_metric_families": [],
        "claim_ceiling": "outcome_only",
        "family_analyzer_dispatch": "none",
        "limitations": limitations or ["No reviewed exact scenario hash is available."],
    }


def resolve_scenario_profile(
    scenario_hash: str | None,
    display_name: str | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve only reviewed exact hashes; display names remain non-dispatching hints."""
    data = validate_registry(registry) if registry is not None else load_registry()
    launch_manifest = (
        validate_launch_manifest(manifest, registry=data)
        if manifest is not None
        else load_launch_manifest(registry=data)
    )
    safe_hash: str | None
    try:
        safe_hash = _scenario_hash(scenario_hash) if scenario_hash is not None else None
    except ScenarioProfileError:
        safe_hash = None
    safe_display_name = _optional_display_name(display_name) if display_name is not None else None
    profiles_by_hash: dict[str, dict[str, Any]] = {}
    for entry in sorted(data["entries"], key=lambda item: item["entry_version"]):
        existing = profiles_by_hash.get(entry["scenario_hash"])
        if (
            existing is None
            or entry["status"] == "active"
            or (
                existing["status"] != "active"
                and entry["entry_version"] > existing["entry_version"]
            )
        ):
            profiles_by_hash[entry["scenario_hash"]] = entry
    profile = profiles_by_hash.get(safe_hash) if safe_hash else None
    if profile is None:
        candidates = [
            entry for entry in data["entries"]
            if entry["status"] == "active" and safe_display_name
            and entry["display_name"].casefold() == safe_display_name.casefold()
        ]
        if len(candidates) == 1:
            return _outcome_only_resolution(
                scenario_hash=safe_hash,
                display_name=safe_display_name,
                registry_version=data["registry_version"],
                manifest_version=launch_manifest["manifest_version"],
                classification_source="name_heuristic",
                classification_confidence="candidate",
                limitations=["Display-name matching is only a review candidate, not a scenario identity."],
            )
        return _outcome_only_resolution(
            scenario_hash=safe_hash,
            display_name=safe_display_name,
            registry_version=data["registry_version"],
            manifest_version=launch_manifest["manifest_version"],
        )

    profile_ref = scenario_profile_ref(profile)
    manifests_by_hash = {entry["scenario_hash"]: entry for entry in launch_manifest["entries"]}
    manifest_entry = manifests_by_hash.get(profile["scenario_hash"])
    manifest_status = manifest_entry["status"] if manifest_entry else "unlisted"
    dispatch_allowed = profile["status"] == "active" and manifest_status == "active"
    limitations = list(profile["limitations"])
    if not dispatch_allowed:
        limitations.append("The launch manifest is not active; family-specific analysis is unavailable.")
    return {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "scenario_hash": profile["scenario_hash"],
        "display_name": safe_display_name or profile["display_name"],
        "registry_version": data["registry_version"],
        "manifest_version": launch_manifest["manifest_version"],
        "scenario_profile_ref": profile_ref,
        "classification_source": profile["taxonomy_source"],
        "classification_confidence": "confirmed",
        "profile_status": profile["status"],
        "reviewed_at": profile["reviewed_at"],
        "source_refs": list(profile["source_refs"]),
        "supersedes": list(profile["supersedes"]),
        "manifest_status": manifest_status,
        "fixture_ref": manifest_entry["fixture_ref"] if manifest_entry else None,
        "review_source_ref": (
            manifest_entry["review_source_ref"] if manifest_entry else None
        ),
        "manifest_reviewed_at": (
            manifest_entry["reviewed_at"] if manifest_entry else None
        ),
        "family_gate_refs": (
            list(manifest_entry["family_gate_refs"]) if manifest_entry else []
        ),
        "aim_family": profile["aim_family"],
        "subdomains": list(profile["subdomains"]),
        "target_motion": dict(profile["target_motion"]),
        "allowed_analyzers": list(profile["allowed_analyzers"]),
        "allowed_metric_families": list(profile["allowed_metric_families"]),
        "claim_ceiling": "family_specific" if dispatch_allowed else "outcome_only",
        "family_analyzer_dispatch": "allowed" if dispatch_allowed else "none",
        "limitations": limitations,
    }


__all__ = [
    "MANIFEST_PATH", "MANIFEST_SCHEMA_VERSION", "REGISTRY_PATH", "REGISTRY_SCHEMA_VERSION",
    "RESOLUTION_SCHEMA_VERSION", "ScenarioProfileError", "load_launch_manifest", "load_registry",
    "resolve_scenario_profile", "scenario_profile_ref", "validate_launch_manifest", "validate_registry",
]
