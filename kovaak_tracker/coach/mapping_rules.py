"""coach_mapping.v1: schema constants, validator, and compiler for mapping docs.

Contract source: kb-sdk implementation plan C1
(``.zcode/kb-sdk-impl-plan-2026-09-20.md``). A mapping document carries only
keys, numbers, and short copy; long explanation prose stays in the knowledge
registry. Unified fail-closed evaluation semantics (empty knowledge refs and
unresolvable ``expected_entry_ref`` drop the rule at run time) are engine
behavior hard-coded by the evaluator, not schema switches.

Module editing-rights pipeline (plan §4.2):

- WP-02: schema constants, :func:`validate_mapping`, :func:`compile_mapping`
  with a module-level cache keyed by ``path + mtime_ns + size``.
- WP-04: static evaluation — :func:`evaluate_static` and
  :func:`dispatch_static`.
- WP-05 (this version): family evaluation — :func:`evaluate_family` plus the
  engine closures :func:`make_family_advice_fn` returns (unified fail-closed
  semantics; dropping the dynamic_clicking candidate whose knowledge refs
  come back empty is the one intentional behavior change, decided
  2026-09-20, no switch).

The ``Compiled*`` shapes are the seam between validation and the evaluators;
do not casually reshape them when adding evaluators.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import advice
from . import knowledge_active
from .knowledge_registry import entry_ref


class MappingValidationError(ValueError):
    """A mapping document violates the frozen coach_mapping.v1 contract."""


# ---------------------------------------------------------------------------
# C1 schema constants (frozen; knowledge/mapping/vocabulary.v1.json mirrors
# the enums — validate_mapping cross-checks them and rejects drift).
# ---------------------------------------------------------------------------
MAPPING_SCHEMA_VERSION = "coach_mapping.v1"

MAX_TOTAL_RULES = 128        # static + every family rule combined
MAX_STATIC_RULES = 32
MAX_RULES_PER_FAMILY = 32
MAX_ARCHETYPES = 32
MAX_ROOT_CAUSES = 64
MAX_DEPTH = 6                # container nesting levels inside the document
MAX_FILE_BYTES = 256 * 1024  # enforced by file loaders (knowledge_pack), not here

MIN_CONDITIONS = 1
MAX_CONDITIONS = 4
MAX_PRESCRIPTIONS = 8

MAX_TEXT_LENGTH = 600               # StaticRule.text
MAX_MEANING_LENGTH = 400            # plain_language_meaning / expected_result
MAX_SCENARIO_LENGTH = 120           # Prescription.scenario
MAX_PRESCRIPTION_TEXT_LENGTH = 400  # reason / cue / purpose / stop_or_adjust_rule
MAX_RETEST_AFTER_LENGTH = 200
MAX_LIST_TEXT_LENGTH = 200          # free-text items inside string lists
MAX_ROOT_CAUSE_TEXT_LENGTH = 400
MAX_ID_LENGTH = 60                  # archetype id / label

SEVERITIES = frozenset({"info", "watch", "fix"})
DIRECTIONS = frozenset({"higher", "lower", "absolute_higher"})
CLAIM_LEVELS = frozenset({
    "deterministic_rule", "research_supported", "community_practice",
    "community_consensus", "experimental",
})
CONDITION_INPUTS = frozenset({"self_summary", "reference_summary", "settings"})
CONDITION_OPS = frozenset({
    ">", "<", ">=", "<=", "in_band", "ratio_to_ref_lt", "metric_version_not_in",
})
COMPARISON_OPS = frozenset({">", "<", ">=", "<="})
CONDITION_STATS = frozenset({"med"})
GUARDRAIL_OPS = frozenset({"<=baseline", ">=baseline"})
CONDITION_DEFAULT_STAT = "med"
DEFAULT_FAMILY_CLAIM_LEVEL = "deterministic_rule"
DEFAULT_PRESCRIPTION_SOURCE_LEVEL = "community_consensus"
DEFAULT_REQUESTED_KNOWLEDGE_SECTIONS = (
    "definition", "mechanisms", "alternative_explanations", "cue",
    "dose_guardrail", "matched_retest", "stop_adjust_rule",
)
# Registry section names a family rule may request for Coach context.
KNOWLEDGE_SECTION_NAMES = frozenset({
    "definition", "scope", "expected_direction", "mechanisms",
    "alternative_explanations", "forbidden_inferences", "cue",
    "dose_guardrail", "matched_retest", "near_transfer_retest",
    "stop_adjust_rule",
})
# Registry-wide source-level space (knowledge_registry v2 set). Pack-level
# narrowing (C2: forbid product_contract / coach_first_party) is enforced by
# knowledge_pack, not here.
SOURCE_LEVELS = frozenset({
    "product_contract", "academic_peer_reviewed", "community_organization",
    "coach_first_party", "community_consensus", "personal_experience_unverified",
    "experimental",
})

FAMILIES = ("continuous_tracking", "dynamic_clicking", "target_switching")

# "knowledge:<entry_id>@<version>"; entry_id shape mirrors
# knowledge_registry._ENTRY_ID_RE.
_ENTRY_REF_RE = re.compile(r"^knowledge:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+@[1-9][0-9]*$")

# Same口径 as knowledge_registry._PATH_RE/_SECRET_RE (that module is read-only
# for this work package; keep the copies in sync deliberately).
_PATH_RE = re.compile(r"^(?:/|\\|~/|\.\.[/\\]|[A-Za-z]:[/\\]|file://)", re.I)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}"
)

# Smuggling defense: mapping content is data and must never carry behavior
# instructions. Registry-derived markers (credentials/payloads) match as
# substrings, same口径 as knowledge_registry. Instruction-like names match
# whole key segments only (split on non-alphanumerics), so the legitimate
# schema field "prescriptions" is not tripped by its "script" substring.
_FORBIDDEN_KEY_MARKERS = (
    "rawtrace", "payload", "credential", "apikey", "accesstoken",
    "refreshtoken", "password", "secret", "authorization",
)
_FORBIDDEN_INSTRUCTION_KEYS = frozenset({
    "command", "exec", "execute", "eval", "evaluate", "shell", "terminal",
    "script", "instruction", "prompt", "handler", "callback",
})
# 2x the largest legal copy field; below it the typed checks report precise
# length errors, above it the text is treated as smuggled content.
_SUSPICIOUS_TEXT_LENGTH = 1200

# ---------------------------------------------------------------------------
# Structural safety walk (runs first, like knowledge_registry does)
# ---------------------------------------------------------------------------


def _reject_unsafe_shape(value: Any, *, depth: int = 0) -> None:
    if isinstance(value, Mapping):
        if depth > MAX_DEPTH:
            raise MappingValidationError(
                f"mapping exceeds the container nesting limit (max depth {MAX_DEPTH})"
            )
        for key, child in value.items():
            if not isinstance(key, str):
                raise MappingValidationError("mapping keys must be strings")
            compact = re.sub(r"[^a-z0-9]", "", key.casefold())
            segments = [segment for segment in re.split(r"[^a-z0-9]+", key.casefold()) if segment]
            if (
                any(marker in compact for marker in _FORBIDDEN_KEY_MARKERS)
                or compact.endswith("path")
                or any(segment in _FORBIDDEN_INSTRUCTION_KEYS for segment in segments)
            ):
                raise MappingValidationError(f"mapping contains unsafe fields: {key}")
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if depth > MAX_DEPTH:
            raise MappingValidationError(
                f"mapping exceeds the container nesting limit (max depth {MAX_DEPTH})"
            )
        if len(value) > MAX_TOTAL_RULES:
            raise MappingValidationError("mapping list exceeds size limit")
        for child in value:
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, str):
        if _PATH_RE.search(value) or _SECRET_RE.search(value):
            raise MappingValidationError("mapping contains unsafe text")
        if len(value) > _SUSPICIOUS_TEXT_LENGTH:
            raise MappingValidationError("mapping contains overlong text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise MappingValidationError("mapping contains unsupported value")
    elif isinstance(value, float) and not math.isfinite(value):
        raise MappingValidationError("mapping contains non-finite number")


# ---------------------------------------------------------------------------
# Typed validation helpers
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _check_fields(
    raw: Any,
    *,
    path: str,
    required: frozenset[str] | set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> None:
    if not isinstance(raw, Mapping):
        raise MappingValidationError(f"{path} must be an object")
    present = set(raw)
    missing = set(required) - present
    if missing:
        raise MappingValidationError(
            f"{path} is missing required fields: {sorted(missing)}"
        )
    unknown = present - set(required) - set(optional)
    if unknown:
        raise MappingValidationError(f"{path} has unknown fields: {sorted(unknown)}")


def _require_text(value: Any, path: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingValidationError(f"{path} must be non-empty text")
    if len(value.strip()) > max_length:
        raise MappingValidationError(
            f"{path} exceeds the {max_length}-character limit"
        )
    return value


def _require_string_list(
    value: Any,
    path: str,
    *,
    allowed: frozenset[str] | None = None,
    vocabulary_section: str | None = None,
    allow_empty: bool = True,
    max_items: int = 64,
    max_item_length: int = MAX_LIST_TEXT_LENGTH,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MappingValidationError(f"{path} must be a list")
    if not allow_empty and not value:
        raise MappingValidationError(f"{path} must not be empty")
    if len(value) > max_items:
        raise MappingValidationError(f"{path} exceeds the {max_items}-item limit")
    items = [
        _require_text(item, f"{path}[{i}]", max_length=max_item_length)
        for i, item in enumerate(value)
    ]
    if len(set(items)) != len(items):
        raise MappingValidationError(f"{path} contains duplicates")
    if allowed is not None:
        for item in items:
            if item not in allowed:
                section = f" ({vocabulary_section})" if vocabulary_section else ""
                raise MappingValidationError(
                    f"{path} item is not in the frozen vocabulary{section}: {item}"
                )
    return items


def _vocab_set(vocabulary: Mapping[str, Any], section: str) -> frozenset[str]:
    values = vocabulary.get(section)
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise MappingValidationError(f"vocabulary section {section} is missing or invalid")
    return frozenset(values)


_ENUM_CONTRACT: dict[str, frozenset[str]] = {
    "severity": SEVERITIES,
    "direction": DIRECTIONS,
    "claim_level": CLAIM_LEVELS,
    "op": CONDITION_OPS,
    "input": CONDITION_INPUTS,
}


def _check_vocabulary(vocabulary: Any) -> None:
    if not isinstance(vocabulary, Mapping):
        raise MappingValidationError("vocabulary must be an object")
    enums = vocabulary.get("enums")
    if not isinstance(enums, Mapping):
        raise MappingValidationError("vocabulary enums section is missing")
    for name, constant in _ENUM_CONTRACT.items():
        values = enums.get(name)
        if (
            isinstance(values, (str, bytes))
            or not isinstance(values, Sequence)
            or set(values) != set(constant)
        ):
            raise MappingValidationError(
                f"vocabulary enums.{name} disagree with the {MAPPING_SCHEMA_VERSION} contract"
            )


# ---------------------------------------------------------------------------
# Rule-level validation
# ---------------------------------------------------------------------------

_TOP_LEVEL_FIELDS = {
    "schema_version", "static_clicking", "families", "archetypes", "root_causes",
}
_STATIC_REQUIRED = {"signal", "severity", "text", "claim_level", "metric_refs", "conditions"}
_STATIC_OPTIONAL = {
    "plain_language_meaning", "expected_result", "limitations",
    "observation_ref", "prescriptions",
}
_FAMILY_REQUIRED = {
    "signal", "metric", "row_field", "direction", "knowledge_metric_ref",
    "observation_ref",
}
_FAMILY_OPTIONAL = {
    "expected_entry_ref", "requires_metric_availability", "blocking_limitations",
    "guardrails", "row_filter", "claim_level", "requested_knowledge_sections",
}
_PRESCRIPTION_REQUIRED = {"scenario", "reason", "target_metrics", "expected_direction"}
_PRESCRIPTION_OPTIONAL = {
    "cue", "purpose", "retest_after", "stop_or_adjust_rule", "source_level",
}
_CONDITION_REQUIRED = {"input", "metric", "op", "value"}
_CONDITION_OPTIONAL = {"stat"}
_ARCHETYPE_FIELDS = {"id", "label", "conditions", "positive"}


def _validate_condition_value(op: str, value: Any, path: str) -> None:
    if op in COMPARISON_OPS:
        if not _is_number(value):
            raise MappingValidationError(
                f"{path}.value must be a finite number for op {op!r}"
            )
    elif op == "in_band":
        if (
            isinstance(value, (str, bytes))
            or not isinstance(value, Sequence)
            or len(value) != 2
        ):
            raise MappingValidationError(f"{path}.value must be a [lo, hi] pair for in_band")
        lo, hi = value
        if not _is_number(lo) or not _is_number(hi):
            raise MappingValidationError(
                f"{path}.value must be a [lo, hi] pair of finite numbers for in_band"
            )
        if not float(lo) < float(hi):
            raise MappingValidationError(
                f"{path}.value band needs lo < hi (got [{lo}, {hi}])"
            )
    elif op == "ratio_to_ref_lt":
        if not _is_number(value) or float(value) <= 0:
            raise MappingValidationError(
                f"{path}.value must be a positive number for ratio_to_ref_lt"
            )
    elif op == "metric_version_not_in":
        _require_string_list(
            value, f"{path}.value", allow_empty=False, max_items=16, max_item_length=200,
        )
    else:  # pragma: no cover - guarded by the op enum check
        raise MappingValidationError(f"{path}.op is not a v1 operator")


def _validate_condition(raw: Any, path: str, metric_keys: frozenset[str]) -> None:
    _check_fields(raw, path=path, required=_CONDITION_REQUIRED, optional=_CONDITION_OPTIONAL)
    if raw["input"] not in CONDITION_INPUTS:
        raise MappingValidationError(f"{path}.input is not a v1 input channel: {raw['input']!r}")
    if raw["metric"] not in metric_keys:
        raise MappingValidationError(
            f"{path}.metric is not in the frozen vocabulary (metric_keys): {raw['metric']!r}"
        )
    stat = raw.get("stat", CONDITION_DEFAULT_STAT)
    if stat not in CONDITION_STATS:
        raise MappingValidationError(f"{path}.stat must be omitted or 'med' in v1")
    if raw["op"] not in CONDITION_OPS:
        raise MappingValidationError(f"{path}.op is not a v1 operator: {raw['op']!r}")
    _validate_condition_value(raw["op"], raw["value"], path)


def _validate_prescription(raw: Any, index: int) -> None:
    path = f"prescriptions[{index}]"
    _check_fields(raw, path=path, required=_PRESCRIPTION_REQUIRED, optional=_PRESCRIPTION_OPTIONAL)
    _require_text(raw["scenario"], f"{path}.scenario", max_length=MAX_SCENARIO_LENGTH)
    _require_text(raw["reason"], f"{path}.reason", max_length=MAX_PRESCRIPTION_TEXT_LENGTH)
    for name in ("cue", "purpose", "stop_or_adjust_rule"):
        if name in raw:
            _require_text(raw[name], f"{path}.{name}", max_length=MAX_PRESCRIPTION_TEXT_LENGTH)
    if "retest_after" in raw:
        _require_text(raw["retest_after"], f"{path}.retest_after", max_length=MAX_RETEST_AFTER_LENGTH)
    for name in ("target_metrics", "expected_direction"):
        _require_string_list(raw[name], f"{path}.{name}", max_items=16)
    if "source_level" in raw and raw["source_level"] not in SOURCE_LEVELS:
        raise MappingValidationError(
            f"{path}.source_level is not a known source level: {raw['source_level']!r}"
        )


def _validate_static_rule(raw: Any, index: int, vocab: Mapping[str, Any]) -> None:
    path = f"static_clicking[{index}]"
    _check_fields(raw, path=path, required=_STATIC_REQUIRED, optional=_STATIC_OPTIONAL)
    if raw["signal"] not in _vocab_set(vocab, "signals"):
        raise MappingValidationError(
            f"{path}.signal is not in the frozen vocabulary (signals): {raw['signal']!r}"
        )
    if raw["severity"] not in SEVERITIES:
        raise MappingValidationError(f"{path}.severity is not a v1 severity")
    _require_text(raw["text"], f"{path}.text", max_length=MAX_TEXT_LENGTH)
    for name in ("plain_language_meaning", "expected_result"):
        if name in raw:
            _require_text(raw[name], f"{path}.{name}", max_length=MAX_MEANING_LENGTH)
    if raw["claim_level"] not in CLAIM_LEVELS:
        raise MappingValidationError(f"{path}.claim_level is not a v1 claim level")
    _require_string_list(
        raw["metric_refs"], f"{path}.metric_refs",
        allowed=_vocab_set(vocab, "metric_keys"), vocabulary_section="metric_keys",
        allow_empty=False,
    )
    if "limitations" in raw:
        _require_string_list(
            raw["limitations"], f"{path}.limitations",
            allowed=_vocab_set(vocab, "limitation_tokens"),
            vocabulary_section="limitation_tokens",
        )
    if "observation_ref" in raw and raw["observation_ref"] not in _vocab_set(vocab, "observation_refs"):
        raise MappingValidationError(
            f"{path}.observation_ref is not in the frozen vocabulary (observation_refs)"
        )
    conditions = raw["conditions"]
    if isinstance(conditions, (str, bytes)) or not isinstance(conditions, Sequence):
        raise MappingValidationError(f"{path}.conditions must be a list")
    if not MIN_CONDITIONS <= len(conditions) <= MAX_CONDITIONS:
        raise MappingValidationError(
            f"{path}.conditions must carry {MIN_CONDITIONS}-{MAX_CONDITIONS} conditions "
            f"(got {len(conditions)})"
        )
    for cond_index, condition in enumerate(conditions):
        _validate_condition(condition, f"{path}.conditions[{cond_index}]", _vocab_set(vocab, "metric_keys"))
    if "prescriptions" in raw:
        prescriptions = raw["prescriptions"]
        if isinstance(prescriptions, (str, bytes)) or not isinstance(prescriptions, Sequence):
            raise MappingValidationError(f"{path}.prescriptions must be a list")
        if len(prescriptions) > MAX_PRESCRIPTIONS:
            raise MappingValidationError(f"{path}.prescriptions exceeds the {MAX_PRESCRIPTIONS}-prescription cap")
        for pres_index, prescription in enumerate(prescriptions):
            _validate_prescription(prescription, pres_index)


def _find_registry_entry(registry: Any, reference: str) -> Mapping[str, Any] | None:
    """Resolve ``knowledge:<id>@<version>`` against an already-loaded registry.

    Mirrors the public behavior of ``knowledge_registry.resolve_entry`` (which
    re-loads a packaged registry by version string) without touching that
    module: same ``entry_ref`` comparison, then the caller checks status.
    """
    if not isinstance(registry, Mapping):
        raise MappingValidationError("cross-check registry must be an object")
    entries = registry.get("entries")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise MappingValidationError("cross-check registry has no entries list")
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_id = entry.get("entry_id")
        version = entry.get("entry_version")
        if not isinstance(entry_id, str):
            continue
        if isinstance(version, bool) or not isinstance(version, int):
            continue
        if entry_ref(entry) == reference:
            return entry
    return None


def _validate_family_rule(
    raw: Any,
    family: str,
    index: int,
    vocab: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    path = f"families.{family}[{index}]"
    _check_fields(raw, path=path, required=_FAMILY_REQUIRED, optional=_FAMILY_OPTIONAL)
    if raw["signal"] not in _vocab_set(vocab, "signals"):
        raise MappingValidationError(
            f"{path}.signal is not in the frozen vocabulary (signals): {raw['signal']!r}"
        )
    if raw["metric"] not in _vocab_set(vocab, "metric_keys"):
        raise MappingValidationError(
            f"{path}.metric is not in the frozen vocabulary (metric_keys): {raw['metric']!r}"
        )
    if raw["row_field"] not in _vocab_set(vocab, "row_fields"):
        raise MappingValidationError(
            f"{path}.row_field is not in the frozen vocabulary (row_fields)"
        )
    if raw["direction"] not in DIRECTIONS:
        raise MappingValidationError(f"{path}.direction is not a v1 direction")
    if raw["knowledge_metric_ref"] not in _vocab_set(vocab, "knowledge_metric_tokens"):
        raise MappingValidationError(
            f"{path}.knowledge_metric_ref is not in the frozen vocabulary "
            f"(knowledge_metric_tokens)"
        )
    if raw["observation_ref"] not in _vocab_set(vocab, "observation_refs"):
        raise MappingValidationError(
            f"{path}.observation_ref is not in the frozen vocabulary (observation_refs)"
        )
    if "expected_entry_ref" in raw:
        reference = raw["expected_entry_ref"]
        if not isinstance(reference, str) or not _ENTRY_REF_RE.fullmatch(reference):
            raise MappingValidationError(
                f"{path}.expected_entry_ref is not a knowledge entry reference"
            )
        resolved = _find_registry_entry(registry, reference)
        if resolved is None:
            raise MappingValidationError(
                f"{path}.expected_entry_ref cannot be resolved in the provided registry: {reference}"
            )
        if resolved.get("status") != "active":
            raise MappingValidationError(
                f"{path}.expected_entry_ref is not an active registry entry: {reference}"
            )
    if "requires_metric_availability" in raw and raw["requires_metric_availability"] != "available":
        raise MappingValidationError(f"{path}.requires_metric_availability only accepts 'available'")
    if "blocking_limitations" in raw:
        _require_string_list(
            raw["blocking_limitations"], f"{path}.blocking_limitations",
            allowed=_vocab_set(vocab, "limitation_tokens"),
            vocabulary_section="limitation_tokens",
        )
    if "guardrails" in raw:
        guardrails = raw["guardrails"]
        if not isinstance(guardrails, Mapping) or set(guardrails) != {"all"}:
            raise MappingValidationError(f"{path}.guardrails must be an object with an 'all' list")
        gates = guardrails["all"]
        if isinstance(gates, (str, bytes)) or not isinstance(gates, Sequence) or not gates:
            raise MappingValidationError(f"{path}.guardrails.all must be a non-empty list")
        for gate_index, gate in enumerate(gates):
            gate_path = f"{path}.guardrails.all[{gate_index}]"
            if not isinstance(gate, Mapping) or set(gate) != {"metric", "op"}:
                raise MappingValidationError(f"{gate_path} fields are invalid")
            if gate["metric"] not in _vocab_set(vocab, "metric_keys"):
                raise MappingValidationError(
                    f"{gate_path}.metric is not in the frozen vocabulary (metric_keys)"
                )
            if gate["op"] not in GUARDRAIL_OPS:
                raise MappingValidationError(f"{gate_path}.op is not a baseline operator")
            if gate["metric"] == raw["metric"]:
                raise MappingValidationError(
                    f"{path}.guardrails cannot gate the rule's own metric: {gate['metric']}"
                )
    if "row_filter" in raw and raw["row_filter"] not in _vocab_set(vocab, "row_filters"):
        raise MappingValidationError(
            f"{path}.row_filter is not in the frozen vocabulary (row_filters)"
        )
    if "claim_level" in raw and raw["claim_level"] not in CLAIM_LEVELS:
        raise MappingValidationError(f"{path}.claim_level is not a v1 claim level")
    if "requested_knowledge_sections" in raw:
        _require_string_list(
            raw["requested_knowledge_sections"], f"{path}.requested_knowledge_sections",
            allowed=KNOWLEDGE_SECTION_NAMES, allow_empty=False, max_items=len(KNOWLEDGE_SECTION_NAMES),
        )


def _validate_archetype(raw: Any, index: int, signals: frozenset[str]) -> str:
    path = f"archetypes[{index}]"
    _check_fields(raw, path=path, required=_ARCHETYPE_FIELDS)
    archetype_id = _require_text(raw["id"], f"{path}.id", max_length=MAX_ID_LENGTH)
    _require_text(raw["label"], f"{path}.label", max_length=MAX_ID_LENGTH)
    conditions = raw["conditions"]
    if not isinstance(conditions, Mapping):
        raise MappingValidationError(f"{path}.conditions must be an object")
    for signal, weight in conditions.items():
        if signal not in signals:
            raise MappingValidationError(
                f"{path}.conditions key is not in the frozen vocabulary (signals): {signal!r}"
            )
        if not _is_number(weight) or not 0 < float(weight) <= 1:
            raise MappingValidationError(
                f"{path}.conditions weight for {signal!r} must satisfy 0 < w <= 1"
            )
    if not isinstance(raw["positive"], bool):
        raise MappingValidationError(f"{path}.positive must be a boolean")
    if (len(conditions) == 0) != raw["positive"]:
        raise MappingValidationError(
            f"{path}.positive must be true exactly when conditions are empty "
            "(positive fallback archetypes)"
        )
    return archetype_id


def _validate_root_causes(raw: Any, signals: frozenset[str]) -> None:
    path = "root_causes"
    if not isinstance(raw, Mapping):
        raise MappingValidationError(f"{path} must be an object")
    if len(raw) > MAX_ROOT_CAUSES:
        raise MappingValidationError(f"{path} exceeds the {MAX_ROOT_CAUSES}-key cap")
    for signal, triple in raw.items():
        if signal not in signals:
            raise MappingValidationError(
                f"{path} key is not in the frozen vocabulary (signals): {signal!r}"
            )
        if (
            isinstance(triple, (str, bytes))
            or not isinstance(triple, Sequence)
            or len(triple) != 3
        ):
            raise MappingValidationError(
                f"{path}[{signal!r}] must be exactly three copy strings "
                "(symptom layer / physical layer / training layer)"
            )
        for i, item in enumerate(triple):
            _require_text(item, f"{path}[{signal!r}][{i}]", max_length=MAX_ROOT_CAUSE_TEXT_LENGTH)


# ---------------------------------------------------------------------------
# Public validation entry point
# ---------------------------------------------------------------------------


def validate_mapping(doc: Any, *, vocabulary: Mapping[str, Any], registry: Mapping[str, Any]) -> None:
    """Validate a ``coach_mapping.v1`` document; raise on the first violation.

    ``vocabulary`` is the frozen ``knowledge/mapping/vocabulary.v1.json``
    document; ``registry`` is a loaded knowledge registry (validated shape as
    produced by ``knowledge_registry.load_registry``) used to cross-check every
    ``expected_entry_ref`` as an active entry. Returns ``None`` on success.
    """
    if not isinstance(doc, Mapping):
        raise MappingValidationError("mapping document must be an object")
    _reject_unsafe_shape(doc)
    if doc.get("schema_version") != MAPPING_SCHEMA_VERSION:
        raise MappingValidationError(
            f"schema_version must be {MAPPING_SCHEMA_VERSION!r}"
        )
    _check_fields(doc, path="mapping", required=_TOP_LEVEL_FIELDS)
    _check_vocabulary(vocabulary)

    signals = _vocab_set(vocabulary, "signals")
    static_raw = doc["static_clicking"]
    if isinstance(static_raw, (str, bytes)) or not isinstance(static_raw, Sequence):
        raise MappingValidationError("static_clicking must be a list")
    families_raw = doc["families"]
    if not isinstance(families_raw, Mapping) or set(families_raw) != set(FAMILIES):
        raise MappingValidationError(
            f"families must be an object with exactly the v1 family keys: {', '.join(FAMILIES)}"
        )
    for family in FAMILIES:
        rules = families_raw[family]
        if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
            raise MappingValidationError(f"families.{family} must be a list")
    archetypes_raw = doc["archetypes"]
    if isinstance(archetypes_raw, (str, bytes)) or not isinstance(archetypes_raw, Sequence):
        raise MappingValidationError("archetypes must be a list")

    for index, rule in enumerate(static_raw):
        _validate_static_rule(rule, index, vocabulary)
    for family in FAMILIES:
        for index, rule in enumerate(families_raw[family]):
            _validate_family_rule(rule, family, index, vocabulary, registry)

    seen_archetype_ids: set[str] = set()
    for index, archetype in enumerate(archetypes_raw):
        archetype_id = _validate_archetype(archetype, index, signals)
        if archetype_id in seen_archetype_ids:
            raise MappingValidationError(
                f"archetypes[{index}].id duplicates an earlier archetype id: {archetype_id}"
            )
        seen_archetype_ids.add(archetype_id)
    _validate_root_causes(doc["root_causes"], signals)

    total = len(static_raw) + sum(len(families_raw[family]) for family in FAMILIES)
    if total > MAX_TOTAL_RULES:
        raise MappingValidationError(
            f"mapping carries {total} rules; the total cap is {MAX_TOTAL_RULES}"
        )
    if len(static_raw) > MAX_STATIC_RULES:
        raise MappingValidationError(
            f"static_clicking exceeds the {MAX_STATIC_RULES}-rule cap"
        )
    for family in FAMILIES:
        if len(families_raw[family]) > MAX_RULES_PER_FAMILY:
            raise MappingValidationError(
                f"families.{family} exceeds the {MAX_RULES_PER_FAMILY}-rule cap"
            )
    if len(archetypes_raw) > MAX_ARCHETYPES:
        raise MappingValidationError(f"archetypes exceeds the {MAX_ARCHETYPES}-item cap")


# ---------------------------------------------------------------------------
# Compilation (validation -> evaluation-friendly shapes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledCondition:
    input: str
    metric: str
    stat: str
    op: str
    value: "float | tuple[float, float] | tuple[str, ...]"


@dataclass(frozen=True)
class CompiledPrescription:
    scenario: str
    reason: str
    cue: str
    purpose: str
    target_metrics: tuple[str, ...]
    expected_direction: tuple[str, ...]
    retest_after: str
    stop_or_adjust_rule: str
    source_level: str


@dataclass(frozen=True)
class CompiledStaticRule:
    signal: str
    severity: str
    text: str
    plain_language_meaning: str
    expected_result: str
    claim_level: str
    metric_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    observation_ref: str | None
    conditions: tuple[CompiledCondition, ...]
    prescriptions: tuple[CompiledPrescription, ...]


@dataclass(frozen=True)
class CompiledFamilyRule:
    family: str
    signal: str
    metric: str
    row_field: str
    direction: str
    knowledge_metric_ref: str
    expected_entry_ref: str | None
    observation_ref: str
    requires_metric_availability: str
    blocking_limitations: frozenset[str]
    guardrails: tuple[tuple[str, str], ...]  # (metric, op)
    row_filter: str | None
    claim_level: str
    requested_knowledge_sections: tuple[str, ...]


@dataclass(frozen=True)
class CompiledArchetype:
    id: str
    label: str
    conditions: dict[str, float]
    positive: bool


@dataclass(frozen=True)
class CompiledMapping:
    schema_version: str
    static_rules: tuple[CompiledStaticRule, ...]
    family_rules: dict[str, tuple[CompiledFamilyRule, ...]]
    archetypes: tuple[CompiledArchetype, ...]
    root_causes: dict[str, tuple[str, str, str]]
    total_rule_count: int
    source_path: str | None


def _compile_value(op: str, value: Any) -> "float | tuple[float, float] | tuple[str, ...]":
    if op == "in_band":
        lo, hi = value
        return (float(lo), float(hi))
    if op == "metric_version_not_in":
        return tuple(str(item) for item in value)
    return float(value)


def _compile_condition(raw: Mapping[str, Any]) -> CompiledCondition:
    op = raw["op"]
    return CompiledCondition(
        input=raw["input"],
        metric=raw["metric"],
        stat=raw.get("stat", CONDITION_DEFAULT_STAT),
        op=op,
        value=_compile_value(op, raw["value"]),
    )


def _compile_prescription(raw: Mapping[str, Any]) -> CompiledPrescription:
    return CompiledPrescription(
        scenario=raw["scenario"],
        reason=raw["reason"],
        cue=raw.get("cue", ""),
        purpose=raw.get("purpose", ""),
        target_metrics=tuple(raw["target_metrics"]),
        expected_direction=tuple(raw["expected_direction"]),
        retest_after=raw.get("retest_after", ""),
        stop_or_adjust_rule=raw.get("stop_or_adjust_rule", ""),
        source_level=raw.get("source_level", DEFAULT_PRESCRIPTION_SOURCE_LEVEL),
    )


def _compile_static_rule(raw: Mapping[str, Any]) -> CompiledStaticRule:
    return CompiledStaticRule(
        signal=raw["signal"],
        severity=raw["severity"],
        text=raw["text"],
        plain_language_meaning=raw.get("plain_language_meaning", ""),
        expected_result=raw.get("expected_result", ""),
        claim_level=raw["claim_level"],
        metric_refs=tuple(raw["metric_refs"]),
        limitations=tuple(raw.get("limitations", ())),
        observation_ref=raw.get("observation_ref"),
        conditions=tuple(_compile_condition(c) for c in raw["conditions"]),
        prescriptions=tuple(_compile_prescription(p) for p in raw.get("prescriptions", ())),
    )


def _compile_family_rule(raw: Mapping[str, Any], family: str) -> CompiledFamilyRule:
    guardrails = raw.get("guardrails")
    return CompiledFamilyRule(
        family=family,
        signal=raw["signal"],
        metric=raw["metric"],
        row_field=raw["row_field"],
        direction=raw["direction"],
        knowledge_metric_ref=raw["knowledge_metric_ref"],
        expected_entry_ref=raw.get("expected_entry_ref"),
        observation_ref=raw["observation_ref"],
        requires_metric_availability=raw.get("requires_metric_availability", "available"),
        blocking_limitations=frozenset(raw.get("blocking_limitations", ())),
        guardrails=(
            tuple((gate["metric"], gate["op"]) for gate in guardrails["all"])
            if guardrails is not None
            else ()
        ),
        row_filter=raw.get("row_filter"),
        claim_level=raw.get("claim_level", DEFAULT_FAMILY_CLAIM_LEVEL),
        requested_knowledge_sections=tuple(
            raw.get("requested_knowledge_sections", DEFAULT_REQUESTED_KNOWLEDGE_SECTIONS)
        ),
    )


def _compile_archetype(raw: Mapping[str, Any]) -> CompiledArchetype:
    return CompiledArchetype(
        id=raw["id"],
        label=raw["label"],
        conditions={str(signal): float(weight) for signal, weight in raw["conditions"].items()},
        positive=raw["positive"],
    )


def _build_compiled(doc: Mapping[str, Any], *, source_path: str | None) -> CompiledMapping:
    static_rules = tuple(_compile_static_rule(rule) for rule in doc["static_clicking"])
    family_rules: dict[str, tuple[CompiledFamilyRule, ...]] = {}
    total = len(static_rules)
    for family in FAMILIES:
        rules = tuple(_compile_family_rule(rule, family) for rule in doc["families"][family])
        family_rules[family] = rules
        total += len(rules)
    archetypes = tuple(_compile_archetype(archetype) for archetype in doc["archetypes"])
    root_causes = {
        str(signal): (str(triple[0]), str(triple[1]), str(triple[2]))
        for signal, triple in doc["root_causes"].items()
    }
    return CompiledMapping(
        schema_version=doc["schema_version"],
        static_rules=static_rules,
        family_rules=family_rules,
        archetypes=archetypes,
        root_causes=root_causes,
        total_rule_count=total,
        source_path=source_path,
    )


_COMPILE_CACHE: dict[tuple[str, int, int], CompiledMapping] = {}
_COMPILE_CACHE_MAX_ENTRIES = 8


def compile_mapping(doc: Mapping[str, Any], *, path: str | Path | None = None) -> CompiledMapping:
    """Compile a *validated* mapping document into evaluation-friendly shapes.

    Precondition: ``validate_mapping`` already accepted ``doc``; compilation
    does not re-validate. With ``path`` given, the compiled result is cached
    under ``(path, mtime_ns, size)`` so repeated loads of an unchanged file
    return the same object; any file change (or a missing/unreadable path)
    recompiles. Without ``path``, a fresh compilation is returned every time.
    """
    key: tuple[str, int, int] | None = None
    if path is not None:
        try:
            stat_result = Path(path).stat()
        except OSError:
            key = None
        else:
            key = (str(Path(path)), stat_result.st_mtime_ns, stat_result.st_size)
    if key is not None:
        cached = _COMPILE_CACHE.get(key)
        if cached is not None:
            return cached
    compiled = _build_compiled(doc, source_path=str(path) if path is not None else None)
    if key is not None:
        while len(_COMPILE_CACHE) >= _COMPILE_CACHE_MAX_ENTRIES:
            _COMPILE_CACHE.pop(next(iter(_COMPILE_CACHE)))
        _COMPILE_CACHE[key] = compiled
    return compiled


# ---------------------------------------------------------------------------
# Static evaluation (WP-04) — plan C1 fail-closed semantics
# ---------------------------------------------------------------------------
# Missing data never triggers a rule; a rule error drops that rule with a
# recorded diagnostic while the analysis succeeds; a broken/unusable mapping
# falls back to the frozen built-in ``advice.advise`` (a missing mapping file
# is that same normal fallback state, not an error). Diagnosis sentences that
# the built-in interpolates with runtime metric values are carried in the
# mapping ``text`` as ``str.format`` templates; the slot values are computed
# here (evaluation mechanism stays in Python, plan C1).

# Official vocabulary resource: same resource-root rule as knowledge_active
# (mirrored here, not imported, to keep the private constant uncoupled). The
# vocabulary is product-owned and never pack-replaced (plan C7).
_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
_VOCABULARY_PATH = (
    Path(_RESOURCE_ROOT) / "knowledge" / "mapping" / "vocabulary.v1.json"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[2] / "knowledge" / "mapping" / "vocabulary.v1.json"
)

_VOCAB_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_VOCAB_CACHE_MAX_ENTRIES = 4

_static_diagnostics: list[str] = []


def last_static_diagnostics() -> list[str]:
    """Diagnostics recorded by the most recent :func:`dispatch_static` call
    (dropped rules, fallback reasons). Mirrors
    ``knowledge_active.last_fallback_reason``; the analysis never fails
    because of them."""
    return list(_static_diagnostics)


def _load_vocabulary() -> dict[str, Any]:
    """Load the frozen vocabulary document (mtime-keyed cache, like the
    compile cache). Any failure raises, so ``dispatch_static`` fails closed
    to the built-in engine."""
    try:
        stat_result = _VOCABULARY_PATH.stat()
    except OSError as exc:
        raise MappingValidationError(f"mapping vocabulary is unavailable: {exc}") from exc
    key = (str(_VOCABULARY_PATH), stat_result.st_mtime_ns, stat_result.st_size)
    cached = _VOCAB_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        doc = json.loads(_VOCABULARY_PATH.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MappingValidationError(f"mapping vocabulary is unreadable: {exc}") from exc
    if not isinstance(doc, dict):
        raise MappingValidationError("mapping vocabulary is not a JSON object")
    while len(_VOCAB_CACHE) >= _VOCAB_CACHE_MAX_ENTRIES:
        _VOCAB_CACHE.pop(next(iter(_VOCAB_CACHE)))
    _VOCAB_CACHE[key] = doc
    return doc


def _static_text_slots(
    signal: str,
    *,
    summary: Mapping[str, Any],
    reference: Mapping[str, Any] | None,
    settings: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Named ``str.format`` slot values for a static rule's diagnosis text.

    Slot names mirror the quantities the built-in f-strings interpolate
    (``advice.py``); format specs stay in the mapping text. The values reuse
    ``advice._med`` so number coercion cannot drift from the built-in engine.
    """
    def med(document: Mapping[str, Any] | None, metric: str) -> float:
        value = advice._med(document or {}, metric)
        if value is None:
            raise ValueError(f"{signal}: missing med for {metric}")
        return value

    if signal in ("decel_frac high", "decel_frac low"):
        return {"decel_frac_pct": med(summary, "decel_frac") * 100.0}
    if signal == "linearity high":
        return {"linearity_med": med(summary, "linearity")}
    if signal == "sparc low":
        return {"sparc_med": med(summary, "sparc")}
    if signal == "reverse_ratio high":
        return {"reverse_ratio_pct": med(summary, "reverse_ratio") * 100.0}
    if signal in ("peak_position low", "peak_position high"):
        return {"peak_position_pct": med(summary, "peak_position_pct")}
    if signal == "path_efficiency low":
        return {"path_efficiency": med(summary, "path_efficiency")}
    if signal == "peak_speed below reference":
        self_peak = med(summary, "peak_speed_deg")
        ref_peak = med(reference, "peak_speed_deg")
        return {
            "self_peak": self_peak,
            "ref_peak": ref_peak,
            "ratio_pct": self_peak / ref_peak * 100.0,
        }
    if signal == "throughput below reference":
        self_throughput = med(summary, "throughput")
        ref_throughput = med(reference, "throughput")
        return {
            "self_throughput": self_throughput,
            "ref_throughput": ref_throughput,
            "throughput_ratio_pct": self_throughput / ref_throughput * 100.0,
        }
    if signal == "sensitivity high":
        value = settings.get("cm_per_360") if isinstance(settings, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("sensitivity high: missing settings.cm_per_360")
        return {"cm_per_360": float(value)}
    # Signals without interpolated values (e.g. "submovement two-stage") and
    # pack-authored signals without slots need no context at all.
    return {}


def _channel_med(condition: CompiledCondition, *, summary, reference) -> float | None:
    if condition.input == "self_summary":
        return advice._med(summary or {}, condition.metric)
    if condition.input == "reference_summary":
        if reference is None:
            return None
        return advice._med(reference, condition.metric)
    return None  # settings channel carries raw values, not {med: ...} entries


def _channel_version(condition: CompiledCondition, *, summary, reference) -> str | None:
    if condition.input == "self_summary":
        return advice._metric_version(summary or {}, condition.metric)
    if condition.input == "reference_summary" and reference is not None:
        return advice._metric_version(reference, condition.metric)
    return None


def _settings_value(condition: CompiledCondition, settings) -> float | None:
    if not isinstance(settings, Mapping):
        return None
    value = settings.get(condition.metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _condition_holds(
    condition: CompiledCondition,
    *,
    summary,
    reference,
    settings,
) -> bool:
    if condition.op == "ratio_to_ref_lt":
        # advice.py guards `if self and ref:` before dividing: a missing or
        # zero median on either side means "no signal", never a 0% ratio.
        self_value = advice._med(summary or {}, condition.metric)
        ref_value = advice._med(reference, condition.metric) if reference is not None else None
        if self_value is None or ref_value is None:
            return False
        if not self_value or not ref_value:
            return False
        return (self_value / ref_value) < condition.value
    if condition.op == "metric_version_not_in":
        # advice._metric_version normalizes to a non-empty str or None; a
        # missing/unversioned metric passes the gate (None is not in the set).
        version = _channel_version(condition, summary=summary, reference=reference)
        return version is None or version not in condition.value
    if condition.input == "settings":
        value = _settings_value(condition, settings)
    else:
        value = _channel_med(condition, summary=summary, reference=reference)
    if value is None:
        return False  # missing data never triggers (plan C1)
    if condition.op == ">":
        return value > condition.value
    if condition.op == "<":
        return value < condition.value
    if condition.op == ">=":
        return value >= condition.value
    if condition.op == "<=":
        return value <= condition.value
    if condition.op == "in_band":
        # Open interval (boundaries excluded): pinned to the built-in strict
        # peak_position thresholds, where 30.0/60.0 stay silent (< 30 / > 60).
        lo, hi = condition.value
        return lo < value < hi
    raise ValueError(f"unsupported static condition op: {condition.op!r}")


def _build_static_finding(
    rule: CompiledStaticRule,
    *,
    summary,
    reference,
    settings,
) -> advice.Finding:
    slots = _static_text_slots(
        rule.signal, summary=summary, reference=reference, settings=settings,
    )
    return advice.Finding(
        signal=rule.signal,
        severity=rule.severity,
        diagnosis=rule.text.format(**slots),
        prescriptions=[
            advice.Prescription(
                scenario=item.scenario,
                reason=item.reason,
                cue=item.cue,
                purpose=item.purpose,
                target_metrics=list(item.target_metrics),
                expected_direction=list(item.expected_direction),
                retest_after=item.retest_after,
                stop_or_adjust_rule=item.stop_or_adjust_rule,
                source_level=item.source_level,
            )
            for item in rule.prescriptions
        ],
        claim_level=rule.claim_level,
        metric_refs=list(rule.metric_refs),
        event_refs=[],
        limitations=list(rule.limitations),
        plain_language_meaning=rule.plain_language_meaning,
        expected_result=rule.expected_result,
        verification={},
    )


def evaluate_static(
    compiled: CompiledMapping,
    summary,
    reference=None,
    settings=None,
) -> list[advice.Finding]:
    """Evaluate compiled static rules into built-in-shape ``Finding`` objects.

    The result must stay deep-equal to ``advice.advise`` for the same inputs
    (golden harness contract). The unified calibration finalizer
    (``advice._finalize_uncalibrated_findings``) runs on the engine output
    exactly as on the built-in output, so severity/claim_level/metric_refs/
    limitations/meanings/prescription completion stay single-sourced there.
    A rule-level error drops that rule and records a diagnostic; the rest of
    the analysis continues (plan C1).
    """
    findings: list[advice.Finding] = []
    for rule in compiled.static_rules:
        try:
            triggered = all(
                _condition_holds(
                    condition, summary=summary, reference=reference, settings=settings,
                )
                for condition in rule.conditions
            )
            if triggered:
                findings.append(
                    _build_static_finding(
                        rule, summary=summary, reference=reference, settings=settings,
                    )
                )
        except Exception as exc:
            _static_diagnostics.append(f"static rule dropped: {rule.signal}: {exc}")
    advice._finalize_uncalibrated_findings(findings)
    return findings


def _fallback_cm_per_360(settings) -> float | None:
    """``advise``'s cm_per_360 parameter extracted from the settings channel."""
    if isinstance(settings, Mapping):
        value = settings.get("cm_per_360")
        if not isinstance(value, bool) and isinstance(value, (int, float)):
            return float(value)
    return None


def dispatch_static(summary, reference=None, settings=None):
    """Production static diagnosis path (plan C5).

    Loads the active mapping (``knowledge_active.load_active_mapping``),
    validates + compiles + evaluates it, and returns ``list[Finding]`` shaped
    exactly like ``advice.advise``. A missing mapping file is the normal
    built-in fallback state (not an error); any other failure (validation,
    compilation, evaluation, active resolution) fails closed to the frozen
    built-in ``advice.advise`` with a recorded diagnostic. Diagnostics of the
    latest call are readable via :func:`last_static_diagnostics`.
    """
    _static_diagnostics.clear()
    try:
        doc, _reason = knowledge_active.load_active_mapping()
        if doc is None:
            return advice.advise(summary, reference, _fallback_cm_per_360(settings))
        validate_mapping(
            doc,
            vocabulary=_load_vocabulary(),
            registry=knowledge_active.load_active_registry(),
        )
        compiled = compile_mapping(doc)
        return evaluate_static(
            compiled, summary, reference=reference, settings=settings,
        )
    except Exception as exc:  # fail-closed to the frozen built-in rules
        _static_diagnostics.append(f"active mapping unusable, built-in fallback: {exc}")
        return advice.advise(summary, reference, _fallback_cm_per_360(settings))


def make_family_advice_fn(family: str):
    """C5 worker wiring for family analyses (worker.py binds this once).

    Returns a closure with the built-in builder signature
    ``(analysis) -> list[dict]``. On each call it loads the active mapping
    (``knowledge_active.load_active_mapping``), validates + compiles it, and
    evaluates the family through :func:`evaluate_family` when the active
    mapping carries rules for it. A missing mapping file or an empty family
    section is the normal frozen built-in fallback (not an error); any other
    failure (validation, compilation, evaluation, active resolution) fails
    closed to the frozen ``advice_*.py`` builder with a recorded diagnostic.
    Diagnostics of the latest call are readable via
    :func:`last_family_diagnostics`.
    """
    from .. import advice_dynamic_clicking
    from .. import advice_target_switching
    from .. import advice_tracking

    builders = {
        "continuous_tracking": advice_tracking.build_tracking_candidate_advice,
        "dynamic_clicking": advice_dynamic_clicking.build_dynamic_clicking_candidate_advice,
        "target_switching": advice_target_switching.build_target_switching_candidate_advice,
    }
    if not isinstance(family, str) or family not in builders:
        raise ValueError(f"unknown family: {family!r}")
    builtin_builder = builders[family]

    def advice_fn(analysis):
        _family_diagnostics.clear()
        try:
            doc, _reason = knowledge_active.load_active_mapping()
            if doc is None:
                return builtin_builder(analysis)
            validate_mapping(
                doc,
                vocabulary=_load_vocabulary(),
                registry=knowledge_active.load_active_registry(),
            )
            compiled = compile_mapping(doc)
            if not compiled.family_rules.get(family, ()):
                return builtin_builder(analysis)
            return evaluate_family(family, compiled, analysis)
        except Exception as exc:  # fail-closed to the frozen built-in rules
            _family_diagnostics.append(
                f"active mapping unusable for {family}, built-in fallback: {exc}"
            )
            return builtin_builder(analysis)

    return advice_fn


# ---------------------------------------------------------------------------
# Family evaluation (WP-05) — unified fail-closed semantics (plan C1/C5)
# ---------------------------------------------------------------------------
# One evaluator for all three families, mirroring the frozen built-ins
# (advice_tracking / advice_dynamic_clicking / advice_target_switching).
#
# Family-level preconditions (hard-coded, uniform): ``support_status ==
# "outcome_only"`` or a missing/non-comparable comparison or missing
# metrics/baseline maps produce no candidates. Rule-level gates, in order:
# metric availability, worse-than-baseline trigger (equal to baseline never
# triggers), guardrails (``guardrails.all`` must all hold; ``<=baseline`` /
# ``>=baseline`` include equality), blocking limitations (metric limitations
# intersecting ``blocking_limitations`` drop the rule), row splitting
# (supporting = row value worse than baseline, counterexample = not worse; a
# named ``row_filter`` narrows the evidence pool — v1 whitelist:
# ``observable_switch_chain``), then knowledge resolution through
# ``resolve_candidate_knowledge_refs``.
#
# Unified fail-closed knowledge semantics (decided 2026-09-20, no switch): a
# rule carrying ``expected_entry_ref`` is dropped unless the resolved refs
# contain it; a rule without one is dropped when the resolved refs are empty.
# The second half intentionally changes dynamic_clicking, whose frozen
# built-in kept candidates with empty refs (golden fixture notes the change).
#
# Rules carrying a ``row_filter`` additionally (a) require at least one
# supporting row inside the filtered evidence pool and (b) carry
# ``verification_targets`` and merge filtered-row limitations into the
# candidate — the v1 observable_switch_chain contract of target_switching.
# A rule-level error drops only that rule and records a diagnostic; the
# analysis never fails because of it.

_family_diagnostics: list[str] = []


def last_family_diagnostics() -> list[str]:
    """Diagnostics recorded by the most recent family engine closure call
    (dropped rules, fallback reasons). Mirrors
    :func:`last_static_diagnostics`; the analysis never fails because of
    them."""
    return list(_family_diagnostics)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _entry_number(value: Any) -> float | None:
    """Number from a metric/baseline entry: ``{value}``, or an
    availability-gated ``{availability, value}`` dict, or a raw number.

    Availability-gated (unavailable entries read as missing) so the
    ``requires_metric_availability`` gate is uniform across families; raw
    numbers cover the baseline_metrics shape the built-ins compare against.
    """
    if isinstance(value, Mapping):
        if value.get("availability") == "unavailable":
            return None
        value = value.get("value")
    return _finite_number(value)


def _is_worse_than_baseline(
    current: float, baseline: float, direction: str,
) -> bool:
    """Same口径 as the frozen tracking built-in: ``higher``/``lower``
    compare signed values (equal never counts as worse),
    ``absolute_higher`` compares magnitudes."""
    if direction == "higher":
        return current > baseline
    if direction == "absolute_higher":
        return abs(current) > abs(baseline)
    return current < baseline


def _rule_metric_current(metrics: Mapping[str, Any], rule: CompiledFamilyRule) -> float | None:
    metric = metrics.get(rule.metric)
    if not isinstance(metric, Mapping):
        return None
    if metric.get("availability", "available") != rule.requires_metric_availability:
        return None  # requires_metric_availability gate: unavailable never triggers
    return _finite_number(metric.get("value"))


def _guardrails_hold(
    rule: CompiledFamilyRule,
    metrics: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
) -> bool:
    for gate_metric, op in rule.guardrails:
        current = _entry_number(metrics.get(gate_metric))
        baseline = _entry_number(baseline_metrics.get(gate_metric))
        if current is None or baseline is None:
            return False  # a guardrail without both numbers cannot hold
        if op == "<=baseline":
            if not current <= baseline:
                return False
        elif op == ">=baseline":
            if not current >= baseline:
                return False
        else:  # pragma: no cover - guarded by the GUARDRAIL_OPS enum check
            raise ValueError(f"unsupported guardrail op: {op!r}")
    return True


def _evidence_rows(analysis: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = analysis.get("processed_rows") or []
    return [
        row for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("event_ref"), str)
    ]


def _observable_switch_chain(row: Mapping[str, Any]) -> bool:
    """Same口径 as the frozen switching built-in: classification decides when
    present, row_kind is the fallback."""
    classification = row.get("classification")
    if classification == "stats_bounded_switch_chain":
        return row.get("row_kind") == "switch_chain"
    if classification is not None:
        return classification == "observable_target_switch"
    return row.get("row_kind") in {"target_switch_chain", "switch_chain"}


def _rule_evidence_pool(
    rule: CompiledFamilyRule, rows: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if rule.row_filter is None:
        return rows
    if rule.row_filter != "observable_switch_chain":
        # validate_mapping only admits vocabulary row_filters; anything else
        # here is a contract breach, so fail closed (drop via rule error).
        raise ValueError(f"unsupported row filter: {rule.row_filter!r}")
    return [row for row in rows if _observable_switch_chain(row)]


def _split_rows(
    rows: list[Mapping[str, Any]],
    rule: CompiledFamilyRule,
    baseline: float,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    supporting = []
    counterexample = []
    for row in rows:
        value = _entry_number(row.get(rule.row_field))
        if value is None:
            continue
        if _is_worse_than_baseline(value, baseline, rule.direction):
            supporting.append(row)
        else:
            counterexample.append(row)
    return supporting, counterexample


def _rule_knowledge_refs(
    rule: CompiledFamilyRule,
) -> tuple[str, list[str]]:
    """Resolve knowledge refs; apply the unified fail-closed narrowing.

    Returns (registry_version, refs); empty ``refs`` means the rule is
    dropped (expected_entry_ref missing from the resolved top-3, or an empty
    resolution when no expected ref is pinned — the intentional dynamic
    unification). Import happens at call time so test harnesses can stub the
    resolver on the diagnosis module, same as the frozen built-ins.
    """
    from .diagnosis import resolve_candidate_knowledge_refs

    knowledge = resolve_candidate_knowledge_refs(
        issue_signal=rule.signal,
        metric_refs=[rule.knowledge_metric_ref],
    )
    refs = knowledge.entry_refs
    if rule.expected_entry_ref is not None:
        refs = [ref for ref in refs if ref == rule.expected_entry_ref]
    return knowledge.registry_version, refs


def _rule_limitations(
    rule: CompiledFamilyRule,
    analysis: Mapping[str, Any],
    supporting_rows: list[Mapping[str, Any]],
    counterexample_rows: list[Mapping[str, Any]],
) -> list[Any]:
    if rule.row_filter is None:
        return list(analysis.get("limitations") or [])
    row_limitations = [
        limitation
        for row in [*supporting_rows, *counterexample_rows]
        for limitation in row.get("limitations", [])
        if isinstance(limitation, str)
    ]
    return sorted(set([
        *(
            item for item in analysis.get("limitations", [])
            if isinstance(item, str)
        ),
        *row_limitations,
    ]))


def _build_family_candidate(
    rule: CompiledFamilyRule,
    *,
    analysis: Mapping[str, Any],
    current: float,
    baseline: float,
    supporting_rows: list[Mapping[str, Any]],
    counterexample_rows: list[Mapping[str, Any]],
    registry_version: str,
    knowledge_entry_refs: list[str],
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "signal": rule.signal,
        "claim_level": rule.claim_level,
        "metric_refs": [rule.metric],
        "observation": {
            "current": current,
            "matched_baseline": baseline,
            "delta": current - baseline,
        },
        "supporting_row_refs": [row["event_ref"] for row in supporting_rows],
        "counterexample_row_refs": [
            row["event_ref"] for row in counterexample_rows
        ],
        "observation_ref": rule.observation_ref,
        "knowledge_registry_version": registry_version,
        "knowledge_entry_refs": knowledge_entry_refs,
        "requested_knowledge_sections": list(rule.requested_knowledge_sections),
    }
    if rule.row_filter is not None:
        # Row-evidence-gated candidates stay verifiable per row (the v1
        # switching contract).
        candidate["verification_targets"] = [{
            "metric_ref": rule.metric,
            "expected_direction": "lower_better",
            "condition": "matched_comparable_baseline",
        }]
    candidate["limitations"] = _rule_limitations(
        rule, analysis, supporting_rows, counterexample_rows,
    )
    return candidate


def evaluate_family(
    family: str,
    compiled: CompiledMapping,
    family_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate compiled family rules into the built-in candidate-observation
    shape (plan C5: same keys as ``build_*_candidate_advice``).

    The result must stay deep-equal to the frozen built-in builder for the
    same inputs (golden harness contract), except the unified fail-closed
    knowledge-refs drop documented in the section header. A rule-level error
    drops that rule and records a diagnostic via
    :func:`last_family_diagnostics`; the rest of the analysis continues.
    """
    if family not in FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    analysis = family_result
    if analysis.get("support_status") == "outcome_only":
        return []
    comparison = analysis.get("comparison")
    if not isinstance(comparison, Mapping) or comparison.get("comparable") is not True:
        return []
    metrics = analysis.get("metrics")
    baseline_metrics = comparison.get("baseline_metrics")
    if not isinstance(metrics, Mapping) or not isinstance(baseline_metrics, Mapping):
        return []
    rows = _evidence_rows(analysis)

    candidates: list[dict[str, Any]] = []
    for rule in compiled.family_rules.get(family, ()):
        try:
            current = _rule_metric_current(metrics, rule)
            baseline = _entry_number(baseline_metrics.get(rule.metric))
            if (
                current is None
                or baseline is None
                or not _is_worse_than_baseline(current, baseline, rule.direction)
            ):
                continue
            if rule.blocking_limitations:
                metric = metrics.get(rule.metric)
                metric_limitations = (
                    set(metric.get("limitations") or [])
                    if isinstance(metric, Mapping)
                    else set()
                )
                if metric_limitations.intersection(rule.blocking_limitations):
                    continue
            if rule.guardrails and not _guardrails_hold(rule, metrics, baseline_metrics):
                continue
            evidence_pool = _rule_evidence_pool(rule, rows)
            supporting_rows, counterexample_rows = _split_rows(
                evidence_pool, rule, baseline,
            )
            if rule.row_filter is not None and not supporting_rows:
                continue  # row-filtered rules claim evidence from that pool
            registry_version, knowledge_entry_refs = _rule_knowledge_refs(rule)
            if not knowledge_entry_refs:
                continue  # unified fail-closed (plan C1, decided 2026-09-20)
            candidates.append(_build_family_candidate(
                rule,
                analysis=analysis,
                current=current,
                baseline=baseline,
                supporting_rows=supporting_rows,
                counterexample_rows=counterexample_rows,
                registry_version=registry_version,
                knowledge_entry_refs=knowledge_entry_refs,
            ))
        except Exception as exc:
            _family_diagnostics.append(
                f"family rule dropped: {family}:{rule.signal}: {exc}"
            )
    return candidates


__all__ = [
    "MAPPING_SCHEMA_VERSION",
    "MAX_TOTAL_RULES", "MAX_STATIC_RULES", "MAX_RULES_PER_FAMILY",
    "MAX_ARCHETYPES", "MAX_ROOT_CAUSES", "MAX_DEPTH", "MAX_FILE_BYTES",
    "MIN_CONDITIONS", "MAX_CONDITIONS", "MAX_PRESCRIPTIONS",
    "MAX_TEXT_LENGTH", "MAX_MEANING_LENGTH", "MAX_SCENARIO_LENGTH",
    "MAX_PRESCRIPTION_TEXT_LENGTH", "MAX_RETEST_AFTER_LENGTH",
    "MAX_LIST_TEXT_LENGTH", "MAX_ROOT_CAUSE_TEXT_LENGTH", "MAX_ID_LENGTH",
    "SEVERITIES", "DIRECTIONS", "CLAIM_LEVELS", "CONDITION_INPUTS",
    "CONDITION_OPS", "COMPARISON_OPS", "CONDITION_STATS", "GUARDRAIL_OPS",
    "CONDITION_DEFAULT_STAT", "DEFAULT_FAMILY_CLAIM_LEVEL",
    "DEFAULT_PRESCRIPTION_SOURCE_LEVEL", "DEFAULT_REQUESTED_KNOWLEDGE_SECTIONS",
    "KNOWLEDGE_SECTION_NAMES", "SOURCE_LEVELS", "FAMILIES",
    "MappingValidationError",
    "CompiledCondition", "CompiledPrescription", "CompiledStaticRule",
    "CompiledFamilyRule", "CompiledArchetype", "CompiledMapping",
    "validate_mapping", "compile_mapping",
    "evaluate_static", "dispatch_static", "make_family_advice_fn",
    "last_static_diagnostics",
    "evaluate_family", "last_family_diagnostics",
]
