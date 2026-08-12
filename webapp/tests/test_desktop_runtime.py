from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from webapp.backend import desktop_runtime, routes
from webapp.backend.kovaak_ingest import NonRetryableIngestionError
from webapp.backend.native_capture_client import NativeCaptureRetryableError


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
        async def finalizing_capture_session(self):
            return None

        async def release_capture_session(self, capture_session_id: str) -> bool:
            raise AssertionError(f"unexpected release for {capture_session_id}")

        async def shutdown(self) -> None:
            events.append("finalizer-shutdown")

    class FakeCaptureExitReleaseTracker:
        async def monitor(self, _finalizer, _finalizer_futures, _stop_event):
            events.append("capture-monitor-start")
            try:
                await asyncio.Future()
            finally:
                events.append("capture-monitor-stop")

        async def drain(self) -> None:
            events.append("capture-release-drain")

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
    monkeypatch.setattr(
        desktop_runtime,
        "CaptureExitReleaseTracker",
        FakeCaptureExitReleaseTracker,
    )
    monkeypatch.setattr(desktop_runtime.db, "close_conn", close_conn)
    monkeypatch.setattr("builtins.print", fake_print)

    task = asyncio.create_task(desktop_runtime.run_runtime(stop_event=stop))
    await asyncio.wait_for(api_started.wait(), timeout=1)
    while "ready" not in events:
        await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert [
        event for event in events
        if event not in {
            "capture-monitor-start",
            "capture-monitor-stop",
            "capture-release-drain",
        }
    ] == [
        "api-start",
        "worker-start",
        "trace-reconcile",
        "video-reconcile",
        "ingestion-start",
        "ready",
        "ingestion-stop",
        "api-stop",
        "worker-stop",
        "finalizer-shutdown",
        "db-close",
    ]
    assert events.index("capture-monitor-stop") < events.index("capture-release-drain")
    assert events.index("capture-release-drain") < events.index("ingestion-stop")
    assert events.index("ready") < events.index("capture-monitor-start")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_level"),
    [
        ("capture_control_unavailable", logging.INFO),
        ("capture_control_timeout", logging.ERROR),
    ],
)
async def test_capture_status_distinguishes_shutdown_unavailable_from_real_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_code: str,
    expected_level: int,
) -> None:
    class FailingNativeCaptureClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            raise NativeCaptureRetryableError(error_code)

    async def no_runs(_user_id: str) -> list[dict]:
        return []

    monkeypatch.setattr(routes.config, "NATIVE_CAPTURE_CONTROL_ADDR", "127.0.0.1:1")
    monkeypatch.setattr(routes.config, "NATIVE_CAPTURE_CONTROL_SECRET", "0" * 64)
    monkeypatch.setattr(routes, "NativeCaptureClient", FailingNativeCaptureClient)
    monkeypatch.setattr(
        routes.kovaak_run_store,
        "list_kovaak_run_summaries",
        no_runs,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(desktop_shutdown_requested=True),
        ),
    )

    with caplog.at_level(logging.INFO, logger=routes.__name__):
        response = await routes.get_capture_status(request=request, _=None)

    assert response.availability == "unavailable"
    records = [record for record in caplog.records if record.name == routes.__name__]
    assert len(records) == 1
    assert records[0].levelno == expected_level
    assert records[0].exc_info is None
    if error_code == "capture_control_unavailable":
        assert error_code not in records[0].getMessage()
    else:
        assert error_code in records[0].getMessage() or records[0].exc_info is not None


@pytest.mark.asyncio
async def test_capture_status_keeps_unexpected_failure_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenNativeCaptureClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            raise RuntimeError("unexpected native failure")

    async def no_runs(_user_id: str) -> list[dict]:
        return []

    monkeypatch.setattr(routes.config, "NATIVE_CAPTURE_CONTROL_ADDR", "127.0.0.1:1")
    monkeypatch.setattr(routes.config, "NATIVE_CAPTURE_CONTROL_SECRET", "0" * 64)
    monkeypatch.setattr(routes, "NativeCaptureClient", BrokenNativeCaptureClient)
    monkeypatch.setattr(routes.kovaak_run_store, "list_kovaak_run_summaries", no_runs)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(desktop_shutdown_requested=False)),
    )

    with caplog.at_level(logging.ERROR, logger=routes.__name__):
        response = await routes.get_capture_status(request=request, _=None)

    assert response.availability == "unavailable"
    records = [record for record in caplog.records if record.name == routes.__name__]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None


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
async def test_capture_exit_release_waits_for_known_finalizers_then_releases_once() -> None:
    tracker = desktop_runtime.FinalizerFutureTracker()
    started = asyncio.Event()
    finish = asyncio.Event()
    released = asyncio.Event()

    class FakeFinalizer:
        async def finalizing_capture_session(self):
            return "session-1"

        async def release_capture_session(self, capture_session_id: str) -> bool:
            assert capture_session_id == "session-1"
            released.set()
            return True

    async def pending_finalizer() -> dict:
        started.set()
        await finish.wait()
        return {"id": 1}

    future = asyncio.run_coroutine_threadsafe(
        pending_finalizer(), asyncio.get_running_loop(),
    )
    tracker.track(future)
    await asyncio.wait_for(started.wait(), timeout=1)

    releases = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=1)
    await releases.observe(FakeFinalizer(), tracker)
    assert not released.is_set()

    finish.set()
    await asyncio.wait_for(released.wait(), timeout=1)
    await releases.drain()
    assert future.result() == {"id": 1}


