"""Generic JSON file read/write helpers backed by DATA_ROOT."""

from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
from pathlib import Path

from . import config


def _data_root() -> Path:
    return Path(config.DATA_ROOT)


# [边界收紧 2026-10-11] 公有名：跨模块消费走 data_root()（同一函数对象），
# 不再 import 私有 _data_root。
data_root = _data_root


def data_root_ready() -> bool:
    """Return True if DATA_ROOT exists and is writable."""
    try:
        root = _data_root()
        root.mkdir(parents=True, exist_ok=True)
        return root.is_dir() and os.access(root, os.W_OK)
    except Exception:
        return False


def read_json(relative_path: str) -> dict | list | None:
    """Read a JSON file. Return None if not exists."""
    path = _data_root() / relative_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def stat_json(relative_path: str) -> tuple[int, int] | None:
    """Return (mtime_ns, size) for a data-root file, or None if missing.

    Queue 的 session 缓存用它做变更检测：stat 比 parse 便宜两个数量级，
    指纹不变即可信任缓存条目，无需重新解析 JSON。
    """
    path = _data_root() / relative_path
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _sanitize_json_value(value):
    """Replace non-finite floats with null so files stay strict JSON.

    NaN/Infinity are not valid JSON; writing them literally (Python's default
    ``json.dumps`` behaviour) produces files that Node/other consumers reject.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def write_json(relative_path: str, data) -> None:
    """Write a JSON file atomically, creating parent dirs.

    Write to a unique sibling ``.tmp`` file first, then ``os.replace`` it over
    the target so a crash mid-write never leaves a truncated JSON file behind.
    The tmp name is unique per call (``tempfile.mkstemp``), so two threads
    writing the same target never contend on a shared tmp file (系统评审
    2026-10-16 #1：固定 ``.{name}.tmp`` 在切窗线程与事件循环并发写 run meta
    时 120 次出现 46 次文件占用/权限异常)；写或 replace 失败时清掉本次 tmp，
    不留残留。与分析侧 ``analysis_service.mkstemp`` 原子写先例同款。
    """
    path = _data_root() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_sanitize_json_value(data), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def read_jsonl(relative_path: str) -> list[dict]:
    """Read a JSONL file. Return empty list if not exists."""
    path = _data_root() / relative_path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_jsonl(relative_path: str, entry: dict) -> None:
    """Append a single entry to a JSONL file."""
    path = _data_root() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_sanitize_json_value(entry), ensure_ascii=False) + "\n")


def delete_file(relative_path: str) -> bool:
    """Delete a file. Return True if it existed."""
    path = _data_root() / relative_path
    if path.exists():
        path.unlink()
        return True
    return False


def list_dir(relative_dir: str, pattern: str = "*.json") -> list[Path]:
    """List files in a directory matching pattern."""
    directory = _data_root() / relative_dir
    if not directory.is_dir():
        return []
    return sorted(directory.glob(pattern))


def list_subdirs(relative_dir: str) -> list[Path]:
    """List immediate subdirectories of a directory, sorted by name."""
    directory = _data_root() / relative_dir
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())
