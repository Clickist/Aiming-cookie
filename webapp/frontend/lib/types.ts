/**
 * TypeScript mirrors of the backend Python dataclasses.
 *
 * Field names are kept in snake_case to match the wire JSON exactly — the
 * backend serializes via dataclasses.asdict() (see webapp/backend/worker.py),
 * which preserves Python field names. Keeping parity means no client/server
 * mapping layer is needed.
 *
 * Sources mirrored:
 *   - kovaak_tracker/coach/diagnosis.py (CoachReport / CoachDiagnosis /
 *     DiagnosisIssue / ProfileMatch / RootCause)
 *   - kovaak_tracker/advice.py (Prescription, Finding — Finding is collapsed
 *     into DiagnosisIssue by the diagnosis builder)
 *   - webapp/backend/schemas.py (AnalyzeResponse, SessionStatus)
 *   - webapp/backend/contracts.py (AnalysisResult v1, Error v1)
 *   - webapp/backend/worker.py run_report (figures plotly Figure.to_dict())
 */

export type Severity = "info" | "watch" | "fix";

/** A training scenario + why it helps this finding. Mirrors advice.Prescription. */
export interface Prescription {
  scenario: string;
  reason: string;
}

/** A legacy candidate explanation layer. Mirrors diagnosis.RootCause. */
export interface RootCause {
  /** "symptom" | "physical" | "training" */
  level: string;
  text: string;
}

/** The player archetype the engine matched this session to. */
export interface ProfileMatch {
  archetype_id: string;
  label: string;
  confidence: number;
  secondary_tags: string[];
}

/** A diagnosed issue. New v2 results carry observations; legacy fields remain readable. */
export interface DiagnosisIssue {
  signal: string;
  severity: Severity;
  root_causes?: RootCause[];
  prescriptions?: Prescription[];
  priority: number;
  priority_reason: string;
  plain_language_meaning?: string;
  expected_result?: string;
  claim_level?: string;
  observation_ref?: string;
  knowledge_registry_version?: string;
  knowledge_entry_refs?: string[];
  metric_refs?: string[];
  event_refs?: string[];
  limitations?: string[];
}

/** Self-vs-reference per-metric comparison row. Mirrors advice.compare_table(). */
export interface ComparisonRow {
  metric: string;
  self: number;
  ref: number;
  delta: number;
  /** "better" | "worse" | "same" | "info" */
  verdict: string;
}

/** The structured diagnosis consumed by visualization + narrator. */
export interface CoachDiagnosis {
  profile: ProfileMatch;
  issues: DiagnosisIssue[];
  summary: Record<string, unknown>;
  comparison: ComparisonRow[] | null;
  meta: Record<string, unknown>;
}

/**
 * Plotly figure as JSON. Mirrors Python plotly.graph_objects.Figure.to_dict()
 * — shape is `{ data: [...], layout: {...}, ... }`. We type it loosely because
 * the exact trace shapes vary by chart (SPARC, decel curve, etc.).
 */
export interface PlotlyFigure {
  data: unknown[];
  layout: Record<string, unknown>;
  [key: string]: unknown;
}

/** UI view model for report/coach views — not the wire envelope (see AnalysisResultV1). */
export interface CoachReport {
  diagnosis: CoachDiagnosis;
  figures: Record<string, PlotlyFigure>;
  narration: string | null;
  notes: string[];
}

/** 时间轴上的一个事件 marker(peak/miss/kill/corrective)。 */
export interface TimelineEvent {
  frame: number | null;
  time_s: number | null;
  relative_ms: number | null;
  type: "kill" | "miss" | "peak" | "corrective" | string;
  label: string;
  source: string | null;
}

/* ---- AnalysisResult v1 wire contract (webapp/backend/contracts.py) ---- */

export type AnalysisSchemaVersion = "analysis_result.v1";
export type AnalysisSchemaVersionV2 = "analysis_result.v2";
export type AnalysisVersion =
  | "flicking_fair_summary.v1"
  | "legacy_unversioned";
export type AnalysisSummaryType = "flicking";
export type ArtifactManifestSchemaVersion = "artifact_manifest.v1";
export type ErrorSchemaVersion = "error.v1";
export type ErrorCategory =
  | "input_validation"
  | "local_cv_runtime"
  | "llm_provider"
  | "network_cloud"
  | "storage_disk"
  | "internal_unknown";
export type NarrationStatus = "available" | "unavailable" | "not_requested";
export type ArtifactStatus = "available" | "missing" | "deleted";

