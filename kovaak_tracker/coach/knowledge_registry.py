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

from kovaak_tracker.scenario_profiles import active_scenario_profile_refs

REGISTRY_SCHEMA_VERSION_V1 = "coach_knowledge_registry.v1"
REGISTRY_SCHEMA_VERSION_V2 = "coach_knowledge_registry.v2"
REGISTRY_SCHEMA_VERSION_V3 = "coach_knowledge_registry.v3"
REGISTRY_SCHEMA_VERSION = REGISTRY_SCHEMA_VERSION_V3
_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
_REGISTRY_ROOT = (
    Path(_RESOURCE_ROOT) / "knowledge" / "coach"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[2] / "knowledge" / "coach"
)
REGISTRY_PATH_V1 = _REGISTRY_ROOT / "registry.v1.json"
REGISTRY_PATH_V2 = _REGISTRY_ROOT / "registry.v2.json"
REGISTRY_PATH_V3 = _REGISTRY_ROOT / "registry.v3.json"
REGISTRY_PATH_V4 = _REGISTRY_ROOT / "registry.v4.json"
REGISTRY_PATH_V5 = _REGISTRY_ROOT / "registry.v5.json"
REGISTRY_PATH_V6 = _REGISTRY_ROOT / "registry.v6.json"
REGISTRY_PATH_V7 = _REGISTRY_ROOT / "registry.v7.json"
REGISTRY_PATH = REGISTRY_PATH_V7
_PACKAGED_REGISTRIES = {
    "2026-07-14.v1": REGISTRY_PATH_V1,
    "2026-07-22.v2": REGISTRY_PATH_V2,
    "2026-07-28.v3": REGISTRY_PATH_V3,
    "2026-07-29.v4": REGISTRY_PATH_V4,
    "2026-08-06.v5": REGISTRY_PATH_V5,
    "2026-08-06.v6": REGISTRY_PATH_V6,
    "2026-08-15.v7": REGISTRY_PATH_V7,
}
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
_SOURCE_LEVELS_V2 = {
    "product_contract", "academic_peer_reviewed", "community_organization",
    "coach_first_party", "community_consensus", "personal_experience_unverified",
    "experimental",
}
_CLAIM_LEVELS = {
    "deterministic_rule", "research_supported", "community_consensus", "experimental",
}
_CLAIM_LEVELS_V2 = {
    "deterministic_rule", "research_supported", "community_practice",
    "community_consensus", "experimental",
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
_SUPPORTED_USES_V3 = {
    "explanation_only", "diagnosis_support", "candidate_experiment",
    "scenario_prescription",
}
_CAPABILITY_PREFIXES_V3 = (
    ("explanation_only",),
    ("explanation_only", "diagnosis_support"),
    ("explanation_only", "diagnosis_support", "candidate_experiment"),
    (
        "explanation_only", "diagnosis_support", "candidate_experiment",
        "scenario_prescription",
    ),
)
_ENTRY_FIELDS_V2 = {
    "entry_id", "entry_version", "status", "category", "topics", "signals",
    "metric_refs", "family_scope", "observation_refs", "quality_prerequisites",
    "definition", "scope", "expected_direction", "mechanisms",
    "alternative_explanations", "forbidden_inferences", "limitations",
    "counterevidence", "cue", "dose_guardrail", "matched_retest",
    "near_transfer_retest", "stop_adjust_rule", "sources", "supported_uses",
}
_SCENARIO_PRESCRIPTION_FIELD = "scenario_prescription"
_SCENARIO_PROFILE_REF_RE = re.compile(r"^scenario:[A-Za-z0-9._:@-]+$")
_SCENARIO_REVIEW_AFTER = {
    "next comparable practice session", "next matched retest",
    "after one comparable practice block",
}
_CATEGORIES_V2 = {
    "observation_definition", "mechanism", "training_cue",
    "prescription_verification", "limitation", "outcome_only",
}
_FAMILIES_V2 = {
    "static_clicking", "dynamic_clicking", "predictable_tracking",
    "reactive_tracking", "control_tracking", "target_switching", "movement_aiming",
}
_DIRECTIONS_V2 = {
    "lower_better", "higher_better", "target_band", "descriptive_only",
    "comparison_only",
}
_SOURCE_CLAIM_CEILING = {
    "experimental": "experimental",
    "personal_experience_unverified": "experimental",
    "coach_first_party": "community_practice",
    "community_organization": "community_practice",
    "community_consensus": "community_consensus",
    "academic_peer_reviewed": "research_supported",
    "product_contract": "deterministic_rule",
}
_CLAIM_RANK = {
    "experimental": 0,
    "community_practice": 1,
    "community_consensus": 2,
    "research_supported": 3,
    "deterministic_rule": 4,
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


def _normalize_entry_v1(raw: Any, index: int) -> dict[str, Any]:
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


def _validate_registry_v1(raw: Any) -> dict[str, Any]:
    _reject_unsafe_shape(raw)
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "registry_version", "signal_aliases", "entries",
    }:
        raise KnowledgeRegistryError("registry fields are invalid")
    if raw["schema_version"] != REGISTRY_SCHEMA_VERSION_V1:
        raise KnowledgeRegistryError("unsupported schema_version")
    registry_version = _required_text(raw["registry_version"], "registry_version", max_length=80)
    aliases_raw = raw["signal_aliases"]
    if not isinstance(aliases_raw, Mapping) or len(aliases_raw) > 128:
        raise KnowledgeRegistryError("signal_aliases must be an object")
    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        alias_text = _required_text(alias, "signal alias", max_length=120)
        canonical_text = _required_text(canonical, "canonical signal", max_length=120)
        if (
            not _TOKEN_RE.fullmatch(alias_text)
            or not _TOKEN_RE.fullmatch(canonical_text)
            or alias_text == canonical_text
            or alias_text in aliases
            or canonical_text in aliases_raw
        ):
            raise KnowledgeRegistryError("signal alias is invalid or chained")
        aliases[alias_text] = canonical_text
    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise KnowledgeRegistryError("entries must be a list")
    if not 1 <= len(entries_raw) <= MAX_ENTRIES:
        raise KnowledgeRegistryError("entries has invalid length")
    entries = [_normalize_entry_v1(item, index) for index, item in enumerate(entries_raw)]
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
        "schema_version": REGISTRY_SCHEMA_VERSION_V1,
        "registry_version": registry_version,
        "signal_aliases": aliases,
        "entries": entries,
    })