@pytest.mark.asyncio
async def test_capture_exit_release_uses_hard_grace_when_no_finalizer_arrives() -> None:
    released: list[str] = []

    class FakeFinalizer:
        async def finalizing_capture_session(self):
            return "session-1"

        async def release_capture_session(self, capture_session_id: str) -> bool:
            released.append(capture_session_id)
            return True

    releases = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=0)
    await releases.observe(FakeFinalizer(), desktop_runtime.FinalizerFutureTracker())
    assert releases._task is not None
    await asyncio.wait_for(releases._task, timeout=1)

    assert released == ["session-1"]


@pytest.mark.asyncio
async def test_capture_exit_release_drain_cancels_pending_hard_grace_promptly() -> None:
    class FakeFinalizer:
        async def finalizing_capture_session(self):
            return "session-1"

        async def release_capture_session(self, capture_session_id: str) -> bool:
            raise AssertionError(f"unexpected release for {capture_session_id}")

    releases = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=30)
    await releases.observe(FakeFinalizer(), desktop_runtime.FinalizerFutureTracker())
    assert releases._task is not None

    await releases.drain()

    assert releases._task.cancelled()


@pytest.mark.asyncio
async def test_capture_exit_monitor_waits_before_the_first_status_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    stop = asyncio.Event()

    class FakeFinalizer:
        async def finalizing_capture_session(self):
            nonlocal calls
            calls += 1
            return None

    monkeypatch.setattr(
        desktop_runtime,
        "CAPTURE_EXIT_STATUS_POLL_SECONDS",
        0.03,
    )
    monitor = desktop_runtime.CaptureExitReleaseTracker()
    task = asyncio.create_task(
        monitor.monitor(FakeFinalizer(), desktop_runtime.FinalizerFutureTracker(), stop)
    )

    await asyncio.sleep(0.015)
    assert calls == 0
    await asyncio.sleep(0.1)
    assert calls >= 1
    stop.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_capture_exit_monitor_releases_promptly_when_finalizers_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = desktop_runtime.FinalizerFutureTracker()
    started = asyncio.Event()
    finish = asyncio.Event()
    observed = asyncio.Event()
    released = asyncio.Event()
    stop = asyncio.Event()

    class FakeFinalizer:
        async def finalizing_capture_session(self):
            observed.set()
            return "session-1"

        async def release_capture_session(self, capture_session_id: str) -> bool:
            assert capture_session_id == "session-1"
            released.set()
            return True

    async def pending_finalizer() -> dict:
        started.set()
        await finish.wait()
        return {"id": 1}

    monkeypatch.setattr(
        desktop_runtime,
        "CAPTURE_EXIT_STATUS_POLL_SECONDS",
        0.01,
    )
    future = asyncio.run_coroutine_threadsafe(
        pending_finalizer(), asyncio.get_running_loop(),
    )
    tracker.track(future)
    await asyncio.wait_for(started.wait(), timeout=1)

    monitor = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=0.3)
    task = asyncio.create_task(
        monitor.monitor(FakeFinalizer(), tracker, stop)
    )

    try:
        await asyncio.wait_for(observed.wait(), timeout=1)
        assert tracker.has_pending()

        finish.set()
        await asyncio.wait_for(released.wait(), timeout=0.15)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        await monitor.drain()
    assert future.result() == {"id": 1}


@pytest.mark.asyncio
async def test_capture_exit_monitor_propagates_status_failure_after_finalizers_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFinalizer:
        async def finalizing_capture_session(self):
            raise NativeCaptureRetryableError("capture_export_failed")

    monkeypatch.setattr(
        desktop_runtime,
        "CAPTURE_EXIT_STATUS_POLL_SECONDS",
        0.001,
    )
    monitor = desktop_runtime.CaptureExitReleaseTracker()
    with pytest.raises(NativeCaptureRetryableError, match="capture_export_failed"):
        await asyncio.wait_for(
            monitor.monitor(
                FakeFinalizer(), desktop_runtime.FinalizerFutureTracker(), asyncio.Event(),
            ),
            timeout=1,
        )


