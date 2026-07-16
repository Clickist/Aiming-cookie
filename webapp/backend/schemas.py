from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationInfo, field_validator


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


class TraceQualityOut(BaseModel):
    state: str
    availability: str
    alignment_status: Optional[str] = None
    coverage: Optional[float] = None


class VisualReplayOut(BaseModel):
    kind: Literal["seekable_mp4", "native_only", "unavailable"]
    available: bool
    seekable: bool
    endpoint: Optional[str] = None
    artifact_ref: Optional[str] = None
    reason: Optional[str] = None


class DiagnosisLocatorOut(BaseModel):
    analysis_ref: str
    section: Literal["diagnosis"]


class EvidenceReferenceOut(BaseModel):
    id: str
    source: str
    artifact_id: Optional[str] = None
    challenge_time_range_ms: Optional[list[float]] = None
    alignment_status: str
    availability: str
    local_only: bool
    metric_keys: list[str] = Field(default_factory=list)


class AnalysisHistoryDetailOut(BaseModel):
    analysis_ref: str
    run_ref: Optional[str] = None
    scenario: Optional[str] = None
    input_mode: str
    source_availability: dict[str, str] = Field(default_factory=dict)
    trace_quality: TraceQualityOut
    visual_replay: VisualReplayOut
    diagnosis_locator: DiagnosisLocatorOut
    evidence_refs: list[EvidenceReferenceOut] = Field(default_factory=list)


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
    history: Optional[AnalysisHistoryDetailOut] = None


class SessionListItem(BaseModel):
    id: int
    analysis_ref: str
    run_ref: Optional[str] = None
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
    source_availability: dict[str, str] = Field(default_factory=dict)
    trace_quality: TraceQualityOut


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class KovaaKRunListItem(BaseModel):
    id: int
    run_ref: str
    source_key: Optional[str] = None
    scenario: Optional[str] = None
    source_availability: dict[str, str] = Field(default_factory=dict)
    trace_quality: TraceQualityOut
    trace_state: str = "none"
    trace_error: Optional[str] = None
    created_at: str
    updated_at: str


class KovaaKRunItem(KovaaKRunListItem):
    stats_source_ref: Optional[str] = None
    performance_source_ref: Optional[str] = None
    trace_artifact_ref: Optional[str] = None
    stats_summary: Optional[dict] = None
    performance_summary: Optional[dict] = None


class KovaaKRunListResponse(BaseModel):
    runs: list[KovaaKRunListItem]


class HistoryTrendResponse(BaseModel):
    comparable: bool
    reason: Optional[str] = None
    classification: Optional[Literal["deterministic"]] = None
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


class CoachProductCommandResult(BaseModel):
    """Safe, canonical product-command response; excludes paths, URLs and secrets."""

    schema_version: Literal["coach_product_command_result.v1"]
    command_id: str
    status: Literal["succeeded", "failed", "cancelled", "needs_confirmation", "unavailable"]
    result_ref: Optional[str] = None
    result: Optional[dict | list[dict]] = None
    ui_event: Optional[dict] = None
    confirmation: Optional[dict] = None
    warning_or_error: Optional[dict] = None
    audit_ref: str

PROVIDER_KINDS = Literal["builtin", "custom_openai_compatible"]
PROVIDER_STATUSES = Literal[
    "unconfigured",
    "auth_expired",
    "needs_reauth",
    "ready",
    "model_unavailable",
    "connection_failed",
]


class ProviderProfileCreate(BaseModel):
    name: str
    kind: PROVIDER_KINDS
    provider_id: Optional[str] = Field(default=None, validate_default=True)
    base_url: Optional[str] = Field(default=None, validate_default=True)
    model_id: str
    api_key: Optional[str] = Field(default=None, repr=False, validate_default=True)
    is_default: bool = False

    @field_validator("name", "model_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("provider_id")
    @classmethod
    def _optional_provider_id(
        cls, value: Optional[str], info: ValidationInfo,
    ) -> Optional[str]:
        if value is not None:
            value = value.strip() or None
        if info.data.get("kind") == "builtin" and not value:
            raise ValueError("provider_id is required for builtin providers")
        return value

    @field_validator("base_url")
    @classmethod
    def _http_url(
        cls, value: Optional[str], info: ValidationInfo,
    ) -> Optional[str]:
        if value is not None:
            value = value.strip() or None
        if info.data.get("kind") == "custom_openai_compatible" and not value:
            raise ValueError("base_url is required for custom providers")
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("base_url must be a valid HTTP(S) URL")
            return value.rstrip("/")
        return None

    @field_validator("api_key")
    @classmethod
    def _api_key_text(
        cls, value: Optional[str], info: ValidationInfo,
    ) -> Optional[str]:
        if value is not None:
            value = value.strip() or None
        if info.data.get("kind") == "custom_openai_compatible" and not value:
            raise ValueError("api_key is required for custom providers")
        return value


class ProviderProfilePatch(BaseModel):
    name: Optional[str] = None
    provider_id: Optional[str] = None
    kind: Optional[PROVIDER_KINDS] = None
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    api_key: Optional[str] = Field(default=None, repr=False)
    is_default: Optional[bool] = None

    @field_validator("name", "provider_id", "model_id")
    @classmethod
    def _optional_required_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def _optional_http_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("base_url must use http:// or https://")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be a valid HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def _optional_api_key_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ProviderProfileOut(BaseModel):
    id: int
    name: str
    provider_id: str
    kind: PROVIDER_KINDS
    base_url: Optional[str] = None
    model_id: str
    is_default: bool
    configured: bool
    credential_configured: bool
    has_api_key: bool
    status: PROVIDER_STATUSES
    created_at: str
    updated_at: str


class ProviderProfileListResponse(BaseModel):
    profiles: list[ProviderProfileOut]


class ProviderProfileDeleteResponse(BaseModel):
    deleted: bool
    id: int


class ProviderProfileStatusResponse(BaseModel):
    profile_id: Optional[int] = None
    configured: bool
    status: PROVIDER_STATUSES
    message: str


class ProviderApiKeyRequest(BaseModel):
    api_key: str = Field(repr=False)

    @field_validator("api_key")
    @classmethod
    def _required_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("api_key must not be blank")
        return value


class ProviderAuthorizeRequest(BaseModel):
    # device_code is a dynamic Pi event emitted during an OAuth login, not a mode.
    mode: Literal["api_key", "oauth"]


class ProviderAuthInputRequest(BaseModel):
    prompt_id: str
    value: str = Field(repr=False)

    @field_validator("prompt_id")
    @classmethod
    def _required_prompt_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt_id must not be blank")
        return value


PROVIDER_AUTH_OPERATION_STATUSES = Literal[
    "running",
    "awaiting_input",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
]


class ProviderAuthOperationOut(BaseModel):
    operation_id: str
    profile_id: int
    action: Literal["login", "refresh"]
    mode: Optional[Literal["api_key", "oauth"]] = None
    status: PROVIDER_AUTH_OPERATION_STATUSES
    prompts: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    error: Optional[dict] = None
    created_at: str
    expires_at: str


class ProviderRevokeResponse(BaseModel):
    profile_id: int
    revoked: bool
    remote_revoked: Literal[False] = False
