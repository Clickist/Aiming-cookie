from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .auth import require_desktop_token
from .health import router as health_router
from .routes import router
from .db import init_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生产启动时 init schema。测试用 fixture init,不依赖 lifespan。"""
    await init_schema()
    yield


app = FastAPI(title="Aiming Cookie API", lifespan=lifespan)


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