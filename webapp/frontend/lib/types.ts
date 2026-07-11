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
  frame: number;
  time_s: number;
  type: "kill" | "miss" | "peak" | "corrective" | string;
  label: string;
}

/* ---- AnalysisResult v1 wire contract (webapp/backend/contracts.py) ---- */

export type AnalysisSchemaVersion = "analysis_result.v1";
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

export interface SessionStatus {
  id: number;
  status: SessionStatusEnum;
  /** Present when status === "done" — AnalysisResult v1 wire envelope. */
  result: AnalysisResultV1 | null;
  /** Present when status === "failed" — Error v1 wire envelope. */
  error: ErrorV1 | null;
  llm_cost_cny: number | null;
  created_at: string;
  attempts: number;
  max_attempts: number;
  worker_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** One row from GET /api/sessions (no full result payload). */
export interface SessionListItem {
  id: number;
  status: SessionStatusEnum;
  created_at: string;
  finished_at: string | null;
  attempts: number;
  max_attempts: number;
  llm_cost_cny: number | null;
  summary_label: string | null;
}

export interface SessionListResponse {
  sessions: SessionListItem[];
}

export interface DeleteSessionResponse {
  deleted: boolean;
  id: number;
  files_removed: string[];
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
  fps: number;
  duration_frames: number;
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
