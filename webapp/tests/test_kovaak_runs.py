import struct
import shutil
from pathlib import Path

import pytest

from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceHeader,
)
from webapp.backend import kovaak_run_store
from webapp.backend.kovaak_ingest import KovaaKFileDiscovery


@pytest.mark.asyncio
async def test_upsert_merges_stats_and_performance_for_same_owner(tmp_path: Path):
    stats = tmp_path / "1wall Stats.csv"
    perf = tmp_path / "1wall Performance.perf"
    first = await kovaak_run_store.upsert_kovaak_run(
        user_id="desktop-local",
        source_key="1wall",
        scenario="1wall",
        stats_path=str(stats),
        stats_summary={"score": 10},
    )
    second = await kovaak_run_store.upsert_kovaak_run(
        user_id="desktop-local",
        source_key="1wall",
        performance_path=str(perf),
        performance_summary={"event_count": 5},
    )

    assert second["id"] == first["id"]
    assert second["stats_path"] == str(stats)
    assert second["performance_path"] == str(perf)
    assert second["stats_summary"] == {"score": 10}
    assert second["performance_summary"] == {"event_count": 5}


@pytest.mark.asyncio
async def test_ingest_rejects_conflicting_stats_and_performance_identity(
    tmp_path: Path, monkeypatch,
):
    stats = tmp_path / "shared Stats.csv"
    performance = tmp_path / "shared Performance.perf"
    stats.write_text("stats", encoding="utf-8")
    performance.write_bytes(b"performance")
    await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="shared",
        scenario="Scenario A",
        stats_path=str(stats),
    )
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: PerformanceData(
            header=PerformanceHeader(
                scenario_name="Scenario B",
                challenge_start_utc=0,
                challenge_profile=ChallengeProfile(time_limit=0),
            ),
        ),
    )

    with pytest.raises(kovaak_run_store.PairingConflictError, match="pairing_conflict"):
        await kovaak_run_store.ingest_discovery(
            KovaaKFileDiscovery(stem="shared", performance_path=performance),
            user_id="u1",
        )

    run = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert run["scenario"] == "Scenario A"
    assert run["performance_path"] is None


@pytest.mark.asyncio
async def test_ingest_accepts_normalized_matching_scenario_identity(
    tmp_path: Path, monkeypatch,
):
    performance = tmp_path / "shared Performance.perf"
    performance.write_bytes(b"performance")
    original = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="shared",
        scenario="  Scenario   A ",
    )
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: PerformanceData(
            header=PerformanceHeader(
                scenario_name="scenario a",
                challenge_start_utc=0,
                challenge_profile=ChallengeProfile(time_limit=0),
            ),
        ),
    )

    merged = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="shared", performance_path=performance),
        user_id="u1",
    )

    assert merged["id"] == original["id"]
    assert merged["performance_path"] == str(performance)


@pytest.mark.asyncio
async def test_ingest_rejects_second_stats_source_for_same_run_identity(tmp_path: Path):
    fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    first = tmp_path / "first Stats.csv"
    second = tmp_path / "second Stats.csv"
    shutil.copyfile(fixture, first)
    shutil.copyfile(fixture, second)

    await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="stable-run", stats_path=first),
        user_id="u1",
    )

    with pytest.raises(kovaak_run_store.PairingConflictError, match="pairing_conflict"):
        await kovaak_run_store.ingest_discovery(
            KovaaKFileDiscovery(stem="stable-run", stats_path=second),
            user_id="u1",
        )

    run = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert run["stats_path"] == str(first)
    assert run["stats_summary"]["source"]["sha256"]
    assert run["stats_summary"]["source"]["parser_version"] == "kovaak_stats.v1"


@pytest.mark.asyncio
async def test_list_and_get_are_owner_scoped():
    mine = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="mine", scenario="Mine",
    )
    await kovaak_run_store.upsert_kovaak_run(
        user_id="u2", source_key="other", scenario="Other",
    )

    assert [run["id"] for run in await kovaak_run_store.list_kovaak_runs("u1")] == [mine["id"]]
    assert await kovaak_run_store.get_kovaak_run(mine["id"], "u2") is None


@pytest.mark.asyncio
async def test_ingest_discovery_parses_existing_stats_fixture():
    stats = Path("data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv").resolve()
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="fixture", stats_path=stats),
    )
    assert run["scenario"] == "1wall 6targets small"
    assert run["stats_summary"]["kill_count"] > 0
    assert run["stats_path"] == str(stats)


