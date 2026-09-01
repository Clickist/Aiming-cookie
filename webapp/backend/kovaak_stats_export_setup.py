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
- KovaaK 运行中跳过（游戏会话内/退出时会用内存值整文件回写，外部写入会被覆盖）；
- PUS 不存在（未安装/未运行过游戏）或缺少目标键（跨版本 PUS）时跳过；
- stdout 全程捕获：桌面运行时 stdout 是 Tauri 的 ready 协议通道，注入器的
  print 输出一律转入日志，不得外泄。
"""

from __future__ import annotations

import contextlib
import io
import logging
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
RESULT_SKIPPED_GAME_RUNNING = "skipped_game_running"  # KovaaK 运行中，拒绝写入
RESULT_FILE_MISSING = "file_missing"                # PUS 不存在
RESULT_KEYS_MISSING = "keys_missing"                # PUS 缺少目标键（跨版本）
RESULT_NO_INSTALL = "no_install"                    # 无法定位 KovaaK 安装根
RESULT_ERROR = "error"


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
    processes = injector.find_game_processes()
    if processes:
        listing = ", ".join("%s(PID %s)" % (name, pid) for pid, name in processes)
        log.warning(
            "KovaaK stats export not enabled but the game is running (%s); "
            "skipping the write (external edits would be overwritten). "
            "Will retry on next app start.", listing,
        )
        return RESULT_SKIPPED_GAME_RUNNING
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
