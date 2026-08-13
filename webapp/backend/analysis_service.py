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
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from . import history_trends, kovaak_run_store, queue
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


def _analysis_type_for_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Keep the persisted request type aligned with the reviewed dispatch family."""
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
    }.get(family, "flicking")


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
    completed = next((item for item in existing if item.get("status") == "done"), None)
    if completed is not None:
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
