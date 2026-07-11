from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timezone

from . import queue
from .config import HEARTBEAT_INTERVAL_SECONDS, LLM_PROVIDER
from .contracts import (
    build_analysis_result_v1,
    build_artifact_manifest_v1,
    build_error_v1,
)

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def _heartbeat_loop(session_id: int, stop: asyncio.Event) -> None:
    """Renew lease until stop is set. First beat immediately, then every interval."""
    while True:
        try:
            await queue.heartbeat(session_id, WORKER_ID)
        except Exception:
            log.exception(
                "heartbeat failed session=%s worker=%s", session_id, WORKER_ID,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            continue


# --- 包装 kovaak_tracker(隔离 + 便于 mock)---

def run_analysis(
    video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
) -> tuple[dict, dict]:
    """调 kovaak_tracker.analyze_flicking_fair_summary,返回 (summary, extras)。

    cm_per_360 / fov 优先用 caller 传的(用户填);若 None,从 CSV fallback:
      - fov:csv_parser stats.fov(KovaaK CSV 的 FOV 字段)
      - cm_per_360:csv_parser stats.cm_per_360(DPI + Horiz Sens + Sens Scale yaw 表)
    传给 analyze_flicking_fair_summary 影响 deg_per_px(fov) + peak_cm_per_s(cm/360)。
    """
    from kovaak_tracker.csv_parser import parse_stats_csv
    from kovaak_tracker.pan_tracker import analyze_flicking_fair_summary

    # CSV fallback(若 caller 没传):从 KovaaK CSV config 块读真实值
    if cm_per_360 is None or fov is None:
        stats = parse_stats_csv(csv_path)
        if cm_per_360 is None:
            cm_per_360 = stats.cm_per_360
        if fov is None:
            fov = stats.fov

    summary, extras = analyze_flicking_fair_summary(
        video_path, csv_path, fov=fov, cm_per_360=cm_per_360, return_extras=True,
    )
    return summary, extras


def _build_timeline(extras: dict) -> list[dict]:
    """把 analyze_flicking_fair_summary 的 extras 转成 timeline events 列表。

    schema(routes.get_session_timeline 消费):
        {"frame": int, "time_s": float, "type": str, "label": str}
    types: "kill" | "peak" | "corrective"。flicking pipeline 没有 miss 概念
    (那是 tracking 的事),所以这里不产 miss markers。
    """
    if not isinstance(extras, dict):
        return []
    fps = extras.get("fps") or 60
    if fps <= 0:
        fps = 60
    events: list[dict] = []

    def _add(frame: int, type_: str, label: str) -> None:
        if frame is None or frame < 0:
            return
        events.append({
            "frame": int(frame),
            "time_s": round(frame / fps, 3),
            "type": type_,
            "label": label,
        })

    for flick in extras.get("flicks") or []:
        peak_frame = flick.get("peak_frame")
        if peak_frame is not None:
            _add(peak_frame, "peak", "速度峰值")
    for frame in extras.get("corrective_frames") or []:
        _add(frame, "corrective", "修正")
    for frame in extras.get("kill_frames") or []:
        _add(frame, "kill", "击杀")

    # 按 frame 升序排,方便前端顺序渲染。
    events.sort(key=lambda e: e["frame"])
    return events


def run_report(summary: dict, backend) -> dict:
    """调 coach.build_report(传 backend 拿 narration),返回 CoachReport dict。

    build_report 内部 best-effort 调 generate_narration:LLM 失败时 narration=None
    + notes 记错,**不崩**。所以 worker 不用单独 try LLM。
    figures(plotly Figure)转 to_dict() 使其 JSON 可序列化(mark_done 要 json.dumps)。
    """
    from dataclasses import asdict, is_dataclass
    from kovaak_tracker.coach.report import build_report
    report = build_report(summary, backend=backend)
    d = asdict(report) if is_dataclass(report) else {"_raw": str(report)}
    # plotly Figure 不可 JSON 序列化 → 转 dict
    figures = d.get("figures")
    if isinstance(figures, dict):
        d["figures"] = {
            k: (f.to_dict() if hasattr(f, "to_dict") else f)
            for k, f in figures.items()
        }
    return d


def _load_backend():
    """加载 LLM backend(DeepSeek 默认,providers.json 配置)。"""
    from kovaak_tracker.coach.providers import load_backend
    return load_backend(LLM_PROVIDER)


def _estimate_llm_cost_cny(
    text: str, input_tokens: int = 2000, min_output_tokens: int = 500,
) -> float:
    """DeepSeek deepseek-chat 粗估:¥1/1M input,¥2/1M output。

    真实 token 数要 backend 返回 usage,切片 3 部署时接 DeepSeek 真实字段。
    min_output_tokens 给保守下界——预检查时 narration 还没生成,按至少
    500 output 估算,避免低估让预算检查形同虚设(review:之前传空串 cost≈0)。
    """
    output_tokens = max(len(text or "") // 2, min_output_tokens)  # 中文 ~2 字/token
    return input_tokens * 1e-6 * 1 + output_tokens * 1e-6 * 2


def _sqlite_created_at_to_iso_z(created_at: str | None) -> str:
    """SQLite ``YYYY-MM-DD HH:MM:SS`` → ``YYYY-MM-DDTHH:MM:SSZ`` (UTC)."""
    if not created_at or not str(created_at).strip():
        return _utc_now_iso_z()
    s = str(created_at).strip()
    if "T" in s:
        return s if s.endswith("Z") else f"{s}Z"
    return s.replace(" ", "T") + "Z"


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _delete_video_safely(path) -> None:
    """失败路径清理临时文件(视频/CSV)。

    用户上传的视频/CSV 是可再生副本(源在用户本地),属 CLAUDE.md §5 例外
    (regenerable 临时文件可 hard remove 而非走 Recycle Bin);且 worker
    批量清理场景下 os.remove 比 SendToRecycleBin 快千倍。函数名沿用历史。
    """
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删临时文件失败 %s: %s", path, e)


# --- 编排 ---

async def process_one() -> bool:
    """处理一个 job。True=处理了(无论成败),False=队列空。"""
    await queue.recover_stale_jobs()
    job = await queue.claim_next(WORKER_ID)
    if job is None:
        return False
    sid = job["id"]
    stop_hb = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(sid, stop_hb))
    try:
        summary, extras = await asyncio.to_thread(
            run_analysis,
            job["video_path"],
            job["csv_path"],
            job.get("cm_per_360"),
            job.get("fov"),
        )
        timeline_events = _build_timeline(extras)
        from . import llm_budget
        estimated_cost = _estimate_llm_cost_cny("")
        budget_ok = await llm_budget.check_and_record(job["user_id"], estimated_cost)
        backend_load_failed = False
        if not budget_ok:
            log.warning("用户 %s 今日 LLM 超额,narration 跳过", job["user_id"])
            report_dict = await asyncio.to_thread(run_report, summary, None)
            cost = 0.0
            narration_status = "not_requested"
        else:
            try:
                backend = _load_backend()
            except Exception as e:
                log.warning("_load_backend 失败,降级 backend=None: %s", e)
                backend = None
                backend_load_failed = True
            report_dict = await asyncio.to_thread(run_report, summary, backend)
            cost = _estimate_llm_cost_cny(report_dict.get("narration") or "")
            narration = report_dict.get("narration")
            if backend_load_failed or not (
                isinstance(narration, str) and narration.strip()
            ):
                narration_status = "unavailable"
            else:
                narration_status = "available"

        created_at_iso = _sqlite_created_at_to_iso_z(job.get("created_at"))
        completed_at_iso = _utc_now_iso_z()
        artifact_manifest = build_artifact_manifest_v1(
            video_path=job["video_path"],
            csv_path=job["csv_path"],
            created_at=created_at_iso,
        )
        result_v1 = build_analysis_result_v1(
            report=report_dict,
            timeline=timeline_events,
            narration_status=narration_status,
            cm_per_360=job.get("cm_per_360"),
            fov=job.get("fov"),
            artifact_manifest=artifact_manifest,
            created_at=created_at_iso,
            completed_at=completed_at_iso,
        )
        if not await queue.mark_done(sid, result_v1, cost, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        # 视频保留——coach 回放 + 失败重试；用户删除走 History 删除语义。
    except Exception:
        trace_id = str(uuid.uuid4())
        log.exception("分析失败 session=%s trace_id=%s", sid, trace_id)
        error_v1 = build_error_v1(
            category="internal_unknown",
            code="analysis_failed",
            message="分析失败，请重试；若持续失败请联系维护者。",
            retryable=True,
            trace_id=trace_id,
        )
        if not await queue.mark_failed(sid, error_v1, worker_id=WORKER_ID):
            log.warning("lost lease session=%s worker=%s", sid, WORKER_ID)
        # 不删输入文件：支持用户 retry；与「用户自己删」产品决定一致。
    finally:
        stop_hb.set()
        try:
            await hb_task
        except Exception:
            log.exception("heartbeat task join failed session=%s", sid)
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
            try:
                await queue.recover_stale_jobs()
            except Exception:
                log.exception("idle recover_stale_jobs 失败")
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