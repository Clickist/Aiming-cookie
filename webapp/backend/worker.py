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
    build_analysis_result_v2,
    build_artifact_manifest_v2,
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


def run_native_analysis(
    snapshot: dict,
    cm_per_360: float | None = None,
    fov: float | None = None,
) -> dict:
    """Load a frozen Run snapshot and invoke the native flicking adapter."""
    from .kovaak_run_store import read_mouse_snapshot
    from kovaak_tracker.csv_parser import parse_stats_csv
    from kovaak_tracker.native_flicking_analysis import analyze_native_flicking
    from kovaak_tracker.performance_parser import parse_performance_file

    sources = snapshot.get("sources") or {}
    trace = snapshot.get("trace") or {}
    stats_path = (sources.get("stats") or {}).get("path")
    performance_path = (sources.get("performance") or {}).get("path")
    trace_path = trace.get("path")
    if not isinstance(stats_path, str) or not isinstance(performance_path, str):
        raise ValueError("native analysis requires stats and performance sources")
    if not isinstance(trace_path, str):
        raise ValueError("native analysis requires a raw input trace")

    parsed_stats = parse_stats_csv(stats_path)
    stats = {
        "summary": dict(parsed_stats.summary),
        "config": dict(parsed_stats.config),
        "scenario": parsed_stats.scenario,
        "cm_per_360": cm_per_360 if cm_per_360 is not None else parsed_stats.cm_per_360,
        "fov": fov if fov is not None else parsed_stats.fov,
    }
    return analyze_native_flicking(
        read_mouse_snapshot(trace_path),
        parse_performance_file(performance_path),
        stats=stats,
    )


def _native_deterministic_v2(native_result: dict) -> dict:
    """Adapt the native payload to v2's path-safe public contract."""
    deterministic = native_result.get("deterministic") or {}
    metrics = dict(deterministic.get("metrics") or {})
    if "path_length" in metrics:
        metrics["distance_raw_counts"] = metrics.pop("path_length")
    if "calibrated_path_length" in metrics:
        metrics["calibrated_distance"] = metrics.pop("calibrated_path_length")
    trajectory = deterministic.get("trajectory") or {}
    public_trajectory = {
        "unit": trajectory.get("unit", "raw_counts"),
        "point_count": int(trajectory.get("point_count") or 0),
    }
    return {
        "status": native_result.get("status", "unavailable"),
        "trajectory": public_trajectory,
        "metrics": metrics,
        "timeline": list(deterministic.get("timeline") or []),
        "limitations": list(native_result.get("limitations") or []),
    }


def _native_v2_evidence(
    native_result: dict,
    *,
    run_ref: str,
    video_availability: str | None = None,
) -> dict:
    native_evidence = native_result.get("evidence") or {}
    source_values = native_evidence.get("sources") or {}
    sources = {
        key: dict(value)
        for key, value in source_values.items()
        if isinstance(value, dict)
    }
    if video_availability is not None:
        sources["mp4"] = {
            "source": "mp4",
            "role": "cross_validation",
            "availability": video_availability,
            "alignment": "not_required",
            "warnings": [],
        }
    availability = {
        key: value.get("availability", "unavailable")
        for key, value in sources.items()
    }
    return {
        "sources": sources,
        "provenance": {
            "kovaak_run_ref": run_ref,
            "adapter": "native_flicking_analysis",
        },
        "availability": availability,
        "alignment": dict(native_evidence.get("alignment") or {"status": "unavailable"}),
        "warnings": list(native_evidence.get("warnings") or []),
    }


def _native_artifact_manifest_v2(
    job: dict,
    snapshot: dict,
    *,
    include_video: bool,
) -> dict:
    external_inputs: list[dict] = []
    for kind, source in (snapshot.get("sources") or {}).items():
        if isinstance(source, dict) and source.get("artifact_ref"):
            external_inputs.append({
                "id": source["artifact_ref"],
                "kind": kind,
                "availability": source.get("availability", "unavailable"),
            })
    trace = snapshot.get("trace")
    if isinstance(trace, dict) and trace.get("artifact_ref"):
        external_inputs.append({
            "id": trace["artifact_ref"],
            "kind": "raw_input",
            "availability": trace.get("availability", "unavailable"),
        })
    if include_video:
        external_inputs.append({
            "id": f"analysis:{job['id']}:video",
            "kind": "mp4",
            "availability": "available" if job.get("video_path") else "missing",
        })
    return build_artifact_manifest_v2(
        external_inputs=external_inputs,
        owned_outputs=[{"id": f"analysis:{job['id']}", "kind": "analysis_result"}],
    )


