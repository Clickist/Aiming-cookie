/**
 * Fetch-based API client for the Aiming Cookie backend.
 *
 * Browser requests stay relative to `/api` so Next.js can proxy them. The
 * Tauri WebView resolves its per-launch loopback base URL through the in-memory
 * `desktop_runtime_connection` command instead.
 */

import {
  getDesktopRuntimeConnection,
  isDesktopRuntime,
  resetDesktopRuntimeConnection,
} from "./desktop";
import type {
  AnalyzeResponse,
  BenchmarkRecord,
  BenchmarkRecordCreate,
  BenchmarkRecordListResponse,
  CalibrationValues,
  CalibrationProfileV1,
  CaptureStatusV1,
  ChatResponse,
  CoachPrimaryAttachResponse,
  CoachPrimaryMessageResponse,
  CoachPrimaryResponse,
  CoachSessionListResponse,
  CoachSessionOut,
  CoachRuntimeStatusResponse,
  CoachAgentRunV1,
  CoachAnalysisSoftStartRequestV1,
  CoachConfirmationV1,
  CoachContextListV1,
  CoachContextMutationV1,
  CoachContextRefV1,
  CustomProviderModelDiscoveryResponse,
  CustomProviderModelListRequest,
  CustomProviderModelListResponse,
  CustomProviderProtocol,
  CurrentTrainingV1,
  DeleteSessionResponse,
  FrontendAnalysisDataV1,
  FrontendAnalysisFamilyDataV1,
  HistoryTrend,
  IncompleteCaptureListV1,
  IncompleteCaptureRemovalV1,
  KovaaKAnalysisRequest,
  KovaaKConnectionDeleteResponseV1,
  KovaaKConnectionSaveRequestV1,
  KovaaKConnectionStatusV1,
  KovaaKScoreSyncRequestV1,
  KovaaKScoreSyncResultV1,
  KovaaKScoresV1,
  KovaaKRunItem,
  KovaaKRunListResponse,
  ProductStateV1,
  ProductReadinessV1,
  ProviderAuthCapabilitiesV1,
  ProviderAuthOperation,
  ProviderCatalogV1,
  ProviderProfile,
  ProviderProfileCreate,
  ProviderProfileListResponse,
  ProviderProfileStatus,
  RunEvidenceRemovalResponse,
  SessionStatus,
  SessionListResponse,
  StorageResponse,
  FrontendEvidenceSegmentsV1,
  TaskDetailV1,
  TaskListV1,
  Timeline,
} from "./types";

/** Browser API paths are intentionally relative so Next rewrites can proxy them. */
export const API_BASE = "";

/** Default X-User-Id placeholder (slice 1 dev shim; Clerk lands in slice 3). */
const DEFAULT_USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? "dev";
const DESKTOP_USER_ID = "desktop-local";
const MOCK_API_MODE = process.env.NEXT_PUBLIC_AIMING_COOKIE_API_MODE === "mock";

type RequestOptions = {
  desktopToken?: boolean;
  signal?: AbortSignal;
  userId?: string;
};

export class DesktopRuntimeUnavailableError extends Error {
  constructor() {
    super("Desktop runtime is temporarily unavailable");
    this.name = "DesktopRuntimeUnavailableError";
  }
}

