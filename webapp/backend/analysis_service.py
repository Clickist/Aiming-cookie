"""Owner-aware Analysis/History product commands.

After the 2026-08-13 architecture rewrite, Coach reads analysis files directly
via the Node sidecar. These Python functions remain for the desktop Analysis /
History UI routes that still proxy through the backend:

- ``/api/kovaak-runs`` and ``/api/kovaak-runs/{id}`` (run index / detail)
- ``/api/kovaak-runs/{id}/analyze`` (freeze + enqueue one run analysis)
- ``/api/sessions`` and ``/api/sessions/{id}/retry`` (analysis list / retry)

This module deliberately contains no FastAPI types and no Coach proxy imports.
It is the extracted live subset of the old ``coach_commands`` module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

from . import history_trends, kovaak_run_store, queue
from .contracts import SCENARIO_OUTCOME_ONLY_VERSION
from .kovaak_run_projection import public_kovaak_run
from .source_requirements import validate_source_requirements
from .workspace import copy_path_to_path, remove_session_workspace, session_dir

RESULT_SCHEMA_VERSION = "coach_product_command_result.v1"


class ProductCommandError(Exception):
    """A stable application error that HTTP can map without importing FastAPI."""

    def __init__(
        self, code: str, message: str, *, kind: str = "failed", result_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind
        self.result_ref = result_ref


def _safe_run(run: dict[str, Any]) -> dict[str, Any]:
    return public_kovaak_run(run)


def _safe_analysis(session: dict[str, Any]) -> dict[str, Any]:
    error = session.get("error")
    safe_error = None
    if isinstance(error, dict):
        safe_error = {
            key: error[key]
            for key in ("schema_version", "category", "code", "message", "retryable")
            if key in error
        }
    return {
        "analysis_ref": f"analysis:{session['id']}",
        "id": session["id"],
        "status": session.get("status"),
        "analysis_type": session.get("analysis_type", "flicking"),
        "input_mode": session.get("input_mode", "video_fallback"),
        "run_ref": f"run:{session['kovaak_run_id']}" if session.get("kovaak_run_id") else None,
        "created_at": session.get("created_at"),
        "started_at": session.get("started_at"),
        "finished_at": session.get("finished_at"),
        "error": safe_error,
    }


async def list_runs(owner_id: str) -> list[dict[str, Any]]:
    return await kovaak_run_store.list_kovaak_run_summaries(owner_id)


async def get_run(owner_id: str, run_id: int) -> dict[str, Any]:
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    return _public_run_projection(run, owner_id)


def _public_run_projection(run: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Project one run with its live analysis count (matches list summaries)."""
    projected = dict(run)
    projected["analysis_count"] = kovaak_run_store._get_analysis_count_for_run(
        int(run["id"]), owner_id,
    )
    return _safe_run(projected)


async def list_history(owner_id: str) -> list[dict[str, Any]]:
    return await queue.list_sessions(owner_id)


async def history_trend(owner_id: str, metric_key: str) -> dict[str, Any]:
    if not isinstance(metric_key, str) or not metric_key or len(metric_key) > 128:
        raise ProductCommandError("invalid_metric_key", "metric_key is required")
    return await history_trends.recent_trend_for_user(owner_id, metric_key)


async def get_analysis(owner_id: str, analysis_id: int) -> dict[str, Any]:
    session = await queue.get_session(analysis_id)
    if session is None:
        raise ProductCommandError("not_found", "Analysis 不存在", kind="unavailable")
    if session.get("user_id") != owner_id:
        raise ProductCommandError("forbidden", "无权访问此 Analysis")
    return _safe_analysis(session)


