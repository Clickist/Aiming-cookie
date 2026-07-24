from __future__ import annotations

import json
import logging

import pytest

import webapp.backend.app as app_module
from webapp.backend import db, queue
from webapp.backend.workspace import session_dir


async def _insert_tombstone(session_id: int, owner_id: str) -> None:
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO analysis_deletion_tombstones(analysis_session_id, owner_id) "
        "VALUES(?, ?)",
        (session_id, owner_id),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_lifespan_reconciles_after_schema_before_api_ready(monkeypatch):
    events: list[str] = []

    async def fake_init_schema() -> None:
        events.append("schema")

    async def fake_reconcile_analysis_deletions() -> dict[str, int]:
        events.append("deletions")
        return {"processed": 0, "cleaned": 0, "failed": 0}

    async def fake_reconcile_stale_uploads() -> dict[str, int]:
        events.append("uploads")
        return {"processed": 0, "cleaned": 0, "failed": 0}

    monkeypatch.setattr(app_module, "init_schema", fake_init_schema)
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

    assert events == ["schema", "deletions", "uploads", "ready"]


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
        conn = await db.get_conn()
        tombstone = await (
            await conn.execute(
                "SELECT 1 FROM analysis_deletion_tombstones "
                "WHERE analysis_session_id=?",
                (session_id,),
            )
        ).fetchone()
        assert tombstone is None


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
    conn = await db.get_conn()
    tombstone = await (
        await conn.execute(
            "SELECT owner_id, cleanup_state, cleanup_attempts, last_error_code "
            "FROM analysis_deletion_tombstones WHERE analysis_session_id=?",
            (session_id,),
        )
    ).fetchone()
    persisted = dict(tombstone)
    assert persisted == {
        "owner_id": "startup-owner",
        "cleanup_state": "failed",
        "cleanup_attempts": 1,
        "last_error_code": "workspace_cleanup_failed",
    }

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

    observable = logs + json.dumps(persisted, ensure_ascii=False)
    assert str(workspace) not in observable
    assert "locked private path" not in observable
    assert "OSError" not in observable
    assert "Traceback" not in observable


@pytest.mark.asyncio
async def test_lifespan_unexpected_reconciliation_db_failure_blocks_ready(
    monkeypatch,
    tmp_path,
    caplog,
):
    monkeypatch.setattr(app_module.config, "DATA_ROOT", tmp_path / "managed")
    session_id = 901003
    await _insert_tombstone(session_id, "startup-db-failure")
    ready = False

    async def fail_finalize(_session_id: int) -> None:
        raise RuntimeError("unexpected tombstone database failure")

    monkeypatch.setattr(queue, "_finalize_analysis_cleanup", fail_finalize)

    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        with pytest.raises(RuntimeError, match="tombstone database failure"):
            async with app_module.lifespan(app_module.app):
                ready = True

    assert ready is False
    assert "workspace_cleanup_failed" not in caplog.text
