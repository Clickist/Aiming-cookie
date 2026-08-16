import type {
  AnalysisFamilySupportState,
  AnalysisMetricV2,
  AnalysisResultV2,
  CalibrationValues,
  CoachContextRefV1,
  InputMode,
  HistoryTrend,
  KovaaKAnalysisRequest,
  KovaaKRunListItem,
  SessionListItem,
  TaskDetailV1,
  TaskFailureDomain,
  TaskPhase,
  TaskState,
  SessionStatus,
  StorageCategoryTotals,
  TimelineEvent,
} from "./types";

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

const SWITCHING_PRESENTATION_TEXT: Record<string, string> = {
  "target_switching.transition_time_ms": "切换耗时",
  "target_switching.transition_distance_px": "切换距离",
  "target_switching.path_efficiency": "路径效率",
  "target_switching.settle_duration_ms": "稳定耗时",
  "switch transition slow": "切换耗时高于可比基线",
  "switch arrival error high": "到达后稳定耗时高于可比基线",
};

const DIAGNOSIS_PRESENTATION_TEXT: Record<string, string> = {
  "decel_frac high": "减速阶段偏长",
  "linearity high": "制动节奏不够均匀",
  "reverse_ratio high": "反向修正偏多",
  "submovement two-stage": "主要移动与后续修正较分离",
  sparc: "运动平滑度（SPARC）",
  decel_frac: "减速占比",
  reverse_ratio: "反向修正比例",
  submovement_overlap: "主动作与修正重叠程度",
  "reverse_ratio ↓": "反向修正比例下降",
  "decel_frac toward individually calibrated target": "减速占比向个人基准靠近",
  "submovement_overlap toward chosen technique": "动作衔接更接近所选技术方式",
  target_relative_facts_unavailable: "缺少目标位置证据，不能判断过冲、欠冲或目标误差。",
  alignment_partial: "输入与事件为部分对齐；指标可描述本局，但不应用通用好坏阈值。",
  "Exact reviewed scenario hash only; other hashes with the same display name remain unclassified.": "仅适用于已审核的精确场景；同名其他场景不在此分类中。",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于已审核的精确场景、1920x1080 分辨率和单个目标。",
  "Input-native metrics do not establish target-relative error, overshoot, or undershoot.": "缺少目标位置证据，不能判断过冲、欠冲或目标误差。",
  "减速段占比过高，在「蹭」": "速度达到峰值后，减速阶段持续得较久。",
  "输入数据能观察到减速段偏长，但不能单独证明是制动释放不果断": "证据只能说明减速阶段偏长。",
  "减速一次到位的意识": "减速尽量一次完成。",
  "练完整的加速→减速，减速果断一次到位": "练习完整的加速和减速，减速尽量一次完成。",
  "acc 90%+，逼你把单次 flick 加减速打完整": "完成单次 Flick 的加速和减速。",
  "减速段反复修正": "减速阶段出现较多反向修正。",
  "输入数据能观察到反向修正偏多，但不能单独证明制动方向不稳的身体原因": "证据只能说明反向修正偏多。",
  "单次制动 + 流体修正": "单次制动后做连续微调。",
  "转流体派：减速段即微调，别 readjust": "在减速阶段微调，减少来回修正。",
  "落点精度，减少二次修正": "练习落点控制，减少二次修正。",
  "flick→急停→独立 micro": "主要移动后出现一次相对独立的微调。",
  "输入数据能观察到 corrective 与 primary 分离，但不能单独证明其由某种身体原因造成": "证据只能说明主要移动和后续修正较分离。",
  "转流体派（overlapping submovements）": "主要移动和后续微调重叠衔接。",
  "转流体派：corrective 与 primary 重叠，减速段即微调": "让修正与主动作更连贯地衔接，在减速阶段微调。",
};

