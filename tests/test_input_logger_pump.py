# -*- coding: utf-8 -*-
"""input_logger 消息泵事件等待（[perf D3] 2026-10-06）的行为锁。

背景：主循环原先 0.5ms ``time.sleep`` 空转（~2000 次/秒空醒），AC 在后台时
与前台游戏抢 CPU。改为 ``MsgWaitForMultipleObjectsEx(0, None, 250, QS_ALLINPUT,
0)`` 事件等待：队列有输入（含 QS_RAWINPUT 的 WM_INPUT）立即醒来，250ms 超时
兜底 clock_map 的 10s 节奏。WM_INPUT 是排队消息，不泵时积压在队列不丢。

验证设计（两层）：
1. 单测：harness 子进程内建窗 + PostMessage(WM_APP)，断言等待函数 <100ms
   醒来且返回 WAIT_OBJECT_0。
2. 冒烟 + 防空转：harness 子进程内跑真实 run_raw 循环 2s，断言产出合法
   JSONL 且 process_time 远低于墙钟（空转泵/病态立即返回会烧满一核；事件
   等待即使在 ~1kHz 真实鼠标洪流下 CPU 也 ≪ 预算）。

为什么用独立 harness 子进程而不是 pytest 进程内直接建窗：make_wnd 的窗口在
"非生产形态的宿主进程"（pytest 进程、import 本测试模块的进程）里，排空真实
鼠标 WM_INPUT 洪流时间歇性触发 DispatchMessageW access violation（生产形态
的独立 input_logger 子进程数小时连转无恙，本改动前后行为一致，属现存环境
脆弱点）。harness 只建窗 + 预投消息 + 单次等待（不排空），或走生产同款的
run_raw 自身循环；真实输入队列从不被测试代码排空。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "telemetry_capture" / "input_logger.py"

# harness 进程内：建窗 → 预投 WM_APP → 单次 wait_input_event → 上报。
# 绝不 PeekMessageW 排空：真实鼠标 WM_INPUT 留在队列随进程退出即可（见
# 模块 docstring 的环境脆弱点说明）。退出码 0=本次判定通过。
_WAKE_HARNESS = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from telemetry_capture import input_logger as il

logger = il.Logger(None)
hwnd, _proc = il.make_wnd(logger)
assert il.u32.PostMessageW(hwnd, 0x8000, 0, 0)  # WM_APP：任意 QS_POST 消息
t0 = time.perf_counter()
ret = il.wait_input_event(250)
elapsed = time.perf_counter() - t0
print("RET %s ELAPSED %.6f" % (ret, elapsed), flush=True)
sys.exit(0 if (ret == 0 and elapsed < 0.100) else 1)
"""

# harness 进程内：生产同款 run_raw 真实循环跑 2s，上报 process_time 占比。
_RUN_RAW_HARNESS = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from telemetry_capture import input_logger as il

cpu0 = time.process_time()
il.run_raw(seconds=2.0, path=sys.argv[2])
cpu = time.process_time() - cpu0
print("CPU %.6f" % cpu, flush=True)
"""

# 事件等待即使在 ~1kHz 真实鼠标洪流下（每包 GetRawInputData×2 + json），
# 2s 墙钟的 CPU 也应远低于此预算；0.5ms 空转泵或病态立即返回则 ≈ 墙钟。
_CPU_BUDGET_S = 0.5


def test_wait_wakes_on_posted_message_within_100ms():
    proc = subprocess.run(  # noqa: S603 - 固定参数拼装
        [sys.executable, "-c", _WAKE_HARNESS, str(REPO_ROOT)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"harness(wake) 判定未通过 rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}")
    ret = int(proc.stdout.split("RET ")[1].split()[0])
    elapsed = float(proc.stdout.split("ELAPSED ")[1])
    assert ret == 0, "有排队消息时应返回 WAIT_OBJECT_0（0 个句柄）"
    assert elapsed < 0.100, f"等待函数 {elapsed*1000:.1f}ms 才醒来（要求 <100ms）"


def test_run_raw_no_busy_spin_and_valid_jsonl(tmp_path):
    out = tmp_path / "input_smoke.jsonl"
    proc = subprocess.run(  # noqa: S603 - 固定参数拼装
        [sys.executable, "-c", _RUN_RAW_HARNESS, str(REPO_ROOT), str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"run_raw harness 判定未通过 rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}")
    cpu = float(proc.stdout.split("CPU ")[1])
    assert cpu < _CPU_BUDGET_S, (
        f"run_raw 2s 墙钟耗 CPU {cpu:.3f}s ≥ 预算 {_CPU_BUDGET_S}s："
        "疑似空转泵/病态立即返回复活")
    # 产物合法性
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines, "run_raw 至少写出首行 clock_map"
    first = json.loads(lines[0])
    assert first["ev"] == "clock_map"
    n_clock = 0
    for line in lines:
        rec = json.loads(line)
        if rec["ev"] == "clock_map":
            n_clock += 1
            assert isinstance(rec["qpc"], int)
            assert isinstance(rec["t"], float)
            assert isinstance(rec["t_unix"], float)
        elif rec["ev"] == "m":
            assert isinstance(rec["dx"], int) and isinstance(rec["dy"], int)
            assert isinstance(rec["btn"], list)
        else:
            raise AssertionError(f"未知事件类型: {rec['ev']}")
    assert n_clock >= 1
