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
} from "./desktop";
import type {
  AnalyzeResponse,
  ChatResponse,
  CoachPrimaryAttachResponse,
  CoachPrimaryMessageResponse,
  CoachPrimaryResponse,
  CoachRuntimeStatusResponse,
  DeleteSessionResponse,
  SessionListResponse,
  SessionStatus,
  StorageResponse,
  Timeline,
} from "./types";

/** Browser API paths are intentionally relative so Next rewrites can proxy them. */
export const API_BASE = "";

/** Default X-User-Id placeholder (slice 1 dev shim; Clerk lands in slice 3). */
const DEFAULT_USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? "dev";
const DESKTOP_USER_ID = "desktop-local";

type RequestOptions = {
  desktopToken?: boolean;
  signal?: AbortSignal;
  userId?: string;
};

async function apiFetch(
  path: string,
  init: RequestInit = {},
  opts: RequestOptions = {},
): Promise<Response> {
  const desktop = isDesktopRuntime();
  const connection = desktop ? await getDesktopRuntimeConnection() : null;
  const headers = new Headers(init.headers);

  headers.set(
    "X-User-Id",
    desktop ? DESKTOP_USER_ID : (opts.userId ?? DEFAULT_USER_ID),
  );
  if (desktop && connection) {
    headers.set("X-Aiming-Cookie-Desktop-Token", connection.token);
  } else if (opts.desktopToken) {
    throw new Error("Desktop-only API is unavailable in this browser session");
  }

  return fetch(`${connection?.baseUrl ?? API_BASE}${path}`, {
    ...init,
    headers,
    signal: opts.signal,
  });
}

export interface UploadOptions {
  /** Required Stats CSV (KovaaK's export). Backend hard-requires it. */
  csv: File;
  cmPer360?: number;
  fov?: number;
  /** Override X-User-Id (defaults to env or "dev"). */
  userId?: string;
  signal?: AbortSignal;
}

export interface DesktopPathImportOptions {
  videoPath: string;
  csvPath: string;
  cmPer360?: number;
  fov?: number;
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
  if (opts.cmPer360 !== undefined) {
    form.append("cm_per_360", String(opts.cmPer360));
  }
  if (opts.fov !== undefined) {
    form.append("fov", String(opts.fov));
  }

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
        cm_per_360: opts.cmPer360,
        fov: opts.fov,
      }),
    },
    { desktopToken: true, signal: opts.signal },
  );
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as AnalyzeResponse;
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
  opts: { signal?: AbortSignal } = {},
): Promise<SessionStatus> {
  const res = await apiFetch(
    `/api/sessions/${sessionId}/retry`,
    { method: "POST" },
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

export async function getCoachRuntimeStatus(
  opts: { signal?: AbortSignal } = {},
): Promise<CoachRuntimeStatusResponse> {
  const res = await apiFetch("/api/coach/runtime-status", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachRuntimeStatusResponse;
}

export async function getCoachPrimary(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryResponse> {
  const res = await apiFetch("/api/coach/primary", { method: "GET" }, opts);
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryResponse;
}

export async function attachCoachPrimaryAnalysis(
  analysisSessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryAttachResponse> {
  const res = await apiFetch(
    "/api/coach/primary/attach",
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
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryMessageResponse> {
  const body: { content: string; analysis_session_id?: number } = { content };
  if (analysisSessionId !== undefined) {
    body.analysis_session_id = analysisSessionId;
  }
  const res = await apiFetch(
    "/api/coach/primary/messages",
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
