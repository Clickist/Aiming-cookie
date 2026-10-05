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
    # [reverted→refixed 2026-10-05] 分析科学栈预热：numpy/scipy 懒加载的冻结
    # 首次导入在 LdrLoadDll 里楔 10-20 分钟且**全程持有 GIL**——事件循环/心跳/
    # 统计全部冻死（实机实测）。两条不可行路线都试过：后台线程预热=进程无痕
    # 死亡（五连杀）；不做预热=首局分析楔 10 分钟。可行解=**独立子进程**预热
    # （--telemetry-child 机制跑 prewarm_science.py）：楔在子进程里无感，OS
    # 缓存与安全软件扫描留热，父进程随后的真实导入秒级。不等待不看结果，
    # 子进程楔死/崩溃对服务零影响。
    try:
        _spawn_science_prewarm_child()
    except Exception as exc:  # noqa: BLE001 - 预热失败静默，首分析自付
        log.warning("science prewarm child spawn failed: %s", exc)
    yield


def _spawn_science_prewarm_child() -> None:
    """冻结环境用 --telemetry-child 自镜像子进程预热；开发环境直跑脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from .telemetry_capture_service import resolve_scripts_dir

    scripts_dir = resolve_scripts_dir()
    if scripts_dir is None:
        return
    script = scripts_dir / "prewarm_science.py"
    if getattr(sys, "frozen", False):
        argv = [sys.executable, "--telemetry-child", "prewarm_science.py"]
    else:
        argv = [sys.executable, str(script)]
    subprocess.Popen(
        argv,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


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
