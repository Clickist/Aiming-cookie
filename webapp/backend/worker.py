from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import re
import socket
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from . import analysis_output, queue
from .config import DATA_ROOT, DESKTOP_LOCAL_PROFILE, HEARTBEAT_INTERVAL_SECONDS
from .contracts import (
    ANALYSIS_RESULT_V2_SCHEMA_VERSION,
    ANALYSIS_VERSION,
    CONTINUOUS_TRACKING_ANALYSIS_VERSION,
    DYNAMIC_CLICKING_ANALYSIS_VERSION,
    NATIVE_ANALYSIS_VERSION,
    TARGET_SWITCHING_ANALYSIS_VERSION,
    SCENARIO_OUTCOME_ONLY_VERSION,
    build_analysis_result_v2,
    build_artifact_manifest_v2,
    build_error_v1,
    validate_scenario_resolution_v1,
)
from .read_models import resolve_calibration_v1

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
DYNAMIC_CLICKING_BASELINE_ANALYSIS_VERSION = "dynamic_clicking.baseline.v1"
STATIC_CLICKING_BASELINE_ANALYSIS_VERSION = "static_clicking.baseline.v1"
CONTINUOUS_TRACKING_BASELINE_ANALYSIS_VERSION = "continuous_tracking.baseline.v1"
TARGET_SWITCHING_BASELINE_ANALYSIS_VERSION = "target_switching.baseline.v1"
_FAMILY_BASELINE_ANALYSIS_VERSIONS = {
    STATIC_CLICKING_BASELINE_ANALYSIS_VERSION,
    DYNAMIC_CLICKING_BASELINE_ANALYSIS_VERSION,
    CONTINUOUS_TRACKING_BASELINE_ANALYSIS_VERSION,
    TARGET_SWITCHING_BASELINE_ANALYSIS_VERSION,
}
_FAMILY_BASELINE_DISPATCH_FAMILIES = {
    "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
}
VISUAL_WORKER_RESPONSE_LIMIT_BYTES = 64 * 1024 * 1024
VISUAL_WORKER_SHUTDOWN_GRACE_SECONDS = 2.0
# Upper bound on a single CV child process. The worker's consume loop is
# serial, so a hung child would otherwise starve every queued analysis.
VISUAL_WORKER_TIMEOUT_SECONDS = 600.0
VISUAL_WORKER_JOB_FIELDS = (
    "id", "kovaak_run_id", "video_path", "input_snapshot", "video_receipt",
)
VISUAL_WORKER_EVIDENCE_JOB_FIELDS = ("id", "user_id", "input_snapshot")

from .worker_visual_producers import (  # noqa: F401 (re-export for backward compat)
    _REVIEWED_TRACKING_SCENARIO_HASH,
    _REVIEWED_TRACKING_SCENARIO_PROFILE_REF,
    _REVIEWED_DYNAMIC_SCENARIO_HASH,
    _REVIEWED_DYNAMIC_SCENARIO_PROFILE_REF,
    _REVIEWED_SWITCHING_SCENARIO_HASH,
    _REVIEWED_SWITCHING_SCENARIO_PROFILE_REF,
    _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF,
    _build_reviewed_single_target_tracking_producer,
    _build_reviewed_dynamic_clicking_producer,
    _build_reviewed_target_switching_producer,
    _REVIEWED_VISUAL_PRODUCERS,
    _resolve_reviewed_visual_producer,
    _run_owned_visual_video_time_mapping,
    _visual_runtime_selector,
    run_visual_preprocessing,
    video_decode_preroll_ms,
)


