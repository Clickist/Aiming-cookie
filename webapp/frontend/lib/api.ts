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
import type { IntroSessionCreated, IntroSessionStatus } from "./intro-session";
import type {
  AnalyzeResponse,
  BenchmarkRecord,
  BenchmarkRecordCreate,
  BenchmarkRecordListResponse,
  CalibrationValues,
  CalibrationProfileV1,
  CaptureStatusV1,
  CoachSessionListResponse,
  CoachSessionOut,
  CoachRuntimeStatusResponse,
  CoachAgentRunQueueV1,
  CoachAgentRunV1,
  CustomProviderModelDiscoveryResponse,
  CustomProviderModelListRequest,
  CustomProviderModelListResponse,
  CustomProviderProtocol,
  CurrentTrainingV1,
  DeleteSessionResponse,
  ExternalRunListResponseV1,
  OfficialRelayBalance,
  ExternalTelemetryConfigV1,
  ExternalTelemetryWatchRootUpdateV1,
  FrontendAnalysisDataV1,
  FrontendAnalysisFamilyDataV1,
  HistoryTrend,
  IncompleteCaptureListV1,
  IncompleteCaptureRemovalV1,
  KovaaKAnalysisRequest,
  KovaaKConnectionDeleteResponseV1,
  KovaaKConnectionSaveRequestV1,
  KovaaKConnectionStatusV1,
  KovaaKLocalDirectoriesUpdateV1,
  KovaaKLocalDirectoriesV1,
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
  ProviderProfileStatusDetail,
  ProviderReasoningEffort,
  RunEvidenceRemovalResponse,
  SessionStatus,
  SessionListResponse,
  StorageResponse,
  FrontendEvidenceSegmentsV1,
  StoredCustomProviderModelListRequest,
  TaskDetailV1,
  TaskListV1,
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

async function apiFetchSidecar(
  path: string,
  init: RequestInit = {},
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<Response> {
  if (!isDesktopRuntime()) {
    // Browser/dev sessions have no sidecar — fall back to the Python backend.
    // `/v1/*` 与 Coach 原生前缀 `/coach/*` 都映射到后端 `/api/coach/*`。
    return apiFetch(path.replace(/^\/(?:v1|coach)\//, "/api/coach/"), init, opts);
  }
  const request = async (connection: Awaited<ReturnType<typeof getDesktopRuntimeConnection>>) => {
    const headers = new Headers(init.headers);
    headers.set("X-User-Id", DESKTOP_USER_ID);
    return fetch(`${connection.sidecarUrl}${path}`, {
      ...init,
      headers,
      signal: opts.signal,
    });
  };
  const connection = await getDesktopRuntimeConnection();
  try {
    return await request(connection);
  } catch (error) {
    if (isAbortError(error)) throw error;
    resetDesktopRuntimeConnection();
    if (!isReadRequest(init)) throw new DesktopRuntimeUnavailableError();
    const replacement = await getDesktopRuntimeConnection();
    if (replacement.sidecarUrl === connection.sidecarUrl) {
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

/** Desktop-only local KovaaK directory configuration. */
export async function getKovaaKLocalDirectories(
  opts: { signal?: AbortSignal } = {},
): Promise<KovaaKLocalDirectoriesV1> {
  const res = await apiFetch(
    "/api/kovaak-local-directories",
    { method: "GET" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKLocalDirectoriesV1;
}

/** Desktop-only local directory update activates the watcher when the runtime is available. */
export async function saveKovaaKLocalDirectories(
  body: KovaaKLocalDirectoriesUpdateV1,
  opts: { signal?: AbortSignal } = {},
): Promise<KovaaKLocalDirectoriesV1> {
  const res = await apiFetch(
    "/api/kovaak-local-directories",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as KovaaKLocalDirectoriesV1;
}

/** Desktop-only external telemetry (cleaned KovaaK rounds) watch-root state. */
export async function getExternalTelemetry(
  opts: { signal?: AbortSignal } = {},
): Promise<ExternalTelemetryConfigV1> {
  const res = await apiFetch(
    "/api/external-telemetry",
    { method: "GET" },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ExternalTelemetryConfigV1;
}

/** Desktop-only external telemetry watch-root update (hot reconfigures the watcher). */
export async function saveExternalTelemetryWatchRoot(
  body: ExternalTelemetryWatchRootUpdateV1,
  opts: { signal?: AbortSignal } = {},
): Promise<ExternalTelemetryConfigV1> {
  const res = await apiFetch(
    "/api/external-telemetry",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ExternalTelemetryConfigV1;
}

/** Imported ExternalTelemetryRun list (shallow, newest first). */
export async function listExternalRuns(
  opts: { limit?: number; signal?: AbortSignal } = {},
): Promise<ExternalRunListResponseV1> {
  const query = opts.limit ? `?limit=${opts.limit}` : "";
  const res = await apiFetch(
    `/api/external-runs${query}`,
    { method: "GET" },
    { signal: opts.signal, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ExternalRunListResponseV1;
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
    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (body?.detail && typeof body.detail === "object") {
      const structured = body.detail as { message?: unknown; code?: unknown };
      if (typeof structured.message === "string" && structured.message.trim()) {
        detail = structured.message;
      } else if (typeof structured.code === "string" && structured.code.trim()) {
        detail = structured.code;
      }
    }
  } catch {
    // Not JSON — keep status text.
  }
  const err = new Error(detail);
  err.name = `ApiError_${res.status}`;
  return err;
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

export async function deleteCurrentTraining(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<void> {
  const res = await apiFetch("/api/current-training", { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
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

export async function completeOnboarding(
  completionKind: "connected",
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProductStateV1> {
  const res = await apiFetch(
    "/api/product-state/onboarding",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ completed: true, completion_kind: completionKind }),
    },
    { ...opts, desktopToken: true },
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
  const res = await apiFetchSidecar("/v1/providers/catalog", { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderCatalogV1;
}

export async function getProviderAuthCapabilities(
  opts: { signal?: AbortSignal } = {},
): Promise<ProviderAuthCapabilitiesV1> {
  const res = await apiFetchSidecar("/v1/provider-auth/capabilities", { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderAuthCapabilitiesV1;
}

export async function listProviderProfiles(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileListResponse> {
  const res = await apiFetchSidecar("/v1/provider-profiles", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileListResponse;
}

export async function getDefaultProviderStatus(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileStatus> {
  const res = await apiFetchSidecar("/v1/provider-profiles/status", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileStatus;
}

/** 官方中转档余额（点点 0912 拍板）：sidecar 用存档 key 查 new-api 计费端点，
 * 前端只拿算好的数字。401=Key 无效；502=站点不可达。 */
export async function getOfficialRelayBalance(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<OfficialRelayBalance> {
  const res = await apiFetchSidecar("/v1/provider-profiles/official/balance", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as OfficialRelayBalance;
}

/** 眼睛按钮的「显示 Key」（点点 0912 拍板）：测试阶段 key 对用户可见。 */
export async function getProviderCredential(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<{ api_key: string }> {
  const res = await apiFetchSidecar(`/v1/provider-profiles/${profileId}/auth/credential`, { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as { api_key: string };
}

export async function listCustomProviderModels(
  input: CustomProviderModelListRequest,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CustomProviderModelListResponse> {
  const res = await apiFetchSidecar(
    "/v1/provider-profiles/custom/models",
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

/**
 * 已存 custom 档的模型发现（Coach 模型菜单，点点 09-08 拍板）：只传
 * profile_id，key 不出 sidecar，由后端读档内凭证就地拉取该 Provider 的
 * /models 列表。
 */
export async function listStoredCustomProviderModels(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CustomProviderModelListResponse> {
  const res = await apiFetchSidecar(
    "/v1/provider-profiles/custom/models",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId } satisfies StoredCustomProviderModelListRequest),
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
  const res = await apiFetchSidecar(
    "/v1/provider-profiles",
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

export async function updateProviderProfile(
  profileId: number,
  profile: ProviderProfileCreate,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

/**
 * Switch the default profile's model within the current Provider. The sidecar
 * validates the model (builtin: must be in the pinned catalog) before
 * persisting; the response carries the resolved model with its display name.
 *
 * `reasoningEffort` 走同一路由调整档级思考力度：缺省 = 沿用已存值（切模型
 * 不动力度），null = 清除回「未设置」，否则必须落在五档枚举内。选择对下
 * 一段回复生效。
 */
export async function switchProviderModel(
  modelId: string,
  opts: { signal?: AbortSignal; userId?: string; reasoningEffort?: ProviderReasoningEffort | null } = {},
): Promise<ProviderProfileStatusDetail> {
  const res = await apiFetchSidecar(
    "/v1/provider-profiles/model",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "coach_provider_model_switch.v1",
        model_id: modelId,
        ...(opts.reasoningEffort !== undefined ? { reasoning_effort: opts.reasoningEffort } : {}),
      }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileStatusDetail;
}

export async function testProviderProfile(
  profileId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileStatus> {
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/test`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfileStatus;
}

/**
 * Dry-run the same connectivity check as `testProviderProfile` against a
 * complete but not-yet-persisted candidate profile (Raycast-style verify
 * before save). The sidecar must not touch its stored profiles; the response
 * reuses the regular status projection with `profile_id` fixed at null.
 */
export async function testProviderProfileDraft(
  profile: ProviderProfileCreate,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfileStatus> {
  const res = await apiFetchSidecar(
    "/v1/provider-profiles/test",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    },
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
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/auth/authorize`,
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

/** Persist the credential from a completed profile-scoped auth operation. */
export async function takeProviderAuthResult(
  profileId: number,
  operationId: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderProfile> {
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/auth/take-result`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation_id: operationId }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ProviderProfile;
}

export async function getProviderAuthOperation(
  operationId: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<ProviderAuthOperation> {
  const res = await apiFetchSidecar(
    `/v1/auth/operations/${encodeURIComponent(operationId)}`,
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
  const res = await apiFetchSidecar(
    `/v1/auth/operations/${encodeURIComponent(operationId)}/input`,
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
  const res = await apiFetchSidecar(
    `/v1/auth/operations/${encodeURIComponent(operationId)}`,
    { method: "DELETE" },
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
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/auth/api-key`,
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
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/auth/credential`,
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
  const res = await apiFetchSidecar(
    `/v1/provider-profiles/${profileId}/default`,
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
  const res = await apiFetchSidecar(`/v1/provider-profiles/${profileId}`, { method: "DELETE" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as { deleted: boolean; id: number };
}

export async function listCoachSessions(
  opts: { includeArchived?: boolean; signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionListResponse> {
  const params = new URLSearchParams();
  if (opts.includeArchived) params.set("include_archived", "true");
  const query = params.toString();
  const res = await apiFetchSidecar(`/v1/sessions${query ? `?${query}` : ""}`, { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionListResponse;
}

export async function createCoachSession(
  title?: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetchSidecar(
    "/v1/sessions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(title?.trim() ? { title: title.trim() } : {}),
    },
    { signal: opts.signal },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

// ── 开场分析（Intro Session，PRD §6.1.1）────────────────────────────────
//
// sidecar 契约（coach-runtime 并行实现中，尚未进入后端 OpenAPI，故此处手写
// 局部类型）：GET /coach/intro-session → 首启创建状态；POST 幂等创建/取回会话，
// 同时由 sidecar 发出首条开场消息（kickoff run）。前端只触发与呈现，标题与
// 内容全部由 sidecar 决定。契约稳定后并入 types.ts。
// 类型定义在 lib/intro-session.ts（触发逻辑与 api 客户端共用同一来源）。

/** 查询首启「开场分析」是否已创建；只读，供挂载时的触发守卫判断。 */
export async function getIntroSession(
  opts: { signal?: AbortSignal } = {},
): Promise<IntroSessionStatus> {
  const res = await apiFetchSidecar("/coach/intro-session", { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as IntroSessionStatus;
}

/** 幂等创建（或取回既有）「开场分析」会话；标题由 sidecar 定，前端不传。 */
export async function createIntroSession(
  opts: { signal?: AbortSignal } = {},
): Promise<IntroSessionCreated> {
  const res = await apiFetchSidecar("/coach/intro-session", { method: "POST" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as IntroSessionCreated;
}

export async function updateCoachSession(
  sessionId: number,
  update: { title?: string; status?: "archived" },
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetchSidecar(
    `/v1/sessions/${sessionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
    { signal: opts.signal },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

export async function deleteCoachSession(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionOut> {
  const res = await apiFetchSidecar(`/v1/sessions/${sessionId}`, { method: "DELETE" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionOut;
}

export interface CoachSessionDetail extends CoachSessionOut {
  messages: import("./types").CoachThreadMessageOut[];
}

export async function getCoachSession(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<CoachSessionDetail> {
  const res = await apiFetchSidecar(`/v1/sessions/${sessionId}`, { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionDetail;
}

export async function createCoachAgentRun(
  content: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number; contextRefs?: string[] } = {},
): Promise<CoachAgentRunV1> {
  const res = await apiFetchSidecar(
    "/v1/agent-runs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "coach_agent_run_request.v1",
        content,
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
        // 结构化分析引用（引用菜单选择）：消息文本不再携带 analysis:N 机器码。
        ...(opts.contextRefs?.length ? { context_refs: opts.contextRefs } : {}),
      }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function getCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const res = await apiFetchSidecar(`/v1/agent-runs/${encodeURIComponent(runRef)}`, { method: "GET" }, { signal: opts.signal });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

/**
 * Resolve the sidecar SSE stream URL for a live agent run. EventSource cannot
 * attach custom headers, so this relies on the sidecar's default loopback
 * owner (desktop-local) matching the desktop runtime's X-User-Id.
 */
export async function getCoachAgentRunStreamUrl(runRef: string): Promise<string> {
  const connection = await getDesktopRuntimeConnection();
  return `${connection.sidecarUrl}/v1/agent-runs/${encodeURIComponent(runRef)}/stream`;
}

export async function stopCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const res = await apiFetchSidecar(
    `/v1/agent-runs/${encodeURIComponent(runRef)}/stop`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

export async function retryCoachAgentRun(
  runRef: string,
  opts: { signal?: AbortSignal; userId?: string; sessionId?: number } = {},
): Promise<CoachAgentRunV1> {
  const res = await apiFetchSidecar(
    `/v1/agent-runs/${encodeURIComponent(runRef)}/retry`,
    { method: "POST" },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunV1;
}

/**
 * Composer 排队/转向透传（纯转发，零持久化）。steer 在运行中注入 pi 会话，
 * followUp 排到停止边界之后；运行中 send 不再静默丢失。409
 * run_not_steerable / 404 语义由 sidecar 显式给出，批 5 UI 据此编排。
 */
export async function steerCoachAgentRun(
  runRef: string,
  text: string,
  opts: { signal?: AbortSignal; userId?: string; drainMode?: "all" | "one-at-a-time" } = {},
): Promise<CoachAgentRunQueueV1> {
  const res = await apiFetchSidecar(
    `/v1/agent-runs/${encodeURIComponent(runRef)}/steer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(opts.drainMode ? { drain_mode: opts.drainMode } : {}),
        text,
      }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunQueueV1;
}

export async function followUpCoachAgentRun(
  runRef: string,
  text: string,
  opts: { signal?: AbortSignal; userId?: string; drainMode?: "all" | "one-at-a-time" } = {},
): Promise<CoachAgentRunQueueV1> {
  const res = await apiFetchSidecar(
    `/v1/agent-runs/${encodeURIComponent(runRef)}/follow-up`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(opts.drainMode ? { drain_mode: opts.drainMode } : {}),
        text,
      }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunQueueV1;
}

/**
 * next_turn 队列（审计#13）：排进当前 run 的下一轮开头，run_id 不变。
 * 供 Composer 在"回复刚结束、run 尚未销毁"的窗口使用，避免被迫发新 run。
 */
export async function nextTurnCoachAgentRun(
  runRef: string,
  text: string,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachAgentRunQueueV1> {
  const res = await apiFetchSidecar(
    `/v1/agent-runs/${encodeURIComponent(runRef)}/next-turn`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachAgentRunQueueV1;
}

/**
 * 编辑重发截断（digests §11 item 7 截断派）：保留会话前 keepMessages 条可见
 * 消息并丢弃其后历史。409 session_busy 表示仍有活跃 run 在写该会话，
 * 前端须等运行结束后重试。
 */
export async function truncateCoachSession(
  sessionId: number,
  keepMessages: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachSessionDetail> {
  const res = await apiFetchSidecar(
    `/v1/sessions/${encodeURIComponent(String(sessionId))}/truncate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep_messages: keepMessages }),
    },
    opts,
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachSessionDetail;
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

/** 「打开文件位置」：只发条目 id/kind；本地路径由后端解析，绝不下发前端。 */
export async function revealStorageItem(
  request: {
    kind: "run_video" | "run_raw" | "incomplete_capture";
    run_id?: number;
    item_ref?: string;
  },
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const res = await apiFetch(
    "/api/storage/reveal",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
    { ...opts, desktopToken: true },
  );
  if (!res.ok) throw await apiError(res);
}
