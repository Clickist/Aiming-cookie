"""Idempotent Stats/Performance to Run-owned Raw/MP4 finalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
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


_TERMINAL_VIDEO_ERRORS = {
    "capture_coverage_gap": "video_coverage_gap",
    "capture_session_mismatch": "video_capture_session_mismatch",
    # [fix 2026-10-07 W8] native 的 capture_window_invalid = 窗口整体不在
    # replay 覆盖内（重启后全量补跑旧局的必然形态），与 control_window_invalid
    # （Python 侧窗口合法性，如 >300s）语义不同，拆码让补跑旧局在历史页/诊断
    # 里可辨认；前端合同=未知码原样显示，无需前端改动。
    "capture_window_invalid": "video_replay_window_out_of_range",
    "control_window_invalid": "video_window_invalid",
    "capture_video_invalid": "video_hardware_invalid",
    # control_auth_failed 是鉴权层终态失败；control_message_invalid 自
    # 0930 提案 D 起归入客户端可重试码（传输层腐蚀症状），不再映射终态。
    "control_auth_failed": "video_capture_protocol_invalid",
    "managed_path_invalid": "video_capture_protocol_invalid",
}

# [fix 2026-10-07 W7] 补跑预判的 30 天活动窗：只把「最近 30 天内打的局」
# 当补跑预判对象，远古窗口（含测试假窗口）不截胡。
_REPLAY_REBUILD_ACTIVITY_WINDOW_MS = 30 * 24 * 60 * 60 * 1000
log = logging.getLogger(__name__)


def _log_shutdown_control_failure(operation: str, error: Exception) -> None:
    code = getattr(error, "code", "capture_shutdown_failed")
    if code == "capture_control_unavailable":
        log.info("native capture endpoint already stopped during desktop shutdown")
        return
    log.warning("capture shutdown %s failed: %s", operation, code)


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
        telemetry_cut_hook=None,
    ) -> None:
        self._native_client = native_client
        self._data_root = Path(data_root).resolve()
        self._raw_input_snapshot_path = Path(raw_input_snapshot_path).resolve()
        self._user_id = user_id
        # 按局增量遥测切窗（见 telemetry_capture_service.request_run_cut）：
        # fire-and-forget，签名 (run_id, window_start_ms, window_end_ms)。
        self._telemetry_cut_hook = telemetry_cut_hook

    async def shutdown(self) -> None:
        """Release only the native session already finalizing during runtime exit."""
        if self._native_client is None:
            return
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            _log_shutdown_control_failure("status", error)
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
            _log_shutdown_control_failure("release", error)
            return
        if (
            released.get("phase") != "waiting_for_kovaak"
            or released.get("captureSessionId") is not None
        ):
            log.warning("capture shutdown release returned an unexpected status")

    async def finalizing_capture_session(self) -> str | None:
        """Return the finalizing session still retaining its replay buffer."""
        if self._native_client is None:
            return None
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            if error.code != "capture_control_unavailable":
                log.warning("capture exit status unavailable: %s", error.code)
            return None
        capture_session_id = status.get("captureSessionId")
        if (
            status.get("phase") != "finalizing"
            or not isinstance(capture_session_id, str)
        ):
            return None
        return capture_session_id

    async def release_capture_session(self, capture_session_id: str) -> bool:
        """Release one finalizing session; live capturing sessions keep their pre-roll.

        phase=finalizing is only entered after the owning KovaaK process
        exits, so a fast-restarted KovaaK process does not block the release.
        """
        if self._native_client is None:
            return False
        try:
            status = await asyncio.to_thread(self._native_client.status)
        except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
            log.warning("capture exit release status unavailable: %s", error.code)
            return False
        if (
            status.get("phase") != "finalizing"
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
        """Run _finalize with pipeline timing instrumentation (log-only).

        native export_replay 响应协议不含 native 端耗时（elapsed_ms 只进
        native.log 与诊断包）；以下分段均为 Python 侧墙钟差值，export 段
        含客户端内部最多 3 次瞬态重试。异常收尾路径记 finalization_state=raised。
        """
        finalize_started = time.monotonic()
        timings: dict[str, float] = {}
        try:
            stem = _source_key(discovery)
        except ValueError:
            stem = "<unknown>"
        run: dict | None = None
        try:
            run = await self._finalize(discovery, timings)
            return run
        finally:
            segments = " ".join(
                f"{phase}_ms={value:.0f}" for phase, value in sorted(timings.items())
            )
            log.info(
                "KovaaK finalize timing stem=%s elapsed_ms=%.0f "
                "finalization_state=%s %s",
                stem,
                (time.monotonic() - finalize_started) * 1000.0,
                run.get("finalization_state") if isinstance(run, dict) else "raised",
                segments,
            )

    async def _finalize(
        self,
        discovery: KovaaKFileDiscovery,
        timings: dict[str, float],
    ) -> dict:
        source_key = _source_key(discovery)
        if kovaak_run_store.is_kovaak_run_source_deleted(self._user_id, source_key):
            # 用户已删除该 run：源 CSV 仍在游戏目录，不再重新导入。
            log.info(
                "KovaaK ingestion skipped stem=%s phase=finalize code=source_deleted_by_user",
                source_key,
            )
            raise NonRetryableIngestionError(
                "source deleted by user", code="source_deleted_by_user",
            )
        ingest_started = time.monotonic()
        merged = await self._merge_discovery(discovery)
        trace_pending: RetryableIngestionError | None = None
        try:
            run = await kovaak_run_store.ingest_discovery(
                merged,
                user_id=self._user_id,
                raw_input_snapshot_path=self._raw_input_snapshot_path,
                require_stats_for_trace=True,
                defer_trace_attachment=True,
            )
        except RetryableIngestionError as error:
            if error.code != "trace_pending":
                raise
            trace_pending = error
            run = await kovaak_run_store.get_kovaak_run_by_source_key(
                self._user_id, _source_key(merged),
            )
            if run is None:
                raise
        timings["ingest"] = (time.monotonic() - ingest_started) * 1000.0

        if not run.get("stats_path"):
            # stats 缺失=真的没有源，继续等；performance 缺失不再阻塞：
            # 2026-10-08 起 finalize 走 CSV-only 对齐（kovaak_run_store），
            # KovaaK 不产 .perf 的机器由此放行（waiting_for_sources 永久卡死病灶）。
            if (
                run.get("finalization_state") != "pending"
                or run.get("finalization_error") != "waiting_for_sources"
            ):
                await kovaak_run_store.set_run_finalization_state(
                    run["id"], self._user_id, "pending", "waiting_for_sources",
                )
            raise NonRetryableIngestionError(
                "waiting_for_sources", code="waiting_for_sources",
            )

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
            run, trace_pending = await self._attach_trace_snapshot(
                run, None, require_coverage=False,
            )
            if trace_pending is not None:
                # The video window is terminally invalid; a trace that
                # cannot attach now must not hot-loop the finalized run.
                trace_pending = None
            return await self._finish_or_retry_trace(
                run, trace_pending, "video_window_invalid",
            )

        # [fix 2026-10-07 W7] 补跑预判跳过：重启/首启的全量补跑会把远早于
        # 采集覆盖（video replay buffer=300s=MAX_CAPTURE_WINDOW_MS 同源设计值；
        # raw ring 保留=10min=MAX_SNAPSHOT_SPAN_MS）的旧局整队送进 export +
        # trace 重试，每一局都是注定失败的 IO 与日志放大（10-07 报障实锤：
        # queue_depth 84 全量补跑，40+ 局 video_window_invalid/trace_stale）。
        # 预判只在「30 天活动窗内且窗口终点早于 video 覆盖下界（两界中更严
        # 的 300s）」时截胡：更老的局量少且老路径终态语义本就正确；刚打完的
        # 局（窗口终点距现在秒级）不受影响。时间源 wall clock，预判不抛错。
        now_ms = int(time.time() * 1000)
        if (
            now_ms - _REPLAY_REBUILD_ACTIVITY_WINDOW_MS
            < end_epoch_ms
            < now_ms - kovaak_run_store.MAX_CAPTURE_WINDOW_MS
        ):
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, "video_replay_expired",
            ) or run
            run = await kovaak_run_store.mark_mouse_trace_unavailable(
                run["id"], self._user_id, "trace_snapshot_out_of_coverage",
            ) or run
            return await self._finish_or_retry_trace(run, None, "video_replay_expired")

        # 挑战窗有效即触发按局增量遥测切窗（与 mp4/raw-input 同一收尾时机；
        # 服务侧幂等去重 + fire-and-forget，失败不影响本函数）。
        if self._telemetry_cut_hook is not None:
            try:
                self._telemetry_cut_hook(run["id"], start_epoch_ms, end_epoch_ms)
            except Exception:
                log.exception(
                    "telemetry run cut dispatch failed run=%s", run["id"],
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
        # A capture session the native side no longer holds (e.g. after a
        # desktop restart) can never be re-exported: its replay buffer is
        # gone, so the mismatch is terminal for the video instead of a
        # retryable loop. Already attached video keeps its evidence.
        video_session_mismatch = (
            isinstance(persisted_capture_session_id, str)
            and capture_session_id != persisted_capture_session_id
            and run.get("video_state") != "attached"
            and (
                trace_needs_snapshot
                or run.get("video_state") == "pending"
                or run.get("video_error") == "video_capture_session_mismatch"
            )
        )
        if video_session_mismatch and run.get("video_state") == "pending":
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"], self._user_id, "video_capture_session_mismatch",
            ) or run

        if trace_needs_snapshot:
            trace_started = time.monotonic()
            snapshot: dict[str, object] | None = None
            # 收尾局也要取覆盖回执：游戏退出后 phase 进入 finalizing（raw 后端在
            # release 前仍保留），若只认 capturing/degraded，收尾局的 trace 会一直
            # 等到 10 分钟保留期结束才被判 stale。这里仅放宽「何时可取回执」，覆盖
            # 门本身不变（receipt 仍由 attach_mouse_trace_snapshot_window 校验）。
            if (
                status.get("phase") in {"capturing", "degraded", "finalizing"}
                and status.get("raw", {}).get("state") in {"capturing", "finalizing"}
                and isinstance(capture_session_id, str)
            ):
                try:
                    snapshot = await asyncio.to_thread(
                        self._native_client.flush_raw_snapshot,
                        capture_session_id,
                    )
                except (NativeCaptureRetryableError, NativeCaptureTerminalError) as error:
                    log.warning(
                        "flush_raw_snapshot failed run=%s session=%s code=%s",
                        run.get("id"), capture_session_id, getattr(error, "code", None),
                    )
            run, trace_pending = await self._attach_trace_snapshot(
                run, snapshot,
            )
            timings["trace"] = (time.monotonic() - trace_started) * 1000.0

        if video_session_mismatch:
            return await self._finish_or_retry_trace(
                run, trace_pending, "video_capture_session_mismatch",
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
            raise RetryableIngestionError(
                "video_pending_conflict", code="video_pending_conflict",
            )

        export_started = time.monotonic()
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
                run = await kovaak_run_store.invalidate_run_for_video_coverage_gap(
                    run["id"],
                    self._user_id,
                    expected_pending_video_path=video_path,
                    expected_request_digest=request_digest,
                    data_root=self._data_root,
                ) or run
                return await self._finish_or_retry_trace(
                    run, trace_pending, video_error,
                )
            run = await kovaak_run_store.mark_run_video_unavailable(
                run["id"],
                self._user_id,
                video_error,
                expected_pending_video_path=video_path,
                expected_request_digest=request_digest,
            ) or run
            return await self._finish_or_retry_trace(run, trace_pending, video_error)
        finally:
            timings["export"] = (time.monotonic() - export_started) * 1000.0

        attach_started = time.monotonic()
        run = await kovaak_run_store.attach_run_video(
            run["id"],
            self._user_id,
            video_path,
            expected_pending_video_path=video_path,
            expected_request_digest=request_digest,
            data_root=self._data_root,
        ) or run
        timings["attach"] = (time.monotonic() - attach_started) * 1000.0
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
        *, require_coverage: bool = True,
    ) -> tuple[dict, RetryableIngestionError | None]:
        try:
            attached = await kovaak_run_store.attach_mouse_trace_snapshot_window(
                run,
                user_id=self._user_id,
                raw_input_snapshot_path=self._raw_input_snapshot_path,
                raw_snapshot_receipt=raw_snapshot_receipt,
                require_coverage=require_coverage,
            )
        except RetryableIngestionError as error:
            current = await kovaak_run_store.get_kovaak_run(
                run["id"], self._user_id,
            )
            return current or run, error
        return attached, None

    async def _finish_or_retry_trace(
        self,
        run: dict,
        trace_pending: RetryableIngestionError | None,
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
    "KovaaKCaptureFinalizer",
]
