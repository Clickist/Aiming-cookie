from __future__ import annotations

from typing import Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


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


class AnalyzeResponse(BaseModel):
    session_id: int


class CalibrationValues(BaseModel):
    """Path-free calibration inputs; Stats values are selected by the worker."""

    cm_per_360: Optional[float] = None
    fov: Optional[float] = None


class AnalyzePathsRequest(BaseModel):
    video_path: str
    csv_path: str
    cm_per_360: Optional[float] = None
    fov: Optional[float] = None
    profile_default: Optional[CalibrationValues] = None
    manual_override: Optional[CalibrationValues] = None


class KovaaKAnalysisRequest(BaseModel):
    """Create an Analysis from a persisted local Run."""

    input_mode: Optional[Literal["input_native", "multimodal", "video_fallback"]] = None
    video_path: Optional[str] = None
    cm_per_360: Optional[float] = None
    fov: Optional[float] = None
    profile_default: Optional[CalibrationValues] = None
    manual_override: Optional[CalibrationValues] = None


class StorageSessionItem(BaseModel):
    session_id: int
    status: str
    created_at: str
    workspace_bytes: int


class StorageCategoryTotals(BaseModel):
    analysis_artifacts_bytes: int = 0
    run_video_bytes: int = 0
    run_raw_bytes: int = 0
    incomplete_recovery_bytes: int = 0


class StorageResponse(BaseModel):
    total_bytes: int
    categories: StorageCategoryTotals
    sessions: list[StorageSessionItem]


class RunEvidenceRemovalResponse(BaseModel):
    run_ref: str
    evidence_kind: Literal["video", "raw"]
    artifact_ref: Optional[str] = None
    availability: Literal["unavailable"]
    removal_state: Literal[
        "completed", "pending_cleanup", "already_unavailable"
    ]
    reclaimed_bytes: int
    affected_modes: list[
        Literal["input_native", "multimodal", "video_fallback"]
    ]


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


class ProductStateResponse(BaseModel):
    schema_version: Literal["product_state.v1"]
    availability: Literal["available", "unavailable"]
    onboarding_completed: Optional[bool] = None
    onboarding_completion_kind: Optional[Literal["connected", "skipped", "legacy"]] = None
    has_pending_runs: Optional[bool] = None
    has_runs: Optional[bool] = None
    has_analyses: Optional[bool] = None
    error: Optional[dict] = None


class OnboardingStateRequest(BaseModel):
    completed: bool = True
    completion_kind: Literal["connected", "skipped", "legacy"]


class CaptureRunAttachment(BaseModel):
    run_ref: str
    raw_attached: bool
    video_attached: bool


class CaptureStatusResponse(BaseModel):
    schema_version: Literal["capture_status.v1"]
    availability: Literal["available", "unavailable"]
    platform_supported: Optional[bool] = None
    raw_input_permission: Literal["granted", "denied", "not_determined"]
    capture_enabled: Optional[bool] = None
    kovaak_process_present: Optional[bool] = None
    replay_buffer_active: Optional[bool] = None
    runtime_health: Literal["healthy", "degraded", "unavailable"]
    finalization_state: str
    pause_state: Literal["clear", "fail_closed", "unknown"]
    pause_fail_closed: bool
    runs: list[CaptureRunAttachment] = Field(default_factory=list)
    error: Optional[dict] = None


class TaskFailure(BaseModel):
    domain: Literal[
        "source_file", "alignment", "kinematics", "video",
        "provider", "coach", "network",
    ]
    code: str
    message: str
    retryable: bool


class TaskPartialOutcome(BaseModel):
    status: Literal["partial"]
    native_preserved: bool
    visual_status: str
    reason_code: str


class TaskAttempt(BaseModel):
    attempt_ref: str
    attempt_number: int
    state: Literal["importing", "queued", "running", "done", "failed", "retrying"]
    state_label: str
    phase: Optional[str] = None
    failure: Optional[TaskFailure] = None
    partial_outcome: Optional[TaskPartialOutcome] = None
    retryable: bool = False
    can_delete: bool
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TaskDetailResponse(BaseModel):
    schema_version: Literal["task_detail.v1"]
    availability: Literal["available", "unavailable"]
    task_ref: Optional[str] = None
    analysis_ref: Optional[str] = None
    state: Optional[str] = None
    state_label: Optional[str] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    input_mode: Optional[str] = None
    analysis_type: Optional[str] = None
    run_ref: Optional[str] = None
    failure: Optional[TaskFailure] = None
    partial_outcome: Optional[TaskPartialOutcome] = None
    retryable: Optional[bool] = None
    can_delete: Optional[bool] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt_number: Optional[int] = None
    attempt_history: list[TaskAttempt] = Field(default_factory=list)
    error: Optional[dict] = None


