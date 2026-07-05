from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AnalyzeResponse(BaseModel):
    session_id: int


class SessionStatus(BaseModel):
    id: int
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
    llm_cost_cny: Optional[float] = None


class ChatRequest(BaseModel):
    message: str
    """可选:用户"锁定当前时间轴"时附的视频秒数。后端把它拼到 user
    message 前缀([锁定 0:23] ...)传给 agent——agent 不感知字段,靠文本提示。"""
    pinned_frame_sec: Optional[float] = None


class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: str


class ChatResponse(BaseModel):
    reply: Optional[str] = None
    history: list[ChatMessageOut] = []
    notes: list[str] = []


class TimelineEvent(BaseModel):
    frame: int
    time_s: float
    type: str   # "kill" | "miss" | "peak" | "corrective"
    label: str


class Timeline(BaseModel):
    fps: int
    duration_frames: int
    events: list[TimelineEvent] = []