@pytest.mark.asyncio
async def test_desktop_runs_api_is_token_protected(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="api-run",
        scenario="1wall",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/api/kovaak-runs")
        allowed = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert len(allowed.json()["runs"]) == 1


@pytest.mark.asyncio
async def test_desktop_runs_api_does_not_expose_private_paths(monkeypatch, tmp_path: Path):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "secret Stats.csv"
    stats.write_text("private", encoding="utf-8")
    await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="private-run",
        scenario="Scenario",
        stats_path=str(stats),
        stats_summary={
            "source": {
                "path": str(stats),
                "sha256": "a" * 64,
                "basename": stats.name,
                "size": stats.stat().st_size,
                "mtime_ns": stats.stat().st_mtime_ns,
                "parser_version": "kovaak_stats.v1",
                "availability": "available",
            },
        },
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
    assert response.status_code == 200
    payload = response.json()["runs"][0]
    assert "stats_path" not in payload
    assert str(stats) not in response.text
    assert payload["stats_source_ref"].startswith("run:")


@pytest.mark.asyncio
async def test_run_analysis_endpoint_freezes_owned_snapshot_without_returning_paths(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config, queue
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="endpoint-run", stats_path=stats),
        user_id=config.DESKTOP_LOCAL_PROFILE,
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
            json={"input_mode": "video_fallback", "video_path": str(video)},
        )
    assert response.status_code == 200
    session = await queue.get_session(response.json()["session_id"])
    assert session["input_mode"] == "video_fallback"
    assert session["kovaak_run_id"] == run["id"]
    assert session["input_snapshot"]["scenario_identity_version"] == "kovaak_scenario.v1"
    assert session["input_snapshot"]["sources"]["stats"]["artifact_ref"].startswith("run:")


def test_raw_input_snapshot_codec_and_window_extraction(tmp_path: Path):
    snapshot = tmp_path / "buffer.bin"
    destination = tmp_path / "runs" / "1" / "mouse_trace.bin"
    points = [
        {"timestamp_ms": 100, "dx": 1, "dy": 2, "buttons": 0},
        {"timestamp_ms": 200, "dx": -3, "dy": 4, "buttons": 1},
        {"timestamp_ms": 400, "dx": 5, "dy": 6, "buttons": 0},
    ]
    kovaak_run_store.write_mouse_snapshot(snapshot, points)
    assert kovaak_run_store.read_mouse_snapshot(snapshot) == points
    assert kovaak_run_store.extract_mouse_snapshot_window(snapshot, 150, 300, destination) == 1
    assert kovaak_run_store.read_mouse_snapshot(destination) == [points[1]]


@pytest.mark.asyncio
async def test_ingest_discovery_pairs_raw_trace_to_performance_window(tmp_path: Path, monkeypatch):
    import struct
    from webapp.backend import config

    def varint(value: int) -> bytes:
        out = bytearray()
        while value >= 0x80:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
        return bytes(out)

    def field(number: int, wire_type: int, raw: bytes | int) -> bytes:
        prefix = varint((number << 3) | wire_type)
        if wire_type == 0:
            return prefix + varint(int(raw))
        if wire_type == 5:
            return prefix + bytes(raw)
        return prefix + varint(len(raw)) + raw

    performance = tmp_path / "Scenario Performance.perf"
    profile = field(1, 5, struct.pack("<f", 1.0))
    header = b"".join([
        field(1, 2, b"Scenario"),
        field(3, 0, 1000),
        field(5, 2, profile),
    ])
    performance.write_bytes(field(1, 2, header))
    snapshot = tmp_path / "buffer.bin"
    kovaak_run_store.write_mouse_snapshot(snapshot, [
        {"timestamp_ms": 900, "dx": 1, "dy": 1, "buttons": 0},
        {"timestamp_ms": 1500, "dx": 2, "dy": 2, "buttons": 0},
        {"timestamp_ms": 2500, "dx": 3, "dy": 3, "buttons": 0},
    ])
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="scenario", performance_path=performance),
        raw_input_snapshot_path=snapshot,
    )
    assert run["mouse_trace_path"] is not None
    trace = kovaak_run_store.read_mouse_snapshot(run["mouse_trace_path"])
    assert [point["timestamp_ms"] for point in trace] == [1500]


