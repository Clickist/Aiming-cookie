"""自动开启 KovaaK 统计导出（kovaak_stats_export_setup）的行为测试。

全部场景都在 ``tmp_path`` 构造 PUS 副本，绝不触碰真实 KovaaK 安装目录
（E:/SteamLibrary/...）与 AC 真实 DATA_ROOT；进程检测按 INJECTOR.md §5 的
打桩方法替换 ``kovaaks_settings_inject.find_game_processes``。硬重启路径额外
替换本模块的进程杀/拉起缝（``_kill_game_processes`` / ``_launch_kovaak``），
绝不杀/拉起真实进程。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from webapp.backend import kovaak_stats_export_setup as setup_mod
from webapp.backend import kovaaks_settings_inject as injector

BOOLEAN_KEY = "EBooleanSettingId::SaveStatistics"
INTEGER_KEY = "EIntegerSettingId::StatsExportLevel"
BOM = b"\xef\xbb\xbf"


@pytest.fixture(autouse=True)
def _reset_hard_restart_state():
    """硬重启退避状态是模块级全局：每个测试前后清零，避免相互污染。"""
    setup_mod._last_hard_restart_monotonic = None
    yield
    setup_mod._last_hard_restart_monotonic = None


def _patch_running_game(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> dict:
    """打桩「游戏运行中」：进程检测/杀/拉起全部走状态变量与事件记录。

    杀进程会把状态清空，于是随后的 ``_wait_for_game_exit`` 与注入器守卫都看到
    「已退出」，写入得以进行。
    """
    state = {"running": [(4321, "FPSAimTrainer.exe")]}

    def fake_find() -> list[tuple[int, str]]:
        return list(state["running"])

    def fake_kill(processes: list[tuple[int, str]]) -> None:
        events.append("kill")
        state["running"] = []

    monkeypatch.setattr(injector, "find_game_processes", fake_find)
    monkeypatch.setattr(setup_mod, "_kill_game_processes", fake_kill)
    return state


def _pus_text(*, save_statistics: bool = False, stats_export_level: int = 0) -> str:
    """仿真 PUS 结构（CRLF + Tab 缩进 + 目标两键 + 干扰键）。"""
    return "\r\n".join([
        "{",
        '\t"booleanSettings":',
        "\t{",
        '\t\t"%s": %s,' % (BOOLEAN_KEY, "true" if save_statistics else "false"),
        '\t\t"EBooleanSettingId::ShowFps": true',
        "\t},",
        '\t"integerSettings":',
        "\t{",
        '\t\t"%s": %d,' % (INTEGER_KEY, stats_export_level),
        '\t\t"EIntegerSettingId::ChallengeHistogramStats": 3',
        "\t},",
        '\t"floatSettings":',
        "\t{",
        '\t\t"EFloatSettingId::Gamma": 2.2',
        "\t},",
        '\t"version": 3',
        "}",
    ])


def _make_install(tmp_path: Path, raw: bytes) -> tuple[Path, Path]:
    """返回 (install_root, PUS 路径)：install_root 含 FPSAimTrainer/Saved/SaveGames。"""
    savegames = tmp_path / "FPSAimTrainer" / "Saved" / "SaveGames"
    savegames.mkdir(parents=True)
    pus = savegames / injector.PUS_NAME
    pus.write_bytes(raw)
    return tmp_path, pus


def _install_from_text(
    tmp_path: Path,
    text: str,
    *,
    bom: bool = True,
) -> tuple[Path, Path, bytes]:
    raw = (BOM if bom else b"") + text.encode("utf-8")
    install_root, pus = _make_install(tmp_path, raw)
    return install_root, pus, raw


def test_no_install_root_returns_no_install() -> None:
    assert setup_mod.ensure_kovaak_stats_export(None) == setup_mod.RESULT_NO_INSTALL


def test_missing_pus_fails_soft_without_creating_files(tmp_path: Path) -> None:
    result = setup_mod.ensure_kovaak_stats_export(tmp_path)

    assert result == setup_mod.RESULT_FILE_MISSING
    assert not (tmp_path / "FPSAimTrainer").exists()


def test_already_at_target_is_noop_without_backup_or_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = _pus_text(save_statistics=True, stats_export_level=1)
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_ALREADY_OK
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))
    # 桌面运行时 stdout 是 Tauri ready 协议通道：包装层必须吃掉注入器的 print。
    assert capsys.readouterr().out == ""


def test_injection_fixes_exactly_two_keys_and_keeps_rest_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(injector, "find_game_processes", lambda: [])
    text = _pus_text(save_statistics=False, stats_export_level=0)
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_FIXED
    new_raw = pus.read_bytes()
    assert new_raw.startswith(BOM)  # BOM 保留
    new_text = new_raw.decode("utf-8-sig")
    parsed = json.loads(new_text)
    # 两键生效，且类型正确（bool 必须真 bool）。
    assert parsed["booleanSettings"][BOOLEAN_KEY] is True
    assert parsed["integerSettings"][INTEGER_KEY] == 1
    # 其余键深度不变。
    assert parsed["booleanSettings"]["EBooleanSettingId::ShowFps"] is True
    assert parsed["integerSettings"]["EIntegerSettingId::ChallengeHistogramStats"] == 3
    assert parsed["floatSettings"]["EFloatSettingId::Gamma"] == 2.2
    assert parsed["version"] == 3
    # 未触及行字节不变：恰好只有两行不同。
    old_lines = text.split("\r\n")
    new_lines = new_text.split("\r\n")
    assert len(old_lines) == len(new_lines)
    changed = [
        index for index, (old, new) in enumerate(zip(old_lines, new_lines))
        if old != new
    ]
    assert changed == [3, 8]  # SaveStatistics 行与 StatsExportLevel 行
    # 写后结构无损断言（注入器自带校验器交叉验证）。
    injector.verify_edited_structure(
        text,
        new_text,
        {
            BOOLEAN_KEY: ("booleanSettings", True),
            INTEGER_KEY: ("integerSettings", 1),
        },
    )
    # 写前自动备份：恰好一份，内容等于写前字节。
    backups = list(pus.parent.glob("*.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert capsys.readouterr().out == ""


def test_second_call_is_idempotent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injector, "find_game_processes", lambda: [])
    install_root, pus, raw = _install_from_text(tmp_path, _pus_text())

    assert setup_mod.ensure_kovaak_stats_export(install_root) == setup_mod.RESULT_FIXED
    after_first = pus.read_bytes()
    backups_after_first = list(pus.parent.glob("*.bak.*"))

    assert setup_mod.ensure_kovaak_stats_export(install_root) == setup_mod.RESULT_ALREADY_OK
    assert pus.read_bytes() == after_first
    assert list(pus.parent.glob("*.bak.*")) == backups_after_first


def test_game_running_and_not_at_target_hard_restarts_kill_write_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """游戏运行中 + 设置不对：杀进程 → 写 PUS → 重新拉起，三步都发生。"""
    events: list[str] = []
    _patch_running_game(monkeypatch, events)
    text = _pus_text()
    install_root, pus, raw = _install_from_text(tmp_path, text)

    def fake_launch(root: Path) -> None:
        # 拉起之前 PUS 必须已经写好（证明顺序是 杀→写→拉起，而非先拉起）。
        parsed = json.loads(pus.read_bytes().decode("utf-8-sig"))
        assert parsed["booleanSettings"][BOOLEAN_KEY] is True
        assert parsed["integerSettings"][INTEGER_KEY] == 1
        events.append("launch")

    monkeypatch.setattr(setup_mod, "_launch_kovaak", fake_launch)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_HARD_RESTARTED
    assert events == ["kill", "launch"]
    new_text = pus.read_bytes().decode("utf-8-sig")
    parsed = json.loads(new_text)
    assert parsed["booleanSettings"][BOOLEAN_KEY] is True
    assert parsed["integerSettings"][INTEGER_KEY] == 1
    # 写前自动备份恰好一份，内容等于写前字节。
    backups = list(pus.parent.glob("*.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw


def test_game_running_but_already_at_target_does_not_touch_game(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设置已达标时全程不动：不杀、不写、不拉起。"""
    events: list[str] = []
    _patch_running_game(monkeypatch, events)
    monkeypatch.setattr(
        setup_mod,
        "_launch_kovaak",
        lambda _root: events.append("launch"),
    )
    text = _pus_text(save_statistics=True, stats_export_level=1)
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_ALREADY_OK
    assert events == []
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))


