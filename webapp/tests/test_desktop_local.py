from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, file_store, queue, routes
from webapp.backend.app import app
from webapp.backend.workspace import session_dir


def _desktop_headers() -> dict[str, str]:
    return {"X-Aiming-Cookie-Desktop-Token": "test-launch-token"}


@pytest.fixture
def desktop_token(monkeypatch):
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "test-launch-token")


def test_resolve_data_root_honors_explicit_override(monkeypatch, tmp_path):
    override = tmp_path / "explicit-data-root"
    monkeypatch.setenv("DATA_ROOT", str(override))
    assert config.resolve_data_root() == override.resolve()


def test_resolve_data_root_uses_windows_appdata_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(config.sys, "platform", "win32")
    assert config.resolve_data_root() == (tmp_path / "AppData" / "Aiming Cookie").resolve()


def test_resolve_data_root_uses_linux_xdg_data_home_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.resolve_data_root() == (tmp_path / "xdg-data" / "Aiming Cookie").resolve()


def test_desktop_local_profile_is_stable():
    assert config.DESKTOP_LOCAL_PROFILE == "desktop-local"


@pytest.mark.asyncio
async def test_desktop_runtime_protects_all_api_routes_but_not_health(desktop_token):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/healthz")
        preflight = await client.options(
            "/api/sessions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": (
                    "X-Aiming-Cookie-Desktop-Token,X-User-Id"
                ),
            },
        )
        missing = await client.get("/api/sessions")
        invalid = await client.get(
            "/api/sessions",
            headers={"X-Aiming-Cookie-Desktop-Token": "wrong-token"},
        )
        valid = await client.get("/api/sessions", headers=_desktop_headers())

    assert health.status_code == 200
    assert preflight.status_code == 200
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200


@pytest.mark.asyncio
async def test_storage_requires_valid_desktop_token(desktop_token):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/storage")
        invalid = await client.get(
            "/api/storage",
            headers={"X-Aiming-Cookie-Desktop-Token": "wrong-token"},
        )
        valid = await client.get("/api/storage", headers=_desktop_headers())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200


@pytest.mark.asyncio
async def test_desktop_path_import_copies_sources_to_managed_workspace(
    desktop_token, monkeypatch, tmp_path,
):
    managed_root = tmp_path / "managed"
    sources = tmp_path / "sources"
    sources.mkdir()
    video = sources / "clip.mp4"
    csv = sources / "stats.csv"
    video.write_bytes(b"original-video")
    csv.write_bytes(b"frame,time_s\n0,0\n")
    monkeypatch.setattr(config, "DATA_ROOT", managed_root)
    destinations = []
    original_copy = routes.copy_path_to_path

    def record_temp_destination(source, destination):
        destinations.append(destination)
        assert destination.name.endswith(".tmp")
        assert not destination.with_name(destination.name[:-4]).exists()
        return original_copy(source, destination)

    monkeypatch.setattr(routes, "copy_path_to_path", record_temp_destination)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={
                "video_path": str(video),
                "csv_path": str(csv),
                "cm_per_360": 51.0,
                "fov": 103.0,
            },
        )

    assert response.status_code == 200
    sid = response.json()["session_id"]
    workspace = session_dir(sid)
    assert (workspace / "video.mp4").read_bytes() == b"original-video"
    assert (workspace / "stats.csv").read_bytes() == b"frame,time_s\n0,0\n"
    assert video.read_bytes() == b"original-video"
    assert csv.read_bytes() == b"frame,time_s\n0,0\n"
    session = await queue.get_session(sid)
    assert session is not None
    assert session["user_id"] == config.DESKTOP_LOCAL_PROFILE
    assert session["status"] == "queued"
    assert destinations == [workspace / "video.mp4.tmp", workspace / "stats.csv.tmp"]
    assert list(workspace.glob("*.tmp")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("video_path", "csv_path"),
    [
        ("relative.mp4", "relative.csv"),
        ("/missing/video.mp4", "/missing/stats.csv"),
    ],
)
async def test_desktop_path_import_rejects_relative_or_missing_paths(
    desktop_token, video_path, csv_path,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": video_path, "csv_path": csv_path},
        )
    assert response.status_code == 400
    assert await queue.list_sessions(config.DESKTOP_LOCAL_PROFILE) == []


