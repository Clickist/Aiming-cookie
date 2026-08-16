import struct
import shutil
import hashlib
import json
import os
from pathlib import Path

import pytest

from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceHeader,
)
from webapp.backend import kovaak_run_store
from webapp.backend.contracts import build_analysis_result_v2, build_artifact_manifest_v2
from webapp.backend.kovaak_ingest import KovaaKFileDiscovery


async def _complete_multimodal_run(
    tmp_path: Path,
    *,
    user_id: str,
    source_key: str,
) -> tuple[dict, Path, Path, Path]:
    """Create the complete source bundle required by the fixed Run contract."""
    stats = tmp_path / f"{source_key}-Stats.csv"
    performance = tmp_path / f"{source_key}-Performance.perf"
    trace = tmp_path / f"{source_key}-trace.bin"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=source_key,
        scenario="Complete test scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        mouse_trace_path=str(trace),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
    )
    run = await kovaak_run_store.set_run_alignment(
        run["id"],
        user_id,
        state="resolved",
        summary={
            "start_ms": 1_000,
            "end_ms": 2_000,
            "duration_ms": 1_000,
            "start_source": "test_start",
            "end_source": "test_end",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    ) or run
    return run, stats, performance, trace


def test_sqlite_run_timestamps_are_explicit_utc_on_public_projection():
    assert kovaak_run_store._timestamp_to_wire_utc("2026-08-08 08:10:09") == "2026-08-08T08:10:09Z"
    assert kovaak_run_store._timestamp_to_wire_utc("2026-08-08T08:10:09Z") == "2026-08-08T08:10:09Z"


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

    with pytest.raises(kovaak_run_store.NonRetryableIngestionError, match="pairing_conflict"):
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

    with pytest.raises(kovaak_run_store.NonRetryableIngestionError, match="pairing_conflict"):
        await kovaak_run_store.ingest_discovery(
            KovaaKFileDiscovery(stem="stable-run", stats_path=second),
            user_id="u1",
        )

    run = (await kovaak_run_store.list_kovaak_runs("u1"))[0]
    assert run["stats_path"] == str(first)
    assert run["stats_summary"]["source"]["sha256"]
    assert run["stats_summary"]["source"]["parser_version"] == "kovaak_stats.v2"


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
    listed = (await kovaak_run_store.list_kovaak_run_summaries("desktop-local"))[0]
    assert listed["stats_calibration"] == {
        "fov": 103.0,
        "dpi": 1600.0,
        "sensitivity": 0.16,
        "cm_per_360": 51.03,
    }


@pytest.mark.asyncio
async def test_duplicate_stable_single_source_revision_is_a_database_noop(
    tmp_path: Path,
):
    fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "stable Stats.csv"
    shutil.copyfile(fixture, stats)
    discovery = KovaaKFileDiscovery(stem="stable-single", stats_path=stats)

    first = await kovaak_run_store.ingest_discovery(discovery, user_id="u1")

    duplicate = await kovaak_run_store.ingest_discovery(discovery, user_id="u1")

    assert duplicate["id"] == first["id"]
    assert len(await kovaak_run_store.list_kovaak_runs("u1")) == 1


@pytest.mark.asyncio
async def test_ingest_forwards_stats_pause_count_to_alignment(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd

    stats_path = tmp_path / "paused Stats.csv"
    performance_path = tmp_path / "paused Performance.perf"
    stats_path.write_text("stats", encoding="utf-8")
    performance_path.write_bytes(b"performance")
    stats = SimpleNamespace(
        file_name=stats_path.name,
        scenario="Scenario",
        summary={
            "Scenario": "Scenario",
            "Challenge Start": "01:46:40.321",
            "Pause Count": "1",
        },
        config={},
        kills=pd.DataFrame(),
    )
    performance_data = PerformanceData(
        header=PerformanceHeader(
            scenario_name="Scenario",
            challenge_start_utc=1_699_897_600_000,
            challenge_profile=ChallengeProfile(time_limit=60.0, timescale=1.0),
        ),
    )
    captured = {}

    monkeypatch.setattr(kovaak_run_store, "parse_stats_csv", lambda _path: stats)
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: performance_data,
    )

    def fail_closed(*_args, **kwargs):
        captured.update(kwargs)
        raise kovaak_run_store.TimeAlignmentError("pause_unsupported: Stats Pause Count > 0")

    monkeypatch.setattr(kovaak_run_store, "resolve_time_window", fail_closed)

    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(
            stem="paused",
            stats_path=stats_path,
            performance_path=performance_path,
        ),
        user_id="u1",
    )

    assert captured["pause_count"] == "1"
    assert run["trace_state"] == "none"


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
    run = await kovaak_run_store.upsert_kovaak_run(
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
        list_response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert list_response.status_code == 200
    listed = list_response.json()["runs"][0]
    assert "stats_path" not in listed
    assert "stats_source_ref" not in listed
    assert "performance_source_ref" not in listed
    assert "stats_summary" not in listed
    assert "performance_summary" not in listed
    assert str(stats) not in list_response.text

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["stats_source_ref"].startswith(f"run:{run['id']}:stats:")
    assert str(stats) not in detail_response.text


@pytest.mark.parametrize(
    "unsafe_digest",
    [
        "not-a-sha256-sentinel",
        r"C:\private\digest.sha256",
        "file:///C:/private/digest.sha256",
        "g" * 64,
        "a" * 63,
    ],
)
@pytest.mark.asyncio
async def test_run_read_models_reject_unsafe_source_digest_refs(
    monkeypatch,
    tmp_path: Path,
    unsafe_digest: str,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "unsafe-digest Stats.csv"
    performance = tmp_path / "unsafe-digest Performance.perf"
    stats.write_text("stats", encoding="utf-8")
    performance.write_bytes(b"performance")

    def source(path: Path, parser_version: str) -> dict[str, object]:
        return {
            "path": str(path),
            "sha256": unsafe_digest,
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "parser_version": parser_version,
        }

    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="unsafe-digest-run",
        scenario="Scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        stats_summary={
            "source": source(stats, kovaak_run_store.STATS_PARSER_VERSION),
        },
        performance_summary={
            "source": source(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert list_response.status_code == 200, list_response.text
    assert detail_response.status_code == 200, detail_response.text
    listed = list_response.json()["runs"][0]
    assert "stats_source_ref" not in listed
    assert "performance_source_ref" not in listed
    detail = detail_response.json()
    assert detail["stats_source_ref"] is None
    assert detail["performance_source_ref"] is None
    assert detail["source_availability"] == {
        "stats": "invalid",
        "performance": "invalid",
    }
    assert unsafe_digest not in list_response.text + detail_response.text


@pytest.mark.asyncio
async def test_run_detail_requires_complete_fingerprint_while_list_remains_stat_only(
    monkeypatch,
    tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "missing-digest Stats.csv"
    stats.write_text("stats", encoding="utf-8")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="missing-digest-run",
        scenario="Scenario",
        stats_path=str(stats),
        stats_summary={
            "source": {
                "path": str(stats),
                "size": stats.stat().st_size,
                "mtime_ns": stats.stat().st_mtime_ns,
                "parser_version": kovaak_run_store.STATS_PARSER_VERSION,
            },
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert list_response.status_code == 200, list_response.text
    assert detail_response.status_code == 200, detail_response.text
    assert (
        list_response.json()["runs"][0]["source_availability"]["stats"]
        == "available"
    )
    detail = detail_response.json()
    assert detail["stats_source_ref"] is None
    assert detail["source_availability"]["stats"] == "invalid"


@pytest.mark.asyncio
async def test_run_list_uses_light_query_and_detail_lazy_loads_summaries(
    monkeypatch,
    tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "private Stats.csv"
    stats.write_text("stats", encoding="utf-8")
    trace = tmp_path / "private-trace.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    summary = {
        "score": 123,
        "private_summary_sentinel": "RUN_SUMMARY_MUST_BE_LAZY",
        "source": kovaak_run_store._source_metadata(
            stats, kovaak_run_store.STATS_PARSER_VERSION,
        ),
    }
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="lazy-run",
        scenario="Scenario",
        stats_path=str(stats),
        stats_summary=summary,
        mouse_trace_path=str(trace),
    )

    def reject_content_hashing(*_args, **_kwargs):
        raise AssertionError("Run list must not hash source contents")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with monkeypatch.context() as guarded:
            guarded.setattr(
                kovaak_run_store,
                "_source_metadata",
                reject_content_hashing,
            )
            listed_response = await client.get(
                "/api/kovaak-runs",
                headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
            )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert listed_response.status_code == 200, listed_response.text
    listed = listed_response.json()["runs"][0]
    assert listed["run_ref"] == f"run:{run['id']}"
    assert listed["source_availability"]["stats"] == "available"
    assert listed["trace_quality"] == {
        "state": "attached",
        "availability": "available",
        "alignment_status": None,
        "coverage": None,
    }
    assert set(listed["trace_quality"]) == {
        "state", "availability", "alignment_status", "coverage",
    }
    assert "stats_source_ref" not in listed
    assert "performance_source_ref" not in listed
    assert "trace_artifact_ref" not in listed
    assert "stats_summary" not in listed
    assert "performance_summary" not in listed
    assert "RUN_SUMMARY_MUST_BE_LAZY" not in listed_response.text

    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["run_ref"] == f"run:{run['id']}"
    assert detail["stats_source_ref"].startswith(f"run:{run['id']}:stats:")
    assert detail["trace_artifact_ref"] == f"run:{run['id']}:trace"
    assert detail["stats_summary"]["private_summary_sentinel"] == (
        "RUN_SUMMARY_MUST_BE_LAZY"
    )
    assert str(stats) not in detail_response.text
    assert str(trace) not in detail_response.text


@pytest.mark.asyncio
async def test_run_read_model_marks_changed_source_invalid_and_preserves_ref(
    monkeypatch,
    tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "revision Stats.csv"
    stats.write_text("first revision", encoding="utf-8")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="revision-run",
        scenario="Scenario",
        stats_path=str(stats),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        stats.write_text("changed revision with different size", encoding="utf-8")
        after = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert before.status_code == after.status_code == 200
    assert before.json()["source_availability"]["stats"] == "available"
    assert after.json()["source_availability"]["stats"] == "invalid"
    assert after.json()["stats_source_ref"] == before.json()["stats_source_ref"]
    assert str(stats) not in after.text


@pytest.mark.parametrize(
    "unsafe_source_key",
    [
        r"C:\Users\dot\private\run",
        "file:///C:/Users/dot/private/run",
    ],
)
@pytest.mark.asyncio
async def test_run_read_models_recursively_filter_path_like_public_fields(
    monkeypatch,
    tmp_path: Path,
    unsafe_source_key: str,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = tmp_path / "unsafe Stats.csv"
    performance = tmp_path / "unsafe Performance.perf"
    stats.write_text("stats", encoding="utf-8")
    performance.write_text("performance", encoding="utf-8")
    unsafe_path = r"C:\Users\dot\private\payload.csv"
    unsafe_uri = "file:///C:/Users/dot/private/payload.csv"
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key=unsafe_source_key,
        scenario="Safe Scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
            "safe_score": 123,
            "path": unsafe_path,
            "nested": {
                "safe_label": "kept",
                "source_path": str(stats),
                "uri_value": unsafe_uri,
                "deep": {"safe_count": 2, "tracePath": unsafe_path},
            },
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
            "safe_event_count": 5,
            "payload": {
                "safe_label": "kept",
                "artifactPath": str(performance),
                "uri": unsafe_uri,
            },
        },
    )
    await kovaak_run_store.mark_mouse_trace_unavailable(
        run["id"],
        config.DESKTOP_LOCAL_PROFILE,
        unsafe_uri,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed_response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert listed_response.status_code == 200, listed_response.text
    assert detail_response.status_code == 200, detail_response.text
    listed = listed_response.json()["runs"][0]
    assert listed["run_ref"] == f"run:{run['id']}"
    assert listed["source_key"] is None
    assert listed["trace_error"] is None

    detail = detail_response.json()
    assert detail["run_ref"] == f"run:{run['id']}"
    assert detail["source_key"] is None
    assert detail["trace_error"] is None
    assert detail["stats_source_ref"].startswith(f"run:{run['id']}:stats:")
    assert detail["performance_source_ref"].startswith(
        f"run:{run['id']}:performance:"
    )
    assert detail["stats_summary"]["safe_score"] == 123
    assert detail["stats_summary"]["nested"] == {
        "safe_label": "kept",
        "deep": {"safe_count": 2},
    }
    assert detail["performance_summary"] == {
        "safe_event_count": 5,
        "payload": {"safe_label": "kept"},
    }
    serialized = listed_response.text + detail_response.text
    for sentinel in (
        unsafe_source_key,
        unsafe_path,
        unsafe_uri,
        str(stats),
        str(performance),
    ):
        assert sentinel not in serialized


@pytest.mark.asyncio
async def test_run_analysis_endpoint_accepts_missing_idempotency_key(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="endpoint-key-required",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
            json={"input_mode": "multimodal", "video_path": str(video)},
        )

    assert response.status_code == 200
    assert isinstance(response.json()["session_id"], int)


@pytest.mark.asyncio
async def test_run_analysis_endpoint_freezes_owned_snapshot_once_for_same_idempotency_key(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import analysis_service, config, queue
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="endpoint-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    calls = 0
    original_create = analysis_service.create_analysis_from_run

    async def counted_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(analysis_service, "create_analysis_from_run", counted_create)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {
            "X-Aiming-Cookie-Desktop-Token": "run-token",
            "Idempotency-Key": "analyze-endpoint-run",
        }
        response = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(video)},
        )
        replay = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(video)},
        )
    assert response.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert calls == 2  # second call is deduplicated by run, not skipped by a journal
    session = await queue.get_session(response.json()["session_id"])
    assert session["input_mode"] == "multimodal"
    assert session["kovaak_run_id"] == run["id"]
    assert session["input_snapshot"]["scenario_identity_version"] == "kovaak_scenario.v1"
    assert session["input_snapshot"]["sources"]["stats"]["artifact_ref"].startswith("run:")
    assert session["input_snapshot"]["sources"]["video"]["fingerprint"] == {
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "size": video.stat().st_size,
        "mtime_ns": video.stat().st_mtime_ns,
    }


@pytest.mark.asyncio
async def test_run_analysis_endpoint_reuses_active_analysis_for_same_run(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="endpoint-conflict",
    )
    first_video = tmp_path / "first.mp4"
    second_video = tmp_path / "second.mp4"
    first_video.write_bytes(b"first")
    second_video.write_bytes(b"second")
    headers = {
        "X-Aiming-Cookie-Desktop-Token": "run-token",
        "Idempotency-Key": "analyze-conflict",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(first_video)},
        )
        conflict = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(second_video)},
        )

    assert first.status_code == 200
    assert conflict.status_code == 200
    assert conflict.json() == first.json()


