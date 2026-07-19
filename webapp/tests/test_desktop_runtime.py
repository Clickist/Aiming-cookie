from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from webapp.backend import desktop_runtime


def test_server_is_configured_for_dynamic_loopback_without_access_logs() -> None:
    server = desktop_runtime.create_server(0)
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 0
    assert server.config.access_log is False


def test_bound_port_reads_actual_dynamic_socket() -> None:
    assert desktop_runtime._bound_port(_FakeServerShell(43127)) == 43127
    with pytest.raises(desktop_runtime.RuntimeStartupError, match="did not bind"):
        desktop_runtime._bound_port(_FakeServerShell(0))


@pytest.mark.skipif(
    os.environ.get("RUN_DESKTOP_RUNTIME_INTEGRATION") != "1",
    reason="requires permission to bind a loopback socket",
)
@pytest.mark.asyncio
async def test_runtime_process_binds_dynamic_port_and_serves_health(tmp_path) -> None:
    env = os.environ.copy()
    env.update({
        "AIMING_COOKIE_DESKTOP_TOKEN": "test-runtime-token",
        "AIMING_COOKIE_WATCH_PARENT_STDIN": "1",
        "DATA_ROOT": str(tmp_path / "data"),
        "VIDEO_TMP_DIR": str(tmp_path / "data"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
    })
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "webapp.backend.desktop_runtime",
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        ready = json.loads(line)
        assert set(ready) == {"type", "port"}
        assert ready["type"] == "ready"
        assert isinstance(ready["port"], int) and ready["port"] > 0
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"http://127.0.0.1:{ready['port']}/healthz")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        assert process.stdin is not None
        process.stdin.close()
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)


@pytest.mark.asyncio
async def test_runtime_starts_api_and_worker_before_ready_then_shuts_both_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    api_started = asyncio.Event()
    stop = asyncio.Event()

    class FakeServer:
        should_exit = False
        started = False
        servers = []

        async def serve(self) -> None:
            events.append("api-start")
            self.started = True
            self.servers = [_FakeBoundServer(43127)]
            api_started.set()
            while not self.should_exit:
                await asyncio.sleep(0)
            events.append("api-stop")

    async def fake_worker(stop_event: asyncio.Event) -> None:
        events.append("worker-start")
        await stop_event.wait()
        events.append("worker-stop")

    async def reconcile_traces(_data_root) -> dict:
        events.append("trace-reconcile")
        return {"attached": 0, "unavailable": 0, "quarantined": 0}

    async def reconcile_videos(_data_root) -> dict:
        events.append("video-reconcile")
        return {
            "attached": 0,
            "retryable": 0,
            "unavailable": 0,
            "quarantined": 0,
        }

    class FakeIngestionService:
        def start(self) -> None:
            events.append("ingestion-start")

        def stop(self) -> None:
            events.append("ingestion-stop")

    class FakeFinalizer:
        async def shutdown(self) -> None:
            events.append("finalizer-shutdown")

    async def close_conn() -> None:
        events.append("db-close")

    def fake_print(*args, **kwargs) -> None:
        events.append("ready")

    monkeypatch.setattr(desktop_runtime, "create_server", lambda _port: FakeServer())
    monkeypatch.setattr(desktop_runtime, "run_worker", fake_worker)
    monkeypatch.setattr(
        desktop_runtime.kovaak_run_store,
        "reconcile_mouse_traces",
        reconcile_traces,
    )
    monkeypatch.setattr(
        desktop_runtime.kovaak_run_store,
        "reconcile_run_videos",
        reconcile_videos,
    )
    monkeypatch.setattr(
        desktop_runtime,
        "create_kovaak_capture_finalizer",
        lambda: FakeFinalizer(),
    )
    monkeypatch.setattr(
        desktop_runtime,
        "create_kovaak_ingestion_service",
        lambda _loop, _finalizer, _tasks: FakeIngestionService(),
    )
    monkeypatch.setattr(desktop_runtime.db, "close_conn", close_conn)
    monkeypatch.setattr("builtins.print", fake_print)

    task = asyncio.create_task(desktop_runtime.run_runtime(stop_event=stop))
    await asyncio.wait_for(api_started.wait(), timeout=1)
    while "ready" not in events:
        await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert events == [
        "api-start",
        "worker-start",
        "trace-reconcile",
        "video-reconcile",
        "ingestion-start",
        "ready",
        "ingestion-stop",
        "finalizer-shutdown",
        "api-stop",
        "worker-stop",
        "db-close",
    ]


@pytest.mark.asyncio
async def test_finalizer_future_drain_cancels_pending_work_before_db_close() -> None:
    tracker = desktop_runtime.FinalizerFutureTracker()
    started = asyncio.Event()
    allow_write = asyncio.Event()
    writes: list[str] = []

    async def pending_finalizer() -> None:
        started.set()
        await allow_write.wait()
        writes.append("after-db-close")

    future = asyncio.run_coroutine_threadsafe(
        pending_finalizer(), asyncio.get_running_loop(),
    )
    tracker.track(future)
    await asyncio.wait_for(started.wait(), timeout=1)

    await tracker.drain()
    allow_write.set()
    await asyncio.sleep(0)

    assert future.cancelled()
    assert writes == []


@pytest.mark.asyncio
async def test_runtime_does_not_emit_ready_when_worker_exits_during_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed = False

    class FakeServer:
        should_exit = False
        started = True
        servers = [_FakeBoundServer(43127)]

        async def serve(self) -> None:
            while not self.should_exit:
                await asyncio.sleep(0)

    async def stopped_worker(stop_event: asyncio.Event) -> None:
        return None

    def fake_print(*args, **kwargs) -> None:
        nonlocal printed
        printed = True

    monkeypatch.setattr(desktop_runtime, "create_server", lambda _port: FakeServer())
    monkeypatch.setattr(desktop_runtime, "run_worker", stopped_worker)
    monkeypatch.setattr("builtins.print", fake_print)

    with pytest.raises(desktop_runtime.RuntimeStartupError, match="worker exited before ready"):
        await desktop_runtime.run_runtime(stop_event=asyncio.Event())
    assert printed is False


@pytest.mark.asyncio
async def test_ingestion_service_routes_discovery_through_one_finalizer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir()
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_STATS_DIR", stats_dir)
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_PERFORMANCE_DIR", None)
    observed = []

    class FakeFinalizer:
        async def finalize(self, discovery):
            observed.append(discovery)
            return {"id": 7}

    service = desktop_runtime.create_kovaak_ingestion_service(
        asyncio.get_running_loop(),
        FakeFinalizer(),
    )
    discovery = desktop_runtime.kovaak_ingest.KovaaKFileDiscovery(
        stem="scenario",
        stats_path=stats_dir / "Scenario Stats.csv",
    )
    future = service._watchers[0].callback(discovery)
    result = await asyncio.wrap_future(future)

    assert result == {"id": 7}
    assert observed == [discovery]


class _FakeServerShell:
    def __init__(self, port: int) -> None:
        self.servers = [_FakeBoundServer(port)]


class _FakeBoundServer:
    def __init__(self, port: int) -> None:
        self.sockets = [_FakeSocket(port)]


class _FakeSocket:
    def __init__(self, port: int) -> None:
        self._port = port

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", self._port)