async def _stop_visual_worker_process(process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(
            process.wait(), timeout=VISUAL_WORKER_SHUTDOWN_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


class ContinuousTrackingAnalysisProcessError(RuntimeError):
    """The isolated Tracking postprocessor failed after visual preprocessing."""

    def __init__(self, code: str, visual_result: dict) -> None:
        super().__init__(code)
        self.code = code
        self.visual_result = visual_result


async def _run_isolated_analysis_request(payload: dict) -> dict:
    from .visual_worker_process import build_child_environment
    from kovaak_tracker.visual_signals import VisualPreprocessingUnavailable

    request = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    # Under PyInstaller sys.executable is the frozen app itself: it cannot run
    # "-m module" (the flag is ignored and the full backend would start).
    # Dispatch to the same one-shot worker via the entry's argv mode instead.
    if getattr(sys, "frozen", False):
        worker_argv = [sys.executable, "--visual-worker"]
    else:
        worker_argv = [sys.executable, "-m", "webapp.backend.visual_worker_process"]
    process = await asyncio.create_subprocess_exec(
        *worker_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,
        env=build_child_environment(),
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(request), timeout=VISUAL_WORKER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # Kill the child so it cannot leak, then fail the bounded way.
        await _stop_visual_worker_process(process)
        raise RuntimeError("visual_preprocessing_timeout") from None
    except asyncio.CancelledError:
        await _stop_visual_worker_process(process)
        raise
    if process.returncode != 0 or len(stdout) > VISUAL_WORKER_RESPONSE_LIMIT_BYTES:
        raise RuntimeError("visual_preprocessing_failed")
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("visual_preprocessing_failed") from error
    if not isinstance(response, dict) or response.get("ok") is not True:
        failure = response.get("error") if isinstance(response, dict) else None
        kind = failure.get("kind") if isinstance(failure, dict) else None
        code = failure.get("code") if isinstance(failure, dict) else None
        if kind == "source_snapshot_changed" and isinstance(code, str):
            raise SourceSnapshotChangedError(code)
        if kind == "visual_preprocessing_unavailable" and isinstance(code, str):
            raise VisualPreprocessingUnavailable(code)
        if kind == "family_analysis_failed" and isinstance(code, str):
            visual_result = response.get("visual_result")
            if isinstance(visual_result, dict):
                raise ContinuousTrackingAnalysisProcessError(code, visual_result)
        raise RuntimeError("visual_preprocessing_failed")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("visual_preprocessing_failed")
    return result


async def _run_visual_worker_request(
    job: dict,
    *,
    postprocess: str | None = None,
) -> dict:
    child_job = {
        field: job[field]
        for field in VISUAL_WORKER_JOB_FIELDS
        if field in job
    }
    payload = {"job": child_job}
    if postprocess is not None:
        payload["postprocess"] = postprocess
    return await _run_isolated_analysis_request(payload)


async def run_visual_preprocessing_isolated(job: dict) -> dict:
    """Run reviewed CV outside the API/heartbeat process without changing output."""
    return await _run_visual_worker_request(job)


async def run_generic_static_clicking_isolated(job: dict) -> dict:
    """Run the untrained generic static-clicking detector in the CV child."""
    return await _run_visual_worker_request(
        job, postprocess="generic_static_clicking",
    )


async def run_continuous_tracking_pipeline_isolated(job: dict) -> tuple[dict, dict | None]:
    """Keep reviewed Tracking CV and its numeric postprocessor in one child process."""
    result = await _run_visual_worker_request(job, postprocess="continuous_tracking")
    if set(result) != {"visual_result", "family_result"}:
        raise RuntimeError("visual_preprocessing_failed")
    visual_result = result["visual_result"]
    family_result = result["family_result"]
    if not isinstance(visual_result, dict) or (
        family_result is not None and not isinstance(family_result, dict)
    ):
        raise RuntimeError("visual_preprocessing_failed")
    return visual_result, family_result


async def run_target_switching_pipeline_isolated(job: dict) -> tuple[dict, dict]:
    """Keep reviewed Switching CV and local episode projection in one child."""
    result = await _run_visual_worker_request(job, postprocess="target_switching")
    if set(result) != {"visual_result", "family_result"}:
        raise RuntimeError("visual_preprocessing_failed")
    visual_result = result["visual_result"]
    episode_result = result["family_result"]
    if not isinstance(visual_result, dict) or not isinstance(episode_result, dict):
        raise RuntimeError("visual_preprocessing_failed")
    return visual_result, episode_result


async def commit_continuous_tracking_evidence_isolated(
    job: dict,
    result: dict,
    visual_result: dict,
    tracking_result: dict,
) -> dict:
    """Build and commit Tracking evidence without occupying the desktop runtime."""
    child_job = {
        field: job[field]
        for field in VISUAL_WORKER_EVIDENCE_JOB_FIELDS
        if field in job
    }
    return await _run_isolated_analysis_request({
        "operation": "commit_continuous_tracking_evidence",
        "job": child_job,
        "result": result,
        "visual_result": visual_result,
        "tracking_result": tracking_result,
    })


def _unavailable_visual_summary(limitation: str) -> dict:
    return {
        "schema_version": "visual_signal_summary.v1",
        "status": "unavailable",
        "quality_status": "unavailable",
        "producer_version": None,
        "enabled_metric_families": [],
        "track_count": 0,
        "observation_count": 0,
        "target_coverage": None,
        "crosshair_coverage": None,
        "completeness": "unavailable",
        "event_counts": {},
        "limitations": [limitation],
    }


def _mark_visual_artifact_unavailable(result: dict) -> dict:
    """Remove an uncommitted visual claim while retaining native/outcome facts."""
    updated = dict(result)
    deterministic = dict(updated.get("deterministic") or {})
    deterministic["visual_validation"] = _unavailable_visual_summary(
        "visual_artifact_commit_failed"
    )
    limitations = list(deterministic.get("limitations") or [])
    if "visual_artifact_commit_failed" not in limitations:
        limitations.append("visual_artifact_commit_failed")
    deterministic["limitations"] = limitations
    updated["deterministic"] = deterministic
    warnings = list(updated.get("warnings") or [])
    warning = {"code": "visual_artifact_commit_failed"}
    if warning not in warnings:
        warnings.append(warning)
    updated["warnings"] = warnings
    return updated


def _artifact_with_visual_commit_limitation(artifact: dict) -> dict:
    fallback = copy.deepcopy(artifact)
    limitations = list(fallback.get("limitations") or [])
    if "visual_artifact_commit_failed" not in limitations:
        limitations.append("visual_artifact_commit_failed")
    fallback["limitations"] = limitations
    return fallback


def _downgrade_family_evidence_result(
    job: dict, result: dict, *, family_key: str,
) -> dict:
    limitation = f"{family_key}_evidence_artifact_unavailable"
    return _build_outcome_only_result_v2(
        job,
        created_at=str(result["created_at"]),
        completed_at=str(result["completed_at"]),
        limitations_override=[limitation],
        visual_validation=_unavailable_visual_summary(limitation),
        extra_warnings=[{"code": "visual_artifact_commit_failed"}],
        analysis_type_override=family_key,
    )


def _downgrade_dynamic_evidence_result(job: dict, result: dict) -> dict:
    return _downgrade_family_evidence_result(
        job, result, family_key="dynamic_clicking",
    )


def _downgrade_tracking_evidence_result(job: dict, result: dict) -> dict:
    return _downgrade_family_evidence_result(
        job, result, family_key="continuous_tracking",
    )


def _downgrade_switching_evidence_result(job: dict, result: dict) -> dict:
    return _downgrade_family_evidence_result(
        job, result, family_key="target_switching",
    )


def _maybe_commit_analysis_evidence(
    job: dict,
    result: dict,
    *,
    parsed_stats=None,
    native_result: dict | None = None,
    visual_result: dict | None = None,
    dynamic_result: dict | None = None,
    tracking_result: dict | None = None,
    switching_result: dict | None = None,
    outcome_event_bundle: dict | None = None,
    generic_visual_result: dict | None = None,
) -> dict:
    """Best-effort local L1/L2 projection before the terminal result commit.

    A legacy video-only request or a Run without a canonical window remains
    readable without this derived artifact.  Once a frozen Stats/Performance
    pair and window exist, failure is fail-closed so a partial artifact cannot
    be advertised as complete.
    """
    snapshot = job.get("input_snapshot") or {}
    window = snapshot.get("canonical_time_window")
    if not isinstance(window, dict):
        return result
    analysis_id = f"analysis:{job['id']}"
    sources = snapshot.get("sources") or {}
    stats_source = sources.get("stats") if isinstance(sources, dict) else None
    performance_source = sources.get("performance") if isinstance(sources, dict) else None
    if not isinstance(stats_source, dict) or not isinstance(performance_source, dict):
        return result
    try:
        from kovaak_tracker.analysis_evidence import (
            build_analysis_evidence_artifact_v1,
            build_processed_event_table_catalog,
            validate_analysis_evidence_artifact_v2,
        )
        from kovaak_tracker.csv_parser import parse_stats_bytes
        from kovaak_tracker.performance_parser import parse_performance_bytes
        from . import evidence_store

        if parsed_stats is None:
            parsed_stats = parse_stats_bytes(
                _read_frozen_source_bytes("stats", stats_source),
                file_name=str(stats_source.get("basename") or "stats.csv"),
            )
        performance = parse_performance_bytes(
            _read_frozen_source_bytes("performance", performance_source),
        )
        artifact = build_analysis_evidence_artifact_v1(
            analysis_ref=analysis_id,
            canonical_time_window=window,
            scenario_profile_ref=(snapshot.get("scenario_resolution") or {}).get("scenario_profile_ref"),
            stats=parsed_stats,
            performance=performance,
            stats_source_ref=stats_source.get("artifact_ref"),
            performance_source_ref=performance_source.get("artifact_ref"),
            stats_parser_version=str(stats_source.get("parser_version") or "kovaak_stats.v1"),
            performance_parser_version=str(performance_source.get("parser_version") or "kovaak_performance.v1"),
        )
        resolution = snapshot.get("scenario_resolution")
        active_static = (
            isinstance(resolution, dict)
            and resolution.get("aim_family") == "static_clicking"
            and result.get("analysis_version") == NATIVE_ANALYSIS_VERSION
        )
        if active_static:
            from kovaak_tracker.native_flicking_analysis import (
                build_native_static_evidence_extension,
            )

            adapter_input = native_result or {
                "deterministic": result.get("deterministic") or {},
                "evidence": result.get("evidence") or {},
            }
            artifact = build_native_static_evidence_extension(
                artifact,
                adapter_input,
                raw_source_ref=(snapshot.get("trace") or {}).get("artifact_ref"),
                scenario_profile_ref=resolution.get("scenario_profile_ref"),
            )
            result = _bind_static_evidence_to_diagnosis(result, artifact, analysis_id)
        base_artifact = copy.deepcopy(artifact)
    except (SourceSnapshotChangedError, ValueError, OSError) as error:
        log.warning(
            "analysis evidence projection unavailable session=%s error=%s",
            job.get("id"),
            type(error).__name__,
        )
        if dynamic_result is not None:
            return _downgrade_dynamic_evidence_result(job, result)
        if tracking_result is not None:
            return _downgrade_tracking_evidence_result(job, result)
        if switching_result is not None:
            return _downgrade_switching_evidence_result(job, result)
        return _mark_visual_artifact_unavailable(result) if visual_result is not None else result
    if visual_result is not None:
        try:
            from kovaak_tracker.visual_signals import (
                extend_analysis_evidence_with_visual_signals_v1,
            )

            artifact = extend_analysis_evidence_with_visual_signals_v1(
                base_artifact,
                visual_result,
            )
            if outcome_event_bundle is not None:
                artifact = copy.deepcopy(artifact)
                artifact["schema_version"] = "analysis_evidence_artifact.v2"
                artifact["event_bundles"].append(copy.deepcopy(outcome_event_bundle))
                artifact = validate_analysis_evidence_artifact_v2(artifact)
            if dynamic_result is not None:
                from kovaak_tracker.dynamic_clicking_analysis import (
                    extend_analysis_evidence_with_dynamic_clicking_v1,
                )

                artifact = extend_analysis_evidence_with_dynamic_clicking_v1(
                    artifact,
                    dynamic_result,
                )
            if tracking_result is not None:
                from kovaak_tracker.tracking_analysis import (
                    extend_analysis_evidence_with_continuous_tracking_v1,
                )

                artifact = extend_analysis_evidence_with_continuous_tracking_v1(
                    artifact,
                    tracking_result,
                )
            if switching_result is not None:
                from kovaak_tracker.target_switching_analysis import (
                    extend_analysis_evidence_with_target_switching_v1,
                )

                artifact = extend_analysis_evidence_with_target_switching_v1(
                    artifact,
                    switching_result,
                )
            processed_event_tables = build_processed_event_table_catalog(artifact)
            safe_ref = evidence_store.write_analysis_evidence_artifact(
                session_id=int(job["id"]),
                owner_id=str(job["user_id"]),
                artifact=artifact,
            )
        except (ValueError, OSError) as error:
            log.warning(
                "visual evidence artifact unavailable session=%s error=%s",
                job.get("id"),
                type(error).__name__,
            )
            result = (
                _downgrade_dynamic_evidence_result(job, result)
                if dynamic_result is not None
                else _mark_visual_artifact_unavailable(result)
            )
            if tracking_result is not None:
                result = _downgrade_tracking_evidence_result(job, result)
            if switching_result is not None:
                result = _downgrade_switching_evidence_result(job, result)
            artifact = _artifact_with_visual_commit_limitation(base_artifact)
            try:
                processed_event_tables = build_processed_event_table_catalog(artifact)
                safe_ref = evidence_store.write_analysis_evidence_artifact(
                    session_id=int(job["id"]),
                    owner_id=str(job["user_id"]),
                    artifact=artifact,
                )
            except (SourceSnapshotChangedError, ValueError, OSError) as fallback_error:
                log.warning(
                    "analysis evidence fallback unavailable session=%s error=%s",
                    job.get("id"),
                    type(fallback_error).__name__,
                )
                return result
    else:
        artifact = base_artifact
        if generic_visual_result is not None:
            try:
                artifact, result = _extend_with_generic_visual(
                    base_artifact,
                    result,
                    generic_visual_result,
                    job=job,
                    parsed_stats=parsed_stats,
                    native_result=native_result,
                )
            except (ValueError, OSError) as error:
                log.warning(
                    "generic visual evidence unavailable session=%s error=%s",
                    job.get("id"),
                    type(error).__name__,
                    exc_info=error,
                )
                artifact = base_artifact
        try:
            processed_event_tables = build_processed_event_table_catalog(artifact)
            safe_ref = evidence_store.write_analysis_evidence_artifact(
                session_id=int(job["id"]),
                owner_id=str(job["user_id"]),
                artifact=artifact,
            )
        except (SourceSnapshotChangedError, ValueError, OSError) as error:
            log.warning(
                "analysis evidence projection unavailable session=%s error=%s",
                job.get("id"),
                type(error).__name__,
            )
            return result
    result = dict(result)
    result["evidence"] = {
        **(result.get("evidence") or {}),
        "derived_artifact": safe_ref,
        **(
            {"processed_event_tables": processed_event_tables}
            if processed_event_tables
            else {}
        ),
    }
    manifest = dict(result.get("artifact_manifest") or {})
    owned_outputs = list(manifest.get("owned_outputs") or [])
    external_ids = {
        entry.get("id")
        for entry in list(manifest.get("external_inputs") or [])
        if isinstance(entry, dict)
    }
    owned_outputs.append(
        evidence_store.analysis_evidence_manifest_entry(
            safe_ref,
            derived_from=[
                ref for ref in (stats_source.get("artifact_ref"), performance_source.get("artifact_ref"))
                if ref in external_ids
            ],
        )
    )
    result["artifact_manifest"] = {**manifest, "owned_outputs": owned_outputs}
    return result


def _build_profile_contribution_payload(result: Mapping[str, object]) -> dict | None:
    """Compatibility wrapper around the profile store's canonical projector."""
    from .aiming_profile_store import build_contribution_from_analysis_result

    return build_contribution_from_analysis_result(result)


async def _record_profile_contribution(job: Mapping[str, object], result: Mapping[str, object]) -> None:
    payload = _build_profile_contribution_payload(result)
    if payload is None:
        return
    from . import aiming_profile_store

    await aiming_profile_store.record_deterministic_contribution(
        str(job["user_id"]),
        f"analysis:{job['id']}",
        payload,
    )


def _bind_static_evidence_to_diagnosis(
    result: dict,
    artifact: dict,
    analysis_ref: str,
) -> dict:
    """Attach analysis-scoped segment refs without changing native findings."""
    deterministic = dict(result.get("deterministic") or {})
    diagnosis = dict(deterministic.get("diagnosis") or {})
    issues = []
    segments = list(artifact.get("evidence_segments") or [])
    events = {
        event["event_id"]: event
        for bundle in artifact.get("event_bundles") or []
        for event in bundle.get("events") or []
    }
    legacy_events = {
        event.get("attributes", {}).get("legacy_event_ref"): event_id
        for event_id, event in events.items()
        if isinstance(event.get("attributes", {}).get("legacy_event_ref"), str)
    }
    rank_order = {"worst": 0, "typical": 1, "improved": 2}
    for index, issue in enumerate(diagnosis.get("issues") or [], 1):
        if not isinstance(issue, dict):
            continue
        issue = dict(issue)
        event_refs = list(issue.get("event_refs") or [])
        mapped_event_refs = list(dict.fromkeys(
            legacy_events.get(ref, ref)
            for ref in event_refs
            if isinstance(ref, str)
        ))
        mapped_events = set(mapped_event_refs)
        if mapped_event_refs:
            issue["event_refs"] = mapped_event_refs
        metric_refs = {
            ref if ref.startswith("static_clicking.") else f"static_clicking.{ref}"
            for ref in issue.get("metric_refs") or []
            if isinstance(ref, str)
        }
        matching = [
            segment for segment in segments
            if mapped_events.intersection(segment.get("event_refs") or [])
            or any(
                any(metric_ref in ref for metric_ref in metric_refs)
                for ref in segment.get("metric_refs") or []
            )
        ]
        matching.sort(key=lambda segment: rank_order.get(segment.get("rank_reason"), 99))
        if matching:
            primary = next(
                (segment for segment in matching if segment.get("rank_reason") == "worst"),
                matching[0],
            )
            supporting = [
                segment["segment_id"]
                for segment in matching
                if segment["segment_id"] != primary["segment_id"]
            ][:2]
            issue["primary_evidence_segment_ref"] = primary["segment_id"]
            issue["supporting_evidence_segment_refs"] = supporting
            issue_ref = f"{analysis_ref}:issue:{index}"
            primary["issue_refs"] = list(dict.fromkeys([*primary.get("issue_refs", []), issue_ref]))
            for segment in matching:
                if segment["segment_id"] in supporting:
                    segment["issue_refs"] = list(dict.fromkeys([*segment.get("issue_refs", []), issue_ref]))
        issues.append(issue)
    if issues:
        diagnosis["issues"] = issues
        deterministic["diagnosis"] = diagnosis
        result = dict(result)
        result["deterministic"] = deterministic
    return result


from .worker_source_validation import (  # noqa: F401 (re-export for backward compat)
    SourceSnapshotChangedError,
    _read_frozen_source_bytes,
    _managed_video_contract,
    _assert_managed_video_matches_snapshot,
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
    *, stats=None, profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
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
    if stats is None:
        stats = parse_stats_csv(csv_path)
    stats_values = {
        "cm_per_360": getattr(stats, "cm_per_360", None) if stats is not None else None,
        "fov": getattr(stats, "fov", None) if stats is not None else None,
    }
    manual_override = _manual_override_or_legacy(
        manual_override, cm_per_360=cm_per_360, fov=fov,
    )
    calibration = resolve_calibration_v1(
        stats=stats_values,
        manual_override=manual_override,
        profile_default=profile_default,
    )
    cm_per_360 = calibration["cm_per_360"]["value"]
    fov = calibration["fov"]["value"]

    summary, extras = analyze_flicking_fair_summary(
        video_path,
        csv_path,
        fov=fov,
        cm_per_360=cm_per_360,
        stats=stats,
        return_extras=True,
    )
    if isinstance(extras, dict):
        extras["calibration"] = calibration
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


def run_report(summary: dict) -> dict:
    """Build the deterministic local report without invoking a Provider."""
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report
    report = build_report(summary, backend=None)
    d = asdict(report) if is_dataclass(report) else {"_raw": str(report)}
    # plotly Figure 不可 JSON 序列化 → 转 dict
    figures = d.get("figures")
    if isinstance(figures, dict):
        d["figures"] = {
            k: (f.to_dict() if hasattr(f, "to_dict") else f)
            for k, f in figures.items()
        }
    return d


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
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
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
    canonical_window = snapshot.get("canonical_time_window")
    if snapshot.get("schema_version") in {
        "analysis_input_snapshot.v2", "analysis_input_snapshot.v3",
    } and not isinstance(canonical_window, dict):
        raise ValueError("source_unavailable: canonical time window missing")

    stats_bytes = _read_frozen_source_bytes("stats", sources.get("stats"))
    performance_bytes = _read_frozen_source_bytes(
        "performance", sources.get("performance"),
    )
    trace_bytes = _read_frozen_source_bytes("raw_input", trace)
    parsed_stats = parse_stats_bytes(stats_bytes, file_name=Path(stats_path).name)
    manual_override = _manual_override_or_legacy(
        manual_override, cm_per_360=cm_per_360, fov=fov,
    )
    calibration = resolve_calibration_v1(
        stats={"cm_per_360": parsed_stats.cm_per_360, "fov": parsed_stats.fov},
        manual_override=manual_override,
        profile_default=profile_default,
    )
    stats = {
        "summary": dict(parsed_stats.summary),
        "config": dict(parsed_stats.config),
        "scenario": parsed_stats.scenario,
        "cm_per_360": calibration["cm_per_360"]["value"],
        "fov": calibration["fov"]["value"],
        "calibration": calibration,
        "kill_count": int(len(parsed_stats.kills.index))
        if hasattr(parsed_stats, "kills")
        else None,
        "weapon_aggregates": list(
            getattr(parsed_stats, "weapon_aggregates", ()) or ()
        ),
        "field_presence": dict(
            getattr(parsed_stats, "field_presence", {}) or {}
        ),
    }
    trace_points = decode_mouse_snapshot_bytes(trace_bytes)
    performance = parse_performance_bytes(performance_bytes)
    result = analyze_native_flicking(
        trace_points,
        performance,
        stats=stats,
        canonical_window=canonical_window,
    )
    if isinstance(result, dict):
        result["calibration"] = calibration
    return (result, parsed_stats) if return_parsed_stats else result


def _manual_override_or_legacy(
    manual_override: Mapping[str, object] | None,
    *,
    cm_per_360: float | None,
    fov: float | None,
) -> Mapping[str, object] | None:
    """Treat pre-contract flat values as manual input only when override is absent."""
    if isinstance(manual_override, Mapping):
        return manual_override
    if cm_per_360 is None and fov is None:
        return None
    return {"cm_per_360": cm_per_360, "fov": fov}


def _freeze_job_calibration(job: dict, parsed_stats: object) -> dict:
    request = job.get("calibration_request")
    manual = request.get("manual_override") if isinstance(request, Mapping) else None
    profile = request.get("profile_default") if isinstance(request, Mapping) else None
    manual = _manual_override_or_legacy(
        manual,
        cm_per_360=job.get("cm_per_360"),
        fov=job.get("fov"),
    )
    calibration = resolve_calibration_v1(
        stats={
            "cm_per_360": getattr(parsed_stats, "cm_per_360", None),
            "fov": getattr(parsed_stats, "fov", None),
        },
        manual_override=manual,
        profile_default=profile,
    )
    snapshot = job.get("input_snapshot")
    if isinstance(snapshot, dict):
        snapshot["calibration"] = calibration
    job["calibration_snapshot"] = calibration
    return calibration


from .worker_family_analysis import (  # noqa: F401 (re-export for backward compat)
    _parse_frozen_stats_for_visual,
    _raw_left_button_rising_edges,
    _target_switching_episode_tracks,
    _target_switching_stats_kills,
    _build_validated_outcome_association,
    run_dynamic_clicking_analysis,
    run_continuous_tracking_analysis,
    run_target_switching_analysis,
)


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
    quality = _native_quality_projection(native_result)
    _project_metric_quality(metrics, quality)
    trajectory = deterministic.get("trajectory") or {}
    public_trajectory = {
        "unit": trajectory.get("unit", "raw_counts"),
        "point_count": int(trajectory.get("point_count") or 0),
    }
    diagnosis = _native_diagnosis(
        metrics,
        input_mode=input_mode or native_result.get("input_mode") or "input_native",
        quality=quality,
    )
    return {
        "status": native_result.get("status", "unavailable"),
        "summary": dict(diagnosis.get("summary") or {}),
        "trajectory": public_trajectory,
        "metrics": metrics,
        "timeline": list(deterministic.get("timeline") or []),
        "diagnosis": diagnosis,
        "figures": {},
        "limitations": quality["limitations"],
    }


def _native_quality_projection(native_result: Mapping[str, object]) -> dict:
    evidence = native_result.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    alignment = evidence.get("alignment")
    alignment = alignment if isinstance(alignment, Mapping) else {}
    coverage = evidence.get("coverage")
    coverage = (
        float(coverage)
        if isinstance(coverage, (int, float))
        and not isinstance(coverage, bool)
        and math.isfinite(float(coverage))
        and 0.0 <= float(coverage) <= 1.0
        else 0.0
    )
    limitations = [
        value for value in native_result.get("limitations") or []
        if isinstance(value, str) and value
    ]
    if alignment.get("status") != "aligned" or coverage < 1.0:
        limitations.append("alignment_partial")
    timeline = (native_result.get("deterministic") or {}).get("timeline")
    has_flick_evidence = isinstance(timeline, list) and any(
        isinstance(item, Mapping) and item.get("event_type") == "flick"
        for item in timeline
    )
    if not has_flick_evidence:
        limitations.append("left_click_anchors_missing")
    complete = (
        native_result.get("status") == "available"
        and alignment.get("status") == "aligned"
        and coverage >= 1.0
        and has_flick_evidence
    )
    quality_limitations = list(dict.fromkeys(limitations)) if not complete else []
    return {
        "status": "available" if complete else "limited",
        "coverage": coverage,
        "limitations": quality_limitations,
    }


def _visual_quality_projection(visual_result: Mapping[str, object]) -> dict:
    limitations = [
        value for value in visual_result.get("limitations") or []
        if value in {
            "missing_frame_pts",
            "non_monotonic_frame_pts",
            "frame_pts_outside_canonical_window",
        }
    ]
    summary = visual_result.get("safe_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    coverages = [
        value for value in (
            summary.get("target_coverage"), summary.get("crosshair_coverage"),
        )
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    ]
    return {
        "status": "limited" if limitations else "available",
        "coverage": min(float(value) for value in coverages) if coverages else None,
        "limitations": list(dict.fromkeys(limitations)),
    }


def _project_metric_quality(metrics: Mapping[str, object], quality: Mapping[str, object]) -> None:
    limitations = quality.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        return
    coverage_cap = quality.get("coverage")
    for metric in metrics.values():
        if not isinstance(metric, dict):
            continue
        coverage = metric.get("coverage")
        if (
            isinstance(coverage_cap, (int, float))
            and not isinstance(coverage_cap, bool)
            and isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and math.isfinite(float(coverage))
        ):
            metric["coverage"] = min(float(coverage), float(coverage_cap))
        metric["limitations"] = list(dict.fromkeys([
            *(metric.get("limitations") or []), *limitations,
        ]))


def _native_diagnosis(
    metrics: dict,
    *,
    input_mode: str = "input_native",
    quality: Mapping[str, object] | None = None,
) -> dict:
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
        meta={
            "summary_type": "flicking",
            "input_mode": input_mode,
            "quality_status": (quality or {}).get("status"),
            "quality_limitations": list((quality or {}).get("limitations") or []),
        },
    )
    projection = asdict(diagnosis)
    for issue in projection.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue.pop("root_causes", None)
        issue.pop("prescriptions", None)
        if issue.get("observation_ref") is None:
            issue.pop("observation_ref", None)
        if issue.get("knowledge_registry_version") is None:
            issue.pop("knowledge_registry_version", None)
        if not issue.get("knowledge_entry_refs"):
            issue.pop("knowledge_entry_refs", None)
    return projection


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


def _target_switching_production_gate(
    resolution: Mapping[str, object],
) -> bool:
    """Require only the exact reviewed visual episode producer."""
    from kovaak_tracker.visual_signals import (
        VISUAL_TARGET_EPISODE_PRODUCER_ID,
        VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        visual_detector_config_ref_v1,
    )

    profile_ref = resolution.get("scenario_profile_ref")
    if not isinstance(profile_ref, str):
        return False
    producer = _REVIEWED_VISUAL_PRODUCERS.get(profile_ref)
    if not isinstance(producer, Mapping):
        return False
    quality = producer.get("visual_quality_profile")
    if not isinstance(quality, Mapping):
        return False
    detector_config = producer.get("detector_config")
    calibration_context = quality.get("calibration_context")
    expected_profile_ref = (
        f"visual-quality:{VISUAL_TARGET_EPISODE_PRODUCER_ID}@"
        f"{VISUAL_TARGET_EPISODE_PRODUCER_VERSION}"
    )
    if not (
        quality.get("status") == "accepted"
        and quality.get("producer_id") == VISUAL_TARGET_EPISODE_PRODUCER_ID
        and quality.get("producer_version")
        == VISUAL_TARGET_EPISODE_PRODUCER_VERSION
        and quality.get("profile_ref") == expected_profile_ref
        and "switching" in (quality.get("validated_metric_families") or [])
        and (
            quality.get("quality_status_by_metric_family") or {}
        ).get("switching") == "accepted"
        and producer.get("detector_config_ref")
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
        and isinstance(detector_config, Mapping)
        and visual_detector_config_ref_v1(detector_config)
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
        and isinstance(calibration_context, Mapping)
        and calibration_context.get("detector_config_ref")
        == _REVIEWED_SWITCHING_DETECTOR_CONFIG_REF
    ):
        return False
    return True


def _scenario_dispatch(job: dict, input_mode: str) -> str:
    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution")
    if resolution is None:
        snapshot_version = snapshot.get("schema_version")
        if (
            snapshot_version in {
                "analysis_input_snapshot.v1", "analysis_input_snapshot.v2",
            }
            and (job.get("analysis_type") or "flicking") == "flicking"
        ):
            return "legacy_static_compatibility"
        if (
            not snapshot
            and input_mode == "video_fallback"
            and job.get("kovaak_run_id") is None
            and (job.get("analysis_type") or "flicking") == "flicking"
        ):
            return "legacy_static_compatibility"
        if snapshot_version == "analysis_input_snapshot.v3":
            raise ValueError("analysis_input_snapshot.v3 requires scenario resolution")
        raise SourceSnapshotChangedError(
            "source_unavailable: unsupported input snapshot",
        )
    if not isinstance(resolution, dict):
        raise ValueError("scenario resolution is invalid")
    resolution = validate_scenario_resolution_v1(resolution)
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "static_clicking"
        and input_mode in {"input_native", "multimodal"}
        and NATIVE_ANALYSIS_VERSION in (resolution.get("allowed_analyzers") or [])
        and "static_clicking" in (resolution.get("allowed_metric_families") or [])
    ):
        return NATIVE_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "dynamic_clicking"
        and input_mode == "multimodal"
        and DYNAMIC_CLICKING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "dynamic_clicking" in (resolution.get("allowed_metric_families") or [])
    ):
        return DYNAMIC_CLICKING_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "continuous_tracking"
        and input_mode == "multimodal"
        and CONTINUOUS_TRACKING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "continuous_tracking"
        in (resolution.get("allowed_metric_families") or [])
    ):
        return CONTINUOUS_TRACKING_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "target_switching"
        and input_mode == "multimodal"
        and TARGET_SWITCHING_ANALYSIS_VERSION
        in (resolution.get("allowed_analyzers") or [])
        and "target_switching"
        in (resolution.get("allowed_metric_families") or [])
        and _target_switching_production_gate(resolution)
    ):
        return TARGET_SWITCHING_ANALYSIS_VERSION
    if (
        resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("claim_ceiling") == "family_specific"
        and input_mode == "input_native"
        and resolution.get("aim_family") in {
            "dynamic_clicking", "continuous_tracking", "target_switching",
        }
        and resolution.get("aim_family")
        in (resolution.get("allowed_metric_families") or [])
    ):
        # No video: degrade the exact-reviewed visual pipeline to the family's
        # input-kinematics baseline instead of an outcome-only result.
        return f"{resolution['aim_family']}.baseline.v1"
    baseline_analyzer = f"{resolution.get('aim_family')}.baseline.v1"
    if (
        resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("claim_ceiling") == "descriptive_only"
        and resolution.get("aim_family") in _FAMILY_BASELINE_DISPATCH_FAMILIES
        and baseline_analyzer in (resolution.get("allowed_analyzers") or [])
        and set(resolution.get("allowed_metric_families") or [])
        == {"outcome", "input_kinematics"}
        and input_mode in {"input_native", "multimodal"}
    ):
        return baseline_analyzer
    return "outcome_only"