const LIMITATION_PRESENTATION_TEXT: Record<string, string> = {
  "Exact scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "仅适用于当前已审核场景、1920×1080 分辨率和单目标布局。",
  "Unknown or multi-target scenarios remain fail-closed.": "未知场景或多目标布局不生成此类结论。",
  "Unknown hashes and concurrent target layouts are not classified by this entry.": "未知场景或多目标布局不生成此类结论。",
  alignment_latency_reported_separately: "对齐延迟单独报告，不等同于跟随滞后。",
  scenario_name_is_a_candidate_not_an_identity: "场景名称只是识别候选，不构成场景身份。",
  challenge_shape_is_a_statistical_candidate_not_an_identity: "按击杀密度识别训练大类，只是统计候选，不构成场景身份。",
  scenario_override_is_a_user_confirmed_family_not_an_identity: "训练大类由用户确认，不构成场景身份。",
  scenario_family_unresolved: "未能识别训练大类；按 static clicking 基础运动学分析处理。",
  exact_manifest_gate_inactive_visual_claims_unavailable: "精确场景审核门未激活，不提供视觉测量结论。",
  exact_visual_profile_unavailable: "缺少精确视觉档案，不做目标相对测量。",
  target_relative_facts_unavailable: "缺少目标相对事实（误差、目标身份或速度）。",
  outcome_association_unavailable: "命中关联不可用。",
  scenario_prescription_unavailable: "场景专属训练处方不可用。",
  static_clicking_baseline_without_exact_visual_profile: "基础分析缺少精确视觉档案，不含目标相对结论。",
  dynamic_clicking_baseline_without_exact_visual_profile: "基础分析缺少精确视觉档案，不含目标相对结论。",
  continuous_tracking_baseline_without_exact_visual_profile: "基础分析缺少精确视觉档案，不含目标相对结论。",
  target_switching_baseline_without_exact_visual_profile: "基础分析缺少精确视觉档案，不含目标相对结论。",
};

const OBSERVATION_REF_RE = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
const KNOWLEDGE_REGISTRY_VERSION_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}\.v[1-9][0-9]*$/;
const KNOWLEDGE_ENTRY_REF_RE = /^knowledge:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+@[1-9][0-9]*$/;
const CLAIM_LEVEL_LABELS: Record<string, string> = {
  deterministic_rule: "规则化观察",
  experimental: "待验证",
  research_supported: "研究支持",
  community_consensus: "社区经验",
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

function safeStableRef(value: unknown, pattern: RegExp, maxLength: number): string | null {
  return typeof value === "string" && value.length <= maxLength && pattern.test(value)
    ? value
    : null;
}

function safeKnowledgeEntryRefs(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 8) return [];
  if (!value.every((item) => safeStableRef(item, KNOWLEDGE_ENTRY_REF_RE, 180))) return [];
  return new Set(value).size === value.length ? value : [];
}

function safeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function presentTimelineEvent(value: unknown): TimelineEvent | null {  const item = record(value);
  if (Object.keys(item).length === 0) return null;
  const type = safeString(item.type)
    ?? safeString(item.event_type)
    ?? safeString(item.payload_type)
    ?? safeString(item.kind)
    ?? "event";
  return {
    frame: safeNumber(item.frame),
    time_s: safeNumber(item.time_s),
    relative_ms: safeNumber(item.relative_ms),
    type,
    label: safeString(item.label) ?? safeString(item.id) ?? type,
    source: safeString(item.source),
  };
}

function hasChineseDisplayText(value: string): boolean {
  return /[\u3400-\u9fff]/.test(value);
}

function presentDisplayText(value: string, fallback: string): string {
  return SWITCHING_PRESENTATION_TEXT[value]
    ?? DIAGNOSIS_PRESENTATION_TEXT[value]
    ?? (hasChineseDisplayText(value) ? value : fallback);
}

function presentPriorityReason(value: string): string | null {
  const withoutClaimLevel = value.replace(/^\[experimental\]\s*/i, "");
  if (
    /^观察项排序第\s*\d+$/.test(withoutClaimLevel)
    || withoutClaimLevel === "本次优先观察项"
    || withoutClaimLevel === "本次优先处理项"
  ) {
    return null;
  }
  return presentDisplayText(
    withoutClaimLevel,
    "当前合同未提供可展示的优先级理由",
  );
}

