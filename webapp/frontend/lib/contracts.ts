/**
 * Single adapter: AnalysisResult v1 (wire) → CoachReport (UI view model).
 * Mirrors webapp/backend/contracts.py analysis_result_to_coach_report.
 */

import type {
  AnalysisFamilySupportState,
  AnalysisMetricV2,
  AnalysisResultV2,
  AnalysisResultV1,
  CalibrationValues,
  CoachContextRefV1,
  CoachReport,
  InputMode,
  HistoryTrend,
  KovaaKAnalysisRequest,
  KovaaKRunListItem,
  SessionListItem,
  ProductStateV1,
  TaskDetailV1,
  TaskFailureDomain,
  TaskPhase,
  TaskState,
  SessionStatus,
  StorageCategoryTotals,
  TimelineEvent,
} from "./types";

export function analysisResultToCoachReport(
  result: AnalysisResultV1,
): CoachReport {
  const narration = result.narration;
  const narrationOut: string | null =
    narration.status === "available" ? narration.text : null;

  return {
    diagnosis: result.deterministic.diagnosis,
    figures: result.deterministic.figures,
    narration: narrationOut,
    notes: [...(result.notes ?? [])],
  };
}

export type AnalysisViewState =
  | "loading"
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "retryable"
  | "deleted-unavailable"
  | "unavailable";

export function getAnalysisViewState(input: {
  loading?: boolean;
  session?: SessionStatus | null;
  errorStatus?: number | null;
}): AnalysisViewState {
  if (input.loading) return "loading";
  if (input.errorStatus === 404 || input.errorStatus === 410) return "deleted-unavailable";
  if (input.errorStatus) return "unavailable";
  const status = input.session?.status;
  if (status === "done") return "done";
  if (status === "running") return "running";
  if (status === "failed") return input.session?.error?.retryable ? "retryable" : "failed";
  if (status === "queued" || status === "uploading") return "queued";
  return "unavailable";
}

const FAMILY_LABELS: Record<string, string> = {
  static_clicking: "静态点击",
  dynamic_clicking: "动态点击",
  continuous_tracking: "连续跟枪",
  target_switching: "目标切换",
  movement_aiming: "移动瞄准",
  unknown: "未确认分类",
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function safeString(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  if (/([A-Za-z]:\\|file:\/\/|\/Users\/|\/home\/)/.test(value)) return null;
  return value.trim();
}

function safeStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const projected = safeString(item);
    return projected ? [projected] : [];
  });
}

function safeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function familyStatus(result: AnalysisResultV2): AnalysisFamilySupportState {
  const resolution = result.input_snapshot.scenario_resolution;
  const support = safeString(result.deterministic.support_status)
    ?? safeString(result.scenario?.support_status);
  if (support === "outcome_only" || resolution?.claim_ceiling === "outcome_only") {
    return "outcome-only";
  }
  if (resolution?.claim_ceiling === "descriptive_only" || support === "partial") {
    return "descriptive";
  }
  if (
    resolution?.family_analyzer_dispatch === "allowed"
    && resolution.claim_ceiling === "family_specific"
    && support === "supported"
  ) {
    return "supported";
  }
  return "unavailable";
}

export interface AnalysisMetricPresentation {
  key: string;
  value: number | string | null;
  unit: string | null;
  availability: string;
  classification: string;
  coverage: number | null;
  sources: string[];
  limitations: string[];
}

export interface AnalysisIssuePresentation {
  signal: string;
  severity: "info" | "watch" | "fix";
  priority: number;
  priorityReason: string;
  claimLevel: string | null;
  rootCauses: Array<{ level: string; text: string }>;
  prescriptions: Array<{ scenario: string; reason: string; cue: string | null }>;
  metricRefs: string[];
  eventRefs: string[];
  limitations: string[];
}

