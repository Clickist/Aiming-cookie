from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceHeader,
)
from webapp.backend import file_store, kovaak_run_store
from webapp.backend.kovaak_capture_finalizer import (
    KovaaKCaptureFinalizer,
)
from webapp.backend.kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
    RetryableIngestionError,
)
from webapp.backend.native_capture_client import (
    NativeCaptureProtocolError,
    NativeCaptureRetryableError,
    NativeCaptureTerminalError,
)


class FakeNativeCaptureClient:
    def __init__(
        self,
        data_root: Path,
        *,
        terminal_code: str | None = None,
        lose_first_response: bool = False,
    ) -> None:
        self.data_root = data_root
        self.terminal_code = terminal_code
        self.lose_first_response = lose_first_response
        self.export_calls: list[dict] = []
        self.flush_calls: list[str] = []
        self.release_calls: list[str] = []
        self.capture_session_id = "session-1"
        self.raw_snapshot_covered_through_epoch_ms = 2**62
        self.raw_snapshot_capture_session_start_epoch_ms = 0
        self.raw_snapshot_queue_dropped_points = 0
        self.raw_snapshot_queue_drop_first_epoch_ms: int | None = None
        self.raw_snapshot_queue_drop_last_epoch_ms: int | None = None
        self.raw_snapshot_ring_expired_points = 0
        self.raw_snapshot_ring_expired_through_epoch_ms: int | None = None
        self.publication_count = 0
        self.phase = "capturing"
        self.kovaak_process_present = True
        self.release_error: Exception | None = None

    def status(self) -> dict:
        return {
            "enabled": True,
            "phase": self.phase,
            "captureSessionId": self.capture_session_id,
            "kovaakProcessPresent": self.kovaak_process_present,
            "windowHandle": 123,
            "reason": None,
            "raw": {
                "state": "finalizing" if self.phase == "finalizing" else "capturing",
                "reason": None,
            },
            "video": {
                "state": "finalizing" if self.phase == "finalizing" else "capturing",
                "reason": None,
            },
        }

    def export_replay(self, **request) -> dict:
        self.export_calls.append(dict(request))
        if self.terminal_code is not None:
            raise NativeCaptureTerminalError(self.terminal_code)
        request_digest = hashlib.sha256(
            (
                "capture_export.v1|"
                f"{request['request_id']}|{request['run_id']}|"
                f"{request['capture_session_id']}|{request['start_epoch_ms']}|"
                f"{request['end_epoch_ms']}"
            ).encode("utf-8")
        ).hexdigest()
        video = (
            self.data_root
            / "runs"
            / str(request["run_id"])
            / f"video-{request['request_id']}.mp4"
        )
        video.parent.mkdir(parents=True, exist_ok=True)
        contents = b"native-run-owned-video"
        duration_100ns = (
            request["end_epoch_ms"] - request["start_epoch_ms"]
        ) * 10_000
        receipt_path = video.with_name(f"{video.stem}.receipt.json")
        receipt = {
            "version": "capture_receipt.v1",
            "requestDigest": request_digest,
            "requestId": request["request_id"],
            "runId": request["run_id"],
            "captureSessionId": request["capture_session_id"],
            "startEpochMs": request["start_epoch_ms"],
            "endEpochMs": request["end_epoch_ms"],
            "replay": {
                "requestedStart100ns": 20_000_000,
                "requestedEnd100ns": 20_000_000 + duration_100ns,
                "decodeStart100ns": 19_000_000,
                "visibleDuration100ns": duration_100ns,
                "decodePreroll100ns": 1_000_000,
                "packetCount": 60,
                "encodedBytes": len(contents),
                "reencodedFrames": 0,
                "captureClock": {
                    "utcEpochMs": request["start_epoch_ms"],
                    "qpcNs": 2_000_000_000,
                    "clockSource": "utc_epoch_ms+qpc+wgc_system_relative_time",
                    "timebaseVersion": "time_alignment.v2",
                },
            },
            "file": {
                "size": len(contents),
                "digest": hashlib.sha256(contents).hexdigest(),
            },
        }
        if video.exists() and receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        else:
            video.write_bytes(contents)
            receipt_path.write_text(
                json.dumps(receipt, separators=(",", ":")),
                encoding="utf-8",
            )
            self.publication_count += 1
        if self.lose_first_response and len(self.export_calls) == 1:
            raise NativeCaptureRetryableError("capture_control_response_lost")
        return {
            "requestDigest": request_digest,
            "captureSessionId": request["capture_session_id"],
            "requestedStartEpochMs": request["start_epoch_ms"],
            "requestedEndEpochMs": request["end_epoch_ms"],
            "replay": receipt["replay"],
            "file": receipt["file"],
        }

    def flush_raw_snapshot(self, capture_session_id: str) -> dict:
        self.flush_calls.append(capture_session_id)
        return {
            "receiptVersion": "raw_snapshot_receipt.v2",
            "captureSessionStartEpochMs": self.raw_snapshot_capture_session_start_epoch_ms,
            "coveredThroughEpochMs": self.raw_snapshot_covered_through_epoch_ms,
            "snapshotAtEpochMs": self.raw_snapshot_covered_through_epoch_ms + 1,
            "pointCount": 1,
            "queueDroppedPoints": self.raw_snapshot_queue_dropped_points,
            "queueDropFirstEpochMs": self.raw_snapshot_queue_drop_first_epoch_ms,
            "queueDropLastEpochMs": self.raw_snapshot_queue_drop_last_epoch_ms,
            "ringExpiredPoints": self.raw_snapshot_ring_expired_points,
            "ringExpiredThroughEpochMs": self.raw_snapshot_ring_expired_through_epoch_ms,
            "clockSource": "utc_epoch_ms+qpc",
            "timebaseVersion": "time_alignment.v2",
        }

    def release_capture_session(self, capture_session_id: str) -> dict:
        self.release_calls.append(capture_session_id)
        if self.release_error is not None:
            raise self.release_error
        self.capture_session_id = None
        self.phase = "waiting_for_kovaak"
        return self.status()


def _configure_parsers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pause_count: str = "0",
    start_epoch_ms: int = 1_000,
    time_limit: float = 60.0,
    timescale: float = 1.0,
    bot_max_lives: tuple[int, ...] = (),
    stats_event_times: tuple[float, ...] = (),
) -> None:
    stats = SimpleNamespace(
        file_name="Scenario Stats.csv",
        scenario="Scenario",
        summary={"Scenario": "Scenario", "Pause Count": pause_count},
        config={},
        kills=pd.DataFrame({"time_s": list(stats_event_times)}),
    )
    performance = PerformanceData(
        header=PerformanceHeader(
            scenario_name="Scenario",
            challenge_start_utc=start_epoch_ms,
            challenge_profile=ChallengeProfile(
                time_limit=time_limit,
                timescale=timescale,
                bot_max_lives=bot_max_lives,
            ),
        ),
    )
    monkeypatch.setattr(kovaak_run_store, "parse_stats_csv", lambda _path: stats)
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: performance,
    )


