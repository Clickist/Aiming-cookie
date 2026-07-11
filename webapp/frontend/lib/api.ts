/**
 * Minimal fetch-based API client for the Aiming Cookie FastAPI backend.
 *
 * No axios — fetch + AbortController is enough. Endpoints mirrored from
 * webapp/backend/routes.py:
 *   POST /api/analyze           (multipart: video + csv + X-User-Id header)
 *   GET  /api/sessions/{id}     (polling for status / result)
 *
 * Note on CORS: webapp/backend/app.py mounts CORSMiddleware allowing
 * http://localhost:3000 (Next.js dev). Override via CORS_ORIGINS env.
 */

import type {
  AnalyzeResponse,
  ChatResponse,
  CoachPrimaryAttachResponse,
  CoachPrimaryMessageResponse,
  CoachPrimaryResponse,
  DeleteSessionResponse,
  SessionListResponse,
  SessionStatus,
  Timeline,
} from "./types";

/** Base URL of the FastAPI backend. Override via env at build/dev time. */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** Default X-User-Id placeholder (slice 1 dev shim; Clerk lands in slice 3). */
const DEFAULT_USER_ID =
  process.env.NEXT_PUBLIC_USER_ID ?? "dev";

export interface UploadOptions {
  /** Required Stats CSV (KovaaK's export). Backend hard-requires it. */
  csv: File;
  /** Reserved for future use — backend doesn't read these yet (slice 1 contract
   *  only accepts video + csv). */
  cmPer360?: number;
  fov?: number;
  /** Override X-User-Id (defaults to env or "dev"). */
  userId?: string;
  /** Abort the in-flight request (for cancel buttons). */
  signal?: AbortSignal;
}

/**
 * Upload a flicking recording + Stats CSV for asynchronous analysis.
 *
 * Wraps POST /api/analyze. Returns the new session's integer id — polling
 * `getSession(id)` gives queued → running → done | failed.
 *
 * Throws on non-2xx (the caller should surface 429 "已有分析进行中" and 413
 * "视频超过 100MB 限制" specifically — see routes.py).
 */
export async function uploadVideo(
  video: File,
  opts: UploadOptions,
): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append("video", video);
  form.append("csv", opts.csv);
  // cm/360 + FOV:后端 routes 接收 → db 存 → worker 传给 analyze_flicking_fair_summary。
  // cm/360 用户填实测最准(公式对 KovaaK's Horiz Sens 单位敏感);FOV 从 CSV 自动填。
  if (opts.cmPer360 !== undefined) {
    form.append("cm_per_360", String(opts.cmPer360));
  }
  if (opts.fov !== undefined) {
    form.append("fov", String(opts.fov));
  }

  const headers: Record<string, string> = {
    "X-User-Id": opts.userId ?? DEFAULT_USER_ID,
  };
  // NOTE: do NOT set Content-Type — the browser sets multipart boundary.

  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    body: form,
    headers,
    signal: opts.signal,
  });

  if (!res.ok) {
    throw await apiError(res);
  }
  return (await res.json()) as AnalyzeResponse;
}

/**
 * Poll analysis status / fetch result. Wraps GET /api/sessions/{id}.
 *
 * Status transitions: queued → running → done | failed. When status === "done",
 * `result` is AnalysisResult v1 (see lib/types.ts); map to CoachReport via
 * lib/contracts.ts for report/coach UI.
 */
export async function getSession(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<SessionStatus> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
    method: "GET",
    headers: { "X-User-Id": DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) {
    throw await apiError(res);
  }
  return (await res.json()) as SessionStatus;
}

