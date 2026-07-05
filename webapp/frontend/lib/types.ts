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

/** The full coach report returned in SessionStatus.result. */
export interface CoachReport {
  diagnosis: CoachDiagnosis;
  figures: Record<string, PlotlyFigure>;
  narration: string | null;
  notes: string[];
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
  /** Present when status === "done". */
  result: CoachReport | null;
  /** Present when status === "failed". */
  error: string | null;
  llm_cost_cny: number | null;
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

/** 时间轴上的一个事件 marker(peak/miss/kill/corrective)。 */
export interface TimelineEvent {
  frame: number;
  time_s: number;
  type: "kill" | "miss" | "peak" | "corrective" | string;
  label: string;
}

export interface Timeline {
  fps: number;
  duration_frames: number;
  events: TimelineEvent[];
}
