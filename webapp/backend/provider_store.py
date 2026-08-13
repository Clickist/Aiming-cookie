"""Owner-scoped Provider profiles stored as JSON files.

The provider config is a single ``config/provider.json`` document because the
desktop product has exactly one owner and one selected (default) profile.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from . import config, file_store

_CUSTOM_KINDS = {"custom_openai_compatible", "custom_anthropic_compatible"}
_KINDS = {"builtin", *_CUSTOM_KINDS}
_WRITE_LOCK = asyncio.Lock()
_PROVIDER_PATH = "config/provider.json"


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer or null")
    return value


def normalize_custom_provider_base_url(kind: str, base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if kind == "custom_anthropic_compatible" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _normalize_profile(
    value: Mapping[str, Any],
    *,
    require_custom_api_key: bool = False,
) -> dict[str, Any]:
    kind = _required_text(value.get("kind"), "kind")
    if kind not in _KINDS:
        raise ValueError("invalid provider kind")
    base_url = value.get("base_url")
    if isinstance(base_url, str):
        base_url = base_url.strip() or None
    elif base_url is not None:
        raise ValueError("base_url must be a string or null")
    if kind in _CUSTOM_KINDS:
        if not base_url:
            raise ValueError("base_url is required for custom providers")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("base_url must be a valid HTTP(S) URL")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("base_url must not include credentials")
        base_url = normalize_custom_provider_base_url(kind, base_url)
    api_key = value.get("api_key")
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    elif api_key is not None:
        raise ValueError("api_key must be a string or null")
    if kind in _CUSTOM_KINDS and require_custom_api_key and not api_key:
        raise ValueError("api_key is required for custom providers")
    provider_id = value.get("provider_id")
    if isinstance(provider_id, str):
        provider_id = provider_id.strip() or None
    elif provider_id is not None:
        raise ValueError("provider_id must be a string or null")
    if kind == "builtin" and not provider_id:
        raise ValueError("provider_id is required for builtin providers")
    if kind in _CUSTOM_KINDS and not provider_id:
        provider_id = f"custom:{uuid.uuid4().hex}"

    return {
        "name": _required_text(value.get("name"), "name"),
        "provider_id": provider_id,
        "kind": kind,
        "base_url": base_url,
        "model_id": _required_text(value.get("model_id"), "model_id"),
        "context_window": _optional_positive_int(value.get("context_window"), "context_window"),
        "max_tokens": _optional_positive_int(value.get("max_tokens"), "max_tokens"),
        "api_key": api_key,
        "is_default": bool(value.get("is_default", False)),
    }


def normalize_credential(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("credential must be an object")
    credential = dict(value)
    credential_type = credential.get("type")
    if not isinstance(credential_type, str) or not credential_type.strip():
        raise ValueError("credential.type is required")
    credential["type"] = credential_type.strip()
    if credential["type"] == "api_key":
        key = credential.get("key")
        if key is not None:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("credential.key must be a non-empty string when supplied")
            credential["key"] = key.strip()
        env = credential.get("env")
        if env is not None and (
            not isinstance(env, Mapping)
            or any(not isinstance(item, str) for item in env.values())
        ):
            raise ValueError("credential.env must contain string values")
    try:
        return json.loads(json.dumps(credential, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError("credential must be JSON serializable") from error


def runtime_profile_configured(profile: Mapping[str, Any] | None) -> bool:
    if not profile:
        return False
    required = ("provider_id", "provider_name", "kind", "model_id")
    if not all(
        isinstance(profile.get(field), str) and profile[field].strip()
        for field in required
    ):
        return False
    credential = profile.get("credential")
    if credential is None and isinstance(profile.get("api_key"), str):
        credential = {"type": "api_key", "key": profile["api_key"]}
    if profile.get("kind") in _CUSTOM_KINDS:
        if not isinstance(profile.get("base_url"), str) or not profile["base_url"].strip():
            return False
        return (
            isinstance(profile.get("context_window"), int)
            and not isinstance(profile.get("context_window"), bool)
            and profile["context_window"] > 0
            and isinstance(profile.get("max_tokens"), int)
            and not isinstance(profile.get("max_tokens"), bool)
            and profile["max_tokens"] > 0
            and isinstance(credential, Mapping)
            and any(
                isinstance(credential.get(field), str) and credential[field].strip()
                for field in ("key", "access", "access_token", "refresh", "refresh_token")
            )
        )
    return profile.get("kind") == "builtin"


# ---- Internal data model ----
# The on-disk document:
#   {
#     "next_id": 2,
#     "profiles": [ {id, name, provider_id, kind, base_url, model_id,
#                     context_window, max_tokens, is_default, created_at, updated_at} ],
#     "credentials": { "<profile_id>": {credential_type, credential_json, revision, needs_reauth, updated_at} }
#   }

def _load_doc() -> dict[str, Any]:
    data = file_store.read_json(_PROVIDER_PATH)
    if data is None:
        return {"next_id": 1, "profiles": [], "credentials": {}}
    return data  # type: ignore[return-value]


def _save_doc(doc: dict[str, Any]) -> None:
    file_store.write_json(_PROVIDER_PATH, doc)


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_profile(doc: dict, profile_id: int) -> dict | None:
    for p in doc.get("profiles", []):
        if p["id"] == profile_id:
            return p
    return None


def _credential_from_doc(doc: dict, profile_id: int) -> dict | None:
    raw = doc.get("credentials", {}).get(str(profile_id))
    if raw is None:
        return None
    try:
        value = json.loads(raw["credential_json"]) if isinstance(raw["credential_json"], str) else raw["credential_json"]
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("stored provider credential is invalid")
    if not isinstance(value, dict):
        raise RuntimeError("stored provider credential is not an object")
    return value


def _public_profile(
    profile: dict[str, Any],
    credential_record: dict[str, Any] | None,
) -> dict[str, Any]:
    credential = (
        json.loads(credential_record["credential_json"])
        if credential_record and isinstance(credential_record.get("credential_json"), str)
        else (credential_record.get("credential_json") if credential_record else None)
    )
    needs_reauth = bool(credential_record and credential_record.get("needs_reauth"))
    credential_configured = credential is not None
    configured = credential_configured and not needs_reauth and runtime_profile_configured({
        "provider_id": profile["provider_id"],
        "provider_name": profile["name"],
        "kind": profile["kind"],
        "base_url": profile.get("base_url"),
        "model_id": profile["model_id"],
        "context_window": profile.get("context_window"),
        "max_tokens": profile.get("max_tokens"),
        "credential": credential,
    })
    has_api_key = bool(
        isinstance(credential, Mapping)
        and credential.get("type") == "api_key"
        and isinstance(credential.get("key"), str)
        and credential["key"]
    )
    return {
        "id": int(profile["id"]),
        "name": profile["name"],
        "provider_id": profile["provider_id"],
        "kind": profile["kind"],
        "base_url": profile.get("base_url"),
        "model_id": profile["model_id"],
        "context_window": profile.get("context_window"),
        "max_tokens": profile.get("max_tokens"),
        "is_default": bool(profile.get("is_default")),
        "configured": configured,
        "credential_configured": credential_configured,
        "has_api_key": has_api_key,
        "status": "needs_reauth" if needs_reauth else ("ready" if configured else "unconfigured"),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
    }


def _runtime_profile(
    profile: dict[str, Any],
    credential_record: dict[str, Any] | None,
) -> dict[str, Any]:
    credential = (
        json.loads(credential_record["credential_json"])
        if credential_record and isinstance(credential_record.get("credential_json"), str)
        else (credential_record.get("credential_json") if credential_record else None)
    )
    runtime: dict[str, Any] = {
        "profile_id": int(profile["id"]),
        "provider_id": profile["provider_id"],
        "provider_name": profile["name"],
        "kind": profile["kind"],
        "base_url": profile.get("base_url"),
        "model_id": profile["model_id"],
        "context_window": profile.get("context_window"),
        "max_tokens": profile.get("max_tokens"),
    }
    if credential is not None:
        runtime["credential"] = credential
    if credential_record is not None and credential_record.get("needs_reauth"):
        runtime["credential_needs_reauth"] = True
    return runtime


def _invalidate_auth_scope(owner_id: str, profile_id: int) -> None:
    try:
        from . import provider_auth
        provider_auth.invalidate_scope(owner_id, profile_id)
    except ImportError:
        pass


async def create_profile(owner_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    profile = _normalize_profile(value, require_custom_api_key=True)
    credential = (
        {"type": "api_key", "key": profile["api_key"]}
        if profile.get("api_key") else None
    )
    async with _WRITE_LOCK:
        doc = _load_doc()
        now = _utc_now()
        if profile["is_default"]:
            for p in doc["profiles"]:
                p["is_default"] = False
        profile_id = doc["next_id"]
        doc["next_id"] = profile_id + 1
        record = {
            "id": profile_id,
            "name": profile["name"],
            "provider_id": profile["provider_id"],
            "kind": profile["kind"],
            "base_url": profile["base_url"],
            "model_id": profile["model_id"],
            "context_window": profile["context_window"],
            "max_tokens": profile["max_tokens"],
            "is_default": profile["is_default"],
            "created_at": now,
            "updated_at": now,
        }
        doc["profiles"].append(record)
        if credential is not None:
            normalized = normalize_credential(credential)
            doc["credentials"][str(profile_id)] = {
                "credential_type": normalized["type"],
                "credential_json": json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                "revision": 1,
                "needs_reauth": False,
                "updated_at": now,
            }
        _save_doc(doc)
    created = await get_profile(profile_id, owner_id)
    assert created is not None
    return created


async def get_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    profile = _find_profile(doc, profile_id)
    if profile is None:
        return None
    cred = doc.get("credentials", {}).get(str(profile_id))
    return _public_profile(profile, cred)


async def get_runtime_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    profile = _find_profile(doc, profile_id)
    if profile is None:
        return None
    cred = doc.get("credentials", {}).get(str(profile_id))
    return _runtime_profile(profile, cred)


async def get_default_runtime_profile(owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    for profile in doc["profiles"]:
        if profile.get("is_default"):
            cred = doc.get("credentials", {}).get(str(profile["id"]))
            return _runtime_profile(profile, cred)
    return None


async def get_default_profile(owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    for profile in doc["profiles"]:
        if profile.get("is_default"):
            cred = doc.get("credentials", {}).get(str(profile["id"]))
            return _public_profile(profile, cred)
    return None


async def list_profiles(owner_id: str) -> list[dict[str, Any]]:
    doc = _load_doc()
    out: list[dict[str, Any]] = []
    ordered = sorted(doc["profiles"], key=lambda p: (not p.get("is_default"), p["id"]))
    for profile in ordered:
        cred = doc.get("credentials", {}).get(str(profile["id"]))
        out.append(_public_profile(profile, cred))
    return out


async def get_credential(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    cred = doc.get("credentials", {}).get(str(profile_id))
    if cred is None:
        return None
    try:
        value = json.loads(cred["credential_json"]) if isinstance(cred["credential_json"], str) else cred["credential_json"]
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("stored provider credential is invalid")
    if not isinstance(value, dict):
        raise RuntimeError("stored provider credential is not an object")
    return value


async def get_credential_record(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    doc = _load_doc()
    cred = doc.get("credentials", {}).get(str(profile_id))
    if cred is None:
        return None
    return {
        "credential": _credential_from_doc(doc, profile_id),
        "revision": int(cred.get("revision", 1)),
        "needs_reauth": bool(cred.get("needs_reauth")),
        "updated_at": cred.get("updated_at"),
    }


async def replace_credential(
    profile_id: int,
    owner_id: str,
    credential: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
    invalidate: bool = True,
) -> dict[str, Any] | None:
    normalized = normalize_credential(credential)
    if invalidate:
        _invalidate_auth_scope(owner_id, profile_id)
    async with _WRITE_LOCK:
        doc = _load_doc()
        profile = _find_profile(doc, profile_id)
        if profile is None:
            return None
        cred = doc.get("credentials", {}).get(str(profile_id))
        current_revision = int(cred["revision"]) if cred else 0
        if expected_revision is not None and current_revision != expected_revision:
            return None
        next_revision = current_revision + 1
        doc["credentials"][str(profile_id)] = {
            "credential_type": normalized["type"],
            "credential_json": json.dumps(normalized, ensure_ascii=False, sort_keys=True),
            "revision": next_revision,
            "needs_reauth": False,
            "updated_at": _utc_now(),
        }
        profile["updated_at"] = _utc_now()
        _save_doc(doc)
    return await get_profile(profile_id, owner_id)


async def delete_credential(
    profile_id: int,
    owner_id: str,
    *,
    expected_revision: int | None = None,
    invalidate: bool = True,
) -> dict[str, Any] | None:
    if invalidate:
        _invalidate_auth_scope(owner_id, profile_id)
    async with _WRITE_LOCK:
        doc = _load_doc()
        profile = _find_profile(doc, profile_id)
        if profile is None:
            return None
        cred = doc.get("credentials", {}).get(str(profile_id))
        current_revision = int(cred["revision"]) if cred else 0
        if expected_revision is not None and current_revision != expected_revision:
            return None
        doc.get("credentials", {}).pop(str(profile_id), None)
        profile["updated_at"] = _utc_now()
        _save_doc(doc)
    return await get_profile(profile_id, owner_id)


async def mark_needs_reauth(
    profile_id: int,
    owner_id: str,
    value: bool = True,
    *,
    expected_revision: int | None = None,
) -> bool:
    async with _WRITE_LOCK:
        doc = _load_doc()
        cred = doc.get("credentials", {}).get(str(profile_id))
        if cred is None:
            return False
        if expected_revision is not None and int(cred.get("revision", 1)) != expected_revision:
            return False
        cred["needs_reauth"] = bool(value)
        cred["updated_at"] = _utc_now()
        _save_doc(doc)
    return True


async def update_profile(
    profile_id: int,
    owner_id: str,
    changes: Mapping[str, Any],
) -> dict[str, Any] | None:
    credential_supplied = "api_key" in changes
    if credential_supplied:
        _invalidate_auth_scope(owner_id, profile_id)
    async with _WRITE_LOCK:
        doc = _load_doc()
        profile = _find_profile(doc, profile_id)
        if profile is None:
            return None
        merged = {
            "name": profile["name"], "provider_id": profile["provider_id"],
            "kind": profile["kind"], "base_url": profile.get("base_url"),
            "model_id": profile["model_id"], "context_window": profile.get("context_window"),
            "max_tokens": profile.get("max_tokens"), "is_default": bool(profile.get("is_default")),
            "api_key": changes.get("api_key") if credential_supplied else None,
        }
        merged.update({key: value for key, value in changes.items() if key != "api_key"})
        normalized = _normalize_profile(merged)
        model_changed = normalized["model_id"] != profile["model_id"]
        if model_changed and not {"context_window", "max_tokens"} <= changes.keys():
            normalized["context_window"] = None
            normalized["max_tokens"] = None
        identity_changed = any(
            normalized[field] != profile.get(field)
            for field in ("provider_id", "kind", "base_url")
        )
        credential_change = credential_supplied or identity_changed
        if normalized["is_default"]:
            for p in doc["profiles"]:
                if p["id"] != profile_id:
                    p["is_default"] = False
        profile.update({
            "name": normalized["name"], "provider_id": normalized["provider_id"],
            "kind": normalized["kind"], "base_url": normalized["base_url"],
            "model_id": normalized["model_id"],
            "context_window": normalized["context_window"],
            "max_tokens": normalized["max_tokens"],
            "is_default": normalized["is_default"],
            "updated_at": _utc_now(),
        })
        if credential_change:
            cred = doc.get("credentials", {}).get(str(profile_id))
            if normalized["api_key"]:
                norm_cred = normalize_credential({"type": "api_key", "key": normalized["api_key"]})
                next_revision = int(cred["revision"]) + 1 if cred else 1
                doc["credentials"][str(profile_id)] = {
                    "credential_type": norm_cred["type"],
                    "credential_json": json.dumps(norm_cred, ensure_ascii=False, sort_keys=True),
                    "revision": next_revision,
                    "needs_reauth": False,
                    "updated_at": _utc_now(),
                }
            else:
                doc.get("credentials", {}).pop(str(profile_id), None)
        _save_doc(doc)
    return await get_profile(profile_id, owner_id)


async def set_default_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    async with _WRITE_LOCK:
        doc = _load_doc()
        profile = _find_profile(doc, profile_id)
        if profile is None:
            return None
        for p in doc["profiles"]:
            p["is_default"] = (p["id"] == profile_id)
            p["updated_at"] = _utc_now()
        _save_doc(doc)
    return await get_profile(profile_id, owner_id)


async def delete_profile(profile_id: int, owner_id: str) -> bool:
    _invalidate_auth_scope(owner_id, profile_id)
    async with _WRITE_LOCK:
        doc = _load_doc()
        original_len = len(doc["profiles"])
        doc["profiles"] = [p for p in doc["profiles"] if p["id"] != profile_id]
        deleted = len(doc["profiles"]) < original_len
        doc.get("credentials", {}).pop(str(profile_id), None)
        if deleted:
            _save_doc(doc)
    return deleted


# ── Provider runtime: sidecar status / test / catalog ──────────────────
#
# These helpers talk to the local Pi sidecar for provider readiness. They
# were migrated out of the deleted ``coach_runtime`` module during the
# 2026-08-13 architecture rewrite; Provider management remains in Python.


class CoachRuntimeError(RuntimeError):
    """Pi coach-runtime failed (sidecar or subprocess) or returned ok=false."""


class CustomProviderModelDiscoveryError(RuntimeError):
    """A custom Provider could not return its model list."""


class ProviderUnconfiguredError(CoachRuntimeError):
    """The owner has no usable selected Provider profile."""


class ProviderReauthenticationRequiredError(CoachRuntimeError):
    """The selected Provider credential must be reauthenticated in Settings."""


def credential_secret_values(value: Any) -> list[str]:
    """Collect generic credential secrets for error/log redaction only."""
    secrets: list[str] = []

    def visit_credential(item: Any, key: str | None = None) -> None:
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit_credential(child, str(child_key).lower())
        elif isinstance(item, list):
            for child in item:
                visit_credential(child, key)
        elif isinstance(item, str) and item and key != "type":
            secrets.append(item)

    if isinstance(value, Mapping):
        credential = value.get("credential")
        if isinstance(credential, Mapping):
            visit_credential(credential)
        api_key = value.get("api_key")
        if isinstance(api_key, str) and api_key:
            secrets.append(api_key)
    return list(dict.fromkeys(secrets))


def redact_provider_secrets(value: str, profile: Mapping[str, Any] | None) -> str:
    redacted = value
    for secret in credential_secret_values(profile):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _normalize_runtime_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ProviderUnconfiguredError("Coach Provider 未配置")

    provider_id = profile.get("provider_id")
    provider_name = profile.get("provider_name")
    kind = profile.get("kind")
    model_id = profile.get("model_id")
    api_key = profile.get("api_key")
    credential = profile.get("credential")
    base_url = profile.get("base_url")
    context_window = profile.get("context_window")
    max_tokens = profile.get("max_tokens")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (provider_id, provider_name, kind, model_id)
    ):
        raise ProviderUnconfiguredError("Coach Provider 未配置完整元数据")
    if kind not in {"builtin", "custom_openai_compatible", "custom_anthropic_compatible"}:
        raise ProviderUnconfiguredError("Coach Provider 类型不受支持")
    if credential is None and isinstance(api_key, str) and api_key:
        credential = {"type": "api_key", "key": api_key}
    if credential is not None:
        try:
            credential = normalize_credential(credential)
        except ValueError as error:
            raise ProviderUnconfiguredError("Coach Provider credential 无效") from error
    if profile.get("credential_needs_reauth"):
        raise ProviderReauthenticationRequiredError()
    has_credential_secret = bool(credential_secret_values({"credential": credential}))
    if kind in {"custom_openai_compatible", "custom_anthropic_compatible"} and (
        not isinstance(base_url, str) or not base_url.strip()
        or not has_credential_secret
        or isinstance(context_window, bool) or not isinstance(context_window, int) or context_window <= 0
        or isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0
    ):
        raise ProviderUnconfiguredError("Coach Provider 未返回已验证的模型能力")
    if api_key is not None and not isinstance(api_key, str):
        raise ProviderUnconfiguredError("Coach Provider API key 无效")
    if isinstance(base_url, str) and kind in {"custom_openai_compatible", "custom_anthropic_compatible"}:
        base_url = normalize_custom_provider_base_url(kind, base_url)
    normalized = {
        "provider_id": provider_id.strip(),
        "provider_name": provider_name.strip(),
        "kind": kind,
        "base_url": base_url.strip().rstrip("/") if isinstance(base_url, str) else None,
        "model_id": model_id.strip(),
    }
    if kind in {"custom_openai_compatible", "custom_anthropic_compatible"}:
        normalized["context_window"] = context_window
        normalized["max_tokens"] = max_tokens
    if isinstance(credential, Mapping):
        normalized["credential"] = dict(credential)
    return normalized


def _provider_status_result(
    profile: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    explicit_test: bool,
) -> dict[str, Any]:
    allowed = {
        "ready", "unconfigured", "auth_expired", "needs_reauth", "model_unavailable",
        "connection_failed",
    }
    status = str(data.get("status") or ("ready" if data.get("ok") else "connection_failed"))
    if status not in allowed:
        status = "connection_failed"
    configured = status in {
        "ready", "auth_expired", "model_unavailable", "connection_failed",
    }
    if status == "ready":
        message = "Provider 连接成功" if explicit_test else "Coach Provider 已就绪"
    elif status == "auth_expired":
        message = "Provider OAuth credential 已过期"
    elif status == "needs_reauth":
        message = "Provider credential 需要重新认证"
    elif status == "model_unavailable":
        message = "当前 Provider model 不可用，请重新选择"
    elif explicit_test:
        message = "Provider 连接测试失败，请检查设置后重试"
    else:
        message = "Coach Provider 尚未就绪"
    raw_error = data.get("error")
    if isinstance(raw_error, Mapping) and isinstance(raw_error.get("message"), str):
        message = redact_provider_secrets(raw_error["message"], profile)
    return {"configured": configured, "status": status, "message": message}


async def fetch_provider_catalog(timeout_s: float = 5.0) -> Any:
    """Fetch the Pi catalog without applying an Aiming Cookie allow-list."""
    url = f"{config.COACH_SIDECAR_URL.rstrip('/')}/v0/providers/catalog"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
    except httpx.HTTPError as error:
        raise CoachRuntimeError(f"Pi provider catalog unavailable: {type(error).__name__}") from error
    if resp.status_code != 200:
        raise CoachRuntimeError(f"Pi provider catalog HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as error:
        raise CoachRuntimeError("Pi provider catalog response is not JSON") from error


async def fetch_custom_provider_models(
    protocol: str,
    base_url: str,
    api_key: str,
    timeout_s: float = 10.0,
) -> list[dict[str, int | str | None]]:
    """Read a custom Provider /models list without persisting its key."""
    timeout_s = max(0.001, min(float(timeout_s), 30.0))
    if protocol == "openai-completions":
        headers = {"Authorization": f"Bearer {api_key}"}
        model_list_path = "/models"
    elif protocol == "anthropic-messages":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        model_list_path = "/v1/models"
    else:
        raise CustomProviderModelDiscoveryError("custom Provider protocol is unsupported")
    if protocol == "anthropic-messages":
        base_url = normalize_custom_provider_base_url("custom_anthropic_compatible", base_url)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}{model_list_path}",
                headers=headers,
            )
    except httpx.HTTPError as error:
        raise CustomProviderModelDiscoveryError("custom Provider models unavailable") from error

    if response.status_code != 200:
        raise CustomProviderModelDiscoveryError("custom Provider models unavailable")
    try:
        body = response.json()
    except ValueError as error:
        raise CustomProviderModelDiscoveryError("custom Provider models response is not JSON") from error
    if not isinstance(body, Mapping) or not isinstance(body.get("data"), list):
        raise CustomProviderModelDiscoveryError("custom Provider models response is invalid")

    models: list[dict[str, int | str | None]] = []
    for item in body["data"]:
        model_id = item.get("id") if isinstance(item, Mapping) else item
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if model_id and all(model["model_id"] != model_id for model in models):
            context_window = item.get("context_window") if isinstance(item, Mapping) else None
            max_tokens = item.get("max_tokens") if isinstance(item, Mapping) else None
            models.append({
                "model_id": model_id,
                "context_window": (
                    context_window
                    if isinstance(context_window, int)
                    and not isinstance(context_window, bool)
                    and context_window > 0
                    else None
                ),
                "max_tokens": (
                    max_tokens
                    if isinstance(max_tokens, int)
                    and not isinstance(max_tokens, bool)
                    and max_tokens > 0
                    else None
                ),
            })
        if len(models) == 200:
            break
    return models


async def get_provider_profile_status(
    profile: Mapping[str, Any],
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Read readiness via /v1/profile/status; this endpoint sends no completion."""
    if not runtime_profile_configured(profile):
        missing_custom_capabilities = profile.get("kind") in {
            "custom_openai_compatible", "custom_anthropic_compatible",
        } and (
            isinstance(profile.get("context_window"), bool)
            or not isinstance(profile.get("context_window"), int)
            or profile["context_window"] <= 0
            or isinstance(profile.get("max_tokens"), bool)
            or not isinstance(profile.get("max_tokens"), int)
            or profile["max_tokens"] <= 0
        )
        return {
            "configured": False,
            "status": "needs_reauth" if profile.get("credential_needs_reauth") else "unconfigured",
            "message": (
                "Provider credential 需要重新认证"
                if profile.get("credential_needs_reauth")
                else "Provider 未返回已验证的 context_window 和 max_tokens"
                if missing_custom_capabilities
                else "Coach Provider 未配置完整凭据"
            ),
        }
    try:
        runtime_snapshot = _normalize_runtime_profile(profile)
    except ProviderUnconfiguredError:
        return {
            "configured": False,
            "status": "needs_reauth" if profile.get("credential_needs_reauth") else "unconfigured",
            "message": "Coach Provider 未配置完整凭据",
        }
    timeout_s = max(0.001, min(float(timeout_s), 30.0))
    url = f"{config.COACH_SIDECAR_URL.rstrip('/')}/v1/profile/status"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json={"profile": runtime_snapshot})
        data = resp.json()
        if not isinstance(data, Mapping):
            raise ValueError("response must be an object")
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("sidecar failure", request=resp.request, response=resp)
        return _provider_status_result(profile, data, explicit_test=False)
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "configured": True,
            "status": "connection_failed",
            "message": "Pi Provider 状态暂不可用",
        }