def _normalize_source_v2(raw: Any, index: int) -> dict[str, Any]:
    required = {
        "source_ref", "source_level", "title", "author_or_org", "retrieved_at",
        "locator", "applicability", "supports_sections",
    }
    if not isinstance(raw, Mapping) or not required <= set(raw) or set(raw) - (required | {"published_at"}):
        raise KnowledgeRegistryError(f"source[{index}] fields are invalid")
    source_ref = _required_text(raw["source_ref"], f"source[{index}].source_ref", max_length=160)
    if not _TOKEN_RE.fullmatch(source_ref):
        raise KnowledgeRegistryError(f"source[{index}].source_ref is invalid")
    source_level = raw["source_level"]
    if source_level not in _SOURCE_LEVELS_V2:
        raise KnowledgeRegistryError(f"source[{index}].source_level is invalid")
    published_at = raw.get("published_at")
    if published_at is not None:
        published_at = _required_text(published_at, f"source[{index}].published_at", max_length=32)
    retrieved_at = _required_text(raw["retrieved_at"], f"source[{index}].retrieved_at", max_length=32)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", retrieved_at):
        raise KnowledgeRegistryError(f"source[{index}].retrieved_at is invalid")
    applicability = _string_list(
        raw["applicability"], f"source[{index}].applicability", allow_empty=False
    )
    supports_sections = _string_list(
        raw["supports_sections"], f"source[{index}].supports_sections", allow_empty=False
    )
    for token in [source_ref, *applicability, *supports_sections]:
        if not _TOKEN_RE.fullmatch(token):
            raise KnowledgeRegistryError(f"source[{index}] contains invalid token")
    return {
        "source_ref": source_ref,
        "source_level": source_level,
        "title": _required_text(raw["title"], f"source[{index}].title", max_length=1200),
        "author_or_org": _required_text(
            raw["author_or_org"], f"source[{index}].author_or_org", max_length=1200
        ),
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "locator": _required_text(raw["locator"], f"source[{index}].locator", max_length=1200),
        "applicability": applicability,
        "supports_sections": supports_sections,
    }