@pytest.mark.asyncio
async def test_run_analysis_endpoint_reuses_active_analysis_after_video_revision(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="endpoint-same-path-conflict",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"first-video-revision")
    headers = {
        "X-Aiming-Cookie-Desktop-Token": "run-token",
        "Idempotency-Key": "analyze-same-path-conflict",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(video)},
        )
        video.write_bytes(b"second-video-revision")
        conflict = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "multimodal", "video_path": str(video)},
        )

    assert first.status_code == 200
    assert conflict.status_code == 200
    assert conflict.json() == first.json()


@pytest.mark.asyncio
async def test_multimodal_rejects_stats_revision_before_snapshot(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    run, stats, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="owner-copy-race",
        source_key="copy-race-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    stats.write_bytes(stats.read_bytes() + b"\nsource replaced")

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run(
            "owner-copy-race",
            run["id"],
            input_mode="multimodal",
            managed_video_source=video,
        )

    assert exc_info.value.code == "input_unavailable"
    assert await queue.get_active_session("owner-copy-race") is None


@pytest.mark.asyncio
async def test_multimodal_rejects_stats_mtime_changed_before_snapshot(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    run, stats, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="owner-copy-mtime-race",
        source_key="copy-mtime-race-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    stat = stats.stat()
    os.utime(stats, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run(
            "owner-copy-mtime-race",
            run["id"],
            input_mode="multimodal",
            managed_video_source=video,
        )

    assert exc_info.value.code == "input_unavailable"
    assert await queue.get_active_session("owner-copy-mtime-race") is None


@pytest.mark.asyncio
async def test_multimodal_rejects_video_replaced_after_freeze_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="owner-video-copy-race",
        source_key="video-copy-race-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"frozen-video-revision")
    original_copy = analysis_service.copy_path_to_path

    def replace_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            source.write_bytes(b"different-video-revision")
        return original_copy(source, destination)

    monkeypatch.setattr(analysis_service, "copy_path_to_path", replace_video_then_copy)

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run(
            "owner-video-copy-race",
            run["id"],
            input_mode="multimodal",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert str(video) not in exc_info.value.message
    assert await queue.get_active_session("owner-video-copy-race") is None


@pytest.mark.asyncio
async def test_multimodal_rejects_video_mtime_changed_after_freeze_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="owner-video-copy-mtime-race",
        source_key="video-copy-mtime-race-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stable-video-bytes")
    original_copy = analysis_service.copy_path_to_path

    def touch_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            stat = source.stat()
            os.utime(
                source,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
            )
        return original_copy(source, destination)

    monkeypatch.setattr(analysis_service, "copy_path_to_path", touch_video_then_copy)

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run(
            "owner-video-copy-mtime-race",
            run["id"],
            input_mode="multimodal",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert str(video) not in exc_info.value.message
    assert await queue.get_active_session("owner-video-copy-mtime-race") is None


@pytest.mark.asyncio
async def test_multimodal_reports_video_disappearance_during_managed_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="owner-video-copy-disappears",
        source_key="video-copy-disappears-run",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-before-copy")
    original_copy = analysis_service.copy_path_to_path

    def delete_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            source.unlink()
        return original_copy(source, destination)

    monkeypatch.setattr(analysis_service, "copy_path_to_path", delete_video_then_copy)

    with pytest.raises(analysis_service.ProductCommandError) as exc_info:
        await analysis_service.create_analysis_from_run(
            "owner-video-copy-disappears",
            run["id"],
            input_mode="multimodal",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert str(video) not in exc_info.value.message
    assert await queue.get_active_session("owner-video-copy-disappears") is None


@pytest.mark.asyncio
async def test_analysis_input_snapshot_freezes_raw_trace_fingerprint(tmp_path: Path):
    def source_summary(path: Path, parser_version: str) -> dict:
        stat = path.stat()
        return {
            "source": {
                "path": str(path.resolve()),
                "basename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "parser_version": parser_version,
                "availability": "available",
            },
        }

    stats = tmp_path / "Stats.csv"
    performance_file = tmp_path / "Performance.perf"
    trace = tmp_path / "trace.bin"
    stats.write_bytes(b"stats")
    performance_file.write_bytes(b"performance")
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    legacy_bytes = bytearray(trace.read_bytes())
    legacy_bytes[4] = 1
    trace.write_bytes(legacy_bytes)
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="fingerprinted-run",
        scenario="Scenario",
        stats_path=str(stats),
        performance_path=str(performance_file),
        stats_summary=source_summary(stats, "kovaak_stats.v1"),
        performance_summary=source_summary(
            performance_file, "kovaak_performance.v1",
        ),
        mouse_trace_path=str(trace),
    )
    await kovaak_run_store.set_run_alignment(
        run["id"],
        "u1",
        state="resolved",
        summary={
            "start_ms": 1_000,
            "end_ms": 2_000,
            "duration_ms": 1_000,
            "start_source": "stats_challenge_start",
            "end_source": "timer_profile",
            "timebase_version": "time_alignment.v2",
            "stats_anchor_status": "mapped_local_time",
            "stats_time_of_day_ms": 1_000,
            "stats_local_to_utc_mapping": {
                "version": "stats_local_to_utc.v1",
                "source": "fixture",
                "utc_offset_minutes": 0,
            },
            "warnings": [],
        },
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")

    assert snapshot["trace"]["fingerprint"] == {
        "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "size": trace.stat().st_size,
        "mtime_ns": trace.stat().st_mtime_ns,
    }
    public = kovaak_run_store.public_analysis_input_snapshot(snapshot)
    assert public["trace"]["fingerprint"] == snapshot["trace"]["fingerprint"]
    assert "path" not in public["trace"]
    assert snapshot["schema_version"] == "analysis_input_snapshot.v3"
    assert snapshot["canonical_time_window"] == {
        "schema_version": "canonical_time_window.v1",
        "timebase_version": "time_alignment.v2",
        "start_ms": 1_000,
        "end_ms": 2_000,
        "duration_ms": 1_000,
        "start_source": "stats_challenge_start",
        "end_source": "timer_profile",
        "stats_anchor_status": "mapped_local_time",
        "stats_time_of_day_ms": 1_000,
        "stats_local_to_utc_mapping": {
            "version": "stats_local_to_utc.v1",
            "source": "fixture",
            "utc_offset_minutes": 0,
        },
        "warnings": [],
        "window_semantics": "half_open",
    }
    assert snapshot["trace"]["format_version"] == 1
    assert public["canonical_time_window"] == snapshot["canonical_time_window"]


def test_public_analysis_input_snapshot_strips_file_uris():
    public = kovaak_run_store.public_analysis_input_snapshot({
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": 4,
        "scenario": " file:///C:/Users/dot/private/Scenario",
        "sources": {
            "stats": {
                "artifact_ref": "run:4:stats",
                "path": "C:/Users/dot/private/Stats.csv",
                "source_uri": "file:///C:/Users/dot/private/Stats.csv",
            },
        },
        "trace": {
            "artifact_ref": "run:4:trace",
            "path": "C:/Users/dot/private/trace.acri",
            "source_uri": "file:///C:/Users/dot/private/trace.acri",
        },
    })

    assert public["scenario"] is None
    assert public["sources"] == {"stats": {"artifact_ref": "run:4:stats"}}
    assert public["trace"] == {"artifact_ref": "run:4:trace"}


def test_analysis_result_v2_rejects_file_uri_in_public_input_snapshot():
    with pytest.raises(ValueError, match="absolute paths"):
        build_analysis_result_v2(
            analysis_id="analysis:4",
            analysis_type="flicking",
            input_mode="input_native",
            kovaak_run_ref="run:4",
            evidence={
                "sources": {},
                "provenance": {},
                "availability": {},
                "alignment": {},
                "warnings": [],
            },
            deterministic={},
            artifact_manifest=build_artifact_manifest_v2(
                external_inputs=[], owned_outputs=[],
            ),
            input_snapshot={
                "sources": {
                    "stats": {
                        "artifact_ref": "run:4:stats",
                        "source_uri": "file:///C:/Users/dot/private/Stats.csv",
                    },
                },
            },
            created_at="2026-07-16T12:00:00Z",
            completed_at="2026-07-16T12:01:00Z",
            warnings=[],
            errors=[],
        )


@pytest.mark.asyncio
async def test_public_run_read_models_mark_corrupt_attached_trace_unavailable(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    trace = tmp_path / "attached-trace.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=config.DESKTOP_LOCAL_PROFILE,
        source_key="corrupt-trace-run",
        scenario="Scenario",
        mouse_trace_path=str(trace),
    )
    trace.write_bytes(b"corrupt trace")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed_response = await client.get(
            "/api/kovaak-runs",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )
        detail_response = await client.get(
            f"/api/kovaak-runs/{run['id']}",
            headers={"X-Aiming-Cookie-Desktop-Token": "run-token"},
        )

    assert listed_response.status_code == detail_response.status_code == 200
    listed = listed_response.json()["runs"][0]
    detail = detail_response.json()
    assert listed["trace_state"] == detail["trace_state"] == "attached"
    assert listed["trace_quality"]["availability"] == "unavailable"
    assert detail["trace_quality"]["availability"] == "unavailable"


@pytest.mark.asyncio
async def test_run_summary_validates_attached_trace_without_decoding_points(
    monkeypatch, tmp_path: Path,
):
    trace = tmp_path / "attached-trace.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="lightweight-trace-run",
        scenario="Scenario",
        mouse_trace_path=str(trace),
    )

    def fail_if_decoded(*_args, **_kwargs):
        raise AssertionError("Run summary must not decode trace points")

    monkeypatch.setattr(kovaak_run_store, "read_mouse_snapshot", fail_if_decoded)

    summaries = await kovaak_run_store.list_kovaak_run_summaries("u1")

    assert summaries[0]["trace_quality"]["availability"] == "available"


def test_raw_input_snapshot_codec_and_window_extraction(tmp_path: Path):
    snapshot = tmp_path / "buffer.bin"
    destination = tmp_path / "runs" / "1" / "mouse_trace.bin"
    points = [
        {"timestamp_ms": 100, "dx": 1, "dy": 2, "buttons": 0},
        {"timestamp_ms": 200, "dx": -3, "dy": 4, "buttons": 0},
        {"timestamp_ms": 400, "dx": 5, "dy": 6, "buttons": 0},
    ]
    kovaak_run_store.write_mouse_snapshot(snapshot, points)
    assert kovaak_run_store.read_mouse_snapshot(snapshot) == points
    assert kovaak_run_store.decode_mouse_snapshot_bytes(snapshot.read_bytes()) == points
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

    with pytest.raises(kovaak_run_store.RetryableIngestionError, match="trace_pending"):
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


@pytest.mark.asyncio
async def test_automatic_trace_attachment_requires_native_snapshot_coverage_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from webapp.backend import config

    performance = tmp_path / "Scenario Performance.perf"
    performance.write_bytes(b"performance")
    raw_snapshot = tmp_path / "raw.bin"
    # A player may be still through the final part of a Challenge. The last Raw
    # event therefore cannot be used as a tail-coverage signal.
    kovaak_run_store.write_mouse_snapshot(raw_snapshot, [
        {"timestamp_ms": 1_100, "dx": 3, "dy": -2, "buttons": 0},
    ])
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(kovaak_run_store, "_now_ms", lambda: 2_001)
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: PerformanceData(
            header=PerformanceHeader(
                scenario_name="Scenario",
                challenge_start_utc=1_000,
                challenge_profile=ChallengeProfile(time_limit=1.0),
            ),
        ),
    )
    discovery = KovaaKFileDiscovery(stem="covered", performance_path=performance)

    with pytest.raises(kovaak_run_store.RetryableIngestionError, match="coverage"):
        await kovaak_run_store.ingest_discovery(
            discovery,
            user_id="u1",
            raw_input_snapshot_path=raw_snapshot,
            raw_snapshot_covered_through_epoch_ms=1_999,
        )

    attached = await kovaak_run_store.ingest_discovery(
        discovery,
        user_id="u1",
        raw_input_snapshot_path=raw_snapshot,
        raw_snapshot_covered_through_epoch_ms=2_000,
    )

    assert attached["trace_state"] == "attached"
    assert kovaak_run_store.read_mouse_snapshot(attached["mouse_trace_path"]) == [
        {"timestamp_ms": 1_100, "dx": 3, "dy": -2, "buttons": 0},
    ]


@pytest.mark.asyncio
async def test_stale_snapshot_coverage_becomes_unavailable_after_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from webapp.backend import config

    performance = tmp_path / "Scenario Performance.perf"
    performance.write_bytes(b"performance")
    raw_snapshot = tmp_path / "raw.bin"
    kovaak_run_store.write_mouse_snapshot(raw_snapshot, [
        {"timestamp_ms": 1_100, "dx": 3, "dy": -2, "buttons": 0},
    ])
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        kovaak_run_store,
        "_now_ms",
        lambda: 2_000 + kovaak_run_store.MAX_SNAPSHOT_SPAN_MS + 1,
    )
    monkeypatch.setattr(
        kovaak_run_store,
        "parse_performance_file",
        lambda _path: PerformanceData(
            header=PerformanceHeader(
                scenario_name="Scenario",
                challenge_start_utc=1_000,
                challenge_profile=ChallengeProfile(time_limit=1.0),
            ),
        ),
    )

    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="stale-coverage", performance_path=performance),
        user_id="u1",
        raw_input_snapshot_path=raw_snapshot,
        raw_snapshot_covered_through_epoch_ms=1_999,
    )

    assert run["trace_state"] == "unavailable"
    assert run["trace_error"] == "trace_snapshot_stale"
    assert run["mouse_trace_path"] is None
    # The run record is still persisted so the run is not lost.
    stored = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    assert stored is not None
    assert stored["trace_state"] == "unavailable"