function isReadRequest(init: RequestInit): boolean {
  const method = (init.method ?? "GET").toUpperCase();
  return method === "GET" || method === "HEAD";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function sameDesktopConnection(
  left: { baseUrl: string; token: string },
  right: { baseUrl: string; token: string },
): boolean {
  return left.baseUrl === right.baseUrl && left.token === right.token;
}

async function apiFetch(
  path: string,
  init: RequestInit = {},
  opts: RequestOptions = {},
): Promise<Response> {
  const desktop = isDesktopRuntime();
  if (!desktop) {
    if (opts.desktopToken && !MOCK_API_MODE) {
      throw new Error("Desktop-only API is unavailable in this browser session");
    }
    const headers = new Headers(init.headers);
    headers.set("X-User-Id", opts.userId ?? DEFAULT_USER_ID);
    return fetch(`${API_BASE}${path}`, { ...init, headers, signal: opts.signal });
  }

  const request = async (connection: Awaited<ReturnType<typeof getDesktopRuntimeConnection>>) => {
    const headers = new Headers(init.headers);
    headers.set("X-User-Id", DESKTOP_USER_ID);
    headers.set("X-Aiming-Cookie-Desktop-Token", connection.token);
    return fetch(`${connection.baseUrl}${path}`, { ...init, headers, signal: opts.signal });
  };
  const connection = await getDesktopRuntimeConnection();
  let response: Response;
  try {
    response = await request(connection);
  } catch (error) {
    if (isAbortError(error)) throw error;
    resetDesktopRuntimeConnection();
    if (!isReadRequest(init)) throw new DesktopRuntimeUnavailableError();
    const replacement = await getDesktopRuntimeConnection();
    if (sameDesktopConnection(connection, replacement)) {
      resetDesktopRuntimeConnection();
      throw new DesktopRuntimeUnavailableError();
    }
    try {
      return await request(replacement);
    } catch (retryError) {
      resetDesktopRuntimeConnection();
      if (isAbortError(retryError)) throw retryError;
      throw new DesktopRuntimeUnavailableError();
    }
  }
  if (response.status !== 401 || !isReadRequest(init)) return response;

  resetDesktopRuntimeConnection();
  const replacement = await getDesktopRuntimeConnection();
  if (sameDesktopConnection(connection, replacement)) {
    resetDesktopRuntimeConnection();
    throw new DesktopRuntimeUnavailableError();
  }
  try {
    return await request(replacement);
  } catch (error) {
    resetDesktopRuntimeConnection();
    if (isAbortError(error)) throw error;
    throw new DesktopRuntimeUnavailableError();
  }
}

export interface UploadOptions {
  /** Required Stats CSV (KovaaK's export). Backend hard-requires it. */
  csv: File;
  profileDefault?: CalibrationValues;
  manualOverride?: CalibrationValues;
  /** Override X-User-Id (defaults to env or "dev"). */
  userId?: string;
  signal?: AbortSignal;
}

export interface DesktopPathImportOptions {
  videoPath: string;
  csvPath: string;
  profileDefault?: CalibrationValues;
  manualOverride?: CalibrationValues;
  signal?: AbortSignal;
}

/** Browser multipart upload. The browser retains its existing file-size checks in the UI. */
export async function uploadVideo(
  video: File,
  opts: UploadOptions,
): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append("video", video);
  form.append("csv", opts.csv);
  appendCalibration(form, "profile_default", opts.profileDefault);
  appendCalibration(form, "manual_override", opts.manualOverride);

  const res = await apiFetch(
    "/api/analyze",
    { method: "POST", body: form },
    { signal: opts.signal, userId: opts.userId },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as AnalyzeResponse;
}

/** Desktop path import. Browser sessions cannot call this route. */
export async function importDesktopPaths(
  opts: DesktopPathImportOptions,
): Promise<AnalyzeResponse> {
  const res = await apiFetch(
    "/api/desktop/analyze-paths",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_path: opts.videoPath,
        csv_path: opts.csvPath,
        profile_default: opts.profileDefault,
        manual_override: opts.manualOverride,
      }),
    },
    { desktopToken: true, signal: opts.signal },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as AnalyzeResponse;
}

function appendCalibration(
  form: FormData,
  prefix: "profile_default" | "manual_override",
  values: CalibrationValues | undefined,
): void {
  if (typeof values?.cm_per_360 === "number") {
    form.append(`${prefix}_cm_per_360`, String(values.cm_per_360));
  }
  if (typeof values?.fov === "number") {
    form.append(`${prefix}_fov`, String(values.fov));
  }
}

