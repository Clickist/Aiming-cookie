"""Export the frozen mapping vocabulary (knowledge/mapping/vocabulary.v1.json).

WP-01 of the knowledge-SDK plan (2026-09-20): the vocabulary is the authoring
contract for ``coach_mapping.v1`` (C7). Every value is extracted from real
code constants; nothing is invented here:

- signals            union of analyzer-emitted signals (advice/advice_*.py
                     constants + Finding literals + family candidate tuples)
                     and active official registry entry signals.
- metric_keys        metric_definitions.METRIC_DEFINITIONS keys plus the
                     settings-channel key "cm_per_360" (C1 settings input,
                     ``advice.advise(cm_per_360=...)`` parameter).
- knowledge_metric_tokens  registry entry metric_refs ("metric:...") plus the
                     tokens the family candidate builders pass to
                     ``resolve_candidate_knowledge_refs``.
- observation_refs   diagnosis._STATIC_OBSERVATION_REFS values plus the
                     observation_ref constants in the three family candidate
                     builders.
- limitation_tokens  AST harvest of limitation string literals in the advice
                     modules and the family/visual analyzers (append/assign/
                     dict/compare/for-return contexts), plus two curated
                     tokens returned from helpers whose result is bound into a
                     limitation-named variable by the caller.
- row_fields         the per-row split field names from the candidate tuples.
- row_classifications AST harvest of classification/row_kind values in the
                     target-switching analyzer and its row filter.
- row_filters/enums  named filter and operator/value enums from the C1/C7
                     contract (engine-internal names; no code constant yet).

The output is deterministic: rerunning the script must not change the file
(idempotent). The committed artifact must stay in sync with the code; the
anti-drift test ``tests/coach/test_mapping_vocabulary.py`` re-derives every
section and fails when a new code constant is not registered.

stdlib only; Python 3.11. Run from the repo root:
    .venv/Scripts/python.exe scripts/export_mapping_vocabulary.py
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kovaak_tracker import (  # noqa: E402
    advice,
    advice_dynamic_clicking,
    advice_target_switching,
    advice_tracking,
    metric_definitions,
)
from kovaak_tracker.coach import diagnosis, profiles  # noqa: E402
from kovaak_tracker.coach.knowledge_registry import load_registry  # noqa: E402

SCHEMA_VERSION = "coach_mapping_vocabulary.v1"
OUTPUT_PATH = REPO_ROOT / "knowledge" / "mapping" / "vocabulary.v1.json"

# --- contract constants (C1/C7): engine-internal names, not code literals yet ---
ROW_FILTERS = [
    # Named row-filter whitelist; the three-branch classification/row_kind
    # logic of advice_target_switching._observable_chain stays in the engine
    # (C1) and the evaluator registers it under this name (WP-05).
    "observable_switch_chain",
]
ENUMS = {
    "severity": ["info", "watch", "fix"],
    "direction": ["higher", "lower", "absolute_higher"],
    "claim_level": [
        "deterministic_rule",
        "research_supported",
        "community_practice",
        "community_consensus",
        "experimental",
    ],
    "op": [">", "<", ">=", "<=", "in_band", "ratio_to_ref_lt", "metric_version_not_in"],
    "input": ["self_summary", "reference_summary", "settings"],
}

# Limitation tokens returned from helper functions whose result is bound into
# a limitation-named variable by the caller; the AST context rules cannot see
# through that call boundary. Each token must still appear verbatim in the
# cited module (enforced by the anti-drift test).
EXTRA_LIMITATION_TOKENS = {
    "acquisition_start_window_censored": "kovaak_tracker/dynamic_clicking_analysis.py",
    "acquisition_not_observed_before_click": "kovaak_tracker/dynamic_clicking_analysis.py",
}

# Analyzer modules whose emitted limitation tokens are frozen (plan WP-01:
# advice files + family analyzers + visual_signals).
LIMITATION_SOURCES = [
    "kovaak_tracker/advice.py",
    "kovaak_tracker/advice_tracking.py",
    "kovaak_tracker/advice_dynamic_clicking.py",
    "kovaak_tracker/advice_target_switching.py",
    "kovaak_tracker/tracking_analysis.py",
    "kovaak_tracker/dynamic_clicking_analysis.py",
    "kovaak_tracker/target_switching_analysis.py",
    "kovaak_tracker/visual_signals.py",
]

# Modules whose row classification/row_kind values are frozen (the target
# switching row filter semantics depend on them; deep-dive §5.1).
ROW_CLASSIFICATION_SOURCES = [
    "kovaak_tracker/advice_target_switching.py",
    "kovaak_tracker/target_switching_analysis.py",
]

# Limitation tokens are atomic snake_case words; colon-composites (e.g.
# "visual_quality_below_threshold:...") are parameterized product-internal
# quality gates and stay out of the frozen domain. Single words without an
# underscore are excluded too, which keeps same-named concepts on other
# layers out (e.g. the metric_record-level "deterministic" classification in
# target_switching_analysis.py is not a row classification value).
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")


def _read_tree(rel_path: str) -> ast.Module:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    return ast.parse(source, filename=rel_path)


def _iter_literal_strings(node: ast.AST | None):
    """Yield str constants inside literal shapes only.

    Recurses through List/Set/Tuple elements, IfExp and BoolOp branches,
    Starred items, and set/frozenset/list/tuple/sorted call arguments.
    Dict keys, names, subscripts, f-strings and comprehensions are not
    entered, so field labels and parameterized composites never leak in.
    """
    if node is None:
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
        return
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        for elt in node.elts:
            yield from _iter_literal_strings(elt)
        return
    if isinstance(node, ast.IfExp):
        yield from _iter_literal_strings(node.body)
        yield from _iter_literal_strings(node.orelse)
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _iter_literal_strings(value)
        return
    if isinstance(node, ast.Starred):
        yield from _iter_literal_strings(node.value)
        return
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"set", "frozenset", "list", "tuple", "sorted"}
    ):
        for arg in node.args:
            yield from _iter_literal_strings(arg)
        return


def _assign_target_labels(target: ast.AST) -> list[str]:
    """Names/attrs/subscript-keys assigned to, lowercased for matching."""
    labels: list[str] = []
    if isinstance(target, ast.Name):
        labels.append(target.id.lower())
    elif isinstance(target, ast.Attribute):
        labels.append(target.attr.lower())
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            labels.extend(_assign_target_labels(elt))
    elif isinstance(target, ast.Subscript):
        if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
            labels.append(target.slice.value.lower())
    return labels


def _dict_entries(node: ast.Dict, keys: set[str]):
    """Yield (key, value) pairs whose literal dict key is in *keys*."""
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in keys:
            yield key.value, value


def _structural_mentions(node: ast.AST, needles: set[str]) -> bool:
    """True when *node* references a needle in identifier or field-access
    position: a Name id, an Attribute attr, a string subscript key, or the
    first argument of a ``.get(...)`` call. A needle that is merely a member
    of a literal container (e.g. the row-key exclusion set
    ``{"event_ref", "row_kind", "start_ms", "end_ms", "limitations"}`` in
    target_switching_analysis.py) does not count, so exclusion sets are not
    mistaken for limitation/classification value sources."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and any(n in sub.id.lower() for n in needles):
            return True
        if isinstance(sub, ast.Attribute) and any(n in sub.attr.lower() for n in needles):
            return True
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.slice, ast.Constant)
            and isinstance(sub.slice.value, str)
            and any(n in sub.slice.value.lower() for n in needles)
        ):
            return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "get"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
            and isinstance(sub.args[0].value, str)
            and any(n in sub.args[0].value.lower() for n in needles)
        ):
            return True
    return False