def test_python_reads_shared_acri_v1_golden_fixture_and_writes_v2(tmp_path: Path):
    fixture = Path(__file__).with_name("fixtures") / "acri-v1-golden.bin"

    expected = [
        {"timestamp_ms": 1_700_000_000_000, "dx": -2, "dy": 4, "buttons": 1},
        {"timestamp_ms": 1_700_000_000_016, "dx": 8, "dy": -9, "buttons": 0},
    ]
    assert kovaak_run_store.read_mouse_snapshot(fixture) == expected

    version, decoded = kovaak_run_store.read_mouse_snapshot_with_version(fixture)
    assert version == 1
    assert decoded == expected

    python_output = tmp_path / "python-v2.bin"
    kovaak_run_store.write_mouse_snapshot(python_output, expected)
    assert python_output.read_bytes()[4] == 2
    assert kovaak_run_store.read_mouse_snapshot(python_output) == [
        {"timestamp_ms": 1_700_000_000_000, "dx": 0, "dy": 0, "buttons": 1},
        expected[0],
        {"timestamp_ms": 1_700_000_000_016, "dx": 0, "dy": 0, "buttons": 0},
        expected[1],
    ]


def test_acri_reader_rejects_unknown_version(tmp_path: Path):
    fixture = Path(__file__).with_name("fixtures") / "acri-v1-golden.bin"
    unknown = bytearray(fixture.read_bytes())
    unknown[4] = 99
    path = tmp_path / "acri-unknown.bin"
    path.write_bytes(unknown)

    with pytest.raises(ValueError, match="unsupported raw input snapshot version"):
        kovaak_run_store.read_mouse_snapshot(path)


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
async def test_stale_trace_writer_cannot_overwrite_newer_pending_or_attached_trace(
    tmp_path: Path,
):
    first_trace = tmp_path / "runs" / "1" / "trace-first.bin"
    second_trace = tmp_path / "runs" / "1" / "trace-second.bin"
    for path, dx in ((first_trace, 1), (second_trace, 2)):
        kovaak_run_store.write_mouse_snapshot(path, [
            {"timestamp_ms": 100, "dx": dx, "dy": 0, "buttons": 1},
        ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="concurrent-trace",
    )

    await kovaak_run_store.begin_mouse_trace_attach(run["id"], "u1", first_trace)
    await kovaak_run_store.begin_mouse_trace_attach(run["id"], "u1", second_trace)

    stale_waiting = await kovaak_run_store.mark_mouse_trace_waiting(
        run["id"],
        "u1",
        expected_pending_trace_path=first_trace,
    )
    assert stale_waiting["trace_state"] == "pending"
    assert stale_waiting["pending_trace_path"] == str(second_trace)
    assert stale_waiting["trace_error"] is None

    stale_attach = await kovaak_run_store.attach_mouse_trace(
        run["id"],
        "u1",
        str(first_trace),
        expected_pending_trace_path=first_trace,
    )
    assert stale_attach["trace_state"] == "pending"
    assert stale_attach["pending_trace_path"] == str(second_trace)
    assert stale_attach["mouse_trace_path"] is None

    attached = await kovaak_run_store.attach_mouse_trace(
        run["id"],
        "u1",
        str(second_trace),
        expected_pending_trace_path=second_trace,
    )
    assert attached["trace_state"] == "attached"
    assert attached["mouse_trace_path"] == str(second_trace)

    stale_failure = await kovaak_run_store.mark_mouse_trace_unavailable(
        run["id"],
        "u1",
        "trace_snapshot_failed",
        expected_pending_trace_path=first_trace,
    )
    assert stale_failure["trace_state"] == "attached"
    assert stale_failure["mouse_trace_path"] == str(second_trace)
    assert stale_failure["trace_error"] is None


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


def test_public_analysis_input_snapshot_preserves_scenario_identity_version():
    public = kovaak_run_store.public_analysis_input_snapshot({
        "schema_version": "analysis_input_snapshot.v1",
        "run_id": 4,
        "scenario": "1wall",
        "scenario_identity_version": "kovaak_scenario.v1",
        "sources": {"stats": {"artifact_ref": "run:4:stats", "path": "/private/stats.csv"}},
        "trace": {"artifact_ref": "run:4:trace", "path": "/private/trace.acri"},
    })

    assert public["scenario_identity_version"] == "kovaak_scenario.v1"
    assert "path" not in public["sources"]["stats"]
    assert "path" not in public["trace"]


@pytest.mark.asyncio
async def test_analysis_input_snapshot_freezes_unreviewed_hash_as_name_candidate(
    tmp_path: Path,
    monkeypatch,
):
    stats = tmp_path / "same-name Stats.csv"
    performance = tmp_path / "same-name Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    run = {
        "id": 41,
        "scenario": "Same Display Name",
        "stats_path": str(stats),
        "performance_path": str(performance),
        "stats_summary": {
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        "performance_summary": {
            "header": {"scenario_hash": "observed-but-unreviewed-hash"},
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
        "trace_state": "none",
        "alignment_state": "unavailable",
        "video_state": "none",
    }

    async def get_run(_run_id: int, _user_id: str):
        return run

    monkeypatch.setattr(kovaak_run_store, "get_kovaak_run", get_run)

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(41, "u1")

    resolution = snapshot["scenario_resolution"]
    assert resolution["scenario_hash"] == "observed-but-unreviewed-hash"
    assert resolution["display_name"] == "Same Display Name"
    assert resolution["scenario_profile_ref"] is None
    assert resolution["classification_source"] == "name_heuristic"
    assert resolution["classification_confidence"] == "candidate"
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    assert resolution["family_analyzer_dispatch"] == "allowed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    public = kovaak_run_store.public_analysis_input_snapshot(snapshot)
    assert public["scenario_resolution"] == resolution
    assert str(tmp_path) not in json.dumps(public)


def _shape_refine_run() -> dict:
    return {
        "stats_summary": {"kill_count": 0},
        "performance_summary": {"header": {"scenario_hash": "unreviewed-hash"}},
    }


def _shape_refine_snapshot(resolution: dict) -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "scenario": "Air Angelic 4 Voltaic Easy",
        "scenario_resolution": resolution,
        "canonical_time_window": {
            "schema_version": "canonical_time_window.v1",
            "start_ms": 0,
            "end_ms": 30_000,
            "duration_ms": 30_000,
        },
    }


def test_challenge_button_samples_held_counts_in_window_held_samples(tmp_path: Path):
    from webapp.backend import analysis_service
    from webapp.backend.kovaak_snapshot_codec import write_mouse_snapshot

    trace_path = tmp_path / "trace.acri"
    write_mouse_snapshot(trace_path, [
        {"timestamp_ms": 0, "dx": 1, "dy": 0, "buttons": 0},
        {"timestamp_ms": 1, "dx": 0, "dy": 1, "buttons": 1},
        {"timestamp_ms": 2, "dx": 1, "dy": 1, "buttons": 1},
        {"timestamp_ms": 3, "dx": 0, "dy": 0, "buttons": 0},
        {"timestamp_ms": 900, "dx": 2, "dy": 0, "buttons": 1},  # 窗外不计
    ])
    snapshot = {
        "trace": {"availability": "available", "path": str(trace_path)},
        "canonical_time_window": {"start_ms": 0, "end_ms": 10, "duration_ms": 10},
    }

    # canonical 解码后按住状态同时出现在按钮状态点与移动点上：
    # 窗内 held = t=1 的状态点+移动点、t=2 的移动点共 3 个；t=900 在窗外不计。
    assert analysis_service._challenge_button_samples_held(snapshot) == 3
    shape = analysis_service._challenge_shape_for_run(
        {"stats_summary": {"kill_count": 39}}, snapshot,
    )
    assert shape["button_samples_held"] == 3

    # trace 缺失/不可解码时安静退化为无 raw 信号。
    assert analysis_service._challenge_button_samples_held(
        {"canonical_time_window": snapshot["canonical_time_window"]},
    ) is None
    assert analysis_service._challenge_button_samples_held({
        "trace": {"availability": "available", "path": str(tmp_path / "missing.acri")},
        "canonical_time_window": snapshot["canonical_time_window"],
    }) is None


def test_analysis_service_refines_name_candidates_with_challenge_shape():
    from webapp.backend import analysis_service
    from kovaak_tracker import scenario_profiles

    name_resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash", display_name="Air Angelic 4 Voltaic Easy",
    )
    assert name_resolution["classification_source"] == "name_heuristic"
    snapshot = _shape_refine_snapshot(name_resolution)
    # 该合成 run 无 trace → 无按住采样，0 杀走无 raw 弱判据（kill_density_fallback）。

    refined = analysis_service._apply_challenge_shape_resolution(
        _shape_refine_run(), snapshot,
    )

    assert refined["scenario_resolution"]["classification_source"] == "challenge_shape"
    assert refined["scenario_resolution"]["aim_family"] == "continuous_tracking"
    assert refined["scenario_challenge_shape"] == {
        "schema_version": "scenario_challenge_shape.v1",
        "kills": 0,
        "duration_ms": 30_000,
    }
    # 原快照不被就地修改。
    assert snapshot["scenario_resolution"]["classification_source"] == "name_heuristic"
    assert "scenario_challenge_shape" not in snapshot

    from webapp.backend import kovaak_run_projection
    public = kovaak_run_projection.public_analysis_input_snapshot(refined)
    assert public["scenario_challenge_shape"] == refined["scenario_challenge_shape"]