export interface AnalysisWorkspacePresentation {
  analysisId: number;
  scenario: string;
  createdAt: string;
  status: string;
  input: {
    mode: "input_native" | "multimodal" | "video_fallback";
    label: string;
    preview: boolean;
  };
  family: {
    code: string;
    label: string;
    status: AnalysisFamilySupportState;
  };
  evidence: Array<{ source: string; availability: string; alignment: string | null }>;
  limitations: string[];
  calibration: {
    cmPer360: number | null;
    fov: number | null;
    cmSource: string | null;
    fovSource: string | null;
  };
  partial: boolean;
  headline: string;
  profile: { label: string; confidence: number | null; tags: string[] } | null;
  issues: AnalysisIssuePresentation[];
  metrics: { formal: AnalysisMetricPresentation[]; limited: AnalysisMetricPresentation[] };
  timeline: TimelineEvent[];
  video: { kind: "seekable" | "native-only" | "unavailable"; reason: string | null };
}

function presentMetric(key: string, value: AnalysisMetricV2 | number): AnalysisMetricPresentation {
  if (typeof value === "number") {
    return {
      key,
      value: safeNumber(value),
      unit: null,
      availability: "available",
      classification: "legacy",
      coverage: null,
      sources: [],
      limitations: ["legacy_metric_metadata_unavailable"],
    };
  }
  return {
    key: safeString(value.key) ?? key,
    value: typeof value.value === "string" ? safeString(value.value) : safeNumber(value.value),
    unit: safeString(value.unit),
    availability: safeString(value.availability) ?? "unavailable",
    classification: safeString(value.classification) ?? "unclassified",
    coverage: safeNumber(value.coverage),
    sources: safeStrings(value.provenance?.sources),
    limitations: safeStrings(value.limitations),
  };
}

function presentIssues(value: unknown): AnalysisIssuePresentation[] {
  const issues = Array.isArray(value) ? value : [];
  return issues.flatMap((raw) => {
    const issue = record(raw);
    const signal = safeString(issue.signal);
    if (!signal) return [];
    const severity: AnalysisIssuePresentation["severity"] = issue.severity === "fix" || issue.severity === "watch"
      ? issue.severity
      : "info";
    const rootCauses = (Array.isArray(issue.root_causes) ? issue.root_causes : []).flatMap((item) => {
      const cause = record(item);
      const level = safeString(cause.level);
      const text = safeString(cause.text);
      return level && text ? [{ level, text }] : [];
    });
    const prescriptions = (Array.isArray(issue.prescriptions) ? issue.prescriptions : []).flatMap((item) => {
      const prescription = record(item);
      const scenario = safeString(prescription.scenario);
      const reason = safeString(prescription.reason) ?? safeString(prescription.purpose);
      if (!scenario || !reason) return [];
      return [{ scenario, reason, cue: safeString(prescription.cue) }];
    });
    return [{
      signal,
      severity,
      priority: safeNumber(issue.priority) ?? 999,
      priorityReason: safeString(issue.priority_reason) ?? "当前合同未提供优先级理由",
      claimLevel: safeString(issue.claim_level),
      rootCauses,
      prescriptions,
      metricRefs: safeStrings(issue.metric_refs),
      eventRefs: safeStrings(issue.event_refs),
      limitations: safeStrings(issue.limitations),
    }];
  }).sort((left, right) => left.priority - right.priority).slice(0, 3);
}

