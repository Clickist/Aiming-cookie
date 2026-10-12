# -*- coding: utf-8 -*-
"""_session_output_counts 指纹测试（鲁棒性审计 P1-15，先于实现编写）。

病灶：input 通道指纹只有 is_file()——input_logger 落盘失败停通道后文件仍在
（恒 True），"进程活着但零产出"的僵尸通道对指纹不可见。修复合同：加性字段
input_log_bytes 携带字节尺寸（两次采样不涨 + 进程活着 = 僵尸），既有布尔
字段保留不动（消费方零破坏）。
"""
from __future__ import annotations

from pathlib import Path

from webapp.backend.telemetry_capture_service import TelemetryCaptureService


def _svc_with_session_dir(session_dir: Path | None) -> TelemetryCaptureService:
    # _session_output_counts 只读 _session_dir：object.__new__ 绕开重初始化。
    svc = object.__new__(TelemetryCaptureService)
    svc._session_dir = session_dir
    return svc


def test_input_log_fingerprint_carries_byte_size(tmp_path: Path) -> None:
    payload = '{"ev":"m","t":1.0}\n'
    # newline="\n"：Windows 默认会把 \n 翻译成 \r\n，字节数断言会被扰动。
    (tmp_path / "input_log.jsonl").write_text(payload, encoding="utf-8", newline="\n")
    counts = _svc_with_session_dir(tmp_path)._session_output_counts()
    assert counts["input_log"] is True
    assert counts["input_log_bytes"] == len(payload.encode("utf-8"))


def test_input_log_bytes_zero_when_file_missing(tmp_path: Path) -> None:
    counts = _svc_with_session_dir(tmp_path)._session_output_counts()
    assert counts["input_log"] is False
    assert counts["input_log_bytes"] == 0


def test_counts_default_without_session_dir() -> None:
    counts = _svc_with_session_dir(None)._session_output_counts()
    assert counts == {
        "target_files": 0,
        "camera_files": 0,
        "input_log": False,
        "input_log_bytes": 0,
    }