def _challenge_button_samples_held(
    snapshot: Mapping[str, Any],
) -> int | None:
    """Count canonical in-window Raw samples with the fire button held.

    The button field is a per-millisecond state bitmask (bit 0 = fire), so a
    sustained hold emits one held sample per ms — exactly the fire-mode signal
    the challenge-shape classifier reads. Any decode/read failure means "no
    Raw signal", never a failed analysis.
    """
    trace = snapshot.get("trace")
    window = snapshot.get("canonical_time_window")
    if not isinstance(trace, Mapping) or not isinstance(window, Mapping):
        return None
    path = trace.get("path")
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        not isinstance(path, str)
        or not isinstance(start_ms, int) or isinstance(start_ms, bool)
        or not isinstance(end_ms, int) or isinstance(end_ms, bool)
        or end_ms <= start_ms
    ):
        return None
    from .kovaak_snapshot_codec import decode_mouse_snapshot_bytes

    try:
        points = decode_mouse_snapshot_bytes(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    held = 0
    for point in points:
        if start_ms <= point["timestamp_ms"] < end_ms and point["buttons"] & 1:
            held += 1
    return held


def _challenge_shape_for_run(
    run: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Bounded, path-free shape facts: Stats kills, challenge duration and,
    when the run has a Raw trace, the in-window held-button sample count."""
    stats_summary = run.get("stats_summary")
    kills = stats_summary.get("kill_count") if isinstance(stats_summary, Mapping) else None
    window = snapshot.get("canonical_time_window")
    duration_ms = window.get("duration_ms") if isinstance(window, Mapping) else None
    if (
        not isinstance(kills, int) or isinstance(kills, bool) or kills < 0
        or not isinstance(duration_ms, int) or isinstance(duration_ms, bool)
        or duration_ms <= 0
    ):
        return None
    shape: dict[str, Any] = {
        "schema_version": "scenario_challenge_shape.v1",
        "kills": kills,
        "duration_ms": duration_ms,
    }
    button_samples_held = _challenge_button_samples_held(snapshot)
    if button_samples_held is not None:
        shape["button_samples_held"] = button_samples_held
    return shape


SCENARIO_OVERRIDES_SCHEMA_VERSION = "scenario_overrides.v1"
SCENARIO_OVERRIDES_MAX_ENTRIES = 5000
SCENARIO_OVERRIDES_MAX_BYTES = 1024 * 1024
_SCENARIO_OVERRIDE_HASH_RE = re.compile(r"[0-9a-f]{32}")
_SCENARIO_OVERRIDE_FAMILIES = {
    "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
}


def _load_scenario_overrides() -> dict[str, dict[str, Any]]:
    """Read user-confirmed scenario family memories from the app-data config dir.

    The file is written by the Coach sidecar (``scenario_memory.set``) after the
    user confirms a scenario's aim family once. Malformed entries are skipped
    individually; a wrong top-level shape means "no memory", never a failed
    analysis.
    """
    from . import config

    path = config.DATA_ROOT / "config" / "scenario-overrides.json"
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return {}
    if len(raw_bytes) > SCENARIO_OVERRIDES_MAX_BYTES:
        return {}
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    entries = raw.get("overrides") if isinstance(raw, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != SCENARIO_OVERRIDES_SCHEMA_VERSION
        or not isinstance(entries, Mapping)
        or len(entries) > SCENARIO_OVERRIDES_MAX_ENTRIES
    ):
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for scenario_hash, entry in entries.items():
        note = entry.get("note") if isinstance(entry, Mapping) else None
        if (
            not isinstance(scenario_hash, str)
            or not _SCENARIO_OVERRIDE_HASH_RE.fullmatch(scenario_hash)
            or not isinstance(entry, Mapping)
            or set(entry) - {"aim_family", "confirmed_by", "note", "updated_at"}
            or entry.get("aim_family") not in _SCENARIO_OVERRIDE_FAMILIES
            or (
                note is not None
                and (
                    not isinstance(note, str)
                    or len(note) > 200
                    or any(ord(char) < 32 for char in note)
                )
            )
        ):
            continue
        overrides[scenario_hash] = dict(entry)
    return overrides


def _apply_scenario_override_resolution(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply the user-confirmed family memory above the heuristic chain.

    Exact reviewed hashes keep priority (a resolution with a profile ref is
    never replaced); the override beats the `.sce`, challenge-shape and name
    layers. It routes the confirmed family baseline pipeline — confidence
    confirmed, descriptive claims only — and never establishes scenario
    identity or visual claims.
    """
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping) or resolution.get("scenario_profile_ref"):
        return snapshot
    scenario_hash = resolution.get("scenario_hash")
    if not isinstance(scenario_hash, str):
        return snapshot
    override = _load_scenario_overrides().get(scenario_hash)
    if override is None:
        return snapshot
    from kovaak_tracker.scenario_profiles import (
        _FAMILY_BASELINE_LIMITATIONS,
        _family_baseline_resolution,
    )

    next_resolution = _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=resolution.get("display_name"),
        registry_version=resolution["registry_version"],
        manifest_version=resolution["manifest_version"],
        aim_family=override["aim_family"],
        classification_source="scenario_override",
        classification_confidence="confirmed",
        target_motion={"model": "unknown", "target_count_model": "unknown"},
        limitations=[
            "scenario_override_is_a_user_confirmed_family_not_an_identity",
            *_FAMILY_BASELINE_LIMITATIONS,
        ],
    )
    next_snapshot = dict(snapshot)
    next_snapshot["scenario_resolution"] = next_resolution
    return next_snapshot


