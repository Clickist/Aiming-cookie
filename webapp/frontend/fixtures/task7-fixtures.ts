import { readFile } from "node:fs/promises";
import path from "node:path";

import type { Page, Route } from "@playwright/test";

import type {
  AnalysisResultV2,
  CalibrationProfileV1,
  CaptureStatusV1,
  CoachContextListV1,
  CoachContextRefV1,
  CoachPrimaryResponse,
  CoachSessionOut,
  CurrentTrainingV1,
  FrontendAnalysisDataV1,
  FrontendAnalysisFamilyDataV1,
  FrontendEvidenceSegmentsV1,
  HistoryTrend,
  IncompleteCaptureListV1,
  KovaaKRunItem,
  KovaaKRunListItem,
  KovaaKScoresV1,
  ProductStateV1,
  ProviderAuthCapabilitiesV1,
  ProviderCatalogV1,
  ProviderProfile,
  ProviderProfileListResponse,
  ProviderProfileStatus,
  SessionListItem,
  SessionStatus,
  StorageResponse,
  TaskDetailV1,
  TaskListV1,
  TaskState,
} from "../lib/types";
import {
  REAL_STATIC_ANALYSIS_DATA,
  REAL_STATIC_FAMILY_DATA,
  REAL_STATIC_METRICS,
  REAL_STATIC_TIMELINE,
} from "./task7-real-static-fixture";

const NOW = "2026-07-25T06:32:00Z";
const TASK7_VIDEO = () => readFile(path.join(process.cwd(), "fixtures", "task7-video.mp4"));

export const PRODUCT_STATE: ProductStateV1 = {
  schema_version: "product_state.v1",
  availability: "available",
  onboarding_completed: true,
  onboarding_completion_kind: "connected",
  has_pending_runs: true,
  has_runs: true,
  has_analyses: true,
  error: null,
};

export const RUN_MULTIMODAL: KovaaKRunListItem = {
  id: 7,
  run_ref: "run:7",
  source_key: "1wall6targets-small",
  scenario: "1wall 6targets small",
  source_availability: { stats: "available", performance: "available", raw: "available", mp4: "available" },
  trace_quality: { state: "attached", availability: "available", alignment_status: "aligned", coverage: 1 },
  trace_state: "attached",
  trace_error: null,
  video_artifact_ref: "run:7:video",
  finalization_state: "completed",
  finalization_error: null,
  readiness_state: "analyzed",
  analysis_count: 1,
  supported_input_modes: ["input_native", "multimodal", "video_fallback"],
  evidence_availability: { stats: "available", performance: "available", raw: "available", mp4: "available" },
  alignment: { status: "aligned" },
  video_quality: { availability: "available" },
  limitations: [],
  created_at: NOW,
  updated_at: NOW,
};

export const RUN_NATIVE: KovaaKRunListItem = {
  ...RUN_MULTIMODAL,
  id: 8,
  run_ref: "run:8",
  source_key: "vox-targetswitch",
  scenario: "VoxTargetSwitch",
  video_artifact_ref: null,
  supported_input_modes: ["input_native"],
  evidence_availability: { stats: "available", performance: "available", raw: "available", mp4: "missing" },
  video_quality: { availability: "unavailable" },
  limitations: ["video_unavailable"],
};

export const RUN_PENDING_MULTIMODAL: KovaaKRunListItem = {
  ...RUN_MULTIMODAL,
  readiness_state: "pending_analysis",
  analysis_count: 0,
};

export const RUN_PENDING_NATIVE: KovaaKRunListItem = {
  ...RUN_NATIVE,
  readiness_state: "pending_analysis",
  analysis_count: 0,
};

export const CAPTURE_STATUS: CaptureStatusV1 = {
  schema_version: "capture_status.v1",
  availability: "available",
  platform_supported: true,
  raw_input_permission: "granted",
  capture_enabled: true,
  kovaak_process_present: true,
  replay_buffer_active: true,
  runtime_health: "healthy",
  finalization_state: "idle",
  pause_state: "clear",
  pause_fail_closed: false,
  runs: [
    { run_ref: "run:7", raw_attached: true, video_attached: true },
    { run_ref: "run:8", raw_attached: true, video_attached: false },
  ],
  error: null,
};

function task(state: TaskState, index: number, overrides: Partial<TaskDetailV1> = {}): TaskDetailV1 {
  const terminal = state === "done" || state === "failed";
  return {
    schema_version: "task_detail.v1",
    availability: "available",
    task_ref: `task:${index}`,
    analysis_ref: `analysis:${40 + index}`,
    state,
    state_label: "fixture label is intentionally ignored",
    phase: state === "running" ? "computing_kinematics" : state === "done" ? "generating_diagnostics" : null,
    phase_label: null,
    input_mode: index % 2 === 0 ? "input_native" : "multimodal",
    analysis_type: "flicking",
    run_ref: `run:${index}`,
    failure: null,
    partial_outcome: null,
    retryable: false,
    can_delete: terminal,
    created_at: NOW,
    started_at: state === "queued" || state === "importing" ? null : NOW,
    finished_at: terminal ? "2026-07-25T06:33:00Z" : null,
    attempt_number: 1,
    attempt_history: [],
    error: null,
    ...overrides,
  };
}

export const TASKS: TaskDetailV1[] = [
  task("importing", 1),
  task("queued", 2),
  task("running", 3),
  task("done", 4),
  task("failed", 5, {
    failure: { domain: "alignment", code: "alignment_failed", message: "输入事件无法可靠对齐。", retryable: true },
    retryable: true,
    can_delete: true,
  }),
  task("retrying", 6, {
    attempt_number: 2,
    attempt_history: [
      {
        attempt_ref: "attempt:6:1",
        attempt_number: 1,
        state: "failed",
        state_label: "Failed",
        phase: "aligning_input_events",
        failure: { domain: "alignment", code: "alignment_failed", message: "输入事件无法可靠对齐。", retryable: true },
        partial_outcome: null,
        retryable: true,
        can_delete: true,
        created_at: NOW,
        started_at: NOW,
        finished_at: NOW,
      },
      {
        attempt_ref: "attempt:6:2",
        attempt_number: 2,
        state: "retrying",
        state_label: "Retrying",
        phase: "preparing_training_record",
        failure: null,
        partial_outcome: null,
        retryable: false,
        can_delete: false,
        created_at: NOW,
        started_at: NOW,
        finished_at: null,
      },
    ],
  }),
  task("done", 7, {
    partial_outcome: { status: "partial", native_preserved: true, visual_status: "unavailable", reason_code: "video_analysis_failed" },
  }),
];