def test_analysis_service_keeps_exact_and_local_definitions_over_challenge_shape():
    from webapp.backend import analysis_service
    from kovaak_tracker import scenario_profiles

    exact = scenario_profiles.resolve_scenario_profile(
        "b2ae4a24b710e36afc6e57c61f590ab4",
        display_name="WHJ SmoothStrafeSphere Easy",
    )
    unchanged = analysis_service._apply_challenge_shape_resolution(
        _shape_refine_run(), _shape_refine_snapshot(exact),
    )
    assert unchanged["scenario_resolution"] == exact
    assert "scenario_challenge_shape" not in unchanged

    local_definition = dict(exact, classification_source="local_scenario_definition")
    kept = analysis_service._apply_challenge_shape_resolution(
        _shape_refine_run(), _shape_refine_snapshot(local_definition),
    )
    assert kept["scenario_resolution"] == local_definition


def test_analysis_service_skips_challenge_shape_in_the_undecided_band():
    from webapp.backend import analysis_service
    from kovaak_tracker import scenario_profiles

    name_resolution = scenario_profiles.resolve_scenario_profile(
        "unreviewed-hash", display_name="Air Angelic 4 Voltaic Easy",
    )
    # 无 trace、34-39 杀区间（密度判据已推翻）→ 诚实不判定，保持名称层结果。
    run = {"stats_summary": {"kill_count": 39}}
    snapshot = _shape_refine_snapshot(name_resolution)
    snapshot["canonical_time_window"] = {
        **snapshot["canonical_time_window"],
        "end_ms": 90_000,
        "duration_ms": 90_000,
    }

    refined = analysis_service._apply_challenge_shape_resolution(run, snapshot)

    assert refined["scenario_resolution"] == name_resolution
    assert "scenario_challenge_shape" not in refined


