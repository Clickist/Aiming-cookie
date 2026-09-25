import { getLocale, t, type MessageKey } from "./i18n/core";
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

// i18n 批 1：下列映射表的表键是后端码/后端原句（不动），表值改为字典键，
// 查找函数在调用时经 t() 解析——zh-CN 字典值是现行文案的逐字搬运。

const FAMILY_LABEL_KEYS: Record<string, MessageKey> = {
  static_clicking: "analysis.family.staticClicking",
  dynamic_clicking: "analysis.family.dynamicClicking",
  continuous_tracking: "analysis.family.continuousTracking",
  target_switching: "analysis.family.targetSwitching",
  movement_aiming: "analysis.family.movementAiming",
  unknown: "analysis.family.unknown",
};

const SWITCHING_PRESENTATION_KEYS: Record<string, MessageKey> = {
  "target_switching.transition_time_ms": "analysis.switching.transitionTime",
  "target_switching.transition_distance_px": "analysis.switching.transitionDistance",
  "target_switching.path_efficiency": "analysis.switching.pathEfficiency",
  "target_switching.settle_duration_ms": "analysis.switching.settleDuration",
  "switch transition slow": "analysis.switching.slowBaseline",
  "switch arrival error high": "analysis.switching.arrivalBaseline",
};

const DIAGNOSIS_PRESENTATION_KEYS: Record<string, MessageKey> = {
  "decel_frac high": "analysis.diagnosis.decelLong",
  "linearity high": "analysis.diagnosis.brakingUneven",
  "reverse_ratio high": "analysis.diagnosis.reverseMany",
  "submovement two-stage": "analysis.diagnosis.twoStageSeparation",
  sparc: "analysis.diagnosis.sparc",
  decel_frac: "analysis.diagnosis.decelFrac",
  reverse_ratio: "analysis.diagnosis.reverseRatio",
  submovement_overlap: "analysis.diagnosis.submovementOverlap",
  "reverse_ratio ↓": "analysis.diagnosis.reverseRatioDown",
  "decel_frac toward individually calibrated target": "analysis.diagnosis.decelTowardTarget",
  "submovement_overlap toward chosen technique": "analysis.diagnosis.overlapTowardTechnique",
  target_relative_facts_unavailable: "analysis.diagnosis.targetRelativeUnavailable",
  alignment_partial: "analysis.diagnosis.alignmentPartial",
  "Exact reviewed scenario hash only; other hashes with the same display name remain unclassified.": "analysis.diagnosis.exactReviewedOnly",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "analysis.diagnosis.exactReviewedSingleTarget",
  "Input-native metrics do not establish target-relative error, overshoot, or undershoot.": "analysis.diagnosis.targetRelativeUnavailable",
  "减速段占比过高，在「蹭」": "analysis.diagnosis.decelTooLong",
  "输入数据能观察到减速段偏长，但不能单独证明是制动释放不果断": "analysis.diagnosis.decelLongEvidenceOnly",
  "减速一次到位的意识": "analysis.diagnosis.decelOneShot",
  "练完整的加速→减速，减速果断一次到位": "analysis.diagnosis.decelFullPractice",
  "acc 90%+，逼你把单次 flick 加减速打完整": "analysis.diagnosis.flickComplete",
  "减速段反复修正": "analysis.diagnosis.decelRepeatedFix",
  // B3 en 对照（现行产出的 root-cause/处方 reason；kovaak_tracker/coach/labels 同值）。
  "Repeated corrections in the deceleration phase": "analysis.diagnosis.decelRepeatedFix",
  "Landing precision with fewer second corrections": "analysis.diagnosis.precisionLanding",
  "输入数据能观察到反向修正偏多，但不能单独证明制动方向不稳的身体原因": "analysis.diagnosis.reverseManyEvidenceOnly",
  "单次制动 + 流体修正": "analysis.diagnosis.singleBrakeFluid",
  "转流体派：减速段即微调，别 readjust": "analysis.diagnosis.fluidStyleDecel",
  "落点精度，减少二次修正": "analysis.diagnosis.precisionLanding",
  "flick→急停→独立 micro": "analysis.diagnosis.flickStopMicro",
  "输入数据能观察到 corrective 与 primary 分离，但不能单独证明其由某种身体原因造成": "analysis.diagnosis.separationEvidenceOnly",
  "转流体派（overlapping submovements）": "analysis.diagnosis.fluidOverlap",
  "转流体派：corrective 与 primary 重叠，减速段即微调": "analysis.diagnosis.fluidCoherent",
};