const ANALYSIS_RESULT: AnalysisResultV2 = {
  schema_version: "analysis_result.v2",
  analysis_id: "analysis:42",
  analysis_type: "flicking",
  input_mode: "input_native",
  kovaak_run_ref: "run:1030",
  evidence: {
    sources: {
      stats: { source: "stats", role: "scenario_config", availability: "available", alignment: "not_required" },
      performance: { source: "performance", role: "event_anchor", availability: "available", alignment: "partial" },
      raw_input: { source: "raw_input", role: "kinematics", availability: "available", alignment: "partial" },
    },
    provenance: { adapter: "native_flicking_analysis", adapter_version: "native_flicking.v1", kovaak_run_ref: "run:1030" },
    availability: { stats: "available", performance: "available", raw_input: "available" },
    alignment: { status: "partial", coverage_ratio: 0.9998666666666667 },
    warnings: ["trace_coverage_partial"],
  },
  deterministic: {
    status: "partial",
    support_status: "partial",
    diagnosis: {
      profile: { archetype_id: "two_stage", label: "两段式型", confidence: 1, secondary_tags: ["急加速-长减速型", "减速抖动型"] },
      issues: [
        {
          signal: "decel_frac high",
          severity: "info",
          root_causes: [
            { level: "symptom", text: "减速段占比过高，在「蹭」" },
            { level: "physical", text: "输入数据能观察到减速段偏长，但不能单独证明是制动释放不果断" },
            { level: "training", text: "减速一次到位的意识" },
          ],
          prescriptions: [
            { scenario: "pasu", reason: "练完整的加速→减速，减速果断一次到位" },
            { scenario: "1w4ts Voltaic", reason: "acc 90%+，逼你把单次 flick 加减速打完整" },
          ],
          priority: 1,
          priority_reason: "[experimental] 观察项排序第 1",
          claim_level: "experimental",
          plain_language_meaning: "速度峰值后用了较长时间完成减速",
          expected_result: "decel_frac toward individually calibrated target",
          metric_refs: ["decel_frac"],
          event_refs: ["analysis:42:event:static-flick:32", "analysis:42:event:static-flick:112"],
          limitations: ["threshold_requires_product_calibration"],
        },
        {
          signal: "reverse_ratio high",
          severity: "info",
          root_causes: [
            { level: "symptom", text: "减速段反复修正" },
            { level: "physical", text: "输入数据能观察到反向修正偏多，但不能单独证明制动方向不稳的身体原因" },
            { level: "training", text: "单次制动 + 流体修正" },
          ],
          prescriptions: [
            { scenario: "pasu", reason: "转流体派：减速段即微调，别 readjust" },
            { scenario: "Multiclick", reason: "落点精度，减少二次修正" },
          ],
          priority: 2,
          priority_reason: "[experimental] 观察项排序第 2",
          claim_level: "experimental",
          plain_language_meaning: "接近落点后出现了较多反向修正",
          expected_result: "reverse_ratio ↓",
          metric_refs: ["reverse_ratio"],
          event_refs: ["analysis:42:event:static-flick:57", "analysis:42:event:static-flick:109"],
          limitations: ["threshold_requires_product_calibration"],
        },
        {
          signal: "submovement two-stage",
          severity: "info",
          root_causes: [
            { level: "symptom", text: "flick→急停→独立 micro" },
            { level: "physical", text: "输入数据能观察到 corrective 与 primary 分离，但不能单独证明其由某种身体原因造成" },
            { level: "training", text: "转流体派（overlapping submovements）" },
          ],
          prescriptions: [
            { scenario: "pasu", reason: "转流体派：corrective 与 primary 重叠，减速段即微调" },
            { scenario: "Multiclick", reason: "落点精度，减少二次修正" },
          ],
          priority: 3,
          priority_reason: "[experimental] 观察项排序第 3",
          claim_level: "experimental",
          plain_language_meaning: "主要移动与后续修正更像两个分离动作",
          expected_result: "submovement_overlap toward chosen technique",
          metric_refs: ["submovement_overlap"],
          event_refs: ["analysis:42:event:static-flick:10", "analysis:42:event:static-flick:26", "analysis:42:event:static-flick:47"],
          limitations: ["threshold_requires_product_calibration"],
        },
      ],
      summary: {
        path_efficiency: { med: 0.9621542770432387, metric_version: "native_flicking.v1" },
        sparc: { med: -4.177432518826556, metric_version: "native_flicking.sparc.v2" },
        peak_position_pct: { med: 33.53413654618474, metric_version: "native_flicking.v1" },
        submovement_overlap: { med: 0.0019551209388482173, metric_version: "native_flicking.v1" },
        reverse_ratio: { med: 0.3563218390804598, metric_version: "native_flicking.v1" },
        decel_frac: { med: 0.6646586345381527, metric_version: "native_flicking.v1" },
      },
      comparison: null,
      meta: { input_mode: "input_native", quality_status: "limited", summary_type: "flicking" },
    },
    metrics: REAL_STATIC_METRICS,
    timeline: REAL_STATIC_TIMELINE,
    limitations: ["target_relative_facts_unavailable", "alignment_partial"],
  },
  scenario: {
    scenario_profile_ref: "scenario:static.1wall_6targets_small@1",
    analyzer_refs: ["native_flicking.v1"],
    support_status: "partial",
    limitations: ["target_relative_facts_unavailable", "alignment_partial"],
  },
  artifact_manifest: { schema_version: "artifact_manifest.v2", external_inputs: [], owned_outputs: [] },
  input_snapshot: {
    scenario: "1wall 6targets small",
    scenario_resolution: {
      schema_version: "scenario_resolution.v1",
      aim_family: "static_clicking",
      claim_ceiling: "family_specific",
      family_analyzer_dispatch: "allowed",
      scenario_profile_ref: "scenario:static.1wall_6targets_small@1",
      classification_source: "reviewed_registry",
      limitations: [
        "Exact reviewed scenario hash only; other hashes with the same display name remain unclassified.",
        "Input-native metrics do not establish target-relative error, overshoot, or undershoot.",
      ],
    },
    calibration: {
      cm_per_360: { value: 54.43, source: "stats" },
      fov: { value: 103, source: "stats" },
    },
    sources: {},
    trace: null,
  },
  created_at: "2026-07-28T15:22:42Z",
  completed_at: "2026-07-28T15:22:55Z",
  warnings: [],
  errors: [],
  normalization_issues: [],
};

export function analysisSession(overrides: Partial<SessionStatus> = {}): SessionStatus {
  return {
    id: 42,
    status: "done",
    result: ANALYSIS_RESULT,
    error: null,
    llm_cost_cny: null,
    created_at: "2026-07-28T15:22:42Z",
    attempts: 1,
    max_attempts: 2,
    worker_id: null,
    started_at: "2026-07-28T15:22:42Z",
    finished_at: "2026-07-28T15:22:55Z",
    analysis_type: "flicking",
    input_mode: "input_native",
    kovaak_run_id: 1030,
    history: {
      analysis_ref: "analysis:42",
      run_ref: "run:1030",
      scenario: "1wall 6targets small",
      input_mode: "input_native",
      source_availability: { stats: "available", performance: "available", raw: "available", mp4: "unavailable" },
      trace_quality: { state: "attached", availability: "available", alignment_status: "partial", coverage: 0.9998666666666667 },
      visual_replay: {
        kind: "native_only",
        available: false,
        seekable: false,
        endpoint: null,
        artifact_ref: null,
        reason: "input_native_without_managed_video",
      },
      diagnosis_locator: { analysis_ref: "analysis:42", section: "diagnosis" },
      evidence_refs: [],
    },
    ...overrides,
  };
}

