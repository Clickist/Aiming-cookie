import type { Page, Route } from "@playwright/test";

import type {
  AnalysisResultV2,
  CalibrationProfileV1,
  CaptureStatusV1,
  CoachContextListV1,
  CoachPrimaryResponse,
  FrontendEvidenceSegmentsV1,
  HistoryTrend,
  IncompleteCaptureListV1,
  KovaaKRunItem,
  KovaaKRunListItem,
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

const NOW = "2026-07-25T06:32:00Z";

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
  readiness_state: "pending_analysis",
  analysis_count: 0,
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
  input_mode: "multimodal",
  kovaak_run_ref: "run:7",
  evidence: {
    sources: {
      stats: { source: "stats", availability: "available", alignment: "aligned" },
      performance: { source: "performance", availability: "available", alignment: "aligned" },
      raw_input: { source: "raw_input", availability: "available", alignment: "aligned" },
      mp4: { source: "mp4", availability: "available", alignment: "aligned" },
    },
    provenance: {},
    availability: { stats: "available", performance: "available", raw_input: "available", mp4: "available" },
    alignment: { status: "aligned" },
    warnings: [],
  },
  deterministic: {
    support_status: "supported",
    diagnosis: {
      profile: { archetype_id: "decel-wave", label: "减速波动型", confidence: 0.78, secondary_tags: ["远距离过冲"] },
      issues: [
        {
          signal: "停枪控制不稳",
          severity: "fix",
          root_causes: [
            { level: "symptom", text: "接近目标时减速不足" },
            { level: "physical", text: "反向修正出现较晚" },
            { level: "training", text: "需要稳定停枪节奏" },
          ],
          prescriptions: [{ scenario: "1wall5targets_pasu", reason: "练习减速" }],
          priority: 1,
          priority_reason: "远距离目标持续出现",
          metric_refs: ["sparc"],
          event_refs: ["event:corrective:800"],
        },
      ],
      summary: {},
      comparison: null,
      meta: {},
    },
    metrics: {
      sparc: {
        key: "sparc",
        value: -4.21,
        unit: "score",
        availability: "available",
        coverage: 1,
        classification: "deterministic",
        metric_version: "sparc.v1",
        limitations: [],
        provenance: { kind: "derived", sources: ["raw_input"] },
      },
      visual_validation: {
        key: "visual_validation",
        value: null,
        unit: "status",
        availability: "unavailable",
        coverage: 0,
        classification: "experimental",
        metric_version: "visual_validation.v1",
        limitations: ["visual_quality_limited"],
        provenance: { kind: "derived", sources: ["mp4"] },
      },
    },
    timeline: [
      { frame: null, time_s: 0.8, relative_ms: 800, type: "corrective", label: "反向修正", source: "raw_input" },
      { frame: null, time_s: 1.4, relative_ms: 1400, type: "kill", label: "命中", source: "performance" },
    ],
    limitations: [],
  },
  artifact_manifest: { schema_version: "artifact_manifest.v2", external_inputs: [], owned_outputs: [] },
  input_snapshot: {
    scenario: "1wall 6targets small",
    scenario_resolution: {
      schema_version: "scenario_resolution.v1",
      aim_family: "static_clicking",
      claim_ceiling: "family_specific",
      family_analyzer_dispatch: "allowed",
      limitations: [],
    },
    calibration: {
      cm_per_360: { value: 42, source: "stats" },
      fov: { value: 103, source: "stats" },
    },
    sources: {},
    trace: null,
  },
  created_at: NOW,
  completed_at: "2026-07-25T06:33:00Z",
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
    created_at: NOW,
    attempts: 1,
    max_attempts: 2,
    worker_id: null,
    started_at: NOW,
    finished_at: "2026-07-25T06:33:00Z",
    analysis_type: "flicking",
    input_mode: "multimodal",
    kovaak_run_id: 7,
    history: {
      analysis_ref: "analysis:42",
      run_ref: "run:7",
      scenario: "1wall 6targets small",
      input_mode: "multimodal",
      source_availability: { stats: "available", performance: "available", raw: "available", mp4: "available" },
      trace_quality: RUN_MULTIMODAL.trace_quality,
      visual_replay: {
        kind: "seekable_mp4",
        available: true,
        seekable: true,
        endpoint: "/api/sessions/42/video",
        artifact_ref: "analysis:42:video",
        reason: null,
      },
      diagnosis_locator: { analysis_ref: "analysis:42", section: "diagnosis" },
      evidence_refs: [],
    },
    ...overrides,
  };
}

