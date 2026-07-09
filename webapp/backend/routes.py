from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Form, UploadFile, File, Header, HTTPException, Path
from fastapi.responses import FileResponse

from . import db, llm_budget, queue
from .config import LLM_PROVIDER, MAX_CSV_BYTES, MAX_VIDEO_BYTES, VIDEO_TMP_DIR
from .schemas import (
    AnalyzeResponse,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    SessionStatus,
    Timeline,
    TimelineEvent,
)

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

# X-User-Id 校验:防路径穿越(user_id 直接拼进 VIDEO_TMP_DIR 文件名)。
# 只允许字母/数字/下划线/短横,1-64 字符。切片 3 加 Clerk 后换内部 session。
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ALLOWED_VIDEO_EXTS = {".mp4"}
_ALLOWED_CSV_EXTS = {".csv"}


def _assert_session_owner(s: dict, x_user_id: str) -> None:
    """IDOR 防护:校验 session 属于调用者。v1 最小方案(X-User-Id 自报无签名,
    防 session_id 枚举读他人数据/花他人 budget);切片 3 换 Clerk session token
    + 服务端验签后由鉴权中间件取代。"""
    if s["user_id"] != x_user_id:
        raise HTTPException(403, "无权访问此 session")


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    video: UploadFile = File(...),
    csv: UploadFile = File(...),
    cm_per_360: Optional[float] = Form(default=None),
    fov: Optional[float] = Form(default=None),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """接收 flicking 视频 + Stats CSV,入队异步分析。

    限制:单用户同时 1 个 job(并发防滥用);视频 100MB 上限。
    user_id 暂从 X-User-Id header(切片 1 占位,切片 3 加 Clerk 后换 session)。
    """
    # user_id 校验(防路径穿越:它直接拼进 VIDEO_TMP_DIR 文件名)
    if not _USER_ID_RE.match(x_user_id):
        raise HTTPException(400, "X-User-Id 含非法字符(只允许字母数字_-)")
    if await queue.has_active(x_user_id):
        raise HTTPException(429, "已有分析进行中,等完成再提交")

    # 大小预检:用 UploadFile.size(Starlette ≥0.36,multipart 解析时已知)
    # 而非 await read(),避免多 GB 上传先全量载入内存再 413 的 OOM。
    if video.size is not None and video.size > MAX_VIDEO_BYTES:
        raise HTTPException(413, "视频超过 100MB 限制")
    if csv.size is not None and csv.size > MAX_CSV_BYTES:
        raise HTTPException(413, f"CSV 超过 {MAX_CSV_BYTES // 1024 // 1024}MB 限制")

    # 文件名 sanitize:uuid 防冲突 + 扩展名白名单(不信任用户 filename 后缀)
    video_ext = os.path.splitext(video.filename or "video.mp4")[1].lower()
    if video_ext not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"视频扩展名不支持(仅 .mp4): {video_ext or '(无)'}")
    csv_ext = os.path.splitext(csv.filename or "stats.csv")[1].lower()
    if csv_ext not in _ALLOWED_CSV_EXTS:
        raise HTTPException(400, f"CSV 扩展名不支持(仅 .csv): {csv_ext or '(无)'}")
    video_path = VIDEO_TMP_DIR / f"{x_user_id}_{uuid.uuid4().hex[:8]}{video_ext}"
    csv_path = VIDEO_TMP_DIR / f"{x_user_id}_{uuid.uuid4().hex[:8]}{csv_ext}"
    try:
        video_path.write_bytes(await video.read())
        csv_path.write_bytes(await csv.read())
        sid = await queue.enqueue(
            x_user_id, str(video_path), str(csv_path),
            cm_per_360=cm_per_360, fov=fov,
        )
    except Exception:
        # enqueue 失败 → 清理刚写的临时文件,避免孤儿堆积
        for p in (video_path, csv_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        raise
    return AnalyzeResponse(session_id=sid)


@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """查询分析状态/结果(queued / running / done / failed)。"""
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    return SessionStatus(
        id=s["id"],
        status=s["status"],
        result=s["result"],
        error=s["error"],
        llm_cost_cny=float(s["llm_cost_cny"] or 0),
    )


# ---------------------------------------------------------------------------
# Coach 多轮对话
# ---------------------------------------------------------------------------


def _reconstruct_diagnosis(result: dict):
    """从 sessions.result 存的 CoachReport dict 重建 CoachDiagnosis。

    worker.run_report 用 dataclasses.asdict 把 CoachReport 序列化,所以
    diagnosis 子树是纯 dict + list。手动反序列化(frozen dataclass 无 from_dict)。
    """
    from kovaak_tracker.advice import Prescription
    from kovaak_tracker.coach.diagnosis import (
        CoachDiagnosis, DiagnosisIssue, ProfileMatch, RootCause,
    )

    d = result.get("diagnosis") or {}
    profile_d = d.get("profile") or {}
    profile = ProfileMatch(
        archetype_id=profile_d.get("archetype_id", "unclassified"),
        label=profile_d.get("label", "未分类"),
        confidence=float(profile_d.get("confidence", 0.0)),
        secondary_tags=list(profile_d.get("secondary_tags", [])),
    )
    issues = []
    for i in d.get("issues") or []:
        rcs = [RootCause(level=rc.get("level", "symptom"),
                         text=rc.get("text", ""))
               for rc in (i.get("root_causes") or [])]
        rx = [Prescription(scenario=p.get("scenario", ""),
                           reason=p.get("reason", ""))
              for p in (i.get("prescriptions") or [])]
        issues.append(DiagnosisIssue(
            signal=i.get("signal", ""),
            severity=i.get("severity", "info"),
            root_causes=rcs,
            prescriptions=rx,
            priority=int(i.get("priority", 99)),
            priority_reason=i.get("priority_reason", ""),
        ))
    return CoachDiagnosis(
        profile=profile,
        issues=issues,
        summary=d.get("summary") or {},
        comparison=d.get("comparison"),
        meta=d.get("meta") or {},
    )


def _load_backend_or_none():
    """加载 LLM backend;失败返回 None(由 caller 决定降级文案)。"""
    try:
        from kovaak_tracker.coach.providers import load_backend
        return load_backend(LLM_PROVIDER)
    except Exception as e:
        log.warning("load_backend 失败,chat 走降级: %s", e)
        return None


@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(
    session_id: int = Path(...),
    body: ChatRequest = Body(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """多轮对话:用户提问 → coach 回复。

    前置:session 必须 status=done(有诊断结果才能聊)。否则 409。
    """
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")
    result = s.get("result") or {}
    if not isinstance(result, dict) or "diagnosis" not in result:
        raise HTTPException(409, "诊断结果缺失,暂不可对话")

    # 拉历史(喂给 agent + 响应历史拼装基础)。append 本轮 user message 在
    # budget 预检之后,避免 429 时留孤立 user message。
    history = await db.load_chat_history(session_id)

    user_msg = body.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # chat 也走 LLM——预检查日预算(chat 不经 worker.process_one,需显式检查)。
    # 单次保守估算(DeepSeek deepseek-chat):2000 input + 500 output ≈ ¥0.003。
    # 真实 usage 切片 3 接 DeepSeek 字段后换精确值。
    from .worker import _estimate_llm_cost_cny
    chat_cost = _estimate_llm_cost_cny("", min_output_tokens=500)
    if not await llm_budget.check_and_record(s["user_id"], chat_cost):
        raise HTTPException(429, "今日 LLM 预算已用尽,明天再聊")

    # 用户"锁定当前时间轴"时,前端附 pinned_frame_sec。把它拼成可读前缀
    # ([锁定 0:23] ...)给 agent——这样 agent 不用感知字段,靠文本提示就能
    # 知道用户当前在看视频的哪一段。
    pinned = body.pinned_frame_sec
    if pinned is not None and pinned >= 0:
        mm, ss = divmod(int(pinned), 60)
        user_msg_to_store = f"[锁定 {mm}:{ss:02d}] {user_msg}"
    else:
        user_msg_to_store = user_msg
    await db.save_chat_message(session_id, "user", user_msg_to_store)

    notes: list[str] = []
    reply: Optional[str] = None
    try:
        from kovaak_tracker.coach.agent import ChatMessage, chat_with_coach

        diagnosis = _reconstruct_diagnosis(result)
        # 历史 + 本轮 user message 喂给 agent
        chat_history = [
            ChatMessage(role=m["role"], content=m["content"]) for m in history
        ]
        chat_history.append(ChatMessage(role="user", content=user_msg_to_store))

        backend = _load_backend_or_none()
        if backend is None:
            notes.append("LLM 后端不可用,本次未生成回复")
        else:
            # chat_with_coach 是同步阻塞 LLM 调用(10-30s),丢线程池避免阻塞
            # event loop(其他请求/worker 并发不被 hold)。
            reply = await asyncio.to_thread(
                chat_with_coach, diagnosis, chat_history, backend,
            )
            if reply is None:
                notes.append("agent 未在限定轮次内产出回复")
    except Exception as e:
        log.exception("chat_with_coach 失败 session=%s", session_id)
        notes.append(f"对话失败: {e}")

    # 持久化 assistant 回复(即使是降级空回复也存一条,前端能感知"已处理")
    if reply is not None:
        await db.save_chat_message(session_id, "assistant", reply)
        # chat 成功(LLM 真调了)→ 累加 cost 到 session,下次 budget 检查反映真实累计
        await queue.add_llm_cost(session_id, chat_cost)
    else:
        await db.save_chat_message(
            session_id, "assistant",
            "(本次未能生成回复,见 notes)", None,
        )

    # 响应 history 用首次 load 的结果 + 本轮 user/assistant 本地拼装,
    # 避免二次 DB 查询。新消息 created_at 用当前 UTC 时间近似(展示用,
    # 与 DB 的 CURRENT_TIMESTAMP 格式一致)。
    now_ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    out_history = [
        ChatMessageOut(role=m["role"], content=m["content"],
                       created_at=m["created_at"])
        for m in history
    ]
    out_history.append(ChatMessageOut(
        role="user", content=user_msg_to_store, created_at=now_ts,
    ))
    assistant_content = reply if reply is not None else "(本次未能生成回复,见 notes)"
    out_history.append(ChatMessageOut(
        role="assistant", content=assistant_content, created_at=now_ts,
    ))
    return ChatResponse(reply=reply, history=out_history, notes=notes)


@router.get("/sessions/{session_id}/chat", response_model=ChatResponse)
async def get_chat_history(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """页面 mount 时拉历史对话。session 不存在 → 404;未 done → 409。"""
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")
    history = await db.load_chat_history(session_id)
    return ChatResponse(
        reply=None,
        history=[ChatMessageOut(role=m["role"], content=m["content"],
                                created_at=m["created_at"]) for m in history],
        notes=[],
    )


# ---------------------------------------------------------------------------
# Coach 页:视频流 + 时间轴 markers
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/video")
async def get_session_video(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """流式返回 session 关联的视频文件(给 coach 页 <video src>)。

    路径从 sessions.video_path 取。worker 分析完后**不再删视频**(见
    worker.process_one 注释),所以 coach 页能播。文件不存在 → 404。
    """
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    video_path = s.get("video_path") or ""
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(404, "视频文件不存在或已归档")
    return FileResponse(video_path, media_type="video/mp4")


@router.get("/sessions/{session_id}/timeline", response_model=Timeline)
async def get_session_timeline(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """返回视频时间轴事件 markers。

    数据源优先级:
      1. result.timeline 字段(worker 持久化的 markers)——当前 worker 不写,
         预留兼容。
      2. result.meta.fps / result.meta.duration_s 推 fps + duration_frames。
      3. events 默认空(spec 兜底:v1 markers 区域留空,后续再补)。
    """
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")

    result = s.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    meta = (result.get("diagnosis") or {}).get("meta") or {}

    fps = 60
    if isinstance(meta.get("fps"), (int, float)):
        fps = int(meta["fps"])

    # duration:优先 meta.duration_s,fallback meta.duration_frames
    duration_frames = 0
    if isinstance(meta.get("duration_frames"), (int, float)):
        duration_frames = int(meta["duration_frames"])
    elif isinstance(meta.get("duration_s"), (int, float)):
        duration_frames = int(meta["duration_s"] * fps)

    events_raw = result.get("timeline") or []
    events = [
        TimelineEvent(
            frame=int(e.get("frame", 0)),
            time_s=float(e.get("time_s", 0.0)),
            type=str(e.get("type", "")),
            label=str(e.get("label", "")),
        )
        for e in events_raw
        if isinstance(e, dict) and e.get("type")
    ]
    return Timeline(fps=fps, duration_frames=duration_frames, events=events)
