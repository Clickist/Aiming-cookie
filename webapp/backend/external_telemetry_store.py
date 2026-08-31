"""Config, ledger and run-meta storage for external KovaaK telemetry imports.

An ExternalTelemetryRun is one cleaned round (``round_NN.jsonl``) captured by
the external memory-read telemetry pipeline (FORMAT v2, channel A). It is the
external counterpart of a KovaaKRun: one cleaned round maps to exactly one
imported run. All artifacts live under ``{DATA_ROOT}/external/``; the upstream
``cleaned/`` tree is only ever read, never written.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from . import file_store

log = logging.getLogger(__name__)

_CONFIG_PATH = "config/external-telemetry.json"
LEDGER_PATH = "external/_ledger.json"

IMPORT_PARSER_VERSION = "external_run_import.v1"
SCHEMA_VERSION = "external_run.v1"
SUPPORTED_FORMAT_VERSION = 1


def external_run_id(dedup_key: str) -> str:
    """Derive the stable run id from a dedup key (source|round|index_dirname)."""
    return "ext-" + hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()[:16]


def meta_path(external_run_id: str) -> str:
    return f"external/{external_run_id}/meta.json"


def frames_path(external_run_id: str) -> str:
    return f"external/{external_run_id}/round.jsonl"


def _normalize_watch_root(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("External telemetry watch root must be an absolute path")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("External telemetry watch root must be a directory")
    return resolved


def get_watch_root() -> Path | None:
    """Return the configured watch root, or None when unset/corrupted.

    Unlike the KovaaK stats directories the root does not have to exist right
    now: a moved/deleted upstream tree must stay observable through the
    watcher's ``directory_missing`` diagnostic instead of failing the config.
    """
    try:
        data = file_store.read_json(_CONFIG_PATH)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    watch_root = data.get("watch_root")
    if not isinstance(watch_root, str) or not watch_root:
        return None
    try:
        return _normalize_watch_root(watch_root)
    except ValueError:
        return None


def save_watch_root(raw_path: str) -> Path:
    resolved = _normalize_watch_root(raw_path)
    file_store.write_json(_CONFIG_PATH, {"watch_root": str(resolved)})
    return resolved


def read_ledger() -> dict:
    try:
        data = file_store.read_json(LEDGER_PATH)
    except (OSError, ValueError):
        # A corrupted ledger must not wedge the watcher: start empty and let
        # content hashes re-deduplicate what is already on disk.
        log.warning("external telemetry ledger unreadable; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def write_ledger(ledger: dict) -> None:
    file_store.write_json(LEDGER_PATH, ledger)


def load_meta(external_run_id: str) -> dict | None:
    try:
        data = file_store.read_json(meta_path(external_run_id))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_meta(external_run_id: str, meta: dict) -> None:
    file_store.write_json(meta_path(external_run_id), meta)


def write_frozen_frames(external_run_id: str, payload: bytes) -> None:
    """Freeze a copy of the round jsonl under DATA_ROOT (atomic tmp+replace)."""
    path = file_store._data_root() / frames_path(external_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, path)


def count_external_runs() -> int:
    """Count imported runs from the ledger without reading every meta."""
    return sum(
        1
        for entry in read_ledger().values()
        if isinstance(entry, dict) and entry.get("status") == "imported"
    )


def list_external_runs(limit: int = 200) -> list[dict]:
    """Return run metas sorted by import time, newest first (shallow)."""
    items: list[dict] = []
    for path in file_store.list_subdirs("external"):
        if not path.name.startswith("ext-"):
            continue
        meta = load_meta(path.name)
        if meta is None:
            continue
        items.append(meta)
    items.sort(key=lambda meta: str(meta.get("imported_at", "")), reverse=True)
    return items[: max(1, min(limit, 500))]
