"""SQLite-backed local KovaaK run records.

The run record is deliberately separate from an Analysis Session. A run can
exist without video or a CV job and can later be referenced by one or more
analysis sessions.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import struct
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from kovaak_tracker.csv_parser import parse_stats_csv
from kovaak_tracker.performance_parser import parse_performance_file

from .db import get_conn
from .kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
    RetryableIngestionError,
    normalize_kovaak_stem,
)


SNAPSHOT_MAGIC = b"ACRI"
SNAPSHOT_VERSION = 1
SNAPSHOT_HEADER_SIZE = 12
SNAPSHOT_RECORD_SIZE = 20
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_POINTS = 1_000_000
MAX_SNAPSHOT_SPAN_MS = 10 * 60 * 1000
SUPPORTED_BUTTON_MASK = 0b111
STATS_PARSER_VERSION = "kovaak_stats.v1"
PERFORMANCE_PARSER_VERSION = "kovaak_performance.v1"
SCENARIO_IDENTITY_VERSION = "kovaak_scenario.v1"
_SHA256_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")


class PairingConflictError(NonRetryableIngestionError):
    """Raised when sources with one stable key disagree on scenario identity."""


class SourceUnstableError(RetryableIngestionError):
    """Raised when a source revision changes while it is being parsed."""


class TracePendingError(RetryableIngestionError):
    """Raised while waiting for the post-run Raw Input snapshot flush."""


def _normalize_scenario_identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _assert_same_scenario_identity(*values: str | None) -> None:
    identities = {
        normalized
        for value in values
        if (normalized := _normalize_scenario_identity(value))
    }
    if len(identities) > 1:
        raise PairingConflictError("pairing_conflict: scenario identity mismatch")


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
        raise SourceUnstableError("source_unstable: source changed while fingerprinting")
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
        raise PairingConflictError(f"pairing_conflict: second {kind} source")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _trace_pairing_within_retention(performance: object) -> bool:
    header = performance.header
    end_ms = header.challenge_start_utc + int(header.challenge_profile.time_limit * 1000)
    return _now_ms() <= end_ms + MAX_SNAPSHOT_SPAN_MS


def _validate_snapshot_points(points: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(points) > MAX_SNAPSHOT_POINTS:
        raise ValueError("raw input snapshot has too many points")

    normalized: list[dict[str, int]] = []
    first_timestamp: int | None = None
    previous_timestamp: int | None = None
    for point in points:
        timestamp_ms = int(point["timestamp_ms"])
        dx = int(point["dx"])
        dy = int(point["dy"])
        buttons = int(point.get("buttons", 0))
        if not -(2**63) <= timestamp_ms < 2**63:
            raise ValueError("raw input timestamp is outside i64 range")
        if not -(2**31) <= dx < 2**31 or not -(2**31) <= dy < 2**31:
            raise ValueError("raw input delta is outside i32 range")
        if buttons < 0 or buttons & ~SUPPORTED_BUTTON_MASK:
            raise ValueError("raw input buttons use unsupported bits")
        if previous_timestamp is not None and timestamp_ms < previous_timestamp:
            raise ValueError("raw input timestamps are not monotonic")
        if first_timestamp is None:
            first_timestamp = timestamp_ms
        elif timestamp_ms - first_timestamp > MAX_SNAPSHOT_SPAN_MS:
            raise ValueError("raw input snapshot exceeds time span limit")
        previous_timestamp = timestamp_ms
        normalized.append({
            "timestamp_ms": timestamp_ms,
            "dx": dx,
            "dy": dy,
            "buttons": buttons,
        })
    return normalized


def decode_mouse_snapshot_bytes(data: bytes) -> list[dict[str, int]]:
    """Decode exact Raw Input bytes after their source fingerprint is accepted."""
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("raw input snapshot exceeds byte limit")
    if len(data) < SNAPSHOT_HEADER_SIZE or data[:4] != SNAPSHOT_MAGIC:
        raise ValueError("invalid raw input snapshot")
    if data[4] != SNAPSHOT_VERSION:
        raise ValueError("unsupported raw input snapshot version")
    if data[5:8] != b"\0\0\0":
        raise ValueError("unsupported raw input snapshot header")
    count = struct.unpack_from("<I", data, 8)[0]
    if count > MAX_SNAPSHOT_POINTS:
        raise ValueError("raw input snapshot has too many points")
    expected = SNAPSHOT_HEADER_SIZE + count * SNAPSHOT_RECORD_SIZE
    if len(data) != expected:
        raise ValueError("truncated raw input snapshot")
    points = []
    offset = SNAPSHOT_HEADER_SIZE
    for _ in range(count):
        timestamp_ms, dx, dy, buttons = struct.unpack_from("<qiiI", data, offset)
        points.append({"timestamp_ms": timestamp_ms, "dx": dx, "dy": dy, "buttons": buttons})
        offset += SNAPSHOT_RECORD_SIZE
    return _validate_snapshot_points(points)


def read_mouse_snapshot(path: str | Path) -> list[dict[str, int]]:
    """Read and validate the versioned Rust Raw Input snapshot format."""
    source = Path(path)
    with source.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    return decode_mouse_snapshot_bytes(data)


def _has_valid_mouse_snapshot_header(path: str | Path) -> bool:
    """Check a snapshot's bounded structural header without decoding its points."""
    source = Path(path)
    size = source.stat().st_size
    if size < SNAPSHOT_HEADER_SIZE or size > MAX_SNAPSHOT_BYTES:
        return False
    with source.open("rb") as stream:
        header = stream.read(SNAPSHOT_HEADER_SIZE)
    if len(header) != SNAPSHOT_HEADER_SIZE or header[:4] != SNAPSHOT_MAGIC:
        return False
    if header[4] != SNAPSHOT_VERSION or header[5:8] != b"\0\0\0":
        return False
    count = struct.unpack_from("<I", header, 8)[0]
    return (
        count <= MAX_SNAPSHOT_POINTS
        and size == SNAPSHOT_HEADER_SIZE + count * SNAPSHOT_RECORD_SIZE
    )


