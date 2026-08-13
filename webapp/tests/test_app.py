from __future__ import annotations

import logging

import pytest

import webapp.backend.app as app_module
from webapp.backend import file_store, queue
from webapp.backend.workspace import session_dir

_TOMBSTONES_PATH = "sessions/_deletion_tombstones.json"


async def _insert_tombstone(session_id: int, owner_id: str) -> None:
    tombstones = file_store.read_json(_TOMBSTONES_PATH) or []
    tombstones.append({"analysis_session_id": session_id, "owner_id": owner_id})
    file_store.write_json(_TOMBSTONES_PATH, tombstones)


@pytest.mark.asyncio
async def test_lifespan_reconciles_after_schema_before_api_ready(monkeypatch):
    events: list[str] = []

    async def fake_reconcile_analysis_deletions() -> dict[str, int]:
        events.append("deletions")
        return {"processed": 0, "cleaned": 0, "failed": 0}

    async def fake_reconcile_stale_uploads() -> dict[str, int]:
        events.append("uploads")
        return {"processed": 0, "cleaned": 0, "failed": 0}

    monkeypatch.setattr(
        queue,
        "reconcile_analysis_deletions",
        fake_reconcile_analysis_deletions,
    )
    monkeypatch.setattr(
        queue,
        "reconcile_stale_uploads",
        fake_reconcile_stale_uploads,
    )

    async with app_module.lifespan(app_module.app):
        events.append("ready")

    assert events == ["deletions", "uploads", "ready"]


@pytest.mark.asyncio
async def test_lifespan_reconciles_pending_workspace_before_api_ready(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(app_module.config, "DATA_ROOT", tmp_path / "managed")
    session_id = 901001
    workspace = session_dir(session_id)
    workspace.mkdir(parents=True)
    (workspace / "pending.bin").write_bytes(b"pending")
    await _insert_tombstone(session_id, "startup-owner")

    async with app_module.lifespan(app_module.app):
        assert not workspace.exists()
        tombstones = file_store.read_json(_TOMBSTONES_PATH) or []
        assert all(
            entry.get("analysis_session_id") != session_id
            for entry in tombstones
        )


@pytest.mark.asyncio
async def test_lifespan_cleanup_failure_is_observable_safe_and_does_not_block_ready(
    monkeypatch,
    tmp_path,
    caplog,
):
    monkeypatch.setattr(app_module.config, "DATA_ROOT", tmp_path / "managed")
    session_id = 901002
    workspace = session_dir(session_id)
    workspace.mkdir(parents=True)
    (workspace / "windows-locked.bin").write_bytes(b"locked")
    await _insert_tombstone(session_id, "startup-owner")

    def fail_cleanup(actual_session_id: int) -> bool:
        assert actual_session_id == session_id
        raise OSError(f"locked private path {workspace}")

    monkeypatch.setattr(queue, "remove_session_workspace", fail_cleanup)

    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        async with app_module.lifespan(app_module.app):
            startup_reached_ready = True

    assert startup_reached_ready is True
    tombstones = file_store.read_json(_TOMBSTONES_PATH) or []
    assert any(
        entry.get("analysis_session_id") == session_id
        for entry in tombstones
    )

    logs = caplog.text
    assert any(
        marker in logs
        for marker in ("processed=1", "'processed': 1", '"processed": 1')
    )
    assert any(
        marker in logs
        for marker in ("failed=1", "'failed': 1", '"failed": 1')
    )
    assert "workspace_cleanup_failed" in logs
    assert str(workspace) not in logs
    assert "locked private path" not in logs
