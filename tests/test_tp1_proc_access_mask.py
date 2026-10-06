# -*- coding: utf-8 -*-
"""tp1.Proc OpenProcess 权限收窄与结构化 cause 码验收（病灶 B1）。

野外报障根因：Proc 用 PROCESS_ALL_ACCESS 打开游戏进程，权限过大被杀软/受限
令牌拒绝（err=5 Access Denied）→ target/camera 轨迹全空。红线：
a. 默认掩码 = ACCESS_RO（QUERY_INFORMATION|VM_READ，与 reoffset.py 只读先例
   一致；_module_base 依赖 EnumProcessModulesEx/GetModuleFileNameExW，文档要求
   完整 QUERY_INFORMATION，不能用 QUERY_LIMITED_INFORMATION）；
b. err=5 → 异常文本含稳定 token ``cause=open_process_denied``（诊断包可机读）；
c. 其他 err → ``cause=open_process_failed``；
d. 原 ``OpenProcess(pid) failed err=N`` 前缀格式不变（既有日志/解析依赖）。

测试只替换 tp1.k32.OpenProcess（不建真句柄），err 经 ctypes.set_last_error 注入。
"""
import ctypes
import os
import sys

import pytest

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "telemetry_capture",
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import tp1  # noqa: E402


def _install_fake_open(monkeypatch: pytest.MonkeyPatch, err: int):
    """替换 tp1.k32.OpenProcess：记录入参、注入 last_error、返回空句柄（失败）。"""
    calls: list[tuple[int, bool, int]] = []

    def fake_open(access_mask: int, inherit: bool, pid: int) -> int:
        calls.append((access_mask, bool(inherit), pid))
        ctypes.set_last_error(err)
        return 0

    monkeypatch.setattr(tp1.k32, "OpenProcess", fake_open)
    return calls


def test_default_access_mask_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_open(monkeypatch, err=5)
    with pytest.raises(OSError):
        tp1.Proc(1234)
    assert calls, "OpenProcess 应被调用一次"
    assert calls[0][0] == (0x0400 | 0x0010), (
        "默认掩码必须是 ACCESS_RO（QUERY_INFORMATION|VM_READ），"
        f"实际 0x{calls[0][0]:x}"
    )


def test_access_denied_carries_stable_cause_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_open(monkeypatch, err=5)
    with pytest.raises(OSError, match="cause=open_process_denied"):
        tp1.Proc(1234)


def test_other_error_carries_generic_cause_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_open(monkeypatch, err=87)
    with pytest.raises(OSError, match="cause=open_process_failed"):
        tp1.Proc(1234)


def test_original_message_prefix_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_open(monkeypatch, err=5)
    with pytest.raises(OSError, match=r"OpenProcess\(1234\) failed err=5"):
        tp1.Proc(1234)
