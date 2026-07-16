from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from webapp.backend import config, db, queue
from webapp.backend.config import MAX_CSV_BYTES, UPLOAD_CHUNK_SIZE
from webapp.backend.workspace import (
    InvalidSessionId,
    UploadSizeExceeded,
    remove_session_workspace,
    session_dir,
    stream_upload_to_path,
)


def test_session_dir_under_data_root(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="ac_ws_"))
    monkeypatch.setattr(config, "DATA_ROOT", root)
    p = session_dir(42)
    assert p == (root / "sessions" / "42").resolve()
    assert p.relative_to(root.resolve())


def test_session_dir_rejects_path_traversal(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="ac_ws_"))
    monkeypatch.setattr(config, "DATA_ROOT", root)
    with pytest.raises(InvalidSessionId):
        session_dir("../etc")
    with pytest.raises(InvalidSessionId):
        session_dir("1/../2")
    with pytest.raises(InvalidSessionId):
        session_dir("../../sessions/1")


def test_remove_session_workspace_only_rmtree_session_subdir(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="ac_ws_"))
    monkeypatch.setattr(config, "DATA_ROOT", root)
    sessions_root = root / "sessions"
    sessions_root.mkdir(parents=True)
    sid = "99"
    ws = session_dir(sid)
    ws.mkdir(parents=True)
    (ws / "video.mp4").write_bytes(b"v")
    sibling = sessions_root / "keep"
    sibling.mkdir()
    (sibling / "note.txt").write_text("stay", encoding="utf-8")
    other = root / "outside_sessions.txt"
    other.write_text("stay", encoding="utf-8")

    remove_session_workspace(sid)

    assert not ws.exists()
    assert sibling.exists()
    assert (sibling / "note.txt").read_text(encoding="utf-8") == "stay"
    assert other.exists()


def test_remove_session_workspace_absent_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "managed")

    assert remove_session_workspace(404) is False
    assert remove_session_workspace(404) is False


@pytest.mark.asyncio
async def test_delete_session_removes_workspace_directory(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="ac_ws_del_"))
    monkeypatch.setattr(config, "DATA_ROOT", root)
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    conn = await db.get_conn()
    await conn.execute("UPDATE sessions SET status='failed' WHERE id=?", (sid,))
    await conn.commit()

    ws = session_dir(sid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "artifact.json").write_text("{}", encoding="utf-8")

    await queue.delete_session(sid, "u1")

    assert not ws.exists()
    assert await queue.get_session(sid) is None
    tombstone = await (
        await conn.execute(
            "SELECT analysis_session_id FROM analysis_deletion_tombstones "
            "WHERE analysis_session_id=?",
            (sid,),
        )
    ).fetchone()
    assert tombstone is None


class _ChunkedFakeUpload:
    def __init__(self, data: bytes, *, chunk_size: int | None = None) -> None:
        self._data = data
        self._pos = 0
        self._chunk_size = chunk_size or len(data)
        self.read_calls: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        if self._pos >= len(self._data):
            return b""
        end = min(self._pos + size, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk


@pytest.mark.asyncio
async def test_stream_upload_reads_in_chunks_not_whole_file(tmp_path):
    payload = b"a" * (UPLOAD_CHUNK_SIZE * 3 + 17)
    upload = _ChunkedFakeUpload(payload)
    dest = tmp_path / "video.mp4"
    nbytes = await stream_upload_to_path(
        upload,
        dest,
        max_bytes=MAX_CSV_BYTES,
        field="video",
    )
    assert nbytes == len(payload)
    assert dest.read_bytes() == payload
    assert upload.read_calls
    assert all(n == UPLOAD_CHUNK_SIZE for n in upload.read_calls[:-1])
    assert upload.read_calls[-1] == UPLOAD_CHUNK_SIZE


@pytest.mark.asyncio
async def test_stream_upload_oversize_removes_partial_file(tmp_path):
    limit = 100
    upload = _ChunkedFakeUpload(b"x" * (limit + 1), chunk_size=40)
    dest = tmp_path / "big.csv"
    with pytest.raises(UploadSizeExceeded):
        await stream_upload_to_path(upload, dest, max_bytes=limit, field="csv")
    assert not dest.exists()
