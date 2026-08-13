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
  readSessionMessages,
  sessionExists,
  deleteSessionFile,
  writeConversationMeta,
  type ConversationMeta,
  type SessionMessage,
} from "./session-repo.ts";

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
  analysis_session_ids: number[];
}

function shapeSession(
  ownerId: string,
  id: number,
  meta: ConversationMeta,
  messages: SessionMessage[],
): SessionOut {
  const lastEntry = messages[messages.length - 1];
  const title = deriveConversationTitle(messages, meta.title);
  const updatedAt = lastEntry ? lastEntry.timestamp : meta.updated_at;
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
    last_message_preview: lastEntry ? lastEntry.content.slice(0, 240) : null,
    analysis_session_ids: meta.analysis_session_ids ?? [],
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
    const messages = await readSessionMessages(id);
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
  const messages = await readSessionMessages(id);
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
  }
  if (update.status === "archived") {
    meta.status = "archived";
  }
  meta.updated_at = new Date().toISOString();
  writeConversationMeta(sessionId, meta);
  const messages = await readSessionMessages(sessionId);
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
  const entries = await readSessionMessages(sessionId);
  const base = shapeSession(ownerId, sessionId, meta, entries);
  const messages = entries.map((entry, index) => ({
    id: index + 1,
    role: entry.role,
    content: entry.content,
    created_at: entry.timestamp,
    legacy_session_id: null,
  }));
  return { ...base, messages };
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
  const entries = await readSessionMessages(targetId);
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