export function presentAnalysisWorkspace(session: SessionStatus): AnalysisWorkspacePresentation | null {
  const result = session.result;
  if (!result || result.schema_version !== "analysis_result.v2") return null;
  const resolution = result.input_snapshot.scenario_resolution;
  const familyCode = safeString(resolution?.aim_family) ?? "unknown";
  const diagnosis = record(result.deterministic.diagnosis);
  const profileRaw = record(diagnosis.profile);
  const profileLabel = safeString(profileRaw.label);
  const issues = presentIssues(diagnosis.issues);
  const metrics = Object.entries(result.deterministic.metrics ?? {}).map(([key, metric]) =>
    presentMetric(key, metric)
  );
  const formal = metrics.filter((metric) =>
    metric.availability === "available" && metric.classification === "deterministic"
  );
  const evidenceSources = Array.isArray(result.evidence.sources)
    ? result.evidence.sources
    : Object.values(result.evidence.sources);
  const evidence = evidenceSources.flatMap((raw) => {
    const source = safeString(raw.source);
    if (!source) return [];
    return [{
      source,
      availability: safeString(raw.availability) ?? "unavailable",
      alignment: safeString(raw.alignment),
    }];
  });
  const replay = session.history?.visual_replay;
  const videoKind = result.input_mode === "input_native"
    ? "native-only"
    : replay?.kind === "seekable_mp4"
      ? "seekable"
      : "unavailable";
  const calibration = result.input_snapshot.calibration;
  const limitations = Array.from(new Set([
    ...safeStrings(result.deterministic.limitations),
    ...safeStrings(result.scenario?.limitations),
    ...safeStrings(resolution?.limitations),
  ]));
  const partial = result.input_mode === "multimodal" && videoKind === "unavailable";
  const inputLabels = {
    input_native: "输入原生",
    multimodal: "多源模式",
    video_fallback: "视频兼容",
  } as const;
  return {
    analysisId: session.id,
    scenario: safeString(result.input_snapshot.scenario)
      ?? safeString(session.history?.scenario)
      ?? "场景信息不可用",
    createdAt: safeString(result.completed_at) ?? safeString(session.created_at) ?? "时间不可用",
    status: safeString(session.status) ?? "unavailable",
    input: {
      mode: result.input_mode,
      label: inputLabels[result.input_mode],
      preview: result.input_mode === "input_native",
    },
    family: {
      code: familyCode,
      label: FAMILY_LABELS[familyCode] ?? FAMILY_LABELS.unknown,
      status: familyStatus(result),
    },
    evidence,
    limitations,
    calibration: {
      cmPer360: safeNumber(calibration?.cm_per_360?.value),
      fov: safeNumber(calibration?.fov?.value),
      cmSource: safeString(calibration?.cm_per_360?.source),
      fovSource: safeString(calibration?.fov?.source),
    },
    partial,
    headline: issues[0]
      ? `最需要处理：${issues[0].signal}`
      : "当前证据不足以形成正式机制诊断",
    profile: profileLabel ? {
      label: profileLabel,
      confidence: safeNumber(profileRaw.confidence),
      tags: safeStrings(profileRaw.secondary_tags),
    } : null,
    issues,
    metrics: { formal, limited: metrics.filter((metric) => !formal.includes(metric)) },
    timeline: Array.isArray(result.deterministic.timeline)
      ? result.deterministic.timeline.slice(0, 500)
      : [],
    video: { kind: videoKind, reason: safeString(replay?.reason) },
  };
}

export function getProductStartRoute(
  state: ProductStateV1,
): "/onboarding" | "/analyze" | "/history" | null {
  if (state.availability !== "available") return null;
  if (state.onboarding_completed !== true) return "/onboarding";
  return state.has_runs || state.has_analyses ? "/history" : "/analyze";
}

const TASK_STATE_TEXT: Record<TaskState, string> = {
  importing: "正在导入",
  queued: "等待分析",
  running: "分析中",
  done: "已完成",
  failed: "失败",
  retrying: "正在重试",
};

const TASK_PHASE_TEXT: Record<TaskPhase, string> = {
  preparing_training_record: "准备训练记录",
  aligning_input_events: "对齐输入事件",
  computing_kinematics: "计算运动学指标",
  analyzing_video: "分析视频",
  generating_diagnostics: "生成诊断",
};

const FAILURE_DOMAIN_TEXT: Record<TaskFailureDomain, string> = {
  source_file: "源文件",
  alignment: "输入对齐",
  kinematics: "运动学计算",
  video: "视频分析",
  provider: "Provider",
  coach: "Coach",
  network: "网络",
};

