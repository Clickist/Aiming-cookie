"""storage_path_rewrite：存储迁移后库内绝对路径一次性重写的单测。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from webapp.backend import storage_path_rewrite as spr

OLD_ROOT = r"D:\OldRoot"
NEW_ROOT = r"E:\NewRoot"


@pytest.fixture(autouse=True)
def _hermetic_default_root(monkeypatch: pytest.MonkeyPatch):
    """切断默认根探测：单测不得读到开发机真实的迁移记录。"""
    monkeypatch.setattr(spr, "_platform_default_data_root", lambda: None)


def _write_record(
    data_root: Path,
    *,
    source_root: str = OLD_ROOT,
    target_root: str = NEW_ROOT,
    phase: str = "done",
    extra: dict | None = None,
) -> Path:
    record: dict = {
        "sourceRoot": source_root,
        "targetRoot": target_root,
        "phase": phase,
        "movedEntries": ["aiming_cookie.db"],
        "pendingEntries": [],
        "totalBytes": 10,
        "copiedBytes": 10,
        "error": None,
        "updatedAt": "2026-09-29T00:00:00Z",
    }
    if extra:
        record.update(extra)
    path = data_root / "storage-migration.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def _make_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY, video_path TEXT, note TEXT, size INTEGER
        );
        CREATE TABLE kovaak_runs (
            id INTEGER PRIMARY KEY, video_path TEXT, mouse_trace_path TEXT
        );
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, ref TEXT);
        """
    )
    con.executemany(
        "INSERT INTO sessions (id, video_path, note, size) VALUES (?, ?, ?, ?)",
        [
            (1, r"D:\OldRoot\sessions\9\video.mp4", "keep", 1),
            (2, "D:/OldRoot/sessions/9/derived/frame.png", "keep", 2),
            (3, r"D:\OldRoot", "keep", 3),
            (4, r"D:\OldRootBackup\sessions\1\v.mp4", "keep", 4),
            (5, None, "keep", 5),
            (6, r"E:\Other\sessions\1\v.mp4", "keep", 6),
        ],
    )
    con.execute(
        "INSERT INTO kovaak_runs (id, video_path, mouse_trace_path) VALUES (?, ?, ?)",
        (1, r"D:\OldRoot\runs\a.mp4", r"D:\OldRoot\traces\a.bin"),
    )
    con.execute(
        "INSERT INTO evidence (id, ref) VALUES (?, ?)", (1, r"D:\OldRoot\evidence\e.json")
    )
    con.commit()
    con.close()


