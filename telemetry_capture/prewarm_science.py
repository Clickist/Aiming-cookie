# -*- coding: utf-8 -*-
"""手动运行的科学栈导入诊断。

不会自动启动；输出总导入耗时。
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