def write_mouse_snapshot(path: str | Path, points: list[dict[str, int]]) -> None:
    """Write a validated trace snapshot atomically in the Rust-compatible format."""
    normalized = _validate_snapshot_points(points)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray(SNAPSHOT_MAGIC + bytes([SNAPSHOT_VERSION, 0, 0, 0]))
    payload.extend(struct.pack("<I", len(normalized)))
    for point in normalized:
        payload.extend(struct.pack(
            "<qiiI", point["timestamp_ms"], point["dx"], point["dy"], point["buttons"],
        ))
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def extract_mouse_snapshot_window(
    path: str | Path, start_ms: int, end_ms: int, destination: str | Path,
) -> int:
    if end_ms < start_ms:
        raise ValueError("raw input window end precedes start")
    points = [
        point for point in read_mouse_snapshot(path)
        if start_ms <= point["timestamp_ms"] <= end_ms
    ]
    if points:
        write_mouse_snapshot(destination, points)
    return len(points)


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
    result = dict(row)
    result["stats_summary"] = _decode(result.get("stats_summary"))
    result["performance_summary"] = _decode(result.get("performance_summary"))
    return result


def _source_ref(run_id: int, kind: str, summary: object | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    source = summary.get("source")
    if not isinstance(source, dict):
        return None
    digest = source.get("sha256")
    if not isinstance(digest, str) or not _SHA256_DIGEST.fullmatch(digest):
        return None
    return f"run:{run_id}:{kind}:{digest[:16].lower()}"


def _public_summary(summary: object | None) -> dict | None:
    if not isinstance(summary, dict):
        return None
    public = {
        key: value
        for key, value in summary.items()
        if key != "source"
    }
    sanitized = _sanitize_public_value(public)
    return sanitized if isinstance(sanitized, dict) else None


_DROP_PUBLIC_VALUE = object()


def _path_like_key(key: object) -> bool:
    compact = "".join(character for character in str(key).lower() if character.isalnum())
    return compact == "path" or compact.endswith("path") or compact.endswith("paths")


def _is_absolute_path_or_file_uri(value: str) -> bool:
    candidate = value.strip()
    return bool(
        candidate
        and (
            candidate.lower().startswith("file:")
            or os.path.isabs(candidate)
            or candidate.startswith(("\\", "/"))
            or re.match(r"^[A-Za-z]:[\\/]", candidate)
        )
    )


def _sanitize_public_value(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[object, object] = {}
        for key, child in value.items():
            if _path_like_key(key) or (
                isinstance(key, str) and _is_absolute_path_or_file_uri(key)
            ):
                continue
            public_child = _sanitize_public_value(child)
            if public_child is not _DROP_PUBLIC_VALUE:
                sanitized[key] = public_child
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_items = [_sanitize_public_value(child) for child in value]
        return [child for child in sanitized_items if child is not _DROP_PUBLIC_VALUE]
    if isinstance(value, str) and _is_absolute_path_or_file_uri(value):
        return _DROP_PUBLIC_VALUE
    return value


def _public_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return None if _is_absolute_path_or_file_uri(value) else value


def _source_revision_availability(
    path: object,
    *,
    sha256: object = None,
    size: object = None,
    mtime_ns: object = None,
    parser_version: object = None,
) -> str:
    if not isinstance(path, str) or not path:
        return "not_present"
    candidate = Path(path)
    if not candidate.is_file():
        return "missing"
    if (
        not isinstance(sha256, str)
        or not _SHA256_DIGEST.fullmatch(sha256)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or isinstance(mtime_ns, bool)
        or not isinstance(mtime_ns, int)
    ):
        return "invalid"
    try:
        observed = _source_metadata(
            candidate,
            str(parser_version or "source.v1"),
        )
    except (OSError, SourceUnstableError):
        return "unavailable"
    return "available" if (
        observed.get("sha256") == sha256.lower()
        and observed.get("size") == size
        and observed.get("mtime_ns") == mtime_ns
    ) else "invalid"


def _source_stat_availability(
    path: object,
    *,
    size: object = None,
    mtime_ns: object = None,
) -> str:
    if not isinstance(path, str) or not path:
        return "not_present"
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    if not candidate.is_file():
        return "missing"
    if size is None and mtime_ns is None:
        return "available"
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or isinstance(mtime_ns, bool)
        or not isinstance(mtime_ns, int)
    ):
        return "invalid"
    return "available" if (
        stat.st_size == size and stat.st_mtime_ns == mtime_ns
    ) else "invalid"


def _summary_source(summary: object | None) -> dict:
    if not isinstance(summary, dict):
        return {}
    source = summary.get("source")
    return source if isinstance(source, dict) else {}


def _trace_quality(
    trace_state: object, trace_path: object, *, shallow: bool = False,
) -> dict[str, object]:
    state = trace_state if isinstance(trace_state, str) and trace_state else "none"
    if state == "none" and not trace_path:
        availability = "not_present"
    elif state == "attached" and isinstance(trace_path, str):
        try:
            if shallow:
                valid = _has_valid_mouse_snapshot_header(trace_path)
            else:
                read_mouse_snapshot(trace_path)
                valid = True
        except (OSError, ValueError):
            availability = "unavailable"
        else:
            availability = "available" if valid else "unavailable"
    else:
        availability = "unavailable"
    return {
        "state": state,
        "availability": availability,
        "alignment_status": None,
        "coverage": None,
    }


def public_kovaak_run(run: dict) -> dict:
    """Project a DB-private run row into a path-free public DTO."""
    stats_path = run.get("stats_path")
    performance_path = run.get("performance_path")
    trace_path = run.get("mouse_trace_path")
    stats_source = _summary_source(run.get("stats_summary"))
    performance_source = _summary_source(run.get("performance_summary"))
    return {
        "id": run["id"],
        "run_ref": f"run:{run['id']}",
        "source_key": _public_string(run.get("source_key")),
        "scenario": _public_string(run.get("scenario")),
        "stats_source_ref": _source_ref(run["id"], "stats", run.get("stats_summary")),
        "performance_source_ref": _source_ref(
            run["id"], "performance", run.get("performance_summary"),
        ),
        "trace_artifact_ref": f"run:{run['id']}:trace" if trace_path else None,
        "source_availability": {
            "stats": _source_revision_availability(
                stats_path,
                sha256=stats_source.get("sha256"),
                size=stats_source.get("size"),
                mtime_ns=stats_source.get("mtime_ns"),
                parser_version=stats_source.get("parser_version"),
            ),
            "performance": _source_revision_availability(
                performance_path,
                sha256=performance_source.get("sha256"),
                size=performance_source.get("size"),
                mtime_ns=performance_source.get("mtime_ns"),
                parser_version=performance_source.get("parser_version"),
            ),
        },
        "trace_quality": _trace_quality(run.get("trace_state"), trace_path),
        "trace_state": run.get("trace_state", "none"),
        "trace_error": _public_string(run.get("trace_error")),
        "stats_summary": _public_summary(run.get("stats_summary")),
        "performance_summary": _public_summary(run.get("performance_summary")),
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
    }


async def list_kovaak_run_summaries(user_id: str, limit: int = 100) -> list[dict]:
    """Return public scalar Run summaries without loading parser summary blobs."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, trace_error, created_at, updated_at, "
        "CASE WHEN json_valid(stats_summary) THEN "
        "json_extract(stats_summary, '$.source.size') END AS stats_size, "
        "CASE WHEN json_valid(stats_summary) THEN "
        "json_extract(stats_summary, '$.source.mtime_ns') END AS stats_mtime_ns, "
        "CASE WHEN json_valid(performance_summary) THEN "
        "json_extract(performance_summary, '$.source.size') END AS performance_size, "
        "CASE WHEN json_valid(performance_summary) THEN "
        "json_extract(performance_summary, '$.source.mtime_ns') END AS performance_mtime_ns "
        "FROM kovaak_runs WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    out: list[dict] = []
    for row in await cur.fetchall():
        item = dict(row)
        out.append({
            "id": int(item["id"]),
            "run_ref": f"run:{item['id']}",
            "source_key": _public_string(item.get("source_key")),
            "scenario": _public_string(item.get("scenario")),
            "source_availability": {
                "stats": _source_stat_availability(
                    item.get("stats_path"),
                    size=item.get("stats_size"),
                    mtime_ns=item.get("stats_mtime_ns"),
                ),
                "performance": _source_stat_availability(
                    item.get("performance_path"),
                    size=item.get("performance_size"),
                    mtime_ns=item.get("performance_mtime_ns"),
                ),
            },
            "trace_quality": _trace_quality(
                item.get("trace_state"), item.get("mouse_trace_path"), shallow=True,
            ),
            "trace_state": item.get("trace_state") or "none",
            "trace_error": _public_string(item.get("trace_error")),
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        })
    return out


async def build_analysis_input_snapshot(run_id: int, user_id: str) -> dict:
    """Freeze the currently observed Run inputs for one Analysis request.

    Paths stay in this DB-private snapshot for the local worker only; result and
    API projections use the stable refs and never serialize them.
    """
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
        }
    trace: dict[str, object] | None = None
    trace_path = run.get("mouse_trace_path")
    if run.get("trace_state") == "attached" and trace_path and Path(trace_path).is_file():
        read_mouse_snapshot(trace_path)
        trace_revision = _source_metadata(
            trace_path, f"raw_input_snapshot.v{SNAPSHOT_VERSION}",
        )
        trace = {
            "artifact_ref": f"run:{run_id}:trace",
            "path": str(Path(trace_path).resolve()),
            "availability": "available",
            "format_version": SNAPSHOT_VERSION,
            "fingerprint": {
                "sha256": trace_revision["sha256"],
                "size": trace_revision["size"],
                "mtime_ns": trace_revision["mtime_ns"],
            },
        }
    return {
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": run_id,
        "scenario": run.get("scenario"),
        "scenario_identity_version": SCENARIO_IDENTITY_VERSION,
        "sources": sources,
        "trace": trace,
    }


def public_analysis_input_snapshot(snapshot: dict) -> dict:
    """Remove DB-private paths before an input snapshot crosses a result boundary."""
    sources: dict[object, dict] = {}
    for kind, source in (snapshot.get("sources") or {}).items():
        sanitized = _sanitize_public_value(source)
        if isinstance(sanitized, dict):
            sources[kind] = sanitized
    trace = snapshot.get("trace")
    sanitized_trace = _sanitize_public_value(trace)
    public_trace = sanitized_trace if isinstance(sanitized_trace, dict) else None
    return {
        "schema_version": snapshot.get("schema_version", "analysis_input_snapshot.v1"),
        "run_id": snapshot.get("run_id"),
        "scenario": _public_string(snapshot.get("scenario")),
        "scenario_identity_version": snapshot.get("scenario_identity_version"),
        "sources": sources,
        "trace": public_trace,
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
    conn = await get_conn()
    await conn.execute(
        """INSERT INTO kovaak_runs(
            user_id, source_key, scenario, stats_path, performance_path,
            mouse_trace_path, trace_state, pending_trace_path, trace_error,
            stats_summary, performance_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, source_key) DO UPDATE SET
            scenario=COALESCE(excluded.scenario, kovaak_runs.scenario),
            stats_path=COALESCE(excluded.stats_path, kovaak_runs.stats_path),
            performance_path=COALESCE(excluded.performance_path, kovaak_runs.performance_path),
            mouse_trace_path=COALESCE(excluded.mouse_trace_path, kovaak_runs.mouse_trace_path),
            trace_state=CASE
                WHEN excluded.mouse_trace_path IS NOT NULL THEN 'attached'
                ELSE kovaak_runs.trace_state
            END,
            pending_trace_path=CASE
                WHEN excluded.mouse_trace_path IS NOT NULL THEN NULL
                ELSE kovaak_runs.pending_trace_path
            END,
            trace_error=CASE
                WHEN excluded.mouse_trace_path IS NOT NULL THEN NULL
                ELSE kovaak_runs.trace_error
            END,
            stats_summary=COALESCE(excluded.stats_summary, kovaak_runs.stats_summary),
            performance_summary=COALESCE(excluded.performance_summary, kovaak_runs.performance_summary),
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            user_id,
            source_key,
            scenario,
            stats_path,
            performance_path,
            mouse_trace_path,
            "attached" if mouse_trace_path is not None else "none",
            None,
            None,
            _json(stats_summary),
            _json(performance_summary),
        ),
    )
    await conn.commit()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "stats_summary, performance_summary, created_at, updated_at "
        "FROM kovaak_runs WHERE user_id=? AND source_key=?",
        (user_id, source_key),
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("kovaak run disappeared after upsert")
    return _row(row)


