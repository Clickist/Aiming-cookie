"""Loopback API and worker lifecycle owned by the Tauri desktop shell."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

import uvicorn

from . import db, worker
from .app import app

LOOPBACK_HOST = "127.0.0.1"
SERVER_START_POLL_SECONDS = 0.01
PARENT_STDIN_WATCH_ENV = "AIMING_COOKIE_WATCH_PARENT_STDIN"


class RuntimeStartupError(RuntimeError):
    """The desktop runtime could not become ready for the shell."""


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


async def run_runtime(*, stop_event: asyncio.Event | None = None) -> None:
    """Run API and worker until Tauri requests shutdown or either exits."""
    shutdown_requested = stop_event or asyncio.Event()
    remove_handlers = _install_shutdown_signal_handlers(shutdown_requested)
    _watch_parent_stdin(shutdown_requested)
    server = create_server(0)
    worker_stop = asyncio.Event()
    server_task = asyncio.create_task(server.serve())
    worker_task: asyncio.Task[None] | None = None
    stop_task = asyncio.create_task(shutdown_requested.wait())

    try:
        port = await _wait_for_server_start(server, server_task)
        worker_task = asyncio.create_task(run_worker(worker_stop))
        await asyncio.sleep(0)
        if worker_task.done():
            await worker_task
            raise RuntimeStartupError("worker exited before ready")

        # This is intentionally the runtime's only stdout protocol write.
        print(
            json.dumps({"type": "ready", "port": port}, separators=(",", ":")),
            flush=True,
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
        remove_handlers()
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        worker_stop.set()
        server.should_exit = True
        tasks = [server_task]
        if worker_task is not None:
            tasks.append(worker_task)
        cleanup_results = await asyncio.gather(*tasks, return_exceptions=True)
        await db.close_conn()
        if not active_error:
            for result in cleanup_results:
                if isinstance(result, BaseException):
                    raise result


def main() -> None:
    asyncio.run(run_runtime())


if __name__ == "__main__":
    main()
