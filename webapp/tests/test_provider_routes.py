from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import coach_runtime, provider_store
from webapp.backend.app import app


async def _client(user_id: str):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": user_id},
    )


def _create_body(**overrides):
    body = {
        "name": "My Local Model",
        "provider_id": "my-local-provider",
        "kind": "custom_openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5",
        "context_window": 32768,
        "max_tokens": 4096,
        "api_key": "route-secret-key",
        "is_default": True,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_provider_profile_crud_redacts_key_and_is_owner_scoped():
    async with await _client("owner-a") as client:
        created_resp = await client.post("/api/provider-profiles", json=_create_body())
        assert created_resp.status_code == 200, created_resp.text
        created = created_resp.json()
        profile_id = created["id"]
        assert created["configured"] is True
        assert created["has_api_key"] is True
        assert "api_key" not in created
        assert "route-secret-key" not in created_resp.text

        listed_resp = await client.get("/api/provider-profiles")
        assert listed_resp.status_code == 200
        assert listed_resp.json()["profiles"] == [created]
        assert "route-secret-key" not in listed_resp.text

        updated_resp = await client.put(
            f"/api/provider-profiles/{profile_id}",
            json={"name": "Renamed", "api_key": None},
        )
        assert updated_resp.status_code == 200, updated_resp.text
        updated = updated_resp.json()
        assert updated["name"] == "Renamed"
        assert updated["configured"] is False
        assert updated["has_api_key"] is False
        assert "api_key" not in updated

    async with await _client("owner-b") as other:
        assert (await other.get(f"/api/provider-profiles/{profile_id}")).status_code == 404
        assert (await other.put(
            f"/api/provider-profiles/{profile_id}", json={"name": "stolen"},
        )).status_code == 404
        assert (await other.delete(f"/api/provider-profiles/{profile_id}")).status_code == 404
        assert (await other.post(
            f"/api/provider-profiles/{profile_id}/default",
        )).status_code == 404

    async with await _client("owner-a") as client:
        deleted = await client.delete(f"/api/provider-profiles/{profile_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "id": profile_id}


@pytest.mark.asyncio
async def test_provider_identity_update_without_new_key_requires_reauthentication():
    created = await provider_store.create_profile("owner-a", _create_body())

    async with await _client("owner-a") as client:
        response = await client.put(
            f"/api/provider-profiles/{created['id']}",
            json={
                "provider_id": "replacement-provider",
                "base_url": "http://127.0.0.1:22434/v1",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False
    assert response.json()["credential_configured"] is False
    assert "route-secret-key" not in response.text
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["provider_id"] == "replacement-provider"
    assert "credential" not in runtime


@pytest.mark.asyncio
async def test_provider_identity_and_key_route_update_roll_back_together(monkeypatch):
    created = await provider_store.create_profile("owner-a", _create_body())
    original_normalize = provider_store.normalize_credential

    def fail_replacement_credential(value):
        if value.get("key") == "replacement-route-key":
            raise RuntimeError("credential write failed")
        return original_normalize(value)

    monkeypatch.setattr(provider_store, "normalize_credential", fail_replacement_credential)
    async with await _client("owner-a") as client:
        with pytest.raises(RuntimeError, match="credential write failed"):
            await client.put(
                f"/api/provider-profiles/{created['id']}",
                json={
                    "provider_id": "replacement-provider",
                    "base_url": "http://127.0.0.1:22434/v1",
                    "api_key": "replacement-route-key",
                },
            )

    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["provider_id"] == "my-local-provider"
    assert runtime["base_url"] == "http://127.0.0.1:11434/v1"
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "route-secret-key",
    }


@pytest.mark.asyncio
async def test_custom_provider_requires_http_url_and_model_fields():
    async with await _client("owner-a") as client:
        body_without_internal_id = _create_body()
        body_without_internal_id.pop("provider_id")
        generated = await client.post(
            "/api/provider-profiles", json=body_without_internal_id,
        )
        assert generated.status_code == 200, generated.text
        assert generated.json()["provider_id"].startswith("custom:")

        bad_url = await client.post(
            "/api/provider-profiles", json=_create_body(base_url="file:///tmp/model"),
        )
        assert bad_url.status_code == 422
        missing_model = await client.post(
            "/api/provider-profiles", json=_create_body(model_id="  "),
        )
        assert missing_model.status_code == 422
        missing_key = await client.post(
            "/api/provider-profiles", json=_create_body(api_key=None),
        )
        assert missing_key.status_code == 422


@pytest.mark.asyncio
async def test_custom_provider_without_discovered_limits_is_explicitly_unconfigured():
    async with await _client("owner-a") as client:
        created = await client.post(
            "/api/provider-profiles",
            json=_create_body(context_window=None, max_tokens=None),
        )
        assert created.status_code == 200
        status = await client.get(f"/api/provider-profiles/{created.json()['id']}/status")

    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["status"] == "unconfigured"
    assert "context_window" in status.json()["message"]


@pytest.mark.asyncio
async def test_anthropic_compatible_custom_provider_profile_is_accepted():
    async with await _client("owner-a") as client:
        response = await client.post(
            "/api/provider-profiles",
            json=_create_body(
                kind="custom_anthropic_compatible",
                base_url="https://provider.example/v1",
                model_id="claude-custom",
            ),
        )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "custom_anthropic_compatible"
    assert "route-secret-key" not in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": None},
        {"kind": "builtin", "provider_id": None, "base_url": None},
    ],
)
@pytest.mark.asyncio
async def test_provider_create_validation_error_does_not_echo_api_key(overrides):
    sentinel = "validation-secret-sentinel"
    body = _create_body(api_key=sentinel, **overrides)

    async with await _client("owner-a") as client:
        response = await client.post("/api/provider-profiles", json=body)

    assert response.status_code == 422
    assert sentinel not in response.text


@pytest.mark.asyncio
async def test_custom_provider_rejects_userinfo_in_base_url_without_echoing_it():
    sentinel = "url-embedded-secret"

    async with await _client("owner-a") as client:
        response = await client.post(
            "/api/provider-profiles",
            json=_create_body(
                base_url=f"https://user:{sentinel}@example.com/v1",
            ),
        )

    assert response.status_code == 422
    assert sentinel not in response.text


@pytest.mark.asyncio
async def test_default_status_is_unconfigured_without_profile_and_never_calls_sidecar(monkeypatch):
    calls: list[dict] = []

    async def fake_test(profile):
        calls.append(profile)
        return {"configured": True, "status": "ready", "message": "unexpected"}

    monkeypatch.setattr(coach_runtime, "get_provider_profile_status", fake_test)
    monkeypatch.setattr(coach_runtime, "test_provider_profile", fake_test)
    async with await _client("owner-a") as client:
        resp = await client.get("/api/provider-profiles/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "profile_id": None,
        "configured": False,
        "status": "unconfigured",
        "message": "Coach Provider 尚未配置",
    }
    assert calls == []


@pytest.mark.asyncio
async def test_default_and_connection_test_use_runtime_secret_but_redact_response(monkeypatch):
    captured: list[dict] = []

    async def fake_test(profile):
        captured.append(profile)
        return {
            "configured": True,
            "status": "ready",
            "message": "连接成功",
            "api_key": profile["credential"]["key"],
        }

    monkeypatch.setattr(coach_runtime, "get_provider_profile_status", fake_test)
    monkeypatch.setattr(coach_runtime, "test_provider_profile", fake_test)
    created = await provider_store.create_profile("owner-a", _create_body())

    async with await _client("owner-a") as client:
        status_resp = await client.get("/api/provider-profiles/status")
        test_resp = await client.post(f"/api/provider-profiles/{created['id']}/test")

    for resp in (status_resp, test_resp):
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_id"] == created["id"]
        assert resp.json()["configured"] is True
        assert resp.json()["status"] == "ready"
        assert "api_key" not in resp.json()
        assert "route-secret-key" not in resp.text
    assert len(captured) == 2
    assert all(
        profile["credential"]["key"] == "route-secret-key"
        for profile in captured
    )


@pytest.mark.asyncio
async def test_connection_test_sidecar_failure_is_recoverable(monkeypatch):
    async def fake_test(profile):
        return {
            "configured": True,
            "status": "connection_failed",
            "message": "Provider 连接失败，请检查设置后重试",
        }

    monkeypatch.setattr(coach_runtime, "test_provider_profile", fake_test)
    created = await provider_store.create_profile("owner-a", _create_body())

    async with await _client("owner-a") as client:
        resp = await client.post(f"/api/provider-profiles/{created['id']}/test")

    assert resp.status_code == 200
    assert resp.json()["status"] == "connection_failed"


@pytest.mark.asyncio
async def test_catalog_route_proxies_full_pi_catalog_without_allow_list(monkeypatch):
    catalog = {
        "providers": [
            {
                "provider_id": "arbitrary-pi-provider",
                "provider_name": "Arbitrary Pi Provider",
                "api_key": "catalog-secret",
                "models": [{"model_id": "model-from-pi"}],
            }
        ]
    }

    async def fake_catalog():
        return catalog

    monkeypatch.setattr(coach_runtime, "fetch_provider_catalog", fake_catalog)
    async with await _client("owner-a") as client:
        resp = await client.get("/api/providers/catalog")

    assert resp.status_code == 200
    assert resp.json()["providers"][0]["provider_id"] == "arbitrary-pi-provider"
    assert "api_key" not in resp.json()["providers"][0]
    assert "catalog-secret" not in resp.text
