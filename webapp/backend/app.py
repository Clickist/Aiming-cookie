from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .routes import router
from .db import init_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生产启动时 init schema。测试用 fixture init,不依赖 lifespan。"""
    await init_schema()
    yield


app = FastAPI(title="Aiming Cookie API", lifespan=lifespan)
app.include_router(router)
