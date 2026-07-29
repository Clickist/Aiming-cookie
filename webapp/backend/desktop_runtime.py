"""Loopback API and worker lifecycle owned by the Tauri desktop shell."""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import Future
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import uvicorn

from . import config, db, kovaak_ingest, kovaak_run_store, worker
from .app import app
from .kovaak_capture_finalizer import KovaaKCaptureFinalizer
from .native_capture_client import NativeCaptureClient

LOOPBACK_HOST = "127.0.0.1"
SERVER_START_POLL_SECONDS = 0.01
CAPTURE_EXIT_STATUS_POLL_SECONDS = 0.5
CAPTURE_EXIT_HARD_GRACE_SECONDS = 30
PARENT_STDIN_WATCH_ENV = "AIMING_COOKIE_WATCH_PARENT_STDIN"
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

    return kovaak_ingest.KovaaKIngestionService(
        stats_dir=config.KOVAAK_STATS_DIR,
        performance_dir=config.KOVAAK_PERFORMANCE_DIR,
        callback=on_discovery,
        poll_interval=config.KOVAAK_WATCH_POLL_SECONDS,
        candidate_limit=50,
    )


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
    ingestion_service = create_kovaak_ingestion_service(
        asyncio.get_running_loop(), finalizer, finalizer_futures,
    )

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
        ingestion_service.start()

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
        await capture_exit_releases.drain()
        ingestion_service.stop()
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
        await db.close_conn()
        app.state.desktop_shutdown_requested = False
        if not active_error:
            for result in [*server_results, *worker_results]:
                if isinstance(result, BaseException):
                    raise result


def main() -> None:
    asyncio.run(run_runtime())


if __name__ == "__main__":
    main()
