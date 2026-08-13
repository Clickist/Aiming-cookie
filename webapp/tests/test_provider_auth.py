from __future__ import annotations

import asyncio
import json
import os

import aiosqlite
import pytest

from webapp.backend import provider_auth, provider_commands, provider_store


def _profile(**overrides):
    value = {
        "name": "Task 4 Provider",
        "provider_id": "task4-provider",
        "kind": "custom_openai_compatible",
        "base_url": "https://provider.example/v1",
        "model_id": "task4-model",
        "api_key": "initial-secret",
        "is_default": True,
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def reset_provider_auth_state():
    provider_auth.reset_in_memory_state()
    yield
    provider_auth.reset_in_memory_state()
@pytest.mark.asyncio
async def test_runtime_credential_is_internal_and_oauth_extra_fields_persist():
    created = await provider_store.create_profile("owner-a", _profile())
    public = await provider_store.get_profile(created["id"], "owner-a")
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")

    assert public is not None
    assert "credential" not in public
    assert "api_key" not in public
    assert "initial-secret" not in repr(public)
    assert runtime is not None
    assert runtime["credential"] == {"type": "api_key", "key": "initial-secret"}

    oauth = {
        "type": "oauth",
        "access": "oauth-access-secret",
        "refresh": "oauth-refresh-secret",
        "expires": 4_102_444_800_000,
        "provider_account": "account-1",
        "provider_specific": {"tenant": "tenant-a"},
    }
    await provider_store.replace_credential(created["id"], "owner-a", oauth)

    restored = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert restored is not None
    assert restored["credential"] == oauth
    listed = await provider_store.list_profiles("owner-a")
    assert "oauth-access-secret" not in repr(listed)
    assert "oauth-refresh-secret" not in repr(listed)


@pytest.mark.asyncio
async def test_api_key_replace_delete_and_owner_isolation():
    created = await provider_store.create_profile("owner-a", _profile())
    profile_id = created["id"]

    updated = await provider_commands.set_api_key(
        "owner-a", profile_id, "replacement-secret"
    )
    assert updated is not None
    assert "replacement-secret" not in repr(updated)
    runtime = await provider_store.get_runtime_profile(profile_id, "owner-a")
    assert runtime is not None
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "replacement-secret",
    }

    deleted = await provider_commands.delete_credential("owner-a", profile_id)
    assert deleted is not None
    assert deleted["configured"] is False
    runtime = await provider_store.get_runtime_profile(profile_id, "owner-a")
    assert runtime is not None
    assert "credential" not in runtime


@pytest.mark.asyncio
async def test_auth_operation_owner_isolation_input_no_echo_and_cancel(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None)
    )
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(method, path, *, payload=None, timeout_s):
        calls.append((method, path, payload))
        if method == "POST" and path == "/v1/auth/operations":
            return {"id": "sidecar-op", "status": "awaiting_input", "prompts": [
                {"id": "code", "message": "Enter code"}
            ]}
        if method == "GET":
            return {"id": "sidecar-op", "status": "awaiting_input", "prompts": [
                {"id": "code", "message": "Enter code"}
            ]}
        if method == "POST" and path.endswith("/input"):
            return {"status": "awaiting_input"}
        if method == "DELETE":
            return {"status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    operation = await provider_commands.authorize(
        "owner-a", created["id"], mode="oauth"
    )

    assert await provider_commands.get_auth_operation(
        "owner-b", operation["operation_id"]
    ) is None
    submitted = await provider_commands.submit_auth_input(
        "owner-a", operation["operation_id"], "code", "input-secret-sentinel"
    )
    assert "input-secret-sentinel" not in repr(submitted)
    assert "input-secret-sentinel" not in repr(provider_auth.debug_operation_state())

    cancelled = await provider_commands.cancel_auth_operation(
        "owner-a", operation["operation_id"]
    )
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert any(
        call[2] == {"prompt_id": "code", "value": "input-secret-sentinel"}
        for call in calls
    )


@pytest.mark.asyncio
async def test_cancel_during_result_fetch_prevents_credential_commit(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a",
        _profile(kind="builtin", base_url=None, api_key=None),
    )
    result_requested = asyncio.Event()
    allow_result = asyncio.Event()

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {"id": "cancel-race", "status": "running"}
        if method == "GET":
            return {"id": "cancel-race", "status": "completed"}
        if method == "POST" and path.endswith("/take-result"):
            result_requested.set()
            await allow_result.wait()
            return {
                "credential": {"type": "oauth", "access": "cancelled-secret"},
            }
        if method == "DELETE":
            return {"id": "cancel-race", "status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    operation = await provider_commands.authorize(
        "owner-a", created["id"], mode="oauth",
    )
    finish_task = asyncio.create_task(
        provider_commands.get_auth_operation("owner-a", operation["operation_id"]),
    )
    await result_requested.wait()

    cancelled = await provider_commands.cancel_auth_operation(
        "owner-a", operation["operation_id"],
    )
    allow_result.set()
    finished = await finish_task

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert finished is not None
    assert finished["status"] == "cancelled"
    assert await provider_store.get_credential(created["id"], "owner-a") is None
    assert "cancelled-secret" not in repr(finished)