def _harvest_limitation_strings(tree: ast.Module) -> set[str]:
    tokens: set[str] = set()

    for node in ast.walk(tree):
        # R1: <...limitation...>.append/extend/add(str or literal shapes)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend", "add"}
            and "limitation" in ast.unparse(node.func.value).lower()
        ):
            for arg in node.args:
                tokens.update(t for t in _iter_literal_strings(arg) if _TOKEN_RE.fullmatch(t))
        # R2: assignment into a limitation-named target
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                any("limitation" in label for label in _assign_target_labels(t))
                for t in targets
            ):
                tokens.update(
                    t for t in _iter_literal_strings(node.value) if _TOKEN_RE.fullmatch(t)
                )
        # R3: dict literal entries under "limitations"
        elif isinstance(node, ast.Dict):
            for _, value in _dict_entries(node, {"limitations"}):
                tokens.update(
                    t for t in _iter_literal_strings(value) if _TOKEN_RE.fullmatch(t)
                )
        # R4: comparisons that structurally reference a limitation name, e.g.
        # "tok" in limitations / x == interval_limitation / "tok" in row["limitations"]
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_structural_mentions(op, {"limitation"}) for op in operands):
                for op in operands:
                    tokens.update(
                        t for t in _iter_literal_strings(op) if _TOKEN_RE.fullmatch(t)
                    )
        # R5: for-loops binding a limitation-named variable over literal pairs
        elif isinstance(node, ast.For):
            if any(
                "limitation" in label for label in _assign_target_labels(node.target)
            ):
                tokens.update(
                    t for t in _iter_literal_strings(node.iter) if _TOKEN_RE.fullmatch(t)
                )

    # R6: str returns of functions named *limitation*
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "limitation" in node.name.lower():
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    tokens.update(
                        t for t in _iter_literal_strings(sub.value) if _TOKEN_RE.fullmatch(t)
                    )
    return tokens


