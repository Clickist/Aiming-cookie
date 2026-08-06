"""Owner/profile-scoped Provider profiles and completed credentials.

Public profile DTOs never contain credential material.  Runtime snapshots are
constructed only for the local Pi bridge and carry the generic Pi credential
object, not a public/API representation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Mapping
from urllib.parse import urlsplit

from .db import get_conn

_CUSTOM_KINDS = {"custom_openai_compatible", "custom_anthropic_compatible"}
_KINDS = {"builtin", *_CUSTOM_KINDS}
_WRITE_LOCK = asyncio.Lock()
_PROFILE_COLUMNS = (
    "id, owner_id, name, provider_id, kind, base_url, model_id, api_key, "
    "context_window, max_tokens, is_default, created_at, updated_at"
)
_CREDENTIAL_COLUMNS = (
    "id, owner_id, profile_id, credential_type, credential_json, revision, "
    "needs_reauth, created_at, updated_at"
)


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
    """Store Anthropic-compatible custom endpoints as service roots for Pi."""
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
        # Kept only as an input/compatibility field.  The DB column is never
        # used as the credential source after the v10 migration.
        "api_key": api_key,
        "is_default": bool(value.get("is_default", False)),
    }


def normalize_credential(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate/copy a generic Pi credential without applying a provider allow-list."""
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