export interface NarrationMetadataV1 {
  status: NarrationStatus;
  text: string | null;
  provider: string | null;
  model: string | null;
  usage: unknown | null;
}

export interface ArtifactEntryV1 {
  id: string;
  kind: string;
  media_type: string;
  size_bytes: number | null;
  checksum_sha256: string | null;
  status: ArtifactStatus;
  created_at: string | null;
}

export interface ArtifactManifestV1 {
  schema_version: ArtifactManifestSchemaVersion;
  inputs: ArtifactEntryV1[];
  outputs: ArtifactEntryV1[];
}

export interface NormalizationIssueV1 {
  path: string;
  code: string;
  original: "nan" | "+infinity" | "-infinity";
}

export interface AnalysisResultInputV1 {
  cm_per_360: number | null;
  fov: number | null;
}

export interface AnalysisResultDeterministicV1 {
  diagnosis: CoachDiagnosis;
  figures: Record<string, PlotlyFigure>;
  timeline: TimelineEvent[];
}

export interface AnalysisResultV1 {
  schema_version: AnalysisSchemaVersion;
  analysis_version: AnalysisVersion;
  summary_type: AnalysisSummaryType;
  created_at: string | null;
  completed_at: string | null;
  input: AnalysisResultInputV1;
  deterministic: AnalysisResultDeterministicV1;
  narration: NarrationMetadataV1;
  artifact_manifest: ArtifactManifestV1;
  notes: string[];
  normalization_issues: NormalizationIssueV1[];
}

/** Path-free evidence view from AnalysisResult v2. Values are stable refs/statuses only. */
export interface AnalysisEvidenceSourceV2 {
  source?: string;
  role?: string;
  availability?: string;
  alignment?: string;
  warnings?: string[];
}

export interface AnalysisEvidenceV2 {
  sources: Record<string, AnalysisEvidenceSourceV2> | AnalysisEvidenceSourceV2[];
  provenance: Record<string, unknown>;
  availability: Record<string, string>;
  alignment: { status?: string; [key: string]: unknown };
  warnings: string[];
}

export interface ArtifactManifestV2 {
  schema_version: "artifact_manifest.v2";
  external_inputs: ArtifactEntryV1[];
  owned_outputs: ArtifactEntryV1[];
}

export interface AnalysisMetricV2 {
  key?: string;
  value?: number | string | null;
  unit?: string;
  metric_version?: string;
  availability?: string;
  coverage?: number;
  classification?: string;
  calibration_ref?: string | null;
  limitations?: string[];
  provenance?: { kind?: string; sources?: string[] };
}

export type AimFamily =
  | "static_clicking"
  | "dynamic_clicking"
  | "continuous_tracking"
  | "target_switching"
  | "movement_aiming"
  | "unknown";

export interface ScenarioResolutionV1 {
  schema_version: "scenario_resolution.v1";
  aim_family: AimFamily;
  claim_ceiling: "family_specific" | "descriptive_only" | "outcome_only";
  family_analyzer_dispatch: "allowed" | "none";
  scenario_profile_ref?: string | null;
  classification_source?: string;
  limitations?: string[];
}

export interface AnalysisCalibrationValue {
  value: number | null;
  source: "stats" | "manual_override" | "profile_default" | "undetermined" | string;
}

export interface AnalysisCalibrationSnapshot {
  cm_per_360?: AnalysisCalibrationValue;
  fov?: AnalysisCalibrationValue;
}

export interface AnalysisResultV2 {
  schema_version: AnalysisSchemaVersionV2;
  analysis_id: string;
  analysis_type: string;
  input_mode: InputMode;
  kovaak_run_ref: string;
  evidence: AnalysisEvidenceV2;
  deterministic: {
    status?: string;
    support_status?: string;
    diagnosis?: CoachDiagnosis;
    metrics?: Record<string, AnalysisMetricV2 | number>;
    timeline?: unknown[];
    limitations?: string[];
    visual_validation?: { status?: string; limitations?: string[]; [key: string]: unknown };
    [key: string]: unknown;
  };
  scenario?: {
    scenario_profile_ref?: string | null;
    analyzer_refs?: string[];
    support_status?: string;
    limitations?: string[];
  };
  artifact_manifest: ArtifactManifestV2;
  input_snapshot: {
    scenario?: string | null;
    scenario_identity_version?: string;
    scenario_resolution?: ScenarioResolutionV1;
    calibration?: AnalysisCalibrationSnapshot;
    sources?: Record<string, { artifact_ref?: string; availability?: string }>;
    trace?: { artifact_ref?: string; availability?: string; format_version?: number } | null;
  };
  created_at: string | null;
  completed_at: string | null;
  warnings: string[];
  errors: unknown[];
  normalization_issues: NormalizationIssueV1[];
}