_OVERRIDE_HASH = "0123456789abcdef" * 2


def _write_scenario_overrides(overrides: dict) -> None:
    from webapp.backend import config as backend_config

    config_dir = backend_config.DATA_ROOT / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "scenario-overrides.json").write_text(
        json.dumps({
            "schema_version": "scenario_overrides.v1",
            "overrides": overrides,
        }),
        encoding="utf-8",
    )


def test_scenario_override_beats_name_and_shape_layers():
    from webapp.backend import analysis_service
    from webapp.backend.contracts import validate_scenario_resolution_v1
    from kovaak_tracker import scenario_profiles

    name_resolution = scenario_profiles.resolve_scenario_profile(
        _OVERRIDE_HASH, display_name="Air Angelic 4 Voltaic Easy",
    )
    assert name_resolution["classification_source"] == "name_heuristic"
    _write_scenario_overrides({
        _OVERRIDE_HASH: {
            "aim_family": "static_clicking",
            "confirmed_by": "user",
            "note": "1w4ts = one wall four targets small",
            "updated_at": "2026-08-15T00:00:00Z",
        },
    })
    snapshot = _shape_refine_snapshot(name_resolution)

    overridden = analysis_service._apply_scenario_override_resolution(snapshot)

    resolution = overridden["scenario_resolution"]
    assert resolution["classification_source"] == "scenario_override"
    assert resolution["aim_family"] == "static_clicking"
    assert resolution["classification_confidence"] == "confirmed"
    assert resolution["claim_ceiling"] == "descriptive_only"
    assert resolution["allowed_analyzers"] == ["static_clicking.baseline.v1"]
    # 识别链后续层（challenge shape）不再覆盖 override。
    shaped = analysis_service._apply_challenge_shape_resolution(
        _shape_refine_run(), overridden,
    )
    assert shaped["scenario_resolution"]["classification_source"] == "scenario_override"
    assert "scenario_challenge_shape" not in shaped
    # 新来源通过 frozen contracts 校验，并驱动该 family 的 analysis_type。
    validate_scenario_resolution_v1(resolution)
    assert analysis_service._analysis_type_for_snapshot(overridden) == "static_clicking"
    # 原快照不被就地修改。
    assert snapshot["scenario_resolution"]["classification_source"] == "name_heuristic"


def test_exact_reviewed_hash_beats_scenario_override():
    from webapp.backend import analysis_service
    from kovaak_tracker import scenario_profiles

    exact = scenario_profiles.resolve_scenario_profile(
        "b2ae4a24b710e36afc6e57c61f590ab4",
        display_name="WHJ SmoothStrafeSphere Easy",
    )
    _write_scenario_overrides({
        "b2ae4a24b710e36afc6e57c61f590ab4": {
            "aim_family": "static_clicking",
            "confirmed_by": "user",
            "note": None,
            "updated_at": "2026-08-15T00:00:00Z",
        },
    })

    unchanged = analysis_service._apply_scenario_override_resolution(
        _shape_refine_snapshot(exact),
    )

    assert unchanged["scenario_resolution"] == exact


def test_scenario_override_miss_or_bad_entry_falls_back_to_the_original_chain():
    from webapp.backend import analysis_service
    from kovaak_tracker import scenario_profiles

    name_resolution = scenario_profiles.resolve_scenario_profile(
        _OVERRIDE_HASH, display_name="Air Angelic 4 Voltaic Easy",
    )
    # 未写入 override 文件 → 原链不变。
    untouched = analysis_service._apply_scenario_override_resolution(
        _shape_refine_snapshot(name_resolution),
    )
    assert untouched["scenario_resolution"] == name_resolution

    # 命中但条目不合法（family 越界）→ 该条目被跳过，仍落原链。
    _write_scenario_overrides({
        _OVERRIDE_HASH: {"aim_family": "movement_aiming", "confirmed_by": "user"},
    })
    skipped = analysis_service._apply_scenario_override_resolution(
        _shape_refine_snapshot(name_resolution),
    )
    assert skipped["scenario_resolution"] == name_resolution

    # note 超长同理被跳过。
    _write_scenario_overrides({
        _OVERRIDE_HASH: {"aim_family": "static_clicking", "note": "x" * 201},
    })
    skipped_note = analysis_service._apply_scenario_override_resolution(
        _shape_refine_snapshot(name_resolution),
    )
    assert skipped_note["scenario_resolution"] == name_resolution


async def _override_ready_run(tmp_path: Path, *, owner: str) -> dict:
    """A complete Run whose Performance hash is _OVERRIDE_HASH (no override file yet)."""
    stats = tmp_path / f"{owner}-Stats.csv"
    performance = tmp_path / f"{owner}-Performance.perf"
    trace = tmp_path / f"{owner}-trace.bin"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=owner,
        source_key=f"{owner}-run",
        scenario="Complete test scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        mouse_trace_path=str(trace),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
            "header": {"scenario_hash": _OVERRIDE_HASH},
        },
    )
    run = await kovaak_run_store.set_run_alignment(
        run["id"],
        owner,
        state="resolved",
        summary={
            "start_ms": 1_000,
            "end_ms": 2_000,
            "duration_ms": 1_000,
            "start_source": "test_start",
            "end_source": "test_end",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    ) or run
    return run


def _mark_session_done(session_id: int, result: dict | None = None) -> None:
    from webapp.backend import queue

    session = queue._load_session(session_id)
    assert isinstance(session, dict)
    session["status"] = "done"
    if result is not None:
        session["result"] = result
    queue._save_session(session)


@pytest.mark.asyncio
async def test_create_from_run_enqueues_the_override_family(
    monkeypatch, tmp_path: Path,
):
    """先写 override 再 create_from_run：入队 snapshot 的 resolution 用 override family。"""
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    _write_scenario_overrides({
        _OVERRIDE_HASH: {
            "aim_family": "target_switching",
            "confirmed_by": "user",
            "note": "1w4ts = one wall four targets small",
            "updated_at": "2026-08-15T00:00:00Z",
        },
    })
    owner = "owner-override-create"
    run = await _override_ready_run(tmp_path, owner=owner)
    video = tmp_path / f"{owner}-clip.mp4"
    video.write_bytes(b"video")

    created = await analysis_service.create_analysis_from_run(
        owner,
        run["id"],
        managed_video_source=video,
    )

    session = await queue.get_session(created["session_id"])
    resolution = session["input_snapshot"]["scenario_resolution"]
    assert resolution["classification_source"] == "scenario_override"
    assert resolution["aim_family"] == "target_switching"
    # 名称层本会把 Complete test scenario 判成 static_clicking；override 改写了入队类型。
    assert session["analysis_type"] == "target_switching"


@pytest.mark.asyncio
async def test_reclassified_done_analysis_creates_a_new_session(
    monkeypatch, tmp_path: Path,
):
    """类型被 override 改变的 done Run：create_from_run 新建新 session 而非复用旧的。"""
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    owner = "owner-reclassify"
    run = await _override_ready_run(tmp_path, owner=owner)
    video = tmp_path / f"{owner}-clip.mp4"
    video.write_bytes(b"video")

    first = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    first_session = await queue.get_session(first["session_id"])
    assert first_session["analysis_type"] == "static_clicking"
    _mark_session_done(first["session_id"])

    # 用户纠正为 target_switching → set 记忆 → 再 create 应按新类型新建。
    _write_scenario_overrides({
        _OVERRIDE_HASH: {"aim_family": "target_switching", "confirmed_by": "user"},
    })
    second = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )

    assert second["session_id"] != first["session_id"]
    assert "reused" not in second
    second_session = await queue.get_session(second["session_id"])
    resolution = second_session["input_snapshot"]["scenario_resolution"]
    assert resolution["classification_source"] == "scenario_override"
    assert second_session["analysis_type"] == "target_switching"
    # 旧分析保持不动，成为该 run 的历史版本。
    assert (await queue.get_session(first["session_id"]))["analysis_type"] == "static_clicking"


