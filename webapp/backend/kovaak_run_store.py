"""File-backed local KovaaK run records.

Each run is stored as ``runs/{id}/meta.json``.  A simple counter file manages
auto-increment IDs.  The run record is deliberately separate from an Analysis
Session.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import stat
import threading
import time
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from kovaak_tracker.csv_parser import parse_stats_csv
from kovaak_tracker.performance_parser import parse_performance_file
from kovaak_tracker.time_alignment import TimeAlignmentError, resolve_time_window

from . import file_store
from .kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
    RetryableIngestionError,
    normalize_kovaak_stem,
)
from .workspace import session_dir

log = logging.getLogger(__name__)


from .kovaak_snapshot_codec import (
    LEGACY_SNAPSHOT_VERSION,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_POINTS,
    MAX_SNAPSHOT_SPAN_MS,
    SNAPSHOT_HEADER_SIZE,
    SNAPSHOT_MAGIC,
    SNAPSHOT_RECORD_SIZE,
    SNAPSHOT_VERSION,
    SUPPORTED_BUTTON_MASK,
    SUPPORTED_SNAPSHOT_VERSIONS,
    _canonicalize_mouse_points,
    _decode_mouse_snapshot_bytes_with_version,
    _has_valid_mouse_snapshot_header,
    _validate_snapshot_points,
    decode_mouse_snapshot_bytes,
    extract_mouse_snapshot_window,
    read_mouse_snapshot,
    read_mouse_snapshot_with_version,
    write_mouse_snapshot,
)
from .kovaak_evidence_artifacts import (
    _file_fingerprint,
    _is_app_video_artifact,
    _managed_evidence_artifact,
    _managed_run_video_path,
    _quarantine_run_video_file,
    _resolve_evidence_relpath,
    _unlink_run_evidence_artifact,
    _video_receipt_path,
)
from .kovaak_run_projection import (
    _DROP_PUBLIC_VALUE,
    _SHA256_DIGEST,
    _canonical_time_window_from_run,
    _current_video_evidence,
    _is_absolute_path_or_file_uri,
    _path_like_key,
    _public_alignment,
    _public_stats_calibration,
    _public_string,
    _public_summary,
    _run_evidence_view,
    _sanitize_public_value,
    _source_ref,
    _source_revision_availability,
    _source_stat_availability,
    _summary_source,
    _trace_quality,
    _video_quality,
    derive_run_readiness,
    public_analysis_input_snapshot,
    public_kovaak_run,
)
STATS_PARSER_VERSION = "kovaak_stats.v2"
PERFORMANCE_PARSER_VERSION = "kovaak_performance.v2"
SCENARIO_IDENTITY_VERSION = "kovaak_scenario.v1"
ANALYSIS_INPUT_SNAPSHOT_VERSION = "analysis_input_snapshot.v3"
CANONICAL_TIME_WINDOW_VERSION = "canonical_time_window.v1"
_STRICT_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_SCENARIO_DEFINITION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,159}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_CAPTURE_WINDOW_MS = 300_000


# ---- run file I/O ----

_RUNS_DIR = "runs"
_COUNTER_PATH = "runs/_counter.json"
_RUN_ID_LOCK = threading.Lock()
_EVIDENCE_TOMBSTONES = "runs/_evidence_tombstones.json"
_INCOMPLETE_TOMBSTONES = "runs/_incomplete_tombstones.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_to_wire_utc(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text.endswith("Z"):
        return text
    if " " in text and "T" not in text:
        return f"{text.replace(' ', 'T')}Z"
    if "T" in text:
        return f"{text}Z"
    return value


def _run_meta_path(run_id: int) -> str:
    return f"{_RUNS_DIR}/{run_id}/meta.json"


def _next_run_id() -> int:
    # Read-modify-write on the shared counter file; the lock keeps the
    # allocation atomic if a second caller ever runs off the event loop
    # (today the watcher path is serialized by the ingestion service's
    # finalizer_lock, so this is defensive hardening).
    with _RUN_ID_LOCK:
        data = file_store.read_json(_COUNTER_PATH)
        if data is None:
            max_id = 0
            for p in file_store.list_subdirs(_RUNS_DIR):
                try:
                    max_id = max(max_id, int(p.name))
                except ValueError:
                    continue
            data = {"next_id": max_id + 1}
        next_id = int(data.get("next_id", 1))
        data["next_id"] = next_id + 1
        file_store.write_json(_COUNTER_PATH, data)
        return next_id


def _load_run(run_id: int) -> Optional[dict]:
    return file_store.read_json(_run_meta_path(run_id))


def _save_run(run: dict) -> None:
    file_store.write_json(_run_meta_path(run["id"]), run)


def _all_runs(user_id: str | None = None) -> list[dict]:
    runs = []
    for p in file_store.list_subdirs(_RUNS_DIR):
        try:
            int(p.name)
        except ValueError:
            continue
        try:
            data = file_store.read_json(f"{_RUNS_DIR}/{p.name}/meta.json")
        except (OSError, ValueError):
            log.warning("skipping unreadable run meta %s", f"{_RUNS_DIR}/{p.name}/meta.json")
            continue
        if data is None:
            continue
        if user_id is None or data.get("user_id") == user_id:
            runs.append(data)
    runs.sort(key=lambda r: (r.get("created_at", ""), r.get("id", 0)), reverse=True)
    return runs


def _get_analysis_count_for_run(run_id: int, user_id: str) -> int:
    count = 0
    for p in file_store.list_dir("sessions", "*.json"):
        try:
            int(p.stem)
        except ValueError:
            continue
        session = file_store.read_json(f"sessions/{p.name}")
        if session and session.get("kovaak_run_id") == run_id and session.get("user_id") == user_id:
            count += 1
    return count

def _analysis_counts_by_run(user_id: str) -> dict[int, int]:
    """One pass over the sessions directory yielding run_id -> analysis count.

    ``_public_run`` used to rescan every session file per run, making run
    lists O(runs x sessions). Batch callers pass this map instead.
    """
    counts: dict[int, int] = {}
    for p in file_store.list_dir("sessions", "*.json"):
        try:
            int(p.stem)
        except ValueError:
            continue
        session = file_store.read_json(f"sessions/{p.name}")
        if not session or session.get("user_id") != user_id:
            continue
        run_id = session.get("kovaak_run_id")
        if isinstance(run_id, int):
            counts[run_id] = counts.get(run_id, 0) + 1
    return counts


def _normalize_scenario_identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _assert_same_scenario_identity(*values: str | None) -> None:
    identities = {
        normalized
        for value in values
        if (normalized := _normalize_scenario_identity(value))
    }
    if len(identities) > 1:
        raise NonRetryableIngestionError(
            "pairing_conflict: scenario identity mismatch", code="pairing_conflict",
        )


def _stat_revision(path: str | Path) -> tuple[int, int]:
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def _source_metadata(path: str | Path, parser_version: str) -> dict[str, object]:
    source = Path(path).resolve()
    before = _stat_revision(source)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = _stat_revision(source)
    if before != after:
        raise RetryableIngestionError(
            "source_unstable: source changed while fingerprinting", code="source_unstable",
        )
    return {
        "path": str(source),
        "basename": source.name,
        "sha256": digest.hexdigest(),
        "size": after[0],
        "mtime_ns": after[1],
        "parser_version": parser_version,
        "availability": "available",
    }


def _assert_source_identity(
    existing_summary: object | None,
    observed_source: dict[str, object] | None,
    kind: str,
) -> None:
    if not isinstance(existing_summary, dict) or observed_source is None:
        return
    existing_source = existing_summary.get("source")
    if not isinstance(existing_source, dict):
        return
    if existing_source.get("path") != observed_source.get("path"):
        raise NonRetryableIngestionError(
            f"pairing_conflict: second {kind} source", code="pairing_conflict",
        )
    revision_fields = ("sha256", "size", "mtime_ns", "parser_version")
    if any(
        existing_source.get(field) != observed_source.get(field)
        for field in revision_fields
    ):
        raise NonRetryableIngestionError(
            f"pairing_conflict: changed {kind} source revision", code="pairing_conflict",
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stats_time_mapping_for_performance(start_epoch_ms: int) -> dict[str, object]:
    instant = datetime.fromtimestamp(start_epoch_ms / 1_000, timezone.utc)
    local = instant.astimezone()
    offset = local.utcoffset()
    if offset is None:
        raise TimeAlignmentError("anchor_timezone_unmapped: system timezone offset unavailable")
    offset_minutes = int(offset.total_seconds() // 60)
    return {
        "version": "stats_local_to_utc.v1",
        "source": "system_timezone_at_performance_anchor",
        "utc_offset_minutes": offset_minutes,
    }


def _json(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None) -> object | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _row(row: Any) -> dict:
    """Ensure a run dict has decoded JSON fields (already dicts in file store)."""
    result = dict(row)
    return result


def _public_run(
    raw: dict, *, shallow: bool = False, analysis_count: int | None = None,
) -> dict:
    """Wrap public_kovaak_run with analysis_count injection."""
    run = dict(raw)
    run["analysis_count"] = (
        analysis_count
        if analysis_count is not None
        else _get_analysis_count_for_run(int(run["id"]), str(run.get("user_id", "")))
    )
    return public_kovaak_run(run, shallow=shallow)


async def list_kovaak_run_summaries(user_id: str, limit: int = 100) -> list[dict]:
    runs = _all_runs(user_id)[:max(1, min(limit, 500))]
    counts = _analysis_counts_by_run(user_id)
    summaries = []
    for raw in runs:
        public = _public_run(
            raw, shallow=True, analysis_count=counts.get(int(raw["id"]), 0),
        )
        public.pop("stats_summary", None)
        public.pop("performance_summary", None)
        summaries.append(public)
    return summaries


def _local_scenario_behavior_descriptor(
    scenario: object,
) -> dict[str, object] | None:
    if not isinstance(scenario, str) or not _SCENARIO_DEFINITION_NAME.fullmatch(scenario):
        return None
    from .config import resolve_kovaak_install_dir
    from kovaak_tracker.scenario_profiles import parse_local_scenario_behavior_descriptor

    install = resolve_kovaak_install_dir()
    if install is None:
        return None
    candidate = (
        install / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
        / f"{scenario}.sce"
    )
    try:
        data = candidate.read_bytes()
    except OSError:
        return None
    return parse_local_scenario_behavior_descriptor(data, expected_display_name=scenario)


async def build_analysis_input_snapshot(run_id: int, user_id: str) -> dict:
    run = await get_kovaak_run(run_id, user_id)
    if run is None:
        raise LookupError("kovaak run not found")
    sources: dict[str, dict[str, object]] = {}
    for kind, path_key, summary_key in (
        ("stats", "stats_path", "stats_summary"),
        ("performance", "performance_path", "performance_summary"),
    ):
        path = run.get(path_key)
        if not path or not Path(path).is_file():
            continue
        summary = run.get(summary_key)
        source = summary.get("source") if isinstance(summary, dict) else None
        if not isinstance(source, dict) or not source.get("sha256"):
            raise ValueError(f"source_unavailable: {kind} identity missing")
        observed = _source_metadata(
            path,
            str(source.get("parser_version") or f"kovaak_{kind}.v1"),
        )
        if observed != source:
            raise ValueError(f"source_unavailable: {kind} revision changed")
        sources[kind] = {
            "artifact_ref": _source_ref(run_id, kind, summary),
            "basename": source.get("basename"),
            "fingerprint": {
                "sha256": source.get("sha256"),
                "size": source.get("size"),
                "mtime_ns": source.get("mtime_ns"),
            },
            "path": str(Path(path).resolve()),
            "availability": "available",
            "parser_version": source.get("parser_version"),
        }
    trace: dict[str, object] | None = None
    trace_path = run.get("mouse_trace_path")
    if run.get("trace_state") == "attached" and trace_path and Path(trace_path).is_file():
        trace_version, _ = read_mouse_snapshot_with_version(trace_path)
        trace_revision = _source_metadata(
            trace_path, f"raw_input_snapshot.v{trace_version}",
        )
        trace = {
            "artifact_ref": f"run:{run_id}:trace",
            "path": str(Path(trace_path).resolve()),
            "availability": "available",
            "format_version": trace_version,
            "fingerprint": {
                "sha256": trace_revision["sha256"],
                "size": trace_revision["size"],
                "mtime_ns": trace_revision["mtime_ns"],
            },
        }
    video_availability, video = _current_video_evidence(run)
    if video_availability == "available" and video is not None:
        sources["video"] = video
    canonical_time_window = _canonical_time_window_from_run(run)
    performance_summary = run.get("performance_summary")
    performance_header = (
        performance_summary.get("header")
        if isinstance(performance_summary, dict)
        else None
    )
    observed_scenario_hash = (
        performance_header.get("scenario_hash")
        if isinstance(performance_header, dict)
        else None
    )
    from kovaak_tracker.scenario_profiles import resolve_scenario_profile

    behavior_descriptor = _local_scenario_behavior_descriptor(run.get("scenario"))
    scenario_resolution = resolve_scenario_profile(
        observed_scenario_hash if isinstance(observed_scenario_hash, str) else None,
        run.get("scenario") if isinstance(run.get("scenario"), str) else None,
        behavior_descriptor=behavior_descriptor,
    )
    return {
        "schema_version": ANALYSIS_INPUT_SNAPSHOT_VERSION,
        "run_id": run_id,
        "scenario": run.get("scenario"),
        "scenario_identity_version": SCENARIO_IDENTITY_VERSION,
        "scenario_resolution": scenario_resolution,
        "scenario_behavior_descriptor": behavior_descriptor,
        "sources": sources,
        "trace": trace,
        "canonical_time_window": canonical_time_window,
    }


async def upsert_kovaak_run(
    *,
    user_id: str,
    source_key: str,
    scenario: Optional[str] = None,
    stats_path: Optional[str] = None,
    performance_path: Optional[str] = None,
    stats_summary: object | None = None,
    performance_summary: object | None = None,
    mouse_trace_path: Optional[str] = None,
) -> dict:
    if mouse_trace_path is not None:
        read_mouse_snapshot(mouse_trace_path)
    # Find existing run by source_key
    existing = None
    for run in _all_runs(user_id):
        if run.get("source_key") == source_key:
            existing = run
            break
    now = _utc_now()
    if existing is not None:
        existing["scenario"] = scenario or existing.get("scenario")
        existing["stats_path"] = stats_path or existing.get("stats_path")
        existing["performance_path"] = performance_path or existing.get("performance_path")
        existing["mouse_trace_path"] = mouse_trace_path or existing.get("mouse_trace_path")
        if mouse_trace_path is not None:
            existing["trace_state"] = "attached"
            existing["pending_trace_path"] = None
            existing["trace_error"] = None
        if stats_summary is not None:
            existing["stats_summary"] = stats_summary
        if performance_summary is not None:
            existing["performance_summary"] = performance_summary
        existing["updated_at"] = now
        _save_run(existing)
        return existing
    else:
        run_id = _next_run_id()
        run = {
            "id": run_id,
            "user_id": user_id,
            "source_key": source_key,
            "scenario": scenario,
            "stats_path": stats_path,
            "performance_path": performance_path,
            "mouse_trace_path": mouse_trace_path,
            "trace_state": "attached" if mouse_trace_path is not None else "none",
            "pending_trace_path": None,
            "trace_error": None,
            "capture_session_id": None,
            "window_start_epoch_ms": None,
            "window_end_epoch_ms": None,
            "alignment_state": "unresolved",
            "alignment_summary": None,
            "finalization_state": "discovered",
            "finalization_error": None,
            "video_path": None,
            "video_state": "none",
            "pending_video_path": None,
            "video_request_digest": None,
            "video_receipt": None,
            "video_summary": None,
            "video_error": None,
            "stats_summary": stats_summary,
            "performance_summary": performance_summary,
            "created_at": now,
            "updated_at": now,
        }
        _save_run(run)
        return run


async def attach_mouse_trace_snapshot_window(
    run: dict,
    *,
    user_id: str,
    raw_input_snapshot_path: str | Path | None,
    covered_through_epoch_ms: int | None = None,
    raw_snapshot_receipt: dict[str, object] | None = None,
    require_coverage: bool = False,
) -> dict:
    start_ms = run.get("window_start_epoch_ms")
    end_ms = run.get("window_end_epoch_ms")
    if (
        isinstance(start_ms, bool)
        or isinstance(end_ms, bool)
        or not isinstance(start_ms, int)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        return run
    within_retention = _now_ms() <= end_ms + MAX_SNAPSHOT_SPAN_MS
    if raw_snapshot_receipt is not None:
        receipt_error, covered_through_epoch_ms = _raw_snapshot_receipt_quality(
            raw_snapshot_receipt,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        if receipt_error is not None:
            return await mark_mouse_trace_unavailable(
                run["id"], user_id, receipt_error,
            ) or run
    if require_coverage and (
        isinstance(covered_through_epoch_ms, bool)
        or not isinstance(covered_through_epoch_ms, int)
        or covered_through_epoch_ms < end_ms
    ):
        if within_retention:
            await mark_mouse_trace_waiting(run["id"], user_id)
            raise RetryableIngestionError(
                "trace_pending: Raw Input snapshot coverage is not ready",
                code="trace_pending",
            )
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_snapshot_stale",
        ) or run
    if not raw_input_snapshot_path or not Path(raw_input_snapshot_path).is_file():
        if within_retention:
            await mark_mouse_trace_waiting(run["id"], user_id)
            raise RetryableIngestionError(
                "trace_pending: waiting for Raw Input snapshot", code="trace_pending",
            )
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_capture_unavailable",
        ) or run

    from . import config

    target = (
        config.DATA_ROOT / "runs" / str(run["id"])
        / f"trace-{uuid4().hex}.bin"
    )
    run = await begin_mouse_trace_attach(run["id"], user_id, target) or run
    try:
        count = extract_mouse_snapshot_window(
            raw_input_snapshot_path,
            start_ms,
            end_ms,
            target,
        )
    except (OSError, ValueError) as error:
        if within_retention:
            await mark_mouse_trace_waiting(
                run["id"], user_id, expected_pending_trace_path=target,
            )
            raise RetryableIngestionError(
                "trace_pending: Raw Input snapshot is not ready",
                code="trace_pending",
            ) from error
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_snapshot_failed",
            expected_pending_trace_path=target,
        ) or run
    if not count:
        if within_retention:
            await mark_mouse_trace_waiting(
                run["id"], user_id, expected_pending_trace_path=target,
            )
            raise RetryableIngestionError(
                "trace_pending: trace window is not flushed yet", code="trace_pending",
            )
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_quality_insufficient",
            expected_pending_trace_path=target,
        ) or run
    try:
        return await attach_mouse_trace(
            run["id"], user_id, str(target),
            expected_pending_trace_path=target,
        ) or run
    except (OSError, ValueError):
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_attach_failed",
            expected_pending_trace_path=target,
        ) or run


def _raw_snapshot_receipt_quality(
    receipt: dict[str, object],
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[str | None, int | None]:
    legacy_keys = {
        "coveredThroughEpochMs",
        "snapshotAtEpochMs",
        "pointCount",
        "clockSource",
        "timebaseVersion",
    }
    v2_keys = legacy_keys | {
        "receiptVersion",
        "captureSessionStartEpochMs",
        "queueDroppedPoints",
        "queueDropFirstEpochMs",
        "queueDropLastEpochMs",
        "ringExpiredPoints",
        "ringExpiredThroughEpochMs",
    }
    if set(receipt) == legacy_keys:
        return "trace_legacy_quality_unknown", None
    if (
        set(receipt) != v2_keys
        or receipt.get("receiptVersion") != "raw_snapshot_receipt.v2"
    ):
        return "trace_quality_insufficient", None

    values: dict[str, int] = {}
    for key in (
        "captureSessionStartEpochMs",
        "coveredThroughEpochMs",
        "snapshotAtEpochMs",
        "pointCount",
        "queueDroppedPoints",
        "ringExpiredPoints",
    ):
        value = receipt.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "trace_quality_insufficient", None
        values[key] = value
    if (
        values["coveredThroughEpochMs"] > values["snapshotAtEpochMs"]
        or receipt.get("clockSource") != "utc_epoch_ms+qpc"
        or receipt.get("timebaseVersion") != "time_alignment.v2"
    ):
        return "trace_quality_insufficient", None
    queue_first = receipt.get("queueDropFirstEpochMs")
    queue_last = receipt.get("queueDropLastEpochMs")
    if values["queueDroppedPoints"] == 0:
        if queue_first is not None or queue_last is not None:
            return "trace_quality_insufficient", None
    elif (
        isinstance(queue_first, bool)
        or isinstance(queue_last, bool)
        or not isinstance(queue_first, int)
        or not isinstance(queue_last, int)
        or queue_first > queue_last
    ):
        return "trace_quality_insufficient", None
    elif queue_first < end_ms and queue_last >= start_ms:
        return "trace_raw_queue_dropped", values["coveredThroughEpochMs"]

    ring_through = receipt.get("ringExpiredThroughEpochMs")
    if values["ringExpiredPoints"] == 0:
        if ring_through is not None:
            return "trace_quality_insufficient", None
    elif (
        isinstance(ring_through, bool)
        or not isinstance(ring_through, int)
    ):
        return "trace_quality_insufficient", None
    elif ring_through >= start_ms:
        return "trace_raw_ring_expired", values["coveredThroughEpochMs"]
    if values["captureSessionStartEpochMs"] > start_ms:
        return "trace_raw_window_coverage_gap", values["coveredThroughEpochMs"]
    return None, values["coveredThroughEpochMs"]


async def ingest_discovery(
    discovery: KovaaKFileDiscovery,
    *,
    user_id: str = "desktop-local",
    trace_path: Optional[str] = None,
    raw_input_snapshot_path: str | Path | None = None,
    raw_snapshot_covered_through_epoch_ms: int | None = None,
    require_stats_for_trace: bool = False,
    defer_trace_attachment: bool = False,
) -> dict:
    stats_summary: object | None = None
    performance_summary: object | None = None
    stats_source: dict[str, object] | None = None
    performance_source: dict[str, object] | None = None
    performance = None
    stats = None
    stats_event_times_seconds: list[float] = []
    stats_pause_count: str | None = None
    stats_scenario: Optional[str] = None
    performance_scenario: Optional[str] = None
    if discovery.stats_path is not None:
        stats_revision = _stat_revision(discovery.stats_path)
        stats = parse_stats_csv(discovery.stats_path)
        stats_source = _source_metadata(discovery.stats_path, STATS_PARSER_VERSION)
        if stats_revision != (stats_source["size"], stats_source["mtime_ns"]):
            raise RetryableIngestionError(
                "source_unstable: Stats changed while parsing", code="source_unstable",
            )
        stats_scenario = stats.scenario
        stats_summary = {
            "file_name": stats.file_name,
            "summary": stats.summary,
            "config": stats.config,
            "cm_per_360": getattr(stats, "cm_per_360", None),
            "kill_count": int(len(stats.kills.index)),
            "weapon_aggregates": list(
                getattr(stats, "weapon_aggregates", ()) or ()
            ),
            "field_presence": dict(
                getattr(stats, "field_presence", {}) or {}
            ),
            "source": stats_source,
        }
        stats_pause_count = stats.summary.get("Pause Count")
    if discovery.performance_path is not None:
        performance_revision = _stat_revision(discovery.performance_path)
        performance = parse_performance_file(discovery.performance_path)
        performance_source = _source_metadata(
            discovery.performance_path, PERFORMANCE_PARSER_VERSION,
        )
        if performance_revision != (
            performance_source["size"], performance_source["mtime_ns"],
        ):
            raise RetryableIngestionError(
                "source_unstable: Performance changed while parsing", code="source_unstable",
            )
        performance_scenario = performance.header.scenario_name or None
        performance_summary = {
            "header": asdict(performance.header),
            "event_count": len(performance.events),
            "source_event_count": performance.source_event_count,
            "omitted_event_indexes": list(performance.omitted_event_indexes),
            "post_window_event_count": performance.post_window_event_count,
            "timeline_status": performance.timeline_status,
            "unknown_field_observability": performance.unknown_field_observability,
            "source": performance_source,
        }
        profile = performance.header.challenge_profile
        event_terminated = bool(
            any(value > 0 for value in profile.bot_max_lives)
            or profile.end_challenge_after_kills > 0
            or profile.end_challenge_after_damage > 0
        )
        if event_terminated and stats is not None and "time_s" in stats.kills:
            stats_event_times_seconds = [
                float(value)
                for value in stats.kills["time_s"].tolist()
                if value == value and float(value) >= 0
            ]
    source_key = discovery.stem or normalize_kovaak_stem(discovery.paths[0])
    existing = None
    for run in _all_runs(user_id):
        if run.get("source_key") == source_key:
            existing = run
            break
    _assert_same_scenario_identity(
        existing.get("scenario") if existing else None,
        stats_scenario,
        performance_scenario,
    )
    _assert_source_identity(
        existing.get("stats_summary") if existing else None,
        stats_source,
        "Stats",
    )
    _assert_source_identity(
        existing.get("performance_summary") if existing else None,
        performance_source,
        "Performance",
    )
    if (
        existing
        and existing.get("finalization_state") == "finalized"
        and existing.get("finalization_error") == "video_coverage_gap"
    ):
        return existing
    if existing and len(discovery.paths) == 1:
        if (
            stats_source is not None
            and not existing.get("performance_path")
            and existing.get("trace_state") == "none"
            and _summary_source(existing.get("stats_summary")) == stats_source
        ):
            return existing
        if (
            performance_source is not None
            and not existing.get("stats_path")
            and existing.get("trace_state") == "none"
            and _summary_source(existing.get("performance_summary")) == performance_source
        ):
            return existing
    performance_revision_unchanged = bool(
        existing
        and isinstance(existing.get("performance_summary"), dict)
        and existing["performance_summary"].get("source") == performance_source
    )
    scenario = stats_scenario or performance_scenario
    run = await upsert_kovaak_run(
        user_id=user_id,
        source_key=source_key,
        scenario=scenario,
        stats_path=str(discovery.stats_path) if discovery.stats_path else None,
        performance_path=str(discovery.performance_path) if discovery.performance_path else None,
        stats_summary=stats_summary,
        performance_summary=performance_summary,
    )
    if trace_path is not None:
        try:
            run = await attach_mouse_trace(run["id"], user_id, trace_path) or run
        except (OSError, ValueError):
            return await mark_mouse_trace_unavailable(
                run["id"], user_id, "trace_attach_failed",
            ) or run
    has_alignment_window = bool(
        performance is not None
        and performance.header.challenge_start_utc > 0
        and performance.header.challenge_profile.time_limit > 0
    )
    has_trace_window = bool(
        has_alignment_window and (stats is not None or not require_stats_for_trace)
    )
    alignment_window = None
    alignment_error: str | None = None
    if performance is not None and has_alignment_window:
        stats_start = None
        if isinstance(stats_summary, dict):
            summary = stats_summary.get("summary")
            if isinstance(summary, dict):
                stats_start = summary.get("Challenge Start")
        try:
            alignment_window = resolve_time_window(
                performance,
                stats_challenge_start=stats_start,
                stats_event_times_seconds=stats_event_times_seconds,
                pause_count=stats_pause_count,
                stats_local_to_utc_mapping=_stats_time_mapping_for_performance(
                    performance.header.challenge_start_utc
                ),
            )
        except TimeAlignmentError as error:
            alignment_error = str(error)
            alignment_window = None
    if alignment_window is not None:
        run = await set_run_alignment(
            run["id"],
            user_id,
            state="resolved",
            summary=asdict(alignment_window),
            start_epoch_ms=alignment_window.start_ms,
            end_epoch_ms=alignment_window.end_ms,
        ) or run
    elif alignment_error is not None:
        error_prefix = alignment_error.split(":", 1)[0]
        error_code = (
            error_prefix
            if error_prefix in {
                "pause_unsupported",
                "anchor_conflict",
                "anchor_timezone_unmapped",
                "duration_missing",
            }
            else "time_alignment_unavailable"
        )
        run = await set_run_alignment(
            run["id"],
            user_id,
            state="unavailable",
            summary={
                "timebase_version": "time_alignment.v2",
                "error_code": error_code,
            },
        ) or run
    if (
        trace_path is None
        and has_trace_window
        and existing
        and existing.get("trace_state") == "attached"
        and performance_revision_unchanged
    ):
        return run
    if (
        trace_path is None
        and has_trace_window
        and alignment_window is not None
        and not defer_trace_attachment
    ):
        run = await attach_mouse_trace_snapshot_window(
            run,
            user_id=user_id,
            raw_input_snapshot_path=raw_input_snapshot_path,
            covered_through_epoch_ms=raw_snapshot_covered_through_epoch_ms,
            require_coverage=(
                require_stats_for_trace
                or raw_snapshot_covered_through_epoch_ms is not None
            ),
        )
    return run


async def list_kovaak_runs(user_id: str, limit: int = 100) -> list[dict]:
    return [_row(run) for run in _all_runs(user_id)[:max(1, min(limit, 500))]]


async def get_kovaak_run(run_id: int, user_id: str) -> Optional[dict]:
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    return run


async def get_kovaak_run_any_owner(run_id: int) -> Optional[dict]:
    return _load_run(run_id)


async def set_run_alignment(
    run_id: int,
    user_id: str,
    *,
    state: str,
    summary: object | None,
    start_epoch_ms: int | None = None,
    end_epoch_ms: int | None = None,
) -> Optional[dict]:
    if state not in {"unresolved", "resolved", "unavailable"}:
        raise ValueError("alignment state is invalid")
    if state == "resolved":
        if (
            isinstance(start_epoch_ms, bool)
            or isinstance(end_epoch_ms, bool)
            or not isinstance(start_epoch_ms, int)
            or not isinstance(end_epoch_ms, int)
            or end_epoch_ms <= start_epoch_ms
        ):
            raise ValueError("resolved alignment window is invalid")
    else:
        start_epoch_ms = None
        end_epoch_ms = None
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    run["alignment_state"] = state
    run["alignment_summary"] = summary
    run["window_start_epoch_ms"] = start_epoch_ms
    run["window_end_epoch_ms"] = end_epoch_ms
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def set_run_finalization_state(
    run_id: int,
    user_id: str,
    state: str,
    error: str | None = None,
) -> Optional[dict]:
    if state not in {"discovered", "pending", "retryable", "finalized"}:
        raise ValueError("finalization state is invalid")
    if error is not None and _ERROR_CODE.fullmatch(error) is None:
        raise ValueError("finalization error code is invalid")
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    run["finalization_state"] = state
    run["finalization_error"] = error
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def get_kovaak_run_by_source_key(
    user_id: str,
    source_key: str,
) -> Optional[dict]:
    for run in _all_runs(user_id):
        if run.get("source_key") == source_key:
            return run
    return None


def _strict_receipt_integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"capture receipt {field} is invalid")
    return value


def _assert_exact_receipt_keys(
    value: object,
    expected: set[str],
    field: str,
) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"capture receipt {field} is invalid")
    return value


def _validate_video_receipt(
    video_path: Path,
    *,
    run_id: int,
    request_id: str,
    request_digest: str,
    capture_session_id: str,
    start_epoch_ms: int,
    end_epoch_ms: int,
) -> tuple[dict, dict[str, object]]:
    receipt_path = _video_receipt_path(video_path)
    if not video_path.is_file() or not receipt_path.is_file():
        raise ValueError("capture receipt or MP4 is missing")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("capture receipt is malformed") from error
    receipt = _assert_exact_receipt_keys(
        receipt,
        {
            "version",
            "requestDigest",
            "requestId",
            "runId",
            "captureSessionId",
            "startEpochMs",
            "endEpochMs",
            "replay",
            "file",
        },
        "root",
    )
    if receipt.get("version") != "capture_receipt.v1":
        raise ValueError("capture receipt version is invalid")
    if receipt.get("requestDigest") != request_digest:
        raise ValueError("capture receipt request digest does not match")
    if receipt.get("requestId") != request_id:
        raise ValueError("capture receipt request id does not match")
    if receipt.get("runId") != run_id:
        raise ValueError("capture receipt Run id does not match")
    if receipt.get("captureSessionId") != capture_session_id:
        raise ValueError("capture receipt capture session does not match")
    if (
        receipt.get("startEpochMs") != start_epoch_ms
        or receipt.get("endEpochMs") != end_epoch_ms
    ):
        raise ValueError("capture receipt canonical window does not match")

    replay = _assert_exact_receipt_keys(
        receipt.get("replay"),
        {
            "requestedStart100ns",
            "requestedEnd100ns",
            "decodeStart100ns",
            "visibleDuration100ns",
            "decodePreroll100ns",
            "packetCount",
            "encodedBytes",
            "reencodedFrames",
            "captureClock",
        },
        "replay",
    )
    requested_start = _strict_receipt_integer(
        replay.get("requestedStart100ns"), "requestedStart100ns",
    )
    requested_end = _strict_receipt_integer(
        replay.get("requestedEnd100ns"), "requestedEnd100ns", minimum=1,
    )
    if requested_end <= requested_start:
        raise ValueError("capture receipt replay window is invalid")
    _strict_receipt_integer(replay.get("decodeStart100ns"), "decodeStart100ns")
    visible_duration = _strict_receipt_integer(
        replay.get("visibleDuration100ns"), "visibleDuration100ns", minimum=1,
    )
    _strict_receipt_integer(replay.get("decodePreroll100ns"), "decodePreroll100ns")
    packet_count = _strict_receipt_integer(
        replay.get("packetCount"), "packetCount", minimum=1,
    )
    _strict_receipt_integer(replay.get("encodedBytes"), "encodedBytes")
    _strict_receipt_integer(replay.get("reencodedFrames"), "reencodedFrames")
    clock = _assert_exact_receipt_keys(
        replay.get("captureClock"),
        {"utcEpochMs", "qpcNs", "clockSource", "timebaseVersion"},
        "captureClock",
    )
    _strict_receipt_integer(clock.get("utcEpochMs"), "utcEpochMs")
    _strict_receipt_integer(clock.get("qpcNs"), "qpcNs")
    if clock.get("clockSource") != "utc_epoch_ms+qpc+wgc_system_relative_time":
        raise ValueError("capture receipt clock source is invalid")
    if clock.get("timebaseVersion") != "time_alignment.v2":
        raise ValueError("capture receipt timebase is invalid")

    stored_file = _assert_exact_receipt_keys(
        receipt.get("file"), {"size", "digest"}, "file",
    )
    stored_size = _strict_receipt_integer(stored_file.get("size"), "file size")
    stored_digest = stored_file.get("digest")
    if not isinstance(stored_digest, str) or not _SHA256_DIGEST.fullmatch(stored_digest):
        raise ValueError("capture receipt file fingerprint is invalid")
    observed = _file_fingerprint(video_path)
    if observed != {"sha256": stored_digest.lower(), "size": stored_size}:
        raise ValueError("capture receipt file fingerprint does not match")

    summary = {
        "availability": "available",
        "fingerprint": observed,
        "packetCount": packet_count,
        "visibleDuration100ns": visible_duration,
        "timebaseVersion": clock["timebaseVersion"],
    }
    return receipt, summary


async def begin_run_video_attach(
    run_id: int,
    user_id: str,
    *,
    pending_video_path: str | Path,
    request_digest: str,
    capture_session_id: str,
    start_epoch_ms: int,
    end_epoch_ms: int,
    data_root: str | Path,
    alignment_summary: object | None = None,
) -> Optional[dict]:
    candidate, _request_id = _managed_run_video_path(
        data_root, run_id, pending_video_path,
    )
    if not isinstance(request_digest, str) or not _SHA256_DIGEST.fullmatch(
        request_digest
    ):
        raise ValueError("video request digest is invalid")
    request_digest = request_digest.lower()
    if (
        not isinstance(capture_session_id, str)
        or not 8 <= len(capture_session_id) <= 128
        or _STRICT_IDENTIFIER.fullmatch(capture_session_id) is None
    ):
        raise ValueError("capture session id is invalid")
    if (
        isinstance(start_epoch_ms, bool)
        or isinstance(end_epoch_ms, bool)
        or not isinstance(start_epoch_ms, int)
        or not isinstance(end_epoch_ms, int)
        or end_epoch_ms <= start_epoch_ms
        or end_epoch_ms - start_epoch_ms > MAX_CAPTURE_WINDOW_MS
    ):
        raise ValueError("canonical capture window is invalid")

    current = await get_kovaak_run(run_id, user_id)
    if current is None or current.get("video_state") == "attached":
        return current
    if current.get("video_state") == "pending":
        return current

    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if run.get("video_state") not in ("none", "unavailable"):
        return run
    run["capture_session_id"] = capture_session_id
    run["window_start_epoch_ms"] = start_epoch_ms
    run["window_end_epoch_ms"] = end_epoch_ms
    run["alignment_state"] = "resolved"
    run["alignment_summary"] = alignment_summary
    run["finalization_state"] = "pending"
    run["finalization_error"] = None
    run["video_path"] = None
    run["video_state"] = "pending"
    run["pending_video_path"] = str(candidate)
    run["video_request_digest"] = request_digest
    run["video_receipt"] = None
    run["video_summary"] = None
    run["video_error"] = None
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def mark_run_video_unavailable(
    run_id: int,
    user_id: str,
    error: str,
    *,
    expected_pending_video_path: str | Path | None = None,
    expected_request_digest: str | None = None,
) -> Optional[dict]:
    if not isinstance(error, str) or _ERROR_CODE.fullmatch(error) is None:
        raise ValueError("video error code is invalid")
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if expected_pending_video_path is not None:
        if run.get("video_state") != "pending":
            return run
        expected = str(Path(expected_pending_video_path).resolve())
        if run.get("pending_video_path") != expected:
            return run
    else:
        if run.get("video_state") == "attached":
            return run
    if expected_request_digest is not None:
        if _SHA256_DIGEST.fullmatch(expected_request_digest) is None:
            raise ValueError("video request digest is invalid")
        if run.get("video_request_digest") != expected_request_digest.lower():
            return run
    run["video_path"] = None
    run["video_state"] = "unavailable"
    run["pending_video_path"] = None
    run["video_receipt"] = None
    run["video_summary"] = None
    run["video_error"] = error
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def invalidate_run_for_video_coverage_gap(
    run_id: int,
    user_id: str,
    *,
    expected_pending_video_path: str | Path,
    expected_request_digest: str,
    data_root: str | Path,
) -> Optional[dict]:
    """Mark only the video unavailable; the trace stays for input_native."""
    candidate, _request_id = _managed_run_video_path(
        data_root, run_id, expected_pending_video_path,
    )
    if _SHA256_DIGEST.fullmatch(expected_request_digest) is None:
        raise ValueError("video request digest is invalid")
    expected_request_digest = expected_request_digest.lower()

    run = _load_run(run_id)
    if run is None:
        return None
    if run.get("user_id") != user_id:
        raise PermissionError("kovaak run is not owned by this user")
    if (
        run.get("finalization_state") == "finalized"
        and run.get("finalization_error") == "video_coverage_gap"
        and run.get("video_state") == "unavailable"
    ):
        return run
    if (
        run.get("video_state") != "pending"
        or run.get("pending_video_path") != str(candidate)
        or run.get("video_request_digest") != expected_request_digest
    ):
        raise ValueError("video pending state changed before coverage invalidation")

    run["video_path"] = None
    run["video_state"] = "unavailable"
    run["pending_video_path"] = None
    run["video_receipt"] = None
    run["video_summary"] = None
    run["video_error"] = "video_coverage_gap"
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def attach_run_video(
    run_id: int,
    user_id: str,
    video_path: str | Path,
    *,
    expected_pending_video_path: str | Path,
    expected_request_digest: str,
    data_root: str | Path,
) -> Optional[dict]:
    candidate, request_id = _managed_run_video_path(data_root, run_id, video_path)
    expected_path = Path(expected_pending_video_path).resolve()
    if candidate != expected_path:
        raise ValueError("video attachment path does not match pending path")
    if _SHA256_DIGEST.fullmatch(expected_request_digest) is None:
        raise ValueError("video request digest is invalid")
    expected_request_digest = expected_request_digest.lower()

    current = await get_kovaak_run(run_id, user_id)
    if current is None:
        return None
    if current.get("video_state") == "pending" and (
        current.get("pending_video_path") != str(expected_path)
        or current.get("video_request_digest") != expected_request_digest
    ):
        return current
    if current.get("video_state") not in {"pending", "attached"}:
        return current

    receipt, summary = _validate_video_receipt(
        candidate,
        run_id=run_id,
        request_id=request_id,
        request_digest=expected_request_digest,
        capture_session_id=current.get("capture_session_id"),
        start_epoch_ms=current.get("window_start_epoch_ms"),
        end_epoch_ms=current.get("window_end_epoch_ms"),
    )
    if current.get("video_state") == "attached":
        if (
            current.get("video_path") == str(candidate)
            and current.get("video_request_digest") == expected_request_digest
            and current.get("video_receipt") == receipt
            and current.get("video_summary") == summary
        ):
            return current
        raise ValueError("attached video artifact conflicts with existing evidence")

    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if run.get("video_state") != "pending":
        return run
    run["video_path"] = str(candidate)
    run["video_state"] = "attached"
    run["pending_video_path"] = None
    run["video_receipt"] = receipt
    run["video_summary"] = summary
    run["video_error"] = None
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def _mark_run_video_waiting(
    run_id: int,
    user_id: str,
    pending_video_path: str | Path,
    request_digest: str,
) -> None:
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return
    if run.get("video_state") != "pending":
        return
    if run.get("pending_video_path") != str(Path(pending_video_path).resolve()):
        return
    if run.get("video_request_digest") != request_digest:
        return
    run["video_error"] = "video_waiting_artifact"
    run["updated_at"] = _utc_now()
    _save_run(run)


async def _cleanup_evidence_tombstone(
    row: dict[str, object], data_root: str | Path,
) -> tuple[bool, int]:
    run_id = int(row["run_id"])
    evidence_kind = str(row["evidence_kind"])
    try:
        artifact = _resolve_evidence_relpath(
            data_root, run_id, evidence_kind, str(row["artifact_relpath"]),
        )
        if artifact.is_file():
            observed = _file_fingerprint(artifact)
            expected = {
                "sha256": str(row["expected_sha256"]),
                "size": int(row["expected_size"]),
            }
            if observed != expected:
                raise OSError("Run evidence fingerprint changed before cleanup")
        if evidence_kind == "video":
            await _remove_analysis_video_aliases(
                run_id,
                str(row["owner_id"]),
                artifact,
            )
        reclaimed = _unlink_run_evidence_artifact(artifact)
        if evidence_kind == "video":
            reclaimed += _unlink_run_evidence_artifact(_video_receipt_path(artifact))
    except (OSError, ValueError):
        tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
        for t in tombstones:
            if t.get("run_id") == run_id and t.get("evidence_kind") == evidence_kind:
                t["cleanup_state"] = "failed"
                t["cleanup_attempts"] = int(t.get("cleanup_attempts", 0)) + 1
                t["last_error_code"] = "artifact_cleanup_failed"
        file_store.write_json(_EVIDENCE_TOMBSTONES, tombstones)
        return False, 0

    tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
    tombstones = [
        t for t in tombstones
        if not (t.get("run_id") == run_id and t.get("evidence_kind") == evidence_kind)
    ]
    file_store.write_json(_EVIDENCE_TOMBSTONES, tombstones)
    return True, reclaimed


async def _remove_analysis_video_aliases(
    run_id: int,
    owner_id: str,
    run_video: Path,
) -> None:
    for p in file_store.list_dir("sessions", "*.json"):
        try:
            session_id = int(p.stem)
        except ValueError:
            continue
        session = file_store.read_json(f"sessions/{p.name}")
        if session is None:
            continue
        if session.get("kovaak_run_id") != run_id or session.get("user_id") != owner_id:
            continue
        alias = session_dir(session_id) / "video.mp4"
        stored_path = session.get("video_path")
        if not isinstance(stored_path, str) or Path(stored_path).resolve() != alias.resolve():
            continue
        try:
            metadata = alias.lstat()
        except FileNotFoundError:
            continue
        if alias.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise OSError("Analysis video alias is not a regular file")
        run_video = run_video.resolve()
        if run_video.is_file() and not alias.samefile(run_video):
            continue
        alias.unlink()
        session["video_path"] = None
        file_store.write_json(f"sessions/{p.name}", session)


def _removal_result(
    run_id: int,
    evidence_kind: str,
    removal_state: str,
    reclaimed_bytes: int,
    artifact_ref: str | None,
) -> dict[str, object]:
    return {
        "run_ref": f"run:{run_id}",
        "evidence_kind": evidence_kind,
        "artifact_ref": artifact_ref,
        "availability": "unavailable",
        "removal_state": removal_state,
        "reclaimed_bytes": reclaimed_bytes,
        "affected_modes": (
            ["multimodal", "video_fallback"]
            if evidence_kind == "video"
            else ["input_native", "multimodal"]
        ),
    }


async def remove_run_evidence(
    run_id: int,
    user_id: str,
    evidence_kind: str,
    data_root: str | Path,
) -> dict[str, object]:
    if evidence_kind not in {"video", "raw"}:
        raise ValueError("evidence kind must be video or raw")
    run = _load_run(run_id)
    if run is None:
        raise LookupError("kovaak run not found")
    if run.get("user_id") != user_id:
        raise PermissionError("kovaak run is not owned by this user")

    tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
    existing = None
    for t in tombstones:
        if t.get("run_id") == run_id and t.get("evidence_kind") == evidence_kind:
            existing = t
            break
    if existing is not None:
        completed, reclaimed = await _cleanup_evidence_tombstone(existing, data_root)
        return _removal_result(
            run_id, evidence_kind,
            "completed" if completed else "pending_cleanup",
            reclaimed if completed else 0,
            None,
        )

    state = run.get("video_state") if evidence_kind == "video" else run.get("trace_state")
    path_value = run.get("video_path") if evidence_kind == "video" else run.get("mouse_trace_path")
    if state != "attached" or not path_value:
        return _removal_result(run_id, evidence_kind, "already_unavailable", 0, None)

    artifact, relative_path = _managed_evidence_artifact(
        data_root, run_id, evidence_kind, path_value,
    )
    fingerprint = _file_fingerprint(artifact)
    artifact_ref = (
        f"run:{run_id}:video:{str(fingerprint['sha256'])[:16]}"
        if evidence_kind == "video"
        else f"run:{run_id}:trace"
    )
    tombstones.append({
        "run_id": run_id,
        "evidence_kind": evidence_kind,
        "owner_id": user_id,
        "artifact_relpath": relative_path,
        "expected_sha256": fingerprint["sha256"],
        "expected_size": fingerprint["size"],
    })
    file_store.write_json(_EVIDENCE_TOMBSTONES, tombstones)

    if evidence_kind == "video":
        run["video_path"] = None
        run["video_state"] = "unavailable"
        run["pending_video_path"] = None
        run["video_error"] = "removed_by_user"
    else:
        run["mouse_trace_path"] = None
        run["trace_state"] = "unavailable"
        run["pending_trace_path"] = None
        run["trace_error"] = "removed_by_user"
    run["updated_at"] = _utc_now()
    _save_run(run)

    tombstone = {
        "run_id": run_id,
        "evidence_kind": evidence_kind,
        "owner_id": user_id,
        "artifact_relpath": relative_path,
        "expected_sha256": fingerprint["sha256"],
        "expected_size": fingerprint["size"],
    }
    completed, reclaimed = await _cleanup_evidence_tombstone(tombstone, data_root)
    return _removal_result(
        run_id, evidence_kind,
        "completed" if completed else "pending_cleanup",
        reclaimed if completed else 0,
        artifact_ref,
    )


async def reconcile_run_evidence_deletions(
    data_root: str | Path,
) -> dict[str, int]:
    tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
    outcome = {"completed": 0, "failed": 0}
    for row in tombstones:
        completed, _reclaimed = await _cleanup_evidence_tombstone(row, data_root)
        outcome["completed" if completed else "failed"] += 1
    return outcome


async def run_storage_usage(
    user_id: str, data_root: str | Path,
) -> dict[str, int]:
    totals = {
        "run_video_bytes": 0,
        "run_raw_bytes": 0,
        "incomplete_recovery_bytes": 0,
    }
    root = Path(data_root).resolve()
    for run in _all_runs(user_id):
        run_id = int(run["id"])
        run_root = (root / "runs" / str(run_id)).resolve()
        if not run_root.is_dir():
            continue
        video_files: set[Path] = set()
        raw_files: set[Path] = set()
        try:
            if run.get("video_state") == "attached" and run.get("video_path"):
                video, _ = _managed_evidence_artifact(
                    data_root, run_id, "video", run["video_path"],
                )
                video_files.update({video, _video_receipt_path(video)})
            if run.get("trace_state") == "attached" and run.get("mouse_trace_path"):
                trace, _ = _managed_evidence_artifact(
                    data_root, run_id, "raw", run["mouse_trace_path"],
                )
                raw_files.add(trace)
        except ValueError:
            pass
        for directory, _, names in os.walk(run_root, followlinks=False):
            for name in names:
                candidate = Path(directory) / name
                if candidate.name == "meta.json":
                    # store-internal run metadata is not a user artifact.
                    continue
                try:
                    metadata = candidate.lstat()
                except OSError:
                    continue
                if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    continue
                resolved = candidate.resolve()
                if resolved in video_files:
                    totals["run_video_bytes"] += metadata.st_size
                elif resolved in raw_files:
                    totals["run_raw_bytes"] += metadata.st_size
                else:
                    totals["incomplete_recovery_bytes"] += metadata.st_size
    return totals


def _incomplete_reason(path: Path) -> str:
    name = path.name.casefold()
    if "partial" in name or "recovery" in name or name.endswith(".tmp"):
        return "interrupted_finalization"
    return "unclassified_capture_artifact"


def _incomplete_item(
    *,
    owner_id: str,
    run_id: int,
    relative_path: str,
    path: Path,
) -> dict[str, object]:
    fingerprint = _file_fingerprint(path)
    item_key = json.dumps(
        {
            "owner_id": owner_id,
            "run_id": run_id,
            "relative_path": relative_path,
            "sha256": fingerprint["sha256"],
            "size": fingerprint["size"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    item_ref = f"incomplete:{hashlib.sha256(item_key.encode('utf-8')).hexdigest()[:32]}"
    return {
        "schema_version": "incomplete_capture_item.v1",
        "item_ref": item_ref,
        "run_ref": f"run:{run_id}",
        "size_bytes": int(fingerprint["size"]),
        "reason": _incomplete_reason(path),
        "removable": True,
        "impact": {
            "code": "incomplete_recovery_only",
            "message": (
                "Only this incomplete recovery artifact will be removed; "
                "Run evidence and user source files are unchanged."
            ),
        },
        "created_at": datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "_relative_path": relative_path,
        "_sha256": fingerprint["sha256"],
    }


async def list_incomplete_capture_items(
    owner_id: str,
    data_root: str | Path,
) -> list[dict[str, object]]:
    root = Path(data_root).resolve()
    items: list[dict[str, object]] = []
    for run in _all_runs(owner_id):
        run_id = int(run["id"])
        run_root = (root / "runs" / str(run_id)).resolve()
        if not run_root.is_dir():
            continue
        owned: set[Path] = set()
        try:
            if run.get("video_state") == "attached" and run.get("video_path"):
                video, _ = _managed_evidence_artifact(
                    data_root, run_id, "video", run["video_path"],
                )
                owned.update({video, _video_receipt_path(video)})
            if run.get("trace_state") == "attached" and run.get("mouse_trace_path"):
                raw, _ = _managed_evidence_artifact(
                    data_root, run_id, "raw", run["mouse_trace_path"],
                )
                owned.add(raw)
        except ValueError:
            pass
        for directory, _, names in os.walk(run_root, followlinks=False):
            for name in names:
                candidate = Path(directory) / name
                try:
                    metadata = candidate.lstat()
                except OSError:
                    continue
                if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    continue
                resolved = candidate.resolve()
                if resolved in owned:
                    continue
                try:
                    relative_path = resolved.relative_to(root).as_posix()
                except ValueError:
                    continue
                items.append(_incomplete_item(
                    owner_id=owner_id,
                    run_id=run_id,
                    relative_path=relative_path,
                    path=resolved,
                ))
    return items


def _resolve_incomplete_relpath(
    data_root: str | Path,
    run_id: int,
    relative_path: str,
) -> Path:
    if (
        not relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ValueError("stored incomplete capture path is invalid")
    root = Path(data_root).resolve()
    run_root = (root / "runs" / str(run_id)).resolve()
    candidate = (root / Path(*relative_path.split("/"))).resolve()
    try:
        candidate.relative_to(run_root)
    except ValueError as error:
        raise ValueError("stored incomplete capture path escapes the Run root") from error
    return candidate


async def _cleanup_incomplete_capture_tombstone(
    tombstone: dict[str, object],
    data_root: str | Path,
) -> tuple[bool, int]:
    item_ref = str(tombstone["item_ref"])
    try:
        artifact = _resolve_incomplete_relpath(
            data_root,
            int(tombstone["run_id"]),
            str(tombstone["artifact_relpath"]),
        )
        reclaimed = 0
        try:
            metadata = artifact.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if artifact.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise OSError("incomplete capture artifact is not a regular file")
            observed = _file_fingerprint(artifact)
            if (
                observed["sha256"] != tombstone["expected_sha256"]
                or int(observed["size"]) != int(tombstone["expected_size"])
            ):
                raise OSError("incomplete capture fingerprint changed before cleanup")
            reclaimed = metadata.st_size
            artifact.unlink()
    except (OSError, ValueError):
        all_tombstones = file_store.read_json(_INCOMPLETE_TOMBSTONES) or []
        for t in all_tombstones:
            if t.get("item_ref") == item_ref:
                t["cleanup_state"] = "failed"
                t["cleanup_attempts"] = int(t.get("cleanup_attempts", 0)) + 1
                t["last_error_code"] = "artifact_cleanup_failed"
        file_store.write_json(_INCOMPLETE_TOMBSTONES, all_tombstones)
        return False, 0
    all_tombstones = file_store.read_json(_INCOMPLETE_TOMBSTONES) or []
    for t in all_tombstones:
        if t.get("item_ref") == item_ref:
            t["cleanup_state"] = "completed"
            t["cleanup_attempts"] = int(t.get("cleanup_attempts", 0)) + 1
            t["last_error_code"] = None
            t["reclaimed_bytes"] = reclaimed
    file_store.write_json(_INCOMPLETE_TOMBSTONES, all_tombstones)
    return True, reclaimed


async def remove_incomplete_capture_item(
    owner_id: str,
    item_ref: str,
    data_root: str | Path,
) -> dict[str, object] | None:
    all_tombstones = file_store.read_json(_INCOMPLETE_TOMBSTONES) or []
    existing = None
    for t in all_tombstones:
        if t.get("item_ref") == item_ref and t.get("owner_id") == owner_id:
            existing = t
            break
    if existing is not None:
        if existing.get("cleanup_state") == "completed":
            return {
                "schema_version": "incomplete_capture_removal.v1",
                "item_ref": item_ref,
                "removal_state": "already_unavailable",
                "reclaimed_bytes": 0,
                "impact": {
                    "code": "incomplete_recovery_only",
                    "message": "The incomplete recovery artifact is already unavailable.",
                },
            }
        completed, reclaimed = await _cleanup_incomplete_capture_tombstone(existing, data_root)
        return {
            "schema_version": "incomplete_capture_removal.v1",
            "item_ref": item_ref,
            "removal_state": "completed" if completed else "pending_cleanup",
            "reclaimed_bytes": reclaimed,
            "impact": {
                "code": "incomplete_recovery_only",
                "message": "Only the incomplete recovery artifact is affected.",
            },
        }
    current = {
        str(item["item_ref"]): item
        for item in await list_incomplete_capture_items(owner_id, data_root)
    }.get(item_ref)
    if current is None:
        return None
    run_ref = str(current["run_ref"])
    run_id = int(run_ref.split(":", 1)[1])
    all_tombstones.append({
        "item_ref": item_ref,
        "owner_id": owner_id,
        "run_id": run_id,
        "artifact_relpath": current["_relative_path"],
        "expected_sha256": current["_sha256"],
        "expected_size": current["size_bytes"],
    })
    file_store.write_json(_INCOMPLETE_TOMBSTONES, all_tombstones)
    # Re-read the tombstone we just wrote
    all_tombstones = file_store.read_json(_INCOMPLETE_TOMBSTONES) or []
    for t in all_tombstones:
        if t.get("item_ref") == item_ref:
            completed, reclaimed = await _cleanup_incomplete_capture_tombstone(t, data_root)
            return {
                "schema_version": "incomplete_capture_removal.v1",
                "item_ref": item_ref,
                "removal_state": "completed" if completed else "pending_cleanup",
                "reclaimed_bytes": reclaimed,
                "impact": {
                    "code": "incomplete_recovery_only",
                    "message": "Only the incomplete recovery artifact is affected.",
                },
            }
    return None


async def reconcile_run_videos(data_root: str | Path) -> dict[str, int]:
    await reconcile_run_evidence_deletions(data_root)
    runs_root = (Path(data_root) / "runs").resolve()
    quarantine_root = runs_root / "orphans"
    outcome = {"attached": 0, "retryable": 0, "unavailable": 0, "quarantined": 0}

    for run in _all_runs():
        if run.get("video_state") != "pending":
            continue
        pending_path = run.get("pending_video_path")
        request_digest = run.get("video_request_digest")
        if not pending_path or not isinstance(request_digest, str):
            await mark_run_video_unavailable(
                run["id"], run["user_id"], "video_pending_state_invalid",
            )
            outcome["unavailable"] += 1
            continue
        try:
            candidate, _request_id = _managed_run_video_path(
                data_root, run["id"], pending_path,
            )
        except ValueError:
            await mark_run_video_unavailable(
                run["id"], run["user_id"], "video_managed_path_invalid",
                expected_pending_video_path=pending_path,
                expected_request_digest=request_digest,
            )
            outcome["unavailable"] += 1
            continue
        receipt_path = _video_receipt_path(candidate)
        if not candidate.is_file() or not receipt_path.is_file():
            await _mark_run_video_waiting(
                run["id"], run["user_id"], pending_path, request_digest,
            )
            outcome["retryable"] += 1
            continue
        try:
            await attach_run_video(
                run["id"],
                run["user_id"],
                candidate,
                expected_pending_video_path=pending_path,
                expected_request_digest=request_digest,
                data_root=data_root,
            )
        except (OSError, ValueError):
            for artifact in (candidate, receipt_path):
                if _quarantine_run_video_file(artifact, quarantine_root):
                    outcome["quarantined"] += 1
            await mark_run_video_unavailable(
                run["id"], run["user_id"], "video_receipt_invalid",
                expected_pending_video_path=pending_path,
                expected_request_digest=request_digest,
            )
            outcome["unavailable"] += 1
        else:
            outcome["attached"] += 1

    if not runs_root.is_dir():
        return outcome
    referenced: set[Path] = set()
    for run in _all_runs():
        for value in (run.get("video_path"), run.get("pending_video_path")):
            if not value:
                continue
            video = Path(value).resolve()
            referenced.add(video)
            referenced.add(_video_receipt_path(video))
    tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
    for t in tombstones:
        if t.get("evidence_kind") != "video":
            continue
        try:
            video = _resolve_evidence_relpath(
                data_root, int(t["run_id"]), "video", t["artifact_relpath"],
            )
        except ValueError:
            continue
        referenced.update({video, _video_receipt_path(video)})
    for candidate in runs_root.rglob("*"):
        if not candidate.is_file() or not _is_app_video_artifact(candidate):
            continue
        try:
            candidate.relative_to(quarantine_root)
            continue
        except ValueError:
            pass
        if candidate.resolve() in referenced:
            continue
        if _quarantine_run_video_file(candidate, quarantine_root):
            outcome["quarantined"] += 1
    return outcome


async def begin_mouse_trace_attach(
    run_id: int, user_id: str, pending_trace_path: str | Path,
) -> Optional[dict]:
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    run["mouse_trace_path"] = None
    run["trace_state"] = "pending"
    run["pending_trace_path"] = str(pending_trace_path)
    run["trace_error"] = None
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def mark_mouse_trace_waiting(
    run_id: int,
    user_id: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if expected_pending_trace_path is not None:
        if run.get("trace_state") != "pending":
            return run
        if run.get("pending_trace_path") != str(expected_pending_trace_path):
            return run
    run["mouse_trace_path"] = None
    run["trace_state"] = "pending"
    run["pending_trace_path"] = None
    run["trace_error"] = "trace_waiting_snapshot"
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def mark_mouse_trace_unavailable(
    run_id: int,
    user_id: str,
    error: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if expected_pending_trace_path is not None:
        if run.get("trace_state") != "pending":
            return run
        if run.get("pending_trace_path") != str(expected_pending_trace_path):
            return run
    run["mouse_trace_path"] = None
    run["trace_state"] = "unavailable"
    run["pending_trace_path"] = None
    run["trace_error"] = error
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


async def attach_mouse_trace(
    run_id: int,
    user_id: str,
    trace_path: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    read_mouse_snapshot(trace_path)
    run = _load_run(run_id)
    if run is None or run.get("user_id") != user_id:
        return None
    if expected_pending_trace_path is not None:
        if run.get("trace_state") != "pending":
            return run
        if run.get("pending_trace_path") != str(expected_pending_trace_path):
            return run
    run["mouse_trace_path"] = trace_path
    run["trace_state"] = "attached"
    run["pending_trace_path"] = None
    run["trace_error"] = None
    run["updated_at"] = _utc_now()
    _save_run(run)
    return run


def _normalized_path(path: str | Path) -> Path:
    return Path(path).resolve()


async def reconcile_mouse_traces(data_root: str | Path) -> dict[str, int]:
    await reconcile_run_evidence_deletions(data_root)
    runs_root = Path(data_root) / "runs"
    outcome = {"attached": 0, "unavailable": 0, "quarantined": 0}

    for run in _all_runs():
        if run.get("trace_state") != "pending":
            continue
        trace_path = run.get("pending_trace_path")
        if not trace_path:
            continue
        expected_root = (runs_root / str(run["id"])).resolve()
        candidate = Path(trace_path).resolve()
        if not candidate.is_relative_to(expected_root):
            await mark_mouse_trace_unavailable(
                run["id"], run["user_id"], "trace_attach_failed",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
            continue
        if not candidate.is_file():
            await mark_mouse_trace_unavailable(
                run["id"], run["user_id"], "trace_attach_failed",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
            continue
        try:
            await attach_mouse_trace(
                run["id"], run["user_id"], str(candidate),
                expected_pending_trace_path=trace_path,
            )
        except (OSError, ValueError):
            await mark_mouse_trace_unavailable(
                run["id"], run["user_id"], "trace_quality_insufficient",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
        else:
            outcome["attached"] += 1

    if not runs_root.is_dir():
        return outcome

    referenced: set[Path] = set()
    for run in _all_runs():
        for value in (run.get("mouse_trace_path"), run.get("pending_trace_path")):
            if value:
                referenced.add(_normalized_path(value))
    tombstones = file_store.read_json(_EVIDENCE_TOMBSTONES) or []
    for t in tombstones:
        if t.get("evidence_kind") != "raw":
            continue
        try:
            trace = _resolve_evidence_relpath(
                data_root, int(t["run_id"]), "raw", t["artifact_relpath"],
            )
        except ValueError:
            continue
        referenced.add(_normalized_path(trace))
    quarantine_root = runs_root / "orphans"
    for candidate in runs_root.rglob("*.bin"):
        try:
            candidate.relative_to(quarantine_root)
            continue
        except ValueError:
            pass
        if _normalized_path(candidate) in referenced:
            continue
        quarantine_root.mkdir(parents=True, exist_ok=True)
        destination = quarantine_root / candidate.name
        if destination.exists():
            destination = quarantine_root / f"{candidate.stem}-{uuid4().hex}{candidate.suffix}"
        candidate.replace(destination)
        outcome["quarantined"] += 1
    return outcome
