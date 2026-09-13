"""Owner-scoped Intro Session user profile persistence (JSON file backed).

The Intro Session collects four free-form answers plus an optional Steam
profile link.  Only the fixed whitelist below can ever be written here: the
store exposes no generic write path, so Coach/LLM output cannot persist
arbitrary fields into this file.

Stored at DATA_ROOT ``config/user-profile.json`` through ``file_store``'s
atomic write helper (same convention as ``peripheral_profile_store``).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import file_store

_PROFILE_PATH = "config/user-profile.json"

MAX_GAMES = 8
MAX_GAME_LENGTH = 40
MAX_EXPERIENCE_LENGTH = 300
MAX_SELF_ASSESSMENT_LENGTH = 500
MAX_GOAL_LENGTH = 300
MAX_STEAM_PROFILE_URL_LENGTH = 200

_ALLOWED_FIELDS = frozenset({
    "games", "experience", "self_assessment", "goal", "steam_profile_url",
})

_STEAM_ID_RE = re.compile(r"^[0-9]{17}$")
_STEAM_PROFILE_PATH_RE = re.compile(
    r"^/(profiles|id)/([A-Za-z0-9_-]{1,64})/?$",
)
_STEAM_HOSTS = frozenset({"steamcommunity.com", "www.steamcommunity.com"})


class InvalidUserProfile(ValueError):
    """Raised when an Intro Session profile contribution is not whitelisted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_profile() -> dict[str, Any]:
    return {
        "games": [],
        "experience": None,
        "self_assessment": None,
        "goal": None,
        "steam_profile_url": None,
        "updated_at": None,
    }


def _profile(row: dict[str, Any] | None) -> dict[str, Any]:
    """Project a stored row onto the fixed public schema (missing → defaults)."""
    if not isinstance(row, dict):
        return _empty_profile()
    games = row.get("games")
    return {
        "games": [item for item in games if isinstance(item, str)] if isinstance(games, list) else [],
        "experience": row.get("experience"),
        "self_assessment": row.get("self_assessment"),
        "goal": row.get("goal"),
        "steam_profile_url": row.get("steam_profile_url"),
        "updated_at": row.get("updated_at"),
    }


def _optional_text(value: Any, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidUserProfile(f"{field} must be a string or null")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise InvalidUserProfile(f"{field} must be at most {max_length} characters")
    return text


def _games(value: Any) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise InvalidUserProfile("games must be a list of strings")
    if len(value) > MAX_GAMES:
        raise InvalidUserProfile(f"games must contain at most {MAX_GAMES} entries")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise InvalidUserProfile("games entries must be strings")
        text = item.strip()
        if not text:
            raise InvalidUserProfile("games entries must be non-empty")
        if len(text) > MAX_GAME_LENGTH:
            raise InvalidUserProfile(
                f"games entries must be at most {MAX_GAME_LENGTH} characters"
            )
        out.append(text)
    return out


def normalize_steam_profile(raw: Any) -> str | None:
    """Return a canonical steamcommunity profile URL, or None when unusable.

    A bare 17-digit SteamID and any ``/profiles/<17-digit>`` homepage link
    collapse to ``https://steamcommunity.com/profiles/<id>``.  Vanity
    ``/id/<name>`` links keep their canonical form: resolving a vanity name to
    a 17-digit SteamID needs a network round trip, which this store must not
    do.  Invalid or oversized input yields None and never raises (the wireframe
    treats the Steam link as an optional, skippable extra).
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_STEAM_PROFILE_URL_LENGTH:
        return None
    if _STEAM_ID_RE.fullmatch(text):
        return f"https://steamcommunity.com/profiles/{text}"
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _STEAM_HOSTS:
        return None
    match = _STEAM_PROFILE_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    kind, identifier = match.groups()
    if kind == "profiles":
        if _STEAM_ID_RE.fullmatch(identifier) is None:
            return None
        return f"https://steamcommunity.com/profiles/{identifier}"
    return f"https://steamcommunity.com/id/{identifier}"


async def get_profile(owner_id: str) -> dict[str, Any]:
    data = file_store.read_json(_PROFILE_PATH)
    return _profile(data if isinstance(data, dict) else None)


async def update_profile(owner_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Partial update restricted to the fixed field whitelist."""
    if not isinstance(updates, dict):
        raise InvalidUserProfile("profile update must be an object")
    unknown = set(updates) - _ALLOWED_FIELDS
    if unknown:
        raise InvalidUserProfile(
            "profile update contains unsupported fields: " + ", ".join(sorted(unknown)),
        )
    if not updates:
        raise InvalidUserProfile("at least one field must be provided")

    validated: dict[str, Any] = {}
    if "games" in updates:
        validated["games"] = _games(updates["games"])
    if "experience" in updates:
        validated["experience"] = _optional_text(
            updates["experience"], "experience", MAX_EXPERIENCE_LENGTH,
        )
    if "self_assessment" in updates:
        validated["self_assessment"] = _optional_text(
            updates["self_assessment"], "self_assessment", MAX_SELF_ASSESSMENT_LENGTH,
        )
    if "goal" in updates:
        validated["goal"] = _optional_text(updates["goal"], "goal", MAX_GOAL_LENGTH)
    if "steam_profile_url" in updates:
        validated["steam_profile_url"] = normalize_steam_profile(
            updates["steam_profile_url"],
        )

    existing = file_store.read_json(_PROFILE_PATH)
    merged = _profile(existing)
    merged.update(validated)
    merged["updated_at"] = _utc_now()
    file_store.write_json(_PROFILE_PATH, merged)
    return merged


__all__ = [
    "InvalidUserProfile",
    "get_profile",
    "normalize_steam_profile",
    "update_profile",
]