def _normalize_section_v2(
    raw: Any,
    *,
    entry_id: str,
    entry_family_scope: set[str],
    field: str,
    section_name: str,
    entry_source_refs: set[str],
    sources_by_ref: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "section_ref", "claim_level", "source_refs", "text",
    }:
        raise KnowledgeRegistryError(f"{field} fields are invalid")
    section_reference = _required_text(raw["section_ref"], f"{field}.section_ref", max_length=160)
    if not _TOKEN_RE.fullmatch(section_reference) or not section_reference.startswith(f"{entry_id}."):
        raise KnowledgeRegistryError(f"{field}.section_ref is invalid")
    claim_level = raw["claim_level"]
    if claim_level not in _CLAIM_LEVELS_V2:
        raise KnowledgeRegistryError(f"{field}.claim_level is invalid")
    source_refs = _string_list(raw["source_refs"], f"{field}.source_refs", allow_empty=False)
    if not set(source_refs) <= entry_source_refs:
        raise KnowledgeRegistryError(f"{field}.source_refs escape the entry sources")
    for source_ref in source_refs:
        source = sources_by_ref[source_ref]
        if section_name not in source["supports_sections"]:
            raise KnowledgeRegistryError(f"{field} is unsupported by source {source_ref}")
        applicability = set(source["applicability"])
        if "all_families" not in applicability and not entry_family_scope <= applicability:
            raise KnowledgeRegistryError(
                f"{field} source {source_ref} does not cover the entry family scope"
            )
        ceiling = _SOURCE_CLAIM_CEILING[source["source_level"]]
        if _CLAIM_RANK[claim_level] > _CLAIM_RANK[ceiling]:
            raise KnowledgeRegistryError(f"{field}.claim_level exceeds its source ceiling")
    return {
        "section_ref": section_reference,
        "claim_level": claim_level,
        "source_refs": source_refs,
        "text": _required_text(raw["text"], f"{field}.text", max_length=1200),
    }


