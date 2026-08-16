"""Versioned, hash-first ScenarioProfile registry resolution."""
from __future__ import annotations

import copy
import hashlib
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
_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
SCENARIOS_DIR = (
    Path(_RESOURCE_ROOT) / "knowledge" / "scenarios"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[1] / "knowledge" / "scenarios"
)
REGISTRY_PATH = SCENARIOS_DIR / "registry.v1.json"
MANIFEST_PATH = SCENARIOS_DIR / "launch-manifest.v1.json"
MAX_REGISTRY_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ENTRIES = 512
MAX_LIST_LENGTH = 64
MAX_DEPTH = 8
MAX_LOCAL_SCENARIO_DEFINITION_BYTES = 2 * 1024 * 1024
MAX_LOCAL_SCENARIO_DEFINITION_LINES = 65_536

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
# Families with a baseline analyzer pipeline; any scenario resolving to one of
# these dispatches at least its input-kinematics baseline analysis.
_FAMILY_PIPELINE_FAMILIES = {
    "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
}
_FAMILY_BASELINE_LIMITATIONS = [
    "exact_visual_profile_unavailable",
    "target_relative_facts_unavailable",
    "outcome_association_unavailable",
    "scenario_prescription_unavailable",
]
# Challenge-shape v1: the winning discriminator is the FIRE MODE read from
# the Raw Input button bitmask (state samples, not edges). Real runs: tapping
# classes spend 16–27 held samples per kill, sustained-fire tracking 426–445,
# zero-kill pure tracking 12k–17k held samples. Kill density alone was refuted
# (a 34–39 kill tracking map overlaps the 43-kill clicking floor), so without
# raw input only the unambiguous ≤6-kill corner stays decidable.
CHALLENGE_SHAPE_SCHEMA_VERSION = "scenario_challenge_shape.v1"
CHALLENGE_SHAPE_MIN_DURATION_MS = 15_000
CHALLENGE_SHAPE_TRACKING_MIN_BUTTON_SAMPLES_PER_KILL = 100.0
CHALLENGE_SHAPE_CLICKING_MAX_BUTTON_SAMPLES_PER_KILL = 50.0
CHALLENGE_SHAPE_TRACKING_MIN_ZERO_KILL_BUTTON_SAMPLES = 500
CHALLENGE_SHAPE_FALLBACK_MAX_TRACKING_KILLS = 6


def _normalized_scenario_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _definition_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _definition_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _definition_sections(data: bytes) -> tuple[dict[str, str], dict[str, list[dict[str, str]]]] | None:
    if not data or len(data) > MAX_LOCAL_SCENARIO_DEFINITION_BYTES or b"\0" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if len(lines) > MAX_LOCAL_SCENARIO_DEFINITION_LINES:
        return None
    root: dict[str, str] = {}
    sections: dict[str, list[dict[str, str]]] = {}
    current: dict[str, str] | None = None
    current_kind: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_kind = line[1:-1].strip().casefold()
            if not current_kind or len(current_kind) > 80:
                return None
            current = {}
            sections.setdefault(current_kind, []).append(current)
            if len(sections[current_kind]) > MAX_LIST_LENGTH:
                return None
            continue
        if "=" not in line:
            if current is not None:
                continue
            return None
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or len(key) > 80 or len(value) > 2_000:
            return None
        target = current if current is not None else root
        if key in target:
            return None
        target[key] = value
    return root, sections


def _named_definition_section(
    sections: Mapping[str, Sequence[Mapping[str, str]]],
    kind: str,
    name: str,
) -> Mapping[str, str] | None:
    matches = [
        section for section in sections.get(kind.casefold(), ())
        if _normalized_scenario_name(section.get("Name", "")) == _normalized_scenario_name(name)
    ]
    return matches[0] if len(matches) == 1 else None


