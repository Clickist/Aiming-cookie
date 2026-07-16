"""Local discovery of KovaaK Stats and Performance files.

RefleK was evaluated as a capability reference. This is an independent polling
and stability implementation kept separate from database and UI contracts.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_PERFORMANCE_SUFFIXES = {".perf"}
_SUFFIX_RE = re.compile(r"\s+(?:stats|performance)$", re.IGNORECASE)
log = logging.getLogger(__name__)


class NonRetryableIngestionError(RuntimeError):
    """An ingestion failure that should remain observable without hot-loop retries."""


class RetryableIngestionError(RuntimeError):
    """A transient ingestion failure that should return to the ready state."""


def _is_retryable(error: BaseException) -> bool:
    return not isinstance(error, NonRetryableIngestionError)


@dataclass(frozen=True)
class KovaaKFileDiscovery:
    """A stable file or paired set discovered in the KovaaK directory."""

    stem: str
    stats_path: Optional[Path] = None
    performance_path: Optional[Path] = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(p for p in (self.stats_path, self.performance_path) if p is not None)


@dataclass
class _FileState:
    size: int
    mtime_ns: int
    stable_scans: int = 1


def is_stats_path(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.suffix.lower() == ".stats":
        return True
    return candidate.suffix.lower() == ".csv" and _SUFFIX_RE.search(candidate.stem) is not None


def is_performance_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _PERFORMANCE_SUFFIXES


def normalize_kovaak_stem(path: str | Path) -> str:
    """Normalize Stats/Performance suffixes so the files can be paired."""
    name = Path(path).name
    suffix = Path(name).suffix
    if suffix:
        name = name[: -len(suffix)]
    name = _SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip().casefold()


class KovaaKDirectoryWatcher:
    """Poll a KovaaK directory and callback once for stable new discoveries."""

    def __init__(
        self,
        directory: str | Path,
        callback: Callable[[KovaaKFileDiscovery], object],
        *,
        poll_interval: float = 1.0,
        stable_scans: int = 2,
        candidate_limit: int = 50,
    ) -> None:
        if stable_scans < 1:
            raise ValueError("stable_scans must be at least 1")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if (
            isinstance(candidate_limit, bool)
            or not isinstance(candidate_limit, int)
            or candidate_limit < 1
        ):
            raise ValueError("candidate_limit must be a positive integer")
        self.directory = Path(directory).expanduser()
        self.callback = callback
        self.poll_interval = poll_interval
        self.stable_scans = stable_scans
        self.candidate_limit = candidate_limit
        self._states: dict[Path, _FileState] = {}
        self._emitted: set[tuple[tuple[Path, int, int], ...]] = set()
        self._pending: set[tuple[tuple[Path, int, int], ...]] = set()
        self._emission_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def scan_once(self) -> list[KovaaKFileDiscovery]:
        """Scan once and return stable discoveries not emitted before."""
        try:
            paths = [path for path in self.directory.iterdir() if path.is_file()]
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return []

        candidates = []
        for path in paths:
            if not (is_stats_path(path) or is_performance_path(path)):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append((path, stat))
        candidates.sort(key=lambda item: (-item[1].st_mtime_ns, item[0].name.casefold()))
        supported = candidates[: self.candidate_limit]
        current = {path for path, _stat in supported}
        self._states = {path: state for path, state in self._states.items() if path in current}
        stable_paths: list[Path] = []
        for path, stat in supported:
            previous = self._states.get(path)
            if previous and previous.size == stat.st_size and previous.mtime_ns == stat.st_mtime_ns:
                previous.stable_scans += 1
            else:
                self._states[path] = _FileState(stat.st_size, stat.st_mtime_ns)
            if self._states[path].stable_scans >= self.stable_scans:
                stable_paths.append(path)

        current_revisions = {
            (path, state.size, state.mtime_ns)
            for path, state in self._states.items()
        }
        with self._emission_lock:
            self._emitted = {
                key for key in self._emitted
                if all(revision in current_revisions for revision in key)
            }

        grouped: dict[str, dict[str, Path]] = {}
        for path in stable_paths:
            stem = normalize_kovaak_stem(path)
            kind = "stats" if is_stats_path(path) else "performance"
            grouped.setdefault(stem, {})[kind] = path

        discoveries: list[KovaaKFileDiscovery] = []
        for stem, group in sorted(grouped.items()):
            discovery = KovaaKFileDiscovery(
                stem=stem,
                stats_path=group.get("stats"),
                performance_path=group.get("performance"),
            )
            key = tuple(
                (path, self._states[path].size, self._states[path].mtime_ns)
                for path in discovery.paths
            )
            if not self._reserve(key):
                continue
            try:
                result = self.callback(discovery)
            except Exception as error:
                if _is_retryable(error):
                    self._release(key)
                else:
                    self._mark_emitted(key)
                log.exception("KovaaK ingestion callback failed for %s", discovery.stem)
                continue
            self._complete_when_ready(key, discovery, result)
            discoveries.append(discovery)
        return discoveries

    def _reserve(self, key: tuple[tuple[Path, int, int], ...]) -> bool:
        with self._emission_lock:
            if key in self._emitted or key in self._pending:
                return False
            self._pending.add(key)
            return True

    def _release(self, key: tuple[tuple[Path, int, int], ...]) -> None:
        with self._emission_lock:
            self._pending.discard(key)

    def _mark_emitted(self, key: tuple[tuple[Path, int, int], ...]) -> None:
        with self._emission_lock:
            self._pending.discard(key)
            self._emitted.add(key)

    def _complete_when_ready(
        self,
        key: tuple[tuple[Path, int, int], ...],
        discovery: KovaaKFileDiscovery,
        result: object,
    ) -> None:
        add_done_callback = getattr(result, "add_done_callback", None)
        if not callable(add_done_callback):
            self._mark_emitted(key)
            return

        def report_result(done: object) -> None:
            try:
                done.result()  # type: ignore[union-attr]
            except BaseException as error:
                if _is_retryable(error):
                    self._release(key)
                else:
                    self._mark_emitted(key)
                log.exception("KovaaK ingestion future failed for %s", discovery.stem)
            else:
                self._mark_emitted(key)

        try:
            add_done_callback(report_result)
        except Exception:
            self._release(key)
            log.exception("KovaaK ingestion future registration failed for %s", discovery.stem)

    def start(self) -> None:
        """Start the background polling thread; safe to call repeatedly."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kovaak-ingest", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background polling thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.scan_once()
            self._stop.wait(self.poll_interval)


__all__ = [
    "KovaaKDirectoryWatcher",
    "KovaaKFileDiscovery",
    "KovaaKIngestionService",
    "NonRetryableIngestionError",
    "RetryableIngestionError",
    "is_performance_path",
    "is_stats_path",
    "normalize_kovaak_stem",
]


class KovaaKIngestionService:
    """Own Stats/Performance watchers and bridge discoveries to one callback."""

    def __init__(
        self,
        *,
        stats_dir: str | Path | None,
        performance_dir: str | Path | None,
        callback: Callable[[KovaaKFileDiscovery], object],
        poll_interval: float = 1.0,
        candidate_limit: int = 50,
    ) -> None:
        directories = [directory for directory in (stats_dir, performance_dir) if directory]
        self._watchers = [
            KovaaKDirectoryWatcher(
                directory,
                callback,
                poll_interval=poll_interval,
                stable_scans=2,
                candidate_limit=candidate_limit,
            )
            for directory in dict.fromkeys(Path(directory) for directory in directories)
        ]

    def start(self) -> None:
        for watcher in self._watchers:
            watcher.start()

    def stop(self) -> None:
        for watcher in self._watchers:
            watcher.stop()

    @property
    def watcher_count(self) -> int:
        return len(self._watchers)