def _normalize_scenario_prescription_v2(
    raw: Any,
    *,
    field: str,
    entry_family_scope: set[str],
    entry_source_refs: set[str],
    sources_by_ref: Mapping[str, Mapping[str, Any]],
    active_scenario_refs: set[str],
) -> dict[str, Any] | str:
    if raw == "not_applicable":
        return raw
    if not isinstance(raw, Mapping) or set(raw) != {
        "scenario_profile_ref", "practice_condition", "review_after", "source_refs",
        "claim_level",
    }:
        raise KnowledgeRegistryError(f"{field}.scenario_prescription fields are invalid")
    scenario_profile_ref = _required_text(
        raw["scenario_profile_ref"], f"{field}.scenario_profile_ref", max_length=200
    )
    if not _SCENARIO_PROFILE_REF_RE.fullmatch(scenario_profile_ref):
        raise KnowledgeRegistryError(f"{field}.scenario_profile_ref is invalid")
    if scenario_profile_ref not in active_scenario_refs:
        raise KnowledgeRegistryError(f"{field}.scenario_profile_ref is not an active scenario")
    practice_condition = _required_text(
        raw["practice_condition"], f"{field}.practice_condition", max_length=500
    )
    review_after = raw["review_after"]
    if review_after not in _SCENARIO_REVIEW_AFTER:
        raise KnowledgeRegistryError(f"{field}.review_after is invalid")
    claim_level = raw["claim_level"]
    if claim_level not in _CLAIM_LEVELS_V2:
        raise KnowledgeRegistryError(f"{field}.scenario_prescription.claim_level is invalid")
    source_refs = _string_list(
        raw["source_refs"], f"{field}.scenario_prescription.source_refs", allow_empty=False
    )
    if not set(source_refs) <= entry_source_refs:
        raise KnowledgeRegistryError(f"{field}.scenario_prescription.source_refs escape the entry sources")
    for source_ref in source_refs:
        source = sources_by_ref[source_ref]
        if _SCENARIO_PRESCRIPTION_FIELD not in source["supports_sections"]:
            raise KnowledgeRegistryError(
                f"{field}.scenario_prescription is unsupported by source {source_ref}"
            )
        applicability = set(source["applicability"])
        if "all_families" not in applicability and not entry_family_scope <= applicability:
            raise KnowledgeRegistryError(
                f"{field}.scenario_prescription source {source_ref} does not cover the entry family scope"
            )
        ceiling = _SOURCE_CLAIM_CEILING[source["source_level"]]
        if _CLAIM_RANK[claim_level] > _CLAIM_RANK[ceiling]:
            raise KnowledgeRegistryError(
                f"{field}.scenario_prescription.claim_level exceeds its source ceiling"
            )
    return {
        "scenario_profile_ref": scenario_profile_ref,
        "practice_condition": practice_condition,
        "review_after": review_after,
        "source_refs": source_refs,
        "claim_level": claim_level,
    }