export interface TaskPresentation {
  state: string;
  phase: string | null;
  failureDomain: string | null;
}

export function presentTask(task: TaskDetailV1): TaskPresentation {
  return {
    state: task.state ? TASK_STATE_TEXT[task.state] : "状态不可用",
    phase: task.phase ? TASK_PHASE_TEXT[task.phase] : null,
    failureDomain: task.failure ? FAILURE_DOMAIN_TEXT[task.failure.domain] : null,
  };
}

export interface RunModeAvailability {
  available: boolean;
  limitations: readonly string[];
}

export function getRunModeAvailability(
  run: Pick<KovaaKRunListItem, "supported_input_modes" | "limitations">,
  mode: InputMode,
): RunModeAvailability {
  return {
    available: run.supported_input_modes.includes(mode),
    limitations: run.limitations,
  };
}

function hasCalibrationValue(values: CalibrationValues | undefined): boolean {
  return Boolean(
    values &&
      (typeof values.cm_per_360 === "number" || typeof values.fov === "number"),
  );
}

export function buildRunAnalysisRequest(input: {
  inputMode: InputMode;
  profileDefault?: CalibrationValues;
  manualOverride?: CalibrationValues;
}): KovaaKAnalysisRequest {
  return {
    input_mode: input.inputMode,
    ...(hasCalibrationValue(input.profileDefault)
      ? { profile_default: input.profileDefault }
      : {}),
    ...(hasCalibrationValue(input.manualOverride)
      ? { manual_override: input.manualOverride }
      : {}),
  };
}

export interface HistorySections {
  pendingRuns: KovaaKRunListItem[];
  runRecords: KovaaKRunListItem[];
  analysisRecords: SessionListItem[];
}

export function buildHistorySections(input: {
  runs: KovaaKRunListItem[];
  sessions: SessionListItem[];
}): HistorySections {
  return {
    pendingRuns: input.runs.filter((run) => run.readiness_state === "pending_analysis"),
    runRecords: input.runs.filter((run) => run.readiness_state !== "pending_analysis"),
    analysisRecords: input.sessions,
  };
}

const HISTORY_STATUS_TEXT: Record<string, string> = {
  available: "可用",
  attached: "已关联",
  partial: "部分结果",
  source_unavailable: "来源不可用",
  unavailable: "来源不可用",
  unsupported: "不支持",
  offline: "离线",
  permission_denied: "权限被拒绝",
  deleted: "引用已删除",
  missing: "缺失",
  failed: "失败",
};

export function getHistoryStatusText(status: string | null | undefined): string {
  return HISTORY_STATUS_TEXT[status ?? ""] ?? "状态不可用";
}

export interface TrendPresentation {
  comparable: boolean;
  summary: string;
  value: number | null;
}

const TREND_REASON_TEXT: Record<string, string> = {
  scenario_mismatch: "场景不一致",
  mode_mismatch: "分析模式不一致",
  metric_mismatch: "指标不一致",
  unit_mismatch: "单位不一致",
  calibration_mismatch: "校准不一致",
  quality_insufficient: "质量不足",
  insufficient_history: "可比较记录不足",
};

export function getTrendPresentation(trend: HistoryTrend): TrendPresentation {
  if (!trend.comparable || typeof trend.current !== "number") {
    const reason = TREND_REASON_TEXT[trend.reason ?? ""] ?? "记录不满足比较条件";
    return { comparable: false, summary: `暂不可比较：${reason}`, value: null };
  }
  const unit = trend.unit ? `${trend.unit}` : "";
  const current = `${trend.current}${unit}`;
  const baseline = typeof trend.baseline === "number" ? `${trend.baseline}${unit}` : null;
  const delta = typeof trend.delta === "number"
    ? `${trend.delta >= 0 ? "+" : ""}${trend.delta}${unit}`
    : null;
  return {
    comparable: true,
    summary: `当前 ${current}${baseline ? ` · 基线 ${baseline}` : ""}${delta ? ` · 差异 ${delta}` : ""}`,
    value: trend.current,
  };
}