def test_hard_restart_backoff_suppresses_second_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避窗口内「游戏在跑 + 设置仍不对」只记日志，不二次重启。"""
    events: list[str] = []
    _patch_running_game(monkeypatch, events)
    monkeypatch.setattr(
        setup_mod,
        "_launch_kovaak",
        lambda _root: events.append("launch"),
    )
    text = _pus_text()
    install_root, pus, raw = _install_from_text(tmp_path, text)

    first = setup_mod.ensure_kovaak_stats_export(install_root)
    assert first == setup_mod.RESULT_HARD_RESTARTED
    backups_after_first = list(pus.parent.glob("*.bak.*"))
    assert events == ["kill", "launch"]

    # 状态回滚成「仍在跑 + 仍未达标」，模拟重启后仍不对的下一轮复查。
    pus.write_bytes(raw)
    monkeypatch.setattr(
        injector,
        "find_game_processes",
        lambda: [(4321, "FPSAimTrainer.exe")],
    )
    assert setup_mod._last_hard_restart_monotonic is not None
    assert setup_mod._last_hard_restart_monotonic > time.monotonic() - (
        setup_mod._HARD_RESTART_BACKOFF_SECONDS
    )

    second = setup_mod.ensure_kovaak_stats_export(install_root)

    assert second == setup_mod.RESULT_RESTART_BACKOFF
    assert events == ["kill", "launch"]  # 没有第二次杀/拉起
    assert pus.read_bytes() == raw  # 退避期间绝不写盘
    assert list(pus.parent.glob("*.bak.*")) == backups_after_first


def test_hard_restart_allowed_again_after_backoff_elapses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_running_game(monkeypatch, events)
    monkeypatch.setattr(
        setup_mod,
        "_launch_kovaak",
        lambda _root: events.append("launch"),
    )
    install_root, _pus, _raw = _install_from_text(tmp_path, _pus_text())
    # 把最近重启时刻推远到退避窗口之外。
    monkeypatch.setattr(
        setup_mod,
        "_last_hard_restart_monotonic",
        time.monotonic() - setup_mod._HARD_RESTART_BACKOFF_SECONDS - 1.0,
    )

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_HARD_RESTARTED
    assert events == ["kill", "launch"]


def test_hard_restart_aborts_write_when_game_survives_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """杀进程失败（进程仍在）：绝不写 PUS，返回 restart_failed。"""
    events: list[str] = []
    state = _patch_running_game(monkeypatch, events)

    def kill_but_ignore(_processes: list[tuple[int, str]]) -> None:
        events.append("kill")
        state["running"] = [(4321, "FPSAimTrainer.exe")]  # 没杀掉

    monkeypatch.setattr(setup_mod, "_kill_game_processes", kill_but_ignore)
    monkeypatch.setattr(
        setup_mod,
        "_launch_kovaak",
        lambda _root: events.append("launch"),
    )
    monkeypatch.setattr(setup_mod, "_GAME_EXIT_TIMEOUT_SECONDS", 0.0)
    text = _pus_text()
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_RESTART_FAILED
    assert events == ["kill"]  # 未写、未拉起
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))


def test_game_started_between_precheck_and_write_maps_to_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """包装层预检通过后，注入器守卫命中（SystemExit 2）也必须折损为跳过。"""
    calls: list[int] = []

    def fake_processes() -> list[tuple[int, str]]:
        calls.append(1)
        if len(calls) == 1:
            return []  # 包装层预检：未见游戏进程
        return [(7, "FPSAimTrainer-Win64-Shipping.exe")]  # 注入器守卫：命中

    monkeypatch.setattr(injector, "find_game_processes", fake_processes)
    text = _pus_text()
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_SKIPPED_GAME_RUNNING
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))


def test_pus_without_target_keys_fails_soft_and_writes_nothing(tmp_path: Path) -> None:
    """跨版本 PUS 缺键：注入器默认拒绝新增键（会被旧程序丢弃），必须跳过。"""
    text = "\r\n".join([
        "{",
        '\t"booleanSettings":',
        "\t{",
        '\t\t"EBooleanSettingId::ShowFps": true',
        "\t},",
        '\t"integerSettings":',
        "\t{",
        '\t\t"EIntegerSettingId::ChallengeHistogramStats": 3',
        "\t},",
        '\t"version": 3',
        "}",
    ])
    install_root, pus, raw = _install_from_text(tmp_path, text)

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_KEYS_MISSING
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))


def test_corrupt_pus_returns_error_without_write(tmp_path: Path) -> None:
    install_root, pus, raw = _install_from_text(
        tmp_path, "{ truncated by power loss",
    )

    result = setup_mod.ensure_kovaak_stats_export(install_root)

    assert result == setup_mod.RESULT_ERROR
    assert pus.read_bytes() == raw
    assert not list(pus.parent.glob("*.bak.*"))
