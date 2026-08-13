from __future__ import annotations

import asyncio

import pytest

from webapp.backend import provider_store


def _profile(**overrides):
    value = {
        "name": "Local OpenAI",
        "provider_id": "local-openai",
        "kind": "custom_openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5",
        "context_window": 32768,
        "max_tokens": 4096,
        "api_key": "secret-provider-key",
        "is_default": False,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_provider_profile_public_reads_redact_api_key_runtime_read_includes_it():
    created = await provider_store.create_profile("owner-a", _profile(is_default=True))

    assert created["name"] == "Local OpenAI"
    assert created["configured"] is True
    assert created["credential_configured"] is True
    assert created["has_api_key"] is True
    assert created["status"] == "ready"
    assert "api_key" not in created

    listed = await provider_store.list_profiles("owner-a")
    assert listed == [created]
    assert "secret-provider-key" not in repr(listed)

    runtime = await provider_store.get_default_runtime_profile("owner-a")
    assert runtime == {
        "profile_id": created["id"],
        "provider_id": "local-openai",
        "provider_name": "Local OpenAI",
        "kind": "custom_openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5",
        "context_window": 32768,
        "max_tokens": 4096,
        "credential": {"type": "api_key", "key": "secret-provider-key"},
    }


@pytest.mark.asyncio
async def test_anthropic_compatible_profile_is_configured_and_keeps_credential_private():
    created = await provider_store.create_profile(
        "owner-a",
        _profile(
            name="Custom Anthropic",
            provider_id="custom-anthropic",
            kind="custom_anthropic_compatible",
            base_url="https://provider.example/v1",
            model_id="claude-custom",
            is_default=True,
        ),
    )

    assert created["kind"] == "custom_anthropic_compatible"
    assert created["configured"] is True
    assert "secret-provider-key" not in repr(created)
    runtime = await provider_store.get_default_runtime_profile("owner-a")
    assert runtime is not None
    assert runtime["kind"] == "custom_anthropic_compatible"
    assert runtime["base_url"] == "https://provider.example"
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "secret-provider-key",
    }
    assert provider_store.runtime_profile_configured(runtime) is True


@pytest.mark.asyncio
async def test_custom_profile_without_discovered_limits_is_persisted_but_not_runtime_ready():
    created = await provider_store.create_profile(
        "owner-a", _profile(context_window=None, max_tokens=None),
    )

    assert created["context_window"] is None
    assert created["max_tokens"] is None
    assert created["configured"] is False
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["context_window"] is None
    assert runtime["max_tokens"] is None
    assert provider_store.runtime_profile_configured(runtime) is False


@pytest.mark.asyncio
async def test_provider_profiles_are_single_owner_and_crud_works():
    # Local single-user desktop: profiles are not multi-user isolated.
    created = await provider_store.create_profile("owner-a", _profile())
    profile_id = created["id"]

    assert await provider_store.get_profile(profile_id, "owner-b") is not None
    assert await provider_store.get_runtime_profile(profile_id, "owner-b") is not None
    assert await provider_store.update_profile(profile_id, "owner-b", {"name": "Local OpenAI"}) is not None
    assert await provider_store.set_default_profile(profile_id, "owner-b") is not None

    untouched = await provider_store.get_profile(profile_id, "owner-a")
    assert untouched is not None
    assert untouched["name"] == "Local OpenAI"
    assert untouched["is_default"] is True
    assert await provider_store.delete_profile(profile_id, "owner-b") is True


@pytest.mark.asyncio
async def test_default_selection_is_transaction_safe_and_leaves_one_owner_default():
    first = await provider_store.create_profile("owner-a", _profile(name="First"))
    second = await provider_store.create_profile(
        "owner-a",
        _profile(name="Second", provider_id="second", model_id="model-2"),
    )

    results = await asyncio.gather(
        provider_store.set_default_profile(first["id"], "owner-a"),
        provider_store.set_default_profile(second["id"], "owner-a"),
    )
    assert all(result is not None for result in results)

    profiles = await provider_store.list_profiles("owner-a")
    defaults = [profile for profile in profiles if profile["is_default"]]
    assert len(defaults) == 1