@pytest.mark.asyncio
async def test_override_matching_the_done_family_keeps_the_reuse(
    monkeypatch, tmp_path: Path,
):
    """override 命中但 family 与 done 分析一致：仍复用，不产生重复分析。"""
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    owner = "owner-same-family"
    run = await _override_ready_run(tmp_path, owner=owner)
    video = tmp_path / f"{owner}-clip.mp4"
    video.write_bytes(b"video")

    first = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    assert (await queue.get_session(first["session_id"]))["analysis_type"] == "static_clicking"
    _mark_session_done(first["session_id"])

    # 用户确认的类型与现有分析一致（static_clicking）→ 复用旧的。
    _write_scenario_overrides({
        _OVERRIDE_HASH: {"aim_family": "static_clicking", "confirmed_by": "user"},
    })
    again = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )

    assert again["reused"] is True
    assert again["session_id"] == first["session_id"]
    assert len(await queue.get_run_analysis_states(owner, run["id"])) == 1


@pytest.mark.asyncio
async def test_repeat_create_without_override_reuses_without_freezing(
    monkeypatch, tmp_path: Path,
):
    """无 override 的普通重复调用：走快速复用，不重建输入快照（零冻结开销）。"""
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    owner = "owner-fast-reuse"
    run = await _override_ready_run(tmp_path, owner=owner)
    video = tmp_path / f"{owner}-clip.mp4"
    video.write_bytes(b"video")

    first = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    _mark_session_done(first["session_id"])

    async def _boom_snapshot(*_args, **_kwargs):
        raise AssertionError("fast reuse must not rebuild the input snapshot")

    monkeypatch.setattr(kovaak_run_store, "build_analysis_input_snapshot", _boom_snapshot)
    repeat = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )

    assert repeat["reused"] is True
    assert repeat["session_id"] == first["session_id"]
    assert len(await queue.get_run_analysis_states(owner, run["id"])) == 1


@pytest.mark.asyncio
async def test_outcome_only_done_analysis_rebuilds_instead_of_reusing(
    monkeypatch, tmp_path: Path,
):
    """done 但版本是 outcome_only 降级的旧分析（老包残留）：重建而非复用空结果。"""
    from webapp.backend import analysis_service, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    owner = "owner-outcome-only-rebuild"
    run = await _override_ready_run(tmp_path, owner=owner)
    video = tmp_path / f"{owner}-clip.mp4"
    video.write_bytes(b"video")

    first = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    _mark_session_done(
        first["session_id"], result={"analysis_version": "scenario_outcome_only.v1"},
    )

    second = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    assert second["session_id"] != first["session_id"]
    assert "reused" not in second

    # 重建出的正常分析成为新的复用答案，不会无限重建。
    _mark_session_done(second["session_id"])
    third = await analysis_service.create_analysis_from_run(
        owner, run["id"], managed_video_source=video,
    )
    assert third["reused"] is True
    assert third["session_id"] == second["session_id"]


@pytest.mark.asyncio
async def test_analysis_input_snapshot_freezes_local_dynamic_behavior_descriptor(
    tmp_path: Path,
    monkeypatch,
):
    stats = tmp_path / "pasu Stats.csv"
    performance = tmp_path / "pasu Performance.perf"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    scenario_dir = tmp_path / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_dir.joinpath("1wall5targets_pasu.sce").write_text(
        """Name=1wall5targets_pasu
AddedBots=test.bot;test.bot;test.bot;test.bot;test.bot
PlayerCharacters=A_air_pistol_frozen

[Bot Profile]
Name=test
DodgeProfileNames=test
CharacterProfile=react

[Character Profile]
Name=react
MaxSpeed=1300.0

[Dodge Profile]
Name=test
ToggleLeftRight=true
ToggleForwardBack=true

[Character Profile]
Name=A_air_pistol_frozen
WeaponProfileNames=pistol

[Weapon Profile]
Name=pistol
Type=Hitscan
ShotsPerClick=1
DamagePerShot=1000.0
Category=SemiAuto
""",
        encoding="utf-8",
    )
    run = {
        "id": 42,
        "scenario": "1wall5targets_pasu",
        "stats_path": str(stats),
        "performance_path": str(performance),
        "stats_summary": {"source": kovaak_run_store._source_metadata(stats, kovaak_run_store.STATS_PARSER_VERSION)},
        "performance_summary": {
            "header": {"scenario_hash": "a5be19c6e6aeb0d774c5e9d9fb497e91"},
            "source": kovaak_run_store._source_metadata(performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION),
        },
        "trace_state": "none",
        "alignment_state": "unavailable",
        "video_state": "none",
    }

    async def get_run(_run_id: int, _user_id: str):
        return run

    monkeypatch.setattr(kovaak_run_store, "get_kovaak_run", get_run)
    monkeypatch.setenv("KOVAAK_INSTALL_DIR", str(tmp_path))
    snapshot = await kovaak_run_store.build_analysis_input_snapshot(42, "u1")

    assert snapshot["scenario_resolution"]["classification_source"] == "local_scenario_definition"
    assert snapshot["scenario_resolution"]["aim_family"] == "dynamic_clicking"
    assert snapshot["scenario_behavior_descriptor"] == {
        "schema_version": "scenario_behavior_descriptor.v1",
        "display_name": "1wall5targets_pasu",
        "source_sha256": snapshot["scenario_behavior_descriptor"]["source_sha256"],
        "bot_count": 5,
        "reactive_bot_count": 5,
        "dodge_axes": ["horizontal", "depth"],
        "weapon": {
            "delivery": "hitscan",
            "fire_mode": "semi_auto",
            "shots_per_click": 1,
            "damage_per_shot": 1000.0,
        },
    }
    assert str(tmp_path) not in json.dumps(kovaak_run_store.public_analysis_input_snapshot(snapshot))


