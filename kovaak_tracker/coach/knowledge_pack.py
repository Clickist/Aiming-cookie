"""coach_knowledge_pack.v1: third-party knowledge pack validator + local store.

Contract source: kb-sdk plan C2/C3, work package WP-08b
(``.zcode/kb-sdk-impl-plan-2026-09-20.md``). A pack is a directory or zip
holding ``manifest.json`` + ``knowledge/registry.json`` (required),
``mapping.json`` + ``README.md`` (optional). Validation reuses the frozen
v3 registry validator and the ``coach_mapping.v1`` validator, then applies
the six pack-level narrowing rules:

1. registry scale: v3 validator + the 1 MiB file gate (512 entries / 1 MiB /
   1200-char sections come from ``knowledge_registry`` itself);
2. ``sources[].source_level`` may not be ``product_contract`` or
   ``coach_first_party`` (third-party claim ceiling);
3. prescriptions stay open, but ``scenario_prescription.scenario_profile_ref``
   must hit a reviewed ref of the OFFICIAL scenario registry (never pack-local);
4. entry ``signals`` / ``metric_refs`` must sit inside the frozen official
   vocabulary; ``signal_aliases`` stay free;
5. ``mapping.json`` must pass ``validate_mapping`` against the official
   vocabulary with THIS pack's registry as the cross-check registry, so every
   ``expected_entry_ref`` resolves to an active entry at import time;
6. unsafe-shape checks run everywhere (mapping via ``validate_mapping``,
   registry via the v3 validator, manifest/zip layout here).

Storage writes ``DATA_ROOT/knowledge-packs/{pack_id}/`` (flat overwrite
install) and registers the pack in ``DATA_ROOT/config/knowledge.json``
(``knowledge_config.v1``, C3). ``set_active`` only writes config; knowledge
rematerialization is the sidecar's job. A pack ``registry_version`` is
``"{pack_id}@{pack_version}"`` — the ``@`` keeps the pack namespace disjoint
from the official ``2026-09-12.v12``-style version space.

CLI: ``python -m kovaak_tracker.coach.knowledge_pack validate <path>``
(exit 0 = valid, 1 = invalid) — the SDK documentation entry point.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import scenario_profiles
from . import knowledge_registry, mapping_rules
from .knowledge_active import ACTIVE_OFFICIAL, CONFIG_SCHEMA_VERSION
from .knowledge_registry import MAX_REGISTRY_BYTES, REGISTRY_SCHEMA_VERSION_V3
from .mapping_rules import MAX_FILE_BYTES, MappingValidationError

PACK_SCHEMA_VERSION = "coach_knowledge_pack.v1"
# Starts and ends with [a-z0-9] so neither "." nor ".." can be a pack_id
# (installation key is also a directory name under DATA_ROOT/knowledge-packs).
PACK_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
PACK_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

MANIFEST_NAME = "manifest.json"
REGISTRY_NAME = "knowledge/registry.json"
MAPPING_NAME = "mapping.json"
README_NAME = "README.md"
_ALLOWED_FILES = frozenset({MANIFEST_NAME, REGISTRY_NAME, MAPPING_NAME, README_NAME})

MAX_MANIFEST_BYTES = 64 * 1024
MAX_README_BYTES = 256 * 1024
MAX_PACK_TOTAL_BYTES = 8 * 1024 * 1024
_FILE_CAPS: dict[str, int] = {
    MANIFEST_NAME: MAX_MANIFEST_BYTES,
    REGISTRY_NAME: MAX_REGISTRY_BYTES,
    MAPPING_NAME: MAX_FILE_BYTES,
    README_NAME: MAX_README_BYTES,
}

MANIFEST_REQUIRED_FIELDS = frozenset({
    "schema_version", "pack_id", "display_name", "author", "pack_version",
    "license", "ac_compat",
})
MANIFEST_OPTIONAL_FIELDS = frozenset({"homepage"})
AC_COMPAT_REQUIRED_FIELDS = frozenset({"knowledge_schema"})
AC_COMPAT_OPTIONAL_FIELDS = frozenset({"mapping_schema"})
ACCEPTED_KNOWLEDGE_SCHEMAS = frozenset({"coach_knowledge_registry.v3"})
ACCEPTED_MAPPING_SCHEMAS = frozenset({mapping_rules.MAPPING_SCHEMA_VERSION})

# C2 rule 2: the third-party claim ceiling. Official-only source levels.
FORBIDDEN_SOURCE_LEVELS = frozenset({"product_contract", "coach_first_party"})

# Same口径 as knowledge_registry/_PATH_RE and mapping_rules/_PATH_RE.
_PATH_RE = re.compile(r"^(?:/|\\|~/|\.\.[/\\]|[A-Za-z]:[/\\]|file://)", re.I)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}"
)
_FORBIDDEN_KEY_MARKERS = (
    "rawtrace", "payload", "credential", "apikey", "accesstoken",
    "refreshtoken", "password", "secret", "authorization",
)
_FORBIDDEN_INSTRUCTION_KEYS = frozenset({
    "command", "exec", "execute", "eval", "evaluate", "shell", "terminal",
    "script", "instruction", "prompt", "handler", "callback",
})
_MAX_SHAPE_TEXT = 1200
_MAX_SHAPE_DEPTH = 6
_HOMEPAGE_RE = re.compile(r"^https?://\S+$")

_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
_VOCABULARY_PATH = (
    Path(_RESOURCE_ROOT) / "knowledge" / "mapping" / "vocabulary.v1.json"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[2] / "knowledge" / "mapping" / "vocabulary.v1.json"
)


class KnowledgePackError(ValueError):
    """Pack source, store operation, or configuration violates the contract."""


class KnowledgePackRejected(KnowledgePackError):
    """A pack failed validation; ``errors`` carries the structured report."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(f"knowledge pack rejected: {len(result['errors'])} error(s)")
        self.result = result