@pytest.mark.asyncio
async def test_capture_exit_release_does_not_release_while_kovaak_is_alive() -> None:
    class FakeFinalizer:
        async def finalizing_capture_session(self):
            return None

        async def release_capture_session(self, capture_session_id: str) -> bool:
            raise AssertionError(f"unexpected release for {capture_session_id}")

    releases = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=0)
    await releases.observe(FakeFinalizer(), desktop_runtime.FinalizerFutureTracker())
    await releases.drain()


@pytest.mark.asyncio
async def test_capture_exit_release_allows_the_next_capture_session_without_app_exit() -> None:
    released: list[str] = []

    class FakeFinalizer:
        current_session = "session-1"

        async def finalizing_capture_session(self):
            return self.current_session

        async def release_capture_session(self, capture_session_id: str) -> bool:
            released.append(capture_session_id)
            self.current_session = None
            return True

    finalizer = FakeFinalizer()
    releases = desktop_runtime.CaptureExitReleaseTracker(grace_seconds=0)
    await releases.observe(finalizer, desktop_runtime.FinalizerFutureTracker())
    assert releases._task is not None
    await asyncio.wait_for(releases._task, timeout=1)

    finalizer.current_session = "session-2"
    await releases.observe(finalizer, desktop_runtime.FinalizerFutureTracker())
    assert releases._task is not None
    await asyncio.wait_for(releases._task, timeout=1)

    assert released == ["session-1", "session-2"]


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


@pytest.mark.asyncio
async def test_ingestion_service_logs_unexpected_future_failure_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir()
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_STATS_DIR", stats_dir)
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_PERFORMANCE_DIR", None)
    finalized = asyncio.Event()

    class FakeFinalizer:
        async def finalize(self, _discovery):
            finalized.set()
            raise RuntimeError("unexpected ingestion failure")

    service = desktop_runtime.create_kovaak_ingestion_service(
        asyncio.get_running_loop(),
        FakeFinalizer(),
    )
    caplog.set_level(logging.ERROR)

    (stats_dir / "Broken Stats.csv").write_text("stats", encoding="utf-8")
    assert service._watchers[0].scan_once() == []
    assert len(service._watchers[0].scan_once()) == 1
    await asyncio.wait_for(finalized.wait(), timeout=1)
    await asyncio.sleep(0)

    records = [
        record for record in caplog.records
        if "KovaaK" in record.getMessage() and record.exc_info is not None
    ]
    assert len(records) == 1


@pytest.mark.asyncio
async def test_ingestion_service_treats_waiting_for_sources_as_expected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir()
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_STATS_DIR", stats_dir)
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_PERFORMANCE_DIR", None)
    finalized = asyncio.Event()

    class FakeFinalizer:
        async def finalize(self, _discovery):
            finalized.set()
            raise NonRetryableIngestionError(
                "waiting_for_sources", code="waiting_for_sources",
            )

    service = desktop_runtime.create_kovaak_ingestion_service(
        asyncio.get_running_loop(),
        FakeFinalizer(),
    )
    caplog.set_level(logging.INFO)

    (stats_dir / "Waiting Stats.csv").write_text("stats", encoding="utf-8")
    assert service._watchers[0].scan_once() == []
    assert len(service._watchers[0].scan_once()) == 1
    await asyncio.wait_for(finalized.wait(), timeout=1)
    await asyncio.sleep(0)

    records = [
        record for record in caplog.records
        if "KovaaK" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].exc_info is None


@pytest.mark.asyncio
async def test_ingestion_service_serializes_heavy_finalizers_and_drain_cancels_queued_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir()
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_STATS_DIR", stats_dir)
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_PERFORMANCE_DIR", None)
    first_started = asyncio.Event()
    allow_first = asyncio.Event()
    second_started = asyncio.Event()

    class FakeFinalizer:
        async def finalize(self, discovery):
            if discovery.stem == "first":
                first_started.set()
                await allow_first.wait()
            else:
                second_started.set()
            return {"stem": discovery.stem}

    tracker = desktop_runtime.FinalizerFutureTracker()
    service = desktop_runtime.create_kovaak_ingestion_service(
        asyncio.get_running_loop(), FakeFinalizer(), tracker,
    )
    first = desktop_runtime.kovaak_ingest.KovaaKFileDiscovery(
        stem="first", stats_path=stats_dir / "First Stats.csv",
    )
    second = desktop_runtime.kovaak_ingest.KovaaKFileDiscovery(
        stem="second", stats_path=stats_dir / "Second Stats.csv",
    )
    first_future = service._watchers[0].callback(first)
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second_future = service._watchers[0].callback(second)
    await asyncio.sleep(0)
    assert second_started.is_set() is False

    await tracker.drain()

    assert first_future.cancelled()
    assert second_future.cancelled()
    assert second_started.is_set() is False


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