def parse_local_scenario_behavior_descriptor(
    data: bytes,
    *,
    expected_display_name: str,
) -> dict[str, Any] | None:
    """Extract a bounded, path-free behavior descriptor from a local KovaaK definition."""
    expected = _optional_display_name(expected_display_name)
    parsed = _definition_sections(data)
    if expected is None or parsed is None:
        return None
    root, sections = parsed
    if _normalized_scenario_name(root.get("Name", "")) != _normalized_scenario_name(expected):
        return None
    bots = [item.strip() for item in root.get("AddedBots", "").split(";") if item.strip()]
    if not 2 <= len(bots) <= MAX_LIST_LENGTH:
        return None
    bot_profiles = {item.rsplit(".", 1)[0] for item in bots}
    if len(bot_profiles) != 1:
        return None
    bot_profile = _named_definition_section(sections, "bot profile", bot_profiles.pop())
    if bot_profile is None:
        return None
    character_name = bot_profile.get("CharacterProfile")
    dodge_name = next((item.strip() for item in bot_profile.get("DodgeProfileNames", "").split(";") if item.strip()), None)
    character = (
        _named_definition_section(sections, "character profile", character_name)
        if character_name else None
    )
    dodge = _named_definition_section(sections, "dodge profile", dodge_name) if dodge_name else None
    max_speed = _definition_number(character.get("MaxSpeed")) if character else None
    if character is None or max_speed is None or max_speed < 0:
        return None
    axes = []
    if dodge is not None and _definition_bool(dodge.get("ToggleLeftRight")) is True:
        axes.append("horizontal")
    if dodge is not None and _definition_bool(dodge.get("ToggleForwardBack")) is True:
        axes.append("depth")
    reactive = max_speed > 0 and bool(axes)
    if max_speed > 0 and not reactive:
        return None
    player_name = root.get("PlayerCharacters")
    player = (
        _named_definition_section(sections, "character profile", player_name)
        if player_name else None
    )
    weapon_name = (
        next((item.strip() for item in player.get("WeaponProfileNames", "").split(";") if item.strip()), None)
        if player is not None else None
    )
    weapon = _named_definition_section(sections, "weapon profile", weapon_name) if weapon_name else None
    shots = _definition_number(weapon.get("ShotsPerClick")) if weapon else None
    damage = _definition_number(weapon.get("DamagePerShot")) if weapon else None
    if not (
        weapon is not None
        and weapon.get("Type", "").casefold() == "hitscan"
        and weapon.get("Category", "").casefold() == "semiauto"
        and shots == 1
        and damage is not None
        and damage > 0
    ):
        return None
    return {
        "schema_version": "scenario_behavior_descriptor.v1",
        "display_name": expected,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "bot_count": len(bots),
        "reactive_bot_count": len(bots) if reactive else 0,
        "dodge_axes": axes if reactive else [],
        "weapon": {
            "delivery": "hitscan",
            "fire_mode": "semi_auto",
            "shots_per_click": 1,
            "damage_per_shot": damage,
        },
    }


