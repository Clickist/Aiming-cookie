"""B0 locale 管道测试：X-Locale 头 → request.state.locale（默认 zh-CN）。

B1 错误码化的关键接口断言也在此补充（detail.code 结构）。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from webapp.backend import queue
from webapp.backend.app import (
    DEFAULT_LOCALE,
    app,
    normalize_locale_header,
    store_request_locale,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en-US", "en-US"),
        ("zh-CN", "zh-CN"),
        (None, DEFAULT_LOCALE),
        ("", DEFAULT_LOCALE),
        ("fr-FR", DEFAULT_LOCALE),  # 非法值回落，与前端 normalizeLocale 同口径
        ("en-us", DEFAULT_LOCALE),  # 大小写敏感（与前端 === 严格匹配一致）
    ],
)
def test_normalize_locale_header(raw, expected):
    assert normalize_locale_header(raw) == expected


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
    }


async def _run_locale_middleware(headers: list[tuple[bytes, bytes]]) -> str | None:
    """直接执行中间件函数，返回 call_next 看到的 request.state.locale。"""
    seen: dict[str, str | None] = {}

    async def call_next(request: Request):
        seen["locale"] = getattr(request.state, "locale", None)
        return PlainTextResponse("ok")

    response = await store_request_locale(Request(_http_scope(headers)), call_next)
    assert response.status_code == 200
    return seen.get("locale")


@pytest.mark.asyncio
async def test_locale_middleware_reads_x_locale_header():
    assert await _run_locale_middleware([(b"x-locale", b"en-US")]) == "en-US"


@pytest.mark.asyncio
async def test_locale_middleware_defaults_to_zh_cn_without_header():
    assert await _run_locale_middleware([]) == DEFAULT_LOCALE


@pytest.mark.asyncio
async def test_locale_middleware_falls_back_on_invalid_header():
    assert await _run_locale_middleware([(b"x-locale", b"fr-FR")]) == DEFAULT_LOCALE


@pytest.mark.asyncio
async def test_requests_with_locale_header_still_serve_normally():
    """端到端冒烟：带/不带 X-Locale 都不影响现有路由（纯管道零行为变化）。"""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_locale", "X-Locale": "en-US"},
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_coded_error_detail_shape_on_unknown_session():
    """B1：detail 是 {code, message} 结构，message 保留 zh 原文兜底。"""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_locale"},
    ) as client:
        resp = await client.get("/api/sessions/999999")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "session.not_found"
    assert detail["message"] == "session 不存在"


@pytest.mark.asyncio
async def test_coded_error_detail_for_active_analysis():
    """B1：429 并发上限的稳定码（upload.analysis_active）。"""
    await queue.enqueue("u_active", "/a", "/a.csv")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "u_active"},
    ) as client:
        resp = await client.post(
            "/api/analyze",
            files={
                "video": ("v.mp4", b"x", "video/mp4"),
                "csv": ("s.csv", b"y", "text/csv"),
            },
            headers={"X-User-Id": "u_active"},
        )
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "upload.analysis_active"