def _write_capture_video_bundle(
    video_path: Path,
    *,
    run_id: int,
    request_id: str = "request-1",
    request_digest: str = "a" * 64,
    capture_session_id: str = "session-1",
    start_epoch_ms: int = 1_000,
    end_epoch_ms: int = 2_000,
    contents: bytes = b"run-owned-mp4",
) -> tuple[Path, dict]:
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(contents)
    receipt = {
        "version": "capture_receipt.v1",
        "requestDigest": request_digest,
        "requestId": request_id,
        "runId": run_id,
        "captureSessionId": capture_session_id,
        "startEpochMs": start_epoch_ms,
        "endEpochMs": end_epoch_ms,
        "replay": {
            "requestedStart100ns": 20_000_000,
            "requestedEnd100ns": 30_000_000,
            "decodeStart100ns": 19_000_000,
            "visibleDuration100ns": 10_000_000,
            "decodePreroll100ns": 1_000_000,
            "packetCount": 60,
            "encodedBytes": len(contents),
            "reencodedFrames": 0,
            "captureClock": {
                "utcEpochMs": 1_000,
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
    receipt_path = video_path.with_name(f"{video_path.stem}.receipt.json")
    receipt_path.write_text(
        json.dumps(receipt, separators=(",", ":")),
        encoding="utf-8",
    )
    return receipt_path, receipt


@pytest.mark.asyncio
async def test_video_pending_attach_is_managed_and_persists_canonical_receipt(
    tmp_path: Path,
):
    data_root = tmp_path / "data"
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="video-attach",
    )
    video = data_root / "runs" / str(run["id"]) / "video-request-1.mp4"
    request_digest = "a" * 64

    pending = await kovaak_run_store.begin_run_video_attach(
        run["id"],
        "u1",
        pending_video_path=video,
        request_digest=request_digest,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        alignment_summary={"method": "time_alignment.v2"},
        data_root=data_root,
    )
    assert pending["video_state"] == "pending"
    assert pending["pending_video_path"] == str(video.resolve())
    assert pending["capture_session_id"] == "session-1"
    assert pending["window_start_epoch_ms"] == 1_000
    assert pending["window_end_epoch_ms"] == 2_000
    assert pending["alignment_state"] == "resolved"
    assert pending["finalization_state"] == "pending"

    _write_capture_video_bundle(video, run_id=run["id"])
    attached = await kovaak_run_store.attach_run_video(
        run["id"],
        "u1",
        video,
        expected_pending_video_path=video,
        expected_request_digest=request_digest,
        data_root=data_root,
    )

    assert attached["video_state"] == "attached"
    assert attached["video_path"] == str(video.resolve())
    assert attached["pending_video_path"] is None
    assert attached["video_request_digest"] == request_digest
    assert attached["video_receipt"]["requestDigest"] == request_digest
    assert attached["video_summary"] == {
        "availability": "available",
        "fingerprint": {
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "size": video.stat().st_size,
        },
        "packetCount": 60,
        "visibleDuration100ns": 10_000_000,
        "timebaseVersion": "time_alignment.v2",
    }
    assert str(tmp_path) not in json.dumps(attached["video_summary"])


@pytest.mark.asyncio
async def test_stale_video_finalizer_cannot_replace_newer_or_attached_artifact(
    tmp_path: Path,
):
    data_root = tmp_path / "data"
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="stale-video",
    )
    run_root = data_root / "runs" / str(run["id"])
    first = run_root / "video-request-first.mp4"
    second = run_root / "video-request-second.mp4"
    first_digest = "a" * 64
    second_digest = "b" * 64
    await kovaak_run_store.begin_run_video_attach(
        run["id"], "u1",
        pending_video_path=first,
        request_digest=first_digest,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    await kovaak_run_store.mark_run_video_unavailable(
        run["id"], "u1", "video_retryable_failure",
        expected_pending_video_path=first,
        expected_request_digest=first_digest,
    )
    await kovaak_run_store.begin_run_video_attach(
        run["id"], "u1",
        pending_video_path=second,
        request_digest=second_digest,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    _write_capture_video_bundle(
        first,
        run_id=run["id"],
        request_id="request-first",
        request_digest=first_digest,
    )
    _write_capture_video_bundle(
        second,
        run_id=run["id"],
        request_id="request-second",
        request_digest=second_digest,
    )

    stale = await kovaak_run_store.attach_run_video(
        run["id"], "u1", first,
        expected_pending_video_path=first,
        expected_request_digest=first_digest,
        data_root=data_root,
    )
    assert stale["video_state"] == "pending"
    assert stale["pending_video_path"] == str(second.resolve())

    attached = await kovaak_run_store.attach_run_video(
        run["id"], "u1", second,
        expected_pending_video_path=second,
        expected_request_digest=second_digest,
        data_root=data_root,
    )
    repeated = await kovaak_run_store.attach_run_video(
        run["id"], "u1", second,
        expected_pending_video_path=second,
        expected_request_digest=second_digest,
        data_root=data_root,
    )
    assert repeated["id"] == attached["id"]
    assert repeated["video_path"] == str(second.resolve())
    assert repeated["video_receipt"] == attached["video_receipt"]

    rediscovered = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="stale-video",
        stats_summary={"source_revision": "same-run"},
    )
    assert rediscovered["id"] == attached["id"]
    assert rediscovered["video_state"] == "attached"
    assert rediscovered["video_path"] == str(second.resolve())
    assert rediscovered["video_receipt"] == attached["video_receipt"]

    stale_failure = await kovaak_run_store.mark_run_video_unavailable(
        run["id"], "u1", "stale_failure",
        expected_pending_video_path=first,
        expected_request_digest=first_digest,
    )
    assert stale_failure["video_state"] == "attached"
    assert stale_failure["video_path"] == str(second.resolve())

    replacement = run_root / "video-replacement.mp4"
    after_replacement_begin = await kovaak_run_store.begin_run_video_attach(
        run["id"], "u1",
        pending_video_path=replacement,
        request_digest="c" * 64,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    assert after_replacement_begin["video_state"] == "attached"
    assert after_replacement_begin["video_path"] == str(second.resolve())


@pytest.mark.asyncio
async def test_video_attach_rejects_unmanaged_tampered_or_conflicting_receipts(
    tmp_path: Path,
):
    data_root = tmp_path / "data"
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="invalid-video",
    )
    external = tmp_path / "external.mp4"
    with pytest.raises(ValueError, match="managed Run root"):
        await kovaak_run_store.begin_run_video_attach(
            run["id"], "u1",
            pending_video_path=external,
            request_digest="a" * 64,
            capture_session_id="session-1",
            start_epoch_ms=1_000,
            end_epoch_ms=2_000,
            data_root=data_root,
        )

    video = data_root / "runs" / str(run["id"]) / "video-request-1.mp4"
    await kovaak_run_store.begin_run_video_attach(
        run["id"], "u1",
        pending_video_path=video,
        request_digest="a" * 64,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    receipt_path, receipt = _write_capture_video_bundle(video, run_id=run["id"])
    video.write_bytes(b"tampered-after-receipt")
    with pytest.raises(ValueError, match="fingerprint"):
        await kovaak_run_store.attach_run_video(
            run["id"], "u1", video,
            expected_pending_video_path=video,
            expected_request_digest="a" * 64,
            data_root=data_root,
        )
    pending = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    assert pending["video_state"] == "pending"

    video.write_bytes(b"run-owned-mp4")
    receipt["requestDigest"] = "d" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="request digest"):
        await kovaak_run_store.attach_run_video(
            run["id"], "u1", video,
            expected_pending_video_path=video,
            expected_request_digest="a" * 64,
            data_root=data_root,
        )


@pytest.mark.asyncio
async def test_reconcile_run_videos_attaches_retries_and_quarantines_without_sources(
    tmp_path: Path,
):
    data_root = tmp_path / "data"
    stats = tmp_path / "user Stats.csv"
    performance = tmp_path / "user Performance.perf"
    stats.write_bytes(b"user-stats")
    performance.write_bytes(b"user-performance")

    attached_run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="recover-video",
        stats_path=str(stats), performance_path=str(performance),
    )
    attached_video = (
        data_root / "runs" / str(attached_run["id"]) / "video-request-1.mp4"
    )
    await kovaak_run_store.begin_run_video_attach(
        attached_run["id"], "u1",
        pending_video_path=attached_video,
        request_digest="a" * 64,
        capture_session_id="session-1",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    _write_capture_video_bundle(attached_video, run_id=attached_run["id"])

    missing_run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="missing-video",
    )
    missing_video = (
        data_root / "runs" / str(missing_run["id"]) / "video-request-2.mp4"
    )
    await kovaak_run_store.begin_run_video_attach(
        missing_run["id"], "u1",
        pending_video_path=missing_video,
        request_digest="b" * 64,
        capture_session_id="session-2",
        start_epoch_ms=2_000,
        end_epoch_ms=3_000,
        data_root=data_root,
    )

    conflict_run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="conflict-video",
    )
    conflict_video = (
        data_root / "runs" / str(conflict_run["id"]) / "video-request-3.mp4"
    )
    await kovaak_run_store.begin_run_video_attach(
        conflict_run["id"], "u1",
        pending_video_path=conflict_video,
        request_digest="c" * 64,
        capture_session_id="session-3",
        start_epoch_ms=3_000,
        end_epoch_ms=4_000,
        data_root=data_root,
    )
    conflict_receipt, receipt = _write_capture_video_bundle(
        conflict_video,
        run_id=conflict_run["id"],
        request_id="request-3",
        request_digest="c" * 64,
        capture_session_id="session-3",
        start_epoch_ms=3_000,
        end_epoch_ms=4_000,
    )
    receipt["runId"] = 999
    conflict_receipt.write_text(json.dumps(receipt), encoding="utf-8")
    partial = conflict_video.with_name(
        f".{conflict_video.name}.partial-123-1"
    )
    partial.write_bytes(b"app-created-partial")

    outcome = await kovaak_run_store.reconcile_run_videos(data_root)

    assert outcome == {
        "attached": 1,
        "retryable": 1,
        "unavailable": 1,
        "quarantined": 3,
    }
    recovered = await kovaak_run_store.get_kovaak_run(attached_run["id"], "u1")
    assert recovered["video_state"] == "attached"
    retryable = await kovaak_run_store.get_kovaak_run(missing_run["id"], "u1")
    assert retryable["video_state"] == "pending"
    assert retryable["video_error"] == "video_waiting_artifact"
    conflicted = await kovaak_run_store.get_kovaak_run(conflict_run["id"], "u1")
    assert conflicted["video_state"] == "unavailable"
    assert conflicted["video_error"] == "video_receipt_invalid"
    assert not conflict_video.exists()
    assert not conflict_receipt.exists()
    assert not partial.exists()
    assert len(list((data_root / "runs" / "orphans").iterdir())) == 3
    assert stats.read_bytes() == b"user-stats"
    assert performance.read_bytes() == b"user-performance"


@pytest.mark.asyncio
async def test_run_readiness_exposes_the_best_available_analysis_tier(
    tmp_path: Path,
):
    raw_run, _, _, _ = await _complete_multimodal_run(
        tmp_path,
        user_id="u1",
        source_key="raw-incomplete",
    )
    await kovaak_run_store.mark_run_video_unavailable(
        raw_run["id"], "u1", "video_hardware_unavailable",
    )
    raw_readiness = kovaak_run_store.derive_run_readiness(
        await kovaak_run_store.get_kovaak_run(raw_run["id"], "u1")
    )
    assert raw_readiness == {
        "ready": True,
        "state": "pending_analysis",
        "input_native": True,
        "video_fallback": False,
    }

    incomplete = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1", source_key="incomplete",
    )
    assert kovaak_run_store.derive_run_readiness(incomplete) == {
        "ready": False,
        "state": "incomplete_evidence",
        "input_native": False,
        "video_fallback": False,
    }
    from webapp.backend import file_store
    assert file_store.list_dir("sessions") == []


