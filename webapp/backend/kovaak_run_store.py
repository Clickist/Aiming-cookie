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
import stat
import struct
import time
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from kovaak_tracker.csv_parser import parse_stats_csv
from kovaak_tracker.performance_parser import parse_performance_file
from kovaak_tracker.time_alignment import TimeAlignmentError, resolve_time_window

from .db import get_conn
from .kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
    RetryableIngestionError,
    normalize_kovaak_stem,
)
from .workspace import session_dir
from .source_requirements import validate_source_requirements


SNAPSHOT_MAGIC = b"ACRI"
SNAPSHOT_VERSION = 2
LEGACY_SNAPSHOT_VERSION = 1
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({LEGACY_SNAPSHOT_VERSION, SNAPSHOT_VERSION})
SNAPSHOT_HEADER_SIZE = 12
SNAPSHOT_RECORD_SIZE = 20
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_POINTS = 1_000_000
MAX_SNAPSHOT_SPAN_MS = 10 * 60 * 1000
SUPPORTED_BUTTON_MASK = 0b111
STATS_PARSER_VERSION = "kovaak_stats.v2"
PERFORMANCE_PARSER_VERSION = "kovaak_performance.v2"
SCENARIO_IDENTITY_VERSION = "kovaak_scenario.v1"
ANALYSIS_INPUT_SNAPSHOT_VERSION = "analysis_input_snapshot.v3"
CANONICAL_TIME_WINDOW_VERSION = "canonical_time_window.v1"
_SHA256_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")
_STRICT_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_SCENARIO_DEFINITION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,159}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_CAPTURE_WINDOW_MS = 300_000


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
    revision_fields = ("sha256", "size", "mtime_ns", "parser_version")
    if any(
        existing_source.get(field) != observed_source.get(field)
        for field in revision_fields
    ):
        raise PairingConflictError(f"pairing_conflict: changed {kind} source revision")


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


def _decode_mouse_snapshot_bytes_with_version(
    data: bytes,
) -> tuple[int, list[dict[str, int]]]:
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("raw input snapshot exceeds byte limit")
    if len(data) < SNAPSHOT_HEADER_SIZE or data[:4] != SNAPSHOT_MAGIC:
        raise ValueError("invalid raw input snapshot")
    version = data[4]
    if version not in SUPPORTED_SNAPSHOT_VERSIONS:
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
    return version, _validate_snapshot_points(points)


def decode_mouse_snapshot_bytes(data: bytes) -> list[dict[str, int]]:
    """Decode exact Raw Input bytes after their source fingerprint is accepted."""
    return _decode_mouse_snapshot_bytes_with_version(data)[1]


def _canonicalize_mouse_points(points: list[dict[str, int]]) -> list[dict[str, int]]:
    source = _validate_snapshot_points(points)
    normalized: list[dict[str, int]] = []
    bucket: dict[str, int] | None = None
    buttons = 0

    def flush() -> None:
        nonlocal bucket
        if bucket is not None and (bucket["dx"] != 0 or bucket["dy"] != 0):
            normalized.append(bucket)
        bucket = None

    for point in source:
        timestamp_ms = point["timestamp_ms"]
        if bucket is not None and timestamp_ms > bucket["timestamp_ms"]:
            flush()
        if point["dx"] != 0 or point["dy"] != 0:
            if bucket is None:
                bucket = {
                    "timestamp_ms": timestamp_ms,
                    "dx": point["dx"],
                    "dy": point["dy"],
                    "buttons": buttons,
                }
            else:
                next_dx = bucket["dx"] + point["dx"]
                next_dy = bucket["dy"] + point["dy"]
                if not -(2**31) <= next_dx < 2**31 or not -(2**31) <= next_dy < 2**31:
                    raise ValueError("raw input aggregate is outside i32 range")
                bucket["dx"] = next_dx
                bucket["dy"] = next_dy

        changed = buttons ^ point["buttons"]
        for mask in (1, 2, 4):
            if not changed & mask:
                continue
            if point["buttons"] & mask:
                buttons |= mask
            else:
                buttons &= ~mask
            if bucket is not None:
                bucket["buttons"] = buttons
            normalized.append({
                "timestamp_ms": timestamp_ms,
                "dx": 0,
                "dy": 0,
                "buttons": buttons,
            })
    flush()
    return _validate_snapshot_points(normalized)


def read_mouse_snapshot(path: str | Path) -> list[dict[str, int]]:
    """Read and validate the versioned Rust Raw Input snapshot format."""
    source = Path(path)
    with source.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    return decode_mouse_snapshot_bytes(data)


def read_mouse_snapshot_with_version(
    path: str | Path,
) -> tuple[int, list[dict[str, int]]]:
    """Read a Raw Input snapshot while retaining its actual on-disk version."""
    source = Path(path)
    with source.open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    return _decode_mouse_snapshot_bytes_with_version(data)


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
    if header[4] not in SUPPORTED_SNAPSHOT_VERSIONS or header[5:8] != b"\0\0\0":
        return False
    count = struct.unpack_from("<I", header, 8)[0]
    return (
        count <= MAX_SNAPSHOT_POINTS
        and size == SNAPSHOT_HEADER_SIZE + count * SNAPSHOT_RECORD_SIZE
    )


def write_mouse_snapshot(path: str | Path, points: list[dict[str, int]]) -> None:
    """Write a validated trace snapshot atomically in the Rust-compatible format."""
    normalized = _canonicalize_mouse_points(points)
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
        if start_ms <= point["timestamp_ms"] < end_ms
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
    result["alignment_summary"] = _decode(result.get("alignment_summary"))
    result["video_receipt"] = _decode(result.pop("video_receipt_json", None))
    result["video_summary"] = _decode(result.pop("video_summary_json", None))
    return result


def _sqlite_timestamp_to_wire_utc(value: object) -> object:
    """Mark SQLite CURRENT_TIMESTAMP values as UTC for browser parsing."""
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