/** Desktop storage listing. Browser sessions cannot call this route. */
export async function getStorage(
  opts: { signal?: AbortSignal } = {},
): Promise<StorageResponse> {
  const res = await apiFetch(
    "/api/storage",
    { method: "GET" },
    { desktopToken: true, signal: opts.signal },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as StorageResponse;
}

export async function getSession(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<SessionStatus> {
  const res = await apiFetch(`/api/sessions/${sessionId}`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as SessionStatus;
}

export async function listSessions(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<SessionListResponse> {
  const res = await apiFetch("/api/sessions", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as SessionListResponse;
}

/** Desktop-only Run index. This response is deliberately summary-only. */
export async function listKovaakRuns(
  opts: { signal?: AbortSignal } = {},
): Promise<KovaaKRunListResponse> {
  const res = await apiFetch(
    "/api/kovaak-runs",
    { method: "GET" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKRunListResponse;
}

/** Desktop-only Run detail. The server returns a path-free public projection. */
export async function getKovaakRun(
  runId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<KovaaKRunItem> {
  const res = await apiFetch(
    `/api/kovaak-runs/${runId}`,
    { method: "GET" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKRunItem;
}


/** Desktop-only submission from a path-free persisted Run. */
export async function analyzeKovaakRun(
  runId: number,
  request: KovaaKAnalysisRequest = {},
  opts: { idempotencyKey: string; signal?: AbortSignal },
): Promise<AnalyzeResponse> {
  const res = await apiFetch(
    `/api/kovaak-runs/${runId}/analyze`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": opts.idempotencyKey,
      },
      body: JSON.stringify(request),
    },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as AnalyzeResponse;
}

export async function getHistoryTrend(
  metricKey: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<HistoryTrend> {
  const res = await apiFetch(
    `/api/history/trends/${encodeURIComponent(metricKey)}`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as HistoryTrend;
}

export async function listBenchmarks(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<BenchmarkRecordListResponse> {
  const res = await apiFetch("/api/benchmarks", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as BenchmarkRecordListResponse;
}

export async function createBenchmark(
  record: BenchmarkRecordCreate,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<BenchmarkRecord> {
  const res = await apiFetch(
    "/api/benchmarks",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as BenchmarkRecord;
}

export async function deleteSession(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<DeleteSessionResponse> {
  const res = await apiFetch(`/api/sessions/${sessionId}`, { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as DeleteSessionResponse;
}

export async function retrySession(
  sessionId: number,
  opts: { idempotencyKey: string; signal?: AbortSignal },
): Promise<SessionStatus> {
  const res = await apiFetch(
    `/api/sessions/${sessionId}/retry`,
    {
      method: "POST",
      headers: { "Idempotency-Key": opts.idempotencyKey },
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as SessionStatus;
}

async function apiError(res: Response): Promise<Error> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body?.detail) detail = String(body.detail);
  } catch {
    // Not JSON — keep status text.
  }
  const err = new Error(detail);
  err.name = `ApiError_${res.status}`;
  return err;
}

export async function getChatHistory(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<ChatResponse> {
  const res = await apiFetch(`/api/sessions/${sessionId}/chat`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ChatResponse;
}

export async function sendChatMessage(
  sessionId: number,
  message: string,
  pinnedFrameSec?: number,
  opts: { signal?: AbortSignal } = {},
): Promise<ChatResponse> {
  const body: { message: string; pinned_frame_sec?: number } = { message };
  if (typeof pinnedFrameSec === "number" && Number.isFinite(pinnedFrameSec)) {
    body.pinned_frame_sec = pinnedFrameSec;
  }
  const res = await apiFetch(
    `/api/sessions/${sessionId}/chat`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ChatResponse;
}

export async function getTimeline(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<Timeline> {
  const res = await apiFetch(`/api/sessions/${sessionId}/timeline`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as Timeline;
}

/** Browser-only video URL. Desktop uses the Tauri asset protocol instead. */
export function getVideoUrl(sessionId: number): string {
  return `/api/sessions/${sessionId}/video`;
}

/** Browser media elements cannot attach the owner header, so fetch bytes before playback. */
export async function getAnalysisVideoBlob(sessionId: number): Promise<Blob> {
  const res = await apiFetch(getVideoUrl(sessionId), { method: "GET" });
  if (!res.ok) throw await apiError(res);
  return res.blob();
}

export async function getCoachRuntimeStatus(
  opts: { signal?: AbortSignal } = {},
): Promise<CoachRuntimeStatusResponse> {
  const res = await apiFetch("/api/coach/runtime-status", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachRuntimeStatusResponse;
}

export async function getCoachPrimary(
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachPrimaryResponse> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(`/api/coach/primary${query}`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryResponse;
}

export async function attachCoachPrimaryAnalysis(
  analysisSessionId: number,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachPrimaryAttachResponse> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(
    `/api/coach/primary/attach${query}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ analysis_session_id: analysisSessionId }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryAttachResponse;
}

export async function postCoachPrimaryMessage(
  content: string,
  analysisSessionId?: number,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachPrimaryMessageResponse> {
  const body: { content: string; analysis_session_id?: number } = { content };
  if (analysisSessionId !== undefined) {
    body.analysis_session_id = analysisSessionId;
  }
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(
    `/api/coach/primary/messages${query}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryMessageResponse;
}

export async function getAnalysisEvidenceSegments(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<FrontendEvidenceSegmentsV1> {
  const res = await apiFetch(
    `/api/sessions/${sessionId}/evidence-segments`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as FrontendEvidenceSegmentsV1;
}

export async function getAnalysisData(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<FrontendAnalysisDataV1> {
  const res = await apiFetch(
    `/api/sessions/${sessionId}/analysis-data`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as FrontendAnalysisDataV1;
}

export async function getAnalysisFamilyData(
  sessionId: number,
  pagination: { limit?: number; offset?: number } = {},
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<FrontendAnalysisFamilyDataV1> {
  const params = new URLSearchParams({
    limit: String(pagination.limit ?? 50),
    offset: String(pagination.offset ?? 0),
  });
  const res = await apiFetch(
    `/api/sessions/${sessionId}/analysis-data/family?${params.toString()}`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as FrontendAnalysisFamilyDataV1;
}

export async function getCurrentTraining(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CurrentTrainingV1> {
  const res = await apiFetch("/api/current-training", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CurrentTrainingV1;
}

export async function syncKovaaKScores(
  body: KovaaKScoreSyncRequestV1,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKScoreSyncResultV1> {
  const res = await apiFetch(
    "/api/benchmarks/sync/kovaaks",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKScoreSyncResultV1;
}

export async function getKovaaKScores(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKScoresV1> {
  const res = await apiFetch("/api/kovaak-scores", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKScoresV1;
}

export async function getKovaaKConnection(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKConnectionStatusV1> {
  const res = await apiFetch("/api/kovaak-connection", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKConnectionStatusV1;
}

export async function saveKovaaKConnection(
  body: KovaaKConnectionSaveRequestV1,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKConnectionStatusV1> {
  const res = await apiFetch(
    "/api/kovaak-connection",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKConnectionStatusV1;
}

export async function deleteKovaaKConnection(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKConnectionDeleteResponseV1> {
  const res = await apiFetch("/api/kovaak-connection", { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKConnectionDeleteResponseV1;
}

export async function refreshKovaaKConnection(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<KovaaKScoreSyncResultV1> {
  const res = await apiFetch(
    "/api/kovaak-connection/refresh",
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKScoreSyncResultV1;
}

export async function getHistorySessions(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<SessionListResponse> {
  return listSessions(opts);
}

export async function getHistoryRun(
  runId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<KovaaKRunItem> {
  return getKovaakRun(runId, opts);
}

export async function getHistoryAnalysisDetail(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<SessionStatus> {
  return getSession(sessionId, opts);
}

export async function getProductState(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProductStateV1> {
  const res = await apiFetch("/api/product-state", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProductStateV1;
}

export async function getProductReadiness(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProductReadinessV1> {
  const res = await apiFetch("/api/product-readiness", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProductReadinessV1;
}

export async function acknowledgeCoachGuidance(
  payload: { run_ref: string; intent_id: string; outcome: "completed" | "cancelled" | "failed" | "timed_out" },
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<{ schema_version: "guidance_ack_response.v1"; run_ref: string; intent_id: string; outcome: typeof payload.outcome; next_intent?: import("@/lib/types").GuidanceIntentV1 | null; terminal_state?: Record<string, unknown> | null }> {
  const res = await apiFetch("/api/coach/guidance/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schema_version: "guidance_ack_request.v1", ...payload }),
  }, opts);
  if (!res.ok) throw await apiError(res);
  return await res.json();
}

export async function completeOnboarding(
  completionKind: "connected" | "skipped",
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProductStateV1> {
  const res = await apiFetch(
    "/api/product-state/onboarding",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ completed: true, completion_kind: completionKind }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProductStateV1;
}

export async function getCaptureStatus(
  opts: { signal?: AbortSignal } = {},
): Promise<CaptureStatusV1> {
  const res = await apiFetch(
    "/api/capture-status",
    { method: "GET" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CaptureStatusV1;
}

export async function listTasks(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<TaskListV1> {
  const res = await apiFetch("/api/tasks", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as TaskListV1;
}

export async function getTask(
  taskRef: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<TaskDetailV1> {
  const res = await apiFetch(
    `/api/tasks/${encodeURIComponent(taskRef)}`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as TaskDetailV1;
}

export async function getProviderCatalog(
  opts: { signal?: AbortSignal } = {},
): Promise<ProviderCatalogV1> {
  const res = await apiFetch("/api/providers/catalog", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderCatalogV1;
}

export async function getProviderAuthCapabilities(
  opts: { signal?: AbortSignal } = {},
): Promise<ProviderAuthCapabilitiesV1> {
  const res = await apiFetch("/api/provider-auth/capabilities", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthCapabilitiesV1;
}

export async function listProviderProfiles(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileListResponse> {
  const res = await apiFetch("/api/provider-profiles", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileListResponse;
}

export async function getDefaultProviderStatus(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileStatus> {
  const res = await apiFetch("/api/provider-profiles/status", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileStatus;
}

export async function listCustomProviderModels(
  input: CustomProviderModelListRequest,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CustomProviderModelListResponse> {
  const res = await apiFetch(
    "/api/provider-profiles/custom/models",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CustomProviderModelListResponse;
}

export async function discoverCustomProviderModels(
  input: Omit<CustomProviderModelListRequest, "protocol">,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CustomProviderModelDiscoveryResponse> {
  const protocols: CustomProviderProtocol[] = ["openai-completions", "anthropic-messages"];
  const attempts = await Promise.allSettled(
    protocols.map((protocol) => listCustomProviderModels({ ...input, protocol }, opts)),
  );
  const successful = attempts.flatMap((attempt, index) => (
    attempt.status === "fulfilled"
      ? [{ ...attempt.value, protocol: protocols[index] }]
      : []
  ));
  if (!successful.length) throw new Error("Custom Provider protocol discovery failed");
  return successful.find((attempt) => attempt.models.length > 0) ?? successful[0];
}

export async function createProviderProfile(
  profile: ProviderProfileCreate,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetch(
    "/api/provider-profiles",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

export async function testProviderProfile(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileStatus> {
  const res = await apiFetch(
    `/api/provider-profiles/${profileId}/test`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileStatus;
}

export async function authorizeProviderProfile(
  profileId: number,
  mode: "api_key" | "oauth",
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderAuthOperation> {
  const res = await apiFetch(
    `/api/provider-profiles/${profileId}/auth/authorize`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthOperation;
}

export async function getProviderAuthOperation(
  operationId: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderAuthOperation> {
  const res = await apiFetch(
    `/api/provider-auth-operations/${encodeURIComponent(operationId)}`,
    { method: "GET" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthOperation;
}

export async function submitProviderAuthInput(
  operationId: string,
  promptId: string,
  value: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderAuthOperation> {
  const res = await apiFetch(
    `/api/provider-auth-operations/${encodeURIComponent(operationId)}/input`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_id: promptId, value }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthOperation;
}

export async function cancelProviderAuthOperation(
  operationId: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderAuthOperation> {
  const res = await apiFetch(
    `/api/provider-auth-operations/${encodeURIComponent(operationId)}/cancel`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthOperation;
}

export async function setProviderApiKey(
  profileId: number,
  apiKey: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetch(
    `/api/provider-profiles/${profileId}/auth/api-key`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

export async function deleteProviderCredential(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetch(
    `/api/provider-profiles/${profileId}/auth/credential`,
    { method: "DELETE" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

export async function setDefaultProviderProfile(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetch(
    `/api/provider-profiles/${profileId}/default`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

export async function deleteProviderProfile(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<{ deleted: boolean; id: number }> {
  const res = await apiFetch(`/api/provider-profiles/${profileId}`, { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as { deleted: boolean; id: number };
}

export async function getCoachContexts(
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachContextListV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(`/api/coach/context${query}`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachContextListV1;
}

export async function listCoachSessions(
  opts: { query?: string; includeArchived?: boolean; signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionListResponse> {
  const params = new URLSearchParams();
  if (opts.query?.trim()) params.set("q", opts.query.trim());
  if (opts.includeArchived) params.set("include_archived", "true");
  const query = params.toString();
  const res = await apiFetch(`/api/coach/sessions${query ? `?${query}` : ""}`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionListResponse;
}

export async function createCoachSession(
  title?: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetch(
    "/api/coach/sessions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(title?.trim() ? { title: title.trim() } : {}),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

export async function updateCoachSession(
  sessionId: number,
  update: { title?: string; status?: "archived" },
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetch(
    `/api/coach/sessions/${sessionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

export async function deleteCoachSession(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetch(`/api/coach/sessions/${sessionId}`, { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

export async function attachCoachContext(
  context: {
    kind: CoachContextRefV1["kind"];
    analysis_ref: string;
    target_ref?: string;
    start_ms?: number;
    end_ms?: number;
    comparison_analysis_ref?: string;
  },
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachContextMutationV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(
    `/api/coach/context/attach${query}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: "coach_context_attach.v1", ...context }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachContextMutationV1;
}

export async function detachCoachContext(
  contextRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachContextMutationV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(
    `/api/coach/context/${encodeURIComponent(contextRef)}/detach${query}`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachContextMutationV1;
}

export async function createCoachAgentRun(
  content: string,
  contextRefs: string[],
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const res = await apiFetch(
    "/api/coach/agent-runs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "coach_agent_run_request.v1",
        content,
        context_refs: contextRefs,
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
      }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function startCoachAnalysisSoftStart(
  analysisId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<CoachAgentRunV1> {
  const body: CoachAnalysisSoftStartRequestV1 = {
    schema_version: "coach_analysis_soft_start_request.v1",
    analysis_session_id: analysisId,
  };
  const res = await apiFetch(
    "/api/coach/analysis-soft-start",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function getCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(`/api/coach/agent-runs/${encodeURIComponent(runRef)}${query}`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function stopCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(`/api/coach/agent-runs/${encodeURIComponent(runRef)}/stop${query}`, { method: "POST" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function retryCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(`/api/coach/agent-runs/${encodeURIComponent(runRef)}/retry${query}`, { method: "POST" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function decideCoachConfirmation(
  confirmationRef: string,
  decision: "confirm" | "reject",
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachConfirmationV1> {
  const query = opts.sessionId ? `?session_id=${encodeURIComponent(opts.sessionId)}` : "";
  const res = await apiFetch(
    `/api/coach/confirmations/${encodeURIComponent(confirmationRef)}/decision${query}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: "coach_confirmation_decision.v1", decision }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachConfirmationV1;
}

export async function getCalibrationProfile(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CalibrationProfileV1> {
  const res = await apiFetch("/api/calibration-profile", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CalibrationProfileV1;
}

export async function saveCalibrationProfile(
  values: CalibrationValues,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CalibrationProfileV1> {
  const res = await apiFetch(
    "/api/calibration-profile",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: "calibration_profile_update.v1", ...values }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CalibrationProfileV1;
}

export async function deleteCalibrationProfile(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CalibrationProfileV1> {
  const res = await apiFetch("/api/calibration-profile", { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CalibrationProfileV1;
}

export async function listIncompleteCaptures(
  opts: { signal?: AbortSignal } = {},
): Promise<IncompleteCaptureListV1> {
  const res = await apiFetch("/api/storage/incomplete", { method: "GET" }, { ...opts, desktopToken: true });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as IncompleteCaptureListV1;
}

export async function removeIncompleteCapture(
  itemRef: string,
  opts: { signal?: AbortSignal } = {},
): Promise<IncompleteCaptureRemovalV1> {
  const res = await apiFetch(
    `/api/storage/incomplete/${encodeURIComponent(itemRef)}`,
    { method: "DELETE" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as IncompleteCaptureRemovalV1;
}

export async function removeRunEvidence(
  runId: number,
  kind: "video" | "raw",
  opts: { signal?: AbortSignal } = {},
): Promise<RunEvidenceRemovalResponse> {
  const res = await apiFetch(
    `/api/kovaak-runs/${runId}/evidence/${kind}`,
    { method: "DELETE" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as RunEvidenceRemovalResponse;
}
