from __future__ import annotations

import os
import shutil
from pathlib import Path
from . import config


class InvalidSessionId(ValueError):
    """session_id is not a safe workspace identifier."""


class WorkspacePathError(ValueError):
    """Resolved workspace path is outside DATA_ROOT."""


def _safe_session_id(session_id: int | str) -> str:
    sid = str(session_id).strip()
    if not sid or sid in (".", ".."):
        raise InvalidSessionId(f"invalid session_id: {session_id!r}")
    if "/" in sid or "\\" in sid or "\0" in sid:
        raise InvalidSessionId(f"invalid session_id: {session_id!r}")
    return sid


def session_dir(session_id: int | str) -> Path:
    """Return {DATA_ROOT}/sessions/{session_id}/; resolved path must stay under DATA_ROOT."""
    sid = _safe_session_id(session_id)
    root = config.DATA_ROOT.resolve()
    sessions_root = (root / "sessions").resolve()
    path = (sessions_root / sid).resolve()
    try:
        path.relative_to(sessions_root)
    except ValueError as exc:
        raise WorkspacePathError(
            f"session workspace escapes sessions root: {path}",
        ) from exc
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            f"session workspace escapes DATA_ROOT: {path}",
        ) from exc
    return path


def remove_session_workspace(session_id: int | str) -> bool:
    """Remove the session workspace directory if it exists. Returns True if removed."""
    path = session_dir(session_id)
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


_WORKSPACE_SIZE_LEDGER: dict[str, tuple[int, int]] = {}


def _measure_workspace(path: Path) -> int:
    """Pure read/scan: sum managed workspace file sizes.

    多链接文件（nlink>1）不计入：run 视频被硬链接成工作区别名后，字节实际
    归属 runs/{id}/，两边各算一次会让存储页虚报、并让自动清理按虚高的用量
    提前触发。共享字节只在归属侧计一次。
    """
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                stat_result = (Path(root) / name).stat()
            except OSError:
                continue
            if stat_result.st_nlink > 1:
                continue
            total += stat_result.st_size
    return total


def workspace_size_bytes(session_id: int | str) -> int:
    """Return managed workspace size; the resolved directory must remain under sessions/.

    记账：以工作区目录 mtime 为变更信号，内容未变时直接复用上次测量值，
    /api/storage 不再每次 os.walk 全部工作区。目录 mtime 对文件的创建/
    删除/重命名敏感；上传结束时的 tmp→正式名 replace 会触发重算。
    """
    path = session_dir(session_id)
    if not path.is_dir():
        return 0
    try:
        dir_mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        return 0
    key = str(path)
    recorded = _WORKSPACE_SIZE_LEDGER.get(key)
    if recorded is not None and recorded[0] == dir_mtime_ns:
        return recorded[1]
    total = _measure_workspace(path)
    _WORKSPACE_SIZE_LEDGER[key] = (dir_mtime_ns, total)
    return total


def copy_path_to_path(source: Path, destination: Path) -> int:
    """Stream a local source into the managed workspace without loading it into memory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=config.UPLOAD_CHUNK_SIZE)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination.stat().st_size