def _finalizer(
    tmp_path: Path,
    client: FakeNativeCaptureClient,
    *,
    raw_snapshot: Path | None = None,
) -> KovaaKCaptureFinalizer:
    return KovaaKCaptureFinalizer(
        native_client=client,
        data_root=tmp_path / "data",
        raw_input_snapshot_path=raw_snapshot or tmp_path / "missing-raw.bin",
        user_id="u1",
    )


@pytest.mark.asyncio
async def test_exit_release_requires_the_same_finalizing_session_after_process_exit(
    tmp_path: Path,
) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = "finalizing"
    client.kovaak_process_present = False
    finalizer = _finalizer(tmp_path, client)

    assert await finalizer.finalizing_capture_session() == "session-1"
    assert await finalizer.release_capture_session("other-session") is False
    assert client.release_calls == []
    assert await finalizer.release_capture_session("session-1") is True
    assert client.release_calls == ["session-1"]
    assert await finalizer.release_capture_session("session-1") is False


@pytest.mark.asyncio
async def test_exit_release_releases_finalizing_session_after_fast_kovaak_restart(
    tmp_path: Path,
) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = "finalizing"
    finalizer = _finalizer(tmp_path, client)

    assert await finalizer.finalizing_capture_session() == "session-1"
    assert await finalizer.release_capture_session("session-1") is True
    assert client.release_calls == ["session-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["capturing", "degraded", "waiting_for_kovaak"])
async def test_exit_release_requires_finalizing_phase(tmp_path: Path, phase: str) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = phase
    finalizer = _finalizer(tmp_path, client)

    assert await finalizer.finalizing_capture_session() is None
    assert await finalizer.release_capture_session("session-1") is False
    assert client.release_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_records"),
    [
        ("capture_control_unavailable", 0),
        ("capture_control_timeout", 1),
    ],
)
async def test_exit_monitor_silently_retries_transient_unavailable_but_reports_real_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error_code: str,
    expected_records: int,
) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")

    def fail_status() -> dict:
        raise NativeCaptureRetryableError(error_code)

    client.status = fail_status  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO):
        assert await _finalizer(tmp_path, client).finalizing_capture_session() is None

    records = [
        record for record in caplog.records
        if record.name == "webapp.backend.kovaak_capture_finalizer"
    ]
    assert len(records) == expected_records
    if records:
        assert records[0].levelno == logging.WARNING
        assert error_code in records[0].getMessage()


@pytest.mark.asyncio
async def test_shutdown_releases_only_matching_finalizing_session_without_mutating_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)
    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="shutdown-release",
        stats_path=stats,
        performance_path=performance,
    ))
    before = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    client.phase = "finalizing"

    await finalizer.shutdown()

    assert client.release_calls == ["session-1"]
    assert client.phase == "waiting_for_kovaak"
    assert await kovaak_run_store.get_kovaak_run(run["id"], "u1") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["capturing", "degraded", "waiting_for_kovaak"])
async def test_shutdown_does_not_release_non_finalizing_session(
    tmp_path: Path,
    phase: str,
) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = phase

    await _finalizer(tmp_path, client).shutdown()

    assert client.release_calls == []


@pytest.mark.asyncio
async def test_shutdown_without_native_client_is_a_noop(tmp_path: Path) -> None:
    await _finalizer(tmp_path, None).shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_level"),
    [
        ("capture_control_unavailable", logging.INFO),
        ("capture_control_timeout", logging.WARNING),
    ],
)
async def test_shutdown_logs_expected_endpoint_loss_separately_from_real_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error_code: str,
    expected_level: int,
) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")

    def fail_status() -> dict:
        raise NativeCaptureRetryableError(error_code)

    client.status = fail_status  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO):
        await _finalizer(tmp_path, client).shutdown()

    records = [
        record for record in caplog.records
        if record.name == "webapp.backend.kovaak_capture_finalizer"
    ]
    assert len(records) == 1
    assert records[0].levelno == expected_level
    assert records[0].exc_info is None
    if error_code == "capture_control_unavailable":
        assert error_code not in records[0].getMessage()
    else:
        assert error_code in records[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NativeCaptureRetryableError("capture_unavailable"),
        NativeCaptureTerminalError("capture_session_mismatch"),
        NativeCaptureProtocolError("capture_control_response_schema_invalid"),
    ],
)
async def test_shutdown_release_failure_does_not_mutate_persisted_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)
    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="shutdown-release-failure",
        stats_path=stats,
        performance_path=performance,
    ))
    before = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    client.phase = "finalizing"
    client.release_error = error

    await finalizer.shutdown()

    assert client.release_calls == ["session-1"]
    assert await kovaak_run_store.get_kovaak_run(run["id"], "u1") == before


