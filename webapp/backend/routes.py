from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Path

from . import queue
from .config import VIDEO_TMP_DIR, MAX_VIDEO_BYTES
from .schemas import AnalyzeResponse, SessionStatus

router = APIRouter(prefix="/api")


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    video: UploadFile = File(...),
    csv: UploadFile = File(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    """接收 flicking 视频 + Stats CSV,入队异步分析。

    限制:单用户同时 1 个 job(并发防滥用);视频 100MB 上限。
    user_id 暂从 X-User-Id header(切片 1 占位,切片 3 加 Clerk 后换 session)。
    """
    if await queue.has_active(x_user_id):
        raise HTTPException(429, "已有分析进行中,等完成再提交")

    content = await video.read()
    if len(content) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "视频超过 100MB 限制")

    # 文件名 sanitize:uuid 防冲突 + 防路径遍历(不信任用户 filename)
    import os
    import uuid
    video_ext = os.path.splitext(video.filename or "video.mp4")[1] or ".mp4"
    csv_ext = os.path.splitext(csv.filename or "stats.csv")[1] or ".csv"
    video_path = VIDEO_TMP_DIR / f"{x_user_id}_{uuid.uuid4().hex[:8]}{video_ext}"
    csv_path = VIDEO_TMP_DIR / f"{x_user_id}_{uuid.uuid4().hex[:8]}{csv_ext}"
    video_path.write_bytes(content)
    csv_path.write_bytes(await csv.read())

    sid = await queue.enqueue(x_user_id, str(video_path), str(csv_path))
    return AnalyzeResponse(session_id=sid)


@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(session_id: int = Path(...)):
    """查询分析状态/结果(queued / running / done / failed)。"""
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    return SessionStatus(
        id=s["id"],
        status=s["status"],
        result=s["result"],
        error=s["error"],
        llm_cost_cny=float(s["llm_cost_cny"] or 0),
    )
