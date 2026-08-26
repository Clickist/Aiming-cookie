"""Persist and inspect user-confirmed local KovaaK source directories."""

from __future__ import annotations

import os
from pathlib import Path

from . import file_store

_DIRECTORIES_PATH = "config/kovaak-local-directories.json"


def _resolve_directory(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("KovaaK directory paths must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("KovaaK directory paths must exist") from error
    if not resolved.is_dir():
        raise ValueError("KovaaK directory paths must be directories")
    if not os.access(resolved, os.R_OK):
        raise ValueError("KovaaK directory paths must be readable")
    return resolved


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def validate_directories(stats_dir: str, performance_dir: str) -> tuple[Path, Path]:
    stats = _resolve_directory(stats_dir)
    performance = _resolve_directory(performance_dir)
    if _path_key(stats) == _path_key(performance):
        raise ValueError("Stats and Performance directories must be different")
    return stats, performance


def get_confirmed_directories() -> tuple[Path, Path] | None:
    try:
        data = file_store.read_json(_DIRECTORIES_PATH)
    except (OSError, ValueError):
        # Corrupted/unreadable file is treated as never confirmed; a
        # JSONDecodeError here used to escape through config.py's import-time
        # resolution and prevent the backend from starting at all.
        return None
    if not isinstance(data, dict):
        return None
    stats_dir = data.get("stats_dir")
    performance_dir = data.get("performance_dir")
    if not isinstance(stats_dir, str) or not isinstance(performance_dir, str):
        return None
    try:
        return validate_directories(stats_dir, performance_dir)
    except ValueError:
        return None


def save_confirmed_directories(stats_dir: str, performance_dir: str) -> tuple[Path, Path]:
    stats, performance = validate_directories(stats_dir, performance_dir)
    file_store.write_json(_DIRECTORIES_PATH, {
        "stats_dir": str(stats),
        "performance_dir": str(performance),
    })
    return stats, performance


def _is_stats_file(path: Path) -> bool:
    return path.suffix == ".stats" or path.name.endswith(" Stats.csv")


def _is_performance_file(path: Path) -> bool:
    return path.suffix == ".perf"


def matching_file_count(directory: Path, *, kind: str) -> int:
    matcher = _is_stats_file if kind == "stats" else _is_performance_file
    try:
        return sum(1 for path in directory.iterdir() if path.is_file() and matcher(path))
    except OSError:
        return 0


def directory_status(directory: Path | None, *, kind: str, source: str) -> dict:
    count = matching_file_count(directory, kind=kind) if directory is not None else 0
    return {
        "path": str(directory) if directory is not None else None,
        "source": source,
        "matching_file_count": count,
        "matching_files": "found" if count else "no_matching_files",
    }
