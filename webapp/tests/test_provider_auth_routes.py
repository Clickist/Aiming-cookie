from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import coach_runtime, provider_auth, provider_store
from webapp.backend.app import app


async def _client(owner: str):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": owner},
    )


@pytest.fixture(autouse=True)
def reset_auth_state():
    provider_auth.reset_in_memory_state()
    yield
    provider_auth.reset_in_memory_state()


async def _profile(owner: str = "owner-a"):
    return await provider_store.create_profile(owner, {
        "name": "Built-in",
        "provider_id": "pi-provider",
        "kind": "builtin",
        "base_url": None,
        "model_id": "pi-model",
        "api_key": "route-initial-secret",
        "is_default": True,
    })


@pytest.mark.asyncio
async def test_write_only_api_key_and_delete_routes_are_owner_scoped():
    profile = await _profile()
    path = f"/api/provider-profiles/{profile['id']}/auth/api-key"

    async with await _client("owner-b") as other:
        denied = await other.put(path, json={"api_key": "stolen-secret"})
    assert denied.status_code == 404

    async with await _client("owner-a") as client:
        updated = await client.put(path, json={"api_key": "route-new-secret"})
        deleted = await client.delete(
            f"/api/provider-profiles/{profile['id']}/auth/credential"
        )

    assert updated.status_code == 200
    assert "route-new-secret" not in updated.text
    assert "credential" not in updated.json()
    assert deleted.status_code == 200
    assert deleted.json()["configured"] is False
    assert deleted.json()["credential_configured"] is False
    assert deleted.json()["status"] == "unconfigured"
    runtime = await provider_store.get_runtime_profile(profile["id"], "owner-a")
    assert runtime is not None
    assert "credential" not in runtime


@pytest.mark.asyncio
async def test_authorize_input_cancel_routes_never_echo_input(monkeypatch):
    profile = await _profile()

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {
                "operation_id": "remote-op",
                "status": "awaiting_input",
                "prompts": [{"prompt_id": "device", "message": "Enter device code"}],
                "events": [{
                    "type": "device_code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://provider.example/device",
                }],
            }
        if method == "POST" and path.endswith("/input"):
            return {"status": "awaiting_input"}
        if method == "GET":
            return {
                "status": "awaiting_input",
                "prompts": [{"prompt_id": "device", "message": "Enter device code"}],
                "events": [{
                    "type": "device_code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://provider.example/device",
                }],
            }
        if method == "DELETE":
            return {"status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    async with await _client("owner-a") as client:
        started = await client.post(
            f"/api/provider-profiles/{profile['id']}/auth/authorize",
            json={"mode": "oauth"},
        )
        operation_id = started.json()["operation_id"]
        submitted = await client.post(
            f"/api/provider-auth-operations/{operation_id}/input",
            json={"prompt_id": "device", "value": "route-input-secret"},
        )
        cancelled = await client.post(
            f"/api/provider-auth-operations/{operation_id}/cancel"
        )

    assert started.status_code == 200
    assert started.json()["events"] == [{
        "type": "device_code",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://provider.example/device",
    }]
    assert submitted.status_code == 200
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert "route-input-secret" not in submitted.text
    assert "route-input-secret" not in repr(provider_auth.debug_operation_state())


@pytest.mark.asyncio
async def test_device_code_is_an_event_not_an_authorize_mode():
    profile = await _profile()
    async with await _client("owner-a") as client:
        response = await client.post(
            f"/api/provider-profiles/{profile['id']}/auth/authorize",
            json={"mode": "device_code"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_status_uses_readiness_only_and_explicit_test_is_separate(monkeypatch):
    profile = await _profile()
    calls = {"status": 0, "test": 0}

    async def fake_status(runtime):
        calls["status"] += 1
        return {
            "configured": True,
            "status": "auth_expired",
            "message": "expired",
        }

    async def fake_test(runtime):
        calls["test"] += 1
        return {"configured": True, "status": "ready", "message": "tested"}

    monkeypatch.setattr(coach_runtime, "get_provider_profile_status", fake_status)
    monkeypatch.setattr(coach_runtime, "test_provider_profile", fake_test)
    async with await _client("owner-a") as client:
        status = await client.get(f"/api/provider-profiles/{profile['id']}/status")
        assert calls == {"status": 1, "test": 0}
        tested = await client.post(f"/api/provider-profiles/{profile['id']}/test")

    assert status.status_code == 200
    assert status.json()["status"] == "auth_expired"
    assert tested.status_code == 200
    assert calls == {"status": 1, "test": 1}


@pytest.mark.asyncio
async def test_custom_provider_model_discovery_is_temporary_and_never_echoes_key(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    async def fetch_models(protocol: str, base_url: str, api_key: str):
        calls.append((protocol, base_url, api_key))
        return ["provider-model-a", "provider-model-b"]

    monkeypatch.setattr(coach_runtime, "fetch_custom_provider_models", fetch_models)
    secret = "custom-model-list-secret"
    async with await _client("owner-a") as client:
        response = await client.post(
            "/api/provider-profiles/custom/models",
            json={
                "protocol": "openai-completions",
                "base_url": "https://provider.example/v1",
                "api_key": secret,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["provider-model-a", "provider-model-b"]}
    assert calls == [("openai-completions", "https://provider.example/v1", secret)]
    assert secret not in response.text


@pytest.mark.asyncio
async def test_custom_provider_model_discovery_falls_back_without_echoing_key(monkeypatch):
    async def unavailable(protocol: str, base_url: str, api_key: str):
        raise coach_runtime.CustomProviderModelDiscoveryError("unavailable")

    monkeypatch.setattr(coach_runtime, "fetch_custom_provider_models", unavailable)
    secret = "custom-model-list-secret"
    async with await _client("owner-a") as client:
        response = await client.post(
            "/api/provider-profiles/custom/models",
            json={
                "protocol": "anthropic-messages",
                "base_url": "https://provider.example/v1",
                "api_key": secret,
            },
        )

    assert response.status_code == 502
    assert secret not in response.text


@pytest.mark.asyncio
async def test_anthropic_model_discovery_uses_anthropic_endpoint_and_headers(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "claude-custom"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, *, headers: dict[str, str]):
            calls.append((url, headers))
            return FakeResponse()

    monkeypatch.setattr(coach_runtime.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    secret = "custom-model-list-secret"

    models = await coach_runtime.fetch_custom_provider_models(
        "anthropic-messages",
        "https://provider.example",
        secret,
    )

    assert models == ["claude-custom"]
    assert calls == [(
        "https://provider.example/v1/models",
        {
            "x-api-key": secret,
            "anthropic-version": "2023-06-01",
        },
    )]