export function partialAnalysisSession(): SessionStatus {
  const result: AnalysisResultV2 = {
    ...ANALYSIS_RESULT,
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
    history: {
      ...base.history!,
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
    run_ref: "run:7",
    status: "done",
    created_at: NOW,
    finished_at: "2026-07-25T06:33:00Z",
    attempts: 1,
    max_attempts: 2,
    llm_cost_cny: null,
    summary_label: "diagnosis",
    analysis_type: "flicking",
    input_mode: "multimodal",
    kovaak_run_id: 7,
    scenario: "1wall 6targets small",
    source_availability: { stats: "available", performance: "available", raw: "available", mp4: "available" },
    trace_quality: RUN_MULTIMODAL.trace_quality,
  },
];

export const EVIDENCE_SEGMENTS: FrontendEvidenceSegmentsV1 = {
  schema_version: "frontend_evidence_segments.v1",
  analysis_ref: "analysis:42",
  video_availability: "available",
  video_route: "/api/sessions/42/video",
  canonical_window_start_ms: 0,
  segments: [
    {
      segment_id: "segment:42:1",
      analysis_ref: "analysis:42",
      analyzer_ref: "flicking.v1",
      segment_kind: "corrective_window",
      start_ms: 500,
      end_ms: 1100,
      focus_start_ms: 760,
      focus_end_ms: 860,
      title_key: "反向修正窗口",
      rank_reason: "primary_issue",
      issue_refs: ["issue:0"],
      metric_refs: ["sparc"],
      event_refs: ["event:corrective:800"],
      available_channels: ["raw_input", "mp4"],
      source_coverage: 1,
      confidence: 0.82,
      limitations: [],
      playback: {
        schema_version: "evidence_segment_playback.v1",
        availability: "available",
        video_route: "/api/sessions/42/video",
        relative_start_ms: 500,
        relative_end_ms: 1100,
        limitations: [],
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
    label: "1wall 6targets small · 7月25日",
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

export interface ApiScenario {
  productState: ProductStateV1;
  runs: KovaaKRunListItem[];
  tasks: TaskDetailV1[];
  sessions: SessionListItem[];
  analysis: SessionStatus;
  evidenceSegments: FrontendEvidenceSegmentsV1;
  capture: CaptureStatusV1;
  providerStatus: ProviderProfileStatus;
  profiles: ProviderProfileListResponse;
  failures: Record<string, number>;
}

export function apiScenario(overrides: Partial<ApiScenario> = {}): ApiScenario {
  return {
    productState: PRODUCT_STATE,
    runs: [RUN_MULTIMODAL, RUN_NATIVE],
    tasks: TASKS,
    sessions: SESSION_LIST,
    analysis: analysisSession(),
    evidenceSegments: EVIDENCE_SEGMENTS,
    capture: CAPTURE_STATUS,
    providerStatus: READY_PROVIDER_STATUS,
    profiles: { profiles: [PROVIDER_PROFILE] },
    failures: {},
    ...overrides,
  };
}

function runDetail(run: KovaaKRunListItem): KovaaKRunItem {
  return { ...run, stats_source_ref: null, performance_source_ref: null, trace_artifact_ref: null, stats_summary: null, performance_summary: null };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function installApiFixtures(page: Page, scenario = apiScenario()): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const failureStatus = scenario.failures[`${method} ${path}`] ?? scenario.failures[path];
    if (failureStatus) return fulfillJson(route, { detail: { code: "fixture_unavailable", message: "Fixture service unavailable" } }, failureStatus);

    if (path === "/api/product-state") return fulfillJson(route, scenario.productState);
    if (path === "/api/product-state/onboarding") return fulfillJson(route, { ...scenario.productState, onboarding_completed: true });
    if (path === "/api/providers/catalog") return fulfillJson(route, PROVIDER_CATALOG);
    if (path === "/api/provider-auth/capabilities") return fulfillJson(route, PROVIDER_CAPABILITIES);
    if (path === "/api/provider-profiles/status") return fulfillJson(route, scenario.providerStatus);
    if (path === "/api/provider-profiles" && method === "GET") return fulfillJson(route, scenario.profiles);
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
    if (runMatch) {
      const run = scenario.runs.find((candidate) => candidate.id === Number(runMatch[1]));
      return run ? fulfillJson(route, runDetail(run)) : fulfillJson(route, { detail: "Not found" }, 404);
    }
    if (/^\/api\/kovaak-runs\/\d+\/analyze$/.test(path)) return fulfillJson(route, { session_id: 42 });
    if (path === "/api/sessions") return fulfillJson(route, { sessions: scenario.sessions });
    if (path === "/api/sessions/42/evidence-segments") return fulfillJson(route, scenario.evidenceSegments);
    if (path === "/api/sessions/42/video") {
      await route.fulfill({ status: 200, contentType: "video/mp4", body: Buffer.alloc(0) });
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
      invoke: async (command) => {
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
