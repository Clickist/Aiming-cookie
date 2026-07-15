"""Shared Provider credential/auth application handlers.

This is intentionally a small function set rather than a speculative command
framework.  HTTP routes and Coach preflight call the same owner-scoped logic.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any, Mapping

from . import provider_auth, provider_store


def _api_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("api_key must not be blank")
    return value.strip()


async def create_profile(
    owner_id: str, value: Mapping[str, Any],
) -> dict[str, Any]:
    return await provider_store.create_profile(owner_id, value)


async def update_profile(
    owner_id: str, profile_id: int, changes: Mapping[str, Any],
) -> dict[str, Any] | None:
    lock = provider_auth.get_scope_lock(owner_id, profile_id)
    async with lock:
        if any(key in changes for key in ("provider_id", "kind", "base_url")):
            provider_auth.invalidate_scope(owner_id, profile_id)
        return await provider_store.update_profile(profile_id, owner_id, changes)


async def delete_profile(owner_id: str, profile_id: int) -> bool:
    lock = provider_auth.get_scope_lock(owner_id, profile_id)
    async with lock:
        if await provider_store.get_profile(profile_id, owner_id) is None:
            return False
        return await provider_store.delete_profile(profile_id, owner_id)


async def set_api_key(
    owner_id: str, profile_id: int, api_key: str,
) -> dict[str, Any] | None:
    key = _api_key(api_key)
    lock = provider_auth.get_scope_lock(owner_id, profile_id)
    async with lock:
        if await provider_store.get_profile(profile_id, owner_id) is None:
            return None
        provider_auth.invalidate_scope(owner_id, profile_id)
        return await provider_store.replace_credential(
            profile_id,
            owner_id,
            {"type": "api_key", "key": key},
            invalidate=False,
        )


async def delete_credential(
    owner_id: str, profile_id: int,
) -> dict[str, Any] | None:
    lock = provider_auth.get_scope_lock(owner_id, profile_id)
    async with lock:
        if await provider_store.get_profile(profile_id, owner_id) is None:
            return None
        provider_auth.invalidate_scope(owner_id, profile_id)
        return await provider_store.delete_credential(
            profile_id, owner_id, invalidate=False,
        )


async def authorize(
    owner_id: str, profile_id: int, *, mode: str,
) -> dict[str, Any]:
    return await provider_auth.start_authorize(owner_id, profile_id, mode)


async def refresh(
    owner_id: str, profile_id: int,
) -> dict[str, Any]:
    return await provider_auth.start_refresh(owner_id, profile_id)


async def get_auth_operation(
    owner_id: str, operation_id: str,
) -> dict[str, Any] | None:
    return await provider_auth.get_operation(owner_id, operation_id)


async def submit_auth_input(
    owner_id: str,
    operation_id: str,
    prompt_id: str,
    value: str,
) -> dict[str, Any] | None:
    return await provider_auth.submit_input(owner_id, operation_id, prompt_id, value)


async def cancel_auth_operation(
    owner_id: str, operation_id: str,
) -> dict[str, Any] | None:
    return await provider_auth.cancel_operation(owner_id, operation_id)


async def revoke(owner_id: str, profile_id: int) -> dict[str, Any] | None:
    lock = provider_auth.get_scope_lock(owner_id, profile_id)
    async with lock:
        if await provider_store.get_profile(profile_id, owner_id) is None:
            return None
        provider_auth.invalidate_scope(owner_id, profile_id)
        await provider_store.delete_credential(
            profile_id, owner_id, invalidate=False,
        )
    return {
        "profile_id": profile_id,
        "revoked": True,
        "remote_revoked": False,
    }


def _expiry_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return timestamp
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        timestamp = float(text)
    except ValueError:
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return timestamp


def credential_requires_refresh(credential: Mapping[str, Any] | None) -> bool:
    if not isinstance(credential, Mapping):
        return False
    if credential.get("type") == "api_key":
        return False
    expires_at = credential.get(
        "expires",
        credential.get("expires_at", credential.get("expiresAt")),
    )
    timestamp = _expiry_timestamp(expires_at)
    return timestamp is not None and timestamp <= time.time()


async def refresh_for_coach(
    owner_id: str,
    profile_id: int,
) -> dict[str, Any]:
    operation = await refresh(owner_id, profile_id)
    completed = await provider_auth.wait_for_operation(
        owner_id, operation["operation_id"], timeout_s=30.0,
    )
    if completed is None:
        raise provider_auth.ProviderAuthError(
            "operation_interrupted", "Provider credential refresh 已中断",
        )
    return completed
