"""设置页「数据与存储」打磨的后端合同：行内 size/文件名透出 + reveal 端点。

- run 列表投影增量字段：attached 产物的当前 size 与文件名（无路径）。
- POST /api/storage/reveal：输入条目 id/kind，后端解析本地路径并调起
  explorer /select；文件路径绝不出现在请求或响应里（path-free 合同）。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import config, kovaak_run_store
import webapp.backend.routes as routes_mod
from webapp.backend.app import app

from .test_kovaak_run_storage_ledger import _seed_run_with_evidence

TOKEN = "reveal-token"
HEADERS = {"X-Aiming-Cookie-Desktop-Token": TOKEN}


@pytest.mark.asyncio
async def test_run_list_exposes_size_and_basename_without_paths(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    run = await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    run_root = config.DATA_ROOT / "runs" / str(run["id"])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/kovaak-runs", headers=HEADERS)

    assert response.status_code == 200, response.text
    item = response.json()["runs"][0]
    assert item["video_size_bytes"] == (run_root / "video-request-1.mp4").stat().st_size
    assert item["video_name"] == "video-request-1.mp4"
    assert item["raw_size_bytes"] == (run_root / "trace-ledger.bin").stat().st_size
    assert item["raw_name"] == "trace-ledger.bin"
    # path-free 合同：增量字段只是文件名，绝不携带目录分隔符。
    for key in ("video_name", "raw_name"):
        assert "/" not in item[key] and "\\" not in item[key]


@pytest.mark.asyncio
async def test_reveal_run_video_selects_local_file(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    run = await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    revealed: list[object] = []

    def fake_reveal(path) -> None:
        revealed.append(path)

    monkeypatch.setattr(routes_mod, "_reveal_in_explorer", fake_reveal)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/storage/reveal",
            json={"kind": "run_video", "run_id": run["id"]},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"revealed": True, "kind": "run_video"}
    assert len(revealed) == 1
    assert revealed[0] == config.DATA_ROOT / "runs" / str(run["id"]) / "video-request-1.mp4"


@pytest.mark.asyncio
async def test_reveal_run_raw_selects_trace_file(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    run = await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    revealed: list[object] = []
    monkeypatch.setattr(
        routes_mod, "_reveal_in_explorer", lambda path: revealed.append(path),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/storage/reveal",
            json={"kind": "run_raw", "run_id": run["id"]},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert revealed == [config.DATA_ROOT / "runs" / str(run["id"]) / "trace-ledger.bin"]


@pytest.mark.asyncio
async def test_reveal_incomplete_capture_by_item_ref(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    revealed: list[object] = []
    monkeypatch.setattr(
        routes_mod, "_reveal_in_explorer", lambda path: revealed.append(path),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        listing = await client.get("/api/storage/incomplete", headers=HEADERS)
        assert listing.status_code == 200, listing.text
        item = listing.json()["items"][0]
        response = await client.post(
            "/api/storage/reveal",
            json={"kind": "incomplete_capture", "item_ref": item["item_ref"]},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert len(revealed) == 1
    assert revealed[0].name == ".video-request-1.partial-recovery.mp4"


@pytest.mark.asyncio
async def test_reveal_missing_file_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    run = await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)
    (config.DATA_ROOT / "runs" / str(run["id"]) / "video-request-1.mp4").unlink()
    monkeypatch.setattr(
        routes_mod, "_reveal_in_explorer", lambda path: pytest.fail("must not reveal"),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/storage/reveal",
            json={"kind": "run_video", "run_id": run["id"]},
            headers=HEADERS,
        )

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_reveal_rejects_other_owner_and_unknown_refs(monkeypatch) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", TOKEN)
    run = await _seed_run_with_evidence(owner_id="someone-else")
    monkeypatch.setattr(
        routes_mod, "_reveal_in_explorer", lambda path: pytest.fail("must not reveal"),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        forbidden = await client.post(
            "/api/storage/reveal",
            json={"kind": "run_video", "run_id": run["id"]},
            headers=HEADERS,
        )
        unknown_ref = await client.post(
            "/api/storage/reveal",
            json={"kind": "incomplete_capture", "item_ref": "incomplete:does-not-exist"},
            headers=HEADERS,
        )
        unknown_run = await client.post(
            "/api/storage/reveal",
            json={"kind": "run_raw", "run_id": 999_999},
            headers=HEADERS,
        )

    assert forbidden.status_code == 403, forbidden.text
    assert unknown_ref.status_code == 404, unknown_ref.text
    assert unknown_run.status_code == 404, unknown_run.text


def test_reveal_in_explorer_falls_back_to_parent_dir_for_comma_paths(
    monkeypatch, tmp_path,
) -> None:
    """Windows 合法路径可含逗号；/select,<path> 会在逗号处截断 → 退化为打开父目录。"""
    calls: list[list[str]] = []

    monkeypatch.setattr(routes_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        routes_mod.subprocess, "Popen", lambda argv, *a, **k: calls.append(argv),
    )

    plain = tmp_path / "video.mp4"
    routes_mod._reveal_in_explorer(plain)
    assert calls[-1] == ["explorer", f"/select,{plain}"]

    with_comma = tmp_path / "my, video.mp4"
    routes_mod._reveal_in_explorer(with_comma)
    assert calls[-1] == ["explorer", str(tmp_path)]


@pytest.mark.asyncio
async def test_reveal_path_resolution_helper_contract(monkeypatch) -> None:
    """store 层合同：未 attached / 未知 kind / 缺 run_id 分别失败。"""
    run = await _seed_run_with_evidence(owner_id=config.DESKTOP_LOCAL_PROFILE)

    with pytest.raises(ValueError):
        await kovaak_run_store.resolve_storage_reveal_path(
            config.DESKTOP_LOCAL_PROFILE, "run_video", None, None, config.DATA_ROOT,
        )
    with pytest.raises(ValueError):
        await kovaak_run_store.resolve_storage_reveal_path(
            config.DESKTOP_LOCAL_PROFILE, "bogus", run["id"], None, config.DATA_ROOT,
        )
    await kovaak_run_store.remove_run_evidence(
        run["id"], config.DESKTOP_LOCAL_PROFILE, "raw", config.DATA_ROOT,
    )
    with pytest.raises(LookupError):
        await kovaak_run_store.resolve_storage_reveal_path(
            config.DESKTOP_LOCAL_PROFILE, "run_raw", run["id"], None, config.DATA_ROOT,
        )
