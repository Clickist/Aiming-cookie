"""Canonical, versioned Coach knowledge registry and deterministic retrieval."""
from __future__ import annotations

import copy
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = "coach_knowledge_registry.v1"
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "coach" / "registry.v1.json"
MAX_RESULTS = 3
MAX_REGISTRY_BYTES = 512 * 1024
MAX_ENTRIES = 512
MAX_TEXT_LENGTH = 4_000
MAX_LIST_LENGTH = 64
MAX_DEPTH = 8

_STATUSES = {"active", "retired"}
_CATEGORIES = {
    "metric_definition", "kinematic_mechanism", "diagnostic_scope", "research",
    "training_cue", "prescription_verification", "practice_structure",
    "body_tension_hypothesis", "settings_experiment", "limitation_counterevidence",
}
_SOURCE_LEVELS = {
    "product_contract", "academic_peer_reviewed", "community_consensus",
    "personal_experience_unverified", "experimental",
}
_CLAIM_LEVELS = {
    "deterministic_rule", "research_supported", "community_consensus", "experimental",
}
_SUPPORTED_USES = {
    "definition", "mechanism", "diagnostic_scope", "research_context",
    "training_cue", "practice_structure", "candidate_hypothesis", "verification",
}
_ENTRY_FIELDS = {
    "entry_id", "entry_version", "status", "category", "topics", "signals",
    "metric_refs", "text", "sources", "max_claim_level", "limitations",
    "counterevidence", "supported_uses",
}
_ENTRY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,159}$")
_PATH_RE = re.compile(r"^(?:/|\\|~/|\.\.[/\\]|[A-Za-z]:[/\\]|file://)", re.I)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}"
)


class KnowledgeRegistryError(ValueError):
    """Registry asset or query violates the frozen product contract."""


