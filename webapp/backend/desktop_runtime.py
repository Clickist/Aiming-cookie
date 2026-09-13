"""Loopback API and worker lifecycle owned by the Tauri desktop shell."""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import Future
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from typing import Any

import uvicorn

from . import config, file_store, kovaak_ingest, kovaak_run_store, worker
from . import kovaak_stats_export_setup
from . import external_telemetry_ingest
from . import telemetry_capture_service
from .app import app
from .kovaak_capture_finalizer import KovaaKCaptureFinalizer
from .native_capture_client import NativeCaptureClient

LOOPBACK_HOST = "127.0.0.1"
SERVER_START_POLL_SECONDS = 0.01
CAPTURE_EXIT_STATUS_POLL_SECONDS = 0.5
CAPTURE_EXIT_HARD_GRACE_SECONDS = 30
PARENT_STDIN_WATCH_ENV = "AIMING_COOKIE_WATCH_PARENT_STDIN"
# 运行期复查 KovaaK 统计导出设置的节流间隔（游戏可能在 AC 启动后才被打开）。
KOVAAK_EXPORT_RECHECK_SECONDS = 45.0
log = logging.getLogger(__name__)


class RuntimeStartupError(RuntimeError):
    """The desktop runtime could not become ready for the shell."""


class FinalizerFutureTracker:
    """Drain watcher-submitted finalizers before closing the shared database."""

    def __init__(self) -> None:
        self._futures: set[Future[dict]] = set()
        self._lock = threading.Lock()
        self._closed = False

    def track(self, future: Future[dict]) -> None:
        with self._lock:
            if self._closed:
                future.cancel()
                return
            self._futures.add(future)
        future.add_done_callback(self._discard)

    def _discard(self, future: Future[dict]) -> None:
        with self._lock:
            self._futures.discard(future)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._futures)

    async def wait_for_capture_exit_drain(self, grace_seconds: float) -> bool:
        deadline = time.monotonic() + max(grace_seconds, 0)
        observed_finalizer = self.has_pending()
        while True:
            if observed_finalizer and not self.has_pending():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(remaining, CAPTURE_EXIT_STATUS_POLL_SECONDS))
            observed_finalizer = observed_finalizer or self.has_pending()

    async def drain(self) -> None:
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        if futures:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures),
                return_exceptions=True,
            )


