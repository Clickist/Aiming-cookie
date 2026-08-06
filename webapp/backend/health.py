from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import config, db

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


async def check_db_ready() -> bool:
    try:
        conn = await db.get_conn()
        cursor = await conn.execute("SELECT 1")
        await cursor.fetchone()
        return True
    except Exception:
        log.exception("readyz: database check failed")
        return False


async def build_coach_runtime_status() -> dict[str, object]:
    """Coach UI / dev: sidecar readiness without failing like readyz."""
    sidecar_up = await check_sidecar_ready()
    if sidecar_up:
        return {
            "ok": True,
            "runtime": "pi",
            "sidecar": "up",
            "ready_for_fast_path": True,
            "message": "教练引擎已就绪",
        }
    return {
        "ok": True,
        "runtime": "pi",
        "sidecar": "down",
        "ready_for_fast_path": False,
        "message": (
            "教练引擎准备中；首次回复可能较慢"
            "（将连接常驻 sidecar 或走冷启动/较慢路径）"
        ),
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