export type AnalysisResult = AnalysisResultV1 | AnalysisResultV2;
export type InputMode = "input_native" | "multimodal" | "video_fallback";

export interface ErrorV1 {
  schema_version: ErrorSchemaVersion;
  category: ErrorCategory;
  code: string;
  message: string;
  retryable: boolean;
  trace_id: string | null;
  details: unknown | null;
}

/* ---- backend HTTP-layer schemas (webapp/backend/schemas.py) ---- */

export interface AnalyzeResponse {
  /** NOTE: the backend uses an integer session id, not a string. */
  session_id: number;
}

export type SessionStatusEnum = "queued" | "running" | "done" | "failed";

/** Path-free trace state shared by Run and Analysis read models. */
export interface TraceQuality {
  state: string;
  availability: string;
  alignment_status: string | null;
  coverage: number | null;
}

export interface VisualReplay {
  kind: "seekable_mp4" | "native_only" | "unavailable";
  available: boolean;
  seekable: boolean;
  endpoint: string | null;
  artifact_ref: string | null;
  reason: string | null;
}

export interface DiagnosisLocator {
  analysis_ref: string;
  section: "diagnosis";
}

export interface EvidenceReference {
  id: string;
  source: string;
  artifact_id: string | null;
  challenge_time_range_ms: number[] | null;
  alignment_status: string;
  availability: string;
  local_only: boolean;
  metric_keys: string[];
}

/** Lazy history detail returned only by GET /api/sessions/:id. */
export interface AnalysisHistoryDetail {
  analysis_ref: string;
  run_ref: string | null;
  scenario: string | null;
  presentation_label?: string | null;
  training_at?: string | null;
  analysis_completed_at?: string | null;
  input_mode: string;
  source_availability: Record<string, string>;
  trace_quality: TraceQuality;
  visual_replay: VisualReplay;
  diagnosis_locator: DiagnosisLocator;
  evidence_refs: EvidenceReference[];
}

export interface SessionStatus {
  id: number;
  status: string;
  /** Present when status === "done" — versioned, path-free result envelope. */
  result: AnalysisResult | null;
  /** Present when status === "failed" — Error v1 wire envelope. */
  error: ErrorV1 | null;
  llm_cost_cny: number | null;
  created_at: string;
  attempts: number;
  max_attempts: number;
  worker_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  analysis_type: string;
  input_mode: string;
  kovaak_run_id: number | null;
  presentation_label?: string | null;
  training_at?: string | null;
  analysis_completed_at?: string | null;
  history: AnalysisHistoryDetail | null;
}

export type AnalysisFamilySupportState =
  | "supported"
  | "descriptive"
  | "unavailable"
  | "outcome-only";

export interface EvidenceSegmentPlaybackV1 {
  schema_version: "evidence_segment_playback.v1";
  availability: "available" | "unavailable";
  video_route: string | null;
  relative_start_ms: number | null;
  relative_end_ms: number | null;
  limitations: string[];
}

export interface FrontendEvidenceSegmentV1 {
  segment_id: string;
  analysis_ref: string;
  analyzer_ref: string | null;
  segment_kind: string | null;
  start_ms: number | null;
  end_ms: number | null;
  focus_start_ms: number | null;
  focus_end_ms: number | null;
  title_key: string | null;
  rank_reason: string | null;
  issue_refs: string[];
  metric_refs: string[];
  event_refs: string[];
  available_channels: string[];
  source_coverage: number | null;
  confidence: number | null;
  limitations: string[];
  playback: EvidenceSegmentPlaybackV1;
}

export interface FrontendEvidenceSegmentsV1 {
  schema_version: "frontend_evidence_segments.v1";
  analysis_ref: string;
  video_availability: "available" | "unavailable";
  video_route: string | null;
  canonical_window_start_ms: number | null;
  segments: FrontendEvidenceSegmentV1[];
}

export interface FrontendAnalysisDataMarkerV1 {
  event_ref: string;
  kind: string;
  relative_ms: number;
}

export interface FrontendAnalysisDataDistributionV1 {
  kind: string;
  count: number;
}

export interface TargetRelativeErrorRadiusPointV1 {
  relative_ms: number;
  normalized_error_radius: number;
}

export interface TargetRelativeErrorRadiusV1 {
  availability: "available" | "unavailable";
  reason: string | null;
  points: TargetRelativeErrorRadiusPointV1[];
}

