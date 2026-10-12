"""analysis_artifacts_cleaner 行为测试（[ac P0-5 2026-10-11] 三层清理的层 2/3）。

锁死：默认 10GB 与设置回环、LRU（最旧先删）选择、活动工作区与 Run-owned
证据绝不触碰、孤立工作区参与最旧优先、关闭时零副作用、触发留痕（快照 +
日志）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webapp.backend import analysis_artifacts_cleaner as cleaner
from webapp.backend import config, file_store
from webapp.backend.workspace import session_dir, workspace_size_bytes


def _make_workspace(session_id: int, *, status: str, created_at: str, payload: bytes) -> int:
    """直接落一条 session 记录 + 工作区（绕开 enqueue 的活动约束）。"""
    file_store.write_json(f"sessions/{session_id}.json", {
        "id": session_id,
        "user_id": "u1",
        "status": status,
        "created_at": created_at,
    })
    directory = session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "video.mp4").write_bytes(payload)
    return workspace_size_bytes(session_id)


def _make_orphan(session_id: int, payload: bytes) -> int:
    directory = session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "stats.csv").write_bytes(payload)
    return workspace_size_bytes(session_id)


def test_default_settings_enabled_10gb_and_roundtrip() -> None:
    settings = cleaner.get_cleanup_settings()
    assert settings == {"enabled": True, "limit_bytes": cleaner.DEFAULT_LIMIT_BYTES}
    assert cleaner.DEFAULT_LIMIT_BYTES == 10 * 1024**3  # 点点拍板：默认 10GB

    saved = cleaner.save_cleanup_settings(enabled=False, limit_bytes=64 * 1024**3)
    assert saved == {"enabled": False, "limit_bytes": 64 * 1024**3}
    assert cleaner.get_cleanup_settings() == saved


def test_save_settings_rejects_out_of_range_limit() -> None:
    with pytest.raises(ValueError):
        cleaner.save_cleanup_settings(enabled=True, limit_bytes=1024)  # < 1GB
    with pytest.raises(ValueError):
        cleaner.save_cleanup_settings(enabled=True, limit_bytes=2 * 1024**4 * 1024**3)
    with pytest.raises(ValueError):
        cleaner.save_cleanup_settings(enabled="yes", limit_bytes=cleaner.DEFAULT_LIMIT_BYTES)  # type: ignore[arg-type]


def test_cap_deletes_oldest_first_until_under_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LRU 选择锁死：超限从最旧开始删，删到回落上限即停（不是清空）。"""
    import logging

    _make_workspace(11, status="done", created_at="2026-01-01T00:00:00Z", payload=b"a" * 1000)
    _make_workspace(12, status="done", created_at="2026-01-02T00:00:00Z", payload=b"b" * 1000)
    _make_workspace(13, status="done", created_at="2026-01-03T00:00:00Z", payload=b"c" * 1000)

    with caplog.at_level(logging.INFO, logger=cleaner.log.name):
        snapshot = cleaner.enforce_analysis_artifacts_cap(
            settings={"enabled": True, "limit_bytes": 2500},
        )

    assert snapshot["triggered"] is True
    removed = snapshot["removed"]
    assert [item["session_id"] for item in removed] == [11]  # 只删最旧
    assert removed[0]["bytes"] == 1000 and removed[0]["orphan"] is False
    assert snapshot["total_after_bytes"] == 2000
    assert not session_dir(11).exists()
    assert session_dir(12).is_dir() and session_dir(13).is_dir()
    # 层 3 留痕：快照落盘 + 每个删除一条日志（删了哪个、释放多少）。
    assert cleaner.get_last_run() == snapshot
    assert "analysis artifacts cap removed session=11" in caplog.text
    assert "1000" in caplog.text


def test_cap_never_touches_active_workspaces_or_run_evidence() -> None:
    """红线：活动分析工作区跳过；runs/ 下的录像 MP4 与 Raw trace 永不触碰。"""
    _make_workspace(21, status="running", created_at="2026-01-01T00:00:00Z", payload=b"x" * 5000)
    run_video = Path(config.DATA_ROOT) / "runs" / "7" / "video.mp4"
    run_raw = Path(config.DATA_ROOT) / "runs" / "7" / "trace.bin"
    run_video.parent.mkdir(parents=True, exist_ok=True)
    run_video.write_bytes(b"run-owned-mp4")
    run_raw.write_bytes(b"run-owned-raw")

    snapshot = cleaner.enforce_analysis_artifacts_cap(
        settings={"enabled": True, "limit_bytes": 2500},
    )

    # 总量超限但唯一工作区是活动态：零删除、明确记账 skipped_active。
    assert snapshot["removed"] == []
    assert snapshot["triggered"] is False
    assert snapshot["skipped_active"] == 1
    assert session_dir(21).is_dir()
    assert run_video.read_bytes() == b"run-owned-mp4"
    assert run_raw.read_bytes() == b"run-owned-raw"


