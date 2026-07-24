"""Idempotent Stats/Performance to Run-owned Raw/MP4 finalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from . import kovaak_run_store
from .kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
    RetryableIngestionError,
    normalize_kovaak_stem,
)
from .native_capture_client import (
    NativeCaptureClient,
    NativeCaptureProtocolError,
    NativeCaptureRetryableError,
    NativeCaptureTerminalError,
)


class CaptureFinalizationPending(RetryableIngestionError):
    pass


class CaptureFinalizationWaiting(NonRetryableIngestionError):
    pass


_TERMINAL_VIDEO_ERRORS = {
    "capture_coverage_gap": "video_coverage_gap",
    "capture_session_mismatch": "video_capture_session_mismatch",
    "capture_window_invalid": "video_window_invalid",
    "control_window_invalid": "video_window_invalid",
    "capture_video_invalid": "video_hardware_invalid",
    "control_auth_failed": "video_capture_protocol_invalid",
    "control_message_invalid": "video_capture_protocol_invalid",
    "managed_path_invalid": "video_capture_protocol_invalid",
}
log = logging.getLogger(__name__)


def _source_key(discovery: KovaaKFileDiscovery) -> str:
    if discovery.stem:
        return discovery.stem
    if not discovery.paths:
        raise ValueError("KovaaK discovery has no source paths")
    return normalize_kovaak_stem(discovery.paths[0])


def _source_revision(summary: object) -> dict[str, object] | None:
    if not isinstance(summary, dict):
        return None
    source = summary.get("source")
    if not isinstance(source, dict):
        return None
    fields = ("sha256", "size", "mtime_ns", "parser_version")
    if any(source.get(field) is None for field in fields):
        return None
    return {field: source[field] for field in fields}


def _request_identity(run: dict) -> tuple[str, str]:
    identity = {
        "run_id": run["id"],
        "stats": _source_revision(run.get("stats_summary")),
        "performance": _source_revision(run.get("performance_summary")),
        "start_epoch_ms": run.get("window_start_epoch_ms"),
        "end_epoch_ms": run.get("window_end_epoch_ms"),
        "capture_session_id": run.get("capture_session_id"),
    }
    request_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    request_digest = hashlib.sha256(
        (
            "capture_export.v1|"
            f"{request_id}|{run['id']}|{run['capture_session_id']}|"
            f"{run['window_start_epoch_ms']}|{run['window_end_epoch_ms']}"
        ).encode("utf-8")
    ).hexdigest()
    return request_id, request_digest


class KovaaKCaptureFinalizer:
    def __init__(
        self,
        *,
        native_client: NativeCaptureClient | None,
        data_root: str | Path,
        raw_input_snapshot_path: str | Path,
        user_id: str,
    ) -> None:
        self._native_client = native_client
        self._data_root = Path(data_root).resolve()
        self._raw_input_snapshot_path = Path(raw_input_snapshot_path).resolve()
        self._user_id = user_id

    async def shutdown(self) -> None:
        """Release only the native session already finalizing during runtime exit."""
        if self._native_client is None:
            return
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture shutdown status unavailable: %s", error.code)
            return
        capture_session_id = status.get("captureSessionId")
        if (
            status.get("phase") != "finalizing"
            or not isinstance(capture_session_id, str)
        ):
            return
        try:
            released = await asyncio.to_thread(
                self._native_client.release_capture_session,
                capture_session_id,
            )
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture shutdown release failed: %s", error.code)
            return
        if (
            released.get("phase") != "waiting_for_kovaak"
            or released.get("captureSessionId") is not None
        ):
            log.warning("capture shutdown release returned an unexpected status")

    async def finalizing_capture_session(self) -> str | None:
        """Return the exited native session that is still retaining its replay buffer."""
        if self._native_client is None:
            return None
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture exit status unavailable: %s", error.code)
            return None
        capture_session_id = status.get("captureSessionId")
        if (
            status.get("phase") != "finalizing"
            or status.get("kovaakProcessPresent") is not False
            or not isinstance(capture_session_id, str)
        ):
            return None
        return capture_session_id

    async def release_capture_session(self, capture_session_id: str) -> bool:
        """Release one exited session, preserving a live KovaaK session's pre-roll."""
        if self._native_client is None:
            return False
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture exit release status unavailable: %s", error.code)
            return False
        if (
            status.get("phase") != "finalizing"
            or status.get("kovaakProcessPresent") is not False
            or status.get("captureSessionId") != capture_session_id
        ):
            return False
        try:
            released = await asyncio.to_thread(
                self._native_client.release_capture_session,
                capture_session_id,
            )
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture exit release failed: %s", error.code)
            return False
        return (
            released.get("phase") == "waiting_for_kovaak"
            and released.get("captureSessionId") is None
        )

    async def finalize(self, discovery: KovaaKFileDiscovery) -> dict:
        merged = await self._merge_discovery(discovery)
        trace_pending: kovaak_run_store.TracePendingError | None = None
        try:
            run = await kovaak_run_store.ingest_discovery(
                merged,
                user_id=self._user_id,
                raw_input_snapshot_path=self._raw_input_snapshot_path,
                require_stats_for_trace=True,
                defer_trace_attachment=True,
            )
        except kovaak_run_store.TracePendingError as error:
            trace_pending = error
            run = await kovaak_run_store.get_kovaak_run_by_source_key(
                self._user_id, _source_key(merged),
            )
            if run is None:
                raise

        if not run.get("stats_path") or not run.get("performance_path"):
            if (
                run.get("finalization_state") != "pending"
                or run.get("finalization_error") != "waiting_for_sources"
            ):
                await kovaak_run_store.set_run_finalization_state(
                    run["id"], self._user_id, "pending", "waiting_for_sources",
                )
            raise CaptureFinalizationWaiting("waiting_for_sources")

        if (
            run.get("finalization_state") == "finalized"
            and run.get("finalization_error") == "video_coverage_gap"
        ):
            return run

        if run.get("alignment_state") != "resolved":
            alignment = run.get("alignment_summary")
            error_code = (
                alignment.get("error_code")
                if isinstance(alignment, dict)
                else "time_alignment_unavailable"
            )
            video_error = (
                "video_pause_unsupported"
                if error_code == "pause_unsupported"
                else "video_time_alignment_unavailable"
            )
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, video_error,
            ) or run
            return await self._finish_or_retry_trace(run, trace_pending, video_error)

        start_epoch_ms = run.get("window_start_epoch_ms")
        end_epoch_ms = run.get("window_end_epoch_ms")
        if (
            not isinstance(start_epoch_ms, int)
            or not isinstance(end_epoch_ms, int)
            or end_epoch_ms <= start_epoch_ms
            or end_epoch_ms - start_epoch_ms > kovaak_run_store.MAX_CAPTURE_WINDOW_MS
        ):
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, "video_window_invalid",
            ) or run
            return await self._finish_or_retry_trace(
                run, trace_pending, "video_window_invalid",
            )

        if (
            run.get("video_state") == "attached"
            and run.get("trace_state") == "attached"
        ):
            return await self._finish_or_retry_trace(run, trace_pending, None)

        await kovaak_run_store.set_run_finalization_state(
            run["id"], self._user_id, "pending",
        )
        if self._native_client is None:
            run, trace_pending = await self._attach_trace_snapshot(run, None)
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, "video_capture_unavailable",
            ) or run
            return await self._finish_or_retry_trace(
                run, trace_pending, "video_capture_unavailable",
            )

        try:
            status = await asyncio.to_thread(self._native_client.status)
        except NativeCaptureRetryableError as error:
            run, trace_pending = await self._attach_trace_snapshot(run, None)
            await kovaak_run_store.set_run_finalization_state(
                run["id"], self._user_id, "retryable", error.code,
            )
            raise
        except NativeCaptureTerminalError as error:
            run, trace_pending = await self._attach_trace_snapshot(run, None)
            video_error = _TERMINAL_VIDEO_ERRORS.get(
                error.code, "video_capture_unavailable",
            )
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, video_error,
            ) or run
            return await self._finish_or_retry_trace(run, trace_pending, video_error)

        capture_session_id = status.get("captureSessionId")
        persisted_capture_session_id = run.get("capture_session_id")
        trace_needs_snapshot = run.get("trace_state") in {"none", "pending"}
        if (
            isinstance(persisted_capture_session_id, str)
            and capture_session_id != persisted_capture_session_id
            and (trace_needs_snapshot or run.get("video_state") == "pending")
        ):
            await kovaak_run_store.set_run_finalization_state(
                run["id"], self._user_id, "retryable", "capture_session_mismatch",
            )
            raise CaptureFinalizationPending("capture_session_mismatch")

        if trace_needs_snapshot:
            snapshot: dict[str, object] | None = None
            if (
                status.get("phase") in {"capturing", "degraded"}
                and status.get("raw", {}).get("state") == "capturing"
                and isinstance(capture_session_id, str)
            ):
                try:
                    snapshot = await asyncio.to_thread(
                        self._native_client.flush_raw_snapshot,
                        capture_session_id,
                    )
                except (NativeCaptureRetryableError, NativeCaptureTerminalError):
                    pass
            run, trace_pending = await self._attach_trace_snapshot(
                run, snapshot,
            )

        if run.get("video_state") == "attached":
            return await self._finish_or_retry_trace(run, trace_pending, None)

        if (
            status.get("phase") not in {"capturing", "finalizing"}
            or status.get("video", {}).get("state") not in {"capturing", "finalizing"}
            or not isinstance(capture_session_id, str)
        ):
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, "video_capture_unavailable",
            ) or run
            return await self._finish_or_retry_trace(
                run, trace_pending, "video_capture_unavailable",
            )

        run["capture_session_id"] = capture_session_id
        request_id, request_digest = _request_identity(run)
        video_path = (
            self._data_root
            / "runs"
            / str(run["id"])
            / f"video-{request_id}.mp4"
        )
        run = await kovaak_run_store.begin_run_video_attach(
            run["id"],
            self._user_id,
            pending_video_path=video_path,
            request_digest=request_digest,
            capture_session_id=capture_session_id,
            start_epoch_ms=start_epoch_ms,
            end_epoch_ms=end_epoch_ms,
            alignment_summary=run.get("alignment_summary"),
            data_root=self._data_root,
        ) or run
        if run.get("video_state") == "attached":
            return await self._finish_or_retry_trace(run, trace_pending, None)
        if (
            run.get("pending_video_path") != str(video_path.resolve())
            or run.get("video_request_digest") != request_digest
        ):
            await kovaak_run_store.set_run_finalization_state(
                run["id"], self._user_id, "retryable", "video_pending_conflict",
            )
            raise CaptureFinalizationPending("video_pending_conflict")

        try:
            response = await asyncio.to_thread(
                self._native_client.export_replay,
                request_id=request_id,
                run_id=run["id"],
                capture_session_id=capture_session_id,
                start_epoch_ms=start_epoch_ms,
                end_epoch_ms=end_epoch_ms,
            )
            if response.get("requestDigest") != request_digest:
                raise NativeCaptureProtocolError(
                    "capture_control_response_schema_invalid"
                )
        except NativeCaptureRetryableError as error:
            await kovaak_run_store.set_run_finalization_state(
                run["id"], self._user_id, "retryable", error.code,
            )
            raise
        except NativeCaptureTerminalError as error:
            video_error = _TERMINAL_VIDEO_ERRORS.get(
                error.code, "video_capture_unavailable",
            )
            if error.code == "capture_coverage_gap":
                return await kovaak_run_store.invalidate_run_for_video_coverage_gap(
                    run["id"],
                    self._user_id,
                    expected_pending_video_path=video_path,
                    expected_request_digest=request_digest,
                    data_root=self._data_root,
                ) or run
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"],
                self._user_id,
                video_error,
                expected_pending_video_path=video_path,
                expected_request_digest=request_digest,
            ) or run
            return await self._finish_or_retry_trace(run, trace_pending, video_error)

        run = await kovaak_run_store.attach_run_video(
            run["id"],
            self._user_id,
            video_path,
            expected_pending_video_path=video_path,
            expected_request_digest=request_digest,
            data_root=self._data_root,
        ) or run
        return await self._finish_or_retry_trace(run, trace_pending, None)

    async def _merge_discovery(
        self,
        discovery: KovaaKFileDiscovery,
    ) -> KovaaKFileDiscovery:
        source_key = _source_key(discovery)
        existing = await kovaak_run_store.get_kovaak_run_by_source_key(
            self._user_id, source_key,
        )
        return KovaaKFileDiscovery(
            stem=source_key,
            stats_path=(
                discovery.stats_path
                or (Path(existing["stats_path"]) if existing and existing.get("stats_path") else None)
            ),
            performance_path=(
                discovery.performance_path
                or (
                    Path(existing["performance_path"])
                    if existing and existing.get("performance_path")
                    else None
                )
            ),
        )

    async def _attach_trace_snapshot(
        self,
        run: dict,
        raw_snapshot_receipt: dict[str, object] | None,
    ) -> tuple[dict, kovaak_run_store.TracePendingError | None]:
        try:
            attached = await kovaak_run_store.attach_mouse_trace_snapshot_window(
                run,
                user_id=self._user_id,
                raw_input_snapshot_path=self._raw_input_snapshot_path,
                raw_snapshot_receipt=raw_snapshot_receipt,
                require_coverage=True,
            )
        except kovaak_run_store.TracePendingError as error:
            current = await kovaak_run_store.get_kovaak_run(
                run["id"], self._user_id,
            )
            return current or run, error
        return attached, None

    async def _finish_or_retry_trace(
        self,
        run: dict,
        trace_pending: kovaak_run_store.TracePendingError | None,
        finalization_error: str | None,
    ) -> dict:
        if trace_pending is not None:
            await kovaak_run_store.set_run_finalization_state(
                run["id"],
                self._user_id,
                "retryable",
                "trace_waiting_snapshot",
            )
            raise trace_pending
        return await kovaak_run_store.set_run_finalization_state(
            run["id"],
            self._user_id,
            "finalized",
            finalization_error,
        ) or run


__all__ = [
    "CaptureFinalizationPending",
    "CaptureFinalizationWaiting",
    "KovaaKCaptureFinalizer",
]
