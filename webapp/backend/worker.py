from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import queue
from .config import DESKTOP_LOCAL_PROFILE, HEARTBEAT_INTERVAL_SECONDS
from .contracts import (
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    ANALYSIS_VERSION,
    NATIVE_ANALYSIS_VERSION,
    build_analysis_result_v2,
    build_artifact_manifest_v2,
    build_error_v1,
)

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class SourceSnapshotChangedError(ValueError):
    """Frozen Analysis source is missing, unidentified, or no longer the same revision."""


def _read_frozen_source_bytes(kind: str, source: object) -> bytes:
    if not isinstance(source, dict):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, dict) or any(
        fingerprint.get(field) is None for field in ("sha256", "size", "mtime_ns")
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    expected = {
        "sha256": fingerprint["sha256"],
        "size": fingerprint["size"],
        "mtime_ns": fingerprint["mtime_ns"],
    }
    if (
        not isinstance(expected["sha256"], str)
        or isinstance(expected["size"], bool)
        or not isinstance(expected["size"], int)
        or expected["size"] < 0
        or isinstance(expected["mtime_ns"], bool)
        or not isinstance(expected["mtime_ns"], int)
    ):
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} identity missing")
    path = source.get("path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} path missing")
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(expected["size"] + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} file missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            f"source_unavailable: {kind} changed while reading"
        )
    actual = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mtime_ns": after.st_mtime_ns,
    }
    if actual != expected:
        raise SourceSnapshotChangedError(f"source_unavailable: {kind} revision changed")
    return data


