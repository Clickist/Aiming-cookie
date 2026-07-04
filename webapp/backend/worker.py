from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Tuple

from . import queue
from .config import LLM_PROVIDER

log = logging.getLogger(__name__)


# --- 包装 kovaak_tracker(隔离 + 便于 mock;真实对接 Task 8 E2E 校准)---

def run_analysis(video_path: str, csv_path: str) -> dict:
    """调 kovaak_tracker.analyze_flicking_video,返回 summary dict。

    实际签名/返回结构在 Task 8 E2E 对照真实接口调整。
    """
    from kovaak_tracker.pan_tracker import analyze_flicking_video
    return analyze_flicking_video(video_path, csv_path)


def build_report(summary: dict) -> dict:
    """调 coach.build_report,返回结构化诊断(dataclass → dict)。"""
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report as _br
    report = _br(summary)
    if is_dataclass(report):
        return asdict(report)
    return {"_raw": str(report)}


def call_llm(report_dict: dict) -> Tuple[str, float]:
    """调 DeepSeek 生成 narration,返回 (文本, 成本 ¥)。失败 raise。

    report_dict → CoachDiagnosis 的重建在 Task 8 E2E 按真实字段对接。
    """
    from kovaak_tracker.coach.providers import load_backend
    from kovaak_tracker.coach.narrator import generate_narration
    backend = load_backend(LLM_PROVIDER)
    diagnosis = _diagnosis_from_report(report_dict)
    narration = generate_narration(diagnosis, backend)
    cost = _estimate_llm_cost_cny(narration)
    return narration, cost


def _diagnosis_from_report(report_dict: dict):
    """从 report dict 取 CoachDiagnosis 给 narrator。Task 8 E2E 调整真实字段。"""
    d = report_dict.get("diagnosis") if isinstance(report_dict, dict) else None
    return d if d is not None else report_dict


def _estimate_llm_cost_cny(text: str, input_tokens: int = 2000) -> float:
    """DeepSeek deepseek-chat 粗估:¥1/1M input,¥2/1M output。

    真实 token 数要 backend 返回 usage,切片 3 部署时接 DeepSeek 真实字段。
    """
    output_tokens = len(text) // 2  # 中文 ~2 字/token
    return input_tokens * 1e-6 * 1 + output_tokens * 1e-6 * 2


def _delete_video_safely(path) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删视频失败 %s: %s", path, e)


# --- 编排 ---

async def process_one() -> bool:
    """处理一个 job。True=处理了(无论成败),False=队列空。"""
    job = await queue.claim_next()
    if job is None:
        return False
    sid = job["id"]
    try:
        summary = run_analysis(job["video_path"], job["csv_path"])
        report_dict = build_report(summary)
        try:
            narration, cost = call_llm(report_dict)
            report_dict["narration"] = narration
        except Exception as e:
            log.warning("LLM 失败,降级无 narration: %s", e)
            cost = 0.0
        await queue.mark_done(sid, report_dict, cost)
    except Exception as e:
        log.exception("分析失败 session=%s", sid)
        await queue.mark_failed(sid, str(e))
    finally:
        _delete_video_safely(job["video_path"])
    return True


async def _run_loop_async() -> None:
    """单 event loop 跑消费循环(db._conn 不跨 loop)。"""
    while True:
        try:
            handled = await process_one()
        except Exception:
            log.exception("process_one 异常")
            handled = False
        if not handled:
            await asyncio.sleep(2)


def run_loop() -> None:
    """阻塞消费循环入口(worker 进程 main)。"""
    asyncio.run(_run_loop_async())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    log.info("Aiming Cookie worker 启动")
    run_loop()
