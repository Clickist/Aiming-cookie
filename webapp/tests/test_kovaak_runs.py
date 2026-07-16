import struct
import shutil
import hashlib
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
    conn = await kovaak_run_store.get_conn()

    class GuardedConnection:
        async def execute(self, sql: str, params=()):
            normalized = " ".join(sql.lower().split())
            if normalized.startswith("select ") and " from kovaak_runs" in normalized:
                selected = normalized.split(" from kovaak_runs", 1)[0].replace(",", " ").split()
                assert "stats_summary" not in selected, sql
                assert "performance_summary" not in selected, sql
            return await conn.execute(sql, params)

    async def guarded_get_conn():
        return GuardedConnection()

    def reject_content_hashing(*_args, **_kwargs):
        raise AssertionError("Run list must not hash source contents")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with monkeypatch.context() as guarded:
            guarded.setattr(kovaak_run_store, "get_conn", guarded_get_conn)
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
async def test_run_analysis_endpoint_requires_idempotency_key(
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
        KovaaKFileDiscovery(stem="endpoint-key-required", stats_path=stats),
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

    assert response.status_code == 400
    assert "idempotency_key" in response.json()["detail"]
    assert await queue.get_active_session(config.DESKTOP_LOCAL_PROFILE) is None


@pytest.mark.asyncio
async def test_run_analysis_endpoint_freezes_owned_snapshot_once_for_same_idempotency_key(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import coach_commands, config, db, queue
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

    calls = 0
    original_create = coach_commands.create_analysis_from_run

    async def counted_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", counted_create)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {
            "X-Aiming-Cookie-Desktop-Token": "run-token",
            "Idempotency-Key": "analyze-endpoint-run",
        }
        response = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "video_fallback", "video_path": str(video)},
        )
        replay = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "video_fallback", "video_path": str(video)},
        )
    assert response.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert calls == 1
    session = await queue.get_session(response.json()["session_id"])
    assert session["input_mode"] == "video_fallback"
    assert session["kovaak_run_id"] == run["id"]
    assert session["input_snapshot"]["scenario_identity_version"] == "kovaak_scenario.v1"
    assert session["input_snapshot"]["sources"]["stats"]["artifact_ref"].startswith("run:")
    assert session["input_snapshot"]["sources"]["video"]["fingerprint"] == {
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "size": video.stat().st_size,
        "mtime_ns": video.stat().st_mtime_ns,
    }
    conn = await db.get_conn()
    cur = await conn.execute(
        "SELECT safe_parameters_summary_json, result_json "
        "FROM coach_product_commands WHERE command_name='analysis.create_from_run'",
    )
    audit_rows = await cur.fetchall()
    assert len(audit_rows) == 2
    assert all(str(video) not in row["safe_parameters_summary_json"] for row in audit_rows)
    assert all(str(video) not in row["result_json"] for row in audit_rows)