def _required_text(value: Any, field: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeRegistryError(f"{field} must be non-empty text")
    text = value.strip()
    if len(text) > max_length:
        raise KnowledgeRegistryError(f"{field} exceeds length limit")
    if _PATH_RE.search(text) or _SECRET_RE.search(text):
        raise KnowledgeRegistryError(f"{field} contains unsafe text")
    return text


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise KnowledgeRegistryError(f"{field} must be a list")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        raise KnowledgeRegistryError(f"{field} has invalid length")
    result = [_required_text(item, field, max_length=500) for item in value]
    if len(set(result)) != len(result):
        raise KnowledgeRegistryError(f"{field} contains duplicates")
    return result


def _reject_unsafe_shape(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise KnowledgeRegistryError("registry exceeds nesting limit")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise KnowledgeRegistryError("registry keys must be strings")
            compact = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(marker in compact for marker in (
                "rawtrace", "payload", "credential", "apikey", "accesstoken",
                "refreshtoken", "password", "secret", "authorization",
            )) or compact.endswith("path"):
                raise KnowledgeRegistryError(f"registry contains unsafe fields: {key}")
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > MAX_ENTRIES:
            raise KnowledgeRegistryError("registry list exceeds size limit")
        for child in value:
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, str):
        if _PATH_RE.search(value) or _SECRET_RE.search(value):
            raise KnowledgeRegistryError("registry contains unsafe text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise KnowledgeRegistryError("registry contains unsupported value")
    elif isinstance(value, float) and not math.isfinite(value):
        raise KnowledgeRegistryError("registry contains non-finite number")


def _normalize_entry(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _ENTRY_FIELDS:
        raise KnowledgeRegistryError(f"entry[{index}] fields are invalid")
    entry_id = _required_text(raw["entry_id"], f"entry[{index}].entry_id", max_length=160)
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise KnowledgeRegistryError(f"entry[{index}].entry_id is invalid")
    version = raw["entry_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise KnowledgeRegistryError(f"entry[{index}].entry_version is invalid")
    status = raw["status"]
    if status not in _STATUSES:
        raise KnowledgeRegistryError(f"entry[{index}].status is invalid")
    category = raw["category"]
    if category not in _CATEGORIES:
        raise KnowledgeRegistryError(f"entry[{index}].category is invalid")
    topics = _string_list(raw["topics"], f"entry[{index}].topics", allow_empty=False)
    signals = _string_list(raw["signals"], f"entry[{index}].signals")
    metric_refs = _string_list(raw["metric_refs"], f"entry[{index}].metric_refs")
    for token in topics + signals + metric_refs:
        if not _TOKEN_RE.fullmatch(token):
            raise KnowledgeRegistryError(f"entry[{index}] contains invalid token")
    text = _required_text(raw["text"], f"entry[{index}].text", max_length=MAX_TEXT_LENGTH)
    sources_raw = raw["sources"]
    if isinstance(sources_raw, (str, bytes)) or not isinstance(sources_raw, Sequence) or not sources_raw:
        raise KnowledgeRegistryError(f"entry[{index}].sources must be non-empty")
    if len(sources_raw) > 12:
        raise KnowledgeRegistryError(f"entry[{index}].sources exceeds length limit")
    sources: list[dict[str, str]] = []
    for source_index, source in enumerate(sources_raw):
        if not isinstance(source, Mapping) or set(source) != {"source_ref", "source_level"}:
            raise KnowledgeRegistryError(f"entry[{index}].sources[{source_index}] fields are invalid")
        source_ref = _required_text(source["source_ref"], "source_ref", max_length=240)
        source_level = source["source_level"]
        if source_level not in _SOURCE_LEVELS:
            raise KnowledgeRegistryError(f"entry[{index}] source_level is invalid")
        sources.append({"source_ref": source_ref, "source_level": source_level})
    max_claim = raw["max_claim_level"]
    if max_claim not in _CLAIM_LEVELS:
        raise KnowledgeRegistryError(f"entry[{index}].max_claim_level is invalid")
    limitations = _string_list(raw["limitations"], f"entry[{index}].limitations", allow_empty=False)
    counterevidence = _string_list(raw["counterevidence"], f"entry[{index}].counterevidence")
    supported_uses = _string_list(raw["supported_uses"], f"entry[{index}].supported_uses", allow_empty=False)
    if set(supported_uses) - _SUPPORTED_USES:
        raise KnowledgeRegistryError(f"entry[{index}].supported_uses is invalid")

    levels = {source["source_level"] for source in sources}
    if "community_consensus" in levels and max_claim not in {"community_consensus", "experimental"}:
        raise KnowledgeRegistryError(f"entry[{index}] community source exceeds claim level")
    if levels & {"personal_experience_unverified", "experimental"} and max_claim != "experimental":
        raise KnowledgeRegistryError(f"entry[{index}] personal/experimental source must stay experimental")
    if category in {"body_tension_hypothesis", "settings_experiment"} and max_claim != "experimental":
        raise KnowledgeRegistryError(f"entry[{index}] category must stay experimental")

    return {
        "entry_id": entry_id,
        "entry_version": version,
        "status": status,
        "category": category,
        "topics": topics,
        "signals": signals,
        "metric_refs": metric_refs,
        "text": text,
        "sources": sources,
        "max_claim_level": max_claim,
        "limitations": limitations,
        "counterevidence": counterevidence,
        "supported_uses": supported_uses,
    }


def validate_registry(raw: Any) -> dict[str, Any]:
    _reject_unsafe_shape(raw)
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "registry_version", "signal_aliases", "entries",
    }:
        raise KnowledgeRegistryError("registry fields are invalid")
    if raw["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise KnowledgeRegistryError("unsupported schema_version")
    registry_version = _required_text(raw["registry_version"], "registry_version", max_length=80)
    aliases_raw = raw["signal_aliases"]
    if not isinstance(aliases_raw, Mapping) or len(aliases_raw) > 128:
        raise KnowledgeRegistryError("signal_aliases must be an object")
    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        alias_text = _required_text(alias, "signal alias", max_length=120)
        canonical_text = _required_text(canonical, "canonical signal", max_length=120)
        if alias_text == canonical_text or alias_text in aliases or canonical_text in aliases_raw:
            raise KnowledgeRegistryError("signal alias is invalid or chained")
        aliases[alias_text] = canonical_text
    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise KnowledgeRegistryError("entries must be a list")
    if not 1 <= len(entries_raw) <= MAX_ENTRIES:
        raise KnowledgeRegistryError("entries has invalid length")
    entries = [_normalize_entry(item, index) for index, item in enumerate(entries_raw)]
    seen: set[tuple[str, int]] = set()
    active: set[str] = set()
    for entry in entries:
        key = (entry["entry_id"], entry["entry_version"])
        if key in seen:
            raise KnowledgeRegistryError("duplicate entry version")
        seen.add(key)
        if entry["status"] == "active":
            if entry["entry_id"] in active:
                raise KnowledgeRegistryError("multiple active versions for entry")
            active.add(entry["entry_id"])
    return copy.deepcopy({
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_version": registry_version,
        "signal_aliases": aliases,
        "entries": entries,
    })


@lru_cache(maxsize=1)
def load_registry(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path is not None else REGISTRY_PATH
    try:
        raw_bytes = registry_path.read_bytes()
    except OSError as exc:
        raise KnowledgeRegistryError("knowledge registry is unavailable") from exc
    if len(raw_bytes) > MAX_REGISTRY_BYTES:
        raise KnowledgeRegistryError("knowledge registry exceeds size limit")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeRegistryError("knowledge registry is invalid JSON") from exc
    return validate_registry(raw)


def entry_ref(entry: Mapping[str, Any]) -> str:
    return f"knowledge:{entry['entry_id']}@{entry['entry_version']}"


def query_registry(
    registry: Mapping[str, Any] | None = None,
    *,
    topic: str | None = None,
    issue_signal: str | None = None,
    metric_refs: Sequence[str] = (),
    supported_use: str | None = None,
) -> list[dict[str, Any]]:
    data = validate_registry(registry) if registry is not None else load_registry()
    topic_value = topic.strip() if isinstance(topic, str) and topic.strip() else None
    signal_value = issue_signal.strip() if isinstance(issue_signal, str) and issue_signal.strip() else None
    metric_values = {
        item.strip() for item in metric_refs
        if isinstance(item, str) and item.strip()
    }
    use_value = supported_use.strip() if isinstance(supported_use, str) and supported_use.strip() else None
    if not any((topic_value, signal_value, metric_values, use_value)):
        raise KnowledgeRegistryError("at least one query condition is required")
    aliases = data["signal_aliases"]
    canonical_signal = aliases.get(signal_value, signal_value) if signal_value else None
    ranked: list[tuple[int, str, int, dict[str, Any]]] = []
    for entry in data["entries"]:
        if entry["status"] != "active":
            continue
        score = 0
        if canonical_signal and canonical_signal in entry["signals"]:
            score += 16
        if metric_values and metric_values.intersection(entry["metric_refs"]):
            score += 8
        if topic_value and topic_value in entry["topics"]:
            score += 4
        if use_value and use_value in entry["supported_uses"]:
            score += 2
        if score:
            ranked.append((-score, entry["entry_id"], -entry["entry_version"], entry))
    ranked.sort(key=lambda row: row[:3])
    return [copy.deepcopy(row[3]) for row in ranked[:MAX_RESULTS]]


__all__ = [
    "KnowledgeRegistryError", "REGISTRY_PATH", "REGISTRY_SCHEMA_VERSION",
    "MAX_RESULTS", "entry_ref", "load_registry", "query_registry", "validate_registry",
]
