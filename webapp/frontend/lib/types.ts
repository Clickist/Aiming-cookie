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

/** One layer of a finding's cause. Mirrors diagnosis.RootCause. */
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

/** A diagnosed issue, enriched with root-cause layers and prescriptions. */
export interface DiagnosisIssue {
  signal: string;
  severity: Severity;
  root_causes: RootCause[];
  prescriptions: Prescription[];
  priority: number;
  priority_reason: string;
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
  value?: number;
  unit?: string;
  metric_version?: string;
  availability?: string;
  coverage?: number;
  classification?: string;
  calibration_ref?: string | null;
  limitations?: string[];
  provenance?: { kind?: string; sources?: string[] };
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
    metrics?: Record<string, AnalysisMetricV2 | number>;
    timeline?: TimelineEvent[];
    limitations?: string[];
    [key: string]: unknown;
  };
  artifact_manifest: ArtifactManifestV2;
  input_snapshot: {
    scenario?: string | null;
    scenario_identity_version?: string;
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
  result: Record<string, unknown> | null;
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
  history: AnalysisHistoryDetail | null;
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
  source_availability: Record<string, string>;
  trace_quality: TraceQuality;
}

export interface SessionListResponse {
  sessions: SessionListItem[];
}

/** Safe public Run list projection. Local source paths, trace bytes, and parser summaries are absent. */
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

export interface KovaaKAnalysisRequest {
  input_mode?: InputMode;
  video_path?: string;
  cm_per_360?: number;
  fov?: number;
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
  external_identity_ref?: string | null;
  identity_consent?: boolean;
}

export interface BenchmarkRecord extends BenchmarkRecordCreate {
  id: number;
  created_at: string;
  availability: BenchmarkAvailability;
  external_identity_ref: string | null;
  identity_consent: boolean;
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
  sessions: StorageSessionItem[];
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

export interface CoachThreadMessageOut {
  id: number;
  role: string;
  content: string;
  created_at: string;
  legacy_session_id: number | null;
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

export interface CoachRuntimeStatusResponse {
  ok: boolean;
  runtime: "pi" | "python";
  sidecar: "up" | "down" | "n/a";
  ready_for_fast_path: boolean;
  message: string;
}
