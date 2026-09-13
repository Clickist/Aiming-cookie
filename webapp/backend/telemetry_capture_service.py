"""Desktop-managed KovaaK's 目标真值遥测采集（telemetry_capture/ 的伴生进程管理）.

点点拍板（2026-09-06）：采集工具随产品分发并自动运行。本服务把仓库
``telemetry_capture/`` 的三个采集脚本作为桌面后端的伴生子进程常驻管理：

- 脚本自带 ``--wait`` / 重附着循环：服务启动即拉起，游戏出现即采、退出即停写、
  重开游戏自动重新附着（每次附着 = 新文件新 clock_map，FORMAT §2.4-5）；
- 监控线程观察游戏进程出现→消失的下降沿，把会话目录里已静止的原始 JSONL 收尾：
  ``cleaner.py`` 清洗分轮 → ``merge_channels.py`` 并旁车 → 托管 cleaned 根；
- 托管 cleaned 根在用户未配置 watch 根时由 ``ensure_managed_watch_root`` 自动
  指给外部遥测导入器（已配置则绝不覆盖）；
- AC 崩溃/被杀时残留的孤儿采集进程与会话目录，由下次启动的扫尾回收（孤儿按
  pid + 进程创建时间精确匹配后才终止，避免误杀回收的 pid）。

全部故障软化：采集/收尾的任何失败只记日志与诊断快照，绝不影响主摄取链路。
安全阀：环境变量 ``AIMING_COOKIE_TELEMETRY_CAPTURE=0`` 整体停用。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, file_store
from .kovaaks_settings_inject import find_game_processes

log = logging.getLogger(__name__)

DISABLE_ENV = "AIMING_COOKIE_TELEMETRY_CAPTURE"
SCRIPTS_DIR_ENV = "TELEMETRY_CAPTURE_DIR"
_MARKER_SCRIPT = "tp1.py"
_RAW_GLOB = "target_poll_out_*.jsonl"
_CAMERA_GLOB = "camera_probe_out_*.jsonl"
_INPUT_NAME = "input_log.jsonl"
_PROCESSED_SUBDIR = "processed"
_PIDS_FILE = "pids.json"
_FINALIZE_TIMEOUT_SECONDS = 300.0
_TERMINATE_TIMEOUT_SECONDS = 5.0


def managed_capture_roots(data_root: Path | None = None) -> tuple[Path, Path]:
    """返回 (会话根, 托管 cleaned 根)，均位于 DATA_ROOT 下。"""
    base = Path(data_root) if data_root else config.DATA_ROOT
    root = base / "external-capture"
    return root / "sessions", root / "cleaned"


def resolve_scripts_dir() -> Path | None:
    """定位 telemetry_capture/ 脚本目录（env > 打包数据 > 仓库）。"""
    candidates: list[Path] = []
    override = os.environ.get(SCRIPTS_DIR_ENV, "").strip()
    if override:
        candidates.append(Path(override))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "telemetry_capture")
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(repo_root / "telemetry_capture")
    for candidate in candidates:
        if (candidate / _MARKER_SCRIPT).is_file():
            return candidate
    return None


def run_telemetry_child(script_name: str, argv: list[str]) -> None:
    """desktop-runtime-entry 的 ``--telemetry-child`` 模式。

    打包态 sys.executable 是 frozen 运行时本体，不能 ``python script.py``；
    与 --visual-worker 同款先例：主程序以脚本为主入口重新执行。脚本依赖按
    文件名同目录解析（tp1/names），先把目录挂上 sys.path。
    """
    import runpy

    scripts_dir = resolve_scripts_dir()
    if scripts_dir is None:
        raise SystemExit("telemetry capture scripts not found")
    script_path = scripts_dir / script_name
    sys.path.insert(0, str(scripts_dir))
    sys.argv = [str(script_path), *argv]
    runpy.run_path(str(script_path), run_name="__main__")


def ensure_managed_watch_root() -> bool:
    """watch 根未配置时自动指向托管 cleaned 根；已配置/失败一律不动现状。"""
    try:
        from . import external_telemetry_store as store

        if store.get_watch_root() is not None:
            return False
        _, cleaned_root = managed_capture_roots()
        store.save_watch_root(str(cleaned_root))
        log.info("external telemetry watch root auto-configured to managed root")
        return True
    except Exception:
        log.exception("managed external-telemetry watch root auto-config failed")
        return False


def create_telemetry_capture_service() -> TelemetryCaptureService | None:
    try:
        return TelemetryCaptureService()
    except Exception:
        log.exception("telemetry capture service creation failed")
        return None


def _filetime_of(handle: int) -> int | None:
    """进程创建时间（FILETIME 100ns），用于 pid 复用防误杀；不可得返回 None。"""
    class _FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]

    kernel32 = ctypes.windll.kernel32
    creation = _FT(); exit_ = _FT(); kernel = _FT(); user = _FT()
    if not kernel32.GetProcessTimes(
        handle, ctypes.byref(creation), ctypes.byref(exit_),
        ctypes.byref(kernel), ctypes.byref(user),
    ):
        return None
    return (creation.hi << 32) | creation.lo


class TelemetryCaptureService:
    """常驻三采集通道 + 游戏退场收尾。见模块 docstring。"""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        scripts_dir: Path | None = None,
        poll_interval: float = 3.0,
        game_processes_fn=None,
        finalize_grace_seconds: float = 8.0,
        finalize_retry_seconds: float = 60.0,
    ) -> None:
        self.data_root = Path(data_root) if data_root else config.DATA_ROOT
        self.sessions_root, self.cleaned_root = managed_capture_roots(self.data_root)
        self.scripts_dir = Path(scripts_dir) if scripts_dir else resolve_scripts_dir()
        self.poll_interval = poll_interval
        # 退场缓冲：采集脚本松开原始件句柄、游戏侧落盘收尾都需要几秒。
        self.finalize_grace_seconds = finalize_grace_seconds
        # 收尾失败（如 WinError 32 句柄竞争）后的自动重试间隔。
        self.finalize_retry_seconds = finalize_retry_seconds
        self._game_processes_fn = game_processes_fn or find_game_processes
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._children: dict[str, subprocess.Popen] = {}
        # 已上报死亡的子进程 role：避免每次轮询重复 log/落盘。
        self._dead_children: set[str] = set()
        self._session_dir: Path | None = None
        self._game_present = False
        self._state = "idle"
        self._last_error: str | None = None
        self._last_finalize: dict[str, object] = {}
        self._next_finalize_retry_epoch = 0.0

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> bool:
        """常驻拉起三通道 + 起监控线程。返回是否实际启动。"""
        if os.environ.get(DISABLE_ENV) == "0":
            log.info("telemetry capture disabled via %s", DISABLE_ENV)
            return False
        if self.scripts_dir is None or not (self.scripts_dir / _MARKER_SCRIPT).is_file():
            self._state = "unavailable"
            self._last_error = "scripts_dir_not_found"
            self._persist_diagnostics()
            log.warning("telemetry capture scripts not found; capture stays off")
            return False
        try:
            self.sessions_root.mkdir(parents=True, exist_ok=True)
            self.cleaned_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._state = "unavailable"
            self._last_error = f"mkdir_failed: {error}"
            self._persist_diagnostics()
            log.warning("telemetry capture session dirs unavailable: %s", error)
            return False

        self._sweep_finished_sessions()
        if self._spawn_session():
            self._state = "capturing"
        self._thread = threading.Thread(
            target=self._monitor_loop, name="telemetry-capture", daemon=True,
        )
        self._thread.start()
        self._persist_diagnostics()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval + 5.0)
            self._thread = None
        self._terminate_children()
        if self._session_dir is not None and any(self._session_dir.glob(_RAW_GLOB)):
            # 运行中直接关 AC：原始件留给下次启动扫尾收尾。
            self._state = "pending_finalize"
        else:
            self._state = "idle"
        self._persist_diagnostics()

    def diagnostics(self) -> dict[str, object]:
        session = self._session_dir.name if self._session_dir else None
        return {
            "version": "telemetry_capture.v1",
            "state": self._state,
            "scripts_found": self.scripts_dir is not None
            and (self.scripts_dir / _MARKER_SCRIPT).is_file(),
            "game_present": self._game_present,
            "session": session,
            "children": sorted(self._children),
            "last_error": self._last_error,
            "last_finalize": self._last_finalize,
        }

    # ------------------------------------------------------------------ 内部

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            self._check_children_alive()
            try:
                present = bool(self._game_processes_fn())
            except Exception as error:
                # 检测失败必须落盘可见（只记内存会让错误在轮询里静默打转）。
                log.exception("telemetry capture game detection failed")
                self._last_error = f"game_detect_failed: {error}"
                self._persist_diagnostics()
                continue
            if present == self._game_present:
                # 游戏不在场且会话仍有未归档原始件：收尾失败自动重试。
                if (not present and self._session_dir is not None
                        and any(self._session_dir.glob(_RAW_GLOB))
                        and time.time() >= self._next_finalize_retry_epoch):
                    self._next_finalize_retry_epoch = time.time() + self.finalize_retry_seconds
                    self._run_finalize()
                continue
            self._game_present = present
            self._persist_diagnostics()
            if not present:
                # 退场缓冲：等采集脚本退出采样循环、松开原始件句柄。
                self._stop_event.wait(self.finalize_grace_seconds)
                self._state = "finalizing"
                self._persist_diagnostics()
                self._run_finalize()
                self._state = "capturing"
                self._persist_diagnostics()

    def _check_children_alive(self) -> None:
        """采集子进程死亡的可见性（只记录不重启）。

        子进程 stdout/stderr 都是 DEVNULL，任何崩溃（未知 exe 版本、缺 numpy 等）
        本会完全静默消失，设置页诊断看不出异常。这里在轮询里主动 poll：退出即
        log.error 落盘 + 记入 _last_error + 持久化诊断。重启语义需产品决定，不在
        此处自动重启。
        """
        for role, child in self._children.items():
            if role in self._dead_children:
                continue
            returncode = child.poll()
            if returncode is None:
                continue
            self._dead_children.add(role)
            self._last_error = f"child_exited: {role} rc={returncode}"
            log.error(
                "telemetry capture child exited role=%s pid=%s returncode=%s",
                role, child.pid, returncode,
            )
            self._persist_diagnostics()

    def _run_finalize(self) -> None:
        try:
            self._finalize_dir(self._session_dir)
        except Exception as error:
            self._last_error = f"finalize_failed: {error}"
            log.exception("telemetry capture finalize failed")

    def _spawn_session(self) -> bool:
        session_dir = self.sessions_root / datetime.now().strftime("session-%y%m%d-%H%M%S")
        try:
            session_dir.mkdir(parents=True)
        except OSError as error:
            self._last_error = f"session_mkdir_failed: {error}"
            return False
        spawned: dict[str, subprocess.Popen] = {}
        try:
            spawned["target"] = self._spawn_process(self._child_argv("target_poll2.py") + [
                "--run", "--wait", "--out-dir", str(session_dir),
            ])
            spawned["camera"] = self._spawn_process(self._child_argv("camera_probe.py") + [
                "--run", "--wait", "--out-dir", str(session_dir),
            ])
            spawned["input"] = self._spawn_process(self._child_argv("input_logger.py") + [
                str(session_dir / _INPUT_NAME),
            ])
        except OSError as error:
            self._last_error = f"spawn_failed: {error}"
            for child in spawned.values():
                self._terminate_process(child)
            return False
        self._children = spawned
        self._dead_children = set()
        self._session_dir = session_dir
        self._write_pids_file()
        log.info(
            "telemetry capture session started dir=%s pids=%s",
            session_dir.name, {role: child.pid for role, child in spawned.items()},
        )
        return True

    def _spawn_process(self, argv: list[str]) -> subprocess.Popen:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # 自适应偏移表的用户缓存必须落用户数据目录：打包版内嵌表只读，写不进去
        child_env = dict(os.environ,
                         AIMING_COOKIE_OFFSETS_CACHE=str(config.DATA_ROOT / "offsets.local.json"))
        return subprocess.Popen(  # noqa: S603 - argv 由本模块固定拼装
            argv,
            cwd=str(self.scripts_dir),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def _child_argv(self, script_name: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--telemetry-child", script_name]
        return [sys.executable, str(self.scripts_dir / script_name)]

    def _terminate_children(self) -> None:
        for child in self._children.values():
            self._terminate_process(child)
        self._children = {}

    @staticmethod
    def _terminate_process(child: subprocess.Popen) -> None:
        if child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            try:
                child.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                log.warning("telemetry capture child pid=%s unkillable", child.pid)

    def _write_pids_file(self) -> None:
        if self._session_dir is None:
            return
        entries = {}
        for role, child in self._children.items():
            creation = None
            try:
                creation = _filetime_of(int(child._handle))  # noqa: SLF001 - ctypes 句柄
            except Exception:
                creation = None
            entries[role] = {"pid": child.pid, "ctime": creation}
        _atomic_write_json(self._session_dir / _PIDS_FILE, entries)

    def _sweep_finished_sessions(self) -> None:
        """回收旧会话：先杀孤儿采集进程（pid+创建时间双匹配），再补收尾。"""
        try:
            dirs = [d for d in self.sessions_root.iterdir() if d.is_dir()]
        except OSError:
            return
        for old_dir in dirs:
            self._kill_orphans(old_dir)
        for old_dir in dirs:
            if old_dir == self._session_dir:
                continue
            if any(old_dir.glob(_RAW_GLOB)):
                try:
                    self._finalize_dir(old_dir)
                except Exception:
                    log.exception("telemetry capture sweep finalize failed dir=%s", old_dir.name)

    def _kill_orphans(self, session_dir: Path) -> None:
        pids_path = session_dir / _PIDS_FILE
        if os.name != "nt" or not pids_path.is_file():
            return
        try:
            entries = json.loads(pids_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        kernel32 = ctypes.windll.kernel32
        for role, entry in entries.items():
            try:
                pid = int(entry["pid"])
                ctime = int(entry["ctime"])
            except (KeyError, TypeError, ValueError):
                continue
            if ctime <= 0:
                continue
            handle = kernel32.OpenProcess(0x0001 | 0x0800, False, pid)  # TERMINATE|QUERY_LIMITED
            if not handle:
                continue
            try:
                if _filetime_of(handle) == ctime:
                    kernel32.TerminateProcess(handle, 1)
                    log.info("telemetry capture orphan terminated role=%s pid=%s", role, pid)
            finally:
                kernel32.CloseHandle(handle)

    def _finalize_dir(self, session_dir: Path | None) -> None:
        if session_dir is None:
            return
        raw_files = sorted(session_dir.glob(_RAW_GLOB))
        if not raw_files:
            return
        camera = _latest_by_name(session_dir.glob(_CAMERA_GLOB))
        input_log = session_dir / _INPUT_NAME
        use_sidecars = camera is not None and input_log.is_file()
        year = str(datetime.now().year)
        processed_dir = session_dir / _PROCESSED_SUBDIR
        processed_dir.mkdir(exist_ok=True)
        results: dict[str, str] = {}
        merge_status: dict[str, str] = {}
        # cleaner 一次吃本会话全部原始件（index 是覆写式单文件，逐文件跑会让
        # 后一次覆盖前一次的 sources 覆盖），产出一个总 rounds_index.json。
        outcome = self._run_finalize_step(
            self._child_argv("cleaner.py") + [
                str(f) for f in raw_files
            ] + ["--outdir", str(self.cleaned_root)],
            label="cleaner.py",
        )
        index_path = self.cleaned_root / "rounds_index.json"
        if outcome != "ok" or not index_path.is_file():
            for target_file in raw_files:
                results[target_file.stem] = f"cleaner_{outcome}"
            self._last_finalize = {
                "finished_epoch_s": time.time(),
                "session": session_dir.name,
                "sidecars": use_sidecars,
                "merge": merge_status,
                "results": results,
            }
            return
        for target_file in raw_files:
            stem = target_file.stem
            round_dir = self.cleaned_root / stem
            # 旁车（views/inputs）是增强不是门槛：merge fail-closed 拒绝对齐时
            # 轮次照常入库，旁车缺失记录在案；原始件不重试（结果确定性相同）。
            if use_sidecars:
                outcome = self._run_finalize_step(
                    self._child_argv("merge_channels.py") + [
                        "--round-dir", str(round_dir),
                        "--camera", str(camera),
                        "--input", str(input_log),
                        "--year", year,
                    ],
                    label="merge_channels.py",
                )
                merge_status[stem] = "ok" if outcome == "ok" else outcome
            else:
                merge_status[stem] = "skipped_missing_channels"
            results[stem] = "ok"
            try:
                os.replace(target_file, processed_dir / target_file.name)
            except OSError as error:
                # 归档是簿记不是数据门槛（轮次此刻已入库）；失败留给自动重试。
                self._last_error = f"archive_pending: {error}"
                results[stem] = "ok_archive_pending"
        self._last_finalize = {
            "finished_epoch_s": time.time(),
            "session": session_dir.name,
            "sidecars": use_sidecars,
            "merge": merge_status,
            "results": results,
        }
        log.info(
            "telemetry capture finalize session=%s results=%s merge=%s",
            session_dir.name, results, merge_status,
        )

    def _run_finalize_step(self, argv: list[str], *, label: str) -> str:
        """跑单步收尾子进程；stdout/stderr 落会话 finalize.log；失败折损为返回码。"""
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = subprocess.run(  # noqa: S603 - argv 由本模块固定拼装
                argv,
                cwd=str(self.scripts_dir),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_FINALIZE_TIMEOUT_SECONDS,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._last_error = f"finalize_step_failed: {error}"
            return "error"
        self._append_finalize_log(label, completed)
        if completed.returncode != 0:
            self._last_error = f"finalize_step_exit={completed.returncode}"
            return f"exit_{completed.returncode}"
        return "ok"

    def _append_finalize_log(self, label: str, completed: subprocess.CompletedProcess) -> None:
        if self._session_dir is None:
            return
        log_path = self._session_dir / "finalize.log"
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"===== {label} exit={completed.returncode}\n")
                if completed.stdout:
                    stream.write(completed.stdout[-8000:])
                if completed.stderr:
                    stream.write(completed.stderr[-8000:])
        except OSError:
            pass

    def _persist_diagnostics(self) -> None:
        snapshot = self.diagnostics()
        snapshot.pop("session", None)  # 会话名不入诊断导出（与 watcher 同款脱敏思路）
        try:
            file_store.write_json("diagnostics/telemetry-capture.json", snapshot)
        except Exception:
            log.exception("telemetry capture diagnostics write failed")


def _latest_by_name(paths) -> Path | None:
    ordered = sorted(paths, key=lambda p: p.name)
    return ordered[-1] if ordered else None


def _atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
