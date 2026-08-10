"""Steam identity redaction helpers for Coach command text processing.

These functions are pure text transformations with zero shared state and zero
dependencies on the rest of :mod:`coach_commands`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping


_STEAM_ID = re.compile(r"^[0-9]{17}$")
_STEAM_PROFILE_REF = re.compile(r"^steam_profile:([1-9][0-9]*)$")
_STEAM_PROFILE_URL_IN_TEXT = re.compile(
    r"https://steamcommunity\.com/profiles/([0-9]{17})/(?![A-Za-z0-9_/?#=&-])",
)
_STEAM_ID_IN_TEXT = re.compile(r"(?<![0-9])([0-9]{17})(?![0-9])")


def prepare_temporary_steam_profiles(value: object) -> tuple[str, dict[str, str]]:
    """Replace current-message Steam identities before they can be persisted."""
    if not isinstance(value, str):
        raise ValueError("Coach text is invalid")
    refs_by_steam_id: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        steam_id = match.group(1)
        profile_ref = refs_by_steam_id.setdefault(
            steam_id, f"steam_profile:{len(refs_by_steam_id) + 1}",
        )
        return profile_ref

    prepared = _STEAM_PROFILE_URL_IN_TEXT.sub(replace, value)
    prepared = _STEAM_ID_IN_TEXT.sub(replace, prepared)
    return prepared, {ref: steam_id for steam_id, ref in refs_by_steam_id.items()}


def redact_temporary_steam_profiles(value: object) -> str:
    """Remove historical Steam identities without making them callable this turn."""
    if not isinstance(value, str):
        return ""
    redacted = _STEAM_PROFILE_URL_IN_TEXT.sub("[Steam Profile hidden]", value)
    return _STEAM_ID_IN_TEXT.sub("[Steam Profile hidden]", redacted)


def contains_temporary_steam_profile(value: object) -> bool:
    if isinstance(value, str):
        return bool(
            _STEAM_PROFILE_URL_IN_TEXT.search(value)
            or _STEAM_ID_IN_TEXT.search(value)
        )
    if isinstance(value, Mapping):
        return any(
            contains_temporary_steam_profile(key)
            or contains_temporary_steam_profile(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_temporary_steam_profile(item) for item in value)
    return False
