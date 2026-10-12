# -*- coding: utf-8 -*-
"""input_logger 僵尸通道防护测试（鲁棒性审计 P1-15，先于实现编写）。

病灶：flush() 的 OSError 沿 wndproc ctypes 回调外溢被吞（"Exception ignored"
进 role.log），self.lines 永不清空 → 无界增长；self.f 恒真 → is_file() 指纹
失明、UI 恒显 capturing。修复合同：
- flush 落盘失败必须清空缓冲（停缓冲）、停用通道（f=None）并留 cause 码；
- 通道停用后 emit 不得继续积压（无界增长红线）；
- stats 必须打印 input_channel_dead cause 行（降级可观测合同）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="input_logger 依赖 Win32 Raw Input（ctypes WinDLL），仅 Windows 可导入",
)


class _FailingFile:
    """必失败的文件句柄桩：模拟磁盘满/杀软锁（OSError 族）。"""

    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: str) -> int:  # pragma: no cover - 直接抛
        raise OSError(28, "No space left on device")

    def flush(self) -> None:
        raise OSError(28, "No space left on device")

    def close(self) -> None:
        self.closed = True


def _make_logger(tmp_path: str):
    from telemetry_capture.input_logger import Logger

    logger = Logger(os.path.join(tmp_path, "input_log.jsonl"))
    # 换上必失败句柄（绕开真实磁盘语义，专注失败路径）。
    logger.f.close()
    logger.f = _FailingFile()
    logger.lines = [f'{{"ev":"m","t":{i}}}' for i in range(199)]
    return logger


def test_flush_failure_clears_buffer_disables_channel_and_records_cause(tmp_path):
    logger = _make_logger(str(tmp_path))
    logger.emit(3, 4, [])  # 第 200 行触发自动 flush → OSError

    assert logger.lines == [], "落盘失败后缓冲必须清空（无界增长红线）"
    assert logger.f is None, "停用通道：僵尸不得再假装活着"
    assert logger.write_failures == 1
    assert logger.write_error is not None and logger.write_error.startswith("io_error:")


def test_disabled_channel_stops_buffering_new_events(tmp_path):
    logger = _make_logger(str(tmp_path))
    logger.emit(1, 2, [])  # 触发 flush 失败 → 停通道
    logger.emit(5, 6, ["L_down"])
    logger.emit(7, 8, [])

    assert logger.lines == [], "停用通道后 emit 不得继续积压"
    assert logger.f is None
    assert logger.write_failures == 1, "后续 emit 不再触碰已死通道"


def test_stats_line_carries_input_channel_dead_cause(capsys, tmp_path):
    from telemetry_capture.input_logger import stats

    logger = _make_logger(str(tmp_path))
    logger.emit(1, 1, [])  # flush 失败 → 停通道
    stats(logger, 10.0)

    out = capsys.readouterr().out
    assert "cause=input_channel_dead" in out
    assert "io_error:OSError" in out
    # 健康通道不得打 cause 行。
    healthy = types.SimpleNamespace(
        n_move=1000, n_btn=10, write_failures=0, write_error=None)
    stats(healthy, 10.0)
    out2 = capsys.readouterr().out
    assert "input_channel_dead" not in out2


def test_healthy_flush_still_persists_and_clears(tmp_path):
    from telemetry_capture.input_logger import Logger

    path = os.path.join(str(tmp_path), "healthy.jsonl")
    logger = Logger(path)
    logger.emit(1, 2, [])
    before = len(logger.lines)
    assert before > 0
    logger.flush()
    assert logger.lines == []
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert '"ev": "clock_map"' in content or '"ev":"clock_map"' in content
    assert logger.f is not None