@pytest.mark.asyncio
async def test_desktop_path_import_rejects_unreadable_or_wrong_type_paths(
    desktop_token, monkeypatch, tmp_path,
):
    video = tmp_path / "clip.mp4"
    csv = tmp_path / "stats.csv"
    video.write_bytes(b"video")
    csv.write_bytes(b"csv")
    directory = tmp_path / "not-a-file.csv"
    directory.mkdir()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        wrong_type = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": str(video), "csv_path": str(directory)},
        )
        monkeypatch.setattr(routes.os, "access", lambda path, mode: False)
        unreadable = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": str(video), "csv_path": str(csv)},
        )

    assert wrong_type.status_code == 400
    assert unreadable.status_code == 400
    assert await queue.list_sessions(config.DESKTOP_LOCAL_PROFILE) == []


@pytest.mark.asyncio
async def test_desktop_path_import_does_not_apply_multipart_size_caps(
    desktop_token, monkeypatch, tmp_path,
):
    video = tmp_path / "clip.mp4"
    csv = tmp_path / "stats.csv"
    video.write_bytes(b"video")
    csv.write_bytes(b"csv")
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    monkeypatch.setattr(config, "MAX_VIDEO_BYTES", 1)
    monkeypatch.setattr(config, "MAX_CSV_BYTES", 1)
    monkeypatch.setattr(
        routes.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=config.MIN_FREE_DISK_BYTES + 1024),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": str(video), "csv_path": str(csv)},
        )

    assert response.status_code == 200
    session = await queue.get_session(response.json()["session_id"])
    assert session is not None
    assert session["status"] == "queued"


@pytest.mark.asyncio
async def test_desktop_path_import_rejects_insufficient_disk_without_creating_session(
    desktop_token, monkeypatch, tmp_path,
):
    video = tmp_path / "clip.mp4"
    csv = tmp_path / "stats.csv"
    video.write_bytes(b"video")
    csv.write_bytes(b"csv")
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    monkeypatch.setattr(config, "MIN_FREE_DISK_BYTES", 100)
    monkeypatch.setattr(
        routes.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=100),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": str(video), "csv_path": str(csv)},
        )

    assert response.status_code == 507
    assert await queue.list_sessions(config.DESKTOP_LOCAL_PROFILE) == []
    assert video.read_bytes() == b"video"
    assert csv.read_bytes() == b"csv"


@pytest.mark.asyncio
async def test_desktop_path_import_cleans_incomplete_workspace_after_copy_error(
    desktop_token, monkeypatch, tmp_path,
):
    managed_root = tmp_path / "managed"
    video = tmp_path / "clip.mp4"
    csv = tmp_path / "stats.csv"
    video.write_bytes(b"video")
    csv.write_bytes(b"csv")
    monkeypatch.setattr(config, "DATA_ROOT", managed_root)

    calls = 0

    def interrupted_copy(source, destination):
        nonlocal calls
        calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        if calls == 2:
            raise OSError("copy interrupted")
        return len(b"partial")

    monkeypatch.setattr(routes, "copy_path_to_path", interrupted_copy)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/desktop/analyze-paths",
            headers=_desktop_headers(),
            json={"video_path": str(video), "csv_path": str(csv)},
        )

    assert response.status_code == 500
    sessions_root = managed_root / "sessions"
    assert not sessions_root.exists() or [
        p for p in sessions_root.iterdir() if p.name != "_counter.json"
    ] == []
    assert await queue.list_sessions(config.DESKTOP_LOCAL_PROFILE) == []
    assert video.read_bytes() == b"video"
    assert csv.read_bytes() == b"csv"


