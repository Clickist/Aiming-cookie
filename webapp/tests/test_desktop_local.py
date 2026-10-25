from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, file_store, queue
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