export interface FrontendAnalysisDataV1 {
  schema_version: "frontend_analysis_data.v1";
  analysis_ref: string;
  limitations: string[];
  event_markers: FrontendAnalysisDataMarkerV1[];
  event_distribution: FrontendAnalysisDataDistributionV1[];
  target_relative_error_radius: TargetRelativeErrorRadiusV1;
}

export type FrontendAnalysisFamilyRowKindV1 =
  | "switch_chain"
  | "tracking_fixed_window"
  | "tracking_loss"
  | "tracking_reacquisition"
  | "tracking_change_response"
  | "static_flick";

export interface FrontendAnalysisFamilyDataRowV1 {
  kind: FrontendAnalysisFamilyRowKindV1;
  timing: Record<string, number>;
  metrics: Record<string, number>;
  limitations: string[];
}

export interface FrontendAnalysisFamilyDataV1 {
  schema_version: "frontend_analysis_family_data.v1";
  analysis_ref: string;
  family: "switching" | "tracking" | "flicking" | "unsupported";
  availability: "available" | "unavailable";
  reason: string | null;
  limitations: string[];
  total_count: number;
  next_offset: number | null;
  rows: FrontendAnalysisFamilyDataRowV1[];
}

export interface CurrentTrainingItemV1 {
  display_name: string | null;
  scenario_profile_ref: string | null;
  scenario_availability: "available" | "unavailable";
  status: "planned" | "active" | "completed" | "cancelled";
  practice_condition: string | null;
  cue: string | null;
  dose_guardrail: string | null;
  observation: string | null;
  retest: string | null;
}

export interface CurrentTrainingV1 {
  schema_version: "current_training.v1";
  availability: "available" | "unavailable";
  reason: "no_current_plan" | null;
  plan_status: "active" | "paused" | null;
  total_item_count: number;
  visible_item_count: number;
  limitations: string[];
  items: CurrentTrainingItemV1[];
}

export type ScenarioOpenStatus =
  | "scenario_dispatched"
  | "desktop_unavailable"
  | "scenario_unmapped"
  | "deep_link_dispatch_failed";

export interface ScenarioOpenResultV1 {
  status: ScenarioOpenStatus;
  scenario_profile_ref: string | null;
  display_name: string | null;
  message: string;
}

export interface KovaaKScoreSyncRequestV1 {
  schema_version: "kovaak_benchmark_sync_request.v1";
  steam_id: string;
  identity_consent: boolean;
}

export interface KovaaKScoreSyncResultV1 {
  schema_version: "kovaak_benchmark_sync_result.v1";
  imported_score_count: number;
  difficulty_counts: Record<"easier" | "medium", number>;
  observed_at: string;
}

export interface KovaaKConnectionSaveRequestV1 {
  steam_profile: string;
  identity_consent: boolean;
}

export interface KovaaKConnectionStatusV1 {
  connected: boolean;
}

export interface KovaaKConnectionDeleteResponseV1 {
  deleted: boolean;
}

export interface KovaaKScoreStageV1 {
  stage: "easier" | "medium";
  completed: number;
  required: number;
  rank: number;
  rank_name: string;
}

export interface KovaaKScoreItemV1 {
  stage: "easier" | "medium";
  name: string;
  category: string;
  subcategory: string;
  score: number;
  item_rank: number;
  item_rank_name: string;
  completed: boolean;
}

export interface KovaaKScoresV1 {
  schema_version: "kovaak_scores.v1";
  availability: "available" | "unavailable";
  observed_at: string | null;
  stages: KovaaKScoreStageV1[];
  items: KovaaKScoreItemV1[];
}

/** One row from GET /api/sessions (no full result payload). */
export interface SessionListItem {
  id: number;
  analysis_ref: string;
  run_ref: string | null;
  status: string;
  created_at: string;
  finished_at: string | null;
  attempts: number;
  max_attempts: number;
  llm_cost_cny: number | null;
  summary_label: string | null;
  analysis_type: string;
  input_mode: string;
  kovaak_run_id: number | null;
  scenario: string | null;
  presentation_label?: string | null;
  training_at?: string | null;
  analysis_completed_at?: string | null;
  source_availability: Record<string, string>;
  trace_quality: TraceQuality;
}

export interface SessionListResponse {
  sessions: SessionListItem[];
}

export interface PublicReadModelError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface ProductStateV1 {
  schema_version: "product_state.v1";
  availability: "available" | "unavailable";
  onboarding_completed: boolean | null;
  onboarding_completion_kind: "connected" | "legacy" | null;
  has_pending_runs: boolean | null;
  has_runs: boolean | null;
  has_analyses: boolean | null;
  error: PublicReadModelError | null;
}