def _outcome_only_evidence(
    job: dict,
    snapshot: dict,
    *,
    include_video: bool,
) -> dict:
    analysis_id = f"analysis:{job['id']}"
    sources: dict[str, dict] = {}
    roles = {
        "stats": "outcome_source",
        "performance": "event_anchor",
        "raw_input": "input_kinematics_source",
        "mp4": "visual_source_not_analyzed",
    }
    for kind, source in (snapshot.get("sources") or {}).items():
        if not isinstance(source, dict) or kind == "video":
            continue
        artifact_ref = source.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            continue
        sources[kind] = {
            "source": kind,
            "role": roles.get(kind, "source"),
            "availability": source.get("availability", "unavailable"),
            "artifact_ref": artifact_ref,
            "parser_or_format_version": _source_parser_version(kind, source),
            "alignment": "not_evaluated",
            "warnings": [],
        }
    trace = snapshot.get("trace")
    if isinstance(trace, dict) and isinstance(trace.get("artifact_ref"), str):
        sources["raw_input"] = {
            "source": "raw_input",
            "role": roles["raw_input"],
            "availability": trace.get("availability", "unavailable"),
            "artifact_ref": trace["artifact_ref"],
            "parser_or_format_version": trace.get("format_version", 1),
            "alignment": "not_evaluated",
            "warnings": [],
        }
    if include_video:
        video_source = (snapshot.get("sources") or {}).get("video") or {}
        if isinstance(video_source, dict):
            sources["mp4"] = {
                "source": "mp4",
                "role": roles["mp4"],
                "availability": "available" if job.get("video_path") else "missing",
                "artifact_ref": f"{analysis_id}:video",
                "parser_or_format_version": "mp4",
                "alignment": "not_evaluated",
                "warnings": [],
            }
    window = snapshot.get("canonical_time_window")
    if isinstance(window, dict):
        alignment = {
            "status": "aligned",
            "challenge_start_epoch_ms": window.get("start_ms"),
            "challenge_end_epoch_ms": window.get("end_ms"),
            "window_semantics": window.get("window_semantics", "half_open"),
        }
    else:
        alignment = {"status": "unavailable"}
    return {
        "sources": sources,
        "provenance": {
            "kovaak_run_ref": (
                f"run:{job.get('kovaak_run_id') or snapshot.get('run_id')}"
            ),
            "adapter": "scenario_dispatch_gate",
            "adapter_version": SCENARIO_OUTCOME_ONLY_VERSION,
        },
        "availability": {
            kind: source["availability"] for kind, source in sources.items()
        },
        "alignment": alignment,
        "coverage": None,
        "warnings": [{"code": "family_analyzer_not_dispatched"}],
    }