class _PackLayoutError(Exception):
    """Internal: zip/dir materialization failed with structured errors."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(errors[0]["message"] if errors else "pack layout error")
        self.errors = errors


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _error(code: str, message: str, path: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        item["path"] = path
    return item


def _reject_unsafe_shape(value: Any, *, depth: int = 0) -> None:
    """Manifest-side unsafe-shape walk (rule 6). Mapping is covered by
    ``validate_mapping`` and the registry by the v3 validator."""
    if isinstance(value, Mapping):
        if depth > _MAX_SHAPE_DEPTH:
            raise KnowledgePackError("manifest exceeds the nesting limit")
        for key, child in value.items():
            if not isinstance(key, str):
                raise KnowledgePackError("manifest keys must be strings")
            compact = re.sub(r"[^a-z0-9]", "", key.casefold())
            segments = [s for s in re.split(r"[^a-z0-9]+", key.casefold()) if s]
            if (
                any(marker in compact for marker in _FORBIDDEN_KEY_MARKERS)
                or compact.endswith("path")
                or any(segment in _FORBIDDEN_INSTRUCTION_KEYS for segment in segments)
            ):
                raise KnowledgePackError(f"manifest contains unsafe fields: {key}")
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe_shape(child, depth=depth + 1)
    elif isinstance(value, str):
        if _PATH_RE.search(value) or _SECRET_RE.search(value):
            raise KnowledgePackError("manifest contains unsafe text")
        if len(value) > _MAX_SHAPE_TEXT:
            raise KnowledgePackError("manifest contains overlong text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise KnowledgePackError("manifest contains unsupported value")


def _read_json_file(path: Path, *, max_bytes: int) -> tuple[Any | None, dict[str, Any] | None]:
    """(doc, None) or (None, error). Trailing whitespace is valid JSON, which
    keeps the size gate honest: padding alone cannot smuggle content."""
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return None, _error("pack_file_unreadable", f"cannot read {path.name}: {exc}", path.name)
    if len(raw_bytes) > max_bytes:
        return None, _error(
            "pack_file_too_large",
            f"{path.name} exceeds the {max_bytes} byte limit",
            path.name,
        )
    try:
        return json.loads(raw_bytes), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, _error(
            "pack_invalid_json", f"{path.name} is not valid JSON: {exc}", path.name
        )


def _load_vocabulary() -> dict[str, Any]:
    doc, error = _read_json_file(_VOCABULARY_PATH, max_bytes=MAX_MANIFEST_BYTES)
    if error is not None or not isinstance(doc, dict):
        raise KnowledgePackError(
            "official mapping vocabulary is unavailable; cannot verify vocabulary rules"
        )
    return doc


# ---------------------------------------------------------------------------
# Zip / directory materialization (safe unpack)
# ---------------------------------------------------------------------------


def _is_zip_source(source: Path) -> bool:
    if source.is_dir():
        return False
    try:
        return zipfile.is_zipfile(source)
    except OSError:
        return False


def _safe_zip_members(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(member_name, relative_posix_path) after safety checks and root strip."""
    file_infos = [info for info in zf.infolist() if not info.is_dir()]
    if not file_infos:
        raise _PackLayoutError([_error("pack_empty_archive", "zip archive contains no files")])
    names = [info.filename for info in file_infos]
    for name in names:
        if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise _PackLayoutError([
                _error("zip_unsafe_entry", f"zip entry uses an unsafe absolute path: {name}", name)
            ])
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise _PackLayoutError([
                _error("zip_unsafe_entry", f"zip entry escapes the pack root: {name}", name)
            ])
    parts_list = [name.split("/") for name in names]
    strip = 0
    if all(len(parts) >= 2 for parts in parts_list):
        firsts = {parts[0] for parts in parts_list}
        if len(firsts) == 1:
            strip = 1  # single wrapper folder, e.g. zipping the pack directory
    members: list[tuple[str, str]] = []
    for info, parts in zip(file_infos, parts_list):
        relative = "/".join(parts[strip:])
        if relative not in _ALLOWED_FILES:
            raise _PackLayoutError([
                _error(
                    "pack_unknown_file",
                    f"unexpected file in pack (allowed: {', '.join(sorted(_ALLOWED_FILES))}): "
                    f"{relative}",
                    relative,
                )
            ])
        members.append((info.filename, relative))
    return members


