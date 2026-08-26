from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, file_store, kovaak_directory_store
from webapp.backend.app import app


def _desktop_headers() -> dict[str, str]:
    return {"X-Aiming-Cookie-Desktop-Token": "test-launch-token"}


@pytest.fixture
def desktop_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "test-launch-token")


def _clear_kovaak_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KOVAAK_INSTALL_DIR", "KOVAAK_STATS_DIR", "KOVAAK_PERFORMANCE_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_confirmed_directories_persist_atomically_and_require_distinct_readable_absolute_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    stats_dir = tmp_path / "stats"
    performance_dir = tmp_path / "performance"
    stats_dir.mkdir()
    performance_dir.mkdir()

    saved = kovaak_directory_store.save_confirmed_directories(
        str(stats_dir), str(performance_dir),
    )

    assert saved == (stats_dir.resolve(), performance_dir.resolve())
    assert file_store.read_json("config/kovaak-local-directories.json") == {
        "stats_dir": str(stats_dir.resolve()),
        "performance_dir": str(performance_dir.resolve()),
    }
    assert kovaak_directory_store.get_confirmed_directories() == saved
    with pytest.raises(ValueError, match="absolute"):
        kovaak_directory_store.save_confirmed_directories("relative", str(performance_dir))
    with pytest.raises(ValueError, match="different"):
        kovaak_directory_store.save_confirmed_directories(str(stats_dir), str(stats_dir))


def test_resolver_prioritizes_environment_then_confirmed_then_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _clear_kovaak_overrides(monkeypatch)
    stats_dir = tmp_path / "stats"
    performance_dir = tmp_path / "performance"
    env_stats_dir = tmp_path / "env-stats"
    for directory in (stats_dir, performance_dir, env_stats_dir):
        directory.mkdir()
    kovaak_directory_store.save_confirmed_directories(str(stats_dir), str(performance_dir))

    assert config.resolve_kovaak_data_dirs() == (stats_dir.resolve(), performance_dir.resolve())

    monkeypatch.setenv("KOVAAK_STATS_DIR", str(env_stats_dir))
    assert config.resolve_kovaak_data_dirs() == (env_stats_dir.resolve(), performance_dir.resolve())


def test_get_confirmed_directories_treats_corrupted_file_as_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_ROOT", data_root)
    corrupt_file = data_root / "config" / "kovaak-local-directories.json"
    corrupt_file.parent.mkdir(parents=True)
    corrupt_file.write_text("{ truncated by power loss", encoding="utf-8")

    assert kovaak_directory_store.get_confirmed_directories() is None


def test_resolver_falls_back_to_discovery_when_confirmed_file_is_corrupted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_ROOT", data_root)
    _clear_kovaak_overrides(monkeypatch)
    corrupt_file = data_root / "config" / "kovaak-local-directories.json"
    corrupt_file.parent.mkdir(parents=True)
    corrupt_file.write_text("{ truncated by power loss", encoding="utf-8")
    discovered_install = tmp_path / "discovered-install"
    monkeypatch.setattr(
        config, "resolve_kovaak_install_dir", lambda: discovered_install,
    )

    stats_dir, performance_dir = config.resolve_kovaak_data_dirs()

    assert (stats_dir, performance_dir) == (
        discovered_install / "FPSAimTrainer" / "stats",
        discovered_install / "FPSAimTrainer" / "performances",
    )


def test_directory_status_counts_only_supported_canonical_files(tmp_path: Path) -> None:
    stats_dir = tmp_path / "stats"
    performance_dir = tmp_path / "performance"
    stats_dir.mkdir()
    performance_dir.mkdir()
    (stats_dir / "legacy.stats").write_text("", encoding="utf-8")
    (stats_dir / "Scenario Stats.csv").write_text("", encoding="utf-8")
    (stats_dir / "scenario stats.csv").write_text("", encoding="utf-8")
    (stats_dir / "notes.csv").write_text("", encoding="utf-8")
    (performance_dir / "Scenario.perf").write_text("", encoding="utf-8")
    (performance_dir / "Scenario.PERF").write_text("", encoding="utf-8")

    assert kovaak_directory_store.directory_status(
        stats_dir, kind="stats", source="confirmed",
    ) == {
        "path": str(stats_dir),
        "source": "confirmed",
        "matching_file_count": 2,
        "matching_files": "found",
    }
    assert kovaak_directory_store.directory_status(
        performance_dir, kind="performance", source="confirmed",
    )["matching_file_count"] == 1


