from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceHeader,
)
from webapp.backend import kovaak_run_store
from webapp.backend.kovaak_capture_finalizer import (
    CaptureFinalizationPending,
    KovaaKCaptureFinalizer,
)
from webapp.backend.kovaak_ingest import (
    KovaaKFileDiscovery,
    NonRetryableIngestionError,
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
async def test_exit_release_does_not_release_a_live_kovaak_session(tmp_path: Path) -> None:
    client = FakeNativeCaptureClient(tmp_path / "data")
    client.phase = "finalizing"
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
    conn = await kovaak_run_store.get_conn()
    assert (await (await conn.execute("SELECT COUNT(*) FROM sessions")).fetchone())[0] == 0


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
    assert isinstance(first_result[0], NonRetryableIngestionError)
    conn = await kovaak_run_store.get_conn()
    sequence_after_first = (await (await conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='kovaak_runs'"
    )).fetchone())[0]

    assert watcher.scan_once() == []
    assert len(tasks) == 1
    assert (await (await conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='kovaak_runs'"
    )).fetchone())[0] == sequence_after_first

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

    with pytest.raises(kovaak_run_store.TracePendingError, match="coverage"):
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
    assert readiness["state"] == (
        "pending_analysis" if expected_trace_state == "attached"
        else "incomplete_evidence"
    )


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

    with pytest.raises(NonRetryableIngestionError, match="waiting_for_sources"):
        await finalizer.finalize(KovaaKFileDiscovery(stem="missing", stats_path=stats))
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
async def test_video_coverage_gap_invalidates_canonical_run_evidence(
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
    assert run["trace_state"] == "unavailable"
    assert run["mouse_trace_path"] is None
    assert run["pending_trace_path"] is None
    assert run["trace_error"] == "trace_video_coverage_gap"
    assert run["video_state"] == "unavailable"
    assert run["video_error"] == "video_coverage_gap"
    assert run["finalization_state"] == "finalized"
    assert kovaak_run_store.derive_run_readiness(run) == {
        "ready": False,
        "state": "incomplete_evidence",
        "input_native": False,
        "video_fallback": False,
    }
    assert len(client.export_calls) == 1
    assert list((tmp_path / "data" / "runs" / str(run["id"])).glob("trace-*.bin")) == []
    assert raw.read_bytes() == raw_source
    assert stats.read_bytes() == b"stats"
    assert performance.read_bytes() == b"performance"
    conn = await kovaak_run_store.get_conn()
    assert (await (await conn.execute(
        "SELECT COUNT(*) FROM run_evidence_deletion_tombstones WHERE run_id=?",
        (run["id"],),
    )).fetchone())[0] == 0
    assert (await (await conn.execute("SELECT COUNT(*) FROM sessions")).fetchone())[0] == 0


@pytest.mark.asyncio
async def test_video_coverage_gap_cleanup_failure_reconciles_exact_managed_raw(
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
    original_unlink = kovaak_run_store._unlink_run_evidence_artifact

    def fail_unlink(_path: Path) -> int:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(kovaak_run_store, "_unlink_run_evidence_artifact", fail_unlink)
    run = await _finalizer(
        tmp_path,
        FakeNativeCaptureClient(
            tmp_path / "data", terminal_code="capture_coverage_gap",
        ),
        raw_snapshot=raw,
    ).finalize(KovaaKFileDiscovery(
        stem="coverage-gap-cleanup",
        stats_path=stats,
        performance_path=performance,
    ))

    managed = list((tmp_path / "data" / "runs" / str(run["id"])).glob("trace-*.bin"))
    assert run["trace_state"] == "unavailable"
    assert kovaak_run_store.derive_run_readiness(run)["state"] == "incomplete_evidence"
    assert len(managed) == 1 and managed[0].is_file()
    conn = await kovaak_run_store.get_conn()
    tombstone = await (await conn.execute(
        "SELECT cleanup_state, cleanup_attempts, last_error_code "
        "FROM run_evidence_deletion_tombstones "
        "WHERE run_id=? AND evidence_kind='raw'",
        (run["id"],),
    )).fetchone()
    assert tuple(tombstone) == ("failed", 1, "artifact_cleanup_failed")

    monkeypatch.setattr(
        kovaak_run_store, "_unlink_run_evidence_artifact", original_unlink,
    )
    assert await kovaak_run_store.reconcile_run_evidence_deletions(
        tmp_path / "data"
    ) == {"completed": 1, "failed": 0}
    assert not managed[0].exists()


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
    client.raw_snapshot_covered_through_epoch_ms = 2_000
    with pytest.raises(CaptureFinalizationPending, match="capture_session_mismatch"):
        await finalizer.finalize(discovery)

    unchanged = await kovaak_run_store.get_kovaak_run(pending["id"], "u1")
    assert client.flush_calls == ["session-1"]
    assert unchanged["capture_session_id"] == "session-1"
    assert unchanged["video_state"] == "pending"
    assert unchanged["trace_state"] == "pending"
    assert unchanged["mouse_trace_path"] is None


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
    assert kovaak_run_store.derive_run_readiness(run)["state"] == "incomplete_evidence"
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

    with pytest.raises(NonRetryableIngestionError, match="waiting_for_sources"):
        await finalizer.finalize(KovaaKFileDiscovery(
            stem="revision-conflict",
            stats_path=stats,
        ))
    stats.write_bytes(b"conflicting-second-stats-revision")

    with pytest.raises(kovaak_run_store.PairingConflictError):
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
    assert (await kovaak_run_store.get_conn()).in_transaction is False


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