class TaskListResponse(BaseModel):
    schema_version: Literal["task_list.v1"]
    availability: Literal["available", "unavailable"]
    tasks: list[TaskDetailResponse] = Field(default_factory=list)
    error: Optional[dict] = None


class KovaaKRunListItem(BaseModel):
    id: int
    run_ref: str
    source_key: Optional[str] = None
    scenario: Optional[str] = None
    source_availability: dict[str, str] = Field(default_factory=dict)
    trace_quality: TraceQualityOut
    trace_state: str = "none"
    trace_error: Optional[str] = None
    video_artifact_ref: Optional[str] = None
    finalization_state: str = "discovered"
    finalization_error: Optional[str] = None
    readiness_state: Literal[
        "pending_analysis", "analyzed", "incomplete_evidence"
    ] = "incomplete_evidence"
    analysis_count: int = 0
    supported_input_modes: list[
        Literal["input_native", "multimodal", "video_fallback"]
    ] = Field(default_factory=list)
    evidence_availability: dict[str, str] = Field(default_factory=dict)
    alignment: dict = Field(default_factory=dict)
    video_quality: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
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
    context_refs: list[dict] = []


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
    result: Optional[Union[dict, list[dict]]] = None
    ui_event: Optional[dict] = None
    confirmation: Optional[dict] = None
    warning_or_error: Optional[dict] = None
    audit_ref: str


class CoachContextAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach_context_attach.v1"]
    kind: Literal[
        "analysis", "issue", "time_range", "metric", "evidence_segment", "comparison"
    ]
    analysis_ref: str
    target_ref: Optional[str] = None
    start_ms: Optional[float] = Field(default=None, ge=0)
    end_ms: Optional[float] = Field(default=None, ge=0)
    comparison_analysis_ref: Optional[str] = None


class CoachContextRefOut(BaseModel):
    schema_version: Literal["coach_context_ref.v1"]
    context_ref: str
    kind: Literal[
        "analysis", "issue", "time_range", "metric", "evidence_segment", "comparison"
    ]
    status: Literal["active", "detached", "deleted"]
    label: str
    analysis_ref: str
    comparison_analysis_ref: Optional[str] = None
    target_ref: Optional[str] = None
    time_range_ms: Optional[list[float]] = None
    attached_at: str
    detached_at: Optional[str] = None
    deleted_at: Optional[str] = None


class CoachContextListResponse(BaseModel):
    schema_version: Literal["coach_context_list.v1"] = "coach_context_list.v1"
    contexts: list[CoachContextRefOut]


class CoachContextMutationResponse(BaseModel):
    schema_version: Literal["coach_context_mutation.v1"] = "coach_context_mutation.v1"
    action: Literal["attached", "already_attached", "detached", "already_detached"]
    context: CoachContextRefOut


class CoachAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach_agent_run_request.v1"]
    content: str = Field(min_length=1, max_length=12_000)
    context_refs: Optional[list[str]] = Field(default=None, max_length=8)


class CoachAgentRunErrorOut(BaseModel):
    domain: Literal["network", "model", "permission", "tool"]
    code: str
    message: str
    retryable: bool


class CoachAgentRunEventOut(BaseModel):
    schema_version: Literal["coach_agent_run_event.v1"]
    event_ref: str
    sequence: int
    type: Literal["status", "phase", "tool", "text", "confirmation", "error"]
    phase: Literal["queued", "text_generation", "tool_execution", "completed"]
    code: str
    message: str
    payload: Optional[dict] = None
    created_at: str


class CoachAgentRunOut(BaseModel):
    schema_version: Literal["coach_agent_run.v1"]
    run_ref: str
    parent_run_ref: Optional[str] = None
    attempt: int
    status: Literal["queued", "running", "succeeded", "failed", "stopped"]
    phase: Literal["queued", "text_generation", "tool_execution", "completed"]
    partial_text: Optional[str] = None
    error: Optional[CoachAgentRunErrorOut] = None
    contexts: list[CoachContextRefOut]
    events: list[CoachAgentRunEventOut]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class CoachConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach_confirmation_request.v1"]
    action: Literal[
        "analysis_delete",
        "overwrite",
        "provider_credential_change",
        "provider_oauth_authorize",
        "provider_oauth_revoke",
        "upload_share",
        "external_purchase",
        "coach_side_effect",
        "navigation",
        "query",
    ]
    target_ref: str


class CoachConfirmationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach_confirmation_decision.v1"]
    decision: Literal["confirm", "reject"]


class CoachConfirmationImpactOut(BaseModel):
    code: str
    message: str


