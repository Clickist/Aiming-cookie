"""Recursive discovery and import of cleaned external KovaaK telemetry.

Discovers every ``rounds_index.json`` below the configured watch root
(upstream layouts evolved three times: top-level batch index, index inside
the source directory, and a session wrapper layer), deduplicates rounds by
``(source_file, round, index_dirname)`` — path-independent — and writes one
ExternalTelemetryRun per round into ``{DATA_ROOT}/external/``.

Import-side computation boundary (deliberate): only deterministic,
constant-free summaries of the target channel are computed here (T2K
distribution, spawn/death/timeout counts, official-counter reconciliation).
Anything depending on sensitivity constants (deg/count), input traces,
camera frames or fine alignment S belongs to the analysis side. Rule of
thumb: a rollup that would reference ``calibration_profile``, ``window_*``,
an input trace or camera frames must not live in this module.

SIDECARS.md v1 sidecars (``views_NN.jsonl``/``inputs_NN.jsonl``/``bb.json``/
``merge_manifest.json``) are frozen verbatim next to the frames for the
analysis side; nothing is computed from them here.

Upstream ``cleaned/`` is read-only; frozen frame copies land in DATA_ROOT.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config, external_telemetry_store as store
from .kovaak_ingest import NonRetryableIngestionError, RetryableIngestionError

log = logging.getLogger(__name__)

# 清洗阈值口径对齐 FORMAT.md §6（v2，版本钉死在 docs/EXTERNAL_TELEMETRY_IMPORT.md）。
SUPPORTED_FORMAT_VERSION = store.SUPPORTED_FORMAT_VERSION
IMPORT_PARSER_VERSION = store.IMPORT_PARSER_VERSION
T2K_LONG_SECONDS = 2.0
_WINDOW_EPS = 1e-6
_PAIR_TOLERANCE_MS = 1000
_DIAGNOSTIC_CANDIDATE_LIMIT = 10
_RETRY_WINDOW_SECONDS = 10 * 60.0
_SOURCE_TS_RE = re.compile(r"_(\d{2})(\d{2})_(\d{6})\.jsonl$", re.IGNORECASE)
# 已知目标数交叉检查只认标签里显式写出数量的场景（如 "1wall 6targets small"）；
# 没有官方目标数知识时不检查（gate=unknown）。
_KNOWN_TARGET_COUNT_RE = re.compile(r"(\d+)\s*targets", re.IGNORECASE)

_SUMMARY_KEYS = ("imported", "revised", "skipped", "rejected", "proposal_patches", "failed")


class UnsupportedFormatVersion(NonRetryableIngestionError):
    """A fail-closed gate: unsupported index version or unusable round data."""


@dataclass
class _FileState:
    size: int
    mtime_ns: int
    stable_scans: int = 1


@dataclass
class _RetryBudget:
    failures: int = 0
    first_failure_monotonic: float = field(default_factory=time.monotonic)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile (same method as numpy.percentile)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _round6(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def epoch_anchor_for_source(source_file: str) -> dict:
    """Coarse (±1s) epoch anchor from the recorder's MMDD_HHMMSS filename.

    The filename timestamp is the recorder's local wall clock at t0
    (strftime runs before t0 is captured, FORMAT §5.3); the year is not
    encoded, so the current year is assumed and recorded explicitly.
    """
    match = _SOURCE_TS_RE.search(source_file)
    if match is None:
        return {"method": "none"}
    year = datetime.now().year
    try:
        stamp = datetime.strptime(f"{year}{match.group(1)}{match.group(2)} {match.group(3)}", "%Y%m%d %H%M%S")
    except ValueError:
        return {"method": "none"}
    return {
        "method": "filename_wallclock",
        "epoch_start_est": stamp.timestamp(),
        "precision_s": 1.0,
        "year_assumed": year,
    }


def summarize_frames(payload: bytes) -> dict:
    """Count parseable frame lines; tolerate individual bad lines (degraded)."""
    frame_lines = 0
    bad_lines = 0
    unsupported_events = 0
    timestamps: list[float] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(record, dict):
            bad_lines += 1
            continue
        if record.get("ev") != "frame":
            # 未来事件（input/cam）一期不受支持：可观测、不崩溃（§1.2 版本门）。
            unsupported_events += 1
            continue
        frame_lines += 1
        stamp = record.get("t")
        if isinstance(stamp, (int, float)):
            timestamps.append(float(stamp))
    total = frame_lines + bad_lines + unsupported_events
    return {
        "frame_lines": frame_lines,
        "bad_lines": bad_lines,
        "unsupported_events": unsupported_events,
        "parse_rate": frame_lines / total if total else 0.0,
        "t_min": min(timestamps) if timestamps else None,
        "t_max": max(timestamps) if timestamps else None,
    }


def t2k_rollup(lives: list[dict], t_start: float, t_end: float) -> dict:
    """Deterministic target-channel T2K summary over the round's own window.

    口径照抄 session_quant 系列（已对拍 ±0.001s）：仅统计窗内出生且在窗内
    死亡的 life；最后一帧仍存活（death ≥ t_end − ε）的 life 记为 timeout，
    不入分布。只依赖目标通道 + tr 时基，与灵敏度常数无关。
    """
    durations = sorted(
        float(life["t_end"]) - float(life["t_start"])
        for life in lives
        if float(life["t_start"]) >= t_start - _WINDOW_EPS
        and float(life["t_end"]) < t_end - _WINDOW_EPS
    )
    count = len(durations)
    over_2s = sum(1 for value in durations if value > T2K_LONG_SECONDS)
    return {
        "n": count,
        "p10": _round6(_percentile(durations, 10)),
        "p50": _round6(_percentile(durations, 50)),
        "p90": _round6(_percentile(durations, 90)),
        "mean": _round6(sum(durations) / count) if count else None,
        "max": _round6(durations[-1]) if count else None,
        "over_2s_share": _round6(over_2s / count) if count else None,
        "definition": (
            "target life duration (birth->death, cleaned segment boundaries); "
            "lives born and dying strictly inside [t_start, t_end); a life "
            "still present at the final frame counts as timeout"
        ),
    }


def counts_from_index(round_entry: dict) -> dict:
    lives = [life for target in round_entry.get("targets", []) for life in target.get("lives", [])]
    t_start = float(round_entry.get("t_start", 0.0))
    t_end = float(round_entry.get("t_end", 0.0))
    spawns = sum(1 for life in lives if float(life["t_start"]) >= t_start - _WINDOW_EPS)
    deaths = sum(1 for life in lives if float(life["t_end"]) < t_end - _WINDOW_EPS)
    timeouts = sum(1 for life in lives if float(life["t_end"]) >= t_end - _WINDOW_EPS)
    return {
        "n_targets": round_entry.get("n_targets", len(round_entry.get("targets", []))),
        "n_moving_targets": round_entry.get("n_moving_targets", 0),
        "motion_mix": round_entry.get("motion_mix"),
        "spawns": spawns,
        "deaths": deaths,
        "timeouts": timeouts,
        "timeout_rate": _round6(timeouts / spawns) if spawns else None,
    }


def run_windows_from_store() -> list[dict]:
    """Read KovaaK run pairing anchors (id/window/perf header) from DATA_ROOT.

    Deliberately narrow: only the fields needed for the coarse filename-anchor
    pairing; the KovaaK run contract itself is not touched.
    """
    runs: list[dict] = []
    for path in store.file_store.list_subdirs("runs"):
        try:
            int(path.name)
        except ValueError:
            continue
        try:
            meta = store.file_store.read_json(f"runs/{path.name}/meta.json")
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        window_start = meta.get("window_start_epoch_ms")
        window_end = meta.get("window_end_epoch_ms")
        if not isinstance(window_start, (int, float)) or not isinstance(window_end, (int, float)):
            continue
        performance = meta.get("performance_summary") if isinstance(meta.get("performance_summary"), dict) else {}
        header = performance.get("header") if isinstance(performance.get("header"), dict) else {}
        totals = performance.get("totals") if isinstance(performance.get("totals"), dict) else {}
        runs.append({
            "run_id": meta.get("id", path.name),
            "window_start_epoch_ms": float(window_start),
            "window_end_epoch_ms": float(window_end),
            "scenario_name": header.get("scenario_name"),
            "kills": totals.get("kills"),
            "fired": totals.get("shotsFired"),
            "score": totals.get("score"),
        })
    return runs


def pair_runs(
    epoch_window_s: tuple[float, float],
    proposal_label: str | None,
    runs: list[dict],
) -> dict:
    """Coarse window intersection (±1s) against KovaaK run challenge windows."""
    start_ms = epoch_window_s[0] * 1000.0
    end_ms = epoch_window_s[1] * 1000.0
    matched = [
        run for run in runs
        if (start_ms - _PAIR_TOLERANCE_MS) < run["window_end_epoch_ms"]
        and (end_ms + _PAIR_TOLERANCE_MS) > run["window_start_epoch_ms"]
    ]
    matched.sort(key=lambda run: abs(run["window_start_epoch_ms"] - start_ms))
    official = None
    if matched:
        best = matched[0]
        official = {
            "scenario_name": best.get("scenario_name"),
            "kills": best.get("kills"),
            "fired": best.get("fired"),
            "score": best.get("score"),
            "run_id": best.get("run_id"),
        }
    label = (proposal_label or "").strip().casefold()
    official_name = str((official or {}).get("scenario_name") or "").strip().casefold()
    if not label or not official_name:
        agreement = "unverifiable"
    elif label == official_name or label in official_name or official_name in label:
        agreement = "agree"
    else:
        agreement = "disagree"
    return {
        "matched_run_ids": [run["run_id"] for run in matched],
        "pair_confidence": "coarse" if matched else None,
        "perf_official": official,
        "label_agreement": agreement,
    }


def scenario_proposal_for(
    round_dir: Path, round_number: int, round_file: str,
) -> dict | None:
    """Read the proposal-only scenario label for one round (never overrides).

    scenario.json is an optional sidecar that may be generated after the
    round files; callers persist a pending placeholder and patch later (§2.5).
    """
    try:
        payload = (round_dir / "scenario.json").read_bytes()
    except OSError:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for entry in data.get("rounds", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("round") != round_number and entry.get("round_file") != round_file:
            continue
        scenario = entry.get("scenario")
        if not isinstance(scenario, dict):
            return None
        proposal = dict(scenario)  # 逐字段原样透传（含置信度/tie_group/candidates）
        proposal["generator"] = data.get("generator")
        proposal["generated_at"] = data.get("generated_at")
        proposal["source"] = "scenario.json"
        return proposal
    return None


def known_target_count(label: object) -> int | None:
    """Only trust labels that state their own target count (e.g. "6targets")."""
    if not isinstance(label, str):
        return None
    match = _KNOWN_TARGET_COUNT_RE.search(label)
    return int(match.group(1)) if match else None


def _sidecar_names(round_file: str) -> tuple[tuple[str, str], ...]:
    """(meta key, source filename) pairs of one round's sidecars (SIDECARS.md §0).

    views/inputs 按轮号 NN 与 round_NN.jsonl 一一对应；bb/merge_manifest 是轮
    目录级共享件。命名铁律（SIDECARS.md）：旁车不以 round_ 开头，不会被
    round_*.jsonl glob 误当孤儿轮。
    """
    round_no = round_file[len("round_"):-len(".jsonl")]
    return (
        ("views", f"views_{round_no}.jsonl"),
        ("inputs", f"inputs_{round_no}.jsonl"),
        ("bb", "bb.json"),
        ("merge_manifest", "merge_manifest.json"),
    )


def build_targets(index_targets: list[dict]) -> list[dict]:
    """Project index target metadata. addr never becomes identity (pooling);
    target paths are namespaced to ``*_cm`` (AC input-side ``path_length``
    means something else). tid identity = (source_file, round, tid) triple."""
    targets: list[dict] = []
    for target in index_targets:
        lives = [
            {
                "t_start": life.get("t_start"),
                "t_end": life.get("t_end"),
                "n": life.get("n"),
                "path_cm": life.get("path"),
            }
            for life in target.get("lives", [])
        ]
        targets.append({
            "tid": target.get("tid"),
            "addr_hex": target.get("addr_hex"),
            "motion": target.get("motion"),
            "birth": target.get("birth"),
            "death": target.get("death"),
            "alive_window": target.get("alive_window"),
            "n_samples": target.get("n_samples"),
            "n_lives": target.get("n_lives", len(lives)),
            "target_path_length_cm": target.get("path_length"),
            "domain": target.get("domain"),
            "lives": lives,
        })
    return targets


class ExternalTelemetryWatcher:
    """Poll a watch root recursively and import stable cleaned rounds once.

    Reuses the KovaaKDirectoryWatcher mechanics: 1s polling, a file counts as
    stable after ``stable_scans`` consecutive scans with identical size+mtime,
    a 10-minute retry budget for transient failures, and observable
    diagnostics. The ledger (not in-memory state alone) carries idempotence
    across restarts.
    """

    def __init__(
        self,
        watch_root: str | Path,
        *,
        poll_interval: float = 1.0,
        stable_scans: int = 2,
        source: str = "automatic",
    ) -> None:
        if stable_scans < 1:
            raise ValueError("stable_scans must be at least 1")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.watch_root = Path(watch_root).expanduser()
        self.poll_interval = poll_interval
        self.stable_scans = stable_scans
        self.source = source
        self._states: dict[tuple[str, Path], _FileState] = {}
        self._retry_budgets: dict[str, _RetryBudget] = {}
        self._ledger_lock = threading.RLock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_scan_epoch_ms: int | None = None
        self._last_error: str | None = None
        self._directory_state = "unknown"
        self._totals = {key: 0 for key in _SUMMARY_KEYS}
        self._last_summary: dict | None = None
        self._recent_candidates: list[dict[str, object]] = []
        self._scan_runs: list[dict] | None = None
        # 旁车每扫缓存（SIDECARS.md v1）：round_dir -> {文件名: (size, mtime_ns)}，
        # 只含本次 scan 已稳定的旁车源件。
        self._scan_sidecars: dict[Path, dict[str, tuple[int, int]]] = {}
        # 上次哈希旁车时的 (size, mtime_ns)：skip 路径据此免重读大文件（幂等快路径）。
        self._hashed_sidecars: dict[Path, tuple[int, int]] = {}
        self._failed_index_dirs: set[Path] = set()
        # 本次 scan 内冻结副本写失败次数（如 Windows 杀软锁目标文件）：
        # 不中止 scan，并入 summary["failed"] 可观测。
        self._sidecar_write_failures = 0

    # ------------------------------------------------------------------ scan

    def scan_once(self) -> dict:
        """Scan once; import stable index/orphan discoveries. Returns counts."""
        self._last_scan_epoch_ms = int(time.time() * 1000)
        summary = {key: 0 for key in _SUMMARY_KEYS}
        if not self.watch_root.is_dir():
            self._set_directory_error("directory_missing")
            return summary
        self._set_directory_ready()

        index_files = self._stable_files("rounds_index.json")
        round_files = self._stable_files("round_*.jsonl")
        scenario_files = self._stable_files("scenario.json")
        # 旁车（FPSAimTrainer SIDECARS.md v1）复用同一稳定检测机制：四个 pattern
        # 在 _states 里按 (pattern, path) 分槽各扫各的，导入侧只冻结已稳定源件。
        sidecar_patterns = ("views_*.jsonl", "inputs_*.jsonl", "bb.json", "merge_manifest.json")
        sidecar_files = {pattern: self._stable_files(pattern) for pattern in sidecar_patterns}
        with self._lock:
            sidecar_index: dict[Path, dict[str, tuple[int, int]]] = {}
            for pattern, paths in sidecar_files.items():
                for path in paths:
                    state = self._states.get((pattern, path))
                    if state is not None:
                        sidecar_index.setdefault(path.parent, {})[path.name] = (state.size, state.mtime_ns)
            self._scan_sidecars = sidecar_index
            self._recent_candidates = [
                {
                    "path": self._relative(path),
                    "size": state.size,
                    "mtime_ns": state.mtime_ns,
                    "stable_scans": state.stable_scans,
                }
                for (_pattern, path), state in sorted(self._states.items(), key=lambda item: str(item[0][1]))[:_DIAGNOSTIC_CANDIDATE_LIMIT]
            ]

        self._scan_runs = None  # per-scan pairing cache, loaded lazily
        self._sidecar_write_failures = 0
        try:
            seen_index_paths = {path for path in self.watch_root.rglob("rounds_index.json") if path.is_file()}
        except OSError:
            seen_index_paths = set(index_files)
        # 尚未成功解析的 index（未稳定/半写失败）：其目录树下本轮文件暂不做
        # 孤立导入，等 index 就绪走带完整元数据的导入路径，避免重复身份。
        self._failed_index_dirs = {path.parent for path in seen_index_paths - set(index_files)}
        covered_round_dirs: set[Path] = set()
        for index_path in index_files:
            covered_round_dirs.update(self._import_index(index_path, summary))
        for round_path in round_files:
            if round_path.parent in covered_round_dirs:
                continue
            if self._under_unresolved_index(round_path):
                continue
            self._import_orphan_round(round_path, summary)
        self._patch_scenario_proposals(scenario_files, summary)

        with self._lock:
            summary["failed"] += self._sidecar_write_failures
            self._last_summary = dict(summary)
            for key in _SUMMARY_KEYS:
                self._totals[key] += summary.get(key, 0)
        return summary

    def _under_unresolved_index(self, round_path: Path) -> bool:
        """True when any ancestor dir (inside the watch root) holds a pending index."""
        try:
            relative = round_path.parent.relative_to(self.watch_root)
        except ValueError:
            return False
        directory = self.watch_root
        for part in relative.parts:
            directory = directory / part
            if directory in self._failed_index_dirs:
                return True
        return False

    def _stable_files(self, pattern: str) -> list[Path]:
        try:
            paths = [path for path in self.watch_root.rglob(pattern) if path.is_file()]
        except OSError as error:
            self._set_directory_error("directory_scan_failed", error)
            return []
        stable: list[Path] = []
        with self._lock:
            # 状态按 (pattern, path) 分槽：三个模式各扫各的稳定计数；本模式消失
            # 的文件才清槽，不能冲掉其它模式的计数。
            current = {(pattern, path) for path in paths}
            self._states = {
                key: state
                for key, state in self._states.items()
                if key[0] != pattern or key in current
            }
            for path in paths:
                key = (pattern, path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                previous = self._states.get(key)
                if previous and previous.size == stat.st_size and previous.mtime_ns == stat.st_mtime_ns:
                    previous.stable_scans += 1
                else:
                    self._states[key] = _FileState(stat.st_size, stat.st_mtime_ns)
                if self._states[key].stable_scans >= self.stable_scans:
                    stable.append(path)
        return sorted(stable, key=str)

    # ---------------------------------------------------------------- import

    def _import_index(self, index_path: Path, summary: dict) -> set[Path]:
        """Import all rounds of one index; returns the round dirs it covered.

        The covered dirs are returned even when the index is rejected, so a
        fail-closed index also suppresses orphan-import of its round files.
        """
        try:
            payload = index_path.read_bytes()
            index_data = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            # cleaner 批量重写期间读到半成品 → 重试预算，绝不半写 meta。
            # 其目录树下的轮文件同时挂起孤立导入（等 index 就绪）。
            self._failed_index_dirs.add(index_path.parent)
            self._handle_retryable(f"index|{self._proposal_key(index_path)}", error, summary)
            return set()
        if not isinstance(index_data, dict):
            self._reject(
                f"index|{self._proposal_key(index_path)}",
                UnsupportedFormatVersion(f"{index_path.name}: not a JSON object", code="unsupported_version"),
                summary,
            )
            return set()
        covered = {
            self._resolve_round_dir(index_path.parent, source.get("outdir"))
            for source in index_data.get("sources", []) or []
            if isinstance(source, dict)
        }
        if index_data.get("format_version") != SUPPORTED_FORMAT_VERSION:
            # fail-closed：未知版本只进台账，不产 meta（§2.6 质量门 1）。
            self._reject(
                f"index|{self._proposal_key(index_path)}",
                UnsupportedFormatVersion(
                    f"{index_path.name}: format_version={index_data.get('format_version')!r} unsupported",
                    code="unsupported_version",
                ),
                summary,
            )
            return covered

        index_sha = _sha256_bytes(payload)
        index_dirname = index_path.parent.name
        index_rel = self._relative(index_path)
        for source in index_data.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            source_file = str(source.get("source", ""))
            round_dir = self._resolve_round_dir(index_path.parent, source.get("outdir"))
            for round_entry in source.get("rounds", []) or []:
                if not isinstance(round_entry, dict):
                    continue
                dedup_key = f"{source_file}|{round_entry.get('round')}|{index_dirname}"
                try:
                    outcome = self._import_round(
                        dedup_key,
                        round_entry=round_entry,
                        source_file=source_file,
                        round_dir=round_dir,
                        index_rel=index_rel,
                        index_sha=index_sha,
                        index_params=index_data.get("params"),
                        index_generator=index_data.get("generator"),
                        source_discarded=source.get("discarded"),
                        source_cut_stats=source.get("per_addr_cut_stats"),
                    )
                except RetryableIngestionError as error:
                    self._handle_retryable(dedup_key, error, summary)
                    continue
                except UnsupportedFormatVersion as error:
                    self._reject(dedup_key, error, summary)
                    continue
                summary[outcome] = summary.get(outcome, 0) + 1
        return covered

    def _resolve_round_dir(self, index_dir: Path, outdir: object) -> Path:
        if isinstance(outdir, str) and outdir not in ("", "."):
            candidate = index_dir / outdir
            if candidate.is_dir():
                return candidate
        return index_dir

    def _import_round(
        self,
        dedup_key: str,
        *,
        round_entry: dict,
        source_file: str,
        round_dir: Path,
        index_rel: str,
        index_sha: str,
        index_params: object,
        index_generator: object,
        source_discarded: object,
        source_cut_stats: object,
    ) -> str:
        round_file = str(round_entry.get("file", ""))
        round_path = round_dir / round_file
        if not round_path.is_file():
            log.info("external telemetry round file missing key=%s path=%s", dedup_key, round_path)
            return "skipped"
        try:
            payload = round_path.read_bytes()
        except OSError as error:
            raise RetryableIngestionError(f"round file unreadable: {error}") from error
        content_hash = _sha256_bytes(payload)
        external_id = store.external_run_id(dedup_key)
        previous = store.read_ledger().get(dedup_key)
        existing_meta = store.load_meta(external_id)
        if (
            isinstance(previous, dict)
            and previous.get("content_hash") == content_hash
            and existing_meta is not None
        ):
            # 同内容 skip：旁车可能后到或 merge 重跑变了哈希——先走轻路径补
            # 冻结+更新 meta.sidecars，不触发 content revision（revisions 只跟
            # 轮内容哈希，SIDECARS.md v1）。
            self._refresh_skip_sidecars(external_id, round_dir, round_file, existing_meta)
            return "skipped"

        frame_stats = summarize_frames(payload)
        t_start = float(round_entry.get("t_start", frame_stats["t_min"] or 0.0))
        t_end = float(round_entry.get("t_end", frame_stats["t_max"] or 0.0))
        duration = float(round_entry.get("duration", t_end - t_start))
        if duration <= 0 or frame_stats["frame_lines"] <= 0:
            # 不可信数据不入库，但必须可观测：只记台账，不产 meta。
            raise UnsupportedFormatVersion(
                f"round {round_entry.get('round')}: duration={duration} frames={frame_stats['frame_lines']}",
                code="duration_nonpositive",
            )
        index_targets = round_entry.get("targets", []) or []
        proposal = scenario_proposal_for(round_dir, int(round_entry.get("round", 0)), round_file)
        gates = {
            "format_version": "pass",
            "frames_readable": "pass" if frame_stats["parse_rate"] >= 0.99 else "degraded",
            "target_count": self._target_count_gate(proposal, len(index_targets)),
            "duration_positive": "pass",
        }
        known_issues = ["cleaner_short_respawn_merge"]
        if not index_targets:
            known_issues.append("missing_rounds_index")
        if frame_stats["unsupported_events"]:
            known_issues.append("unsupported_events_present")

        anchor = epoch_anchor_for_source(source_file)
        anchor_start = anchor.get("epoch_start_est")
        if anchor_start is None:
            epoch_window = (0.0, 0.0)
        else:
            epoch_window = (float(anchor_start) + t_start, float(anchor_start) + t_end)
        pairing = pair_runs(epoch_window, proposal.get("label") if proposal else None, self._run_windows())

        meta = {
            "schema_version": store.SCHEMA_VERSION,
            "external_run_id": external_id,
            "user_id": config.DESKTOP_LOCAL_PROFILE,
            "origin": {
                "source_file": source_file,
                "round": round_entry.get("round"),
                "round_file": self._relative(round_path),
                "index_file": index_rel,
                "generator": index_generator,
                "format_version": SUPPORTED_FORMAT_VERSION,
                "params": index_params,
            },
            "fingerprints": {
                "round_sha256": content_hash,
                "round_size": len(payload),
                "round_mtime_ns": self._mtime_ns(round_path),
                "import_parser_version": IMPORT_PARSER_VERSION,
            },
            "time": {
                "t_start": t_start,
                "t_end": t_end,
                "duration": duration,
                "n_frames": round_entry.get("n_frames", frame_stats["frame_lines"]),
                "epoch_anchor": anchor,
                "sampling_note": "~32Hz variable-dt; max gap 1.05s; never assume fixed dt",
            },
            "counts": counts_from_index(round_entry),
            "targets": build_targets(index_targets),
            "quality": {
                "gates": gates,
                "discarded": source_discarded,
                "per_addr_cut_stats": source_cut_stats,
                "known_issues": known_issues,
            },
            # proposal-only：置信度/tie_group/candidates 原样透传，绝不写
            # config/scenario-overrides.json（§4 标签语义）。
            "scenario_proposal": proposal if proposal is not None else {"source": "pending"},
            "pairing": pairing,
            "rollups": {"t2k": t2k_rollup(
                [life for target in index_targets for life in target.get("lives", [])],
                t_start, t_end,
            )},
            "frames_path": store.frames_path(external_id),
            "imported_at": _utc_now(),
            "revisions": [],
        }

        outcome = "imported"
        sidecars_recorded = None
        if isinstance(previous, dict) and previous.get("content_hash") not in (None, content_hash):
            # cleaner 重洗 → 同一 id 下登记 content revision（§2.3）。
            outcome = "revised"
            sidecars_recorded = (existing_meta or {}).get("sidecars")
            meta["revisions"] = list((existing_meta or {}).get("revisions", [])) + [{
                "round_sha256": previous.get("content_hash"),
                "imported_at": previous.get("imported_at"),
                "superseded_at": meta["imported_at"],
            }]

        # 旁车冻结（SIDECARS.md v1）：指纹进 meta.sidecars，副本与帧同目录、保持
        # 原名；旁车哈希变化只刷指纹+副本，不触发 content revision。
        meta["sidecars"] = self._freeze_sidecars(external_id, round_dir, round_file, sidecars_recorded)
        store.write_frozen_frames(external_id, payload)
        store.save_meta(external_id, meta)
        self._update_ledger(lambda ledger: ledger.update({
            dedup_key: {
                "status": "imported",
                "external_run_id": external_id,
                "content_hash": content_hash,
                "imported_at": meta["imported_at"],
                "index_file_seen": index_rel,
                "index_sha256": index_sha,
                "round_file": self._relative(round_path),
                "proposal_path": self._proposal_key(round_dir / "scenario.json"),
                "proposal_mtime_ns": self._mtime_ns(round_dir / "scenario.json"),
            }
        }))
        return outcome

    def _import_orphan_round(self, round_path: Path, summary: dict) -> None:
        """Import a round file whose source directory has no rounds_index.json.

        Upstream batch indexes are rewritten (not appended), so capture copies
        can lose index coverage entirely. The round file and scenario.json
        sidecar are still first-class: import with frame-derived metadata and
        an explicit ``missing_rounds_index`` issue (no tid/lives invented).
        """
        dedup_key = f"{round_path.name}|0|{round_path.parent.name}"
        try:
            payload = round_path.read_bytes()
        except OSError as error:
            self._handle_retryable(dedup_key, error, summary)
            return
        content_hash = _sha256_bytes(payload)
        external_id = store.external_run_id(dedup_key)
        ledger = store.read_ledger()
        previous = ledger.get(dedup_key)
        existing_meta = store.load_meta(external_id)
        if (
            isinstance(previous, dict)
            and previous.get("content_hash") == content_hash
            and existing_meta is not None
        ):
            # 同内容 skip 的旁车补齐与带 index 路径同口径（SIDECARS.md v1）。
            self._refresh_skip_sidecars(external_id, round_path.parent, round_path.name, existing_meta)
            summary["skipped"] += 1
            return
        # 跨 key 同内容去重：cleaner 每次 finalize 覆写共享根下的 rounds_index，
        # 旧会话的轮会失去索引覆盖而落入 orphan 路径（dedup key 与索引路径不同）。
        # 若同一个轮文件（round_file 相对路径一致）此前已由带索引路径导入，则按
        # 同内容 skip 处理，不再二次导入——否则 orphan 贫瘠 meta（targets 空）会以
        # 更新的 imported_at 在选轮时挤掉完整版本。按 round_file 限定只合并「同一
        # 物理轮」的两种身份，不会误并不同目录下内容恰好相同的两个轮文件。
        round_rel = self._relative(round_path)
        for candidate in ledger.values():
            if not isinstance(candidate, dict):
                continue
            if candidate.get("status") != "imported":
                continue
            if candidate.get("content_hash") != content_hash:
                continue
            if candidate.get("round_file") != round_rel:
                continue
            duplicate_id = candidate.get("external_run_id")
            if not isinstance(duplicate_id, str) or duplicate_id == external_id:
                continue
            duplicate_meta = store.load_meta(duplicate_id)
            if not isinstance(duplicate_meta, dict):
                continue
            self._refresh_skip_sidecars(
                duplicate_id, round_path.parent, round_path.name, duplicate_meta,
            )
            summary["skipped"] += 1
            return
        frame_stats = summarize_frames(payload)
        t_start = frame_stats["t_min"] or 0.0
        t_end = frame_stats["t_max"] or 0.0
        proposal = scenario_proposal_for(round_path.parent, 0, round_path.name)
        proposal = proposal if proposal is not None else {"source": "pending"}
        anchor = epoch_anchor_for_source(round_path.name)
        anchor_start = anchor.get("epoch_start_est")
        epoch_window = (
            (float(anchor_start) + t_start, float(anchor_start) + t_end) if anchor_start is not None else (0.0, 0.0)
        )
        meta = {
            "schema_version": store.SCHEMA_VERSION,
            "external_run_id": external_id,
            "user_id": config.DESKTOP_LOCAL_PROFILE,
            "origin": {
                "source_file": round_path.stem + ".jsonl",
                "round": 1,
                "round_file": self._relative(round_path),
                "index_file": None,
                "generator": None,
                "format_version": SUPPORTED_FORMAT_VERSION,
                "params": None,
            },
            "fingerprints": {
                "round_sha256": content_hash,
                "round_size": len(payload),
                "round_mtime_ns": self._mtime_ns(round_path),
                "import_parser_version": IMPORT_PARSER_VERSION,
            },
            "time": {
                "t_start": t_start,
                "t_end": t_end,
                "duration": t_end - t_start,
                "n_frames": frame_stats["frame_lines"],
                "epoch_anchor": anchor,
                "sampling_note": "~32Hz variable-dt; max gap 1.05s; never assume fixed dt",
            },
            "counts": {
                "n_targets": None, "n_moving_targets": None, "motion_mix": None,
                "spawns": 0, "deaths": 0, "timeouts": 0, "timeout_rate": None,
            },
            "targets": [],
            "quality": {
                "gates": {
                    "format_version": "unknown",
                    "frames_readable": "pass" if frame_stats["parse_rate"] >= 0.99 else "degraded",
                    "target_count": "unknown",
                    "duration_positive": "pass" if t_end > t_start and frame_stats["frame_lines"] > 0 else "fail",
                },
                "discarded": None,
                "per_addr_cut_stats": None,
                "known_issues": ["missing_rounds_index", "cleaner_short_respawn_merge"],
            },
            "scenario_proposal": proposal,
            "pairing": pair_runs(epoch_window, proposal.get("label"), self._run_windows()),
            "rollups": {"t2k": {"n": 0, "p10": None, "p50": None, "p90": None, "mean": None,
                                "max": None, "over_2s_share": None,
                                "definition": "unavailable without rounds_index target metadata"}},
            "frames_path": store.frames_path(external_id),
            "imported_at": _utc_now(),
            "revisions": [],
        }
        # 旁车冻结（SIDECARS.md v1）：与带 index 路径同口径。
        meta["sidecars"] = self._freeze_sidecars(external_id, round_path.parent, round_path.name, None)
        store.write_frozen_frames(external_id, payload)
        store.save_meta(external_id, meta)
        self._update_ledger(lambda ledger: ledger.update({
            dedup_key: {
                "status": "imported",
                "external_run_id": external_id,
                "content_hash": content_hash,
                "imported_at": meta["imported_at"],
                "index_file_seen": None,
                "index_sha256": None,
                "round_file": self._relative(round_path),
                "proposal_path": self._proposal_key(round_path.parent / "scenario.json"),
                "proposal_mtime_ns": self._mtime_ns(round_path.parent / "scenario.json"),
            }
        }))
        summary["imported"] += 1

    # -------------------------------------------------------------- sidecars

    def _freeze_sidecars(
        self, external_id: str, round_dir: Path, round_file: str, recorded: object,
    ) -> dict[str, dict]:
        """Freeze this round's stable sidecars; return the meta.sidecars section.

        旁车合同（FPSAimTrainer analysis/external/SIDECARS.md v1）：views/inputs
        按轮号对应轮文件；bb/merge_manifest 是轮目录级共享件（该目录任一轮导入
        时各随轮冻结一份，meta 各轮都记指纹）。只消费稳定检测通过的源件；指纹
        与 recorded 一致时不重读不重冻（幂等），缺/变才写新副本；源件未稳定
        （merge 重写中）沿用 recorded 旧指纹，稳定后下轮 scan 补齐。
        """
        stable = self._scan_sidecars.get(round_dir, {})
        section: dict[str, dict] = {}
        for key, name in _sidecar_names(round_file):
            path = round_dir / name
            stat = stable.get(name)
            recorded_fp = recorded.get(key) if isinstance(recorded, dict) else None
            known = isinstance(recorded_fp, dict) and recorded_fp.get("present") is True
            absent = {"present": False, "sha256": None, "size": None}
            if stat is None:
                section[key] = recorded_fp if known else absent
                continue
            if known and self._hashed_sidecars.get(path) == stat:
                # 自上次哈希起 size+mtime 未变：指纹必然一致，不重读大文件。
                section[key] = recorded_fp
                continue
            try:
                payload = path.read_bytes()
            except OSError:
                section[key] = recorded_fp if known else absent
                continue
            digest = _sha256_bytes(payload)
            if (
                known
                and digest == recorded_fp.get("sha256")
                and len(payload) == recorded_fp.get("size")
            ):
                self._hashed_sidecars[path] = stat
                section[key] = recorded_fp  # 内容未变：不重写冻结副本
                continue
            fingerprint = {"present": True, "sha256": digest, "size": len(payload)}
            try:
                store.write_frozen_sidecar(external_id, name, payload)
            except OSError:
                # 写副本失败（store 内 mkdir/tmp/replace，如 Windows 杀软锁住
                # 目标文件触发 PermissionError）：降级保留 recorded 指纹并计入
                # failed；缓存不写入，下轮 scan 的 size+mtime 快路径不生效，
                # 会重试写副本。失败必须与读失败一样不逃出逐轮错误隔离。
                self._sidecar_write_failures += 1
                section[key] = recorded_fp if known else absent
                continue
            self._hashed_sidecars[path] = stat
            section[key] = fingerprint
        return section

    def _refresh_skip_sidecars(
        self, external_id: str, round_dir: Path, round_file: str, meta: dict,
    ) -> None:
        """Skip-path light refresh: freeze late/changed sidecars, patch meta.sidecars.

        轻路径（同 §2.5 的读-改-写口径）：只动 sidecars 段，不重建 meta、不动
        revisions/台账；指纹无变化时连 meta 都不重写（幂等，§SIDECARS 后到件）。
        """
        recorded = meta.get("sidecars")
        section = self._freeze_sidecars(external_id, round_dir, round_file, recorded)
        if section != recorded:
            meta["sidecars"] = section
            store.save_meta(external_id, meta)

    # ------------------------------------------------------------- proposals

    def _patch_scenario_proposals(self, scenario_files: list[Path], summary: dict) -> None:
        """Single-field patch when a scenario.json appears/changes after import.

        实测 morning 的 scenario.json 曾滞后于轮文件生成；meta 先以 pending
        落盘，旁车稳定后读-改-写补齐（仍原子，§2.5）。判定基准是各 run 台账
        记录的旁车 mtime：mtime 未变且 proposal 已非 pending → 不动 meta。
        """
        for scenario_path in scenario_files:
            mtime_ns = self._mtime_ns(scenario_path)
            proposal_key = self._proposal_key(scenario_path)
            with self._ledger_lock:
                ledger = store.read_ledger()
                patched = 0
                for key, entry in ledger.items():
                    if not isinstance(entry, dict) or entry.get("status") != "imported":
                        continue
                    if entry.get("proposal_path") != proposal_key:
                        continue
                    if entry.get("proposal_mtime_ns") == mtime_ns:
                        continue
                    meta = store.load_meta(str(entry.get("external_run_id", "")))
                    if meta is None:
                        continue
                    origin = meta.get("origin", {})
                    # orphan 轮（无 index）的 origin.round 是合成值，只能按
                    # round_file 名匹配旁车条目，防止错配到别的轮的标签。
                    round_number = int(origin.get("round") or 0) if origin.get("index_file") is not None else 0
                    round_dir = self._round_dir_of(origin.get("round_file"))
                    proposal = scenario_proposal_for(
                        round_dir, round_number, Path(str(origin.get("round_file") or "")).name,
                    )
                    if proposal is None:
                        continue  # 旁车还没写到本轮（半写/缺 entry）：mtime 保持旧值，下轮再试
                    meta["scenario_proposal"] = proposal
                    store.save_meta(str(entry["external_run_id"]), meta)
                    entry["proposal_mtime_ns"] = mtime_ns
                    patched += 1
                if patched:
                    store.write_ledger(ledger)
            if patched:
                summary["proposal_patches"] += patched
                log.info(
                    "external telemetry scenario proposals patched runs=%s scenario=%s",
                    patched, scenario_path.name,
                )

    # -------------------------------------------------------------- rejects

    def _reject(self, key: str, error: UnsupportedFormatVersion, summary: dict) -> None:
        def mutate(ledger: dict) -> bool:
            previous = ledger.get(key)
            if isinstance(previous, dict) and previous.get("code") == error.code:
                return False
            ledger[key] = {
                "status": "rejected",
                "code": error.code,
                "detail": str(error)[:240],
                "seen_at": _utc_now(),
            }
            return True

        if self._update_ledger(mutate):
            log.info("external telemetry rejected key=%s code=%s error=%s", key, error.code, error)
        summary["rejected"] += 1

    def _handle_retryable(self, key: str, error: BaseException, summary: dict) -> None:
        summary["failed"] += 1
        budget = self._retry_budgets.setdefault(key, _RetryBudget())
        budget.failures += 1
        exhausted = time.monotonic() - budget.first_failure_monotonic >= _RETRY_WINDOW_SECONDS
        if exhausted:
            self._retry_budgets.pop(key, None)
            log.warning("external telemetry gave up after retention window key=%s error=%s", key, error)
        else:
            log.warning("external telemetry retrying key=%s error=%s", key, error)
        self._last_error = str(error)[:240]

    # -------------------------------------------------------------- helpers

    def _update_ledger(self, mutate: Callable[[dict], object]) -> bool:
        """Read-modify-write the ledger atomically; returns mutate's verdict."""
        with self._ledger_lock:
            ledger = store.read_ledger()
            verdict = mutate(ledger)
            store.write_ledger(ledger)
            return bool(verdict)

    def _run_windows(self) -> list[dict]:
        if self._scan_runs is None:
            self._scan_runs = run_windows_from_store()
        return self._scan_runs

    def _target_count_gate(self, proposal: dict | None, n_targets: int) -> str:
        expected = known_target_count(proposal.get("label")) if isinstance(proposal, dict) else None
        if expected is None or not n_targets:
            return "unknown"
        return "pass" if expected == n_targets else "warn"

    def _round_dir_of(self, round_file: object) -> Path:
        if not round_file:
            return self.watch_root
        return (self.watch_root / str(round_file)).parent

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.watch_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _proposal_key(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        return str(resolved).casefold()

    def _mtime_ns(self, path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _set_directory_error(self, code: str, error: OSError | None = None) -> None:
        previous = self._directory_state
        self._directory_state = code
        self._last_error = code
        if previous != code:
            suffix = f": {error}" if error else ""
            log.warning("External telemetry watcher directory state=%s root=%s%s", code, self.watch_root, suffix)

    def _set_directory_ready(self) -> None:
        if self._directory_state not in {"unknown", "ready"}:
            log.info("External telemetry watcher directory recovered root=%s", self.watch_root)
        self._directory_state = "ready"
        self._last_error = None

    # ------------------------------------------------------------- lifecycle

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "version": "external_telemetry_watcher.v1",
                "source": self.source,
                "running": self.is_running(),
                "directory_state": self._directory_state,
                "last_scan_epoch_ms": self._last_scan_epoch_ms,
                "last_error": self._last_error,
                "stable_scans_required": self.stable_scans,
                "tracked_files": len(self._states),
                "totals": dict(self._totals),
                "last_scan_summary": dict(self._last_summary) if self._last_summary else None,
                "recent_candidates": list(self._recent_candidates),
                "retrying_count": len(self._retry_budgets),
            }

    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="external-telemetry-ingest", daemon=True)
        self._thread.start()
        log.info("External telemetry watcher started root=%s source=%s", self.watch_root, self.source)

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
                log.exception("External telemetry watcher scan failed root=%s", self.watch_root)
            self._stop.wait(self.poll_interval)