export function registryBackedAnalysisSession(): SessionStatus {
  const base = analysisSession();
  const result = base.result;
  if (!result || result.schema_version !== "analysis_result.v2") {
    throw new Error("registry-backed fixture requires AnalysisResult v2");
  }
  const diagnosis = result.deterministic.diagnosis;
  const firstIssue = diagnosis?.issues[0];
  if (!diagnosis || !firstIssue) {
    throw new Error("registry-backed fixture requires a diagnosis issue");
  }
  const issue = {
    ...firstIssue,
    claim_level: "deterministic_rule",
    observation_ref: "static_clicking.reverse_ratio",
    knowledge_registry_version: "2026-07-29.v4",
    knowledge_entry_refs: ["knowledge:static.flicking-terminal-control@2"],
    plain_language_meaning: "停枪控制不稳是当前证据支持的候选解释，不代表已确认的身体或动作根因。",
    expected_result: "反向修正出现得更早，且在同一场景和证据质量下可以复测。",
  };
  delete issue.root_causes;
  delete issue.prescriptions;
  return analysisSession({
    result: {
      ...result,
      deterministic: {
        ...result.deterministic,
        diagnosis: {
          ...diagnosis,
          issues: [issue, ...diagnosis.issues.slice(1)],
        },
      },
    },
  });
}

export function partialAnalysisSession(): SessionStatus {
  const result: AnalysisResultV2 = {
    ...ANALYSIS_RESULT,
    input_mode: "multimodal",
    evidence: {
      ...ANALYSIS_RESULT.evidence,
      sources: {
        ...(ANALYSIS_RESULT.evidence.sources as Record<string, { source?: string; availability?: string; alignment?: string }>),
        mp4: { source: "mp4", availability: "unavailable", alignment: "aligned" },
      },
      availability: { ...ANALYSIS_RESULT.evidence.availability, mp4: "unavailable" },
      warnings: ["visual_replay_unavailable"],
    },
  };
  const base = analysisSession();
  return analysisSession({
    result,
    input_mode: "multimodal",
    history: {
      ...base.history!,
      input_mode: "multimodal",
      source_availability: { ...base.history!.source_availability, mp4: "unavailable" },
      visual_replay: {
        kind: "unavailable",
        available: false,
        seekable: false,
        endpoint: null,
        artifact_ref: "analysis:42:video",
        reason: "run_owned_video_unavailable",
      },
    },
  });
}

export const SESSION_LIST: SessionListItem[] = [
  {
    id: 42,
    analysis_ref: "analysis:42",
    run_ref: "run:1030",
    status: "done",
    created_at: "2026-07-28T15:22:42Z",
    finished_at: "2026-07-28T15:22:55Z",
    attempts: 1,
    max_attempts: 2,
    llm_cost_cny: null,
    summary_label: "diagnosis",
    analysis_type: "flicking",
    input_mode: "input_native",
    kovaak_run_id: 1030,
    scenario: "1wall 6targets small",
    source_availability: { stats: "available", performance: "available", raw: "available", mp4: "unavailable" },
    trace_quality: { state: "attached", availability: "available", alignment_status: "partial", coverage: 0.9998666666666667 },
  },
];

export const EVIDENCE_SEGMENTS: FrontendEvidenceSegmentsV1 = {
  schema_version: "frontend_evidence_segments.v1",
  analysis_ref: "analysis:42",
  video_availability: "unavailable",
  video_route: null,
  canonical_window_start_ms: 1785151583880,
  segments: [
    {
      segment_id: "analysis:42:segment:worst:20",
      analysis_ref: "analysis:42",
      analyzer_ref: "native_flicking.v1",
      segment_kind: "worst",
      start_ms: 1785151593273,
      end_ms: 1785151593750,
      focus_start_ms: 1785151593273,
      focus_end_ms: 1785151593750,
      title_key: "static_clicking.worst",
      rank_reason: "worst",
      issue_refs: [],
      metric_refs: ["metric:static_clicking.corrective_count@native_flicking.v1"],
      event_refs: ["analysis:42:event:static-flick:20"],
      available_channels: ["mouse.position_x", "mouse.position_y", "mouse.speed", "mouse.acceleration"],
      source_coverage: 0.9998666666666667,
      confidence: 0.9998666666666667,
      limitations: ["within_run_rank_not_learning_effect", "descriptive_rank_not_health_threshold"],
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "unavailable",
        video_route: null,
        relative_start_ms: null,
        relative_end_ms: null,
        limitations: ["within_run_rank_not_learning_effect", "descriptive_rank_not_health_threshold", "local_video_seek_unavailable"],
      },
    },
    {
      segment_id: "analysis:42:segment:improved:65",
      analysis_ref: "analysis:42",
      analyzer_ref: "native_flicking.v1",
      segment_kind: "improved",
      start_ms: 1785151614731,
      end_ms: 1785151615203,
      focus_start_ms: 1785151614731,
      focus_end_ms: 1785151615176,
      title_key: "static_clicking.improved",
      rank_reason: "improved",
      issue_refs: [],
      metric_refs: ["metric:static_clicking.corrective_count@native_flicking.v1"],
      event_refs: ["analysis:42:event:static-flick:65"],
      available_channels: ["mouse.position_x", "mouse.position_y", "mouse.speed", "mouse.acceleration"],
      source_coverage: 0.9998666666666667,
      confidence: 0.9998666666666667,
      limitations: ["within_run_late_relative_improvement_not_learning_effect", "descriptive_rank_not_health_threshold"],
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "unavailable",
        video_route: null,
        relative_start_ms: null,
        relative_end_ms: null,
        limitations: ["within_run_late_relative_improvement_not_learning_effect", "descriptive_rank_not_health_threshold", "local_video_seek_unavailable"],
      },
    },
    {
      segment_id: "analysis:42:segment:typical:1",
      analysis_ref: "analysis:42",
      analyzer_ref: "native_flicking.v1",
      segment_kind: "typical",
      start_ms: 1785151583884,
      end_ms: 1785151584676,
      focus_start_ms: 1785151583884,
      focus_end_ms: 1785151584638,
      title_key: "static_clicking.typical",
      rank_reason: "typical",
      issue_refs: [],
      metric_refs: ["metric:static_clicking.corrective_count@native_flicking.v1"],
      event_refs: ["analysis:42:event:static-flick:1"],
      available_channels: ["mouse.position_x", "mouse.position_y", "mouse.speed", "mouse.acceleration"],
      source_coverage: 0.9998666666666667,
      confidence: 0.9998666666666667,
      limitations: ["within_run_rank_not_learning_effect", "descriptive_rank_not_health_threshold"],
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "unavailable",
        video_route: null,
        relative_start_ms: null,
        relative_end_ms: null,
        limitations: ["within_run_rank_not_learning_effect", "descriptive_rank_not_health_threshold", "local_video_seek_unavailable"],
      },
    },
  ],
};

