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


class AnalyzePathsRequest(BaseModel):
    video_path: str
    csv_path: str
    cm_per_360: Optional[float] = None
    fov: Optional[float] = None


class KovaaKAnalysisRequest(BaseModel):
    """Create an Analysis from a persisted local Run."""

    input_mode: Optional[Literal["input_native", "multimodal", "video_fallback"]] = None
    video_path: Optional[str] = None
    cm_per_360: Optional[float] = None
    fov: Optional[float] = None


class StorageSessionItem(BaseModel):
    session_id: int
    status: str
    created_at: str
    workspace_bytes: int


class StorageResponse(BaseModel):
    total_bytes: int
    sessions: list[StorageSessionItem]


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
    analysis_type: str = "flicking"
    input_mode: str = "video_fallback"
    kovaak_run_id: Optional[int] = None


class SessionListItem(BaseModel):
    id: int
    status: str
    created_at: str
    finished_at: Optional[str] = None
    attempts: int
    max_attempts: int
    llm_cost_cny: Optional[float] = None
    summary_label: Optional[str] = None
    analysis_type: str = "flicking"
    input_mode: str = "video_fallback"
    kovaak_run_id: Optional[int] = None
    scenario: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class KovaaKRunItem(BaseModel):
    id: int
    source_key: str
    scenario: Optional[str] = None
    stats_source_ref: Optional[str] = None
    performance_source_ref: Optional[str] = None
    trace_artifact_ref: Optional[str] = None
    source_availability: dict = {}
    trace_state: str = "none"
    trace_error: Optional[str] = None
    stats_summary: Optional[dict] = None
    performance_summary: Optional[dict] = None
    created_at: str
    updated_at: str


class KovaaKRunListResponse(BaseModel):
    runs: list[KovaaKRunItem]


class HistoryTrendResponse(BaseModel):
    comparable: bool
    reason: Optional[str] = None
    metric_key: Optional[str] = None
    unit: Optional[str] = None
    metric_version: Optional[str] = None
    current: Optional[float] = None
    baseline: Optional[float] = None
    delta: Optional[float] = None
    percent_change: Optional[float] = None
    current_session_id: Optional[int] = None
    baseline_session_id: Optional[int] = None


class BenchmarkRecordCreate(BaseModel):
    provider: str
    provider_license_note: str
    catalog_version: str
    scenario_id: str
    metric_key: str
    unit: str
    value: float
    observed_at: str
    availability: Literal["available", "stale", "unavailable"] = "available"
    external_identity_ref: Optional[str] = None
    identity_consent: bool = False


class BenchmarkRecordOut(BenchmarkRecordCreate):
    id: int
    created_at: str


class BenchmarkRecordListResponse(BaseModel):
    records: list[BenchmarkRecordOut]


class DeleteSessionResponse(BaseModel):
    deleted: bool
    id: int
    files_removed: list[str]
    cleanup_failed: list[str]


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
    frame: Optional[int] = None
    time_s: Optional[float] = None
    relative_ms: Optional[float] = None
    type: str   # "kill" | "miss" | "peak" | "corrective"
    label: str
    source: Optional[str] = None


class Timeline(BaseModel):
    fps: Optional[int] = None
    duration_frames: Optional[int] = None
    events: list[TimelineEvent] = []


class CoachThreadOut(BaseModel):
    id: int
    user_id: str
    kind: str
    created_at: str
    updated_at: str


class CoachThreadMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str
    legacy_session_id: Optional[int] = None
    context: Optional[dict] = None


class CoachAnalysisRefOut(BaseModel):
    id: int
    analysis_session_id: Optional[int]
    status: str
    attached_at: str
    deleted_at: Optional[str] = None


class CoachPrimaryResponse(BaseModel):
    thread: CoachThreadOut
    messages: list[CoachThreadMessageOut]
    refs: list[CoachAnalysisRefOut]


class CoachPrimaryMessageRequest(BaseModel):
    content: str
    analysis_session_id: Optional[int] = None


class CoachPrimaryAttachRequest(BaseModel):
    analysis_session_id: int


class CoachPrimaryMessageResponse(BaseModel):
    reply: Optional[str] = None
    notes: list[str] = []
    messages: list[CoachThreadMessageOut] = []


class CoachPrimaryAttachResponse(BaseModel):
    ref: CoachAnalysisRefOut


class CoachRuntimeStatusResponse(BaseModel):
    ok: bool = True
    runtime: Literal["pi", "python"]
    sidecar: Literal["up", "down", "n/a"]
    ready_for_fast_path: bool
    message: str