class ExternalTelemetryService:
    """Own the watcher configured via /external-telemetry and allow hot reconfig."""

    def __init__(self, *, poll_interval: float = 1.0, source: str = "automatic") -> None:
        self._poll_interval = poll_interval
        self._source = source
        self._lock = threading.RLock()
        self._watcher: ExternalTelemetryWatcher | None = None
        watch_root = store.get_watch_root()
        if watch_root is not None:
            self._watcher = ExternalTelemetryWatcher(
                watch_root, poll_interval=poll_interval, source=source,
            )

    @property
    def watch_root(self) -> Path | None:
        with self._lock:
            return self._watcher.watch_root if self._watcher else None

    def reconfigure(self, watch_root: Path | None) -> bool:
        with self._lock:
            was_running = self._watcher.is_running() if self._watcher else False
            if self._watcher is not None:
                self._watcher.stop()
            if watch_root is None:
                self._watcher = None
            else:
                self._watcher = ExternalTelemetryWatcher(
                    watch_root, poll_interval=self._poll_interval, source="confirmed",
                )
                if was_running:
                    self._watcher.start()
        log.info("External telemetry reconfigured root=%s", watch_root)
        return True

    def start(self) -> None:
        with self._lock:
            if self._watcher is not None:
                self._watcher.start()
            else:
                log.info(
                    "External telemetry service started without a watch root "
                    "(configure via PUT /api/external-telemetry)"
                )

    def stop(self) -> None:
        with self._lock:
            if self._watcher is not None:
                self._watcher.stop()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            if self._watcher is None:
                return {
                    "version": "external_telemetry_watcher.v1",
                    "source": self._source,
                    "configured": False,
                }
            snapshot = self._watcher.diagnostics()
            snapshot["configured"] = True
            return snapshot


def create_external_telemetry_service(*, poll_interval: float | None = None) -> ExternalTelemetryService:
    if poll_interval is None:
        poll_interval = float(getattr(config, "KOVAAK_WATCH_POLL_SECONDS", 1.0))
    return ExternalTelemetryService(poll_interval=poll_interval)