def _managed_video_contract(job: dict, input_mode: str) -> tuple[str, str, int] | None:
    if input_mode not in {"multimodal", "video_fallback"}:
        return None
    if job.get("kovaak_run_id") is None:
        return None
    snapshot = job.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        raise SourceSnapshotChangedError("source_unavailable: video snapshot missing")
    video = sources.get("video")
    if not isinstance(video, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    if "fingerprint" not in video:
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    fingerprint = video.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    expected_mtime_ns = fingerprint.get("mtime_ns")
    if (
        not isinstance(expected_sha, str)
        or not expected_sha
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        raise SourceSnapshotChangedError("source_unavailable: video identity missing")
    path = job.get("video_path")
    if not isinstance(path, str) or not path:
        raise SourceSnapshotChangedError("source_unavailable: managed video missing")
    return path, expected_sha, expected_size


def _assert_managed_video_matches_snapshot(job: dict, input_mode: str) -> None:
    contract = _managed_video_contract(job, input_mode)
    if contract is None:
        return
    path, expected_sha, expected_size = contract
    digest = hashlib.sha256()
    observed_size = 0
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != expected_size:
                raise SourceSnapshotChangedError(
                    "source_unavailable: managed video revision changed"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                observed_size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except SourceSnapshotChangedError:
        raise
    except OSError as exc:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video missing or unreadable"
        ) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video changed while reading"
        )
    if observed_size != expected_size or digest.hexdigest() != expected_sha:
        raise SourceSnapshotChangedError(
            "source_unavailable: managed video revision changed"
        )


async def _heartbeat_loop(session_id: int, stop: asyncio.Event) -> None:
    """Renew lease until stop is set. First beat immediately, then every interval."""
    while True:
        try:
            await queue.heartbeat(session_id, WORKER_ID)
        except Exception:
            log.exception(
                "heartbeat failed session=%s worker=%s", session_id, WORKER_ID,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            continue


# --- 包装 kovaak_tracker(隔离 + 便于 mock)---

def run_analysis(
    video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
    *, stats=None,
) -> tuple[dict, dict]:
    """调 kovaak_tracker.analyze_flicking_fair_summary,返回 (summary, extras)。

    cm_per_360 / fov 优先用 caller 传的(用户填);若 None,从 CSV fallback:
      - fov:csv_parser stats.fov(KovaaK CSV 的 FOV 字段)
      - cm_per_360:csv_parser stats.cm_per_360(DPI + Horiz Sens + Sens Scale yaw 表)
    传给 analyze_flicking_fair_summary 影响 deg_per_px(fov) + peak_cm_per_s(cm/360)。
    """
    from kovaak_tracker.csv_parser import parse_stats_csv
    from kovaak_tracker.pan_tracker import analyze_flicking_fair_summary

    # CSV fallback(若 caller 没传):从 KovaaK CSV config 块读真实值
    if stats is None and (cm_per_360 is None or fov is None):
        stats = parse_stats_csv(csv_path)
    if stats is not None:
        if cm_per_360 is None:
            cm_per_360 = stats.cm_per_360
        if fov is None:
            fov = stats.fov

    summary, extras = analyze_flicking_fair_summary(
        video_path,
        csv_path,
        fov=fov,
        cm_per_360=cm_per_360,
        stats=stats,
        return_extras=True,
    )
    return summary, extras


def _build_timeline(extras: dict) -> list[dict]:
    """把 analyze_flicking_fair_summary 的 extras 转成 timeline events 列表。

    schema(routes.get_session_timeline 消费):
        {"frame": int, "time_s": float, "type": str, "label": str}
    types: "kill" | "peak" | "corrective"。flicking pipeline 没有 miss 概念
    (那是 tracking 的事),所以这里不产 miss markers。
    """
    if not isinstance(extras, dict):
        return []
    fps = extras.get("fps") or 60
    if fps <= 0:
        fps = 60
    events: list[dict] = []

    def _add(frame: int, type_: str, label: str) -> None:
        if frame is None or frame < 0:
            return
        events.append({
            "frame": int(frame),
            "time_s": round(frame / fps, 3),
            "type": type_,
            "label": label,
        })

    for flick in extras.get("flicks") or []:
        peak_frame = flick.get("peak_frame")
        if peak_frame is not None:
            _add(peak_frame, "peak", "速度峰值")
    for frame in extras.get("corrective_frames") or []:
        _add(frame, "corrective", "修正")
    for frame in extras.get("kill_frames") or []:
        _add(frame, "kill", "击杀")

    # 按 frame 升序排,方便前端顺序渲染。
    events.sort(key=lambda e: e["frame"])
    return events


def run_report(summary: dict, backend) -> dict:
    """调 coach.build_report(传 backend 拿 narration),返回 CoachReport dict。

    build_report 内部 best-effort 调 generate_narration:LLM 失败时 narration=None
    + notes 记错,**不崩**。所以 worker 不用单独 try LLM。
    figures(plotly Figure)转 to_dict() 使其 JSON 可序列化(mark_done 要 json.dumps)。
    """
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report
    report = build_report(summary, backend=backend)
    d = asdict(report) if is_dataclass(report) else {"_raw": str(report)}
    # plotly Figure 不可 JSON 序列化 → 转 dict
    figures = d.get("figures")
    if isinstance(figures, dict):
        d["figures"] = {
            k: (f.to_dict() if hasattr(f, "to_dict") else f)
            for k, f in figures.items()
        }
    return d


def _load_backend(profile: dict | None):
    """Build narration fallback only from the owner's selected local profile."""
    from .coach_engine import load_backend_for_profile

    return load_backend_for_profile(profile)


def _sqlite_created_at_to_iso_z(created_at: str | None) -> str:
    """SQLite ``YYYY-MM-DD HH:MM:SS`` → ``YYYY-MM-DDTHH:MM:SSZ`` (UTC)."""
    if not created_at or not str(created_at).strip():
        return _utc_now_iso_z()
    s = str(created_at).strip()
    if "T" in s:
        return s if s.endswith("Z") else f"{s}Z"
    return s.replace(" ", "T") + "Z"


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _delete_video_safely(path) -> None:
    """失败路径清理临时文件(视频/CSV)。

    用户上传的视频/CSV 是可再生副本(源在用户本地),属 CLAUDE.md §5 例外
    (regenerable 临时文件可 hard remove 而非走 Recycle Bin);且 worker
    批量清理场景下 os.remove 比 SendToRecycleBin 快千倍。函数名沿用历史。
    """
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删临时文件失败 %s: %s", path, e)


def run_native_analysis(
    snapshot: dict,
    cm_per_360: float | None = None,
    fov: float | None = None,
    *,
    return_parsed_stats: bool = False,
):
    """Load a frozen Run snapshot and invoke the native flicking adapter."""
    from .kovaak_run_store import decode_mouse_snapshot_bytes
    from kovaak_tracker.csv_parser import parse_stats_bytes
    from kovaak_tracker.native_flicking_analysis import analyze_native_flicking
    from kovaak_tracker.performance_parser import parse_performance_bytes

    sources = snapshot.get("sources") or {}
    trace = snapshot.get("trace") or {}
    stats_path = (sources.get("stats") or {}).get("path")
    performance_path = (sources.get("performance") or {}).get("path")
    trace_path = trace.get("path")
    if not isinstance(stats_path, str) or not isinstance(performance_path, str):
        raise ValueError("native analysis requires stats and performance sources")
    if not isinstance(trace_path, str):
        raise ValueError("native analysis requires a raw input trace")

    stats_bytes = _read_frozen_source_bytes("stats", sources.get("stats"))
    performance_bytes = _read_frozen_source_bytes(
        "performance", sources.get("performance"),
    )
    trace_bytes = _read_frozen_source_bytes("raw_input", trace)
    parsed_stats = parse_stats_bytes(stats_bytes, file_name=Path(stats_path).name)
    stats = {
        "summary": dict(parsed_stats.summary),
        "config": dict(parsed_stats.config),
        "scenario": parsed_stats.scenario,
        "cm_per_360": cm_per_360 if cm_per_360 is not None else parsed_stats.cm_per_360,
        "fov": fov if fov is not None else parsed_stats.fov,
    }
    trace_points = decode_mouse_snapshot_bytes(trace_bytes)
    performance = parse_performance_bytes(performance_bytes)
    result = analyze_native_flicking(
        trace_points,
        performance,
        stats=stats,
    )
    return (result, parsed_stats) if return_parsed_stats else result


def _native_deterministic_v2(
    native_result: dict,
    *,
    input_mode: str | None = None,
) -> dict:
    """Adapt the native payload to v2's path-safe public contract."""
    deterministic = native_result.get("deterministic") or {}
    # Preserve native metric keys and envelopes; v2 path safety rejects path
    # fields, not metric names such as path_length or path_efficiency.
    metrics: dict[str, object] = {}
    for key, value in (deterministic.get("metrics") or {}).items():
        if not isinstance(value, dict):
            metrics[key] = value
            continue
        metric = dict(value)
        metric.setdefault("classification", "deterministic")
        metrics[key] = metric
    trajectory = deterministic.get("trajectory") or {}
    public_trajectory = {
        "unit": trajectory.get("unit", "raw_counts"),
        "point_count": int(trajectory.get("point_count") or 0),
    }
    diagnosis = _native_diagnosis(
        metrics,
        input_mode=input_mode or native_result.get("input_mode") or "input_native",
    )
    return {
        "status": native_result.get("status", "unavailable"),
        "summary": dict(diagnosis.get("summary") or {}),
        "trajectory": public_trajectory,
        "metrics": metrics,
        "timeline": list(deterministic.get("timeline") or []),
        "diagnosis": diagnosis,
        "figures": {},
        "limitations": list(native_result.get("limitations") or []),
    }


def _native_diagnosis(metrics: dict, *, input_mode: str = "input_native") -> dict:
    """Build deterministic Coach issues from available native distributions."""
    from dataclasses import asdict
    from kovaak_tracker.advice import advise
    from kovaak_tracker.coach.diagnosis import build_diagnosis

    supported = {
        "decel_frac",
        "linearity",
        "sparc",
        "reverse_ratio",
        "submovement_overlap",
        "peak_position_pct",
        "path_efficiency",
        "peak_speed_deg",
        "throughput",
    }
    summary: dict[str, dict[str, float]] = {}
    for key in supported:
        metric = metrics.get(key)
        if not isinstance(metric, dict) or metric.get("availability") == "unavailable":
            continue
        value = metric.get("med", metric.get("median", metric.get("value")))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        summary[key] = {
            "med": float(value),
            "metric_version": metric.get("metric_version"),
        }

    findings = advise(summary)
    for finding in findings:
        event_refs: list[str] = []
        for metric_key in finding.metric_refs:
            metric = metrics.get(metric_key)
            if not isinstance(metric, dict):
                continue
            refs = metric.get("outlier_refs") or metric.get("sample_refs") or []
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, str) and ref not in event_refs:
                    event_refs.append(ref)
                if len(event_refs) >= 3:
                    break
            if len(event_refs) >= 3:
                break
        finding.event_refs = event_refs
    diagnosis = build_diagnosis(
        findings,
        summary,
        comparison=None,
        meta={"summary_type": "flicking", "input_mode": input_mode},
    )
    return asdict(diagnosis)


def _result_owner(job: dict) -> tuple[str, str | None]:
    owner_id = job.get("user_id")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("analysis job requires user_id")
    return owner_id, owner_id if owner_id == DESKTOP_LOCAL_PROFILE else None


def _source_parser_version(kind: str, source: dict) -> str:
    version = source.get("parser_version")
    if isinstance(version, str) and version:
        return version
    from .kovaak_run_store import PERFORMANCE_PARSER_VERSION, STATS_PARSER_VERSION

    return {
        "stats": STATS_PARSER_VERSION,
        "performance": PERFORMANCE_PARSER_VERSION,
    }.get(kind, f"{kind}.v1")


def _artifact_entry(
    *,
    artifact_id: str,
    kind: str,
    source: str,
    availability: str,
    ownership: str,
    managed: bool,
    local_only: bool,
    derived_from: list[str],
    parser_version: str | None = None,
    format_version: str | int | None = None,
    checksum: str | None = None,
) -> dict:
    entry = {
        "id": artifact_id,
        "kind": kind,
        "source": source,
        "availability": availability,
        "ownership": ownership,
        "managed": managed,
        "local_only": local_only,
        "status": availability,
        "derived_from": derived_from,
    }
    if parser_version is not None:
        entry["parser_version"] = parser_version
    if format_version is not None:
        entry["format_version"] = format_version
    if checksum:
        entry["checksum"] = checksum
    return entry


def _native_source_contract(
    kind: str,
    source: dict,
    snapshot: dict,
) -> dict:
    if kind == "raw_input":
        snapshot_source = snapshot.get("trace") or {}
        version = snapshot_source.get("format_version", 1)
    else:
        snapshot_source = (snapshot.get("sources") or {}).get(kind) or {}
        version = _source_parser_version(kind, snapshot_source)
    out = dict(source)
    out["artifact_ref"] = snapshot_source.get("artifact_ref")
    out["parser_or_format_version"] = version
    return out


def _native_v2_evidence(
    native_result: dict,
    *,
    run_ref: str,
    snapshot: dict,
    analysis_id: str,
    video_availability: str | None = None,
) -> dict:
    native_evidence = native_result.get("evidence") or {}
    source_values = native_evidence.get("sources") or {}
    sources = {
        key: _native_source_contract(key, value, snapshot)
        for key, value in source_values.items()
        if isinstance(value, dict)
    }
    if video_availability is not None:
        sources["mp4"] = {
            "source": "mp4",
            "role": "visual_evidence",
            "availability": video_availability,
            "artifact_ref": f"{analysis_id}:video",
            "parser_or_format_version": "mp4",
            "alignment": "not_required",
            "warnings": [],
        }
    availability = {
        key: value.get("availability", "unavailable")
        for key, value in sources.items()
    }
    return {
        "sources": sources,
        "provenance": {
            "kovaak_run_ref": run_ref,
            "adapter": "native_flicking_analysis",
            "adapter_version": NATIVE_ANALYSIS_VERSION,
        },
        "availability": availability,
        "alignment": dict(native_evidence.get("alignment") or {"status": "unavailable"}),
        "coverage": native_evidence.get("coverage"),
        "warnings": list(native_evidence.get("warnings") or []),
    }


def _native_artifact_manifest_v2(
    job: dict,
    snapshot: dict,
    *,
    include_video: bool,
) -> dict:
    analysis_id = f"analysis:{job['id']}"
    external_inputs: list[dict] = []
    for kind, source in (snapshot.get("sources") or {}).items():
        if kind == "video":
            continue
        if isinstance(source, dict) and source.get("artifact_ref"):
            fingerprint = source.get("fingerprint") or {}
            external_inputs.append(_artifact_entry(
                artifact_id=source["artifact_ref"],
                kind=kind,
                source=kind,
                availability=source.get("availability", "unavailable"),
                ownership="user_source",
                managed=False,
                local_only=True,
                parser_version=_source_parser_version(kind, source),
                checksum=fingerprint.get("sha256") if isinstance(fingerprint, dict) else None,
                derived_from=[],
            ))
    trace = snapshot.get("trace")
    if isinstance(trace, dict) and trace.get("artifact_ref"):
        fingerprint = trace.get("fingerprint") or {}
        external_inputs.append(_artifact_entry(
            artifact_id=trace["artifact_ref"],
            kind="raw_input",
            source="raw_input",
            availability=trace.get("availability", "unavailable"),
            ownership="kovaak_run",
            managed=True,
            local_only=True,
            format_version=trace.get("format_version", 1),
            checksum=fingerprint.get("sha256") if isinstance(fingerprint, dict) else None,
            derived_from=[],
        ))
    if include_video:
        video_source = (snapshot.get("sources") or {}).get("video") or {}
        video_fingerprint = video_source.get("fingerprint") or {}
        external_inputs.append(_artifact_entry(
            artifact_id=f"{analysis_id}:video",
            kind="mp4",
            source="mp4",
            availability="available" if job.get("video_path") else "missing",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version="mp4",
            checksum=(
                video_fingerprint.get("sha256")
                if isinstance(video_fingerprint, dict)
                else None
            ),
            derived_from=[],
        ))
    return build_artifact_manifest_v2(
        external_inputs=external_inputs,
        owned_outputs=[_artifact_entry(
            artifact_id=analysis_id,
            kind="analysis_result",
            source="analysis",
            availability="available",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version=ANALYSIS_RESULT_V2_SCHEMA_VERSION,
            derived_from=[entry["id"] for entry in external_inputs],
        )],
    )


def _build_native_result_v2(
    job: dict,
    native_result: dict,
    *,
    created_at: str,
    completed_at: str,
    video_availability: str | None = None,
    warnings: list[dict] | None = None,
    visual_validation: dict | None = None,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("native analysis requires kovaak_run_id")
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    run_ref = f"run:{run_id}"
    input_mode = job.get("input_mode") or "input_native"
    deterministic = _native_deterministic_v2(native_result, input_mode=input_mode)
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    public_snapshot = public_analysis_input_snapshot(snapshot)
    if input_mode == "input_native":
        public_snapshot.get("sources", {}).pop("video", None)
    elif video_availability is not None:
        video_source = dict(public_snapshot.get("sources", {}).get("video") or {})
        video_source.update({
            "artifact_ref": f"{analysis_id}:video",
            "availability": "available" if job.get("video_path") else "missing",
        })
        public_snapshot.setdefault("sources", {})["video"] = video_source
    return build_analysis_result_v2(
        analysis_version=NATIVE_ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type=native_result.get("analysis_type", "flicking"),
        input_mode=input_mode,
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=run_ref,
        evidence=_native_v2_evidence(
            native_result,
            run_ref=run_ref,
            snapshot=snapshot,
            analysis_id=analysis_id,
            video_availability=video_availability,
        ),
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job,
            snapshot,
            include_video=video_availability is not None,
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=list(warnings or []),
        errors=[],
    )


_VIDEO_FALLBACK_SPARC_METRIC_VERSION = "flicking_fair_summary.sparc.v2"


_VIDEO_FALLBACK_METRIC_UNITS = {
    "peak_speed_deg": "degrees_per_second",
    "linearity": "dimensionless",
    "sparc": "dimensionless",
    "reverse_ratio": "dimensionless",
    "decel_frac": "dimensionless",
    "endpoint_peak": "dimensionless",
    "peak_position_pct": "percent",
    "corrective_count": "count",
    "submovement_overlap": "dimensionless",
    "path_efficiency": "dimensionless",
    "path_length_deg": "degrees",
    "throughput": "bits_per_second",
    "peak_cm_per_s": "centimeters_per_second",
    "flick_count": "count",
}


def _video_fallback_metrics(summary: dict) -> dict:
    metrics: dict[str, dict] = {}
    flick_count = summary.get("flick_count")
    sample_count = (
        int(flick_count)
        if isinstance(flick_count, (int, float))
        and not isinstance(flick_count, bool)
        and math.isfinite(float(flick_count))
        else None
    )
    for key, raw_value in summary.items():
        distribution = raw_value if isinstance(raw_value, dict) else {}
        value = distribution.get("med") if distribution else raw_value
        available = (
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        unit = _VIDEO_FALLBACK_METRIC_UNITS.get(key, "unknown")
        limitations = ["raw_input_not_used"]
        if distribution:
            limitations.append("descriptive_distribution_not_health_threshold")
        limitations.append("coverage_not_recorded")
        if unit == "unknown":
            limitations.append("unit_not_registered")
        if not available:
            limitations.append("metric_value_unavailable")
        metric = {
            "key": key,
            "value": value if available else None,
            "unit": unit,
            "availability": "available" if available else "unavailable",
            "provenance": {"kind": "fused", "sources": ["mp4", "stats"]},
            "metric_version": (
                _VIDEO_FALLBACK_SPARC_METRIC_VERSION
                if key == "sparc"
                else ANALYSIS_VERSION
            ),
            "sample_count": sample_count,
            "coverage": None,
            "classification": "deterministic",
            "limitations": limitations,
        }
        for distribution_key in ("med", "p75", "p90"):
            if distribution_key in distribution:
                metric[distribution_key] = distribution[distribution_key]
        metrics[key] = metric
    return metrics


def _build_video_fallback_result_v2(
    job: dict,
    summary: dict,
    report: dict,
    timeline: list[dict],
    *,
    created_at: str,
    completed_at: str,
    narration_status: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    run_ref = f"run:{run_id}" if run_id is not None else None
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    stats_ref = f"analysis:{job['id']}:stats"
    video_ref = f"analysis:{job['id']}:video"

    if run_ref is not None:
        public_snapshot = public_analysis_input_snapshot(snapshot)
        public_snapshot["sources"] = {
            key: value
            for key, value in public_snapshot.get("sources", {}).items()
            if key in {"stats", "video"}
        }
        stats_source = public_snapshot["sources"].get("stats")
        if stats_source and stats_source.get("artifact_ref"):
            stats_ref = stats_source["artifact_ref"]
    else:
        stats_source = {
            "artifact_ref": stats_ref,
            "availability": "available" if job.get("csv_path") else "missing",
        }
        public_snapshot = {
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": None,
            "scenario": None,
            "sources": {"stats": stats_source},
            "trace": None,
        }

    video_source = dict(public_snapshot["sources"].get("video") or {})
    video_source.update({
        "artifact_ref": video_ref,
        "availability": "available" if job.get("video_path") else "missing",
    })
    public_snapshot["sources"]["video"] = video_source
    public_snapshot["trace"] = None
    public_snapshot["calibration"] = {
        "cm_per_360": job.get("cm_per_360"),
        "fov": job.get("fov"),
    }
    stats_availability = (
        "available"
        if stats_source and stats_source.get("availability") == "available"
        else "missing"
    )
    sources = {
        "stats": {
            "source": "stats",
            "role": "scenario_config",
            "availability": stats_availability,
            "artifact_ref": stats_ref,
            "parser_or_format_version": _source_parser_version("stats", stats_source or {}),
            "alignment": "not_required",
            "warnings": [],
        },
        "mp4": {
            "source": "mp4",
            "role": "visual_evidence",
            "availability": "available" if job.get("video_path") else "missing",
            "artifact_ref": video_ref,
            "parser_or_format_version": "mp4",
            "alignment": "not_required",
            "warnings": [],
        },
    }
    external_inputs = [
        _artifact_entry(
            artifact_id=stats_ref,
            kind="stats",
            source="stats",
            availability=stats_availability,
            ownership="user_source" if run_ref is not None else "analysis",
            managed=run_ref is None,
            local_only=True,
            parser_version=_source_parser_version("stats", stats_source or {}),
            derived_from=[],
        ),
        _artifact_entry(
            artifact_id=video_ref,
            kind="mp4",
            source="mp4",
            availability="available" if job.get("video_path") else "missing",
            ownership="analysis",
            managed=True,
            local_only=True,
            format_version="mp4",
            checksum=(
                video_source.get("fingerprint", {}).get("sha256")
                if isinstance(video_source.get("fingerprint"), dict)
                else None
            ),
            derived_from=[],
        ),
    ]
    narration = report.get("narration") if narration_status == "available" else None
    provenance = {
        "adapter": "video_flicking_fair_summary",
        "adapter_version": ANALYSIS_VERSION,
    }
    if run_ref is not None:
        provenance["kovaak_run_ref"] = run_ref
    result = build_analysis_result_v2(
        analysis_version=ANALYSIS_VERSION,
        analysis_id=analysis_id,
        analysis_type="flicking",
        input_mode="video_fallback",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=run_ref,
        evidence={
            "sources": sources,
            "provenance": provenance,
            "availability": {
                key: value["availability"] for key, value in sources.items()
            },
            "alignment": {"status": "not_required"},
            "coverage": None,
            "warnings": [],
        },
        deterministic={
            "status": "available",
            "summary": summary,
            "metrics": _video_fallback_metrics(summary),
            "diagnosis": report.get("diagnosis", {}),
            "figures": report.get("figures", {}),
            "timeline": timeline,
            "limitations": ["raw_input_not_used"],
        },
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=external_inputs,
            owned_outputs=[_artifact_entry(
                artifact_id=analysis_id,
                kind="analysis_result",
                source="analysis",
                availability="available",
                ownership="analysis",
                managed=True,
                local_only=True,
                format_version=ANALYSIS_RESULT_V2_SCHEMA_VERSION,
                derived_from=[entry["id"] for entry in external_inputs],
            )],
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=[{"code": "raw_input_not_used"}],
        errors=[],
    )
    result["narration"] = {
        "status": narration_status,
        "text": narration,
        "provider": None,
        "model": None,
        "usage": None,
    }
    return result


# --- 编排 ---

async def process_one() -> bool:
    """处理一个 job。True=处理了(无论成败),False=队列空。"""
    await queue.recover_stale_jobs()
    job = await queue.claim_next(WORKER_ID)
    if job is None:
        return False
    sid = job["id"]
    stop_hb = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(sid, stop_hb))
    try:
        input_mode = job.get("input_mode") or "video_fallback"
        created_at_iso = _sqlite_created_at_to_iso_z(job.get("created_at"))
        completed_at_iso = _utc_now_iso_z()
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )

        if input_mode in {"input_native", "multimodal"}:
            if input_mode == "multimodal":
                native_result, frozen_stats = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                    return_parsed_stats=True,
                )
            else:
                native_result = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                )
                frozen_stats = None
            video_availability = None
            warnings: list[dict] = []
            visual_validation = None
            if input_mode == "multimodal":
                try:
                    stats_path = ((job.get("input_snapshot") or {}).get("sources") or {}).get(
                        "stats", {}
                    ).get("path")
                    if not isinstance(stats_path, str):
                        raise ValueError("multimodal analysis requires stats source")
                    _, visual_extras = await asyncio.to_thread(
                        run_analysis,
                        job["video_path"],
                        stats_path,
                        job.get("cm_per_360"),
                        job.get("fov"),
                        stats=frozen_stats,
                    )
                    video_availability = "available"
                    visual_validation = {
                        "status": "available",
                        "timeline": _build_timeline(visual_extras),
                    }
                except SourceSnapshotChangedError:
                    raise
                except Exception:
                    await asyncio.to_thread(
                        _assert_managed_video_matches_snapshot,
                        job,
                        input_mode,
                    )
                    log.warning("multimodal video validation unavailable session=%s", sid)
                    video_availability = "unavailable"
                    warnings.append({"code": "video_cv_unavailable"})
            result = _build_native_result_v2(
                job,
                native_result,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                video_availability=video_availability,
                warnings=warnings,
                visual_validation=visual_validation,
            )
            cost = 0.0
        else:
            try:
                summary, extras = await asyncio.to_thread(
                    run_analysis,
                    job["video_path"],
                    job["csv_path"],
                    job.get("cm_per_360"),
                    job.get("fov"),
                )
            except Exception as error:
                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                log.warning(
                    "video fallback analysis unavailable session=%s error=%s",
                    sid,
                    type(error).__name__,
                )
                raise RuntimeError("video fallback analysis failed") from None
            await asyncio.to_thread(
                _assert_managed_video_matches_snapshot,
                job,
                input_mode,
            )
            summary = dict(summary)
            sparc_distribution = summary.get("sparc")
            if isinstance(sparc_distribution, dict):
                summary["sparc"] = {
                    **sparc_distribution,
                    "metric_version": _VIDEO_FALLBACK_SPARC_METRIC_VERSION,
                }
            timeline_events = _build_timeline(extras)
            from . import provider_store

            profile = None
            try:
                candidate = await provider_store.get_default_runtime_profile(job["user_id"])
                if (
                    provider_store.runtime_profile_configured(candidate)
                    and not bool(candidate and candidate.get("credential_needs_reauth"))
                ):
                    profile = candidate
            except Exception as error:
                log.warning(
                    "selected narration profile unavailable user=%s error=%s",
                    job["user_id"],
                    type(error).__name__,
                )
            backend_failed = False
            try:
                backend = _load_backend(profile)
            except Exception as error:
                log.warning(
                    "selected narration backend unavailable user=%s error=%s",
                    job["user_id"],
                    type(error).__name__,
                )
                backend = None
                backend_failed = True
            provider_requested = profile is not None or backend is not None or backend_failed
            report_dict = await asyncio.to_thread(run_report, summary, backend)
            cost = 0.0
            narration = report_dict.get("narration")
            if isinstance(narration, str) and narration.strip() and backend is not None:
                narration_status = "available"
            elif provider_requested:
                narration_status = "unavailable"
            else:
                narration_status = "not_requested"

            result = _build_video_fallback_result_v2(
                job,
                summary,
                report_dict,
                timeline_events,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                narration_status=narration_status,
            )
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )
        if not await queue.mark_done(sid, result, cost, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        # 视频保留——coach 回放 + 失败重试；用户删除走 History 删除语义。
    except SourceSnapshotChangedError:
        log.warning("analysis source unavailable session=%s code=source_unavailable", sid)
        error_v1 = build_error_v1(
            category="input_validation",
            code="source_unavailable",
            message="分析输入源已不可用或已变更，请重新提交分析。",
            retryable=False,
            trace_id=None,
        )
        if not await queue.mark_failed(sid, error_v1, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
    except Exception:
        trace_id = str(uuid.uuid4())
        log.exception("分析失败 session=%s trace_id=%s", sid, trace_id)
        error_v1 = build_error_v1(
            category="internal_unknown",
            code="analysis_failed",
            message="分析失败，请重试；若持续失败请联系维护者。",
            retryable=True,
            trace_id=trace_id,
        )
        if not await queue.mark_failed(sid, error_v1, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        # 不删输入文件：支持用户 retry；与「用户自己删」产品决定一致。
    finally:
        stop_hb.set()
        try:
            await hb_task
        except Exception:
            log.exception("heartbeat task join failed session=%s", sid)
    return True

async def _run_loop_async() -> None:
    """单 event loop 跑消费循环(db._conn 不跨 loop)。"""
    while True:
        try:
            handled = await process_one()
        except Exception:
            log.exception("process_one 异常")
            handled = False
        if not handled:
            try:
                await queue.recover_stale_jobs()
            except Exception:
                log.exception("idle recover_stale_jobs 失败")
            await asyncio.sleep(2)


def run_loop() -> None:
    """阻塞消费循环入口(worker 进程 main)。"""
    asyncio.run(_run_loop_async())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    log.info("Aiming Cookie worker 启动")
    run_loop()