const LIMITATION_PRESENTATION_KEYS: Record<string, MessageKey> = {
  "Exact scenario hash, 1920x1080 resolution and one target bot only.": "metric.limitation.exactScenarioGate",
  "Exact reviewed scenario hash, 1920x1080 resolution and one target bot only.": "metric.limitation.exactScenarioGate",
  "Unknown or multi-target scenarios remain fail-closed.": "metric.limitation.unknownMultiTargetFailClosed",
  "Unknown hashes and concurrent target layouts are not classified by this entry.": "metric.limitation.unknownMultiTargetFailClosed",
  alignment_latency_reported_separately: "metric.limitation.alignmentLatencySeparate",
  scenario_name_is_a_candidate_not_an_identity: "metric.limitation.scenarioNameCandidate",
  challenge_shape_is_a_statistical_candidate_not_an_identity: "metric.limitation.killDensityCandidate",
  scenario_override_is_a_user_confirmed_family_not_an_identity: "metric.limitation.userConfirmedFamily",
  scenario_family_unresolved: "metric.limitation.familyUnresolved",
  exact_manifest_gate_inactive_visual_claims_unavailable: "metric.limitation.manifestGateInactive",
  exact_visual_profile_unavailable: "metric.limitation.exactVisualProfileUnavailable",
  target_relative_facts_unavailable: "metric.limitation.targetRelativeFactsMissing",
  outcome_association_unavailable: "metric.limitation.outcomeAssociationUnavailable",
  scenario_prescription_unavailable: "metric.limitation.scenarioPrescriptionUnavailable",
  static_clicking_baseline_without_exact_visual_profile: "metric.limitation.baselineWithoutExactProfile",
  dynamic_clicking_baseline_without_exact_visual_profile: "metric.limitation.baselineWithoutExactProfile",
  continuous_tracking_baseline_without_exact_visual_profile: "metric.limitation.baselineWithoutExactProfile",
  target_switching_baseline_without_exact_visual_profile: "metric.limitation.baselineWithoutExactProfile",
};