@pytest.mark.asyncio
async def test_revoke_invalidates_late_authorize_commit(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None)
    )

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {"id": "late-op", "status": "pending"}
        if method == "GET":
            return {"id": "late-op", "status": "completed"}
        if method == "POST" and path.endswith("/take-result"):
            return {"credential": {"type": "oauth", "access": "late-secret"}}
        if method == "DELETE":
            return {"status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    operation = await provider_commands.authorize(
        "owner-a", created["id"], mode="oauth"
    )
    revoked = await provider_commands.revoke("owner-a", created["id"])
    assert revoked == {
        "profile_id": created["id"],
        "revoked": True,
        "remote_revoked": False,
    }

    late = await provider_commands.get_auth_operation(
        "owner-a", operation["operation_id"]
    )
    assert late is not None
    assert late["status"] == "interrupted"
    assert await provider_store.get_credential(created["id"], "owner-a") is None
    assert "late-secret" not in repr(late)


@pytest.mark.asyncio
async def test_authorize_timeout_and_lost_sidecar_operation_are_recoverable(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None)
    )
    clock = [100.0]

    async def timeout_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {
                "id": "timeout-op",
                "status": "pending",
                "expires_in_ms": 1_000,
            }
        if method == "DELETE":
            return {"status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(provider_auth, "_request_json", timeout_request)
    operation = await provider_commands.authorize(
        "owner-a", created["id"], mode="oauth"
    )
    clock[0] += 2
    timed_out = await provider_commands.get_auth_operation(
        "owner-a", operation["operation_id"]
    )
    assert timed_out is not None
    assert timed_out["status"] == "timed_out"

    provider_auth.reset_in_memory_state()

    async def interrupted_request(method, path, *, payload=None, timeout_s):
        if method == "POST":
            return {"id": "lost-op", "status": "pending"}
        if method == "GET":
            raise provider_auth._SidecarNotFound(
                "operation_interrupted", "lost", status_code=404
            )
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", interrupted_request)
    second = await provider_commands.authorize(
        "owner-a", created["id"], mode="api_key"
    )
    interrupted = await provider_commands.get_auth_operation(
        "owner-a", second["operation_id"]
    )
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"]["retryable"] is True
@pytest.mark.asyncio
async def test_revoke_invalidates_late_refresh_commit(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None)
    )
    await provider_store.replace_credential(created["id"], "owner-a", {
        "type": "oauth",
        "access": "initial-oauth-access",
        "refresh": "initial-oauth-refresh",
        "expires": 1,
    })

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            assert payload["action"] == "refresh"
            assert payload["credential"]["access"] == "initial-oauth-access"
            return {"id": "late-refresh", "status": "pending"}
        if method == "GET":
            return {"id": "late-refresh", "status": "completed"}
        if method == "POST" and path.endswith("/take-result"):
            return {
                "credential": {
                    "type": "oauth",
                    "access": "late-refresh-secret",
                    "refresh": "late-refresh-token",
                    "expires": 4_102_444_800_000,
                }
            }
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    operation = await provider_commands.refresh("owner-a", created["id"])
    await provider_commands.revoke("owner-a", created["id"])

    late = await provider_commands.get_auth_operation(
        "owner-a", operation["operation_id"]
    )
    assert late is not None
    assert late["status"] == "interrupted"
    assert await provider_store.get_credential(created["id"], "owner-a") is None
    assert "late-refresh-secret" not in repr(late)


@pytest.mark.asyncio
async def test_auth_failure_redacts_provider_specific_credential_fields(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None)
    )
    sentinel = "provider-specific-auth-secret"
    await provider_store.replace_credential(created["id"], "owner-a", {
        "type": "oauth",
        "access": "ordinary-access-secret",
        "refresh": "ordinary-refresh-secret",
        "expires": 1,
        "provider_specific_secret": sentinel,
    })

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {"id": "failed-refresh", "status": "pending"}
        if method == "GET":
            return {
                "status": "failed",
                "error": {
                    "code": "invalid_grant",
                    "message": f"credential rejected: {sentinel}",
                    "retryable": True,
                },
            }
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    started = await provider_commands.refresh("owner-a", created["id"])
    failed = await provider_commands.get_auth_operation(
        "owner-a", started["operation_id"]
    )
    assert failed is not None
    assert failed["status"] == "failed"
    assert sentinel not in repr(failed)
    assert "[REDACTED]" in failed["error"]["message"]
    record = await provider_store.get_credential_record(created["id"], "owner-a")
    assert record is not None
    assert record["needs_reauth"] is True


