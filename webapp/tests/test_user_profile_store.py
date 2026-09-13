"""Tests for the Intro Session user profile store and its routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import (
    config,
    kovaak_run_store,
    user_profile_store as store,
)
from webapp.backend.app import app


PROFILE_PATH = "config/user-profile.json"

EMPTY_PROFILE = {
    "games": [],
    "experience": None,
    "self_assessment": None,
    "goal": None,
    "steam_profile_url": None,
    "updated_at": None,
}


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u1"},
    )


async def _seed_run(
    user_id: str,
    *,
    source_key: str,
    scenario: str,
    score: int,
    duration_ms: int,
) -> None:
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=user_id,
        source_key=source_key,
        scenario=scenario,
        stats_summary={"summary": {"Score": str(score)}},
    )
    await kovaak_run_store.set_run_alignment(
        run["id"],
        user_id,
        state="resolved",
        summary={
            "start_ms": 0,
            "end_ms": duration_ms,
            "duration_ms": duration_ms,
            "start_source": "test",
            "end_source": "test",
            "warnings": [],
        },
        start_epoch_ms=0,
        end_epoch_ms=duration_ms,
    )


# --------------------------------------------------------------- store 校验


@pytest.mark.asyncio
async def test_games_bounds_and_types_are_enforced():
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"games": ["g"] * 9})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"games": ["x" * 41]})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"games": "CS2"})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"games": [1]})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"games": ["  "]})

    saved = await store.update_profile("u1", {"games": ["x" * 40] * 8})
    assert len(saved["games"]) == 8
    assert saved["games"][0] == "x" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "limit"),
    [("experience", 300), ("self_assessment", 500), ("goal", 300)],
)
async def test_text_field_bounds_and_types_are_enforced(field: str, limit: int):
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {field: "x" * (limit + 1)})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {field: 123})

    saved = await store.update_profile("u1", {field: "x" * limit})
    assert saved[field] == "x" * limit
    # 空串归一化为 None（可跳过的自由文本项）。
    cleared = await store.update_profile("u1", {field: "   "})
    assert cleared[field] is None


@pytest.mark.asyncio
async def test_whitelist_rejects_unknown_fields_and_empty_updates():
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {"notes": "llm output"})
    with pytest.raises(store.InvalidUserProfile):
        await store.update_profile("u1", {})


def test_normalize_steam_profile_accepts_ids_and_links_and_nulls_the_rest():
    steam_id = "76561199033719938"
    assert store.normalize_steam_profile(steam_id) == (
        f"https://steamcommunity.com/profiles/{steam_id}"
    )
    assert store.normalize_steam_profile(
        f"https://steamcommunity.com/profiles/{steam_id}/",
    ) == f"https://steamcommunity.com/profiles/{steam_id}"
    # Vanity 链接保留规范形态（离线无法把 vanity 解析成 17 位 SteamID）。
    assert store.normalize_steam_profile(
        "https://steamcommunity.com/id/vapor/",
    ) == "https://steamcommunity.com/id/vapor"
    assert store.normalize_steam_profile(
        "steamcommunity.com/id/vapor",
    ) == "https://steamcommunity.com/id/vapor"
    # 非法输入一律 null，不报错。
    assert store.normalize_steam_profile(f"https://example.com/profiles/{steam_id}") is None
    assert store.normalize_steam_profile("not a link") is None
    assert store.normalize_steam_profile("https://steamcommunity.com/profiles/12345") is None
    assert store.normalize_steam_profile(None) is None
    assert store.normalize_steam_profile("x" * 201) is None


@pytest.mark.asyncio
async def test_invalid_steam_link_is_stored_as_null_without_error():
    saved = await store.update_profile("u1", {"steam_profile_url": "not a link"})
    assert saved["steam_profile_url"] is None


# --------------------------------------------------------------- 存储行为


@pytest.mark.asyncio
async def test_update_writes_atomically_and_leaves_no_temp_file():
    await store.update_profile("u1", {"experience": "断断续续小半年"})

    path = Path(config.DATA_ROOT) / PROFILE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["experience"] == "断断续续小半年"
    assert isinstance(payload["updated_at"], str)
    assert payload["updated_at"].endswith("Z")
    assert not (path.parent / f".{path.name}.tmp").exists()


@pytest.mark.asyncio
async def test_partial_update_preserves_previous_fields():
    await store.update_profile("u1", {"games": ["CS2"], "goal": "甩枪上 Fox"})
    saved = await store.update_profile("u1", {"experience": "半年"})
    assert saved["games"] == ["CS2"]
    assert saved["goal"] == "甩枪上 Fox"
    assert saved["experience"] == "半年"


# --------------------------------------------------------------- 路由


@pytest.mark.asyncio
async def test_get_user_profile_returns_empty_defaults_without_file():
    async with _client() as client:
        response = await client.get("/api/user-profile")
    assert response.status_code == 200
    assert response.json() == EMPTY_PROFILE


@pytest.mark.asyncio
async def test_put_user_profile_is_partial_and_idempotent():
    body = {
        "games": ["CS2", "Valorant"],
        "steam_profile_url": "https://steamcommunity.com/id/vapor/",
    }
    async with _client() as client:
        first = await client.put("/api/user-profile", json=body)
        second = await client.put("/api/user-profile", json=body)
        partial = await client.put("/api/user-profile", json={"goal": "甩枪上 Fox"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["steam_profile_url"] == "https://steamcommunity.com/id/vapor"
    assert second.json()["steam_profile_url"] == "https://steamcommunity.com/id/vapor"
    assert first.json()["games"] == ["CS2", "Valorant"]
    assert partial.json()["games"] == ["CS2", "Valorant"]
    assert partial.json()["goal"] == "甩枪上 Fox"


@pytest.mark.asyncio
async def test_put_user_profile_rejects_invalid_fields():
    async with _client() as client:
        too_many_games = await client.put(
            "/api/user-profile", json={"games": ["g"] * 9},
        )
        unknown_field = await client.put(
            "/api/user-profile", json={"system_prompt": "hijack"},
        )
    assert too_many_games.status_code == 422
    assert unknown_field.status_code == 422


# --------------------------------------------------------------- intro-context


@pytest.mark.asyncio
async def test_intro_context_without_data_returns_defaults():
    async with _client() as client:
        response = await client.get("/api/coach/intro-context")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "has_local_data": False,
        "total_runs": 0,
        "total_playtime_minutes": 0,
        "last_played_at": None,
        "top_scenarios": [],
    }


@pytest.mark.asyncio
async def test_intro_context_summarizes_local_runs():
    await _seed_run(
        "u1",
        source_key="Alpha - challenge - 2026.09.01-10.00.00",
        scenario="Alpha",
        score=100,
        duration_ms=1_800_000,
    )
    await _seed_run(
        "u1",
        source_key="Beta - challenge - 2026.09.02-10.00.00",
        scenario="Beta",
        score=900,
        duration_ms=600_000,
    )
    # 他人数据不能进入 u1 的摘要。
    await _seed_run(
        "other",
        source_key="Gamma - challenge - 2026.09.03-10.00.00",
        scenario="Gamma",
        score=5000,
        duration_ms=9_000_000,
    )

    async with _client() as client:
        response = await client.get("/api/coach/intro-context")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_local_data"] is True
    assert body["total_runs"] == 2
    assert body["total_playtime_minutes"] == 40
    assert isinstance(body["last_played_at"], str)
    assert body["last_played_at"].endswith("Z")
    assert [item["name"] for item in body["top_scenarios"]] == ["Beta", "Alpha"]
    assert [item["score"] for item in body["top_scenarios"]] == [900, 100]