def _valid_local_scenario_behavior_descriptor(
    value: object,
    *,
    display_name: str | None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "display_name", "source_sha256", "bot_count", "reactive_bot_count",
        "dodge_axes", "weapon",
    } or value.get("schema_version") != "scenario_behavior_descriptor.v1":
        return None
    name = _optional_display_name(value.get("display_name"))
    digest = value.get("source_sha256")
    if (
        name is None
        or display_name is None
        or _normalized_scenario_name(name) != _normalized_scenario_name(display_name)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        return None
    counts = (value.get("bot_count"), value.get("reactive_bot_count"))
    if not isinstance(counts[0], int) or isinstance(counts[0], bool) or not 1 <= counts[0] <= MAX_LIST_LENGTH:
        return None
    if not isinstance(counts[1], int) or isinstance(counts[1], bool) or not 0 <= counts[1] <= counts[0]:
        return None
    axes = value.get("dodge_axes")
    weapon = value.get("weapon")
    if (
        not isinstance(axes, list)
        or (bool(axes) != bool(counts[1]))
        or set(axes) - {"horizontal", "depth"}
        or len(set(axes)) != len(axes)
        or not isinstance(weapon, Mapping)
        or set(weapon) != {"delivery", "fire_mode", "shots_per_click", "damage_per_shot"}
        or weapon.get("delivery") != "hitscan"
        or weapon.get("fire_mode") != "semi_auto"
        or weapon.get("shots_per_click") != 1
        or not isinstance(weapon.get("damage_per_shot"), (int, float))
        or isinstance(weapon.get("damage_per_shot"), bool)
        or not math.isfinite(float(weapon["damage_per_shot"]))
        or float(weapon["damage_per_shot"]) <= 0
    ):
        return None
    return copy.deepcopy(dict(value))


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


def active_scenario_profile_refs(
    *,
    registry: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> set[str]:
    """Return exact profiles enabled by both the Registry and launch manifest."""
    data = validate_registry(registry) if registry is not None else load_registry()
    launch_manifest = (
        validate_launch_manifest(manifest, registry=data)
        if manifest is not None
        else load_launch_manifest(registry=data)
    )
    registry_refs = {
        scenario_profile_ref(entry)
        for entry in data["entries"]
        if entry["status"] == "active"
    }
    manifest_refs = {
        entry["scenario_profile_ref"]
        for entry in launch_manifest["entries"]
        if entry["status"] == "active"
    }
    return registry_refs & manifest_refs


def _family_baseline_resolution(
    *,
    scenario_hash: str | None,
    display_name: str | None,
    registry_version: str,
    manifest_version: str,
    aim_family: str,
    classification_source: str,
    classification_confidence: str,
    target_motion: Mapping[str, str],
    subdomains: Sequence[str] = (),
    limitations: Sequence[str] = (),
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
        "aim_family": aim_family,
        "subdomains": list(subdomains),
        "target_motion": dict(target_motion),
        "allowed_analyzers": [f"{aim_family}.baseline.v1"],
        "allowed_metric_families": ["outcome", "input_kinematics"],
        "claim_ceiling": "descriptive_only",
        "family_analyzer_dispatch": "allowed",
        "limitations": list(limitations),
    }


def _family_from_display_name(display_name: str) -> str | None:
    """Best-effort family candidate from scenario-name keywords.

    The result is a candidate only: it routes the family baseline pipeline but
    never establishes scenario identity or any visual claim.
    """
    normalized = _normalized_scenario_name(display_name)
    if not normalized:
        return None
    if "strafe" in normalized or "track" in normalized:
        return "continuous_tracking"
    if "switch" in normalized:
        return "target_switching"
    if "pasu" in normalized or ("reload" in normalized and "no reload" not in normalized):
        return "dynamic_clicking"
    return "static_clicking"


def _name_candidate_family(
    entries: Sequence[Mapping[str, Any]],
    safe_display_name: str | None,
) -> str:
    """Family for a name-only candidate: unique reviewed name match, else keywords."""
    if safe_display_name is not None:
        candidates = [
            entry for entry in entries
            if entry["status"] == "active"
            and entry["display_name"].casefold() == safe_display_name.casefold()
        ]
        if (
            len(candidates) == 1
            and candidates[0]["aim_family"] in _FAMILY_PIPELINE_FAMILIES
        ):
            return candidates[0]["aim_family"]
    return (_family_from_display_name(safe_display_name)
            if safe_display_name is not None else None) or "static_clicking"


def _valid_challenge_shape_descriptor(value: object) -> dict[str, Any] | None:
    """Accept only bounded, path-free Stats/Raw-derived shape facts.

    ``button_samples_held`` is optional: present when the run has a Raw trace
    (canonical per-ms samples with the fire button held), absent for
    historical runs.
    """
    if (
        not isinstance(value, Mapping)
        or set(value) - {"schema_version", "kills", "duration_ms", "button_samples_held"}
        or value.get("schema_version") != CHALLENGE_SHAPE_SCHEMA_VERSION
    ):
        return None
    kills = value.get("kills")
    duration_ms = value.get("duration_ms")
    button_samples_held = value.get("button_samples_held")
    if (
        not isinstance(kills, int) or isinstance(kills, bool) or not 0 <= kills <= 1_000_000
        or not isinstance(duration_ms, int) or isinstance(duration_ms, bool)
        or not 1 <= duration_ms <= 86_400_000
        or (
            button_samples_held is not None
            and (
                not isinstance(button_samples_held, int)
                or isinstance(button_samples_held, bool)
                or not 0 <= button_samples_held <= 100_000_000
            )
        )
    ):
        return None
    shape = {
        "schema_version": CHALLENGE_SHAPE_SCHEMA_VERSION,
        "kills": kills,
        "duration_ms": duration_ms,
    }
    if button_samples_held is not None:
        shape["button_samples_held"] = button_samples_held
    return shape


def classify_challenge_shape_v1(
    kills: int,
    duration_ms: int,
    button_samples_held: int | None = None,
) -> dict[str, Any] | None:
    """Fire-mode shape verdict; None in the undecided band or short runs.

    With Raw input the verdict reads the button bitmask: sustained fire (many
    held samples per kill, or heavy fire with zero kills) marks tracking;
    tapping marks clicking. Without Raw input only the unambiguous ≤6-kill
    corner is decidable (kill density was refuted as a separator).
    Verdicts are statistical candidates only — they route the family baseline
    pipeline, never visual claims or scenario identity.
    """
    if duration_ms < CHALLENGE_SHAPE_MIN_DURATION_MS:
        return None
    if button_samples_held is not None:
        if kills == 0:
            if button_samples_held < CHALLENGE_SHAPE_TRACKING_MIN_ZERO_KILL_BUTTON_SAMPLES:
                return None
            return {
                "shape_class": "tracking_candidate",
                "basis": "zero_kill_sustained_fire",
                "kills": kills,
                "duration_ms": duration_ms,
                "button_samples_held": button_samples_held,
                "button_samples_per_kill": None,
            }
        button_samples_per_kill = button_samples_held / kills
        if button_samples_per_kill > CHALLENGE_SHAPE_TRACKING_MIN_BUTTON_SAMPLES_PER_KILL:
            shape_class = "tracking_candidate"
            basis = "fire_mode_hold"
        elif button_samples_per_kill < CHALLENGE_SHAPE_CLICKING_MAX_BUTTON_SAMPLES_PER_KILL:
            shape_class = "clicking_candidate"
            basis = "fire_mode_tap"
        else:
            return None
        return {
            "shape_class": shape_class,
            "basis": basis,
            "kills": kills,
            "duration_ms": duration_ms,
            "button_samples_held": button_samples_held,
            "button_samples_per_kill": round(button_samples_per_kill, 2),
        }
    if kills <= CHALLENGE_SHAPE_FALLBACK_MAX_TRACKING_KILLS:
        return {
            "shape_class": "tracking_candidate",
            "basis": "kill_density_fallback",
            "kills": kills,
            "duration_ms": duration_ms,
            "button_samples_held": None,
            "button_samples_per_kill": None,
        }
    return None


def _challenge_shape_summary_limitation(verdict: Mapping[str, Any]) -> str:
    """Machine-readable verdict basis; the frontend renders it in Chinese."""
    held = verdict.get("button_samples_held")
    if held is None:
        return (
            f"challenge_shape_kill_density_kills_{verdict['kills']}"
            f"_duration_ms_{verdict['duration_ms']}"
        )
    per_kill = verdict.get("button_samples_per_kill")
    per_kill_text = "inf" if per_kill is None else per_kill
    return (
        f"challenge_shape_fire_mode_kills_{verdict['kills']}"
        f"_button_samples_held_{held}"
        f"_button_samples_per_kill_{per_kill_text}"
    )


def _challenge_shape_resolution(
    *,
    scenario_hash: str | None,
    display_name: str | None,
    registry_version: str,
    manifest_version: str,
    aim_family: str,
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    return _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=display_name,
        registry_version=registry_version,
        manifest_version=manifest_version,
        aim_family=aim_family,
        classification_source="challenge_shape",
        classification_confidence="candidate",
        target_motion={"model": "unknown", "target_count_model": "unknown"},
        limitations=[
            "challenge_shape_is_a_statistical_candidate_not_an_identity",
            _challenge_shape_summary_limitation(verdict),
            *_FAMILY_BASELINE_LIMITATIONS,
        ],
    )


def _name_candidate_resolution(
    *,
    scenario_hash: str | None,
    display_name: str | None,
    registry_version: str,
    manifest_version: str,
    aim_family: str,
) -> dict[str, Any]:
    return _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=display_name,
        registry_version=registry_version,
        manifest_version=manifest_version,
        aim_family=aim_family,
        classification_source="name_heuristic",
        classification_confidence="candidate",
        target_motion={"model": "unknown", "target_count_model": "unknown"},
        limitations=[
            "scenario_name_is_a_candidate_not_an_identity",
            *_FAMILY_BASELINE_LIMITATIONS,
        ],
    )


def _family_default_resolution(
    *,
    scenario_hash: str | None,
    display_name: str | None,
    registry_version: str,
    manifest_version: str,
) -> dict[str, Any]:
    return _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=display_name,
        registry_version=registry_version,
        manifest_version=manifest_version,
        aim_family="static_clicking",
        classification_source="family_default",
        classification_confidence="unknown",
        target_motion={"model": "unknown", "target_count_model": "unknown"},
        limitations=[
            "scenario_family_unresolved",
            *_FAMILY_BASELINE_LIMITATIONS,
        ],
    )


