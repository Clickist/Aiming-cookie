from __future__ import annotations

import asyncio
import logging
import os

from . import queue
from .config import LLM_PROVIDER

log = logging.getLogger(__name__)


# --- 包装 kovaak_tracker(隔离 + 便于 mock)---

def run_analysis(video_path: str, csv_path: str) -> dict:
    """调 kovaak_tracker.analyze_flicking_video,返回 FlickAnalysis.summary。

    analyze_flicking_video 返回 (FlickAnalysis, ChallengeWindow);
    FlickAnalysis.summary 是 {metric: {"med": v, ...}} dict(build_report 期望格式)。
    """
    from kovaak_tracker.pan_tracker import analyze_flicking_video
    fa, _window = analyze_flicking_video(video_path, csv_path)
    return fa.summary


def run_report(summary: dict, backend) -> dict:
    """调 coach.build_report(传 backend 拿 narration),返回 CoachReport dict。

    build_report 内部 best-effort 调 generate_narration:LLM 失败时 narration=None
    + notes 记错,**不崩**。所以 worker 不用单独 try LLM。
    """
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report
    report = build_report(summary, backend=backend)
    if is_dataclass(report):
        return asdict(report)
    return {"_raw": str(report)}


def _load_backend():
    """加载 LLM backend(DeepSeek 默认,providers.json 配置)。"""
    from kovaak_tracker.coach.providers import load_backend
    return load_backend(LLM_PROVIDER)


def _estimate_llm_cost_cny(text: str, input_tokens: int = 2000) -> float:
    """DeepSeek deepseek-chat 粗估:¥1/1M input,¥2/1M output。

    真实 token 数要 backend 返回 usage,切片 3 部署时接 DeepSeek 真实字段。
    """
    output_tokens = len(text or "") // 2  # 中文 ~2 字/token
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
        from . import llm_budget
        estimated_cost = _estimate_llm_cost_cny("")
        if not await llm_budget.check_and_record(job["user_id"], estimated_cost):
            log.warning("用户 %s 今日 LLM 超额,narration 跳过", job["user_id"])
            report_dict = run_report(summary, backend=None)
            cost = 0.0
        else:
            backend = _load_backend()
            report_dict = run_report(summary, backend=backend)
            cost = _estimate_llm_cost_cny(report_dict.get("narration") or "")
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