async def ingest_discovery(
    discovery: KovaaKFileDiscovery,
    *,
    user_id: str = "desktop-local",
    trace_path: Optional[str] = None,
    raw_input_snapshot_path: str | Path | None = None,
) -> dict:
    """Parse available source files and idempotently persist one run."""
    stats_summary: object | None = None
    performance_summary: object | None = None
    stats_source: dict[str, object] | None = None
    performance_source: dict[str, object] | None = None
    performance = None
    stats_scenario: Optional[str] = None
    performance_scenario: Optional[str] = None
    if discovery.stats_path is not None:
        stats_revision = _stat_revision(discovery.stats_path)
        stats = parse_stats_csv(discovery.stats_path)
        stats_source = _source_metadata(discovery.stats_path, STATS_PARSER_VERSION)
        if stats_revision != (stats_source["size"], stats_source["mtime_ns"]):
            raise SourceUnstableError("source_unstable: Stats changed while parsing")
        stats_scenario = stats.scenario
        stats_summary = {
            "file_name": stats.file_name,
            "summary": stats.summary,
            "config": stats.config,
            "kill_count": int(len(stats.kills.index)),
            "source": stats_source,
        }
    if discovery.performance_path is not None:
        performance_revision = _stat_revision(discovery.performance_path)
        performance = parse_performance_file(discovery.performance_path)
        performance_source = _source_metadata(
            discovery.performance_path, PERFORMANCE_PARSER_VERSION,
        )
        if performance_revision != (
            performance_source["size"], performance_source["mtime_ns"],
        ):
            raise SourceUnstableError("source_unstable: Performance changed while parsing")
        performance_scenario = performance.header.scenario_name or None
        performance_summary = {
            "header": asdict(performance.header),
            "event_count": len(performance.events),
            "source": performance_source,
        }
    source_key = discovery.stem or normalize_kovaak_stem(discovery.paths[0])
    existing = await _get_kovaak_run_by_source_key(user_id, source_key)
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
    has_trace_window = bool(
        performance is not None
        and performance.header.challenge_start_utc > 0
        and performance.header.challenge_profile.time_limit > 0
    )
    if (
        trace_path is None
        and has_trace_window
        and existing
        and existing.get("trace_state") == "attached"
        and performance_revision_unchanged
    ):
        return run
    if trace_path is None and has_trace_window:
        from . import config

        target = (
            config.DATA_ROOT / "runs" / str(run["id"])
            / f"trace-{uuid4().hex}.bin"
        )
        if not raw_input_snapshot_path or not Path(raw_input_snapshot_path).is_file():
            if _trace_pairing_within_retention(performance):
                await mark_mouse_trace_waiting(run["id"], user_id)
                raise TracePendingError("trace_pending: waiting for Raw Input snapshot")
            return await mark_mouse_trace_unavailable(
                run["id"], user_id, "trace_capture_unavailable",
            ) or run
        run = await begin_mouse_trace_attach(run["id"], user_id, target) or run
        try:
            count = extract_mouse_snapshot_window(
                raw_input_snapshot_path,
                performance.header.challenge_start_utc,
                performance.header.challenge_start_utc
                + int(performance.header.challenge_profile.time_limit * 1000),
                target,
            )
        except (OSError, ValueError) as error:
            if _trace_pairing_within_retention(performance):
                await mark_mouse_trace_waiting(
                    run["id"],
                    user_id,
                    expected_pending_trace_path=target,
                )
                raise TracePendingError(
                    "trace_pending: Raw Input snapshot is not ready",
                ) from error
            return await mark_mouse_trace_unavailable(
                run["id"],
                user_id,
                "trace_snapshot_failed",
                expected_pending_trace_path=target,
            ) or run
        if not count:
            if _trace_pairing_within_retention(performance):
                await mark_mouse_trace_waiting(
                    run["id"],
                    user_id,
                    expected_pending_trace_path=target,
                )
                raise TracePendingError("trace_pending: trace window is not flushed yet")
            return await mark_mouse_trace_unavailable(
                run["id"],
                user_id,
                "trace_quality_insufficient",
                expected_pending_trace_path=target,
            ) or run
        try:
            run = await attach_mouse_trace(
                run["id"],
                user_id,
                str(target),
                expected_pending_trace_path=target,
            ) or run
        except (OSError, ValueError):
            return await mark_mouse_trace_unavailable(
                run["id"],
                user_id,
                "trace_attach_failed",
                expected_pending_trace_path=target,
            ) or run
    return run