def _extract_zip(source: Path, dest_dir: Path) -> None:
    """Extract the pack zip into dest_dir, enforcing path safety and caps."""
    try:
        zf = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _PackLayoutError([
            _error("pack_invalid_zip", f"source is not a readable zip archive: {exc}")
        ]) from exc
    with zf:
        try:
            members = _safe_zip_members(zf)
        except _PackLayoutError:
            raise
        total = 0
        for member_name, relative in members:
            cap = _FILE_CAPS[relative]
            target = dest_dir / Path(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                with zf.open(member_name) as src, target.open("wb") as dst:
                    while chunk := src.read(64 * 1024):
                        written += len(chunk)
                        total += len(chunk)
                        if written > cap:
                            raise _PackLayoutError([
                                _error(
                                    "pack_file_too_large",
                                    f"{relative} exceeds the {cap} byte limit",
                                    relative,
                                )
                            ])
                        if total > MAX_PACK_TOTAL_BYTES:
                            raise _PackLayoutError([
                                _error(
                                    "pack_too_large",
                                    f"pack exceeds the {MAX_PACK_TOTAL_BYTES} byte total limit",
                                )
                            ])
                        dst.write(chunk)
            except (OSError, RuntimeError) as exc:
                raise _PackLayoutError([
                    _error("pack_invalid_zip", f"cannot extract {relative}: {exc}", relative)
                ]) from exc


def _check_dir_layout(pack_dir: Path) -> None:
    found: set[str] = set()
    for current, _dirs, files in os.walk(pack_dir):
        for name in files:
            absolute = Path(current) / name
            relative = absolute.relative_to(pack_dir).as_posix()
            if relative not in _ALLOWED_FILES:
                raise _PackLayoutError([
                    _error(
                        "pack_unknown_file",
                        f"unexpected file in pack (allowed: {', '.join(sorted(_ALLOWED_FILES))}): "
                        f"{relative}",
                        relative,
                    )
                ])
            found.add(relative)
    if MANIFEST_NAME not in found:
        raise _PackLayoutError([
            _error("manifest_missing", "pack is missing manifest.json", MANIFEST_NAME)
        ])
    if REGISTRY_NAME not in found:
        raise _PackLayoutError([
            _error(
                "registry_missing",
                "pack is missing knowledge/registry.json",
                REGISTRY_NAME,
            )
        ])


def _materialize(source: Path, dest_dir: Path) -> None:
    """Lay the pack out as a plain directory under dest_dir (dir copy or safe zip unpack)."""
    if source.is_dir():
        shutil.copytree(source, dest_dir)
    elif _is_zip_source(source):
        _extract_zip(source, dest_dir)
    else:
        raise _PackLayoutError([
            _error("source_invalid", f"pack source is neither a directory nor a zip: {source}")
        ])
    _check_dir_layout(dest_dir)


# ---------------------------------------------------------------------------
# Manifest validation (rule 6 manifest half + C2 manifest contract)
# ---------------------------------------------------------------------------


def _validate_manifest(doc: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    errors: list[dict[str, Any]] = []
    if not isinstance(doc, Mapping):
        return [_error("manifest_invalid", "manifest.json must be a JSON object")], None
    try:
        _reject_unsafe_shape(doc)
    except KnowledgePackError as exc:
        errors.append(_error("manifest_unsafe_content", str(exc), MANIFEST_NAME))
    if doc.get("schema_version") != PACK_SCHEMA_VERSION:
        errors.append(_error(
            "manifest_schema_version_invalid",
            f"manifest schema_version must be {PACK_SCHEMA_VERSION!r}",
            MANIFEST_NAME,
        ))
    unknown = set(doc) - MANIFEST_REQUIRED_FIELDS - MANIFEST_OPTIONAL_FIELDS
    if unknown:
        errors.append(_error(
            "manifest_field_unknown",
            f"manifest has unknown fields: {sorted(unknown)}",
            MANIFEST_NAME,
        ))
    missing = MANIFEST_REQUIRED_FIELDS - set(doc)
    if missing:
        errors.append(_error(
            "manifest_field_missing",
            f"manifest is missing required fields: {sorted(missing)}",
            MANIFEST_NAME,
        ))

    def text_field(name: str, *, max_length: int) -> str | None:
        value = doc.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(_error(
                "manifest_value_invalid", f"manifest field {name!r} must be non-empty text", MANIFEST_NAME,
            ))
            return None
        if len(value) > max_length:
            errors.append(_error(
                "manifest_value_invalid",
                f"manifest field {name!r} exceeds the {max_length}-character limit",
                MANIFEST_NAME,
            ))
            return None
        return value

    pack_id: str | None = None
    raw_pack_id = doc.get("pack_id")
    if isinstance(raw_pack_id, str) and PACK_ID_RE.fullmatch(raw_pack_id):
        pack_id = raw_pack_id
    else:
        errors.append(_error(
            "manifest_pack_id_invalid",
            "manifest pack_id must match ^[a-z0-9.-]+$ and may not start or end "
            "with '.', '-' (it is the installation directory name)",
            MANIFEST_NAME,
        ))
    pack_version: str | None = None
    raw_version = doc.get("pack_version")
    if isinstance(raw_version, str) and PACK_VERSION_RE.fullmatch(raw_version):
        pack_version = raw_version
    else:
        errors.append(_error(
            "manifest_pack_version_invalid",
            "manifest pack_version must be a semver string like 1.2.0",
            MANIFEST_NAME,
        ))
    for name, limit in (("display_name", 120), ("author", 120), ("license", 120)):
        text_field(name, max_length=limit)
    if "homepage" in doc:
        homepage = doc.get("homepage")
        if not isinstance(homepage, str) or not _HOMEPAGE_RE.fullmatch(homepage) or len(homepage) > 300:
            errors.append(_error(
                "manifest_value_invalid",
                "manifest field 'homepage' must be an http(s) URL of at most 300 characters",
                MANIFEST_NAME,
            ))
    if errors:
        return errors, None

    has_mapping_file_hint = "mapping_schema" in (doc.get("ac_compat") or {})
    ac_errors = _validate_ac_compat(doc.get("ac_compat"))
    errors.extend(ac_errors)
    if errors:
        return errors, None
    return [], {
        "pack_id": pack_id,
        "pack_version": pack_version,
        "display_name": doc["display_name"],
        "author": doc["author"],
        "license": doc["license"],
        "homepage": doc.get("homepage"),
        "ac_compat": doc["ac_compat"],
        "declares_mapping": has_mapping_file_hint,
    }


def _validate_ac_compat(ac_compat: Any) -> list[dict[str, Any]]:
    if not isinstance(ac_compat, Mapping):
        return [_error(
            "ac_compat_invalid",
            "manifest ac_compat must be an object with a knowledge_schema list",
            MANIFEST_NAME,
        )]
    unknown = set(ac_compat) - AC_COMPAT_REQUIRED_FIELDS - AC_COMPAT_OPTIONAL_FIELDS
    if unknown:
        return [_error(
            "ac_compat_invalid", f"ac_compat has unknown fields: {sorted(unknown)}", MANIFEST_NAME,
        )]
    missing = AC_COMPAT_REQUIRED_FIELDS - set(ac_compat)
    if missing:
        return [_error(
            "ac_compat_invalid",
            f"ac_compat is missing required fields: {sorted(missing)}",
            MANIFEST_NAME,
        )]
    knowledge_schema = ac_compat["knowledge_schema"]
    if (
        isinstance(knowledge_schema, (str, bytes))
        or not isinstance(knowledge_schema, Sequence)
        or not knowledge_schema
        or set(knowledge_schema) - ACCEPTED_KNOWLEDGE_SCHEMAS
    ):
        return [_error(
            "ac_compat_invalid",
            f"ac_compat knowledge_schema must be a non-empty list from {sorted(ACCEPTED_KNOWLEDGE_SCHEMAS)}",
            MANIFEST_NAME,
        )]
    if "mapping_schema" in ac_compat:
        mapping_schema = ac_compat["mapping_schema"]
        if (
            isinstance(mapping_schema, (str, bytes))
            or not isinstance(mapping_schema, Sequence)
            or not mapping_schema
            or set(mapping_schema) - ACCEPTED_MAPPING_SCHEMAS
        ):
            return [_error(
                "ac_compat_invalid",
                f"ac_compat mapping_schema must be a non-empty list from "
                f"{sorted(ACCEPTED_MAPPING_SCHEMAS)}",
                MANIFEST_NAME,
            )]
    return []


# ---------------------------------------------------------------------------
# Registry narrowing rules (C2 rules 1-4) on the raw parsed document
# ---------------------------------------------------------------------------


def _narrow_source_levels(raw: Any, errors: list[dict[str, Any]]) -> None:
    entries = raw.get("sources") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return  # structural problems are reported by the v3 validator
    seen: set[str] = set()
    for index, source in enumerate(entries):
        if not isinstance(source, Mapping):
            continue
        level = source.get("source_level")
        if isinstance(level, str) and level in FORBIDDEN_SOURCE_LEVELS and level not in seen:
            seen.add(level)
            errors.append(_error(
                "source_level_forbidden",
                f"sources[{index}] source_level {level!r} is first-party-only; third-party packs "
                f"are capped at community_organization / community_consensus",
                REGISTRY_NAME,
            ))


def _narrow_vocabulary(raw: Any, vocabulary: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    entries = raw.get("entries") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return
    signals_vocab = vocabulary.get("signals")
    metrics_vocab = vocabulary.get("metric_keys")
    signal_ok = signals_vocab if isinstance(signals_vocab, Sequence) else ()
    metric_ok = metrics_vocab if isinstance(metrics_vocab, Sequence) else ()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        signals = entry.get("signals")
        if isinstance(signals, Sequence) and not isinstance(signals, (str, bytes)):
            for signal in signals:
                if isinstance(signal, str) and signal not in signal_ok:
                    errors.append(_error(
                        "signal_out_of_vocabulary",
                        f"entries[{index}].signals item {signal!r} is not in the frozen official "
                        f"vocabulary (signals); extend signal_aliases instead",
                        REGISTRY_NAME,
                    ))
        metric_refs = entry.get("metric_refs")
        if isinstance(metric_refs, Sequence) and not isinstance(metric_refs, (str, bytes)):
            for metric in metric_refs:
                if isinstance(metric, str) and metric not in metric_ok:
                    errors.append(_error(
                        "metric_out_of_vocabulary",
                        f"entries[{index}].metric_refs item {metric!r} is not in the frozen "
                        f"official vocabulary (metric_keys)",
                        REGISTRY_NAME,
                    ))


def _narrow_scenario_refs(raw: Any, official_refs: set[str] | None, errors: list[dict[str, Any]]) -> None:
    entries = raw.get("entries") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        prescription = entry.get("scenario_prescription")
        if not isinstance(prescription, Mapping):
            continue
        ref = prescription.get("scenario_profile_ref")
        if not isinstance(ref, str):
            continue
        if official_refs is None:
            errors.append(_error(
                "scenario_registry_unavailable",
                "the official scenario registry could not be loaded; cannot verify "
                "scenario_profile_ref (fail-closed)",
                REGISTRY_NAME,
            ))
            return
        if ref not in official_refs:
            errors.append(_error(
                "scenario_ref_not_official",
                f"entries[{index}].scenario_prescription.scenario_profile_ref {ref!r} is not a "
                f"reviewed scenario of the official knowledge/scenarios registry; scenario "
                f"profiles cannot be defined inside a pack",
                REGISTRY_NAME,
            ))


# ---------------------------------------------------------------------------
# Public validation entry point
# ---------------------------------------------------------------------------


def validate_pack(source: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate a pack directory or zip; return ``{valid, errors, pack}``.

    ``errors`` items are ``{code, message, path?}`` with readable messages for
    UI display; ``pack`` is a summary dict on success, else ``None``.
    """
    source_path = Path(source)
    errors: list[dict[str, Any]] = []
    if not source_path.exists():
        return {"valid": False, "errors": [
            _error("source_not_found", f"pack source does not exist: {source_path}")
        ], "pack": None}

    with tempfile.TemporaryDirectory(prefix="aiming-cookie-pack-") as tmp:
        staging = Path(tmp) / "pack"
        try:
            _materialize(source_path, staging)
        except _PackLayoutError as exc:
            return {"valid": False, "errors": exc.errors, "pack": None}

        manifest_doc, manifest_error = _read_json_file(staging / MANIFEST_NAME, max_bytes=MAX_MANIFEST_BYTES)
        manifest: dict[str, Any] | None = None
        if manifest_error is not None:
            errors.append(manifest_error)
        else:
            manifest_errors, manifest = _validate_manifest(manifest_doc)
            errors.extend(manifest_errors)

        vocabulary: dict[str, Any] | None = None
        try:
            vocabulary = _load_vocabulary()
        except KnowledgePackError as exc:
            errors.append(_error("vocabulary_unavailable", str(exc)))

        # Registry: size/JSON gates, narrowing rules on the raw doc, then the
        # frozen v3 validator (rule 1 scale + unsafe shape + structure).
        validated_registry: dict[str, Any] | None = None
        registry_doc, registry_error = _read_json_file(staging / REGISTRY_NAME, max_bytes=MAX_REGISTRY_BYTES)
        if registry_error is not None:
            errors.append(registry_error)
        else:
            # Packs are v3-only: the shared validator still dispatches v1/v2
            # for historical official assets, and a v1 doc has no top-level
            # ``sources``, so the pack source ceiling would silently never
            # apply (inline v1 source_level even allows product_contract).
            if (
                not isinstance(registry_doc, Mapping)
                or registry_doc.get("schema_version") != REGISTRY_SCHEMA_VERSION_V3
            ):
                errors.append(_error(
                    "registry_schema_version_invalid",
                    "knowledge/registry.json must declare schema_version "
                    f"'{REGISTRY_SCHEMA_VERSION_V3}'; packs cannot ship legacy "
                    "v1/v2 registries (see sdk/knowledge-pack/SPEC.md)",
                    REGISTRY_NAME,
                ))
            _narrow_source_levels(registry_doc, errors)
            if vocabulary is not None:
                _narrow_vocabulary(registry_doc, vocabulary, errors)
            try:
                official_refs: set[str] | None = scenario_profiles.active_scenario_profile_refs()
            except Exception:  # noqa: BLE001 - official assets must be loadable here
                official_refs = None
            _narrow_scenario_refs(registry_doc, official_refs, errors)
            try:
                validated_registry = knowledge_registry.validate_registry(registry_doc)
            except Exception as exc:  # noqa: BLE001 - surface validator reasons readably
                errors.append(_error(
                    "registry_invalid", f"knowledge/registry.json: {exc}", REGISTRY_NAME,
                ))
            if isinstance(registry_doc, Mapping) and manifest is not None:
                expected = f"{manifest['pack_id']}@{manifest['pack_version']}"
                actual = registry_doc.get("registry_version")
                if actual != expected:
                    errors.append(_error(
                        "registry_version_mismatch",
                        f"registry registry_version must be {expected!r} "
                        f"('<pack_id>@<pack_version>'); official registries use the "
                        f"'YYYY-MM-DD.vN' space and never contain '@'",
                        REGISTRY_NAME,
                    ))

        # Mapping (rule 5 + rule 6 mapping half): optional file, full C1
        # validation with the pack's own registry as cross-check registry.
        mapping_path = staging / MAPPING_NAME
        if mapping_path.is_file():
            mapping_doc, mapping_error = _read_json_file(mapping_path, max_bytes=MAX_FILE_BYTES)
            if mapping_error is not None:
                errors.append(mapping_error)
            elif manifest is not None and not manifest.get("declares_mapping"):
                errors.append(_error(
                    "ac_compat_mapping_mismatch",
                    "mapping.json is present but manifest ac_compat does not declare "
                    "mapping_schema: [\"coach_mapping.v1\"]",
                    MAPPING_NAME,
                ))
            elif vocabulary is None or validated_registry is None:
                pass  # the blocking error is already reported above
            else:
                try:
                    mapping_rules.validate_mapping(
                        mapping_doc, vocabulary=vocabulary, registry=validated_registry
                    )
                except Exception as exc:  # noqa: BLE001 - surface validator reasons readably
                    errors.append(_error(
                        "mapping_invalid", f"mapping.json: {exc}", MAPPING_NAME,
                    ))
        elif manifest is not None and manifest.get("declares_mapping"):
            errors.append(_error(
                "ac_compat_mapping_mismatch",
                "manifest declares ac_compat.mapping_schema but mapping.json is missing",
                MAPPING_NAME,
            ))

        if manifest is None:
            return {"valid": False, "errors": errors, "pack": None}
        return {
            "valid": not errors,
            "errors": errors,
            "pack": {
                "pack_id": manifest["pack_id"],
                "pack_version": manifest["pack_version"],
                "display_name": manifest["display_name"],
                "author": manifest["author"],
                "license": manifest["license"],
                "has_mapping": (staging / MAPPING_NAME).is_file(),
            },
        }


# ---------------------------------------------------------------------------
# Store: DATA_ROOT/knowledge-packs + config/knowledge.json (C3)
# ---------------------------------------------------------------------------


def _data_root(data_root: str | os.PathLike[str]) -> Path:
    path = Path(data_root)
    if not str(path).strip():
        raise KnowledgePackError("data_root must be a non-empty path")
    return path


def _packs_dir(data_root: str | os.PathLike[str]) -> Path:
    return _data_root(data_root) / "knowledge-packs"


def default_config() -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "active": ACTIVE_OFFICIAL,
        "installed": [],
    }


def read_config(data_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Load knowledge_config.v1; a missing or corrupt config is the fresh
    official state (same fallback semantics as knowledge_active, write side)."""
    path = _data_root(data_root) / "config" / "knowledge.json"
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return default_config()
    try:
        doc = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return default_config()
    if not isinstance(doc, dict) or doc.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return default_config()
    active = doc.get("active")
    installed = doc.get("installed")
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "active": active if isinstance(active, str) and active else ACTIVE_OFFICIAL,
        "installed": [item for item in installed if isinstance(item, dict)]
        if isinstance(installed, list)
        else [],
    }


def write_config(config: Mapping[str, Any], data_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Persist knowledge_config.v1 (atomic replace). Fills in missing fields."""
    merged = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "active": config.get("active", ACTIVE_OFFICIAL),
        "installed": list(config.get("installed", [])),
    }
    path = _data_root(data_root) / "config" / "knowledge.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return merged


def list_installed(data_root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    return [dict(item) for item in read_config(data_root)["installed"]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def install_pack(source_path: str | os.PathLike[str], data_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate then install a pack (directory or zip); same pack_id overwrites.

    Returns ``{pack_id, pack_version, display_name, installed_at, has_mapping,
    warnings}``. Raises :class:`KnowledgePackRejected` with the structured
    error report when validation fails.
    """
    result = validate_pack(source_path)
    if not result["valid"]:
        raise KnowledgePackRejected(result)
    pack = result["pack"]
    pack_id: str = pack["pack_id"]
    source = Path(source_path)
    packs_dir = _packs_dir(data_root)
    dest = packs_dir / pack_id
    staging = packs_dir / f".staging-{pack_id}"
    packs_dir.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    try:
        _materialize(source, staging)
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    config = read_config(data_root)
    entry = {
        "pack_id": pack_id,
        "pack_version": pack["pack_version"],
        "display_name": pack["display_name"],
        "author": pack["author"],
        "installed_at": _now_iso(),
        "has_mapping": bool(pack["has_mapping"]),
    }
    config["installed"] = [
        item for item in config["installed"] if item.get("pack_id") != pack_id
    ]
    config["installed"].append(entry)
    write_config(config, data_root)
    return {**entry, "warnings": []}


def uninstall_pack(pack_id: str, data_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Remove an installed pack; an active pack falls back to official."""
    if not isinstance(pack_id, str) or not PACK_ID_RE.fullmatch(pack_id):
        raise KnowledgePackError(f"invalid pack_id: {pack_id!r}")
    config = read_config(data_root)
    entry = next(
        (item for item in config["installed"] if item.get("pack_id") == pack_id), None
    )
    pack_dir = _packs_dir(data_root) / pack_id
    if entry is None and not pack_dir.exists():
        raise KnowledgePackError(f"knowledge pack is not installed: {pack_id}")
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    config["installed"] = [
        item for item in config["installed"] if item.get("pack_id") != pack_id
    ]
    reverted = False
    if config.get("active") == pack_id:
        config["active"] = ACTIVE_OFFICIAL
        reverted = True
    write_config(config, data_root)
    return {"removed": pack_id, "active_reverted_to_official": reverted}


def set_active(active: str, data_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Point ``config.active`` at ``official`` or an installed pack_id.

    Only writes configuration; knowledge rematerialization is the sidecar's
    job (C3). Raises when the target pack is not installed.
    """
    if not isinstance(active, str) or not active:
        raise KnowledgePackError("active must be 'official' or an installed pack_id")
    config = read_config(data_root)
    if active != ACTIVE_OFFICIAL:
        installed_ids = {item.get("pack_id") for item in config["installed"]}
        if active not in installed_ids:
            raise KnowledgePackError(
                f"cannot activate knowledge pack that is not installed: {active}"
            )
    config["active"] = active
    return write_config(config, data_root)


# ---------------------------------------------------------------------------
# CLI: python -m kovaak_tracker.coach.knowledge_pack validate <path>
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kovaak_tracker.coach.knowledge_pack",
        description="Validate a coach knowledge pack (directory or zip).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser(
        "validate", help="validate a pack; exit 0 when valid, 1 when invalid"
    )
    validate_parser.add_argument("path", help="pack directory or zip file")
    args = parser.parse_args(argv)

    result = validate_pack(args.path)
    if result["valid"]:
        pack = result["pack"]
        print(f"OK {pack['pack_id']}@{pack['pack_version']} has_mapping={pack['has_mapping']}")
        return 0
    for item in result["errors"]:
        location = f" [{item['path']}]" if item.get("path") else ""
        print(f"ERROR {item['code']}{location}: {item['message']}")
    print(f"FAILED with {len(result['errors'])} error(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