@pytest.mark.asyncio
async def test_multipart_analyze_remains_available_without_desktop_runtime_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/analyze",
            headers={"X-User-Id": "multipart-user"},
            files={
                "video": ("clip.mp4", b"video", "video/mp4"),
                "csv": ("stats.csv", b"frame,time_s\n0,0\n", "text/csv"),
            },
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_storage_lists_managed_workspace_bytes_for_desktop_profile(
    desktop_token, monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    done_id = await queue.enqueue(config.DESKTOP_LOCAL_PROFILE, "", "")
    failed_id = await queue.enqueue(config.DESKTOP_LOCAL_PROFILE, "", "")
    other_id = await queue.enqueue("other-user", "", "")
    for sid, status in ((done_id, "done"), (failed_id, "failed")):
        session = file_store.read_json(f"sessions/{sid}.json")
        assert isinstance(session, dict)
        session["status"] = status
        file_store.write_json(f"sessions/{sid}.json", session)
    done_workspace = session_dir(done_id)
    done_workspace.mkdir(parents=True)
    (done_workspace / "video.mp4").write_bytes(b"1234")
    failed_workspace = session_dir(failed_id)
    failed_workspace.mkdir(parents=True)
    (failed_workspace / "report.json").write_bytes(b"123456")
    other_workspace = session_dir(other_id)
    other_workspace.mkdir(parents=True)
    (other_workspace / "private.bin").write_bytes(b"x" * 99)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/storage", headers=_desktop_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_bytes"] == 10
    assert [item["session_id"] for item in body["sessions"]] == [failed_id, done_id]
    assert [item["workspace_bytes"] for item in body["sessions"]] == [6, 4]
    assert all(item["created_at"].endswith("Z") for item in body["sessions"])


@pytest.mark.asyncio
async def test_delete_rejects_uploading_and_cleans_workspace_after_logical_delete(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    uploading = await queue.enqueue("u-delete", "", "", status="uploading")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u-delete"},
    ) as client:
        active = await client.delete(f"/api/sessions/{uploading}")
    assert active.status_code == 409
    assert await queue.get_session(uploading) is not None

    done = await queue.enqueue("u-delete", "", "")
    session = file_store.read_json(f"sessions/{done}.json")
    assert isinstance(session, dict)
    session["status"] = "done"
    file_store.write_json(f"sessions/{done}.json", session)
    workspace = session_dir(done)
    workspace.mkdir(parents=True)
    (workspace / "video.mp4").write_bytes(b"managed-copy")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u-delete"},
    ) as client:
        deleted = await client.delete(f"/api/sessions/{done}")

    assert deleted.status_code == 200
    assert deleted.json() == {
        "deleted": True,
        "id": done,
        "files_removed": ["workspace"],
        "cleanup_failed": [],
    }
    assert not workspace.exists()
    assert await queue.get_session(done) is None


@pytest.mark.asyncio
async def test_delete_cleanup_failure_keeps_logical_delete_and_tombstone(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")
    sid = await queue.enqueue("u-cleanup", "", "")
    await queue.claim_next("test-worker:desktop-local")
    await queue.mark_done(
        sid,
        {"schema_version": "analysis_result.v1"},
        0.0,
        worker_id="test-worker:desktop-local",
    )
    workspace = session_dir(sid)
    workspace.mkdir(parents=True)
    (workspace / "video.mp4").write_bytes(b"managed-copy")
    remaining = workspace / "windows-locked.bin"
    remaining.write_bytes(b"locked")

    def fail_cleanup(session_id):
        assert session_id == sid
        (workspace / "video.mp4").unlink()
        raise OSError(f"busy private path {workspace}")

    monkeypatch.setattr(queue, "remove_session_workspace", fail_cleanup)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u-cleanup"},
    ) as client:
        deleted = await client.delete(f"/api/sessions/{sid}")

    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert body["id"] == sid
    assert body["cleanup_failed"] == ["workspace"]
    assert str(workspace) not in deleted.text
    assert "busy private path" not in deleted.text
    assert await queue.get_session(sid) is None
    tombstones = file_store.read_json("sessions/_deletion_tombstones.json") or []
    assert any(entry.get("analysis_session_id") == sid for entry in tombstones)
    assert workspace.exists()
    assert remaining.read_bytes() == b"locked"