def _dynamic_baseline_resolution(
    *,
    scenario_hash: str | None,
    display_name: str,
    registry_version: str,
    manifest_version: str,
    target_count_model: str,
) -> dict[str, Any]:
    return _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=display_name,
        registry_version=registry_version,
        manifest_version=manifest_version,
        aim_family="dynamic_clicking",
        classification_source="local_scenario_definition",
        classification_confidence="confirmed",
        subdomains=["reactive", "control"],
        target_motion={"model": "reactive", "target_count_model": target_count_model},
        limitations=_FAMILY_BASELINE_LIMITATIONS,
    )


def _static_baseline_resolution(
    *,
    scenario_hash: str | None,
    display_name: str,
    registry_version: str,
    manifest_version: str,
    target_count_model: str,
) -> dict[str, Any]:
    return _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=display_name,
        registry_version=registry_version,
        manifest_version=manifest_version,
        aim_family="static_clicking",
        classification_source="local_scenario_definition",
        classification_confidence="confirmed",
        subdomains=["precision", "control"],
        target_motion={"model": "static", "target_count_model": target_count_model},
        limitations=_FAMILY_BASELINE_LIMITATIONS,
    )


def resolve_scenario_profile(
    scenario_hash: str | None,
    display_name: str | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
    behavior_descriptor: Mapping[str, Any] | None = None,
    challenge_shape: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Identify the aim family and dispatch its pipeline for any scenario.

    Resolution levels: exact reviewed hash (fast lane to the calibrated full
    analysis), local `.sce` structure, the Stats-derived challenge shape, a
    display-name candidate, or the unresolved default. Unreviewed identity only
    withholds visual and target-relative claims — it never blocks the family
    baseline pipeline.
    """
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
        descriptor = _valid_local_scenario_behavior_descriptor(
            behavior_descriptor,
            display_name=safe_display_name,
        )
        if descriptor is not None:
            baseline = (
                _dynamic_baseline_resolution
                if descriptor["reactive_bot_count"] == descriptor["bot_count"]
                else _static_baseline_resolution
            )
            return baseline(
                scenario_hash=safe_hash,
                display_name=safe_display_name,
                registry_version=data["registry_version"],
                manifest_version=launch_manifest["manifest_version"],
                target_count_model=(
                    "single" if descriptor["bot_count"] == 1 else "concurrent"
                ),
            )
        shape = _valid_challenge_shape_descriptor(challenge_shape)
        shape_verdict = (
            classify_challenge_shape_v1(
                shape["kills"],
                shape["duration_ms"],
                shape.get("button_samples_held"),
            )
            if shape is not None
            else None
        )
        if shape_verdict is not None:
            if shape_verdict["shape_class"] == "tracking_candidate":
                shape_family = "continuous_tracking"
            else:
                # The shape confirms a clicking class; the name only refines
                # which clicking family. A tracking name contradicts the
                # measured shape, so it degrades to the static default.
                shape_family = _name_candidate_family(data["entries"], safe_display_name)
                if shape_family == "continuous_tracking":
                    shape_family = "static_clicking"
            return _challenge_shape_resolution(
                scenario_hash=safe_hash,
                display_name=safe_display_name,
                registry_version=data["registry_version"],
                manifest_version=launch_manifest["manifest_version"],
                aim_family=shape_family,
                verdict=shape_verdict,
            )
        if safe_display_name is not None:
            return _name_candidate_resolution(
                scenario_hash=safe_hash,
                display_name=safe_display_name,
                registry_version=data["registry_version"],
                manifest_version=launch_manifest["manifest_version"],
                aim_family=_name_candidate_family(data["entries"], safe_display_name),
            )
        return _family_default_resolution(
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
    if dispatch_allowed:
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
            "claim_ceiling": "family_specific",
            "family_analyzer_dispatch": "allowed",
            "limitations": limitations,
        }
    # The manifest gate withholds the calibrated visual/full analysis only; the
    # reviewed family still routes the baseline input-kinematics pipeline.
    if profile["aim_family"] in _FAMILY_PIPELINE_FAMILIES:
        limitations.append("exact_manifest_gate_inactive_visual_claims_unavailable")
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
            "allowed_analyzers": [f"{profile['aim_family']}.baseline.v1"],
            "allowed_metric_families": ["outcome", "input_kinematics"],
            "claim_ceiling": "descriptive_only",
            "family_analyzer_dispatch": "allowed",
            "limitations": limitations,
        }
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
        "claim_ceiling": "outcome_only",
        "family_analyzer_dispatch": "none",
        "limitations": limitations,
    }


__all__ = [
    "CHALLENGE_SHAPE_SCHEMA_VERSION", "MANIFEST_PATH", "MANIFEST_SCHEMA_VERSION", "REGISTRY_PATH", "REGISTRY_SCHEMA_VERSION",
    "RESOLUTION_SCHEMA_VERSION", "ScenarioProfileError", "active_scenario_profile_refs", "classify_challenge_shape_v1", "load_launch_manifest", "load_registry",
    "parse_local_scenario_behavior_descriptor", "resolve_scenario_profile", "scenario_profile_ref", "validate_launch_manifest", "validate_registry",
]