def _current_video_evidence(
    run: dict, *, shallow: bool = False,
) -> tuple[str, dict[str, object] | None]:
    """Validate the attached MP4 against the immutable DB fingerprint."""
    if run.get("video_state") != "attached" or not run.get("video_path"):
        return ("not_present", None)
    path = Path(str(run["video_path"]))
    if not path.is_file():
        return ("missing", None)
    summary = run.get("video_summary")
    fingerprint = summary.get("fingerprint") if isinstance(summary, dict) else None
    if (
        not isinstance(fingerprint, dict)
        or not isinstance(fingerprint.get("sha256"), str)
        or _SHA256_DIGEST.fullmatch(fingerprint["sha256"]) is None
        or isinstance(fingerprint.get("size"), bool)
        or not isinstance(fingerprint.get("size"), int)
        or fingerprint["size"] < 0
    ):
        return ("invalid", None)
    if shallow:
        try:
            observed_size = path.stat().st_size
        except OSError:
            return ("unavailable", None)
        if observed_size != fingerprint["size"]:
            return ("invalid", None)
        observed = {"sha256": fingerprint["sha256"].lower(), "size": observed_size}
    else:
        try:
            observed = _file_fingerprint(path)
        except OSError:
            return ("unavailable", None)
    expected = {
        "sha256": fingerprint["sha256"].lower(),
        "size": fingerprint["size"],
    }
    if observed != expected:
        return ("invalid", None)
    return (
        "available",
        {
            "artifact_ref": (
                f"run:{run['id']}:video:{expected['sha256'][:16]}"
            ),
            "basename": path.name,
            "fingerprint": expected,
            "path": str(path.resolve()),
            "availability": "available",
            "format_version": "mp4",
            "ownership": "run",
        },
    )


def _public_alignment(run: dict) -> dict[str, object]:
    summary = run.get("alignment_summary")
    allowed = {
        "duration_ms",
        "start_source",
        "end_source",
        "timebase_version",
        "warnings",
        "method",
        "anchor",
        "coverage",
        "error_code",
    }
    public_summary = {
        key: value
        for key, value in summary.items()
        if key in allowed
    } if isinstance(summary, dict) else {}
    return {
        "state": run.get("alignment_state") or "unresolved",
        **public_summary,
    }


def _video_quality(run: dict, availability: str) -> dict[str, object]:
    summary = run.get("video_summary")
    if availability != "available" or not isinstance(summary, dict):
        return {"availability": availability, "coverage": None}
    packet_count = summary.get("packetCount")
    visible_duration = summary.get("visibleDuration100ns")
    coverage = None
    if (
        isinstance(packet_count, int)
        and not isinstance(packet_count, bool)
        and packet_count >= 0
        and isinstance(visible_duration, int)
        and not isinstance(visible_duration, bool)
        and visible_duration >= 0
    ):
        coverage = {
            "packet_count": packet_count,
            "visible_duration_ms": visible_duration / 10_000,
        }
    return {"availability": "available", "coverage": coverage}


def _run_evidence_view(run: dict, *, shallow: bool = False) -> dict[str, object]:
    stats_source = _summary_source(run.get("stats_summary"))
    performance_source = _summary_source(run.get("performance_summary"))
    def source_availability(path: object, source: dict) -> str:
        if shallow:
            return _source_stat_availability(
                path, size=source.get("size"), mtime_ns=source.get("mtime_ns"),
            )
        if not source and isinstance(path, str) and Path(path).is_file():
            return "available"
        return _source_revision_availability(
            path,
            sha256=source.get("sha256"),
            size=source.get("size"),
            mtime_ns=source.get("mtime_ns"),
            parser_version=source.get("parser_version"),
        )

    stats_availability = source_availability(run.get("stats_path"), stats_source)
    performance_availability = source_availability(
        run.get("performance_path"), performance_source,
    )
    trace_quality = _trace_quality(
        run.get("trace_state"), run.get("mouse_trace_path"), shallow=shallow,
    )
    video_availability, video = _current_video_evidence(run, shallow=shallow)
    native = (
        stats_availability == "available"
        and performance_availability == "available"
        and trace_quality["availability"] == "available"
    )
    try:
        canonical_window = _canonical_time_window_from_run(run)
    except ValueError:
        canonical_window = None
    canonical_window_available = canonical_window is not None
    fixed_bundle = validate_source_requirements({
        "sources": {
            "stats": {"availability": stats_availability},
            "performance": {"availability": performance_availability},
            "video": {"availability": video_availability},
        },
        "trace": {"availability": trace_quality["availability"]},
        "canonical_time_window": canonical_window,
    })
    supported = ["multimodal"] if fixed_bundle["ready"] else []
    ready = bool(supported)
    analysis_count = int(run.get("analysis_count") or 0)
    limitations: list[str] = []
    if stats_availability != "available":
        limitations.append(f"stats_{stats_availability}")
    if performance_availability != "available" and native is False:
        limitations.append(f"performance_{performance_availability}")
    if trace_quality["availability"] != "available" and "input_native" not in supported:
        limitations.append(f"raw_{trace_quality['availability']}")
    if video_availability != "available":
        limitations.append(f"video_{video_availability}")
    if not canonical_window_available:
        limitations.append("canonical_window_missing")
    return {
        "readiness_state": (
            "analyzed" if ready and analysis_count > 0
            else "pending_analysis" if ready
            else "incomplete_evidence"
        ),
        "ready": ready,
        "supported_input_modes": supported,
        "evidence_availability": {
            "stats": stats_availability,
            "performance": performance_availability,
            "raw": trace_quality["availability"],
            "video": video_availability,
            "canonical_window": "available" if canonical_window_available else "missing",
        },
        "video": video,
        "video_quality": _video_quality(run, video_availability),
        "limitations": limitations,
    }


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