const OBSERVATION_REF_RE = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
const KNOWLEDGE_REGISTRY_VERSION_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}\.v[1-9][0-9]*$/;
const KNOWLEDGE_ENTRY_REF_RE = /^knowledge:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+@[1-9][0-9]*$/;
const CLAIM_LEVEL_KEYS: Record<string, MessageKey> = {
  deterministic_rule: "analysis.claim.deterministicRule",
  experimental: "analysis.claim.experimental",
  research_supported: "analysis.claim.researchSupported",
  community_consensus: "analysis.claim.communityConsensus",
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

/**
 * 后端原句 → 展示文案：命中映射表走字典；后端已是中文原样透出；否则回落
 * fallback——通常是字典键，也可以是调用方已翻译好的展示串（查表未命中时
 * translate 原样返回，见 i18n/core.ts 的缺 key 语义）。
 */
function presentDisplayText(value: string, fallback: MessageKey | string): string {
  const key = SWITCHING_PRESENTATION_KEYS[value] ?? DIAGNOSIS_PRESENTATION_KEYS[value];
  if (key !== undefined) return t(key);
  // B3：zh 数据（存量/中文语料）原样透出；en locale 下后端已是英文目录文案，
  // 同样原样透出。zh UI 读到未入表的英文串仍走 fallback（现行行为不变）。
  if (hasChineseDisplayText(value) || getLocale() === "en-US") return value;
  return t(fallback as MessageKey);
}

// B3：后端 en 目录的 priority_reason 样板串（kovaak_tracker/coach/labels/en.py
// 同值维护）；zh 串/正则兜住存量中文结果。
const PRIORITY_REASON_BOILERPLATE = new Set([
  "本次优先观察项",
  "本次优先处理项",
  "Priority watch item for this run",
  "Priority fix item for this run",
]);

function presentPriorityReason(value: string): string | null {
  const withoutClaimLevel = value.replace(/^\[experimental\]\s*/i, "");
  if (
    /^观察项排序第\s*\d+$/.test(withoutClaimLevel)
    || PRIORITY_REASON_BOILERPLATE.has(withoutClaimLevel)
  ) {
    return null;
  }
  return presentDisplayText(
    withoutClaimLevel,
    "analysis.fallback.priorityReasonDisplay",
  );
}

function presentLimitation(value: string): string {
  const fireMode = /^challenge_shape_fire_mode_kills_(\d+)_button_samples_held_(\d+)_button_samples_per_kill_(inf|[0-9.]+)$/.exec(value);
  if (fireMode) {
    const perKill = fireMode[3] === "inf" ? "∞" : fireMode[3];
    return t("metric.limitation.fireModeBasis", { kills: fireMode[1], samples: fireMode[2], perKill });
  }
  const densityFallback = /^challenge_shape_kill_density_kills_(\d+)_duration_ms_(\d+)$/.exec(value);
  if (densityFallback) {
    const seconds = Math.round(Number(densityFallback[2]) / 1000);
    return t("metric.limitation.densityBasis", { kills: densityFallback[1], seconds });
  }
  const key = LIMITATION_PRESENTATION_KEYS[value];
  if (key !== undefined) return t(key);
  return presentDisplayText(value, "analysis.fallback.limitation");
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
      limitations: [t("metric.limitation.legacyNoMetadata")],
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
  // B3：en 目录文案按英文关键词命中（overshoot/undershoot/landing…）；
  // zh 分支兜住存量中文结果（labels/en.py 的 ROOT_CAUSES 措辞对齐）。
  return /接近落点|过冲|欠冲|是否到位|没有到位|冲过目标|没到目标|对准目标|overshoot|undershoot|landing|arrive on target|reach the target/.test(value);
}

function presentIssues(value: unknown, targetRelativeFactsUnavailable: boolean): AnalysisIssuePresentation[] {
  const issues = Array.isArray(value) ? value : [];
  return issues.flatMap((raw) => {
    const issue = record(raw);
    const signalRaw = safeString(issue.signal);
    if (!signalRaw) return [];
    const signal = presentDisplayText(signalRaw, "analysis.fallback.observation");
    const severity: AnalysisIssuePresentation["severity"] = issue.severity === "fix" || issue.severity === "watch"
      ? issue.severity
      : "info";
    const rootCauses = (Array.isArray(issue.root_causes) ? issue.root_causes : []).flatMap((item) => {
      const cause = record(item);
      const level = safeString(cause.level);
      const text = safeString(cause.text);
      return level && text ? [{ level, text: presentDisplayText(text, "analysis.fallback.candidateExplanation") }] : [];
    });
    const prescriptions = (Array.isArray(issue.prescriptions) ? issue.prescriptions : []).flatMap((item) => {
      const prescription = record(item);
      const scenario = safeString(prescription.scenario);
      const reason = safeString(prescription.reason) ?? safeString(prescription.purpose);
      if (!scenario || !reason) return [];
      return [{
        scenario,
        reason: presentDisplayText(reason, "analysis.fallback.prescription"),
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
        safeString(issue.priority_reason) ?? t("analysis.fallback.priorityReasonRaw"),
      ),
      presentationKind,
      claimLevel,
      claimLabel: (() => {
        const claimKey = CLAIM_LEVEL_KEYS[claimLevel ?? ""];
        return claimKey !== undefined
          ? t(claimKey)
          : (presentationKind === "registry-backed" ? t("analysis.claim.unlabeled") : null);
      })(),
      candidateExplanation: presentationKind === "registry-backed"
        ? (() => {
          const explanation = safeString(issue.plain_language_meaning);
          return targetRelativeFactsUnavailable && explanation && containsTargetRelativeClaim(explanation)
            ? presentDisplayText(signalRaw, signal)
            : explanation;
        })()
        : null,
      expectedResult: presentationKind === "registry-backed"
        ? expectedResult && presentDisplayText(expectedResult, "analysis.fallback.expectedResult")
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
  const inputLabelKeys: Record<string, MessageKey> = {
    input_native: "analysis.input.native",
    multimodal: "analysis.input.multimodal",
    video_fallback: "analysis.input.videoFallback",
  };
  const rawScenario = safeString(result.input_snapshot.scenario)
    ?? safeString(session.history?.scenario);
  const recordLabel = presentRecordLabel({
    scenario: rawScenario,
    trainingAt: session.training_at ?? session.history?.training_at,
    analysisCompletedAt: session.analysis_completed_at ?? result.completed_at ?? session.finished_at,
  });
  return {
    analysisId: session.id,
    scenario: recordLabel.split(" | ")[0] ?? t("analysis.scenario.unnamed"),
    recordLabel,
    createdAt: safeString(result.completed_at) ?? safeString(session.created_at) ?? t("analysis.record.timeUnavailable"),
    status: safeString(session.status) ?? "unavailable",
    input: {
      mode: result.input_mode,
      label: t(inputLabelKeys[result.input_mode] ?? "analysis.input.videoFallback"),
      preview: result.input_mode === "input_native",
    },
    family: {
      code: familyCode,
      label: t(FAMILY_LABEL_KEYS[familyCode] ?? FAMILY_LABEL_KEYS.unknown),
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
      ? t("analysis.headline.topIssue", { signal: issues[0].signal })
      : t("analysis.headline.none"),
    profile: profileLabel ? {
      label: profileLabel,
      // B3：优先稳定 archetype_id（新结果两种语言都带）；zh label 等值兜住
      // 存量中文结果（contracts.ts:547 的历史耦合）。
      ...(safeString(profileRaw.archetype_id) === "two_stage"
        || profileLabel === "两段式型" ? {
        description: t("analysis.profile.twoStageDescription"),
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

const TASK_STATE_KEYS: Record<TaskState, MessageKey> = {
  importing: "history.taskState.importing",
  queued: "history.taskState.queued",
  running: "history.taskState.running",
  done: "history.taskState.done",
  failed: "history.taskState.failed",
  retrying: "history.taskState.retrying",
};

const TASK_PHASE_KEYS: Record<TaskPhase, MessageKey> = {
  preparing_training_record: "history.taskPhase.preparing",
  aligning_input_events: "history.taskPhase.aligning",
  computing_kinematics: "history.taskPhase.kinematics",
  analyzing_video: "history.taskPhase.video",
  generating_diagnostics: "history.taskPhase.diagnostics",
};

const FAILURE_DOMAIN_KEYS: Record<TaskFailureDomain, MessageKey> = {
  source_file: "history.failureDomain.sourceFile",
  alignment: "history.failureDomain.alignment",
  kinematics: "history.failureDomain.kinematics",
  video: "history.failureDomain.video",
  provider: "history.failureDomain.provider",
  coach: "history.failureDomain.coach",
  network: "history.failureDomain.network",
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
  trainingAt?: unknown;
  analysisCompletedAt?: unknown;
  /** History 行标题瘦身后只要场景名：时间由副行承载，标题不再裸露 ISO
      时间戳。默认 false 保持旧行为（Coach 话术、任务卡等调用方不受影响）。 */
  titleOnly?: boolean;
}): string {
  const scenario = safePresentationScenario(input.scenario) ?? t("analysis.scenario.unnamed");
  if (input.titleOnly) return scenario;
  const trainingAt = safePresentationTimestamp(input.trainingAt) ?? t("analysis.record.trainingUnknown");
  const analysisText = safePresentationTimestamp(input.analysisCompletedAt) ?? t("analysis.record.analysisPending");
  return t("analysis.record.label", { scenario, trainingAt, analysisAt: analysisText });
}

export function presentTask(task: TaskDetailV1): TaskPresentation {
  return {
    state: task.state ? t(TASK_STATE_KEYS[task.state]) : t("history.status.unavailableFallback"),
    phase: task.phase ? t(TASK_PHASE_KEYS[task.phase]) : null,
    failureDomain: task.failure ? t(FAILURE_DOMAIN_KEYS[task.failure.domain]) : null,
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
  if (!iso) return t("history.time.unknown");
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const isSameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  // 时间格式跟随当前 locale（zh-CN 14:30 风格，en-US 走本地惯例）。
  const timeLocale = getLocale() === "en-US" ? "en-US" : "zh-CN";
  const time = date.toLocaleTimeString(timeLocale, { hour: "2-digit", minute: "2-digit" });
  if (isSameDay(date, now)) return t("history.time.today", { time });
  if (isSameDay(date, yesterday)) return t("history.time.yesterday", { time });
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return t("history.time.monthDay", { month, day, time });
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
      ? t("coach.draft.itemWithScenario", { name, when, ref })
      : t("coach.draft.itemTimeOnly", { when, ref });
  };
  const itemSep = t("coach.draft.itemSeparator");
  const partSep = t("coach.draft.partSeparator");
  const tail = t("coach.draft.tail");
  const runRefs = runs.map((run) => run.run_ref);
  const analysisRefs = analyses.map((analysis) => analysis.run_ref);
  const parts: string[] = [];
  if (runs.length > 0) {
    const lead = t(runs.length === 1 ? "coach.draft.analyzeOne" : "coach.draft.analyzeMany");
    parts.push(`${lead}${runs.map((run, i) => describe(run, runRefs[i], true)).join(itemSep)}`);
  }
  if (analyses.length > 0) {
    const lead = t(analyses.length === 1 ? "coach.draft.referenceOne" : "coach.draft.referenceMany");
    parts.push(`${lead}${analyses.map((analysis, i) => describe(analysis, analysisRefs[i], true)).join(itemSep)}`);
  }
  const draft = `${parts.join(partSep)}${tail}`;
  if (draft.length <= COACH_DRAFT_MAX_LENGTH) return draft;
  const degraded: string[] = [];
  if (runs.length > 0) {
    const lead = t(runs.length === 1 ? "coach.draft.analyzeOne" : "coach.draft.analyzeMany");
    degraded.push(`${lead}${runs.map((run, i) => describe(run, runRefs[i], false)).join(itemSep)}`);
  }
  if (analyses.length > 0) {
    const lead = t(analyses.length === 1 ? "coach.draft.referenceOne" : "coach.draft.referenceMany");
    degraded.push(`${lead}${analyses.map((analysis, i) => describe(analysis, analysisRefs[i], false)).join(itemSep)}`);
  }
  return `${degraded.join(partSep)}${tail}`;
}

/**
 * 分析完成自动开讲：Analysis 首次观察到 done 时，AppShell 用这句话为该分析
 * 创建 Coach run（analysis ref 定位，Coach 工具自行读取分析结果）。内容与
 * 「让 Coach 分析」话术同构，不承载分析结论本身。
 */
export function buildAnalysisAutoTeachContent(analysisRef: string): string {
  return t("coach.draft.autoTeach", { ref: analysisRef });
}

/**
 * localStorage 标记：每个 Analysis 只自动开讲一次（AppShell 消费；防刷新/重进
 * 重复触发）。这不是后端幂等事实源，仅是前端去重标记。
 */
export const ANALYSIS_AUTO_TEACH_KEY = "aiming-cookie.analysis-auto-teach";
/** AnalysisWorkspace 在活体观察到 done 转换时派发的事件名。 */
export const ANALYSIS_AUTO_TEACH_EVENT = "aiming-cookie:analysis-auto-teach";
/** Coach 会话变更事件：CoachPanel 派发，AppShell 监听后刷新会话列表。 */
export const COACH_SESSION_UPDATED_EVENT = "aiming-cookie:coach-session-updated";
/**
 * 试用闸「回复落地」事件（AC 验证闸）：教练回复成功落地（run succeeded 收敛）
 * 时 CoachPanel 派发，AppShell 监听后上报 question_answered（过闸/去重/补报
 * 见 lib/trial；流中断与失败回合不派发）。detail 携带 `{ run_ref }` 作去重键。
 */
export const TRIAL_QUESTION_ANSWERED_EVENT = "aiming-cookie:trial-question-answered";

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

/** localStorage 键：应用重启后自动打开上次最后在看的 Coach 会话。 */
export const LAST_COACH_SESSION_KEY = "aiming-cookie.last-coach-session";

/** 读取上次最后在看的会话 id；损坏/非法数据或存储不可用返回 null。 */
export function readLastCoachSessionId(storage: Storage | null | undefined): number | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(LAST_COACH_SESSION_KEY);
    const id = raw ? Number(raw) : NaN;
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    // 存储被禁用（隐私模式等）时静默放弃恢复，与 write 的防御对称。
    return null;
  }
}

/** 记录当前正在看的会话 id；写失败静默（尽力而为）。 */
export function writeLastCoachSessionId(storage: Storage | null | undefined, sessionId: number): void {
  if (!storage) return;
  try {
    storage.setItem(LAST_COACH_SESSION_KEY, String(sessionId));
  } catch {
    // 本地偏好写失败不影响当前会话。
  }
}

/**
 * 分析类步骤的预估耗时：本机历史已完成分析的实际执行时长中位数
 * （finished_at - started_at；旧会话无 started_at 时退回 created_at），
 * 向上取整到 5 秒档，无样本或全是排队失真样本时返回 null（不编造数字）。
 *
 * 点点 09-08 拍板修正：分析耗时随 analysis_type 差一个量级（点击类 ~12s、
 * 跟枪类 ~49s、甩枪类 ~133s），全局中位数对长尾类型严重失真——
 * 传入 currentAnalysisType 时优先取同类型样本的中位数（≥3 条才分桶，
 * 不足回退全局）。样本另有 14 天新鲜度门槛：管线切换/长期不用后，
 * 旧样本不再支撑预估，宁可显示空也不显示误导数字。
 */
const ANALYSIS_ETA_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
const ANALYSIS_ETA_TYPE_BUCKET_MIN = 3;

export function computeAnalysisEtaSeconds(
  sessions: ReadonlyArray<
    Pick<SessionListItem, "status" | "created_at" | "started_at" | "finished_at" | "analysis_type">
  >,
  opts: { currentAnalysisType?: string | null } = {},
): number | null {
  const minFinishedMs = Date.now() - ANALYSIS_ETA_MAX_AGE_MS;
  const durations: Array<{ seconds: number; type: string | null }> = [];
  for (const item of sessions) {
    if (item.status !== "done" || !item.finished_at) continue;
    const startAt = item.started_at ?? item.created_at;
    if (!startAt) continue;
    const finishedMs = Date.parse(item.finished_at);
    if (!Number.isFinite(finishedMs) || finishedMs < minFinishedMs) continue;
    const seconds = (finishedMs - Date.parse(startAt)) / 1000;
    // 排队时间失真样本（无 started_at 的旧会话跨重启排队）与异常值过滤：
    // 真实分析时长远小于 1 小时。
    if (Number.isFinite(seconds) && seconds > 0 && seconds <= 3600) {
      durations.push({ seconds, type: item.analysis_type ?? null });
    }
  }
  if (durations.length === 0) return null;

  const medianOf = (list: number[]): number => {
    const sorted = [...list].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
  };

  // 同类型样本足够时按类型分桶（甩枪局不该用点击局的中位数预估）。
  const currentType = opts.currentAnalysisType ?? null;
  if (currentType) {
    const sameType = durations.filter((entry) => entry.type === currentType);
    if (sameType.length >= ANALYSIS_ETA_TYPE_BUCKET_MIN) {
      return Math.max(5, Math.ceil(medianOf(sameType.map((entry) => entry.seconds)) / 5) * 5);
    }
  }
  return Math.max(5, Math.ceil(medianOf(durations.map((entry) => entry.seconds)) / 5) * 5);
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

const HISTORY_STATUS_KEYS: Record<string, MessageKey> = {
  completed: "history.status.completed",
  finalized: "history.status.completed",
  available: "history.status.available",
  attached: "history.status.attached",
  partial: "history.status.partial",
  not_present: "history.status.notPresent",
  source_unavailable: "history.status.sourceUnavailable",
  unavailable: "history.status.sourceUnavailable",
  unsupported: "history.status.unsupported",
  offline: "history.status.offline",
  permission_denied: "history.status.permissionDenied",
  deleted: "history.status.deleted",
  missing: "history.status.missing",
  failed: "history.status.failed",
};

export function getHistoryStatusText(status: string | null | undefined): string {
  const key = HISTORY_STATUS_KEYS[status ?? ""];
  return key === undefined ? t("history.status.unavailableFallback") : t(key);
}

export interface TrendPresentation {
  comparable: boolean;
  summary: string;
  value: number | null;
}

const TREND_REASON_KEYS: Record<string, MessageKey> = {
  scenario_mismatch: "history.trend.scenarioMismatch",
  mode_mismatch: "history.trend.modeMismatch",
  metric_mismatch: "history.trend.metricMismatch",
  unit_mismatch: "history.trend.unitMismatch",
  calibration_mismatch: "history.trend.calibrationMismatch",
  quality_insufficient: "history.trend.qualityInsufficient",
  insufficient_history: "history.trend.insufficientHistory",
};

export function getTrendPresentation(trend: HistoryTrend): TrendPresentation {
  if (!trend.comparable || typeof trend.current !== "number") {
    const reasonKey = TREND_REASON_KEYS[trend.reason ?? ""];
    const reason = reasonKey === undefined ? t("history.trend.notComparableReason") : t(reasonKey);
    return { comparable: false, summary: t("history.trend.notComparable", { reason }), value: null };
  }
  const unit = trend.unit ? `${trend.unit}` : "";
  const current = `${trend.current}${unit}`;
  const baseline = typeof trend.baseline === "number" ? `${trend.baseline}${unit}` : null;
  const delta = typeof trend.delta === "number"
    ? `${trend.delta >= 0 ? "+" : ""}${trend.delta}${unit}`
    : null;
  // 摘要由三段可整句翻译的片段拼接（当前/· 基线/· 差异），缺段不出现。
  const summary = t("history.trend.current", { value: current })
    + (baseline ? t("history.trend.baseline", { value: baseline }) : "")
    + (delta ? t("history.trend.delta", { value: delta }) : "");
  return {
    comparable: true,
    summary,
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
  const kindLabelKeys: Record<string, MessageKey> = {
    analysis: "coach.context.analysis",
    comparison: "coach.context.comparison",
    issue: "coach.context.issue",
    time_range: "coach.context.timeRange",
    metric: "coach.context.metric",
    evidence_segment: "coach.context.evidenceSegment",
  };
  const fallbackKindKey = kindLabelKeys[context.kind];
  return {
    contextRef: context.context_ref,
    kind: context.kind,
    label: context.label ?? context.analysis_ref ?? (fallbackKindKey !== undefined ? t(fallbackKindKey) : context.context_ref),
    status: context.status,
    locator: context.locator ?? null,
  };
}

export function presentStorageCategories(
  categories: StorageCategoryTotals,
): Array<[string, number]> {
  return [
    [t("settings.storage.analysisArtifacts"), categories.analysis_artifacts_bytes],
    [t("settings.storage.runVideo"), categories.run_video_bytes],
    [t("settings.storage.runRaw"), categories.run_raw_bytes],
    [t("settings.storage.incomplete"), categories.incomplete_recovery_bytes],
  ];
}