export const UNAVAILABLE_EVIDENCE_SEGMENTS: FrontendEvidenceSegmentsV1 = {
  schema_version: "frontend_evidence_segments.v1",
  analysis_ref: "analysis:42",
  video_availability: "unavailable",
  video_route: null,
  canonical_window_start_ms: null,
  segments: [],
};

export const ANALYSIS_DATA_TRACKING: FrontendAnalysisDataV1 = {
  schema_version: "frontend_analysis_data.v1",
  analysis_ref: "analysis:42",
  limitations: ["visual_quality_limited"],
  event_markers: [
    { event_ref: "analysis:42:event:target-change:1", kind: "target_change_point", relative_ms: 800 },
    { event_ref: "analysis:42:event:tracking-loss:1", kind: "tracking_loss", relative_ms: 1200 },
  ],
  event_distribution: [
    { kind: "target_change_point", count: 2 },
    { kind: "tracking_loss", count: 1 },
  ],
  target_relative_error_radius: {
    availability: "available",
    reason: null,
    points: [
      { relative_ms: 400, normalized_error_radius: 0.35 },
      { relative_ms: 800, normalized_error_radius: 0.8 },
      { relative_ms: 1200, normalized_error_radius: 0.45 },
    ],
  },
};

export const ANALYSIS_DATA = REAL_STATIC_ANALYSIS_DATA;

export const ANALYSIS_FAMILY_TRACKING: FrontendAnalysisFamilyDataV1 = {
  schema_version: "frontend_analysis_family_data.v1",
  analysis_ref: "analysis:42",
  family: "tracking",
  availability: "available",
  reason: null,
  limitations: [],
  total_count: 4,
  next_offset: null,
  rows: [
    { kind: "tracking_fixed_window", timing: { start_ms: 400, end_ms: 900 }, metrics: { target_relative_error_px: 18.4, time_in_radius_ratio: 0.87, correction_burden: 0.42, sparc: -2.14 }, limitations: [] },
    { kind: "tracking_loss", timing: { start_ms: 1200, end_ms: 1324 }, metrics: { duration_ms: 124 }, limitations: [] },
    { kind: "tracking_reacquisition", timing: { start_ms: 1324, end_ms: 1480 }, metrics: { reacquisition_latency_ms: 156 }, limitations: [] },
    { kind: "tracking_change_response", timing: { start_ms: 1800, end_ms: 1930 }, metrics: { observed_change_response_ms: 130, alignment_latency_ms: 22, post_change_error_px: 21.5 }, limitations: ["capture_alignment_descriptor_not_human_response"] },
  ],
};

export const ANALYSIS_FAMILY_SWITCHING: FrontendAnalysisFamilyDataV1 = {
  schema_version: "frontend_analysis_family_data.v1",
  analysis_ref: "analysis:42",
  family: "switching",
  availability: "available",
  reason: null,
  limitations: [],
  total_count: 2,
  next_offset: null,
  rows: [
    { kind: "switch_chain", timing: { kill_ms: 1200, transition_ms: 1220, acquire_ms: 1388, settle_ms: 1430 }, metrics: { transition_time_ms: 168, transition_distance_px: 512, path_efficiency: 0.91, settle_duration_ms: 42 }, limitations: [] },
    { kind: "switch_chain", timing: { kill_ms: 4100, transition_ms: 4140, acquire_ms: 4552, settle_ms: 4638 }, metrics: { transition_time_ms: 412, transition_distance_px: 964, path_efficiency: 0.52, settle_duration_ms: 86 }, limitations: [] },
  ],
};

export const ANALYSIS_FAMILY_FLICKING = REAL_STATIC_FAMILY_DATA;

export const ANALYSIS_FAMILY_FLICKING_UNAVAILABLE: FrontendAnalysisFamilyDataV1 = {
  schema_version: "frontend_analysis_family_data.v1",
  analysis_ref: "analysis:42",
  family: "flicking",
  availability: "unavailable",
  reason: "family_detail_requires_input_native_flicking",
  limitations: [],
  total_count: 0,
  next_offset: null,
  rows: [],
};

export const PROVIDER_CATALOG: ProviderCatalogV1 = {
  schema_version: "coach_provider_catalog.v1",
  providers: [{
    provider_id: "openai",
    provider_name: "OpenAI",
    auth_modes: ["api_key", "oauth"],
    models: [{ model_id: "gpt-5.4", model_name: "GPT-5.4" }],
  }],
};

export const PROVIDER_CAPABILITIES: ProviderAuthCapabilitiesV1 = {
  schema_version: "coach_provider_auth_capabilities.v1",
  providers: [{ provider_id: "openai", provider_name: "OpenAI", auth_modes: ["api_key", "oauth"] }],
};

export const PROVIDER_PROFILE: ProviderProfile = {
  id: 1,
  name: "OpenAI",
  provider_id: "openai",
  kind: "builtin",
  base_url: null,
  model_id: "gpt-5.4",
  is_default: true,
  configured: true,
  credential_configured: true,
  has_api_key: true,
  status: "ready",
  created_at: NOW,
  updated_at: NOW,
};

export const READY_PROVIDER_STATUS: ProviderProfileStatus = {
  profile_id: 1,
  configured: true,
  status: "ready",
  message: "Provider ready",
};

export const CALIBRATION_PROFILE: CalibrationProfileV1 = {
  schema_version: "calibration_profile.v1",
  configured: true,
  values: { cm_per_360: 42, fov: 103 },
  dpi: null,
  sensitivity: null,
  adoption_priority: ["stats", "manual_override", "profile_default", "undetermined"],
  updated_at: NOW,
  deletion_state: null,
};

export const STORAGE: StorageResponse = {
  total_bytes: 2_500_000_000,
  categories: {
    analysis_artifacts_bytes: 180_000_000,
    run_video_bytes: 2_000_000_000,
    run_raw_bytes: 300_000_000,
    incomplete_recovery_bytes: 20_000_000,
  },
  sessions: [{ session_id: 42, status: "done", created_at: NOW, workspace_bytes: 180_000_000 }],
};

export const INCOMPLETE_CAPTURES: IncompleteCaptureListV1 = {
  schema_version: "incomplete_capture_list.v1",
  total_bytes: 20_000_000,
  items: [{
    schema_version: "incomplete_capture_item.v1",
    item_ref: "incomplete:1",
    run_ref: "run:recovery:1",
    size_bytes: 20_000_000,
    reason: "interrupted_finalization",
    removable: true,
    impact: { code: "incomplete_recovery_only", message: "只移除未完成采集恢复材料。" },
    created_at: NOW,
  }],
};

