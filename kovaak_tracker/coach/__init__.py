"""AI aim coach: single-shot coaching output (diagnosis -> viz -> narration).

``build_report`` 采用惰性导入——它依赖 numpy/plotly 等重库（经 visualization）；
纯逻辑子模块（advice / diagnosis / knowledge）不应因此被拖累，须能
在不安装重依赖时独立导入与测试。这与 providers.py 把 anthropic/openai 放进
``__init__`` 惰性导入是同一模式。

要拿 build_report：``from kovaak_tracker.coach import build_report``（首次访问触发
import）或直接 ``from kovaak_tracker.coach.report import build_report``。
"""
from __future__ import annotations

__all__ = ["build_report"]


def __getattr__(name):
    if name == "build_report":
        from .report import build_report
        return build_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