def test_cap_includes_orphan_workspaces_oldest_first() -> None:
    """孤立工作区（记录已失）按目录 mtime 参与最旧优先，一并收敛。"""
    import os

    orphan_bytes = _make_orphan(31, payload=b"o" * 4000)
    _make_workspace(32, status="failed", created_at="2026-01-02T00:00:00Z", payload=b"n" * 1000)
    # 把孤立目录 mtime 钉到比 32 的 created_at 更旧：最旧优先必须先删它。
    old = 1767225600.0  # 2026-01-01T00:00:00Z
    os.utime(session_dir(31), (old, old))

    snapshot = cleaner.enforce_analysis_artifacts_cap(
        settings={"enabled": True, "limit_bytes": 2000},
    )

    # 4000 + 1000 超限：删最旧的孤立（4000）后 1000 ≤ 2000 即停，终态新件留下。
    removed_ids = [item["session_id"] for item in snapshot["removed"]]
    assert removed_ids == [31]
    removed = snapshot["removed"][0]
    assert removed["orphan"] is True and removed["bytes"] == orphan_bytes
    assert not session_dir(31).exists()
    assert session_dir(32).is_dir()
    assert snapshot["total_after_bytes"] == 1000


def test_cap_disabled_never_deletes_and_keeps_last_snapshot() -> None:
    """关闭 = 零副作用，且不覆写上次触发的留痕快照。"""
    _make_workspace(41, status="done", created_at="2026-01-01T00:00:00Z", payload=b"z" * 5000)
    first = cleaner.enforce_analysis_artifacts_cap(
        settings={"enabled": True, "limit_bytes": 1000},
    )
    assert first["triggered"] is True
    stale_snapshot = cleaner.get_last_run()

    # 关闭后新工作区再次超限：不得删除，快照保持上次触发的留痕。
    _make_workspace(42, status="done", created_at="2026-01-02T00:00:00Z", payload=b"y" * 5000)
    result = cleaner.enforce_analysis_artifacts_cap(
        settings={"enabled": False, "limit_bytes": 1000},
    )

    assert result == {"triggered": False, "reason": "disabled"}
    assert session_dir(42).is_dir()
    assert cleaner.get_last_run() == stale_snapshot


def test_settings_corruption_falls_back_to_defaults() -> None:
    """损坏配置回默认（enabled+10GB）：上限不因坏 JSON 失效。"""
    file_store.write_json("config/analysis-cleanup.json", {"enabled": "oops"})
    settings = cleaner.get_cleanup_settings()
    assert settings == {"enabled": True, "limit_bytes": cleaner.DEFAULT_LIMIT_BYTES}


# ---- 端点合同：/api/storage/analysis-cleanup GET/PUT ----


@pytest.mark.asyncio
async def test_analysis_cleanup_endpoints_roundtrip_and_enforce(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "cleanup-token")
    headers = {"X-Aiming-Cookie-Desktop-Token": "cleanup-token"}
    _make_workspace(51, status="done", created_at="2026-01-01T00:00:00Z", payload=b"w" * 5000)
    # 稀疏文件凑一个 2GiB 的较新工作区（PUT 校验下限 1GB，测试不必真写盘）。
    sparse_dir = session_dir(52)
    sparse_dir.mkdir(parents=True, exist_ok=True)
    file_store.write_json("sessions/52.json", {
        "id": 52, "user_id": "u1", "status": "done",
        "created_at": "2026-01-02T00:00:00Z",
    })
    with (sparse_dir / "video.mp4").open("wb") as handle:
        handle.seek(2 * 1024**3 - 1)
        handle.write(b"t")  # size 恰为 2GiB：删掉 51 后即回落到上限，52 留下。

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        got = await client.get("/api/storage/analysis-cleanup", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["schema_version"] == "analysis_cleanup_settings.v1"
        assert body["enabled"] is True
        assert body["limit_bytes"] == cleaner.DEFAULT_LIMIT_BYTES
        assert body["last_run"] is None

        put = await client.put(
            "/api/storage/analysis-cleanup",
            headers=headers,
            json={"enabled": True, "limit_bytes": 2 * 1024**3},
        )
        assert put.status_code == 200, put.text
        put_body = put.json()
        # PUT 即刻对账：超限工作区当场被清，last_run 带明细（明示留痕）。
        assert put_body["limit_bytes"] == 2 * 1024**3
        assert put_body["last_run"]["triggered"] is True
        assert [
            item["session_id"] for item in put_body["last_run"]["removed"]
        ] == [51]
        assert not session_dir(51).exists()

        bad = await client.put(
            "/api/storage/analysis-cleanup",
            headers=headers,
            json={"enabled": True, "limit_bytes": 1024},
        )
        assert bad.status_code == 422