export const COACH_CONTEXTS: CoachContextListV1 = {
  schema_version: "coach_context_list.v1",
  contexts: [{
    schema_version: "coach_context_ref.v1",
    context_ref: "context:analysis:42",
    kind: "analysis",
    status: "active",
    label: "1wall 6targets small · 7月28日",
    analysis_ref: "analysis:42",
    comparison_analysis_ref: null,
    target_ref: null,
    time_range_ms: null,
    attached_at: NOW,
    detached_at: null,
    deleted_at: null,
    locator: { view: "diagnosis" },
  }],
};

export const COACH_PRIMARY: CoachPrimaryResponse = {
  thread: { id: 1, user_id: "dev", kind: "primary", created_at: NOW, updated_at: NOW },
  messages: [{
    id: 1,
    role: "assistant",
    content: "先稳定接近目标时的减速节奏，再复测同一场景。",
    created_at: NOW,
    legacy_session_id: null,
    context_refs: COACH_CONTEXTS.contexts,
  }],
  refs: [{ id: 1, analysis_session_id: 42, status: "active", attached_at: NOW, deleted_at: null }],
};

export const COACH_SESSIONS: CoachSessionOut[] = [
  {
    id: 1,
    user_id: "dev",
    kind: "primary",
    title: "长期训练",
    status: "active",
    deleted_at: null,
    created_at: NOW,
    updated_at: NOW,
    message_count: 1,
    last_message_preview: "先稳定接近目标时的减速节奏，再复测同一场景。",
    analysis_session_ids: [42],
  },
  {
    id: 2,
    user_id: "dev",
    kind: "conversation",
    title: "1wall 6targets 复盘",
    status: "active",
    deleted_at: null,
    created_at: NOW,
    updated_at: NOW,
    message_count: 0,
    last_message_preview: null,
    analysis_session_ids: [],
  },
];

export const CURRENT_TRAINING_ACTIVE: CurrentTrainingV1 = {
  // Frozen from real Run 1030, reanalyzed as Session 26 against Registry v4.
  schema_version: "current_training.v1",
  availability: "available",
  reason: null,
  plan_status: "active",
  total_item_count: 1,
  visible_item_count: 1,
  limitations: [],
  items: [
    {
      display_name: "1wall 6targets small",
      scenario_profile_ref: "scenario:static.1wall_6targets_small@1",
      scenario_availability: "available",
      status: "planned",
      practice_condition: "保持完全相同的静态场景条件，只测试一个终点控制提示。",
      cue: "只使用一个动作效果提示：先受控地到达目标，再让点击跟随已经稳定的瞄点。",
      dose_guardrail: "使用能够清楚判断表现的难度版本，每次只改变一个任务变量；如果出现不适，或与提示无关的表现质量明显下降，就停止或降低难度。",
      observation: null,
      retest: "下一次可比训练后复查。",
    },
  ],
};

export const CURRENT_TRAINING_NO_PLAN: CurrentTrainingV1 = {
  schema_version: "current_training.v1",
  availability: "available",
  reason: "no_current_plan",
  plan_status: null,
  total_item_count: 0,
  visible_item_count: 0,
  limitations: [],
  items: [],
};

export const CURRENT_TRAINING_PAUSED: CurrentTrainingV1 = {
  ...CURRENT_TRAINING_ACTIVE,
  plan_status: "paused",
};

export const CURRENT_TRAINING_UNAVAILABLE: CurrentTrainingV1 = {
  schema_version: "current_training.v1",
  availability: "unavailable",
  reason: null,
  plan_status: null,
  total_item_count: 0,
  visible_item_count: 0,
  limitations: ["current_training_projection_unavailable"],
  items: [],
};

export const KOVAAK_SCORES: KovaaKScoresV1 = {
  schema_version: "kovaak_scores.v1",
  availability: "unavailable",
  observed_at: null,
  stages: [],
  items: [],
};

export const KOVAAK_SCORES_AVAILABLE: KovaaKScoresV1 = {
  schema_version: "kovaak_scores.v1",
  availability: "available",
  observed_at: NOW,
  stages: [
    { stage: "easier", completed: 18, required: 39, rank: 5, rank_name: "黄金 III" },
    { stage: "medium", completed: 0, required: 39, rank: 0, rank_name: "未完成" },
  ],
  items: [
    { stage: "easier", name: "controlsphere", category: "Control Tracking", subcategory: "稳定跟随", score: 8214, item_rank: 4, item_rank_name: "黄金 I", completed: true },
    { stage: "easier", name: "air", category: "Reactive Tracking", subcategory: "变化跟随", score: 5141, item_rank: 2, item_rank_name: "白银 I", completed: true },
    { stage: "easier", name: "1wall 6targets small", category: "Flick Tech", subcategory: "快速点击", score: 1022, item_rank: 5, item_rank_name: "黄金 III", completed: true },
    { stage: "medium", name: "microshot speed", category: "Click Timing", subcategory: "点击时机", score: 0, item_rank: 0, item_rank_name: "未完成", completed: false },
  ],
};

export interface ApiScenario {
  productState: ProductStateV1;
  runs: KovaaKRunListItem[];
  tasks: TaskDetailV1[];
  sessions: SessionListItem[];
  analysis: SessionStatus;
  analysisData: FrontendAnalysisDataV1;
  analysisFamilyData: FrontendAnalysisFamilyDataV1;
  evidenceSegments: FrontendEvidenceSegmentsV1;
  capture: CaptureStatusV1;
  providerStatus: ProviderProfileStatus;
  currentTraining: CurrentTrainingV1;
  coachContexts: CoachContextListV1;
  coachSessions: CoachSessionOut[];
  profiles: ProviderProfileListResponse;
  kovaakConnected: boolean;
  kovaakScores: KovaaKScoresV1;
  failures: Record<string, number>;
}

export function apiScenario(overrides: Partial<ApiScenario> = {}): ApiScenario {
  return {
    productState: PRODUCT_STATE,
    runs: [RUN_MULTIMODAL, RUN_NATIVE],
    tasks: TASKS,
    sessions: SESSION_LIST,
    analysis: analysisSession(),
    analysisData: ANALYSIS_DATA,
    analysisFamilyData: ANALYSIS_FAMILY_FLICKING,
    evidenceSegments: EVIDENCE_SEGMENTS,
    capture: CAPTURE_STATUS,
    providerStatus: READY_PROVIDER_STATUS,
    currentTraining: CURRENT_TRAINING_ACTIVE,
    coachContexts: {
      ...COACH_CONTEXTS,
      contexts: COACH_CONTEXTS.contexts.map((context) => ({ ...context })),
    },
    coachSessions: COACH_SESSIONS.map((session) => ({ ...session })),
    profiles: { profiles: [PROVIDER_PROFILE] },
    kovaakConnected: true,
    kovaakScores: KOVAAK_SCORES,
    failures: {},
    ...overrides,
  };
}