def _normalize_entry_v2(
    raw: Any,
    index: int,
    *,
    sources_by_ref: Mapping[str, Mapping[str, Any]],
    requires_scenario_prescription: bool,
    supported_uses_allowed: set[str] = _SUPPORTED_USES,
    allow_non_outcome_not_applicable: bool = False,
    allow_empty_observation_context: bool = False,
    active_scenario_refs: set[str],
) -> dict[str, Any]:
    expected_fields = set(_ENTRY_FIELDS_V2)
    if isinstance(raw, Mapping) and _SCENARIO_PRESCRIPTION_FIELD in raw:
        expected_fields.add(_SCENARIO_PRESCRIPTION_FIELD)
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise KnowledgeRegistryError(f"entry[{index}] fields are invalid")
    if requires_scenario_prescription and _SCENARIO_PRESCRIPTION_FIELD not in raw:
        raise KnowledgeRegistryError(f"entry[{index}].scenario_prescription is required")
    field = f"entry[{index}]"
    entry_id = _required_text(raw["entry_id"], f"{field}.entry_id", max_length=160)
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise KnowledgeRegistryError(f"{field}.entry_id is invalid")
    version = raw["entry_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise KnowledgeRegistryError(f"{field}.entry_version is invalid")
    status = raw["status"]
    if status not in _STATUSES:
        raise KnowledgeRegistryError(f"{field}.status is invalid")
    category = raw["category"]
    if category not in _CATEGORIES_V2:
        raise KnowledgeRegistryError(f"{field}.category is invalid")

    token_fields = {}
    for name, allow_empty in (
        ("topics", False), ("signals", True), ("metric_refs", True),
        ("family_scope", False),
        ("observation_refs", allow_empty_observation_context),
        ("quality_prerequisites", allow_empty_observation_context), ("sources", False),
        ("supported_uses", False),
    ):
        values = _string_list(raw[name], f"{field}.{name}", allow_empty=allow_empty)
        if any(not _TOKEN_RE.fullmatch(token) for token in values):
            raise KnowledgeRegistryError(f"{field}.{name} contains invalid token")
        token_fields[name] = values
    if set(token_fields["family_scope"]) - _FAMILIES_V2:
        raise KnowledgeRegistryError(f"{field}.family_scope is invalid")
    if set(token_fields["sources"]) - set(sources_by_ref):
        raise KnowledgeRegistryError(f"{field}.sources contains an unknown source")
    if set(token_fields["supported_uses"]) - supported_uses_allowed:
        raise KnowledgeRegistryError(f"{field}.supported_uses is invalid")
    entry_source_refs = set(token_fields["sources"])

    def section(name: str, value: Any | None = None) -> dict[str, Any]:
        return _normalize_section_v2(
            raw[name] if value is None else value,
            entry_id=entry_id,
            entry_family_scope=set(token_fields["family_scope"]),
            field=f"{field}.{name}",
            section_name=name,
            entry_source_refs=entry_source_refs,
            sources_by_ref=sources_by_ref,
        )

    definition = section("definition")
    scope = section("scope")
    expected_direction = section("expected_direction")
    if expected_direction["text"] not in _DIRECTIONS_V2:
        raise KnowledgeRegistryError(f"{field}.expected_direction is invalid")
    mechanisms_raw = raw["mechanisms"]
    if isinstance(mechanisms_raw, (str, bytes)) or not isinstance(mechanisms_raw, Sequence) or not mechanisms_raw:
        raise KnowledgeRegistryError(f"{field}.mechanisms must be non-empty")
    mechanisms = [section("mechanisms", value) for value in mechanisms_raw]
    prose_fields = {
        name: _string_list(raw[name], f"{field}.{name}", allow_empty=False)
        for name in (
            "alternative_explanations", "forbidden_inferences", "limitations",
            "counterevidence",
        )
    }

    outcome_only = category == "outcome_only"
    prescription: dict[str, Any] = {}
    for name in ("cue", "matched_retest", "near_transfer_retest"):
        value = raw[name]
        if value == "not_applicable":
            if not outcome_only and not allow_non_outcome_not_applicable:
                raise KnowledgeRegistryError(f"{field}.{name} cannot be not_applicable")
            prescription[name] = value
        else:
            prescription[name] = section(name)
    for name in ("dose_guardrail", "stop_adjust_rule"):
        value = raw[name]
        if value == "not_applicable":
            if not outcome_only and not allow_non_outcome_not_applicable:
                raise KnowledgeRegistryError(f"{field}.{name} cannot be not_applicable")
            prescription[name] = value
            continue
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
            raise KnowledgeRegistryError(f"{field}.{name} must be non-empty")
        prescription[name] = [section(name, item) for item in value]
    if outcome_only and set(token_fields["family_scope"]) != {"movement_aiming"}:
        raise KnowledgeRegistryError(f"{field} outcome_only scope is invalid")
    scenario_prescription = (
        _normalize_scenario_prescription_v2(
            raw[_SCENARIO_PRESCRIPTION_FIELD],
            field=field,
                entry_family_scope=set(token_fields["family_scope"]),
                entry_source_refs=entry_source_refs,
                sources_by_ref=sources_by_ref,
                active_scenario_refs=active_scenario_refs,
            )
        if _SCENARIO_PRESCRIPTION_FIELD in raw
        else None
    )

    normalized = {
        "entry_id": entry_id,
        "entry_version": version,
        "status": status,
        "category": category,
        "topics": token_fields["topics"],
        "signals": token_fields["signals"],
        "metric_refs": token_fields["metric_refs"],
        "family_scope": token_fields["family_scope"],
        "observation_refs": token_fields["observation_refs"],
        "quality_prerequisites": token_fields["quality_prerequisites"],
        "definition": definition,
        "scope": scope,
        "expected_direction": expected_direction,
        "mechanisms": mechanisms,
        **prose_fields,
        **prescription,
        "sources": token_fields["sources"],
        "supported_uses": token_fields["supported_uses"],
    }
    if scenario_prescription is not None:
        normalized[_SCENARIO_PRESCRIPTION_FIELD] = scenario_prescription
    return normalized


def _normalize_entry_v3(
    raw: Any,
    index: int,
    *,
    sources_by_ref: Mapping[str, Mapping[str, Any]],
    active_scenario_refs: set[str],
) -> dict[str, Any]:
    field = f"entry[{index}]"
    optional_fields = {
        "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
        "stop_adjust_rule", _SCENARIO_PRESCRIPTION_FIELD,
    }
    base_fields = set(_ENTRY_FIELDS_V2) - optional_fields
    if not isinstance(raw, Mapping) or not base_fields <= set(raw) or set(raw) - base_fields - optional_fields:
        raise KnowledgeRegistryError(f"{field} fields are invalid")
    supported_uses = _string_list(
        raw["supported_uses"], f"{field}.supported_uses", allow_empty=False,
    )
    if tuple(supported_uses) not in _CAPABILITY_PREFIXES_V3:
        raise KnowledgeRegistryError(f"{field}.supported_uses capability prefix is invalid")
    if set(supported_uses) - _SUPPORTED_USES_V3:
        raise KnowledgeRegistryError(f"{field}.supported_uses is invalid")

    has_diagnosis = "diagnosis_support" in supported_uses
    has_experiment = "candidate_experiment" in supported_uses
    has_scenario = "scenario_prescription" in supported_uses
    required_fields = set()
    if has_experiment:
        required_fields.update({"cue", "dose_guardrail", "matched_retest", "stop_adjust_rule"})
    if has_scenario:
        required_fields.update({"near_transfer_retest", _SCENARIO_PRESCRIPTION_FIELD})
    forbidden_fields = optional_fields - required_fields
    if not required_fields <= set(raw):
        raise KnowledgeRegistryError(f"{field} capability required fields are missing")
    if forbidden_fields.intersection(raw):
        raise KnowledgeRegistryError(f"{field} capability forbidden fields are present")
    if has_diagnosis:
        if not raw.get("observation_refs") or not raw.get("quality_prerequisites"):
            raise KnowledgeRegistryError(f"{field} diagnosis_support context is required")

    normalized_input = dict(raw)
    for name in optional_fields:
        normalized_input.setdefault(name, "not_applicable")
    normalized = _normalize_entry_v2(
        normalized_input,
        index,
        sources_by_ref=sources_by_ref,
        requires_scenario_prescription=True,
        supported_uses_allowed=_SUPPORTED_USES_V3,
        allow_non_outcome_not_applicable=True,
        allow_empty_observation_context=True,
        active_scenario_refs=active_scenario_refs,
    )
    for name in forbidden_fields:
        normalized.pop(name, None)
    return normalized


def _validate_registry_v2(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "registry_version", "signal_aliases", "sources", "entries",
    }:
        raise KnowledgeRegistryError("registry fields are invalid")
    registry_version = _required_text(raw["registry_version"], "registry_version", max_length=80)
    aliases_raw = raw["signal_aliases"]
    if not isinstance(aliases_raw, Mapping) or len(aliases_raw) > 128:
        raise KnowledgeRegistryError("signal_aliases must be an object")
    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        alias_text = _required_text(alias, "signal alias", max_length=120)
        canonical_text = _required_text(canonical, "canonical signal", max_length=120)
        if (
            not _TOKEN_RE.fullmatch(alias_text)
            or not _TOKEN_RE.fullmatch(canonical_text)
            or alias_text == canonical_text
            or alias_text in aliases
            or canonical_text in aliases_raw
        ):
            raise KnowledgeRegistryError("signal alias is invalid or chained")
        aliases[alias_text] = canonical_text

    sources_raw = raw["sources"]
    if isinstance(sources_raw, (str, bytes)) or not isinstance(sources_raw, Sequence) or not sources_raw:
        raise KnowledgeRegistryError("sources must be a non-empty list")
    sources = [_normalize_source_v2(item, index) for index, item in enumerate(sources_raw)]
    sources_by_ref = {source["source_ref"]: source for source in sources}
    if len(sources_by_ref) != len(sources):
        raise KnowledgeRegistryError("duplicate source_ref")

    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise KnowledgeRegistryError("entries must be a list")
    if not 1 <= len(entries_raw) <= MAX_ENTRIES:
        raise KnowledgeRegistryError("entries has invalid length")
    requires_scenario_prescription = registry_version == "2026-07-28.v3"
    active_scenario_refs = active_scenario_profile_refs()
    entries = [
        _normalize_entry_v2(
            item,
            index,
            sources_by_ref=sources_by_ref,
            requires_scenario_prescription=requires_scenario_prescription,
            active_scenario_refs=active_scenario_refs,
        )
        for index, item in enumerate(entries_raw)
    ]
    seen_entries: set[tuple[str, int]] = set()
    active_entries: set[str] = set()
    seen_sections: set[str] = set()
    for entry in entries:
        key = (entry["entry_id"], entry["entry_version"])
        if key in seen_entries:
            raise KnowledgeRegistryError("duplicate entry version")
        seen_entries.add(key)
        if entry["status"] == "active":
            if entry["entry_id"] in active_entries:
                raise KnowledgeRegistryError("multiple active versions for entry")
            active_entries.add(entry["entry_id"])
        section_values = [entry["definition"], entry["scope"], entry["expected_direction"]]
        section_values.extend(entry["mechanisms"])
        for name in ("cue", "matched_retest", "near_transfer_retest"):
            value = entry.get(name)
            if value is not None and value != "not_applicable":
                section_values.append(value)
        for name in ("dose_guardrail", "stop_adjust_rule"):
            value = entry.get(name)
            if value is not None and value != "not_applicable":
                section_values.extend(value)
        for section_value in section_values:
            section_reference = section_value["section_ref"]
            if section_reference in seen_sections:
                raise KnowledgeRegistryError("duplicate section_ref")
            seen_sections.add(section_reference)
    return copy.deepcopy({
        "schema_version": REGISTRY_SCHEMA_VERSION_V2,
        "registry_version": registry_version,
        "signal_aliases": aliases,
        "sources": sources,
        "entries": entries,
    })


def _validate_registry_v3(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "registry_version", "signal_aliases", "sources", "entries",
    }:
        raise KnowledgeRegistryError("registry fields are invalid")
    registry_version = _required_text(raw["registry_version"], "registry_version", max_length=80)
    aliases_raw = raw["signal_aliases"]
    if not isinstance(aliases_raw, Mapping) or len(aliases_raw) > 128:
        raise KnowledgeRegistryError("signal_aliases must be an object")
    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        alias_text = _required_text(alias, "signal alias", max_length=120)
        canonical_text = _required_text(canonical, "canonical signal", max_length=120)
        if (
            not _TOKEN_RE.fullmatch(alias_text)
            or not _TOKEN_RE.fullmatch(canonical_text)
            or alias_text == canonical_text
            or alias_text in aliases
            or canonical_text in aliases_raw
        ):
            raise KnowledgeRegistryError("signal alias is invalid or chained")
        aliases[alias_text] = canonical_text

    sources_raw = raw["sources"]
    if isinstance(sources_raw, (str, bytes)) or not isinstance(sources_raw, Sequence) or not sources_raw:
        raise KnowledgeRegistryError("sources must be a non-empty list")
    sources = [_normalize_source_v2(item, index) for index, item in enumerate(sources_raw)]
    sources_by_ref = {source["source_ref"]: source for source in sources}
    if len(sources_by_ref) != len(sources):
        raise KnowledgeRegistryError("duplicate source_ref")

    entries_raw = raw["entries"]
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise KnowledgeRegistryError("entries must be a list")
    if not 1 <= len(entries_raw) <= MAX_ENTRIES:
        raise KnowledgeRegistryError("entries has invalid length")
    active_scenario_refs = active_scenario_profile_refs()
    entries = [
        _normalize_entry_v3(
            item,
            index,
            sources_by_ref=sources_by_ref,
            active_scenario_refs=active_scenario_refs,
        )
        for index, item in enumerate(entries_raw)
    ]
    seen_entries: set[tuple[str, int]] = set()
    active_entries: set[str] = set()
    seen_sections: set[str] = set()
    for entry in entries:
        key = (entry["entry_id"], entry["entry_version"])
        if key in seen_entries:
            raise KnowledgeRegistryError("duplicate entry version")
        seen_entries.add(key)
        if entry["status"] == "active":
            if entry["entry_id"] in active_entries:
                raise KnowledgeRegistryError("multiple active versions for entry")
            active_entries.add(entry["entry_id"])
        section_values = [entry["definition"], entry["scope"], entry["expected_direction"]]
        section_values.extend(entry["mechanisms"])
        for name in ("cue", "matched_retest", "near_transfer_retest"):
            value = entry.get(name)
            if value is not None and value != "not_applicable":
                section_values.append(value)
        for name in ("dose_guardrail", "stop_adjust_rule"):
            value = entry.get(name)
            if value is not None and value != "not_applicable":
                section_values.extend(value)
        for section_value in section_values:
            section_reference = section_value["section_ref"]
            if section_reference in seen_sections:
                raise KnowledgeRegistryError("duplicate section_ref")
            seen_sections.add(section_reference)
    return copy.deepcopy({
        "schema_version": REGISTRY_SCHEMA_VERSION_V3,
        "registry_version": registry_version,
        "signal_aliases": aliases,
        "sources": sources,
        "entries": entries,
    })


def validate_registry(raw: Any) -> dict[str, Any]:
    _reject_unsafe_shape(raw)
    if not isinstance(raw, Mapping):
        raise KnowledgeRegistryError("registry must be an object")
    schema_version = raw.get("schema_version")
    if schema_version == REGISTRY_SCHEMA_VERSION_V1:
        return _validate_registry_v1(raw)
    if schema_version == REGISTRY_SCHEMA_VERSION_V2:
        return _validate_registry_v2(raw)
    if schema_version == REGISTRY_SCHEMA_VERSION_V3:
        return _validate_registry_v3(raw)
    raise KnowledgeRegistryError("unsupported schema_version")


@lru_cache(maxsize=8)
def load_registry(
    path: str | os.PathLike[str] | None = None,
    *,
    registry_version: str | None = None,
) -> dict[str, Any]:
    if path is not None and registry_version is not None:
        raise KnowledgeRegistryError("path and registry_version are mutually exclusive")
    if registry_version is not None:
        registry_path = _PACKAGED_REGISTRIES.get(registry_version)
        if registry_path is None:
            raise KnowledgeRegistryError("unknown registry version")
    else:
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
    validated = validate_registry(raw)
    if registry_version is not None and validated["registry_version"] != registry_version:
        raise KnowledgeRegistryError("registry version does not match its asset")
    return validated


def entry_ref(entry: Mapping[str, Any]) -> str:
    return f"knowledge:{entry['entry_id']}@{entry['entry_version']}"


def claim_ref(section: Mapping[str, Any]) -> str:
    section_reference = section.get("section_ref")
    if not isinstance(section_reference, str) or not _TOKEN_RE.fullmatch(section_reference):
        raise KnowledgeRegistryError("knowledge section_ref is invalid")
    return f"claim:{section_reference}"


def resolve_entry(*, registry_version: str, entry_reference: str) -> dict[str, Any]:
    data = load_registry(registry_version=registry_version)
    for entry in data["entries"]:
        if entry_ref(entry) == entry_reference:
            return copy.deepcopy(entry)
    raise KnowledgeRegistryError("unknown knowledge entry")


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
    "KnowledgeRegistryError", "REGISTRY_PATH", "REGISTRY_PATH_V1", "REGISTRY_PATH_V2",
    "REGISTRY_PATH_V3", "REGISTRY_PATH_V4", "REGISTRY_PATH_V5", "REGISTRY_PATH_V6", "REGISTRY_SCHEMA_VERSION",
    "REGISTRY_SCHEMA_VERSION_V1", "REGISTRY_SCHEMA_VERSION_V2", "REGISTRY_SCHEMA_VERSION_V3",
    "MAX_RESULTS", "claim_ref", "entry_ref", "load_registry", "query_registry",
    "resolve_entry", "validate_registry",
]
