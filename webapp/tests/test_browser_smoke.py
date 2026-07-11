"""Browser E2E 骨架：Playwright 打开 API /healthz，确认页面不崩。

默认在无 Playwright 或未安装 Chromium 时 **skip**（不阻塞日常 pytest）。

安装与运行（可选）::

    pip install playwright
    playwright install chromium
    pytest webapp/tests/test_browser_smoke.py -v

若前端首页也要测，先起 dev server 再设::

    BROWSER_E2E_FRONTEND_URL=http://127.0.0.1:3000 pytest webapp/tests/test_browser_smoke.py -v -k frontend
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

pytest.importorskip(
    "playwright",
    reason="未安装 playwright：pip install playwright && playwright install chromium",
)

from playwright.async_api import async_playwright  # noqa: E402

from webapp.backend.app import app  # noqa: E402


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_uvicorn_thread(host: str, port: int) -> threading.Thread:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)

    def _run() -> None:
        server.run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail(f"uvicorn 未在 {host}:{port} 就绪")

    return thread


@pytest.mark.asyncio
async def test_browser_healthz_json_ok():
    """用真实浏览器请求本进程起的 /healthz。"""
    host = "127.0.0.1"
    port = _pick_free_port()
    _start_uvicorn_thread(host, port)
    url = f"http://{host}:{port}/healthz"

    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - env dependent
                pytest.skip(
                    f"Chromium 不可用（{exc!r}）。运行: playwright install chromium"
                )
            try:
                page = await browser.new_page()
                resp = await page.goto(url, wait_until="commit", timeout=15_000)
                assert resp is not None
                assert resp.ok
                body = await page.inner_text("body")
                data = json.loads(body)
                assert data == {"ok": True}
            finally:
                await browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "browserType.launch" in str(exc):
            pytest.skip(
                f"Playwright 浏览器未安装（{exc!r}）。运行: playwright install chromium"
            )
        raise


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("BROWSER_E2E_FRONTEND_URL", "").strip(),
    reason="设 BROWSER_E2E_FRONTEND_URL（如 http://127.0.0.1:3000）以测前端首页",
)
async def test_browser_frontend_home_loads():
    base = os.environ["BROWSER_E2E_FRONTEND_URL"].rstrip("/")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium 不可用: {exc!r}")
        try:
            page = await browser.new_page()
            resp = await page.goto(
                base + "/", wait_until="domcontentloaded", timeout=30_000
            )
            assert resp is not None
            assert resp.ok or resp.status < 500
        finally:
            await browser.close()