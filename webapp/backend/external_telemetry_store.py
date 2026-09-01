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
import threading
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


def sidecar_path(external_run_id: str, name: str) -> str:
    """Path of a frozen sidecar copy (kept under its original file name)."""
    return f"external/{external_run_id}/{name}"


def sidecar_source_name(key: str, round_file: str) -> str | None:
    """Original file name of one sidecar key (SIDECARS.md §0).

    与 ingest._sidecar_names 是同一命名合同（views/inputs 按轮号 NN 对应
    round_NN.jsonl，bb/merge_manifest 固定名）；分析快照侧按它定位冻结副本。
    """
    round_no = ""
    if round_file.startswith("round_") and round_file.endswith(".jsonl"):
        round_no = round_file[len("round_"):-len(".jsonl")]
    names = {
        "views": f"views_{round_no}.jsonl",
        "inputs": f"inputs_{round_no}.jsonl",
        "bb": "bb.json",
        "merge_manifest": "merge_manifest.json",
    }
    return names.get(key)


def write_frozen_sidecar(external_run_id: str, name: str, payload: bytes) -> None:
    """Freeze a copy of one sidecar file under DATA_ROOT (atomic tmp+replace).

    旁车合同（FPSAimTrainer analysis/external/SIDECARS.md v1）：views/inputs/
    bb/merge_manifest 与 round.jsonl 同目录原样冻结，供分析侧消费。
    """
    path = file_store._data_root() / sidecar_path(external_run_id, name)
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


# ---- matched-run 反向索引（分析快照路径用） ----

# pairing.matched_run_id -> [external_run_id] 的轻量反向索引：分析快照每次都
# 要按 KovaaK run 找配对的遥测轮，全量重扫 meta 是 O(外部轮数×分析次数)。
# 这里惰性构建一次，并按台账 (path, mtime_ns, size) 失效——导入、修订、
# proposal 补丁与旁车补齐都会写台账，是外部库变化的充分信号。取舍：台账
# 之外手改 meta 不会触发重建（本地单用户场景可接受）；索引只存 id，命中时
# 再读 meta，保证拿到的是最新内容。
_MATCHED_INDEX_LOCK = threading.Lock()
_MATCHED_INDEX: dict[str, object] = {"signature": None, "by_run_id": {}}


def _matched_index_signature() -> tuple[str, int, int] | None:
    path = file_store._data_root() / LEDGER_PATH
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _rebuild_matched_index() -> dict[int, list[str]]:
    index: dict[int, list[str]] = {}
    for path in file_store.list_subdirs("external"):
        if not path.name.startswith("ext-"):
            continue
        meta = load_meta(path.name)
        if meta is None:
            continue
        pairing = meta.get("pairing")
        matched = pairing.get("matched_run_ids") if isinstance(pairing, dict) else None
        if not isinstance(matched, list):
            continue
        external_id = meta.get("external_run_id")
        if not isinstance(external_id, str) or not external_id:
            continue
        for run_id in matched:
            if isinstance(run_id, int) and not isinstance(run_id, bool):
                index.setdefault(run_id, []).append(external_id)
    return index


def _matched_external_ids(kovaak_run_id: int) -> list[str]:
    """Matched external ids for one KovaaK run from the (lazily rebuilt) index."""
    signature = _matched_index_signature()
    with _MATCHED_INDEX_LOCK:
        cached = _MATCHED_INDEX["by_run_id"]
        if signature is not None and _MATCHED_INDEX["signature"] == signature:
            external_ids = cached.get(int(kovaak_run_id)) if isinstance(cached, dict) else None
        else:
            by_run_id = _rebuild_matched_index()
            _MATCHED_INDEX["signature"] = signature
            _MATCHED_INDEX["by_run_id"] = by_run_id
            external_ids = by_run_id.get(int(kovaak_run_id))
    return list(external_ids or [])


def matched_metas(kovaak_run_id: int) -> list[dict]:
    """All metas whose pairing matched this KovaaK run, newest import first."""
    metas = [
        meta
        for meta in (load_meta(item) for item in _matched_external_ids(kovaak_run_id))
        if meta is not None
    ]
    metas.sort(key=lambda meta: str(meta.get("imported_at", "")), reverse=True)
    return metas


def find_latest_matched_meta(kovaak_run_id: int) -> dict | None:
    """Return the newest imported meta whose pairing matched this KovaaK run."""
    metas = matched_metas(kovaak_run_id)
    return metas[0] if metas else None
