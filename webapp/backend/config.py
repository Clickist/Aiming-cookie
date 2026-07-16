from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath

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


_STEAM_APP_ID = "824270"
_STEAM_REGISTRY_VALUES = (
    ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
    (
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\WOW6432Node\Valve\Steam",
        "InstallPath",
    ),
)
_VDF_TOKEN_RE = re.compile(r'\s*(?:"((?:\\.|[^"\\])*)"|([{}]))', re.DOTALL)


def _decode_vdf_string(value: str) -> str | None:
    decoded: list[str] = []
    index = 0
    escapes = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    while index < len(value):
        character = value[index]
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        index += 1
        if index >= len(value) or value[index] not in escapes:
            return None
        decoded.append(escapes[value[index]])
        index += 1
    return "".join(decoded)


def _tokenize_vdf(text: str) -> list[tuple[str, str]] | None:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if not text[position:].strip():
            break
        match = _VDF_TOKEN_RE.match(text, position)
        if match is None:
            return None
        quoted, brace = match.groups()
        if brace is not None:
            tokens.append(("brace", brace))
        else:
            decoded = _decode_vdf_string(quoted)
            if decoded is None:
                return None
            tokens.append(("string", decoded))
        position = match.end()
    return tokens


def _parse_vdf_object(
    tokens: list[tuple[str, str]],
    position: int,
    *,
    expects_closing_brace: bool,
) -> tuple[dict[str, object], int] | None:
    parsed: dict[str, object] = {}
    while position < len(tokens):
        kind, key = tokens[position]
        if kind == "brace":
            if key == "}" and expects_closing_brace:
                return parsed, position + 1
            return None
        if key in parsed:
            return None
        position += 1
        if position >= len(tokens):
            return None
        value_kind, value = tokens[position]
        if value_kind == "string":
            parsed[key] = value
            position += 1
            continue
        if value != "{":
            return None
        child = _parse_vdf_object(
            tokens,
            position + 1,
            expects_closing_brace=True,
        )
        if child is None:
            return None
        parsed[key], position = child
    if expects_closing_brace:
        return None
    return parsed, position


def _read_vdf_root(path: Path, root_name: str) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    tokens = _tokenize_vdf(text)
    if tokens is None:
        return None
    result = _parse_vdf_object(tokens, 0, expects_closing_brace=False)
    if result is None:
        return None
    parsed, position = result
    root = parsed.get(root_name)
    if position != len(tokens) or len(parsed) != 1 or not isinstance(root, dict):
        return None
    return root


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _windows_steam_roots() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []

    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(value: object, *, require_existing: bool = False) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        try:
            root = Path(value).expanduser().resolve()
            if require_existing and not root.is_dir():
                return
            key = _path_key(root)
        except (OSError, RuntimeError):
            return
        if key not in seen:
            seen.add(key)
            roots.append(root)

    for hive_name, subkey, value_name in _STEAM_REGISTRY_VALUES:
        try:
            hive = getattr(winreg, hive_name)
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                value, value_type = winreg.QueryValueEx(key, value_name)
            valid_types = {
                getattr(winreg, "REG_SZ", None),
                getattr(winreg, "REG_EXPAND_SZ", None),
            }
            if value_type in valid_types:
                add_root(value)
        except (AttributeError, OSError):
            continue

    for environment_name in ("ProgramFiles(x86)", "ProgramFiles"):
        program_files = os.environ.get(environment_name)
        if program_files:
            add_root(str(Path(program_files) / "Steam"), require_existing=True)
    return roots


def _steam_libraries(root: Path) -> list[Path] | None:
    parsed = _read_vdf_root(root / "steamapps" / "libraryfolders.vdf", "libraryfolders")
    if parsed is None:
        return None
    libraries = [root]
    for index, entry in parsed.items():
        if not index.isdigit() or not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str) and path.strip():
            libraries.append(Path(path).expanduser())
    return libraries


def _safe_install_directory_name(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    candidate = PureWindowsPath(value)
    if (
        candidate.drive
        or candidate.root
        or len(candidate.parts) != 1
        or candidate.name in {".", ".."}
    ):
        return None
    return value


def _discover_kovaak_install_dir() -> Path | None:
    libraries: list[Path] = []
    seen_libraries: set[str] = set()
    for root in _windows_steam_roots():
        root_libraries = _steam_libraries(root)
        if root_libraries is None:
            continue
        for library in root_libraries:
            try:
                resolved = library.resolve()
                key = _path_key(resolved)
            except (OSError, RuntimeError):
                continue
            if key not in seen_libraries:
                seen_libraries.add(key)
                libraries.append(resolved)

    installs: list[Path] = []
    seen_installs: set[str] = set()
    for library in libraries:
        manifest = _read_vdf_root(
            library / "steamapps" / f"appmanifest_{_STEAM_APP_ID}.acf",
            "AppState",
        )
        if manifest is None or manifest.get("appid") != _STEAM_APP_ID:
            continue
        install_name = _safe_install_directory_name(manifest.get("installdir"))
        if install_name is None:
            continue
        install = library / "steamapps" / "common" / install_name
        try:
            resolved = install.resolve()
            if not resolved.is_dir():
                continue
            key = _path_key(resolved)
        except (OSError, RuntimeError):
            continue
        if key not in seen_installs:
            seen_installs.add(key)
            installs.append(resolved)
    return installs[0] if len(installs) == 1 else None


def resolve_kovaak_install_dir() -> Path | None:
    """Resolve the local KovaaK installation used by Desktop auto-ingestion."""
    override = os.environ.get("KOVAAK_INSTALL_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return _discover_kovaak_install_dir()
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

# Legacy compatibility inputs only. Active Coach/worker provider selection is
# owner-scoped local profile state and does not apply the fixed CNY budget.
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