export interface CaptureRunAttachment {
  run_ref: string;
  raw_attached: boolean;
  video_attached: boolean;
}

export interface CaptureStatusV1 {
  schema_version: "capture_status.v1";
  availability: "available" | "unavailable";
  platform_supported: boolean | null;
  raw_input_permission: "granted" | "denied" | "not_determined";
  capture_enabled: boolean | null;
  kovaak_process_present: boolean | null;
  replay_buffer_active: boolean | null;
  runtime_health: "healthy" | "degraded" | "unavailable";
  finalization_state: string;
  pause_state: "clear" | "fail_closed" | "unknown";
  pause_fail_closed: boolean;
  runs: CaptureRunAttachment[];
  error: PublicReadModelError | null;
}

export type TaskState = "importing" | "queued" | "running" | "done" | "failed" | "retrying";
export type TaskPhase =
  | "preparing_training_record"
  | "aligning_input_events"
  | "computing_kinematics"
  | "analyzing_video"
  | "generating_diagnostics";
export type TaskFailureDomain =
  | "source_file"
  | "alignment"
  | "kinematics"
  | "video"
  | "provider"
  | "coach"
  | "network";

export interface TaskFailureV1 {
  domain: TaskFailureDomain;
  code: string;
  message: string;
  retryable: boolean;
}

export interface TaskPartialOutcomeV1 {
  status: "partial";
  native_preserved: boolean;
  visual_status: string;
  reason_code: string;
}