async def _get_kovaak_run_by_source_key(user_id: str, source_key: str) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "stats_summary, performance_summary, created_at, updated_at "
        "FROM kovaak_runs WHERE user_id=? AND source_key=?",
        (user_id, source_key),
    )
    row = await cur.fetchone()
    return _row(row) if row else None


async def list_kovaak_runs(user_id: str, limit: int = 100) -> list[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "stats_summary, performance_summary, created_at, updated_at "
        "FROM kovaak_runs WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    return [_row(row) for row in await cur.fetchall()]


async def get_kovaak_run(run_id: int, user_id: str) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "stats_summary, performance_summary, created_at, updated_at "
        "FROM kovaak_runs WHERE id=? AND user_id=?",
        (run_id, user_id),
    )
    row = await cur.fetchone()
    return _row(row) if row else None


async def get_kovaak_run_any_owner(run_id: int) -> Optional[dict]:
    """Internal owner check for product commands; never expose this row directly."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "stats_summary, performance_summary, created_at, updated_at "
        "FROM kovaak_runs WHERE id=?",
        (run_id,),
    )
    row = await cur.fetchone()
    return _row(row) if row else None


async def begin_mouse_trace_attach(
    run_id: int, user_id: str, pending_trace_path: str | Path,
) -> Optional[dict]:
    """Clear any old attachment before a new managed trace write begins."""
    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET mouse_trace_path=NULL, trace_state='pending', "
        "pending_trace_path=?, trace_error=NULL, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND user_id=?",
        (str(pending_trace_path), run_id, user_id),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def mark_mouse_trace_waiting(
    run_id: int,
    user_id: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    conn = await get_conn()
    where = "WHERE id=? AND user_id=?"
    params: list[object] = [run_id, user_id]
    if expected_pending_trace_path is not None:
        where += " AND trace_state='pending' AND pending_trace_path=?"
        params.append(str(expected_pending_trace_path))
    await conn.execute(
        "UPDATE kovaak_runs SET mouse_trace_path=NULL, trace_state='pending', "
        "pending_trace_path=NULL, trace_error='trace_waiting_snapshot', "
        f"updated_at=CURRENT_TIMESTAMP {where}",
        tuple(params),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def mark_mouse_trace_unavailable(
    run_id: int,
    user_id: str,
    error: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    conn = await get_conn()
    where = "WHERE id=? AND user_id=?"
    params: list[object] = [error, run_id, user_id]
    if expected_pending_trace_path is not None:
        where += " AND trace_state='pending' AND pending_trace_path=?"
        params.append(str(expected_pending_trace_path))
    await conn.execute(
        "UPDATE kovaak_runs SET mouse_trace_path=NULL, trace_state='unavailable', "
        "pending_trace_path=NULL, trace_error=?, updated_at=CURRENT_TIMESTAMP "
        f"{where}",
        tuple(params),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def attach_mouse_trace(
    run_id: int,
    user_id: str,
    trace_path: str,
    *,
    expected_pending_trace_path: str | Path | None = None,
) -> Optional[dict]:
    read_mouse_snapshot(trace_path)
    conn = await get_conn()
    where = "WHERE id=? AND user_id=?"
    params: list[object] = [trace_path, run_id, user_id]
    if expected_pending_trace_path is not None:
        where += " AND trace_state='pending' AND pending_trace_path=?"
        params.append(str(expected_pending_trace_path))
    await conn.execute(
        "UPDATE kovaak_runs SET mouse_trace_path=?, trace_state='attached', "
        "pending_trace_path=NULL, trace_error=NULL, updated_at=CURRENT_TIMESTAMP "
        f"{where}",
        tuple(params),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


def _normalized_path(path: str | Path) -> Path:
    return Path(path).resolve()


async def reconcile_mouse_traces(data_root: str | Path) -> dict[str, int]:
    """Recover pending managed artifacts and quarantine unreferenced trace files."""
    runs_root = Path(data_root) / "runs"
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, pending_trace_path FROM kovaak_runs "
        "WHERE trace_state='pending'"
    )
    pending = [dict(row) for row in await cur.fetchall()]
    outcome = {"attached": 0, "unavailable": 0, "quarantined": 0}

    for row in pending:
        trace_path = row["pending_trace_path"]
        if not trace_path:
            continue
        expected_root = (runs_root / str(row["id"])).resolve()
        candidate = Path(trace_path).resolve()
        if not candidate.is_relative_to(expected_root):
            await mark_mouse_trace_unavailable(
                row["id"],
                row["user_id"],
                "trace_attach_failed",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
            continue
        if not candidate.is_file():
            await mark_mouse_trace_unavailable(
                row["id"],
                row["user_id"],
                "trace_attach_failed",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
            continue
        try:
            await attach_mouse_trace(
                row["id"],
                row["user_id"],
                str(candidate),
                expected_pending_trace_path=trace_path,
            )
        except (OSError, ValueError):
            await mark_mouse_trace_unavailable(
                row["id"],
                row["user_id"],
                "trace_quality_insufficient",
                expected_pending_trace_path=trace_path,
            )
            outcome["unavailable"] += 1
        else:
            outcome["attached"] += 1

    if not runs_root.is_dir():
        return outcome

    cur = await conn.execute(
        "SELECT mouse_trace_path, pending_trace_path FROM kovaak_runs "
        "WHERE mouse_trace_path IS NOT NULL OR pending_trace_path IS NOT NULL"
    )
    referenced = {
        _normalized_path(value)
        for row in await cur.fetchall()
        for value in (row["mouse_trace_path"], row["pending_trace_path"])
        if value
    }
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
