/**
 * Sidecar Coach data-access layer (Phase 1 simplified).
 *
 * Conversation content lives in Pi JSONL sessions managed by session-repo.ts
 * (JsonlSessionRepo); this module shapes those sessions into the HTTP contract
 * consumed by the frontend. Context attach/detach is removed — Coach reads
 * files directly.
 */

import type http from "node:http";

import { ensureAppDataDirs } from "./app-data.ts";
import {
  deriveConversationTitle,
  ensureSession,
  listSessionIds,
  nextSessionIdSync,
  readConversationMeta,
  readSessionMessagesForUi,
  sessionExists,
  deleteSessionFile,
  truncateSessionFromMessage,
  writeConversationMeta,
  type ConversationMeta,
  type SessionMessage,
} from "./session-repo.ts";
import { hasActiveAgentRunForSession } from "./agent-runs.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function ownerIdFromRequest(req: http.IncomingMessage): string {
  const raw = req.headers["x-user-id"];
  if (typeof raw === "string" && raw.trim()) return raw;
  return "desktop-local";
}

export class CoachDataError extends Error {
  constructor(public statusCode: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------------------
// Session shaping
// ---------------------------------------------------------------------------

interface SessionOut {
  id: number;
  user_id: string;
  kind: string;
  title: string | null;
  status: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
  /** 首轮已落盘但模型命名尚未生成（前端据此安排一次延迟补刷）。 */
  title_pending?: boolean;
  analysis_session_ids: number[];
  /** Non-subject deep-read analyses (@time-link fallback; NOT 本次讨论). */
  deep_read_analysis_session_ids: number[];
}

function shapeSession(
  ownerId: string,
  id: number,
  meta: ConversationMeta,
  messages: SessionMessage[],
): SessionOut {
  const lastEntry = messages[messages.length - 1];
  // 侧栏预览向前找最后一条有正文的：末尾的 stopped 空标记不充当预览。
  const lastVisible = [...messages].reverse().find((message) => message.content.trim().length > 0);
  const title = deriveConversationTitle(messages, meta);
  const updatedAt = lastEntry ? lastEntry.timestamp : meta.updated_at;
  // 首轮已发生但 auto 命名还没落库（异步生成中）→ 前端安排一次延迟补刷
  const titlePending = !meta.title_source && messages.some((message) => message.role === "user");
  return {
    id,
    user_id: ownerId,
    kind: id === 1 ? "primary" : "conversation",
    title,
    status: meta.status,
    deleted_at: null,
    created_at: meta.created_at,
    updated_at: updatedAt,
    message_count: messages.length,
    last_message_preview: lastVisible ? lastVisible.content.slice(0, 240) : null,
    title_pending: titlePending || undefined,
    analysis_session_ids: meta.analysis_session_ids ?? [],
    deep_read_analysis_session_ids: meta.deep_read_analysis_session_ids ?? [],
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function listCoachSessions(
  ownerId: string,
  opts: { includeArchived?: boolean } = {},
): Promise<{ sessions: SessionOut[] }> {
  ensureAppDataDirs();
  const ids = await listSessionIds();
  const sessions: SessionOut[] = [];
  for (const id of ids) {
    const meta = readConversationMeta(id);
    const messages = await readSessionMessagesForUi(id);
    sessions.push(shapeSession(ownerId, id, meta, messages));
  }

  // Search is done client-side by the frontend SessionRail; the sidecar only
  // filters archived sessions.
  let filtered = sessions;
  if (!opts.includeArchived) {
    filtered = filtered.filter((s) => s.status === "active");
  }

  return { schema_version: "coach_session_list.v1", sessions: filtered } as unknown as { sessions: SessionOut[] };
}

export async function createCoachSession(ownerId: string, title?: string): Promise<SessionOut> {
  ensureAppDataDirs();
  const id = nextSessionIdSync();
  const normalizedTitle = title && title.trim() ? title.trim().slice(0, 120) : "新对话";
  const now = new Date().toISOString();
  await ensureSession(id);
  const meta: ConversationMeta = {
    id,
    title: normalizedTitle,
    status: "active",
    created_at: now,
    updated_at: now,
  };
  writeConversationMeta(id, meta);
  const messages = await readSessionMessagesForUi(id);
  return shapeSession(ownerId, id, meta, messages);
}

export async function updateCoachSession(
  ownerId: string,
  sessionId: number,
  update: { title?: string; status?: "archived" },
): Promise<SessionOut> {
  const meta = readConversationMeta(sessionId);
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  if (update.title !== undefined) {
    const title = update.title.trim();
    if (!title) throw new CoachDataError(400, "session title cannot be empty");
    meta.title = title.slice(0, 120);
    // 手动改名后自动命名永不再覆盖（title_source 守卫）。
    meta.title_source = "user";
  }
  if (update.status === "archived") {
    meta.status = "archived";
  }
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(sessionId, meta);
  const messages = await readSessionMessagesForUi(sessionId);
  return shapeSession(ownerId, sessionId, meta, messages);
}

export async function getCoachSessionDetail(
  ownerId: string,
  sessionId: number,
): Promise<SessionOut & { messages: Array<Record<string, unknown>> }> {
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  const meta = readConversationMeta(sessionId);
  // 活跃 run 期间，末尾「有工具/思考活动但尚无正文」的 assistant 回合是正常
  // 进行中的中间态：若照常合成空 stopped 标记，前端刷新会误挂「回答已停止」
  // 徽标（0915 CDP 真机实测；run 落定后徽标自行消失）。更早回合的真停止
  // 标记必须保留，故只抑制尾部这一条。
  const entries = await readSessionMessagesForUi(sessionId, {
    ...(hasActiveAgentRunForSession(sessionId) ? { suppressTrailingInterruptMarker: true } : {}),
  });
  const base = shapeSession(ownerId, sessionId, meta, entries);
  const messages = entries.map((entry, index) => ({
    id: index + 1,
    role: entry.role,
    content: entry.content,
    created_at: entry.timestamp,
    legacy_session_id: null,
    ...(entry.stopped ? { stopped: true } : {}),
  }));
  return { ...base, messages };
}

/**
 * 编辑重发截断（digests §11 item 7）：把会话可见消息截到前 `keepMessages`
 * 条，返回截断后的完整 session detail 供前端一次刷新。
 * 活跃 run 会继续向同一 JSONL 追加——先拒绝（409 session_busy），
 * 由前端在 run 结束后重试。
 */
export async function truncateCoachSession(
  ownerId: string,
  sessionId: number,
  keepMessages: number,
): Promise<SessionOut & { messages: Array<Record<string, unknown>> }> {
  if (!Number.isInteger(sessionId) || sessionId <= 0) {
    throw new CoachDataError(400, "Coach session id is invalid");
  }
  if (!Number.isInteger(keepMessages) || keepMessages < 0) {
    throw new CoachDataError(400, "keep_messages must be a non-negative integer");
  }
  if (!(await sessionExists(sessionId))) {
    throw new CoachDataError(404, "Coach session is unavailable");
  }
  if (hasActiveAgentRunForSession(sessionId)) {
    throw new CoachDataError(409, "session_busy");
  }
  await truncateSessionFromMessage(sessionId, keepMessages);
  return getCoachSessionDetail(ownerId, sessionId);
}

export async function deleteCoachSession(ownerId: string, sessionId: number): Promise<SessionOut> {
  const meta = readConversationMeta(sessionId);
  meta.status = "archived";
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(sessionId, meta);
  // Remove conversation content but keep meta for audit
  await deleteSessionFile(sessionId);
  return shapeSession(ownerId, sessionId, meta, []);
}

export async function getCoachPrimary(
  ownerId: string,
  sessionId?: number,
): Promise<{
  thread: { id: number; user_id: string; kind: string; created_at: string; updated_at: string };
  messages: Array<{ id: number; role: string; content: string; created_at: string }>;
  refs: unknown[];
}> {
  const targetId = sessionId ?? 1;
  const entries = await readSessionMessagesForUi(targetId);
  return {
    thread: {
      id: targetId,
      user_id: ownerId,
      kind: "primary",
      created_at: new Date().toISOString(),
      updated_at: entries.length > 0 ? entries[entries.length - 1].timestamp : new Date().toISOString(),
    },
    messages: entries.map((entry, index) => ({
      id: index + 1,
      role: entry.role,
      content: entry.content,
      created_at: entry.timestamp,
    })),
    refs: [],
  };
}