def _build_outcome_only_result_v2(
    job: dict,
    *,
    created_at: str,
    completed_at: str,
    limitations_override: list[str] | None = None,
    visual_validation: dict | None = None,
    extra_warnings: list[dict] | None = None,
    analysis_type_override: str | None = None,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution") or {}
    input_mode = job.get("input_mode") or "video_fallback"
    include_video = input_mode in {"multimodal", "video_fallback"}
    public_snapshot = public_analysis_input_snapshot(snapshot)
    if input_mode == "input_native":
        public_snapshot.get("sources", {}).pop("video", None)
    owner_id, local_profile = _result_owner(job)
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("outcome-only analysis requires kovaak_run_id")
    limitations = list(
        limitations_override
        if limitations_override is not None
        else resolution.get("limitations") or ["scenario_not_in_active_manifest"]
    )
    deterministic = {
        "support_status": "outcome_only",
        "metrics": {},
        "limitations": limitations,
    }
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    result = build_analysis_result_v2(
        analysis_version=SCENARIO_OUTCOME_ONLY_VERSION,
        analysis_id=f"analysis:{job['id']}",
        analysis_type=analysis_type_override or job.get("analysis_type") or "flicking",
        input_mode=input_mode,
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=_outcome_only_evidence(
            job,
            snapshot,
            include_video=include_video,
        ),
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job,
            snapshot,
            include_video=include_video,
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=[{"code": "scenario_outcome_only"}, *(extra_warnings or [])],
        errors=[],
    )
    return result


def _build_family_result_v2(
    job: dict,
    family_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
    advice_fn,
    summary_type: str,
    analysis_version: str,
    plain_language_meaning: str,
    scenario_motion_class,
    comparison_value,
    extra_comparable_requirements=None,
    coverage_fn=None,
    set_click_anchor_source: bool = False,
    aim_family: str | None = None,
    run_id_error_message: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError(run_id_error_message)
    analysis_id = f"analysis:{job['id']}"
    owner_id, local_profile = _result_owner(job)
    metrics = {
        metric_key: {
            "key": metric_key,
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "availability": metric.get("availability"),
            "provenance": {
                "kind": (metric.get("provenance") or {}).get("kind", "derived"),
                "sources": list((metric.get("provenance") or {}).get("source_refs") or []),
            },
            "metric_version": metric.get("metric_version"),
            "coverage": metric.get("coverage"),
            "classification": metric.get("classification"),
            "limitations": list(metric.get("limitations") or []),
            "condition_refs": list(metric.get("condition_refs") or []),
        }
        for metric_key, metric in (family_result.get("metrics") or {}).items()
        if isinstance(metric, Mapping)
    }
    visual_summary = dict(visual_result.get("safe_summary") or {})
    visual_quality = _visual_quality_projection(visual_result)
    _project_metric_quality(metrics, visual_quality)
    visual_quality_profile_ref = visual_result.get("visual_quality_profile_ref")
    if isinstance(visual_quality_profile_ref, str) and visual_quality_profile_ref:
        for metric in metrics.values():
            metric["calibration_ref"] = visual_quality_profile_ref
    candidate_observations = advice_fn(family_result)
    comparable_requirements = [
        "same scenario profile", "same visual quality profile",
        "same motion condition",
    ]
    if extra_comparable_requirements:
        comparable_requirements.extend(extra_comparable_requirements)
    comparable_requirements.append("same metric version")
    diagnosis_issues = [
        {
            "signal": candidate["signal"],
            "priority": index,
            "priority_reason": "matched comparison candidate",
            "plain_language_meaning": plain_language_meaning,
            "claim_level": candidate["claim_level"],
            "metric_refs": list(candidate["metric_refs"]),
            "observation_ref": candidate["observation_ref"],
            "knowledge_registry_version": candidate["knowledge_registry_version"],
            "knowledge_entry_refs": list(candidate["knowledge_entry_refs"]),
            "event_refs": [
                *candidate["supporting_row_refs"],
                *candidate["counterexample_row_refs"],
            ],
            "limitations": list(candidate["limitations"]),
            "verification": {
                "comparable_requirements": comparable_requirements,
                "success_signals": ["move toward the matched baseline without outcome collapse"],
                "insufficient_evidence_behavior": "keep the observation descriptive and collect another matched Run",
            },
        }
        for index, candidate in enumerate(candidate_observations, 1)
    ]
    deterministic = {
        "support_status": family_result.get("support_status"),
        "scenario_motion_class": scenario_motion_class,
        "metrics": metrics,
        "candidate_observations": candidate_observations,
        "diagnosis": {
            "profile": {},
            "issues": diagnosis_issues,
            "summary": metrics,
            "comparison": comparison_value,
            "meta": {
                "summary_type": summary_type,
                "classification": "deterministic",
            },
        },
        "visual_validation": visual_summary,
        "visual_quality_profile_ref": visual_quality_profile_ref,
        "limitations": list(dict.fromkeys([
            *(family_result.get("limitations") or []),
            *visual_quality["limitations"],
        ])),
    }
    evidence = _outcome_only_evidence(job, snapshot, include_video=True)
    evidence["provenance"] = {
        "kovaak_run_ref": f"run:{run_id}",
        "adapter": summary_type,
        "adapter_version": analysis_version,
    }
    if coverage_fn is not None:
        coverage_components = coverage_fn(family_result, visual_summary)
    else:
        coverage_components = [
            visual_summary.get("target_coverage"),
            visual_summary.get("crosshair_coverage"),
        ]
    evidence["coverage"] = (
        min(float(value) for value in coverage_components)
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
            for value in coverage_components
        )
        else None
    )
    evidence["warnings"] = []
    if "mp4" in evidence["sources"]:
        evidence["sources"]["mp4"]["role"] = "visual_kinematics_source"
        evidence["sources"]["mp4"]["alignment"] = "aligned"
    if set_click_anchor_source and "raw_input" in evidence["sources"]:
        evidence["sources"]["raw_input"]["role"] = "click_anchor_source"
        evidence["sources"]["raw_input"]["alignment"] = "aligned"
    result = build_analysis_result_v2(
        analysis_version=analysis_version,
        analysis_id=analysis_id,
        analysis_type=summary_type,
        input_mode="multimodal",
        owner_id=owner_id,
        local_profile=local_profile,
        kovaak_run_ref=f"run:{run_id}",
        evidence=evidence,
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job, snapshot, include_video=True,
        ),
        input_snapshot=public_analysis_input_snapshot(snapshot),
        created_at=created_at,
        completed_at=completed_at,
        warnings=[],
        errors=[],
    )
    resolution = snapshot.get("scenario_resolution") or {}
    if aim_family is not None:
        result["scenario"] = {
            "scenario_profile_ref": resolution.get("scenario_profile_ref"),
            "aim_family": aim_family,
            "analyzer_refs": [analysis_version],
            "support_status": family_result.get("support_status", "unavailable"),
            "limitations": list(family_result.get("limitations") or []),
        }
    else:
        result["scenario"] = {
            "scenario_profile_ref": resolution.get("scenario_profile_ref"),
            "analyzer_refs": [analysis_version],
            "support_status": family_result.get("support_status", "unavailable"),
            "limitations": list(family_result.get("limitations") or []),
        }
    return result