@pytest.mark.asyncio
async def test_api_key_can_be_replaced_or_cleared_without_public_disclosure():
    created = await provider_store.create_profile("owner-a", _profile(is_default=True))

    updated = await provider_store.update_profile(
        created["id"], "owner-a", {"api_key": "replacement-key"},
    )
    assert updated is not None
    assert updated["configured"] is True
    assert "api_key" not in updated
    runtime = await provider_store.get_default_runtime_profile("owner-a")
    assert runtime is not None
    assert runtime["credential"]["key"] == "replacement-key"

    cleared = await provider_store.update_profile(
        created["id"], "owner-a", {"api_key": None},
    )
    assert cleared is not None
    assert cleared["configured"] is False
    assert cleared["credential_configured"] is False
    assert cleared["has_api_key"] is False
    assert cleared["status"] == "unconfigured"


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "other-openai"},
        {"base_url": "http://127.0.0.1:22434/v1"},
        {"kind": "builtin", "provider_id": "openai", "base_url": None},
    ],
)
@pytest.mark.asyncio
async def test_provider_identity_change_clears_existing_credential(changes):
    created = await provider_store.create_profile("owner-a", _profile())

    updated = await provider_store.update_profile(
        created["id"], "owner-a", changes,
    )

    assert updated is not None
    assert updated["configured"] is False
    assert updated["credential_configured"] is False
    assert updated["status"] == "unconfigured"
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert "credential" not in runtime


@pytest.mark.asyncio
async def test_unchanged_provider_identity_preserves_existing_credential():
    created = await provider_store.create_profile("owner-a", _profile())

    updated = await provider_store.update_profile(
        created["id"],
        "owner-a",
        {
            "provider_id": "local-openai",
            "kind": "custom_openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    )

    assert updated is not None
    assert updated["configured"] is True
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "secret-provider-key",
    }


@pytest.mark.asyncio
async def test_model_change_clears_stale_discovered_capabilities_but_keeps_provider_credential():
    created = await provider_store.create_profile("owner-a", _profile())

    updated = await provider_store.update_profile(
        created["id"], "owner-a", {"model_id": "new-model"},
    )

    assert updated is not None
    assert updated["context_window"] is None
    assert updated["max_tokens"] is None
    assert updated["configured"] is False
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "secret-provider-key",
    }


@pytest.mark.asyncio
async def test_provider_identity_and_new_api_key_update_together():
    created = await provider_store.create_profile("owner-a", _profile())

    updated = await provider_store.update_profile(
        created["id"],
        "owner-a",
        {
            "provider_id": "other-openai",
            "base_url": "http://127.0.0.1:22434/v1",
            "api_key": "replacement-key",
        },
    )

    assert updated is not None
    assert updated["configured"] is True
    runtime = await provider_store.get_runtime_profile(created["id"], "owner-a")
    assert runtime is not None
    assert runtime["provider_id"] == "other-openai"
    assert runtime["base_url"] == "http://127.0.0.1:22434/v1"
    assert runtime["credential"] == {
        "type": "api_key",
        "key": "replacement-key",
    }


@pytest.mark.asyncio
async def test_builtin_profile_without_stored_credential_does_not_claim_ready():
    created = await provider_store.create_profile(
        "owner-a",
        {
            "name": "Ambient-capable built-in",
            "provider_id": "openai",
            "kind": "builtin",
            "base_url": None,
            "model_id": "gpt-5.4",
            "api_key": None,
            "is_default": True,
        },
    )

    assert created["configured"] is False
    assert created["credential_configured"] is False
    assert created["status"] == "unconfigured"
    assert provider_store.runtime_profile_configured(
        await provider_store.get_runtime_profile(created["id"], "owner-a")
    ) is True  # metadata is valid; Pi status may still resolve ambient auth.
