from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ErrorV1(BaseModel):
    """Wire Error v1 envelope (sessions.error after read-time coerce)."""

    schema_version: Literal["error.v1"]
    category: Literal[
        "input_validation",
        "local_cv_runtime",
        "llm_provider",
        "network_cloud",
        "storage_disk",
        "internal_unknown",
    ]
    code: str
    message: str
    retryable: bool
    trace_id: Optional[str] = None
    details: Optional[object] = None


class AnalyzeResponse(BaseModel):
    session_id: int


class SessionStatus(BaseModel):
    """GET /sessions/{id} — result is AnalysisResult v1 dict (validated at queue layer)."""

    id: int
    status: str
    result: Optional[dict] = None
    error: Optional[ErrorV1] = None
    llm_cost_cny: Optional[float] = None
    created_at: str
    attempts: int
    max_attempts: int
    worker_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class SessionListItem(BaseModel):
    id: int
    status: str
    created_at: str
    finished_at: Optional[str] = None
    attempts: int
    max_attempts: int
    llm_cost_cny: Optional[float] = None
    summary_label: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class DeleteSessionResponse(BaseModel):
    deleted: bool
    id: int
    files_removed: list[str]


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