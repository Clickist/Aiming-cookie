# -*- coding: utf-8 -*-
"""分析科学栈预热子进程（--telemetry-child 机制，独立进程跑完即退）。

背景（1005 凌晨实机）：numpy/scipy 是函数体内懒加载，冻结运行时首次导入
在 LdrLoadDll 里楔 10-20 分钟（安全软件首扫），且**导入全程持有 GIL**——
在 backend 进程内发生时整个事件循环（心跳/API/统计）跟着冻死；放到后台
线程预热则进程无痕死亡（五连杀，机制未明）。子进程是唯一安全位：楔在
子进程里无感，OS 文件缓存与安全软件扫描记录留热，父进程随后的真实导入
秒级完成。

由 webapp/backend/app.py lifespan 启动时 spawn（不等待、不看结果）。
"""
import sys
import time
from pathlib import Path

# 直跑（dev）时补仓库根上 sys.path；冻结环境 kovaak_tracker 来自 PYZ，无害。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

started = time.monotonic()
import numpy  # noqa: F401
from scipy.signal import find_peaks, savgol_filter  # noqa: F401

import kovaak_tracker.dynamic_clicking_analysis  # noqa: F401
import kovaak_tracker.tracking_analysis  # noqa: F401

print(
    "[prewarm-science] done in %.1fs" % (time.monotonic() - started),
    file=sys.stderr,
)