@pytest.mark.asyncio
async def test_local_directories_api_requires_token_saves_empty_dirs_and_reconfigures_runtime(
    desktop_token,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _clear_kovaak_overrides(monkeypatch)
    stats_dir = tmp_path / "stats"
    performance_dir = tmp_path / "performance"
    stats_dir.mkdir()
    performance_dir.mkdir()
    calls: list[dict] = []

    class Runtime:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    previous_state = getattr(app.state, "kovaak_ingestion_service", None)
    app.state.kovaak_ingestion_service = Runtime()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.get("/api/kovaak-local-directories")
            saved = await client.put(
                "/api/kovaak-local-directories",
                headers=_desktop_headers(),
                json={"stats_dir": str(stats_dir), "performance_dir": str(performance_dir)},
            )

        assert missing.status_code == 401
        assert saved.status_code == 200
        body = saved.json()
        assert body["activation"] == "activated"
        assert body["stats"] == {
            "path": str(stats_dir.resolve()),
            "source": "confirmed",
            "matching_file_count": 0,
            "matching_files": "no_matching_files",
        }
        assert body["performance"]["matching_files"] == "no_matching_files"
        assert calls == [{
            "stats_dirs": [stats_dir.resolve()],
            "performance_dirs": [performance_dir.resolve()],
            "source": "confirmed",
        }]
    finally:
        if previous_state is None:
            delattr(app.state, "kovaak_ingestion_service")
        else:
            app.state.kovaak_ingestion_service = previous_state


@pytest.mark.asyncio
async def test_local_directories_api_rejects_same_directory_and_is_graceful_without_runtime(
    desktop_token,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _clear_kovaak_overrides(monkeypatch)
    directory = tmp_path / "same"
    other_directory = tmp_path / "other"
    directory.mkdir()
    other_directory.mkdir()
    previous_state = getattr(app.state, "kovaak_ingestion_service", None)
    if previous_state is not None:
        delattr(app.state, "kovaak_ingestion_service")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.put(
                "/api/kovaak-local-directories",
                headers=_desktop_headers(),
                json={"stats_dir": str(directory), "performance_dir": str(directory)},
            )
            saved = await client.put(
                "/api/kovaak-local-directories",
                headers=_desktop_headers(),
                json={"stats_dir": str(directory), "performance_dir": str(other_directory)},
            )

        assert invalid.status_code == 422
        assert saved.status_code == 200
        assert saved.json()["activation"] == "runtime_unavailable"
    finally:
        if previous_state is not None:
            app.state.kovaak_ingestion_service = previous_state


def _watcher_snapshot(*watchers: dict) -> dict:
    return {
        "version": "kovaak_watcher.v1",
        "source": "automatic",
        "watcher_count": len(watchers),
        "watchers": list(watchers),
    }


class SnapshotRuntime:
    """Minimal stand-in exposing only the diagnostics surface used by routes."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot

    def diagnostics(self) -> dict:
        return self._snapshot


@pytest.mark.asyncio
async def test_local_directories_reports_watcher_health_from_live_service(
    desktop_token,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    cases = [
        # 候选目录为 0：自动发现失败且未确认过目录。
        (_watcher_snapshot(), "no_candidates"),
        # watcher 在盯但目录已消失。
        (_watcher_snapshot({"directory_state": "directory_missing", "supported_count": 0}), "not_exporting"),
        # 目录存在但识别到的受支持文件数为 0（大概率没开统计导出）。
        (_watcher_snapshot({"directory_state": "ready", "supported_count": 0}), "not_exporting"),
        # 已有数据：不提示任何引导卡片。
        (_watcher_snapshot(
            {"directory_state": "ready", "supported_count": 2},
            {"directory_state": "ready", "supported_count": 0},
        ), "ingesting"),
    ]
    previous_state = getattr(app.state, "kovaak_ingestion_service", None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for snapshot, expected in cases:
                app.state.kovaak_ingestion_service = SnapshotRuntime(snapshot)
                response = await client.get(
                    "/api/kovaak-local-directories",
                    headers=_desktop_headers(),
                )
                assert response.status_code == 200
                assert response.json()["watcher_status"] == expected
    finally:
        if previous_state is None:
            delattr(app.state, "kovaak_ingestion_service")
        else:
            app.state.kovaak_ingestion_service = previous_state


@pytest.mark.asyncio
async def test_local_directories_watcher_health_falls_back_to_persisted_snapshot(
    desktop_token,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    previous_state = getattr(app.state, "kovaak_ingestion_service", None)
    if previous_state is not None:
        delattr(app.state, "kovaak_ingestion_service")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            absent = await client.get("/api/kovaak-local-directories", headers=_desktop_headers())
            assert absent.json()["watcher_status"] is None

            file_store.write_json(
                "diagnostics/kovaak-watcher.json",
                _watcher_snapshot({"directory_state": "ready", "supported_count": 0}),
            )
            empty_files = await client.get("/api/kovaak-local-directories", headers=_desktop_headers())
            assert empty_files.json()["watcher_status"] == "not_exporting"

            file_store.write_json("diagnostics/kovaak-watcher.json", {"watchers": []})
            zero_candidates = await client.get("/api/kovaak-local-directories", headers=_desktop_headers())
            assert zero_candidates.json()["watcher_status"] == "no_candidates"
    finally:
        if previous_state is not None:
            app.state.kovaak_ingestion_service = previous_state


@pytest.mark.asyncio
async def test_save_reconfigure_runs_off_event_loop_and_keeps_activation(
    desktop_token,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """reconfigure 内部 join 最长 2s；阻塞期间事件循环必须仍能服务其他请求。"""
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    _clear_kovaak_overrides(monkeypatch)
    stats_dir = tmp_path / "stats"
    performance_dir = tmp_path / "performance"
    stats_dir.mkdir()
    performance_dir.mkdir()
    started = threading.Event()
    release = threading.Event()
    calls: list[dict] = []

    class BlockingRuntime:
        def reconfigure(self, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("test never released reconfigure")
            calls.append(kwargs)
            return True

    runtime = BlockingRuntime()
    previous_state = getattr(app.state, "kovaak_ingestion_service", None)
    app.state.kovaak_ingestion_service = runtime
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            save_task = asyncio.create_task(client.put(
                "/api/kovaak-local-directories",
                headers=_desktop_headers(),
                json={"stats_dir": str(stats_dir), "performance_dir": str(performance_dir)},
            ))
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=5)
            # reconfigure 仍在阻塞线程里等待：GET 必须能立即返回。
            probe_task = asyncio.create_task(client.get(
                "/api/kovaak-local-directories", headers=_desktop_headers(),
            ))
            probe = await asyncio.wait_for(probe_task, timeout=2)
            assert probe.status_code == 200

            release.set()
            saved = await asyncio.wait_for(save_task, timeout=5)

        assert saved.status_code == 200
        assert saved.json()["activation"] == "activated"
        assert calls == [{
            "stats_dirs": [stats_dir.resolve()],
            "performance_dirs": [performance_dir.resolve()],
            "source": "confirmed",
        }]
    finally:
        release.set()
        if previous_state is None:
            delattr(app.state, "kovaak_ingestion_service")
        else:
            app.state.kovaak_ingestion_service = previous_state
