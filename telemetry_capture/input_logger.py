# -*- coding: utf-8 -*-
"""input_logger.py — OS 层原始输入记录器（路线 B，纯外部，完全不碰游戏进程）。

Windows Raw Input（WM_INPUT）记录相对鼠标位移与左/右键按下抬起，≥500Hz（实际
= 鼠标上报率，游戏鼠标 1000Hz），QueryPerformanceCounter 高精度时间戳。
注册 RIDEV_INPUTSINK → 游戏在前台/全屏时同样收到输入（不与游戏自身的 Raw Input
注册冲突：Raw Input 允许多个窗口同时接收同一设备流）。

输出 JSONL（~.jsonl）：
  {"ev":"clock_map","qpc":..,"t":<perf_counter秒>,"t_unix":<time.time()>}   # 首行+每10s
  {"ev":"m","t":..,"qpc":..,"dx":..,"dy":..,"btn":["L_down",..]}            # 每个输入包一行

边界（2026-08-29 实测）：Raw Input 只上报真实硬件设备的输入流；SendInput 合成的
移动/点击不产生 WM_INPUT（但 WH_MOUSE_LL 低级钩子可见，LLMHF_INJECTED 标记）。
→ 本记录器天然只记录真人硬件输入，且不影响/不干扰游戏自身的 Raw Input 注册
（Raw Input 允许多窗口同时接收同一设备）。无真实鼠标操作时 --test 事件数为 0 属预期。

用法：
    python input_logger.py out.jsonl            # 持续记录，Ctrl+C 停止
    python input_logger.py --test               # 30 秒自测：打印事件速率统计
    python input_logger.py --poll out.jsonl     # 降级方案：GetAsyncKeyState 500Hz 轮询
                                                #   （丢 raw 增量精度：用 GetCursorPos 差分）
与 target_poll2.py 的 time.time() 对齐：记录行里的 t_unix 即 time.time() 基准；
换算 Δ = t_unix - t（perf_counter 秒），target_poll 的 t_direct = 行内 t + Δ。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.DefWindowProcW.restype = ctypes.c_longlong
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

QPF = wt.LARGE_INTEGER()
k32.QueryPerformanceFrequency(ctypes.byref(QPF))
QPF = float(QPF.value)

# ---- Raw Input 常量 ----
WM_INPUT = 0x00FF
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RAWINPUTHEADER_SIZE = 24          # x64: {DWORD dwType; DWORD dwSize; HANDLE hDev; WPARAM wp;}
MOUSE_MOVE_RELATIVE = 0
RI_MOUSE_LEFT_DOWN = 0x0001
RI_MOUSE_LEFT_UP = 0x0002
RI_MOUSE_RIGHT_DOWN = 0x0004
RI_MOUSE_RIGHT_UP = 0x0008
RI_MOUSE_MIDDLE_DOWN = 0x0010
RI_MOUSE_MIDDLE_UP = 0x0020
BTN_MAP = [(RI_MOUSE_LEFT_DOWN, "L_down"), (RI_MOUSE_LEFT_UP, "L_up"),
           (RI_MOUSE_RIGHT_DOWN, "R_down"), (RI_MOUSE_RIGHT_UP, "R_up"),
           (RI_MOUSE_MIDDLE_DOWN, "M_down"), (RI_MOUSE_MIDDLE_UP, "M_up")]

# RAWMOUSE 在缓冲区内的偏移（x64）：header 24B 后
OFF_FLAGS, OFF_BTN, OFF_X, OFF_Y = 24, 28, 36, 40


class Logger:
    def __init__(self, path=None):
        self.t0_perf = time.perf_counter()
        self.t0_unix = time.time()
        qpc = wt.LARGE_INTEGER()
        k32.QueryPerformanceCounter(ctypes.byref(qpc))
        self.t0_qpc = qpc.value
        self.lines = []
        self.n_move = 0
        self.n_btn = 0
        self.last_map = 0.0
        self.path = path
        self.f = open(path, "a", encoding="utf-8") if path else None
        self.map_line()

    def now(self):
        qpc = wt.LARGE_INTEGER()
        k32.QueryPerformanceCounter(ctypes.byref(qpc))
        return qpc.value, qpc.value / QPF

    def map_line(self, force=False):
        qpc, perf = self.now()
        self.last_map = perf
        rec = {"ev": "clock_map", "qpc": qpc, "t": perf,
               "t_unix": self.t0_unix + (perf - (self.t0_qpc / QPF))}
        if force:
            print("[clock] %r" % rec)
        if self.f:
            self.lines.append(json.dumps(rec))

    def emit(self, dx, dy, btn):
        qpc, perf = self.now()
        if dx or dy:
            self.n_move += 1
        if btn:
            self.n_btn += len(btn)
        self.lines.append(json.dumps({"ev": "m", "t": perf, "qpc": qpc,
                                      "dx": dx, "dy": dy, "btn": btn}))
        if self.f and len(self.lines) >= 200:
            self.flush()

    def flush(self):
        if self.f and self.lines:
            self.f.write("\n".join(self.lines) + "\n")
            self.f.flush()
            self.lines = []


def make_wnd(logger):
    """隐藏窗口 + WM_INPUT 处理。返回 (hwnd, WNDPROC引用, 消息循环函数)。"""
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT,
                                 wt.WPARAM, wt.LPARAM)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = wt.UINT(0)
            # 第一次调用拿需要的缓冲大小
            u32.GetRawInputData(ctypes.c_void_p(lparam), RID_INPUT, None,
                                ctypes.byref(size), RAWINPUTHEADER_SIZE)
            if size.value == 0:
                return 0
            buf2 = ctypes.create_string_buffer(max(size.value, 48))
            sz = wt.UINT(len(buf2))
            got = u32.GetRawInputData(ctypes.c_void_p(lparam), RID_INPUT, buf2,
                                      ctypes.byref(sz), RAWINPUTHEADER_SIZE)
            if got and got >= OFF_Y + 4:
                raw = buf2.raw
                us_flags = raw[OFF_FLAGS] | (raw[OFF_FLAGS + 1] << 8)
                btn_bits = raw[OFF_BTN] | (raw[OFF_BTN + 1] << 8)
                dx = int.from_bytes(raw[OFF_X:OFF_X + 4], "little", signed=True)
                dy = int.from_bytes(raw[OFF_Y:OFF_Y + 4], "little", signed=True)
                btns = [name for bit, name in BTN_MAP if btn_bits & bit]
                if dx or dy or btns:
                    if us_flags & 1:   # MOUSE_MOVE_ABSOLUTE：跳过（罕见设备）
                        if btns:
                            logger.emit(0, 0, btns)
                    else:
                        logger.emit(dx, dy, btns)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    hinst = k32.GetModuleHandleW(None)
    wc = WNDCLASSW(0, proc, 0, 0, hinst, None, None, None, None, "ZCInputLoggerCls")
    if not u32.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != 0:
        # 已注册也 OK
        pass
    hwnd = u32.CreateWindowExW(0, "ZCInputLoggerCls", "zc_input_logger",
                               0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise OSError("CreateWindowExW failed err=%d" % ctypes.get_last_error())

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                    ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]

    rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # usagePage=1 usage=2 鼠标
    if not u32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
        raise OSError("RegisterRawInputDevices failed err=%d" % ctypes.get_last_error())

    return hwnd, proc


def run_raw(seconds=None, path=None):
    logger = Logger(path)
    hwnd, proc = make_wnd(logger)
    print("[raw] 记录中（Raw Input, RIDEV_INPUTSINK；Ctrl+C 停止）"
          + (" → %s" % path if path else " → stdout(仅 --test 统计)"))
    msg = wt.MSG()
    t0 = time.perf_counter()
    nmsg = 0
    try:
        while seconds is None or time.perf_counter() - t0 < seconds:
            while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
                nmsg += 1
            time.sleep(0.0005)
            if logger.f and time.perf_counter() - logger.last_map > 10.0:
                logger.map_line()
    except KeyboardInterrupt:
        pass
    finally:
        logger.flush()
        if logger.f:
            logger.f.close()
        u32.DestroyWindow(hwnd)
    stats(logger, time.perf_counter() - t0, nmsg)


def stats(logger, dur, nmsg=0):
    print("[stats] %.1fs 移动包=%d (%.0f/s) 按钮事件=%d (%.0f/s) JSONL行=%d"
          % (dur, logger.n_move, logger.n_move / max(dur, 1e-9),
             logger.n_btn, logger.n_btn / max(dur, 1e-9),
             logger.n_move + logger.n_btn))
    if logger.n_move / max(dur, 1e-9) < 400:
        print("[stats] 注意：速率 <400/s。原始鼠标上报率通常 125/500/1000Hz；"
              "若远低于该值，检查省电设置/蓝牙连接。")


# ---- 降级方案：GetAsyncKeyState + GetCursorPos 轮询 ----
def run_poll(seconds=None, path=None, hz=500):
    user32 = u32
    logger = Logger(path)
    class PT(ctypes.Structure):
        _fields_ = [("x", wt.LONG), ("y", wt.LONG)]
    pt = PT()
    user32.GetCursorPos(ctypes.byref(pt))
    px, py = pt.x, pt.y
    l_prev = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
    r_prev = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
    print("[poll] 降级记录 %dHz（精度损失：无 raw 增量、点击沿量化到轮询周期）" % hz)
    t0 = time.perf_counter()
    dt = 1.0 / hz
    try:
        while seconds is None or time.perf_counter() - t0 < seconds:
            now = user32.GetAsyncKeyState(0x01) & 0x8000
            nowl = bool(now)
            nowr = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
            user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - px, pt.y - py
            px, py = pt.x, pt.y
            btn = []
            if nowl and not l_prev: btn.append("L_down")
            if not nowl and l_prev: btn.append("L_up")
            if nowr and not r_prev: btn.append("R_down")
            if not nowr and r_prev: btn.append("R_up")
            l_prev, r_prev = nowl, nowr
            if dx or dy or btn:
                logger.emit(dx, dy, btn)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        logger.flush()
        if logger.f:
            logger.f.close()
    stats(logger, time.perf_counter() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--test", action="store_true", help="30 秒自测（不写文件，只打印统计）")
    ap.add_argument("--poll", action="store_true", help="降级：GetAsyncKeyState 轮询")
    ap.add_argument("--hz", type=int, default=500, help="--poll 的轮询率")
    ap.add_argument("--secs", type=float, default=None)
    args = ap.parse_args()
    secs = args.secs if args.secs is not None else (30.0 if args.test else None)
    path = None if args.test else (args.path or "input_log.jsonl")
    if args.test and args.path:
        path = args.path   # --test 也可选择写文件
    if args.poll:
        run_poll(secs, path, args.hz)
    else:
        run_raw(secs, path)


if __name__ == "__main__":
    main()
