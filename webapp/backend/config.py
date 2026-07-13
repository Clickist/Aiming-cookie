from __future__ import annotations

import os
import sys
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


def resolve_data_root() -> Path:
    """Resolve the managed desktop data directory, retaining DATA_ROOT for tests/dev."""
    override = os.environ.get("DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return (base / "Aiming Cookie").expanduser().resolve()


# Session workspaces: {DATA_ROOT}/sessions/{session_id}/. DATA_ROOT is an explicit
# dev/test override; production desktop defaults to platform App Data.
DATA_ROOT = resolve_data_root()
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# The desktop runtime owns this per-launch secret; it is never persisted or logged.
DESKTOP_LAUNCH_TOKEN = os.environ.get("AIMING_COOKIE_DESKTOP_TOKEN", "")
DESKTOP_LOCAL_PROFILE = "desktop-local"


def resolve_kovaak_install_dir() -> Path | None:
    """Resolve the local KovaaK installation used by Desktop auto-ingestion."""
    override = os.environ.get("KOVAAK_INSTALL_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer")
    return None


def resolve_kovaak_data_dirs() -> tuple[Path | None, Path | None]:
    """Return Stats and Performance directories without creating user-owned paths."""
    stats_override = os.environ.get("KOVAAK_STATS_DIR", "").strip()
    perf_override = os.environ.get("KOVAAK_PERFORMANCE_DIR", "").strip()
    install = resolve_kovaak_install_dir()
    stats = Path(stats_override).expanduser().resolve() if stats_override else None
    perf = Path(perf_override).expanduser().resolve() if perf_override else None
    if install is not None:
        stats = stats or install / "FPSAimTrainer" / "stats"
        perf = perf or install / "FPSAimTrainer" / "performances"
    return stats, perf


KOVAAK_STATS_DIR, KOVAAK_PERFORMANCE_DIR = resolve_kovaak_data_dirs()
KOVAAK_WATCH_POLL_SECONDS = float(os.environ.get("KOVAAK_WATCH_POLL_SECONDS", "1.0"))

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")
LLM_DAILY_BUDGET_CNY = float(os.environ.get("LLM_DAILY_BUDGET_CNY", "1.0"))
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB
MAX_CSV_BYTES = 10 * 1024 * 1024    # 10MB(KovaaK Stats CSV 实际 <1MB,留余量)
UPLOAD_CHUNK_SIZE = int(os.environ.get("UPLOAD_CHUNK_SIZE", str(1024 * 1024)))  # 1MB
MIN_FREE_DISK_BYTES = int(
    os.environ.get("MIN_FREE_DISK_BYTES", str(500 * 1024 * 1024))
)  # refuse upload when DATA_ROOT volume free space is below this

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
# Preview/prod: TRUST_PROXY_USER=1 behind VPN/SSO reverse proxy; only proxy user headers count.
TRUST_PROXY_USER = os.environ.get("TRUST_PROXY_USER", "0").strip()