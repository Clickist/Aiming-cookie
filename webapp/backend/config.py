from __future__ import annotations

import os
import tempfile
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./aiming_cookie_dev.db"
)


def _sqlite_path(url: str) -> str:
    """sqlite+aiosqlite:///./path.db → ./path.db"""
    if ":///" in url:
        return url.split(":///", 1)[-1]
    if "://" in url:
        return url.split("://", 1)[-1]
    return url


DB_PATH = _sqlite_path(DATABASE_URL)

# 默认用系统 temp 目录(跨平台:Windows 下 /tmp 解析为 C:\tmp 非标准)。
VIDEO_TMP_DIR = Path(os.environ.get(
    "VIDEO_TMP_DIR", str(Path(tempfile.gettempdir()) / "aiming_cookie"),
))
VIDEO_TMP_DIR.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")
LLM_DAILY_BUDGET_CNY = float(os.environ.get("LLM_DAILY_BUDGET_CNY", "1.0"))
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB
MAX_CSV_BYTES = 10 * 1024 * 1024    # 10MB(KovaaK Stats CSV 实际 <1MB,留余量)