export interface RunInspectorPresentation {
  identity: {
    scenario: string;
    createdAt: string;
    finalization: string;
  };
  evidence: Record<string, {
    availability: string;
    coverage: number | null;
    alignment: string;
  }>;
  capabilities: {
    modes: Array<{ code: InputMode; available: boolean; reason: string | null }>;
  };
  operations: string[];
}

export function presentRunInspector(run: KovaaKRunListItem): RunInspectorPresentation {
  const evidence = run.evidence_availability;
  const alignment = typeof run.alignment.status === "string"
    ? run.alignment.status
    : run.trace_quality.alignment_status ?? "unknown";
  return {
    identity: {
      scenario: run.scenario ?? "未知场景",
      createdAt: run.created_at,
      finalization: getHistoryStatusText(run.finalization_state),
    },
    evidence: {
      stats: {
        availability: getHistoryStatusText(evidence.stats ?? run.source_availability.stats),
        coverage: null,
        alignment,
      },
      performance: {
        availability: getHistoryStatusText(evidence.performance ?? run.source_availability.performance),
        coverage: null,
        alignment,
      },
      raw: {
        availability: getHistoryStatusText(evidence.raw ?? run.trace_quality.availability),
        coverage: run.trace_quality.coverage,
        alignment,
      },
      video: {
        availability: getHistoryStatusText(evidence.mp4 ?? evidence.video ?? run.source_availability.mp4),
        coverage: null,
        alignment,
      },
    },
    capabilities: {
      modes: (["input_native", "multimodal", "video_fallback"] as InputMode[]).map((code) => ({
        code,
        available: run.supported_input_modes.includes(code),
        reason: run.supported_input_modes.includes(code) ? null : run.limitations[0] ?? "后端合同未提供该模式",
      })),
    },
    operations: [
      "start_analysis",
      "view_source",
      "manage_storage",
      ...(run.analysis_count > 0 ? ["view_analysis"] : []),
    ],
  };
}

export type CoachLayoutMode = "side-by-side" | "overlay" | "full";

export const COACH_MIN_WIDTH = 320;
export const COACH_DEFAULT_WIDTH = 360;
export const COACH_MAX_WIDTH = 480;
export const COACH_WIDTH_STEP = 16;

export function clampCoachWidth(value: number): number {
  return Math.min(COACH_MAX_WIDTH, Math.max(COACH_MIN_WIDTH, Math.round(value)));
}

export function coachLayoutMode(
  availableWidth: number,
  requestedWidth: number,
): { mode: CoachLayoutMode; width: number } {
  const width = clampCoachWidth(requestedWidth);
  if (availableWidth < 840 || availableWidth - width < 480) {
    return { mode: "full", width };
  }
  if (availableWidth < 1160) return { mode: "overlay", width };
  return { mode: "side-by-side", width };
}

export interface CoachContextPresentation {
  contextRef: string;
  kind: CoachContextRefV1["kind"];
  label: string;
  status: CoachContextRefV1["status"];
  locator: CoachContextRefV1["locator"] | null;
}

export function presentCoachContext(
  context: CoachContextRefV1,
): CoachContextPresentation {
  return {
    contextRef: context.context_ref,
    kind: context.kind,
    label: context.label,
    status: context.status,
    locator: context.locator ?? null,
  };
}

export function presentStorageCategories(
  categories: StorageCategoryTotals,
): Array<[string, number]> {
  return [
    ["分析产物", categories.analysis_artifacts_bytes],
    ["Run 录像", categories.run_video_bytes],
    ["Raw trace", categories.run_raw_bytes],
    ["未完成采集", categories.incomplete_recovery_bytes],
  ];
}