def _build_native_result_v2(
    job: dict,
    native_result: dict,
    *,
    created_at: str,
    completed_at: str,
    video_availability: str | None = None,
    warnings: list[dict] | None = None,
    visual_validation: dict | None = None,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    if run_id is None:
        raise ValueError("native analysis requires kovaak_run_id")
    run_ref = f"run:{run_id}"
    deterministic = _native_deterministic_v2(native_result)
    if visual_validation is not None:
        deterministic["visual_validation"] = visual_validation
    return build_analysis_result_v2(
        analysis_id=f"analysis:{job['id']}",
        analysis_type=native_result.get("analysis_type", "flicking"),
        input_mode=job.get("input_mode") or "input_native",
        kovaak_run_ref=run_ref,
        evidence=_native_v2_evidence(
            native_result,
            run_ref=run_ref,
            video_availability=video_availability,
        ),
        deterministic=deterministic,
        artifact_manifest=_native_artifact_manifest_v2(
            job,
            snapshot,
            include_video=video_availability is not None,
        ),
        input_snapshot=public_analysis_input_snapshot(snapshot),
        created_at=created_at,
        completed_at=completed_at,
        warnings=list(warnings or []),
        errors=[],
    )


def _build_video_fallback_result_v2(
    job: dict,
    report: dict,
    timeline: list[dict],
    *,
    created_at: str,
    completed_at: str,
    narration_status: str,
) -> dict:
    from .kovaak_run_store import public_analysis_input_snapshot

    snapshot = job.get("input_snapshot") or {}
    run_id = job.get("kovaak_run_id") or snapshot.get("run_id")
    run_ref = f"run:{run_id}" if run_id is not None else None
    stats_ref = f"analysis:{job['id']}:stats"
    video_ref = f"analysis:{job['id']}:video"

    if run_ref is not None:
        public_snapshot = public_analysis_input_snapshot(snapshot)
        public_snapshot["sources"] = {
            key: value
            for key, value in public_snapshot.get("sources", {}).items()
            if key == "stats"
        }
        stats_source = public_snapshot["sources"].get("stats")
        if stats_source and stats_source.get("artifact_ref"):
            stats_ref = stats_source["artifact_ref"]
    else:
        stats_source = {
            "artifact_ref": stats_ref,
            "availability": "available" if job.get("csv_path") else "missing",
        }
        public_snapshot = {
            "schema_version": "analysis_input_snapshot.v1",
            "run_id": None,
            "scenario": None,
            "sources": {"stats": stats_source},
            "trace": None,
        }

    public_snapshot["sources"]["video"] = {
        "artifact_ref": video_ref,
        "availability": "available" if job.get("video_path") else "missing",
    }
    public_snapshot["trace"] = None
    public_snapshot["calibration"] = {
        "cm_per_360": job.get("cm_per_360"),
        "fov": job.get("fov"),
    }
    stats_availability = (
        "available"
        if stats_source and stats_source.get("availability") == "available"
        else "missing"
    )
    sources = {
        "stats": {
            "source": "stats",
            "role": "scenario_facts",
            "availability": stats_availability,
            "alignment": "not_required",
            "warnings": [],
        },
        "mp4": {
            "source": "mp4",
            "role": "visual_analysis",
            "availability": "available" if job.get("video_path") else "missing",
            "alignment": "not_required",
            "warnings": [],
        },
    }
    external_inputs = [
        {
            "id": stats_ref,
            "kind": "stats",
            "availability": stats_availability,
        },
        {
            "id": video_ref,
            "kind": "mp4",
            "availability": "available" if job.get("video_path") else "missing",
        },
    ]
    narration = report.get("narration") if narration_status == "available" else None
    provenance = {"adapter": "video_flicking_fair_summary"}
    if run_ref is not None:
        provenance["kovaak_run_ref"] = run_ref
    result = build_analysis_result_v2(
        analysis_id=f"analysis:{job['id']}",
        analysis_type="flicking",
        input_mode="video_fallback",
        kovaak_run_ref=run_ref,
        evidence={
            "sources": sources,
            "provenance": provenance,
            "availability": {
                key: value["availability"] for key, value in sources.items()
            },
            "alignment": {"status": "not_required"},
            "warnings": [],
        },
        deterministic={
            "status": "available",
            "diagnosis": report.get("diagnosis", {}),
            "figures": report.get("figures", {}),
            "timeline": timeline,
            "limitations": ["raw_input_not_used"],
        },
        artifact_manifest=build_artifact_manifest_v2(
            external_inputs=external_inputs,
            owned_outputs=[{"id": f"analysis:{job['id']}", "kind": "analysis_result"}],
        ),
        input_snapshot=public_snapshot,
        created_at=created_at,
        completed_at=completed_at,
        warnings=[{"code": "raw_input_not_used"}],
        errors=[],
    )
    result["narration"] = {
        "status": narration_status,
        "text": narration,
        "provider": None,
        "model": None,
        "usage": None,
    }
    return result


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
        input_mode = job.get("input_mode") or "video_fallback"
        created_at_iso = _sqlite_created_at_to_iso_z(job.get("created_at"))
        completed_at_iso = _utc_now_iso_z()

        if input_mode in {"input_native", "multimodal"}:
            native_result = await asyncio.to_thread(
                run_native_analysis,
                job.get("input_snapshot") or {},
                job.get("cm_per_360"),
                job.get("fov"),
            )
            video_availability = None
            warnings: list[dict] = []
            visual_validation = None
            if input_mode == "multimodal":
                try:
                    stats_path = ((job.get("input_snapshot") or {}).get("sources") or {}).get(
                        "stats", {}
                    ).get("path")
                    if not isinstance(stats_path, str):
                        raise ValueError("multimodal analysis requires stats source")
                    _, visual_extras = await asyncio.to_thread(
                        run_analysis,
                        job["video_path"],
                        stats_path,
                        job.get("cm_per_360"),
                        job.get("fov"),
                    )
                    video_availability = "available"
                    visual_validation = {
                        "status": "available",
                        "timeline": _build_timeline(visual_extras),
                    }
                except Exception:
                    log.warning("multimodal video validation unavailable session=%s", sid)
                    video_availability = "unavailable"
                    warnings.append({"code": "video_cv_unavailable"})
            result = _build_native_result_v2(
                job,
                native_result,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                video_availability=video_availability,
                warnings=warnings,
                visual_validation=visual_validation,
            )
            cost = 0.0
        else:
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

            result = _build_video_fallback_result_v2(
                job,
                report_dict,
                timeline_events,
                created_at=created_at_iso,
                completed_at=completed_at_iso,
                narration_status=narration_status,
            )
        if not await queue.mark_done(sid, result, cost, worker_id=WORKER_ID):
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
