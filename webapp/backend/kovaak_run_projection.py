"""Project DB-private KovaaK run rows into path-free public DTOs."""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import kovaak_run_store
from .source_requirements import validate_source_requirements
from .kovaak_snapshot_codec import (
    _has_valid_mouse_snapshot_header,
    read_mouse_snapshot,
)
from .kovaak_evidence_artifacts import _file_fingerprint


_SHA256_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")

_DROP_PUBLIC_VALUE = object()


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
        observed = kovaak_run_store._source_metadata(
            candidate,
            str(parser_version or "source.v1"),
        )
    except (OSError, kovaak_run_store.RetryableIngestionError):
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
    supported = list(fixed_bundle["supported_modes"])
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
        "created_at": kovaak_run_store._sqlite_timestamp_to_wire_utc(run.get("created_at")),
        "updated_at": kovaak_run_store._sqlite_timestamp_to_wire_utc(run.get("updated_at")),
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
        "schema_version": kovaak_run_store.CANONICAL_TIME_WINDOW_VERSION,
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
