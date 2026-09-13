"""Desktop 启动时自动开启 KovaaK's 统计导出（Challenge Completion）。

Aiming Cookie 的数据摄取依赖 KovaaK 每局落盘 stats CSV + .perf 文件；这由 PUS
（PrimaryUserSettings.json，位于 <安装根>/FPSAimTrainer/Saved/SaveGames/）中的
SaveStatistics=true + StatsExportLevel=1（Challenge Completion）两键控制。用户没开
它时 History 会一直空白 —— 这里在 watcher 启动等时机自动保底开启（点点拍板：
直接帮用户打开）。

注入语义全部来自同目录 vendor 的 kovaaks_settings_inject.py（来源：
Desktop/FPSAimTrainer 仓库 analysis/migration_tool/，零依赖单文件；自带游戏进程
检测、写前备份、原子写 + 结构无损断言、幂等 no-op）。本模块只做薄包装：

- 一切失败 fail-soft（绝不阻塞/中断 AC 启动），结果与原因写入 backend.log；
- 游戏未运行时直接写入（注入器自带备份 + 原子写 + 幂等 no-op）；
- 游戏运行中且 PUS 未达标时硬重启 KovaaK（点点拍板：杀游戏进程 → 写 PUS →
  重新拉起，不给确认弹窗）。游戏会话内/退出时会用内存值整文件回写，所以必须
  先让游戏退出才写，不能带进程写；
- PUS 不存在（未安装/未运行过游戏）或缺少目标键（跨版本 PUS）时跳过；
- stdout 全程捕获：桌面运行时 stdout 是 Tauri 的 ready 协议通道，注入器的
  print 输出一律转入日志，不得外泄。

硬重启的进程检测/杀/拉起都走本模块的可注入缝（``_find_game_processes`` /
``_kill_game_processes`` / ``_launch_kovaak``），测试不触碰真实进程；进程名来源
与注入器 ``find_game_processes`` 一致（FPSAimTrainer 前缀），只杀游戏本体进程。
重启后进入退避窗口：N 分钟内复查若发现仍不对则只记日志、不二次重启，防止
「写不进 → 反复重启」死循环。
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from . import kovaaks_settings_inject as injector

log = logging.getLogger(__name__)

# 与 kovaaks_settings_inject.PRESET_AIMINGCOOKIE 一致的两键目标值。
_TARGET_BOOLEAN_KEY = "EBooleanSettingId::SaveStatistics"
_TARGET_INTEGER_KEY = "EIntegerSettingId::StatsExportLevel"
_TARGET_EXPORT_LEVEL = 1  # Challenge Completion（None=0, or Reset=2, Always=3）

_SAVEGAMES_RELATIVE = Path("FPSAimTrainer") / "Saved" / "SaveGames"

# 结果类别（调用方与日志使用；保持平铺字符串便于 grep backend.log）。
RESULT_ALREADY_OK = "already_ok"                    # 两键已是目标值，未写盘
RESULT_FIXED = "fixed"                              # 本次注入修复（已写 + 备份）
RESULT_HARD_RESTARTED = "hard_restarted"            # 游戏运行中：杀/写/拉起已完成
RESULT_RESTART_BACKOFF = "restart_backoff"          # 退避窗口内，抑制二次重启
RESULT_RESTART_FAILED = "restart_failed"            # 杀进程或拉起失败（fail-soft）
RESULT_SKIPPED_GAME_RUNNING = "skipped_game_running"  # KovaaK 运行中，未硬重启时跳过
RESULT_FILE_MISSING = "file_missing"                # PUS 不存在
RESULT_KEYS_MISSING = "keys_missing"                # PUS 缺少目标键（跨版本）
RESULT_NO_INSTALL = "no_install"                    # 无法定位 KovaaK 安装根
RESULT_ERROR = "error"

# 硬重启参数。
_HARD_RESTART_BACKOFF_SECONDS = 120.0   # 重启后 N 秒内不再二次硬重启（防死循环）
_GAME_EXIT_TIMEOUT_SECONDS = 10.0       # 杀进程后等待游戏完全退出写盘的上限
_GAME_EXIT_POLL_SECONDS = 0.5
# Steam 应用 ID（KovaaK's）；与 backend.config._STEAM_APP_ID 一致，仅供 exe 缺失时
# 的 steam:// 深链兜底使用。
_STEAM_APP_ID = "824270"

# 最近一次硬重启时刻（time.monotonic），None 表示本进程尚未硬重启过。
_restart_state_lock = threading.Lock()
_last_hard_restart_monotonic: float | None = None


def _pus_path(install_root: Path) -> Path:
    return install_root / _SAVEGAMES_RELATIVE / injector.PUS_NAME


def _at_target(pus_path: Path) -> tuple[bool, bool, bool]:
    """返回 (是否已达标, SaveStatistics 键存在, StatsExportLevel 键存在)。"""
    text, _ = injector.read_text(pus_path)
    data = injector.parse_pus(text)
    booleans = data.get("booleanSettings", {})
    integers = data.get("integerSettings", {})
    save_ok = booleans.get(_TARGET_BOOLEAN_KEY) is True
    level_ok = integers.get(_TARGET_INTEGER_KEY) == _TARGET_EXPORT_LEVEL
    return (save_ok and level_ok), _TARGET_BOOLEAN_KEY in booleans, (
        _TARGET_INTEGER_KEY in integers
    )


def _run_injector(savegames_parent: Path) -> None:
    """以 aimingcookie 预设注入（捕获 stdout，映射注入器的 SystemExit 语义）。"""
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            injector.run([
                "--preset", "aimingcookie",
                "--install", str(savegames_parent),
            ])
    except SystemExit as exit_error:
        # 注入器约定：exit 2 = 游戏运行中拒绝写入（包装层预检后仍可能在窄窗口内
        # 拉起游戏，这里按同类跳过处理）；其他非零退出视为错误。
        if exit_error.code == 2:
            raise _GameRunningError(output.getvalue()) from exit_error
        raise InjectorError(exit_error.code, output.getvalue()) from exit_error
    log.info(
        "KovaaK stats export enabled (SaveStatistics=true, StatsExportLevel=%d "
        "Challenge Completion); injector: %s",
        _TARGET_EXPORT_LEVEL,
        " | ".join(output.getvalue().splitlines()),
    )


# ---------------------------------------------------------------------------
# 硬重启缝（可注入/可 mock；进程名语义与注入器 find_game_processes 同源）
# ---------------------------------------------------------------------------


def _find_game_processes() -> list[tuple[int, str]]:
    """游戏本体进程列表；来源与注入器完全一致（FPSAimTrainer 前缀）。"""
    return injector.find_game_processes()


def _kill_game_processes(processes: list[tuple[int, str]]) -> None:
    """只杀游戏本体进程（按 PID，无 /T 不牵连子进程/其它程序）。

    taskkill 是 Windows 自带命令；失败不抛异常，交由 ``_wait_for_game_exit``
    复核是否真的退出（仍存活则中止本次写入）。
    """
    for pid, name in processes:
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            log.warning("KovaaK process kill failed: %s(PID %s)", name, pid)


def _wait_for_game_exit(timeout_seconds: float | None = None) -> bool:
    """轮询直到游戏进程全部退出；超时返回 False（此时必须放弃写入）。

    超时默认在调用时读取模块常量（便于测试按需覆盖）。
    """
    if timeout_seconds is None:
        timeout_seconds = _GAME_EXIT_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not _find_game_processes():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_GAME_EXIT_POLL_SECONDS)


def _launch_kovaak(install_root: Path) -> None:
    """重新拉起 KovaaK：优先直接启动安装根下的 FPSAimTrainer.exe。

    本机实测安装布局为 ``<install_root>/FPSAimTrainer.exe``（Steam
    appmanifest_824270 的 installdir）；exe 缺失时才退回 steam:// 深链。
    os.startfile 是 Windows ShellExecute，非阻塞，不产生 stdout。
    """
    exe = Path(install_root) / "FPSAimTrainer.exe"
    try:
        if exe.is_file():
            os.startfile(str(exe))  # noqa: S606 - Windows ShellExecute
            return
        os.startfile("steam://rungameid/%s" % _STEAM_APP_ID)  # noqa: S606
    except OSError:
        log.warning("KovaaK relaunch failed for %s", exe, exc_info=True)


def _hard_restart_allowed(now: float) -> bool:
    with _restart_state_lock:
        last = _last_hard_restart_monotonic
    return last is None or (now - last) >= _HARD_RESTART_BACKOFF_SECONDS


def _note_hard_restart(now: float) -> None:
    """记录本次硬重启时刻（在动手前记录，杀进程失败也照算一次，避免紧循环）。"""
    global _last_hard_restart_monotonic
    with _restart_state_lock:
        _last_hard_restart_monotonic = now


def _hard_restart_kovaak(
    install_root: Path,
    pus_path: Path,
    processes: list[tuple[int, str]],
) -> str:
    listing = ", ".join("%s(PID %s)" % (name, pid) for pid, name in processes)
    log.warning(
        "KovaaK stats export not enabled but the game is running (%s); "
        "hard-restarting KovaaK (kill -> write PUS -> relaunch)", listing,
    )
    _note_hard_restart(time.monotonic())
    try:
        _kill_game_processes(processes)
    except Exception:
        log.exception("KovaaK game process termination failed")
        return RESULT_RESTART_FAILED
    if not _wait_for_game_exit():
        log.warning(
            "KovaaK game process still alive after termination; aborting the "
            "stats export write (no settings touched)",
        )
        return RESULT_RESTART_FAILED
    try:
        # --install 需要「含 Saved/SaveGames/ 的目录」，即 <install_root>/FPSAimTrainer。
        _run_injector(pus_path.parents[2])
    except _GameRunningError:
        log.warning(
            "KovaaK restarted while the stats export write was prepared; "
            "skipping the write.",
        )
        return RESULT_SKIPPED_GAME_RUNNING
    try:
        _launch_kovaak(install_root)
    except Exception:
        log.exception("KovaaK relaunch failed")
        return RESULT_RESTART_FAILED
    log.info(
        "KovaaK restarted and stats export enabled (SaveStatistics=true, "
        "StatsExportLevel=%d Challenge Completion)",
        _TARGET_EXPORT_LEVEL,
    )
    return RESULT_HARD_RESTARTED


def _ensure(install_root: Path | str | None) -> str:
    if install_root is None:
        log.info("KovaaK stats export check skipped: no install dir resolved")
        return RESULT_NO_INSTALL
    pus_path = _pus_path(Path(install_root))
    if not pus_path.is_file():
        log.info(
            "KovaaK stats export check skipped: %s not found (game not installed "
            "or never started)", pus_path,
        )
        return RESULT_FILE_MISSING
    try:
        at_target, has_boolean, has_integer = _at_target(pus_path)
    except FileNotFoundError:
        log.info("KovaaK stats export check skipped: %s disappeared", pus_path)
        return RESULT_FILE_MISSING
    except (OSError, ValueError):
        log.warning(
            "KovaaK stats export check skipped: %s is unreadable or not valid JSON",
            pus_path,
        )
        return RESULT_ERROR
    if at_target:
        log.info(
            "KovaaK stats export already enabled (SaveStatistics=true, "
            "StatsExportLevel=%d Challenge Completion)",
            _TARGET_EXPORT_LEVEL,
        )
        return RESULT_ALREADY_OK
    if not (has_boolean and has_integer):
        log.warning(
            "KovaaK stats export check skipped: %s lacks SaveStatistics/"
            "StatsExportLevel keys (unsupported PUS version); not writing new keys",
            pus_path,
        )
        return RESULT_KEYS_MISSING
    install = Path(install_root)
    processes = _find_game_processes()
    if processes:
        # 游戏运行中 + 设置不达标：硬重启（点点拍板：不分时机、不要确认弹窗）。
        # 退避窗口内只记日志，避免「写不进 → 反复重启」死循环。
        if not _hard_restart_allowed(time.monotonic()):
            log.warning(
                "KovaaK stats export not enabled and the game is running, but a "
                "hard restart happened within the last %.0fs; suppressing another "
                "restart (will not write while the game runs).",
                _HARD_RESTART_BACKOFF_SECONDS,
            )
            return RESULT_RESTART_BACKOFF
        return _hard_restart_kovaak(install, pus_path, processes)
    try:
        # --install 需要「含 Saved/SaveGames/ 的目录」，即 <install_root>/FPSAimTrainer。
        _run_injector(pus_path.parents[2])
    except _GameRunningError:
        log.warning(
            "KovaaK started while the stats export write was prepared; "
            "skipping the write. Will retry on next app start.",
        )
        return RESULT_SKIPPED_GAME_RUNNING
    return RESULT_FIXED


class _GameRunningError(Exception):
    """注入器在窄窗口内检测到游戏拉起，拒绝写入（SystemExit(2) 的内部映射）。"""


class InjectorError(Exception):
    """注入器因校验/写盘失败主动退出（SystemExit 非 2/0 的内部映射）。"""


def ensure_kovaak_stats_export(install_root: Path | str | None) -> str:
    """确保 SaveStatistics=true + StatsExportLevel=1(Challenge Completion)。

    幂等；绝不抛异常（所有失败折损为结果类别并记日志），供桌面启动流程在线程中
    调用。``install_root`` 是含 ``FPSAimTrainer/`` 子目录的 KovaaK 安装根
    （``config.resolve_kovaak_install_dir()`` 的返回值）。
    """
    try:
        result = _ensure(install_root)
    except InjectorError as error:
        log.error("KovaaK stats export injection failed: %r", error)
        return RESULT_ERROR
    except Exception:
        log.exception("KovaaK stats export check failed unexpectedly")
        return RESULT_ERROR
    log.info("KovaaK stats export auto-enable result: %s", result)
    return result