def _harvest_row_classifications(tree: ast.Module) -> set[str]:
    keys = {"classification", "row_kind"}
    tokens: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for _, value in _dict_entries(node, keys):
                tokens.update(
                    t for t in _iter_literal_strings(value) if _TOKEN_RE.fullmatch(t)
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                any(label in keys for label in _assign_target_labels(t))
                for t in targets
            ):
                tokens.update(
                    t for t in _iter_literal_strings(node.value) if _TOKEN_RE.fullmatch(t)
                )
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_structural_mentions(op, keys) for op in operands):
                for op in operands:
                    tokens.update(
                        t for t in _iter_literal_strings(op) if _TOKEN_RE.fullmatch(t)
                    )
    return tokens


def _finding_signal_literals(rel_path: str) -> set[str]:
    """First positional str argument of Finding(...) constructor calls."""
    tree = _read_tree(rel_path)
    signals: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Finding"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            signals.add(node.args[0].value)
    return signals


def _switching_candidate_specs() -> list[tuple[str, str, str, str]]:
    """(metric_key, row_field, signal, knowledge_metric_ref) 4-tuples literal
    in build_target_switching_candidate_advice."""
    tree = _read_tree("kovaak_tracker/advice_target_switching.py")
    specs: list[tuple[str, str, str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple)
            and len(node.elts) == 4
            and all(isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in node.elts)
        ):
            specs.append(tuple(elt.value for elt in node.elts))  # type: ignore[assignment]
    return specs


def _switching_observation_refs() -> set[str]:
    tree = _read_tree("kovaak_tracker/advice_target_switching.py")
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for _, value in _dict_entries(node, {"observation_ref"}):
                refs.update(_iter_literal_strings(value))
    return refs


