from __future__ import annotations

import datetime
import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Form, UploadFile, File, Header, HTTPException, Path
from fastapi.responses import FileResponse

from . import coach_store, db, queue
from .coach_service import run_chat_turn
from .config import MAX_CSV_BYTES, MAX_VIDEO_BYTES, VIDEO_TMP_DIR
from .contracts import UnsupportedContractVersion, analysis_result_to_coach_report
from .queue import (
    RetryNotAllowed,
    SessionForbidden,
    SessionNotDeletable,
    SessionNotFound,
)
from .schemas import (
    AnalyzeResponse,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CoachAnalysisRefOut,
    CoachPrimaryAttachRequest,
    CoachPrimaryAttachResponse,
    CoachPrimaryMessageRequest,
    CoachPrimaryMessageResponse,
    CoachPrimaryResponse,
    CoachThreadMessageOut,
    CoachThreadOut,
    DeleteSessionResponse,
    SessionListItem,
    SessionListResponse,
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


async def _get_owned_session(session_id: int, x_user_id: str) -> dict:
    try:
        s = await queue.get_session(session_id)
    except UnsupportedContractVersion as exc:
        log.error(
            "unsupported analysis contract session_id=%s version=%s",
            session_id,
            exc,
        )
        raise HTTPException(500, "分析结果版本不受支持") from exc
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    return s


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


def _session_status_response(s: dict) -> SessionStatus:
    return SessionStatus(
        id=s["id"],
        status=s["status"],
        result=s["result"],
        error=s["error"],
        llm_cost_cny=float(s["llm_cost_cny"] or 0),
        created_at=s["created_at"],
        attempts=int(s["attempts"] or 0),
        max_attempts=int(s["max_attempts"] or 1),
        worker_id=s.get("worker_id"),
        started_at=s.get("started_at"),
        finished_at=s.get("finished_at"),
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """当前用户的分析列表(新→旧)。不返回完整 result。"""
    rows = await queue.list_sessions(x_user_id)
    return SessionListResponse(
        sessions=[SessionListItem(**row) for row in rows],
    )


@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """查询分析状态/结果(queued / running / done / failed)。"""
    s = await _get_owned_session(session_id, x_user_id)
    return _session_status_response(s)


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
async def delete_session(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """删除 session 行、chat 记录，并 best-effort 删除磁盘上的 video/csv。"""
    try:
        out = await queue.delete_session(session_id, x_user_id)
    except SessionNotFound as exc:
        raise HTTPException(404, "session 不存在") from exc
    except SessionForbidden as exc:
        raise HTTPException(403, "无权访问此 session") from exc
    except SessionNotDeletable as exc:
        raise HTTPException(409, exc.message) from exc
    return DeleteSessionResponse(**out)


@router.post("/sessions/{session_id}/retry", response_model=SessionStatus)
async def retry_session(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """将 failed session 重新入队(输入文件仍在时)。"""
    s = await _get_owned_session(session_id, x_user_id)
    if await queue.has_active(x_user_id):
        # Allow retry of this session itself if it's the only failed one —
        # has_active only counts queued/running, so failed is fine.
        pass
    try:
        updated = await queue.requeue_for_retry(session_id)
    except RetryNotAllowed as exc:
        status = 404 if exc.code == "not_found" else 409
        raise HTTPException(status, exc.message) from exc
    return _session_status_response(updated)




def _reconstruct_diagnosis(result: dict):
    """从 coach view dict 重建 CoachDiagnosis。

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




async def _diagnosis_from_done_session(s: dict):
    result = s.get("result") or {}
    if not isinstance(result, dict):
        raise HTTPException(409, "诊断结果缺失,暂不可对话")
    coach_view = analysis_result_to_coach_report(result)
    if not coach_view.get("diagnosis"):
        raise HTTPException(409, "诊断结果缺失,暂不可对话")
    return _reconstruct_diagnosis(coach_view)


def _coach_thread_message_out(m: dict) -> CoachThreadMessageOut:
    return CoachThreadMessageOut(
        id=int(m["id"]),
        role=m["role"],
        content=m["content"],
        created_at=m["created_at"],
        legacy_session_id=m.get("legacy_session_id"),
    )


def _coach_ref_out(r: dict) -> CoachAnalysisRefOut:
    return CoachAnalysisRefOut(
        id=int(r["id"]),
        analysis_session_id=r.get("analysis_session_id"),
        status=r["status"],
        attached_at=r["attached_at"],
        deleted_at=r.get("deleted_at"),
    )


async def _build_coach_primary_response(x_user_id: str) -> CoachPrimaryResponse:
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    tid = int(thread["id"])
    messages = await coach_store.load_messages(tid)
    refs = await coach_store.list_analysis_refs(tid)
    return CoachPrimaryResponse(
        thread=CoachThreadOut(
            id=tid,
            user_id=thread["user_id"],
            kind=thread["kind"],
            created_at=thread["created_at"],
            updated_at=thread["updated_at"],
        ),
        messages=[_coach_thread_message_out(m) for m in messages],
        refs=[_coach_ref_out(r) for r in refs],
    )


async def _assert_analysis_ref_active(
    thread_id: int,
    analysis_session_id: int,
) -> None:
    refs = await coach_store.list_analysis_refs(thread_id)
    for r in refs:
        if r.get("analysis_session_id") == analysis_session_id:
            if r["status"] == "deleted":
                raise HTTPException(409, "分析已删除,不可作为对话上下文")
            return


async def _ensure_done_analysis_attached(
    x_user_id: str,
    thread_id: int,
    analysis_session_id: int,
) -> dict:
    s = await _get_owned_session(analysis_session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,不可附加")
    await _assert_analysis_ref_active(thread_id, analysis_session_id)
    await coach_store.attach_analysis_ref(thread_id, analysis_session_id)
    return s


async def _load_session_coach_messages(x_user_id: str, session_id: int) -> list[dict]:
    await coach_store.ensure_legacy_session_messages_migrated(session_id)
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    all_msgs = await coach_store.load_messages(int(thread["id"]))
    return [m for m in all_msgs if m.get("legacy_session_id") == session_id]


# ---------------------------------------------------------------------------
# Persistent Coach (primary thread)
# ---------------------------------------------------------------------------


@router.get("/coach/primary", response_model=CoachPrimaryResponse)
async def get_coach_primary(
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    return await _build_coach_primary_response(x_user_id)


@router.post("/coach/primary/attach", response_model=CoachPrimaryAttachResponse)
async def attach_coach_primary_analysis(
    body: CoachPrimaryAttachRequest = Body(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    await _ensure_done_analysis_attached(
        x_user_id, int(thread["id"]), body.analysis_session_id,
    )
    refs = await coach_store.list_analysis_refs(int(thread["id"]))
    ref = next(
        r for r in refs
        if r.get("analysis_session_id") == body.analysis_session_id
    )
    return CoachPrimaryAttachResponse(ref=_coach_ref_out(ref))


@router.post("/coach/primary/messages", response_model=CoachPrimaryMessageResponse)
async def post_coach_primary_message(
    body: CoachPrimaryMessageRequest = Body(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    user_msg = body.content.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    thread_id = int(thread["id"])
    prior = await coach_store.load_messages(thread_id)

    diagnosis = None
    cost_session_id: Optional[int] = None
    legacy_session_id: Optional[int] = None

    if body.analysis_session_id is not None:
        try:
            s = await _get_owned_session(body.analysis_session_id, x_user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(409, "分析已删除,不可作为对话上下文") from exc
            raise
        await _assert_analysis_ref_active(thread_id, body.analysis_session_id)
        if s["status"] != "done":
            raise HTTPException(409, "分析未完成,不可作为对话上下文")
        await coach_store.attach_analysis_ref(thread_id, body.analysis_session_id)
        diagnosis = await _diagnosis_from_done_session(s)
        cost_session_id = body.analysis_session_id
        legacy_session_id = body.analysis_session_id

    result = await run_chat_turn(
        x_user_id=x_user_id,
        thread_id=thread_id,
        prior_messages=prior,
        user_msg_to_store=user_msg,
        diagnosis=diagnosis,
        legacy_session_id=legacy_session_id,
        cost_session_id=cost_session_id,
    )

    now_ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    out_msgs = [
        _coach_thread_message_out({
            "id": 0,
            "role": "user",
            "content": user_msg,
            "created_at": now_ts,
            "legacy_session_id": legacy_session_id,
        }),
        _coach_thread_message_out({
            "id": 0,
            "role": "assistant",
            "content": result.assistant_content,
            "created_at": now_ts,
            "legacy_session_id": legacy_session_id,
        }),
    ]
    return CoachPrimaryMessageResponse(
        reply=result.reply,
        notes=result.notes,
        messages=out_msgs,
    )

# ---------------------------------------------------------------------------
# Coach 多轮对话
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(
    session_id: int = Path(...),
    body: ChatRequest = Body(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """多轮对话(兼容层):写入用户 primary thread + 附加本 session 分析 ref。"""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")

    user_msg = body.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    pinned = body.pinned_frame_sec
    if pinned is not None and pinned >= 0:
        mm, ss = divmod(int(pinned), 60)
        user_msg_to_store = f"[锁定 {mm}:{ss:02d}] {user_msg}"
    else:
        user_msg_to_store = user_msg

    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    thread_id = int(thread["id"])
    await coach_store.attach_analysis_ref(thread_id, session_id)
    history = await _load_session_coach_messages(x_user_id, session_id)
    diagnosis = await _diagnosis_from_done_session(s)

    result = await run_chat_turn(
        x_user_id=x_user_id,
        thread_id=thread_id,
        prior_messages=history,
        user_msg_to_store=user_msg_to_store,
        diagnosis=diagnosis,
        legacy_session_id=session_id,
        cost_session_id=session_id,
    )

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
    out_history.append(ChatMessageOut(
        role="assistant", content=result.assistant_content, created_at=now_ts,
    ))
    return ChatResponse(
        reply=result.reply,
        history=out_history,
        notes=result.notes,
    )


@router.get("/sessions/{session_id}/chat", response_model=ChatResponse)
async def get_chat_history(
    session_id: int = Path(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """页面 mount 时拉历史对话(primary thread 中 legacy_session_id 过滤)。"""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")
    history = await _load_session_coach_messages(x_user_id, session_id)
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
    s = await _get_owned_session(session_id, x_user_id)
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

    数据源: analysis_result_to_coach_report 后的 timeline + diagnosis.meta。
    """
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")

    result = s.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    coach_view = analysis_result_to_coach_report(result)
    meta = (coach_view.get("diagnosis") or {}).get("meta") or {}

    fps = 60
    if isinstance(meta.get("fps"), (int, float)):
        fps = int(meta["fps"])

    # duration:优先 meta.duration_s,fallback meta.duration_frames
    duration_frames = 0
    if isinstance(meta.get("duration_frames"), (int, float)):
        duration_frames = int(meta["duration_frames"])
    elif isinstance(meta.get("duration_s"), (int, float)):
        duration_frames = int(meta["duration_s"] * fps)

    events_raw = coach_view.get("timeline") or []
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