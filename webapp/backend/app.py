from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, queue, storage_path_rewrite
from .auth import require_desktop_token
from .health import router as health_router
from .routes import router


log = logging.getLogger(__name__)

# B0 locale 管道：请求级 X-Locale 头（与 X-User-Id 惯例同构），中间件解析后
# 存入 request.state.locale，投影层（read_models/contracts/queue）后续按需读取。
# 默认恒 zh-CN；非法值回落（与前端 lib/i18n normalizeLocale 同一口径）。
SUPPORTED_LOCALES = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"


def normalize_locale_header(value: str | None) -> str:
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时执行 reconciliation（DB schema 初始化已移除）。"""
    # 存储迁移后的旧根绝对路径一次性重写（db 列 + sessions/*.json 字段，
    # 各自独立标志）。必须在对外服务前完成；fail-soft，结果只进日志。
    rewrite_status = await asyncio.to_thread(
        storage_path_rewrite.run_startup_path_rewrite
    )
    if rewrite_status:
        log.info("storage path rewrite: %s", rewrite_status)
    reconciliation = await queue.reconcile_analysis_deletions()
    error_code = (
        "workspace_cleanup_failed"
        if reconciliation["failed"]
        else "none"
    )
    log.info(
        "analysis deletion reconciliation processed=%s cleaned=%s failed=%s code=%s",
        reconciliation["processed"],
        reconciliation["cleaned"],
        reconciliation["failed"],
        error_code,
    )
    stale_uploads = await queue.reconcile_stale_uploads()
    log.info(
        "stale upload reconciliation processed=%s cleaned=%s failed=%s",
        stale_uploads["processed"],
        stale_uploads["cleaned"],
        stale_uploads["failed"],
    )
    # [reverted 2026-10-05] 科学栈开机预热（后台线程 import numpy/scipy）在冻结
    # 运行时上实机五连杀（进程无痕消失，死点均在 scipy 导入；无预热则只是首局
    # 分析楔 ~10 分钟后自愈）。冻结环境的安全首导方案未定（子进程预热待研），
    # 先回退，代价=每次启动后第一局分析慢（已知限制，Coach 阶段话术如实）。
    yield


app = FastAPI(title="Aiming Cookie API", lifespan=lifespan)


@app.middleware("http")
async def store_request_locale(request: Request, call_next):
    """B0 纯管道：只存不消费（默认 zh-CN，行为不变）。"""
    request.state.locale = normalize_locale_header(request.headers.get("x-locale"))
    return await call_next(request)


@app.middleware("http")
async def require_desktop_api_token(request: Request, call_next):
    """Protect every API route when running under the desktop shell."""
    if request.url.path.startswith("/api/") and config.DESKTOP_LAUNCH_TOKEN:
        try:
            require_desktop_token(request)
        except HTTPException as error:
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers=error.headers,
            )
    return await call_next(request)


# CORS：Next.js dev (3000) → FastAPI (8000) 跨端口必须开。
# 生产用 CORS_ORIGINS env 限制具体域名（逗号分隔）。
_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(router)