function presentLimitation(value: string): string {
  const fireMode = /^challenge_shape_fire_mode_kills_(\d+)_button_samples_held_(\d+)_button_samples_per_kill_(inf|[0-9.]+)$/.exec(value);
  if (fireMode) {
    const perKill = fireMode[3] === "inf" ? "∞" : fireMode[3];
    return `形态判定依据（开火模式）：${fireMode[1]} 次击杀 / 按住采样 ${fireMode[2]} 点（每杀 ${perKill}）。`;
  }
  const densityFallback = /^challenge_shape_kill_density_kills_(\d+)_duration_ms_(\d+)$/.exec(value);
  if (densityFallback) {
    const seconds = Math.round(Number(densityFallback[2]) / 1000);
    return `形态判定依据（无 Raw 弱判据）：${densityFallback[1]} 次击杀 / ${seconds} 秒。`;
  }
  return LIMITATION_PRESENTATION_TEXT[value]
    ?? presentDisplayText(value, "当前合同未提供可展示的限制说明");
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
  referenceKey?: string;
  value: number | string | null;
  unit: string | null;
  availability: string;
  classification: string;
  coverage: number | null;
  sources: string[];
  limitations: string[];
  definition?: { name?: string; description?: string };
}

export interface AnalysisIssuePresentation {
  signal: string;
  severity: "info" | "watch" | "fix";
  priority: number;
  priorityReason: string | null;
  presentationKind: "registry-backed" | "legacy";
  claimLevel: string | null;
  claimLabel: string | null;
  candidateExplanation: string | null;
  expectedResult: string | null;
  observationRef: string | null;
  knowledgeRegistryVersion: string | null;
  knowledgeEntryRefs: string[];
  rootCauses: Array<{ level: string; text: string }>;
  prescriptions: Array<{ scenario: string; reason: string; cue: string | null }>;
  metricRefs: string[];
  eventRefs: string[];
  limitations: string[];
}

export interface AnalysisWorkspacePresentation {
  analysisId: number;
  scenario: string;
  recordLabel: string;
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
  profile: { label: string; description?: string; confidence: number | null; tags: string[] } | null;
  issues: AnalysisIssuePresentation[];
  metrics: {
    formal: AnalysisMetricPresentation[];
    limited: AnalysisMetricPresentation[];
    summary: AnalysisMetricPresentation[];
    summaryMode: "formal" | "descriptive" | "empty";
  };
  timeline: TimelineEvent[];
  video: { kind: "seekable" | "native-only" | "unavailable"; reason: string | null };
}

function presentMetric(key: string, value: AnalysisMetricV2 | number): AnalysisMetricPresentation {
  if (typeof value === "number") {
    return {
      key,
      referenceKey: key,
      value: safeNumber(value),
      unit: null,
      availability: "available",
      classification: "legacy",
      coverage: null,
      sources: [],
      limitations: ["历史指标缺少完整元数据"],
    };
  }
  const referenceKey = safeString(value.key) ?? key;
  const definition = value.definition;
  const displayName = definition?.name
    ? definition.name
    : referenceKey;
  return {
    key: displayName,
    referenceKey,
    value: typeof value.value === "string" ? safeString(value.value) : safeNumber(value.value),
    unit: safeString(value.unit),
    availability: safeString(value.availability) ?? "unavailable",
    classification: safeString(value.classification) ?? "unclassified",
    coverage: safeNumber(value.coverage),
    sources: safeStrings(value.provenance?.sources),
    limitations: Array.from(new Set(safeStrings(value.limitations).map(presentLimitation))),
    definition,
  };
}

function containsTargetRelativeClaim(value: string): boolean {
  return /接近落点|过冲|欠冲|是否到位|没有到位|冲过目标|没到目标|对准目标/.test(value);
}