export interface TaskAttemptV1 {
  attempt_ref: string;
  attempt_number: number;
  state: TaskState;
  state_label: string;
  phase: TaskPhase | null;
  failure: TaskFailureV1 | null;
  partial_outcome: TaskPartialOutcomeV1 | null;
  retryable: boolean;
  can_delete: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskDetailV1 {
  schema_version: "task_detail.v1";
  availability: "available" | "unavailable";
  task_ref: string | null;
  analysis_ref?: string | null;
  state?: TaskState | null;
  state_label?: string | null;
  phase?: TaskPhase | null;
  phase_label?: string | null;
  input_mode?: InputMode | null;
  analysis_type?: string | null;
  run_ref?: string | null;
  presentation_label?: string | null;
  training_at?: string | null;
  analysis_completed_at?: string | null;
  failure?: TaskFailureV1 | null;
  partial_outcome?: TaskPartialOutcomeV1 | null;
  retryable?: boolean | null;
  can_delete?: boolean | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  attempt_number?: number | null;
  attempt_history?: TaskAttemptV1[];
  error: PublicReadModelError | null;
}

export interface TaskListV1 {
  schema_version: "task_list.v1";
  availability: "available" | "unavailable";
  tasks: TaskDetailV1[];
  error: PublicReadModelError | null;
}

/** Safe public Run list projection. Local source paths, trace bytes, and parser summaries are absent. */
export interface KovaaKRunStatsCalibration {
  fov?: number;
  dpi?: number;
  sensitivity?: number;
  cm_per_360?: number;
}

export interface KovaaKRunAlignment {
  state?: string;
  status?: string;
  coverage?: number;
  duration_ms?: number;
  error_code?: string;
}

export interface KovaaKRunVideoQuality {
  availability?: string;
  coverage?: {
    packet_count?: number;
    visible_duration_ms?: number;
  } | null;
}

export interface KovaaKRunListItem {
  id: number;
  run_ref: string;
  source_key: string | null;
  scenario: string | null;
  source_availability: Record<string, string>;
  trace_quality: TraceQuality;
  trace_state: string;
  /** Kept for diagnostics only; the UI intentionally does not render it. */
  trace_error: string | null;
  video_artifact_ref: string | null;
  finalization_state: string;
  finalization_error?: string | null;
  readiness_state: "pending_analysis" | "analyzed" | "incomplete_evidence";
  analysis_count: number;
  supported_input_modes: InputMode[];
  evidence_availability: Record<string, string>;
  alignment: KovaaKRunAlignment;
  video_quality: KovaaKRunVideoQuality;
  limitations: string[];
  stats_calibration?: KovaaKRunStatsCalibration | null;
  created_at: string;
  updated_at: string;
}

/** Safe public Run detail. Local source paths and trace bytes are never present. */
export interface KovaaKRunItem extends KovaaKRunListItem {
  stats_source_ref: string | null;
  performance_source_ref: string | null;
  trace_artifact_ref: string | null;
  stats_summary: Record<string, unknown> | null;
  performance_summary: Record<string, unknown> | null;
}

export interface KovaaKRunListResponse {
  runs: KovaaKRunListItem[];
}

export interface CalibrationValues {
  cm_per_360?: number | null;
  fov?: number | null;
}

export interface KovaaKAnalysisRequest {
  allow_parallel?: boolean;
  video_path?: string;
  /** Legacy wire fields retained for backend migration compatibility; Task 3 UI never sends them. */
  cm_per_360?: number;
  fov?: number;
  profile_default?: CalibrationValues;
  manual_override?: CalibrationValues;
}

export type ProviderAuthMode = "api_key" | "oauth" | "ambient";

export interface ProviderCatalogModel {
  model_id: string;
  model_name?: string;
  api?: string;
  provider_id?: string;
  base_url?: string;
  reasoning?: boolean;
  input?: string[];
  context_window?: number;
  max_tokens?: number;
}

export interface ProviderCatalogEntry {
  provider_id: string;
  provider_name: string;
  auth_modes: ProviderAuthMode[];
  base_url?: string | null;
  models: ProviderCatalogModel[];
}

export interface ProviderCatalogV1 {
  schema_version?: "coach_provider_catalog.v1";
  providers: ProviderCatalogEntry[];
}

export interface ProviderAuthCapability {
  provider_id: string;
  provider_name: string;
  auth_modes: ProviderAuthMode[];
}

export interface ProviderAuthCapabilitiesV1 {
  schema_version?: "coach_provider_auth_capabilities.v1";
  providers: ProviderAuthCapability[];
}

export type CustomProviderKind = "custom_openai_compatible" | "custom_anthropic_compatible";
export type CustomProviderProtocol = "openai-completions" | "anthropic-messages";
export type ProviderKind = "builtin" | CustomProviderKind;
export type ProviderProfileState =
  | "unconfigured"
  | "auth_expired"
  | "needs_reauth"
  | "ready"
  | "model_unavailable"
  | "connection_failed";

export interface ProviderProfileCreate {
  name: string;
  kind: ProviderKind;
  provider_id?: string | null;
  base_url?: string | null;
  model_id: string;
  context_window?: number | null;
  max_tokens?: number | null;
  api_key?: string | null;
  is_default?: boolean;
}

export interface ProviderProfile {
  id: number;
  name: string;
  provider_id: string;
  kind: ProviderKind;
  base_url: string | null;
  model_id: string;
  context_window?: number | null;
  max_tokens?: number | null;
  is_default: boolean;
  configured: boolean;
  credential_configured: boolean;
  has_api_key: boolean;
  status: ProviderProfileState;
  created_at: string;
  updated_at: string;
}

export interface ProviderProfileListResponse {
  profiles: ProviderProfile[];
}

export interface ProviderProfileStatus {
  profile_id: number | null;
  configured: boolean;
  status: ProviderProfileState;
  message: string;
}

export interface CustomProviderModel {
  model_id: string;
  context_window: number | null;
  max_tokens: number | null;
}

export interface CustomProviderModelListResponse {
  models: CustomProviderModel[];
}

export interface CustomProviderModelDiscoveryResponse extends CustomProviderModelListResponse {
  protocol: CustomProviderProtocol;
}

export interface CustomProviderModelListRequest {
  protocol: CustomProviderProtocol;
  base_url: string;
  api_key: string;
}

export interface ProviderAuthPrompt {
  prompt_id: string;
  type: "text" | "secret" | "select" | "manual_code";
  message: string;
  placeholder?: string;
  options?: Array<{ id: string; label: string; description?: string }>;
}

export type ProviderAuthEvent =
  | { type: "auth_url"; url: string; instructions?: string }
  | {
      type: "device_code";
      user_code: string;
      verification_uri: string;
      interval_seconds?: number;
      expires_in_seconds?: number;
    }
  | { type: "progress"; message: string };

export interface ProviderAuthOperation {
  operation_id: string;
  profile_id: number;
  action: "login" | "refresh";
  mode: "api_key" | "oauth" | null;
  status: "running" | "awaiting_input" | "succeeded" | "failed" | "cancelled" | "timed_out" | "interrupted";
  prompts: ProviderAuthPrompt[];
  events: ProviderAuthEvent[];
  error: PublicReadModelError | null;
  created_at: string;
  expires_at: string;
}

export interface DesktopCaptureCoordinatorStatus {
  enabled: boolean;
  phase: string;
  captureSessionId: string | null;
  kovaakProcessPresent: boolean;
  windowHandle: number | null;
  reason: string | null;
  raw: { state: string; reason: string | null };
  video: { state: string; reason: string | null };
}

export interface HistoryTrend {
  comparable: boolean;
  reason?: string;
  classification?: "deterministic";
  metric_key?: string;
  unit?: string;
  metric_version?: string;
  current?: number;
  baseline?: number;
  delta?: number;
  percent_change?: number;
  current_session_id?: number;
  baseline_session_id?: number;
}

export type BenchmarkAvailability = "available" | "stale" | "unavailable";

export interface BenchmarkRecordCreate {
  provider: string;
  provider_license_note: string;
  catalog_version: string;
  scenario_id: string;
  metric_key: string;
  unit: string;
  value: number;
  observed_at: string;
  availability?: BenchmarkAvailability;
}

export interface BenchmarkRecord extends BenchmarkRecordCreate {
  id: number;
  created_at: string;
  availability: BenchmarkAvailability;
}

export interface BenchmarkRecordListResponse {
  records: BenchmarkRecord[];
}

export interface DeleteSessionResponse {
  deleted: boolean;
  id: number;
  files_removed: string[];
  cleanup_failed: string[];
}

export interface StorageSessionItem {
  session_id: number;
  status: string;
  created_at: string;
  workspace_bytes: number;
}

export interface StorageResponse {
  total_bytes: number;
  categories: StorageCategoryTotals;
  sessions: StorageSessionItem[];
}

export interface StorageCategoryTotals {
  analysis_artifacts_bytes: number;
  run_video_bytes: number;
  run_raw_bytes: number;
  incomplete_recovery_bytes: number;
}

export interface RunEvidenceRemovalResponse {
  run_ref: string;
  evidence_kind: "video" | "raw";
  artifact_ref: string | null;
  availability: "unavailable";
  removal_state: "completed" | "pending_cleanup" | "already_unavailable";
  reclaimed_bytes: number;
}

export interface IncompleteCaptureItemV1 {
  schema_version: "incomplete_capture_item.v1";
  item_ref: string;
  run_ref: string;
  size_bytes: number;
  reason: "interrupted_finalization" | "unclassified_capture_artifact";
  removable: boolean;
  impact: { code: "incomplete_recovery_only"; message: string };
  created_at: string;
}

export interface IncompleteCaptureListV1 {
  schema_version: "incomplete_capture_list.v1";
  total_bytes: number;
  items: IncompleteCaptureItemV1[];
}

export interface IncompleteCaptureRemovalV1 {
  schema_version: "incomplete_capture_removal.v1";
  item_ref: string;
  removal_state: "completed" | "pending_cleanup" | "already_unavailable";
  reclaimed_bytes: number;
  impact: { code: "incomplete_recovery_only"; message: string };
}

/* ---- coach 页:chat / timeline ---- */

/** 一条 chat 消息(来自 GET /chat history 或 POST /chat reply)。 */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ChatResponse {
  reply: string | null;
  history: ChatMessage[];
  notes: string[];
}

export interface Timeline {
  fps: number | null;
  duration_frames: number | null;
  events: TimelineEvent[];
}

/* ---- persistent primary coach (GET/POST /api/coach/primary*) ---- */

export interface CoachThreadOut {
  id: number;
  user_id: string;
  kind: string;
  created_at: string;
  updated_at: string;
}

export interface CoachMessageCardV1 {
  schema_version: "coach_message_card.v1";
  kind: "metrics" | "timeline" | "evidence";
  analysis_ref: string;
  target_ref: string | null;
  time_range_ms: number[] | null;
}

export interface CoachThreadMessageOut {
  id: number;
  role: string;
  content: string;
  created_at: string;
  legacy_session_id: number | null;
  context_refs: CoachContextRefV1[];
  cards?: CoachMessageCardV1[];
}

export type CoachAnalysisRefStatus = "active" | "deleted";

export interface CoachAnalysisRefOut {
  id: number;
  analysis_session_id: number | null;
  status: CoachAnalysisRefStatus | string;
  attached_at: string;
  deleted_at: string | null;
}

export interface CoachPrimaryResponse {
  thread: CoachThreadOut;
  messages: CoachThreadMessageOut[];
  refs: CoachAnalysisRefOut[];
}

export interface CoachPrimaryMessageResponse {
  reply: string | null;
  notes: string[];
  messages: CoachThreadMessageOut[];
}

export interface CoachPrimaryAttachResponse {
  ref: CoachAnalysisRefOut;
}

export type CoachSessionKind = "primary" | "conversation";
export type CoachSessionStatus = "active" | "archived" | "deleted";

export interface CoachSessionOut {
  id: number;
  user_id: string;
  kind: CoachSessionKind;
  title: string | null;
  status: CoachSessionStatus;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
  analysis_session_ids: number[];
}

export interface CoachSessionListResponse {
  schema_version: "coach_session_list.v1";
  sessions: CoachSessionOut[];
}

export interface CoachRuntimeStatusResponse {
  ok: boolean;
  runtime: "pi" | "python";
  sidecar: "up" | "down" | "n/a";
  ready_for_fast_path: boolean;
  message: string;
}

export type CoachContextKind =
  | "analysis"
  | "issue"
  | "time_range"
  | "metric"
  | "evidence_segment"
  | "comparison";

export interface CoachContextRefV1 {
  schema_version: "coach_context_ref.v1";
  context_ref: string;
  kind: CoachContextKind;
  status: "active" | "detached" | "deleted";
  label: string;
  analysis_ref?: string;
  comparison_analysis_ref?: string | null;
  target_ref?: string | null;
  time_range_ms?: number[] | null;
  attached_at?: string;
  detached_at?: string | null;
  deleted_at?: string | null;
  locator?: { view: "diagnosis" | "video" | "data"; relative_start_ms?: number };
}

export interface CoachContextListV1 {
  schema_version: "coach_context_list.v1";
  contexts: CoachContextRefV1[];
}

export interface CoachContextMutationV1 {
  schema_version: "coach_context_mutation.v1";
  action: "attached" | "already_attached" | "detached" | "already_detached";
  context: CoachContextRefV1;
}

export interface CoachAgentRunEventV1 {
  schema_version: "coach_agent_run_event.v1";
  event_ref: string;
  sequence: number;
  type: "status" | "phase" | "tool" | "text" | "confirmation" | "guidance" | "error";
  phase: "queued" | "text_generation" | "tool_execution" | "completed";
  code: string;
  message: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface ProductReadinessDomainV1 {
  state: string;
  availability: "known" | "unavailable";
  reason_code?: string | null;
  refs: string[];
  count: number;
  truncated: boolean;
}

export interface ProductReadinessV1 {
  schema_version: "product_readiness.v1";
  domains: Record<
    "onboarding" | "provider" | "capture" | "kovaak" | "pending_runs" | "analysis" | "training_plan" | "storage",
    ProductReadinessDomainV1
  >;
  capabilities: string[];
  blocking_reasons: string[];
}

export type GuidanceIntentKind =
  | "execute_command"
  | "request_confirmation"
  | "ui_navigation"
  | "user_action_required"
  | "wait_for_state"
  | "completed"
  | "blocked";

export interface GuidanceIntentV1 {
  schema_version: "guidance_intent.v1";
  intent_id: string;
  kind: GuidanceIntentKind;
  goal: string;
  target?: { target_id: string; safe_prefill: Record<string, string> } | null;
  command_result_ref?: string | null;
  precondition?: Record<string, unknown> | null;
  completion_condition?: Record<string, unknown> | null;
  recovery?: Record<string, unknown> | null;
}

export interface CoachAgentRunV1 {
  schema_version: "coach_agent_run.v1";
  run_ref: string;
  session_id: number;
  parent_run_ref: string | null;
  attempt: number;
  status: "queued" | "running" | "succeeded" | "failed" | "stopped";
  phase: "queued" | "text_generation" | "tool_execution" | "completed";
  partial_text: string | null;
  error: {
    domain: "network" | "model" | "permission" | "tool";
    code: string;
    message: string;
    retryable: boolean;
  } | null;
  contexts: CoachContextRefV1[];
  events: CoachAgentRunEventV1[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CoachAnalysisSoftStartRequestV1 {
  schema_version: "coach_analysis_soft_start_request.v1";
  analysis_session_id: number;
}

export interface CoachConfirmationV1 {
  schema_version: "coach_confirmation.v1";
  confirmation_ref: string;
  action: string;
  target_ref: string;
  status: "pending" | "confirmed" | "rejected";
  impact: { code: string; message: string };
  audit_ref: string | null;
  audit_state?: "pending" | "completed" | null;
  execution: Record<string, unknown> | null;
  created_at: string;
  decided_at: string | null;
}

export interface CalibrationProfileV1 {
  schema_version: "calibration_profile.v1";
  configured: boolean;
  values: CalibrationValues;
  dpi: number | null;
  sensitivity: number | null;
  adoption_priority: Array<"stats" | "manual_override" | "profile_default" | "undetermined">;
  updated_at: string | null;
  deletion_state: "completed" | "already_absent" | null;
}