@pytest.mark.parametrize(
    ("first_kind", "timescale", "expected_duration_ms"),
    [
        ("stats", 1.0, 60_000),
        ("performance", 1.0, 60_000),
        ("stats", 0.5, 120_000),
    ],
)
@pytest.mark.asyncio
async def test_stats_and_performance_orders_converge_on_one_idempotent_video_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_kind: str,
    timescale: float,
    expected_duration_ms: int,
) -> None:
    _configure_parsers(monkeypatch, timescale=timescale)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stable-stats")
    performance.write_bytes(b"stable-performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)
    first = KovaaKFileDiscovery(
        stem="scenario",
        stats_path=stats if first_kind == "stats" else None,
        performance_path=performance if first_kind == "performance" else None,
    )
    second = KovaaKFileDiscovery(
        stem="scenario",
        stats_path=stats if first_kind == "performance" else None,
        performance_path=performance if first_kind == "stats" else None,
    )

    # 2026-10-08 CSV-only 兼容：stats-only 不再 waiting_for_sources（.perf 永不
    # 出现的机器曾因此永久 pending）。CSV 信息不足（stub 无 Challenge Start）时
    # 诚实落终态 alignment unavailable；.perf 到场后同一 run 收敛到完整终局。
    # performance-only 仍是 waiting_for_sources（没有任何 stats 源可重建）。
    if first_kind == "stats":
        partial = await finalizer.finalize(first)
        assert partial["finalization_state"] == "finalized"
        assert partial["video_state"] == "unavailable"
    else:
        with pytest.raises(NonRetryableIngestionError, match="waiting_for_sources"):
            await finalizer.finalize(first)
    run = await finalizer.finalize(second)
    duplicate = await finalizer.finalize(second)

    assert run["id"] == duplicate["id"]
    assert run["video_state"] == "attached"
    assert run["finalization_state"] == "finalized"
    assert len(client.export_calls) == 1
    request = client.export_calls[0]
    assert request["end_epoch_ms"] - request["start_epoch_ms"] == expected_duration_ms
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1
    assert file_store.list_dir("sessions") == []


@pytest.mark.parametrize("first_kind", ["stats", "performance"])
@pytest.mark.asyncio
async def test_watcher_consumes_missing_source_once_then_finalizes_counterpart_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_kind: str,
) -> None:
    import asyncio

    from webapp.backend import config
    from webapp.backend.kovaak_ingest import KovaaKDirectoryWatcher

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    tasks: list[asyncio.Task] = []

    def finalize(discovery: KovaaKFileDiscovery) -> asyncio.Task:
        task = asyncio.create_task(finalizer.finalize(discovery))
        tasks.append(task)
        return task

    watcher = KovaaKDirectoryWatcher(tmp_path, finalize, stable_scans=1)
    first = stats if first_kind == "stats" else performance
    counterpart = performance if first_kind == "stats" else stats
    first.write_bytes(b"stable-first")

    assert len(watcher.scan_once()) == 1
    first_result = await asyncio.gather(tasks[-1], return_exceptions=True)
    await asyncio.sleep(0)
    # 2026-10-08 CSV-only：stats-only 首次发现落劣质终局（stub 无 Challenge
    # Start → alignment unavailable）而非 waiting 异常；performance-only 仍
    # waiting。两种首源 watcher 都不重发。
    if first_kind == "stats":
        assert not isinstance(first_result[0], NonRetryableIngestionError)
    else:
        assert isinstance(first_result[0], NonRetryableIngestionError)
    runs_before = len(await kovaak_run_store.list_kovaak_runs("u1"))

    assert watcher.scan_once() == []
    assert len(tasks) == 1
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == runs_before

    counterpart.write_bytes(b"stable-counterpart")
    assert len(watcher.scan_once()) == 1
    paired_result = await asyncio.gather(tasks[-1], return_exceptions=True)
    await asyncio.sleep(0)

    assert not isinstance(paired_result[0], BaseException)
    assert watcher.scan_once() == []
    assert len(tasks) == 2
    assert len(client.export_calls) == 1
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1