def _credential_from_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        value = json.loads(row["credential_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("stored provider credential is invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError("stored provider credential is not an object")
    return value


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
    # Built-ins may resolve ambient auth in Pi; absence of a local credential is
    # therefore a readiness question, not an invalid profile.
    return profile.get("kind") == "builtin"


def _public_profile(
    row: Mapping[str, Any], credential_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    credential = _credential_from_row(credential_row)
    needs_reauth = bool(credential_row and credential_row["needs_reauth"])
    credential_configured = credential is not None
    # Store reads cannot determine ambient readiness. Fail closed here and let
    # the explicit status endpoint ask Pi whether environment/file auth works.
    configured = credential_configured and not needs_reauth and runtime_profile_configured({
        "provider_id": row["provider_id"],
        "provider_name": row["name"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model_id": row["model_id"],
        "context_window": row["context_window"],
        "max_tokens": row["max_tokens"],
        "credential": credential,
    })
    has_api_key = bool(
        isinstance(credential, Mapping)
        and credential.get("type") == "api_key"
        and isinstance(credential.get("key"), str)
        and credential["key"]
    )
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "provider_id": row["provider_id"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model_id": row["model_id"],
        "context_window": row["context_window"],
        "max_tokens": row["max_tokens"],
        "is_default": bool(row["is_default"]),
        "configured": configured,
        "credential_configured": credential_configured,
        "has_api_key": has_api_key,
        "status": "needs_reauth" if needs_reauth else ("ready" if configured else "unconfigured"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _runtime_profile(
    row: Mapping[str, Any], credential_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    credential = _credential_from_row(credential_row)
    runtime = {
        "profile_id": int(row["id"]),
        "provider_id": row["provider_id"],
        "provider_name": row["name"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model_id": row["model_id"],
        "context_window": row["context_window"],
        "max_tokens": row["max_tokens"],
    }
    if credential is not None:
        runtime["credential"] = credential
    if credential_row is not None and credential_row["needs_reauth"]:
        runtime["credential_needs_reauth"] = True
    return runtime


async def _get_row(profile_id: int, owner_id: str):
    conn = await get_conn()
    cur = await conn.execute(
        f"SELECT {_PROFILE_COLUMNS} FROM provider_profiles WHERE id=? AND owner_id=?",
        (profile_id, owner_id),
    )
    return await cur.fetchone()


async def _get_credential_row(
    profile_id: int, owner_id: str, conn=None,
):
    conn = conn or await get_conn()
    cur = await conn.execute(
        f"SELECT {_CREDENTIAL_COLUMNS} FROM provider_credentials "
        "WHERE profile_id=? AND owner_id=?",
        (profile_id, owner_id),
    )
    return await cur.fetchone()


async def _get_profile_bundle(profile_id: int, owner_id: str):
    row = await _get_row(profile_id, owner_id)
    if row is None:
        return None, None
    return row, await _get_credential_row(profile_id, owner_id)


async def create_profile(owner_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    profile = _normalize_profile(value, require_custom_api_key=True)
    credential = (
        {"type": "api_key", "key": profile["api_key"]}
        if profile.get("api_key") else None
    )
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            if profile["is_default"]:
                await conn.execute(
                    "UPDATE provider_profiles SET is_default=0, "
                    "updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND is_default=1",
                    (owner_id,),
                )
            cur = await conn.execute(
                "INSERT INTO provider_profiles(owner_id, name, provider_id, kind, "
                "base_url, model_id, context_window, max_tokens, api_key, is_default) VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, ?) "
                "RETURNING id",
                (
                    owner_id, profile["name"], profile["provider_id"], profile["kind"],
                    profile["base_url"], profile["model_id"], profile["context_window"], profile["max_tokens"],
                    1 if profile["is_default"] else 0,
                ),
            )
            row = await cur.fetchone()
            profile_id = int(row["id"])
            if credential is not None:
                normalized = normalize_credential(credential)
                await conn.execute(
                    "INSERT INTO provider_credentials(owner_id, profile_id, credential_type, "
                    "credential_json, revision, needs_reauth) VALUES(?, ?, ?, ?, 1, 0)",
                    (
                        owner_id, profile_id, normalized["type"],
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    ),
                )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    created = await get_profile(profile_id, owner_id)
    assert created is not None
    return created


async def get_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    row, credential_row = await _get_profile_bundle(profile_id, owner_id)
    return _public_profile(row, credential_row) if row is not None else None


async def get_runtime_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    row, credential_row = await _get_profile_bundle(profile_id, owner_id)
    return _runtime_profile(row, credential_row) if row is not None else None


async def get_default_runtime_profile(owner_id: str) -> dict[str, Any] | None:
    conn = await get_conn()
    cur = await conn.execute(
        f"SELECT {_PROFILE_COLUMNS} FROM provider_profiles "
        "WHERE owner_id=? AND is_default=1 LIMIT 1",
        (owner_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _runtime_profile(row, await _get_credential_row(int(row["id"]), owner_id, conn))


async def get_default_profile(owner_id: str) -> dict[str, Any] | None:
    conn = await get_conn()
    cur = await conn.execute(
        f"SELECT {_PROFILE_COLUMNS} FROM provider_profiles "
        "WHERE owner_id=? AND is_default=1 LIMIT 1",
        (owner_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _public_profile(row, await _get_credential_row(int(row["id"]), owner_id, conn))


async def list_profiles(owner_id: str) -> list[dict[str, Any]]:
    conn = await get_conn()
    cur = await conn.execute(
        f"SELECT {_PROFILE_COLUMNS} FROM provider_profiles WHERE owner_id=? "
        "ORDER BY is_default DESC, id ASC",
        (owner_id,),
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(_public_profile(row, await _get_credential_row(int(row["id"]), owner_id, conn)))
    return out


async def get_credential(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    return _credential_from_row(await _get_credential_row(profile_id, owner_id))


async def get_credential_record(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    row = await _get_credential_row(profile_id, owner_id)
    if row is None:
        return None
    return {
        "credential": _credential_from_row(row),
        "revision": int(row["revision"]),
        "needs_reauth": bool(row["needs_reauth"]),
        "updated_at": row["updated_at"],
    }


def _invalidate_auth_scope(owner_id: str, profile_id: int) -> None:
    # Import lazily to keep provider_store usable during module initialization.
    try:
        from . import provider_auth
        provider_auth.invalidate_scope(owner_id, profile_id)
    except ImportError:
        pass


async def _replace_credential(
    profile_id: int,
    owner_id: str,
    credential: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
    invalidate: bool = True,
) -> bool:
    normalized = normalize_credential(credential)
    if invalidate:
        _invalidate_auth_scope(owner_id, profile_id)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            profile_row = await _get_row(profile_id, owner_id)
            if profile_row is None:
                await conn.execute("ROLLBACK")
                return False
            current = await _get_credential_row(profile_id, owner_id, conn)
            current_revision = int(current["revision"]) if current is not None else 0
            if expected_revision is not None and current_revision != expected_revision:
                await conn.execute("ROLLBACK")
                return False
            next_revision = current_revision + 1
            if current is None:
                await conn.execute(
                    "INSERT INTO provider_credentials(owner_id, profile_id, credential_type, "
                    "credential_json, revision, needs_reauth) VALUES(?, ?, ?, ?, ?, 0)",
                    (
                        owner_id, profile_id, normalized["type"],
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True), next_revision,
                    ),
                )
            else:
                await conn.execute(
                    "UPDATE provider_credentials SET credential_type=?, credential_json=?, "
                    "revision=?, needs_reauth=0, updated_at=CURRENT_TIMESTAMP "
                    "WHERE owner_id=? AND profile_id=?",
                    (
                        normalized["type"],
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                        next_revision, owner_id, profile_id,
                    ),
                )
            await conn.execute(
                "UPDATE provider_profiles SET api_key=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            await conn.commit()
            return True
        except Exception:
            await conn.execute("ROLLBACK")
            raise


async def replace_credential(
    profile_id: int,
    owner_id: str,
    credential: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
    invalidate: bool = True,
) -> dict[str, Any] | None:
    if not await _replace_credential(
        profile_id, owner_id, credential,
        expected_revision=expected_revision, invalidate=invalidate,
    ):
        return None
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
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            profile_row = await _get_row(profile_id, owner_id)
            if profile_row is None:
                await conn.execute("ROLLBACK")
                return None
            current = await _get_credential_row(profile_id, owner_id, conn)
            current_revision = int(current["revision"]) if current is not None else 0
            if expected_revision is not None and current_revision != expected_revision:
                await conn.execute("ROLLBACK")
                return None
            await conn.execute(
                "DELETE FROM provider_credentials WHERE profile_id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            await conn.execute(
                "UPDATE provider_profiles SET api_key=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_profile(profile_id, owner_id)


async def mark_needs_reauth(
    profile_id: int,
    owner_id: str,
    value: bool = True,
    *,
    expected_revision: int | None = None,
) -> bool:
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            sql = (
                "UPDATE provider_credentials SET needs_reauth=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE profile_id=? AND owner_id=?"
            )
            parameters: tuple[Any, ...] = (1 if value else 0, profile_id, owner_id)
            if expected_revision is not None:
                sql += " AND revision=?"
                parameters += (expected_revision,)
            cur = await conn.execute(sql, parameters)
            await conn.commit()
            return cur.rowcount == 1
        except Exception:
            await conn.execute("ROLLBACK")
            raise


async def update_profile(
    profile_id: int,
    owner_id: str,
    changes: Mapping[str, Any],
) -> dict[str, Any] | None:
    conn = await get_conn()
    credential_supplied = "api_key" in changes
    if credential_supplied:
        _invalidate_auth_scope(owner_id, profile_id)
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _get_row(profile_id, owner_id)
            if row is None:
                await conn.execute("ROLLBACK")
                return None
            merged = {
                "name": row["name"], "provider_id": row["provider_id"],
                "kind": row["kind"], "base_url": row["base_url"],
                "model_id": row["model_id"], "context_window": row["context_window"],
                "max_tokens": row["max_tokens"], "is_default": bool(row["is_default"]),
                "api_key": changes.get("api_key") if credential_supplied else None,
            }
            merged.update({key: value for key, value in changes.items() if key != "api_key"})
            profile = _normalize_profile(merged)
            model_changed = profile["model_id"] != row["model_id"]
            if model_changed and not {"context_window", "max_tokens"} <= changes.keys():
                profile["context_window"] = None
                profile["max_tokens"] = None
            identity_changed = any(
                profile[field] != row[field]
                for field in ("provider_id", "kind", "base_url")
            )
            credential_change = credential_supplied or identity_changed
            if profile["is_default"]:
                await conn.execute(
                    "UPDATE provider_profiles SET is_default=0, "
                    "updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND id<>? AND is_default=1",
                    (owner_id, profile_id),
                )
            await conn.execute(
                "UPDATE provider_profiles SET name=?, provider_id=?, kind=?, base_url=?, "
                "model_id=?, context_window=?, max_tokens=?, api_key=NULL, is_default=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND owner_id=?",
                (
                    profile["name"], profile["provider_id"], profile["kind"],
                    profile["base_url"], profile["model_id"], profile["context_window"], profile["max_tokens"],
                    1 if profile["is_default"] else 0, profile_id, owner_id,
                ),
            )
            if credential_change:
                current = await _get_credential_row(profile_id, owner_id, conn)
                if profile["api_key"]:
                    normalized = normalize_credential({"type": "api_key", "key": profile["api_key"]})
                    next_revision = int(current["revision"]) + 1 if current is not None else 1
                    if current is None:
                        await conn.execute(
                            "INSERT INTO provider_credentials(owner_id, profile_id, credential_type, "
                            "credential_json, revision, needs_reauth) VALUES(?, ?, ?, ?, ?, 0)",
                            (
                                owner_id, profile_id, normalized["type"],
                                json.dumps(normalized, ensure_ascii=False, sort_keys=True), next_revision,
                            ),
                        )
                    else:
                        await conn.execute(
                            "UPDATE provider_credentials SET credential_type=?, credential_json=?, "
                            "revision=?, needs_reauth=0, updated_at=CURRENT_TIMESTAMP "
                            "WHERE profile_id=? AND owner_id=?",
                            (
                                normalized["type"],
                                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                                next_revision, profile_id, owner_id,
                            ),
                        )
                else:
                    await conn.execute(
                        "DELETE FROM provider_credentials WHERE profile_id=? AND owner_id=?",
                        (profile_id, owner_id),
                    )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_profile(profile_id, owner_id)


async def set_default_profile(profile_id: int, owner_id: str) -> dict[str, Any] | None:
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await _get_row(profile_id, owner_id)
            if row is None:
                await conn.execute("ROLLBACK")
                return None
            await conn.execute(
                "UPDATE provider_profiles SET is_default=0, updated_at=CURRENT_TIMESTAMP "
                "WHERE owner_id=? AND is_default=1",
                (owner_id,),
            )
            await conn.execute(
                "UPDATE provider_profiles SET is_default=1, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return await get_profile(profile_id, owner_id)


async def delete_profile(profile_id: int, owner_id: str) -> bool:
    _invalidate_auth_scope(owner_id, profile_id)
    conn = await get_conn()
    async with _WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute(
                "DELETE FROM provider_credentials WHERE profile_id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            cur = await conn.execute(
                "DELETE FROM provider_profiles WHERE id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            deleted = cur.rowcount == 1
            await conn.commit()
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return deleted
