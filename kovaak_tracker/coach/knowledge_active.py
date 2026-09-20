"""Active knowledge resolution: the single entry point that decides whether
Coach reads the official first-party knowledge base or an installed pack.

Semantics (kb-sdk plan C3/C4): ``DATA_ROOT/config/knowledge.json`` selects the
active knowledge base. A missing config (or missing DATA_ROOT) is the default
official state; a broken config or an ``active`` pointer that cannot be
resolved degrades to official with a recorded fallback reason, and a pack
registry that fails to load degrades the same way. Official mapping assets
follow the same resource-root rule as ``knowledge_registry``; a missing
official mapping file is the normal built-in-fallback state, not an error.

This module never edits ``knowledge_registry``; it calls it through the module
attribute so test harnesses can pin ``knowledge_registry.load_registry``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import knowledge_registry

CONFIG_SCHEMA_VERSION = "knowledge_config.v1"
ACTIVE_OFFICIAL = "official"

_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
_OFFICIAL_MAPPING_PATH = (
    Path(_RESOURCE_ROOT) / "knowledge" / "mapping" / "official.v1.json"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[2] / "knowledge" / "mapping" / "official.v1.json"
)


@dataclass
class ActiveKnowledge:
    mode: str                    # "official" | "pack"
    pack_id: str | None
    pack_version: str | None
    registry_path: Path | None   # pack mode: registry.json inside the pack
    mapping_path: Path | None    # pack mode: mapping.json inside the pack; official: resource-root path


_last_fallback_reason: str | None = None


def _record_fallback(reason: str) -> None:
    global _last_fallback_reason
    _last_fallback_reason = reason


def last_fallback_reason() -> str | None:
    """Fallback reason of the most recent resolve/load call (None = no fallback)."""
    return _last_fallback_reason


def _data_root() -> Path | None:
    raw = os.environ.get("DATA_ROOT", "").strip()
    return Path(raw) if raw else None


def _config_path() -> Path | None:
    root = _data_root()
    return root / "config" / "knowledge.json" if root else None


def _official() -> ActiveKnowledge:
    return ActiveKnowledge(
        mode="official",
        pack_id=None,
        pack_version=None,
        registry_path=None,
        mapping_path=_OFFICIAL_MAPPING_PATH,
    )


def _load_config(path: Path) -> dict[str, Any] | None:
    """None = no usable config (missing file is the default official state)."""
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None
    try:
        doc = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _record_fallback("knowledge config is not valid JSON")
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != CONFIG_SCHEMA_VERSION:
        _record_fallback("knowledge config schema_version is unsupported")
        return None
    return doc


def _installed_entry(config: dict[str, Any], pack_id: str) -> dict[str, Any] | None:
    installed = config.get("installed")
    if not isinstance(installed, list):
        return None
    for item in installed:
        if isinstance(item, dict) and item.get("pack_id") == pack_id:
            return item
    return None


def _resolve() -> ActiveKnowledge:
    config_path = _config_path()
    if config_path is None:
        return _official()
    config = _load_config(config_path)
    if config is None:
        return _official()
    active = config.get("active")
    if not isinstance(active, str) or not active:
        _record_fallback("knowledge config active field is invalid")
        return _official()
    if active == ACTIVE_OFFICIAL:
        return _official()
    entry = _installed_entry(config, active)
    pack_dir = _data_root() / "knowledge-packs" / active
    if entry is None or not pack_dir.is_dir():
        _record_fallback(f"active knowledge pack is not installed: {active}")
        return _official()
    registry_path = pack_dir / "knowledge" / "registry.json"
    if not registry_path.is_file():
        _record_fallback(f"active knowledge pack registry is missing: {active}")
        return _official()
    mapping_path = pack_dir / "mapping.json"
    pack_version = entry.get("pack_version")
    return ActiveKnowledge(
        mode="pack",
        pack_id=active,
        pack_version=pack_version if isinstance(pack_version, str) else None,
        registry_path=registry_path,
        mapping_path=mapping_path if mapping_path.is_file() else None,
    )


def resolve_active() -> ActiveKnowledge:
    """Resolve the current active knowledge base; degraded states fall back to official."""
    global _last_fallback_reason
    _last_fallback_reason = None
    return _resolve()


def load_active_registry() -> dict[str, Any]:
    """Load the active registry; any pack failure falls back to official + reason."""
    global _last_fallback_reason
    _last_fallback_reason = None
    active = _resolve()
    if active.registry_path is None:
        return knowledge_registry.load_registry()
    try:
        return knowledge_registry.load_registry(path=active.registry_path)
    except Exception:
        _record_fallback(f"active knowledge pack registry is invalid: {active.pack_id}")
        return knowledge_registry.load_registry()


def load_active_mapping() -> tuple[dict[str, Any] | None, str | None]:
    """(mapping doc, fallback reason). A missing file -> (None, None): the
    built-in frozen rules are the normal fallback, not a degradation."""
    global _last_fallback_reason
    _last_fallback_reason = None
    active = _resolve()
    path = active.mapping_path
    if path is None:
        return (None, None)
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return (None, None)
    try:
        doc = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        reason = "active mapping is not valid JSON"
        _record_fallback(reason)
        return (None, reason)
    if not isinstance(doc, dict):
        reason = "active mapping is not a JSON object"
        _record_fallback(reason)
        return (None, reason)
    return (doc, None)