@pytest.mark.asyncio
async def test_desktop_ingestion_slice_persists_a_run_for_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A discovered stable pair becomes a History Run through the real desktop bridge."""
    import asyncio

    from webapp.backend import config, desktop_runtime

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Session Stats.csv"
    performance = tmp_path / "Session Performance.perf"
    stats.write_bytes(b"stable-stats")
    performance.write_bytes(b"stable-performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)
    loop = asyncio.get_running_loop()
    service = desktop_runtime.create_kovaak_ingestion_service(
        loop,
        finalizer,
    )
    service.reconfigure(
        stats_dirs=[tmp_path],
        performance_dirs=[tmp_path],
        source="confirmed",
    )
    watcher = service._watchers[0]

    # First scan observes the files; second scan confirms stability and emits
    # the pair. Grab the bridge's async future so we can await it deterministically.
    assert watcher.scan_once() == []
    discovery = watcher.scan_once()[0]
    future = watcher.callback(discovery)
    await asyncio.wrap_future(future)

    runs = await kovaak_run_store.list_kovaak_run_summaries("u1")
    assert len(runs) == 1
    assert runs[0]["scenario"] == "Scenario"
    assert runs[0]["finalization_state"] == "finalized"
    assert len(client.export_calls) == 1



@pytest.mark.asyncio
async def test_pause_fails_closed_before_native_export(tmp_path: Path, monkeypatch) -> None:
    _configure_parsers(monkeypatch, pause_count="1")
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"paused-stats")
    performance.write_bytes(b"paused-performance")
    client = FakeNativeCaptureClient(tmp_path / "data")

    run = await _finalizer(tmp_path, client).finalize(KovaaKFileDiscovery(
        stem="paused",
        stats_path=stats,
        performance_path=performance,
    ))

    assert client.export_calls == []
    assert run["alignment_state"] == "unavailable"
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_pause_unsupported"
    assert run["finalization_state"] == "finalized"
    assert kovaak_run_store.derive_run_readiness(run)["state"] == "incomplete_evidence"


@pytest.mark.parametrize(
    ("timescale", "last_event", "expected_duration_ms"),
    [
        (1.0, 59.330, 60_000),
        (0.7, 85.694, 85_714),
    ],
)
@pytest.mark.asyncio
async def test_all_zero_bot_lives_use_timer_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timescale: float,
    last_event: float,
    expected_duration_ms: int,
) -> None:
    _configure_parsers(
        monkeypatch,
        timescale=timescale,
        bot_max_lives=(0, 0, 0),
        stats_event_times=(last_event,),
    )
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")

    run = await _finalizer(tmp_path, client).finalize(KovaaKFileDiscovery(
        stem=f"timer-{timescale}",
        stats_path=stats,
        performance_path=performance,
    ))

    assert run["alignment_summary"]["end_source"] == "timer_profile"
    assert run["alignment_summary"]["duration_ms"] == expected_duration_ms
    assert client.export_calls[0]["end_epoch_ms"] - 1_000 == expected_duration_ms


@pytest.mark.asyncio
async def test_complete_pair_waits_for_native_raw_snapshot_barrier_without_reexporting_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    # The player can be stationary for nearly the entire canonical tail.
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.raw_snapshot_covered_through_epoch_ms = 1_999
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="raw-barrier",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(RetryableIngestionError, match="coverage"):
        await finalizer.finalize(discovery)

    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["trace_state"] == "pending"
    assert len(client.export_calls) == 1
    assert client.flush_calls == ["session-1"]

    client.raw_snapshot_covered_through_epoch_ms = 2_000
    attached = await finalizer.finalize(discovery)

    assert attached["trace_state"] == "attached"
    assert attached["video_state"] == "attached"
    assert len(client.export_calls) == 1
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1
    assert client.flush_calls == ["session-1", "session-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quality", "expected_trace_state", "expected_error"),
    [
        pytest.param(
            {},
            "attached",
            None,
            id="canonical-normalization-is-not-loss",
        ),
        (
            {
                "raw_snapshot_queue_dropped_points": 1,
                "raw_snapshot_queue_drop_first_epoch_ms": 1_100,
                "raw_snapshot_queue_drop_last_epoch_ms": 1_100,
            },
            "unavailable",
            "trace_raw_queue_dropped",
        ),
        (
            {
                "raw_snapshot_ring_expired_points": 1,
                "raw_snapshot_ring_expired_through_epoch_ms": 1_000,
            },
            "unavailable",
            "trace_raw_ring_expired",
        ),
        (
            {"raw_snapshot_capture_session_start_epoch_ms": 1_001},
            "unavailable",
            "trace_raw_window_coverage_gap",
        ),
        (
            {
                "raw_snapshot_queue_dropped_points": 1,
                "raw_snapshot_queue_drop_first_epoch_ms": 900,
                "raw_snapshot_queue_drop_last_epoch_ms": 900,
                "raw_snapshot_ring_expired_points": 1,
                "raw_snapshot_ring_expired_through_epoch_ms": 999,
            },
            "attached",
            None,
        ),
    ],
)
async def test_raw_completeness_receipt_never_attaches_incomplete_native_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quality: dict[str, int],
    expected_trace_state: str,
    expected_error: str | None,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.raw_snapshot_covered_through_epoch_ms = 2_000
    for field, value in quality.items():
        setattr(client, field, value)

    run = await _finalizer(tmp_path, client, raw_snapshot=raw).finalize(
        KovaaKFileDiscovery(
                stem=f"raw-quality-{expected_trace_state}",
            stats_path=stats,
            performance_path=performance,
        )
    )

    assert run["trace_state"] == expected_trace_state
    assert run["trace_error"] == expected_error
    readiness = kovaak_run_store.derive_run_readiness(run)
    assert readiness["state"] == "pending_analysis"
    if expected_trace_state == "attached":
        assert readiness["input_native"] is True
        # 回显只挂在 receipt 判死路径；正常 attach 的 run 不携带。
        assert "trace_receipt_echo" not in run
    else:
        assert readiness["video_fallback"] is True
        # receipt 判死必须回显时钟锚/覆盖值/窗口（区分元数据误报与真缺数据）。
        echo = run["trace_receipt_echo"]
        assert echo["receiptVersion"] == "raw_snapshot_receipt.v2"
        assert echo["window_start_epoch_ms"] == 1_000
        assert echo["window_end_epoch_ms"] == 2_000
        assert echo["captureSessionStartEpochMs"] == getattr(
            client, "raw_snapshot_capture_session_start_epoch_ms",
        )
        assert echo["coveredThroughEpochMs"] == 2_000
        assert echo["snapshotAtEpochMs"] == 2_001
        assert echo["pointCount"] == 1
        assert echo["queueDroppedPoints"] == getattr(
            client, "raw_snapshot_queue_dropped_points",
        )
        assert echo["ringExpiredPoints"] == getattr(
            client, "raw_snapshot_ring_expired_points",
        )
        # 回显随 meta.json 持久化（诊断包 recent_runs 从磁盘读）。
        persisted = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
        assert persisted["trace_receipt_echo"] == echo


@pytest.mark.asyncio
async def test_raw_snapshot_barrier_skips_missing_pause_and_attached_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    # 2026-10-08 CSV-only：stats-only 不再 waiting，因 stub 无 Challenge Start
    # 诚实落终态（alignment unavailable），且不触发 raw barrier flush。
    orphan = await finalizer.finalize(KovaaKFileDiscovery(stem="missing", stats_path=stats))
    assert orphan["alignment_state"] == "unavailable"
    assert client.flush_calls == []

    _configure_parsers(monkeypatch, pause_count="1", time_limit=1.0)
    paused = await finalizer.finalize(KovaaKFileDiscovery(
        stem="paused", stats_path=stats, performance_path=performance,
    ))
    assert paused["alignment_state"] == "unavailable"
    assert client.flush_calls == []

    _configure_parsers(monkeypatch, time_limit=1.0)
    complete = KovaaKFileDiscovery(
        stem="duplicate", stats_path=stats, performance_path=performance,
    )
    await finalizer.finalize(complete)
    assert client.flush_calls == ["session-1"]
    await finalizer.finalize(complete)
    assert client.flush_calls == ["session-1"]


@pytest.mark.asyncio
async def test_positive_bot_life_limit_still_uses_terminal_stats_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(
        monkeypatch,
        time_limit=1_000.0,
        bot_max_lives=(0, 5, 0),
        stats_event_times=(113.944,),
    )
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")

    run = await _finalizer(tmp_path, client).finalize(KovaaKFileDiscovery(
        stem="event-terminated",
        stats_path=stats,
        performance_path=performance,
    ))

    assert run["alignment_summary"]["end_source"] == "stats_event"
    assert run["alignment_summary"]["duration_ms"] == 113_944


@pytest.mark.asyncio
async def test_video_coverage_gap_keeps_trace_and_marks_video_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webapp.backend import config

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    raw_source = raw.read_bytes()
    client = FakeNativeCaptureClient(
        tmp_path / "data",
        terminal_code="capture_coverage_gap",
    )
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="coverage-gap",
        stats_path=stats,
        performance_path=performance,
    )

    run = await finalizer.finalize(discovery)
    duplicate = await finalizer.finalize(discovery)

    assert run["id"] == duplicate["id"]
    assert run["trace_state"] == "attached"
    assert run["pending_trace_path"] is None
    assert run["trace_error"] is None
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_coverage_gap"
    assert run["finalization_state"] == "finalized"
    assert kovaak_run_store.derive_run_readiness(run) == {
        "ready": True,
        "state": "pending_analysis",
        "input_native": True,
        "video_fallback": False,
    }
    assert len(client.export_calls) == 1
    managed = list((tmp_path / "data" / "runs" / str(run["id"])).glob("trace-*.bin"))
    assert len(managed) == 1 and managed[0].is_file()
    assert run["mouse_trace_path"] is not None
    assert Path(run["mouse_trace_path"]).is_file()
    assert raw.read_bytes() == raw_source
    assert stats.read_bytes() == b"stats"
    assert performance.read_bytes() == b"performance"
    tombstones = file_store.read_json("runs/_evidence_tombstones.json") or []
    assert all(t.get("run_id") != run["id"] for t in tombstones)
    assert file_store.list_dir("sessions") == []


@pytest.mark.asyncio
async def test_response_loss_retries_same_request_and_attaches_existing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(
        tmp_path / "data",
        lose_first_response=True,
    )
    finalizer = _finalizer(tmp_path, client)
    discovery = KovaaKFileDiscovery(
        stem="response-loss",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(NativeCaptureRetryableError) as exc_info:
        await finalizer.finalize(discovery)
    assert exc_info.value.code == "capture_control_response_lost"
    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["video_state"] == "pending"

    attached = await finalizer.finalize(discovery)

    assert attached["video_state"] == "attached"
    assert attached["finalization_state"] == "finalized"
    assert len(client.export_calls) == 2
    assert client.export_calls[0] == client.export_calls[1]
    assert client.publication_count == 1
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1


@pytest.mark.asyncio
async def test_response_loss_does_not_attach_raw_from_a_new_capture_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(
        tmp_path / "data",
        lose_first_response=True,
    )
    client.raw_snapshot_covered_through_epoch_ms = 1_999
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="response-loss-session-change",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(NativeCaptureRetryableError, match="response_lost"):
        await finalizer.finalize(discovery)
    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["capture_session_id"] == "session-1"
    assert pending["video_state"] == "pending"
    assert pending["trace_state"] == "pending"

    client.capture_session_id = "session-2"
    client.raw_snapshot_capture_session_start_epoch_ms = 2_001
    mismatched = await finalizer.finalize(discovery)

    assert client.flush_calls == ["session-1", "session-2"]
    assert mismatched["capture_session_id"] == "session-1"
    assert mismatched["video_state"] == "unavailable"
    assert mismatched["video_error"] == "video_capture_session_mismatch"
    assert mismatched["trace_state"] == "unavailable"
    assert mismatched["trace_error"] == "trace_raw_window_coverage_gap"
    assert mismatched["mouse_trace_path"] is None


@pytest.mark.asyncio
async def test_capture_session_mismatch_marks_pending_video_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(
        tmp_path / "data",
        lose_first_response=True,
    )
    client.raw_snapshot_covered_through_epoch_ms = 1_999
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="session-mismatch-terminal",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(NativeCaptureRetryableError, match="response_lost"):
        await finalizer.finalize(discovery)
    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["capture_session_id"] == "session-1"
    assert pending["video_state"] == "pending"
    assert pending["trace_state"] == "pending"

    run_dir = tmp_path / "data" / "runs" / str(pending["id"])
    for artifact in run_dir.iterdir():
        artifact.unlink()
    client.capture_session_id = "session-2"
    client.raw_snapshot_capture_session_start_epoch_ms = 2_001
    run = await finalizer.finalize(discovery)

    assert len(client.export_calls) == 1
    assert client.flush_calls == ["session-1", "session-2"]
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_capture_session_mismatch"
    assert run["capture_session_id"] == "session-1"
    assert run["trace_state"] == "unavailable"
    assert run["trace_error"] == "trace_raw_window_coverage_gap"
    assert run["mouse_trace_path"] is None
    assert run["finalization_state"] == "finalized"
    assert run["finalization_error"] == "video_capture_session_mismatch"


@pytest.mark.asyncio
async def test_stale_trace_terminal_duplicate_does_not_flush_or_reattach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(
        kovaak_run_store,
        "_now_ms",
        lambda: 2_000 + kovaak_run_store.MAX_SNAPSHOT_SPAN_MS + 1,
    )
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_100, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.raw_snapshot_covered_through_epoch_ms = 1_999
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="stale-terminal-duplicate",
        stats_path=stats,
        performance_path=performance,
    )

    stale = await finalizer.finalize(discovery)
    assert stale["trace_state"] == "unavailable"
    assert stale["trace_error"] == "trace_snapshot_stale"
    assert client.flush_calls == ["session-1"]

    client.capture_session_id = "session-2"
    client.raw_snapshot_covered_through_epoch_ms = 2_000
    duplicate = await finalizer.finalize(discovery)

    assert client.flush_calls == ["session-1"]
    assert duplicate["trace_state"] == "unavailable"
    assert duplicate["trace_error"] == "trace_snapshot_stale"
    assert duplicate["mouse_trace_path"] is None


@pytest.mark.asyncio
async def test_over_300_second_window_fails_before_native_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch, time_limit=301.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")

    run = await _finalizer(tmp_path, client).finalize(KovaaKFileDiscovery(
        stem="too-long",
        stats_path=stats,
        performance_path=performance,
    ))

    assert client.export_calls == []
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_window_invalid"
    assert run["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_invalid_video_window_keeps_trace_for_input_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webapp.backend import config

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _configure_parsers(monkeypatch, time_limit=301.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 150_000, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="window-invalid",
        stats_path=stats,
        performance_path=performance,
    ))

    assert client.export_calls == []
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_window_invalid"
    assert run["finalization_state"] == "finalized"
    assert run["trace_state"] == "attached"
    assert run["trace_error"] is None
    managed = list((tmp_path / "data" / "runs" / str(run["id"])).glob("trace-*.bin"))
    assert len(managed) == 1 and managed[0].is_file()
    assert kovaak_run_store.derive_run_readiness(run) == {
        "ready": True,
        "state": "pending_analysis",
        "input_native": True,
        "video_fallback": False,
    }


@pytest.mark.asyncio
async def test_capture_session_mismatch_is_terminal_video_degradation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(
        tmp_path / "data",
        terminal_code="capture_session_mismatch",
    )

    run = await _finalizer(tmp_path, client, raw_snapshot=raw).finalize(KovaaKFileDiscovery(
        stem="session-mismatch",
        stats_path=stats,
        performance_path=performance,
    ))

    assert len(client.export_calls) == 1
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_capture_session_mismatch"
    assert run["trace_state"] == "attached"
    readiness = kovaak_run_store.derive_run_readiness(run)
    assert readiness["state"] == "pending_analysis"
    assert readiness["input_native"] is True
    assert readiness["video_fallback"] is False
    assert run["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_conflicting_same_path_source_revision_remains_pairing_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"first-stats-revision")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)

    # 2026-10-08 CSV-only：stats-only 首次 finalize 落劣质终局（stub 无
    # Challenge Start）；随后 performance-only 收尾经 merge 合并双源，stats
    # revision 已被替换 → source identity 冲突继续 fail-closed。
    await finalizer.finalize(KovaaKFileDiscovery(
        stem="revision-conflict",
        stats_path=stats,
    ))
    stats.write_bytes(b"conflicting-second-stats-revision")

    with pytest.raises(kovaak_run_store.NonRetryableIngestionError):
        await finalizer.finalize(KovaaKFileDiscovery(
            stem="revision-conflict",
            performance_path=performance,
        ))

    assert client.export_calls == []
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1


@pytest.mark.asyncio
async def test_vertical_slice_keeps_consecutive_normal_and_timescale_runs_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw-ring.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 1, "dy": 2, "buttons": 0},
        {"timestamp_ms": 70_500, "dx": 3, "dy": 4, "buttons": 0},
    ])
    parser_profiles = {
        "normal": (1_000, 60.0, 1.0),
        "timescale": (70_000, 60.0, 0.5),
    }

    def parse_stats(path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            file_name=path.name,
            scenario="Scenario",
            summary={"Scenario": "Scenario", "Pause Count": "0"},
            config={},
            kills=pd.DataFrame({"time_s": []}),
        )

    def parse_performance(path: Path) -> PerformanceData:
        start_ms, time_limit, timescale = parser_profiles[path.stem.split()[0]]
        return PerformanceData(
            header=PerformanceHeader(
                scenario_name="Scenario",
                challenge_start_utc=start_ms,
                challenge_profile=ChallengeProfile(
                    time_limit=time_limit, timescale=timescale,
                ),
            ),
        )

    monkeypatch.setattr(kovaak_run_store, "parse_stats_csv", parse_stats)
    monkeypatch.setattr(kovaak_run_store, "parse_performance_file", parse_performance)
    normal_stats = tmp_path / "normal Stats.csv"
    normal_performance = tmp_path / "normal Performance.perf"
    timescale_stats = tmp_path / "timescale Stats.csv"
    timescale_performance = tmp_path / "timescale Performance.perf"
    for path, contents in (
        (normal_stats, b"normal-stats"),
        (normal_performance, b"normal-performance"),
        (timescale_stats, b"timescale-stats"),
        (timescale_performance, b"timescale-performance"),
    ):
        path.write_bytes(contents)
    source_bytes = {path: path.read_bytes() for path in (
        normal_stats, normal_performance, timescale_stats, timescale_performance,
    )}
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discoveries = [
        KovaaKFileDiscovery(
            stem="normal", stats_path=normal_stats, performance_path=normal_performance,
        ),
        KovaaKFileDiscovery(
            stem="timescale", stats_path=timescale_stats,
            performance_path=timescale_performance,
        ),
    ]

    runs = [await finalizer.finalize(discovery) for discovery in discoveries]
    duplicates = [await finalizer.finalize(discovery) for discovery in discoveries]

    assert [run["id"] for run in runs] == [duplicate["id"] for duplicate in duplicates]
    assert len({run["id"] for run in runs}) == 2
    assert len(client.export_calls) == 2
    assert client.publication_count == 2
    assert client.release_calls == []
    assert {
        request["end_epoch_ms"] - request["start_epoch_ms"]
        for request in client.export_calls
    } == {60_000, 120_000}
    for run in runs:
        assert run["video_state"] == "attached"
        assert run["trace_state"] == "attached"
        assert run["finalization_state"] == "finalized"
        assert kovaak_run_store.derive_run_readiness(run)["state"] == "pending_analysis"
        assert run["mouse_trace_path"] != runs[0]["mouse_trace_path"] or run["id"] == runs[0]["id"]
        public = kovaak_run_store.public_kovaak_run(run)
        assert "path" not in json.dumps(public, ensure_ascii=False)
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 2
    assert all(path.read_bytes() == contents for path, contents in source_bytes.items())


@pytest.mark.asyncio
async def test_vertical_slice_response_loss_startup_reconcile_then_removes_only_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stable-user-stats")
    performance.write_bytes(b"stable-user-performance")
    source_bytes = {stats: stats.read_bytes(), performance: performance.read_bytes()}
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 5, "dy": 6, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data", lose_first_response=True)
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="restart-reconcile",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(NativeCaptureRetryableError, match="response_lost"):
        await finalizer.finalize(discovery)
    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["video_state"] == "pending"
    assert client.publication_count == 1

    startup = await kovaak_run_store.reconcile_run_videos(tmp_path / "data")
    attached = await kovaak_run_store.get_kovaak_run(pending["id"], "u1")
    assert startup["attached"] == 1
    assert attached["video_state"] == "attached"
    assert attached["trace_state"] == "attached"
    assert len(client.export_calls) == 1
    assert client.publication_count == 1

    removed = await kovaak_run_store.remove_run_evidence(
        pending["id"], "u1", "video", tmp_path / "data",
    )
    assert removed["removal_state"] == "completed"
    final = await kovaak_run_store.get_kovaak_run(pending["id"], "u1")
    assert final["video_state"] == "unavailable"
    assert final["trace_state"] == "attached"
    assert Path(final["mouse_trace_path"]).is_file()
    assert kovaak_run_store.public_kovaak_run(final)["video_artifact_ref"] is None
    assert all(path.read_bytes() == contents for path, contents in source_bytes.items())


@pytest.mark.asyncio
async def test_flush_snapshot_failure_leaves_a_log_trail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from webapp.backend import config

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _configure_parsers(monkeypatch, time_limit=1.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    original_flush = client.flush_raw_snapshot

    def failing_flush(capture_session_id: str) -> dict:
        raise NativeCaptureRetryableError("raw_snapshot_failed")

    client.flush_raw_snapshot = failing_flush  # type: ignore[method-assign]
    finalizer = _finalizer(tmp_path, client)
    discovery = KovaaKFileDiscovery(
        stem="flush-failure",
        stats_path=stats,
        performance_path=performance,
    )

    with caplog.at_level(logging.WARNING, logger="webapp.backend.kovaak_capture_finalizer"):
        run = await finalizer.finalize(discovery)

    assert any(
        "flush_raw_snapshot failed" in record.getMessage()
        and "raw_snapshot_failed" in record.getMessage()
        for record in caplog.records
    ), "flush failure must leave a log trail instead of vanishing silently"
    assert run["trace_state"] in {"pending", "unavailable"}
    client.flush_raw_snapshot = original_flush  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_finalizing_session_flushes_snapshot_and_attaches_trace_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收尾局：游戏退出、phase 进入 finalizing 时仍要取回覆盖回执并立即附加 trace。

    修复前该路径不会请求 flush（只认 capturing/degraded），导致收尾局在 10 分钟
    保留期内反复 trace_pending，直到保留期结束才判 stale。
    """
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = "finalizing"
    client.kovaak_process_present = False
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="finalizing-flush",
        stats_path=stats,
        performance_path=performance,
    ))

    assert client.flush_calls == ["session-1"]
    assert run["trace_state"] == "attached"
    assert run["trace_error"] is None
    assert run["video_state"] == "attached"
    assert run["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_finalizing_session_without_coverage_still_waits_for_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收尾局 flush 也必须服从覆盖门：覆盖没到窗口末仍保持 trace_pending，不放弃。"""
    _configure_parsers(monkeypatch, time_limit=1.0)
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = "finalizing"
    client.kovaak_process_present = False
    client.raw_snapshot_covered_through_epoch_ms = 1_999
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)
    discovery = KovaaKFileDiscovery(
        stem="finalizing-uncovered",
        stats_path=stats,
        performance_path=performance,
    )

    with pytest.raises(RetryableIngestionError, match="coverage"):
        await finalizer.finalize(discovery)

    assert client.flush_calls == ["session-1"]
    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["trace_state"] == "pending"
    assert pending["finalization_state"] == "retryable"
    assert pending["finalization_error"] == "trace_waiting_snapshot"