function runDetail(run: KovaaKRunListItem): KovaaKRunItem {
  return { ...run, stats_source_ref: null, performance_source_ref: null, trace_artifact_ref: null, stats_summary: null, performance_summary: null };
}

export interface ReviewApiRequest {
  method: string;
  path: string;
  body?: unknown;
  query?: Record<string, string>;
}

export interface ReviewApiResponse {
  status: number;
  body: unknown;
  video?: boolean;
}

const response = (body: unknown, status = 200): ReviewApiResponse => ({ status, body });
const requestBody = (body: unknown): Record<string, unknown> => body && typeof body === "object" ? body as Record<string, unknown> : {};

function analysisFamilyPage(
  data: FrontendAnalysisFamilyDataV1,
  query: Record<string, string> | undefined,
): FrontendAnalysisFamilyDataV1 {
  if (data.availability === "unavailable") return data;
  const rawLimit = Number(query?.limit ?? 50);
  const rawOffset = Number(query?.offset ?? 0);
  const limit = Number.isInteger(rawLimit) ? Math.min(100, Math.max(1, rawLimit)) : 50;
  const offset = Number.isInteger(rawOffset) ? Math.max(0, rawOffset) : 0;
  const rows = data.rows.slice(offset, offset + limit);
  const nextOffset = offset + rows.length;
  return {
    ...data,
    total_count: data.rows.length,
    next_offset: nextOffset < data.rows.length ? nextOffset : null,
    rows,
  };
}

/** Shared by Playwright's adapter and the local Next.js review route. */
export function handleReviewApiRequest(scenario: ApiScenario, request: ReviewApiRequest): ReviewApiResponse {
  const { method, path } = request;
  const failureStatus = scenario.failures[`${method} ${path}`] ?? scenario.failures[path];
  if (failureStatus) return response({ detail: { code: "fixture_unavailable", message: "Fixture service unavailable" } }, failureStatus);
  if (path === "/api/product-state" && method === "GET") return response(scenario.productState);
  if (path === "/api/product-state/onboarding" && method === "POST") {
    const body = requestBody(request.body);
    scenario.productState = { ...scenario.productState, onboarding_completed: true, onboarding_completion_kind: "connected" };
    return response(scenario.productState);
  }
  if (path === "/api/providers/catalog") return response(PROVIDER_CATALOG);
  if (path === "/api/provider-auth/capabilities") return response(PROVIDER_CAPABILITIES);
  if (path === "/api/provider-profiles/status") return response(scenario.providerStatus);
  if (path === "/api/provider-profiles" && method === "GET") return response(scenario.profiles);
  if (path === "/api/provider-profiles" && method === "POST") {
    const body = requestBody(request.body);
    const id = Math.max(0, ...scenario.profiles.profiles.map((profile) => profile.id)) + 1;
    const profile: ProviderProfile = { id, name: String(body.name ?? "Custom provider"), provider_id: String(body.provider_id ?? "custom"), kind: body.kind as ProviderProfile["kind"], base_url: typeof body.base_url === "string" ? body.base_url : null, model_id: String(body.model_id ?? "review-model"), is_default: Boolean(body.is_default), configured: Boolean(body.api_key), credential_configured: Boolean(body.api_key), has_api_key: Boolean(body.api_key), status: "unconfigured", created_at: NOW, updated_at: NOW };
    if (profile.is_default) scenario.profiles.profiles.forEach((item) => { item.is_default = false; });
    scenario.profiles.profiles.push(profile);
    return response(profile);
  }
  if (path === "/api/provider-profiles/custom/models" && method === "POST") {
    const body = requestBody(request.body);
    return typeof body.base_url === "string" && body.base_url.includes("unavailable") ? response({ detail: { code: "model_discovery_failed", message: "Mock provider unavailable" } }, 503) : response({
      models: [
        { model_id: "custom-model-a", context_window: 32768, max_tokens: 4096 },
        { model_id: "custom-model-b", context_window: 65536, max_tokens: 8192 },
      ],
      protocol: body.protocol ?? "openai-completions",
    });
  }
  const providerAction = /^\/api\/provider-profiles\/(\d+)(?:\/(test|default)|\/auth\/api-key)?$/.exec(path);
  if (providerAction) {
    const profile = scenario.profiles.profiles.find((item) => item.id === Number(providerAction[1]));
    if (!profile) return response({ detail: "Not found" }, 404);
    if (providerAction[2] === "test" && method === "POST") { profile.status = "ready"; scenario.providerStatus = { profile_id: profile.id, configured: true, status: "ready", message: "Provider ready" }; return response(scenario.providerStatus); }
    if (providerAction[2] === "default" && method === "POST") { scenario.profiles.profiles.forEach((item) => { item.is_default = item.id === profile.id; }); return response(profile); }
    if (path.endsWith("/auth/api-key") && method === "PUT") { profile.configured = true; profile.credential_configured = true; profile.has_api_key = true; return response(profile); }
    if (method === "DELETE") { scenario.profiles.profiles = scenario.profiles.profiles.filter((item) => item.id !== profile.id); return response({ deleted: true }); }
  }
  if (path === "/api/kovaak-connection" && method === "GET") return response({ connected: scenario.kovaakConnected });
  if (path === "/api/kovaak-connection" && method === "PUT") { scenario.kovaakConnected = true; return response({ connected: true }); }
  if (path === "/api/kovaak-connection" && method === "DELETE") { scenario.kovaakConnected = false; return response({ deleted: true }); }
  if (path === "/api/kovaak-connection/refresh" && method === "POST") return response({ schema_version: "kovaak_benchmark_sync_result.v1", imported_score_count: scenario.kovaakScores.items.length, difficulty_counts: { easier: 0, medium: 0 }, observed_at: NOW });
  if (path === "/api/kovaak-scores") return response(scenario.kovaakScores);
  if (path === "/api/calibration-profile" && method === "GET") return response(CALIBRATION_PROFILE);
  if (path === "/api/capture-status") return response(scenario.capture);
  if (path === "/api/storage") return response(STORAGE);
  if (path === "/api/storage/incomplete") return response(INCOMPLETE_CAPTURES);
  if (path === "/api/tasks") return response({ schema_version: "task_list.v1", availability: "available", tasks: scenario.tasks, error: null } satisfies TaskListV1);
  if (path === "/api/kovaak-runs") return response({ runs: scenario.runs });
  const runMatch = /^\/api\/kovaak-runs\/(\d+)$/.exec(path);
  if (runMatch) { const run = scenario.runs.find((candidate) => candidate.id === Number(runMatch[1])); return run ? response(runDetail(run)) : response({ detail: "Not found" }, 404); }
  if (/^\/api\/kovaak-runs\/\d+\/analyze$/.test(path)) return response({ session_id: 42 });
  if (path === "/api/sessions") return response({ sessions: scenario.sessions });
  if (path === "/api/sessions/42/analysis-data") return response(scenario.analysisData);
  if (path === "/api/sessions/42/analysis-data/family") return response(analysisFamilyPage(scenario.analysisFamilyData, request.query));
  if (path === "/api/sessions/42/evidence-segments") return response(scenario.evidenceSegments);
  if (path === "/api/sessions/42/video") {
    return scenario.analysis.history?.visual_replay.kind === "seekable_mp4"
      ? { status: 200, body: null, video: true }
      : response({ detail: { code: "managed_video_unavailable", message: "Managed video is unavailable for this Analysis" } }, 410);
  }
  if (path === "/api/sessions/42/retry" && method === "POST") return response({ ...scenario.analysis, id: 43, status: "queued" });
  if (path === "/api/sessions/42") return response(scenario.analysis);
  if (path.startsWith("/api/history/trends/")) return response({ comparable: false, reason: "insufficient_records" } satisfies HistoryTrend);
  if (path === "/api/coach/sessions" && method === "GET") {
    const includeArchived = request.query?.include_archived === "true";
    const query = request.query?.q?.trim().toLocaleLowerCase();
    const sessions = scenario.coachSessions.filter((session) => {
      if (session.status === "deleted" || (!includeArchived && session.status === "archived")) return false;
      return !query || [session.title, session.last_message_preview]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(query));
    });
    return response({ schema_version: "coach_session_list.v1", sessions });
  }
  if (path === "/api/coach/sessions" && method === "POST") {
    const body = requestBody(request.body);
    const id = Math.max(0, ...scenario.coachSessions.map((session) => session.id)) + 1;
    const session: CoachSessionOut = {
      id,
      user_id: "dev",
      kind: "conversation",
      title: typeof body.title === "string" ? body.title : null,
      status: "active",
      deleted_at: null,
      created_at: NOW,
      updated_at: NOW,
      message_count: 0,
      last_message_preview: null,
      analysis_session_ids: [],
    };
    scenario.coachSessions.push(session);
    return response(session, 201);
  }
  const coachSessionMatch = /^\/api\/coach\/sessions\/(\d+)$/.exec(path);
  if (coachSessionMatch) {
    const session = scenario.coachSessions.find((candidate) => candidate.id === Number(coachSessionMatch[1]));
    if (!session) return response({ detail: "Not found" }, 404);
    if (method === "DELETE") {
      session.status = "deleted";
      session.deleted_at = NOW;
    } else if (method === "PATCH") {
      const body = requestBody(request.body);
      if (body.status === "archived") session.status = "archived";
      if (typeof body.title === "string") session.title = body.title;
    }
    return response(session);
  }
  if (path === "/api/coach/context") return response(scenario.coachContexts);
  if (path === "/api/coach/context/attach" && method === "POST") {
    const body = requestBody(request.body);
    const analysisRef = typeof body.analysis_ref === "string" ? body.analysis_ref : "";
    if (body.kind !== "analysis" || !/^analysis:\d+$/.test(analysisRef)) {
      return response({ detail: { code: "invalid_analysis_ref", message: "Analysis reference is invalid" } }, 400);
    }
    const session = scenario.sessions.find((item) => item.analysis_ref === analysisRef);
    if (!session || session.status !== "done") {
      return response({ detail: { code: "not_found", message: "Analysis is unavailable" } }, 404);
    }
    const existing = scenario.coachContexts.contexts.find(
      (context) => context.kind === "analysis" && context.analysis_ref === analysisRef && context.status === "active",
    );
    if (existing) {
      return response({ schema_version: "coach_context_mutation.v1", action: "already_attached", context: existing });
    }
    const context: CoachContextRefV1 = {
      schema_version: "coach_context_ref.v1",
      context_ref: `context:${analysisRef}`,
      kind: "analysis",
      status: "active",
      label: session.presentation_label ?? analysisRef,
      analysis_ref: analysisRef,
      comparison_analysis_ref: null,
      target_ref: null,
      time_range_ms: null,
      attached_at: NOW,
      detached_at: null,
      deleted_at: null,
      locator: { view: "diagnosis" },
    };
    scenario.coachContexts.contexts.push(context);
    return response({ schema_version: "coach_context_mutation.v1", action: "attached", context });
  }
  if (path === "/api/coach/primary") return response(COACH_PRIMARY);
  if (path === "/api/current-training" && method === "GET") return response(scenario.currentTraining);
  if (path === "/api/coach/agent-runs" && method === "POST") return response({ schema_version: "coach_agent_run.v1", run_ref: "coach-run:1", parent_run_ref: null, attempt: 1, status: "running", phase: "text_generation", partial_text: "正在整理证据", error: null, contexts: COACH_CONTEXTS.contexts, events: [], created_at: NOW, started_at: NOW, finished_at: null });
  if (path === "/api/analyze" || path === "/api/desktop/analyze-paths") return response({ session_id: 42 });
  return response({ detail: { code: "fixture_route_missing", message: `${method} ${path}` } }, 501);
}