@pytest.mark.asyncio
async def test_ingest_retries_trace_pairing_during_post_run_retention_grace(
    tmp_path: Path, monkeypatch,
):
    from webapp.backend import config

    def varint(value: int) -> bytes:
        out = bytearray()
        while value >= 0x80:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
        return bytes(out)

    def field(number: int, wire_type: int, raw: bytes | int) -> bytes:
        prefix = varint((number << 3) | wire_type)
        if wire_type == 0:
            return prefix + varint(int(raw))
        if wire_type == 5:
            return prefix + bytes(raw)
        return prefix + varint(len(raw)) + raw

    performance = tmp_path / "Scenario Performance.perf"
    profile = field(1, 5, struct.pack("<f", 1.0))
    performance.write_bytes(field(1, 2, b"".join([
        field(1, 2, b"Scenario"),
        field(3, 0, 1_000),
        field(5, 2, profile),
    ])))
    snapshot = tmp_path / "buffer.bin"
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)

    with pytest.raises(kovaak_run_store.TracePendingError, match="trace_pending"):
        await kovaak_run_store.ingest_discovery(
            KovaaKFileDiscovery(stem="scenario", performance_path=performance),
            user_id="u1",
            raw_input_snapshot_path=snapshot,
        )

    pending = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert pending["trace_state"] == "pending"
    assert pending["trace_error"] == "trace_waiting_snapshot"

    kovaak_run_store.write_mouse_snapshot(snapshot, [
        {"timestamp_ms": 1_500, "dx": 2, "dy": 3, "buttons": 0},
    ])
    attached = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="scenario", performance_path=performance),
        user_id="u1",
        raw_input_snapshot_path=snapshot,
    )

    assert attached["trace_state"] == "attached"
    assert kovaak_run_store.read_mouse_snapshot(attached["mouse_trace_path"])[0][
        "timestamp_ms"
    ] == 1_500



def test_python_reads_shared_acri_v1_golden_fixture(tmp_path: Path):
    fixture = Path(__file__).with_name("fixtures") / "acri-v1-golden.bin"

    expected = [
        {"timestamp_ms": 1_700_000_000_000, "dx": -2, "dy": 4, "buttons": 1},
        {"timestamp_ms": 1_700_000_000_016, "dx": 8, "dy": -9, "buttons": 0},
    ]
    assert kovaak_run_store.read_mouse_snapshot(fixture) == expected

    python_output = tmp_path / "python-v1.bin"
    kovaak_run_store.write_mouse_snapshot(python_output, expected)
    assert python_output.read_bytes() == fixture.read_bytes()


def test_acri_v1_reader_rejects_invalid_resource_and_event_semantics(tmp_path: Path):
    def snapshot(points: list[tuple[int, int, int, int]], *, count: int | None = None) -> bytes:
        actual_count = len(points) if count is None else count
        payload = bytearray(b"ACRI\x01\x00\x00\x00")
        payload.extend(struct.pack("<I", actual_count))
        for point in points:
            payload.extend(struct.pack("<qiiI", *point))
        return bytes(payload)

    invalid_cases = {
        "point-limit": snapshot([], count=kovaak_run_store.MAX_SNAPSHOT_POINTS + 1),
        "non-monotonic": snapshot([(200, 1, 1, 0), (199, 1, 1, 0)]),
        "unsupported-button": snapshot([(200, 1, 1, 8)]),
        "span-limit": snapshot([
            (0, 1, 1, 0),
            (kovaak_run_store.MAX_SNAPSHOT_SPAN_MS + 1, 1, 1, 0),
        ]),
    }
    for name, contents in invalid_cases.items():
        path = tmp_path / f"{name}.bin"
        path.write_bytes(contents)
        with pytest.raises(ValueError):
            kovaak_run_store.read_mouse_snapshot(path)