def _public_stats_calibration(run: dict) -> dict[str, float] | None:
    """Project only scalar calibration facts from the private Stats summary."""
    summary = run.get("stats_summary")
    config = summary.get("config") if isinstance(summary, dict) else None
    if not isinstance(config, dict):
        config = {}
    raw_values = {
        "fov": config.get("FOV", run.get("stats_fov")),
        "dpi": config.get("DPI", run.get("stats_dpi")),
        "sensitivity": config.get("Horiz Sens", run.get("stats_sensitivity")),
        "cm_per_360": (
            summary.get("cm_per_360", run.get("stats_cm_per_360"))
            if isinstance(summary, dict)
            else run.get("stats_cm_per_360")
        ),
    }
    values: dict[str, float] = {}
    for key, raw in raw_values.items():
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            continue
        if numeric > 0 and numeric == numeric:
            values[key] = numeric
    return values or None


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


def public_kovaak_run(run: dict, *, shallow: bool = False) -> dict:
    """Project a DB-private run row into a path-free public DTO."""
    stats_path = run.get("stats_path")
    performance_path = run.get("performance_path")
    trace_path = run.get("mouse_trace_path")
    stats_source = _summary_source(run.get("stats_summary"))
    performance_source = _summary_source(run.get("performance_summary"))
    evidence = _run_evidence_view(run, shallow=shallow)
    video = evidence["video"]
    alignment = _public_alignment(run)
    trace_quality = _trace_quality(
        run.get("trace_state"), trace_path, shallow=shallow,
    )
    coverage = alignment.get("coverage")
    if (
        trace_quality["availability"] == "available"
        and alignment.get("state") == "resolved"
        and isinstance(coverage, (int, float))
        and not isinstance(coverage, bool)
        and 0 <= coverage <= 1
    ):
        trace_quality["alignment_status"] = "aligned"
        trace_quality["coverage"] = coverage
    if shallow:
        source_availability = {
            "stats": _source_stat_availability(
                stats_path,
                size=stats_source.get("size"),
                mtime_ns=stats_source.get("mtime_ns"),
            ),
            "performance": _source_stat_availability(
                performance_path,
                size=performance_source.get("size"),
                mtime_ns=performance_source.get("mtime_ns"),
            ),
        }
    else:
        source_availability = {
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
        }
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
        "video_artifact_ref": (
            video.get("artifact_ref") if isinstance(video, dict) else None
        ),
        "source_availability": source_availability,
        "trace_quality": trace_quality,
        "trace_state": run.get("trace_state", "none"),
        "trace_error": _public_string(run.get("trace_error")),
        "finalization_state": run.get("finalization_state") or "discovered",
        "finalization_error": _public_string(run.get("finalization_error")),
        "readiness_state": evidence["readiness_state"],
        "analysis_count": int(run.get("analysis_count") or 0),
        "supported_input_modes": evidence["supported_input_modes"],
        "evidence_availability": evidence["evidence_availability"],
        "alignment": alignment,
        "video_quality": evidence["video_quality"],
        "limitations": evidence["limitations"],
        "stats_calibration": _public_stats_calibration(run),
        "stats_summary": _public_summary(run.get("stats_summary")),
        "performance_summary": _public_summary(run.get("performance_summary")),
        "created_at": _sqlite_timestamp_to_wire_utc(run.get("created_at")),
        "updated_at": _sqlite_timestamp_to_wire_utc(run.get("updated_at")),
    }


async def list_kovaak_run_summaries(user_id: str, limit: int = 100) -> list[dict]:
    """Return path-free Run summaries with current evidence readiness."""
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT kr.id, kr.source_key, kr.scenario, kr.stats_path, kr.performance_path, "
        "kr.mouse_trace_path, kr.trace_state, kr.trace_error, "
        "kr.window_start_epoch_ms, kr.window_end_epoch_ms, "
        "kr.alignment_state, kr.alignment_summary, kr.finalization_state, "
        "kr.finalization_error, kr.video_path, kr.video_state, kr.video_error, "
        "kr.video_receipt_json, kr.video_summary_json, kr.created_at, kr.updated_at, "
        "json_extract(kr.stats_summary, '$.config.FOV') AS stats_fov, "
        "json_extract(kr.stats_summary, '$.config.DPI') AS stats_dpi, "
        "json_extract(kr.stats_summary, '$.config.\"Horiz Sens\"') AS stats_sensitivity, "
        "json_extract(kr.stats_summary, '$.cm_per_360') AS stats_cm_per_360, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kr.id "
        "AND s.user_id=kr.user_id) AS analysis_count "
        "FROM kovaak_runs AS kr WHERE kr.user_id=? "
        "ORDER BY kr.created_at DESC, kr.id DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    summaries = []
    for row in await cur.fetchall():
        public = public_kovaak_run(_row(row), shallow=True)
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