def _apply_challenge_shape_resolution(
    run: Mapping[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Let the Stats-derived challenge shape refine name/default identifications.

    Exact hashes and `.sce` structure keep priority: the shape layer only
    replaces name-keyword or unresolved-default resolutions, and only when it
    reaches a verdict (the middle kill-density band stays undecided).
    """
    resolution = snapshot.get("scenario_resolution")
    if (
        not isinstance(resolution, Mapping)
        or resolution.get("classification_source")
        not in {"name_heuristic", "family_default"}
    ):
        return snapshot
    shape = _challenge_shape_for_run(run, snapshot)
    if shape is None:
        return snapshot
    from kovaak_tracker.scenario_profiles import resolve_scenario_profile

    scenario_hash = resolution.get("scenario_hash")
    scenario = snapshot.get("scenario")
    refined = resolve_scenario_profile(
        scenario_hash if isinstance(scenario_hash, str) else None,
        scenario if isinstance(scenario, str) else None,
        challenge_shape=shape,
    )
    if refined.get("classification_source") != "challenge_shape":
        return snapshot
    next_snapshot = dict(snapshot)
    next_snapshot["scenario_challenge_shape"] = shape
    next_snapshot["scenario_resolution"] = refined
    return next_snapshot


async def _run_may_be_reclassified(owner_id: str, run_id: int) -> bool:
    """True when the user-confirmed scenario memory may reclassify a done Run.

    Cheap pre-check that keeps the done-analysis reuse fast path untouched:
    only a run whose Performance scenario hash has an override entry needs the
    fresh snapshot comparison; every other repeat call stays a pure reuse.
    """
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        return False
    performance_summary = run.get("performance_summary")
    header = (
        performance_summary.get("header")
        if isinstance(performance_summary, Mapping)
        else None
    )
    scenario_hash = header.get("scenario_hash") if isinstance(header, Mapping) else None
    return (
        isinstance(scenario_hash, str)
        and scenario_hash in _load_scenario_overrides()
    )


def _analysis_type_for_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Keep the persisted request type aligned with the identified dispatch family."""
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping):
        return "flicking"
    family = resolution.get("aim_family")
    return {
        "dynamic_clicking": "dynamic_clicking",
        "continuous_tracking": "continuous_tracking",
        "target_switching": "target_switching",
        "static_clicking": (
            "static_clicking"
            if "static_clicking.baseline.v1"
            in (resolution.get("allowed_analyzers") or [])
            else "flicking"
        ),
    }.get(family, "input_kinematics")


def _matches_frozen_copy(
    path: Path,
    fingerprint: object,
    *,
    source: Path | None = None,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    expected_mtime_ns = fingerprint.get("mtime_ns")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        return False
    if source is not None:
        try:
            source_stat = source.stat()
        except OSError:
            return False
        if (
            source_stat.st_size != expected_size
            or source_stat.st_mtime_ns != expected_mtime_ns
        ):
            return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha


def _matches_frozen_hard_link(
    path: Path,
    source: Path,
    fingerprint: object,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
    ):
        return False
    try:
        if path.is_symlink() or not path.is_file() or not path.samefile(source):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha
    except OSError:
        return False


def _freeze_video_source(path: Path) -> dict[str, object]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ProductCommandError(
            "source_unavailable",
            "Video source unavailable",
            kind="unavailable",
        ) from exc
    before_revision = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_revision = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_revision != after_revision:
        raise ProductCommandError(
            "source_unavailable",
            "Video source revision changed while freezing",
            kind="unavailable",
        )
    return {
        "sha256": digest.hexdigest(),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


async def create_analysis_from_run(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["multimodal"] = "multimodal",
    allow_parallel: bool = False,
    cm_per_360: float | None = None,
    fov: float | None = None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    managed_video_fingerprint: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Freeze a Run and enqueue its highest valid automatic evidence tier.

    ``input_mode`` remains an internal compatibility argument for older callers.
    New Run Analysis never trusts it: the frozen snapshot is the sole source
    of the selected tier.
    """
    existing = await queue.get_run_analysis_states(owner_id, run_id)
    # A done analysis is the reusable answer for this Run unless the
    # user-confirmed scenario memory may have reclassified it since. An
    # outcome-only degradation is never the answer: a code upgrade can change
    # what the Run should dispatch to, so those rebuild instead of pinning
    # the empty pre-upgrade result.
    completed = next(
        (
            item for item in existing
            if item.get("status") == "done"
            and item.get("analysis_version") != SCENARIO_OUTCOME_ONLY_VERSION
        ),
        None,
    )
    reclassified = (
        completed is not None and await _run_may_be_reclassified(owner_id, run_id)
    )
    if completed is not None and not reclassified:
        session_id = int(completed["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    run_active = next(
        (item for item in existing if item.get("status") in {"uploading", "queued", "running"}),
        None,
    )
    if run_active is not None:
        session_id = int(run_active["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    active = await queue.get_active_session(owner_id)
    if active is not None and not allow_parallel:
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    try:
        snapshot = await kovaak_run_store.build_analysis_input_snapshot(run_id, owner_id)
    except (LookupError, ValueError) as exc:
        raise ProductCommandError("input_unavailable", str(exc), kind="unavailable") from exc
    snapshot = _apply_scenario_override_resolution(snapshot)
    snapshot = _apply_challenge_shape_resolution(run, snapshot)
    if reclassified:
        # The override leaves the done analysis stale only when it changes the
        # dispatch: a same-family confirmation (or an exact reviewed hash
        # keeping priority) keeps the done analysis as this Run's answer.
        completed_session = await queue.get_session(int(completed["id"]))
        if (
            completed_session is not None
            and completed_session.get("analysis_type")
            == _analysis_type_for_snapshot(snapshot)
        ):
            session_id = int(completed["id"])
            return {
                "session_id": session_id,
                "analysis_ref": f"analysis:{session_id}",
                "reused": True,
            }

    run_video = snapshot["sources"].get("video")
    run_video_source = None
    if managed_video_source is None and isinstance(run_video, Mapping):
        run_video_path = run_video.get("path")
        run_video_fingerprint = run_video.get("fingerprint")
        if (
            isinstance(run_video_path, str)
            and Path(run_video_path).is_file()
            and isinstance(run_video_fingerprint, Mapping)
        ):
            run_video_source = Path(run_video_path)
    video_fingerprint = None
    if managed_video_source is not None:
        video_fingerprint = (
            dict(managed_video_fingerprint)
            if isinstance(managed_video_fingerprint, Mapping)
            else _freeze_video_source(managed_video_source)
        )
        run_video_fingerprint = (
            run_video.get("fingerprint") if isinstance(run_video, Mapping) else None
        )
        preserves_run_identity = (
            isinstance(run_video, Mapping)
            and isinstance(run_video_fingerprint, Mapping)
            and run_video_fingerprint.get("sha256") == video_fingerprint.get("sha256")
            and run_video_fingerprint.get("size") == video_fingerprint.get("size")
        )
        snapshot["sources"]["video"] = {
            **(dict(run_video) if preserves_run_identity else {}),
            "basename": managed_video_source.name,
            "fingerprint": video_fingerprint,
            "path": str(managed_video_source.resolve()),
            "availability": "available",
            "format_version": "mp4",
        }
    elif run_video_source is not None and isinstance(run_video, Mapping):
        video_fingerprint = dict(run_video["fingerprint"])

    source_gate = validate_source_requirements(snapshot)
    if not source_gate["ready"]:
        missing = ", ".join(str(item) for item in source_gate["missing"])
        raise ProductCommandError(
            "input_unavailable",
            f"required Run sources are unavailable: {missing}",
            kind="unavailable",
        )
    selected_mode = source_gate["selected_mode"]
    if not isinstance(selected_mode, str):  # guarded by ready; keeps the queue contract strict
        raise ProductCommandError("input_unavailable", "Run has no supported analysis tier", kind="unavailable")
    snapshot["source_requirements_version"] = "automatic_quality_tier.v1"

    try:
        session_id = await queue.enqueue(
            owner_id,
            "",
            "",
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default=dict(profile_default) if isinstance(profile_default, Mapping) else None,
            manual_override=dict(manual_override) if isinstance(manual_override, Mapping) else None,
            analysis_type=_analysis_type_for_snapshot(snapshot),
            input_mode=selected_mode,
            kovaak_run_id=run_id,
            input_snapshot=snapshot,
            status="uploading",
            require_no_active=not allow_parallel,
            video_receipt=run.get("video_receipt"),
        )
    except queue.ActiveSessionExists as exc:
        active = await queue.get_active_session(owner_id)
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}" if active is not None else None,
        ) from exc
    try:
        managed_video = ""
        managed_csv = ""
        workspace = session_dir(session_id)
        uses_video = selected_mode in {"multimodal", "video_fallback"}
        if uses_video and managed_video_source is not None:
            video_destination = workspace / "video.mp4"
            try:
                copy_path_to_path(managed_video_source, video_destination)
            except OSError as exc:
                try:
                    observed_fingerprint = _freeze_video_source(managed_video_source)
                except ProductCommandError as source_exc:
                    raise source_exc from exc
                if observed_fingerprint != video_fingerprint:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Video source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not _matches_frozen_copy(
                video_destination,
                video_fingerprint,
                source=managed_video_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Video source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_video = str(video_destination)
        elif uses_video and run_video_source is not None:
            video_destination = workspace / "video.mp4"
            workspace.mkdir(parents=True, exist_ok=True)
            # Re-analysis of an already-analysed run may retry into a workspace that
            # still holds a prior (partial) freeze; os.link is not idempotent. Reuse an
            # existing matching hard link instead of failing with FileExistsError.
            if not (
                video_destination.exists()
                and _matches_frozen_hard_link(
                    video_destination, run_video_source, run_video_fingerprint,
                )
            ):
                if video_destination.exists():
                    video_destination.unlink()
                os.link(run_video_source, video_destination)
            if not _matches_frozen_hard_link(
                video_destination,
                run_video_source,
                run_video_fingerprint,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Run video revision changed before managed link",
                    kind="unavailable",
                )
            managed_video = str(video_destination)
        if selected_mode == "video_fallback":
            run_stats = snapshot["sources"].get("stats")
            stats_path = run_stats.get("path") if isinstance(run_stats, Mapping) else None
            stats_fingerprint = (
                run_stats.get("fingerprint") if isinstance(run_stats, Mapping) else None
            )
            if not isinstance(stats_path, str) or not isinstance(stats_fingerprint, Mapping):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source identity is unavailable",
                    kind="unavailable",
                )
            stats_source = Path(stats_path)
            stats_destination = workspace / "stats.csv"
            try:
                copy_path_to_path(stats_source, stats_destination)
            except OSError as exc:
                try:
                    source_matches = _matches_frozen_copy(stats_source, stats_fingerprint)
                except OSError:
                    source_matches = False
                if not source_matches:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Stats source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not _matches_frozen_copy(
                stats_destination,
                stats_fingerprint,
                source=stats_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_csv = str(stats_destination)
        await queue.set_session_input_paths(session_id, owner_id, managed_video, managed_csv)
        if not await queue.finish_upload(session_id):
            raise ProductCommandError("upload_state_lost", "分析输入状态已失效，请重新提交", kind="unavailable")
    except ProductCommandError:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        raise
    except Exception as exc:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        log.exception("input snapshot setup failed run_id=%s session_id=%s", run_id, session_id)
        raise ProductCommandError(
            "input_setup_failed",
            "无法建立分析输入快照",
        ) from exc
    return {
        "session_id": session_id,
        "analysis_ref": f"analysis:{session_id}",
        "input_mode": selected_mode,
        "limitations": [
            item for item in source_gate["missing"]
            if isinstance(item, str)
        ],
    }


async def retry_analysis(owner_id: str, analysis_id: int) -> dict[str, Any]:
    await get_analysis(owner_id, analysis_id)
    active = await queue.get_active_session(owner_id)
    if active is not None and int(active["id"]) != analysis_id:
        raise ProductCommandError(
            "active_analysis",
            "已有其它 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    try:
        updated = await queue.requeue_for_retry(analysis_id)
    except queue.RetryNotAllowed as exc:
        kind = "unavailable" if exc.code in {
            "active_analysis", "not_found", "missing_video", "missing_csv", "missing_snapshot",
        } else "failed"
        raise ProductCommandError(exc.code, exc.message, kind=kind) from exc
    return _safe_analysis(updated)


def _failure_result(
    command_id: str,
    code: str,
    message: str,
    *,
    kind: str = "failed",
    result_ref: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "unavailable" if kind == "unavailable" else "failed",
        "warning_or_error": {"code": code, "message": message},
    }
    if result_ref is not None:
        out["result_ref"] = result_ref
    return out


async def execute_trusted_analysis_create(
    owner_id: str,
    run_id: int,
    *,
    cm_per_360: float | None,
    fov: float | None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    idempotency_key: str | None = None,
    allow_parallel: bool = False,
) -> dict[str, Any]:
    """Execute the validated desktop Analysis write and return the canonical result."""
    command_id = "analysis.create_from_run"
    if not isinstance(owner_id, str) or not owner_id.strip():
        return _failure_result(command_id, "invalid_owner", "owner is required")

    video_fingerprint = None
    if managed_video_source is not None:
        try:
            video_fingerprint = _freeze_video_source(managed_video_source)
        except ProductCommandError as exc:
            return _failure_result(command_id, exc.code, exc.message, kind=exc.kind)
    try:
        created = await create_analysis_from_run(
            owner_id,
            run_id,
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default=profile_default,
            manual_override=manual_override,
            managed_video_source=managed_video_source,
            managed_video_fingerprint=video_fingerprint,
            allow_parallel=allow_parallel,
        )
    except ProductCommandError as exc:
        return _failure_result(
            command_id,
            exc.code,
            exc.message,
            kind=exc.kind,
            result_ref=exc.result_ref,
        )
    analysis_ref = created.get("analysis_ref") or f"analysis:{created['session_id']}"
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "succeeded",
        "result_ref": analysis_ref,
        "result": {**created, "analysis_ref": analysis_ref},
    }


async def execute_analysis_retry(
    owner_id: str,
    analysis_id: int,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Re-enqueue a failed Analysis and return the canonical product result."""
    command_id = "analysis.retry"
    if not isinstance(owner_id, str) or not owner_id.strip():
        return _failure_result(command_id, "invalid_owner", "owner is required")
    try:
        retried = await retry_analysis(owner_id, analysis_id)
    except ProductCommandError as exc:
        return _failure_result(
            command_id,
            exc.code,
            exc.message,
            kind=exc.kind,
            result_ref=exc.result_ref,
        )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "succeeded",
        "result_ref": retried.get("analysis_ref"),
        "result": retried,
    }
