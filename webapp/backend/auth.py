"""Request-scoped user identity (dev X-User-Id vs trusted reverse-proxy headers)."""

from __future__ import annotations

import hmac
import re

from fastapi import HTTPException, Request

from . import config

# Same rules as legacy routes: path-safe user id for workspace paths.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# TRUST_PROXY_USER=1: prefer X-Forwarded-User (nginx/Envoy), then Remote-User (Apache mod_auth).
_PROXY_USER_HEADER_ORDER = ("X-Forwarded-User", "Remote-User")


def _trust_proxy_enabled() -> bool:
    return config.TRUST_PROXY_USER.lower() in ("1", "true", "yes")


def _proxy_user_from_headers(request: Request) -> str | None:
    for name in _PROXY_USER_HEADER_ORDER:
        value = request.headers.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def get_request_user_id(request: Request) -> str:
    """Single entry point for API user identity.

    Default (TRUST_PROXY_USER=0): ``X-User-Id`` header, default ``dev``.
    Trust mode (TRUST_PROXY_USER=1): only ``X-Forwarded-User`` then ``Remote-User``;
    client ``X-User-Id`` is ignored. Missing proxy user → 401.
    """
    if _trust_proxy_enabled():
        user_id = _proxy_user_from_headers(request)
        if not user_id:
            raise HTTPException(
                401,
                "未认证：预览/生产环境需由 VPN/SSO 反代注入用户头"
                "（X-Forwarded-User 或 Remote-User）",
            )
    else:
        user_id = request.headers.get("X-User-Id", "dev")

    if not _USER_ID_RE.match(user_id):
        raise HTTPException(400, "用户标识含非法字符(只允许字母数字_-)")

    return user_id


def require_desktop_token(request: Request) -> None:
    """Validate the launch-scoped desktop token without logging or persisting it."""
    expected = config.DESKTOP_LAUNCH_TOKEN
    provided = request.headers.get("X-Aiming-Cookie-Desktop-Token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "桌面运行时令牌无效或缺失")