/** List all sessions for the current user (newest first). GET /api/sessions. */
export async function listSessions(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<SessionListResponse> {
  const res = await fetch(`${API_BASE}/api/sessions`, {
    method: "GET",
    headers: { "X-User-Id": opts.userId ?? DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) {
    throw await apiError(res);
  }
  return (await res.json()) as SessionListResponse;
}

/** Hard-delete a session (DB row, chat, on-disk inputs). DELETE /api/sessions/{id}. */
export async function deleteSession(
  sessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<DeleteSessionResponse> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { "X-User-Id": opts.userId ?? DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) {
    throw await apiError(res);
  }
  return (await res.json()) as DeleteSessionResponse;
}

/**
 * Re-queue a failed session when input files still exist on the server.
 * Wraps POST /api/sessions/{id}/retry.
 */
export async function retrySession(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<SessionStatus> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/retry`, {
    method: "POST",
    headers: { "X-User-Id": DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) {
    throw await apiError(res);
  }
  return (await res.json()) as SessionStatus;
}

/**
 * Wrap a non-2xx response in an Error that carries the backend's JSON message
 * (FastAPI HTTPException returns `{"detail": "..."}`). Falls back to status
 * text if the body isn't JSON.
 */
async function apiError(res: Response): Promise<Error> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body?.detail) detail = String(body.detail);
  } catch {
    /* not JSON — keep status text */
  }
  const err = new Error(detail);
  err.name = `ApiError_${res.status}`;
  return err;
}

/* ---- coach 页:chat / timeline / video ---- */

/**
 * 拉历史对话。mount 时调一次。session 未 done 时后端返 409(由 caller 处理)。
 */
export async function getChatHistory(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/chat`, {
    method: "GET",
    headers: { "X-User-Id": DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ChatResponse;
}

/**
 * 发送一条用户消息。pinnedFrameSec 可选——"锁定当前时间轴"按钮附的视频秒数,
 * 后端把它拼成 [锁定 0:23] 前缀传给 agent。
 */
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
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: {
      "X-User-Id": DEFAULT_USER_ID,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as ChatResponse;
}

/** 拉视频时间轴 markers(fps / duration / events)。 */
export async function getTimeline(
  sessionId: number,
  opts: { signal?: AbortSignal } = {},
): Promise<Timeline> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/timeline`, {
    method: "GET",
    headers: { "X-User-Id": DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as Timeline;
}

/**
 * 视频流 URL——直接放 <video src>。后端用 FileResponse 流式返回 mp4。
 * 注意:不经过 fetch(浏览器原生 video 元素直接拉流)。
 */
export function getVideoUrl(sessionId: number): string {
  return `${API_BASE}/api/sessions/${sessionId}/video`;
}

/* ---- primary coach thread ---- */

/** GET /api/coach/primary — lazy-create thread, messages, refs. */
export async function getCoachPrimary(
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryResponse> {
  const res = await fetch(`${API_BASE}/api/coach/primary`, {
    method: "GET",
    headers: { "X-User-Id": opts.userId ?? DEFAULT_USER_ID },
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryResponse;
}

/** POST /api/coach/primary/attach — idempotent attach done analysis. */
export async function attachCoachPrimaryAnalysis(
  analysisSessionId: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryAttachResponse> {
  const res = await fetch(`${API_BASE}/api/coach/primary/attach`, {
    method: "POST",
    headers: {
      "X-User-Id": opts.userId ?? DEFAULT_USER_ID,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ analysis_session_id: analysisSessionId }),
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryAttachResponse;
}

/**
 * POST /api/coach/primary/messages — send without analysis_session_id for
 * context-free chat; pass analysis_session_id to inject that done analysis.
 */
export async function postCoachPrimaryMessage(
  content: string,
  analysisSessionId?: number,
  opts: { signal?: AbortSignal; userId?: string } = {},
): Promise<CoachPrimaryMessageResponse> {
  const body: { content: string; analysis_session_id?: number } = { content };
  if (analysisSessionId !== undefined) {
    body.analysis_session_id = analysisSessionId;
  }
  const res = await fetch(`${API_BASE}/api/coach/primary/messages`, {
    method: "POST",
    headers: {
      "X-User-Id": opts.userId ?? DEFAULT_USER_ID,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: opts.signal,
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as CoachPrimaryMessageResponse;
}