export async function readReviewVideo(): Promise<Buffer> {
  return TASK7_VIDEO();
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function fulfillVideo(route: Route): Promise<void> {
  const body = await TASK7_VIDEO();
  const range = route.request().headers().range;
  if (!range) {
    await route.fulfill({
      status: 200,
      contentType: "video/mp4",
      headers: { "Accept-Ranges": "bytes", "Content-Length": String(body.length) },
      body,
    });
    return;
  }
  const match = /^bytes=(\d+)-(\d*)$/.exec(range);
  const start = match ? Number(match[1]) : body.length;
  const end = match?.[2] ? Math.min(Number(match[2]), body.length - 1) : body.length - 1;
  if (!match || start >= body.length || end < start) {
    await route.fulfill({
      status: 416,
      headers: { "Content-Range": `bytes */${body.length}` },
    });
    return;
  }
  const chunk = body.subarray(start, end + 1);
  await route.fulfill({
    status: 206,
    contentType: "video/mp4",
    headers: {
      "Accept-Ranges": "bytes",
      "Content-Length": String(chunk.length),
      "Content-Range": `bytes ${start}-${end}/${body.length}`,
    },
    body: chunk,
  });
}

export async function installApiFixtures(page: Page, scenario = apiScenario()): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    let body: unknown;
    if (method !== "GET" && method !== "HEAD") {
      try {
        body = request.postDataJSON();
      } catch {
        body = undefined;
      }
    }
    const shared = handleReviewApiRequest(scenario, { method, path, body, query: Object.fromEntries(url.searchParams) });
    if (shared.video) return fulfillVideo(route);
    return fulfillJson(route, shared.body, shared.status);
    /*
    // Legacy explicit map retained below only until its static cases are removed.
    const failureStatus = scenario.failures[`${method} ${path}`] ?? scenario.failures[path];
    if (failureStatus) return fulfillJson(route, { detail: { code: "fixture_unavailable", message: "Fixture service unavailable" } }, failureStatus);

    if (path === "/api/product-state") return fulfillJson(route, scenario.productState);
    if (path === "/api/product-state/onboarding") return fulfillJson(route, { ...scenario.productState, onboarding_completed: true });
    if (path === "/api/providers/catalog") return fulfillJson(route, PROVIDER_CATALOG);
    if (path === "/api/provider-auth/capabilities") return fulfillJson(route, PROVIDER_CAPABILITIES);
    if (path === "/api/provider-profiles/status") return fulfillJson(route, scenario.providerStatus);
    if (path === "/api/provider-profiles" && method === "GET") return fulfillJson(route, scenario.profiles);
    if (path === "/api/provider-profiles/custom/models" && method === "POST") return fulfillJson(route, {
      models: [
        { model_id: "custom-model-a", context_window: 32768, max_tokens: 4096 },
        { model_id: "custom-model-b", context_window: 65536, max_tokens: 8192 },
      ],
    });
    if (path === "/api/kovaak-connection" && method === "GET") return fulfillJson(route, { connected: scenario.kovaakConnected });
    if (path === "/api/kovaak-connection" && method === "PUT") return fulfillJson(route, { connected: true });
    if (path === "/api/kovaak-connection" && method === "DELETE") return fulfillJson(route, { deleted: true });
    if (path === "/api/kovaak-connection/refresh" && method === "POST") {
      return fulfillJson(route, { schema_version: "kovaak_benchmark_sync_result.v1", imported_score_count: scenario.kovaakScores.items.length, difficulty_counts: { easier: 18, medium: 0 }, observed_at: NOW });
    }
    if (path === "/api/kovaak-scores" && method === "GET") return fulfillJson(route, scenario.kovaakScores);
    if (path === "/api/calibration-profile") return fulfillJson(route, CALIBRATION_PROFILE);
    if (path === "/api/capture-status") return fulfillJson(route, scenario.capture);
    if (path === "/api/storage") return fulfillJson(route, STORAGE);
    if (path === "/api/storage/incomplete") return fulfillJson(route, INCOMPLETE_CAPTURES);
    if (path === "/api/tasks") {
      const body: TaskListV1 = { schema_version: "task_list.v1", availability: "available", tasks: scenario.tasks, error: null };
      return fulfillJson(route, body);
    }
    if (path === "/api/kovaak-runs") return fulfillJson(route, { runs: scenario.runs });
    const runMatch = /^\/api\/kovaak-runs\/(\d+)$/.exec(path);
    if (runMatch !== null) {
      const run = scenario.runs.find((candidate) => candidate.id === Number(runMatch[1]!));
      return run ? fulfillJson(route, runDetail(run)) : fulfillJson(route, { detail: "Not found" }, 404);
    }
    if (/^\/api\/kovaak-runs\/\d+\/analyze$/.test(path)) return fulfillJson(route, { session_id: 42 });
    if (path === "/api/sessions") return fulfillJson(route, { sessions: scenario.sessions });
    if (path === "/api/sessions/42/analysis-data") return fulfillJson(route, scenario.analysisData);
    if (path === "/api/sessions/42/analysis-data/family") return fulfillJson(route, scenario.analysisFamilyData);
    if (path === "/api/sessions/42/evidence-segments") return fulfillJson(route, scenario.evidenceSegments);
    if (path === "/api/sessions/42/video") {
      await fulfillVideo(route);
      return;
    }
    if (path === "/api/sessions/42/retry") return fulfillJson(route, { ...scenario.analysis, id: 43, status: "queued" });
    if (path === "/api/sessions/42") return fulfillJson(route, scenario.analysis);
    if (path === "/api/history/trends/accuracy") {
      const trend: HistoryTrend = { comparable: false, reason: "insufficient_records" };
      return fulfillJson(route, trend);
    }
    if (path === "/api/coach/context") return fulfillJson(route, COACH_CONTEXTS);
    if (path === "/api/coach/primary") return fulfillJson(route, COACH_PRIMARY);
    if (path === "/api/current-training" && method === "GET") return fulfillJson(route, scenario.currentTraining);
    if (path === "/api/coach/agent-runs" && method === "POST") {
      return fulfillJson(route, {
        schema_version: "coach_agent_run.v1",
        run_ref: "coach-run:1",
        parent_run_ref: null,
        attempt: 1,
        status: "running",
        phase: "text_generation",
        partial_text: "正在整理证据",
        error: null,
        contexts: COACH_CONTEXTS.contexts,
        events: [],
        created_at: NOW,
        started_at: NOW,
        finished_at: null,
      });
    }
    if (path === "/api/analyze" || path === "/api/desktop/analyze-paths") return fulfillJson(route, { session_id: 42 });
    await fulfillJson(route, { detail: { code: "fixture_route_missing", message: `${method} ${path}` } }, 501);
    */
  });
}

