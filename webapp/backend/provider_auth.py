"""Thin Pi auth bridge plus process-local operation ownership/commit state.

Pi owns provider-specific login, device-code, callback and refresh behavior.
This module only maps those operations to an owner/profile, enforces one active
operation per scope, and commits the one-time completed credential to SQLite.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from .config import COACH_SIDECAR_URL

AUTHORIZE_TIMEOUT_MS = 15 * 60 * 1000
REFRESH_TIMEOUT_MS = 30 * 1000
SIDECAR_REQUEST_TIMEOUT_SECONDS = 30.0
_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}


class ProviderAuthError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _SidecarNotFound(ProviderAuthError):
    pass


@dataclass
class _Operation:
    operation_id: str
    sidecar_operation_id: str
    owner_id: str
    profile_id: int
    provider_id: str
    action: str
    mode: str | None
    generation: int
    expected_revision: int
    status: str = "running"
    prompts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: _iso_now())
    expires_at: str = ""
    deadline_monotonic: float = 0.0


_operations: dict[str, _Operation] = {}
_active_by_scope: dict[tuple[str, int], str] = {}
_generations: dict[tuple[str, int], int] = {}
_scope_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _iso_after(milliseconds: int) -> str:
    return (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(milliseconds=milliseconds)
    ).isoformat()


def _sidecar_remaining_ms(response: Mapping[str, Any]) -> int | None:
    for key, multiplier in (
        ("expires_in_ms", 1),
        ("timeout_ms", 1),
        ("expires_in", 1000),
    ):
        value = response.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value * multiplier)
    expires_at = response.get("expires_at")
    if isinstance(expires_at, (int, float)):
        timestamp = float(expires_at)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return max(0, int((timestamp - time.time()) * 1000))
    if isinstance(expires_at, str) and expires_at.strip():
        try:
            parsed = dt.datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return max(0, int((parsed.timestamp() - time.time()) * 1000))
    return None


def _apply_sidecar_expiry(operation: _Operation, response: Mapping[str, Any]) -> None:
    remaining_ms = _sidecar_remaining_ms(response)
    if remaining_ms is None:
        return
    candidate_deadline = time.monotonic() + remaining_ms / 1000
    if candidate_deadline < operation.deadline_monotonic:
        operation.deadline_monotonic = candidate_deadline
        operation.expires_at = _iso_after(remaining_ms)


def reset_in_memory_state() -> None:
    """Test/process-restart helper; no auth operation state is durable by design."""
    _operations.clear()
    _active_by_scope.clear()
    _generations.clear()
    _scope_locks.clear()


def get_scope_lock(owner_id: str, profile_id: int) -> asyncio.Lock:
    return _scope_locks.setdefault((owner_id, profile_id), asyncio.Lock())


def invalidate_scope(owner_id: str, profile_id: int) -> int:
    """Advance generation and interrupt any operation superseded by local intent."""
    scope = (owner_id, profile_id)
    generation = _generations.get(scope, 0) + 1
    _generations[scope] = generation
    operation_id = _active_by_scope.pop(scope, None)
    operation = _operations.get(operation_id) if operation_id else None
    if operation is not None and operation.status not in _TERMINAL:
        operation.status = "interrupted"
        operation.prompts = []
        operation.error = {
            "code": "interrupted",
            "message": "认证操作已被新的凭据操作中断，请重试",
            "retryable": True,
        }
    return generation


def _sanitize_public(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_public(item) for item in value]
    if isinstance(value, Mapping):
        blocked = {
            "credential", "api_key", "key", "access", "refresh",
            "access_token", "refresh_token", "token", "secret",
            "client_secret", "authorization", "value",
        }
        return {
            str(key): _sanitize_public(item)
            for key, item in value.items()
            if str(key).lower() not in blocked
        }
    return value


def _public_operation(operation: _Operation) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation_id": operation.operation_id,
        "profile_id": operation.profile_id,
        "action": operation.action,
        "mode": operation.mode,
        "status": operation.status,
        "prompts": _sanitize_public(operation.prompts),
        "events": _sanitize_public(operation.events),
        "error": _sanitize_public(operation.error),
        "created_at": operation.created_at,
        "expires_at": operation.expires_at,
    }
    return result


def debug_operation_state() -> list[dict[str, Any]]:
    """Non-secret diagnostic projection used by tests; input values are never stored."""
    return [_public_operation(operation) for operation in _operations.values()]


async def _request_json(
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    url = f"{COACH_SIDECAR_URL.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.request(method, url, json=payload)
    except httpx.HTTPError as error:
        raise ProviderAuthError(
            "auth_unavailable",
            f"Pi 认证服务暂不可用（{type(error).__name__}）",
        ) from error
    if response.status_code == 404:
        raise _SidecarNotFound(
            "operation_interrupted",
            "认证操作已丢失，请重试",
            status_code=404,
        )
    if response.status_code >= 400:
        raise ProviderAuthError(
            "auth_failed",
            f"Pi 认证服务返回 HTTP {response.status_code}",
            status_code=502,
        )
    try:
        body = response.json()
    except ValueError as error:
        raise ProviderAuthError("invalid_response", "Pi 认证响应不是 JSON") from error
    if not isinstance(body, dict):
        raise ProviderAuthError("invalid_response", "Pi 认证响应必须是对象")
    return body


async def fetch_capabilities() -> dict[str, Any]:
    return _sanitize_public(await _request_json(
        "GET", "/v1/auth/capabilities", timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
    ))


def _sidecar_operation_id(response: Mapping[str, Any]) -> str:
    operation_id = response.get("operation_id", response.get("id"))
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ProviderAuthError("invalid_response", "Pi 认证操作缺少 id")
    return operation_id.strip()


def _map_status(value: Any) -> str:
    status = str(value or "pending").strip().lower()
    if status in {"pending", "running", "in_progress", "started"}:
        return "running"
    if status in {"awaiting_input", "needs_input", "input_required"}:
        return "awaiting_input"
    if status in {"completed", "complete", "success", "succeeded", "ready"}:
        return "succeeded"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"timeout", "timed_out", "expired"}:
        return "timed_out"
    if status in {"interrupted", "not_found"}:
        return "interrupted"
    if status in {"failed", "error", "needs_reauth"}:
        return "failed"
    return "pending"


def _extract_prompts(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("prompts", response.get("interactions", []))
    if not isinstance(raw, list):
        return []
    return [
        _sanitize_public(dict(item))
        for item in raw
        if isinstance(item, Mapping)
    ]


def _extract_events(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("events", [])
    if not isinstance(raw, list):
        return []
    return [
        _sanitize_public(dict(item))
        for item in raw
        if isinstance(item, Mapping)
    ]


def _safe_error(response: Mapping[str, Any], fallback_code: str) -> dict[str, Any]:
    raw = response.get("error")
    if isinstance(raw, Mapping):
        code = str(raw.get("code") or fallback_code)
        message = str(raw.get("message") or "认证操作失败")
        retryable = bool(raw.get("retryable", True))
    else:
        code = fallback_code
        message = "认证操作失败，请重试"
        retryable = True
    return _sanitize_public({"code": code, "message": message, "retryable": retryable})


async def _begin_operation(
    owner_id: str,
    profile_id: int,
    *,
    action: str,
    mode: str | None,
) -> tuple[_Operation, dict[str, Any]]:
    from . import provider_store

    scope = (owner_id, profile_id)
    lock = get_scope_lock(owner_id, profile_id)
    async with lock:
        active_id = _active_by_scope.get(scope)
        active = _operations.get(active_id) if active_id else None
        if active is not None and active.status not in _TERMINAL:
            raise ProviderAuthError(
                "operation_conflict",
                "该 Provider profile 已有认证操作进行中",
                status_code=409,
            )
        # Read metadata and credential only after acquiring the same scope lock
        # used by profile mutations. Otherwise an authorize call can capture an
        # old provider_id while a concurrent update is still holding the lock.
        runtime_profile = await provider_store.get_runtime_profile(profile_id, owner_id)
        if runtime_profile is None:
            raise ProviderAuthError(
                "profile_not_found", "Provider profile 不存在", status_code=404,
            )
        refresh_credential: Mapping[str, Any] | None = None
        if action == "refresh":
            credential = runtime_profile.get("credential")
            if not isinstance(credential, Mapping):
                raise ProviderAuthError(
                    "credential_missing", "Provider credential 不存在", status_code=409,
                )
            if credential.get("type") != "oauth":
                raise ProviderAuthError(
                    "credential_not_refreshable",
                    "只有 OAuth credential 支持 refresh",
                    status_code=409,
                )
            refresh_credential = credential
        record = await provider_store.get_credential_record(profile_id, owner_id)
        expected_revision = int(record["revision"]) if record else 0
        timeout_ms = AUTHORIZE_TIMEOUT_MS if action == "login" else REFRESH_TIMEOUT_MS
        operation = _Operation(
            operation_id=str(uuid.uuid4()),
            sidecar_operation_id="",
            owner_id=owner_id,
            profile_id=profile_id,
            provider_id=str(runtime_profile["provider_id"]),
            action=action,
            mode=mode,
            generation=_generations.get(scope, 0),
            expected_revision=expected_revision,
            expires_at=_iso_after(timeout_ms),
            deadline_monotonic=time.monotonic() + timeout_ms / 1000,
        )
        _operations[operation.operation_id] = operation
        _active_by_scope[scope] = operation.operation_id

    payload: dict[str, Any] = {
        "action": action,
        "provider_id": operation.provider_id,
        "timeout_ms": timeout_ms,
    }
    if action == "login":
        payload["mode"] = mode
    else:
        assert refresh_credential is not None
        payload["credential"] = dict(refresh_credential)

    try:
        response = await _request_json(
            "POST", "/v1/auth/operations", payload=payload,
            timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception:
        if operation.status not in _TERMINAL:
            operation.status = "interrupted"
            operation.error = {
                "code": "auth_unavailable",
                "message": "认证操作未启动，请重试",
                "retryable": True,
            }
        _active_by_scope.pop(scope, None)
        _operations.pop(operation.operation_id, None)
        raise

    operation.sidecar_operation_id = _sidecar_operation_id(response)
    cancel_stale_sidecar = False
    mapped = _map_status(response.get("status"))
    async with lock:
        if (
            operation.status in _TERMINAL
            or _generations.get(scope, 0) != operation.generation
        ):
            operation.status = "interrupted"
            operation.prompts = []
            operation.error = {
                "code": "interrupted",
                "message": "认证操作已被新的 Provider 设置中断，请重试",
                "retryable": True,
            }
            _active_by_scope.pop(scope, None)
            cancel_stale_sidecar = True
        else:
            _apply_sidecar_expiry(operation, response)
            operation.events = _extract_events(response)
            if mapped == "awaiting_input":
                operation.status = mapped
                operation.prompts = _extract_prompts(response)
            elif mapped == "running":
                operation.status = "running"
    if cancel_stale_sidecar:
        try:
            await _request_json(
                "DELETE",
                f"/v1/auth/operations/{operation.sidecar_operation_id}",
                timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
            )
        except ProviderAuthError:
            pass
    elif mapped not in {"awaiting_input", "running"}:
        await _finish_operation(operation, response, mapped)
    return operation, response


async def start_authorize(owner_id: str, profile_id: int, mode: str) -> dict[str, Any]:
    if not isinstance(mode, str) or mode not in {"api_key", "oauth"}:
        raise ProviderAuthError(
            "invalid_mode", "mode 必须是 api_key 或 oauth", status_code=422,
        )
    operation, _ = await _begin_operation(
        owner_id, profile_id, action="login", mode=mode.strip(),
    )
    return _public_operation(operation)


async def start_refresh(owner_id: str, profile_id: int) -> dict[str, Any]:
    operation, _ = await _begin_operation(
        owner_id, profile_id, action="refresh", mode=None,
    )
    return _public_operation(operation)


async def _finish_operation(
    operation: _Operation, response: Mapping[str, Any], mapped_status: str,
) -> None:
    from . import provider_store

    scope = (operation.owner_id, operation.profile_id)
    lock = get_scope_lock(operation.owner_id, operation.profile_id)
    async with lock:
        if operation.status in _TERMINAL:
            return
        operation.prompts = []
    if mapped_status == "succeeded":
        try:
            result = await _request_json(
                "POST",
                f"/v1/auth/operations/{operation.sidecar_operation_id}/take-result",
                timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
            )
        except _SidecarNotFound:
            mapped_status = "interrupted"
            result = {}
        credential = result.get("credential") if isinstance(result, Mapping) else None
        if mapped_status == "succeeded" and not isinstance(credential, Mapping):
            mapped_status = "failed"
            operation.error = {
                "code": "credential_missing",
                "message": "认证完成但未返回 credential，请重试",
                "retryable": True,
            }
        if mapped_status == "succeeded":
            async with lock:
                if operation.status in _TERMINAL:
                    return
                if _generations.get(scope, 0) != operation.generation:
                    mapped_status = "interrupted"
                else:
                    committed = await provider_store.replace_credential(
                        operation.profile_id,
                        operation.owner_id,
                        credential,
                        expected_revision=operation.expected_revision,
                        invalidate=False,
                    )
                    if committed is None:
                        mapped_status = "interrupted"
                if mapped_status == "succeeded":
                    operation.status = "succeeded"
                    _active_by_scope.pop(scope, None)
                    return
    if mapped_status == "failed":
        operation.error = operation.error or _safe_error(response, "auth_failed")
        runtime_profile = await provider_store.get_runtime_profile(
            operation.profile_id, operation.owner_id,
        )
        if runtime_profile is not None:
            from .provider_store import redact_provider_secrets

            operation.error["message"] = redact_provider_secrets(
                str(operation.error.get("message", "认证操作失败")),
                runtime_profile,
            )
        code = str(operation.error.get("code", ""))
        if (
            str(response.get("status", "")).lower() == "needs_reauth"
            or code in {"needs_reauth", "invalid_grant", "credential_expired"}
        ):
            await provider_store.mark_needs_reauth(
                operation.profile_id,
                operation.owner_id,
                True,
                expected_revision=operation.expected_revision,
            )
    if mapped_status == "interrupted":
        operation.error = {
            "code": "interrupted",
            "message": "认证操作已中断，请重试",
            "retryable": True,
        }
    async with lock:
        if operation.status in _TERMINAL:
            return
        operation.status = mapped_status
        _active_by_scope.pop(scope, None)


async def get_operation(owner_id: str, operation_id: str) -> dict[str, Any] | None:
    operation = _operations.get(operation_id)
    if operation is None or operation.owner_id != owner_id:
        return None
    if operation.status in _TERMINAL:
        return _public_operation(operation)
    if time.monotonic() >= operation.deadline_monotonic:
        try:
            await _request_json(
                "DELETE", f"/v1/auth/operations/{operation.sidecar_operation_id}",
                timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
            )
        except ProviderAuthError:
            pass
        await _finish_operation(operation, {}, "timed_out")
        return _public_operation(operation)
    try:
        response = await _request_json(
            "GET", f"/v1/auth/operations/{operation.sidecar_operation_id}",
            timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
        )
    except _SidecarNotFound:
        await _finish_operation(operation, {}, "interrupted")
        return _public_operation(operation)
    _apply_sidecar_expiry(operation, response)
    operation.events = _extract_events(response)
    mapped = _map_status(response.get("status"))
    if mapped == "awaiting_input":
        operation.status = mapped
        operation.prompts = _extract_prompts(response)
    elif mapped == "running":
        operation.status = "running"
        operation.prompts = []
    else:
        await _finish_operation(operation, response, mapped)
    return _public_operation(operation)


async def submit_input(
    owner_id: str,
    operation_id: str,
    prompt_id: str,
    value: str,
) -> dict[str, Any] | None:
    operation = _operations.get(operation_id)
    if operation is None or operation.owner_id != owner_id:
        return None
    if operation.status in _TERMINAL:
        return _public_operation(operation)
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        raise ProviderAuthError("invalid_prompt", "prompt_id 不能为空", status_code=422)
    if not isinstance(value, str):
        raise ProviderAuthError("invalid_input", "value 必须是字符串", status_code=422)
    await _request_json(
        "POST", f"/v1/auth/operations/{operation.sidecar_operation_id}/input",
        payload={"prompt_id": prompt_id.strip(), "value": value},
        timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
    )
    # Deliberately do not retain or echo `value`.
    return await get_operation(owner_id, operation_id)


async def cancel_operation(owner_id: str, operation_id: str) -> dict[str, Any] | None:
    operation = _operations.get(operation_id)
    if operation is None or operation.owner_id != owner_id:
        return None
    scope = (operation.owner_id, operation.profile_id)
    lock = get_scope_lock(operation.owner_id, operation.profile_id)
    async with lock:
        if operation.status in _TERMINAL:
            return _public_operation(operation)
        operation.status = "cancelled"
        operation.prompts = []
        _active_by_scope.pop(scope, None)
    try:
        await _request_json(
            "DELETE", f"/v1/auth/operations/{operation.sidecar_operation_id}",
            timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
        )
    except _SidecarNotFound:
        async with lock:
            if operation.status == "cancelled":
                operation.status = "interrupted"
                operation.error = {
                    "code": "interrupted",
                    "message": "认证操作已中断，请重试",
                    "retryable": True,
                }
    return _public_operation(operation)


async def wait_for_operation(
    owner_id: str,
    operation_id: str,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while True:
        operation = await get_operation(owner_id, operation_id)
        if operation is None or operation["status"] in _TERMINAL:
            return operation
        if time.monotonic() >= deadline:
            internal = _operations.get(operation_id)
            if internal is None:
                return None
            try:
                await _request_json(
                    "DELETE", f"/v1/auth/operations/{internal.sidecar_operation_id}",
                    timeout_s=SIDECAR_REQUEST_TIMEOUT_SECONDS,
                )
            except ProviderAuthError:
                pass
            await _finish_operation(internal, {}, "timed_out")
            return _public_operation(internal)
        await asyncio.sleep(0.05)