@pytest.mark.asyncio
async def test_run_owned_video_snapshot_and_public_dto_are_path_free(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    stats = tmp_path / "Stats.csv"
    performance = tmp_path / "Performance.perf"
    trace = data_root / "runs" / "seed" / "trace.bin"
    stats.write_bytes(b"stats")
    performance.write_bytes(b"performance")
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id="u1",
        source_key="public-video-run",
        scenario="Scenario",
        stats_path=str(stats),
        performance_path=str(performance),
        mouse_trace_path=str(trace),
        stats_summary={
            "source": kovaak_run_store._source_metadata(
                stats, kovaak_run_store.STATS_PARSER_VERSION,
            ),
        },
        performance_summary={
            "source": kovaak_run_store._source_metadata(
                performance, kovaak_run_store.PERFORMANCE_PARSER_VERSION,
            ),
        },
    )
    video = data_root / "runs" / str(run["id"]) / "video-request-public.mp4"
    await kovaak_run_store.begin_run_video_attach(
        run["id"], "u1",
        pending_video_path=video,
        request_digest="e" * 64,
        capture_session_id="session-public",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        alignment_summary={
            "duration_ms": 1_000,
            "coverage": 0.75,
            "start_source": "performance_challenge_start_utc",
            "end_source": "timer_profile",
            "timebase_version": "time_alignment.v2",
            "warnings": [],
        },
        data_root=data_root,
    )
    _write_capture_video_bundle(
        video,
        run_id=run["id"],
        request_id="request-public",
        request_digest="e" * 64,
        capture_session_id="session-public",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
    )
    await kovaak_run_store.attach_run_video(
        run["id"], "u1", video,
        expected_pending_video_path=video,
        expected_request_digest="e" * 64,
        data_root=data_root,
    )
    run = await kovaak_run_store.set_run_finalization_state(
        run["id"], "u1", "finalized",
    )

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")
    assert snapshot["sources"]["video"] == {
        "artifact_ref": f"run:{run['id']}:video:{hashlib.sha256(video.read_bytes()).hexdigest()[:16]}",
        "basename": video.name,
        "fingerprint": {
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "size": video.stat().st_size,
        },
        "path": str(video.resolve()),
        "availability": "available",
        "format_version": "mp4",
        "ownership": "run",
    }
    public_snapshot = kovaak_run_store.public_analysis_input_snapshot(snapshot)
    assert "path" not in public_snapshot["sources"]["video"]

    public = kovaak_run_store.public_kovaak_run(run)
    assert public["readiness_state"] == "pending_analysis"
    assert public["supported_input_modes"] == ["multimodal", "input_native", "video_fallback"]
    assert public["evidence_availability"] == {
        "stats": "available",
        "performance": "available",
        "raw": "available",
        "video": "available",
        "canonical_window": "available",
    }
    assert public["video_artifact_ref"] == snapshot["sources"]["video"]["artifact_ref"]
    assert public["alignment"] == {
        "state": "resolved",
        "duration_ms": 1_000,
        "coverage": 0.75,
        "start_source": "performance_challenge_start_utc",
        "end_source": "timer_profile",
        "timebase_version": "time_alignment.v2",
        "warnings": [],
    }
    assert public["trace_quality"]["alignment_status"] == "aligned"
    assert public["trace_quality"]["coverage"] == 0.75
    assert public["video_quality"]["coverage"] == {
        "packet_count": 60,
        "visible_duration_ms": 1_000.0,
    }
    assert public["limitations"] == []
    serialized = json.dumps(public, ensure_ascii=False)
    for private in (
        str(tmp_path),
        "captureSessionId",
        "capture_session_id",
        "requestDigest",
        "video_receipt",
        "window_start_epoch_ms",
    ):
        assert private not in serialized

    summaries = await kovaak_run_store.list_kovaak_run_summaries("u1")
    assert summaries[0]["readiness_state"] == "pending_analysis"
    assert summaries[0]["supported_input_modes"] == public["supported_input_modes"]
    assert str(tmp_path) not in json.dumps(summaries, ensure_ascii=False)


async def _seed_removable_run(
    tmp_path: Path, *, user_id: str = "u1",
) -> tuple[Path, dict, Path, Path, Path, Path]:
    data_root = tmp_path / "data"
    stats = tmp_path / "user Stats.csv"
    performance = tmp_path / "user Performance.perf"
    stats.write_bytes(b"user-stats-source")
    performance.write_bytes(b"user-performance-source")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=f"removable-{user_id}",
        stats_path=str(stats),
        performance_path=str(performance),
    )
    trace = data_root / "runs" / str(run["id"]) / "trace-removal.bin"
    kovaak_run_store.write_mouse_snapshot(trace, [
        {"timestamp_ms": 1_000, "dx": 1, "dy": 2, "buttons": 0},
    ])
    await kovaak_run_store.begin_mouse_trace_attach(run["id"], user_id, trace)
    await kovaak_run_store.attach_mouse_trace(
        run["id"], user_id, str(trace), expected_pending_trace_path=trace,
    )
    video = data_root / "runs" / str(run["id"]) / "video-remove.mp4"
    await kovaak_run_store.begin_run_video_attach(
        run["id"], user_id,
        pending_video_path=video,
        request_digest="f" * 64,
        capture_session_id="session-remove",
        start_epoch_ms=1_000,
        end_epoch_ms=2_000,
        data_root=data_root,
    )
    receipt, _ = _write_capture_video_bundle(
        video,
        run_id=run["id"],
        request_id="remove",
        request_digest="f" * 64,
        capture_session_id="session-remove",
    )
    run = await kovaak_run_store.attach_run_video(
        run["id"], user_id, video,
        expected_pending_video_path=video,
        expected_request_digest="f" * 64,
        data_root=data_root,
    )
    return data_root, run, stats, performance, trace, receipt


@pytest.mark.asyncio
async def test_storage_usage_classifies_run_video_raw_and_recovery_without_sources(
    tmp_path: Path,
) -> None:
    data_root, run, stats, performance, trace, receipt = await _seed_removable_run(
        tmp_path,
    )
    video = Path(run["video_path"])
    recovery = data_root / "runs" / str(run["id"]) / ".video-partial.recovery"
    recovery.write_bytes(b"recovery")

    usage = await kovaak_run_store.run_storage_usage("u1", data_root)

    assert usage == {
        "run_video_bytes": video.stat().st_size + receipt.stat().st_size,
        "run_raw_bytes": trace.stat().st_size,
        "incomplete_recovery_bytes": recovery.stat().st_size,
    }
    assert stats.read_bytes() == b"user-stats-source"
    assert performance.read_bytes() == b"user-performance-source"


@pytest.mark.asyncio
async def test_remove_run_video_is_contained_owner_scoped_and_idempotent(
    tmp_path: Path,
) -> None:
    data_root, run, stats, performance, trace, receipt = await _seed_removable_run(
        tmp_path,
    )
    video = Path(run["video_path"])
    video_bytes = video.stat().st_size + receipt.stat().st_size

    with pytest.raises(PermissionError):
        await kovaak_run_store.remove_run_evidence(
            run["id"], "other-owner", "video", data_root,
        )
    with pytest.raises(ValueError, match="evidence kind"):
        await kovaak_run_store.remove_run_evidence(
            run["id"], "u1", "stats", data_root,
        )

    removed = await kovaak_run_store.remove_run_evidence(
        run["id"], "u1", "video", data_root,
    )
    repeated = await kovaak_run_store.remove_run_evidence(
        run["id"], "u1", "video", data_root,
    )

    assert removed["removal_state"] == "completed"
    assert removed["reclaimed_bytes"] == video_bytes
    assert removed["affected_modes"] == ["multimodal", "video_fallback"]
    assert repeated["removal_state"] == "already_unavailable"
    assert repeated["reclaimed_bytes"] == 0
    persisted = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    assert persisted["video_state"] == "unavailable"
    assert persisted["video_error"] == "removed_by_user"
    assert persisted["trace_state"] == "attached"
    assert trace.is_file()
    assert not video.exists()
    assert not receipt.exists()
    assert stats.is_file() and performance.is_file()
    assert str(tmp_path) not in json.dumps(removed, ensure_ascii=False)


@pytest.mark.asyncio
async def test_remove_failure_keeps_tombstone_and_reconciliation_retries_exact_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root, run, stats, performance, trace, receipt = await _seed_removable_run(
        tmp_path,
    )
    video = Path(run["video_path"])
    original_unlink = kovaak_run_store._unlink_run_evidence_artifact

    def fail_unlink(_path: Path) -> int:
        raise OSError("injected unlink failure")

    monkeypatch.setattr(kovaak_run_store, "_unlink_run_evidence_artifact", fail_unlink)
    removed = await kovaak_run_store.remove_run_evidence(
        run["id"], "u1", "raw", data_root,
    )

    assert removed["removal_state"] == "pending_cleanup"
    assert removed["reclaimed_bytes"] == 0
    persisted = await kovaak_run_store.get_kovaak_run(run["id"], "u1")
    assert persisted["trace_state"] == "unavailable"
    assert persisted["trace_error"] == "removed_by_user"
    assert trace.is_file()
    from webapp.backend import file_store
    tombstones = file_store.read_json("runs/_evidence_tombstones.json") or []
    tombstone = next(
        (t for t in tombstones if t.get("run_id") == run["id"] and t.get("evidence_kind") == "raw"),
        None,
    )
    assert tombstone is not None
    assert tombstone["cleanup_state"] == "failed"
    assert tombstone["cleanup_attempts"] == 1
    assert tombstone["last_error_code"] == "artifact_cleanup_failed"

    trace_reconciliation = await kovaak_run_store.reconcile_mouse_traces(data_root)
    assert trace_reconciliation["quarantined"] == 0
    assert trace.is_file()

    monkeypatch.setattr(
        kovaak_run_store, "_unlink_run_evidence_artifact", original_unlink,
    )
    outcome = await kovaak_run_store.reconcile_run_evidence_deletions(data_root)

    assert outcome == {"completed": 1, "failed": 0}
    assert not trace.exists()
    assert video.is_file() and receipt.is_file()
    assert stats.is_file() and performance.is_file()
    remaining = file_store.read_json("runs/_evidence_tombstones.json") or []
    assert all(t.get("run_id") != run["id"] for t in remaining)
