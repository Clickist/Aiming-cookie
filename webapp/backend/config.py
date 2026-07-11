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

# Worker job lease / heartbeat (CV ~160s; TTL leaves headroom if heartbeat pauses)
LEASE_TTL_SECONDS = int(os.environ.get("LEASE_TTL_SECONDS", "300"))
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "30"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("DEFAULT_MAX_ATTEMPTS", "3"))

# Pi coach runtime (subprocess); Task 4 wires routes to COACH_RUNTIME.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COACH_RUNTIME = os.environ.get("COACH_RUNTIME", "pi").strip().lower()
if COACH_RUNTIME not in ("pi", "python"):
    COACH_RUNTIME = "pi"
COACH_RUNTIME_FALLBACK_PYTHON = os.environ.get("COACH_RUNTIME_FALLBACK_PYTHON", "1").strip()
PI_SOURCE_DIR = Path(
    os.environ.get("PI_SOURCE_DIR", str(_REPO_ROOT / "third_party" / "pi"))
).resolve()
COACH_RUNTIME_RUN_TURN = _REPO_ROOT / "webapp" / "coach-runtime" / "run-turn.ts"
COACH_RUNTIME_TSX_LOADER = PI_SOURCE_DIR / "node_modules" / "tsx" / "dist" / "loader.mjs"
COACH_RUNTIME_TIMEOUT_SECONDS = int(
    os.environ.get("COACH_RUNTIME_TIMEOUT_SECONDS", "120")
)
COACH_SIDECAR_PORT = int(os.environ.get("COACH_SIDECAR_PORT", "8765"))
COACH_SIDECAR_URL = os.environ.get(
    "COACH_SIDECAR_URL", f"http://127.0.0.1:{COACH_SIDECAR_PORT}"
).strip()
COACH_SIDECAR_FALLBACK_SUBPROCESS = os.environ.get(
    "COACH_SIDECAR_FALLBACK_SUBPROCESS", "1"
).strip()