def _candidate_facts() -> dict[str, set[str]]:
    """Family candidate builder constants split into vocabulary sections."""
    signals: set[str] = set()
    row_fields: set[str] = set()
    metric_keys: set[str] = set()
    knowledge_tokens: set[str] = set()
    observation_refs: set[str] = set()

    for spec in advice_tracking._TRACKING_CANDIDATES:
        metric_key, row_field, signal, knowledge_token, _, _, observation_ref = spec
        metric_keys.add(metric_key)
        row_fields.add(row_field)
        signals.add(signal)
        knowledge_tokens.add(knowledge_token)
        observation_refs.add(observation_ref)

    for spec in advice_dynamic_clicking._CANDIDATES:
        metric_key, row_field, signal, observation_ref = spec
        metric_keys.add(metric_key)
        row_fields.add(row_field)
        signals.add(signal)
        observation_refs.add(observation_ref)
        # advice_dynamic_clicking passes f"metric:{row_field}" to
        # resolve_candidate_knowledge_refs.
        knowledge_tokens.add(f"metric:{row_field}")

    for metric_key, row_field, signal, knowledge_token in _switching_candidate_specs():
        metric_keys.add(metric_key)
        row_fields.add(row_field)
        signals.add(signal)
        knowledge_tokens.add(knowledge_token)

    observation_refs.update(_switching_observation_refs())
    return {
        "signals": signals,
        "row_fields": row_fields,
        "metric_keys": metric_keys,
        "knowledge_metric_tokens": knowledge_tokens,
        "observation_refs": observation_refs,
    }


def _registry_facts() -> tuple[set[str], set[str]]:
    """Signals and metric-ref tokens declared by active official registry
    entries (C7: the signals domain is registry signals ∪ analyzer signals)."""
    registry = load_registry()
    signals: set[str] = set()
    tokens: set[str] = set()
    for entry in registry["entries"]:
        if entry.get("status") != "active":
            continue
        signals.update(s for s in entry.get("signals") or [] if isinstance(s, str))
        tokens.update(t for t in entry.get("metric_refs") or [] if isinstance(t, str))
    return signals, tokens


def build_vocabulary() -> dict:
    candidates = _candidate_facts()
    registry_signals, registry_tokens = _registry_facts()

    signals = set(advice._SIGNAL_METRICS) | set(advice_tracking._PLAIN_MEANINGS)
    signals.update(_finding_signal_literals("kovaak_tracker/advice.py"))
    signals.update(_finding_signal_literals("kovaak_tracker/advice_tracking.py"))
    signals.update(candidates["signals"])
    signals.update(registry_signals)
    for archetype in profiles.ARCHETYPES:
        signals.update(archetype["conditions"])
    signals.update(profiles.ROOT_CAUSES)

    metric_keys = set(metric_definitions.METRIC_DEFINITIONS) | {"cm_per_360"}
    metric_keys.update(candidates["metric_keys"])

    knowledge_metric_tokens = set(registry_tokens) | candidates["knowledge_metric_tokens"]

    observation_refs = set(diagnosis._STATIC_OBSERVATION_REFS.values())
    observation_refs.update(candidates["observation_refs"])

    limitation_tokens = set(EXTRA_LIMITATION_TOKENS)
    for entry_set in advice_dynamic_clicking._ADVICE_BLOCKING_LIMITATIONS.values():
        limitation_tokens.update(entry_set)
    for rel_path in LIMITATION_SOURCES:
        limitation_tokens.update(_harvest_limitation_strings(_read_tree(rel_path)))

    row_classifications: set[str] = set()
    for rel_path in ROW_CLASSIFICATION_SOURCES:
        row_classifications.update(_harvest_row_classifications(_read_tree(rel_path)))

    return {
        "schema_version": SCHEMA_VERSION,
        "signals": sorted(signals),
        "metric_keys": sorted(metric_keys),
        "knowledge_metric_tokens": sorted(knowledge_metric_tokens),
        "observation_refs": sorted(observation_refs),
        "limitation_tokens": sorted(limitation_tokens),
        "row_fields": sorted(candidates["row_fields"]),
        "row_classifications": sorted(row_classifications),
        "row_filters": sorted(ROW_FILTERS),
        "enums": {name: sorted(values) for name, values in ENUMS.items()},
    }


def serialize_vocabulary(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    doc = build_vocabulary()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(serialize_vocabulary(doc))
    counts = ", ".join(f"{k}={len(v)}" for k, v in doc.items() if isinstance(v, list))
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