def _read_rows(db_path: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        con.close()


def _record_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_migration_record_is_a_no_op(tmp_path: Path):
    _make_db(tmp_path / "aiming_cookie.db")
    before = _read_rows(tmp_path / "aiming_cookie.db", "sessions")

    status = spr.rewrite_paths_for_migration(tmp_path)

    assert status == spr.STATUS_SKIPPED_NO_RECORD
    assert _read_rows(tmp_path / "aiming_cookie.db", "sessions") == before
    assert not (tmp_path / "storage-migration.json").exists()


def test_rewrites_both_separator_forms_and_sets_flag(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    _make_db(db_path)
    record_path = _write_record(tmp_path)

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_REWRITTEN
    sessions = _read_rows(db_path, "sessions")
    assert sessions[0] == (1, r"E:\NewRoot\sessions\9\video.mp4", "keep", 1)
    assert sessions[1] == (2, "E:/NewRoot/sessions/9/derived/frame.png", "keep", 2)
    assert sessions[2] == (3, r"E:\NewRoot", "keep", 3)
    # 共享字符串前缀的同级目录不得误伤；NULL 与无关根原样保留。
    assert sessions[3] == (4, r"D:\OldRootBackup\sessions\1\v.mp4", "keep", 4)
    assert sessions[4] == (5, None, "keep", 5)
    assert sessions[5] == (6, r"E:\Other\sessions\1\v.mp4", "keep", 6)
    runs = _read_rows(db_path, "kovaak_runs")
    assert runs == [(1, r"E:\NewRoot\runs\a.mp4", r"E:\NewRoot\traces\a.bin")]
    assert _read_rows(db_path, "evidence") == [(1, r"E:\NewRoot\evidence\e.json")]
    # 标志写回，其余字段保留。
    record = _record_of(record_path)
    assert record["path_rewrite_done"] is True
    assert record["phase"] == "done"
    assert record["sourceRoot"] == OLD_ROOT
    assert record["targetRoot"] == NEW_ROOT


def test_flag_set_short_circuits(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    _make_db(db_path)
    before = _read_rows(db_path, "sessions")
    record_path = _write_record(tmp_path, extra={"path_rewrite_done": True})

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_SKIPPED_FLAG_SET
    assert _read_rows(db_path, "sessions") == before


def test_non_done_phase_is_skipped(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    _make_db(db_path)
    before = _read_rows(db_path, "sessions")
    record_path = _write_record(tmp_path, phase="running")

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_SKIPPED_PHASE
    assert _read_rows(db_path, "sessions") == before
    assert "path_rewrite_done" not in _record_of(record_path)


def test_identical_roots_is_a_noop_but_sets_flag(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    _make_db(db_path)
    before = _read_rows(db_path, "sessions")
    record_path = _write_record(tmp_path, source_root=OLD_ROOT, target_root="D:/OldRoot")

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_NOOP_IDENTICAL_ROOTS
    assert _read_rows(db_path, "sessions") == before
    assert _record_of(record_path)["path_rewrite_done"] is True


def test_verbatim_prefix_in_record_roots_is_stripped(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    _make_db(db_path)
    record_path = _write_record(
        tmp_path,
        source_root=r"\\?\D:\OldRoot",
        target_root=r"\\?\E:\NewRoot",
    )

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_REWRITTEN
    assert _read_rows(db_path, "sessions")[0] == (
        1, r"E:\NewRoot\sessions\9\video.mp4", "keep", 1,
    )


def test_underscore_in_root_matches_literally(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, p TEXT)")
    con.executemany(
        "INSERT INTO t (id, p) VALUES (?, ?)",
        [
            (1, r"D:\old_root\a.mp4"),
            (2, r"D:\oldXroot\a.mp4"),  # LIKE 未转义时会误命中
        ],
    )
    con.commit()
    con.close()
    record_path = _write_record(
        tmp_path, source_root=r"D:\old_root", target_root=r"E:\new_root"
    )

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_REWRITTEN
    assert _read_rows(db_path, "t") == [(1, r"E:\new_root\a.mp4"), (2, r"D:\oldXroot\a.mp4")]


def test_unreadable_record_is_skipped(tmp_path: Path):
    _make_db(tmp_path / "aiming_cookie.db")
    before = _read_rows(tmp_path / "aiming_cookie.db", "sessions")
    record_path = tmp_path / "storage-migration.json"
    record_path.write_text("{not json", encoding="utf-8")

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_SKIPPED_NO_RECORD
    assert _read_rows(tmp_path / "aiming_cookie.db", "sessions") == before


def test_missing_db_skips_without_flag(tmp_path: Path):
    record_path = _write_record(tmp_path)

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_SKIPPED_DB_MISSING
    assert "path_rewrite_done" not in _record_of(record_path)


def test_default_root_fallback_finds_record_when_data_root_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """生产自定义位置场景：记录在默认根，db 在生效根（DATA_ROOT）。"""
    data_root = tmp_path / "effective"
    default_root = tmp_path / "default"
    data_root.mkdir()
    default_root.mkdir()
    db_path = data_root / "aiming_cookie.db"
    _make_db(db_path)
    record_path = _write_record(default_root)
    monkeypatch.setattr(spr, "_platform_default_data_root", lambda: default_root)

    status = spr.rewrite_paths_for_migration(data_root)

    assert status == spr.STATUS_REWRITTEN
    assert _read_rows(db_path, "sessions")[0] == (
        1, r"E:\NewRoot\sessions\9\video.mp4", "keep", 1,
    )
    assert _record_of(record_path)["path_rewrite_done"] is True


def test_corrupt_db_fails_soft_without_flag(tmp_path: Path):
    db_path = tmp_path / "aiming_cookie.db"
    db_path.write_bytes(b"this is not a sqlite database" * 10)
    record_path = _write_record(tmp_path)

    status = spr.rewrite_paths_for_migration(tmp_path, record_path)

    assert status == spr.STATUS_FAILED
    assert "path_rewrite_done" not in _record_of(record_path)


def test_strip_extended_prefix_mirrors_rust_semantics():
    assert spr.strip_extended_prefix(r"\\?\E:\ACData") == r"E:\ACData"
    assert spr.strip_extended_prefix(r"\\?\UNC\server\share") == r"\\server\share"
    assert spr.strip_extended_prefix(r"E:\ACData") == r"E:\ACData"
    assert spr.strip_extended_prefix(r"\\server\share") == r"\\server\share"
    assert spr.strip_extended_prefix("E:/ACData") == "E:/ACData"
    long_path = r"\\?\E:" + "\\" + "a" * 300
    assert spr.strip_extended_prefix(long_path) == long_path