@pytest.mark.asyncio
async def test_finalize_fires_telemetry_cut_hook_with_challenge_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """挑战窗有效即触发按局增量遥测切窗钩子（fire-and-forget、异常不扩散）。"""
    _configure_parsers(monkeypatch, start_epoch_ms=1_000)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stable-stats")
    performance.write_bytes(b"stable-performance")
    discovery = KovaaKFileDiscovery(
        stem="scenario", stats_path=stats, performance_path=performance,
    )
    cuts: list[tuple[object, object, object]] = []

    # 钩子抛异常必须被吞掉（记日志），收尾照常完成。
    def _boom_hook(run_id, start_ms, end_ms):
        raise RuntimeError("hook must be isolated from finalization")

    boom_finalizer = KovaaKCaptureFinalizer(
        native_client=FakeNativeCaptureClient(tmp_path / "data"),
        data_root=tmp_path / "data",
        raw_input_snapshot_path=tmp_path / "missing-raw.bin",
        user_id="u1",
        telemetry_cut_hook=_boom_hook,
    )
    run = await boom_finalizer.finalize(discovery)
    assert run["finalization_state"] == "finalized"

    # 源不齐的收尾重试不触发（2026-10-08 CSV-only：stats-only 走 CSV 对齐，
    # stub 无 Challenge Start → alignment unavailable，仍不触发钩子）；
    # 窗口校验通过后带着挑战窗触发一次（跨多次 finalize 的幂等去重由服务侧
    # 负责，finalizer 只负责派发）。换独立 stem：boom 阶段已把同 stem 的 run
    # 收尾完成，重试路径需要一个未成形的 run。
    stats2 = tmp_path / "Scenario2 Stats.csv"
    performance2 = tmp_path / "Scenario2 Performance.perf"
    stats2.write_bytes(b"stable-stats")
    performance2.write_bytes(b"stable-performance")
    finalizer = KovaaKCaptureFinalizer(
        native_client=FakeNativeCaptureClient(tmp_path / "data"),
        data_root=tmp_path / "data",
        raw_input_snapshot_path=tmp_path / "missing-raw.bin",
        user_id="u1",
        telemetry_cut_hook=lambda *args: cuts.append(args),
    )
    orphan = await finalizer.finalize(KovaaKFileDiscovery(
        stem="scenario2", stats_path=stats2, performance_path=None,
    ))
    assert orphan["alignment_state"] == "unavailable"
    assert cuts == []

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="scenario2", stats_path=stats2, performance_path=performance2,
    ))
    assert run["finalization_state"] == "finalized"
    assert len(cuts) == 1
    run_id, start_ms, end_ms = cuts[0]
    assert run_id == run["id"]
    assert start_ms == 1_000
    assert end_ms == 61_000  # challenge_start + time_limit(60s)


