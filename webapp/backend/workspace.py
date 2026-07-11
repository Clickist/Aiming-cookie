from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

from . import config


class UploadReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class InvalidSessionId(ValueError):
    """session_id is not a safe workspace identifier."""


class WorkspacePathError(ValueError):
    """Resolved workspace path is outside DATA_ROOT."""


class UploadSizeExceeded(ValueError):
    """Streamed upload exceeded configured byte limit."""

    def __init__(self, *, field: str, limit: int, received: int) -> None:
        self.field = field
        self.limit = limit
        self.received = received
        super().__init__(
            f"{field} exceeds limit ({received} > {limit})",
        )


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


def workspace_size_bytes(session_id: int | str) -> int:
    """Return managed workspace size; the resolved directory must remain under sessions/."""
    path = session_dir(session_id)
    if not path.is_dir():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
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


async def stream_upload_to_path(
    upload: UploadReader,
    dest: Path,
    *,
    max_bytes: int,
    field: str = "file",
) -> int:
    """Write upload body to dest in chunks; never loads the whole file into memory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    chunk_size = config.UPLOAD_CHUNK_SIZE
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadSizeExceeded(
                        field=field,
                        limit=max_bytes,
                        received=total,
                    )
                out.write(chunk)
    except Exception:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return total