"""file_store.write_json 原子写行为测试（系统评审 2026-10-16 #1）。

评审实证：所有写方共用固定名 ``.{name}.tmp``，切窗线程与事件循环并发写同一
run meta 时 120 次受控并发出现 46 次文件占用/权限异常。本文件锁两个行为：
并发写同一目标互不踩踏、replace 失败不留 tmp 残留。DATA_ROOT 由 conftest
隔离；所有路径都是相对 DATA_ROOT 的相对路径。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from webapp.backend import config, file_store


def _abs(rel: str) -> Path:
    return Path(config.DATA_ROOT) / rel


def test_write_json_roundtrip_and_no_tmp_residue() -> None:
    file_store.write_json("probe/shared.json", {"n": 1, "text": "中文"})
    assert file_store.read_json("probe/shared.json") == {"n": 1, "text": "中文"}
    assert list(_abs("probe").glob(".*.tmp")) == []


def test_write_json_concurrent_writers_to_one_path_do_not_collide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两线程并发写同一目标：共享 tmp 的写/replace 重叠必踩踏，唯一 tmp 不受影响。

    真实竞争形态（评审 120 次并发 46 次文件占用/权限异常）：线程 A 还握着共享
    tmp 的写句柄时，线程 B 已经走到 os.replace。测试用角色门把线程 1 钉在
    write_text 句柄内、线程 2 钉在 os.replace 处，强制两者重叠——旧实现共用
    同一个 ``.{name}.tmp`` 必然抛共享冲突/找不到文件；新实现各自持有唯一
    tmp，双双成功且零残留。
    """
    rel = "probe/shared.json"
    target = _abs(rel)
    real_replace = os.replace
    real_write_text = Path.write_text
    real_fdopen = os.fdopen
    barrier = threading.Barrier(2)
    t1_ident: dict[str, int] = {}
    t1_in_gate = threading.Event()

    def holding_write_text(self, data, *args, **kwargs):
        # 角色 1（旧写路径 Path.write_text）：握着本方 tmp 的打开句柄停在屏障处。
        if (str(self).endswith(".tmp") and target.name in self.name
                and threading.get_ident() == t1_ident.get("id")):
            t1_in_gate.set()
            barrier.wait(timeout=5.0)
        return real_write_text(self, data, *args, **kwargs)

    def holding_fdopen(fd, *args, **kwargs):
        # 角色 1（mkstemp 写路径 os.fdopen）：句柄已打开、写入前停在屏障处。
        # 本测试窗口内只有 write_json 用 fdopen，按线程身份即可钉住角色 1。
        stream = real_fdopen(fd, *args, **kwargs)
        if ("id" in t1_ident
                and threading.get_ident() == t1_ident["id"]):
            t1_in_gate.set()
            barrier.wait(timeout=5.0)
        return stream

    def gated_replace(src, dst, *args, **kwargs):
        # 角色 2：替换方停在屏障处，与角色 1 的写句柄重叠。
        src_path = Path(src)
        if (str(src_path).endswith(".tmp") and target.name in src_path.name
                and threading.get_ident() != t1_ident.get("id")
                and "id" in t1_ident):
            barrier.wait(timeout=5.0)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", holding_write_text)
    monkeypatch.setattr(os, "fdopen", holding_fdopen)
    monkeypatch.setattr(os, "replace", gated_replace)

    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            if index == 0:
                t1_ident["id"] = threading.get_ident()
            file_store.write_json(rel, {"writer": index})
        except BaseException as error:  # noqa: BLE001 - 收集后统一断言
            errors.append(error)

    thread1 = threading.Thread(target=writer, args=(0,), daemon=True)
    thread1.start()
    assert t1_in_gate.wait(timeout=5.0), "角色 1 必须先进入写门"
    thread2 = threading.Thread(target=writer, args=(1,), daemon=True)
    thread2.start()

    for thread in (thread1, thread2):
        thread.join(timeout=10.0)
        assert not thread.is_alive()

    assert errors == [], f"并发写出现异常：{errors!r}"
    final = file_store.read_json(rel)
    assert final in ({"writer": 0}, {"writer": 1})
    assert list(target.parent.glob(".*.tmp")) == []


def test_write_json_cleans_up_tmp_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """replace 失败必须清掉本次 tmp，既不留残留也不破坏旧目标文件。"""
    rel = "probe/replace-fail.json"
    target = _abs(rel)
    file_store.write_json(rel, {"generation": "old"})
    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        src_path = Path(src)
        if str(src_path).endswith(".tmp") and target.name in src_path.name:
            raise OSError(80, "simulated replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        file_store.write_json(rel, {"generation": "new"})

    assert list(target.parent.glob(".*.tmp")) == [], "失败的 tmp 必须被清理"
    assert file_store.read_json(rel) == {"generation": "old"}
