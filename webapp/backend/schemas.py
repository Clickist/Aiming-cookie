from __future__ import annotations

from typing import Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


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

    allow_parallel: bool = False
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
    presentation_label: Optional[str] = None
    training_at: Optional[str] = None
    analysis_completed_at: Optional[str] = None
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
    presentation_label: Optional[str] = None
    training_at: Optional[str] = None
    analysis_completed_at: Optional[str] = None
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
    presentation_label: Optional[str] = None
    training_at: Optional[str] = None
    analysis_completed_at: Optional[str] = None
    source_availability: dict[str, str] = Field(default_factory=dict)
    trace_quality: TraceQualityOut


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class ProductStateResponse(BaseModel):
    schema_version: Literal["product_state.v1"]
    availability: Literal["available", "unavailable"]
    onboarding_completed: Optional[bool] = None
    onboarding_completion_kind: Optional[Literal["connected", "legacy"]] = None
    has_pending_runs: Optional[bool] = None
    has_runs: Optional[bool] = None
    has_analyses: Optional[bool] = None
    error: Optional[dict] = None


class OnboardingStateRequest(BaseModel):
    completed: bool = True
    completion_kind: Literal["connected", "legacy"]


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
    presentation_label: Optional[str] = None
    training_at: Optional[str] = None
    analysis_completed_at: Optional[str] = None
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
    stats_calibration: Optional[dict[str, float]] = None
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
    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_license_note: str
    catalog_version: str
    scenario_id: str
    metric_key: str
    unit: str
    value: float
    observed_at: str
    availability: Literal["available", "stale", "unavailable"] = "available"


class BenchmarkRecordOut(BenchmarkRecordCreate):
    id: int
    created_at: str


class BenchmarkRecordListResponse(BaseModel):
    records: list[BenchmarkRecordOut]


class KovaaKBenchmarkSyncRequest(BaseModel):
    schema_version: Literal["kovaak_benchmark_sync_request.v1"]
    steam_id: str = Field(repr=False)
    identity_consent: bool

    @field_validator("steam_id")
    @classmethod
    def _steam_id(cls, value: str) -> str:
        from .kovaak_benchmark_provider import normalize_steam_profile_input

        return normalize_steam_profile_input(value)

    @field_validator("identity_consent")
    @classmethod
    def _identity_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("identity consent is required for each sync")
        return value


class KovaaKBenchmarkSyncResponse(BaseModel):
    schema_version: Literal["kovaak_benchmark_sync_result.v1"]
    imported_score_count: int
    difficulty_counts: dict[Literal["easier", "medium"], int]
    observed_at: str


class KovaaKConnectionSaveRequest(BaseModel):
    steam_profile: str = Field(repr=False)
    identity_consent: bool

    @field_validator("steam_profile")
    @classmethod
    def _steam_profile(cls, value: str) -> str:
        from .kovaak_benchmark_provider import normalize_steam_profile_input

        return normalize_steam_profile_input(value)

    @field_validator("identity_consent")
    @classmethod
    def _identity_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("identity consent is required to save a KovaaK connection")
        return value


class KovaaKConnectionStatusResponse(BaseModel):
    connected: bool


class KovaaKConnectionDeleteResponse(BaseModel):
    deleted: bool


class KovaaKScoreStage(BaseModel):
    stage: Literal["easier", "medium"]
    completed: int
    required: int
    rank: int
    rank_name: str


class KovaaKScoreItem(BaseModel):
    stage: Literal["easier", "medium"]
    name: str
    category: str
    subcategory: str
    score: float
    item_rank: int
    item_rank_name: str
    completed: bool


class KovaaKScoresResponse(BaseModel):
    schema_version: Literal["kovaak_scores.v1"]
    availability: Literal["available", "unavailable"]
    observed_at: Optional[str] = None
    stages: list[KovaaKScoreStage] = Field(default_factory=list)
    items: list[KovaaKScoreItem] = Field(default_factory=list)


class DeleteSessionResponse(BaseModel):
    deleted: bool
    id: int
    files_removed: list[str]
    cleanup_failed: list[str]


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