def _canonical_time_window_from_run(run: dict) -> dict[str, object] | None:
    if run.get("alignment_state") != "resolved":
        return None
    start_ms = run.get("window_start_epoch_ms")
    end_ms = run.get("window_end_epoch_ms")
    summary = run.get("alignment_summary")
    if (
        isinstance(start_ms, bool)
        or isinstance(end_ms, bool)
        or not isinstance(start_ms, int)
        or not isinstance(end_ms, int)
        or end_ms <= start_ms
        or not isinstance(summary, dict)
    ):
        raise ValueError("source_unavailable: canonical time window is invalid")
    duration_ms = end_ms - start_ms
    if (
        summary.get("start_ms", start_ms) != start_ms
        or summary.get("end_ms", end_ms) != end_ms
        or summary.get("duration_ms") != duration_ms
    ):
        raise ValueError("source_unavailable: canonical time window changed")
    start_source = summary.get("start_source")
    end_source = summary.get("end_source")
    warnings = summary.get("warnings") or ()
    if (
        not isinstance(start_source, str)
        or not start_source
        or not isinstance(end_source, str)
        or not end_source
        or not isinstance(warnings, (list, tuple))
        or not all(isinstance(item, str) and item for item in warnings)
    ):
        raise ValueError("source_unavailable: canonical time provenance is invalid")
    return {
        "schema_version": CANONICAL_TIME_WINDOW_VERSION,
        "timebase_version": summary.get("timebase_version", "time_alignment.v2"),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": duration_ms,
        "start_source": start_source,
        "end_source": end_source,
        "stats_anchor_status": summary.get("stats_anchor_status", "missing"),
        "stats_time_of_day_ms": summary.get("stats_time_of_day_ms"),
        "stats_local_to_utc_mapping": summary.get("stats_local_to_utc_mapping"),
        "warnings": list(warnings),
        "window_semantics": "half_open",
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
    public_snapshot = {
        "schema_version": snapshot.get("schema_version", "analysis_input_snapshot.v1"),
        "run_id": snapshot.get("run_id"),
        "scenario": _public_string(snapshot.get("scenario")),
        "scenario_identity_version": snapshot.get("scenario_identity_version"),
        "sources": sources,
        "trace": public_trace,
        "canonical_time_window": _sanitize_public_value(
            snapshot.get("canonical_time_window")
        ),
    }
    if "scenario_resolution" in snapshot:
        public_snapshot["scenario_resolution"] = _sanitize_public_value(
            snapshot.get("scenario_resolution")
        )
    if "scenario_behavior_descriptor" in snapshot:
        public_snapshot["scenario_behavior_descriptor"] = _sanitize_public_value(
            snapshot.get("scenario_behavior_descriptor")
        )
    if "calibration" in snapshot:
        public_snapshot["calibration"] = _sanitize_public_value(
            snapshot.get("calibration")
        )
    return public_snapshot


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
        "capture_session_id, window_start_epoch_ms, window_end_epoch_ms, "
        "alignment_state, alignment_summary, finalization_state, finalization_error, "
        "video_path, video_state, pending_video_path, video_request_digest, "
        "video_receipt_json, video_summary_json, video_error, "
        "stats_summary, performance_summary, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kovaak_runs.id "
        "AND s.user_id=kovaak_runs.user_id) AS analysis_count, "
        "created_at, updated_at "
        "FROM kovaak_runs WHERE user_id=? AND source_key=?",
        (user_id, source_key),
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("kovaak run disappeared after upsert")
    return _row(row)


async def attach_mouse_trace_snapshot_window(
    run: dict,
    *,
    user_id: str,
    raw_input_snapshot_path: str | Path | None,
    covered_through_epoch_ms: int | None = None,
    raw_snapshot_receipt: dict[str, object] | None = None,
    require_coverage: bool = False,
) -> dict:
    """Attach one canonical Raw window after an optional native coverage barrier."""
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
            raise TracePendingError(
                "trace_pending: Raw Input snapshot coverage is not ready"
            )
        return await mark_mouse_trace_unavailable(
            run["id"], user_id, "trace_snapshot_stale",
        ) or run
    if not raw_input_snapshot_path or not Path(raw_input_snapshot_path).is_file():
        if within_retention:
            await mark_mouse_trace_waiting(run["id"], user_id)
            raise TracePendingError("trace_pending: waiting for Raw Input snapshot")
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
        if within_retention:
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
        return await attach_mouse_trace(
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
    """Parse available source files and idempotently persist one run."""
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
            raise SourceUnstableError("source_unstable: Stats changed while parsing")
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
            raise SourceUnstableError("source_unstable: Performance changed while parsing")
        performance_scenario = performance.header.scenario_name or None
        performance_summary = {
            "header": asdict(performance.header),
            "event_count": len(performance.events),
            "source_event_count": performance.source_event_count,
            "omitted_event_indexes": list(performance.omitted_event_indexes),
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


async def _get_kovaak_run_by_source_key(user_id: str, source_key: str) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "capture_session_id, window_start_epoch_ms, window_end_epoch_ms, "
        "alignment_state, alignment_summary, finalization_state, finalization_error, "
        "video_path, video_state, pending_video_path, video_request_digest, "
        "video_receipt_json, video_summary_json, video_error, "
        "stats_summary, performance_summary, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kovaak_runs.id "
        "AND s.user_id=kovaak_runs.user_id) AS analysis_count, "
        "created_at, updated_at "
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
        "capture_session_id, window_start_epoch_ms, window_end_epoch_ms, "
        "alignment_state, alignment_summary, finalization_state, finalization_error, "
        "video_path, video_state, pending_video_path, video_request_digest, "
        "video_receipt_json, video_summary_json, video_error, "
        "stats_summary, performance_summary, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kovaak_runs.id "
        "AND s.user_id=kovaak_runs.user_id) AS analysis_count, "
        "created_at, updated_at "
        "FROM kovaak_runs WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    return [_row(row) for row in await cur.fetchall()]


async def get_kovaak_run(run_id: int, user_id: str) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, source_key, scenario, stats_path, performance_path, "
        "mouse_trace_path, trace_state, pending_trace_path, trace_error, "
        "capture_session_id, window_start_epoch_ms, window_end_epoch_ms, "
        "alignment_state, alignment_summary, finalization_state, finalization_error, "
        "video_path, video_state, pending_video_path, video_request_digest, "
        "video_receipt_json, video_summary_json, video_error, "
        "stats_summary, performance_summary, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kovaak_runs.id "
        "AND s.user_id=kovaak_runs.user_id) AS analysis_count, "
        "created_at, updated_at "
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
        "capture_session_id, window_start_epoch_ms, window_end_epoch_ms, "
        "alignment_state, alignment_summary, finalization_state, finalization_error, "
        "video_path, video_state, pending_video_path, video_request_digest, "
        "video_receipt_json, video_summary_json, video_error, "
        "stats_summary, performance_summary, "
        "(SELECT COUNT(*) FROM sessions AS s WHERE s.kovaak_run_id=kovaak_runs.id "
        "AND s.user_id=kovaak_runs.user_id) AS analysis_count, "
        "created_at, updated_at "
        "FROM kovaak_runs WHERE id=?",
        (run_id,),
    )
    row = await cur.fetchone()
    return _row(row) if row else None


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
    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET alignment_state=?, alignment_summary=?, "
        "window_start_epoch_ms=?, window_end_epoch_ms=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        (
            state,
            _json(summary),
            start_epoch_ms,
            end_epoch_ms,
            run_id,
            user_id,
        ),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


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
    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET finalization_state=?, finalization_error=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        (state, error, run_id, user_id),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def get_kovaak_run_by_source_key(
    user_id: str,
    source_key: str,
) -> Optional[dict]:
    return await _get_kovaak_run_by_source_key(user_id, source_key)


def _managed_run_video_path(
    data_root: str | Path,
    run_id: int,
    video_path: str | Path,
) -> tuple[Path, str]:
    run_root = (Path(data_root) / "runs" / str(run_id)).resolve()
    candidate = Path(video_path).resolve()
    try:
        candidate.relative_to(run_root)
    except ValueError as error:
        raise ValueError("video path must stay inside the managed Run root") from error
    match = re.fullmatch(r"video-([A-Za-z0-9_-]{1,64})\.mp4", candidate.name)
    if match is None:
        raise ValueError("managed Run video path has an invalid file name")
    return candidate, match.group(1)


def _video_receipt_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.receipt.json")


def _file_fingerprint(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


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
        if (
            current.get("pending_video_path") == str(candidate)
            and current.get("video_request_digest") == request_digest
            and current.get("capture_session_id") == capture_session_id
            and current.get("window_start_epoch_ms") == start_epoch_ms
            and current.get("window_end_epoch_ms") == end_epoch_ms
        ):
            return current
        return current

    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET capture_session_id=?, window_start_epoch_ms=?, "
        "window_end_epoch_ms=?, alignment_state='resolved', alignment_summary=?, "
        "finalization_state='pending', finalization_error=NULL, video_path=NULL, "
        "video_state='pending', pending_video_path=?, video_request_digest=?, "
        "video_receipt_json=NULL, video_summary_json=NULL, video_error=NULL, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? "
        "AND video_state IN ('none', 'unavailable')",
        (
            capture_session_id,
            start_epoch_ms,
            end_epoch_ms,
            _json(alignment_summary),
            str(candidate),
            request_digest,
            run_id,
            user_id,
        ),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


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
    where = "WHERE id=? AND user_id=?"
    params: list[object] = [error, run_id, user_id]
    if expected_pending_video_path is not None:
        where += " AND video_state='pending' AND pending_video_path=?"
        params.append(str(Path(expected_pending_video_path).resolve()))
    else:
        where += " AND video_state!='attached'"
    if expected_request_digest is not None:
        if _SHA256_DIGEST.fullmatch(expected_request_digest) is None:
            raise ValueError("video request digest is invalid")
        where += " AND video_request_digest=?"
        params.append(expected_request_digest.lower())
    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET video_path=NULL, video_state='unavailable', "
        "pending_video_path=NULL, video_receipt_json=NULL, video_summary_json=NULL, "
        "video_error=?, updated_at=CURRENT_TIMESTAMP " + where,
        tuple(params),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def invalidate_run_for_video_coverage_gap(
    run_id: int,
    user_id: str,
    *,
    expected_pending_video_path: str | Path,
    expected_request_digest: str,
    data_root: str | Path,
) -> Optional[dict]:
    candidate, _request_id = _managed_run_video_path(
        data_root, run_id, expected_pending_video_path,
    )
    if _SHA256_DIGEST.fullmatch(expected_request_digest) is None:
        raise ValueError("video request digest is invalid")
    expected_request_digest = expected_request_digest.lower()
    tombstone: dict[str, object] | None = None

    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        row = await (
            await conn.execute(
                "SELECT user_id, video_state, pending_video_path, "
                "video_request_digest, mouse_trace_path, trace_state, "
                "finalization_state, finalization_error "
                "FROM kovaak_runs WHERE id=?",
                (run_id,),
            )
        ).fetchone()
        if row is None:
            await conn.execute("COMMIT")
            return None
        if row["user_id"] != user_id:
            raise PermissionError("kovaak run is not owned by this user")
        if (
            row["finalization_state"] == "finalized"
            and row["finalization_error"] == "video_coverage_gap"
            and row["video_state"] == "unavailable"
            and row["trace_state"] == "unavailable"
        ):
            await conn.execute("COMMIT")
            return await get_kovaak_run(run_id, user_id)
        if (
            row["video_state"] != "pending"
            or row["pending_video_path"] != str(candidate)
            or row["video_request_digest"] != expected_request_digest
        ):
            raise ValueError("video pending state changed before coverage invalidation")

        trace_path = row["mouse_trace_path"]
        if row["trace_state"] == "attached" and trace_path:
            try:
                artifact, relative_path = _managed_evidence_artifact(
                    data_root, run_id, "raw", trace_path,
                )
                fingerprint = _file_fingerprint(artifact)
            except (OSError, ValueError):
                pass
            else:
                await conn.execute(
                    "INSERT INTO run_evidence_deletion_tombstones("
                    "run_id, evidence_kind, owner_id, artifact_relpath, "
                    "expected_sha256, expected_size) VALUES(?, 'raw', ?, ?, ?, ?)",
                    (
                        run_id,
                        user_id,
                        relative_path,
                        fingerprint["sha256"],
                        fingerprint["size"],
                    ),
                )
                tombstone = {
                    "run_id": run_id,
                    "evidence_kind": "raw",
                    "owner_id": user_id,
                    "artifact_relpath": relative_path,
                    "expected_sha256": fingerprint["sha256"],
                    "expected_size": fingerprint["size"],
                }

        cursor = await conn.execute(
            "UPDATE kovaak_runs SET video_path=NULL, video_state='unavailable', "
            "pending_video_path=NULL, video_receipt_json=NULL, "
            "video_summary_json=NULL, video_error='video_coverage_gap', "
            "mouse_trace_path=NULL, trace_state='unavailable', "
            "pending_trace_path=NULL, trace_error='trace_video_coverage_gap', "
            "finalization_state='finalized', "
            "finalization_error='video_coverage_gap', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND user_id=? AND video_state='pending' "
            "AND pending_video_path=? AND video_request_digest=?",
            (
                run_id,
                user_id,
                str(candidate),
                expected_request_digest,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("coverage invalidation lost its pending video owner")
        await conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise

    if tombstone is not None:
        await _cleanup_evidence_tombstone(tombstone, data_root)
    return await get_kovaak_run(run_id, user_id)


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

    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET video_path=?, video_state='attached', "
        "pending_video_path=NULL, video_receipt_json=?, video_summary_json=?, "
        "video_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? "
        "AND video_state='pending' AND pending_video_path=? "
        "AND video_request_digest=?",
        (
            str(candidate),
            _json(receipt),
            _json(summary),
            run_id,
            user_id,
            str(expected_path),
            expected_request_digest,
        ),
    )
    await conn.commit()
    return await get_kovaak_run(run_id, user_id)


async def _mark_run_video_waiting(
    run_id: int,
    user_id: str,
    pending_video_path: str | Path,
    request_digest: str,
) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE kovaak_runs SET video_error='video_waiting_artifact', "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? "
        "AND video_state='pending' AND pending_video_path=? "
        "AND video_request_digest=?",
        (
            run_id,
            user_id,
            str(Path(pending_video_path).resolve()),
            request_digest,
        ),
    )
    await conn.commit()


def _quarantine_run_video_file(candidate: Path, quarantine_root: Path) -> bool:
    if not candidate.is_file():
        return False
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / candidate.name
    if destination.exists():
        destination = quarantine_root / (
            f"{candidate.stem}-{uuid4().hex}{candidate.suffix}"
        )
    candidate.replace(destination)
    return True


def _is_app_video_artifact(path: Path) -> bool:
    name = path.name
    return bool(
        re.fullmatch(r"video-[A-Za-z0-9_-]{1,64}\.mp4", name)
        or re.fullmatch(r"video-[A-Za-z0-9_-]{1,64}\.receipt\.json", name)
        or (name.startswith(".video-") and ".partial-" in name)
    )


def _managed_evidence_artifact(
    data_root: str | Path,
    run_id: int,
    evidence_kind: str,
    path: str | Path,
) -> tuple[Path, str]:
    root = Path(data_root).resolve()
    run_root = (root / "runs" / str(run_id)).resolve()
    candidate = Path(path).resolve()
    try:
        relative_to_run = candidate.relative_to(run_root)
    except ValueError as error:
        raise ValueError("Run evidence path escapes the managed Run root") from error
    if len(relative_to_run.parts) != 1:
        raise ValueError("Run evidence must be a direct managed Run artifact")
    if evidence_kind == "video":
        _managed_run_video_path(data_root, run_id, candidate)
    elif evidence_kind == "raw":
        if re.fullmatch(r"trace-[A-Za-z0-9_-]{1,128}\.bin", candidate.name) is None:
            raise ValueError("managed Raw artifact has an invalid file name")
    else:
        raise ValueError("evidence kind must be video or raw")
    return candidate, candidate.relative_to(root).as_posix()


def _resolve_evidence_relpath(
    data_root: str | Path,
    run_id: int,
    evidence_kind: str,
    relative_path: str,
) -> Path:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ValueError("stored Run evidence path is invalid")
    root = Path(data_root).resolve()
    candidate = (root / Path(*relative_path.split("/"))).resolve()
    managed, expected_relative = _managed_evidence_artifact(
        data_root, run_id, evidence_kind, candidate,
    )
    if expected_relative != relative_path:
        raise ValueError("stored Run evidence path is not canonical")
    return managed


def _unlink_run_evidence_artifact(path: Path) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise OSError("Run evidence artifact is not a regular file")
    size = metadata.st_size
    path.unlink()
    return size


async def _mark_evidence_cleanup_failed(run_id: int, evidence_kind: str) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE run_evidence_deletion_tombstones "
        "SET cleanup_state='failed', cleanup_attempts=cleanup_attempts + 1, "
        "last_error_code='artifact_cleanup_failed', updated_at=CURRENT_TIMESTAMP "
        "WHERE run_id=? AND evidence_kind=?",
        (run_id, evidence_kind),
    )
    await conn.commit()


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
        await _mark_evidence_cleanup_failed(run_id, evidence_kind)
        return False, 0

    conn = await get_conn()
    await conn.execute(
        "DELETE FROM run_evidence_deletion_tombstones "
        "WHERE run_id=? AND evidence_kind=?",
        (run_id, evidence_kind),
    )
    await conn.commit()
    return True, reclaimed


async def _remove_analysis_video_aliases(
    run_id: int,
    owner_id: str,
    run_video: Path,
) -> None:
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT id, video_path, input_snapshot_json FROM sessions "
            "WHERE kovaak_run_id=? AND user_id=?",
            (run_id, owner_id),
        )
    ).fetchall()
    cleared: list[int] = []
    run_video = run_video.resolve()
    for row in rows:
        session_id = int(row["id"])
        alias = session_dir(session_id) / "video.mp4"
        stored_path = row["video_path"]
        if not isinstance(stored_path, str) or Path(stored_path).resolve() != alias.resolve():
            continue
        try:
            snapshot = json.loads(row["input_snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        video_source = (snapshot.get("sources") or {}).get("video")
        source_path = video_source.get("path") if isinstance(video_source, dict) else None
        if not isinstance(source_path, str) or Path(source_path).resolve() != run_video:
            continue
        try:
            metadata = alias.lstat()
        except FileNotFoundError:
            cleared.append(session_id)
            continue
        if alias.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise OSError("Analysis video alias is not a regular file")
        if run_video.is_file() and not alias.samefile(run_video):
            continue
        alias.unlink()
        cleared.append(session_id)
    if cleared:
        placeholders = ",".join("?" for _ in cleared)
        await conn.execute(
            f"UPDATE sessions SET video_path=NULL WHERE id IN ({placeholders})",
            tuple(cleared),
        )
        await conn.commit()


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
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        row = await (
            await conn.execute(
                "SELECT id, user_id, video_path, video_state, video_summary_json, "
                "mouse_trace_path, trace_state FROM kovaak_runs WHERE id=?",
                (run_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError("kovaak run not found")
        if row["user_id"] != user_id:
            raise PermissionError("kovaak run is not owned by this user")
        existing = await (
            await conn.execute(
                "SELECT run_id, evidence_kind, owner_id, artifact_relpath, "
                "expected_sha256, expected_size, cleanup_state, cleanup_attempts, "
                "last_error_code FROM run_evidence_deletion_tombstones "
                "WHERE run_id=? AND evidence_kind=?",
                (run_id, evidence_kind),
            )
        ).fetchone()
        if existing is not None:
            await conn.execute("COMMIT")
            completed, reclaimed = await _cleanup_evidence_tombstone(dict(existing), data_root)
            return _removal_result(
                run_id,
                evidence_kind,
                "completed" if completed else "pending_cleanup",
                reclaimed if completed else 0,
                None,
            )

        state = row["video_state"] if evidence_kind == "video" else row["trace_state"]
        path_value = row["video_path"] if evidence_kind == "video" else row["mouse_trace_path"]
        if state != "attached" or not path_value:
            await conn.execute("COMMIT")
            return _removal_result(
                run_id, evidence_kind, "already_unavailable", 0, None,
            )
        artifact, relative_path = _managed_evidence_artifact(
            data_root, run_id, evidence_kind, path_value,
        )
        fingerprint = _file_fingerprint(artifact)
        artifact_ref = (
            f"run:{run_id}:video:{str(fingerprint['sha256'])[:16]}"
            if evidence_kind == "video"
            else f"run:{run_id}:trace"
        )
        await conn.execute(
            "INSERT INTO run_evidence_deletion_tombstones("
            "run_id, evidence_kind, owner_id, artifact_relpath, expected_sha256, "
            "expected_size) VALUES(?, ?, ?, ?, ?, ?)",
            (
                run_id,
                evidence_kind,
                user_id,
                relative_path,
                fingerprint["sha256"],
                fingerprint["size"],
            ),
        )
        if evidence_kind == "video":
            await conn.execute(
                "UPDATE kovaak_runs SET video_path=NULL, video_state='unavailable', "
                "pending_video_path=NULL, video_error='removed_by_user', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                (run_id, user_id),
            )
        else:
            await conn.execute(
                "UPDATE kovaak_runs SET mouse_trace_path=NULL, trace_state='unavailable', "
                "pending_trace_path=NULL, trace_error='removed_by_user', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                (run_id, user_id),
            )
        await conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            await conn.execute("ROLLBACK")
        raise

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
        run_id,
        evidence_kind,
        "completed" if completed else "pending_cleanup",
        reclaimed if completed else 0,
        artifact_ref,
    )


async def reconcile_run_evidence_deletions(
    data_root: str | Path,
) -> dict[str, int]:
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT run_id, evidence_kind, owner_id, artifact_relpath, "
            "expected_sha256, expected_size, cleanup_state, cleanup_attempts, "
            "last_error_code FROM run_evidence_deletion_tombstones "
            "ORDER BY run_id, evidence_kind"
        )
    ).fetchall()
    outcome = {"completed": 0, "failed": 0}
    for row in rows:
        completed, _reclaimed = await _cleanup_evidence_tombstone(dict(row), data_root)
        outcome["completed" if completed else "failed"] += 1
    return outcome


async def run_storage_usage(
    user_id: str, data_root: str | Path,
) -> dict[str, int]:
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT id, video_path, video_state, mouse_trace_path, trace_state "
            "FROM kovaak_runs WHERE user_id=?",
            (user_id,),
        )
    ).fetchall()
    totals = {
        "run_video_bytes": 0,
        "run_raw_bytes": 0,
        "incomplete_recovery_bytes": 0,
    }
    root = Path(data_root).resolve()
    for row in rows:
        run_id = int(row["id"])
        run_root = (root / "runs" / str(run_id)).resolve()
        if not run_root.is_dir():
            continue
        video_files: set[Path] = set()
        raw_files: set[Path] = set()
        try:
            if row["video_state"] == "attached" and row["video_path"]:
                video, _ = _managed_evidence_artifact(
                    data_root, run_id, "video", row["video_path"],
                )
                video_files.update({video, _video_receipt_path(video)})
            if row["trace_state"] == "attached" and row["mouse_trace_path"]:
                trace, _ = _managed_evidence_artifact(
                    data_root, run_id, "raw", row["mouse_trace_path"],
                )
                raw_files.add(trace)
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
    conn = await get_conn()
    rows = await (
        await conn.execute(
            "SELECT id, video_path, video_state, mouse_trace_path, trace_state "
            "FROM kovaak_runs WHERE user_id=? ORDER BY id",
            (owner_id,),
        )
    ).fetchall()
    root = Path(data_root).resolve()
    items: list[dict[str, object]] = []
    for row in rows:
        run_id = int(row["id"])
        run_root = (root / "runs" / str(run_id)).resolve()
        if not run_root.is_dir():
            continue
        owned: set[Path] = set()
        try:
            if row["video_state"] == "attached" and row["video_path"]:
                video, _ = _managed_evidence_artifact(
                    data_root, run_id, "video", row["video_path"],
                )
                owned.update({video, _video_receipt_path(video)})
            if row["trace_state"] == "attached" and row["mouse_trace_path"]:
                raw, _ = _managed_evidence_artifact(
                    data_root, run_id, "raw", row["mouse_trace_path"],
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
        conn = await get_conn()
        await conn.execute(
            "UPDATE incomplete_capture_deletion_tombstones SET cleanup_state='failed', "
            "cleanup_attempts=cleanup_attempts+1, last_error_code='artifact_cleanup_failed', "
            "updated_at=CURRENT_TIMESTAMP WHERE item_ref=?",
            (item_ref,),
        )
        await conn.commit()
        return False, 0
    conn = await get_conn()
    await conn.execute(
        "UPDATE incomplete_capture_deletion_tombstones SET cleanup_state='completed', "
        "cleanup_attempts=cleanup_attempts+1, last_error_code=NULL, reclaimed_bytes=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE item_ref=?",
        (reclaimed, item_ref),
    )
    await conn.commit()
    return True, reclaimed


async def remove_incomplete_capture_item(
    owner_id: str,
    item_ref: str,
    data_root: str | Path,
) -> dict[str, object] | None:
    conn = await get_conn()
    existing = await (
        await conn.execute(
            "SELECT * FROM incomplete_capture_deletion_tombstones "
            "WHERE item_ref=? AND owner_id=?",
            (item_ref, owner_id),
        )
    ).fetchone()
    if existing is not None:
        row = dict(existing)
        if row["cleanup_state"] == "completed":
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
        completed, reclaimed = await _cleanup_incomplete_capture_tombstone(row, data_root)
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
    await conn.execute(
        "INSERT INTO incomplete_capture_deletion_tombstones(item_ref, owner_id, run_id, "
        "artifact_relpath, expected_sha256, expected_size) VALUES(?, ?, ?, ?, ?, ?)",
        (
            item_ref, owner_id, run_id, current["_relative_path"],
            current["_sha256"], current["size_bytes"],
        ),
    )
    await conn.commit()
    tombstone = await (
        await conn.execute(
            "SELECT * FROM incomplete_capture_deletion_tombstones WHERE item_ref=?",
            (item_ref,),
        )
    ).fetchone()
    completed, reclaimed = await _cleanup_incomplete_capture_tombstone(
        dict(tombstone), data_root,
    )
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


async def reconcile_run_videos(data_root: str | Path) -> dict[str, int]:
    await reconcile_run_evidence_deletions(data_root)
    runs_root = (Path(data_root) / "runs").resolve()
    quarantine_root = runs_root / "orphans"
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, pending_video_path, video_request_digest "
        "FROM kovaak_runs WHERE video_state='pending'"
    )
    pending = [dict(row) for row in await cur.fetchall()]
    outcome = {"attached": 0, "retryable": 0, "unavailable": 0, "quarantined": 0}

    for row in pending:
        pending_path = row.get("pending_video_path")
        request_digest = row.get("video_request_digest")
        if not pending_path or not isinstance(request_digest, str):
            await mark_run_video_unavailable(
                row["id"], row["user_id"], "video_pending_state_invalid",
            )
            outcome["unavailable"] += 1
            continue
        try:
            candidate, _request_id = _managed_run_video_path(
                data_root, row["id"], pending_path,
            )
        except ValueError:
            await mark_run_video_unavailable(
                row["id"], row["user_id"], "video_managed_path_invalid",
                expected_pending_video_path=pending_path,
                expected_request_digest=request_digest,
            )
            outcome["unavailable"] += 1
            continue
        receipt_path = _video_receipt_path(candidate)
        if not candidate.is_file() or not receipt_path.is_file():
            await _mark_run_video_waiting(
                row["id"], row["user_id"], pending_path, request_digest,
            )
            outcome["retryable"] += 1
            continue
        try:
            await attach_run_video(
                row["id"],
                row["user_id"],
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
                row["id"], row["user_id"], "video_receipt_invalid",
                expected_pending_video_path=pending_path,
                expected_request_digest=request_digest,
            )
            outcome["unavailable"] += 1
        else:
            outcome["attached"] += 1

    if not runs_root.is_dir():
        return outcome
    cur = await conn.execute(
        "SELECT video_path, pending_video_path FROM kovaak_runs "
        "WHERE video_path IS NOT NULL OR pending_video_path IS NOT NULL"
    )
    referenced: set[Path] = set()
    for row in await cur.fetchall():
        for value in (row["video_path"], row["pending_video_path"]):
            if not value:
                continue
            video = Path(value).resolve()
            referenced.add(video)
            referenced.add(_video_receipt_path(video))
    tombstones = await (
        await conn.execute(
            "SELECT run_id, artifact_relpath FROM run_evidence_deletion_tombstones "
            "WHERE evidence_kind='video'"
        )
    ).fetchall()
    for row in tombstones:
        try:
            video = _resolve_evidence_relpath(
                data_root, int(row["run_id"]), "video", row["artifact_relpath"],
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


def derive_run_readiness(run: dict) -> dict[str, object]:
    evidence = _run_evidence_view(run)
    supported = evidence["supported_input_modes"]
    input_native = "input_native" in supported
    video_fallback = "video_fallback" in supported
    ready = bool(evidence["ready"])
    return {
        "ready": ready,
        "state": evidence["readiness_state"],
        "input_native": input_native,
        "video_fallback": video_fallback,
    }


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
    await reconcile_run_evidence_deletions(data_root)
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
    tombstones = await (
        await conn.execute(
            "SELECT run_id, artifact_relpath FROM run_evidence_deletion_tombstones "
            "WHERE evidence_kind='raw'"
        )
    ).fetchall()
    for row in tombstones:
        try:
            trace = _resolve_evidence_relpath(
                data_root, int(row["run_id"]), "raw", row["artifact_relpath"],
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
