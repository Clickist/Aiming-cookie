from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol

from . import config

log = logging.getLogger(__name__)


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
    try:
        path = session_dir(session_id)
    except (InvalidSessionId, WorkspacePathError):
        log.warning(
            "remove_session_workspace skipped unsafe session_id=%r",
            session_id,
        )
        return False
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


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