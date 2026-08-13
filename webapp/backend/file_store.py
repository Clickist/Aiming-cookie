"""Generic JSON file read/write helpers backed by DATA_ROOT."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from . import config


def _data_root() -> Path:
    return Path(config.DATA_ROOT)


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

    Write to a sibling ``.tmp`` file first, then ``os.replace`` it over the
    target so a crash mid-write never leaves a truncated JSON file behind.
    """
    path = _data_root() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_sanitize_json_value(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


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