@pytest.mark.asyncio
async def test_finalize_skips_reimport_of_user_deleted_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_parsers(monkeypatch)
    stats = tmp_path / "Deleted Scenario Stats.csv"
    performance = tmp_path / "Deleted Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client)
    stem = "deleted-scenario - challenge - 2026.09.30-00.00.00"
    file_store.write_json("runs/_deleted_source_keys.json", [
        {"user_id": "u1", "source_key": stem, "deleted_at": "2026-09-30 12:00"},
    ])

    with pytest.raises(NonRetryableIngestionError) as exc_info:
        await finalizer.finalize(KovaaKFileDiscovery(
            stem=stem, stats_path=stats, performance_path=performance,
        ))

    assert exc_info.value.code == "source_deleted_by_user"
    assert await kovaak_run_store.list_kovaak_runs("u1") == []


def test_transport_corruption_codes_are_not_terminal_video_errors() -> None:
    # 0930 提案 D 回归锁：传输层被腐蚀的症状（control_read_failed /
    # control_message_invalid）在客户端侧已归可重试码，finalizer 不得再把
    # 它们映射成终态 video 错误；鉴权失败仍是终态。
    from webapp.backend.kovaak_capture_finalizer import _TERMINAL_VIDEO_ERRORS

    assert "control_message_invalid" not in _TERMINAL_VIDEO_ERRORS
    assert "control_read_failed" not in _TERMINAL_VIDEO_ERRORS
    assert "control_auth_failed" in _TERMINAL_VIDEO_ERRORS