@pytest.mark.asyncio
async def test_begin_trace_attach_clears_stale_attachment_and_records_pending(tmp_path: Path):
    old_trace = tmp_path / "old.bin"
    pending_trace = tmp_path / "runs" / "1" / "trace-new.bin"
    kovaak_run_store.write_mouse_snapshot(old_trace, [
        {"timestamp_ms": 100, "dx": 1, "dy": 1, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="stale-trace", mouse_trace_path=str(old_trace),
    )

    pending = await kovaak_run_store.begin_mouse_trace_attach(
        run["id"], "u1", pending_trace,
    )

    assert pending["mouse_trace_path"] is None
    assert pending["pending_trace_path"] == str(pending_trace)
    assert pending["trace_state"] == "pending"
    assert pending["trace_error"] is None


@pytest.mark.asyncio
async def test_reconcile_pending_trace_attaches_only_valid_completed_artifact(tmp_path: Path):
    data_root = tmp_path / "data"
    pending_trace = data_root / "runs" / "1" / "trace-recover.bin"
    run = await kovaak_run_store.upsert_kovaak_run(user_id="u1", source_key="recover")
    await kovaak_run_store.begin_mouse_trace_attach(run["id"], "u1", pending_trace)
    kovaak_run_store.write_mouse_snapshot(pending_trace, [
        {"timestamp_ms": 100, "dx": 1, "dy": 1, "buttons": 1},
    ])

    outcome = await kovaak_run_store.reconcile_mouse_traces(data_root)
    recovered = await kovaak_run_store.get_kovaak_run(run["id"], "u1")

    assert outcome == {"attached": 1, "unavailable": 0, "quarantined": 0}
    assert recovered["trace_state"] == "attached"
    assert recovered["mouse_trace_path"] == str(pending_trace)
    assert recovered["pending_trace_path"] is None


@pytest.mark.asyncio
async def test_reconcile_pending_missing_trace_is_observable_not_silent_success(tmp_path: Path):
    data_root = tmp_path / "data"
    run = await kovaak_run_store.upsert_kovaak_run(user_id="u1", source_key="missing")
    await kovaak_run_store.begin_mouse_trace_attach(
        run["id"], "u1", data_root / "runs" / "1" / "trace-missing.bin",
    )

    outcome = await kovaak_run_store.reconcile_mouse_traces(data_root)
    recovered = await kovaak_run_store.get_kovaak_run(run["id"], "u1")

    assert outcome == {"attached": 0, "unavailable": 1, "quarantined": 0}
    assert recovered["trace_state"] == "unavailable"
    assert recovered["trace_error"] == "trace_attach_failed"
    assert recovered["mouse_trace_path"] is None


@pytest.mark.asyncio
async def test_reconcile_rejects_pending_trace_outside_managed_run_root(tmp_path: Path):
    data_root = tmp_path / "data"
    external = tmp_path / "external.bin"
    kovaak_run_store.write_mouse_snapshot(external, [
        {"timestamp_ms": 100, "dx": 1, "dy": 1, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(user_id="u1", source_key="unsafe")
    await kovaak_run_store.begin_mouse_trace_attach(run["id"], "u1", external)

    outcome = await kovaak_run_store.reconcile_mouse_traces(data_root)
    recovered = await kovaak_run_store.get_kovaak_run(run["id"], "u1")

    assert outcome["unavailable"] == 1
    assert recovered["trace_state"] == "unavailable"
    assert recovered["trace_error"] == "trace_attach_failed"
    assert external.exists()


@pytest.mark.asyncio
async def test_reconcile_quarantines_unreferenced_managed_trace(tmp_path: Path):
    data_root = tmp_path / "data"
    orphan = data_root / "runs" / "99" / "trace-orphan.bin"
    kovaak_run_store.write_mouse_snapshot(orphan, [
        {"timestamp_ms": 100, "dx": 1, "dy": 1, "buttons": 0},
    ])

    outcome = await kovaak_run_store.reconcile_mouse_traces(data_root)

    assert outcome == {"attached": 0, "unavailable": 0, "quarantined": 1}
    assert not orphan.exists()
    assert list((data_root / "runs" / "orphans").glob("trace-orphan*.bin"))



@pytest.mark.asyncio
async def test_ingest_snapshot_failure_clears_stale_trace_attachment(
    tmp_path: Path, monkeypatch,
):
    from webapp.backend import config

    def varint(value: int) -> bytes:
        out = bytearray()
        while value >= 0x80:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
        return bytes(out)

    def field(number: int, wire_type: int, raw: bytes | int) -> bytes:
        prefix = varint((number << 3) | wire_type)
        if wire_type == 0:
            return prefix + varint(int(raw))
        if wire_type == 5:
            return prefix + bytes(raw)
        return prefix + varint(len(raw)) + raw

    performance = tmp_path / "Scenario Performance.perf"
    profile = field(1, 5, struct.pack("<f", 1.0))
    performance.write_bytes(field(1, 2, b"".join([
        field(1, 2, b"Scenario"),
        field(3, 0, 1000),
        field(5, 2, profile),
    ])))
    old_trace = tmp_path / "old.bin"
    kovaak_run_store.write_mouse_snapshot(old_trace, [
        {"timestamp_ms": 1000, "dx": 1, "dy": 1, "buttons": 0},
    ])
    await kovaak_run_store.upsert_kovaak_run(
        user_id="desktop-local", source_key="scenario", mouse_trace_path=str(old_trace),
    )
    raw_snapshot = tmp_path / "buffer.bin"
    raw_snapshot.write_bytes(b"present only to start attachment")
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(kovaak_run_store, "extract_mouse_snapshot_window", fail_snapshot)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="scenario", performance_path=performance),
        raw_input_snapshot_path=raw_snapshot,
    )

    assert run["trace_state"] == "unavailable"
    assert run["trace_error"] == "trace_snapshot_failed"
    assert run["mouse_trace_path"] is None
    assert old_trace.exists()
