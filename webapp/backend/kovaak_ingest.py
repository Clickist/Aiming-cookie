"""Local discovery of KovaaK Stats and Performance files."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

_PERFORMANCE_SUFFIXES = {".perf"}
_SUFFIX_RE = re.compile(r"\s+(?:stats|performance)$", re.IGNORECASE)
_RETRY_WINDOW_SECONDS = 10 * 60.0
_DIAGNOSTIC_CANDIDATE_LIMIT = 10
log = logging.getLogger(__name__)


class NonRetryableIngestionError(RuntimeError):
    """An ingestion failure that should remain observable without hot-loop retries."""

    def __init__(self, message: str = "", *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class RetryableIngestionError(RuntimeError):
    """A transient ingestion failure that should return to the ready state."""

    def __init__(self, message: str = "", *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


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


@dataclass
class _RetryBudget:
    failures: int = 0
    first_failure_monotonic: float = field(default_factory=time.monotonic)


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
        source: str = "automatic",
    ) -> None:
        if stable_scans < 1:
            raise ValueError("stable_scans must be at least 1")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or candidate_limit < 1:
            raise ValueError("candidate_limit must be a positive integer")
        self.directory = Path(directory).expanduser()
        self.callback = callback
        self.poll_interval = poll_interval
        self.stable_scans = stable_scans
        self.candidate_limit = candidate_limit
        self.source = source
        self._states: dict[Path, _FileState] = {}
        self._emitted: set[tuple[tuple[Path, int, int], ...]] = set()
        self._pending: set[tuple[tuple[Path, int, int], ...]] = set()
        self._retry_budgets: dict[tuple[tuple[Path, int, int], ...], _RetryBudget] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan_epoch_ms: int | None = None
        self._last_error: str | None = None
        self._directory_state = "unknown"
        self._supported_count = 0
        self._omitted_count = 0
        self._recent_candidates: list[dict[str, object]] = []

    def scan_once(self) -> list[KovaaKFileDiscovery]:
        """Scan once and return stable discoveries not emitted before."""
        self._last_scan_epoch_ms = int(time.time() * 1000)
        try:
            paths = [path for path in self.directory.iterdir() if path.is_file()]
        except FileNotFoundError:
            self._set_directory_error("directory_missing")
            return []
        except NotADirectoryError:
            self._set_directory_error("not_directory")
            return []
        except PermissionError:
            self._set_directory_error("directory_unreadable")
            return []
        except OSError as error:
            self._set_directory_error("directory_scan_failed", error)
            return []
        self._set_directory_ready()

        candidates: list[tuple[Path, object]] = []
        for path in paths:
            if not (is_stats_path(path) or is_performance_path(path)):
                continue
            try:
                candidates.append((path, path.stat()))
            except OSError:
                continue
        candidates.sort(key=lambda item: (-item[1].st_mtime_ns, item[0].name.casefold()))
        supported = candidates[: self.candidate_limit]
        self._supported_count = len(candidates)
        self._omitted_count = max(0, len(candidates) - len(supported))
        with self._lock:
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
            current_revisions = {(path, state.size, state.mtime_ns) for path, state in self._states.items()}
            self._emitted = {key for key in self._emitted if all(revision in current_revisions for revision in key)}
            self._retry_budgets = {key: budget for key, budget in self._retry_budgets.items() if all(revision in current_revisions for revision in key)}
            self._recent_candidates = [
                {
                    "name": path.name,
                    "kind": "stats" if is_stats_path(path) else "performance",
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "stable_scans": self._states[path].stable_scans,
                }
                for path, stat in supported[:_DIAGNOSTIC_CANDIDATE_LIMIT]
            ]

        grouped: dict[str, dict[str, Path]] = {}
        for path in stable_paths:
            grouped.setdefault(normalize_kovaak_stem(path), {})["stats" if is_stats_path(path) else "performance"] = path
        discoveries: list[KovaaKFileDiscovery] = []
        for stem, group in sorted(grouped.items()):
            discovery = KovaaKFileDiscovery(stem=stem, stats_path=group.get("stats"), performance_path=group.get("performance"))
            with self._lock:
                key = tuple((path, self._states[path].size, self._states[path].mtime_ns) for path in discovery.paths)
            if not self._reserve(key):
                continue
            try:
                result = self.callback(discovery)
            except Exception as error:
                self._handle_error(key, discovery.stem, error, "callback")
                continue
            self._complete_when_ready(key, discovery, result)
            discoveries.append(discovery)
        return discoveries

    def _set_directory_error(self, code: str, error: OSError | None = None) -> None:
        previous = self._directory_state
        self._directory_state = code
        self._last_error = code
        if previous != code:
            suffix = f": {error}" if error else ""
            log.warning("KovaaK watcher directory state=%s directory=%s%s", code, self.directory, suffix)

    def _set_directory_ready(self) -> None:
        if self._directory_state not in {"unknown", "ready"}:
            log.info("KovaaK watcher directory recovered directory=%s", self.directory)
        self._directory_state = "ready"
        self._last_error = None

    def _reserve(self, key: tuple[tuple[Path, int, int], ...]) -> bool:
        with self._lock:
            if key in self._emitted or key in self._pending:
                return False
            self._pending.add(key)
            return True

    def _handle_error(self, key: tuple[tuple[Path, int, int], ...], stem: str, error: BaseException, phase: str) -> None:
        if _is_retryable(error):
            self._release_or_give_up(key, stem, error)
            # OSError 是预期 IO 抖动（文件被占用等），重试窗口内每轮一条，带栈会刷屏；
            # 其余 retryable 都是意外异常，桌面用户排障只有 backend.log，必须带栈。
            log.warning(
                "KovaaK ingestion retrying stem=%s phase=%s error=%s",
                stem, phase, error,
                exc_info=None if isinstance(error, OSError) else error,
            )
        else:
            self._mark_emitted(key)
            code = getattr(error, "code", "non_retryable")
            log.info("KovaaK ingestion rejected stem=%s phase=%s code=%s error=%s", stem, phase, code, error)
        self._last_error = str(error)[:240]

    def _release_or_give_up(self, key: tuple[tuple[Path, int, int], ...], stem: str, error: BaseException) -> None:
        with self._lock:
            budget = self._retry_budgets.get(key)
            if budget is None:
                budget = _RetryBudget()
                self._retry_budgets[key] = budget
            budget.failures += 1
            exhausted = time.monotonic() - budget.first_failure_monotonic >= _RETRY_WINDOW_SECONDS
            self._pending.discard(key)
            if not exhausted:
                return
            self._emitted.add(key)
            del self._retry_budgets[key]
        log.warning("KovaaK ingestion gave up after retention window stem=%s error=%s", stem, error)

    def _mark_emitted(self, key: tuple[tuple[Path, int, int], ...]) -> None:
        with self._lock:
            self._pending.discard(key)
            self._emitted.add(key)
            self._retry_budgets.pop(key, None)

    def _complete_when_ready(self, key: tuple[tuple[Path, int, int], ...], discovery: KovaaKFileDiscovery, result: object) -> None:
        add_done_callback = getattr(result, "add_done_callback", None)
        if not callable(add_done_callback):
            self._mark_emitted(key)
            return

        def report_result(done: object) -> None:
            try:
                done.result()  # type: ignore[union-attr]
            except BaseException as error:
                self._handle_error(key, discovery.stem, error, "finalize")
            else:
                self._mark_emitted(key)

        try:
            add_done_callback(report_result)
        except Exception as error:
            self._handle_error(key, discovery.stem, error, "future_registration")

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "directory": str(self.directory),
                "source": self.source,
                "running": bool(self._thread and self._thread.is_alive()),
                "directory_state": self._directory_state,
                "last_scan_epoch_ms": self._last_scan_epoch_ms,
                "last_error": self._last_error,
                "supported_count": self._supported_count,
                "omitted_count": self._omitted_count,
                "candidate_limit": self.candidate_limit,
                "stable_scans_required": self.stable_scans,
                "recent_candidates": list(self._recent_candidates),
                "pending_count": len(self._pending),
                "retrying_count": len(self._retry_budgets),
            }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kovaak-ingest", daemon=True)
        self._thread.start()
        log.info("KovaaK watcher started directory=%s source=%s", self.directory, self.source)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                self._last_error = "watcher_scan_unhandled"
                log.exception("KovaaK watcher scan failed directory=%s", self.directory)
            self._stop.wait(self.poll_interval)


class KovaaKIngestionService:
    """Own Stats/Performance watchers and bridge discoveries to one callback."""

    def __init__(
        self,
        *,
        stats_dir: str | Path | Sequence[str | Path] | None,
        performance_dir: str | Path | Sequence[str | Path] | None,
        callback: Callable[[KovaaKFileDiscovery], object],
        poll_interval: float = 1.0,
        candidate_limit: int = 50,
        source: str = "automatic",
    ) -> None:
        self._callback = callback
        self._poll_interval = poll_interval
        self._candidate_limit = candidate_limit
        self._lock = threading.RLock()
        self._running = False
        self._source = source
        self._watchers: list[KovaaKDirectoryWatcher] = []
        self._replace_watchers(stats_dir, performance_dir, source)

    @staticmethod
    def _as_paths(value: str | Path | Sequence[str | Path] | None) -> list[Path]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [Path(value)]
        return [Path(path) for path in value]

    def _replace_watchers(self, stats_dir: str | Path | Sequence[str | Path] | None, performance_dir: str | Path | Sequence[str | Path] | None, source: str) -> None:
        directories = self._as_paths(stats_dir) + self._as_paths(performance_dir)
        unique = list(dict.fromkeys(path.expanduser() for path in directories))
        self._watchers = [
            KovaaKDirectoryWatcher(directory, self._callback, poll_interval=self._poll_interval, stable_scans=2, candidate_limit=self._candidate_limit, source=source)
            for directory in unique
        ]
        self._source = source

    def reconfigure(self, stats_dirs: str | Path | Sequence[str | Path], performance_dirs: str | Path | Sequence[str | Path], *, source: str = "confirmed") -> bool:
        with self._lock:
            old_watchers = self._watchers
            was_running = self._running
            for watcher in old_watchers:
                watcher.stop()
            try:
                self._replace_watchers(stats_dirs, performance_dirs, source)
                if was_running:
                    for watcher in self._watchers:
                        watcher.start()
            except Exception:
                self._watchers = old_watchers
                if was_running:
                    for watcher in old_watchers:
                        watcher.start()
                raise
        log.info("KovaaK ingestion reconfigured watcher_count=%s source=%s", self.watcher_count, source)
        return True

    def start(self) -> None:
        with self._lock:
            self._running = True
            for watcher in self._watchers:
                watcher.start()
        if not self._watchers:
            log.warning("KovaaK ingestion started with zero watchers")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            for watcher in self._watchers:
                watcher.stop()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {"version": "kovaak_watcher.v1", "source": self._source, "watcher_count": len(self._watchers), "watchers": [watcher.diagnostics() for watcher in self._watchers]}

    @property
    def watcher_count(self) -> int:
        with self._lock:
            return len(self._watchers)


__all__ = ["KovaaKDirectoryWatcher", "KovaaKFileDiscovery", "KovaaKIngestionService", "NonRetryableIngestionError", "RetryableIngestionError", "is_performance_path", "is_stats_path", "normalize_kovaak_stem"]