@pytest.mark.asyncio
async def test_stale_window_replay_rebuild_skips_export_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[fix 2026-10-07 W7] 窗口终点早于 replay 覆盖下界（重启后全量补跑旧局）
    直接终态新 cause 码：不发起 export、不触发切窗、不进 trace 重试循环。"""
    stale_start = int(time.time() * 1000) - 600_000  # 窗口 [now-10min, now-9min]
    _configure_parsers(monkeypatch, start_epoch_ms=stale_start, time_limit=60.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    client = FakeNativeCaptureClient(tmp_path / "data")

    run = await _finalizer(tmp_path, client).finalize(KovaaKFileDiscovery(
        stem="replay-expired",
        stats_path=stats,
        performance_path=performance,
    ))

    assert client.export_calls == []
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_replay_expired"
    assert run["trace_state"] == "unavailable"
    assert run["trace_error"] == "trace_snapshot_out_of_coverage"
    assert run["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_stale_rebuild_reenqueue_keeps_attached_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[fix 2026-10-09] 重启补跑重遇已 finalize 且 trace 已 attach 的局，
    不得把 Raw 轨迹清成 unavailable。

    v1.4.5~v1.4.9 的补跑预判无条件翻转 trace——每次重启都把近期局已采集
    好的 Raw 证据清掉，用户更新重启后历史局全部「Raw 来源不可用」（10-09
    报障实锤）。视频侧 mark_run_video_unavailable 自带 attached 保护，所以
    症状恰好是「有视频没轨迹」。"""
    fresh_start = int(time.time() * 1000) - 60_000
    _configure_parsers(monkeypatch, start_epoch_ms=fresh_start, time_limit=60.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": fresh_start + 30_000, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="keep-trace-attached",
        stats_path=stats,
        performance_path=performance,
    ))
    assert run["trace_state"] == "attached"
    assert run["video_state"] == "attached"
    assert run["finalization_state"] == "finalized"

    # 次日重启：watcher 重发同一 stem，窗口相对 now 已超 replay 覆盖（W7 触发）。
    stale_start = fresh_start - 24 * 60 * 60 * 1000
    _configure_parsers(monkeypatch, start_epoch_ms=stale_start, time_limit=60.0)
    rerun = await finalizer.finalize(KovaaKFileDiscovery(
        stem="keep-trace-attached",
        stats_path=stats,
        performance_path=performance,
    ))

    assert rerun["id"] == run["id"]
    assert rerun["finalization_error"] == "video_replay_expired"
    assert rerun["video_state"] == "attached"
    assert rerun["trace_state"] == "attached"
    assert rerun["trace_error"] is None