class CoachConfirmationOut(BaseModel):
    schema_version: Literal["coach_confirmation.v1"]
    confirmation_ref: str
    action: str
    target_ref: str
    status: Literal["pending", "confirmed", "rejected"]
    impact: CoachConfirmationImpactOut
    audit_ref: Optional[str] = None
    audit_state: Optional[Literal["pending", "completed"]] = None
    execution: Optional[CoachProductCommandResult] = None
    created_at: str
    decided_at: Optional[str] = None


class CalibrationProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["calibration_profile_update.v1"]
    cm_per_360: Optional[float] = Field(default=None, gt=0, le=1000)
    fov: Optional[float] = Field(default=None, gt=0, le=180)


class CalibrationValuesOut(BaseModel):
    cm_per_360: Optional[float] = None
    fov: Optional[float] = None


class CalibrationProfileOut(BaseModel):
    schema_version: Literal["calibration_profile.v1"]
    configured: bool
    values: CalibrationValuesOut
    dpi: Optional[float] = None
    sensitivity: Optional[float] = None
    adoption_priority: list[
        Literal["stats", "manual_override", "profile_default", "undetermined"]
    ]
    updated_at: Optional[str] = None
    deletion_state: Optional[Literal["completed", "already_absent"]] = None


class IncompleteCaptureImpactOut(BaseModel):
    code: Literal["incomplete_recovery_only"]
    message: str


class IncompleteCaptureItemOut(BaseModel):
    schema_version: Literal["incomplete_capture_item.v1"]
    item_ref: str
    run_ref: str
    size_bytes: int
    reason: Literal["interrupted_finalization", "unclassified_capture_artifact"]
    removable: bool
    impact: IncompleteCaptureImpactOut
    created_at: str


class IncompleteCaptureListResponse(BaseModel):
    schema_version: Literal["incomplete_capture_list.v1"] = "incomplete_capture_list.v1"
    total_bytes: int
    items: list[IncompleteCaptureItemOut]


class IncompleteCaptureRemovalResponse(BaseModel):
    schema_version: Literal["incomplete_capture_removal.v1"]
    item_ref: str
    removal_state: Literal["completed", "pending_cleanup", "already_unavailable"]
    reclaimed_bytes: int
    impact: IncompleteCaptureImpactOut


class TrainingPlanItemCreateRequest(BaseModel):
    plan_version: Optional[int] = None
    item_payload: dict[str, object]


class TrainingPlanExecutionCreateRequest(BaseModel):
    scenario_ref: str
    run_refs: list[str]
    planned_dose: dict[str, object]
    completed_dose: dict[str, object]
    completion_status: Literal["completed", "partial", "skipped"]
    user_feedback: str


class TrainingPlanRetestCreateRequest(BaseModel):
    kind: Literal["matched", "near_transfer"]
    expected_metric_ref: str
    expected_direction: Literal[
        "lower_better",
        "higher_better",
        "target_band",
        "descriptive_only",
        "comparison_only",
    ]
    analysis_refs: list[str]
    comparability: Literal["comparable", "not_comparable", "unavailable"]
    result: str
    limitations: list[str]


class EvidenceSegmentPlayback(BaseModel):
    schema_version: Literal["evidence_segment_playback.v1"]
    availability: Literal["available", "unavailable"]
    video_route: Optional[str] = None
    relative_start_ms: Optional[int] = None
    relative_end_ms: Optional[int] = None
    limitations: list[str] = Field(default_factory=list)


class ManagedVideoUnavailableResponse(BaseModel):
    schema_version: Literal["managed_video_unavailable.v1"]
    availability: Literal["unavailable"]
    reason: Literal[
        "input_native_has_no_visual_replay",
        "run_owned_video_unavailable",
        "managed_video_unavailable",
    ]


class FrontendEvidenceSegment(BaseModel):
    segment_id: str
    analysis_ref: str
    analyzer_ref: Optional[str] = None
    segment_kind: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    focus_start_ms: Optional[int] = None
    focus_end_ms: Optional[int] = None
    title_key: Optional[str] = None
    rank_reason: Optional[str] = None
    issue_refs: list[str] = Field(default_factory=list)
    metric_refs: list[str] = Field(default_factory=list)
    event_refs: list[str] = Field(default_factory=list)
    available_channels: list[str] = Field(default_factory=list)
    source_coverage: Optional[float] = None
    confidence: Optional[float] = None
    limitations: list[str] = Field(default_factory=list)
    playback: EvidenceSegmentPlayback


class FrontendEvidenceSegmentsResponse(BaseModel):
    schema_version: Literal["frontend_evidence_segments.v1"]
    analysis_ref: str
    video_availability: Literal["available", "unavailable"]
    video_route: Optional[str] = None
    canonical_window_start_ms: Optional[int] = None
    segments: list[FrontendEvidenceSegment]

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