function presentIssues(value: unknown, targetRelativeFactsUnavailable: boolean): AnalysisIssuePresentation[] {
  const issues = Array.isArray(value) ? value : [];
  return issues.flatMap((raw) => {
    const issue = record(raw);
    const signalRaw = safeString(issue.signal);
    if (!signalRaw) return [];
    const signal = presentDisplayText(signalRaw, "当前合同未提供可展示的观察");
    const severity: AnalysisIssuePresentation["severity"] = issue.severity === "fix" || issue.severity === "watch"
      ? issue.severity
      : "info";
    const rootCauses = (Array.isArray(issue.root_causes) ? issue.root_causes : []).flatMap((item) => {
      const cause = record(item);
      const level = safeString(cause.level);
      const text = safeString(cause.text);
      return level && text ? [{ level, text: presentDisplayText(text, "当前合同未提供可展示的候选说明") }] : [];
    });
    const prescriptions = (Array.isArray(issue.prescriptions) ? issue.prescriptions : []).flatMap((item) => {
      const prescription = record(item);
      const scenario = safeString(prescription.scenario);
      const reason = safeString(prescription.reason) ?? safeString(prescription.purpose);
      if (!scenario || !reason) return [];
      return [{
        scenario,
        reason: presentDisplayText(reason, "当前合同未提供可展示的训练说明"),
        cue: safeString(prescription.cue),
      }];
    });
    const observationRef = safeStableRef(issue.observation_ref, OBSERVATION_REF_RE, 160);
    const knowledgeRegistryVersion = safeStableRef(
      issue.knowledge_registry_version,
      KNOWLEDGE_REGISTRY_VERSION_RE,
      80,
    );
    const knowledgeEntryRefs = safeKnowledgeEntryRefs(issue.knowledge_entry_refs);
    const hasKnowledgePair = knowledgeRegistryVersion !== null && knowledgeEntryRefs.length > 0;
    const presentationKind: AnalysisIssuePresentation["presentationKind"] = observationRef !== null && hasKnowledgePair
      ? "registry-backed"
      : "legacy";
    const claimLevel = safeString(issue.claim_level);
    const expectedResult = safeString(issue.expected_result);
    return [{
      signal,
      severity,
      priority: safeNumber(issue.priority) ?? 999,
      priorityReason: presentPriorityReason(
        safeString(issue.priority_reason) ?? "当前合同未提供优先级理由",
      ),
      presentationKind,
      claimLevel,
      claimLabel: CLAIM_LEVEL_LABELS[claimLevel ?? ""]
        ?? (presentationKind === "registry-backed" ? "未标注" : null),
      candidateExplanation: presentationKind === "registry-backed"
        ? (() => {
          const explanation = safeString(issue.plain_language_meaning);
          return targetRelativeFactsUnavailable && explanation && containsTargetRelativeClaim(explanation)
            ? presentDisplayText(signalRaw, signal)
            : explanation;
        })()
        : null,
      expectedResult: presentationKind === "registry-backed"
        ? expectedResult && presentDisplayText(expectedResult, "当前 Analysis 未提供可展示的验证目标。")
        : null,
      observationRef,
      knowledgeRegistryVersion: hasKnowledgePair ? knowledgeRegistryVersion : null,
      knowledgeEntryRefs: hasKnowledgePair ? knowledgeEntryRefs : [],
      rootCauses,
      prescriptions,
      metricRefs: safeStrings(issue.metric_refs),
      eventRefs: safeStrings(issue.event_refs),
      limitations: Array.from(new Set(safeStrings(issue.limitations).map(presentLimitation))),
    }];
  }).sort((left, right) => left.priority - right.priority).slice(0, 3);
}

function summaryMetricReferences(
  diagnosis: Record<string, unknown>,
  issues: AnalysisIssuePresentation[],
  metrics: AnalysisMetricPresentation[],
): string[] {
  const explicit = Object.keys(record(diagnosis.summary));
  const fallback = issues.flatMap((issue) => issue.metricRefs);
  const references = explicit.length > 0
    ? explicit
    : fallback.length > 0
      ? fallback
      : metrics.map((metric) => metric.referenceKey ?? metric.key);
  return Array.from(new Set(references));
}