class FrontendAnalysisDataMarker(BaseModel):
    event_ref: str
    kind: str
    relative_ms: int


class FrontendAnalysisDataDistribution(BaseModel):
    kind: str
    count: int


class TargetRelativeErrorRadiusPoint(BaseModel):
    relative_ms: int
    normalized_error_radius: float


class TargetRelativeErrorRadius(BaseModel):
    availability: Literal["available", "unavailable"]
    reason: Optional[str] = None
    points: list[TargetRelativeErrorRadiusPoint]


class FrontendAnalysisDataResponse(BaseModel):
    schema_version: Literal["frontend_analysis_data.v1"]
    analysis_ref: str
    limitations: list[str] = Field(default_factory=list)
    event_markers: list[FrontendAnalysisDataMarker]
    event_distribution: list[FrontendAnalysisDataDistribution]
    target_relative_error_radius: TargetRelativeErrorRadius


class FrontendAnalysisFamilyDataRow(BaseModel):
    kind: Literal[
        "switch_chain",
        "tracking_fixed_window",
        "tracking_loss",
        "tracking_reacquisition",
        "tracking_change_response",
        "static_flick",
    ]
    timing: dict[str, int]
    metrics: dict[str, float]
    limitations: list[str] = Field(default_factory=list)


class FrontendAnalysisFamilyDataResponse(BaseModel):
    schema_version: Literal["frontend_analysis_family_data.v1"]
    analysis_ref: str
    family: Literal["switching", "tracking", "flicking", "unsupported"]
    availability: Literal["available", "unavailable"]
    reason: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
    total_count: int = 0
    next_offset: Optional[int] = None
    rows: list[FrontendAnalysisFamilyDataRow] = Field(default_factory=list)


class CurrentTrainingItem(BaseModel):
    display_name: Optional[str] = None
    scenario_profile_ref: Optional[str] = None
    scenario_availability: Literal["available", "unavailable"]
    status: Literal["planned", "active", "completed", "cancelled"]
    practice_condition: Optional[str] = None
    cue: Optional[str] = None
    dose_guardrail: Optional[str] = None
    observation: Optional[str] = None
    retest: Optional[str] = None


class CurrentTrainingResponse(BaseModel):
    schema_version: Literal["current_training.v1"]
    availability: Literal["available", "unavailable"]
    reason: Optional[Literal["no_current_plan"]] = None
    plan_status: Optional[Literal["active", "paused"]] = None
    total_item_count: int
    visible_item_count: int
    limitations: list[str] = Field(default_factory=list)
    items: list[CurrentTrainingItem] = Field(default_factory=list)


PROVIDER_KINDS = Literal[
    "builtin",
    "custom_openai_compatible",
    "custom_anthropic_compatible",
]
CUSTOM_PROVIDER_KINDS = {"custom_openai_compatible", "custom_anthropic_compatible"}
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
    context_window: Optional[int] = Field(default=None, gt=0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
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
        if info.data.get("kind") in CUSTOM_PROVIDER_KINDS and not value:
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
        if info.data.get("kind") in CUSTOM_PROVIDER_KINDS and not value:
            raise ValueError("api_key is required for custom providers")
        return value


class ProviderProfilePatch(BaseModel):
    name: Optional[str] = None
    provider_id: Optional[str] = None
    kind: Optional[PROVIDER_KINDS] = None
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    context_window: Optional[int] = Field(default=None, gt=0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
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
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
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


class CustomProviderModelListRequest(BaseModel):
    protocol: Literal["openai-completions", "anthropic-messages"]
    base_url: str
    api_key: str = Field(repr=False)

    @field_validator("base_url")
    @classmethod
    def _custom_provider_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be a valid HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def _custom_provider_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("api_key must not be blank")
        return value


class CustomProviderModel(BaseModel):
    model_id: str
    context_window: Optional[int] = Field(default=None, gt=0)
    max_tokens: Optional[int] = Field(default=None, gt=0)


class CustomProviderModelListResponse(BaseModel):
    models: list[CustomProviderModel]


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