@pytest.mark.asyncio
async def test_failed_refresh_cannot_mark_replaced_credential_needs_reauth(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a", _profile(kind="builtin", base_url=None),
    )
    await provider_store.replace_credential(created["id"], "owner-a", {
        "type": "oauth",
        "access": "expired-access",
        "refresh": "expired-refresh",
        "expires": 1,
    })

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            return {"id": "stale-failed-refresh", "status": "pending"}
        if method == "GET":
            return {
                "status": "failed",
                "error": {
                    "code": "invalid_grant",
                    "message": "old credential rejected",
                    "retryable": True,
                },
            }
        raise AssertionError((method, path, payload))

    mark_entered = asyncio.Event()
    allow_mark = asyncio.Event()
    original_mark = provider_store.mark_needs_reauth

    async def paused_mark(*args, **kwargs):
        mark_entered.set()
        await allow_mark.wait()
        return await original_mark(*args, **kwargs)

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    monkeypatch.setattr(provider_store, "mark_needs_reauth", paused_mark)
    started = await provider_commands.refresh("owner-a", created["id"])
    finish_task = asyncio.create_task(
        provider_commands.get_auth_operation("owner-a", started["operation_id"])
    )
    await mark_entered.wait()

    replaced = await provider_commands.set_api_key(
        "owner-a", created["id"], "fresh-api-key",
    )
    assert replaced is not None
    allow_mark.set()
    finished = await finish_task

    record = await provider_store.get_credential_record(created["id"], "owner-a")
    assert finished is not None
    assert finished["status"] == "interrupted"
    assert record is not None
    assert record["credential"] == {
        "type": "api_key",
        "key": "fresh-api-key",
    }
    assert record["needs_reauth"] is False


@pytest.mark.asyncio
async def test_authorize_reads_profile_metadata_after_waiting_for_scope_lock(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a",
        _profile(
            kind="builtin",
            provider_id="old-provider",
            model_id="old-model",
            base_url=None,
        ),
    )
    update_entered = asyncio.Event()
    allow_update = asyncio.Event()
    original_update = provider_store.update_profile

    async def paused_update(profile_id, owner_id, changes):
        update_entered.set()
        await allow_update.wait()
        return await original_update(profile_id, owner_id, changes)

    started_with: list[str] = []

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            started_with.append(payload["provider_id"])
            return {"id": "new-provider-operation", "status": "running"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_store, "update_profile", paused_update)
    monkeypatch.setattr(provider_auth, "_request_json", fake_request)

    update_task = asyncio.create_task(
        provider_commands.update_profile(
            "owner-a",
            created["id"],
            {"provider_id": "new-provider", "model_id": "new-model"},
        )
    )
    await update_entered.wait()
    authorize_task = asyncio.create_task(
        provider_commands.authorize("owner-a", created["id"], mode="oauth")
    )
    await asyncio.sleep(0)
    allow_update.set()

    await update_task
    await authorize_task
    assert started_with == ["new-provider"]


@pytest.mark.asyncio
async def test_profile_update_during_sidecar_start_does_not_resurrect_operation(monkeypatch):
    created = await provider_store.create_profile(
        "owner-a",
        _profile(
            kind="builtin",
            provider_id="old-provider",
            model_id="old-model",
            base_url=None,
        ),
    )
    start_entered = asyncio.Event()
    allow_start = asyncio.Event()
    cancelled: list[str] = []

    async def fake_request(method, path, *, payload=None, timeout_s):
        if method == "POST" and path == "/v1/auth/operations":
            start_entered.set()
            await allow_start.wait()
            return {"id": "stale-sidecar-operation", "status": "running"}
        if method == "DELETE" and path.endswith("/stale-sidecar-operation"):
            cancelled.append(path)
            return {"id": "stale-sidecar-operation", "status": "cancelled"}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(provider_auth, "_request_json", fake_request)
    authorize_task = asyncio.create_task(
        provider_commands.authorize("owner-a", created["id"], mode="oauth")
    )
    await start_entered.wait()
    await provider_commands.update_profile(
        "owner-a",
        created["id"],
        {"provider_id": "new-provider", "model_id": "new-model"},
    )
    allow_start.set()

    operation = await authorize_task
    assert operation["status"] == "interrupted"
    assert cancelled == ["/v1/auth/operations/stale-sidecar-operation"]