export function presentAnalysisWorkspace(session: SessionStatus): AnalysisWorkspacePresentation | null {
  const result = session.result;
  if (!result || result.schema_version !== "analysis_result.v2") return null;
  const resolution = result.input_snapshot.scenario_resolution;
  const familyCode = safeString(resolution?.aim_family) ?? "unknown";
  const diagnosis = record(result.deterministic.diagnosis);
  const profileRaw = record(diagnosis.profile);
  const profileLabel = safeString(profileRaw.label);
  const targetRelativeFactsUnavailable = safeStrings(result.deterministic.limitations)
    .includes("target_relative_facts_unavailable");
  const issues = presentIssues(diagnosis.issues, targetRelativeFactsUnavailable);
  const familySupport = familyStatus(result);
  const metrics = Object.entries(result.deterministic.metrics ?? {}).map(([key, metric]) =>
    presentMetric(key, metric)
  );
  const formal = metrics.filter((metric) =>
    familySupport === "supported"
    && metric.availability === "available"
    && metric.classification === "deterministic"
  );
  const eligibleSummaryMetrics = metrics.filter((metric) =>
    metric.availability === "available" && metric.classification === "deterministic"
  );
  const canDescribeDiagnosticMetrics = issues.length > 0 && familySupport !== "outcome-only";
  const diagnosticMetricReferences = familySupport === "unavailable"
    ? Array.from(new Set(issues.flatMap((issue) => issue.metricRefs)))
    : summaryMetricReferences(diagnosis, issues, eligibleSummaryMetrics);
  const requestedSummary = canDescribeDiagnosticMetrics
    ? diagnosticMetricReferences
      .flatMap((reference) => eligibleSummaryMetrics.filter((metric) => metric.referenceKey === reference))
    : [];
  const summary = requestedSummary.length > 0
    ? requestedSummary
    : canDescribeDiagnosticMetrics
      ? eligibleSummaryMetrics
      : [];
  const summaryMode: AnalysisWorkspacePresentation["metrics"]["summaryMode"] = summary.length === 0
    ? "empty"
    : familySupport === "supported"
      ? "formal"
      : "descriptive";
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
  ].map(presentLimitation)));
  const partial = result.input_mode === "multimodal" && videoKind === "unavailable";
  const inputLabels = {
    input_native: "输入原生",
    multimodal: "多源模式",
    video_fallback: "视频兼容",
  } as const;
  const rawScenario = safeString(result.input_snapshot.scenario)
    ?? safeString(session.history?.scenario);
  const recordLabel = presentRecordLabel({
    scenario: rawScenario,
    trainingAt: session.training_at ?? session.history?.training_at,
    analysisCompletedAt: session.analysis_completed_at ?? result.completed_at ?? session.finished_at,
  });
  return {
    analysisId: session.id,
    scenario: recordLabel.split(" | ")[0] ?? "未命名场景",
    recordLabel,
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
      status: familySupport,
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
      ? `本轮最值得关注：${issues[0].signal}`
      : "当前证据不足以形成明确发现",
    profile: profileLabel ? {
      label: profileLabel,
      ...(profileLabel === "两段式型" ? {
        description: "主要移动和后续修正看起来分为两段。",
      } : {}),
      confidence: safeNumber(profileRaw.confidence),
      tags: safeStrings(profileRaw.secondary_tags),
    } : null,
    issues,
    metrics: { formal, limited: metrics.filter((metric) => !formal.includes(metric)), summary, summaryMode },
    timeline: Array.isArray(result.deterministic.timeline)
      ? result.deterministic.timeline.slice(0, 500).flatMap((event) => {
        const projected = presentTimelineEvent(event);
        return projected ? [projected] : [];
      })
      : [],
    video: { kind: videoKind, reason: safeString(replay?.reason) },
  };
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
  presentationLabel: string;
}

function safePresentationScenario(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const scenario = value.trim();
  if (
    !scenario
    || scenario.length > 160
    || /^(?:run|analysis):\d+$/i.test(scenario)
    || /(?:[A-Za-z]:[\\/]|\/(?:Users|home|private|tmp|var)\/|secret|token|password)/i.test(scenario)
    || /[\u0000-\u001f]/.test(scenario)
  ) return null;
  return scenario;
}

function safePresentationTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(value)) return null;
  return Number.isNaN(new Date(value).valueOf()) ? null : value;
}