def _build_dynamic_result_v2(
    job: dict,
    dynamic_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from kovaak_tracker.advice_dynamic_clicking import (
        build_dynamic_clicking_candidate_advice,
    )

    def coverage_fn(family_result, visual_summary):
        processed_rows = family_result.get("processed_rows")
        processed_table = family_result.get("processed_event_table")
        click_row_count = (
            processed_table.get("row_count")
            if isinstance(processed_table, Mapping)
            else None
        )
        return [
            visual_summary.get("target_coverage"),
            visual_summary.get("crosshair_coverage"),
            (
                1.0
                if isinstance(processed_rows, list)
                and processed_rows
                and isinstance(click_row_count, int)
                and not isinstance(click_row_count, bool)
                and click_row_count == len(processed_rows)
                else None
            ),
        ]

    return _build_family_result_v2(
        job,
        dynamic_result,
        visual_result,
        created_at=created_at,
        completed_at=completed_at,
        advice_fn=build_dynamic_clicking_candidate_advice,
        summary_type="dynamic_clicking",
        analysis_version=DYNAMIC_CLICKING_ANALYSIS_VERSION,
        plain_language_meaning=(
            "A matched prior Run differs on the referenced dynamic metric; "
            "mechanism and training guidance require the referenced knowledge entry."
        ),
        scenario_motion_class=dynamic_result.get("scenario_motion_class"),
        comparison_value=None,
        coverage_fn=coverage_fn,
        set_click_anchor_source=True,
        aim_family="dynamic_clicking",
        run_id_error_message="dynamic analysis requires kovaak_run_id",
    )


def _build_continuous_tracking_result_v2(
    job: dict,
    tracking_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from kovaak_tracker.advice_tracking import build_tracking_candidate_advice

    return _build_family_result_v2(
        job,
        tracking_result,
        visual_result,
        created_at=created_at,
        completed_at=completed_at,
        advice_fn=build_tracking_candidate_advice,
        summary_type="continuous_tracking",
        analysis_version=CONTINUOUS_TRACKING_ANALYSIS_VERSION,
        plain_language_meaning=(
            "A matched prior Run differs on the referenced tracking metric; "
            "mechanism and training guidance require the referenced knowledge entry."
        ),
        scenario_motion_class=tracking_result.get("scenario_motion_class"),
        comparison_value=tracking_result.get("comparison"),
        run_id_error_message="continuous tracking analysis requires kovaak_run_id",
    )


def _build_target_switching_result_v2(
    job: dict,
    switching_result: Mapping[str, object],
    visual_result: Mapping[str, object],
    *,
    created_at: str,
    completed_at: str,
) -> dict:
    from kovaak_tracker.advice_target_switching import (
        build_target_switching_candidate_advice,
    )

    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution") or {}
    target_motion = resolution.get("target_motion") or {}

    def coverage_fn(family_result, visual_summary):
        processed_rows = family_result.get("processed_rows")
        processed_tables = family_result.get("processed_event_tables")
        row_count = sum(
            int(table.get("row_count", 0))
            for table in processed_tables or []
            if isinstance(table, Mapping)
        )
        return [
            visual_summary.get("target_coverage"),
            visual_summary.get("crosshair_coverage"),
            (
                1.0
                if isinstance(processed_rows, list)
                and processed_rows
                and row_count == len(processed_rows)
                else None
            ),
        ]

    return _build_family_result_v2(
        job,
        switching_result,
        visual_result,
        created_at=created_at,
        completed_at=completed_at,
        advice_fn=build_target_switching_candidate_advice,
        summary_type="target_switching",
        analysis_version=TARGET_SWITCHING_ANALYSIS_VERSION,
        plain_language_meaning=(
            "A matched prior Run differs on the referenced target-switching "
            "metric; mechanism and training guidance require the referenced "
            "knowledge entry."
        ),
        scenario_motion_class=target_motion.get("model"),
        comparison_value=switching_result.get("comparison"),
        extra_comparable_requirements=["same metric condition"],
        coverage_fn=coverage_fn,
        run_id_error_message="target switching analysis requires kovaak_run_id",
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
    resolution = snapshot.get("scenario_resolution")
    active_static = (
        isinstance(resolution, Mapping)
        and resolution.get("manifest_status") == "active"
        and resolution.get("family_analyzer_dispatch") == "allowed"
        and resolution.get("aim_family") == "static_clicking"
        and NATIVE_ANALYSIS_VERSION in (resolution.get("allowed_analyzers") or [])
        and "static_clicking" in (resolution.get("allowed_metric_families") or [])
    )
    if active_static:
        deterministic["support_status"] = {
            "available": "supported",
            "partial": "partial",
            "limited": "partial",
        }.get(str(deterministic.get("status")), "unavailable")
    result_warnings = list(warnings or [])
    if not isinstance(snapshot.get("scenario_resolution"), dict):
        deterministic.setdefault("limitations", []).append(
            "legacy_static_compatibility"
        )
        result_warnings.append({"code": "legacy_static_compatibility"})
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    public_snapshot = public_analysis_input_snapshot(snapshot)
    calibration = native_result.get("calibration")
    if isinstance(calibration, Mapping):
        public_snapshot["calibration"] = dict(calibration)
    if input_mode == "input_native":
        public_snapshot.get("sources", {}).pop("video", None)
    elif video_availability is not None:
        video_source = dict(public_snapshot.get("sources", {}).get("video") or {})
        video_source.update({
            "artifact_ref": f"{analysis_id}:video",
            "availability": "available" if job.get("video_path") else "missing",
        })
        public_snapshot.setdefault("sources", {})["video"] = video_source
    result = build_analysis_result_v2(
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
        warnings=result_warnings,
        errors=[],
    )
    if active_static:
        result["scenario"] = {
            "scenario_profile_ref": resolution.get("scenario_profile_ref"),
            "aim_family": "static_clicking",
            "analyzer_refs": [NATIVE_ANALYSIS_VERSION],
            "support_status": deterministic["support_status"],
            "limitations": list(deterministic.get("limitations") or []),
        }
    return result


def _build_clicking_baseline_result_v2(
    job: dict,
    native_result: dict,
    *,
    created_at: str,
    completed_at: str,
    video_availability: str | None = None,
    warnings: list[dict] | None = None,
) -> dict:
    """Expose input-native movement facts for an auto-classified family task.

    This deliberately reuses the native movement computation but removes the
    static diagnosis layer. Target geometry and outcome association require the
    exact visual profile path and are not available here. A managed MP4 stays
    replayable: ``video_availability`` attaches the video evidence reference
    (replay only, no visual measurement) the same way the native tier does.
    """
    result = _build_native_result_v2(
        job,
        native_result,
        created_at=created_at,
        completed_at=completed_at,
        video_availability=video_availability,
    )
    snapshot = job.get("input_snapshot") or {}
    resolution = snapshot.get("scenario_resolution") or {}
    deterministic = result.get("deterministic") or {}
    limitations = list(dict.fromkeys([
        *(deterministic.get("limitations") or []),
        *(resolution.get("limitations") or []),
        f"{resolution.get('aim_family')}_baseline_without_exact_visual_profile",
    ]))
    deterministic["support_status"] = "partial"
    deterministic["limitations"] = limitations
    deterministic["diagnosis"] = {
        "profile": {},
        "issues": [],
        "summary": dict(deterministic.get("metrics") or {}),
        "comparison": None,
        "meta": {
            "summary_type": f"{resolution.get('aim_family')}_baseline",
            "classification": "deterministic",
        },
    }
    aim_family = resolution.get("aim_family")
    result["analysis_version"] = f"{aim_family}.baseline.v1"
    result["analysis_type"] = aim_family
    result["deterministic"] = deterministic
    result["scenario"] = {
        "scenario_profile_ref": None,
        "aim_family": aim_family,
        "analyzer_refs": [f"{aim_family}.baseline.v1"],
        "support_status": "partial",
        "limitations": limitations,
    }
    result["warnings"] = [
        *result.get("warnings", []),
        *(warnings or []),
        {"code": f"{aim_family}_baseline"},
    ]
    return result


_VIDEO_FALLBACK_SPARC_METRIC_VERSION = "flicking_fair_summary.sparc.v2"


def _extend_with_generic_visual(
    artifact: dict,
    result: dict,
    generic_visual_result: dict,
    *,
    job: dict,
    parsed_stats,
    native_result: dict | None,
) -> tuple[dict, dict]:
    """Associate generic visual tracks per family and upgrade the result.

    Returns the extended artifact and result when the quality gate passes;
    a failed gate returns the untouched artifact with an annotated result.
    """
    from kovaak_tracker.generic_static_clicking_analysis import (
        GENERIC_STATIC_CLICKING_ANALYSIS_VERSION,
        associate_generic_static_clicks_v1,
        extend_analysis_evidence_with_generic_static_clicking_v1,
        extract_left_click_rising_edges_v1,
    )

    snapshot = job.get("input_snapshot") or {}
    window = artifact["canonical_time_window"]
    window_start = int(window["start_ms"])
    window_end = int(window["end_ms"])
    analysis_ref = artifact["analysis_ref"]
    aim_family = (snapshot.get("scenario_resolution") or {}).get("aim_family")

    points = (
        ((native_result or {}).get("deterministic") or {}).get("trajectory") or {}
    ).get("points")
    click_times = (
        extract_left_click_rising_edges_v1(
            points, start_ms=window_start, end_ms=window_end,
        )
        if isinstance(points, list)
        else []
    )
    kill_records = []
    for record in artifact.get("normalized_outcome_records") or []:
        values = record.get("values") if isinstance(record, dict) else None
        if not isinstance(values, list):
            continue
        kill_index = next(
            (
                item.get("value")
                for item in values
                if isinstance(item, dict)
                and item.get("metric_key") == "stats.kill.kill_index"
            ),
            None,
        )
        if isinstance(kill_index, int) and not isinstance(kill_index, bool):
            kill_records.append({
                "canonical_time_ms": record["canonical_time_ms"],
                "kill_index": kill_index,
            })

    resolution = getattr(parsed_stats, "resolution", None)
    match = (
        re.fullmatch(r"\s*([1-9][0-9]*)\s*[xX]\s*([1-9][0-9]*)\s*", resolution)
        if isinstance(resolution, str)
        else None
    )
    viewport = (
        [int(match.group(1)), int(match.group(2))]
        if match
        else generic_visual_result.get("resolution")
    )
    deg_per_px = None
    fov = getattr(parsed_stats, "fov", None)
    if (
        match
        and isinstance(fov, (int, float))
        and not isinstance(fov, bool)
        and fov > 0
    ):
        deg_per_px = float(fov) / float(viewport[0])

    if aim_family == "static_clicking" or aim_family is None:
        association = associate_generic_static_clicks_v1(
            analysis_ref=analysis_ref,
            generic_visual_result=generic_visual_result,
            click_times_ms=click_times,
            kill_records=kill_records,
            viewport_size=viewport,
            deg_per_px=deg_per_px,
        )
        if not association["gate"]["passed"]:
            return (
                artifact,
                _annotate_result_generic_gate_failed(result, association),
            )
        video_source_ref = _video_source_ref(snapshot, analysis_ref)
        extended = extend_analysis_evidence_with_generic_static_clicking_v1(
            artifact,
            generic_visual_result,
            association,
            video_source_ref=video_source_ref,
        )
        return extended, _upgrade_result_with_generic_visual(
            result,
            association,
            version=GENERIC_STATIC_CLICKING_ANALYSIS_VERSION,
            aim_family="static_clicking",
            source_ref=video_source_ref,
        )

    from kovaak_tracker.generic_aim_family_analysis import (
        associate_generic_dynamic_clicks_v1,
        associate_generic_switching_v1,
        associate_generic_tracking_v1,
        extend_analysis_evidence_with_generic_family_v1,
        generic_family_analysis_version,
    )

    version = generic_family_analysis_version(aim_family)
    if version is None:
        raise ValueError(f"unsupported generic aim family: {aim_family}")
    if aim_family == "continuous_tracking":
        association = associate_generic_tracking_v1(
            analysis_ref=analysis_ref,
            generic_visual_result=generic_visual_result,
            canonical_time_window=window,
            viewport_size=viewport,
            deg_per_px=deg_per_px,
        )
    elif aim_family == "dynamic_clicking":
        association = associate_generic_dynamic_clicks_v1(
            analysis_ref=analysis_ref,
            generic_visual_result=generic_visual_result,
            click_times_ms=click_times,
            kill_records=kill_records,
            viewport_size=viewport,
            deg_per_px=deg_per_px,
        )
    else:
        association = associate_generic_switching_v1(
            analysis_ref=analysis_ref,
            generic_visual_result=generic_visual_result,
            click_times_ms=click_times,
            kill_records=kill_records,
            viewport_size=viewport,
            deg_per_px=deg_per_px,
        )
    if not association["gate"]["passed"]:
        return artifact, _annotate_result_generic_gate_failed(result, association)
    extended = extend_analysis_evidence_with_generic_family_v1(
        artifact,
        generic_visual_result,
        association,
        aim_family=aim_family,
        video_source_ref=_video_source_ref(snapshot, analysis_ref),
    )
    return extended, _upgrade_result_with_generic_visual(
        result,
        association,
        version=version,
        aim_family=aim_family,
        source_ref=_video_source_ref(snapshot, analysis_ref),
    )


def _video_source_ref(snapshot: dict, analysis_ref: str) -> str:
    video_source_ref = (
        (snapshot.get("sources") or {}).get("video") or {}
    ).get("artifact_ref")
    if not isinstance(video_source_ref, str) or not video_source_ref:
        video_source_ref = f"{analysis_ref}:video"
    return video_source_ref


def _generic_visual_summary_fields(association: Mapping[str, object]) -> dict:
    summary = {
        key: association[key]
        for key in (
            "click_count", "hit_count", "miss_count", "no_target_count",
            "kills_total", "kills_paired", "kill_pairing_rate", "coverage",
        )
        if key in association
    }
    summary["frame_coverage"] = association["gate"]["frame_coverage"]
    summary["gate_passed"] = association["gate"]["passed"]
    return summary


def _upgrade_result_with_generic_visual(
    result: dict,
    association: Mapping[str, object],
    *,
    version: str,
    aim_family: str,
    source_ref: str,
) -> dict:
    result = dict(result)
    deterministic = dict(result.get("deterministic") or {})
    limitations = [
        item
        for item in (deterministic.get("limitations") or [])
        if item != "target_relative_facts_unavailable"
    ]
    limitations.append("generic_visual_limited_validation")
    deterministic["limitations"] = limitations
    # 把 generic 视觉指标投到 metrics 面（Coach 的讲解读取面）：
    # metrics.json / overview.metrics_summary 由 deterministic.metrics 物化，
    # knowledge_refs 显式指向知识条目的 metric_refs，让条目匹配不靠猜。
    deterministic["metrics"] = {
        **(deterministic.get("metrics") or {}),
        **_generic_visual_metric_entries(association, aim_family, source_ref),
    }
    result["deterministic"] = deterministic
    result["analysis_version"] = version
    scenario = dict(result.get("scenario") or {})
    scenario["analyzer_refs"] = [version]
    scenario["limitations"] = list(limitations)
    result["scenario"] = scenario
    result["warnings"] = [
        *result.get("warnings", []),
        {"code": "generic_visual"},
    ]
    result["generic_visual_summary"] = _generic_visual_summary_fields(association)
    return result


# generic 指标 → 知识条目 metric_refs 的显式桥。只配语义核对过的映射；
# 未列出的指标照常投影数字，不带 knowledge_refs。miss_distance 挂
# normalized_click_error 依赖条目的 comparison_only 语义：同条件对比下
# 绝对角度与归一化形态等价（目标尺寸恒定）。static 家族不配——该家族
# 条目明确声明不含 target-relative 误差。
_GENERIC_METRIC_KNOWLEDGE_REFS: dict[str, list[str]] = {
    "tracking.generic.error_median_deg": ["metric:tracking_error"],
    "tracking.generic.error_p90_deg": ["metric:tracking_error"],
    "tracking.generic.in_target_ratio": ["metric:time_in_radius"],
    "tracking.generic.loss_count": ["metric:loss_count"],
    "switching.generic.transition_time_ms": [
        "metric:target_switching.transition_time_ms",
    ],
    "dynamic_clicking.generic.miss_distance_deg": [
        "metric:normalized_click_error",
    ],
    "switching.generic.miss_distance_deg": [
        "metric:normalized_click_error",
    ],
}


def _generic_visual_metric_entries(
    association: Mapping[str, object],
    aim_family: str,
    source_ref: str,
) -> dict[str, dict]:
    if aim_family == "static_clicking":
        from kovaak_tracker.generic_static_clicking_analysis import (
            build_generic_static_metric_records_v1,
        )

        records = build_generic_static_metric_records_v1(
            association, source_ref=source_ref,
        )
    else:
        from kovaak_tracker.generic_aim_family_analysis import (
            build_generic_family_metric_records_v1,
        )

        records = build_generic_family_metric_records_v1(
            association, aim_family=aim_family, source_ref=source_ref,
        )
    entries: dict[str, dict] = {}
    for record in records:
        key = record.get("metric_key")
        if not isinstance(key, str) or not key:
            continue
        # deterministic.metrics 的 v2 持久化合同要求九个必填字段
        # （key/value/unit/availability/provenance/metric_version/coverage/
        # classification/limitations），形态对齐 native 指标条目。
        entry = {
            "key": key,
            "value": record.get("value"),
            "unit": record.get("unit"),
            "availability": record.get("availability"),
            "provenance": {
                "kind": "measured",
                "sources": [source_ref],
            },
            "metric_version": record.get("metric_version"),
            "coverage": record.get("coverage"),
            "classification": record.get("classification"),
            "limitations": ["generic_visual_limited_validation"],
        }
        knowledge_refs = _GENERIC_METRIC_KNOWLEDGE_REFS.get(key)
        if knowledge_refs:
            entry["knowledge_refs"] = knowledge_refs
        entries[key] = entry
    return entries


def _annotate_result_generic_gate_failed(
    result: dict, association: Mapping[str, object],
) -> dict:
    result = dict(result)
    deterministic = dict(result.get("deterministic") or {})
    limitations = list(dict.fromkeys([
        *(deterministic.get("limitations") or []),
        "generic_visual_quality_below_threshold",
    ]))
    deterministic["limitations"] = limitations
    result["deterministic"] = deterministic
    scenario = dict(result.get("scenario") or {})
    scenario["limitations"] = list(limitations)
    result["scenario"] = scenario
    result["generic_visual_summary"] = {
        **_generic_visual_summary_fields(association),
        "gate_reasons": list(association["gate"]["reasons"]),
    }
    return result


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
    calibration = job.get("calibration_snapshot")
    if isinstance(calibration, Mapping):
        public_snapshot["calibration"] = dict(calibration)
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
        frozen_stats = None
        visual_result = None
        generic_visual_result = None
        dynamic_result = None
        tracking_result = None
        switching_result = None
        outcome_event_bundle = None
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )
        calibration_request = job.get("calibration_request")
        profile_default = (
            calibration_request.get("profile_default")
            if isinstance(calibration_request, Mapping) else None
        )
        manual_override = (
            calibration_request.get("manual_override")
            if isinstance(calibration_request, Mapping) else None
        )
        await queue.set_task_phase(sid, "aligning_input_events", worker_id=WORKER_ID)

        scenario_dispatch = _scenario_dispatch(job, input_mode)
        if scenario_dispatch == "outcome_only":
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                job.get("input_snapshot") or {},
            )
            _freeze_job_calibration(job, frozen_stats)
            result = _build_outcome_only_result_v2(
                job,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
            )
            cost = 0.0
        elif scenario_dispatch == DYNAMIC_CLICKING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result = await run_visual_preprocessing_isolated(job)
            except SourceSnapshotChangedError:
                raise
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="dynamic_clicking",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "dynamic_clicking"
                    in (quality.get("enabled_metric_families") or [])
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["dynamic_clicking_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "dynamic_clicking_analyzer_unavailable"}],
                        analysis_type_override="dynamic_clicking",
                    )
                else:
                    try:
                        outcome_event_bundle = await asyncio.to_thread(
                            _build_validated_outcome_association,
                            job,
                            frozen_stats,
                            visual_result,
                        )
                    except Exception as error:
                        log.warning(
                            "dynamic outcome association unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                    try:
                        dynamic_result = await asyncio.to_thread(
                            run_dynamic_clicking_analysis,
                            job,
                            visual_result,
                            outcome_event_bundle,
                        )
                    except SourceSnapshotChangedError:
                        raise
                    except Exception as error:
                        log.warning(
                            "dynamic clicking analysis unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                        result = _build_outcome_only_result_v2(
                            job,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                            limitations_override=["dynamic_clicking_analysis_unavailable"],
                            visual_validation=visual_validation,
                            extra_warnings=[{"code": "dynamic_clicking_analyzer_unavailable"}],
                            analysis_type_override="dynamic_clicking",
                        )
                    else:
                        result = _build_dynamic_result_v2(
                            job,
                            dynamic_result,
                            visual_result,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                        )
                        try:
                            from .history_trends import matched_dynamic_baseline_for_user

                            comparison = await matched_dynamic_baseline_for_user(
                                str(job["user_id"]),
                                result,
                                list(dynamic_result.get("metrics") or {}),
                            )
                        except Exception as error:
                            log.warning(
                                "dynamic baseline unavailable session=%s error=%s",
                                sid,
                                type(error).__name__,
                            )
                        else:
                            if comparison.get("comparable") is True:
                                dynamic_result = copy.deepcopy(dynamic_result)
                                dynamic_result["comparison"] = comparison
                                result = _build_dynamic_result_v2(
                                    job,
                                    dynamic_result,
                                    visual_result,
                                    created_at=created_at_iso,
                                    completed_at=completed_at_iso,
                                )
            cost = 0.0
        elif scenario_dispatch == CONTINUOUS_TRACKING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result, tracking_result = (
                    await run_continuous_tracking_pipeline_isolated(job)
                )
            except SourceSnapshotChangedError:
                raise
            except ContinuousTrackingAnalysisProcessError as error:
                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                visual_result = error.visual_result
                visual_validation = dict(visual_result.get("safe_summary") or {})
                log.warning(
                    "continuous tracking analysis unavailable session=%s error=%s",
                    sid,
                    error.code,
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=["continuous_tracking_analysis_unavailable"],
                    visual_validation=visual_validation,
                    extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                    analysis_type_override="continuous_tracking",
                )
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="continuous_tracking",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "tracking" in (quality.get("enabled_metric_families") or [])
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["continuous_tracking_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                        analysis_type_override="continuous_tracking",
                    )
                elif tracking_result is None:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["continuous_tracking_analysis_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "continuous_tracking_analyzer_unavailable"}],
                        analysis_type_override="continuous_tracking",
                    )
                else:
                    result = _build_continuous_tracking_result_v2(
                        job,
                        tracking_result,
                        visual_result,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                    )
                    try:
                        from .history_trends import matched_tracking_baseline_for_user

                        comparison = await matched_tracking_baseline_for_user(
                            str(job["user_id"]),
                            result,
                            list(tracking_result.get("metrics") or {}),
                        )
                    except Exception as error:
                        log.warning(
                            "continuous tracking baseline unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                    else:
                        if comparison.get("comparable") is True:
                            tracking_result = copy.deepcopy(tracking_result)
                            tracking_result["comparison"] = comparison
                            result = _build_continuous_tracking_result_v2(
                                job,
                                tracking_result,
                                visual_result,
                                created_at=created_at_iso,
                                completed_at=completed_at_iso,
                            )
            cost = 0.0
        elif scenario_dispatch == TARGET_SWITCHING_ANALYSIS_VERSION:
            await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
            snapshot = job.get("input_snapshot") or {}
            frozen_stats = await asyncio.to_thread(
                _parse_frozen_stats_for_visual,
                snapshot,
            )
            _freeze_job_calibration(job, frozen_stats)
            try:
                visual_result, episode_result = (
                    await run_target_switching_pipeline_isolated(job)
                )
            except SourceSnapshotChangedError:
                raise
            except Exception as error:
                from kovaak_tracker.visual_signals import (
                    VisualPreprocessingUnavailable,
                )

                await asyncio.to_thread(
                    _assert_managed_video_matches_snapshot,
                    job,
                    input_mode,
                )
                limitation = (
                    error.code
                    if isinstance(error, VisualPreprocessingUnavailable)
                    else "visual_preprocessing_failed"
                )
                result = _build_outcome_only_result_v2(
                    job,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    limitations_override=[limitation],
                    visual_validation=_unavailable_visual_summary(limitation),
                    extra_warnings=[{"code": "video_cv_unavailable"}],
                    analysis_type_override="target_switching",
                )
            else:
                visual_validation = dict(visual_result.get("safe_summary") or {})
                quality = visual_result.get("quality")
                enabled_families = (
                    set(quality.get("enabled_metric_families") or [])
                    if isinstance(quality, Mapping)
                    else set()
                )
                quality_enabled = (
                    isinstance(quality, Mapping)
                    and quality.get("status") in {"accepted", "limited"}
                    and "target_switching" in enabled_families
                )
                if not quality_enabled:
                    result = _build_outcome_only_result_v2(
                        job,
                        created_at=created_at_iso,
                        completed_at=completed_at_iso,
                        limitations_override=["target_switching_visual_quality_unavailable"],
                        visual_validation=visual_validation,
                        extra_warnings=[{"code": "target_switching_analyzer_unavailable"}],
                        analysis_type_override="target_switching",
                    )
                else:
                    try:
                        switching_result = await asyncio.to_thread(
                            run_target_switching_analysis,
                            job,
                            visual_result,
                            episode_result,
                            frozen_stats,
                        )
                    except SourceSnapshotChangedError:
                        raise
                    except Exception as error:
                        log.warning(
                            "target switching analysis unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                        result = _build_outcome_only_result_v2(
                            job,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                            limitations_override=["target_switching_analysis_unavailable"],
                            visual_validation=visual_validation,
                            extra_warnings=[{"code": "target_switching_analyzer_unavailable"}],
                            analysis_type_override="target_switching",
                        )
                    else:
                        result = _build_target_switching_result_v2(
                            job,
                            switching_result,
                            visual_result,
                            created_at=created_at_iso,
                            completed_at=completed_at_iso,
                        )
                        try:
                            from .history_trends import (
                                matched_target_switching_baseline_for_user,
                            )

                            comparison = await matched_target_switching_baseline_for_user(
                                str(job["user_id"]),
                                result,
                                list(switching_result.get("metrics") or {}),
                            )
                        except Exception as error:
                            log.warning(
                                "target switching baseline unavailable session=%s error=%s",
                                sid,
                                type(error).__name__,
                            )
                        else:
                            if comparison.get("comparable") is True:
                                switching_result = copy.deepcopy(switching_result)
                                switching_result["comparison"] = comparison
                                result = _build_target_switching_result_v2(
                                    job,
                                    switching_result,
                                    visual_result,
                                    created_at=created_at_iso,
                                    completed_at=completed_at_iso,
                                )
            cost = 0.0
        elif input_mode in {"input_native", "multimodal"}:
            await queue.set_task_phase(sid, "computing_kinematics", worker_id=WORKER_ID)
            if input_mode == "multimodal":
                native_result, frozen_stats = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                    return_parsed_stats=True,
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
            else:
                native_result = await asyncio.to_thread(
                    run_native_analysis,
                    job.get("input_snapshot") or {},
                    job.get("cm_per_360"),
                    job.get("fov"),
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
                frozen_stats = None
            if isinstance(native_result, Mapping) and isinstance(
                native_result.get("calibration"), Mapping
            ):
                job["calibration_snapshot"] = dict(native_result["calibration"])
                snapshot = job.get("input_snapshot")
                if isinstance(snapshot, dict):
                    snapshot["calibration"] = dict(native_result["calibration"])
            video_availability = None
            warnings: list[dict] = []
            visual_validation = None
            if (
                input_mode == "multimodal"
                and scenario_dispatch not in _FAMILY_BASELINE_ANALYSIS_VERSIONS
            ):
                await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
                snapshot = job.get("input_snapshot") or {}
                if snapshot.get("schema_version") in {
                    "analysis_input_snapshot.v2", "analysis_input_snapshot.v3",
                }:
                    video_availability = "available"
                    try:
                        visual_result = await run_visual_preprocessing_isolated(job)
                        visual_validation = visual_result["safe_summary"]
                    except Exception as error:
                        from kovaak_tracker.visual_signals import (
                            VisualPreprocessingUnavailable,
                        )

                        await asyncio.to_thread(
                            _assert_managed_video_matches_snapshot,
                            job,
                            input_mode,
                        )
                        if isinstance(error, VisualPreprocessingUnavailable):
                            limitation = error.code
                        else:
                            limitation = "visual_preprocessing_failed"
                        visual_validation = _unavailable_visual_summary(limitation)
                        warnings.append({"code": "video_cv_unavailable"})
                        log.warning(
                            "multimodal visual preprocessing unavailable session=%s error=%s",
                            sid,
                            type(error).__name__,
                        )
                else:
                    try:
                        stats_path = (snapshot.get("sources") or {}).get(
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
            generic_visual_result = None
            # static_clicking 的正牌 dispatch（native_flicking.v1）也纳入 generic：
            # 该家族的 exact 视觉档案场景不存在，reviewed 路必失败，没有
            # generic 兜底的话所有 static 场景都只有输入端运动学。
            if (
                input_mode == "multimodal"
                and (
                    scenario_dispatch in _FAMILY_BASELINE_ANALYSIS_VERSIONS
                    or scenario_dispatch == NATIVE_ANALYSIS_VERSION
                )
                and job.get("video_path")
            ):
                await queue.set_task_phase(sid, "analyzing_video", worker_id=WORKER_ID)
                try:
                    generic_visual_result = await run_generic_static_clicking_isolated(job)
                except Exception as error:
                    log.warning(
                        "generic static clicking unavailable session=%s error=%s",
                        sid,
                        type(error).__name__,
                    )
                    warnings.append({"code": "generic_visual_unavailable"})
            if scenario_dispatch in _FAMILY_BASELINE_ANALYSIS_VERSIONS:
                # The baseline tier consumes no visual measurement, but the
                # managed MP4 stays replayable: attach the video evidence
                # reference when the workspace video exists.
                baseline_video_availability = (
                    ("available" if job.get("video_path") else "unavailable")
                    if input_mode == "multimodal"
                    else None
                )
                result = _build_clicking_baseline_result_v2(
                    job,
                    native_result,
                    created_at=created_at_iso,
                    completed_at=completed_at_iso,
                    video_availability=baseline_video_availability,
                    warnings=warnings,
                )
            else:
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
            await queue.set_task_phase(sid, "computing_kinematics", worker_id=WORKER_ID)
            try:
                summary, extras = await asyncio.to_thread(
                    run_analysis,
                    job["video_path"],
                    job["csv_path"],
                    job.get("cm_per_360"),
                    job.get("fov"),
                    profile_default=profile_default,
                    manual_override=manual_override,
                )
                if isinstance(extras, Mapping) and isinstance(
                    extras.get("calibration"), Mapping
                ):
                    job["calibration_snapshot"] = dict(extras["calibration"])
                    snapshot = job.get("input_snapshot")
                    if isinstance(snapshot, dict):
                        snapshot["calibration"] = dict(extras["calibration"])
                else:
                    job["calibration_snapshot"] = resolve_calibration_v1(
                        stats=None,
                        manual_override=_manual_override_or_legacy(
                            manual_override,
                            cm_per_360=job.get("cm_per_360"),
                            fov=job.get("fov"),
                        ),
                        profile_default=profile_default,
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
            report_dict = await asyncio.to_thread(run_report, summary)
            cost = 0.0

            result = _build_video_fallback_result_v2(
                job,
                summary,
                report_dict,
                timeline_events,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                narration_status="not_requested",
            )
        await asyncio.to_thread(
            _assert_managed_video_matches_snapshot,
            job,
            input_mode,
        )
        await queue.set_task_phase(sid, "generating_diagnostics", worker_id=WORKER_ID)
        if (
            scenario_dispatch == CONTINUOUS_TRACKING_ANALYSIS_VERSION
            and isinstance(visual_result, dict)
            and isinstance(tracking_result, dict)
        ):
            result = await commit_continuous_tracking_evidence_isolated(
                job,
                result,
                visual_result,
                tracking_result,
            )
        else:
            result = await asyncio.to_thread(
                _maybe_commit_analysis_evidence,
                job,
                result,
                parsed_stats=frozen_stats,
                native_result=(native_result if "native_result" in locals() else None),
                visual_result=visual_result,
                dynamic_result=dynamic_result,
                tracking_result=tracking_result,
                switching_result=switching_result,
                outcome_event_bundle=outcome_event_bundle,
                generic_visual_result=generic_visual_result,
            )
        # Carry the capture receipt preroll so downstream video-relative
        # times (overview anchors, frontend playback seeks) can correct
        # for the decode preroll between MP4 PTS 0 and the canonical window.
        preroll_ms = video_decode_preroll_ms(job)
        if preroll_ms:
            result["video_decode_preroll_ms"] = preroll_ms
        marked_done = await queue.mark_done(sid, result, cost, worker_id=WORKER_ID)
        if not marked_done:
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        else:
            try:
                await _record_profile_contribution(job, result)
            except Exception as error:
                log.warning(
                    "aiming profile contribution unavailable session=%s error=%s",
                    sid,
                    type(error).__name__,
                )
            try:
                snapshot_for_output = job.get("input_snapshot") or {}
                stats_source_path = (
                    (snapshot_for_output.get("sources") or {}).get("stats") or {}
                ).get("path")
                await asyncio.to_thread(
                    analysis_output.write_progressive_disclosure,
                    str(DATA_ROOT),
                    sid,
                    result,
                    stats_source_path,
                )
            except Exception:
                log.warning(
                    "progressive disclosure output failed session=%s",
                    sid,
                    exc_info=True,
                )
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
        await queue.set_failure_domain(sid, "source_file")
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
        domain = "video" if input_mode == "multimodal" else "kinematics"
        await queue.set_failure_domain(sid, domain)
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