class CaptureExitReleaseTracker:
    """Release an exited native session after finalizers drain or hard grace."""

    def __init__(self, *, grace_seconds: float = CAPTURE_EXIT_HARD_GRACE_SECONDS) -> None:
        self._grace_seconds = grace_seconds
        self._released_sessions: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._session_id: str | None = None

    async def observe(
        self,
        finalizer: KovaaKCaptureFinalizer,
        finalizer_futures: FinalizerFutureTracker,
    ) -> None:
        capture_session_id = await finalizer.finalizing_capture_session()
        if capture_session_id is None or capture_session_id in self._released_sessions:
            return
        if (
            self._task is not None
            and not self._task.done()
            and self._session_id == capture_session_id
        ):
            return
        self._session_id = capture_session_id
        self._task = asyncio.create_task(
            self._drain_then_release(
                finalizer, finalizer_futures, capture_session_id,
            )
        )

    async def _drain_then_release(
        self,
        finalizer: KovaaKCaptureFinalizer,
        finalizer_futures: FinalizerFutureTracker,
        capture_session_id: str,
    ) -> None:
        await finalizer_futures.wait_for_capture_exit_drain(self._grace_seconds)
        if await finalizer.release_capture_session(capture_session_id):
            self._released_sessions.add(capture_session_id)

    async def monitor(
        self,
        finalizer: KovaaKCaptureFinalizer,
        finalizer_futures: FinalizerFutureTracker,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=CAPTURE_EXIT_STATUS_POLL_SECONDS,
                )
            except asyncio.TimeoutError:
                await self.observe(finalizer, finalizer_futures)

    async def drain(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

def create_server(port: int) -> uvicorn.Server:
    """Create the loopback-only API server without request access logs."""
    config = uvicorn.Config(
        app,
        host=LOOPBACK_HOST,
        port=port,
        access_log=False,
        log_config=None,
    )
    return uvicorn.Server(config)


BACKEND_LOG_MAX_BYTES = 2_000_000
BACKEND_LOG_BACKUP_COUNT = 1

RUNTIME_LOCK_NAME = ".runtime.lock"


def acquire_runtime_lock(data_root: Path) -> bool:
    """Hold an exclusive OS lock on ``{DATA_ROOT}/.runtime.lock`` for our lifetime.

    The kernel releases it when the process exits (any exit path), so a stale
    lock can never outlive its owner. A second runtime on the same DATA_ROOT
    is the strongest known cause of backend.log truncation: two
    RotatingFileHandler instances rotate the same file out from under each
    other on Windows. Returns False when a live runtime already holds it.
    """
    lock_path = data_root / RUNTIME_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0)
        handle.write(f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
    except OSError:
        with contextlib.suppress(OSError):
            handle.close()
        return False
    # 故意不 close：锁必须活到进程结束（进程退出时内核自动释放句柄与锁）。
    _RUNTIME_LOCK_HANDLES.append(handle)
    return True


_RUNTIME_LOCK_HANDLES: list[Any] = []


def configure_file_logging() -> None:
    """Mirror backend logs (API/finalizer/worker share this process) into
    ``{DATA_ROOT}/logs/backend.log``.

    The packaged shell pipes stderr only into a GUI process without a
    console, so without this file every finalizer/ingest failure line is
    lost on user machines. Never raise here: diagnostics logging must not
    block runtime startup. ``delay=True`` reopens the file per record so
    an externally rotated/locked file self-heals on the next line instead
    of writing into a renamed handle forever.
    """
    try:
        log_dir = config.DATA_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "backend.log",
            maxBytes=BACKEND_LOG_MAX_BYTES,
            backupCount=BACKEND_LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        # delay 模式下文件要到首条日志才创建；这里主动 touch 保持
        # 「启动即有 backend.log」的可诊断性（logHealth / 用户找文件）。
        (log_dir / "backend.log").touch()
        root = logging.getLogger()
        if any(
            isinstance(existing, RotatingFileHandler)
            and getattr(existing, "baseFilename", None) == handler.baseFilename
            for existing in root.handlers
        ):
            return
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except OSError:
        pass


def _bound_port(server: Any) -> int:
    for runtime_server in getattr(server, "servers", []):
        for sock in getattr(runtime_server, "sockets", []):
            address = sock.getsockname()
            if isinstance(address, tuple) and len(address) >= 2:
                port = address[1]
                if isinstance(port, int) and 1 <= port <= 65535:
                    return port
    raise RuntimeStartupError("runtime server did not bind a loopback port")


async def _wait_for_server_start(server: Any, server_task: asyncio.Task[None]) -> int:
    while not getattr(server, "started", False):
        if server_task.done():
            await server_task
            raise RuntimeStartupError("API server exited before ready")
        await asyncio.sleep(SERVER_START_POLL_SECONDS)
    return _bound_port(server)


async def run_worker(stop_event: asyncio.Event) -> None:
    """Run the existing worker loop and cancel it during desktop shutdown."""
    worker_task = asyncio.create_task(worker._run_loop_async())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            {worker_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_task in done:
            await worker_task
    finally:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


def _watch_parent_stdin(stop_event: asyncio.Event) -> None:
    """Request shutdown when the Tauri-owned stdin pipe reaches EOF."""
    if os.environ.get(PARENT_STDIN_WATCH_ENV) != "1":
        return

    loop = asyncio.get_running_loop()

    def wait_for_eof() -> None:
        try:
            sys.stdin.buffer.read(1)
            loop.call_soon_threadsafe(stop_event.set)
        except (OSError, RuntimeError):
            # A signal-driven shutdown may close the loop before this daemon wakes.
            pass

    threading.Thread(
        target=wait_for_eof,
        name="desktop-parent-stdin",
        daemon=True,
    ).start()


def _install_shutdown_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            # Some Windows event loops do not support add_signal_handler.
            continue

    def remove_handlers() -> None:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)

    return remove_handlers


def create_kovaak_capture_finalizer() -> KovaaKCaptureFinalizer:
    address = config.NATIVE_CAPTURE_CONTROL_ADDR
    secret = config.NATIVE_CAPTURE_CONTROL_SECRET
    if bool(address) != bool(secret):
        raise RuntimeStartupError("native capture control configuration is incomplete")
    client = NativeCaptureClient(address, secret) if address and secret else None
    return KovaaKCaptureFinalizer(
        native_client=client,
        data_root=config.DATA_ROOT,
        raw_input_snapshot_path=config.DATA_ROOT / "raw-input" / "buffer.bin",
        user_id=config.DESKTOP_LOCAL_PROFILE,
    )


def create_kovaak_ingestion_service(
    loop: asyncio.AbstractEventLoop,
    finalizer: KovaaKCaptureFinalizer,
    finalizer_futures: FinalizerFutureTracker | None = None,
) -> kovaak_ingest.KovaaKIngestionService:
    """Create the Desktop-only watcher bridge without changing Web runtime behavior."""
    finalizer_lock = asyncio.Lock()

    async def finalize_one(
        discovery: kovaak_ingest.KovaaKFileDiscovery,
    ) -> dict:
        async with finalizer_lock:
            return await finalizer.finalize(discovery)

    def on_discovery(discovery: kovaak_ingest.KovaaKFileDiscovery) -> Future[dict]:
        future = asyncio.run_coroutine_threadsafe(
            finalize_one(discovery),
            loop,
        )
        if finalizer_futures is not None:
            finalizer_futures.track(future)

        return future

    stats_dirs = list(getattr(config, "KOVAAK_STATS_DIRS", None) or ())
    if not stats_dirs and config.KOVAAK_STATS_DIR:
        stats_dirs = [config.KOVAAK_STATS_DIR]
    performance_dirs = list(getattr(config, "KOVAAK_PERFORMANCE_DIRS", None) or ())
    if not performance_dirs and config.KOVAAK_PERFORMANCE_DIR:
        performance_dirs = [config.KOVAAK_PERFORMANCE_DIR]

    return kovaak_ingest.KovaaKIngestionService(
        stats_dir=stats_dirs,
        performance_dir=performance_dirs,
        callback=on_discovery,
        poll_interval=config.KOVAAK_WATCH_POLL_SECONDS,
        candidate_limit=50,
    )


def persist_kovaak_ingestion_diagnostics(
    ingestion_service: kovaak_ingest.KovaaKIngestionService,
) -> None:
    """Persist path-redacted watcher health for a later desktop diagnostics export."""
    diagnostics = getattr(ingestion_service, "diagnostics", None)
    if not callable(diagnostics):
        return
    snapshot = diagnostics()
    for watcher in snapshot.get("watchers", []):
        if isinstance(watcher, dict):
            watcher.pop("directory", None)
    file_store.write_json("diagnostics/kovaak-watcher.json", snapshot)


async def monitor_kovaak_ingestion_diagnostics(
    ingestion_service: kovaak_ingest.KovaaKIngestionService,
    stop_event: asyncio.Event,
) -> None:
    # 僵尸兜底清扫按小时节流即可；启动时的一次性清扫在桌面启动序列里做。
    next_expire_monotonic = 0.0
    # 运行期复查统计导出设置按 KOVAAK_EXPORT_RECHECK_SECONDS 节流（启动时已查过，
    # 这里首次顺延一个周期，避免和启动检查重复）。确保函数自带硬重启 + 退避，
    # 且整体 fail-soft：任何异常只进日志，不影响诊断监控循环。
    next_export_recheck_monotonic = time.monotonic() + KOVAAK_EXPORT_RECHECK_SECONDS
    while not stop_event.is_set():
        try:
            persist_kovaak_ingestion_diagnostics(ingestion_service)
        except Exception:
            log.exception("KovaaK ingestion diagnostics snapshot write failed")
        if time.monotonic() >= next_expire_monotonic:
            next_expire_monotonic = time.monotonic() + 3600.0
            try:
                await kovaak_run_store.expire_stale_pending_runs(config.DESKTOP_LOCAL_PROFILE)
            except Exception:
                log.exception("Stale pending run expiry sweep failed")
        if time.monotonic() >= next_export_recheck_monotonic:
            next_export_recheck_monotonic = (
                time.monotonic() + KOVAAK_EXPORT_RECHECK_SECONDS
            )
            try:
                # 进程检测/写盘/拉起都是阻塞调用，放线程避免卡住事件循环。
                await asyncio.to_thread(
                    kovaak_stats_export_setup.ensure_kovaak_stats_export,
                    config.resolve_kovaak_install_dir(),
                )
            except Exception:
                log.exception("Periodic KovaaK stats export recheck failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            continue


def create_external_telemetry_service() -> external_telemetry_ingest.ExternalTelemetryService | None:
    """Create the external telemetry watcher (parallel data source, optional).

    Failure here must never block the main KovaaK ingest chain: without a
    configured watch root the service simply runs inert.
    """
    try:
        return external_telemetry_ingest.create_external_telemetry_service()
    except Exception:
        log.exception("External telemetry service creation failed")
        return None


def persist_external_telemetry_diagnostics(
    service: external_telemetry_ingest.ExternalTelemetryService | None,
) -> None:
    """Persist path-redacted external watcher health for diagnostics export."""
    diagnostics = getattr(service, "diagnostics", None)
    if not callable(diagnostics):
        return
    snapshot = diagnostics()
    snapshot.pop("recent_candidates", None)
    file_store.write_json("diagnostics/external-telemetry-watcher.json", snapshot)


async def run_runtime(*, stop_event: asyncio.Event | None = None) -> None:
    """Run API and worker until Tauri requests shutdown or either exits."""
    shutdown_requested = stop_event or asyncio.Event()
    app.state.desktop_shutdown_requested = False
    remove_handlers = _install_shutdown_signal_handlers(shutdown_requested)
    _watch_parent_stdin(shutdown_requested)
    server = create_server(0)
    worker_stop = asyncio.Event()
    server_task = asyncio.create_task(server.serve())
    worker_task: asyncio.Task[None] | None = None
    stop_task = asyncio.create_task(shutdown_requested.wait())
    finalizer = create_kovaak_capture_finalizer()
    finalizer_futures = FinalizerFutureTracker()
    capture_exit_releases = CaptureExitReleaseTracker()
    capture_exit_task: asyncio.Task[None] | None = None
    ingestion_diagnostics_task: asyncio.Task[None] | None = None
    ingestion_service = create_kovaak_ingestion_service(
        asyncio.get_running_loop(), finalizer, finalizer_futures,
    )
    app.state.kovaak_ingestion_service = ingestion_service
    # 采集工具随产品分发（2026-09-06 拍板）：先保证 watch 根自动指向托管
    # cleaned 根（已配置则不动），再创建外部遥测服务让它读到该配置。
    telemetry_capture_service.ensure_managed_watch_root()
    external_service = create_external_telemetry_service()
    app.state.external_telemetry_service = external_service
    capture_service = telemetry_capture_service.create_telemetry_capture_service()
    app.state.telemetry_capture_service = capture_service

    try:
        port = await _wait_for_server_start(server, server_task)
        worker_task = asyncio.create_task(run_worker(worker_stop))
        await asyncio.sleep(0)
        if worker_task.done():
            await worker_task
            raise RuntimeStartupError("worker exited before ready")
        reconciliation = await kovaak_run_store.reconcile_mouse_traces(config.DATA_ROOT)
        if any(reconciliation.values()):
            log.info("KovaaK trace reconciliation completed: %s", reconciliation)
        video_reconciliation = await kovaak_run_store.reconcile_run_videos(
            config.DATA_ROOT
        )
        if any(video_reconciliation.values()):
            log.info(
                "KovaaK video reconciliation completed: %s",
                video_reconciliation,
            )
        # 启动即保底开启 KovaaK 统计导出（SaveStatistics + Challenge Completion），
        # 保证每局落盘 stats CSV + .perf（History 数据源）。幂等且 fail-soft：
        # 结果与失败原因由包装层写入 backend.log，任何失败都不阻塞桌面启动。
        await asyncio.to_thread(
            kovaak_stats_export_setup.ensure_kovaak_stats_export,
            config.resolve_kovaak_install_dir(),
        )
        # 启动清扫一次超龄僵尸条目（升级自愈：老版本留下的 pending 永久等待
        # 在这里就地终态）；此后由诊断监控循环按小时节流补扫。fail-soft。
        try:
            await kovaak_run_store.expire_stale_pending_runs(config.DESKTOP_LOCAL_PROFILE)
        except Exception:
            log.exception("Startup stale pending run expiry sweep failed")
        ingestion_service.start()
        persist_kovaak_ingestion_diagnostics(ingestion_service)
        ingestion_diagnostics_task = asyncio.create_task(
            monitor_kovaak_ingestion_diagnostics(ingestion_service, shutdown_requested)
        )
        if external_service is not None:
            external_service.start()
            persist_external_telemetry_diagnostics(external_service)
        if capture_service is not None:
            # start() 可能带旧会话收尾（cleaner/merge 子进程），放线程避免阻塞事件循环。
            await asyncio.to_thread(capture_service.start)

        # This is intentionally the runtime's only stdout protocol write.
        print(
            json.dumps({"type": "ready", "port": port}, separators=(",", ":")),
            flush=True,
        )
        capture_exit_task = asyncio.create_task(
            capture_exit_releases.monitor(
                finalizer, finalizer_futures, shutdown_requested,
            )
        )

        done, _ = await asyncio.wait(
            {server_task, worker_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task not in done:
            if server_task in done:
                await server_task
                raise RuntimeStartupError("API server exited unexpectedly")
            await worker_task
            raise RuntimeStartupError("worker exited unexpectedly")
    finally:
        active_error = sys.exc_info()[0] is not None
        app.state.desktop_shutdown_requested = True
        remove_handlers()
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        if capture_exit_task is not None:
            capture_exit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await capture_exit_task
        if ingestion_diagnostics_task is not None:
            ingestion_diagnostics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ingestion_diagnostics_task
        await capture_exit_releases.drain()
        ingestion_service.stop()
        if external_service is not None:
            external_service.stop()
            with contextlib.suppress(Exception):
                persist_external_telemetry_diagnostics(external_service)
        app.state.external_telemetry_service = None
        with contextlib.suppress(Exception):
            persist_kovaak_ingestion_diagnostics(ingestion_service)
        app.state.kovaak_ingestion_service = None
        if capture_service is not None:
            # 终止采集伴生进程；有未收尾的原始件则留给下次启动扫尾。
            with contextlib.suppress(Exception):
                await asyncio.to_thread(capture_service.stop)
        worker_stop.set()
        await finalizer_futures.drain()
        server.should_exit = True
        server_results = await asyncio.gather(server_task, return_exceptions=True)
        await finalizer.shutdown()
        worker_results: list[object] = []
        if worker_task is not None:
            worker_results = list(
                await asyncio.gather(worker_task, return_exceptions=True)
            )
        app.state.desktop_shutdown_requested = False
        if not active_error:
            for result in [*server_results, *worker_results]:
                if isinstance(result, BaseException):
                    raise result


def main() -> None:
    configure_file_logging()
    # 远程排障锚点（0912 日志覆盖审计 P1）：backend.log 首行固定打启动环境，
    # 否则远程看到日志无法确认对应哪次安装/哪份数据根。
    log.info(
        "Aiming Cookie backend runtime starting: data_root=%s executable=%s python=%s",
        config.DATA_ROOT,
        sys.executable,
        platform.python_version(),
    )
    if not acquire_runtime_lock(config.DATA_ROOT):
        log.error(
            "backend runtime refused to start: another live runtime holds the "
            "DATA_ROOT lock (%s). A second instance on the same data root "
            "corrupts rotating log files — close the other instance first.",
            config.DATA_ROOT,
        )
        raise SystemExit(3)
    asyncio.run(run_runtime())


if __name__ == "__main__":
    main()