export function presentRecordLabel(input: {
  scenario: unknown;
  trainingAt: unknown;
  analysisCompletedAt: unknown;
}): string {
  const scenario = safePresentationScenario(input.scenario) ?? "未命名场景";
  const trainingAt = safePresentationTimestamp(input.trainingAt) ?? "训练时间未知";
  const analysisCompletedAt = safePresentationTimestamp(input.analysisCompletedAt) ?? "分析尚未完成";
  return `${scenario} | 训练：${trainingAt} | 分析：${analysisCompletedAt}`;
}

export function presentTask(task: TaskDetailV1): TaskPresentation {
  return {
    state: task.state ? TASK_STATE_TEXT[task.state] : "状态不可用",
    phase: task.phase ? TASK_PHASE_TEXT[task.phase] : null,
    failureDomain: task.failure ? FAILURE_DOMAIN_TEXT[task.failure.domain] : null,
    presentationLabel: presentRecordLabel({
      scenario: task.presentation_label?.split(" | ")[0],
      trainingAt: task.training_at,
      analysisCompletedAt: task.analysis_completed_at,
    }),
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

export function isRunPauseFailClosed(
  run: Pick<KovaaKRunListItem, "alignment">,
): boolean {
  return run.alignment.error_code === "pause_unsupported";
}

function hasCalibrationValue(values: CalibrationValues | undefined): boolean {
  return Boolean(
    values &&
      (typeof values.cm_per_360 === "number" || typeof values.fov === "number"),
  );
}

export function buildRunAnalysisRequest(input: {
  profileDefault?: CalibrationValues;
  manualOverride?: CalibrationValues;
}): KovaaKAnalysisRequest {
  return {
    ...(hasCalibrationValue(input.profileDefault)
      ? { profile_default: input.profileDefault }
      : {}),
    ...(hasCalibrationValue(input.manualOverride)
      ? { manual_override: input.manualOverride }
      : {}),
  };
}

/** 历史时间展示：今天/昨天/M月d日 + HH:mm。 */
export function formatHistoryDate(iso: string | null | undefined): string {
  if (!iso) return "时间未知";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const isSameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (isSameDay(date, now)) return `今天 ${time}`;
  if (isSameDay(date, yesterday)) return `昨天 ${time}`;
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}月${day}日 ${time}`;
}

/** Coach 分析话术里单条场景名的长度上限；超长截断，run_ref 始终完整保留。 */
const COACH_DRAFT_SCENARIO_MAX = 24;
/** CoachPanel pending intent 消费的 draft 上限（见 CoachPanel pendingIntentDraft）。 */
const COACH_DRAFT_MAX_LENGTH = 240;

export interface CoachAnalysisDraftRun {
  run_ref: string;
  scenario: string | null;
  created_at: string | null;
}

/** sessionStorage key：跨页面把「让 Coach 分析」话术交给 Coach 输入框（CoachPanel 消费）。 */
export const COACH_PENDING_INTENT_KEY = "aiming-cookie.ui.coach-pending-intent";

/**
 * 生成「让 Coach 分析」按钮填充进 Coach 输入框的话术。勾选 run 让 Coach 触发
 * 分析（run_ref 定位），勾选已完成分析让 Coach 直接读结果讨论（analysis ref
 * 定位）。场景名超长截断；整体超出 intent 上限时退化为「时间（ref）」形式
 * （UI 上限 5 条时退化形式不会超限）。
 */
export function buildCoachAnalysisDraft(input: {
  runs: ReadonlyArray<CoachAnalysisDraftRun>;
  analyses: ReadonlyArray<CoachAnalysisDraftRun>;
}): string {
  const { runs, analyses } = input;
  if (runs.length === 0 && analyses.length === 0) return "";
  const describe = (item: CoachAnalysisDraftRun, ref: string, withScenario: boolean): string => {
    const scenario = (item.scenario ?? "").trim();
    const name = scenario.length > COACH_DRAFT_SCENARIO_MAX
      ? `${scenario.slice(0, COACH_DRAFT_SCENARIO_MAX)}…`
      : scenario;
    const when = formatHistoryDate(item.created_at);
    return withScenario && name
      ? `${name}，${when}（${ref}）`
      : `${when}（${ref}）`;
  };
  const runRefs = runs.map((run) => run.run_ref);
  const analysisRefs = analyses.map((analysis) => analysis.run_ref);
  const parts: string[] = [];
  if (runs.length > 0) {
    const lead = runs.length === 1 ? "请分析我这局训练：" : "请分析我这几局训练：";
    parts.push(`${lead}${runs.map((run, i) => describe(run, runRefs[i], true)).join("；")}`);
  }
  if (analyses.length > 0) {
    const lead = analyses.length === 1 ? "请结合这份分析：" : "请结合这几份分析：";
    parts.push(`${lead}${analyses.map((analysis, i) => describe(analysis, analysisRefs[i], true)).join("；")}`);
  }
  const tail = "，讲讲主要问题和改进方向。";
  const draft = `${parts.join("，")}${tail}`;
  if (draft.length <= COACH_DRAFT_MAX_LENGTH) return draft;
  const degraded: string[] = [];
  if (runs.length > 0) {
    const lead = runs.length === 1 ? "请分析我这局训练：" : "请分析我这几局训练：";
    degraded.push(`${lead}${runs.map((run, i) => describe(run, runRefs[i], false)).join("；")}`);
  }
  if (analyses.length > 0) {
    const lead = analyses.length === 1 ? "请结合这份分析：" : "请结合这几份分析：";
    degraded.push(`${lead}${analyses.map((analysis, i) => describe(analysis, analysisRefs[i], false)).join("；")}`);
  }
  return `${degraded.join("，")}${tail}`;
}

/**
 * 分析完成自动开讲：Analysis 首次观察到 done 时，AppShell 用这句话为该分析
 * 创建 Coach run（analysis ref 定位，Coach 工具自行读取分析结果）。内容与
 * 「让 Coach 分析」话术同构，不承载分析结论本身。
 */
export function buildAnalysisAutoTeachContent(analysisRef: string): string {
  return `请结合这份分析：${analysisRef}，讲讲主要问题和改进方向。`;
}

/**
 * localStorage 标记：每个 Analysis 只自动开讲一次（AppShell 消费；防刷新/重进
 * 重复触发）。这不是后端幂等事实源，仅是前端去重标记。
 */
export const ANALYSIS_AUTO_TEACH_KEY = "aiming-cookie.analysis-auto-teach";
/** AnalysisWorkspace 在活体观察到 done 转换时派发的事件名。 */
export const ANALYSIS_AUTO_TEACH_EVENT = "aiming-cookie:analysis-auto-teach";

/** 读取已自动开讲的 analysis ref 集合（损坏数据按空集处理）。 */
export function readAutoTaughtAnalyses(storage: Storage | null | undefined): Set<string> {
  if (!storage) return new Set();
  try {
    const raw = JSON.parse(storage.getItem(ANALYSIS_AUTO_TEACH_KEY) ?? "[]");
    return new Set(Array.isArray(raw) ? raw.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

/** 把 analysis ref 讇为已自动开讲；写失败静默（去重尽力而为）。 */
export function markAnalysisAutoTaught(storage: Storage | null | undefined, analysisRef: string): void {
  if (!storage) return;
  const done = readAutoTaughtAnalyses(storage);
  done.add(analysisRef);
  try {
    storage.setItem(ANALYSIS_AUTO_TEACH_KEY, JSON.stringify([...done]));
  } catch {
    // 本地去重标记写失败不影响开讲本身。
  }
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
  completed: "\u5df2\u5b8c\u6210",
  finalized: "\u5df2\u5b8c\u6210",
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
  const kindLabels: Record<string, string> = {
    analysis: "分析记录",
    comparison: "对比分析",
    issue: "问题定位",
    time_range: "时间区间",
    metric: "指标",
    evidence_segment: "证据片段",
  };
  return {
    contextRef: context.context_ref,
    kind: context.kind,
    label: context.label ?? context.analysis_ref ?? kindLabels[context.kind] ?? context.context_ref,
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
