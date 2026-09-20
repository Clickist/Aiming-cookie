from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import config, file_store

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


async def check_db_ready() -> bool:
    """Data root is writable; the JSON file store has no connection to open."""
    try:
        return file_store.data_root_ready()
    except Exception:
        log.exception("readyz: data root check failed")
        return False


# B3 i18n：runtime-status message 双目录（code 是稳定枚举 ready/warming_up，
# 前端字典键 coachRuntime.status.*；message 保留供老客户端/缺码兜底直读）。
_RUNTIME_STATUS_MESSAGES = {
    "ready": {
        "zh-CN": "教练引擎已就绪",
        "en-US": "Coach engine is ready",
    },
    "warming_up": {
        "zh-CN": (
            "教练引擎准备中；首次回复可能较慢"
            "（将连接常驻 sidecar 或走冷启动/较慢路径）"
        ),
        "en-US": (
            "Coach engine is warming up; the first reply may be slower "
            "(it will connect to the resident sidecar or take a cold-start path)"
        ),
    },
}


async def build_coach_runtime_status(locale: str = "zh-CN") -> dict[str, object]:
    """Coach UI / dev: sidecar readiness without failing like readyz.

    B1：code 是稳定枚举（ready/warming_up）；B3 起 message 按 locale 出。
    """
    sidecar_up = await check_sidecar_ready()
    code = "ready" if sidecar_up else "warming_up"
    entry = _RUNTIME_STATUS_MESSAGES[code]
    return {
        "ok": True,
        "runtime": "pi",
        "sidecar": "up" if sidecar_up else "down",
        "ready_for_fast_path": sidecar_up,
        "code": code,
        "message": entry.get(locale) or entry["zh-CN"],
    }


async def check_sidecar_ready() -> bool:
    url = (config.COACH_SIDECAR_URL or "").strip()
    if not url:
        return True
    health_url = f"{url.rstrip('/')}/healthz"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(health_url)
        if resp.status_code != 200:
            log.warning("readyz: sidecar healthz status %s", resp.status_code)
            return False
        data = resp.json()
        return data.get("ok") is True
    except Exception:
        log.exception("readyz: sidecar check failed for %s", health_url)
        return False


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz")
async def readyz():
    if not await check_db_ready():
        return JSONResponse(status_code=503, content={"ok": False, "db": False})

    # Pi is an optional Coach capability. Its failure must not block local
    # Analysis/History readiness. The separate runtime-status endpoint reports it.
    return {"ok": True}