@pytest.mark.asyncio
async def test_run_analysis_endpoint_rejects_reused_key_for_different_video(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="endpoint-conflict", stats_path=stats),
        user_id=config.DESKTOP_LOCAL_PROFILE,
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
            json={"input_mode": "video_fallback", "video_path": str(first_video)},
        )
        conflict = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "video_fallback", "video_path": str(second_video)},
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "idempotency" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_run_analysis_endpoint_rejects_reused_key_after_same_video_path_changes(
    monkeypatch, tmp_path: Path,
):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "run-token")
    stats = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="endpoint-same-path-conflict", stats_path=stats),
        user_id=config.DESKTOP_LOCAL_PROFILE,
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
            json={"input_mode": "video_fallback", "video_path": str(video)},
        )
        video.write_bytes(b"second-video-revision")
        conflict = await client.post(
            f"/api/kovaak-runs/{run['id']}/analyze",
            headers=headers,
            json={"input_mode": "video_fallback", "video_path": str(video)},
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "idempotency" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_video_fallback_rejects_stats_replaced_after_snapshot_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import coach_commands, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    source_fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "source Stats.csv"
    shutil.copyfile(source_fixture, stats)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="copy-race-run", stats_path=stats),
        user_id="owner-copy-race",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    original_copy = coach_commands.copy_path_to_path

    def replace_stats_then_copy(source: Path, destination: Path):
        if source.resolve() == stats.resolve():
            source.write_bytes(source.read_bytes() + b"\nsource replaced after snapshot")
        return original_copy(source, destination)

    monkeypatch.setattr(coach_commands, "copy_path_to_path", replace_stats_then_copy)

    with pytest.raises(coach_commands.ProductCommandError) as exc_info:
        await coach_commands.create_analysis_from_run(
            "owner-copy-race",
            run["id"],
            input_mode="video_fallback",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert await queue.get_active_session("owner-copy-race") is None


@pytest.mark.asyncio
async def test_video_fallback_rejects_stats_mtime_changed_after_snapshot_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import coach_commands, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    source_fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "source Stats.csv"
    shutil.copyfile(source_fixture, stats)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="copy-mtime-race-run", stats_path=stats),
        user_id="owner-copy-mtime-race",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    original_copy = coach_commands.copy_path_to_path

    def touch_stats_then_copy(source: Path, destination: Path):
        if source.resolve() == stats.resolve():
            stat = source.stat()
            os.utime(
                source,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
            )
        return original_copy(source, destination)

    monkeypatch.setattr(coach_commands, "copy_path_to_path", touch_stats_then_copy)

    with pytest.raises(coach_commands.ProductCommandError) as exc_info:
        await coach_commands.create_analysis_from_run(
            "owner-copy-mtime-race",
            run["id"],
            input_mode="video_fallback",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert await queue.get_active_session("owner-copy-mtime-race") is None


@pytest.mark.asyncio
async def test_video_fallback_rejects_video_replaced_after_freeze_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import coach_commands, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    source_fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "source Stats.csv"
    shutil.copyfile(source_fixture, stats)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="video-copy-race-run", stats_path=stats),
        user_id="owner-video-copy-race",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"frozen-video-revision")
    original_copy = coach_commands.copy_path_to_path

    def replace_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            source.write_bytes(b"different-video-revision")
        return original_copy(source, destination)

    monkeypatch.setattr(coach_commands, "copy_path_to_path", replace_video_then_copy)

    with pytest.raises(coach_commands.ProductCommandError) as exc_info:
        await coach_commands.create_analysis_from_run(
            "owner-video-copy-race",
            run["id"],
            input_mode="video_fallback",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert str(video) not in exc_info.value.message
    assert await queue.get_active_session("owner-video-copy-race") is None


@pytest.mark.asyncio
async def test_video_fallback_rejects_video_mtime_changed_after_freeze_before_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import coach_commands, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    source_fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "source Stats.csv"
    shutil.copyfile(source_fixture, stats)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="video-copy-mtime-race-run", stats_path=stats),
        user_id="owner-video-copy-mtime-race",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stable-video-bytes")
    original_copy = coach_commands.copy_path_to_path

    def touch_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            stat = source.stat()
            os.utime(
                source,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
            )
        return original_copy(source, destination)

    monkeypatch.setattr(coach_commands, "copy_path_to_path", touch_video_then_copy)

    with pytest.raises(coach_commands.ProductCommandError) as exc_info:
        await coach_commands.create_analysis_from_run(
            "owner-video-copy-mtime-race",
            run["id"],
            input_mode="video_fallback",
            managed_video_source=video,
        )

    assert exc_info.value.code == "source_unavailable"
    assert str(video) not in exc_info.value.message
    assert await queue.get_active_session("owner-video-copy-mtime-race") is None


@pytest.mark.asyncio
async def test_video_fallback_reports_video_disappearance_during_managed_copy(
    monkeypatch, tmp_path: Path,
):
    from webapp.backend import coach_commands, config, queue

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    source_fixture = Path(
        "data/1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv"
    ).resolve()
    stats = tmp_path / "source Stats.csv"
    shutil.copyfile(source_fixture, stats)
    run = await kovaak_run_store.ingest_discovery(
        KovaaKFileDiscovery(stem="video-copy-disappears-run", stats_path=stats),
        user_id="owner-video-copy-disappears",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-before-copy")
    original_copy = coach_commands.copy_path_to_path

    def delete_video_then_copy(source: Path, destination: Path):
        if source.resolve() == video.resolve():
            source.unlink()
        return original_copy(source, destination)

    monkeypatch.setattr(coach_commands, "copy_path_to_path", delete_video_then_copy)

    with pytest.raises(coach_commands.ProductCommandError) as exc_info:
        await coach_commands.create_analysis_from_run(
            "owner-video-copy-disappears",
            run["id"],
            input_mode="video_fallback",
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

    snapshot = await kovaak_run_store.build_analysis_input_snapshot(run["id"], "u1")

    assert snapshot["trace"]["fingerprint"] == {
        "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "size": trace.stat().st_size,
        "mtime_ns": trace.stat().st_mtime_ns,
    }
    public = kovaak_run_store.public_analysis_input_snapshot(snapshot)
    assert public["trace"]["fingerprint"] == snapshot["trace"]["fingerprint"]
    assert "path" not in public["trace"]


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
        {"timestamp_ms": 200, "dx": -3, "dy": 4, "buttons": 1},
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