export async function installDesktopBridge(page: Page): Promise<void> {
  await page.addInitScript(({ origin }) => {
    type TauriFixtureWindow = Window & {
      isTauri: boolean;
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
        convertFileSrc: (path: string, protocol?: string) => string;
      };
    };
    const fixtureWindow = window as unknown as TauriFixtureWindow;
    fixtureWindow.isTauri = true;
    fixtureWindow.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "desktop_runtime_connection") return { baseUrl: origin, token: "task7-fixture-token" };
        if (command === "desktop_capture_coordinator_status" || command === "desktop_capture_coordinator_set_enabled") {
          return {
            enabled: true,
            phase: "recording",
            captureSessionId: "capture-fixture",
            kovaakProcessPresent: true,
            windowHandle: 1,
            reason: null,
            raw: { state: "recording", reason: null },
            video: { state: "buffering", reason: null },
          };
        }
        if (command === "scenario_open") {
          const scenarioProfileRef = args?.scenarioProfileRef;
          if (scenarioProfileRef !== "scenario:static.1wall_6targets_small@1") {
            return {
              status: "scenario_unmapped",
              scenario_profile_ref: null,
              display_name: null,
              message: "该训练项目没有可验证的 KovaaK 场景",
            };
          }
          return {
            status: "scenario_dispatched",
            scenario_profile_ref: scenarioProfileRef,
            display_name: "1wall 6targets small",
            message: "已请求打开 KovaaK，请确认目标场景已加载",
          };
        }
        if (command === "plugin:dialog|open") return "C:\\Task7Fixture\\selected.file";
        throw new Error(`Unhandled desktop fixture command: ${command}`);
      },
      convertFileSrc: (path, protocol = "asset") =>
        `http://${protocol}.localhost/${encodeURIComponent(path)}`,
    };
  }, { origin: "http://127.0.0.1:3106" });
}

export async function setThemePreference(page: Page, preference: "system" | "light" | "dark"): Promise<void> {
  await page.addInitScript((value) => {
    if (value === "system") localStorage.removeItem("aiming-cookie.ui.theme");
    else localStorage.setItem("aiming-cookie.ui.theme", value);
    localStorage.removeItem("aiming-cookie.ui.coach-open");
    localStorage.removeItem("aiming-cookie.ui.coach-first-analysis-opened");
  }, preference);
}
