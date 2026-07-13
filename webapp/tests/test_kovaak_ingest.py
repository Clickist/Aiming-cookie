from pathlib import Path

import pytest

from webapp.backend.kovaak_ingest import (
    KovaaKDirectoryWatcher,
    NonRetryableIngestionError,
    is_performance_path,
    is_stats_path,
    normalize_kovaak_stem,
)


def test_supported_paths_and_stem_pairing():
    assert is_stats_path("1wall Stats.csv")
    assert is_stats_path("legacy.stats")
    assert not is_stats_path("notes.csv")
    assert is_performance_path("1wall Performance.perf")
    assert not is_performance_path("1wall.csv")
    assert normalize_kovaak_stem("1wall 6targets - Challenge - 2026 Stats.csv") == (
        "1wall 6targets - challenge - 2026"
    )
    assert normalize_kovaak_stem("1wall 6targets - Challenge - 2026 Performance.perf") == (
        "1wall 6targets - challenge - 2026"
    )


def test_watcher_waits_for_stable_file_and_deduplicates(tmp_path: Path):
    events = []
    watcher = KovaaKDirectoryWatcher(tmp_path, events.append, stable_scans=2)
    stats = tmp_path / "1wall Stats.csv"
    stats.write_text("first", encoding="utf-8")

    assert watcher.scan_once() == []
    first = watcher.scan_once()
    assert len(first) == 1
    assert first[0].stats_path == stats
    assert first[0].performance_path is None
    assert watcher.scan_once() == []
    assert events == first


def test_watcher_pairs_later_performance_file(tmp_path: Path):
    events = []
    watcher = KovaaKDirectoryWatcher(tmp_path, events.append, stable_scans=1)
    stats = tmp_path / "1wall Stats.csv"
    perf = tmp_path / "1wall Performance.perf"
    stats.write_text("stats", encoding="utf-8")
    assert len(watcher.scan_once()) == 1

    perf.write_bytes(b"perf")
    paired = watcher.scan_once()
    assert len(paired) == 1
    assert paired[0].stats_path == stats
    assert paired[0].performance_path == perf
    assert watcher.scan_once() == []


def test_watcher_reemits_same_path_when_observed_revision_changes(tmp_path: Path):
    events = []
    watcher = KovaaKDirectoryWatcher(tmp_path, events.append, stable_scans=1)
    stats = tmp_path / "1wall Stats.csv"
    stats.write_text("first", encoding="utf-8")

    assert len(watcher.scan_once()) == 1
    stats.write_text("second revision", encoding="utf-8")

    changed = watcher.scan_once()
    assert len(changed) == 1
    assert changed[0].stats_path == stats
    assert len(events) == 2


def test_watcher_retries_when_callback_raises_after_file_is_stable(tmp_path: Path):
    attempts = []

    def fail_once(discovery):
        attempts.append(discovery)
        if len(attempts) == 1:
            raise RuntimeError("transient ingestion failure")

    watcher = KovaaKDirectoryWatcher(tmp_path, fail_once, stable_scans=1)
    (tmp_path / "1wall Stats.csv").write_text("stats", encoding="utf-8")

    assert watcher.scan_once() == []
    retried = watcher.scan_once()

    assert len(attempts) == 2
    assert len(retried) == 1
    assert watcher.scan_once() == []


def test_watcher_retries_after_async_callback_future_fails(tmp_path: Path):
    from concurrent.futures import Future

    futures = []

    def ingest_later(_discovery):
        future = Future()
        futures.append(future)
        return future

    watcher = KovaaKDirectoryWatcher(tmp_path, ingest_later, stable_scans=1)
    (tmp_path / "1wall Stats.csv").write_text("stats", encoding="utf-8")

    first = watcher.scan_once()
    assert len(first) == 1
    assert watcher.scan_once() == []

    futures[0].set_exception(RuntimeError("async ingestion failure"))
    retried = watcher.scan_once()

    assert len(futures) == 2
    assert retried == first
    futures[1].set_result(None)
    assert watcher.scan_once() == []


def test_desktop_ingestion_bridge_returns_future_to_watcher(tmp_path: Path, monkeypatch):
    import asyncio
    from concurrent.futures import Future

    from webapp.backend import desktop_runtime

    future = Future()
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_STATS_DIR", tmp_path)
    monkeypatch.setattr(desktop_runtime.config, "KOVAAK_PERFORMANCE_DIR", None)

    async def fake_ingest_discovery(*_args, **_kwargs):
        return None

    def fake_run_coroutine_threadsafe(coroutine, _loop):
        coroutine.close()
        return future

    monkeypatch.setattr(
        desktop_runtime.kovaak_run_store,
        "ingest_discovery",
        fake_ingest_discovery,
    )
    monkeypatch.setattr(
        asyncio,
        "run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    loop = asyncio.new_event_loop()
    try:
        service = desktop_runtime.create_kovaak_ingestion_service(loop)
        watcher = service._watchers[0]
        stats = tmp_path / "1wall Stats.csv"
        stats.write_text("stats", encoding="utf-8")

        assert watcher.scan_once() == []
        assert len(watcher.scan_once()) == 1
        future.set_exception(RuntimeError("async ingestion failure"))
        assert len(watcher.scan_once()) == 1
    finally:
        loop.close()


def test_watcher_does_not_retry_non_retryable_identity_conflict(tmp_path: Path):
    from concurrent.futures import Future

    future = Future()
    watcher = KovaaKDirectoryWatcher(tmp_path, lambda _item: future, stable_scans=1)
    stats = tmp_path / "1wall Stats.csv"
    stats.write_text("stats", encoding="utf-8")

    assert len(watcher.scan_once()) == 1
    future.set_exception(NonRetryableIngestionError("pairing_conflict"))
    assert watcher.scan_once() == []


@pytest.mark.asyncio
async def test_runtime_reconciles_traces_before_starting_ingestion(monkeypatch):
    import asyncio

    from webapp.backend import desktop_runtime

    events: list[str] = []
    stop = asyncio.Event()

    class FakeServer:
        should_exit = False
        started = True
        servers = [type("Bound", (), {"sockets": [type("Socket", (), {
            "getsockname": lambda self: ("127.0.0.1", 43127),
        })()]})()]

        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0)

    class FakeIngestionService:
        def start(self):
            events.append("ingestion-start")

        def stop(self):
            events.append("ingestion-stop")

    async def fake_worker(worker_stop):
        await worker_stop.wait()

    async def fake_reconcile(_data_root):
        events.append("reconcile")
        return {"attached": 0, "unavailable": 0, "quarantined": 0}

    def fake_print(*_args, **_kwargs):
        events.append("ready")
        stop.set()

    monkeypatch.setattr(desktop_runtime, "create_server", lambda _port: FakeServer())
    monkeypatch.setattr(desktop_runtime, "run_worker", fake_worker)
    monkeypatch.setattr(
        desktop_runtime, "create_kovaak_ingestion_service",
        lambda _loop: FakeIngestionService(),
    )
    monkeypatch.setattr(
        desktop_runtime.kovaak_run_store,
        "reconcile_mouse_traces",
        fake_reconcile,
    )
    monkeypatch.setattr("builtins.print", fake_print)

    await asyncio.wait_for(desktop_runtime.run_runtime(stop_event=stop), timeout=1)

    assert events[:3] == ["reconcile", "ingestion-start", "ready"]