@pytest.mark.asyncio
async def test_stale_rebuild_heals_clobbered_trace_from_disk_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[fix 2026-10-09] 已被历史版本清掉 trace 的局，补跑重遇时从磁盘上的
    窗口切片文件回挂自愈，恢复 Raw 证据。"""
    from webapp.backend import config

    fresh_start = int(time.time() * 1000) - 60_000
    _configure_parsers(monkeypatch, start_epoch_ms=fresh_start, time_limit=60.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": fresh_start + 30_000, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="heal-trace-artifact",
        stats_path=stats,
        performance_path=performance,
    ))
    assert run["trace_state"] == "attached"

    # 复刻 v1.4.5~v1.4.9 的历史伤害：只清指针，窗口切片文件留在磁盘。
    damaged = await kovaak_run_store.mark_mouse_trace_unavailable(
        run["id"], "u1", "trace_snapshot_out_of_coverage",
    )
    assert damaged["trace_state"] == "unavailable"
    artifacts = list(
        (config.DATA_ROOT / "runs" / str(run["id"])).glob("trace-*.bin")
    )
    assert artifacts, "首次 attach 落盘的窗口切片文件应仍在磁盘上"

    stale_start = fresh_start - 24 * 60 * 60 * 1000
    _configure_parsers(monkeypatch, start_epoch_ms=stale_start, time_limit=60.0)
    rerun = await finalizer.finalize(KovaaKFileDiscovery(
        stem="heal-trace-artifact",
        stats_path=stats,
        performance_path=performance,
    ))

    assert rerun["id"] == run["id"]
    assert rerun["trace_state"] == "attached"
    assert rerun["trace_error"] is None
    assert rerun["mouse_trace_path"] is not None
    assert Path(rerun["mouse_trace_path"]).is_file()


@pytest.mark.asyncio
async def test_stale_rebuild_heals_trace_quarantined_to_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[fix 2026-10-09] 被清局的原地切片若已被 reconcile 孤儿清扫收走
    （runs/orphans/ 平铺、无局归属），补跑按局窗口时间唯一回配认领，
    拷回本局目录后 attach 恢复 Raw 证据。"""
    from types import SimpleNamespace

    from webapp.backend import config
    from webapp.backend import kovaak_capture_finalizer as finalizer_module

    fresh_start = int(time.time() * 1000) - 60_000
    _configure_parsers(monkeypatch, start_epoch_ms=fresh_start, time_limit=60.0)
    stats = tmp_path / "Scenario Stats.csv"
    performance = tmp_path / "Scenario Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    raw = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw, [
        {"timestamp_ms": fresh_start + 30_000, "dx": 2, "dy": 3, "buttons": 0},
    ])
    client = FakeNativeCaptureClient(tmp_path / "data")
    finalizer = _finalizer(tmp_path, client, raw_snapshot=raw)

    run = await finalizer.finalize(KovaaKFileDiscovery(
        stem="heal-trace-orphan",
        stats_path=stats,
        performance_path=performance,
    ))
    assert run["trace_state"] == "attached"

    # 复刻野外伤害全链：W7 清指针后，reconcile 把无引用切片收进孤儿仓。
    damaged = await kovaak_run_store.mark_mouse_trace_unavailable(
        run["id"], "u1", "trace_snapshot_out_of_coverage",
    )
    assert damaged["trace_state"] == "unavailable"
    orphans = config.DATA_ROOT / "runs" / "orphans"
    orphans.mkdir(parents=True, exist_ok=True)
    artifacts = list(
        (config.DATA_ROOT / "runs" / str(run["id"])).glob("trace-*.bin")
    )
    assert len(artifacts) == 1
    artifacts[0].rename(orphans / artifacts[0].name)

    # 次日重启（文件与窗口不动，时钟前进=生产形态）：W7 触发 → 孤儿按窗口回配。
    real_time = finalizer_module.time
    monkeypatch.setattr(
        finalizer_module, "time",
        SimpleNamespace(time=lambda: real_time.time() + 86_400, monotonic=real_time.monotonic),
    )
    rerun = await finalizer.finalize(KovaaKFileDiscovery(
        stem="heal-trace-orphan",
        stats_path=stats,
        performance_path=performance,
    ))

    assert rerun["id"] == run["id"]
    assert rerun["finalization_error"] == "video_replay_expired"
    assert rerun["trace_state"] == "attached"
    assert rerun["trace_error"] is None
    healed_path = Path(rerun["mouse_trace_path"])
    assert healed_path.is_file()
    assert orphans not in healed_path.parents, "认领件应拷回本局目录，不留在孤儿仓"


def test_capture_window_invalid_maps_to_replay_range_code() -> None:
    """[fix 2026-10-07 W8] native 的 capture_window_invalid（窗口不在 replay
    覆盖内，重启补跑旧局的必然形态）与 control_window_invalid（Python 侧窗口
    合法性）拆码，历史页/诊断可辨认补跑局。"""
    from webapp.backend.kovaak_capture_finalizer import _TERMINAL_VIDEO_ERRORS

    assert (
        _TERMINAL_VIDEO_ERRORS["capture_window_invalid"]
        == "video_replay_window_out_of_range"
    )
    assert _TERMINAL_VIDEO_ERRORS["control_window_invalid"] == "video_window_invalid"