async def test_provider_profile(
    profile: Mapping[str, Any],
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Ask the local sidecar to test one runtime-only profile snapshot."""
    if not runtime_profile_configured(profile):
        missing_custom_capabilities = profile.get("kind") in {
            "custom_openai_compatible", "custom_anthropic_compatible",
        } and (
            isinstance(profile.get("context_window"), bool)
            or not isinstance(profile.get("context_window"), int)
            or profile["context_window"] <= 0
            or isinstance(profile.get("max_tokens"), bool)
            or not isinstance(profile.get("max_tokens"), int)
            or profile["max_tokens"] <= 0
        )
        return {
            "configured": False,
            "status": "unconfigured",
            "message": (
                "Provider 未返回已验证的 context_window 和 max_tokens"
                if missing_custom_capabilities
                else "Coach Provider 未配置完整凭据"
            ),
        }
    try:
        runtime_snapshot = _normalize_runtime_profile(profile)
    except ProviderUnconfiguredError:
        return {
            "configured": False,
            "status": "unconfigured",
            "message": "Coach Provider 未配置完整凭据",
        }
    timeout_s = max(0.001, min(float(timeout_s), 30.0))
    url = f"{config.COACH_SIDECAR_URL.rstrip('/')}/v0/providers/test"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                url,
                json={"profile": runtime_snapshot, "timeout_ms": int(timeout_s * 1000)},
            )
        if resp.status_code != 200:
            return {
                "configured": True,
                "status": "connection_failed",
                "message": "Provider 连接测试失败，请检查设置后重试",
            }
        data = resp.json()
        if not isinstance(data, Mapping):
            raise ValueError("response must be an object")
        return _provider_status_result(profile, data, explicit_test=True)
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "configured": True,
            "status": "connection_failed",
            "message": "Provider 连接测试失败，请检查设置后重试",
        }
